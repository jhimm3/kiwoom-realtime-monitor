from contextlib import closing
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
import sqlite3
import unittest
from unittest.mock import patch

import test_research_development_validation as fixtures
from kiwoom_monitor import research_process as rp
from kiwoom_monitor.application.research_splits import FinalHoldoutBatchSpec
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
from kiwoom_monitor.infrastructure.research_data_source import (
    FINAL_INPUT_VERSION, FrozenResearchDataset, development_partition_start,
    prepare_development_partition, prepare_final_holdout_partition,
)


class FinalPreparationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.DevelopmentValidationTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.source = self.fixture.root, self.fixture.source
        original = self.fixture.batch.request
        self.evaluation = replace(original.evaluation, folds=(original.evaluation.folds[-1],))
        self.request = replace(original, evaluation=self.evaluation)
        self.code_hash = rp.research_implementation_hash(self.request.session_profile)
        self.batch = self.make_batch((self.request,))
        self.at = '2026-09-15T00:00:00+00:00'

    def make_batch(self, candidates):
        hashes = tuple(sorted(rp.final_candidate_spec_hash(request, self.code_hash) for request in candidates))
        return FinalHoldoutBatchSpec('final_holdout_batch/v1', self.source.manifest['dataset_id'],
            self.source.manifest['revision_ids_hash'], self.request.session_profile, hashes, self.evaluation)

    def prepare(self, batch=None, candidates=None, **kwargs):
        return rp.prepare_final_holdout_evaluation(batch or self.batch, candidates or (self.request,),
            request_id='prepare', accessed_at=self.at, **kwargs)

    def repo(self):
        return ResearchRepository(self.request.database)

    def insert_history(self, evaluation=None, manifest=None):
        spec = {'evaluation_spec': evaluation.to_dict()} if evaluation is not None else {}
        self.repo().start_run('previous', spec, manifest if manifest is not None else self.source.manifest)

    def test_real_verified_source_once_access_before_handoff_without_engine_or_runs(self):
        with (patch.object(rp, 'load_research_input', wraps=rp.load_research_input) as loader,
              patch.object(rp, 'execute_research', side_effect=AssertionError('no engine'))):
            result = self.prepare()
        self.assertEqual(1, loader.call_count)
        self.assertEqual(FINAL_INPUT_VERSION, result.dataset.manifest['runtime_input_version'])
        self.assertEqual(self.batch.to_dict()['evaluation'], result.dataset.manifest['final_holdout_partition']['spec']['evaluation'])
        self.assertNotIn('dataset_hash', result.dataset.manifest['final_holdout_partition']['spec'])
        self.assertEqual(self.code_hash, result.implementation_hash)
        self.assertEqual(['FINAL_ACCESS'], [event['event_type'] for event in self.repo().load_final_holdout_events(self.batch.window_id)])
        with closing(sqlite3.connect(self.request.database)) as connection:
            self.assertEqual(0, connection.execute('SELECT COUNT(*) FROM research_runs').fetchone()[0])
        self.assertFalse(self.request.runs_dir.exists())

    def test_hash_excludes_operating_paths_budget_dates_and_binds_strategy_costs_code(self):
        changed = replace(self.request, dataset=self.root/'other', database=self.root/'other.db',
            runs_dir=self.root/'other-runs', resource_limits=replace(self.request.resource_limits, memory_mb=1024),
            evaluation=self.fixture.batch.request.evaluation)
        expected = rp.final_candidate_spec_hash(self.request, self.code_hash)
        self.assertEqual(expected, rp.final_candidate_spec_hash(changed, self.code_hash))
        for request, code in ((replace(self.request, strategy=replace(self.request.strategy, buffer_bps=self.request.strategy.buffer_bps+1)), self.code_hash),
                             (replace(self.request, execution=replace(self.request.execution, cost_model=replace(self.request.execution.cost_model, slippage_bps=12))), self.code_hash),
                             (self.request, 'a'*64)):
            self.assertNotEqual(expected, rp.final_candidate_spec_hash(request, code))

    def test_multiple_candidates_sorted_and_no_missing_extra_duplicate_allowed(self):
        other = replace(self.request, strategy=replace(self.request.strategy, buffer_bps=self.request.strategy.buffer_bps+1))
        batch = self.make_batch((self.request, other))
        with patch.object(rp, 'load_research_input', side_effect=AssertionError('invalid set cannot load')):
            for candidates in ((self.request,), (self.request,self.request), (self.request,other,self.request)):
                with self.assertRaises(ValueError): self.prepare(batch, candidates)
        result = self.prepare(batch, (other,self.request))
        self.assertEqual(batch.candidate_spec_hashes, tuple(rp.final_candidate_spec_hash(r, self.code_hash) for r in result.candidates))

    def test_invalid_candidate_rejected_before_loading_and_db_creation(self):
        for changes in ({'mode':'rank_comparison'}, {'family':'unknown'}, {'session_profile':None},
                        {'development_partition':self.fixture.fixture.partition},
                        {'execution':replace(self.request.execution, cost_model=None)}):
            with self.subTest(changes=changes), patch.object(rp, 'load_research_input', side_effect=AssertionError('no load')):
                with self.assertRaises(ValueError): self.prepare(candidates=(replace(self.request, **changes),))
        self.assertFalse(self.request.database.exists())

    def test_wrong_policy_or_paths_rejected_before_loading(self):
        for changed in (replace(self.request, evaluation=self.fixture.batch.request.evaluation),
                        replace(self.request, database=self.request.dataset/'state.db')):
            with patch.object(rp, 'load_research_input', side_effect=AssertionError('no load')):
                with self.assertRaises(ValueError): self.prepare(candidates=(changed,))
        self.assertFalse(self.request.database.exists())

    def test_wrong_source_identity_and_corrupt_bytes_create_no_ledger(self):
        with self.assertRaises(ValueError): self.prepare(replace(self.batch, dataset_hash='a'*64))
        with patch.object(rp, 'load_research_input', side_effect=ValueError('corrupt export')):
            with self.assertRaises(ValueError): self.prepare()
        self.assertFalse(self.request.database.exists())

    def test_projection_bounds_clone_and_omit_future_quality_metadata(self):
        projected = self.prepare().dataset
        descriptor = projected.manifest['final_holdout_partition']
        lower, end = datetime.fromisoformat(descriptor['warmup_start']), datetime.fromisoformat(descriptor['end'])
        for row in projected.observations:
            available = datetime.fromisoformat(row['available_at'])
            self.assertTrue(lower <= available < end or row['kind'] == 'top20_membership')
            self.assertIn(row, self.source.observations)
        self.assertNotEqual(self.source.manifest['dataset_id'], projected.manifest['dataset_id'])
        for key in ('children','watermark','bundle_id'):
            self.assertNotIn(key, projected.manifest)
        self.assertEqual('unknown', projected.manifest['quality_summary']['recording_gap'])
        before = deepcopy(projected.observations)
        self.source.observations[-1]['payload']['canary'] = 'later mutation'
        self.assertEqual(before, projected.observations)

    def test_generic_execution_and_development_recycling_rejected_before_run(self):
        projected = self.prepare().dataset
        repo = self.repo()
        with patch.object(repo, 'start_run', side_effect=AssertionError('must reject before run')):
            with self.assertRaisesRegex(ValueError, 'gated final evaluator'):
                rp.execute_research(projected, repo, self.request.runs_dir, self.request.strategy,
                    self.request.execution, self.evaluation, session_profile=self.request.session_profile)
        with self.assertRaisesRegex(ValueError, 'gated final evaluator'):
            development_partition_start(projected, self.evaluation)
        with self.assertRaisesRegex(ValueError, 'recycled'):
            prepare_development_partition(projected, self.fixture.fixture.partition, self.fixture.batch.request.evaluation)
        untagged = FrozenResearchDataset({**projected.manifest, 'runtime_input_version':None}, projected.observations)
        with self.assertRaisesRegex(ValueError, 'gated final evaluator'):
            development_partition_start(untagged, self.evaluation)

    def test_projected_source_cannot_be_used_as_full_source(self):
        projected = self.prepare().dataset
        forged = FrozenResearchDataset({**projected.manifest, 'dataset_id':self.batch.dataset_id,
            'revision_ids_hash':self.batch.dataset_hash}, projected.observations)
        with self.assertRaisesRegex(ValueError, 'full frozen source'):
            prepare_final_holdout_partition(forged, self.batch)

    def test_legacy_full_oos_history_blocked_without_access_event(self):
        self.insert_history(self.fixture.batch.request.evaluation)
        with self.assertRaisesRegex(ValueError, 'already used'): self.prepare()
        self.assertIsNone(self.repo().load_final_holdout_window(self.batch.window_id))

    def test_overlapping_train_and_validation_including_warmup_are_blocked(self):
        self.repo()
        for role in ('TRAIN','VALIDATION'):
            with self.subTest(role=role):
                evaluation = replace(self.evaluation, folds=(replace(self.evaluation.folds[0], role=role,
                    start=self.fixture.fixture.at(30), end=self.fixture.fixture.at(32)),))
                with closing(sqlite3.connect(self.request.database)) as connection, connection:
                    connection.execute('DELETE FROM research_runs')
                self.insert_history(evaluation)
                with self.assertRaisesRegex(ValueError, 'including warmup'): self.prepare()

    def test_disjoint_development_history_allowed(self):
        full = self.fixture.batch.request.evaluation
        partition = replace(self.fixture.fixture.partition, fold_name='validation')
        projected = prepare_development_partition(self.source, partition, full)
        evaluation = partition.evaluation_for(full)
        self.insert_history(evaluation, projected.manifest)
        self.prepare()
        self.assertEqual(1,len(self.repo().load_final_holdout_events(self.batch.window_id)))

    def test_unknown_legacy_history_fails_closed(self):
        self.insert_history(manifest={})
        with self.assertRaisesRegex(ValueError, 'cannot prove'): self.prepare()
        self.assertIsNone(self.repo().load_final_holdout_window(self.batch.window_id))

    def test_legacy_captured_range_without_evaluation_is_checked(self):
        self.insert_history()
        with self.assertRaisesRegex(ValueError, 'already used'): self.prepare()

    def test_continuous_input_outside_reported_folds_is_still_exposed(self):
        full = self.fixture.batch.request.evaluation
        evaluation = replace(full, folds=full.folds[:1])
        self.insert_history(evaluation)
        with self.assertRaisesRegex(ValueError, 'already used'): self.prepare()
        self.assertIsNone(self.repo().load_final_holdout_window(self.batch.window_id))

    def test_continuous_input_between_reported_folds_is_still_exposed(self):
        full = self.fixture.batch.request.evaluation
        evaluation = replace(full, folds=full.folds[:2], warmup_seconds=0)
        self.insert_history(evaluation)
        final = replace(self.evaluation, folds=(replace(self.evaluation.folds[0],
            start='2026-09-14T09:10:30+09:00', end='2026-09-14T09:11:30+09:00'),))
        request = replace(self.request, evaluation=final)
        batch = replace(self.batch, evaluation=final)
        with self.assertRaisesRegex(ValueError, 'already used'): self.prepare(batch, (request,))

    def test_report_policy_alone_cannot_prove_actual_input_bounds(self):
        self.insert_history(self.evaluation, {})
        with self.assertRaisesRegex(ValueError, 'cannot prove'): self.prepare()

    def test_history_checked_inside_immediate_transaction_before_any_event(self):
        repo = self.repo()
        original = repo._connect
        statements = []
        def connect():
            connection = original()
            connection.set_trace_callback(statements.append)
            return connection
        with patch.object(repo, '_connect', side_effect=connect):
            repo.record_final_holdout_access(self.batch, request_id='trace', accessed_at=self.at,
                check_development_history=True)
        begin = next(i for i, sql in enumerate(statements) if sql == 'BEGIN IMMEDIATE')
        history = next(i for i, sql in enumerate(statements) if sql.startswith('SELECT r.run_id,r.spec_json'))
        event = next(i for i, sql in enumerate(statements) if sql.startswith('INSERT INTO research_final_holdout_events'))
        self.assertLess(begin, history); self.assertLess(history, event)

    def test_malformed_history_is_not_ignored(self):
        self.insert_history()
        with closing(sqlite3.connect(self.request.database)) as connection, connection:
            connection.execute("UPDATE research_runs SET spec_json='{}',input_manifest_json='invalid'")
        with self.assertRaisesRegex(ValueError, 'cannot prove'): self.prepare()
        self.assertEqual([],self.repo().load_final_holdout_events(self.batch.window_id))

    def test_history_check_callback_rolls_back_and_exact_retry_is_only_metadata(self):
        repo = self.repo()
        def cancelled(): raise RuntimeError('cancelled')
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            repo.record_final_holdout_access(self.batch, request_id='cancel', accessed_at=self.at,
                check_development_history=True, checkpoint=cancelled)
        self.assertIsNone(repo.load_final_holdout_window(self.batch.window_id))
        self.prepare(); self.prepare()
        self.assertEqual(1,len(repo.load_final_holdout_events(self.batch.window_id)))

    def test_code_drift_before_ledger_rejected(self):
        with patch.object(rp, 'research_implementation_hash', side_effect=[self.code_hash,'f'*64]):
            with self.assertRaisesRegex(ValueError, 'implementation changed'): self.prepare()
        self.assertFalse(self.request.database.exists())

    def test_unselected_canary_and_full_source_identity_do_not_change_projected_input(self):
        first = prepare_final_holdout_partition(self.source, self.batch)
        observations = deepcopy(self.source.observations)
        outside = next(row for row in observations if row['kind'] == 'minute_bar'
                       and datetime.fromisoformat(row['available_at']) < datetime.fromisoformat(self.batch.start))
        outside['payload']['canary'] = 'unselected training value'
        changed = FrozenResearchDataset({**self.source.manifest, 'dataset_id':'new-full-source',
            'revision_ids_hash':'a'*64}, observations, self.source.theme_snapshots)
        batch = replace(self.batch, dataset_id='new-full-source', dataset_hash='a'*64)
        second = prepare_final_holdout_partition(changed, batch)
        self.assertEqual(first, second)

    def test_timezone_alias_has_identical_final_projection_and_batch_identity(self):
        utc = FinalHoldoutBatchSpec.from_dict(self.batch.to_dict())
        self.assertEqual(self.batch.batch_id, utc.batch_id)
        self.assertEqual(prepare_final_holdout_partition(self.source, self.batch),
                         prepare_final_holdout_partition(self.source, utc))

    def test_every_candidate_requires_absolute_storage_paths(self):
        from pathlib import Path
        other = replace(self.request, strategy=replace(self.request.strategy, buffer_bps=self.request.strategy.buffer_bps+1),
                        database=Path('relative.db'))
        batch = self.make_batch((self.request,other))
        with patch.object(rp, 'load_research_input', side_effect=AssertionError('no load')):
            with self.assertRaisesRegex(ValueError, 'absolute storage paths'):
                self.prepare(batch, (self.request,other))

    def test_actual_corrupt_export_bytes_are_rejected_before_ledger(self):
        path = self.request.dataset/'observations.jsonl'
        self.assertTrue(path.exists())
        path.write_text(path.read_text(encoding='utf-8')+'{}\n', encoding='utf-8')
        with self.assertRaises(ValueError): self.prepare()
        self.assertFalse(self.request.database.exists())

    def test_cost_validity_must_cover_final_window(self):
        costs = replace(self.request.execution.cost_model, valid_to=self.evaluation.folds[0].start)
        request = replace(self.request, execution=replace(self.request.execution, cost_model=costs))
        with patch.object(rp, 'load_research_input', side_effect=AssertionError('no load')):
            with self.assertRaisesRegex(ValueError, 'do not cover'): self.prepare(self.make_batch((request,)), (request,))


if __name__ == '__main__':
    unittest.main()
