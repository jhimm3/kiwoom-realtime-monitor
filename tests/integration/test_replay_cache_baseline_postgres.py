"""Opt-in v2 acceptance. Operator seals v2 offline; tests never provision/seal."""
from __future__ import annotations

from datetime import datetime, timedelta
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from kiwoom_monitor.central_server.database_query_cache import StoredQuery
from kiwoom_monitor.central_server.diagnostic_replay_baseline import ReplayDatabaseLease, TABLES
from kiwoom_monitor.central_server.diagnostic_top20_seed import Top20FixtureClock


class ReplayCacheBaselinePostgresTests(unittest.TestCase):
    def setUp(self):
        url = os.environ.get('KIWOOM_REPLAY_DATABASE_URL', '')
        token = os.environ.get('KIWOOM_REPLAY_OWNER_TOKEN', '')
        origin = os.environ.get('KIWOOM_REPLAY_SOURCE_ORIGIN', '')
        if not url or not token or not origin:
            self.skipTest('sealed v2 replay DB, owner token, and source origin are required')
        self.clock = Top20FixtureClock(datetime.fromisoformat(origin))
        self.lease = ReplayDatabaseLease(url, token, baseline_version=2, cache_clock=self.clock)
        self.lease.__enter__()
        self.addCleanup(self.lease.__exit__, None, None, None)
        self.baseline_id = self.lease.status()['baseline_id']
        if not self.baseline_id:
            self.skipTest('operator must explicitly seal cache baseline v2 first')
        self.lease.restore(self.baseline_id)
        self.addCleanup(self.lease.restore, self.baseline_id)
        self.key = 'cache-v2-gate-' + uuid4().hex
        self.v1_before = self.v1_proof()
        self.addCleanup(lambda: self.assertEqual(self.v1_before, self.v1_proof()))

    def v1_proof(self):
        with self.lease.connection.cursor() as cursor:
            cursor.execute('SELECT baseline_id,manifest FROM replay_meta.baseline WHERE singleton')
            row = cursor.fetchone()
            digest = self.lease._tables_digest(cursor, 'replay_baseline', tables=TABLES)
        return row, digest

    def write(self):
        value = StoredQuery({'source': self.key}, True, 'page-2')
        self.lease.store().save_query(self.key, 'ka10001', self.clock.wall_time() + 5, value)
        self.assertEqual(value, self.lease.store().load_query(self.key))

    def test_three_restores_remove_cache_only_in_v2_and_fence_previous_stores(self):
        for _ in range(3):
            previous = self.lease.store()
            self.write()
            restored = self.lease.restore(self.baseline_id)
            self.assertIn('central_api_query_cache', restored['table_counts'])
            self.assertIsNone(self.lease.store().load_query(self.key))
            with self.assertRaisesRegex(RuntimeError, 'retired'):
                previous.load_query(self.key)
            self.assertEqual(self.v1_before, self.v1_proof())

    def test_failed_restore_rolls_back_cache_rows_and_next_sequence(self):
        self.write()
        with self.lease.connection.cursor() as cursor:
            cursor.execute("SELECT nextval('central_observation_revisions_accepted_sequence_seq')")
            cursor.fetchone()
            before = self.lease._sequences(cursor)
        original = self.lease._tables_digest
        def fail(cursor, schema, **options):
            if schema == 'public':
                raise RuntimeError('injected v2 verification failure after cache restore')
            return original(cursor, schema, **options)
        with patch.object(self.lease, '_tables_digest', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'injected v2 verification'):
                self.lease.restore(self.baseline_id)
        with self.lease.connection.cursor() as cursor:
            cursor.execute('SELECT payload_json FROM central_api_query_cache WHERE cache_key=%s', (self.key,))
            self.assertEqual({'source': self.key}, cursor.fetchone()[0])
            self.assertEqual(before, self.lease._sequences(cursor))
        self.assertEqual(self.v1_before, self.v1_proof())

    def test_corrupt_v2_snapshot_and_changed_clock_reject_before_reset(self):
        self.write()
        with self.lease.connection.transaction(force_rollback=True):
            with self.lease.connection.cursor() as cursor:
                cursor.execute('INSERT INTO replay_baseline_v2.central_api_query_cache '
                               '(cache_key,api_id,expires_at,payload_json,has_next,next_key) '
                               "VALUES(%s,'ka10001',0,'{}',false,'')", (self.key,))
            with self.assertRaisesRegex(RuntimeError, 'snapshot_changed'):
                self.lease.restore(self.baseline_id)
        self.clock._origin += timedelta(seconds=1)
        try:
            with self.assertRaisesRegex(RuntimeError, 'clock_mismatch'):
                self.lease.restore(self.baseline_id)
        finally:
            self.clock._origin -= timedelta(seconds=1)
        with self.lease.connection.cursor() as cursor:
            cursor.execute('SELECT count(*) FROM central_api_query_cache WHERE cache_key=%s', (self.key,))
            self.assertEqual(1, cursor.fetchone()[0])
        self.assertEqual(self.v1_before, self.v1_proof())


if __name__ == '__main__':
    unittest.main()
