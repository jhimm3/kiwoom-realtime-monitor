"""가격을 합성하지 않는 모의 자동운용의 독립 시계 판단."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta

from kiwoom_monitor.application.breakout_strategy import (
    REGULAR_SESSION_EXIT_POLICY, StrategyDecision, regular_session_fill_deadline,
    regular_session_time_rejection,
)


SESSION_CLOCK_VERSION = "mock_automation_session_clock/v1"


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("session clock requires timezone-aware instants")
    return value


def session_clock_assessment(config, state, now: datetime, active=()) -> dict:
    """실제 체결의 진입 거래일과 주문 유효시간을 사용한다. 체결 상태는 바꾸지 않는다."""
    _aware(now)
    if config.session_exit_policy != REGULAR_SESSION_EXIT_POLICY:
        return {"reasons": [], "exit_required": False, "expired_intent_ids": []}
    opened = _aware(datetime.fromisoformat(state.opened_at)) if state.status == "open" else now
    deadline = regular_session_fill_deadline(opened)
    due = deadline - timedelta(seconds=config.session_exit_buffer_seconds)
    reasons = []
    if state.status == "open":
        if opened > now:
            reasons.append("position_time_from_future")
        if now >= due:
            reasons.append("session_exit_due")
        if now >= opened + timedelta(minutes=config.max_hold_minutes):
            reasons.append("max_hold_reached")
    if now >= deadline and (state.status == "open" or active):
        reasons.append("session_deadline_passed")
    expired = [record.intent.intent_id for record in active if now >= _aware(record.intent.expires_at)]
    if expired:
        reasons.append("order_validity_expired")
    return {
        "reasons": reasons, "exit_required": state.status == "open" and bool(reasons),
        "expired_intent_ids": expired, "deadline": deadline.isoformat(),
        "session_exit_due_at": due.isoformat(), "assessed_at": now.isoformat(),
    }


def content_id(prefix: str, body: dict) -> str:
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return prefix + "_" + hashlib.sha256(encoded).hexdigest()


def clock_exit_decision(*, spec_id, run_id, state, assessment, bar, now, maximum_age_seconds):
    """시계 Decision과 원본 가격 참조를 만든다. 봉의 available_at은 변경하지 않는다."""
    _aware(now)
    if not assessment["exit_required"] or state.status != "open":
        raise ValueError("clock exit requires an actual owned open position")
    if regular_session_time_rejection(now):
        raise ValueError("outside_regular_continuous_session")
    if bar is None or bar.code != state.symbol or bar.close <= 0:
        raise ValueError("owned_price_reference_missing")
    if type(maximum_age_seconds) is not int or maximum_age_seconds < 0:
        raise ValueError("price_freshness_limit_unknown")
    available = _aware(datetime.fromisoformat(bar.available_at))
    ended = _aware(datetime.fromisoformat(bar.bar_end))
    # 최근에 도착한 과거 봉도 현재 가격 근거로 쓰지 않는다.
    if available > now or ended > now:
        raise ValueError("owned_price_reference_from_future")
    if any((now - at).total_seconds() > maximum_age_seconds for at in (available, ended)):
        raise ValueError("owned_price_reference_stale")
    snapshot = {
        "version": SESSION_CLOCK_VERSION, "spec_id": spec_id, "run_id": run_id,
        "decided_at": now.isoformat(), "state_before": state.to_dict(),
        "reasons": assessment["reasons"], "deadline": assessment["deadline"],
        "price_reference": {
            "revision_id": bar.revision_id, "observation_key": bar.observation_key,
            "symbol": bar.code, "close": bar.close,
            "bar_end": bar.bar_end, "available_at": bar.available_at,
        },
    }
    snapshot_id = content_id("session_clock_snapshot", snapshot)
    body = {
        "run_id": run_id, "snapshot_id": snapshot_id, "symbol": state.symbol,
        "proposal": "EXIT", "final_action": "EXIT", "quantity": int(state.position_quantity),
        "signal_reference_price": int(bar.close), "required_capital_won": 0,
        "reasons": tuple(assessment["reasons"]), "constraints": (SESSION_CLOCK_VERSION,),
        "state_before": state.to_dict(), "state_after": state.to_dict(),
        "decided_at": now.isoformat(),
    }
    return StrategyDecision(decision_id=content_id("decision", body), **body), snapshot
