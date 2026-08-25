from __future__ import annotations

import unittest

from PIL import Image, ImageDraw

from kiwoom_monitor.infrastructure.ocr.paddle_theme_ocr import (
    _OcrToken,
    _badge_regions,
    _merge_badge_tokens,
    _merge_theme_rows,
    _normalized_badge_text,
    _theme_rows_from_tokens,
)


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

    def test_both_prefers_badges_over_noisy_theme_column_ocr(self) -> None:
        tokens = (
            _OcrToken("종목명", 100, 20),
            _OcrToken("테마", 320, 20),
            _OcrToken("이유", 650, 20),
            _OcrToken("셀리드", 100, 60),
            _OcrToken("84,221백만", 320, 60),
            _OcrToken("코로나", 420, 60, True),
        )

        self.assertEqual(_theme_rows_from_tokens(tokens, "both"), (_row("셀리드", "코로나"),))

    def test_badges_work_when_theme_and_reason_headers_are_missed(self) -> None:
        tokens = (
            _OcrToken("종목명", 100, 20),
            _OcrToken("셀리드", 100, 60),
            _OcrToken("코로나", 420, 60, True),
        )

        self.assertEqual(_theme_rows_from_tokens(tokens, "both"), (_row("셀리드", "코로나"),))

    def test_accepts_common_name_header_ocr_error(self) -> None:
        tokens = (
            _OcrToken("종목망", 100, 20),
            _OcrToken("셀리드", 100, 60),
            _OcrToken("코로나", 420, 60, True),
        )

        self.assertEqual(_theme_rows_from_tokens(tokens, "both"), (_row("셀리드", "코로나"),))

    def test_infers_left_name_column_when_header_row_is_absent(self) -> None:
        tokens = (
            _OcrToken("애프터 마켓", 40, 10),
            _OcrToken("효성", 20, 40),
            _OcrToken("개별이슈", 300, 40, True),
        )

        self.assertEqual(_theme_rows_from_tokens(tokens, "both"), (_row("효성", "개별이슈"),))

    def test_finds_pastel_badge_but_not_colored_reason_text(self) -> None:
        image = Image.new("RGB", (500, 100), "white")
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((210, 40, 270, 62), radius=9, fill=(246, 238, 255))
        draw.rectangle((290, 44, 440, 58), fill=(217, 48, 37))

        self.assertEqual(((210, 40, 271, 63),), _badge_regions(image, 180))

    def test_finds_neutral_gray_badge(self) -> None:
        image = Image.new("RGB", (500, 100), "white")
        ImageDraw.Draw(image).rounded_rectangle((210, 40, 260, 62), radius=9, fill=(245, 245, 245))

        self.assertEqual(((210, 40, 261, 63),), _badge_regions(image, 180))

    def test_finds_long_two_line_badge(self) -> None:
        image = Image.new("RGB", (600, 140), "white")
        ImageDraw.Draw(image).rounded_rectangle((210, 40, 390, 84), radius=9, fill=(246, 238, 255))

        self.assertEqual(((210, 40, 391, 85),), _badge_regions(image, 180))

    def test_merges_split_text_only_inside_each_badge(self) -> None:
        tokens = (
            _OcrToken("제약·", 325, 50),
            _OcrToken("바이오", 365, 50),
            _OcrToken("은", 397, 50),
            _OcrToken("설명 문장", 450, 50),
        )

        self.assertEqual(
            (_OcrToken("제약·바이오", 350.0, 50.0, True),),
            _merge_badge_tokens(tokens, ((300, 40, 400, 60),)),
        )

    def test_corrects_single_a_badge_to_ai(self) -> None:
        self.assertEqual(
            (_OcrToken("AI", 225.0, 50.0, True),),
            _merge_badge_tokens((_OcrToken("A", 225, 50),), ((210, 40, 240, 60),)),
        )

    def test_merges_wrapped_badge_top_line_first(self) -> None:
        tokens = (
            _OcrToken("건설", 325, 65),
            _OcrToken("호남반도체/", 350, 50),
        )

        self.assertEqual(
            (_OcrToken("호남반도체/건설", 350.0, 55.0, True),),
            _merge_badge_tokens(tokens, ((300, 40, 400, 70),)),
        )

    def test_corrects_repeated_korean_badge_ocr_substitutions(self) -> None:
        self.assertEqual("통신장비/광통신", _normalized_badge_text("동신장비/광동신"))
        self.assertEqual("광트랜시버/전력설비", _normalized_badge_text("광트랜시베/전력실비"))

    def test_corrects_additional_user_reported_badge_substitutions(self) -> None:
        self.assertEqual("개별이슈/로봇AI", _normalized_badge_text("개벌이슈/로봇시"))
        self.assertEqual("바이오/제약/탈중국", _normalized_badge_text("바이외제약/달중국"))
        self.assertEqual("AI제조/통신", _normalized_badge_text("시제조/동신"))
        self.assertEqual("가상화폐/화장품", _normalized_badge_text("가상화페/회장품"))

    def test_merges_same_stock_from_multiple_images(self) -> None:
        self.assertEqual(
            (_row("심텍", "반도체/AI PCB"),),
            _merge_theme_rows([_row("심텍", "반도체"), _row("심텍", "AI PCB"), _row("심텍", "반도체")]),
        )


def _row(name: str, themes: str):
    from kiwoom_monitor.infrastructure.ocr.paddle_theme_ocr import ImageThemeRow
    return ImageThemeRow(name, themes)
