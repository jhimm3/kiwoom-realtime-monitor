from __future__ import annotations

import unittest

from kiwoom_monitor.presentation.main_window_layout import (
    clamp_uniform_row_height,
    fitted_window_size,
    parse_window_position,
    proportional_column_widths,
    responsive_row_height,
)


class MainWindowLayoutTests(unittest.TestCase):
    def test_row_height_limits_match_existing_ui_contract(self) -> None:
        self.assertEqual(12, clamp_uniform_row_height(5))
        self.assertEqual(42, clamp_uniform_row_height(42))
        self.assertEqual(100, clamp_uniform_row_height(120))

    def test_responsive_row_height_uses_viewport_and_limits(self) -> None:
        self.assertEqual(30, responsive_row_height(20, 600, 24))
        self.assertEqual(14, responsive_row_height(20, 100, 24))
        self.assertEqual(64, responsive_row_height(1, 200, 24))
        self.assertEqual(24, responsive_row_height(0, 0, 24))

    def test_proportional_widths_preserve_available_total(self) -> None:
        self.assertEqual((100, 200, 300), proportional_column_widths((50, 100, 150), 600))

    def test_proportional_widths_keep_minimum_and_noop_conditions(self) -> None:
        self.assertEqual((), proportional_column_widths((), 400))
        self.assertEqual((100, 200), proportional_column_widths((100, 200), 300))
        self.assertEqual((40, 80), proportional_column_widths((10, 90), 120))

    def test_fitted_window_size_respects_minimum(self) -> None:
        self.assertEqual(
            (900, 700),
            fitted_window_size(
                window_width=800,
                window_height=600,
                minimum_width=500,
                minimum_height=400,
                table_width=600,
                table_height=400,
                content_width=700,
                content_height=500,
            ),
        )
        self.assertEqual(
            (500, 400),
            fitted_window_size(
                window_width=500,
                window_height=400,
                minimum_width=500,
                minimum_height=400,
                table_width=700,
                table_height=600,
                content_width=100,
                content_height=100,
            ),
        )

    def test_parse_window_position_distinguishes_invalid_values(self) -> None:
        self.assertEqual((10, -20), parse_window_position("10", "-20"))
        self.assertIsNone(parse_window_position("", "20"))
        self.assertIsNone(parse_window_position(None, "20"))


if __name__ == "__main__":
    unittest.main()
