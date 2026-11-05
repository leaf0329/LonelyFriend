from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Plain
from astrbot.api.star import Context, Star, register
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from astrbot.core.star.filter.command import GreedyStr
from astrbot.core.star import command_management
from astrbot.core.star.filter.permission import COMMAND_PERMISSION_TYPES, PermissionTypeFilter

from .command_menu import render_menu, visible_commands


@register("astrbot_plugin_lonelyfriend_activities", "leaf0329", "命令菜单与表情工具", "0.3.0")
class LonelyFriendActivities(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.data = Path(get_astrbot_data_path())
        self.meme_lock = asyncio.Lock()

    def origins(self) -> list[str]:
        access = json.loads((self.data / "cuteq-access.json").read_text(encoding="utf-8-sig"))
        return ([f"cuteq_qq:FriendMessage:{v}" for v in access["private_qq"]]
                + [f"cuteq_qq:GroupMessage:{v}" for v in access["group_qq"]])

    @filter.command("菜单", alias={"命令菜单", "功能菜单"})
    async def command_menu(self, event: AstrMessageEvent, query: GreedyStr):
        """按分类、关键词或页码查看实时命令菜单。"""
        items = await command_management.list_commands()
        config = self.context.get_config(umo=event.unified_msg_origin)

        def permitted(item):
            if not item["reserved"] and "*" not in config["plugin_set"] and item["plugin"] not in config["plugin_set"]:
                return False
            if not event.is_admin() and "仅限Bot管理员" in item["description"]:
                return False
            permission = item["permission"]
            return permission == "everyone" or PermissionTypeFilter(COMMAND_PERMISSION_TYPES[permission], raise_error=False).filter(event, config)

        entries = visible_commands(items, permitted)
        entry = next(item["effective_command"] for item in entries
                     if item["plugin"] == "astrbot_plugin_lonelyfriend_activities" and item["handler_name"] == "command_menu")
        prefix = config["wake_prefix"][0] if config["wake_prefix"] else ""
        hints = []
        if config["provider_settings"]["web_search"]:
            hints.append("联网：先搜索这个问题，并附来源")
        if config["provider_settings"]["proactive_capability"]["add_cron_tools"]:
            hints.append("提醒：明天晚上八点提醒我……")
        if any(item["plugin"] == "astrbot_plugin_meme_generator" for item in entries):
            hints.append("表情：帮我做一张举牌表情")
        yield event.plain_result(render_menu(entries, query, prefix=prefix, entry_command=entry, capability_hints=hints)).use_t2i(False)

    def plugin(self, name: str):
        metadata = next((s for s in self.context.get_all_stars()
                         if s.root_dir_name == name and s.activated and s.star_cls is not None), None)
        if metadata is None:
            raise RuntimeError(f"Required plugin is not available: {name}")
        return metadata.star_cls

    @filter.llm_tool(name="lonelyfriend_make_meme")
    async def make_meme(self, event: AstrMessageEvent, template: str, text: str = "") -> str:
        """按模板制作并发送一张表情包，偶尔用于当前话题或用户明确要求。

        Args:
            template(string): 模板中文关键词，例如举牌、摸头；模板不存在时不要重复重试。
            text(string): 模板上的短文字；不需要文字的模板留空，最多80字。
        """
        if event.unified_msg_origin not in self.origins() or event.is_stopped():
            return "当前会话未获准制作表情。"
        if event.get_extra("lonelyfriend_sticker_attempted", False):
            return "本轮已尝试发送表情，请继续文字回答。"
        if not template.strip() or len(template) > 24 or len(text) > 80:
            return "模板或文字长度不符合要求。"
        event.set_extra("lonelyfriend_sticker_attempted", True)
        async with self.meme_lock:
            plugin = self.plugin("astrbot_plugin_meme_generator")
            command = f"{plugin.meme_manager.config.trigger_prefix}{template} {text}".strip()
            proxy = copy.copy(event)
            proxy.message_obj = copy.deepcopy(event.message_obj)
            proxy.message_str = command
            proxy.message_obj.message_str = command
            proxy.message_obj.message = [Plain(command)] + [part for part in proxy.message_obj.message if isinstance(part, Image)]
            image = await plugin.meme_manager.generate_meme(proxy)
            if image is None:
                return "未生成图片，模板可能不匹配或正在冷却。"
            await event.send(event.chain_result([Image.fromBytes(image)]))
            return "已发送一张表情，请自然继续聊天，不汇报工具执行。"
