from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star, register
from astrbot.core.message.message_event_result import MessageEventResult
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .tracking import PresenceStore, changed, describe, merge_activity, notification


@register("astrbot_plugin_lonelyfriend_steam", "leaf0329", "Steam 在线、游戏与详细活动监测", "0.1.0")
class SteamPresence(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.data = Path(get_astrbot_data_path())
        self.root = self.data.parent
        self.config_path = self.data / "steam-presence.json"
        self.store = PresenceStore(self.data / "plugin_data/astrbot_plugin_lonelyfriend_steam/state.db")
        self.worker = None
        self.process = None
        self.health = "尚未启动"
        self.first_observation = True

    def policy(self):
        if not self.config_path.is_file():
            return None
        value = json.loads(self.config_path.read_text(encoding="utf-8-sig"))
        if not isinstance(value["steam_id"], str) or not value["steam_id"].isascii() or not value["steam_id"].isdigit() or len(value["steam_id"]) != 17:
            raise ValueError("SteamID必须是17位数字字符串")
        if not isinstance(value["group_id"], str) or not value["group_id"].isascii() or not value["group_id"].isdigit():
            raise ValueError("目标群号必须是数字字符串")
        if not isinstance(value["poll_seconds"], int) or value["poll_seconds"] < 30:
            raise ValueError("Steam检查间隔不得小于30秒")
        return value

    def allowed_origin(self, policy):
        access = json.loads((self.data / "cuteq-access.json").read_text(encoding="utf-8-sig"))
        if policy["group_id"] not in access["group_qq"]:
            return None
        return f"{policy['platform_id']}:GroupMessage:{policy['group_id']}"

    async def initialize(self):
        policy = self.policy()
        if policy is None:
            self.health = "尚未配置监测目标"
        elif not policy["enabled"]:
            self.health = "已停止"
        elif not self.allowed_origin(policy):
            self.health = "目标群未获准"
        elif not (self.data / "steam-presence/login.json").is_file():
            self.health = "等待Steam扫码登录"
        else:
            self.first_observation = True
            self.health = "正在连接Steam"
            self.worker = asyncio.create_task(self.run_worker(policy))

    async def accept_state(self, current: dict):
        policy = self.policy()
        if policy is None:
            raise ValueError("Steam监测配置已移除")
        if current["steam_id"] != policy["steam_id"]:
            raise ValueError("Steam监听返回了未配置的目标")
        previous = self.store.get(current["steam_id"])
        current = merge_activity(previous, current)
        origin = self.allowed_origin(policy) if policy["enabled"] else None
        should_send = bool(origin and previous and not self.first_observation and changed(previous, current))
        # Persist the observation before sending, so ambiguous sends cannot be retried as new changes.
        self.store.save(current, "attempted" if should_send else "observed")
        self.first_observation = False
        self.health = "监测正常" if current["activity_known"] else "基本状态可读，游戏内活动暂不可读"
        if not should_send:
            return
        try:
            result = await self.context.send_message(origin, MessageEventResult(chain=[Plain(notification(previous, current))]))
        except Exception as error:
            self.store.delivered(current["steam_id"], "unknown", type(error).__name__)
            self.health = "群通知结果不明，未自动重发"
            logger.warning("Steam状态通知结果不明：%s", type(error).__name__)
        else:
            self.store.delivered(current["steam_id"], "sent" if result else "unconfirmed")
            if not result:
                self.health = "群通知未获确认，未自动重发"

    async def run_worker(self, policy):
        try:
            self.health = "正在连接Steam"
            self.process = await asyncio.create_subprocess_exec(
                str(self.root / policy["node_binary"]), "--max-old-space-size=192",
                str(self.root / "services/steam-presence/presence.cjs"),
                "watch", str(self.config_path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                limit=65536)
            async for line in self.process.stdout:
                packet = json.loads(line)
                if packet["type"] == "state":
                    await self.accept_state(packet)
                else:
                    labels = {"connected": "已连接Steam，等待玩家数据", "disconnected": "Steam连接断开，等待恢复",
                              "poll_error": "本次Steam读取失败，保留上次状态", "activity_unavailable": "本次游戏活动读取失败",
                              "connection_error": "Steam连接失败，可能需要重新登录", "login_required": "需要重新扫码登录",
                              "startup_error": "Steam监听启动失败"}
                    self.health = labels[packet["status"]]
            code = await self.process.wait()
            self.health += f"；监听进程已退出（{code}）"
        except (OSError, ValueError, KeyError, sqlite3.Error) as error:
            self.health = f"Steam监听异常：{type(error).__name__}"
            logger.error(self.health)
        finally:
            if self.process is not None and self.process.returncode is None:
                self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=5)
                except TimeoutError:
                    self.process.kill()
                    await self.process.wait()
            self.process = None

    async def terminate(self):
        if self.worker is not None:
            self.worker.cancel()
            try:
                await self.worker
            except asyncio.CancelledError:
                pass
            self.worker = None

    @filter.command("Steam状态", alias={"steam状态"})
    async def status(self, event: AstrMessageEvent):
        """查看已配置玩家的在线、游戏与详细活动状态，不新增监测目标。"""
        policy = self.policy()
        if policy is None:
            text = self.health
        elif event.unified_msg_origin != self.allowed_origin(policy) and not (event.is_admin() and not event.get_group_id()):
            text = "Steam状态只在指定群或管理员私聊中提供。"
        else:
            state = self.store.get(policy["steam_id"])
            text = f"Steam监测：{self.health}"
            if state:
                when = datetime.fromtimestamp(state["observed_at"], ZoneInfo("Asia/Shanghai")).strftime("%m-%d %H:%M:%S")
                text += f"\n{state['name']}：{describe(state)}\n最近成功读取：{when}"
        yield event.plain_result(text).use_t2i(False)

    @filter.command("Steam重连", alias={"steam重连"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def reconnect(self, event: AstrMessageEvent):
        """重新连接Steam监听；首次读取只建立当前状态，不补发停机期间的变化。"""
        await self.terminate()
        await self.initialize()
        yield event.plain_result(self.health).use_t2i(False)
