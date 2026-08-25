from __future__ import annotations

import unittest

from kiwoom_monitor.application.trade_strength import StockFundamentals, trade_strength_percent
from kiwoom_monitor.domain.strength_level import strength_badge


class TradeStrengthTests(unittest.TestCase):
    def test_uses_floating_market_cap(self) -> None:
        fundamentals = StockFundamentals(market_cap_eok=1_000, float_ratio_percent=40)
        self.assertEqual(400, fundamentals.float_market_cap_eok)
        self.assertEqual(2.5, trade_strength_percent(10, fundamentals))

    def test_returns_none_without_floating_market_cap(self) -> None:
        self.assertIsNone(trade_strength_percent(10, StockFundamentals(100, 0)))

    def test_prefers_verified_float_shares_and_current_price(self) -> None:
        fundamentals = StockFundamentals(1_000, 40, float_shares=200_000_000)
        self.assertEqual(2.5, trade_strength_percent(10, fundamentals, current_price=200))

    def test_uses_realtime_market_cap_before_rest_cache_when_shares_are_missing(self) -> None:
        fundamentals = StockFundamentals(1_000, 40)
        self.assertEqual(1.25, trade_strength_percent(10, fundamentals, market_cap_eok=2_000))

    def test_marks_interest_caution_and_fire_levels(self) -> None:
        self.assertEqual("0.50% 👀", strength_badge(0.5))
        self.assertEqual("1.00% ⚠️", strength_badge(1.0))
        self.assertEqual("2.00% 🔥", strength_badge(2.0))

    def test_uses_configured_strength_icons(self) -> None:
        self.assertEqual("0.50% A", strength_badge(0.5, icons=("A", "B", "C")))
        self.assertEqual("1.00% B", strength_badge(1.0, icons=("A", "B", "C")))
        self.assertEqual("2.00% C", strength_badge(2.0, icons=("A", "B", "C")))
