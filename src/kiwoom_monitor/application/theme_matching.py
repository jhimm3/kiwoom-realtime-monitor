from __future__ import annotations
from dataclasses import dataclass
from kiwoom_monitor.domain.theme_import import ThemeImportRow

class StockLookup:
    def find_code_by_name(self, name: str) -> str | None: ...

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
