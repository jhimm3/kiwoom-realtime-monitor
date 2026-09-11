from __future__ import annotations

import unittest
from datetime import datetime

from kiwoom_monitor.application.market_session_schedule import (
    active_realtime_codes,
    after_hours_data_pause,
    is_nxt_only_session,
    next_realtime_session_boundary,
    top20_collection_available,
    top20_collection_open,
)


class MarketSessionScheduleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.codes = ("A", "B")
        self.nxt_codes = {"B"}

    def test_realtime_codes_follow_krx_and_nxt_boundaries(self) -> None:
        self.assertEqual((), active_realtime_codes(self.codes, self.nxt_codes, datetime(2026, 9, 9, 7, 59)))
        self.assertEqual(("B",), active_realtime_codes(self.codes, self.nxt_codes, datetime(2026, 9, 9, 8, 0)))
        self.assertEqual(self.codes, active_realtime_codes(self.codes, self.nxt_codes, datetime(2026, 9, 9, 9, 0)))
        self.assertEqual(self.codes, active_realtime_codes(self.codes, self.nxt_codes, datetime(2026, 9, 9, 15, 29)))
        self.assertEqual(("B",), active_realtime_codes(self.codes, self.nxt_codes, datetime(2026, 9, 9, 15, 30)))
        self.assertEqual((), active_realtime_codes(self.codes, self.nxt_codes, datetime(2026, 9, 9, 20, 0)))

    def test_nxt_only_requires_real_environment_and_weekday(self) -> None:
        morning = datetime(2026, 9, 9, 8, 30)
        saturday = datetime(2026, 9, 12, 8, 30)
        self.assertTrue(is_nxt_only_session("real", morning))
        self.assertFalse(is_nxt_only_session("mock", morning))
        self.assertFalse(is_nxt_only_session("real", saturday))

    def test_next_session_boundary_skips_boundary_equal_to_now(self) -> None:
        self.assertEqual(
            datetime(2026, 9, 9, 9, 0),
            next_realtime_session_boundary(datetime(2026, 9, 9, 8, 55)),
        )

    def test_after_hours_pause_and_top20_collection_keep_existing_windows(self) -> None:
        self.assertTrue(after_hours_data_pause(datetime(2026, 9, 9, 7, 54)))
        self.assertFalse(after_hours_data_pause(datetime(2026, 9, 9, 7, 55)))
        self.assertTrue(after_hours_data_pause(datetime(2026, 9, 9, 20, 5)))
        self.assertTrue(top20_collection_open(datetime(2026, 9, 9, 8, 0)))
        self.assertFalse(top20_collection_open(datetime(2026, 9, 9, 20, 0)))
        self.assertFalse(top20_collection_open(datetime(2026, 9, 12, 10, 0)))

    def test_nas_disconnect_routes_leave_a_visible_top20_gap(self) -> None:
        now = datetime(2026, 9, 9, 10, 0)
        self.assertTrue(top20_collection_available(now, ""))
        self.assertTrue(top20_collection_available(now, "central"))
        self.assertFalse(top20_collection_available(now, "central_waiting"))
        self.assertFalse(top20_collection_available(now, "central_retry"))
        self.assertFalse(top20_collection_available(now, "local_fallback"))


if __name__ == "__main__":
    unittest.main()
