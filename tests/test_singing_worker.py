from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
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


def sample(seconds=2):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\x10\x00\xf0\xff" * (12000 * seconds))
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

    async def test_full_song_resolves_actual_length_and_keeps_one_mp3_without_long_wav(self):
        original = self.path / "whole.wav"
        await self.worker.run_process([self.worker.ffmpeg, "-v", "error", "-f", "lavfi", "-i",
            "sine=frequency=220:duration=45", "-ac", 2, "-ar", 44100, original], self.path / "source.log")
        self.worker.sources.library["whole"] = {"path": str(original), "title": "whole song", "duration": 45}
        run_process = self.worker.run_process
        inference_calls = []

        async def render_without_gpu(args, log, *, inference=False, timeout=30):
            if inference:
                values = list(map(str, args))
                self.assertEqual(float(values[values.index("--start") + 1]), 0)
                self.assertAlmostEqual(float(values[values.index("--duration") + 1]), 45)
                inference_calls.append(values)
                output = Path(values[values.index("--output") + 1])
                output.mkdir()
                (output / "si-cover-+0.wav").write_bytes(original.read_bytes())
            else:
                await run_process(args, log, timeout=timeout)

        self.worker.run_process = render_without_gpu
        payload = {"id": uuid.uuid4().hex, "source": {"source": "local", "id": "whole"}}
        response = await self.http.post("/v1/jobs", json=payload, headers=self.headers)
        self.assertEqual(response.status, 202)
        await asyncio.wait_for(self.worker.task, 10)
        state = self.worker.load(payload["id"])
        self.assertEqual(state["status"], "done", state.get("error"))
        self.assertAlmostEqual(state["duration"], 45)
        self.assertEqual(len(inference_calls), 1)
        self.assertIsNone(state["params"]["start"])
        cache = self.worker.cache / state["cache_key"]
        self.assertFalse((cache / "voice.wav").exists())
        response = await self.http.get(f"/v1/jobs/{payload['id']}/audio", headers=self.headers)
        self.assertEqual(response.status, 200)
        self.assertTrue((await response.read()).startswith(b"ID3"))
        response = await self.http.get(f"/v1/jobs/{payload['id']}/audio?format=voice", headers=self.headers)
        self.assertEqual(response.status, 422)
        response = await self.http.post("/v1/jobs", json=payload, headers=self.headers)
        self.assertEqual(response.status, 200)
        self.assertEqual(len(inference_calls), 1)
        for params in ({"start": 5}, {"end": 20}, {"start": 0, "end": 3601}):
            response = await self.http.post("/v1/jobs", json={**payload, "id": uuid.uuid4().hex, **params}, headers=self.headers)
            self.assertEqual(response.status, 422)

    async def test_missing_or_preview_original_uses_gd_for_the_same_track(self):
        source = {"source": "netease", "id": "123"}
        info = {"title": "selected song", "duration": 8}
        for missing in (True, False):
            with self.subTest(missing=missing):
                async def original(selected):
                    if missing:
                        raise ValueError("原接口未提供完整音频")
                    return info, "https://m1.music.126.net/preview", {}

                async def download(url, platform, path, headers):
                    path.write_bytes(sample(2 if url.endswith("preview") else 8))

                self.worker.sources.resolve = original
                self.worker.sources.download = download
                self.worker.sources.resolve_gd = AsyncMock(return_value=(info, "https://m1.music.126.net/full", {}))
                received, duration, provider = await self.worker.acquire_remote(source, self.path / "track.wav", None)
                self.assertEqual(received, info)
                self.assertEqual(duration, 8)
                self.assertEqual(provider, "gd")
                self.worker.sources.resolve_gd.assert_awaited_once_with(source)

    async def test_successful_original_and_other_platform_failures_do_not_use_gd(self):
        async def download(url, platform, path, headers):
            path.write_bytes(sample(8))
        self.worker.sources.resolve = AsyncMock(return_value=({"title": "song", "duration": 8}, "full", {}))
        self.worker.sources.download = AsyncMock(side_effect=download)
        self.worker.sources.resolve_gd = AsyncMock()
        result = await self.worker.acquire_remote({"source": "netease", "id": "123"}, self.path / "track.wav", None)
        self.assertEqual(result[2], "platform")
        self.worker.sources.download.reset_mock()
        with self.assertRaisesRegex(ValueError, "超出音源时长"):
            await self.worker.acquire_remote({"source": "netease", "id": "123"}, self.path / "track.wav", 20)
        self.worker.sources.download.assert_not_awaited()
        self.worker.sources.resolve.side_effect = ValueError("B站不可用")
        with self.assertRaisesRegex(ValueError, "B站不可用"):
            await self.worker.acquire_remote({"source": "bilibili", "id": "BV1Bpn162Ekn", "page": 1}, self.path / "track.wav", None)
        self.worker.sources.resolve_gd.assert_not_awaited()

    async def test_gd_preview_fails_job_before_gpu_inference(self):
        info = {"title": "song", "duration": 8}
        self.worker.sources.resolve = AsyncMock(side_effect=ValueError("原接口不可用"))
        self.worker.sources.resolve_gd = AsyncMock(return_value=(info, "https://m1.music.126.net/preview", {}))
        async def download(url, platform, path, headers):
            path.write_bytes(sample(2))
        self.worker.sources.download = download
        self.worker.run_process = AsyncMock()
        payload = {"id": uuid.uuid4().hex, "source": {"source": "netease", "id": "123"}}
        response = await self.http.post("/v1/jobs", json=payload, headers=self.headers)
        self.assertEqual(response.status, 202)
        await asyncio.wait_for(self.worker.task, 5)
        state = self.worker.load(payload["id"])
        self.assertEqual(state["status"], "failed")
        self.assertIn("GD补源", state["error"])
        self.assertIn("试听", state["error"])
        self.worker.run_process.assert_not_awaited()
        self.assertEqual(list(self.worker.cache.iterdir()), [])

    async def test_cancelling_gd_lookup_does_not_start_download_or_inference(self):
        entered = asyncio.Event()
        async def waiting_gd(source):
            entered.set()
            await asyncio.Event().wait()
        self.worker.sources.resolve = AsyncMock(side_effect=ValueError("原接口不可用"))
        self.worker.sources.resolve_gd = waiting_gd
        self.worker.sources.download = AsyncMock()
        self.worker.run_process = AsyncMock()
        payload = {"id": uuid.uuid4().hex, "source": {"source": "netease", "id": "123"}}
        await self.http.post("/v1/jobs", json=payload, headers=self.headers)
        await asyncio.wait_for(entered.wait(), 2)
        response = await self.http.post(f"/v1/jobs/{payload['id']}/cancel", headers=self.headers)
        self.assertEqual((await response.json())["status"], "cancelled")
        self.assertIsNone(self.worker.active)
        self.worker.sources.download.assert_not_awaited()
        self.worker.run_process.assert_not_awaited()


class SourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_gd_keeps_selected_id_and_rejects_untrusted_audio_locations(self):
        sources = MusicSources({})
        await sources.http.aclose()
        destination = "https://m1.music.126.net/full.mp3"
        async def respond(request):
            if request.url.host == "music.163.com":
                return httpx.Response(200, json={"songs": [{"name": "selected song", "duration": 8000}]})
            self.assertEqual(request.url.host, "music-api.gdstudio.xyz")
            self.assertEqual(dict(request.url.params), {"types": "url", "source": "netease", "id": "123", "br": "320"})
            return httpx.Response(200, json={"url": destination})
        sources.http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        self.addAsyncCleanup(sources.close)
        info, url, headers = await sources.resolve_gd({"source": "netease", "id": "123"})
        self.assertEqual(info, {"title": "selected song", "duration": 8})
        self.assertEqual(url, destination)
        for destination in (None, "", "http://127.0.0.1/private", "https://music.126.net.evil.example/file",
                            "https://user:pass@m1.music.126.net/file", "https://qq.com/different-platform"):
            with self.assertRaises(ValueError):
                await sources.resolve_gd({"source": "netease", "id": "123"})

    async def test_song_name_keeps_exact_matches_instead_of_unrequested_remixes(self):
        sources = MusicSources({})
        await sources.http.aclose()
        async def respond(request):
            return httpx.Response(200, json={"code": 200, "result": {"songs": [
                {"id": 1, "name": "5:20AM（DJ remix）", "artists": [{"name": "DJ"}], "album": {"name": "mix"}, "duration": 200000},
                {"id": 2, "name": "5:20AM", "artists": [{"name": "original"}], "album": {"name": "song"}, "duration": 200000}]}})
        sources.http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        self.addAsyncCleanup(sources.close)
        self.assertEqual([s["id"] for s in await sources.search("《5:20am》", "netease")], ["2"])
        self.assertIn("1", [s["id"] for s in await sources.search("5:20am DJ", "netease")])

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
        self.assertEqual(interval("0:00-1:00"), (0, 60))
        for value in ["3:70-4:00", "-1-20", "0:00-61:00", "NaN-30"]:
            with self.assertRaises(ValueError):
                interval(value)
        self.assertEqual(source_from_text("https://music.163.com/#/song?id=67014"), {"source": "netease", "id": "67014"})
        self.assertEqual(source_from_text("https://www.bilibili.com/video/BV1Bpn162Ekn?p=2")["page"], 2)
        with self.assertRaises(ValueError):
            source_from_text("E:/private.wav")


if __name__ == "__main__":
    unittest.main()
