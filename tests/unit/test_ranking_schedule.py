from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace

from kiwoom_monitor.application.ranking_schedule import (
    RankingResponseAction,
    decide_ranking_response,
    next_ranking_schedule,
    summarize_ranking_changes,
)


class RankingScheduleTests(unittest.TestCase):
    def test_thirty_second_schedule_uses_next_half_minute_and_quarter_second_offset(self) -> None:
        schedule = next_ranking_schedule(datetime(2026, 9, 9, 10, 12, 10, 123000), "5")
        self.assertEqual(datetime(2026, 9, 9, 10, 12, 30), schedule.next_time)
        self.assertEqual(datetime(2026, 9, 9, 10, 12, 30, 250000), schedule.request_time)
        self.assertEqual(20_127, schedule.delay_ms)
        self.assertEqual(17_627, schedule.preparation_delay_ms)

    def test_thirty_second_schedule_moves_to_next_minute_after_boundary(self) -> None:
        schedule = next_ranking_schedule(datetime(2026, 9, 9, 10, 12, 30), "5")
        self.assertEqual(datetime(2026, 9, 9, 10, 13, 0), schedule.next_time)

    def test_minute_ten_minute_and_hour_schedules_keep_existing_boundaries(self) -> None:
        now = datetime(2026, 9, 9, 10, 12, 10)
        self.assertEqual(datetime(2026, 9, 9, 10, 13), next_ranking_schedule(now, "1").next_time)
        self.assertEqual(datetime(2026, 9, 9, 10, 20), next_ranking_schedule(now, "2").next_time)
        self.assertEqual(datetime(2026, 9, 9, 11, 0), next_ranking_schedule(now, "3").next_time)

    def test_unknown_query_type_uses_thirty_second_default(self) -> None:
        schedule = next_ranking_schedule(datetime(2026, 9, 9, 10, 12, 5), "unknown")
        self.assertEqual(datetime(2026, 9, 9, 10, 12, 30), schedule.next_time)

    def test_partial_response_retries_twice_then_waits_for_next_schedule(self) -> None:
        first = decide_ranking_response(17, 20, 0, False)
        second = decide_ranking_response(17, 20, first.retry_count, False)
        third = decide_ranking_response(17, 20, second.retry_count, False)
        self.assertEqual(RankingResponseAction.RETRY_SOON, first.action)
        self.assertEqual(RankingResponseAction.RETRY_SOON, second.action)
        self.assertEqual(RankingResponseAction.WAIT_NEXT, third.action)
        self.assertEqual(3, third.retry_count)

    def test_complete_response_is_deferred_while_modal_is_open(self) -> None:
        decision = decide_ranking_response(20, 20, 2, True)
        self.assertEqual(RankingResponseAction.DEFER_WHILE_MODAL, decision.action)
        self.assertEqual(2, decision.retry_count)

    def test_applied_complete_response_resets_retry_count(self) -> None:
        decision = decide_ranking_response(20, 20, 2, False)
        self.assertEqual(RankingResponseAction.APPLY, decision.action)
        self.assertEqual(0, decision.retry_count)

    def test_first_ranking_response_does_not_highlight_every_row(self) -> None:
        stocks = (
            SimpleNamespace(code="A", rank=1, change_rate=3.2),
            SimpleNamespace(code="B", rank=2, change_rate=1.1),
        )
        summary = summarize_ranking_changes(stocks, {}, ())
        self.assertEqual({"A": 1, "B": 2}, summary.rank_by_code)
        self.assertEqual(frozenset(), summary.changed_codes)
        self.assertFalse(summary.unchanged)

    def test_changed_ranks_and_new_codes_are_detected_after_first_response(self) -> None:
        stocks = (
            SimpleNamespace(code="A", rank=2, change_rate=3.2),
            SimpleNamespace(code="C", rank=1, change_rate=4.0),
        )
        summary = summarize_ranking_changes(stocks, {"A": 1, "B": 2}, ((1, "A", 3.2), (2, "B", 1.1)))
        self.assertEqual(frozenset({"A", "C"}), summary.changed_codes)
        self.assertFalse(summary.unchanged)

    def test_identical_signature_is_reported_as_unchanged(self) -> None:
        stocks = (SimpleNamespace(code="A", rank=1, change_rate=3.2),)
        signature = ((1, "A", 3.2),)
        summary = summarize_ranking_changes(stocks, {"A": 1}, signature)
        self.assertEqual(frozenset(), summary.changed_codes)
        self.assertTrue(summary.unchanged)


if __name__ == "__main__":
    unittest.main()
