from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest

from kiwoom_monitor.application.research_splits import FinalHoldoutBatchSpec, ResearchEvaluationSpec, ResearchFoldSpec
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository, _MIGRATIONS
from kiwoom_monitor.infrastructure.persistence.schema_migrations import SQLiteMigrationRunner


class FinalHoldoutLedgerTests(unittest.TestCase):
    def setUp(self):
        root = TemporaryDirectory(); self.addCleanup(root.cleanup)
        self.path = Path(root.name) / 'research.sqlite3'
        self.repo = ResearchRepository(self.path)
        self.evaluation = ResearchEvaluationSpec('chronological_holdout/v1', (
            ResearchFoldSpec('final', 'OOS', '2026-09-14T09:00:00+09:00', '2026-09-14T15:30:00+09:00'),
        ), 120, 0, 60, 10, 1)
        self.batch = FinalHoldoutBatchSpec('final_holdout_batch/v1', 'frozen-data', 'a'*64,
                                         'krx-regular/v1', ('b'*64, 'c'*64), self.evaluation)
        self.at = '2026-09-15T00:00:00+00:00'

    def access(self, batch=None, request='access', at=None):
        return self.repo.record_final_holdout_access(batch or self.batch, request_id=request, accessed_at=at or self.at)

    def changed_window(self, start, end):
        return replace(self.batch, evaluation=replace(self.evaluation, folds=(ResearchFoldSpec('final', 'OOS', start, end),)))

    def test_roundtrip_immutable_and_timezone_aliases_have_identical_ids(self):
        self.assertEqual(self.batch.to_dict(), FinalHoldoutBatchSpec.from_dict(self.batch.to_dict()).to_dict())
        utc = self.changed_window('2026-09-14T00:00:00+00:00', '2026-09-14T06:30:00+00:00')
        self.assertEqual(self.batch.batch_id, utc.batch_id)
        self.assertEqual(self.batch.window_id, utc.window_id)
        with self.assertRaises(FrozenInstanceError):
            self.batch.dataset_id = 'new'
        self.assertNotIn('database', self.batch.to_dict())

    def test_rejects_invalid_identity_hash_candidates_and_non_final_policy(self):
        changes = ({'version':'other'}, {'dataset_id':''}, {'dataset_hash':'label'}, {'session_profile':'SOR'},
                   {'candidate_spec_hashes':['b'*64]}, {'candidate_spec_hashes':()},
                   {'candidate_spec_hashes':('c'*64,'b'*64)}, {'candidate_spec_hashes':('b'*64,'b'*64)},
                   {'candidate_spec_hashes':('not-a-hash',)},
                   {'candidate_spec_hashes':tuple(f'{i:064x}' for i in range(201))},
                   {'evaluation':replace(self.evaluation, folds=(replace(self.evaluation.folds[0], role='TRAIN'),))},
                   {'evaluation':replace(self.evaluation, final_holdout_accessed_at=self.at, final_holdout_access_reason='seen')},
                   {'evaluation':replace(self.evaluation, minimum_closed_trades=True)})
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(self.batch, **change)
        document = self.batch.to_dict(); document['extra'] = True
        with self.assertRaises(ValueError): FinalHoldoutBatchSpec.from_dict(document)
        for policy in (None, {**self.batch.to_dict()['evaluation'], 'extra': True},
                       {**self.batch.to_dict()['evaluation'], 'minimum_closed_trades': True}):
            document = self.batch.to_dict(); document['evaluation'] = policy
            with self.assertRaises(ValueError): FinalHoldoutBatchSpec.from_dict(document)

    def test_idempotent_access_and_new_technical_request_keep_locked_snapshot(self):
        self.assertTrue(self.access()); self.assertFalse(self.access())
        self.assertFalse(self.access(at='2026-09-15T09:00:00+09:00'))
        self.assertTrue(self.access(request='technical-retry'))
        window = ResearchRepository(self.path).load_final_holdout_window(self.batch.window_id)
        self.assertEqual('FINAL_RESERVED', window['state'])
        self.assertEqual(self.batch.to_dict(), window['spec'])
        self.assertEqual(2, len(self.repo.load_final_holdout_events(self.batch.window_id)))

    def test_candidate_input_profile_threshold_or_fold_rename_cannot_reuse_window(self):
        self.access()
        changes = ({'candidate_spec_hashes':('d'*64,)}, {'dataset_id':'renamed-data'}, {'dataset_hash':'d'*64},
                   {'session_profile':'krx-full-day/v1'},
                   {'evaluation':replace(self.evaluation, minimum_closed_trades=11)},
                   {'evaluation':replace(self.evaluation, folds=(replace(self.evaluation.folds[0], name='renamed'),))})
        for change in changes:
            batch = replace(self.batch, **change)
            self.assertEqual(self.batch.window_id, batch.window_id)
            with self.subTest(change=change), self.assertRaises(ValueError): self.access(batch, request='modified')
        self.assertEqual(1, len(self.repo.load_final_holdout_events(self.batch.window_id)))

    def test_overlap_shift_subset_superset_and_changed_profile_are_blocked(self):
        self.access()
        windows = [('08:00','10:00'), ('10:00','11:00'), ('15:00','16:00'), ('08:00','16:00')]
        for start, end in windows:
            batch = self.changed_window(f'2026-09-14T{start}:00+09:00', f'2026-09-14T{end}:00+09:00')
            with self.subTest(window=(start,end)), self.assertRaises(ValueError): self.access(batch, request='shift')
        self.assertEqual(1, len(self.repo.load_final_holdout_events(self.batch.window_id)))

    def test_adjacent_window_is_allowed_and_fractional_overlap_is_not(self):
        self.access()
        adjacent = self.changed_window('2026-09-14T15:30:00+09:00', '2026-09-14T16:00:00+09:00')
        self.assertTrue(self.access(adjacent, request='adjacent'))
        overlap = self.changed_window('2026-09-14T15:29:59.999999+09:00', '2026-09-14T16:00:00+09:00')
        with self.assertRaises(ValueError): self.access(overlap, request='fractional-overlap')

    def test_exposure_is_persistent_irreversible_and_access_is_blocked(self):
        self.access()
        kwargs = dict(request_id='expose', exposed_at=self.at, reason='used final result to improve strategy')
        self.assertTrue(self.repo.expose_final_holdout(self.batch.window_id, **kwargs))
        self.assertFalse(self.repo.expose_final_holdout(self.batch.window_id, **kwargs))
        self.repo = ResearchRepository(self.path)
        self.assertEqual('EXPOSED_DEVELOPMENT', self.repo.load_final_holdout_window(self.batch.window_id)['state'])
        with self.assertRaises(ValueError): self.access(request='retry-after-exposure')
        with self.assertRaises(ValueError): self.access()
        self.assertEqual(['FINAL_ACCESS', 'EXPOSED_DEVELOPMENT'],
                         [row['event_type'] for row in self.repo.load_final_holdout_events(self.batch.window_id)])

    def test_events_need_aware_times_after_end_and_cannot_move_backwards(self):
        for at in ('2026-09-14T06:29:59+00:00', '2026-09-15T00:00:00', 'invalid'):
            with self.subTest(at=at), self.assertRaises(ValueError): self.access(at=at)
        self.access()
        with self.assertRaises(ValueError): self.access(request='backwards', at='2026-09-14T23:59:59+00:00')
        for request in ('', True, 'x'*257, 'a\x00b'):
            with self.assertRaises(ValueError): self.access(request=request)
        self.assertEqual(1, len(self.repo.load_final_holdout_events(self.batch.window_id)))

    def test_request_id_collision_rolls_back_new_window_and_exposure(self):
        self.access()
        tomorrow = self.changed_window('2026-09-15T09:00:00+09:00','2026-09-15T15:30:00+09:00')
        with self.assertRaises(ValueError): self.access(tomorrow, at='2026-09-16T00:00:00+00:00')
        self.assertIsNone(self.repo.load_final_holdout_window(tomorrow.window_id))
        with self.assertRaises(ValueError):
            self.repo.expose_final_holdout(self.batch.window_id, request_id='access', exposed_at=self.at, reason='collision')
        self.assertEqual('FINAL_RESERVED', self.repo.load_final_holdout_window(self.batch.window_id)['state'])

    def test_exposure_needs_known_window_reason_and_non_backwards_time(self):
        self.access()
        for window, reason, at in [('unknown','reason',self.at), (self.batch.window_id,'',self.at),
                                  (self.batch.window_id,'x'*2001,self.at),
                                  (self.batch.window_id,'reason','2026-09-14T23:59:59+00:00')]:
            with self.assertRaises(ValueError):
                self.repo.expose_final_holdout(window, request_id='bad', exposed_at=at, reason=reason)

    def test_concurrent_different_batches_have_one_atomic_winner(self):
        barrier = Barrier(2)
        def reserve(index):
            repo = ResearchRepository(self.path); barrier.wait(timeout=5)
            batch = self.batch if index == 0 else replace(self.batch, candidate_spec_hashes=('d'*64,))
            try:
                return repo.record_final_holdout_access(batch, request_id=f'race-{index}', accessed_at=self.at)
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(reserve, (0,1)))
        self.assertEqual([False,True], sorted(results))
        self.assertEqual(1, len(self.repo.load_final_holdout_events(self.batch.window_id)))

    def test_concurrent_overlapping_windows_have_one_atomic_winner(self):
        barrier = Barrier(2)
        shifted = self.changed_window('2026-09-14T10:00:00+09:00', '2026-09-14T16:00:00+09:00')
        def reserve(index):
            repo = ResearchRepository(self.path); barrier.wait(timeout=5)
            try:
                return repo.record_final_holdout_access(self.batch if index == 0 else shifted,
                    request_id=f'overlap-{index}', accessed_at=self.at)
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual([False,True], sorted(executor.map(reserve, (0,1))))
        windows = [self.repo.load_final_holdout_window(batch.window_id) for batch in (self.batch, shifted)]
        self.assertEqual(1, sum(window is not None for window in windows))

    def test_read_only_v18_cannot_mutate_and_bounded_queries_preserve_bytes(self):
        self.access(); before = self.path.read_bytes()
        reader = ResearchRepository(self.path, read_only=True)
        self.assertEqual(self.batch.batch_id, reader.load_final_holdout_window(self.batch.window_id)['batch_id'])
        self.assertEqual(1, len(reader.load_final_holdout_events(self.batch.window_id, limit=1)))
        with self.assertRaises(sqlite3.OperationalError):
            reader.record_final_holdout_access(self.batch, request_id='ro', accessed_at=self.at)
        self.assertEqual(before, self.path.read_bytes())
        for limit in (True,0,1001):
            with self.assertRaises(ValueError): reader.load_final_holdout_events(self.batch.window_id, limit=limit)

    def test_v17_read_only_compatibility_and_additive_migration_preserve_rows(self):
        old_path = self.path.parent / 'v17.sqlite3'
        with closing(sqlite3.connect(old_path)) as connection:
            SQLiteMigrationRunner(connection, table='research_schema_migrations').apply(_MIGRATIONS[:17])
            connection.execute("INSERT INTO research_runs(run_id,status,started_at,spec_json,input_manifest_json) VALUES('old','completed','now','{}','{}')")
            connection.execute("INSERT INTO research_reports VALUES('old-report','old','ELIGIBLE','{}')")
            connection.commit()
            runs = connection.execute('SELECT * FROM research_runs').fetchall()
            reports = connection.execute('SELECT * FROM research_reports').fetchall()
            migrations = connection.execute('SELECT * FROM research_schema_migrations').fetchall()
        before = old_path.read_bytes()
        self.assertEqual(17, ResearchRepository(old_path, read_only=True).schema_version())
        self.assertEqual(before, old_path.read_bytes())
        migrated = ResearchRepository(old_path)
        self.assertEqual(27, migrated.schema_version())
        with closing(sqlite3.connect(old_path)) as connection:
            self.assertEqual(runs, connection.execute('SELECT * FROM research_runs').fetchall())
            self.assertEqual(reports, connection.execute('SELECT * FROM research_reports').fetchall())
            self.assertEqual(migrations, connection.execute('SELECT * FROM research_schema_migrations WHERE version<=17').fetchall())
            self.assertEqual(0, connection.execute('SELECT COUNT(*) FROM research_final_holdout_windows').fetchone()[0])
            self.assertEqual(0, connection.execute('SELECT COUNT(*) FROM research_final_holdout_executions').fetchone()[0])
        self.assertEqual('completed', migrated.load_run('old')['status'])

    def test_v18_read_only_compatibility_and_execution_migrations(self):
        old_path = self.path.parent / 'v18.sqlite3'
        with closing(sqlite3.connect(old_path)) as connection:
            SQLiteMigrationRunner(connection, table='research_schema_migrations').apply(_MIGRATIONS[:18])
            connection.execute("INSERT INTO research_runs(run_id,status,started_at,spec_json,input_manifest_json) VALUES('v18-run','completed','now','{}','{}')")
            connection.commit()
        before = old_path.read_bytes()
        self.assertEqual(18,ResearchRepository(old_path,read_only=True).schema_version())
        self.assertEqual(before,old_path.read_bytes())
        migrated = ResearchRepository(old_path)
        self.assertEqual(27, migrated.schema_version())
        self.assertEqual('completed',migrated.load_run('v18-run')['status'])
        self.assertEqual((),migrated.load_final_holdout_executions('absent'))

    def test_v19_read_only_compatibility_and_v20_recovery_migration(self):
        old_path = self.path.parent / 'v19.sqlite3'
        with closing(sqlite3.connect(old_path)) as connection:
            SQLiteMigrationRunner(connection, table='research_schema_migrations').apply(_MIGRATIONS[:19])
            connection.execute("INSERT INTO research_runs(run_id,status,started_at,spec_json,input_manifest_json,error) "
                               "VALUES('v19-run','failed','now','{}','{}','fixture failure')")
            connection.execute('INSERT INTO research_final_holdout_windows VALUES(?,?,?,?,?,?,?)',
                ('window-v19','2026-09-01T00:00:00+00:00','2026-09-02T00:00:00+00:00',
                 'FINAL_RESERVED','batch-v19','{}','now'))
            connection.execute('INSERT INTO research_final_holdout_executions VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                ('execution-v19','window-v19','batch-v19','a'*64,'v19-run','owner','FAILED',
                 'now','now','','fixture failure'))
            connection.commit()
        before = old_path.read_bytes()
        self.assertEqual(19, ResearchRepository(old_path, read_only=True).schema_version())
        self.assertEqual(before, old_path.read_bytes())
        migrated = ResearchRepository(old_path)
        self.assertEqual(27, migrated.schema_version())
        self.assertEqual(1, migrated.load_final_holdout_executions('batch-v19')[0]['generation'])
        self.assertEqual((), migrated.load_final_holdout_recoveries('batch-v19'))


if __name__ == '__main__':
    unittest.main()
