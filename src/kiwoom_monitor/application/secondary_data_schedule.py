"""실시간 순위 뒤 저우선순위 보완 조회의 대상 선택 정책."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from enum import Enum


class FollowupPhase(str, Enum):
    NEW_HIGH = "new_high"
    NXT = "nxt"


class SecondaryStartPhase(str, Enum):
    FINALIZATION = "finalization"
    WEEKEND_DAILY_HIGH = "weekend_daily_high"
    AFTER_HOURS = "after_hours"
    MINUTE_HISTORY = "minute_history"


class SecondaryDataFollowupCoordinator:
    """저우선순위 worker의 실행 순서만 조정하고 실제 조회는 호출자에게 맡긴다."""

    def __init__(
        self,
        *,
        start_minute_history: Callable[[tuple[str, ...], bool], bool],
        start_daily_high: Callable[[tuple[str, ...], bool], bool],
        start_daily_high_phase: Callable[[tuple[str, ...]], None],
        start_fundamentals: Callable[[tuple[str, ...]], bool],
        start_fundamentals_phase: Callable[[tuple[str, ...]], None],
        start_nxt_phase: Callable[[tuple[str, ...]], None],
        start_new_high_phase: Callable[[tuple[str, ...]], None],
    ) -> None:
        self._start_minute_history = start_minute_history
        self._start_daily_high = start_daily_high
        self._start_daily_high_phase = start_daily_high_phase
        self._start_fundamentals = start_fundamentals
        self._start_fundamentals_phase = start_fundamentals_phase
        self._start_nxt_phase = start_nxt_phase
        self._start_new_high_phase = start_new_high_phase

    def start(
        self,
        codes: tuple[str, ...],
        *,
        finalization_codes: tuple[str, ...] = (),
        after_hours_pause: bool = False,
        weekend_daily_high_codes: tuple[str, ...] = (),
    ) -> SecondaryStartPhase:
        if finalization_codes and self._start_minute_history(finalization_codes, True):
            return SecondaryStartPhase.FINALIZATION
        if after_hours_pause:
            if weekend_daily_high_codes and self._start_daily_high(weekend_daily_high_codes, False):
                return SecondaryStartPhase.WEEKEND_DAILY_HIGH
            if not self._start_fundamentals(codes):
                self._start_nxt_phase(codes)
            return SecondaryStartPhase.AFTER_HOURS
        if not self._start_minute_history(codes, False):
            self._start_daily_high_phase(codes)
        return SecondaryStartPhase.MINUTE_HISTORY

    def minute_history_finished(self, codes: tuple[str, ...], *, forced: bool) -> None:
        if forced:
            self._start_daily_high(codes, True)
        else:
            self._start_daily_high_phase(codes)

    def daily_high_finished(self, codes: tuple[str, ...]) -> None:
        self._start_fundamentals_phase(codes)

    def fundamentals_finished(self, codes: tuple[str, ...], *, after_hours_pause: bool) -> None:
        if phase_after_fundamentals(after_hours_pause) is FollowupPhase.NXT:
            self._start_nxt_phase(codes)
        else:
            self._start_new_high_phase(codes)


def minute_history_candidates(
    codes: tuple[str, ...], loaded_codes: Collection[str], *, force: bool = False,
) -> tuple[str, ...]:
    return codes if force else tuple(code for code in codes if code not in loaded_codes)


def daily_high_candidates(
    codes: tuple[str, ...],
    loaded_codes: Collection[str],
    refreshed_codes: Collection[str],
    *,
    force: bool = False,
    adjusted_basis_refresh: bool = False,
) -> tuple[str, ...]:
    if force or adjusted_basis_refresh:
        return codes
    return tuple(code for code in codes if code not in loaded_codes or code not in refreshed_codes)


def fundamentals_candidates(
    codes: tuple[str, ...],
    loaded_codes: Collection[str],
    refresh_codes: Collection[str] | None,
    *,
    force: bool = False,
) -> tuple[str, ...]:
    if force:
        return codes
    if refresh_codes is not None:
        refresh_set = set(refresh_codes)
        return tuple(code for code in codes if code in refresh_set)
    return tuple(code for code in codes if code not in loaded_codes)


def nxt_eligibility_candidates(
    codes: tuple[str, ...], cached: Mapping[str, bool] | None, checked_codes: Collection[str],
) -> tuple[str, ...]:
    if cached is not None:
        return tuple(code for code in codes if code not in cached)
    return tuple(code for code in codes if code not in checked_codes)


def phase_after_fundamentals(after_hours_pause: bool) -> FollowupPhase:
    return FollowupPhase.NXT if after_hours_pause else FollowupPhase.NEW_HIGH


def daily_catalog_sync_due(last_success: str, today: str) -> bool:
    """KRX 전체 목록은 잔여 미분류 종목과 무관하게 하루 한 번만 갱신한다."""
    return not str(last_success).startswith(today)
