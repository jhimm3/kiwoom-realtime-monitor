"""Native TOP20/peer correctness gates on a disposable sealed v2 database."""
import asyncio
from datetime import datetime
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kiwoom_monitor.central_server import diagnostic_replay_baseline as baseline
from kiwoom_monitor.central_server import diagnostic_trace as trace
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.diagnostic_top20_experiment import execute_owned_top20_fixture
from kiwoom_monitor.central_server.diagnostic_top20_seed import Top20FixtureClock
from tests.top20_native_fixture import capture_native_top20_fixture


class Top20SessionPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.url = os.environ.get('KIWOOM_REPLAY_DATABASE_URL', '')
        cls.token = os.environ.get('KIWOOM_REPLAY_OWNER_TOKEN', '')
        origin = os.environ.get('KIWOOM_REPLAY_SOURCE_ORIGIN', '')
        if (not cls.url or not cls.token or not origin
                or os.environ.get('KIWOOM_REPLAY_TEMPORARY_FIXTURE') != '1'):
            raise unittest.SkipTest('disposable sealed v2 TOP20 fixture is required')
        cls.origin = datetime.fromisoformat(origin)
        if cls.origin.strftime('%H:%M:%S') != '09:30:01':
            raise unittest.SkipTest('use the --top20-lifecycle disposable acceptance harness')
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.parent = Path(cls.temporary.name)
        store = SQLiteQueryStore(cls.parent / 'source.sqlite3')
        store.initialize()
        try:
            cls.fixture = asyncio.run(capture_native_top20_fixture(store, cls.origin,
                capture_store=True, capture_seconds=20, include_peer=True,
                preparation_timeout=180))
        finally:
            store.close()
        with cls.lease() as lease:
            with lease.connection.cursor() as cursor:
                cls.baseline_id, cls.manifest = lease._baseline(cursor)
                cursor.execute('SELECT baseline_id,manifest FROM replay_meta.baseline WHERE singleton')
                cls.v1_before = cursor.fetchone(), lease._tables_digest(cursor, 'replay_baseline', tables=baseline.TABLES)
            lease.restore(cls.baseline_id)

    @classmethod
    def lease(cls):
        return baseline.ReplayDatabaseLease(cls.url, cls.token, baseline_version=2,
                                            cache_clock=Top20FixtureClock(cls.origin))

    def assert_restored(self, lease):
        self.assertEqual(0, lease.status()['owned_connections'])
        with lease.connection.cursor() as cursor:
            self.assertEqual(self.manifest['tables'], lease._tables_digest(cursor, 'public'))
            self.assertEqual(self.manifest['sequences'], lease._sequences(cursor))
            cursor.execute('SELECT baseline_id,manifest FROM replay_meta.baseline WHERE singleton')
            self.assertEqual(self.v1_before, (cursor.fetchone(),
                lease._tables_digest(cursor, 'replay_baseline', tables=baseline.TABLES)))

    def execute(self, lease, **selection):
        fixture = self.fixture
        with patch.object(trace, 'input_token', return_value=None), patch(
                'kiwoom_monitor.central_server.realtime_collector.REALTIME_REG_INTERVAL_SECONDS', 0):
            return asyncio.run(execute_owned_top20_fixture(lease, self.baseline_id,
                events=fixture['events'], manifest=fixture['manifest'], task_bindings=fixture['roles'],
                component=fixture['component'], source_hub_component=fixture['hub'],
                source_collector_component=fixture['collector'], outbox_parent=self.parent,
                end_seconds=20, minute_backfill_enabled=False, **selection))

    def test_three_native_runs_and_whole_service_exclusion_restore_db_sequence_and_files(self):
        memberships, identities, stage_results = [], [], []
        for enabled, peers in ((True, ()), (True, ()), (True, ()), (False, ()), (True, ('shadow',))):
            with self.lease() as lease:
                connections = []
                original = baseline._ReplayStore._connect
                def connect(store):
                    value = original(store)
                    connections.append(value)
                    return value
                with patch.object(baseline._ReplayStore, '_connect', connect):
                    report = self.execute(lease, include_top20=enabled, exclude_peer_workloads=peers)
                self.assertTrue(report['execution_succeeded'], report)
                self.assertTrue(report['cleanup']['cleanup_complete'])
                self.assertFalse(report['source_state_equivalent'])
                self.assertFalse(report['full_experiment_acceptance'])
                self.assertFalse(report['file_checksums_verified'])
                self.assertTrue(report['final_database_snapshot_verified'])
                self.assertEqual(0, report['runtime']['pending_threads'])
                self.assertEqual(0, report['runtime']['pending_tasks'])
                self.assertEqual(len(connections), len({id(value) for value in connections}))
                self.assertTrue(all(value.closed for value in connections))
                self.assert_restored(lease)
                peer = report['peer_execution']
                self.assertEqual(None if peers else 'complete', peer['state'] if peer else None)
                membership_rows = report['final_tables']['central_dataset_snapshots']['rows']
                self.assertGreaterEqual(membership_rows, 1 if enabled else 0)
                self.assertEqual(1 if enabled else 0, report['final_tables']['central_minute_bars']['rows'])
                self.assertEqual(1 if enabled else 0, report['final_tables']['central_second_trade_bars']['rows'])
                self.assertGreaterEqual(report['final_tables']['central_observation_revisions']['rows'],
                                        1 if enabled else 0)
                if enabled and not peers:
                    memberships.append(report['native_membership']['payload'])
                    identities.append(report['experiment_id'])
                    stage_results.append(report['native_stage_ready'])
                if not enabled:
                    self.assertEqual(0, membership_rows)
                    self.assertIsNone(report['native_membership'])
                    self.assertEqual([], report['subscription']['subscriptions'])
        self.assertEqual(memberships[0], memberships[1])
        self.assertEqual(memberships[0], memberships[2])
        self.assertEqual(stage_results[0], stage_results[1])
        self.assertEqual(stage_results[0], stage_results[2])
        self.assertEqual(1, len(set(identities)))
        for path in self.parent.glob('top20-replay-*/top20-index.json'):
            self.assertEqual(b'{}', path.read_bytes())

    def test_peer_commit_ack_loss_is_failed_after_actual_commit_and_restores_both_baselines(self):
        original = baseline._ReplayStore.upsert_documents
        committed = []
        def lost_ack(store, collection, values):
            result = original(store, collection, values)
            if collection == 'top20_daily_entrants' and any(row.get('owner') == 'peer-fixture' for row in values):
                committed.append(True)
                raise OSError('injected peer COMMIT acknowledgement loss')
            return result
        with self.lease() as lease, patch.object(baseline._ReplayStore, 'upsert_documents', lost_ack):
            try:
                report = self.execute(lease, include_top20=False)
            except RuntimeError as error:
                self.assertEqual('top20_session_peer_execution_incomplete', str(error))
            else:
                self.assertFalse(report['execution_succeeded'])
                self.assertEqual('incomplete', report['peer_execution']['state'])
                self.assertTrue(report['cleanup']['cleanup_complete'])
            self.assertEqual([True], committed)
            self.assert_restored(lease)
