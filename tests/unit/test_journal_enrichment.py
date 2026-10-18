from __future__ import annotations

import tempfile
import unittest
import uuid
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from kiwoom_monitor.application.journal_enrichment import JournalEnrichmentService
from kiwoom_monitor.application.trade_journal_summary import TradeReview
from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository
from kiwoom_monitor.infrastructure.persistence.journal_backup import JournalBackupService
from kiwoom_monitor.infrastructure.persistence.journal_schema import JOURNAL_SCHEMA_VERSION
from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope, LEGACY_ACCOUNT_SCOPE
from kiwoom_monitor.domain.time_basis import as_kst


class JournalEnrichmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "journal.sqlite3"
        self.repository = JournalRepository(self.path)
        self.clock = Mock(side_effect=AssertionError("explicit timestamps must not read the provider"))
        self.service = JournalEnrichmentService(self.repository, "test-owner", now_provider=self.clock)
        self.now = datetime(2026, 9, 13, 20, 5)

    def tearDown(self) -> None:
        self.clock.assert_not_called()
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
            current = self.repository.load_enrichment_task(task.task_id)
            if attempt < 3:
                due = start_at + timedelta(minutes=5 * 2 ** (attempt - 1))
                self.assertEqual(due, current.next_retry_at)
                self.assertFalse(self.service.start(task.task_id, due - timedelta(microseconds=1)))
                self.assertTrue(self.service.can_start(current, due))
            else:
                self.assertIsNone(current.next_retry_at)

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

    def test_native_injected_lifecycle_clock_preserves_completion_recovery_and_failed_writes(self) -> None:
        origin = AccountScope("kiwoom", AccountEnvironment.MOCK, "11111111-1111-4111-8111-111111111111")
        canonical = AccountScope("kiwoom", AccountEnvironment.MOCK, "22222222-2222-4222-8222-222222222222")

        def stored(task_id):
            with closing(sqlite3.connect(self.path)) as connection:
                connection.row_factory = sqlite3.Row
                return dict(connection.execute("SELECT * FROM journal_enrichment_tasks WHERE task_id=?", (task_id,)).fetchone())

        for scope, effective, source in (
            (LEGACY_ACCOUNT_SCOPE, None, datetime(2030, 1, 2, 3, 4, 5)),
            (origin, canonical, datetime(2030, 1, 1, 18, 4, 5, tzinfo=timezone.utc)),
        ):
            with self.subTest(scope=scope.account_ref):
                clock = Mock(return_value=source)
                with patch("kiwoom_monitor.application.journal_enrichment.kst_now", clock):
                    # The unbound startup/offline constructor and explicit online provider share KST normalization.
                    service = JournalEnrichmentService(self.repository, "online-owner", **(
                        {} if scope == LEGACY_ACCOUNT_SCOPE else {"now_provider": clock}
                    ))

                def once(call):
                    clock.reset_mock()
                    result = call()
                    clock.assert_called_once_with()
                    return result

                with patch("kiwoom_monitor.application.journal_enrichment.kst_wall_now",
                           side_effect=AssertionError("host clock used")), \
                     patch("kiwoom_monitor.infrastructure.persistence.journal_trade_repository.kst_wall_now",
                           side_effect=AssertionError("repository host clock used")):
                    inputs = {"stock_code": "005930", "trade_date": "2026-09-12"}
                    register = lambda: service.register("stock_day", "005930:2026-09-12", "costs", inputs,
                                                        account_scope=scope, canonical_scope=effective)
                    task = once(register)
                    receipt = as_kst(source)
                    initial = stored(task.task_id)
                    self.assertEqual(receipt.isoformat(), initial["created_at"])
                    self.assertEqual(receipt.isoformat(), initial["updated_at"])
                    self.assertIsNone(initial["started_at"])
                    self.assertIsNone(initial["completed_at"])
                    self.assertEqual(scope, task.origin_scope)
                    self.assertEqual(effective or scope, task.effective_scope)

                    clock.return_value = receipt + timedelta(minutes=1)
                    self.assertTrue(once(lambda: service.start(task.task_id)))
                    self.assertEqual(clock.return_value.isoformat(), stored(task.task_id)["started_at"])
                    self.assertEqual("online-owner", stored(task.task_id)["owner"])
                    clock.return_value += timedelta(minutes=1)
                    self.assertTrue(once(lambda: service.partial(task.task_id, {"cost_count": 0}, "pending costs")))
                    waiting = self.repository.load_enrichment_task(task.task_id)
                    self.assertEqual(clock.return_value + timedelta(minutes=30), waiting.next_retry_at)
                    self.assertEqual({"cost_count": 0}, waiting.result)
                    self.assertTrue(once(lambda: service.start(task.task_id, force=True)))
                    self.assertEqual(2, self.repository.load_enrichment_task(task.task_id).attempts)
                    clock.return_value += timedelta(minutes=1)
                    self.assertTrue(once(lambda: service.complete(task.task_id, {"cost_count": 1})))
                    completed = stored(task.task_id)
                    self.assertEqual(clock.return_value.isoformat(), completed["completed_at"])
                    self.assertEqual(completed["completed_at"], completed["updated_at"])

                    clock.return_value += timedelta(days=1)
                    self.assertEqual("complete", once(register).state)
                    self.assertEqual(completed, stored(task.task_id))
                    clock.reset_mock()
                    self.assertFalse(service.can_start(self.repository.load_enrichment_task(task.task_id), force=True))
                    with self.assertRaises(ValueError):
                        service.register("stock_day", "invalid", "invalid", {})
                    clock.assert_not_called()
                    for call in (lambda: service.start(task.task_id, force=True),
                                 lambda: service.fail(task.task_id, "late failure"),
                                 lambda: service.partial(task.task_id, {}, "late partial"),
                                 lambda: service.complete(task.task_id, {}),
                                 lambda: service.unavailable(task.task_id, "late unavailable")):
                        self.assertFalse(once(call))
                        self.assertEqual(completed, stored(task.task_id))

                    interrupted = once(lambda: service.register("stock_day", "005930:2026-09-12", "daily_bars", inputs,
                                                               account_scope=scope, canonical_scope=effective))
                    self.assertTrue(once(lambda: service.start(interrupted.task_id)))
                    before_recovery = stored(interrupted.task_id)
                    review = TradeReview("group-clock", "entry", "user review", "tag", "good", "done")
                    self.repository.save_review(review, self.now)
                    restarted = JournalEnrichmentService(JournalRepository(self.path), "restarted-owner", now_provider=clock)
                    clock.return_value += timedelta(minutes=1)
                    self.assertEqual(1, once(restarted.recover_interrupted))
                    recovered = stored(interrupted.task_id)
                    self.assertEqual(("retryable_failed", "", 1), (recovered["state"], recovered["owner"], recovered["attempts"]))
                    self.assertEqual(clock.return_value.isoformat(), recovered["next_retry_at"])
                    self.assertEqual(recovered["next_retry_at"], recovered["updated_at"])
                    self.assertEqual(before_recovery["created_at"], recovered["created_at"])
                    self.assertEqual(before_recovery["started_at"], recovered["started_at"])
                    self.assertEqual(review, self.repository.load_review("group-clock"))
                    self.assertTrue(once(lambda: restarted.start(interrupted.task_id)))
                    running = stored(interrupted.task_id)
                    with closing(sqlite3.connect(self.path)) as connection:
                        connection.execute("CREATE TRIGGER reject_task_update BEFORE UPDATE ON journal_enrichment_tasks "
                                           "BEGIN SELECT RAISE(ABORT, 'task write rejected'); END")
                    with self.assertRaisesRegex(sqlite3.IntegrityError, "task write rejected"):
                        once(lambda: restarted.fail(interrupted.task_id, "must roll back"))
                    clock.assert_called_once_with()
                    self.assertEqual(running, stored(interrupted.task_id))
                    with closing(sqlite3.connect(self.path)) as connection:
                        connection.execute("DROP TRIGGER reject_task_update")
                    clock.return_value += timedelta(minutes=1)
                    self.assertTrue(once(lambda: restarted.unavailable(interrupted.task_id, "unsupported", {"available": False})))
                    unavailable = stored(interrupted.task_id)
                    self.assertEqual("unavailable", unavailable["state"])
                    self.assertEqual(clock.return_value.isoformat(), unavailable["completed_at"])
                    self.assertEqual({"available": False}, self.repository.load_enrichment_task(interrupted.task_id).result)

    def test_retry_boundaries_compare_naive_kst_and_offsets_without_rewriting_saved_times(self) -> None:
        naive = self.now
        for source in (naive, as_kst(naive), as_kst(naive).astimezone(timezone.utc)):
            with self.subTest(source=source.isoformat()):
                task = self.service.register("stock_day", "mixed-retry", "minute_bars", {"source": source.isoformat()}, now=source)
                self.assertTrue(self.service.start(task.task_id, source))
                self.assertTrue(self.service.fail(task.task_id, "retry", source))
                waiting = self.repository.load_enrichment_task(task.task_id)
                due = source + timedelta(minutes=5)
                self.assertEqual(due, waiting.next_retry_at)
                with closing(sqlite3.connect(self.path)) as connection:
                    before = connection.execute("SELECT created_at,updated_at,next_retry_at FROM journal_enrichment_tasks WHERE task_id=?",
                                                (task.task_id,)).fetchone()
                self.assertEqual((source.isoformat(), source.isoformat(), due.isoformat()), before)
                for current in (as_kst(due).replace(tzinfo=None), as_kst(due), as_kst(due).astimezone(timezone.utc)):
                    with self.subTest(current=current.isoformat()):
                        self.assertFalse(self.service.can_start(waiting, current - timedelta(microseconds=1)))
                        self.assertTrue(self.service.can_start(waiting, current))
                with closing(sqlite3.connect(self.path)) as connection:
                    self.assertEqual(before, connection.execute(
                        "SELECT created_at,updated_at,next_retry_at FROM journal_enrichment_tasks WHERE task_id=?", (task.task_id,)).fetchone())
                clock = Mock(return_value=as_kst(due).astimezone(timezone.utc) - timedelta(microseconds=1))
                injected = JournalEnrichmentService(self.repository, "mixed-owner", now_provider=clock)
                self.assertFalse(injected.can_start(waiting))
                clock.assert_called_once_with()
                clock.return_value += timedelta(microseconds=1)
                clock.reset_mock()
                self.assertTrue(injected.start(task.task_id))
                clock.assert_called_once_with()
                clock.return_value += timedelta(minutes=1)
                clock.reset_mock()
                self.assertTrue(injected.fail(task.task_id, "second retry"))
                clock.assert_called_once_with()
                self.assertEqual(as_kst(clock.return_value) + timedelta(minutes=10),
                                 self.repository.load_enrichment_task(task.task_id).next_retry_at)

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
