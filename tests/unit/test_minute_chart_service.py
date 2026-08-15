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
