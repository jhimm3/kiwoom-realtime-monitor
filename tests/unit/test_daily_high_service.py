from __future__ import annotations

import unittest

from kiwoom_monitor.application.daily_high_service import DailyHighService


class FakeClient:
    def request(self, api_id: str, path: str, body: dict[str, object]) -> dict[str, object]:
        rows = [{"date": f"202608{day:02d}", "high_pric": str(100 + day)} for day in range(1, 26)]
        return {"stk_ddwkmm": rows}


class DailyHighServiceTests(unittest.TestCase):
    def test_calculates_five_and_twenty_day_highs_from_latest_daily_bars(self) -> None:
        targets = DailyHighService(FakeClient()).load("005930")
        self.assertEqual(125, targets.high_5_price)
        self.assertEqual(125, targets.high_20_price)

