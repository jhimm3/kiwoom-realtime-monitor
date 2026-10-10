"""Account prerequisite projection, durable reconstruction and atomic seal gates."""
import copy
import asyncio
import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from kiwoom_monitor.central_server import diagnostic_account_context as context
from kiwoom_monitor.central_server import diagnostic_trace as trace
from kiwoom_monitor.central_server import diagnostic_replay_baseline as baseline
from kiwoom_monitor.central_server.database import SQLiteQueryStore, PostgresQueryStore
from kiwoom_monitor.central_server.diagnostic_account_input import (
    AccountInputProjection, resolve_owner_bindings, restore_account_arguments, source_execution_owner,
)
from kiwoom_monitor.central_server.diagnostic_replay_contract import (
    freeze_payload, thaw_payload, install_store_capture, capture_owner,
)
from kiwoom_monitor.central_server.diagnostic_top20_seed import Top20FixtureClock
from kiwoom_monitor.domain.order_contract import AccountBinding, AccountEnvironment, AccountScope, AccountSnapshot
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import AccountRecovery
from tests.unit.test_diagnostic_trace_blocks import persist_now
from tests.unit.test_diagnostic_trace_deferred import deferred_capture

REF = 'f9f2a34b-9188-4f8e-9e14-6fbc271c0001'
NOW = datetime(2026, 10, 12, 0, 0, 0, tzinfo=timezone.utc)
URL = 'postgresql://kiwoom_monitor_replay:fixture@localhost/kiwoom_monitor_replay_test'


class SQLiteProjectionCursor:
    """Test-only bridge for explicit capsule INSERTs, not a PostgreSQL acceptance."""
    def __init__(self, cursor):
        self.cursor = cursor

    def execute(self, statement, arguments=()):
        statement = statement if type(statement) is str else statement.as_string()
        statement = statement.replace('"public".', '')
        if 'collection=ANY(%s)' in statement:
            statement = statement.replace('collection=ANY(%s)', 'collection IN (' + ','.join('?' for _ in arguments[0]) + ')')
            arguments = tuple(arguments[0])
        if statement.startswith('ALTER SEQUENCE '):
            next_value = int(statement.rsplit(' ', 1)[1])
            self.cursor.execute("DELETE FROM sqlite_sequence WHERE name='central_execution_events'")
            return self.cursor.execute("INSERT INTO sqlite_sequence(name,seq) VALUES('central_execution_events',?)", (next_value - 1,))
        return self.cursor.execute(statement.replace('%s', '?'), arguments)


class AccountContextTests(unittest.TestCase):
    def scheduler_fixture(self):
        from tests.unit.test_recorded_execution import events
        from kiwoom_monitor.central_server.diagnostic_replay_contract import _result_summary
        capsule = self.capsule()
        binding = AccountBinding('profile-1', AccountScope('kiwoom', AccountEnvironment.REAL, REF), 3, NOW)
        recovery = AccountRecovery(AccountSnapshot(REF, 10000, 0, {'005930': 3}, NOW), ())
        specs = [
            ('snapshot', 'account', 1, 0, 'save_execution_account_snapshot', {
                'value': {'snapshot_id': 'snapshot', 'environment': 'mock', 'account_ref': REF,
                          'as_of': NOW.isoformat(), 'received_at': NOW.isoformat()},
                'ownership': {'owner_key': 'mock:' + REF, 'owner_token': 'run:with:colons:original-authority',
                              'run_id': 'run:with:colons'}}),
            ('release', 'account', 2, .02, 'release_execution_runtime', {
                'owner_key': 'mock:' + REF, 'owner_token': 'run:with:colons:original-authority'}),
            ('revision', 'account', 3, .04, 'save_real_account_recovery', {
                'binding': binding, 'recovery': recovery, 'received_at': NOW, 'settings_revision': 1}),
            ('vi', 'account', 4, .06, 'append_vi_events', {'values': [{
                'event_id': 'vi-1', 'event_key': 'vi-key', 'stock_code': '005930',
                'event_kind': 'start', 'vi_type': 'dynamic', 'received_at': NOW.timestamp(),
                'available_at': NOW.timestamp()}]}),
        ]
        projected = []
        metadata = {}
        for spec in specs:
            identifier, actor, sequence, offset, method, arguments = spec
            if method != 'append_vi_events':
                arguments, metadata[identifier] = self.projection.project(method, arguments)
            projected.append((identifier, actor, sequence, offset, method, arguments))
        rows = events(projected)
        for row in rows:
            row.update(metadata.get(row['operation_id'], {}))
            if row['event_type'] == 'operation_end':
                if row['operation_id'] == 'revision':
                    row.update(outcome='failed', exception_type='ValueError', native_error_code='ACCOUNT_CONTEXT_MISMATCH')
                else:
                    row.update(_result_summary(row['method'], 1 if row['operation_id'] == 'vi' else True))
        target = self.store('scheduler-' + str(time.monotonic_ns()))
        aliases = resolve_owner_bindings([*capsule['owner_bindings'], *rows])
        with target._connection() as connection:
            context._apply_account_context(SQLiteProjectionCursor(connection.cursor()), capsule, bindings=aliases)
        target._lease = SimpleNamespace(baseline_version=3)  # Local caller-owned store fixture only.
        target._execution_wall_now = lambda: NOW
        return capsule, rows, target

    def test_scheduler_shared_initial_alias_and_expected_rollback_continue_to_vi(self):
        from tests.unit.test_recorded_execution import execute
        capsule, rows, target = self.scheduler_fixture()
        result = asyncio.run(execute(target, rows, account_context=capsule))
        self.assertEqual('complete', result['state'])
        self.assertTrue(result['source_outcomes_match'])
        self.assertEqual(['returned', 'returned', 'failed', 'returned'], [row['state'] for row in result['calls']])
        self.assertTrue(result['calls'][2]['expected_failure_matched'])
        with target._connection() as connection:
            self.assertEqual(0, connection.execute('SELECT count(*) FROM central_execution_runtime_leases').fetchone()[0])
            self.assertEqual(0, connection.execute("SELECT count(*) FROM central_documents WHERE collection='real_account_recovery'").fetchone()[0])
            self.assertEqual(1, connection.execute('SELECT count(*) FROM central_vi_event_revisions').fetchone()[0])

    def test_scheduler_error_code_or_result_mismatch_stops_following_native_calls(self):
        from tests.unit.test_recorded_execution import execute
        for mismatch in ('error', 'result'):
            capsule, rows, target = self.scheduler_fixture()
            if mismatch == 'error':
                target.save_real_account_recovery = lambda **kwargs: (_ for _ in ()).throw(ValueError('ACCOUNT_IDENTITY_UNVERIFIED'))
            else:
                rows[1]['result'] = False
            result = asyncio.run(execute(target, rows, account_context=capsule))
            self.assertEqual('incomplete', result['state'])
            self.assertFalse(result['source_outcomes_match'])
            self.assertEqual('not_started', result['calls'][-1]['state'])

    def test_scheduler_missing_context_or_failure_receipt_rejected_before_native_call(self):
        from tests.unit.test_recorded_execution import execute
        capsule, rows, target = self.scheduler_fixture()
        with patch.object(target, 'save_execution_account_snapshot', wraps=target.save_execution_account_snapshot) as native:
            with self.assertRaisesRegex(ValueError, 'context_and_clock_required'):
                asyncio.run(execute(target, rows))
            rows[5].pop('native_error_code')
            with self.assertRaisesRegex(ValueError, 'failure_receipt_required'):
                asyncio.run(execute(target, rows, account_context=capsule))
            native.assert_not_called()

    def test_owned_runner_source_clock_mismatch_rejects_before_database_connection(self):
        from kiwoom_monitor.central_server.diagnostic_recorded_execution import run_owned_recorded_experiment
        capsule, rows, _ = self.scheduler_fixture()
        with patch.object(baseline, '_maintenance_connection') as connect:
            with self.assertRaisesRegex(ValueError, 'account_source_clock_mismatch'):
                run_owned_recorded_experiment(URL, 'a' * 32, '0' * 64, rows,
                    baseline_version=3, cache_clock=Top20FixtureClock(NOW), account_context=capsule,
                    started_mono_ns=1_000_000_000, window_start_seconds=0, window_end_seconds=.2)
            connect.assert_not_called()

    def test_scheduler_expected_error_class_and_success_must_match(self):
        from tests.unit.test_recorded_execution import execute
        for returned in (False, True):
            capsule, rows, target = self.scheduler_fixture()
            if returned:
                target.save_real_account_recovery = lambda **kwargs: {}
            else:
                target.save_real_account_recovery = lambda **kwargs: (_ for _ in ()).throw(RuntimeError('ACCOUNT_CONTEXT_MISMATCH'))
            result = asyncio.run(execute(target, rows, account_context=capsule))
            self.assertEqual('incomplete', result['state'])
            self.assertFalse(result['source_outcomes_match'])
            self.assertEqual('not_started', result['calls'][-1]['state'])

    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        self.source = self.store('source')
        settings = {'scope': {'broker': 'kiwoom', 'environment': 'real', 'account_ref': REF},
                    'active_profile_id': 'profile-1', 'revision': 2, 'monitor_enabled': True,
                    'mock_order_enabled': False}
        with self.source._connection() as connection:
            connection.execute('INSERT INTO central_account_registry VALUES(?,?,?,?,?,?)',
                (REF, 'kiwoom', 'real', 'original-secret-fingerprint', NOW.isoformat(), 'active'))
            connection.execute('INSERT INTO central_account_binding_revisions VALUES(?,?,?,?,?,?,?,?)',
                ('binding-1', 'profile-1', 'kiwoom', 'real', REF, 3, NOW.isoformat(), 'broker-read'))
            connection.execute('INSERT INTO central_execution_runtime_leases VALUES(?,?,?,?)',
                ('mock:' + REF, 'run:with:colons:original-authority',
                 (NOW + timedelta(seconds=30)).isoformat(), NOW.isoformat()))
            connection.execute('INSERT INTO central_documents VALUES(?,?,?,?,?)',
                ('server_account_settings', 'kiwoom:real:' + REF, 'settings', NOW.timestamp(), json.dumps(settings)))
            connection.execute('INSERT INTO central_documents VALUES(?,?,?,?,?)',
                ('unrelated', 'owner', 'key', 1.0, json.dumps({'access_token': 'must-not-read'})))
        self.projection = AccountInputProjection('trace-fixture')

    def store(self, name):
        store = SQLiteQueryStore(Path(self.root.name) / (name + '.sqlite3'))
        store.initialize()
        self.addCleanup(store.close)
        return store

    def capsule(self):
        return context.read_account_context(self.source, self.projection, source_release='fixture-release')

    def seed_ledger(self):
        intent = {'intent_id': 'prior-intent', 'run_id': 'prior-run', 'environment': 'mock',
                  'account_ref': REF, 'state': 'SUBMITTED', 'created_at': NOW.isoformat(), 'updated_at': NOW.isoformat()}
        event = {'event_id': 'prior-event', 'intent_id': 'prior-intent', 'state': 'SUBMITTED',
                 'occurred_at': NOW.isoformat(), 'received_at': NOW.isoformat()}
        snapshot = {'snapshot_id': 'prior-snapshot', 'environment': 'mock', 'account_ref': REF,
                    'as_of': NOW.isoformat(), 'received_at': NOW.isoformat()}
        self.source.create_execution_intent(intent)
        self.source.append_execution_event(intent, event)
        self.source.save_execution_account_snapshot(snapshot)
        with self.source._connection() as connection:
            connection.execute("UPDATE sqlite_sequence SET seq=12 WHERE name='central_execution_events'")
        return intent, event, snapshot

    def test_preexisting_ledger_duplicate_results_followup_and_sequence_gap_preserved(self):
        intent, event, snapshot = self.seed_ledger()
        capsule = self.capsule()
        self.assertEqual(13, capsule['execution_event_next_sequence'])
        self.assertEqual(7, capsule['row_count'])
        target = self.store('prior-ledger')
        with target._connection() as connection:
            context._apply_account_context(SQLiteProjectionCursor(connection.cursor()), capsule,
                bindings=resolve_owner_bindings(capsule['owner_bindings']))
        self.assertEqual(self.source.load_execution_intent('prior-intent'), target.load_execution_intent('prior-intent'))
        self.assertEqual(self.source.load_execution_events('prior-intent'), target.load_execution_events('prior-intent'))
        for store in (self.source, target):
            self.assertFalse(store.create_execution_intent(intent))
            self.assertFalse(store.append_execution_event(intent, event))
            self.assertFalse(store.save_execution_account_snapshot(snapshot))
            self.assertTrue(store.append_execution_event({**intent, 'state': 'FILLED'},
                {**event, 'event_id': 'followup-event', 'state': 'FILLED'}))
        with self.source._connection() as first, target._connection() as second:
            query = 'SELECT * FROM central_execution_events ORDER BY accepted_sequence'
            normalize = lambda rows: [(*row[:-1], json.loads(row[-1])) for row in rows]
            self.assertEqual(normalize(first.execute(query).fetchall()), normalize(second.execute(query).fetchall()))
            self.assertEqual(13, second.execute("SELECT accepted_sequence FROM central_execution_events WHERE event_id='followup-event'").fetchone()[0])

    def test_initial_ledger_corruption_and_sensitive_documents_fail_before_apply(self):
        self.seed_ledger()
        capsule = self.capsule()
        for kind in ('parent', 'duplicate', 'sequence', 'json_scope', 'secret'):
            altered = copy.deepcopy(capsule)
            if kind == 'parent':
                altered['tables']['central_execution_events'][0]['intent_id'] = 'missing'
            elif kind == 'duplicate':
                duplicate = copy.deepcopy(altered['tables']['central_execution_events'][0])
                duplicate['accepted_sequence'] = 2
                altered['tables']['central_execution_events'].append(duplicate)
                altered['row_count'] += 1
            elif kind == 'sequence':
                altered['execution_event_next_sequence'] = 1
            elif kind == 'json_scope':
                altered['tables']['central_execution_intents'][0]['document_json']['account_ref'] = 'other'
            else:
                altered['tables']['central_execution_intents'][0]['document_json']['owner_token'] = 'never serialize'
            altered['sha256'] = context._digest({k: v for k, v in altered.items() if k != 'sha256'})[0]
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                context.validate_account_context(altered)

    def test_legacy_v1_context_still_validates_without_inventing_prior_ledger(self):
        capsule = self.capsule()
        capsule['version'] = 'account-context/v1'
        capsule.pop('execution_event_next_sequence')
        capsule['tables'] = {key: capsule['tables'][key] for key in context.COLUMNS_V1}
        capsule['sha256'] = context._digest({k: v for k, v in capsule.items() if k != 'sha256'})[0]
        self.assertIs(capsule, context.validate_account_context(capsule))
        self.assertNotIn('central_execution_intents', capsule['tables'])

    def test_context_payload_profile_accepts_supported_versions_and_rejects_unknown(self):
        from kiwoom_monitor.central_server.diagnostic_replay_contract import (
            payload_copy_limit, ACCOUNT_CONTEXT_PROFILE, ACCOUNT_CONTEXT_COPY_BYTES,
        )
        for version in context.VERSIONS:
            self.assertEqual(ACCOUNT_CONTEXT_COPY_BYTES, payload_copy_limit('account_context',
                {'payload_profile': ACCOUNT_CONTEXT_PROFILE, 'account_context_version': version}))
        with self.assertRaisesRegex(ValueError, 'invalid_payload_profile'):
            payload_copy_limit('account_context', {'payload_profile': ACCOUNT_CONTEXT_PROFILE,
                                                 'account_context_version': 'unknown'})

    def test_read_only_projection_preserves_revisions_and_excludes_source_secrets(self):
        with self.source._connection() as connection:
            before = list(connection.iterdump())
        # An unrelated caller's owner context must not give a DB row a guessed run.
        with source_execution_owner('unrelated-owner', 'unrelated-run'):
            value = self.capsule()
        serialized = json.dumps(value)
        for secret in ('original-secret-fingerprint', 'original-authority', 'must-not-read'):
            self.assertNotIn(secret, serialized)
        self.assertEqual(4, value['row_count'])
        self.assertFalse(value['source_state_equivalent'])
        self.assertEqual(3, value['tables']['central_account_binding_revisions'][0]['binding_revision'])
        self.assertIsNone(value['owner_bindings'][0]['execution_owner_binding']['run_id'])
        with self.source._connection() as connection:
            self.assertEqual(before, list(connection.iterdump()))

    def test_native_recovery_and_lease_fences_match_after_projected_restore(self):
        value = self.capsule()
        target = self.store('target')
        projected, owner = self.projection.project('save_execution_account_snapshot', {
            'value': {'snapshot_id': 'mock-snapshot', 'environment': 'mock', 'account_ref': REF,
                      'as_of': NOW.isoformat(), 'received_at': NOW.isoformat()},
            'ownership': {'owner_key': 'mock:' + REF, 'owner_token': 'run:with:colons:original-authority',
                          'run_id': 'run:with:colons'}})
        owner = {'method': 'save_execution_account_snapshot', **owner}
        bindings = resolve_owner_bindings([*value['owner_bindings'], owner])
        with target._connection() as connection:
            connection.execute('BEGIN')
            context._apply_account_context(SQLiteProjectionCursor(connection.cursor()), value, bindings=bindings)
        arguments = restore_account_arguments(owner, projected, bindings=bindings)
        target._execution_wall_now = lambda: NOW
        self.assertTrue(target.save_execution_account_snapshot(**arguments))
        target._execution_wall_now = lambda: NOW + timedelta(seconds=30)
        with self.assertRaisesRegex(RuntimeError, 'EXECUTION_OWNERSHIP_LOST'):
            target.save_execution_account_snapshot(**arguments)
        binding = AccountBinding('profile-1', AccountScope('kiwoom', AccountEnvironment.REAL, REF), 3, NOW)
        recovery = AccountRecovery(AccountSnapshot(REF, 10000, 0, {'005930': 3}, NOW), ())
        self.source._account_input_wall_time = target._account_input_wall_time = lambda: NOW.timestamp()
        expected = self.source.save_real_account_recovery(binding, recovery, NOW, settings_revision=2)
        self.assertEqual(expected, target.save_real_account_recovery(binding, recovery, NOW, settings_revision=2))
        with self.assertRaisesRegex(ValueError, 'ACCOUNT_CONTEXT_MISMATCH'):
            target.save_real_account_recovery(binding, recovery, NOW, settings_revision=1)
        with target._connection() as connection:
            rows = connection.execute("SELECT updated_at FROM central_documents WHERE collection='real_account_recovery'").fetchall()
            self.assertEqual([(NOW.timestamp(),)], rows)

    def test_context_recording_and_durable_reader_retain_one_snapshot(self):
        with deferred_capture(account_inputs=True, account_context_store=self.source) as (identifier, _):
            self.assertEqual('captured', trace.status()['account_context']['state'])
            persist_now()
            value = context.read_recorded_account_context(identifier)
            self.assertEqual(identifier, value['trace_id'])
            self.assertEqual(4, value['row_count'])
            self.assertEqual(trace.status()['account_context']['sha256'], value['sha256'])
            manifest, rows = trace.recorded_events(identifier)
            self.assertEqual(['account_context'], [row['event_type'] for row in rows])
            self.assertNotIn('original-authority', json.dumps(manifest))
            first = next(iter(manifest['blobs'].values()))['parts'][0]
            path = trace._directory() / identifier / first['name']
            with path.open('r+b') as stream:
                stream.seek(first['offset'])
                stream.write(b'!')
            with self.assertRaisesRegex(ValueError, 'checksum_mismatch'):
                context.read_recorded_account_context(identifier)

    def test_limits_corruption_and_secret_documents_fail_closed(self):
        value = self.capsule()
        altered = copy.deepcopy(value)
        altered['tables']['central_account_binding_revisions'][0]['binding_revision'] += 1
        with self.assertRaisesRegex(ValueError, 'checksum_mismatch'):
            context.validate_account_context(altered)
        with patch.object(context, 'MAX_ROWS', 3), self.assertRaisesRegex(ValueError, 'row_limit'):
            self.capsule()
        with patch.object(context, 'MAX_BYTES', 1024), self.assertRaisesRegex(ValueError, 'byte_limit'):
            self.capsule()
        with self.source._connection() as connection:
            connection.execute("UPDATE central_documents SET document_json=? WHERE collection='server_account_settings'",
                               (json.dumps({'owner_token': 'sensitive'}),))
        with self.assertRaisesRegex(ValueError, '^account_context_read_failed$'):
            self.capsule()

    def test_same_recorded_inputs_and_initial_context_preserve_native_results_and_contents(self):
        target = self.store('replayed')
        binding = AccountBinding('profile-1', AccountScope('kiwoom', AccountEnvironment.REAL, REF), 3, NOW)
        recovery = AccountRecovery(AccountSnapshot(REF, 10000, 0, {'005930': 3}, NOW), ())
        ownership = {'owner_key': 'mock:' + REF, 'owner_token': 'run:with:colons:original-authority',
                     'run_id': 'run:with:colons'}
        snapshot = {'snapshot_id': 'recorded-snapshot', 'environment': 'mock', 'account_ref': REF,
                    'as_of': NOW.isoformat(), 'received_at': NOW.isoformat()}
        self.source._execution_wall_now = target._execution_wall_now = lambda: NOW
        self.source._account_input_wall_time = target._account_input_wall_time = lambda: NOW.timestamp()
        install_store_capture(self.source)
        with deferred_capture(account_inputs=True, account_context_store=self.source) as (identifier, _):
            with capture_owner('account', 'fixture', 'actor'):
                expected = [self.source.save_execution_account_snapshot(snapshot, ownership=ownership),
                            self.source.save_real_account_recovery(binding, recovery, NOW, settings_revision=2)]
            final = persist_now()
            self.assertEqual(0, final['input_rejected'])
            capsule = context.read_recorded_account_context(identifier)
            _, rows = trace.recorded_events(identifier)
            starts = [row for row in rows if row['event_type'] == 'operation_start']
            bindings = resolve_owner_bindings([*capsule['owner_bindings'], *starts])
            with target._connection() as connection:
                connection.execute('BEGIN')
                context._apply_account_context(SQLiteProjectionCursor(connection.cursor()), capsule, bindings=bindings)
            actual = [getattr(target, row['method'])(**restore_account_arguments(
                row, thaw_payload(row['payload']), bindings=bindings)) for row in starts]
            self.assertEqual(expected, actual)
            for table, where in (('central_execution_account_snapshots', ''),
                                 ('central_documents', " WHERE collection='real_account_recovery'")):
                with self.source._connection() as source, target._connection() as replayed:
                    self.assertEqual(source.execute('SELECT * FROM ' + table + where).fetchall(),
                                     replayed.execute('SELECT * FROM ' + table + where).fetchall())

    def test_snapshot_failure_or_control_stop_does_not_start_or_revive_capture(self):
        from kiwoom_monitor.central_server.diagnostic_workloads import _set_trace, trace_status, diagnostic_tool_status
        real_read = context.read_account_context
        # Start the ordinary fixture solely to obtain its existing master; stop
        # it before exercising the new preparation path under the same session.
        with deferred_capture() as (_, control):
            trace.stop(timeout=.01)
            persist_now()
            session = diagnostic_tool_status()['session_id']
            _set_trace(control, True, 120, expected_session=session)
            with patch.object(context, 'read_account_context', side_effect=ValueError('account_context_read_failed')):
                with self.assertRaisesRegex(ValueError, 'account_context_read_failed'):
                    trace.start(seconds=60, store_inputs=True, account_inputs=True,
                                account_context_store=self.source, persist_at=time.time() + 3600)
            # The old recorder's worker retired its child; enable it for the next
            # start, then turn it off while the source snapshot is being read.
            _set_trace(control, True, 120, expected_session=session)
            def stop_during_read(*args, **kwargs):
                value = real_read(*args, **kwargs)
                _set_trace(control, False, 120, expected_session=session)
                return value
            with patch.object(context, 'read_account_context', side_effect=stop_during_read):
                with self.assertRaisesRegex(ValueError, 'diagnostic_trace_child_off'):
                    trace.start(seconds=60, store_inputs=True, account_inputs=True,
                                account_context_store=self.source, persist_at=time.time() + 3600)
            self.assertFalse(trace_status()['enabled'])
            _set_trace(control, True, 120, expected_session=session)
            def stop_at_revision_check(path, enabled, seconds, **options):
                _set_trace(path, False, seconds, expected_session=session)
                return _set_trace(path, enabled, seconds, **options)
            with patch.object(trace, '_set_trace', side_effect=stop_at_revision_check):
                with self.assertRaisesRegex(ValueError, 'diagnostic_control_conflict'):
                    trace.start(seconds=60, store_inputs=True, account_inputs=True,
                                account_context_store=self.source, persist_at=time.time() + 3600)
            self.assertFalse(trace_status()['enabled'])

    def test_postgres_source_uses_read_only_snapshot_named_cursor_and_bounded_fetches(self):
        store = PostgresQueryStore(URL)
        connection, settings, reader = MagicMock(), MagicMock(), MagicMock()
        connection.__enter__.return_value = connection
        settings.__enter__.return_value = settings
        settings.fetchone.side_effect = [(1, 1, False), (1, False)]
        reader.__enter__.return_value = reader
        reader.fetchmany.return_value = []
        connection.cursor.side_effect = lambda **options: reader if options.get('name') else settings
        store._connect = MagicMock(return_value=connection)
        value = context.read_account_context(store, self.projection, source_release='fixture')
        self.assertEqual('repeatable_read_read_only', value['isolation'])
        self.assertEqual(0, value['row_count'])
        self.assertIn('REPEATABLE READ, READ ONLY', settings.execute.call_args_list[0].args[0])
        self.assertIn({'name': 'recorded_account_context'}, [call.kwargs for call in connection.cursor.call_args_list])
        for call in reader.fetchmany.call_args_list:
            self.assertEqual((16,), call.args)
        for call in reader.execute.call_args_list:
            statement = call.args[0]
            self.assertTrue(statement.startswith('SELECT '))
            self.assertIn(' LIMIT ', statement)
            self.assertNotIn('identity_fingerprint', statement)
            self.assertNotIn('central_credential_profiles', statement)
            self.assertNotIn('credential_vault', statement)
        self.assertTrue(any('octet_length' in call.args[0] for call in reader.execute.call_args_list))

    def test_source_deadline_and_document_bound_fail_before_unbounded_retention(self):
        with patch.object(context, 'SOURCE_READ_SECONDS', 0):
            with self.assertRaisesRegex(ValueError, 'snapshot_timeout'):
                self.capsule()
        with self.source._connection() as connection:
            connection.execute("UPDATE central_documents SET document_json=? WHERE collection='server_account_settings'",
                               (json.dumps({'oversized': 'x' * (9 * 1024 * 1024)}),))
        with self.assertRaisesRegex(ValueError, 'document_oversized_or_null'):
            self.capsule()

    def test_large_context_uses_blocks_without_enabling_large_store_arguments(self):
        with self.source._connection() as connection:
            connection.executemany('INSERT INTO central_documents VALUES(?,?,?,?,?)',
                [('execution_mock_automation_control', str(i), str(i), 1.0,
                  json.dumps({'fixture': '가' * 3000})) for i in range(900)])
        value = self.capsule()
        with self.assertRaisesRegex(ValueError, 'payload_budget_exceeded'):
            freeze_payload(value)
        with deferred_capture(account_inputs=True, account_context_store=self.source) as (identifier, _):
            final = persist_now()
            self.assertEqual((1, 1, 0), (final['accepted'], final['written'], final['input_rejected']))
            self.assertGreater(len(next(iter(final['blobs'].values()))['parts']), 1)
            restored = context.read_recorded_account_context(identifier)
            # Session keys differ between a standalone projection and capture.
            # Compare native prerequisite data separately from authority aliases.
            for table in context.COLUMNS:
                if table != 'central_execution_runtime_leases':
                    self.assertEqual(value['tables'][table], restored['tables'][table])
            expected = {k: v for k, v in value['tables']['central_execution_runtime_leases'][0].items() if k != 'owner_alias'}
            actual = {k: v for k, v in restored['tables']['central_execution_runtime_leases'][0].items() if k != 'owner_alias'}
            self.assertEqual(expected, actual)

    def test_v3_seal_verifies_parent_before_context_and_uses_one_transaction(self):
        value = self.capsule()
        lease = baseline.ReplayDatabaseLease(URL, 'a' * 32, baseline_version=3, cache_clock=Top20FixtureClock(NOW))
        lease._active = True
        lease.connection = MagicMock()
        cursor = lease.connection.cursor.return_value.__enter__.return_value
        parent = {'version': 1, 'config': lease.config, 'schema_sha256': 'schema',
                  'tables': {}, 'sequences': {}}
        cursor.fetchone.return_value = (baseline._hash(parent), parent)
        with patch.object(lease, '_maintenance'), patch.object(lease, '_lock_tables'), \
             patch.object(lease, '_baseline_row', return_value=None), patch.object(lease, '_schema', return_value='schema'), \
             patch.object(lease, '_tables_digest', return_value={}), patch.object(lease, '_sequences', return_value={}), \
             patch.object(context, '_apply_account_context') as apply:
            sealed = lease.seal(account_context=value)
            apply.assert_called_once()
            lease.connection.transaction.assert_called_once()
            self.assertEqual(value['sha256'], sealed['manifest']['account_context']['sha256'])
            self.assertFalse(sealed['manifest']['source_state_equivalent'])
            cursor.fetchone.return_value = ('wrong-parent', parent)
            apply.reset_mock()
            with self.assertRaisesRegex(RuntimeError, 'requires_restored_v1'):
                lease.seal(account_context=value)
            apply.assert_not_called()
