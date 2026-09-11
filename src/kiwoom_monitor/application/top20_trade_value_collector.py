"""TOP20 거래대금 코호트와 분 마감 상태를 UI와 독립적으로 관리한다."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from kiwoom_monitor.domain.market_data_contract import (
    CandidateUniverse,
    DataUnit,
    DataValueKind,
    MarketDataObservation,
    MarketDatasetKind,
    MarketDataMetadata,
    TradingVenue,
    metadata_from_legacy_capture_state,
)


MarketValues = tuple[float, float, float]
MarketCounts = tuple[int, int, int]
ValueProvider = Callable[[str, datetime], float]
MarketProvider = Callable[[str], str]


@dataclass(frozen=True)
class Top20MinuteRecord:
    minute: datetime
    market_values: MarketValues
    codes: tuple[str, ...]
    market_counts: MarketCounts
    cohort_segments: tuple[tuple[str, tuple[str, ...]], ...]
    capture_state: str

    @property
    def total(self) -> float:
        return sum(self.market_values)

    def metadata(self, *, available_at: datetime | None = None) -> MarketDataMetadata:
        """기존 capture_state를 시뮬레이션용 공통 의미로 노출한다."""
        return metadata_from_legacy_capture_state(
            self.capture_state,
            effective_at=self.minute,
            available_at=available_at,
            venue=TradingVenue.COMBINED,
            unit=DataUnit.EOK_WON,
            value_kind=DataValueKind.DERIVED_FROM_ACTUAL,
            source="top20_trade_value_index",
            candidate_universe=CandidateUniverse.RANKING_TOP20,
        )

    def observation(
        self, *, available_at: datetime | None = None,
    ) -> MarketDataObservation["Top20MinuteRecord"]:
        return MarketDataObservation(
            kind=MarketDatasetKind.TOP20_INDEX,
            subject="KOSPI+KOSDAQ",
            value=self,
            metadata=self.metadata(available_at=available_at),
        )


@dataclass(frozen=True)
class Top20CollectionUpdate:
    completed: Top20MinuteRecord | None
    live_minute: datetime
    live_values: MarketValues | None
    collection_open: bool
    cohort_changed: bool = False


class Top20TradeValueCollector:
    """30초 순위 코호트를 적용하고 두 구간을 한 개의 1분 값으로 마감한다."""

    def __init__(self, max_completed: int = 1_440) -> None:
        self.active_codes: tuple[str, ...] = ()
        self.next_codes: tuple[str, ...] = ()
        self.next_activation: datetime | None = None
        self.minute: datetime | None = None
        self.samples: list[tuple[int, float]] = []
        self.segment_baselines: dict[str, float] = {}
        self.segment_accumulated: MarketValues = (0.0, 0.0, 0.0)
        self.minute_codes: set[str] = set()
        self.cohort_segments: list[tuple[str, tuple[str, ...]]] = []
        self.completed: deque[tuple[datetime, float, float, float]] = deque(maxlen=max_completed)

    def prepare(self, codes: tuple[str, ...], observed_at: datetime, *, enabled: bool, collection_open: bool) -> None:
        if not enabled:
            self._clear_collection()
            return
        if not collection_open:
            self.next_codes = ()
            self.next_activation = None
            return
        self.next_codes = tuple(dict.fromkeys(codes[:20]))
        minute = observed_at.replace(second=0, microsecond=0)
        self.next_activation = (
            minute.replace(second=30) if observed_at.second < 30
            else minute + timedelta(minutes=1)
        )

    def realtime_codes(self, visible_codes: tuple[str, ...], *, enabled: bool) -> tuple[str, ...]:
        if not enabled:
            return tuple(dict.fromkeys(visible_codes))
        return tuple(dict.fromkeys((*visible_codes, *self.active_codes, *self.next_codes)))

    def advance(
        self, now: datetime, *, enabled: bool, collection_open: bool,
        value_provider: ValueProvider, market_provider: MarketProvider,
    ) -> Top20CollectionUpdate:
        minute = now.replace(second=0, microsecond=0)
        if not enabled:
            self._clear_collection()
            return Top20CollectionUpdate(None, minute, None, collection_open)
        completed = self._roll_minute(minute, value_provider, market_provider)
        if not collection_open:
            self._clear_collection()
            return Top20CollectionUpdate(completed, minute, None, False)

        cohort_changed = self._activate_with_market(now, minute, value_provider, market_provider)
        live_values: MarketValues | None = None
        if self.active_codes:
            active_values = self.active_segment_values(minute, value_provider, market_provider)
            live_values = tuple(
                self.segment_accumulated[index] + active_values[index]
                for index in range(3)
            )
            sample = (min(59, max(0, now.second)), sum(live_values))
            if self.samples and self.samples[-1][0] == sample[0]:
                self.samples[-1] = sample
            else:
                self.samples.append(sample)
        return Top20CollectionUpdate(completed, minute, live_values, True, cohort_changed)

    def partial_record(self, value_provider: ValueProvider, market_provider: MarketProvider) -> Top20MinuteRecord | None:
        if not self.samples or self.minute is None:
            return None
        active = self.active_segment_values(self.minute, value_provider, market_provider)
        market_values = tuple(self.segment_accumulated[index] + active[index] for index in range(3))
        return self._record(self.minute, market_values, market_provider, "partial")

    def active_segment_values(
        self, minute: datetime, value_provider: ValueProvider, market_provider: MarketProvider,
    ) -> MarketValues:
        values = [0.0, 0.0, 0.0]
        for code in self.active_codes:
            contribution = max(0.0, value_provider(code, minute) - self.segment_baselines.get(code, 0.0))
            values[_market_index(market_provider(code))] += contribution
        return values[0], values[1], values[2]

    def market_counts(self, codes: tuple[str, ...], market_provider: MarketProvider) -> MarketCounts:
        counts = [0, 0, 0]
        for code in codes:
            counts[_market_index(market_provider(code))] += 1
        return counts[0], counts[1], counts[2]

    def _roll_minute(
        self, minute: datetime, value_provider: ValueProvider, market_provider: MarketProvider,
    ) -> Top20MinuteRecord | None:
        if self.minute == minute:
            return None
        completed = None
        if self.samples and self.minute is not None:
            active = self.active_segment_values(self.minute, value_provider, market_provider)
            market_values = tuple(self.segment_accumulated[index] + active[index] for index in range(3))
            completed = self._record(self.minute, market_values, market_provider, "realtime_complete")
            self.completed.append((self.minute, *market_values))
        self.samples = []
        self.minute = minute
        self.segment_accumulated = (0.0, 0.0, 0.0)
        self.segment_baselines = {code: value_provider(code, minute) for code in self.active_codes}
        self.minute_codes = set(self.active_codes)
        self.cohort_segments = []
        if self.active_codes:
            self.cohort_segments.append((minute.isoformat(timespec="seconds"), self.active_codes))
        return completed

    def _activate_with_market(
        self, now: datetime, minute: datetime, value_provider: ValueProvider, market_provider: MarketProvider,
    ) -> bool:
        activation = self.next_activation
        if activation is None or now < activation or not self.next_codes:
            return False
        active = self.active_segment_values(minute, value_provider, market_provider)
        self.segment_accumulated = tuple(self.segment_accumulated[index] + active[index] for index in range(3))
        self.active_codes = self.next_codes
        self.next_codes = ()
        self.next_activation = None
        self.segment_baselines = {code: value_provider(code, minute) for code in self.active_codes}
        self.minute_codes.update(self.active_codes)
        self.cohort_segments.append((activation.isoformat(timespec="seconds"), self.active_codes))
        return True

    def _record(
        self, minute: datetime, market_values: MarketValues,
        market_provider: MarketProvider, capture_state: str,
    ) -> Top20MinuteRecord:
        codes = tuple(self.minute_codes)
        return Top20MinuteRecord(
            minute, market_values, codes, self.market_counts(codes, market_provider),
            tuple(self.cohort_segments), capture_state,
        )

    def _clear_collection(self) -> None:
        self.active_codes = ()
        self.next_codes = ()
        self.next_activation = None
        self.samples = []
        self.segment_baselines = {}
        self.segment_accumulated = (0.0, 0.0, 0.0)
        self.minute_codes.clear()
        self.cohort_segments = []


def _market_index(market: str) -> int:
    if "KOSPI" in market or "코스피" in market or market in {"유가", "STK", "1"}:
        return 0
    if "KOSDAQ" in market or "코스닥" in market or market in {"KSQ", "2"}:
        return 1
    return 2
