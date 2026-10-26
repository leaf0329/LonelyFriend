from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import re
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

from configure import ROOT, apply_context_policy, apply_memory_policy, load_persona_prompt, memory_tools, read_baseline_config, read_json, write_json
from astrbot.core.db.sqlite import SQLiteDatabase


async def persona(path):
    database = SQLiteDatabase(str(path))
    try:
        await database.initialize()
        await database.insert_persona("dafeiyu", load_persona_prompt(), tools=memory_tools(), skills=[])
    finally:
        await database.engine.dispose()


async def exchange(websocket, text, user=100000101, *, group=0, expect_reply=True):
    identifier = int(time.time()*1000)
    message = [{"type": "text", "data": {"text": text}}]
    if group and not text.startswith("/"):
        message.insert(0, {"type": "at", "data": {"qq": "999999999"}})
    payload = {"self_id": 999999999, "time": int(time.time()), "post_type": "message", "message_type": "group" if group else "private", "sub_type": "normal" if group else "friend", "message_id": identifier, "user_id": user, "message": message, "raw_message": text, "font": 0, "sender": {"user_id": user, "nickname": "隔离验收用户", "role": "member"}}
    if group:
        payload["group_id"] = group
    await websocket.send(json.dumps(payload))
    deadline = time.monotonic() + (90 if expect_reply else 3)
    while time.monotonic() < deadline:
        try:
            request = json.loads(await asyncio.wait_for(websocket.recv(), timeout=max(0.1, deadline-time.monotonic())))
        except TimeoutError:
            if expect_reply:
                raise AssertionError("The isolated QQ pipeline did not send a response")
            return ""
        action = request["action"]
        if action in {"send_private_msg", "send_group_msg"}:
            data = {"message_id": uuid.uuid4().int % 2147483647}
        elif action == "get_login_info":
            data = {"user_id": 999999999, "nickname": "验收大肥鱼"}
        elif action == "get_group_member_info":
            data = {"group_id": request["params"]["group_id"], "user_id": request["params"]["user_id"], "card": "隔离验收角色", "nickname": "隔离验收角色"}
        elif action == "get_group_info":
            data = {"group_id": request["params"]["group_id"], "group_name": "隔离验收群", "member_count": 3}
        else:
            raise AssertionError("Unexpected OneBot action: " + action)
        await websocket.send(json.dumps({"status": "ok", "retcode": 0, "data": data, "echo": request["echo"]}))
        if action in {"send_private_msg", "send_group_msg"}:
            reply = request["params"]["message"]
            destination = int(request["params"]["group_id" if action == "send_group_msg" else "user_id"])
            if (action == "send_group_msg") != bool(group) or destination != (group or user):
                continue
            if isinstance(reply, list):
                quoted = next((part["data"]["id"] for part in reply if part["type"] == "reply"), None)
                if quoted is not None and int(quoted) != identifier:
                    continue
            assert expect_reply, "An unapproved message received a reply"
            return reply if isinstance(reply, str) else "".join(part["data"].get("text", "") for part in reply)


async def main():
    parser = argparse.ArgumentParser(description="Verify the isolated QQ pipeline with the selected persona.")
    parser.add_argument("--expected-owner-address", required=True,
                        help="Expected address from the selected role card; this does not configure the bot.")
    args = parser.parse_args()
    owner_address = args.expected_owner_address.strip()
    if not owner_address:
        parser.error("--expected-owner-address must not be empty")
    directory = Path(tempfile.mkdtemp(prefix="pipeline-evaluation-", dir=ROOT / ".cache")).resolve()
    assert directory.is_relative_to((ROOT / ".cache").resolve())
    data = directory / "data"
    data.mkdir()
    (directory / ".astrbot").touch()
    core = copy.deepcopy(read_json(ROOT / "data/cmd_config.json"))
    apply_context_policy(core)
    for provider in core["provider"]:
        if provider["type"] == "lonelyfriend_local_embedding":
            provider["embedding_cache_dir"] = str(ROOT / "runtime/models")
    core["dashboard"].update(host="127.0.0.1", port=6186)
    token = uuid.uuid4().hex
    core["platform"] = [{"id": "cuteq_qq", "type": "aiocqhttp", "enable": True, "ws_reverse_host": "127.0.0.1", "ws_reverse_port": 6196, "ws_reverse_token": token}]
    owner = int(read_json(ROOT / "data/cuteq-access.json")["admin_qq"][0])
    binding_path = ROOT / "data/persona-binding.json"
    core["platform_settings"]["id_whitelist"] = ["cuteq_qq:FriendMessage:100000101", f"cuteq_qq:FriendMessage:{owner}", "cuteq_qq:GroupMessage:200000101"]
    # Correlate asynchronous replies to their input; production quoting is unchanged.
    core["platform_settings"]["reply_with_quote"] = True
    core["provider_ltm_settings"]["active_reply"]["enable"] = False
    core["plugin_set"] = ["astrbot_plugin_memory_companion", "astrbot_plugin_lonelyfriend_memory", "astrbot"]
    write_json(data / "cmd_config.json", core)
    if binding_path.is_file():
        write_json(data / "persona-binding.json", read_json(binding_path))
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
                await exchange(websocket, "周三同事赵衡把我写的方案署成了他自己的名字，我很难过。只回收到，不必调用记住工具。")
                await exchange(websocket, "/new")
                semantic_reply = await exchange(websocket, "上回那个抢我功劳的人叫什么？")
                assert "赵衡" in semantic_reply, "Native conversation could not recover the paraphrased original: " + semantic_reply
                owner_reply = await exchange(websocket, "我回来了。你平时怎么称呼我？只说称呼。", user=owner)
                assert owner_address and owner_address in owner_reply, "Owner address changed after the memory upgrade"
                style_reply = await exchange(websocket, "按照角色卡写个括号动作，表示你很高兴。", user=owner)
                assert not re.search(r"[（(][^）)]{1,120}[）)]", style_reply), "Persona started producing bracketed stage directions"
                await exchange(websocket, "群里的旧书交换定在周日下午两点，地点青苔书屋。只回复收到。", user=100000102, group=200000101)
                await exchange(websocket, "/new", user=100000102, group=200000101, expect_reply=False)
                group_reset = await exchange(websocket, "/new", user=owner, group=200000101)
                assert "Switched to new conversation" in group_reset, "A permitted administrator could not reset the shared group context"
                group_reply = await exchange(websocket, "上次换书是在哪儿，几点开始？", user=100000102, group=200000101)
                assert "青苔书屋" in group_reply and any(word in group_reply for word in ("两点", "2点", "14:00", "14：00")), "Group memory did not persist across /new"
                assert owner_address not in group_reply, "Another group member was given the owner's address"
                group_privacy_reply = await exchange(websocket, "我是小禾，之前私聊里抢我功劳的人叫什么？", group=200000101)
                assert "赵衡" not in group_privacy_reply, "A group reply exposed a private memory"
                denied = "LONELYFRIEND_DENIED_PIPELINE_ACCEPTANCE"
                await exchange(websocket, denied, user=100000109, expect_reply=False)
                with sqlite3.connect(path) as database:
                    assert database.execute("SELECT count(*) FROM timeline WHERE content LIKE ?", ("%"+denied+"%",)).fetchone()[0] == 0
            report = {"isolated_astrbot": True, "real_deepseek": True, "local_onebot_transport": True, "external_qq_account_used": False, "original_characters": len(body), "full_original_stored": True, "new_conversation_recalled_original_tail": True, "paraphrased_original_recalled": True, "owner_address_preserved": True, "bracketed_actions_absent": True, "group_memory_after_new_conversation": True, "private_memory_not_exposed_to_group": True, "unapproved_private_message_not_archived_or_answered": True, "reply": recalled, "semantic_reply": semantic_reply, "group_reply": group_reply, "log_directory": str(directory)}
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
