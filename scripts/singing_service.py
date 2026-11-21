from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

from aiohttp import web
import httpx
import psutil
import win32api
import win32con
import win32job
from bilibili_api.exceptions import ApiException as BiliError
from qqmusic_api.core.exceptions import BaseApiException as QQMusicError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from plugins.astrbot_plugin_lonelyfriend_activities.music_sources import MusicSources
from plugins.astrbot_plugin_lonelyfriend_activities.singing_protocol import (
    AUDIO_SUFFIXES, MAX_SOURCE_BYTES, validate_clip, validate_source,
)

LOG = logging.getLogger("singing-service")


class ProcessJob:
    """Closing this handle also ends inference descendants after a worker crash."""

    def __init__(self, pid: int):
        self.handle = win32job.CreateJobObject(None, "")
        try:
            info = win32job.QueryInformationJobObject(self.handle, win32job.JobObjectExtendedLimitInformation)
            info["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            win32job.SetInformationJobObject(self.handle, win32job.JobObjectExtendedLimitInformation, info)
            def assign(process_id):
                process = win32api.OpenProcess(win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE
                    | win32con.PROCESS_QUERY_INFORMATION, False, process_id)
                try:
                    if not win32job.IsProcessInJob(process, self.handle):
                        win32job.AssignProcessToJobObject(self.handle, process)
                finally:
                    process.Close()
            assign(pid)
            # Windows venv launchers may create the real interpreter before assignment.
            # The CLI handshake keeps that interpreter from starting inference children.
            for child in psutil.Process(pid).children(recursive=True):
                assign(child.pid)
        except BaseException:
            self.handle.Close()
            raise

    def close(self):
        self.handle.Close()


class SingingWorker:
    def __init__(self, config: dict, root: Path = ROOT, data_dir: Path | None = None):
        self.root, self.config = root, config
        self.data = data_dir if data_dir is not None else root / "data/singing/worker"
        self.cache, self.uploads = self.data / "cache", self.data / "uploads"
        for directory in (self.cache, self.uploads, self.data / "jobs"):
            directory.mkdir(parents=True, exist_ok=True)
        self.sources = MusicSources(config.get("library", {}))
        self.active: dict | None = None
        self.task: asyncio.Task | None = None
        self.uploading = False
        self.busy_cache_key = None
        self.ffmpeg = next((root / "runtime/ffmpeg").glob("*/bin/ffmpeg.exe"))
        self.engine_python = root / "runtime/singing/.venv/Scripts/python.exe"
        self.reference = Path(config["reference_file"])
        if not self.reference.is_file() or not self.engine_python.is_file():
            raise FileNotFoundError("翻唱环境或角色参考录音缺失。")
        for path in (self.data / "jobs").glob("*.json"):
            job = json.loads(path.read_text(encoding="utf-8"))
            if job["status"] in {"accepted", "running"}:
                job.update(status="failed", error="本地服务曾中断，请重新提交。")
                self.save(job)

    def save(self, job: dict):
        path = self.data / "jobs" / (job["id"] + ".json")
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)

    def load(self, job_id: str) -> dict:
        if not isinstance(job_id, str) or not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise ValueError("任务编号不正确。")
        path = self.data / "jobs" / (job_id + ".json")
        if not path.is_file():
            raise web.HTTPNotFound()
        return json.loads(path.read_text(encoding="utf-8"))

    def cleanup(self):
        now = time.time()
        for path in self.uploads.iterdir():
            if self.active and self.active["params"]["source"] == {"source": "upload", "id": path.stem}:
                continue
            if now - path.stat().st_mtime > 3600:
                path.unlink()
        folders = sorted((p for p in self.cache.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime)
        sizes = {p: sum(f.stat().st_size for f in p.iterdir() if f.is_file()) for p in folders}
        total = sum(sizes.values())
        for folder in folders:
            if folder.name == self.busy_cache_key:
                continue
            if total <= 128 * 1024 * 1024 and now - folder.stat().st_mtime < 7 * 86400:
                continue
            shutil.rmtree(folder)
            total -= sizes[folder]
        reports = sorted((self.data / "jobs").glob("*.json"), key=lambda p: p.stat().st_mtime)
        for report in reports[:-100]:
            report.unlink()
            report.with_suffix(".log").unlink(missing_ok=True)

    async def run_process(self, args: list, log: Path, *, inference=False):
        with log.open("ab") as output:
            process = await asyncio.create_subprocess_exec(
                *map(str, args), cwd=self.root, stdin=asyncio.subprocess.PIPE,
                stdout=output, stderr=output, creationflags=subprocess.CREATE_NO_WINDOW)
            job = None
            try:
                if inference:
                    job = ProcessJob(process.pid)
                    # The CLI waits for this before starting any child process.
                    process.stdin.write(b"start\n")
                    await process.stdin.drain()
                process.stdin.close()
                code = await asyncio.wait_for(process.wait(), timeout=600 if inference else 30)
                if code:
                    raise ValueError("本地音频处理失败，详情已保存在翻唱任务日志。")
            finally:
                process.stdin.close()
                if job is not None:
                    job.close()
                elif process.returncode is None:
                    process.kill()
                await process.wait()

    async def probe(self, path: Path) -> float:
        process = await asyncio.create_subprocess_exec(
            str(self.ffmpeg.with_name("ffprobe.exe")), "-v", "error", "-show_entries", "format=duration",
            "-of", "json", str(path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            output, _ = await asyncio.wait_for(process.communicate(), timeout=10)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        if process.returncode:
            raise ValueError("文件不是可解码的音频。")
        duration = float(json.loads(output)["format"]["duration"])
        if not 0 < duration <= 3600:
            raise ValueError("音源须为60分钟以内的音频。")
        return duration

    def cache_key(self, params: dict, identity: str) -> str:
        source = params["source"]
        fingerprint = {"source": {"source": "upload"} if source["source"] == "upload" else source,
            "identity": identity, "start": float(params["start"]), "end": float(params["end"]), "pitch": params["pitch"],
            "reference": hashlib.sha256(self.reference.read_bytes()).hexdigest(),
            "pipeline": (self.root / "scripts/sing_cover.py").stat().st_mtime_ns,
            "models": (self.root / "data/singing/model-manifest.json").read_text(encoding="utf-8")}
        return hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()

    async def render(self, job: dict):
        params = job["params"]
        source = params["source"]
        log = self.data / "jobs" / (job["id"] + ".log")
        cache_dir = None
        try:
            self.cleanup()
            job.update(status="running", stage="取音源")
            self.save(job)
            with tempfile.TemporaryDirectory(prefix="render-", dir=self.data) as temporary:
                work = Path(temporary)
                if source["source"] == "upload":
                    matches = list(self.uploads.glob(source["id"] + ".*"))
                    if len(matches) != 1:
                        raise ValueError("上传音频已过期，请重新发送文件。")
                    original = matches[0]
                    info = {"title": "你提供的音频", "duration": await self.probe(original)}
                    with original.open("rb") as content:
                        identity = hashlib.file_digest(content, "sha256").hexdigest()
                elif source["source"] == "local":
                    info, original, _ = await self.sources.resolve(source)
                    identity = str(original.stat().st_mtime_ns) + ":" + str(original.stat().st_size)
                else:
                    identity = str(source)
                key = self.cache_key(params, identity)
                cache_dir = self.cache / key
                self.busy_cache_key = key
                cached = (cache_dir / "ready.json").is_file()
                if cached:
                    info = json.loads((cache_dir / "ready.json").read_text(encoding="utf-8"))
                else:
                    if source["source"] not in {"upload", "local"}:
                        info, url, headers = await self.sources.resolve(source)
                        if params["end"] > info["duration"] + 0.05:
                            raise ValueError(f"所选时间超出音源时长（{info['duration']:.1f}秒）。")
                        original = work / "original.audio"
                        await self.sources.download(url, source["source"], original, headers)
                    duration = await self.probe(original)
                    if abs(duration - info["duration"]) > 3:
                        raise ValueError("音频长度与完整歌曲不符，可能只有试听，停止翻唱。")
                    if params["end"] > duration + 0.05:
                        raise ValueError(f"所选时间超出音源时长（{duration:.1f}秒）。")
                    job["stage"] = "分离与翻唱"
                    self.save(job)
                    await self.run_process([self.engine_python, self.root / "scripts/sing_cover.py",
                        "--wait-for-parent", "--source", original, "--reference", self.reference,
                        "--log-file", log,
                        "--start", params["start"], "--duration", params["end"] - params["start"],
                        "--pitch-shift", params["pitch"], "--steps", 40, "--output", work / "cover"], log, inference=True)
                    result = work / "cover" / f"si-cover-{params['pitch']:+}.wav"
                    cache_dir.mkdir(exist_ok=True)
                    job["stage"] = "整理音频"
                    self.save(job)
                    await self.run_process([self.ffmpeg, "-v", "error", "-y", "-i", result,
                        "-ac", 1, "-ar", 24000, cache_dir / "voice.wav"], log)
                    await self.run_process([self.ffmpeg, "-v", "error", "-y", "-i", result,
                        "-codec:a", "libmp3lame", "-b:a", "192k", cache_dir / "cover.mp3"], log)
                if abs(await self.probe(cache_dir / "voice.wav") - (params["end"] - params["start"])) > 0.1:
                    raise ValueError("成品音频长度校验失败。")
                if abs(await self.probe(cache_dir / "cover.mp3") - (params["end"] - params["start"])) > 0.2:
                    raise ValueError("成品MP3长度校验失败。")
                (cache_dir / "ready.json").write_text(json.dumps({"duration": params["end"] - params["start"], "title": info["title"]}), encoding="utf-8")
                job.update(status="done", stage="完成", title=info["title"], cache_key=key, cache_hit=cached,
                           duration=params["end"] - params["start"])
        except asyncio.CancelledError:
            job.update(status="cancelled", stage="已取消")
            raise
        except Exception as error:
            LOG.exception("Cover job failed: %s", job["id"])
            job.update(status="failed", stage="失败", error=public_error(error))
        finally:
            job["finished_at"] = time.time()
            self.save(job)
            self.active = None
            self.busy_cache_key = None

    async def close(self, app):
        if self.task is not None and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        await self.sources.close()


def public_error(error: Exception) -> str:
    if isinstance(error, httpx.HTTPError):
        return "音源网络请求失败，请稍后查询状态或换用文件。"
    if isinstance(error, TimeoutError):
        return "本地处理超时，任务已停止。"
    if isinstance(error, (ValueError, QQMusicError, BiliError)):
        return str(error)[:240]
    return "本地翻唱出现内部错误，详情已记录；请勿重复提交同一任务。"


def make_app(worker: SingingWorker) -> web.Application:
    @web.middleware
    async def boundary(request, handler):
        if not secrets.compare_digest(request.headers.get("Authorization", ""), "Bearer " + worker.config["api_key"]):
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            return await handler(request)
        except (ValueError, httpx.HTTPError, TimeoutError, QQMusicError, BiliError) as error:
            return web.json_response({"error": public_error(error)}, status=422)

    app = web.Application(middlewares=[boundary], client_max_size=MAX_SOURCE_BYTES)
    routes = web.RouteTableDef()

    async def small_json(request):
        body = await request.content.read(16385)
        if len(body) > 16384:
            raise web.HTTPRequestEntityTooLarge(max_size=16384, actual_size=len(body))
        value = json.loads(body)
        if not isinstance(value, dict):
            raise ValueError("请求须为JSON对象。")
        return value

    @routes.get("/v1/health")
    async def health(request):
        return web.json_response({"ok": True, "service": "lonelyfriend-singing", "pid": os.getpid(),
            "busy": worker.active is not None, "max_clip_seconds": 30})

    @routes.post("/v1/search")
    async def search(request):
        data = await small_json(request)
        return web.json_response({"songs": await worker.sources.search(str(data.get("keyword", "")), data.get("source", "netease"))})

    @routes.post("/v1/uploads")
    async def upload(request):
        if worker.uploading:
            return web.json_response({"error": "已有文件正在上传。"}, status=429)
        suffix = Path(request.query.get("name", "")).suffix.lower()
        if suffix not in AUDIO_SUFFIXES:
            raise ValueError("请提供MP3、WAV、FLAC、M4A、AAC、OGG或OPUS文件。")
        worker.uploading = True
        path = worker.uploads / (uuid.uuid4().hex + suffix)
        try:
            worker.cleanup()
            available = 2 * MAX_SOURCE_BYTES - sum(p.stat().st_size for p in worker.uploads.iterdir())
            count = 0
            with path.open("wb") as output:
                async for chunk in request.content.iter_chunked(65536):
                    count += len(chunk)
                    if count > min(MAX_SOURCE_BYTES, available):
                        raise ValueError("音频超过200MB或上传缓存已满。")
                    output.write(chunk)
            await worker.probe(path)
            return web.json_response({"source": "upload", "id": path.stem})
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        finally:
            worker.uploading = False

    @routes.post("/v1/jobs")
    async def create(request):
        data = await small_json(request)
        job_id = data.get("id", "")
        if not isinstance(job_id, str) or not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise ValueError("任务编号不正确。")
        source = validate_source(data.get("source", {}))
        validate_clip(data.get("start"), data.get("end"), data.get("pitch", 0))
        params = {"source": source, "start": float(data["start"]), "end": float(data["end"]), "pitch": data.get("pitch", 0)}
        try:
            existing = worker.load(job_id)
        except web.HTTPNotFound:
            existing = None
        if existing is not None:
            if existing["params"] != params:
                return web.json_response({"error": "任务编号已被不同参数占用。"}, status=409)
            return web.json_response(existing)
        if worker.active is not None:
            return web.json_response({"error": "已有翻唱正在处理，请完成后再提交。"}, status=429)
        job = {"id": job_id, "params": params, "created_at": time.time(), "status": "accepted", "stage": "等待处理"}
        worker.save(job)
        worker.active = job
        worker.task = asyncio.create_task(worker.render(job))
        return web.json_response(job, status=202)

    @routes.get("/v1/jobs/{job_id}")
    async def status(request):
        return web.json_response(worker.load(request.match_info["job_id"]))

    @routes.post("/v1/jobs/{job_id}/cancel")
    async def cancel(request):
        job = worker.load(request.match_info["job_id"])
        if worker.active is not None and worker.active["id"] == job["id"]:
            worker.task.cancel()
            await asyncio.gather(worker.task, return_exceptions=True)
        return web.json_response(worker.load(job["id"]))

    @routes.get("/v1/jobs/{job_id}/audio")
    async def audio(request):
        job = worker.load(request.match_info["job_id"])
        if job["status"] != "done":
            return web.json_response({"error": "任务尚未完成。"}, status=409)
        filename = "cover.mp3" if request.query.get("format") == "file" else "voice.wav"
        path = worker.cache / job["cache_key"] / filename
        if not path.is_file():
            return web.json_response({"error": "音频缓存已过期，请重新提交。"}, status=410)
        return web.FileResponse(path)

    app.add_routes(routes)
    app.on_cleanup.append(worker.close)
    return app


def main():
    import msvcrt
    config = json.loads((ROOT / "data/singing-local.json").read_text(encoding="utf-8-sig"))
    with (ROOT / "data/singing-worker.lock").open("a+b") as lock:
        if lock.seek(0, 2) == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return
        (ROOT / "logs").mkdir(exist_ok=True)
        handler = RotatingFileHandler(ROOT / "logs/singing-service.log", maxBytes=1024 * 1024, backupCount=1, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        LOG.addHandler(handler)
        LOG.setLevel(logging.INFO)
        logging.getLogger("aiohttp.server").addHandler(handler)
        web.run_app(make_app(SingingWorker(config)), host="127.0.0.1", port=config["port"],
                    access_log=None, print=None, handle_signals=False)


if __name__ == "__main__":
    main()
