from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta

from kiwoom_monitor.application.market_data_finalization import (
    FinalizationScope,
    evaluate_finalization_outcome,
    finalization_candidates,
    finalization_target_date,
    minute_bars_complete,
)


class MarketDataFinalizationTests(unittest.TestCase):
    def test_premarket_uses_latest_saved_trade_date_or_previous_business_day(self) -> None:
        monday = datetime(2026, 9, 7, 7, 54)
        self.assertEqual(date(2026, 9, 3), finalization_target_date(monday, date(2026, 9, 3)))
        self.assertEqual(date(2026, 9, 4), finalization_target_date(monday, None))
        self.assertIsNone(finalization_target_date(datetime(2026, 9, 6, 12), date(2026, 9, 4)))

    def test_close_threshold_differs_for_krx_and_nxt(self) -> None:
        codes = ("KRX", "NXT")
        now = datetime(2026, 9, 8, 15, 35)
        selected = finalization_candidates(codes, now, now.date(), set(), {"KRX": False, "NXT": True}, {}, {})
        self.assertEqual(("KRX",), selected)
        selected = finalization_candidates(codes, now.replace(hour=20, minute=5), now.date(), set(), {"KRX": False, "NXT": True}, {}, {})
        self.assertEqual(codes, selected)

    def test_effective_date_full_day_waits_until_2005_for_every_krx_stock(self) -> None:
        target = date(2026, 9, 14)
        codes = ("KRX_ONLY", "NXT")
        self.assertEqual(
            (),
            finalization_candidates(
                codes, datetime(2026, 9, 14, 15, 35), target, set(),
                {"KRX_ONLY": False, "NXT": True}, {}, {},
            ),
        )
        self.assertEqual(
            codes,
            finalization_candidates(
                codes, datetime(2026, 9, 14, 20, 5), target, set(),
                {"KRX_ONLY": False, "NXT": True}, {}, {},
            ),
        )

    def test_regular_scope_remains_available_at_1535(self) -> None:
        target = date(2026, 9, 14)
        self.assertEqual(
            ("005930",),
            finalization_candidates(
                ("005930",), datetime(2026, 9, 14, 15, 35), target,
                set(), {}, {}, {}, session_scope=FinalizationScope.REGULAR,
            ),
        )

    def test_retry_limit_and_delay_are_enforced(self) -> None:
        now = datetime(2026, 9, 8, 20, 5)
        target = now.date()
        attempts = {(target, "WAIT"): 1, (target, "DONE"): 2}
        waits = {(target, "WAIT"): now + timedelta(minutes=1)}
        self.assertEqual(
            (),
            finalization_candidates(("WAIT", "DONE"), now, target, set(), {}, attempts, waits),
        )

    def test_minute_completion_uses_last_completed_market_bar(self) -> None:
        target = date(2026, 9, 8)
        self.assertTrue(minute_bars_complete((datetime(2026, 9, 8, 15, 29),), target, False))
        self.assertFalse(minute_bars_complete((datetime(2026, 9, 8, 15, 29),), target, True))
        self.assertTrue(minute_bars_complete((datetime(2026, 9, 8, 19, 59),), target, True))

    def test_effective_date_completion_uses_query_evidence_not_last_trade_minute(self) -> None:
        target = date(2026, 9, 14)
        sparse = (datetime(2026, 9, 14, 19, 42),)
        self.assertTrue(minute_bars_complete(sparse, target, False, query_completed=True))
        self.assertFalse(minute_bars_complete(sparse, target, False, query_completed=False))
        self.assertFalse(minute_bars_complete((), target, False, query_completed=True))

    def test_outcome_separates_complete_retry_and_unconfirmed(self) -> None:
        now = datetime(2026, 9, 8, 20, 10)
        target = now.date()
        outcome = evaluate_finalization_outcome(
            {"OK", "RETRY", "NO_MINUTE", "NO_BOTH"},
            {"OK", "RETRY", "NO_BOTH"},
            {"OK", "NO_MINUTE"},
            target,
            {(target, "RETRY"): 1, (target, "NO_MINUTE"): 2, (target, "NO_BOTH"): 2},
            now,
        )
        self.assertEqual(("OK",), outcome.completed)
        self.assertEqual(("RETRY",), outcome.retry_codes)
        self.assertEqual(
            (("NO_BOTH", ("일봉",)), ("NO_MINUTE", ("분봉",))),
            outcome.unconfirmed,
        )
        self.assertEqual(now + timedelta(minutes=5), outcome.retry_at)


if __name__ == "__main__":
    unittest.main()
