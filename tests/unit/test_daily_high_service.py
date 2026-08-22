from __future__ import annotations

import unittest

from kiwoom_monitor.application.daily_high_service import DailyHighService


class FakeClient:
    def request(self, api_id: str, path: str, body: dict[str, object]) -> dict[str, object]:
        self.api_id = api_id
        self.path = path
        self.body = body
        rows = [{"dt": f"202608{day:02d}", "high_pric": str(100 + day)} for day in range(1, 26)]
        return {"stk_dt_pole_chart_qry": rows}


class DailyHighServiceTests(unittest.TestCase):
    def test_calculates_five_and_twenty_day_highs_from_latest_daily_bars(self) -> None:
        client = FakeClient()
        targets = DailyHighService(client).load("005930")
        self.assertEqual(125, targets.high_5_price)
        self.assertEqual(125, targets.high_20_price)
        self.assertEqual("ka10081", client.api_id)
        self.assertEqual("/api/dostk/chart", client.path)

    def test_prefers_direct_daily_trade_value_from_ka10081(self) -> None:
        class DirectValueClient:
            def request(self, api_id: str, path: str, body: dict[str, object]) -> dict[str, object]:
                return {
                    "stk_dt_pole_chart_qry": [
                        {"dt": "20260822", "high_pric": "100", "trde_prica": "98765432100"},
                        {"dt": "20260821", "high_pric": "90", "trde_prica": "12345678900"},
                    ]
                }

        targets = DailyHighService(DirectValueClient()).load("005930")
        self.assertAlmostEqual(123.456789, targets.previous_day_trade_value_eok or 0)
