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
        self.assertEqual(self.module.read(self.root / "data/cmd_config.json")["agent_runner"]["config"]["persona"]["persona_id"], "dafeiyu")

    def test_incomplete_backup_never_deletes_original_paths(self):
        with self.mocks(), patch.object(self.module.shutil, "copytree", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.module.switch_server(self.root, "roast")
        self.assert_original_data()

    def test_failed_startup_restores_prior_chat_config_and_memory(self):
        clock = SimpleNamespace(monotonic=Mock(side_effect=[0, 111]), sleep=Mock())
        with self.mocks(), patch.object(self.module, "time", clock), patch.object(self.module.httpx, "Client"):
            with self.assertRaisesRegex(RuntimeError, "did not become healthy"):
                self.module.switch_server(self.root, "roast")
        self.assert_original_data()


if __name__ == "__main__":
    unittest.main()
