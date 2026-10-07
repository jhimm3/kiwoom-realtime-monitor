from __future__ import annotations

import unittest

from kiwoom_monitor.application.theme_matching import extract_known_stocks_and_unknown_fragments, match_theme_rows, split_concatenated_stock_name
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

    def test_splits_multiple_names_with_one_missing_character(self) -> None:
        stocks = (
            ("0001", "세명전기"),
            ("0002", "대주전자재료"),
            ("0003", "효성중공업"),
        )

        self.assertEqual(
            stocks,
            split_concatenated_stock_name("세명전기대주전자재효성중공업", stocks),
        )

    def test_does_not_split_when_multiple_characters_are_wrong(self) -> None:
        stocks = (("0001", "한미약품"), ("0002", "일동제약"))

        self.assertEqual((), split_concatenated_stock_name("한미약팜일동재약", stocks))

    def test_keeps_known_stocks_and_returns_only_the_misspelled_remainder(self) -> None:
        stocks = (
            ("0001", "세명전기"),
            ("0002", "대주전자재료"),
            ("0003", "효성중공업"),
        )

        known, fragments = extract_known_stocks_and_unknown_fragments(
            "세명전기대주전짜재효성중공업", stocks
        )

        self.assertEqual((("0001", "세명전기"), ("0003", "효성중공업")), known)
        self.assertEqual(("대주전짜재",), fragments)

    def test_splits_many_joined_stocks_with_one_error_in_several_names(self) -> None:
        stocks = tuple(
            (f"{index:04d}", name)
            for index, name in enumerate(
                ("가나다전자", "라마바산업", "사아자테크", "차카타제약", "파하가에너지",
                 "나무로봇", "다리건설", "마소통신", "바다화학", "사과전기"),
                start=1,
            )
        )
        joined = "가나다전짜라마바산업사아자테크차카타재약파하가에너지나무로봇다리건설마소통신바다화학사과전키"

        self.assertEqual(stocks, split_concatenated_stock_name(joined, stocks))

    def test_rejects_split_when_too_many_stock_names_need_correction(self) -> None:
        stocks = (("0001", "가나다전자"), ("0002", "라마바산업"), ("0003", "사아자테크"))

        self.assertEqual(
            (),
            split_concatenated_stock_name("가나다전짜라마바산엽사아자태크", stocks),
        )
