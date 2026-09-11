from __future__ import annotations

import gc
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QToolBar

from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.presentation.main_window import MainWindow, NxtMarkerDelegate, selected_high_cycle_periods
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import TradeTick
from kiwoom_monitor.application.daily_high_service import DailyHighTargets
from kiwoom_monitor.application.trade_strength import StockFundamentals
from kiwoom_monitor.application.minute_trade_value import MinuteOhlcv


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
    def test_theme_change_forces_google_settings_and_theme_backup(self) -> None:
        scheduled: list[str] = []
        refreshed: list[bool] = []
        owner = SimpleNamespace(
            _theme_store=SimpleNamespace(all_by_name=lambda: {"005930": ("반도체",)}),
            _refresh_rankings=lambda: refreshed.append(True),
            _schedule_google_drive_upload=scheduled.append,
        )

        MainWindow._on_themes_changed(owner)

        self.assertEqual({"005930": ("반도체",)}, owner._themes)
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
            widgets = [toolbar.widgetForAction(action) for action in toolbar.actions()]
            self.assertEqual(widgets.index(settings_button), widgets.index(version_label) + 1)
            self.assertEqual((settings_button.width(), settings_button.height()), (24, 22))
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
