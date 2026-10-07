"""매매 묶음을 일간·주간·월간 통계로 집계한다."""

from __future__ import annotations

from dataclasses import dataclass

from kiwoom_monitor.application.trade_journal_summary import TradeEpisode


@dataclass(frozen=True)
class TradePeriodStatistic:
    period: str
    trade_count: int
    win_count: int
    loss_count: int
    realized_profit: int
    matched_cost: int

    @property
    def win_rate(self) -> float:
        closed = self.win_count + self.loss_count
        return self.win_count / closed * 100 if closed else 0.0

    @property
    def return_rate(self) -> float:
        return self.realized_profit / self.matched_cost * 100 if self.matched_cost else 0.0


def summarize_periods(episodes: tuple[TradeEpisode, ...], unit: str) -> tuple[TradePeriodStatistic, ...]:
    grouped: dict[str, list[TradeEpisode]] = {}
    for episode in episodes:
        day = episode.started_at.date()
        if unit == "주간":
            year, week, _ = day.isocalendar()
            key = f"{year}-W{week:02d}"
        elif unit == "월간":
            key = day.strftime("%Y-%m")
        else:
            key = day.isoformat()
        grouped.setdefault(key, []).append(episode)
    values = []
    for key, rows in grouped.items():
        profits = tuple(value.summary.realized_profit for value in rows)
        values.append(TradePeriodStatistic(
            key, len(rows), sum(value > 0 for value in profits), sum(value < 0 for value in profits),
            sum(profits), sum(value.summary.matched_cost for value in rows),
        ))
    return tuple(sorted(values, key=lambda value: value.period, reverse=True))
