"""v2 owned runner gates on the disposable controlled cache fixture only.

No provisioning/resealing here. These tests are not a market-load baseline.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from kiwoom_monitor.central_server import diagnostic_replay_baseline as baseline
from kiwoom_monitor.central_server.database_query_cache import StoredQuery
from kiwoom_monitor.central_server.diagnostic_recorded_execution import run_owned_recorded_experiment
from kiwoom_monitor.central_server.diagnostic_replay_contract import COLLECTOR_INPUT_VERSION, freeze_payload
from kiwoom_monitor.central_server.diagnostic_top20_seed import Top20FixtureClock
from tests.unit.test_recorded_execution import events


class ReplayCacheExecutionPostgresTests(unittest.TestCase):
    def setUp(self):
        self.url = os.environ.get('KIWOOM_REPLAY_DATABASE_URL', '')
        self.token = os.environ.get('KIWOOM_REPLAY_OWNER_TOKEN', '')
        origin = os.environ.get('KIWOOM_REPLAY_SOURCE_ORIGIN', '')
        if (not self.url or not self.token or not origin
                or os.environ.get('KIWOOM_REPLAY_TEMPORARY_FIXTURE') != '1'):
            self.skipTest('disposable sealed v2 cache fixture is required')
        self.origin = datetime.fromisoformat(origin)
        self.key = 'cache-run-' + uuid4().hex
        with self.lease() as lease:
            with lease.connection.cursor() as cursor:
                self.baseline_id, self.manifest = lease._baseline(cursor)
            lease.restore(self.baseline_id)
            seed = lease.store().load_query('immutable-cache-seed')
            if seed is None:
                raise RuntimeError('controlled_cache_execution_seed_missing')
            self.v1_before = self.v1_proof(lease)
        self.addCleanup(self.restore)

    def lease(self):
        return baseline.ReplayDatabaseLease(self.url, self.token, baseline_version=2,
                                            cache_clock=Top20FixtureClock(self.origin))

    def v1_proof(self, lease):
        with lease.connection.cursor() as cursor:
            cursor.execute('SELECT baseline_id,manifest FROM replay_meta.baseline WHERE singleton')
            return cursor.fetchone(), lease._tables_digest(cursor, 'replay_baseline', tables=baseline.TABLES)

    def restore(self):
        with self.lease() as lease:
            lease.restore(self.baseline_id)

    def assert_restored(self):
        with self.lease() as lease, lease.connection.cursor() as cursor:
            # Do not restore before inspecting: that would hide cleanup failure.
            self.assertEqual(self.manifest['tables'], lease._tables_digest(cursor, 'public'))
            self.assertEqual(self.manifest['sequences'], lease._sequences(cursor))
            self.assertEqual(0, lease.status()['owned_connections'])
            self.assertEqual(self.v1_before, self.v1_proof(lease))

    def run_fixture(self, rows, *, start=120, end=120.02, **options):
        return run_owned_recorded_experiment(self.url, self.token, self.baseline_id, rows,
            baseline_version=2, cache_clock=Top20FixtureClock(self.origin),
            started_mono_ns=1_000_000_000, window_start_seconds=start,
            window_end_seconds=end, **options)

    def cache_events(self, *, start=120):
        rows = events([
            ('expired', 'cache', 1, start, 'load_query', {'cache_key': 'immutable-cache-seed'}),
            ('save', 'cache', 2, start + .001, 'save_query', {'cache_key': self.key,
                'api_id': 'ka10001', 'expires_at': self.origin.timestamp() + 10000,
                'value': StoredQuery({'source': self.key}, False, '')}),
            ('read', 'cache', 3, start + .002, 'load_query', {'cache_key': self.key}),
        ])
        for row in rows:
            row['workload_id'] = 'rest_market'
            row['producer_component'] = 'cache:controlled'
        return rows

    def test_three_runs_use_source_ttl_preserve_connections_and_restore_cache_and_v1(self):
        original = baseline._ReplayStore.load_query
        reads, stores = [], []
        def inspect(store, cache_key):
            value = original(store, cache_key)
            reads.append((cache_key, value, store._query_cache_wall_time()))
            stores.append(store)
            return value
        with patch.object(baseline._ReplayStore, 'load_query', inspect):
            for _ in range(3):
                result = self.run_fixture(self.cache_events())
                self.assertEqual('complete', result['state'])
                self.assertEqual(2, result['baseline_version'])
                self.assertTrue(result['cleanup']['baseline_restored'])
                self.assertEqual(120, result['cache_clock']['start_seconds'])
                self.assertEqual(3, len(result['calls']))
                self.assert_restored()
        self.assertEqual(6, len(reads))
        for key, value, at in reads:
            self.assertGreaterEqual(at, self.origin.timestamp() + 120)
            self.assertEqual(None if key == 'immutable-cache-seed' else
                             StoredQuery({'source': self.key}, False, ''), value)
        for store in stores:
            with self.assertRaisesRegex(RuntimeError, 'retired'):
                store._query_cache_wall_time()

    def test_collector_and_cache_share_clock_and_exclusion_does_not_inject_old_cache_output(self):
        rows = self.cache_events(start=0)
        component = 'collector:controlled'
        for number, (kind, offset, value) in enumerate([
            ('initial_state', 0, {'source_time': self.origin, 'approved_sources': (),
                                 'continuous_from': (), 'warm_accumulators': False}),
            ('message', .01, {'source_time': self.origin + timedelta(seconds=.01),
                'message': {'trnm': 'REAL', 'data': [
                    {'type': '0J', 'item': '001', 'values': {'20': '085500', '10': '2500.1', '14': '99'}}]}}),
        ]):
            rows.append({'seq': len(rows) + 1, 'event_type': 'collector_input',
                'input_kind': kind, 'input_id': f'input-{number}', 'workload_id': 'realtime',
                'producer_component': component, 'mono_ns': 1_000_000_000 + int(offset * 1e9),
                'collector_input_version': COLLECTOR_INPUT_VERSION, 'payload': freeze_payload(value).value})
        for excluded in ((), ('rest_market',)):
            with self.subTest(excluded=excluded):
                result = self.run_fixture(rows, start=0, end=.03,
                    mode='collector_with_background', collector_components=(component,),
                    exclude_workloads=excluded)
                self.assertEqual('complete', result['state'])
                self.assertEqual(0, result['cache_clock']['start_seconds'])
                self.assertEqual(0 if excluded else 3, len(result['calls']))
                self.assertEqual({'0J': 1}, result['collector_reports'][0]['event_type_counts'])
                self.assertEqual(0, result['collector_reports'][0]['pending_records_after_drain'])
                self.assertEqual(self.manifest['tables']['central_api_query_cache']['rows'] +
                                 (0 if excluded else 1), result['final_tables']['central_api_query_cache']['rows'])
                self.assert_restored()

    def test_lost_cache_commit_ack_drains_stops_actor_and_restores_baseline(self):
        original = baseline._ReplayStore.save_query
        stores = []
        def lost_ack(store, **arguments):
            original(store, **arguments)
            stores.append(store)
            raise OSError('injected cache COMMIT acknowledgement loss')
        with patch.object(baseline._ReplayStore, 'save_query', lost_ack):
            result = self.run_fixture(self.cache_events())
        self.assertEqual('incomplete', result['state'])
        self.assertEqual(['returned', 'failed', 'not_started'], [row['state'] for row in result['calls']])
        self.assertTrue(result['cleanup']['baseline_restored'])
        self.assertEqual(1, len(stores))
        with self.assertRaisesRegex(RuntimeError, 'retired'):
            stores[0].load_query(self.key)
        self.assert_restored()


if __name__ == '__main__':
    unittest.main()
