from __future__ import annotations

from kiwoom_monitor.central_server.diagnostic_top20_lifecycle_input import CollectorInputRecorder, HubInputRecorder

import asyncio
import json
import tempfile
import threading
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.central_server import diagnostic_trace as trace
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.database_query_cache import StoredQuery
from kiwoom_monitor.central_server.diagnostic_replay_contract import (
    CODEC_VERSION, COLLECTOR_INPUT_VERSION, MAX_NODES, InputRejected, capture_owner, compile_recorded_plan, freeze_payload,
    install_store_capture, thaw_payload,
)
from kiwoom_monitor.central_server.diagnostic_workloads import _set_tool, _set_trace
from kiwoom_monitor.central_server.postgres_access import DBWriterContext, _trace_start
from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
from tests.unit.test_diagnostic_trace_deferred import recorder_storage_headroom


@contextmanager
def capture(*, store_inputs=True, collector_inputs=True):
    with tempfile.TemporaryDirectory() as root:
        control = Path(root) / 'control.json'
        with recorder_storage_headroom(), patch.object(trace, 'control_path', return_value=control), patch(
                'kiwoom_monitor.central_server.diagnostic_workloads.control_path', return_value=control):
            master = _set_tool(control, True, 300)['diagnostic_tool']['session_id']
            _set_trace(control, True, 120, expected_session=master)
            session = trace.start(seconds=60, store_inputs=store_inputs, collector_inputs=collector_inputs)
            try:
                yield session
            finally:
                trace.stop()
                _set_tool(control, False)


def rejected_evidence(identifier):
    """Inspect committed evidence, without opening an incomplete trace for replay."""
    manifest = trace.status(identifier)
    rows = []
    for part in manifest['chunks']:
        for line in trace.chunk_bytes(identifier, part['name']).splitlines():
            row = json.loads(line)
            digest = row.pop('payload_ref', None)
            if digest is not None:
                row['payload'] = json.loads(trace.payload_bytes(identifier, digest))
            rows.append(row)
    return manifest, rows


class NativeStore:
    def __init__(self):
        self.calls = []
        install_store_capture(self)

    def save_dataset_snapshot(self, kind, subject, snapshot_key, payload, *, observation=None):
        self.save_dataset_snapshots([(kind, subject, snapshot_key, payload, observation)])

    def save_dataset_snapshots(self, values):
        context = DBWriterContext('dataset', values[0][0], 'native')
        trace.emit(trace.token(), 'call_start', _trace_start(context))
        self.calls.append(values)
        trace.emit(trace.token(), 'call_end', {'call_id': context.call_id, **context.recorded_identity})

    def save_realtime_snapshots(self, values):
        self.calls.append(values)

    def load_documents(self, collection, owner="", limit=1000, offset=0, *, subject_prefix=None):
        return []

    def load_daily_bars(self, code, limit=250):
        if code == 'fail':
            raise LookupError('native failure')
        return []


class RecordedCaptureTests(unittest.TestCase):
    def test_native_recent_minutes_preserve_market_day_and_overlay_through_recorded_execution(self):
        from kiwoom_monitor.central_server.diagnostic_recorded_execution import (
            _execute_recorded_operations, _result_digest,
        )
        from kiwoom_monitor.central_server.diagnostic_replay_contract import thaw_operation_arguments

        now = datetime.fromisoformat('2026-10-16T11:00:00+09:00')
        with tempfile.TemporaryDirectory() as root:
            store = SQLiteQueryStore(Path(root) / 'recent-recorded.sqlite3', now_provider=lambda: now)
            install_store_capture(store)
            store.initialize()
            self.addCleanup(store.close)
            base = dict(code='005930', minute='10:00', market='KRX', open=100, high=110,
                        low=90, close=105, volume=10, trade_value_million_won=20, updated_at=now.timestamp())
            store.save_minute_bars([
                {**base, 'trading_date': '2026-10-14'},
                {**base, 'trading_date': '2026-10-15', 'market': 'SOR', 'volume': 7},
                {**base, 'trading_date': '2026-10-16', 'market': 'UNKNOWN', 'volume': 99},
            ])
            deltas = [
                {**base, 'trading_date': '2026-10-14', 'volume': 3, 'operation_id': 'recorded-existing'},
                {**base, 'trading_date': '2026-10-16', 'volume': 4, 'operation_id': 'recorded-ram-day'},
            ]
            with capture(store_inputs=True, collector_inputs=False) as session:
                with capture_owner('readers', 'recent:source', 'recent:actor'):
                    results = [store.load_recent_minute_bars('005930', '2026-09-16', '2026-10-16', market, 2,
                        realtime_deltas=deltas) for market in ('COMBINED', 'KRX')]
                trace.stop()
                manifest, events = trace.recorded_events(session['trace_id'])
            self.assertEqual(0, manifest['input_rejected'])
            self.assertEqual([
                [('2026-10-15', 'SOR', 7), ('2026-10-16', 'KRX', 4)],
                [('2026-10-14', 'KRX', 13), ('2026-10-16', 'KRX', 4)],
            ], [[(row['trading_date'], row['market'], row['volume']) for row in rows] for rows in results])
            starts = [row for row in events if row['event_type'] == 'operation_start'
                      and row['method'] == 'load_recent_minute_bars']
            self.assertEqual(2, len(starts))
            endings = {row['operation_id']: row for row in events if row['event_type'] == 'operation_end'}
            for start, market, result in zip(starts, ('COMBINED', 'KRX'), results, strict=True):
                self.assertEqual(dict(code='005930', start_date='2026-09-16', end_date='2026-10-16',
                    market=market, trading_days=2, realtime_deltas=deltas), thaw_operation_arguments(start))
                ending = endings[start['operation_id']]
                self.assertEqual(('returned', 'list', len(result)),
                                 (ending['outcome'], ending['result_type'], ending['result_count']))
            window_end = max((row['entered_mono_ns'] - manifest['started_mono_ns']) / 1e9
                             for row in starts) + .05
            replay = asyncio.run(_execute_recorded_operations(store, events,
                started_mono_ns=manifest['started_mono_ns'], window_start_seconds=0,
                window_end_seconds=window_end))
            self.assertEqual('complete', replay['state'])
            self.assertEqual([row['operation_id'] for row in starts],
                             [row['source_operation_id'] for row in replay['calls']])
            self.assertEqual([_result_digest(result)[0] for result in results],
                             [row['result_digest'] for row in replay['calls']])
            self.assertTrue(all(not row['result_digest_unavailable'] for row in replay['calls']))
            self.assertEqual(['recorded-existing', 'recorded-ram-day'], [row['operation_id'] for row in deltas])

    def test_native_minute_range_and_legacy_full_day_arguments_survive_capture_and_replay(self):
        store = SQLiteQueryStore(Path(':memory:'))
        install_store_capture(store)
        store.initialize()
        self.addCleanup(store.close)
        with capture(store_inputs=True, collector_inputs=False) as session:
            with capture_owner('shadow', 'minute:source', 'minute:actor'):
                self.assertEqual([], store.load_minute_bars('005930', '2026-10-13', 'KRX',
                    start_minute='10:00', end_minute='10:10'))
                self.assertEqual([], store.load_minute_bars('005930', '2026-10-13'))
            trace.stop()
            manifest, events = trace.recorded_events(session['trace_id'])
        self.assertEqual(0, manifest['input_rejected'])
        starts = [row for row in events if row['event_type'] == 'operation_start'
            and row['method'] == 'load_minute_bars']
        self.assertEqual(2, len(starts))
        arguments = [thaw_payload(row['payload']) for row in starts]
        self.assertEqual(('10:00', '10:10'), (arguments[0]['start_minute'], arguments[0]['end_minute']))
        self.assertEqual((None, None), (arguments[1]['start_minute'], arguments[1]['end_minute']))
        for values in arguments:
            self.assertEqual([], store.load_minute_bars(**values))
        # Prior captures have no new optional fields and remain valid native calls.
        self.assertEqual([], store.load_minute_bars(code='005930', trading_date='2026-10-13',
            market='KRX', realtime_deltas=None))

    def test_mixed_market_capture_preserves_order_fields_and_omissions_across_bounded_groups(self):
        now = datetime(2026, 10, 7, 1, tzinfo=timezone.utc)
        hub_observer = HubInputRecorder()
        capture_hub = RealtimeHub(observer=hub_observer)
        collector = CentralRealtimeCollector(lambda: '', 'real', capture_hub, lambda: now, input_observer=CollectorInputRecorder(hub_observer=hub_observer))
        source_rows = [
            {'type': '0B', 'item': '005930_AL', 'values': {'10': '100', '20': '100000', 'unused': 'discard'}},
            {'type': '0w', 'item': ['005930_NX'], 'values': {'20': '100000', '210': '-5', '211': '2',
                '212': '7', '213': '-1', '9201': 'private-account'}},
            {'type': '0J', 'item': '001', 'values': {'20': '100000', '10': '2500.1', '12': '-1.2',
                '14': '99', '252': '100', '255': '200', '253': '30', 'unused': 'discard'}},
            {'type': '0U', 'item': '101', 'values': {'20': '100000', '10': '800.5'}},
        ]
        rows = source_rows * 26 + [{'type': '00', 'values': {'9201': 'private-account'}},
                                  {'type': '0g', 'item': '005930', 'values': {'305': '100'}}]
        with capture(store_inputs=False) as session:
            collector._input_observer.record_message(collector, {'trnm': 'REAL', 'data': rows})
            trace.stop()
            manifest, recorded = trace.recorded_events(session['trace_id'])
            self.assertEqual(0, manifest['input_rejected'])
            messages = [row for row in recorded if row.get('input_kind') == 'message']
            self.assertEqual([100, 4], [len(thaw_payload(row['payload'])['message']['data']) for row in messages])
            self.assertEqual([0, 100], [thaw_payload(row['payload'])['row_offset'] for row in messages])
            self.assertEqual(1, len({thaw_payload(row['payload'])['message_id'] for row in messages}))
            copied = [item for row in messages for item in thaw_payload(row['payload'])['message']['data']]
            self.assertEqual([row['type'] for row in source_rows] * 26, [row['type'] for row in copied])
            self.assertEqual(['005930_NX'], copied[1]['item'])
            self.assertEqual({'20': '100000', '210': '-5', '211': '2', '212': '7', '213': '-1'}, copied[1]['values'])
            self.assertEqual({'00': 1, 'other': 1}, messages[-1]['excluded_types'])
            self.assertTrue(all(row['collector_input_version'] == COLLECTOR_INPUT_VERSION
                                for row in recorded if row['event_type'] == 'collector_input'))
            self.assertNotIn('private-account', json.dumps(recorded))
            self.assertNotIn('discard', json.dumps(recorded))

    def test_window_reader_resolves_only_measured_operation_payloads(self):
        with capture(store_inputs=True, collector_inputs=False) as session:
            manifest = trace.status(session['trace_id'])
            started = manifest['started_mono_ns']
            for identifier, offset in (('selected', 20), ('outside', 50)):
                fields = {
                    'operation_id': identifier, 'method': 'save_dataset_snapshot',
                    'codec_version': CODEC_VERSION, 'workload_id': 'top20',
                    'producer_component': 'top20:source', 'actor_id': 'top20:actor',
                    'actor_known': True, 'actor_sequence': 1,
                    'entered_mono_ns': started + offset * 1_000_000_000,
                }
                arguments = {'kind': 'ranking', 'subject': identifier, 'key': 'key',
                             'payload': {'value': offset}}
                self.assertTrue(trace.emit_payload(
                    session['trace_id'], 'operation_start', fields, arguments,
                ))
                trace.emit(session['trace_id'], 'operation_end', {
                    **fields, 'outcome': 'returned',
                    'finished_mono_ns': started + (offset + 1) * 1_000_000_000,
                })
            trace.stop()
            active = trace._SESSION
            active['finished_mono_ns'] = active['started_mono_ns'] + 60 * 1_000_000_000
            trace._manifest(trace._directory() / session['trace_id'], active)
            result_manifest, rows = trace.recorded_window_events(
                session['trace_id'], window_start_seconds=10, window_end_seconds=40,
                mode='recorded_operations',
            )
            starts = {row['operation_id']: row for row in rows if row['event_type'] == 'operation_start'}
            self.assertIn('payload', starts['selected'])
            self.assertNotIn('payload', starts['outside'])
            self.assertEqual(1, result_manifest['window_read']['payload_blobs_loaded'])
            self.assertGreater(result_manifest['window_read']['payload_bytes_loaded'], 0)
            plan = compile_recorded_plan(
                rows, started_mono_ns=started, window_start_seconds=10,
                window_end_seconds=40,
            )
            self.assertEqual(('selected',), plan.operation_ids)
            with self.assertRaisesRegex(ValueError, 'exceeds_capture_duration'):
                trace.recorded_window_events(
                    session['trace_id'], window_start_seconds=0,
                    window_end_seconds=61, mode='recorded_operations',
                )
            with self.assertRaisesRegex(ValueError, 'recorded_window_out_of_bounds'):
                trace.recorded_window_events(
                    session['trace_id'], window_start_seconds='10',
                    window_end_seconds=40, mode='recorded_operations',
                )

    def test_collector_windows_reject_prefixes_over_fifteen_minutes(self):
        with self.assertRaisesRegex(ValueError, 'prefix_exceeds_limit'):
            compile_recorded_plan(
                [], started_mono_ns=1, window_start_seconds=800,
                window_end_seconds=901, mode='collector_with_background',
                collector_components=('realtime_collector:test',),
            )

    def test_late_operation_window_remains_available_without_collector_state(self):
        with capture(store_inputs=True, collector_inputs=False) as session:
            identifier = session['trace_id']
            started = trace.status(identifier)['started_mono_ns']
            for name, offset in (('early', 20), ('late', 3600)):
                fields = {
                    'operation_id': name, 'method': 'save_dataset_snapshot',
                    'codec_version': CODEC_VERSION, 'workload_id': 'top20',
                    'producer_component': 'top20:source', 'actor_id': 'top20:actor',
                    'actor_known': True, 'actor_sequence': 1 if name == 'early' else 2,
                    'entered_mono_ns': started + offset * 1_000_000_000,
                }
                self.assertTrue(trace.emit_payload(identifier, 'operation_start', fields, {
                    'kind': 'ranking', 'subject': name, 'key': 'key', 'payload': {'value': offset},
                }))
                trace.emit(identifier, 'operation_end', {
                    **fields, 'outcome': 'returned',
                    'finished_mono_ns': started + (offset + 1) * 1_000_000_000,
                })
            trace.stop()
            active = trace._SESSION
            active['finished_mono_ns'] = started + 3900 * 1_000_000_000
            trace._manifest(trace._directory() / identifier, active)

            manifest, rows = trace.recorded_window_events(
                identifier, window_start_seconds=3550, window_end_seconds=3650,
                mode='recorded_operations',
            )
            plan = compile_recorded_plan(
                rows, started_mono_ns=started, window_start_seconds=3550,
                window_end_seconds=3650,
            )
            self.assertEqual(('late',), plan.operation_ids)
            self.assertEqual(1, manifest['window_read']['payload_blobs_loaded'])
            self.assertNotIn('payload', next(row for row in rows
                             if row.get('operation_id') == 'early' and row['event_type'] == 'operation_start'))
            with self.assertRaisesRegex(ValueError, 'recorded_window_out_of_bounds'):
                trace.recorded_window_events(
                    identifier, window_start_seconds=3550, window_end_seconds=3650,
                    mode='collector_with_background',
                    collector_components=('realtime_collector:test',),
                )

    def test_window_reader_resolves_collector_prefix_payloads(self):
        with capture(store_inputs=True, collector_inputs=True) as session:
            started = trace.status(session['trace_id'])['started_mono_ns']
            component = 'realtime_collector:test'
            self.assertTrue(trace.emit_payload(
                session['trace_id'], 'collector_input', {
                    'workload_id': 'realtime', 'producer_component': component,
                    'input_kind': 'message', 'input_id': 'input-1',
                }, {'message': {'trnm': 'REAL', 'data': [{'type': '0B'}]}},
            ))
            self.assertTrue(trace.emit_payload(
                session['trace_id'], 'collector_input', {
                    'workload_id': 'realtime', 'producer_component': 'other:collector',
                    'input_kind': 'message', 'input_id': 'input-2',
                }, {'message': {'trnm': 'REAL', 'data': [{'type': '0w'}]}},
            ))
            operation = {
                'operation_id': 'peer-op', 'method': 'save_dataset_snapshot',
                'codec_version': CODEC_VERSION, 'workload_id': 'top20',
                'producer_component': 'top20:source', 'actor_id': 'top20:actor',
                'actor_known': True, 'actor_sequence': 1,
                'entered_mono_ns': started + 20 * 1_000_000_000,
                'cause_input_id': 'input-1',
            }
            self.assertTrue(trace.emit_payload(
                session['trace_id'], 'operation_start', operation,
                {'kind': 'ranking', 'subject': 'peer', 'key': 'key',
                 'payload': {'value': 1}},
            ))
            trace.emit(session['trace_id'], 'operation_end', {
                **operation, 'outcome': 'returned',
                'finished_mono_ns': started + 21 * 1_000_000_000,
            })
            trace.stop()
            active = trace._SESSION
            active['finished_mono_ns'] = active['started_mono_ns'] + 60 * 1_000_000_000
            trace._manifest(trace._directory() / session['trace_id'], active)
            manifest, rows = trace.recorded_window_events(
                session['trace_id'], window_start_seconds=10, window_end_seconds=40,
                mode='collector_with_background', collector_components=(component,),
            )
            inputs = [row for row in rows if row['event_type'] == 'collector_input']
            starts = [row for row in rows if row['event_type'] == 'operation_start']
            self.assertEqual(2, len(inputs))
            selected_inputs = [row for row in inputs if row['producer_component'] == component]
            self.assertEqual(1, len(selected_inputs))
            self.assertEqual('REAL', thaw_payload(selected_inputs[0]['payload'])['message']['trnm'])
            self.assertNotIn('payload', next(row for row in inputs if row['producer_component'] == 'other:collector'))
            self.assertEqual(1, len(starts))
            self.assertEqual({'input-1', 'input-2'}, {row['input_id'] for row in inputs})
            self.assertEqual(2, manifest['window_read']['payload_blobs_loaded'])

    def test_codec_preserves_typed_values_and_rejects_sensitive_or_unbounded_input(self):
        original = {'query': StoredQuery({'price': 42}, False, ''),
                    'at': datetime.now(timezone.utc), 'keys': ('a', 'b')}
        frozen = freeze_payload(original)
        original['query'].payload['price'] = 999
        decoded = thaw_payload(json.loads(json.dumps(frozen.value)))
        self.assertEqual(42, decoded['query'].payload['price'])
        self.assertEqual(('a', 'b'), decoded['keys'])
        self.assertEqual(original['at'], decoded['at'])
        cyclic = []; cyclic.append(cyclic)
        for value in (cyclic, {'access_token': 'must never copy'}, (x for x in range(3))):
            with self.assertRaises(InputRejected):
                freeze_payload(value)
        with self.assertRaises(InputRejected) as byte_error:
            freeze_payload('a' * 1000, maximum_bytes=100)
        self.assertEqual('payload_budget_exceeded', str(byte_error.exception))
        self.assertEqual('bytes', byte_error.exception.details['budget'])
        with self.assertRaises(InputRejected) as node_error:
            freeze_payload([None] * (MAX_NODES + 1), maximum_bytes=16 * 1024 * 1024)
        self.assertEqual('payload_budget_exceeded', str(node_error.exception))
        self.assertEqual('nodes', node_error.exception.details['budget'])
        with self.assertRaises(InputRejected):
            thaw_payload(['enum', [], 'unknown'])

    def test_rejections_include_safe_collection_and_payload_budget_details(self):
        store = NativeStore()
        with capture(store_inputs=True, collector_inputs=False) as session:
            self.assertEqual([], store.load_documents('not_allowlisted', 'scope', 1))
            budget_error = InputRejected('payload_budget_exceeded', details={
                'budget': 'nodes', 'observed_bytes': 4096, 'observed_nodes': 120001,
                'maximum_bytes': 8 * 1024 * 1024, 'maximum_nodes': MAX_NODES,
            })
            with patch('kiwoom_monitor.central_server.diagnostic_replay_contract.freeze_payload',
                       side_effect=budget_error):
                store.save_dataset_snapshot('ranking', 'subject', 'key', {'value': 1})
            trace.stop()
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                trace.recorded_events(session['trace_id'])
            manifest, events = rejected_evidence(session['trace_id'])
            self.assertEqual('incomplete', manifest['state'])
            rejected = [event for event in events if event['event_type'] == 'input_rejected']
            collection = next(event for event in rejected if event.get('method') == 'load_documents')
            self.assertEqual('not_allowlisted', collection['collection'])
            self.assertNotIn('payload', collection)
            budget = next(event for event in rejected if event.get('method') == 'save_dataset_snapshot')
            self.assertEqual('nodes', budget['rejection_detail']['budget'])
            self.assertEqual(MAX_NODES, budget['rejection_detail']['maximum_nodes'])

    def test_off_path_and_observer_failure_preserve_native_result_and_exception(self):
        store = NativeStore()
        with patch('kiwoom_monitor.central_server.diagnostic_replay_contract.freeze_payload',
                   side_effect=AssertionError('OFF must not copy')):
            self.assertEqual([], store.load_daily_bars('ok'))
        with patch.object(trace, 'input_token', return_value='test'), \
             patch.object(trace, 'emit_payload', side_effect=OSError('observer')), \
             patch.object(trace, 'reject_input', side_effect=OSError('observer')), \
             patch.object(trace, 'emit', side_effect=OSError('observer')):
            self.assertEqual([], store.load_daily_bars('ok'))
            with self.assertRaisesRegex(LookupError, 'native failure'):
                store.load_daily_bars('fail')

    def test_public_outer_operation_only_and_db_spans_share_identity_across_threads(self):
        store = NativeStore()
        with capture() as session:
            async def exercise():
                async def worker(actor, subject):
                    with capture_owner('top20', 'top20:source', actor):
                        await asyncio.to_thread(store.save_dataset_snapshot, 'ranking', subject, 'key', {'n': 1})
                await asyncio.gather(worker('a', 'one'), worker('b', 'two'))
            asyncio.run(exercise())
            trace.stop()
            manifest, events = trace.recorded_events(session['trace_id'])
            operations = [row for row in events if row['event_type'] == 'operation_start']
            self.assertEqual(2, len(operations))
            self.assertEqual({'a', 'b'}, {row['actor_id'] for row in operations})
            calls = [row for row in events if row['event_type'] == 'call_start']
            self.assertEqual({row['operation_id'] for row in operations}, {row['input_operation_id'] for row in calls})
            self.assertTrue(all(row['method'] == 'save_dataset_snapshot' for row in operations))
            self.assertEqual(0, manifest['charged_bytes'])
            self.assertEqual(0, manifest['copy_reserved_bytes'])

    def test_actual_0b_source_and_background_compile_without_duplicating_collector_sinks(self):
        store = SQLiteQueryStore(Path(':memory:'))
        install_store_capture(store)
        store.initialize()
        now = datetime(2026, 10, 6, 1, 4, 15, tzinfo=timezone.utc)
        hub_observer = HubInputRecorder()
        capture_hub = RealtimeHub(observer=hub_observer)
        collector = CentralRealtimeCollector(lambda: '', 'real', capture_hub, lambda: now, store, input_observer=CollectorInputRecorder(hub_observer=hub_observer))
        component = f'realtime_collector:{id(collector):x}'
        message = {'trnm': 'REAL', 'data': [
            {'type': '0B', 'item': '005930_AL', 'values': {'10': '10000', '13': '10', '14': '1',
                '15': '2', '17': '10000', '20': '100415', '290': '2'}},
            {'type': '00', 'values': {'9201': 'private-account', 'access_token': 'private-token'}},
        ]}
        try:
            with capture() as session:
                collector._apply_trade_source_approval({('005930', 'SOR')}, now, reset_all=True)
                collector._publish_parsed(message)
                collector._mark_capture_gap()
                # A background task using the very same writer must remain selected.
                with capture_owner('external_market', 'peer:source', 'peer'):
                    store.save_realtime_snapshots([{'event_type': 'trade', 'item_key': 'peer',
                                                  'received_at': now.timestamp(), 'event': {}}])
                asyncio.run(collector._flush_snapshots())
                message['data'][0]['values']['10'] = '77777'
                trace.stop()
                manifest, events = trace.recorded_events(session['trace_id'])
                raw = [row for row in events if row.get('input_kind') == 'message']
                self.assertEqual(1, len(raw))
                value = thaw_payload(raw[0]['payload'])
                self.assertEqual('10000', value['message']['data'][0]['values']['10'])
                self.assertEqual({'00': 1}, raw[0]['excluded_types'])
                self.assertNotIn('private-account', json.dumps(events))
                self.assertNotIn('private-token', json.dumps(events))
                self.assertEqual({'initial_state', 'source_approval', 'message', 'capture_gap'},
                                 {row.get('input_kind') for row in events if row['event_type'] == 'collector_input'})
                plan = compile_recorded_plan(events, started_mono_ns=manifest['started_mono_ns'],
                    window_start_seconds=0, window_end_seconds=59,
                    mode='collector_with_background', collector_components=(component,))
                starts = {row['operation_id']: row for row in events if row['event_type'] == 'operation_start'}
                self.assertTrue(plan.replaced_operation_ids)
                self.assertTrue(all(starts[key]['producer_component'] == component for key in plan.replaced_operation_ids))
                self.assertEqual(('peer:source',), tuple(starts[key]['producer_component'] for key in plan.operation_ids))
                self.assertTrue(all(starts[key]['cause_input_id'] for key in plan.replaced_operation_ids))
                self.assertFalse(plan.execution_ready)
                mixed = [dict(row) for row in events]
                next(row for row in mixed if row.get('input_kind') == 'message')['excluded_types'] = {'0w': 1}
                with self.assertRaisesRegex(ValueError, 'mixed_latest'):
                    compile_recorded_plan(mixed, started_mono_ns=manifest['started_mono_ns'],
                        window_start_seconds=0, window_end_seconds=59,
                        mode='collector_with_background', collector_components=(component,))
                alone = compile_recorded_plan(events, started_mono_ns=manifest['started_mono_ns'],
                    window_start_seconds=0, window_end_seconds=59, include_workloads=('external_market',))
                self.assertEqual(plan.operation_ids, alone.operation_ids)
        finally:
            store.close()

    def test_selection_preserves_other_workloads_and_rejects_gaps_censored_or_mixed_inputs(self):
        store = NativeStore()
        with capture() as session:
            for workload in ('top20', 'news', 'shadow', 'external_market'):
                with capture_owner(workload, workload, workload):
                    store.save_dataset_snapshot('ranking', workload, 'key', {'value': 1})
            trace.stop()
            manifest, events = trace.recorded_events(session['trace_id'])
            kwargs = dict(started_mono_ns=manifest['started_mono_ns'], window_start_seconds=0, window_end_seconds=59)
            all_plan = compile_recorded_plan(events, **kwargs)
            excluded = compile_recorded_plan(events, exclude_workloads=('news',), **kwargs)
            self.assertEqual(4, len(all_plan.operation_ids))
            self.assertEqual(3, len(excluded.operation_ids))
            self.assertEqual(1, len(excluded.excluded_operation_ids))
            for workload in all_plan.selected_workloads:
                self.assertEqual(1, len(compile_recorded_plan(events, include_workloads=(workload,), **kwargs).operation_ids))
            self.assertEqual(2, len(compile_recorded_plan(events, include_workloads=('top20', 'shadow'), **kwargs).operation_ids))
            with self.assertRaisesRegex(ValueError, 'sequence_gap'):
                compile_recorded_plan(events[1:], **kwargs)
            no_end = [row for row in events if not (row['event_type'] == 'operation_end' and row['workload_id'] == 'news')]
            for index, row in enumerate(no_end, 1):
                row = dict(row); row['seq'] = index; no_end[index - 1] = row
            with self.assertRaisesRegex(ValueError, 'censored'):
                compile_recorded_plan(no_end, **kwargs)

    def test_blob_dedup_charge_release_and_checksum_validation(self):
        with capture() as session:
            for index in range(3):
                self.assertTrue(trace.emit_payload(session['trace_id'], 'collector_input',
                    {'input_id': str(index), 'workload_id': 'realtime'}, {'same': ['immutable'] * 100}))
            trace.stop()
            manifest, events = trace.recorded_events(session['trace_id'])
            self.assertEqual(1, len(manifest['blobs']))
            self.assertEqual(0, manifest['charged_bytes'])
            self.assertEqual(3, len(events))
            digest = next(iter(manifest['blobs']))
            path = trace._directory() / session['trace_id'] / manifest['blobs'][digest]['name']
            path.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                trace.payload_bytes(session['trace_id'], digest)

    def test_stop_waits_for_inflight_copy_and_cannot_admit_its_late_payload(self):
        entered, release = threading.Event(), threading.Event()
        original = freeze_payload
        def held(value):
            entered.set(); release.wait(5)
            return original(value)
        with capture() as session:
            with patch('kiwoom_monitor.central_server.diagnostic_replay_contract.freeze_payload', side_effect=held):
                copier = threading.Thread(target=trace.emit_payload,
                    args=(session['trace_id'], 'collector_input', {'workload_id': 'realtime'}, {'value': 1}))
                copier.start()
                self.assertTrue(entered.wait(2))
                state = trace.stop(timeout=0.02)
                self.assertEqual('stopping', state['state'])
                self.assertGreater(state['copy_reserved_bytes'], 0)
                release.set(); copier.join(2)
                final = trace.stop()
            self.assertEqual('incomplete', final['state'])
            self.assertTrue(final['input_capture_censored'])
            self.assertEqual(0, final['copy_reserved_bytes'])
            self.assertEqual(0, final['written'])

    def test_busy_copy_rejects_replay_coverage_without_delaying_native_store(self):
        release = threading.Event()
        condition = threading.Condition()
        active = 0
        original = freeze_payload
        def held(value):
            nonlocal active
            with condition:
                active += 1
                condition.notify_all()
            release.wait(5)
            return original(value)
        store = NativeStore()
        with capture() as session:
            with patch('kiwoom_monitor.central_server.diagnostic_replay_contract.freeze_payload', side_effect=held):
                threads = [threading.Thread(target=store.load_daily_bars, args=('ok',)) for _ in range(2)]
                for thread in threads:
                    thread.start()
                try:
                    with condition:
                        self.assertTrue(condition.wait_for(lambda: active == 2, timeout=2))
                    self.assertEqual([], store.load_daily_bars('third'))
                    active_status = trace.status()
                    self.assertEqual(2 * trace._COPY_RESERVATION, active_status['copy_reserved_bytes'])
                    self.assertEqual(1, active_status['input_rejected_reasons']['capture_copy_busy'])
                finally:
                    release.set()
                    for thread in threads:
                        thread.join(2)
                trace.stop()
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                trace.recorded_events(session['trace_id'])
            manifest, events = rejected_evidence(session['trace_id'])
            self.assertEqual('incomplete', manifest['state'])
            self.assertEqual(0, manifest['copy_reserved_bytes'])
            with self.assertRaisesRegex(ValueError, 'selected_input_unsupported'):
                compile_recorded_plan(events, started_mono_ns=manifest['started_mono_ns'],
                    window_start_seconds=0, window_end_seconds=59)

    def test_collector_input_is_captured_while_two_store_copies_are_busy(self):
        release = threading.Event()
        condition = threading.Condition()
        active = 0
        original = freeze_payload

        def held(value):
            nonlocal active
            if 'code' in value:
                with condition:
                    active += 1
                    condition.notify_all()
                if not release.wait(5):
                    raise TimeoutError('store copies did not release')
            return original(value)

        store = NativeStore()
        hub_observer = HubInputRecorder()
        capture_hub = RealtimeHub(observer=hub_observer)
        collector = CentralRealtimeCollector(lambda: '', 'real', capture_hub,
                                             lambda: datetime(2026, 10, 8, tzinfo=timezone.utc), input_observer=CollectorInputRecorder(hub_observer=hub_observer))
        message = {'trnm': 'REAL', 'data': [
            {'type': '0B', 'item': '005930_AL', 'values': {'10': '100', '20': '092320'}}]}
        with capture() as session:
            with patch('kiwoom_monitor.central_server.diagnostic_replay_contract.freeze_payload', side_effect=held):
                threads = [threading.Thread(target=store.load_daily_bars, args=('busy',)) for _ in range(2)]
                for thread in threads:
                    thread.start()
                try:
                    with condition:
                        self.assertTrue(condition.wait_for(lambda: active == 2, timeout=2))
                    collector._input_observer.record_message(collector, message)
                    state = trace.status()
                    self.assertEqual(0, state['input_rejected'])
                    self.assertEqual(2, state['payload_accepted'])  # initial state and actual message
                    self.assertEqual({'store': 2, 'collector': 0}, state['copy_inflight'])
                    self.assertEqual(2 * trace._COPY_RESERVATION, state['copy_reserved_bytes'])
                    # API/status callers must not be able to mutate session accounting.
                    state['copy_inflight']['store'] = 99
                    self.assertEqual(2, trace.status()['copy_inflight']['store'])
                finally:
                    release.set()
                    for thread in threads:
                        thread.join(3)
                        self.assertFalse(thread.is_alive())
            final = trace.stop()
            self.assertEqual('complete', final['state'])
            self.assertEqual({'store': 0, 'collector': 0}, final['copy_inflight'])
            manifest, rows = trace.recorded_events(session['trace_id'])
            recorded = [thaw_payload(row['payload'])['message'] for row in rows
                        if row.get('event_type') == 'collector_input' and row.get('input_kind') == 'message']
            self.assertEqual([message], recorded)
            self.assertEqual(4, manifest['payload_accepted'])

    def test_both_copy_lanes_drain_on_stop_and_busy_detail_is_visible(self):
        release = threading.Event()
        condition = threading.Condition()
        active = 0
        original = freeze_payload

        def held(value):
            nonlocal active
            with condition:
                active += 1
                condition.notify_all()
            if not release.wait(5):
                raise TimeoutError('copies did not release')
            return original(value)

        with capture() as session:
            with patch('kiwoom_monitor.central_server.diagnostic_replay_contract.freeze_payload', side_effect=held):
                threads = [threading.Thread(target=trace.emit_payload,
                    args=(session['trace_id'], kind, {'workload_id': owner}, {'value': 1}))
                    for kind, owner in [('operation_start', 'shadow'), ('collector_input', 'realtime')]]
                for thread in threads:
                    thread.start()
                try:
                    with condition:
                        self.assertTrue(condition.wait_for(lambda: active == 2, timeout=2))
                    self.assertEqual({'store': 1, 'collector': 1}, trace.status()['copy_inflight'])
                    self.assertFalse(trace.emit_payload(session['trace_id'], 'collector_input',
                                                       {'workload_id': 'realtime'}, {'value': 2}))
                    self.assertEqual('stopping', trace.stop(timeout=.02)['state'])
                    self.assertEqual(2 * trace._COPY_RESERVATION, trace.status()['copy_reserved_bytes'])
                finally:
                    release.set()
                    for thread in threads:
                        thread.join(3)
                        self.assertFalse(thread.is_alive())
            final = trace.stop()
            self.assertEqual(0, final['copy_reserved_bytes'])
            self.assertEqual({'store': 0, 'collector': 0}, final['copy_inflight'])
            self.assertTrue(final['input_capture_censored'])
            _, rows = rejected_evidence(session['trace_id'])
            detail = next(row['rejection_detail'] for row in rows if row['event_type'] == 'input_rejected')
            self.assertEqual({'copy_lane': 'collector', 'lane_capacity': 1,
                'store_reserved_copies': 1, 'collector_reserved_copies': 1,
                'copy_reserved_bytes': 2 * trace._COPY_RESERVATION}, detail)
        # Censored copies must still release the collector slot for the next session.
        with capture() as session:
            self.assertTrue(trace.emit_payload(session['trace_id'], 'collector_input', {}, {'value': 3}))

    def test_unsupported_document_write_records_collection_and_keeps_native_result(self):
        class WritingStore(NativeStore):
            def upsert_documents(self, collection, values):
                self.calls.append((collection, values))
                return len(values)

        store = WritingStore()
        values = [{'owner': 'day', 'key': 'code', 'document': {'value': 1}}]
        with capture() as session:
            self.assertEqual(1, store.upsert_documents('not_allowlisted', values))
            trace.stop()
            _, rows = rejected_evidence(session['trace_id'])
            rejected = next(row for row in rows if row['event_type'] == 'input_rejected')
            self.assertEqual('not_allowlisted', rejected['collection'])
            self.assertNotIn('payload', rejected)
            self.assertIs(values, store.calls[0][1])

    def test_disk_failure_is_visible_without_changing_successful_native_write(self):
        store = NativeStore()
        original = trace.os.replace
        def fail_payload(source, target):
            if str(target).endswith('.payloads'):
                raise OSError('injected disk failure')
            return original(source, target)
        with capture() as session:
            with patch.object(trace.os, 'replace', side_effect=fail_payload):
                store.save_dataset_snapshot('ranking', 'peer', 'key', {'value': 1})
                final = trace.stop()
            self.assertEqual(1, len(store.calls))
            self.assertEqual('failed', final['state'])
            self.assertEqual('OSError', final['reason'])
            self.assertEqual({}, final['blobs'])
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                trace.recorded_events(session['trace_id'])

    def test_body_receipt_registry_preserves_old_string_binding_and_narrow_new_receipt(self):
        from kiwoom_monitor.central_server.diagnostic_replay_contract import OPERATIONS, _result_summary
        self.assertEqual("news", OPERATIONS["save_news_body_revision"])
        self.assertEqual("news", OPERATIONS["save_news_body_revision_receipt"])
        self.assertEqual({"result_binding": {"operation": "save_news_body_revision", "source_id": "old-body-id"}},
                         _result_summary("save_news_body_revision", "old-body-id"))
        receipt = {"body_revision_id": "new-body-id", "article_revision_id": "actual-article", "content_hash": "raw-hash",
                   "extractor_version": "source-v7", "fetched_at": 100.125, "available_at": 102.75, "status": "summary_only"}
        self.assertEqual({"result_type": "dict", "result_count": 7, "result_binding": {
            "operation": "save_news_body_revision_receipt", "source_id": "new-body-id"}},
            _result_summary("save_news_body_revision_receipt", receipt))
        self.assertEqual((100.125, 102.75), (receipt["fetched_at"], receipt["available_at"]))


class CollectorInputObserverBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_or_none_replay_validator_cannot_start_native_work(self):
        at = datetime.fromisoformat('2026-10-12T11:00:00+09:00')
        collector = CentralRealtimeCollector(lambda: self.fail('replay requested credentials'),
            'real', RealtimeHub(now_provider=lambda: at), lambda: at, object())
        tasks = set(asyncio.all_tasks())
        with self.assertRaises(TypeError):
            await collector.start_input_replay()
        self.assertFalse(collector._input_replay_only)
        self.assertIsNone(collector._snapshot_task)
        self.assertEqual(tasks, set(asyncio.all_tasks()))
        with self.assertRaisesRegex(TypeError, 'INPUT_REPLAY_MESSAGE_VALIDATOR_REQUIRED'):
            await collector.start_input_replay(message_validator=None)
        self.assertFalse(collector._input_replay_only)
        self.assertIsNone(collector._replay_message_validator)
        self.assertIsNone(collector._snapshot_task)
        self.assertIsNone(collector._task)
        self.assertEqual(tasks, set(asyncio.all_tasks()))

    async def test_recorder_owns_high_water_legacy_batch_and_native_flush_cause(self):
        from kiwoom_monitor.central_server.diagnostic_metrics import CURRENT_FLUSH_ID
        from kiwoom_monitor.central_server.diagnostic_replay_contract import operation_identity

        at = datetime.fromisoformat('2026-10-12T11:00:00+09:00')
        hub_observer = HubInputRecorder()
        hub = RealtimeHub(now_provider=lambda: at, observer=hub_observer)
        other_hub_observer = HubInputRecorder()
        other_hub = RealtimeHub(now_provider=lambda: at, observer=other_hub_observer)
        recorder = CollectorInputRecorder(hub_observer=hub_observer)
        other = CollectorInputRecorder(hub_observer=other_hub_observer)
        calls = []

        class Store:
            def save_realtime_snapshots(self, values):
                calls.append((values, operation_identity(), CURRENT_FLUSH_ID.get()))

        collector = CentralRealtimeCollector(lambda: self.fail('network credentials requested'),
            'real', hub, lambda: at, Store(), input_observer=recorder)
        component = f'realtime_collector:{id(collector):x}'
        with patch.object(trace, 'input_token', return_value='collector-owner-fixture'), \
                patch.object(trace, 'emit_payload', return_value=True) as emit, \
                patch.object(trace, 'reject_input'):
            accepted = recorder.record_input(collector, 'capture_gap', {'clear_continuous': False})
            self.assertEqual(('collector-owner-fixture', accepted), recorder.input_high_water)
            self.assertEqual((None, ''), other.input_high_water)
            self.assertIsNone(other.capture_epoch)
            emit.return_value = False
            self.assertIsNone(recorder.record_input(collector, 'capture_gap', {'clear_continuous': False}))
            self.assertEqual(('collector-owner-fixture', accepted), recorder.input_high_water)
            emit.return_value = True
            groups = {'1000': [{'item': ['005930'], 'type': ['0B']}]}
            with recorder.subscription_scope(collector, 'KRX', ('005930',), (), None, None) as returned:
                returned(groups)
            self.assertEqual(1, recorder.subscription_batch['group_count'])
            self.assertIsNone(other.subscription_batch)
            self.assertIs(hub._observer, recorder.hub_observer)
            self.assertEqual('collector-owner-fixture', hub._observer.lifecycle_epoch)
            self.assertIsNone(other_hub._observer.lifecycle_epoch)
            hub.connect()
            self.assertEqual(1, sum(call.args[2].get('input_kind') == 'hub_initial'
                                    for call in emit.call_args_list))
            collector._pending_snapshots[('trade', '005930')] = {
                'event_type': 'trade', 'item_key': '005930', 'received_at': at.timestamp(), 'event': {}}
            outer = asyncio.current_task()
            await collector._flush_snapshots()
            self.assertEqual(1, len(calls))
            identity = calls[0][1]
            self.assertEqual(component, identity['producer_component'])
            self.assertEqual(f'{component}:{id(outer):x}', identity['actor_id'])
            self.assertEqual(accepted, identity['cause_input_id'])
            self.assertTrue(calls[0][2])
            self.assertEqual('', operation_identity()['cause_input_id'])
            self.assertFalse(CURRENT_FLUSH_ID.get())
            self.assertEqual(set(), collector._approved_trade_sources)
            self.assertFalse(any(key.startswith('_recorded_') for key in vars(collector)))
            emit.return_value = False
            with recorder.subscription_scope(collector, 'KRX', ('005930',), (), None, None):
                self.assertIsNone(recorder.subscription_batch)
            self.assertIsNone(recorder.subscription_batch)

    async def test_native_collector_input_and_flush_run_with_all_diagnostic_source_absent(self):
        import hashlib
        import shutil
        import subprocess
        import sys
        import textwrap

        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / 'src' / 'kiwoom_monitor'
            shutil.copytree(root / 'src' / 'kiwoom_monitor', copied,
                ignore=shutil.ignore_patterns('__pycache__', 'diagnostic_*.py'))
            self.assertFalse(list(copied.rglob('diagnostic_*.py')))
            expected = hashlib.sha256((root / 'src/kiwoom_monitor/central_server/realtime_collector.py').read_bytes()).hexdigest()
            program = r'''
import asyncio, hashlib, json, os, runpy, sys, time
from pathlib import Path
from datetime import datetime
from threading import Event
from unittest.mock import patch
runpy.run_path(os.environ['KIWOOM_REGRESSION_REGISTRY_HELPER'])['join_from_environment']()
copied, expected = Path(sys.argv[1]), sys.argv[2]
sys.path.insert(0, str(copied.parent))
from kiwoom_monitor.central_server import realtime_collector as module
from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
actual = Path(module.__file__).resolve()
assert actual == (copied / 'central_server' / 'realtime_collector.py').resolve()
assert hashlib.sha256(actual.read_bytes()).hexdigest() == expected
assert not list(copied.rglob('diagnostic_*.py'))
at = datetime.fromisoformat('2026-10-12T11:00:00+09:00')

class Store:
    # Deliberate protocol fixture: real SQLite/PG construction is a separate gate.
    def __init__(self):
        self.fail = True
        self.hold = False
        self.entered, self.release = Event(), Event()
        self.snapshots, self.minutes = [], []
    def save_realtime_snapshots(self, values):
        if self.fail:
            self.fail = False
            raise LookupError('native retry fixture')
        if self.hold:
            self.entered.set()
            if not self.release.wait(3): raise TimeoutError('native release missing')
        self.snapshots.extend(values)
    def save_minute_bars(self, values, **kwargs): self.minutes.extend(values)
    def finalize_minute_bars(self, values): pass
    def save_dataset_snapshots(self, values): pass
    def upsert_documents(self, collection, values): pass

class Socket:
    def __init__(self): self.responses = asyncio.Queue()
    async def send(self, raw):
        frame = json.loads(raw)
        self.responses.put_nowait({'trnm': frame['trnm'], 'return_code': 0})
    async def recv(self): return json.dumps(await self.responses.get())
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False

async def until(predicate):
    deadline = time.monotonic() + 3
    while not predicate():
        if time.monotonic() >= deadline: raise AssertionError('native fixture did not progress')
        await asyncio.sleep(.002)

async def exercise():
    store, socket = Store(), Socket()
    hub = RealtimeHub(now_provider=lambda: at)
    sub = hub.connect()
    hub.update_subscription(sub, ['005930'], [], program_codes=[], priority_codes=[])
    async def snapshot_sleep(seconds): await asyncio.Event().wait()
    collector = module.CentralRealtimeCollector(lambda: 'fixture', 'real', hub, lambda: at, store,
                                                snapshot_sleep=snapshot_sleep)
    before = set(asyncio.all_tasks())
    with patch.object(module, 'connect', return_value=socket), \
            patch.object(module, 'REALTIME_REG_INTERVAL_SECONDS', 0):
        await collector.start()
        try:
            await until(lambda: hub.upstream_ready)
            assert collector._approved_trade_sources == {('005930', 'KRX')}
            collector.set_ram_observation_retention(20)
            message = {'trnm': 'REAL', 'data': [{'type': '0B', 'item': '005930',
                'values': {'10': '1000', '20': '110000', '15': '1', '13': '10', '14': '1'}}]}
            socket.responses.put_nowait(message)
            await until(lambda: bool(collector._pending_snapshots))
            snapshot = collector.ram_observation_snapshot(('005930',), seconds=20)
            assert snapshot['capture_anchor'] is None and len(snapshot['rows']) == 1
            assert snapshot['decision_time'] == at.isoformat()
            try: await collector._flush_snapshots()
            except LookupError: pass
            else: raise AssertionError('native writer error lost')
            assert ('trade', '005930') in collector._pending_snapshots
            await collector._flush_snapshots()
            assert len(store.snapshots) == 1 and not collector._pending_snapshots
            assert len(store.minutes) == 1
            generation = collector._ram_observation_generation
            collector._mark_capture_gap()
            assert collector._ram_observation_generation == generation + 1
            assert collector.ram_observation_snapshot(('005930',), seconds=20)['rows'] == ()
            message['data'][0]['values']['10'] = '1001'
            socket.responses.put_nowait(message)
            await until(lambda: bool(collector._pending_snapshots))
            store.hold = True
            flushing = asyncio.create_task(collector._flush_snapshots())
            assert await asyncio.to_thread(store.entered.wait, 3)
            flushing.cancel()
            try: await flushing
            except asyncio.CancelledError: pass
            else: raise AssertionError('caller cancellation lost')
            assert collector._flush_tasks and any(not task.done() for task in collector._flush_tasks)
            store.release.set()
        finally:
            store.release.set()
            await collector.close()
        assert len(store.snapshots) == 2
        assert not collector._flush_tasks and not collector._pending_snapshots
        assert collector._credential_phase == 'STOPPED'
        assert not any(key.startswith('_recorded_') for key in vars(collector))
        await collector.start()
        await until(lambda: hub.upstream_ready)
        await collector.close()
    await asyncio.sleep(0)
    assert set(asyncio.all_tasks()) == before
    assert not [name for name in sys.modules if name.startswith('kiwoom_monitor.') and '.diagnostic_' in name]
    return {'actual_origin': str(actual), 'diagnostic_sources_absent': True,
            'native_receive_retry_cancel_drain_restart': 'passed', 'store_scope': 'protocol_fixture'}
print(json.dumps(asyncio.run(exercise())))
'''
            result = await asyncio.to_thread(subprocess.run,
                [sys.executable, '-I', '-B', '-X', 'utf8', '-c', textwrap.dedent(program),
                 str(copied), expected], capture_output=True, text=True, encoding='utf-8', timeout=15)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            proof = json.loads(result.stdout.strip())
            self.assertTrue(proof['diagnostic_sources_absent'])
            self.assertEqual('protocol_fixture', proof['store_scope'])
            self.assertEqual('passed', proof['native_receive_retry_cancel_drain_restart'])
