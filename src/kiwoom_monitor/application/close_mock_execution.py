"""Bind a CLOSE entry to its stored Shadow setup and explicit mock order terms."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import json

from .breakout_strategy import StrategyState
from .close_and_ipo import CloseIpoPosition, CloseIpoSetup, ClosePolicy, evaluate_close_gap, matches_close_shadow
from .market_session_schedule import KST, MarketPhase, MarketSession, SessionSupport
from .research_replay import KrxMinuteBarFrame
from .mock_automation_execution import (
    MockAutomationGateStatus,
    assess_mock_automation_decision_from_risk,
    dispatch_mock_automation_decision_from_risk,
)
from .mock_automation_session_clock import content_id
from .mock_automation_risk import mock_owned_buy_basis
from kiwoom_monitor.domain.execution_activation import CLOSE_NATIVE_PLAN_MODE
from kiwoom_monitor.domain.order_contract import OrderPlanRef, OrderSide, TERMINAL_ORDER_STATES
from kiwoom_monitor.domain.strategy_contract import StrategyDecision


CLOSE_ENTRY_COLLECTION = "execution_close_entry_evidence"
CLOSE_EXIT_COLLECTION = "execution_close_exit_evidence"
CLOSE_ORDER_POLICY_VERSION = "close_execution_adapter/v1"
CLOSE_CANCEL_VERSION = "close-entry-deadline-cancel/v1"


@dataclass(frozen=True)
class CloseEntryDeadlineCancelClaim:
    plan: OrderPlanRef
    risk_snapshot_id: str
    risk_reconciliation_revision: int
    recovery: object

    def to_dict(self):
        return {"version": CLOSE_CANCEL_VERSION, "plan": asdict(self.plan),
                "risk_snapshot_id": self.risk_snapshot_id,
                "risk_reconciliation_revision": self.risk_reconciliation_revision}

    @classmethod
    def from_dict(cls, value, recovery):
        if (set(value) != {"version", "plan", "risk_snapshot_id", "risk_reconciliation_revision"}
                or value["version"] != CLOSE_CANCEL_VERSION or recovery is None
                or type(value["risk_reconciliation_revision"]) is not int
                or value["risk_reconciliation_revision"] <= 0):
            raise ValueError("invalid CLOSE deadline cancellation claim")
        return cls(OrderPlanRef(**value["plan"]), value["risk_snapshot_id"],
                   value["risk_reconciliation_revision"], recovery)


def close_entry_plan_id(spec_id: str, run_id: str, event_id: str) -> str:
    return content_id("close_plan", {"spec_id": spec_id, "run_id": run_id, "event_id": event_id})


def _close_quote(spec, quote, decided_at, now):
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
        raise ValueError("CLOSE execution requires a fresh acknowledged 0B quote")
    return {"price_won": quote["price_won"], "available_at": at.isoformat(),
            "input_ref": quote["input_ref"], "durable": False, "market_status": "TRADABLE"}


def frozen_close_candidate(event):
    value = {key: item for key, item in event.items() if key != 'accepted_sequence'}
    value['expires_at'] = datetime.fromisoformat(value['expires_at']).astimezone(KST).isoformat()
    return value


def build_close_entry_evidence(spec, *, run_id, parameters, event, quote, decided_at, now, retain_candidate=True):
    """Freeze a sized Decision. Native claim independently repeats this pure check."""
    terms = spec.close_execution_policy
    if spec.execution_plan_mode != CLOSE_NATIVE_PLAN_MODE or terms is None:
        raise ValueError("CLOSE native execution terms are missing")
    if not matches_close_shadow(parameters, event):
        raise ValueError("CLOSE entry does not match its stored Shadow setup")
    frozen_quote = _close_quote(spec, quote, decided_at, now)
    gap = spec.maximum_data_gap_seconds
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
    snapshot = {"version": "close_entry_snapshot/v2" if retain_candidate else "close_entry_snapshot/v1", "spec_id": spec.spec_id,
                "candidate_package_hash": spec.candidate_package_hash, "account_ref": spec.account_scope.account_ref,
                "execution_run_id": run_id, "candidate_event_id": event["event_id"],
                "candidate_snapshot_id": event["snapshot_id"], "quote": frozen_quote}
    if retain_candidate:
        snapshot['candidate'] = frozen_close_candidate(event)
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
            event=event, quote=quote, decided_at=decided_at, now=decided_at,
            retain_candidate=saved['snapshot']['version'] != 'close_entry_snapshot/v1')
        if evidence != previous:
            raise ValueError("stored CLOSE entry evidence changed")
        if runtime.load_intent(mock_automation_intent_id(spec.spec_id, decision.decision_id)) is not None:
            return dispatch_mock_automation_decision_from_risk(repository, runtime, decision, recovery, risk,
                plan=plan, **scope)
    evidence, decision, plan = build_close_entry_evidence(spec, run_id=runtime.run_id, parameters=parameters,
        event=event, quote=quote, decided_at=decided_at, now=now,
        retain_candidate=saved is None or saved['snapshot']['version'] != 'close_entry_snapshot/v1')
    # A new proof is retained only after the ordinary current Risk gate approves it.
    if runtime.load_intent(mock_automation_intent_id(spec.spec_id, decision.decision_id)) is None:
        gate = assess_mock_automation_decision_from_risk(repository, runtime, decision, recovery, risk, plan=plan, **scope)
        if gate.status is MockAutomationGateStatus.BLOCKED:
            return gate, None, None
    repository.save_close_entry_evidence(runtime.account_ref, plan_id, evidence)
    return dispatch_mock_automation_decision_from_risk(repository, runtime, decision, recovery, risk, plan=plan, **scope)


def close_exit_key(plan: OrderPlanRef) -> str:
    return content_id("close_exit_slot", asdict(plan))


def validate_close_entry_deadline(spec, *, run_id, parameters, event, entry_evidence, root, head,
                                  fill_events, recovery, risk, now):
    """Authorize only cancellation of the original, currently mapped BUY remainder."""
    from .mock_automation_execution import mock_automation_intent_id
    from .mock_automation_risk import mock_automation_risk_account_revision

    entered_at = datetime.fromisoformat(entry_evidence["decision"]["decided_at"])
    expected, decision, plan = build_close_entry_evidence(spec, run_id=run_id, parameters=parameters,
        event=event, quote=entry_evidence["snapshot"]["quote"], decided_at=entered_at, now=entered_at,
        retain_candidate=entry_evidence['snapshot']['version'] != 'close_entry_snapshot/v1')
    if (expected != entry_evidence or root is None
            or root.state.value not in {"ACCEPTED", "PARTIALLY_FILLED"}
            or (root.intent.intent_id, root.intent.account_ref, root.intent.run_id, root.intent.environment,
                root.intent.symbol, root.intent.side, root.intent.plan, root.intent.policy_version)
            != (mock_automation_intent_id(spec.spec_id, decision.decision_id), spec.account_scope.account_ref,
                run_id, "mock", event["symbol"], OrderSide.BUY, plan, CLOSE_ORDER_POLICY_VERSION)
            or not root.broker_order_id or root.last_broker_as_of is None
            or not 0 <= root.detailed_filled_quantity == root.filled_quantity == root.broker_reported_filled_quantity < root.intent.quantity
            or (head.plan.plan_id, head.plan.policy_ref, head.symbol, head.quantity)
            != (plan.plan_id, plan.policy_ref, event["symbol"], root.detailed_filled_quantity)
            or head.unreconciled_sell_quantity):
        raise ValueError("CLOSE deadline requires the confirmed original BUY remainder")
    deadline = datetime.fromisoformat(event["close_ipo_entry_setup"]["entry_deadline"])
    if now < deadline:
        raise ValueError("CLOSE entry deadline has not arrived")
    age = spec.maximum_data_gap_seconds
    account = recovery.account
    orders = [order for order in recovery.orders if order.broker_order_id == root.broker_order_id]
    if (len(orders) != 1 or age is None or (risk.account_ref, risk.execution_run_id, risk.binding_revision)
            != (spec.account_scope.account_ref, run_id, spec.binding_revision)
            or risk.account_revision != mock_automation_risk_account_revision(recovery)
            or (account.account_ref, account.as_of) != (risk.account_ref, risk.account_as_of)
            or risk.submission_unknown_count or risk.balance_mismatch_count
            or head.fill_cursor > risk.execution_event_high_watermark
            or dict(risk.owned_position_quantities).get(event["symbol"], 0) < head.quantity
            or account.positions.get(event["symbol"], 0) < head.quantity
            or any(not 0 <= (now - at).total_seconds() <= age
                   for at in (risk.account_as_of, risk.observed_at, root.last_broker_as_of))):
        raise ValueError("CLOSE deadline requires a current matching broker and Risk cut")
    order = orders[0]
    if ((order.account_ref, order.symbol, order.state, order.filled_quantity, order.remaining_quantity, order.as_of)
            != (root.intent.account_ref, root.intent.symbol, root.state, root.detailed_filled_quantity,
                root.intent.quantity - root.detailed_filled_quantity, root.last_broker_as_of)
            or any(fill.execution_id not in root.fill_ids for fill in order.fills)):
        raise ValueError("CLOSE deadline broker order changed")
    fills = [row for row in fill_events if row["event_type"] == "FILL"]
    if (any(row["event_type"] == "CANCEL_STARTED" for row in fill_events)
            or sum(row["quantity"] for row in fills) != head.quantity
            or tuple(row["broker_execution_id"] for row in fills) != root.fill_ids):
        raise ValueError("CLOSE deadline native fill or cancellation history changed")


def prepare_close_entry_deadline_claim(repository, runtime, execution, spec, root, *, parameters, event, recovery, risk):
    entry = repository.load_close_entry_evidence(runtime.account_ref, root.intent.plan.plan_id)
    head = execution.load_plan_position(runtime.account_ref, runtime.run_id, root.intent.symbol, root.intent.plan)
    if entry is None:
        raise ValueError("CLOSE original entry evidence is missing")
    validate_close_entry_deadline(spec, run_id=runtime.run_id, parameters=parameters, event=event,
        entry_evidence=entry, root=root, head=head, fill_events=execution.events(root.intent.intent_id),
        recovery=recovery, risk=risk, now=runtime.server_now())
    return CloseEntryDeadlineCancelClaim(head.plan, risk.snapshot_id, risk.reconciliation_revision, recovery)


def cancel_close_entry_deadline(repository, runtime, execution, spec, expected, *, parameters, event,
                                recovery, risk, control_revision):
    """Claim once through the existing cancel primitive; late broker fills remain authoritative."""
    def build_claim(root):
        return prepare_close_entry_deadline_claim(repository, runtime, execution, spec, root,
            parameters=parameters, event=event, recovery=recovery, risk=risk)

    return runtime.cancel_automation_intent(expected, spec_id=spec.spec_id, control_revision=control_revision,
                                          build_planned_cancel_claim=build_claim)


def _completed_close_gap(position, observations, decided_at):
    setup, window = position.setup, position.setup.next_session
    if window is None:
        return None  # Older stored setups retain their 0B protection and deadline path.
    if ((window.venue, window.trading_date, window.starts_at, window.session, window.phase, window.support)
            != ('KRX', setup.next_date, setup.next_open_at, MarketSession.KRX_REGULAR, MarketPhase.CONTINUOUS,
                SessionSupport.SUPPORTED) or decided_at.astimezone(KST).date() != setup.next_date):
        return None
    if len(observations) != 2 or len({row['revision_id'] for row in observations}) != 2:
        raise ValueError('CLOSE gap requires two distinct native bar revisions')
    frames = []
    for row in observations:
        payload = row['payload']
        if ((row['kind'], row['subject'], row['venue'], row['value_kind'], row['completeness'])
                != ('minute_bar', setup.symbol, 'KRX', 'actual', 'complete')
                or payload.get('window_closed') is not True or payload.get('capture_quality') != 'complete'
                or payload.get('market') != 'KRX' or payload.get('code') != setup.symbol
                or payload.get('price_unit') != 'won'
                or any(type(payload.get(key)) is not int for key in
                       ('open', 'high', 'low', 'close', 'volume', 'trade_value_million_won'))):
            raise ValueError('CLOSE gap requires completed actual KRX bars in won')
        frames.append(KrxMinuteBarFrame(row['revision_id'], row['observation_key'], setup.symbol,
            payload['bar_start'], payload['bar_end'], row['available_at'],
            payload['open'], payload['high'], payload['low'], payload['close'], payload['volume'],
            payload['trade_value_million_won'], bool(payload.get('session_finalized', False)),
            payload['capture_quality'], payload['finalization_source'], market_session=payload['session'],
            market_phase=payload['phase'], schedule_version=payload['schedule_version']))
    result = evaluate_close_gap(position, frames, session=window, cut=decided_at)
    return result if result.action == 'EXIT' else None


def build_close_exit_evidence(spec, *, run_id, parameters, event, entry_evidence, root, head, fill_events,
                              trigger_quote, quote, decided_at, now, gap_observations=None):
    """Manage only the original plan's detailed fills, independently of new-entry settings."""
    entered_at = datetime.fromisoformat(entry_evidence["decision"]["decided_at"])
    expected_entry, entry_decision, original_plan = build_close_entry_evidence(spec, run_id=run_id,
        parameters=parameters, event=event, quote=entry_evidence["snapshot"]["quote"], decided_at=entered_at, now=entered_at,
        retain_candidate=entry_evidence['snapshot']['version'] != 'close_entry_snapshot/v1')
    from .mock_automation_execution import mock_automation_intent_id

    if (entry_evidence != expected_entry or root is None or root.state not in TERMINAL_ORDER_STATES
            or (root.intent.intent_id, root.intent.run_id, root.intent.account_ref, root.intent.environment,
                root.intent.side, root.intent.symbol, root.intent.policy_version, getattr(root.intent, "plan", None))
            != (mock_automation_intent_id(spec.spec_id, entry_decision.decision_id), run_id,
                spec.account_scope.account_ref, "mock", OrderSide.BUY, event["symbol"], CLOSE_ORDER_POLICY_VERSION, original_plan)
            or (head.plan.plan_id, head.plan.policy_ref) != (original_plan.plan_id, original_plan.policy_ref)
            or head.symbol != event["symbol"] or head.quantity <= 0 or head.unreconciled_sell_quantity):
        raise ValueError("CLOSE exit requires the original terminal entry and confirmed plan quantity")
    fills = [row for row in fill_events if row["event_type"] == "FILL"]
    quantity = basis = 0
    for fill in fills:
        if (not fill.get("broker_execution_id") or type(fill["quantity"]) is not int or fill["quantity"] <= 0
                or type(fill["price"]) is not int or fill["price"] <= 0):
            raise ValueError("CLOSE entry detailed fill basis is invalid")
        basis = mock_owned_buy_basis(quantity, basis, fill["quantity"], fill["price"])
        quantity += fill["quantity"]
    if (quantity != root.detailed_filled_quantity or quantity < head.quantity
            or len(fills) != len(root.fill_ids)
            or {row["broker_execution_id"] for row in fills} != set(root.fill_ids)):
        raise ValueError("CLOSE entry detailed fill basis is incomplete")
    trigger = _close_quote(spec, trigger_quote, decided_at, now)
    execution_quote = _close_quote(spec, quote, decided_at, now)
    if (datetime.fromisoformat(trigger["available_at"]) > datetime.fromisoformat(execution_quote["available_at"])
            or execution_quote["price_won"] * 10_000 < trigger["price_won"] * (
                10_000 - spec.close_execution_policy.protective_slippage_bps)):
        raise ValueError("CLOSE exit quote exceeds the approved protective slippage bound")
    setup = CloseIpoSetup.from_document(event["close_ipo_entry_setup"])
    if not isinstance(setup.policy, ClosePolicy) or setup.policy.variant != "CLOSE":
        raise ValueError("CLOSE exit requires the original ordinary CLOSE policy")
    local = decided_at.astimezone(KST)
    if trigger["price_won"] * 10_000 <= basis * (10_000 - setup.policy.protection_loss_bps):
        reason = "filled_position_protection_0B"
    elif local.date() > setup.next_date:
        reason = "next_session_management_overdue_0B"
    elif local.date() == setup.next_date and decided_at >= setup.next_open_at + timedelta(
            minutes=setup.policy.exit_minutes_after_open):
        reason = "next_session_exit_deadline_0B"
    elif gap_observations is not None and _completed_close_gap(
            CloseIpoPosition(setup, head.quantity, basis, 0), gap_observations, decided_at) is not None:
        reason = 'adverse_gap_full_exit_completed_bars'
    else:
        return None
    snapshot = {"version": "close_exit_snapshot/v1", "spec_id": spec.spec_id,
        "candidate_package_hash": spec.candidate_package_hash, "account_ref": spec.account_scope.account_ref,
        "execution_run_id": run_id, "entry_proof_id": entry_evidence["proof_id"], "plan": asdict(head.plan),
        "entry_intent_id": root.intent.intent_id, "quantity": head.quantity, "fill_cursor": head.fill_cursor,
        "entry_price_won": basis, "entry_fill_ref": content_id("close_fills", fills),
        "trigger_quote": trigger, "quote": execution_quote, "reason": reason}
    if reason == 'adverse_gap_full_exit_completed_bars':
        snapshot['gap_observations'] = gap_observations
    state = StrategyState(status="open", symbol=event["symbol"], entry_price=basis,
        position_quantity=head.quantity, opened_at=fills[0]["occurred_at"]).to_dict()
    body = {"run_id": run_id, "snapshot_id": content_id("snapshot", snapshot), "symbol": event["symbol"],
        "proposal": "EXIT", "final_action": "EXIT", "quantity": head.quantity,
        "signal_reference_price": execution_quote["price_won"], "required_capital_won": 0,
        "reasons": (reason,), "constraints": (CLOSE_ORDER_POLICY_VERSION,), "state_before": state, "state_after": state,
        "decided_at": decided_at.isoformat()}
    decision = StrategyDecision(decision_id=content_id("decision", body), **body)
    document = json.loads(json.dumps({"snapshot": snapshot, "decision": decision.to_dict()}, allow_nan=False))
    return {**document, "proof_id": content_id("close_exit", document)}, decision, head.plan


def dispatch_close_exit(repository, runtime, execution, spec, plan, *, parameters, event, trigger_quote, quote, recovery, risk,
                        gap_observations=None):
    """Restore frozen management from its native entry and use the existing SELL gate."""
    from .mock_automation_execution import mock_automation_intent_id

    saved = repository.load_close_exit_evidence(runtime.account_ref, close_exit_key(plan))
    scope = {"account_ref": runtime.account_ref, "spec_id": spec.spec_id, "strategy_ref": spec.strategy_ref}
    now = decided_at = runtime.server_now()
    if saved is not None:
        snapshot = saved["snapshot"]
        if (saved["proof_id"] != content_id("close_exit", {key: value for key, value in saved.items() if key != "proof_id"})
                or snapshot["plan"] != asdict(plan) or snapshot["spec_id"] != spec.spec_id
                or snapshot["execution_run_id"] != runtime.run_id or snapshot["account_ref"] != runtime.account_ref):
            raise ValueError("stored CLOSE exit evidence changed")
        raw = {**saved["decision"], "reasons": tuple(saved["decision"]["reasons"]),
               "constraints": tuple(saved["decision"]["constraints"])}
        decision = StrategyDecision(**raw)
        if runtime.load_intent(mock_automation_intent_id(spec.spec_id, decision.decision_id)) is not None:
            return dispatch_mock_automation_decision_from_risk(repository, runtime, decision, recovery, risk, plan=plan, **scope)
        trigger_quote, quote = snapshot["trigger_quote"], snapshot["quote"]
        gap_observations = snapshot.get('gap_observations')
        decided_at = datetime.fromisoformat(decision.decided_at)
    entry = repository.load_close_entry_evidence(runtime.account_ref, plan.plan_id)
    if entry is None:
        raise ValueError("CLOSE original entry evidence is missing")
    root_id = mock_automation_intent_id(spec.spec_id, entry["decision"]["decision_id"])
    root = execution.load(root_id)
    head = execution.load_plan_position(runtime.account_ref, runtime.run_id, event["symbol"], plan)
    if head.plan != plan:
        raise ValueError("CLOSE plan revision changed before management")
    prepared = build_close_exit_evidence(spec, run_id=runtime.run_id, parameters=parameters, event=event,
        entry_evidence=entry, root=root, head=head, fill_events=execution.events(root_id),
        trigger_quote=trigger_quote, quote=quote, decided_at=decided_at, now=now, gap_observations=gap_observations)
    if prepared is None:
        return None, None, None
    evidence, decision, plan = prepared
    gate = assess_mock_automation_decision_from_risk(repository, runtime, decision, recovery, risk, plan=plan, **scope)
    if gate.status is MockAutomationGateStatus.BLOCKED:
        return gate, None, None
    repository.save_close_exit_evidence(runtime.account_ref, close_exit_key(plan), evidence)
    return dispatch_mock_automation_decision_from_risk(repository, runtime, decision, recovery, risk, plan=plan, **scope)
