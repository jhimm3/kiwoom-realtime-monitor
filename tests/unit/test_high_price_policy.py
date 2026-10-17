from __future__ import annotations

import unittest

from kiwoom_monitor.application.daily_high_service import DailyHighTargets
from kiwoom_monitor.application.high_price_policy import (
    merge_fundamentals_with_adjusted_high,
    selected_high_price,
)
from kiwoom_monitor.application.trade_strength import StockFundamentals


class HighPricePolicyTests(unittest.TestCase):
    def test_cached_display_and_intraday_overlay_do_not_certify_period(self):
        cached = DailyHighTargets(110, 120, 130)
        for period in ("5", "20", "250"):
            self.assertEqual(1000, selected_high_price(period, daily=cached,
                              fundamentals=StockFundamentals(100, 50, 999), historical_high=999, today_high=1000))
            self.assertFalse(cached.period_verified(period))
        self.assertFalse(cached.collection_verified)

    def test_fundamentals_refresh_keeps_cached_adjusted_high(self) -> None:
        value = merge_fundamentals_with_adjusted_high(
            StockFundamentals(1_100, 45, 2_987_000),
            existing=StockFundamentals(1_000, 50, 3_002_000),
            daily=DailyHighTargets(None, None, None),
        )
        self.assertEqual(3_002_000, value.high_250_price)
        self.assertEqual(1_100, value.market_cap_eok)

    def test_current_daily_adjusted_high_replaces_stale_cache(self) -> None:
        value = merge_fundamentals_with_adjusted_high(
            StockFundamentals(1_100, 45, 2_987_000),
            existing=StockFundamentals(1_000, 50, 3_100_000),
            daily=DailyHighTargets(None, None, 3_002_000, period_statuses=(("250", "ready"),)),
        )
        self.assertEqual(3_002_000, value.high_250_price)

    def test_250_day_displays_cache_until_daily_refresh_replaces_it(self) -> None:
        fundamentals = StockFundamentals(1_000, 50, 2_987_000)
        self.assertEqual(
            3_002_000,
            selected_high_price(
                "250", daily=DailyHighTargets(None, None, 3_002_000, period_statuses=(("250", "ready"),)),
                fundamentals=fundamentals, historical_high=None, today_high=None,
            ),
        )
        self.assertEqual(
            2_987_000,
            selected_high_price(
                "250", daily=DailyHighTargets(None, None, None),
                fundamentals=fundamentals, historical_high=None, today_high=None,
            ),
        )

    def test_new_lower_daily_and_historical_values_replace_higher_saved_values(self):
        daily = DailyHighTargets(None, None, 200, period_statuses=(("250", "ready"),))
        fundamentals = StockFundamentals(1000, 50, 999)
        self.assertEqual(200, selected_high_price("250",daily=daily,fundamentals=fundamentals,
            historical_high=None,today_high=None))
        self.assertEqual(250, selected_high_price("historical",daily=daily,fundamentals=fundamentals,
            historical_high=250,today_high=None))

    def test_historical_and_intraday_values_are_lower_bounds(self) -> None:
        self.assertEqual(
            3_007_000,
            selected_high_price(
                "historical", daily=DailyHighTargets(None, None, 3_002_000, period_statuses=(("250", "ready"),)),
                fundamentals=StockFundamentals(1_000, 50, 2_987_000),
                historical_high=3_001_000, today_high=3_007_000,
            ),
        )

    def test_short_period_uses_corresponding_daily_high(self) -> None:
        daily = DailyHighTargets(110, 120, 130, period_statuses=(("5", "ready"), ("20", "provisional"), ("250", "ready")))
        self.assertEqual(
            110,
            selected_high_price(
                "5", daily=daily, fundamentals=None, historical_high=None, today_high=None,
            ),
        )
        self.assertEqual(
            125,
            selected_high_price(
                "20", daily=daily, fundamentals=None, historical_high=None, today_high=125,
            ),
        )


if __name__ == "__main__":
    unittest.main()
