"""등록된 연구 계약 안에서만 동작하는 결정론적 제한 탐색."""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import random
import time
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Callable, Iterable, Mapping

from kiwoom_monitor.application.research_families import get_research_family
from kiwoom_monitor.application.research_evaluation import build_development_evidence


SEARCH_VERSION = "limited_search/v2"
HARD_CONSTRAINT_FIELDS = frozenset({
    "strategy_version", "rolling_factor_version", "rank_factor_version",
    "rank_persistence_enabled", "rank_persistence_required", "quantity", "capital_won",
})
TERMINAL_STATUSES = frozenset({"COMPLETED", "FAILED", "INELIGIBLE"})


class TrialAttemptInterrupted(RuntimeError):
    """실험 결과가 아니라 재시도 가능한 실행 중단을 나타낸다."""


@dataclass(frozen=True)
class ExperimentSpec:
    version: str
    hypothesis_refs: tuple[str, ...]
    dataset_id: str
    dataset_hash: str
    family_allowlist: tuple[str, ...]
    factor_allowlist: tuple[str, ...]
    parameter_space: Mapping[str, tuple[int, ...]]
    objective: Mapping[str, str]
    constraints: Mapping[str, int]
    split_version: str
    max_trials: int
    max_seconds: int
    seed: int
    generation_mode: str = "constrained_auto"
    execution_environment: str = "historical_simulation"
    stopping_rule: str = "budget_exhausted"
    include_no_trade_baseline: bool = True
    cost_stress_multipliers_ppm: tuple[int, ...] = ()
    ablations: tuple[str, ...] = ()
    resource_budget: Mapping[str, int] = field(
        default_factory=lambda: {"max_concurrent_trials": 1},
    )
    final_holdout_accessed_at: str = ""
    final_holdout_access_reason: str = ""
    research_context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.version != SEARCH_VERSION:
            raise ValueError(f"unregistered research search: {self.version}")
        if not self.research_context:
            raise ValueError("v2 search requires the complete research context")
        try:
            json.dumps(self.research_context, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise ValueError("research context must be canonical JSON data") from exc
        if not self.dataset_id.strip() or not self.dataset_hash.strip():
            raise ValueError("search dataset id and hash are required")
        if len(self.family_allowlist) != 1:
            raise ValueError("one finite experiment must select exactly one registered family")
        family = get_research_family(self.family_allowlist[0])
        if not self.factor_allowlist or not set(self.factor_allowlist) <= set(family.factor_ids):
            raise ValueError("search factor allowlist contains an unregistered factor")
        keys = set(self.parameter_space)
        if keys & HARD_CONSTRAINT_FIELDS:
            raise ValueError("hard strategy constraints cannot be searched or relaxed")
        if not keys <= family.searchable_parameters:
            raise ValueError("parameter space contains an unregistered parameter")
        if any(not values for values in self.parameter_space.values()):
            raise ValueError("each search parameter requires at least one value")
        if (
            self.max_trials < 0 or self.max_trials > 1_000 or self.max_seconds < 0
            or (self.max_trials > 0 and self.max_seconds == 0)
        ):
            raise ValueError("search budget is outside the supported boundary")
        if self.stopping_rule != "budget_exhausted":
            raise ValueError("unregistered stopping rule")
        if self.generation_mode not in {
            "manual", "constrained_auto", "free_research", "one_parameter_at_a_time",
        }:
            raise ValueError("unregistered research generation mode")
        if self.generation_mode == "manual" and any(
            len(tuple(dict.fromkeys(values))) != 1
            for values in self.parameter_space.values()
        ):
            raise ValueError("manual research requires one explicit value per parameter")
        if self.execution_environment != "historical_simulation":
            raise ValueError("v2 limited search supports historical_simulation only")
        if any(value < 1_000_000 for value in self.cost_stress_multipliers_ppm):
            raise ValueError("cost stress multiplier must be at least the locked base cost")
        if not set(self.ablations) <= {"rank_persistence"}:
            raise ValueError("search contains an unregistered ablation")
        if not self.objective:
            raise ValueError("at least one explicit objective is required")
        if set(self.objective.values()) - {"maximize", "minimize"}:
            raise ValueError("objective direction must be maximize or minimize")
        if int(self.resource_budget.get("max_concurrent_trials", 0)) != 1:
            raise ValueError("limited search supports exactly one concurrent trial")
        cpu_duty_percent = int(self.resource_budget.get("cpu_duty_percent", 100))
        if not 10 <= cpu_duty_percent <= 100:
            raise ValueError("research cpu duty percent must be between 10 and 100")
        if int(self.resource_budget.get("max_generated_candidates", 1_000)) <= 0:
            raise ValueError("max_generated_candidates must be positive")
        if int(self.resource_budget.get("max_retained_jobs", 100)) <= 0:
            raise ValueError("max_retained_jobs must be positive")
        if not 128 <= int(self.resource_budget.get("memory_mb", 512)) <= 4_096:
            raise ValueError("research memory budget must be between 128MB and 4096MB")
        if not set(self.constraints) <= {
            "minimum_closed_trades", "minimum_active_days", "maximum_drawdown_won",
        }:
            raise ValueError("search contains an unregistered selection constraint")
        if bool(self.final_holdout_accessed_at) != bool(self.final_holdout_access_reason.strip()):
            raise ValueError("holdout access timestamp and reason must be recorded together")

    def to_dict(self) -> dict[str, Any]:
        document = asdict(self)
        document["parameter_space"] = {
            key: list(self.parameter_space[key]) for key in sorted(self.parameter_space)
        }
        return document

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ExperimentSpec":
        space = value.get("parameter_space")
        if not isinstance(space, Mapping):
            raise ValueError("parameter_space must be an object")
        return cls(
            version=str(value.get("version", "")),
            hypothesis_refs=tuple(str(item) for item in value.get("hypothesis_refs", ())),
            dataset_id=str(value.get("dataset_id", "")),
            dataset_hash=str(value.get("dataset_hash", "")),
            family_allowlist=tuple(str(item) for item in value.get("family_allowlist", ())),
            factor_allowlist=tuple(str(item) for item in value.get("factor_allowlist", ())),
            parameter_space={
                str(key): tuple(int(item) for item in values)
                for key, values in space.items()
            },
            objective={str(key): str(direction) for key, direction in _mapping(value.get("objective"), "objective").items()},
            constraints={str(key): int(limit) for key, limit in _mapping(value.get("constraints", {}), "constraints").items()},
            split_version=str(value.get("split_version", "")),
            max_trials=int(value.get("max_trials", 0)),
            max_seconds=int(value.get("max_seconds", 0)),
            seed=int(value.get("seed", 0)),
            generation_mode=str(value.get("generation_mode", "constrained_auto")),
            execution_environment=str(value.get(
                "execution_environment", "historical_simulation",
            )),
            stopping_rule=str(value.get("stopping_rule", "budget_exhausted")),
            include_no_trade_baseline=bool(value.get("include_no_trade_baseline", True)),
            cost_stress_multipliers_ppm=tuple(
                int(item) for item in value.get("cost_stress_multipliers_ppm", ())
            ),
            ablations=tuple(str(item) for item in value.get("ablations", ())),
            resource_budget={
                str(key): int(item) for key, item in _mapping(
                    value.get("resource_budget", {"max_concurrent_trials": 1}),
                    "resource_budget",
                ).items()
            },
            final_holdout_accessed_at=str(value.get("final_holdout_accessed_at", "")),
            final_holdout_access_reason=str(value.get("final_holdout_access_reason", "")),
            research_context=_mapping(value.get("research_context", {}), "research_context"),
        )

    @property
    def experiment_id(self) -> str:
        return _content_id("experiment", self.evidence_dict())

    def evidence_dict(self) -> dict[str, Any]:
        """운영 자원 예산과 분리된 재사용 가능한 과학적 실험 명세."""
        document = self.to_dict()
        for key in ("max_trials", "max_seconds", "resource_budget"):
            document.pop(key, None)
        return document


@dataclass(frozen=True)
class SearchTrial:
    trial_id: str
    experiment_id: str
    ordinal: int
    parameters: Mapping[str, int]
    variant: str = "parameter_grid"
    cost_multiplier_ppm: int = 1_000_000
    ablation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TrialOutcome:
    status: str
    run_id: str = ""
    report_status: str = ""
    net_pnl_won: int | None = None
    max_drawdown_won: int | None = None
    closed_trade_count: int = 0
    active_day_count: int = 0
    reasons: tuple[str, ...] = ()
    error: str = ""

    def __post_init__(self) -> None:
        if self.status not in TERMINAL_STATUSES:
            raise ValueError("trial outcome must be terminal")


@dataclass(frozen=True)
class CandidateCard:
    card_id: str
    experiment_id: str
    trial_id: str
    ordinal: int
    status: str
    run_id: str
    variant: str
    parameters: Mapping[str, int]
    objective: Mapping[str, str]
    selection_criteria: Mapping[str, int]
    net_pnl_won: int | None
    max_drawdown_won: int | None
    closed_trade_count: int
    active_day_count: int
    reasons: tuple[str, ...]
    holdout_accessed_at: str
    holdout_access_reason: str
    total_search_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def generate_trials(spec: ExperimentSpec) -> tuple[SearchTrial, ...]:
    """작은 grid를 고정 seed로 섞어 동일 spec에 같은 후보 목록을 만든다."""
    keys = tuple(sorted(spec.parameter_space))
    distinct_values = {
        key: tuple(dict.fromkeys(spec.parameter_space[key])) for key in keys
    }
    combination_count = (
        sum(len(distinct_values[key]) for key in keys)
        if spec.generation_mode == "one_parameter_at_a_time"
        else math.prod(len(distinct_values[key]) for key in keys)
        if keys else 1
    )
    fixed_count = 1 + int(spec.include_no_trade_baseline) + len(spec.ablations) + len(
        spec.cost_stress_multipliers_ppm
    )
    variable_count = combination_count if keys else 0
    if variable_count + fixed_count > int(
        spec.resource_budget.get("max_generated_candidates", 1_000)
    ):
        raise ValueError("candidate plan exceeds max_generated_candidates")
    if spec.generation_mode == "one_parameter_at_a_time":
        combinations = [
            {key: value}
            for key in keys
            for value in distinct_values[key]
        ]
    else:
        combinations = [dict(zip(keys, values, strict=True)) for values in itertools.product(
            *(distinct_values[key] for key in keys)
        )] if keys else [{}]
    random.Random(spec.seed).shuffle(combinations)
    plans: list[tuple[str, dict[str, int], int, str]] = [("baseline", {}, 1_000_000, "")]
    if spec.include_no_trade_baseline:
        plans.append(("no_trade_baseline", {}, 1_000_000, ""))
    plans.extend(("ablation", {}, 1_000_000, name) for name in spec.ablations)
    plans.extend(
        ("cost_stress", {}, multiplier, "")
        for multiplier in spec.cost_stress_multipliers_ppm
    )
    parameter_variant = (
        "single_parameter" if spec.generation_mode == "one_parameter_at_a_time"
        else "parameter_grid"
    )
    plans.extend((parameter_variant, parameters, 1_000_000, "") for parameters in combinations if parameters)
    trials = []
    for ordinal, (variant, parameters, cost_multiplier, ablation) in enumerate(plans):
        identity = {
            "experiment_id": spec.experiment_id, "ordinal": ordinal,
            "parameters": parameters, "variant": variant,
            "cost_multiplier_ppm": cost_multiplier, "ablation": ablation,
        }
        trials.append(SearchTrial(
            trial_id=_content_id("trial", identity), experiment_id=spec.experiment_id,
            ordinal=ordinal, parameters=parameters,
            variant=variant, cost_multiplier_ppm=cost_multiplier, ablation=ablation,
        ))
    return tuple(trials)


def build_candidate_card(
    spec: ExperimentSpec, trial: SearchTrial, outcome: TrialOutcome, total_search_count: int,
) -> CandidateCard:
    body = {
        "experiment_id": spec.experiment_id, "trial_id": trial.trial_id,
        "status": outcome.status, "run_id": outcome.run_id,
    }
    return CandidateCard(
        card_id=_content_id("candidate_card", body), experiment_id=spec.experiment_id,
        trial_id=trial.trial_id, ordinal=trial.ordinal, status=outcome.status,
        run_id=outcome.run_id, variant=trial.variant, parameters=dict(trial.parameters),
        objective=dict(spec.objective), selection_criteria=dict(spec.constraints),
        net_pnl_won=outcome.net_pnl_won, max_drawdown_won=outcome.max_drawdown_won,
        closed_trade_count=outcome.closed_trade_count,
        active_day_count=outcome.active_day_count,
        reasons=tuple(outcome.reasons) + ((outcome.error,) if outcome.error else ()),
        holdout_accessed_at=spec.final_holdout_accessed_at,
        holdout_access_reason=spec.final_holdout_access_reason,
        total_search_count=total_search_count,
    )


def run_limited_search(
    spec: ExperimentSpec,
    *,
    existing_trials: Iterable[Mapping[str, Any]],
    evaluate: Callable[[SearchTrial], TrialOutcome],
    record: Callable[[SearchTrial, TrialOutcome, CandidateCard], None],
    cancel_requested: Callable[[], bool] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    batch_cpu_governed: bool = False,
) -> tuple[CandidateCard, ...]:
    """종료된 trial을 건너뛰며 실패·부적격도 과학적 예산에 포함한다."""
    prior = {str(row.get("trial_id", "")): row for row in existing_trials}

    def prior_status(row: Mapping[str, Any]) -> str:
        outcome = row.get("outcome")
        if isinstance(outcome, Mapping):
            return str(outcome.get("status", ""))
        return str(row.get("status", ""))

    attempted = sum(prior_status(row) in TERMINAL_STATUSES for row in prior.values())
    deadline = monotonic() + spec.max_seconds if spec.max_seconds else None
    cards: list[CandidateCard] = []
    for trial in generate_trials(spec):
        previous = prior.get(trial.trial_id)
        if previous is not None and prior_status(previous) in TERMINAL_STATUSES:
            continue
        if attempted >= spec.max_trials or (deadline is not None and monotonic() >= deadline):
            break
        if cancel_requested is not None and cancel_requested():
            break
        trial_started = monotonic()
        try:
            outcome = evaluate(trial)
        except TrialAttemptInterrupted:
            raise
        except Exception as exc:
            outcome = TrialOutcome(status="FAILED", error=f"{type(exc).__name__}: {exc}")
        attempted += 1
        card = build_candidate_card(spec, trial, outcome, attempted)
        record(trial, outcome, card)
        cards.append(card)
        cpu_duty_percent = int(spec.resource_budget.get("cpu_duty_percent", 100))
        if cpu_duty_percent < 100 and not batch_cpu_governed:
            active_seconds = max(0.0, monotonic() - trial_started)
            pause_seconds = active_seconds * (100 - cpu_duty_percent) / cpu_duty_percent
            if deadline is not None:
                pause_seconds = min(pause_seconds, max(0.0, deadline - monotonic()))
            if pause_seconds > 0:
                sleeper(pause_seconds)
    return tuple(cards)


def outcome_from_report(run_id: str, report: Mapping[str, Any]) -> TrialOutcome:
    evidence = build_development_evidence(report)
    return TrialOutcome(
        status='COMPLETED' if evidence.status == 'ELIGIBLE' else 'INELIGIBLE',
        run_id=run_id, report_status=evidence.status,
        net_pnl_won=evidence.net_pnl_won, max_drawdown_won=evidence.max_drawdown_won,
        closed_trade_count=evidence.closed_trade_count, active_day_count=evidence.active_day_count,
        reasons=evidence.reasons,
    )


def apply_selection_constraints(
    outcome: TrialOutcome, constraints: Mapping[str, int],
) -> TrialOutcome:
    if outcome.status != "COMPLETED":
        return outcome
    reasons = []
    if outcome.closed_trade_count < int(constraints.get("minimum_closed_trades", 0)):
        reasons.append("search_minimum_closed_trades_not_met")
    if outcome.active_day_count < int(constraints.get("minimum_active_days", 0)):
        reasons.append("search_minimum_active_days_not_met")
    maximum_drawdown = constraints.get("maximum_drawdown_won")
    if maximum_drawdown is not None and (
        outcome.max_drawdown_won is None
        or outcome.max_drawdown_won > int(maximum_drawdown)
    ):
        reasons.append("search_maximum_drawdown_exceeded")
    return replace(
        outcome,
        status="INELIGIBLE" if reasons else outcome.status,
        reasons=tuple(outcome.reasons) + tuple(reasons),
    )


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _content_id(prefix: str, value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{prefix}_{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"
