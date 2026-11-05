from __future__ import annotations

import importlib.util
import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from apply_extensions import extend_persona_tools, update_extension_access
from astrbot.core.db.sqlite import SQLiteDatabase


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


class PersonaToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_extension_update_removes_news_tools_without_losing_other_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data").mkdir()
            database = SQLiteDatabase(str(root / "data/data_v4.db"))
            await database.initialize()
            try:
                await database.insert_persona("restricted", "fixture", tools=[
                    "memory_companion_query", "custom_tool", "rss_add_subscription",
                    "rss_list_subscriptions", "lonelyfriend_read_feeds",
                    "lonelyfriend_read_hot_topics", "lonelyfriend_share_news"])
                await database.insert_persona("unrestricted", "fixture", tools=None)
                await database.insert_persona("wildcard", "fixture", tools=["*"])
                for persona_id in ("restricted", "unrestricted", "wildcard"):
                    await extend_persona_tools(root, persona_id)
                tools = (await database.get_persona_by_id("restricted")).tools
                self.assertIn("memory_companion_query", tools)
                self.assertIn("custom_tool", tools)
                self.assertIn("future_task", tools)
                self.assertIn("lonelyfriend_make_meme", tools)
                self.assertFalse(any(tool.startswith("rss_") or tool.startswith("lonelyfriend_read_")
                                     or tool == "lonelyfriend_share_news" for tool in tools))
                self.assertIsNone((await database.get_persona_by_id("unrestricted")).tools)
                self.assertEqual((await database.get_persona_by_id("wildcard")).tools, ["*"])
            finally:
                await database.engine.dispose()


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
