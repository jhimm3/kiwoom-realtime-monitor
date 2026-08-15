from __future__ import annotations
import unittest
from kiwoom_monitor.application.stock_fundamentals_service import StockFundamentalsService

class FakeClient:
    def request(self, api_id: str, path: str, body: dict[str, object]) -> dict[str, object]:
        return {"mac": "1,000", "dstr_rt": "40.5", "250hgst": "72,000"}

class StockFundamentalsServiceTests(unittest.TestCase):
    def test_loads_market_cap_and_float_ratio(self) -> None:
        value = StockFundamentalsService(FakeClient()).load("005930")
        self.assertEqual(1000, value.market_cap_eok)
        self.assertEqual(40.5, value.float_ratio_percent)
        self.assertEqual(72000, value.high_250_price)
