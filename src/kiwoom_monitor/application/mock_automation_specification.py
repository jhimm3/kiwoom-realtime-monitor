"""Publish a frozen READY mock-automation operating specification."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Protocol

from kiwoom_monitor.application.mock_automation_candidate import (
    CandidateEligibilityStatus,
    candidate_package_from_dict,
    eligibility_policy_from_dict,
    eligibility_receipt_from_dict,
)
from kiwoom_monitor.application.research_families import (
    get_research_family,
    parse_strategy_config,
    shadow_monitor_id_for_config,
)
from kiwoom_monitor.domain.execution_activation import (
    ForwardCriteria,
    ForwardEvaluationSpec,
    MOCK_CANDIDATE_TRANSITION_POLICY,
    MOCK_RECOVERY_POLICY,
    MOCK_STOP_POLICY,
    MockAutomationOperatingSpec,
    MockAutomationReadiness,
    MockAutomationReadinessStatus,
    StrategyLifecycleStage,
    StrategyStageRevision,
    assess_mock_automation_readiness,
    strategy_stage_revision,
)
from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope


MOCK_AUTOMATION_LIMIT_FIELDS = (
    "maximum_concurrent_strategies", "maximum_concurrent_positions",
    "maximum_capital_won", "maximum_daily_loss_won", "maximum_data_gap_seconds",
    "maximum_submission_unknown_count", "maximum_reconnect_count",
    "maximum_balance_mismatch_count",
)


def matching_shadow_events(
    candidate_publication: Mapping[str, Any], events: tuple[Mapping[str, Any], ...],
) -> tuple[dict[str, Any], ...]:
    package = candidate_package_from_dict(_mapping(candidate_publication, "package"))
    family_id = str(package.candidate_spec.get("family", ""))
    config = parse_strategy_config(family_id, _mapping(package.candidate_spec, "parameters"))
    session = _mapping(package.candidate_spec, "session_profile")
    session_profile = str(session.get("profile", ""))
    monitor_id = shadow_monitor_id_for_config(family_id, config, session_profile)
    strategy_parts = family_id.rsplit("/", 1)
    if len(strategy_parts) != 2:
        return ()
    matched = []
    for value in events:
        if (
            str(value.get("run_id", "")) != monitor_id
            or str(value.get("strategy_id", "")) != strategy_parts[0]
            or str(value.get("strategy_version", "")) != strategy_parts[1]
            or str(value.get("transition", "")) != "flat->candidate"
            or type(value.get("quantity")) is not int
            or int(value["quantity"]) <= 0
        ):
            continue
        try:
            available_at = datetime.fromisoformat(str(value.get("available_at", "")))
        except ValueError:
            continue
        if available_at.tzinfo is not None and str(value.get("event_id", "")):
            matched.append(dict(value))
    return tuple(sorted(
        matched, key=lambda value: (
            datetime.fromisoformat(str(value["available_at"])), str(value["event_id"]),
        ), reverse=True,
    ))


def build_mock_automation_publication_documents(
    *, candidate_publication: Mapping[str, Any], binding: Mapping[str, Any],
    credential_profile_id: str, shadow_event: Mapping[str, Any],
    evaluation_start: datetime, evaluation_end: datetime, frozen_at: datetime,
    criteria: ForwardCriteria, limits: Mapping[str, int],
) -> dict[str, Any]:
    """Build the explicit PC-owned READY publication; this performs no I/O."""
    package = candidate_package_from_dict(_mapping(candidate_publication, "package"))
    policy = eligibility_policy_from_dict(
        _mapping(candidate_publication, "eligibility_policy")
    )
    receipt = eligibility_receipt_from_dict(
        _mapping(candidate_publication, "eligibility_receipt")
    )
    if (
        receipt.status is not CandidateEligibilityStatus.ELIGIBLE
        or policy.tbd_fields
        or receipt.package_hash != package.package_hash
        or receipt.policy_id != policy.policy_id
    ):
        raise ValueError("READY publication requires one complete ELIGIBLE candidate")
    if criteria.tbd_fields:
        raise ValueError("forward criteria contain TBD fields")
    if set(limits) != set(MOCK_AUTOMATION_LIMIT_FIELDS):
        raise ValueError("mock automation limits are incomplete or contain unknown fields")
    matches = matching_shadow_events(candidate_publication, (shadow_event,))
    if not matches:
        raise ValueError("selected shadow event does not match the candidate")
    if str(binding.get("credential_profile_id", "")) != credential_profile_id:
        raise ValueError("candidate binding profile does not match the selected profile")
    account_ref = receipt.account_ref
    if str(binding.get("account_ref", account_ref)) != account_ref:
        raise ValueError("candidate binding account does not match the selected account")
    if str(binding.get("verification_method", "")) != "ka00001":
        raise ValueError("candidate binding is not verified by ka00001")
    binding_verified_at = datetime.fromisoformat(str(binding.get("verified_at", "")))
    shadow_at = datetime.fromisoformat(str(shadow_event["available_at"]))
    if any(value.tzinfo is None for value in (
        binding_verified_at, shadow_at, evaluation_start, evaluation_end, frozen_at,
    )):
        raise ValueError("READY publication times must be timezone-aware")
    if frozen_at < max(binding_verified_at, shadow_at):
        raise ValueError("READY publication freeze predates binding or shadow evidence")

    family_id = str(package.candidate_spec.get("family", ""))
    family = get_research_family(family_id)
    session = _mapping(package.candidate_spec, "session_profile")
    session_profile = str(session.get("profile", ""))
    profile = ForwardEvaluationSpec(
        strategy_ref=package.strategy_ref, family_version=family.family_id,
        factor_versions=family.factor_ids, policy_version=policy.policy_id,
        data_path="nas", account_ref=account_ref, environment="mock",
        evaluation_start=evaluation_start, evaluation_end=evaluation_end,
        frozen_at=frozen_at, criteria=criteria, session_profile=session_profile,
    )
    evaluated_at = package.source_run_finished_at
    validated_at = max(evaluated_at, receipt.evaluated_at)
    shadow_decided_at = max(validated_at, shadow_at)
    stages = (
        strategy_stage_revision(
            strategy_ref=package.strategy_ref,
            previous_stage=StrategyLifecycleStage.DRAFT,
            stage=StrategyLifecycleStage.EVALUATED,
            evidence_refs=(package.package_hash,), reason="final candidate evaluated",
            decided_at=evaluated_at,
        ),
        strategy_stage_revision(
            strategy_ref=package.strategy_ref,
            previous_stage=StrategyLifecycleStage.EVALUATED,
            stage=StrategyLifecycleStage.VALIDATED,
            evidence_refs=(receipt.receipt_id,), reason="eligibility validated",
            decided_at=validated_at,
        ),
        strategy_stage_revision(
            strategy_ref=package.strategy_ref,
            previous_stage=StrategyLifecycleStage.VALIDATED,
            stage=StrategyLifecycleStage.SHADOW,
            evidence_refs=(str(shadow_event["event_id"]),), reason="shadow event observed",
            decided_at=shadow_decided_at,
        ),
    )
    spec = MockAutomationOperatingSpec(
        strategy_ref=package.strategy_ref,
        candidate_package_hash=package.package_hash,
        final_result_hash=package.source_final_result_hash,
        final_batch_id=package.source_final_batch_id,
        final_run_id=package.source_final_run_id,
        forward_profile_id=profile.profile_id,
        account_scope=AccountScope("kiwoom", AccountEnvironment.MOCK, account_ref),
        credential_profile_id=credential_profile_id,
        binding_revision=int(binding.get("binding_revision", 0)),
        binding_verified_at=binding_verified_at, frozen_at=frozen_at,
        shadow_evidence_ref=stages[-1].revision_id,
        supported_venue="KRX", session_profile=session_profile, data_path="nas",
        candidate_transition_policy=MOCK_CANDIDATE_TRANSITION_POLICY,
        stop_policy=MOCK_STOP_POLICY, recovery_policy=MOCK_RECOVERY_POLICY,
        **{name: int(limits[name]) for name in MOCK_AUTOMATION_LIMIT_FIELDS},
    )
    return {
        "account_ref": account_ref,
        "credential_profile_id": credential_profile_id,
        "expected_binding_revision": spec.binding_revision,
        "shadow_event_id": str(shadow_event["event_id"]),
        "forward_profile": profile.to_dict(),
        "stage_revisions": [value.to_dict() for value in stages],
        "operating_spec": spec.to_dict(),
    }


def _mapping(value: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    result = value.get(name)
    if not isinstance(result, Mapping):
        raise ValueError(f"{name} is missing")
    return result


class MockAutomationSpecificationRepository(Protocol):
    def load_mock_automation_candidate_package(self, strategy_ref: str, package_hash: str): ...
    def load_mock_automation_eligibility_policy(self, strategy_ref: str, package_hash: str): ...
    def load_mock_automation_eligibility_receipt(self, account_ref: str, package_hash: str): ...
    def load_stage_revisions(self, strategy_ref: str) -> tuple[StrategyStageRevision, ...]: ...
    def load_profile(self, strategy_ref: str, profile_id: str) -> ForwardEvaluationSpec | None: ...
    def load_mock_automation_spec(
        self, account_ref: str, spec_id: str,
    ) -> MockAutomationOperatingSpec | None: ...
    def save_profile(self, value: ForwardEvaluationSpec) -> bool: ...
    def save_stage_revision(self, value: StrategyStageRevision) -> bool: ...
    def save_mock_automation_spec(self, value: MockAutomationOperatingSpec) -> bool: ...


def publish_ready_mock_automation_spec(
    repository: MockAutomationSpecificationRepository,
    *,
    profile: ForwardEvaluationSpec,
    stage_revisions: tuple[StrategyStageRevision, ...],
    spec: MockAutomationOperatingSpec,
    shadow_event: Mapping[str, Any],
    current_binding: Mapping[str, Any],
) -> tuple[MockAutomationReadiness, bool]:
    """Validate the complete immutable chain before persisting any of its documents."""
    package = repository.load_mock_automation_candidate_package(
        spec.strategy_ref, spec.candidate_package_hash,
    )
    if package is None:
        raise ValueError("mock automation candidate package is not stored")
    policy = repository.load_mock_automation_eligibility_policy(
        spec.strategy_ref, spec.candidate_package_hash,
    )
    receipt = repository.load_mock_automation_eligibility_receipt(
        spec.account_scope.account_ref, spec.candidate_package_hash,
    )
    if (
        policy is None
        or receipt is None
        or receipt.status is not CandidateEligibilityStatus.ELIGIBLE
        or policy.tbd_fields
        or receipt.policy_id != policy.policy_id
        or receipt.candidate_spec_hash != package.candidate_spec_hash
    ):
        raise ValueError("mock automation candidate does not have complete ELIGIBLE evidence")
    if (
        package.source_final_batch_id != spec.final_batch_id
        or package.source_final_run_id != spec.final_run_id
        or package.source_final_result_hash != spec.final_result_hash
    ):
        raise ValueError("candidate final lineage does not match operating spec")

    family_id = str(package.candidate_spec.get("family", ""))
    family = get_research_family(family_id)
    config = parse_strategy_config(
        family_id, package.candidate_spec.get("parameters", {}),
    )
    session = package.candidate_spec.get("session_profile", {})
    package_session = str(session.get("profile", "")) if isinstance(session, Mapping) else ""
    if (
        profile.strategy_ref != package.strategy_ref
        or profile.account_ref != spec.account_scope.account_ref
        or profile.environment != "mock"
        or profile.data_path != "nas"
        or profile.family_version != family.family_id
        or profile.factor_versions != family.factor_ids
        or profile.policy_version != policy.policy_id
        or profile.session_profile != package_session
        or spec.forward_profile_id != profile.profile_id
        or spec.session_profile != package_session
        or spec.data_path != profile.data_path
    ):
        raise ValueError("forward profile does not match the published candidate and operating spec")
    if profile.criteria.tbd_fields:
        raise ValueError("forward profile criteria contain TBD fields")

    expected_stages = (
        StrategyLifecycleStage.EVALUATED,
        StrategyLifecycleStage.VALIDATED,
        StrategyLifecycleStage.SHADOW,
    )
    if len(stage_revisions) != len(expected_stages):
        raise ValueError("operating publication requires evaluated, validated and shadow revisions")
    previous = StrategyLifecycleStage.DRAFT
    for revision, stage in zip(stage_revisions, expected_stages, strict=True):
        if (
            revision.strategy_ref != spec.strategy_ref
            or revision.previous_stage is not previous
            or revision.stage is not stage
        ):
            raise ValueError("strategy stage revisions are not one complete ordered chain")
        previous = stage
    if any(
        later.decided_at < earlier.decided_at
        for earlier, later in zip(stage_revisions, stage_revisions[1:])
    ):
        raise ValueError("strategy stage revision times are not monotonic")
    evaluated, validated, shadow = stage_revisions
    shadow_event_id = str(shadow_event.get("event_id", ""))
    if (
        package.package_hash not in evaluated.evidence_refs
        or receipt.receipt_id not in validated.evidence_refs
        or shadow_event_id not in shadow.evidence_refs
        or spec.shadow_evidence_ref != shadow.revision_id
    ):
        raise ValueError("strategy stage evidence does not match candidate publication")
    expected_monitor_id = shadow_monitor_id_for_config(family_id, config, package_session)
    family_parts = family_id.rsplit("/", 1)
    shadow_quantity = shadow_event.get("quantity")
    if (
        len(family_parts) != 2
        or str(shadow_event.get("run_id", "")) != expected_monitor_id
        or str(shadow_event.get("strategy_id", "")) != family_parts[0]
        or str(shadow_event.get("strategy_version", "")) != family_parts[1]
        or str(shadow_event.get("transition", "")) != "flat->candidate"
        or type(shadow_quantity) is not int
        or shadow_quantity <= 0
    ):
        raise ValueError("shadow evidence does not match the published candidate configuration")
    try:
        shadow_at = datetime.fromisoformat(str(shadow_event.get("available_at", "")))
    except (TypeError, ValueError):
        raise ValueError("shadow evidence available_at is invalid") from None
    if (
        shadow_at.tzinfo is None
        or shadow_at > shadow.decided_at
        or shadow.decided_at > spec.frozen_at
    ):
        raise ValueError(
            "shadow evidence must be timezone-aware, observed before the shadow revision, "
            "and decided before spec freeze"
        )

    if (
        str(current_binding.get("credential_profile_id", "")) != spec.credential_profile_id
        or str(current_binding.get("broker", "")) != "kiwoom"
        or str(current_binding.get("environment", "")) != "mock"
        or str(current_binding.get("account_ref", "")) != spec.account_scope.account_ref
        or int(current_binding.get("binding_revision", 0)) != spec.binding_revision
        or str(current_binding.get("verified_at", "")) != spec.binding_verified_at.isoformat()
        or str(current_binding.get("verification_method", "")) != "ka00001"
    ):
        raise ValueError("operating publication requires the current verified mock binding")

    readiness = assess_mock_automation_readiness(
        spec, profile, strategy_stage=StrategyLifecycleStage.SHADOW,
    )
    if readiness.status is not MockAutomationReadinessStatus.READY:
        raise ValueError("mock automation operating spec is BLOCKED: " + ",".join(readiness.reasons))
    existing = repository.load_stage_revisions(spec.strategy_ref)
    if len(existing) > len(stage_revisions) or existing != stage_revisions[:len(existing)]:
        raise ValueError("stored strategy stage history conflicts with publication")
    stored_profile = repository.load_profile(profile.strategy_ref, profile.profile_id)
    if stored_profile is not None and stored_profile != profile:
        raise ValueError("stored forward profile conflicts with publication")
    stored_spec = repository.load_mock_automation_spec(
        spec.account_scope.account_ref, spec.spec_id,
    )
    if stored_spec is not None and stored_spec != spec:
        raise ValueError("stored operating spec conflicts with publication")

    changed = repository.save_profile(profile)
    for revision in stage_revisions[len(existing):]:
        changed = repository.save_stage_revision(revision) or changed
    changed = repository.save_mock_automation_spec(spec) or changed
    return readiness, changed
