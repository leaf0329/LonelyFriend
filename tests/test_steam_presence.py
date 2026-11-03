from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from plugins.astrbot_plugin_lonelyfriend_steam import main as steam
from plugins.astrbot_plugin_lonelyfriend_steam.tracking import PresenceStore
from astrbot.core.message.message_event_result import MessageEventResult

ID = "76561198000000000"
GROUP = "54321"


def state(**changes):
    return {"steam_id": ID, "name": "测试玩家", "persona_state": 1, "game_id": "123", "game_name": "测试游戏",
            "activity": "主菜单", "activity_known": True, "observed_at": 1234, **changes}


class SteamPresenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="steam-presence-test-")
        self.addCleanup(directory.cleanup)
        self.plugin = steam.SteamPresence.__new__(steam.SteamPresence)
        self.plugin.data = Path(directory.name)
        self.plugin.root = self.plugin.data.parent
        self.plugin.config_path = self.plugin.data / "steam-presence.json"
        self.policy = {"enabled": True, "steam_id": ID, "group_id": GROUP, "platform_id": "cuteq_qq",
                       "poll_seconds": 60, "node_binary": "runtime/node/bin/node"}
        self.plugin.config_path.write_text(json.dumps(self.policy), encoding="utf-8")
        self.access = self.plugin.data / "cuteq-access.json"
        self.access.write_text(json.dumps({"group_qq": [GROUP]}), encoding="utf-8")
        self.plugin.store = PresenceStore(self.plugin.data / "state.db")
        self.plugin.context = SimpleNamespace(send_message=AsyncMock(return_value=True))
        self.plugin.first_observation = True
        self.plugin.worker = self.plugin.process = None
        self.plugin.health = "尚未启动"

    async def test_first_read_and_identical_reads_do_not_notify(self):
        await self.plugin.accept_state(state())
        await self.plugin.accept_state(state(observed_at=1300))
        self.plugin.context.send_message.assert_not_awaited()
        self.assertEqual(self.plugin.store.get(ID)["observed_at"], 1300)

    async def test_activity_change_and_simultaneous_fields_each_emit_one_message(self):
        await self.plugin.accept_state(state())
        await self.plugin.accept_state(state(activity="物物交换"))
        self.plugin.context.send_message.assert_awaited_once()
        origin, body = self.plugin.context.send_message.await_args.args
        self.assertEqual(origin, f"cuteq_qq:GroupMessage:{GROUP}")
        self.assertIn("物物交换", body.get_plain_text())
        self.assertEqual(len(body.chain), 1)
        self.plugin.context.send_message.reset_mock()
        await self.plugin.accept_state(state(persona_state=3, game_id="456", game_name="另一款游戏", activity="匹配"))
        self.plugin.context.send_message.assert_awaited_once()
        self.assertIn("离开｜另一款游戏｜匹配", self.plugin.context.send_message.await_args.args[1].get_plain_text())

    async def test_persona_change_while_in_the_same_game_is_not_missed(self):
        await self.plugin.accept_state(state())
        await self.plugin.accept_state(state(persona_state=2))
        self.plugin.context.send_message.assert_awaited_once()
        self.assertIn("忙碌", self.plugin.context.send_message.await_args.args[1].get_plain_text())

    async def test_missing_activity_does_not_create_a_false_change_or_repeat_on_recovery(self):
        await self.plugin.accept_state(state())
        await self.plugin.accept_state(state(activity="", activity_known=False))
        self.assertEqual(self.plugin.store.get(ID)["activity"], "主菜单")
        await self.plugin.accept_state(state())
        self.plugin.context.send_message.assert_not_awaited()

    async def test_send_unknown_is_not_repeated_after_recovery(self):
        await self.plugin.accept_state(state())
        self.plugin.context.send_message.side_effect = OSError("connection lost")
        await self.plugin.accept_state(state(activity="战局"))
        with closing(sqlite3.connect(self.plugin.store.path)) as database:
            self.assertEqual(database.execute("SELECT delivery_status FROM states").fetchone()[0], "unknown")
        self.plugin.context.send_message.side_effect = None
        await self.plugin.accept_state(state(activity="战局"))
        self.plugin.context.send_message.assert_awaited_once()

    async def test_persistence_failure_prevents_group_send(self):
        await self.plugin.accept_state(state())
        with patch.object(self.plugin.store, "save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                await self.plugin.accept_state(state(activity="战局"))
        self.plugin.context.send_message.assert_not_awaited()

    async def test_removed_group_disabled_monitor_or_wrong_player_cannot_send(self):
        await self.plugin.accept_state(state())
        self.access.write_text(json.dumps({"group_qq": []}), encoding="utf-8")
        await self.plugin.accept_state(state(activity="战局"))
        self.access.write_text(json.dumps({"group_qq": [GROUP]}), encoding="utf-8")
        self.policy["enabled"] = False
        self.plugin.config_path.write_text(json.dumps(self.policy), encoding="utf-8")
        await self.plugin.accept_state(state(activity="交易"))
        with self.assertRaises(ValueError):
            await self.plugin.accept_state(state(steam_id="76561198000000001"))
        self.plugin.context.send_message.assert_not_awaited()

    async def test_missing_login_leaves_a_working_plugin_without_spawning_a_worker(self):
        await self.plugin.initialize()
        self.assertEqual(self.plugin.health, "等待Steam扫码登录")
        self.assertIsNone(self.plugin.worker)

    async def test_status_is_private_to_the_configured_group_or_admin_private_chat(self):
        await self.plugin.accept_state(state())
        for admin, group, expected in [(False, "other", False), (True, "other", False),
                                       (True, "", True), (False, GROUP, True)]:
            with self.subTest(admin=admin, group=group):
                origin = f"cuteq_qq:GroupMessage:{group}" if group else "cuteq_qq:FriendMessage:12345"
                event = SimpleNamespace(unified_msg_origin=origin, is_admin=lambda: admin, get_group_id=lambda: group,
                                        plain_result=lambda text: MessageEventResult().message(text))
                result, = [item async for item in self.plugin.status(event)]
                self.assertEqual("测试玩家" in result.get_plain_text(), expected)

    async def test_terminating_plugin_stops_the_owned_listener_task(self):
        stopped = asyncio.Event()
        async def running():
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        self.plugin.worker = asyncio.create_task(running())
        await asyncio.sleep(0)
        await self.plugin.terminate()
        self.assertTrue(stopped.is_set())
        self.assertIsNone(self.plugin.worker)

    async def test_database_failure_reports_a_clear_error_and_closes_the_owned_process(self):
        async def lines():
            yield json.dumps({"type": "state", **state()}).encode()
        process = SimpleNamespace(stdout=lines(), returncode=None, terminate=Mock(), wait=AsyncMock(return_value=-15))
        with patch.object(steam.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)), \
                patch.object(self.plugin.store, "save", side_effect=sqlite3.OperationalError("disk full")):
            await self.plugin.run_worker(self.policy)
        self.assertIn("OperationalError", self.plugin.health)
        process.terminate.assert_called_once()
        process.wait.assert_awaited_once()
        self.assertIsNone(self.plugin.process)
        self.plugin.context.send_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
