from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from configure import apply_memory_policy, read_baseline_config, read_json
from evaluate_memory import PLUGIN, MemoryCompanionService, event_for, prepare_indexes
from plugins.astrbot_plugin_lonelyfriend_memory.local_embedding import LocalChineseEmbeddingProvider
from plugins.astrbot_plugin_lonelyfriend_memory.main import LonelyFriendMemory, capture_visible_text, finish_source_index, inject_source_recall, normalize_source_projection
from astrbot.api.message_components import Plain
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.core.message.message_event_result import MessageEventResult, ResultContentType
from huggingface_hub.errors import LocalEntryNotFoundError


class SemanticMemoryTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.embedding_config = read_json(ROOT / "config/memory-policy.json")["embedding_provider"]
        cls.embedding = LocalChineseEmbeddingProvider(cls.embedding_config, {})

    @classmethod
    def tearDownClass(cls):
        asyncio.run(cls.embedding.terminate())

    async def asyncSetUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="semantic-test-", dir=ROOT / ".cache"))
        config = read_baseline_config("astrbot_plugin_memory_companion_config.json")
        apply_memory_policy(config)
        self.context = SimpleNamespace(get_embedding_provider_by_id=lambda _: self.embedding)
        self.service = MemoryCompanionService(context=self.context, config=config, plugin_root=PLUGIN, data_dir=self.directory)
        await self.service.initialize_database()

    async def asyncTearDown(self):
        await self.service.aclose()

    async def capture(self, text, user="100000101", group=""):
        event = event_for(user, text, group)
        event.message_obj.message = [Plain(text)]
        event.message_obj.timestamp = int(time.time())
        receipt = await capture_visible_text(self.service, event, "dafeiyu")
        return event, await self.service.identity.resolve_event_context(event), receipt["source_ref"]

    async def test_paraphrase_recall_stays_inside_authorized_scopes(self):
        own, own_ctx, own_ref = await self.capture("周三同事赵衡把我写的方案署成了他的名字，我难过的是劳动成果被冒领。")
        other, other_ctx, other_ref = await self.capture("我家的游戏手柄放在电视柜最下面的抽屉里。", user="100000102")
        group, group_ctx, group_ref = await self.capture("群里的活动定在周日午后，大家到旧书店交换小说。", group="200000101")
        await prepare_indexes(self.service, self.embedding, [own_ctx, other_ctx, group_ctx])
        query = "之前那个抢我功劳的人叫什么？"
        positive = await self.service.tool_discover_sources(own, query=query, limit=5)
        self.assertTrue(positive["ok"], positive)
        self.assertIn(own_ref, [row["source_ref"] for row in positive["sources"]])
        request = ProviderRequest(prompt=query, func_tool=SimpleNamespace(tools=[SimpleNamespace(name="memory_companion_discover_sources", active=True)]))
        self.assertGreater(await inject_source_recall(self.service, event_for("100000101", query), request), 0)
        self.assertIn("赵衡", "".join(part.text for part in request.extra_user_content_parts))
        disabled = ProviderRequest(prompt=query, func_tool=SimpleNamespace(tools=[]))
        self.assertEqual(0, await inject_source_recall(self.service, event_for("100000101", query), disabled))
        self.assertFalse(disabled.extra_user_content_parts)
        for event, allowed_ref in ((other, other_ref), (group, group_ref)):
            result = await self.service.tool_discover_sources(event, query=query, limit=5)
            self.assertTrue(result["ok"], result)
            self.assertEqual({allowed_ref}, {row["source_ref"] for row in result["sources"]})
            direct = await self.service.tool_sources(event, action="read", source_ref=own_ref)
            self.assertFalse(any("赵衡" in row.get("excerpt", "") for row in direct.get("sources", [])))

    async def test_long_original_tail_is_semantically_retrievable_after_restart(self):
        text = "这段是在聊天气和散步，那天天气很好，公园里开了花。" * 420
        text += "最后顺嘴提一句，父亲留下的柯达胶片机放在书房靠窗的藤编篮子里，不在客厅。"
        event, ctx, source_ref = await self.capture(text)
        self.assertTrue(await finish_source_index(self.service, ctx))
        status = await self.service.source_semantic_status()
        self.assertEqual("ready", status["state"])
        self.assertGreater(status["ready_documents"], 32)
        await self.service.aclose()
        self.service = MemoryCompanionService(context=self.context, config=self.service.config.raw, plugin_root=PLUGIN, data_dir=self.directory)
        normalize_source_projection(self.service)
        result = await self.service.tool_discover_sources(event_for("100000101", "父亲的老相机"), query="爸爸那台老相机收在哪里？", limit=5)
        self.assertTrue(result["ok"], result)
        self.assertIn(source_ref, [row["source_ref"] for row in result["sources"]])
        self.assertIn("藤编篮子", json.dumps(result, ensure_ascii=False))

    async def test_missing_model_does_not_disable_original_keyword_search(self):
        event, ctx, source_ref = await self.capture("备用钥匙在厨房绿色饼干盒里。")
        await prepare_indexes(self.service, self.embedding, [ctx])
        broken = LocalChineseEmbeddingProvider({**self.embedding_config, "embedding_cache_dir": str(self.directory / "empty-model-cache")}, {})
        try:
            with self.assertRaises(LocalEntryNotFoundError):
                await broken.get_embedding("备用钥匙")
            self.service.context = SimpleNamespace(get_embedding_provider_by_id=lambda _: broken)
            failed = await self.service.tool_discover_sources(event, query="开门的东西")
            self.assertFalse(failed["ok"])
            keyword = await self.service.tool_sources(event, action="search", terms=["备用钥匙"])
            self.assertTrue(keyword["ok"], keyword)
            self.assertIn(source_ref, [row["source_ref"] for row in keyword["sources"]])
        finally:
            await broken.terminate()

    async def test_final_answer_replaces_contradictory_tool_preamble(self):
        star = object.__new__(LonelyFriendMemory)
        event = event_for("100000101", "那个抢功的人叫什么")
        await star.on_final_reply(event, LLMResponse(role="assistant", completion_text="赵衡。"))
        event.set_result(MessageEventResult(chain=[Plain("没印象，得先翻翻。"), Plain("赵衡。")], result_content_type=ResultContentType.LLM_RESULT))
        await star.on_format_reply(event)
        self.assertEqual("赵衡。", event.get_result().get_plain_text())
