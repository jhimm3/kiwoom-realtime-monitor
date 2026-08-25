"""유통시가총액 대비 기간 거래대금의 사용자 정의 거래강도."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StockFundamentals:
    market_cap_eok: float
    float_ratio_percent: float
    high_250_price: int | None = None
    # ka10001 dstr_stk 원값은 실제 응답 검증 결과 천 주 단위다.
    float_shares: int | None = None

    @property
    def float_market_cap_eok(self) -> float:
        return self.market_cap_eok * self.float_ratio_percent / 100

    def float_market_cap_eok_at(
        self,
        *,
        current_price: int | None = None,
        market_cap_eok: float | None = None,
    ) -> float:
        """현재가·유통주식 수가 있으면 이를 우선해 유통시총을 계산한다.

        둘 중 하나가 없으면 실시간 0B 시가총액, 마지막으로 ka10001 시가총액과
        유통비율을 사용한다.
        """
        if self.float_shares is not None and self.float_shares > 0 and current_price is not None and current_price > 0:
            return current_price * self.float_shares / 100_000_000
        cap = market_cap_eok if market_cap_eok is not None and market_cap_eok > 0 else self.market_cap_eok
        return cap * self.float_ratio_percent / 100


def trade_strength_percent(
    trade_value_eok: float,
    fundamentals: StockFundamentals,
    *,
    current_price: int | None = None,
    market_cap_eok: float | None = None,
) -> float | None:
    """기간 거래대금 ÷ 유통시가총액 × 100."""
    float_market_cap = fundamentals.float_market_cap_eok_at(
        current_price=current_price,
        market_cap_eok=market_cap_eok,
    )
    if float_market_cap <= 0:
        return None
    return trade_value_eok / float_market_cap * 100
