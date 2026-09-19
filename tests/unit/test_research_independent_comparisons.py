from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import patch

from kiwoom_monitor.application.research_evaluation import build_independent_development_comparison as compare
from kiwoom_monitor.application.research_splits import (
    DevelopmentPartitionSpec, ResearchEvaluationSpec, ResearchFoldSpec,
)
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
import test_research_development_partitions as fixtures


def record(run_id='train', minute=0, role='TRAIN', pnl=100, mdd=90):
    start = datetime(2026, 9, 14, tzinfo=timezone.utc) + timedelta(minutes=minute)
    evaluation = ResearchEvaluationSpec('chronological_holdout/v1', (
        ResearchFoldSpec(run_id, role, start.isoformat(), (start + timedelta(minutes=10)).isoformat()),
    ), 120, 0, 60, 1, 1).to_dict()
    partition = DevelopmentPartitionSpec('development_partition/v2', run_id).to_dict()
    manifest = {'dataset_id': f'development-{minute}', 'revision_ids_hash': f'input-{minute}',
                'schema_version': 3, 'research_session_profile': {'version': 'fixture/v1'},
                'universe_rule': 'point_in_time_top20', 'order_policy_version': 'ordering/v1',
                'runtime_input_version': 'independent_development_input/v1',
                'development_partition': {'spec': partition, 'evaluation': evaluation,
                                          'warmup_start': (start - timedelta(minutes=2)).isoformat(),
                                          'active_start': start.isoformat(),
                                          'end': (start + timedelta(minutes=10)).isoformat()}}
    spec = {'code_hash': 'locked-code', 'family': 'breakout/v1', 'parameters': {'target_bps': 400},
            'execution_model': {'initial_cash_won': 10000}, 'cost_model': {'version': 'locked/v1'},
            'session_profile': manifest['research_session_profile'], 'evaluation_spec': evaluation,
            'data_manifest_hash': f'manifest-{minute}'}
    fold = {'name': run_id, 'role': role, 'status': 'ELIGIBLE', 'reasons': [],
            'closed_trade_count': 1, 'active_day_count': 1, 'net_realized_pnl_won': pnl,
            'max_drawdown_won': mdd,
            'by_symbol': [{'key': '005930', 'closed_trade_count': 1, 'net_realized_pnl_won': pnl}],
            'by_date': [{'key': '2026-09-14', 'closed_trade_count': 1, 'net_realized_pnl_won': pnl}],
            'by_time_bucket': [{'key': '09:00', 'closed_trade_count': 1, 'net_realized_pnl_won': pnl}]}
    return {'run_id': run_id, 'status': 'completed', 'spec': spec, 'input_manifest': manifest,
            'logical_result_hash': f'outcome-{minute}', 'report': {'run_id': run_id,
                'split_spec': evaluation, 'fold_reports': [fold]}}


class IndependentComparisonTests(unittest.TestCase):
    def test_same_conditions_different_inputs_roles_preserve_per_partition_metrics(self):
        result = compare([record(pnl=100, mdd=90), record('validation', 20, 'VALIDATION', -50, 80)])
        self.assertEqual('COMPLETE', result.status)
        self.assertEqual((2, 1, -50, 100, 25.0, 90), (
            result.eligible_partition_count, result.positive_partition_count,
            result.minimum_partition_pnl_won, result.maximum_partition_pnl_won,
            result.median_partition_pnl_won, result.worst_partition_drawdown_won))
        self.assertNotIn('return_ppm', result.to_dict())
        self.assertNotIn('max_drawdown_won', result.to_dict())
        self.assertEqual(2, sum(row.active_day_count for row in result.partitions))
        self.assertIn('active_days_are_per_partition_not_unique_days', result.limitations)

    def test_same_run_reference_does_not_double_count(self):
        result = compare([record(), record()])
        self.assertEqual(('COMPLETE', 2, 1, 1), (result.status, result.requested_count,
                                               result.unique_run_count, result.eligible_partition_count))
        self.assertEqual('duplicate_run_reference', result.partitions[1].excluded_reason)

    def test_same_effective_evidence_other_run_is_not_new_sample(self):
        other = record()
        other['run_id'] = other['report']['run_id'] = 'retry'
        result = compare([record(), other])
        self.assertEqual('duplicate_evidence', result.partitions[1].excluded_reason)
        self.assertEqual(('COMPLETE', 1), (result.status, result.eligible_partition_count))

    def test_contradictory_same_run_reference_cannot_hide_failure(self):
        failed = record(); failed.update(status='failed', error='failed attempt')
        result = compare([record(), failed])
        self.assertEqual(('INCOMPLETE', 0), (result.status, result.eligible_partition_count))
        self.assertEqual('FAILED', result.partitions[1].status)
        self.assertEqual(record()['report']['fold_reports'][0]['role'], result.partitions[1].role)
        self.assertTrue(all(row.excluded_reason == 'conflicting_run_reference' for row in result.partitions))

    def test_input_revision_same_window_cannot_supply_two_observations(self):
        other = record('revised')
        other['input_manifest']['dataset_id'] = 'development-revised'
        result = compare([record(), other])
        self.assertEqual(('INCOMPLETE', 0), (result.status, result.eligible_partition_count))
        self.assertTrue(all(row.excluded_reason == 'same_window_input_revision' for row in result.partitions))

    def test_overlapping_active_windows_exclude_both_but_adjacent_windows_are_allowed(self):
        result = compare([record(), record('overlap', 5)])
        self.assertEqual(0, result.eligible_partition_count)
        self.assertTrue(all(row.excluded_reason == 'overlapping_active_windows' for row in result.partitions))
        self.assertEqual('COMPLETE', compare([record(), record('adjacent', 10)]).status)

    def test_strategy_cost_code_execution_data_contract_and_sample_policies_must_match(self):
        for path, value in [
            (('spec', 'parameters'), {'target_bps': 500}),
            (('spec', 'cost_model'), {'version': 'other/v1'}),
            (('spec', 'code_hash'), 'other-code'),
            (('spec', 'execution_model'), {'initial_cash_won': 20000}),
            (('input_manifest', 'universe_rule'), 'other-universe'),
        ]:
            with self.subTest(path=path):
                other = record('validation', 20)
                other[path[0]][path[1]] = value
                result = compare([record(), other])
                self.assertEqual(('INCOMPARABLE', 0, None),
                                 (result.status, result.eligible_partition_count, result.median_partition_pnl_won))
        other = record('validation', 20)
        other['spec']['evaluation_spec']['purge_seconds'] = 0
        self.assertEqual('INCOMPARABLE', compare([record(), other]).status)

    def test_missing_failed_running_and_completed_without_report_remain_visible(self):
        missing = {'run_id': 'missing', 'status': 'missing'}
        failed = {'run_id': 'failed', 'status': 'failed', 'error': 'input hash mismatch'}
        running = {'run_id': 'running', 'status': 'running'}
        no_report = record('no-report', 20)
        no_report['report'] = None
        result = compare([record(), missing, failed, running, no_report])
        self.assertEqual(['ELIGIBLE', 'MISSING', 'FAILED', 'INCOMPLETE', 'INVALID'],
                         [row.status for row in result.partitions])
        self.assertEqual(('INCOMPLETE', 5, 1), (result.status, result.unique_run_count, result.eligible_partition_count))
        self.assertIn('input hash mismatch', result.partitions[2].reasons)

    def test_shortage_no_closed_trade_and_quality_failures_are_distinct(self):
        for source_status, reasons, expected in [
            ('INELIGIBLE', ['minimum_closed_trades_not_met'], 'INSUFFICIENT_SAMPLE'),
            ('INELIGIBLE', ['minimum_closed_trades_not_met', 'strict_krx_minute_bars_missing'], 'INELIGIBLE'),
            ('NOT_APPLICABLE', ['no_closed_simulated_trade'], 'NO_CLOSED_TRADE'),
        ]:
            with self.subTest(expected=expected):
                source = record()
                source['report']['fold_reports'][0].update(status=source_status, reasons=reasons,
                    closed_trade_count=0, active_day_count=0, net_realized_pnl_won=None)
                result = compare([source])
                self.assertEqual(('INCOMPLETE', expected, 0),
                                 (result.status, result.partitions[0].status, result.eligible_partition_count))

    def test_reject_legacy_final_exposed_boundary_mismatch_and_invalid_sample_metrics(self):
        sources = []
        legacy = record(); legacy['input_manifest']['runtime_input_version'] = 'continuous_bundle_replay/v1'
        sources.append(legacy)
        final = record(role='OOS'); sources.append(final)
        exposed = record()
        exposed['spec']['evaluation_spec'].update(final_holdout_accessed_at='2026-09-15T00:00:00+00:00', final_holdout_access_reason='already viewed')
        sources.append(exposed)
        boundary = record(); boundary['input_manifest']['development_partition']['end'] = '2026-09-15T00:00:00+00:00'
        sources.append(boundary)
        invalid = record(); invalid['report']['fold_reports'][0]['closed_trade_count'] = 0
        sources.append(invalid)
        for source in sources:
            with self.subTest(source=source['input_manifest'].get('runtime_input_version')):
                self.assertEqual('INVALID', compare([source]).partitions[0].status)

    def test_global_final_canary_does_not_change_projection_or_mutate_source(self):
        source = record()
        changed = deepcopy(source)
        changed['report'].update(status='FINAL_FAILED', reasons=['final-canary'], raw={'OOS': -10**30})
        changed['input_manifest']['quality_summary'] = {'OOS': 'missing'}
        before = deepcopy(changed)
        self.assertEqual(compare([source]), compare([changed]))
        self.assertEqual(before, changed)

    def test_strata_preserve_concentration_and_are_immutable(self):
        source = record()
        result = compare([source])
        source['report']['fold_reports'][0]['by_symbol'][0]['key'] = 'changed'
        self.assertEqual(('by_symbol', '005930', 1, 100), result.partitions[0].strata[0])
        with self.assertRaises(FrozenInstanceError):
            result.partitions[0].status = 'changed'
        self.assertNotIn('report', result.to_dict()['partitions'][0])

    def test_empty_and_excessive_scope_rejected(self):
        for sources in ([], [record()] * 201):
            with self.assertRaises(ValueError):
                compare(sources)

    def test_repository_reads_explicit_snapshot_preserves_missing_and_does_not_load_raw_data(self):
        fixture = fixtures.DevelopmentPartitionTests(); fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        repo = ResearchRepository(fixture.root / 'compare.sqlite3')
        ids = []
        for name in ('train', 'validation'):
            partition = replace(fixture.partition, fold_name=name)
            result = fixtures.execute_research(fixture.prepare(partition=partition), repo,
                fixture.root / 'runs', fixtures._strategy(), fixtures._execution(),
                partition.evaluation_for(fixture.evaluation), session_profile=fixtures.PROFILE)
            ids.append(result.run_id)
        before = repo.path.read_bytes()
        with (patch.object(repo, 'load_execution_events', side_effect=AssertionError('raw events forbidden')),
              patch.object(repo, 'load_run', side_effect=AssertionError('no per-run query'))):
            result = repo.load_independent_development_comparison(tuple(ids + ['unknown', ids[0]]))
        self.assertEqual(4, result.requested_count)
        self.assertEqual(1, len(result.condition_keys))
        self.assertEqual('MISSING', result.partitions[2].status)
        self.assertEqual('duplicate_run_reference', result.partitions[3].excluded_reason)
        self.assertFalse(any(row.status == 'INVALID' for row in result.partitions[:2]), result)
        self.assertEqual(before, repo.path.read_bytes())
        self.assertEqual(23, repo.schema_version())
        json.dumps(result.to_dict())
        for values in ((), ('',), ('a',) * 201):
            with self.assertRaises(ValueError):
                repo.load_independent_development_comparison(values)
