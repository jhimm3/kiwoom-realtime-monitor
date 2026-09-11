"""중앙 수집기가 저장하는 시장 관측의 의미를 한곳에서 정의한다."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

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
    metadata_from_legacy_capture_state,
    trading_venue,
)


KST = timezone(timedelta(hours=9))


def ranking_observation(
    subject: str,
    snapshot_key: str,
    value: dict[str, Any],
    available_at: datetime,
    *,
    source: str,
) -> MarketDataObservation[dict[str, Any]]:
    return MarketDataObservation(
        kind=MarketDatasetKind.CANDIDATE_SET,
        subject=subject,
        value=value,
        metadata=MarketDataMetadata(
            effective_at=snapshot_datetime(snapshot_key, available_at),
            available_at=as_kst(available_at),
            venue=TradingVenue.COMBINED,
            unit=DataUnit.COUNT,
            value_kind=DataValueKind.ACTUAL,
            completeness=DataCompleteness.COMPLETE,
            origin=ObservationOrigin.QUERY,
            source=source,
            candidate_universe=CandidateUniverse.RANKING_TOP20,
        ),
    )


def top20_index_observation(
    subject: str,
    snapshot_key: str,
    value: dict[str, Any],
    available_at: datetime,
    capture_state: str,
) -> MarketDataObservation[dict[str, Any]]:
    return MarketDataObservation(
        kind=MarketDatasetKind.TOP20_INDEX,
        subject=subject,
        value=value,
        metadata=metadata_from_legacy_capture_state(
            capture_state,
            effective_at=snapshot_datetime(snapshot_key, available_at),
            available_at=as_kst(available_at),
            venue=TradingVenue.COMBINED,
            unit=DataUnit.EOK_WON,
            value_kind=DataValueKind.DERIVED_FROM_ACTUAL,
            source="nas-top20-0B",
            candidate_universe=CandidateUniverse.RANKING_TOP20,
        ),
    )


def market_state_observation(
    subject: str,
    snapshot_key: str,
    value: dict[str, Any],
    available_at: datetime,
) -> MarketDataObservation[dict[str, Any]]:
    return MarketDataObservation(
        kind=MarketDatasetKind.MARKET_STATE,
        subject=subject,
        value=value,
        metadata=MarketDataMetadata(
            effective_at=snapshot_datetime(snapshot_key, available_at),
            available_at=as_kst(available_at),
            venue=TradingVenue.KRX,
            unit=DataUnit.UNKNOWN,
            value_kind=DataValueKind.ACTUAL,
            completeness=DataCompleteness.COMPLETE,
            origin=ObservationOrigin.REALTIME,
            source="kiwoom-websocket-0J-0U",
            candidate_universe=CandidateUniverse.MARKET_ALL,
        ),
    )


def minute_bar_observation(
    value: dict[str, Any],
    *,
    origin: ObservationOrigin,
    completeness: DataCompleteness,
    source: str,
    value_kind: DataValueKind,
) -> MarketDataObservation[dict[str, Any]]:
    subject = _bar_subject(value)
    snapshot_key = f"{value['trading_date']}T{value['minute']}"
    return MarketDataObservation(
        kind=MarketDatasetKind.MINUTE_BAR,
        subject=subject,
        value=value,
        metadata=MarketDataMetadata(
            effective_at=snapshot_datetime(snapshot_key, _bar_available_at(value)),
            available_at=_bar_available_at(value),
            venue=trading_venue(value.get("market")),
            unit=DataUnit.UNKNOWN,
            value_kind=value_kind,
            completeness=completeness,
            origin=origin,
            source=source,
            candidate_universe=CandidateUniverse.UNKNOWN,
        ),
    )


def daily_bar_observation(
    value: dict[str, Any],
    *,
    completeness: DataCompleteness,
) -> MarketDataObservation[dict[str, Any]]:
    available_at = _bar_available_at(value)
    return MarketDataObservation(
        kind=MarketDatasetKind.DAILY_BAR,
        subject=_bar_subject(value),
        value=value,
        metadata=MarketDataMetadata(
            effective_at=snapshot_datetime(str(value["trading_date"]), available_at),
            available_at=available_at,
            venue=trading_venue(value.get("market")),
            unit=DataUnit.UNKNOWN,
            value_kind=DataValueKind.ACTUAL,
            completeness=completeness,
            origin=ObservationOrigin.QUERY,
            source="kiwoom-ka10081",
            candidate_universe=CandidateUniverse.UNKNOWN,
        ),
    )


def bar_observation_key(observation: MarketDataObservation[dict[str, Any]]) -> str:
    value = observation.value
    if observation.kind == MarketDatasetKind.MINUTE_BAR:
        return f"{value['trading_date']}T{value['minute']}"
    return str(value["trading_date"])


def snapshot_datetime(snapshot_key: str, fallback: datetime) -> datetime:
    try:
        parsed = datetime.fromisoformat(snapshot_key)
    except ValueError:
        return as_kst(fallback)
    return as_kst(parsed)


def as_kst(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=KST)
    return value.astimezone(KST)


def _bar_subject(value: dict[str, Any]) -> str:
    return f"{value['code']}:{value.get('market', '') or 'UNKNOWN'}"


def _bar_available_at(value: dict[str, Any]) -> datetime:
    try:
        return datetime.fromtimestamp(float(value["updated_at"]), tz=KST)
    except (KeyError, TypeError, ValueError, OSError):
        return datetime.now(KST)
