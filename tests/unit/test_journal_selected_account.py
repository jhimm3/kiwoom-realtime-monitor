import os
import unittest
import uuid
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock
from threading import Event, get_ident

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QComboBox, QMainWindow
from PySide6.QtCore import QTimer, QCoreApplication, QEvent
from qt_settings_test_support import wait_until

from kiwoom_monitor.journal_process import JournalWindow
from kiwoom_monitor.domain.order_contract import AccountScope, AccountEnvironment, LEGACY_ACCOUNT_SCOPE
from kiwoom_monitor.infrastructure.kiwoom_rest.account_query import AccountQueryContext
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
            _selected_account_scope=lambda: selected or self.scope,
            _repo=SimpleNamespace(upsert_history_sync=Mock()), _history_enrichment_failed=Mock(),
            _journal_settings=SimpleNamespace(setValue=Mock()), _status=SimpleNamespace(setText=Mock()),
            reload_history=Mock())

    def test_late_result_after_account_change_never_writes(self):
        owner = self.owner(self.other)
        JournalWindow._history_received(owner, ((), (), "", self.context), date(2026, 9, 15), date(2026, 9, 15))
        owner._repo.upsert_history_sync.assert_not_called()
        owner._history_enrichment_failed.assert_called_once()

    def test_empty_completed_query_retains_explicit_account_scope(self):
        owner = self.owner()
        JournalWindow._history_received(owner, ((), (), "", self.context), date(2026, 9, 15), date(2026, 9, 15))
        owner._repo.upsert_history_sync.assert_called_once_with((), (), account_scope=self.scope)

    def test_rows_cannot_override_completed_legacy_context(self):
        owner = self.owner(LEGACY_ACCOUNT_SCOPE); owner._history_expected_scope = LEGACY_ACCOUNT_SCOPE
        legacy = AccountQueryContext(LEGACY_ACCOUNT_SCOPE, "legacy-unverified", 0, "legacy")
        JournalWindow._history_received(owner, ((SimpleNamespace(origin_scope=self.scope),), (), "", legacy),
            date(2026, 9, 15), date(2026, 9, 15))
        owner._repo.upsert_history_sync.assert_not_called()
        owner._history_enrichment_failed.assert_called_once()

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
