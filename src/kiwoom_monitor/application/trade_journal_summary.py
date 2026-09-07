"""체결내역을 날짜·종목별 복기 단위로 요약한다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from kiwoom_monitor.application.trade_history_service import TradeFill


@dataclass(frozen=True)
class TradeJournalSummary:
    trade_date: date
    stock_code: str
    stock_name: str
    buy_quantity: int
    sell_quantity: int
    buy_amount: int
    sell_amount: int
    realized_profit: int
    matched_cost: int
    open_quantity: int
    fill_count: int

    @property
    def return_rate(self) -> float:
        return self.realized_profit / self.matched_cost * 100 if self.matched_cost else 0.0

    @property
    def state(self) -> str:
        if self.open_quantity > 0:
            return f"보유 {self.open_quantity:,}주"
        if self.open_quantity < 0:
            return f"매도초과 {abs(self.open_quantity):,}주"
        return "매매 완료"


@dataclass(frozen=True)
class TradeEpisode:
    group_id: str
    started_at: datetime
    ended_at: datetime
    summary: TradeJournalSummary
    fills: tuple[TradeFill, ...]
    source: str = "auto"


@dataclass(frozen=True)
class TradeReview:
    group_id: str
    reason: str = ""
    review: str = ""
    tags: str = ""
    rating: str = "보통"
    status: str = "미작성"


def trade_fill_key(fill: TradeFill) -> str:
    return "|".join((fill.order_no, fill.stock_code, fill.filled_at.isoformat(timespec="seconds"), fill.side))


def group_trade_episodes(
    fills: tuple[TradeFill, ...], overrides: dict[str, str] | None = None,
) -> tuple[TradeEpisode, ...]:
    """같은 날 같은 종목의 독립 회차를 기본 한 묶음으로 합친다.

    먼저 0→보유→0의 포지션 회차를 찾아 다일 보유 매매를 보존한 뒤, 같은
    날짜에 시작한 회차끼리 합친다. 사용자가 지정한 수동 묶음은 항상 우선한다.
    """
    overrides = overrides or {}
    automatic: dict[str, list[TradeFill]] = {}
    by_stock: dict[str, list[TradeFill]] = {}
    for fill in fills:
        by_stock.setdefault(fill.stock_code, []).append(fill)
    for code, stock_fills in by_stock.items():
        position = 0
        current_id = ""
        for fill in sorted(stock_fills, key=lambda value: value.filled_at):
            if not current_id:
                # 조회 범위 앞쪽에 과거 체결이 추가되어도 첫 체결 고유값은 바뀌지 않는다.
                current_id = "auto:" + trade_fill_key(fill)
            automatic.setdefault(current_id, []).append(fill)
            if fill.side == "매수":
                position += fill.quantity
            elif position > 0:
                position = max(0, position - fill.quantity)
                if position == 0:
                    current_id = ""
            else:
                # 조회 기간 이전 보유분의 매도는 원가를 알 수 없어 한 체결 묶음으로 둔다.
                current_id = ""

    # 같은 종목·같은 시작일의 자동 회차는 그날 첫 회차 ID 아래 합친다.
    # 첫 체결 기반 ID를 유지해 기존 첫 회차 복기와의 연결도 보존한다.
    canonical_by_day: dict[tuple[str, date], str] = {}
    automatic_canonical: dict[str, str] = {}
    for automatic_id, values in automatic.items():
        first = min(values, key=lambda value: value.filled_at)
        key = (first.stock_code, first.filled_at.date())
        automatic_canonical[automatic_id] = canonical_by_day.setdefault(key, automatic_id)

    grouped: dict[str, list[TradeFill]] = {}
    sources: dict[str, str] = {}
    for automatic_id, values in automatic.items():
        for fill in values:
            fill_key = trade_fill_key(fill)
            selected_id = overrides.get(fill_key, automatic_canonical[automatic_id])
            grouped.setdefault(selected_id, []).append(fill)
            sources[selected_id] = "manual" if fill_key in overrides else sources.get(selected_id, "auto")

    episodes: list[TradeEpisode] = []
    for group_id, values in grouped.items():
        ordered = tuple(sorted(values, key=lambda value: value.filled_at))
        summary = _summarize_values(ordered)
        episodes.append(TradeEpisode(group_id, ordered[0].filled_at, ordered[-1].filled_at, summary, ordered, sources[group_id]))
    return tuple(sorted(episodes, key=lambda value: value.started_at, reverse=True))


def summarize_trade_fills(fills: tuple[TradeFill, ...]) -> tuple[TradeJournalSummary, ...]:
    """평균법이 아닌 FIFO로 실현손익을 계산한다. 수수료·세금은 API 원자료에 없어 제외한다."""
    grouped: dict[tuple[date, str], list[TradeFill]] = {}
    for fill in fills:
        grouped.setdefault((fill.filled_at.date(), fill.stock_code), []).append(fill)

    summaries: list[TradeJournalSummary] = []
    for values in grouped.values():
        summaries.append(_summarize_values(tuple(sorted(values, key=lambda value: value.filled_at))))
    return tuple(sorted(summaries, key=lambda value: (value.trade_date, value.realized_profit), reverse=True))


def split_trade_episode_cycles(episode: TradeEpisode) -> tuple[TradeEpisode, ...]:
    """한 묶음 안의 0→보유→0 회차를 독립 복기 단위로 나눈다."""
    grouped: list[tuple[TradeFill, ...]] = []
    current: list[TradeFill] = []
    position = 0
    for fill in sorted(episode.fills, key=lambda value: value.filled_at):
        quantity = max(0, int(fill.quantity))
        if fill.side == "매수":
            if position == 0 and current:
                grouped.append(tuple(current)); current = []
            current.append(fill); position += quantity
        elif position > 0:
            current.append(fill); position = max(0, position - quantity)
            if position == 0:
                grouped.append(tuple(current)); current = []
        elif current:
            grouped.append(tuple(current)); current = []
    if current:
        grouped.append(tuple(current))
    if not grouped:
        return (episode,)
    return tuple(
        TradeEpisode(
            f"{episode.group_id}:cycle:{index}", values[0].filled_at, values[-1].filled_at,
            _summarize_values(values), values, episode.source,
        )
        for index, values in enumerate(grouped, 1)
    )


def _summarize_values(ordered: tuple[TradeFill, ...]) -> TradeJournalSummary:
    trade_date, stock_code = ordered[0].filled_at.date(), ordered[0].stock_code
    lots: list[list[int]] = []  # [남은 수량, 매수가]
    buy_quantity = sell_quantity = buy_amount = sell_amount = 0
    realized_profit = matched_cost = 0
    unmatched_sells = 0
    for fill in ordered:
        if fill.side == "매수":
            buy_quantity += fill.quantity
            buy_amount += fill.quantity * fill.price
            lots.append([fill.quantity, fill.price])
            continue
        sell_quantity += fill.quantity
        sell_amount += fill.quantity * fill.price
        remaining = fill.quantity
        while remaining > 0 and lots:
            matched = min(remaining, lots[0][0])
            cost = matched * lots[0][1]
            matched_cost += cost
            realized_profit += matched * fill.price - cost
            remaining -= matched
            lots[0][0] -= matched
            if lots[0][0] == 0:
                lots.pop(0)
        unmatched_sells += remaining
    open_quantity = sum(lot[0] for lot in lots) - unmatched_sells
    return TradeJournalSummary(
        trade_date=trade_date,
        stock_code=stock_code,
        stock_name=next((value.stock_name for value in ordered if value.stock_name), stock_code),
        buy_quantity=buy_quantity,
        sell_quantity=sell_quantity,
        buy_amount=buy_amount,
        sell_amount=sell_amount,
        realized_profit=realized_profit,
        matched_cost=matched_cost,
        open_quantity=open_quantity,
        fill_count=len(ordered),
    )
