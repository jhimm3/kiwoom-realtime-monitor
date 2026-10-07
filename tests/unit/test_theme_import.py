from __future__ import annotations

import unittest

from kiwoom_monitor.domain.theme_import import validate_theme_header


class ThemeImportTests(unittest.TestCase):
    def test_accepts_required_excel_headers(self) -> None:
        self.assertEqual((), validate_theme_header(("종목명", "테마")))

    def test_rejects_wrong_excel_headers(self) -> None:
        self.assertTrue(validate_theme_header(("이름", "분류")))
