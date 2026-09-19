from copy import deepcopy
from dataclasses import replace
from datetime import datetime
import hashlib
import json
import unittest
from unittest.mock import patch

import test_research_development_partitions as partitions
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
from kiwoom_monitor.infrastructure.research_data_source import load_research_input
from kiwoom_monitor.research_process import execute_process_request, load_research_process_request
from scripts.run_research import ResearchRunCancelled


class PartitionSearchTests(unittest.TestCase):
    def setUp(self):
        self.fixture = partitions.DevelopmentPartitionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def request(self, *, name='train', trials=4):
        path = self.fixture.write_request(mode='limited_search', name=name)
        document = json.loads(path.read_text())
        source = load_research_input(self.fixture.root / document['dataset'], session_profile=partitions.PROFILE)
        document['search'] = {
            'version': 'limited_search/v2', 'hypothesis_refs': ['partition-hypothesis'],
            'dataset_id': source.manifest['dataset_id'], 'dataset_hash': source.manifest['revision_ids_hash'],
            'family_allowlist': ['krx_bar_close_breakout/v1'],
            'factor_allowlist': ['rolling_high_breakout/v1', 'rank_persistence/v1'],
            'parameter_space': {'target_bps': [400, 500]},
            'objective': {'net_pnl_won': 'maximize'}, 'constraints': {},
            'split_version': self.fixture.evaluation.version, 'max_trials': trials,
            'max_seconds': 60, 'seed': 11, 'ablations': ['rank_persistence'],
            'cost_stress_multipliers_ppm': [2_000_000],
            'resource_budget': {'max_concurrent_trials': 1, 'cpu_duty_percent': 100},
        }
        path.write_text(json.dumps(document), encoding='utf-8')
        return load_research_process_request(path), source

    def write_changed_source(self, request, source):
        encoded = ''.join(json.dumps(row, sort_keys=True) + '\n' for row in source.observations).encode()
        (request.dataset / 'observations.jsonl').write_bytes(encoded)
        manifest_path = request.dataset / 'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest.update(dataset_id=source.manifest['dataset_id'], observations_file_hash=hashlib.sha256(encoded).hexdigest(),
                        revision_ids_hash=hashlib.sha256('\n'.join(row['revision_id'] for row in source.observations).encode()).hexdigest(),
                        quality_summary=source.manifest.get('quality_summary', {}))
        manifest_path.write_text(json.dumps(manifest))
        return replace(request, search=replace(request.search, dataset_id=manifest['dataset_id'], dataset_hash=manifest['revision_ids_hash']))

    def test_variants_share_selected_input_and_resume_cached_results(self):
        request, source = self.request()
        submitted = request.search.to_dict()
        before = {path: path.read_bytes() for path in request.dataset.rglob('*') if path.is_file()}
        first = execute_process_request(request)
        second = execute_process_request(request)
        self.assertEqual('completed', first['job_status'])
        self.assertEqual(4, first['attempted_now'])
        self.assertEqual(0, second['attempted_now'])
        self.assertEqual(first['candidate_cards'], second['candidate_cards'])
        self.assertNotEqual(request.search.experiment_id, first['experiment_id'])
        self.assertEqual(submitted, request.search.to_dict())
        self.assertEqual(before, {path: path.read_bytes() for path in request.dataset.rglob('*') if path.is_file()})
        self.assertEqual(['baseline', 'no_trade_baseline', 'ablation', 'cost_stress'],
                         [card['variant'] for card in first['candidate_cards']])
        repo = ResearchRepository(request.database)
        for trial in repo.load_search_trials(first['experiment_id']):
            run_id = trial['outcome']['run_id']
            report = repo.load_research_report(run_id)
            self.assertEqual(['train'], [fold['name'] for fold in report['fold_reports']])
            run = repo.load_run(run_id)
            self.assertTrue(run['input_manifest']['dataset_id'].startswith('development-'))
            self.assertEqual('train', run['input_manifest']['development_partition']['spec']['fold_name'])

    def test_interrupted_attempt_retries_with_same_effective_identity(self):
        request, _ = self.request(trials=1)
        with patch('kiwoom_monitor.research_process.execute_research', side_effect=ResearchRunCancelled('fixture')):
            interrupted = execute_process_request(request)
        self.assertEqual('cancelled', interrupted['job_status'])
        finished = execute_process_request(request)
        self.assertEqual(interrupted['experiment_id'], finished['experiment_id'])
        self.assertEqual(interrupted['job_id'], finished['job_id'])
        repo = ResearchRepository(request.database)
        self.assertEqual(1, len(repo.load_search_trials(finished['experiment_id'])))
        attempts = repo.load_trial_attempts(finished['experiment_id'])
        self.assertEqual('INTERRUPTED', attempts[0]['status'])
        self.assertEqual(2, len(attempts))

    def test_changed_final_source_identity_and_metadata_reuse_development_cache(self):
        request, source = self.request(trials=1)
        first = execute_process_request(request)
        changed = deepcopy(source)
        for row in changed.observations:
            if row['kind'] == 'minute_bar' and datetime.fromisoformat(row['available_at']) >= datetime.fromisoformat(self.fixture.at(22)):
                row['payload']['close'] = 999999
                row['revision_id'] += '-final-canary'
        changed.manifest.update(dataset_id='changed-source', quality_summary={'final_missing': True})
        second = execute_process_request(self.write_changed_source(request, changed))
        self.assertEqual(first['experiment_id'], second['experiment_id'])
        self.assertEqual(0, second['attempted_now'])
        self.assertEqual(first['candidate_cards'], second['candidate_cards'])

    def test_active_payload_change_with_same_revision_ids_invalidates_cache(self):
        request, source = self.request(trials=1)
        first = execute_process_request(request)
        changed = deepcopy(source)
        row = next(row for row in changed.observations if row['kind'] == 'minute_bar' and row['payload']['bar_start'] == self.fixture.at(5))
        row['payload']['close'] += 5
        second = execute_process_request(self.write_changed_source(request, changed))
        self.assertNotEqual(first['experiment_id'], second['experiment_id'])
        self.assertEqual(1, second['attempted_now'])

    def test_different_development_fold_does_not_reuse_training_cache(self):
        training, _ = self.request(trials=1)
        first = execute_process_request(training)
        path = self.fixture.root / 'request.json'
        document = json.loads(path.read_text())
        document['development_partition']['fold_name'] = 'validation'
        path.write_text(json.dumps(document))
        validation = load_research_process_request(path)
        second = execute_process_request(validation)
        self.assertNotEqual(first['experiment_id'], second['experiment_id'])
        self.assertEqual(1, second['attempted_now'])

    def test_wrong_original_source_identity_is_rejected_before_database_write(self):
        request, _ = self.request(trials=1)
        for field in ('dataset_id', 'dataset_hash'):
            bad = replace(request, search=replace(request.search, **{field: 'wrong'}))
            with self.assertRaisesRegex(ValueError, 'source dataset identity'):
                execute_process_request(bad)
            self.assertFalse(request.database.exists())

    def test_partition_context_tampering_is_rejected_before_database_write(self):
        request, _ = self.request(trials=1)
        context = dict(request.search.research_context)
        context.pop('development_partition')
        with self.assertRaisesRegex(ValueError, 'locked request'):
            execute_process_request(replace(request, search=replace(request.search, research_context=context)))
        self.assertFalse(request.database.exists())

    def test_campaign_entry_and_enqueue_require_verified_registration(self):
        request, _ = self.request(trials=1)
        with self.assertRaisesRegex(ValueError, 'stored campaign identity'):
            execute_process_request(request, campaign_cycle={})
        self.assertFalse(request.database.exists())
        repo = ResearchRepository(request.database)
        with self.assertRaisesRegex(ValueError, 'separately verified source request'):
            repo.enqueue_campaign_experiment('missing', request.search, request.dataset, source_kind='hypothesis')

    def test_original_hash_corruption_is_not_bypassed_by_projection(self):
        request, _ = self.request(trials=1)
        manifest_path = request.dataset / 'manifest.json'
        document = json.loads(manifest_path.read_text())
        document['revision_ids_hash'] = 'corrupt'
        manifest_path.write_text(json.dumps(document))
        with self.assertRaises(ValueError):
            execute_process_request(request)
        self.assertFalse(request.database.exists())

    def test_parser_locks_partition_and_rejects_supplied_context_mismatch(self):
        request, _ = self.request(trials=1)
        path = self.fixture.root / 'request.json'
        document = json.loads(path.read_text())
        document['search'] = request.search.to_dict()
        path.write_text(json.dumps(document))
        self.assertEqual(request.search, load_research_process_request(path).search)
        document['development_partition']['fold_name'] = 'validation'
        path.write_text(json.dumps(document))
        with self.assertRaisesRegex(ValueError, 'locked request'):
            load_research_process_request(path)
        self.assertFalse(request.database.exists())

    def test_final_fold_cannot_be_searched(self):
        request, _ = self.request(trials=1)
        path = self.fixture.root / 'request.json'
        document = json.loads(path.read_text())
        document['development_partition']['fold_name'] = 'final'
        path.write_text(json.dumps(document))
        with self.assertRaisesRegex(ValueError, 'final access is disabled'):
            load_research_process_request(path)
        self.assertFalse(request.database.exists())

    def test_cancel_during_load_creates_no_search_database(self):
        request, _ = self.request(trials=1)
        with self.assertRaises(ResearchRunCancelled):
            execute_process_request(request, cancel_requested=lambda: True)
        self.assertFalse(request.database.exists())
