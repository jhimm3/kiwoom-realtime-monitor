"""Frozen forward-evaluation and strategy-stage contracts for broker execution."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping

from kiwoom_monitor.application.market_session_schedule import (
    SUPPORTED_RESEARCH_SESSION_PROFILES,
    research_session_profile_document,
)
from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope


MOCK_AUTOMATION_SPEC_VERSION = "mock_automation_operating_spec/v1"
MOCK_CANDIDATE_TRANSITION_POLICY = "wait_until_flat"
MOCK_STOP_POLICY = "disable_new_orders_and_reconcile"
MOCK_RECOVERY_POLICY = "broker_reconcile_before_resume"


class StrategyLifecycleStage(StrEnum):
    DRAFT = "draft"
    EVALUATED = "evaluated"
    VALIDATED = "validated"
    SHADOW = "shadow"
    BROKER_MOCK_VALIDATED = "broker_mock_validated"
    APPROVED_FOR_LIVE = "approved_for_live"
    RETIRED = "retired"


class ForwardReportStatus(StrEnum):
    BLOCKED = "BLOCKED"
    PENDING = "PENDING"
    FAILED = "FAILED"
    PASSED = "PASSED"


class MockAutomationReadinessStatus(StrEnum):
    BLOCKED = "BLOCKED"
    READY = "READY"


@dataclass(frozen=True)
class ForwardCriteria:
    minimum_comparable_observations: int | None = None
    minimum_coverage_ppm: int | None = None
    maximum_p95_delay_ms: int | None = None
    maximum_gap_count: int | None = None
    maximum_submission_unknown_count: int | None = None
    maximum_rejected_order_count: int | None = None
    maximum_cancel_failure_count: int | None = None
    maximum_reconnect_count: int | None = None
    maximum_balance_mismatch_count: int | None = None
    minimum_active_day_count: int | None = None
    minimum_closed_trade_count: int | None = None
    minimum_adjusted_net_pnl_won: int | None = None
    maximum_drawdown_ppm: int | None = None
    maximum_exposure_ppm: int | None = None

    def __post_init__(self) -> None:
        values = asdict(self)
        for name, value in values.items():
            if value is not None and not isinstance(value, int):
                raise ValueError(f"{name} must be an integer or TBD")
            if value is not None and value < 0 and name != "minimum_adjusted_net_pnl_won":
                raise ValueError(f"{name} must not be negative")
        for name in ("minimum_coverage_ppm", "maximum_drawdown_ppm", "maximum_exposure_ppm"):
            value = values[name]
            if value is not None and value > 1_000_000:
                raise ValueError(f"{name} must be between 0 and 1,000,000 ppm")

    @property
    def tbd_fields(self) -> tuple[str, ...]:
        return tuple(name for name, value in asdict(self).items() if value is None)

    def to_dict(self) -> dict[str, int | None]:
        return asdict(self)


@dataclass(frozen=True)
class ForwardEvaluationSpec:
    strategy_ref: str
    family_version: str
    factor_versions: tuple[str, ...]
    policy_version: str
    data_path: str
    account_ref: str
    environment: str
    evaluation_start: datetime
    evaluation_end: datetime
    frozen_at: datetime
    criteria: ForwardCriteria
    session_profile: str | None = None

    def __post_init__(self) -> None:
        for name in ("strategy_ref", "family_version", "policy_version", "account_ref"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        if not self.factor_versions or any(not value.strip() for value in self.factor_versions):
            raise ValueError("factor_versions must contain at least one version")
        if len(set(self.factor_versions)) != len(self.factor_versions):
            raise ValueError("factor_versions must not contain duplicates")
        if self.data_path not in {"nas", "direct"}:
            raise ValueError("data_path must be nas or direct")
        if self.environment != "mock":
            raise ValueError("O2a forward evaluation supports only the mock environment")
        if (
            self.session_profile is not None
            and self.session_profile not in SUPPORTED_RESEARCH_SESSION_PROFILES
        ):
            raise ValueError(f"unsupported research session profile: {self.session_profile}")
        for name in ("evaluation_start", "evaluation_end", "frozen_at"):
            _require_aware(getattr(self, name), name)
        if self.evaluation_end <= self.evaluation_start:
            raise ValueError("evaluation_end must be later than evaluation_start")
        if self.frozen_at > self.evaluation_start:
            raise ValueError("the forward profile must be frozen before evaluation starts")

    @property
    def profile_id(self) -> str:
        return _content_id("forward_profile", self._identity_document())

    def to_dict(self) -> dict[str, Any]:
        return {"profile_id": self.profile_id, **self._identity_document()}

    def _identity_document(self) -> dict[str, Any]:
        document = {
            "strategy_ref": self.strategy_ref,
            "family_version": self.family_version,
            "factor_versions": list(self.factor_versions),
            "policy_version": self.policy_version,
            "data_path": self.data_path,
            "account_ref": self.account_ref,
            "environment": self.environment,
            "evaluation_start": self.evaluation_start.isoformat(),
            "evaluation_end": self.evaluation_end.isoformat(),
            "frozen_at": self.frozen_at.isoformat(),
            "criteria": self.criteria.to_dict(),
        }
        if self.session_profile is not None:
            document["session_profile"] = research_session_profile_document(self.session_profile)
        return document


@dataclass(frozen=True)
class MockAutomationOperatingSpec:
    """Frozen O2-M limits. A complete spec still does not enable order transport."""

    strategy_ref: str
    candidate_package_hash: str
    final_result_hash: str
    final_batch_id: str
    final_run_id: str
    forward_profile_id: str
    account_scope: AccountScope
    credential_profile_id: str
    binding_revision: int
    binding_verified_at: datetime
    frozen_at: datetime
    shadow_evidence_ref: str | None = None
    maximum_concurrent_strategies: int | None = None
    maximum_concurrent_positions: int | None = None
    maximum_capital_won: int | None = None
    maximum_daily_loss_won: int | None = None
    maximum_data_gap_seconds: int | None = None
    maximum_submission_unknown_count: int | None = None
    maximum_reconnect_count: int | None = None
    maximum_balance_mismatch_count: int | None = None
    supported_venue: str | None = None
    session_profile: str | None = None
    data_path: str | None = None
    candidate_transition_policy: str | None = None
    stop_policy: str | None = None
    recovery_policy: str | None = None
    version: str = MOCK_AUTOMATION_SPEC_VERSION

    def __post_init__(self) -> None:
        for name in (
            "strategy_ref", "final_batch_id", "final_run_id", "forward_profile_id",
            "credential_profile_id",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        _require_sha256(self.candidate_package_hash, "candidate_package_hash")
        _require_sha256(self.final_result_hash, "final_result_hash")
        if self.version != MOCK_AUTOMATION_SPEC_VERSION:
            raise ValueError("unsupported mock automation operating spec version")
        if self.account_scope.environment is not AccountEnvironment.MOCK:
            raise ValueError("mock automation requires a verified mock account scope")
        if self.binding_revision <= 0:
            raise ValueError("binding_revision must be positive")
        _require_aware(self.binding_verified_at, "binding_verified_at")
        _require_aware(self.frozen_at, "frozen_at")
        if self.frozen_at < self.binding_verified_at:
            raise ValueError("operating spec cannot predate account verification")

        positive = (
            "maximum_concurrent_strategies", "maximum_concurrent_positions",
            "maximum_capital_won", "maximum_daily_loss_won",
        )
        nonnegative = (
            "maximum_data_gap_seconds", "maximum_submission_unknown_count",
            "maximum_reconnect_count", "maximum_balance_mismatch_count",
        )
        for name in positive:
            value = getattr(self, name)
            if value is not None and (not isinstance(value, int) or value <= 0):
                raise ValueError(f"{name} must be a positive integer or TBD")
        for name in nonnegative:
            value = getattr(self, name)
            if value is not None and (not isinstance(value, int) or value < 0):
                raise ValueError(f"{name} must be a non-negative integer or TBD")
        if self.supported_venue is not None and self.supported_venue != "KRX":
            raise ValueError("Kiwoom mock automation supports only KRX")
        if (
            self.session_profile is not None
            and self.session_profile not in SUPPORTED_RESEARCH_SESSION_PROFILES
        ):
            raise ValueError(f"unsupported research session profile: {self.session_profile}")
        if self.data_path is not None and self.data_path not in {"nas", "direct"}:
            raise ValueError("data_path must be nas, direct or TBD")
        allowed_policies = {
            "candidate_transition_policy": MOCK_CANDIDATE_TRANSITION_POLICY,
            "stop_policy": MOCK_STOP_POLICY,
            "recovery_policy": MOCK_RECOVERY_POLICY,
        }
        for name, expected in allowed_policies.items():
            value = getattr(self, name)
            if value is not None and value != expected:
                raise ValueError(f"unsupported {name}: {value}")

    @property
    def tbd_fields(self) -> tuple[str, ...]:
        names = (
            "shadow_evidence_ref", "maximum_concurrent_strategies",
            "maximum_concurrent_positions", "maximum_capital_won",
            "maximum_daily_loss_won", "maximum_data_gap_seconds",
            "maximum_submission_unknown_count", "maximum_reconnect_count",
            "maximum_balance_mismatch_count", "supported_venue", "session_profile",
            "data_path", "candidate_transition_policy", "stop_policy", "recovery_policy",
        )
        return tuple(name for name in names if getattr(self, name) in {None, ""})

    @property
    def spec_id(self) -> str:
        return _content_id("mock_automation_spec", self._identity_document())

    def to_dict(self) -> dict[str, Any]:
        return {"spec_id": self.spec_id, **self._identity_document()}

    def _identity_document(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "strategy_ref": self.strategy_ref,
            "candidate_package_hash": self.candidate_package_hash,
            "final_result_hash": self.final_result_hash,
            "final_batch_id": self.final_batch_id,
            "final_run_id": self.final_run_id,
            "forward_profile_id": self.forward_profile_id,
            "account_scope": self.account_scope.to_dict(),
            "credential_profile_id": self.credential_profile_id,
            "binding_revision": self.binding_revision,
            "binding_verified_at": self.binding_verified_at.isoformat(),
            "frozen_at": self.frozen_at.isoformat(),
            "shadow_evidence_ref": self.shadow_evidence_ref,
            "maximum_concurrent_strategies": self.maximum_concurrent_strategies,
            "maximum_concurrent_positions": self.maximum_concurrent_positions,
            "maximum_capital_won": self.maximum_capital_won,
            "maximum_daily_loss_won": self.maximum_daily_loss_won,
            "maximum_data_gap_seconds": self.maximum_data_gap_seconds,
            "maximum_submission_unknown_count": self.maximum_submission_unknown_count,
            "maximum_reconnect_count": self.maximum_reconnect_count,
            "maximum_balance_mismatch_count": self.maximum_balance_mismatch_count,
            "supported_venue": self.supported_venue,
            "session_profile": self.session_profile,
            "data_path": self.data_path,
            "candidate_transition_policy": self.candidate_transition_policy,
            "stop_policy": self.stop_policy,
            "recovery_policy": self.recovery_policy,
        }


@dataclass(frozen=True)
class MockAutomationReadiness:
    spec_id: str
    status: MockAutomationReadinessStatus
    reasons: tuple[str, ...]


def assess_mock_automation_readiness(
    spec: MockAutomationOperatingSpec,
    profile: ForwardEvaluationSpec,
    *,
    strategy_stage: StrategyLifecycleStage,
) -> MockAutomationReadiness:
    reasons = [f"TBD:{name}" for name in spec.tbd_fields]
    if profile.profile_id != spec.forward_profile_id:
        reasons.append("FORWARD_PROFILE_MISMATCH")
    if profile.strategy_ref != spec.strategy_ref:
        reasons.append("STRATEGY_MISMATCH")
    if profile.account_ref != spec.account_scope.account_ref:
        reasons.append("ACCOUNT_SCOPE_MISMATCH")
    if profile.environment != "mock":
        reasons.append("FORWARD_PROFILE_NOT_MOCK")
    if profile.criteria.tbd_fields:
        reasons.extend(f"FORWARD_CRITERIA_TBD:{name}" for name in profile.criteria.tbd_fields)
    if profile.session_profile != spec.session_profile:
        reasons.append("SESSION_PROFILE_MISMATCH")
    if profile.data_path != spec.data_path:
        reasons.append("DATA_PATH_MISMATCH")
    if spec.frozen_at > profile.evaluation_start:
        reasons.append("SPEC_FROZEN_AFTER_EVALUATION_START")
    if strategy_stage is not StrategyLifecycleStage.SHADOW:
        reasons.append("STRATEGY_NOT_IN_SHADOW")
    if (
        spec.maximum_concurrent_strategies is not None
        and spec.maximum_concurrent_strategies != 1
    ):
        reasons.append("UNSUPPORTED_CONCURRENT_STRATEGY_COUNT")
    if (
        spec.maximum_concurrent_positions is not None
        and spec.maximum_concurrent_positions != 1
    ):
        reasons.append("UNSUPPORTED_CONCURRENT_POSITION_COUNT")
    if spec.session_profile is not None and spec.session_profile != "krx-regular/v1":
        reasons.append("UNSUPPORTED_MOCK_SESSION_PROFILE")
    unique_reasons = tuple(dict.fromkeys(reasons))
    return MockAutomationReadiness(
        spec_id=spec.spec_id,
        status=(
            MockAutomationReadinessStatus.BLOCKED
            if unique_reasons
            else MockAutomationReadinessStatus.READY
        ),
        reasons=unique_reasons,
    )


@dataclass(frozen=True)
class ForwardEvidence:
    profile_id: str
    as_of: datetime
    finalized: bool
    comparable_observation_count: int
    coverage_ppm: int | None
    p95_delay_ms: int | None
    gap_count: int
    submission_unknown_count: int
    rejected_order_count: int
    cancel_failure_count: int
    reconnect_count: int
    balance_mismatch_count: int
    no_trade_count: int
    active_day_count: int
    closed_trade_count: int
    broker_net_pnl_won: int | None
    broker_reported_cost_won: int | None
    additional_unmodeled_cost_won: int | None
    max_drawdown_ppm: int | None
    exposure_ppm: int | None

    def __post_init__(self) -> None:
        if not self.profile_id.strip():
            raise ValueError("profile_id is required")
        _require_aware(self.as_of, "as_of")
        nonnegative = (
            "comparable_observation_count", "gap_count", "submission_unknown_count",
            "rejected_order_count", "cancel_failure_count", "reconnect_count",
            "balance_mismatch_count", "no_trade_count", "active_day_count",
            "closed_trade_count", "broker_reported_cost_won",
            "additional_unmodeled_cost_won", "max_drawdown_ppm", "exposure_ppm",
        )
        for name in nonnegative:
            value = getattr(self, name)
            if value is not None and (not isinstance(value, int) or value < 0):
                raise ValueError(f"{name} must be a non-negative integer when known")
        if self.coverage_ppm is not None and (
            not isinstance(self.coverage_ppm, int) or not 0 <= self.coverage_ppm <= 1_000_000
        ):
            raise ValueError("coverage_ppm must be an integer between 0 and 1,000,000")
        if self.p95_delay_ms is not None and (
            not isinstance(self.p95_delay_ms, int) or self.p95_delay_ms < 0
        ):
            raise ValueError("p95_delay_ms must be a non-negative integer")
        for name in ("max_drawdown_ppm", "exposure_ppm"):
            value = getattr(self, name)
            if value is not None and value > 1_000_000:
                raise ValueError(f"{name} must not exceed 1,000,000 ppm")

    @property
    def adjusted_net_pnl_won(self) -> int | None:
        if self.broker_net_pnl_won is None or self.additional_unmodeled_cost_won is None:
            return None
        return self.broker_net_pnl_won - self.additional_unmodeled_cost_won

    def to_dict(self) -> dict[str, Any]:
        document = asdict(self)
        document["as_of"] = self.as_of.isoformat()
        document["adjusted_net_pnl_won"] = self.adjusted_net_pnl_won
        return document


@dataclass(frozen=True)
class StrategyStageRevision:
    revision_id: str
    strategy_ref: str
    previous_stage: StrategyLifecycleStage
    stage: StrategyLifecycleStage
    evidence_refs: tuple[str, ...]
    reason: str
    decided_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision_id": self.revision_id,
            "strategy_ref": self.strategy_ref,
            "previous_stage": self.previous_stage.value,
            "stage": self.stage.value,
            "evidence_refs": list(self.evidence_refs),
            "reason": self.reason,
            "decided_at": self.decided_at.isoformat(),
        }


_NEXT_STAGE = {
    StrategyLifecycleStage.DRAFT: StrategyLifecycleStage.EVALUATED,
    StrategyLifecycleStage.EVALUATED: StrategyLifecycleStage.VALIDATED,
    StrategyLifecycleStage.VALIDATED: StrategyLifecycleStage.SHADOW,
    StrategyLifecycleStage.SHADOW: StrategyLifecycleStage.BROKER_MOCK_VALIDATED,
}


def strategy_stage_revision(
    *, strategy_ref: str, previous_stage: StrategyLifecycleStage,
    stage: StrategyLifecycleStage, evidence_refs: tuple[str, ...],
    reason: str, decided_at: datetime,
) -> StrategyStageRevision:
    if not strategy_ref.strip() or not reason.strip():
        raise ValueError("strategy_ref and reason are required")
    _require_aware(decided_at, "decided_at")
    if stage is StrategyLifecycleStage.APPROVED_FOR_LIVE:
        raise ValueError("approved_for_live requires the separate O2b live approval boundary")
    if stage is not StrategyLifecycleStage.RETIRED and _NEXT_STAGE.get(previous_stage) is not stage:
        raise ValueError(f"invalid strategy stage transition: {previous_stage.value} -> {stage.value}")
    refs = tuple(dict.fromkeys(value.strip() for value in evidence_refs if value.strip()))
    if stage is not StrategyLifecycleStage.RETIRED and not refs:
        raise ValueError("stage promotion requires immutable evidence references")
    body: Mapping[str, Any] = {
        "strategy_ref": strategy_ref,
        "previous_stage": previous_stage.value,
        "stage": stage.value,
        "evidence_refs": list(refs),
        "reason": reason,
        "decided_at": decided_at.isoformat(),
    }
    return StrategyStageRevision(
        revision_id=_content_id("strategy_stage", body), strategy_ref=strategy_ref,
        previous_stage=previous_stage, stage=stage, evidence_refs=refs,
        reason=reason, decided_at=decided_at,
    )


def forward_spec_from_dict(value: Mapping[str, Any]) -> ForwardEvaluationSpec:
    criteria = value.get("criteria")
    if not isinstance(criteria, Mapping):
        raise ValueError("forward profile criteria are missing")
    spec = ForwardEvaluationSpec(
        strategy_ref=str(value["strategy_ref"]),
        family_version=str(value["family_version"]),
        factor_versions=tuple(str(item) for item in value["factor_versions"]),
        policy_version=str(value["policy_version"]),
        data_path=str(value["data_path"]), account_ref=str(value["account_ref"]),
        environment=str(value["environment"]),
        evaluation_start=datetime.fromisoformat(str(value["evaluation_start"])),
        evaluation_end=datetime.fromisoformat(str(value["evaluation_end"])),
        frozen_at=datetime.fromisoformat(str(value["frozen_at"])),
        criteria=ForwardCriteria(**{name: criteria.get(name) for name in ForwardCriteria.__dataclass_fields__}),
        session_profile=(
            str(value["session_profile"].get("profile", ""))
            if isinstance(value.get("session_profile"), Mapping)
            else str(value["session_profile"])
            if value.get("session_profile") is not None
            else None
        ),
    )
    expected = str(value.get("profile_id", spec.profile_id))
    if expected != spec.profile_id:
        raise ValueError("forward profile content does not match profile_id")
    return spec


def mock_automation_spec_from_dict(value: Mapping[str, Any]) -> MockAutomationOperatingSpec:
    scope = value.get("account_scope")
    if not isinstance(scope, Mapping):
        raise ValueError("mock automation account_scope is missing")
    spec = MockAutomationOperatingSpec(
        version=str(value.get("version", "")),
        strategy_ref=str(value["strategy_ref"]),
        candidate_package_hash=str(value["candidate_package_hash"]),
        final_result_hash=str(value["final_result_hash"]),
        final_batch_id=str(value["final_batch_id"]),
        final_run_id=str(value["final_run_id"]),
        forward_profile_id=str(value["forward_profile_id"]),
        account_scope=AccountScope(
            broker=str(scope["broker"]),
            environment=AccountEnvironment(str(scope["environment"])),
            account_ref=str(scope["account_ref"]),
        ),
        credential_profile_id=str(value["credential_profile_id"]),
        binding_revision=int(value["binding_revision"]),
        binding_verified_at=datetime.fromisoformat(str(value["binding_verified_at"])),
        frozen_at=datetime.fromisoformat(str(value["frozen_at"])),
        shadow_evidence_ref=_optional_text(value.get("shadow_evidence_ref")),
        maximum_concurrent_strategies=_optional_int(value.get("maximum_concurrent_strategies")),
        maximum_concurrent_positions=_optional_int(value.get("maximum_concurrent_positions")),
        maximum_capital_won=_optional_int(value.get("maximum_capital_won")),
        maximum_daily_loss_won=_optional_int(value.get("maximum_daily_loss_won")),
        maximum_data_gap_seconds=_optional_int(value.get("maximum_data_gap_seconds")),
        maximum_submission_unknown_count=_optional_int(
            value.get("maximum_submission_unknown_count")
        ),
        maximum_reconnect_count=_optional_int(value.get("maximum_reconnect_count")),
        maximum_balance_mismatch_count=_optional_int(
            value.get("maximum_balance_mismatch_count")
        ),
        supported_venue=_optional_text(value.get("supported_venue")),
        session_profile=_optional_text(value.get("session_profile")),
        data_path=_optional_text(value.get("data_path")),
        candidate_transition_policy=_optional_text(value.get("candidate_transition_policy")),
        stop_policy=_optional_text(value.get("stop_policy")),
        recovery_policy=_optional_text(value.get("recovery_policy")),
    )
    expected = str(value.get("spec_id", spec.spec_id))
    if expected != spec.spec_id:
        raise ValueError("mock automation operating spec content does not match spec_id")
    return spec


def stage_revision_from_dict(value: Mapping[str, Any]) -> StrategyStageRevision:
    revision = StrategyStageRevision(
        revision_id=str(value["revision_id"]), strategy_ref=str(value["strategy_ref"]),
        previous_stage=StrategyLifecycleStage(str(value["previous_stage"])),
        stage=StrategyLifecycleStage(str(value["stage"])),
        evidence_refs=tuple(str(item) for item in value.get("evidence_refs", ())),
        reason=str(value["reason"]), decided_at=datetime.fromisoformat(str(value["decided_at"])),
    )
    expected = strategy_stage_revision(
        strategy_ref=revision.strategy_ref, previous_stage=revision.previous_stage,
        stage=revision.stage, evidence_refs=revision.evidence_refs,
        reason=revision.reason, decided_at=revision.decided_at,
    )
    if expected.revision_id != revision.revision_id:
        raise ValueError("strategy stage revision content does not match revision_id")
    return revision


def _content_id(prefix: str, value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()}"


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _require_sha256(value: str, name: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
