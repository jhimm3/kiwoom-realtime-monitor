"""Admit one validated research candidate to a disabled O1 mock runtime lease."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping, Protocol

from kiwoom_monitor.domain.execution_activation import (
    ForwardEvaluationSpec,
    MockAutomationOperatingSpec,
    MockAutomationReadinessStatus,
    StrategyLifecycleStage,
    StrategyStageRevision,
    assess_mock_automation_readiness,
)
from kiwoom_monitor.application.mock_automation_candidate import CandidateEligibilityStatus


MOCK_AUTOMATION_ADMISSION_VERSION = "mock_automation_admission/v1"
MOCK_AUTOMATION_LEASE_RECEIPT_VERSION = "mock_automation_lease_receipt/v1"
MOCK_AUTOMATION_CONTROL_VERSION = "mock_automation_control/v1"


class MockAutomationDesiredState(StrEnum):
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"


@dataclass(frozen=True)
class MockAutomationControl:
    version: str
    account_ref: str
    desired_state: MockAutomationDesiredState
    control_revision: int
    active_spec_id: str
    execution_run_id: str
    changed_at: datetime
    reason: str

    def __post_init__(self) -> None:
        if self.version != MOCK_AUTOMATION_CONTROL_VERSION:
            raise ValueError("unsupported mock automation control version")
        if not all(str(getattr(self, name)).strip() for name in (
            "account_ref", "active_spec_id", "execution_run_id", "reason",
        )):
            raise ValueError("mock automation control identity and reason are required")
        if type(self.control_revision) is not int or self.control_revision <= 0:
            raise ValueError("control_revision must be a positive integer")
        _require_aware(self.changed_at, "changed_at")

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "account_ref": self.account_ref,
            "desired_state": self.desired_state.value,
            "control_revision": self.control_revision,
            "active_spec_id": self.active_spec_id,
            "execution_run_id": self.execution_run_id,
            "changed_at": self.changed_at.isoformat(),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class MockAutomationAdmission:
    admission_id: str
    version: str
    spec_id: str
    strategy_ref: str
    account_ref: str
    candidate_package_hash: str
    final_result_hash: str
    final_batch_id: str
    final_run_id: str
    execution_run_id: str
    requested_at: datetime
    state: str = "REQUESTED"

    def __post_init__(self) -> None:
        for name in (
            "admission_id", "spec_id", "strategy_ref", "account_ref",
            "final_batch_id", "final_run_id", "execution_run_id",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        if self.version != MOCK_AUTOMATION_ADMISSION_VERSION or self.state != "REQUESTED":
            raise ValueError("unsupported mock automation admission contract")
        _require_sha256(self.candidate_package_hash, "candidate_package_hash")
        _require_sha256(self.final_result_hash, "final_result_hash")
        _require_aware(self.requested_at, "requested_at")

    def to_dict(self) -> dict[str, Any]:
        return {
            "admission_id": self.admission_id,
            "version": self.version,
            "spec_id": self.spec_id,
            "strategy_ref": self.strategy_ref,
            "account_ref": self.account_ref,
            "candidate_package_hash": self.candidate_package_hash,
            "final_result_hash": self.final_result_hash,
            "final_batch_id": self.final_batch_id,
            "final_run_id": self.final_run_id,
            "execution_run_id": self.execution_run_id,
            "requested_at": self.requested_at.isoformat(),
            "state": self.state,
        }


@dataclass(frozen=True)
class MockAutomationLeaseReceipt:
    receipt_id: str
    version: str
    admission_id: str
    spec_id: str
    account_ref: str
    execution_run_id: str
    admitted_at: datetime
    new_orders_enabled: bool = False
    state: str = "LEASED_ORDERS_DISABLED"

    def __post_init__(self) -> None:
        for name in ("receipt_id", "admission_id", "spec_id", "account_ref", "execution_run_id"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        if (
            self.version != MOCK_AUTOMATION_LEASE_RECEIPT_VERSION
            or self.state != "LEASED_ORDERS_DISABLED"
            or self.new_orders_enabled is not False
        ):
            raise ValueError("mock automation admission must keep new orders disabled")
        _require_aware(self.admitted_at, "admitted_at")

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "version": self.version,
            "admission_id": self.admission_id,
            "spec_id": self.spec_id,
            "account_ref": self.account_ref,
            "execution_run_id": self.execution_run_id,
            "admitted_at": self.admitted_at.isoformat(),
            "new_orders_enabled": self.new_orders_enabled,
            "state": self.state,
        }


class MockAutomationRepository(Protocol):
    def load_mock_automation_spec(
        self, account_ref: str, spec_id: str,
    ) -> MockAutomationOperatingSpec | None: ...
    def load_profile(self, strategy_ref: str, profile_id: str) -> ForwardEvaluationSpec | None: ...
    def save_mock_automation_spec(self, value: MockAutomationOperatingSpec) -> bool: ...
    def latest_stage(self, strategy_ref: str) -> StrategyLifecycleStage: ...
    def load_stage_revisions(self, strategy_ref: str) -> tuple[StrategyStageRevision, ...]: ...
    def save_mock_automation_admission(self, value: MockAutomationAdmission) -> bool: ...
    def load_mock_automation_admissions(
        self, account_ref: str,
    ) -> tuple[MockAutomationAdmission, ...]: ...
    def load_mock_automation_admission_for_spec(
        self, account_ref: str, spec_id: str,
    ) -> MockAutomationAdmission | None: ...
    def save_mock_automation_lease_receipt(self, value: MockAutomationLeaseReceipt) -> bool: ...
    def load_mock_automation_lease_receipts(
        self, account_ref: str,
    ) -> tuple[MockAutomationLeaseReceipt, ...]: ...
    def load_mock_automation_lease_for_admission(
        self, account_ref: str, admission_id: str,
    ) -> MockAutomationLeaseReceipt | None: ...
    def load_mock_automation_control(
        self, account_ref: str,
    ) -> MockAutomationControl | None: ...
    def save_mock_automation_control(
        self, value: MockAutomationControl, *, expected_revision: int,
    ) -> bool: ...


class FinalCandidateRepository(Protocol):
    def load_mock_automation_candidate_package(
        self, strategy_ref: str, package_hash: str,
    ) -> Any | None: ...
    def load_mock_automation_eligibility_policy(
        self, strategy_ref: str, package_hash: str,
    ) -> Any | None: ...
    def load_mock_automation_eligibility_receipt(
        self, account_ref: str, package_hash: str,
    ) -> Any | None: ...


class DisabledMockRuntime(Protocol):
    account_ref: str
    run_id: str

    def start(
        self, *, lease_seconds: int = 60, new_orders_enabled: bool | None = None,
    ) -> None: ...


def execution_run_id_for_spec(spec_id: str) -> str:
    if not spec_id.strip():
        raise ValueError("spec_id is required")
    return f"mock_auto_run_{hashlib.sha256(spec_id.encode('utf-8')).hexdigest()}"


def build_mock_automation_admission(
    spec: MockAutomationOperatingSpec,
    *,
    requested_at: datetime,
) -> MockAutomationAdmission:
    _require_aware(requested_at, "requested_at")
    body = {
        "version": MOCK_AUTOMATION_ADMISSION_VERSION,
        "spec_id": spec.spec_id,
        "strategy_ref": spec.strategy_ref,
        "account_ref": spec.account_scope.account_ref,
        "candidate_package_hash": spec.candidate_package_hash,
        "final_result_hash": spec.final_result_hash,
        "final_batch_id": spec.final_batch_id,
        "final_run_id": spec.final_run_id,
        "execution_run_id": execution_run_id_for_spec(spec.spec_id),
        "requested_at": requested_at.isoformat(),
        "state": "REQUESTED",
    }
    return MockAutomationAdmission(
        admission_id=_content_id("mock_automation_admission", body),
        requested_at=requested_at,
        **{name: value for name, value in body.items() if name != "requested_at"},
    )


def build_mock_automation_lease_receipt(
    admission: MockAutomationAdmission,
    *,
    admitted_at: datetime,
) -> MockAutomationLeaseReceipt:
    _require_aware(admitted_at, "admitted_at")
    body = {
        "version": MOCK_AUTOMATION_LEASE_RECEIPT_VERSION,
        "admission_id": admission.admission_id,
        "spec_id": admission.spec_id,
        "account_ref": admission.account_ref,
        "execution_run_id": admission.execution_run_id,
        "admitted_at": admitted_at.isoformat(),
        "new_orders_enabled": False,
        "state": "LEASED_ORDERS_DISABLED",
    }
    return MockAutomationLeaseReceipt(
        receipt_id=_content_id("mock_automation_lease", body),
        admitted_at=admitted_at,
        **{name: value for name, value in body.items() if name != "admitted_at"},
    )


def admit_mock_automation(
    repository: MockAutomationRepository,
    research_repository: FinalCandidateRepository,
    runtime: DisabledMockRuntime,
    *,
    account_ref: str,
    spec_id: str,
    requested_at: datetime,
    lease_seconds: int = 60,
) -> tuple[MockAutomationAdmission, MockAutomationLeaseReceipt]:
    spec = repository.load_mock_automation_spec(account_ref, spec_id)
    if spec is None:
        raise ValueError("mock automation operating spec is not stored")
    repository.save_mock_automation_spec(spec)  # Revalidate the current mock binding at admission time.
    profile = repository.load_profile(spec.strategy_ref, spec.forward_profile_id)
    if profile is None:
        raise ValueError("mock automation forward profile is not stored")
    readiness = assess_mock_automation_readiness(
        spec, profile, strategy_stage=repository.latest_stage(spec.strategy_ref),
    )
    if readiness.status is not MockAutomationReadinessStatus.READY:
        raise ValueError("mock automation operating spec is BLOCKED: " + ",".join(readiness.reasons))
    revisions = repository.load_stage_revisions(spec.strategy_ref)
    if (
        not revisions
        or revisions[-1].stage is not StrategyLifecycleStage.SHADOW
        or revisions[-1].revision_id != spec.shadow_evidence_ref
    ):
        raise ValueError("mock automation shadow evidence does not match the latest stage revision")
    _verify_final_candidate(research_repository, spec)
    expected_run_id = execution_run_id_for_spec(spec.spec_id)
    if runtime.account_ref != account_ref or runtime.run_id != expected_run_id:
        raise ValueError("mock automation runtime does not match the admitted spec")

    existing = repository.load_mock_automation_admission_for_spec(account_ref, spec.spec_id)
    admission = existing if existing is not None else build_mock_automation_admission(
        spec, requested_at=requested_at,
    )
    repository.save_mock_automation_admission(admission)

    existing_control = repository.load_mock_automation_control(account_ref)
    if existing_control is not None and (
        existing_control.desired_state is not MockAutomationDesiredState.RUNNING
        or existing_control.active_spec_id != spec.spec_id
        or existing_control.execution_run_id != admission.execution_run_id
    ):
        raise RuntimeError("mock automation control requires explicit resume or handoff")

    runtime.start(lease_seconds=lease_seconds, new_orders_enabled=False)
    existing_receipt = repository.load_mock_automation_lease_for_admission(
        account_ref, admission.admission_id,
    )
    if existing_receipt is not None:
        receipt = existing_receipt
    else:
        receipt = build_mock_automation_lease_receipt(admission, admitted_at=requested_at)
        repository.save_mock_automation_lease_receipt(receipt)
    control = existing_control
    if control is None:
        control = MockAutomationControl(
            version=MOCK_AUTOMATION_CONTROL_VERSION,
            account_ref=account_ref,
            desired_state=MockAutomationDesiredState.RUNNING,
            control_revision=1,
            active_spec_id=spec.spec_id,
            execution_run_id=admission.execution_run_id,
            changed_at=requested_at,
            reason="initial admission",
        )
        if not repository.save_mock_automation_control(control, expected_revision=0):
            raise RuntimeError("mock automation control changed during admission")
    elif (
        control.desired_state is not MockAutomationDesiredState.RUNNING
        or control.active_spec_id != spec.spec_id
        or control.execution_run_id != admission.execution_run_id
    ):
        raise RuntimeError("mock automation control requires explicit resume or handoff")
    return admission, receipt


def mock_automation_control_from_dict(value: Mapping[str, Any]) -> MockAutomationControl:
    return MockAutomationControl(
        version=str(value["version"]),
        account_ref=str(value["account_ref"]),
        desired_state=MockAutomationDesiredState(str(value["desired_state"])),
        control_revision=int(value["control_revision"]),
        active_spec_id=str(value["active_spec_id"]),
        execution_run_id=str(value["execution_run_id"]),
        changed_at=datetime.fromisoformat(str(value["changed_at"])),
        reason=str(value["reason"]),
    )


def mock_automation_admission_from_dict(value: Mapping[str, Any]) -> MockAutomationAdmission:
    requested_at = datetime.fromisoformat(str(value["requested_at"]))
    admission = MockAutomationAdmission(
        admission_id=str(value["admission_id"]),
        version=str(value["version"]),
        spec_id=str(value["spec_id"]),
        strategy_ref=str(value["strategy_ref"]),
        account_ref=str(value["account_ref"]),
        candidate_package_hash=str(value["candidate_package_hash"]),
        final_result_hash=str(value["final_result_hash"]),
        final_batch_id=str(value["final_batch_id"]),
        final_run_id=str(value["final_run_id"]),
        execution_run_id=str(value["execution_run_id"]),
        requested_at=requested_at,
        state=str(value["state"]),
    )
    expected = _content_id("mock_automation_admission", {
        key: item for key, item in admission.to_dict().items() if key != "admission_id"
    })
    if admission.admission_id != expected:
        raise ValueError("mock automation admission content does not match admission_id")
    return admission


def mock_automation_lease_receipt_from_dict(
    value: Mapping[str, Any],
) -> MockAutomationLeaseReceipt:
    admitted_at = datetime.fromisoformat(str(value["admitted_at"]))
    receipt = MockAutomationLeaseReceipt(
        receipt_id=str(value["receipt_id"]),
        version=str(value["version"]),
        admission_id=str(value["admission_id"]),
        spec_id=str(value["spec_id"]),
        account_ref=str(value["account_ref"]),
        execution_run_id=str(value["execution_run_id"]),
        admitted_at=admitted_at,
        new_orders_enabled=value["new_orders_enabled"],
        state=str(value["state"]),
    )
    expected = _content_id("mock_automation_lease", {
        key: item for key, item in receipt.to_dict().items() if key != "receipt_id"
    })
    if receipt.receipt_id != expected:
        raise ValueError("mock automation lease receipt content does not match receipt_id")
    return receipt


def _verify_final_candidate(
    repository: FinalCandidateRepository,
    spec: MockAutomationOperatingSpec,
) -> None:
    package = repository.load_mock_automation_candidate_package(
        spec.strategy_ref, spec.candidate_package_hash,
    )
    if package is None:
        raise ValueError("mock automation candidate package is not stored")
    if (
        package.source_final_batch_id != spec.final_batch_id
        or package.source_final_run_id != spec.final_run_id
        or package.source_final_result_hash != spec.final_result_hash
    ):
        raise ValueError("mock automation candidate package lineage does not match the operating spec")
    receipt = repository.load_mock_automation_eligibility_receipt(
        spec.account_scope.account_ref, package.package_hash,
    )
    if receipt is None or receipt.status is not CandidateEligibilityStatus.ELIGIBLE:
        raise ValueError("mock automation candidate does not have an ELIGIBLE receipt")
    policy = repository.load_mock_automation_eligibility_policy(
        spec.strategy_ref, package.package_hash,
    )
    if policy is None or (
        policy.candidate_spec_hash != package.candidate_spec_hash
        or policy.policy_id != receipt.policy_id
        or policy.tbd_fields
        or receipt.candidate_spec_hash != package.candidate_spec_hash
    ):
        raise ValueError("mock automation eligibility policy is missing or incomplete")


def _content_id(prefix: str, value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()}"


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _require_sha256(value: str, name: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
