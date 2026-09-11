from __future__ import annotations

import unittest

from kiwoom_monitor.presentation.main_table_formatting import (
    change_rate_text_color,
    decimal_places,
    format_market_cap_eok,
    format_trade_value_eok,
    rank_highlight_duration_ms,
    row_background_color,
    theme_trade_summary_html,
    trade_value_color,
)


class MainTableFormattingTests(unittest.TestCase):
    def test_formats_eok_and_jo_boundaries(self) -> None:
        self.assertEqual("9,999.5억", format_trade_value_eok(9_999.5, 1))
        self.assertEqual("1조", format_trade_value_eok(10_000, 1))
        self.assertEqual("1조234.5억", format_trade_value_eok(10_234.5, 1))
        self.assertEqual("1조 · 234억", format_market_cap_eok(10_234.5))

    def test_summary_escapes_theme_and_uses_value_band(self) -> None:
        rendered = theme_trade_summary_html([("반도체<script>", 12_000)], 0)
        self.assertIn("반도체&lt;script&gt;", rendered)
        self.assertIn("1조2,000억", rendered)
        self.assertIn("#C00000", rendered)
        self.assertEqual("#0070C0", trade_value_color(9_999))

    def test_row_background_priority_is_preserved(self) -> None:
        common = dict(
            rank_changed_color="#00FF00", rank=2,
            odd_color="#FFFFFF", even_color="#EEEEEE",
        )
        self.assertEqual(
            "#FDE9E7",
            row_background_color(
                near_high=True, selected=True, rank_changed=True,
                rank_changed_enabled=True, **common,
            ),
        )
        self.assertEqual(
            "#DDEBF7",
            row_background_color(
                near_high=False, selected=True, rank_changed=True,
                rank_changed_enabled=True, **common,
            ),
        )
        self.assertEqual(
            "#00FF00",
            row_background_color(
                near_high=False, selected=False, rank_changed=True,
                rank_changed_enabled=True, **common,
            ),
        )
        self.assertEqual(
            "#EEEEEE",
            row_background_color(
                near_high=False, selected=False, rank_changed=True,
                rank_changed_enabled=False, **common,
            ),
        )

    def test_display_settings_are_parsed_conservatively(self) -> None:
        self.assertEqual(4, decimal_places("9", "trade_value"))
        self.assertEqual(8, decimal_places("9", "strength"))
        self.assertEqual(0, decimal_places("-1", "trade_value"))
        self.assertEqual(2, decimal_places("bad", "trade_value"))
        self.assertEqual(1_500, rank_highlight_duration_ms("1.5"))
        self.assertEqual(2_000, rank_highlight_duration_ms("bad"))

    def test_change_rate_color_handles_sign_and_unparseable_text(self) -> None:
        self.assertEqual("#C00000", change_rate_text_color("1,234.5%"))
        self.assertEqual("#0070C0", change_rate_text_color("-0.1%"))
        self.assertIsNone(change_rate_text_color("0%"))
        self.assertIsNone(change_rate_text_color("자료 없음"))


if __name__ == "__main__":
    unittest.main()
