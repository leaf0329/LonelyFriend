from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import configure_server
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.core.utils.shared_preferences import SharedPreferences


class ServerMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_migrated_routes_remain_readable_by_the_framework(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "data").mkdir()
            database = SQLiteDatabase(str(root / "data/data_v4.db"))
            await database.initialize()
            try:
                await database.insert_preference_or_update(
                    "global", "global", "unrelated_setting", {"val": "preserved"}
                )
                with patch.object(configure_server, "ROOT", root):
                    await configure_server.reset_legacy_platform_routes()
                preferences = SharedPreferences(database, json_storage_path=str(root / "unused.json"))
                try:
                    self.assertEqual(await preferences.global_get("abconf_mapping"), {})
                    self.assertEqual(await preferences.global_get("umop_config_routing"), {})
                    self.assertEqual(await preferences.global_get("unrelated_setting"), "preserved")
                finally:
                    await preferences.close()
            finally:
                await database.engine.dispose()


if __name__ == "__main__":
    unittest.main()
