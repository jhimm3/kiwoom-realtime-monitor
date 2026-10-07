from __future__ import annotations

import unittest
from datetime import date, datetime, time, timezone

from kiwoom_monitor.application.market_session_schedule import (
    CURRENT_SCHEDULE_VERSION,
    KST,
    KRX_AFTER_RESEARCH_PROFILE,
    KRX_FULL_DAY_RESEARCH_PROFILE,
    KRX_REGULAR_RESEARCH_PROFILE,
    krx_regular_session_hours,
    LEGACY_SCHEDULE_VERSION,
    LEGACY_UNFILTERED_REPLAY_PROFILE,
    MarketPhase,
    MarketSession,
    SessionSupport,
    active_realtime_codes,
    after_hours_data_pause,
    full_day_close_at,
    is_nxt_only_session,
    mock_order_entry_decision,
    next_realtime_session_boundary,
    realtime_subscription_target,
    research_bar_allowed,
    research_session_profile_document,
    research_session_profile_contract_matches,
    session_window_at,
    top20_collection_available,
    top20_collection_open,
)


class MarketSessionScheduleTests(unittest.TestCase):
    def test_old_frozen_session_profile_remains_readable_without_accepting_mutations(self) -> None:
        current = research_session_profile_document(KRX_REGULAR_RESEARCH_PROFILE)
        legacy = {key: value for key, value in current.items() if not key.startswith("verified_delayed_")}
        self.assertTrue(research_session_profile_contract_matches(current, KRX_REGULAR_RESEARCH_PROFILE))
        self.assertTrue(research_session_profile_contract_matches(legacy, KRX_REGULAR_RESEARCH_PROFILE))
        self.assertFalse(research_session_profile_contract_matches(
            {**legacy, "windows_kst": ["09:00-16:00"]}, KRX_REGULAR_RESEARCH_PROFILE,
        ))
        self.assertFalse(research_session_profile_contract_matches(
            {**legacy, "unexpected": True}, KRX_REGULAR_RESEARCH_PROFILE,
        ))

    def test_verified_delayed_krx_date_has_ordinary_1530_and_1630_close(self) -> None:
        day = date(2025, 11, 13)
        self.assertEqual((time(10, 0), time(16, 30)),
                         krx_regular_session_hours(day))
        self.assertEqual(MarketSession.KRX_REGULAR,
                         session_window_at(datetime(2025, 11, 13, 15, 30), venue="KRX").session)
        self.assertEqual(MarketSession.KRX_CLOSING_AUCTION,
                         session_window_at(datetime(2025, 11, 13, 16, 25), venue="KRX").session)
        self.assertTrue(research_bar_allowed(
            datetime(2025, 11, 13, 15, 29, tzinfo=KST),
            datetime(2025, 11, 13, 15, 30, tzinfo=KST),
            venue="KRX", declared_session="KRX_REGULAR", declared_phase="CONTINUOUS",
        ))

    def setUp(self) -> None:
        self.codes = ("A", "B")
        self.nxt_codes = {"B"}

    def test_realtime_codes_follow_krx_and_nxt_boundaries(self) -> None:
        self.assertEqual((), active_realtime_codes(self.codes, self.nxt_codes, datetime(2026, 9, 9, 7, 59)))
        self.assertEqual(("B",), active_realtime_codes(self.codes, self.nxt_codes, datetime(2026, 9, 9, 8, 0)))
        self.assertEqual(self.codes, active_realtime_codes(self.codes, self.nxt_codes, datetime(2026, 9, 9, 9, 0)))
        self.assertEqual(self.codes, active_realtime_codes(self.codes, self.nxt_codes, datetime(2026, 9, 9, 15, 29)))
        self.assertEqual(("B",), active_realtime_codes(self.codes, self.nxt_codes, datetime(2026, 9, 9, 15, 30)))
        self.assertEqual((), active_realtime_codes(self.codes, self.nxt_codes, datetime(2026, 9, 9, 20, 0)))

    def test_effective_date_keeps_requested_krx_and_nxt_venues_through_after_market(self) -> None:
        preparing = realtime_subscription_target(
            self.codes, self.nxt_codes, datetime(2026, 9, 14, 8, 55), environment="real",
        )
        after = realtime_subscription_target(
            self.codes, self.nxt_codes, datetime(2026, 9, 14, 16, 0), environment="real",
        )
        nxt_main_preparing = realtime_subscription_target(
            self.codes, self.nxt_codes, datetime(2026, 9, 14, 9, 0), environment="real",
        )
        nxt_after_preparing = realtime_subscription_target(
            self.codes, self.nxt_codes, datetime(2026, 9, 14, 15, 30), environment="real",
        )
        self.assertEqual(self.codes, preparing.krx_codes)
        self.assertEqual(self.codes, after.krx_codes)
        self.assertEqual(("B",), nxt_main_preparing.nxt_codes)
        self.assertEqual(("B",), nxt_after_preparing.nxt_codes)
        self.assertEqual(("B",), after.nxt_codes)
        self.assertEqual((), realtime_subscription_target(
            self.codes, self.nxt_codes, datetime(2026, 9, 14, 20, 0), environment="real",
        ).active_codes)

    def test_mock_subscription_is_not_extended_into_after_market(self) -> None:
        self.assertEqual((), realtime_subscription_target(
            self.codes, self.nxt_codes, datetime(2026, 9, 14, 16, 0), environment="mock",
        ).active_codes)

    def test_required_afternoon_boundaries_change_policy_signature(self) -> None:
        signatures = [
            realtime_subscription_target(
                self.codes, self.nxt_codes, datetime(2026, 9, 14, hour, minute), environment="real",
            ).signature
            for hour, minute in ((15, 19), (15, 20), (15, 30), (15, 40), (16, 0), (20, 0))
        ]
        self.assertEqual(len(signatures), len(set(signatures)))

    def test_nxt_only_requires_real_environment_and_weekday(self) -> None:
        morning = datetime(2026, 9, 9, 8, 30)
        saturday = datetime(2026, 9, 12, 8, 30)
        self.assertTrue(is_nxt_only_session("real", morning))
        self.assertFalse(is_nxt_only_session("mock", morning))
        self.assertFalse(is_nxt_only_session("real", saturday))

    def test_next_session_boundary_skips_boundary_equal_to_now(self) -> None:
        self.assertEqual(
            datetime(2026, 9, 9, 9, 0),
            next_realtime_session_boundary(datetime(2026, 9, 9, 8, 55)),
        )

    def test_after_hours_pause_and_top20_collection_keep_existing_windows(self) -> None:
        self.assertTrue(after_hours_data_pause(datetime(2026, 9, 9, 7, 54)))
        self.assertFalse(after_hours_data_pause(datetime(2026, 9, 9, 7, 55)))
        self.assertTrue(after_hours_data_pause(datetime(2026, 9, 9, 20, 5)))
        self.assertTrue(top20_collection_open(datetime(2026, 9, 9, 8, 0)))
        self.assertFalse(top20_collection_open(datetime(2026, 9, 9, 20, 0)))
        self.assertFalse(top20_collection_open(datetime(2026, 9, 12, 10, 0)))

    def test_nas_disconnect_routes_leave_a_visible_top20_gap(self) -> None:
        now = datetime(2026, 9, 9, 10, 0)
        self.assertTrue(top20_collection_available(now, ""))
        self.assertTrue(top20_collection_available(now, "central"))
        self.assertFalse(top20_collection_available(now, "central_waiting"))
        self.assertFalse(top20_collection_available(now, "central_retry"))
        self.assertFalse(top20_collection_available(now, "local_fallback"))

    def test_effective_date_selects_legacy_and_new_krx_after_sessions(self) -> None:
        legacy = session_window_at(datetime(2026, 9, 11, 16, 0), venue="KRX")
        current = session_window_at(datetime(2026, 9, 14, 16, 0), venue="KRX")

        self.assertEqual(LEGACY_SCHEDULE_VERSION, legacy.schedule_version)
        self.assertEqual(MarketSession.KRX_LEGACY_PERIODIC_AUCTION, legacy.session)
        self.assertEqual(MarketPhase.PERIODIC_AUCTION, legacy.phase)
        self.assertEqual(CURRENT_SCHEDULE_VERSION, current.schedule_version)
        self.assertEqual(MarketSession.KRX_AFTER_MARKET, current.session)
        self.assertEqual(MarketPhase.CONTINUOUS, current.phase)
        self.assertEqual(18, full_day_close_at(date(2026, 9, 11), venue="KRX").hour)
        self.assertEqual(20, full_day_close_at(date(2026, 9, 14), venue="KRX").hour)

    def test_krx_regular_close_and_after_hours_close_remain_distinct(self) -> None:
        closing = session_window_at(datetime(2026, 9, 14, 15, 20), venue="KRX")
        order_entry = session_window_at(datetime(2026, 9, 14, 15, 30), venue="KRX")
        fixed_price = session_window_at(datetime(2026, 9, 14, 15, 40), venue="KRX")

        self.assertEqual(MarketSession.KRX_CLOSING_AUCTION, closing.session)
        self.assertTrue(closing.permits("regular_close"))
        self.assertEqual(MarketPhase.AUCTION_ORDER_ENTRY, order_entry.phase)
        self.assertEqual(MarketSession.KRX_AFTER_HOURS_CLOSE, fixed_price.session)
        self.assertEqual(MarketPhase.FIXED_PRICE, fixed_price.phase)
        self.assertEqual(MarketSession.CLOSED,
                         session_window_at(datetime(2026, 9, 14, 20, 0), venue="KRX").session)

    def test_unknown_nxt_phase_mock_and_non_trading_day_are_not_promoted(self) -> None:
        unknown_order_entry = session_window_at(datetime(2026, 9, 14, 15, 35), venue="NXT")
        unknown_dynamic = session_window_at(
            datetime(2026, 9, 14, 10, 0), venue="NXT", observed_phase="NEW_PHASE",
        )
        unobserved_dynamic = session_window_at(datetime(2026, 9, 14, 10, 0), venue="NXT")
        mock_after = session_window_at(
            datetime(2026, 9, 14, 16, 0), venue="KRX", environment="mock",
        )
        sunday = session_window_at(datetime(2026, 9, 13, 10, 0), venue="KRX")

        self.assertEqual(SessionSupport.UNKNOWN, unknown_order_entry.support)
        self.assertEqual(MarketPhase.UNKNOWN, unknown_order_entry.phase)
        self.assertEqual(SessionSupport.UNKNOWN, unknown_dynamic.support)
        self.assertEqual(MarketPhase.UNKNOWN, unobserved_dynamic.phase)
        self.assertFalse(unobserved_dynamic.permits("continuous_trade"))
        self.assertEqual(SessionSupport.UNSUPPORTED, mock_after.support)
        self.assertEqual(SessionSupport.UNSUPPORTED, sunday.support)

    def test_mock_new_order_gate_allows_regular_and_manual_krx_after_limit_probe(self) -> None:
        allowed = mock_order_entry_decision(
            datetime(2026, 9, 14, 9, 0),
            environment="mock", venue="KRX", order_type="LIMIT",
        )
        self.assertTrue(allowed.allowed)
        self.assertEqual(MarketSession.KRX_REGULAR, allowed.session)
        self.assertIn(CURRENT_SCHEDULE_VERSION, allowed.policy_version)

        for hour, minute in ((15, 20), (15, 30), (15, 35), (15, 40)):
            with self.subTest(hour=hour, minute=minute):
                rejected = mock_order_entry_decision(
                    datetime(2026, 9, 14, hour, minute),
                    environment="mock", venue="KRX", order_type="LIMIT",
                )
                self.assertFalse(rejected.allowed)
                self.assertTrue(rejected.evidence.startswith("UNSUPPORTED|"))
                self.assertIn(f"session={rejected.session.value}", rejected.evidence)
                self.assertIn(f"phase={rejected.phase.value}", rejected.evidence)
                self.assertIn(f"schedule={CURRENT_SCHEDULE_VERSION}", rejected.evidence)
                self.assertIn(f"profile={KRX_REGULAR_RESEARCH_PROFILE}", rejected.evidence)

        for hour, minute in ((16, 0), (19, 59)):
            with self.subTest(after_probe=(hour, minute)):
                probe = mock_order_entry_decision(
                    datetime(2026, 9, 14, hour, minute),
                    environment="mock", venue="KRX", order_type="LIMIT",
                )
                self.assertTrue(probe.allowed)
                self.assertEqual("mock_after_broker_probe", probe.reason)
                self.assertEqual(MarketSession.KRX_AFTER_MARKET, probe.session)
                self.assertEqual(KRX_AFTER_RESEARCH_PROFILE, probe.session_profile)
                self.assertIn("manual-mock-krx-after-limit-probe/v1", probe.policy_version)

        market = mock_order_entry_decision(
            datetime(2026, 9, 14, 10, 0),
            environment="mock", venue="KRX", order_type="MARKET",
        )
        nxt = mock_order_entry_decision(
            datetime(2026, 9, 14, 10, 0),
            environment="mock", venue="NXT", order_type="LIMIT",
        )
        self.assertEqual("mock_order_type_not_verified", market.reason)
        self.assertEqual("mock_venue_not_verified", nxt.reason)

    def test_nxt_090030_boundary_remains_unknown_for_research(self) -> None:
        before = session_window_at(datetime(2026, 9, 14, 9, 0, 29), venue="NXT")
        opened = session_window_at(datetime(2026, 9, 14, 9, 0, 30), venue="NXT")
        self.assertEqual(MarketSession.CLOSED, before.session)
        self.assertEqual(MarketSession.NXT_MAIN_MARKET, opened.session)
        self.assertEqual(MarketPhase.UNKNOWN, opened.phase)
        self.assertFalse(opened.permits("continuous_trade"))

    def test_next_session_boundary_includes_new_afternoon_refreshes(self) -> None:
        self.assertEqual(
            datetime(2026, 9, 9, 15, 20),
            next_realtime_session_boundary(datetime(2026, 9, 9, 15, 19, 59)),
        )
        self.assertEqual(
            datetime(2026, 9, 9, 15, 40),
            next_realtime_session_boundary(datetime(2026, 9, 9, 15, 35)),
        )

    def test_regular_research_profile_rejects_after_market_and_unknown_metadata(self) -> None:
        regular_start = datetime(2026, 9, 14, 6, 29, tzinfo=timezone.utc)
        regular_end = datetime(2026, 9, 14, 6, 30, tzinfo=timezone.utc)
        after_start = datetime(2026, 9, 14, 7, 0, tzinfo=timezone.utc)
        after_end = datetime(2026, 9, 14, 7, 1, tzinfo=timezone.utc)

        self.assertTrue(research_bar_allowed(
            regular_start, regular_end, venue="KRX", session_profile=KRX_REGULAR_RESEARCH_PROFILE,
        ))
        self.assertFalse(research_bar_allowed(
            after_start, after_end, venue="KRX", session_profile=KRX_REGULAR_RESEARCH_PROFILE,
        ))
        self.assertFalse(research_bar_allowed(
            regular_start, regular_end, venue="KRX", session_profile=KRX_REGULAR_RESEARCH_PROFILE,
            declared_phase="NEW_PHASE",
        ))
        self.assertTrue(research_bar_allowed(
            after_start, after_end, venue="KRX", session_profile=LEGACY_UNFILTERED_REPLAY_PROFILE,
        ))

    def test_unclassified_pre_effective_bar_keeps_legacy_replay_interpretation(self) -> None:
        legacy_after_start = datetime(2026, 9, 11, 7, 0, tzinfo=timezone.utc)
        legacy_after_end = datetime(2026, 9, 11, 7, 1, tzinfo=timezone.utc)

        self.assertTrue(research_bar_allowed(
            legacy_after_start, legacy_after_end,
            venue="KRX", session_profile=KRX_REGULAR_RESEARCH_PROFILE,
        ))
        self.assertFalse(research_bar_allowed(
            legacy_after_start, legacy_after_end,
            venue="KRX", session_profile=KRX_REGULAR_RESEARCH_PROFILE,
            declared_session=MarketSession.KRX_LEGACY_PERIODIC_AUCTION.value,
        ))

    def test_after_and_full_day_profiles_are_explicit_and_exclude_fixed_price_gap(self) -> None:
        regular = (datetime(2026, 9, 14, 6, 29, tzinfo=timezone.utc),
                   datetime(2026, 9, 14, 6, 30, tzinfo=timezone.utc))
        fixed = (datetime(2026, 9, 14, 6, 40, tzinfo=timezone.utc),
                 datetime(2026, 9, 14, 6, 41, tzinfo=timezone.utc))
        after = (datetime(2026, 9, 14, 7, 0, tzinfo=timezone.utc),
                 datetime(2026, 9, 14, 7, 1, tzinfo=timezone.utc))
        self.assertFalse(research_bar_allowed(
            *regular, venue="KRX", session_profile=KRX_AFTER_RESEARCH_PROFILE,
        ))
        self.assertTrue(research_bar_allowed(
            *after, venue="KRX", session_profile=KRX_AFTER_RESEARCH_PROFILE,
        ))
        self.assertTrue(research_bar_allowed(
            *regular, venue="KRX", session_profile=KRX_FULL_DAY_RESEARCH_PROFILE,
        ))
        self.assertFalse(research_bar_allowed(
            *fixed, venue="KRX", session_profile=KRX_FULL_DAY_RESEARCH_PROFILE,
        ))
        contract = research_session_profile_document(KRX_FULL_DAY_RESEARCH_PROFILE)
        self.assertEqual(["15:30-16:00"], contract["excluded_windows_kst"])


if __name__ == "__main__":
    unittest.main()
