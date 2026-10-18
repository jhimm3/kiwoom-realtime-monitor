"""로컬 메인·매매일지 봉 저장 경로의 공통 관측 의미."""

from __future__ import annotations

from datetime import date, datetime, time

from kiwoom_monitor.domain.market_data_contract import (
    CandidateUniverse,
    DataCompleteness,
    DataUnit,
    DataValueKind,
    MarketDataMetadata,
    MarketDataObservation,
    MarketDatasetKind,
    ObservationOrigin,
    TradingVenue,
)
from kiwoom_monitor.domain.time_basis import KST, as_kst, kst_now


def local_minute_bar_observation(
    code: str,
    minute: datetime,
    value: object,
    *,
    available_at: datetime | None = None,
    source: str,
    actual_trade_value: bool,
    value_kind: DataValueKind | None = None,
    completeness: DataCompleteness | None = None,
    origin: ObservationOrigin = ObservationOrigin.REALTIME,
    candidate_universe: CandidateUniverse = CandidateUniverse.UNKNOWN,
) -> MarketDataObservation[object]:
    # Unbound legacy/offline callers use the shared local fallback.
    observed = as_kst(available_at if available_at is not None else kst_now())
    effective = as_kst(minute)
    state = completeness or (
        DataCompleteness.COMPLETE
        if effective.replace(second=0, microsecond=0) < observed.replace(second=0, microsecond=0)
        else DataCompleteness.IN_PROGRESS
    )
    return MarketDataObservation(
        MarketDatasetKind.MINUTE_BAR,
        code,
        value,
        MarketDataMetadata(
            effective_at=effective,
            available_at=observed,
            venue=TradingVenue.COMBINED,
            unit=DataUnit.UNKNOWN,
            value_kind=value_kind or (
                DataValueKind.ACTUAL if actual_trade_value else DataValueKind.ESTIMATED
            ),
            completeness=state,
            origin=origin,
            source=source,
            candidate_universe=candidate_universe,
        ),
    )


def local_daily_bar_observation(
    code: str,
    trading_day: date,
    value: object,
    *,
    available_at: datetime | None = None,
    source: str,
    origin: ObservationOrigin = ObservationOrigin.QUERY,
    completeness: DataCompleteness | None = None,
) -> MarketDataObservation[object]:
    # Unbound legacy/offline callers use the shared local fallback.
    observed = as_kst(available_at if available_at is not None else kst_now())
    effective = datetime.combine(trading_day, time(), tzinfo=KST)
    complete = trading_day < observed.date() or (
        trading_day == observed.date() and observed.time() >= time(20)
    )
    return MarketDataObservation(
        MarketDatasetKind.DAILY_BAR,
        code,
        value,
        MarketDataMetadata(
            effective_at=effective,
            available_at=observed,
            venue=TradingVenue.COMBINED,
            unit=DataUnit.UNKNOWN,
            value_kind=DataValueKind.ACTUAL,
            completeness=completeness or (DataCompleteness.COMPLETE if complete else DataCompleteness.IN_PROGRESS),
            origin=origin,
            source=source,
            candidate_universe=CandidateUniverse.UNKNOWN,
        ),
    )
