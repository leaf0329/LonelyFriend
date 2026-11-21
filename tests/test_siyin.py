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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from plugins.astrbot_plugin_lonelyfriend_activities import siyin
from astrbot.api.event import MessageChain
from astrbot.api.message_components import At, AtAll, Image, Plain, Record, Reply
from astrbot.core.message.message_event_result import MessageEventResult, ResultContentType
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent


def wav_bytes():
    data = io.BytesIO()
    with wave.open(data, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\x10\x00\xf0\xff" * 2400)
    return data.getvalue()


class SiyinTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="siyin-test-")
        self.addCleanup(self.directory.cleanup)
        config = json.loads((ROOT / "config/siyin.example.json").read_text())
        config["probability"] = 1
        self.speech = siyin.SiyinSpeech(config)
        await self.speech.client.aclose()
        self.calls = []
        self.audio = wav_bytes()
        self.health = {"ok": True, "active_requests": {}, "engine": {"queue": 0}}
        self.failure = None

        async def handler(request):
            self.calls.append(request)
            if self.failure:
                raise self.failure
            if request.method == "GET":
                return httpx.Response(200, json=self.health)
            await asyncio.sleep(0.01)
            return httpx.Response(200, content=self.audio, headers={"Content-Type": "audio/wav"})

        self.speech.client = httpx.AsyncClient(base_url="http://127.0.0.1:19881",
            headers={"Authorization": "Bearer fixture-secret"}, transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(self.speech.close)
        self.patch = patch.object(siyin, "get_astrbot_temp_path", return_value=self.directory.name)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.origin = "cuteq_qq:FriendMessage:12345"

    def event(self, text="你好呀。", *, group="", origin=None, command=False, cron=False, extra_part=None, incoming=None):
        extras = {"cron_payload": {"job": True}} if cron else {}
        chain = [Plain(text)] + ([extra_part] if extra_part else [])
        result = MessageEventResult(chain=chain, result_content_type=(
            ResultContentType.GENERAL_RESULT if command else ResultContentType.LLM_RESULT))
        return SimpleNamespace(is_stopped=lambda: False, get_platform_id=lambda: "cuteq_qq",
            get_group_id=lambda: group, unified_msg_origin=origin or self.origin,
            get_self_id=lambda: "999999999", get_messages=lambda: incoming or [Plain("你好")],
            get_result=lambda: result, get_extra=extras.get, set_extra=extras.__setitem__)

    async def test_group_voice_requires_actual_at_and_an_explicit_current_message_request(self):
        origin = "cuteq_qq:GroupMessage:54321"
        self.speech.config["probability"] = 0
        self.speech.next_attempt[origin] = float("inf")
        for request in ("用语音回复我，你今天怎么样？", "请发条语音。", "能不能用语音说一句你好", "不要文字，请用语音回答"):
            event = self.event(group="54321", origin=origin, incoming=[At(qq="999999999"), Plain(request)])
            await self.speech.decorate(event, [origin])
            self.assertIsInstance(event.get_result().chain[0], Record)
        self.assertEqual(sum(call.method == "POST" for call in self.calls), 4)

    async def test_group_voice_does_not_trigger_from_quotes_other_mentions_or_negative_requests(self):
        origin = "cuteq_qq:GroupMessage:54321"
        for incoming in ([At(qq="999999999"), Plain("你好")],
                         [Reply(id="1", sender_id="999999999"), Plain("用语音回复")],
                         [At(qq="888888888"), Plain("发条语音")],
                         [AtAll(), Plain("发条语音")],
                         [Plain("@祀 用语音回复")],
                         [At(qq="999999999"), Reply(id="1", sender_id="999999999", message_str="用语音回复"), Plain("继续")],
                         [At(qq="999999999"), Plain("别发语音，用文字说")],
                         [At(qq="999999999"), Plain("不要用语音回复我")],
                         [At(qq="999999999"), Plain("你支持语音回复吗")]):
            with self.subTest(incoming=incoming):
                event = self.event(group="54321", origin=origin, incoming=incoming)
                await self.speech.decorate(event, [origin])
                self.assertIsInstance(event.get_result().chain[0], Plain)
        self.assertFalse(self.calls)

    async def test_simultaneous_explicit_group_voice_requests_do_not_queue_multiple_syntheses(self):
        origin = "cuteq_qq:GroupMessage:54321"
        incoming = [At(qq="999999999"), Plain("请用语音回复")]
        events = [self.event(group="54321", origin=origin, incoming=incoming) for _ in range(2)]
        await asyncio.gather(*(self.speech.decorate(event, [origin]) for event in events))
        self.assertEqual(sum(call.method == "POST" for call in self.calls), 1)

    async def test_only_explicit_group_voice_requests_get_a_short_spoken_reply_instruction(self):
        from plugins.astrbot_plugin_lonelyfriend_activities.main import LonelyFriendActivities
        event = self.event(group="54321", incoming=[At(qq="999999999"), Plain("用语音回复")])
        request = SimpleNamespace(system_prompt="当前人设")
        await LonelyFriendActivities.prepare_group_voice_reply(None, event, request)
        self.assertTrue(request.system_prompt.startswith("当前人设"))
        self.assertIn("100字", request.system_prompt)
        request.system_prompt = "当前人设"
        await LonelyFriendActivities.prepare_group_voice_reply(None, self.event(group="54321"), request)
        self.assertEqual(request.system_prompt, "当前人设")

    async def test_success_becomes_one_qq_voice_with_its_original_text(self):
        event = self.event("第一段。\n\n第二段。")
        await self.speech.decorate(event, [self.origin])
        component, = event.get_result().chain
        self.assertIsInstance(component, Record)
        self.assertEqual(component.text, "第一段。\n\n第二段。")
        self.assertEqual(json.loads(self.calls[-1].content), {"text": component.text, "voice": "si"})
        bot = SimpleNamespace(send_private_msg=AsyncMock(), send_group_msg=AsyncMock())
        await AiocqhttpMessageEvent.send_message(bot, MessageChain([component]), is_group=False, session_id="12345")
        bot.send_group_msg.assert_not_awaited()
        payload = bot.send_private_msg.await_args.kwargs["message"]
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["type"], "record")
        self.assertEqual(base64.b64decode(payload[0]["data"]["file"].removeprefix("base64://")), self.audio)

    async def test_groups_unapproved_sessions_commands_and_nonspoken_content_remain_text(self):
        for arguments in ({"group": "54321"}, {"origin": "cuteq_qq:FriendMessage:99999"},
                          {"command": True}, {"cron": True}, {"text": "长" * 121},
                          {"text": "参见 https://example.org"}, {"text": "```python\npass\n```"},
                          {"extra_part": Image.fromURL("https://example.org/image.png")}):
            with self.subTest(arguments=arguments):
                event = self.event(**arguments)
                original = list(event.get_result().chain)
                await self.speech.decorate(event, [self.origin])
                self.assertEqual(event.get_result().chain, original)
        self.assertFalse(self.calls)

    async def test_desktop_offline_preserves_the_original_reply_without_retry(self):
        self.failure = httpx.ConnectError("desktop offline")
        event = self.event("原有文字\n\n继续保留")
        original = list(event.get_result().chain)
        await self.speech.decorate(event, [self.origin])
        self.assertEqual(event.get_result().chain, original)
        self.assertEqual(len(self.calls), 1)

    async def test_invalid_audio_and_busy_engine_do_not_replace_text(self):
        self.audio = b"not wav"
        event = self.event()
        await self.speech.decorate(event, [self.origin])
        self.assertIsInstance(event.get_result().chain[0], Plain)
        self.speech.next_attempt.clear()
        self.calls.clear()
        self.health["engine"]["queue"] = 1
        await self.speech.decorate(self.event(), [self.origin])
        self.assertEqual([call.method for call in self.calls], ["GET"])

    async def test_parallel_replies_and_repeated_hooks_do_not_create_a_voice_burst(self):
        first, second = self.event(), self.event("另一个回复。")
        await asyncio.gather(self.speech.decorate(first, [self.origin]), self.speech.decorate(second, [self.origin]))
        await self.speech.decorate(first, [self.origin])
        self.assertEqual(sum(call.method == "POST" for call in self.calls), 1)
        self.assertEqual(sum(isinstance(event.get_result().chain[0], Record) for event in (first, second)), 1)

    async def test_probability_can_leave_a_normal_reply_as_text(self):
        self.speech.config["probability"] = 0.15
        event = self.event()
        with patch.object(siyin.random, "random", return_value=0.9):
            await self.speech.decorate(event, [self.origin])
        self.assertFalse(self.calls)
        self.assertIsInstance(event.get_result().chain[0], Plain)


if __name__ == "__main__":
    unittest.main()
