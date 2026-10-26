from __future__ import annotations

import asyncio
import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from plugins.astrbot_plugin_lonelyfriend_memory.main import LonelyFriendMemory
from plugins.astrbot_plugin_lonelyfriend_memory.stickers import StickerLibrary
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent

GIF = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")


class StickerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cache = (ROOT / ".cache").resolve()
        self.temporary = tempfile.TemporaryDirectory(prefix="sticker-test-", dir=cache)
        self.directory = Path(self.temporary.name).resolve()
        assert self.directory.is_relative_to(cache)
        (self.directory / "files").mkdir()
        self.items = [
            {"id": "happy-a", "name": "开心", "emotion": "开心", "tags": ["庆祝"], "description": "收到好消息", "file": "happy-a.gif"},
            {"id": "happy-b", "name": "开心", "emotion": "开心", "tags": ["庆祝"], "description": "开心笑一下", "file": "happy-b.gif"},
            {"id": "sleepy", "name": "晚安", "emotion": "疲惫", "tags": ["睡觉", "困了"], "description": "准备休息", "file": "sleepy.gif"},
        ]
        for item in self.items:
            item["review"] = {"status": "approved"}
            (self.directory / "files" / item["file"]).write_bytes(GIF)
        self.write_catalog()
        self.library = StickerLibrary(self.directory)
        self.adapter = object.__new__(LonelyFriendMemory)
        self.adapter._stickers = self.library
        self.adapter._sticker_recent = {}

    def tearDown(self):
        self.temporary.cleanup()

    def write_catalog(self):
        (self.directory / "catalog.json").write_text(json.dumps({"schema": 1, "stickers": self.items}), encoding="utf-8")

    def event(self, group=False):
        message = AstrBotMessage()
        message.type = MessageType.GROUP_MESSAGE if group else MessageType.FRIEND_MESSAGE
        message.sender = MessageMember(user_id="100000101", nickname="表情验收用户")
        message.group_id = "200000101" if group else ""
        message.self_id = "999999999"
        message.message_id = "1001"
        message.message = []
        bot = SimpleNamespace(send_private_msg=AsyncMock(), send_group_msg=AsyncMock())
        return AiocqhttpMessageEvent("发个表情", message, PlatformMetadata(
            name="aiocqhttp", description="isolated sticker test", id="cuteq_qq",
        ), message.group_id or message.sender.user_id, bot)

    def test_scene_search_and_recent_selection(self):
        self.assertEqual(self.library.choose("睡觉 晚安", [])["id"], "sleepy")
        self.assertEqual(self.library.choose("开心", ["happy-a"])["id"], "happy-b")
        self.assertIsNone(self.library.choose("没有这种表情-unknown", []))

    def test_catalog_cannot_reference_files_outside_sticker_directory(self):
        self.items[0]["file"] = "../cuteq-private.json"
        self.write_catalog()
        with self.assertRaises(ValueError):
            StickerLibrary(self.directory)

    def test_missing_collection_is_optional(self):
        self.assertEqual(StickerLibrary(self.directory / "not-installed").entries, [])

    def test_unreviewed_or_reserved_images_are_never_automatically_selected(self):
        for item in self.items:
            item["review"] = {"status": "reserved"}
        self.items[0].pop("review")
        self.write_catalog()
        self.assertIsNone(StickerLibrary(self.directory).choose("开心", []))

    def test_original_unverified_caption_does_not_affect_search(self):
        self.library.entries[0]["source_description"] = "恐吓"
        self.library.entries[0]["source_tags"] = ["恐吓"]
        self.assertEqual(self.library.search("恐吓"), [])

    async def test_private_send_preserves_gif_bytes_and_qq_sticker_type(self):
        event = self.event()
        result = json.loads(await self.adapter.send_local_sticker(event, "晚安"))
        self.assertTrue(result["ok"])
        call = event.bot.send_private_msg.await_args.kwargs
        self.assertEqual(call["user_id"], 100000101)
        image = call["message"][0]
        self.assertEqual(image["type"], "image")
        self.assertEqual(image["data"]["sub_type"], 1)
        self.assertEqual(base64.b64decode(image["data"]["file"].removeprefix("base64://")), GIF)
        event.bot.send_group_msg.assert_not_awaited()

    async def test_group_send_stays_in_current_group(self):
        event = self.event(group=True)
        result = json.loads(await self.adapter.send_local_sticker(event, "开心"))
        self.assertTrue(result["ok"])
        self.assertEqual(event.bot.send_group_msg.await_args.kwargs["group_id"], 200000101)
        event.bot.send_private_msg.assert_not_awaited()

    async def test_parallel_calls_send_at_most_one_picture_in_a_turn(self):
        event = self.event()
        results = await asyncio.gather(self.adapter.send_local_sticker(event, "开心"),
                                       self.adapter.send_local_sticker(event, "晚安"))
        self.assertEqual(sum(json.loads(result)["ok"] for result in results), 1)
        self.assertEqual(event.bot.send_private_msg.await_count, 1)

    async def test_unknown_request_does_not_read_or_send_an_arbitrary_file(self):
        event = self.event()
        result = json.loads(await self.adapter.send_local_sticker(event, "../../cuteq-private.json"))
        self.assertFalse(result["ok"])
        event.bot.send_private_msg.assert_not_awaited()

    async def test_failed_send_is_not_reported_as_success_or_retried(self):
        event = self.event()
        event.bot.send_private_msg.side_effect = RuntimeError("transport failed")
        with self.assertRaisesRegex(RuntimeError, "transport failed"):
            await self.adapter.send_local_sticker(event, "晚安")
        result = json.loads(await self.adapter.send_local_sticker(event, "晚安"))
        self.assertFalse(result["ok"])
        self.assertEqual(event.bot.send_private_msg.await_count, 1)

    async def test_stopped_message_cannot_send(self):
        event = self.event()
        event.stop_event()
        result = json.loads(await self.adapter.send_local_sticker(event, "开心"))
        self.assertFalse(result["ok"])
        event.bot.send_private_msg.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
