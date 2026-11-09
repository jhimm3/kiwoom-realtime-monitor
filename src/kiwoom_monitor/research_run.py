"""Reusable frozen research run workflow and immutable result publication."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Callable
from typing import Any, Mapping

from kiwoom_monitor.application.breakout_strategy import StrategyState, logical_evaluation_document
from kiwoom_monitor.application.market_research_features import MatureBreakoutSummary, market_frames_from_observations
from kiwoom_monitor.application.market_session_schedule import (
    KRX_REGULAR_RESEARCH_PROFILE, research_session_profile_document, research_session_profile_contract_matches,
)
from kiwoom_monitor.application.research_factors import FACTOR_VERSION, compute_market_regime
from kiwoom_monitor.application.research_families import family_for_config
from kiwoom_monitor.application.leader_breakout import (
    LeaderBreakoutConfig, leader_research_rows, evaluate_leader_breakout_bar_step,
)
from kiwoom_monitor.application.research_evaluation import (
    ResearchComparison, build_research_comparison, build_research_report,
    build_outcome_labels, evaluate_performance,
)
from kiwoom_monitor.application.research_execution import PaperExecutionEngine, SimulationExecutionConfig
from kiwoom_monitor.application.research_replay import (
    ResearchReplayCursor, replay_candidate_universe, replay_krx_minute_bars,
)
from kiwoom_monitor.application.research_splits import ResearchEvaluationSpec, DevelopmentPartitionSpec
from kiwoom_monitor.application.research_resources import ResearchResourceGuard, ResearchResourceBlocked
from kiwoom_monitor.application.research_implementation import (
    LEGACY_RESEARCH_IMPLEMENTATION_HASH, research_implementation_hash,
)
from kiwoom_monitor.application.section12_minute_research import (
    BREAKOUT_STUDY_FAMILY, CLOSE_FAMILY, IPO_BREAKOUT_FAMILY, IPO_CLOSE_FAMILY, MinuteExperiment, MinuteExperimentRun,
)
from kiwoom_monitor.domain.simulation_contract import COMPLETED_CLOSE_EXECUTION_VERSION
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
from kiwoom_monitor.infrastructure.research_data_source import (
    FrozenResearchDataset, FrozenStrategyReferences, FrozenReconstructedMarketProxy, research_observation_order,
    development_partition_start, final_holdout_partition_start,
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
    position_target_plan: tuple | None = None,
    unfilled_action_plan: tuple | None = None,
    position_action_study=None, position_study_case=None,position_study_cohort=None,position_study_context=None,
    strategy_references: FrozenStrategyReferences | None = None,
    shadow_only: bool = False,
    reconstructed_market_proxy: FrozenReconstructedMarketProxy | None = None,
) -> ResearchRunResult:
    leader = isinstance(config, LeaderBreakoutConfig)
    section12 = isinstance(config, MinuteExperiment)
    final_scope = execution_scope == 'independent_final_holdout/v1'
    from kiwoom_monitor.domain.research_contract import MINUTE_FINAL_CANDIDATE_FAMILIES
    final_minute = final_scope and section12 and config.family in MINUTE_FINAL_CANDIDATE_FAMILIES
    historical_reconstruction = _is_historical_reconstruction(dataset.manifest)
    historical_breakout = (section12 and config.family == BREAKOUT_STUDY_FAMILY and historical_reconstruction
                           and session_profile == KRX_REGULAR_RESEARCH_PROFILE
                           and research_session_profile_contract_matches(dataset.manifest.get("research_session_profile"), session_profile))
    if shadow_only and not section12:
        raise ValueError("shadow replay requires a Section 12 minute experiment")
    if section12 and config.panic_allow_unresolved_news and (shadow_only or execution_scope is not None and not final_minute):
        raise ValueError("unresolved PANIC news is supported only in Paper research")
    if section12 and config.panic_previous_session_news is not None and (shadow_only or execution_scope is not None):
        raise ValueError("PANIC prior-session news is supported only in ordinary Paper comparisons")
    if section12 != (execution_config.version == COMPLETED_CLOSE_EXECUTION_VERSION):
        raise ValueError("completed-close execution and a Section 12 minute experiment must be selected together")
    if section12 and (session_profile != KRX_REGULAR_RESEARCH_PROFILE or (strategy_references is None and not historical_breakout)
                      or position_target_plan is not None or unfilled_action_plan is not None or position_action_study is not None):
        raise ValueError("minute experiments require pinned references and their own KRX regular execution")
    if reconstructed_market_proxy is not None:
        if (not historical_breakout or shadow_only or execution_scope is not None
                or config.policy.variant not in {"B2", "B3"}
                or dataset.manifest.get("runtime_input_version") != DEVELOPMENT_INPUT_VERSION
                or dataset.manifest.get("dataset_id") != reconstructed_market_proxy.dataset_id
                or config.policy.entry_filters.market_scope != reconstructed_market_proxy.scope
                or config.policy.entry_filters.minimum_market_relative_turnover_bps
                != reconstructed_market_proxy.comparison_threshold_bps
                or any(proof.kind == "market_relative_turnover" for proof in config.breakout_entry_proofs)):
            raise ValueError("reconstructed market proxy requires its own B2/B3 development proof source")
    experiment = MinuteExperimentRun(
        config, strategy_references, reconstructed_market_values=(
            reconstructed_market_proxy.values if reconstructed_market_proxy is not None else None
        ),
        research_context=not shadow_only and (execution_scope is None or final_minute),
    ) if section12 else None
    if strategy_references is not None and (
        session_profile is None or (execution_scope is not None and not final_minute) or leader
    ):
        raise ValueError("pinned strategy references require ordered minute research or a final CLOSE candidate")
    calendar_evidence = strategy_references.calendar if strategy_references is not None else None
    if leader:
        missing = config.execution_missing("1m")
        if missing:
            raise ValueError("leader performance blocked:" + ",".join(missing))
        if position_target_plan or unfilled_action_plan or position_action_study:
            raise ValueError("leader ADD/REDUCE/unfilled study policy is unconfigured")
    def partition_checkpoint():
        if cancel_requested is not None and cancel_requested():
            raise ResearchRunCancelled('development partition validation interrupted')
        if resource_guard is not None:
            resource_guard.checkpoint()
    active_start = (final_holdout_partition_start(dataset, evaluation_spec, checkpoint=partition_checkpoint)
                    if final_scope else development_partition_start(dataset, evaluation_spec, checkpoint=partition_checkpoint))
    target_partition = (DevelopmentPartitionSpec.from_dict(dataset.manifest['development_partition']['spec']).symbol_partition
                        if active_start is not None and not final_scope else None)
    if active_start is not None and (session_profile is None or
            not research_session_profile_contract_matches(dataset.manifest.get('research_session_profile'), session_profile)):
        raise ValueError('independent partition requires a matching explicit session profile')
    bundle_replay = dataset.manifest.get("runtime_input_version") == "continuous_bundle_replay/v1"
    partition_replay = dataset.manifest.get('runtime_input_version') in (DEVELOPMENT_INPUT_VERSION, FINAL_INPUT_VERSION)
    if historical_reconstruction:
        if dataset.manifest.get('not_contemporaneous_top20') is not True:
            raise ValueError('historical reconstruction input is missing its population boundary')
        if not leader and config.rank_persistence_enabled:
            raise ValueError('historical reconstruction does not support the contemporaneous rank factor')
        if session_profile not in (None, 'krx-regular/v1'):
            raise ValueError('historical reconstruction currently supports krx-regular/v1 only')
    if bundle_replay and (session_profile is None or
            not research_session_profile_contract_matches(dataset.manifest.get("research_session_profile"), session_profile)):
        raise ValueError("research bundle requires a matching explicit session profile")
    # Current profiles consume the recorded ingest prefix even for a one-day export.
    # Keep the unprofiled historical comparison and its fixed identity unchanged.
    ordered_replay = not leader and (bundle_replay or partition_replay or session_profile is not None)
    def checkpoint():
        if cancel_requested is not None and cancel_requested():
            raise ResearchRunCancelled('research run cancellation requested')
        if resource_guard is not None:
            resource_guard.checkpoint()
    preparation_error = None
    def preparation_checkpoint(*, only_if_due=False):
        nonlocal preparation_error
        try:
            if not only_if_due or (
                resource_guard is not None and resource_guard.checkpoint_due()
            ):
                checkpoint()
        except Exception as exc:
            preparation_error = exc
            raise
    def checked_observation_order(value):
        preparation_checkpoint(only_if_due=True)
        # Data errors must still propagate before run registration.
        return research_observation_order(value)
    cursor = None
    if ordered_replay:
        try:
            preparation_checkpoint()
            ordered_input = tuple(sorted(dataset.observations, key=checked_observation_order))
            preparation_checkpoint()
            cursor = ResearchReplayCursor(
                ordered_input,
                session_profile=session_profile or 'krx-regular/v1',
                checkpoint=checkpoint,
                calendar_evidence=calendar_evidence,
                universe_kinds=(
                    ("historical_candidate_population",)
                    if historical_reconstruction else ("top20_membership",)
                ),
            )
        except Exception as exc:
            if exc is not preparation_error:
                raise
    leader_rows = leader_research_rows(dataset.observations, session_profile=session_profile,
        checkpoint=checkpoint) if leader else ()
    leader_frames = replay_krx_minute_bars(leader_rows, strict=False, session_profile=session_profile) if leader else ()
    leader_frames_by_revision = {bar.revision_id: bar for bar in leader_frames}
    leader_minute_states = {}
    family = family_for_config(config) if not section12 else None
    run_id, spec = research_run_identity(dataset, config, execution_config, evaluation_spec,
                                        session_profile=session_profile, execution_scope=execution_scope,
                                        position_target_plan=position_target_plan,unfilled_action_plan=unfilled_action_plan,
                                        position_action_study=position_action_study,position_study_case=position_study_case,
                                        position_study_cohort=position_study_cohort,position_study_context=position_study_context,
                                        strategy_references=strategy_references, shadow_only=shadow_only,
                                        reconstructed_market_proxy=reconstructed_market_proxy)
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
    logical_digest = hashlib.sha256()
    logical_row_count = 0

    def append_logical(row: Mapping[str, Any]) -> None:
        nonlocal logical_row_count
        if logical_row_count:
            logical_digest.update(b"\n")
        logical_digest.update(_canonical_json(row).encode("utf-8"))
        logical_row_count += 1
    engine = PaperExecutionEngine(
        run_id, execution_config, config, session_profile=session_profile,
    )
    execution_events = []
    candidate_events = []
    decision_rows: list[dict[str, Any]] = []
    processed_execution_bars: set[tuple[str, str]] = set()
    candidate_count = 0
    leader_position_context = None
    decision_count = 0
    last_available_at: datetime | None = None
    pending_evaluations = []
    if position_study_case is not None and position_study_case.branch_action != 'HOLD':
        position_target_plan=(position_study_case.request,)
    study_key=((position_study_case.request.decision_time.astimezone(timezone.utc),position_study_case.request.symbol)
               if position_study_case is not None else None)
    used_study=False
    target_plan = {(request.decision_time.astimezone(timezone.utc),request.symbol):request
                   for request in position_target_plan or ()}
    used_targets = set()
    unfilled_plan={(request.decision_time.astimezone(timezone.utc),request.symbol):request
                   for request in unfilled_action_plan or ()}
    used_unfilled=set()

    def flush_evaluations() -> None:
        if pending_evaluations:
            repository.append_evaluations(tuple(pending_evaluations))
            pending_evaluations.clear()

    try:
        if preparation_error is not None:
            raise preparation_error
        if cancel_requested is not None and cancel_requested():
            raise ResearchRunCancelled("research run cancellation requested")
        for observation in (leader_rows if leader else _ordered_minute_observations(dataset)):
            checkpoint()
            if cancel_requested is not None and cancel_requested():
                raise ResearchRunCancelled("research run cancellation requested")
            cutoff = _aware_datetime(observation.get("available_at"))
            cutoff_utc = cutoff.astimezone(timezone.utc)
            last_available_at = max(last_available_at, cutoff_utc) if last_available_at else cutoff_utc
            if leader:
                evaluation_bar = leader_frames_by_revision.get(observation["revision_id"])
                bars = (evaluation_bar,) if evaluation_bar is not None else ()
                universe = ()
            elif cursor is not None:
                evaluation_bar, bars, universe = cursor.advance_for_observation(observation)
            else:
                bars = replay_krx_minute_bars(dataset.observations, as_of=cutoff, strict=True,
                                            session_profile=session_profile or "krx-regular/v1")
                universe = replay_candidate_universe(
                    dataset.observations, as_of=cutoff, chronological=False,
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
                if leader:
                    _, leader_minute_states[evaluation_bar.code] = evaluate_leader_breakout_bar_step(
                        run_id=run_id, evaluation_bar=evaluation_bar, config=config, state=StrategyState(),
                        minute_state=leader_minute_states.get(evaluation_bar.code), original_observation=observation)
                continue
            # Keep the full as-of universe, with same-code/day bar history for each admitted target.
            if target_partition is not None and not target_partition.allows(evaluation_bar.code):
                continue
            latest_codes = universe[-1].codes if universe else ()
            if (position_action_study is not None and position_action_study.version=='position-action-study/v4'
                    and evaluation_bar.research_session!=spec['native_study_episode_session']):
                continue  # Other sessions supply frozen warmup inputs, never prior account trades.
            execution_key = (evaluation_bar.code, evaluation_bar.observation_key)
            just_closed = False
            if execution_key not in processed_execution_bars:
                lineage=((engine.observe_unfilled_bar(evaluation_bar),) if unfilled_action_plan is not None else ())
                bar_events = () if shadow_only else lineage + engine.process_bar(evaluation_bar)
                if ((position_target_plan is not None or unfilled_action_plan is not None or position_study_case is not None)
                        and not any(e.event_type=='MARK' for e in bar_events)
                        and evaluation_bar.market_phase=='CONTINUOUS'
                        and (engine.portfolio.position is None or engine.portfolio.position.symbol==evaluation_bar.code)):
                    bar_events += (engine.observe_position_value(evaluation_bar),)
                if bar_events:
                    flush_evaluations()
                repository.append_execution_events(bar_events)
                execution_events.extend(bar_events)
                for event in bar_events:
                    append_logical({"execution": event.to_dict()})
                processed_execution_bars.add(execution_key)
                if experiment is not None:
                    just_closed = experiment.observe_fills(bar_events, engine.portfolio.position)
            managed_symbol = (
                engine.strategy_state.status in {"candidate", "open"}
                and engine.strategy_state.symbol == evaluation_bar.code
            ) or (
                engine.portfolio.pending_order is not None
                and engine.portfolio.pending_order.symbol == evaluation_bar.code
            )
            listing_population = experiment is not None and config.family in {IPO_CLOSE_FAMILY, IPO_BREAKOUT_FAMILY}
            if not leader and not listing_population and evaluation_bar.code not in latest_codes and not managed_symbol and not just_closed and (cutoff_utc,evaluation_bar.code) not in unfilled_plan:
                continue
            if leader:
                evaluation, leader_minute_states[evaluation_bar.code] = evaluate_leader_breakout_bar_step(
                    run_id=run_id, evaluation_bar=evaluation_bar, config=config, state=engine.strategy_state,
                    minute_state=leader_minute_states.get(evaluation_bar.code),
                    position_context=leader_position_context, original_observation=observation)
            elif experiment is not None:
                evaluation = experiment.evaluate(
                    run_id=run_id, bar=evaluation_bar, bars=bars, cursor=cursor,
                    universe_ref=universe[-1].revision_id if universe else "", engine=engine,
                    just_closed=just_closed,
                )
            else:
                evaluation = family.evaluate_bar(
                run_id=run_id,
                evaluation_bar=evaluation_bar,
                bar_history=bars,
                universe_frames=universe,
                config=config,
                state=engine.strategy_state,
                )
            if leader and evaluation.decision.final_action == "ENTER":
                from kiwoom_monitor.application.leader_breakout import LeaderBreakoutContext
                leader_position_context = LeaderBreakoutContext(**evaluation.snapshot.feature_values[0]["context"])
            pending_evaluations.append(evaluation)
            # The report only counts NO_TRADE reasons by fold. Keep that bounded
            # projection, while the complete immutable decision is in the repository.
            decision_rows.append({
                "decided_at": evaluation.decision.decided_at,
                "final_action": evaluation.decision.final_action,
                "reasons": evaluation.decision.reasons,
            })
            decision_count += 1
            append_logical({"strategy": logical_evaluation_document(evaluation)})
            target_key=(cutoff_utc,evaluation_bar.code)
            if study_key == target_key:
                if used_study:raise ValueError('native study case cut was observed twice')
                proof=engine.observe_position_study_case(spec['position_action_study']['study_policy_ref'],
                    position_action_study,position_study_case,source_bar=evaluation_bar,
                    historical_context=spec.get('position_study_context'))
                repository.append_execution_events((proof,));execution_events.append(proof)
                append_logical({'execution':proof.to_dict()});used_study=True
            target_request=target_plan.get(target_key)
            unfilled_request=unfilled_plan.get(target_key)
            if shadow_only:
                decision_events = ()
            elif engine.native_study_active:
                decision_events=engine.process_native_family(evaluation.decision,evaluation.state,source_bar=evaluation_bar,
                    bar_history=bars,universe_frames=universe)
                if target_request is not None:used_targets.add(target_key)
            elif study_key == target_key and position_study_case.branch_action == 'HOLD':
                # An independent HOLD branch observes the same goal/pre-state;
                # it submits no target and follows the registered family.
                decision_events=engine.process_decision(evaluation.decision,evaluation.state,source_bar=evaluation_bar)
            elif unfilled_request is not None and evaluation.decision.final_action != 'EXIT':
                decision_events=engine.process_unfilled_action(unfilled_request,source_bar=evaluation_bar)
                used_unfilled.add(target_key)
            elif target_request is not None and evaluation.decision.final_action != 'EXIT':
                decision_events=engine.process_position_target(target_request,source_bar=evaluation_bar)
                used_targets.add(target_key)
            elif (unfilled_action_plan is not None and evaluation.decision.final_action=='ENTER'
                    and engine.portfolio.pending_order is not None
                    and engine.portfolio.pending_order.limit_execution_version):
                decision_events=()
            else:
                decision_events = engine.process_decision(
                    evaluation.decision, evaluation.state, source_bar=evaluation_bar,
                    minimum_fill_exclusive=experiment.entry_failure_price(evaluation.decision) if experiment is not None else None,
                )
            if decision_events or evaluation.candidate_event is not None or len(pending_evaluations) >= 128:
                flush_evaluations()
            repository.append_execution_events(decision_events)
            execution_events.extend(decision_events)
            for event in decision_events:
                append_logical({"execution": event.to_dict()})
            if evaluation.candidate_event is not None:
                candidate_count += 1
                candidate_events.append(evaluation.candidate_event)
        if position_study_case is not None and not used_study:
            raise ValueError('native study case cut was not observed')
        if set(target_plan)!=used_targets:
            raise ValueError('position target plan has missing, duplicate, or EXIT-preempted source cuts')
        if set(unfilled_plan)!=used_unfilled:
            raise ValueError('unfilled action plan has missing or EXIT-preempted source cuts')
        if cancel_requested is not None and cancel_requested():
            raise ResearchRunCancelled("research run cancellation requested")
        flush_evaluations()
        ended_at = last_available_at.isoformat() if last_available_at else str(
            dataset.manifest.get("end", "")
        )
        if partition_replay:
            partition_key = 'final_holdout_partition' if final_scope else 'development_partition'
            ended_at = dataset.manifest[partition_key]['end']
        if not ended_at:
            ended_at = "1970-01-01T00:00:00+00:00"
        final_events = () if shadow_only else engine.finalize(ended_at)
        repository.append_execution_events(final_events)
        execution_events.extend(final_events)
        for event in final_events:
            append_logical({"execution": event.to_dict()})
        final_bars = leader_frames if leader else replay_krx_minute_bars(
            dataset.observations, strict=True,
            session_profile=session_profile or "krx-regular/v1",
            calendar_evidence=calendar_evidence,
        )
        checkpoint()
        outcomes = build_outcome_labels(
            candidate_events, final_bars, run_ended=True,
            session_profile=session_profile,
        )
        checkpoint()
        repository.append_outcome_labels(outcomes)
        for label in outcomes:
            append_logical({"outcome": label.to_dict()})
        market_regime = _final_market_regime(dataset, outcomes, chronological=session_profile is not None)
        checkpoint()
        if market_regime is not None:
            append_logical({"market_regime": market_regime})
        performance = evaluate_performance(
            run_id, execution_events,
            initial_cash_won=execution_config.initial_cash_won,
            cost_model_present=execution_config.cost_model is not None,
        )
        repository.save_run_evaluation(performance)
        checkpoint()
        append_logical({"performance": performance.to_dict()})
        core_logical_hash = logical_digest.copy().hexdigest()
        report = None
        if evaluation_spec is not None:
            report = build_research_report(
                run_id,
                input_manifest=dataset.manifest,
                # Quality/reproducibility belongs to the frozen source manifest.
                # Leader closes are a judgment projection, not its source rows.
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
                evaluated_final_holdout=final_scope,
            )
            repository.save_research_report(report)
            checkpoint()
            append_logical({"research_report": report.to_dict()})
        logical_hash = logical_digest.hexdigest()
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
    return _save_research_pair(dataset, repository, runs_dir, baseline, variant, evaluation_spec,
                               changed_condition="rank_persistence_filter")


def execute_breakout_filter_comparison(
    dataset, repository, runs_dir, configs, execution_config, evaluation_spec,
    cancel_requested, session_profile, resource_guard, *, strategy_references,
    reconstructed_market_proxy: FrozenReconstructedMarketProxy | None = None,
) -> tuple[tuple[ResearchRunResult, ...], tuple[ResearchComparisonResult, ...]]:
    """Compare each added entry filter against its immediate predecessor."""
    if (len(evaluation_spec.folds) != 1 or evaluation_spec.folds[0].role == "OOS"
            or dataset.manifest.get("oos_included") is True or dataset.manifest.get("runtime_input_version") == FINAL_INPUT_VERSION):
        raise ValueError("breakout comparison requires one isolated development fold")
    if tuple(config.policy.variant for config in configs) != ("B0", "B1", "B2", "B3"):
        raise ValueError("breakout comparison requires the complete B0–B3 chain")
    if not dataset.manifest.get("development_partition"):
        fold = evaluation_spec.folds[0]
        start, end = _aware_datetime(fold.start), _aware_datetime(fold.end)
        for index, row in enumerate(dataset.observations):
            if index % 128 == 0:
                if cancel_requested is not None and cancel_requested():
                    raise ResearchRunCancelled("breakout comparison input validation cancelled")
                if resource_guard is not None:
                    resource_guard.checkpoint()
            if row.get("kind") == "minute_bar" and row.get("venue") == "KRX" and (
                _aware_datetime(row["payload"]["bar_start"]) < start or _aware_datetime(row["available_at"]) > end
            ):
                raise ValueError("breakout comparison input contains minute observations outside its development fold")
    runs = tuple(execute_research(
        dataset, repository, runs_dir, config, execution_config, evaluation_spec,
        cancel_requested, session_profile, resource_guard, strategy_references=strategy_references,
        reconstructed_market_proxy=reconstructed_market_proxy if config.policy.variant in {"B2", "B3"} else None,
    ) for config in configs)
    conditions = ("breakout_price_turnover_filter", "breakout_market_turnover_filter", "breakout_daily_context_filter")
    comparisons = tuple(_save_research_pair(
        dataset, repository, runs_dir, baseline, variant, evaluation_spec,
        changed_condition=condition, match_signal_revision=True,
    ) for baseline, variant, condition in zip(runs, runs[1:], conditions))
    return runs, comparisons


def _save_research_pair(
    dataset, repository, runs_dir, baseline, variant, evaluation_spec, *, changed_condition,
    match_signal_revision=False,
) -> ResearchComparisonResult:
    def candidates(run_id):
        events = repository.load_candidate_events(run_id)
        if not match_signal_revision:
            return events
        return tuple({**event, "dedup_key": _content_id("breakout_signal", {
            "symbol": event["symbol"], "reference_revision_id": event["reference_revision_id"],
        })} for event in events)

    baseline_report = repository.load_research_report(baseline.run_id)
    variant_report = repository.load_research_report(variant.run_id)
    if baseline_report is None or variant_report is None:
        raise ValueError("paired research reports are missing")
    comparison: ResearchComparison = build_research_comparison(
        baseline_run_id=baseline.run_id,
        variant_run_id=variant.run_id,
        changed_condition=changed_condition,
        input_dataset_id=str(dataset.manifest.get("dataset_id", "")),
        input_revision_ids_hash=str(dataset.manifest.get("revision_ids_hash", "")),
        baseline_report=baseline_report,
        variant_report=variant_report,
        baseline_candidates=candidates(baseline.run_id),
        variant_candidates=candidates(variant.run_id),
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


def research_run_identity(dataset, config, execution_config, evaluation_spec, *, session_profile=None, execution_scope=None,
                          position_target_plan=None, unfilled_action_plan=None,
                          position_action_study=None,position_study_case=None,position_study_cohort=None,position_study_context=None,
                          strategy_references: FrozenStrategyReferences | None = None, shadow_only: bool = False,
                          reconstructed_market_proxy: FrozenReconstructedMarketProxy | None = None):
    """Shared scientific identity; default legacy run identity is unchanged."""
    if execution_scope not in (None, 'independent_development_validation/v1', 'independent_final_holdout/v1'):
        raise ValueError('unsupported research execution scope')
    expected_input = (FINAL_INPUT_VERSION if execution_scope == 'independent_final_holdout/v1'
                      else DEVELOPMENT_INPUT_VERSION if execution_scope is not None else None)
    if expected_input is not None and dataset.manifest.get('runtime_input_version') != expected_input:
        raise ValueError('independent execution requires its prepared input')
    spec = _run_spec(dataset.manifest, config, execution_config, evaluation_spec, session_profile=session_profile)
    if strategy_references is not None:
        spec['strategy_references'] = {'version': 'strategy-reference/v1', 'sha256': strategy_references.sha256}
    if reconstructed_market_proxy is not None:
        spec["reconstructed_market_proxy"] = reconstructed_market_proxy.identity()
    if shadow_only:
        spec['observation_mode'] = 'shadow-replay/v1'
    if execution_scope is not None:
        spec['execution_scope'] = execution_scope
    if position_action_study is not None or position_study_case is not None:
        from kiwoom_monitor.application.position_target import (position_action_study_spec,
            PositionActionStudyPolicy,PositionActionStudyCase)
        descriptor=dataset.manifest.get('development_partition',{})
        role=DevelopmentPartitionSpec.from_dict(descriptor['spec']).evaluation_for(
            ResearchEvaluationSpec.from_dict(descriptor['evaluation'])).folds[0].role if descriptor else None
        if (execution_scope != 'independent_development_validation/v1' or session_profile != 'krx-regular/v1'
                or position_target_plan is not None or unfilled_action_plan is not None
                or not isinstance(position_action_study,PositionActionStudyPolicy)
                or not isinstance(position_study_case,PositionActionStudyCase)
                or role != position_action_study.development_role):
            raise ValueError('position study requires its own independent native branch')
        if position_action_study.version=='position-action-study/v4':
            inputs=json.loads(json.dumps(list(dataset.observations),ensure_ascii=False,allow_nan=False))
            encoded=_canonical_json(inputs)
            if len(encoded.encode())>256*1024:raise ValueError('native study frozen inputs exceed 256 KiB')
            spec['native_study_inputs']=inputs
            spec['native_study_inputs_ref']='native-study-inputs:'+hashlib.sha256(encoded.encode()).hexdigest()
            from zoneinfo import ZoneInfo
            spec['native_study_episode_session']=position_study_case.request.decision_time.astimezone(
                ZoneInfo('Asia/Seoul')).date().isoformat()+':KRX_REGULAR'
        spec['position_action_study']=position_action_study_spec(position_action_study,position_study_case,spec)
        if position_study_context is not None:
            from kiwoom_monitor.application.research_evaluation import validate_position_study_historical_context
            validate_position_study_historical_context(position_study_context,dataset.manifest,position_study_case.request,dataset=dataset)
            spec['position_study_context']=json.loads(json.dumps(position_study_context,ensure_ascii=False,allow_nan=False))
        if position_study_cohort is not None:
            from kiwoom_monitor.application.position_target import validate_position_action_study_cohort
            spec['position_action_study_cohort']=validate_position_action_study_cohort(position_study_cohort,
                position_action_study,position_study_case,spec,dataset.manifest)
        if position_study_case.branch_action != 'HOLD':position_target_plan=(position_study_case.request,)
    elif position_study_cohort is not None or position_study_context is not None:
        raise ValueError('native cohort requires an explicit v2 study case')
    if unfilled_action_plan is not None:
        from kiwoom_monitor.application.research_execution import UnfilledResearchAction,UNFILLED_EXECUTION_VERSION
        if (execution_scope!='independent_development_validation/v1' or session_profile!='krx-regular/v1'
                or position_target_plan is not None or not isinstance(unfilled_action_plan,tuple)
                or not 2<=len(unfilled_action_plan)<=200
                or any(not isinstance(r,UnfilledResearchAction) for r in unfilled_action_plan)):
            raise ValueError('unfilled plan requires bounded independent KRX development instructions')
        if (len({(r.decision_time,r.symbol) for r in unfilled_action_plan})!=len(unfilled_action_plan)
                or len({r.symbol for r in unfilled_action_plan})!=1
                or tuple(sorted(unfilled_action_plan,key=lambda r:r.decision_time))!=unfilled_action_plan):
            raise ValueError('unfilled plan requires ordered unique single-symbol source cuts')
        serialized=json.dumps([r.to_dict() for r in unfilled_action_plan],ensure_ascii=False)
        if len(serialized.encode('utf-8'))>256*1024:
            raise ValueError('unfilled plan exceeds 256 KiB')
        spec['unfilled_action_execution']={'version':UNFILLED_EXECUTION_VERSION,
            'fill_basis':'subsequent_minute_open_full_quantity_with_volume/v1',
            'cancel_latency_seconds':0,'partial_fills_supported':False}
        spec['unfilled_action_plan']=json.loads(serialized)
    if position_target_plan is not None:
        from kiwoom_monitor.application.position_target import PositionTargetShadowRequest, _json_value
        from kiwoom_monitor.application.research_execution import POSITION_TARGET_EXECUTION_VERSION
        if (execution_scope!='independent_development_validation/v1' or session_profile!='krx-regular/v1'
                or not isinstance(position_target_plan,tuple) or not 1<=len(position_target_plan)<=200):
            raise ValueError('position target plan requires an explicit bounded independent KRX development run')
        if any(not isinstance(r,PositionTargetShadowRequest) or r.target_policy is None
               or r.account_ref!='research-single-position' or r.cost_model!=execution_config.cost_model for r in position_target_plan):
            raise ValueError('position target plan must use native paper account, costs and automatic policy')
        first=position_target_plan[0]
        if (len({(r.decision_time,r.symbol) for r in position_target_plan})!=len(position_target_plan)
                or any(r.target_policy!=first.target_policy or r.policy_version!=first.policy_version for r in position_target_plan)):
            raise ValueError('position target plan requires unique source cuts and one unchanged policy')
        spec['position_action_execution']={'version':POSITION_TARGET_EXECUTION_VERSION,
            'source_policy_version':first.policy_version,
            'target_policy':json.loads(json.dumps(asdict(first.target_policy),default=_json_value))}
        serialized=json.dumps([asdict(r) for r in position_target_plan],default=_json_value,ensure_ascii=False)
        if len(serialized.encode('utf-8'))>256*1024:
            raise ValueError('position target plan exceeds 256 KiB')
        spec['position_target_plan']=json.loads(serialized)
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
    experiment = isinstance(config, MinuteExperiment)
    family = family_for_config(config) if not experiment else None
    factor_versions = {
        factor_id.split("/", 1)[0]: (
            config.rank_factor_version
            if factor_id.startswith("rank_persistence/") and config.rank_persistence_enabled
            else "disabled"
            if factor_id.startswith("rank_persistence/")
            else factor_id.split("/", 1)[1]
        )
        for factor_id in (family.factor_ids if family is not None else (config.family,))
    }
    factor_versions["market_regime"] = FACTOR_VERSION
    historical_reconstruction = (input_manifest.get('runtime_input_version') == 'historical_reconstruction_strategy/v1'
                                 or experiment and _is_historical_reconstruction(input_manifest))
    document = {
        "mode": "historical_reconstruction" if historical_reconstruction else "historical_replay",
        "data_manifest_hash": hashlib.sha256(
            _canonical_json(input_manifest).encode("utf-8"),
        ).hexdigest(),
        "code_hash": research_implementation_hash(session_profile),
        "factor_versions": factor_versions,
        "family": family.family_id if family is not None else config.family,
        "policy_version": (config.family if experiment else "leader_execution_adapter/v1"
                           if isinstance(config, LeaderBreakoutConfig) else "single_position_buy_then_sell/v1"),
        "parameters": config.to_dict(),
        "initial_state": StrategyState().to_dict(),
        "seed": 0,
        "clock": "final_completed_minute" if isinstance(config, LeaderBreakoutConfig) else "historical_bar_close" if historical_reconstruction else "observation_available_at",
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
    if experiment and historical_reconstruction:
        document["observation_clock_basis"] = "reconstructed_completed_bar_end_proxy"
    return document


def _is_historical_reconstruction(manifest: Mapping[str, Any]) -> bool:
    return 'historical_reconstruction_strategy/v1' in (
        manifest.get('runtime_input_version'), manifest.get('source_runtime_input_version'),
    )


def _final_market_regime(
    dataset: FrozenResearchDataset,
    outcomes: tuple[object, ...],
    *, chronological: bool = True,
) -> dict[str, Any] | None:
    """run 종료 시점에 성숙해 있던 3분 결과만 시장 표본에 포함한다."""
    frames = market_frames_from_observations(
        dataset.observations, dataset.theme_snapshots, chronological=chronological,
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
