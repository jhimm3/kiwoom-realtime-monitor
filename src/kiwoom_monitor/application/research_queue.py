"""유한 ExperimentSpec의 지속 연구 작업 식별과 알림 판정."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Iterable, Mapping

from kiwoom_monitor.application.research_families import get_research_family
from kiwoom_monitor.application.research_hypotheses import ResearchHypothesis
from kiwoom_monitor.application.research_search import ExperimentSpec


@dataclass(frozen=True)
class ResearchJobIdentity:
    job_id: str
    experiment_id: str
    dataset_id: str
    dataset_hash: str


@dataclass(frozen=True)
class ResearchCampaignPolicy:
    """Persistent scheduling limits; final evaluation remains explicitly manual."""
    max_active_jobs: int = 100
    max_attempts: int = 3
    retry_initial_seconds: int = 30
    retry_max_seconds: int = 900
    auto_final_evaluation: bool = False
    auto_hypotheses: bool = False
    hypothesis_seed: int = 0
    max_hypotheses: int = 1_000
    max_generated_hypotheses_per_cycle: int = 8
    hypothesis_parameter_values: Mapping[str, Mapping[str, tuple[int, ...]]] = field(
        default_factory=dict
    )

    def __post_init__(self):
        if type(self.auto_final_evaluation) is not bool or type(self.auto_hypotheses) is not bool:
            raise ValueError('campaign automation flags must be boolean')
        if not 1 <= self.max_active_jobs <= 10000 or not 1 <= self.max_attempts <= 100:
            raise ValueError('campaign active/attempt limit is invalid')
        if not 1 <= self.retry_initial_seconds <= self.retry_max_seconds <= 86400:
            raise ValueError('campaign retry backoff is invalid')
        if self.auto_final_evaluation:
            raise ValueError('campaign automatic final evaluation requires a later research stage')
        if isinstance(self.hypothesis_seed, bool) or not isinstance(self.hypothesis_seed, int):
            raise ValueError('campaign hypothesis seed must be an integer')
        if not 1 <= self.max_hypotheses <= 10_000:
            raise ValueError('campaign hypothesis limit must be between 1 and 10000')
        if not 1 <= self.max_generated_hypotheses_per_cycle <= 1_000:
            raise ValueError('campaign hypothesis cycle limit must be between 1 and 1000')
        if not isinstance(self.hypothesis_parameter_values, Mapping):
            raise ValueError('campaign hypothesis parameter policy must be an object')
        normalized: dict[str, dict[str, tuple[int, ...]]] = {}
        for family_id, parameters in self.hypothesis_parameter_values.items():
            definition = get_research_family(str(family_id))
            if not isinstance(parameters, Mapping) or not parameters:
                raise ValueError('each campaign hypothesis family requires parameter values')
            if not set(parameters) <= definition.searchable_parameters:
                raise ValueError('campaign hypothesis policy contains an unregistered parameter')
            normalized[str(family_id)] = {}
            for parameter, values in parameters.items():
                if not isinstance(values, (list, tuple)) or not values:
                    raise ValueError('campaign hypothesis allowed values must be nonempty arrays')
                unique = tuple(dict.fromkeys(values))
                if any(isinstance(value, bool) or not isinstance(value, int) for value in unique):
                    raise ValueError('campaign hypothesis allowed values must be integers')
                normalized[str(family_id)][str(parameter)] = unique
        object.__setattr__(self, 'hypothesis_parameter_values', normalized)

    def to_dict(self):
        return {
            'version': 'research_campaign/v1',
            'max_active_jobs': self.max_active_jobs,
            'max_attempts': self.max_attempts,
            'retry_initial_seconds': self.retry_initial_seconds,
            'retry_max_seconds': self.retry_max_seconds,
            'auto_final_evaluation': self.auto_final_evaluation,
            'auto_hypotheses': self.auto_hypotheses,
            'hypothesis_seed': self.hypothesis_seed,
            'max_hypotheses': self.max_hypotheses,
            'max_generated_hypotheses_per_cycle': self.max_generated_hypotheses_per_cycle,
            'hypothesis_parameter_values': {
                family_id: {key: list(values) for key, values in parameters.items()}
                for family_id, parameters in self.hypothesis_parameter_values.items()
            },
        }

    def retry_seconds(self, attempt: int) -> int:
        return min(self.retry_max_seconds, self.retry_initial_seconds * 2 ** min(max(attempt - 1, 0), 30))


def build_research_job_identity(spec: ExperimentSpec) -> ResearchJobIdentity:
    job_id = "research_job_" + hashlib.sha256(
        spec.experiment_id.encode("utf-8")
    ).hexdigest()
    return ResearchJobIdentity(
        job_id=job_id, experiment_id=spec.experiment_id,
        dataset_id=spec.dataset_id, dataset_hash=spec.dataset_hash,
    )


def build_hypothesis_experiment_spec(
    template: ExperimentSpec,
    hypothesis: ResearchHypothesis,
) -> ExperimentSpec:
    """Turn one READY lineage node into one exact existing-search experiment."""
    if not isinstance(template, ExperimentSpec) or not isinstance(hypothesis, ResearchHypothesis):
        raise ValueError('typed experiment template and research hypothesis are required')
    if hypothesis.status != 'READY':
        raise ValueError('only a READY research hypothesis can be scheduled')
    if template.final_holdout_accessed_at or template.final_holdout_access_reason:
        raise ValueError('automatic hypotheses cannot use a final holdout template')
    if template.max_trials < 2:
        raise ValueError('automatic hypothesis template requires budget for baseline and no-trade trials')
    if int(template.resource_budget.get('max_generated_candidates', 1_000)) < 2:
        raise ValueError('automatic hypothesis template candidate budget is too small')
    source_family = get_research_family(template.family_allowlist[0])
    target_family = get_research_family(hypothesis.family_id)
    if tuple(source_family.contract.get('required_inputs', ())) != tuple(
        target_family.contract.get('required_inputs', ())
    ):
        raise ValueError('hypothesis family requires a different frozen input contract')
    context = dict(template.research_context)
    context['family'] = hypothesis.family_id
    context['baseline_strategy'] = dict(hypothesis.parameters)
    return ExperimentSpec(
        version=template.version,
        hypothesis_refs=(hypothesis.hypothesis_id,),
        dataset_id=template.dataset_id,
        dataset_hash=template.dataset_hash,
        family_allowlist=(hypothesis.family_id,),
        factor_allowlist=hypothesis.factor_allowlist,
        parameter_space={},
        objective=dict(template.objective),
        constraints=dict(template.constraints),
        split_version=template.split_version,
        max_trials=template.max_trials,
        max_seconds=template.max_seconds,
        seed=template.seed,
        generation_mode='manual',
        execution_environment=template.execution_environment,
        stopping_rule=template.stopping_rule,
        include_no_trade_baseline=True,
        cost_stress_multipliers_ppm=(),
        ablations=(),
        resource_budget=dict(template.resource_budget),
        final_holdout_accessed_at='',
        final_holdout_access_reason='',
        research_context=context,
    )


def campaign_worker_retry_delay_ms(worker: Mapping[str, Any], now: datetime | None = None) -> int | None:
    if worker['state'] == 'NEEDS_ATTENTION':
        return None
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        raise ValueError('worker retry clock must be timezone-aware')
    target = worker['lease_expires_at'] if worker['state'] in ('STARTING', 'RUNNING') else worker['next_retry_at']
    delay = (datetime.fromisoformat(target) - moment).total_seconds() if target else 0
    return max(1000, min(2_147_483_647, int(delay * 1000) + 100))


def should_notify_research_result(
    *,
    terminal_status: str,
    previous_cards: Iterable[Mapping[str, Any]],
    current_cards: Iterable[Mapping[str, Any]],
) -> bool:
    """완료·실패 또는 원래 지표가 달라진 후보가 있을 때만 알림을 제안한다."""
    if terminal_status == "failed":
        return True
    if terminal_status != "completed":
        return False
    previous = {_card_signature(card) for card in previous_cards}
    current = {_card_signature(card) for card in current_cards}
    return previous != current


def _card_signature(card: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        str(card.get("trial_id", "")), str(card.get("status", "")),
        card.get("net_pnl_won"), card.get("max_drawdown_won"),
        int(card.get("closed_trade_count", 0)), int(card.get("active_day_count", 0)),
        tuple(str(reason) for reason in card.get("reasons", ())),
    )
