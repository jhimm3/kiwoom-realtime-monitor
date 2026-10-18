import os
import sqlite3
import tempfile
import unittest
import uuid
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from threading import Event, get_ident

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QComboBox, QMainWindow
from PySide6.QtCore import QTimer, QCoreApplication, QEvent
from qt_settings_test_support import wait_until

from kiwoom_monitor.journal_process import JournalWindow
from kiwoom_monitor.application.journal_enrichment import JournalEnrichmentService
from kiwoom_monitor.application.trade_cost_service import DailyTradeCost
from kiwoom_monitor.application.trade_history_service import TradeFill
from kiwoom_monitor.domain.order_contract import AccountScope, AccountEnvironment, LEGACY_ACCOUNT_SCOPE
from kiwoom_monitor.domain.time_basis import as_kst
from kiwoom_monitor.infrastructure.kiwoom_rest.account_query import AccountQueryContext
from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository
from kiwoom_monitor.presentation.journal_workers import HistoryWorker


class JournalSelectedAccountTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.scope = AccountScope("kiwoom", AccountEnvironment.MOCK, str(uuid.uuid4()))
        self.other = AccountScope("kiwoom", AccountEnvironment.MOCK, str(uuid.uuid4()))
        self.context = AccountQueryContext(
            self.scope, "profile-a", 1, "nas", display_label="단타 모의",
        )

    def owner(self, selected=None):
        return SimpleNamespace(_history_expected_scope=self.scope,
            _query_client=SimpleNamespace(server_now=Mock(return_value=datetime(2026, 9, 15, 10))),
            _selected_account_scope=lambda: selected or self.scope,
            _repo=SimpleNamespace(upsert_history_sync=Mock()), _history_enrichment_failed=Mock(),
            _journal_settings=SimpleNamespace(setValue=Mock()), _status=SimpleNamespace(setText=Mock()),
            reload_history=Mock())

    def test_late_result_after_account_change_never_writes(self):
        owner = self.owner(self.other)
        JournalWindow._history_received(owner, ((), (), "", self.context), date(2026, 9, 15), date(2026, 9, 15))
        owner._repo.upsert_history_sync.assert_not_called()
        owner._history_enrichment_failed.assert_called_once()
        owner._query_client.server_now.assert_not_called()

    def test_empty_completed_query_retains_explicit_account_scope(self):
        owner = self.owner()
        JournalWindow._history_received(owner, ((), (), "", self.context), date(2026, 9, 15), date(2026, 9, 15))
        owner._repo.upsert_history_sync.assert_called_once_with(
            (), (), now=as_kst(datetime(2026, 9, 15, 10)), account_scope=self.scope,
        )
        owner._query_client.server_now.assert_called_once_with()

    def test_rows_cannot_override_completed_legacy_context(self):
        owner = self.owner(LEGACY_ACCOUNT_SCOPE); owner._history_expected_scope = LEGACY_ACCOUNT_SCOPE
        legacy = AccountQueryContext(LEGACY_ACCOUNT_SCOPE, "legacy-unverified", 0, "legacy")
        JournalWindow._history_received(owner, ((SimpleNamespace(origin_scope=self.scope),), (), "", legacy),
            date(2026, 9, 15), date(2026, 9, 15))
        owner._repo.upsert_history_sync.assert_not_called()
        owner._history_enrichment_failed.assert_called_once()
        owner._query_client.server_now.assert_not_called()

    def test_native_history_receipt_commits_before_refresh_and_finishes_tasks_at_same_time(self):
        real = AccountScope("kiwoom", AccountEnvironment.REAL, "11111111-1111-4111-8111-111111111111")
        cases = (("actual_cost", real, ""), ("pending_cost", real, ""),
                 ("failed_cost", real, "cost failure: " + "x" * 1100), ("estimated_cost", self.scope, ""))
        for scenario, scope, cost_error in cases:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "journal.sqlite3"
                repository = JournalRepository(path)
                fill = TradeFill("history-1", "005930", "fixture", "매수", datetime(2026, 9, 15, 9, 10, 11),
                                 2, 1000, origin_scope=scope)
                cost = DailyTradeCost(date(2026, 9, 15), date(2026, 9, 17), "005930", "매수",
                                     2000, 1997, 1, 2, 3, origin_scope=scope)
                costs = (cost,) if scenario == "actual_cost" else ()
                context = AccountQueryContext(scope, "fixture", 1, "nas")
                source = datetime(2030, 1, 2, 11, 6, 7, tzinfo=timezone.utc)
                received_at = as_kst(source)
                clock = Mock(return_value=source)
                implicit_clock = Mock(side_effect=AssertionError("task transition must reuse history receipt"))
                enrichment = JournalEnrichmentService(repository, "history-owner", now_provider=implicit_clock)
                task_ids = {}
                for kind in ("fills", "costs"):
                    task = enrichment.register("date_range", "2026-09-14:2026-09-15", kind,
                                               {"scope": scope.account_ref}, now=received_at - timedelta(minutes=1), account_scope=scope)
                    enrichment.start(task.task_id, received_at - timedelta(minutes=1))
                    task_ids[kind] = task.task_id
                owner = self.owner(scope)
                owner._history_expected_scope = scope
                owner._query_client.server_now = clock
                owner._repo = repository
                owner._journal_enrichment = enrichment
                owner._history_enrichment_task_ids = task_ids
                events, snapshots = [], []
                original_connect = sqlite3.connect

                def connection(*args, **kwargs):
                    current = original_connect(*args, **kwargs)
                    current.set_trace_callback(lambda sql: events.append(sql) if sql in {"COMMIT", "ROLLBACK"} else None)
                    return current

                def refresh(stage):
                    # An independent reader must see committed rows before accounts/UI refresh runs.
                    snapshots.append((stage,
                        repository.load_fills(datetime(2026, 9, 14), datetime(2026, 9, 16), scope),
                        repository.load_trade_costs(datetime(2026, 9, 14), datetime(2026, 9, 16), scope),
                        tuple(repository.load_enrichment_task(task_ids[kind]).state for kind in ("fills", "costs"))))
                    events.append(stage)

                owner._refresh_account_choices = Mock(side_effect=lambda _scope: refresh("accounts"))
                owner.reload_history = Mock(side_effect=lambda: refresh("reload"))
                with patch("kiwoom_monitor.infrastructure.persistence.journal_trade_repository.sqlite3.connect", connection), \
                     patch("kiwoom_monitor.infrastructure.persistence.journal_trade_repository.kst_wall_now",
                           return_value=datetime(2031, 1, 2, 12, 34, 56)):
                    JournalWindow._history_received(owner, ((fill,), costs, cost_error, context),
                                                    date(2026, 9, 14), date(2026, 9, 15))
                clock.assert_called_once_with()
                implicit_clock.assert_not_called()
                owner._history_enrichment_failed.assert_not_called()
                owner._refresh_account_choices.assert_called_once_with(scope)
                owner.reload_history.assert_called_once_with()
                self.assertEqual(3, events.count("COMMIT"))  # history transaction + the two existing task transitions
                self.assertLess(events.index("COMMIT"), events.index("accounts"))
                self.assertLess(events.index("accounts"), events.index("reload"))
                self.assertEqual(("accounts", (fill,), costs, ("running", "running")), snapshots[0])
                expected_cost_state = {"pending_cost": "partial", "failed_cost": "retryable_failed"}.get(scenario, "complete")
                self.assertEqual(("reload", (fill,), costs, ("complete", expected_cost_state)), snapshots[1])
                self.assertEqual((), repository.load_fills(datetime(2026, 9, 14), datetime(2026, 9, 16), self.other))
                with closing(sqlite3.connect(path)) as current:
                    self.assertEqual(("2026-09-15T09:10:11", scope.account_ref, scope.account_ref), current.execute(
                        "SELECT filled_at,origin_account_ref,canonical_account_ref FROM trade_fills").fetchone())
                    actual_costs = current.execute("SELECT fill_date,settlement_date,confirmed_at FROM daily_trade_costs").fetchall()
                    self.assertEqual([("2026-09-15", "2026-09-17", received_at.isoformat())] if costs else [], actual_costs)
                    rows = current.execute("SELECT kind,state,updated_at,completed_at,next_retry_at,last_error FROM journal_enrichment_tasks ORDER BY kind").fetchall()
                expected_retry = received_at + timedelta(minutes=30 if scenario == "pending_cost" else 5)
                self.assertEqual([
                    ("costs", expected_cost_state, received_at.isoformat(),
                     received_at.isoformat() if expected_cost_state == "complete" else None,
                     expected_retry.isoformat() if expected_cost_state != "complete" else None,
                     "체결 비용 정산 대기" if scenario == "pending_cost" else cost_error[:1000]),
                    ("fills", "complete", received_at.isoformat(), received_at.isoformat(), None, ""),
                ], rows)
                expected_result = {"cost_count": 0, "source": "estimated"} if scenario == "estimated_cost" else {"cost_count": len(costs)}
                if scenario != "failed_cost":
                    self.assertEqual(expected_result, repository.load_enrichment_task(task_ids["costs"]).result)
                if scenario in {"actual_cost", "estimated_cost"}:
                    owner._journal_settings.setValue.assert_called_once_with("last_history_sync_day", "2030-01-02")
                else:
                    owner._journal_settings.setValue.assert_not_called()

    def test_native_history_rejects_scope_rolls_back_cost_failure_and_never_writes_without_clock(self):
        for scenario in ("scope", "mixed_rows", "invalid", "shutdown", "database", "provider"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "journal.sqlite3"
                repository = JournalRepository(path)
                upsert = Mock(wraps=repository.upsert_history_sync)
                repository.upsert_history_sync = upsert
                fill = TradeFill("history-1", "005930", "fixture", "매수", datetime(2026, 9, 15, 9, 10, 11),
                                 2, 1000, origin_scope=self.scope)
                cost = DailyTradeCost(date(2026, 9, 15), date(2026, 9, 17), "005930", "매수", 2000, 1997, 1, 2, 3,
                                     origin_scope=self.other if scenario == "mixed_rows" else self.scope)
                clock = Mock(return_value=datetime(2030, 1, 2, 20, 6, 7))
                if scenario == "provider":
                    clock.side_effect = RuntimeError("cached clock unavailable")
                owner = self.owner()
                owner._repo = repository
                owner._query_client.server_now = clock
                owner._refresh_account_choices = Mock()
                owner._shutting_down = scenario == "shutdown"
                context = AccountQueryContext(self.other, "fixture", 1, "nas") if scenario == "scope" else self.context
                result = ((fill,), (cost,), "", context) if scenario != "invalid" else "invalid result"
                if scenario == "database":
                    with closing(sqlite3.connect(path)) as current:
                        current.execute("CREATE TRIGGER reject_history_cost BEFORE INSERT ON daily_trade_costs "
                                        "BEGIN SELECT RAISE(ABORT, 'cost write rejected'); END")
                failures = []

                def failed(message):
                    with closing(sqlite3.connect(path)) as current:
                        failures.append((message, current.execute("SELECT COUNT(*) FROM trade_fills").fetchone()[0],
                                         current.execute("SELECT COUNT(*) FROM daily_trade_costs").fetchone()[0]))

                owner._history_enrichment_failed.side_effect = failed
                JournalWindow._history_received(owner, result, date(2026, 9, 14), date(2026, 9, 15))
                if scenario in {"database", "provider"}:
                    clock.assert_called_once_with()
                else:
                    clock.assert_not_called()
                if scenario == "database":
                    upsert.assert_called_once_with((fill,), (cost,), now=as_kst(clock.return_value), account_scope=self.scope)
                else:
                    upsert.assert_not_called()
                if scenario not in {"invalid", "shutdown"}:
                    owner._history_enrichment_failed.assert_called_once()
                    self.assertEqual((0, 0), failures[0][1:])  # failure callback runs after rollback, not before it
                    self.assertTrue(failures[0][0].startswith("체결·비용 저장 실패: "))
                else:
                    owner._history_enrichment_failed.assert_not_called()
                owner.reload_history.assert_not_called()
                owner._refresh_account_choices.assert_not_called()
                owner._journal_settings.setValue.assert_not_called()
                with closing(sqlite3.connect(path)) as current:
                    self.assertEqual((0, 0), (current.execute("SELECT COUNT(*) FROM trade_fills").fetchone()[0],
                                             current.execute("SELECT COUNT(*) FROM daily_trade_costs").fetchone()[0]))

    def test_slow_account_discovery_runs_outside_gui_and_uses_queued_slot(self):
        started, release = Event(), Event()
        ui_thread = get_ident(); io_threads = []
        def loader():
            io_threads.append(get_ident()); started.set(); release.wait(2)
            return (self.context,)
        # Initialize only the real Qt receiver; avoid unrelated DB/QSettings/startup I/O.
        window = JournalWindow.__new__(JournalWindow); QMainWindow.__init__(window)
        window._query_client = SimpleNamespace(load_account_contexts=loader)
        window._account_list_worker = None; window._shutting_down = False
        window._journal_settings = SimpleNamespace(value=lambda *args, **kwargs: "saved-account")
        window._refresh_account_choices = Mock(); window.reload_history = Mock(); window._auto_sync_history_once = Mock()
        window._load_api_account_choices(); worker = window._account_list_worker; joined = False
        try:
            wait_until(started.is_set)
            ticks = []; QTimer.singleShot(0, lambda: ticks.append(True))
            wait_until(lambda: bool(ticks))
            self.assertNotEqual(io_threads[0], ui_thread)
            window._load_api_account_choices()
            self.assertEqual(len(io_threads), 1)
            release.set(); worker.wait(3000); joined = True
            wait_until(lambda: window._account_list_worker is None)
            self.assertEqual(window._available_account_contexts, (self.context,))
            window._refresh_account_choices.assert_called_once_with(None)
        finally:
            release.set()
            window._shutting_down = True
            window.deleteLater()
            if not joined: worker.wait(3000)
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.app.processEvents()

    def test_account_with_no_journal_rows_is_selectable_and_selection_is_preserved(self):
        combo = QComboBox()
        owner = SimpleNamespace(_account_filter=combo,
            _journal_settings=SimpleNamespace(value=lambda *args, **kwargs: ""),
            _repo=SimpleNamespace(list_account_scopes=lambda: (LEGACY_ACCOUNT_SCOPE,)),
            _available_account_contexts=(self.context,), _account_label=JournalWindow._account_label)
        JournalWindow._refresh_account_choices(owner, self.scope)
        self.assertEqual(combo.count(), 2)
        self.assertEqual(combo.currentData(), self.scope)
        self.assertEqual(combo.currentText(), "모의 · 단타 모의")
        JournalWindow._refresh_account_choices(owner)
        self.assertEqual(combo.currentData(), self.scope)
        combo.deleteLater(); self.app.processEvents()

    def test_worker_rejects_connection_rotation_with_same_account(self):
        history = SimpleNamespace(load_day_batch=Mock(side_effect=[SimpleNamespace(fills=(), context=self.context),
            SimpleNamespace(fills=(), context=AccountQueryContext(self.scope, "profile-a", 2, "nas"))]))
        costs = SimpleNamespace(load_period_batch=Mock())
        worker = HistoryWorker(history, costs, date(2026, 9, 14), date(2026, 9, 15), account_scope=self.scope)
        complete, failed = [], []
        worker.completed.connect(lambda *args: complete.append(args)); worker.failed.connect(failed.append)
        worker.run()
        self.assertEqual(complete, []); self.assertEqual(len(failed), 1)
        costs.load_period_batch.assert_not_called()

    def test_worker_cannot_relabel_default_query_to_selected_account(self):
        history = SimpleNamespace(load_day_batch=Mock())
        costs = SimpleNamespace(load_period_batch=Mock())
        worker = HistoryWorker(history, costs, date(2026, 9, 15), date(2026, 9, 15),
            account_client=object(), account_scope=self.scope)
        failed = []; worker.failed.connect(failed.append); worker.run()
        self.assertEqual(len(failed), 1); history.load_day_batch.assert_not_called()

    def test_history_thread_finishes_and_deletes_without_losing_its_owner(self):
        legacy_context = AccountQueryContext(
            LEGACY_ACCOUNT_SCOPE, "legacy-unverified", 0, "legacy",
        )
        window = JournalWindow.__new__(JournalWindow); QMainWindow.__init__(window)
        window._history_worker = None
        window._history_service = SimpleNamespace(
            load_day_batch=lambda _day: SimpleNamespace(fills=(), context=legacy_context),
        )
        window._cost_service = SimpleNamespace(
            load_period_batch=lambda _start, _end: SimpleNamespace(
                costs=(), context=legacy_context,
            ),
        )
        window._query_client = None
        window._selected_account_scope = lambda: LEGACY_ACCOUNT_SCOPE
        window._history_progress = lambda _message: None
        window._history_enrichment_failed = lambda _message: None
        window._history_received = lambda *_values: None
        window._history_enrichment_task_ids = {}
        window._quit_after_history_sync = False

        JournalWindow._start_history_sync(
            window, date(2026, 9, 21), date(2026, 9, 21),
        )

        wait_until(lambda: window._history_worker is None)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
