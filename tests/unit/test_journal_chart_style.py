from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from kiwoom_monitor.journal_process import (
    MinuteChart, adaptive_price_grid_lines, chart_time_step_minutes, chart_time_tick_indices, rounded_price_grid,
    average_price_label_positions, chart_close_information, format_trade_value_eok, multi_day_time_tick_indices, trade_callout_text,
    trade_marker_polygon, visible_trade_fills,
)
from kiwoom_monitor.application.trade_history_service import TradeFill


class JournalChartStyleTests(unittest.TestCase):
    def test_price_grid_uses_between_five_and_fifteen_lines_by_zoom_and_height(self) -> None:
        self.assertEqual(15, adaptive_price_grid_lines(30, 700))
        self.assertEqual(5, adaptive_price_grid_lines(600, 300))
        self.assertGreater(adaptive_price_grid_lines(30, 420), adaptive_price_grid_lines(240, 420))

    def test_trade_value_uses_jo_and_eok_units(self) -> None:
        self.assertEqual("9,999억", format_trade_value_eok(9_999, 0))
        self.assertEqual("1조", format_trade_value_eok(10_000, 0))
        self.assertEqual("1조 2,345억", format_trade_value_eok(12_345, 0))
        self.assertEqual("자료 없음", format_trade_value_eok(None))

    def test_holding_highlight_keeps_selected_color_translucent(self) -> None:
        chart = MinuteChart(); chart.set_holding_highlight_color("#80cbc4")
        brush = chart._holding_highlight_brush()
        self.assertEqual("#80cbc4", brush.name())
        self.assertEqual(55, brush.alpha())

    def test_chart_top_margin_can_be_compacted_for_dual_view(self) -> None:
        chart = MinuteChart(); chart.set_top_margin(30)
        self.assertEqual(30.0, chart._top_margin)
        chart.set_top_margin(10)
        self.assertEqual(28.0, chart._top_margin)

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_nine_thousand_price_axis_uses_flexible_exchange_tick_multiples(self) -> None:
        low, high, step = rounded_price_grid(9_077, 9_143)
        self.assertEqual((9_070, 9_150, 10), (low, high, step))
        self.assertEqual(0, step % 10)

    def test_three_hundred_thousand_price_axis_uses_thousand_won_round_figures(self) -> None:
        low, high, step = rounded_price_grid(298_700, 300_200)
        self.assertEqual(500, step)
        self.assertEqual(0, low % 500)
        self.assertEqual(0, high % 500)

    def test_visible_narrow_range_is_not_forced_to_coarse_round_figures(self) -> None:
        low, high, step = rounded_price_grid(49_910, 50_090)
        self.assertLessEqual(step, 100)
        self.assertLessEqual(high - low, 500)

    def test_chart_text_and_grid_switch_to_light_colors_on_dark_background(self) -> None:
        chart = MinuteChart()
        chart.set_background("#222222")
        self.assertGreater(chart._foreground.lightness(), chart._background.lightness())
        self.assertGreater(chart._grid.lightness(), chart._background.lightness())

    def test_minute_and_daily_trade_value_thresholds_render(self) -> None:
        chart = MinuteChart(); chart.resize(900, 420)
        chart.set_trade_value_threshold(40); chart.set_daily_trade_value_threshold(1_000)
        chart.set_rows((("2026-08-28T09:00", 100, 110, 90, 105, 1, 50.0, "확정"),))
        self.assertEqual(40.0, chart._trade_value_threshold)
        self.assertFalse(chart.grab().isNull())
        chart.set_daily_rows((("2026-08-28", 100, 110, 90, 105, 1, 1_500.0, "확정"),))
        chart.set_interval("일봉")
        self.assertEqual(1_000.0, chart._daily_trade_value_threshold)
        self.assertFalse(chart.grab().isNull())

    def test_moving_average_lines_have_independent_styles(self) -> None:
        chart = MinuteChart(); chart.resize(900, 420)
        chart.set_moving_average_styles({5: (True, "#ff00aa", 2.5), 10: (False, "#0011ff", 1.0)})
        chart.set_rows(tuple(
            (f"2026-08-28T09:{minute:02d}", 1_000 + minute, 1_010 + minute, 990 + minute, 1_005 + minute, 1, 1.0, "확정")
            for minute in range(30)
        ))
        self.assertEqual((True, "#ff00aa", 2.5), chart._moving_average_styles[5])
        self.assertEqual((False, "#0011ff", 1.0), chart._moving_average_styles[10])
        self.assertFalse(chart.grab().isNull())

    def test_chart_callouts_show_each_fill_and_close_change_separately(self) -> None:
        buy = TradeFill("1", "001210", "금호전기", "매수", datetime(2026, 8, 28, 9, 10, 5), 10, 1_000, "", "KRX")
        sell = TradeFill("2", "001210", "금호전기", "매도", datetime(2026, 8, 28, 10, 20, 7), 10, 1_100, "", "KRX")
        rows = (
            ("2026-08-27T15:30", 980, 990, 970, 990, 1, 1.0, "확정"),
            ("2026-08-28T09:00", 1_000, 1_010, 990, 1_005, 1, 1.0, "확정"),
            ("2026-08-28T15:30", 1_005, 1_120, 1_000, 1_100, 1, 1.0, "확정"),
        )
        buy_text, sell_text = trade_callout_text(buy), trade_callout_text(sell)
        close_text = chart_close_information((buy, sell), rows)
        self.assertIn("09:10:05", buy_text); self.assertIn("10주", buy_text); self.assertIn("1,000원", buy_text)
        self.assertIn("10:20:07", sell_text); self.assertIn("1,100원", sell_text)
        self.assertIn("종가 1,100원", close_text)
        self.assertIn("전일 종가 대비 +11.11%", close_text)
        self.assertNotIn("시가", close_text)

    def test_chart_line_and_rectangle_annotations_render_and_clear(self) -> None:
        chart = MinuteChart(); chart.resize(900, 420)
        chart.set_rows((
            ("2026-08-28T09:00", 1_000, 1_010, 990, 1_005, 1, 1.0, "확정"),
            ("2026-08-28T09:01", 1_005, 1_020, 1_000, 1_015, 1, 1.0, "확정"),
        ))
        start, end = datetime(2026, 8, 28, 9, 0), datetime(2026, 8, 28, 9, 1)
        chart.set_drawing_style(3.5, "#008000", "#123456")
        chart._annotations.extend((
            ("선", start, 1_000.0, end, 1_015.0),
            ("가로선", start, 1_005.0, end, 1_005.0),
            ("사각형", start, 990.0, end, 1_020.0),
        ))
        self.assertEqual(3.5, chart._drawing_line_width)
        self.assertEqual("#008000", chart._rectangle_color.name())
        self.assertEqual("#123456", chart._line_color.name())
        self.assertFalse(chart.grab().isNull())
        chart._selected_annotation = 1
        chart.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier))
        self.assertEqual(2, len(chart._annotations))
        chart.clear_annotations()
        self.assertEqual([], chart._annotations)

    def test_chart_text_annotation_uses_configured_style_and_can_be_deleted(self) -> None:
        chart = MinuteChart(); chart.resize(900, 420)
        chart.set_rows((("2026-08-28T09:00", 1_000, 1_010, 990, 1_005, 1, 1.0, "확정"),))
        chart.set_text_style("#123456", 18)
        moment = datetime(2026, 8, 28, 9, 0)
        chart._annotations.append(("텍스트", moment, 1_005.0, moment, 1_005.0, "돌파 확인"))
        self.assertEqual("#123456", chart._text_color.name())
        self.assertEqual(18, chart._text_size)
        self.assertFalse(chart.grab().isNull())
        chart._selected_annotation = 0
        chart.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier))
        self.assertEqual([], chart._annotations)

    def test_chart_text_is_edited_inline_without_a_dialog(self) -> None:
        chart = MinuteChart(); chart.resize(900, 420)
        chart.set_rows((("2026-08-28T09:00", 1_000, 1_010, 990, 1_005, 1, 1.0, "확정"),))
        moment = datetime(2026, 8, 28, 9, 0)
        chart._annotations.append(("텍스트", moment, 1_005.0, moment, 1_005.0, "기존"))
        chart._begin_text_edit(0, chart.rect().center())
        self.assertIsNotNone(chart._text_editor)
        chart._text_editor.setText("수정한 메모")
        chart._finish_text_edit()
        self.assertEqual("수정한 메모", chart._annotations[0][5])

    def test_trade_marker_tip_stays_on_exact_trade_price(self) -> None:
        buy = trade_marker_polygon(100, 200, "매수")
        sell = trade_marker_polygon(100, 200, "매도")
        self.assertEqual((100, 200), (buy.at(0).x(), buy.at(0).y()))
        self.assertTrue(any((sell.at(index).x(), sell.at(index).y()) == (100, 200) for index in range(sell.count())))
        self.assertGreaterEqual(min(buy.at(index).y() for index in range(buy.count())), 200)
        self.assertLessEqual(max(sell.at(index).y() for index in range(sell.count())), 200)

    def test_chart_keeps_target_day_fill_when_previous_day_is_also_loaded(self) -> None:
        chart = MinuteChart()
        fill = TradeFill("1", "001210", "금호전기", "매수", datetime(2026, 8, 28, 9, 40, 54), 10, 9_910, "", "KRX")
        chart.set_fills((fill,))
        chart.set_rows((
            ("2026-08-27T15:30", 9_800, 9_900, 9_700, 9_850, 1, 1.0, "확정"),
            ("2026-08-28T09:40", 9_980, 10_050, 9_750, 9_910, 1, 1.0, "확정"),
        ))
        visible = visible_trade_fills(chart._fills, datetime(2026, 8, 27, 15, 30), datetime(2026, 8, 28, 9, 41))
        self.assertEqual((fill,), visible)

    def test_chart_renders_selected_setup_type_over_holding_area(self) -> None:
        chart = MinuteChart(); chart.resize(900, 420)
        start = datetime(2026, 8, 28, 9, 0)
        buy = TradeFill("1", "001210", "금호전기", "매수", start, 10, 1_000, "", "KRX")
        sell = TradeFill("2", "001210", "금호전기", "매도", start + timedelta(minutes=1), 10, 1_010, "", "KRX")
        chart.set_fills((buy, sell)); chart.set_setup_types(("돌파",))
        chart.set_rows((
            ("2026-08-28T09:00", 1_000, 1_010, 990, 1_005, 1, 1.0, "확정"),
            ("2026-08-28T09:01", 1_005, 1_020, 1_000, 1_010, 1, 1.0, "확정"),
        ))
        self.assertEqual(("돌파",), chart.setup_types())
        self.assertFalse(chart.grab().isNull())

    def test_zoom_changes_screen_bar_count_and_allows_single_bar(self) -> None:
        chart = MinuteChart()
        rows = tuple(
            (f"2026-08-28T09:{minute:02d}", 100, 101, 99, 100, 1, 1.0, "확정")
            for minute in range(30)
        )
        chart.set_rows(rows)
        chart.set_view_count(2)
        chart.zoom(0.5)
        self.assertEqual(1, chart._view_count)

    def test_zoom_keeps_buy_sell_midpoint_at_screen_center(self) -> None:
        chart = MinuteChart()
        start = datetime(2026, 8, 28, 9, 0)
        rows = tuple(
            ((start + timedelta(minutes=index)).isoformat(timespec="minutes"), 100, 101, 99, 100, 1, 1.0, "확정")
            for index in range(100)
        )
        buy = TradeFill("1", "001210", "금호전기", "매수", start + timedelta(minutes=20), 1, 100, "", "KRX")
        sell = TradeFill("2", "001210", "금호전기", "매도", start + timedelta(minutes=60), 1, 100, "", "KRX")
        chart.set_rows(rows); chart.set_fills((buy, sell)); chart.set_view_count(20)
        self.assertEqual(30, chart._view_start)
        chart.zoom(0.5)
        self.assertEqual(35, chart._view_start)

    def test_time_axis_uses_round_steps_for_zoom_level(self) -> None:
        self.assertEqual(5, chart_time_step_minutes(30, "1분", 800))
        self.assertEqual(10, chart_time_step_minutes(60, "1분", 800))
        self.assertEqual(30, chart_time_step_minutes(120, "1분", 800))
        start = datetime(2026, 8, 28, 9, 0)
        values = [start + timedelta(minutes=index) for index in range(60)]
        indices = chart_time_tick_indices(values, "1분", 800)
        self.assertTrue(all((values[index].hour * 60 + values[index].minute) % 10 == 0 for index in indices))

    def test_higher_average_price_label_stays_above_lower_price(self) -> None:
        positions = average_price_label_positions((
            ("buy", 10_000, 100.0), ("sell", 10_010, 98.0),
        ))
        self.assertLess(positions["sell"], positions["buy"])
        self.assertGreaterEqual(positions["buy"] - positions["sell"], 14.0)

    def test_multi_day_axis_hides_session_edges_for_date_separator(self) -> None:
        values = [
            datetime(2026, 8, 26, 15, 0), datetime(2026, 8, 26, 15, 30),
            datetime(2026, 8, 27, 9, 0), datetime(2026, 8, 27, 9, 30),
        ]
        self.assertEqual([0, 3], multi_day_time_tick_indices(values, [0, 1, 2, 3]))

if __name__ == "__main__":
    unittest.main()
