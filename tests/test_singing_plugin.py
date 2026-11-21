from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plugins.astrbot_plugin_lonelyfriend_activities.main import LonelyFriendActivities
from astrbot.api.message_components import Plain
from astrbot.core.message.message_event_result import MessageEventResult


class SingingPluginTests(unittest.IsolatedAsyncioTestCase):
    def plugin(self):
        plugin = LonelyFriendActivities.__new__(LonelyFriendActivities)
        plugin.origins = lambda: ["cuteq_qq:GroupMessage:12345"]
        plugin.singing = SimpleNamespace(start=AsyncMock(return_value="a" * 32), tasks={})
        plugin.speech = SimpleNamespace(decorate=AsyncMock())
        return plugin

    def event(self, *, origin="cuteq_qq:GroupMessage:12345", cron=False):
        extras = {"cron_payload": {"task": True}} if cron else {}
        result = MessageEventResult(chain=[Plain("保持原文")])
        event = SimpleNamespace(unified_msg_origin=origin, stopped=False,
            get_extra=extras.get, get_result=lambda: result,
            plain_result=lambda text: MessageEventResult(chain=[Plain(text)]))
        event.stop_event = lambda: setattr(event, "stopped", True)
        return event

    async def test_command_passes_explicit_timing_pitch_and_file_mode(self):
        plugin, event = self.plugin(), self.event()
        results = [r async for r in plugin.singing_cover(event, "1 3:53-4:15.5 -12 文件")]
        self.assertTrue(event.stopped)
        plugin.singing.start.assert_awaited_once_with(event, "1", 233, 255.5, -12, "file")
        self.assertIn("已接下翻唱任务", results[0].chain[0].text)

    async def test_unapproved_context_and_cron_do_not_start_singing(self):
        for event in (self.event(origin="cuteq_qq:GroupMessage:99999"), self.event(cron=True)):
            plugin = self.plugin()
            results = [r async for r in plugin.singing_cover(event, "1 0:00-0:20")]
            plugin.singing.start.assert_not_awaited()
            self.assertIn("未获准", results[0].chain[0].text)

    async def test_active_singing_keeps_regular_reply_as_text(self):
        plugin, event = self.plugin(), self.event()
        plugin.singing.tasks["active"] = object()
        await plugin.occasional_speech(event)
        plugin.speech.decorate.assert_not_awaited()
        self.assertEqual(event.get_result().chain[0].text, "保持原文")
        plugin.singing.tasks.clear()
        await plugin.occasional_speech(event)
        plugin.speech.decorate.assert_awaited_once()

    async def test_llm_number_schema_accepts_integral_pitch_and_rejects_fractions(self):
        plugin, event = self.plugin(), self.event()
        result = await plugin.cover_music(event, "本地:sample", "3:53", "4:15", 0.0)
        self.assertIn("已提交", result)
        self.assertIs(type(plugin.singing.start.await_args.args[4]), int)
        plugin.singing.start.reset_mock()
        result = await plugin.cover_music(event, "本地:sample", "3:53", "4:15", 1.5)
        self.assertIn("整数半音", result)
        plugin.singing.start.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
