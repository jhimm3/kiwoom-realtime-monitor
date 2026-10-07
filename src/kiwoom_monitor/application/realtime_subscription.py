"""실시간 0B/0w 구독의 유지·교체·종료 결정을 관리한다."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from kiwoom_monitor.application.market_session_schedule import realtime_subscription_target


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
    signature: tuple[str, ...] = ()
    stop_existing_first: bool = False


class RealtimeSubscriptionCoordinator:
    """현재 구독 종목을 소유하고 다음 worker 동작만 결정한다.

    QThread 생성·신호 연결·종료 대기는 presentation adapter가 수행한다.
    """

    def __init__(self, now: Callable[[], datetime], environment: Callable[[], str] | None = None) -> None:
        self._now = now
        self._environment = environment or (lambda: "real")
        self._current_codes: tuple[str, ...] = ()
        self._current_nxt_codes: tuple[str, ...] = ()
        self._current_signature: tuple[str, ...] = ()

    @property
    def current_codes(self) -> tuple[str, ...]:
        return self._current_codes

    def reset(self) -> None:
        self._current_codes = ()
        self._current_nxt_codes = ()
        self._current_signature = ()

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

        target = realtime_subscription_target(
            requested_codes, nxt_enabled_codes, self._now(),
            environment=self._environment(),
        )
        active_codes = target.active_codes
        nxt_codes = target.nxt_codes
        if not active_codes:
            action = RealtimeSubscriptionAction.STOP if worker_running else RealtimeSubscriptionAction.NONE
            return RealtimeSubscriptionDecision(action)
        if worker_running:
            action = (
                RealtimeSubscriptionAction.NONE
                if (
                    active_codes == self._current_codes
                    and nxt_codes == self._current_nxt_codes
                    and target.signature == self._current_signature
                )
                else RealtimeSubscriptionAction.UPDATE
            )
            return RealtimeSubscriptionDecision(action, active_codes, nxt_codes, target.signature)
        return RealtimeSubscriptionDecision(
            RealtimeSubscriptionAction.START,
            active_codes,
            nxt_codes,
            target.signature,
            stop_existing_first=worker_exists,
        )

    def commit(self, decision: RealtimeSubscriptionDecision) -> None:
        if decision.action == RealtimeSubscriptionAction.STOP:
            self._current_codes = ()
            self._current_nxt_codes = ()
            self._current_signature = ()
        elif decision.action in {
            RealtimeSubscriptionAction.UPDATE,
            RealtimeSubscriptionAction.START,
        }:
            self._current_codes = decision.active_codes
            self._current_nxt_codes = decision.nxt_codes
            self._current_signature = decision.signature
