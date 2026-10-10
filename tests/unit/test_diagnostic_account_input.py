"""Future account capture preserves native arguments without authority secrets."""
import asyncio
import json
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kiwoom_monitor.central_server import diagnostic_trace as trace
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.diagnostic_account_input import (
    AccountInputProjection, restore_account_arguments, source_execution_owner,
    native_error_receipt,
    resolve_owner_bindings,
)
from kiwoom_monitor.central_server.diagnostic_recorded_execution import _execute_recorded_operations
from kiwoom_monitor.central_server.diagnostic_replay_contract import (
    InputRejected, capture_owner, freeze_payload, thaw_payload, thaw_operation_arguments,
    validate_operation,
)
from kiwoom_monitor.domain.order_contract import (
    AccountBinding, AccountEnvironment, AccountScope, AccountSnapshot,
    BrokerFill, BrokerOrderSnapshot, OrderState,
)
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import AccountRecovery
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import OrderExecution, AccountBalanceChange
from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository
from tests.unit.test_diagnostic_trace_blocks import persist_now
from tests.unit.test_diagnostic_trace_deferred import deferred_capture

REF = 'f9f2a34b-9188-4f8e-9e14-6fbc271c0001'
NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


class DiagnosticAccountInputTests(unittest.TestCase):
    def test_frontier_alias_resolution_preserves_nested_runs_and_unbound_release_identity(self):
        projection = AccountInputProjection('trace-1')
        key, original = 'mock:' + REF, 'run:with:colons:authority'
        pairs = []
        for run in ('run', 'run:with:colons', 'run:wrong'):
            value, meta = projection.project('save_execution_account_snapshot', {
                'value': {}, 'ownership': {'owner_key': key, 'owner_token': original, 'run_id': run}})
            pairs.append(({'method': 'save_execution_account_snapshot', **meta}, value))
        value, meta = projection.project('release_execution_runtime', {'owner_key': key, 'owner_token': original})
        pairs.append(({'method': 'release_execution_runtime', **meta}, value))
        bindings = resolve_owner_bindings([row for row, _ in pairs])
        tokens = []
        for row, value in pairs:
            restored = restore_account_arguments(row, value, bindings=bindings)
            tokens.append(restored.get('ownership', restored)['owner_token'])
        self.assertEqual(1, len(set(tokens)))
        self.assertTrue(tokens[0].startswith('run:with:colons:'))
        self.assertFalse(tokens[0].startswith('run:wrong:'))
        self.assertIsNone(pairs[-1][0]['execution_owner_binding']['run_id'])
        altered = {**pairs[0][0], 'execution_owner_binding': {
            **pairs[0][0]['execution_owner_binding'], 'prefix_matches': False}}
        with self.assertRaisesRegex(ValueError, 'prefix_conflict'):
            resolve_owner_bindings([pairs[0][0], altered])
        with self.assertRaisesRegex(ValueError, 'resolution_invalid'):
            restore_account_arguments(pairs[0][0], pairs[0][1], bindings={})

    def test_failure_receipt_uses_only_allowlisted_native_codes(self):
        self.assertEqual({'native_error_code': 'REAL_ACCOUNT_RECOVERY_INVALID'},
                         native_error_receipt('save_real_account_recovery',
                                              ValueError('REAL_ACCOUNT_RECOVERY_INVALID')))
        self.assertEqual({}, native_error_receipt('save_real_account_recovery',
                                                  ValueError('private-authority account-number')))
        self.assertEqual({}, native_error_receipt('load_document', ValueError('ACCOUNT_CONTEXT_MISMATCH')))

    def test_previous_document_reads_are_allowed_without_opening_writes(self):
        for collection in ('app_settings', 'app_column_settings', 'journal_news_link', 'journal_v2_news_links'):
            with self.subTest(collection=collection):
                self.assertEqual('rest_market', validate_operation('load_document',
                                                                   {'collection': collection, 'owner': 'app', 'key': 'k'}))
                with self.assertRaisesRegex(InputRejected, 'unsupported_document_collection'):
                    validate_operation('upsert_documents', {'collection': collection, 'values': []})

    def test_typed_recovery_and_events_round_trip_without_raw_account_fields(self):
        scope = AccountScope('kiwoom', AccountEnvironment.REAL, REF)
        binding = AccountBinding('profile-1', scope, 2, NOW)
        account = AccountSnapshot(REF, 10000, 0, {'005930': 3}, NOW)
        order = BrokerOrderSnapshot('order-1', REF, '005930', OrderState.FILLED, 3, 0, NOW,
                                    (BrokerFill('fill-1', 3, 100, NOW),))
        recovery = AccountRecovery(account, (order,))
        execution = OrderExecution('order-1', 'fill-1', '005930', 'name', 'buy', 100, 3,
                                   '090000', origin_scope=scope)
        balance = AccountBalanceChange('005930', 'name', 3, 3, 100, 300, 10000, 100,
                                       origin_scope=scope)
        for value in (binding, account, order, recovery, execution, balance):
            with self.subTest(kind=type(value).__name__):
                self.assertEqual(value, thaw_payload(freeze_payload(value).value))
        for key in ('account_number', '9201', 'access_token', 'owner_token'):
            with self.subTest(secret=key), self.assertRaisesRegex(InputRejected, 'sensitive_payload_field'):
                freeze_payload({key: 'secret'})

    def test_explicit_run_context_and_same_alias_preserve_acquire_write_release(self):
        projection = AccountInputProjection('trace-1')
        key, run, original = 'mock:' + REF, 'run:with:colons', 'run:with:colons:authority-secret'
        with source_execution_owner(key, run):
            acquire, metadata = projection.project('acquire_execution_runtime', {
                'owner_key': key, 'owner_token': original, 'now': NOW.isoformat(),
                'lease_expires_at': (NOW + timedelta(seconds=60)).isoformat()})
            release, release_meta = projection.project('release_execution_runtime',
                                                        {'owner_key': key, 'owner_token': original})
        write, write_meta = projection.project('save_execution_account_snapshot', {
            'value': {'environment': 'mock', 'account_ref': REF},
            'ownership': {'owner_key': key, 'owner_token': original, 'run_id': run,
                          'control_revision': 7, 'active_spec_id': 'spec'}})
        self.assertEqual(acquire['owner_alias'], release['owner_alias'])
        self.assertEqual(acquire['owner_alias'], write['ownership']['owner_alias'])
        restored = [restore_account_arguments({'method': method, **meta}, value) for method, meta, value in (
            ('acquire_execution_runtime', metadata, acquire),
            ('save_execution_account_snapshot', write_meta, write),
            ('release_execution_runtime', release_meta, release))]
        token = restored[0]['owner_token']
        self.assertTrue(token.startswith(run + ':'))
        self.assertEqual(token, restored[1]['ownership']['owner_token'])
        self.assertEqual(token, restored[2]['owner_token'])
        self.assertNotEqual(original, token)
        self.assertNotIn(original, json.dumps([acquire, write, release, metadata, write_meta, release_meta]))
        other, _ = AccountInputProjection('trace-2').project('release_execution_runtime',
                                                           {'owner_key': key, 'owner_token': original})
        self.assertNotEqual(acquire['owner_alias'], other['owner_alias'])

    def test_invalid_prefix_is_not_repaired_and_unknown_run_is_not_guessed(self):
        projection = AccountInputProjection('trace-1')
        key = 'mock:' + REF
        write, meta = projection.project('save_execution_account_snapshot', {
            'value': {}, 'ownership': {'owner_key': key, 'owner_token': 'different:secret', 'run_id': '!!!!'}})
        restored = restore_account_arguments({'method': 'save_execution_account_snapshot', **meta}, write)
        self.assertFalse(restored['ownership']['owner_token'].startswith('!!!!:'))
        release, release_meta = projection.project('release_execution_runtime', {
            'owner_key': key, 'owner_token': 'run:with:colons:secret'})
        self.assertIsNone(release_meta['execution_owner_binding']['run_id'])
        bad = {**release_meta, 'execution_owner_binding': {
            **release_meta['execution_owner_binding'], 'owner_key': 'wrong'}}
        with self.assertRaisesRegex(ValueError, 'binding_invalid'):
            restore_account_arguments({'method': 'release_execution_runtime', **bad}, release)

    def test_repository_native_capture_keeps_tokens_outside_recording(self):
        with tempfile.TemporaryDirectory() as root:
            store = SQLiteQueryStore(Path(root) / 'source.sqlite3')
            store.initialize()
            repository = ExecutionRepository(store)
            try:
                with deferred_capture(account_inputs=True) as (identifier, _):
                    with capture_owner('account', 'fixture', 'actor'):
                        self.assertTrue(repository.claim_runtime('mock', REF, 'run:with:colons',
                                                                 'private-authority', NOW))
                        repository.bind_runtime_owner(REF, 'run:with:colons', 'private-authority')
                        self.assertTrue(repository.save_account_snapshot(
                            'mock', AccountSnapshot(REF, 10000, 0, {'005930': 3}, NOW), NOW))
                        self.assertTrue(repository.release_runtime('mock', REF, 'run:with:colons',
                                                                   'private-authority'))
                    final = persist_now()
                    self.assertEqual((4, 0, 0), (final['schema_version'], final['input_rejected'],
                                                   final['operation_inflight']))
                    manifest, rows = trace.recorded_events(identifier)
                    self.assertIsNone(trace._ACCOUNT_PROJECTION)
                    starts = [row for row in rows if row['event_type'] == 'operation_start']
                    self.assertEqual(['acquire_execution_runtime', 'save_execution_account_snapshot',
                                      'release_execution_runtime'], [row['method'] for row in starts])
                    restored = [thaw_operation_arguments(row) for row in starts]
                    self.assertEqual(restored[0]['owner_token'], restored[1]['ownership']['owner_token'])
                    self.assertEqual(restored[0]['owner_token'], restored[2]['owner_token'])
                    for part in manifest['chunks']:
                        self.assertNotIn(b'private-authority', trace.chunk_bytes(identifier, part['name']))
                    for digest in manifest['blobs']:
                        self.assertNotIn(b'private-authority', trace.payload_bytes(identifier, digest))
                    self.assertNotIn('private-authority', json.dumps(manifest))
                    # Capture is available before context/baselinev3 execution is enabled.
                    with self.assertRaisesRegex(ValueError, 'account_vi_requires_v3'):
                        asyncio.run(_execute_recorded_operations(
                            store, rows, started_mono_ns=manifest['started_mono_ns'],
                            window_start_seconds=0, window_end_seconds=1))
            finally:
                store.close()

    def test_same_initial_db_and_input_remove_capture_rejections_without_extra_commits(self):
        contents, counts, rejections = [], [], []
        with tempfile.TemporaryDirectory() as root:
            for enabled in (False, True):
                store = SQLiteQueryStore(Path(root) / ('after.sqlite3' if enabled else 'before.sqlite3'))
                store.initialize()
                observed = []
                connection = store._connection

                @contextmanager
                def counted_connection():
                    with connection() as native:
                        def observe(statement):
                            kind = statement.strip().split(' ', 1)[0].upper()
                            if kind in {'BEGIN', 'COMMIT', 'ROLLBACK'}:
                                observed.append(kind)  # No SQL/parameters/token in evidence.
                        native.set_trace_callback(observe)
                        yield native

                store._connection = counted_connection
                repository = ExecutionRepository(store)
                try:
                    with deferred_capture(account_inputs=enabled):
                        with capture_owner('account', 'fixture', 'actor'):
                            outcomes = [repository.claim_runtime('mock', REF, 'run', 'private-authority', NOW)]
                            repository.bind_runtime_owner(REF, 'run', 'private-authority')
                            outcomes.append(repository.save_account_snapshot(
                                'mock', AccountSnapshot(REF, 10000, 0, {'005930': 3}, NOW), NOW))
                            outcomes.append(repository.release_runtime('mock', REF, 'run', 'private-authority'))
                        self.assertEqual([True, True, True], outcomes)
                        final = persist_now('complete' if enabled else 'incomplete')
                        rejections.append(final['input_rejected'])
                    counts.append({kind: observed.count(kind) for kind in ('BEGIN', 'COMMIT', 'ROLLBACK')})
                    with connection() as native:
                        contents.append((native.execute('SELECT * FROM central_execution_account_snapshots').fetchall(),
                                         native.execute('SELECT * FROM central_execution_runtime_leases').fetchall()))
                finally:
                    store.close()
        self.assertEqual([3, 0], rejections)
        self.assertEqual(contents[0], contents[1])
        self.assertEqual([{'BEGIN': 3, 'COMMIT': 3, 'ROLLBACK': 0}] * 2, counts)
