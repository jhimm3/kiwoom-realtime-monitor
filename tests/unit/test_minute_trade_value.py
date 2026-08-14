from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from kiwoom_monitor.application.minute_trade_value import MinuteOhlcv, MinuteTradeValueAggregator
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import TradeTick


def tick(price: int, volume: int, trade_time: str = "101500") -> TradeTick:
    return TradeTick("005930", price, None, None, volume, None, trade_time)


class MinuteTradeValueTests(unittest.TestCase):
    def test_uses_hero_formula_for_one_minute(self) -> None:
        aggregator = MinuteTradeValueAggregator()
        at = datetime(2026, 8, 14, 10, 15, 10)
        aggregator.ingest(tick(100, 10), at)
        bar = aggregator.ingest(tick(120, 5), at.replace(second=40))

        self.assertIsNotNone(bar)
        self.assertEqual(15, bar.volume)
        self.assertEqual(100, bar.open_price)
        self.assertEqual(120, bar.high_price)
        self.assertEqual(100, bar.low_price)
        self.assertEqual(120, bar.close_price)
        self.assertAlmostEqual(15 * 110 / 100_000_000, bar.trade_value_eok)

    def test_sums_completed_and_current_minutes(self) -> None:
        aggregator = MinuteTradeValueAggregator()
        aggregator.ingest(tick(100, 10), datetime(2026, 8, 14, 10, 15, 0))
        aggregator.ingest(tick(200, 10, "101600"), datetime(2026, 8, 14, 10, 16, 0))

        self.assertAlmostEqual((1_000 + 2_000) / 100_000_000, aggregator.trade_value_eok("005930", 5))

    def test_uses_the_trade_time_when_the_message_arrives_late(self) -> None:
        aggregator = MinuteTradeValueAggregator()

        aggregator.ingest(tick(100, 10, "101500"), datetime(2026, 8, 14, 10, 16, 2))

        bar = aggregator.ingest(tick(120, 5, "101559"), datetime(2026, 8, 14, 10, 16, 3))
        self.assertIsNotNone(bar)
        self.assertEqual(datetime(2026, 8, 14, 10, 15), bar.minute)
        self.assertEqual(15, bar.volume)

    def test_does_not_duplicate_the_in_progress_rest_minute(self) -> None:
        aggregator = MinuteTradeValueAggregator()
        current_minute = datetime.now().replace(second=0, microsecond=0)
        previous_minute = current_minute - timedelta(minutes=1)
        aggregator.seed(
            "005930",
            (
                MinuteOhlcv(previous_minute, 100, 100, 100, 100, 10),
                MinuteOhlcv(current_minute, 150, 150, 150, 150, 20),
            ),
        )

        aggregator.ingest(tick(200, 10), current_minute)

        self.assertAlmostEqual((1_000 + 2_000) / 100_000_000, aggregator.trade_value_eok("005930", 5))
