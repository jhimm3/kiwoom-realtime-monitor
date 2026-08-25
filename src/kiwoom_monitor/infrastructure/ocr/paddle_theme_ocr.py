"""Korean table-text extraction for the image theme-import preview."""

from __future__ import annotations

import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from dataclasses import dataclass
from PIL import Image


@dataclass(frozen=True)
class ImageThemeRow:
    name: str
    themes: str


@dataclass(frozen=True)
class _OcrToken:
    text: str
    x: float
    y: float
    has_badge_background: bool = False


def _badge_regions(image: Image.Image, minimum_x: int = 0) -> tuple[tuple[int, int, int, int], ...]:
    """Find compact pastel badge backgrounds while ignoring colored reason text."""
    import cv2
    import numpy as np

    rgb = np.asarray(image.convert("RGB"))
    maximum = rgb.max(axis=2)
    minimum = rgb.min(axis=2)
    # 배지 배경은 흰색에 가까운 저채도 파스텔이다. 빨강·파랑 설명 글자는
    # 어두운 픽셀이므로 제외하고, 머리글의 큰 보라색 면은 크기에서 제외한다.
    chroma = maximum - minimum
    pastel = (chroma >= 5) & (minimum >= 220)
    neutral_badge = (chroma <= 3) & (minimum >= 240) & (maximum <= 250)
    mask = (pastel | neutral_badge).astype("uint8") * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 3)))
    _, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    regions: list[tuple[int, int, int, int]] = []
    for left, top, width, height, area in stats[1:]:
        if left < minimum_x or not (25 <= width <= 130 and 15 <= height <= 32):
            continue
        if area / max(1, width * height) < 0.35:
            continue
        regions.append((int(left), int(top), int(left + width), int(top + height)))
    return tuple(sorted(regions, key=lambda region: (region[1], region[0])))


def _merge_badge_tokens(tokens: tuple[_OcrToken, ...], regions: tuple[tuple[int, int, int, int], ...]) -> tuple[_OcrToken, ...]:
    """Join OCR fragments that belong to the same colored badge."""
    output: list[_OcrToken] = []
    for left, top, right, bottom in regions:
        parts = sorted(
            (token for token in tokens if left - 4 <= token.x <= right + 4 and top - 4 <= token.y <= bottom + 4),
            key=lambda token: token.x,
        )
        if any(len(part.text.strip()) >= 2 for part in parts):
            # 배지 바로 뒤 설명의 첫 글자나 둥근 테두리를 한 글자로 잘못
            # 인식한 조각은 정상적인 배지명이 함께 있을 때 제외한다.
            parts = [part for part in parts if len(part.text.strip()) >= 2]
        text = "".join(part.text.strip() for part in parts if part.text.strip())
        if text:
            output.append(_OcrToken(text, (left + right) / 2, (top + bottom) / 2, True))
    return tuple(output)


def _normalized_header(value: str) -> str:
    return value.replace(" ", "").replace("\n", "")


@contextmanager
def _suppress_ocr_child_console_windows():
    """Keep short-lived Windows console windows from OCR helper processes hidden."""
    if os.name != "nt":
        yield
        return
    original_popen = subprocess.Popen

    def hidden_popen(*args: object, **kwargs: object):
        # Paddle's model/runtime helpers can launch a short-lived process on
        # Windows.  Preserve any caller flags while ensuring it has no console.
        kwargs["creationflags"] = int(kwargs.get("creationflags", 0)) | subprocess.CREATE_NO_WINDOW
        return original_popen(*args, **kwargs)

    subprocess.Popen = hidden_popen  # type: ignore[assignment]
    try:
        yield
    finally:
        subprocess.Popen = original_popen  # type: ignore[assignment]


def _theme_rows_from_tokens(tokens: tuple[_OcrToken, ...], mode: str, theme_header: str = "테마") -> tuple[ImageThemeRow, ...]:
    """Build import rows from a theme column, reason badges, or both together."""
    normalized_theme_header = _normalized_header(theme_header)
    headers = {
        _normalized_header(token.text): token.x
        for token in tokens
        if _normalized_header(token.text) in {"종목명", normalized_theme_header, "이유"}
    }
    name_x = headers.get("종목명")
    if name_x is None:
        raise ValueError("이미지에서 '종목명' 열을 찾지 못했습니다.")
    if mode == "both":
        rows: list[ImageThemeRow] = []
        if normalized_theme_header and normalized_theme_header in headers:
            rows.extend(_theme_rows_from_tokens(tokens, "theme_column", theme_header))
        if "이유" in headers:
            rows.extend(_theme_rows_from_tokens(tokens, "reason_badges", theme_header))
        if not rows:
            raise ValueError(
                "이미지에서 설정한 테마 열 또는 '이유' 열을 찾지 못했습니다. "
                "테마 열 제목이나 읽기 방식을 확인해 보세요."
            )
        return _merge_theme_rows(rows)
    if mode == "theme_column" and not normalized_theme_header:
        raise ValueError("테마 열 제목을 입력하거나 '색상 배지 읽기' 방식을 선택하세요.")
    if mode == "theme_column" and normalized_theme_header not in headers:
        raise ValueError(f"이미지에서 '{theme_header}' 열을 찾지 못했습니다. 열 제목을 수정하거나 '색상 배지 읽기' 방식을 선택해 보세요.")
    if mode == "reason_badges" and "이유" not in headers:
        raise ValueError("이미지에서 '이유' 열을 찾지 못했습니다. '테마 열' 방식을 선택해 보세요.")

    ignored_headers = {"종목명", normalized_theme_header, "등락률", "거래대금", "거래대금(백만)", "이유"}
    grouped: dict[int, list[_OcrToken]] = {}
    for token in tokens:
        if _normalized_header(token.text) in ignored_headers:
            continue
        grouped.setdefault(round(token.y / 18), []).append(token)

    if mode == "theme_column":
        theme_x = headers[normalized_theme_header]
        output: list[ImageThemeRow] = []
        for items in grouped.values():
            name_token = min(items, key=lambda item: abs(item.x - name_x))
            theme_token = min(items, key=lambda item: abs(item.x - theme_x))
            if name_token is not theme_token and abs(name_token.x - name_x) < abs(name_token.x - theme_x):
                output.append(ImageThemeRow(name_token.text, theme_token.text))
        return tuple(output)

    # 이유 열 방식은 테이블의 텍스트 전체가 아니라, 색상 배경이 확인된 배지의
    # 텍스트만 수집한다. 일반 이유 문장이 테마로 잘못 들어가는 일을 피한다.
    anchors: list[tuple[str, float]] = []
    for _, items in sorted(grouped.items()):
        name_token = min(items, key=lambda item: abs(item.x - name_x))
        if abs(name_token.x - name_x) <= 100:
            anchors.append((name_token.text, name_token.y))
    themes_by_name: dict[int, list[str]] = {index: [] for index in range(len(anchors))}
    for token in tokens:
        if not token.has_badge_background or _normalized_header(token.text) in ignored_headers:
            continue
        nearest = min(range(len(anchors)), key=lambda index: abs(anchors[index][1] - token.y), default=None)
        if nearest is None or abs(anchors[nearest][1] - token.y) > 42:
            continue
        if token.text not in themes_by_name[nearest]:
            themes_by_name[nearest].append(token.text)
    return tuple(
        ImageThemeRow(name, "/".join(themes_by_name[index]))
        for index, (name, _) in enumerate(anchors)
        if themes_by_name[index]
    )


def _merge_theme_rows(rows: list[ImageThemeRow]) -> tuple[ImageThemeRow, ...]:
    """Combine both OCR sources per stock while preserving the first theme order."""
    merged: dict[str, tuple[str, list[str], set[str]]] = {}
    for row in rows:
        name = row.name.strip()
        name_key = _normalized_header(name).casefold()
        if not name_key:
            continue
        if name_key not in merged:
            merged[name_key] = (name, [], set())
        display_name, themes, seen = merged[name_key]
        for theme in (item.strip() for item in row.themes.split("/")):
            theme_key = _normalized_header(theme).casefold()
            if theme_key and theme_key not in seen:
                themes.append(theme)
                seen.add(theme_key)
        merged[name_key] = (display_name, themes, seen)
    return tuple(
        ImageThemeRow(name, "/".join(themes))
        for name, themes, _ in merged.values()
        if themes
    )


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

    @staticmethod
    def _has_badge_background(image: Image.Image, points: list[object]) -> bool:
        """Detect the wide pastel background behind a theme badge, not merely colored text."""
        coordinates = [(float(point[0]), float(point[1])) for point in points]  # type: ignore[index]
        left = max(0, int(min(x for x, _ in coordinates)) - 3)
        top = max(0, int(min(y for _, y in coordinates)) - 3)
        right = min(image.width, int(max(x for x, _ in coordinates)) + 4)
        bottom = min(image.height, int(max(y for _, y in coordinates)) + 4)
        if right <= left or bottom <= top:
            return False
        pixels = image.crop((left, top, right, bottom)).getdata()
        # 흰 표 배경과 검정/빨강 글자는 제외하고, 배지의 넓은 연한 색 면적만 센다.
        tinted = sum(
            1 for red, green, blue in pixels
            if max(red, green, blue) - min(red, green, blue) >= 14 and min(red, green, blue) >= 120
        )
        return tinted / max(1, len(pixels)) >= 0.22

    def extract_rows(self, image_path: Path, mode: str = "theme_column", theme_header: str = "테마") -> tuple[ImageThemeRow, ...]:
        """Use OCR positions to split a screenshot according to the selected theme layout."""
        if self._ocr is None:
            self.extract_lines(image_path)
        result = self._ocr.predict(str(image_path))
        try:
            image = Image.open(image_path).convert("RGB")
        except OSError as error:
            raise ValueError("이미지 파일을 읽을 수 없습니다.") from error
        tokens: list[_OcrToken] = []
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
                    tokens.append(_OcrToken(value, x, y, self._has_badge_background(image, points)))
        if mode in {"reason_badges", "both"}:
            name_token = next((token for token in tokens if _normalized_header(token.text) == "종목명"), None)
            regions = _badge_regions(image, int(name_token.x + 100) if name_token is not None else 0)
            if regions:
                # 긴 이유 문장과 배지가 한 OCR 상자로 합쳐져도 설명을 읽지 않도록
                # 원본 크기를 유지한 채 배지 사각형만 남긴 이미지를 한 번 더 읽는다.
                import cv2
                import numpy as np

                original = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
                isolated = np.full_like(original, 255)
                for left, top, right, bottom in regions:
                    # 검출된 배지 경계 밖의 설명 첫 글자가 붙지 않도록 배경
                    # 사각형 안쪽만 남긴다.
                    y1, y2 = max(0, top), min(original.shape[0], bottom)
                    x1, x2 = max(0, left), min(original.shape[1], right)
                    isolated[y1:y2, x1:x2] = original[y1:y2, x1:x2]
                scale = 2.0
                isolated = cv2.resize(isolated, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
                badge_tokens: list[_OcrToken] = []
                for page in self._ocr.predict(isolated):
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
                        badge_tokens.append(_OcrToken(
                            str(text).strip(),
                            sum(float(point[0]) for point in points) / len(points) / scale,
                            sum(float(point[1]) for point in points) / len(points) / scale,
                        ))
                # 기존 전체 이미지 OCR의 배지 판정은 사용하지 않는다. 분리된
                # 사각형 안에서 읽힌 글자만 배지로 표시한다.
                tokens = [_OcrToken(token.text, token.x, token.y, False) for token in tokens]
                tokens.extend(_merge_badge_tokens(tuple(badge_tokens), regions))
        return _theme_rows_from_tokens(tuple(tokens), mode, theme_header)


class ImageThemeOcrWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, image_path: Path, mode: str = "theme_column", theme_header: str = "테마") -> None:
        super().__init__()
        self._image_path = image_path
        self._mode = mode
        self._theme_header = theme_header

    def run(self) -> None:
        try:
            with _suppress_ocr_child_console_windows():
                ocr = PaddleThemeOcr()
                self.progress.emit("OCR 엔진을 준비하고 있습니다…")
                # extract_rows()가 처음 실행될 때 내부적으로 수행하던 초기 인식 단계를
                # 분리해, 화면에 현재 단계를 알려 준다.
                ocr.extract_lines(self._image_path)
                if self.isInterruptionRequested():
                    return
                self.progress.emit("이미지의 테마와 색상 배지를 분석하고 있습니다…")
                rows = ocr.extract_rows(self._image_path, self._mode, self._theme_header)
        except Exception as error:
            self.failed.emit(str(error))
            return
        if not self.isInterruptionRequested():
            self.completed.emit(rows)
