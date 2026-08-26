from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.presentation.main_window import MainWindow, NxtMarkerDelegate, selected_high_cycle_periods
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import TradeTick


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
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_main_window_has_default_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Database(Path(temporary_directory) / "monitor.sqlite3")
            database.initialize()
            window = MainWindow(database.settings, FakeRankingLoader())

            self.assertEqual(window.windowTitle(), "키움 실시간 모니터 (테스트)")
            table = window.centralWidget().findChild(type(window._table))
            self.assertEqual(table.columnCount(), 16)
            self.assertFalse(table.horizontalHeader().stretchLastSection())
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
            window._refresh_rankings()
            window._ranking_worker.wait()
            QApplication.processEvents()
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
