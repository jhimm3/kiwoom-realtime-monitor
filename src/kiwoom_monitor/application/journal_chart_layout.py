"""매매일지 차트의 UI 프레임워크와 무관한 표시 계산."""

from __future__ import annotations

import math
from datetime import date, datetime, time

from kiwoom_monitor.application.trade_history_service import TradeFill


def rounded_price_grid(minimum: float, maximum: float, target_lines: int = 8) -> tuple[float, float, float]:
    if maximum <= minimum:
        maximum = minimum + max(1.0, abs(minimum) * 0.01)
    raw_step = (maximum - minimum) / max(1, target_lines - 1)
    magnitude = 10 ** math.floor(math.log10(max(raw_step, 1.0)))
    step = next((factor * magnitude for factor in (1, 2, 5, 10) if factor * magnitude >= raw_step), 10 * magnitude)
    midpoint = (minimum + maximum) / 2
    tick = 1 if midpoint < 2_000 else 5 if midpoint < 5_000 else 10 if midpoint < 20_000 else 50 if midpoint < 50_000 else 100 if midpoint < 200_000 else 500 if midpoint < 500_000 else 1_000
    step = max(float(tick), math.ceil(step / tick) * float(tick))
    lower = math.floor(minimum / step) * step
    upper = math.ceil(maximum / step) * step
    return lower, upper if upper > lower else lower + step, step


def adaptive_price_grid_lines(visible_bars: int, chart_height: int) -> int:
    bars = max(1, int(visible_bars))
    zoom_target = round(15 - max(0, bars - 30) / 30)
    height_target = round(max(1, int(chart_height)) * 0.73 / 28)
    return max(5, min(15, zoom_target, height_target))


def visible_trade_fills(fills: tuple[TradeFill, ...], start: datetime, end: datetime) -> tuple[TradeFill, ...]:
    return tuple(fill for fill in fills if start <= fill.filled_at < end)


def chart_close_information(fills: tuple[TradeFill, ...], rows: tuple[tuple[object, ...], ...], *, index_mode: bool = False) -> str:
    target_day = max((fill.filled_at.date() for fill in fills), default=None)
    day_rows = tuple(row for row in rows if target_day is not None and datetime.fromisoformat(str(row[0])).date() == target_day)
    if not day_rows:
        return "당일 종가 미확정" if target_day == date.today() else "확정 일봉 보완 대기"
    close_price = float(day_rows[-1][4]) if index_mode else int(day_rows[-1][4])
    previous_rows = tuple(row for row in rows if datetime.fromisoformat(str(row[0])).date() < target_day)
    price_text = f"{close_price:,.2f}" if index_mode else f"{close_price:,}원"
    if not previous_rows:
        return f"{target_day:%m-%d} 종가 {price_text}  ·  전일 종가 대비 자료 없음"
    previous_close = float(previous_rows[-1][4]) if index_mode else int(previous_rows[-1][4])
    change = (close_price / previous_close - 1) * 100 if previous_close else 0.0
    return f"{target_day:%m-%d} 종가 {price_text}  ·  전일 종가 대비 {change:+.2f}%"


def trade_callout_text(fill: TradeFill) -> str:
    return f"{fill.filled_at:%H:%M:%S} · {fill.price:,}원 · {fill.quantity:,}주"


def format_trade_value_eok(value: object, decimals: int = 2) -> str:
    if value is None:
        return "자료 없음"
    amount = max(0.0, float(value))
    if amount < 10_000:
        return f"{amount:,.{decimals}f}억"
    jo = int(amount // 10_000)
    eok = int(round(amount - jo * 10_000))
    if eok >= 10_000:
        jo += 1
        eok = 0
    return f"{jo:,}조" if eok == 0 else f"{jo:,}조 {eok:,}억"


def chart_time_step_minutes(row_count: int, interval: str, plot_width: float) -> int:
    bar_minutes = {"1분": 1, "3분": 3, "5분": 5, "10분": 10, "30분": 30, "60분": 60}.get(interval, 1)
    target_labels = max(2, int(max(1.0, plot_width) // 90))
    raw_step = max(bar_minutes, row_count * bar_minutes / target_labels)
    return next((step for step in (5, 10, 30, 60, 120, 240) if step >= raw_step), 480)


def chart_time_tick_indices(row_minutes: list[datetime], interval: str, plot_width: float) -> list[int]:
    if not row_minutes:
        return []
    step = chart_time_step_minutes(len(row_minutes), interval, plot_width)
    indices = [index for index, value in enumerate(row_minutes) if (value.hour * 60 + value.minute) % step == 0]
    for index, value in enumerate(row_minutes):
        if index == 0 or value.date() != row_minutes[index - 1].date():
            if not any(row_minutes[candidate].date() == value.date() for candidate in indices):
                indices.append(index)
    return sorted(set(indices))


def multi_day_time_tick_indices(row_minutes: list[datetime], indices: list[int]) -> list[int]:
    boundary_indices = {index for index in range(1, len(row_minutes)) if row_minutes[index].date() != row_minutes[index - 1].date()}
    session_edges = {8 * 60, 9 * 60, 15 * 60 + 30, 20 * 60}
    return [index for index in indices if index not in boundary_indices and row_minutes[index].hour * 60 + row_minutes[index].minute not in session_edges]


def daily_chart_rows(rows: tuple[tuple[object, ...], ...]) -> tuple[tuple[object, ...], ...]:
    grouped: dict[date, list[tuple[object, ...]]] = {}
    for row in rows:
        try:
            grouped.setdefault(datetime.fromisoformat(str(row[0])).date(), []).append(row)
        except (TypeError, ValueError):
            continue
    result: list[tuple[object, ...]] = []
    for day, values in sorted(grouped.items()):
        trade_values = [float(value[6]) for value in values if value[6] is not None]
        result.append((datetime.combine(day, time()).isoformat(timespec="minutes"), float(values[0][1]), max(float(value[2]) for value in values), min(float(value[3]) for value in values), float(values[-1][4]), 0, sum(trade_values) if trade_values else None, "market_index_daily"))
    return tuple(result)


def average_price_label_positions(values: tuple[tuple[str, float, float], ...], minimum_gap: float = 14.0) -> dict[str, float]:
    positions: dict[str, float] = {}
    previous_y: float | None = None
    for key, _price, desired_y in sorted(values, key=lambda value: value[1], reverse=True):
        label_y = desired_y if previous_y is None else max(desired_y, previous_y + minimum_gap)
        positions[key] = label_y
        previous_y = label_y
    return positions
