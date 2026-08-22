"""Korean table-text extraction for the image theme-import preview."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from dataclasses import dataclass


@dataclass(frozen=True)
class ImageThemeRow:
    name: str
    themes: str


class PaddleThemeOcr:
    """Lazy-load PaddleOCR so the main monitor starts without OCR overhead."""

    # 개발 실행과 PyInstaller 폴더형 배포 모두에서 같은 data 경로를 사용한다.
    _APP_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[4]
    _MODEL_ROOT = _APP_ROOT / "data" / "ocr_models"
    _DETECTION_MODEL = _MODEL_ROOT / "PP-OCRv5_mobile_det"
    _KOREAN_RECOGNITION_MODEL = _MODEL_ROOT / "korean_PP-OCRv5_mobile_rec"

    def __init__(self) -> None:
        self._ocr = None

    @staticmethod
    def _engine_model_path(model_path: Path) -> str:
        """Prefer a relative path: Paddle's Windows native runner mishandles Korean paths."""
        try:
            return str(model_path.relative_to(Path.cwd()))
        except ValueError:
            return str(model_path)

    def extract_lines(self, image_path: Path) -> tuple[str, ...]:
        if not image_path.is_file():
            raise ValueError("선택한 이미지 파일을 찾을 수 없습니다.")
        if self._ocr is None:
            try:
                # PaddleOCR 3.x의 기본 공식 모델 원본은 Hugging Face다. 일부
                # 환경에서 BOS가 선택되면 한국어 모델을 찾지 못하는 경우가 있다.
                os.environ["PADDLE_PDX_MODEL_SOURCE"] = "huggingface"
                # 회사망/보안 프로그램이 저장소 상태 확인 주소를 막아도 실제 모델
                # 다운로드 주소는 열려 있는 경우가 있다. 사전 연결 검사를 건너뛴다.
                os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
                from paddleocr import PaddleOCR
            except ImportError as error:
                raise ValueError("PaddleOCR 구성요소가 없습니다. 프로젝트 의존성을 설치해 주세요.") from error
            options = {
                # 화면 캡처 표에는 문서 회전·왜곡 보정·글줄 방향 모델이 필요 없다.
                # 이를 끄면 첫 다운로드와 시작 시간이 크게 줄어든다.
                "use_doc_orientation_classify": False,
                "use_doc_unwarping": False,
                "use_textline_orientation": False,
            }
            if self._DETECTION_MODEL.is_dir() and self._KOREAN_RECOGNITION_MODEL.is_dir():
                options["text_detection_model_name"] = "PP-OCRv5_mobile_det"
                options["text_recognition_model_name"] = "korean_PP-OCRv5_mobile_rec"
                options["text_detection_model_dir"] = self._engine_model_path(self._DETECTION_MODEL)
                options["text_recognition_model_dir"] = self._engine_model_path(self._KOREAN_RECOGNITION_MODEL)
            else:
                # Korean PP-OCRv5 uses this exact detector/recognizer pair.
                options["lang"] = "korean"
                options["ocr_version"] = "PP-OCRv5"
            self._ocr = PaddleOCR(**options)
        result = self._ocr.predict(str(image_path))
        lines: list[str] = []
        for page in result:
            payload = page.json if hasattr(page, "json") else page
            if callable(payload):
                payload = payload()
            data = payload.get("res", payload) if isinstance(payload, dict) else {}
            texts = data.get("rec_texts", ()) if isinstance(data, dict) else ()
            lines.extend(str(text).strip() for text in texts if str(text).strip())
        return tuple(lines)

    def extract_rows(self, image_path: Path) -> tuple[ImageThemeRow, ...]:
        """Use OCR bounding boxes to split a screenshot into stock/theme columns."""
        if self._ocr is None:
            self.extract_lines(image_path)
        result = self._ocr.predict(str(image_path))
        tokens: list[tuple[str, float, float]] = []
        for page in result:
            payload = page.json if hasattr(page, "json") else page
            if callable(payload):
                payload = payload()
            data = payload.get("res", payload) if isinstance(payload, dict) else {}
            texts = data.get("rec_texts", ()) if isinstance(data, dict) else ()
            polygons = data.get("rec_polys", ()) if isinstance(data, dict) else ()
            for text, polygon in zip(texts, polygons):
                points = list(polygon)
                if not points:
                    continue
                x = sum(float(point[0]) for point in points) / len(points)
                y = sum(float(point[1]) for point in points) / len(points)
                value = str(text).strip()
                if value:
                    tokens.append((value, x, y))
        headers = {text.replace(" ", ""): x for text, x, _ in tokens if text.replace(" ", "") in {"종목명", "테마"}}
        name_x, theme_x = headers.get("종목명"), headers.get("테마")
        if name_x is None or theme_x is None:
            raise ValueError("이미지에서 '종목명'과 '테마' 열을 찾지 못했습니다.")
        rows: dict[int, list[tuple[str, float]]] = {}
        for text, x, y in tokens:
            if text.replace(" ", "") in {"종목명", "테마", "등락률", "거래대금", "이유"}:
                continue
            rows.setdefault(round(y / 18), []).append((text, x))
        output: list[ImageThemeRow] = []
        for items in rows.values():
            name = min(items, key=lambda item: abs(item[1] - name_x))[0]
            theme = min(items, key=lambda item: abs(item[1] - theme_x))[0]
            if name != theme and abs(next(x for text, x in items if text == name) - name_x) < abs(next(x for text, x in items if text == name) - theme_x):
                output.append(ImageThemeRow(name, theme))
        return tuple(output)


class ImageThemeOcrWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, image_path: Path) -> None:
        super().__init__()
        self._image_path = image_path

    def run(self) -> None:
        try:
            rows = PaddleThemeOcr().extract_rows(self._image_path)
        except Exception as error:
            self.failed.emit(str(error))
            return
        if not self.isInterruptionRequested():
            self.completed.emit(rows)
