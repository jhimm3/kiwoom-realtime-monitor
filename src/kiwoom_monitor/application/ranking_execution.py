"""실시간 순위 조회 회차의 상태와 응답 적용 결정을 조정한다.

Qt 타이머와 worker 수명은 presentation 계층에 남기되, 조회 회차 사이에
유지되는 상태는 이 객체가 단독으로 소유한다.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime

from kiwoom_monitor.application.ranking_schedule import (
    RankingChangeSummary,
    RankingResponseAction,
    RankingResponseDecision,
    RankingSchedule,
    decide_ranking_response,
    next_ranking_schedule,
    summarize_ranking_changes,
)


@dataclass(frozen=True)
class RankingResponseOutcome:
    decision: RankingResponseDecision
    change_summary: RankingChangeSummary | None = None


class RankingExecutionCoordinator:
    """중복 조회·부분 응답·지연 반영 상태를 UI 밖에서 관리한다."""

    def __init__(self, now: Callable[[], datetime]) -> None:
        self._now = now
        self._priority_preparing = False
        self._request_due = False
        self._partial_retry_count = 0
        self._deferred_stocks: tuple[object, ...] | None = None
        self._last_rank_by_code: dict[str, int] = {}
        self._last_signature: tuple[tuple[object, object, object], ...] = ()

    @property
    def priority_preparing(self) -> bool:
        return self._priority_preparing

    @property
    def rank_by_code(self) -> Mapping[str, int]:
        return self._last_rank_by_code

    @property
    def has_deferred_response(self) -> bool:
        return self._deferred_stocks is not None

    def next_schedule(self, query_type: str) -> RankingSchedule:
        return next_ranking_schedule(self._now(), query_type)

    def begin_priority_preparation(self) -> None:
        self._priority_preparing = True

    def end_priority_preparation(self) -> None:
        self._priority_preparing = False

    def ranking_timer_fired(self, *, worker_running: bool) -> bool:
        """기준 시각 요청을 지금 시작할 수 있는지 반환한다."""
        self._priority_preparing = False
        if worker_running:
            self._request_due = True
            return False
        return True

    def worker_finished(self) -> bool:
        """진행 중 쌓인 요청 하나를 즉시 소비할지 반환한다."""
        if not self._request_due:
            return False
        self._request_due = False
        return True

    def cancel_pending_request(self) -> None:
        self._request_due = False

    def handle_response(
        self,
        stocks: tuple[object, ...],
        *,
        expected_count: int,
        has_blocking_modal: bool,
    ) -> RankingResponseOutcome:
        partial = expected_count > 0 and len(stocks) < expected_count
        decision = decide_ranking_response(
            len(stocks),
            expected_count,
            self._partial_retry_count,
            has_blocking_modal if not partial else False,
        )
        self._partial_retry_count = decision.retry_count
        if decision.action == RankingResponseAction.DEFER_WHILE_MODAL:
            self._deferred_stocks = stocks
            return RankingResponseOutcome(decision)
        if decision.action != RankingResponseAction.APPLY:
            return RankingResponseOutcome(decision)

        change_summary = summarize_ranking_changes(
            stocks,
            self._last_rank_by_code,
            self._last_signature,
        )
        self._last_rank_by_code = change_summary.rank_by_code
        self._last_signature = change_summary.signature
        return RankingResponseOutcome(decision, change_summary)

    def take_deferred_response(self) -> tuple[object, ...] | None:
        stocks = self._deferred_stocks
        self._deferred_stocks = None
        return stocks
