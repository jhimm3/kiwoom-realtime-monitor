"""Per-decision safety gate for one admitted mock-automation runtime."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from copy import deepcopy
from datetime import datetime, timedelta
from enum import StrEnum
from contextlib import AbstractContextManager
from typing import Any, Mapping, Protocol

from kiwoom_monitor.application.breakout_strategy import StrategyDecision
from kiwoom_monitor.application.market_session_schedule import mock_order_entry_decision
from kiwoom_monitor.application.mock_automation_admission import (
    MockAutomationAdmission,
    MockAutomationControl,
    MockAutomationDesiredState,
    MockAutomationLeaseReceipt,
    MOCK_AUTOMATION_CONTROL_VERSION,
)
from kiwoom_monitor.application.mock_automation_recovery import (
    MockAutomationRecoveryDecision,
    MockAutomationRecoveryStatus,
    mock_account_recovery_fingerprint,
)
from kiwoom_monitor.application.mock_automation_risk import (
    MockAutomationRiskSnapshot,
    VERIFIED_DAILY_PNL_SOURCE,
)
from kiwoom_monitor.domain.execution_activation import ForwardEvaluationSpec, MockAutomationOperatingSpec
from kiwoom_monitor.domain.order_contract import (
    TERMINAL_ORDER_STATES,
    AccountSnapshot,
    OrderIntent,
    OrderSide,
    OrderType,
)
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import MockAccountRecovery
from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRecord


MOCK_AUTOMATION_DECISION_GATE_VERSION = "mock_automation_decision_gate/v2"
MOCK_AUTOMATION_DECISION_GATE_LEGACY_VERSION = "mock_automation_decision_gate/v1"
MOCK_AUTOMATION_REPLACEMENT_GATE_VERSION = "mock_automation_replacement_gate/v3"
MOCK_AUTOMATION_DISPATCH_RECEIPT_VERSION = "mock_automation_dispatch_receipt/v1"
MOCK_AUTOMATION_STOP_VERSION = "mock_automation_stop/v1"


class MockAutomationGateStatus(StrEnum):
    BLOCKED = "BLOCKED"
    APPROVED_FOR_SINGLE_SUBMISSION = "APPROVED_FOR_SINGLE_SUBMISSION"


@dataclass(frozen=True)
class MockAutomationLiveMetrics:
    observed_at: datetime
    recovery_complete: bool
    daily_net_pnl_won: int | None
    daily_net_pnl_source: str | None
    data_gap_seconds: int | None
    submission_unknown_count: int
    reconnect_count: int
    balance_mismatch_count: int
    data_path: str
    reconciliation_pending: bool = False
    owned_position_quantities: tuple[tuple[str, int], ...] = ()
    pending_sell_quantities: tuple[tuple[str, int], ...] = ()

    def __post_init__(self) -> None:
        _require_aware(self.observed_at, "observed_at")
        if self.daily_net_pnl_won is not None and type(self.daily_net_pnl_won) is not int:
            raise ValueError("daily_net_pnl_won must be an integer or unknown")
        if self.data_gap_seconds is not None and (
            type(self.data_gap_seconds) is not int or self.data_gap_seconds < 0
        ):
            raise ValueError("data_gap_seconds must be non-negative or unknown")
        if any(
            type(value) is not int or value < 0
            for value in (
                self.submission_unknown_count,
                self.reconnect_count,
                self.balance_mismatch_count,
            )
        ):
            raise ValueError("mock automation fault counters must be non-negative integers")
        if self.data_path not in {"nas", "direct"}:
            raise ValueError("data_path must be nas or direct")
        if type(self.reconciliation_pending) is not bool:
            raise ValueError("reconciliation_pending must be a boolean")
        for name in ("owned_position_quantities", "pending_sell_quantities"):
            values = getattr(self, name)
            if any(not str(symbol).strip() or type(quantity) is not int or quantity < 0
                   for symbol, quantity in values):
                raise ValueError(f"{name} must contain non-negative symbol quantities")
            if len({str(symbol) for symbol, _ in values}) != len(values):
                raise ValueError(f"{name} must not contain duplicate symbols")


@dataclass(frozen=True)
class MockAutomationDecisionGate:
    gate_id: str
    version: str
    admission_id: str
    lease_receipt_id: str
    recovery_decision_id: str
    spec_id: str
    strategy_ref: str
    account_ref: str
    execution_run_id: str
    strategy_decision_id: str
    intent_id: str
    symbol: str
    action: str
    quantity: int
    limit_price: int
    decided_at: datetime
    observed_at: datetime
    account_as_of: datetime
    server_now: datetime
    recovery_fingerprint: str
    session_evidence: str
    daily_net_pnl_won: int | None
    daily_net_pnl_source: str | None
    data_gap_seconds: int | None
    submission_unknown_count: int
    reconnect_count: int
    balance_mismatch_count: int
    in_flight_order_count: int
    control_revision: int
    status: MockAutomationGateStatus
    reasons: tuple[str, ...]
    risk_snapshot_id: str = ""
    risk_reconciliation_revision: int = 0
    replacement_binding: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        for name in (
            "gate_id", "admission_id", "lease_receipt_id", "recovery_decision_id",
            "spec_id", "strategy_ref", "account_ref", "execution_run_id",
            "strategy_decision_id", "intent_id", "symbol", "action",
            "recovery_fingerprint", "session_evidence",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        if self.version not in {
            MOCK_AUTOMATION_DECISION_GATE_VERSION,
            MOCK_AUTOMATION_DECISION_GATE_LEGACY_VERSION,
            MOCK_AUTOMATION_REPLACEMENT_GATE_VERSION,
        }:
            raise ValueError("unsupported mock automation decision gate")
        if not self.action:
            raise ValueError("mock automation gate action is required")
        if self.quantity < 0 or self.limit_price <= 0 or self.in_flight_order_count < 0:
            raise ValueError("mock automation order quantity must be non-negative and price positive")
        if type(self.control_revision) is not int or self.control_revision < 0:
            raise ValueError("control_revision must be non-negative")
        _require_aware(self.decided_at, "decided_at")
        _require_aware(self.observed_at, "observed_at")
        _require_aware(self.account_as_of, "account_as_of")
        _require_aware(self.server_now, "server_now")
        if self.status is MockAutomationGateStatus.APPROVED_FOR_SINGLE_SUBMISSION and self.reasons:
            raise ValueError("approved mock automation gate cannot contain blocking reasons")
        if self.status is MockAutomationGateStatus.APPROVED_FOR_SINGLE_SUBMISSION and (
            self.action not in ({"ADD", "REENTER", "REDUCE", "EXIT"}
                if self.version == MOCK_AUTOMATION_REPLACEMENT_GATE_VERSION else {"ENTER", "EXIT"}) or self.quantity <= 0
            or (
                self.version != MOCK_AUTOMATION_DECISION_GATE_LEGACY_VERSION
                and self.control_revision <= 0
            )
        ):
            raise ValueError("only actionable positive-quantity Decisions may be approved")
        if bool(self.risk_snapshot_id) != (self.risk_reconciliation_revision > 0):
            raise ValueError("risk snapshot identity and revision must be provided together")
        if self.version == MOCK_AUTOMATION_REPLACEMENT_GATE_VERSION:
            from kiwoom_monitor.application.order_lifecycle import unfilled_plan_intent
            binding = self.replacement_binding
            keys = {"root_intent_id", "parent_intent_id", "attempt", "purpose", "operating_policy_id",
                    "child_payload", "child_payload_hash", "assessment_ref", "paired_evidence"}
            if not isinstance(binding, Mapping) or set(binding) != keys:
                raise ValueError("replacement approval requires exact child lineage")
            child = unfilled_plan_intent(binding["child_payload"])
            if (type(binding["attempt"]) is not int or not 1 <= binding["attempt"] <= 64
                    or not binding["root_intent_id"] or not binding["parent_intent_id"]
                    or not str(binding["operating_policy_id"]).startswith("mock_unfilled_policy_")
                    or child.intent_id != "unfilled-native-child:" + hashlib.sha256(
                        f'{binding["root_intent_id"]}:{binding["attempt"]}'.encode()).hexdigest()
                    or binding["child_payload_hash"] != _content_id("fixed_child", binding["child_payload"])
                    or (child.intent_id, child.decision_id, child.account_ref, child.run_id, child.symbol,
                        child.quantity, child.limit_price, child.created_at) != (
                        self.intent_id, self.strategy_decision_id, self.account_ref, self.execution_run_id,
                        self.symbol, self.quantity, self.limit_price, self.decided_at)
                    or child.environment != "mock" or child.venue != "KRX" or child.order_type is not OrderType.LIMIT
                    or (child.side is OrderSide.BUY) != (self.action in {"ADD", "REENTER"})
                    or binding["purpose"] not in {"BUY_OPPORTUNITY", "TAKE_PROFIT", "PROTECTIVE_EXIT"}
                    or (binding["purpose"] == "BUY_OPPORTUNITY") != (child.side is OrderSide.BUY)
                    or (binding["purpose"] == "PROTECTIVE_EXIT" and self.action != "EXIT")
                    or not self.risk_snapshot_id):
                raise ValueError("replacement approval child binding mismatch")
            if not str(binding['assessment_ref']).startswith('unfilled-order-management-shadow:'):
                raise ValueError('replacement approval assessment reference missing')
            if binding['paired_evidence'] is not None:
                from kiwoom_monitor.application.order_lifecycle import UnfilledRepriceEvidence
                evidence = dict(binding['paired_evidence'])
                evidence['available_at'] = datetime.fromisoformat(evidence['available_at'])
                UnfilledRepriceEvidence(**evidence)
        elif self.replacement_binding is not None:
            raise ValueError("legacy gate cannot carry replacement authority")

    def to_dict(self) -> dict[str, Any]:
        document = {
            "gate_id": self.gate_id,
            "version": self.version,
            "admission_id": self.admission_id,
            "lease_receipt_id": self.lease_receipt_id,
            "recovery_decision_id": self.recovery_decision_id,
            "spec_id": self.spec_id,
            "strategy_ref": self.strategy_ref,
            "account_ref": self.account_ref,
            "execution_run_id": self.execution_run_id,
            "strategy_decision_id": self.strategy_decision_id,
            "intent_id": self.intent_id,
            "symbol": self.symbol,
            "action": self.action,
            "quantity": self.quantity,
            "limit_price": self.limit_price,
            "decided_at": self.decided_at.isoformat(),
            "observed_at": self.observed_at.isoformat(),
            "account_as_of": self.account_as_of.isoformat(),
            "server_now": self.server_now.isoformat(),
            "recovery_fingerprint": self.recovery_fingerprint,
            "session_evidence": self.session_evidence,
            "daily_net_pnl_won": self.daily_net_pnl_won,
            "daily_net_pnl_source": self.daily_net_pnl_source,
            "data_gap_seconds": self.data_gap_seconds,
            "submission_unknown_count": self.submission_unknown_count,
            "reconnect_count": self.reconnect_count,
            "balance_mismatch_count": self.balance_mismatch_count,
            "in_flight_order_count": self.in_flight_order_count,
            "control_revision": self.control_revision,
            "status": self.status.value,
            "reasons": list(self.reasons),
            "risk_snapshot_id": self.risk_snapshot_id,
            "risk_reconciliation_revision": self.risk_reconciliation_revision,
        }
        if self.replacement_binding is not None:
            document["replacement_binding"] = deepcopy(dict(self.replacement_binding))
        return document


@dataclass(frozen=True)
class MockAutomationDispatchReceipt:
    receipt_id: str
    version: str
    gate_id: str
    intent_id: str
    account_ref: str
    execution_run_id: str
    strategy_decision_id: str
    order_state: str
    broker_order_id: str
    dispatched_at: datetime

    def __post_init__(self) -> None:
        for name in (
            "receipt_id", "gate_id", "intent_id", "account_ref",
            "execution_run_id", "strategy_decision_id", "order_state",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        if self.version != MOCK_AUTOMATION_DISPATCH_RECEIPT_VERSION:
            raise ValueError("unsupported mock automation dispatch receipt")
        _require_aware(self.dispatched_at, "dispatched_at")

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "version": self.version,
            "gate_id": self.gate_id,
            "intent_id": self.intent_id,
            "account_ref": self.account_ref,
            "execution_run_id": self.execution_run_id,
            "strategy_decision_id": self.strategy_decision_id,
            "order_state": self.order_state,
            "broker_order_id": self.broker_order_id,
            "dispatched_at": self.dispatched_at.isoformat(),
        }


@dataclass(frozen=True)
class MockAutomationStopRevision:
    revision_id: str
    version: str
    admission_id: str
    spec_id: str
    account_ref: str
    execution_run_id: str
    stopped_at: datetime
    reason: str
    new_orders_enabled: bool = False

    def __post_init__(self) -> None:
        for name in (
            "revision_id", "admission_id", "spec_id", "account_ref",
            "execution_run_id", "reason",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        if self.version != MOCK_AUTOMATION_STOP_VERSION or self.new_orders_enabled is not False:
            raise ValueError("mock automation stop must close new orders")
        _require_aware(self.stopped_at, "stopped_at")

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision_id": self.revision_id,
            "version": self.version,
            "admission_id": self.admission_id,
            "spec_id": self.spec_id,
            "account_ref": self.account_ref,
            "execution_run_id": self.execution_run_id,
            "stopped_at": self.stopped_at.isoformat(),
            "reason": self.reason,
            "new_orders_enabled": self.new_orders_enabled,
        }


class MockAutomationExecutionRepository(Protocol):
    def load_mock_automation_spec(
        self, account_ref: str, spec_id: str,
    ) -> MockAutomationOperatingSpec | None: ...
    def load_profile(
        self, strategy_ref: str, profile_id: str,
    ) -> ForwardEvaluationSpec | None: ...
    def save_mock_automation_spec(self, value: MockAutomationOperatingSpec) -> bool: ...
    def load_mock_automation_admissions(
        self, account_ref: str,
    ) -> tuple[MockAutomationAdmission, ...]: ...
    def load_mock_automation_admission_for_spec(
        self, account_ref: str, spec_id: str,
    ) -> MockAutomationAdmission | None: ...
    def load_mock_automation_lease_receipts(
        self, account_ref: str,
    ) -> tuple[MockAutomationLeaseReceipt, ...]: ...
    def load_mock_automation_lease_for_admission(
        self, account_ref: str, admission_id: str,
    ) -> MockAutomationLeaseReceipt | None: ...
    def load_mock_automation_recovery_decisions(
        self, account_ref: str,
    ) -> tuple[MockAutomationRecoveryDecision, ...]: ...
    def load_latest_mock_automation_recovery(
        self, account_ref: str, admission_id: str,
    ) -> MockAutomationRecoveryDecision | None: ...
    def save_mock_automation_decision_gate(self, value: MockAutomationDecisionGate) -> bool: ...
    def save_mock_automation_dispatch_receipt(
        self, value: MockAutomationDispatchReceipt,
    ) -> bool: ...
    def load_mock_automation_dispatch_receipts(
        self, account_ref: str,
    ) -> tuple[MockAutomationDispatchReceipt, ...]: ...
    def load_mock_automation_dispatch_receipt(
        self, account_ref: str, intent_id: str,
    ) -> MockAutomationDispatchReceipt | None: ...
    def load_mock_automation_decision_gates(
        self, account_ref: str,
    ) -> tuple[MockAutomationDecisionGate, ...]: ...
    def load_mock_automation_approved_gate(
        self, account_ref: str, intent_id: str,
    ) -> MockAutomationDecisionGate | None: ...
    def load_mock_automation_decision_gate(self, account_ref: str, gate_id: str) -> MockAutomationDecisionGate | None: ...
    def save_mock_automation_stop_revision(self, value: MockAutomationStopRevision) -> bool: ...
    def load_mock_automation_stop_revisions(
        self, account_ref: str,
    ) -> tuple[MockAutomationStopRevision, ...]: ...
    def load_latest_mock_automation_stop(
        self, account_ref: str, admission_id: str,
    ) -> MockAutomationStopRevision | None: ...
    def load_mock_automation_control(self, account_ref: str) -> MockAutomationControl | None: ...
    def save_mock_automation_control(
        self, value: MockAutomationControl, *, expected_revision: int,
    ) -> bool: ...
    def load_latest_mock_automation_risk(
        self, account_ref: str,
    ) -> MockAutomationRiskSnapshot | None: ...


class ClosedGateMockRuntime(Protocol):
    account_ref: str
    run_id: str

    def heartbeat(self) -> None: ...
    def server_now(self) -> datetime: ...
    def set_new_orders_enabled(self, enabled: bool) -> None: ...
    def automation_decision_guard(self) -> AbstractContextManager[None]: ...
    def load_intent(self, intent_id: str) -> ExecutionRecord | None: ...
    def load_active_intents(self) -> tuple[ExecutionRecord, ...]: ...
    def load_account_active_intents(self) -> tuple[tuple[ExecutionRecord, ...], bool]: ...
    def load_intent_events(self, intent_id: str) -> tuple[Mapping[str, Any], ...]: ...
    def load_intent_claim_approval(self, intent_id: str) -> Mapping[str, Any] | None: ...
    def submit_automation_intent(
        self, intent: OrderIntent, account: AccountSnapshot, *, reference_price: int | None = None,
        spec_id: str, control_revision: int, replacement_claim: dict | None = None,
        protection_claim: dict | None = None,
    ) -> ExecutionRecord: ...


def mock_automation_intent_id(spec_id: str, strategy_decision_id: str) -> str:
    if not spec_id.strip() or not strategy_decision_id.strip():
        raise ValueError("spec_id and strategy_decision_id are required")
    digest = hashlib.sha256(f"{spec_id}:{strategy_decision_id}".encode("utf-8")).hexdigest()
    return f"mock_auto_intent_{digest}"


def _decision_safety_context(spec, admission, recovery_decision, decision, recovery, metrics, *,
                             strategy_ref, latest_stop, control, in_flight_order_count, profile,
                             effective_now, is_exit, allow_manage_only=False):
    """Shared existing safety rules; action/parent-child checks stay with each caller."""
    decided_at = datetime.fromisoformat(decision.decided_at)
    reasons: list[str] = []
    if strategy_ref != spec.strategy_ref:
        reasons.append("STRATEGY_REF_MISMATCH")
    if decision.run_id != admission.execution_run_id:
        reasons.append("EXECUTION_RUN_MISMATCH")
    allowed_recovery = {
        MockAutomationRecoveryStatus.CLEARED_ORDERS_DISABLED,
        *({MockAutomationRecoveryStatus.MANAGE_ONLY_ORDERS_DISABLED} if is_exit or allow_manage_only else set()),
    }
    if recovery_decision.status not in allowed_recovery:
        reasons.append("LATEST_RECOVERY_NOT_CLEARED")
    if metrics.observed_at < recovery_decision.observed_at:
        reasons.append("LIVE_OBSERVATION_PREDATES_RECOVERY")
    if metrics.observed_at > effective_now:
        reasons.append("LIVE_OBSERVATION_FROM_FUTURE")
    elif spec.maximum_data_gap_seconds is not None and (
        effective_now - metrics.observed_at
    ).total_seconds() > spec.maximum_data_gap_seconds:
        reasons.append("LIVE_OBSERVATION_STALE")
    if profile is None:
        reasons.append("FORWARD_PROFILE_MISSING")
    elif not is_exit:
        if effective_now < profile.evaluation_start:
            reasons.append("EVALUATION_NOT_STARTED")
        elif effective_now > profile.evaluation_end:
            reasons.append("EVALUATION_ENDED")
    if latest_stop is not None and latest_stop.stopped_at >= recovery_decision.observed_at:
        reasons.append("EMERGENCY_STOP_REQUIRES_NEW_RECOVERY")
    if control is None:
        reasons.append("AUTOMATION_CONTROL_MISSING")
    elif (
        control.desired_state is not MockAutomationDesiredState.RUNNING
        or control.active_spec_id != spec.spec_id
        or control.execution_run_id != admission.execution_run_id
    ):
        reasons.append("AUTOMATION_CONTROL_NOT_RUNNING")
    if not metrics.recovery_complete:
        reasons.append("BROKER_RECOVERY_INCOMPLETE")
    if metrics.reconciliation_pending:
        reasons.append("RECONCILIATION_PENDING")
    if not is_exit:
        if metrics.daily_net_pnl_won is None:
            reasons.append("DAILY_NET_PNL_UNKNOWN")
        if metrics.daily_net_pnl_source != VERIFIED_DAILY_PNL_SOURCE:
            reasons.append("DAILY_NET_PNL_SOURCE_UNVERIFIED")
        if metrics.data_gap_seconds is None:
            reasons.append("DATA_GAP_UNKNOWN")
    if metrics.data_path != spec.data_path:
        reasons.append("DATA_PATH_MISMATCH")

    if not is_exit:
        _append_limit_reason(
            reasons, metrics.daily_net_pnl_won, spec.maximum_daily_loss_won,
            lambda value, limit: value <= -limit, "DAILY_LOSS_LIMIT_REACHED",
        )
        _append_limit_reason(
            reasons, metrics.data_gap_seconds, spec.maximum_data_gap_seconds,
            lambda value, limit: value > limit, "DATA_GAP_LIMIT_EXCEEDED",
        )
    for value, limit, reason in (
        (metrics.submission_unknown_count, spec.maximum_submission_unknown_count,
         "SUBMISSION_UNKNOWN_LIMIT_EXCEEDED"),
        (metrics.balance_mismatch_count, spec.maximum_balance_mismatch_count,
         "BALANCE_MISMATCH_LIMIT_EXCEEDED"),
    ):
        _append_limit_reason(reasons, value, limit, lambda item, maximum: item > maximum, reason)
    if not is_exit:
        _append_limit_reason(
            reasons, metrics.reconnect_count, spec.maximum_reconnect_count,
            lambda item, maximum: item > maximum, "RECONNECT_LIMIT_EXCEEDED",
        )

    max_gap = spec.maximum_data_gap_seconds
    if decided_at > metrics.observed_at:
        reasons.append("STRATEGY_DECISION_FROM_FUTURE")
    elif max_gap is not None and (metrics.observed_at - decided_at).total_seconds() > max_gap:
        reasons.append("STRATEGY_DECISION_STALE")
    if recovery.account.as_of > metrics.observed_at:
        reasons.append("BROKER_ACCOUNT_SNAPSHOT_FROM_FUTURE")
    elif max_gap is not None and (
        metrics.observed_at - recovery.account.as_of
    ).total_seconds() > max_gap:
        reasons.append("BROKER_ACCOUNT_SNAPSHOT_STALE")
    if any(order.as_of > metrics.observed_at for order in recovery.orders):
        reasons.append("BROKER_ORDER_SNAPSHOT_FROM_FUTURE")
    elif max_gap is not None and any(
        (metrics.observed_at - order.as_of).total_seconds() > max_gap
        for order in recovery.orders
    ):
        reasons.append("BROKER_ORDER_SNAPSHOT_STALE")
    if any(order.state not in TERMINAL_ORDER_STATES for order in recovery.orders):
        reasons.append("BROKER_OPEN_ORDER_PRESENT")
    if in_flight_order_count:
        reasons.append("EXECUTION_ORDER_IN_FLIGHT")

    positions = {
        symbol: int(quantity)
        for symbol, quantity in recovery.account.positions.items()
        if int(quantity) > 0
    }
    if spec.maximum_concurrent_positions is not None and (
        len(positions) > spec.maximum_concurrent_positions
    ):
        reasons.append("POSITION_LIMIT_EXCEEDED")
    return reasons, positions


def assess_mock_automation_decision(
    spec: MockAutomationOperatingSpec,
    admission: MockAutomationAdmission,
    lease_receipt: MockAutomationLeaseReceipt,
    recovery_decision: MockAutomationRecoveryDecision,
    decision: StrategyDecision,
    recovery: MockAccountRecovery,
    metrics: MockAutomationLiveMetrics,
    *,
    strategy_ref: str,
    latest_stop: MockAutomationStopRevision | None = None,
    control: MockAutomationControl | None = None,
    in_flight_order_count: int = 0,
    profile: ForwardEvaluationSpec | None = None,
    server_now: datetime | None = None,
    risk_snapshot_id: str = "",
    risk_reconciliation_revision: int = 0,
) -> MockAutomationDecisionGate:
    _validate_decision_identity(decision)
    _validate_lineage(spec, admission, lease_receipt, recovery_decision, recovery)
    decided_at = datetime.fromisoformat(decision.decided_at)
    effective_now = server_now or metrics.observed_at
    _require_aware(effective_now, "server_now")
    intent_id = mock_automation_intent_id(spec.spec_id, decision.decision_id)
    reasons, positions = _decision_safety_context(
        spec, admission, recovery_decision, decision, recovery, metrics,
        strategy_ref=strategy_ref, latest_stop=latest_stop, control=control,
        in_flight_order_count=in_flight_order_count, profile=profile, effective_now=effective_now,
        is_exit=decision.final_action == "EXIT",
    )
    if decision.final_action == "ENTER":
        if positions:
            reasons.append("ENTRY_REQUIRES_FLAT_ACCOUNT")
        if decision.required_capital_won != decision.quantity * decision.signal_reference_price:
            reasons.append("DECISION_CAPITAL_MISMATCH")
        if (
            spec.maximum_capital_won is not None
            and decision.required_capital_won > spec.maximum_capital_won
        ):
            reasons.append("CAPITAL_LIMIT_EXCEEDED")
        available = recovery.account.available_cash_won - recovery.account.reserved_open_buy_won
        if decision.required_capital_won > available:
            reasons.append("INSUFFICIENT_AVAILABLE_CASH")
    elif decision.final_action == "EXIT":
        owned_positions = dict(metrics.owned_position_quantities)
        pending_sells = dict(metrics.pending_sell_quantities)
        owned = owned_positions.get(decision.symbol)
        if owned is None:
            reasons.append("RUN_POSITION_UNVERIFIED")
        elif max(0, min(int(positions.get(decision.symbol, 0)), owned)
                 - pending_sells.get(decision.symbol, 0)) < decision.quantity:
            reasons.append("INSUFFICIENT_POSITION")
    else:
        reasons.append("NON_ACTION_DECISION")

    session = mock_order_entry_decision(
        effective_now,
        environment="mock",
        venue=spec.supported_venue or "",
        order_type=OrderType.LIMIT.value,
    )
    if not session.allowed:
        reasons.append("MARKET_SESSION_NOT_SUPPORTED")
    if session.session_profile != spec.session_profile:
        reasons.append("SESSION_PROFILE_MISMATCH")
    unique_reasons = tuple(dict.fromkeys(reasons))
    body = {
        "version": MOCK_AUTOMATION_DECISION_GATE_VERSION,
        "admission_id": admission.admission_id,
        "lease_receipt_id": lease_receipt.receipt_id,
        "recovery_decision_id": recovery_decision.decision_id,
        "spec_id": spec.spec_id,
        "strategy_ref": strategy_ref,
        "account_ref": admission.account_ref,
        "execution_run_id": admission.execution_run_id,
        "strategy_decision_id": decision.decision_id,
        "intent_id": intent_id,
        "symbol": decision.symbol,
        "action": decision.final_action,
        "quantity": decision.quantity,
        "limit_price": decision.signal_reference_price,
        "decided_at": decided_at.isoformat(),
        "observed_at": metrics.observed_at.isoformat(),
        "account_as_of": recovery.account.as_of.isoformat(),
        "server_now": effective_now.isoformat(),
        "recovery_fingerprint": mock_account_recovery_fingerprint(recovery),
        "session_evidence": session.evidence,
        "daily_net_pnl_won": metrics.daily_net_pnl_won,
        "daily_net_pnl_source": metrics.daily_net_pnl_source,
        "data_gap_seconds": metrics.data_gap_seconds,
        "submission_unknown_count": metrics.submission_unknown_count,
        "reconnect_count": metrics.reconnect_count,
        "balance_mismatch_count": metrics.balance_mismatch_count,
        "in_flight_order_count": in_flight_order_count,
        "control_revision": control.control_revision if control is not None else 0,
        "status": (
            MockAutomationGateStatus.BLOCKED.value
            if unique_reasons else MockAutomationGateStatus.APPROVED_FOR_SINGLE_SUBMISSION.value
        ),
        "reasons": list(unique_reasons),
        "risk_snapshot_id": risk_snapshot_id,
        "risk_reconciliation_revision": risk_reconciliation_revision,
    }
    return MockAutomationDecisionGate(
        gate_id=_content_id("mock_automation_gate", body),
        decided_at=decided_at,
        observed_at=metrics.observed_at,
        account_as_of=recovery.account.as_of,
        server_now=effective_now,
        status=MockAutomationGateStatus(body["status"]),
        reasons=unique_reasons,
        **{
            key: value for key, value in body.items()
            if key not in {
                "decided_at", "observed_at", "account_as_of", "server_now",
                "status", "reasons",
            }
        },
    )


def dispatch_mock_automation_decision(
    repository: MockAutomationExecutionRepository,
    runtime: ClosedGateMockRuntime,
    decision: StrategyDecision,
    recovery: MockAccountRecovery,
    metrics: MockAutomationLiveMetrics,
    *,
    account_ref: str,
    spec_id: str,
    strategy_ref: str,
    risk_snapshot_id: str = "",
    risk_reconciliation_revision: int = 0,
    protection_exit_id: str | None = None,
) -> tuple[MockAutomationDecisionGate, ExecutionRecord | None, MockAutomationDispatchReceipt | None]:
    spec, profile, admission, lease, recovered, latest_stop, control = _load_context(
        repository, account_ref, spec_id,
    )
    if runtime.account_ref != account_ref or runtime.run_id != admission.execution_run_id:
        raise ValueError("mock automation runtime does not match its admission")
    runtime.set_new_orders_enabled(False)
    with runtime.automation_decision_guard():
        server_now = runtime.server_now()
        control = repository.load_mock_automation_control(account_ref)
        repository.save_mock_automation_spec(spec)  # Revalidate binding for every new Decision.
        _validate_decision_identity(decision)
        if strategy_ref != spec.strategy_ref:
            raise ValueError("strategy_ref does not match the admitted operating spec")
        if decision.run_id != admission.execution_run_id:
            raise ValueError("strategy Decision does not belong to the admitted execution run")
        if decision.final_action not in {"ENTER", "EXIT"}:
            gate = assess_mock_automation_decision(
                spec, admission, lease, recovered, decision, recovery, metrics,
                strategy_ref=strategy_ref, latest_stop=latest_stop, control=control,
                profile=profile, server_now=server_now,
                risk_snapshot_id=risk_snapshot_id,
                risk_reconciliation_revision=risk_reconciliation_revision,
            )
            repository.save_mock_automation_decision_gate(gate)
            return gate, None, None
        expected_intent = _intent_for_decision(spec, admission, decision)
        if protection_exit_id is not None:
            from dataclasses import replace
            from kiwoom_monitor.application.mock_automation_risk import PROTECTION_EXIT_VERSION
            if decision.final_action != 'EXIT':
                raise ValueError('protection_claim_requires_exit')
            expected_intent = replace(expected_intent, policy_version=PROTECTION_EXIT_VERSION)
        existing = runtime.load_intent(expected_intent.intent_id)
        if existing is not None:
            if existing.intent != expected_intent:
                raise ValueError("deterministic mock automation intent has conflicting content")
            gate = repository.load_mock_automation_approved_gate(
                account_ref, existing.intent.intent_id,
            )
            if gate is None or gate.admission_id != admission.admission_id:
                raise RuntimeError("existing automation intent has no approved decision gate")
            receipt = repository.load_mock_automation_dispatch_receipt(
                account_ref, existing.intent.intent_id,
            )
            if receipt is None:
                raise RuntimeError("existing automation intent has no dispatch receipt")
            return gate, existing, receipt

        in_flight = len(runtime.load_active_intents())
        gate = assess_mock_automation_decision(
            spec, admission, lease, recovered, decision, recovery, metrics,
            strategy_ref=strategy_ref, latest_stop=latest_stop, control=control,
            in_flight_order_count=in_flight,
            profile=profile, server_now=server_now,
            risk_snapshot_id=risk_snapshot_id,
            risk_reconciliation_revision=risk_reconciliation_revision,
        )
        repository.save_mock_automation_decision_gate(gate)
        if gate.status is MockAutomationGateStatus.BLOCKED:
            return gate, None, None

        record = runtime.submit_automation_intent(
            expected_intent, recovery.account, reference_price=decision.signal_reference_price,
            spec_id=spec.spec_id, control_revision=gate.control_revision,
            **({'protection_claim': {'workflow_id':protection_exit_id,'gate_id':gate.gate_id,
                'risk_snapshot_id':risk_snapshot_id,'risk_reconciliation_revision':risk_reconciliation_revision}}
               if protection_exit_id is not None else {}),
        )
        receipt = _dispatch_receipt(gate, record, metrics.observed_at)
        repository.save_mock_automation_dispatch_receipt(receipt)
        return gate, record, receipt


def dispatch_mock_automation_decision_from_risk(
    repository: MockAutomationExecutionRepository,
    runtime: ClosedGateMockRuntime,
    decision: StrategyDecision,
    recovery: MockAccountRecovery,
    risk: MockAutomationRiskSnapshot,
    *,
    account_ref: str,
    spec_id: str,
    strategy_ref: str,
    protection_exit_id: str | None = None,
) -> tuple[MockAutomationDecisionGate, ExecutionRecord | None, MockAutomationDispatchReceipt | None]:
    """Dispatch only from the latest stored NAS risk revision."""
    metrics = _validated_risk_metrics(repository, runtime, recovery, risk, account_ref, spec_id)
    return dispatch_mock_automation_decision(
        repository, runtime, decision, recovery, metrics,
        account_ref=account_ref, spec_id=spec_id, strategy_ref=strategy_ref,
        risk_snapshot_id=risk.snapshot_id,
        risk_reconciliation_revision=risk.reconciliation_revision,
        **({'protection_exit_id':protection_exit_id} if protection_exit_id is not None else {}),
    )


def assess_mock_automation_decision_from_risk(repository, runtime, decision, recovery, risk, *, account_ref, spec_id, strategy_ref):
    """Existing ENTER/EXIT gate assessment without dispatch or approved-gate writes.

    The caller owns automation_decision_guard through its native checkpoint ACK.
    Unsupported ADD/REDUCE remain NON_ACTION_DECISION; never relabel them.
    """
    spec, profile, admission, lease, recovered, latest_stop, control, metrics, now, in_flight = _native_gate_context(
        repository, runtime, recovery, risk, account_ref, spec_id,
    )
    return assess_mock_automation_decision(
        spec, admission, lease, recovered, decision, recovery, metrics,
        strategy_ref=strategy_ref, latest_stop=latest_stop, control=control,
        profile=profile, server_now=now, in_flight_order_count=in_flight,
        risk_snapshot_id=risk.snapshot_id, risk_reconciliation_revision=risk.reconciliation_revision,
    )


def _native_gate_context(repository, runtime, recovery, risk, account_ref, spec_id):
    metrics = _validated_risk_metrics(repository, runtime, recovery, risk, account_ref, spec_id)
    spec, profile, admission, lease, recovered, stop, control = _load_context(repository, account_ref, spec_id)
    if runtime.account_ref != account_ref or runtime.run_id != admission.execution_run_id:
        raise ValueError('mock automation runtime does not match its admission')
    repository.save_mock_automation_spec(spec)
    return spec, profile, admission, lease, recovered, stop, control, metrics, runtime.server_now(), len(runtime.load_active_intents())


def assess_mock_unfilled_gate_bundle_from_risk(repository, runtime, decision, recovery, risk, *,
                                              account_ref, spec_id, strategy_ref, plan, root, managed,
                                              protection_review_required=False):
    """One native context, legacy comparison and exact-child offline purpose gate.

    This creates no executable approval, dispatch receipt, intent or transport call.
    The calling runner owns the closed runtime guard through checkpoint ACK.
    """
    context = _native_gate_context(repository, runtime, recovery, risk, account_ref, spec_id)
    spec, profile, admission, lease, recovered, stop, control, metrics, now, in_flight = context
    legacy = assess_mock_automation_decision(
        spec, admission, lease, recovered, decision, recovery, metrics,
        strategy_ref=strategy_ref, latest_stop=stop, control=control, profile=profile,
        server_now=now, in_flight_order_count=in_flight,
        risk_snapshot_id=risk.snapshot_id, risk_reconciliation_revision=risk.reconciliation_revision,
    )
    result = plan['result']
    protective = result['purpose'] == 'PROTECTIVE_EXIT'
    reasons, positions = _decision_safety_context(
        spec, admission, recovered, decision, recovery, metrics, strategy_ref=strategy_ref,
        latest_stop=stop, control=control, profile=profile, effective_now=now,
        in_flight_order_count=in_flight, is_exit=protective, allow_manage_only=True,
    )
    if protection_review_required and not protective:
        reasons.append('PROTECTION_REVIEW_BLOCKS_OPPORTUNITY')
    pending = plan['children'][-1] if len(plan['children']) > len(plan['native_claimed_intent_ids']) else None
    child = pending['intent'] if pending else None
    if result['action'] != 'REPRICE' or result['status'] != 'PROPOSED':
        reasons.append('REPLACEMENT_POLICY_NOT_READY')
    if not plan['pending_child_matches_current_cut'] or child is None:
        reasons.append('FIXED_CHILD_NOT_CURRENT_OR_EXPIRED')
    if child is not None:
        if (pending['parent_intent_id'] != managed.intent.intent_id
                or pending['root_intent_id'] != root.intent.intent_id
                or child['account_ref'] != account_ref or child['run_id'] != admission.execution_run_id
                or child['symbol'] != decision.symbol or child['side'] != managed.intent.side.value
                or child['quantity'] != result['replacement_quantity']
                or child['limit_price'] != result['replacement_limit_price_won']):
            reasons.append('FIXED_CHILD_BINDING_MISMATCH')
        if (managed.state.value != 'CANCELLED' or managed.filled_quantity != managed.detailed_filled_quantity
                or managed.broker_reported_filled_quantity > managed.detailed_filled_quantity
                or managed.last_broker_as_of is None
                or managed.last_broker_as_of > datetime.fromisoformat(child['created_at'])):
            reasons.append('PARENT_CANCEL_AND_FILLS_NOT_CONFIRMED')
        if child['quantity'] > managed.intent.quantity - managed.filled_quantity:
            reasons.append('ORIGINAL_UNFILLED_BUDGET_EXCEEDED')
        if (plan['native_reprices_used'] >= plan['anchor']['policy']['maximum_reprices']
                or pending['attempt'] != plan['native_reprices_used'] + 1):
            reasons.append('NATIVE_RETRY_BUDGET_EXHAUSTED_OR_INCONSISTENT')
        if any(row['state'] not in {state.value for state in TERMINAL_ORDER_STATES}
               for row in result['inputs']['open_orders']):
            reasons.append('ACCOUNT_WIDE_EXECUTION_ORDER_IN_FLIGHT')
        if managed.intent.side is OrderSide.BUY:
            original_gate = repository.load_mock_automation_approved_gate(account_ref, root.intent.intent_id)
            if (original_gate is None or original_gate.status is not MockAutomationGateStatus.APPROVED_FOR_SINGLE_SUBMISSION
                    or original_gate.action != 'ENTER' or original_gate.admission_id != admission.admission_id
                    or original_gate.spec_id != spec_id or original_gate.account_ref != account_ref
                    or original_gate.execution_run_id != admission.execution_run_id
                    or original_gate.intent_id != root.intent.intent_id
                    or original_gate.strategy_decision_id != root.intent.decision_id
                    or original_gate.symbol != root.intent.symbol
                    or original_gate.decided_at != root.intent.created_at
                    or original_gate.control_revision != plan['anchor']['scope']['control_revision']
                    or root.intent.intent_id != mock_automation_intent_id(spec_id, root.intent.decision_id)
                    or original_gate.quantity != root.intent.quantity or original_gate.limit_price != root.intent.limit_price):
                reasons.append('ORIGINAL_BUY_APPROVAL_LINEAGE_UNVERIFIED')
            if decision.final_action not in {'ADD', 'REENTER'} or result['purpose'] != 'BUY_OPPORTUNITY':
                reasons.append('BUY_REPLACEMENT_PURPOSE_MISMATCH')
            if any(symbol != child['symbol'] for symbol in positions):
                reasons.append('BUY_REPLACEMENT_OTHER_POSITION_PRESENT')
            if positions.get(child['symbol'], 0) != dict(metrics.owned_position_quantities).get(child['symbol'], 0):
                reasons.append('RUN_POSITION_UNVERIFIED')
            amount = child['quantity'] * child['limit_price']
            exposure = (positions.get(child['symbol'], 0) + child['quantity']) * child['limit_price']
            if spec.maximum_capital_won is None or exposure > spec.maximum_capital_won:
                reasons.append('CAPITAL_LIMIT_EXCEEDED_OR_UNKNOWN')
            if amount > recovery.account.available_cash_won - recovery.account.reserved_open_buy_won:
                reasons.append('INSUFFICIENT_AVAILABLE_CASH')
        else:
            if decision.final_action not in {'REDUCE', 'EXIT'} or (protective and decision.final_action != 'EXIT'):
                reasons.append('SELL_REPLACEMENT_PURPOSE_MISMATCH')
            owned = dict(metrics.owned_position_quantities).get(child['symbol'])
            if owned is None:
                reasons.append('RUN_POSITION_UNVERIFIED')
            elif max(0, min(positions.get(child['symbol'], 0), owned)
                     - dict(metrics.pending_sell_quantities).get(child['symbol'], 0)) < child['quantity']:
                reasons.append('INSUFFICIENT_POSITION')
    session = mock_order_entry_decision(now, environment='mock', venue=spec.supported_venue or '', order_type='LIMIT')
    if not session.allowed or session.reason == 'mock_after_broker_probe':
        reasons.append('MARKET_SESSION_NOT_SUPPORTED')
    if session.session_profile != spec.session_profile:
        reasons.append('SESSION_PROFILE_MISMATCH')
    unique = list(dict.fromkeys(reasons))
    body = {'version': 'mock-unfilled-replacement-gate/v1', 'account_ref': account_ref,
        'execution_run_id': admission.execution_run_id, 'spec_id': spec_id, 'admission_id': admission.admission_id,
        'lease_receipt_id': lease.receipt_id, 'control_revision': control.control_revision if control else 0,
        'purpose': result['purpose'], 'action': decision.final_action, 'root_intent_id': root.intent.intent_id,
        'parent_intent_id': managed.intent.intent_id, 'child_intent_id': child['intent_id'] if child else None,
        'child_payload': child, 'child_payload_hash': _content_id('fixed_child', child) if child else None,
        'source_decision_ref': result['decision_id'], 'risk_snapshot_id': risk.snapshot_id,
        'risk_reconciliation_revision': risk.reconciliation_revision, 'assessed_at': now.isoformat(),
        'status': 'BLOCKED' if unique else 'VALIDATED_FOR_OFFLINE_REPLACEMENT', 'reasons': unique,
        'ev_authority': result['ev_status'], 'risk_path_required': result['risk_path_required'],
        'transport_state': 'OFF', 'order_authorized': False}
    return legacy, {'gate_id': _content_id('mock_unfilled_gate', body), **body}


def unfilled_execution_policy_rejections(spec, policy, root):
    """Shared frozen management limits for cancellation and replacement."""
    frozen = spec.unfilled_execution_policy
    if frozen is None:
        return ('UNFILLED_EXECUTION_POLICY_OFF',)
    reasons = []
    for field in ('maximum_reprices', 'maximum_adverse_slippage_bps', 'protective_slippage_bps',
                  'max_snapshot_age_seconds'):
        if getattr(policy, field) > getattr(frozen, field):
            reasons.append('UNFILLED_EXECUTION_FROZEN_LIMIT_EXCEEDED')
    for field in ('minimum_net_advantage_won', 'uncertainty_multiplier', 'minimum_paired_samples'):
        if getattr(policy, field) < getattr(frozen, field):
            reasons.append('UNFILLED_EXECUTION_EVIDENCE_LIMIT_WEAKENED')
    if (policy.reprice_horizon_seconds != frozen.reprice_horizon_seconds
            or policy.valid_until > root.intent.created_at + timedelta(seconds=frozen.maximum_management_seconds)):
        reasons.append('UNFILLED_EXECUTION_FROZEN_HORIZON_EXCEEDED')
    return tuple(reasons)


def dispatch_mock_unfilled_replacement_from_risk(
    repository, runtime, *, plan, policy, broker_snapshot, target, observation, assessment, recovery, risk,
    protection_review_required=False,
    execution_cancelled=False,
):
    """Fresh native approval for one fixed child; never promote an OFF receipt."""
    from dataclasses import asdict
    from kiwoom_monitor.application.order_lifecycle import (
        validate_unfilled_attempt_plan, project_unfilled_native_attempts,
        evaluate_unfilled_order_management_shadow, unfilled_plan_intent,
    )
    from kiwoom_monitor.application.position_target import _json_value
    validate_unfilled_attempt_plan(plan)
    if not plan['children']:
        raise ValueError('unfilled_execution_requires_fixed_child')
    scope = plan['anchor']['scope']; account_ref = scope['account_ref']; spec_id = scope['spec_id']
    slot = plan['children'][-1]; intent = unfilled_plan_intent(slot['intent'])
    with runtime.automation_decision_guard():
        root = runtime.load_intent(plan['anchor']['root_intent']['intent_id'])
        if root is None or root.intent != unfilled_plan_intent(plan['anchor']['root_intent']):
            raise ValueError('unfilled_execution_root_changed')
        if root.intent.intent_id.startswith('unfilled-native-child:'):
            raise ValueError('unfilled_replacement_child_cannot_reset_original_anchor')
        records = [runtime.load_intent(row['intent']['intent_id']) for row in plan['children']]
        histories = [runtime.load_intent_events(row['intent']['intent_id']) for row in plan['children']]
        projection = project_unfilled_native_attempts(plan, root, records, histories,
                                                    root_events=runtime.load_intent_events(root.intent.intent_id))
        if records[-1] is not None:
            # QUEUED/UNKNOWN is a consumed attempt, not a retry instruction.
            gate = repository.load_mock_automation_approved_gate(account_ref, intent.intent_id)
            if (gate is None or gate.version != MOCK_AUTOMATION_REPLACEMENT_GATE_VERSION
                    or gate.replacement_binding['child_payload'] != slot['intent']):
                raise RuntimeError('unfilled_existing_child_without_fixed_approval')
            claim = runtime.load_intent_claim_approval(intent.intent_id)
            claimed_gate = (repository.load_mock_automation_decision_gate(account_ref, claim['gate_id'])
                            if claim else None)
            if (claimed_gate is None or claimed_gate.version != MOCK_AUTOMATION_REPLACEMENT_GATE_VERSION
                    or claimed_gate.status is not MockAutomationGateStatus.APPROVED_FOR_SINGLE_SUBMISSION
                    or claimed_gate.risk_snapshot_id != claim['risk_snapshot_id']
                    or claimed_gate.risk_reconciliation_revision != claim['risk_reconciliation_revision']
                    or _fixed_replacement_binding(claimed_gate) != _fixed_replacement_binding(gate)):
                raise RuntimeError('unfilled_existing_child_claim_gate_missing_or_changed')
            gate = claimed_gate
            receipt = repository.load_mock_automation_dispatch_receipt(account_ref, intent.intent_id)
            if receipt is not None:
                actual_gate = repository.load_mock_automation_decision_gate(account_ref, receipt.gate_id)
                if actual_gate is None or actual_gate.gate_id != claimed_gate.gate_id:
                    raise RuntimeError('unfilled_dispatch_receipt_gate_mismatch')
                gate = actual_gate
            if receipt is None:
                receipt = _dispatch_receipt(gate, records[-1], runtime.server_now())
                repository.save_mock_automation_dispatch_receipt(receipt)
            return gate, records[-1], receipt
        managed = projection['managed_record']
        if json.loads(json.dumps(asdict(policy), default=_json_value)) != plan['anchor']['policy']:
            raise ValueError('unfilled_execution_policy_anchor_changed')
        if (observation.account != recovery.account or observation.risk_snapshot != risk
                or not observation.open_orders_complete or observation.unmapped_broker_order_ids):
            raise ValueError('unfilled_execution_account_cut_mismatch')
        active, complete = runtime.load_account_active_intents()
        if not complete or set(active) != set(observation.open_orders):
            raise ValueError('unfilled_execution_account_ledger_not_current')
        result = evaluate_unfilled_order_management_shadow(
            policy, managed, broker_snapshot, target, observation,
            replace(assessment, reprices_used=projection['native_reprices_used']), root_record=root,
        )
        if result != plan['result']:
            raise ValueError('unfilled_execution_assessment_changed')
        raw = deepcopy(plan['assessment_decision'])
        raw['reasons'] = tuple(raw['reasons']); raw['constraints'] = tuple(raw['constraints'])
        decision = StrategyDecision(**raw); _validate_decision_identity(decision)
        pending_protection = lambda: (protection_review_required() if callable(protection_review_required)
                                     else bool(protection_review_required))
        spec = repository.load_mock_automation_spec(account_ref, spec_id)
        legacy, purpose = assess_mock_unfilled_gate_bundle_from_risk(
            repository, runtime, decision, recovery, risk, account_ref=account_ref, spec_id=spec_id,
            strategy_ref=spec.strategy_ref, plan=plan, root=root, managed=managed,
            protection_review_required=pending_protection(),
        )
        frozen = spec.unfilled_execution_policy
        reasons = list(purpose['reasons'])
        reasons.extend(unfilled_execution_policy_rejections(spec, policy, root))
        if frozen is not None:
            if (intent.created_at > risk.observed_at
                    or intent.expires_at > intent.created_at + timedelta(seconds=frozen.max_snapshot_age_seconds)):
                reasons.append('FIXED_CHILD_EXCEEDS_NATIVE_RISK_FRESHNESS')
            if result['purpose'] != 'PROTECTIVE_EXIT' and (
                    not frozen.allow_supplied_paired_mock_evidence
                    or result['ev_status'] != 'SUPPLIED_PAIRED_UNFILLED_ESTIMATE'):
                reasons.append('UNFILLED_PAIRED_EVIDENCE_NOT_ADMITTED_FOR_MOCK')
        binding = {'root_intent_id': root.intent.intent_id, 'parent_intent_id': managed.intent.intent_id,
                   'attempt': slot['attempt'], 'purpose': result['purpose'],
                   'operating_policy_id': frozen.policy_id if frozen else 'mock_unfilled_policy_OFF',
                   'child_payload': deepcopy(slot['intent']), 'child_payload_hash': _content_id('fixed_child', slot['intent']),
                   'assessment_ref': result['decision_id'],
                   'paired_evidence': (json.loads(json.dumps(asdict(assessment.reprice_evidence), default=_json_value))
                       if assessment.reprice_evidence is not None and result['purpose'] != 'PROTECTIVE_EXIT' else None)}
        body = legacy.to_dict()
        body.pop('gate_id')
        body.update(version=MOCK_AUTOMATION_REPLACEMENT_GATE_VERSION, replacement_binding=binding,
                    strategy_decision_id=intent.decision_id, intent_id=intent.intent_id, action=target.action,
                    quantity=intent.quantity, limit_price=intent.limit_price, decided_at=intent.created_at.isoformat(),
                    reasons=list(dict.fromkeys(reasons)), status=(MockAutomationGateStatus.BLOCKED.value if reasons
                            else MockAutomationGateStatus.APPROVED_FOR_SINGLE_SUBMISSION.value))
        gate = mock_automation_decision_gate_from_dict({'gate_id': _content_id('mock_automation_gate', body), **body})
        if gate.status is MockAutomationGateStatus.BLOCKED:
            repository.save_mock_automation_decision_gate(gate)
            return gate, None, None
        preserved = repository.load_mock_automation_approved_gate(account_ref, intent.intent_id)
        if preserved is not None:
            if preserved.version != gate.version or _fixed_replacement_binding(preserved) != _fixed_replacement_binding(gate):
                raise RuntimeError('unfilled_fixed_child_approval_conflict')
        repository.save_mock_automation_decision_gate(gate)  # Fresh cut ledger; first fixed approval stays immutable.
        # Revalidate mutable evidence after all approval writes/ACKs.
        _validated_risk_metrics(repository, runtime, recovery, risk, account_ref, spec_id)
        control = repository.load_mock_automation_control(account_ref)
        now = runtime.server_now()
        if (control is None or control.desired_state is not MockAutomationDesiredState.RUNNING
                or control.active_spec_id != spec_id or control.control_revision != scope['control_revision']
                or runtime.load_intent(managed.intent.intent_id) != managed
                or runtime.load_intent(intent.intent_id) is not None
                or not 0 <= (now - risk.observed_at).total_seconds() <= frozen.max_snapshot_age_seconds
                or not 0 <= (now - target.decision_time).total_seconds() <= frozen.max_snapshot_age_seconds
                or now >= intent.expires_at
                or (pending_protection() and result['purpose'] != 'PROTECTIVE_EXIT')):
            raise ValueError('unfilled_execution_changed_or_expired_during_approval')
        if execution_cancelled() if callable(execution_cancelled) else execution_cancelled:
            raise RuntimeError('unfilled_execution_closed_before_claim')
        record = runtime.submit_automation_intent(
            intent, recovery.account, reference_price=intent.limit_price, spec_id=spec_id,
            control_revision=control.control_revision,
            replacement_claim={'gate_id': gate.gate_id, 'risk_snapshot_id': risk.snapshot_id,
                               'risk_reconciliation_revision': risk.reconciliation_revision},
        )
        receipt = _dispatch_receipt(gate, record, now)
        repository.save_mock_automation_dispatch_receipt(receipt)
        return gate, record, receipt


def _fixed_replacement_binding(gate):
    """The child claim identity excludes renewed Risk/paired-evidence cuts."""
    return {key: value for key, value in gate.replacement_binding.items()
            if key not in {'assessment_ref', 'paired_evidence'}}


def _validated_risk_metrics(repository, runtime, recovery, risk, account_ref, spec_id):
    loader = getattr(repository, "load_latest_mock_automation_risk", None)
    current = loader(account_ref) if callable(loader) else None
    if current != risk:
        raise RuntimeError("latest mock automation risk snapshot changed")
    admission = repository.load_mock_automation_admission_for_spec(account_ref, spec_id)
    if admission is None:
        raise ValueError("mock automation admission is missing")
    recovered = repository.load_latest_mock_automation_recovery(
        account_ref, admission.admission_id,
    )
    if recovered is None or (
        recovered.risk_snapshot_id != risk.snapshot_id
        or recovered.risk_reconciliation_revision != risk.reconciliation_revision
    ):
        raise RuntimeError("latest broker recovery is not linked to this risk snapshot")
    if (
        risk.account_ref != account_ref
        or risk.execution_run_id != runtime.run_id
        or risk.account_as_of != recovery.account.as_of
    ):
        raise ValueError("mock automation risk snapshot does not match dispatch context")
    metrics = MockAutomationLiveMetrics(
        observed_at=risk.observed_at,
        recovery_complete=risk.reconciliation_complete,
        daily_net_pnl_won=risk.daily_net_pnl_won,
        daily_net_pnl_source=risk.daily_net_pnl_source,
        data_gap_seconds=risk.data_gap_seconds,
        submission_unknown_count=risk.submission_unknown_count,
        reconnect_count=risk.reconnect_count,
        balance_mismatch_count=risk.balance_mismatch_count,
        data_path=risk.data_path,
        reconciliation_pending=not risk.reconciliation_complete,
        owned_position_quantities=risk.owned_position_quantities,
        pending_sell_quantities=risk.pending_sell_quantities,
    )
    return metrics


def emergency_stop_mock_automation(
    repository: MockAutomationExecutionRepository,
    runtime: ClosedGateMockRuntime,
    *,
    account_ref: str,
    spec_id: str,
    stopped_at: datetime,
    reason: str,
    expected_control_revision: int | None = None,
) -> MockAutomationStopRevision:
    _require_aware(stopped_at, "stopped_at")
    if not reason.strip():
        raise ValueError("emergency stop reason is required")
    spec = repository.load_mock_automation_spec(account_ref, spec_id)
    if spec is None:
        raise ValueError("mock automation operating spec is not stored")
    admission = repository.load_mock_automation_admission_for_spec(account_ref, spec_id)
    if admission is None:
        raise ValueError("mock automation admission is missing")
    control = repository.load_mock_automation_control(account_ref)
    if runtime.account_ref != account_ref or runtime.run_id != admission.execution_run_id:
        raise ValueError("mock automation runtime does not match its admission")
    if control is None:
        runtime.set_new_orders_enabled(False)
        raise RuntimeError("mock automation control is missing")
    if (
        control.active_spec_id != spec.spec_id
        or control.execution_run_id != admission.execution_run_id
        or (expected_control_revision is not None
            and control.control_revision != expected_control_revision)
    ):
        runtime.set_new_orders_enabled(False)
        raise RuntimeError("mock automation control does not belong to this run or revision")
    stopped_control = MockAutomationControl(
        version=MOCK_AUTOMATION_CONTROL_VERSION,
        account_ref=account_ref,
        desired_state=MockAutomationDesiredState.STOPPED,
        control_revision=control.control_revision + 1,
        active_spec_id=spec.spec_id,
        execution_run_id=admission.execution_run_id,
        changed_at=stopped_at,
        reason=reason.strip(),
    )
    try:
        saved = repository.save_mock_automation_control(
            stopped_control, expected_revision=control.control_revision,
        )
    except Exception:
        runtime.set_new_orders_enabled(False)
        raise
    if not saved:
        runtime.set_new_orders_enabled(False)
        raise RuntimeError("mock automation control changed before stop")
    runtime.set_new_orders_enabled(False)
    body = {
        "version": MOCK_AUTOMATION_STOP_VERSION,
        "admission_id": admission.admission_id,
        "spec_id": spec.spec_id,
        "account_ref": account_ref,
        "execution_run_id": admission.execution_run_id,
        "stopped_at": stopped_at.isoformat(),
        "reason": reason.strip(),
        "new_orders_enabled": False,
    }
    revision = MockAutomationStopRevision(
        revision_id=_content_id("mock_automation_stop", body),
        stopped_at=stopped_at,
        **{key: value for key, value in body.items() if key != "stopped_at"},
    )
    repository.save_mock_automation_stop_revision(revision)
    return revision


def resume_mock_automation(
    repository: MockAutomationExecutionRepository,
    runtime: ClosedGateMockRuntime,
    *,
    account_ref: str,
    spec_id: str,
    resumed_at: datetime,
    reason: str,
) -> MockAutomationControl:
    """Resume a stopped run only after a newer broker reconciliation was recorded."""
    _require_aware(resumed_at, "resumed_at")
    if not reason.strip():
        raise ValueError("mock automation resume reason is required")
    spec = repository.load_mock_automation_spec(account_ref, spec_id)
    if spec is None:
        raise ValueError("mock automation operating spec is not stored")
    admission = repository.load_mock_automation_admission_for_spec(account_ref, spec_id)
    if admission is None:
        raise ValueError("mock automation admission is missing")
    if runtime.account_ref != account_ref or runtime.run_id != admission.execution_run_id:
        raise ValueError("mock automation runtime does not match its admission")
    runtime.set_new_orders_enabled(False)
    control = repository.load_mock_automation_control(account_ref)
    if control is None:
        raise RuntimeError("mock automation control is missing")
    if (
        control.active_spec_id != spec.spec_id
        or control.execution_run_id != admission.execution_run_id
    ):
        raise RuntimeError("mock automation control does not belong to this run")
    if control.desired_state is not MockAutomationDesiredState.STOPPED:
        raise RuntimeError("mock automation is not stopped")
    repository.save_mock_automation_spec(spec)  # Revalidate the current account binding.
    recovery = repository.load_latest_mock_automation_recovery(
        account_ref, admission.admission_id,
    )
    if recovery is None or recovery.observed_at < control.changed_at:
        raise RuntimeError("a fresh broker reconciliation is required before resume")
    if recovery.status not in {
        MockAutomationRecoveryStatus.CLEARED_ORDERS_DISABLED,
        MockAutomationRecoveryStatus.MANAGE_ONLY_ORDERS_DISABLED,
    }:
        raise RuntimeError("latest broker reconciliation does not permit resume")
    resumed = MockAutomationControl(
        version=MOCK_AUTOMATION_CONTROL_VERSION,
        account_ref=account_ref,
        desired_state=MockAutomationDesiredState.RUNNING,
        control_revision=control.control_revision + 1,
        active_spec_id=spec.spec_id,
        execution_run_id=admission.execution_run_id,
        changed_at=resumed_at,
        reason=reason.strip(),
    )
    if not repository.save_mock_automation_control(
        resumed, expected_revision=control.control_revision,
    ):
        raise RuntimeError("mock automation control changed before resume")
    return resumed


def mock_automation_decision_gate_from_dict(value: Mapping[str, Any]) -> MockAutomationDecisionGate:
    gate = MockAutomationDecisionGate(
        gate_id=str(value["gate_id"]), version=str(value["version"]),
        admission_id=str(value["admission_id"]),
        lease_receipt_id=str(value["lease_receipt_id"]),
        recovery_decision_id=str(value["recovery_decision_id"]), spec_id=str(value["spec_id"]),
        strategy_ref=str(value["strategy_ref"]), account_ref=str(value["account_ref"]),
        execution_run_id=str(value["execution_run_id"]),
        strategy_decision_id=str(value["strategy_decision_id"]), intent_id=str(value["intent_id"]),
        symbol=str(value["symbol"]), action=str(value["action"]), quantity=int(value["quantity"]),
        limit_price=int(value["limit_price"]), decided_at=datetime.fromisoformat(str(value["decided_at"])),
        observed_at=datetime.fromisoformat(str(value["observed_at"])),
        account_as_of=datetime.fromisoformat(str(value["account_as_of"])),
        server_now=datetime.fromisoformat(str(value.get("server_now", value["observed_at"]))),
        recovery_fingerprint=str(value["recovery_fingerprint"]),
        session_evidence=str(value["session_evidence"]),
        daily_net_pnl_won=(int(value["daily_net_pnl_won"]) if value.get("daily_net_pnl_won") is not None else None),
        daily_net_pnl_source=(str(value["daily_net_pnl_source"]) if value.get("daily_net_pnl_source") is not None else None),
        data_gap_seconds=(int(value["data_gap_seconds"]) if value.get("data_gap_seconds") is not None else None),
        submission_unknown_count=int(value["submission_unknown_count"]),
        reconnect_count=int(value["reconnect_count"]),
        balance_mismatch_count=int(value["balance_mismatch_count"]),
        in_flight_order_count=int(value.get("in_flight_order_count", 0)),
        control_revision=int(value.get("control_revision", 0)),
        status=MockAutomationGateStatus(str(value["status"])),
        reasons=tuple(str(item) for item in value.get("reasons", ())),
        risk_snapshot_id=str(value.get("risk_snapshot_id") or ""),
        risk_reconciliation_revision=int(value.get("risk_reconciliation_revision") or 0),
        replacement_binding=deepcopy(value.get("replacement_binding")),
    )
    _verify_content_id("mock_automation_gate", gate.gate_id, dict(value), "gate_id")
    return gate


def mock_automation_dispatch_receipt_from_dict(
    value: Mapping[str, Any],
) -> MockAutomationDispatchReceipt:
    receipt = MockAutomationDispatchReceipt(
        receipt_id=str(value["receipt_id"]), version=str(value["version"]),
        gate_id=str(value["gate_id"]), intent_id=str(value["intent_id"]),
        account_ref=str(value["account_ref"]), execution_run_id=str(value["execution_run_id"]),
        strategy_decision_id=str(value["strategy_decision_id"]), order_state=str(value["order_state"]),
        broker_order_id=str(value.get("broker_order_id") or ""),
        dispatched_at=datetime.fromisoformat(str(value["dispatched_at"])),
    )
    _verify_content_id("mock_automation_dispatch", receipt.receipt_id, receipt.to_dict(), "receipt_id")
    return receipt


def mock_automation_stop_revision_from_dict(value: Mapping[str, Any]) -> MockAutomationStopRevision:
    revision = MockAutomationStopRevision(
        revision_id=str(value["revision_id"]), version=str(value["version"]),
        admission_id=str(value["admission_id"]), spec_id=str(value["spec_id"]),
        account_ref=str(value["account_ref"]), execution_run_id=str(value["execution_run_id"]),
        stopped_at=datetime.fromisoformat(str(value["stopped_at"])), reason=str(value["reason"]),
        new_orders_enabled=value["new_orders_enabled"],
    )
    _verify_content_id("mock_automation_stop", revision.revision_id, revision.to_dict(), "revision_id")
    return revision


def _load_context(repository, account_ref: str, spec_id: str):
    spec = repository.load_mock_automation_spec(account_ref, spec_id)
    if spec is None:
        raise ValueError("mock automation operating spec is not stored")
    profile = repository.load_profile(spec.strategy_ref, spec.forward_profile_id)
    admission = repository.load_mock_automation_admission_for_spec(account_ref, spec_id)
    if admission is None:
        raise ValueError("mock automation admission is missing")
    lease = repository.load_mock_automation_lease_for_admission(
        account_ref, admission.admission_id,
    )
    if lease is None:
        raise ValueError("mock automation lease receipt is missing")
    recovery = repository.load_latest_mock_automation_recovery(
        account_ref, admission.admission_id,
    )
    if recovery is None:
        raise ValueError("mock automation recovery decision is missing")
    latest_stop = repository.load_latest_mock_automation_stop(
        account_ref, admission.admission_id,
    )
    control = repository.load_mock_automation_control(account_ref)
    return spec, profile, admission, lease, recovery, latest_stop, control


def _validate_lineage(spec, admission, lease, recovery_decision, recovery) -> None:
    if (
        admission.spec_id != spec.spec_id
        or admission.account_ref != spec.account_scope.account_ref
        or lease.admission_id != admission.admission_id
        or lease.spec_id != spec.spec_id
        or lease.execution_run_id != admission.execution_run_id
        or lease.new_orders_enabled is not False
        or recovery_decision.admission_id != admission.admission_id
        or recovery_decision.lease_receipt_id != lease.receipt_id
        or recovery_decision.spec_id != spec.spec_id
        or recovery_decision.execution_run_id != admission.execution_run_id
        or recovery.account.account_ref != admission.account_ref
        or any(order.account_ref != admission.account_ref for order in recovery.orders)
    ):
        raise ValueError("mock automation decision lineage is invalid")


def _validate_decision_identity(decision: StrategyDecision) -> None:
    body = decision.to_dict()
    body.pop("decision_id")
    if decision.decision_id != _content_id("decision", body):
        raise ValueError("strategy Decision content does not match decision_id")


def _intent_for_decision(
    spec: MockAutomationOperatingSpec,
    admission: MockAutomationAdmission,
    decision: StrategyDecision,
) -> OrderIntent:
    decided_at = datetime.fromisoformat(decision.decided_at)
    side = OrderSide.BUY if decision.final_action == "ENTER" else OrderSide.SELL
    validity_seconds = max(1, int(spec.maximum_data_gap_seconds or 1))
    return OrderIntent(
        intent_id=mock_automation_intent_id(spec.spec_id, decision.decision_id),
        run_id=admission.execution_run_id,
        decision_id=decision.decision_id,
        account_ref=admission.account_ref,
        environment="mock",
        symbol=decision.symbol,
        venue=spec.supported_venue or "",
        side=side,
        quantity=decision.quantity,
        order_type=OrderType.LIMIT,
        limit_price=decision.signal_reference_price,
        created_at=decided_at,
        expires_at=decided_at + timedelta(seconds=validity_seconds),
        policy_version=f"{MOCK_AUTOMATION_DECISION_GATE_VERSION}:{spec.session_profile}",
    )


def _dispatch_receipt(
    gate: MockAutomationDecisionGate, record: ExecutionRecord, dispatched_at: datetime,
) -> MockAutomationDispatchReceipt:
    body = {
        "version": MOCK_AUTOMATION_DISPATCH_RECEIPT_VERSION,
        "gate_id": gate.gate_id,
        "intent_id": record.intent.intent_id,
        "account_ref": gate.account_ref,
        "execution_run_id": gate.execution_run_id,
        "strategy_decision_id": gate.strategy_decision_id,
        "order_state": record.state.value,
        "broker_order_id": record.broker_order_id,
        "dispatched_at": dispatched_at.isoformat(),
    }
    return MockAutomationDispatchReceipt(
        receipt_id=_content_id("mock_automation_dispatch", body),
        dispatched_at=dispatched_at,
        **{key: value for key, value in body.items() if key != "dispatched_at"},
    )


def _append_limit_reason(reasons, value, limit, predicate, reason) -> None:
    if value is not None and limit is not None and predicate(value, limit):
        reasons.append(reason)


def _content_id(prefix: str, value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()}"


def _verify_content_id(prefix: str, actual: str, value: dict[str, Any], id_name: str) -> None:
    body = dict(value)
    body.pop(id_name)
    if actual != _content_id(prefix, body):
        raise ValueError(f"mock automation {id_name} content hash is invalid")


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
