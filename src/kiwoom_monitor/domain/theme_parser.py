from __future__ import annotations
import re

DEFAULT_SEPARATORS = ",/|;"

def parse_themes(value: str, separators: str = DEFAULT_SEPARATORS) -> tuple[str, ...]:
    pattern = "[" + re.escape(separators) + "]"
    result: list[str] = []
    for item in re.split(pattern, value):
        theme = item.strip()
        if theme and theme not in result: result.append(theme)
    return tuple(result)
