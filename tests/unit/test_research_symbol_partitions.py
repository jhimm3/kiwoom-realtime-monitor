from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_research_development_partitions as fixtures
import test_research_independent_comparisons as comparison_fixtures
import test_research_partition_search as search_fixtures
from kiwoom_monitor.application.research_splits import (
    DevelopmentSymbolPartitionSpec, DevelopmentPartitionSpec,
)
from kiwoom_monitor.application.research_evaluation import build_independent_development_comparison
from kiwoom_monitor.infrastructure.research_data_source import prepare_development_partition, development_partition_start
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
from kiwoom_monitor.research_process import load_research_process_request, execute_process_request
from scripts import run_research as runner


def symbol(bucket=0, salt='research-2026', count=4):
    return DevelopmentSymbolPartitionSpec('stock_hash_partition/v1', salt, count, bucket)


class SymbolPartitionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.DevelopmentPartitionTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.partition = DevelopmentPartitionSpec('development_partition/v3', 'train', symbol_partition=symbol())

    def prepare(self, dataset=None, partition=None):
        return prepare_development_partition(dataset or self.fixture.dataset, partition or self.partition, self.fixture.evaluation)

    def test_pinned_assignment_aliases_alphanumeric_and_new_stocks_do_not_reshuffle(self):
        policy = symbol()
        self.assertEqual([0, 0, 1], [policy.bucket_for(code) for code in ('005930', '000660', '0710A0')])
        self.assertTrue(all(policy.bucket_for(code) == 0 for code in ('005930', 'A005930', '005930_NX', '005930_AL', ' A005930_AL ')))
        before = {code: policy.bucket_for(code) for code in ('005930', '000660', '0710A0')}
        for code in ('999999', '000001', '000002'): policy.bucket_for(code)
        self.assertEqual(before, {code: policy.bucket_for(code) for code in before})
        self.assertEqual(policy, DevelopmentSymbolPartitionSpec.from_dict(policy.to_dict()))
        self.assertEqual(self.partition, DevelopmentPartitionSpec.from_dict(self.partition.to_dict()))

    def test_invalid_policy_and_codes_rejected_without_io(self):
        for change in ({'version': 'wrong'}, {'salt': ''}, {'salt': ' '}, {'salt': 1}, {'salt': 'x'*129}, {'salt': '\x00'},
                       {'bucket_count': 1}, {'bucket_count': 21}, {'bucket_count': True}, {'bucket_count': 4.0},
                       {'bucket': -1}, {'bucket': 4}, {'bucket': True}, {'bucket': 1.0}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                DevelopmentSymbolPartitionSpec.from_dict({**symbol().to_dict(), **change})
        for code in ('', '5930', '005930_KRX', 'abc123', '00000/', 5930, None):
            with self.subTest(code=code), self.assertRaises(ValueError): symbol().bucket_for(code)
        for document in ({**symbol().to_dict(), 'use_future_prices': True}, {'version': 'stock_hash_partition/v1'}, None):
            with self.assertRaises(ValueError): DevelopmentSymbolPartitionSpec.from_dict(document)
        self.assertEqual([], list(self.root.iterdir()))

    def test_legacy_v2_document_bit_compatible_and_new_version_requires_symbol(self):
        legacy = self.fixture.partition
        self.assertEqual({'version': 'development_partition/v2', 'fold_name': 'train',
                          'state_policy': 'reset_state_and_cash_per_partition/v1'}, legacy.to_dict())
        for document in ({**legacy.to_dict(), 'symbol_partition': symbol().to_dict()},
                         {'version': 'development_partition/v3', 'fold_name': 'train'},
                         {**self.partition.to_dict(), 'symbol_partition': None}):
            with self.assertRaises(ValueError): DevelopmentPartitionSpec.from_dict(document)

    def test_normalization_dependency_is_in_explicit_profile_implementation_hash(self):
        before = runner.research_implementation_hash(fixtures.PROFILE)
        legacy = runner.research_implementation_hash(None)
        original = Path.read_bytes
        def changed(path):
            value = original(path)
            return value + b'\n# simulated normalization revision\n' if path.name == 'ranking.py' else value
        with patch.object(Path, 'read_bytes', new=changed):
            after = runner.research_implementation_hash(fixtures.PROFILE)
            self.assertEqual(legacy, runner.research_implementation_hash(None))
        self.assertNotEqual(before, after)

    def test_final_and_exposed_policy_remain_disabled(self):
        for name in ('final', 'missing'):
            with self.assertRaises(ValueError): self.prepare(partition=replace(self.partition, fold_name=name))
        exposed = replace(self.fixture.evaluation, final_holdout_accessed_at=self.fixture.at(31), final_holdout_access_reason='seen')
        with self.assertRaises(ValueError): prepare_development_partition(self.fixture.dataset, self.partition, exposed)

    def test_projection_keeps_original_market_context_and_selected_identity_locks_policy(self):
        prepared = self.prepare()
        legacy = self.fixture.prepare()
        self.assertEqual(legacy.observations, prepared.observations)
        self.assertEqual(legacy.theme_snapshots, prepared.theme_snapshots)
        self.assertNotEqual(legacy.manifest['dataset_id'], prepared.manifest['dataset_id'])
        for other in (replace(self.partition, symbol_partition=symbol(1)),
                      replace(self.partition, symbol_partition=symbol(salt='other')),
                      replace(self.partition, symbol_partition=symbol(count=5))):
            self.assertNotEqual(prepared.manifest['dataset_id'], self.prepare(partition=other).manifest['dataset_id'])
        self.assertEqual(symbol().to_dict(), prepared.manifest['development_partition']['spec']['symbol_partition'])

    def test_oos_canary_global_counts_and_future_listing_do_not_change_input(self):
        original = self.prepare(); changed = deepcopy(self.fixture.dataset)
        changed.manifest.update(dataset_id='changed-full', revision_count=999999, quality_summary={'future_failure': True})
        for row in changed.observations:
            if row['available_at'] >= self.fixture.at(22):
                row['payload'] = {'future_listing': '999999', 'extreme_price': 10**30}
                row['revision_id'] = 'future-' + row['revision_id']
        self.assertEqual(original, self.prepare(changed))

    def multi_symbol(self):
        dataset = deepcopy(self.fixture.dataset)
        rows = list(dataset.observations)
        rows[0]['payload']['codes'] = ['005930', '0710A0']
        for row in tuple(rows):
            if row['kind'] != 'minute_bar': continue
            peer = deepcopy(row); peer.update(revision_id='peer-'+row['revision_id'], subject='0710A0:KRX', accepted_sequence=row['accepted_sequence']+100)
            peer['payload']['code'] = '0710A0'; rows.append(peer)
        return replace(dataset, observations=tuple(rows))

    def execute(self, prepared, suffix):
        repo = ResearchRepository(self.root / f'{suffix}.sqlite3')
        result = runner.execute_research(prepared, repo, self.root / suffix,
            fixtures._strategy(), fixtures._execution(), self.partition.evaluation_for(self.fixture.evaluation),
            session_profile=fixtures.PROFILE)
        return result, repo

    def test_only_admitted_targets_reach_engine_with_own_bars_and_full_universe(self):
        dataset = self.multi_symbol(); prepared = self.prepare(dataset)
        processed, evaluated, histories, universes = [], [], [], []
        original_bar = runner.PaperExecutionEngine.process_bar
        family = runner.family_for_config(fixtures._strategy())
        original_evaluate = family.evaluate_bar
        def bar(engine, value):
            processed.append(value.code); return original_bar(engine, value)
        def evaluate(**kwargs):
            evaluated.append(kwargs['evaluation_bar'].code)
            histories.append({value.code for value in kwargs['bar_history']})
            universes.append(kwargs['universe_frames'][-1].codes)
            return original_evaluate(**kwargs)
        with patch.object(runner.PaperExecutionEngine, 'process_bar', new=bar), patch.object(runner, 'family_for_config', return_value=replace(family, evaluate_bar=evaluate)):
            result, repo = self.execute(prepared, 'targets')
        self.assertTrue(processed); self.assertEqual({'005930'}, set(processed))
        self.assertTrue(evaluated); self.assertEqual({'005930'}, set(evaluated))
        self.assertTrue(all(values == {'005930'} for values in histories))
        self.assertTrue(all(values == ('005930', '0710A0') for values in universes))
        report = repo.load_research_report(result.run_id)
        self.assertEqual('PASS', report['data_quality']['status'])

    def test_each_group_starts_with_fresh_cash_and_distinct_scientific_identity(self):
        dataset = self.multi_symbol(); states = []; original = runner.PaperExecutionEngine
        def engine(*args, **kwargs):
            value = original(*args, **kwargs); states.append(deepcopy(value.portfolio)); return value
        with patch.object(runner, 'PaperExecutionEngine', side_effect=engine):
            first, _ = self.execute(self.prepare(dataset), 'first')
            second, _ = self.execute(self.prepare(dataset, replace(self.partition, symbol_partition=symbol(1))), 'second')
        self.assertEqual(states[0], states[1]); self.assertNotEqual(first.run_id, second.run_id)

    def test_peer_changes_affect_input_identity_context_is_shared_not_sealed(self):
        dataset = self.multi_symbol(); original = self.prepare(dataset); changed = deepcopy(dataset)
        for row in changed.observations:
            if row['kind'] == 'minute_bar' and row['payload']['code'] == '0710A0' and row['available_at'] < self.fixture.at(10):
                row['payload']['close'] += 1; break
        self.assertNotEqual(original.manifest['dataset_id'], self.prepare(changed).manifest['dataset_id'])

    def test_invalid_runtime_symbol_is_rejected_before_start_run(self):
        prepared = deepcopy(self.prepare())
        next(row for row in prepared.observations if row['kind'] == 'minute_bar')['payload']['code'] = 'bad'
        with patch.object(ResearchRepository, 'start_run', side_effect=AssertionError('must not write run')):
            with self.assertRaises(ValueError): self.execute(prepared, 'invalid')

    def test_existing_json_single_run_accepts_v3_without_new_api_or_batch_changes(self):
        path = self.fixture.write_request()
        document = json.loads(path.read_text()); document['development_partition'] = self.partition.to_dict()
        path.write_text(json.dumps(document))
        request = load_research_process_request(path)
        self.assertEqual(self.partition, request.development_partition)
        result = execute_process_request(request)
        self.assertEqual(['train'], [row['name'] for row in result['report']['fold_reports']])
        manifest = json.loads(Path(result['manifest']).read_text())
        self.assertEqual(self.partition.to_dict(), manifest['development_partition']['spec'])

    def test_empty_admitted_group_preserves_no_trade_instead_of_fake_samples(self):
        prepared = self.prepare(partition=replace(self.partition, symbol_partition=symbol(1)))
        result, repo = self.execute(prepared, 'empty')
        self.assertEqual(0, result.candidate_count)
        self.assertEqual(0, repo.load_research_report(result.run_id)['fold_reports'][0]['closed_trade_count'])

    def test_existing_finite_search_variants_and_cache_keep_same_target_policy(self):
        fixture = search_fixtures.PartitionSearchTests(); fixture.fixture = self.fixture
        fixture.request()
        path = self.root / 'request.json'
        document = json.loads(path.read_text())
        document['development_partition'] = replace(self.partition, symbol_partition=symbol(1)).to_dict()
        path.write_text(json.dumps(document))
        request = load_research_process_request(path)
        with patch.object(runner.PaperExecutionEngine, 'process_bar', side_effect=AssertionError('no admitted targets')):
            first = execute_process_request(request)
            second = execute_process_request(request)
        self.assertEqual(4, first['attempted_now']); self.assertEqual(0, second['attempted_now'])
        self.assertEqual('completed', first['job_status'])

    def test_comparison_conditions_do_not_mix_different_target_groups(self):
        def record(run_id, bucket):
            document = comparison_fixtures.record(run_id)
            document['input_manifest']['development_partition']['spec'] = replace(self.partition, fold_name=run_id, symbol_partition=symbol(bucket)).to_dict()
            document['report']['fold_reports'][0]['by_symbol'][0]['key'] = '005930' if bucket == 0 else '0710A0'
            return document
        same = build_independent_development_comparison((record('one', 0), record('two', 0)))
        different = build_independent_development_comparison((record('one', 0), record('two', 1)))
        self.assertEqual(1, len(same.condition_keys))
        self.assertEqual('INCOMPARABLE', different.status)
        self.assertEqual(2, len(different.condition_keys))

    def test_completed_report_with_wrong_target_or_missing_symbol_samples_is_invalid(self):
        for wrong in ('outside', 'missing'):
            document = comparison_fixtures.record('train')
            document['input_manifest']['development_partition']['spec'] = self.partition.to_dict()
            if wrong == 'outside': document['report']['fold_reports'][0]['by_symbol'][0]['key'] = '0710A0'
            else: document['report']['fold_reports'][0]['by_symbol'] = []
            result = build_independent_development_comparison((document,))
            self.assertEqual('INVALID', result.partitions[0].status)


if __name__ == '__main__':
    unittest.main()
