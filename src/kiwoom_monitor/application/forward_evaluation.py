"""Build an O2a forward report without activating any broker order path."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from kiwoom_monitor.application.journal_enrichment import JournalResearchLink
from kiwoom_monitor.application.research_families import (
    get_research_family,
    parse_strategy_config,
)
from kiwoom_monitor.application.trade_cost_service import allocate_episode_cost
from kiwoom_monitor.application.trade_history_query_service import (
    TradeHistoryReconciliationResult,
)
from kiwoom_monitor.application.trade_journal_summary import group_trade_episodes
from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope

from kiwoom_monitor.domain.execution_activation import (
    ForwardEvaluationSpec,
    ForwardEvidence,
    ForwardReportStatus,
)


FEEDBACK_EVIDENCE_VERSION = "journal_feedback_evidence/v1"
FEEDBACK_REVIEW_VERSION = "journal_feedback_review/v1"
FEEDBACK_REVIEW_POLICY_VERSION = "journal_feedback_review_policy/v1"
FEEDBACK_IMPROVEMENT_VERSION = "journal_feedback_improvement/v1"
FEEDBACK_IMPROVEMENT_POLICY_VERSION = "registered_single_parameter_feedback/v1"
_KST = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class ForwardGateResult:
    name: str
    section: str
    status: str
    observed: int | None
    threshold: int | None
    direction: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ForwardEvaluationReport:
    report_id: str
    profile_id: str
    status: ForwardReportStatus
    computed_at: datetime
    evidence_as_of: datetime
    finalized: bool
    promotion_eligible: bool
    reasons: tuple[str, ...]
    data_metrics: Mapping[str, int | None]
    system_metrics: Mapping[str, int | None]
    performance_metrics: Mapping[str, int | None]
    gates: tuple[ForwardGateResult, ...]
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_aware(self.computed_at, "computed_at")
        _require_aware(self.evidence_as_of, "evidence_as_of")
        if self.computed_at < self.evidence_as_of:
            raise ValueError("computed_at must not predate the evidence")
        if self.promotion_eligible is not (self.status is ForwardReportStatus.PASSED):
            raise ValueError("promotion_eligible must match PASSED status")

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id, "profile_id": self.profile_id,
            "status": self.status.value, "computed_at": self.computed_at.isoformat(),
            "evidence_as_of": self.evidence_as_of.isoformat(),
            "finalized": self.finalized, "promotion_eligible": self.promotion_eligible,
            "reasons": list(self.reasons), "data_metrics": dict(self.data_metrics),
            "system_metrics": dict(self.system_metrics),
            "performance_metrics": dict(self.performance_metrics),
            "gates": [gate.to_dict() for gate in self.gates],
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True)
class FeedbackTradeOutcome:
    outcome_id: str
    stock_code: str
    started_at: datetime
    ended_at: datetime
    run_ids: tuple[str, ...]
    decision_ids: tuple[str, ...]
    fill_statuses: tuple[str, ...]
    fill_confirmed: bool
    cost_confirmed: bool
    closed: bool
    gross_realized_pnl_won: int | None
    broker_reported_cost_won: int | None
    broker_net_pnl_won: int | None
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.outcome_id.strip() or not self.stock_code.strip():
            raise ValueError("feedback outcome identity is incomplete")
        _require_aware(self.started_at, "started_at")
        _require_aware(self.ended_at, "ended_at")
        if self.ended_at < self.started_at:
            raise ValueError("feedback outcome interval is invalid")
        values = (
            self.gross_realized_pnl_won,
            self.broker_reported_cost_won,
            self.broker_net_pnl_won,
        )
        if any(value is None for value in values) and any(value is not None for value in values):
            raise ValueError("feedback outcome financial values must be complete or absent")
        if self.broker_reported_cost_won is not None and self.broker_reported_cost_won < 0:
            raise ValueError("feedback broker cost must not be negative")
        if all(value is not None for value in values) and not (
            self.fill_confirmed and self.cost_confirmed and self.closed
        ):
            raise ValueError("unconfirmed feedback outcome cannot contain financial values")

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat(),
            "run_ids": list(self.run_ids),
            "decision_ids": list(self.decision_ids),
            "fill_statuses": list(self.fill_statuses),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class FeedbackEvidence:
    evidence_id: str
    version: str
    account_scope: AccountScope
    strategy_ref: str
    evaluation_start: datetime
    evaluation_end: datetime
    evidence_as_of: datetime
    frozen_at: datetime
    finalized: bool
    selected_run_ids: tuple[str, ...]
    selection_declared_at: datetime | None
    selection_bias_status: str
    point_in_time_status: str
    research_exposure_status: str
    eligible_for_strategy_feedback: bool
    outcomes: tuple[FeedbackTradeOutcome, ...]
    fill_quality_counts: Mapping[str, int]
    source_event_ids: tuple[str, ...]
    broker_execution_ids: tuple[str, ...]
    research_link_ids: tuple[str, ...]
    research_evidence_timing_counts: Mapping[str, int]
    active_day_count: int
    closed_trade_count: int
    broker_net_pnl_won: int | None
    broker_reported_cost_won: int | None
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            not self.evidence_id.strip()
            or self.version != FEEDBACK_EVIDENCE_VERSION
            or not self.strategy_ref.strip()
        ):
            raise ValueError("feedback evidence version or strategy_ref is invalid")
        if self.account_scope.environment is not AccountEnvironment.MOCK:
            raise ValueError("feedback evidence requires a verified mock account")
        for name in ("evaluation_start", "evaluation_end", "evidence_as_of", "frozen_at"):
            _require_aware(getattr(self, name), name)
        if self.evaluation_end <= self.evaluation_start:
            raise ValueError("feedback evaluation interval is invalid")
        if self.evidence_as_of > self.frozen_at:
            raise ValueError("feedback cannot be frozen before its evidence")
        if self.finalized and self.evidence_as_of < self.evaluation_end:
            raise ValueError("finalized feedback must cover the evaluation interval")
        if self.selection_declared_at is not None:
            _require_aware(self.selection_declared_at, "selection_declared_at")
        if self.selection_bias_status not in {
            "ALL_ACCOUNT_TRADES", "PREDECLARED", "POST_HOC", "UNVERIFIED",
        }:
            raise ValueError("feedback selection bias status is invalid")
        if self.point_in_time_status not in {
            "AT_EXECUTION", "POST_TRADE_INCLUDED", "UNVERIFIED",
        }:
            raise ValueError("feedback point-in-time status is invalid")
        if self.research_exposure_status not in {
            "DEVELOPMENT_ONLY", "FINAL_EXPOSED", "UNVERIFIED",
        }:
            raise ValueError("feedback research exposure status is invalid")
        expected_statuses = {"EXACT", "DETAIL_ONLY", "SUMMARY_ONLY", "PARTIAL", "CONFLICT"}
        if set(self.fill_quality_counts) != expected_statuses or any(
            type(value) is not int or value < 0 for value in self.fill_quality_counts.values()
        ):
            raise ValueError("feedback fill quality counts are invalid")
        if self.active_day_count < 0 or not 0 <= self.closed_trade_count <= len(self.outcomes):
            raise ValueError("feedback outcome counts are invalid")
        if self.active_day_count > self.closed_trade_count:
            raise ValueError("feedback active days cannot exceed closed trades")
        if (
            (self.broker_net_pnl_won is None) != (self.broker_reported_cost_won is None)
            or (self.broker_reported_cost_won is not None and self.broker_reported_cost_won < 0)
        ):
            raise ValueError("feedback aggregate financial values are invalid")
        if self.eligible_for_strategy_feedback and (
            not self.finalized
            or self.selection_bias_status != "PREDECLARED"
            or self.point_in_time_status != "AT_EXECUTION"
            or self.research_exposure_status != "DEVELOPMENT_ONLY"
            or self.broker_net_pnl_won is None
        ):
            raise ValueError("eligible feedback requires finalized unbiased confirmed evidence")

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "version": self.version,
            "account_scope": self.account_scope.to_dict(),
            "strategy_ref": self.strategy_ref,
            "evaluation_start": self.evaluation_start.isoformat(),
            "evaluation_end": self.evaluation_end.isoformat(),
            "evidence_as_of": self.evidence_as_of.isoformat(),
            "frozen_at": self.frozen_at.isoformat(),
            "finalized": self.finalized,
            "selected_run_ids": list(self.selected_run_ids),
            "selection_declared_at": (
                self.selection_declared_at.isoformat() if self.selection_declared_at else None
            ),
            "selection_bias_status": self.selection_bias_status,
            "point_in_time_status": self.point_in_time_status,
            "research_exposure_status": self.research_exposure_status,
            "eligible_for_strategy_feedback": self.eligible_for_strategy_feedback,
            "outcomes": [outcome.to_dict() for outcome in self.outcomes],
            "fill_quality_counts": dict(self.fill_quality_counts),
            "source_event_ids": list(self.source_event_ids),
            "broker_execution_ids": list(self.broker_execution_ids),
            "research_link_ids": list(self.research_link_ids),
            "research_evidence_timing_counts": dict(self.research_evidence_timing_counts),
            "active_day_count": self.active_day_count,
            "closed_trade_count": self.closed_trade_count,
            "broker_net_pnl_won": self.broker_net_pnl_won,
            "broker_reported_cost_won": self.broker_reported_cost_won,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class FeedbackReviewRevision:
    """Deterministic machine review kept separate from user-authored journal text."""

    review_id: str
    version: str
    policy_version: str
    evidence_id: str
    account_scope: AccountScope
    strategy_ref: str
    derived_at: datetime
    assessment: str
    eligible_for_improvement_proposal: bool
    minimum_closed_trade_count: int
    minimum_active_day_count: int
    reviewed_outcome_ids: tuple[str, ...]
    metrics: Mapping[str, int | None]
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            not self.review_id.strip()
            or self.version != FEEDBACK_REVIEW_VERSION
            or self.policy_version != FEEDBACK_REVIEW_POLICY_VERSION
            or not self.evidence_id.strip()
            or not self.strategy_ref.strip()
        ):
            raise ValueError("feedback review identity or version is invalid")
        if self.account_scope.environment is not AccountEnvironment.MOCK:
            raise ValueError("feedback review requires a verified mock account")
        _require_aware(self.derived_at, "derived_at")
        if self.assessment not in {
            "BLOCKED", "INSUFFICIENT_SAMPLE", "POSITIVE", "NEGATIVE", "FLAT",
        }:
            raise ValueError("feedback review assessment is invalid")
        if self.minimum_closed_trade_count < 1 or self.minimum_active_day_count < 1:
            raise ValueError("feedback review sample policy must be positive")
        expected_metrics = {
            "active_day_count", "closed_trade_count", "profitable_trade_count",
            "losing_trade_count", "flat_trade_count", "gross_realized_pnl_won",
            "broker_reported_cost_won", "broker_net_pnl_won", "win_rate_ppm",
            "cost_to_abs_gross_ppm", "largest_gain_won", "largest_loss_won",
        }
        if set(self.metrics) != expected_metrics or any(
            value is not None and type(value) is not int for value in self.metrics.values()
        ):
            raise ValueError("feedback review metrics are invalid")
        if self.eligible_for_improvement_proposal and self.assessment not in {
            "POSITIVE", "NEGATIVE", "FLAT",
        }:
            raise ValueError("only a completed feedback review can propose an improvement")
        if self.assessment in {"BLOCKED", "INSUFFICIENT_SAMPLE"} and not self.reasons:
            raise ValueError("blocked feedback review requires a reason")

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "version": self.version,
            "policy_version": self.policy_version,
            "evidence_id": self.evidence_id,
            "account_scope": self.account_scope.to_dict(),
            "strategy_ref": self.strategy_ref,
            "derived_at": self.derived_at.isoformat(),
            "assessment": self.assessment,
            "eligible_for_improvement_proposal": self.eligible_for_improvement_proposal,
            "minimum_closed_trade_count": self.minimum_closed_trade_count,
            "minimum_active_day_count": self.minimum_active_day_count,
            "reviewed_outcome_ids": list(self.reviewed_outcome_ids),
            "metrics": dict(self.metrics),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class FeedbackImprovementProposalRevision:
    """One registered parameter counterfactual; it does not modify the strategy."""

    proposal_id: str
    version: str
    policy_version: str
    review_id: str
    evidence_id: str
    account_scope: AccountScope
    strategy_ref: str
    derived_at: datetime
    family_id: str
    factor_allowlist: tuple[str, ...]
    baseline_parameters: Mapping[str, Any]
    proposed_parameters: Mapping[str, Any]
    allowed_parameter_values: Mapping[str, tuple[int, ...]]
    changed_parameter: str
    changed_from: int
    changed_to: int
    rationale_code: str
    status: str = "READY_FOR_REVIEW"

    def __post_init__(self) -> None:
        if (
            not self.proposal_id.strip()
            or self.version != FEEDBACK_IMPROVEMENT_VERSION
            or self.policy_version != FEEDBACK_IMPROVEMENT_POLICY_VERSION
            or not self.review_id.strip()
            or not self.evidence_id.strip()
            or not self.strategy_ref.strip()
            or self.status != "READY_FOR_REVIEW"
        ):
            raise ValueError("feedback improvement proposal identity or status is invalid")
        if self.account_scope.environment is not AccountEnvironment.MOCK:
            raise ValueError("feedback improvement proposal requires a mock account")
        _require_aware(self.derived_at, "derived_at")
        definition = get_research_family(self.family_id)
        if (
            not self.factor_allowlist
            or len(set(self.factor_allowlist)) != len(self.factor_allowlist)
            or not set(self.factor_allowlist) <= set(definition.factor_ids)
        ):
            raise ValueError("feedback proposal factor allowlist is invalid")
        baseline = parse_strategy_config(
            self.family_id, self.baseline_parameters,
        ).to_dict()
        proposed = parse_strategy_config(
            self.family_id, self.proposed_parameters,
        ).to_dict()
        if _canonical_document(baseline) != _canonical_document(self.baseline_parameters):
            raise ValueError("feedback proposal baseline is not normalized")
        if _canonical_document(proposed) != _canonical_document(self.proposed_parameters):
            raise ValueError("feedback proposal parameters are not normalized")
        allowed = _normalize_feedback_improvement_policy(
            self.family_id, baseline, self.allowed_parameter_values,
        )
        if allowed != dict(self.allowed_parameter_values):
            raise ValueError("feedback proposal allowed values are not normalized")
        differences = tuple(
            key for key in sorted(set(baseline) | set(proposed))
            if baseline.get(key) != proposed.get(key)
        )
        if differences != (self.changed_parameter,):
            raise ValueError("feedback proposal must change exactly one parameter")
        if (
            self.changed_parameter not in definition.searchable_parameters
            or isinstance(self.changed_from, bool)
            or isinstance(self.changed_to, bool)
            or not isinstance(self.changed_from, int)
            or not isinstance(self.changed_to, int)
            or baseline[self.changed_parameter] != self.changed_from
            or proposed[self.changed_parameter] != self.changed_to
            or self.changed_from == self.changed_to
            or self.changed_to not in allowed[self.changed_parameter]
        ):
            raise ValueError("feedback proposal change is outside the registered policy")
        if self.rationale_code not in {
            "positive_counterfactual_refinement",
            "negative_counterfactual_recovery",
            "flat_counterfactual_discrimination",
        }:
            raise ValueError("feedback proposal rationale is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "version": self.version,
            "policy_version": self.policy_version,
            "review_id": self.review_id,
            "evidence_id": self.evidence_id,
            "account_scope": self.account_scope.to_dict(),
            "strategy_ref": self.strategy_ref,
            "derived_at": self.derived_at.isoformat(),
            "family_id": self.family_id,
            "factor_allowlist": list(self.factor_allowlist),
            "baseline_parameters": dict(self.baseline_parameters),
            "proposed_parameters": dict(self.proposed_parameters),
            "allowed_parameter_values": {
                key: list(values) for key, values in self.allowed_parameter_values.items()
            },
            "changed_parameter": self.changed_parameter,
            "changed_from": self.changed_from,
            "changed_to": self.changed_to,
            "rationale_code": self.rationale_code,
            "status": self.status,
        }


def build_feedback_evidence(
    *,
    account_scope: AccountScope,
    strategy_ref: str,
    evaluation_start: datetime,
    evaluation_end: datetime,
    evidence_as_of: datetime,
    frozen_at: datetime,
    history: TradeHistoryReconciliationResult,
    finalized: bool,
    selected_run_ids: tuple[str, ...] = (),
    selection_declared_at: datetime | None = None,
    research_links: tuple[JournalResearchLink, ...] = (),
    research_exposure_status: str = "UNVERIFIED",
) -> FeedbackEvidence:
    """Freeze account-scoped fills, costs and research lineage without changing the journal."""
    for name, value in (
        ("evaluation_start", evaluation_start), ("evaluation_end", evaluation_end),
        ("evidence_as_of", evidence_as_of), ("frozen_at", frozen_at),
    ):
        _require_aware(value, name)
    if evaluation_end <= evaluation_start or evidence_as_of > frozen_at:
        raise ValueError("feedback evidence interval or freeze time is invalid")
    if finalized and evidence_as_of < evaluation_end:
        raise ValueError("finalized feedback must cover the evaluation interval")
    if account_scope.environment is not AccountEnvironment.MOCK:
        raise ValueError("feedback evidence requires a verified mock account")
    selected_runs = tuple(dict.fromkeys(value.strip() for value in selected_run_ids if value.strip()))
    if selection_declared_at is not None:
        _require_aware(selection_declared_at, "selection_declared_at")
    selection_status = _feedback_selection_status(
        selected_runs, selection_declared_at, evaluation_start,
    )
    if any(link.effective_scope != account_scope for link in research_links):
        raise ValueError("feedback research link crossed the account scope")
    if research_exposure_status not in {
        "DEVELOPMENT_ONLY", "FINAL_EXPOSED", "UNVERIFIED",
    }:
        raise ValueError("feedback research exposure status is invalid")

    all_groups = history.reconciliation.groups
    groups = tuple(
        group for group in all_groups
        if not selected_runs or any(
            evidence.run_id in selected_runs for evidence in group.detailed_fills
        )
    )
    selected_fills = tuple(
        fill for group in groups for fill in group.selected_fills
    )
    episodes = tuple(
        episode for episode in group_trade_episodes(selected_fills)
        if any(_feedback_fill_in_interval(fill.filled_at, evaluation_start, evaluation_end)
               for fill in episode.fills)
    )
    fill_groups_by_object = {
        id(fill): group for group in groups for fill in group.selected_fills
    }
    outcomes: list[FeedbackTradeOutcome] = []
    for episode in episodes:
        episode_groups = tuple(dict.fromkeys(
            fill_groups_by_object[id(fill)] for fill in episode.fills
        ))
        cost = allocate_episode_cost(
            episode, history.reconciliation.fills, history.costs,
        )
        fill_confirmed = bool(episode_groups) and all(
            group.fill_confirmed for group in episode_groups
        )
        closed = episode.summary.open_quantity == 0 and episode.summary.matched_cost > 0
        reasons: list[str] = []
        if not fill_confirmed:
            reasons.extend(
                f"fill_{status.lower()}" for status in sorted({
                    group.status for group in episode_groups if not group.fill_confirmed
                })
            )
        if not cost.complete:
            reasons.append("broker_cost_missing")
        if not closed:
            reasons.append("position_not_closed")
        financially_confirmed = fill_confirmed and cost.complete and closed
        run_ids = tuple(sorted({
            evidence.run_id for group in episode_groups
            for evidence in group.detailed_fills if evidence.run_id
        }))
        decision_ids = tuple(sorted({
            evidence.decision_id for group in episode_groups
            for evidence in group.detailed_fills if evidence.decision_id
        }))
        started_at = _feedback_aware_time(episode.started_at)
        ended_at = _feedback_aware_time(episode.ended_at)
        identity = {
            "account_scope": account_scope.to_dict(),
            "stock_code": episode.summary.stock_code,
            "started_at": started_at.isoformat(),
            "ended_at": ended_at.isoformat(),
            "run_ids": list(run_ids),
            "decision_ids": list(decision_ids),
            "fill_statuses": sorted({group.status for group in episode_groups}),
        }
        outcomes.append(FeedbackTradeOutcome(
            outcome_id=_content_id("feedback_outcome", identity),
            stock_code=episode.summary.stock_code,
            started_at=started_at,
            ended_at=ended_at,
            run_ids=run_ids,
            decision_ids=decision_ids,
            fill_statuses=tuple(identity["fill_statuses"]),
            fill_confirmed=fill_confirmed,
            cost_confirmed=cost.complete,
            closed=closed,
            gross_realized_pnl_won=(
                episode.summary.realized_profit if financially_confirmed else None
            ),
            broker_reported_cost_won=(cost.total_cost if financially_confirmed else None),
            broker_net_pnl_won=(cost.net_realized_profit if financially_confirmed else None),
            reasons=tuple(dict.fromkeys(reasons)),
        ))
    outcomes_tuple = tuple(sorted(outcomes, key=lambda value: (value.started_at, value.outcome_id)))
    financially_complete = bool(outcomes_tuple) and all(
        outcome.fill_confirmed and outcome.cost_confirmed and outcome.closed
        for outcome in outcomes_tuple
    )
    confirmed_outcomes = tuple(
        outcome for outcome in outcomes_tuple
        if outcome.fill_confirmed and outcome.cost_confirmed and outcome.closed
    )
    source_event_ids = tuple(sorted({
        evidence.source_event_id for group in groups for evidence in group.detailed_fills
    }))
    broker_execution_ids = tuple(sorted({
        evidence.broker_execution_id for group in groups for evidence in group.detailed_fills
    }))
    outcome_run_ids = {run_id for outcome in outcomes_tuple for run_id in outcome.run_ids}
    outcome_decision_ids = {
        decision_id for outcome in outcomes_tuple for decision_id in outcome.decision_ids
    }
    relevant_links = tuple(link for link in research_links if (
        link.execution_ref in set(source_event_ids) | set(broker_execution_ids)
        or (link.run_id is not None and link.run_id in outcome_run_ids)
        or (link.decision_id is not None and link.decision_id in outcome_decision_ids)
    ))
    point_in_time_status = _feedback_point_in_time_status(relevant_links)
    reasons: list[str] = []
    if not outcomes_tuple:
        reasons.append("no_feedback_outcomes")
    if not financially_complete:
        reasons.append("financial_evidence_incomplete")
    if selection_status != "PREDECLARED":
        reasons.append(f"selection_{selection_status.lower()}")
    if point_in_time_status != "AT_EXECUTION":
        reasons.append(f"point_in_time_{point_in_time_status.lower()}")
    if research_exposure_status != "DEVELOPMENT_ONLY":
        reasons.append(f"research_exposure_{research_exposure_status.lower()}")
    if not finalized:
        reasons.append("evaluation_interval_not_finalized")
    reasons.extend(
        reason for outcome in outcomes_tuple for reason in outcome.reasons
    )
    quality_counts = {
        status: sum(group.status == status for group in groups)
        for status in ("EXACT", "DETAIL_ONLY", "SUMMARY_ONLY", "PARTIAL", "CONFLICT")
    }
    eligible = (
        finalized
        and financially_complete
        and selection_status == "PREDECLARED"
        and point_in_time_status == "AT_EXECUTION"
        and research_exposure_status == "DEVELOPMENT_ONLY"
    )
    document = {
        "version": FEEDBACK_EVIDENCE_VERSION,
        "account_scope": account_scope.to_dict(),
        "strategy_ref": strategy_ref,
        "evaluation_start": evaluation_start.isoformat(),
        "evaluation_end": evaluation_end.isoformat(),
        "evidence_as_of": evidence_as_of.isoformat(),
        "frozen_at": frozen_at.isoformat(),
        "finalized": finalized,
        "selected_run_ids": list(selected_runs),
        "selection_declared_at": selection_declared_at.isoformat() if selection_declared_at else None,
        "selection_bias_status": selection_status,
        "point_in_time_status": point_in_time_status,
        "research_exposure_status": research_exposure_status,
        "eligible_for_strategy_feedback": eligible,
        "outcomes": [outcome.to_dict() for outcome in outcomes_tuple],
        "fill_quality_counts": quality_counts,
        "source_event_ids": list(source_event_ids),
        "broker_execution_ids": list(broker_execution_ids),
        "research_link_ids": sorted({link.link_id for link in relevant_links}),
        "research_evidence_timing_counts": {
            timing: sum(link.evidence_timing == timing for link in relevant_links)
            for timing in ("at_execution", "post_trade", "unverified")
        },
        "active_day_count": len({outcome.started_at.date() for outcome in confirmed_outcomes}),
        "closed_trade_count": len(confirmed_outcomes),
        "broker_net_pnl_won": (
            sum(int(outcome.broker_net_pnl_won or 0) for outcome in outcomes_tuple)
            if financially_complete else None
        ),
        "broker_reported_cost_won": (
            sum(int(outcome.broker_reported_cost_won or 0) for outcome in outcomes_tuple)
            if financially_complete else None
        ),
        "reasons": list(dict.fromkeys(reasons)),
    }
    return _feedback_evidence_from_document({
        "evidence_id": _content_id("feedback_evidence", document), **document,
    })


def feedback_evidence_from_dict(value: Mapping[str, Any]) -> FeedbackEvidence:
    evidence = _feedback_evidence_from_document(value)
    document = evidence.to_dict()
    evidence_id = document.pop("evidence_id")
    if _content_id("feedback_evidence", document) != evidence_id:
        raise ValueError("feedback evidence content does not match evidence_id")
    return evidence


def build_feedback_review_revision(
    feedback: FeedbackEvidence,
    *,
    minimum_closed_trade_count: int,
    minimum_active_day_count: int,
) -> FeedbackReviewRevision:
    """Derive a repeatable review without editing user notes or strategy source."""
    feedback = feedback_evidence_from_dict(feedback.to_dict())
    if minimum_closed_trade_count < 1 or minimum_active_day_count < 1:
        raise ValueError("feedback review sample policy must be positive")
    complete_outcomes = tuple(
        outcome for outcome in feedback.outcomes
        if outcome.fill_confirmed
        and outcome.cost_confirmed
        and outcome.closed
        and outcome.gross_realized_pnl_won is not None
        and outcome.broker_reported_cost_won is not None
        and outcome.broker_net_pnl_won is not None
    )
    net_values = tuple(int(outcome.broker_net_pnl_won or 0) for outcome in complete_outcomes)
    gross_values = tuple(
        int(outcome.gross_realized_pnl_won or 0) for outcome in complete_outcomes
    )
    profitable_count = sum(value > 0 for value in net_values)
    losing_count = sum(value < 0 for value in net_values)
    flat_count = sum(value == 0 for value in net_values)
    gross_absolute_total = sum(abs(value) for value in gross_values)
    metrics: dict[str, int | None] = {
        "active_day_count": feedback.active_day_count,
        "closed_trade_count": feedback.closed_trade_count,
        "profitable_trade_count": profitable_count,
        "losing_trade_count": losing_count,
        "flat_trade_count": flat_count,
        "gross_realized_pnl_won": (
            sum(gross_values) if feedback.eligible_for_strategy_feedback else None
        ),
        "broker_reported_cost_won": (
            feedback.broker_reported_cost_won
            if feedback.eligible_for_strategy_feedback else None
        ),
        "broker_net_pnl_won": (
            feedback.broker_net_pnl_won
            if feedback.eligible_for_strategy_feedback else None
        ),
        "win_rate_ppm": (
            profitable_count * 1_000_000 // len(net_values) if net_values else None
        ),
        "cost_to_abs_gross_ppm": (
            int(feedback.broker_reported_cost_won or 0) * 1_000_000
            // gross_absolute_total
            if feedback.eligible_for_strategy_feedback and gross_absolute_total else None
        ),
        "largest_gain_won": max((value for value in net_values if value > 0), default=0),
        "largest_loss_won": min((value for value in net_values if value < 0), default=0),
    }
    reasons: list[str] = []
    if not feedback.eligible_for_strategy_feedback:
        assessment = "BLOCKED"
        reasons.append("source_feedback_not_eligible")
        reasons.extend(feedback.reasons)
    elif feedback.closed_trade_count < minimum_closed_trade_count:
        assessment = "INSUFFICIENT_SAMPLE"
        reasons.append("minimum_closed_trade_count_not_met")
    elif feedback.active_day_count < minimum_active_day_count:
        assessment = "INSUFFICIENT_SAMPLE"
        reasons.append("minimum_active_day_count_not_met")
    else:
        net_pnl = int(feedback.broker_net_pnl_won or 0)
        assessment = "POSITIVE" if net_pnl > 0 else "NEGATIVE" if net_pnl < 0 else "FLAT"
    eligible = assessment in {"POSITIVE", "NEGATIVE", "FLAT"}
    document = {
        "version": FEEDBACK_REVIEW_VERSION,
        "policy_version": FEEDBACK_REVIEW_POLICY_VERSION,
        "evidence_id": feedback.evidence_id,
        "account_scope": feedback.account_scope.to_dict(),
        "strategy_ref": feedback.strategy_ref,
        "derived_at": feedback.frozen_at.isoformat(),
        "assessment": assessment,
        "eligible_for_improvement_proposal": eligible,
        "minimum_closed_trade_count": minimum_closed_trade_count,
        "minimum_active_day_count": minimum_active_day_count,
        "reviewed_outcome_ids": [outcome.outcome_id for outcome in complete_outcomes],
        "metrics": metrics,
        "reasons": list(dict.fromkeys(reasons)),
    }
    return _feedback_review_from_document({
        "review_id": _content_id("feedback_review", document), **document,
    })


def feedback_review_from_dict(value: Mapping[str, Any]) -> FeedbackReviewRevision:
    review = _feedback_review_from_document(value)
    document = review.to_dict()
    review_id = document.pop("review_id")
    if _content_id("feedback_review", document) != review_id:
        raise ValueError("feedback review content does not match review_id")
    return review


def generate_feedback_improvement_proposals(
    review: FeedbackReviewRevision,
    *,
    family_id: str,
    factor_allowlist: tuple[str, ...],
    baseline_parameters: Mapping[str, Any],
    allowed_parameter_values: Mapping[str, tuple[int, ...]],
    seed: int,
    max_proposals: int,
) -> tuple[FeedbackImprovementProposalRevision, ...]:
    """Generate bounded registered counterfactuals without selecting or applying one."""
    review = feedback_review_from_dict(review.to_dict())
    if not review.eligible_for_improvement_proposal:
        raise ValueError("feedback review is not eligible for an improvement proposal")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("feedback proposal seed must be an integer")
    if (
        isinstance(max_proposals, bool)
        or not isinstance(max_proposals, int)
        or not 0 <= max_proposals <= 1_000
    ):
        raise ValueError("max_proposals must be between 0 and 1000")
    definition = get_research_family(family_id)
    factors = tuple(factor_allowlist)
    if (
        not factors
        or len(set(factors)) != len(factors)
        or not set(factors) <= set(definition.factor_ids)
    ):
        raise ValueError("feedback proposal factor allowlist is invalid")
    baseline = parse_strategy_config(family_id, baseline_parameters).to_dict()
    if _canonical_document(baseline) != _canonical_document(baseline_parameters):
        raise ValueError("feedback proposal baseline is not normalized")
    allowed = _normalize_feedback_improvement_policy(
        family_id, baseline, allowed_parameter_values,
    )
    candidates = [
        (key, value)
        for key in sorted(allowed)
        for value in allowed[key]
        if baseline[key] != value
    ]
    candidates.sort(key=lambda item: (
        hashlib.sha256(
            f"{seed}:{review.review_id}:{item[0]}:{item[1]}".encode("utf-8")
        ).hexdigest(),
        item[0],
        item[1],
    ))
    rationale = {
        "POSITIVE": "positive_counterfactual_refinement",
        "NEGATIVE": "negative_counterfactual_recovery",
        "FLAT": "flat_counterfactual_discrimination",
    }[review.assessment]
    proposals: list[FeedbackImprovementProposalRevision] = []
    for key, value in candidates[:max_proposals]:
        proposed = dict(baseline)
        proposed[key] = value
        proposed = parse_strategy_config(family_id, proposed).to_dict()
        document = {
            "version": FEEDBACK_IMPROVEMENT_VERSION,
            "policy_version": FEEDBACK_IMPROVEMENT_POLICY_VERSION,
            "review_id": review.review_id,
            "evidence_id": review.evidence_id,
            "account_scope": review.account_scope.to_dict(),
            "strategy_ref": review.strategy_ref,
            "derived_at": review.derived_at.isoformat(),
            "family_id": family_id,
            "factor_allowlist": list(factors),
            "baseline_parameters": baseline,
            "proposed_parameters": proposed,
            "allowed_parameter_values": {
                name: list(values) for name, values in allowed.items()
            },
            "changed_parameter": key,
            "changed_from": baseline[key],
            "changed_to": value,
            "rationale_code": rationale,
            "status": "READY_FOR_REVIEW",
        }
        proposals.append(_feedback_improvement_from_document({
            "proposal_id": _content_id("feedback_improvement", document), **document,
        }))
    return tuple(proposals)


def feedback_improvement_from_dict(
    value: Mapping[str, Any],
) -> FeedbackImprovementProposalRevision:
    proposal = _feedback_improvement_from_document(value)
    document = proposal.to_dict()
    proposal_id = document.pop("proposal_id")
    if _content_id("feedback_improvement", document) != proposal_id:
        raise ValueError("feedback improvement content does not match proposal_id")
    return proposal


def forward_evidence_from_feedback(
    spec: ForwardEvaluationSpec,
    feedback: FeedbackEvidence,
    *,
    validation_summary: Mapping[str, Any],
    execution_events: Iterable[Mapping[str, Any]],
    additional_unmodeled_cost_won: int | None = None,
    max_drawdown_ppm: int | None = None,
    exposure_ppm: int | None = None,
) -> ForwardEvidence:
    """Adapt only finalized, predeclared, point-in-time-safe journal feedback."""
    if (
        feedback.strategy_ref != spec.strategy_ref
        or feedback.account_scope.account_ref != spec.account_ref
        or feedback.account_scope.environment.value != spec.environment
        or feedback.evaluation_start != spec.evaluation_start
        or feedback.evaluation_end != spec.evaluation_end
    ):
        raise ValueError("feedback evidence does not match the forward profile")
    if not feedback.eligible_for_strategy_feedback:
        raise ValueError("feedback evidence is not eligible for strategy evaluation")
    performance = {
        "active_day_count": feedback.active_day_count,
        "closed_trade_count": feedback.closed_trade_count,
        "broker_net_pnl_won": feedback.broker_net_pnl_won,
        "broker_reported_cost_won": feedback.broker_reported_cost_won,
        "additional_unmodeled_cost_won": additional_unmodeled_cost_won,
        "max_drawdown_ppm": max_drawdown_ppm,
        "exposure_ppm": exposure_ppm,
    }
    return forward_evidence_from_sources(
        profile_id=spec.profile_id,
        as_of=feedback.evidence_as_of,
        finalized=feedback.finalized,
        validation_summary=validation_summary,
        execution_events=execution_events,
        performance=performance,
    )


def _feedback_evidence_from_document(value: Mapping[str, Any]) -> FeedbackEvidence:
    scope = value.get("account_scope")
    if not isinstance(scope, Mapping):
        raise ValueError("feedback account scope is invalid")
    account_scope = AccountScope(
        str(scope.get("broker") or ""),
        AccountEnvironment(str(scope.get("environment") or "")),
        str(scope.get("account_ref") or ""),
    )
    outcomes = tuple(
        FeedbackTradeOutcome(
            outcome_id=str(row["outcome_id"]),
            stock_code=str(row["stock_code"]),
            started_at=datetime.fromisoformat(str(row["started_at"])),
            ended_at=datetime.fromisoformat(str(row["ended_at"])),
            run_ids=tuple(str(item) for item in row.get("run_ids", ())),
            decision_ids=tuple(str(item) for item in row.get("decision_ids", ())),
            fill_statuses=tuple(str(item) for item in row.get("fill_statuses", ())),
            fill_confirmed=bool(row["fill_confirmed"]),
            cost_confirmed=bool(row["cost_confirmed"]),
            closed=bool(row["closed"]),
            gross_realized_pnl_won=(
                int(row["gross_realized_pnl_won"])
                if row.get("gross_realized_pnl_won") is not None else None
            ),
            broker_reported_cost_won=(
                int(row["broker_reported_cost_won"])
                if row.get("broker_reported_cost_won") is not None else None
            ),
            broker_net_pnl_won=(
                int(row["broker_net_pnl_won"])
                if row.get("broker_net_pnl_won") is not None else None
            ),
            reasons=tuple(str(item) for item in row.get("reasons", ())),
        )
        for row in value.get("outcomes", ())
        if isinstance(row, Mapping)
    )
    declared = value.get("selection_declared_at")
    return FeedbackEvidence(
        evidence_id=str(value["evidence_id"]),
        version=str(value["version"]),
        account_scope=account_scope,
        strategy_ref=str(value["strategy_ref"]),
        evaluation_start=datetime.fromisoformat(str(value["evaluation_start"])),
        evaluation_end=datetime.fromisoformat(str(value["evaluation_end"])),
        evidence_as_of=datetime.fromisoformat(str(value["evidence_as_of"])),
        frozen_at=datetime.fromisoformat(str(value["frozen_at"])),
        finalized=bool(value["finalized"]),
        selected_run_ids=tuple(str(item) for item in value.get("selected_run_ids", ())),
        selection_declared_at=datetime.fromisoformat(str(declared)) if declared else None,
        selection_bias_status=str(value["selection_bias_status"]),
        point_in_time_status=str(value["point_in_time_status"]),
        research_exposure_status=str(value["research_exposure_status"]),
        eligible_for_strategy_feedback=bool(value["eligible_for_strategy_feedback"]),
        outcomes=outcomes,
        fill_quality_counts={
            str(key): int(count) for key, count in dict(value.get("fill_quality_counts", {})).items()
        },
        source_event_ids=tuple(str(item) for item in value.get("source_event_ids", ())),
        broker_execution_ids=tuple(str(item) for item in value.get("broker_execution_ids", ())),
        research_link_ids=tuple(str(item) for item in value.get("research_link_ids", ())),
        research_evidence_timing_counts={
            str(key): int(count)
            for key, count in dict(value.get("research_evidence_timing_counts", {})).items()
        },
        active_day_count=int(value["active_day_count"]),
        closed_trade_count=int(value["closed_trade_count"]),
        broker_net_pnl_won=(
            int(value["broker_net_pnl_won"])
            if value.get("broker_net_pnl_won") is not None else None
        ),
        broker_reported_cost_won=(
            int(value["broker_reported_cost_won"])
            if value.get("broker_reported_cost_won") is not None else None
        ),
        reasons=tuple(str(item) for item in value.get("reasons", ())),
    )


def _feedback_review_from_document(value: Mapping[str, Any]) -> FeedbackReviewRevision:
    scope = value.get("account_scope")
    metrics = value.get("metrics")
    if not isinstance(scope, Mapping) or not isinstance(metrics, Mapping):
        raise ValueError("feedback review scope or metrics are invalid")
    return FeedbackReviewRevision(
        review_id=str(value["review_id"]),
        version=str(value["version"]),
        policy_version=str(value["policy_version"]),
        evidence_id=str(value["evidence_id"]),
        account_scope=AccountScope(
            str(scope.get("broker") or ""),
            AccountEnvironment(str(scope.get("environment") or "")),
            str(scope.get("account_ref") or ""),
        ),
        strategy_ref=str(value["strategy_ref"]),
        derived_at=datetime.fromisoformat(str(value["derived_at"])),
        assessment=str(value["assessment"]),
        eligible_for_improvement_proposal=bool(
            value["eligible_for_improvement_proposal"]
        ),
        minimum_closed_trade_count=int(value["minimum_closed_trade_count"]),
        minimum_active_day_count=int(value["minimum_active_day_count"]),
        reviewed_outcome_ids=tuple(
            str(item) for item in value.get("reviewed_outcome_ids", ())
        ),
        metrics={
            str(key): int(metric) if metric is not None else None
            for key, metric in metrics.items()
        },
        reasons=tuple(str(item) for item in value.get("reasons", ())),
    )


def _feedback_improvement_from_document(
    value: Mapping[str, Any],
) -> FeedbackImprovementProposalRevision:
    scope = value.get("account_scope")
    baseline = value.get("baseline_parameters")
    proposed = value.get("proposed_parameters")
    allowed = value.get("allowed_parameter_values")
    if not all(isinstance(item, Mapping) for item in (scope, baseline, proposed, allowed)):
        raise ValueError("feedback improvement document is invalid")
    return FeedbackImprovementProposalRevision(
        proposal_id=str(value["proposal_id"]),
        version=str(value["version"]),
        policy_version=str(value["policy_version"]),
        review_id=str(value["review_id"]),
        evidence_id=str(value["evidence_id"]),
        account_scope=AccountScope(
            str(scope.get("broker") or ""),
            AccountEnvironment(str(scope.get("environment") or "")),
            str(scope.get("account_ref") or ""),
        ),
        strategy_ref=str(value["strategy_ref"]),
        derived_at=datetime.fromisoformat(str(value["derived_at"])),
        family_id=str(value["family_id"]),
        factor_allowlist=tuple(str(item) for item in value.get("factor_allowlist", ())),
        baseline_parameters=dict(baseline),
        proposed_parameters=dict(proposed),
        allowed_parameter_values={
            str(key): tuple(int(item) for item in values)
            for key, values in allowed.items()
        },
        changed_parameter=str(value["changed_parameter"]),
        changed_from=int(value["changed_from"]),
        changed_to=int(value["changed_to"]),
        rationale_code=str(value["rationale_code"]),
        status=str(value["status"]),
    )


def _normalize_feedback_improvement_policy(
    family_id: str,
    baseline: Mapping[str, Any],
    values: Mapping[str, tuple[int, ...]],
) -> dict[str, tuple[int, ...]]:
    definition = get_research_family(family_id)
    if not isinstance(values, Mapping) or not values:
        raise ValueError("feedback proposal requires allowed parameter values")
    if not set(values) <= definition.searchable_parameters:
        raise ValueError("feedback proposal contains an unregistered parameter")
    normalized: dict[str, tuple[int, ...]] = {}
    for key in sorted(values):
        candidates = values[key]
        if not isinstance(candidates, (list, tuple)) or not candidates:
            raise ValueError("feedback proposal allowed values must be nonempty arrays")
        unique = tuple(dict.fromkeys(candidates))
        if any(isinstance(item, bool) or not isinstance(item, int) for item in unique):
            raise ValueError("feedback proposal allowed values must be integers")
        for candidate_value in unique:
            candidate = dict(baseline)
            candidate[key] = candidate_value
            parsed = parse_strategy_config(family_id, candidate).to_dict()
            if parsed.get(key) != candidate_value:
                raise ValueError(
                    f"feedback proposal value cannot affect normalized parameter: {key}"
                )
        normalized[str(key)] = unique
    return normalized


def _feedback_selection_status(
    selected_runs: tuple[str, ...],
    selection_declared_at: datetime | None,
    evaluation_start: datetime,
) -> str:
    if not selected_runs:
        return "ALL_ACCOUNT_TRADES"
    if selection_declared_at is None:
        return "UNVERIFIED"
    return "PREDECLARED" if selection_declared_at <= evaluation_start else "POST_HOC"


def _feedback_point_in_time_status(
    links: tuple[JournalResearchLink, ...],
) -> str:
    if not links or any(link.evidence_timing == "unverified" for link in links):
        return "UNVERIFIED"
    if any(link.evidence_timing == "post_trade" for link in links):
        return "POST_TRADE_INCLUDED"
    return "AT_EXECUTION"


def _feedback_fill_in_interval(
    filled_at: datetime, start: datetime, end: datetime,
) -> bool:
    comparable = _feedback_aware_time(filled_at)
    return start <= comparable.astimezone(start.tzinfo) < end


def _feedback_aware_time(value: datetime) -> datetime:
    return value.replace(tzinfo=_KST) if value.tzinfo is None else value.astimezone(_KST)


def build_forward_report(
    spec: ForwardEvaluationSpec,
    evidence: ForwardEvidence,
    *,
    computed_at: datetime,
) -> ForwardEvaluationReport:
    """Evaluate frozen data, system and performance gates; never changes activation."""
    _require_aware(computed_at, "computed_at")
    if evidence.profile_id != spec.profile_id:
        raise ValueError("forward evidence belongs to a different profile")
    if evidence.as_of < spec.evaluation_start:
        raise ValueError("forward evidence predates the evaluation interval")
    if computed_at < evidence.as_of:
        raise ValueError("computed_at must not predate the evidence")
    finalized = evidence.finalized and evidence.as_of >= spec.evaluation_end
    criteria = spec.criteria
    definitions = (
        ("comparable_observations", "data", evidence.comparable_observation_count,
         criteria.minimum_comparable_observations, "minimum"),
        ("coverage_ppm", "data", evidence.coverage_ppm, criteria.minimum_coverage_ppm, "minimum"),
        ("p95_delay_ms", "data", evidence.p95_delay_ms, criteria.maximum_p95_delay_ms, "maximum"),
        ("gap_count", "data", evidence.gap_count, criteria.maximum_gap_count, "maximum"),
        ("submission_unknown_count", "system", evidence.submission_unknown_count,
         criteria.maximum_submission_unknown_count, "maximum"),
        ("rejected_order_count", "system", evidence.rejected_order_count,
         criteria.maximum_rejected_order_count, "maximum"),
        ("cancel_failure_count", "system", evidence.cancel_failure_count,
         criteria.maximum_cancel_failure_count, "maximum"),
        ("reconnect_count", "system", evidence.reconnect_count,
         criteria.maximum_reconnect_count, "maximum"),
        ("balance_mismatch_count", "system", evidence.balance_mismatch_count,
         criteria.maximum_balance_mismatch_count, "maximum"),
        ("active_day_count", "performance", evidence.active_day_count,
         criteria.minimum_active_day_count, "minimum"),
        ("closed_trade_count", "performance", evidence.closed_trade_count,
         criteria.minimum_closed_trade_count, "minimum"),
        ("adjusted_net_pnl_won", "performance", evidence.adjusted_net_pnl_won,
         criteria.minimum_adjusted_net_pnl_won, "minimum"),
        ("max_drawdown_ppm", "performance", evidence.max_drawdown_ppm,
         criteria.maximum_drawdown_ppm, "maximum"),
        ("exposure_ppm", "performance", evidence.exposure_ppm,
         criteria.maximum_exposure_ppm, "maximum"),
    )
    gates = tuple(_gate(*definition, finalized=finalized) for definition in definitions)
    statuses = {gate.status for gate in gates}
    if "BLOCKED" in statuses:
        status = ForwardReportStatus.BLOCKED
    elif "FAILED" in statuses:
        status = ForwardReportStatus.FAILED
    elif "PENDING" in statuses or not finalized:
        status = ForwardReportStatus.PENDING
    else:
        status = ForwardReportStatus.PASSED
    reasons = tuple(dict.fromkeys(
        gate.reason for gate in gates if gate.reason
    ))
    if not finalized and "evaluation_interval_not_finalized" not in reasons:
        reasons = (*reasons, "evaluation_interval_not_finalized")
    data_metrics = {
        "comparable_observation_count": evidence.comparable_observation_count,
        "coverage_ppm": evidence.coverage_ppm, "p95_delay_ms": evidence.p95_delay_ms,
        "gap_count": evidence.gap_count,
    }
    system_metrics = {
        "submission_unknown_count": evidence.submission_unknown_count,
        "rejected_order_count": evidence.rejected_order_count,
        "cancel_failure_count": evidence.cancel_failure_count,
        "reconnect_count": evidence.reconnect_count,
        "balance_mismatch_count": evidence.balance_mismatch_count,
        "no_trade_count": evidence.no_trade_count,
    }
    performance_metrics = {
        "active_day_count": evidence.active_day_count,
        "closed_trade_count": evidence.closed_trade_count,
        "broker_net_pnl_won": evidence.broker_net_pnl_won,
        "broker_reported_cost_won": evidence.broker_reported_cost_won,
        "additional_unmodeled_cost_won": evidence.additional_unmodeled_cost_won,
        "adjusted_net_pnl_won": evidence.adjusted_net_pnl_won,
        "max_drawdown_ppm": evidence.max_drawdown_ppm,
        "exposure_ppm": evidence.exposure_ppm,
    }
    limitations = (
        "mock_fill_probability_and_queue_position_do_not_equal_live_trading",
        "broker_reported_cost_is_already_in_broker_net_and_is_not_subtracted_twice",
        "passing_does_not_enable_orders_or_approve_live_trading",
    )
    promotion_eligible = status is ForwardReportStatus.PASSED
    identity = _report_identity(
        spec.profile_id, status, computed_at, evidence.as_of, finalized, reasons,
        promotion_eligible, data_metrics, system_metrics, performance_metrics, gates,
        limitations,
    )
    return ForwardEvaluationReport(
        report_id=_content_id("forward_report", identity), profile_id=spec.profile_id,
        status=status, computed_at=computed_at, evidence_as_of=evidence.as_of,
        finalized=finalized, promotion_eligible=promotion_eligible,
        reasons=reasons, data_metrics=data_metrics, system_metrics=system_metrics,
        performance_metrics=performance_metrics, gates=gates,
        limitations=limitations,
    )


def forward_report_from_dict(value: Mapping[str, Any]) -> ForwardEvaluationReport:
    gates = tuple(ForwardGateResult(**dict(row)) for row in value.get("gates", ()))
    report = ForwardEvaluationReport(
        report_id=str(value["report_id"]), profile_id=str(value["profile_id"]),
        status=ForwardReportStatus(str(value["status"])),
        computed_at=datetime.fromisoformat(str(value["computed_at"])),
        evidence_as_of=datetime.fromisoformat(str(value["evidence_as_of"])),
        finalized=bool(value["finalized"]), promotion_eligible=bool(value["promotion_eligible"]),
        reasons=tuple(str(item) for item in value.get("reasons", ())),
        data_metrics=dict(value.get("data_metrics", {})),
        system_metrics=dict(value.get("system_metrics", {})),
        performance_metrics=dict(value.get("performance_metrics", {})), gates=gates,
        limitations=tuple(str(item) for item in value.get("limitations", ())),
    )
    identity = _report_identity(
        report.profile_id, report.status, report.computed_at, report.evidence_as_of,
        report.finalized, report.reasons, report.promotion_eligible,
        report.data_metrics, report.system_metrics, report.performance_metrics,
        report.gates, report.limitations,
    )
    if _content_id("forward_report", identity) != report.report_id:
        raise ValueError("forward report content does not match report_id")
    return report


def forward_evidence_from_sources(
    *, profile_id: str, as_of: datetime, finalized: bool,
    validation_summary: Mapping[str, Any], execution_events: Iterable[Mapping[str, Any]],
    performance: Mapping[str, Any],
) -> ForwardEvidence:
    """Adapt V1 validation metrics and O1 event rows without mutating either ledger."""
    comparable = _nonnegative_int(validation_summary.get("comparable_count", 0), "comparable_count")
    missing = _nonnegative_int(validation_summary.get("unmatched_count", 0), "unmatched_count")
    denominator = comparable + missing
    coverage_ppm = comparable * 1_000_000 // denominator if denominator else None
    arrival = validation_summary.get("arrival_gap_ms", {})
    if not isinstance(arrival, Mapping):
        raise ValueError("validation arrival_gap_ms must be an object")
    p95_raw = arrival.get("p95")
    p95_delay_ms = None if p95_raw is None else max(0, round(float(p95_raw)))
    unique_events: dict[str, Mapping[str, Any]] = {}
    for sequence, event in enumerate(execution_events):
        event_id = str(event.get("event_id") or f"row-{sequence}")
        unique_events.setdefault(event_id, event)
    event_types = [str(event.get("event_type", "")) for event in unique_events.values()]
    return ForwardEvidence(
        profile_id=profile_id, as_of=as_of, finalized=finalized,
        comparable_observation_count=comparable, coverage_ppm=coverage_ppm,
        p95_delay_ms=p95_delay_ms, gap_count=missing,
        submission_unknown_count=event_types.count("SUBMISSION_RESPONSE_UNKNOWN"),
        rejected_order_count=sum(value in {"PRECHECK_REJECTED", "SUBMISSION_REJECTED"} for value in event_types),
        cancel_failure_count=sum(value in {"CANCEL_REJECTED", "CANCEL_RESPONSE_UNKNOWN"} for value in event_types),
        reconnect_count=event_types.count("BROKER_RECONNECTED"),
        balance_mismatch_count=event_types.count("ACCOUNT_MISMATCH"),
        no_trade_count=event_types.count("NO_TRADE"),
        active_day_count=_performance_int(performance, "active_day_count"),
        closed_trade_count=_performance_int(performance, "closed_trade_count"),
        broker_net_pnl_won=_optional_int(performance.get("broker_net_pnl_won"), "broker_net_pnl_won"),
        broker_reported_cost_won=_optional_int(
            performance.get("broker_reported_cost_won"), "broker_reported_cost_won",
        ),
        additional_unmodeled_cost_won=_optional_int(
            performance.get("additional_unmodeled_cost_won"), "additional_unmodeled_cost_won",
        ),
        max_drawdown_ppm=_optional_int(performance.get("max_drawdown_ppm"), "max_drawdown_ppm"),
        exposure_ppm=_optional_int(performance.get("exposure_ppm"), "exposure_ppm"),
    )


def _gate(
    name: str, section: str, observed: int | None, threshold: int | None,
    direction: str, *, finalized: bool,
) -> ForwardGateResult:
    if threshold is None:
        return ForwardGateResult(name, section, "BLOCKED", observed, None, direction, f"criterion_tbd:{name}")
    if observed is None:
        return ForwardGateResult(name, section, "FAILED" if finalized else "PENDING",
                                 None, threshold, direction, f"evidence_missing:{name}")
    passed = observed >= threshold if direction == "minimum" else observed <= threshold
    if passed:
        return ForwardGateResult(name, section, "PASSED", observed, threshold, direction, "")
    status = "FAILED" if finalized or direction == "maximum" else "PENDING"
    return ForwardGateResult(name, section, status, observed, threshold, direction,
                             f"criterion_not_met:{name}")


def _content_id(prefix: str, value: Mapping[str, Any]) -> str:
    encoded = _canonical_document(value).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()}"


def _canonical_document(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError("forward evaluation document must be canonical JSON data") from exc


def _report_identity(
    profile_id: str, status: ForwardReportStatus, computed_at: datetime,
    evidence_as_of: datetime, finalized: bool, reasons: tuple[str, ...],
    promotion_eligible: bool,
    data_metrics: Mapping[str, int | None], system_metrics: Mapping[str, int | None],
    performance_metrics: Mapping[str, int | None], gates: tuple[ForwardGateResult, ...],
    limitations: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "profile_id": profile_id, "status": status.value,
        "computed_at": computed_at.isoformat(), "evidence_as_of": evidence_as_of.isoformat(),
        "finalized": finalized, "reasons": list(reasons),
        "promotion_eligible": promotion_eligible,
        "data_metrics": dict(data_metrics), "system_metrics": dict(system_metrics),
        "performance_metrics": dict(performance_metrics),
        "gates": [gate.to_dict() for gate in gates],
        "limitations": list(limitations),
    }


def _nonnegative_int(value: object, name: str) -> int:
    result = int(value)
    if result < 0:
        raise ValueError(f"{name} must not be negative")
    return result


def _performance_int(value: Mapping[str, Any], name: str) -> int:
    if name not in value:
        raise ValueError(f"performance metric is missing: {name}")
    return _nonnegative_int(value[name], name)


def _optional_int(value: object, name: str) -> int | None:
    if value is None:
        return None
    result = int(value)
    if name != "broker_net_pnl_won" and result < 0:
        raise ValueError(f"{name} must not be negative")
    return result


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
