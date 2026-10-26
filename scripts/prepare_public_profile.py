from __future__ import annotations

import json
from urllib.parse import quote

import httpx

from configure import ROOT, read_json


def main() -> None:
    private = read_json(ROOT / "data/cuteq-private.json")
    with httpx.Client(base_url="http://127.0.0.1:6185", timeout=30, trust_env=False) as client:
        login = client.post("/api/auth/login", json=private["dashboard"])
        login.raise_for_status()
        client.headers["Authorization"] = "Bearer " + login.json()["data"]["token"]
        commands = client.get("/api/v1/commands")
        commands.raise_for_status()
        restricted = []
        for command in commands.json()["data"]["items"]:
            if command["plugin"] != "astrbot_plugin_qzone":
                continue
            if command["permission"] != "admin":
                changed = client.patch("/api/v1/commands/" + quote(command["handler_full_name"], safe=""), json={"permission_group": "admin"})
                changed.raise_for_status()
                if changed.json()["status"] != "ok":
                    raise RuntimeError("Social command permission update failed")
            restricted.append(command["handler_full_name"])
        if not restricted:
            raise RuntimeError("QQ space commands have not been loaded")
        print(json.dumps({"qzone_commands_require_admin": len(restricted)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
