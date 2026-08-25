from __future__ import annotations

import unittest

from kiwoom_monitor.application.ranking_service import RankingService


class FakeClient:
    def request(self, api_id: str, path: str, body: dict[str, object]) -> dict[str, object]:
        if api_id == "ka00198":
            return {
                "item_inq_rank": [
                    {"bigd_rank": "1", "stk_cd": "005930", "stk_nm": "삼성전자", "base_comp_chgr": "1.25", "past_curr_prc": "+72000"},
                    {"bigd_rank": "2", "stk_cd": "000660", "stk_nm": "SK하이닉스", "base_comp_chgr": "-0.50", "cur_prc": "-260000"},
                ]
            }
        period = body["dt"]
        codes = {"5": ["005930"], "20": ["005930", "000660"], "250": ["000660"]}[str(period)]
        return {"ntl_pric": [{"stk_cd": code} for code in codes]}


class RankingServiceTests(unittest.TestCase):
    def test_combines_rankings_with_new_high_periods(self) -> None:
        service = RankingService(FakeClient())
        service.refresh_new_highs()
        stocks = service.load_top_stocks()

        self.assertEqual(2, len(stocks))
        self.assertEqual(frozenset({5, 20}), stocks[0].new_high_periods)
        self.assertEqual("5일, 20일", stocks[0].new_high_label)
        self.assertEqual(frozenset({20, 250}), stocks[1].new_high_periods)
        self.assertEqual(72_000, stocks[0].current_price)
        self.assertEqual(260_000, stocks[1].current_price)
