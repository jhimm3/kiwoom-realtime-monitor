from __future__ import annotations

import unittest

from kiwoom_monitor.application.theme_matching import MatchedThemeRow, extract_known_stocks_and_unknown_fragments, match_theme_rows, split_concatenated_stock_name
from kiwoom_monitor.application.theme_preview import ThemeChange, preview_theme_changes
from kiwoom_monitor.domain.theme_import import ThemeImportRow


class ThemeMatchingTests(unittest.TestCase):
    def test_matching_batch_preserves_duplicate_rows_and_legacy_lookup_order(self) -> None:
        rows = (
            ThemeImportRow("Current", ("AI",)),
            ThemeImportRow("Alias", ("로봇",)),
            ThemeImportRow("Current", ("반도체",)),
            ThemeImportRow("missing", ("제외",)),
        )
        expected = (
            (MatchedThemeRow("A", "Current", ("AI",)),
             MatchedThemeRow("B", "Alias", ("로봇",)),
             MatchedThemeRow("A", "Current", ("반도체",))),
            (rows[3],),
        )

        class Legacy:
            def __init__(self):
                self.calls = []

            def find_code_by_name(self, name):
                self.calls.append(name)
                return {"Current": "A", "Alias": "B"}.get(name)

        legacy = Legacy()
        self.assertEqual(expected, match_theme_rows(rows, legacy))
        self.assertEqual(["Current", "Alias", "Current", "missing"], legacy.calls)

        class Batch(Legacy):
            def find_codes_by_names(self, names):
                self.calls.append(names)
                return {name: {"Current": "A", "Alias": "B"}.get(name) for name in names}

        batch = Batch()
        self.assertEqual(expected, match_theme_rows(rows, batch))
        self.assertEqual([("Current", "Alias", "missing")], batch.calls)

    def test_matching_empty_input_and_batch_error_do_not_invoke_point_fallback(self) -> None:
        class Lookup:
            def find_codes_by_names(self, _names):
                raise ValueError("lookup failed")

            def find_code_by_name(self, _name):
                raise AssertionError("batch failure must propagate")

        self.assertEqual(((), ()), match_theme_rows((), Lookup()))
        with self.assertRaisesRegex(ValueError, "lookup failed"):
            match_theme_rows((ThemeImportRow("A", ("AI",)),), Lookup())

    def test_preview_preserves_rows_and_legacy_reader_contract(self) -> None:
        class Memberships:
            def __init__(self):
                self.calls = []

            def themes_for_stock(self, code):
                self.calls.append(code)
                return {"A": ("AI", "반도체"), "B": ("로봇",)}.get(code, ())

        rows = (
            MatchedThemeRow("A", "첫 이름", ("반도체", "ai")),
            MatchedThemeRow("B", "둘째", ("신규 테마",)),
            MatchedThemeRow("A", "중복 행", ("다른 테마",)),
            MatchedThemeRow("missing", "없는 종목", ()),
        )
        legacy = Memberships()
        expected = (
            ThemeChange("A", "첫 이름", ("AI", "반도체"), ("반도체", "ai"), "변경 없음"),
            ThemeChange("B", "둘째", ("로봇",), ("신규 테마",), "테마 변경"),
            ThemeChange("A", "중복 행", ("AI", "반도체"), ("다른 테마",), "테마 변경"),
            ThemeChange("missing", "없는 종목", (), (), "변경 없음"),
        )
        self.assertEqual(expected, preview_theme_changes(rows, legacy))
        self.assertEqual(["A", "B", "A", "missing"], legacy.calls)

        class BatchMemberships(Memberships):
            def themes_for_stocks(self, codes):
                self.calls.append(codes)
                return {code: {"A": ("AI", "반도체"), "B": ("로봇",)}.get(code, ()) for code in codes}

        batch = BatchMemberships()
        self.assertEqual(expected, preview_theme_changes(rows, batch))
        self.assertEqual([("A", "B", "missing")], batch.calls)

    def test_preview_empty_input_and_batch_failure_do_not_fall_back_to_point_reads(self) -> None:
        class Memberships:
            def themes_for_stocks(self, _codes):
                raise ValueError("batch failed")

            def themes_for_stock(self, _code):
                raise AssertionError("point read must not hide a batch failure")

        self.assertEqual((), preview_theme_changes((), Memberships()))
        with self.assertRaisesRegex(ValueError, "batch failed"):
            preview_theme_changes((MatchedThemeRow("A", "종목", ("테마",)),), Memberships())

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
