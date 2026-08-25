from __future__ import annotations

import unittest

from kiwoom_monitor.infrastructure.ocr.paddle_theme_ocr import _OcrToken, _theme_rows_from_tokens


class ThemeImageLayoutTests(unittest.TestCase):
    def test_reads_existing_theme_column_layout(self) -> None:
        tokens = (
            _OcrToken("종목명", 100, 20),
            _OcrToken("테마", 320, 20),
            _OcrToken("셀리드", 100, 60),
            _OcrToken("코로나", 320, 60),
        )

        self.assertEqual(_theme_rows_from_tokens(tokens, "theme_column"), (_row("셀리드", "코로나"),))

    def test_reads_user_named_theme_column(self) -> None:
        tokens = (
            _OcrToken("종목명", 100, 20),
            _OcrToken("관련 테마", 320, 20),
            _OcrToken("셀리드", 100, 60),
            _OcrToken("코로나", 320, 60),
        )

        self.assertEqual(_theme_rows_from_tokens(tokens, "theme_column", "관련 테마"), (_row("셀리드", "코로나"),))

    def test_reads_only_colored_reason_badges(self) -> None:
        tokens = (
            _OcrToken("종목명", 100, 20),
            _OcrToken("이유", 650, 20),
            _OcrToken("SV인베스트먼트", 100, 60),
            _OcrToken("레블리온", 350, 60, True),
            _OcrToken("엔비디아 협력 기대감", 520, 60, False),
            _OcrToken("코로나", 340, 82, True),
            _OcrToken("코로나19 재유행 우려", 510, 82, False),
            _OcrToken("키다리스튜디오", 100, 120),
            _OcrToken("일본 국제표준 소식", 410, 120, False),
        )

        self.assertEqual(_theme_rows_from_tokens(tokens, "reason_badges"), (_row("SV인베스트먼트", "레블리온/코로나"),))

    def test_combines_theme_column_and_reason_badges_without_duplicates(self) -> None:
        tokens = (
            _OcrToken("종목명", 100, 20),
            _OcrToken("테마", 320, 20),
            _OcrToken("이유", 650, 20),
            _OcrToken("셀리드", 100, 60),
            _OcrToken("코로나", 320, 60),
            _OcrToken("코로나", 520, 60, True),
            _OcrToken("바이오", 590, 60, True),
        )

        self.assertEqual(
            _theme_rows_from_tokens(tokens, "both"),
            (_row("셀리드", "코로나/바이오"),),
        )


def _row(name: str, themes: str):
    from kiwoom_monitor.infrastructure.ocr.paddle_theme_ocr import ImageThemeRow
    return ImageThemeRow(name, themes)
