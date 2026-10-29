from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3

import httpx

from configure import ROOT, read_json, write_json


def main() -> None:
    parser = argparse.ArgumentParser(description="Check a running Linux installation without sending QQ messages.")
    parser.add_argument("--model", action="store_true", help="Call the configured model and local embedding provider.")
    args = parser.parse_args()
    private = read_json(ROOT / "data/cuteq-private.json")
    config = read_json(ROOT / "data/cmd_config.json")
    expected = {"astrbot_plugin_qzone", "astrbot_plugin_memory_companion",
                "astrbot_plugin_proactive_chat", "astrbot_plugin_lonelyfriend_memory"}
    if (ROOT / "data/extensions.json").is_file():
        expected.update({"astrbot_plugin_rsshub", "astrbot_plugin_parser", "astrbot_plugin_meme_generator",
                         "astrbot_plugin_self_learning", "astrbot_plugin_lonelyfriend_activities"})
    assert {p.name for p in (ROOT / "data/plugins").iterdir() if p.is_dir() and not p.name.startswith("_")} == expected
    assert [p["id"] for p in config["platform"]] == ["cuteq_qq"]
    assert config["dashboard"]["host"] == "127.0.0.1"
    assert config["platform_settings"]["enable_id_white_list"]
    assert config["platform_settings"]["id_whitelist"]
    report = {"qq_only": True, "private_contacts": len(read_json(ROOT / "data/cuteq-access.json")["private_qq"])}
    with httpx.Client(base_url="http://127.0.0.1:6185", timeout=120, trust_env=False) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/v1/plugins").status_code in (401, 403)
        login = client.post("/api/auth/login", json=private["dashboard"])
        login.raise_for_status()
        assert login.json()["status"] == "ok"
        client.headers["Authorization"] = "Bearer " + login.json()["data"]["token"]
        loaded = client.get("/api/v1/plugins").json()["data"]
        assert {p["name"] for p in loaded if not p["reserved"] and p["activated"]} == expected
        assert not client.get("/api/v1/plugins/failed").json()["data"]
        commands = client.get("/api/v1/commands").json()["data"]["items"]
        qzone_commands = [item for item in commands if item["plugin"] == "astrbot_plugin_qzone"]
        assert qzone_commands and all(item["permission"] == "admin" for item in qzone_commands)
        report.update(plugins_loaded=sorted(expected), failed_plugins=[],
                      qzone_admin_commands=len(qzone_commands), dashboard_authentication=True)
        if args.model:
            llm = client.post("/api/v1/providers/cuteq-deepseek-flash/test", json={})
            llm.raise_for_status()
            assert llm.json()["status"] == "ok", llm.json().get("message")
            assert llm.json()["data"]["status"] == "available", llm.json()["data"].get("error")
            report["model_test"] = llm.json()["data"]
            embedding = client.post("/api/v1/providers/cuteq-local-embedding/embedding-dimension", json={})
            embedding.raise_for_status()
            assert embedding.json()["status"] == "ok", embedding.json().get("message")
            assert embedding.json()["data"]["embedding_dimensions"] == 512
            report["local_embedding_dimensions"] = 512
        qzone_status = client.get("/api/plug/astrbot_plugin_qzone/page/status")
        qzone_status.raise_for_status()
        assert qzone_status.json()["ok"]
        report["qzone_account_bound"] = qzone_status.json()["data"]["login"]["bound"]
    with httpx.Client(base_url="http://127.0.0.1:6099", timeout=15, trust_env=False) as client:
        login_hash = hashlib.sha256((private["napcat_token"] + ".napcat").encode()).hexdigest()
        login = client.post("/api/auth/login", json={"hash": login_hash}).json()
        assert login["code"] == 0
        client.headers["Authorization"] = "Bearer " + login["data"]["Credential"]
        state = client.post("/api/QQLogin/CheckLoginStatus", json={}).json()["data"]
        report["qq_logged_in"] = state["isLogin"]
        report["qq_login_phase"] = state.get("loginPhase")
    memory_path = ROOT / "data/plugin_data/astrbot_plugin_memory_companion/memory_companion.db"
    with sqlite3.connect(memory_path) as database:
        assert database.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        report["memory_rows"] = {name: database.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
                                 for name in ("memories", "timeline", "capture_sources", "source_semantic_documents")}
    qzone = read_json(ROOT / "data/config/astrbot_plugin_qzone_config.json")
    report["qzone_publish_cron"] = qzone["trigger"]["publish_cron"]
    report["qzone_group_history_disabled"] = qzone["source"]["post_max_msg"] == 0
    report["live_qq_delivery_verified"] = False
    report["live_publication_verified"] = False
    write_json(ROOT / "data/server-verification.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
