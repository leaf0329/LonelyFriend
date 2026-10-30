from __future__ import annotations

import argparse
import json
import os
import pwd
import shutil
import sqlite3
import subprocess
import tempfile
import time
from contextlib import closing
from pathlib import Path

import httpx

from switch_persona import ROOT, RESET_DIRECTORIES, RESET_FILES, contained, load_card, read, switch_and_reset, write

CONFIG_FILES = (
    "data/cmd_config.json", "data/data_v4.db", "data/persona.md", "data/persona-source.json",
    "data/persona-reset.json", "data/persona-deployment.json",
    "data/config/astrbot_plugin_proactive_chat_config.json", "data/config/astrbot_plugin_qzone_config.json",
    "data/config/astrbot_plugin_self_learning_config.json", "data/plugin_data/astrbot_plugin_rsshub/rsshub.db",
)


def switch_server(root: Path, persona_id: str) -> dict:
    root = root.resolve()
    load_card(root, persona_id)
    private = read(root / "data/cuteq-private.json")
    paths = list(dict.fromkeys((*CONFIG_FILES, *RESET_FILES, *RESET_DIRECTORIES)))
    for relative in paths:
        contained(root, relative)
    cache = root / ".cache"
    cache.mkdir(exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix="persona-reset-rollback-", dir=cache))
    os.chmod(backup, 0o700)
    account = pwd.getpwnam("lonelyfriend")
    subprocess.run(["systemctl", "stop", "lonelyfriend"], check=True)
    present = []
    mutation_started = False
    committed = False
    try:
        for relative in paths:
            source = root / relative
            target = backup / relative
            if source.is_dir():
                shutil.copytree(source, target, symlinks=True)
                present.append(relative)
            elif source.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                if source.suffix == ".db":
                    with closing(sqlite3.connect(source)) as origin, closing(sqlite3.connect(target)) as destination:
                        origin.backup(destination)
                else:
                    shutil.copy2(source, target)
                present.append(relative)
        mutation_started = True
        report = switch_and_reset(root, persona_id)
        for relative in CONFIG_FILES:
            path = root / relative
            if path.exists():
                os.chown(path, account.pw_uid, account.pw_gid)
        subprocess.run(["systemctl", "start", "lonelyfriend"], check=True)
        config = read(root / "data/cmd_config.json")
        url = f"http://127.0.0.1:{config['dashboard']['port']}"
        deadline = time.monotonic() + 110
        ready = False
        with httpx.Client(base_url=url, timeout=10, trust_env=False) as client:
            while time.monotonic() < deadline:
                try:
                    response = client.post("/api/auth/login", json=private["dashboard"])
                    response.raise_for_status()
                    login = response.json()
                    if login.get("status") == "ok":
                        client.headers["Authorization"] = "Bearer " + login["data"]["token"]
                        plugins = client.get("/api/v1/plugins").json()["data"]
                        expected = 9 if (root / "data/extensions.json").is_file() else 4
                        ready = len([p for p in plugins if p["activated"] and not p["reserved"]]) == expected
                        if ready:
                            assert not client.get("/api/v1/plugins/failed").json()["data"]
                            persona = client.get("/api/v1/personas/by-id", params={"persona_id": persona_id}).json()["data"]
                            assert persona["system_prompt"] == (root / "data/persona.md").read_text()
                            break
                except httpx.ConnectError:
                    pass
                time.sleep(2)
        if not ready:
            raise RuntimeError("Chat service did not become healthy after persona reset")
        committed = True
        report.update(chat_service_ready=True, failed_plugins=[], temporary_rollback_removed=False)
        assert backup.resolve().parent == cache.resolve()
        try:
            shutil.rmtree(backup)
            report["temporary_rollback_removed"] = True
        except OSError as error:
            report["backup_cleanup_error"] = str(error)
        write(root / "data/persona-reset.json", report)
        write(root / "data/persona-deployment.json", report)
        return report
    except BaseException:
        if committed:
            raise
        if not mutation_started:
            subprocess.run(["systemctl", "start", "lonelyfriend"], check=True)
            raise
        subprocess.run(["systemctl", "stop", "lonelyfriend"], check=False)
        for relative in paths:
            target = root / relative
            saved = backup / relative
            if relative in present:
                if saved.is_dir():
                    if target.is_dir():
                        shutil.rmtree(target)
                    shutil.copytree(saved, target, symlinks=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if target.suffix == ".db":
                        for suffix in ("-wal", "-shm"):
                            Path(str(target) + suffix).unlink(missing_ok=True)
                    shutil.copy2(saved, target)
            elif target.is_file():
                target.unlink()
            elif target.is_dir():
                shutil.rmtree(target)
            if target.exists():
                for item in [target, *(target.rglob("*") if target.is_dir() else [])]:
                    if not item.is_symlink():
                        os.chown(item, account.pw_uid, account.pw_gid)
        subprocess.run(["systemctl", "start", "lonelyfriend"], check=True)
        raise


def main():
    parser = argparse.ArgumentParser(description="Stop the Ubuntu chat service, switch persona with a full test reset, and restart it.")
    parser.add_argument("persona_id")
    parser.add_argument("--root", type=Path, default=ROOT)
    arguments = parser.parse_args()
    print(json.dumps(switch_server(arguments.root, arguments.persona_id), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
