from contextlib import closing
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from io import StringIO
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

import test_research_development_validation as fixtures
from kiwoom_monitor import research_process as rp
from kiwoom_monitor.application.research_splits import DevelopmentSymbolPartitionSpec, DevelopmentPartitionSpec
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
from kiwoom_monitor.infrastructure.research_data_source import prepare_development_partition
from kiwoom_monitor.presentation.research_dialog import DevelopmentValidationDialog
from scripts import run_research as runner


class SymbolValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.fixture = fixtures.DevelopmentValidationTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.document = {**deepcopy(self.fixture.document), 'version': 'independent_development_validation/v2',
            'symbol_partition': {'version': 'stock_hash_partition/v1', 'salt': 'research-2026', 'bucket_count': 4, 'buckets': [0, 1]}}
        self.path = self.root / 'group-validation.json'
        self.write(); self.batch = rp.load_development_validation_request(self.path)
        self.cancel, self.result = self.root / 'group.cancel', self.root / 'group.result.json'

    def write(self, document=None):
        self.path.write_text(json.dumps(document or self.document), encoding='utf-8')

    def execute(self, batch=None, **kwargs):
        return rp.execute_development_validation(batch or self.batch, cancel_path=self.cancel, **kwargs)

    def cli(self):
        with patch('sys.stdout', new=StringIO()):
            code = rp.main(['--validate-partitions', str(self.path), '--result', str(self.result), '--cancel', str(self.cancel)])
        return code, json.loads(self.result.read_text())

    def test_v2_roundtrip_relative_paths_shared_policy_and_one_fold(self):
        self.assertEqual('independent_development_validation/v2', self.batch.version)
        self.assertEqual([0, 1], [policy.bucket for policy in self.batch.symbol_partitions])
        snapshot = self.root / 'snapshot.json'; snapshot.write_text(json.dumps(self.batch.to_dict()))
        self.assertEqual(self.batch, rp.load_development_validation_request(snapshot))
        self.assertTrue(Path(self.batch.to_dict()['request']['dataset']).is_absolute())
        self.write({**self.document, 'fold_names': ['train']})
        batch = rp.load_development_validation_request(self.path)
        result = self.execute(batch)
        self.assertEqual((2, ['train', 'train']), (result['requested_step_count'], [row['fold_name'] for row in result['steps']]))

    def test_parser_rejects_wrong_version_extra_missing_and_malformed_group_header(self):
        headers = [None, [], {}, {**self.document['symbol_partition'], 'extra': 1},
            {**self.document['symbol_partition'], 'version': 'unknown'},
            {**self.document['symbol_partition'], 'salt': ''},
            {**self.document['symbol_partition'], 'bucket_count': True}]
        for header in headers:
            self.write({**self.document, 'symbol_partition': header})
            with self.subTest(header=header), self.assertRaises(ValueError): rp.load_development_validation_request(self.path)
        for document in ({**self.document, 'version': 'independent_development_validation/v1'},
                         {key: value for key, value in self.document.items() if key != 'symbol_partition'},
                         {**self.document, 'extra': True}):
            self.write(document)
            with self.assertRaises(ValueError): rp.load_development_validation_request(self.path)
        self.assertFalse(self.batch.request.database.exists())

    def test_parser_rejects_duplicate_reverse_outside_noninteger_empty_or_single_bucket(self):
        for buckets in ([0, 0], [1, 0], [-1, 0], [0, 4], [0, True], [0, 1.0], [], [0], '0,1', list(range(21))):
            self.write({**self.document, 'symbol_partition': {**self.document['symbol_partition'], 'buckets': buckets}})
            with self.subTest(buckets=buckets), self.assertRaises(ValueError): rp.load_development_validation_request(self.path)

    def test_direct_request_rejects_mutable_heterogeneous_policy_and_matrix_over_200(self):
        for symbols in (list(self.batch.symbol_partitions), (self.batch.symbol_partitions[0],),
                        (self.batch.symbol_partitions[0], replace(self.batch.symbol_partitions[1], salt='different')),
                        tuple(reversed(self.batch.symbol_partitions)), (None, None)):
            with self.assertRaises(ValueError): replace(self.batch, symbol_partitions=symbols)
        symbols = tuple(DevelopmentSymbolPartitionSpec('stock_hash_partition/v1', 'fixed', 20, bucket) for bucket in range(20))
        with self.assertRaisesRegex(ValueError, '200'): replace(self.batch, fold_names=tuple(f'f{i}' for i in range(11)), symbol_partitions=symbols)

    def test_parser_rejects_matrix_over_200_final_exposed_nonfixed_and_empty_fold(self):
        for names in ([], ['final'], ['train', 'final'], ['validation', 'train'], ['train', 'train'], ['missing']):
            self.write({**self.document, 'fold_names': names})
            with self.assertRaises(ValueError): rp.load_development_validation_request(self.path)
        for key, value in (('mode', 'limited_search'), ('session_profile', None), ('development_partition', self.fixture.fixture.partition.to_dict())):
            document = deepcopy(self.document); document['request'][key] = value; self.write(document)
            with self.assertRaises(ValueError): rp.load_development_validation_request(self.path)
        document = deepcopy(self.document)
        document['request']['evaluation'].update(final_holdout_accessed_at=self.fixture.fixture.at(31), final_holdout_access_reason='seen')
        self.write(document)
        with self.assertRaises(ValueError): rp.load_development_validation_request(self.path)
        document = deepcopy(self.document); start = datetime.fromisoformat(self.fixture.fixture.at(2))
        folds = [dict(name=f'f{i}', role='TRAIN', start=(start+timedelta(minutes=3*i)).isoformat(),
                      end=(start+timedelta(minutes=3*i+1)).isoformat()) for i in range(11)]
        document['request']['evaluation']['folds'] = folds; document['fold_names'] = [fold['name'] for fold in folds]
        document['symbol_partition'].update(bucket_count=20, buckets=list(range(20))); self.write(document)
        with self.assertRaisesRegex(ValueError, '200'): rp.load_development_validation_request(self.path)
        document['request']['evaluation']['folds'] = folds[:10]; document['fold_names'] = [fold['name'] for fold in folds[:10]]
        self.write(document)
        self.assertEqual(200, len(rp.load_development_validation_request(self.path).fold_names) * 20)

    def test_four_real_steps_load_source_once_reset_each_engine_and_keep_time_first_order(self):
        states, inputs, progress = [], [], []; original_engine = runner.PaperExecutionEngine
        def engine(*args, **kwargs):
            value = original_engine(*args, **kwargs); states.append((deepcopy(value.portfolio), deepcopy(value.strategy_state))); return value
        original_execute = rp.execute_research; original_write = rp._write_result
        def execute(dataset, *args, **kwargs):
            inputs.append(dataset); return original_execute(dataset, *args, **kwargs)
        def write(path, document):
            progress.append(deepcopy(document)); return original_write(path, document)
        with (patch.object(rp, 'load_research_input', wraps=rp.load_research_input) as load,
              patch.object(runner, 'PaperExecutionEngine', side_effect=engine),
              patch.object(rp, 'execute_research', side_effect=execute), patch.object(rp, '_write_result', side_effect=write)):
            result = self.execute(result_path=self.result)
        self.assertEqual(1, load.call_count); self.assertEqual(4, len(states)); self.assertTrue(all(value == states[0] for value in states))
        self.assertEqual(['train/bucket-0', 'train/bucket-1', 'validation/bucket-0', 'validation/bucket-1'], [row['step_key'] for row in result['steps']])
        self.assertEqual(4, len(set(result['run_ids']))); self.assertEqual(('COMPLETED', 4), (result['batch_status'], result['attempted_now']))
        self.assertTrue(any(document['steps'][0]['state'] == 'RUNNING' and document['not_started_count'] == 3 for document in progress))
        for dataset, row in zip(inputs, result['steps']):
            self.assertEqual(row['symbol_bucket'], dataset.manifest['development_partition']['spec']['symbol_partition']['bucket'])
            self.assertEqual('development_partition/v3', dataset.manifest['development_partition']['spec']['version'])
        self.assertEqual(inputs[0].observations, inputs[1].observations)
        self.assertEqual(json.loads(json.dumps(result)), json.loads(self.result.read_text()))

    def test_group_comparisons_preserve_all_requested_counts_and_never_merge_cash(self):
        result = self.execute()
        self.assertEqual('per_symbol_bucket_identified_runs/v1', result['comparison_scope'])
        self.assertIsNone(result['comparison'])
        self.assertEqual([0, 1], [group['symbol_bucket'] for group in result['group_comparisons']])
        for group in result['group_comparisons']:
            self.assertEqual((2, 2, 0), (group['requested_step_count'], group['completed_step_count'], group['not_started_count']))
            self.assertEqual(1, len(group['comparison']['condition_keys']))
            self.assertEqual(group['run_ids'], [row['run_id'] for row in group['comparison']['partitions']])
        empty = result['group_comparisons'][1]['comparison']
        self.assertEqual(['NO_CLOSED_TRADE', 'NO_CLOSED_TRADE'], [row['status'] for row in empty['partitions']])
        self.assertEqual(0, empty['eligible_partition_count'])

    def test_completed_cache_reuses_all_steps_without_engine_and_preserves_reports(self):
        first = self.execute(); repo = ResearchRepository(self.batch.request.database)
        reports = [repo.load_research_report(run_id) for run_id in first['run_ids']]
        with patch.object(rp, 'execute_research', side_effect=AssertionError('cache must not run')): second = self.execute()
        self.assertEqual((0, 4, first['run_ids']), (second['attempted_now'], second['cached_count'], second['run_ids']))
        self.assertEqual(reports, [repo.load_research_report(run_id) for run_id in second['run_ids']])

    def test_additional_bucket_preserves_existing_scientific_ids_and_cached_steps(self):
        first = self.execute(); expanded = replace(self.batch, symbol_partitions=self.batch.symbol_partitions + (replace(self.batch.symbol_partitions[0], bucket=2),))
        second = self.execute(expanded)
        self.assertEqual((2, 4), (second['attempted_now'], second['cached_count']))
        self.assertEqual(first['run_ids'], [row['run_id'] for row in second['steps'] if row['symbol_bucket'] in (0, 1)])

    def test_cancel_between_groups_retains_completed_and_resume_only_remaining(self):
        original = rp.execute_research
        def execute(*args, **kwargs):
            value = original(*args, **kwargs); self.cancel.write_text('cancel'); return value
        with patch.object(rp, 'execute_research', side_effect=execute): first = self.execute(result_path=self.result)
        self.assertEqual(('cancelled', 3), (first['status'], first['not_started_count']))
        self.assertEqual(2, first['group_comparisons'][0]['requested_step_count'])
        self.assertEqual(1, len(first['group_comparisons'][0]['run_ids']))
        self.assertIsNone(first['group_comparisons'][1]['comparison'])
        self.cancel.unlink(); second = self.execute()
        self.assertEqual((3, 1, 'COMPLETED'), (second['attempted_now'], second['cached_count'], second['batch_status']))

    def test_cancel_after_claim_cleans_only_owned_scientific_run_and_resume(self):
        def execute(*args, **kwargs):
            self.cancel.write_text('cancel'); raise rp.ResearchRunCancelled('fixture')
        with patch.object(rp, 'execute_research', side_effect=execute): first = self.execute()
        repo = ResearchRepository(self.batch.request.database)
        self.assertEqual('cancelled', repo.load_run(first['run_ids'][0])['status'])
        self.cancel.unlink(); self.assertEqual(4, self.execute()['attempted_now'])

    def test_budget_between_groups_preserves_pending_and_resume_cached_progress(self):
        now = [0.0]; original = rp.execute_research
        def execute(*args, **kwargs):
            value = original(*args, **kwargs); now[0] = 61.0; return value
        with patch.object(rp.time, 'monotonic', side_effect=lambda: now[0]), patch.object(rp, 'execute_research', side_effect=execute): first = self.execute()
        self.assertEqual(('ok', 'PARTIAL', 'validation_time_budget_exhausted', 3), (first['status'], first['batch_status'], first['reason'], first['not_started_count']))
        second = self.execute()
        self.assertEqual((3, 1), (second['attempted_now'], second['cached_count']))

    def test_resource_block_early_preserves_all_groups_and_never_creates_db(self):
        with patch.object(rp, 'research_input_encoded_bytes', side_effect=rp.ResearchResourceBlocked('cap')):
            first = self.execute(result_path=self.result)
        self.assertEqual(('resource_blocked', 4, []), (first['status'], first['not_started_count'], first['run_ids']))
        self.assertTrue(all(group['comparison'] is None for group in first['group_comparisons']))
        self.assertFalse(self.batch.request.database.exists())

    def test_failure_in_one_group_continues_other_steps_without_automatic_retry(self):
        original = rp.execute_research; calls = [0]
        def execute(*args, **kwargs):
            calls[0] += 1
            if calls[0] == 1: raise RuntimeError('fixture failure')
            return original(*args, **kwargs)
        with patch.object(rp, 'execute_research', side_effect=execute): first = self.execute()
        self.assertEqual(['FAILED', 'COMPLETED', 'COMPLETED', 'COMPLETED'], [row['state'] for row in first['steps']])
        self.assertEqual('development_validation_failures_require_review', first['reason'])
        with patch.object(rp, 'execute_research', side_effect=AssertionError('no failed retry')): second = self.execute()
        self.assertEqual((0, 3), (second['attempted_now'], second['cached_count']))

    def test_missing_completed_report_is_cache_invalid_in_its_group_only(self):
        first = self.execute(); repo = ResearchRepository(self.batch.request.database)
        with closing(sqlite3.connect(repo.path)) as conn, conn:
            conn.execute('DELETE FROM research_reports WHERE run_id=?', (first['run_ids'][0],))
        second = self.execute()
        self.assertEqual('CACHE_INVALID', second['steps'][0]['state'])
        self.assertEqual(3, second['cached_count'])
        self.assertEqual('INVALID', second['group_comparisons'][0]['comparison']['partitions'][0]['status'])
        self.assertEqual('completed', repo.load_run(first['run_ids'][0])['status'])

    def test_busy_target_does_not_block_other_group_or_mutate_its_owner(self):
        policy = DevelopmentPartitionSpec('development_partition/v3', 'train', symbol_partition=self.batch.symbol_partitions[0])
        selected = prepare_development_partition(self.fixture.source, policy, self.batch.request.evaluation)
        run_id, spec = runner.research_run_identity(selected, self.batch.request.strategy, self.batch.request.execution,
            policy.evaluation_for(self.batch.request.evaluation), session_profile=self.batch.request.session_profile,
            execution_scope='independent_development_validation/v1')
        repo = ResearchRepository(self.batch.request.database); repo.start_run(run_id, spec, selected.manifest, claim_independent=True)
        result = self.execute()
        self.assertEqual(('BUSY', 3), (result['steps'][0]['state'], result['attempted_now']))
        self.assertEqual('running', repo.load_run(run_id)['status'])

    def test_oos_canary_and_global_source_change_do_not_reexecute_any_group(self):
        first = self.execute(); changed = deepcopy(self.fixture.source)
        changed.manifest.update(dataset_id='changed-final', revision_ids_hash='changed-global', quality_summary={'final': 'missing'})
        for row in changed.observations:
            if datetime.fromisoformat(row['available_at']) >= datetime.fromisoformat(self.fixture.fixture.at(22)):
                row['payload'] = {'final_canary': 10**30}; row['revision_id'] = 'future-'+row['revision_id']
        batch = replace(self.batch, dataset_id='changed-final', dataset_hash='changed-global')
        with patch.object(rp, 'load_research_input', return_value=changed), patch.object(rp, 'execute_research', side_effect=AssertionError('no OOS rerun')):
            second = self.execute(batch)
        self.assertEqual(first['run_ids'], second['run_ids']); self.assertEqual(first['group_comparisons'], second['group_comparisons'])

    def test_changed_shared_peer_context_reexecutes_affected_window_in_all_groups_only(self):
        first = self.execute(); changed = deepcopy(self.fixture.source)
        row = next(row for row in changed.observations if row['kind'] == 'minute_bar' and row['payload']['bar_start'] == self.fixture.fixture.at(5))
        row['payload']['close'] += 1
        with patch.object(rp, 'load_research_input', return_value=changed): second = self.execute()
        self.assertEqual((2, 2), (second['attempted_now'], second['cached_count']))
        self.assertNotEqual(first['run_ids'][:2], second['run_ids'][:2]); self.assertEqual(first['run_ids'][2:], second['run_ids'][2:])

    def test_wrong_source_binding_and_code_drift_are_not_cached_or_claimed(self):
        with self.assertRaises(ValueError): self.execute(replace(self.batch, dataset_hash='wrong'))
        self.assertFalse(self.batch.request.database.exists())
        original = rp.research_run_identity
        def identity(*args, **kwargs):
            run_id, spec = original(*args, **kwargs); spec['code_hash'] = 'drift'; return run_id, spec
        with patch.object(rp, 'research_run_identity', side_effect=identity): result = self.execute()
        self.assertEqual((0, []), (result['attempted_now'], result['run_ids']))
        self.assertEqual(['FAILED']*4, [row['state'] for row in result['steps']])

    def test_cli_success_cancel_resource_and_invalid_output_paths(self):
        code, result = self.cli(); self.assertEqual((0, 4), (code, result['requested_step_count']))
        self.cancel.write_text('cancel'); code, result = self.cli(); self.assertEqual((2, 4), (code, result['not_started_count']))
        self.cancel.unlink()
        with patch.object(rp, 'research_input_encoded_bytes', side_effect=rp.ResearchResourceBlocked('cap')):
            code, result = self.cli()
        self.assertEqual((3, 'RESOURCE_BLOCKED'), (code, result['batch_status']))
        original = self.path.read_bytes()
        with patch('sys.stderr', new=StringIO()), self.assertRaises(SystemExit) as exc:
            rp.main(['--validate-partitions', str(self.path), '--result', str(self.path)])
        self.assertEqual(2, exc.exception.code); self.assertEqual(original, self.path.read_bytes())

    def test_legacy_v1_request_result_and_two_fold_minimum_remain_unchanged(self):
        batch = self.fixture.batch
        self.assertNotIn('symbol_partition', batch.to_dict()); self.assertEqual('independent_development_validation/v1', batch.version)
        result = self.execute(batch)
        self.assertNotIn('group_comparisons', result); self.assertNotIn('version', result)
        self.assertIsNotNone(result['comparison']); self.assertEqual(2, len(result['steps']))
        document = deepcopy(self.fixture.document); document['fold_names'] = ['train']; self.write(document)
        with self.assertRaises(ValueError): rp.load_development_validation_request(self.path)

    def test_ui_accepts_v2_without_gui_database_or_input_read(self):
        app = self.app
        dialog = DevelopmentValidationDialog(self.root / 'state'); dialog._request_path.setText(str(self.path))
        try:
            with (patch.object(dialog._manager, 'start') as launch,
                  patch.object(rp, 'load_research_input', side_effect=AssertionError('GUI input read')),
                  patch.object(rp.ResearchRepository, '__init__', side_effect=AssertionError('GUI DB read'))):
                dialog._start()
            launch.assert_called_once()
            self.assertIsNotNone(dialog._operation)
            self.assertEqual(self.batch, rp.load_development_validation_request(dialog._operation['request']))
            self.assertFalse(self.batch.request.database.exists())
        finally:
            dialog.stop(); dialog.deleteLater(); QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete); app.processEvents()


if __name__ == '__main__':
    unittest.main()
