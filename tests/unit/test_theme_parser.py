from __future__ import annotations
import unittest
from kiwoom_monitor.domain.theme_parser import parse_themes

class ThemeParserTests(unittest.TestCase):
    def test_splits_default_separators_and_removes_duplicates(self) -> None:
        self.assertEqual(("반도체", "AI", "로봇", "2차전지"), parse_themes("반도체, AI / 로봇 | 2차전지; AI"))
    def test_keeps_spaces_inside_one_theme(self) -> None:
        self.assertEqual(("자율주행 자동차",), parse_themes("자율주행 자동차"))
