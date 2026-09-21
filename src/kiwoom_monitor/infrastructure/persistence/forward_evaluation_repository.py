"""Content-addressed O2a profiles, reports and stage revisions in the central store."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Protocol

from kiwoom_monitor.application.mock_automation_admission import (
    MockAutomationAdmission,
    MockAutomationControl,
    MockAutomationLeaseReceipt,
    execution_run_id_for_spec,
    mock_automation_admission_from_dict,
    mock_automation_control_from_dict,
    mock_automation_lease_receipt_from_dict,
)
from kiwoom_monitor.application.mock_automation_candidate import (
    MockAutomationCandidatePackage,
    MockAutomationEligibilityPolicy,
    MockAutomationEligibilityReceipt,
    candidate_package_from_dict,
    eligibility_policy_from_dict,
    eligibility_receipt_from_dict,
    evaluate_candidate_eligibility,
)
from kiwoom_monitor.application.mock_automation_recovery import (
    MockAutomationRecoveryDecision,
    mock_automation_recovery_decision_from_dict,
)
from kiwoom_monitor.application.mock_automation_risk import (
    MockAutomationRiskSnapshot,
    mock_automation_risk_snapshot_from_dict,
)
from kiwoom_monitor.application.mock_automation_execution import (
    MockAutomationDecisionGate,
    MockAutomationDispatchReceipt,
    MockAutomationGateStatus,
    MockAutomationStopRevision,
    mock_automation_decision_gate_from_dict,
    mock_automation_dispatch_receipt_from_dict,
    mock_automation_stop_revision_from_dict,
)
from kiwoom_monitor.application.forward_evaluation import (
    FeedbackEvidence,
    FeedbackImprovementProposalRevision,
    FeedbackReviewRevision,
    ForwardEvaluationReport,
    build_feedback_review_revision,
    feedback_evidence_from_dict,
    feedback_improvement_from_dict,
    feedback_review_from_dict,
    forward_report_from_dict,
)
from kiwoom_monitor.application.feedback_strategy_revision import (
    FeedbackRevalidationRequest,
    FeedbackRevalidationReceipt,
    FeedbackStrategyVersion,
    feedback_revalidation_request_from_dict,
    feedback_revalidation_receipt_from_dict,
    feedback_strategy_version_from_dict,
)
from kiwoom_monitor.domain.execution_activation import (
    ForwardEvaluationSpec,
    ForwardReportStatus,
    MockAutomationOperatingSpec,
    StrategyLifecycleStage,
    StrategyStageRevision,
    forward_spec_from_dict,
    mock_automation_spec_from_dict,
    stage_revision_from_dict,
)


class ForwardEvaluationStore(Protocol):
    def upsert_documents(self, collection: str, values: list[dict[str, Any]]) -> None: ...
    def load_documents(
        self, collection: str, owner: str = "", limit: int = 1000,
        offset: int = 0, updated_after: float = 0.0,
    ) -> list[dict[str, Any]]: ...
    def load_document(
        self, collection: str, owner: str, key: str,
    ) -> dict[str, Any] | None: ...
    def load_account_bindings(self) -> list[dict[str, Any]]: ...
    def load_mock_automation_control(self, account_ref: str) -> dict[str, Any] | None: ...
    def save_mock_automation_control(
        self, value: dict[str, Any], *, expected_revision: int,
    ) -> bool: ...


class ForwardEvaluationRepository:
    PROFILE_COLLECTION = "execution_forward_profiles"
    REPORT_COLLECTION = "execution_forward_reports"
    STAGE_COLLECTION = "execution_strategy_stage_revisions"
    FEEDBACK_COLLECTION = "execution_feedback_evidence"
    FEEDBACK_REVIEW_COLLECTION = "execution_feedback_reviews"
    FEEDBACK_IMPROVEMENT_COLLECTION = "execution_feedback_improvement_proposals"
    FEEDBACK_STRATEGY_VERSION_COLLECTION = "execution_feedback_strategy_versions"
    FEEDBACK_REVALIDATION_REQUEST_COLLECTION = "execution_feedback_revalidation_requests"
    FEEDBACK_REVALIDATION_COLLECTION = "execution_feedback_revalidation_receipts"
    MOCK_AUTOMATION_SPEC_COLLECTION = "execution_mock_automation_specs"
    MOCK_AUTOMATION_ADMISSION_COLLECTION = "execution_mock_automation_admissions"
    MOCK_AUTOMATION_ADMISSION_BY_SPEC_COLLECTION = "execution_mock_automation_admission_by_spec"
    MOCK_AUTOMATION_LEASE_COLLECTION = "execution_mock_automation_lease_receipts"
    MOCK_AUTOMATION_LEASE_BY_ADMISSION_COLLECTION = "execution_mock_automation_lease_by_admission"
    MOCK_AUTOMATION_RECOVERY_COLLECTION = "execution_mock_automation_recovery_decisions"
    MOCK_AUTOMATION_CURRENT_RECOVERY_COLLECTION = "execution_mock_automation_current_recovery"
    MOCK_AUTOMATION_GATE_COLLECTION = "execution_mock_automation_decision_gates"
    MOCK_AUTOMATION_APPROVED_GATE_COLLECTION = "execution_mock_automation_approved_gates"
    MOCK_AUTOMATION_DISPATCH_COLLECTION = "execution_mock_automation_dispatch_receipts"
    MOCK_AUTOMATION_DISPATCH_BY_INTENT_COLLECTION = "execution_mock_automation_dispatch_by_intent"
    MOCK_AUTOMATION_STOP_COLLECTION = "execution_mock_automation_stop_revisions"
    MOCK_AUTOMATION_CURRENT_STOP_COLLECTION = "execution_mock_automation_current_stop"
    MOCK_AUTOMATION_CANDIDATE_COLLECTION = "execution_mock_automation_candidate_packages"
    MOCK_AUTOMATION_ELIGIBILITY_POLICY_COLLECTION = "execution_mock_automation_eligibility_policies"
    MOCK_AUTOMATION_ELIGIBILITY_RECEIPT_COLLECTION = "execution_mock_automation_eligibility_receipts"
    MOCK_AUTOMATION_RISK_COLLECTION = "execution_mock_automation_risk_snapshots"
    MOCK_AUTOMATION_CURRENT_RISK_COLLECTION = "execution_mock_automation_current_risk"

    def __init__(self, store: ForwardEvaluationStore) -> None:
        self._store = store

    def save_profile(self, value: ForwardEvaluationSpec) -> bool:
        return self._save_immutable(
            self.PROFILE_COLLECTION, value.strategy_ref, value.profile_id, value.to_dict(),
        )

    def load_profile(self, strategy_ref: str, profile_id: str) -> ForwardEvaluationSpec | None:
        document = self._find(self.PROFILE_COLLECTION, strategy_ref, profile_id)
        return forward_spec_from_dict(document) if document else None

    def save_mock_automation_spec(self, value: MockAutomationOperatingSpec) -> bool:
        value = mock_automation_spec_from_dict(value.to_dict())
        profile = self.load_profile(value.strategy_ref, value.forward_profile_id)
        if profile is None:
            raise ValueError("mock automation spec requires a stored forward profile")
        if (
            profile.account_ref != value.account_scope.account_ref
            or profile.environment != "mock"
        ):
            raise ValueError("mock automation spec does not match its forward profile scope")
        bindings = [
            row for row in self._store.load_account_bindings()
            if str(row.get("credential_profile_id", "")) == value.credential_profile_id
            and str(row.get("broker", "")) == value.account_scope.broker
            and str(row.get("environment", "")) == value.account_scope.environment.value
        ]
        latest = max(bindings, key=lambda row: int(row.get("binding_revision", 0)), default=None)
        if latest is None or (
            str(latest.get("account_ref", "")) != value.account_scope.account_ref
            or int(latest.get("binding_revision", 0)) != value.binding_revision
            or str(latest.get("verified_at", "")) != value.binding_verified_at.isoformat()
            or str(latest.get("verification_method", "")) != "ka00001"
        ):
            raise ValueError("mock automation spec requires the current verified mock binding")
        return self._save_immutable(
            self.MOCK_AUTOMATION_SPEC_COLLECTION,
            value.account_scope.account_ref,
            value.spec_id,
            value.to_dict(),
        )

    def load_mock_automation_specs(
        self, account_ref: str,
    ) -> tuple[MockAutomationOperatingSpec, ...]:
        rows = self._store.load_documents(
            self.MOCK_AUTOMATION_SPEC_COLLECTION, account_ref, 10_000,
        )
        values = [mock_automation_spec_from_dict(_document(row)) for row in rows]
        return tuple(sorted(values, key=lambda item: (item.frozen_at, item.spec_id)))

    def load_mock_automation_spec(
        self, account_ref: str, spec_id: str,
    ) -> MockAutomationOperatingSpec | None:
        document = self._find(self.MOCK_AUTOMATION_SPEC_COLLECTION, account_ref, spec_id)
        return mock_automation_spec_from_dict(document) if document else None

    def save_mock_automation_risk_snapshot(self, value: MockAutomationRiskSnapshot) -> bool:
        value = mock_automation_risk_snapshot_from_dict(value.to_dict())
        current = self.load_latest_mock_automation_risk(value.account_ref)
        if current is not None and (
            current.reconciliation_revision > value.reconciliation_revision
            or (
                current.reconciliation_revision == value.reconciliation_revision
                and current.snapshot_id != value.snapshot_id
            )
        ):
            raise ValueError("mock automation risk revision did not advance monotonically")
        created = self._save_immutable(
            self.MOCK_AUTOMATION_RISK_COLLECTION,
            value.account_ref,
            value.snapshot_id,
            value.to_dict(),
        )
        if current is None or current.snapshot_id != value.snapshot_id:
            self._store.upsert_documents(self.MOCK_AUTOMATION_CURRENT_RISK_COLLECTION, [{
                "owner": value.account_ref,
                "key": value.account_ref,
                "document": value.to_dict(),
            }])
        return created

    def load_latest_mock_automation_risk(
        self, account_ref: str,
    ) -> MockAutomationRiskSnapshot | None:
        document = self._find(
            self.MOCK_AUTOMATION_CURRENT_RISK_COLLECTION, account_ref, account_ref,
        )
        return mock_automation_risk_snapshot_from_dict(document) if document else None

    def load_mock_automation_risk_snapshots(
        self, account_ref: str,
    ) -> tuple[MockAutomationRiskSnapshot, ...]:
        rows = self._store.load_documents(
            self.MOCK_AUTOMATION_RISK_COLLECTION, account_ref, 10_000,
        )
        values = [mock_automation_risk_snapshot_from_dict(_document(row)) for row in rows]
        return tuple(sorted(values, key=lambda item: (
            item.reconciliation_revision, item.observed_at, item.snapshot_id,
        )))

    def save_mock_automation_admission(self, value: MockAutomationAdmission) -> bool:
        value = mock_automation_admission_from_dict(value.to_dict())
        spec = self.load_mock_automation_spec(value.account_ref, value.spec_id)
        if spec is None:
            raise ValueError("mock automation admission requires a stored operating spec")
        if (
            spec.strategy_ref != value.strategy_ref
            or spec.candidate_package_hash != value.candidate_package_hash
            or spec.final_result_hash != value.final_result_hash
            or spec.final_batch_id != value.final_batch_id
            or spec.final_run_id != value.final_run_id
            or value.execution_run_id != execution_run_id_for_spec(spec.spec_id)
        ):
            raise ValueError("mock automation admission does not match its operating spec")
        existing = self.load_mock_automation_admission_for_spec(
            value.account_ref, value.spec_id,
        )
        if existing is not None and existing.admission_id != value.admission_id:
            raise ValueError("mock automation spec already has another admission")
        saved = self._save_immutable(
            self.MOCK_AUTOMATION_ADMISSION_COLLECTION,
            value.account_ref,
            value.admission_id,
            value.to_dict(),
        )
        self._save_immutable(
            self.MOCK_AUTOMATION_ADMISSION_BY_SPEC_COLLECTION,
            value.account_ref,
            value.spec_id,
            value.to_dict(),
        )
        return saved

    def publish_mock_automation_candidate(
        self,
        package: MockAutomationCandidatePackage,
        policy: MockAutomationEligibilityPolicy,
        receipt: MockAutomationEligibilityReceipt,
        *,
        credential_profile_id: str,
        expected_binding_revision: int,
    ) -> bool:
        """Persist a validated publication without enabling execution or order transport."""
        package = candidate_package_from_dict(package.to_dict())
        policy = eligibility_policy_from_dict(policy.to_dict())
        receipt = eligibility_receipt_from_dict(receipt.to_dict())
        if (
            package.strategy_ref != policy.strategy_ref
            or package.candidate_spec_hash != policy.candidate_spec_hash
            or receipt.package_hash != package.package_hash
            or receipt.candidate_spec_hash != package.candidate_spec_hash
            or receipt.policy_id != policy.policy_id
        ):
            raise ValueError("candidate publication lineage is inconsistent")
        expected_receipt = evaluate_candidate_eligibility(
            package, policy, account_ref=receipt.account_ref,
        )
        if expected_receipt != receipt:
            raise ValueError("candidate eligibility receipt does not match policy evaluation")
        bindings = [
            row for row in self._store.load_account_bindings()
            if str(row.get("credential_profile_id", "")) == credential_profile_id
            and str(row.get("broker", "")) == "kiwoom"
            and str(row.get("environment", "")) == "mock"
        ]
        latest = max(bindings, key=lambda row: int(row.get("binding_revision", 0)), default=None)
        if latest is None or (
            str(latest.get("account_ref", "")) != receipt.account_ref
            or int(latest.get("binding_revision", 0)) != expected_binding_revision
            or str(latest.get("verification_method", "")) != "ka00001"
        ):
            raise ValueError("candidate publication requires the current verified mock binding")
        # Anchor the first policy by package before the remaining documents. If the
        # process stops mid-publication, a retry cannot substitute a lower threshold.
        changed = self._save_immutable(
            self.MOCK_AUTOMATION_ELIGIBILITY_POLICY_COLLECTION,
            policy.strategy_ref,
            package.package_hash,
            policy.to_dict(),
        )
        changed = self._save_immutable(
            self.MOCK_AUTOMATION_CANDIDATE_COLLECTION,
            package.strategy_ref,
            package.package_hash,
            package.to_dict(),
        ) or changed
        changed = self._save_immutable(
            self.MOCK_AUTOMATION_ELIGIBILITY_RECEIPT_COLLECTION,
            receipt.account_ref,
            package.package_hash,
            receipt.to_dict(),
        ) or changed
        return changed

    def load_mock_automation_candidate_package(
        self, strategy_ref: str, package_hash: str,
    ) -> MockAutomationCandidatePackage | None:
        document = self._find(
            self.MOCK_AUTOMATION_CANDIDATE_COLLECTION, strategy_ref, package_hash,
        )
        return candidate_package_from_dict(document) if document else None

    def load_mock_automation_eligibility_policy(
        self, strategy_ref: str, package_hash: str,
    ) -> MockAutomationEligibilityPolicy | None:
        document = self._find(
            self.MOCK_AUTOMATION_ELIGIBILITY_POLICY_COLLECTION, strategy_ref, package_hash,
        )
        return eligibility_policy_from_dict(document) if document else None

    def load_mock_automation_eligibility_receipt(
        self, account_ref: str, package_hash: str,
    ) -> MockAutomationEligibilityReceipt | None:
        document = self._find(
            self.MOCK_AUTOMATION_ELIGIBILITY_RECEIPT_COLLECTION, account_ref, package_hash,
        )
        return eligibility_receipt_from_dict(document) if document else None

    def load_mock_automation_candidate_publications(
        self, account_ref: str,
    ) -> tuple[tuple[
        MockAutomationCandidatePackage,
        MockAutomationEligibilityPolicy,
        MockAutomationEligibilityReceipt,
    ], ...]:
        """Load complete candidate publications visible to one mock account."""
        receipt_rows = self._store.load_documents(
            self.MOCK_AUTOMATION_ELIGIBILITY_RECEIPT_COLLECTION, account_ref, 10_000,
        )
        package_rows = self._store.load_documents(
            self.MOCK_AUTOMATION_CANDIDATE_COLLECTION, "", 10_000,
        )
        packages = {
            str(document.get("package_hash", "")): candidate_package_from_dict(document)
            for document in (_document(row) for row in package_rows)
        }
        publications = []
        for row in receipt_rows:
            receipt = eligibility_receipt_from_dict(_document(row))
            package = packages.get(receipt.package_hash)
            if package is None:
                raise ValueError("candidate publication package is missing")
            policy = self.load_mock_automation_eligibility_policy(
                package.strategy_ref, package.package_hash,
            )
            if policy is None:
                raise ValueError("candidate publication eligibility policy is missing")
            if (
                receipt.account_ref != account_ref
                or receipt.candidate_spec_hash != package.candidate_spec_hash
                or receipt.policy_id != policy.policy_id
            ):
                raise ValueError("candidate publication lineage is inconsistent")
            publications.append((package, policy, receipt))
        return tuple(sorted(
            publications,
            key=lambda value: (
                value[2].evaluated_at, value[0].source_run_finished_at,
                value[0].package_hash,
            ),
            reverse=True,
        ))

    def load_mock_automation_admission_for_spec(
        self, account_ref: str, spec_id: str,
    ) -> MockAutomationAdmission | None:
        document = self._find(
            self.MOCK_AUTOMATION_ADMISSION_BY_SPEC_COLLECTION, account_ref, spec_id,
        )
        return mock_automation_admission_from_dict(document) if document else None

    def load_mock_automation_admissions(
        self, account_ref: str,
    ) -> tuple[MockAutomationAdmission, ...]:
        rows = self._store.load_documents(
            self.MOCK_AUTOMATION_ADMISSION_COLLECTION, account_ref, 10_000,
        )
        values = [mock_automation_admission_from_dict(_document(row)) for row in rows]
        return tuple(sorted(values, key=lambda item: (item.requested_at, item.admission_id)))

    def save_mock_automation_lease_receipt(
        self, value: MockAutomationLeaseReceipt,
    ) -> bool:
        value = mock_automation_lease_receipt_from_dict(value.to_dict())
        document = self._find(
            self.MOCK_AUTOMATION_ADMISSION_COLLECTION,
            value.account_ref,
            value.admission_id,
        )
        if document is None:
            raise ValueError("mock automation lease receipt requires a stored admission")
        admission = mock_automation_admission_from_dict(document)
        if (
            admission.spec_id != value.spec_id
            or admission.execution_run_id != value.execution_run_id
            or value.new_orders_enabled is not False
        ):
            raise ValueError("mock automation lease receipt does not match its admission")
        existing = self.load_mock_automation_lease_for_admission(
            value.account_ref, value.admission_id,
        )
        if existing is not None and existing.receipt_id != value.receipt_id:
            raise ValueError("mock automation admission already has another lease receipt")
        saved = self._save_immutable(
            self.MOCK_AUTOMATION_LEASE_COLLECTION,
            value.account_ref,
            value.receipt_id,
            value.to_dict(),
        )
        self._save_immutable(
            self.MOCK_AUTOMATION_LEASE_BY_ADMISSION_COLLECTION,
            value.account_ref,
            value.admission_id,
            value.to_dict(),
        )
        return saved

    def load_mock_automation_lease_for_admission(
        self, account_ref: str, admission_id: str,
    ) -> MockAutomationLeaseReceipt | None:
        document = self._find(
            self.MOCK_AUTOMATION_LEASE_BY_ADMISSION_COLLECTION,
            account_ref,
            admission_id,
        )
        return mock_automation_lease_receipt_from_dict(document) if document else None

    def load_mock_automation_lease_receipts(
        self, account_ref: str,
    ) -> tuple[MockAutomationLeaseReceipt, ...]:
        rows = self._store.load_documents(
            self.MOCK_AUTOMATION_LEASE_COLLECTION, account_ref, 10_000,
        )
        values = [mock_automation_lease_receipt_from_dict(_document(row)) for row in rows]
        return tuple(sorted(values, key=lambda item: (item.admitted_at, item.receipt_id)))

    def load_mock_automation_control(
        self, account_ref: str,
    ) -> MockAutomationControl | None:
        document = self._store.load_mock_automation_control(account_ref)
        return mock_automation_control_from_dict(document) if document else None

    def save_mock_automation_control(
        self, value: MockAutomationControl, *, expected_revision: int,
    ) -> bool:
        value = mock_automation_control_from_dict(value.to_dict())
        if value.control_revision != expected_revision + 1:
            raise ValueError("mock automation control revision must increase by one")
        return self._store.save_mock_automation_control(
            value.to_dict(), expected_revision=expected_revision,
        )

    def save_mock_automation_recovery_decision(
        self, value: MockAutomationRecoveryDecision,
    ) -> bool:
        value = mock_automation_recovery_decision_from_dict(value.to_dict())
        spec = self.load_mock_automation_spec(value.account_ref, value.spec_id)
        if spec is None:
            raise ValueError("mock automation recovery requires a stored operating spec")
        admission_document = self._find(
            self.MOCK_AUTOMATION_ADMISSION_COLLECTION,
            value.account_ref,
            value.admission_id,
        )
        receipt_document = self._find(
            self.MOCK_AUTOMATION_LEASE_COLLECTION,
            value.account_ref,
            value.lease_receipt_id,
        )
        if admission_document is None or receipt_document is None:
            raise ValueError("mock automation recovery requires stored admission and lease receipt")
        admission = mock_automation_admission_from_dict(admission_document)
        receipt = mock_automation_lease_receipt_from_dict(receipt_document)
        if (
            admission.spec_id != value.spec_id
            or admission.execution_run_id != value.execution_run_id
            or receipt.admission_id != value.admission_id
            or receipt.execution_run_id != value.execution_run_id
            or receipt.new_orders_enabled is not False
        ):
            raise ValueError("mock automation recovery does not match admission lineage")
        if value.risk_snapshot_id:
            risk = self.load_latest_mock_automation_risk(value.account_ref)
            if risk is None or (
                risk.snapshot_id != value.risk_snapshot_id
                or risk.reconciliation_revision != value.risk_reconciliation_revision
                or risk.execution_run_id != value.execution_run_id
            ):
                raise ValueError("mock automation recovery does not match current risk evidence")
        saved = self._save_immutable(
            self.MOCK_AUTOMATION_RECOVERY_COLLECTION,
            value.account_ref,
            value.decision_id,
            value.to_dict(),
        )
        current = self._find(
            self.MOCK_AUTOMATION_CURRENT_RECOVERY_COLLECTION,
            value.account_ref,
            value.admission_id,
        )
        if current is None or datetime.fromisoformat(str(current["observed_at"])) <= value.observed_at:
            self._store.upsert_documents(
                self.MOCK_AUTOMATION_CURRENT_RECOVERY_COLLECTION,
                [{"owner": value.account_ref, "key": value.admission_id,
                  "document": value.to_dict()}],
            )
        return saved

    def load_latest_mock_automation_recovery(
        self, account_ref: str, admission_id: str,
    ) -> MockAutomationRecoveryDecision | None:
        document = self._find(
            self.MOCK_AUTOMATION_CURRENT_RECOVERY_COLLECTION,
            account_ref,
            admission_id,
        )
        return mock_automation_recovery_decision_from_dict(document) if document else None

    def load_mock_automation_recovery_decisions(
        self, account_ref: str,
    ) -> tuple[MockAutomationRecoveryDecision, ...]:
        rows = self._store.load_documents(
            self.MOCK_AUTOMATION_RECOVERY_COLLECTION, account_ref, 10_000,
        )
        values = [mock_automation_recovery_decision_from_dict(_document(row)) for row in rows]
        return tuple(sorted(values, key=lambda item: (item.observed_at, item.decision_id)))

    def save_mock_automation_decision_gate(
        self, value: MockAutomationDecisionGate,
    ) -> bool:
        value = mock_automation_decision_gate_from_dict(value.to_dict())
        spec = self.load_mock_automation_spec(value.account_ref, value.spec_id)
        admission = self._find(
            self.MOCK_AUTOMATION_ADMISSION_COLLECTION, value.account_ref, value.admission_id,
        )
        lease = self._find(
            self.MOCK_AUTOMATION_LEASE_COLLECTION, value.account_ref, value.lease_receipt_id,
        )
        recovery = self._find(
            self.MOCK_AUTOMATION_RECOVERY_COLLECTION, value.account_ref,
            value.recovery_decision_id,
        )
        if spec is None or admission is None or lease is None or recovery is None:
            raise ValueError("mock automation decision gate requires complete stored lineage")
        admission_value = mock_automation_admission_from_dict(admission)
        lease_value = mock_automation_lease_receipt_from_dict(lease)
        recovery_value = mock_automation_recovery_decision_from_dict(recovery)
        if (
            admission_value.spec_id != value.spec_id
            or admission_value.execution_run_id != value.execution_run_id
            or lease_value.admission_id != value.admission_id
            or recovery_value.admission_id != value.admission_id
            or recovery_value.lease_receipt_id != value.lease_receipt_id
        ):
            raise ValueError("mock automation decision gate lineage is inconsistent")
        if value.risk_snapshot_id:
            risk = self.load_latest_mock_automation_risk(value.account_ref)
            if risk is None or (
                risk.snapshot_id != value.risk_snapshot_id
                or risk.reconciliation_revision != value.risk_reconciliation_revision
                or recovery_value.risk_snapshot_id != value.risk_snapshot_id
            ):
                raise ValueError("mock automation decision gate does not match current risk evidence")
        saved = self._save_immutable(
            self.MOCK_AUTOMATION_GATE_COLLECTION,
            value.account_ref,
            value.gate_id,
            value.to_dict(),
        )
        if value.status is MockAutomationGateStatus.APPROVED_FOR_SINGLE_SUBMISSION:
            self._save_immutable(
                self.MOCK_AUTOMATION_APPROVED_GATE_COLLECTION,
                value.account_ref,
                value.intent_id,
                value.to_dict(),
            )
        return saved

    def load_mock_automation_approved_gate(
        self, account_ref: str, intent_id: str,
    ) -> MockAutomationDecisionGate | None:
        document = self._find(
            self.MOCK_AUTOMATION_APPROVED_GATE_COLLECTION, account_ref, intent_id,
        )
        return mock_automation_decision_gate_from_dict(document) if document else None

    def load_mock_automation_decision_gates(
        self, account_ref: str,
    ) -> tuple[MockAutomationDecisionGate, ...]:
        rows = self._store.load_documents(
            self.MOCK_AUTOMATION_GATE_COLLECTION, account_ref, 10_000,
        )
        values = [mock_automation_decision_gate_from_dict(_document(row)) for row in rows]
        return tuple(sorted(values, key=lambda item: (item.observed_at, item.gate_id)))

    def save_mock_automation_dispatch_receipt(
        self, value: MockAutomationDispatchReceipt,
    ) -> bool:
        value = mock_automation_dispatch_receipt_from_dict(value.to_dict())
        gate_document = self._find(
            self.MOCK_AUTOMATION_GATE_COLLECTION, value.account_ref, value.gate_id,
        )
        if gate_document is None:
            raise ValueError("mock automation dispatch requires a stored decision gate")
        gate = mock_automation_decision_gate_from_dict(gate_document)
        if (
            gate.status is not MockAutomationGateStatus.APPROVED_FOR_SINGLE_SUBMISSION
            or gate.intent_id != value.intent_id
            or gate.execution_run_id != value.execution_run_id
            or gate.strategy_decision_id != value.strategy_decision_id
        ):
            raise ValueError("mock automation dispatch does not match its approved gate")
        saved = self._save_immutable(
            self.MOCK_AUTOMATION_DISPATCH_COLLECTION,
            value.account_ref,
            value.receipt_id,
            value.to_dict(),
        )
        self._save_immutable(
            self.MOCK_AUTOMATION_DISPATCH_BY_INTENT_COLLECTION,
            value.account_ref,
            value.intent_id,
            value.to_dict(),
        )
        return saved

    def load_mock_automation_dispatch_receipt(
        self, account_ref: str, intent_id: str,
    ) -> MockAutomationDispatchReceipt | None:
        document = self._find(
            self.MOCK_AUTOMATION_DISPATCH_BY_INTENT_COLLECTION, account_ref, intent_id,
        )
        return mock_automation_dispatch_receipt_from_dict(document) if document else None

    def load_mock_automation_dispatch_receipts(
        self, account_ref: str,
    ) -> tuple[MockAutomationDispatchReceipt, ...]:
        rows = self._store.load_documents(
            self.MOCK_AUTOMATION_DISPATCH_COLLECTION, account_ref, 10_000,
        )
        values = [mock_automation_dispatch_receipt_from_dict(_document(row)) for row in rows]
        return tuple(sorted(values, key=lambda item: (item.dispatched_at, item.receipt_id)))

    def save_mock_automation_stop_revision(
        self, value: MockAutomationStopRevision,
    ) -> bool:
        value = mock_automation_stop_revision_from_dict(value.to_dict())
        admission_document = self._find(
            self.MOCK_AUTOMATION_ADMISSION_COLLECTION, value.account_ref, value.admission_id,
        )
        if admission_document is None:
            raise ValueError("mock automation stop requires a stored admission")
        admission = mock_automation_admission_from_dict(admission_document)
        if (
            admission.spec_id != value.spec_id
            or admission.execution_run_id != value.execution_run_id
            or value.new_orders_enabled is not False
        ):
            raise ValueError("mock automation stop does not match its admission")
        saved = self._save_immutable(
            self.MOCK_AUTOMATION_STOP_COLLECTION,
            value.account_ref,
            value.revision_id,
            value.to_dict(),
        )
        current = self._find(
            self.MOCK_AUTOMATION_CURRENT_STOP_COLLECTION,
            value.account_ref,
            value.admission_id,
        )
        if current is None or datetime.fromisoformat(str(current["stopped_at"])) <= value.stopped_at:
            self._store.upsert_documents(
                self.MOCK_AUTOMATION_CURRENT_STOP_COLLECTION,
                [{"owner": value.account_ref, "key": value.admission_id,
                  "document": value.to_dict()}],
            )
        return saved

    def load_latest_mock_automation_stop(
        self, account_ref: str, admission_id: str,
    ) -> MockAutomationStopRevision | None:
        document = self._find(
            self.MOCK_AUTOMATION_CURRENT_STOP_COLLECTION,
            account_ref,
            admission_id,
        )
        return mock_automation_stop_revision_from_dict(document) if document else None

    def load_mock_automation_stop_revisions(
        self, account_ref: str,
    ) -> tuple[MockAutomationStopRevision, ...]:
        rows = self._store.load_documents(
            self.MOCK_AUTOMATION_STOP_COLLECTION, account_ref, 10_000,
        )
        values = [mock_automation_stop_revision_from_dict(_document(row)) for row in rows]
        return tuple(sorted(values, key=lambda item: (item.stopped_at, item.revision_id)))

    def save_report(self, value: ForwardEvaluationReport) -> bool:
        value = forward_report_from_dict(value.to_dict())
        return self._save_immutable(
            self.REPORT_COLLECTION, value.profile_id, value.report_id, value.to_dict(),
        )

    def load_reports(self, profile_id: str) -> tuple[ForwardEvaluationReport, ...]:
        rows = self._store.load_documents(self.REPORT_COLLECTION, profile_id, 10_000)
        reports = [forward_report_from_dict(_document(row)) for row in rows]
        return tuple(sorted(reports, key=lambda item: (item.computed_at, item.report_id)))

    def save_feedback_evidence(self, value: FeedbackEvidence) -> bool:
        value = feedback_evidence_from_dict(value.to_dict())
        return self._save_immutable(
            self.FEEDBACK_COLLECTION, value.strategy_ref, value.evidence_id, value.to_dict(),
        )

    def load_feedback_evidence(self, strategy_ref: str) -> tuple[FeedbackEvidence, ...]:
        rows = self._store.load_documents(self.FEEDBACK_COLLECTION, strategy_ref, 10_000)
        values = [feedback_evidence_from_dict(_document(row)) for row in rows]
        return tuple(sorted(values, key=lambda item: (item.frozen_at, item.evidence_id)))

    def save_feedback_review(self, value: FeedbackReviewRevision) -> bool:
        value = feedback_review_from_dict(value.to_dict())
        evidence = self._find(
            self.FEEDBACK_COLLECTION, value.strategy_ref, value.evidence_id,
        )
        if evidence is None:
            raise ValueError("feedback review requires stored source evidence")
        source = feedback_evidence_from_dict(evidence)
        if source.account_scope != value.account_scope or source.strategy_ref != value.strategy_ref:
            raise ValueError("feedback review scope does not match source evidence")
        expected = build_feedback_review_revision(
            source,
            minimum_closed_trade_count=value.minimum_closed_trade_count,
            minimum_active_day_count=value.minimum_active_day_count,
        )
        if expected != value:
            raise ValueError("feedback review does not match stored source evidence and policy")
        return self._save_immutable(
            self.FEEDBACK_REVIEW_COLLECTION,
            value.strategy_ref,
            value.review_id,
            value.to_dict(),
        )

    def load_feedback_reviews(self, strategy_ref: str) -> tuple[FeedbackReviewRevision, ...]:
        rows = self._store.load_documents(
            self.FEEDBACK_REVIEW_COLLECTION, strategy_ref, 10_000,
        )
        values = [feedback_review_from_dict(_document(row)) for row in rows]
        return tuple(sorted(values, key=lambda item: (item.derived_at, item.review_id)))

    def save_feedback_improvement(
        self, value: FeedbackImprovementProposalRevision,
    ) -> bool:
        value = feedback_improvement_from_dict(value.to_dict())
        document = self._find(
            self.FEEDBACK_REVIEW_COLLECTION, value.strategy_ref, value.review_id,
        )
        if document is None:
            raise ValueError("feedback improvement requires a stored source review")
        review = feedback_review_from_dict(document)
        expected_rationale = {
            "POSITIVE": "positive_counterfactual_refinement",
            "NEGATIVE": "negative_counterfactual_recovery",
            "FLAT": "flat_counterfactual_discrimination",
        }.get(review.assessment)
        if (
            not review.eligible_for_improvement_proposal
            or review.evidence_id != value.evidence_id
            or review.account_scope != value.account_scope
            or review.strategy_ref != value.strategy_ref
            or review.derived_at != value.derived_at
            or value.rationale_code != expected_rationale
        ):
            raise ValueError("feedback improvement does not match its eligible source review")
        return self._save_immutable(
            self.FEEDBACK_IMPROVEMENT_COLLECTION,
            value.strategy_ref,
            value.proposal_id,
            value.to_dict(),
        )

    def load_feedback_improvements(
        self, strategy_ref: str,
    ) -> tuple[FeedbackImprovementProposalRevision, ...]:
        rows = self._store.load_documents(
            self.FEEDBACK_IMPROVEMENT_COLLECTION, strategy_ref, 10_000,
        )
        values = [feedback_improvement_from_dict(_document(row)) for row in rows]
        return tuple(sorted(values, key=lambda item: (item.derived_at, item.proposal_id)))

    def save_feedback_strategy_version(self, value: FeedbackStrategyVersion) -> bool:
        value = feedback_strategy_version_from_dict(value.to_dict())
        proposal_document = self._find(
            self.FEEDBACK_IMPROVEMENT_COLLECTION,
            value.parent_strategy_ref,
            value.source_proposal_id,
        )
        if proposal_document is None:
            raise ValueError("feedback strategy version requires a stored source proposal")
        proposal = feedback_improvement_from_dict(proposal_document)
        if (
            proposal.strategy_ref != value.parent_strategy_ref
            or proposal.review_id != value.source_review_id
            or proposal.evidence_id != value.source_evidence_id
            or proposal.account_scope != value.source_account_scope
            or proposal.family_id != value.family_id
            or proposal.factor_allowlist != value.factor_allowlist
            or dict(proposal.proposed_parameters) != dict(value.parameters)
            or proposal.derived_at != value.source_frozen_at
        ):
            raise ValueError("feedback strategy version does not match its source proposal")
        for row in self._store.load_documents(
            self.FEEDBACK_STRATEGY_VERSION_COLLECTION,
            value.parent_strategy_ref,
            10_000,
        ):
            stored = feedback_strategy_version_from_dict(_document(row))
            if (
                stored.source_proposal_id == value.source_proposal_id
                and stored.version_id != value.version_id
            ):
                raise ValueError("feedback proposal already has another adopted strategy version")
        return self._save_immutable(
            self.FEEDBACK_STRATEGY_VERSION_COLLECTION,
            value.parent_strategy_ref,
            value.version_id,
            value.to_dict(),
        )

    def load_feedback_strategy_versions(
        self, parent_strategy_ref: str,
    ) -> tuple[FeedbackStrategyVersion, ...]:
        rows = self._store.load_documents(
            self.FEEDBACK_STRATEGY_VERSION_COLLECTION, parent_strategy_ref, 10_000,
        )
        values = [feedback_strategy_version_from_dict(_document(row)) for row in rows]
        return tuple(sorted(values, key=lambda item: (item.source_frozen_at, item.version_id)))

    def save_feedback_revalidation_request(
        self, value: FeedbackRevalidationRequest,
    ) -> bool:
        value = feedback_revalidation_request_from_dict(value.to_dict())
        version_document = self._find(
            self.FEEDBACK_STRATEGY_VERSION_COLLECTION,
            value.parent_strategy_ref,
            value.strategy_version_ref,
        )
        if version_document is None:
            raise ValueError("feedback revalidation request requires a stored strategy version")
        version = feedback_strategy_version_from_dict(version_document)
        if version.source_proposal_id != value.source_proposal_id:
            raise ValueError("feedback revalidation request does not match its strategy version")
        for row in self._store.load_documents(
            self.FEEDBACK_REVALIDATION_REQUEST_COLLECTION,
            value.parent_strategy_ref,
            10_000,
        ):
            stored = feedback_revalidation_request_from_dict(_document(row))
            if (
                stored.strategy_version_ref == value.strategy_version_ref
                and stored.request_id != value.request_id
            ):
                raise ValueError("feedback strategy version already has another revalidation request")
        return self._save_immutable(
            self.FEEDBACK_REVALIDATION_REQUEST_COLLECTION,
            value.parent_strategy_ref,
            value.request_id,
            value.to_dict(),
        )

    def load_feedback_revalidation_requests(
        self, parent_strategy_ref: str,
    ) -> tuple[FeedbackRevalidationRequest, ...]:
        rows = self._store.load_documents(
            self.FEEDBACK_REVALIDATION_REQUEST_COLLECTION, parent_strategy_ref, 10_000,
        )
        values = [feedback_revalidation_request_from_dict(_document(row)) for row in rows]
        return tuple(sorted(values, key=lambda item: (item.campaign_id, item.request_id)))

    def save_feedback_revalidation_receipt(
        self, value: FeedbackRevalidationReceipt,
    ) -> bool:
        value = feedback_revalidation_receipt_from_dict(value.to_dict())
        version_document = self._find(
            self.FEEDBACK_STRATEGY_VERSION_COLLECTION,
            value.parent_strategy_ref,
            value.strategy_version_ref,
        )
        if version_document is None:
            raise ValueError("feedback revalidation receipt requires a stored strategy version")
        version = feedback_strategy_version_from_dict(version_document)
        if version.source_proposal_id != value.source_proposal_id:
            raise ValueError("feedback revalidation receipt does not match its strategy version")
        request_document = self._find(
            self.FEEDBACK_REVALIDATION_REQUEST_COLLECTION,
            value.parent_strategy_ref,
            value.request_id,
        )
        if request_document is None:
            raise ValueError("feedback revalidation receipt requires a stored request")
        request = feedback_revalidation_request_from_dict(request_document)
        if (
            request.strategy_version_ref != value.strategy_version_ref
            or request.source_proposal_id != value.source_proposal_id
            or request.campaign_id != value.campaign_id
            or request.template_job_id != value.template_job_id
            or request.experiment_id != value.experiment_id
            or request.job_id != value.job_id
        ):
            raise ValueError("feedback revalidation receipt does not match its request")
        for row in self._store.load_documents(
            self.FEEDBACK_REVALIDATION_COLLECTION, limit=10_000,
        ):
            stored = feedback_revalidation_receipt_from_dict(_document(row))
            if (
                stored.strategy_version_ref == value.strategy_version_ref
                and stored.receipt_id != value.receipt_id
            ):
                raise ValueError("feedback strategy version already has another revalidation job")
        return self._save_immutable(
            self.FEEDBACK_REVALIDATION_COLLECTION,
            value.campaign_id,
            value.receipt_id,
            value.to_dict(),
        )

    def load_feedback_revalidation_receipts(
        self, campaign_id: str,
    ) -> tuple[FeedbackRevalidationReceipt, ...]:
        rows = self._store.load_documents(
            self.FEEDBACK_REVALIDATION_COLLECTION, campaign_id, 10_000,
        )
        values = [feedback_revalidation_receipt_from_dict(_document(row)) for row in rows]
        return tuple(sorted(values, key=lambda item: (item.template_job_id, item.receipt_id)))

    def save_stage_revision(self, value: StrategyStageRevision) -> bool:
        value = stage_revision_from_dict(value.to_dict())
        existing = self._find(
            self.STAGE_COLLECTION, value.strategy_ref, value.revision_id,
        )
        if existing is not None:
            if existing != value.to_dict():
                raise ValueError(
                    f"immutable document conflict: {self.STAGE_COLLECTION}/"
                    f"{value.strategy_ref}/{value.revision_id}"
                )
            return False
        latest = self.latest_stage(value.strategy_ref)
        if latest is not value.previous_stage:
            raise ValueError(
                f"strategy stage changed: expected {value.previous_stage.value}, found {latest.value}"
            )
        if (
            value.stage is StrategyLifecycleStage.BROKER_MOCK_VALIDATED
            and not self._has_passing_forward_report(value.strategy_ref, value.evidence_refs)
        ):
            raise ValueError(
                "broker_mock_validated requires a stored finalized PASSED forward report "
                "for the same strategy"
            )
        return self._save_immutable(
            self.STAGE_COLLECTION, value.strategy_ref, value.revision_id, value.to_dict(),
        )

    def load_stage_revisions(self, strategy_ref: str) -> tuple[StrategyStageRevision, ...]:
        rows = self._store.load_documents(self.STAGE_COLLECTION, strategy_ref, 10_000)
        revisions = [stage_revision_from_dict(_document(row)) for row in rows]
        return tuple(sorted(revisions, key=lambda item: (item.decided_at, item.revision_id)))

    def latest_stage(self, strategy_ref: str) -> StrategyLifecycleStage:
        revisions = self.load_stage_revisions(strategy_ref)
        return revisions[-1].stage if revisions else StrategyLifecycleStage.DRAFT

    def _save_immutable(
        self, collection: str, owner: str, key: str, document: Mapping[str, Any],
    ) -> bool:
        existing = self._find(collection, owner, key)
        body = dict(document)
        if existing is not None:
            if existing != body:
                raise ValueError(f"immutable document conflict: {collection}/{owner}/{key}")
            return False
        self._store.upsert_documents(collection, [{"owner": owner, "key": key, "document": body}])
        persisted = self._find(collection, owner, key)
        if persisted != body:
            raise RuntimeError(f"failed to persist immutable document: {collection}/{owner}/{key}")
        return True

    def _find(self, collection: str, owner: str, key: str) -> dict[str, Any] | None:
        row = self._store.load_document(collection, owner, key)
        return _document(row) if row is not None else None

    def _has_passing_forward_report(
        self, strategy_ref: str, evidence_refs: tuple[str, ...],
    ) -> bool:
        expected = set(evidence_refs)
        for profile_row in self._store.load_documents(
            self.PROFILE_COLLECTION, strategy_ref, 10_000,
        ):
            profile = forward_spec_from_dict(_document(profile_row))
            for report_row in self._store.load_documents(
                self.REPORT_COLLECTION, profile.profile_id, 10_000,
            ):
                if str(report_row.get("key", "")) not in expected:
                    continue
                report = forward_report_from_dict(_document(report_row))
                if (
                    report.finalized
                    and report.status is ForwardReportStatus.PASSED
                    and report.promotion_eligible
                ):
                    return True
        return False


def _document(row: Mapping[str, Any]) -> dict[str, Any]:
    value = row.get("document")
    if not isinstance(value, Mapping):
        raise ValueError("central forward-evaluation document is invalid")
    return dict(value)
