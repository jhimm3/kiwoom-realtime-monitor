"""유통시가총액 대비 기간 거래대금의 사용자 정의 거래강도."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StockFundamentals:
    market_cap_eok: float
    float_ratio_percent: float
    high_250_price: int | None = None
    current_price: int | None = None

    @property
    def float_market_cap_eok(self) -> float:
        return self.market_cap_eok * self.float_ratio_percent / 100


def trade_strength_percent(trade_value_eok: float, fundamentals: StockFundamentals) -> float | None:
    """기간 거래대금 ÷ 유통시가총액 × 100."""
    if fundamentals.float_market_cap_eok <= 0:
        return None
    return trade_value_eok / fundamentals.float_market_cap_eok * 100
