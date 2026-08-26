from __future__ import annotations
from dataclasses import dataclass
import re
from kiwoom_monitor.domain.theme_import import ThemeImportRow

class StockLookup:
    def find_code_by_name(self, name: str) -> str | None: ...


def split_concatenated_stock_name(
    value: str, stocks: tuple[tuple[str, str], ...]
) -> tuple[tuple[str, str], ...]:
    """Split a glued OCR/text value only when stock names cover it completely."""
    normalized = re.sub(r"\s+", "", value).casefold()
    if not normalized:
        return ()
    candidates: list[tuple[str, str, str]] = []
    for code, name in stocks:
        key = re.sub(r"\s+", "", name).casefold()
        if key and key != normalized and key in normalized:
            candidates.append((key, code, name))
    candidates.sort(key=lambda item: len(item[0]), reverse=True)

    best: dict[int, tuple[tuple[str, str, str], ...]] = {0: ()}
    for position in range(len(normalized)):
        prefix = best.get(position)
        if prefix is None:
            continue
        for candidate in candidates:
            key = candidate[0]
            if not normalized.startswith(key, position):
                continue
            end = position + len(key)
            result = prefix + (candidate,)
            existing = best.get(end)
            if existing is None or len(result) < len(existing):
                best[end] = result
    result = best.get(len(normalized), ())
    if len(result) < 2:
        return ()
    return tuple((code, name) for _, code, name in result)

@dataclass(frozen=True)
class MatchedThemeRow:
    code: str
    name: str
    themes: tuple[str, ...]

def match_theme_rows(rows: tuple[ThemeImportRow, ...], stocks: StockLookup) -> tuple[tuple[MatchedThemeRow, ...], tuple[ThemeImportRow, ...]]:
    matched=[]; errors=[]
    for row in rows:
        code=stocks.find_code_by_name(row.name)
        if code is None: errors.append(row)
        else: matched.append(MatchedThemeRow(code,row.name,row.themes))
    return tuple(matched),tuple(errors)
