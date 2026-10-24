from __future__ import annotations

import argparse
import json

from configure import ROOT, apply_access, plugin_config, read_json, write_json


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply the QQ private/group allowlists without changing model or platform accounts.")
    parser.add_argument("--check", action="store_true", help="Validate without writing configuration")
    args = parser.parse_args()
    access = read_json(ROOT / "data/cuteq-access.json")
    config = read_json(ROOT / "data/cmd_config.json")
    apply_access(config, access)
    if not args.check:
        write_json(ROOT / "data/cmd_config.json", config)
        proactive = plugin_config("astrbot_plugin_proactive_chat")
        proactive["friend_settings"]["session_list"] = [f"cuteq_qq:FriendMessage:{value}" for value in access["private_qq"]]
        proactive["group_settings"]["session_list"] = [f"cuteq_qq:GroupMessage:{value}" for value in access["group_qq"]]
        proactive.save_config()
    print(json.dumps({"valid": True, "applied": not args.check, "private_contacts": len(access["private_qq"]), "groups": len(access["group_qq"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
