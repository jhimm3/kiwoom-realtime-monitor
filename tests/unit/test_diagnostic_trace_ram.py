from __future__ import annotations

import json
import threading
import unittest
from unittest.mock import patch

from kiwoom_monitor.central_server import diagnostic_trace as trace
from kiwoom_monitor.central_server.diagnostic_trace_ram import pack_records, Segment
from tests.unit.test_diagnostic_trace_deferred import deferred_capture
from tests.unit.test_diagnostic_delivery_record import identity


class TraceRamBlockTests(unittest.TestCase):
    def test_metadata_and_frozen_payload_have_exact_legacy_encoding(self):
        records = [
            {'seq': 2, 'event_type': 'input', 'unicode': '삼성전자', 'payload': ('map', (('value', 12),))},
            {'seq': 4, 'event_type': 'delivery', 'outcome': None},
            {'seq': 7, 'event_type': 'delivery', 'payload': None},
        ]
        segments = pack_records(records, target_bytes=150)
        restored = []
        for segment in segments:
            while (row := segment.take()) is not None:
                restored.append(dict(row))
                if 'payload' in records[len(restored) - 1]:
                    encoded = json.dumps(records[len(restored) - 1]['payload'], ensure_ascii=False,
                                         separators=(',', ':')).encode('utf-8')
                    self.assertEqual(encoded, row.payload_bytes())
                else:
                    self.assertIsNone(row.payload_bytes())
                segment.commit(row)
            self.assertEqual(len(segment.data), segment.committed_offset)
            self.assertGreaterEqual(segment.charge, len(segment.data))
            self.assertLessEqual(segment.scalar_charge, segment.charge)
        self.assertEqual([{k: v for k, v in r.items() if k != 'payload'} for r in records], restored)

    def test_partial_commit_rewind_keeps_only_uncommitted_suffix(self):
        segment, = pack_records([{'seq': i, 'event_type': 'x'} for i in range(1, 5)])
        first, second = segment.take(), segment.take()
        with self.assertRaisesRegex(RuntimeError, 'out_of_order'):
            segment.commit(second)
        segment.commit(first)
        segment.rewind()
        self.assertEqual(2, segment.take()['seq'])
        self.assertEqual(3, segment.take()['seq'])
        self.assertEqual(4, segment.take()['seq'])
        self.assertIsNone(segment.take())

    def test_invalid_lengths_sequence_and_oversized_input_fail_closed(self):
        segment, = pack_records([{'seq': 1, 'event_type': 'x'}])
        broken = Segment(segment.data[:-1], 0)
        with self.assertRaisesRegex(ValueError, 'frame_invalid'):
            broken.take()
        with self.assertRaisesRegex(ValueError, 'header_invalid'):
            Segment(b'BADMAGIC' + segment.data[8:], 0)
        with self.assertRaisesRegex(ValueError, 'sequence_invalid'):
            pack_records([{'seq': 2}, {'seq': 1}])
        with self.assertRaisesRegex(OSError, 'too_large'):
            pack_records([{'seq': 1, 'payload': 'a' * 100}], max_payload_bytes=20)

    def test_packing_releases_shared_contexts_but_preserves_native_receipt(self):
        pack = trace._pack_deferred
        with patch.object(trace, '_pack_deferred'), deferred_capture() as (token, _):
            receipt, _, _, _ = identity(token)
            for stage in ('enqueue', 'dequeue'):
                trace.emit_delivery(token, receipt, stage)
            pack(trace._SESSION)
            self.assertEqual(0, receipt.node._refs)
            self.assertEqual((0, 0), tuple(node._refs for node in receipt.node.children))
            packed = trace.status()
            self.assertEqual((2, 2, 0, 0), (packed['accepted'], packed['packed_events'],
                                           packed['raw_charged_bytes'], packed['written']))
            trace.emit_delivery(token, receipt, 'consume_end')
            self.assertEqual(1, receipt.node._refs)
            pack(trace._SESSION)
            self.assertEqual(0, receipt.node._refs)
            self.assertEqual(3, trace.status()['queued'])

    def test_encoding_failure_keeps_packed_prefix_and_raw_suffix_owned(self):
        pack = trace._pack_deferred
        with patch.object(trace, '_pack_deferred'), deferred_capture() as (token, _):
            trace.emit(token, 'prefix', {})
            pack(trace._SESSION)
            receipt, _, _, _ = identity(token)
            trace.emit_delivery(token, receipt, 'enqueue')
            before = trace.status()
            with patch.object(trace, 'pack_records', side_effect=ValueError('injected packing failure')):
                with self.assertRaisesRegex(ValueError, 'injected'):
                    pack(trace._SESSION)
            after = trace.status()
            self.assertEqual((before['charged_bytes'], 1, 2, 0),
                             (after['charged_bytes'], after['packed_events'], after['queued'], after['packing_events']))
            self.assertEqual(1, receipt.node._refs)

    def test_raw_overflow_closes_capture_without_waiting_or_early_writing(self):
        with patch.object(trace, '_pack_deferred'), deferred_capture() as (token, _), \
                patch.object(trace, '_RAW_CAPACITY', 2):
            trace.emit(token, 'x', {})
            trace.emit(token, 'x', {})
            trace.emit(token, 'x', {})
            status = trace.status()
            self.assertEqual((2, 1, 0), (status['accepted'], status['known_dropped'], status['written']))
            self.assertEqual({'raw_staging_budget': 1}, status['drop_reasons'])
            self.assertIsNone(trace.token())
            self.assertEqual(2, status['queued'])

    def test_total_capacity_includes_already_packed_events(self):
        pack = trace._pack_deferred
        with patch.object(trace, '_pack_deferred'), deferred_capture() as (token, _), \
                patch.object(trace, '_CAPACITY', 2):
            for _ in range(2):
                trace.emit(token, 'x', {})
                pack(trace._SESSION)
            trace.emit(token, 'x', {})
            state = trace.status()
            self.assertEqual((2, 2, 1, 0), (state['accepted'], state['packed_events'],
                                          state['known_dropped'], state['written']))
            self.assertEqual({'event_capacity': 1}, state['drop_reasons'])

    def test_transfer_budget_failure_keeps_all_original_ownership(self):
        pack = trace._pack_deferred
        with patch.object(trace, '_pack_deferred'), deferred_capture() as (token, _):
            receipt, _, _, _ = identity(token)
            trace.emit_delivery(token, receipt, 'enqueue')
            before = trace.status()
            with trace._LOCK:
                trace._SESSION['memory_limit_bytes'] = trace._PACK_WORKER_RESERVE + 1
            with self.assertRaisesRegex(RuntimeError, 'transfer_budget'):
                pack(trace._SESSION)
            after = trace.status()
            self.assertEqual((before['charged_bytes'], 1, 0),
                             (after['charged_bytes'], after['queued'], after['packed_events']))
            self.assertEqual(1, receipt.node._refs)

    def test_arrival_during_encoding_keeps_refs_and_does_not_wait_for_packer(self):
        pack, encode = trace._pack_deferred, trace.pack_records
        entered, resume, admitted = threading.Event(), threading.Event(), threading.Event()
        errors = []
        with patch.object(trace, '_pack_deferred'), deferred_capture() as (token, _):
            receipt, _, _, _ = identity(token)
            trace.emit_delivery(token, receipt, 'enqueue')
            trace.emit_delivery(token, receipt, 'dequeue')

            def held_encode(records, **kwargs):
                entered.set()
                if not resume.wait(5):
                    raise TimeoutError('test encoding gate')
                return encode(records, **kwargs)

            def run_pack():
                try:
                    pack(trace._SESSION)
                except BaseException as error:
                    errors.append(error)

            def arrive():
                trace.emit_delivery(token, receipt, 'consume_end')
                admitted.set()

            with patch.object(trace, 'pack_records', side_effect=held_encode):
                worker = threading.Thread(target=run_pack)
                worker.start()
                producer = None
                try:
                    self.assertTrue(entered.wait(2))
                    producer = threading.Thread(target=arrive)
                    producer.start()
                    self.assertTrue(admitted.wait(1), 'producer blocked behind RAM encoding')
                finally:
                    resume.set()
                    worker.join(5)
                    if producer is not None:
                        producer.join(5)
            self.assertFalse(errors)
            self.assertEqual(3 - trace.status()['packed_events'], receipt.node._refs)
            self.assertEqual(3, trace.status()['queued'])
            pack(trace._SESSION)
            state = trace.status()
            self.assertEqual((3, 0, 0), (state['packed_events'], state['raw_charged_bytes'], state['known_dropped']))
            self.assertEqual(0, receipt.node._refs)


if __name__ == '__main__':
    unittest.main()
