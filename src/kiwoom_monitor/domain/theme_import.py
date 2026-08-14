from __future__ import annotations
from dataclasses import dataclass
from .theme_parser import parse_themes

@dataclass(frozen=True)
class ThemeImportRow:
    name: str
    themes: tuple[str, ...]

def validate_theme_rows(rows: tuple[tuple[str, str], ...], separators: str | None = None) -> tuple[tuple[ThemeImportRow, ...], tuple[str, ...]]:
    valid=[]; errors=[]; seen=set()
    for number,(name,value) in enumerate(rows, start=2):
        name=name.strip(); themes=parse_themes(value, separators) if separators is not None else parse_themes(value)
        if not name: errors.append(f"{number}행: 종목명이 비어 있습니다.")
        elif not themes: errors.append(f"{number}행: 테마가 비어 있습니다.")
        elif name in seen: errors.append(f"{number}행: 종목명이 중복됩니다 ({name}).")
        else: valid.append(ThemeImportRow(name,themes)); seen.add(name)
    return tuple(valid),tuple(errors)
