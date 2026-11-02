from __future__ import annotations

import json
import os
import secrets
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path


class ReviewStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as database:
            database.executescript("""
                CREATE TABLE IF NOT EXISTS reviews (
                    review_id TEXT PRIMARY KEY, origin TEXT NOT NULL, day TEXT NOT NULL,
                    reviewer_qq TEXT NOT NULL, generation TEXT NOT NULL,
                    body TEXT NOT NULL, selection_reason TEXT NOT NULL,
                    source TEXT NOT NULL, title TEXT NOT NULL, evidence TEXT NOT NULL,
                    created_at REAL NOT NULL, expires_at REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', notification TEXT NOT NULL DEFAULT 'not_attempted',
                    decision TEXT, decision_reason TEXT, reviewed_by TEXT, reviewed_at REAL,
                    sent_at REAL, delivery_error TEXT,
                    UNIQUE(origin, day)
                );
                CREATE TABLE IF NOT EXISTS review_events (
                    event_id INTEGER PRIMARY KEY, review_id TEXT NOT NULL REFERENCES reviews(review_id),
                    happened_at REAL NOT NULL, action TEXT NOT NULL, actor_qq TEXT, note TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS review_events_request ON review_events(review_id, event_id);
            """)
        os.chmod(path, 0o600)

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path)) as database:
            database.row_factory = sqlite3.Row
            database.execute("PRAGMA foreign_keys=ON")
            with database:
                yield database

    @staticmethod
    def event(database, review_id, now, action, actor="", note=""):
        database.execute("INSERT INTO review_events(review_id,happened_at,action,actor_qq,note) VALUES (?,?,?,?,?)",
                         (review_id, now, action, actor, note))

    def recover(self, now: float):
        with self.connect() as database:
            for row in database.execute("SELECT review_id FROM reviews WHERE status='sending'").fetchall():
                database.execute("UPDATE reviews SET status='unknown',delivery_error=? WHERE review_id=?",
                                 ("服务重启前的发送结果未确认，不自动重发", row["review_id"]))
                self.event(database, row["review_id"], now, "delivery_unknown", note="服务重启，不自动重发")
            for row in database.execute("SELECT review_id FROM reviews WHERE notification='attempted'").fetchall():
                database.execute("UPDATE reviews SET notification='unknown' WHERE review_id=?", (row["review_id"],))
                self.event(database, row["review_id"], now, "notification_unknown", note="通知结果未确认，可在待审列表查看")

    def create(self, *, origin, day, reviewer, generation, body, selection_reason, evidence, now, expires_at):
        review_id = secrets.token_hex(5)
        with self.connect() as database:
            result = database.execute("""INSERT INTO reviews
                (review_id,origin,day,reviewer_qq,generation,body,selection_reason,source,title,evidence,created_at,expires_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(origin,day) DO NOTHING""",
                (review_id, origin, day, reviewer, generation, body, selection_reason,
                 evidence[0]["source"], evidence[0]["title"], json.dumps(evidence, ensure_ascii=False), now, expires_at))
            created = result.rowcount == 1
            row = database.execute("SELECT * FROM reviews WHERE origin=? AND day=?", (origin, day)).fetchone()
            if created:
                self.event(database, review_id, now, "created", note=selection_reason)
            return dict(row), created

    def notification(self, review_id, now, status, note=""):
        with self.connect() as database:
            database.execute("UPDATE reviews SET notification=? WHERE review_id=?", (status, review_id))
            self.event(database, review_id, now, "notification_" + status, note=note)

    def get(self, review_id):
        with self.connect() as database:
            row = database.execute("SELECT * FROM reviews WHERE review_id=?", (review_id,)).fetchone()
            return dict(row) if row is not None else None

    def expire(self, now, generation, reviewer):
        with self.connect() as database:
            rows = database.execute("SELECT * FROM reviews WHERE status='pending'").fetchall()
            for row in rows:
                if now >= row["expires_at"]:
                    status, note = "expired", "超过当天审核截止时间"
                elif row["generation"] != generation or row["reviewer_qq"] != reviewer:
                    status, note = "cancelled", "角色测试状态或审核账号已变更"
                else:
                    continue
                changed = database.execute("UPDATE reviews SET status=? WHERE review_id=? AND status='pending'",
                                           (status, row["review_id"])).rowcount
                if changed:
                    self.event(database, row["review_id"], now, status, note=note)

    def cancel(self, review_id, now, note):
        with self.connect() as database:
            changed = database.execute("UPDATE reviews SET status='cancelled' WHERE review_id=? AND status='pending'",
                                       (review_id,)).rowcount
            if changed:
                self.event(database, review_id, now, "cancelled", note=note)

    def decide(self, review_id, now, reviewer, approve, reason="") -> bool:
        with self.connect() as database:
            result = database.execute("""UPDATE reviews SET status=?,decision=?,decision_reason=?,reviewed_by=?,reviewed_at=?
                WHERE review_id=? AND status='pending' AND reviewer_qq=?""",
                ("sending" if approve else "rejected", "approved" if approve else "rejected",
                 reason, reviewer, now, review_id, reviewer))
            if result.rowcount != 1:
                return False
            self.event(database, review_id, now, "approved" if approve else "rejected", reviewer, reason)
            if approve:
                self.event(database, review_id, now, "delivery_reserved", reviewer)
            return True

    def finish(self, review_id, now, status, error=""):
        with self.connect() as database:
            changed = database.execute("""UPDATE reviews SET status=?,sent_at=?,delivery_error=?
                WHERE review_id=? AND status IN ('sending','unknown')""",
                (status, now if status == "sent" else None, error, review_id)).rowcount
            if changed != 1:
                raise RuntimeError("The delivery result does not match a reserved review")
            self.event(database, review_id, now, "delivery_" + status, note=error)

    def recent(self, reviewer, pending=False, limit=5):
        with self.connect() as database:
            return [dict(row) for row in database.execute(
                "SELECT * FROM reviews WHERE reviewer_qq=?" + (" AND status='pending'" if pending else "") +
                " ORDER BY created_at DESC LIMIT ?", (reviewer, limit))]

    def history(self, review_id):
        with self.connect() as database:
            return [dict(row) for row in database.execute(
                "SELECT * FROM review_events WHERE review_id=? ORDER BY event_id", (review_id,))]

    def statistics(self, reviewer):
        with self.connect() as database:
            return [dict(row) for row in database.execute("""SELECT source,COUNT(*) AS total,
                COUNT(CASE WHEN decision='approved' THEN 1 END) AS approved,
                COUNT(CASE WHEN decision='rejected' THEN 1 END) AS rejected,
                SUM(status='sent') AS sent,SUM(status='pending') AS pending
                FROM reviews WHERE reviewer_qq=? GROUP BY source ORDER BY source""", (reviewer,))]
