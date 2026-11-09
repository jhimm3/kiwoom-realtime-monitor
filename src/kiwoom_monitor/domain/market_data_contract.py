"""과거 시점 재현에 필요한 시장 데이터 의미와 시간 계약."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time
from enum import StrEnum
from typing import Generic, TypeVar

from kiwoom_monitor.domain.time_basis import KST


ValueT = TypeVar("ValueT")


class TradingVenue(StrEnum):
    KRX = "KRX"
    NXT = "NXT"
    SOR = "SOR"
    COMBINED = "COMBINED"
    UNKNOWN = "UNKNOWN"


class DataUnit(StrEnum):
    WON = "won"
    EOK_WON = "eok_won"
    MILLION_WON = "million_won"
    SHARES = "shares"
    PERCENT = "percent"
    INDEX_POINTS = "index_points"
    COUNT = "count"
    UNKNOWN = "unknown"


class DataValueKind(StrEnum):
    ACTUAL = "actual"
    ESTIMATED = "estimated"
    DERIVED_FROM_ACTUAL = "derived_from_actual"
    DERIVED_FROM_ESTIMATE = "derived_from_estimate"
    UNKNOWN = "unknown"


class DataCompleteness(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNCONFIRMED = "unconfirmed"
    MISSING = "missing"


class ObservationOrigin(StrEnum):
    MISSING = "missing"
    REALTIME = "realtime"
    QUERY = "query"
    BACKFILLED = "backfilled"
    DERIVED = "derived"
    UNKNOWN = "unknown"


class CandidateUniverse(StrEnum):
    RANKING_TOP20 = "ranking_top20"
    RANKING_VISIBLE = "ranking_visible"
    MARKET_ALL = "market_all"
    TRADE_ENTRIES = "trade_entries"
    UNKNOWN = "unknown"


class MarketDatasetKind(StrEnum):
    CLOSING_AUCTION_PRICE = 'closing_auction_price'
    MINUTE_BAR = "minute_bar"
    DAILY_BAR = "daily_bar"
    MARKET_STATE = "market_state"
    CANDIDATE_SET = "candidate_set"
    TOP20_INDEX = "top20_index"
    ENTRY_CONTEXT = "entry_context"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class MarketDataMetadata:
    """값과 함께 보존해야 하는 최소 메타데이터.

    ``effective_at``은 값이 설명하는 시장 시각이고 ``available_at``은
    해당 값이 수집기/분석기에 실제로 보이기 시작한 시각이다. 두 시각 중
    하나라도 모르는 과거 행은 시점 재생에서 임의로 당시 이용 가능했다고
    간주하지 않는다.
    """

    effective_at: datetime | None
    available_at: datetime | None
    venue: TradingVenue = TradingVenue.UNKNOWN
    unit: DataUnit = DataUnit.UNKNOWN
    value_kind: DataValueKind = DataValueKind.UNKNOWN
    completeness: DataCompleteness = DataCompleteness.UNCONFIRMED
    origin: ObservationOrigin = ObservationOrigin.UNKNOWN
    source: str = ""
    candidate_universe: CandidateUniverse = CandidateUniverse.UNKNOWN

    @property
    def is_complete(self) -> bool:
        return self.completeness == DataCompleteness.COMPLETE

    def was_available_by(self, as_of: datetime) -> bool:
        """미래 정보 유입 없이 ``as_of`` 당시 사용 가능했는지 판정한다."""
        if self.effective_at is None or self.available_at is None:
            return False
        if self.completeness in {DataCompleteness.MISSING, DataCompleteness.UNCONFIRMED}:
            return False
        try:
            return self.effective_at <= as_of and self.available_at <= as_of
        except TypeError:
            # timezone 유무가 다른 시각을 묵시적으로 섞지 않는다.
            return False


@dataclass(frozen=True)
class CoverageObservation:
    observation_key: str
    metadata: MarketDataMetadata


@dataclass(frozen=True)
class MarketDataObservation(Generic[ValueT]):
    """봉·시장 상태·후보군을 같은 시간 계약으로 전달하는 얇은 봉투."""

    kind: MarketDatasetKind
    subject: str
    value: ValueT
    metadata: MarketDataMetadata


class ListingType(StrEnum):
    IPO = "ipo"
    TRANSFER = "transfer"
    KONEX_TO_KOSDAQ = "konex_to_kosdaq"
    RELISTING = "relisting"
    SPAC = "spac"
    SPAC_MERGER = "spac_merger"
    UNKNOWN = "unknown"


class ListingEvidenceStatus(StrEnum):
    AVAILABLE = "available"
    UNKNOWN = "unknown"
    INCOMPLETE = "incomplete"
    CONFLICT = "conflict"


class ListingPhase(StrEnum):
    """Trading date relative to the reported date, independent of trading status."""

    BEFORE_REPORTED_DATE = "before_reported_date"
    LISTING_DAY = "listing_day"
    AFTER_LISTING_DAY = "after_listing_day"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ListingEvidence:
    symbol: str
    listing_date: date | None
    listing_type: ListingType
    available_at: datetime | None
    source: str
    revision: str
    evidence_id: str
    completeness: DataCompleteness = DataCompleteness.COMPLETE

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.symbol, self.source, self.revision, self.evidence_id)):
            raise ValueError("listing evidence requires symbol, source, revision and evidence_id")
        if self.listing_date is not None and type(self.listing_date) is not date:
            raise ValueError("listing evidence date must be a calendar date")
        if not isinstance(self.listing_type, ListingType) or not isinstance(self.completeness, DataCompleteness):
            raise ValueError("listing evidence requires typed listing type and completeness")
        if self.available_at is not None and self.available_at.utcoffset() is None:
            raise ValueError("listing evidence availability must include its timezone")


@dataclass(frozen=True)
class ListingEvidenceSelection:
    status: ListingEvidenceStatus
    evidence: tuple[ListingEvidence, ...] = ()
    phase: ListingPhase = ListingPhase.UNKNOWN

    @property
    def supports_ipo_strategy(self) -> bool:
        if self.status != ListingEvidenceStatus.AVAILABLE:
            return False
        listing_type = self.evidence[0].listing_type
        return listing_type == ListingType.IPO or (
            listing_type == ListingType.KONEX_TO_KOSDAQ and self.phase == ListingPhase.LISTING_DAY
        )


def select_listing_evidence(
    rows: Iterable[ListingEvidence], *, symbol: str, trading_date: date, as_of: datetime,
) -> ListingEvidenceSelection:
    """Select the latest known listing evidence for a strict replay cut.

    A relevant row with unknown availability leaves the revision order unknown.
    With known availability, determine the latest instant before checking quality.
    This preserves incomplete corrections and conflicts at that instant.
    """
    if as_of.utcoffset() is None:
        raise ValueError("listing evidence cutoff must include its timezone")
    cutoff = as_of.astimezone(KST)
    if cutoff.date() > trading_date:
        raise ValueError("listing evidence cutoff follows the trading date")

    unknown_time = set()
    latest = set()
    latest_at = None
    for row in rows:
        if row.symbol != symbol:
            continue
        if row.available_at is None:
            unknown_time.add(row)
            continue
        available_at = row.available_at.astimezone(KST)
        if available_at > cutoff:
            continue
        if latest_at is None or available_at > latest_at:
            latest_at = available_at
            latest = {row}
        elif available_at == latest_at:
            latest.add(row)

    evidence = tuple(sorted(unknown_time or latest, key=lambda row: (
        row.source, row.revision, row.evidence_id, row.listing_date.isoformat() if row.listing_date else "",
        row.listing_type, row.completeness,
    )))
    if unknown_time or not evidence:
        return ListingEvidenceSelection(ListingEvidenceStatus.UNKNOWN, evidence)

    facts = {(row.listing_date, row.listing_type, row.completeness) for row in evidence}
    if len(facts) != 1:
        return ListingEvidenceSelection(ListingEvidenceStatus.CONFLICT, evidence)
    listing_date, listing_type, completeness = next(iter(facts))
    if listing_date is None or listing_type == ListingType.UNKNOWN or completeness != DataCompleteness.COMPLETE:
        return ListingEvidenceSelection(ListingEvidenceStatus.INCOMPLETE, evidence)
    if trading_date < listing_date:
        phase = ListingPhase.BEFORE_REPORTED_DATE
    elif trading_date == listing_date:
        phase = ListingPhase.LISTING_DAY
    else:
        phase = ListingPhase.AFTER_LISTING_DAY
    return ListingEvidenceSelection(ListingEvidenceStatus.AVAILABLE, evidence, phase)


def metadata_from_legacy_capture_state(
    capture_state: str,
    *,
    effective_at: datetime | None,
    available_at: datetime | None = None,
    venue: TradingVenue = TradingVenue.UNKNOWN,
    unit: DataUnit = DataUnit.UNKNOWN,
    value_kind: DataValueKind = DataValueKind.UNKNOWN,
    source: str = "",
    candidate_universe: CandidateUniverse = CandidateUniverse.UNKNOWN,
) -> MarketDataMetadata:
    """현재 DB의 문자열 상태를 새 계약으로 읽는 비파괴 어댑터."""
    normalized = str(capture_state).strip().casefold()
    origin, completeness = _LEGACY_CAPTURE_STATES.get(
        normalized,
        (ObservationOrigin.UNKNOWN, DataCompleteness.UNCONFIRMED),
    )
    return MarketDataMetadata(
        effective_at=effective_at,
        available_at=available_at,
        venue=venue,
        unit=unit,
        value_kind=value_kind,
        completeness=completeness,
        origin=origin,
        source=source,
        candidate_universe=candidate_universe,
    )


def trading_venue(value: object) -> TradingVenue:
    normalized = str(value or "").strip().upper()
    aliases = {
        "_NX": TradingVenue.NXT,
        "NX": TradingVenue.NXT,
        "_AL": TradingVenue.SOR,
        "AL": TradingVenue.SOR,
        "통합": TradingVenue.COMBINED,
    }
    if normalized in TradingVenue._value2member_map_:
        return TradingVenue(normalized)
    return aliases.get(normalized, TradingVenue.UNKNOWN)


def kiwoom_hhmmss(value: object) -> time | None:
    raw = str(value or "").strip()
    if len(raw) != 6 or not raw.isascii() or not raw.isdigit():
        return None
    try:
        return time.fromisoformat(raw)
    except ValueError:
        return None


@dataclass(frozen=True)
class KiwoomViPhase:
    trigger_time: time
    release_time: time | None
    extended: bool


def kiwoom_vi_phase(values: Mapping[str, object], *, received_clock: time) -> KiwoomViPhase | None:
    """Interpret 1h without changing the stored wire event label."""
    category = str(values.get("9068", "")).strip()
    if category not in {"1", "2", "3", "4"}:
        return None
    trigger = kiwoom_hhmmss(values.get("1223"))
    if trigger is None or not time.min < trigger <= received_clock:
        return None
    release = kiwoom_hhmmss(values.get("1224"))
    extended = category == "4"
    # The same 9068 category can appear at activation and release.
    if extended or release is None or not trigger < release <= received_clock:
        release = None
    return KiwoomViPhase(trigger, release, extended)


def kiwoom_vi_venue(exchange: object, wire_code: object) -> TradingVenue:
    venue = str(exchange or "").strip()
    if venue:
        return TradingVenue._value2member_map_.get(venue, TradingVenue.UNKNOWN)
    code = str(wire_code or "").strip()
    if not code:
        return TradingVenue.UNKNOWN
    if code.endswith("_NX"):
        return TradingVenue.NXT
    return TradingVenue.SOR if code.endswith("_AL") else TradingVenue.KRX


_LEGACY_CAPTURE_STATES: dict[str, tuple[ObservationOrigin, DataCompleteness]] = {
    "realtime_complete": (ObservationOrigin.REALTIME, DataCompleteness.COMPLETE),
    "realtime_partial": (ObservationOrigin.REALTIME, DataCompleteness.PARTIAL),
    "realtime_core": (ObservationOrigin.REALTIME, DataCompleteness.PARTIAL),
    "partial": (ObservationOrigin.REALTIME, DataCompleteness.PARTIAL),
    "in_progress": (ObservationOrigin.REALTIME, DataCompleteness.IN_PROGRESS),
    "query_complete": (ObservationOrigin.QUERY, DataCompleteness.COMPLETE),
    "query_partial": (ObservationOrigin.QUERY, DataCompleteness.PARTIAL),
    "backfilled_complete": (ObservationOrigin.BACKFILLED, DataCompleteness.COMPLETE),
    "backfilled": (ObservationOrigin.BACKFILLED, DataCompleteness.PARTIAL),
    "unconfirmed": (ObservationOrigin.UNKNOWN, DataCompleteness.UNCONFIRMED),
    "missing": (ObservationOrigin.MISSING, DataCompleteness.MISSING),
}
