from __future__ import annotations

import unittest
from datetime import datetime

from kiwoom_monitor.central_server.minute_bars import SecondTradeAccumulator
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import TradeTick


def _tick(
    price: int,
    volume: int | None,
    trade_time: str,
    *,
    cumulative_volume: int | None,
    cumulative_trade_value: int | None = None,
    market: str = "KRX",
) -> TradeTick:
    return TradeTick(
        "005930", price, cumulative_volume, cumulative_trade_value,
        volume, None, trade_time, market=market,
    )


class SecondTradeAggregationTests(unittest.TestCase):
    def test_same_second_builds_ohlcv_trade_value_and_count(self) -> None:
        accumulator = SecondTradeAccumulator()
        now = datetime(2026, 9, 10, 10, 0, 1)

        accumulator.add(_tick(100, 2, "100001", cumulative_volume=1_000), now, 1.0)
        accumulator.add(_tick(110, 3, "100001", cumulative_volume=1_003), now, 2.0)
        accumulator.add(_tick(90, 1, "100001", cumulative_volume=1_004), now, 3.0)

        [bar] = accumulator.drain_dirty()
        self.assertEqual(
            (100, 110, 90, 90),
            (bar["open"], bar["high"], bar["low"], bar["close"]),
        )
        self.assertEqual(6, bar["volume"])
        self.assertEqual(620, bar["trade_value_won"])
        self.assertEqual(3, bar["trade_count"])
        self.assertEqual(3.0, bar["available_at"])

    def test_duplicate_provider_tick_is_ignored(self) -> None:
        accumulator = SecondTradeAccumulator()
        now = datetime(2026, 9, 10, 10, 0, 1)
        tick = _tick(
            70_000, 2, "100001", cumulative_volume=10_002,
            cumulative_trade_value=700,
        )

        accumulator.add(tick, now, 1.0)
        accumulator.add(tick, now, 2.0)

        [bar] = accumulator.drain_dirty()
        self.assertEqual(2, bar["volume"])
        self.assertEqual(140_000, bar["trade_value_won"])
        self.assertEqual(1, bar["trade_count"])

    def test_out_of_order_second_within_late_window_keeps_its_own_bar(self) -> None:
        accumulator = SecondTradeAccumulator(max_late_seconds=5)
        now = datetime(2026, 9, 10, 10, 0, 2)

        accumulator.add(_tick(102, 2, "100002", cumulative_volume=1_002), now, 2.0)
        accumulator.add(_tick(101, 1, "100001", cumulative_volume=1_001), now, 3.0)

        bars = {str(value["trade_second"]): value for value in accumulator.drain_dirty()}
        self.assertEqual({"10:00:01", "10:00:02"}, set(bars))
        self.assertEqual(1, bars["10:00:01"]["volume"])
        self.assertEqual(2, bars["10:00:02"]["volume"])

    def test_tick_older_than_late_window_is_ignored(self) -> None:
        accumulator = SecondTradeAccumulator(max_late_seconds=5)
        now = datetime(2026, 9, 10, 10, 0, 10)

        accumulator.add(_tick(110, 1, "100010", cumulative_volume=1_010), now, 1.0)
        accumulator.add(_tick(104, 1, "100004", cumulative_volume=1_004), now, 2.0)

        bars = accumulator.drain_dirty()
        self.assertEqual(["10:00:10"], [value["trade_second"] for value in bars])

    def test_first_stale_trade_time_is_not_assigned_to_received_date(self) -> None:
        accumulator = SecondTradeAccumulator(max_late_seconds=5)
        now = datetime(2026, 9, 10, 8, 0, 0)

        accumulator.add(_tick(100, 1, "195959", cumulative_volume=1), now, 1.0)

        self.assertEqual([], accumulator.drain_dirty())

    def test_date_and_venue_are_independent_keys(self) -> None:
        accumulator = SecondTradeAccumulator()
        first_day = datetime(2026, 9, 10, 10, 0, 1)
        next_day = datetime(2026, 9, 11, 10, 0, 1)

        accumulator.add(_tick(100, 1, "100001", cumulative_volume=1, market="KRX"), first_day, 1.0)
        accumulator.add(_tick(200, 2, "100001", cumulative_volume=2, market="NXT"), first_day, 2.0)
        accumulator.add(_tick(300, 3, "100001", cumulative_volume=3, market="KRX"), next_day, 3.0)

        bars = accumulator.drain_dirty()
        keys = {
            (value["trading_date"], value["market"]): value
            for value in bars
        }
        self.assertEqual(
            {("2026-09-10", "KRX"), ("2026-09-10", "NXT"), ("2026-09-11", "KRX")},
            set(keys),
        )
        self.assertEqual(1, keys[("2026-09-10", "KRX")]["volume"])
        self.assertEqual(2, keys[("2026-09-10", "NXT")]["volume"])
        self.assertEqual(3, keys[("2026-09-11", "KRX")]["volume"])
        self.assertEqual(
            {("2026-09-11", "005930", "KRX")},
            set(accumulator._cumulative_volume),
        )

    def test_cumulative_volume_is_fallback_when_trade_volume_is_missing(self) -> None:
        accumulator = SecondTradeAccumulator()
        now = datetime(2026, 9, 10, 10, 0, 1)

        accumulator.add(_tick(100, None, "100001", cumulative_volume=1_000), now, 1.0)
        accumulator.add(_tick(110, None, "100001", cumulative_volume=1_003), now, 2.0)

        [bar] = accumulator.drain_dirty()
        self.assertEqual(3, bar["volume"])
        self.assertEqual(330, bar["trade_value_won"])


if __name__ == "__main__":
    unittest.main()
