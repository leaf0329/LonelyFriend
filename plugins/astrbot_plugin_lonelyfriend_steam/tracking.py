from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

STATES = {0: "离线", 1: "在线", 2: "忙碌", 3: "离开", 4: "打盹", 5: "想交易", 6: "想一起玩", 7: "隐身"}


def merge_activity(previous: dict | None, current: dict) -> dict:
    if (previous and current["game_id"] and current["game_id"] == previous["game_id"]
            and not current["activity_known"]):
        return {**current, "activity": previous["activity"]}
    return current


def changed(previous: dict, current: dict) -> bool:
    if (previous["persona_state"], previous["game_id"]) != (current["persona_state"], current["game_id"]):
        return True
    return current["activity_known"] and previous["activity"] != current["activity"]


def describe(state: dict) -> str:
    lines = [STATES[state["persona_state"]]]
    if state["game_id"]:
        lines.append(state["game_name"])
        if state["activity_known"] and state["activity"]:
            lines.append(state["activity"])
        elif not state["activity_known"]:
            lines.append("游戏内活动暂不可读")
    return "｜".join(lines)


def notification(previous: dict, current: dict) -> str:
    return f"Steam 状态变化 · {current['name']}\n之前：{describe(previous)}\n现在：{describe(current)}"


class PresenceStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as database:
            database.execute("""CREATE TABLE IF NOT EXISTS states (
                steam_id TEXT PRIMARY KEY, snapshot TEXT NOT NULL,
                delivery_status TEXT NOT NULL, delivery_error TEXT NOT NULL DEFAULT '')""")
            database.commit()

    def get(self, steam_id: str) -> dict | None:
        with closing(sqlite3.connect(self.path)) as database:
            row = database.execute("SELECT snapshot FROM states WHERE steam_id=?", (steam_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, state: dict, status: str):
        with closing(sqlite3.connect(self.path)) as database:
            database.execute("INSERT INTO states(steam_id,snapshot,delivery_status) VALUES(?,?,?) "
                             "ON CONFLICT(steam_id) DO UPDATE SET snapshot=excluded.snapshot, "
                             "delivery_status=excluded.delivery_status,delivery_error=''",
                             (state["steam_id"], json.dumps(state, ensure_ascii=False), status))
            database.commit()

    def delivered(self, steam_id: str, status: str, error: str = ""):
        with closing(sqlite3.connect(self.path)) as database:
            database.execute("UPDATE states SET delivery_status=?,delivery_error=? WHERE steam_id=?",
                             (status, error, steam_id))
            database.commit()
