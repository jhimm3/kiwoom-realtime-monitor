from __future__ import annotations

import unittest

from kiwoom_monitor.application.secondary_data_schedule import (
    FollowupPhase,
    SecondaryDataFollowupCoordinator,
    SecondaryStartPhase,
    daily_catalog_sync_due,
    daily_high_candidates,
    fundamentals_candidates,
    minute_history_candidates,
    nxt_eligibility_candidates,
    phase_after_fundamentals,
)


class SecondaryDataScheduleTests(unittest.TestCase):
    def coordinator(self, calls: list[tuple[object, ...]], *, starts: dict[str, bool] | None = None):
        starts = starts or {}
        def worker(name: str):
            def start(codes: tuple[str, ...], force: bool = False) -> bool:
                calls.append((name, codes, force))
                return starts.get(name, False)
            return start
        return SecondaryDataFollowupCoordinator(
            start_minute_history=worker("minute"),
            start_daily_high=worker("daily"),
            start_daily_high_phase=lambda codes: calls.append(("daily_phase", codes)),
            start_fundamentals=lambda codes: worker("fundamentals")(codes),
            start_fundamentals_phase=lambda codes: calls.append(("fundamentals_phase", codes)),
            start_nxt_phase=lambda codes: calls.append(("nxt", codes)),
            start_new_high_phase=lambda codes: calls.append(("new_high", codes)),
        )

    def test_coordinator_prioritizes_finalization_and_stops_chain(self) -> None:
        calls: list[tuple[object, ...]] = []
        coordinator = self.coordinator(calls, starts={"minute": True})

        phase = coordinator.start(("A", "B"), finalization_codes=("B",), after_hours_pause=True)

        self.assertIs(SecondaryStartPhase.FINALIZATION, phase)
        self.assertEqual([("minute", ("B",), True)], calls)

    def test_coordinator_skips_stale_market_data_after_hours(self) -> None:
        calls: list[tuple[object, ...]] = []
        coordinator = self.coordinator(calls)

        phase = coordinator.start(("A",), after_hours_pause=True)

        self.assertIs(SecondaryStartPhase.AFTER_HOURS, phase)
        self.assertEqual([("fundamentals", ("A",), False), ("nxt", ("A",))], calls)

    def test_coordinator_moves_to_daily_phase_when_minute_history_is_cached(self) -> None:
        calls: list[tuple[object, ...]] = []
        coordinator = self.coordinator(calls)

        phase = coordinator.start(("A",), after_hours_pause=False)

        self.assertIs(SecondaryStartPhase.MINUTE_HISTORY, phase)
        self.assertEqual([
            ("minute", ("A",), False),
            ("daily_phase", ("A",)),
        ], calls)

    def test_coordinator_preserves_worker_completion_order(self) -> None:
        calls: list[tuple[object, ...]] = []
        coordinator = self.coordinator(calls)

        coordinator.minute_history_finished(("A",), forced=False)
        coordinator.minute_history_finished(("B",), forced=True)
        coordinator.daily_high_finished(("C",))
        coordinator.fundamentals_finished(("D",), after_hours_pause=False)
        coordinator.fundamentals_finished(("E",), after_hours_pause=True)

        self.assertEqual([
            ("daily_phase", ("A",)),
            ("daily", ("B",), True),
            ("fundamentals_phase", ("C",)),
            ("new_high", ("D",)),
            ("nxt", ("E",)),
        ], calls)

    def test_minute_history_skips_loaded_codes_unless_forced(self) -> None:
        codes = ("A", "B", "C")
        self.assertEqual(("B",), minute_history_candidates(codes, {"A", "C"}))
        self.assertEqual(codes, minute_history_candidates(codes, {"A", "C"}, force=True))

    def test_daily_high_requires_memory_and_same_day_refresh(self) -> None:
        codes = ("A", "B", "C")
        self.assertEqual(
            ("B", "C"),
            daily_high_candidates(codes, {"A", "B"}, {"A", "C"}),
        )
        self.assertEqual(
            codes,
            daily_high_candidates(codes, codes, codes, adjusted_basis_refresh=True),
        )

    def test_repository_refresh_list_overrides_in_memory_fundamentals(self) -> None:
        codes = ("A", "B", "C")
        self.assertEqual(("C",), fundamentals_candidates(codes, {"A"}, ("C",)))
        self.assertEqual(("B", "C"), fundamentals_candidates(codes, {"A"}, None))
        self.assertEqual(codes, fundamentals_candidates(codes, codes, (), force=True))

    def test_nxt_candidates_use_cache_when_available(self) -> None:
        codes = ("A", "B", "C")
        self.assertEqual(("B",), nxt_eligibility_candidates(codes, {"A": True, "C": False}, {"B"}))
        self.assertEqual(("A", "C"), nxt_eligibility_candidates(codes, None, {"B"}))

    def test_after_hours_skips_new_high_and_moves_to_nxt(self) -> None:
        self.assertIs(FollowupPhase.NEW_HIGH, phase_after_fundamentals(False))
        self.assertIs(FollowupPhase.NXT, phase_after_fundamentals(True))

    def test_krx_catalog_sync_is_due_only_until_today_succeeds(self) -> None:
        self.assertTrue(daily_catalog_sync_due("", "2026-09-12"))
        self.assertTrue(daily_catalog_sync_due("2026-09-11 22:00:00", "2026-09-12"))
        self.assertFalse(daily_catalog_sync_due("2026-09-12 04:19:07", "2026-09-12"))


if __name__ == "__main__":
    unittest.main()
