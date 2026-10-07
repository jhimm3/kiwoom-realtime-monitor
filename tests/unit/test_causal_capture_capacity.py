"""Private sizing evidence must distinguish resource saturation from invalid input."""
import unittest

from scripts.check_causal_capture_capacity import classify_capacity_stop, memory_summary


class CausalCaptureCapacityTests(unittest.TestCase):
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
