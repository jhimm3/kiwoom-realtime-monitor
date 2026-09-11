from __future__ import annotations

import unittest
from datetime import datetime

from kiwoom_monitor.central_server.minute_bars import MinuteBarAccumulator
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import TradeTick


class MinuteBarAccumulatorTests(unittest.TestCase):
    def test_builds_ohlcv_and_cumulative_trade_value_increment(self) -> None:
        accumulator = MinuteBarAccumulator()
        now = datetime(2026, 9, 8, 10, 1)
        accumulator.add(TradeTick("005930", 70000, 100, 1000, 3, 70000, "100101"), now, 1.0)
        accumulator.add(TradeTick("005930", 70100, 105, 1012, 5, 70100, "100130"), now, 2.0)
        accumulator.add(TradeTick("005930", 69900, 107, 1017, 2, 70100, "100159"), now, 3.0)
        bars = accumulator.drain_dirty()
        self.assertEqual(1, len(bars))
        self.assertEqual((70000, 70100, 69900, 69900), tuple(bars[0][key] for key in ("open", "high", "low", "close")))
        self.assertEqual(10, bars[0]["volume"])
        self.assertEqual(17, bars[0]["trade_value_million_won"])

    def test_keeps_krx_and_nxt_separate(self) -> None:
        accumulator = MinuteBarAccumulator()
        now = datetime(2026, 9, 8, 10)
        for market in ("KRX", "NXT"):
            accumulator.add(TradeTick("005930", 70000, 1, 10, 1, 70000, "100001", market=market), now, 1.0)
        self.assertEqual({"KRX", "NXT"}, {bar["market"] for bar in accumulator.drain_dirty()})


if __name__ == "__main__":
    unittest.main()
