"""TOP20 테마 빈도·거래대금과 표 정렬 기준 계산."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from kiwoom_monitor.domain.theme_parser import parse_themes


def visible_theme_frequency(stocks: tuple[object, ...], themes_by_name: Mapping[str, str]) -> dict[str, int]:
    """현재 순위 종목에 지정된 테마의 출현 횟수를 대소문자 구분 없이 센다."""
    frequency: dict[str, int] = {}
    for stock in stocks:
        normalized_name = "".join(str(getattr(stock, "name", "")).split())
        for theme in themes_by_name.get(normalized_name, "").split(","):
            key = theme.strip().casefold()
            if key:
                frequency[key] = frequency.get(key, 0) + 1
    return frequency


def aggregate_theme_metrics(entries: Iterable[tuple[tuple[str, ...], float]]) -> tuple[dict[str, int], dict[str, float]]:
    """종목별 테마와 거래대금으로 테마 종목 수·합계를 만든다."""
    frequency: dict[str, int] = {}
    totals: dict[str, float] = {}
    for themes, value in entries:
        for theme in themes:
            key = theme.casefold()
            frequency[key] = frequency.get(key, 0) + 1
            totals[key] = totals.get(key, 0.0) + value
    return frequency, totals


def theme_group_sort_key(
    enabled: bool,
    themes: tuple[str, ...],
    frequency: Mapping[str, int],
    trade_totals: Mapping[str, float],
    change_rate: float,
    rank: int,
) -> str:
    """테마 수, 거래대금, 등락률, 원래 순위 순의 기존 문자열 정렬키를 만든다."""
    if enabled and themes:
        primary = min(
            enumerate(themes),
            key=lambda pair: (
                -frequency.get(pair[1].casefold(), 0),
                -trade_totals.get(pair[1].casefold(), 0.0),
                pair[0],
            ),
        )[1]
        count = frequency.get(primary.casefold(), 0)
        trade_total = trade_totals.get(primary.casefold(), 0.0)
        return f"{999-count:03d}|{9_999_999_999_999.0-trade_total:020.4f}|{primary.casefold()}|{9999.0-change_rate:010.4f}|{rank:04d}"
    if enabled:
        return f"999|{9_999_999_999_999.0:020.4f}|\uffff|{9999.0-change_rate:010.4f}|{rank:04d}"
    return f"{rank:04d}"


def top_theme_trade_values(
    entries: Iterable[tuple[str, str, float]],
    themes_by_name: Mapping[str, str],
    *,
    excluded_values: Iterable[str] = (),
    limit: int = 3,
) -> list[tuple[str, float]]:
    """종목별 거래대금을 테마로 합산하고 큰 순서의 상위 항목을 반환한다."""
    excluded = {"".join(str(value).split()).casefold() for value in excluded_values}
    totals: dict[str, tuple[str, float]] = {}
    for code, name, value in entries:
        if code.casefold() in excluded or "".join(name.split()).casefold() in excluded:
            continue
        raw_themes = themes_by_name.get("".join(name.split()), "")
        for theme in parse_themes(raw_themes, ","):
            key = theme.casefold()
            display, previous = totals.get(key, (theme, 0.0))
            totals[key] = (display, previous + value)
    return sorted(totals.values(), key=lambda item: item[1], reverse=True)[:max(0, limit)]
