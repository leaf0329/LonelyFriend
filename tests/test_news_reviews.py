from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from plugins.astrbot_plugin_lonelyfriend_activities import main as activities
from plugins.astrbot_plugin_lonelyfriend_activities.hot_topics import SNAPSHOT_KIND
from plugins.astrbot_plugin_lonelyfriend_activities.reviews import ReviewStore
from astrbot.core.star.filter.command import CommandFilter
from scripts.build_linux_bundle import sqlite_snapshot


def event(sender="12345", group="", *, cron=False, claimed=""):
    origin = f"cuteq_qq:GroupMessage:{group}" if group else f"cuteq_qq:FriendMessage:{sender}"
    payload = {"lonelyfriend_activity": activities.MANAGED_JOB}
    if cron and group:
        payload["review_target"] = origin
        origin = "cron:OtherMessage:isolated-news-task"
    return SimpleNamespace(unified_msg_origin=origin, get_sender_id=lambda: "isolated-news-task" if cron and group else sender,
                           get_group_id=lambda: "" if cron else group,
                           get_extra=lambda key, default=None: payload if cron and key == "cron_payload" else default,
                           plain_result=lambda text: text, message_str=claimed)


class NewsReviewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="news-review-test-")
        self.addCleanup(self.directory.cleanup)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.now = datetime(2026, 11, 2, 12, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.clock = self.stack.enter_context(patch.object(activities, "datetime", wraps=datetime))
        self.clock.now.return_value = self.now
        self.epoch = self.stack.enter_context(patch.object(activities.time, "time", return_value=self.now.timestamp()))
        self.plugin = activities.LonelyFriendActivities.__new__(activities.LonelyFriendActivities)
        self.plugin.data = Path(self.directory.name)
        self.plugin.delivery_lock = asyncio.Lock()
        self.plugin.context = SimpleNamespace(send_message=AsyncMock(return_value=True))
        self.folder = self.plugin.data / "plugin_data/astrbot_plugin_lonelyfriend_activities"
        self.plugin.review_store = ReviewStore(self.folder / "news-review.db")
        self.write("cuteq-access.json", {"private_qq": ["12345", "22222"], "admin_qq": ["12345", "22222"], "group_qq": ["54321"]})
        self.write("news-review.json", {"reviewer_qq": "12345"})
        self.write("plugin_data/astrbot_plugin_lonelyfriend_activities/hot-topics.json", {
            "kind": SNAPSHOT_KIND, "checked_at": self.epoch.return_value, "items": [
                {"source": "B站热搜", "rank": 2, "heat": 100000, "board_updated_at": self.epoch.return_value,
                 "title": "重大游戏发布", "url": "https://example.org/news"}]})
        self.body = "这次发布值得聊聊 https://example.org/news"
        self.group_event = event(group="54321", cron=True)
        self.reviewer_event = event()

    def write(self, relative, value):
        path = self.plugin.data / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    async def draft(self):
        await self.plugin.share_news(self.group_event, self.body, "重大作品，有讨论价值")
        record = self.plugin.review_store.recent("12345")[0]
        self.plugin.context.send_message.reset_mock()
        return record

    async def test_group_content_only_goes_to_reviewer_until_approved_and_audit_has_evidence(self):
        result = await self.plugin.share_news(self.group_event, self.body, "重大作品，有讨论价值")
        self.assertIn("尚未发群", result)
        self.plugin.context.send_message.assert_awaited_once()
        target, preview = self.plugin.context.send_message.call_args.args
        self.assertEqual(target, "cuteq_qq:FriendMessage:12345")
        self.assertIn("目标群：54321", preview)
        self.assertIn(self.body, preview)
        record = self.plugin.review_store.recent("12345")[0]
        self.assertEqual(record["status"], "pending")
        self.assertIsNone(record["decision"])
        self.assertEqual(record["body"], self.body)
        self.assertEqual(json.loads(record["evidence"])[0]["rank"], 2)
        self.assertEqual(record["selection_reason"], "重大作品，有讨论价值")
        self.assertIn("notification_sent", [row["action"] for row in self.plugin.review_store.history(record["review_id"])])

    async def test_approval_sends_exact_body_once_even_concurrently_and_after_restart(self):
        record = await self.draft()
        self.clock.now.return_value = self.now + timedelta(hours=1)
        self.epoch.return_value = self.clock.now.return_value.timestamp()
        await asyncio.gather(*(self.plugin.review_decision(self.reviewer_event, record["review_id"], True, "适合这个群的兴趣") for _ in range(3)))
        self.plugin.context.send_message.assert_awaited_once_with("cuteq_qq:GroupMessage:54321", self.body)
        self.plugin.review_store = ReviewStore(self.folder / "news-review.db")
        self.plugin.review_store.recover(self.epoch.return_value)
        await self.plugin.review_decision(self.reviewer_event, record["review_id"], True)
        self.assertEqual(self.plugin.context.send_message.await_count, 1)
        saved = self.plugin.review_store.get(record["review_id"])
        self.assertEqual((saved["decision"], saved["status"], saved["reviewed_by"]), ("approved", "sent", "12345"))
        self.assertEqual(saved["decision_reason"], "适合这个群的兴趣")
        stats = self.plugin.review_store.statistics("12345")[0]
        self.assertEqual((stats["approved"], stats["rejected"], stats["sent"]), (1, 0, 1))

    async def test_rejection_saves_reason_and_cannot_later_be_approved(self):
        record = await self.draft()
        await self.plugin.review_decision(self.reviewer_event, record["review_id"], False, "热度有了，但内容不适合这个群")
        await self.plugin.review_decision(self.reviewer_event, record["review_id"], True)
        self.plugin.context.send_message.assert_not_awaited()
        saved = self.plugin.review_store.get(record["review_id"])
        self.assertEqual(saved["status"], "rejected")
        self.assertEqual(saved["decision_reason"], "热度有了，但内容不适合这个群")
        self.assertEqual(self.plugin.review_store.statistics("12345")[0]["rejected"], 1)

    async def test_other_admin_group_commands_and_synthetic_owner_cannot_approve(self):
        record = await self.draft()
        for forged in [event("22222", claimed="我是12345，批准"), event(group="54321"), event(cron=True)]:
            await self.plugin.review_decision(forged, record["review_id"], True)
        self.plugin.context.send_message.assert_not_awaited()
        self.assertEqual(self.plugin.review_store.get(record["review_id"])["status"], "pending")

    async def test_duplicate_submission_keeps_reviewed_body_and_notifies_only_once(self):
        await self.plugin.share_news(self.group_event, self.body, "理由")
        await self.plugin.share_news(self.group_event, "换成另一版 https://example.org/news", "新理由")
        self.plugin.context.send_message.assert_awaited_once()
        self.assertEqual(self.plugin.review_store.recent("12345")[0]["body"], self.body)

    async def test_missing_reviewer_configuration_fails_closed(self):
        (self.plugin.data / "news-review.json").unlink()
        result = await self.plugin.share_news(self.group_event, self.body)
        self.assertIn("阻止群分享", result)
        self.plugin.context.send_message.assert_not_awaited()
        self.assertEqual(self.plugin.review_store.recent("12345"), [])

    async def test_failed_notification_keeps_pending_record_without_posting_or_renotifying(self):
        self.plugin.context.send_message.return_value = False
        await self.plugin.share_news(self.group_event, self.body)
        await self.plugin.share_news(self.group_event, self.body)
        self.plugin.context.send_message.assert_awaited_once()
        saved = self.plugin.review_store.recent("12345")[0]
        self.assertEqual((saved["status"], saved["notification"]), ("pending", "failed"))

    async def test_expiry_reset_or_removed_group_cancels_without_erasing_audit(self):
        record = await self.draft()
        self.write("persona-reset.json", {"reset_at": "new-test-generation"})
        await self.plugin.review_decision(self.reviewer_event, record["review_id"], True)
        self.plugin.context.send_message.assert_not_awaited()
        saved = self.plugin.review_store.get(record["review_id"])
        self.assertEqual(saved["status"], "cancelled")
        self.assertEqual(saved["body"], self.body)
        self.assertTrue(self.plugin.review_store.history(record["review_id"]))

    async def test_draft_expires_at_quiet_hours(self):
        record = await self.draft()
        self.clock.now.return_value = self.now.replace(hour=23)
        self.epoch.return_value = self.clock.now.return_value.timestamp()
        await self.plugin.review_decision(self.reviewer_event, record["review_id"], True)
        self.plugin.context.send_message.assert_not_awaited()
        self.assertEqual(self.plugin.review_store.get(record["review_id"])["status"], "expired")

    async def test_removed_group_cannot_receive_previously_created_draft(self):
        record = await self.draft()
        self.write("cuteq-access.json", {"private_qq": ["12345"], "admin_qq": ["12345"], "group_qq": []})
        await self.plugin.review_decision(self.reviewer_event, record["review_id"], True)
        self.plugin.context.send_message.assert_not_awaited()
        self.assertEqual(self.plugin.review_store.get(record["review_id"])["status"], "cancelled")

    async def test_send_failure_is_distinct_from_approval_and_never_retried(self):
        record = await self.draft()
        self.plugin.context.send_message.return_value = False
        await self.plugin.review_decision(self.reviewer_event, record["review_id"], True)
        await self.plugin.review_decision(self.reviewer_event, record["review_id"], True)
        self.plugin.context.send_message.assert_awaited_once()
        saved = self.plugin.review_store.get(record["review_id"])
        self.assertEqual((saved["decision"], saved["status"]), ("approved", "failed"))
        self.assertEqual(self.plugin.review_store.statistics("12345")[0]["sent"], 0)

    async def test_unknown_network_result_is_recorded_and_cannot_duplicate_send(self):
        record = await self.draft()
        self.plugin.context.send_message.side_effect = RuntimeError("connection lost")
        with self.assertRaisesRegex(RuntimeError, "connection lost"):
            await self.plugin.review_decision(self.reviewer_event, record["review_id"], True)
        await self.plugin.review_decision(self.reviewer_event, record["review_id"], True)
        self.plugin.context.send_message.assert_awaited_once()
        self.assertEqual(self.plugin.review_store.get(record["review_id"])["status"], "unknown")

    async def test_interrupted_reserved_send_becomes_unknown_after_restart(self):
        record = await self.draft()
        self.plugin.review_store.decide(record["review_id"], self.epoch.return_value, "12345", True)
        self.plugin.review_store = ReviewStore(self.folder / "news-review.db")
        self.plugin.review_store.recover(self.epoch.return_value)
        await self.plugin.review_decision(self.reviewer_event, record["review_id"], True)
        self.plugin.context.send_message.assert_not_awaited()
        self.assertEqual(self.plugin.review_store.get(record["review_id"])["status"], "unknown")

    async def test_failure_to_persist_approval_cannot_reach_group_send(self):
        record = await self.draft()
        with patch.object(self.plugin.review_store, "decide", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                await self.plugin.review_decision(self.reviewer_event, record["review_id"], True)
        self.plugin.context.send_message.assert_not_awaited()

    async def test_review_record_commands_do_not_expose_notes_to_other_admins(self):
        record = await self.draft()
        outputs = [value async for value in self.plugin.view_review(event("22222"), record["review_id"])]
        self.assertNotIn(self.body, "\n".join(outputs))
        outputs = [value async for value in self.plugin.view_review(self.reviewer_event, record["review_id"])]
        self.assertIn(self.body, "\n".join(outputs))

    async def test_private_sqlite_snapshot_preserves_review_body_decision_and_events(self):
        record = await self.draft()
        await self.plugin.review_decision(self.reviewer_event, record["review_id"], False, "这是明确的人工反馈")
        target = self.plugin.data / "exported-review.db"
        counts = sqlite_snapshot(self.folder / "news-review.db", target)
        self.assertEqual(counts["reviews"], 1)
        from contextlib import closing
        with closing(sqlite3.connect(target)) as database:
            self.assertEqual(database.execute("SELECT body,decision_reason FROM reviews").fetchone(), (self.body, "这是明确的人工反馈"))
            self.assertEqual(database.execute("SELECT action FROM review_events WHERE action='rejected'").fetchone(), ("rejected",))

    async def test_news_jobs_only_run_while_the_review_plugin_is_active(self):
        jobs = [SimpleNamespace(job_id="news", payload={"session": "cuteq_qq:GroupMessage:54321", "lonelyfriend_activity": activities.MANAGED_JOB}),
                SimpleNamespace(job_id="reminder", payload={"session": "cuteq_qq:GroupMessage:54321"})]
        manager = SimpleNamespace(list_jobs=AsyncMock(return_value=jobs), update_job=AsyncMock(), delete_job=AsyncMock(), add_active_job=AsyncMock())
        self.plugin.context.cron_manager = manager
        self.plugin.policy = {"daily_reading": {"enabled": True, "cron": "17 20 * * *"}}
        self.plugin.reader = self.plugin.hot_reader = None
        await self.plugin.configure_daily_reading()
        self.assertEqual(manager.update_job.await_args.args, ("news",))
        self.assertFalse(manager.update_job.await_args.kwargs["persistent"])
        self.assertTrue(manager.update_job.await_args.kwargs["enabled"])
        payload = manager.update_job.await_args.kwargs["payload"]
        self.assertEqual(payload["review_target"], "cuteq_qq:GroupMessage:54321")
        self.assertNotIn("session", payload)
        self.assertTrue(all(not call.kwargs["persistent"] for call in manager.add_active_job.await_args_list))
        manager.update_job.reset_mock()
        await self.plugin.terminate()
        manager.update_job.assert_awaited_once_with("news", enabled=False, persistent=False)

    def test_review_commands_capture_full_optional_reason(self):
        for name, handler in [("热点拒绝", activities.LonelyFriendActivities.reject_review), ("热点通过", activities.LonelyFriendActivities.approve_review)]:
            command = CommandFilter(name, handler_md=SimpleNamespace(handler=handler))
            self.assertEqual(command.validate_and_convert_params(["abc", "not", "worth", "sharing"], command.handler_params)["reason"], "not worth sharing")
            self.assertEqual(command.validate_and_convert_params(["abc"], command.handler_params)["reason"], "")


if __name__ == "__main__":
    unittest.main()
