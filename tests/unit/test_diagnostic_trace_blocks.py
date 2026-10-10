from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import threading
import time
import unittest
from unittest.mock import patch

from kiwoom_monitor.central_server import diagnostic_trace as trace
from kiwoom_monitor.central_server.diagnostic_recorded_execution import _execute_recorded_operations
from kiwoom_monitor.central_server.diagnostic_replay_contract import (
    InputRejected, LARGE_COPY_PROFILE, LARGE_MAX_COPY_BYTES, capture_owner,
    freeze_payload, install_store_capture, operation_identity, thaw_operation_arguments,
)
from kiwoom_monitor.central_server.diagnostic_trace_payload import BlockPayloadReference
from kiwoom_monitor.central_server.diagnostic_trace_ram import (
    BlockRow, encode_payload_blocks, pack_records,
)
from tests.unit.test_diagnostic_trace_deferred import deferred_capture, wait_state


class NativeStore:
    def __init__(self, held=None, resume=None):
        self.calls, self.held, self.resume = [], held, resume
        install_store_capture(self)

    def save_shadow_monitor_state(self, monitor_id, document):
        token = trace.token()
        fields = operation_identity()
        fields['call_id'] = 'native:' + fields['input_operation_id']
        trace.emit(token, 'call_start', fields)
        self.calls.append((monitor_id, copy.deepcopy(document)))
        if self.held is not None:
            self.held.set()
            if not self.resume.wait(10):
                raise TimeoutError('native test gate')
        trace.emit(token, 'call_end', fields)


def persist_now(expected='complete'):
    trace.stop(timeout=.01)
    wait_state('awaiting_persistence')
    with trace._LOCK:
        trace._SESSION['persist_at'] = time.time() - 1
    trace._WAKE.set()
    final = wait_state(expected, 15)
    trace._THREAD.join(10)
    if trace._THREAD.is_alive():
        raise AssertionError('recorder worker did not retire')
    return final


class TraceBlockTests(unittest.TestCase):
    def test_large_copy_is_explicit_and_keeps_secret_and_type_guards(self):
        value = {'document': {'frames': ['가' * 3000 for _ in range(900)]}}
        with self.assertRaisesRegex(InputRejected, 'payload_budget_exceeded'):
            freeze_payload(value)
        frozen = freeze_payload(value, maximum_bytes=LARGE_MAX_COPY_BYTES)
        self.assertGreater(frozen.charge, 8 * 1024**2)
        row = {'event_type': 'operation_start', 'method': 'save_shadow_monitor_state',
               'codec_version': 'store-input/v1', 'payload_profile': LARGE_COPY_PROFILE,
               'payload': frozen.value}
        self.assertEqual(value, thaw_operation_arguments(row))
        with self.assertRaisesRegex(InputRejected, 'invalid_payload_profile'):
            thaw_operation_arguments({**row, 'method': 'save_query'})
        with self.assertRaisesRegex(InputRejected, 'sensitive_payload_field'):
            freeze_payload({'owner_token': 'not_recordable'}, maximum_bytes=LARGE_MAX_COPY_BYTES)

    def test_streamed_encoding_is_exact_for_utf8_escapes_and_small_frames(self):
        value = ('map', (('문자', '가\n🙂\\"' * 5000), ('null', None), ('tuple', ('tuple', (1, 2)))))
        blocks = encode_payload_blocks(value, block_bytes=2048)
        expected = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        self.assertGreater(len(blocks), 1)
        self.assertTrue(all(len(block) <= 2048 for block in blocks))
        self.assertEqual(expected, b''.join(blocks))
        with self.assertRaisesRegex(OSError, 'too_large'):
            encode_payload_blocks(value, maximum_bytes=16)
        segments = pack_records([
            {'seq': 1, 'event_type': 'before'},
            {'seq': 2, 'payload_profile': LARGE_COPY_PROFILE, 'payload': value},
            {'seq': 3, 'payload_profile': LARGE_COPY_PROFILE, 'event_type': 'operation_end'},
        ])
        self.assertEqual(3, sum(segment.count for segment in segments))
        row = segments[1].take()
        self.assertIsInstance(row, BlockRow)
        self.assertEqual(expected, b''.join(row.payload_blocks()))
        segments[1].rewind()
        row = segments[1].take()
        segments[1].commit(row)
        with self.assertRaisesRegex(RuntimeError, 'out_of_order'):
            segments[1].commit(row)

    def test_full_capture_blocks_restore_one_call_and_pin_file_integrity(self):
        document = {'frames': [{'symbol': '005930', 'value': '가' * 3000} for _ in range(900)]}
        original = copy.deepcopy(document)
        with deferred_capture(large_inputs=True) as (identifier, _):
            native = NativeStore()
            with capture_owner('shadow', 'fixture', 'actor'):
                native.save_shadow_monitor_state('monitor', document)
            document['frames'][0]['value'] = 'changed after native call'
            final = persist_now()
            self.assertEqual((4, 4, 0, 0, 0), (final['accepted'], final['written'], final['known_dropped'],
                                               final['input_rejected'], final['operation_inflight']))
            manifest, rows = trace.recorded_events(identifier)
            start = next(row for row in rows if row['event_type'] == 'operation_start')
            self.assertIsInstance(start['payload'], BlockPayloadReference)
            self.assertEqual(original, thaw_operation_arguments(start)['document'])
            descriptor = json.loads(start['payload'].descriptor)
            self.assertGreater(len(descriptor['parts']), 1)
            self.assertEqual(4, manifest['schema_version'])
            selected, _ = trace.recorded_window_events(
                identifier, window_start_seconds=0,
                window_end_seconds=(manifest['finished_mono_ns'] - manifest['started_mono_ns']) / 1e9,
                mode='recorded_operations')
            self.assertEqual(1, selected['window_read']['block_payloads_verified'])
            self.assertEqual(descriptor['bytes'], selected['window_read']['block_payload_bytes_verified'])
            self.assertEqual(0, selected['window_read']['payload_bytes_loaded'])
            restored = NativeStore()
            result = asyncio.run(_execute_recorded_operations(
                restored, rows, started_mono_ns=manifest['started_mono_ns'],
                window_start_seconds=0, window_end_seconds=1, include_workloads=(), exclude_workloads=()))
            self.assertEqual([('monitor', original)], restored.calls)
            self.assertEqual(1, len(result['calls']))
            first = descriptor['parts'][0]
            path = trace._directory() / identifier / first['name']
            with path.open('r+b') as file:
                file.seek(first['offset'])
                file.write(b'!')
            with self.assertRaisesRegex(ValueError, 'checksum_mismatch'):
                thaw_operation_arguments(start)
            before = len(restored.calls)
            with self.assertRaisesRegex(ValueError, 'checksum_mismatch'):
                asyncio.run(_execute_recorded_operations(
                    restored, rows, started_mono_ns=manifest['started_mono_ns'],
                    window_start_seconds=0, window_end_seconds=1, include_workloads=(), exclude_workloads=()))
            self.assertEqual(before, len(restored.calls))

    def test_stop_keeps_owned_native_end_and_copies_accepted_before_stop(self):
        held, resume, copied, copy_resume = (threading.Event() for _ in range(4))
        errors = []
        real_freeze = freeze_payload

        def held_copy(*args, **kwargs):
            frozen = real_freeze(*args, **kwargs)
            copied.set()
            if not copy_resume.wait(10):
                raise TimeoutError('copy test gate')
            return frozen

        with deferred_capture(large_inputs=True) as (identifier, _):
            native = NativeStore(held, resume)

            def run():
                try:
                    with capture_owner('shadow', 'fixture', 'actor'):
                        native.save_shadow_monitor_state('monitor', {'frames': [1, 2, 3]})
                except BaseException as error:
                    errors.append(error)

            with patch('kiwoom_monitor.central_server.diagnostic_replay_contract.freeze_payload', side_effect=held_copy):
                worker = threading.Thread(target=run)
                worker.start()
                try:
                    self.assertTrue(copied.wait(3))
                    trace.stop(timeout=.01)
                    wait_state('awaiting_persistence')
                    copy_resume.set()
                    self.assertTrue(held.wait(3))
                    self.assertEqual(1, trace.status()['operation_inflight'])
                    with trace._LOCK:
                        trace._SESSION['persist_at'] = time.time() - 1
                    trace._WAKE.set()
                    self.assertEqual('awaiting_persistence', trace.status()['state'])
                finally:
                    copy_resume.set()
                    resume.set()
                    worker.join(10)
            self.assertFalse(errors)
            final = wait_state('complete', 10)
            self.assertEqual((4, 4, 0, False), (final['accepted'], final['written'], final['operation_inflight'],
                                               final['input_capture_censored']))
            _, rows = trace.recorded_events(identifier)
            self.assertEqual(['operation_start', 'call_start', 'call_end', 'operation_end'],
                             [row['event_type'] for row in rows])

    def test_copy_rejected_after_stop_keeps_incomplete_receipt(self):
        copied, resume = threading.Event(), threading.Event()
        errors = []

        def rejected_copy(*args, **kwargs):
            copied.set()
            if not resume.wait(10):
                raise TimeoutError('copy test gate')
            raise InputRejected('payload_budget_exceeded')

        with deferred_capture(large_inputs=True) as (identifier, _):
            native = NativeStore()

            def run():
                try:
                    with capture_owner('shadow', 'fixture', 'actor'):
                        native.save_shadow_monitor_state('monitor', {'frames': [1]})
                except BaseException as error:
                    errors.append(error)

            with patch('kiwoom_monitor.central_server.diagnostic_replay_contract.freeze_payload',
                       side_effect=rejected_copy):
                worker = threading.Thread(target=run)
                worker.start()
                try:
                    self.assertTrue(copied.wait(3))
                    trace.stop(timeout=.01)
                    wait_state('awaiting_persistence')
                finally:
                    resume.set()
                    worker.join(10)
            self.assertFalse(errors)
            self.assertEqual(1, len(native.calls))
            with trace._LOCK:
                trace._SESSION['persist_at'] = time.time() - 1
            trace._WAKE.set()
            final = wait_state('incomplete', 10)
            self.assertEqual(1, final['input_rejected'])
            self.assertEqual({'payload_budget_exceeded': 1}, final['input_rejected_reasons'])
            with self.assertRaisesRegex(ValueError, 'incomplete_or_old_schema'):
                trace.recorded_events(identifier)

    def test_prepared_frontier_excludes_decode_cost_from_source_timeline(self):
        with deferred_capture(large_inputs=True) as (identifier, _):
            native = NativeStore()
            with capture_owner('shadow', 'fixture', 'actor'):
                native.save_shadow_monitor_state('monitor', {'frames': [1, 2]})
            persist_now()
            manifest, rows = trace.recorded_events(identifier)
            original = thaw_operation_arguments
            restored = NativeStore()

            def slow_decode(row):
                time.sleep(.2)
                return original(row)

            with patch('kiwoom_monitor.central_server.diagnostic_recorded_execution.thaw_operation_arguments',
                       side_effect=slow_decode):
                result = asyncio.run(_execute_recorded_operations(
                    restored, rows, started_mono_ns=manifest['started_mono_ns'],
                    window_start_seconds=0, window_end_seconds=.5))
            self.assertEqual(1, len(restored.calls))
            self.assertGreaterEqual(result['calls'][0]['payload_prepare_ms'], 200)
            self.assertLess(result['calls'][0]['start_lag_ms'], 100)
            self.assertTrue(result['input_timing_preserved'])

    def test_payload_credit_is_shared_by_runtime_peers(self):
        from kiwoom_monitor.central_server.diagnostic_replay_runtime import (
            ReplayRuntimeScope, owned_payload_credit,
        )

        async def check():
            runtime = ReplayRuntimeScope()
            with runtime.activate():
                first = owned_payload_credit()
                second = owned_payload_credit()
                self.assertIs(first, second)
                for _ in range(3):
                    await first.acquire()
                self.assertTrue(second.locked())
                first.release()
                await second.acquire()
                for _ in range(3):
                    first.release()

        asyncio.run(check())

    def test_cancelled_actor_keeps_input_credit_until_native_exit(self):
        from kiwoom_monitor.central_server.diagnostic_replay_runtime import (
            ReplayRuntimeScope, owned_payload_credit,
        )
        with deferred_capture(large_inputs=True) as (identifier, _):
            with capture_owner('shadow', 'fixture', 'actor'):
                NativeStore().save_shadow_monitor_state('monitor', {'frames': [1, 2]})
            persist_now()
            manifest, rows = trace.recorded_events(identifier)
            held, resume = threading.Event(), threading.Event()
            restored = NativeStore(held, resume)

            async def check():
                runtime = ReplayRuntimeScope()
                with runtime.activate():
                    credits = owned_payload_credit()
                    runner = asyncio.create_task(_execute_recorded_operations(
                        restored, rows, started_mono_ns=manifest['started_mono_ns'],
                        window_start_seconds=0, window_end_seconds=.2))
                    try:
                        async with asyncio.timeout(3):
                            while not held.is_set():
                                await asyncio.sleep(.001)
                        actors = [task for task in asyncio.all_tasks()
                                  if task.get_name() == 'recorded-replay-actor']
                        self.assertEqual(1, len(actors))
                        actors[0].cancel()
                        await asyncio.sleep(.02)
                        self.assertFalse(runner.done())
                        self.assertEqual(1, runtime.status()['pending_threads'])
                    finally:
                        resume.set()
                    with self.assertRaisesRegex(RuntimeError, 'scheduler_failed'):
                        await runner
                    async with asyncio.timeout(.5):
                        for _ in range(3):
                            await credits.acquire()
                    for _ in range(3):
                        credits.release()
                    runtime.begin_shutdown()
                    status = await runtime.drain(timeout=3)
                    self.assertEqual((0, 0), (status['pending_tasks'], status['pending_threads']))

            asyncio.run(check())
            self.assertEqual(1, len(restored.calls))

    def test_block_descriptor_rejects_missing_reordered_and_wrong_whole_hash(self):
        blocks = (b'first', b'second')
        digest = hashlib.sha256(b''.join(blocks)).hexdigest()
        with deferred_capture(large_inputs=True) as (identifier, _):
            directory = trace._directory() / identifier
            end = time.monotonic() + 3
            while not (directory / 'manifest.json').exists() and time.monotonic() < end:
                threading.Event().wait(.01)
            self.assertTrue((directory / 'manifest.json').exists())
            root = trace._write_payload_blocks(directory, digest, blocks, trace._SESSION)
            manifest = {'schema_version': 4, 'blobs': {digest: root}}
            self.assertEqual(blocks, trace._payload_blocks_from_manifest(identifier, digest, manifest))
            for bad in ({**root, 'parts': root['parts'][:-1]},
                        {**root, 'parts': list(reversed(root['parts']))},
                        {**root, 'bytes': root['bytes'] + 1}):
                with self.subTest(bad=bad), self.assertRaises((KeyError, ValueError)):
                    trace._payload_blocks_from_manifest(identifier, digest, {'schema_version': 4, 'blobs': {digest: bad}})

    def test_three_store_copies_and_collector_have_separate_byte_credit(self):
        copied, resume, lock = threading.Event(), threading.Event(), threading.Lock()
        count, failures = [], []
        real_freeze = freeze_payload

        def held_copy(value, **kwargs):
            frozen = real_freeze(value, **kwargs)
            if kwargs.get('maximum_bytes') == LARGE_MAX_COPY_BYTES:
                with lock:
                    count.append(1)
                    if len(count) == 3:
                        copied.set()
                if not resume.wait(10):
                    raise TimeoutError('three-copy test gate')
            return frozen

        with deferred_capture(large_inputs=True) as (identifier, _):
            stores = [NativeStore() for _ in range(3)]

            def run(index):
                try:
                    with capture_owner('shadow', 'fixture', 'actor:' + str(index)):
                        stores[index].save_shadow_monitor_state('m:' + str(index), {'frames': [index]})
                except BaseException as error:
                    failures.append(error)

            with patch('kiwoom_monitor.central_server.diagnostic_replay_contract.freeze_payload', side_effect=held_copy):
                workers = [threading.Thread(target=run, args=(index,)) for index in range(3)]
                for worker in workers:
                    worker.start()
                try:
                    self.assertTrue(copied.wait(3))
                    self.assertEqual(3 * LARGE_MAX_COPY_BYTES, trace.status()['copy_reserved_bytes'])
                    self.assertTrue(trace.emit_payload(identifier, 'collector_input',
                                                      {'workload_id': 'realtime'}, {'price': 123}))
                finally:
                    resume.set()
                    for worker in workers:
                        worker.join(10)
            self.assertFalse(failures)
            final = persist_now()
            self.assertEqual((13, 13, 0, 0), (final['accepted'], final['written'], final['input_rejected'],
                                              final['copy_reserved_bytes']))
            self.assertEqual([1, 1, 1], [len(store.calls) for store in stores])

    def test_persistence_failure_never_changes_completed_native_call(self):
        with deferred_capture(large_inputs=True) as (identifier, _):
            store = NativeStore()
            with capture_owner('shadow', 'fixture', 'actor'):
                store.save_shadow_monitor_state('m', {'frames': [1, 2]})
            trace.stop(timeout=.01)
            wait_state('awaiting_persistence')
            with patch.object(trace, '_write_payload_blocks', side_effect=OSError('injected bundle failure')):
                with trace._LOCK:
                    trace._SESSION['persist_at'] = time.time() - 1
                trace._WAKE.set()
                failed = wait_state('failed', 5)
            self.assertEqual(1, len(store.calls))
            self.assertEqual(0, failed['written'])
            with self.assertRaisesRegex(ValueError, 'incomplete_or_old_schema'):
                trace.recorded_events(identifier)


if __name__ == '__main__':
    unittest.main()
