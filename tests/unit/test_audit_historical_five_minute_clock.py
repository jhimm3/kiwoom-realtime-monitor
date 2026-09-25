from __future__ import annotations

import unittest
from datetime import date

from scripts.audit_historical_five_minute_clock import expected_clock_bins, summarize_day


class HistoricalFiveMinuteClockTests(unittest.TestCase):
    def test_close_print_is_separate_from_continuous_five_minute_bins(self):
        expected = expected_clock_bins()
        self.assertEqual(77, len(expected))
        self.assertIn("15:20", expected)
        self.assertNotIn("15:25", expected)
        self.assertIn("15:30", expected)

    def test_absent_bin_is_unobserved_and_mixed_resolution_is_visible(self):
        rows = [{"interval_seconds": 300, "bar_time": "2023-09-22T15:30:00+09:00",
                 "bar_time_semantics": "interval_end", "open": 10, "high": 10,
                 "low": 10, "close": 10, "volume": 5},
                {"interval_seconds": 60}]
        result = summarize_day("005930", "2023-09-22", rows)
        self.assertTrue(result["close_auction_print_present"])
        self.assertTrue(result["mixed_resolution_day"])
        self.assertEqual(1, result["one_minute_rows"])
        self.assertEqual(76, len(result["unobserved_expected_clock_bins"]))

    def test_one_minute_only_day_is_not_reported_as_seventy_seven_five_minute_gaps(self):
        result = summarize_day("005930", "2024-08-29", [{"interval_seconds": 60}])
        self.assertFalse(result["five_minute_source_present"])
        self.assertEqual([], result["unobserved_expected_clock_bins"])

    def test_verified_delayed_session_shifts_all_expected_bins(self):
        expected = expected_clock_bins(date(2025, 11, 13))
        self.assertEqual(77, len(expected))
        self.assertIn("10:05", expected)
        self.assertIn("16:20", expected)
        self.assertIn("16:30", expected)
        self.assertIn("15:30", expected)  # Continuous bar on the delayed day.
        self.assertNotIn("16:25", expected)


if __name__ == "__main__":
    unittest.main()
