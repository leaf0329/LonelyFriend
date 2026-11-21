from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
import shlex

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Plain
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, register
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from astrbot.core.star.filter.command import GreedyStr
from astrbot.core.star import command_management
from astrbot.core.star.filter.permission import COMMAND_PERMISSION_TYPES, PermissionTypeFilter

from .command_menu import render_menu, visible_commands
from .group_policy import bot_addressed, group_voice_requested
from .siyin import load_siyin
from .singing import load_singing, SingingError
from .singing_protocol import interval, seconds


@register("astrbot_plugin_lonelyfriend_activities", "leaf0329", "命令菜单、点名群聊、本地语音与翻唱", "0.5.0")
class LonelyFriendActivities(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.data = Path(get_astrbot_data_path())
        self.meme_lock = asyncio.Lock()
        self.speech = load_siyin(self.data / "siyin.json")
        self.singing = load_singing(self.data, self.origins)

    async def terminate(self):
        if self.speech is not None:
            await self.speech.close()
        if self.singing is not None:
            await self.singing.close()

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=1000)
    async def gate_group_conversation(self, event: AstrMessageEvent):
        if event.get_platform_id() == "cuteq_qq":
            # Keep commands and link tools running, but gate the native agent separately.
            event.is_at_or_wake_command = bot_addressed(event)

    @filter.on_llm_request(priority=-90)
    async def prepare_group_voice_reply(self, event: AstrMessageEvent, request: ProviderRequest):
        if event.get_platform_id() == "cuteq_qq" and event.get_group_id() and group_voice_requested(event):
            request.system_prompt += "\n当前群消息点名要求语音回复，系统会负责合成。请直接给出适合朗读的自然台词，控制在100字以内，不添加转语音的说明。\n"

    @filter.on_decorating_result(priority=-100)
    async def occasional_speech(self, event: AstrMessageEvent):
        # Run after final-text restoration and before native paragraph segmentation.
        if event.get_extra("lonelyfriend_cover_requested", False) or (self.singing is not None and self.singing.tasks):
            return
        if self.speech is not None:
            await self.speech.decorate(event, self.origins())

    @filter.event_message_type(filter.EventMessageType.ALL, priority=800)
    async def remember_singing_file(self, event: AstrMessageEvent):
        if self.singing is not None and event.unified_msg_origin in self.origins():
            self.singing.remember_file(event)

    def singing_for(self, event):
        if event.unified_msg_origin not in self.origins() or event.get_extra("cron_payload"):
            raise SingingError("当前会话未获准请求翻唱。")
        if self.singing is None:
            raise SingingError("本地翻唱尚未配置。")
        return self.singing

    @filter.command("翻唱搜歌", priority=100)
    async def singing_search(self, event: AstrMessageEvent, query: GreedyStr):
        """翻唱搜歌 [网易|QQ|本地] 歌名；选定版本后使用翻唱命令。"""
        event.stop_event()
        try:
            client = self.singing_for(event)
            words = query.strip().split(maxsplit=1)
            aliases = {"网易": "netease", "网易云": "netease", "QQ": "tencent", "qq": "tencent", "本地": "local"}
            source = aliases.get(words[0], "netease") if words else "netease"
            keyword = words[1] if words and words[0] in aliases and len(words) == 2 else query
            songs = await client.search(event, keyword, source)
            if not songs:
                text = "没有找到匹配歌曲。可以换关键词，或直接发送音频文件。"
            else:
                text = "\n".join(f"{i}. {s['title']} · {s['artist']} · {int(s['duration'])//60}:{int(s['duration'])%60:02d} · {s['album']}"
                                 + ("〔可能需要平台播放权限〕" if s.get("restricted") else "")
                                 for i, s in enumerate(songs, 1))
                text += "\n\n选定版本后发送：/翻唱 1 0:30-0:50\n单次1—30秒；列表仅本人在当前会话10分钟内可用。"
            yield event.plain_result(text).use_t2i(False)
        except (SingingError, ValueError) as error:
            yield event.plain_result(str(error)).use_t2i(False)

    @filter.command("翻唱", priority=100)
    async def singing_cover(self, event: AstrMessageEvent, query: GreedyStr):
        """翻唱 序号/BV号/歌曲链接/文件/本地:编号 起点-终点 [升降半音] [文件]。"""
        event.stop_event()
        try:
            client = self.singing_for(event)
            args = shlex.split(query)
            if not 2 <= len(args) <= 4:
                yield event.plain_result("先 /翻唱搜歌 歌名，再 /翻唱 1 0:30-0:50\n也可用BV号、完整歌曲链接或“文件”；先发音频文件，再 /翻唱 文件 0:30-0:50\n可附加升降半音（-12至12），末尾加“文件”发送立体声MP3；单次最多30秒。\n/翻唱状态、/取消翻唱、/取回翻唱 可管理自己的任务。").use_t2i(False)
                return
            start, end = interval(args[1])
            mode = "file" if args[-1] == "文件" else "voice"
            options = args[2:-1] if mode == "file" else args[2:]
            if len(options) > 1:
                raise ValueError("升降调只填写一个整数半音。")
            pitch = int(options[0]) if options else 0
            job_id = await client.start(event, args[0], start, end, pitch, mode)
            yield event.plain_result(f"已接下翻唱任务 {job_id[:8]}，{start:g}—{end:g}秒，完成后发到这里。\n可用 /翻唱状态 查看进度；本机离线或忙碌时不会自动重试。").use_t2i(False)
        except (SingingError, ValueError) as error:
            yield event.plain_result(str(error)).use_t2i(False)

    @filter.command("翻唱状态", priority=100)
    async def singing_status(self, event: AstrMessageEvent, query: GreedyStr):
        """查询当前会话自己的最近翻唱，可附加任务编号前缀。"""
        event.stop_event()
        try:
            job, state = await self.singing_for(event).status(event, query.strip())
            delivery = {"not_sent": "尚未发送", "sending": "正在发送", "sent": "已发送", "unknown": "发送结果未确认"}[job["delivery"]]
            yield event.plain_result(f"任务 {job['id'][:8]}：{state['stage']}\n{delivery}"
                + ("\n" + state["error"] if state.get("error") else "")
                + ("\n需要再次发送成品时用 /取回翻唱；发送结果不明时请先检查QQ消息。" if state["status"] == "done" else "")).use_t2i(False)
        except SingingError as error:
            yield event.plain_result(str(error)).use_t2i(False)

    @filter.command("取消翻唱", priority=100)
    async def singing_cancel(self, event: AstrMessageEvent, query: GreedyStr):
        """取消当前会话自己的翻唱，停止本地处理与后续音频发送。"""
        event.stop_event()
        try:
            text = await self.singing_for(event).cancel(event, query.strip())
            yield event.plain_result(text).use_t2i(False)
        except SingingError as error:
            yield event.plain_result(str(error)).use_t2i(False)

    @filter.command("取回翻唱", priority=100)
    async def singing_retrieve(self, event: AstrMessageEvent, query: GreedyStr):
        """主动再次发送自己的已完成翻唱，可附加任务编号前缀。"""
        event.stop_event()
        try:
            client = self.singing_for(event)
            job, state = await client.status(event, query.strip())
            if state["status"] != "done":
                raise SingingError("任务尚未完成。")
            job["cancel_requested"] = False
            await client.deliver(event, job)
        except (SingingError, ValueError) as error:
            yield event.plain_result(str(error)).use_t2i(False)

    @filter.llm_tool(name="lonelyfriend_search_music")
    async def search_music(self, event: AstrMessageEvent, keyword: str, source: str = "netease") -> str:
        """用户请求点歌翻唱时搜索歌曲版本，不主动点歌。选定版本后再调用翻唱工具。

        Args:
            keyword(string): 歌名与歌手关键词。
            source(string): netease、tencent或local；local可查用户已提供的本地音源。
        """
        try:
            songs = await self.singing_for(event).search(event, keyword, source)
            return json.dumps([{"index": i, **song} for i, song in enumerate(songs, 1)], ensure_ascii=False)
        except (SingingError, ValueError) as error:
            return str(error)

    @filter.llm_tool(name="lonelyfriend_cover_music")
    async def cover_music(self, event: AstrMessageEvent, choice: str, start_time: str, end_time: str,
                          pitch: int = 0, delivery: str = "voice") -> str:
        """仅在用户明确要求翻唱并指定时间段时提交本地任务。单次1—30秒，不用于普通语音回复或主动任务。

        Args:
            choice(string): 当前搜歌结果序号，或BV号、完整网易云/QQ歌曲链接、“文件”、本地:编号；不接受本地文件路径。
            start_time(string): 用户指定的片段起点，例如3:53；没指定时先询问，不猜副歌位置。
            end_time(string): 用户指定的终点，例如4:15。
            pitch(number): 升降整数半音，-12至12，默认0。
            delivery(string): voice发送语音，file发送立体声MP3。
        """
        try:
            # AstrBot 4.29 exposes int tool parameters as JSON numbers.
            if type(pitch) not in (int, float) or not -12 <= pitch <= 12 or int(pitch) != pitch:
                raise ValueError("升降调须为-12至12的整数半音。")
            pitch = int(pitch)
            job_id = await self.singing_for(event).start(event, choice, seconds(start_time), seconds(end_time), pitch, delivery)
            return f"已提交翻唱任务{job_id[:8]}，音频完成后会单独发送到当前会话。请简短自然接话，不重复提交，不宣称已唱完。"
        except (SingingError, ValueError) as error:
            return str(error)

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
