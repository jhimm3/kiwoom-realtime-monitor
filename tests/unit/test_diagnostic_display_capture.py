from __future__ import annotations

import asyncio
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
import time
import unittest
from unittest.mock import patch

from kiwoom_monitor.central_server import diagnostic_account_context as context
from kiwoom_monitor.central_server import diagnostic_trace as trace
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.diagnostic_account_input import AccountInputProjection
from kiwoom_monitor.central_server.diagnostic_replay_contract import (
    ACCOUNT_CLOCK_COLLECTION, capture_owner, compile_recorded_plan, freeze_payload,
    validate_operation,
)
from kiwoom_monitor.central_server.diagnostic_top20_display_input import (
    DisplayReplay, environment_scope, environment_value, prepare_environment,
)
from tests.unit.test_diagnostic_trace_deferred import deferred_capture
from tests.unit.test_diagnostic_trace_blocks import persist_now
from tests.unit.test_diagnostic_account_context import REF, NOW, SQLiteProjectionCursor
from tests.unit.test_recorded_execution import execute
from tests.unit.test_top20_reconciliation import DAY, MINUTE, inputs
from tests.unit import test_top20_reconciliation as reconciliation_fixture
from kiwoom_monitor.domain.top20_reconciliation import build_top20_reconciliation


def clock_document():
    return {'version': 'mock_automation_session_clock/v1', 'account_ref': REF,
        'spec_id': 'spec', 'execution_run_id': 'run', 'candidate_package_hash': 'hash',
        'control_revision': 3, 'cancel_attempted_intent_ids': ['already-cancelled'],
        'phase': 'STOPPED', 'pending_intent_id': 'pending', 'unresolved_intent_ids': ['pending'],
        'stop_revision_id': 'stop-revision', 'decision': {'decision_id': 'decision'}}


@contextmanager
def captured_rows(*, account=True):
    rows, rejections = [], []
    starts = {}
    def append(kind, fields, payload=None):
        row = deepcopy(fields)
        row.update(event_type=kind, seq=len(rows) + 1)
        if kind == 'operation_start':
            row['entered_mono_ns'] = 1_000_000_000 + len(starts) * 1_000_000
            starts[row['operation_id']] = row['entered_mono_ns']
            row['payload'] = freeze_payload(payload).value
        elif kind == 'operation_end':
            row['entered_mono_ns'] = starts[row['operation_id']]
            row['finished_mono_ns'] = row['entered_mono_ns'] + 500_000
        rows.append(row)
    with patch.object(trace, 'input_token', side_effect=lambda flag: None if flag == 'account_inputs' and not account else 'fixture'), \
         patch.object(trace, 'emit_payload', side_effect=lambda tid, kind, fields, value: append(kind, fields, value)), \
         patch.object(trace, 'emit', side_effect=lambda tid, kind, fields: append(kind, fields)), \
         patch.object(trace, 'reject_input', side_effect=lambda tid, fields: rejections.append(fields)), \
         capture_owner('top20', 'fixture', 'actor'):
        yield rows, rejections


class DisplayCaptureTests(unittest.TestCase):
    def store(self):
        store = SQLiteQueryStore(Path(':memory:'))
        store.initialize()
        self.addCleanup(store.close)
        return store

    def seed(self, store):
        reconciliation_fixture.NativeStoreTests.seed(SimpleNamespace(store=store))

    def plan(self, rows):
        return compile_recorded_plan(rows, started_mono_ns=1_000_000_000,
            window_start_seconds=0, window_end_seconds=.2)

    def test_display_capture_native_replay_twice_keeps_results_cache_and_initial_state(self):
        source = self.store()
        self.seed(source)
        with captured_rows() as (rows, rejected), \
             patch('kiwoom_monitor.central_server.database_top20_reconciliation._top20_today', return_value=date(2026, 10, 2)), \
             patch('kiwoom_monitor.central_server.database_top20_reconciliation.is_paused', return_value=False):
            source.load_top20_reconciliation_inputs(DAY)
            source.load_top20_series(DAY)
            source.load_top20_display_statistics(DAY, DAY)  # Cache miss writes.
            source.load_top20_display_statistics(DAY, DAY)  # Cache hit.
        self.assertEqual([], rejected)
        self.assertEqual(4, len(self.plan(rows).operation_ids))
        expected = source.load_dataset_snapshots('top20_statistics_display_day', DAY)
        for _ in range(2):
            target = self.store()
            self.seed(target)
            # Any fallback to host time or operating control fails this test.
            with patch('kiwoom_monitor.central_server.database_top20_reconciliation._top20_today', side_effect=AssertionError('host day')), \
                 patch('kiwoom_monitor.central_server.database_top20_reconciliation.is_paused', side_effect=AssertionError('operating pause')), \
                 patch('kiwoom_monitor.central_server.database_top20_reconciliation.time', side_effect=AssertionError('host timestamp')):
                result = asyncio.run(execute(target, rows))
            self.assertEqual('complete', result['state'])
            self.assertTrue(result['source_outcomes_match'])
            self.assertTrue(all(call['source_result_matched'] for call in result['calls']))
            self.assertEqual(expected, target.load_dataset_snapshots('top20_statistics_display_day', DAY))

    def test_reconciled_write_read_and_mixed_batch_keep_raw_and_display_separate(self):
        source = self.store()
        value = build_top20_reconciliation(inputs(), available_at=NOW.isoformat())
        with captured_rows() as (rows, rejected):
            source.save_dataset_snapshots([
                ('top20_index_reconciled', DAY, DAY, value, None),
                ('top20_index', DAY, MINUTE, {'minute': MINUTE, 'market_values': [1, 0, 0]}, None)])
            source.load_top20_series(DAY)
            source.load_top20_series(DAY, basis='realtime')
        self.assertEqual([], rejected)
        self.assertEqual(3, len(self.plan(rows).operation_ids))
        target = self.store()
        result = asyncio.run(execute(target, rows))
        self.assertEqual('complete', result['state'])
        self.assertEqual(source.load_top20_series(DAY), target.load_top20_series(DAY))
        self.assertEqual('realtime', target.load_top20_series(DAY, basis='realtime')['basis'])
        self.assertEqual([], target.load_observation_revisions('top20_index_reconciled', DAY))

    def test_source_pause_changes_per_day_are_replayed_at_each_consumption(self):
        source = self.store()
        with captured_rows() as (rows, rejected), \
             patch('kiwoom_monitor.central_server.database_top20_reconciliation._top20_today', return_value=date(2026, 10, 3)), \
             patch('kiwoom_monitor.central_server.database_top20_reconciliation.is_paused', side_effect=[False, True]):
            source.load_top20_display_statistics(DAY, '2026-10-02')
        self.assertEqual([], rejected)
        target = self.store()
        self.assertEqual('complete', asyncio.run(execute(target, rows))['state'])
        self.assertEqual(1, len(target.load_dataset_snapshots('top20_statistics_display_day', DAY)))
        self.assertEqual([], target.load_dataset_snapshots('top20_statistics_display_day', '2026-10-02'))

    def test_changed_initial_data_fails_content_receipt_even_with_same_result_shape(self):
        source, target = self.store(), self.store()
        self.seed(source)
        source.save_dataset_snapshot('top20_index_reconciled', DAY, DAY,
            build_top20_reconciliation(inputs(), available_at=NOW.isoformat()))
        self.assertNotEqual(source.load_top20_series(DAY), target.load_top20_series(DAY))
        with captured_rows() as (rows, _):
            source.load_top20_series(DAY)
        result = asyncio.run(execute(target, rows))
        self.assertEqual('incomplete', result['state'])
        self.assertFalse(result['calls'][0]['source_result_matched'])

    def test_environment_corruption_and_old_contract_fail_before_native_call(self):
        source = self.store()
        with captured_rows() as (rows, _):
            source.load_top20_display_statistics(DAY, DAY)
        for mutation in ('missing', 'type', 'ordinal', 'receipt', 'version'):
            altered = deepcopy(rows)
            environment = next(row for row in altered if row['event_type'] == 'top20_display_environment')
            ending = altered[-1]
            if mutation == 'missing':
                altered.remove(environment)
                for seq, row in enumerate(altered, 1): row['seq'] = seq
            elif mutation == 'type': environment['value'] = True
            elif mutation == 'ordinal': environment['ordinal'] = 2
            elif mutation == 'receipt': ending['display_environment_sha256'] = '0' * 64
            else: altered[0].pop('display_input_version')
            target = self.store()
            with self.subTest(mutation=mutation), patch.object(target, 'load_top20_display_statistics') as native:
                with self.assertRaises(ValueError): asyncio.run(execute(target, altered))
                native.assert_not_called()

    def test_scope_isolation_and_cleanup_after_exception(self):
        async def actor(value):
            replay = DisplayReplay((('saved_at', value),))
            with environment_scope(replay):
                await asyncio.sleep(0)
                self.assertEqual(value, environment_value('saved_at', lambda: 999))
                replay.finish()
        async def run():
            await asyncio.gather(actor(1), actor(2))
        asyncio.run(run())
        with self.assertRaises(RuntimeError):
            with environment_scope(DisplayReplay(())): raise RuntimeError('injected')
        self.assertEqual(999, environment_value('saved_at', lambda: 999))

    def test_capture_observation_failure_preserves_native_result_and_rejects_input(self):
        source = self.store()
        with captured_rows() as (_, rejected), patch.object(trace, 'emit', side_effect=OSError('observer')):
            self.assertEqual('display', source.load_top20_display_statistics(DAY, DAY)['basis'])
        self.assertTrue(rejected)

    def test_failed_result_observation_still_closes_owned_operation(self):
        source = self.store()
        with captured_rows() as (rows, rejected), \
             patch('kiwoom_monitor.central_server.diagnostic_top20_display_input.result_digest', side_effect=ValueError('limit')):
            self.assertEqual('display', source.load_top20_display_statistics(DAY, DAY)['basis'])
        self.assertTrue(rejected)
        self.assertEqual('operation_end', rows[-1]['event_type'])
        self.assertEqual('returned', rows[-1]['outcome'])
        with self.assertRaisesRegex(ValueError, 'result_receipt_missing'): self.plan(rows)

    def test_result_copy_saturation_preserves_native_return_and_releases_after_failure(self):
        from kiwoom_monitor.central_server import diagnostic_top20_display_input as display
        source = self.store()
        self.assertTrue(display._RESULT_COPY_SLOTS.acquire(blocking=False))
        self.assertTrue(display._RESULT_COPY_SLOTS.acquire(blocking=False))
        try:
            with captured_rows() as (rows, rejected):
                self.assertEqual('display', source.load_top20_display_statistics(DAY, DAY)['basis'])
            self.assertTrue(rejected)
            self.assertEqual('operation_end', rows[-1]['event_type'])
        finally:
            display._RESULT_COPY_SLOTS.release()
            display._RESULT_COPY_SLOTS.release()
        with self.assertRaisesRegex(ValueError, 'sensitive_payload_field'):
            display.result_digest({'owner_token': 'secret'})
        self.assertEqual(display.result_digest({'valid': 1}), display.result_digest({'valid': 1}))

    def test_saved_scoped_window_keeps_display_environment_receipts(self):
        source = self.store()
        self.seed(source)
        with deferred_capture() as (identifier, _):
            with capture_owner('top20', 'fixture', 'actor'):
                source.load_top20_display_statistics(DAY, DAY)
            time.sleep(.21)
            final = persist_now()
            self.assertEqual(0, final['known_dropped'])
            self.assertEqual(0, final['input_rejected'])
            for policy in ('complete', 'scoped-operations'):
                manifest, rows = trace.recorded_window_events(identifier, window_start_seconds=0,
                    window_end_seconds=.2, mode='recorded_operations', capture_policy=policy,
                    include_workloads=('top20',))
                plan = compile_recorded_plan(rows, started_mono_ns=manifest['started_mono_ns'],
                    window_start_seconds=0, window_end_seconds=.2, include_workloads=('top20',))
                self.assertEqual(1, len(plan.operation_ids))
                self.assertTrue(any(row['event_type'] == 'top20_display_environment' for row in rows))

    def test_clock_initial_context_update_retry_and_replay_require_v4(self):
        source = self.store()
        initial = clock_document()
        source.upsert_documents(ACCOUNT_CLOCK_COLLECTION, [{'owner': REF, 'key': 'current', 'document': initial}])
        capsule = context.read_account_context(source, AccountInputProjection('fixture'), source_release='fixture')
        self.assertEqual('account-context/v4', capsule['version'])
        self.assertEqual(ACCOUNT_CLOCK_COLLECTION, capsule['tables']['central_documents'][0]['collection'])
        changed = dict(initial, cancel_attempted_intent_ids=['already-cancelled', 'next-cancel'])
        with captured_rows() as (rows, rejected):
            source.load_documents(ACCOUNT_CLOCK_COLLECTION, REF, 1)
            source.upsert_documents(ACCOUNT_CLOCK_COLLECTION, [{'owner': REF, 'key': 'current', 'document': changed}])
            source.upsert_documents(ACCOUNT_CLOCK_COLLECTION, [{'owner': REF, 'key': 'current', 'document': changed}])
        self.assertEqual([], rejected)
        self.assertTrue(all(row['workload_id'] == 'account' for row in rows))
        for _ in range(2):
            target = self.store()
            with target._connection() as connection:
                context._apply_account_context(SQLiteProjectionCursor(connection.cursor()), capsule, bindings={})
            target._lease = SimpleNamespace(baseline_version=3)
            target._execution_wall_now = lambda: NOW
            self.assertEqual('complete', asyncio.run(execute(target, rows, account_context=capsule))['state'])
            self.assertEqual(changed, target.load_document(ACCOUNT_CLOCK_COLLECTION, REF, 'current')['document'])
        old = deepcopy(capsule)
        old['version'] = 'account-context/v3'
        old['tables']['central_documents'] = []
        old['row_count'] -= 1
        old['sha256'] = context._digest({key:value for key,value in old.items() if key != 'sha256'})[0]
        context.validate_account_context(old)
        with self.assertRaisesRegex(ValueError, 'requires_v4_context'):
            asyncio.run(execute(target, rows, account_context=old))

    def test_clock_flag_off_and_secret_rejection_keep_native_write(self):
        source = self.store()
        with captured_rows(account=False) as (rows, rejected):
            source.upsert_documents(ACCOUNT_CLOCK_COLLECTION, [{'owner':REF, 'key':'current', 'document':clock_document()}])
        self.assertEqual(1, len(rejected))
        self.assertEqual('excluded_operation', rejected[0]['reason'])
        self.assertFalse(any(row['event_type'] == 'operation_start' for row in rows))
        self.assertIsNotNone(source.load_document(ACCOUNT_CLOCK_COLLECTION, REF, 'current'))
        bad = dict(clock_document(), owner_token='secret')
        with self.assertRaisesRegex(ValueError, 'sensitive_payload_field'):
            validate_operation('upsert_documents', {'collection':ACCOUNT_CLOCK_COLLECTION,
                'values':[{'owner':REF,'key':'current','document':bad}]})

    def test_old_contexts_do_not_admit_or_reset_new_clock_collection(self):
        source = self.store()
        source.upsert_documents(ACCOUNT_CLOCK_COLLECTION, [{'owner':REF,'key':'current','document':clock_document()}])
        capsule = context.read_account_context(source, AccountInputProjection('fixture'), source_release='fixture')
        for version in ('account-context/v1','account-context/v2','account-context/v3'):
            old = deepcopy(capsule)
            old['version'] = version
            with self.assertRaisesRegex(ValueError, 'document_scope_invalid'):
                old['sha256'] = context._digest({k:v for k,v in old.items() if k != 'sha256'})[0]
                if version == 'account-context/v1':
                    old['tables'] = {k:old['tables'][k] for k in context.COLUMNS_V1}
                    old.pop('execution_event_next_sequence')
                if version != 'account-context/v3': old.pop('archive_index')
                old['sha256'] = context._digest({k:v for k,v in old.items() if k != 'sha256'})[0]
                context.validate_account_context(old)
            old['tables']['central_documents'] = []
            old['row_count'] = 0
            old['sha256'] = context._digest({k:v for k,v in old.items() if k != 'sha256'})[0]
            target = self.store()
            target.upsert_documents(ACCOUNT_CLOCK_COLLECTION,[{'owner':REF,'key':'current','document':clock_document()}])
            with target._connection() as connection:
                context._apply_account_context(SQLiteProjectionCursor(connection.cursor()), old, bindings={})
            self.assertEqual(clock_document(), target.load_document(ACCOUNT_CLOCK_COLLECTION,REF,'current')['document'])


if __name__ == '__main__':
    unittest.main()
