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
    def test_batch_cost_allocation_preserves_scopes_rounding_and_missing_costs(self) -> None:
        from dataclasses import replace
        from datetime import timedelta
        from kiwoom_monitor.application.trade_cost_service import allocate_episode_costs
        from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope, LEGACY_ACCOUNT_SCOPE

        origin = AccountScope("kiwoom", AccountEnvironment.REAL, "11111111-1111-4111-8111-111111111111")
        canonical = AccountScope("kiwoom", AccountEnvironment.REAL, "22222222-2222-4222-8222-222222222222")
        other = AccountScope("kiwoom", AccountEnvironment.REAL, "33333333-3333-4333-8333-333333333333")
        at = datetime(2026, 9, 10, 9)

        def fill(order, amount, code="005930", side="매수", moment=at, scope=origin, mapped=canonical):
            return TradeFill(order, code, "fixture", side, moment, 1, amount, origin_scope=scope, canonical_scope=mapped)

        first, second = fill("first", 1), fill("second", 3)
        other_buy = fill("other", 1, scope=other, mapped=None)
        legacy = fill("legacy", 1, scope=LEGACY_ACCOUNT_SCOPE, mapped=None)
        previous = fill("previous", 1, moment=at - timedelta(days=1))
        other_sell = fill("sell", 1, side="매도", scope=other, mapped=None)
        missing, zero, outside = fill("missing", 1, "MISS00"), fill("zero", 0, "ZERO00"), fill("outside", 4, "OUT000")
        all_fills = (first, second, other_buy, legacy, previous, other_sell, missing, zero)
        episodes = tuple(group_trade_episodes((value,))[0] for value in (*all_fills, outside))
        partial = replace(episodes[0], group_id="partial", fills=(first, missing))
        empty = replace(episodes[0], group_id="empty", fills=())
        episodes += (partial, empty)

        def cost(value, total, settlement_offset=2):
            return DailyTradeCost(value.filled_at.date(), value.filled_at.date() + timedelta(days=settlement_offset),
                value.stock_code, value.side, value.price, value.price - total, total, 0, total,
                value.origin_scope, value.canonical_scope)

        # The same effective key uses the last row, irrespective of settlement date.
        costs = (cost(first, 99), cost(other_buy, 9), cost(legacy, 7), cost(previous, 5),
                 cost(other_sell, 11), cost(zero, 13), cost(outside, 17), cost(first, 2, 3))
        # 2 * 1/4 and 2 * 3/4 keep Python's existing half-even results 0 and 2.
        expected = ((0, None, True, True), (2, None, True, True), (9, None, True, False),
            (7, None, True, False), (5, None, True, False), (11, None, True, False),
            (0, None, False, False), (0, None, True, False), (17, None, True, False),
            (0, None, False, True), (0, None, False, False))

        def values(results):
            return tuple((row.total_cost, row.net_realized_profit, row.complete, row.allocated) for row in results)

        self.assertEqual(expected, values(allocate_episode_costs(episodes, all_fills, costs)))
        self.assertEqual(expected, values(tuple(allocate_episode_cost(episode, all_fills, costs) for episode in episodes)))
        self.assertEqual(expected, values(allocate_episode_costs(episodes, iter(all_fills), iter(costs))))
        self.assertEqual(expected, values(tuple(allocate_episode_cost(episode, iter(all_fills), iter(costs)) for episode in episodes)))
        fill_stream, cost_stream = iter(all_fills), iter(costs)
        self.assertEqual((expected[0], (0, None, False, False)), values((
            allocate_episode_cost(episodes[0], fill_stream, cost_stream),
            allocate_episode_cost(episodes[1], fill_stream, cost_stream))))
        self.assertEqual(tuple(reversed(expected)), values(allocate_episode_costs(tuple(reversed(episodes)), all_fills, costs)))
        self.assertEqual((), allocate_episode_costs((), iter(()), iter(())))

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
