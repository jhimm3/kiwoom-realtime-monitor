from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path

from kiwoom_monitor.application.research_discovery import DiscoveryRisk, scan_patterns
from kiwoom_monitor.application.research_splits import (
    DevelopmentPartitionSpec, FinalHoldoutBatchSpec, ResearchEvaluationSpec, ResearchFoldSpec,
)
from kiwoom_monitor.domain.simulation_contract import (
    EXECUTION_MODEL_VERSION, SAME_BAR_PATH_VERSION, default_research_cost_model,
)
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
from kiwoom_monitor.infrastructure.research_data_source import (
    FrozenResearchDataset, load_research_input, prepare_development_partition, prepare_final_holdout_partition,
    write_frozen_research_bundle,
)
from kiwoom_monitor.research_discovery import discover, evaluate_final, main
from kiwoom_monitor.research_process import load_final_holdout_execution_request, prepare_mock_automation_candidate_publication
from tests.unit.test_research_bundle_execution import KST, child, rows_for


def _day(root, day):
    rows = rows_for(day, 25)[1:]
    for index, row in enumerate(rows):
        row['ordinal'] = index + 1
        bar = row['payload']
        open_, high, low, close, volume, value = (
            (10000, 10020, 9900, 9900, 500, 1),
            (9900, 10180, 9880, 10150, 1200, 5),
            (10150, 10155, 9980, 10000, 600, 2),
        )[index % 3]
        bar.update(open=open_, high=high, low=low, close=close, volume=volume, trade_value_million_won=value)
        row['available_at'] = bar['bar_end']
    return child(root, day, rows)


def _at(day, hour=0, minute=0):
    return datetime(2026, 9, day, hour, minute, tzinfo=KST).isoformat()


def _evaluation(*folds):
    return ResearchEvaluationSpec('chronological_holdout/v1', folds, 120, 120, 0, 2, 1).to_dict()


class ResearchDiscoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        development = self.root / 'development'
        children = tuple(_day(development, date(2026, 9, day)) for day in (14, 15))
        write_frozen_research_bundle(development, children)
        self.output = self.root / 'output'
        self.request = {
            'dataset': 'development', 'output': 'output', 'max_candidates': 3, 'minimum_samples': 3,
            'max_seconds': 60, 'resource_budget': {'cpu_duty_percent': 100, 'memory_mb': 512},
            'evaluation': _evaluation(ResearchFoldSpec('train', 'TRAIN', _at(14, 9, 3), _at(14, 10)),
                                      ResearchFoldSpec('validation', 'VALIDATION', _at(15, 9, 3), _at(16))),
            'risk': dict(quantity=10, capital_won=200000, stop_loss_bps=300, target_bps=100,
                         max_hold_minutes=4, signal_valid_seconds=180, cooldown_seconds=0,
                         session_exit_buffer_seconds=300),
            'execution': {'version': EXECUTION_MODEL_VERSION, 'same_bar_path_version': SAME_BAR_PATH_VERSION,
                          'initial_cash_won': 200000, 'cost_model': {'valid_from': _at(14), 'valid_to': _at(17)}},
        }
        self.final_request = {'dataset': 'unused-until-final', 'discovery_output': 'output', 'accessed_at': _at(17),
                              'evaluation': _evaluation(ResearchFoldSpec('final', 'OOS', _at(16, 9, 3), _at(16, 10)))}

    def test_discover_replays_shortlist_and_locks_final_results(self):
        request_path = self.root / 'discover.json'
        request_path.write_text(json.dumps(self.request), encoding='utf-8')
        with redirect_stdout(StringIO()) as stdout:
            self.assertEqual(0, main(['discover', str(request_path)]))
        discovered = json.loads(stdout.getvalue())
        self.assertEqual(3, discovered['screened'])
        report_path = self.output / 'development.json'
        before = report_path.read_bytes()
        report = json.loads(before)
        self.assertGreater(discovered['selected_for_final'], 0, report['candidates'][0]['folds'])
        self.assertFalse(report['order_authorized'])
        self.assertEqual('1.5', report['execution']['cost_model']['commission_bps'])
        self.assertEqual('20', report['execution']['cost_model']['sell_tax_bps'])
        for candidate in report['candidates']:
            self.assertGreaterEqual(candidate['screen']['labeled'], 3)
            self.assertEqual({'TRAIN', 'VALIDATION'}, {fold['role'] for fold in candidate['folds']})
            self.assertTrue(all(fold['run_id'] for fold in candidate['folds']))
        self.assertEqual(discovered, discover(self.request, self.root))

        day = _day(self.root, date(2026, 9, 16))
        self.final_request['dataset'] = day
        final = evaluate_final(self.final_request, self.root)
        self.assertEqual('ok', final['status'])
        self.assertEqual(discovered['selected_for_final'], len(final['candidates']))
        self.assertEqual({'COMPLETED'}, {row['state'] for row in final['candidates']})
        self.assertEqual({'ELIGIBLE'}, {row['report_status'] for row in final['candidates']})
        repeated = evaluate_final(self.final_request, self.root)
        self.assertEqual({'CACHED'}, {row['state'] for row in repeated['candidates']})
        self.assertEqual(before, report_path.read_bytes())

        locked = load_final_holdout_execution_request(self.output / 'final-request.json')
        with self.assertRaisesRegex(ValueError, 'research-only'):
            prepare_mock_automation_candidate_publication(
                ResearchRepository(self.output / 'research.sqlite3', read_only=True), locked.candidates[0],
                implementation_hash=report['implementation_hash'], strategy_ref='test', account_ref='test',
                final_batch_id=locked.batch.batch_id, final_run_id=final['candidates'][0]['run_id'], eligibility_policy=None)

        overlapping = {**self.request, 'dataset': day, 'evaluation': _evaluation(
            ResearchFoldSpec('train', 'TRAIN', _at(16, 9, 3), _at(16, 10)),
            ResearchFoldSpec('validation', 'VALIDATION', _at(16, 10, 3), _at(17)))}
        with self.assertRaisesRegex(ValueError, 'overlaps a registered final window'):
            discover(overlapping, self.root)

    def test_unreceipted_development_file_cannot_create_a_receipt(self):
        discover(self.request, self.root)
        path = self.output / 'development.json'
        report = json.loads(path.read_bytes())
        report['scan_id'] = 'unreceipted'
        path.write_text(json.dumps(report), encoding='utf-8')
        for _ in range(2):
            with self.assertRaisesRegex(ValueError, 'saved research receipt'):
                evaluate_final(self.final_request, self.root)
        repo = ResearchRepository(self.output / 'research.sqlite3', read_only=True)
        self.assertIsNone(repo.load_search_experiment('discovery_result_unreceipted'))

    def test_development_source_may_not_extend_into_final_window(self):
        self.request['evaluation']['folds'][1]['end'] = _at(15, 10)
        with self.assertRaisesRegex(ValueError, 'beyond the declared development window'):
            discover(self.request, self.root)
        self.assertFalse(self.output.exists())

    def test_late_bar_correction_does_not_rewrite_screening_history(self):
        evaluation = ResearchEvaluationSpec.from_dict(self.request['evaluation'])
        dataset = load_research_input(self.root / 'development', session_profile='krx-regular/v1')
        train = prepare_development_partition(dataset, DevelopmentPartitionSpec('development_partition/v2', 'train'), evaluation)
        correction = deepcopy(train.observations[5])
        correction.update(revision_id='late-correction', available_at=_at(14, 9, 40))
        correction['payload'].update(high=10800, close=10800)
        arguments = dict(fold=evaluation.folds[0], purge_seconds=0, risk=DiscoveryRisk(**self.request['risk']),
                         cost=default_research_cost_model(), minimum_samples=3, max_candidates=3, checkpoint=lambda: None)
        baseline = scan_patterns(train.observations, **arguments)
        self.assertGreater(baseline['opportunities']['labeled'], 0)
        self.assertEqual(baseline, scan_patterns((*train.observations, correction), **arguments))

    def test_final_reconstruction_requires_explicit_scope_and_preserves_source_clock(self):
        relative = _day(self.root, date(2026, 9, 16))
        source = load_research_input(self.root / relative, session_profile='krx-regular/v1')
        manifest = {**source.manifest, 'runtime_input_version': 'historical_reconstruction_strategy/v1',
                    'not_contemporaneous_top20': True, 'source_availability_preserved_in_payload': True}
        observations = deepcopy(source.observations)
        for row in observations:
            original = row['available_at']
            row['payload']['source_available_at'] = _at(17)
            row['available_at'] = (datetime.fromisoformat(original) + timedelta(seconds=1)).isoformat()
        archive = FrozenResearchDataset(manifest, observations)
        batch = FinalHoldoutBatchSpec('final_holdout_batch/v1', manifest['dataset_id'], manifest['revision_ids_hash'],
                                      'krx-regular/v1', ('a' * 64,), ResearchEvaluationSpec.from_dict(self.final_request['evaluation']))
        with self.assertRaisesRegex(ValueError, 'requires the full frozen source'):
            prepare_final_holdout_partition(archive, batch)
        selected = prepare_final_holdout_partition(archive, batch, allow_historical_reconstruction=True)
        self.assertEqual('historical_reconstruction_strategy/v1', selected.manifest['source_runtime_input_version'])
        self.assertTrue(selected.observations)
        for row in selected.observations:
            self.assertEqual(_at(17), row['payload']['source_available_at'])
            self.assertEqual(timedelta(seconds=1), datetime.fromisoformat(row['available_at'])
                             - datetime.fromisoformat(row['payload']['bar_end']))

    def test_relative_shortlist_can_review_negative_proxies_without_marking_them_positive(self):
        evaluation = ResearchEvaluationSpec.from_dict(self.request['evaluation'])
        dataset = load_research_input(self.root / 'development', session_profile='krx-regular/v1')
        train = prepare_development_partition(dataset, DevelopmentPartitionSpec('development_partition/v2', 'train'), evaluation)
        cost = replace(default_research_cost_model(), commission_bps=Decimal('200'))
        arguments = dict(fold=evaluation.folds[0], purge_seconds=0, risk=DiscoveryRisk(**self.request['risk']),
                         cost=cost, minimum_samples=3, max_candidates=3, checkpoint=lambda: None)
        self.assertEqual([], scan_patterns(train.observations, **arguments)['candidates'])
        relative = scan_patterns(train.observations, **arguments, relative_shortlist=True)
        self.assertEqual(0, relative['positive_qualified_patterns'])
        self.assertEqual(3, len(relative['candidates']))
        for candidate in relative['candidates']:
            screen = candidate['screen']
            self.assertLess(screen['baseline_mean_net_bps'], screen['mean_net_bps'])
            self.assertLess(screen['mean_net_bps'], 0)
