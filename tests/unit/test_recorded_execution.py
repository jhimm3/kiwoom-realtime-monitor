from __future__ import annotations

import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from threading import Event, Lock
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.diagnostic_recorded_execution import _execute_recorded_operations
from kiwoom_monitor.central_server.diagnostic_replay_contract import (
    CODEC_VERSION, COLLECTOR_INPUT_VERSION, compile_recorded_plan, freeze_payload, operation_identity,
)


def events(specs):
    rows = []
    for identifier, actor, actor_sequence, offset, method, arguments in specs:
        fields = {
            'operation_id': identifier, 'method': method, 'codec_version': CODEC_VERSION,
            'workload_id': 'top20', 'producer_component': 'top20:fixture',
            'actor_id': actor, 'actor_known': True, 'actor_sequence': actor_sequence,
            'entered_mono_ns': 1_000_000_000 + int(offset * 1e9),
        }
        rows.append({**fields, 'seq': len(rows) + 1, 'event_type': 'operation_start',
                     'payload': freeze_payload(arguments).value})
        rows.append({**fields, 'seq': len(rows) + 1, 'event_type': 'operation_end',
                     'outcome': 'returned', 'finished_mono_ns': fields['entered_mono_ns'] + 1})
    return rows


def execute(store, rows, **options):
    return _execute_recorded_operations(
        store, rows, started_mono_ns=1_000_000_000,
        window_start_seconds=0, window_end_seconds=0.2, **options,
    )


class NativeStore:
    def __init__(self):
        self.calls = []
        self.active = 0
        self.peak = 0
        self.lock = Lock()
        self.release = Event()
        self.started = Event()
        self.finished = Event()

    def load_daily_bars(self, code, market='', limit=250):
        with self.lock:
            self.active += 1
            self.peak = max(self.active, self.peak)
            self.calls.append((code, operation_identity()))
        try:
            if code == 'held':
                self.started.set()
                if not self.release.wait(2):
                    raise AssertionError('test gate not released')
            elif code == 'fail':
                raise OSError('injected native failure')
            else:
                time.sleep(0.06)
            return [{'code': code}]
        finally:
            with self.lock:
                self.active -= 1
            self.finished.set()


def read(identifier, actor, sequence=1, offset=0, code=None):
    return (identifier, actor, sequence, offset, 'load_daily_bars', {'code': code or identifier})


class RecordedExecutionTests(unittest.TestCase):
    def test_mixed_collector_replaces_latest_and_own_market_history_keeps_peers_and_other_sinks(self):
        component = 'collector:source'
        rows = events([
            ('own-latest', 'flush', 1, .03, 'save_realtime_snapshots', {'values': [
                {'event_type': 'program_trade', 'item_key': 'old', 'received_at': 0, 'event': {}}]}),
            ('own-history', 'flush', 2, .04, 'save_dataset_snapshots', {'values': [
                ('market_state', 'kospi', 'old-history', {'historical': True}, None)]}),
            ('peer-history', 'peer', 1, .05, 'save_dataset_snapshots', {'values': [
                ('market_state', 'peer', 'peer-history', {'peer': True}, None)]}),
            ('own-other', 'flush', 3, .06, 'save_dataset_snapshots', {'values': [
                ('ranking', 'retained', 'rank', {'retained': True}, None)]}),
            ('own-reference', 'flush', 4, .07, 'upsert_documents', {'collection': 'stock_price_references',
                'values': [{'owner': '005930', 'key': 'latest', 'document': {'base_price': 100}}]}),
        ])
        for row in rows:
            row['workload_id'] = 'realtime'
            row['producer_component'] = 'peer' if row['operation_id'] == 'peer-history' else component
        origin = datetime(2026, 10, 7, 10, tzinfo=timezone(timedelta(hours=9)))
        for number, (kind, offset, value, omissions) in enumerate([
            ('initial_state', 0, {'source_time': origin, 'approved_sources': (('005930', 'SOR'),),
                                 'continuous_from': (), 'warm_accumulators': False}, {}),
            ('message', .01, {'source_time': origin + timedelta(seconds=.01), 'message': {'trnm': 'REAL', 'data': [
                {'type': '0B', 'item': '005930_AL', 'values': {'10': '70000', '13': '100', '14': '7', '15': '1', '20': '100000'}},
                {'type': '0w', 'item': '005930_NX', 'values': {'20': '100000', '210': '-5', '212': '7'}},
                {'type': '0J', 'item': '001', 'values': {'20': '100000', '10': '2500.1', '14': '99'}},
                {'type': '0U', 'item': '101', 'values': {'20': '100000', '10': '800.5', '14': '42'}},
            ]}}, {}),
            # Metadata-only frame cannot inject the excluded account/reference input.
            ('message', .02, {'source_time': origin + timedelta(seconds=.02),
                             'message': {'trnm': 'REAL', 'data': []}}, {'00': 1, 'other': 1}),
        ]):
            rows.append({'seq': len(rows) + 1, 'event_type': 'collector_input',
                'input_kind': kind, 'input_id': f'input-{number}', 'workload_id': 'realtime',
                'producer_component': component, 'mono_ns': 1_000_000_000 + int(offset * 1e9),
                'collector_input_version': COLLECTOR_INPUT_VERSION, 'excluded_types': omissions,
                'payload': freeze_payload(value).value})
        with tempfile.TemporaryDirectory() as root:
            store = SQLiteQueryStore(Path(root) / 'market.sqlite3')
            store.initialize()
            try:
                with patch('kiwoom_monitor.central_server.realtime_collector.connect',
                           side_effect=AssertionError('must not connect')):
                    result = asyncio.run(execute(store, rows, mode='collector_with_background',
                                                collector_components=(component,)))
                self.assertEqual('complete', result['state'])
                self.assertEqual(['own-latest', 'own-history'], result['replaced_operations'])
                self.assertEqual({'peer-history', 'own-other', 'own-reference'},
                                 {call['source_operation_id'] for call in result['calls']})
                report = result['collector_reports'][0]
                self.assertEqual({'0B': 1, '0w': 1, '0J': 1, '0U': 1}, report['event_type_counts'])
                self.assertEqual({'00': 1, 'other': 1}, report['excluded_type_counts'])
                self.assertEqual(0, report['pending_records_after_drain'])
                self.assertTrue(all(call['replay_operation_id'] for call in report['calls']))
                self.assertIn('market_state', {call['kind'] for call in report['calls']})
                self.assertEqual('input-2', next(call for call in report['calls']
                                               if call['kind'] == 'market_state')['source_input_id_high_water'])
                with patch('kiwoom_monitor.central_server.database_realtime_snapshot.time', return_value=origin.timestamp() + 1):
                    latest = store.load_realtime_snapshots(['005930', 'kospi', 'kosdaq'])
                self.assertEqual(4, len(latest))
                program = next(row['payload'] for row in latest if row['type'] == 'program_trade')
                self.assertEqual(('NXT', -5, 7), (program['market'], program['net_buy_quantity'], program['net_buy_amount_million_won']))
                history = store.load_dataset_snapshots('market_state', 'kospi')
                self.assertEqual(1, len(history))
                self.assertEqual(2500.1, history[0]['payload']['value']['index_value'])
                self.assertEqual({'peer': True}, store.load_dataset_snapshots('market_state', 'peer')[0]['payload'])
                self.assertEqual({'retained': True}, store.load_dataset_snapshots('ranking', 'retained')[0]['payload'])
                self.assertEqual(100, store.load_document('stock_price_references', '005930', 'latest')['document']['base_price'])
                self.assertFalse(result['cross_component_causal_replay'])
                self.assertFalse(result['source_state_equivalent'])
            finally:
                store.close()

    def test_mixed_dataset_legacy_market_and_unknown_input_versions_fail_before_native_access(self):
        component = 'collector'
        initial = {'source_time': datetime(2026, 10, 7, tzinfo=timezone.utc),
                   'approved_sources': (), 'continuous_from': ()}
        for version, values, error in [
            (COLLECTOR_INPUT_VERSION, [('market_state', 'k', 'k', {}, None), ('ranking', 'r', 'r', {}, None)], 'mixed_dataset'),
            (None, [('market_state', 'k', 'k', {}, None)], 'market_input_missing'),
            ('collector-input/v99', [('market_state', 'k', 'k', {}, None)], 'version_unsupported'),
        ]:
            with self.subTest(version=version, error=error):
                rows = events([('history', 'flush', 1, .01, 'save_dataset_snapshots', {'values': values})])
                for row in rows:
                    row.update(workload_id='realtime', producer_component=component)
                row = {'seq': 3, 'event_type': 'collector_input', 'input_kind': 'initial_state',
                       'input_id': 'initial', 'workload_id': 'realtime', 'producer_component': component,
                       'mono_ns': 1_000_000_000, 'payload': freeze_payload(initial).value}
                if version:
                    row['collector_input_version'] = version
                rows.append(row)
                store = NativeStore()
                with self.assertRaisesRegex(ValueError, error):
                    asyncio.run(execute(store, rows, mode='collector_with_background', collector_components=(component,)))
                self.assertEqual([], store.calls)

    def test_forbidden_or_legacy_new_market_message_blocks_valid_peer_before_invocation(self):
        origin = datetime(2026, 10, 7, tzinfo=timezone.utc)
        for version, item in [
            (None, {'type': '0w', 'item': '005930', 'values': {'210': '1'}}),
            (COLLECTOR_INPUT_VERSION, {'type': '00', 'values': {'10': '1'}}),
            (COLLECTOR_INPUT_VERSION, {'type': '0J', 'item': '001', 'values': {'10': '1', 'extra': 'bad'}}),
        ]:
            rows = events([read('peer', 'peer')])
            rows[0]['workload_id'] = rows[1]['workload_id'] = 'realtime'
            for kind, value in [('initial_state', {'source_time': origin, 'approved_sources': (), 'continuous_from': ()}),
                                ('message', {'source_time': origin, 'message': {'trnm': 'REAL', 'data': [item]}})]:
                row = {'seq': len(rows) + 1, 'event_type': 'collector_input', 'input_kind': kind,
                       'input_id': kind, 'workload_id': 'realtime', 'producer_component': 'collector',
                       'mono_ns': 1_000_000_000, 'payload': freeze_payload(value).value}
                if version:
                    row['collector_input_version'] = version
                rows.append(row)
            store = NativeStore()
            with self.assertRaisesRegex(ValueError, 'collector_message_invalid'):
                asyncio.run(execute(store, rows, mode='collector_with_background', collector_components=('collector',)))
            self.assertEqual([], store.calls)

    def test_cancellation_during_market_history_commit_drains_native_worker(self):
        class HeldMarketStore:
            def __init__(self):
                self.started, self.release, self.finished = Event(), Event(), Event()

            def save_realtime_snapshots(self, values):
                pass

            def save_dataset_snapshots(self, values):
                self.started.set()
                if not self.release.wait(3):
                    raise AssertionError('market history commit gate not released')
                self.finished.set()

        async def exercise():
            store = HeldMarketStore()
            origin = datetime(2026, 10, 7, tzinfo=timezone.utc)
            rows = []
            for kind, value, offset in (
                ('initial_state', {'source_time': origin, 'approved_sources': (), 'continuous_from': ()}, 0),
                ('message', {'source_time': origin, 'message': {'trnm': 'REAL', 'data': [
                    {'type': '0J', 'item': '001', 'values': {'10': '2500.1', '20': '090000'}}]}}, .01),
            ):
                rows.append({'seq': len(rows) + 1, 'event_type': 'collector_input', 'input_kind': kind,
                    'input_id': kind, 'workload_id': 'realtime', 'producer_component': 'collector',
                    'mono_ns': 1_000_000_000 + int(offset * 1e9),
                    'collector_input_version': COLLECTOR_INPUT_VERSION, 'payload': freeze_payload(value).value})
            task = asyncio.create_task(execute(store, rows, mode='collector_with_background',
                                                collector_components=('collector',)))
            try:
                while not store.started.is_set() and not task.done():
                    await asyncio.sleep(.001)
                self.assertTrue(store.started.is_set())
                task.cancel()
                await asyncio.sleep(.02)
                self.assertFalse(task.done())
                self.assertFalse(store.finished.is_set())
                store.release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertTrue(store.finished.is_set())
            finally:
                store.release.set()
                await asyncio.gather(task, return_exceptions=True)
        asyncio.run(exercise())

    def test_recorded_collector_inputs_replace_own_sinks_but_keep_peer(self):
        async def exercise(store):
            component = 'collector:source'
            rows = events([
                ('own', 'collector-flush', 1, .04, 'save_realtime_snapshots', {'values': []}),
                ('peer', 'peer-flush', 1, .05, 'save_realtime_snapshots', {'values': []}),
            ])
            for row in rows:
                row['workload_id'] = 'realtime'
                row['producer_component'] = component if row['operation_id'] == 'own' else 'peer'
            origin = datetime(2026, 10, 6, 9, 59, 58, tzinfo=timezone(timedelta(hours=9)))
            payloads = [
                ('initial_state', 0, {'source_time': origin,
                    'approved_sources': (('005930', 'KRX'),),
                    'continuous_from': (('005930', 'KRX', origin),),
                    'warm_accumulators': False}),
                ('message', .01, {'source_time': origin + timedelta(seconds=.01),
                    'message': {'trnm': 'REAL', 'data': [{'type': '0B', 'item': '005930',
                        'values': {'10': '70000', '13': '100', '14': '7', '15': '1', '20': '095958'}}]}}),
                ('capture_gap', .02, {'source_time': origin + timedelta(seconds=.02),
                                     'clear_continuous': True}),
                ('source_approval', .03, {'source_time': origin + timedelta(seconds=.03),
                    'subscribed_at': origin + timedelta(seconds=.03),
                    'sources': (('005930', 'KRX'),), 'reset_all': True}),
            ]
            for number, (kind, offset, value) in enumerate(payloads):
                rows.append({'seq': len(rows) + 1, 'event_type': 'collector_input',
                    'input_kind': kind, 'input_id': f'input-{number}', 'workload_id': 'realtime',
                    'producer_component': component, 'mono_ns': 1_000_000_000 + int(offset * 1e9),
                    'payload': freeze_payload(value).value})
            return await execute(store, rows, mode='collector_with_background',
                                 collector_components=(component,))
        with tempfile.TemporaryDirectory() as root:
            store = SQLiteQueryStore(Path(root) / 'collector.sqlite3')
            store.initialize()
            try:
                with patch('kiwoom_monitor.central_server.realtime_collector.connect',
                           side_effect=AssertionError('must not connect to Kiwoom')):
                    result = asyncio.run(exercise(store))
                self.assertEqual('complete', result['state'])
                self.assertEqual(['own'], result['replaced_operations'])
                self.assertEqual(['peer'], [call['source_operation_id'] for call in result['calls']])
                collector = result['collector_reports'][0]
                self.assertEqual(4, collector['input_count'])
                self.assertEqual(0, collector['pending_records_after_drain'])
                self.assertTrue(collector['calls'])
                self.assertTrue(all(call['phase'] == 'drain' for call in collector['calls']))
                self.assertTrue(all(call['replay_operation_id'] for call in collector['calls']))
                self.assertEqual('005930', store.load_minute_bars('005930', '2026-10-06')[0]['code'])
            finally:
                store.close()

    def test_actor_is_serial_peer_overlaps_and_ids_reach_native_spans(self):
        store = NativeStore()
        result = asyncio.run(execute(store, events([
            read('a1', 'a'), read('a2', 'a', 2, .01), read('b1', 'b', 1, .01),
        ])))
        self.assertEqual('complete', result['state'])
        self.assertEqual(2, store.peak)
        self.assertLess([code for code, _ in store.calls].index('a1'),
                        [code for code, _ in store.calls].index('a2'))
        by_id = {call['source_operation_id']: call for call in result['calls']}
        self.assertGreater(by_id['a2']['actor_wait_ms'], 30)
        self.assertGreaterEqual(by_id['a2']['started_seconds'], by_id['a1']['native_finished_seconds'])
        self.assertLess(by_id['b1']['started_seconds'], by_id['a1']['native_finished_seconds'])
        for code, identity in store.calls:
            self.assertEqual(by_id[code]['replay_operation_id'], identity['input_operation_id'])
            self.assertEqual('top20', identity['workload_id'])
        self.assertTrue(all(call['result_digest'] for call in result['calls']))
        self.assertFalse(result['baseline_managed'])
        self.assertFalse(result['public_execution_ready'])

    def test_all_adapters_and_signatures_checked_before_any_write(self):
        for bad in (
            ('bad', 'b', 1, .01, 'claim_news_jobs', {'limit': 1}),
            ('bad', 'b', 1, .01, 'load_daily_bars', {'code': 'bad', 'unknown': 1}),
        ):
            store = NativeStore()
            with self.assertRaisesRegex(ValueError, 'adapter_missing|signature_mismatch'):
                asyncio.run(execute(store, events([read('first', 'a'), bad])))
            self.assertEqual([], store.calls)

    def test_actor_sequence_reversal_rejected_before_invocation(self):
        store = NativeStore()
        with self.assertRaisesRegex(ValueError, 'actor_sequence_invalid'):
            asyncio.run(execute(store, events([read('a1', 'a', 2), read('a2', 'a', 1, .01)])))
        self.assertEqual([], store.calls)

    def test_concurrency_cap_wait_is_measured_separately(self):
        store = NativeStore()
        result = asyncio.run(execute(store, events([read('a', 'a'), read('b', 'b')]), concurrency=1))
        self.assertEqual(1, store.peak)
        self.assertGreater(result['calls'][1]['concurrency_wait_ms'], 30)

    def test_failure_does_not_execute_queued_same_actor(self):
        store = NativeStore()
        result = asyncio.run(execute(store, events([
            read('a1', 'a', code='fail'), read('a2', 'a', 2, .01),
        ])))
        self.assertEqual('incomplete', result['state'])
        self.assertEqual(['failed', 'not_started'], [call['state'] for call in result['calls']])
        self.assertEqual('OSError', result['calls'][0]['exception_type'])
        self.assertFalse(result['input_timing_preserved'])
        self.assertFalse(result['source_outcomes_match'])
        self.assertEqual(1, len(store.calls))

    def test_stop_and_cancel_drain_native_worker_and_skip_queued_call(self):
        async def exercise(cancel):
            store = NativeStore()
            stop = Event()
            task = asyncio.create_task(execute(store, events([
                read('a1', 'a', code='held'), read('a2', 'a', 2, .01),
            ]), stop=stop))
            try:
                async with asyncio.timeout(2):
                    while not store.started.is_set() and not task.done():
                        await asyncio.sleep(.001)
                self.assertTrue(store.started.is_set())
                if cancel:
                    task.cancel()
                else:
                    stop.set()
                await asyncio.sleep(.03)
                self.assertFalse(task.done())
                self.assertFalse(store.finished.is_set())
                store.release.set()
                if cancel:
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                else:
                    result = await task
                    self.assertEqual('incomplete', result['state'])
                self.assertTrue(store.finished.is_set())
                self.assertEqual(1, len(store.calls))
            finally:
                store.release.set()
                await asyncio.gather(task, return_exceptions=True)
        for cancel in (False, True):
            asyncio.run(exercise(cancel))

    def test_selected_native_store_writes_then_reads_same_natural_key(self):
        with tempfile.TemporaryDirectory() as root:
            store = SQLiteQueryStore(Path(root) / 'test.sqlite3')
            store.initialize()
            try:
                rows = events([
                    ('save', 'a', 1, 0, 'save_dataset_snapshot', {
                        'kind': 'ranking', 'subject': 'fixture', 'snapshot_key': '2026-10-05',
                        'payload': {'price': 12345},
                    }),
                    ('read', 'a', 2, .01, 'load_dataset_snapshots', {
                        'kind': 'ranking', 'subject': 'fixture',
                    }),
                ])
                result = asyncio.run(execute(store, rows))
                self.assertEqual('complete', result['state'])
                self.assertEqual({'price': 12345}, store.load_dataset_snapshots('ranking', 'fixture')[0]['payload'])
            finally:
                store.close()

    def test_source_call_ids_are_associations_not_replay_calls(self):
        store = NativeStore()
        rows = events([read('a', 'a')])
        rows.append({'seq': 3, 'event_type': 'call_start', 'call_id': 'old-db-call',
                     'input_operation_id': 'a'})
        result = asyncio.run(execute(store, rows))
        self.assertEqual(['old-db-call'], result['calls'][0]['source_call_ids'])
        self.assertNotEqual('old-db-call', result['calls'][0]['replay_operation_id'])

    def test_cancel_during_collector_shutdown_waits_for_native_flush(self):
        class HeldCollectorStore:
            def __init__(self):
                self.started, self.release, self.finished = Event(), Event(), Event()

            def save_realtime_snapshots(self, values):
                self.started.set()
                if not self.release.wait(2):
                    raise AssertionError('collector write gate not released')
                self.finished.set()

            def save_minute_bars(self, values, *, observations=None):
                pass

            def finalize_minute_bars(self, values):
                pass

            def save_second_trade_bars(self, values):
                pass

        async def exercise():
            store = HeldCollectorStore()
            origin = datetime(2026, 10, 6, 9, 59, 58, tzinfo=timezone(timedelta(hours=9)))
            rows = []
            for kind, value, offset in (
                ('initial_state', {'source_time': origin, 'approved_sources': (('005930', 'KRX'),),
                                   'continuous_from': ()}, 0),
                ('message', {'source_time': origin, 'message': {'trnm': 'REAL', 'data': [
                    {'type': '0B', 'item': '005930', 'values': {'10': '70000', '13': '100',
                     '14': '7', '15': '1', '20': '095958'}}]}}, .01),
            ):
                rows.append({'seq': len(rows) + 1, 'event_type': 'collector_input',
                    'input_kind': kind, 'input_id': kind, 'workload_id': 'realtime',
                    'producer_component': 'collector', 'mono_ns': 1_000_000_000 + int(offset * 1e9),
                    'payload': freeze_payload(value).value})
            task = asyncio.create_task(execute(store, rows, mode='collector_with_background',
                                                collector_components=('collector',)))
            try:
                async with asyncio.timeout(2):
                    while not store.started.is_set() and not task.done():
                        await asyncio.sleep(.001)
                self.assertTrue(store.started.is_set())
                task.cancel()
                await asyncio.sleep(.02)
                self.assertFalse(task.done())
                self.assertFalse(store.finished.is_set())
                store.release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertTrue(store.finished.is_set())
            finally:
                store.release.set()
                await asyncio.gather(task, return_exceptions=True)
        asyncio.run(exercise())

    def test_invalid_collector_payload_prevents_valid_peer_db_invocation(self):
        store = NativeStore()
        rows = events([read('peer', 'peer')])
        rows[0]['workload_id'] = rows[1]['workload_id'] = 'realtime'
        rows.append({'seq': 3, 'event_type': 'collector_input', 'input_kind': 'initial_state',
            'input_id': 'initial', 'workload_id': 'realtime', 'producer_component': 'collector',
            'mono_ns': 1_000_000_000, 'payload': freeze_payload({
                'source_time': datetime(2026, 10, 6), 'approved_sources': (),
                'continuous_from': (),
            }).value})
        with self.assertRaisesRegex(ValueError, 'collector_clock_missing'):
            asyncio.run(execute(store, rows, mode='collector_with_background',
                                collector_components=('collector',)))
        self.assertEqual([], store.calls)


if __name__ == '__main__':
    unittest.main()
