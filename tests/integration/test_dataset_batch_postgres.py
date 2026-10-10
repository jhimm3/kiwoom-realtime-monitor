"""Dataset batching contracts on the explicit disposable diagnostic database."""
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event
from urllib.parse import urlsplit
from uuid import uuid4

import psycopg
from kiwoom_monitor.central_server.database import PostgresQueryStore
from kiwoom_monitor.central_server.diagnostic_metrics import summarize_db_calls
from kiwoom_monitor.central_server.market_observations import market_state_observation
from . import test_storage_boundary_postgres as storage


class DatasetBatchPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.url = os.environ.get('KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL', '')
        if not cls.url:
            raise unittest.SkipTest('dedicated diagnostic PostgreSQL required')
        if urlsplit(cls.url).path != '/kiwoom_monitor_diagnostic_test':
            raise RuntimeError('refusing non-diagnostic database')
        cls.store = PostgresQueryStore(cls.url)
        cls.store.initialize()

    def setUp(self):
        self.subject = 'dataset-batch-' + uuid4().hex
        self.subjects = {self.subject}

    def tearDown(self):
        with self.store._connect() as conn, conn.cursor() as cur:
            cur.execute('DELETE FROM central_dataset_snapshots WHERE subject=ANY(%s)', (list(self.subjects),))
            cur.execute('DELETE FROM central_market_data_observation_meta WHERE subject=ANY(%s)', (list(self.subjects),))

    def values(self, count=2, observations=True):
        now = datetime(2099, 1, 14, 1, tzinfo=timezone.utc)
        rows = []
        for i in range(count):
            key, payload = f'key-{i}', {'index': i}
            rows.append(('market_state', self.subject, key, payload,
                market_state_observation(self.subject, key, payload, now) if observations else None))
        return rows

    def count(self, subject):
        with self.store._connect() as conn, conn.cursor() as cur:
            cur.execute('SELECT count(*) FROM central_dataset_snapshots WHERE subject=%s', (subject,))
            return cur.fetchone()[0]

    def test_batch_repeat_preserves_payload_metadata_and_freshness_with_three_sql(self):
        import time
        rows = self.values()
        with storage._writer_metrics_capture():
            start = time.time()
            self.store.save_dataset_snapshots(rows)
            before = self.store.load_dataset_snapshots('market_state', self.subject, 10)
            self.store.save_dataset_snapshots(rows)
            after = self.store.load_dataset_snapshots('market_state', self.subject, 10)
            calls = [c for c in summarize_db_calls(start, time.time()+1, mode='raw')['calls']
                     if c['writer_family'] == 'dataset.snapshot']
        self.assertEqual([3, 3], [c['sql_calls'] for c in calls])
        self.assertEqual([1, 1], [c['commits'] for c in calls])
        self.assertEqual([(r['snapshot_key'], r['payload']) for r in before],
                         [(r['snapshot_key'], r['payload']) for r in after])
        self.assertGreater(after[0]['saved_at'], before[0]['saved_at'])
        self.assertEqual(1, len({r['saved_at'] for r in after}))
        with self.store._connect() as conn, conn.cursor() as cur:
            cur.execute('SELECT observation_key,available_at,source FROM central_market_data_observation_meta '
                        'WHERE subject=%s ORDER BY observation_key', (self.subject,))
            metadata = cur.fetchall()
        self.assertEqual(['key-0', 'key-1'], [r[0] for r in metadata])
        self.assertEqual(1, len({(r[1], r[2]) for r in metadata}))

    def test_duplicate_snapshot_and_metadata_keys_keep_sequential_last_value(self):
        import time
        rows = self.values()
        rows[1] = (*rows[0][:3], {'index': 99}, rows[0][4])
        with storage._writer_metrics_capture():
            start = time.time()
            self.store.save_dataset_snapshots(rows)
            self.assertEqual({'index': 99}, self.store.load_dataset_snapshots(
                'market_state', self.subject, 10)[0]['payload'])
            other = self.subject + '-other'
            self.subjects.add(other)
            metadata_shared = [rows[0], ('market_state', other, rows[0][2], {'index': 7}, rows[0][4])]
            self.store.save_dataset_snapshots(metadata_shared)
            calls = [c for c in summarize_db_calls(start, time.time()+1, mode='raw')['calls']
                     if c['writer_family'] == 'dataset.snapshot']
        self.assertEqual([5, 5], [c['sql_calls'] for c in calls])
        self.assertEqual(1, self.count(other))
        self.assertEqual({'index': 0}, self.store.load_dataset_snapshots('market_state', self.subject, 10)[0]['payload'])

    def test_later_chunk_failure_rolls_back_without_undoing_independent_peer(self):
        entered, release = Event(), Event()
        class FailingCursor(psycopg.Cursor):
            snapshots = 0
            def execute(self, query, params=None, **options):
                if isinstance(query, str) and query.startswith('INSERT INTO central_dataset_snapshots'):
                    self.snapshots += 1
                    if self.snapshots == 2:
                        entered.set()
                        if not release.wait(10):
                            raise RuntimeError('peer not completed')
                        return super().execute('SELECT 1/0')
                return super().execute(query, params, **options)
        failed = PostgresQueryStore(self.url)
        failed._connect = lambda: psycopg.connect(self.url, cursor_factory=FailingCursor)
        peer = self.subject + '-peer'
        self.subjects.add(peer)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(failed.save_dataset_snapshots, self.values(1001, observations=False))
            try:
                self.assertTrue(entered.wait(10))
                self.store.save_dataset_snapshots([('market_state', peer, 'peer', {}, None)])
            finally:
                release.set()
            with self.assertRaises(psycopg.errors.DivisionByZero):
                future.result(10)
        self.assertEqual(0, self.count(self.subject))
        self.assertEqual(1, self.count(peer))
        self.store.save_dataset_snapshots(self.values(1001, observations=False))
        self.assertEqual(1001, self.count(self.subject))

    def test_committed_ack_loss_retry_keeps_two_rows_and_closes_connection(self):
        class LostAck(psycopg.Connection):
            def commit(self):
                super().commit()
                raise OSError('injected acknowledgement loss')
        failed = PostgresQueryStore(self.url)
        connection = LostAck.connect(self.url)
        failed._connect = lambda: connection
        with self.assertRaisesRegex(OSError, 'acknowledgement loss'):
            failed.save_dataset_snapshots(self.values())
        self.assertTrue(connection.closed)
        self.assertEqual(2, self.count(self.subject))
        self.store.save_dataset_snapshots(self.values())
        self.assertEqual(2, self.count(self.subject))
