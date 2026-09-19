"""H2 가격·거래대금 기반 테마 대장과 진입 근거의 순수 연구 계약."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from kiwoom_monitor.domain.ranking import normalize_stock_code


LEADERSHIP_VERSION = "theme-leadership/price-trade-value-v1"
ENTRY_THESIS_VERSION = "entry-thesis/v1"
LEADERSHIP_FACTOR_ID = "theme_leadership"
LIMIT_STATES = frozenset({"NONE", "NEAR", "TOUCHED", "UNKNOWN"})
TREND_STATES = frozenset({"ADVANCING", "STABLE", "WEAKENING", "RECOVERED", "UNKNOWN"})
EXPANSION_STATES = frozenset({"EXPANDING", "STABLE", "SLOWING", "REACCELERATING", "UNKNOWN"})
THESIS_POLICIES = frozenset({
    "PRICE_ONLY", "IMMEDIATE_EXIT", "REDUCE_ON_RISK", "CONFIRM_THEN_EXIT",
})
_SEOUL = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class ThemeMembership:
    revision_id: str
    theme_id: str
    member_codes: tuple[str, ...]
    available_at: str

    def __post_init__(self) -> None:
        normalized = tuple(dict.fromkeys(normalize_stock_code(code) for code in self.member_codes))
        if not self.revision_id or not self.theme_id or len(normalized) < 2 or "" in normalized:
            raise ValueError("theme leadership requires a revision and at least two members")
        _aware_datetime(self.available_at)
        object.__setattr__(self, "member_codes", normalized)


@dataclass(frozen=True)
class SecondTradePoint:
    input_ref: str
    code: str
    market: str
    observed_at: str
    available_at: str
    close: int
    high: int
    volume: int
    trade_value_won: int
    trade_count: int

    def __post_init__(self) -> None:
        observed = _aware_datetime(self.observed_at)
        available = _aware_datetime(self.available_at)
        if not self.input_ref or not normalize_stock_code(self.code):
            raise ValueError("second trade point identity is required")
        if self.market not in {"KRX", "NXT"}:
            raise ValueError("theme leadership keeps KRX and NXT separate")
        if available < observed:
            raise ValueError("second trade point cannot be available before observation")
        if self.close <= 0 or self.high <= 0:
            raise ValueError("second trade price must be positive")
        if min(self.volume, self.trade_value_won, self.trade_count) < 0:
            raise ValueError("second trade totals must not be negative")


@dataclass(frozen=True)
class UpperLimitReference:
    input_ref: str
    code: str
    price: int
    available_at: str

    def __post_init__(self) -> None:
        if not self.input_ref or not normalize_stock_code(self.code) or self.price <= 0:
            raise ValueError("upper limit reference is incomplete")
        _aware_datetime(self.available_at)


@dataclass(frozen=True)
class ThemeLeadershipParameters:
    window_seconds: int = 10
    max_data_age_seconds: int = 3
    price_exit_bps: int = 150
    price_reclaim_bps: int = 50
    slowdown_ratio_ppm: int = 600_000
    reacceleration_ratio_ppm: int = 1_250_000
    near_limit_bps: int = 100
    display_confirmation_seconds: int = 3

    def __post_init__(self) -> None:
        if self.window_seconds <= 0 or self.max_data_age_seconds <= 0:
            raise ValueError("leadership windows and data age must be positive")
        if not 0 < self.price_reclaim_bps < self.price_exit_bps < 10_000:
            raise ValueError("price reclaim/exit bps are invalid")
        if not 0 < self.slowdown_ratio_ppm < 1_000_000:
            raise ValueError("slowdown ratio must be below one")
        if self.reacceleration_ratio_ppm <= 1_000_000:
            raise ValueError("reacceleration ratio must be above one")
        if self.near_limit_bps < 0 or self.display_confirmation_seconds < 0:
            raise ValueError("limit distance and display confirmation must not be negative")


@dataclass(frozen=True)
class MemberLeadershipMetric:
    code: str
    first_price: int
    last_price: int
    peak_price: int
    return_bps: int
    drawdown_bps: int
    current_trade_value_won: int
    previous_trade_value_won: int
    trade_value_ratio_ppm: int | None
    trade_count: int
    return_rank_ppm: int
    trade_value_rank_ppm: int
    leadership_score_ppm: int


@dataclass(frozen=True)
class LeadershipEvent:
    event_id: str
    event_type: str
    theme_id: str
    code: str
    observed_at: str
    available_at: str
    input_refs: tuple[str, ...]
    facts: Mapping[str, Any]


@dataclass(frozen=True)
class ThemeLeadershipState:
    theme_id: str
    theme_revision_id: str
    venue: str
    displayed_leader_id: str = ""
    candidate_leader_id: str = ""
    candidate_since: str = ""
    leader_peak_price: int | None = None
    leader_last_price: int | None = None
    trend_state: str = "UNKNOWN"
    expansion_state: str = "UNKNOWN"
    last_observed_at: str = ""

    def __post_init__(self) -> None:
        if self.trend_state not in TREND_STATES or self.expansion_state not in EXPANSION_STATES:
            raise ValueError("unsupported leadership state")


@dataclass(frozen=True)
class ThemeLeadershipEvaluation:
    revision_id: str
    policy_version: str
    theme_id: str
    theme_revision_id: str
    venue: str
    as_of: str
    raw_leader_id: str
    displayed_leader_id: str
    trend_state: str
    expansion_state: str
    limit_state: str
    members: tuple[MemberLeadershipMetric, ...]
    events: tuple[LeadershipEvent, ...]
    input_refs: tuple[str, ...]
    quality: Mapping[str, Any]
    state: ThemeLeadershipState

    def __post_init__(self) -> None:
        if self.policy_version != LEADERSHIP_VERSION:
            raise ValueError("unsupported leadership policy version")
        if self.limit_state not in LIMIT_STATES:
            raise ValueError("unsupported limit state")
        body = asdict(self)
        body.pop("revision_id")
        if self.revision_id != _content_id("theme-leadership", body):
            raise ValueError("leadership revision id does not match immutable content")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EntryThesis:
    thesis_id: str
    run_id: str
    symbol: str
    theme_id: str
    theme_revision_id: str
    leader_id: str
    leadership_revision_id: str
    factor_refs: tuple[str, ...]
    hypothesis_refs: tuple[str, ...]
    created_at: str
    available_at: str
    policy_version: str = ENTRY_THESIS_VERSION

    def __post_init__(self) -> None:
        if not all((self.run_id, normalize_stock_code(self.symbol), self.theme_id,
                    self.theme_revision_id, normalize_stock_code(self.leader_id),
                    self.leadership_revision_id)):
            raise ValueError("entry thesis requires immutable entry evidence")
        created = _aware_datetime(self.created_at)
        available = _aware_datetime(self.available_at)
        if available < created:
            raise ValueError("entry thesis cannot be available before creation")
        body = asdict(self)
        body.pop("thesis_id")
        if self.thesis_id != _content_id("entry-thesis", body):
            raise ValueError("entry thesis id does not match immutable content")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ThesisPolicyDecision:
    decision_id: str
    run_id: str
    thesis_id: str
    leadership_revision_id: str
    policy_version: str
    thesis_state: str
    proposal: str
    final_action: str
    reasons: tuple[str, ...]
    available_at: str
    order_authorized: bool = False

    def __post_init__(self) -> None:
        if self.policy_version not in THESIS_POLICIES:
            raise ValueError("unsupported entry thesis policy")
        if self.thesis_state not in {"VALID", "AT_RISK", "INVALIDATED", "UNKNOWN"}:
            raise ValueError("unsupported thesis state")
        if self.proposal not in {"HOLD", "WAIT", "REDUCE", "EXIT", "NO_TRADE"}:
            raise ValueError("unsupported thesis proposal")
        if self.final_action not in {"HOLD", "WAIT", "REDUCE", "EXIT"}:
            raise ValueError("unsupported thesis action")
        if self.order_authorized:
            raise ValueError("research thesis decision cannot authorize an order")
        _aware_datetime(self.available_at)
        body = asdict(self)
        body.pop("decision_id")
        if self.decision_id != _content_id("thesis-decision", body):
            raise ValueError("thesis decision id does not match immutable content")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def second_trade_points_from_rows(
    rows: Iterable[Mapping[str, Any]],
) -> tuple[SecondTradePoint, ...]:
    """H1 중앙 저장 행의 날짜·초·epoch 가용시각을 H2 입력으로 명시 변환한다."""
    result: list[SecondTradePoint] = []
    for row in rows:
        trading_date = str(row.get("trading_date", ""))
        trade_second = str(row.get("trade_second", ""))
        try:
            observed = datetime.fromisoformat(
                f"{trading_date}T{trade_second}"
            ).replace(tzinfo=_SEOUL).astimezone(timezone.utc)
            raw_available = row.get("available_at")
            available = (
                datetime.fromtimestamp(float(raw_available), tz=timezone.utc)
                if isinstance(raw_available, (int, float))
                else _aware_datetime(raw_available).astimezone(timezone.utc)
            )
            document = {
                "trading_date": trading_date, "trade_second": trade_second,
                "code": normalize_stock_code(row.get("code", "")),
                "market": str(row.get("market", "")).upper(),
                "open": int(row.get("open", 0)), "high": int(row.get("high", 0)),
                "low": int(row.get("low", 0)), "close": int(row.get("close", 0)),
                "volume": int(row.get("volume", 0)),
                "trade_value_won": int(row.get("trade_value_won", 0)),
                "trade_count": int(row.get("trade_count", 0)),
                "available_at": available.isoformat(),
            }
            result.append(SecondTradePoint(
                input_ref=str(row.get("revision_id") or _content_id("second-trade", document)),
                code=document["code"], market=document["market"],
                observed_at=observed.isoformat(), available_at=available.isoformat(),
                close=document["close"], high=document["high"], volume=document["volume"],
                trade_value_won=document["trade_value_won"], trade_count=document["trade_count"],
            ))
        except (TypeError, ValueError, OSError):
            continue
    return tuple(sorted(result, key=lambda value: (value.observed_at, value.input_ref)))


def theme_memberships_from_snapshot(
    snapshot: Mapping[str, Any],
) -> tuple[ThemeMembership, ...]:
    """D5 전체 프로필 snapshot의 활성 프로필을 테마별 불변 구성으로 바꾼다."""
    snapshot_id = str(snapshot.get("snapshot_id", ""))
    raw_available = snapshot.get("available_at")
    available = (
        datetime.fromtimestamp(float(raw_available), tz=timezone.utc)
        if isinstance(raw_available, (int, float))
        else _aware_datetime(raw_available).astimezone(timezone.utc)
    )
    document = snapshot.get("document")
    if not snapshot_id or not isinstance(document, Mapping):
        return ()
    active = str(document.get("active_profile", "")).casefold()
    profiles = document.get("profiles")
    if not isinstance(profiles, list):
        return ()
    profile = next((
        value for value in profiles
        if isinstance(value, Mapping) and str(value.get("name", "")).casefold() == active
    ), None)
    if not isinstance(profile, Mapping) or not isinstance(profile.get("stock_themes"), list):
        return ()
    by_theme: dict[str, list[str]] = {}
    for assignment in profile["stock_themes"]:
        if not isinstance(assignment, Mapping):
            continue
        theme = str(assignment.get("theme", "")).strip()
        code = normalize_stock_code(assignment.get("code", ""))
        if theme and code and code not in by_theme.setdefault(theme, []):
            by_theme[theme].append(code)
    return tuple(
        ThemeMembership(
            revision_id=snapshot_id, theme_id=theme,
            member_codes=tuple(codes), available_at=available.isoformat(),
        )
        for theme, codes in sorted(by_theme.items()) if len(codes) >= 2
    )


def evaluate_theme_leadership(
    membership: ThemeMembership,
    points: Iterable[SecondTradePoint],
    *,
    as_of: str,
    venue: str,
    continuity_status: str,
    parameters: ThemeLeadershipParameters,
    previous_state: ThemeLeadershipState | None = None,
    upper_limits: Iterable[UpperLimitReference] = (),
) -> ThemeLeadershipEvaluation:
    """같은 테마·venue의 두 연속 창을 비교해 대장과 이탈/재가속을 계산한다."""
    cutoff = _aware_datetime(as_of).astimezone(timezone.utc)
    if _aware_datetime(membership.available_at).astimezone(timezone.utc) > cutoff:
        raise ValueError("theme membership was not available at evaluation time")
    if venue not in {"KRX", "NXT"}:
        raise ValueError("leadership evaluation venue must be KRX or NXT")
    state = previous_state or ThemeLeadershipState(
        membership.theme_id, membership.revision_id, venue,
    )
    if state.theme_id != membership.theme_id or state.venue != venue:
        raise ValueError("previous leadership state belongs to another theme or venue")
    members = tuple(dict.fromkeys(normalize_stock_code(code) for code in membership.member_codes))
    removed_leader = (
        state.displayed_leader_id
        if state.displayed_leader_id and state.displayed_leader_id not in members else ""
    )
    if removed_leader:
        state = ThemeLeadershipState(membership.theme_id, membership.revision_id, venue)
    window_start = cutoff - timedelta(seconds=parameters.window_seconds)
    previous_start = window_start - timedelta(seconds=parameters.window_seconds)
    eligible = tuple(sorted((
        point for point in points
        if normalize_stock_code(point.code) in members
        and point.market == venue
        and _aware_datetime(point.observed_at).astimezone(timezone.utc) <= cutoff
        and _aware_datetime(point.observed_at).astimezone(timezone.utc) > previous_start
        and _aware_datetime(point.available_at).astimezone(timezone.utc) <= cutoff
    ), key=lambda point: (point.observed_at, point.available_at, point.input_ref)))
    input_refs = tuple(dict.fromkeys((membership.revision_id, *(point.input_ref for point in eligible))))
    quality_reasons: list[str] = []
    if continuity_status != "COMPLETE":
        quality_reasons.append(f"second_trade_continuity_{continuity_status.casefold()}")
    current_by_code = {
        code: tuple(point for point in eligible if normalize_stock_code(point.code) == code
                    and _aware_datetime(point.observed_at).astimezone(timezone.utc) > window_start)
        for code in members
    }
    previous_by_code = {
        code: tuple(point for point in eligible if normalize_stock_code(point.code) == code
                    and _aware_datetime(point.observed_at).astimezone(timezone.utc) <= window_start)
        for code in members
    }
    missing_current = tuple(code for code in members if not current_by_code[code])
    if missing_current:
        quality_reasons.append("member_current_price_unavailable")
    stale = tuple(
        code for code, values in current_by_code.items()
        if values and (cutoff - _aware_datetime(values[-1].available_at).astimezone(timezone.utc)).total_seconds()
        > parameters.max_data_age_seconds
    )
    if stale:
        quality_reasons.append("member_second_trade_stale")
    if quality_reasons:
        return _unavailable_evaluation(
            membership, cutoff, venue, state, input_refs,
            tuple(dict.fromkeys(quality_reasons)), missing_current, stale,
            continuity_status,
        )

    raw_values: dict[str, dict[str, int | None]] = {}
    for code in members:
        current = current_by_code[code]
        previous = previous_by_code[code]
        first, last = current[0].close, current[-1].close
        peak = max(point.high for point in current)
        current_value = sum(point.trade_value_won for point in current)
        previous_value = sum(point.trade_value_won for point in previous)
        raw_values[code] = {
            "first": first,
            "last": last,
            "peak": peak,
            "return": (last - first) * 10_000 // first,
            "drawdown": max(0, (peak - last) * 10_000 // peak),
            "current_value": current_value,
            "previous_value": previous_value,
            "ratio": current_value * 1_000_000 // previous_value if previous_value > 0 else None,
            "trade_count": sum(point.trade_count for point in current),
        }
    return_ranks = _tied_ranks({code: int(value["return"] or 0) for code, value in raw_values.items()})
    value_ranks = _tied_ranks({code: int(value["current_value"] or 0) for code, value in raw_values.items()})
    metrics = tuple(MemberLeadershipMetric(
        code=code,
        first_price=int(raw_values[code]["first"] or 0),
        last_price=int(raw_values[code]["last"] or 0),
        peak_price=int(raw_values[code]["peak"] or 0),
        return_bps=int(raw_values[code]["return"] or 0),
        drawdown_bps=int(raw_values[code]["drawdown"] or 0),
        current_trade_value_won=int(raw_values[code]["current_value"] or 0),
        previous_trade_value_won=int(raw_values[code]["previous_value"] or 0),
        trade_value_ratio_ppm=(
            int(raw_values[code]["ratio"]) if raw_values[code]["ratio"] is not None else None
        ),
        trade_count=int(raw_values[code]["trade_count"] or 0),
        return_rank_ppm=return_ranks[code],
        trade_value_rank_ppm=value_ranks[code],
        leadership_score_ppm=(return_ranks[code] + value_ranks[code]) // 2,
    ) for code in members)
    raw_leader = max(
        metrics,
        key=lambda value: (
            value.leadership_score_ppm, value.current_trade_value_won,
            value.return_bps, value.code,
        ),
    ).code
    displayed, candidate, candidate_since, leader_events = _stabilize_leader(
        raw_leader, cutoff, state, membership, input_refs, parameters,
    )
    if removed_leader:
        leader_events = (_event(
            "LEADER_REMOVED_FROM_THEME", membership, raw_leader, cutoff, input_refs,
            {"previous_leader_id": removed_leader, "theme_revision_id": membership.revision_id},
        ), *leader_events)
    displayed_metric = next(value for value in metrics if value.code == displayed)
    trend, peak, trend_events = _trend_state(
        displayed_metric, cutoff, state, membership, input_refs, parameters,
        leader_changed=displayed != state.displayed_leader_id,
    )
    expansion, expansion_events = _expansion_state(
        displayed_metric, cutoff, state, membership, input_refs, parameters,
        leader_changed=displayed != state.displayed_leader_id,
    )
    limit_state, limit_ref = _limit_state(
        displayed_metric, cutoff, upper_limits, parameters,
    )
    if limit_ref:
        input_refs = tuple(dict.fromkeys((*input_refs, limit_ref)))
    next_state = ThemeLeadershipState(
        theme_id=membership.theme_id, theme_revision_id=membership.revision_id,
        venue=venue, displayed_leader_id=displayed,
        candidate_leader_id=candidate, candidate_since=candidate_since,
        leader_peak_price=peak, leader_last_price=displayed_metric.last_price,
        trend_state=trend, expansion_state=expansion,
        last_observed_at=cutoff.isoformat(),
    )
    events = (*leader_events, *trend_events, *expansion_events)
    body = {
        "policy_version": LEADERSHIP_VERSION,
        "theme_id": membership.theme_id,
        "theme_revision_id": membership.revision_id,
        "venue": venue,
        "as_of": cutoff.isoformat(),
        "raw_leader_id": raw_leader,
        "displayed_leader_id": displayed,
        "trend_state": trend,
        "expansion_state": expansion,
        "limit_state": limit_state,
        "members": tuple(asdict(value) for value in metrics),
        "events": tuple(asdict(value) for value in events),
        "input_refs": input_refs,
        "quality": {"status": "COMPLETE", "reasons": (), "continuity": continuity_status},
        "state": asdict(next_state),
    }
    return ThemeLeadershipEvaluation(
        revision_id=_content_id("theme-leadership", body),
        policy_version=LEADERSHIP_VERSION, theme_id=membership.theme_id,
        theme_revision_id=membership.revision_id, venue=venue,
        as_of=cutoff.isoformat(), raw_leader_id=raw_leader,
        displayed_leader_id=displayed, trend_state=trend,
        expansion_state=expansion, limit_state=limit_state,
        members=metrics, events=events, input_refs=input_refs,
        quality=body["quality"], state=next_state,
    )


def build_entry_thesis(
    *,
    run_id: str,
    symbol: str,
    leadership: ThemeLeadershipEvaluation,
    factor_refs: Iterable[str],
    hypothesis_refs: Iterable[str],
    created_at: str,
    available_at: str,
) -> EntryThesis:
    if leadership.quality.get("status") != "COMPLETE" or not leadership.displayed_leader_id:
        raise ValueError("entry thesis requires complete leadership evidence")
    created = _aware_datetime(created_at).astimezone(timezone.utc)
    available = _aware_datetime(available_at).astimezone(timezone.utc)
    if created < _aware_datetime(leadership.as_of).astimezone(timezone.utc):
        raise ValueError("entry thesis cannot predate leadership evidence")
    if available < created:
        raise ValueError("entry thesis cannot be available before creation")
    body = {
        "run_id": str(run_id), "symbol": normalize_stock_code(symbol),
        "theme_id": leadership.theme_id,
        "theme_revision_id": leadership.theme_revision_id,
        "leader_id": leadership.displayed_leader_id,
        "leadership_revision_id": leadership.revision_id,
        "factor_refs": tuple(dict.fromkeys(str(value) for value in factor_refs if str(value))),
        "hypothesis_refs": tuple(dict.fromkeys(str(value) for value in hypothesis_refs if str(value))),
        "created_at": created.isoformat(),
        "available_at": available.isoformat(),
        "policy_version": ENTRY_THESIS_VERSION,
    }
    return EntryThesis(thesis_id=_content_id("entry-thesis", body), **body)


def evaluate_entry_thesis(
    thesis: EntryThesis,
    leadership: ThemeLeadershipEvaluation,
    *,
    policy_version: str,
) -> ThesisPolicyDecision:
    """근거 변화에 대한 연구 제안만 만들며 주문이나 포지션을 직접 바꾸지 않는다."""
    if policy_version not in THESIS_POLICIES:
        raise ValueError("unsupported entry thesis policy")
    if thesis.theme_id != leadership.theme_id:
        raise ValueError("leadership evaluation belongs to another thesis theme")
    if (
        _aware_datetime(leadership.as_of).astimezone(timezone.utc)
        < _aware_datetime(thesis.available_at).astimezone(timezone.utc)
    ):
        raise ValueError("entry thesis cannot be evaluated with earlier leadership evidence")
    reasons: list[str] = []
    if leadership.quality.get("status") != "COMPLETE":
        thesis_state, proposal, final = "UNKNOWN", "NO_TRADE", "HOLD"
        reasons.append("leadership_data_unavailable")
    elif leadership.displayed_leader_id != thesis.leader_id:
        thesis_state = "INVALIDATED"
        reasons.append("entry_leader_replaced")
        proposal, final = _policy_action(policy_version, thesis_state)
    elif leadership.trend_state == "WEAKENING":
        thesis_state = "AT_RISK"
        reasons.append("entry_leader_price_exit")
        proposal, final = _policy_action(policy_version, thesis_state)
    else:
        thesis_state, proposal, final = "VALID", "HOLD", "HOLD"
        reasons.append("entry_leadership_thesis_intact")
    body = {
        "run_id": thesis.run_id, "thesis_id": thesis.thesis_id,
        "leadership_revision_id": leadership.revision_id,
        "policy_version": policy_version, "thesis_state": thesis_state,
        "proposal": proposal, "final_action": final, "reasons": tuple(reasons),
        "available_at": leadership.as_of, "order_authorized": False,
    }
    return ThesisPolicyDecision(
        decision_id=_content_id("thesis-decision", body), **body,
    )


def _unavailable_evaluation(
    membership: ThemeMembership,
    cutoff: datetime,
    venue: str,
    state: ThemeLeadershipState,
    input_refs: tuple[str, ...],
    reasons: tuple[str, ...],
    missing: tuple[str, ...],
    stale: tuple[str, ...],
    continuity_status: str,
) -> ThemeLeadershipEvaluation:
    event = _event(
        "DATA_UNAVAILABLE", membership, state.displayed_leader_id,
        cutoff, input_refs, {"reasons": reasons, "missing_members": missing, "stale_members": stale},
    )
    next_state = ThemeLeadershipState(
        theme_id=membership.theme_id, theme_revision_id=membership.revision_id,
        venue=venue, displayed_leader_id=state.displayed_leader_id,
        candidate_leader_id=state.candidate_leader_id, candidate_since=state.candidate_since,
        leader_peak_price=state.leader_peak_price, leader_last_price=state.leader_last_price,
        trend_state="UNKNOWN", expansion_state="UNKNOWN",
        last_observed_at=cutoff.isoformat(),
    )
    body = {
        "policy_version": LEADERSHIP_VERSION, "theme_id": membership.theme_id,
        "theme_revision_id": membership.revision_id, "venue": venue,
        "as_of": cutoff.isoformat(), "raw_leader_id": "",
        "displayed_leader_id": state.displayed_leader_id,
        "trend_state": "UNKNOWN", "expansion_state": "UNKNOWN", "limit_state": "UNKNOWN",
        "members": (), "events": (asdict(event),), "input_refs": input_refs,
        "quality": {
            "status": "PARTIAL", "reasons": reasons,
            "continuity": continuity_status,
            "missing_members": missing, "stale_members": stale,
        },
        "state": asdict(next_state),
    }
    return ThemeLeadershipEvaluation(
        revision_id=_content_id("theme-leadership", body),
        policy_version=LEADERSHIP_VERSION, theme_id=membership.theme_id,
        theme_revision_id=membership.revision_id, venue=venue,
        as_of=cutoff.isoformat(), raw_leader_id="",
        displayed_leader_id=state.displayed_leader_id,
        trend_state="UNKNOWN", expansion_state="UNKNOWN", limit_state="UNKNOWN",
        members=(), events=(event,), input_refs=input_refs,
        quality=body["quality"], state=next_state,
    )


def _stabilize_leader(
    raw_leader: str,
    cutoff: datetime,
    state: ThemeLeadershipState,
    membership: ThemeMembership,
    input_refs: tuple[str, ...],
    parameters: ThemeLeadershipParameters,
) -> tuple[str, str, str, tuple[LeadershipEvent, ...]]:
    if not state.displayed_leader_id:
        return raw_leader, "", "", (_event(
            "INITIAL_LEADER", membership, raw_leader, cutoff, input_refs,
            {"raw_leader_id": raw_leader},
        ),)
    if raw_leader == state.displayed_leader_id:
        return raw_leader, "", "", ()
    candidate_since = (
        state.candidate_since if state.candidate_leader_id == raw_leader and state.candidate_since
        else cutoff.isoformat()
    )
    elapsed = (cutoff - _aware_datetime(candidate_since).astimezone(timezone.utc)).total_seconds()
    if elapsed >= parameters.display_confirmation_seconds:
        return raw_leader, "", "", (_event(
            "LEADER_CHANGED", membership, raw_leader, cutoff, input_refs,
            {"previous_leader_id": state.displayed_leader_id, "confirmation_seconds": int(elapsed)},
        ),)
    return state.displayed_leader_id, raw_leader, candidate_since, (_event(
        "LEADER_CHANGE_CANDIDATE", membership, raw_leader, cutoff, input_refs,
        {"displayed_leader_id": state.displayed_leader_id, "required_seconds": parameters.display_confirmation_seconds},
    ),)


def _trend_state(
    metric: MemberLeadershipMetric,
    cutoff: datetime,
    state: ThemeLeadershipState,
    membership: ThemeMembership,
    input_refs: tuple[str, ...],
    parameters: ThemeLeadershipParameters,
    *,
    leader_changed: bool,
) -> tuple[str, int, tuple[LeadershipEvent, ...]]:
    previous_peak = None if leader_changed else state.leader_peak_price
    peak = max(metric.peak_price, previous_peak or metric.peak_price)
    drawdown = max(0, (peak - metric.last_price) * 10_000 // peak)
    if drawdown >= parameters.price_exit_bps:
        events = () if state.trend_state == "WEAKENING" else (_event(
            "PRICE_EXIT", membership, metric.code, cutoff, input_refs,
            {"peak_price": peak, "last_price": metric.last_price, "drawdown_bps": drawdown},
        ),)
        return "WEAKENING", peak, events
    if (
        not leader_changed and state.trend_state == "WEAKENING"
        and drawdown <= parameters.price_reclaim_bps
    ):
        return "RECOVERED", peak, (_event(
            "PRICE_RECLAIM", membership, metric.code, cutoff, input_refs,
            {"peak_price": peak, "last_price": metric.last_price, "drawdown_bps": drawdown},
        ),)
    if (
        not leader_changed and state.leader_last_price is not None
        and metric.last_price > state.leader_last_price
    ):
        return "ADVANCING", peak, ()
    return "STABLE", peak, ()


def _expansion_state(
    metric: MemberLeadershipMetric,
    cutoff: datetime,
    state: ThemeLeadershipState,
    membership: ThemeMembership,
    input_refs: tuple[str, ...],
    parameters: ThemeLeadershipParameters,
    *,
    leader_changed: bool,
) -> tuple[str, tuple[LeadershipEvent, ...]]:
    ratio = metric.trade_value_ratio_ppm
    if ratio is None:
        return "UNKNOWN", ()
    if ratio <= parameters.slowdown_ratio_ppm:
        events = () if state.expansion_state == "SLOWING" else (_event(
            "TRADE_VALUE_SLOWDOWN", membership, metric.code, cutoff, input_refs,
            {"trade_value_ratio_ppm": ratio},
        ),)
        return "SLOWING", events
    if (
        not leader_changed and state.expansion_state == "SLOWING"
        and ratio >= parameters.reacceleration_ratio_ppm
    ):
        return "REACCELERATING", (_event(
            "TRADE_VALUE_REACCELERATION", membership, metric.code, cutoff, input_refs,
            {"trade_value_ratio_ppm": ratio},
        ),)
    if ratio >= parameters.reacceleration_ratio_ppm:
        return "EXPANDING", ()
    return "STABLE", ()


def _limit_state(
    metric: MemberLeadershipMetric,
    cutoff: datetime,
    references: Iterable[UpperLimitReference],
    parameters: ThemeLeadershipParameters,
) -> tuple[str, str]:
    eligible = tuple(sorted((
        value for value in references
        if normalize_stock_code(value.code) == metric.code
        and _aware_datetime(value.available_at).astimezone(timezone.utc) <= cutoff
    ), key=lambda value: (value.available_at, value.input_ref)))
    if not eligible:
        return "UNKNOWN", ""
    reference = eligible[-1]
    if metric.peak_price >= reference.price:
        return "TOUCHED", reference.input_ref
    distance = max(0, (reference.price - metric.last_price) * 10_000 // reference.price)
    return ("NEAR" if distance <= parameters.near_limit_bps else "NONE"), reference.input_ref


def _event(
    event_type: str,
    membership: ThemeMembership,
    code: str,
    at: datetime,
    input_refs: tuple[str, ...],
    facts: Mapping[str, Any],
) -> LeadershipEvent:
    body = {
        "event_type": event_type, "theme_id": membership.theme_id,
        "code": normalize_stock_code(code), "observed_at": at.isoformat(),
        "available_at": at.isoformat(), "input_refs": input_refs, "facts": dict(facts),
    }
    return LeadershipEvent(event_id=_content_id("leader-event", body), **body)


def _policy_action(policy: str, thesis_state: str) -> tuple[str, str]:
    if policy == "PRICE_ONLY":
        return "HOLD", "HOLD"
    if policy == "IMMEDIATE_EXIT":
        return "EXIT", "EXIT"
    if policy == "REDUCE_ON_RISK":
        return "REDUCE", "REDUCE"
    if thesis_state == "INVALIDATED":
        return "EXIT", "EXIT"
    return "WAIT", "WAIT"


def _tied_ranks(values: Mapping[str, int]) -> dict[str, int]:
    unique = sorted(set(values.values()))
    if len(unique) == 1:
        return {code: 500_000 for code in values}
    rank = {value: index * 1_000_000 // (len(unique) - 1) for index, value in enumerate(unique)}
    return {code: rank[value] for code, value in values.items()}


def _aware_datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("theme leadership timestamps must be timezone-aware")
    return parsed


def _content_id(prefix: str, value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}:{hashlib.sha256(encoded).hexdigest()}"
