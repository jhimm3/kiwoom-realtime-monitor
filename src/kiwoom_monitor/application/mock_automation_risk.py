"""Account-scoped risk evidence for one automatic mock execution runtime."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from fractions import Fraction
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from kiwoom_monitor.application.trade_cost_service import DailyTradeCost
from kiwoom_monitor.domain.order_contract import OrderPlanRef, OrderSide, OrderState, PlannedOrderIntent, TERMINAL_ORDER_STATES
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import MockAccountRecovery
from kiwoom_monitor.infrastructure.persistence.execution_repository import (
    AccountExecutionEvent,
    ExecutionRecord,
    execution_broker_legs, execution_remaining_quantity,
)


MOCK_AUTOMATION_RISK_VERSION = "mock_automation_risk_snapshot/v1"
VERIFIED_DAILY_PNL_SOURCE = "account_scoped_fifo_broker_cost/v1"
KST = ZoneInfo("Asia/Seoul")
PROTECTION_EXIT_VERSION = 'mock-protection-exit/v1'
PLANNED_LEADER_STOP_VERSION = "planned-leader-stop/v1"
PLANNED_LEADER_EXPIRED_ENTRY_CANCEL_VERSION = "planned-leader-expired-entry-cancel/v1"
PLANNED_LEADER_PROTECTION_EXIT_VERSION = "planned-leader-protection-exit/v1"


@dataclass(frozen=True)
class PlannedLeaderStop:
    """The sticky, historical stop cut. It cannot authorize today's price or size."""

    plan_id: str
    policy_ref: str
    risk_snapshot_id: str
    risk_reconciliation_revision: int
    observation: Mapping[str, Any]
    stop_loss: Mapping[str, Any]

    def __post_init__(self) -> None:
        if (not isinstance(self.plan_id, str) or not self.plan_id.strip()
                or not isinstance(self.policy_ref, str) or not self.policy_ref.strip()
                or type(self.risk_reconciliation_revision) is not int or self.risk_reconciliation_revision < 1
                or not isinstance(self.observation, Mapping) or not isinstance(self.stop_loss, Mapping)):
            raise ValueError("invalid planned leader stop")
        _require_sha256(self.risk_snapshot_id, "planned leader original Risk")

    def _body(self) -> dict[str, Any]:
        return {"version": PLANNED_LEADER_STOP_VERSION, **asdict(self)}

    @property
    def trigger_id(self) -> str:
        return _sha256(self._body())

    def to_dict(self) -> dict[str, Any]:
        return {**self._body(), "trigger_id": self.trigger_id}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PlannedLeaderStop:
        body = dict(value)
        trigger_id = body.pop("trigger_id", None)
        if body.pop("version", None) != PLANNED_LEADER_STOP_VERSION:
            raise ValueError("unsupported planned leader stop")
        try:
            trigger = cls(**body)
        except TypeError as error:
            raise ValueError("invalid planned leader stop") from error
        if trigger_id != trigger.trigger_id:
            raise ValueError("invalid planned leader stop identity")
        return trigger


@dataclass(frozen=True)
class PlannedLeaderCancelClaim:
    """The current native revision and the acknowledged historical notice."""

    plan: OrderPlanRef
    trigger_id: str
    notice_revision: int
    risk_snapshot_id: str
    risk_reconciliation_revision: int

    def __post_init__(self) -> None:
        if not isinstance(self.plan, OrderPlanRef):
            raise ValueError("planned leader cancellation requires a typed plan")
        _require_sha256(self.trigger_id, "planned leader stop trigger")
        _require_sha256(self.risk_snapshot_id, "planned leader current Risk")
        if any(type(value) is not int or value < 1
               for value in (self.notice_revision, self.risk_reconciliation_revision)):
            raise ValueError("planned leader cancellation requires positive revisions")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PlannedLeaderCancelClaim:
        try:
            body = dict(value)
            body["plan"] = OrderPlanRef(**body["plan"])
            return cls(**body)
        except (KeyError, TypeError) as error:
            raise ValueError("invalid planned leader cancellation claim") from error


@dataclass(frozen=True)
class PlannedLeaderStopCancelRequest:
    """Scope and historical notice selected before the gateway's account refresh."""

    plan: OrderPlanRef
    trigger_id: str
    notice_revision: int

    def claim_from(self, risk: MockAutomationRiskSnapshot) -> PlannedLeaderCancelClaim:
        return PlannedLeaderCancelClaim(
            self.plan, self.trigger_id, self.notice_revision, risk.snapshot_id, risk.reconciliation_revision,
        )


@dataclass(frozen=True)
class PlannedLeaderExpiredEntryCancelClaim:
    plan: OrderPlanRef
    risk_snapshot_id: str
    risk_reconciliation_revision: int
    recovery: MockAccountRecovery
    detailed_quantity_at_cancel: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.plan, OrderPlanRef) or not isinstance(self.recovery, MockAccountRecovery):
            raise ValueError("expired planned entry requires exact plan and broker recovery")
        _require_sha256(self.risk_snapshot_id, "expired planned entry current Risk")
        if type(self.risk_reconciliation_revision) is not int or self.risk_reconciliation_revision < 1:
            raise ValueError("expired planned entry requires a positive Risk revision")
        if type(self.detailed_quantity_at_cancel) is not int or self.detailed_quantity_at_cancel < 0:
            raise ValueError("expired planned entry requires confirmed detailed quantity")

    def to_dict(self) -> dict[str, Any]:
        return {"version": PLANNED_LEADER_EXPIRED_ENTRY_CANCEL_VERSION, "plan": asdict(self.plan),
                "risk_snapshot_id": self.risk_snapshot_id,
                "risk_reconciliation_revision": self.risk_reconciliation_revision,
                **({"detailed_quantity_at_cancel": self.detailed_quantity_at_cancel}
                   if self.detailed_quantity_at_cancel else {})}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], recovery: MockAccountRecovery) -> PlannedLeaderExpiredEntryCancelClaim:
        body = dict(value)
        if body.pop("version", None) != PLANNED_LEADER_EXPIRED_ENTRY_CANCEL_VERSION:
            raise ValueError("unsupported expired planned entry cancellation")
        try:
            body["plan"] = OrderPlanRef(**body["plan"])
            return cls(**body, recovery=recovery)
        except (KeyError, TypeError) as error:
            raise ValueError("invalid expired planned entry cancellation claim") from error


def validate_planned_leader_stop(
    trigger: PlannedLeaderStop, *, spec, leader_config, position_proof, original_risk,
    original_fill_quantity: int,
) -> None:
    """Reproduce the approved original 0B stop against the original Risk and FILL cut."""
    from kiwoom_monitor.application.leader_breakout import (
        evaluate_leader_breakout_trade,
        validate_leader_position_proof,
    )

    context = validate_leader_position_proof(position_proof, leader_config, spec, original_risk.execution_run_id)
    validate_mock_stop_loss_proof(
        trigger.stop_loss, spec, original_risk, leader_config=leader_config, position_proof=position_proof,
    )
    observation = trigger.observation
    if ((trigger.plan_id, trigger.policy_ref) != (position_proof["entry_intent_id"], position_proof["proof_id"])
            or (trigger.risk_snapshot_id, trigger.risk_reconciliation_revision)
            != (original_risk.snapshot_id, original_risk.reconciliation_revision)
            or original_risk.account_ref != spec.account_scope.account_ref
            or original_risk.binding_revision != spec.binding_revision
            or not original_risk.reconciliation_complete or original_risk.submission_unknown_count
            or original_risk.balance_mismatch_count
            or original_fill_quantity != trigger.stop_loss["quantity"]
            or observation.get("approved") is not True or not observation.get("source")
            or not isinstance(observation.get("payload"), Mapping)):
        raise ValueError("planned leader stop scope or original Risk/FILL changed")
    available_at = datetime.fromisoformat(trigger.stop_loss["available_at"])
    decided_at = datetime.fromisoformat(observation["decided_at"])
    age = spec.maximum_data_gap_seconds
    if (not original_risk.account_as_of <= available_at <= decided_at or age is None
            or any(not 0 <= (decided_at - at).total_seconds() <= age for at in (
                original_risk.account_as_of, original_risk.observed_at,
            ))):
        raise ValueError("planned leader stop used unavailable Risk or price")
    raw, _ = evaluate_leader_breakout_trade(
        leader_config.policy, observation["payload"], received_at=datetime.fromisoformat(observation["received_at"]),
        source=observation["source"], decision_time=decided_at, position_open=True, position_context=context,
    )
    if (raw["status"] != "HARD_EXIT" or raw["input_ref"] != trigger.stop_loss["input_ref"]
            or raw["signal_reference_price"] != trigger.stop_loss["price_won"]
            or datetime.fromisoformat(raw["available_at"]) != available_at):
        raise ValueError("planned leader stop did not reproduce")


def validate_planned_leader_current_cut(
    plan: OrderPlanRef, *, spec, run_id: str, symbol: str, risk, plan_position: Mapping[str, Any],
    now: datetime, price: Mapping[str, Any] | None = None,
) -> int:
    """Validate the current quantity and optional fresh quote independently of the trigger."""
    quantity = plan_position["quantity"]
    cursor = plan_position["fill_cursor"]
    age = spec.maximum_data_gap_seconds
    member = plan_position.get('spec_id')
    if member is not None and member != spec.spec_id:
        raise ValueError('planned leader current member is unconfirmed')
    owned = (risk.owned_for_member(member) if member is not None else risk.owned_position_quantities)
    pending = (risk.pending_sells_for_member(member) if member is not None else risk.pending_sell_quantities)
    if ((plan_position["plan_id"], plan_position["policy_ref"], plan_position["revision"],
         plan_position["run_id"], plan_position["symbol"]) != (plan.plan_id, plan.policy_ref,
         plan.expected_revision, run_id, symbol)
            or plan_position.get("version") != "mock_plan_position/v1"
            or type(quantity) is not int or quantity <= 0 or plan_position.get("unreconciled_sell_quantity")
            or type(cursor) is not int or not 0 < cursor <= risk.execution_event_high_watermark
            or (risk.account_ref, risk.execution_run_id, risk.binding_revision)
            != (spec.account_scope.account_ref, run_id, spec.binding_revision)
            or not risk.reconciliation_complete or risk.submission_unknown_count or risk.balance_mismatch_count
            or pending or dict(owned) != {symbol: quantity}
            or age is None or any(not 0 <= (now - at).total_seconds() <= age for at in (
                risk.observed_at, risk.account_as_of,
            ))):
        raise ValueError("planned leader current quantity or Risk is unconfirmed")
    if price is not None and (
        type(price.get("price_won")) is not int or price["price_won"] <= 0
        or price.get("durable") is not False or price.get("market_status") != "TRADABLE"
        or not price.get("input_ref") or not str(price["input_ref"]).startswith("0B:")
        or not isinstance(price.get("available_at"), datetime) or price["available_at"].tzinfo is None
        or price["available_at"] > risk.observed_at or not 0 <= (now - price["available_at"]).total_seconds() <= age
    ):
        raise ValueError("planned leader current execution quote is unconfirmed")
    return quantity


def _planned_leader_execution_quote(observation, *, spec, run_id, leader_config, position_proof):
    from .leader_breakout import evaluate_leader_breakout_trade, validate_leader_position_proof

    context = validate_leader_position_proof(position_proof, leader_config, spec, run_id)
    if (observation.get("approved") is not True or not observation.get("source")
            or not isinstance(observation.get("payload"), Mapping)
            or observation["payload"].get("market", "KRX") != spec.supported_venue):
        raise ValueError("planned leader protection requires an approved raw 0B execution quote")
    received_at = datetime.fromisoformat(observation["received_at"])
    decided_at = datetime.fromisoformat(observation["decided_at"])
    if received_at.tzinfo is None or decided_at.tzinfo is None:
        raise ValueError("planned leader execution quote has no time zone")
    raw, _ = evaluate_leader_breakout_trade(
        leader_config.policy, observation["payload"], received_at=received_at, source=observation["source"],
        decision_time=decided_at, position_open=True, position_context=context,
    )
    if raw["status"] not in {"HARD_EXIT", "INVALIDATED", "HOLD"}:
        raise ValueError("planned leader execution quote is unavailable")
    return {"price_won": raw["signal_reference_price"], "available_at": received_at,
            "observed_at": datetime.fromisoformat(raw["observed_at"]),
            "input_ref": raw["input_ref"], "durable": False, "market_status": "TRADABLE"}


def planned_leader_root_confirmation_time(root, events):
    """Last material FILL followed by a confirmed terminal cut, not a later reread."""
    if (root.state not in {OrderState.FILLED, OrderState.CANCELLED}
            or not root.last_broker_as_of or not root.detailed_filled_quantity
            or root.detailed_filled_quantity != root.filled_quantity
            or root.broker_reported_filled_quantity != root.detailed_filled_quantity):
        raise ValueError('planned_leader_material_confirmation_unavailable')
    fills = [(index, row) for index, row in enumerate(events)
             if row['event_type'] == 'FILL' and row['broker_execution_id']]
    if (not fills or sum(row['quantity'] for _, row in fills) != root.detailed_filled_quantity
            or tuple(row['broker_execution_id'] for _, row in fills) != root.fill_ids):
        raise ValueError('planned_leader_material_fills_changed')
    last_fill = fills[-1][0]
    terminal = next((row for row in events[last_fill + 1:] if row['event_type'] == 'BROKER_RECONCILED'
        and row['state'] == root.state.value and row.get('broker_as_of')), None)
    if terminal is None:
        raise ValueError('planned_leader_material_terminal_unconfirmed')
    at = max(datetime.fromisoformat(terminal['broker_as_of']), datetime.fromisoformat(fills[-1][1]['received_at']))
    if at > root.last_broker_as_of:
        raise ValueError('planned_leader_material_confirmation_from_future')
    return at


def prepare_planned_leader_protection_exit(
    spec, *, run_id, control_revision, notice, plan, observation, risk, plan_position, leader_config,
    position_proof, confirmed_root_state, confirmed_root_at, now, reservation_claimed=False,
):
    """Freeze a candidate. The native owner must prove terminal BUY and both cuts before transport."""
    from .breakout_strategy import StrategyState
    from .mock_automation_session_clock import content_id

    trigger = PlannedLeaderStop.from_dict(notice["planned_leader_stop"])
    if (not isinstance(plan, OrderPlanRef) or spec.unfilled_execution_policy is None
            or confirmed_root_state not in {"CANCELLED", "FILLED"} or confirmed_root_at.tzinfo is None
            or notice.get("state") != "PENDING" or notice.get("order_authorized") is not False
            or notice.get("transport_state") != "OFF"
            or (notice.get("spec_id"), notice.get("account_ref"), notice.get("execution_run_id"))
            != (spec.spec_id, spec.account_scope.account_ref, run_id)
            or notice.get("input_ref") != trigger.stop_loss["input_ref"]
            or (plan.plan_id, plan.policy_ref) != (trigger.plan_id, trigger.policy_ref)
            or (plan.plan_id, plan.policy_ref) != (position_proof["entry_intent_id"], position_proof["proof_id"])):
        raise ValueError("planned leader protection request changed")
    price = _planned_leader_execution_quote(
        observation, spec=spec, run_id=run_id, leader_config=leader_config, position_proof=position_proof,
    )
    if (price["available_at"] <= datetime.fromisoformat(trigger.stop_loss["available_at"])
            or price["available_at"] < confirmed_root_at
            or datetime.fromisoformat(observation["decided_at"]) > risk.observed_at):
        raise ValueError("planned leader protection requires an independent later execution quote")
    age = leader_config.max_data_age_seconds
    if age is None or any(not 0 <= (now - price[field]).total_seconds() <= age for field in ("observed_at", "available_at")):
        raise ValueError("planned leader execution quote is stale at the native claim")
    symbol = position_proof["context"]["symbol"]
    quantity = validate_planned_leader_current_cut(
        replace(plan, expected_revision=plan.expected_revision + 1) if reservation_claimed else plan,
        spec=spec, run_id=run_id, symbol=symbol, risk=risk, plan_position=plan_position, now=now, price=price,
    )
    age = spec.unfilled_execution_policy.max_snapshot_age_seconds
    if any(not 0 <= (now - at).total_seconds() <= age for at in (
        risk.observed_at, risk.account_as_of, price["available_at"],
    )):
        raise ValueError("planned leader protection cut is stale")
    state = StrategyState(
        status="open", symbol=symbol, entry_price=dict(risk.owned_entry_prices).get(symbol),
        position_quantity=quantity, opened_at=position_proof["entry_decided_at"],
    ).to_dict()
    snapshot = {"version": PLANNED_LEADER_PROTECTION_EXIT_VERSION, "spec_id": spec.spec_id,
                "execution_run_id": run_id, "account_ref": spec.account_scope.account_ref,
                "control_revision": control_revision, "notice_revision": notice["revision"],
                "trigger_id": trigger.trigger_id, "plan": asdict(plan), "observation": dict(observation),
                "confirmed_root_state": confirmed_root_state, "confirmed_root_at": confirmed_root_at.isoformat(),
                "risk_snapshot_id": risk.snapshot_id, "risk_reconciliation_revision": risk.reconciliation_revision}
    body = {"run_id": run_id, "snapshot_id": content_id("protection_snapshot", snapshot), "symbol": symbol,
            "proposal": "EXIT", "final_action": "EXIT", "quantity": quantity,
            "signal_reference_price": price["price_won"], "required_capital_won": 0,
            "reasons": ("native_risk:SYMBOL_STOP_LOSS_REACHED",), "constraints": (PROTECTION_EXIT_VERSION,),
            "state_before": state, "state_after": state, "decided_at": risk.observed_at.isoformat()}
    workflow = {"snapshot": snapshot, "decision": {**body, "decision_id": content_id("decision", body)}}
    return json.loads(json.dumps({**workflow, "workflow_id": content_id("protection_exit", workflow)}))


def read_planned_leader_protection_checkpoint(workflow, notice, spec, run_id):
    """Decode historical readback only; never grant new order authority."""
    from .breakout_strategy import StrategyDecision
    from .mock_automation_session_clock import content_id

    snapshot, raw = workflow["snapshot"], workflow["decision"]
    plan = OrderPlanRef(**snapshot["plan"])
    trigger = PlannedLeaderStop.from_dict(notice["planned_leader_stop"])
    decision = StrategyDecision(**{**raw, "reasons": tuple(raw["reasons"]), "constraints": tuple(raw["constraints"])})
    body = {key: value for key, value in raw.items() if key != "decision_id"}
    if (set(workflow) != {"snapshot", "decision", "workflow_id"}
            or workflow["workflow_id"] != content_id("protection_exit", {"snapshot": snapshot, "decision": raw})
            or (snapshot["version"], snapshot["spec_id"], snapshot["account_ref"], snapshot["execution_run_id"])
            != (PLANNED_LEADER_PROTECTION_EXIT_VERSION, spec.spec_id, spec.account_scope.account_ref, run_id)
            or (snapshot["notice_revision"], snapshot["trigger_id"], plan.plan_id, plan.policy_ref)
            != (notice["revision"], trigger.trigger_id, trigger.plan_id, trigger.policy_ref)
            or (decision.snapshot_id, decision.decision_id) != (
                content_id("protection_snapshot", snapshot), content_id("decision", body),
            )
            or decision.run_id != run_id or decision.proposal != "EXIT" or decision.final_action != "EXIT"
            or decision.constraints != (PROTECTION_EXIT_VERSION,) or decision.symbol != trigger.stop_loss["symbol"]
            or type(decision.quantity) is not int or decision.quantity <= 0
            or type(decision.signal_reference_price) is not int or decision.signal_reference_price <= 0):
        raise ValueError("invalid_planned_leader_protection_checkpoint")
    return plan, decision


def planned_review_resume_id(notice) -> str:
    from .mock_automation_session_clock import content_id

    return content_id("planned_protection_resume", {
        **{key: notice[key] for key in ("account_ref", "spec_id", "execution_run_id", "revision",
                                      "resolved_risk_snapshot_id", "resolved_risk_revision",
                                      "resume_from_control_revision")},
        **({"workflow_id": notice["planned_leader_protection_exit"]["workflow_id"]}
           if "planned_leader_protection_exit" in notice else {"zero_fill_root": notice["planned_zero_fill_root"]}),
    })


def is_planned_zero_fill_reconciliation_review(notice) -> bool:
    trigger = notice.get("risk_trigger") or {}
    return (trigger.get("disposition") == "RECONCILE_REQUIRED" and "stop_loss" not in trigger
            and not any(key in notice for key in (
                "planned_leader_stop", "leader_stop_observation", "planned_leader_protection_exit",
            )))


def planned_zero_fill_root_receipt(root, gate, pin, events, *, account_ref, run_id, spec_id):
    """Read an immutable zero-fill cancellation receipt. It cannot authorize a SELL."""
    if root is None or not isinstance(root.intent, PlannedOrderIntent) or gate is None:
        raise ValueError("planned_resume_zero_fill_root_unconfirmed")
    intent = root.intent
    if ((intent.account_ref, intent.run_id, intent.environment, intent.side, intent.policy_version,
         intent.intent_id, intent.plan.plan_id, intent.plan.policy_ref)
            != (account_ref, run_id, "mock", OrderSide.BUY, "leader_execution_adapter/v1",
                pin["entry_intent_id"], pin["entry_intent_id"], pin["proof_id"])
            or root.state is not OrderState.CANCELLED or not root.broker_order_id or root.last_broker_as_of is None
            or root.filled_quantity or root.detailed_filled_quantity or root.broker_reported_filled_quantity or root.fill_ids
            or gate.status.value != "APPROVED_FOR_SINGLE_SUBMISSION"
            or (gate.account_ref, gate.execution_run_id, gate.spec_id, gate.intent_id, gate.strategy_decision_id,
                gate.action, gate.plan) != (account_ref, run_id, spec_id, intent.intent_id, intent.decision_id, "ENTER", intent.plan)):
        raise ValueError("planned_resume_zero_fill_root_unconfirmed")
    cancel = [(index, event) for index, event in enumerate(events) if event["event_type"] == "CANCEL_STARTED"]
    if len(cancel) != 1 or any(event["event_type"] in {"FILL", "BROKER_FILL_AGGREGATE"} for event in events):
        raise ValueError("planned_resume_zero_fill_history_unconfirmed")
    index, started = cancel[0]
    claim = started.get("planned_leader_cancel_claim") or {}
    plan = claim.get("plan") or {}
    terminal = next((event for event in events[index + 1:] if event["event_type"] == "BROKER_RECONCILED"
                     and event["state"] == "CANCELLED" and event["broker_order_id"] == root.broker_order_id), None)
    if (claim.get("version") != PLANNED_LEADER_EXPIRED_ENTRY_CANCEL_VERSION
            or claim.get("detailed_quantity_at_cancel", 0) != 0 or started["state"] != "CANCEL_PENDING"
            or (plan.get("plan_id"), plan.get("policy_ref"), plan.get("expected_revision"))
            != (intent.plan.plan_id, intent.plan.policy_ref, intent.plan.expected_revision + 1)
            or terminal is None or datetime.fromisoformat(terminal["broker_as_of"]) > root.last_broker_as_of):
        raise ValueError("planned_resume_zero_fill_cancel_unconfirmed")
    return {"intent_id": intent.intent_id, "plan_id": intent.plan.plan_id, "policy_ref": intent.plan.policy_ref,
            "approved_gate_id": gate.gate_id, "broker_order_id": root.broker_order_id,
            "cancel_event_id": started["event_id"], "terminal_event_id": terminal["event_id"]}


def gross_price_stop_reached(price, entry, stop_loss_bps):
    """Shared gross-price threshold; provenance is checked by each caller."""
    return price * 10000 <= entry * (10000 - stop_loss_bps)


def validate_mock_stop_loss_proof(proof, spec, risk=None, *, stop_loss_bps=None, leader_config=None, position_proof=None,
                                  ipo_policy=None, ipo_candidate=None):
    """Bind a sticky gross-price stop to the admitted policy and native fill basis."""
    if isinstance(proof, Mapping) and proof.get('version') == 'ipo_structure_stop/v1':
        required = {'version', 'symbol', 'quantity', 'entry_price_won', 'price_won', 'available_at', 'input_ref',
                    'breakout_level', 'candidate_event_id', 'candidate_snapshot_id', 'spec_id', 'package_hash',
                    'execution_run_id'}
        if (set(proof) != required or getattr(ipo_policy, 'variant', None) != 'IPO_BREAKOUT'
                or (proof['spec_id'], proof['package_hash']) != (spec.spec_id, spec.candidate_package_hash)
                or any(type(proof.get(key)) is not int or proof[key] <= 0 for key in
                       ('quantity', 'entry_price_won', 'price_won', 'breakout_level'))
                or proof['price_won'] >= proof['breakout_level']
                or any(not isinstance(proof[key], str) or not proof[key] for key in
                       ('symbol', 'candidate_event_id', 'candidate_snapshot_id', 'execution_run_id', 'input_ref'))
                or not proof['input_ref'].startswith('collector-trade:') or len(proof['input_ref']) > 512
                or datetime.fromisoformat(proof['available_at']).tzinfo is None):
            raise ValueError('ipo_structure_stop_proof_invalid')
        if ipo_candidate is not None and (
                (proof['symbol'], proof['candidate_event_id'], proof['candidate_snapshot_id'], proof['breakout_level'])
                != (ipo_candidate['symbol'], ipo_candidate['event_id'], ipo_candidate['snapshot_id'],
                    ipo_candidate['close_ipo_entry_setup']['breakout_level'])
                or ipo_candidate['close_ipo_entry_setup']['policy'] != ipo_policy.to_dict()):
            raise ValueError('ipo_structure_stop_original_setup_changed')
        if risk is not None and (
                dict(risk.owned_position_quantities).get(proof['symbol']) != proof['quantity']
                or dict(risk.owned_entry_prices).get(proof['symbol']) != proof['entry_price_won']
                or risk.execution_run_id != proof['execution_run_id']):
            raise ValueError('ipo_structure_stop_native_basis_changed')
        return
    if isinstance(proof, Mapping) and proof.get("version") == "leader_absolute_stop/v1":
        from .leader_breakout import _id, LeaderBreakoutContext, validate_leader_position_proof
        required = {"version", "symbol", "quantity", "entry_price_won", "price_won", "available_at", "input_ref",
                    "hard_stop_price", "position_proof", "rules_hash", "package_hash"}
        if (set(proof) != required or proof["package_hash"] != spec.candidate_package_hash
                or any(type(proof.get(k)) is not int or proof[k] <= 0 for k in
                       ("quantity", "entry_price_won", "price_won", "hard_stop_price"))
                or proof["price_won"] > proof["hard_stop_price"]
                or not isinstance(proof["input_ref"], str) or not proof["input_ref"].startswith("0B:")
                or datetime.fromisoformat(proof["available_at"]).tzinfo is None):
            raise ValueError("leader_absolute_stop_proof_invalid")
        position = proof["position_proof"]
        context = LeaderBreakoutContext(**position["context"])
        body = {k: v for k, v in position.items() if k != "proof_id"}
        if (position.get("proof_id") != _id("leader-position-proof/v1", body)
                or position.get("spec_id") != spec.spec_id or position.get("package_hash") != spec.candidate_package_hash
                or proof["symbol"] != context.symbol or proof["hard_stop_price"] != context.hard_stop_price
                or proof["rules_hash"] != position.get("rules_hash")):
            raise ValueError("leader_stop_position_binding_invalid")
        if position_proof is not None and position != position_proof:
            raise ValueError("leader_stop_pinned_context_changed")
        if leader_config is not None:
            validate_leader_position_proof(position, leader_config, spec, position["run_id"])
        if risk is not None and (dict(risk.owned_position_quantities).get(proof["symbol"]) != proof["quantity"]
                or dict(risk.owned_entry_prices).get(proof["symbol"]) != proof["entry_price_won"]
                or risk.execution_run_id != position["run_id"]):
            raise ValueError("leader_stop_native_basis_changed")
        return
    if leader_config is not None:
        raise ValueError("leader execution cannot use a legacy percentage stop proof")
    if (not isinstance(proof, Mapping) or set(proof) != {
            'symbol', 'quantity', 'entry_price_won', 'stop_loss_bps',
            'price_won', 'available_at', 'input_ref'}
            or any(type(proof.get(k)) is not int or proof[k] <= 0
                   for k in ('quantity', 'entry_price_won', 'stop_loss_bps', 'price_won'))
            or proof['stop_loss_bps'] >= 10000
            or stop_loss_bps is not None and proof['stop_loss_bps'] != stop_loss_bps
            or not isinstance(proof['input_ref'], str) or not proof['input_ref'].startswith('collector-trade:')
            or len(proof['input_ref']) > 512
            or not gross_price_stop_reached(proof['price_won'], proof['entry_price_won'], proof['stop_loss_bps'])
            or datetime.fromisoformat(proof['available_at']).tzinfo is None):
        raise ValueError('mock_stop_loss_proof_invalid')
    if risk is not None and (
            dict(risk.owned_position_quantities).get(proof['symbol']) != proof['quantity']
            or dict(risk.owned_entry_prices).get(proof['symbol']) != proof['entry_price_won']):
        raise ValueError('mock_stop_loss_native_basis_changed')


def mock_automation_stop_loss_trigger(spec, risk, *, execution_run_id, now, price, account_positions, stop_loss_bps,
                                      ipo_policy=None, ipo_candidate=None):
    """Evaluate an admitted stop from ACKed fill basis and borrowed Collector RAM."""
    basis = dict(risk.owned_entry_prices)
    owned = dict(risk.owned_position_quantities)
    symbol = price.get('symbol') if isinstance(price, Mapping) else None
    structure_stop = (getattr(ipo_policy, 'variant', None) == 'IPO_BREAKOUT' and ipo_candidate is not None
                      and symbol == ipo_candidate['symbol'] and type(price.get('price_won')) is int
                      and price['price_won'] < ipo_candidate['close_ipo_entry_setup']['breakout_level'])
    age = spec.maximum_data_gap_seconds
    if spec.unfilled_execution_policy is not None:
        age = min(age, spec.unfilled_execution_policy.max_snapshot_age_seconds)
    if (symbol not in basis or symbol not in owned or owned.get(symbol) != account_positions.get(symbol)
            or price.get('durable') is not False or price.get('market_status') != 'TRADABLE'
            or type(price.get('price_won')) is not int or price['price_won'] <= 0
            or not isinstance(price.get('available_at'), datetime) or price['available_at'].tzinfo is None
            or not 0 <= (now-price['available_at']).total_seconds() <= age
            or not (structure_stop or gross_price_stop_reached(price['price_won'], basis[symbol], stop_loss_bps))):
        return None
    proof = {'symbol': symbol, 'quantity': owned[symbol], 'entry_price_won': basis[symbol],
        'stop_loss_bps': stop_loss_bps, 'price_won': price['price_won'],
        'available_at': price['available_at'].isoformat(), 'input_ref': price['input_ref']}
    if structure_stop:
        proof.pop('stop_loss_bps')
        proof.update(version='ipo_structure_stop/v1',
            breakout_level=ipo_candidate['close_ipo_entry_setup']['breakout_level'],
            candidate_event_id=ipo_candidate['event_id'], candidate_snapshot_id=ipo_candidate['snapshot_id'],
            spec_id=spec.spec_id, package_hash=spec.candidate_package_hash, execution_run_id=execution_run_id)
    validate_mock_stop_loss_proof(proof, spec, risk, stop_loss_bps=stop_loss_bps,
                                  ipo_policy=ipo_policy, ipo_candidate=ipo_candidate)
    trigger = mock_automation_protection_trigger(spec, risk, execution_run_id=execution_run_id,
        now=now, account_positions=account_positions)
    if trigger is None:
        trigger = {'version':'mock-native-risk-protection/v1', 'account_ref':risk.account_ref,
            'execution_run_id':execution_run_id, 'binding_revision':risk.binding_revision,
            'snapshot_id':risk.snapshot_id, 'reconciliation_revision':risk.reconciliation_revision,
            'observed_at':risk.observed_at.isoformat(), 'disposition':'EXIT_REQUIRED', 'reasons':[],
            'source':'acknowledged_account_monitor'}
    trigger = {**trigger, 'reasons':[*trigger['reasons'], 'SYMBOL_STOP_LOSS_REACHED'], 'stop_loss':proof}
    trigger['cause_signature'] = _sha256({'stop_loss':proof, 'disposition':trigger['disposition'],
        'reasons':trigger['reasons']})
    return trigger


def _protective_exit_trigger_valid(trigger, spec, risk=None):
    reasons = trigger.get('reasons', [])
    if (trigger.get('disposition') != 'EXIT_REQUIRED'
            or reasons not in (['DAILY_LOSS_LIMIT_REACHED'], ['SYMBOL_STOP_LOSS_REACHED'],
                              ['DAILY_LOSS_LIMIT_REACHED', 'SYMBOL_STOP_LOSS_REACHED'])
            or trigger.get('source') != 'acknowledged_account_monitor'):
        return False
    if 'SYMBOL_STOP_LOSS_REACHED' in reasons:
        validate_mock_stop_loss_proof(trigger.get('stop_loss'), spec, risk)
    elif 'stop_loss' in trigger:
        return False
    return True


def prepare_mock_protection_exit(spec, *, run_id, control_revision, notice, risk, state, price):
    """Freeze one native loss-protection EXIT, without submitting or estimating EV."""
    from kiwoom_monitor.application.mock_automation_session_clock import content_id
    trigger = notice.get('risk_trigger', {})
    if (spec.unfilled_execution_policy is None or notice.get('state') != 'PENDING'
            or not _protective_exit_trigger_valid(trigger, spec, risk)
            or state.status != 'open' or dict(risk.owned_position_quantities) != {state.symbol: state.position_quantity}
            or price['available_at'] > risk.observed_at):
        raise ValueError('protection_exit_native_loss_or_owned_position_unverified')
    snapshot = {'version': PROTECTION_EXIT_VERSION, 'spec_id': spec.spec_id, 'execution_run_id': run_id,
        'account_ref': spec.account_scope.account_ref, 'control_revision': control_revision,
        'request_revision': notice['revision'], 'request_input_ref': notice['input_ref'],
        'request_trigger': trigger, 'risk_snapshot_id': risk.snapshot_id,
        'risk_reconciliation_revision': risk.reconciliation_revision,
        'price_input_ref': price['input_ref'], 'price_available_at': price['available_at'].isoformat(),
        'price_won': price['price_won']}
    body = {'run_id': run_id, 'snapshot_id': content_id('protection_snapshot', snapshot), 'symbol': state.symbol,
        'proposal':'EXIT','final_action':'EXIT','quantity':state.position_quantity,
        'signal_reference_price':price['price_won'],'required_capital_won':0,
        'reasons':tuple('native_risk:'+reason for reason in trigger['reasons']), 'constraints':(PROTECTION_EXIT_VERSION,),
        'state_before':state.to_dict(),'state_after':state.to_dict(),'decided_at':risk.observed_at.isoformat()}
    workflow = {'snapshot': snapshot, 'decision': {**body,'decision_id':content_id('decision',body)}}
    return json.loads(json.dumps({**workflow, 'workflow_id':content_id('protection_exit',workflow)}))


def validate_mock_protection_exit(value, spec, run_id):
    from kiwoom_monitor.application.breakout_strategy import StrategyDecision
    from kiwoom_monitor.application.mock_automation_session_clock import content_id
    if (not isinstance(value, Mapping) or set(value) != {'snapshot','decision','workflow_id'}
            or value['workflow_id'] != content_id('protection_exit', {k:v for k,v in value.items() if k!='workflow_id'})):
        raise ValueError('invalid_protection_exit_workflow')
    snapshot=value['snapshot'];raw=dict(value['decision']);body={k:v for k,v in raw.items() if k!='decision_id'}
    trigger=snapshot.get('request_trigger',{})
    if (spec.unfilled_execution_policy is None or snapshot.get('version') != PROTECTION_EXIT_VERSION
            or snapshot.get('spec_id') != spec.spec_id or snapshot.get('execution_run_id') != run_id
            or snapshot.get('account_ref') != spec.account_scope.account_ref
            or type(snapshot.get('control_revision')) is not int or snapshot['control_revision'] <= 0
            or type(snapshot.get('request_revision')) is not int or snapshot['request_revision'] <= 0
            or not _protective_exit_trigger_valid(trigger, spec)
            or trigger.get('account_ref') != snapshot['account_ref'] or trigger.get('execution_run_id') != run_id
            or trigger.get('binding_revision') != spec.binding_revision
            or raw.get('decision_id') != content_id('decision',body)
            or raw.get('snapshot_id') != content_id('protection_snapshot',snapshot)
            or raw.get('run_id') != run_id or raw.get('final_action') != 'EXIT' or raw.get('proposal') != 'EXIT'
            or raw.get('required_capital_won') != 0 or raw.get('signal_reference_price') != snapshot.get('price_won')
            or tuple(raw.get('constraints',())) != (PROTECTION_EXIT_VERSION,)
            or tuple(raw.get('reasons',())) != tuple('native_risk:'+reason for reason in trigger['reasons'])
            or raw.get('state_before') != raw.get('state_after')
            or raw.get('state_before',{}).get('symbol') != raw.get('symbol')
            or raw.get('state_before',{}).get('position_quantity') != raw.get('quantity')
            or type(raw.get('quantity')) is not int or raw['quantity'] <= 0):
        raise ValueError('protection_exit_scope_or_fixed_decision_mismatch')
    at=datetime.fromisoformat(snapshot['price_available_at']);cut=datetime.fromisoformat(raw['decided_at'])
    if at.tzinfo is None or cut.tzinfo is None or at > cut or not snapshot['price_input_ref']:
        raise ValueError('protection_exit_price_from_future_or_missing')
    if 'stop_loss' in trigger and any(snapshot[k] != trigger['stop_loss'][p]
            for k,p in (('price_won','price_won'),('price_input_ref','input_ref'),('price_available_at','available_at'))):
        raise ValueError('protection_exit_stop_price_changed')
    if 'stop_loss' in trigger and (
            raw['symbol'] != trigger['stop_loss']['symbol'] or raw['quantity'] != trigger['stop_loss']['quantity']
            or raw['state_before'].get('entry_price') != trigger['stop_loss']['entry_price_won']):
        raise ValueError('protection_exit_stop_position_changed')
    raw['reasons']=tuple(raw['reasons']);raw['constraints']=tuple(raw['constraints'])
    return StrategyDecision(**raw)


def protection_limit_assessment(spec, risk, now, *, daily_loss_verified):
    """Shared arithmetic only; callers retain origin and account authority."""
    reasons = []
    gap = spec.maximum_data_gap_seconds
    if gap is None or any(not 0 <= (now - at).total_seconds() <= gap
                          for at in (risk.observed_at, risk.account_as_of)):
        reasons.append("NATIVE_RISK_CUT_NOT_FRESH")
    if not risk.reconciliation_complete:
        reasons.append("RECONCILIATION_REQUIRED")
    if risk.submission_unknown_count:
        reasons.append("UNKNOWN_EXPOSURE")
    if risk.balance_mismatch_count:
        reasons.append("BALANCE_MISMATCH")
    if gap is not None and risk.data_gap_seconds is not None and risk.data_gap_seconds > gap:
        reasons.append("DATA_GAP_LIMIT_REACHED")
    if spec.maximum_reconnect_count is not None and risk.reconnect_count > spec.maximum_reconnect_count:
        reasons.append("RECONNECT_LIMIT_REACHED")
    if (spec.maximum_daily_loss_won is not None and risk.daily_net_pnl_won is not None
            and daily_loss_verified
            and risk.daily_net_pnl_won <= -spec.maximum_daily_loss_won):
        reasons.append("DAILY_LOSS_LIMIT_REACHED")
    disposition = "EXIT_REQUIRED" if reasons == ["DAILY_LOSS_LIMIT_REACHED"] else "RECONCILE_REQUIRED"
    return {"reasons": reasons, "disposition": disposition if reasons else None}


def mock_automation_protection_trigger(spec, risk, *, execution_run_id, now, account_positions=None):
    """Classify ACKed native Risk using admitted limits, without order authority.

    Unknown execution/balance is reconciled first. A verified daily loss breach
    requests protection independently of EV; missing costs alone cannot block EXIT.
    """
    if (now.tzinfo is None or risk.account_ref != spec.account_scope.account_ref
            or risk.execution_run_id != execution_run_id
            or risk.binding_revision != spec.binding_revision or risk.data_path != spec.data_path):
        raise ValueError("mock_protection_native_risk_scope_mismatch")
    assessment = protection_limit_assessment(spec, risk, now,
        daily_loss_verified=risk.daily_net_pnl_source == VERIFIED_DAILY_PNL_SOURCE)
    reasons, disposition = assessment["reasons"], assessment["disposition"]
    if not reasons:
        return None
    cause = {"reasons": reasons, "disposition": disposition,
             "owned": risk.owned_position_quantities, "pending_sell": risk.pending_sell_quantities,
             "unknown": risk.submission_unknown_count, "balance_mismatch": bool(risk.balance_mismatch_count),
             "account_positions": sorted((account_positions or {}).items())}
    signature = hashlib.sha256(json.dumps(cause, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"version": "mock-native-risk-protection/v1", "account_ref": risk.account_ref,
            "execution_run_id": execution_run_id, "binding_revision": risk.binding_revision,
            "snapshot_id": risk.snapshot_id, "reconciliation_revision": risk.reconciliation_revision,
            "observed_at": risk.observed_at.isoformat(), "disposition": disposition,
            "reasons": reasons, "cause_signature": signature, "source": "acknowledged_account_monitor"}


@dataclass(frozen=True)
class MockAutomationRiskSnapshot:
    snapshot_id: str
    version: str
    account_ref: str
    execution_run_id: str
    binding_revision: int
    trading_date: date
    observed_at: datetime
    broker_query_started_at: datetime
    broker_query_completed_at: datetime
    account_as_of: datetime
    account_revision: str
    reconciliation_revision: int
    execution_event_high_watermark: int
    cost_evidence_revision: str
    daily_net_pnl_won: int | None
    daily_net_pnl_source: str | None
    cost_complete: bool
    missing_cost_reasons: tuple[str, ...]
    reconciliation_complete: bool
    data_path: str
    data_gap_seconds: int | None
    submission_unknown_count: int
    reconnect_count: int
    balance_mismatch_count: int
    owned_position_quantities: tuple[tuple[str, int], ...]
    pending_sell_quantities: tuple[tuple[str, int], ...]
    owned_entry_prices: tuple[tuple[str, int], ...] = ()
    member_attribution: str | None = None
    member_positions: tuple[tuple[str, str, int, int], ...] = ()
    member_pending_sells: tuple[tuple[str, str, int], ...] = ()

    def __post_init__(self) -> None:
        if self.version != MOCK_AUTOMATION_RISK_VERSION:
            raise ValueError("unsupported mock automation risk snapshot")
        for name in ("snapshot_id", "account_ref", "execution_run_id", "account_revision"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        _require_sha256(self.snapshot_id, "snapshot_id")
        _require_sha256(self.account_revision, "account_revision")
        _require_sha256(self.cost_evidence_revision, "cost_evidence_revision")
        for name in ("observed_at", "broker_query_started_at", "broker_query_completed_at", "account_as_of"):
            _require_aware(getattr(self, name), name)
        if self.broker_query_started_at > self.broker_query_completed_at:
            raise ValueError("broker query interval is invalid")
        if self.observed_at < self.broker_query_completed_at:
            raise ValueError("risk observation predates its broker query")
        if self.trading_date != self.observed_at.astimezone(KST).date():
            raise ValueError("risk trading_date must use the KST observation date")
        for name in (
            "binding_revision", "reconciliation_revision", "execution_event_high_watermark",
            "submission_unknown_count", "reconnect_count", "balance_mismatch_count",
        ):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.binding_revision <= 0 or self.reconciliation_revision <= 0:
            raise ValueError("binding and reconciliation revisions must be positive")
        if self.data_path not in {"nas", "direct"}:
            raise ValueError("data_path must be nas or direct")
        if self.data_gap_seconds is not None and self.data_gap_seconds < 0:
            raise ValueError("data_gap_seconds must be non-negative or unknown")
        if self.daily_net_pnl_won is not None and type(self.daily_net_pnl_won) is not int:
            raise ValueError("daily_net_pnl_won must be an integer or unknown")
        if self.cost_complete != (not self.missing_cost_reasons):
            raise ValueError("cost completeness and missing reasons disagree")
        if self.daily_net_pnl_won is not None and self.daily_net_pnl_source != VERIFIED_DAILY_PNL_SOURCE:
            raise ValueError("known daily PnL requires the verified source")
        for values in (self.owned_position_quantities, self.pending_sell_quantities):
            if any(not symbol or quantity < 0 for symbol, quantity in values):
                raise ValueError("position quantities must be non-negative")
        if (len(dict(self.owned_entry_prices)) != len(self.owned_entry_prices)
                or any(symbol not in dict(self.owned_position_quantities) or type(price) is not int or price <= 0
                       for symbol, price in self.owned_entry_prices)):
            raise ValueError('invalid owned fill entry prices')
        if self.member_attribution not in {None, 'COMPLETE', 'INCOMPLETE'}:
            raise ValueError('invalid member attribution state')
        if self.member_attribution is None and (self.member_positions or self.member_pending_sells):
            raise ValueError('member facts require their attribution state')
        for rows, width in ((self.member_positions, 4), (self.member_pending_sells, 3)):
            keys = set()
            if type(rows) is not tuple:
                raise ValueError('member facts must be immutable')
            for row in rows:
                if (type(row) is not tuple or len(row) != width or not _valid_member_spec_id(row[0])
                        or not isinstance(row[1], str) or not row[1]
                        or any(type(number) is not int or number <= 0 for number in row[2:])
                        or row[:2] in keys):
                    raise ValueError('invalid member position facts')
                keys.add(row[:2])
        if self.member_attribution == 'COMPLETE':
            for rows, aggregate in ((self.member_positions, self.owned_position_quantities),
                                    (self.member_pending_sells, self.pending_sell_quantities)):
                totals = {}
                for row in rows:
                    totals[row[1]] = totals.get(row[1], 0)+row[2]
                if totals != dict(aggregate):
                    raise ValueError('complete member facts do not match their run totals')

    def _require_member_attribution(self) -> None:
        if self.member_attribution == 'INCOMPLETE' or (self.member_attribution is None
                and (self.owned_position_quantities or self.pending_sell_quantities)):
            raise ValueError('native member attribution is unavailable or incomplete')

    def owned_for_member(self, spec_id: str) -> tuple[tuple[str, int], ...]:
        self._require_member_attribution()
        return tuple((symbol, quantity) for member, symbol, quantity, price in self.member_positions if member == spec_id)

    def pending_sells_for_member(self, spec_id: str) -> tuple[tuple[str, int], ...]:
        self._require_member_attribution()
        return tuple((symbol, quantity) for member, symbol, quantity in self.member_pending_sells if member == spec_id)

    def entry_prices_for_member(self, spec_id: str) -> tuple[tuple[str, int], ...]:
        self._require_member_attribution()
        return tuple((symbol, price) for member, symbol, quantity, price in self.member_positions if member == spec_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            **({'owned_entry_prices':[list(v) for v in self.owned_entry_prices]} if self.owned_entry_prices else {}),
            **({'member_attribution': self.member_attribution,
                'member_positions': [list(row) for row in self.member_positions],
                'member_pending_sells': [list(row) for row in self.member_pending_sells]}
               if self.member_attribution is not None else {}),
            "snapshot_id": self.snapshot_id,
            "version": self.version,
            "account_ref": self.account_ref,
            "execution_run_id": self.execution_run_id,
            "binding_revision": self.binding_revision,
            "trading_date": self.trading_date.isoformat(),
            "observed_at": self.observed_at.isoformat(),
            "broker_query_started_at": self.broker_query_started_at.isoformat(),
            "broker_query_completed_at": self.broker_query_completed_at.isoformat(),
            "account_as_of": self.account_as_of.isoformat(),
            "account_revision": self.account_revision,
            "reconciliation_revision": self.reconciliation_revision,
            "execution_event_high_watermark": self.execution_event_high_watermark,
            "cost_evidence_revision": self.cost_evidence_revision,
            "daily_net_pnl_won": self.daily_net_pnl_won,
            "daily_net_pnl_source": self.daily_net_pnl_source,
            "cost_complete": self.cost_complete,
            "missing_cost_reasons": list(self.missing_cost_reasons),
            "reconciliation_complete": self.reconciliation_complete,
            "data_path": self.data_path,
            "data_gap_seconds": self.data_gap_seconds,
            "submission_unknown_count": self.submission_unknown_count,
            "reconnect_count": self.reconnect_count,
            "balance_mismatch_count": self.balance_mismatch_count,
            "owned_position_quantities": [list(value) for value in self.owned_position_quantities],
            "pending_sell_quantities": [list(value) for value in self.pending_sell_quantities],
        }


def position_cost_fifo(exact_fills, costs, trading_date, *, cost_query_complete=True):
    """Origin-neutral FIFO/cost arithmetic over already validated fill facts."""
    lots: dict[str, list[list[Any]]] = {}
    realized = Fraction(0)
    # A failed query is not an empty, confirmed broker-cost response, even when
    # the local fill ledger is empty or cached costs still match its rows.
    if type(cost_query_complete) is not bool:
        raise ValueError("cost_query_complete must be a boolean")
    missing_costs: list[str] = [] if cost_query_complete else ["COST_QUERY_UNAVAILABLE"]
    costs_by_key = {(cost.fill_date, cost.stock_code, cost.side): cost for cost in costs}
    fills_by_key: dict[tuple[date, str, str], list[AccountExecutionEvent]] = {}
    for event in exact_fills:
        side = "매수" if event.side == "BUY" else "매도"
        key = (event.occurred_at.astimezone(KST).date(), event.symbol, side)
        fills_by_key.setdefault(key, []).append(event)
    allocated_cost: dict[str, Fraction] = {}
    for key, values in fills_by_key.items():
        evidence = costs_by_key.get(key)
        gross = sum(value.quantity * value.price for value in values)
        if evidence is None:
            missing_costs.append(f"COST_MISSING:{key[0].isoformat()}:{key[1]}:{key[2]}")
            continue
        if evidence.gross_amount != gross:
            missing_costs.append(f"COST_GROSS_MISMATCH:{key[0].isoformat()}:{key[1]}:{key[2]}")
            continue
        for value in values:
            allocated_cost[value.source_event_id] = (
                Fraction(evidence.total_cost * value.quantity * value.price, gross)
                if gross else Fraction(0)
            )
    for key in costs_by_key.keys() - fills_by_key.keys():
        missing_costs.append(f"COST_WITHOUT_EXECUTION_FILL:{key[0].isoformat()}:{key[1]}:{key[2]}")

    unmatched_sell = False
    for event in sorted(exact_fills, key=lambda item: (item.occurred_at, item.accepted_sequence)):
        fill_day = event.occurred_at.astimezone(KST).date()
        if event.side == "BUY":
            lots.setdefault(event.symbol, []).append([
                event.quantity,
                Fraction(event.quantity * event.price) + allocated_cost.get(event.source_event_id, 0),
                event.run_id,
            ])
            continue
        remaining = event.quantity
        sale_cost = allocated_cost.get(event.source_event_id)
        if sale_cost is None:
            sale_cost = Fraction(0)
        matched_proceeds = Fraction(0)
        matched_basis = Fraction(0)
        symbol_lots = lots.setdefault(event.symbol, [])
        while remaining and symbol_lots:
            lot_quantity, lot_basis, lot_run = symbol_lots[0]
            matched = min(remaining, lot_quantity)
            matched_basis += lot_basis * matched / lot_quantity
            matched_proceeds += matched * event.price
            remaining -= matched
            if matched == lot_quantity:
                symbol_lots.pop(0)
            else:
                symbol_lots[0] = [lot_quantity - matched, lot_basis * (lot_quantity - matched) / lot_quantity, lot_run]
        if remaining:
            unmatched_sell = True
            missing_costs.append(f"FIFO_BUY_MISSING:{event.symbol}")
        if fill_day == trading_date:
            realized += matched_proceeds - matched_basis - sale_cost

    return lots, realized, missing_costs, unmatched_sell


def mock_automation_risk_account_revision(recovery: MockAccountRecovery) -> str:
    account = recovery.account
    positions = {str(symbol): int(quantity) for symbol, quantity in account.positions.items() if int(quantity) > 0}
    return _sha256({
        "account_ref": account.account_ref,
        "available_cash_won": account.available_cash_won,
        "reserved_open_buy_won": account.reserved_open_buy_won,
        "positions": sorted(positions.items()),
        "as_of": account.as_of.isoformat(),
        "orders": sorted((order.broker_order_id, order.state.value, order.filled_quantity,
                          order.remaining_quantity, order.as_of.isoformat()) for order in recovery.orders),
    })


def build_mock_automation_risk_snapshot(
    recovery: MockAccountRecovery,
    events: Sequence[AccountExecutionEvent],
    costs: Sequence[DailyTradeCost],
    active_intents: Sequence[ExecutionRecord],
    *,
    execution_run_id: str,
    binding_revision: int,
    reconciliation_revision: int,
    observed_at: datetime,
    broker_query_started_at: datetime,
    broker_query_completed_at: datetime,
    reconnect_count: int = 0,
    balance_mismatch_count: int = 0,
    data_path: str = "nas",
    cost_query_complete: bool = True,
) -> MockAutomationRiskSnapshot:
    """Project immutable risk evidence without estimating missing broker costs."""
    account = recovery.account
    for value in (observed_at, broker_query_started_at, broker_query_completed_at, account.as_of):
        _require_aware(value, "risk timestamp")
    if any(event.account_ref != account.account_ref or event.environment != "mock" for event in events):
        raise ValueError("execution events crossed the risk account scope")
    if any(record.intent.account_ref != account.account_ref for record in active_intents):
        raise ValueError("active intents crossed the risk account scope")
    trading_date = observed_at.astimezone(KST).date()
    exact_fills = _exact_fills(events)
    lots, realized, missing_costs, unmatched_sell = position_cost_fifo(
        exact_fills, costs, trading_date, cost_query_complete=cost_query_complete)

    net_positions = {
        symbol: sum(int(lot[0]) for lot in values)
        for symbol, values in lots.items() if sum(int(lot[0]) for lot in values) > 0
    }
    broker_positions = {
        str(symbol): int(quantity) for symbol, quantity in account.positions.items() if int(quantity) > 0
    }
    balance_matches = net_positions == broker_positions
    unresolved_aggregate = _has_unresolved_aggregate(events)
    open_orders = any(order.state not in TERMINAL_ORDER_STATES for order in recovery.orders)
    submission_unknown = sum(record.state.value == "SUBMISSION_UNKNOWN" for record in active_intents)
    owned = _run_positions(exact_fills, execution_run_id)
    entry_prices = _run_entry_prices(exact_fills, execution_run_id)
    member_facts = _member_risk_facts(exact_fills, active_intents, execution_run_id)
    pending_sells: dict[str, int] = {}
    for record in active_intents:
        if record.intent.run_id == execution_run_id and record.intent.side.value == "SELL":
            pending_sells[record.intent.symbol] = pending_sells.get(record.intent.symbol, 0) + execution_remaining_quantity(record)
    missing_reasons = tuple(dict.fromkeys(missing_costs))
    reconciliation_complete = not unresolved_aggregate and not unmatched_sell and balance_matches
    # Zero is evidence only after a complete account recovery and an empty confirmed ledger.
    known = not missing_reasons and reconciliation_complete
    if not exact_fills and (broker_positions or (open_orders and not _confirmed_zero_fill_orders(recovery, active_intents))):
        known = False
        reconciliation_complete = False
    daily_pnl = round(realized) if known else None
    cost_document = [
        {
            "fill_date": value.fill_date.isoformat(), "stock_code": value.stock_code,
            "side": value.side, "gross_amount": value.gross_amount,
            "total_cost": value.total_cost,
        }
        for value in sorted(costs, key=lambda item: (item.fill_date, item.stock_code, item.side))
    ]
    cost_revision = _sha256(cost_document)
    account_revision = mock_automation_risk_account_revision(recovery)
    body = {
        **member_facts,
        **({'owned_entry_prices':[list(v) for v in sorted(entry_prices.items())]} if entry_prices else {}),
        "version": MOCK_AUTOMATION_RISK_VERSION,
        "account_ref": account.account_ref,
        "execution_run_id": execution_run_id,
        "binding_revision": binding_revision,
        "trading_date": trading_date.isoformat(),
        "observed_at": observed_at.isoformat(),
        "broker_query_started_at": broker_query_started_at.isoformat(),
        "broker_query_completed_at": broker_query_completed_at.isoformat(),
        "account_as_of": account.as_of.isoformat(),
        "account_revision": account_revision,
        "reconciliation_revision": reconciliation_revision,
        "execution_event_high_watermark": max((event.accepted_sequence for event in events), default=0),
        "cost_evidence_revision": cost_revision,
        "daily_net_pnl_won": daily_pnl,
        "daily_net_pnl_source": VERIFIED_DAILY_PNL_SOURCE if daily_pnl is not None else None,
        "cost_complete": not missing_reasons,
        "missing_cost_reasons": list(missing_reasons),
        "reconciliation_complete": reconciliation_complete,
        "data_path": data_path,
        "data_gap_seconds": max(0, int((observed_at - account.as_of).total_seconds())) if account.as_of <= observed_at else None,
        "submission_unknown_count": submission_unknown,
        "reconnect_count": reconnect_count,
        "balance_mismatch_count": balance_mismatch_count + (0 if balance_matches else 1),
        "owned_position_quantities": [list(value) for value in sorted(owned.items()) if value[1] > 0],
        "pending_sell_quantities": [list(value) for value in sorted(pending_sells.items()) if value[1] > 0],
    }
    return MockAutomationRiskSnapshot(snapshot_id=_sha256(body), **{
        **body,
        "trading_date": trading_date,
        "observed_at": observed_at,
        "broker_query_started_at": broker_query_started_at,
        "broker_query_completed_at": broker_query_completed_at,
        "account_as_of": account.as_of,
        "missing_cost_reasons": missing_reasons,
        "owned_position_quantities": tuple(tuple(value) for value in body["owned_position_quantities"]),
        "pending_sell_quantities": tuple(tuple(value) for value in body["pending_sell_quantities"]),
        "owned_entry_prices": tuple(sorted(entry_prices.items())),
        'member_positions': tuple(tuple(row) for row in member_facts.get('member_positions', ())),
        'member_pending_sells': tuple(tuple(row) for row in member_facts.get('member_pending_sells', ())),
    })


def mock_automation_risk_snapshot_from_dict(value: Mapping[str, Any]) -> MockAutomationRiskSnapshot:
    snapshot = MockAutomationRiskSnapshot(
        snapshot_id=str(value["snapshot_id"]), version=str(value["version"]),
        account_ref=str(value["account_ref"]), execution_run_id=str(value["execution_run_id"]),
        binding_revision=int(value["binding_revision"]),
        trading_date=date.fromisoformat(str(value["trading_date"])),
        observed_at=datetime.fromisoformat(str(value["observed_at"])),
        broker_query_started_at=datetime.fromisoformat(str(value["broker_query_started_at"])),
        broker_query_completed_at=datetime.fromisoformat(str(value["broker_query_completed_at"])),
        account_as_of=datetime.fromisoformat(str(value["account_as_of"])),
        account_revision=str(value["account_revision"]),
        reconciliation_revision=int(value["reconciliation_revision"]),
        execution_event_high_watermark=int(value["execution_event_high_watermark"]),
        cost_evidence_revision=str(value["cost_evidence_revision"]),
        daily_net_pnl_won=(int(value["daily_net_pnl_won"]) if value.get("daily_net_pnl_won") is not None else None),
        daily_net_pnl_source=(str(value["daily_net_pnl_source"]) if value.get("daily_net_pnl_source") else None),
        cost_complete=bool(value["cost_complete"]),
        missing_cost_reasons=tuple(str(item) for item in value.get("missing_cost_reasons", ())),
        reconciliation_complete=bool(value["reconciliation_complete"]),
        data_path=str(value["data_path"]),
        data_gap_seconds=(int(value["data_gap_seconds"]) if value.get("data_gap_seconds") is not None else None),
        submission_unknown_count=int(value["submission_unknown_count"]),
        reconnect_count=int(value["reconnect_count"]),
        balance_mismatch_count=int(value["balance_mismatch_count"]),
        owned_position_quantities=tuple((str(item[0]), int(item[1])) for item in value.get("owned_position_quantities", ())),
        pending_sell_quantities=tuple((str(item[0]), int(item[1])) for item in value.get("pending_sell_quantities", ())),
        owned_entry_prices=tuple((item[0], item[1]) for item in value.get('owned_entry_prices', ())),
        member_attribution=value.get('member_attribution'),
        member_positions=tuple(tuple(row) for row in value.get('member_positions', ())),
        member_pending_sells=tuple(tuple(row) for row in value.get('member_pending_sells', ())),
    )
    expected = _sha256({key: item for key, item in snapshot.to_dict().items() if key != "snapshot_id"})
    if snapshot.snapshot_id != expected:
        raise ValueError("mock automation risk content does not match snapshot_id")
    return snapshot


def _exact_fills(events: Sequence[AccountExecutionEvent]) -> tuple[AccountExecutionEvent, ...]:
    found = {}
    for event in events:
        if event.event_type != "FILL" or not event.broker_execution_id or event.quantity <= 0 or event.price <= 0:
            continue
        key = (event.account_ref, event.environment, event.occurred_at.astimezone(KST).date(),
               event.broker_order_id, event.broker_execution_id)
        previous = found.get(key)
        if previous is not None and previous != event:
            raise ValueError("broker execution id has conflicting account events")
        found[key] = event
    return tuple(found.values())


def _has_unresolved_aggregate(events: Sequence[AccountExecutionEvent]) -> bool:
    by_intent = {}
    for event in events:
        by_intent.setdefault((event.intent_id, event.broker_order_id), []).append(event)
    for values in by_intent.values():
        unresolved = 0
        seen_fills: set[str] = set()
        for value in sorted(values, key=lambda item: item.accepted_sequence):
            if value.event_type == "BROKER_FILL_AGGREGATE":
                unresolved += max(0, value.quantity)
            elif value.event_type == "FILL" and value.broker_execution_id not in seen_fills:
                seen_fills.add(value.broker_execution_id)
                unresolved = max(0, unresolved - max(0, value.quantity))
        if unresolved:
            return True
    return False


def _confirmed_zero_fill_orders(recovery, active_intents):
    """Known, accepted native BUY reservations are not missing position/FILL evidence."""
    expected = {}
    for record in active_intents:
        if (record.state not in {OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED}
                or record.intent.side is not OrderSide.BUY or record.filled_quantity
                or record.detailed_filled_quantity or record.broker_reported_filled_quantity
                or record.amendment is not None and record.amendment.reconciliation_pending):
            return False
        for leg in execution_broker_legs(record):
            if leg.state not in TERMINAL_ORDER_STATES and leg.remaining_quantity:
                if leg.state not in {OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED}:
                    return False
                if leg.broker_order_id in expected:
                    return False
                expected[leg.broker_order_id] = (record.intent.symbol, leg.remaining_quantity)
    working = [order for order in recovery.orders if order.state not in TERMINAL_ORDER_STATES]
    actual = {order.broker_order_id: (order.symbol, order.remaining_quantity) for order in working}
    return bool(expected) and len(actual) == len(working) and actual == expected and all(
        order.account_ref == recovery.account.account_ref and order.filled_quantity == 0
        and order.state in {OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED}
        for order in working)


def _run_positions(events: Sequence[AccountExecutionEvent], run_id: str) -> dict[str, int]:
    values: dict[str, int] = {}
    for event in events:
        if event.run_id != run_id:
            continue
        change = event.quantity if event.side == "BUY" else -event.quantity
        values[event.symbol] = values.get(event.symbol, 0) + change
    return values


def _valid_member_spec_id(value):
    prefix = 'mock_automation_spec_'
    return (isinstance(value, str) and value.startswith(prefix) and len(value) == len(prefix)+64
            and all(c in '0123456789abcdef' for c in value[len(prefix):]))


def _member_risk_facts(events, active_intents, run_id):
    fills = [event for event in events if event.run_id == run_id]
    active = [record for record in active_intents if record.intent.run_id == run_id]
    if not any(getattr(item, 'member_spec_id', None) is not None for item in (*fills, *active)):
        return {}
    complete = all(_valid_member_spec_id(getattr(item, 'member_spec_id', None)) for item in (*fills, *active))
    positions, pending, invalid = {}, {}, set()
    for event in sorted(fills, key=lambda item: item.accepted_sequence):
        member = getattr(event, 'member_spec_id', None)
        if not _valid_member_spec_id(member):
            continue
        key = (member, event.symbol)
        quantity, price = positions.get(key, (0, 0))
        if event.side == 'BUY':
            positions[key] = (quantity+event.quantity,
                mock_owned_buy_basis(quantity, price, event.quantity, event.price) if quantity >= 0 else event.price)
        elif event.quantity > quantity:
            invalid.add(key)
            positions[key] = (quantity-event.quantity, price)
        else:
            positions[key] = (quantity-event.quantity, price)
    for record in active:
        member = getattr(record, 'member_spec_id', None)
        if _valid_member_spec_id(member) and record.intent.side is OrderSide.SELL:
            key = (member, record.intent.symbol)
            pending[key] = pending.get(key, 0)+execution_remaining_quantity(record)
    return {'member_attribution': 'COMPLETE' if complete and not invalid else 'INCOMPLETE',
        'member_positions': [[member, symbol, quantity, price] for (member, symbol), (quantity, price)
                             in sorted(positions.items()) if quantity > 0 and (member,symbol) not in invalid],
        'member_pending_sells': [[member, symbol, quantity] for (member, symbol), quantity in sorted(pending.items()) if quantity > 0]}


def _run_entry_prices(events, run_id):
    """Same accepted-FILL moving average/rounding as Runner; never infer an aggregate."""
    positions = {}
    invalid = set()
    for event in sorted(events, key=lambda item: item.accepted_sequence):
        if event.run_id != run_id:
            continue
        quantity, price = positions.get(event.symbol, (0, 0))
        if event.side == 'BUY':
            total = quantity+event.quantity
            positions[event.symbol] = (total, mock_owned_buy_basis(quantity, price, event.quantity, event.price))
        elif event.quantity > quantity:
            invalid.add(event.symbol)
        else:
            positions[event.symbol] = (quantity-event.quantity, price)
    return {symbol:price for symbol,(quantity,price) in positions.items() if quantity > 0 and symbol not in invalid}


def mock_owned_buy_basis(old_quantity, old_price, fill_quantity, fill_price):
    """The existing strategy's integer moving average, shared by native projections."""
    return (old_quantity*old_price + fill_quantity*fill_price)//(old_quantity+fill_quantity)


def _sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _require_sha256(value: str, name: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{name} must be a lowercase sha256")
