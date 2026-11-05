"""Bind a CLOSE entry to its stored Shadow setup and explicit mock order terms."""

from __future__ import annotations

from datetime import datetime
import json

from .breakout_strategy import StrategyState
from .close_and_ipo import matches_close_shadow
from .mock_automation_execution import (
    MockAutomationGateStatus,
    assess_mock_automation_decision_from_risk,
    dispatch_mock_automation_decision_from_risk,
)
from .mock_automation_session_clock import content_id
from kiwoom_monitor.domain.execution_activation import CLOSE_NATIVE_PLAN_MODE
from kiwoom_monitor.domain.order_contract import OrderPlanRef
from kiwoom_monitor.domain.strategy_contract import StrategyDecision


CLOSE_ENTRY_COLLECTION = "execution_close_entry_evidence"
CLOSE_ORDER_POLICY_VERSION = "close_execution_adapter/v1"


def close_entry_plan_id(spec_id: str, run_id: str, event_id: str) -> str:
    return content_id("close_plan", {"spec_id": spec_id, "run_id": run_id, "event_id": event_id})


def build_close_entry_evidence(spec, *, run_id, parameters, event, quote, decided_at, now):
    """Freeze a sized Decision. Native claim independently repeats this pure check."""
    terms = spec.close_execution_policy
    if spec.execution_plan_mode != CLOSE_NATIVE_PLAN_MODE or terms is None:
        raise ValueError("CLOSE native execution terms are missing")
    if not matches_close_shadow(parameters, event):
        raise ValueError("CLOSE entry does not match its stored Shadow setup")
    at = quote.get("available_at")
    if isinstance(at, str):
        at = datetime.fromisoformat(at)
    gap = spec.maximum_data_gap_seconds
    if (not isinstance(at, datetime) or at.utcoffset() is None or decided_at.utcoffset() is None
            or now.utcoffset() is None or gap is None or gap <= 0
            or quote.get("durable") is not False or quote.get("market_status") != "TRADABLE"
            or not isinstance(quote.get("input_ref"), str) or not quote["input_ref"].startswith("collector-trade:")
            or type(quote.get("price_won")) is not int or quote["price_won"] <= 0
            or not 0 <= (now - at).total_seconds() <= gap
            or not 0 <= (now - decided_at).total_seconds() <= gap
            or at > decided_at):
        raise ValueError("CLOSE entry requires a fresh acknowledged 0B quote")
    available = datetime.fromisoformat(event["available_at"])
    expiry = datetime.fromisoformat(event["expires_at"])
    deadline = datetime.fromisoformat(event["close_ipo_entry_setup"]["entry_deadline"])
    if (not 0 <= (now - available).total_seconds() <= gap or decided_at < available
            or now >= min(expiry, deadline)):
        raise ValueError("CLOSE entry signal expired or comes from the future")
    signal_price = event.get("signal_reference_price")
    if (type(signal_price) is not int or signal_price <= 0
            or quote["price_won"] * 10_000 > signal_price * (10_000 + terms.maximum_adverse_slippage_bps)):
        raise ValueError("CLOSE entry quote exceeds the approved signal slippage bound")
    frozen_quote = {"price_won": quote["price_won"], "available_at": at.isoformat(),
                    "input_ref": quote["input_ref"], "durable": False, "market_status": "TRADABLE"}
    snapshot = {"version": "close_entry_snapshot/v1", "spec_id": spec.spec_id,
                "candidate_package_hash": spec.candidate_package_hash, "account_ref": spec.account_scope.account_ref,
                "execution_run_id": run_id, "candidate_event_id": event["event_id"],
                "candidate_snapshot_id": event["snapshot_id"], "quote": frozen_quote}
    state = StrategyState().to_dict()
    body = {"run_id": run_id, "snapshot_id": content_id("snapshot", snapshot), "symbol": event["symbol"],
            "proposal": "ENTER", "final_action": "ENTER", "quantity": event["quantity"],
            "signal_reference_price": quote["price_won"], "required_capital_won": quote["price_won"] * event["quantity"],
            "reasons": ("preclose_return_setup",), "constraints": (CLOSE_ORDER_POLICY_VERSION,),
            "state_before": state, "state_after": state, "decided_at": decided_at.isoformat()}
    decision = StrategyDecision(decision_id=content_id("decision", body), **body)
    document = json.loads(json.dumps({"snapshot": snapshot, "decision": decision.to_dict()}, allow_nan=False))
    proof_id = content_id("close_entry", document)
    plan = OrderPlanRef(close_entry_plan_id(spec.spec_id, run_id, event["event_id"]), proof_id, 0)
    return {**document, "proof_id": proof_id}, decision, plan


def dispatch_close_entry(repository, runtime, spec, *, parameters, event, quote, recovery, risk):
    """Reuse the existing Risk gate, immutable writer and planned order transaction."""
    plan_id = close_entry_plan_id(spec.spec_id, runtime.run_id, event["event_id"])
    saved = repository.load_close_entry_evidence(runtime.account_ref, plan_id)
    now = runtime.server_now()
    decided_at = now
    if saved is not None:
        previous = saved
        quote = previous["snapshot"]["quote"]
        decided_at = datetime.fromisoformat(previous["decision"]["decided_at"])
    scope = {"account_ref": runtime.account_ref, "spec_id": spec.spec_id, "strategy_ref": spec.strategy_ref}
    from .mock_automation_execution import mock_automation_intent_id

    if saved is not None:
        evidence, decision, plan = build_close_entry_evidence(spec, run_id=runtime.run_id, parameters=parameters,
            event=event, quote=quote, decided_at=decided_at, now=decided_at)
        if evidence != previous:
            raise ValueError("stored CLOSE entry evidence changed")
        if runtime.load_intent(mock_automation_intent_id(spec.spec_id, decision.decision_id)) is not None:
            return dispatch_mock_automation_decision_from_risk(repository, runtime, decision, recovery, risk,
                plan=plan, **scope)
    evidence, decision, plan = build_close_entry_evidence(spec, run_id=runtime.run_id, parameters=parameters,
        event=event, quote=quote, decided_at=decided_at, now=now)
    # A new proof is retained only after the ordinary current Risk gate approves it.
    if runtime.load_intent(mock_automation_intent_id(spec.spec_id, decision.decision_id)) is None:
        gate = assess_mock_automation_decision_from_risk(repository, runtime, decision, recovery, risk, plan=plan, **scope)
        if gate.status is MockAutomationGateStatus.BLOCKED:
            return gate, None, None
    repository.save_close_entry_evidence(runtime.account_ref, plan_id, evidence)
    return dispatch_mock_automation_decision_from_risk(repository, runtime, decision, recovery, risk, plan=plan, **scope)
