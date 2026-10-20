from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

# Load the native parser dependency before provider sys.modules patches restore
# their snapshot; reimporting a removed cv2 Python package leaves native types stale.
import cv2
from PIL import Image, ImageDraw

from kiwoom_monitor.infrastructure.ocr.paddle_theme_ocr import (
    PaddleThemeOcr,
    ImageThemeOcrWorker,
    _OcrToken,
    _badge_regions,
    _merge_badge_tokens,
    _merge_theme_rows,
    _normalized_badge_text,
    _theme_rows_from_tokens,
)


class ThemeImageLayoutTests(unittest.TestCase):
    def test_lazy_rows_and_worker_read_each_image_once_in_all_layout_modes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = tuple(Path(directory) / name for name in ("first.png", "second.png"))
            for path in paths:
                _save_ocr_image(path)
            for mode, theme in (("theme_column", "반도체"), ("reason_badges", "AI"), ("both", "AI")):
                with self.subTest(mode=mode, entry="extract_rows"):
                    engine = _CountingOcrEngine()
                    factory = Mock(return_value=engine)
                    with patch.dict("sys.modules", {"paddleocr": SimpleNamespace(PaddleOCR=factory)}):
                        rows = PaddleThemeOcr().extract_rows(paths[0], mode, "관련 테마")
                    self.assertEqual((_row("심텍", theme),), rows)
                    self.assertEqual([str(paths[0])], engine.images)
                    self.assertEqual(int(mode != "theme_column"), engine.badges)
                    factory.assert_called_once()
                    self.assertEqual("PP-OCRv5_mobile_det", factory.call_args.kwargs["text_detection_model_name"])
                    self.assertFalse(factory.call_args.kwargs["use_doc_orientation_classify"])
                with self.subTest(mode=mode, entry="worker"):
                    engine = _CountingOcrEngine()
                    factory = Mock(return_value=engine)
                    worker = ImageThemeOcrWorker(paths, mode, "관련 테마")
                    completed, failed, progress = [], [], []
                    worker.completed.connect(completed.append)
                    worker.failed.connect(failed.append)
                    worker.progress.connect(progress.append)
                    with patch.dict("sys.modules", {"paddleocr": SimpleNamespace(PaddleOCR=factory)}):
                        worker.run()
                    self.assertEqual([(_row("심텍", theme),)], completed)
                    self.assertEqual([], failed)
                    self.assertEqual(list(map(str, paths)), engine.images)
                    self.assertEqual(2 * int(mode != "theme_column"), engine.badges)
                    self.assertEqual(3, len(progress))
                    self.assertIn("엔진", progress[0])
                    self.assertIn("1/2", progress[1])
                    self.assertIn("2/2", progress[2])
                    factory.assert_called_once()

    def test_extract_lines_keeps_page_formats_and_does_not_reuse_a_previous_image(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "theme.png"
            _save_ocr_image(path)
            engine = _CountingOcrEngine()
            engine.pages = (
                {"rec_texts": ("  심텍 ", "", "반도체")},
                SimpleNamespace(json={"res": {"rec_texts": ("AI",)}}),
                SimpleNamespace(json=lambda: {"res": {"rec_texts": ("PCB",)}}),
                {"res": []},
                "invalid page",
            )
            factory = Mock(return_value=engine)
            with patch.dict("sys.modules", {"paddleocr": SimpleNamespace(PaddleOCR=factory)}):
                ocr = PaddleThemeOcr()
                self.assertEqual(("심텍", "반도체", "AI", "PCB"), ocr.extract_lines(path))
                self.assertEqual(("심텍", "반도체", "AI", "PCB"), ocr.extract_lines(path))
            self.assertEqual([str(path), str(path)], engine.images)
            factory.assert_called_once()

    def test_worker_keeps_preparation_and_image_failure_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first, second = (Path(directory) / name for name in ("first.png", "second.png"))
            _save_ocr_image(first)
            _save_ocr_image(second)
            cases = (
                ((), None, None, "선택한 이미지가 없습니다.", 0),
                ((Path(directory) / "missing.png",), None, None, "선택한 이미지 파일을 찾을 수 없습니다.", 0),
                ((first,), "model", None, "model failed", 1),
                ((first,), None, 1, "predict failed", 1),
                ((first, second), None, str(second), "second.png: predict failed", 1),
            )
            for paths, model_failure, failed_call, expected, constructor_count in cases:
                with self.subTest(expected=expected):
                    engine = _CountingOcrEngine(failed_call=failed_call)
                    factory = Mock(return_value=engine, side_effect=RuntimeError("model failed") if model_failure else None)
                    worker = ImageThemeOcrWorker(paths, "theme_column", "관련 테마")
                    failed, completed = [], []
                    worker.failed.connect(failed.append)
                    worker.completed.connect(completed.append)
                    with patch.dict("sys.modules", {"paddleocr": SimpleNamespace(PaddleOCR=factory)}):
                        worker.run()
                    self.assertEqual([expected], failed)
                    self.assertEqual([], completed)
                    self.assertEqual(constructor_count, factory.call_count)
            first.write_bytes(b"not an image")
            engine = _CountingOcrEngine()
            worker = ImageThemeOcrWorker((first,), "theme_column", "관련 테마")
            failed = []
            worker.failed.connect(failed.append)
            with patch.dict("sys.modules", {"paddleocr": SimpleNamespace(PaddleOCR=lambda **_options: engine)}):
                worker.run()
            self.assertEqual(["first.png: 이미지 파일을 읽을 수 없습니다."], failed)

    def test_worker_cancel_after_initial_inference_does_not_parse_badges_or_publish(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "theme.png"
            _save_ocr_image(path)
            engine = _CountingOcrEngine()
            worker = ImageThemeOcrWorker((path,), "both", "관련 테마")
            completed, failed, progress = [], [], []
            worker.completed.connect(completed.append)
            worker.failed.connect(failed.append)
            worker.progress.connect(progress.append)
            with (
                patch.dict("sys.modules", {"paddleocr": SimpleNamespace(PaddleOCR=lambda **_options: engine)}),
                patch.object(worker, "isInterruptionRequested", return_value=True),
            ):
                worker.run()
            self.assertEqual([str(path)], engine.images)
            self.assertEqual(0, engine.badges)
            self.assertEqual([], completed)
            self.assertEqual([], failed)
            self.assertEqual(1, len(progress))

    def test_worker_preserves_preparation_page_error_and_allows_a_fresh_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "theme.png"
            _save_ocr_image(path)
            broken, healthy = _CountingOcrEngine(), _CountingOcrEngine()
            broken.pages = (SimpleNamespace(json=Mock(side_effect=ValueError("page failed"))),)
            factory = Mock(side_effect=(broken, healthy))
            with patch.dict("sys.modules", {"paddleocr": SimpleNamespace(PaddleOCR=factory)}):
                for expected_error in ("page failed", None):
                    worker = ImageThemeOcrWorker((path,), "theme_column", "관련 테마")
                    failed, completed = [], []
                    worker.failed.connect(failed.append)
                    worker.completed.connect(completed.append)
                    worker.run()
                    self.assertEqual([expected_error] if expected_error else [], failed)
                    self.assertEqual([] if expected_error else [(_row("심텍", "반도체"),)], completed)
            self.assertEqual(2, factory.call_count)
            self.assertEqual([str(path)], broken.images)
            self.assertEqual([str(path)], healthy.images)

    def test_uses_current_app_data_directory_for_local_models(self) -> None:
        data_dir = Path("C:/user-data")
        with patch(
            "kiwoom_monitor.infrastructure.ocr.paddle_theme_ocr.AppPaths.for_current_user",
            return_value=SimpleNamespace(data_dir=data_dir),
        ):
            ocr = PaddleThemeOcr()

        self.assertEqual(data_dir / "ocr_models" / "PP-OCRv5_mobile_det", ocr._detection_model)
        self.assertEqual(
            data_dir / "ocr_models" / "korean_PP-OCRv5_mobile_rec",
            ocr._korean_recognition_model,
        )

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


def _save_ocr_image(path: Path) -> None:
    image = Image.new("RGB", (800, 120), "white")
    ImageDraw.Draw(image).rectangle((470, 48, 530, 72), fill=(246, 238, 255))
    image.save(path)


class _CountingOcrEngine:
    def __init__(self, failed_call: int | str | None = None) -> None:
        self.images: list[str] = []
        self.badges = 0
        self.failed_call = failed_call
        self.pages = None

    def predict(self, source):
        # A one-shot iterator catches accidental consumption of the first result
        # while keeping real image/token/badge parsing in the production code.
        if isinstance(source, str):
            self.images.append(source)
            if len(self.images) == self.failed_call or source == self.failed_call:
                raise RuntimeError("predict failed")
            texts = ("종목명", "관련 테마", "이유", "심텍", "반도체")
            coordinates = ((50, 20), (320, 20), (650, 20), (50, 60), (320, 60))
        else:
            self.badges += 1
            texts, coordinates = ("AI",), ((1000, 120),)
        pages = self.pages or ({"res": {
            "rec_texts": texts,
            "rec_polys": tuple(
                ((x - 10, y - 4), (x + 10, y - 4), (x + 10, y + 4), (x - 10, y + 4))
                for x, y in coordinates
            ),
        }},)
        yield from pages
