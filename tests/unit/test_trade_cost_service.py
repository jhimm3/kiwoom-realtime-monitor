from __future__ import annotations

import unittest
from datetime import date

from datetime import datetime

from kiwoom_monitor.application.trade_cost_service import DailyTradeCost, TradeCostService, allocate_episode_cost, estimate_episode_cost
from kiwoom_monitor.application.trade_history_service import TradeFill
from kiwoom_monitor.application.trade_journal_summary import group_trade_episodes


class FakeClient:
    def request_with_continuation(self, api_id, path, body, *, cont_yn="N", next_key=""):
        return {"trst_ovrl_trde_prps_array": [
            {"trde_dt": "20260828", "cntr_dt": "20260826", "stk_cd": "A005930", "io_tp_nm": "매수",
             "trde_amt": "100,000", "exct_amt": "100,010", "cmsn": "10", "trde_agri_tax": "0", "tax_sum_cmsn": "10"},
            {"trde_dt": "20260828", "cntr_dt": "20260826", "stk_cd": "A005930", "io_tp_nm": "매수",
             "trde_amt": "200,000", "exct_amt": "200,020", "cmsn": "20", "trde_agri_tax": "0", "tax_sum_cmsn": "20"},
            {"trde_dt": "20260828", "cntr_dt": "20260825", "stk_cd": "A000660", "io_tp_nm": "매도",
             "trde_amt": "50,000", "exct_amt": "49,900", "cmsn": "10", "trde_agri_tax": "90", "tax_sum_cmsn": "100"},
        ]}, False, ""


class AlphaCodeClient:
    def request_with_continuation(self, api_id, path, body, *, cont_yn="N", next_key=""):
        return {"trst_ovrl_trde_prps_array": [
            {"trde_dt": "20260810", "cntr_dt": "20260806", "stk_cd": "A0039P0", "io_tp_nm": "매도",
             "trde_amt": "719590", "exct_amt": "718053", "cmsn": "100", "trde_agri_tax": "1437", "tax_sum_cmsn": "1537"},
        ]}, False, ""


class TradeCostServiceTests(unittest.TestCase):
    def test_preserves_letters_inside_new_stock_codes(self) -> None:
        values = TradeCostService(AlphaCodeClient()).load_period(date(2026, 8, 6), date(2026, 8, 6), date(2026, 8, 30))
        self.assertEqual("0039P0", values[0].stock_code)

    def test_groups_actual_cost_by_fill_date_stock_and_side(self) -> None:
        values = TradeCostService(FakeClient()).load_period(date(2026, 8, 26), date(2026, 8, 26), date(2026, 8, 29))
        self.assertEqual(1, len(values))
        value = values[0]
        self.assertEqual(date(2026, 8, 26), value.fill_date)
        self.assertEqual(300_000, value.gross_amount)
        self.assertEqual(30, value.commission)
        self.assertEqual(30, value.total_cost)

    def test_same_day_group_uses_complete_actual_daily_cost_without_allocation(self) -> None:
        fills = (
            TradeFill("1", "005930", "삼성전자", "매수", datetime(2026, 8, 26, 9), 10, 100),
            TradeFill("2", "005930", "삼성전자", "매도", datetime(2026, 8, 26, 10), 10, 110),
            TradeFill("3", "005930", "삼성전자", "매수", datetime(2026, 8, 26, 11), 10, 100),
            TradeFill("4", "005930", "삼성전자", "매도", datetime(2026, 8, 26, 12), 10, 120),
        )
        episodes = group_trade_episodes(fills)
        costs = (
            DailyTradeCost(date(2026, 8, 26), date(2026, 8, 28), "005930", "매수", 2_000, 2_010, 10, 0, 10),
            DailyTradeCost(date(2026, 8, 26), date(2026, 8, 28), "005930", "매도", 2_300, 2_280, 10, 10, 20),
        )
        self.assertEqual(1, len(episodes))
        result = allocate_episode_cost(episodes[0], fills, costs)
        self.assertTrue(result.complete)
        self.assertFalse(result.allocated)
        self.assertEqual(30, result.total_cost)
        self.assertEqual(270, result.net_realized_profit)

    def test_estimates_unsettled_round_trip_cost_from_sell_amount(self) -> None:
        fills = (
            TradeFill("1", "005930", "삼성전자", "매수", datetime(2026, 8, 26, 9), 10, 100_000),
            TradeFill("2", "005930", "삼성전자", "매도", datetime(2026, 8, 26, 10), 10, 110_000),
        )
        episode = group_trade_episodes(fills)[0]
        self.assertEqual(2_515, estimate_episode_cost(episode, 0.015, 0.215))


if __name__ == "__main__":
    unittest.main()
