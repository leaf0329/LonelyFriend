from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
from pathlib import Path

from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.db.sqlite import SQLiteDatabase

ROOT = Path(__file__).resolve().parents[1]
NEW_PLUGINS = ("astrbot_plugin_rsshub", "astrbot_plugin_parser", "astrbot_plugin_meme_generator", "astrbot_plugin_self_learning")
ACTIVITY_PLUGIN = "astrbot_plugin_lonelyfriend_activities"
EXTRA_TOOLS = {"web_search_deepseek", "deepseek_fetch_web_page", "future_task", "parse_media_url",
               "lonelyfriend_read_feeds", "lonelyfriend_make_meme", "lonelyfriend_share_news", "rss_list_subscriptions", "rss_list_push_history"}


def apply_addon_patches(root: Path):
    manifest = read(root / "sources.lock.json")
    for item in manifest["plugins"]:
        if item["name"] not in NEW_PLUGINS or "patch" not in item:
            continue
        command = ["git", "-C", str(root / "data/plugins" / item["name"]), "apply"]
        patch = str(root / item["patch"])
        if subprocess.run(command + ["--reverse", "--check", patch], capture_output=True).returncode == 0:
            continue
        subprocess.run(command + ["--check", patch], check=True, capture_output=True)
        subprocess.run(command + [patch], check=True, capture_output=True)


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def configure_addons(root: Path, policy: dict, access: dict):
    persona_id = read(root / "data/cmd_config.json")["agent_runner"]["config"]["persona"]["persona_id"]
    origins = ([f"cuteq_qq:FriendMessage:{v}" for v in access["private_qq"]]
               + [f"cuteq_qq:GroupMessage:{v}" for v in access["group_qq"]])
    for name in NEW_PLUGINS:
        config = AstrBotConfig(config_path=str(root / f"data/config/{name}_config.json"),
                              schema=read(root / f"data/plugins/{name}/_conf_schema.json"))
        if name == "astrbot_plugin_parser":
            config.update(whitelist=origins, source_max_size=15, source_max_minute=3,
                          download_timeout=60, download_retry_times=0, debounce_interval=600,
                          single_heavy_render_card=True)
            # Use the upstream schema's defaults for each parser template.
            if not config.get("parsers_template"):
                schema = read(root / f"data/plugins/{name}/_conf_schema.json")["parsers_template"]
                templates = []
                for parser_type, template in schema["templates"].items():
                    if parser_type in {"bilibili", "douyin", "kuaishou", "weibo", "xhs", "xiaoheihe", "zhihu", "nga", "ncm"}:
                        templates.append({"__template_key": parser_type,
                                          **{k: v.get("default") for k, v in template["items"].items()}})
                config["parsers_template"] = templates
        elif name == "astrbot_plugin_meme_generator":
            config.update(trigger_prefix="#", cooldown_seconds=15, generation_timeout=20)
        elif name == "astrbot_plugin_rsshub":
            config["basic_config"].update(minimal_interval=60, failed_queue_max_retries=1, history_entry_limit=0)
            config["media"]["table_to_image"] = False
        else:
            config["Self_Learning_Basic"].update(enable_web_interface=False, enable_realtime_learning=False,
                enable_message_capture=True, enable_auto_learning=True, enable_jargon_learning=True,
                enable_style_learning=True, jargon_websearch_enabled=False)
            config["Target_Settings"].update(target_qq_list=[f"group_{v}" for v in access["group_qq"]] or ["group___no_allowed_groups__"],
                                             current_persona_name=persona_id)
            config["Model_Configuration"].update(filter_provider_id="cuteq-deepseek-flash",
                refine_provider_id="cuteq-deepseek-flash", reinforce_provider_id="cuteq-deepseek-flash")
            config["Database_Settings"]["db_type"] = "sqlite"
            config["Learning_Parameters"]["learning_interval_hours"] = policy["self_learning"]["interval_hours"]
            config["Machine_Learning_Settings"]["enable_ml_analysis"] = False
            config["MaiBot_Enhancement"].update(enable_memory_graph=False, enable_knowledge_graph=False,
                                              enable_realtime_expression_learning=False)
            config["Affection_System_Settings"]["enable_affection_system"] = False
            config["Mood_System_Settings"].update(enable_daily_mood=False, enable_startup_random_mood=False)
            config["Social_Context_Settings"]["enable_social_context_injection"] = False
            config["Runtime_Internal_Settings"].update(enable_llm_hooks=True, enable_memory_cleanup=False)
            config["Advanced_Settings"]["auto_apply_approved_persona"] = policy["self_learning"]["automatic_persona_updates"]
            config["Persona_Evolution_Settings"].update(enable_persona_evolution=True,
                auto_apply_persona_updates=policy["self_learning"]["automatic_persona_updates"],
                persona_update_backup_enabled=True)
            config["Persona_Backup_Settings"].update(auto_backup_enabled=True, max_backups_per_group=10)
        config.save_config()


def update_extension_access(root: Path, access: dict):
    if not (root / "data/extensions.json").is_file():
        return
    parser_path = root / "data/config/astrbot_plugin_parser_config.json"
    parser = read(parser_path)
    parser["whitelist"] = ([f"cuteq_qq:FriendMessage:{v}" for v in access["private_qq"]]
                           + [f"cuteq_qq:GroupMessage:{v}" for v in access["group_qq"]]) or ["cuteq:unconfigured:deny"]
    write(parser_path, parser)
    learning_path = root / "data/config/astrbot_plugin_self_learning_config.json"
    learning = read(learning_path)
    learning["Target_Settings"]["target_qq_list"] = [f"group_{v}" for v in access["group_qq"]] or ["group___no_allowed_groups__"]
    write(learning_path, learning)


async def extend_persona_tools(root: Path, persona_id: str):
    database = SQLiteDatabase(str(root / "data/data_v4.db"))
    try:
        await database.initialize()
        persona = await database.get_persona_by_id(persona_id)
        if persona is None:
            raise RuntimeError(f"Active persona does not exist: {persona_id}")
        tools = persona.tools
        if tools is not None and "*" not in tools:
            await database.update_persona(persona_id, tools=sorted(set(tools) | EXTRA_TOOLS))
    finally:
        await database.engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="Apply the six approved QQ extensions without reinitializing chat or memory.")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    root = args.root.resolve()
    apply_addon_patches(root)
    policy = read(root / "config/extensions.json")
    access = read(root / "data/cuteq-access.json")
    private = read(root / "data/cuteq-private.json")
    config_path = root / "data/cmd_config.json"
    config = read(config_path)
    config["provider_settings"].update(web_search=True, websearch_provider="deepseek",
        websearch_deepseek_key=[private["model"]["api_key"]], web_search_link=True)
    config["provider_settings"]["proactive_capability"]["add_cron_tools"] = True
    write(config_path, config)
    write(root / "data/extensions.json", policy)
    shutil.copytree(root / "plugins" / ACTIVITY_PLUGIN, root / "data/plugins" / ACTIVITY_PLUGIN,
                    dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    configure_addons(root, policy, access)
    persona_id = config["agent_runner"]["config"]["persona"]["persona_id"]
    asyncio.run(extend_persona_tools(root, persona_id))
    print(json.dumps({"web_search": "deepseek", "native_proactive": True, "addons": NEW_PLUGINS,
                      "daily_reading_targets": len(access["private_qq"]) + len(access["group_qq"]),
                      "learning_scope": "allowlisted_groups_only", "automatic_persona_updates": True}))


if __name__ == "__main__":
    main()
