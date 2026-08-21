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
        # 테마는 나열 순서와 영문 대소문자에 관계없이 같은 구성이라면 변경하지 않는다.
        before_keys=frozenset(value.casefold() for value in before)
        after_keys=frozenset(value.casefold() for value in after)
        result.append(ThemeChange(row.code,row.name,before,after,"변경 없음" if before_keys==after_keys else ("신규" if not before else "테마 변경")))
    return tuple(result)
