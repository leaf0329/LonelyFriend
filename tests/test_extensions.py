from __future__ import annotations

import asyncio
import importlib.util
import importlib
import json
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("public_reading", ROOT / "plugins/astrbot_plugin_lonelyfriend_activities/reading.py")
reading = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reading)
sys.path.insert(0, str(ROOT / "scripts"))
from apply_extensions import update_extension_access
from plugins.astrbot_plugin_lonelyfriend_activities import main as activities


class PublicReadingTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_source_does_not_hide_good_source_and_cache_avoids_refetch(self):
        fetcher = SimpleNamespace(fetch=AsyncMock(side_effect=[
            SimpleNamespace(error="unavailable", rss_d=None),
            SimpleNamespace(error=None, rss_d={"entries": [{"title": "新消息", "link": "https://example.org/news", "summary": "<b>真实摘要</b>"}]}),
        ]))
        feeds = [{"name": "A", "topics": "游戏", "url": "https://a.example/rss"},
                 {"name": "B", "topics": "AI", "url": "https://b.example/rss"}]
        with tempfile.TemporaryDirectory() as directory:
            reader = reading.PublicFeedReader(feeds, Path(directory) / "reading.json", fetcher)
            first = await reader.read()
            second = await reader.read()
        self.assertEqual(first, second)
        self.assertEqual(first["items"][0]["summary"], "真实摘要")
        self.assertEqual(first["failures"][0]["source"], "A")
        self.assertEqual(fetcher.fetch.await_count, 2)


class AccessUpdateTests(unittest.TestCase):
    def test_removing_all_groups_does_not_turn_learning_into_unrestricted_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data/config").mkdir(parents=True)
            (root / "data/extensions.json").write_text("{}")
            parser = root / "data/config/astrbot_plugin_parser_config.json"
            learning = root / "data/config/astrbot_plugin_self_learning_config.json"
            parser.write_text(json.dumps({"whitelist": ["old"], "source_max_size": 15}))
            learning.write_text(json.dumps({"Target_Settings": {"target_qq_list": ["group_old"]}}))
            update_extension_access(root, {"private_qq": ["12345"], "group_qq": []})
            self.assertEqual(json.loads(parser.read_text())["whitelist"], ["cuteq_qq:FriendMessage:12345"])
            self.assertEqual(json.loads(parser.read_text())["source_max_size"], 15)
            self.assertEqual(json.loads(learning.read_text())["Target_Settings"]["target_qq_list"], ["group___no_allowed_groups__"])


class DailyDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_attempts_send_only_once_and_cannot_redirect(self):
        with tempfile.TemporaryDirectory() as directory:
            plugin = activities.LonelyFriendActivities.__new__(activities.LonelyFriendActivities)
            plugin.data = Path(directory)
            plugin.delivery_lock = asyncio.Lock()
            plugin.context = SimpleNamespace(send_message=AsyncMock(return_value=True))
            (plugin.data / "cuteq-access.json").write_text(json.dumps({"private_qq": ["12345"], "group_qq": []}))
            folder = plugin.data / "plugin_data/astrbot_plugin_lonelyfriend_activities"
            folder.mkdir(parents=True)
            (folder / "reading.json").write_text(json.dumps({"checked_at": time.time(), "items": [{"url": "https://example.org/news"}]}))
            event = SimpleNamespace(unified_msg_origin="cuteq_qq:FriendMessage:12345",
                get_extra=lambda *args: {"lonelyfriend_activity": activities.MANAGED_JOB}, plain_result=lambda text: text)
            with patch.object(activities, "datetime") as clock:
                clock.now.return_value = datetime(2026, 10, 29, 12, tzinfo=ZoneInfo("Asia/Shanghai"))
                await asyncio.gather(*[plugin.share_news(event, "见闻 https://example.org/news") for _ in range(3)])
                event.unified_msg_origin = "cuteq_qq:FriendMessage:99999"
                await plugin.share_news(event, "见闻 https://example.org/news")
            plugin.context.send_message.assert_awaited_once_with("cuteq_qq:FriendMessage:12345", "见闻 https://example.org/news")

    async def test_quiet_hours_do_not_send(self):
        plugin = activities.LonelyFriendActivities.__new__(activities.LonelyFriendActivities)
        plugin.origins = lambda: ["allowed"]
        plugin.context = SimpleNamespace(send_message=AsyncMock())
        event = SimpleNamespace(unified_msg_origin="allowed", get_extra=lambda *args: {"lonelyfriend_activity": activities.MANAGED_JOB})
        with patch.object(activities, "datetime") as clock:
            clock.now.return_value = datetime(2026, 10, 29, 23, tzinfo=ZoneInfo("Asia/Shanghai"))
            await plugin.share_news(event, "见闻")
        plugin.context.send_message.assert_not_awaited()


@unittest.skipUnless((ROOT / "data/plugins/astrbot_plugin_self_learning/webui/manager.py").is_file(), "Optional learning plugin is not installed here")
class EmbeddedLearningPageTests(unittest.IsolatedAsyncioTestCase):
    async def test_embedded_services_initialize_when_standalone_server_is_disabled(self):
        specification = importlib.util.spec_from_file_location("learning_webui_manager_test", ROOT / "data/plugins/astrbot_plugin_self_learning/webui/manager.py")
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        manager = module.WebUIManager.__new__(module.WebUIManager)
        manager._config = SimpleNamespace(enable_web_interface=False)
        manager._database_manager = object()
        manager._acquire_persona_manager = AsyncMock(return_value=object())
        manager._setup_services = AsyncMock()
        await manager.setup_and_start()
        manager._setup_services.assert_awaited_once()

    async def test_failed_backup_prevents_incremental_persona_change(self):
        sys.path.insert(0, str(ROOT / "data/plugins"))
        module = importlib.import_module("astrbot_plugin_self_learning.services.persona.persona_updater")
        updater = module.PersonaUpdater.__new__(module.PersonaUpdater)
        updater.config = SimpleNamespace(auto_backup_enabled=True)
        updater.backup_manager = SimpleNamespace(create_backup_before_update=AsyncMock(side_effect=RuntimeError("disk full")))
        updater.persona_manager_updater = SimpleNamespace(is_available=lambda: True, apply_incremental_update=AsyncMock())
        updater._logger = SimpleNamespace(error=lambda *args: None)
        self.assertFalse(await updater._apply_persona_manager_update("12345", "测试细节"))
        updater.persona_manager_updater.apply_incremental_update.assert_not_awaited()

    async def test_unreadable_persona_cannot_be_saved_as_empty_successful_backup(self):
        sys.path.insert(0, str(ROOT / "data/plugins"))
        module = importlib.import_module("astrbot_plugin_self_learning.services.persona.persona_backup_manager")
        manager = module.PersonaBackupManager.__new__(module.PersonaBackupManager)
        manager.context = SimpleNamespace(persona_manager=None)
        with self.assertRaises(module.BackupError):
            await manager._get_current_persona_data("12345")


if __name__ == "__main__":
    unittest.main()
