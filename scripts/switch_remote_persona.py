from __future__ import annotations

import argparse
import msvcrt
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "data/人格切换结果.txt"
NAMES = {"roast": "毒舌损友", "dafeiyu": "大肥鱼"}


def write_result(text: str):
    timestamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
    RESULT.write_text(f"北京时间：{timestamp}\n\n{text}\n", encoding="utf-8-sig")


def main():
    parser = argparse.ArgumentParser(description="Switch a server test persona in the background and initialize prior chat and memory.")
    parser.add_argument("persona_id", choices=sorted(NAMES))
    argument = parser.parse_args()
    with (ROOT / "data/server-restart.lock").open("a+b") as lock:
        if not lock.seek(0, 2):
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return 0
        write_result("正在切换到“" + NAMES[argument.persona_id] + "”，并初始化服务器上的旧测试对话、记忆和学习状态。")
        command = ("cd /opt/lonelyfriend\nPYTHONPATH=/opt/lonelyfriend/extensions/python "
                   ".venv/bin/python scripts/switch_server_persona.py " + shlex.quote(argument.persona_id) + "\n")
        try:
            result = subprocess.run([sys.executable, str(ROOT / ".cache/ssh/remote.py"), "run"],
                input=command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                cwd=ROOT, timeout=240, creationflags=subprocess.CREATE_NO_WINDOW)
        except (OSError, subprocess.SubprocessError) as error:
            write_result("切换结果尚未确认，请先检查服务状态。\n\n" + str(error))
            return 1
        if result.returncode:
            write_result("切换未完成或检查未通过，请查看以下原因。\n\n" + (result.stderr.strip() or result.stdout.strip()))
            return 1
        write_result("已切换到“" + NAMES[argument.persona_id] + "”，本轮测试数据已初始化，聊天服务检查通过。\n\n" + result.stdout.strip())
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
