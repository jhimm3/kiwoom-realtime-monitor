from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace
from pathlib import Path
from contextlib import closing
from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope, LEGACY_ACCOUNT_SCOPE
from unittest.mock import Mock, patch

from kiwoom_monitor.domain.time_basis import as_kst
from kiwoom_monitor.journal_process import JournalWindow
from kiwoom_monitor.application.trade_history_service import TradeFill
from kiwoom_monitor.application.trade_journal_summary import TradeReview, group_trade_episodes
from kiwoom_monitor.infrastructure.kiwoom_rest.account_query import AccountQueryContext


class JournalDetachedFlowTests(unittest.TestCase):
    def test_window_analysis_and_enrichment_share_current_client_daily_receipt(self) -> None:
        from PySide6.QtCore import QEvent, QSettings, Qt, QTimer
        from PySide6.QtWidgets import QApplication
        from qt_settings_test_support import wait_until
        from kiwoom_monitor.infrastructure.persistence.database import Database
        from kiwoom_monitor.infrastructure.persistence.market_data_metadata_repository import MarketDataMetadataRepository
        from kiwoom_monitor.domain.market_data_contract import MarketDatasetKind
        from kiwoom_monitor.presentation.journal_workers import AnalysisEnrichmentWorker

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
            initial = Mock(return_value=as_kst(datetime(2030, 1, 2, 3, 4, 5)))
            with patch("kiwoom_monitor.journal_process.QSettings", return_value=settings), \
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
                        detached = SimpleNamespace(selected_stock_codes=lambda: ("005930",),
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
                _detached_chart_window=SimpleNamespace(selected_stock_codes=lambda: ("005930",),
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
                upsert_history_sync=lambda saved_fills, saved_costs, *, account_scope: calls.append(
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


if __name__ == "__main__":
    unittest.main()
