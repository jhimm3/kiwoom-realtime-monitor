"""Account/VI capture -> sealed v3 -> native PostgreSQL replay on a fresh job DB.

Only the operator's disposable cluster is accepted. These are controlled inputs,
not reconstructed October 8 account/VI data or an operational database snapshot.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import io
import os
from pathlib import Path
import secrets
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit, urlunsplit

from psycopg import sql

from kiwoom_monitor.central_server import diagnostic_account_context as context
from kiwoom_monitor.central_server import diagnostic_replay_baseline as baseline
from kiwoom_monitor.central_server import diagnostic_trace as trace
from kiwoom_monitor.central_server.database import PostgresQueryStore
from kiwoom_monitor.central_server.diagnostic_account_input import source_execution_owner
from kiwoom_monitor.central_server import diagnostic_replay_database_cli as cli
from kiwoom_monitor.central_server.diagnostic_replay_contract import capture_owner
from kiwoom_monitor.central_server.diagnostic_top20_seed import Top20FixtureClock
from kiwoom_monitor.domain.order_contract import AccountBinding, AccountEnvironment, AccountScope, AccountSnapshot
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import AccountRecovery
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import AccountBalanceChange
from tests.unit.test_diagnostic_trace_blocks import persist_now
from tests.unit.test_diagnostic_trace_deferred import deferred_capture


class RecordedAccountPostgresTests(unittest.TestCase):
    def test_real_native_capture_v3_atomic_seal_replay_twice_and_restore(self):
        fixture_url = os.environ.get('KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL', '')
        if not fixture_url:
            self.skipTest('fresh disposable NAS operator cluster required')
        parts = urlsplit(fixture_url)
        self.assertEqual('kiwoom_operator_fixture', parts.username)
        self.assertEqual('/kiwoom_monitor_diagnostic_test', parts.path)
        self.assertEqual('127.0.0.1', parts.hostname)
        # The operator creates these two unprivileged roles with the same
        # ephemeral secret. No operational DSN/secret is read or reported.
        authority = 'kiwoom_monitor_replay' + parts.netloc[len(parts.username):]
        url = urlunsplit((parts.scheme, authority, '/' + baseline.DATABASE_NAME, '', ''))
        token = secrets.token_hex(16)
        baseline.provision_existing_empty_database(url, token)
        with baseline.ReplayDatabaseLease(url, token) as lease:
            parent = lease.seal()['baseline_id']
            lease.restore(parent)
            now = datetime.now(timezone.utc)
            ref = 'f9f2a34b-9188-4f8e-9e14-6fbc271c0001'
            run = 'run:with:colons'
            authority = run + ':' + secrets.token_hex(16)
            settings = {'scope': {'broker': 'kiwoom', 'environment': 'real', 'account_ref': ref},
                        'active_profile_id': 'profile-1', 'revision': 2, 'monitor_enabled': True,
                        'mock_order_enabled': False}
            with lease.connection.transaction(), lease.connection.cursor() as cursor:
                cursor.execute('INSERT INTO central_account_registry VALUES(%s,%s,%s,%s,%s,%s)',
                    (ref, 'kiwoom', 'real', 'source-fixture-fingerprint', now, 'active'))
                cursor.execute('INSERT INTO central_account_binding_revisions VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',
                    ('binding-1', 'profile-1', 'kiwoom', 'real', ref, 3, now, 'broker-read'))
                cursor.execute('INSERT INTO central_execution_runtime_leases VALUES(%s,%s,%s,%s)',
                    ('mock:' + ref, authority, now - timedelta(seconds=2), now))
                cursor.execute('INSERT INTO central_documents VALUES(%s,%s,%s,%s,%s::jsonb)',
                    ('server_account_settings', 'kiwoom:real:' + ref, 'settings', now.timestamp(), json.dumps(settings)))
        store = PostgresQueryStore(url, **baseline.DEFAULT_CONFIG)
        binding = AccountBinding('profile-1', AccountScope('kiwoom', AccountEnvironment.REAL, ref), 3, now)
        recovery = AccountRecovery(AccountSnapshot(ref, 10000, 0, {'005930': 3}, now), ())
        ownership = {'owner_key': 'mock:' + ref, 'owner_token': authority, 'run_id': run}
        snapshot = {'snapshot_id': 'snapshot-1', 'environment': 'mock', 'account_ref': ref,
                    'as_of': now.isoformat(), 'received_at': now.isoformat()}
        intent = {'intent_id': 'intent-1', 'run_id': run, 'environment': 'mock', 'account_ref': ref,
                  'state': 'CREATED', 'created_at': now.isoformat(), 'updated_at': now.isoformat()}
        event = {'event_id': 'event-1', 'intent_id': 'intent-1', 'state': 'SUBMITTED',
                 'occurred_at': now.isoformat(), 'received_at': now.isoformat()}
        vi = {'event_id': 'vi-1', 'event_key': 'vi-key', 'stock_code': '005930', 'event_kind': 'start',
              'vi_type': 'dynamic', 'received_at': now.timestamp(), 'available_at': now.timestamp()}
        prior_intent = {**intent, 'intent_id': 'prior-intent', 'state': 'SUBMITTED'}
        prior_event = {**event, 'event_id': 'prior-event', 'intent_id': 'prior-intent'}
        prior_snapshot = {**snapshot, 'snapshot_id': 'prior-snapshot'}
        self.assertTrue(store.create_execution_intent(prior_intent))
        self.assertTrue(store.append_execution_event(prior_intent, prior_event))
        self.assertTrue(store.save_execution_account_snapshot(prior_snapshot))
        store.save_real_account_recovery(binding, recovery, now, settings_revision=2)
        with baseline.ReplayDatabaseLease(url, token) as lease:
            with lease.connection.transaction(), lease.connection.cursor() as cursor:
                cursor.execute('ALTER SEQUENCE public.central_execution_events_accepted_sequence_seq RESTART WITH 13')
        large_document = {'frames': [{'symbol': '005930', 'value': '가' * 3000} for _ in range(950)]}
        self.assertGreater(len(json.dumps(large_document, ensure_ascii=False).encode()), 8 * 1024**2)
        with deferred_capture(account_inputs=True, large_inputs=True, account_context_store=store) as (identifier, _):
            with capture_owner('account', 'account:fixture', 'account-actor'), source_execution_owner('mock:' + ref, run):
                store.save_real_account_recovery(binding, recovery, now, settings_revision=2)
                with self.assertRaisesRegex(ValueError, 'ACCOUNT_CONTEXT_MISMATCH'):
                    store.save_real_account_recovery(binding, recovery, now, settings_revision=1)
                with self.assertRaisesRegex(RuntimeError, 'EXECUTION_OWNERSHIP_LOST'):
                    store.save_execution_account_snapshot(snapshot, ownership=ownership)
                self.assertTrue(store.acquire_execution_runtime('mock:' + ref, authority,
                    now.isoformat(), (now + timedelta(seconds=60)).isoformat()))
                self.assertFalse(store.create_execution_intent(prior_intent, ownership=ownership))
                self.assertFalse(store.append_execution_event(prior_intent, prior_event, ownership=ownership))
                self.assertFalse(store.save_execution_account_snapshot(prior_snapshot, ownership=ownership))
                self.assertTrue(store.append_execution_event({**prior_intent, 'state': 'FILLED'},
                    {**prior_event, 'event_id': 'prior-followup', 'state': 'FILLED'}, ownership=ownership))
                self.assertTrue(store.save_execution_account_snapshot(snapshot, ownership=ownership))
                self.assertTrue(store.create_execution_intent(intent, ownership=ownership))
                self.assertTrue(store.append_execution_event({**intent, 'state': 'SUBMITTED'}, event, ownership=ownership))
                balance = AccountBalanceChange('005930', 'fixture', 3, 3, 100, 300, 10000, 100,
                                                origin_scope=binding.scope)
                store.save_real_account_event(binding, 'account_balance', balance, now, settings_revision=2)
                self.assertTrue(store.release_execution_runtime('mock:' + ref, authority))
                with self.assertRaisesRegex(RuntimeError, 'EXECUTION_OWNERSHIP_LOST'):
                    store.save_execution_account_snapshot(snapshot, ownership=ownership)
            with capture_owner('market_events', 'vi:fixture', 'vi-actor'):
                self.assertEqual(1, store.append_vi_events([vi]))
                self.assertEqual(0, store.append_vi_events([vi]))
            with capture_owner('shadow', 'shadow:fixture', 'shadow-actor'):
                store.save_shadow_monitor_state('large-monitor', large_document)
            final = persist_now()
            # Verification is outside capture; it is not a recorded reader input.
            self.assertEqual(large_document, store.load_shadow_monitor_state('large-monitor'))
            self.assertEqual((0, 0), (final['input_rejected'], final['known_dropped']))
            capsule = context.read_recorded_account_context(identifier)
            self.assertEqual(13, capsule['execution_event_next_sequence'])
            self.assertEqual(1, len(capsule['tables']['central_execution_intents']))
            self.assertEqual(1, len(capsule['tables']['central_execution_events']))
            manifest, rows = trace.recorded_events(identifier)
            from kiwoom_monitor.central_server.diagnostic_trace_payload import BlockPayloadReference
            large_rows = [row for row in rows if row.get('event_type') == 'operation_start'
                          and row.get('method') == 'save_shadow_monitor_state']
            self.assertEqual(1, len(large_rows))
            self.assertIsInstance(large_rows[0]['payload'], BlockPayloadReference)
            # The offline reader replaces payload_ref with a pinned descriptor.
            large_descriptor = json.loads(large_rows[0]['payload'].descriptor)
            self.assertGreater(len(large_descriptor['parts']), 1)
            self.assertGreater(large_descriptor['bytes'], 8 * 1024**2)
            recorded = tempfile.TemporaryDirectory()
            self.addCleanup(recorded.cleanup)
            shutil.copytree(trace._directory() / identifier, Path(recorded.name) / identifier)
        self.enterContext(patch.object(trace, '_directory', return_value=Path(recorded.name)))
        origin = datetime.fromtimestamp(manifest['started_at'], timezone.utc)
        source_start = manifest['started_mono_ns']
        # The public reader requires the requested end to lie inside the actual
        # recording; an artificial post-capture margin is not recorded input.
        duration = (manifest['finished_mono_ns'] - source_start) / 1e9
        with baseline.ReplayDatabaseLease(url, token) as lease:
            from kiwoom_monitor.central_server.diagnostic_replay_comparison import collect_final_content_comparison
            with lease.connection.cursor() as cursor:
                source_content = collect_final_content_comparison(cursor,
                    max_bytes=baseline.MAX_BASELINE_BYTES, max_rows=baseline.MAX_BASELINE_ROWS)
                ledger_tables = ('central_execution_intents', 'central_execution_events', 'central_execution_account_snapshots')
                source_ledger = lease._tables_digest(cursor, 'public', tables=ledger_tables)
                source_ledger_sequence = lease._sequences(cursor, sequences=(context.EVENT_SEQUENCE,))
            lease.restore(parent)
            with lease.connection.transaction(), lease.connection.cursor() as cursor:
                # Set the explicit cold fixture after observing source results.
                # This modifies only the disposable DB's new v3 tables.
                cursor.execute(sql.SQL('TRUNCATE {}').format(sql.SQL(',').join(
                    sql.Identifier('public', name) for name in baseline.TABLES_V3 if name not in baseline.TABLES_V2)))
        owner_rows = [row for row in rows if row.get('event_type') == 'operation_start']
        options = dict(baseline_version=3, cache_clock=Top20FixtureClock(origin))
        with baseline.ReplayDatabaseLease(url, token, **options) as lease:
            with lease.connection.cursor() as cursor:
                before = lease._tables_digest(cursor, 'public')
                sequences = lease._sequences(cursor)
            original = context._apply_account_context
            def fail_after_apply(*args, **kwargs):
                original(*args, **kwargs)
                raise RuntimeError('controlled_atomic_seal_failure')
            with patch.object(context, '_apply_account_context', side_effect=fail_after_apply):
                with self.assertRaisesRegex(RuntimeError, 'controlled_atomic_seal_failure'):
                    lease.seal(account_context=capsule, owner_rows=owner_rows)
            with lease.connection.cursor() as cursor:
                self.assertEqual(before, lease._tables_digest(cursor, 'public'))
                self.assertEqual(sequences, lease._sequences(cursor))
                self.assertIsNone(lease._baseline_row(cursor))
        arguments = ['--baseline-version', '3', '--source-origin', origin.isoformat(),
                     '--trace-id', identifier, '--window-start', '0', '--window-end', str(duration)]
        output = io.StringIO()
        self.assertEqual(0, cli.main(['seal', *arguments], environ={cli._URL_ENV: url, cli._TOKEN_ENV: token},
                                    output=output), output.getvalue())
        sealed = json.loads(output.getvalue())['result']
        baseline_id = sealed['baseline_id']
        results = []
        original_comparison = collect_final_content_comparison
        def check_native_large_content(cursor, **options):
            cursor.execute("SELECT document_json FROM public.central_shadow_monitor_state WHERE monitor_id='large-monitor'")
            self.assertEqual(large_document, cursor.fetchone()[0])
            cursor.execute("SELECT accepted_sequence FROM public.central_execution_events WHERE event_id='prior-followup'")
            # PostgreSQL ON CONFLICT consumes13 for the duplicate event.
            self.assertEqual(14, cursor.fetchone()[0])
            return original_comparison(cursor, **options)
        for _ in range(2):
            output = io.StringIO()
            with patch('kiwoom_monitor.central_server.diagnostic_replay_comparison.collect_final_content_comparison',
                       side_effect=check_native_large_content):
                self.assertEqual(0, cli.main(['run', '--baseline-id', baseline_id, *arguments],
                    environ={cli._URL_ENV: url, cli._TOKEN_ENV: token}, output=output), output.getvalue())
            result = json.loads(output.getvalue())['result']
            observed = result['db_calls']
            identifiers = {value['replay_operation_id'] for value in result['calls']}
            native_calls = [value for value in observed['calls'] if value.get('input_operation_id') in identifiers]
            self.assertEqual(17, len(native_calls))
            self.assertEqual(14, sum(value['commits'] for value in native_calls))
            shadow_ids = {row['replay_operation_id'] for row in result['calls'] if row['method'] == 'save_shadow_monitor_state'}
            shadow_calls = [row for row in native_calls if row['input_operation_id'] in shadow_ids]
            self.assertEqual(1, len(shadow_calls))
            self.assertEqual((1, 0), (shadow_calls[0]['commits'], shadow_calls[0]['rollbacks']))
            self.assertEqual(3, sum(value['rollbacks'] for value in native_calls))
            self.assertFalse(observed.get('dropped'))
            self.assertFalse(observed.get('raw_truncated'))
            self.assertTrue(result['db_observation_complete'])
            self.assertEqual([], result['unobserved_replay_operations'])
            self.assertEqual('complete', result['state'], result['calls'])
            self.assertTrue(result['source_outcomes_match'])
            self.assertEqual(17, len(result['calls']))
            self.assertEqual(3, sum(row.get('expected_failure_matched', False) for row in result['calls']))
            self.assertTrue(result['cleanup']['baseline_restored'])
            self.assertEqual(2, result['final_tables']['central_execution_intents']['rows'])
            self.assertEqual(3, result['final_tables']['central_execution_events']['rows'])
            self.assertEqual(1, result['final_tables']['central_vi_event_revisions']['rows'])
            for table in ledger_tables:
                self.assertEqual(source_ledger[table], result['final_tables'][table])
            self.assertEqual(source_ledger_sequence[context.EVENT_SEQUENCE], result['final_sequences'][context.EVENT_SEQUENCE])
            self.assertFalse(result['source_state_equivalent'])
            results.append(result)
        for table in ('central_execution_intents', 'central_execution_events', 'central_execution_account_snapshots',
                      'central_vi_event_revisions', 'central_account_registry', 'central_account_binding_revisions'):
            self.assertEqual(results[0]['final_tables'][table], results[1]['final_tables'][table])
        self.assertEqual(results[0]['final_sequences'], results[1]['final_sequences'])
        for value in results[0]['final_content_comparison']['tables'].values():
            self.assertFalse(value['lineage_errors'])
        self.assertEqual(results[0]['final_content_comparison']['tables']['central_documents']['sha256'],
                         results[1]['final_content_comparison']['tables']['central_documents']['sha256'])
        self.assertEqual(source_content['tables']['central_documents']['sha256'],
                         results[0]['final_content_comparison']['tables']['central_documents']['sha256'])
        with baseline.ReplayDatabaseLease(url, token, baseline_version=3, cache_clock=Top20FixtureClock(origin)) as lease:
            self.assertEqual(0, lease.status()['owned_connections'])
            with lease.connection.cursor() as cursor:
                self.assertEqual(sealed['manifest']['tables'], lease._tables_digest(cursor, 'public'))
                self.assertEqual(sealed['manifest']['sequences'], lease._sequences(cursor))
        # Machine-readable evidence contains no source/test authority tokens.
        print(json.dumps({'account_vi_native_gate': {
            'operations_per_replay': 17, 'replay_runs': 2, 'matched_native_failures_per_run': 3,
            'commits_per_replay': 14, 'rollbacks_per_replay': 3, 'source_document_content_matches': True,
            'preexisting_ledger_and_sequence_gap_preserved': True,
            'final_ledger_content_and_sequence_match_source': True,
            'large_input_blocks_one_native_call_one_commit': True,
            'large_input_stored_content_matches_source': True,
            'capture_rejected': 0, 'capture_dropped': 0, 'atomic_seal_rollback_verified': True,
            'content_sequences_match': True, 'baseline_restored': True,
            'source_state_equivalent': False}}))
