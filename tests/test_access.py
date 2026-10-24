from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from configure import QQ_PLATFORM_ID, apply_access, read_json, ROOT

from astrbot.core.pipeline.whitelist_check.stage import WhitelistCheckStage
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


if __name__ == "__main__":
    unittest.main()
