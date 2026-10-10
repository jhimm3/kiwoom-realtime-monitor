"""Private sizing evidence must distinguish resource saturation from invalid input."""
import unittest

from scripts.check_causal_capture_capacity import classify_capacity_stop, memory_summary, operator_capacity_options, CapacityPairs
from pathlib import Path
import asyncio
import contextlib
import io
import tempfile
from unittest.mock import patch


class CausalCaptureCapacityTests(unittest.TestCase):
    def test_progress_never_reads_blob_index_or_discloses_payloads(self):
        import threading
        from types import SimpleNamespace
        from scripts.check_causal_capture_capacity import persistence_progress
        class UnreadableIndex(dict):
            def items(self):
                raise AssertionError('index must not be copied while polling')
        native = SimpleNamespace(_LOCK=threading.Lock(), _THREAD=None,
            _SESSION={'trace_id': 'fixture', 'state': 'persisting', 'accepted': 2_300_000,
                      'written': 1_000_000, 'blobs': UnreadableIndex(secret='private'),
                      'authority': 'private', 'chunks': UnreadableIndex()})
        progress = persistence_progress(native, 'fixture')
        self.assertEqual({'state': 'persisting', 'writer_alive': False,
                          'accepted': 2_300_000, 'written': 1_000_000}, progress)
        self.assertNotIn('private', repr(progress))
        with self.assertRaisesRegex(RuntimeError, 'private_capture_changed'):
            persistence_progress(native, 'wrong')

    def test_failed_capture_before_persistence_exits_immediately_with_numeric_phase_evidence(self):
        import threading
        import json
        from types import SimpleNamespace
        from scripts.check_causal_capture_capacity import durable_check
        native = SimpleNamespace(_LOCK=threading.Lock(), _THREAD=None,
            _SESSION={'trace_id': 'fixture', 'state': 'failed', 'accepted': 12, 'written': 0},
            stop=lambda **options: None,
            status=lambda: self.fail('failed seal must not copy the full index'))
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaisesRegex(RuntimeError,
                'private_capture_failed_before_persistence'):
            asyncio.run(durable_check(native, 'fixture', 9600))
        last = json.loads(output.getvalue().splitlines()[-1])['persistence_progress']
        self.assertEqual(('seal_failed', 'failed', 12, 0),
            (last['phase'], last['state'], last['accepted'], last['written']))

    def test_interrupted_persistence_is_not_waited_or_accepted_as_complete(self):
        import threading
        import json
        from types import SimpleNamespace
        from scripts.check_causal_capture_capacity import durable_check
        session = {'trace_id': 'fixture', 'state': 'awaiting_persistence', 'accepted': 12,
                   'written': 0, 'known_dropped': 0, 'input_rejected': 0, 'charged_bytes': 5}
        class Wake:
            def set(self):
                session['state'] = 'interrupted'
        native = SimpleNamespace(_LOCK=threading.Lock(), _THREAD=None, _SESSION=session,
            _WAKE=Wake(), stop=lambda **options: None, status=lambda: dict(session))
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaisesRegex(RuntimeError,
                'private_persistence_not_complete'):
            asyncio.run(durable_check(native, 'fixture', 9600))
        last = json.loads(output.getvalue().splitlines()[-1])['persistence_progress']
        self.assertEqual(('persist_failed', 'interrupted', 5),
            (last['phase'], last['state'], last['charged_bytes']))

    def test_bounded_paced_account_probe_persists_distinct_blocks_and_all_native_pairs(self):
        from scripts.check_causal_capture_capacity import run, verify_persisted_capture
        saved_checks = []

        def check_twice(native, identifier, **options):
            first = verify_persisted_capture(native, identifier, **options)
            # Reuse the exact saved files after discarding the live recorder view.
            # This path may read only; starting/stopping a producer is forbidden.
            with patch.object(native, '_SESSION', None), \
                patch.object(native, 'start', side_effect=AssertionError('saved verification must not record')), \
                patch.object(native, 'stop', side_effect=AssertionError('saved verification must not stop')):
                second = verify_persisted_capture(native, identifier, **options)
            for key in ('events', 'large_native_inputs_verified', 'native_operation_method_counts',
                        'closed_deliveries', 'collector_messages', 'catalog_store_rows'):
                self.assertEqual(first[key], second[key])
            saved_checks.append(second)
            return first
        with tempfile.TemporaryDirectory() as directory:
            args = operator_capacity_options('recorder-capacity-smoke', Path(directory))
            # This small fixture never claims the real60second/NAS headroom gate.
            args.messages, args.rows, args.catalog_rows = 20, 2, 50
            args.mixed_every, args.account_every = 10, 10
            args.require_real_headroom = args.hold_window = args.progress = False
            from kiwoom_monitor.central_server import diagnostic_trace as trace
            from tests.unit.test_diagnostic_trace_deferred import recorder_storage_headroom
            with recorder_storage_headroom(), patch.object(trace, '_deferred_memory_check',
                return_value={'bounded_unit_fixture': True}), \
                patch.object(trace, 'chunk_bytes', side_effect=AssertionError('do not copy full index per chunk')), \
                patch.object(trace, 'payload_bytes', side_effect=AssertionError('do not copy full index per payload')), \
                patch('scripts.check_causal_capture_capacity.verify_persisted_capture', side_effect=check_twice), \
                contextlib.redirect_stdout(io.StringIO()):
                result = asyncio.run(run(args))
            self.assertEqual(1, len(saved_checks))
            self.assertEqual(20, result['messages_completed'])
            self.assertLess(result['actual_capture_wall_seconds'], 60)
            self.assertEqual('complete', result['persistence']['state'])
            self.assertEqual(2, result['persistence']['large_native_inputs_verified'])
            self.assertEqual(20 + result['native_counts']['mixed_rounds'], result['persistence']['collector_messages'])
            self.assertEqual(result['native_counts']['native_delivery_supported'], result['persistence']['closed_deliveries'])
            self.assertEqual(4, result['native_counts']['native_delivery_uncovered'])
            self.assertFalse(result['persistence']['whole_causal_compiler_exercised'])
            for method, count in result['account_large_method_counts'].items():
                self.assertEqual(count, result['persistence']['native_operation_method_counts'][method])
            for method, count in result['native_store_method_counts'].items():
                self.assertEqual(count, result['persistence']['native_operation_method_counts'].get(method, 0))
            self.assertEqual((0, 0), (result['trace']['input_rejected'], result['trace']['known_dropped']))

    def test_pinned_verification_rejects_a_changed_terminal_manifest(self):
        from scripts.check_causal_capture_capacity import run
        from kiwoom_monitor.central_server import diagnostic_trace as trace
        from tests.unit.test_diagnostic_trace_deferred import recorder_storage_headroom
        native_read = trace._payload_bytes_from_manifest
        changed = False

        def mutate_manifest(identifier, digest, manifest):
            nonlocal changed
            content = native_read(identifier, digest, manifest)
            if not changed:
                path = trace._directory() / identifier / 'manifest.json'
                path.write_bytes(path.read_bytes() + b'\n')
                changed = True
            return content

        with tempfile.TemporaryDirectory() as directory:
            args = operator_capacity_options('recorder-capacity-smoke', Path(directory))
            args.messages, args.rows, args.catalog_rows = 2, 1, 2
            args.mixed_every, args.account_large = 0, False
            args.require_real_headroom = args.hold_window = args.progress = False
            with recorder_storage_headroom(), patch.object(trace, '_deferred_memory_check',
                return_value={'bounded_unit_fixture': True}), \
                patch.object(trace, '_payload_bytes_from_manifest', side_effect=mutate_manifest), \
                contextlib.redirect_stdout(io.StringIO()), \
                self.assertRaisesRegex(RuntimeError, 'private_final_manifest_changed'):
                asyncio.run(run(args))
            self.assertTrue(changed)

    def test_operator_profiles_have_fixed_real_clock_workload_and_no_mock_headroom(self):
        for profile, seconds in [('recorder-capacity-smoke', 60), ('recorder-capacity', 3600)]:
            args = operator_capacity_options(profile, Path('/private/job'))
            self.assertEqual(seconds * 20, args.messages)
            self.assertEqual((8, 5_000_000, 10, 600, 200),
                (args.memory_gib, args.event_capacity, args.rows, args.account_every, args.mixed_every))
            self.assertTrue(args.require_real_headroom and args.persist and args.hold_window)
            self.assertEqual(seconds, args.messages / args.paced_rate)
        with self.assertRaisesRegex(ValueError, 'unknown_recorder_capacity_profile'):
            operator_capacity_options('arbitrary', Path('/private/job'))

    def test_streamed_pairs_reject_drops_unknown_parents_and_open_work(self):
        for row in [{'event_type': 'input_rejected'},
                    {'event_type': 'top20_delivery', 'delivery_id': 'd', 'source_complete': True,
                     'stage': 'enqueue', 'parent_input_ids': ['unknown']}]:
            with self.assertRaises(RuntimeError):
                CapacityPairs().add(row)
        pairs = CapacityPairs()
        pairs.add({'event_type': 'operation_start', 'operation_id': 'o', 'method': 'save_query'})
        with self.assertRaisesRegex(RuntimeError, 'unclosed_pairs'):
            pairs.finish()
        with self.assertRaisesRegex(RuntimeError, 'pair_mismatch'):
            pairs.add({'event_type': 'operation_end', 'operation_id': 'o', 'method': 'load_query', 'outcome': 'returned'})

    def test_subscription_reuses_start_id_only_for_matching_single_end(self):
        start = dict(event_type='top20_realtime_input', input_id='s', input_kind='subscription_start',
                     producer_component='collector')
        end = {**start, 'input_kind': 'subscription_end'}
        pairs = CapacityPairs()
        pairs.add(start)
        pairs.add(end)
        self.assertFalse(pairs.subscriptions)
        with self.assertRaisesRegex(RuntimeError, 'subscription_pair_mismatch'):
            pairs.add(end)
        for invalid in (start, {**end, 'producer_component': 'other'}):
            pairs = CapacityPairs()
            pairs.add(start)
            with self.assertRaises(RuntimeError):
                pairs.add(invalid)

    def test_memory_rejection_does_not_hide_invalid_payload_or_profile(self):
        for reason in ('capture_copy_busy', 'capture_input_too_large', 'unknown_payload_profile'):
            with self.subTest(reason=reason):
                self.assertEqual('input_rejected', classify_capacity_stop({
                    'input_rejected_reasons': {'capture_memory_full': 1, reason: 1},
                    'drop_reasons': {'event_capacity': 1},
                }))

    def test_expected_memory_and_event_saturation_remain_identifiable(self):
        self.assertEqual('copy_reservation_budget', classify_capacity_stop({
            'input_rejected_reasons': {'capture_memory_full': 1},
        }))
        for reason in ('event_capacity', 'memory_budget', 'payload_memory_budget'):
            with self.subTest(reason=reason):
                self.assertEqual(reason, classify_capacity_stop({'drop_reasons': {reason: 1}}))
        self.assertEqual('unexpected_drop', classify_capacity_stop({
            'drop_reasons': {'event_capacity': 1, 'unexpected_worker_error': 1},
        }))

    def test_memory_summary_keeps_transient_peak_and_lowest_headroom(self):
        before = dict(rss_bytes=10, peak_rss_bytes=10, host_available_bytes=100,
                      container_headroom_bytes=90)
        during = dict(rss_bytes=35, peak_rss_bytes=40, host_available_bytes=60,
                      container_headroom_bytes=50)
        after = dict(rss_bytes=20, peak_rss_bytes=40, host_available_bytes=80,
                     container_headroom_bytes=70)
        self.assertEqual({
            'rss_bytes_max': 35, 'peak_rss_bytes_max': 40,
            'host_available_bytes_min': 60, 'container_headroom_bytes_min': 50,
            'observations': 3,
        }, memory_summary(before, after, [{'memory': during}]))
        self.assertIsNone(memory_summary({}, {}, [])['host_available_bytes_min'])


if __name__ == '__main__':
    unittest.main()
