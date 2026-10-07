"""Owned clock/worker boundaries; no PostgreSQL or external connections."""
import asyncio
from datetime import datetime
import unittest
from unittest.mock import patch

from kiwoom_monitor.central_server.diagnostic_recorded_execution import (
    _execute_recorded_operations, run_owned_recorded_experiment,
)
from kiwoom_monitor.central_server.diagnostic_replay_baseline import ReplayDatabaseLease, _ReplayStore
from kiwoom_monitor.central_server.diagnostic_replay_runtime import ReplayRuntimeScope
from kiwoom_monitor.central_server.diagnostic_top20_execution import _store_clock_is_bound
from kiwoom_monitor.central_server.diagnostic_top20_seed import Top20FixtureClock
from tests.unit.test_recorded_execution import NativeStore, events, read


ORIGIN = datetime.fromisoformat('2026-10-06T09:00:00+09:00')
URL = 'postgresql://kiwoom_monitor_replay:fixture@localhost/kiwoom_monitor_replay_test'


class Top20SharedExecutionTests(unittest.IsolatedAsyncioTestCase):
    def test_owned_cache_binding_keeps_generation_and_retirement_fences(self):
        clock = Top20FixtureClock(ORIGIN)
        lease = ReplayDatabaseLease(URL, 'a' * 32, baseline_version=2, cache_clock=clock)
        # Only the in-memory fence is exercised here. No lease is entered and
        # this is not evidence of PostgreSQL baseline acceptance.
        lease._active = lease._run_ready = True
        store = _ReplayStore(lease)
        self.assertTrue(_store_clock_is_bound(store, clock))
        self.assertEqual(ORIGIN.timestamp(), store._query_cache_wall_time())
        self.assertFalse(_store_clock_is_bound(store, Top20FixtureClock(ORIGIN)))
        lease._generation += 1
        self.assertFalse(_store_clock_is_bound(store, clock))
        with self.assertRaisesRegex(RuntimeError, 'not_ready_or_retired'):
            store._query_cache_wall_time()
        lease._generation = store._generation
        for field in ('_active', '_run_ready'):
            setattr(lease, field, False)
            self.assertFalse(_store_clock_is_bound(store, clock))
            with self.assertRaisesRegex(RuntimeError, 'not_ready_or_retired'):
                store._query_cache_wall_time()
            setattr(lease, field, True)
        store._query_cache_wall_time = clock.wall_time
        self.assertFalse(_store_clock_is_bound(store, clock))

    async def test_peers_share_armed_source_clock_and_own_actual_threads(self):
        runtime, clock = ReplayRuntimeScope(), Top20FixtureClock(ORIGIN)

        class OwnedStore(NativeStore):
            def load_daily_bars(self, code, market='', limit=250):
                runtime.require_native_work()
                return super().load_daily_bars(code, market, limit)

        store = OwnedStore()
        rows = events([read('held-call', 'held-actor', offset=.08, code='held'),
                       read('peer-call', 'independent-actor', offset=.08, code='peer')])
        with runtime.activate():
            clock.arm(start_seconds=.06)
            runner = asyncio.create_task(_execute_recorded_operations(store, rows,
                started_mono_ns=1_000_000_000, window_start_seconds=.05, window_end_seconds=.16,
                concurrency=2, clock=clock, _shared_runtime=runtime))
        try:
            while not store.started.is_set() and not runner.done():
                await asyncio.sleep(.001)
            self.assertTrue(store.started.is_set())
            while not any(code == 'peer' for code, _ in store.calls) and not runner.done():
                await asyncio.sleep(.001)
            self.assertEqual(2, store.peak)
            self.assertGreaterEqual(runtime.status()['pending_threads'], 1)
            with self.assertRaisesRegex(RuntimeError, 'not_drained'):
                runtime.require_drained()
        finally:
            store.release.set()
            result = await runner
            runtime.begin_shutdown()
            await runtime.drain(timeout=2)
        self.assertEqual('complete', result['state'])
        self.assertEqual([.08, .08], [call['scheduled_seconds'] for call in result['calls']])
        self.assertGreaterEqual(result['calls'][0]['started_seconds'], .08)
        self.assertTrue(all(identity for _, identity in store.calls))
        self.assertTrue(runtime.status()['execution_succeeded'])

    async def test_repeated_cancel_waits_for_actual_native_thread(self):
        runtime, clock, store = ReplayRuntimeScope(), Top20FixtureClock(ORIGIN), NativeStore()
        original = store.load_daily_bars
        def guarded(code, market='', limit=250):
            runtime.require_native_work()
            return original(code, market, limit)
        store.load_daily_bars = guarded
        with runtime.activate():
            clock.arm()
            runner = asyncio.create_task(_execute_recorded_operations(store,
                events([read('held-call', 'actor', code='held')]),
                started_mono_ns=1_000_000_000, window_start_seconds=0, window_end_seconds=.1,
                clock=clock, _shared_runtime=runtime))
        try:
            while not store.started.is_set() and not runner.done():
                await asyncio.sleep(.001)
            self.assertTrue(store.started.is_set())
            for _ in range(2):
                runner.cancel()
                await asyncio.sleep(.005)
                self.assertFalse(runner.done())
                self.assertEqual(1, runtime.status()['pending_threads'])
                with self.assertRaisesRegex(RuntimeError, 'not_drained'):
                    runtime.require_drained()
        finally:
            store.release.set()
            with self.assertRaises(asyncio.CancelledError):
                await runner
            runtime.begin_shutdown()
            await runtime.drain(timeout=2)
        self.assertTrue(store.finished.is_set())
        self.assertEqual(0, runtime.status()['pending_threads'])
        self.assertFalse(runtime.status()['execution_succeeded'])

    async def test_invalid_shared_clock_owner_and_mode_fail_before_native_access(self):
        store, runtime, clock = NativeStore(), ReplayRuntimeScope(), Top20FixtureClock(ORIGIN)
        options = dict(started_mono_ns=1_000_000_000, window_start_seconds=0,
                       window_end_seconds=.01, clock=clock, _shared_runtime=runtime)
        rows = events([read('never', 'actor')])
        with runtime.activate():
            with self.assertRaisesRegex(ValueError, 'shared_runtime_invalid'):
                await _execute_recorded_operations(store, rows, **options)
            clock.arm()
            with self.assertRaisesRegex(ValueError, 'shared_runtime_invalid'):
                await _execute_recorded_operations(store, rows, **options, mode='collector_with_background')
        with self.assertRaisesRegex(RuntimeError, 'not_activated'):
            await _execute_recorded_operations(store, rows, **options)
        with runtime.activate():
            runtime.begin_shutdown()
            with self.assertRaisesRegex(RuntimeError, 'new_work_fenced'):
                await _execute_recorded_operations(store, rows, **options)
        self.assertEqual([], store.calls)
        await runtime.drain(timeout=2)

    async def test_native_failure_is_explicitly_incomplete(self):
        store, runtime, clock = NativeStore(), ReplayRuntimeScope(), Top20FixtureClock(ORIGIN)
        with runtime.activate():
            clock.arm()
            result = await _execute_recorded_operations(store, events([read('fail', 'actor')]),
                started_mono_ns=1_000_000_000, window_start_seconds=0, window_end_seconds=.02,
                clock=clock, _shared_runtime=runtime)
        runtime.begin_shutdown()
        await runtime.drain(timeout=2)
        self.assertEqual('incomplete', result['state'])
        self.assertEqual('OSError', result['calls'][0]['exception_type'])
        self.assertFalse(result['input_timing_preserved'])
        self.assertFalse(runtime.status()['execution_succeeded'])

    def test_standalone_runner_rejects_shared_runtime_before_connect_or_restore(self):
        with patch('kiwoom_monitor.central_server.diagnostic_replay_baseline._maintenance_connection') as connect:
            with self.assertRaisesRegex(ValueError, 'requires_lifecycle_owner'):
                run_owned_recorded_experiment(URL, 'a' * 32, '0' * 64,
                    events([read('never', 'actor')]), started_mono_ns=1_000_000_000,
                    window_start_seconds=0, window_end_seconds=.01,
                    _shared_runtime=ReplayRuntimeScope())
            connect.assert_not_called()

    async def test_peer_cannot_use_a_different_database_lease_runtime(self):
        runtime, clock = ReplayRuntimeScope(), Top20FixtureClock(ORIGIN)
        lease = ReplayDatabaseLease(URL, 'a' * 32, baseline_version=2, cache_clock=clock)
        store = _ReplayStore(lease)
        with runtime.activate():
            clock.arm()
            with patch.object(lease, 'connect_store') as connect:
                with self.assertRaisesRegex(ValueError, 'shared_lease_mismatch'):
                    await _execute_recorded_operations(store, events([read('never', 'actor')]),
                        started_mono_ns=1_000_000_000, window_start_seconds=0, window_end_seconds=.01,
                        clock=clock, _shared_runtime=runtime)
                connect.assert_not_called()
