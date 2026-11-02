from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import switch_persona
from astrbot.core.db.sqlite import SQLiteDatabase


class PersonaSwitchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="persona-switch-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.data = self.root / "data"
        (self.data / "config").mkdir(parents=True)
        self.write("config/personas.json", {key: {"name": key, "prompt": f"personas/{key}.md", "source": f"personas/{key}-source.json"} for key in ("previous", "replacement")})
        for key in ("previous", "replacement"):
            self.write(f"config/personas/{key}-source.json", {"persona_id": key})
            (self.root / f"config/personas/{key}.md").write_text(f"# {key} fixture\nSynthetic test instructions.\n", encoding="utf-8")
        self.write("data/cmd_config.json", {"agent_runner": {"config": {"persona": {"persona_id": "previous"}}}, "provider": [{"key": "keep-model-secret"}]})
        self.write("data/config/astrbot_plugin_proactive_chat_config.json", {
            "friend_settings": {"proactive_prompt": "old", "schedule_settings": {"quiet_hours": "23-8"}, "session_list": ["friend"]},
            "group_settings": {"proactive_prompt": "old", "session_list": ["group"]},
        })
        self.write("data/config/astrbot_plugin_qzone_config.json", {"llm": {"post_prompt": "old", "comment_prompt": "old-shi", "reply_prompt": "old-shi"}, "trigger": {"publish_cron": "0 12,21 * * *"}})
        self.write("data/config/astrbot_plugin_self_learning_config.json", {"Target_Settings": {"current_persona_name": "previous", "target_qq_list": ["group_test"]}})
        self.write("data/plugin_data/astrbot_plugin_lonelyfriend_activities/delivery.json", {"friend": {"date": "2026-10-30", "status": "sent"}})
        for relative in ["data/cuteq-private.json", "data/cuteq-access.json", "data/napcat/config/webui.json", "data/stickers/catalog.json", "data/plugin_data/astrbot_plugin_meme_generator/resources/image.png", "data/plugin_data/astrbot_plugin_parser/cookies/login.json"]:
            self.write(relative, {"preserve": relative})
        for relative in switch_persona.RESET_DIRECTORIES:
            self.write(relative + "/old-content.json", {"private": "old-test-content"})
        for relative in switch_persona.RESET_FILES:
            self.write(relative, {"private": "old-test-content"})
        asyncio.run(self.seed())

    def write(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    async def seed(self):
        database = SQLiteDatabase(str(self.data / "data_v4.db"))
        try:
            await database.initialize()
            await database.insert_persona("previous", "old test persona", tools=["memory_companion_query", "future_task"], skills=["allowed-skill"])
            await database.create_conversation("test-user", "aiocqhttp", content=[{"role": "user", "content": "old-test-content"}])
            await database.insert_platform_message_history("cuteq_qq", "test-user", {"text": "old-test-content"})
            await database.insert_preference_or_update("global", "global", "sel_conv_id", {"val": {"friend": "old-conversation"}})
            await database.insert_preference_or_update("global", "global", "inactivated_plugins", {"val": ["keep-disabled-plugin"]})
            await database.create_cron_job("old reminder", "active_agent", "0 12 * * *", payload={"session": "friend"})
            await database.create_cron_job("maintenance", "maintenance", "0 3 * * *")
        finally:
            await database.engine.dispose()

    def test_switch_resets_content_but_preserves_accounts_permissions_assets_and_limits(self):
        keep = [self.data / "cuteq-private.json", self.data / "cuteq-access.json", self.data / "napcat/config/webui.json",
                self.data / "stickers/catalog.json", self.data / "plugin_data/astrbot_plugin_meme_generator/resources/image.png",
                self.data / "plugin_data/astrbot_plugin_parser/cookies/login.json",
                self.data / "plugin_data/astrbot_plugin_lonelyfriend_activities/delivery.json"]
        before = {path: path.read_bytes() for path in keep}
        result = switch_persona.switch_and_reset(self.root, "replacement")
        self.assertEqual(result["persona_id"], "replacement")
        self.assertEqual(result["previous_chat_rows"]["conversations"], 1)
        with closing(sqlite3.connect(self.data / "data_v4.db")) as database:
            for table in ("conversations", "platform_message_history"):
                self.assertEqual(database.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)
            self.assertEqual(database.execute("SELECT job_type FROM cron_jobs").fetchall(), [("maintenance",)])
            prompt, tools, skills = database.execute("SELECT system_prompt,tools,skills FROM personas WHERE persona_id='replacement'").fetchone()
            self.assertIn("replacement fixture", prompt)
            self.assertEqual(json.loads(tools), ["memory_companion_query", "future_task"])
            self.assertEqual(json.loads(skills), ["allowed-skill"])
            self.assertEqual(json.loads(database.execute("SELECT value FROM preferences WHERE key='sel_conv_id'").fetchone()[0]), {"val": {}})
            self.assertEqual(json.loads(database.execute("SELECT value FROM preferences WHERE key='inactivated_plugins'").fetchone()[0]), {"val": ["keep-disabled-plugin"]})
        self.assertTrue(all(not (self.root / path).exists() for path in (*switch_persona.RESET_DIRECTORIES, *switch_persona.RESET_FILES)))
        self.assertTrue(all(path.read_bytes() == content for path, content in before.items()))
        qzone = switch_persona.read(self.data / "config/astrbot_plugin_qzone_config.json")
        self.assertTrue(all(value.startswith("# replacement fixture") for value in qzone["llm"].values()))
        self.assertEqual(qzone["trigger"]["publish_cron"], "0 12,21 * * *")
        self.assertEqual(switch_persona.read(self.data / "config/astrbot_plugin_self_learning_config.json")["Target_Settings"]["current_persona_name"], "replacement")

    def test_second_switch_initializes_newly_accumulated_content_again(self):
        switch_persona.switch_and_reset(self.root, "replacement")
        self.write("data/plugin_data/astrbot_plugin_self_learning/new-state.json", {"old": "new-test-content"})
        switch_persona.switch_and_reset(self.root, "previous")
        self.assertFalse((self.data / "plugin_data/astrbot_plugin_self_learning").exists())
        self.assertEqual(switch_persona.read(self.data / "cmd_config.json")["agent_runner"]["config"]["persona"]["persona_id"], "previous")
        self.assertEqual(switch_persona.read(self.data / "persona-source.json")["persona_id"], "previous")

    def test_running_service_blocks_all_mutation(self):
        before = (self.data / "cmd_config.json").read_bytes()
        with patch.object(switch_persona, "assert_service_stopped", side_effect=RuntimeError("service running")):
            with self.assertRaisesRegex(RuntimeError, "service running"):
                switch_persona.switch_and_reset(self.root, "replacement")
        self.assertEqual((self.data / "cmd_config.json").read_bytes(), before)
        self.assertTrue((self.data / "plugin_data/astrbot_plugin_self_learning/old-content.json").exists())

    def test_catalog_path_escape_is_rejected_before_reset(self):
        self.write("config/personas.json", {"replacement": {"prompt": "../../outside.md", "source": "personas/replacement-source.json"}})
        with self.assertRaises(ValueError):
            switch_persona.switch_and_reset(self.root, "replacement")
        self.assertTrue((self.data / "plugin_data/astrbot_plugin_memory_companion/old-content.json").exists())

    def test_configuration_updates_selected_persona_without_rewriting_another_card(self):
        import configure
        switch_persona.switch_and_reset(self.root, "replacement")
        with patch.object(configure, "ROOT", self.root):
            self.assertEqual(configure.selected_persona_id(), "replacement")
            asyncio.run(configure.set_persona("updated selected card", ["allowed-tool"]))
        with closing(sqlite3.connect(self.data / "data_v4.db")) as database:
            self.assertEqual(database.execute("SELECT system_prompt FROM personas WHERE persona_id='replacement'").fetchone()[0], "updated selected card")
            self.assertEqual(database.execute("SELECT system_prompt FROM personas WHERE persona_id='previous'").fetchone()[0], "old test persona")

    def test_private_card_keeps_its_body_and_source_outside_public_config(self):
        prompt = "# Private card\n\nDo not rewrite these instructions.\n"
        (self.data / "persona.md").write_text(prompt, encoding="utf-8")
        self.write("data/persona-source.json", {"persona_id": "default", "original_metadata": {"title": "private"}})
        self.write("config/personas.json", {"default": {"name": "Private card", "prompt": "../data/persona.md", "source": "../data/persona-source.json"}})
        switch_persona.switch_and_reset(self.root, "default")
        with closing(sqlite3.connect(self.data / "data_v4.db")) as database:
            self.assertEqual(database.execute("SELECT system_prompt FROM personas WHERE persona_id='default'").fetchone()[0], prompt)
        self.assertEqual((self.data / "persona.md").read_text(encoding="utf-8"), prompt)
        self.assertEqual(switch_persona.read(self.data / "persona-source.json")["original_metadata"]["title"], "private")


if __name__ == "__main__":
    unittest.main()
