"""Publish a frozen READY mock-automation operating specification."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Protocol

from kiwoom_monitor.domain.time_basis import same_aware_instant

from kiwoom_monitor.application.mock_automation_candidate import (
    CandidateEligibilityStatus,
    candidate_package_from_dict,
    eligibility_policy_from_dict,
    eligibility_receipt_from_dict,
    evaluate_candidate_eligibility,
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
    CLOSE_NATIVE_PLAN_MODE, LEADER_NATIVE_PLAN_MODE,
    CloseMockExecutionPolicy,
    PANIC_NATIVE_PLAN_MODE, PanicMockExecutionPolicy,
    MockUnfilledExecutionPolicy,
    MockAutomationReadiness,
    MockAutomationReadinessStatus,
    StrategyLifecycleStage,
    StrategyStageRevision,
    assess_mock_automation_readiness,
    strategy_stage_revision,
)
from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope
from kiwoom_monitor.domain.research_contract import CLOSE_FINAL_CANDIDATE_VERSION, MINUTE_FINAL_CANDIDATE_FAMILIES
from .leader_breakout import LeaderBreakoutConfig, validate_leader_shadow_event
from .close_and_ipo import matches_close_shadow
from .panic_rebound import matches_panic_shadow
from .section12_minute_research import MinuteExperiment


MOCK_AUTOMATION_LIMIT_FIELDS = (
    "maximum_concurrent_strategies", "maximum_concurrent_positions",
    "maximum_capital_won", "maximum_daily_loss_won", "maximum_data_gap_seconds",
    "maximum_submission_unknown_count", "maximum_reconnect_count",
    "maximum_balance_mismatch_count",
)


def _candidate_family_contract(package):
    family_id = str(package.candidate_spec.get("family", ""))
    if package.candidate_spec["version"] == CLOSE_FINAL_CANDIDATE_VERSION:
        if family_id not in MINUTE_FINAL_CANDIDATE_FAMILIES:
            raise ValueError("CLOSE candidate family does not match its version")
        MinuteExperiment.from_document(family_id, _mapping(package.candidate_spec, "parameters"), paper_request=True)
        return family_id, (family_id,)
    family = get_research_family(family_id)
    return family.family_id, family.factor_ids


def matching_shadow_events(
    candidate_publication: Mapping[str, Any], events: tuple[Mapping[str, Any], ...],
) -> tuple[dict[str, Any], ...]:
    package = candidate_package_from_dict(_mapping(candidate_publication, "package"))
    if package.candidate_spec["version"] == CLOSE_FINAL_CANDIDATE_VERSION:
        parameters = package.candidate_spec["parameters"]
        return tuple(sorted(
            (dict(event) for event in events if (
                matches_panic_shadow(parameters, event) if package.candidate_spec['family'] == 'panic_rebound/v1'
                else matches_close_shadow(parameters, event, family=package.candidate_spec['family']))),
            key=lambda event: (datetime.fromisoformat(event["available_at"]), event["event_id"]), reverse=True,
        ))
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
        if isinstance(config, LeaderBreakoutConfig):
            try:
                validate_leader_shadow_event(config, value)
            except (KeyError, TypeError, ValueError):
                continue
            matched.append(dict(value))
            continue
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


def _require_staged_leader_order_policy(package, mode, policy):
    parameters = package.candidate_spec.get('parameters') or {}
    contract = parameters.get('execution_contract') or {}
    if (package.candidate_spec.get('family') == 'leader_breakout/v1'
            and contract.get('version') in {'leader_breakout_execution/v2', 'leader_breakout_execution/v3'}
            and (mode != LEADER_NATIVE_PLAN_MODE or policy is None or policy.accepted_order_management is None)):
        raise ValueError('분할 돌파는 명시 계획과 현재 조건 재판단·정정 정책 v2가 필요합니다')


def build_mock_automation_publication_documents(
    *, candidate_publication: Mapping[str, Any], binding: Mapping[str, Any],
    credential_profile_id: str, shadow_event: Mapping[str, Any],
    evaluation_start: datetime, evaluation_end: datetime, frozen_at: datetime,
    criteria: ForwardCriteria, limits: Mapping[str, int],
    unfilled_execution_policy: MockUnfilledExecutionPolicy | None = None,
    execution_plan_mode: str | None = None,
    close_execution_policy: CloseMockExecutionPolicy | None = None,
    panic_execution_policy: PanicMockExecutionPolicy | None = None,
) -> dict[str, Any]:
    """Build the explicit PC-owned READY publication; this performs no I/O."""
    package = candidate_package_from_dict(_mapping(candidate_publication, "package"))
    _require_staged_leader_order_policy(package, execution_plan_mode, unfilled_execution_policy)
    if package.candidate_spec.get('family') == 'panic_rebound/v1' and (
        execution_plan_mode != PANIC_NATIVE_PLAN_MODE or not isinstance(panic_execution_policy, PanicMockExecutionPolicy)
        or not isinstance(shadow_event.get('panic_entry_parameters'), Mapping)
    ):
        raise ValueError('PANIC READY publication requires an explicit PANIC execution policy')
    if package.candidate_spec["version"] == CLOSE_FINAL_CANDIDATE_VERSION and package.candidate_spec['family'] != 'panic_rebound/v1' and (
        execution_plan_mode != CLOSE_NATIVE_PLAN_MODE or not isinstance(close_execution_policy, CloseMockExecutionPolicy)
    ):
        raise ValueError("CLOSE READY publication requires an explicit CLOSE execution policy")
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

    family_id, factor_ids = _candidate_family_contract(package)
    session = _mapping(package.candidate_spec, "session_profile")
    session_profile = str(session.get("profile", ""))
    profile = ForwardEvaluationSpec(
        strategy_ref=package.strategy_ref, family_version=family_id,
        factor_versions=factor_ids, policy_version=policy.policy_id,
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
        unfilled_execution_policy=unfilled_execution_policy,
        execution_plan_mode=execution_plan_mode,
        close_execution_policy=close_execution_policy,
        panic_execution_policy=panic_execution_policy,
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


def validate_mock_automation_account_selection(repository, selection, *, current_binding,
                                               expected_implementation_hash):
    """Bind a passive account composition to existing complete frozen members.

    This is publication, not admission. Activation must obtain fresh account,
    binding, Risk/control and lease evidence through the existing order owner.
    """
    from kiwoom_monitor.domain.execution_activation import (
        MockAutomationAccountSelectionV2, mock_automation_account_selection_from_dict,
    )
    value = mock_automation_account_selection_from_dict(selection.to_dict())
    binding = current_binding or {}
    if (binding.get('credential_profile_id') != value.credential_profile_id
            or binding.get('account_ref') != value.account_scope.account_ref
            or binding.get('binding_revision') != value.binding_revision
            or binding.get('broker') != value.account_scope.broker
            or binding.get('environment') != value.account_scope.environment.value
            or binding.get('verification_method') != 'ka00001'
            or not same_aware_instant(str(binding.get('verified_at', '')), value.binding_verified_at)):
        raise ValueError('account selection requires the current verified mock binding')
    retained = {}
    if isinstance(value, MockAutomationAccountSelectionV2):
        control = repository.load_mock_automation_control(value.account_scope.account_ref)
        if (control is None or control.active_spec_id != value.primary_spec_id
                or control.execution_run_id != value.retained_admission_refs[0].execution_run_id):
            raise ValueError('replacement requires its original account control and run')
        current_ids = (control.account_selection.member_spec_ids if control.account_selection is not None
                       else (control.active_spec_id,))
        retained = {reference.spec_id: reference for reference in value.retained_admission_refs}
        if control.account_selection != value and (
                not set(current_ids) <= set(retained)
                or not set(retained) <= set(current_ids) | set(value.entry_spec_ids)):
            raise ValueError('replacement must retain every current member admission')
        for identity, reference in retained.items():
            admission = repository.load_mock_automation_admission_for_spec(value.account_scope.account_ref, identity)
            if (admission is None or admission.account_ref != value.account_scope.account_ref
                    or not value.matches_admission(spec_id=identity, admission_id=admission.admission_id,
                        original_selection_id=admission.account_selection_id, execution_run_id=admission.execution_run_id)
                    or reference.execution_run_id != control.execution_run_id):
                raise ValueError('replacement retained admission reference conflict')
    implementations, members = {}, []
    for spec_id in value.member_spec_ids:
        member = repository.load_mock_automation_spec(value.account_scope.account_ref, spec_id,
                                                       require_canonical_document=True)
        if member is None:
            raise ValueError('account selection member is missing: ' + spec_id)
        if member.spec_id != spec_id:
            raise ValueError('account selection member identity conflict')
        if (member.account_scope != value.account_scope or member.credential_profile_id != value.credential_profile_id
                or member.binding_revision != value.binding_revision
                or not same_aware_instant(member.binding_verified_at.isoformat(), value.binding_verified_at)
                or member.frozen_at > value.frozen_at):
            raise ValueError('account selection member scope or freeze conflict')
        if spec_id in retained and not value.allows_entry(spec_id):
            # Original plans remain manageable when their old producer or
            # artifact is no longer selected. This grants no new entry.
            members.append(member)
            continue
        profile = repository.load_profile(member.strategy_ref, member.forward_profile_id)
        if profile is None:
            raise ValueError('account selection member forward profile is missing')
        ready = assess_mock_automation_readiness(member, profile,
            strategy_stage=repository.latest_stage(member.strategy_ref))
        if ready.status is not MockAutomationReadinessStatus.READY:
            raise ValueError('account selection member is not READY: ' + ','.join(ready.reasons))
        package = repository.load_mock_automation_candidate_package(member.strategy_ref, member.candidate_package_hash)
        policy = repository.load_mock_automation_eligibility_policy(member.strategy_ref, member.candidate_package_hash)
        receipt = repository.load_mock_automation_eligibility_receipt(value.account_scope.account_ref, member.candidate_package_hash)
        if (package is None or policy is None or receipt is None or policy.tbd_fields
                or receipt.status is not CandidateEligibilityStatus.ELIGIBLE
                or receipt.to_dict() != evaluate_candidate_eligibility(package, policy,
                    account_ref=value.account_scope.account_ref).to_dict()):
            raise ValueError('account selection member lacks complete ELIGIBLE evidence')
        family_id, factor_ids = _candidate_family_contract(package)
        if (package.strategy_ref != member.strategy_ref or policy.strategy_ref != member.strategy_ref
                or (package.source_final_batch_id, package.source_final_run_id, package.source_final_result_hash)
                    != (member.final_batch_id, member.final_run_id, member.final_result_hash)
                or (profile.family_version, profile.factor_versions, profile.policy_version)
                    != (family_id, factor_ids, policy.policy_id)
                or package.candidate_spec['session_profile']['profile'] != member.session_profile):
            raise ValueError('account selection member candidate lineage conflict')
        if member.session_profile not in implementations:
            implementations[member.session_profile] = expected_implementation_hash(member.session_profile)
        if not implementations[member.session_profile] or implementations[member.session_profile] != package.scientific_implementation_hash:
            raise ValueError('account selection member selected implementation changed')
        members.append(member)
    return tuple(members)


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
    _require_staged_leader_order_policy(package, spec.execution_plan_mode, spec.unfilled_execution_policy)
    if package.candidate_spec.get('family') == 'panic_rebound/v1' and (
        spec.execution_plan_mode != PANIC_NATIVE_PLAN_MODE or not isinstance(spec.panic_execution_policy, PanicMockExecutionPolicy)
        or not isinstance(shadow_event.get('panic_entry_parameters'), Mapping)
    ):
        raise ValueError('PANIC READY publication requires an explicit PANIC execution policy')
    if package.candidate_spec["version"] == CLOSE_FINAL_CANDIDATE_VERSION and package.candidate_spec['family'] != 'panic_rebound/v1' and (
        spec.execution_plan_mode != CLOSE_NATIVE_PLAN_MODE
        or not isinstance(spec.close_execution_policy, CloseMockExecutionPolicy)
    ):
        raise ValueError("CLOSE READY publication requires an explicit CLOSE execution policy")
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

    family_id, factor_ids = _candidate_family_contract(package)
    session = package.candidate_spec.get("session_profile", {})
    package_session = str(session.get("profile", "")) if isinstance(session, Mapping) else ""
    if (
        profile.strategy_ref != package.strategy_ref
        or profile.account_ref != spec.account_scope.account_ref
        or profile.environment != "mock"
        or profile.data_path != "nas"
        or profile.family_version != family_id
        or profile.factor_versions != factor_ids
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
    if not matching_shadow_events({"package": package.to_dict()}, (shadow_event,)):
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
        or not same_aware_instant(str(current_binding.get("verified_at", "")), spec.binding_verified_at)
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
