from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kiwoom_monitor.infrastructure.naver_news import LocalNaverNewsConfig, NaverNewsCredentials


class NaverNewsConfigTests(unittest.TestCase):
    def test_shortcuts_are_saved_in_order_and_limited_to_five(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = LocalNaverNewsConfig(Path(directory) / "naver_news.dat")
            shortcuts = tuple((f"링크 {index}", f"https://example.com/{index}") for index in range(6))

            config.save(NaverNewsCredentials(), shortcuts=shortcuts)

            self.assertEqual(shortcuts[:5], config.load_shortcuts())

    def test_default_shortcuts_include_kind_and_ipo_calendar(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = LocalNaverNewsConfig(Path(directory) / "naver_news.dat")

            self.assertEqual(
                (
                    ("KIND", "https://kind.krx.co.kr/"),
                    ("공모주 일정", "https://www.38.co.kr/html/fund/index.htm?o=nw"),
                ),
                config.load_shortcuts(),
            )


if __name__ == "__main__":
    unittest.main()
