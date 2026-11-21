from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from configure import QQ_PLATFORM_ID, apply_access, read_json, ROOT
import configure

from astrbot.core.pipeline.whitelist_check.stage import WhitelistCheckStage
from astrbot.core.pipeline.process_stage.stage import ProcessStage
from astrbot.core.pipeline.process_stage.method.agent_request import AgentRequestSubStage
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.core.star import session_llm_manager, session_plugin_manager
from astrbot.core.star.star_handler import EventType, star_handlers_registry
from astrbot.core.pipeline.waking_check.stage import WakingCheckStage
from astrbot.core.config.default import DEFAULT_CONFIG
from astrbot.api.message_components import At, AtAll, Plain, Reply
from plugins.astrbot_plugin_lonelyfriend_activities.main import LonelyFriendActivities
from astrbot.core.utils.shared_preferences import SharedPreferences
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent


class AccessTests(unittest.IsolatedAsyncioTestCase):
    async def check_access(self, private, groups, sender, group="", admin=False):
        config = copy.deepcopy(read_json(ROOT / "data/cmd_config.json"))
        apply_access(config, {"private_qq": private, "group_qq": groups, "admin_qq": []})
        message = AstrBotMessage()
        message.type = MessageType.GROUP_MESSAGE if group else MessageType.FRIEND_MESSAGE
        message.sender = MessageMember(user_id=sender, nickname="验收用户")
        message.group_id = group
        message.message = []
        message.self_id = "999999999"
        event = AiocqhttpMessageEvent("访问范围验收", message, PlatformMetadata(name="aiocqhttp", description="test", id=QQ_PLATFORM_ID), group or sender, bot=None)
        if admin:
            event.role = "admin"
        stage = WhitelistCheckStage()
        await stage.initialize(SimpleNamespace(astrbot_config=config))
        await stage.process(event)
        return not event.is_stopped()

    async def test_empty_lists_deny_private_and_group(self):
        self.assertFalse(await self.check_access([], [], "100000001"))
        self.assertFalse(await self.check_access([], [], "100000001", "200000001"))

    async def test_allowed_private_contact_can_chat(self):
        self.assertTrue(await self.check_access(["100000001"], [], "100000001"))

    async def test_group_member_does_not_gain_private_access(self):
        self.assertTrue(await self.check_access([], ["200000001"], "100000002", "200000001"))
        self.assertFalse(await self.check_access([], ["200000001"], "100000002"))

    async def test_private_contact_does_not_open_other_groups(self):
        self.assertFalse(await self.check_access(["100000001"], ["200000001"], "100000001", "200000002"))

    async def test_admin_does_not_bypass_the_allowlist(self):
        self.assertFalse(await self.check_access([], [], "100000003", admin=True))

    def test_invalid_account_format_is_rejected(self):
        config = copy.deepcopy(read_json(ROOT / "data/cmd_config.json"))
        with self.assertRaises(ValueError):
            apply_access(config, {"private_qq": [100000001], "group_qq": [], "admin_qq": []})


class GroupConversationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory(prefix="group-policy-test-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "data").mkdir()
        self.database = SQLiteDatabase(str(self.root / "data/data_v4.db"))
        await self.database.initialize()
        self.addAsyncCleanup(self.database.engine.dispose)
        self.group = "cuteq_qq:GroupMessage:200000001"
        self.friend = "cuteq_qq:FriendMessage:100000001"
        for origin in (self.group, self.friend):
            await self.database.insert_preference_or_update("umo", origin, "session_service_config", {
                "val": {"llm_enabled": origin != self.group, "session_enabled": True, "tts_enabled": False},
            })
        with patch.object(configure, "ROOT", self.root):
            await configure.configure_group_conversations({"group_qq": ["200000001", "200000002"]})
        self.preferences = SharedPreferences(self.database, json_storage_path=str(self.root / "unused.json"))
        self.addAsyncCleanup(self.preferences.close)
        for module in (session_llm_manager, session_plugin_manager):
            patcher = patch.object(module, "sp", self.preferences)
            patcher.start()
            self.addCleanup(patcher.stop)

    def event(self, *, group=True, parts=None, text="验收"):
        message = AstrBotMessage()
        message.type = MessageType.GROUP_MESSAGE if group else MessageType.FRIEND_MESSAGE
        message.sender = MessageMember(user_id="100000001", nickname="隔离验收用户")
        message.group_id = "200000001" if group else ""
        message.message = parts or [Plain(text)]
        message.self_id = "999999999"
        event = AiocqhttpMessageEvent(text, message, PlatformMetadata(
            name="aiocqhttp", description="isolated policy test", id=QQ_PLATFORM_ID,
        ), message.group_id or message.sender.user_id, bot=None)
        event.is_at_or_wake_command = True
        return event

    async def test_native_policy_preserves_private_session_and_other_service_settings(self):
        manager = session_llm_manager.SessionServiceManager
        for origin in (self.group, "cuteq_qq:GroupMessage:200000002"):
            self.assertTrue(await manager.is_llm_enabled_for_session(origin))
            self.assertTrue(await manager.is_session_enabled(origin))
        self.assertTrue(await manager.is_llm_enabled_for_session(self.friend))
        settings = await self.preferences.session_get(self.group, "session_service_config")
        self.assertEqual(settings, {"llm_enabled": True, "session_enabled": True, "tts_enabled": False})

    async def process(self, event, *, tool=False):
        config = copy.deepcopy(DEFAULT_CONFIG)
        apply_access(config, {"private_qq": ["100000001"], "group_qq": ["200000001"], "admin_qq": []})
        config["wake_prefix"] = ["/"]
        context = SimpleNamespace(astrbot_config=config, db_helper=None, astrbot_config_id="test")
        waking = WakingCheckStage()
        await waking.initialize(context)
        handler = next(h for h in star_handlers_registry.get_handlers_by_event_type(
            EventType.AdapterMessageEvent, only_activated=False) if h.handler_name == "gate_group_conversation")
        with patch.object(star_handlers_registry, "get_handlers_by_event_type", return_value=[handler]):
            await waking.process(event)
        if event.is_stopped():
            return [], []
        calls = []

        async def run_agent(event, prefix):
            calls.append(event.unified_msg_origin)
            yield

        async def run_commands(event):
            await LonelyFriendActivities.gate_group_conversation(None, event)
            if tool:
                event.set_result(event.plain_result("工具结果"))
                yield
                event.clear_result()

        agent = AgentRequestSubStage()
        agent.ctx = context
        agent.prov_wake_prefix = ""
        agent.agent_sub_stage = SimpleNamespace(process=run_agent)
        stage = ProcessStage()
        stage.ctx = context
        stage.agent_sub_stage = agent
        stage.star_request_sub_stage = SimpleNamespace(process=run_commands)
        outputs = []
        async for _ in stage.process(event):
            if event.get_result():
                outputs.append(event.get_result().get_plain_text())
        return calls, outputs

    async def test_only_real_mentions_and_replies_to_bot_run_the_group_agent(self):
        for parts, text, expected in [
            ([Plain("普通群聊")], "普通群聊", False),
            ([Plain("/不存在的命令")], "/不存在的命令", False),
            ([At(qq="all"), Plain("说句话")], "说句话", False),
            ([AtAll(), Plain("说句话")], "说句话", False),
            ([At(qq="888888888"), Plain("说句话")], "说句话", False),
            ([Reply(id="1", sender_id="888888888"), Plain("继续")], "继续", False),
            ([At(qq="999999999"), Plain("说句话")], "说句话", True),
            ([Reply(id="1", sender_id="999999999"), Plain("继续")], "继续", True),
        ]:
            with self.subTest(parts=parts):
                event = self.event(parts=parts, text=text)
                calls, _ = await self.process(event)
                self.assertEqual(calls, [self.group] if expected else [])
                self.assertFalse(event.is_stopped())
        calls, _ = await self.process(self.event(group=False))
        self.assertEqual(calls, [self.friend])

    async def test_unaddressed_group_tool_result_survives_without_starting_conversation(self):
        event = self.event()
        calls, outputs = await self.process(event, tool=True)
        self.assertEqual(outputs, ["工具结果"])
        self.assertEqual(calls, [])
        self.assertFalse(event.is_stopped())


if __name__ == "__main__":
    unittest.main()
