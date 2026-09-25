from __future__ import annotations

import gc
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QPushButton, QToolBar

from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.presentation.main_window import MainWindow, NxtMarkerDelegate, selected_high_cycle_periods
from kiwoom_monitor.presentation.theme_dialogs import ThemeEditDialog
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import TradeTick
from kiwoom_monitor.application.daily_high_service import DailyHighTargets
from kiwoom_monitor.application.trade_strength import StockFundamentals
from kiwoom_monitor.application.minute_trade_value import MinuteOhlcv
from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope, LEGACY_ACCOUNT_SCOPE


class FakeRankingLoader:
    def load_top_stocks(self) -> tuple[object, ...]:
        class Stock:
            rank = 1
            code = "005930"
            name = "삼성전자"
            new_high_label = "5일"
            change_rate = "+1.23"

        return (Stock(),)


class MainWindowTest(unittest.TestCase):
    def test_followups_start_once_per_applied_ranking_and_ignore_stale_subscription(self) -> None:
        started: list[tuple[str, ...]] = []
        execution = SimpleNamespace(priority_preparing=True)
        owner = SimpleNamespace(
            _closing=False, _ranking_execution=execution,
            _row_by_code={"005930": 0, "000660": 1},
            _ranking_followup_revision=1, _started_followup_revision=-1,
            _start_secondary_loading=lambda codes: started.append(codes),
        )
        codes = ("005930", "000660")
        MainWindow._start_realtime_followups(owner, codes)
        self.assertEqual([], started)
        execution.priority_preparing = False
        MainWindow._start_realtime_followups(owner, ("005930",))
        self.assertEqual([], started)
        MainWindow._start_realtime_followups(owner, codes)
        MainWindow._start_realtime_followups(owner, codes)
        self.assertEqual([codes], started)
        owner._ranking_followup_revision = 2
        MainWindow._start_realtime_followups(owner, codes)
        self.assertEqual([codes, codes], started)

    def test_theme_confirmation_modal_does_not_defer_live_table_updates(self) -> None:
        app = QApplication.instance() or QApplication([])
        editor = ThemeEditDialog("삼성전자", ("반도체",), ",/|;")
        confirmation = QMessageBox(editor)
        owner = SimpleNamespace(_image_theme_workflow_active=False)
        with patch.object(QApplication, "activeModalWidget", return_value=confirmation):
            self.assertFalse(MainWindow._has_blocking_modal(owner))
        with patch.object(QApplication, "activeModalWidget", return_value=QMessageBox()):
            self.assertTrue(MainWindow._has_blocking_modal(owner))
            owner._theme_confirmation_active = True
            self.assertFalse(MainWindow._has_blocking_modal(owner))
        confirmation.close()
        editor.close()

    def test_price_cache_flush_includes_latest_realtime_market_cap(self) -> None:
        queued = []
        writer = SimpleNamespace(
            enqueue_price_cache=lambda *args: queued.append(args),
        )
        now = datetime(2026, 9, 21, 10, 15)
        owner = SimpleNamespace(
            _pending_price_cache={"005930": 71_000},
            _pending_today_high_cache={"005930": 72_000},
            _pending_market_cap_cache={"005930": 4_200_000.0},
            _market_cache_writer=writer,
            _ranking_now=lambda: now,
        )

        MainWindow._save_current_price_cache(owner)

        self.assertEqual(
            [(
                {"005930": 71_000}, {"005930": 72_000},
                {"005930": 4_200_000.0}, now.date(),
            )],
            queued,
        )
        self.assertEqual({}, owner._pending_price_cache)
        self.assertEqual({}, owner._pending_today_high_cache)
        self.assertEqual({}, owner._pending_market_cap_cache)

    def test_api_label_prioritizes_actual_ranking_local_fallback(self) -> None:
        shown = []
        owner = SimpleNamespace(
            _active_api_route="central",
            _ranking_uses_local_fallback=True,
            _set_api_status=lambda text, color: shown.append((text, color)),
        )

        MainWindow._set_connected_api_status(owner)

        self.assertEqual(("API: 로컬 전환", "#B36B00"), shown[-1])

    def test_api_label_keeps_nas_source_while_realtime_is_waiting(self) -> None:
        shown = []
        owner = SimpleNamespace(
            _active_api_route="central_waiting",
            _ranking_uses_local_fallback=False,
            _set_api_status=lambda text, color: shown.append((text, color)),
        )

        MainWindow._set_connected_api_status(owner)

        self.assertEqual(("API: NAS", "#008000"), shown[-1])

    def test_stored_market_cap_does_not_overwrite_newer_live_tick(self) -> None:
        rendered = []
        owner = SimpleNamespace(
            _market_cap_reference_pending={"005930", "000660"},
            _market_cap_reference_codes=set(),
            _realtime_market_caps={"005930": 5_000_000.0},
            _render_market_cap=lambda code: rendered.append(code),
        )

        MainWindow._apply_market_cap_references(owner, ("005930", "000660"), {
            "005930": {"market_cap_eok": 4_000_000},
            "000660": {"market_cap_eok": 900_000},
        })

        self.assertEqual(5_000_000.0, owner._realtime_market_caps["005930"])
        self.assertEqual(900_000.0, owner._realtime_market_caps["000660"])
        self.assertEqual({"005930", "000660"}, owner._market_cap_reference_codes)
        self.assertEqual(set(), owner._market_cap_reference_pending)
        self.assertEqual(["005930", "000660"], rendered)

    def test_stored_market_cap_from_previous_api_runtime_is_discarded(self) -> None:
        old_client, current_client = object(), object()
        owner = SimpleNamespace(
            _market_data_client=current_client,
            _market_cap_reference_pending=set(),
            _market_cap_reference_codes=set(),
            _realtime_market_caps={},
            _render_market_cap=lambda _code: self.fail("old runtime result was rendered"),
        )

        MainWindow._apply_market_cap_references(
            owner, ("005930",), {"005930": {"market_cap_eok": 4_000_000}}, old_client,
        )

        self.assertEqual({}, owner._realtime_market_caps)

    def test_api_reload_discards_late_ranking_response(self) -> None:
        owner = SimpleNamespace(_api_reloading=True)

        MainWindow._on_ranking_loaded(owner, (object(),))

        self.assertTrue(owner._api_reloading)

    def test_ranking_timer_does_not_fire_before_the_scheduled_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Database(Path(temporary_directory) / "monitor.sqlite3")
            database.initialize()
            window = MainWindow(database.settings)

            self.assertEqual(Qt.TimerType.PreciseTimer, window._ranking_timer.timerType())
            window.close()

    def test_journal_news_relay_preserves_late_request_scope_after_account_switch(self) -> None:
        requested = AccountScope(
            "kiwoom", AccountEnvironment.REAL,
            "11111111-1111-4111-8111-111111111111",
        )
        current = AccountScope(
            "kiwoom", AccountEnvironment.MOCK,
            "22222222-2222-4222-8222-222222222222",
        )
        calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        owner = SimpleNamespace(
            _journal_news_inbox=SimpleNamespace(read_new=lambda: {
                "code": "005930", "name": "삼성전자", "group_id": "group-1",
                "trade_date": "2026-09-13", "origin_scope": requested.to_dict(),
                "account_scope": requested.to_dict(),
            }),
            _current_account_scope=current,
            _ensure_news_process=lambda: None,
            _send_news_command=lambda *args, **kwargs: calls.append((args, kwargs)),
        )

        MainWindow._poll_journal_news_request(owner)

        self.assertEqual(requested, calls[0][1]["origin_scope"])
        self.assertEqual(requested, calls[0][1]["account_scope"])

    def test_journal_news_relay_keeps_scope_free_legacy_and_rejects_partial_scope(self) -> None:
        sent: list[dict[str, object]] = []
        documents = iter((
            {"code": "005930", "name": "삼성전자", "group_id": "legacy"},
            {
                "code": "005930", "name": "삼성전자", "group_id": "invalid",
                "origin_scope": LEGACY_ACCOUNT_SCOPE.to_dict(),
            },
        ))
        owner = SimpleNamespace(
            _journal_news_inbox=SimpleNamespace(read_new=lambda: next(documents)),
            _ensure_news_process=lambda: None,
            _send_news_command=lambda *_args, **kwargs: sent.append(kwargs),
        )

        MainWindow._poll_journal_news_request(owner)
        MainWindow._poll_journal_news_request(owner)

        self.assertEqual(1, len(sent))
        self.assertEqual(LEGACY_ACCOUNT_SCOPE, sent[0]["origin_scope"])
        self.assertEqual(LEGACY_ACCOUNT_SCOPE, sent[0]["account_scope"])

    def test_theme_change_forces_google_settings_and_theme_backup(self) -> None:
        scheduled: list[str] = []
        refreshed: list[bool] = []
        badge_refreshes: list[bool] = []
        owner = SimpleNamespace(
            _theme_store=SimpleNamespace(all_by_name=lambda: {"005930": ("반도체",)}),
            _refresh_theme_badges=lambda: badge_refreshes.append(True),
            _refresh_rankings=lambda: refreshed.append(True),
            _schedule_google_drive_upload=scheduled.append,
        )

        MainWindow._on_themes_changed(owner)

        self.assertEqual({"005930": ("반도체",)}, owner._themes)
        self.assertEqual([True], badge_refreshes)
        self.assertEqual([True], refreshed)
        self.assertEqual(["both"], scheduled)

    def test_offline_google_change_remains_marked_for_later_upload(self) -> None:
        saved: dict[str, str] = {}
        owner = SimpleNamespace(
            _google_drive_sync=SimpleNamespace(connected=False),
            _closing=False,
            _google_drive_pending_target="",
            _google_drive_dirty=False,
            _settings=SimpleNamespace(set=lambda key, value: saved.__setitem__(key, value), get=lambda _key: "1"),
            _google_drive_debounce=SimpleNamespace(start=lambda: self.fail("offline change must not start upload")),
            _google_drive_timestamp_now=lambda: "2026-09-12T00:00:00Z",
        )

        MainWindow._schedule_google_drive_upload(owner, "both")

        self.assertTrue(owner._google_drive_dirty)
        self.assertEqual("both", owner._google_drive_pending_target)
        self.assertEqual("1", saved["google_drive_unsynced_changes"])

    def test_dirty_google_upload_sends_settings_and_current_themes_together(self) -> None:
        started: list[tuple[object, ...]] = []
        controller = SimpleNamespace(
            is_running=False,
            start=lambda *args: started.append(args) or True,
        )
        owner = SimpleNamespace(
            _google_drive_sync=SimpleNamespace(configured=True, connected=True),
            _google_drive_worker_controller=controller,
            _google_drive_pending_target="",
            _google_drive_close_pending=False,
            _google_drive_operation="",
            _google_drive_show_completion=False,
            _google_drive_active_target="",
            _settings=SimpleNamespace(get=lambda key: "settings" if key == "google_drive_sync_target" else ""),
            _has_newer_local_google_drive_changes=lambda: True,
            statusBar=lambda: SimpleNamespace(showMessage=lambda _message: None),
        )

        MainWindow._start_google_drive_sync(owner, "upload")

        self.assertEqual("both", started[0][2])
        self.assertEqual("both", owner._google_drive_active_target)

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        # close()만으로는 Qt C++ 객체가 즉시 파괴되지 않는다. 여러 메인 창이
        # 누적되면 뒤쪽 테스트에서 네이티브 종료가 발생하므로 지연 삭제까지
        # 처리한다. 제품 창의 종료 동작은 변경하지 않는다.
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, MainWindow):
                widget.close()
                widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        gc.collect()

    def test_main_window_has_default_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Database(Path(temporary_directory) / "monitor.sqlite3")
            database.initialize()
            window = MainWindow(database.settings, FakeRankingLoader())

            self.assertEqual(window.windowTitle(), "키움 실시간 모니터 (테스트)")
            table = window.centralWidget().findChild(type(window._table))
            self.assertEqual(table.columnCount(), 16)
            self.assertFalse(table.horizontalHeader().stretchLastSection())
            toolbar = window.findChild(QToolBar, "main_tools_toolbar")
            version_label = window.findChild(QLabel, "main_version_label")
            settings_button = window.findChild(QPushButton, "main_settings_button")
            candidate_button = window.findChild(QPushButton, "candidate_monitor_button")
            research_button = window.findChild(QPushButton, "research_button")
            automation_button = window.findChild(QPushButton, "mock_automation_button")
            widgets = [toolbar.widgetForAction(action) for action in toolbar.actions()]
            self.assertEqual(widgets.index(settings_button), widgets.index(version_label) + 1)
            self.assertEqual((settings_button.width(), settings_button.height()), (24, 22))
            self.assertEqual(candidate_button.text(), "")
            self.assertEqual(research_button.text(), "")
            self.assertEqual(automation_button.text(), "")
            self.assertEqual((candidate_button.width(), candidate_button.height()), (14, 14))
            self.assertEqual((research_button.width(), research_button.height()), (14, 14))
            self.assertEqual((automation_button.width(), automation_button.height()), (14, 14))
            self.assertEqual(widgets.index(candidate_button) + 1, widgets.index(research_button))
            self.assertEqual(widgets.index(research_button) + 1, widgets.index(automation_button))
            self.assertEqual(widgets.index(automation_button) + 1, widgets.index(window._rank_query_selector))
            window._refresh_rankings()
            window._ranking_worker.wait()
            QApplication.processEvents()
            self.assertEqual("삼성전자", table.item(0, 1).text())
            window._on_trade_tick(TradeTick("005930", 71000, None, None, 1, 71000, None, 1.45))
            window._flush_trade_tick_updates()
            self.assertEqual("71,000", table.item(0, 5).text())
            self.assertEqual("+1.45%", table.item(0, 3).text())
            self.assertEqual("-", table.item(0, 13).text())
            window._refresh_rankings()
            window._ranking_worker.wait()
            QApplication.processEvents()
            self.assertEqual("71,000", table.item(0, 5).text())
            window.close()

    def test_google_drive_upload_targets_exist_before_deferred_upload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Database(Path(temporary_directory) / "monitor.sqlite3")
            database.initialize()
            window = MainWindow(database.settings, FakeRankingLoader())

            self.assertEqual("", window._google_drive_pending_target)
            self.assertEqual("", window._google_drive_active_target)
            window.close()

    def test_upper_limit_uses_actual_limit_price_and_can_be_hidden(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Database(Path(temporary_directory) / "monitor.sqlite3")
            database.initialize()
            window = MainWindow(database.settings, FakeRankingLoader())
            window._refresh_rankings()
            window._ranking_worker.wait()
            QApplication.processEvents()

            window._fundamentals["005930"] = StockFundamentals(
                1_000, 50, upper_limit_price=91_900,
            )
            window._current_prices["005930"] = 91_900
            window._today_high_prices["005930"] = 91_900
            window._last_change_rates["005930"] = 29.97
            window._render_change_rate("005930")

            item = window._table.item(0, 3)
            self.assertEqual("+29.97%", item.text())
            self.assertEqual(window.UPPER_LIMIT_BADGE_COLOR.casefold(), item.background().color().name().casefold())
            self.assertTrue(item.font().bold())
            self.assertIsNone(window._table.cellWidget(0, 3))

            window._last_change_rates["005930"] = 0.0
            window._render_change_rate("005930")
            self.assertFalse(window._table.item(0, 3).font().bold())
            self.assertNotEqual(
                window.UPPER_LIMIT_BADGE_COLOR.casefold(),
                window._table.item(0, 3).background().color().name().casefold(),
            )

            window._current_prices["005930"] = 90_000
            window._last_change_rates["005930"] = 27.28
            window._render_change_rate("005930")
            self.assertEqual("+27.28%", window._table.item(0, 3).text())
            self.assertFalse(window._table.item(0, 3).font().bold())
            self.assertIsNone(window._table.cellWidget(0, 3))

            window._current_prices["005930"] = 91_900
            database.settings.set("upper_limit_highlight_enabled", "0")
            window._render_change_rate("005930")
            self.assertEqual("+27.28%", window._table.item(0, 3).text())
            self.assertFalse(window._table.item(0, 3).font().bold())
            self.assertIsNone(window._table.cellWidget(0, 3))
            window.close()

    def test_high_header_cycles_only_selected_periods(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Database(Path(temporary_directory) / "monitor.sqlite3")
            database.initialize()
            database.settings.set("high_header_cycle_periods", "250,historical")
            database.settings.set("high_distance_period", "250")
            window = MainWindow(database.settings, FakeRankingLoader())

            window._toggle_table_header_mode(13)
            self.assertEqual("historical", database.settings.get("high_distance_period"))
            window._toggle_table_header_mode(13)
            self.assertEqual("250", database.settings.get("high_distance_period"))
            window.close()

    def test_failed_realtime_minute_save_restores_pending_values(self) -> None:
        class FailingRepository:
            def upsert_many(self, values) -> None:
                raise OSError("temporary failure")

            def upsert_market_index_minutes(self, values) -> None:
                raise AssertionError("stock save must fail first")

        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Database(Path(temporary_directory) / "monitor.sqlite3")
            database.initialize()
            window = MainWindow(database.settings)
            minute = datetime(2026, 9, 10, 10, 15)
            bar = MinuteOhlcv(minute, 100, 102, 99, 101, 10, 0.5)
            window._minute_bar_repository = FailingRepository()
            window._pending_minute_bars = {("005930", minute): bar}
            window._pending_market_index_bars = {
                ("kospi", minute): (2800.0, 2801.0, 2799.0, 2800.5, 100.0)
            }

            window._flush_pending_minute_bars()

            self.assertEqual(bar, window._pending_minute_bars[("005930", minute)])
            self.assertIn(("kospi", minute), window._pending_market_index_bars)
            window.close()

    def test_background_cache_failures_restore_work_without_overwriting_newer_values(self) -> None:
        class Timer:
            def __init__(self) -> None:
                self.started = 0

            def isActive(self) -> bool:
                return False

            def start(self) -> None:
                self.started += 1

        old_minute = datetime(2026, 9, 14, 10, 30)
        new_minute = datetime(2026, 9, 14, 10, 31)
        old_bar = MinuteOhlcv(old_minute, 100, 102, 99, 101, 10, 0.5)
        newer_bar = MinuteOhlcv(old_minute, 100, 103, 99, 102, 20, 1.0)
        minute_timer = Timer()
        owner = SimpleNamespace(
            _pending_minute_bars={("005930", old_minute): newer_bar},
            _pending_market_index_bars={},
            _pending_price_cache={"005930": 102},
            _pending_market_cap_cache={"005930": 2_100_000.0},
            _pending_today_high_cache={"005930": 103},
            _minute_bar_save_timer=minute_timer,
            _price_cache_timer=Timer(),
            _closing=False,
        )

        MainWindow._on_minute_cache_write_failed(
            owner,
            {
                ("005930", old_minute): old_bar,
                ("000660", new_minute): MinuteOhlcv(
                    new_minute, 200, 204, 198, 203, 30, 2.0,
                ),
            },
            {("kospi", old_minute): (2800.0, 2801.0, 2799.0, 2800.5, 100.0)},
            "temporary failure",
        )
        MainWindow._on_price_cache_write_failed(
            owner,
            {"005930": 101, "000660": 203},
            {"005930": 102, "000660": 204},
            {"005930": 2_000_000.0, "000660": 900_000.0},
            object(),
            "temporary failure",
        )

        self.assertEqual(newer_bar, owner._pending_minute_bars[("005930", old_minute)])
        self.assertIn(("000660", new_minute), owner._pending_minute_bars)
        self.assertIn(("kospi", old_minute), owner._pending_market_index_bars)
        self.assertEqual({"005930": 102, "000660": 203}, owner._pending_price_cache)
        self.assertEqual({"005930": 103, "000660": 204}, owner._pending_today_high_cache)
        self.assertEqual(
            {"005930": 2_100_000.0, "000660": 900_000.0},
            owner._pending_market_cap_cache,
        )
        self.assertEqual(1, minute_timer.started)
        self.assertEqual(1, owner._price_cache_timer.started)

    def test_failed_history_save_does_not_mark_code_as_loaded(self) -> None:
        class FailingRepository:
            def upsert_bars(self, code, bars) -> None:
                raise OSError("temporary failure")

            def purge_before(self, value) -> None:
                pass

        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Database(Path(temporary_directory) / "monitor.sqlite3")
            database.initialize()
            window = MainWindow(database.settings)
            window._minute_bar_repository = FailingRepository()
            now = window._ranking_now()
            bars = (MinuteOhlcv(now.replace(second=0, microsecond=0), 100, 102, 99, 101, 10),)

            window._on_history_received("005930", bars)

            self.assertNotIn("005930", window._minute_history_codes)
            window.close()

    def test_fundamentals_refresh_keeps_combined_adjusted_high_in_memory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Database(Path(temporary_directory) / "monitor.sqlite3")
            database.initialize()
            window = MainWindow(database.settings)
            window._fundamentals["000660"] = StockFundamentals(1_000, 50, 3_002_000)
            window._daily_highs["000660"] = DailyHighTargets(None, None, None)

            window._on_fundamentals_received(
                "000660", StockFundamentals(1_100, 45, 2_987_000)
            )

            self.assertEqual(3_002_000, window._fundamentals["000660"].high_250_price)
            self.assertEqual(1_100, window._fundamentals["000660"].market_cap_eok)
            window.close()

    def test_fundamentals_refresh_prefers_current_daily_adjusted_high(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Database(Path(temporary_directory) / "monitor.sqlite3")
            database.initialize()
            window = MainWindow(database.settings)
            window._fundamentals["000660"] = StockFundamentals(1_000, 50, 3_100_000)
            window._daily_highs["000660"] = DailyHighTargets(None, None, 3_002_000)

            window._on_fundamentals_received(
                "000660", StockFundamentals(1_100, 45, 2_987_000)
            )

            self.assertEqual(3_002_000, window._fundamentals["000660"].high_250_price)
            window.close()

    def test_theme_header_groups_common_theme_and_sorts_group_by_change_rate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Database(Path(temporary_directory) / "monitor.sqlite3")
            database.initialize()
            window = MainWindow(database.settings, themes={"가": "AI", "나": "AI", "다": "바이오"})

            class Stock:
                new_high_periods = ()
                current_price = None

                def __init__(self, rank: int, code: str, name: str, change: str) -> None:
                    self.rank, self.code, self.name, self.change_rate = rank, code, name, change

            window._on_ranking_loaded((
                Stock(1, "000001", "가", "+1.00"),
                Stock(2, "000002", "나", "+3.00"),
                Stock(3, "000003", "다", "+5.00"),
            ))
            window._toggle_table_header_mode(2)
            self.assertEqual(["나", "가", "다"], [window._table.item(row, 1).text() for row in range(3)])
            window._toggle_table_header_mode(2)
            self.assertEqual(["가", "나", "다"], [window._table.item(row, 1).text() for row in range(3)])
            window.close()

    def test_equal_theme_counts_are_sorted_by_selected_trade_value_period(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Database(Path(temporary_directory) / "monitor.sqlite3")
            database.initialize()
            database.settings.set("theme_trade_summary_period", "day")
            window = MainWindow(database.settings, themes={"가": "반도체", "나": "반도체", "다": "원전", "라": "원전"})

            class Stock:
                new_high_periods = ()
                current_price = None

                def __init__(self, rank: int, code: str, name: str, change: str) -> None:
                    self.rank, self.code, self.name, self.change_rate = rank, code, name, change

            trade_values = {"000001": 3.0, "000002": 2.0, "000003": 12.0, "000004": 8.0}
            window._minute_aggregator.today_trade_value_eok = lambda code, _now=None: trade_values[code]
            window._on_ranking_loaded((
                Stock(1, "000001", "가", "+4.00"),
                Stock(2, "000002", "나", "+2.00"),
                Stock(3, "000003", "다", "+3.00"),
                Stock(4, "000004", "라", "+1.00"),
            ))
            window._toggle_table_header_mode(2)
            self.assertEqual(["다", "라", "가", "나"], [window._table.item(row, 1).text() for row in range(4)])
            window.close()

    def test_top20_minute_rows_can_be_aggregated_to_five_minutes(self) -> None:
        rows = [
            (datetime(2026, 9, 8, 9, 1), 1.0, 2.0, 0.0),
            (datetime(2026, 9, 8, 9, 4), 3.0, 4.0, 1.0),
            (datetime(2026, 9, 8, 9, 5), 5.0, 6.0, 0.0),
        ]
        self.assertEqual(
            [
                (datetime(2026, 9, 8, 9, 0), 4.0, 6.0, 1.0),
                (datetime(2026, 9, 8, 9, 5), 5.0, 6.0, 0.0),
            ],
            MainWindow._aggregate_top20_rows(rows, 5),
        )

    def test_nas_top20_rows_keep_only_completed_market_splits(self) -> None:
        rows = MainWindow._nas_top20_rows((
            {"minute": "2026-09-08T09:01", "market_values": [1, 2, 3], "capture_state": "realtime_complete"},
            {"minute": "2026-09-08T09:00", "market_values": [9, 9, 9], "capture_state": "partial"},
        ))

        self.assertEqual([(datetime(2026, 9, 8, 9, 1), 1.0, 2.0, 3.0)], rows)

    def test_nas_market_data_is_available_before_realtime_subscription_opens(self) -> None:
        owner = SimpleNamespace(
            _active_api_route="central_waiting",
            _market_data_client=SimpleNamespace(load_stored_minute_bars=lambda *_args: ()),
        )

        self.assertTrue(MainWindow._uses_nas_market_data_source(owner))
        owner._active_api_route = "local_fallback"
        self.assertFalse(MainWindow._uses_nas_market_data_source(owner))

    def test_nas_recovery_forces_current_top20_minute_history_reload(self) -> None:
        class StatusBar:
            def showMessage(self, _message):
                pass

        requests = []
        owner = SimpleNamespace(
            _active_api_route="local_fallback",
            _minute_history_codes={"005930"},
            _row_by_code={"005930": 0, "000660": 1},
            statusBar=lambda: StatusBar(),
            _set_connected_api_status=lambda: None,
            _start_minute_history_loading=lambda codes, force=False: requests.append((codes, force)),
        )

        MainWindow._on_realtime_status_changed(owner, "나스 실시간 체결 구독 중 · 2종목")

        self.assertEqual("central", owner._active_api_route)
        self.assertEqual(set(), owner._minute_history_codes)
        self.assertEqual([(("005930", "000660"), True)], requests)

    def test_top20_collection_continues_while_five_minute_chart_is_selected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Database(Path(temporary_directory) / "monitor.sqlite3")
            database.initialize()
            window = MainWindow(database.settings)
            window._top20_index_timer.stop()
            current = [datetime(2026, 9, 9, 9, 0, 10)]
            window._ranking_now = lambda: current[0]
            window._top20_view_date = current[0].date()
            window._top20_view_mode = "5m"
            collector = window._top20_collector
            collector.minute = current[0].replace(second=0)
            collector.active_codes = ("005930",)
            collector.minute_codes = {"005930"}
            collector.cohort_segments = [("2026-09-09T09:00:00", ("005930",))]
            window._stock_markets["005930"] = "KOSPI"
            window._minute_aggregator.bucket_trade_value_eok = lambda *_args, **_kwargs: 1.0
            saved: list[datetime] = []
            window._save_top20_trade_value_index = lambda minute, *_args: saved.append(minute)

            window._update_top20_trade_value_index()
            self.assertEqual([(10, 1.0)], collector.samples)

            current[0] = datetime(2026, 9, 9, 9, 1)
            window._update_top20_trade_value_index()
            self.assertEqual([datetime(2026, 9, 9, 9, 0)], saved)
            window.close()

    def test_high_header_reenables_table_updates_after_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Database(Path(temporary_directory) / "monitor.sqlite3")
            database.initialize()
            window = MainWindow(database.settings, FakeRankingLoader())
            window._refresh_rankings()
            window._ranking_worker.wait()
            QApplication.processEvents()

            window._toggle_table_header_mode(13)

            self.assertTrue(window._table.updatesEnabled())
            self.assertEqual("historical", database.settings.get("high_distance_period"))
            window.close()

    def test_high_cycle_periods_keep_fixed_order_and_recover_empty_value(self) -> None:
        self.assertEqual(("20", "historical"), selected_high_cycle_periods("historical,20"))
        self.assertEqual(("5", "20", "250", "historical"), selected_high_cycle_periods(""))

    def test_selected_row_background_is_below_near_high_and_above_rank_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Database(Path(temporary_directory) / "monitor.sqlite3")
            database.initialize()
            window = MainWindow(database.settings, FakeRankingLoader())
            window._selected_table_code = "005930"

            self.assertEqual("#ddebf7", window._row_background_color("005930", 0).name())

            window._rank_changed_codes.add("005930")
            self.assertEqual("#ddebf7", window._row_background_color("005930", 0).name())

            window._near_high_codes.add("005930")
            self.assertEqual("#fde9e7", window._row_background_color("005930", 0).name())
            window.close()

    def test_hover_marker_keeps_strong_blue_independent_of_row_selection_color(self) -> None:
        self.assertEqual("#0078d7", NxtMarkerDelegate.ACTIVE_MARKER_COLOR.name())

    def test_delegate_remembers_only_the_clicked_cell_marker(self) -> None:
        delegate = NxtMarkerDelegate()
        delegate.set_selected_cell((2, 4))
        self.assertEqual((2, 4), delegate._selected_cell)
        delegate.set_selected_cell(None)
        self.assertIsNone(delegate._selected_cell)

    def test_rank_changed_highlight_default_duration_is_one_second(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Database(Path(temporary_directory) / "monitor.sqlite3")
            database.initialize()
            self.assertEqual("1.00", database.settings.get("rank_changed_highlight_seconds"))

    def test_trade_value_cell_alert_stays_above_near_high_row_background(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Database(Path(temporary_directory) / "monitor.sqlite3")
            database.initialize()
            window = MainWindow(database.settings, FakeRankingLoader())
            window._on_ranking_loaded(FakeRankingLoader().load_top_stocks())
            item = window._table.item(0, 6)
            item.setData(window.TRADE_VALUE_ALERT_ROLE, True)
            window._near_high_codes.add("005930")

            window._apply_row_background("005930")

            self.assertEqual("#f4cccc", item.background().color().name())
            self.assertEqual("#fde9e7", window._table.item(0, 5).background().color().name())
            window._theme_trade_summary_timer.stop()
            window.close()


if __name__ == "__main__":
    unittest.main()
