from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from configure import apply_reply_policy
from plugins.astrbot_plugin_lonelyfriend_memory.main import LonelyFriendMemory
from astrbot.api.provider import LLMResponse
from astrbot.core.config.default import DEFAULT_CONFIG
from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import MessageEventResult, ResultContentType
from astrbot.core.pipeline.respond.stage import RespondStage
from astrbot.core.pipeline.result_decorate.stage import ResultDecorateStage
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent


class ReplyDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def deliver(self, text, *, group=False, command=False, tool_preamble=False):
        config = copy.deepcopy(DEFAULT_CONFIG)
        apply_reply_policy(config)
        config["platform_settings"]["segmented_reply"]["interval"] = "0,0"
        config["platform_settings"].update(reply_with_mention=False, reply_with_quote=False)
        config["t2i"] = False
        context = SimpleNamespace(
            astrbot_config=config,
            plugin_manager=SimpleNamespace(context=SimpleNamespace(
                get_using_tts_provider_async=AsyncMock(return_value=None),
            )),
        )
        decorate, respond = ResultDecorateStage(), RespondStage()
        await decorate.initialize(context)
        await respond.initialize(context)
        message = AstrBotMessage()
        message.type = MessageType.GROUP_MESSAGE if group else MessageType.FRIEND_MESSAGE
        message.sender = MessageMember(user_id="100000101", nickname="分段验收用户")
        message.group_id = "200000101" if group else ""
        message.self_id = "999999999"
        message.message_id = "1001"
        message.message = []
        bot = SimpleNamespace(send_private_msg=AsyncMock(), send_group_msg=AsyncMock())
        event = AiocqhttpMessageEvent("验收", message, PlatformMetadata(
            name="aiocqhttp", description="isolated delivery test", id="cuteq_qq",
        ), message.group_id or message.sender.user_id, bot)
        event.plugins_name = []
        result_type = ResultContentType.GENERAL_RESULT if command else ResultContentType.LLM_RESULT
        event.set_result(MessageEventResult(chain=[Plain(text)], result_content_type=result_type))
        if tool_preamble:
            adapter = object.__new__(LonelyFriendMemory)
            await adapter.on_final_reply(event, LLMResponse(role="assistant", completion_text=text))
            event.get_result().chain.insert(0, Plain("我先翻翻记录。"))
            await adapter.on_format_reply(event)
        with patch("astrbot.core.platform.astr_message_event.Metric.upload", new_callable=AsyncMock):
            async for _ in decorate.process(event):
                pass
            await respond.process(event)
        target = bot.send_group_msg if group else bot.send_private_msg
        other = bot.send_private_msg if group else bot.send_group_msg
        other.assert_not_awaited()
        self.assertIsNone(event.get_result())
        return ["".join(part["data"]["text"] for part in call.kwargs["message"])
                for call in target.await_args_list]

    async def test_screenshot_becomes_three_qq_messages(self):
        paragraphs = ["哼，还献花，花样倒是不少", "不过你把花举这么高，也别光顾着显摆，递过来啊", "......"]
        for group in (False, True):
            with self.subTest(group=group):
                self.assertEqual(await self.deliver("\n\n".join(paragraphs), group=group), paragraphs)

    async def test_sentence_punctuation_and_single_line_break_stay_together(self):
        text = "你吃饭了吗？我刚准备去吃。\n先把手上的事情放一放！"
        self.assertEqual(await self.deliver(text), [text])

    async def test_crlf_and_extra_blank_lines_do_not_send_empty_messages(self):
        self.assertEqual(await self.deliver("第一段\r\n \t\r\n\r\n第二段\r\n\r\n……\r\n"),
                         ["第一段", "第二段", "……"])

    async def test_reply_longer_than_old_150_character_limit_still_splits(self):
        paragraphs = ["这是同一个完整段落。" * 12, "这是另一个完整段落。" * 12]
        self.assertEqual(await self.deliver("\n\n".join(paragraphs)), paragraphs)

    async def test_fenced_code_preserves_blank_lines_and_indentation(self):
        for fence in ("```", "~~~"):
            text = f"可以这样写：\n\n{fence}python\ndef greet():\n    print('你好')\n\n    return 1\n{fence}\n\n这样就行。"
            with self.subTest(fence=fence):
                self.assertEqual(await self.deliver(text), [text])

    async def test_command_output_is_not_split(self):
        text = "运行状态\n\n账号状态\n\n记忆状态"
        self.assertEqual(await self.deliver(text, command=True), [text])

    async def test_final_answer_replacement_preserves_paragraph_delivery(self):
        paragraphs = ["找到了，是赵衡。", "后来你补充说，他已经把署名改回来了。", "这次记着呢。"]
        self.assertEqual(await self.deliver("\n\n".join(paragraphs), tool_preamble=True), paragraphs)


if __name__ == "__main__":
    unittest.main()
