from __future__ import annotations

import tempfile
import unittest
import uuid
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

from kiwoom_monitor.application.journal_enrichment import JournalEnrichmentService
from kiwoom_monitor.application.trade_journal_summary import TradeReview
from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository
from kiwoom_monitor.infrastructure.persistence.journal_backup import JournalBackupService
from kiwoom_monitor.infrastructure.persistence.journal_schema import JOURNAL_SCHEMA_VERSION
from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope


class JournalEnrichmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "journal.sqlite3"
        self.repository = JournalRepository(self.path)
        self.service = JournalEnrichmentService(self.repository, "test-owner")
        self.now = datetime(2026, 9, 13, 20, 5)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def register(self, kind: str = "minute_bars"):
        return self.service.register(
            "stock_day", "005930:2026-09-12", kind,
            {"stock_code": "005930", "trade_date": "2026-09-12"}, now=self.now,
        )

    def test_same_target_input_and_policy_reuses_one_task(self) -> None:
        first = self.register()
        second = self.register()

        self.assertEqual(first.task_id, second.task_id)
        self.assertEqual(1, len(self.repository.list_enrichment_tasks()))

    def test_same_enrichment_target_is_separate_by_account(self) -> None:
        real = AccountScope("kiwoom", AccountEnvironment.REAL, str(uuid.uuid4()))
        mock = AccountScope("kiwoom", AccountEnvironment.MOCK, str(uuid.uuid4()))
        inputs = {"stock_code": "005930", "trade_date": "2026-09-12"}
        first = self.service.register(
            "stock_day", "005930:2026-09-12", "minute_bars", inputs,
            now=self.now, account_scope=real,
        )
        second = self.service.register(
            "stock_day", "005930:2026-09-12", "minute_bars", inputs,
            now=self.now, account_scope=mock,
        )

        self.assertNotEqual(first.task_id, second.task_id)
        self.assertEqual((first,), self.repository.list_enrichment_tasks(account_scope=real))
        self.assertEqual((second,), self.repository.list_enrichment_tasks(account_scope=mock))

    def test_result_is_complete_only_after_explicit_storage_confirmation(self) -> None:
        task = self.register()

        self.assertTrue(self.service.start(task.task_id, self.now))
        running = self.repository.load_enrichment_task(task.task_id)
        self.assertEqual("running", running.state)
        self.assertEqual(1, running.attempts)

        self.assertTrue(self.service.complete(task.task_id, {"bar_count": 381}, self.now))
        completed = self.repository.load_enrichment_task(task.task_id)
        self.assertEqual("complete", completed.state)
        self.assertEqual({"bar_count": 381}, completed.result)
        self.assertFalse(self.service.start(task.task_id, self.now + timedelta(days=1)))

    def test_partial_cost_is_not_treated_as_zero_cost_complete(self) -> None:
        task = self.register("costs")
        self.service.start(task.task_id, self.now)

        self.service.partial(task.task_id, {"cost_count": 0}, "체결 비용 정산 대기", self.now)
        waiting = self.repository.load_enrichment_task(task.task_id)

        self.assertEqual("partial", waiting.state)
        self.assertEqual(self.now + timedelta(minutes=30), waiting.next_retry_at)
        self.assertFalse(self.service.start(task.task_id, self.now + timedelta(minutes=29)))
        self.assertTrue(self.service.start(task.task_id, self.now + timedelta(minutes=30)))
        self.assertEqual(
            {"cost_count": 0},
            self.repository.load_enrichment_task(task.task_id).result,
        )

    def test_failures_back_off_and_stop_after_bounded_attempts(self) -> None:
        task = self.register()
        for attempt in range(1, 4):
            start_at = self.now + timedelta(hours=attempt)
            self.assertTrue(self.service.start(task.task_id, start_at, force=True))
            self.assertTrue(self.service.fail(task.task_id, f"failure-{attempt}", start_at))

        failed = self.repository.load_enrichment_task(task.task_id)
        self.assertEqual(3, failed.attempts)
        self.assertEqual("unavailable", failed.state)
        self.assertFalse(self.service.start(task.task_id, self.now + timedelta(days=1), force=True))

    def test_restart_recovers_running_without_touching_user_review(self) -> None:
        task = self.register()
        review = TradeReview("group-1", "진입 이유", "사용자 복기", "태그", "좋음", "복기 완료")
        self.repository.save_review(review, self.now)
        self.service.start(task.task_id, self.now)

        recovered = JournalEnrichmentService(
            JournalRepository(self.path), "new-owner",
        ).recover_interrupted(self.now + timedelta(minutes=1))

        loaded = self.repository.load_enrichment_task(task.task_id)
        self.assertEqual(1, recovered)
        self.assertEqual("retryable_failed", loaded.state)
        self.assertEqual(review, self.repository.load_review("group-1"))

    def test_full_database_backup_keeps_enrichment_ledger(self) -> None:
        task = self.register()
        self.service.start(task.task_id, self.now)
        self.service.complete(task.task_id, {"bar_count": 10}, self.now)
        exported = Path(self.directory.name) / "export.sqlite3"
        restored = Path(self.directory.name) / "restored.sqlite3"

        JournalBackupService(self.path).export_to(exported)
        JournalBackupService(restored).import_from(exported)

        loaded = JournalRepository(restored).load_enrichment_task(task.task_id)
        self.assertEqual("complete", loaded.state)
        self.assertEqual({"bar_count": 10}, loaded.result)

    def test_restore_rejects_newer_schema_without_replacing_current_database(self) -> None:
        current_task = self.register()
        newer = Path(self.directory.name) / "newer.sqlite3"
        with closing(sqlite3.connect(newer)) as connection:
            connection.execute("CREATE TABLE trade_fills (id TEXT)")
            connection.execute("CREATE TABLE journal_minute_bars (id TEXT)")
            connection.execute("CREATE TABLE trade_reviews (id TEXT)")
            connection.execute(
                "CREATE TABLE journal_schema_migrations (version INTEGER PRIMARY KEY)"
            )
            connection.execute(
                "INSERT INTO journal_schema_migrations(version) VALUES (?)",
                (JOURNAL_SCHEMA_VERSION + 1,),
            )
            connection.commit()

        with self.assertRaisesRegex(ValueError, "더 최신 매매일지 DB"):
            JournalBackupService(self.path).import_from(newer)

        self.assertEqual(
            current_task,
            JournalRepository(self.path).load_enrichment_task(current_task.task_id),
        )


if __name__ == "__main__":
    unittest.main()
