from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

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

    def test_reactivated_source_uses_first_cumulative_value_as_baseline(self) -> None:
        accumulator = MinuteBarAccumulator()
        now = datetime(2026, 9, 14, 10)
        accumulator.add(TradeTick("005930", 100, 1, 100, 1, 100, "100001", market="KRX"), now, 1.0)
        accumulator.add(TradeTick("005930", 100, 2, 110, 1, 100, "100002", market="KRX"), now, 2.0)
        accumulator.reset_cumulative_sources({("005930", "KRX")})
        accumulator.add(TradeTick("005930", 100, 3, 500, 1, 100, "100003", market="KRX"), now, 3.0)
        accumulator.add(TradeTick("005930", 100, 4, 520, 1, 100, "100004", market="KRX"), now, 4.0)

        [bar] = accumulator.drain_dirty()
        self.assertEqual(30, bar["trade_value_million_won"])

    def test_timer_closes_seen_window_without_synthesizing_empty_bar(self) -> None:
        accumulator = MinuteBarAccumulator()
        kst = timezone(timedelta(hours=9))
        now = datetime(2026, 9, 8, 10, 0, 20, tzinfo=kst)
        accumulator.add(TradeTick("005930", 70000, 1, 10, 1, 70000, "100020"), now, now.timestamp())
        self.assertEqual([], accumulator.drain_closed(now.replace(minute=1, second=1)))
        [closed] = accumulator.drain_closed(now.replace(minute=1, second=2))
        self.assertEqual("10:00", closed["minute"])
        self.assertEqual("complete", closed["capture_quality"])
        self.assertTrue(closed["operation_id"])
        self.assertEqual([], accumulator.drain_closed(now.replace(minute=2)))

    def test_disconnect_marks_open_window_partial(self) -> None:
        accumulator = MinuteBarAccumulator()
        now = datetime(2026, 9, 8, 10, 0, 20)
        accumulator.add(TradeTick("005930", 70000, 1, 10, 1, 70000, "100020"), now, 1.0)
        accumulator.mark_capture_gap()
        [closed] = accumulator.drain_closed(now.replace(minute=1, second=2))
        self.assertEqual("partial", closed["capture_quality"])


if __name__ == "__main__":
    unittest.main()
