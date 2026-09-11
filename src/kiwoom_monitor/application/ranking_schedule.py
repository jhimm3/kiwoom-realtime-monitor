"""실시간 순위 조회 기준 시각과 준비 시각 계산."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum


@dataclass(frozen=True)
class RankingSchedule:
    next_time: datetime
    request_time: datetime
    delay_ms: int
    preparation_delay_ms: int


class RankingResponseAction(str, Enum):
    RETRY_SOON = "retry_soon"
    WAIT_NEXT = "wait_next"
    DEFER_WHILE_MODAL = "defer_while_modal"
    APPLY = "apply"


@dataclass(frozen=True)
class RankingResponseDecision:
    action: RankingResponseAction
    retry_count: int


@dataclass(frozen=True)
class RankingChangeSummary:
    rank_by_code: dict[str, int]
    changed_codes: frozenset[str]
    signature: tuple[tuple[object, object, object], ...]
    unchanged: bool


def summarize_ranking_changes(
    stocks: tuple[object, ...],
    previous_rank_by_code: dict[str, int],
    previous_signature: tuple[tuple[object, object, object], ...],
) -> RankingChangeSummary:
    """순위 응답의 변경 종목과 동일 응답 여부를 UI와 무관하게 계산한다."""
    rank_by_code = {
        str(getattr(stock, "code", "")): int(getattr(stock, "rank", 0) or 0)
        for stock in stocks
    }
    changed_codes = (
        frozenset(
            code
            for code, rank in rank_by_code.items()
            if code and previous_rank_by_code.get(code) != rank
        )
        if previous_rank_by_code
        else frozenset()
    )
    signature = tuple(
        (
            getattr(stock, "rank", None),
            getattr(stock, "code", None),
            getattr(stock, "change_rate", None),
        )
        for stock in stocks
    )
    return RankingChangeSummary(
        rank_by_code=rank_by_code,
        changed_codes=changed_codes,
        signature=signature,
        unchanged=bool(previous_signature) and signature == previous_signature,
    )


def decide_ranking_response(
    actual_count: int,
    expected_count: int,
    retry_count: int,
    has_blocking_modal: bool,
) -> RankingResponseDecision:
    """부분 응답 재시도와 설정창 사용 중 반영 보류 정책을 결정한다."""
    if expected_count > 0 and actual_count < expected_count:
        next_retry_count = retry_count + 1
        action = (
            RankingResponseAction.RETRY_SOON
            if next_retry_count <= 2
            else RankingResponseAction.WAIT_NEXT
        )
        return RankingResponseDecision(action, next_retry_count)
    if has_blocking_modal:
        return RankingResponseDecision(RankingResponseAction.DEFER_WHILE_MODAL, retry_count)
    return RankingResponseDecision(RankingResponseAction.APPLY, 0)


def next_ranking_schedule(now: datetime, query_type: str) -> RankingSchedule:
    """현재 서버 시각에서 다음 순위 조회와 저우선순위 양보 시각을 계산한다."""
    if query_type == "5":
        base = now.replace(microsecond=0)
        next_time = base.replace(second=30) if base.second < 30 else (base + timedelta(minutes=1)).replace(second=0)
    elif query_type == "1":
        next_time = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
    elif query_type == "2":
        base = now.replace(second=0, microsecond=0)
        next_time = base + timedelta(minutes=10 - (base.minute % 10))
    elif query_type == "3":
        next_time = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    else:
        base = now.replace(microsecond=0)
        next_time = base.replace(second=30) if base.second < 30 else (base + timedelta(minutes=1)).replace(second=0)

    request_time = next_time + timedelta(milliseconds=250)
    delay_ms = max(100, round((request_time - now).total_seconds() * 1000))
    return RankingSchedule(
        next_time=next_time,
        request_time=request_time,
        delay_ms=delay_ms,
        preparation_delay_ms=max(100, delay_ms - 2_500),
    )
