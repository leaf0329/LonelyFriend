from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid
import wave

import httpx
import psutil
from aiohttp.test_utils import TestClient, TestServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.singing_service import ProcessJob, SingingWorker, make_app
from plugins.astrbot_plugin_lonelyfriend_activities.music_sources import MusicSources
from plugins.astrbot_plugin_lonelyfriend_activities.singing_protocol import interval, source_from_text


def sample():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\x10\x00\xf0\xff" * 24000)
    return buffer.getvalue()


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="singing-worker-test-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        reference = self.path / "reference.wav"
        reference.write_bytes(sample())
        self.worker = SingingWorker({"reference_file": str(reference), "api_key": "test-key"},
                                    data_dir=self.path / "data")
        self.http = TestClient(TestServer(make_app(self.worker)))
        await self.http.start_server()
        self.addAsyncCleanup(self.http.close)
        self.headers = {"Authorization": "Bearer test-key"}

    async def test_authentication_and_upload_validation(self):
        response = await self.http.get("/v1/health")
        self.assertEqual(response.status, 401)
        response = await self.http.post("/v1/uploads?name=track.wav", data=b"invalid", headers=self.headers)
        self.assertEqual(response.status, 422)
        self.assertEqual(list(self.worker.uploads.iterdir()), [])
        response = await self.http.post("/v1/uploads?name=track.wav", data=sample(), headers=self.headers)
        self.assertEqual(response.status, 200)
        source = await response.json()
        self.assertEqual(source["source"], "upload")
        self.assertEqual(len(list(self.worker.uploads.iterdir())), 1)

    async def test_one_job_idempotency_cancellation_and_private_paths(self):
        waiting = asyncio.Event()
        entered = asyncio.Event()

        async def blocked_source(source):
            entered.set()
            await waiting.wait()

        self.worker.sources.resolve = blocked_source
        payload = {"id": uuid.uuid4().hex, "source": {"source": "netease", "id": "29732235"},
                   "start": 1, "end": 3, "pitch": 0}
        response = await self.http.post("/v1/jobs", json=payload, headers=self.headers)
        self.assertEqual(response.status, 202)
        await asyncio.wait_for(entered.wait(), 2)
        response = await self.http.post("/v1/jobs", json=payload, headers=self.headers)
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["id"], payload["id"])
        response = await self.http.post("/v1/jobs", json={**payload, "end": 4}, headers=self.headers)
        self.assertEqual(response.status, 409)
        response = await self.http.post("/v1/jobs", json={**payload, "id": uuid.uuid4().hex}, headers=self.headers)
        self.assertEqual(response.status, 429)
        response = await self.http.post(f"/v1/jobs/{payload['id']}/cancel", headers=self.headers)
        self.assertEqual((await response.json())["status"], "cancelled")
        self.assertIsNone(self.worker.active)
        response = await self.http.get("/v1/jobs/..%2F..%2Fprivate", headers=self.headers)
        self.assertIn(response.status, (404, 422))

    async def test_native_process_job_kills_venv_interpreter_and_descendants(self):
        code = "import sys,subprocess,time; sys.stdin.readline(); p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']); print(p.pid,flush=True); time.sleep(60)"
        process = await asyncio.create_subprocess_exec(str(ROOT / "runtime/singing/.venv/Scripts/python.exe"),
            "-u", "-c", code, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW)
        job = ProcessJob(process.pid)
        child = None
        try:
            process.stdin.write(b"start\n")
            await process.stdin.drain()
            child = int(await asyncio.wait_for(process.stdout.readline(), 5))
            self.assertTrue(psutil.pid_exists(child))
        finally:
            process.stdin.close()
            job.close()
            await asyncio.wait_for(process.wait(), 5)
        for _ in range(20):
            if not psutil.pid_exists(child):
                break
            await asyncio.sleep(0.05)
        self.assertFalse(psutil.pid_exists(child))

    async def test_cached_remote_cover_does_not_contact_the_music_platform(self):
        source = {"source": "netease", "id": "123"}
        params = {"source": source, "start": 0.0, "end": 2.0, "pitch": 0}
        key = self.worker.cache_key(params, str(source))
        cache = self.worker.cache / key
        cache.mkdir()
        (cache / "voice.wav").write_bytes(sample())
        await self.worker.run_process([self.worker.ffmpeg, "-v", "error", "-i", cache / "voice.wav",
            cache / "cover.mp3"], self.path / "encode.log")
        (cache / "ready.json").write_text(json.dumps({"title": "cached song", "duration": 2}))

        async def unexpected_network(source):
            raise AssertionError("A cache hit contacted the music platform")

        self.worker.sources.resolve = unexpected_network
        job_id = uuid.uuid4().hex
        response = await self.http.post("/v1/jobs", json={"id": job_id, **params}, headers=self.headers)
        self.assertEqual(response.status, 202)
        await asyncio.wait_for(self.worker.task, 5)
        state = self.worker.load(job_id)
        self.assertEqual(state["status"], "done")
        self.assertTrue(state["cache_hit"])


class SourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_catalog_matches_artist_title_and_original_filename(self):
        sources = MusicSources({"1874-live": {"title": "1874 (Live)", "artist": "陈奕迅", "duration": 308,
                                              "path": "E:/Music/现场版1874.mp3"}})
        self.addAsyncCleanup(sources.close)
        for query in ("陈奕迅 1874", "现场版1874"):
            self.assertEqual((await sources.search(query, "local"))[0]["id"], "1874-live")

    async def test_platform_audio_permission_and_domain_boundary(self):
        sources = MusicSources({})
        await sources.http.aclose()

        async def respond(request):
            if "/detail" in request.url.path:
                return httpx.Response(200, json={"songs": [{"name": "song", "duration": 200000}]})
            return httpx.Response(200, json={"data": [{"url": "https://m1.music.126.net/song.mp3", "freeTrialInfo": {"start": 0, "end": 30}}]})

        sources.http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        self.addAsyncCleanup(sources.close)
        with self.assertRaisesRegex(ValueError, "完整音频"):
            await sources.resolve({"source": "netease", "id": "123"})
        self.assertTrue(sources.allowed_download("https://m1.music.126.net/song.mp3", "netease"))
        for value in ["http://127.0.0.1/private", "https://music.126.net.evil.example/file", "https://user:pass@m1.music.126.net/file"]:
            self.assertFalse(sources.allowed_download(value, "netease"))

    def test_timestamps_and_known_source_links(self):
        self.assertEqual(interval("3:53-4:15.5"), (233, 255.5))
        for value in ["3:70-4:00", "-1-20", "0:00-1:00", "NaN-30"]:
            with self.assertRaises(ValueError):
                interval(value)
        self.assertEqual(source_from_text("https://music.163.com/#/song?id=67014"), {"source": "netease", "id": "67014"})
        self.assertEqual(source_from_text("https://www.bilibili.com/video/BV1Bpn162Ekn?p=2")["page"], 2)
        with self.assertRaises(ValueError):
            source_from_text("E:/private.wav")


if __name__ == "__main__":
    unittest.main()
