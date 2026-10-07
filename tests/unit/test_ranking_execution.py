from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace

from kiwoom_monitor.application.ranking_execution import RankingExecutionCoordinator
from kiwoom_monitor.application.ranking_schedule import RankingResponseAction


class FakeClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


class FakeRankingApi:
    def __init__(self, stocks: tuple[object, ...]) -> None:
        self.stocks = stocks

    def load_top_stocks(self) -> tuple[object, ...]:
        return self.stocks


class RankingExecutionCoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock(datetime(2026, 9, 10, 9, 0, 10))
        self.coordinator = RankingExecutionCoordinator(self.clock.now)

    def test_schedule_uses_injected_clock_without_qt(self) -> None:
        schedule = self.coordinator.next_schedule("5")
        self.assertEqual(datetime(2026, 9, 10, 9, 0, 30), schedule.next_time)

    def test_overlapping_timer_request_is_consumed_once_after_worker(self) -> None:
        self.coordinator.begin_priority_preparation()
        self.assertFalse(self.coordinator.ranking_timer_fired(worker_running=True))
        self.assertFalse(self.coordinator.priority_preparing)
        self.assertTrue(self.coordinator.worker_finished())
        self.assertFalse(self.coordinator.worker_finished())

    def test_partial_fake_api_response_retries_twice_then_waits(self) -> None:
        api = FakeRankingApi(tuple(SimpleNamespace(code=str(index), rank=index) for index in range(17)))
        actions = [
            self.coordinator.handle_response(
                api.load_top_stocks(), expected_count=20, has_blocking_modal=True,
            ).decision.action
            for _ in range(3)
        ]
        self.assertEqual(
            [RankingResponseAction.RETRY_SOON, RankingResponseAction.RETRY_SOON, RankingResponseAction.WAIT_NEXT],
            actions,
        )
        self.assertFalse(self.coordinator.has_deferred_response)

    def test_complete_response_is_deferred_then_applied_with_change_state(self) -> None:
        first = (SimpleNamespace(code="A", rank=1, change_rate=1.0),)
        deferred = self.coordinator.handle_response(first, expected_count=1, has_blocking_modal=True)
        self.assertEqual(RankingResponseAction.DEFER_WHILE_MODAL, deferred.decision.action)
        self.assertTrue(self.coordinator.has_deferred_response)

        stocks = self.coordinator.take_deferred_response()
        self.assertEqual(first, stocks)
        applied = self.coordinator.handle_response(stocks or (), expected_count=1, has_blocking_modal=False)
        self.assertEqual(RankingResponseAction.APPLY, applied.decision.action)
        self.assertIsNotNone(applied.change_summary)
        self.assertEqual({"A": 1}, dict(self.coordinator.rank_by_code))

        changed = (SimpleNamespace(code="A", rank=2, change_rate=1.0),)
        outcome = self.coordinator.handle_response(changed, expected_count=1, has_blocking_modal=False)
        self.assertEqual(frozenset({"A"}), outcome.change_summary.changed_codes if outcome.change_summary else frozenset())

    def test_cancel_discards_only_queued_request(self) -> None:
        self.coordinator.ranking_timer_fired(worker_running=True)
        self.coordinator.cancel_pending_request()
        self.assertFalse(self.coordinator.worker_finished())


if __name__ == "__main__":
    unittest.main()
