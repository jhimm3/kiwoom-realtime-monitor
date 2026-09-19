"""Fail-closed broker recovery gate for an admitted automatic mock runtime."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping, Protocol

from kiwoom_monitor.application.mock_automation_admission import (
    MockAutomationAdmission,
    MockAutomationLeaseReceipt,
)
from kiwoom_monitor.domain.execution_activation import MockAutomationOperatingSpec
from kiwoom_monitor.domain.order_contract import TERMINAL_ORDER_STATES
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import MockAccountRecovery


MOCK_AUTOMATION_RECOVERY_VERSION = "mock_automation_recovery/v1"


class MockAutomationRecoveryStatus(StrEnum):
    BLOCKED = "BLOCKED"
    CLEARED_ORDERS_DISABLED = "CLEARED_ORDERS_DISABLED"


@dataclass(frozen=True)
class MockAutomationRecoveryMetrics:
    observed_at: datetime
    recovery_complete: bool
    daily_net_pnl_won: int | None
    data_gap_seconds: int | None
    submission_unknown_count: int
    reconnect_count: int
    balance_mismatch_count: int

    def __post_init__(self) -> None:
        _require_aware(self.observed_at, "observed_at")
        if type(self.recovery_complete) is not bool:
            raise ValueError("recovery_complete must be a boolean")
        if self.daily_net_pnl_won is not None and not isinstance(self.daily_net_pnl_won, int):
            raise ValueError("daily_net_pnl_won must be an integer or unknown")
        for name in (
            "data_gap_seconds", "submission_unknown_count", "reconnect_count",
            "balance_mismatch_count",
        ):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, int) or value < 0):
                raise ValueError(f"{name} must be a non-negative integer or unknown")


@dataclass(frozen=True)
class MockAutomationRecoveryDecision:
    decision_id: str
    version: str
    admission_id: str
    lease_receipt_id: str
    spec_id: str
    account_ref: str
    execution_run_id: str
    observed_at: datetime
    account_as_of: datetime
    recovery_fingerprint: str
    open_order_count: int
    position_count: int
    reserved_open_buy_won: int
    orderable_cash_won: int
    daily_net_pnl_won: int | None
    data_gap_seconds: int | None
    submission_unknown_count: int
    reconnect_count: int
    balance_mismatch_count: int
    status: MockAutomationRecoveryStatus
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "decision_id", "admission_id", "lease_receipt_id", "spec_id",
            "account_ref", "execution_run_id", "recovery_fingerprint",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        if self.version != MOCK_AUTOMATION_RECOVERY_VERSION:
            raise ValueError("unsupported mock automation recovery version")
        _require_sha256(self.recovery_fingerprint, "recovery_fingerprint")
        _require_aware(self.observed_at, "observed_at")
        _require_aware(self.account_as_of, "account_as_of")
        for name in (
            "open_order_count", "position_count", "reserved_open_buy_won",
            "submission_unknown_count", "reconnect_count", "balance_mismatch_count",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if not isinstance(self.orderable_cash_won, int):
            raise ValueError("orderable_cash_won must be an integer")
        if self.data_gap_seconds is not None and self.data_gap_seconds < 0:
            raise ValueError("data_gap_seconds must be non-negative when known")
        if self.status is MockAutomationRecoveryStatus.BLOCKED and not self.reasons:
            raise ValueError("blocked recovery decision requires reasons")
        if self.status is MockAutomationRecoveryStatus.CLEARED_ORDERS_DISABLED and self.reasons:
            raise ValueError("cleared recovery decision cannot have blocking reasons")

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "version": self.version,
            "admission_id": self.admission_id,
            "lease_receipt_id": self.lease_receipt_id,
            "spec_id": self.spec_id,
            "account_ref": self.account_ref,
            "execution_run_id": self.execution_run_id,
            "observed_at": self.observed_at.isoformat(),
            "account_as_of": self.account_as_of.isoformat(),
            "recovery_fingerprint": self.recovery_fingerprint,
            "open_order_count": self.open_order_count,
            "position_count": self.position_count,
            "reserved_open_buy_won": self.reserved_open_buy_won,
            "orderable_cash_won": self.orderable_cash_won,
            "daily_net_pnl_won": self.daily_net_pnl_won,
            "data_gap_seconds": self.data_gap_seconds,
            "submission_unknown_count": self.submission_unknown_count,
            "reconnect_count": self.reconnect_count,
            "balance_mismatch_count": self.balance_mismatch_count,
            "status": self.status.value,
            "reasons": list(self.reasons),
        }


class MockAutomationRecoveryRepository(Protocol):
    def load_mock_automation_spec(
        self, account_ref: str, spec_id: str,
    ) -> MockAutomationOperatingSpec | None: ...
    def load_mock_automation_admissions(
        self, account_ref: str,
    ) -> tuple[MockAutomationAdmission, ...]: ...
    def load_mock_automation_lease_receipts(
        self, account_ref: str,
    ) -> tuple[MockAutomationLeaseReceipt, ...]: ...
    def save_mock_automation_recovery_decision(
        self, value: MockAutomationRecoveryDecision,
    ) -> bool: ...


class DisabledOwnedRuntime(Protocol):
    account_ref: str
    run_id: str

    def set_new_orders_enabled(self, enabled: bool) -> None: ...
    def heartbeat(self) -> None: ...


def assess_mock_automation_recovery(
    spec: MockAutomationOperatingSpec,
    admission: MockAutomationAdmission,
    receipt: MockAutomationLeaseReceipt,
    recovery: MockAccountRecovery,
    metrics: MockAutomationRecoveryMetrics,
) -> MockAutomationRecoveryDecision:
    _validate_lineage(spec, admission, receipt, recovery)
    account = recovery.account
    open_orders = tuple(
        order for order in recovery.orders if order.state not in TERMINAL_ORDER_STATES
    )
    positions = tuple(
        symbol for symbol, quantity in account.positions.items() if int(quantity) > 0
    )
    reasons: list[str] = []
    if not metrics.recovery_complete:
        reasons.append("BROKER_RECOVERY_INCOMPLETE")
    if metrics.daily_net_pnl_won is None:
        reasons.append("DAILY_NET_PNL_UNKNOWN")
    elif (
        spec.maximum_daily_loss_won is not None
        and metrics.daily_net_pnl_won < -spec.maximum_daily_loss_won
    ):
        reasons.append("DAILY_LOSS_LIMIT_REACHED")
    if metrics.data_gap_seconds is None:
        reasons.append("DATA_GAP_UNKNOWN")
    elif (
        spec.maximum_data_gap_seconds is not None
        and metrics.data_gap_seconds > spec.maximum_data_gap_seconds
    ):
        reasons.append("DATA_GAP_LIMIT_EXCEEDED")
    for name, actual, limit, reason in (
        (
            "submission_unknown_count", metrics.submission_unknown_count,
            spec.maximum_submission_unknown_count, "SUBMISSION_UNKNOWN_LIMIT_EXCEEDED",
        ),
        (
            "reconnect_count", metrics.reconnect_count,
            spec.maximum_reconnect_count, "RECONNECT_LIMIT_EXCEEDED",
        ),
        (
            "balance_mismatch_count", metrics.balance_mismatch_count,
            spec.maximum_balance_mismatch_count, "BALANCE_MISMATCH_LIMIT_EXCEEDED",
        ),
    ):
        if limit is None:
            reasons.append(f"{name.upper()}_LIMIT_UNKNOWN")
        elif actual > limit:
            reasons.append(reason)
    if open_orders:
        reasons.append("BROKER_OPEN_ORDER_PRESENT")
    if positions:
        reasons.append("BROKER_POSITION_PRESENT")
    if account.reserved_open_buy_won > 0:
        reasons.append("BROKER_RESERVED_BUY_PRESENT")
    orderable_cash = account.available_cash_won - account.reserved_open_buy_won
    if orderable_cash < 0:
        reasons.append("BROKER_CASH_INCONSISTENT")
    if account.as_of > metrics.observed_at:
        reasons.append("BROKER_ACCOUNT_CLOCK_AHEAD")
    elif (
        spec.maximum_data_gap_seconds is not None
        and int((metrics.observed_at - account.as_of).total_seconds())
        > spec.maximum_data_gap_seconds
    ):
        reasons.append("BROKER_ACCOUNT_SNAPSHOT_STALE")
    if any(order.as_of > metrics.observed_at for order in recovery.orders):
        reasons.append("BROKER_ORDER_CLOCK_AHEAD")
    elif recovery.orders and spec.maximum_data_gap_seconds is not None and any(
        int((metrics.observed_at - order.as_of).total_seconds())
        > spec.maximum_data_gap_seconds
        for order in recovery.orders
    ):
        reasons.append("BROKER_ORDER_SNAPSHOT_STALE")

    unique_reasons = tuple(dict.fromkeys(reasons))
    document = {
        "version": MOCK_AUTOMATION_RECOVERY_VERSION,
        "admission_id": admission.admission_id,
        "lease_receipt_id": receipt.receipt_id,
        "spec_id": spec.spec_id,
        "account_ref": account.account_ref,
        "execution_run_id": admission.execution_run_id,
        "observed_at": metrics.observed_at.isoformat(),
        "account_as_of": account.as_of.isoformat(),
        "recovery_fingerprint": mock_account_recovery_fingerprint(recovery),
        "open_order_count": len(open_orders),
        "position_count": len(positions),
        "reserved_open_buy_won": account.reserved_open_buy_won,
        "orderable_cash_won": orderable_cash,
        "daily_net_pnl_won": metrics.daily_net_pnl_won,
        "data_gap_seconds": metrics.data_gap_seconds,
        "submission_unknown_count": metrics.submission_unknown_count,
        "reconnect_count": metrics.reconnect_count,
        "balance_mismatch_count": metrics.balance_mismatch_count,
        "status": (
            MockAutomationRecoveryStatus.BLOCKED.value
            if unique_reasons
            else MockAutomationRecoveryStatus.CLEARED_ORDERS_DISABLED.value
        ),
        "reasons": list(unique_reasons),
    }
    return MockAutomationRecoveryDecision(
        decision_id=_content_id("mock_automation_recovery", document),
        observed_at=metrics.observed_at,
        account_as_of=account.as_of,
        status=MockAutomationRecoveryStatus(document["status"]),
        reasons=unique_reasons,
        **{
            key: value for key, value in document.items()
            if key not in {"observed_at", "account_as_of", "status", "reasons"}
        },
    )


def record_mock_automation_recovery(
    repository: MockAutomationRecoveryRepository,
    runtime: DisabledOwnedRuntime,
    recovery: MockAccountRecovery,
    metrics: MockAutomationRecoveryMetrics,
    *,
    account_ref: str,
    spec_id: str,
) -> MockAutomationRecoveryDecision:
    spec = repository.load_mock_automation_spec(account_ref, spec_id)
    if spec is None:
        raise ValueError("mock automation operating spec is not stored")
    admissions = tuple(
        item for item in repository.load_mock_automation_admissions(account_ref)
        if item.spec_id == spec_id
    )
    if len(admissions) != 1:
        raise ValueError("mock automation admission is missing or ambiguous")
    admission = admissions[0]
    receipts = tuple(
        item for item in repository.load_mock_automation_lease_receipts(account_ref)
        if item.admission_id == admission.admission_id
    )
    if len(receipts) != 1:
        raise ValueError("mock automation lease receipt is missing or ambiguous")
    if runtime.account_ref != account_ref or runtime.run_id != admission.execution_run_id:
        raise ValueError("mock automation runtime does not match its admission")
    runtime.set_new_orders_enabled(False)
    runtime.heartbeat()
    decision = assess_mock_automation_recovery(
        spec, admission, receipts[0], recovery, metrics,
    )
    repository.save_mock_automation_recovery_decision(decision)
    return decision


def mock_automation_recovery_decision_from_dict(
    value: Mapping[str, Any],
) -> MockAutomationRecoveryDecision:
    decision = MockAutomationRecoveryDecision(
        decision_id=str(value["decision_id"]),
        version=str(value["version"]),
        admission_id=str(value["admission_id"]),
        lease_receipt_id=str(value["lease_receipt_id"]),
        spec_id=str(value["spec_id"]),
        account_ref=str(value["account_ref"]),
        execution_run_id=str(value["execution_run_id"]),
        observed_at=datetime.fromisoformat(str(value["observed_at"])),
        account_as_of=datetime.fromisoformat(str(value["account_as_of"])),
        recovery_fingerprint=str(value["recovery_fingerprint"]),
        open_order_count=int(value["open_order_count"]),
        position_count=int(value["position_count"]),
        reserved_open_buy_won=int(value["reserved_open_buy_won"]),
        orderable_cash_won=int(value["orderable_cash_won"]),
        daily_net_pnl_won=(
            int(value["daily_net_pnl_won"])
            if value.get("daily_net_pnl_won") is not None else None
        ),
        data_gap_seconds=(
            int(value["data_gap_seconds"])
            if value.get("data_gap_seconds") is not None else None
        ),
        submission_unknown_count=int(value["submission_unknown_count"]),
        reconnect_count=int(value["reconnect_count"]),
        balance_mismatch_count=int(value["balance_mismatch_count"]),
        status=MockAutomationRecoveryStatus(str(value["status"])),
        reasons=tuple(str(item) for item in value.get("reasons", ())),
    )
    expected = _content_id("mock_automation_recovery", {
        key: item for key, item in decision.to_dict().items() if key != "decision_id"
    })
    if decision.decision_id != expected:
        raise ValueError("mock automation recovery content does not match decision_id")
    return decision


def _validate_lineage(
    spec: MockAutomationOperatingSpec,
    admission: MockAutomationAdmission,
    receipt: MockAutomationLeaseReceipt,
    recovery: MockAccountRecovery,
) -> None:
    if (
        admission.spec_id != spec.spec_id
        or admission.account_ref != spec.account_scope.account_ref
        or receipt.admission_id != admission.admission_id
        or receipt.spec_id != spec.spec_id
        or receipt.account_ref != admission.account_ref
        or receipt.execution_run_id != admission.execution_run_id
        or receipt.new_orders_enabled is not False
        or recovery.account.account_ref != admission.account_ref
        or any(order.account_ref != admission.account_ref for order in recovery.orders)
    ):
        raise ValueError("mock automation recovery lineage is invalid")


def mock_account_recovery_fingerprint(recovery: MockAccountRecovery) -> str:
    """Return the stable broker-state identity used by later automation gates."""
    document = {
        "account": {
            "account_ref": recovery.account.account_ref,
            "available_cash_won": recovery.account.available_cash_won,
            "reserved_open_buy_won": recovery.account.reserved_open_buy_won,
            "positions": sorted(
                (str(symbol), int(quantity))
                for symbol, quantity in recovery.account.positions.items()
            ),
            "as_of": recovery.account.as_of.isoformat(),
        },
        "orders": sorted((
            order.broker_order_id,
            order.symbol,
            order.state.value,
            order.filled_quantity,
            order.remaining_quantity,
            order.as_of.isoformat(),
        ) for order in recovery.orders),
    }
    encoded = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _content_id(prefix: str, value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()}"


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _require_sha256(value: str, name: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
