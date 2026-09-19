"""장 마감 뒤 분봉·일봉 확정 보완 정책.

DB 조회와 worker 실행은 호출자가 담당하고, 이 모듈은 대상 날짜·종목,
완료 봉 시각, 제한 재시도와 최종 미확정 여부만 결정한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from collections.abc import Iterable, Mapping
from enum import StrEnum

from .market_session_schedule import KRX_AFTER_MARKET_EFFECTIVE_DATE


MAX_FINALIZATION_ATTEMPTS = 2
FINALIZATION_RETRY_DELAY = timedelta(minutes=5)


class FinalizationScope(StrEnum):
    REGULAR = "regular"
    FULL_DAY = "full_day"


def previous_business_day(day: date) -> date:
    candidate = day - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def finalization_target_date(now: datetime, latest_trade_date: date | None) -> date | None:
    """현재 시각에 확인할 거래일을 반환한다. 주말에는 별도 일봉 보완만 수행한다."""
    if now.weekday() >= 5:
        return None
    if now.hour * 60 + now.minute < 7 * 60 + 55:
        return latest_trade_date or previous_business_day(now.date())
    return now.date()


def finalization_retry_allowed(
    target: date,
    code: str,
    now: datetime,
    attempts: Mapping[tuple[date, str], int],
    retry_after: Mapping[tuple[date, str], datetime],
) -> bool:
    key = (target, code)
    return attempts.get(key, 0) < MAX_FINALIZATION_ATTEMPTS and now >= retry_after.get(key, datetime.min)


def finalization_candidates(
    codes: tuple[str, ...],
    now: datetime,
    target: date | None,
    finalized_codes: set[str],
    nxt_enabled: Mapping[str, bool],
    attempts: Mapping[tuple[date, str], int],
    retry_after: Mapping[tuple[date, str], datetime],
    *,
    session_scope: FinalizationScope = FinalizationScope.FULL_DAY,
) -> tuple[str, ...]:
    """요청 범위의 종료 뒤 미확정 종목을 고른다."""
    if target is None:
        return ()
    premarket_repair = target < now.date()
    current_minutes = now.hour * 60 + now.minute
    return tuple(
        code for code in codes
        if code not in finalized_codes
        and (
            premarket_repair
            or current_minutes >= _finalization_ready_minute(
                target, nxt_enabled.get(code, True), session_scope
            )
        )
        and finalization_retry_allowed(target, code, now, attempts, retry_after)
    )


def minute_bars_complete(
    minutes: Iterable[datetime],
    target: date,
    nxt_enabled: bool,
    *,
    session_scope: FinalizationScope = FinalizationScope.FULL_DAY,
    query_completed: bool = True,
) -> bool:
    """성공한 조회가 대상일 자료를 반환했는지 확인한다.

    시행일부터는 마지막 분 체결 유무가 전체일 조회 완료 근거가 아니다.
    호출자의 조회 완료와 대상일 실제 봉 존재를 함께 사용한다. 시행 전
    자료에는 기존 마지막 봉 기준을 그대로 적용한다.
    """
    if not query_completed:
        return False
    received = [minute.time() for minute in minutes if minute.date() == target]
    if not received:
        return False
    if target >= KRX_AFTER_MARKET_EFFECTIVE_DATE:
        return True
    required = (
        time(15, 29)
        if session_scope is FinalizationScope.REGULAR
        else time(19, 59) if nxt_enabled else time(15, 29)
    )
    return max(received) >= required


def _finalization_ready_minute(
    target: date, nxt_enabled: bool, session_scope: FinalizationScope
) -> int:
    if session_scope is FinalizationScope.REGULAR:
        return 15 * 60 + 35
    if target >= KRX_AFTER_MARKET_EFFECTIVE_DATE:
        return 20 * 60 + 5
    # 시행 전 전체일은 기존 NXT 여부별 확정 시각을 보존한다.
    return 20 * 60 + 5 if nxt_enabled else 15 * 60 + 35


@dataclass(frozen=True)
class FinalizationOutcome:
    completed: tuple[str, ...]
    retry_codes: tuple[str, ...]
    unconfirmed: tuple[tuple[str, tuple[str, ...]], ...]
    retry_at: datetime


def evaluate_finalization_outcome(
    attempted: set[str],
    minute_received: set[str],
    daily_received: set[str],
    target: date,
    attempts: Mapping[tuple[date, str], int],
    now: datetime,
) -> FinalizationOutcome:
    """완료, 5분 뒤 재시도, 최대 시도 후 미확정 기록 대상을 나눈다."""
    completed_set = attempted & minute_received & daily_received
    retry_codes: list[str] = []
    unconfirmed: list[tuple[str, tuple[str, ...]]] = []
    for code in sorted(attempted - completed_set):
        if attempts.get((target, code), 0) < MAX_FINALIZATION_ATTEMPTS:
            retry_codes.append(code)
            continue
        missing = tuple(
            part for part, received in (
                ("분봉", code in minute_received),
                ("일봉", code in daily_received),
            )
            if not received
        )
        unconfirmed.append((code, missing))
    return FinalizationOutcome(
        tuple(sorted(completed_set)),
        tuple(retry_codes),
        tuple(unconfirmed),
        now + FINALIZATION_RETRY_DELAY,
    )
