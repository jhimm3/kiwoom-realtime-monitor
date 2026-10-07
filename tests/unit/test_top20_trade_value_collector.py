from __future__ import annotations

import unittest
from datetime import datetime

from kiwoom_monitor.application.top20_trade_value_collector import Top20TradeValueCollector


class Top20TradeValueCollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.values = {"A": 0.0, "B": 0.0}
        self.markets = {"A": "KOSPI", "B": "KOSDAQ"}
        self.value = lambda code, _minute: self.values[code]
        self.market = lambda code: self.markets[code]

    def test_applies_ranked_codes_at_next_half_minute_and_closes_one_minute(self) -> None:
        collector = Top20TradeValueCollector()
        collector.prepare(("A",), datetime(2026, 9, 9, 9, 0, 5), enabled=True, collection_open=True)
        collector.advance(
            datetime(2026, 9, 9, 9, 0, 30), enabled=True, collection_open=True,
            value_provider=self.value, market_provider=self.market,
        )
        self.values["A"] = 2.5

        update = collector.advance(
            datetime(2026, 9, 9, 9, 1), enabled=True, collection_open=True,
            value_provider=self.value, market_provider=self.market,
        )

        self.assertIsNotNone(update.completed)
        assert update.completed is not None
        self.assertEqual((2.5, 0.0, 0.0), update.completed.market_values)
        self.assertEqual("partial", update.completed.capture_state)
        self.assertEqual([(datetime(2026, 9, 9, 9, 0), 2.5, 0.0, 0.0)], list(collector.completed))

    def test_full_minute_is_complete_until_a_queue_gap_is_reported(self) -> None:
        collector = Top20TradeValueCollector()
        collector.active_codes = ("A",)
        collector.advance(datetime(2026, 9, 9, 9, 0), enabled=True, collection_open=True,
                          value_provider=self.value, market_provider=self.market)
        for second in range(1, 60):
            collector.advance(datetime(2026, 9, 9, 9, 0, second), enabled=True, collection_open=True,
                              value_provider=self.value, market_provider=self.market)
        self.values["A"] = 2.0
        first = collector.advance(datetime(2026, 9, 9, 9, 1), enabled=True, collection_open=True,
                                  value_provider=self.value, market_provider=self.market)
        self.assertEqual("realtime_complete", first.completed.capture_state)
        collector.mark_gap()
        self.values["A"] = 3.0
        second = collector.advance(datetime(2026, 9, 9, 9, 2), enabled=True, collection_open=True,
                                   value_provider=self.value, market_provider=self.market)
        self.assertEqual("partial", second.completed.capture_state)

    def test_collector_loop_gap_marks_minute_partial_without_tick_count_assumption(self) -> None:
        collector = Top20TradeValueCollector()
        collector.active_codes = ("A",)
        collector.advance(datetime(2026, 9, 9, 9, 0), enabled=True, collection_open=True,
                          value_provider=self.value, market_provider=self.market)
        collector.advance(datetime(2026, 9, 9, 9, 0, 5), enabled=True, collection_open=True,
                          value_provider=self.value, market_provider=self.market)
        finished = collector.advance(datetime(2026, 9, 9, 9, 1), enabled=True, collection_open=True,
                                     value_provider=self.value, market_provider=self.market)
        self.assertEqual("partial", finished.completed.capture_state)

    def test_cohort_change_preserves_old_segment_and_starts_new_baseline(self) -> None:
        collector = Top20TradeValueCollector()
        collector.active_codes = ("A",)
        collector.minute = datetime(2026, 9, 9, 9, 0)
        collector.minute_codes = {"A"}
        collector.segment_baselines = {"A": 0.0}
        collector.prepare(("B",), datetime(2026, 9, 9, 9, 0, 10), enabled=True, collection_open=True)
        self.values.update(A=3.0, B=10.0)

        changed = collector.advance(
            datetime(2026, 9, 9, 9, 0, 30), enabled=True, collection_open=True,
            value_provider=self.value, market_provider=self.market,
        )
        self.values["B"] = 14.0
        continued = collector.advance(
            datetime(2026, 9, 9, 9, 0, 40), enabled=True, collection_open=True,
            value_provider=self.value, market_provider=self.market,
        )

        self.assertTrue(changed.cohort_changed)
        self.assertEqual((3.0, 0.0, 0.0), changed.live_values)
        self.assertEqual((3.0, 4.0, 0.0), continued.live_values)
        self.assertEqual({"A", "B"}, collector.minute_codes)

    def test_realtime_codes_keep_active_and_pending_cohorts(self) -> None:
        collector = Top20TradeValueCollector()
        collector.active_codes = ("A",)
        collector.next_codes = ("B",)
        self.assertEqual(("VISIBLE", "A", "B"), collector.realtime_codes(("VISIBLE",), enabled=True))
        self.assertEqual(("VISIBLE",), collector.realtime_codes(("VISIBLE",), enabled=False))

    def test_partial_record_uses_current_segments(self) -> None:
        collector = Top20TradeValueCollector()
        collector.active_codes = ("A",)
        collector.minute = datetime(2026, 9, 9, 9, 0)
        collector.minute_codes = {"A"}
        collector.segment_baselines = {"A": 1.0}
        collector.samples = [(20, 2.0)]
        self.values["A"] = 3.0

        record = collector.partial_record(self.value, self.market)

        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual((2.0, 0.0, 0.0), record.market_values)
        self.assertEqual("partial", record.capture_state)

    def test_disabling_collection_clears_pending_and_accumulated_state(self) -> None:
        collector = Top20TradeValueCollector()
        collector.active_codes = ("A",)
        collector.next_codes = ("B",)
        collector.next_activation = datetime(2026, 9, 9, 9, 0, 30)
        collector.segment_accumulated = (3.0, 2.0, 1.0)
        collector.minute_codes = {"A"}
        collector.samples = [(20, 6.0)]

        update = collector.advance(
            datetime(2026, 9, 9, 9, 0, 25), enabled=False, collection_open=True,
            value_provider=self.value, market_provider=self.market,
        )

        self.assertIsNone(update.completed)
        self.assertIsNone(update.live_values)
        self.assertEqual((), collector.active_codes)
        self.assertEqual((), collector.next_codes)
        self.assertEqual((0.0, 0.0, 0.0), collector.segment_accumulated)
        self.assertEqual(set(), collector.minute_codes)
        self.assertEqual([], collector.samples)


if __name__ == "__main__":
    unittest.main()
