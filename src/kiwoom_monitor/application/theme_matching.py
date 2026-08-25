from __future__ import annotations
from dataclasses import dataclass
import re
from kiwoom_monitor.domain.theme_import import ThemeImportRow

class StockLookup:
    def find_code_by_name(self, name: str) -> str | None: ...


def split_concatenated_stock_name(
    value: str, stocks: tuple[tuple[str, str], ...]
) -> tuple[tuple[str, str], ...]:
    """Split glued names, tolerating one OCR/typing error per stock name."""
    normalized = re.sub(r"\s+", "", value).casefold()
    if not normalized:
        return ()
    candidates: list[tuple[str, str, str]] = []
    for code, name in stocks:
        key = re.sub(r"\s+", "", name).casefold()
        if len(key) >= 2 and key != normalized:
            candidates.append((key, code, name))
    candidates.sort(key=lambda item: len(item[0]), reverse=True)
    candidates_by_length: dict[int, list[tuple[str, str, str]]] = {}
    for candidate in candidates:
        candidates_by_length.setdefault(len(candidate[0]), []).append(candidate)
    maximum_length = max(candidates_by_length, default=0)

    # Keep a separate best path for each accumulated error count. Otherwise an
    # early exact-but-oversegmented path can hide the intended fuzzy split.
    best: dict[tuple[int, int], tuple[tuple[str, str, str], ...]] = {(0, 0): ()}
    for position in range(len(normalized)):
        states = tuple((cost, path) for (end, cost), path in best.items() if end == position)
        for cost, prefix in states:
            remaining = len(normalized) - position
            for consumed in range(2, min(remaining, maximum_length + 1) + 1):
                end = position + consumed
                segment = normalized[position:end]
                for key_length in range(max(2, consumed - 1), consumed + 2):
                    for candidate in candidates_by_length.get(key_length, ()):
                        key = candidate[0]
                        distance = _distance_at_most_one(segment, key)
                        if distance is None:
                            continue
                        next_cost = cost + distance
                        # More than six wrong stock names is too ambiguous for an
                        # automatic proposal; the manual remainder flow handles it.
                        if next_cost > 6:
                            continue
                        result = prefix + (candidate,)
                        state_key = (end, next_cost)
                        existing = best.get(state_key)
                        if existing is None or len(result) < len(existing):
                            best[state_key] = result
    endings = tuple(
        (cost, path)
        for (end, cost), path in best.items()
        if end == len(normalized)
        and len(path) >= 2
        and cost <= max(1, (len(path) + 1) // 3)
    )
    if not endings:
        return ()
    _, result = min(endings, key=lambda item: (item[0], len(item[1])))
    return tuple((code, name) for _, code, name in result)


def _distance_at_most_one(left: str, right: str) -> int | None:
    if left == right:
        return 0
    if abs(len(left) - len(right)) > 1:
        return None
    if len(left) == len(right):
        return 1 if sum(a != b for a, b in zip(left, right)) == 1 else None
    shorter, longer = (left, right) if len(left) < len(right) else (right, left)
    short_index = long_index = differences = 0
    while short_index < len(shorter) and long_index < len(longer):
        if shorter[short_index] == longer[long_index]:
            short_index += 1
            long_index += 1
        else:
            differences += 1
            long_index += 1
            if differences > 1:
                return None
    return 1


def extract_known_stocks_and_unknown_fragments(
    value: str, stocks: tuple[tuple[str, str], ...]
) -> tuple[tuple[tuple[str, str], ...], tuple[str, ...]]:
    """Keep exact stock names in a glued value and return only unmatched gaps."""
    normalized = re.sub(r"\s+", "", value)
    if not normalized:
        return (), ()
    by_start: dict[int, list[tuple[int, str, str]]] = {}
    for code, name in stocks:
        key = re.sub(r"\s+", "", name)
        if len(key) < 3 or key.casefold() == normalized.casefold():
            continue
        start = 0
        while True:
            found = normalized.casefold().find(key.casefold(), start)
            if found < 0:
                break
            by_start.setdefault(found, []).append((found + len(key), code, name))
            start = found + 1

    # Select non-overlapping exact names with the greatest total coverage.
    best: dict[int, tuple[int, tuple[tuple[int, int, str, str], ...]]] = {0: (0, ())}
    for position in range(len(normalized)):
        coverage, selected = best.get(position, (-1, ()))
        if coverage < 0:
            continue
        next_state = best.get(position + 1)
        if next_state is None or coverage > next_state[0]:
            best[position + 1] = (coverage, selected)
        for end, code, name in by_start.get(position, ()):
            result = selected + ((position, end, code, name),)
            score = coverage + end - position
            existing = best.get(end)
            if existing is None or (score, -len(result)) > (existing[0], -len(existing[1])):
                best[end] = (score, result)
    selected = best.get(len(normalized), (0, ()))[1]
    if not selected:
        return (), ()
    fragments: list[str] = []
    cursor = 0
    for start, end, _, _ in selected:
        if cursor < start:
            fragments.append(normalized[cursor:start])
        cursor = end
    if cursor < len(normalized):
        fragments.append(normalized[cursor:])
    fragments = [fragment for fragment in fragments if fragment]
    if not fragments:
        return (), ()
    return tuple((code, name) for _, _, code, name in selected), tuple(fragments)

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
