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
from astrbot.core.star import session_llm_manager
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
                "val": {"llm_enabled": True, "session_enabled": True, "tts_enabled": False},
            })
        with patch.object(configure, "ROOT", self.root):
            await configure.disable_group_conversations({"group_qq": ["200000001", "200000002"]})
        self.preferences = SharedPreferences(self.database, json_storage_path=str(self.root / "unused.json"))
        self.addAsyncCleanup(self.preferences.close)
        patcher = patch.object(session_llm_manager, "sp", self.preferences)
        patcher.start()
        self.addCleanup(patcher.stop)

    def event(self, *, group=True):
        message = AstrBotMessage()
        message.type = MessageType.GROUP_MESSAGE if group else MessageType.FRIEND_MESSAGE
        message.sender = MessageMember(user_id="100000001", nickname="隔离验收用户")
        message.group_id = "200000001" if group else ""
        message.message = []
        message.self_id = "999999999"
        event = AiocqhttpMessageEvent("验收", message, PlatformMetadata(
            name="aiocqhttp", description="isolated policy test", id=QQ_PLATFORM_ID,
        ), message.group_id or message.sender.user_id, bot=None)
        event.is_at_or_wake_command = True
        return event

    async def test_native_policy_preserves_private_session_and_other_service_settings(self):
        manager = session_llm_manager.SessionServiceManager
        for origin in (self.group, "cuteq_qq:GroupMessage:200000002"):
            self.assertFalse(await manager.is_llm_enabled_for_session(origin))
            self.assertTrue(await manager.is_session_enabled(origin))
        self.assertTrue(await manager.is_llm_enabled_for_session(self.friend))
        settings = await self.preferences.session_get(self.group, "session_service_config")
        self.assertEqual(settings, {"llm_enabled": False, "session_enabled": True, "tts_enabled": False})

    async def test_group_agent_is_silent_while_private_agent_runs(self):
        calls = []

        async def run_agent(event, prefix):
            calls.append(event.unified_msg_origin)
            yield

        stage = AgentRequestSubStage()
        stage.ctx = SimpleNamespace(astrbot_config={"provider_settings": {"enable": True}})
        stage.prov_wake_prefix = ""
        stage.agent_sub_stage = SimpleNamespace(process=run_agent)
        for group in (True, False):
            async for _ in stage.process(self.event(group=group)):
                pass
        self.assertEqual(calls, [self.friend])

    async def test_group_tool_result_survives_disabled_agent(self):
        event = self.event()
        event.set_extra("activated_handlers", ["tool-command"])

        async def run_command(event):
            event.set_result(event.plain_result("工具结果"))
            yield

        async def run_agent(event, prefix):
            self.fail("A disabled group agent must not run")
            yield

        agent = AgentRequestSubStage()
        agent.ctx = SimpleNamespace(astrbot_config={"provider_settings": {"enable": True}})
        agent.prov_wake_prefix = ""
        agent.agent_sub_stage = SimpleNamespace(process=run_agent)
        stage = ProcessStage()
        stage.ctx = agent.ctx
        stage.agent_sub_stage = agent
        stage.star_request_sub_stage = SimpleNamespace(process=run_command)
        results = []
        async for _ in stage.process(event):
            results.append(event.get_result().get_plain_text())
        self.assertEqual(results, ["工具结果"])
        self.assertFalse(event.is_stopped())


if __name__ == "__main__":
    unittest.main()
