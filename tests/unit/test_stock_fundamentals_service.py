from __future__ import annotations
import unittest
from kiwoom_monitor.application.stock_fundamentals_service import StockFundamentalsService

class FakeClient:
    def request(self, api_id: str, path: str, body: dict[str, object]) -> dict[str, object]:
        return {"mac": "1,000", "dstr_rt": "40.5", "dstr_stk": "1,234", "250hgst": "72,000", "upl_pric": "91,900"}

class StockFundamentalsServiceTests(unittest.TestCase):
    def test_uses_stored_nas_fundamentals_without_tr(self) -> None:
        class StoredClient(FakeClient):
            def __init__(self): self.calls = 0
            def load_stored_fundamentals(self, code):
                return {"mac": "2,000", "dstr_rt": "50"}
            def request(self, api_id, path, body):
                self.calls += 1
                return super().request(api_id, path, body)

        client = StoredClient()
        value = StockFundamentalsService(client).load("005930")
        self.assertEqual(0, client.calls)
        self.assertEqual(2000, value.market_cap_eok)

    def test_loads_market_cap_and_float_ratio(self) -> None:
        value = StockFundamentalsService(FakeClient()).load("005930")
        self.assertEqual(1000, value.market_cap_eok)
        self.assertEqual(40.5, value.float_ratio_percent)
        self.assertEqual(72000, value.high_250_price)
        self.assertEqual(1_234_000, value.float_shares)
        self.assertEqual(91_900, value.upper_limit_price)
