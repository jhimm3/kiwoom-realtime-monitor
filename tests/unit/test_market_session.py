from __future__ import annotations

import unittest
from datetime import datetime

from kiwoom_monitor.infrastructure.kiwoom_rest.realtime_worker import market_session


class MarketSessionTests(unittest.TestCase):
    def test_uses_krx_during_regular_hours(self) -> None:
        self.assertEqual("KRX", market_session(datetime(2026, 8, 14, 10, 0), "real"))

    def test_uses_nxt_only_for_real_api_before_regular_hours(self) -> None:
        self.assertEqual("NXT", market_session(datetime(2026, 8, 14, 8, 30), "real"))
        self.assertIsNone(market_session(datetime(2026, 8, 14, 8, 30), "mock"))

    def test_uses_nxt_after_regular_hours_and_stops_at_twenty(self) -> None:
        self.assertEqual("NXT", market_session(datetime(2026, 8, 14, 16, 0), "real"))
        self.assertIsNone(market_session(datetime(2026, 8, 14, 20, 0), "real"))
