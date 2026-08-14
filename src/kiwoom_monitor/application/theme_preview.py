from __future__ import annotations
from dataclasses import dataclass
from .theme_matching import MatchedThemeRow

@dataclass(frozen=True)
class ThemeChange:
    code: str; name: str; before: tuple[str,...]; after: tuple[str,...]; status: str

def preview_theme_changes(rows: tuple[MatchedThemeRow,...], repository: object) -> tuple[ThemeChange,...]:
    result=[]
    for row in rows:
        before=tuple(repository.themes_for_stock(row.code)); after=tuple(row.themes)
        result.append(ThemeChange(row.code,row.name,before,after,"변경 없음" if before==after else ("신규" if not before else "테마 변경")))
    return tuple(result)
