"""Korean table-text extraction for the image theme-import preview."""

from __future__ import annotations

import os
from pathlib import Path
from dataclasses import dataclass


@dataclass(frozen=True)
class ImageThemeRow:
    name: str
    themes: str


class PaddleThemeOcr:
    """Lazy-load PaddleOCR so the main monitor starts without OCR overhead."""

    def __init__(self) -> None:
        self._ocr = None

    def extract_lines(self, image_path: Path) -> tuple[str, ...]:
        if not image_path.is_file():
            raise ValueError("선택한 이미지 파일을 찾을 수 없습니다.")
        if self._ocr is None:
            try:
                # PaddleOCR 3.x의 기본 공식 모델 원본은 Hugging Face다. 일부
                # 환경에서 BOS가 선택되면 한국어 모델을 찾지 못하는 경우가 있다.
                os.environ["PADDLE_PDX_MODEL_SOURCE"] = "huggingface"
                from paddleocr import PaddleOCR
            except ImportError as error:
                raise ValueError("PaddleOCR 구성요소가 없습니다. 프로젝트 의존성을 설치해 주세요.") from error
            self._ocr = PaddleOCR(lang="korean")
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
