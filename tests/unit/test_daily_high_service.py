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
        self.assertEqual(125, targets.high_250_price)
        self.assertEqual("ka10081", client.api_id)
        self.assertEqual("/api/dostk/chart", client.path)

    def test_prefers_direct_daily_trade_value_from_ka10081(self) -> None:
        class DirectValueClient:
            def request(self, api_id: str, path: str, body: dict[str, object]) -> dict[str, object]:
                return {
                    "stk_dt_pole_chart_qry": [
                        {"dt": "20260822", "high_pric": "100", "trde_prica": "98765432100", "cur_prc": "100"},
                        {"dt": "20260821", "high_pric": "90", "trde_prica": "12345678900", "cur_prc": "80"},
                    ]
                }

        targets = DailyHighService(DirectValueClient()).load("005930")
        self.assertAlmostEqual(987_654_321, targets.previous_day_trade_value_eok or 0)
        self.assertEqual(100, targets.previous_day_close_price)

    def test_uses_latest_completed_bar_when_today_bar_is_absent(self) -> None:
        from datetime import date
        from kiwoom_monitor.application.daily_high_service import DailyBar, DailyHighTargets

        targets = DailyHighTargets.from_daily_bars(
            (
                DailyBar("20260821", 100, 10.0, 80),
                DailyBar("20260820", 90, 9.0, 70),
            ),
            as_of=date(2026, 8, 23),
        )
        self.assertEqual(10.0, targets.previous_day_trade_value_eok)
        self.assertEqual(80, targets.previous_day_close_price)

    def test_sums_krx_and_nxt_only_for_previous_day_trade_value(self) -> None:
        class CombinedMarketClient:
            def request(self, api_id: str, path: str, body: dict[str, object]) -> dict[str, object]:
                code = str(body["stk_cd"])
                value = "2000" if code.endswith("_NX") else "3000"
                high = "200" if code.endswith("_NX") else "100"
                return {
                    "stk_dt_pole_chart_qry": [
                        {"dt": "20260822", "high_pric": high, "trde_prica": value, "cur_prc": "90"}
                    ]
                }

        targets = DailyHighService(CombinedMarketClient(), include_nxt=True).load("005930")
        self.assertEqual(200, targets.high_5_price)  # 신고가도 KRX+NXT 최고가 기준
        self.assertEqual(50.0, targets.previous_day_trade_value_eok)  # 30억 + 20억
