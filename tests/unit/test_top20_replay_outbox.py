"""Strict real-file seeds and independent DB/file cleanup completion."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from kiwoom_monitor.central_server.diagnostic_replay_runtime import ReplayRuntimeScope, owned_to_thread
from kiwoom_monitor.central_server.diagnostic_top20_outbox import (
    Top20ReplayOutbox, restore_top20_replay_baseline,
)
from kiwoom_monitor.central_server.persistent_outbox import JsonRecordOutbox


def record():
    minute = '2026-10-06T09:00:00+09:00'
    return {minute: {'minute': minute, 'market_values': [1.0, 2.0, 0.0],
        'codes': ['005930'], 'market_counts': [1, 0, 0],
        'cohort_segments': [[minute, ['005930']]], 'capture_state': 'realtime_complete', 'total': 3.0}}


class Top20ReplayOutboxTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.parent = Path(self.temporary.name)

    async def drained(self, runtime):
        runtime.begin_shutdown()
        await runtime.drain()

    async def test_three_native_file_runs_restore_identical_pending_seed(self):
        fixture = Top20ReplayOutbox(self.parent, records=record())
        identity = fixture.identity()
        sentinel = self.parent / 'production.json'
        sentinel.write_text('untouched')
        for _ in range(3):
            runtime = ReplayRuntimeScope()
            outbox = fixture.begin_run(runtime)
            self.assertIs(type(outbox), JsonRecordOutbox)
            self.assertEqual(record(), outbox.load())
            with runtime.activate():
                await owned_to_thread(outbox.remove, next(iter(record())))
            self.assertEqual({}, JsonRecordOutbox(fixture.path).load())
            with self.assertRaisesRegex(RuntimeError, 'not_drained'):
                fixture.restore(runtime)
            await self.drained(runtime)
            lease = MagicMock()
            result = restore_top20_replay_baseline(lease, 'baseline', fixture, runtime)
            self.assertTrue(result['cleanup_complete'])
            lease.restore.assert_called_once_with('baseline')
            self.assertEqual(identity, fixture.identity())
        self.assertEqual('untouched', sentinel.read_text())

    async def test_corrupt_duplicate_and_oversized_file_rejected_before_native_load(self):
        for data in (b'{', b'[]', b'{"a":{},"a":{}}', b'x' * (8 * 1024 * 1024 + 1)):
            fixture = Top20ReplayOutbox(self.parent)
            fixture.path.write_bytes(data)
            with self.subTest(size=len(data)), patch.object(JsonRecordOutbox, 'load') as load:
                with self.assertRaises(ValueError):
                    fixture.begin_run(ReplayRuntimeScope())
                load.assert_not_called()

    async def test_foreign_file_owner_and_root_replacement_reject_before_db_reset(self):
        for changed in ('foreign', 'owner', 'root', 'seed'):
            fixture = Top20ReplayOutbox(self.parent)
            runtime = ReplayRuntimeScope()
            fixture.begin_run(runtime)
            await self.drained(runtime)
            if changed == 'foreign':
                (fixture.root / 'foreign.txt').write_text('keep')
            elif changed == 'owner':
                (fixture.root / '.owner').write_bytes(b'{}')
            elif changed == 'root':
                retained = fixture.root.with_name(fixture.root.name + '-original')
                fixture.root.rename(retained)
                fixture.root.mkdir()
                for name in ('.owner', fixture.path.name):
                    (fixture.root / name).write_bytes((retained / name).read_bytes())
            else:
                fixture.seed_bytes = b'{"other":{}}'
            lease = MagicMock()
            with self.subTest(changed=changed), self.assertRaises((ValueError, RuntimeError)):
                restore_top20_replay_baseline(lease, 'baseline', fixture, runtime)
            lease.restore.assert_not_called()

    async def test_failed_replace_prevents_next_run_and_retry_cleans_only_native_temporary(self):
        fixture = Top20ReplayOutbox(self.parent, records=record())
        runtime = ReplayRuntimeScope()
        outbox = fixture.begin_run(runtime)
        with runtime.activate():
            await owned_to_thread(outbox.remove, next(iter(record())))
        await self.drained(runtime)
        lease = MagicMock()
        with patch.object(Path, 'replace', side_effect=OSError('replace failed')):
            with self.assertRaises(OSError):
                restore_top20_replay_baseline(lease, 'baseline', fixture, runtime)
        lease.restore.assert_called_once()
        with self.assertRaisesRegex(RuntimeError, 'baseline_not_restored'):
            fixture.begin_run(ReplayRuntimeScope())
        self.assertTrue(list(fixture.root.glob('*.tmp-*')))
        fixture.restore(runtime)
        self.assertEqual([], list(fixture.root.glob('*.tmp-*')))
        self.assertEqual(record(), JsonRecordOutbox(fixture.path).load())
        fixture.require_baseline()

    async def test_wrong_runtime_cannot_restore_another_runs_file(self):
        fixture = Top20ReplayOutbox(self.parent)
        runtime, peer = ReplayRuntimeScope(), ReplayRuntimeScope()
        fixture.begin_run(runtime)
        await self.drained(runtime)
        await self.drained(peer)
        with self.assertRaisesRegex(RuntimeError, 'owner_changed'):
            fixture.restore(peer)

    async def test_native_start_recovers_ack_lost_pending_file_on_restart(self):
        from datetime import datetime
        from kiwoom_monitor.central_server.autonomous_top20 import AutonomousTop20Service
        from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
        from kiwoom_monitor.central_server.diagnostic_replay_runtime import close_top20_replay_resources
        fixture = Top20ReplayOutbox(self.parent, records=record())
        runtime = ReplayRuntimeScope()
        fixture.begin_run(runtime)
        saved = {}
        calls = []
        class Store:
            def save_dataset_snapshot(self, kind, subject, key, payload, **kwargs):
                saved[key] = payload
                calls.append(key)
                if len(calls) == 1:
                    raise OSError('injected loss after actual commit')
        async def waiting():
            await asyncio.Event().wait()
        def service():
            value = AutonomousTop20Service(MagicMock(), RealtimeHub(), Store(),
                outbox_path=fixture.path, now_provider=lambda: datetime.fromisoformat(next(iter(record()))))
            for name in ('_schedule_loop', '_event_loop', '_index_loop'):
                setattr(value, name, waiting)
            return value
        with runtime.activate():
            first = service()
            await first.start()
            self.assertEqual(record(), JsonRecordOutbox(fixture.path).load())
            await first.close()
            self.assertEqual(record(), JsonRecordOutbox(fixture.path).load())
            restarted = service()
            await restarted.start()
            self.assertEqual({}, JsonRecordOutbox(fixture.path).load())
        receipt = await close_top20_replay_resources(runtime, restarted, None)
        self.assertEqual([next(iter(record()))] * 2, calls)
        self.assertEqual(record(), saved)
        self.assertFalse(receipt['execution_succeeded'])
        self.assertEqual(1, receipt['error_count'])
        fixture.restore(runtime)
        fixture.require_baseline()

    async def test_real_file_outbox_identity_is_bound_to_cold_seed_without_authorizing_execution(self):
        from tests.unit.test_top20_fixture_seed import Top20FixtureSeedTests
        from kiwoom_monitor.central_server.diagnostic_top20_seed import seal_top20_cold_fixture
        existing = Top20FixtureSeedTests()
        existing.setUp()
        service, clock = existing.fixture()
        fixture = Top20ReplayOutbox(self.parent, records=record())
        service._outbox = JsonRecordOutbox(fixture.path)
        seed = seal_top20_cold_fixture(service, clock, baseline=existing.baseline,
            source_manifest=existing.manifest, outbox_fixture=fixture)
        document = json.loads(seed.document)
        self.assertEqual('top20-cold-fixture/v2', document['version'])
        self.assertEqual(fixture.identity(), document['ram_state']['configuration']['durable_outbox'])
        report = existing.preflight(seed, service, clock, outbox_fixture=fixture)
        self.assertFalse(report['execution_authorized'])
