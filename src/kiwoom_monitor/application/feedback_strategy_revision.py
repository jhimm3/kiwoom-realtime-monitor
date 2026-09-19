"""Adopt one reviewed feedback proposal as a new strategy version for revalidation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Protocol

from kiwoom_monitor.application.forward_evaluation import (
    FeedbackImprovementProposalRevision,
    feedback_improvement_from_dict,
)
from kiwoom_monitor.application.research_families import (
    get_research_family,
    parse_strategy_config,
)
from kiwoom_monitor.application.research_queue import build_research_job_identity
from kiwoom_monitor.application.research_search import ExperimentSpec
from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope


FEEDBACK_STRATEGY_VERSION = "feedback_strategy_version/v1"
FEEDBACK_REVALIDATION_REQUEST_VERSION = "feedback_revalidation_request/v1"
FEEDBACK_REVALIDATION_RECEIPT_VERSION = "feedback_revalidation_receipt/v1"


@dataclass(frozen=True)
class FeedbackStrategyVersion:
    version_id: str
    version: str
    parent_strategy_ref: str
    source_proposal_id: str
    source_review_id: str
    source_evidence_id: str
    source_account_scope: AccountScope
    family_id: str
    factor_allowlist: tuple[str, ...]
    parameters: Mapping[str, Any]
    source_frozen_at: datetime
    adoption_reason: str
    lifecycle_status: str = "REVALIDATION_REQUIRED"

    def __post_init__(self) -> None:
        if (
            not self.version_id.strip()
            or self.version != FEEDBACK_STRATEGY_VERSION
            or not self.parent_strategy_ref.strip()
            or not self.source_proposal_id.strip()
            or not self.source_review_id.strip()
            or not self.source_evidence_id.strip()
            or self.lifecycle_status != "REVALIDATION_REQUIRED"
        ):
            raise ValueError("feedback strategy version identity or state is invalid")
        if self.source_account_scope.environment is not AccountEnvironment.MOCK:
            raise ValueError("feedback strategy version requires mock evidence")
        _require_aware(self.source_frozen_at, "source_frozen_at")
        if (
            not self.adoption_reason.strip()
            or len(self.adoption_reason) > 500
            or "\x00" in self.adoption_reason
        ):
            raise ValueError("feedback strategy adoption reason must be 1 to 500 characters")
        definition = get_research_family(self.family_id)
        if (
            not self.factor_allowlist
            or len(set(self.factor_allowlist)) != len(self.factor_allowlist)
            or not set(self.factor_allowlist) <= set(definition.factor_ids)
        ):
            raise ValueError("feedback strategy factor allowlist is invalid")
        normalized = parse_strategy_config(self.family_id, self.parameters).to_dict()
        if _canonical_json(normalized) != _canonical_json(self.parameters):
            raise ValueError("feedback strategy parameters are not normalized")

    def to_dict(self) -> dict[str, Any]:
        return {
            "version_id": self.version_id,
            "version": self.version,
            "parent_strategy_ref": self.parent_strategy_ref,
            "source_proposal_id": self.source_proposal_id,
            "source_review_id": self.source_review_id,
            "source_evidence_id": self.source_evidence_id,
            "source_account_scope": self.source_account_scope.to_dict(),
            "family_id": self.family_id,
            "factor_allowlist": list(self.factor_allowlist),
            "parameters": dict(self.parameters),
            "source_frozen_at": self.source_frozen_at.isoformat(),
            "adoption_reason": self.adoption_reason,
            "lifecycle_status": self.lifecycle_status,
        }


@dataclass(frozen=True)
class FeedbackRevalidationRequest:
    request_id: str
    version: str
    strategy_version_ref: str
    parent_strategy_ref: str
    source_proposal_id: str
    campaign_id: str
    template_job_id: str
    experiment_id: str
    job_id: str
    state: str = "REQUESTED"

    def __post_init__(self) -> None:
        values = (
            self.request_id, self.strategy_version_ref, self.parent_strategy_ref,
            self.source_proposal_id, self.campaign_id, self.template_job_id,
            self.experiment_id, self.job_id,
        )
        if any(not value.strip() or len(value) > 256 or "\x00" in value for value in values):
            raise ValueError("feedback revalidation request identity is invalid")
        if self.version != FEEDBACK_REVALIDATION_REQUEST_VERSION or self.state != "REQUESTED":
            raise ValueError("feedback revalidation request version or state is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "version": self.version,
            "strategy_version_ref": self.strategy_version_ref,
            "parent_strategy_ref": self.parent_strategy_ref,
            "source_proposal_id": self.source_proposal_id,
            "campaign_id": self.campaign_id,
            "template_job_id": self.template_job_id,
            "experiment_id": self.experiment_id,
            "job_id": self.job_id,
            "state": self.state,
        }


@dataclass(frozen=True)
class FeedbackRevalidationReceipt:
    receipt_id: str
    version: str
    request_id: str
    strategy_version_ref: str
    parent_strategy_ref: str
    source_proposal_id: str
    campaign_id: str
    template_job_id: str
    experiment_id: str
    job_id: str
    queue_status: str = "QUEUED_FOR_DEVELOPMENT"

    def __post_init__(self) -> None:
        values = (
            self.receipt_id, self.request_id, self.strategy_version_ref, self.parent_strategy_ref,
            self.source_proposal_id,
            self.campaign_id, self.template_job_id, self.experiment_id, self.job_id,
        )
        if any(not value.strip() or len(value) > 256 or "\x00" in value for value in values):
            raise ValueError("feedback revalidation receipt identity is invalid")
        if (
            self.version != FEEDBACK_REVALIDATION_RECEIPT_VERSION
            or self.queue_status != "QUEUED_FOR_DEVELOPMENT"
        ):
            raise ValueError("feedback revalidation receipt version or state is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "version": self.version,
            "request_id": self.request_id,
            "strategy_version_ref": self.strategy_version_ref,
            "parent_strategy_ref": self.parent_strategy_ref,
            "source_proposal_id": self.source_proposal_id,
            "campaign_id": self.campaign_id,
            "template_job_id": self.template_job_id,
            "experiment_id": self.experiment_id,
            "job_id": self.job_id,
            "queue_status": self.queue_status,
        }


class FeedbackVersionRepository(Protocol):
    def save_feedback_strategy_version(self, value: FeedbackStrategyVersion) -> bool: ...
    def save_feedback_revalidation_request(
        self, value: FeedbackRevalidationRequest,
    ) -> bool: ...
    def save_feedback_revalidation_receipt(
        self, value: FeedbackRevalidationReceipt,
    ) -> bool: ...


class FeedbackResearchRepository(Protocol):
    def load_campaign_job(self, campaign_id: str, job_id: str) -> Mapping[str, Any] | None: ...
    def enqueue_campaign_experiment(
        self, campaign_id: str, spec: ExperimentSpec, input_path: Path, **kwargs: Any,
    ) -> bool: ...


def build_feedback_strategy_version(
    proposal: FeedbackImprovementProposalRevision,
    *,
    adoption_reason: str,
) -> FeedbackStrategyVersion:
    proposal = feedback_improvement_from_dict(proposal.to_dict())
    document = {
        "version": FEEDBACK_STRATEGY_VERSION,
        "parent_strategy_ref": proposal.strategy_ref,
        "source_proposal_id": proposal.proposal_id,
        "source_review_id": proposal.review_id,
        "source_evidence_id": proposal.evidence_id,
        "source_account_scope": proposal.account_scope.to_dict(),
        "family_id": proposal.family_id,
        "factor_allowlist": list(proposal.factor_allowlist),
        "parameters": dict(proposal.proposed_parameters),
        "source_frozen_at": proposal.derived_at.isoformat(),
        "adoption_reason": adoption_reason,
        "lifecycle_status": "REVALIDATION_REQUIRED",
    }
    return _strategy_version_from_document({
        "version_id": _content_id("feedback_strategy", document), **document,
    })


def feedback_strategy_version_from_dict(value: Mapping[str, Any]) -> FeedbackStrategyVersion:
    version = _strategy_version_from_document(value)
    document = version.to_dict()
    version_id = document.pop("version_id")
    if _content_id("feedback_strategy", document) != version_id:
        raise ValueError("feedback strategy content does not match version_id")
    return version


def feedback_revalidation_request_from_dict(
    value: Mapping[str, Any],
) -> FeedbackRevalidationRequest:
    request = _request_from_document(value)
    document = request.to_dict()
    request_id = document.pop("request_id")
    if _content_id("feedback_revalidation_request", document) != request_id:
        raise ValueError("feedback revalidation request content does not match request_id")
    return request


def build_feedback_revalidation_spec(
    template: ExperimentSpec,
    strategy: FeedbackStrategyVersion,
) -> ExperimentSpec:
    """Apply a version to an existing development template without opening final data."""
    strategy = feedback_strategy_version_from_dict(strategy.to_dict())
    if template.final_holdout_accessed_at or template.final_holdout_access_reason:
        raise ValueError("feedback revalidation cannot use a final holdout template")
    if template.max_trials < 2:
        raise ValueError("feedback revalidation requires baseline and no-trade trial budget")
    if int(template.resource_budget.get("max_generated_candidates", 1_000)) < 2:
        raise ValueError("feedback revalidation candidate budget is too small")
    source_family = get_research_family(template.family_allowlist[0])
    target_family = get_research_family(strategy.family_id)
    if tuple(source_family.contract.get("required_inputs", ())) != tuple(
        target_family.contract.get("required_inputs", ())
    ):
        raise ValueError("feedback strategy requires a different frozen input contract")
    context = dict(template.research_context)
    context["family"] = strategy.family_id
    context["baseline_strategy"] = dict(strategy.parameters)
    context["feedback_revalidation"] = {
        "strategy_version_ref": strategy.version_id,
        "parent_strategy_ref": strategy.parent_strategy_ref,
        "source_proposal_id": strategy.source_proposal_id,
        "source_review_id": strategy.source_review_id,
        "source_evidence_id": strategy.source_evidence_id,
    }
    return ExperimentSpec(
        version=template.version,
        hypothesis_refs=(strategy.version_id,),
        dataset_id=template.dataset_id,
        dataset_hash=template.dataset_hash,
        family_allowlist=(strategy.family_id,),
        factor_allowlist=strategy.factor_allowlist,
        parameter_space={},
        objective=dict(template.objective),
        constraints=dict(template.constraints),
        split_version=template.split_version,
        max_trials=template.max_trials,
        max_seconds=template.max_seconds,
        seed=template.seed,
        generation_mode="manual",
        execution_environment=template.execution_environment,
        stopping_rule=template.stopping_rule,
        include_no_trade_baseline=True,
        cost_stress_multipliers_ppm=(),
        ablations=(),
        resource_budget=dict(template.resource_budget),
        final_holdout_accessed_at="",
        final_holdout_access_reason="",
        research_context=context,
    )


def dispatch_feedback_revalidation(
    version_repository: FeedbackVersionRepository,
    research_repository: FeedbackResearchRepository,
    proposal: FeedbackImprovementProposalRevision,
    *,
    campaign_id: str,
    template_job_id: str,
    adoption_reason: str,
) -> tuple[FeedbackStrategyVersion, FeedbackRevalidationReceipt]:
    """Persist a new version, enqueue its exact development spec, then confirm dispatch."""
    if not campaign_id.strip() or not template_job_id.strip():
        raise ValueError("feedback revalidation campaign and template job are required")
    template_job = research_repository.load_campaign_job(campaign_id, template_job_id)
    if template_job is None:
        raise ValueError("feedback revalidation template job does not exist")
    strategy = build_feedback_strategy_version(proposal, adoption_reason=adoption_reason)
    effective = build_feedback_revalidation_spec(
        ExperimentSpec.from_dict(template_job["request"]), strategy,
    )
    source_template = ExperimentSpec.from_dict(template_job["source_request"])
    source = build_feedback_revalidation_spec(source_template, strategy)
    needs_source = "development_partition" in effective.research_context
    identity = build_research_job_identity(effective)
    receipt_document = {
        "version": FEEDBACK_REVALIDATION_RECEIPT_VERSION,
        "request_id": "",
        "strategy_version_ref": strategy.version_id,
        "parent_strategy_ref": strategy.parent_strategy_ref,
        "source_proposal_id": proposal.proposal_id,
        "campaign_id": campaign_id,
        "template_job_id": template_job_id,
        "experiment_id": identity.experiment_id,
        "job_id": identity.job_id,
        "queue_status": "QUEUED_FOR_DEVELOPMENT",
    }
    request_document = {
        "version": FEEDBACK_REVALIDATION_REQUEST_VERSION,
        "strategy_version_ref": strategy.version_id,
        "parent_strategy_ref": strategy.parent_strategy_ref,
        "source_proposal_id": proposal.proposal_id,
        "campaign_id": campaign_id,
        "template_job_id": template_job_id,
        "experiment_id": identity.experiment_id,
        "job_id": identity.job_id,
        "state": "REQUESTED",
    }
    request = _request_from_document({
        "request_id": _content_id("feedback_revalidation_request", request_document),
        **request_document,
    })
    receipt_document["request_id"] = request.request_id
    receipt = _receipt_from_document({
        "receipt_id": _content_id("feedback_revalidation", receipt_document),
        **receipt_document,
    })

    version_repository.save_feedback_strategy_version(strategy)
    version_repository.save_feedback_revalidation_request(request)
    research_repository.enqueue_campaign_experiment(
        campaign_id,
        effective,
        Path(str(template_job["input_path"])),
        source_kind="hypothesis",
        source_spec=source if needs_source else None,
    )
    queued = research_repository.load_campaign_job(campaign_id, identity.job_id)
    if queued is None or ExperimentSpec.from_dict(queued["request"]).to_dict() != effective.to_dict():
        raise RuntimeError("feedback revalidation queue did not preserve the requested experiment")
    version_repository.save_feedback_revalidation_receipt(receipt)
    return strategy, receipt


def feedback_revalidation_receipt_from_dict(
    value: Mapping[str, Any],
) -> FeedbackRevalidationReceipt:
    receipt = _receipt_from_document(value)
    document = receipt.to_dict()
    receipt_id = document.pop("receipt_id")
    if _content_id("feedback_revalidation", document) != receipt_id:
        raise ValueError("feedback revalidation content does not match receipt_id")
    return receipt


def _strategy_version_from_document(value: Mapping[str, Any]) -> FeedbackStrategyVersion:
    scope = value.get("source_account_scope")
    parameters = value.get("parameters")
    if not isinstance(scope, Mapping) or not isinstance(parameters, Mapping):
        raise ValueError("feedback strategy document is invalid")
    return FeedbackStrategyVersion(
        version_id=str(value["version_id"]),
        version=str(value["version"]),
        parent_strategy_ref=str(value["parent_strategy_ref"]),
        source_proposal_id=str(value["source_proposal_id"]),
        source_review_id=str(value["source_review_id"]),
        source_evidence_id=str(value["source_evidence_id"]),
        source_account_scope=AccountScope(
            str(scope.get("broker") or ""),
            AccountEnvironment(str(scope.get("environment") or "")),
            str(scope.get("account_ref") or ""),
        ),
        family_id=str(value["family_id"]),
        factor_allowlist=tuple(str(item) for item in value.get("factor_allowlist", ())),
        parameters=dict(parameters),
        source_frozen_at=datetime.fromisoformat(str(value["source_frozen_at"])),
        adoption_reason=str(value["adoption_reason"]),
        lifecycle_status=str(value["lifecycle_status"]),
    )


def _request_from_document(value: Mapping[str, Any]) -> FeedbackRevalidationRequest:
    return FeedbackRevalidationRequest(
        request_id=str(value["request_id"]),
        version=str(value["version"]),
        strategy_version_ref=str(value["strategy_version_ref"]),
        parent_strategy_ref=str(value["parent_strategy_ref"]),
        source_proposal_id=str(value["source_proposal_id"]),
        campaign_id=str(value["campaign_id"]),
        template_job_id=str(value["template_job_id"]),
        experiment_id=str(value["experiment_id"]),
        job_id=str(value["job_id"]),
        state=str(value["state"]),
    )


def _receipt_from_document(value: Mapping[str, Any]) -> FeedbackRevalidationReceipt:
    return FeedbackRevalidationReceipt(
        receipt_id=str(value["receipt_id"]),
        version=str(value["version"]),
        request_id=str(value["request_id"]),
        strategy_version_ref=str(value["strategy_version_ref"]),
        parent_strategy_ref=str(value["parent_strategy_ref"]),
        source_proposal_id=str(value["source_proposal_id"]),
        campaign_id=str(value["campaign_id"]),
        template_job_id=str(value["template_job_id"]),
        experiment_id=str(value["experiment_id"]),
        job_id=str(value["job_id"]),
        queue_status=str(value["queue_status"]),
    )


def _content_id(prefix: str, value: Mapping[str, Any]) -> str:
    return f"{prefix}_{hashlib.sha256(_canonical_json(value).encode('utf-8')).hexdigest()}"


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError("feedback strategy document must be canonical JSON data") from exc


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
