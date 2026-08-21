from __future__ import annotations

import re

from .theme_parser import parse_themes


def parse_theme_text(value: str, separators: str = ",/|;") -> tuple[tuple[str, str], ...]:
    """Convert a pasted themed-stock note into ``(stock name, themes)`` rows.

    A flame line starts a new main theme.  Lines beginning with ``#`` are
    treated as a sub-list of that same main theme; their label is intentionally
    not added as a separate theme.
    """
    collected: dict[str, tuple[str, list[str]]] = {}
    active_themes: tuple[str, ...] = ()

    for raw_line in value.splitlines():
        line = raw_line.replace("\u200b", " ").strip()
        if not line or set(line) <= {"-", "_", "="}:
            continue
        if line.startswith("🔥"):
            active_themes = parse_themes(line.lstrip("🔥").strip(" :"), separators)
            continue
        if not active_themes or line.startswith(("✳", "※", "*")):
            continue
        if line.startswith("#"):
            if ":" not in line:
                continue
            line = line.split(":", 1)[1]

        # A line such as "카카오페이 등 스테이블코인" should keep only the
        # explicitly named stock.  The rest is descriptive prose.
        line = re.sub(r"\s+등(?:\s|$).*", "", line)
        for token in re.split(r"[,，/;|]", line):
            name = token.strip().strip("·•-–—")
            if not name or name.startswith(("#", "🔥")) or ":" in name:
                continue
            key = name.casefold()
            if key not in collected:
                collected[key] = (name, tuple(), [])
            original, _, themes = collected[key]
            for theme in active_themes:
                if all(theme.casefold() != existing.casefold() for existing in themes):
                    themes.append(theme)
            collected[key] = (original, tuple(themes), themes)

    return tuple((name, "/".join(themes)) for name, _, themes in collected.values() if themes)
