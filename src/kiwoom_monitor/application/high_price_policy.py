"""화면에서 사용할 신고가와 기본정보 보완값의 우선순위를 결정한다."""

from __future__ import annotations

from dataclasses import replace

from .daily_high_service import DailyHighTargets
from .trade_strength import StockFundamentals


def merge_fundamentals_with_adjusted_high(
    incoming: StockFundamentals,
    *,
    existing: StockFundamentals | None,
    daily: DailyHighTargets | None,
) -> StockFundamentals:
    """ka10001 갱신 중 KRX+NXT 수정주가 신고가를 보존한다."""
    adjusted_high = daily.high_250_price if daily is not None else None
    if adjusted_high is None and existing is not None:
        adjusted_high = existing.high_250_price
    return replace(incoming, high_250_price=adjusted_high) if adjusted_high is not None else incoming


def selected_high_price(
    period: str,
    *,
    daily: DailyHighTargets | None,
    fundamentals: StockFundamentals | None,
    historical_high: int | None,
    today_high: int | None,
) -> int | None:
    """선택 기간과 출처 우선순위에 맞는 화면 신고가를 반환한다."""
    if period == "5":
        target = daily.high_5_price if daily is not None else None
    elif period == "20":
        target = daily.high_20_price if daily is not None else None
    elif period == "historical":
        candidates = (
            historical_high,
            daily.high_250_price if daily is not None else None,
            fundamentals.high_250_price if fundamentals is not None else None,
        )
        target = max((value for value in candidates if value is not None), default=None)
    else:
        target = (
            daily.high_250_price
            if daily is not None and daily.high_250_price is not None
            else fundamentals.high_250_price if fundamentals is not None else None
        )
    if target is None:
        return None
    return max(target, today_high) if today_high is not None else target
