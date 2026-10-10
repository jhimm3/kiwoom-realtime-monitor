"""Ownership and draining gates without a PostgreSQL server.

SQL rollback/sequence behavior belongs to the separate real-PostgreSQL gates.
"""
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from threading import Event, Thread
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from kiwoom_monitor.central_server import diagnostic_replay_baseline as baseline


URL = 'postgresql://kiwoom_monitor_replay:fixture@localhost/kiwoom_monitor_replay_test'
TOKEN = 'a' * 32


class Connection:
    def __init__(self, *, wrong_identity=False):
        self.info = SimpleNamespace(dbname='production' if wrong_identity else baseline.DATABASE_NAME,
                                    user=baseline.ROLE_NAME)
        self.closed = False
        self.autocommit = True
        self.cursor = MagicMock(side_effect=AssertionError('per-call validation SQL added'))

    def close(self):
        self.closed = True
        callback = getattr(self, '_replay_release', None)
        if callback:
            self._replay_release = None
            callback()


class ReplayBaselineLeaseTests(unittest.TestCase):
    def v3(self):
        from kiwoom_monitor.central_server.diagnostic_top20_seed import Top20FixtureClock
        return baseline.ReplayDatabaseLease(URL, TOKEN, baseline_version=3,
            cache_clock=Top20FixtureClock(datetime(2026, 10, 12, tzinfo=timezone.utc)))

    def test_v3_expands_exact_reset_scope_without_changing_v1_v2(self):
        old = baseline.ReplayDatabaseLease(URL, TOKEN)
        lease = self.v3()
        self.assertEqual(baseline.TABLES, old.tables)
        self.assertEqual(baseline.SEQUENCES, old._sequences_scope)
        self.assertEqual(12, len(set(lease.tables) - set(baseline.TABLES_V2)))
        self.assertEqual(6, len(lease._sequences_scope))
        self.assertEqual('replay_baseline_v3', lease._snapshot_schema)
        self.assertEqual('baseline_v3', lease._metadata_table)
        self.assertNotIn('central_execution_account_snapshots_accepted_sequence_seq', lease._sequences_scope)
        self.assertFalse(any('credential' in name or 'news' in name for name in lease.tables))
        with self.assertRaisesRegex(ValueError, 'clock_required'):
            baseline.ReplayDatabaseLease(URL, TOKEN, baseline_version=3)

    def test_v3_sequence_inventory_matches_native_schema_and_parent_is_v1_only(self):
        lease = self.v3()
        statements = '\n'.join(statement for migration in baseline.central_schema_migrations()
                              for statement in migration.postgres_statements)
        for name in lease._sequences_scope:
            table = name.removesuffix('_accepted_sequence_seq')
            self.assertIn('CREATE TABLE IF NOT EXISTS ' + table + ' (accepted_sequence BIGSERIAL', statements)
        cursor = MagicMock()
        cursor.fetchone.side_effect = [(1, 1, 9223372036854775807, 1, 1, False), (10, True)] * 6
        self.assertEqual(set(lease._sequences_scope), set(lease._sequences(cursor)))
        cursor.reset_mock()
        cursor.fetchone.side_effect = [(1, 1, 9223372036854775807, 1, 1, False), (10, False)]
        self.assertEqual(baseline.SEQUENCES, tuple(lease._sequences(cursor, sequences=baseline.SEQUENCES)))
        self.assertEqual(2, cursor.execute.call_count)

    def test_v3_recorded_execution_stays_gated_before_any_database_connection(self):
        from kiwoom_monitor.central_server.diagnostic_recorded_execution import run_owned_recorded_experiment
        with patch.object(baseline, '_maintenance_connection') as connect:
            with self.assertRaisesRegex(ValueError, 'v3_context_and_native_clock_required'):
                run_owned_recorded_experiment(URL, TOKEN, '0' * 64, [], baseline_version=3)
            connect.assert_not_called()

    def test_v3_native_clock_requires_arm_and_keeps_generation_and_origin_fences(self):
        lease = self.v3()
        lease._active, lease._run_ready, lease._generation = True, True, 1
        store = lease.store()
        with self.assertRaisesRegex(RuntimeError, 'armed_v3'):
            store._execution_wall_now()
        lease._cache_clock.arm(start_seconds=2)
        self.assertLess(abs((store._execution_wall_now() - lease._cache_clock.now()).total_seconds()), .1)
        self.assertLess(abs(store._account_input_wall_time() - lease._cache_clock.wall_time()), .1)
        lease._cache_clock._origin += timedelta(days=1)
        with self.assertRaisesRegex(RuntimeError, 'clock_changed'):
            store._execution_wall_now()
        lease._cache_clock._origin -= timedelta(days=1)
        lease._generation += 1
        with self.assertRaisesRegex(RuntimeError, 'retired'):
            store._account_input_wall_time()
        for version in (1, 2):
            options = {} if version == 1 else {'baseline_version': 2, 'cache_clock': lease._cache_clock}
            old = baseline.ReplayDatabaseLease(URL, TOKEN, **options)
            self.assertFalse(hasattr(baseline._ReplayStore(old), '_execution_wall_now'))

    def test_native_lease_uses_owned_time_after_row_lock_without_extending_expiry(self):
        from kiwoom_monitor.central_server.database_execution import _require_execution_ownership
        now = datetime(2026, 10, 12, tzinfo=timezone.utc)
        cursor = MagicMock()
        cursor.fetchone.return_value = ('run:alias', now + timedelta(seconds=1))
        ownership = {'owner_key': 'mock:account', 'owner_token': 'run:alias', 'run_id': 'run'}
        value = {'environment': 'mock', 'account_ref': 'account'}
        def at_source():
            self.assertEqual(1, cursor.execute.call_count)
            self.assertIn('FOR UPDATE', cursor.execute.call_args.args[0])
            return now
        _require_execution_ownership(cursor, ownership, value, '%s', now_provider=at_source)
        with self.assertRaisesRegex(RuntimeError, 'OWNERSHIP_LOST'):
            _require_execution_ownership(cursor, ownership, value, '%s',
                                         now_provider=lambda: now + timedelta(seconds=1))
        with self.assertRaisesRegex(RuntimeError, 'OWNER_SCOPE_MISMATCH'):
            _require_execution_ownership(cursor, {**ownership, 'owner_token': 'wrong'}, value, '%s',
                                         now_provider=lambda: now)

    def ready(self):
        lease = baseline.ReplayDatabaseLease(URL, TOKEN)
        lease._active, lease._run_ready, lease._generation = True, True, 1
        lease.connection = MagicMock()
        return lease

    def test_production_existing_diagnostic_and_url_overrides_rejected_before_connect(self):
        variants = [URL.replace(baseline.DATABASE_NAME, name)
                    for name in ('kiwoom_monitor', 'kiwoom_monitor_diagnostic_test')]
        variants += [URL + '?' + value for value in (
            'dbname=kiwoom_monitor', 'user=postgres', 'service=production',
            'options=-c+search_path=other', '%64bname=production')]
        variants += [URL.replace(baseline.ROLE_NAME + ':', 'postgres:'), URL + '#fragment']
        with patch.object(baseline.psycopg, 'connect') as connect:
            for value in variants:
                with self.subTest(value=value), self.assertRaises(ValueError):
                    baseline.ReplayDatabaseLease(value, TOKEN)
            connect.assert_not_called()

    def test_fresh_per_call_connections_close_once_and_do_not_query_validation(self):
        lease = self.ready()
        first, second = Connection(), Connection()
        with patch.object(baseline._OwnedConnection, 'connect', side_effect=[first, second]):
            store = lease.store()
            self.assertIs(first, store._connect())
            self.assertIs(second, store._connect())
        self.assertEqual(2, lease._open_count)
        self.assertFalse(first.autocommit)
        first.close()
        first.close()
        self.assertEqual(1, lease._open_count)
        second.close()
        self.assertEqual(0, lease._open_count)

    def test_owned_native_exit_closes_proxy_after_commit_ack_error_and_keeps_exception(self):
        proxy = MagicMock()
        failure = OSError('lost native commit acknowledgement')
        with patch.object(baseline.psycopg.Connection, '__exit__', side_effect=failure) as native:
            with self.assertRaises(OSError) as caught:
                baseline._OwnedConnection.__exit__(proxy, None, None, None)
        self.assertIs(failure, caught.exception)
        native.assert_called_once_with(proxy, None, None, None)
        proxy.close.assert_called_once_with()

    def test_connection_identity_failure_closes_and_releases_receipt(self):
        lease = self.ready()
        connection = Connection(wrong_identity=True)
        with patch.object(baseline._OwnedConnection, 'connect', return_value=connection):
            with self.assertRaisesRegex(RuntimeError, 'identity_mismatch'):
                lease.store()._connect()
        self.assertTrue(connection.closed)
        self.assertEqual(0, lease._open_count)

    def test_open_failure_releases_receipt(self):
        lease = self.ready()
        with patch.object(baseline._OwnedConnection, 'connect', side_effect=OSError('connect failed')):
            with self.assertRaises(OSError):
                lease.store()._connect()
        self.assertEqual(0, lease._open_count)

    def test_old_store_is_fenced_after_restore_or_lease_reentry(self):
        lease = self.ready()
        store = lease.store()
        lease._generation += 1
        with patch.object(baseline._OwnedConnection, 'connect') as connect:
            with self.assertRaisesRegex(RuntimeError, 'retired'):
                store._connect()
            connect.assert_not_called()

    def test_restore_refuses_a_connection_that_is_still_opening(self):
        lease = self.ready()
        opening, release = Event(), Event()
        errors = []
        def connect(*args, **kwargs):
            opening.set()
            if not release.wait(2):
                raise AssertionError('test release missing')
            raise OSError('injected opening failure')
        def worker():
            try:
                lease.connect_store(1)
            except OSError as error:
                errors.append(error)
        with patch.object(baseline._OwnedConnection, 'connect', side_effect=connect):
            thread = Thread(target=worker)
            thread.start()
            try:
                self.assertTrue(opening.wait(2))
                with self.assertRaisesRegex(RuntimeError, 'not_drained'):
                    lease._maintenance(MagicMock())
            finally:
                release.set()
                thread.join(2)
        self.assertEqual(1, len(errors))
        self.assertEqual(0, lease._open_count)

    def test_retirement_fences_new_connections_and_waits_for_actual_close(self):
        lease = self.ready()
        connection = Connection()
        with patch.object(baseline._OwnedConnection, 'connect', return_value=connection):
            store = lease.store()
            store._connect()
        retired, done = Event(), Event()
        original_wait = lease._condition.wait
        def wait(*args, **kwargs):
            retired.set()
            return original_wait(*args, **kwargs)
        def retire():
            lease.__exit__(None, None, None)
            done.set()
        with patch.object(lease._condition, 'wait', side_effect=wait):
            thread = Thread(target=retire)
            thread.start()
            try:
                self.assertTrue(retired.wait(2))
                self.assertFalse(done.is_set())
                lease.connection.close.assert_not_called()
                with self.assertRaisesRegex(RuntimeError, 'retired'):
                    store._connect()
            finally:
                connection.close()
                thread.join(2)
        self.assertTrue(done.is_set())
        self.assertIsNone(lease.connection)

    def test_process_busy_rejects_before_database_connect(self):
        baseline._PROCESS_RUN_LOCK.acquire()
        try:
            with patch.object(baseline, '_maintenance_connection') as connect:
                with self.assertRaisesRegex(RuntimeError, 'process_run_busy'):
                    with baseline.ReplayDatabaseLease(URL, TOKEN):
                        self.fail('busy lease entered')
                connect.assert_not_called()
        finally:
            baseline._PROCESS_RUN_LOCK.release()

    def test_database_advisory_busy_releases_connection_and_process_gate(self):
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value.fetchone.return_value = (False,)
        with patch.object(baseline, '_maintenance_connection', return_value=connection), \
             patch.object(baseline, '_identity'):
            with self.assertRaisesRegex(RuntimeError, 'database_run_busy'):
                with baseline.ReplayDatabaseLease(URL, TOKEN):
                    self.fail('busy DB entered')
        connection.close.assert_called_once()
        self.assertTrue(baseline._PROCESS_RUN_LOCK.acquire(blocking=False))
        baseline._PROCESS_RUN_LOCK.release()

    def test_superuser_or_foreign_database_owner_rejected(self):
        valid = (baseline.DATABASE_NAME, baseline.ROLE_NAME, False, False, False,
                 False, False, baseline.ROLE_NAME)
        for index, value in ((0, 'production'), (1, 'postgres'), (2, True),
                             (3, True), (4, True), (5, True), (6, True), (7, 'postgres')):
            row = list(valid)
            row[index] = value
            cursor = MagicMock()
            cursor.fetchone.return_value = tuple(row)
            with self.subTest(index=index), self.assertRaisesRegex(RuntimeError, 'identity_mismatch'):
                baseline._identity(cursor)

    def test_offline_runner_preflight_rejects_bad_selection_before_reset(self):
        from kiwoom_monitor.central_server.diagnostic_recorded_execution import run_owned_recorded_experiment
        from tests.unit.test_recorded_execution import events
        rows = events([('bad', 'actor', 1, 0, 'claim_news_jobs', {'limit': 1})])
        with patch.object(baseline, '_maintenance_connection') as connect:
            with self.assertRaisesRegex(ValueError, 'adapter_missing'):
                run_owned_recorded_experiment(URL, TOKEN, '0' * 64, rows,
                    started_mono_ns=1_000_000_000, window_start_seconds=0, window_end_seconds=.1)
            connect.assert_not_called()

    def test_projection_document_variants_fail_before_baseline_reset(self):
        from kiwoom_monitor.central_server.diagnostic_recorded_execution import run_owned_recorded_experiment
        from tests.unit.test_recorded_execution import events
        for method in ('upsert_documents', 'replace_documents'):
            for collection in ('news_article', 'theme_metadata'):
                rows = events([('projection', 'actor', 1, 0, method,
                                {'collection': collection, 'values': []})])
                with self.subTest(method=method, collection=collection), \
                     patch.object(baseline, '_maintenance_connection') as connect:
                    with self.assertRaisesRegex(ValueError, 'projection_adapter_missing'):
                        run_owned_recorded_experiment(URL, TOKEN, '0' * 64, rows,
                            started_mono_ns=1_000_000_000,
                            window_start_seconds=0, window_end_seconds=.1)
                    connect.assert_not_called()

    def test_wrong_owner_marker_or_external_session_releases_management_lock(self):
        for target in ('_marker', '_no_external_sessions'):
            connection = MagicMock()
            connection.cursor.return_value.__enter__.return_value.fetchone.return_value = (True,)
            location = (patch.object(baseline, target, side_effect=RuntimeError('gate failed'))
                        if target == '_marker' else
                        patch.object(baseline.ReplayDatabaseLease, target, side_effect=RuntimeError('gate failed')))
            with self.subTest(target=target), patch.object(baseline, '_maintenance_connection', return_value=connection), \
                 patch.object(baseline, '_identity'), patch.object(baseline, '_marker') if target != '_marker' else nullcontext(), location:
                with self.assertRaisesRegex(RuntimeError, 'gate failed'):
                    with baseline.ReplayDatabaseLease(URL, TOKEN):
                        self.fail('invalid lease entered')
                connection.close.assert_called_once()
            self.assertTrue(baseline._PROCESS_RUN_LOCK.acquire(blocking=False))
            baseline._PROCESS_RUN_LOCK.release()


if __name__ == '__main__':
    unittest.main()
