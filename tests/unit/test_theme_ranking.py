from __future__ import annotations

import unittest
from types import SimpleNamespace

from kiwoom_monitor.application.theme_ranking import (
    aggregate_theme_metrics,
    theme_group_sort_key,
    top_theme_trade_values,
    visible_theme_frequency,
)


class ThemeRankingTests(unittest.TestCase):
    def test_visible_frequency_normalizes_stock_names_and_theme_case(self) -> None:
        stocks = (SimpleNamespace(name="A 전자"), SimpleNamespace(name="B전자"))
        frequencies = visible_theme_frequency(
            stocks, {"A전자": "반도체, AI", "B전자": "반도체, ai"},
        )
        self.assertEqual({"반도체": 2, "ai": 2}, frequencies)

    def test_theme_metrics_count_stocks_and_sum_trade_value(self) -> None:
        frequency, totals = aggregate_theme_metrics((
            (("반도체", "AI"), 100.0),
            (("반도체",), 250.0),
        ))
        self.assertEqual({"반도체": 2, "ai": 1}, frequency)
        self.assertEqual({"반도체": 350.0, "ai": 100.0}, totals)

    def test_equal_theme_counts_put_larger_trade_value_first(self) -> None:
        frequency = {"반도체": 5, "원전": 5}
        totals = {"반도체": 1_000.0, "원전": 2_000.0}
        semiconductor = theme_group_sort_key(True, ("반도체",), frequency, totals, 10.0, 1)
        nuclear = theme_group_sort_key(True, ("원전",), frequency, totals, 5.0, 2)
        self.assertLess(nuclear, semiconductor)

    def test_disabled_theme_sort_uses_original_rank(self) -> None:
        self.assertEqual("0007", theme_group_sort_key(False, ("반도체",), {}, {}, 12.0, 7))

    def test_top_theme_values_sum_and_limit_results(self) -> None:
        entries = (
            ("A", "A전자", 100.0),
            ("B", "B전자", 250.0),
            ("C", "C전자", 80.0),
            ("D", "D전자", 40.0),
        )
        themes = {
            "A전자": "반도체, AI",
            "B전자": "반도체",
            "C전자": "원전",
            "D전자": "로봇",
        }
        self.assertEqual(
            [("반도체", 350.0), ("AI", 100.0), ("원전", 80.0)],
            top_theme_trade_values(entries, themes),
        )

    def test_top_theme_values_exclude_by_code_or_normalized_name(self) -> None:
        entries = (
            ("005930", "삼성 전자", 500.0),
            ("000660", "SK하이닉스", 300.0),
            ("A", "원전주", 200.0),
        )
        themes = {"삼성전자": "반도체", "SK하이닉스": "반도체", "원전주": "원전"}
        result = top_theme_trade_values(
            entries,
            themes,
            excluded_values=("005930", "sk 하이닉스"),
        )
        self.assertEqual([("원전", 200.0)], result)


if __name__ == "__main__":
    unittest.main()
