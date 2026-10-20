from __future__ import annotations

import sqlite3
import sys
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from pathlib import Path
from contextlib import closing, ExitStack
from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope, LEGACY_ACCOUNT_SCOPE
from unittest.mock import Mock, patch

from kiwoom_monitor.domain.time_basis import as_kst
from kiwoom_monitor.journal_process import JournalWindow
from kiwoom_monitor.application.trade_history_service import TradeFill
from kiwoom_monitor.application.trade_journal_summary import TradeReview, group_trade_episodes
from kiwoom_monitor.infrastructure.kiwoom_rest.account_query import AccountQueryContext


class JournalDetachedFlowTests(unittest.TestCase):
    def test_backfill_failure_shares_receipt_with_native_status_and_retry(self) -> None:
        from kiwoom_monitor.application.journal_enrichment import JournalEnrichmentService
        from kiwoom_monitor.application.minute_trade_value import MinuteOhlcv
        from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository

        day, code = date(2026, 9, 9), "005930"
        for receipt in (datetime(2030, 1, 2, 3, 4, 5), datetime(2030, 1, 1, 18, 4, 5, tzinfo=timezone.utc)):
            with self.subTest(receipt=receipt), tempfile.TemporaryDirectory() as directory:
                repository = JournalRepository(Path(directory) / "journal.sqlite3")
                bar = MinuteOhlcv(datetime(2026, 9, 9, 10), 100, 110, 90, 105, 20)
                repository.upsert_bars(code, (bar,), "realtime_partial")
                original_bars = repository.load_chart_bars_range(code, day, day)
                service_clock = Mock(side_effect=AssertionError("explicit receipt must win"))
                service = JournalEnrichmentService(repository, "backfill-owner", now_provider=service_clock)
                task = service.register("stock_day", "2026-09-09:005930", "minute_bars", {}, now=as_kst(datetime(2026, 9, 10, 9)))
                service.start(task.task_id, as_kst(datetime(2026, 9, 10, 9, 1)))
                running = repository.load_enrichment_task(task.task_id)
                clock = Mock(return_value=receipt)
                owner = SimpleNamespace(_query_client=SimpleNamespace(server_now=clock), _repo=repository,
                    _journal_enrichment=service, _backfill_enrichment_task_ids={(code, day): task.task_id})
                message = "controlled failure " + "x" * 510
                failure_order = []
                original_fail = service.fail

                def fail_after_commit(task_id, error, now):
                    # A separate connection/reader must see status before the task transition.
                    state = repository.bar_backfill_state(code, day)
                    self.assertEqual(("실패", 1, message[:500]), (state.state, state.bar_count, state.message))
                    self.assertEqual(running, repository.load_enrichment_task(task_id))
                    failure_order.append("committed-status-before-task")
                    return original_fail(task_id, error, now)

                with patch.object(service, "fail", side_effect=fail_after_commit), \
                     patch("kiwoom_monitor.infrastructure.persistence.journal_bar_repository.kst_wall_now", side_effect=AssertionError("no host receipt")):
                    JournalWindow._backfill_failed(owner, code, day, message)
                clock.assert_called_once_with()
                service_clock.assert_not_called()
                self.assertEqual(["committed-status-before-task"], failure_order)
                expected = as_kst(receipt)
                with closing(sqlite3.connect(repository._path)) as connection:
                    row = connection.execute("SELECT * FROM journal_bar_backfill").fetchone()
                    task_times = connection.execute("SELECT created_at,started_at,updated_at FROM journal_enrichment_tasks WHERE task_id=?", (task.task_id,)).fetchone()
                self.assertEqual((day.isoformat(), code, "실패", message[:500], expected.isoformat(timespec="seconds")), row)
                self.assertEqual(("2026-09-10T09:00:00+09:00", "2026-09-10T09:01:00+09:00", expected.isoformat()), task_times)
                failed = repository.load_enrichment_task(task.task_id)
                self.assertEqual(("retryable_failed", 1, expected + timedelta(minutes=5), message),
                    (failed.state, failed.attempts, failed.next_retry_at, failed.last_error))
                self.assertEqual((running.task_id, running.input_fingerprint, running.origin_scope),
                    (failed.task_id, failed.input_fingerprint, failed.origin_scope))
                self.assertEqual("", failed.owner)  # Existing transition releases the running owner.
                self.assertEqual(original_bars, repository.load_chart_bars_range(code, day, day))
                clock.reset_mock()
                JournalWindow._backfill_failed(owner, code, "not-a-date", message)
                clock.assert_not_called()
                # A late failure with no mapped task still records its own status only.
                owner._backfill_enrichment_task_ids = {}
                JournalWindow._backfill_failed(owner, "000660", day, "unmapped")
                clock.assert_called_once_with()
                self.assertEqual("unmapped", repository.bar_backfill_state("000660", day).message)
                self.assertEqual(failed, repository.load_enrichment_task(task.task_id))

    def test_backfill_receipt_or_storage_failure_preserves_rows_and_running_task(self) -> None:
        from kiwoom_monitor.application.journal_enrichment import JournalEnrichmentService
        from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository

        day, code = date(2026, 9, 9), "005930"
        with tempfile.TemporaryDirectory() as directory:
            repository = JournalRepository(Path(directory) / "journal.sqlite3")
            old = as_kst(datetime(2026, 9, 10, 9))
            repository.mark_bar_backfill(code, day, "실패", "previous", now=old)
            service = JournalEnrichmentService(repository, "backfill-owner", now_provider=Mock(side_effect=AssertionError("no resample")))
            task = service.register("stock_day", "2026-09-09:005930", "minute_bars", {}, now=old)
            service.start(task.task_id, old)
            running = repository.load_enrichment_task(task.task_id)
            clock = Mock(side_effect=OSError("clock unavailable"))
            owner = SimpleNamespace(_query_client=SimpleNamespace(server_now=clock), _repo=repository,
                _journal_enrichment=service, _backfill_enrichment_task_ids={(code, day): task.task_id})
            with closing(sqlite3.connect(repository._path)) as connection:
                original = connection.execute("SELECT * FROM journal_bar_backfill").fetchall()
            with patch.object(service, "fail", wraps=service.fail) as fail:
                with self.assertRaisesRegex(OSError, "clock unavailable"):
                    JournalWindow._backfill_failed(owner, code, day, "new failure")
                fail.assert_not_called()
                clock.side_effect = None
                clock.return_value = as_kst(datetime(2030, 1, 2, 3, 4, 5))
                with closing(sqlite3.connect(repository._path)) as connection, connection:
                    connection.execute("CREATE TRIGGER deny_backfill BEFORE INSERT ON journal_bar_backfill BEGIN SELECT RAISE(ABORT, 'controlled backfill rollback'); END")
                with self.assertRaisesRegex(sqlite3.IntegrityError, "controlled backfill rollback"):
                    JournalWindow._backfill_failed(owner, code, day, "new failure")
                fail.assert_not_called()
            self.assertEqual(2, clock.call_count)
            with closing(sqlite3.connect(repository._path)) as connection:
                self.assertEqual(original, connection.execute("SELECT * FROM journal_bar_backfill").fetchall())
            self.assertEqual(running, repository.load_enrichment_task(task.task_id))

    def test_analysis_task_signals_share_one_clock_per_batch_and_preserve_retry_state(self) -> None:
        from PySide6.QtCore import QEvent
        from PySide6.QtWidgets import QApplication
        from kiwoom_monitor.application.journal_enrichment import JournalEnrichmentService
        from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository
        from kiwoom_monitor.presentation.journal_workers import AnalysisEnrichmentWorker

        type(self)._application = QApplication.instance() or QApplication([])
        app = type(self)._application
        with tempfile.TemporaryDirectory() as directory:
            repository = JournalRepository(Path(directory) / "journal.sqlite3")
            registered_at = as_kst(datetime(2030, 1, 2, 3, 0))
            current = [registered_at]
            clock = Mock(side_effect=lambda: current[0])
            enrichment = JournalEnrichmentService(repository, "test-owner", now_provider=clock)
            current_episode = group_trade_episodes((
                TradeFill("1", "005930", "fixture", "매수", datetime(2026, 9, 9, 9, 10), 1, 100),
                TradeFill("2", "005930", "fixture", "매도", datetime(2026, 9, 9, 9, 15), 1, 105),
            ))[0]
            finish_at = [registered_at + timedelta(minutes=2)]
            failure = [False]

            def prepare(*_args, **_kwargs):
                current[0] = finish_at[0]
                if failure[0]:
                    raise RuntimeError("controlled analysis failure")
                return SimpleNamespace(analysis_revision_id="revision-test", daily_bar_count=0,
                                       cycles=(), entry_snapshots=(), research_links=())

            owner = SimpleNamespace(_shutting_down=False, _analysis_enrichment_worker=None,
                _query_client=SimpleNamespace(server_now=clock), _journal_enrichment=enrichment,
                _repo=repository, _monitor_db=Path(directory) / "missing-monitor.sqlite3",
                _all_episodes=(current_episode,), _AUTO_ANALYSIS_BATCH_SIZE=2,
                _trade_analysis_preparer=SimpleNamespace(prepare=prepare), _active_strategy_pack=None,
                _strategy_packs=(), _strategy_result_mode="together")
            owner._analysis_enrichment_started = lambda group: JournalWindow._analysis_enrichment_started(owner, group)
            owner._analysis_enrichment_completed = lambda group, value: JournalWindow._analysis_enrichment_completed(owner, group, value)
            owner._analysis_enrichment_failed = lambda group, message: JournalWindow._analysis_enrichment_failed(owner, group, message)
            owner._analysis_enrichment_finished = lambda: None
            workers = []
            try:
                # Control launch; run the real worker body/signals in this thread. The other test uses a real QThread.
                with patch.object(AnalysisEnrichmentWorker, "start") as launch:
                    JournalWindow._schedule_analysis_enrichment(owner)
                    self.assertEqual(1, clock.call_count)
                    self.assertEqual(1, launch.call_count)
                    workers.append(owner._analysis_enrichment_worker)
                    first_tasks = {task.kind: task for task in repository.list_enrichment_tasks()}
                    self.assertEqual(5, len(first_tasks))
                    current[0] = registered_at + timedelta(minutes=1)
                    clock.reset_mock()
                    workers[0].run()
                    self.assertEqual(2, clock.call_count)  # One started callback and one completed callback.
                    tasks = {task.kind: task for task in repository.list_enrichment_tasks()}
                    self.assertEqual({"daily_bars": "partial", "market_index": "partial",
                                      "post_trade_news": "unavailable", "investor_flow": "unavailable",
                                      "derived_analysis": "complete"}, {kind: task.state for kind, task in tasks.items()})
                    due = finish_at[0] + timedelta(minutes=30)
                    self.assertEqual(due, tasks["daily_bars"].next_retry_at)
                    with closing(sqlite3.connect(repository._path)) as connection:
                        rows = connection.execute("SELECT created_at,started_at,updated_at FROM journal_enrichment_tasks").fetchall()
                    self.assertEqual([(registered_at.isoformat(), (registered_at + timedelta(minutes=1)).isoformat(),
                                       finish_at[0].isoformat())] * 5, rows)

                    current[0] = due - timedelta(microseconds=1)
                    clock.reset_mock()
                    JournalWindow._schedule_analysis_enrichment(owner)
                    self.assertEqual(1, clock.call_count)
                    self.assertEqual(1, launch.call_count)
                    current[0] = due.astimezone(timezone.utc)
                    clock.reset_mock()
                    JournalWindow._schedule_analysis_enrichment(owner)
                    self.assertEqual(1, clock.call_count)
                    self.assertEqual(2, launch.call_count)
                    self.assertEqual(2, len(owner._analysis_enrichment_task_ids[current_episode.group_id]))
                    workers.append(owner._analysis_enrichment_worker)
                    failure[0] = True
                    finish_at[0] = due + timedelta(minutes=1)
                    clock.reset_mock()
                    workers[1].run()
                    self.assertEqual(2, clock.call_count)
                    after = {task.kind: task for task in repository.list_enrichment_tasks()}
                    self.assertEqual(tasks["derived_analysis"], after["derived_analysis"])
                    for kind in ("daily_bars", "market_index"):
                        self.assertEqual(first_tasks[kind].task_id, after[kind].task_id)
                        self.assertEqual("retryable_failed", after[kind].state)
                        self.assertEqual(2, after[kind].attempts)
                        self.assertEqual(finish_at[0] + timedelta(minutes=10), after[kind].next_retry_at)
                        self.assertEqual("controlled analysis failure", after[kind].last_error)
                    clock.reset_mock()
                    JournalWindow._analysis_enrichment_started(owner, "missing")
                    JournalWindow._analysis_enrichment_completed(owner, "missing", None)
                    JournalWindow._analysis_enrichment_failed(owner, "missing", "ignored")
                    clock.assert_not_called()
            finally:
                for worker in workers:
                    worker.requestInterruption()
                    self.assertTrue(worker.wait(3000))
                    worker.deleteLater()
                    app.sendPostedEvents(worker, QEvent.Type.DeferredDelete)

    def test_window_analysis_and_enrichment_share_current_client_daily_receipt(self) -> None:
        from PySide6.QtCore import QEvent, QSettings, Qt, QTimer
        from PySide6.QtWidgets import QApplication
        from qt_settings_test_support import wait_until
        from kiwoom_monitor.infrastructure.persistence.database import Database
        from kiwoom_monitor.infrastructure.persistence.market_data_metadata_repository import MarketDataMetadataRepository
        from kiwoom_monitor.domain.market_data_contract import MarketDatasetKind
        from kiwoom_monitor.presentation.journal_workers import AnalysisEnrichmentWorker
        from kiwoom_monitor.application.journal_enrichment import JournalEnrichmentService
        from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository

        type(self)._application = QApplication.instance() or QApplication([])
        app = type(self)._application
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            monitor, journal = root / "monitor.sqlite3", root / "journal.sqlite3"
            Database(monitor).initialize()
            with closing(sqlite3.connect(monitor)) as connection, connection:
                connection.executemany(
                    "INSERT INTO daily_bars(stock_code,trade_date,open_price,high_price,low_price,close_price,volume,trade_value_eok) VALUES(?,?,?,?,?,?,?,?)",
                    (("005930", "2026-09-09", 100, 110, 90, 105, 10, 1.0),
                     ("005930", "2030-01-02", 200, 210, 190, 205, 20, 2.0)),
                )
            settings = QSettings(str(root / "journal.ini"), QSettings.Format.IniFormat)
            settings.setValue("auto_history_sync_enabled", False)
            seed_service = JournalEnrichmentService(JournalRepository(journal), "previous-owner")
            seed_time = datetime(2026, 9, 9, 9)
            interrupted = seed_service.register("trade_group", "startup", "derived_analysis", {}, now=seed_time)
            seed_service.start(interrupted.task_id, seed_time)
            startup_time = as_kst(datetime(2026, 9, 10, 8))
            initial = Mock(return_value=as_kst(datetime(2030, 1, 2, 3, 4, 5)))
            with patch("kiwoom_monitor.journal_process.QSettings", return_value=settings), \
                 patch("kiwoom_monitor.journal_process.kst_now", return_value=startup_time) as startup_clock, \
                 patch("kiwoom_monitor.journal_process.create_query_client", return_value=SimpleNamespace(server_now=initial)), \
                 patch.object(QTimer, "singleShot"):
                window = JournalWindow(monitor, journal, root / "api.json")
            window.shutdown()  # No unrelated timer/network work in this isolated UI path.
            window._history_followup_timer.stop()  # Constructor's pending history refresh is outside this path.
            current_episode = group_trade_episodes((
                TradeFill("1", "005930", "fixture", "매수", datetime(2026, 9, 9, 9, 10), 1, 100),
                TradeFill("2", "005930", "fixture", "매도", datetime(2026, 9, 9, 9, 15), 1, 105),
            ))[0]
            worker = None
            try:
                startup_clock.assert_called_once_with()
                restored = window._repo.load_enrichment_task(interrupted.task_id)
                self.assertEqual("retryable_failed", restored.state)
                self.assertEqual(startup_time, restored.next_retry_at)
                initial.reset_mock()
                accepted = window._journal_enrichment.register("trade_group", "online", "derived_analysis", {})
                self.assertEqual(1, initial.call_count)
                with closing(sqlite3.connect(journal)) as connection:
                    created_at = connection.execute("SELECT created_at FROM journal_enrichment_tasks WHERE task_id=?",
                                                    (accepted.task_id,)).fetchone()[0]
                self.assertEqual("2030-01-02T03:04:05+09:00", created_at)
                initial.reset_mock()
                window._show_trade_analysis(current_episode, ())
                self.assertEqual(1, initial.call_count)
                self.assertTrue(window._analysis_label.toPlainText())
                first = MarketDataMetadataRepository(journal).load(
                    MarketDatasetKind.DAILY_BAR, "005930", "2026-09-09")
                self.assertEqual(as_kst(datetime(2030, 1, 2, 3, 4, 5)), first.available_at)
                first_revision = window._repo.load_journal_analysis_revisions(current_episode.group_id)[0]
                self.assertEqual(initial.return_value, first_revision.created_at)
                with closing(sqlite3.connect(journal)) as connection:
                    setup_at = connection.execute(
                        "SELECT updated_at FROM trade_setup_classifications WHERE group_id=?",
                        (current_episode.group_id,),
                    ).fetchone()[0]
                self.assertEqual("2030-01-02T03:04:05+09:00", setup_at)

                # The already-created preparer must resolve the current client again.
                updated = Mock(return_value=datetime(2030, 1, 2, 18, 6, 7, tzinfo=timezone.utc))
                window._query_client = SimpleNamespace(server_now=updated)
                received, failed, totals = [], [], []
                worker = AnalysisEnrichmentWorker(
                    window._trade_analysis_preparer, ((current_episode, ()),),
                    active_pack=window._active_strategy_pack, strategy_packs=window._strategy_packs,
                    result_mode=window._strategy_result_mode,
                )
                worker.item_completed.connect(lambda *args: received.append(args), Qt.ConnectionType.QueuedConnection)
                worker.item_failed.connect(lambda *args: failed.append(args), Qt.ConnectionType.QueuedConnection)
                worker.completed.connect(lambda *args: totals.append(args), Qt.ConnectionType.QueuedConnection)
                worker.start()
                wait_until(lambda: bool(totals), 10)
                self.assertTrue(worker.wait(3000))
                self.assertEqual([], failed)
                self.assertEqual([(1, 0)], totals)
                self.assertEqual(1, received[0][1].daily_bar_count)
                self.assertEqual(1, updated.call_count)
                second = MarketDataMetadataRepository(journal).load(
                    MarketDatasetKind.DAILY_BAR, "005930", "2026-09-09")
                self.assertEqual(as_kst(updated.return_value), second.available_at)
                self.assertEqual(first.effective_at, second.effective_at)
                with closing(sqlite3.connect(journal)) as connection:
                    rows = connection.execute("SELECT trade_date,close_price,confirmed_at FROM journal_daily_bars").fetchall()
                    setup_at = connection.execute(
                        "SELECT updated_at FROM trade_setup_classifications WHERE group_id=?",
                        (current_episode.group_id,),
                    ).fetchone()[0]
                self.assertEqual([("2026-09-09", 105, "2030-01-03T03:06:07+09:00")], rows)
                self.assertEqual("2030-01-03T03:06:07+09:00", setup_at)
                revisions = window._repo.load_journal_analysis_revisions(current_episode.group_id)
                self.assertEqual((first_revision,), revisions)
                self.assertEqual(first_revision.revision_id, received[0][1].analysis_revision_id)
            finally:
                if worker is not None:
                    worker.requestInterruption()
                    self.assertTrue(worker.wait(3000))
                    worker.deleteLater()
                    app.sendPostedEvents(worker, QEvent.Type.DeferredDelete)
                window.shutdown()
                window.deleteLater()
                app.sendPostedEvents(window, QEvent.Type.DeferredDelete)

    def test_account_change_debounces_reload_and_skips_same_scope(self) -> None:
        scope = AccountScope(
            "kiwoom", AccountEnvironment.REAL, "11111111-1111-4111-8111-111111111111",
        )
        calls: list[object] = []
        owner = SimpleNamespace(
            _selected_account_scope=lambda: scope,
            _journal_settings=SimpleNamespace(setValue=lambda *args: calls.append(("setting", *args))),
            _history_followup_timer=SimpleNamespace(stop=lambda: calls.append("stop-followup")),
            _account_reload_timer=SimpleNamespace(start=lambda delay: calls.append(("start", delay))),
            _loaded_history_scope=LEGACY_ACCOUNT_SCOPE,
            _active_episode=object(),
            _save_active_review=Mock(),
            reload_history=lambda: calls.append("reload"),
        )

        JournalWindow._account_filter_changed(owner)
        JournalWindow._account_filter_changed(owner)
        self.assertNotIn("reload", calls)
        self.assertEqual(2, calls.count(("start", 20)))

        JournalWindow._reload_selected_account(owner)
        self.assertEqual(1, calls.count("reload"))
        self.assertIsNone(owner._active_episode)
        owner._loaded_history_scope = scope
        owner._active_episode = object()
        JournalWindow._reload_selected_account(owner)
        self.assertEqual(1, calls.count("reload"))

    def test_reload_history_defers_backfill_and_enrichment_to_followup_event(self) -> None:
        calls: list[object] = []
        selected_day = date(2026, 9, 13)
        scope = LEGACY_ACCOUNT_SCOPE
        result = SimpleNamespace(fills=(), costs=(), episodes=())
        owner = SimpleNamespace(
            _from_date=SimpleNamespace(date=lambda: SimpleNamespace(toPython=lambda: selected_day)),
            _to_date=SimpleNamespace(date=lambda: SimpleNamespace(toPython=lambda: selected_day)),
            _selected_account_scope=lambda: scope,
            _trade_history_query=SimpleNamespace(
                load=lambda *_args, **kwargs: calls.append(("query", kwargs["account_scope"])) or result,
            ),
            _episodes=(),
            _apply_history_filters=lambda: calls.append("render"),
            _render_fills=lambda fills: calls.append(("fills", fills)),
            _history_followup_timer=SimpleNamespace(start=lambda delay: calls.append(("followup", delay))),
            _refresh_backfill_table=lambda: calls.append("backfill"),
            _schedule_analysis_enrichment=lambda: calls.append("enrichment"),
        )

        JournalWindow.reload_history(owner)

        self.assertEqual(
            [("query", scope), "render", ("fills", ()), ("followup", 0)], calls,
        )
        JournalWindow._run_history_followups(owner)
        self.assertEqual(["backfill", "enrichment"], calls[-2:])

    def test_loaded_legacy_review_is_not_autosaved_and_edits_use_legacy_write_contract(self) -> None:
        saved: list[tuple[TradeReview, AccountScope | None]] = []
        legacy_episode = group_trade_episodes((TradeFill(
            "1", "005930", "삼성전자", "매수", datetime(2026, 9, 13, 9), 1, 70000,
        ),))[0]

        class TextValue:
            def __init__(self, value: str = "") -> None:
                self.value = value

            def setPlainText(self, value: str) -> None:
                self.value = value

            def toPlainText(self) -> str:
                return self.value

            def setText(self, value: str) -> None:
                self.value = value

            def text(self) -> str:
                return self.value

            def setCurrentText(self, value: str) -> None:
                self.value = value

            def currentText(self) -> str:
                return self.value

        timer = SimpleNamespace(start=lambda _delay: None, stop=lambda: None)
        owner = SimpleNamespace(
            _active_episode=legacy_episode, _loading_review=False, _review_dirty=True,
            _repo=SimpleNamespace(
                load_review=lambda *_args: TradeReview(
                    legacy_episode.group_id, "기존 이유", "기존 복기",
                    origin_scope=LEGACY_ACCOUNT_SCOPE,
                ),
                save_review=lambda review, *, account_scope: saved.append((review, account_scope)),
            ),
            _review_reason=TextValue(), _review_note=TextValue(), _review_tags=TextValue(),
            _review_rating=TextValue("보통"), _review_status=TextValue("미작성"),
            _review_saved=TextValue(), _review_save_timer=timer,
            _summary_table=SimpleNamespace(currentRow=lambda: -1),
        )

        JournalWindow._load_active_review(owner)
        JournalWindow._save_active_review(owner)
        self.assertEqual([], saved)

        owner._review_note.setPlainText("수정한 복기")
        JournalWindow._review_changed(owner)
        JournalWindow._save_active_review(owner)
        self.assertEqual(1, len(saved))
        self.assertIsNone(saved[0][1])
        self.assertEqual("수정한 복기", saved[0][0].review)

        scoped = AccountScope(
            "kiwoom", AccountEnvironment.REAL, "11111111-1111-4111-8111-111111111111",
        )
        owner._active_episode = group_trade_episodes((TradeFill(
            "2", "005930", "삼성전자", "매수", datetime(2026, 9, 13, 10), 1, 71000,
            origin_scope=scoped,
        ),))[0]
        owner._review_dirty = True
        JournalWindow._save_active_review(owner)
        self.assertEqual(scoped, saved[-1][1])

    def test_multiday_episode_daily_chart_uses_latest_available_day(self) -> None:
        fills = (
            TradeFill("1", "005930", "삼성전자", "매수", datetime(2026, 9, 7, 9, 10), 1, 100, "", "KRX"),
            TradeFill("2", "005930", "삼성전자", "매도", datetime(2026, 9, 9, 10, 20), 1, 110, "", "KRX"),
        )
        episode = group_trade_episodes(fills)[0]
        requested: list[tuple[str, date, str]] = []
        item = SimpleNamespace(data=lambda _role: episode)
        chart = SimpleNamespace(
            clear_daily_rows=lambda: None, set_fills=lambda _fills: None,
            set_rows=lambda _rows: None, focus_time=lambda _time: None,
        )
        owner = SimpleNamespace(
            _query_client=SimpleNamespace(server_now=lambda: datetime(2026, 9, 15, 10)),
            _summary_table=SimpleNamespace(currentRow=lambda: 0, item=lambda _row, _column: item),
            _active_episode=None,
            _review_dirty=False,
            _load_active_review=lambda: None,
            _history_chart=chart,
            _history_daily_chart=SimpleNamespace(clear_daily_rows=lambda: None, set_fills=lambda _fills: None),
            _history_interval=SimpleNamespace(currentText=lambda: "일봉"),
            _dual_history_chart=SimpleNamespace(isChecked=lambda: False),
            _load_selected_chart_rows=lambda: (),
            _apply_history_chart_state=lambda *_args: None,
            _render_fills=lambda _fills: None,
            _show_trade_analysis=lambda *_args: None,
            _sync_detached_charts=lambda: None,
            _request_incomplete_trade_days=lambda _fills: None,
            _request_previous_chart_day=lambda *_args: None,
            _start_daily_chart=lambda *args: requested.append(args),
        )

        JournalWindow._show_selected_summary(owner)

        self.assertEqual([("005930", date(2026, 9, 15), "history")], requested)

    def test_backfill_result_refreshes_selected_chart_only_after_atomic_save(self) -> None:
        day = date(2026, 9, 8)
        bars = (SimpleNamespace(minute=datetime(2026, 9, 8, 9, 1)),)
        calls: list[object] = []
        checked_at = datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        owner = SimpleNamespace(
            _query_client=SimpleNamespace(server_now=lambda: checked_at),
            _repo=SimpleNamespace(
                save_bar_backfill_result=lambda code, saved_day, saved_bars, checked_at, **options: calls.append(
                    (code, saved_day, saved_bars, checked_at, options)
                ) or True,
            ),
            _selected_history_code="005930",
            _selected_history_range=(day, day),
            _refresh_selected_history_after_bars=lambda code, analyze_any_active=False: calls.append(
                ("refresh", code, analyze_any_active)
            ),
        )

        JournalWindow._backfill_received(owner, "005930", day, bars)

        self.assertEqual(
            ("005930", day, bars, as_kst(checked_at), {"coverage_complete": True}), calls[0],
        )
        self.assertEqual(("refresh", "005930", True), calls[1])

    def test_bar_receipts_reach_native_metadata_and_preserve_selected_day(self):
        from kiwoom_monitor.application.minute_trade_value import MinuteOhlcv
        from kiwoom_monitor.infrastructure.persistence.database import Database
        from kiwoom_monitor.infrastructure.persistence.minute_bar_repository import MinuteBarRepository
        from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository
        from kiwoom_monitor.infrastructure.persistence.market_data_metadata_repository import MarketDataMetadataRepository
        from kiwoom_monitor.domain.market_data_contract import MarketDatasetKind
        expected = as_kst(datetime(2030, 1, 1, 18, 4, 5, tzinfo=timezone.utc))
        host = datetime(2031, 1, 2, 12, 34, 56)
        for kind in ("backfill", "daily", "refresh", "detached"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                monitor = root / "monitor.sqlite3"
                Database(monitor).initialize()
                repository = JournalRepository(root / "journal.sqlite3")
                day = expected.date() if kind == "refresh" else date(2026, 9, 8)
                minute = datetime.combine(day, datetime.min.time()).replace(hour=10)
                bar = MinuteOhlcv(minute, 100, 110, 90, 105, 10, 1.0)
                MinuteBarRepository(monitor, now_provider=lambda: expected).upsert_bars("005930", (bar,))
                source_now = (expected.replace(tzinfo=None) if kind in {"backfill", "refresh"}
                              else datetime(2030, 1, 1, 18, 4, 5, tzinfo=timezone.utc))
                clock = Mock(return_value=source_now)
                base = {"_repo": repository, "_monitor_db": monitor,
                        "_query_client": SimpleNamespace(server_now=clock),
                        "_selected_history_code": "", "_selected_history_range": None,
                        "_status": SimpleNamespace(setText=lambda text: None)}
                with patch("kiwoom_monitor.journal_process.kst_wall_now", return_value=host), patch(
                        "kiwoom_monitor.infrastructure.persistence.journal_bar_repository.kst_wall_now", return_value=host), patch(
                        "kiwoom_monitor.journal_process.QTableWidgetItem", side_effect=lambda text: text):
                    if kind == "backfill":
                        JournalWindow._backfill_received(SimpleNamespace(**base), "005930", day, (bar,))
                    elif kind == "daily":
                        JournalWindow._daily_chart_received(SimpleNamespace(**base,
                            _apply_daily_chart_rows=lambda *args, **kwargs: None),
                            "005930", ((day.isoformat(), 100, 110, 90, 105, 10, 1.0),), "history")
                    elif kind == "refresh":
                        table = SimpleNamespace(setRowCount=lambda *args: None,
                                                setItem=lambda *args: None, scrollToBottom=lambda: None)
                        JournalWindow.refresh(SimpleNamespace(**base, _live_code="005930", _table=table,
                                                              _chart=SimpleNamespace(set_rows=lambda rows: None)))
                    else:
                        episode = SimpleNamespace(summary=SimpleNamespace(stock_code="000660"),
                                                  started_at=minute, ended_at=minute)
                        detached = SimpleNamespace(isVisible=lambda: True, selected_stock_codes=lambda: ("005930",),
                                                   selected_index_markets=lambda: (), set_data=lambda *args: None)
                        JournalWindow._sync_detached_charts(SimpleNamespace(**base, _active_episode=episode,
                            _detached_chart_window=detached, _load_selected_chart_rows=lambda: (),
                            _history_chart=SimpleNamespace(setup_types=lambda: ())))
                daily = kind == "daily"
                key = day.isoformat() if daily else minute.isoformat(timespec="minutes")
                metadata = MarketDataMetadataRepository(root / "journal.sqlite3").load(
                    MarketDatasetKind.DAILY_BAR if daily else MarketDatasetKind.MINUTE_BAR, "005930", key)
                table_name = "journal_daily_bars" if daily else "journal_minute_bars"
                with closing(sqlite3.connect(root / "journal.sqlite3")) as connection:
                    stored = connection.execute("SELECT trade_date,confirmed_at FROM " + table_name).fetchall()
                self.assertEqual(1, clock.call_count)
                self.assertIsNotNone(metadata)
                self.assertEqual(expected, metadata.available_at)
                self.assertEqual(day.isoformat(), stored[0][0])
                self.assertEqual("2030-01-02T03:04:05+09:00" if kind in {"backfill", "daily"} else None,
                                 stored[0][1])
                effective = datetime.combine(day, datetime.min.time()) if daily else minute
                self.assertEqual(as_kst(effective), metadata.effective_at)
                if not daily:
                    row = repository.load_bars("005930", minute)[0]
                    self.assertEqual((100, 110, 90, 105, 10, 1.0), row[1:7])
                    self.assertEqual("after_close_confirmed" if kind == "backfill" else "realtime_partial", row[7])

    def test_open_minute_gate_and_daily_copy_use_the_current_client_clock(self):
        from kiwoom_monitor.infrastructure.persistence.database import Database
        from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository
        from kiwoom_monitor.infrastructure.persistence.market_data_metadata_repository import MarketDataMetadataRepository
        from kiwoom_monitor.domain.market_data_contract import MarketDatasetKind
        expected = as_kst(datetime(2030, 1, 1, 18, 4, 5, tzinfo=timezone.utc))
        host = datetime(2031, 1, 2, 12, 34, 56)
        with self.subTest(kind="open"), tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite3"
            repository = JournalRepository(path)
            requests = []
            clock = Mock(return_value=expected.astimezone(timezone.utc))
            owner = SimpleNamespace(_query_client=SimpleNamespace(server_now=clock), _repo=repository, _live_code="",
                _chart=SimpleNamespace(clear_annotations=lambda: None, clear_daily_rows=lambda: None),
                _title=SimpleNamespace(setText=lambda text: None), refresh=lambda: None, _worker=None,
                _start_bar_confirmation=lambda code, day, **kwargs: requests.append(("minute", day)),
                _live_interval=SimpleNamespace(currentText=lambda: "일봉"),
                _start_daily_chart=lambda code, day, target: requests.append(("daily", day)))
            JournalWindow.set_stock(owner, "005930", "fixture")
            with closing(sqlite3.connect(path)) as connection:
                row = connection.execute("SELECT trade_date,last_opened_at FROM journal_stocks").fetchone()
            self.assertEqual(("2030-01-02", "2030-01-02T03:04:05+09:00"), row)
            self.assertEqual([("minute", date(2030, 1, 2)), ("daily", date(2030, 1, 2))], requests)
            self.assertEqual(1, clock.call_count)
        with self.subTest(kind="minute-gate"):
            current = [expected]
            clock = Mock(side_effect=lambda: current[0].astimezone(timezone.utc))
            confirmed = Mock()
            owner = SimpleNamespace(_query_client=SimpleNamespace(server_now=clock), _live_code="005930",
                                    _last_confirmed_minute="", confirm_completed_bars=confirmed)
            counts = []
            for moment in (expected.replace(second=2), expected.replace(second=3), expected.replace(second=4),
                           expected.replace(minute=5, second=1), expected.replace(minute=5, second=3)):
                current[0] = moment
                JournalWindow._confirm_on_new_minute(owner)
                counts.append(confirmed.call_count)
            self.assertEqual([0, 1, 1, 1, 2], counts)
            self.assertEqual("203001020305", owner._last_confirmed_minute)
            self.assertEqual(5, clock.call_count)
        with self.subTest(kind="daily-copy"), tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            monitor = root / "monitor.sqlite3"
            Database(monitor).initialize()
            repository = JournalRepository(root / "journal.sqlite3")
            with closing(sqlite3.connect(monitor)) as connection:
                connection.executemany(
                    "INSERT INTO daily_bars(stock_code,trade_date,open_price,high_price,low_price,close_price,volume,trade_value_eok) "
                    "VALUES('005930',?,100,110,90,105,10,1)", [("2026-09-08",), ("2030-01-02",), ("2031-01-02",)])
                connection.commit()
            # The four-argument repository entry keeps its standalone fallback.
            with patch("kiwoom_monitor.infrastructure.persistence.journal_bar_repository.kst_wall_now", return_value=host):
                self.assertEqual(1, repository.import_monitor_daily_bars(monitor, "005930", date(2026, 9, 8)))
            metadata_repository = MarketDataMetadataRepository(root / "journal.sqlite3")
            self.assertEqual(as_kst(host), metadata_repository.load(
                MarketDatasetKind.DAILY_BAR, "005930", "2026-09-08").available_at)
            current = [expected]
            clock = Mock(side_effect=lambda: current[0].astimezone(timezone.utc))
            minute = datetime(2026, 9, 8, 10)
            owner = SimpleNamespace(_query_client=SimpleNamespace(server_now=clock), _repo=repository, _monitor_db=monitor,
                _active_episode=SimpleNamespace(summary=SimpleNamespace(stock_code="000660"), started_at=minute, ended_at=minute),
                _detached_chart_window=SimpleNamespace(isVisible=lambda: True, selected_stock_codes=lambda: ("005930",),
                                                     selected_index_markets=lambda: (), set_data=lambda *args: None),
                _load_selected_chart_rows=lambda: (), _history_chart=SimpleNamespace(setup_types=lambda: ()))
            JournalWindow._sync_detached_charts(owner)
            with closing(sqlite3.connect(root / "journal.sqlite3")) as connection:
                rows = connection.execute("SELECT trade_date,confirmed_at FROM journal_daily_bars ORDER BY trade_date").fetchall()
            self.assertEqual([("2026-09-08", "2030-01-02T03:04:05+09:00"),
                              ("2030-01-02", "2030-01-02T03:04:05+09:00")], rows)
            self.assertEqual(expected, metadata_repository.load(
                MarketDatasetKind.DAILY_BAR, "005930", "2026-09-08").available_at)
            self.assertEqual(1, clock.call_count)
            # A direct chart request still queries its explicit historical day.
            current[0] = expected.replace(minute=5)
            owner._daily_chart_worker = SimpleNamespace(isRunning=lambda: True)
            owner._pending_daily_chart = None
            owner._apply_daily_chart_rows = Mock()
            JournalWindow._start_daily_chart(owner, "005930", date(2026, 9, 8), "history")
            self.assertEqual(("005930", date(2026, 9, 8), "history"), owner._pending_daily_chart)
            self.assertEqual(expected.replace(minute=5), metadata_repository.load(
                MarketDatasetKind.DAILY_BAR, "005930", "2026-09-08").available_at)
            self.assertEqual(2, clock.call_count)

    def test_live_import_keeps_legacy_lookup_day_and_protects_confirmed_metadata(self):
        from kiwoom_monitor.application.minute_trade_value import MinuteOhlcv
        from kiwoom_monitor.infrastructure.persistence.database import Database
        from kiwoom_monitor.infrastructure.persistence.minute_bar_repository import MinuteBarRepository
        from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository
        from kiwoom_monitor.infrastructure.persistence.market_data_metadata_repository import MarketDataMetadataRepository
        from kiwoom_monitor.domain.market_data_contract import MarketDatasetKind
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            monitor = root / "monitor.sqlite3"
            Database(monitor).initialize()
            minute = datetime(2026, 9, 8, 10)
            original = MinuteOhlcv(minute, 100, 110, 90, 105, 10, 1.0)
            MinuteBarRepository(monitor, now_provider=lambda: as_kst(minute)).upsert_bars("005930", (original,))
            repository = JournalRepository(root / "journal.sqlite3")
            host = datetime(2031, 1, 2, 12, 34, 56)
            with patch("kiwoom_monitor.infrastructure.persistence.journal_bar_repository.kst_wall_now", return_value=host):
                self.assertEqual(1, repository.import_live_bars(monitor, "005930", minute))
            metadata_repository = MarketDataMetadataRepository(root / "journal.sqlite3")
            key = minute.isoformat(timespec="minutes")
            legacy = metadata_repository.load(MarketDatasetKind.MINUTE_BAR, "005930", key)
            self.assertEqual(as_kst(host), legacy.available_at)
            received_at = as_kst(datetime(2030, 1, 2, 3, 4, 5))
            statements = []
            real_connect = sqlite3.connect
            def traced_connect(*args, **kwargs):
                connection = real_connect(*args, **kwargs)
                connection.set_trace_callback(statements.append)
                return connection
            with patch("sqlite3.connect", side_effect=traced_connect):
                self.assertEqual(1, repository.import_live_bars(monitor, "005930", minute, observed_at=received_at))
            self.assertEqual(1, statements.count("COMMIT"))
            self.assertEqual(1, sum(sql.startswith("BEGIN") for sql in statements))
            updated = metadata_repository.load(MarketDatasetKind.MINUTE_BAR, "005930", key)
            self.assertEqual(received_at, updated.available_at)
            self.assertEqual(as_kst(minute), updated.effective_at)
            checked_at = as_kst(datetime(2026, 9, 8, 10, 1))
            confirmed = MinuteOhlcv(minute, 100, 120, 90, 115, 20, 2.0)
            repository.upsert_bars("005930", (confirmed,), "api_confirmed", checked_at)
            before = metadata_repository.load(MarketDatasetKind.MINUTE_BAR, "005930", key)
            repository.import_live_bars(monitor, "005930", minute, observed_at=received_at)
            self.assertEqual(before, metadata_repository.load(MarketDatasetKind.MINUTE_BAR, "005930", key))
            self.assertEqual((100, 120, 90, 115, 20, 2.0, "api_confirmed"), repository.load_bars("005930", minute)[0][1:])
            with closing(sqlite3.connect(root / "journal.sqlite3")) as connection:
                self.assertEqual(checked_at.isoformat(timespec="seconds"), connection.execute(
                    "SELECT confirmed_at FROM journal_minute_bars").fetchone()[0])

    def test_history_result_uses_one_repository_write_before_refresh(self) -> None:
        fills = (SimpleNamespace(order_no="1", origin_scope=LEGACY_ACCOUNT_SCOPE),)
        costs = (SimpleNamespace(total_cost=200, origin_scope=LEGACY_ACCOUNT_SCOPE),)
        calls: list[tuple[str, object, object]] = []
        statuses: list[str] = []
        owner = SimpleNamespace(
            _query_client=SimpleNamespace(server_now=lambda: datetime(2026, 9, 15, 10)),
            _repo=SimpleNamespace(
                upsert_history_sync=lambda saved_fills, saved_costs, *, now, account_scope: calls.append(
                    ("save", saved_fills, saved_costs, account_scope)
                ),
            ),
            _journal_settings=SimpleNamespace(setValue=lambda *_args: None),
            _status=SimpleNamespace(setText=statuses.append),
            reload_history=lambda: calls.append(("reload", None, None)),
        )

        JournalWindow._history_received(
            owner,
            (fills, costs, ""),
            date(2026, 9, 8),
            date(2026, 9, 8),
        )

        self.assertEqual(
            [("save", fills, costs, LEGACY_ACCOUNT_SCOPE), ("reload", None, None)], calls,
        )
        self.assertEqual(["조회 완료 · 체결 1건 · 실제비용 1묶음"], statuses)

    def test_history_result_does_not_call_delayed_same_day_costs_complete(self) -> None:
        fills = (SimpleNamespace(order_no="1", origin_scope=LEGACY_ACCOUNT_SCOPE),)
        statuses: list[str] = []
        owner = SimpleNamespace(
            _query_client=SimpleNamespace(server_now=lambda: datetime(2026, 9, 15, 10)),
            _repo=SimpleNamespace(upsert_history_sync=lambda *_args, **_kwargs: None),
            _journal_settings=SimpleNamespace(setValue=lambda *_args: None),
            _status=SimpleNamespace(setText=statuses.append),
            reload_history=lambda: None,
        )

        JournalWindow._history_received(
            owner, (fills, (), ""), date(2026, 9, 14), date(2026, 9, 14),
        )

        self.assertEqual(["체결 1건 저장 · 실제비용 정산 대기(다음에 재시도)"], statuses)

    def test_mock_history_result_uses_estimated_cost_without_failure_status(self) -> None:
        mock_scope = AccountScope(
            "kiwoom", AccountEnvironment.MOCK, "11111111-1111-4111-8111-111111111111",
        )
        context = AccountQueryContext(mock_scope, "mock-profile", 1, "nas")
        fills = (SimpleNamespace(order_no="1", origin_scope=mock_scope),)
        statuses: list[str] = []
        owner = SimpleNamespace(
            _query_client=SimpleNamespace(server_now=lambda: datetime(2026, 9, 15, 10)),
            _history_expected_scope=mock_scope,
            _selected_account_scope=lambda: mock_scope,
            _repo=SimpleNamespace(upsert_history_sync=lambda *_args, **_kwargs: None),
            _journal_settings=SimpleNamespace(setValue=lambda *_args: None),
            _status=SimpleNamespace(setText=statuses.append),
            reload_history=lambda: None,
        )

        JournalWindow._history_received(
            owner, (fills, (), "", context), date(2026, 9, 21), date(2026, 9, 21),
        )

        self.assertEqual(["모의 체결 1건 저장 · 수수료·세금은 예상값 사용"], statuses)

    def test_history_worker_schedules_deferred_deletion_when_finished(self) -> None:
        calls: list[str] = []

        class Worker:
            def deleteLater(self) -> None:
                calls.append("deleteLater")

        worker = Worker()
        owner = SimpleNamespace(
            _history_worker=worker,
            _history_enrichment_task_ids={"fills": "task"},
            _quit_after_history_sync=False,
        )

        JournalWindow._history_finished(owner)

        self.assertEqual(["deleteLater"], calls)
        self.assertIsNone(owner._history_worker)
        self.assertEqual({}, owner._history_enrichment_task_ids)

    def test_open_active_news_uses_shared_command_channel_contract(self) -> None:
        documents: list[dict[str, object]] = []
        messages: list[str] = []
        episode = SimpleNamespace(
            group_id="group-1",
            summary=SimpleNamespace(
                stock_code="005930",
                stock_name="삼성전자",
                trade_date=date(2026, 9, 8),
            ),
        )
        owner = SimpleNamespace(
            _active_episode=episode,
            _journal_news_channel=SimpleNamespace(send=lambda document: documents.append(document)),
            _review_saved=SimpleNamespace(setText=messages.append),
        )

        JournalWindow._open_active_news(owner)

        self.assertEqual(
            [{
                "code": "005930",
                "name": "삼성전자",
                "group_id": "group-1",
                "trade_date": "2026-09-08",
                "origin_scope": {
                    "broker": "legacy", "environment": "unknown",
                    "account_ref": "legacy-unassigned",
                },
                "account_scope": {
                    "broker": "legacy", "environment": "unknown",
                    "account_ref": "legacy-unassigned",
                },
            }],
            documents,
        )
        self.assertEqual(["뉴스창에서 대표 기사를 선택해 매매일지에 추가할 수 있습니다."], messages)

    def test_market_index_result_uses_one_repository_write_then_refreshes_charts(self) -> None:
        minute_rows = {("kospi", datetime(2026, 9, 8, 9, 1)): (1.0, 2.0, 0.5, 1.5, None)}
        daily_rows = {"kospi": (("2026-09-08T00:00", 1, 2, 1, 2, 10, 20.0),)}
        saved: list[tuple[object, object]] = []
        statuses: list[str] = []
        refreshed: list[bool] = []
        owner = SimpleNamespace(
            _monitor_db="monitor.sqlite3",
            _market_index_active_day=date(2026, 9, 8),
            _market_index_requested_days=set(),
            _status=SimpleNamespace(setText=statuses.append),
            _sync_detached_charts=lambda: refreshed.append(True),
        )

        with patch("kiwoom_monitor.journal_process.MinuteBarRepository") as repository_type:
            repository_type.return_value.replace_market_index_history.side_effect = (
                lambda minutes, daily: saved.append((minutes, daily))
            )
            JournalWindow._market_index_backfill_completed(owner, (minute_rows, daily_rows))

        self.assertEqual([(minute_rows, daily_rows)], saved)
        self.assertEqual({date(2026, 9, 8)}, owner._market_index_requested_days)
        self.assertEqual(["코스피·코스닥 분봉·일봉 보완 · 1개"], statuses)
        self.assertEqual([True], refreshed)

    def test_running_market_index_worker_keeps_only_latest_other_day(self) -> None:
        class RunningWorker:
            @staticmethod
            def isRunning() -> bool:
                return True

        active_day = date(2026, 9, 8)
        owner = SimpleNamespace(
            _market_index_requested_days=set(),
            _market_index_worker=RunningWorker(),
            _market_index_active_day=active_day,
            _pending_market_index_day=None,
        )

        JournalWindow._start_market_index_backfill(owner, active_day)
        JournalWindow._start_market_index_backfill(owner, date(2026, 9, 9))
        JournalWindow._start_market_index_backfill(owner, date(2026, 9, 10))

        self.assertEqual(date(2026, 9, 10), owner._pending_market_index_day)

    def test_finished_market_index_worker_starts_pending_day_once(self) -> None:
        pending = date(2026, 9, 9)
        started: list[date] = []
        owner = SimpleNamespace(
            _market_index_worker=object(),
            _market_index_active_day=date(2026, 9, 8),
            _pending_market_index_day=pending,
            _start_market_index_backfill=started.append,
        )

        JournalWindow._market_index_backfill_finished(owner)

        self.assertIsNone(owner._market_index_worker)
        self.assertIsNone(owner._market_index_active_day)
        self.assertIsNone(owner._pending_market_index_day)
        self.assertEqual([pending], started)

    def test_market_index_save_failure_does_not_mark_day_as_completed(self) -> None:
        target = date(2026, 9, 8)
        statuses: list[str] = []
        owner = SimpleNamespace(
            _monitor_db="monitor.sqlite3",
            _market_index_active_day=target,
            _market_index_requested_days=set(),
            _status=SimpleNamespace(setText=statuses.append),
            _sync_detached_charts=lambda: self.fail("failed save must not refresh charts"),
        )

        with patch("kiwoom_monitor.journal_process.MinuteBarRepository") as repository_type:
            repository_type.return_value.replace_market_index_history.side_effect = sqlite3.Error("locked")
            JournalWindow._market_index_backfill_completed(owner, ({}, {}))

        self.assertNotIn(target, owner._market_index_requested_days)
        self.assertEqual(["시장지수 저장 실패 · locked"], statuses)

    def test_running_daily_worker_keeps_only_latest_pending_request(self) -> None:
        class RunningWorker:
            @staticmethod
            def isRunning() -> bool:
                return True

        owner = SimpleNamespace(
            _query_client=SimpleNamespace(server_now=lambda: datetime(2026, 9, 15, 10)),
            _repo=SimpleNamespace(
                import_monitor_daily_bars=lambda *_args, **_kwargs: 0,
                load_daily_bars=lambda *_args: (),
            ),
            _monitor_db="monitor.sqlite3",
            _daily_chart_worker=RunningWorker(),
            _pending_daily_chart=None,
        )

        JournalWindow._start_daily_chart(owner, "A", date(2026, 9, 8), "history")
        JournalWindow._start_daily_chart(owner, "B", date(2026, 9, 9), "detached:B")

        self.assertEqual(("B", date(2026, 9, 9), "detached:B"), owner._pending_daily_chart)

    def test_finished_daily_worker_starts_pending_request_once(self) -> None:
        started: list[tuple[object, ...]] = []
        owner = SimpleNamespace(
            _daily_chart_worker=object(),
            _pending_daily_chart=("B", date(2026, 9, 9), "detached:B"),
            _start_daily_chart=lambda *request: started.append(request),
        )

        JournalWindow._daily_chart_finished(owner)

        self.assertIsNone(owner._daily_chart_worker)
        self.assertIsNone(owner._pending_daily_chart)
        self.assertEqual([("B", date(2026, 9, 9), "detached:B")], started)

    def test_daily_rows_are_applied_to_history_with_analysis_only_for_fresh_result(self) -> None:
        daily_rows = (("2026-09-08T00:00", 1, 2, 1, 2, 10, 0.1),)
        minute_rows = (("2026-09-08T09:00", 1, 2, 1, 2, 10, 0.1),)
        history_values: list[object] = []
        dual_values: list[object] = []
        analyses: list[tuple[object, object]] = []
        active_episode = SimpleNamespace(summary=SimpleNamespace(stock_code="A"))
        owner = SimpleNamespace(
            _selected_history_code="A",
            _live_code="B",
            _active_episode=active_episode,
            _history_chart=SimpleNamespace(set_daily_rows=history_values.append),
            _history_daily_chart=SimpleNamespace(set_daily_rows=dual_values.append),
            _chart=SimpleNamespace(set_daily_rows=lambda _rows: None),
            _focus_selected_history=lambda: None,
            _sync_detached_charts=lambda: None,
            _load_selected_chart_rows=lambda: minute_rows,
            _show_trade_analysis=lambda episode, rows: analyses.append((episode, rows)),
        )

        JournalWindow._apply_daily_chart_rows(owner, "A", daily_rows, "history", refresh_analysis=False)
        JournalWindow._apply_daily_chart_rows(owner, "A", daily_rows, "history", refresh_analysis=True)

        self.assertEqual([daily_rows, daily_rows], history_values)
        self.assertEqual([daily_rows, daily_rows], dual_values)
        self.assertEqual([(active_episode, minute_rows)], analyses)

    def test_selected_history_refresh_reuses_one_bar_query_for_chart_and_analysis(self) -> None:
        rows = (("2026-09-08T09:00:00", 1, 2, 1, 2, 10, 0.1),)
        loaded: list[bool] = []
        chart_values: list[object] = []
        analyses: list[tuple[object, object]] = []
        active_episode = SimpleNamespace(summary=SimpleNamespace(stock_code="005930"))
        owner = SimpleNamespace(
            _selected_history_day=datetime(2026, 9, 8).date(),
            _selected_history_code="005930",
            _active_episode=active_episode,
            _history_chart=SimpleNamespace(set_rows=lambda value: chart_values.append(value)),
            _load_selected_chart_rows=lambda: loaded.append(True) or rows,
            _focus_selected_history=lambda: None,
            _show_trade_analysis=lambda episode, value: analyses.append((episode, value)),
        )

        JournalWindow._refresh_selected_history_after_bars(owner, "005930")

        self.assertEqual([True], loaded)
        self.assertEqual([rows], chart_values)
        self.assertEqual([(active_episode, rows)], analyses)

    def test_stale_empty_comparison_request_can_be_retried(self) -> None:
        code = "005930"
        started = datetime(2026, 9, 8, 9, 0)
        ended = datetime(2026, 9, 9, 15, 0)
        day = ended.date()
        daily_requests: list[tuple[str, object, str]] = []
        minute_requests: list[tuple[str, object, bool]] = []
        owner = SimpleNamespace(
            _query_client=SimpleNamespace(server_now=lambda: datetime(2026, 9, 15, 10)),
            _detached_chart_window=SimpleNamespace(selected_stock_codes=lambda: (code,)),
            _active_episode=SimpleNamespace(started_at=started, ended_at=ended),
            _compare_stock_requested={(code, day)},
            _worker=None,
            _repo=SimpleNamespace(load_chart_bars_range=lambda *_args: ()),
            _status=SimpleNamespace(setText=lambda _text: None),
            _sync_detached_charts=lambda: None,
            _start_daily_chart=lambda *args: daily_requests.append(args),
            _start_bar_confirmation=lambda stock_code, target_day, include_previous=False: minute_requests.append(
                (stock_code, target_day, include_previous)
            ),
        )

        JournalWindow._detached_source_changed(owner)

        self.assertEqual([(code, date(2026, 9, 15), f"detached:{code}")], daily_requests)
        self.assertEqual([(code, day, True)], minute_requests)
        self.assertIn((code, day), owner._compare_stock_requested)


class JournalReviewLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from PySide6.QtCore import QSettings, QTimer
        from kiwoom_monitor.infrastructure.persistence.database import Database
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.stack = ExitStack()
        self.now = as_kst(datetime(2026, 10, 20, 10, 30))
        self.origin = AccountScope("kiwoom", AccountEnvironment.REAL, "11111111-1111-4111-8111-111111111111")
        self.scope = AccountScope("kiwoom", AccountEnvironment.REAL, "33333333-3333-4333-8333-333333333333")
        self.other = AccountScope("kiwoom", AccountEnvironment.REAL, "22222222-2222-4222-8222-222222222222")
        monitor, journal = self.root / "monitor.sqlite3", self.root / "journal.sqlite3"
        Database(monitor).initialize()
        self.settings = QSettings(str(self.root / "window.ini"), QSettings.Format.IniFormat)
        self.settings.setValue("auto_history_sync_enabled", False)
        self.settings.setValue("auto_backfill", False)
        self.stack.enter_context(patch("kiwoom_monitor.journal_process.QSettings", return_value=self.settings))
        self.stack.enter_context(patch("kiwoom_monitor.journal_process.create_query_client",
            return_value=SimpleNamespace(server_now=lambda: self.now)))
        self.stack.enter_context(patch.object(QTimer, "singleShot"))
        self.stack.enter_context(patch("kiwoom_monitor.infrastructure.persistence.journal_trade_repository.kst_wall_now", return_value=self.now))
        self.window = JournalWindow(monitor, journal, self.root / "api.json")
        for name in ("_refresh_timer", "_confirm_timer", "_auto_backfill_timer", "_history_sync_timer",
                     "_enrichment_timer", "_central_sync_timer", "_account_reload_timer", "_history_followup_timer"):
            getattr(self.window, name).stop()
        # Review ownership stays real; chart/backfill work is outside this fixture.
        for name in ("_show_trade_analysis", "_sync_detached_charts", "_request_incomplete_trade_days",
                     "_request_previous_chart_day"):
            self.stack.enter_context(patch.object(self.window, name))
        self.exit = self.stack.enter_context(patch.object(self.app, "exit"))
        self.app.setProperty("journal_terminating", False)
        self.errors = []
        self.stack.enter_context(patch.object(sys, "excepthook", side_effect=lambda _type, error, _trace: self.errors.append(error)))
        first = (
            TradeFill("one", "005930", "fixture-a", "매수", datetime(2026, 10, 20, 9), 1, 100,
                      origin_scope=self.origin, canonical_scope=self.scope),
            TradeFill("two", "000660", "fixture-b", "매수", datetime(2026, 10, 20, 10), 1, 200,
                      origin_scope=self.origin, canonical_scope=self.scope),
        )
        other = (TradeFill("other", "005930", "other-account", "매수", datetime(2026, 10, 20, 9), 1, 300,
                           origin_scope=self.other),)
        self.window._repo.upsert_history_sync(first, (), now=self.now, account_scope=self.origin)
        self.window._repo.upsert_history_sync(other, (), now=self.now, account_scope=self.other)
        self.episodes = group_trade_episodes((*first, *other))
        for episode in self.episodes:
            original = episode.fills[0].origin_scope
            canonical = episode.summary.account_scope if original != episode.summary.account_scope else None
            self.window._repo.save_review(TradeReview(episode.group_id, "stored reason", "stored review", "stored tags",
                "보통", "작성 중", original, canonical), now=self.now, account_scope=original)
        self.window._refresh_account_choices(self.scope)
        self.window.reload_history()
        self.window._history_followup_timer.stop()
        self.save = self.stack.enter_context(patch.object(self.window._repo, "save_review", wraps=self.window._repo.save_review))

    def tearDown(self):
        from PySide6.QtCore import QCoreApplication, QEvent
        try:
            self.trigger(False)
            self.window._review_save_timer.stop()
            self.window.shutdown()
            self.window.deleteLater()
            QCoreApplication.sendPostedEvents(self.window, QEvent.Type.DeferredDelete)
            self.app.processEvents()
        finally:
            self.app.setProperty("journal_terminating", False)
            self.stack.close()
            self.temp.cleanup()

    def trigger(self, enabled):
        with closing(sqlite3.connect(self.window._journal_db)) as connection, connection:
            if enabled:
                connection.execute("CREATE TRIGGER deny_review BEFORE INSERT ON trade_reviews "
                    "BEGIN SELECT RAISE(ABORT, 'controlled review rollback'); END")
            else:
                connection.execute("DROP TRIGGER IF EXISTS deny_review")

    def edit(self, text="unfinished note"):
        from PySide6.QtTest import QTest
        self.window._review_reason.setPlainText(" edited reason ")
        self.window._review_note.clear()
        QTest.keyClicks(self.window._review_note, text)
        self.window._review_tags.setText(" edited tags ")
        self.window._review_rating.setCurrentText("아쉬움")
        self.window._review_status.setCurrentText("복기 완료")
        self.assertTrue(self.window._review_dirty)
        self.assertTrue(self.window._review_save_timer.isActive())

    def stored(self, episode):
        return self.window._repo.load_review(episode.group_id, episode.summary.account_scope)

    def test_close_before_debounce_commits_native_review_once_with_original_scope(self):
        episode = self.window._active_episode
        self.window.show()
        self.edit()
        self.assertTrue(self.window.close())
        self.assertEqual(1, self.save.call_count)
        self.assertFalse(self.window._review_dirty)
        self.assertFalse(self.window._review_save_timer.isActive())
        review = self.stored(episode)
        self.assertEqual(("edited reason", "unfinished note", "edited tags", "아쉬움", "복기 완료"),
                         (review.reason, review.review, review.tags, review.rating, review.status))
        self.assertEqual((self.origin, self.scope), (review.origin_scope, review.canonical_scope))
        self.assertEqual(self.origin, self.save.call_args.kwargs["account_scope"])
        self.window.shutdown()
        self.assertEqual(1, self.save.call_count)
        self.exit.assert_called_once_with(0)

    def test_actual_debounce_commits_once_and_clean_shutdown_adds_no_write(self):
        from qt_settings_test_support import wait_until
        episode = self.window._active_episode
        self.edit("debounced note")
        self.assertEqual(700, self.window._review_save_timer.interval())
        wait_until(lambda: self.save.call_count == 1)
        self.assertFalse(self.window._review_dirty)
        self.assertEqual("debounced note", self.stored(episode).review)
        self.window.shutdown()
        self.assertEqual(1, self.save.call_count)

    def test_native_review_failure_aborts_actual_close_and_explicit_retry_keeps_draft(self):
        episode = self.window._active_episode
        before = self.stored(episode)
        self.window.show()
        self.edit("failed close draft")
        self.trigger(True)
        try:
            closed = self.window.close()
        except sqlite3.IntegrityError as error:
            self.errors.append(error)
            closed = False
        self.assertFalse(closed)
        self.assertTrue(self.window.isVisible())
        self.assertEqual(1, self.save.call_count)
        self.assertTrue(any(isinstance(error, sqlite3.IntegrityError) for error in self.errors))
        self.assertEqual(before, self.stored(episode))
        self.assertIs(episode, self.window._active_episode)
        self.assertTrue(self.window._review_dirty)
        self.assertEqual("failed close draft", self.window._review_note.toPlainText())
        self.assertTrue(self.window._review_saved.text().startswith("저장 실패"))
        self.assertFalse(self.window._shutting_down)
        self.assertFalse(bool(self.app.property("journal_terminating")))
        self.exit.assert_not_called()
        self.trigger(False)
        self.assertTrue(self.window.close())
        self.assertEqual(2, self.save.call_count)
        self.assertEqual("failed close draft", self.stored(episode).review)

    def test_dirty_same_episode_refresh_and_cross_episode_retry_do_not_overwrite_another_form(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QPushButton
        episode = self.window._active_episode
        self.edit("retained selection draft")
        self.window._show_selected_summary()
        self.assertTrue(self.window._review_dirty)
        self.assertEqual("retained selection draft", self.window._review_note.toPlainText())
        self.assertEqual(0, self.save.call_count)
        target_row = 1 - self.window._summary_table.currentRow()
        other = self.window._summary_table.item(target_row, 0).data(Qt.ItemDataRole.UserRole)
        self.trigger(True)
        self.window._summary_table.setCurrentCell(target_row, 0)
        self.assertTrue(self.errors)
        self.assertIs(episode, self.window._active_episode)
        self.assertTrue(self.window._review_dirty)
        self.trigger(False)
        save_button = next(button for button in self.window.findChildren(QPushButton) if button.text() == "지금 저장")
        save_button.click()
        self.assertEqual("retained selection draft", self.stored(episode).review)
        self.assertEqual("stored review", self.stored(other).review)
        self.assertEqual("작성 중", self.window._summary_table.item(target_row, 13).text())
        self.window._show_selected_summary()
        self.assertEqual(other.group_id, self.window._active_episode.group_id)
        self.assertEqual("stored review", self.window._review_note.toPlainText())

    def test_account_change_flushes_original_review_before_active_episode_is_cleared(self):
        from qt_settings_test_support import wait_until
        episode = self.window._active_episode
        self.edit("original account draft")
        target = next(index for index in range(self.window._account_filter.count())
                      if self.window._account_filter.itemData(index) == self.other)
        self.window._account_filter.setCurrentIndex(target)
        wait_until(lambda: self.window._loaded_history_scope == self.other)
        self.window._history_followup_timer.stop()
        self.assertEqual("original account draft", self.stored(episode).review)
        self.assertEqual(1, self.save.call_count)
        self.assertEqual(self.origin, self.save.call_args.kwargs["account_scope"])
        self.assertEqual(self.other, self.window._active_episode.summary.account_scope)
        self.assertEqual("stored review", self.window._review_note.toPlainText())

    def test_failed_account_change_restores_loaded_scope_and_preserves_draft_until_retry(self):
        from qt_settings_test_support import wait_until
        episode = self.window._active_episode
        before = self.stored(episode)
        self.edit("failed account draft")
        target = next(index for index in range(self.window._account_filter.count())
                      if self.window._account_filter.itemData(index) == self.other)
        self.trigger(True)
        self.window._account_filter.setCurrentIndex(target)
        self.assertTrue(any(isinstance(error, sqlite3.IntegrityError) for error in self.errors))
        self.assertEqual(self.scope, self.window._selected_account_scope())
        self.assertEqual(self.scope, self.window._loaded_history_scope)
        self.assertIs(episode, self.window._active_episode)
        self.assertEqual(before, self.stored(episode))
        self.assertTrue(self.window._review_dirty)
        self.assertEqual("failed account draft", self.window._review_note.toPlainText())
        self.assertFalse(self.window._account_reload_timer.isActive())
        self.trigger(False)
        self.window._account_filter.setCurrentIndex(target)
        wait_until(lambda: self.window._loaded_history_scope == self.other)
        self.window._history_followup_timer.stop()
        self.assertEqual("failed account draft", self.stored(episode).review)
        self.assertEqual(2, self.save.call_count)

    def _assert_process_exit_waits_for_review(self, mode):
        from PySide6.QtCore import QTimer
        from kiwoom_monitor import journal_process
        command_path = self.root / "commands.json"
        command = {"request_id": 1, "action": mode}
        command_path.write_text(json.dumps(command), encoding="utf-8")
        episode = self.window._active_episode
        before = self.stored(episode)
        self.edit(f"{mode} exit draft")
        self.trigger(True)
        command_timers = []
        previous_hook = sys.excepthook
        previous_last_window = self.app.quitOnLastWindowClosed()
        ticks = iter((0.0, 6.0, 6.0))

        def command_timer():
            timer = QTimer()
            command_timers.append(timer)
            return timer

        def drive_process_events():
            # Exercise the real nested poll callback through its actual Qt signal.
            sys.excepthook = lambda _type, error, _trace: self.errors.append(error)
            timer = command_timers[0]
            timer.timeout.emit()
            if mode == "parent-loss":
                timer.timeout.emit()
            self.assertEqual(1, self.save.call_count)
            self.assertTrue(any(isinstance(error, sqlite3.IntegrityError) for error in self.errors))
            self.assertEqual(before, self.stored(episode))
            self.assertTrue(self.window._review_dirty)
            self.assertFalse(self.window._shutting_down)
            self.assertFalse(bool(self.app.property("journal_terminating")))
            self.exit.assert_not_called()
            self.assertEqual(mode != "parent-loss", timer.isActive())
            if mode != "parent-loss":
                timer.timeout.emit()  # The already-consumed command does not automatically retry.
                self.assertEqual(1, self.save.call_count)
            self.trigger(False)
            if mode == "parent-loss":
                self.assertTrue(self.window.close())  # Existing explicit UI retry after parent loss.
            else:
                command_path.write_text(json.dumps({"request_id": 2, "action": mode}), encoding="utf-8")
                timer.timeout.emit()
            self.assertEqual(2, self.save.call_count)
            self.assertEqual(f"{mode} exit draft", self.stored(episode).review)
            self.assertFalse(self.window._review_dirty)
            self.assertTrue(bool(self.app.property("journal_terminating")))
            self.assertFalse(timer.isActive())
            self.exit.assert_called_once_with(0)
            return 0

        try:
            with patch.object(journal_process, "QApplication", return_value=self.app) as application, \
                    patch.object(journal_process, "JournalWindow", return_value=self.window), \
                    patch.object(journal_process, "QTimer", side_effect=command_timer), \
                    patch.object(journal_process, "_set_taskbar_app_id"), \
                    patch.object(journal_process, "_parent_is_alive", return_value=mode != "parent-loss"), \
                    patch.object(journal_process, "monotonic_time", SimpleNamespace(monotonic=lambda: next(ticks))), \
                    patch.object(self.app, "exec", side_effect=drive_process_events):
                application.instance.return_value = self.app
                result = journal_process._run_journal_process([
                    "--monitor-database", str(self.window._monitor_db), "--journal-database", str(self.window._journal_db),
                    "--config", str(self.window._config), "--command-file", str(command_path), "--parent-pid", "1",
                ])
                self.assertEqual(0, result)
        finally:
            sys.excepthook = previous_hook
            self.app.setQuitOnLastWindowClosed(previous_last_window)
            for timer in command_timers:
                timer.stop()
                timer.deleteLater()

    def test_command_shutdown_does_not_ack_failed_review_and_new_request_retries(self):
        self._assert_process_exit_waits_for_review("shutdown")

    def test_sync_exit_does_not_ack_failed_review_and_new_request_retries(self):
        self._assert_process_exit_waits_for_review("sync")

    def test_parent_loss_does_not_ack_failed_review_or_automatically_retry(self):
        self._assert_process_exit_waits_for_review("parent-loss")


if __name__ == "__main__":
    unittest.main()
