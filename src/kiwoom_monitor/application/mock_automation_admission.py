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
    MockAutomationAccountSelection,
    mock_automation_account_selection_from_dict,
    MockAutomationReadinessStatus,
    StrategyLifecycleStage,
    StrategyStageRevision,
    assess_mock_automation_readiness,
)
from kiwoom_monitor.application.mock_automation_candidate import CandidateEligibilityStatus


MOCK_AUTOMATION_ADMISSION_VERSION = "mock_automation_admission/v1"
MOCK_AUTOMATION_LEASE_RECEIPT_VERSION = "mock_automation_lease_receipt/v1"
MOCK_AUTOMATION_CONTROL_VERSION = "mock_automation_control/v1"
MOCK_AUTOMATION_ACCOUNT_CONTROL_VERSION = "mock_automation_account_control/v1"
MOCK_AUTOMATION_ACCOUNT_ADMISSION_VERSION = "mock_automation_account_admission/v1"


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
    entry_resume_required: bool = False
    planned_review_resume_id: str | None = None
    account_selection: MockAutomationAccountSelection | None = None

    def __post_init__(self) -> None:
        if self.version not in {MOCK_AUTOMATION_CONTROL_VERSION, MOCK_AUTOMATION_ACCOUNT_CONTROL_VERSION}:
            raise ValueError("unsupported mock automation control version")
        if self.version == MOCK_AUTOMATION_ACCOUNT_CONTROL_VERSION:
            _require_aware(self.changed_at, 'changed_at')
            if self.account_selection is None:
                raise ValueError('account control requires its explicit frozen selection')
            selection = mock_automation_account_selection_from_dict(self.account_selection.to_dict())
            if (selection.account_scope.account_ref != self.account_ref
                    or selection.primary_spec_id != self.active_spec_id
                    or self.execution_run_id != execution_run_id_for_spec(selection.primary_spec_id)
                    or self.changed_at < selection.frozen_at):
                raise ValueError('account control selection scope conflict')
        elif self.account_selection is not None:
            raise ValueError('standalone control cannot carry account membership')
        if not all(str(getattr(self, name)).strip() for name in (
            "account_ref", "active_spec_id", "execution_run_id", "reason",
        )):
            raise ValueError("mock automation control identity and reason are required")
        if type(self.control_revision) is not int or self.control_revision <= 0:
            raise ValueError("control_revision must be a positive integer")
        if type(self.entry_resume_required) is not bool:
            raise ValueError("entry_resume_required must be a boolean")
        if self.planned_review_resume_id is not None and (
                not isinstance(self.planned_review_resume_id, str)
                or not self.planned_review_resume_id.startswith("planned_protection_resume_")):
            raise ValueError("invalid planned protection resume identity")
        _require_aware(self.changed_at, "changed_at")

    def includes_spec(self, spec_id: str) -> bool:
        return (spec_id in self.account_selection.member_spec_ids if self.account_selection is not None
                else spec_id == self.active_spec_id)

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
            **({'account_selection': self.account_selection.to_dict()} if self.account_selection is not None else {}),
            **({"entry_resume_required": True} if self.entry_resume_required else {}),
            **({"planned_review_resume_id": self.planned_review_resume_id}
               if self.planned_review_resume_id is not None and not self.entry_resume_required else {}),
        }


@dataclass(frozen=True)
class MockAutomationAccountResumeControl(MockAutomationControl):
    """Durable member review receipts without changing standalone control fields."""
    planned_review_resumes: tuple[tuple[str, str], ...] = ()

    def __post_init__(self):
        super().__post_init__()
        if (self.account_selection is None or self.planned_review_resume_id is not None
                or type(self.planned_review_resumes) is not tuple or not self.planned_review_resumes
                or any(type(row) is not tuple or len(row) != 2 for row in self.planned_review_resumes)
                or any(type(row) is not tuple or len(row) != 2
                    or type(row[0]) is not str or row[0] not in self.account_selection.member_spec_ids
                    or type(row[1]) is not str or not row[1].startswith('planned_protection_resume_')
                    for row in self.planned_review_resumes)
                or tuple(sorted(self.planned_review_resumes)) != self.planned_review_resumes
                or len({row[0] for row in self.planned_review_resumes}) != len(self.planned_review_resumes)):
            raise ValueError('invalid account planned protection resume identities')

    @property
    def planned_review_resume_ids(self):
        return dict(self.planned_review_resumes)

    def to_dict(self):
        return {**super().to_dict(), **({'planned_review_resume_ids': self.planned_review_resume_ids}
                if not self.entry_resume_required else {})}


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
    account_selection_id: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "admission_id", "spec_id", "strategy_ref", "account_ref",
            "final_batch_id", "final_run_id", "execution_run_id",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        if self.version not in {MOCK_AUTOMATION_ADMISSION_VERSION, MOCK_AUTOMATION_ACCOUNT_ADMISSION_VERSION} or self.state != "REQUESTED":
            raise ValueError("unsupported mock automation admission contract")
        if self.version == MOCK_AUTOMATION_ACCOUNT_ADMISSION_VERSION:
            if (not isinstance(self.account_selection_id, str)
                    or not self.account_selection_id.startswith('mock_automation_account_selection_')
                    or len(self.account_selection_id.rsplit('_', 1)[-1]) != 64):
                raise ValueError('account admission requires its frozen membership identity')
        elif self.account_selection_id is not None:
            raise ValueError('standalone admission cannot carry account membership')
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
            **({'account_selection_id': self.account_selection_id} if self.account_selection_id is not None else {}),
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
    account_selection: MockAutomationAccountSelection | None = None,
) -> MockAutomationAdmission:
    _require_aware(requested_at, "requested_at")
    body = {
        "version": MOCK_AUTOMATION_ADMISSION_VERSION if account_selection is None else MOCK_AUTOMATION_ACCOUNT_ADMISSION_VERSION,
        "spec_id": spec.spec_id,
        "strategy_ref": spec.strategy_ref,
        "account_ref": spec.account_scope.account_ref,
        "candidate_package_hash": spec.candidate_package_hash,
        "final_result_hash": spec.final_result_hash,
        "final_batch_id": spec.final_batch_id,
        "final_run_id": spec.final_run_id,
        "execution_run_id": execution_run_id_for_spec(spec.spec_id if account_selection is None else account_selection.primary_spec_id),
        "requested_at": requested_at.isoformat(),
        "state": "REQUESTED",
        **({'account_selection_id': account_selection.selection_id} if account_selection is not None else {}),
    }
    if account_selection is not None and (spec.spec_id not in account_selection.member_spec_ids
            or spec.account_scope != account_selection.account_scope or requested_at < account_selection.frozen_at):
        raise ValueError('account admission member scope conflict')
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
    if spec.supported_venue in {'NXT', 'SOR'} and not runtime.supports_automation_venue(spec.supported_venue):
        raise ValueError(f'{spec.supported_venue}_AUTOMATION_REQUIRES_LOCAL_FAKE_BROKER')
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
    if existing is not None and existing.version != MOCK_AUTOMATION_ADMISSION_VERSION:
        raise RuntimeError('explicit account membership requires the account admission path')
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


def admit_mock_automation_account_selection(repository, runtime, *, account_ref: str,
        selection_id: str, requested_at: datetime, expected_control_revision: int,
        expected_implementation_hash, lease_seconds: int = 60):
    """Admit explicit members to one disabled account runtime, never one lease per member.

    Immutable member admissions precede the single control CAS. A lost control
    acknowledgement can be retried against those same admissions and receipts.
    Activation/recovery and every actual order still need their native gates.
    """
    _require_aware(requested_at, 'requested_at')
    if type(expected_control_revision) is not int or expected_control_revision < 0:
        raise ValueError('expected account control revision must be a non-negative integer')
    selection = repository.load_mock_automation_account_selection(account_ref, selection_id)
    if selection is None:
        raise ValueError('mock account selection is not stored')
    if requested_at < selection.frozen_at:
        raise ValueError('account admission predates its frozen selection')
    run_id = execution_run_id_for_spec(selection.primary_spec_id)
    if runtime.account_ref != account_ref or runtime.run_id != run_id:
        raise ValueError('mock account runtime does not match its declared anchor')
    control = repository.load_mock_automation_control(account_ref)
    same = control is not None and control.account_selection == selection
    if selection.version == 'mock_automation_account_selection/v2' and not same:
        raise RuntimeError('account replacement requires its explicit membership operation')
    if control is not None and (control.desired_state is not MockAutomationDesiredState.RUNNING
            or control.execution_run_id != run_id or control.active_spec_id != selection.primary_spec_id
            or not same):
        raise RuntimeError('mock account control requires explicit resume or handoff')
    if (control.control_revision if control is not None else 0) != expected_control_revision:
        raise RuntimeError('mock account control changed before admission')
    # Revalidate current binding, all READY members, exact final lineage and
    # selected implementation even on a retry. Saved preparation grants nothing.
    repository.publish_mock_automation_account_selection(selection,
        expected_implementation_hash=expected_implementation_hash)
    admissions = []
    for spec_id in selection.member_spec_ids:
        spec = repository.load_mock_automation_spec(account_ref, spec_id, require_canonical_document=True)
        if spec.supported_venue in {'NXT', 'SOR'} and not runtime.supports_automation_venue(spec.supported_venue):
            raise ValueError(f'{spec.supported_venue}_AUTOMATION_REQUIRES_LOCAL_FAKE_BROKER')
        if selection.allows_entry(spec_id):
            _verify_final_candidate(repository, spec)
        existing = repository.load_mock_automation_admission_for_spec(account_ref, spec_id)
        if existing is not None and (existing.execution_run_id != run_id
                or existing.version != MOCK_AUTOMATION_ACCOUNT_ADMISSION_VERSION
                or not selection.matches_admission(spec_id=spec_id, admission_id=existing.admission_id,
                    original_selection_id=existing.account_selection_id, execution_run_id=existing.execution_run_id)):
            raise RuntimeError('member already has a different standalone or account admission')
        admissions.append(existing or build_mock_automation_admission(spec,
            requested_at=requested_at, account_selection=selection))
    # Validate the complete set before publishing any new member receipt.
    for admission in admissions:
        repository.save_mock_automation_admission(admission)
    runtime.start(lease_seconds=lease_seconds, new_orders_enabled=False)
    receipts = []
    for admission in admissions:
        receipt = repository.load_mock_automation_lease_for_admission(account_ref, admission.admission_id)
        if receipt is None:
            receipt = build_mock_automation_lease_receipt(admission, admitted_at=requested_at)
            repository.save_mock_automation_lease_receipt(receipt)
        receipts.append(receipt)
    if control is None:
        control = MockAutomationControl(MOCK_AUTOMATION_ACCOUNT_CONTROL_VERSION, account_ref,
            MockAutomationDesiredState.RUNNING, expected_control_revision + 1, selection.primary_spec_id,
            run_id, requested_at, 'explicit account membership admission', account_selection=selection)
        if not repository.save_mock_automation_control(control, expected_revision=expected_control_revision):
            raise RuntimeError('mock account control changed during admission')
    return tuple(admissions), tuple(receipts), control


def prepare_mock_automation_account_replacement(repository, runtime, *, account_ref: str,
        selection_id: str, requested_at: datetime, expected_control_revision: int, expected_implementation_hash):
    """Prepare immutable new admissions; the current control grants them no authority."""
    from kiwoom_monitor.domain.execution_activation import MockAutomationAccountSelectionV2

    _require_aware(requested_at, 'requested_at')
    selection = repository.load_mock_automation_account_selection(account_ref, selection_id)
    control = repository.load_mock_automation_control(account_ref)
    if not isinstance(selection, MockAutomationAccountSelectionV2) or control is None:
        raise ValueError('explicit account replacement selection and current control are required')
    if (runtime.account_ref != account_ref or runtime.run_id != control.execution_run_id
            or selection.primary_spec_id != control.active_spec_id or requested_at < selection.frozen_at):
        raise ValueError('account replacement must preserve its original runtime and anchor')
    same = control.account_selection == selection
    if control.control_revision not in ({expected_control_revision, expected_control_revision + 1}
                                       if same else {expected_control_revision}):
        raise RuntimeError('mock account control changed before replacement')
    runtime.set_new_orders_enabled(False)
    repository.publish_mock_automation_account_selection(selection,
        expected_implementation_hash=expected_implementation_hash)
    current_ids = set(control.account_selection.member_spec_ids if control.account_selection is not None
                      else (control.active_spec_id,))
    admissions, receipts, additions = [], [], []
    for identity in selection.member_spec_ids:
        spec = repository.load_mock_automation_spec(account_ref, identity, require_canonical_document=True)
        if spec is None:
            raise ValueError('account replacement member is not stored')
        if spec.supported_venue in {'NXT', 'SOR'} and not runtime.supports_automation_venue(spec.supported_venue):
            raise ValueError(f'{spec.supported_venue}_AUTOMATION_REQUIRES_LOCAL_FAKE_BROKER')
        if selection.allows_entry(identity):
            _verify_final_candidate(repository, spec)
        admission = repository.load_mock_automation_admission_for_spec(account_ref, identity)
        if admission is None:
            if identity in current_ids or selection.retained_admission_ref(identity) is not None:
                raise RuntimeError('retained account admission is missing')
            admission = build_mock_automation_admission(spec, requested_at=requested_at, account_selection=selection)
            additions.append(admission)
        if (admission.account_ref != account_ref or admission.execution_run_id != runtime.run_id
                or not selection.matches_admission(spec_id=identity, admission_id=admission.admission_id,
                    original_selection_id=admission.account_selection_id, execution_run_id=admission.execution_run_id)):
            raise RuntimeError('account replacement admission reference conflict')
        receipt = repository.load_mock_automation_lease_for_admission(account_ref, admission.admission_id)
        if receipt is None and identity in current_ids:
            raise RuntimeError('retained account lease receipt is missing')
        admissions.append(admission)
        receipts.append(receipt)
    # Validate every member before writing any new immutable admission.
    for admission in additions:
        repository.save_mock_automation_admission(admission)
    for index, admission in enumerate(admissions):
        if receipts[index] is None:
            receipts[index] = build_mock_automation_lease_receipt(admission, admitted_at=requested_at)
            repository.save_mock_automation_lease_receipt(receipts[index])
    return selection, tuple(admissions), tuple(receipts), control




def mock_automation_control_from_dict(value: Mapping[str, Any]) -> MockAutomationControl:
    if value.get('version') == MOCK_AUTOMATION_ACCOUNT_CONTROL_VERSION and type(value.get('control_revision')) is not int:
        raise ValueError('account control revision must be an integer')
    reviews = value.get('planned_review_resume_ids')
    if 'planned_review_resume_ids' in value and (type(reviews) is not dict or not reviews):
        raise ValueError('account planned protection resume identities must be a nonempty object')
    control = (MockAutomationAccountResumeControl if reviews is not None else MockAutomationControl)(
        version=str(value["version"]),
        account_ref=str(value["account_ref"]),
        desired_state=MockAutomationDesiredState(str(value["desired_state"])),
        control_revision=int(value["control_revision"]),
        active_spec_id=str(value["active_spec_id"]),
        execution_run_id=str(value["execution_run_id"]),
        changed_at=datetime.fromisoformat(str(value["changed_at"])),
        reason=str(value["reason"]),
        entry_resume_required=value.get("entry_resume_required", False),
        planned_review_resume_id=value.get("planned_review_resume_id"),
        account_selection=(mock_automation_account_selection_from_dict(value['account_selection'])
                           if value.get('account_selection') is not None else None),
        **({'planned_review_resumes': tuple(sorted(reviews.items()))} if reviews is not None else {}),
    )
    if control.account_selection is not None and control.to_dict() != dict(value):
        raise ValueError('account control document is not canonical')
    return control


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
        account_selection_id=value.get('account_selection_id'),
    )
    expected = _content_id("mock_automation_admission", {
        key: item for key, item in admission.to_dict().items() if key != "admission_id"
    })
    if admission.admission_id != expected:
        raise ValueError("mock automation admission content does not match admission_id")
    if admission.account_selection_id is not None and admission.to_dict() != dict(value):
        raise ValueError('account admission document is not canonical')
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
