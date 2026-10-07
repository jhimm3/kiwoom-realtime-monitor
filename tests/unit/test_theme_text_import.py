from __future__ import annotations

import unittest

from kiwoom_monitor.domain.theme_text_import import parse_theme_text


class ThemeTextImportTests(unittest.TestCase):
    TEXT = """🔥바이오제약헬스1
#mRNA:삼양바이오팜,나이벡
#제약:에스티팜,일동제약
"""

    def test_excludes_subcategory_theme_by_default(self) -> None:
        self.assertEqual(
            (
                ("삼양바이오팜", "바이오제약헬스1"),
                ("나이벡", "바이오제약헬스1"),
                ("에스티팜", "바이오제약헬스1"),
                ("일동제약", "바이오제약헬스1"),
            ),
            parse_theme_text(self.TEXT),
        )

    def test_adds_subcategory_when_selected(self) -> None:
        self.assertEqual(
            (
                ("삼양바이오팜", "바이오제약헬스1/mRNA"),
                ("나이벡", "바이오제약헬스1/mRNA"),
                ("에스티팜", "바이오제약헬스1/제약"),
                ("일동제약", "바이오제약헬스1/제약"),
            ),
            parse_theme_text(self.TEXT, include_subcategories=True),
        )
