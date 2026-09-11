"""체결 스냅샷의 서로 다른 시점·출처를 필드별 관측으로 분리한다."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol

from kiwoom_monitor.domain.market_data_contract import (
    CandidateUniverse,
    DataCompleteness,
    DataUnit,
    DataValueKind,
    MarketDataMetadata,
    MarketDataObservation,
    MarketDatasetKind,
    ObservationOrigin,
    trading_venue,
)
from kiwoom_monitor.domain.snapshot_provenance import snapshot_field_origins


KST = timezone(timedelta(hours=9))


class EntrySnapshotLike(Protocol):
    execution_key: str
    stock_code: str
    executed_at: datetime
    market: str
    rank: int | None
    trade_value_1m_eok: float | None
    trade_value_5m_eok: float | None
    themes: tuple[str, ...]
    high_distance_percent: float | None
    news: tuple[dict[str, object], ...]
    investor_flow: dict[str, object] | None
    orderbook: dict[str, object] | None
    market_state: dict[str, object] | None


def entry_context_observations(
    snapshot: EntrySnapshotLike,
    available_at: datetime,
) -> dict[str, MarketDataObservation[object]]:
    """한 체결의 복합 JSON을 시뮬레이션 가능한 필드별 의미로 푼다."""
    origins = snapshot_field_origins(snapshot)
    investor = snapshot.investor_flow or {}
    program = investor.get("program_trade")
    program_value = dict(program) if isinstance(program, dict) else {}
    values: dict[str, tuple[object, ObservationOrigin, DataUnit, DataValueKind]] = {
        "rank": (snapshot.rank, origins["rank"], DataUnit.COUNT, DataValueKind.ACTUAL),
        "trade_value_1m": (
            snapshot.trade_value_1m_eok, _presence_origin(snapshot.trade_value_1m_eok),
            DataUnit.EOK_WON, DataValueKind.UNKNOWN,
        ),
        "trade_value_5m": (
            snapshot.trade_value_5m_eok, _presence_origin(snapshot.trade_value_5m_eok),
            DataUnit.EOK_WON, DataValueKind.UNKNOWN,
        ),
        "themes": (snapshot.themes, origins["themes"], DataUnit.COUNT, DataValueKind.ACTUAL),
        "high_distance": (
            snapshot.high_distance_percent, _presence_origin(snapshot.high_distance_percent),
            DataUnit.PERCENT, DataValueKind.DERIVED_FROM_ACTUAL,
        ),
        "news": (snapshot.news, origins["news"], DataUnit.COUNT, DataValueKind.ACTUAL),
        "investor_flow": (
            investor, origins["investor_flow"], DataUnit.UNKNOWN, DataValueKind.ACTUAL,
        ),
        "program_trade": (
            program_value, _mapping_origin(program_value), DataUnit.UNKNOWN, DataValueKind.ACTUAL,
        ),
        "orderbook": (
            snapshot.orderbook or {}, origins["orderbook"], DataUnit.UNKNOWN, DataValueKind.ACTUAL,
        ),
        "market_state": (
            snapshot.market_state or {}, origins["market_state"], DataUnit.UNKNOWN, DataValueKind.ACTUAL,
        ),
    }
    effective = _as_kst(snapshot.executed_at)
    available = _as_kst(available_at)
    venue = trading_venue(snapshot.market)
    return {
        field: MarketDataObservation(
            MarketDatasetKind.ENTRY_CONTEXT,
            f"{snapshot.stock_code}:{field}",
            value,
            MarketDataMetadata(
                effective_at=effective,
                available_at=available,
                venue=venue,
                unit=unit,
                value_kind=value_kind,
                completeness=(
                    DataCompleteness.MISSING
                    if origin == ObservationOrigin.MISSING
                    else DataCompleteness.COMPLETE
                ),
                origin=origin,
                source=_source(field, origin),
                candidate_universe=CandidateUniverse.TRADE_ENTRIES,
            ),
        )
        for field, (value, origin, unit, value_kind) in values.items()
    }


def _presence_origin(value: object) -> ObservationOrigin:
    return ObservationOrigin.MISSING if value is None else ObservationOrigin.REALTIME


def _mapping_origin(value: dict[str, object]) -> ObservationOrigin:
    if not value or value.get("available") is False:
        return ObservationOrigin.MISSING
    return ObservationOrigin.BACKFILLED if bool(value.get("backfilled")) else ObservationOrigin.REALTIME


def _source(field: str, origin: ObservationOrigin) -> str:
    return f"trade-entry-{field}-{origin.value}"


def _as_kst(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=KST)
    return value.astimezone(KST)
