"""KRX·NXT 시간대별 실시간 구독과 후속 작업 정책."""

from __future__ import annotations

from datetime import datetime, timedelta


def active_realtime_codes(
    codes: tuple[str, ...],
    nxt_enabled_codes: set[str],
    now: datetime,
) -> tuple[str, ...]:
    """KRX 정규장에는 전체, NXT 전용 시간에는 NXT 가능 종목만 반환한다."""
    minutes = now.hour * 60 + now.minute
    nxt_open = 8 * 60 <= minutes < 20 * 60
    krx_open = 9 * 60 <= minutes < 15 * 60 + 30
    if not nxt_open:
        return ()
    if krx_open:
        return codes
    return tuple(code for code in codes if code in nxt_enabled_codes)


def is_nxt_only_session(environment: str, now: datetime) -> bool:
    """실전 환경의 NXT 단독 거래 시간인지 판단한다."""
    if environment != "real" or now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return 8 * 60 <= minutes < 9 * 60 or 15 * 60 + 30 <= minutes < 20 * 60


def next_realtime_session_boundary(now: datetime) -> datetime:
    """기존 실시간 세션 재구독 경계 중 다음 시각을 반환한다."""
    boundaries = ((7, 55), (8, 55), (9, 0), (15, 30), (15, 35), (20, 5))
    candidates = [
        now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        for hour, minute in boundaries
    ]
    return next((value for value in candidates if value > now), candidates[0] + timedelta(days=1))


def after_hours_data_pause(now: datetime) -> bool:
    """저우선순위 체결·보완 조회를 쉬는 시간인지 판단한다."""
    if now.weekday() >= 5:
        return True
    minutes = now.hour * 60 + now.minute
    return 20 * 60 + 5 <= minutes or minutes < 7 * 60 + 55


def top20_collection_open(now: datetime) -> bool:
    """TOP20 거래대금 지수를 수집하는 평일 08:00~20:00 구간인지 판단한다."""
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return 8 * 60 <= minutes < 20 * 60


def top20_collection_available(now: datetime, active_api_route: str) -> bool:
    """수집시간이어도 NAS 원본 장애 구간은 TOP20 지수에서 제외한다."""
    return top20_collection_open(now) and active_api_route not in {
        "central_waiting", "central_retry", "local_fallback",
    }
