"""로컬·중앙 저장소가 공유하는 시장 관측 메타데이터 행 변환."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TypeVar

from kiwoom_monitor.domain.market_data_contract import (
    CandidateUniverse,
    DataCompleteness,
    DataUnit,
    DataValueKind,
    MarketDataMetadata,
    MarketDataObservation,
    ObservationOrigin,
    TradingVenue,
)


EnumT = TypeVar("EnumT", bound=StrEnum)


def market_metadata_storage_values(
    observation_key: str, observation: MarketDataObservation[object]
) -> tuple[object, ...]:
    key = str(observation_key).strip()
    if not key:
        raise ValueError("observation_key is required")
    metadata = observation.metadata
    return (
        observation.kind.value,
        observation.subject,
        key,
        metadata.effective_at.isoformat() if metadata.effective_at else None,
        metadata.available_at.isoformat() if metadata.available_at else None,
        metadata.venue.value,
        metadata.unit.value,
        metadata.value_kind.value,
        metadata.completeness.value,
        metadata.origin.value,
        metadata.source,
        metadata.candidate_universe.value,
    )


def market_metadata_from_storage_row(
    row: tuple[object, ...] | None,
) -> MarketDataMetadata | None:
    if row is None:
        return None
    return MarketDataMetadata(
        effective_at=_parse_datetime(row[0]),
        available_at=_parse_datetime(row[1]),
        venue=_parse_enum(TradingVenue, row[2], TradingVenue.UNKNOWN),
        unit=_parse_enum(DataUnit, row[3], DataUnit.UNKNOWN),
        value_kind=_parse_enum(DataValueKind, row[4], DataValueKind.UNKNOWN),
        completeness=_parse_enum(
            DataCompleteness, row[5], DataCompleteness.UNCONFIRMED
        ),
        origin=_parse_enum(ObservationOrigin, row[6], ObservationOrigin.UNKNOWN),
        source=str(row[7] or ""),
        candidate_universe=_parse_enum(
            CandidateUniverse, row[8], CandidateUniverse.UNKNOWN
        ),
    )


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _parse_enum(enum_type: type[EnumT], value: object, default: EnumT) -> EnumT:
    try:
        return enum_type(str(value))
    except ValueError:
        return default
