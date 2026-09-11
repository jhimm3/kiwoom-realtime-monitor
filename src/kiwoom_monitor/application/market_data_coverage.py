"""저장된 관측 메타데이터로 과거 구간의 수집 범위를 보수적으로 진단한다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from kiwoom_monitor.domain.market_data_contract import (
    DataCompleteness,
    MarketDataMetadata,
)


class CoverageState(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    MISSING = "missing"


@dataclass(frozen=True)
class CoverageObservation:
    observation_key: str
    metadata: MarketDataMetadata


@dataclass(frozen=True)
class CoverageReport:
    state: CoverageState
    observation_count: int
    usable_count: int
    complete_count: int
    partial_count: int
    unavailable_by_cutoff_count: int
    explicit_complete: bool
    gap_inference_supported: bool
    missing_intervals: tuple[tuple[datetime, datetime], ...]
    absence_meaning: str

    def as_document(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "observation_count": self.observation_count,
            "usable_count": self.usable_count,
            "complete_count": self.complete_count,
            "partial_count": self.partial_count,
            "unavailable_by_cutoff_count": self.unavailable_by_cutoff_count,
            "explicit_complete": self.explicit_complete,
            "gap_inference_supported": self.gap_inference_supported,
            "missing_intervals": [
                {"start": start.isoformat(), "end": end.isoformat()}
                for start, end in self.missing_intervals
            ],
            "absence_meaning": self.absence_meaning,
        }


def evaluate_coverage(
    observations: tuple[CoverageObservation, ...],
    *,
    start: datetime,
    end: datetime,
    available_by: datetime,
    explicit_complete: bool = False,
    expected_seconds: int | None = None,
) -> CoverageReport:
    """관측 부재와 실제 0/무체결을 섞지 않고 수집 범위를 판정한다."""
    if end <= start:
        raise ValueError("end must be after start")
    if expected_seconds is not None and expected_seconds <= 0:
        raise ValueError("expected_seconds must be positive")

    eligible = tuple(
        item for item in observations
        if item.metadata.effective_at is not None
        and _between(item.metadata.effective_at, start, end)
    )
    usable = tuple(item for item in eligible if item.metadata.was_available_by(available_by))
    complete_count = sum(
        item.metadata.completeness == DataCompleteness.COMPLETE for item in usable
    )
    partial_count = len(usable) - complete_count
    missing_intervals: tuple[tuple[datetime, datetime], ...] = ()
    gap_supported = expected_seconds is not None
    if expected_seconds is not None:
        missing_intervals = _missing_intervals(
            usable, start=start, end=end, seconds=expected_seconds
        )

    if explicit_complete and not partial_count:
        state = CoverageState.COMPLETE
    elif expected_seconds is not None and not missing_intervals and not partial_count:
        state = CoverageState.COMPLETE
    elif usable:
        state = CoverageState.PARTIAL
    else:
        state = CoverageState.MISSING
    if explicit_complete:
        absence_meaning = "no_trade_with_complete_coverage"
    elif expected_seconds is not None:
        absence_meaning = "no_observation"
    else:
        absence_meaning = "no_trade_or_no_observation"
    return CoverageReport(
        state=state,
        observation_count=len(eligible),
        usable_count=len(usable),
        complete_count=complete_count,
        partial_count=partial_count,
        unavailable_by_cutoff_count=len(eligible) - len(usable),
        explicit_complete=explicit_complete,
        gap_inference_supported=gap_supported,
        missing_intervals=missing_intervals,
        absence_meaning=absence_meaning,
    )


def _missing_intervals(
    observations: tuple[CoverageObservation, ...],
    *,
    start: datetime,
    end: datetime,
    seconds: int,
) -> tuple[tuple[datetime, datetime], ...]:
    step = timedelta(seconds=seconds)
    observed = {
        _floor(item.metadata.effective_at, seconds)
        for item in observations
        if item.metadata.effective_at is not None
    }
    slots: list[datetime] = []
    cursor = _floor(start, seconds)
    while cursor < end:
        if cursor not in observed:
            slots.append(cursor)
        cursor += step
    if not slots:
        return ()
    intervals: list[tuple[datetime, datetime]] = []
    interval_start = previous = slots[0]
    for current in slots[1:]:
        if current != previous + step:
            intervals.append((interval_start, previous + step))
            interval_start = current
        previous = current
    intervals.append((interval_start, previous + step))
    return tuple(intervals)


def _floor(value: datetime, seconds: int) -> datetime:
    midnight = value.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed = int((value - midnight).total_seconds())
    return midnight + timedelta(seconds=elapsed - elapsed % seconds)


def _between(value: datetime, start: datetime, end: datetime) -> bool:
    try:
        return start <= value < end
    except TypeError:
        return False
