from __future__ import annotations

import copy
import json
from urllib.parse import quote

import httpx

from configure import ROOT, read_json, write_json


def main() -> None:
    private = read_json(ROOT / "data/cuteq-private.json")
    with httpx.Client(base_url="http://127.0.0.1:6185", timeout=30, trust_env=False) as client:
        login = client.post("/api/auth/login", json=private["dashboard"])
        login.raise_for_status()
        client.headers["Authorization"] = "Bearer " + login.json()["data"]["token"]
        response = client.get("/api/v1/config-profiles")
        response.raise_for_status()
        profiles = response.json()["data"]["info_list"]
        existing = next((p for p in profiles if p.get("name") == "大肥鱼·公开社区"), None)
        if existing:
            profile_id = existing["id"]
        else:
            public = copy.deepcopy(read_json(ROOT / "data/cmd_config.json"))
            public["platform_settings"]["enable_id_white_list"] = False
            public["platform_settings"]["id_whitelist"] = []
            public["provider_ltm_settings"]["active_reply"]["enable"] = False
            public["plugin_set"] = ["astrbot_plugin_xiaoheihe_adapter"]
            public["disable_builtin_commands"] = True
            created = client.post("/api/v1/config-profiles", json={"name": "大肥鱼·公开社区", "config": public})
            created.raise_for_status()
            result = created.json()
            if result["status"] != "ok":
                raise RuntimeError(result.get("message", "Public profile creation failed"))
            profile_id = result["data"]["conf_id"]
        routed = client.put("/api/v1/config-routes/cuteq_xiaoheihe::", json={"config_id": profile_id})
        routed.raise_for_status()
        if routed.json()["status"] != "ok":
            raise RuntimeError("Public profile routing failed")
        commands = client.get("/api/v1/commands")
        commands.raise_for_status()
        restricted = []
        for command in commands.json()["data"]["items"]:
            if command["plugin"] not in ("astrbot_plugin_qzone", "astrbot_plugin_bilibili_ai_bot"):
                continue
            if command["permission"] != "admin":
                changed = client.patch("/api/v1/commands/" + quote(command["handler_full_name"], safe=""), json={"permission_group": "admin"})
                changed.raise_for_status()
                if changed.json()["status"] != "ok":
                    raise RuntimeError("Social command permission update failed")
            restricted.append(command["handler_full_name"])
        write_json(ROOT / "data/cuteq-public-profile.json", {"id": profile_id, "platform_id": "cuteq_xiaoheihe"})
        print(json.dumps({"public_profile_ready": True, "qq_allowlist_unchanged": True, "public_profile_excludes_private_memory_and_account_tools": True, "social_commands_require_admin": len(restricted)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
