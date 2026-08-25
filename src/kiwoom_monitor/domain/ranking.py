"""화면에 표시할 순위 종목 데이터."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RankedStock:
    rank: int
    code: str
    name: str
    change_rate: str
    new_high_periods: frozenset[int]
    current_price: int | None = None

    @property
    def new_high_label(self) -> str:
        labels = {5: "5일", 20: "20일", 250: "250일"}
        return ", ".join(labels[period] for period in sorted(self.new_high_periods) if period in labels) or "-"
