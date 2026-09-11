"""실시간 0B/0w 구독의 유지·교체·종료 결정을 관리한다."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from kiwoom_monitor.application.market_session_schedule import active_realtime_codes


class RealtimeSubscriptionAction(str, Enum):
    NONE = "none"
    STOP = "stop"
    UPDATE = "update"
    START = "start"


@dataclass(frozen=True)
class RealtimeSubscriptionDecision:
    action: RealtimeSubscriptionAction
    active_codes: tuple[str, ...] = ()
    nxt_codes: tuple[str, ...] = ()
    stop_existing_first: bool = False


class RealtimeSubscriptionCoordinator:
    """현재 구독 종목을 소유하고 다음 worker 동작만 결정한다.

    QThread 생성·신호 연결·종료 대기는 presentation adapter가 수행한다.
    """

    def __init__(self, now: Callable[[], datetime]) -> None:
        self._now = now
        self._current_codes: tuple[str, ...] = ()

    @property
    def current_codes(self) -> tuple[str, ...]:
        return self._current_codes

    def reset(self) -> None:
        self._current_codes = ()

    def plan(
        self,
        requested_codes: tuple[str, ...],
        nxt_enabled_codes: set[str],
        *,
        closing: bool,
        worker_available: bool,
        worker_exists: bool,
        worker_running: bool,
    ) -> RealtimeSubscriptionDecision:
        if closing or not worker_available:
            return RealtimeSubscriptionDecision(RealtimeSubscriptionAction.NONE)

        active_codes = active_realtime_codes(
            requested_codes, nxt_enabled_codes, self._now(),
        )
        nxt_codes = tuple(code for code in active_codes if code in nxt_enabled_codes)
        if not active_codes:
            action = RealtimeSubscriptionAction.STOP if worker_running else RealtimeSubscriptionAction.NONE
            return RealtimeSubscriptionDecision(action)
        if worker_running:
            action = (
                RealtimeSubscriptionAction.NONE
                if active_codes == self._current_codes
                else RealtimeSubscriptionAction.UPDATE
            )
            return RealtimeSubscriptionDecision(action, active_codes, nxt_codes)
        return RealtimeSubscriptionDecision(
            RealtimeSubscriptionAction.START,
            active_codes,
            nxt_codes,
            stop_existing_first=worker_exists,
        )

    def commit(self, decision: RealtimeSubscriptionDecision) -> None:
        if decision.action == RealtimeSubscriptionAction.STOP:
            self._current_codes = ()
        elif decision.action in {
            RealtimeSubscriptionAction.UPDATE,
            RealtimeSubscriptionAction.START,
        }:
            self._current_codes = decision.active_codes
