"""Native task/thread and real-file cleanup gates on disposable sealed v2 DB.

Controlled fixture only; no market equivalence or performance acceptance.
"""
import asyncio
from datetime import datetime
import os
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import MagicMock, patch

from kiwoom_monitor.central_server import diagnostic_replay_baseline as baseline
from kiwoom_monitor.central_server.autonomous_top20 import AutonomousTop20Service
from kiwoom_monitor.central_server.database_query_cache import StoredQuery
from kiwoom_monitor.central_server.diagnostic_replay_runtime import (
    ReplayRuntimeScope, close_top20_replay_resources, owned_create_task, owned_to_thread,
)
from kiwoom_monitor.central_server.diagnostic_top20_outbox import (
    Top20ReplayOutbox, restore_top20_replay_baseline,
)
from kiwoom_monitor.central_server.diagnostic_top20_seed import Top20FixtureClock
from kiwoom_monitor.central_server.persistent_outbox import JsonRecordOutbox
from kiwoom_monitor.central_server.realtime_hub import RealtimeHub


async def until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(.005)


class Top20ReplayRuntimePostgresTests(unittest.TestCase):
    def setUp(self):
        self.url = os.environ.get('KIWOOM_REPLAY_DATABASE_URL', '')
        self.token = os.environ.get('KIWOOM_REPLAY_OWNER_TOKEN', '')
        origin = os.environ.get('KIWOOM_REPLAY_SOURCE_ORIGIN', '')
        if (not self.url or not self.token or not origin
                or os.environ.get('KIWOOM_REPLAY_TEMPORARY_FIXTURE') != '1'):
            self.skipTest('disposable sealed v2 cache fixture is required')
        self.origin = datetime.fromisoformat(origin)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.parent = Path(self.temporary.name)
        with self.lease() as lease:
            with lease.connection.cursor() as cursor:
                self.baseline_id, self.manifest = lease._baseline(cursor)
            lease.restore(self.baseline_id)
            with lease.connection.cursor() as cursor:
                self.v1_before = self.v1_proof(lease, cursor)
        self.addCleanup(self.restore)

    def lease(self):
        return baseline.ReplayDatabaseLease(self.url, self.token, baseline_version=2,
                                            cache_clock=Top20FixtureClock(self.origin))

    def v1_proof(self, lease, cursor):
        cursor.execute('SELECT baseline_id,manifest FROM replay_meta.baseline WHERE singleton')
        return cursor.fetchone(), lease._tables_digest(cursor, 'replay_baseline', tables=baseline.TABLES)

    def restore(self):
        with self.lease() as lease:
            lease.restore(self.baseline_id)

    def assert_restored(self, lease):
        with lease.connection.cursor() as cursor:
            self.assertEqual(self.manifest['tables'], lease._tables_digest(cursor, 'public'))
            self.assertEqual(self.manifest['sequences'], lease._sequences(cursor))
            self.assertEqual(self.v1_before, self.v1_proof(lease, cursor))

    def test_cancelled_preconnect_thread_cannot_race_restore_or_lease_release(self):
        async def scenario(lease):
            runtime = ReplayRuntimeScope()
            fixture = Top20ReplayOutbox(self.parent)
            fixture.begin_run(runtime)
            lease.bind_runtime(runtime)
            store = lease.store()
            started, release = Event(), Event()
            connections = []
            original = store._connect
            def connect():
                value = original()
                connections.append(value)
                return value
            def late_write():
                started.set()
                if not release.wait(5):
                    raise AssertionError('release missing')
                store.save_query('top20-drain-fixture', 'ka10001', self.origin.timestamp() + 60,
                                 StoredQuery({'fixture': True}, False, ''))
            with patch.object(store, '_connect', side_effect=connect):
                with runtime.activate():
                    task = owned_create_task(owned_to_thread(late_write))
                try:
                    await until(started.is_set)
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                    self.assertEqual(0, lease.status()['owned_connections'])
                    for action in (lambda: lease.restore(self.baseline_id), lease._retire):
                        with self.assertRaisesRegex(RuntimeError, 'not_drained'):
                            action()
                    self.assertEqual([], connections)
                finally:
                    release.set()
                runtime.begin_shutdown()
                await runtime.drain(timeout=5)
            self.assertEqual(1, len(connections))
            self.assertTrue(connections[0].closed)
            self.assertEqual(0, lease.status()['owned_connections'])
            with lease.connection.cursor() as cursor:
                self.assertNotEqual(self.manifest['tables'], lease._tables_digest(cursor, 'public'))
            result = restore_top20_replay_baseline(lease, self.baseline_id, fixture, runtime)
            self.assertTrue(result['cleanup_complete'])
            fixture.require_baseline()
            self.assert_restored(lease)
            with self.assertRaisesRegex(RuntimeError, 'unowned_native'):
                store.load_query('top20-drain-fixture')
        with self.lease() as lease:
            lease.restore(self.baseline_id)
            asyncio.run(scenario(lease))

    def test_native_pending_outbox_restarts_after_lost_commit_ack_then_restores_both(self):
        minute = self.origin.replace(second=0, microsecond=0).isoformat(timespec='minutes')
        records = {minute: {'minute': minute, 'market_values': [1.0, 2.0, 0.0],
            'codes': ['005930'], 'market_counts': [1, 0, 0],
            'cohort_segments': [[minute, ['005930']]], 'capture_state': 'partial', 'total': 3.0}}
        async def scenario(lease):
            runtime = ReplayRuntimeScope()
            fixture = Top20ReplayOutbox(self.parent, records=records)
            fixture.begin_run(runtime)
            lease.bind_runtime(runtime)
            store = lease.store()
            calls = []
            original = store.save_dataset_snapshot
            def ack_loss(*args, **kwargs):
                original(*args, **kwargs)
                calls.append(args)
                if len(calls) == 1:
                    raise OSError('injected acknowledgement loss after native commit')
            async def waiting():
                await asyncio.Event().wait()
            def native_service():
                service = AutonomousTop20Service(MagicMock(), RealtimeHub(), store,
                    outbox_path=fixture.path, now_provider=lambda: self.origin)
                for name in ('_schedule_loop', '_event_loop', '_index_loop'):
                    setattr(service, name, waiting)
                return service
            with patch.object(store, 'save_dataset_snapshot', side_effect=ack_loss):
                with runtime.activate():
                    first = native_service()
                    await first.start()
                    self.assertEqual(records, JsonRecordOutbox(fixture.path).load())
                    await first.close()
                    self.assertEqual(records, JsonRecordOutbox(fixture.path).load())
                    second = native_service()
                    await second.start()
                    self.assertEqual({}, JsonRecordOutbox(fixture.path).load())
                receipt = await close_top20_replay_resources(runtime, second, None)
            self.assertEqual(2, len(calls))
            self.assertFalse(receipt['execution_succeeded'])
            self.assertEqual(0, lease.status()['owned_connections'])
            result = restore_top20_replay_baseline(lease, self.baseline_id, fixture, runtime)
            self.assertTrue(result['cleanup_complete'])
            self.assertEqual(records, JsonRecordOutbox(fixture.path).load())
            self.assert_restored(lease)
        with self.lease() as lease:
            lease.restore(self.baseline_id)
            asyncio.run(scenario(lease))

    def test_file_restore_failure_is_not_combined_cleanup_success(self):
        async def scenario(lease):
            runtime = ReplayRuntimeScope()
            fixture = Top20ReplayOutbox(self.parent)
            outbox = fixture.begin_run(runtime)
            lease.bind_runtime(runtime)
            store = lease.store()
            with runtime.activate():
                await owned_to_thread(store.save_query, 'top20-file-failure', 'ka10001',
                    self.origin.timestamp() + 60, StoredQuery({'fixture': True}, False, ''))
            runtime.begin_shutdown()
            await runtime.drain()
            with patch.object(JsonRecordOutbox, '_write', side_effect=OSError('file restore failure')):
                with self.assertRaises(OSError):
                    restore_top20_replay_baseline(lease, self.baseline_id, fixture, runtime)
            self.assert_restored(lease)  # DB commit alone is insufficient.
            with self.assertRaisesRegex(RuntimeError, 'baseline_not_restored'):
                fixture.begin_run(ReplayRuntimeScope())
            fixture.restore(runtime)
            fixture.require_baseline()
        with self.lease() as lease:
            lease.restore(self.baseline_id)
            asyncio.run(scenario(lease))
