from __future__ import annotations

import unittest
from datetime import datetime

from kiwoom_monitor.application.realtime_subscription import (
    RealtimeSubscriptionAction,
    RealtimeSubscriptionCoordinator,
)


class RealtimeSubscriptionCoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 9, 10, 9, 10)
        self.coordinator = RealtimeSubscriptionCoordinator(lambda: self.now)

    def plan(self, *, exists: bool = False, running: bool = False):
        return self.coordinator.plan(
            ("A", "B"), {"B"}, closing=False, worker_available=True,
            worker_exists=exists, worker_running=running,
        )

    def test_first_regular_session_subscription_starts_all_codes(self) -> None:
        decision = self.plan()
        self.assertEqual(RealtimeSubscriptionAction.START, decision.action)
        self.assertEqual(("A", "B"), decision.active_codes)
        self.assertEqual(("B",), decision.nxt_codes)

    def test_unchanged_running_subscription_keeps_worker(self) -> None:
        first = self.plan()
        self.coordinator.commit(first)
        self.assertEqual(RealtimeSubscriptionAction.NONE, self.plan(exists=True, running=True).action)

    def test_changed_running_subscription_updates_without_restart(self) -> None:
        first = self.plan()
        self.coordinator.commit(first)
        decision = self.coordinator.plan(
            ("B", "C"), {"B"}, closing=False, worker_available=True,
            worker_exists=True, worker_running=True,
        )
        self.assertEqual(RealtimeSubscriptionAction.UPDATE, decision.action)
        self.assertEqual(("B", "C"), decision.active_codes)
        self.coordinator.commit(decision)
        self.assertEqual(("B", "C"), self.coordinator.current_codes)

    def test_nxt_only_session_filters_non_nxt_codes(self) -> None:
        self.now = datetime(2026, 9, 10, 8, 30)
        decision = self.plan()
        self.assertEqual(("B",), decision.active_codes)
        self.assertEqual(("B",), decision.nxt_codes)

    def test_closed_session_stops_running_worker(self) -> None:
        self.coordinator.commit(self.plan())
        self.now = datetime(2026, 9, 10, 20, 0)
        decision = self.plan(exists=True, running=True)
        self.assertEqual(RealtimeSubscriptionAction.STOP, decision.action)
        self.coordinator.commit(decision)
        self.assertEqual((), self.coordinator.current_codes)

    def test_stopped_existing_worker_must_be_cleaned_before_start(self) -> None:
        decision = self.plan(exists=True, running=False)
        self.assertEqual(RealtimeSubscriptionAction.START, decision.action)
        self.assertTrue(decision.stop_existing_first)

    def test_closing_or_missing_factory_does_nothing(self) -> None:
        closing = self.coordinator.plan(
            ("A",), set(), closing=True, worker_available=True,
            worker_exists=False, worker_running=False,
        )
        unavailable = self.coordinator.plan(
            ("A",), set(), closing=False, worker_available=False,
            worker_exists=False, worker_running=False,
        )
        self.assertEqual(RealtimeSubscriptionAction.NONE, closing.action)
        self.assertEqual(RealtimeSubscriptionAction.NONE, unavailable.action)


if __name__ == "__main__":
    unittest.main()
