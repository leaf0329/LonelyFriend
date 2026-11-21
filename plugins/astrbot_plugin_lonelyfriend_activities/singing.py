from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
import time
from urllib.parse import urlsplit
import uuid
import wave

import httpx
from astrbot.api import logger
from astrbot.api.message_components import File, Record
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

from .singing_protocol import (
    AUDIO_SUFFIXES, MAX_AUDIO_SECONDS, MAX_RESULT_BYTES, MAX_SOURCE_BYTES, MAX_VOICE_SECONDS,
    processing_timeout, source_from_text, validate_clip,
)


class SingingError(Exception):
    pass


class SingingClient:
    def __init__(self, config: dict, state_file: Path, allowed_origins):
        self.http = httpx.AsyncClient(base_url=config["api_base"], trust_env=False,
            headers={"Authorization": "Bearer " + config["api_key"]}, timeout=httpx.Timeout(35, connect=1))
        self.state_file = state_file
        self.allowed_origins = allowed_origins
        self.jobs = json.loads(state_file.read_text(encoding="utf-8")) if state_file.is_file() else []
        for job in self.jobs:
            if job.get("delivery") == "sending":
                job["delivery"] = "unknown"
        self.searches = {}
        self.files = {}
        self.tasks = {}
        self.sending = set()
        self.upload_lock = asyncio.Lock()

    def save(self):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.jobs[-100:], ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.state_file)
        self.jobs = self.jobs[-100:]

    @staticmethod
    def owner(event):
        return event.unified_msg_origin, str(event.get_sender_id())

    async def request(self, method: str, path: str, **kwargs):
        try:
            response = await self.http.request(method, path, **kwargs)
        except httpx.HTTPError as error:
            raise SingingError("本机翻唱服务连接失败；请确认电脑与本地连接器在线。提交或发送结果不明时不会自动重试。") from error
        if response.status_code >= 400:
            try:
                message = response.json().get("error", "本地任务请求失败。")
            except ValueError:
                message = "本地未找到此任务，或请求被拒绝。"
            raise SingingError(str(message))
        return response

    def remember_file(self, event):
        files = [part for part in event.get_messages() if isinstance(part, File)
                 and Path(part.name or "").suffix.lower() in AUDIO_SUFFIXES]
        if files:
            self.files[self.owner(event)] = (time.monotonic(), files[-1])
        now = time.monotonic()
        self.files = {key: value for key, value in self.files.items() if now - value[0] < 3600}

    async def search(self, event, keyword: str, source="netease") -> list[dict]:
        response = await self.request("POST", "/v1/search", json={"keyword": keyword, "source": source})
        songs = response.json()["songs"]
        self.searches[self.owner(event)] = (time.monotonic(), songs)
        self.searches = {key: value for key, value in self.searches.items() if time.monotonic() - value[0] < 600}
        return songs

    async def upload_file(self, event) -> dict:
        value = self.files.get(self.owner(event))
        if value is None or time.monotonic() - value[0] >= 3600:
            raise SingingError("请先在这个会话发送音频文件，再使用“翻唱 文件”，默认整首。只使用你自己最近一小时发送的文件。")
        part = value[1]
        async with self.upload_lock:
            location = await part.get_file(allow_return_url=True)
            with tempfile.TemporaryFile() as staging:
                count = 0
                if location.startswith(("https://", "http://")):
                    async with httpx.AsyncClient(trust_env=False, timeout=120) as download:
                        for _ in range(5):
                            parsed = urlsplit(location)
                            host = (parsed.hostname or "").lower()
                            if (parsed.scheme not in {"http", "https"} or parsed.username or parsed.password
                                    or parsed.port not in {None, 80, 443}
                                    or not any(host == suffix or host.endswith("." + suffix)
                                               for suffix in ("qq.com", "qq.com.cn", "qpic.cn", "gtimg.cn", "gtimg.com"))):
                                raise SingingError("文件下载地址不是受支持的QQ文件地址，请重新上传音频。")
                            async with download.stream("GET", location) as response:
                                if response.is_redirect:
                                    location = str(response.url.join(response.headers["location"]))
                                    continue
                                response.raise_for_status()
                                async for chunk in response.aiter_bytes():
                                    count += len(chunk)
                                    if count > MAX_SOURCE_BYTES:
                                        raise SingingError("音频文件超过200MB。")
                                    staging.write(chunk)
                                break
                        else:
                            raise SingingError("QQ文件下载重定向次数过多。")
                else:
                    if not location or not Path(location).is_file():
                        raise SingingError("QQ音频文件已失效，请重新上传。")
                    with Path(location).open("rb") as original:
                        while chunk := original.read(65536):
                            count += len(chunk)
                            if count > MAX_SOURCE_BYTES:
                                raise SingingError("音频文件超过200MB。")
                            staging.write(chunk)
                staging.seek(0)

                async def chunks():
                    while data := staging.read(65536):
                        yield data

                result = await self.request("POST", "/v1/uploads", params={"name": part.name},
                    content=chunks(), headers={"Content-Type": "application/octet-stream"}, timeout=120)
                return result.json()

    async def start(self, event, choice: str, start: float | None = None, end: float | None = None, pitch=0, mode="auto") -> str:
        if event.unified_msg_origin not in self.allowed_origins():
            raise SingingError("当前会话未获准请求翻唱。")
        validate_clip(start, end, pitch)
        if mode not in {"auto", "voice", "file"}:
            raise ValueError("发送方式可选auto、voice或file。")
        if mode == "auto":
            mode = "file" if start is None or end - start > MAX_VOICE_SECONDS else "voice"
        if mode == "voice" and (start is None or end - start > MAX_VOICE_SECONDS):
            raise ValueError("整首或超过30秒请使用MP3文件；语音仅支持明确指定的30秒内片段。")
        if event.get_extra("lonelyfriend_cover_requested", False):
            raise SingingError("本轮已提交过翻唱请求，不会重复提交。")
        health = (await self.request("GET", "/v1/health", timeout=2)).json()
        if health["busy"]:
            raise SingingError("已有翻唱正在处理，请完成后再提交。")
        event.set_extra("lonelyfriend_cover_requested", True)
        if choice == "文件":
            try:
                source = await self.upload_file(event)
            except httpx.HTTPError as error:
                raise SingingError("QQ音频文件下载失败，请重新上传；没有提交翻唱任务。") from error
        elif choice.isdigit():
            previous = self.searches.get(self.owner(event))
            if previous is None or time.monotonic() - previous[0] >= 600:
                raise SingingError("搜歌列表已过期，请先使用“翻唱搜歌 歌名”。")
            index = int(choice) - 1
            if not 0 <= index < len(previous[1]):
                raise SingingError("选歌序号不在当前列表中。")
            song = previous[1][index]
            source = {"source": song["source"], "id": song["id"]}
        else:
            source = source_from_text(choice)
        job_id = uuid.uuid4().hex
        origin, sender = self.owner(event)
        job = {"id": job_id, "origin": origin, "sender": sender, "created_at": time.time(),
               "delivery": "not_sent", "mode": mode, "status": "submission_unknown",
               "duration": end - start if start is not None else None}
        self.jobs.append(job)
        self.save()
        response = await self.request("POST", "/v1/jobs", json={"id": job_id, "source": source,
            "start": start, "end": end, "pitch": pitch}, timeout=10)
        job["status"] = response.json()["status"]
        self.save()
        self.tasks[job_id] = asyncio.create_task(self.poll(event, job))
        self.tasks[job_id].add_done_callback(lambda _: self.tasks.pop(job_id, None))
        return job_id

    def last_job(self, event, prefix="") -> dict:
        origin, sender = self.owner(event)
        jobs = [job for job in self.jobs if job["origin"] == origin and job["sender"] == sender
                and job["id"].startswith(prefix)]
        if not jobs:
            raise SingingError("当前会话没有你的翻唱任务。")
        if prefix and len(jobs) != 1:
            raise SingingError("这个任务编号前缀不唯一，请提供更完整的编号。")
        return jobs[-1]

    async def status(self, event, prefix="") -> tuple[dict, dict]:
        job = self.last_job(event, prefix)
        state = (await self.request("GET", "/v1/jobs/" + job["id"])).json()
        job["status"] = state["status"]
        self.save()
        return job, state

    async def deliver(self, event, job: dict):
        if job["id"] in self.sending:
            raise SingingError("该任务音频正在发送，请先检查QQ消息。")
        self.sending.add(job["id"])
        try:
            await self._deliver(event, job)
        finally:
            self.sending.remove(job["id"])

    async def _deliver(self, event, job: dict):
        if event.unified_msg_origin not in self.allowed_origins():
            raise SingingError("当前会话已移出允许名单，音频未发送。")
        if job.get("cancel_requested"):
            raise SingingError("此任务已请求取消，音频未发送。")
        suffix = ".mp3" if job["mode"] == "file" else ".wav"
        directory = Path(get_astrbot_temp_path())
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix="cover_", suffix=suffix, dir=directory, delete=False) as output:
            path = Path(output.name)
        try:
            count = 0
            async with self.http.stream("GET", f"/v1/jobs/{job['id']}/audio", params={"format": job["mode"]}, timeout=120) as response:
                if response.status_code != 200:
                    raise SingingError("成品尚未可用或缓存已过期，请先查询翻唱状态。")
                with path.open("wb") as output:
                    async for chunk in response.aiter_bytes():
                        count += len(chunk)
                        if count > MAX_RESULT_BYTES:
                            raise SingingError("成品超过90MB发送上限。")
                        output.write(chunk)
            if job["mode"] == "voice":
                with wave.open(str(path)) as audio:
                    frames = audio.getnframes()
                    if (audio.getnchannels() != 1 or audio.getsampwidth() != 2 or audio.getframerate() != 24000
                            or not 1 <= frames / 24000 <= MAX_VOICE_SECONDS + 0.1 or len(audio.readframes(frames)) != frames * 2):
                        raise SingingError("成品语音格式或时长校验失败。")
            else:
                with path.open("rb") as audio:
                    if not audio.read(3).startswith((b"ID3", b"\xff")):
                        raise SingingError("成品不是MP3音频。")
            component = File(name="祀-翻唱.mp3", file=str(path)) if job["mode"] == "file" else Record.fromFileSystem(path)
            if job.get("cancel_requested"):
                raise SingingError("此任务已请求取消，音频未发送。")
            job["delivery"] = "sending"
            self.save()
            try:
                await asyncio.wait_for(event.send(event.chain_result([component])), timeout=120)
            except (Exception, asyncio.CancelledError):
                job["delivery"] = "unknown"
                self.save()
                raise
            job["delivery"] = "sent"
            self.save()
        finally:
            path.unlink(missing_ok=True)

    async def poll(self, event, job: dict):
        try:
            async with asyncio.timeout(processing_timeout(job.get("duration") or MAX_AUDIO_SECONDS) + 180):
                while True:
                    state = (await self.request("GET", "/v1/jobs/" + job["id"])).json()
                    job["status"] = state["status"]
                    self.save()
                    if state["status"] == "done":
                        if job["delivery"] == "not_sent" and job["id"] not in self.sending:
                            await self.deliver(event, job)
                        return
                    if state["status"] in {"failed", "cancelled"}:
                        if state["status"] == "failed" and event.unified_msg_origin in self.allowed_origins():
                            await event.send(event.plain_result("翻唱未完成：" + state["error"]).use_t2i(False))
                        return
                    await asyncio.sleep(2)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("翻唱任务%s中断：%s", job["id"], type(error).__name__)
            job["status"] = "unconfirmed"
            self.save()
            message = str(error) if isinstance(error, SingingError) else "结果或发送状态未确认。"
            try:
                if event.unified_msg_origin in self.allowed_origins():
                    await event.send(event.plain_result(f"翻唱任务{job['id'][:8]}：{message} 不会自动重发，可用 /翻唱状态 查询。").use_t2i(False))
            except Exception as notice_error:
                logger.warning("翻唱状态通知未确认：%s", type(notice_error).__name__)

    async def cancel(self, event, prefix="") -> str:
        job = self.last_job(event, prefix)
        if job["delivery"] in {"sending", "sent", "unknown"}:
            return "音频已发送或已进入发送阶段，不能用取消任务撤回；请先检查QQ消息。"
        job["cancel_requested"] = True
        self.save()
        state = (await self.request("POST", f"/v1/jobs/{job['id']}/cancel")).json()
        task = self.tasks.get(job["id"])
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        job["status"] = state["status"]
        self.save()
        return "已取消，后续不会发送音频。" if state["status"] == "cancelled" else "音频已生成，已停止本次发送。"

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.http.aclose()


def load_singing(data: Path, allowed_origins) -> SingingClient | None:
    path = data / "singing.json"
    return SingingClient(json.loads(path.read_text(encoding="utf-8-sig")),
        data / "plugin_data/astrbot_plugin_lonelyfriend_activities/singing-jobs.json", allowed_origins) if path.is_file() else None
