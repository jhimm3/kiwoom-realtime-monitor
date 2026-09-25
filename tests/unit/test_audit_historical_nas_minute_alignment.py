from __future__ import annotations

import unittest

from scripts.audit_historical_nas_minute_alignment import compare_day


class HistoricalNasMinuteAlignmentTests(unittest.TestCase):
    def test_end_start_alignment_and_nonregular_rows_are_separate(self):
        local = [{"bar_time": "2026-09-22T09:01:00+09:00", "open": 10, "high": 11,
                  "low": 9, "close": 10, "volume": 100}]
        nas = [{"minute": "09:00", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100},
               {"minute": "15:35", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100}]
        result = compare_day("005930", "2026-09-22", local, nas, False)
        self.assertEqual(1, result["shared_minutes"])
        self.assertEqual(1, result["matching_ohlcv"])
        self.assertEqual(1, result["nas_outside_regular_minutes"])
        self.assertEqual([], result["creon_only_minutes"])

    def test_missing_nas_bar_and_volume_difference_remain_visible(self):
        local = [
            {"bar_time": "2026-09-22T09:01:00+09:00", "open": 10, "high": 11,
             "low": 9, "close": 10, "volume": 100},
            {"bar_time": "2026-09-22T09:02:00+09:00", "open": 10, "high": 11,
             "low": 9, "close": 10, "volume": 100},
        ]
        nas = [{"minute": "09:00", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 99}]
        result = compare_day("005930", "2026-09-22", local, nas, False)
        self.assertEqual(["09:01"], result["creon_only_minutes"])
        self.assertEqual(["09:00"], result["mismatching_minutes"])
        self.assertEqual(0, result["matching_ohlcv"])

    def test_duplicate_creon_minute_is_rejected(self):
        row = {"bar_time": "2026-09-22T09:01:00+09:00", "open": 10, "high": 11,
               "low": 9, "close": 10, "volume": 100}
        with self.assertRaisesRegex(ValueError, "duplicate CREON"):
            compare_day("005930", "2026-09-22", [row, row], [], False)

    def test_close_print_matches_same_1530_nas_clock(self):
        local = [{"bar_time": "2026-09-22T15:30:00+09:00", "open": 10, "high": 10,
                  "low": 10, "close": 10, "volume": 100}]
        nas = [{"minute": "15:30", "open": 10, "high": 10, "low": 10,
                "close": 10, "volume": 100}]
        result = compare_day("005930", "2026-09-22", local, nas, False)
        self.assertEqual(1, result["matching_ohlcv"])
        self.assertEqual([], result["creon_only_minutes"])

    def test_delayed_session_1530_is_ordinary_and_1630_is_close(self):
        def row(clock):
            return {"bar_time": f"2025-11-13T{clock}:00+09:00", "open": 10, "high": 10,
                    "low": 10, "close": 10, "volume": 100}
        nas = [{"minute": clock, "open": 10, "high": 10, "low": 10,
                "close": 10, "volume": 100} for clock in ("15:29", "15:30", "16:30")]
        result = compare_day("347850", "2025-11-13",
                             [row("15:30"), row("15:31"), row("16:30")], nas, False)
        self.assertEqual(3, result["matching_ohlcv"])
        self.assertEqual([], result["mismatching_minutes"])


if __name__ == "__main__":
    unittest.main()
