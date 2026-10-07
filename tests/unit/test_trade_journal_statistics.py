from __future__ import annotations

import unittest
from datetime import datetime

from kiwoom_monitor.application.trade_history_service import TradeFill
from kiwoom_monitor.application.trade_journal_statistics import summarize_periods
from kiwoom_monitor.application.trade_journal_summary import group_trade_episodes


class TradeJournalStatisticsTests(unittest.TestCase):
    def test_summarizes_daily_weekly_and_monthly_results(self) -> None:
        fills = (
            TradeFill("1", "A", "수익", "매수", datetime(2026, 8, 24, 9), 1, 100),
            TradeFill("2", "A", "수익", "매도", datetime(2026, 8, 24, 10), 1, 110),
            TradeFill("3", "B", "손실", "매수", datetime(2026, 8, 25, 9), 1, 100),
            TradeFill("4", "B", "손실", "매도", datetime(2026, 8, 25, 10), 1, 95),
        )
        episodes = group_trade_episodes(fills)
        daily = summarize_periods(episodes, "일간")
        weekly = summarize_periods(episodes, "주간")
        monthly = summarize_periods(episodes, "월간")
        self.assertEqual(2, len(daily))
        self.assertEqual(1, len(weekly))
        self.assertEqual(1, weekly[0].win_count)
        self.assertEqual(1, weekly[0].loss_count)
        self.assertEqual(5, monthly[0].realized_profit)


if __name__ == "__main__":
    unittest.main()
