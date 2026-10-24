from __future__ import annotations

import asyncio
import copy
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import httpx
import psutil
from websockets.asyncio.client import connect

from configure import ROOT, apply_context_policy, apply_memory_policy, memory_tools, read_baseline_config, read_json, write_json
from astrbot.core.db.sqlite import SQLiteDatabase


async def persona(path):
    database = SQLiteDatabase(str(path))
    try:
        await database.initialize()
        await database.insert_persona("dafeiyu", (ROOT / "config/persona.md").read_text(encoding="utf-8"), tools=memory_tools(), skills=[])
    finally:
        await database.engine.dispose()


async def exchange(websocket, text, user=100000101, *, expect_reply=True):
    identifier = int(time.time()*1000)
    await websocket.send(json.dumps({"self_id": 999999999, "time": int(time.time()), "post_type": "message", "message_type": "private", "sub_type": "friend", "message_id": identifier, "user_id": user, "message": [{"type": "text", "data": {"text": text}}], "raw_message": text, "font": 0, "sender": {"user_id": user, "nickname": "隔离验收用户"}}))
    deadline = time.monotonic() + (90 if expect_reply else 3)
    while time.monotonic() < deadline:
        try:
            request = json.loads(await asyncio.wait_for(websocket.recv(), timeout=max(0.1, deadline-time.monotonic())))
        except TimeoutError:
            if expect_reply:
                raise AssertionError("The isolated QQ pipeline did not send a response")
            return ""
        action = request["action"]
        if action == "send_private_msg":
            assert expect_reply and int(request["params"]["user_id"]) == user
            data = {"message_id": identifier+1}
        elif action == "get_login_info":
            data = {"user_id": 999999999, "nickname": "验收大肥鱼"}
        else:
            raise AssertionError("Unexpected OneBot action: " + action)
        await websocket.send(json.dumps({"status": "ok", "retcode": 0, "data": data, "echo": request["echo"]}))
        if action == "send_private_msg":
            return json.dumps(request["params"]["message"], ensure_ascii=False)


async def main():
    directory = Path(tempfile.mkdtemp(prefix="pipeline-evaluation-", dir=ROOT / ".cache")).resolve()
    assert directory.is_relative_to((ROOT / ".cache").resolve())
    data = directory / "data"
    data.mkdir()
    (directory / ".astrbot").touch()
    core = copy.deepcopy(read_json(ROOT / "data/cmd_config.json"))
    apply_context_policy(core)
    core["dashboard"].update(host="127.0.0.1", port=6186)
    token = uuid.uuid4().hex
    core["platform"] = [{"id": "cuteq_qq", "type": "aiocqhttp", "enable": True, "ws_reverse_host": "127.0.0.1", "ws_reverse_port": 6196, "ws_reverse_token": token}]
    core["platform_settings"]["id_whitelist"] = ["cuteq_qq:FriendMessage:100000101"]
    core["provider_ltm_settings"]["active_reply"]["enable"] = False
    core["plugin_set"] = ["astrbot_plugin_memory_companion", "astrbot_plugin_lonelyfriend_memory", "astrbot"]
    write_json(data / "cmd_config.json", core)
    memory = read_baseline_config("astrbot_plugin_memory_companion_config.json")
    apply_memory_policy(memory)
    write_json(data / "config/astrbot_plugin_memory_companion_config.json", memory)
    for name, source in (("astrbot_plugin_memory_companion", ROOT / "data/plugins/astrbot_plugin_memory_companion"), ("astrbot_plugin_lonelyfriend_memory", ROOT / "plugins/astrbot_plugin_lonelyfriend_memory")):
        shutil.copytree(source, data / "plugins" / name, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))
    await persona(data / "data_v4.db")
    environment = dict(os.environ, ASTRBOT_ROOT=str(directory), PYTHONUTF8="1", PYTHONUNBUFFERED="1")
    log_path = directory / "runtime.log"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen([str(ROOT / ".venv/Scripts/python.exe"), "-m", "astrbot.cli", "run"], cwd=directory, env=environment, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=1) as client:
                for _ in range(35):
                    if process.poll() is not None:
                        raise RuntimeError("Isolated AstrBot exited; inspect its runtime.log")
                    try:
                        if (await client.get("http://127.0.0.1:6186/")).status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    await asyncio.sleep(1)
                else:
                    raise RuntimeError("Isolated AstrBot did not become ready")
            headers = {"Authorization": "Bearer " + token, "X-Self-ID": "999999999", "X-Client-Role": "Universal"}
            async with connect("ws://127.0.0.1:6196/ws", additional_headers=headers) as websocket:
                body = "这是虚构验收，不是我的真实偏好。" + "那天我慢慢讲了退租的经过。" * 500 + "验收记录末尾的盆栽花名是青柚回声。请仅回答收到，不用调用记住工具。"
                await exchange(websocket, body)
                path = data / "plugin_data/astrbot_plugin_memory_companion/memory_companion.db"
                with sqlite3.connect(path) as database:
                    row = database.execute("SELECT content FROM timeline WHERE event_type='user_message' AND content LIKE ?", ("%验收记录末尾的盆栽花名%",)).fetchone()
                    assert row and row[0] == body, "The deployed hook truncated the original text"
                reset_reply = await exchange(websocket, "/new")
                assert "Switched to new conversation" in reset_reply, "The native conversation reset did not run"
                recalled = await exchange(websocket, "刚才那段验收原文末尾，盆栽花名是什么？只返回花名。")
                assert "青柚回声" in recalled, "The real QQ pipeline could not recall the original after /new"
                denied = "LONELYFRIEND_DENIED_PIPELINE_ACCEPTANCE"
                await exchange(websocket, denied, user=100000109, expect_reply=False)
                with sqlite3.connect(path) as database:
                    assert database.execute("SELECT count(*) FROM timeline WHERE content LIKE ?", ("%"+denied+"%",)).fetchone()[0] == 0
            report = {"isolated_astrbot": True, "real_deepseek": True, "local_onebot_transport": True, "external_qq_account_used": False, "original_characters": len(body), "full_original_stored": True, "new_conversation_recalled_original_tail": True, "unapproved_private_message_not_archived_or_answered": True, "reply": recalled, "log_directory": str(directory)}
            write_json(ROOT / "data/pipeline-verification.json", report)
            print(json.dumps(report, ensure_ascii=False))
        finally:
            if process.poll() is None:
                owned = psutil.Process(process.pid)
                children = owned.children(recursive=True)
                for child in reversed(children):
                    child.terminate()
                process.terminate()
                psutil.wait_procs(children, timeout=10)
                process.wait(timeout=10)
            print(json.dumps({"isolated_runtime_log": str(log_path)}))


if __name__ == "__main__":
    asyncio.run(main())
