from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
import msvcrt
import os
from pathlib import Path
import runpy
import select
import socket
import threading
import time
from datetime import datetime, timezone

import psutil

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "data/siyin-link-state.json"
LOG = logging.getLogger("siyin-link")


def available(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def relay(channel, port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2) as local:
            local.settimeout(None)
            while True:
                readable, _, _ = select.select([local, channel], [], [], 5)
                if local in readable:
                    data = local.recv(65536)
                    if not data:
                        return
                    channel.sendall(data)
                if channel in readable:
                    data = channel.recv(65536)
                    if not data:
                        return
                    local.sendall(data)
    except (OSError, EOFError) as error:
        LOG.info("Audio connection closed: %s", type(error).__name__)
    finally:
        channel.close()


def state(status, remote_port, error=None):
    value = {"pid": os.getpid(), "created_at": psutil.Process().create_time(),
             "state": status, "remote_port": remote_port, "error": error,
             "updated_at": datetime.now(timezone.utc).isoformat()}
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(STATE)


def main():
    config = json.loads((ROOT / "data/siyin-link.json").read_text(encoding="utf-8-sig"))
    ssh = runpy.run_path(str(ROOT / ".cache/ssh/remote.py"))
    paramiko = ssh["paramiko"]
    key = paramiko.RSAKey.from_private_key_file(str(ROOT / config["key_file"]))
    with (ROOT / "data/siyin-link.lock").open("a+b") as lock:
        if lock.seek(0, 2) == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return
        (ROOT / "logs").mkdir(exist_ok=True)
        handler = RotatingFileHandler(ROOT / "logs/siyin-link.log", maxBytes=1024 * 1024, backupCount=1, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        LOG.addHandler(handler)
        LOG.setLevel(logging.INFO)
        remote_port, local_port = config["remote_port"], config["local_port"]
        idle = False
        while True:
            if not available(local_port):
                if not idle:
                    state("waiting_for_siyin", remote_port)
                    LOG.info("Waiting for the local Siyin service")
                    idle = True
                time.sleep(5)
                continue
            idle = False
            client = paramiko.SSHClient()
            client.load_host_keys(str(ROOT / ".cache/ssh/known_hosts"))
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
            try:
                client.connect(config["ssh_host"], username=config["ssh_user"], pkey=key,
                               allow_agent=False, look_for_keys=False,
                               timeout=10, banner_timeout=10, auth_timeout=10)
                transport = client.get_transport()
                transport.set_keepalive(15)
                transport.request_port_forward("127.0.0.1", remote_port)
                state("connected", remote_port)
                LOG.info("Private speech tunnel connected")
                probe_at = time.monotonic() + 20
                check_at = time.monotonic() + 5
                while transport.is_active():
                    channel = transport.accept(1)
                    if channel is not None:
                        threading.Thread(target=relay, args=(channel, local_port), daemon=True).start()
                    if time.monotonic() >= check_at:
                        if not available(local_port):
                            break
                        check_at = time.monotonic() + 5
                    if time.monotonic() >= probe_at:
                        # An acknowledged channel detects a dead link; ignored keepalives do not.
                        probe = transport.open_session(timeout=5)
                        probe.close()
                        probe_at = time.monotonic() + 20
            except (paramiko.SSHException, OSError, EOFError) as error:
                state("reconnecting", remote_port, type(error).__name__)
                LOG.warning("Tunnel reconnect required: %s", type(error).__name__)
            finally:
                client.close()
            time.sleep(5)


if __name__ == "__main__":
    main()
