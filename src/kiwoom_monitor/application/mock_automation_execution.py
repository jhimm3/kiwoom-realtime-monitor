"""Per-decision safety gate for one admitted mock-automation runtime."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from contextlib import AbstractContextManager
from typing import Any, Mapping, Protocol

from kiwoom_monitor.application.breakout_strategy import StrategyDecision
from kiwoom_monitor.application.market_session_schedule import mock_order_entry_decision
from kiwoom_monitor.application.mock_automation_admission import (
    MockAutomationAdmission,
    MockAutomationLeaseReceipt,
)
from kiwoom_monitor.application.mock_automation_recovery import (
    MockAutomationRecoveryDecision,
    MockAutomationRecoveryStatus,
    mock_account_recovery_fingerprint,
)
from kiwoom_monitor.domain.execution_activation import MockAutomationOperatingSpec
from kiwoom_monitor.domain.order_contract import (
    TERMINAL_ORDER_STATES,
    AccountSnapshot,
    OrderIntent,
    OrderSide,
    OrderType,
)
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import MockAccountRecovery
from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRecord


MOCK_AUTOMATION_DECISION_GATE_VERSION = "mock_automation_decision_gate/v1"
MOCK_AUTOMATION_DISPATCH_RECEIPT_VERSION = "mock_automation_dispatch_receipt/v1"
MOCK_AUTOMATION_STOP_VERSION = "mock_automation_stop/v1"
VERIFIED_DAILY_PNL_SOURCE = "account_scoped_fifo_broker_cost/v1"


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
    recovery_fingerprint: str
    session_evidence: str
    daily_net_pnl_won: int | None
    daily_net_pnl_source: str | None
    data_gap_seconds: int | None
    submission_unknown_count: int
    reconnect_count: int
    balance_mismatch_count: int
    in_flight_order_count: int
    status: MockAutomationGateStatus
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "gate_id", "admission_id", "lease_receipt_id", "recovery_decision_id",
            "spec_id", "strategy_ref", "account_ref", "execution_run_id",
            "strategy_decision_id", "intent_id", "symbol", "action",
            "recovery_fingerprint", "session_evidence",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        if self.version != MOCK_AUTOMATION_DECISION_GATE_VERSION:
            raise ValueError("unsupported mock automation decision gate")
        if not self.action:
            raise ValueError("mock automation gate action is required")
        if self.quantity < 0 or self.limit_price <= 0 or self.in_flight_order_count < 0:
            raise ValueError("mock automation order quantity must be non-negative and price positive")
        _require_aware(self.decided_at, "decided_at")
        _require_aware(self.observed_at, "observed_at")
        _require_aware(self.account_as_of, "account_as_of")
        if self.status is MockAutomationGateStatus.APPROVED_FOR_SINGLE_SUBMISSION and self.reasons:
            raise ValueError("approved mock automation gate cannot contain blocking reasons")
        if self.status is MockAutomationGateStatus.APPROVED_FOR_SINGLE_SUBMISSION and (
            self.action not in {"ENTER", "EXIT"} or self.quantity <= 0
        ):
            raise ValueError("only actionable positive-quantity Decisions may be approved")

    def to_dict(self) -> dict[str, Any]:
        return {
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
            "recovery_fingerprint": self.recovery_fingerprint,
            "session_evidence": self.session_evidence,
            "daily_net_pnl_won": self.daily_net_pnl_won,
            "daily_net_pnl_source": self.daily_net_pnl_source,
            "data_gap_seconds": self.data_gap_seconds,
            "submission_unknown_count": self.submission_unknown_count,
            "reconnect_count": self.reconnect_count,
            "balance_mismatch_count": self.balance_mismatch_count,
            "in_flight_order_count": self.in_flight_order_count,
            "status": self.status.value,
            "reasons": list(self.reasons),
        }


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
    def save_mock_automation_spec(self, value: MockAutomationOperatingSpec) -> bool: ...
    def load_mock_automation_admissions(
        self, account_ref: str,
    ) -> tuple[MockAutomationAdmission, ...]: ...
    def load_mock_automation_lease_receipts(
        self, account_ref: str,
    ) -> tuple[MockAutomationLeaseReceipt, ...]: ...
    def load_mock_automation_recovery_decisions(
        self, account_ref: str,
    ) -> tuple[MockAutomationRecoveryDecision, ...]: ...
    def save_mock_automation_decision_gate(self, value: MockAutomationDecisionGate) -> bool: ...
    def save_mock_automation_dispatch_receipt(
        self, value: MockAutomationDispatchReceipt,
    ) -> bool: ...
    def load_mock_automation_dispatch_receipts(
        self, account_ref: str,
    ) -> tuple[MockAutomationDispatchReceipt, ...]: ...
    def load_mock_automation_decision_gates(
        self, account_ref: str,
    ) -> tuple[MockAutomationDecisionGate, ...]: ...
    def save_mock_automation_stop_revision(self, value: MockAutomationStopRevision) -> bool: ...
    def load_mock_automation_stop_revisions(
        self, account_ref: str,
    ) -> tuple[MockAutomationStopRevision, ...]: ...


class ClosedGateMockRuntime(Protocol):
    account_ref: str
    run_id: str

    def heartbeat(self) -> None: ...
    def set_new_orders_enabled(self, enabled: bool) -> None: ...
    def automation_decision_guard(self) -> AbstractContextManager[None]: ...
    def load_intent(self, intent_id: str) -> ExecutionRecord | None: ...
    def submit_automation_intent(
        self, intent: OrderIntent, account: AccountSnapshot, *, reference_price: int | None = None,
    ) -> ExecutionRecord: ...


def mock_automation_intent_id(spec_id: str, strategy_decision_id: str) -> str:
    if not spec_id.strip() or not strategy_decision_id.strip():
        raise ValueError("spec_id and strategy_decision_id are required")
    digest = hashlib.sha256(f"{spec_id}:{strategy_decision_id}".encode("utf-8")).hexdigest()
    return f"mock_auto_intent_{digest}"


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
    in_flight_order_count: int = 0,
) -> MockAutomationDecisionGate:
    _validate_decision_identity(decision)
    _validate_lineage(spec, admission, lease_receipt, recovery_decision, recovery)
    decided_at = datetime.fromisoformat(decision.decided_at)
    intent_id = mock_automation_intent_id(spec.spec_id, decision.decision_id)
    reasons: list[str] = []
    if strategy_ref != spec.strategy_ref:
        reasons.append("STRATEGY_REF_MISMATCH")
    if decision.run_id != admission.execution_run_id:
        reasons.append("EXECUTION_RUN_MISMATCH")
    if recovery_decision.status is not MockAutomationRecoveryStatus.CLEARED_ORDERS_DISABLED:
        reasons.append("LATEST_RECOVERY_NOT_CLEARED")
    if metrics.observed_at < recovery_decision.observed_at:
        reasons.append("LIVE_OBSERVATION_PREDATES_RECOVERY")
    if latest_stop is not None and latest_stop.stopped_at >= recovery_decision.observed_at:
        reasons.append("EMERGENCY_STOP_REQUIRES_NEW_RECOVERY")
    if not metrics.recovery_complete:
        reasons.append("BROKER_RECOVERY_INCOMPLETE")
    if metrics.daily_net_pnl_won is None:
        reasons.append("DAILY_NET_PNL_UNKNOWN")
    if metrics.daily_net_pnl_source != VERIFIED_DAILY_PNL_SOURCE:
        reasons.append("DAILY_NET_PNL_SOURCE_UNVERIFIED")
    if metrics.data_gap_seconds is None:
        reasons.append("DATA_GAP_UNKNOWN")
    if metrics.data_path != spec.data_path:
        reasons.append("DATA_PATH_MISMATCH")

    _append_limit_reason(
        reasons, metrics.daily_net_pnl_won, spec.maximum_daily_loss_won,
        lambda value, limit: value < -limit, "DAILY_LOSS_LIMIT_REACHED",
    )
    _append_limit_reason(
        reasons, metrics.data_gap_seconds, spec.maximum_data_gap_seconds,
        lambda value, limit: value > limit, "DATA_GAP_LIMIT_EXCEEDED",
    )
    for value, limit, reason in (
        (metrics.submission_unknown_count, spec.maximum_submission_unknown_count,
         "SUBMISSION_UNKNOWN_LIMIT_EXCEEDED"),
        (metrics.reconnect_count, spec.maximum_reconnect_count,
         "RECONNECT_LIMIT_EXCEEDED"),
        (metrics.balance_mismatch_count, spec.maximum_balance_mismatch_count,
         "BALANCE_MISMATCH_LIMIT_EXCEEDED"),
    ):
        _append_limit_reason(reasons, value, limit, lambda item, maximum: item > maximum, reason)

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
    if recovery.orders:
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
        if int(positions.get(decision.symbol, 0)) < decision.quantity:
            reasons.append("INSUFFICIENT_POSITION")
    else:
        reasons.append("NON_ACTION_DECISION")

    session = mock_order_entry_decision(
        metrics.observed_at,
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
        "recovery_fingerprint": mock_account_recovery_fingerprint(recovery),
        "session_evidence": session.evidence,
        "daily_net_pnl_won": metrics.daily_net_pnl_won,
        "daily_net_pnl_source": metrics.daily_net_pnl_source,
        "data_gap_seconds": metrics.data_gap_seconds,
        "submission_unknown_count": metrics.submission_unknown_count,
        "reconnect_count": metrics.reconnect_count,
        "balance_mismatch_count": metrics.balance_mismatch_count,
        "in_flight_order_count": in_flight_order_count,
        "status": (
            MockAutomationGateStatus.BLOCKED.value
            if unique_reasons else MockAutomationGateStatus.APPROVED_FOR_SINGLE_SUBMISSION.value
        ),
        "reasons": list(unique_reasons),
    }
    return MockAutomationDecisionGate(
        gate_id=_content_id("mock_automation_gate", body),
        decided_at=decided_at,
        observed_at=metrics.observed_at,
        account_as_of=recovery.account.as_of,
        status=MockAutomationGateStatus(body["status"]),
        reasons=unique_reasons,
        **{
            key: value for key, value in body.items()
            if key not in {"decided_at", "observed_at", "account_as_of", "status", "reasons"}
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
) -> tuple[MockAutomationDecisionGate, ExecutionRecord | None, MockAutomationDispatchReceipt | None]:
    spec, admission, lease, recovered, latest_stop = _load_context(
        repository, account_ref, spec_id,
    )
    if runtime.account_ref != account_ref or runtime.run_id != admission.execution_run_id:
        raise ValueError("mock automation runtime does not match its admission")
    runtime.set_new_orders_enabled(False)
    with runtime.automation_decision_guard():
        repository.save_mock_automation_spec(spec)  # Revalidate binding for every new Decision.
        _validate_decision_identity(decision)
        if strategy_ref != spec.strategy_ref:
            raise ValueError("strategy_ref does not match the admitted operating spec")
        if decision.run_id != admission.execution_run_id:
            raise ValueError("strategy Decision does not belong to the admitted execution run")
        if decision.final_action not in {"ENTER", "EXIT"}:
            gate = assess_mock_automation_decision(
                spec, admission, lease, recovered, decision, recovery, metrics,
                strategy_ref=strategy_ref, latest_stop=latest_stop,
            )
            repository.save_mock_automation_decision_gate(gate)
            return gate, None, None
        expected_intent = _intent_for_decision(spec, admission, decision)
        existing = runtime.load_intent(expected_intent.intent_id)
        approved_gates = tuple(
            gate for gate in repository.load_mock_automation_decision_gates(account_ref)
            if gate.admission_id == admission.admission_id
            and gate.status is MockAutomationGateStatus.APPROVED_FOR_SINGLE_SUBMISSION
        )
        if existing is not None:
            if existing.intent != expected_intent:
                raise ValueError("deterministic mock automation intent has conflicting content")
            matching = tuple(gate for gate in approved_gates if gate.intent_id == existing.intent.intent_id)
            if not matching:
                raise RuntimeError("existing automation intent has no approved decision gate")
            gate = matching[-1]
            receipt = _dispatch_receipt(gate, existing, metrics.observed_at)
            repository.save_mock_automation_dispatch_receipt(receipt)
            return gate, existing, receipt

        in_flight = 0
        for intent_id in dict.fromkeys(gate.intent_id for gate in approved_gates):
            record = runtime.load_intent(intent_id)
            if record is not None and record.state not in TERMINAL_ORDER_STATES:
                in_flight += 1
        gate = assess_mock_automation_decision(
            spec, admission, lease, recovered, decision, recovery, metrics,
            strategy_ref=strategy_ref, latest_stop=latest_stop,
            in_flight_order_count=in_flight,
        )
        repository.save_mock_automation_decision_gate(gate)
        if gate.status is MockAutomationGateStatus.BLOCKED:
            return gate, None, None

        record = runtime.submit_automation_intent(
            expected_intent, recovery.account, reference_price=decision.signal_reference_price,
        )
        receipt = _dispatch_receipt(gate, record, metrics.observed_at)
        repository.save_mock_automation_dispatch_receipt(receipt)
        return gate, record, receipt


def emergency_stop_mock_automation(
    repository: MockAutomationExecutionRepository,
    runtime: ClosedGateMockRuntime,
    *,
    account_ref: str,
    spec_id: str,
    stopped_at: datetime,
    reason: str,
) -> MockAutomationStopRevision:
    _require_aware(stopped_at, "stopped_at")
    if not reason.strip():
        raise ValueError("emergency stop reason is required")
    spec, admission, _, _, _ = _load_context(repository, account_ref, spec_id)
    if runtime.account_ref != account_ref or runtime.run_id != admission.execution_run_id:
        raise ValueError("mock automation runtime does not match its admission")
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
        recovery_fingerprint=str(value["recovery_fingerprint"]),
        session_evidence=str(value["session_evidence"]),
        daily_net_pnl_won=(int(value["daily_net_pnl_won"]) if value.get("daily_net_pnl_won") is not None else None),
        daily_net_pnl_source=(str(value["daily_net_pnl_source"]) if value.get("daily_net_pnl_source") is not None else None),
        data_gap_seconds=(int(value["data_gap_seconds"]) if value.get("data_gap_seconds") is not None else None),
        submission_unknown_count=int(value["submission_unknown_count"]),
        reconnect_count=int(value["reconnect_count"]),
        balance_mismatch_count=int(value["balance_mismatch_count"]),
        in_flight_order_count=int(value.get("in_flight_order_count", 0)),
        status=MockAutomationGateStatus(str(value["status"])),
        reasons=tuple(str(item) for item in value.get("reasons", ())),
    )
    _verify_content_id("mock_automation_gate", gate.gate_id, gate.to_dict(), "gate_id")
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
    admissions = tuple(item for item in repository.load_mock_automation_admissions(account_ref) if item.spec_id == spec_id)
    if len(admissions) != 1:
        raise ValueError("mock automation admission is missing or ambiguous")
    admission = admissions[0]
    leases = tuple(item for item in repository.load_mock_automation_lease_receipts(account_ref) if item.admission_id == admission.admission_id)
    if len(leases) != 1:
        raise ValueError("mock automation lease receipt is missing or ambiguous")
    recoveries = tuple(item for item in repository.load_mock_automation_recovery_decisions(account_ref) if item.admission_id == admission.admission_id)
    if not recoveries:
        raise ValueError("mock automation recovery decision is missing")
    recovery = max(recoveries, key=lambda item: (item.observed_at, item.decision_id))
    stops = tuple(item for item in repository.load_mock_automation_stop_revisions(account_ref) if item.admission_id == admission.admission_id)
    latest_stop = max(stops, key=lambda item: (item.stopped_at, item.revision_id)) if stops else None
    return spec, admission, leases[0], recovery, latest_stop


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
