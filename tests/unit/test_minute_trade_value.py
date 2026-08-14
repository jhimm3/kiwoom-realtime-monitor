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

    def test_can_exclude_the_current_minute_for_completed_strength(self) -> None:
        aggregator = MinuteTradeValueAggregator()
        now = datetime(2026, 8, 14, 10, 16, 10)
        aggregator.ingest(tick(100, 10, "101500"), now)
        aggregator.ingest(tick(200, 10, "101600"), now)

        self.assertAlmostEqual(1_000 / 100_000_000, aggregator.completed_trade_value_eok("005930", 1, now))
        self.assertAlmostEqual(1_000 / 100_000_000, aggregator.completed_today_trade_value_eok("005930", now))

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

    def test_keeps_live_current_minute_when_history_is_seeded(self) -> None:
        aggregator = MinuteTradeValueAggregator()
        now = datetime(2026, 8, 14, 10, 15, 10)
        aggregator.ingest(tick(200, 10, "101510"), now)

        aggregator.seed(
            "005930",
            (
                MinuteOhlcv(now.replace(minute=14, second=0), 100, 100, 100, 100, 10),
                MinuteOhlcv(now.replace(second=0), 500, 500, 500, 500, 9_999_999),
            ),
            now,
        )

        self.assertAlmostEqual((1_000 + 2_000) / 100_000_000, aggregator.trade_value_eok("005930", 5))

    def test_uses_cumulative_volume_when_trade_volume_is_missing(self) -> None:
        aggregator = MinuteTradeValueAggregator()
        now = datetime(2026, 8, 14, 10, 0, 1)
        aggregator.ingest(TradeTick("005930", 100, 1_000, None, None, None, "100001"), now)
        aggregator.ingest(TradeTick("005930", 100, 1_050, None, None, None, "100002"), now)
        self.assertEqual(50, aggregator._bars["005930"][-1].volume)

    def test_prefers_each_trade_volume_over_cumulative_volume(self) -> None:
        aggregator = MinuteTradeValueAggregator()
        now = datetime(2026, 8, 14, 10, 0, 1)

        aggregator.ingest(TradeTick("005930", 100, 1_000, None, 7, None, "100001"), now)
        aggregator.ingest(TradeTick("005930", 100, 9_000_000, None, 3, None, "100002"), now)

        self.assertEqual(10, aggregator._bars["005930"][-1].volume)
