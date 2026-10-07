"""Source calendar/cache decisions; elapsed durations retain the real clock."""
from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.database_query_cache import StoredQuery
from kiwoom_monitor.central_server.diagnostic_replay_baseline import (
    ReplayDatabaseLease, TABLES, TABLES_V2, _hash,
)
from kiwoom_monitor.central_server.diagnostic_top20_seed import Top20FixtureClock
from kiwoom_monitor.central_server.rest_broker import CentralRestBroker
from tests.unit.test_central_rest_broker import FakeClient

URL = 'postgresql://kiwoom_monitor_replay:fixture@localhost/kiwoom_monitor_replay_test'
TOKEN = 'a' * 32
ORIGIN = datetime.fromisoformat('2026-10-06T06:59:59+09:00')


class ReplayCacheClockTests(unittest.TestCase):
    def test_window_clock_is_frozen_until_arm_and_uses_real_elapsed_from_window_start(self):
        clock = Top20FixtureClock(ORIGIN)
        with patch('kiwoom_monitor.central_server.diagnostic_top20_seed.time.monotonic', return_value=100):
            self.assertEqual(ORIGIN.timestamp(), clock.wall_time())
            clock.arm(start_seconds=120)
        with patch('kiwoom_monitor.central_server.diagnostic_top20_seed.time.monotonic', return_value=102):
            self.assertEqual(122, clock.elapsed())
            self.assertEqual(ORIGIN.timestamp() + 122, clock.wall_time())
            with self.assertRaisesRegex(ValueError, 'already_armed'):
                clock.arm()
        for offset in (-1, float('nan'), float('inf'), True):
            with self.subTest(offset=offset), self.assertRaisesRegex(ValueError, 'start_invalid'):
                Top20FixtureClock(ORIGIN).arm(start_seconds=offset)

    def test_cut_window_replays_actual_cache_expiry_without_waiting_for_the_prefix(self):
        from kiwoom_monitor.central_server.diagnostic_recorded_execution import _execute_recorded_operations
        from tests.unit.test_recorded_execution import events
        clock = Top20FixtureClock(ORIGIN)
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / 'cache.sqlite3')
            store.initialize()
            self.addCleanup(store.close)
            store._query_cache_wall_time = clock.wall_time
            store.save_query('expired', 'ka10001', ORIGIN.timestamp() + 60,
                             StoredQuery({'old': True}, False, ''))
            rows = events([('read-expired', 'cache', 1, 120, 'load_query', {'cache_key': 'expired'})])
            observed = []
            original = store.load_query
            def read(cache_key):
                value = original(cache_key)
                observed.append((value, clock.wall_time()))
                return value
            with patch.object(store, 'load_query', side_effect=read):
                result = asyncio.run(_execute_recorded_operations(store, rows,
                    started_mono_ns=1_000_000_000, window_start_seconds=120,
                    window_end_seconds=120.02, clock=clock))
            self.assertEqual('complete', result['state'])
            self.assertIsNone(observed[0][0])
            self.assertGreaterEqual(observed[0][1], ORIGIN.timestamp() + 120)
            self.assertLess(result['elapsed_seconds'], 5)
            self.assertEqual(0, result['calls'][0]['scheduled_seconds'])

    def test_v1_rejects_cache_and_v2_rejects_reused_or_misaligned_clock_before_connect(self):
        from kiwoom_monitor.central_server.diagnostic_recorded_execution import run_owned_recorded_experiment
        from kiwoom_monitor.central_server.diagnostic_replay_contract import COLLECTOR_INPUT_VERSION, freeze_payload
        from tests.unit.test_recorded_execution import events
        rows = events([('cache', 'actor', 1, 0, 'load_query', {'cache_key': 'source'})])
        with patch('kiwoom_monitor.central_server.diagnostic_replay_baseline._maintenance_connection') as connect:
            with self.assertRaisesRegex(ValueError, 'query_cache_requires_v2'):
                run_owned_recorded_experiment(URL, TOKEN, '0' * 64, rows,
                    started_mono_ns=1_000_000_000, window_start_seconds=0, window_end_seconds=.01)
            clock = Top20FixtureClock(ORIGIN)
            clock.arm()
            with self.assertRaisesRegex(ValueError, 'fresh_owned_clock'):
                run_owned_recorded_experiment(URL, TOKEN, '0' * 64, rows,
                    baseline_version=2, cache_clock=clock,
                    started_mono_ns=1_000_000_000, window_start_seconds=0, window_end_seconds=.01)
            collector = [{'seq': 1, 'event_type': 'collector_input', 'input_kind': 'initial_state',
                'input_id': 'initial', 'producer_component': 'collector', 'workload_id': 'realtime',
                'mono_ns': 1_000_000_000, 'collector_input_version': COLLECTOR_INPUT_VERSION,
                'payload': freeze_payload({'source_time': ORIGIN + timedelta(days=1),
                    'approved_sources': (), 'continuous_from': (), 'warm_accumulators': False}).value}]
            with self.assertRaisesRegex(ValueError, 'source_clock_mismatch'):
                run_owned_recorded_experiment(URL, TOKEN, '0' * 64, collector,
                    baseline_version=2, cache_clock=Top20FixtureClock(ORIGIN),
                    started_mono_ns=1_000_000_000, window_start_seconds=0, window_end_seconds=.01,
                    mode='collector_with_background', collector_components=('collector',))
            connect.assert_not_called()

    def test_real_cache_retention_expiry_and_cleanup_follow_source_clock(self):
        clock = Top20FixtureClock(ORIGIN)
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / 'cache.sqlite3')
            store.initialize()
            self.addCleanup(store.close)
            store._query_cache_wall_time = clock.wall_time
            value = StoredQuery({'source': 'old_capture'}, False, '')
            store.save_query('source', 'ka10001', clock.wall_time() + 2, value)
            self.assertEqual(value, store.load_query('source'))
            with patch('kiwoom_monitor.central_server.diagnostic_top20_seed.time.monotonic', return_value=100):
                clock.arm()
            with patch('kiwoom_monitor.central_server.diagnostic_top20_seed.time.monotonic', return_value=102):
                self.assertIsNone(store.load_query('source'))
                store.save_query('new', 'ka10001', clock.wall_time() + 10, value)
            with store._connection() as connection:
                self.assertEqual([('new',)], connection.execute(
                    'SELECT cache_key FROM central_api_query_cache').fetchall())
            store.close()

    def test_broker_source_0700_key_boundary_reservation_and_persisted_ttl(self):
        async def scenario():
            source = [ORIGIN.timestamp()]
            class Store:
                def __init__(self):
                    self.values, self.expiries = {}, []
                def load_query(self, key):
                    row = self.values.get(key)
                    return row[1] if row and row[0] > source[0] else None
                def save_query(self, key, api_id, expiry, value):
                    self.values[key] = (expiry, value)
                    self.expiries.append(expiry)
            store, client = Store(), FakeClient()
            broker = CentralRestBroker(client, store, ranking_reservation=True,
                                       wall_time=lambda: source[0])
            with patch('kiwoom_monitor.central_server.rest_broker.ranking_reservation_delay', return_value=0) as reserve:
                try:
                    await broker.request('ka10001', '/api/dostk/stkinfo', {'stk_cd': '005930'})
                    await broker._persist_queue.join()
                    ttl = store.expiries[-1] - source[0]
                    self.assertGreater(ttl, 0)
                    broker._cache.clear()
                    self.assertTrue((await broker.request('ka10001', '/api/dostk/stkinfo',
                                                          {'stk_cd': '005930'})).cache_hit)
                    source[0] += 1  # Seven o'clock invalidates the pre-open key.
                    self.assertFalse((await broker.request('ka10001', '/api/dostk/stkinfo',
                                                           {'stk_cd': '005930'})).cache_hit)
                    await broker._persist_queue.join()
                    self.assertEqual(2, len(client.calls))
                    self.assertEqual(source[0] + ttl, store.expiries[-1])
                    self.assertEqual([ORIGIN.timestamp(), source[0]],
                                     [call.args[0] for call in reserve.call_args_list])
                finally:
                    await broker.close()
        asyncio.run(scenario())

    def test_v1_scope_unchanged_and_v2_requires_explicit_valid_source_clock(self):
        first = ReplayDatabaseLease(URL, TOKEN)
        self.assertEqual(TABLES, first.tables)
        self.assertEqual('replay_baseline', first._snapshot_schema)
        self.assertNotIn('central_api_query_cache', first.tables)
        for options in ({'baseline_version': 2}, {'cache_clock': Top20FixtureClock(ORIGIN)},
                        {'baseline_version': True}, {'baseline_version': 3},
                        {'baseline_version': 2, 'cache_clock': object()}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                ReplayDatabaseLease(URL, TOKEN, **options)
        second = ReplayDatabaseLease(URL, TOKEN, baseline_version=2,
                                     cache_clock=Top20FixtureClock(ORIGIN))
        self.assertEqual(TABLES_V2, second.tables)
        self.assertEqual('replay_baseline_v2', second._snapshot_schema)
        with self.assertRaises(AttributeError):
            second.tables = TABLES

    def test_cache_provider_rejects_old_generation_retired_lease_and_changed_origin(self):
        clock = Top20FixtureClock(ORIGIN)
        lease = ReplayDatabaseLease(URL, TOKEN, baseline_version=2, cache_clock=clock)
        lease._active, lease._run_ready, lease._generation = True, True, 1
        store = lease.store()
        self.assertEqual(ORIGIN.timestamp(), store._query_cache_wall_time())
        lease._generation += 1
        with self.assertRaisesRegex(RuntimeError, 'retired'):
            store._query_cache_wall_time()
        next_store = lease.store()
        clock._origin += timedelta(seconds=1)
        with self.assertRaisesRegex(RuntimeError, 'clock_changed'):
            next_store._query_cache_wall_time()
        lease._active = False
        with self.assertRaisesRegex(RuntimeError, 'retired'):
            next_store._query_cache_wall_time()

    def test_wrong_clock_or_table_scope_manifest_is_rejected_before_restore_sql(self):
        lease = ReplayDatabaseLease(URL, TOKEN, baseline_version=2,
                                    cache_clock=Top20FixtureClock(ORIGIN))
        manifest = {'version': 2, 'config': lease.config, 'tables': dict.fromkeys(TABLES_V2, {}),
                    'cache_clock': lease._clock_contract}
        cursor = MagicMock()
        for mutate, message in (
            (lambda doc: doc['cache_clock'].update(origin_epoch=0), 'clock_mismatch'),
            (lambda doc: doc['tables'].pop('central_api_query_cache'), 'missing_or_invalid')):
            changed = copy.deepcopy(manifest)
            mutate(changed)
            with patch.object(lease, '_baseline_row', return_value=(_hash(changed), changed)):
                with self.assertRaisesRegex(RuntimeError, message):
                    lease._baseline(cursor)
        cursor.execute.assert_not_called()

    def test_v2_seal_refuses_dirty_parent_before_creating_any_snapshot(self):
        lease = ReplayDatabaseLease(URL, TOKEN, baseline_version=2,
                                    cache_clock=Top20FixtureClock(ORIGIN))
        lease._active = True
        lease.connection = MagicMock()
        cursor = lease.connection.cursor.return_value.__enter__.return_value
        parent = {'version': 1, 'config': lease.config, 'schema_sha256': 'parent',
                  'tables': dict.fromkeys(TABLES, {'rows': 0}), 'sequences': {}}
        cursor.fetchone.return_value = (_hash(parent), parent)
        with patch.object(lease, '_maintenance'), patch.object(lease, '_lock_tables'), \
             patch.object(lease, '_baseline_row', return_value=None), \
             patch.object(lease, '_schema', return_value='parent'), \
             patch.object(lease, '_tables_digest', return_value={'dirty': {'rows': 1}}):
            with self.assertRaisesRegex(RuntimeError, 'requires_restored_v1_parent'):
                lease.seal()
        self.assertEqual(1, cursor.execute.call_count)
        self.assertTrue(cursor.execute.call_args.args[0].startswith('SELECT baseline_id,manifest'))


if __name__ == '__main__':
    unittest.main()
