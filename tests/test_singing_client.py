from __future__ import annotations

import asyncio
import base64
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import wave

import httpx
from astrbot.api.event import MessageChain
from astrbot.api.message_components import File, Plain, Record
from astrbot.core.message.message_event_result import MessageEventResult
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plugins.astrbot_plugin_lonelyfriend_activities import singing


def wav_bytes():
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\x10\x00\xf0\xff" * 24000)
    return output.getvalue()


class SingingClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="singing-client-test-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.origin = "cuteq_qq:GroupMessage:54321"
        self.allowed = [self.origin]
        self.client = singing.SingingClient({"api_base": "http://127.0.0.1:19883", "api_key": "fixture-key"},
            self.directory / "jobs.json", lambda: self.allowed)
        await self.client.http.aclose()
        self.calls = []
        self.state = "done"
        self.audio = wav_bytes()
        self.sdk = SimpleNamespace(send_group_msg=AsyncMock(), send_private_msg=AsyncMock())

        async def respond(request):
            self.calls.append(request)
            if request.url.path == "/v1/health":
                return httpx.Response(200, json={"busy": False})
            if request.url.path == "/v1/search":
                return httpx.Response(200, json={"songs": [{"source": "netease", "id": "123", "title": "song"}]})
            if request.url.path == "/v1/uploads":
                self.assertEqual(await request.aread(), self.audio)
                return httpx.Response(200, json={"source": "upload", "id": "f" * 32})
            if request.url.path.endswith("/audio"):
                return httpx.Response(200, content=self.audio, headers={"Content-Type": "audio/wav"})
            if request.url.path.endswith("/cancel"):
                return httpx.Response(200, json={"status": "cancelled"})
            return httpx.Response(200, json={"status": self.state, "stage": "完成"})

        self.client.http = httpx.AsyncClient(base_url="http://127.0.0.1:19883", transport=httpx.MockTransport(respond))
        self.addAsyncCleanup(self.client.close)
        self.patch = patch.object(singing, "get_astrbot_temp_path", return_value=str(self.directory))
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def event(self, sender="12345", parts=(), fail_media=False):
        extras = {}
        event = SimpleNamespace(unified_msg_origin=self.origin, notices=[],
            get_sender_id=lambda: sender, get_messages=lambda: list(parts),
            get_extra=extras.get, set_extra=extras.__setitem__,
            chain_result=lambda parts: MessageEventResult(chain=parts),
            plain_result=lambda text: MessageEventResult(chain=[Plain(text)]))

        async def send(result):
            if isinstance(result.chain[0], (Record, File)):
                await AiocqhttpMessageEvent.send_message(self.sdk, MessageChain(result.chain), is_group=True, session_id="54321")
                if fail_media:
                    raise TimeoutError("ambiguous QQ response")
            else:
                event.notices.append(result.chain[0].text)
        event.send = send
        return event

    async def finish(self):
        await asyncio.wait_for(asyncio.gather(*list(self.client.tasks.values())), 5)

    async def test_complete_job_is_one_native_qq_voice(self):
        event = self.event()
        job_id = await self.client.start(event, "本地:sample", 0, 2)
        await self.finish()
        payload = self.sdk.send_group_msg.await_args.kwargs["message"]
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["type"], "record")
        self.assertEqual(base64.b64decode(payload[0]["data"]["file"].removeprefix("base64://")), self.audio)
        self.assertEqual(self.client.last_job(event)["delivery"], "sent")
        self.assertEqual(self.client.last_job(event)["id"], job_id)
        self.sdk.send_private_msg.assert_not_awaited()

    async def test_ambiguous_media_delivery_is_not_retried(self):
        event = self.event(fail_media=True)
        await self.client.start(event, "本地:sample", 0, 2)
        await self.finish()
        self.sdk.send_group_msg.assert_awaited_once()
        self.assertEqual(self.client.last_job(event)["delivery"], "unknown")
        self.assertEqual(sum(r.method == "POST" and r.url.path == "/v1/jobs" for r in self.calls), 1)

    async def test_mp3_mode_uses_one_native_file_message(self):
        self.audio = b"ID3" + b"opaque-mp3-fixture"
        event = self.event()
        await self.client.start(event, "本地:sample", 0, 2, mode="file")
        await self.finish()
        self.sdk.send_group_msg.assert_awaited_once()
        payload = self.sdk.send_group_msg.await_args.kwargs["message"]
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["type"], "file")
        self.assertTrue(payload[0]["data"]["file"].startswith("file:"))
        self.assertEqual(self.client.last_job(event)["delivery"], "sent")

    async def test_scope_revocation_suppresses_audio_and_status_notifications(self):
        event = self.event()
        await self.client.start(event, "本地:sample", 0, 2)
        self.allowed.clear()
        await self.finish()
        self.sdk.send_group_msg.assert_not_awaited()
        self.assertEqual(event.notices, [])

    async def test_search_and_file_selection_are_isolated_by_sender(self):
        owner, other = self.event(), self.event(sender="98765")
        await self.client.search(owner, "song")
        with self.assertRaisesRegex(singing.SingingError, "搜歌列表"):
            await self.client.start(other, "1", 0, 2)
        path = self.directory / "source.wav"
        path.write_bytes(self.audio)
        self.client.remember_file(self.event(parts=[File(name="source.wav", file=str(path))]))
        self.assertEqual(sum(r.url.path == "/v1/uploads" for r in self.calls), 0)
        with self.assertRaisesRegex(singing.SingingError, "先在这个会话发送"):
            await self.client.upload_file(other)
        await self.client.start(self.event(), "文件", 0, 2)
        await self.finish()
        self.assertTrue(path.is_file())
        self.assertEqual(sum(r.url.path == "/v1/uploads" for r in self.calls), 1)

    async def test_invalid_time_and_repeated_request_do_not_create_extra_jobs(self):
        event = self.event()
        with self.assertRaises(ValueError):
            await self.client.start(event, "本地:sample", 0, 60)
        self.assertEqual(self.calls, [])
        await self.client.start(event, "本地:sample", 0, 2)
        with self.assertRaisesRegex(singing.SingingError, "本轮已提交"):
            await self.client.start(event, "本地:sample", 0, 2)
        await self.finish()
        self.assertEqual(sum(r.method == "POST" and r.url.path == "/v1/jobs" for r in self.calls), 1)

    async def test_cancel_stops_pending_delivery(self):
        self.state = "running"
        event = self.event()
        await self.client.start(event, "本地:sample", 0, 2)
        text = await self.client.cancel(event)
        self.assertIn("已取消", text)
        self.assertFalse(self.client.tasks)
        await self.finish()
        self.sdk.send_group_msg.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
