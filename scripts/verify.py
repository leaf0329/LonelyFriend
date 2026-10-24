from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sqlite3
import subprocess
import time

import httpx
from websockets.asyncio.client import connect

from configure import ROOT, read_json, write_json


def sse_events(body: str) -> list[dict]:
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]


def marker_count(marker: str) -> int:
    path = ROOT / "data/plugin_data/astrbot_plugin_memory_companion/memory_companion.db"
    with sqlite3.connect(path) as db:
        memories = db.execute("SELECT count(*) FROM memories WHERE content LIKE ?", (f"%{marker}%",)).fetchone()[0]
        timeline = db.execute("SELECT count(*) FROM timeline WHERE content LIKE ?", (f"%{marker}%",)).fetchone()[0]
        return memories + timeline


async def denied_onebot_message(private: dict) -> dict:
    marker = "CUTEQ_DENIED_PRIVATE_ACCEPTANCE"
    before = marker_count(marker)
    payload = {
        "self_id": 999999999, "time": int(time.time()), "post_type": "message",
        "message_type": "private", "sub_type": "friend", "message_id": 2026102401,
        "user_id": 100000009, "message": [{"type": "text", "data": {"text": marker}}],
        "raw_message": marker, "font": 0,
        "sender": {"user_id": 100000009, "nickname": "权限验收"},
    }
    headers = {"Authorization": f"Bearer {private['onebot_token']}", "X-Self-ID": "999999999", "X-Client-Role": "Universal"}
    async with connect("ws://127.0.0.1:6199/ws", additional_headers=headers) as websocket:
        await websocket.send(json.dumps(payload))
        try:
            response = await asyncio.wait_for(websocket.recv(), timeout=2)
        except TimeoutError:
            response = None
    after = marker_count(marker)
    if response is not None or before != after:
        raise AssertionError("An unapproved private message was processed or answered.")
    return {"connected": True, "reply_sent": False, "role_memory_written": False}


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify the local cuteQ installation without publishing to social accounts.")
    parser.add_argument("--chat", action="store_true", help="Include a real DeepSeek recall through the local chat pipeline")
    args = parser.parse_args()
    private = read_json(ROOT / "data/cuteq-private.json")
    config = read_json(ROOT / "data/cmd_config.json")
    assert config["platform_settings"]["enable_id_white_list"]
    assert config["platform_settings"]["id_whitelist"]
    assert config["dashboard"]["host"] == "127.0.0.1"
    report = {"astrbot": "4.29.1", "model": private["model"]["model"], "private_contacts": len(read_json(ROOT / "data/cuteq-access.json")["private_qq"])}
    with httpx.Client(base_url="http://127.0.0.1:6099", trust_env=False, timeout=5) as napcat:
        assert napcat.get("/webui/").status_code == 200
        password_hash = hashlib.sha256((private["napcat_token"] + ".napcat").encode()).hexdigest()
        login = napcat.post("/api/auth/login", json={"hash": password_hash}).json()
        assert login["code"] == 0, "NapCat management login failed."
        napcat.headers["Authorization"] = "Bearer " + login["data"]["Credential"]
        state = napcat.post("/api/QQLogin/CheckLoginStatus", json={}).json()
        assert state["code"] == 0
        qq_logged_in = state["data"]["isLogin"]
        report["qq_login_page"] = {"reachable": True, "qq_logged_in": qq_logged_in, "requires_user_scan": not qq_logged_in}
    with httpx.Client(base_url="http://127.0.0.1:6185", timeout=120, trust_env=False) as client:
        assert client.get("/").status_code == 200
        unauthenticated = client.get("/api/v1/plugins")
        assert unauthenticated.status_code in (401, 403)
        login = client.post("/api/auth/login", json=private["dashboard"])
        login.raise_for_status()
        result = login.json()
        assert result["status"] == "ok"
        client.headers["Authorization"] = "Bearer " + result["data"]["token"]
        plugins = client.get("/api/v1/plugins")
        plugins.raise_for_status()
        expected = ["astrbot_plugin_qzone", "astrbot_plugin_bilibili_ai_bot", "astrbot_plugin_xiaoheihe_adapter", "astrbot_plugin_memory_companion", "astrbot_plugin_proactive_chat"]
        assert all(name in plugins.text for name in expected)
        failed = client.get("/api/v1/plugins/failed")
        failed.raise_for_status()
        assert not failed.json()["data"], "Some plugins failed to load."
        report["dashboard"] = {"reachable": True, "authentication_required": True, "plugins_loaded": expected, "failed_plugins": []}
        profile = read_json(ROOT / "data/cuteq-public-profile.json")
        profiles = client.get("/api/v1/config-routes").json()["data"]["routing"]
        assert profiles["cuteq_xiaoheihe::"] == profile["id"]
        report["public_profile_isolated"] = True
        commands = client.get("/api/v1/commands").json()["data"]["items"]
        social_commands = [command for command in commands if command["plugin"] in ("astrbot_plugin_qzone", "astrbot_plugin_bilibili_ai_bot")]
        assert social_commands and all(command["permission"] == "admin" for command in social_commands)
        report["social_commands_require_admin"] = len(social_commands)
        if args.chat:
            session = client.get("/api/chat/new_session").json()["data"]["session_id"]
            reply = client.post("/api/chat/send", json={
                "session_id": session,
                "message": "请从长期记忆中找出此前安装验收记录里的示例花名，只返回花名。没有找到就如实说没有。",
                "selected_provider": "cuteq-deepseek-flash",
                "flags": {"enable_streaming": False, "enable_reasoning": False},
            })
            reply.raise_for_status()
            events = sse_events(reply.text)
            plain = [event["data"] for event in events if event["type"] == "plain" and event.get("chain_type") not in ("tool_call", "tool_call_result")]
            assert any("海盐星桥" in text for text in plain), "Persisted acceptance memory was not recalled."
            report["chat_pipeline"] = {"real_model_response": True, "fresh_session_recalled_persisted_memory": True, "reply": plain[-1]}

    if not qq_logged_in:
        report["onebot_denied_private"] = asyncio.run(denied_onebot_message(private))
    else:
        report["onebot_denied_private"] = {"skipped": "QQ is already connected; do not inject a test connection."}
    binaries = sorted((ROOT / "runtime/ffmpeg").glob("*/bin/ffmpeg.exe"))
    assert len(binaries) == 1
    ffmpeg = binaries[0]
    probe = ffmpeg.with_name("ffprobe.exe")
    sample = ROOT / ".cache/acceptance-video.mp4"
    subprocess.run([str(ffmpeg), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=0.2", "-frames:v", "2", "-y", str(sample)], check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    inspected = subprocess.run([str(probe), "-v", "error", "-show_entries", "stream=width,height", "-of", "json", str(sample)], check=True, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    dimensions = json.loads(inspected.stdout)["streams"][0]
    assert dimensions == {"width": 64, "height": 64}
    report["video_tools"] = {"ffmpeg": True, "ffprobe": True, "synthetic_video_decoded": True}
    report["social_accounts"] = {"qq": "logged_in" if qq_logged_in else "awaiting_login", "bilibili": "awaiting_login", "xiaoheihe": "awaiting_login", "live_publication_verified": False}
    write_json(ROOT / "data/verification.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
