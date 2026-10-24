from __future__ import annotations

import asyncio
import ast
import json
import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["ASTRBOT_ROOT"] = str(ROOT)
os.chdir(ROOT)

from astrbot.cli.commands.cmd_conf import _set_dashboard_password
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.db.sqlite import SQLiteDatabase

PROVIDER_ID = "cuteq-deepseek-flash"
QQ_PLATFORM_ID = "cuteq_qq"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def memory_tools() -> list[str]:
    source = ROOT / "data/plugins/astrbot_plugin_memory_companion/main.py"
    names = []
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8-sig"))):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            if not isinstance(decorator.func, ast.Attribute) or decorator.func.attr != "llm_tool":
                continue
            name = next((kw.value.value for kw in decorator.keywords if kw.arg == "name" and isinstance(kw.value, ast.Constant)), node.name)
            names.append(name)
    return names


def plugin_config(name: str) -> AstrBotConfig:
    schema = read_json(ROOT / "data/plugins" / name / "_conf_schema.json")
    return AstrBotConfig(config_path=str(ROOT / "data/config" / f"{name}_config.json"), schema=schema)


def apply_access(config: dict, access: dict) -> None:
    lists = {key: access.get(key, []) for key in ("private_qq", "group_qq", "admin_qq")}
    for key, values in lists.items():
        if not isinstance(values, list) or any(not isinstance(v, str) or not v.isascii() or not v.isdigit() or not 5 <= len(v) <= 13 for v in values):
            raise ValueError(f"{key} must contain QQ numbers as strings.")
    origins = [f"{QQ_PLATFORM_ID}:FriendMessage:{v}" for v in lists["private_qq"]]
    origins += [f"{QQ_PLATFORM_ID}:GroupMessage:{v}" for v in lists["group_qq"]]
    # AstrBot treats an empty allowlist as unrestricted, so keep a nonmatching entry.
    config["platform_settings"]["id_whitelist"] = origins or ["cuteq:unconfigured:deny"]
    config["admins_id"] = lists["admin_qq"]
    config["provider_ltm_settings"]["active_reply"]["whitelist"] = lists["group_qq"]


async def set_persona(prompt: str, tools: list[str]) -> None:
    db = SQLiteDatabase(str(ROOT / "data/data_v4.db"))
    try:
        await db.initialize()
        if await db.get_persona_by_id("dafeiyu"):
            await db.update_persona("dafeiyu", system_prompt=prompt, tools=tools, skills=[])
        else:
            await db.insert_persona("dafeiyu", prompt, tools=tools, skills=[])
    finally:
        await db.engine.dispose()


def main() -> None:
    private = read_json(ROOT / "data/cuteq-private.json")
    if "napcat_token" not in private:
        private["napcat_token"] = secrets.token_hex(24)
        write_json(ROOT / "data/cuteq-private.json", private)
    access_path = ROOT / "data/cuteq-access.json"
    if not access_path.exists():
        write_json(access_path, {"private_qq": [], "group_qq": [], "admin_qq": []})
    access = read_json(access_path)
    config = AstrBotConfig(config_path=str(ROOT / "data/cmd_config.json"))
    config["dashboard"].update(host="127.0.0.1", port=6185, username=private["dashboard"]["username"])
    _set_dashboard_password(config, private["dashboard"]["password"])
    config["disable_metrics"] = True
    config["timezone"] = "Asia/Shanghai"
    config["provider_sources"] = [{
        "id": "cuteq-deepseek", "provider": "deepseek", "type": "openai_chat_completion",
        "provider_type": "chat_completion", "enable": True,
        "key": [private["model"]["api_key"]], "api_base": private["model"]["base_url"],
        "timeout": 120, "proxy": "", "custom_headers": {},
    }]
    config["provider"] = [{
        "id": PROVIDER_ID, "provider_source_id": "cuteq-deepseek", "type": "openai_chat_completion",
        "provider_type": "chat_completion", "enable": True, "model": private["model"]["model"],
        "custom_extra_body": {"reasoning_effort": "none"},
    }]
    config["agent_runner"]["config"]["model"].update(provider_id=PROVIDER_ID, request_max_retries=1)
    config["agent_runner"]["config"]["persona"]["persona_id"] = "dafeiyu"
    config["agent_runner"]["config"]["compression"].update(max_turns=80, provider_id=PROVIDER_ID)
    config["agent_runner"]["config"]["misc"]["max_steps"] = 12
    config["provider_settings"]["computer_use_runtime"] = "none"
    config["provider_settings"]["proactive_capability"]["add_cron_tools"] = False
    config["provider_settings"]["default_image_caption_provider_id"] = PROVIDER_ID
    config["provider_ltm_settings"].update(group_message_history_enable=True, group_message_history_max_cnt=150)
    config["provider_ltm_settings"]["active_reply"].update(enable=True, possibility_reply=0.06)
    config["platform_settings"].update(
        enable_id_white_list=True, wl_ignore_admin_on_group=False,
        wl_ignore_admin_on_friend=False, reply_with_mention=False, reply_with_quote=False,
        no_permission_reply=False, ignore_bot_self_message=True,
    )
    config["platform_settings"]["rate_limit"].update(time=60, count=8, strategy="discard")
    config["platform_settings"]["segmented_reply"]["enable"] = False
    config["platform"] = [{
        "id": QQ_PLATFORM_ID, "type": "aiocqhttp", "enable": True,
        "ws_reverse_host": "127.0.0.1", "ws_reverse_port": 6199,
        "ws_reverse_token": private["onebot_token"],
    }, {"id": "cuteq_xiaoheihe", "type": "xiaoheihe", "enable": False, "profile_id": "default"}]
    apply_access(config, access)
    config.save_config()

    prompt = (ROOT / "config/persona.md").read_text(encoding="utf-8")
    tools = memory_tools()
    asyncio.run(set_persona(prompt, tools))

    memory = plugin_config("astrbot_plugin_memory_companion")
    memory["memory_summary"]["provider_id"] = PROVIDER_ID
    memory["memory_summary"]["private_provider_id"] = PROVIDER_ID
    memory["memory_summary"]["group_provider_id"] = PROVIDER_ID
    memory["retrieval"]["embedding_enabled"] = False
    memory["source_semantic"]["enabled"] = False
    memory["conversation_memory_advanced"]["cross_window_private_to_group_enabled"] = False
    memory["visibility"]["enable_acl_rules"] = True
    memory.save_config()

    proactive = plugin_config("astrbot_plugin_proactive_chat")
    proactive["web_admin"]["enabled"] = False
    proactive["telemetry_config"]["enabled"] = False
    proactive["notification_settings"]["enabled"] = False
    proactive["friend_settings"]["schedule_settings"].update(min_interval_minutes=120, max_interval_minutes=480, quiet_hours="23-8", max_unanswered_times=1)
    proactive["friend_settings"]["tts_settings"]["enable_tts"] = False
    for kind, access_key, event_type in (("friend_settings", "private_qq", "FriendMessage"), ("group_settings", "group_qq", "GroupMessage")):
        proactive[kind]["session_list"] = [f"{QQ_PLATFORM_ID}:{event_type}:{v}" for v in access[access_key]]
        proactive[kind]["proactive_prompt"] = prompt + "\n结合最近的话题与当前时间，自然地说一句有实际内容的话；没有值得说的内容时不要硬凑，不催促对方回复。"
    proactive.save_config()

    qzone = plugin_config("astrbot_plugin_qzone")
    qzone["trigger"].update(publish_cron="", comment_cron="", read_prob=0, send_admin=False)
    # Public drafts should not automatically turn arbitrary group history into material.
    qzone["source"]["post_max_msg"] = 0
    for key in ("post_provider_id", "comment_provider_id", "reply_provider_id"):
        qzone["llm"][key] = PROVIDER_ID
    for key in ("post_prompt", "comment_prompt", "reply_prompt"):
        qzone["llm"][key] = prompt + "\n只基于本次明确给出的公开主题或帖子生成简短内容，不引用私人聊天。"
    qzone.save_config()

    bili = plugin_config("astrbot_plugin_bilibili_ai_bot")
    for key in ("LLM_PROVIDER_ID", "SUMMARY_PROVIDER_ID", "GENERATION_PROVIDER_ID"):
        bili[key] = PROVIDER_ID
    bili["VIDEO_VISION_PROVIDER_ID"] = PROVIDER_ID
    bili["IMAGE_VISION_PROVIDER_ID"] = PROVIDER_ID
    bili["VIDEO_VISION_FORMAT"] = "none"
    for key in ("ENABLE_REPLY", "ENABLE_PRIVATE_MESSAGES", "ENABLE_LIVE_DANMAKU_REPLY", "ENABLE_PROACTIVE", "ENABLE_DYNAMIC", "ENABLE_DYNAMIC_WATCH", "ENABLE_BANGUMI", "ENABLE_OWNER_RECOMMEND", "ENABLE_LLM_TOOLS", "ENABLE_AUTO_BLOCK", "PROACTIVE_LIKE", "PROACTIVE_COIN", "PROACTIVE_FAV", "PROACTIVE_FOLLOW", "PROACTIVE_COMMENT"):
        bili[key] = False
    bili["USE_ASTRBOT_PERSONA"] = True
    bili["MEMORY_ISOLATION_MODE"] = "isolated"
    bili.save_config()

    xhh = plugin_config("astrbot_plugin_xiaoheihe_adapter")
    for profile in xhh["profiles"]:
        profile["dry_run"] = True
    xhh["providers"]["llm_provider_id"] = PROVIDER_ID
    xhh["providers"]["image_provider_id"] = PROVIDER_ID
    xhh["proactive_feed"].update(enabled=False, dry_run=True, review_required=True)
    xhh["permissions"]["map_owner_to_astrbot_admin"] = False
    xhh.save_config()

    napcat_config = ROOT / "data/napcat/config"
    write_json(napcat_config / "webui.json", {
        "host": "127.0.0.1", "port": 6099, "token": private["napcat_token"],
        "autoLoginAccount": "", "disableWebUI": False,
    })
    write_json(napcat_config / "onebot11.json", {"network": {
        "httpServers": [], "httpSseServers": [], "httpClients": [], "websocketServers": [], "plugins": [],
        "websocketClients": [{"name": "cuteq", "enable": True, "url": "ws://127.0.0.1:6199/ws",
            "messagePostFormat": "array", "reportSelfMessage": False, "reconnectInterval": 5000,
            "token": private["onebot_token"], "debug": False, "heartInterval": 30000}],
    }})
    write_json(napcat_config / "napcat.json", {
        "fileLog": False, "consoleLog": True, "consoleLogLevel": "info", "autoTimeSync": False,
    })

    login = (
        "cuteQ 本地管理\n\n"
        "地址：http://127.0.0.1:6185\n"
        f"用户名：{private['dashboard']['username']}\n"
        f"密码：{private['dashboard']['password']}\n\n"
        "QQ 登录管理：http://127.0.0.1:6099\n"
        f"QQ 登录管理 Token：{private['napcat_token']}\n\n"
        "QQ 私聊与群聊名单：data/cuteq-access.json\n"
        "修改名单后双击根目录的“应用聊天名单.vbs”。\n"
        "账号尚未扫码登录；公开发布功能尚未启用。\n"
    )
    (ROOT / "data/登录信息.txt").write_text(login, encoding="utf-8-sig")
    print(json.dumps({"configured": True, "model": private["model"]["model"], "persona": "dafeiyu", "private_contacts": len(access["private_qq"]), "groups": len(access["group_qq"]), "memory_tools": tools}, ensure_ascii=False))


if __name__ == "__main__":
    main()
