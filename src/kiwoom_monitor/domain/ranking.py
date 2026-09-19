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


def normalize_stock_code(value: object) -> str:
    """키움 순위/실시간 시장 접미사를 연구용 6자리 코드로 맞춘다."""
    return str(value or "").strip().removeprefix("A").removesuffix("_NX").removesuffix("_AL")
