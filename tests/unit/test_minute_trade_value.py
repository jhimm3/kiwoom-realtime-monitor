from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from kiwoom_monitor.application.minute_trade_value import MinuteOhlcv, MinuteTradeValueAggregator
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import TradeTick


def tick(price: int, volume: int, trade_time: str = "101500") -> TradeTick:
    return TradeTick("005930", price, None, None, volume, None, trade_time)


class MinuteTradeValueTests(unittest.TestCase):
    def test_uses_cumulative_trade_value_delta_when_available(self) -> None:
        aggregator = MinuteTradeValueAggregator()
        now = datetime(2026, 8, 14, 10, 0, 1)

        # 0B 14는 백만원 단위다. 최초 1,000은 기준점이고 1,250으로
        # 증가한 250만 원만 이 분봉의 거래대금으로 반영한다.
        aggregator.ingest(TradeTick("005930", 100, None, 1_000, 10, None, "100001"), now)
        aggregator.ingest(TradeTick("005930", 120, None, 1_250, 20, None, "100002"), now)

        self.assertAlmostEqual(2.5, aggregator.bucket_trade_value_eok("005930", 1, now))

    def test_does_not_add_first_cumulative_trade_value_after_connecting(self) -> None:
        aggregator = MinuteTradeValueAggregator()
        now = datetime(2026, 8, 14, 10, 0, 1)

        aggregator.ingest(TradeTick("005930", 100, None, 50_000, 10, None, "100001"), now)

        self.assertEqual(0.0, aggregator.bucket_trade_value_eok("005930", 1, now))

    def test_zero_snapshot_before_first_positive_cumulative_value_is_not_added(self) -> None:
        aggregator = MinuteTradeValueAggregator()
        now = datetime(2026, 8, 14, 10, 0, 1)

        # 순위 재진입 때 0 스냅샷 뒤 당일 누적값이 오더라도, 공백 구간
        # 전체를 현재 1분에 넣지 않고 첫 양수 값을 새 기준점으로 삼는다.
        aggregator.ingest(TradeTick("005930", 100, None, 0, 10, None, "100001"), now)
        aggregator.ingest(TradeTick("005930", 100, None, 7_457, 10, None, "100002"), now)
        aggregator.ingest(TradeTick("005930", 100, None, 7_557, 10, None, "100003"), now)

        self.assertEqual(1.0, aggregator.bucket_trade_value_eok("005930", 1, now))

    def test_zero_snapshot_after_resubscription_does_not_add_the_gap(self) -> None:
        aggregator = MinuteTradeValueAggregator()
        now = datetime(2026, 8, 14, 9, 9, 1)
        aggregator.ingest(TradeTick("005930", 100, None, 9_000, 10, None, "090901"), now)
        aggregator.ingest(TradeTick("005930", 100, None, 9_100, 10, None, "090902"), now)

        aggregator.reset_cumulative_baselines(("005930",))
        reentered = now.replace(minute=57)
        aggregator.ingest(TradeTick("005930", 100, None, 0, 10, None, "095701"), reentered)
        aggregator.ingest(TradeTick("005930", 100, None, 16_557, 10, None, "095702"), reentered)
        aggregator.ingest(TradeTick("005930", 100, None, 16_657, 10, None, "095703"), reentered)

        self.assertEqual(1.0, aggregator.bucket_trade_value_eok("005930", 1, reentered))

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

    def test_reset_cumulative_baseline_does_not_move_disconnect_gap_into_current_minute(self) -> None:
        aggregator = MinuteTradeValueAggregator()
        now = datetime(2026, 8, 14, 10, 0, 1)
        aggregator.ingest(TradeTick("005930", 100, None, 1_000, 10, None, "100001"), now)
        aggregator.ingest(TradeTick("005930", 100, None, 1_100, 10, None, "100002"), now)

        aggregator.reset_cumulative_baselines(("005930",))
        aggregator.ingest(TradeTick("005930", 100, None, 5_100, 10, None, "100102"), now.replace(minute=1))

        self.assertEqual(0.0, aggregator.bucket_trade_value_eok("005930", 1, now.replace(minute=1)))

    def test_first_trade_delta_is_included_when_minute_changes(self) -> None:
        aggregator = MinuteTradeValueAggregator()
        now = datetime(2026, 8, 14, 10, 0, 59)
        aggregator.ingest(TradeTick("005930", 100, None, 100_000, 10, None, "100059"), now)

        aggregator.ingest(
            TradeTick("005930", 100, None, 100_300, 10, None, "100100"),
            now.replace(minute=1, second=0),
        )

        self.assertEqual(3.0, aggregator.bucket_trade_value_eok("005930", 1, now.replace(minute=1, second=0)))

    def test_krx_and_nxt_cumulative_values_use_separate_baselines(self) -> None:
        aggregator = MinuteTradeValueAggregator()
        now = datetime(2026, 8, 14, 10, 0, 1)
        aggregator.ingest(TradeTick("005930", 100, None, 100_000, 10, None, "100001", market="KRX"), now)
        aggregator.ingest(TradeTick("005930", 100, None, 20_000, 10, None, "100001", market="NXT"), now)
        aggregator.ingest(TradeTick("005930", 100, None, 100_300, 10, None, "100002", market="KRX"), now)
        aggregator.ingest(TradeTick("005930", 100, None, 20_200, 10, None, "100002", market="NXT"), now)

        self.assertEqual(5.0, aggregator.bucket_trade_value_eok("005930", 1, now))
