from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from io import StringIO
import json
from pathlib import Path
import sqlite3
from threading import Barrier
import unittest
from unittest.mock import patch

import test_research_development_partitions as fixtures
from kiwoom_monitor import research_process as rp
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
from kiwoom_monitor.infrastructure.research_data_source import load_research_input, prepare_development_partition
from scripts import run_research as runner


class DevelopmentValidationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.DevelopmentPartitionTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        original = self.fixture.write_request()
        base = json.loads(original.read_text())
        base.pop('development_partition')
        base['resource_budget'] = {'cpu_duty_percent': 100, 'memory_mb': 512}
        self.source = load_research_input(self.root / base['dataset'], session_profile=fixtures.PROFILE)
        self.document = {'version': 'independent_development_validation/v1', 'request': base,
                         'dataset_id': self.source.manifest['dataset_id'],
                         'dataset_hash': self.source.manifest['revision_ids_hash'],
                         'fold_names': ['train', 'validation'], 'max_seconds': 60}
        self.path = self.root / 'validation.json'
        self.write()
        self.batch = rp.load_development_validation_request(self.path)
        self.cancel, self.result = self.root / 'cancel', self.root / 'progress.json'

    def write(self, document=None):
        self.path.write_text(json.dumps(document or self.document), encoding='utf-8')

    def execute(self, **kwargs):
        return rp.execute_development_validation(self.batch, cancel_path=self.cancel, **kwargs)

    def cli(self):
        with patch('sys.stdout', new=StringIO()):
            code = rp.main(['--validate-partitions', str(self.path), '--result', str(self.result), '--cancel', str(self.cancel)])
        return code, json.loads(self.result.read_text(encoding='utf-8'))

    def test_parser_rejects_final_exposed_duplicate_reverse_missing_and_nonfixed_search(self):
        for names in (['train', 'final'], ['train', 'train'], ['validation', 'train'], ['train', 'missing'], ['train']):
            with self.subTest(names=names):
                self.write({**self.document, 'fold_names': names})
                with self.assertRaises(ValueError): rp.load_development_validation_request(self.path)
        for change in ('partition', 'mode', 'profile', 'exposed'):
            document = deepcopy(self.document)
            if change == 'partition': document['request']['development_partition'] = self.fixture.partition.to_dict()
            elif change == 'mode': document['request']['mode'] = 'rank_comparison'
            elif change == 'profile': document['request'].pop('session_profile')
            else: document['request']['evaluation'].update(final_holdout_accessed_at=self.fixture.at(31), final_holdout_access_reason='viewed')
            self.write(document)
            with self.assertRaises(ValueError): rp.load_development_validation_request(self.path)
        self.assertFalse(self.batch.request.database.exists())

    def test_parser_rejects_extra_version_large_file_and_invalid_budget_or_identity(self):
        for key, value in [('version', 'v2'), ('extra', True), ('max_seconds', 0), ('max_seconds', True),
                           ('max_seconds', 3601), ('dataset_id', ''), ('dataset_hash', None)]:
            self.write({**self.document, key: value})
            with self.assertRaises(ValueError): rp.load_development_validation_request(self.path)
        self.path.write_bytes(b' ' * (1024 * 1024 + 1))
        with self.assertRaises(ValueError): rp.load_development_validation_request(self.path)

    def test_two_real_partitions_load_once_reset_state_and_project_before_engine(self):
        initial, datasets, progress = [], [], []
        original_engine = runner.PaperExecutionEngine
        class RecordingEngine(original_engine):
            def __init__(engine, *args, **kwargs):
                super().__init__(*args, **kwargs)
                initial.append((deepcopy(engine.portfolio), deepcopy(engine.strategy_state)))
        original_execute = rp.execute_research
        def execute(dataset, *args, **kwargs):
            datasets.append(dataset); return original_execute(dataset, *args, **kwargs)
        original_write = rp._write_result
        def write(path, document):
            progress.append([row['state'] for row in document['steps']]); return original_write(path, document)
        with (patch.object(rp, 'load_research_input', wraps=rp.load_research_input) as loader,
              patch.object(rp, 'execute_research', side_effect=execute), patch.object(runner, 'PaperExecutionEngine', RecordingEngine),
              patch.object(rp, '_write_result', side_effect=write)):
            result = self.execute(result_path=self.result)
        self.assertEqual(1, loader.call_count)
        self.assertIn(['RUNNING', 'NOT_STARTED'], progress)
        self.assertEqual(['COMPLETED', 'COMPLETED'], progress[-1])
        self.assertEqual(('COMPLETED', 2, 0), (result['batch_status'], result['attempted_now'], result['cached_count']))
        self.assertEqual(2, len(initial)); self.assertEqual(initial[0], initial[1])
        self.assertIsNone(initial[1][0].position); self.assertIsNone(initial[1][0].pending_order)
        self.assertEqual(['TRAIN', 'VALIDATION'], [row['role'] for row in result['steps']])
        self.assertEqual(1, len(result['comparison']['condition_keys']))
        for dataset, step in zip(datasets, result['steps']):
            self.assertTrue(all(datetime.fromisoformat(row['available_at']) < datetime.fromisoformat(step['end']) for row in dataset.observations))
            self.assertNotIn('children', dataset.manifest)
        self.assertEqual(json.loads(json.dumps(result)), json.loads(self.result.read_text()))

    def test_repeated_batch_uses_completed_cache_and_does_not_construct_engine(self):
        first = self.execute()
        repo = ResearchRepository(self.batch.request.database)
        before = [repo.load_research_report(run_id) for run_id in first['run_ids']]
        with patch.object(rp, 'execute_research', side_effect=AssertionError('completed cache may not execute')):
            second = self.execute()
        self.assertEqual((0, 2, first['run_ids']), (second['attempted_now'], second['cached_count'], second['run_ids']))
        self.assertEqual(before, [repo.load_research_report(run_id) for run_id in second['run_ids']])

    def test_cancel_between_folds_preserves_completed_run_and_resume_executes_only_remaining(self):
        original = rp.execute_research
        def execute(*args, **kwargs):
            result = original(*args, **kwargs); self.cancel.write_text('cancel'); return result
        with patch.object(rp, 'execute_research', side_effect=execute):
            first = self.execute(result_path=self.result)
        self.assertEqual(('cancelled', 'COMPLETED', 'NOT_STARTED'), (first['status'], first['steps'][0]['state'], first['steps'][1]['state']))
        self.assertEqual('cancelled', json.loads(self.result.read_text())['status'])
        self.cancel.unlink()
        with patch.object(rp, 'execute_research', wraps=original) as execute:
            second = self.execute()
        self.assertEqual(1, execute.call_count)
        self.assertEqual(('COMPLETED', 1, 1), (second['batch_status'], second['attempted_now'], second['cached_count']))
        self.assertEqual(first['run_ids'][0], second['run_ids'][0])

    def test_cancel_after_claim_before_runner_start_marks_owned_run_cancelled_and_is_resumable(self):
        def execute(*args, **kwargs):
            self.cancel.write_text('cancel'); raise rp.ResearchRunCancelled('cancel before runner')
        with patch.object(rp, 'execute_research', side_effect=execute): first = self.execute()
        repo = ResearchRepository(self.batch.request.database)
        self.assertEqual('cancelled', repo.load_run(first['run_ids'][0])['status'])
        self.cancel.unlink()
        self.assertEqual('COMPLETED', self.execute()['batch_status'])

    def test_scoped_runner_hands_cancel_to_batch_before_exposing_run_as_reclaimable(self):
        started = [False]
        engine, checkpoint, execute = runner.PaperExecutionEngine, rp.ResearchResourceGuard.checkpoint, rp.execute_research
        class StartedEngine(engine):
            def __init__(instance, *args, **kwargs):
                super().__init__(*args, **kwargs); started[0] = True
        def cancel_inside(guard):
            if started[0]:
                self.cancel.write_text('cancel'); raise rp.ResearchRunCancelled('inside owned runner')
            return checkpoint(guard)
        def owned(*args, **kwargs):
            try:
                return execute(*args, **kwargs)
            except rp.ResearchRunCancelled:
                repo = args[1]
                _, run_id, _ = self.prepared_identity()
                self.assertEqual('running', repo.load_run(run_id)['status'], 'only batch should expose terminal cancellation')
                raise
        with (patch.object(runner, 'PaperExecutionEngine', StartedEngine),
              patch.object(rp.ResearchResourceGuard, 'checkpoint', new=cancel_inside),
              patch.object(rp, 'execute_research', side_effect=owned)):
            result = self.execute()
        self.assertEqual('cancelled', ResearchRepository(self.batch.request.database).load_run(result['run_ids'][0])['status'])

    def test_time_budget_between_folds_and_during_load_preserves_partial_scope(self):
        clock = [0.0]
        original = rp.execute_research
        def execute(*args, **kwargs):
            result = original(*args, **kwargs); clock[0] = 100.0; return result
        with patch.object(rp.time, 'monotonic', side_effect=lambda: clock[0]), patch.object(rp, 'execute_research', side_effect=execute):
            result = self.execute()
        self.assertEqual(('ok', 'PARTIAL', 1, 1), (result['status'], result['batch_status'], result['attempted_now'], result['not_started_count']))
        self.assertIn('budget', result['reason'])
        self.assertEqual(1, result['comparison']['requested_count'])
        # A fresh output path must not be created when loading alone consumes the budget.
        batch = replace(self.batch, request=replace(self.batch.request, database=self.root / 'load-only.sqlite3'))
        clock[0] = 0
        def load(*args, **kwargs): clock[0] = 100; return self.source
        with patch.object(rp.time, 'monotonic', side_effect=lambda: clock[0]), patch.object(rp, 'load_research_input', side_effect=load):
            result = rp.execute_development_validation(batch)
        self.assertEqual(('ok', 'PARTIAL', 2), (result['status'], result['batch_status'], result['not_started_count']))
        self.assertFalse(batch.request.database.exists())

    def test_time_budget_inside_claimed_fold_cancels_run_without_terminal_scientific_failure(self):
        clock = [0.0]; original = rp.execute_research
        def execute(*args, **kwargs): clock[0] = 100; return original(*args, **kwargs)
        with patch.object(rp.time, 'monotonic', side_effect=lambda: clock[0]), patch.object(rp, 'execute_research', side_effect=execute):
            first = self.execute()
        self.assertEqual('BUDGET_EXHAUSTED', first['steps'][0]['state'])
        self.assertEqual('cancelled', ResearchRepository(self.batch.request.database).load_run(first['run_ids'][0])['status'])
        self.assertEqual('COMPLETED', self.execute()['batch_status'])

    def test_resource_block_before_load_and_after_claim_does_not_poison_scientific_run(self):
        with patch.object(rp.ResearchResourceGuard, 'preflight', side_effect=rp.ResearchResourceBlocked('memory fixture')):
            result = self.execute()
        self.assertEqual('resource_blocked', result['status']); self.assertFalse(self.batch.request.database.exists())
        with patch.object(rp, 'execute_research', side_effect=rp.ResearchResourceBlocked('rss fixture')):
            result = self.execute()
        self.assertEqual('RESOURCE_BLOCKED', result['steps'][0]['state'])
        repo = ResearchRepository(self.batch.request.database)
        self.assertEqual('cancelled', repo.load_run(result['run_ids'][0])['status'])

    def test_source_binding_mismatch_is_rejected_before_database_creation(self):
        with self.assertRaisesRegex(ValueError, 'locked source'):
            rp.execute_development_validation(replace(self.batch, dataset_id='wrong-source'))
        self.assertFalse(self.batch.request.database.exists())

    def test_code_drift_is_rejected_before_claim_or_engine_and_preserves_locked_hash(self):
        original = rp.research_run_identity
        def identity(*args, **kwargs):
            run_id, spec = original(*args, **kwargs); spec['code_hash'] = 'drifted-code'; return run_id, spec
        with patch.object(rp, 'research_run_identity', side_effect=identity), patch.object(rp, 'execute_research', side_effect=AssertionError('drift may not execute')):
            result = self.execute()
        self.assertEqual((0, ['FAILED', 'FAILED'], []), (result['attempted_now'], [row['state'] for row in result['steps']], result['run_ids']))
        self.assertNotEqual('drifted-code', result['implementation_hash'])

    def test_final_canary_global_identity_changes_do_not_reexecute_development_evidence(self):
        first = self.execute()
        changed = deepcopy(self.source)
        changed.manifest.update(dataset_id='changed-final', revision_ids_hash='changed-global', quality_summary={'final': 'missing'})
        for row in changed.observations:
            if datetime.fromisoformat(row['available_at']) >= datetime.fromisoformat(self.fixture.at(22)):
                row['payload'] = {'final_canary': -10**30}; row['revision_id'] = 'final-' + row['revision_id']
        batch = replace(self.batch, dataset_id='changed-final', dataset_hash='changed-global')
        with patch.object(rp, 'load_research_input', return_value=changed), patch.object(rp, 'execute_research', side_effect=AssertionError('final may not alter evidence')):
            second = rp.execute_development_validation(batch)
        self.assertEqual((2, first['run_ids'], first['comparison']), (second['cached_count'], second['run_ids'], second['comparison']))

    def test_changed_selected_payload_reexecutes_only_affected_partition(self):
        first = self.execute(); changed = deepcopy(self.source)
        bar = next(row for row in changed.observations if row['kind'] == 'minute_bar' and datetime.fromisoformat(row['payload']['bar_start']) == datetime.fromisoformat(self.fixture.at(5)))
        bar['payload']['close'] += 1
        with patch.object(rp, 'load_research_input', return_value=changed): second = self.execute()
        self.assertEqual((1, 1), (second['attempted_now'], second['cached_count']))
        self.assertNotEqual(first['run_ids'][0], second['run_ids'][0])
        self.assertEqual(first['run_ids'][1], second['run_ids'][1])

    def test_failure_is_preserved_and_other_fold_continues_without_automatic_failed_retry(self):
        original = rp.execute_research; calls = [0]
        def execute(*args, **kwargs):
            calls[0] += 1
            if calls[0] == 1: raise RuntimeError('fixture failure')
            return original(*args, **kwargs)
        with patch.object(rp, 'execute_research', side_effect=execute): first = self.execute()
        self.assertEqual(['FAILED', 'COMPLETED'], [row['state'] for row in first['steps']])
        self.assertEqual('INCOMPLETE', first['comparison']['status'])
        self.assertEqual('development_validation_failures_require_review', first['reason'])
        with patch.object(rp, 'execute_research', side_effect=AssertionError('no retry of failed run')): second = self.execute()
        self.assertEqual((0, 1, 'FAILED'), (second['attempted_now'], second['cached_count'], second['steps'][0]['state']))

    def test_completed_missing_report_is_cache_invalid_without_rewriting_completed_run(self):
        first = self.execute(); repo = ResearchRepository(self.batch.request.database)
        with closing(sqlite3.connect(repo.path)) as conn, conn:
            conn.execute('DELETE FROM research_reports WHERE run_id=?', (first['run_ids'][0],))
        with patch.object(rp, 'execute_research', side_effect=AssertionError('immutable completion cannot be replaced')): second = self.execute()
        self.assertEqual('CACHE_INVALID', second['steps'][0]['state'])
        self.assertEqual('completed', repo.load_run(first['run_ids'][0])['status'])

    def prepared_identity(self):
        selected = prepare_development_partition(self.source, self.fixture.partition, self.batch.request.evaluation)
        evaluation = self.fixture.partition.evaluation_for(self.batch.request.evaluation)
        run_id, spec = runner.research_run_identity(selected, self.batch.request.strategy, self.batch.request.execution, evaluation,
            session_profile=fixtures.PROFILE, execution_scope='independent_development_validation/v1')
        return selected, run_id, spec

    def test_atomic_claim_two_connections_allow_only_one_owner_and_cancelled_run_can_be_reclaimed(self):
        selected, run_id, spec = self.prepared_identity()
        repo = ResearchRepository(self.batch.request.database); barrier = Barrier(2)
        def claim():
            other = ResearchRepository(repo.path); barrier.wait()
            return other.start_run(run_id, spec, selected.manifest, claim_independent=True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: claim(), range(2)))
        self.assertEqual(['busy', 'claimed'], sorted(results))
        repo.cancel_run(run_id)
        self.assertEqual('claimed', repo.start_run(run_id, spec, selected.manifest, claim_independent=True))

    def test_busy_scoped_run_is_not_executed_or_marked_failed_and_legacy_ids_are_isolated(self):
        selected, run_id, spec = self.prepared_identity()
        repo = ResearchRepository(self.batch.request.database)
        repo.start_run(run_id, spec, selected.manifest, claim_independent=True)
        original = rp.execute_research
        with patch.object(rp, 'execute_research', wraps=original) as execute: result = self.execute()
        self.assertEqual((1, 'BUSY', 'running'), (execute.call_count, result['steps'][0]['state'], repo.load_run(run_id)['status']))
        self.assertEqual('existing_runs_require_owner_or_recovery_check', result['reason'])
        legacy_id, legacy_spec = runner.research_run_identity(selected, self.batch.request.strategy, self.batch.request.execution,
            self.fixture.partition.evaluation_for(self.batch.request.evaluation), session_profile=fixtures.PROFILE)
        self.assertNotEqual(legacy_id, run_id); self.assertNotIn('execution_scope', legacy_spec)
        with self.assertRaises(ValueError): repo.start_run(legacy_id, legacy_spec, selected.manifest, claim_independent=True)

    def test_cli_progress_success_cancellation_and_resource_exit_codes(self):
        code, result = self.cli()
        self.assertEqual((0, 'COMPLETED'), (code, result['batch_status']))
        self.cancel.write_text('cancel'); code, result = self.cli()
        self.assertEqual((2, 'cancelled', 0), (code, result['status'], result['attempted_now']))
        self.cancel.unlink()
        with patch.object(rp.ResearchResourceGuard, 'preflight', side_effect=rp.ResearchResourceBlocked('memory fixture')):
            code, result = self.cli()
        self.assertEqual((3, 'resource_blocked'), (code, result['status']))

    def test_cli_output_collisions_and_mode_mixing_preserve_source_and_database(self):
        original = self.path.read_bytes()
        for output in (self.path, self.batch.request.database, self.batch.request.dataset / 'output.json'):
            with patch('sys.stderr', new=StringIO()), self.assertRaises(SystemExit):
                rp.main(['--validate-partitions', str(self.path), '--result', str(output)])
        for extra in (['--compare-runs', str(self.path)], ['--register-campaign', 'c'], ['--worker-generation', '1']):
            with patch('sys.stderr', new=StringIO()), self.assertRaises(SystemExit):
                rp.main(['--validate-partitions', str(self.path), '--result', str(self.result), *extra])
        self.assertEqual(original, self.path.read_bytes()); self.assertFalse(self.batch.request.database.exists())
