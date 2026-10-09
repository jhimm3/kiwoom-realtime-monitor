"""Native sequence-lock probes and safe-reader delivery acceptance.

Dedicated PostgreSQL only; these gates do not deploy or run historical decisions.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import os
from threading import Event
from time import monotonic
import unittest
from urllib.parse import urlsplit
import uuid

from kiwoom_monitor.central_server.database import PostgresQueryStore
from kiwoom_monitor.central_server.market_observations import (
    bar_observation_key, minute_bar_observation, ranking_observation,
)
from kiwoom_monitor.domain.market_data_contract import (
    DataCompleteness, DataValueKind, ObservationOrigin,
)
from tests.integration.postgres_test_support import isolated_observation_schema


class ObservationSequenceFencePostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.pg = psycopg
        cls.url = os.environ.get('KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL', '')
        if not cls.url:
            raise unittest.SkipTest('dedicated diagnostic PostgreSQL URL is required')
        if urlsplit(cls.url).path != '/kiwoom_monitor_diagnostic_test':
            raise RuntimeError('sequence fence probe requires diagnostic database')
        cls.url = cls.enterClassContext(isolated_observation_schema(cls.url))
        cls.store = PostgresQueryStore(cls.url)
        cls.store.initialize()

    def setUp(self):
        self.token = uuid.uuid4().hex[:12]
        self.codes = ['SF' + self.token + str(i) for i in range(3)]
        self.subjects = [code + ':KRX' for code in self.codes] + ['rank-' + self.token]
        self.gates = []
        self.pool = ThreadPoolExecutor(max_workers=3)

    def tearDown(self):
        for _, release in self.gates:
            release.set()
        self.pool.shutdown(wait=True)
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute('DELETE FROM central_observation_revisions WHERE subject=ANY(%s)', (self.subjects,))
            cursor.execute('DELETE FROM central_market_data_observation_meta WHERE subject=ANY(%s)', (self.subjects,))
            cursor.execute('DELETE FROM central_dataset_snapshots WHERE subject=ANY(%s)', (self.subjects,))
            cursor.execute('DELETE FROM central_minute_bar_operations WHERE operation_id=ANY(%s)',
                           (['fence-' + code for code in self.codes],))
            cursor.execute('DELETE FROM central_minute_bars WHERE code=ANY(%s)', (self.codes,))

    def _held_store(self, *, rollback=False):
        entered, release = Event(), Event()
        self.gates.append((entered, release))

        class HeldCommit(self.pg.Connection):
            def commit(connection):
                entered.set()
                if not release.wait(30):
                    raise TimeoutError('test COMMIT gate timed out')
                if rollback:
                    connection.rollback()
                    raise OSError('injected native rollback')
                return super().commit()

        store = PostgresQueryStore(self.url)
        store._connect = lambda: HeldCommit.connect(self.url)
        return store, entered, release

    def _save(self, store, index=0, *, scalar=False):
        now = datetime(2099, 1, 14, 1, 1, 10, tzinfo=timezone.utc)
        if scalar:
            subject = self.subjects[-1]
            key = '2099-01-14T10:01:10+09:00'
            value = {'rows': [{'code': self.codes[index], 'rank': 1}]}
            store.save_dataset_snapshot('ranking', subject, key, value,
                observation=ranking_observation(subject, key, value, now, source='fence-proof'))
            return
        value = dict(trading_date='2099-01-14', minute='10:01', code=self.codes[index],
                     market='KRX', open=100, high=110, low=90, close=105, volume=10,
                     trade_value_million_won=2, updated_at=now.timestamp(),
                     operation_id='fence-' + self.codes[index])
        observation = minute_bar_observation(value, origin=ObservationOrigin.REALTIME,
            completeness=DataCompleteness.IN_PROGRESS, source='kiwoom-websocket-0B',
            value_kind=DataValueKind.ACTUAL)
        store.save_minute_bars([value], observations=[(bar_observation_key(observation), observation)])

    def _sequence(self, cursor):
        cursor.execute("SELECT pg_get_serial_sequence('central_observation_revisions','accepted_sequence')::regclass::oid")
        [oid] = cursor.fetchone()
        cursor.execute('SELECT seqcache,seqincrement,seqcycle FROM pg_sequence WHERE seqrelid=%s', (oid,))
        self.assertEqual((1, 1, False), cursor.fetchone(), 'proof requires CACHE 1, +1, NO CYCLE')
        cursor.execute('SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.oid=%s', (oid,))
        schema, name = cursor.fetchone()
        return oid, self.pg.sql.Identifier(schema, name)

    def _holders(self, cursor, oid):
        cursor.execute("SELECT pid,virtualtransaction,mode,fastpath FROM pg_locks "
                       "WHERE locktype='relation' AND relation=%s AND database="
                       "(SELECT oid FROM pg_database WHERE datname=current_database()) "
                       "AND granted AND mode NOT IN ('AccessShareLock','RowShareLock')", (oid,))
        rows = cursor.fetchall()
        self.assertTrue(all(row[0] is not None for row in rows), 'prepared transactions are outside this proof')
        return {(row[0], row[1]) for row in rows}

    def _fence(self):
        with self.pg.connect(self.url, autocommit=True) as connection, connection.cursor() as cursor:
            oid, name = self._sequence(cursor)
            # Ordering is essential: high bound first, lock cohort second.
            cursor.execute(self.pg.sql.SQL('SELECT last_value,is_called FROM {}').format(name))
            high, called = cursor.fetchone()
            return oid, high if called else high - 1, self._holders(cursor, oid)

    def _read(self, fence):
        oid, high, cohort = fence
        with self.pg.connect(self.url, autocommit=True) as connection, connection.cursor() as cursor:
            if cohort & self._holders(cursor, oid):
                return None
            # A separate READ COMMITTED statement AFTER observing cohort completion.
            cursor.execute('SELECT accepted_sequence,subject FROM central_observation_revisions '
                           'WHERE accepted_sequence<=%s AND subject=ANY(%s) ORDER BY accepted_sequence',
                           (high, self.subjects))
            return cursor.fetchall()

    def _native_case(self, *, scalar=False, rollback=False, native_reader=False):
        reader = PostgresQueryStore(self.url)
        held, entered, release = self._held_store(rollback=rollback)
        first = self.pool.submit(self._save, held, 0, scalar=scalar)
        self.assertTrue(entered.wait(10))
        # The independent peer must commit while lower sequence is held.
        self.pool.submit(self._save, self.store, 1).result(timeout=10)
        fence = self._fence()
        self.assertTrue(fence[2], 'native allocation must retain sequence relation lock')
        self.assertIsNone(self._read(fence))
        if native_reader:
            page = reader.load_observation_revision_page(0, ('minute_bar', 'ranking'))
            self.assertFalse(page.ready)
            self.assertEqual('pending_sequence_commit', page.reason)
        later_store, later_entered, later_release = self._held_store()
        later = self.pool.submit(self._save, later_store, 2)
        self.assertTrue(later_entered.wait(10))
        release.set()
        if rollback:
            with self.assertRaisesRegex(OSError, 'injected native rollback'):
                first.result(timeout=10)
        else:
            first.result(timeout=10)
        rows = self._read(fence)
        expected = [self.subjects[1]] if rollback else [self.subjects[-1] if scalar else self.subjects[0], self.subjects[1]]
        self.assertEqual(expected, [row[1] for row in rows])
        self.assertFalse(later.done(), 'new writers must not extend the captured cohort')
        if native_reader:
            page = reader.load_observation_revision_page(0, ('minute_bar', 'ranking'), 1)
            self.assertTrue(page.ready)
            self.assertEqual(expected[:1], [row['subject'] for row in page.rows])
            self.assertEqual(len(expected) <= 1, page.exhausted)
            tail = reader.load_observation_revision_page(page.rows[-1]['accepted_sequence'],
                                                        ('minute_bar', 'ranking'))
            self.assertEqual(expected[1:], [row['subject'] for row in tail.rows])
            seed = reader.load_observation_bootstrap(('minute_bar', 'ranking'))
            self.assertTrue(seed.ready)
            self.assertEqual(expected, [row['subject'] for row in seed.rows])
            self.assertLessEqual(max(row['accepted_sequence'] for row in seed.rows), fence[1])
        later_release.set()
        later.result(timeout=10)
        self.assertEqual(expected + [self.subjects[2]], [row[1] for row in self._read(self._fence())])
        if native_reader:
            final = reader.load_observation_revisions_after(
                max(row['accepted_sequence'] for row in seed.rows), ('minute_bar', 'ranking'))
            self.assertEqual([self.subjects[2]], [row['subject'] for row in final])

    def test_native_batch_late_commit_and_new_writer_do_not_lose_or_starve_prefix(self):
        self._native_case()

    def test_native_scalar_default_and_batch_share_sequence_fence(self):
        self._native_case(scalar=True)

    def test_native_rollback_gap_does_not_block_or_fabricate_input(self):
        self._native_case(rollback=True)

    def test_native_reader_delivers_late_batch_once_and_keeps_peer_independent(self):
        self._native_case(native_reader=True)

    def test_native_reader_bootstraps_scalar_and_batch_under_one_safe_bound(self):
        self._native_case(scalar=True, native_reader=True)

    def test_native_reader_does_not_wait_for_rollback_gap(self):
        self._native_case(rollback=True, native_reader=True)

    def test_concurrent_native_probes_discard_old_snapshot_after_peer_publishes(self):
        reader = PostgresQueryStore(self.url)
        held, entered, release = self._held_store()
        writer = self.pool.submit(self._save, held, 0)
        self.assertTrue(entered.wait(10))
        self._save(self.store, 1)
        probe_entered, probe_release = Event(), Event()
        self.gates.append((probe_entered, probe_release))
        original = reader._observation_delivery.advance

        def gated_advance(version, epoch, high, owners):
            # Delay publication, not the SQL: both readers use actual native
            # probes/connections and the production state version comparison.
            if not probe_entered.is_set():
                probe_entered.set()
                if not probe_release.wait(30):
                    raise TimeoutError('old frontier publication gate timed out')
            return original(version, epoch, high, owners)

        reader._observation_delivery.advance = gated_advance
        older = self.pool.submit(reader.load_observation_revision_page, 0, ('minute_bar',))
        self.assertTrue(probe_entered.wait(10))
        release.set()
        writer.result(timeout=10)
        newer = reader.load_observation_revision_page(0, ('minute_bar',))
        self.assertTrue(newer.ready)
        self.assertEqual(self.subjects[:2], [r['subject'] for r in newer.rows
                                           if r['subject'] in self.subjects])
        probe_release.set()
        stale = older.result(timeout=10)
        self.assertFalse(stale.ready)
        self.assertEqual('concurrent_frontier_refresh', stale.reason)
        self.assertEqual((), stale.rows)
        self._save(self.store, 2)
        tail = reader.load_observation_revision_page(
            max(r['accepted_sequence'] for r in newer.rows), ('minute_bar',))
        self.assertTrue(tail.ready)
        self.assertEqual([self.subjects[2]], [r['subject'] for r in tail.rows])

    def test_native_fastpath_lock_transfer_preserves_pending_owner_and_rollback_gap(self):
        reader = PostgresQueryStore(self.url)
        with self.pg.connect(self.url) as allocator, allocator.cursor() as allocation:
            oid, name = self._sequence(allocation)
            allocation.execute(self.pg.sql.SQL('SELECT nextval({}::regclass)').format(
                self.pg.sql.Literal(name.as_string(allocator))))
            [reserved] = allocation.fetchone()
            pid = allocator.info.backend_pid

            def locks():
                with self.pg.connect(self.url, autocommit=True) as connection, connection.cursor() as cursor:
                    cursor.execute("SELECT pid,virtualtransaction,mode,fastpath,granted FROM pg_locks "
                                   "WHERE relation=%s AND locktype='relation'", (oid,))
                    return cursor.fetchall()

            before = [r for r in locks() if r[0] == pid and r[2] == 'RowExclusiveLock']
            self.assertEqual(1, len(before))
            self.assertTrue(before[0][3], 'gate must exercise a real fast-path sequence lock')
            self._save(self.store, 1)
            pending = reader.load_observation_revision_page(0, ('minute_bar',))
            self.assertFalse(pending.ready)
            self.assertEqual('pending_sequence_commit', pending.reason)

            def alter_sequence():
                with self.pg.connect(self.url, autocommit=True,
                                     options='-c statement_timeout=20000') as ddl, ddl.cursor() as cursor:
                    cursor.execute(self.pg.sql.SQL('ALTER SEQUENCE {} CACHE 1').format(name))

            ddl = self.pool.submit(alter_sequence)
            deadline = monotonic() + 10
            while True:
                transferred = locks()
                waiting = [r for r in transferred if not r[4]]
                if waiting:
                    break
                if monotonic() >= deadline:
                    self.fail('sequence DDL never entered lock wait')
                Event().wait(.02)
            after = [r for r in transferred if r[0] == pid and r[2] == 'RowExclusiveLock']
            self.assertEqual(1, len(after))
            self.assertFalse(after[0][3], 'waiting strong lock must transfer fast-path entry')
            self.assertEqual(before[0][1], after[0][1], 'transfer must retain transaction identity')
            again = reader.load_observation_revision_page(0, ('minute_bar',))
            self.assertFalse(again.ready)
            self.assertFalse(ddl.done())
            allocator.rollback()
            ddl.result(timeout=10)
            final = reader.load_observation_revision_page(0, ('minute_bar',))
            self.assertTrue(final.ready)
            self.assertEqual([self.subjects[1]], [r['subject'] for r in final.rows
                                               if r['subject'] in self.subjects])
            self.assertNotIn(reserved, [r['accepted_sequence'] for r in final.rows])

    def test_native_reader_rejects_reserved_sequence_cache_and_stale_snapshot_isolation(self):
        reader = PostgresQueryStore(self.url)
        with self.pg.connect(self.url, autocommit=True) as connection, connection.cursor() as cursor:
            _, name = self._sequence(cursor)
            try:
                cursor.execute(self.pg.sql.SQL('ALTER SEQUENCE {} CACHE 2').format(name))
                with self.assertRaisesRegex(RuntimeError, 'configuration_unsupported'):
                    reader.load_observation_revision_page(0, ('minute_bar',))
            finally:
                cursor.execute(self.pg.sql.SQL('ALTER SEQUENCE {} CACHE 1').format(name))
        # Keep the owned schema while intentionally changing only isolation.
        options = self.pg.conninfo.conninfo_to_dict(self.url)['options']
        reader._connect = lambda: self.pg.connect(self.url,
            options=options + r' -c default_transaction_isolation=repeatable\ read')
        with self.assertRaisesRegex(RuntimeError, 'configuration_unsupported'):
            reader.load_observation_revision_page(0, ('minute_bar',))

    def test_native_reader_rejects_backward_sequence_without_hiding_committed_rows(self):
        self._save(self.store, 0)
        reader = PostgresQueryStore(self.url)
        first = reader.load_observation_revision_page(0, ('minute_bar',))
        self.assertTrue(first.ready)
        with self.pg.connect(self.url, autocommit=True) as connection, connection.cursor() as cursor:
            _, name = self._sequence(cursor)
            cursor.execute(self.pg.sql.SQL('SELECT last_value FROM {}').format(name))
            [original] = cursor.fetchone()
            try:
                cursor.execute('SELECT setval(%s::regclass,%s,true)', (name.as_string(connection), original + 10))
                reader.load_observation_revision_page(0, ('minute_bar',))
                cursor.execute('SELECT setval(%s::regclass,%s,true)', (name.as_string(connection), original))
                with self.assertRaisesRegex(RuntimeError, 'sequence_regressed'):
                    reader.load_observation_revision_page(0, ('minute_bar',))
            finally:
                cursor.execute('SELECT setval(%s::regclass,%s,true)', (name.as_string(connection), original + 10))
        [stored] = self.store.load_observation_revisions('minute_bar', self.subjects[0])
        self.assertEqual(first.rows[0]['revision_id'], stored['revision_id'])

    def test_preinsert_savepoint_rollback_and_backend_reuse_keep_transaction_identity(self):
        with self.pg.connect(self.url) as writer, writer.cursor() as cursor:
            oid, name = self._sequence(cursor)
            cursor.execute('SAVEPOINT before_allocation')
            cursor.execute(self.pg.sql.SQL('SELECT nextval({}::regclass)').format(self.pg.sql.Literal(name.as_string(writer))))
            [reserved] = cursor.fetchone()
            cursor.execute('ROLLBACK TO SAVEPOINT before_allocation')
            fence = self._fence()
            self.assertGreaterEqual(fence[1], reserved)
            self.assertIsNone(self._read(fence), 'sequence lock belongs to top transaction before INSERT')
            writer.rollback()
            cursor.execute(self.pg.sql.SQL('SELECT nextval({}::regclass)').format(self.pg.sql.Literal(name.as_string(writer))))
            [later] = cursor.fetchone()
            self.assertGreater(later, fence[1])
            self.assertEqual([], self._read(fence), 'same backend new transaction must not extend old cohort')
            self.assertIsNone(self._read(self._fence()))
            writer.rollback()
