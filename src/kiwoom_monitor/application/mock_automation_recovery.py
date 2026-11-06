"""Fail-closed broker recovery gate for an admitted automatic mock runtime."""

from __future__ import annotations

import hashlib
import json
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping, Protocol, runtime_checkable

from kiwoom_monitor.application.mock_automation_admission import (
    MockAutomationAdmission,
    MockAutomationLeaseReceipt,
)
from kiwoom_monitor.domain.execution_activation import MockAutomationOperatingSpec
from kiwoom_monitor.domain.order_contract import TERMINAL_ORDER_STATES
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import MockAccountRecovery
from kiwoom_monitor.application.mock_automation_risk import MockAutomationRiskSnapshot


MOCK_AUTOMATION_RECOVERY_VERSION = "mock_automation_recovery/v1"
MOCK_AUTOMATION_ACCOUNT_RECOVERY_VERSION = "mock_automation_account_recovery/v1"


class MockAutomationRecoveryStatus(StrEnum):
    BLOCKED = "BLOCKED"
    CLEARED_ORDERS_DISABLED = "CLEARED_ORDERS_DISABLED"
    MANAGE_ONLY_ORDERS_DISABLED = "MANAGE_ONLY_ORDERS_DISABLED"


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
    risk_snapshot_id: str = ""
    risk_reconciliation_revision: int = 0

    def __post_init__(self) -> None:
        for name in (
            "decision_id", "admission_id", "lease_receipt_id", "spec_id",
            "account_ref", "execution_run_id", "recovery_fingerprint",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        expected = (MOCK_AUTOMATION_ACCOUNT_RECOVERY_VERSION
                    if isinstance(self, MockAutomationAccountRecoveryDecision) else MOCK_AUTOMATION_RECOVERY_VERSION)
        if self.version != expected:
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
        if bool(self.risk_snapshot_id) != (self.risk_reconciliation_revision > 0):
            raise ValueError("risk snapshot identity and revision must be provided together")

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
            "risk_snapshot_id": self.risk_snapshot_id,
            "risk_reconciliation_revision": self.risk_reconciliation_revision,
        }


@dataclass(frozen=True)
class MockAutomationAccountRecoveryDecision(MockAutomationRecoveryDecision):
    """Member occupancy bound to the unchanged complete account recovery cut."""
    account_selection_id: str = ""
    member_spec_ids: tuple[str, ...] = ()
    own_position_quantities: tuple[tuple[str, int], ...] = ()
    own_open_order_ids: tuple[str, ...] = ()
    other_member_open_order_ids: tuple[str, ...] = ()
    observation_input_ref: str = ""

    def __post_init__(self):
        super().__post_init__()
        if (not self.account_selection_id or not self.risk_snapshot_id
                or type(self.member_spec_ids) is not tuple or self.spec_id not in self.member_spec_ids
                or len(self.member_spec_ids) != len(set(self.member_spec_ids))):
            raise ValueError('invalid account-member recovery lineage')
        if (type(self.own_position_quantities) is not tuple
                or tuple(sorted(self.own_position_quantities)) != self.own_position_quantities
                or len({row[0] for row in self.own_position_quantities}) != len(self.own_position_quantities)
                or any(type(row) is not tuple or len(row) != 2 or not isinstance(row[0], str)
                       or type(row[1]) is not int or row[1] <= 0 for row in self.own_position_quantities)):
            raise ValueError('invalid account-member recovery position facts')
        for ids in (self.own_open_order_ids, self.other_member_open_order_ids):
            if (type(ids) is not tuple or tuple(sorted(set(ids))) != ids
                    or any(type(value) is not str or not value for value in ids)):
                raise ValueError('invalid account-member recovery broker order facts')
        if set(self.own_open_order_ids) & set(self.other_member_open_order_ids):
            raise ValueError('account-member recovery broker order overlaps')
        if self.status is not MockAutomationRecoveryStatus.BLOCKED and not self.observation_input_ref:
            raise ValueError('account-member recovery requires its complete native observation')

    def to_dict(self):
        return {**super().to_dict(), 'account_selection_id': self.account_selection_id,
            'member_spec_ids': list(self.member_spec_ids),
            'own_position_quantities': [list(row) for row in self.own_position_quantities],
            'own_open_order_ids': list(self.own_open_order_ids),
            'other_member_open_order_ids': list(self.other_member_open_order_ids),
            'observation_input_ref': self.observation_input_ref}


class MockAutomationRecoveryRepository(Protocol):
    def load_mock_automation_spec(
        self, account_ref: str, spec_id: str,
    ) -> MockAutomationOperatingSpec | None: ...
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
    def save_mock_automation_recovery_decision(
        self, value: MockAutomationRecoveryDecision,
    ) -> bool: ...
    def load_latest_mock_automation_risk(
        self, account_ref: str, *, _reader: Any = None,
    ) -> MockAutomationRiskSnapshot | None: ...


class DisabledOwnedRuntime(Protocol):
    account_ref: str
    run_id: str

    def set_new_orders_enabled(self, enabled: bool) -> None: ...
    def heartbeat(self) -> None: ...
    def automation_decision_guard(self) -> AbstractContextManager[None]: ...


@runtime_checkable
class MockAutomationAccountObservation(Protocol):
    """The existing monitor's full account/physical-order projection contract."""
    account: Any
    open_orders: tuple
    broker_open_orders: tuple | None
    input_ref: str
    observed_at: datetime
    reconciliation_complete: bool
    open_orders_complete: bool
    reasons: tuple[str, ...]
    unmapped_broker_order_ids: tuple[str, ...]
    risk_snapshot: MockAutomationRiskSnapshot | None


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
        and metrics.daily_net_pnl_won <= -spec.maximum_daily_loss_won
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

    manage_only_reasons = {
        "DAILY_NET_PNL_UNKNOWN", "DAILY_LOSS_LIMIT_REACHED",
        "DATA_GAP_UNKNOWN", "DATA_GAP_LIMIT_EXCEEDED",
        "RECONNECT_LIMIT_EXCEEDED",
    }
    unique_reasons = tuple(dict.fromkeys(reasons))
    manage_only = bool(positions) and set(unique_reasons).issubset(manage_only_reasons)
    if positions and not manage_only:
        unique_reasons = tuple(dict.fromkeys((*unique_reasons, "BROKER_POSITION_PRESENT")))
    status = (
        MockAutomationRecoveryStatus.BLOCKED
        if unique_reasons and not manage_only
        else MockAutomationRecoveryStatus.MANAGE_ONLY_ORDERS_DISABLED
        if positions
        else MockAutomationRecoveryStatus.CLEARED_ORDERS_DISABLED
    )
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
        "status": status.value,
        "reasons": list(unique_reasons),
        "risk_snapshot_id": "",
        "risk_reconciliation_revision": 0,
    }
    return MockAutomationRecoveryDecision(
        decision_id=_content_id("mock_automation_recovery", document),
        observed_at=metrics.observed_at,
        account_as_of=account.as_of,
        status=status,
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
    metrics: MockAutomationRecoveryMetrics | MockAutomationRiskSnapshot,
    *,
    account_ref: str,
    spec_id: str,
    risk_snapshot_id: str = "",
    risk_reconciliation_revision: int = 0,
    observation=None,
) -> MockAutomationRecoveryDecision:
    scope = getattr(repository, "mock_automation_read_scope", None)
    with (scope() if callable(scope) else nullcontext()) as reader:
        reader_args = {} if reader is None else {"_reader": reader}
        current = (repository.load_latest_mock_automation_risk(account_ref, **reader_args)
                   if isinstance(metrics, MockAutomationRiskSnapshot) else None)
        inputs = _recovery_inputs(repository, runtime, recovery, metrics, account_ref, spec_id,
                                  reader_args, current_risk=current)
    spec, admission, receipt, metrics, risk, selection = inputs
    if risk is not None:
        risk_snapshot_id, risk_reconciliation_revision = risk.snapshot_id, risk.reconciliation_revision
    runtime.set_new_orders_enabled(False)
    with runtime.automation_decision_guard():
        decision = _recovery_decision(inputs, recovery, observation, risk_snapshot_id, risk_reconciliation_revision)
        if (getattr(repository, 'selected_management_context', None) is not None and risk is not None
                and decision.status is MockAutomationRecoveryStatus.BLOCKED and decision.reasons
                and set(decision.reasons) <= {
                    'BROKER_OPEN_ORDER_PRESENT', 'BROKER_RESERVED_BUY_PRESENT', 'BROKER_POSITION_PRESENT'}):
            # These are the preserved plan's occupancy facts, not permission to
            # enter. Native acceptance independently checks the full current
            # physical ledger, Risk, source policy and single canonical owner.
            status = MockAutomationRecoveryStatus.MANAGE_ONLY_ORDERS_DISABLED
            body = {key: value for key, value in decision.to_dict().items() if key != 'decision_id'}
            body['status'] = status.value
            decision = MockAutomationRecoveryDecision(**{**decision.__dict__, 'status': status,
                'decision_id': _content_id('mock_automation_recovery', body)})
        repository.save_mock_automation_recovery_decision(decision)
    return decision


def record_mock_automation_recovery_from_risk(
    repository: MockAutomationRecoveryRepository,
    runtime: DisabledOwnedRuntime,
    recovery: MockAccountRecovery,
    risk: MockAutomationRiskSnapshot,
    *,
    account_ref: str,
    spec_id: str,
    observation=None,
) -> MockAutomationRecoveryDecision:
    """Record recovery only from the latest immutable NAS risk revision."""
    if not isinstance(risk, MockAutomationRiskSnapshot):
        raise RuntimeError("latest mock automation risk snapshot changed")
    return record_mock_automation_recovery(
        repository, runtime, recovery, risk,
        account_ref=account_ref, spec_id=spec_id, observation=observation,
    )


def _recovery_inputs(repository, runtime, recovery, metrics, account_ref, spec_id, reader_args,
                     *, current_risk=None, control=None, selected_account=None):
    from .mock_automation_admission import MOCK_AUTOMATION_ACCOUNT_ADMISSION_VERSION
    risk = metrics if isinstance(metrics, MockAutomationRiskSnapshot) else None
    if risk is not None:
        if current_risk != risk:
            raise RuntimeError('latest mock automation risk snapshot changed')
        _validate_risk_snapshot(risk, recovery, account_ref, runtime.run_id)
        metrics = MockAutomationRecoveryMetrics(risk.observed_at, risk.reconciliation_complete,
            risk.daily_net_pnl_won, risk.data_gap_seconds, risk.submission_unknown_count,
            risk.reconnect_count, risk.balance_mismatch_count)
    spec = repository.load_mock_automation_spec(account_ref, spec_id, **reader_args)
    if spec is None:
        raise ValueError('mock automation operating spec is not stored')
    admission = repository.load_mock_automation_admission_for_spec(account_ref, spec_id, **reader_args)
    if admission is None:
        raise ValueError('mock automation admission is missing')
    receipt = repository.load_mock_automation_lease_for_admission(account_ref, admission.admission_id, **reader_args)
    if receipt is None:
        raise ValueError('mock automation lease receipt is missing')
    if runtime.account_ref != account_ref or runtime.run_id != admission.execution_run_id:
        raise ValueError('mock automation runtime does not match its admission')
    management = getattr(repository, 'selected_management_context', None)
    if management is not None:
        from .mock_automation_risk import mock_automation_expected_binding_revision
        if (risk is None or risk.binding_revision != mock_automation_expected_binding_revision(spec, management)
                or (management.spec_id, management.admission_id, management.execution_run_id)
                != (spec_id, admission.admission_id, runtime.run_id)):
            raise ValueError('selected management recovery scope or current binding changed')
    selection = None
    if management is None and (admission.version == MOCK_AUTOMATION_ACCOUNT_ADMISSION_VERSION or selected_account is not None
            or risk is not None and risk.member_attribution is not None):
        if risk is None:
            raise ValueError('account-member recovery requires its canonical Risk snapshot')
        control = control or repository.load_mock_automation_control(account_ref, **reader_args)
        selection = selected_account or (control.account_selection if control is not None else None)
        if (selection is None or not selection.matches_admission(spec_id=spec_id,
                    admission_id=admission.admission_id,
                    original_selection_id=getattr(admission, 'account_selection_id', None),
                    execution_run_id=admission.execution_run_id)
                or spec_id not in selection.member_spec_ids or control.execution_run_id != runtime.run_id
                or risk.binding_revision != selection.binding_revision
                or spec.binding_revision != selection.binding_revision):
            raise ValueError('account-member recovery selection or binding changed')
        if selection.allows_entry(spec_id):
            profile = repository.load_profile(spec.strategy_ref, spec.forward_profile_id, **reader_args)
            repository.revalidate_stored_mock_automation_spec(spec, profile, **reader_args)
    return spec, admission, receipt, metrics, risk, selection


def _recovery_decision(inputs, recovery, observation, risk_snapshot_id='', risk_revision=0):
    spec, admission, receipt, metrics, risk, selection = inputs
    decision = assess_mock_automation_recovery(spec, admission, receipt, recovery, metrics)
    if selection is not None:
        return _account_member_recovery(decision, recovery, risk, observation, selection)
    if not risk_snapshot_id:
        return decision
    body = {**{key: value for key, value in decision.to_dict().items() if key != 'decision_id'},
            'risk_snapshot_id': risk_snapshot_id, 'risk_reconciliation_revision': risk_revision}
    return MockAutomationRecoveryDecision(**{**decision.__dict__,
        'decision_id': _content_id('mock_automation_recovery', body),
        'risk_snapshot_id': risk_snapshot_id, 'risk_reconciliation_revision': risk_revision})


def record_mock_automation_account_recovery_from_cut(repository, runtime, cut, *, account_ref, selection_id=None,
                                                      prospective_selection=None, expected_control_revision=None):
    """Record every selected member from one existing monitor cut and Risk read.

    Each immutable save still checks its current Risk in the native writer.
    A partial/lost acknowledgement grants no account resume: consumers must
    require all selected decisions to reference this same canonical cut.
    """
    if not isinstance(cut, dict) or not {'risk', 'observation', 'recovery'} <= set(cut):
        raise ValueError('account recovery requires the complete native monitor cut')
    scope = getattr(repository, 'mock_automation_read_scope', None)
    with (scope() if callable(scope) else nullcontext()) as reader:
        args = {} if reader is None else {'_reader': reader}
        control = repository.load_mock_automation_control(account_ref, **args)
        selection = control.account_selection if control is not None else None
        if prospective_selection is not None:
            from kiwoom_monitor.domain.execution_activation import MockAutomationAccountSelectionV2
            desired = prospective_selection
            if (not isinstance(desired, MockAutomationAccountSelectionV2) or control is None
                    or type(expected_control_revision) is not int
                    or control.control_revision != expected_control_revision
                    or control.execution_run_id != runtime.run_id
                    or control.active_spec_id != desired.primary_spec_id
                    or desired.account_scope.account_ref != account_ref
                    or repository.load_mock_automation_account_selection(account_ref, desired.selection_id, **args) != desired):
                raise ValueError('prospective account recovery selection or control changed')
            previous_ids = selection.member_spec_ids if selection is not None else (control.active_spec_id,)
            for identity in previous_ids:
                original = repository.load_mock_automation_admission_for_spec(account_ref, identity, **args)
                retained = desired.retained_admission_ref(identity)
                if (original is None or retained is None or not desired.matches_admission(
                        spec_id=identity, admission_id=original.admission_id,
                        original_selection_id=getattr(original, 'account_selection_id', None),
                        execution_run_id=original.execution_run_id)):
                    raise ValueError('prospective account recovery lost an original admission')
            selection = desired
        elif expected_control_revision is not None:
            raise ValueError('prospective account recovery requires its stored selection')
        if selection is None or selection_id is not None and selection.selection_id != selection_id:
            raise ValueError('account recovery selection changed')
        current = repository.load_latest_mock_automation_risk(account_ref, **args)
        inputs = tuple(_recovery_inputs(repository, runtime, cut['recovery'], cut['risk'], account_ref,
            spec_id, args, current_risk=current, control=control, selected_account=selection)
            for spec_id in selection.member_spec_ids)
    runtime.set_new_orders_enabled(False)
    with runtime.automation_decision_guard():
        values = tuple(_recovery_decision(value, cut['recovery'], cut['observation']) for value in inputs)
        for value in values:
            repository.save_mock_automation_recovery_decision(value)
    return values


def _account_member_recovery(base, recovery, risk, observation, selection):
    from kiwoom_monitor.domain.order_contract import OrderSide, OrderState
    reasons, own_ids, other_ids = [], [], []
    own_positions = tuple(sorted(risk.owned_for_member(base.spec_id)))
    valid = (isinstance(observation, MockAutomationAccountObservation)
        and observation.account == recovery.account and observation.risk_snapshot == risk
        and observation.reconciliation_complete is True and observation.open_orders_complete is True
        and type(observation.open_orders) is tuple and type(observation.broker_open_orders) is tuple
        and type(observation.reasons) is tuple and type(observation.unmapped_broker_order_ids) is tuple
        and type(observation.input_ref) is str and bool(observation.input_ref)
        and not observation.unmapped_broker_order_ids and not observation.reasons
        and observation.observed_at >= risk.observed_at)
    if not valid:
        reasons.append('ACCOUNT_MEMBER_OBSERVATION_INCOMPLETE')
    if (risk.member_attribution == 'INCOMPLETE'
            or risk.member_attribution != 'COMPLETE' and risk.owned_position_quantities
            or any(member not in selection.member_spec_ids for member, *_ in risk.member_positions)):
        reasons.append('ACCOUNT_MEMBER_ATTRIBUTION_INCOMPLETE')
    expected = {}
    reserved = 0
    if valid:
        records = {}
        for record in observation.open_orders:
            member = getattr(record, 'member_spec_id', None)
            if (member not in selection.member_spec_ids or record.intent.run_id != base.execution_run_id
                    or record.intent.account_ref != base.account_ref
                    or record.state not in {OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED}
                    or record.amendment is not None and record.amendment.reconciliation_pending):
                reasons.append('ACCOUNT_MEMBER_ORDER_UNCONFIRMED')
                continue
            if record.intent.intent_id in records:
                reasons.append('ACCOUNT_MEMBER_ORDER_COVERAGE_CHANGED')
            records[record.intent.intent_id] = record
        covered = set()
        # Reuse the actual monitor's physical projection; do not invent a
        # broker order, quantity, side or price when that projection is absent.
        for exposure in observation.broker_open_orders:
            record = records.get(exposure.intent_ref)
            if record is None:
                reasons.append('ACCOUNT_MEMBER_ORDER_UNCONFIRMED')
                continue
            legs = {leg.broker_order_id: leg for leg in record.broker_legs}
            leg = legs.get(exposure.broker_order_id)
            if record.broker_legs:
                matches = (leg is not None and (leg.state, leg.ordered_quantity, leg.limit_price,
                    leg.filled_quantity, leg.remaining_quantity) == (exposure.state, exposure.ordered_quantity,
                    exposure.limit_price, exposure.filled_quantity, exposure.remaining_quantity))
                detailed = leg.detailed_filled_quantity if leg is not None else -1
                reported = leg.broker_reported_filled_quantity if leg is not None else -1
            else:
                # The original unamended native record has one physical ID;
                # its exact requested and accepted FILL totals remain intact.
                matches = ((record.broker_order_id, record.state, record.intent.quantity, record.intent.limit_price,
                    record.filled_quantity, record.intent.quantity - record.filled_quantity)
                    == (exposure.broker_order_id, exposure.state, exposure.ordered_quantity, exposure.limit_price,
                        exposure.filled_quantity, exposure.remaining_quantity))
                detailed, reported = record.detailed_filled_quantity, record.broker_reported_filled_quantity
            if (not matches or exposure.account_ref != base.account_ref or exposure.symbol != record.intent.symbol
                    or exposure.side is not record.intent.side or exposure.remaining_quantity <= 0
                    or exposure.broker_order_id in expected):
                reasons.append('ACCOUNT_MEMBER_ORDER_COVERAGE_CHANGED')
            expected[exposure.broker_order_id] = (exposure.symbol, exposure.remaining_quantity, detailed, reported)
            covered.add(record.intent.intent_id)
            (own_ids if record.member_spec_id == base.spec_id else other_ids).append(exposure.broker_order_id)
            if exposure.side is OrderSide.BUY:
                reserved += exposure.remaining_quantity * exposure.limit_price
        if covered != set(records):
            reasons.append('ACCOUNT_MEMBER_ORDER_COVERAGE_CHANGED')
        working = tuple(order for order in recovery.orders if order.state not in TERMINAL_ORDER_STATES)
        actual = {order.broker_order_id: (order.symbol, order.remaining_quantity, order.filled_quantity,
                                        order.filled_quantity) for order in working}
        if actual != expected or len(actual) != len(working):
            reasons.append('ACCOUNT_MEMBER_ORDER_COVERAGE_CHANGED')
        if reserved != recovery.account.reserved_open_buy_won:
            reasons.append('ACCOUNT_MEMBER_RESERVED_BUY_UNCONFIRMED')
    if not reasons:
        # These three account-wide occupancy facts remain in the decision's
        # original counts, cash, reservation and fingerprint. Only the new
        # member disposition distinguishes verified peer occupancy.
        reasons = [reason for reason in base.reasons if reason not in {
            'BROKER_OPEN_ORDER_PRESENT', 'BROKER_RESERVED_BUY_PRESENT', 'BROKER_POSITION_PRESENT'}]
    else:
        reasons = [*base.reasons, *reasons]
        own_ids, other_ids = [], []
    account_positions = {symbol: int(qty) for symbol, qty in recovery.account.positions.items() if int(qty) > 0}
    if account_positions != dict(risk.owned_position_quantities):
        reasons.append('BROKER_NONMEMBER_POSITION_PRESENT')
    reasons = tuple(dict.fromkeys(reasons))
    occupied = bool(own_positions or own_ids)
    manageable = {'DAILY_NET_PNL_UNKNOWN', 'DAILY_LOSS_LIMIT_REACHED', 'DATA_GAP_UNKNOWN',
                  'DATA_GAP_LIMIT_EXCEEDED', 'RECONNECT_LIMIT_EXCEEDED', 'BROKER_NONMEMBER_POSITION_PRESENT'}
    status = (MockAutomationRecoveryStatus.MANAGE_ONLY_ORDERS_DISABLED if occupied and set(reasons) <= manageable
        else MockAutomationRecoveryStatus.BLOCKED if reasons
        else MockAutomationRecoveryStatus.CLEARED_ORDERS_DISABLED)
    body = {**base.to_dict(), 'version': MOCK_AUTOMATION_ACCOUNT_RECOVERY_VERSION,
        'status': status.value, 'reasons': list(reasons), 'risk_snapshot_id': risk.snapshot_id,
        'risk_reconciliation_revision': risk.reconciliation_revision, 'account_selection_id': selection.selection_id,
        'member_spec_ids': list(selection.member_spec_ids), 'own_position_quantities': [list(row) for row in own_positions],
        'own_open_order_ids': sorted(set(own_ids)), 'other_member_open_order_ids': sorted(set(other_ids)),
        'observation_input_ref': observation.input_ref if isinstance(observation, MockAutomationAccountObservation) else ''}
    body.pop('decision_id')
    return MockAutomationAccountRecoveryDecision(**{**base.__dict__, 'decision_id': _content_id('mock_automation_recovery', body),
        'version': MOCK_AUTOMATION_ACCOUNT_RECOVERY_VERSION, 'status': status, 'reasons': reasons,
        'risk_snapshot_id': risk.snapshot_id, 'risk_reconciliation_revision': risk.reconciliation_revision,
        'account_selection_id': selection.selection_id, 'member_spec_ids': selection.member_spec_ids,
        'own_position_quantities': own_positions, 'own_open_order_ids': tuple(body['own_open_order_ids']),
        'other_member_open_order_ids': tuple(body['other_member_open_order_ids']),
        'observation_input_ref': body['observation_input_ref']})


def mock_automation_recovery_decision_from_dict(
    value: Mapping[str, Any],
) -> MockAutomationRecoveryDecision:
    account_member = value.get('version') == MOCK_AUTOMATION_ACCOUNT_RECOVERY_VERSION
    extra = ({'account_selection_id': value['account_selection_id'],
        'member_spec_ids': tuple(value['member_spec_ids']),
        'own_position_quantities': tuple(tuple(row) for row in value['own_position_quantities']),
        'own_open_order_ids': tuple(value['own_open_order_ids']),
        'other_member_open_order_ids': tuple(value['other_member_open_order_ids']),
        'observation_input_ref': value['observation_input_ref']} if account_member else {})
    decision = (MockAutomationAccountRecoveryDecision if account_member else MockAutomationRecoveryDecision)(
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
        risk_snapshot_id=str(value.get("risk_snapshot_id") or ""),
        risk_reconciliation_revision=int(value.get("risk_reconciliation_revision") or 0),
        **extra,
    )
    expected_body = {
        key: item for key, item in decision.to_dict().items() if key != "decision_id"
    }
    expected = _content_id("mock_automation_recovery", expected_body)
    legacy_expected = _content_id("mock_automation_recovery", {
        key: item for key, item in expected_body.items()
        if key not in {"risk_snapshot_id", "risk_reconciliation_revision"}
    })
    if (decision.decision_id not in ({expected} if account_member else {expected, legacy_expected})
            or account_member and decision.to_dict() != dict(value)):
        raise ValueError("mock automation recovery content does not match decision_id")
    return decision


def _validate_risk_snapshot(
    risk: MockAutomationRiskSnapshot,
    recovery: MockAccountRecovery,
    account_ref: str,
    run_id: str,
) -> None:
    if (
        risk.account_ref != account_ref
        or risk.execution_run_id != run_id
        or risk.account_as_of != recovery.account.as_of
        or risk.observed_at < risk.broker_query_completed_at
    ):
        raise ValueError("mock automation risk snapshot does not match broker recovery")


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
