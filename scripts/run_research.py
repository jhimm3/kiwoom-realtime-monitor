"""고정 D2 export에 D3 Factor·전략·모의 실행을 기록한다."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Callable
from typing import Any, Mapping

from kiwoom_monitor.application.breakout_strategy import (
    STRATEGY_ID,
    STRATEGY_VERSION,
    BreakoutStrategyConfig,
    StrategyState,
    evaluate_breakout_bar,
    logical_evaluation_document,
)
from kiwoom_monitor.application.market_research_features import (
    MatureBreakoutSummary,
    market_frames_from_observations,
)
from kiwoom_monitor.application.market_session_schedule import (
    SUPPORTED_RESEARCH_SESSION_PROFILES,
    research_session_profile_document,
)
from kiwoom_monitor.application.research_factors import (
    FACTOR_VERSION,
    compute_market_regime,
)
from kiwoom_monitor.application.research_families import family_for_config
from kiwoom_monitor.application.research_evaluation import (
    ResearchComparison,
    build_research_comparison,
    build_research_report,
    build_outcome_labels,
    evaluate_performance,
)
from kiwoom_monitor.application.research_execution import (
    COST_MODEL_VERSION,
    EXECUTION_MODEL_VERSION,
    SAME_BAR_PATH_VERSION,
    PaperExecutionEngine,
    SimulationCostModel,
    SimulationExecutionConfig,
)
from kiwoom_monitor.application.research_replay import (
    ResearchReplayCursor,
    replay_candidate_universe,
    replay_krx_minute_bars,
)
from kiwoom_monitor.application.research_splits import ResearchEvaluationSpec, DevelopmentPartitionSpec, DEVELOPMENT_PARTITION_VERSION
from kiwoom_monitor.application.research_resources import ResearchResourceGuard, ResearchResourceLimits, ResearchResourceBlocked
from kiwoom_monitor.application.research_implementation import (
    LEGACY_RESEARCH_IMPLEMENTATION_HASH,
    research_implementation_hash,
)
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
from kiwoom_monitor.infrastructure.research_data_source import (
    FrozenResearchDataset,
    load_research_input,
    research_observation_order,
    research_input_encoded_bytes,
    prepare_development_partition, development_partition_start, final_holdout_partition_start,
    DEVELOPMENT_INPUT_VERSION, FINAL_INPUT_VERSION,
)


@dataclass(frozen=True)
class ResearchRunResult:
    run_id: str
    logical_result_hash: str
    decision_count: int
    candidate_count: int
    execution_event_count: int
    outcome_label_count: int
    performance_status: str
    report_status: str
    output_manifest: Path
    market_regime: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ResearchComparisonResult:
    comparison_id: str
    status: str
    baseline_run_id: str
    variant_run_id: str
    output_manifest: Path
    market_regime: Mapping[str, Any] | None = None


class ResearchRunCancelled(RuntimeError):
    pass


def execute_research(
    dataset: FrozenResearchDataset,
    repository: ResearchRepository,
    runs_dir: Path,
    config: Any,
    execution_config: SimulationExecutionConfig,
    evaluation_spec: ResearchEvaluationSpec | None = None,
    cancel_requested: Callable[[], bool] | None = None,
    session_profile: str | None = None,
    resource_guard: ResearchResourceGuard | None = None,
    *, execution_scope: str | None = None, expected_code_hash: str | None = None,
    final_execution: Mapping[str, str] | None = None,
) -> ResearchRunResult:
    def partition_checkpoint():
        if cancel_requested is not None and cancel_requested():
            raise ResearchRunCancelled('development partition validation interrupted')
        if resource_guard is not None:
            resource_guard.checkpoint()
    final_scope = execution_scope == 'independent_final_holdout/v1'
    active_start = (final_holdout_partition_start(dataset, evaluation_spec, checkpoint=partition_checkpoint)
                    if final_scope else development_partition_start(dataset, evaluation_spec, checkpoint=partition_checkpoint))
    target_partition = (DevelopmentPartitionSpec.from_dict(dataset.manifest['development_partition']['spec']).symbol_partition
                        if active_start is not None and not final_scope else None)
    if active_start is not None and (session_profile is None or
            research_session_profile_document(session_profile) != dataset.manifest.get('research_session_profile')):
        raise ValueError('independent partition requires a matching explicit session profile')
    bundle_replay = dataset.manifest.get("runtime_input_version") == "continuous_bundle_replay/v1"
    partition_replay = dataset.manifest.get('runtime_input_version') in (DEVELOPMENT_INPUT_VERSION, FINAL_INPUT_VERSION)
    historical_reconstruction = (
        dataset.manifest.get('runtime_input_version') == 'historical_reconstruction_strategy/v1'
        or dataset.manifest.get('source_runtime_input_version')
        == 'historical_reconstruction_strategy/v1'
    )
    if historical_reconstruction:
        if dataset.manifest.get('not_contemporaneous_top20') is not True:
            raise ValueError('historical reconstruction input is missing its population boundary')
        if config.rank_persistence_enabled:
            raise ValueError('historical reconstruction does not support the contemporaneous rank factor')
        if session_profile not in (None, 'krx-regular/v1'):
            raise ValueError('historical reconstruction currently supports krx-regular/v1 only')
    if bundle_replay and (session_profile is None or
            research_session_profile_document(session_profile) != dataset.manifest.get("research_session_profile")):
        raise ValueError("research bundle requires a matching explicit session profile")
    ordered_input = tuple(sorted(dataset.observations, key=research_observation_order)) if bundle_replay or partition_replay else ()
    def checkpoint():
        if cancel_requested is not None and cancel_requested():
            raise ResearchRunCancelled('research run cancellation requested')
        if resource_guard is not None:
            resource_guard.checkpoint()
    cursor = ResearchReplayCursor(
        ordered_input,
        session_profile=session_profile or 'krx-regular/v1',
        checkpoint=checkpoint,
        universe_kinds=(
            ("historical_candidate_population",)
            if historical_reconstruction else ("top20_membership",)
        ),
    ) if bundle_replay or partition_replay else None
    family = family_for_config(config)
    run_id, spec = research_run_identity(dataset, config, execution_config, evaluation_spec,
                                        session_profile=session_profile, execution_scope=execution_scope)
    if expected_code_hash is not None and spec['code_hash'] != expected_code_hash:
        raise ValueError('research implementation changed during locked validation')
    if final_scope:
        if not isinstance(final_execution, Mapping) or set(final_execution) != {'batch_id', 'candidate_spec_hash', 'owner_token'}:
            raise ValueError('final execution requires a repository-owned claim')
        repository.assert_final_holdout_execution_owner(run_id, batch_id=final_execution['batch_id'],
            candidate_spec_hash=final_execution['candidate_spec_hash'], owner_token=final_execution['owner_token'])
    elif final_execution is not None:
        raise ValueError('final execution ownership cannot be used outside final scope')
    repository.start_run(run_id, spec, dataset.manifest)
    logical_rows: list[dict[str, Any]] = []
    engine = PaperExecutionEngine(
        run_id, execution_config, config, session_profile=session_profile,
    )
    execution_events = []
    candidate_events = []
    decision_rows: list[dict[str, Any]] = []
    processed_execution_bars: set[tuple[str, str]] = set()
    candidate_count = 0
    decision_count = 0
    last_available_at: datetime | None = None
    try:
        if cancel_requested is not None and cancel_requested():
            raise ResearchRunCancelled("research run cancellation requested")
        for observation in _ordered_minute_observations(dataset):
            checkpoint()
            if cancel_requested is not None and cancel_requested():
                raise ResearchRunCancelled("research run cancellation requested")
            cutoff = _aware_datetime(observation.get("available_at"))
            cutoff_utc = cutoff.astimezone(timezone.utc)
            last_available_at = max(last_available_at, cutoff_utc) if last_available_at else cutoff_utc
            if cursor is not None:
                bars, universe = cursor.advance(research_observation_order(observation))
            else:
                bars = replay_krx_minute_bars(dataset.observations, as_of=cutoff, strict=True,
                                            session_profile=session_profile or "krx-regular/v1")
                universe = replay_candidate_universe(
                    dataset.observations, as_of=cutoff,
                    kinds=("historical_candidate_population",) if historical_reconstruction else ("top20_membership",),
                )
            revision_id = str(observation.get("revision_id", ""))
            evaluation_bar = next(
                (bar for bar in bars if bar.revision_id == revision_id), None,
            )
            if evaluation_bar is None:
                continue
            # Replay warmup history/universe, but keep the new portfolio empty until the active window.
            if active_start is not None and (cutoff_utc < active_start or _aware_datetime(evaluation_bar.bar_start).astimezone(timezone.utc) < active_start):
                continue
            # Keep full as-of TOP20/peer history for factors; only admitted targets reach the engine.
            if target_partition is not None and not target_partition.allows(evaluation_bar.code):
                continue
            latest_codes = universe[-1].codes if universe else ()
            execution_key = (evaluation_bar.code, evaluation_bar.observation_key)
            if execution_key not in processed_execution_bars:
                bar_events = engine.process_bar(evaluation_bar)
                repository.append_execution_events(bar_events)
                execution_events.extend(bar_events)
                logical_rows.extend({"execution": event.to_dict()} for event in bar_events)
                processed_execution_bars.add(execution_key)
            managed_symbol = (
                engine.strategy_state.status in {"candidate", "open"}
                and engine.strategy_state.symbol == evaluation_bar.code
            ) or (
                engine.portfolio.pending_order is not None
                and engine.portfolio.pending_order.symbol == evaluation_bar.code
            )
            if evaluation_bar.code not in latest_codes and not managed_symbol:
                continue
            evaluation = family.evaluate_bar(
                run_id=run_id,
                evaluation_bar=evaluation_bar,
                bar_history=bars,
                universe_frames=universe,
                config=config,
                state=engine.strategy_state,
            )
            repository.append_evaluation(evaluation)
            decision_rows.append(evaluation.decision.to_dict())
            decision_count += 1
            logical_rows.append({"strategy": logical_evaluation_document(evaluation)})
            decision_events = engine.process_decision(
                evaluation.decision, evaluation.state, source_bar=evaluation_bar,
            )
            repository.append_execution_events(decision_events)
            execution_events.extend(decision_events)
            logical_rows.extend({"execution": event.to_dict()} for event in decision_events)
            if evaluation.candidate_event is not None:
                candidate_count += 1
                candidate_events.append(evaluation.candidate_event)
        if cancel_requested is not None and cancel_requested():
            raise ResearchRunCancelled("research run cancellation requested")
        ended_at = last_available_at.isoformat() if last_available_at else str(
            dataset.manifest.get("end", "")
        )
        if partition_replay:
            partition_key = 'final_holdout_partition' if final_scope else 'development_partition'
            ended_at = dataset.manifest[partition_key]['end']
        if not ended_at:
            ended_at = "1970-01-01T00:00:00+00:00"
        final_events = engine.finalize(ended_at)
        repository.append_execution_events(final_events)
        execution_events.extend(final_events)
        logical_rows.extend({"execution": event.to_dict()} for event in final_events)
        final_bars = replay_krx_minute_bars(
            dataset.observations, strict=True,
            session_profile=session_profile or "krx-regular/v1",
        )
        checkpoint()
        outcomes = build_outcome_labels(
            candidate_events, final_bars, run_ended=True,
            session_profile=session_profile,
        )
        checkpoint()
        repository.append_outcome_labels(outcomes)
        logical_rows.extend({"outcome": label.to_dict()} for label in outcomes)
        market_regime = _final_market_regime(dataset, outcomes)
        checkpoint()
        if market_regime is not None:
            logical_rows.append({"market_regime": market_regime})
        performance = evaluate_performance(
            run_id, execution_events,
            initial_cash_won=execution_config.initial_cash_won,
            cost_model_present=execution_config.cost_model is not None,
        )
        repository.save_run_evaluation(performance)
        checkpoint()
        logical_rows.append({"performance": performance.to_dict()})
        core_logical_hash = hashlib.sha256(
            "\n".join(_canonical_json(row) for row in logical_rows).encode("utf-8"),
        ).hexdigest()
        report = None
        if evaluation_spec is not None:
            report = build_research_report(
                run_id,
                input_manifest=dataset.manifest,
                observations=dataset.observations,
                candidate_events=candidate_events,
                decisions=decision_rows,
                execution_events=execution_events,
                outcome_labels=outcomes,
                initial_cash_won=execution_config.initial_cash_won,
                cost_model=(
                    execution_config.cost_model.to_dict()
                    if execution_config.cost_model is not None else None
                ),
                spec=evaluation_spec,
                logical_result_hash=core_logical_hash,
            )
            repository.save_research_report(report)
            checkpoint()
            logical_rows.append({"research_report": report.to_dict()})
        logical_hash = hashlib.sha256(
            "\n".join(_canonical_json(row) for row in logical_rows).encode("utf-8"),
        ).hexdigest()
    except ResearchResourceBlocked:
        current = repository.load_run(run_id)
        if execution_scope is None and current is not None and current["status"] != "completed":
            repository.cancel_run(run_id)
        raise
    except ResearchRunCancelled:
        current = repository.load_run(run_id)
        if execution_scope is None and current is not None and current["status"] != "completed":
            repository.cancel_run(run_id)
        raise
    except Exception as exc:
        current = repository.load_run(run_id)
        if execution_scope is None and current is not None and current["status"] != "completed":
            repository.fail_run(run_id, f"{type(exc).__name__}: {exc}")
        raise

    output_manifest = Path(runs_dir) / run_id / "manifest.json"
    result_document = {
        "format": "kiwoom-monitor-research-run",
        "version": 3,
        "run_id": run_id,
        "input_dataset_id": dataset.manifest.get("dataset_id", ""),
        "input_revision_ids_hash": dataset.manifest.get("revision_ids_hash", ""),
        "spec": spec,
        "logical_result_hash": logical_hash,
        "decision_count": decision_count,
        "candidate_count": candidate_count,
        "execution_event_count": len(execution_events),
        "outcome_label_count": len(outcomes),
        "performance": performance.to_dict(),
        "research_report": report.to_dict() if report is not None else None,
        "market_regime": market_regime,
        "final_state": engine.strategy_state.to_dict(),
        "final_portfolio": asdict(engine.portfolio),
        "repository": repository.path.name,
    }
    if partition_replay:
        key = 'final_holdout_partition' if final_scope else 'development_partition'
        result_document[key] = dataset.manifest[key]
    _write_immutable_json(output_manifest, result_document)
    # The durable run is complete only after its immutable output manifest is published.
    repository.finish_run(run_id, logical_hash)
    return ResearchRunResult(
        run_id=run_id,
        logical_result_hash=logical_hash,
        decision_count=decision_count,
        candidate_count=candidate_count,
        execution_event_count=len(execution_events),
        outcome_label_count=len(outcomes),
        performance_status=performance.status,
        report_status=report.status if report is not None else "NOT_REQUESTED",
        output_manifest=output_manifest,
        market_regime=market_regime,
    )


def execute_rank_comparison(
    dataset: FrozenResearchDataset,
    repository: ResearchRepository,
    runs_dir: Path,
    variant_config: Any,
    execution_config: SimulationExecutionConfig,
    evaluation_spec: ResearchEvaluationSpec,
    cancel_requested: Callable[[], bool] | None = None,
    session_profile: str | None = None,
    resource_guard: ResearchResourceGuard | None = None,
) -> ResearchComparisonResult:
    """관심순위 Factor만 끈 동일 Family 기준선을 실행해 쌍으로 비교한다."""
    if not variant_config.rank_persistence_enabled:
        raise ValueError("rank comparison requires rank_persistence_enabled")
    baseline_config = replace(
        variant_config,
        rank_persistence_enabled=False,
        rank_persistence_required=False,
    )
    baseline = execute_research(
        dataset, repository, runs_dir, baseline_config, execution_config, evaluation_spec,
        cancel_requested,
        session_profile,
        resource_guard,
    )
    if cancel_requested is not None and cancel_requested():
        raise ResearchRunCancelled("research comparison cancellation requested")
    variant = execute_research(
        dataset, repository, runs_dir, variant_config, execution_config, evaluation_spec,
        cancel_requested,
        session_profile,
        resource_guard,
    )
    baseline_report = repository.load_research_report(baseline.run_id)
    variant_report = repository.load_research_report(variant.run_id)
    if baseline_report is None or variant_report is None:
        raise ValueError("paired research reports are missing")
    comparison: ResearchComparison = build_research_comparison(
        baseline_run_id=baseline.run_id,
        variant_run_id=variant.run_id,
        changed_condition="rank_persistence_filter",
        input_dataset_id=str(dataset.manifest.get("dataset_id", "")),
        input_revision_ids_hash=str(dataset.manifest.get("revision_ids_hash", "")),
        baseline_report=baseline_report,
        variant_report=variant_report,
        baseline_candidates=repository.load_candidate_events(baseline.run_id),
        variant_candidates=repository.load_candidate_events(variant.run_id),
        baseline_events=repository.load_execution_events(baseline.run_id),
        variant_events=repository.load_execution_events(variant.run_id),
        spec=evaluation_spec,
    )
    repository.save_research_comparison(comparison)
    output_manifest = Path(runs_dir) / "comparisons" / comparison.comparison_id / "manifest.json"
    _write_immutable_json(output_manifest, {
        "format": "kiwoom-monitor-research-comparison",
        "version": 1,
        "comparison": comparison.to_dict(),
        "baseline_manifest": str(baseline.output_manifest),
        "variant_manifest": str(variant.output_manifest),
        "repository": repository.path.name,
    })
    return ResearchComparisonResult(
        comparison.comparison_id, comparison.status, baseline.run_id, variant.run_id,
        output_manifest, variant.market_regime,
    )


def research_run_identity(dataset, config, execution_config, evaluation_spec, *, session_profile=None, execution_scope=None):
    """Shared scientific identity; default legacy run identity is unchanged."""
    if execution_scope not in (None, 'independent_development_validation/v1', 'independent_final_holdout/v1'):
        raise ValueError('unsupported research execution scope')
    expected_input = (FINAL_INPUT_VERSION if execution_scope == 'independent_final_holdout/v1'
                      else DEVELOPMENT_INPUT_VERSION if execution_scope is not None else None)
    if expected_input is not None and dataset.manifest.get('runtime_input_version') != expected_input:
        raise ValueError('independent execution requires its prepared input')
    spec = _run_spec(dataset.manifest, config, execution_config, evaluation_spec, session_profile=session_profile)
    if execution_scope is not None:
        spec['execution_scope'] = execution_scope
    return _content_id('run', {'dataset_id': dataset.manifest.get('dataset_id', ''),
                              'revision_ids_hash': dataset.manifest.get('revision_ids_hash', ''), 'spec': spec}), spec


def _run_spec(
    input_manifest: Mapping[str, Any],
    config: Any,
    execution_config: SimulationExecutionConfig,
    evaluation_spec: ResearchEvaluationSpec | None,
    *,
    session_profile: str | None = None,
) -> dict[str, Any]:
    family = family_for_config(config)
    factor_versions = {
        factor_id.split("/", 1)[0]: (
            config.rank_factor_version
            if factor_id.startswith("rank_persistence/") and config.rank_persistence_enabled
            else "disabled"
            if factor_id.startswith("rank_persistence/")
            else factor_id.split("/", 1)[1]
        )
        for factor_id in family.factor_ids
    }
    factor_versions["market_regime"] = FACTOR_VERSION
    historical_reconstruction = input_manifest.get('runtime_input_version') == 'historical_reconstruction_strategy/v1'
    document = {
        "mode": "historical_reconstruction" if historical_reconstruction else "historical_replay",
        "data_manifest_hash": hashlib.sha256(
            _canonical_json(input_manifest).encode("utf-8"),
        ).hexdigest(),
        "code_hash": research_implementation_hash(session_profile),
        "factor_versions": factor_versions,
        "family": family.family_id,
        "policy_version": "single_position_buy_then_sell/v1",
        "parameters": config.to_dict(),
        "initial_state": StrategyState().to_dict(),
        "seed": 0,
        "clock": "historical_bar_close" if historical_reconstruction else "observation_available_at",
        "ordering": "available_at,accepted_sequence,revision_id",
        "cost_model": (
            execution_config.cost_model.to_dict() if execution_config.cost_model else None
        ),
        "execution_model": execution_config.to_dict(),
    }
    if evaluation_spec is not None:
        document["evaluation_spec"] = evaluation_spec.to_dict()
    if session_profile is not None:
        document["session_profile"] = research_session_profile_document(session_profile)
    return document


def _final_market_regime(
    dataset: FrozenResearchDataset,
    outcomes: tuple[object, ...],
) -> dict[str, Any] | None:
    """run 종료 시점에 성숙해 있던 3분 결과만 시장 표본에 포함한다."""
    frames = market_frames_from_observations(
        dataset.observations, dataset.theme_snapshots,
    )
    if not frames:
        return None
    cutoff = _aware_datetime(frames[-1].available_at).astimezone(timezone.utc)
    completed = []
    pending_count = 0
    for label in outcomes:
        if int(getattr(label, "horizon_seconds", 0)) != 180:
            continue
        status = str(getattr(label, "status", ""))
        available_text = str(getattr(label, "available_at", ""))
        try:
            available = _aware_datetime(available_text).astimezone(timezone.utc)
        except ValueError:
            continue
        if available > cutoff:
            continue
        if status == "COMPLETE" and isinstance(getattr(label, "values", None), Mapping):
            completed.append(label)
        elif status == "PENDING":
            pending_count += 1
    success_count = sum(
        int(getattr(label, "values")["return_bps"]) > 0
        for label in completed
        if getattr(label, "values").get("return_bps") is not None
    )
    factor = compute_market_regime(
        frames,
        MatureBreakoutSummary(
            completed_count=len(completed), success_count=success_count,
            failed_count=len(completed) - success_count, pending_count=pending_count,
        ),
        as_of=cutoff,
    )
    return factor.to_dict()


def _ordered_minute_observations(
    dataset: FrozenResearchDataset,
) -> tuple[Mapping[str, Any], ...]:
    values = [
        value for value in dataset.observations
        if value.get("kind") == "minute_bar" and value.get("venue") == "KRX"
    ]
    return tuple(sorted(values, key=lambda value: (
        _aware_datetime(value.get("available_at")).astimezone(timezone.utc),
        int(value.get("accepted_sequence", 0)),
        str(value.get("revision_id", "")),
    )))


def _write_immutable_json(path: Path, document: Mapping[str, Any]) -> None:
    encoded = json.dumps(
        document, ensure_ascii=False, sort_keys=True, indent=2,
    ) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != encoded:
            raise ValueError("research run manifest already exists with different content")
        return
    path.parent.mkdir(parents=True)
    path.write_text(encoded, encoding="utf-8")


def _content_id(prefix: str, value: Mapping[str, Any]) -> str:
    return f"{prefix}_{hashlib.sha256(_canonical_json(value).encode('utf-8')).hexdigest()}"


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _aware_datetime(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"invalid research observation timestamp: {value}") from exc
    if parsed.tzinfo is None:
        raise ValueError("research observation timestamps must be timezone-aware")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(description="고정 export에 첫 KRX 돌파 전략을 실행합니다.")
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--runs-dir", required=True, type=Path)
    parser.add_argument("--session-profile", choices=SUPPORTED_RESEARCH_SESSION_PROFILES)
    parser.add_argument("--strategy-version", default=STRATEGY_VERSION)
    parser.add_argument("--rolling-factor-version", default=FACTOR_VERSION)
    parser.add_argument("--rank-factor-version", default=FACTOR_VERSION)
    parser.add_argument("--lookback-bars", required=True, type=int)
    parser.add_argument("--buffer-bps", required=True, type=int)
    parser.add_argument("--rank-enabled", action="store_true")
    parser.add_argument("--rank-required", action="store_true")
    parser.add_argument("--rank-top-k", required=True, type=int)
    parser.add_argument("--rank-window-seconds", required=True, type=int)
    parser.add_argument("--rank-max-gap-seconds", required=True, type=int)
    parser.add_argument("--rank-min-residency-seconds", required=True, type=int)
    parser.add_argument("--stop-loss-bps", required=True, type=int)
    parser.add_argument("--target-bps", required=True, type=int)
    parser.add_argument("--max-hold-minutes", required=True, type=int)
    parser.add_argument("--quantity", required=True, type=int)
    parser.add_argument("--capital-won", required=True, type=int)
    parser.add_argument("--signal-valid-seconds", required=True, type=int)
    parser.add_argument("--cooldown-seconds", required=True, type=int)
    parser.add_argument("--execution-model-version", default=EXECUTION_MODEL_VERSION)
    parser.add_argument("--same-bar-path-version", default=SAME_BAR_PATH_VERSION)
    parser.add_argument("--cost-model-version", default=COST_MODEL_VERSION)
    parser.add_argument("--initial-cash-won", required=True, type=int)
    parser.add_argument("--memory-mb", default=512, type=int)
    parser.add_argument("--cpu-duty-percent", default=50, type=int)
    parser.add_argument("--commission-bps", required=True, type=int)
    parser.add_argument("--sell-tax-bps", required=True, type=int)
    parser.add_argument("--slippage-bps", required=True, type=int)
    parser.add_argument("--cost-rate-basis", default="unspecified")
    parser.add_argument("--cost-source", default="")
    parser.add_argument("--cost-valid-from", default="")
    parser.add_argument("--cost-valid-to", default="")
    parser.add_argument(
        "--evaluation-spec", type=Path,
        help="D7 시간순 fold·warmup·gap·purge·적격성 JSON 파일",
    )
    parser.add_argument(
        "--compare-rank-baseline", action="store_true",
        help="같은 조건에서 관심순위 Factor를 끈 기준선과 쌍 비교",
    )
    parser.add_argument('--development-partition', default='', metavar='FOLD_NAME',
                        help='독립 개발 구간 하나 선택(TRAIN/VALIDATION만 허용, 최종 접근 금지)')
    args = parser.parse_args()
    config = BreakoutStrategyConfig(
        strategy_version=args.strategy_version,
        rolling_factor_version=args.rolling_factor_version,
        rank_factor_version=args.rank_factor_version,
        lookback_bars=args.lookback_bars,
        buffer_bps=args.buffer_bps,
        rank_persistence_enabled=args.rank_enabled,
        rank_persistence_required=args.rank_required,
        rank_top_k=args.rank_top_k,
        rank_window_seconds=args.rank_window_seconds,
        rank_max_gap_seconds=args.rank_max_gap_seconds,
        rank_min_residency_seconds=args.rank_min_residency_seconds,
        stop_loss_bps=args.stop_loss_bps,
        target_bps=args.target_bps,
        max_hold_minutes=args.max_hold_minutes,
        quantity=args.quantity,
        capital_won=args.capital_won,
        signal_valid_seconds=args.signal_valid_seconds,
        cooldown_seconds=args.cooldown_seconds,
    )
    resource_guard = ResearchResourceGuard(ResearchResourceLimits(args.memory_mb, args.cpu_duty_percent))
    resource_guard.preflight(research_input_encoded_bytes(args.dataset))
    dataset = load_research_input(args.dataset, session_profile=args.session_profile, checkpoint=resource_guard.checkpoint)
    execution = SimulationExecutionConfig(
        version=args.execution_model_version,
        same_bar_path_version=args.same_bar_path_version,
        initial_cash_won=args.initial_cash_won,
        cost_model=SimulationCostModel(
            version=args.cost_model_version,
            commission_bps=args.commission_bps,
            sell_tax_bps=args.sell_tax_bps,
            slippage_bps=args.slippage_bps,
            rate_basis=args.cost_rate_basis,
            source=args.cost_source,
            valid_from=args.cost_valid_from,
            valid_to=args.cost_valid_to,
        ),
    )
    evaluation = _load_evaluation_spec(args.evaluation_spec)
    if args.development_partition:
        if evaluation is None:
            raise ValueError('development partition requires --evaluation-spec')
        partition = DevelopmentPartitionSpec(DEVELOPMENT_PARTITION_VERSION, args.development_partition)
        dataset = prepare_development_partition(dataset, partition, evaluation, checkpoint=resource_guard.checkpoint)
        evaluation = partition.evaluation_for(evaluation)
    repository = ResearchRepository(args.database)
    if args.compare_rank_baseline:
        if evaluation is None:
            raise ValueError("rank baseline comparison requires --evaluation-spec")
        comparison = execute_rank_comparison(
            dataset, repository, args.runs_dir, config, execution, evaluation,
            session_profile=args.session_profile,
            resource_guard=resource_guard,
        )
        print(json.dumps({
            "status": "ok", "comparison_id": comparison.comparison_id,
            "comparison_status": comparison.status,
            "baseline_run_id": comparison.baseline_run_id,
            "variant_run_id": comparison.variant_run_id,
            "manifest": str(comparison.output_manifest),
        }, ensure_ascii=False))
        return 0
    result = execute_research(
        dataset, repository, args.runs_dir, config, execution, evaluation,
        session_profile=args.session_profile,
        resource_guard=resource_guard,
    )
    print(json.dumps({
        "status": "ok",
        "run_id": result.run_id,
        "logical_result_hash": result.logical_result_hash,
        "decision_count": result.decision_count,
        "candidate_count": result.candidate_count,
        "execution_event_count": result.execution_event_count,
        "outcome_label_count": result.outcome_label_count,
        "performance_status": result.performance_status,
        "report_status": result.report_status,
        "manifest": str(result.output_manifest),
    }, ensure_ascii=False))
    return 0


def _load_evaluation_spec(path: Path | None) -> ResearchEvaluationSpec | None:
    if path is None:
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("evaluation spec cannot be read") from exc
    if not isinstance(document, Mapping):
        raise ValueError("evaluation spec must be a JSON object")
    return ResearchEvaluationSpec.from_dict(document)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ResearchResourceBlocked as exc:
        print(json.dumps({'status': 'resource_blocked', 'reason': str(exc)}))
        sys.exit(3)
