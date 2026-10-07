"""Shared retention and durable wire equivalence, using the actual recorder."""
import json
import asyncio
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from kiwoom_monitor.central_server import diagnostic_trace as trace
from kiwoom_monitor.central_server.diagnostic_delivery_record import (
    DeliveryIdentity, DeliveryStage, MISSING, additional_charge,
)
from kiwoom_monitor.central_server.realtime_hub import CapturedMessageReceipt, RealtimeSubscriber
from tests.unit.test_diagnostic_trace_deferred import deferred_capture, wait_state


def identity(token, subscriber=None, source=None, ordinal=0):
    subscriber = subscriber or RealtimeSubscriber(capture_component='top20:test')
    source = source or CapturedMessageReceipt(token, 'collector:test', 'message', ('parent-a', 'parent-b'), True)
    fields = {'delivery_version': 'top20-hub-delivery/v1', 'trace_id': token, 'workload_id': 'top20',
        'producer_component': subscriber.capture_component, 'subscriber_id': f'subscriber:{id(subscriber):x}',
        'delivery_id': f'delivery-{ordinal}', 'delivery_sequence': ordinal + 1, 'event_kind': 'trade',
        'source_complete': True, 'source_component': source.producer_component,
        'message_id': source.message_id, 'parent_input_ids': source.input_ids, 'parser_ordinal': ordinal}
    result = trace.delivery_identity(token, fields, subscriber, source)
    return result, fields, subscriber, source


def persist():
    trace.stop(timeout=.01)
    wait_state('awaiting_persistence')
    with trace._LOCK:
        trace._SESSION['persist_at'] = time.time() - 1
    trace._WAKE.set()
    return wait_state('complete', 10)


class DeliveryRecordTests(unittest.TestCase):
    def test_native_fragmented_message_and_peer_keep_causal_coverage_after_persistence(self):
        from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
        from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
        from kiwoom_monitor.central_server.diagnostic_top20_input import consumed_delivery, compile_delivery_coverage
        from tests.unit.test_top20_delivery_provenance import message, NOW
        async def source():
            hub = RealtimeHub()
            first = hub.connect(capture_component='autonomous_top20:first')
            peer = hub.connect(capture_component='autonomous_top20:peer')
            collector = CentralRealtimeCollector(lambda: '', 'real', hub, lambda: NOW)
            collector._publish_parsed(message(101))
            nodes = []
            for subscriber in (first, peer):
                while not subscriber.queue.empty():
                    event = subscriber.queue.get_nowait()
                    nodes.append(event.delivery_receipt.node)
                    with consumed_delivery(subscriber, event):
                        pass
            return first, peer, nodes
        with deferred_capture() as (token, _):
            with trace._LOCK:
                trace._SESSION['payload_capture']['top20_inputs'] = True
                trace._SESSION['schema_version'] = 3
            first, peer, nodes = asyncio.run(source())
            with patch.object(trace.time, 'sleep'):
                status = persist()
            _, rows = trace.recorded_events(token)
            for subscriber in (first, peer):
                coverage = compile_delivery_coverage(rows, trace_id=token, component=subscriber.capture_component)
                self.assertEqual(101, len(coverage['delivery_ids']))
            self.assertTrue(all(node._refs == 0 for node in nodes))
            self.assertEqual(status['accepted'], status['written'])
            self.assertEqual(0, status['charged_bytes'])

    def test_shared_context_released_only_after_last_stage_and_last_delivery(self):
        with patch.object(trace, '_pack_deferred'), deferred_capture() as (token, _):
            first, _, sub, source = identity(token)
            second, _, _, _ = identity(token, sub, source, 1)
            self.assertIs(first.node.children[0], second.node.children[0])
            self.assertIs(first.node.children[1], second.node.children[1])
            for receipt in (first, second):
                for stage in ('enqueue', 'dequeue', 'consume_end'):
                    trace.emit_delivery(token, receipt, stage)
            with trace._LOCK:
                queued = list(trace._QUEUE)
                initial = trace._SESSION['charged_bytes']
                expected = sum(row.own_charge for row in queued) + first.node.charge + second.node.charge
                expected += sum(node.charge for node in first.node.children)
                self.assertEqual(expected, initial)
                self.assertEqual((3, 3), (first.node._refs, second.node._refs))
                self.assertEqual((2, 2), tuple(node._refs for node in first.node.children))
                for index in range(3):
                    trace._release_record(trace._SESSION, trace._QUEUE.popleft())
                self.assertEqual(0, first.node._refs)
                self.assertEqual((1, 1), tuple(node._refs for node in first.node.children))
                for index in range(3):
                    trace._release_record(trace._SESSION, trace._QUEUE.popleft())
                self.assertEqual((0, 0), tuple(node._refs for node in first.node.children))
                self.assertEqual((0, 0), (trace._SESSION['charged_bytes'], trace._SESSION['scalar_charged_bytes']))

    def test_admission_refusal_does_not_acquire_and_concurrent_stages_are_atomic(self):
        with deferred_capture() as (token, _):
            receipt, _, _, _ = identity(token)
            record = DeliveryStage(receipt, 'enqueue', MISSING, 123, 456)
            with trace._LOCK:
                trace._SESSION['memory_limit_bytes'] = trace._WORKER_RESERVE + additional_charge(record) - 1
            trace.emit_delivery(token, receipt, 'enqueue')
            self.assertEqual(0, receipt.node._refs)
            self.assertEqual((0, 0), tuple(node._refs for node in receipt.node.children))
            self.assertIsNone(trace.token())
        with patch.object(trace, '_pack_deferred'), deferred_capture() as (token, _):
            receipt, _, _, _ = identity(token)
            with patch.object(trace, '_CAPACITY', 30), ThreadPoolExecutor(4) as pool:
                list(pool.map(lambda index: trace.emit_delivery(token, receipt, 'dequeue'), range(100)))
            status = trace.status()
            self.assertEqual((30, 1), (status['accepted'], status['known_dropped']))
            self.assertIsNone(trace.token())
            self.assertEqual(30, receipt.node._refs)
            self.assertEqual((1, 1), tuple(node._refs for node in receipt.node.children))
            trace.stop('server_shutdown', timeout=10)
            self.assertEqual(0, receipt.node._refs)
            self.assertEqual(0, trace.status()['charged_bytes'])

    def test_worker_roundtrip_preserves_fields_null_absence_order_and_chunk_suffix(self):
        with deferred_capture() as (token, _):
            receipt, fields, _, _ = identity(token)
            self.assertIsInstance(receipt, DeliveryIdentity)
            stages = ('enqueue', 'dequeue', 'consume_end')
            for stage in stages:
                trace.emit_delivery(token, receipt, stage,
                    **({'outcome': 'failed'} if stage == 'consume_end' else {}))
            with patch.object(trace, '_MAX_CHUNK_BYTES', 1100), patch.object(trace.time, 'sleep'):
                status = persist()
            self.assertGreater(len(status['chunks']), 1)
            rows = [json.loads(line) for chunk in status['chunks']
                    for line in trace.chunk_bytes(token, chunk['name']).splitlines()]
            for index, row in enumerate(rows):
                self.assertEqual({**fields, 'stage': stages[index],
                                  **({'outcome': 'failed'} if index == 2 else {})},
                    {key: value for key, value in row.items() if key not in
                     {'seq', 'producer_id', 'wall_ns', 'mono_ns', 'event_type'} } |
                    {'parent_input_ids': tuple(row['parent_input_ids'])})
                self.assertEqual(index + 1, row['seq'])
                self.assertEqual('top20_delivery', row['event_type'])
                self.assertFalse(any(key.startswith('_') for key in row))
            self.assertEqual((0, 0), (status['charged_bytes'], receipt.node._refs))
            self.assertEqual(0, status['scalar_charged_bytes'])

    def test_explicit_null_and_absent_outcome_are_distinct_and_context_is_immutable(self):
        with deferred_capture() as (token, _):
            receipt, _, _, _ = identity(token)
            with self.assertRaises(AttributeError):
                receipt.node.values = ()
            trace.emit_delivery(token, receipt, 'enqueue')
            trace.emit_delivery(token, receipt, 'consume_end', outcome=None)
            with patch.object(trace.time, 'sleep'):
                status = persist()
            rows = [json.loads(line) for part in status['chunks']
                    for line in trace.chunk_bytes(token, part['name']).splitlines()]
            self.assertNotIn('outcome', rows[0])
            self.assertIn('outcome', rows[1])
            self.assertIsNone(rows[1]['outcome'])

    def test_failed_sync_keeps_retained_suffix_and_new_epoch_releases_it(self):
        old = None
        with deferred_capture() as (token, _):
            old, _, sub, source = identity(token)
            trace.emit_delivery(token, old, 'enqueue')
            trace.stop(timeout=.01)
            wait_state('awaiting_persistence')
            with patch.object(trace.os, 'fsync', side_effect=OSError('injected durable failure')):
                with trace._LOCK:
                    trace._SESSION['persist_at'] = time.time() - 1
                trace._WAKE.set()
                failed = wait_state('failed', 10)
                trace._THREAD.join(5)
            self.assertEqual(0, old.node._refs)  # The packed bytes, rather than native receipt, own the suffix.
            self.assertGreater(failed['packed_bytes'], 0)
            self.assertEqual((1, 0), (failed['queued'], failed['pending_events']))
        with deferred_capture() as (new_token, _):
            self.assertEqual(0, old.node._refs)
            new, _, _, _ = identity(new_token, sub, source)
            self.assertIsNot(old.node.children[0], new.node.children[0])
            self.assertIsNot(old.node.children[1], new.node.children[1])
            trace.emit_delivery(new_token, old, 'dequeue')
            self.assertEqual(0, trace.status()['accepted'])

    def test_input_rejected_cannot_finish_complete_even_with_no_scalar_drop(self):
        with deferred_capture() as (token, _):
            trace.reject_input(token, {'workload_id': 'top20', 'reason': 'input_too_large'})
            trace.stop(timeout=.01)
            wait_state('awaiting_persistence')
            with patch.object(trace.time, 'sleep'):
                with trace._LOCK:
                    trace._SESSION['persist_at'] = time.time() - 1
                trace._WAKE.set()
                status = wait_state('incomplete', 10)
            self.assertEqual((0, 1), (status['known_dropped'], status['input_rejected']))
