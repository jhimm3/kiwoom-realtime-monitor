from __future__ import annotations

import unittest
from datetime import datetime

from kiwoom_monitor.application.trade_chart import (
    DailyTradeChartService,
    aggregate_chart_rows,
    daily_chart_display_target,
    should_reuse_cached_daily_chart,
)


class TradeChartTests(unittest.TestCase):
    def test_daily_chart_display_target_keeps_existing_routing(self) -> None:
        context = {"selected_history_code": "A", "live_code": "B"}

        self.assertEqual("history", daily_chart_display_target("history", "A", **context))
        self.assertEqual("live", daily_chart_display_target("live", "B", **context))
        self.assertEqual("detached", daily_chart_display_target("detached:C", "C", **context))
        self.assertEqual("", daily_chart_display_target("history", "B", **context))
        self.assertEqual("", daily_chart_display_target("live", "A", **context))

    def test_only_complete_past_history_or_detached_cache_skips_api(self) -> None:
        today = datetime(2026, 9, 10).date()

        self.assertTrue(should_reuse_cached_daily_chart("history", datetime(2026, 9, 9).date(), 250, today=today))
        self.assertTrue(should_reuse_cached_daily_chart("detached:A", datetime(2026, 9, 9).date(), 250, today=today))
        self.assertFalse(should_reuse_cached_daily_chart("live", datetime(2026, 9, 9).date(), 250, today=today))
        self.assertFalse(should_reuse_cached_daily_chart("history", today, 250, today=today))
        self.assertFalse(should_reuse_cached_daily_chart("history", datetime(2026, 9, 9).date(), 249, today=today))

    def test_loads_recent_daily_ohlcv_rows_for_chart(self) -> None:
        class Client:
            def request(self, api_id, path, body):
                self.call = (api_id, path, body)
                return {"stk_dt_pole_chart_qry": [{
                    "dt": "20260828", "open_pric": "-100", "high_pric": "110", "low_pric": "95",
                    "cur_prc": "105", "trde_qty": "200", "trde_prica": "350",
                }]}
        client = Client()
        rows = DailyTradeChartService(client).load("005930", datetime(2026, 8, 28))
        self.assertEqual((100, 110, 95, 105, 200, 3.5), rows[0][1:7])
        self.assertEqual("20260828", client.call[2]["base_dt"])

    def test_aggregates_ohlcv_and_trade_value_into_five_minute_bars(self) -> None:
        rows = (
            ("2026-08-28T09:01", 100, 105, 99, 103, 10, 1.5, "after_close_confirmed"),
            ("2026-08-28T09:04", 103, 108, 101, 107, 20, 2.5, "after_close_confirmed"),
            ("2026-08-28T09:05", 107, 109, 106, 108, 30, 3.0, "after_close_confirmed"),
        )
        values = aggregate_chart_rows(rows, "5분")
        self.assertEqual(2, len(values))
        self.assertEqual((100, 108.0, 99.0, 107), values[0][1:5])
        self.assertEqual(30, values[0][5])
        self.assertEqual(4.0, values[0][6])

    def test_daily_bar_keeps_full_day_open_high_low_close(self) -> None:
        rows = (
            ("2026-08-28T08:00", 100, 105, 99, 103, 10, 1.0, "after_close_confirmed"),
            ("2026-08-28T19:59", 103, 110, 98, 108, 20, 2.0, "after_close_confirmed"),
        )
        value = aggregate_chart_rows(rows, "일봉")[0]
        self.assertEqual((100, 110.0, 98.0, 108, 30, 3.0), value[1:7])

    def test_aggregation_keeps_missing_trade_value_unavailable(self) -> None:
        rows = (
            ("2026-08-28T09:00", 100, 101, 99, 100, 10, None, "market_index_gap"),
            ("2026-08-28T09:01", 100, 102, 100, 101, 20, None, "market_index_gap"),
        )
        self.assertIsNone(aggregate_chart_rows(rows, "5분")[0][6])


if __name__ == "__main__":
    unittest.main()
