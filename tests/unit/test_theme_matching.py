from __future__ import annotations

import unittest

from kiwoom_monitor.application.theme_matching import match_theme_rows
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
