from __future__ import annotations

import unittest
from datetime import datetime

from kiwoom_monitor.application.minute_chart_service import MinuteChartService


class FakeClient:
    def request(self, api_id: str, path: str, body: dict[str, object]) -> dict[str, object]:
        return {
            "stk_min_pole_chart_qry": [
                {"cntr_tm": "20260814100200", "open_pric": "-110", "high_pric": "120", "low_pric": "100", "cur_prc": "115", "trde_qty": "20"},
                {"cntr_tm": "20260814100100", "open_pric": "100", "high_pric": "110", "low_pric": "90", "cur_prc": "105", "trde_qty": "10"},
            ]
        }


class MinuteChartServiceTests(unittest.TestCase):
    def test_converts_and_sorts_minute_chart_rows(self) -> None:
        bars = MinuteChartService(FakeClient()).load_today("005930", datetime(2026, 8, 14))

        self.assertEqual(2, len(bars))
        self.assertEqual(1, bars[0].minute.minute)
        self.assertEqual(10, bars[0].minute.hour)
        self.assertEqual(110, bars[1].open_price)

    def test_loads_a_second_page_when_ka10080_has_more_rows(self) -> None:
        class ContinuationClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str]] = []

            def request_with_continuation(self, _api_id: str, _path: str, _body: dict[str, object], *, cont_yn: str, next_key: str):
                self.calls.append((cont_yn, next_key))
                if cont_yn == "N":
                    return {"stk_min_pole_chart_qry": [{"cntr_tm": "20260814100100", "open_pric": "100", "high_pric": "100", "low_pric": "100", "cur_prc": "100", "trde_qty": "1"}]}, True, "next"
                return {"stk_min_pole_chart_qry": [{"cntr_tm": "20260814100000", "open_pric": "90", "high_pric": "90", "low_pric": "90", "cur_prc": "90", "trde_qty": "1"}]}, False, ""

        client = ContinuationClient()
        bars = MinuteChartService(client).load_today("005930", datetime(2026, 8, 14))
        self.assertEqual([("N", ""), ("Y", "next")], client.calls)
        self.assertEqual(2, len(bars))

    def test_sums_krx_and_nxt_trade_value_for_same_minute(self) -> None:
        class MarketClient:
            def request(self, _api_id: str, _path: str, body: dict[str, object]) -> dict[str, object]:
                is_nxt = str(body["stk_cd"]).endswith("_NX")
                return {
                    "stk_min_pole_chart_qry": [
                        {
                            "cntr_tm": "20260814100100",
                            "open_pric": "200" if is_nxt else "100",
                            "high_pric": "220" if is_nxt else "120",
                            "low_pric": "190" if is_nxt else "90",
                            "cur_prc": "210" if is_nxt else "110",
                            "trde_qty": "20" if is_nxt else "10",
                        }
                    ]
                }

        bars = MinuteChartService(MarketClient(), include_nxt=True).load_today("005930", datetime(2026, 8, 14))
        self.assertEqual(1, len(bars))
        self.assertEqual(30, bars[0].volume)
        self.assertAlmostEqual(0.0000515, bars[0].trade_value_eok)
