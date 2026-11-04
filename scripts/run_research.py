"""고정 D2 export에 D3 Factor·전략·모의 실행을 기록한다."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Mapping

from kiwoom_monitor.application.breakout_strategy import STRATEGY_VERSION, BreakoutStrategyConfig
from kiwoom_monitor.application.market_session_schedule import SUPPORTED_RESEARCH_SESSION_PROFILES
from kiwoom_monitor.application.research_factors import FACTOR_VERSION
from kiwoom_monitor.application.research_execution import (
    COST_MODEL_VERSION, EXECUTION_MODEL_VERSION, SAME_BAR_PATH_VERSION,
    PaperExecutionEngine, SimulationCostModel, SimulationExecutionConfig,
)
from kiwoom_monitor.application.research_splits import (
    ResearchEvaluationSpec, DevelopmentPartitionSpec, DEVELOPMENT_PARTITION_VERSION,
)
from kiwoom_monitor.application.research_resources import (
    ResearchResourceGuard, ResearchResourceLimits, ResearchResourceBlocked,
)
from kiwoom_monitor.application.research_implementation import (
    LEGACY_RESEARCH_IMPLEMENTATION_HASH, research_implementation_hash,
)
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
from kiwoom_monitor.infrastructure.research_data_source import (
    load_research_input, research_input_encoded_bytes, prepare_development_partition,
)
from kiwoom_monitor.research_run import (
    ResearchRunResult, ResearchComparisonResult, ResearchRunCancelled,
    execute_research, execute_rank_comparison, research_run_identity,
    _content_id,
)


def main(argv: list[str] | None = None) -> int:
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
    parser.add_argument("--commission-bps", required=True)
    parser.add_argument("--sell-tax-bps", required=True)
    parser.add_argument("--slippage-bps", required=True)
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
    parser.add_argument('--result-connection', type=Path)
    parser.add_argument('--result-owner')
    args = parser.parse_args(argv)
    delivery_target = None
    if bool(args.result_connection) != (args.result_owner is not None):
        parser.error('--result-connection and --result-owner must be supplied together')
    if args.result_connection:
        if args.compare_rank_baseline:
            parser.error('reported result delivery supports single_run only')
        connection = args.result_connection.resolve()
        if connection == args.database.resolve() or connection.is_relative_to(args.runs_dir.resolve()):
            parser.error('result connection must not alias research database or generated artifacts')
        from kiwoom_monitor.research_result_delivery import load_result_target
        try:
            delivery_target = load_result_target(connection, args.result_owner)
        except (OSError, TypeError, ValueError):
            parser.error('explicit result connection is invalid')
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
            commission_bps=(int(args.commission_bps) if args.cost_model_version == COST_MODEL_VERSION
                            else args.commission_bps),
            sell_tax_bps=(int(args.sell_tax_bps) if args.cost_model_version == COST_MODEL_VERSION
                          else args.sell_tax_bps),
            slippage_bps=(int(args.slippage_bps) if args.cost_model_version == COST_MODEL_VERSION
                          else args.slippage_bps),
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
    document = {
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
    }
    if delivery_target is not None:
        from kiwoom_monitor.research_result_delivery import attach_result_delivery
        document = attach_result_delivery(document, args.database, args.runs_dir, result.run_id, delivery_target)
    print(json.dumps(document, ensure_ascii=False))
    return 4 if delivery_target is not None and document["delivery"]["status"] != "delivered" else 0


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
