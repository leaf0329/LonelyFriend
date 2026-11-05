from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from plugins.astrbot_plugin_lonelyfriend_activities import main as activities
from plugins.astrbot_plugin_lonelyfriend_activities.command_menu import render_menu, visible_commands
from astrbot.core.message.message_event_result import MessageEventResult
from astrbot.core.star.filter.command import CommandFilter


def command(name, plugin="builtin_commands", **changes):
    return {
        "effective_command": name, "original_command": name, "aliases": [], "description": "",
        "plugin": plugin, "plugin_display_name": plugin, "handler_name": name,
        "permission": "everyone", "enabled": True, "plugin_activated": True, "has_conflict": False,
        "reserved": plugin == "builtin_commands", "is_group": False, "sub_commands": [], **changes,
    }


class MenuFormattingTests(unittest.TestCase):
    def render(self, items, query="", prefix="/", entry="菜单"):
        return render_menu(visible_commands(items, lambda _: True), query, prefix=prefix,
                           entry_command=entry, capability_hints=[])

    def test_nested_children_and_unavailable_parents(self):
        items = [command("mcomp", "astrbot_plugin_memory_companion", is_group=True, sub_commands=[
            command("mcomp help", "astrbot_plugin_memory_companion"), command("mcomp hidden", enabled=False)]),
            command("disabled", enabled=False, sub_commands=[command("disabled child")]),
            command("inactive", plugin_activated=False), command("conflict", has_conflict=True)]
        self.assertEqual([item["effective_command"] for item in visible_commands(items, lambda _: True)],
                         ["mcomp", "mcomp help"])
        self.assertIn("/mcomp help", self.render(items, "记忆"))

    def test_pagination_reaches_every_command_once(self):
        items = [command(f"item{index:02d}") for index in range(23)]
        pages = [self.render(items, f"全部 {page}") for page in range(1, 4)]
        listed = [line.split()[0] for page in pages for line in page.splitlines() if line.startswith("/item")]
        self.assertEqual(listed, [f"/item{index:02d}" for index in range(23)])
        self.assertIn("下一页：/菜单 全部 2", pages[0])
        self.assertNotIn("下一页", pages[-1])
        self.assertIn("共有3页", self.render(items, "全部 4"))
        self.assertIn("第2/3页", self.render(items, "2"))

    def test_search_and_detail_use_current_names_and_prefix(self):
        items = [command("renamed", original_command="old", aliases=["别名"], description="具体用法：renamed 内容")]
        output = self.render(items, "别名", prefix="!", entry="指令")
        self.assertIn("!renamed", output)
        self.assertIn("具体用法", output)
        self.assertIn("返回分类：!指令", output)
        self.assertNotIn("/old", output)
        self.assertIn("!renamed", self.render(items, "用法", prefix="!"))
        self.assertIn("没有找到", self.render(items, "不存在的功能"))


class MenuPermissionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="command-menu-test-")
        self.addCleanup(directory.cleanup)
        self.plugin = activities.LonelyFriendActivities.__new__(activities.LonelyFriendActivities)
        self.plugin.data = Path(directory.name)
        self.config = {"plugin_set": ["*"], "wake_prefix": ["/"], "provider_settings": {
            "web_search": True, "proactive_capability": {"add_cron_tools": True}}}
        self.plugin.context = SimpleNamespace(get_config=lambda **_: self.config)
        own = "astrbot_plugin_lonelyfriend_activities"
        self.items = [command("菜单", own, handler_name="command_menu"),
                      command("admin_only", permission="admin"), command("new", permission="shared_group_admin"),
                      command("表情禁用", "astrbot_plugin_meme_generator", description="禁用（仅限Bot管理员）"),
                      command("解析帮助", "astrbot_plugin_parser")]

    async def menu(self, *, admin=False, group="", isolated=False, query="", cron=False):
        origin = f"cuteq_qq:GroupMessage:{group}" if group else "cuteq_qq:FriendMessage:12345"
        extras = {"_session_isolated": isolated, "cron_payload": {"task": True} if cron else None}
        event = SimpleNamespace(unified_msg_origin=origin, is_admin=lambda: admin,
                                get_sender_id=lambda: "12345", get_group_id=lambda: group,
                                get_extra=lambda key, default=None: extras.get(key, default),
                                plain_result=lambda text: MessageEventResult().message(text))
        with patch.object(activities.command_management, "list_commands", AsyncMock(return_value=self.items)):
            result, = [value async for value in self.plugin.command_menu(event, query)]
        self.assertFalse(result.use_t2i_)
        return result.get_plain_text()

    async def test_permissions_follow_current_sender_and_session(self):
        private = await self.menu(admin=True)
        self.assertIn("菜单导航", private)
        self.assertNotIn("热点", private)
        self.assertNotIn("RSS", private)
        self.assertIn("/admin_only", await self.menu(admin=True, query="admin_only"))
        member = await self.menu(group="54321", query="全部")
        self.assertNotIn("admin_only", member)
        self.assertNotIn("表情禁用", member)
        self.assertNotIn("/new", member)
        self.assertIn("/new", await self.menu(group="54321", isolated=True, query="全部"))

    async def test_disabled_plugin_is_not_advertised(self):
        self.config["plugin_set"] = ["astrbot_plugin_lonelyfriend_activities"]
        output = await self.menu(admin=True, query="全部")
        self.assertNotIn("/解析帮助", output)
        self.assertNotIn("/表情禁用", output)
        self.assertIn("/admin_only", output)

    def test_menu_accepts_empty_query_and_multiword_queries(self):
        handler = activities.LonelyFriendActivities.command_menu
        command_filter = CommandFilter("菜单", handler_md=SimpleNamespace(handler=handler))
        self.assertEqual(command_filter.validate_and_convert_params([], command_filter.handler_params)["query"], "")
        self.assertEqual(command_filter.validate_and_convert_params(["全部", "2"], command_filter.handler_params)["query"], "全部 2")


if __name__ == "__main__":
    unittest.main()
