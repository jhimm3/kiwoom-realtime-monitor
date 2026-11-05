"""중앙 수집기가 저장하는 시장 관측의 의미를 한곳에서 정의한다."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from kiwoom_monitor.domain.time_basis import KST, as_kst, kst_now

from kiwoom_monitor.application.market_session_schedule import (
    KRX_AFTER_MARKET_EFFECTIVE_DATE,
    KRX_AUCTION_RANDOM_END_SECONDS,
    MarketSession,
    session_window_at,
)
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




def closing_auction_price_observation(tick, quote):
    """Retain an actual acknowledged KRX trade at the scheduled auction close, without constructing a bar."""
    if quote is None or tick.market != 'KRX':
        return None
    at, raw = quote['available_at'], tick.trade_time
    if (not isinstance(at, datetime) or at.utcoffset() is None
            or not isinstance(raw, str) or len(raw) != 6 or not raw.isdigit()):
        return None
    at = as_kst(at)
    try:
        traded_at = at.replace(hour=int(raw[:2]), minute=int(raw[2:4]), second=int(raw[4:]), microsecond=0)
    except ValueError:
        return None
    window = session_window_at(traded_at - timedelta(seconds=KRX_AUCTION_RANDOM_END_SECONDS + 1), venue='KRX')
    if (window.session is not MarketSession.KRX_CLOSING_AUCTION
            or not 0 <= (traded_at - window.ends_at).total_seconds() <= KRX_AUCTION_RANDOM_END_SECONDS
            or traded_at > at or quote.get('durable') is not False or quote.get('market_status') != 'TRADABLE'
            or not str(quote.get('input_ref', '')).startswith('collector-trade:')
            or type(tick.current_price) is not int or tick.current_price <= 0
            or tick.current_price != quote['price_won']):
        return None
    payload = {'version': 'closing_auction_price/v1', 'code': tick.code, 'market': 'KRX',
        'trading_date': window.trading_date.isoformat(), 'auction_start': window.starts_at.isoformat(),
        'auction_end': window.ends_at.isoformat(), 'event_at': traded_at.isoformat(), 'available_at': at.isoformat(),
        'price_won': tick.current_price, 'input_ref': quote['input_ref'], 'session_type': tick.session_type,
        'session': window.session.value, 'phase': window.phase.value, 'schedule_version': window.schedule_version,
        'price_unit': 'won', 'window_closed': True, 'capture_quality': 'complete'}
    key = 'closing-auction:' + window.ends_at.isoformat()
    return key, MarketDataObservation(MarketDatasetKind.CLOSING_AUCTION_PRICE, f'{tick.code}:KRX', payload,
        MarketDataMetadata(effective_at=traded_at, available_at=at, venue=TradingVenue.KRX, unit=DataUnit.WON,
            value_kind=DataValueKind.ACTUAL, completeness=DataCompleteness.COMPLETE, origin=ObservationOrigin.REALTIME,
            source='kiwoom-websocket-0B'))


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
    now_provider: Callable[[], datetime] | None = None,
) -> MarketDataObservation[dict[str, Any]]:
    subject = _bar_subject(value)
    snapshot_key = f"{value['trading_date']}T{value['minute']}"
    available_at = _bar_available_at(value, now_provider=now_provider)
    return MarketDataObservation(
        kind=MarketDatasetKind.MINUTE_BAR,
        subject=subject,
        value=value,
        metadata=MarketDataMetadata(
            effective_at=snapshot_datetime(snapshot_key, available_at),
            available_at=available_at,
            venue=trading_venue(value.get("market")),
            unit=DataUnit.UNKNOWN,
            value_kind=value_kind,
            completeness=completeness,
            origin=origin,
            source=source,
            candidate_universe=CandidateUniverse.UNKNOWN,
        ),
    )


def minute_bar_revision_payload(
    value: dict[str, Any], *, window_closed: bool, capture_quality: str,
    finalization_source: str, operation_id: str = "",
    now_provider: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """현재 누적 봉을 연구 원장용의 명시적인 시간 계약으로 바꾼다."""
    bar_start = snapshot_datetime(
        f"{value['trading_date']}T{value['minute']}",
        _bar_available_at(value, now_provider=now_provider),
    )
    session_window = session_window_at(bar_start, venue=str(value.get("market", "")))
    payload = {
        name: value[name]
        for name in (
            "trading_date", "minute", "code", "market", "open", "high", "low",
            "close", "volume", "trade_value_million_won",
        )
    }
    payload.update({
        "bar_start": bar_start.isoformat(),
        "bar_end": (bar_start + timedelta(minutes=1)).isoformat(),
        "price_unit": "won",
        "volume_unit": "shares",
        "trade_value_unit": "million_won",
        "window_closed": bool(window_closed),
        "session_finalized": bool(value.get("session_finalized", False)),
        "capture_quality": str(capture_quality),
        "finalization_source": str(finalization_source),
    })
    if bar_start.date() >= KRX_AFTER_MARKET_EFFECTIVE_DATE:
        payload.update({
            "session": session_window.session.value,
            "phase": session_window.phase.value,
            "schedule_version": session_window.schedule_version,
            "session_support": session_window.support.value,
        })
    if operation_id:
        payload["operation_id"] = operation_id
    return payload


def daily_bar_observation(
    value: dict[str, Any],
    *,
    completeness: DataCompleteness,
    now_provider: Callable[[], datetime] | None = None,
) -> MarketDataObservation[dict[str, Any]]:
    available_at = _bar_available_at(value, now_provider=now_provider)
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


def _bar_subject(value: dict[str, Any]) -> str:
    return f"{value['code']}:{value.get('market', '') or 'UNKNOWN'}"


def _bar_available_at(
    value: dict[str, Any], *, now_provider: Callable[[], datetime] | None = None,
) -> datetime:
    try:
        return datetime.fromtimestamp(float(value["updated_at"]), tz=KST)
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        # Explicit standalone/offline compatibility; app callers pass their
        # existing corrected clock. A valid original epoch never samples it.
        return as_kst((now_provider or kst_now)())
