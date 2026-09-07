from __future__ import annotations

import unittest
from datetime import datetime

from kiwoom_monitor.application.trade_history_service import TradeFill
from kiwoom_monitor.application.trade_journal_summary import group_trade_episodes, summarize_trade_fills, trade_fill_key


class TradeJournalSummaryTests(unittest.TestCase):
    def test_fifo_realized_profit_and_open_quantity(self) -> None:
        fills = (
            TradeFill("1", "005930", "삼성전자", "매수", datetime(2026, 8, 28, 9, 1), 10, 100),
            TradeFill("2", "005930", "삼성전자", "매수", datetime(2026, 8, 28, 9, 2), 10, 120),
            TradeFill("3", "005930", "삼성전자", "매도", datetime(2026, 8, 28, 10, 0), 15, 130),
        )
        summary = summarize_trade_fills(fills)[0]
        self.assertEqual(5, summary.open_quantity)
        self.assertEqual(350, summary.realized_profit)
        self.assertEqual(1_600, summary.matched_cost)
        self.assertAlmostEqual(21.875, summary.return_rate)

    def test_groups_by_day_and_stock(self) -> None:
        fills = (
            TradeFill("1", "005930", "삼성전자", "매수", datetime(2026, 8, 28, 9), 1, 100),
            TradeFill("2", "000660", "SK하이닉스", "매수", datetime(2026, 8, 28, 9), 1, 200),
            TradeFill("3", "005930", "삼성전자", "매도", datetime(2026, 8, 29, 9), 1, 110),
        )
        self.assertEqual(3, len(summarize_trade_fills(fills)))

    def test_same_day_same_stock_cycles_are_grouped_by_default(self) -> None:
        fills = (
            TradeFill("1", "005930", "삼성전자", "매수", datetime(2026, 8, 28, 9), 10, 100),
            TradeFill("2", "005930", "삼성전자", "매도", datetime(2026, 8, 28, 10), 10, 110),
            TradeFill("3", "005930", "삼성전자", "매수", datetime(2026, 8, 28, 11), 5, 120),
        )
        episodes = group_trade_episodes(fills)
        self.assertEqual(1, len(episodes))
        self.assertEqual(("1", "2", "3"), tuple(fill.order_no for fill in episodes[0].fills))

    def test_different_start_days_remain_separate(self) -> None:
        fills = (
            TradeFill("1", "005930", "삼성전자", "매수", datetime(2026, 8, 27, 9), 1, 100),
            TradeFill("2", "005930", "삼성전자", "매도", datetime(2026, 8, 27, 10), 1, 110),
            TradeFill("3", "005930", "삼성전자", "매수", datetime(2026, 8, 28, 9), 1, 120),
        )
        self.assertEqual(2, len(group_trade_episodes(fills)))

    def test_manual_override_merges_auto_episodes(self) -> None:
        fills = (
            TradeFill("1", "005930", "삼성전자", "매수", datetime(2026, 8, 28, 9), 1, 100),
            TradeFill("2", "005930", "삼성전자", "매도", datetime(2026, 8, 28, 10), 1, 110),
            TradeFill("3", "005930", "삼성전자", "매수", datetime(2026, 8, 28, 11), 1, 120),
        )
        overrides = {trade_fill_key(fill): "manual:test" for fill in fills}
        episodes = group_trade_episodes(fills, overrides)
        self.assertEqual(1, len(episodes))
        self.assertEqual("manual", episodes[0].source)

    def test_auto_group_id_stays_stable_when_older_episode_is_added(self) -> None:
        recent = (
            TradeFill("3", "005930", "삼성전자", "매수", datetime(2026, 8, 28, 11), 1, 120),
            TradeFill("4", "005930", "삼성전자", "매도", datetime(2026, 8, 28, 12), 1, 130),
        )
        older = (
            TradeFill("1", "005930", "삼성전자", "매수", datetime(2026, 8, 27, 9), 1, 100),
            TradeFill("2", "005930", "삼성전자", "매도", datetime(2026, 8, 27, 10), 1, 110),
        )
        recent_id = group_trade_episodes(recent)[0].group_id
        expanded = group_trade_episodes(older + recent)
        matching = next(episode for episode in expanded if episode.started_at == recent[0].filled_at)
        self.assertEqual(recent_id, matching.group_id)


if __name__ == "__main__":
    unittest.main()
