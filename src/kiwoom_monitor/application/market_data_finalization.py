"""장 마감 뒤 분봉·일봉 확정 보완 정책.

DB 조회와 worker 실행은 호출자가 담당하고, 이 모듈은 대상 날짜·종목,
완료 봉 시각, 제한 재시도와 최종 미확정 여부만 결정한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from collections.abc import Iterable, Mapping


MAX_FINALIZATION_ATTEMPTS = 2
FINALIZATION_RETRY_DELAY = timedelta(minutes=5)


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
) -> tuple[str, ...]:
    """NXT 종목 20:05, 그 외 종목 15:35 이후의 미확정 종목을 고른다."""
    if target is None:
        return ()
    premarket_repair = target < now.date()
    current_minutes = now.hour * 60 + now.minute
    return tuple(
        code for code in codes
        if code not in finalized_codes
        and (
            premarket_repair
            or current_minutes >= (20 * 60 + 5 if nxt_enabled.get(code, True) else 15 * 60 + 35)
        )
        and finalization_retry_allowed(target, code, now, attempts, retry_after)
    )


def minute_bars_complete(minutes: Iterable[datetime], target: date, nxt_enabled: bool) -> bool:
    """대상일의 마지막 완료 분봉(KRX 15:29, NXT 19:59)이 들어왔는지 확인한다."""
    required = time(19, 59) if nxt_enabled else time(15, 29)
    received = [minute.time() for minute in minutes if minute.date() == target]
    return bool(received) and max(received) >= required


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
