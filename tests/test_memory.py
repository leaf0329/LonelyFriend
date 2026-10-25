from __future__ import annotations

import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from configure import apply_context_policy, apply_memory_policy, read_baseline_config
from evaluate_memory import BOT, PLUGIN, MemoryCompanionService, event_for
from astrbot.api.message_components import Plain
from astrbot.core.agent.context.config import ContextConfig
from astrbot.core.agent.context.manager import ContextManager
from astrbot.core.agent.message import Message

sys.path.insert(0, str(ROOT))
from plugins.astrbot_plugin_lonelyfriend_memory import main as capture_module


class OriginalMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="memory-test-", dir=ROOT / ".cache")).resolve()
        self.assertTrue(self.directory.is_relative_to((ROOT / ".cache").resolve()))
        config = read_baseline_config("astrbot_plugin_memory_companion_config.json")
        apply_memory_policy(config)
        self.service = MemoryCompanionService(context=None, config=config, plugin_root=PLUGIN, data_dir=self.directory)

    async def asyncTearDown(self):
        await self.service.aclose()

    def incoming(self, text, *, user="100000101", group=""):
        event = event_for(user, text, group)
        event.message_obj.message = [Plain(text)]
        event.message_obj.timestamp = int(time.time())
        return event

    async def test_long_original_survives_restart_and_is_not_duplicated(self):
        text = "那天我一路讲起退租的经过。" * 700 + "最后顺嘴提到：备用钥匙在厨房蓝色饼干盒里，盒子在冰箱上面。"
        event = self.incoming(text)
        first = await capture_module.capture_visible_text(self.service, event, "dafeiyu")
        second = await capture_module.capture_visible_text(self.service, event, "dafeiyu")
        self.assertEqual(first["source_ref"], second["source_ref"])
        timeline = await self.service.store.get_timeline_by_ids([first["source_ref"].removeprefix("timeline:")])
        self.assertEqual(text, next(iter(timeline.values()))["content"])
        await self.service.aclose()
        self.service = MemoryCompanionService(context=None, config=self.service.config.raw, plugin_root=PLUGIN, data_dir=self.directory)
        event = self.incoming("备用钥匙在哪里")
        event.persona_id = "dafeiyu"
        result = await self.service.tool_sources(event, action="search", terms=["备用钥匙"], limit=8)
        self.assertTrue(result["ok"])
        self.assertTrue(any("冰箱" in row["excerpt"] for row in result["sources"]))

    async def test_private_source_has_positive_control_and_stays_private(self):
        event = self.incoming("阿岚押金1800元，备用钥匙在蓝色饼干盒里。")
        await capture_module.capture_visible_text(self.service, event, "dafeiyu")
        own = self.incoming("押金")
        own.persona_id = "dafeiyu"
        self.assertTrue((await self.service.tool_sources(own, action="search", terms=["押金"]))["sources"])
        for user, group in (("100000102", ""), ("100000101", "200000101")):
            other = self.incoming("押金", user=user, group=group)
            other.persona_id = "dafeiyu"
            self.assertEqual([], (await self.service.tool_sources(other, action="search", terms=["押金"]))["sources"])

    async def test_old_summarized_source_survives_maintenance(self):
        event = self.incoming("两个月前的共同经历仍应能回查。")
        event.message_obj.timestamp = int((datetime.now(timezone.utc) - timedelta(days=60)).timestamp())
        receipt = await capture_module.capture_visible_text(self.service, event, "dafeiyu")
        source_id = receipt["source_ref"].removeprefix("timeline:")
        await self.service.store.mark_timeline_summarized([source_id])
        result = await self.service._run_raw_event_retention()
        self.assertEqual(0, result["timeline_deleted"])
        self.assertIn(source_id, await self.service.store.get_timeline_by_ids([source_id]))

    async def test_stopped_event_is_not_archived(self):
        event = self.incoming("未获准消息不得存储")
        event.stop_event()
        self.assertIsNone(await capture_module.capture_visible_text(self.service, event, "dafeiyu"))
        self.assertEqual(0, self.service.store._conn.execute("SELECT count(*) FROM timeline").fetchone()[0])


class ContextPolicyTests(unittest.IsolatedAsyncioTestCase):
    def settings(self, provider=None):
        config = read_baseline_config("cmd_config.json")
        apply_context_policy(config)
        compression = config["agent_runner"]["config"]["compression"]
        return ContextConfig(max_context_tokens=config["provider"][0]["max_context_tokens"], enforce_max_turns=compression["max_turns"], llm_compress_keep_recent_ratio=compression["keep_recent_ratio"], llm_compress_instruction=compression["instruction"], llm_compress_provider=provider)

    async def test_more_than_80_short_turns_are_not_silently_discarded(self):
        messages = [Message(role="system", content="大肥鱼人设")]
        for index in range(90):
            messages.extend([Message(role="user", content=f"第{index}轮的个人经历"), Message(role="assistant", content="我听着呢")])
        result = await ContextManager(self.settings()).process(messages)
        self.assertEqual(messages, result)

    async def test_summary_failure_keeps_latest_request_and_complete_tool_rounds(self):
        class UnavailableProvider:
            provider_config = {}

            async def text_chat(self, **kwargs):
                raise RuntimeError("simulated summary outage")

        manager = ContextManager(self.settings(UnavailableProvider()))
        messages = [Message(role="system", content="保留人设")]
        for index in range(30):
            messages.extend([Message(role="user", content="具体经历" * 12000), Message(role="assistant", content="记下这段经过")])
        messages += [Message(role="user", content="查一次历史"), Message(role="assistant", tool_calls=[{"id": "lookup", "function": {"name": "memory_companion_sources", "arguments": "{}"}}]), Message(role="tool", tool_call_id="lookup", content="真实来源"), Message(role="assistant", content="找到依据了"), Message(role="user", content="最新的问题必须保留")]
        result = await manager.process(messages)
        self.assertEqual("保留人设", result[0].content)
        self.assertEqual("最新的问题必须保留", result[-1].content)
        self.assertLess(len(result), len(messages))
        self.assertEqual({call.id for message in result for call in message.tool_calls or []}, {message.tool_call_id for message in result if message.role == "tool"})


if __name__ == "__main__":
    unittest.main()
