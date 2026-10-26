from __future__ import annotations

import argparse
import asyncio
import json

from configure import ROOT, apply_access, apply_qzone_patch, read_json, write_json
from astrbot.core.db.sqlite import SQLiteDatabase


async def reset_legacy_platform_routes() -> None:
    database = SQLiteDatabase(str(ROOT / "data/data_v4.db"))
    try:
        await database.initialize()
        # SharedPreferences stores its payload inside the database's "val" envelope.
        await database.insert_preference_or_update("global", "global", "abconf_mapping", {"val": {}})
        await database.insert_preference_or_update("global", "global", "umop_config_routing", {"val": {}})
    finally:
        await database.engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Adapt migrated QQ configuration to Linux.")
    parser.add_argument("--enable-qzone-publish", action="store_true",
                        help="Enable two daily autonomous QQ space posts.")
    args = parser.parse_args()
    apply_qzone_patch()
    config_path = ROOT / "data/cmd_config.json"
    config = read_json(config_path)
    private = read_json(ROOT / "data/cuteq-private.json")
    config["platform"] = [item for item in config["platform"] if item["id"] == "cuteq_qq"]
    if len(config["platform"]) != 1:
        raise RuntimeError("The migrated configuration must contain one QQ adapter")
    config["dashboard"].update(host="127.0.0.1", port=6185)
    apply_access(config, read_json(ROOT / "data/cuteq-access.json"))
    config["plugin_set"] = ["*"]
    config["timezone"] = "Asia/Shanghai"
    write_json(config_path, config)

    qzone_path = ROOT / "data/config/astrbot_plugin_qzone_config.json"
    qzone = read_json(qzone_path)
    qzone["trigger"].update(
        publish_cron="0 12,21 * * *" if args.enable_qzone_publish else "",
        publish_offset=2700,
        comment_cron="", read_prob=0, send_admin=False,
    )
    qzone["source"]["post_max_msg"] = 0
    prompt = (ROOT / "data/persona.md").read_text(encoding="utf-8-sig")
    qzone["llm"]["post_prompt"] = prompt + (
        "\n现在以你自己的口吻发一条简短自然的 QQ 空间说说，直接输出正文。"
        "可以写当前时段的心情、游戏感想或轻松的小想法。"
        "不要编造自己实际去过的地方、做过的事情，不要使用括号动作描写。"
        "不要提及管理员、聊天对象、群友或他们的资料和经历，"
        "不要引用私聊、群聊、历史记忆、账号、地址或任何凭据。"
        "不要把角色卡中的私人称呼和关系设定写进公开说说。"
    )
    write_json(qzone_path, qzone)

    webui_path = ROOT / "data/napcat/config/webui.json"
    webui = read_json(webui_path)
    webui.update(host="127.0.0.1", port=6099, token=private["napcat_token"], autoLoginAccount="")
    write_json(webui_path, webui)

    napcat_path = ROOT / "data/napcat/config/napcat.json"
    napcat = read_json(napcat_path)
    napcat.update(
        bypass={key: True for key in ("hook", "window", "module", "process", "container", "js")},
        o3HookMode=0,
    )
    write_json(napcat_path, napcat)
    asyncio.run(reset_legacy_platform_routes())
    print(json.dumps({"qq_only": True, "qzone_autopublish": args.enable_qzone_publish,
                      "qzone_publish_cron": qzone["trigger"]["publish_cron"],
                      "qzone_publish_timezone": "Asia/Shanghai"}))


if __name__ == "__main__":
    main()
