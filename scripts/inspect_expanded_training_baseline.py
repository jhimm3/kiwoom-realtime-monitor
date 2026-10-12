"""완료된 확대 TRAIN 원장과 실제 보유 경로를 읽기 전용 대조한다."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import gc
import hashlib
import json
from pathlib import Path
from statistics import median

from scripts.prepare_historical_baseline_requests import _write_immutable_json
from scripts.summarize_historical_baseline_results import (
    execution_loss_summary, position_observation_summary, _training_trade_path,
    _loss_attribution_totals,
)
from kiwoom_monitor.application.breakout_strategy import regular_session_time_rejection
from kiwoom_monitor.application.market_session_schedule import KST, krx_regular_session_hours
from kiwoom_monitor.application.research_implementation import research_implementation_hash
from kiwoom_monitor.application.research_replay import replay_krx_minute_bars
from kiwoom_monitor.application.research_resources import ResearchResourceGuard, ResearchResourceLimits
from kiwoom_monitor.application.research_splits import DevelopmentPartitionSpec
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
from kiwoom_monitor.infrastructure.research_data_source import load_research_input, prepare_development_partition
from kiwoom_monitor.research_process import load_development_validation_request


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(path):
    return json.loads(Path(path).read_bytes())


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_cohort(index, prepared, plan):
    require(index['state'] == 'COMPLETED', '완료 평가만 진단할 수 있습니다.')
    cases = [case for batch in prepared['batches'] for case in batch['cases']]
    require(cases == plan['cohort'] and len(cases) == index['date_count'], '선언된 날짜 전체가 필요합니다.')
    require(all(case['role'] == 'TRAIN' for case in cases), 'TRAIN 외 범위는 진단하지 않습니다.')
    require(len({case['outcome_date'] for case in cases}) == len(cases), '중복 날짜는 허용하지 않습니다.')
    require(len(prepared['batches']) == len(index['batch_reviews']) == index['batch_count'], '묶음 범위가 다릅니다.')
    require([row['request'] for row in prepared['batches']] == plan['requests'], '요청 순서가 다릅니다.')
    return cases


def session_bucket(trade):
    at = datetime.fromisoformat(trade['opened_at'])
    require(at.tzinfo is not None and at.utcoffset() is not None, '진입 시각의 시간대가 필요합니다.')
    require(not regular_session_time_rejection(at), '연속장 밖 진입 원장입니다.')
    local = at.astimezone(KST)
    opened, _ = krx_regular_session_hours(local.date())
    minutes = (local - datetime.combine(local.date(), opened, KST)).total_seconds() / 60
    for upper, label in ((60, '개장 후 0~60분'), (180, '개장 후 60~180분'),
                         (300, '개장 후 180~300분')):
        if minutes < upper:
            return label
    return '개장 후 300분~진입 마감'


def summarize_trades(rows):
    totals = _loss_attribution_totals(rows)
    require(totals['gross_at_fill_prices_won'] - totals['commission_won'] - totals['tax_won']
            == totals['net_pnl_won'], '전체 비용 항등식이 다릅니다.')
    groups = []
    for dimension, key_fn in (
        ('청산 사유', lambda row: tuple(sorted(row['exit_reasons']))),
        ('진입 시간', session_bucket),
        ('결과 월', lambda row: row['outcome_date'][:7]),
        ('돌파 강도', lambda row: strength_bucket(row['entry_factor']['value']['distance_bps'])),
    ):
        selected = defaultdict(list)
        for row in rows:
            selected[key_fn(row)].append(row)
        for key, trades in sorted(selected.items()):
            groups.append({'dimension': dimension, 'key': list(key) if isinstance(key, tuple) else key,
                           **_loss_attribution_totals(trades)})
        require(sum(len(trades) for trades in selected.values()) == len(rows), '분류 범위가 다릅니다.')
    horizons = []
    for minute in (1, 3, 5, 10):
        points = [next(point for point in row['horizons'] if point['minutes'] == minute) for row in rows]
        observed = [point['close_delta_bps'] for point in points if point['status'] == 'OBSERVED']
        horizons.append({'minutes': minute, 'status_counts': dict(Counter(point['status'] for point in points)),
                         'observed_median_close_delta_bps': median(observed) if observed else None,
                         'observed_positive_count': sum(value > 0 for value in observed)})
    return {'totals': totals, 'groups': groups, 'horizon_summary': horizons,
            'path_status_counts': dict(Counter(row['path_status'] for row in rows)),
            'positive_close_seen_net_loss_count': sum(row['positive_close_seen_net_loss'] is True for row in rows),
            'exit_raw_decomposition_count': sum(not row['risk_exit_bar_excluded'] for row in rows),
            'raw_fill_effect_totals': {key: sum(row['raw_fill_effects'][key] or 0 for row in rows) for key in (
                'entry_next_bar_gap_won', 'entry_modeled_slippage_won',
                'exit_next_bar_gap_won', 'exit_modeled_slippage_won')}}


def strength_bucket(distance):
    require(type(distance) is int and distance >= 0, '진입 돌파 강도가 유효하지 않습니다.')
    for upper, label in ((10, '0~10bp'), (25, '10~25bp'), (50, '25~50bp')):
        if distance < upper:
            return label
    return '50bp 이상'


def inspect_batch(batch, review, scientific, checkpoint):
    request = load_development_validation_request(Path(batch['request']))
    config = request.request
    require(all(fold.role == 'TRAIN' for fold in config.evaluation.folds), 'TRAIN 외 요청입니다.')
    require([run['fold_name'] for run in review['runs']] == list(request.fold_names), 'native 날짜 순서가 다릅니다.')
    require(sha(batch['request']) == review['request_sha256'], '요청 바이트가 다릅니다.')
    require(config.execution.cost_model is not None, '원래 비용 모델이 필요합니다.')
    dataset = load_research_input(config.dataset, session_profile=config.session_profile, checkpoint=checkpoint)
    require((dataset.manifest['dataset_id'], dataset.manifest['revision_ids_hash'])
            == (request.dataset_id, request.dataset_hash), '원본 입력 식별자가 다릅니다.')
    repository = ResearchRepository(config.database, read_only=True)
    result = []
    for previous in review['runs']:
        checkpoint()
        run = repository.load_run(previous['run_id'])
        partition = DevelopmentPartitionSpec('development_partition/v2', previous['fold_name'])
        require(run is not None and run['status'] == 'completed', 'native 완료 상태가 아닙니다.')
        spec = run['spec']
        require(spec['code_hash'] == scientific and spec['parameters'] == config.strategy.to_dict()
                and spec['execution_model'] == config.execution.to_dict()
                and spec['evaluation_spec'] == partition.evaluation_for(config.evaluation).to_dict(),
                'native 과학적 설정이 다릅니다.')
        manifest = config.runs_dir / previous['run_id'] / 'manifest.json'
        require(sha(manifest) == previous['output_manifest_sha256'], '출력 manifest 바이트가 다릅니다.')
        published = read(manifest)
        require(published['spec'] == spec and published['logical_result_hash'] == run['logical_result_hash']
                == previous['run_logical_result_hash'], 'native 논리 결과와 출력이 다릅니다.')
        projected = prepare_development_partition(dataset, partition, config.evaluation, checkpoint=checkpoint)
        require((projected.manifest['dataset_id'], projected.manifest['revision_ids_hash']) == (
            previous['native_input_dataset_id'], previous['native_input_revision_ids_hash']), 'native 분할 입력과 다릅니다.')
        events = repository.load_execution_events(previous['run_id'])
        decisions = repository.load_evaluations(previous['run_id'])
        loss = execution_loss_summary(events, decisions, attribute_prices=True)
        plain = {**loss, 'trades': [{key: value for key, value in trade.items() if key != 'price_attribution'}
                                   for trade in loss['trades']]}
        require(plain == previous['loss'], '보존된 FIFO 거래 원장과 다릅니다.')
        require(position_observation_summary(events) == previous['terminal_position'], '종료 포지션 원장과 다릅니다.')
        bars_by_symbol = defaultdict(list)
        for bar in replay_krx_minute_bars(projected.observations, session_profile=config.session_profile):
            bars_by_symbol[bar.code].append(bar)
        event_map = {event['event_id']: event for event in events}
        decision_map = {decision['decision_id']: decision for decision in decisions}
        for trade in loss['trades']:
            checkpoint()
            path = _training_trade_path(trade, event_map, decision_map, bars_by_symbol[trade['symbol']],
                                        config.strategy, config.execution.cost_model)
            result.append({**trade, **path, 'run_id': previous['run_id'],
                           'outcome_date': previous['outcome_date']})
        del projected, events, decisions, bars_by_symbol, event_map, decision_map, loss, plain
    return result


def inspect(root, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    require(output != root and not output.is_relative_to(root), '기준 결과 밖의 별도 출력 폴더를 사용하세요.')
    index, prepared, plan = (read(root / name) for name in
                             ('evaluation-index.json', 'prepared-index.json', 'evaluation-plan.json'))
    cases = validate_cohort(index, prepared, plan)
    scientific = research_implementation_hash('krx-regular/v1')
    require(scientific == index['scientific_hash'] == plan['scientific_hash'], '현재 과학적 구현이 다릅니다.')
    require(sha(root / 'evaluation-plan.json') == index['evaluation_plan_sha256']
            and sha(root / 'prepared-index.json') == index['prepared_index_sha256'], '기준 결합 hash가 다릅니다.')
    bindings = {**plan['bound_file_sha256'], **{str(root / name): sha(root / name) for name in
                ('evaluation-index.json', 'evaluation-summary.json', 'BASELINE_EVALUATION_REVIEW.md')}}
    frozen = {path: digest for batch in prepared['batches'] for path, digest in batch['file_sha256'].items()}
    for path, digest in {**bindings, **frozen}.items():
        require(sha(path) == digest, '고정 근거 바이트가 다릅니다: ' + path)
    output.mkdir(parents=True, exist_ok=True)
    diagnostic_plan = {'version': 'expanded_training_diagnostic_plan/v1', 'scientific_hash': scientific,
        'source_bindings': bindings, 'frozen_input_files': frozen, 'diagnostic_script_sha256': sha(__file__),
        'cohort': cases, 'training_only': True, 'validation_performance_queried': False, 'oos_queried': False,
        'horizons_minutes': [1, 3, 5, 10], 'strength_boundaries_bps': [0, 10, 25, 50],
        'entry_minutes_after_session_open_boundaries': [0, 60, 180, 300],
        'candidate_eligibility_evaluated': False, 'counterfactual_performance_computed': False}
    _write_immutable_json(output / 'diagnostic-plan.json', diagnostic_plan)
    guard = ResearchResourceGuard(ResearchResourceLimits(memory_mb=1024, cpu_duty_percent=50))
    rows, databases, batch_results, observed_dates = [], {}, [], []
    for batch, reference in zip(prepared['batches'], index['batch_reviews'], strict=True):
        guard.checkpoint()
        require(sha(reference['path']) == reference['sha256'], '완료 리뷰 hash가 다릅니다.')
        review = read(reference['path'])
        require(review['evaluation_plan_sha256'] == index['evaluation_plan_sha256']
                and review['scientific_hash'] == scientific, '묶음 계보가 다릅니다.')
        require([run['outcome_date'] for run in review['runs']] == [case['outcome_date'] for case in batch['cases']],
                '날짜 전체 원장 범위가 다릅니다.')
        request = read(batch['request'])
        database = request['request']['database']
        before = sha(database)
        trades = inspect_batch(batch, review, scientific, guard.checkpoint)
        require(sha(database) == before, '진단 중 기준 DB 바이트가 바뀌었습니다.')
        databases[database] = before
        result = {'batch_name': batch['name'], 'baseline_review_sha256': reference['sha256'],
                  'database_sha256': before, 'trades': trades}
        destination = output / (batch['name'] + '.json')
        _write_immutable_json(destination, result)
        batch_results.append({'path': str(destination), 'sha256': sha(destination)})
        rows.extend(trades)
        observed_dates.extend(run['outcome_date'] for run in review['runs'])
        print(json.dumps({'state': 'DIAGNOSING', 'completed_days': len(observed_dates),
                          'completed_batches': len(batch_results), 'closed_trade_count': len(rows)}), flush=True)
        del trades, result, review, request
        gc.collect()
    totals = summarize_trades(rows)
    require(len(rows) == index['closed_trade_count_sum']
            and totals['totals']['net_pnl_won'] == index['net_pnl_won_sum'], '전체 기준 손익과 다릅니다.')
    require([row['outcome_date'] for row in cases] == observed_dates, '날짜 전체 범위가 다릅니다.')
    require(sha(__file__) == diagnostic_plan['diagnostic_script_sha256'], '진단 구현 바이트가 바뀌었습니다.')
    for path, digest in {**bindings, **frozen, **databases}.items():
        require(sha(path) == digest, '진단 후 근거 바이트가 다릅니다: ' + path)
    summary = {'version': 'expanded_training_failure_review/v1', 'state': 'COMPLETED', **totals,
               'date_count': len(cases), 'batch_count': len(batch_results), 'batch_results': batch_results,
               'diagnostic_plan_sha256': sha(output / 'diagnostic-plan.json'), 'source_database_sha256': databases,
               'scientific_hash': scientific, 'baseline_bytes_preserved': True, 'input_bytes_preserved': True,
               'training_only': True, 'oos_queried': False, 'validation_performance_queried': False,
               'candidate_eligibility_evaluated': False, 'counterfactual_performance_computed': False,
               'actual_account_cost_verified': False, 'full_session_completeness_verified': False,
               'continuous_equity_computed': False}
    _write_immutable_json(output / 'failure-review.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = inspect(args.root, args.output)
    print(json.dumps({'state': report['state'], 'date_count': report['date_count'], 'totals': report['totals']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
