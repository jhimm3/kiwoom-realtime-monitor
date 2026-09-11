"""매매일지 기간 조회와 목록 필터를 저장소·Qt 세부사항 밖에서 조정한다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Mapping, Protocol

from kiwoom_monitor.application.trade_cost_service import DailyTradeCost
from kiwoom_monitor.application.trade_history_service import TradeFill
from kiwoom_monitor.application.trade_journal_summary import (
    TradeEpisode,
    TradeReview,
    group_trade_episodes,
)


class TradeHistoryQueryRepository(Protocol):
    def load_fills_with_entry_context(
        self, start: datetime, end: datetime, lookback_days: int = 365,
    ) -> tuple[TradeFill, ...]: ...

    def load_trade_costs(self, start: datetime, end: datetime) -> tuple[DailyTradeCost, ...]: ...

    def load_group_overrides(self) -> dict[str, str]: ...

    def load_review(self, group_id: str) -> TradeReview: ...


@dataclass(frozen=True)
class TradeHistoryQueryResult:
    fills: tuple[TradeFill, ...]
    costs: tuple[DailyTradeCost, ...]
    episodes: tuple[TradeEpisode, ...]


@dataclass(frozen=True)
class TradeHistorySelection:
    stock_code: str
    selected_day: date
    focus: datetime | None
    date_range: tuple[date, date]
    fills: tuple[TradeFill, ...]


def trade_episode_selection(episode: TradeEpisode) -> TradeHistorySelection:
    fills = tuple(sorted(episode.fills, key=lambda value: value.filled_at))
    date_range = (
        min(fill.filled_at.date() for fill in fills),
        max(fill.filled_at.date() for fill in fills),
    ) if fills else (episode.summary.trade_date, episode.summary.trade_date)
    return TradeHistorySelection(
        episode.summary.stock_code,
        episode.summary.trade_date,
        fills[0].filled_at if fills else None,
        date_range,
        fills,
    )


def trade_fill_selection(
    fill: TradeFill,
    *,
    visible_fills: tuple[TradeFill, ...],
    active_episode: TradeEpisode | None,
) -> TradeHistorySelection:
    fills = (
        tuple(sorted(active_episode.fills, key=lambda value: value.filled_at))
        if active_episode is not None and fill in active_episode.fills
        else tuple(sorted(
            (
                value for value in visible_fills
                if value.stock_code == fill.stock_code and value.filled_at.date() == fill.filled_at.date()
            ),
            key=lambda value: value.filled_at,
        ))
    )
    date_range = (
        min(value.filled_at.date() for value in fills),
        max(value.filled_at.date() for value in fills),
    ) if fills else (fill.filled_at.date(), fill.filled_at.date())
    return TradeHistorySelection(
        fill.stock_code,
        fill.filled_at.date(),
        fill.filled_at,
        date_range,
        fills,
    )


class TradeHistoryQueryService:
    def __init__(self, repository: TradeHistoryQueryRepository) -> None:
        self._repository = repository

    def load(self, start: datetime, end: datetime) -> TradeHistoryQueryResult:
        """과거 진입은 원가 연결에만 쓰고 선택 기간과 겹치는 회차만 반환한다."""
        fills = self._repository.load_fills_with_entry_context(start, end)
        costs = self._repository.load_trade_costs(start - timedelta(days=365), end)
        grouped = group_trade_episodes(fills, self._repository.load_group_overrides())
        episodes = tuple(
            episode for episode in grouped
            if any(start <= fill.filled_at < end for fill in episode.fills)
        )
        return TradeHistoryQueryResult(fills, costs, episodes)

    def filter(
        self,
        episodes: tuple[TradeEpisode, ...],
        *,
        query: str = "",
        result_filter: str = "전체 손익",
        review_filter: str = "전체 복기",
    ) -> tuple[TradeEpisode, ...]:
        normalized_query = query.strip().lower()
        values: list[TradeEpisode] = []
        reviews: dict[str, TradeReview] = {}
        if review_filter != "전체 복기":
            batch_loader = getattr(self._repository, "load_reviews", None)
            if callable(batch_loader):
                reviews = batch_loader(tuple(episode.group_id for episode in episodes))
        for episode in episodes:
            summary = episode.summary
            if (
                normalized_query
                and normalized_query not in summary.stock_name.lower()
                and normalized_query not in summary.stock_code.lower()
            ):
                continue
            if result_filter == "수익" and summary.realized_profit <= 0:
                continue
            if result_filter == "손실" and summary.realized_profit >= 0:
                continue
            if result_filter == "보합" and summary.realized_profit != 0:
                continue
            if review_filter != "전체 복기":
                review = reviews.get(episode.group_id)
                if review is None:
                    review = self._repository.load_review(episode.group_id)
                if review.status != review_filter:
                    continue
            values.append(episode)
        return tuple(values)


def history_backfill_cutoff(now: datetime) -> date:
    """20시 전에는 오늘을 제외하고, 이후에는 오늘까지 보완 대상으로 삼는다."""
    return now.date() if now.hour < 20 else now.date() + timedelta(days=1)


def select_history_backfill_tasks(
    candidates: tuple[tuple[str, date], ...],
    states: Mapping[tuple[str, date], str],
    *,
    failed_only: bool = False,
) -> tuple[tuple[str, date], ...]:
    tasks: list[tuple[str, date]] = []
    for candidate in candidates:
        state = states.get(candidate, "미조회")
        if state == "확정":
            continue
        if failed_only and state != "실패":
            continue
        tasks.append(candidate)
    return tuple(tasks)


def summarize_episode_bar_state(states: tuple[str, ...]) -> str:
    if states and all(state == "확정" for state in states):
        return "확정"
    failed = sum(1 for state in states if state == "실패")
    confirmed = sum(1 for state in states if state == "확정")
    if failed:
        return f"실패 {failed}건"
    if confirmed:
        return f"일부 {confirmed}/{len(states)}"
    if any(state == "일부" for state in states):
        return "일부"
    return "미조회"


def incomplete_trade_day_tasks(
    stock_code: str,
    fills: tuple[TradeFill, ...],
    bars_by_day: Mapping[date, tuple[tuple[object, ...], ...]],
) -> tuple[tuple[str, date], ...]:
    """저장 분봉이 체결 시각 범위를 완전히 덮지 못한 거래일만 반환한다."""
    tasks: list[tuple[str, date]] = []
    for day in sorted({fill.filled_at.date() for fill in fills}):
        day_fills = tuple(fill for fill in fills if fill.filled_at.date() == day)
        rows = bars_by_day.get(day, ())
        if not rows:
            tasks.append((stock_code, day))
            continue
        first_bar = datetime.fromisoformat(str(rows[0][0]))
        last_bar = datetime.fromisoformat(str(rows[-1][0])) + timedelta(minutes=1)
        if first_bar > min(fill.filled_at for fill in day_fills) or last_bar <= max(fill.filled_at for fill in day_fills):
            tasks.append((stock_code, day))
    return tuple(tasks)


def backfill_affects_history_selection(
    code: str,
    day: date,
    *,
    selected_code: str,
    selected_range: tuple[date, date] | None,
) -> bool:
    return (
        code == selected_code
        and selected_range is not None
        and selected_range[0] <= day <= selected_range[1]
    )
