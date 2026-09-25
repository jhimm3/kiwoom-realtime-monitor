"""KRX·NXT 시간대별 실시간 구독과 후속 작업 정책."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from enum import Enum
from typing import Mapping
from zoneinfo import ZoneInfo


KST = ZoneInfo("Asia/Seoul")
# KRX notices:
# 2021 https://kind.krx.co.kr/external/2021/11/04/000107/20211104000121/99303.htm
# 2022 CREON 005930 raw 5m: 10:05..16:20 plus 16:30; KRX delayed-day notice
#      https://strn.krx.co.kr/corebbs5/BHPSTRN0401/view?bbsSeq=83
# 2023 https://kind.krx.co.kr/external/2023/11/02/000068/20231102000001/99303.htm
# 2024 https://kind.krx.co.kr/external/2024/10/31/000080/20241031000019/32104.htm
# 2025 https://kind.krx.co.kr/external/2025/10/30/000102/20251030000137/99303.htm
# Only dates checked against a venue notice and/or source bars belong here.
KRX_VERIFIED_DELAYED_DATES = frozenset({
    date(2021, 11, 18), date(2022, 11, 17), date(2023, 11, 16),
    date(2024, 11, 14), date(2025, 11, 13),
})


def krx_regular_session_hours(trading_date: date) -> tuple[time, time]:
    """Return verified regular-session open/close for a KRX trading date."""
    if trading_date in KRX_VERIFIED_DELAYED_DATES:
        return time(10, 0), time(16, 30)
    return time(9, 0), time(15, 30)


def krx_closing_auction_hours(trading_date: date) -> tuple[time, time]:
    _, close = krx_regular_session_hours(trading_date)
    start = (datetime.combine(trading_date, close) - timedelta(minutes=10)).time()
    return start, close
KRX_AFTER_MARKET_EFFECTIVE_DATE = date(2026, 9, 14)
LEGACY_SCHEDULE_VERSION = "krx-nxt-schedule/2026-09-13"
CURRENT_SCHEDULE_VERSION = "krx-nxt-schedule/2026-09-14"
KRX_REGULAR_RESEARCH_PROFILE = "krx-regular/v1"
KRX_AFTER_RESEARCH_PROFILE = "krx-after/v1"
KRX_FULL_DAY_RESEARCH_PROFILE = "krx-full-day/v1"
LEGACY_UNFILTERED_REPLAY_PROFILE = "legacy-unfiltered-krx/v0"
SUPPORTED_RESEARCH_SESSION_PROFILES = (
    KRX_REGULAR_RESEARCH_PROFILE,
    KRX_AFTER_RESEARCH_PROFILE,
    KRX_FULL_DAY_RESEARCH_PROFILE,
)
MOCK_KRX_REGULAR_LIMIT_POLICY = "manual-mock-krx-regular-limit/v2"
MOCK_KRX_AFTER_LIMIT_PROBE_POLICY = "manual-mock-krx-after-limit-probe/v1"


class SessionSupport(str, Enum):
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    UNKNOWN = "UNKNOWN"


class MarketSession(str, Enum):
    CLOSED = "CLOSED"
    KRX_OPENING_AUCTION = "KRX_OPENING_AUCTION"
    KRX_REGULAR = "KRX_REGULAR"
    KRX_CLOSING_AUCTION = "KRX_CLOSING_AUCTION"
    KRX_AFTER_HOURS_CLOSE = "KRX_AFTER_HOURS_CLOSE"
    KRX_LEGACY_PERIODIC_AUCTION = "KRX_LEGACY_PERIODIC_AUCTION"
    KRX_AFTER_MARKET = "KRX_AFTER_MARKET"
    NXT_PRE_MARKET = "NXT_PRE_MARKET"
    NXT_MAIN_MARKET = "NXT_MAIN_MARKET"
    NXT_AFTER_MARKET = "NXT_AFTER_MARKET"
    UNKNOWN = "UNKNOWN"


class MarketPhase(str, Enum):
    CONTINUOUS = "CONTINUOUS"
    AUCTION_ORDER_ENTRY = "AUCTION_ORDER_ENTRY"
    FIXED_PRICE = "FIXED_PRICE"
    PERIODIC_AUCTION = "PERIODIC_AUCTION"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class SessionWindow:
    venue: str
    trading_date: date
    session: MarketSession
    phase: MarketPhase
    starts_at: datetime
    ends_at: datetime
    schedule_version: str
    support: SessionSupport
    reason: str = ""

    def permits(self, capability: str) -> bool:
        if self.support is not SessionSupport.SUPPORTED:
            return False
        if capability == "observe":
            return self.session is not MarketSession.CLOSED
        if capability == "continuous_trade":
            return self.phase is MarketPhase.CONTINUOUS
        if capability == "order_entry":
            return self.phase in {
                MarketPhase.CONTINUOUS,
                MarketPhase.AUCTION_ORDER_ENTRY,
                MarketPhase.FIXED_PRICE,
                MarketPhase.PERIODIC_AUCTION,
            }
        if capability == "regular_close":
            return self.session is MarketSession.KRX_CLOSING_AUCTION
        if capability == "full_day_close":
            return False
        raise ValueError(f"unknown market-session capability: {capability}")


@dataclass(frozen=True)
class RealtimeSubscriptionTarget:
    """현재 연결에서 실제로 요청할 venue별 0B 종목과 정책 서명."""

    krx_codes: tuple[str, ...]
    nxt_codes: tuple[str, ...]
    signature: tuple[str, ...]

    @property
    def active_codes(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*self.krx_codes, *self.nxt_codes)))


@dataclass(frozen=True)
class MockOrderEntryDecision:
    """Frozen support decision for one broker-backed mock new order."""

    allowed: bool
    reason: str
    venue: str
    order_type: str
    session: MarketSession
    phase: MarketPhase
    schedule_version: str
    session_profile: str = KRX_REGULAR_RESEARCH_PROFILE

    @property
    def evidence(self) -> str:
        status = "SUPPORTED" if self.allowed else "UNSUPPORTED"
        fields = (
            status,
            f"reason={self.reason or 'supported'}",
            f"venue={self.venue}",
            f"order_type={self.order_type}",
            f"session={self.session.value}",
            f"phase={self.phase.value}",
            f"schedule={self.schedule_version}",
            f"profile={self.session_profile}",
        )
        return "|".join(fields)

    @property
    def policy_version(self) -> str:
        policy = (
            MOCK_KRX_AFTER_LIMIT_PROBE_POLICY
            if self.session is MarketSession.KRX_AFTER_MARKET
            else MOCK_KRX_REGULAR_LIMIT_POLICY
        )
        return f"{policy}@{self.schedule_version}"


def schedule_version_for(trading_date: date) -> str:
    return (
        CURRENT_SCHEDULE_VERSION
        if trading_date >= KRX_AFTER_MARKET_EFFECTIVE_DATE
        else LEGACY_SCHEDULE_VERSION
    )


def session_window_at(
    at: datetime,
    *,
    venue: str,
    environment: str = "real",
    eligible: bool | None = True,
    observed_phase: str | None = None,
) -> SessionWindow:
    """Return the effective-date schedule without guessing unknown dynamic phases.

    ``at`` must identify an instant. Naive values are interpreted as KST only for
    compatibility with the existing desktop scheduling helpers.
    """
    local = at.replace(tzinfo=KST) if at.tzinfo is None else at.astimezone(KST)
    trading_date = local.date()
    version = schedule_version_for(trading_date)
    normalized_venue = str(venue).strip().upper()
    if local.weekday() >= 5:
        return _window(local, normalized_venue, MarketSession.CLOSED, MarketPhase.UNKNOWN,
                       time.min, time.max, version, SessionSupport.UNSUPPORTED, "non_trading_day")
    if observed_phase is not None and observed_phase not in {phase.value for phase in MarketPhase}:
        return _window(local, normalized_venue, MarketSession.UNKNOWN, MarketPhase.UNKNOWN,
                       local.time(), local.time(), version, SessionSupport.UNKNOWN,
                       f"unknown_observed_phase:{observed_phase}")
    if normalized_venue == "KRX":
        result = _krx_window(local, environment, eligible, version)
    elif normalized_venue == "NXT":
        result = _nxt_window(local, environment, eligible, version)
    else:
        return _window(local, normalized_venue, MarketSession.UNKNOWN, MarketPhase.UNKNOWN,
                       local.time(), local.time(), version, SessionSupport.UNKNOWN, "unknown_venue")
    if observed_phase == MarketPhase.UNKNOWN.value:
        return replace(result, phase=MarketPhase.UNKNOWN, support=SessionSupport.UNKNOWN,
                       reason="observed_phase_unknown")
    if observed_phase:
        return replace(result, phase=MarketPhase(observed_phase))
    return result


def mock_order_entry_decision(
    at: datetime,
    *,
    environment: str,
    venue: str,
    order_type: str,
) -> MockOrderEntryDecision:
    """Allow verified regular orders and a manual KRX-after broker probe.

    This policy applies only to a new submission. Broker reconciliation, late fills,
    account recovery, and cancellation deliberately do not call it. The after-market
    allowance lets an authenticated manual probe reach Kiwoom; broker acceptance is
    still operational evidence rather than an inferred capability.
    """
    normalized_venue = str(venue).strip().upper()
    normalized_order_type = str(order_type).strip().upper()
    window = session_window_at(at, venue=normalized_venue, environment=environment)
    allowed = True
    reason = ""
    if environment != "mock":
        allowed = False
        reason = "mock_environment_required"
    elif normalized_venue != "KRX":
        allowed = False
        reason = "mock_venue_not_verified"
    elif normalized_order_type != "LIMIT":
        allowed = False
        reason = "mock_order_type_not_verified"
    elif window.session is MarketSession.KRX_AFTER_MARKET and window.phase is MarketPhase.CONTINUOUS:
        reason = "mock_after_broker_probe"
    elif window.support is not SessionSupport.SUPPORTED:
        allowed = False
        reason = window.reason or "mock_session_not_supported"
    elif window.session is not MarketSession.KRX_REGULAR or window.phase is not MarketPhase.CONTINUOUS:
        allowed = False
        reason = "mock_regular_continuous_limit_only"
    return MockOrderEntryDecision(
        allowed=allowed,
        reason=reason,
        venue=normalized_venue,
        order_type=normalized_order_type,
        session=window.session,
        phase=window.phase,
        schedule_version=window.schedule_version,
        session_profile=(
            KRX_AFTER_RESEARCH_PROFILE
            if window.session is MarketSession.KRX_AFTER_MARKET
            else KRX_REGULAR_RESEARCH_PROFILE
        ),
    )


def full_day_close_at(trading_date: date, *, venue: str) -> datetime | None:
    """Return a static close only where S1 has a confirmed high-level schedule."""
    if trading_date.weekday() >= 5:
        return None
    normalized_venue = str(venue).strip().upper()
    if normalized_venue == "KRX":
        close = time(20, 0) if trading_date >= KRX_AFTER_MARKET_EFFECTIVE_DATE else time(18, 0)
    elif normalized_venue == "NXT":
        close = time(20, 0)
    else:
        return None
    return datetime.combine(trading_date, close, tzinfo=KST)


def research_bar_allowed(
    bar_start: datetime,
    bar_end: datetime,
    *,
    venue: str,
    session_profile: str = KRX_REGULAR_RESEARCH_PROFILE,
    declared_session: str | None = None,
    declared_phase: str | None = None,
    declared_schedule_version: str | None = None,
) -> bool:
    """Gate strategy input independently from observation/subscription hours."""
    if session_profile == LEGACY_UNFILTERED_REPLAY_PROFILE:
        return str(venue).upper() == "KRX"
    if session_profile not in SUPPORTED_RESEARCH_SESSION_PROFILES:
        raise ValueError(f"unsupported research session profile: {session_profile}")
    if str(venue).upper() != "KRX" or bar_start.tzinfo is None or bar_end.tzinfo is None:
        return False
    start = bar_start.astimezone(KST)
    end = bar_end.astimezone(KST)
    if start.date() != end.date() or end <= start:
        return False
    if declared_schedule_version not in {None, "", schedule_version_for(start.date())}:
        return False
    if (
        session_profile == KRX_REGULAR_RESEARCH_PROFILE
        and
        start.date() < KRX_AFTER_MARKET_EFFECTIVE_DATE
        and start.date() not in KRX_VERIFIED_DELAYED_DATES
        and declared_session in {None, ""}
        and declared_phase in {None, ""}
    ):
        # Old observations had no session metadata and the original reader accepted
        # every strict KRX bar. Preserve that interpretation for old dates only.
        return True
    regular_open, regular_close = krx_regular_session_hours(start.date())
    regular_start = datetime.combine(start.date(), regular_open, tzinfo=KST)
    regular_end = datetime.combine(start.date(), regular_close, tzinfo=KST)
    after_start = datetime.combine(start.date(), time(16, 0), tzinfo=KST)
    after_end = datetime.combine(start.date(), time(20, 0), tzinfo=KST)
    in_regular = regular_start <= start and end <= regular_end
    in_after = (
        start.date() >= KRX_AFTER_MARKET_EFFECTIVE_DATE
        and after_start <= start and end <= after_end
    )
    if session_profile == KRX_REGULAR_RESEARCH_PROFILE:
        if declared_session not in {
            None, "", MarketSession.KRX_REGULAR.value, MarketSession.KRX_CLOSING_AUCTION.value,
        }:
            return False
        if declared_phase not in {
            None, "", MarketPhase.CONTINUOUS.value, MarketPhase.AUCTION_ORDER_ENTRY.value,
        }:
            return False
        return in_regular
    if session_profile == KRX_AFTER_RESEARCH_PROFILE:
        if declared_session not in {None, "", MarketSession.KRX_AFTER_MARKET.value}:
            return False
        if declared_phase not in {None, "", MarketPhase.CONTINUOUS.value}:
            return False
        return in_after
    # Full-day research is the union of regular trading and the new continuous
    # after market. 15:30-16:00 fixed-price/order-entry activity is deliberately
    # excluded because minute OHLC does not reproduce its matching priority.
    allowed_sessions = {
        None, "", MarketSession.KRX_REGULAR.value,
        MarketSession.KRX_CLOSING_AUCTION.value, MarketSession.KRX_AFTER_MARKET.value,
    }
    allowed_phases = {None, "", MarketPhase.CONTINUOUS.value, MarketPhase.AUCTION_ORDER_ENTRY.value}
    return declared_session in allowed_sessions and declared_phase in allowed_phases and (
        in_regular or in_after
    )


def research_session_key(at: datetime, *, session_profile: str) -> str | None:
    """Return the continuous research segment containing an instant."""
    if session_profile not in SUPPORTED_RESEARCH_SESSION_PROFILES:
        raise ValueError(f"unsupported research session profile: {session_profile}")
    local = at.astimezone(KST) if at.tzinfo is not None else at.replace(tzinfo=KST)
    if local.weekday() >= 5:
        return None
    value = local.time()
    if session_profile in {KRX_REGULAR_RESEARCH_PROFILE, KRX_FULL_DAY_RESEARCH_PROFILE}:
        regular_open, regular_close = krx_regular_session_hours(local.date())
        if regular_open <= value < regular_close:
            return f"{local.date().isoformat()}:KRX_REGULAR"
    if session_profile in {KRX_AFTER_RESEARCH_PROFILE, KRX_FULL_DAY_RESEARCH_PROFILE}:
        if local.date() >= KRX_AFTER_MARKET_EFFECTIVE_DATE and time(16, 0) <= value < time(20, 0):
            return f"{local.date().isoformat()}:KRX_AFTER"
    return None


def research_session_profile_document(session_profile: str) -> dict[str, object]:
    """Stable evidence/hash contract for a supported research session profile."""
    if session_profile not in SUPPORTED_RESEARCH_SESSION_PROFILES:
        raise ValueError(f"unsupported research session profile: {session_profile}")
    windows = {
        KRX_REGULAR_RESEARCH_PROFILE: ["09:00-15:30"],
        KRX_AFTER_RESEARCH_PROFILE: ["16:00-20:00"],
        KRX_FULL_DAY_RESEARCH_PROFILE: ["09:00-15:30", "16:00-20:00"],
    }
    return {
        "profile": session_profile,
        "venue": "KRX",
        "schedule_version_policy": "by_trading_date/v1",
        "verified_delayed_session_dates_kst": sorted(day.isoformat() for day in KRX_VERIFIED_DELAYED_DATES),
        "windows_kst": windows[session_profile],
        "verified_delayed_regular_window_kst": "10:00-16:30",
        "excluded_windows_kst": ["15:30-16:00"],
        "verified_delayed_excluded_window_kst": "16:30-17:00",
        "factor_session_policy": "reset_at_each_window/v1",
        "next_bar_policy": "continuous_next_minute_same_window/v1",
        "auction_execution_policy": "unsupported_without_orderbook/v1",
        "horizon_policy": "wall_clock_continuous_coverage/v1",
    }


def research_session_profile_contract_matches(document: object, session_profile: str) -> bool:
    """Validate either the original frozen v1 contract or its dated-hours extension."""
    if not isinstance(document, Mapping):
        return False
    try:
        current = research_session_profile_document(session_profile)
    except ValueError:
        return False
    if dict(document) == current:
        return True
    legacy = {key: value for key, value in current.items() if key not in {
        "verified_delayed_session_dates_kst",
        "verified_delayed_regular_window_kst",
        "verified_delayed_excluded_window_kst",
    }}
    return dict(document) == legacy


def _krx_window(
    local: datetime, environment: str, eligible: bool | None, version: str,
) -> SessionWindow:
    value = local.time()
    regular_open, regular_close = krx_regular_session_hours(local.date())
    auction_start, _ = krx_closing_auction_hours(local.date())
    offset = timedelta(minutes=60 if local.date() in KRX_VERIFIED_DELAYED_DATES else 0)

    def shifted(value: time) -> time:
        return (datetime.combine(local.date(), value) + offset).time()

    definitions = [
        (shifted(time(8, 30)), regular_open, MarketSession.KRX_OPENING_AUCTION, MarketPhase.AUCTION_ORDER_ENTRY),
        (regular_open, auction_start, MarketSession.KRX_REGULAR, MarketPhase.CONTINUOUS),
        (auction_start, regular_close, MarketSession.KRX_CLOSING_AUCTION, MarketPhase.AUCTION_ORDER_ENTRY),
        (regular_close, shifted(time(15, 40)), MarketSession.KRX_AFTER_HOURS_CLOSE, MarketPhase.AUCTION_ORDER_ENTRY),
        (shifted(time(15, 40)), shifted(time(16, 0)), MarketSession.KRX_AFTER_HOURS_CLOSE, MarketPhase.FIXED_PRICE),
    ]
    if local.date() >= KRX_AFTER_MARKET_EFFECTIVE_DATE:
        definitions.append((time(16, 0), time(20, 0), MarketSession.KRX_AFTER_MARKET, MarketPhase.CONTINUOUS))
    else:
        definitions.append((shifted(time(16, 0)), time(18, 0), MarketSession.KRX_LEGACY_PERIODIC_AUCTION,
                            MarketPhase.PERIODIC_AUCTION))
    for start, end, session, phase in definitions:
        if start <= value < end:
            support, reason = _eligibility_support(eligible)
            if environment == "mock" and session in {
                MarketSession.KRX_AFTER_HOURS_CLOSE,
                MarketSession.KRX_AFTER_MARKET,
                MarketSession.KRX_LEGACY_PERIODIC_AUCTION,
            }:
                support, reason = SessionSupport.UNSUPPORTED, "mock_session_not_verified"
            return _window(local, "KRX", session, phase, start, end, version, support, reason)
    return _window(local, "KRX", MarketSession.CLOSED, MarketPhase.UNKNOWN,
                   value, value, version, SessionSupport.UNSUPPORTED, "outside_session")


def _nxt_window(
    local: datetime, environment: str, eligible: bool | None, version: str,
) -> SessionWindow:
    value = local.time()
    definitions = (
        (time(8, 0), time(8, 50), MarketSession.NXT_PRE_MARKET),
        (time(9, 0, 30), time(15, 20), MarketSession.NXT_MAIN_MARKET),
        (time(15, 40), time(20, 0), MarketSession.NXT_AFTER_MARKET),
    )
    for start, end, session in definitions:
        if start <= value < end:
            support, reason = _eligibility_support(eligible)
            if environment == "mock":
                support, reason = SessionSupport.UNSUPPORTED, "mock_session_not_verified"
            elif support is SessionSupport.SUPPORTED:
                reason = "dynamic_phase_not_observed"
            return _window(local, "NXT", session, MarketPhase.UNKNOWN,
                           start, end, version, support, reason)
    if time(15, 30) <= value < time(15, 40):
        return _window(local, "NXT", MarketSession.NXT_AFTER_MARKET, MarketPhase.UNKNOWN,
                       time(15, 30), time(15, 40), version, SessionSupport.UNKNOWN,
                       "nxt_after_order_entry_phase_not_verified")
    return _window(local, "NXT", MarketSession.CLOSED, MarketPhase.UNKNOWN,
                   value, value, version, SessionSupport.UNSUPPORTED, "outside_session")


def _eligibility_support(eligible: bool | None) -> tuple[SessionSupport, str]:
    if eligible is None:
        return SessionSupport.UNKNOWN, "instrument_eligibility_unknown"
    if not eligible:
        return SessionSupport.UNSUPPORTED, "instrument_not_eligible"
    return SessionSupport.SUPPORTED, ""


def _window(
    local: datetime, venue: str, session: MarketSession, phase: MarketPhase,
    start: time, end: time, version: str, support: SessionSupport, reason: str,
) -> SessionWindow:
    return SessionWindow(
        venue=venue,
        trading_date=local.date(),
        session=session,
        phase=phase,
        starts_at=datetime.combine(local.date(), start, tzinfo=KST),
        ends_at=datetime.combine(local.date(), end, tzinfo=KST),
        schedule_version=version,
        support=support,
        reason=reason,
    )


def active_realtime_codes(
    codes: tuple[str, ...],
    nxt_enabled_codes: set[str],
    now: datetime,
) -> tuple[str, ...]:
    """실전 공통 정책에서 현재 어느 venue로든 관측할 종목을 반환한다."""
    return realtime_subscription_target(
        codes, nxt_enabled_codes, now, environment="real",
    ).active_codes


def realtime_subscription_target(
    codes: tuple[str, ...],
    nxt_enabled_codes: set[str],
    now: datetime,
    *,
    environment: str,
) -> RealtimeSubscriptionTarget:
    """S2a의 0B 관측 대상을 venue별로 계산한다.

    KRX 애프터 적격 공식 필드가 없으므로 시행일 이후에는 현재 요청 종목을
    KRX 관측 대상으로 삼는다. NXT 적격 목록은 오직 ``_NX`` 구독에만 쓴다.
    """
    local = now.replace(tzinfo=KST) if now.tzinfo is None else now.astimezone(KST)
    normalized_codes = tuple(dict.fromkeys(str(code).strip() for code in codes if str(code).strip()))
    normalized_nxt = {str(code).strip() for code in nxt_enabled_codes if str(code).strip()}
    version = schedule_version_for(local.date())
    if local.weekday() >= 5:
        return RealtimeSubscriptionTarget((), (), (str(environment), version, "CLOSED"))

    current = local.time().replace(tzinfo=None)
    environment = str(environment).strip().lower()
    # 실전은 09:00 첫 체결 전에 REG 승인을 받을 수 있도록 기존 08:55
    # 준비 경계를 유지한다. 확인하지 않은 mock 관측 시간은 넓히지 않는다.
    krx_start = time(8, 55) if environment == "real" else time(9, 0)
    krx_open = krx_start <= current < time(15, 30)
    if environment == "real" and local.date() >= KRX_AFTER_MARKET_EFFECTIVE_DATE:
        krx_open = krx_start <= current < time(20, 0)
    krx_codes = normalized_codes if krx_open else ()

    # 기존 NXT 0B 연결은 08:00~20:00 동안 유지한다. 세부 매매 phase의
    # UNKNOWN/CLOSED는 전략·주문 gate가 판단하며 관측 연결을 끊는 근거로 쓰지 않는다.
    nxt_open = environment == "real" and time(8, 0) <= current < time(20, 0)
    nxt_codes = tuple(code for code in normalized_codes if nxt_open and code in normalized_nxt)

    krx_window = session_window_at(local, venue="KRX", environment=environment)
    nxt_window = session_window_at(local, venue="NXT", environment=environment)
    signature = (
        environment,
        version,
        krx_window.session.value,
        krx_window.phase.value,
        "KRX_ON" if krx_codes else "KRX_OFF",
        nxt_window.session.value,
        nxt_window.phase.value,
        nxt_window.support.value,
        "NXT_ON" if nxt_codes else "NXT_OFF",
    )
    return RealtimeSubscriptionTarget(krx_codes, nxt_codes, signature)


def is_nxt_only_session(environment: str, now: datetime) -> bool:
    """실전 환경의 NXT 단독 거래 시간인지 판단한다."""
    if environment != "real" or now.weekday() >= 5:
        return False
    target = realtime_subscription_target(("_probe",), {"_probe"}, now, environment=environment)
    return bool(target.nxt_codes) and not target.krx_codes


def next_realtime_session_boundary(now: datetime) -> datetime:
    """기존 실시간 세션 재구독 경계 중 다음 시각을 반환한다."""
    boundaries = (
        (7, 55, 0), (8, 0, 0), (8, 50, 0), (8, 55, 0),
        (9, 0, 0), (9, 0, 30), (15, 20, 0), (15, 30, 0),
        (15, 35, 0), (15, 40, 0), (16, 0, 0), (20, 0, 0), (20, 5, 0),
    )
    candidates = [
        now.replace(hour=hour, minute=minute, second=second, microsecond=0)
        for hour, minute, second in boundaries
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
