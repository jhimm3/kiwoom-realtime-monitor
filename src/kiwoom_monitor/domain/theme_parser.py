from __future__ import annotations
import re

DEFAULT_SEPARATORS = ",/|;"

def theme_key(value: str) -> str:
    """Comparison key tolerant of OCR spacing and decorative brackets."""
    return re.sub(r"[\s\[\]\(\)\{\}<>〈〉《》]", "", value).casefold()

def parse_themes(value: str, separators: str = DEFAULT_SEPARATORS) -> tuple[str, ...]:
    pattern = "[" + re.escape(separators) + "]"
    result: list[str] = []
    for item in re.split(pattern, value):
        theme = item.strip()
        # 테마 이름은 표기만 유지하고, 중복 판단은 대소문자를 구분하지 않는다.
        # 예: "AI / ai"는 하나의 테마로 처리한다.
        if theme and all(theme.casefold() != existing.casefold() for existing in result):
            result.append(theme)
    return tuple(result)
