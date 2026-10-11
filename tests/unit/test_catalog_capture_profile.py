"""Exact catalog arguments, bounded copy admission and symmetric replay preflight."""
import json
import threading
import time
import unittest
from unittest.mock import patch

from kiwoom_monitor.central_server import diagnostic_trace as trace
from kiwoom_monitor.central_server.diagnostic_replay_contract import (
    CATALOG_COPY_PROFILE, CATALOG_MAX_COPY_BYTES, CODEC_VERSION, MAX_COPY_BYTES,
    MAX_NODES, InputRejected, capture_owner, compile_recorded_plan, freeze_payload,
    install_store_capture, payload_copy_limit, thaw_operation_arguments, thaw_payload,
)
from tests.unit.test_diagnostic_trace_deferred import deferred_capture, wait_state


def catalog_arguments(count=5000):
    documents = [{'owner': 'krx', 'key': f'{index:06d}', 'document': {
        'code': f'{index:06d}', 'name': '대표 종목 이름 ' + str(index),
        'market': 'KOSPI', 'as_of': '2026-10-08'}} for index in range(count)]
    documents.append({'owner': 'krx', 'key': '_meta',
                      'document': {'as_of': '2026-10-08', 'rows': count}})
    return {'collection': 'stock_catalog', 'values': documents}


def envelope():
    return {'method': 'replace_documents', 'codec_version': CODEC_VERSION,
            'payload_profile': CATALOG_COPY_PROFILE, 'collection': 'stock_catalog',
            'operation_id': 'catalog', 'workload_id': 'rest_market',
            'actor_id': 'catalog-worker', 'actor_known': True, 'actor_sequence': 1,
            'producer_component': 'top20', 'entered_mono_ns': 1_000_000_000}


class CatalogStore:
    def __init__(self):
        self.calls = []
        install_store_capture(self)

    def replace_documents(self, collection, values):
        self.calls.append((collection, values))
        return len(values)


class CatalogCaptureProfileTests(unittest.TestCase):
    def test_large_exact_arguments_require_named_profile_and_validate_pair(self):
        arguments = catalog_arguments()
        with self.assertRaises(InputRejected):
            freeze_payload(arguments)
        frozen = freeze_payload(arguments, maximum_bytes=CATALOG_MAX_COPY_BYTES)
        self.assertGreater(frozen.charge, MAX_COPY_BYTES)
        start = {**envelope(), 'event_type': 'operation_start', 'seq': 1,
                 'payload': frozen.value}
        end = {**envelope(), 'event_type': 'operation_end', 'seq': 2,
               'outcome': 'returned', 'finished_mono_ns': 1_000_000_001}
        self.assertEqual(arguments, thaw_operation_arguments(start))
        plan = compile_recorded_plan([start, end], started_mono_ns=1_000_000_000,
                                    window_start_seconds=0, window_end_seconds=1)
        self.assertEqual(('catalog',), plan.operation_ids)
        for field in ('payload_profile', 'collection'):
            altered = {**end, field: 'invalid'}
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'pair_invalid'):
                compile_recorded_plan([start, altered], started_mono_ns=1_000_000_000,
                                      window_start_seconds=0, window_end_seconds=1)
        legacy = {key: value for key, value in start.items() if key != 'payload_profile'}
        with self.assertRaises(InputRejected):
            thaw_operation_arguments(legacy)
        legacy['payload'] = freeze_payload(catalog_arguments(1)).value
        legacy_end = {key: value for key, value in end.items() if key != 'payload_profile'}
        with self.assertRaisesRegex(ValueError, 'pair_invalid'):
            compile_recorded_plan([legacy, {**legacy_end, 'payload_profile': None}],
                                  started_mono_ns=1_000_000_000,
                                  window_start_seconds=0, window_end_seconds=1)

    def test_profile_cannot_broaden_other_inputs_or_override_decoded_collection(self):
        fields = envelope()
        for mutation in ({'method': 'upsert_documents'}, {'collection': 'top20_daily_entrants'},
                         {'payload_profile': 'unknown'}, {'payload_profile': None}):
            with self.subTest(mutation=mutation), self.assertRaises(InputRejected):
                payload_copy_limit('operation_start', {**fields, **mutation}, catalog_arguments(1))
        for event in ('collector_input', 'rest_input', 'operation_end'):
            with self.subTest(event=event), self.assertRaises(InputRejected):
                payload_copy_limit(event, fields, catalog_arguments(1))
        for value in (None, [], {'collection': 'top20_daily_entrants'}):
            with self.subTest(value=value), self.assertRaises(InputRejected):
                payload_copy_limit('operation_start', fields, value)
        wrong = {'collection': 'top20_daily_entrants', 'values': []}
        with self.assertRaisesRegex(InputRejected, 'collection_mismatch'):
            thaw_operation_arguments({**fields, 'event_type': 'operation_start',
                                      'payload': freeze_payload(wrong).value})
        self.assertEqual(MAX_COPY_BYTES, payload_copy_limit('collector_input', {}))

    def test_secret_node_and_byte_bounds_remain_enforced(self):
        for value in ({'access_token': 'secret'}, [None] * MAX_NODES,
                      'x' * (CATALOG_MAX_COPY_BYTES // 4)):
            with self.subTest(kind=type(value).__name__), self.assertRaises(InputRejected):
                freeze_payload(value, maximum_bytes=CATALOG_MAX_COPY_BYTES)
        small = freeze_payload({'ok': (1, 2)}).value
        self.assertEqual({'ok': (1, 2)}, thaw_payload(small))
        with self.assertRaises(InputRejected):
            thaw_payload(small, maximum_bytes=CATALOG_MAX_COPY_BYTES + 1)

    def test_native_call_once_off_and_on_then_durable_exact_restore(self):
        arguments = catalog_arguments()
        store = CatalogStore()
        self.assertEqual(5001, store.replace_documents(**arguments))
        with deferred_capture() as (identifier, _):
            with capture_owner('top20', 'rest_market', 'catalog-worker', cause_input_id='catalog-source'):
                self.assertEqual(5001, store.replace_documents(**arguments))
            state = trace.status()
            self.assertEqual(0, state['input_rejected'])
            self.assertEqual(0, state['copy_reserved_bytes'])
            self.assertEqual(0, state['written'])
            trace.stop(timeout=.01)
            wait_state('awaiting_persistence')
            with patch.object(trace.time, 'sleep'):
                with trace._LOCK:
                    trace._SESSION['persist_at'] = time.time() - 1
                trace._WAKE.set()
                final = wait_state('complete', 15)
            rows = []
            for chunk in final['chunks']:
                for line in trace.chunk_bytes(identifier, chunk['name']).splitlines():
                    row = json.loads(line)
                    if 'payload_ref' in row:
                        row['payload'] = json.loads(trace.payload_bytes(identifier, row['payload_ref']))
                    rows.append(row)
            start, end = rows
            self.assertEqual(CATALOG_COPY_PROFILE, start['payload_profile'])
            self.assertEqual(start['payload_profile'], end['payload_profile'])
            self.assertEqual('catalog-source', start['cause_input_id'])
            restored = thaw_operation_arguments(start)
            self.assertEqual(arguments, restored)
            restored['values'][0]['document']['name'] = 'independent'
            self.assertNotEqual(arguments, restored)
            self.assertEqual((2, 2, 0), (final['accepted'], final['written'], final['charged_bytes']))
        self.assertEqual(2, len(store.calls))
        self.assertIs(arguments['values'], store.calls[0][1])
        self.assertIs(arguments['values'], store.calls[1][1])

    def test_admission_uses_16_mib_before_copy_and_native_result_survives_rejection(self):
        store = CatalogStore()
        arguments = catalog_arguments(1)
        with deferred_capture() as (identifier, _):
            with trace._LOCK:
                trace._SESSION['memory_limit_bytes'] = trace._WORKER_RESERVE + trace._SCALAR_RESERVE + 12 * 1024**2
            with patch('kiwoom_monitor.central_server.diagnostic_replay_contract.freeze_payload') as copier:
                self.assertEqual(2, store.replace_documents(**arguments))
                copier.assert_not_called()
            self.assertEqual(1, trace.status()['input_rejected_reasons']['capture_memory_full'])
            self.assertEqual(0, trace.status()['copy_reserved_bytes'])
        self.assertEqual(1, len(store.calls))

    def test_secret_catalog_rejection_keeps_native_call_and_releases_reservation(self):
        store = CatalogStore()
        arguments = catalog_arguments(1)
        arguments['values'][0]['document']['access_token'] = 'not-for-capture'
        with deferred_capture():
            self.assertEqual(2, store.replace_documents(**arguments))
            self.assertEqual(1, trace.status()['input_rejected'])
            self.assertEqual(0, trace.status()['copy_reserved_bytes'])
            self.assertEqual(0, trace.status()['payload_accepted'])
        self.assertEqual(1, len(store.calls))

    def _held_copies(self, *, fail=False, stop=False, both_catalog=False):
        release = threading.Event()
        condition = threading.Condition()
        entered = 0
        results = []
        original = freeze_payload

        def held(value, **kwargs):
            nonlocal entered
            with condition:
                entered += 1
                condition.notify_all()
            if not release.wait(5):
                raise TimeoutError('test copy did not release')
            if fail:
                raise OSError('injected copy failure')
            return original(value, **kwargs)

        with deferred_capture() as (identifier, _):
            def run(fields, value):
                results.append(trace.emit_payload(identifier, 'operation_start', fields, value))
            with patch('kiwoom_monitor.central_server.diagnostic_replay_contract.freeze_payload', side_effect=held):
                threads = [threading.Thread(target=run, args=(envelope(), catalog_arguments(1))),
                           threading.Thread(target=run, args=(envelope() if both_catalog else {},
                                                             catalog_arguments(1) if both_catalog else {'ok': 1}))]
                for thread in threads:
                    thread.start()
                try:
                    with condition:
                        self.assertTrue(condition.wait_for(lambda: entered == 2, timeout=2))
                    expected = CATALOG_MAX_COPY_BYTES + (CATALOG_MAX_COPY_BYTES if both_catalog else MAX_COPY_BYTES)
                    self.assertEqual(expected, trace.status()['copy_reserved_bytes'])
                    self.assertFalse(trace.emit_payload(identifier, 'operation_start', {}, {'ok': 2}))
                    self.assertEqual(1, trace.status()['input_rejected_reasons']['capture_copy_busy'])
                    if stop:
                        self.assertEqual('stopping', trace.stop(timeout=.01)['state'])
                finally:
                    release.set()
                    for thread in threads:
                        thread.join(3)
                        self.assertFalse(thread.is_alive())
            self.assertEqual(0, trace.status()['copy_reserved_bytes'])
            self.assertEqual([not (fail or stop)] * 2, results)
            if stop:
                wait_state('awaiting_persistence')
                self.assertEqual(0, trace.status()['payload_accepted'])
                self.assertTrue(trace.status()['input_capture_censored'])

    def test_mixed_copy_reservations_and_busy_are_bounded(self):
        self._held_copies()

    def test_two_catalog_reservations_never_exceed_32_mib(self):
        self._held_copies(both_catalog=True)

    def test_failed_copy_releases_selected_reservation(self):
        self._held_copies(fail=True)

    def test_stop_during_copy_releases_without_late_admission(self):
        self._held_copies(stop=True)
