from __future__ import annotations

import unittest

from kiwoom_monitor.application.theme_matching import match_theme_rows, split_concatenated_stock_name
from kiwoom_monitor.domain.theme_import import ThemeImportRow


class ThemeMatchingTests(unittest.TestCase):
    def test_returns_unmatched_rows_for_user_correction(self) -> None:
        class Stocks:
            def find_code_by_name(self, name: str) -> str | None:
                return "005930" if name == "삼성전자" else None

        matched, unmatched = match_theme_rows(
            (ThemeImportRow("삼성전자", ("반도체",)), ThemeImportRow("없는종목", ("테마",))),
            Stocks(),
        )

        self.assertEqual("005930", matched[0].code)
        self.assertEqual("없는종목", unmatched[0].name)

    def test_splits_names_only_when_known_stocks_cover_the_whole_value(self) -> None:
        stocks = (("0001", "한미약품"), ("0002", "일동제약"), ("0003", "한미"))

        self.assertEqual(
            (("0001", "한미약품"), ("0002", "일동제약")),
            split_concatenated_stock_name("한미약품 일동제약", stocks),
        )
        self.assertEqual((), split_concatenated_stock_name("한미약품오타", stocks))

    def test_splits_stock_names_that_are_joined_without_spaces(self) -> None:
        stocks = (("0001", "쏘닉스"), ("0002", "코스텍시스"))

        self.assertEqual(
            (("0001", "쏘닉스"), ("0002", "코스텍시스")),
            split_concatenated_stock_name("쏘닉스코스텍시스", stocks),
        )
