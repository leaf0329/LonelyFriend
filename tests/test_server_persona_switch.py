from __future__ import annotations

import importlib
import os
import sqlite3
import sys
import unittest
from contextlib import ExitStack, closing
from types import SimpleNamespace
from unittest.mock import Mock, patch


@unittest.skipIf(sys.platform == "win32", "The controller uses Ubuntu systemd")
class ServerPersonaSwitchTests(unittest.TestCase):
    def setUp(self):
        fixtures = importlib.import_module("test_persona_switch")
        self.fixture = fixtures.PersonaSwitchTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.fixture.write("data/cuteq-private.json", {"dashboard": {"username": "test", "password": "test"}})
        self.module = importlib.import_module("switch_server_persona")
        config = self.module.read(self.root / "data/cmd_config.json")
        config["dashboard"] = {"port": 6185}
        self.fixture.write("data/cmd_config.json", config)
        database_path = self.root / "data/plugin_data/astrbot_plugin_qzone/posts_5.db"
        for suffix in ("", "-wal", "-shm"):
            (database_path.parent / (database_path.name + suffix)).unlink()
        with closing(sqlite3.connect(database_path)) as database:
            database.execute("CREATE TABLE posts(id INTEGER PRIMARY KEY, text TEXT)")
            database.execute("INSERT INTO posts(text) VALUES ('old public post')")
            database.commit()

    def mocks(self):
        stack = ExitStack()
        stack.enter_context(patch.object(self.module.subprocess, "run"))
        stack.enter_context(patch.object(self.module.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())))
        stack.enter_context(patch.object(self.module.os, "chown"))
        return stack

    def assert_original_data(self):
        with closing(sqlite3.connect(self.root / "data/data_v4.db")) as database:
            self.assertEqual(database.execute("SELECT count(*) FROM conversations").fetchone()[0], 1)
        self.assertTrue((self.root / "data/plugin_data/astrbot_plugin_memory_companion/old-content.json").exists())
        self.assertEqual(self.module.read(self.root / "data/cmd_config.json")["agent_runner"]["config"]["persona"]["persona_id"], "previous")

    def test_incomplete_backup_never_deletes_original_paths(self):
        with self.mocks(), patch.object(self.module.shutil, "copytree", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.module.switch_server(self.root, "replacement")
        self.assert_original_data()

    def test_failed_startup_restores_prior_chat_config_and_memory(self):
        clock = SimpleNamespace(monotonic=Mock(side_effect=[0, 111]), sleep=Mock())
        with self.mocks(), patch.object(self.module, "time", clock), patch.object(self.module.httpx, "Client"):
            with self.assertRaisesRegex(RuntimeError, "did not become healthy"):
                self.module.switch_server(self.root, "replacement")
        self.assert_original_data()

    def test_successful_reset_accepts_the_eight_remaining_plugins(self):
        plugins = ["astrbot_plugin_memory_companion", "astrbot_plugin_lonelyfriend_memory",
                   "astrbot_plugin_proactive_chat", "astrbot_plugin_parser", "astrbot_plugin_self_learning",
                   "astrbot_plugin_lonelyfriend_activities", "astrbot_plugin_meme_generator", "astrbot_plugin_qzone"]
        for name in plugins:
            (self.root / "data/plugins" / name).mkdir(parents=True)
        (self.root / "data/extensions.json").write_text("{}")
        prompt, _ = self.module.load_card(self.root, "replacement")

        def response(path, **kwargs):
            if path == "/api/v1/plugins":
                data = [{"name": name, "activated": True, "reserved": False} for name in plugins]
                data.append({"name": "builtin_commands", "activated": True, "reserved": True})
            elif path == "/api/v1/plugins/failed":
                data = {}
            elif path == "/api/v1/personas/by-id":
                data = {"system_prompt": prompt}
            else:
                raise AssertionError(path)
            return SimpleNamespace(json=lambda: {"status": "ok", "data": data})

        client = SimpleNamespace(headers={},
            post=Mock(return_value=SimpleNamespace(raise_for_status=lambda: None,
                      json=lambda: {"status": "ok", "data": {"token": "fixture"}})),
            get=Mock(side_effect=response))
        clock = SimpleNamespace(monotonic=Mock(side_effect=[0, 1, 111]), sleep=Mock())
        with self.mocks(), patch.object(self.module, "time", clock), patch.object(self.module.httpx, "Client") as factory:
            factory.return_value.__enter__.return_value = client
            result = self.module.switch_server(self.root, "replacement")
        self.assertTrue(result["chat_service_ready"])
        self.assertTrue(result["temporary_rollback_removed"])
        with closing(sqlite3.connect(self.root / "data/data_v4.db")) as database:
            self.assertEqual(database.execute("SELECT count(*) FROM conversations").fetchone()[0], 0)
            self.assertIn("replacement fixture", database.execute(
                "SELECT system_prompt FROM personas WHERE persona_id='replacement'").fetchone()[0])
        self.assertFalse((self.root / "data/plugin_data/astrbot_plugin_memory_companion").exists())


if __name__ == "__main__":
    unittest.main()
