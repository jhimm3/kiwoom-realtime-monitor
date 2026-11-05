from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date, datetime, time, timedelta

from kiwoom_monitor.application.close_and_ipo import (
    CloseIpoPosition,
    ClosePolicy,
    IpoBreakoutPolicy,
    evaluate_close_and_ipo,
)
from kiwoom_monitor.application.market_session_schedule import (
    KST,
    MarketPhase,
    MarketSession,
    SessionSupport,
    SessionWindow,
    TradingDayEvidence,
)
from kiwoom_monitor.application.research_replay import KrxMinuteBarFrame
from kiwoom_monitor.domain.market_data_contract import ListingEvidence, ListingPhase, ListingType


class CloseAndIpoTests(unittest.TestCase):
    symbol = "123456"
    friday = date(2026, 9, 11)
    tuesday = date(2026, 9, 15)
    published = datetime.combine(friday, time(8), KST)

    def setUp(self) -> None:
        self.entry = self.window(self.friday, time(9), time(15, 20))
        self.auction = self.window(self.friday, time(15, 20), time(15, 30), auction=True)
        self.following = self.window(self.tuesday, time(10), time(16, 20))
        self.calendar = (
            self.day(self.friday, (self.entry, self.auction)),
            *(self.day(self.friday + timedelta(days=offset), ()) for offset in (1, 2, 3)),
            self.day(self.tuesday, (self.following,)),
        )
        self.listing = ListingEvidence(
            self.symbol, self.friday, ListingType.IPO, self.published,
            "fixture:exchange", "listing-revision", "ipo-listing-fact",
        )
        self.close = ClosePolicy(
            quantity=4, entry_window_minutes=5, lookback_bars=2, minimum_close_return_bps=200,
            adverse_gap_bps=300, protection_loss_bps=800, exit_minutes_after_open=8,
            ipo_reduction_quantity=None, listing_phases=(),
        )
        self.ipo_close = replace(self.close, ipo_reduction_quantity=2, listing_phases=(ListingPhase.LISTING_DAY,))
        self.ipo_breakout = IpoBreakoutPolicy(
            quantity=4, lookback_bars=2, breakout_buffer_bps=50, protection_loss_bps=800,
            exit_minutes_before_close=2, listing_phases=(ListingPhase.LISTING_DAY,),
        )
        self.prior = (
            self.bar(self.entry, time(15, 14), price=100, revision="first"),
            self.bar(self.entry, time(15, 15), price=101, revision="second"),
        )
        self.signal = self.bar(self.entry, time(15, 16), price=104, revision="signal", high=107)
        self.closing = self.bar(self.auction, time(15, 20), price=105, revision="closing-auction")
        self.opening = self.bar(self.following, time(10), price=102, revision="first-next-bar", opening=96)

    def window(self, day: date, start: time, end: time, *, auction: bool = False) -> SessionWindow:
        return SessionWindow(
            "KRX", day, MarketSession.KRX_CLOSING_AUCTION if auction else MarketSession.KRX_REGULAR,
            MarketPhase.AUCTION_ORDER_ENTRY if auction else MarketPhase.CONTINUOUS,
            datetime.combine(day, start, KST), datetime.combine(day, end, KST),
            "fixture:session-window", SessionSupport.SUPPORTED,
        )

    def day(self, day: date, sessions: tuple[SessionWindow, ...]) -> TradingDayEvidence:
        return TradingDayEvidence("KRX", day, self.published, f"day:{day}", "fixture:calendar", sessions)

    def bar(self, window: SessionWindow, minute: time, *, price: int, revision: str,
            opening: int | None = None, high: int | None = None) -> KrxMinuteBarFrame:
        start = datetime.combine(window.trading_date, minute, KST)
        end = window.ends_at if window.session is MarketSession.KRX_CLOSING_AUCTION else start + timedelta(minutes=1)
        opening = price if opening is None else opening
        return KrxMinuteBarFrame(
            revision, start.isoformat(), self.symbol, start.isoformat(), end.isoformat(),
            (end + timedelta(seconds=2)).isoformat(), opening, max(opening, price) if high is None else high,
            min(opening, price), price, 100, 1, False, "complete", "fixture",
            market_session=window.session.value, market_phase=window.phase.value, schedule_version=window.schedule_version,
        )

    def decide(self, policy, *, bar=None, history=None, calendar=None, listings=None, position=None):
        return evaluate_close_and_ipo(
            policy, bar=bar or self.signal, history=self.prior if history is None else history,
            calendar=self.calendar if calendar is None else calendar,
            listings=(self.listing,) if listings is None else listings, position=position,
        )

    def test_general_close_pins_verified_next_date_then_exits_all_actual_fills_on_adverse_gap(self):
        decision = self.decide(self.close)
        self.assertEqual(("ENTER", 4), (decision.action, decision.quantity))
        self.assertEqual(self.tuesday, decision.setup.next_date)
        self.assertEqual(datetime.combine(self.tuesday, time(10), KST), decision.setup.next_open_at)
        self.assertEqual(self.auction, decision.setup.close_auction)
        self.assertEqual(("first", "second", "signal"), decision.setup.entry_bar_refs)

        position = CloseIpoPosition(decision.setup, 3, 103, 0)
        held = self.decide(self.close, position=position)
        self.assertEqual(("HOLD", 0, "planned_overnight_hold"), (held.action, held.quantity, held.reason))
        exit_order = self.decide(self.close, bar=self.opening, history=(self.closing,), position=position)
        self.assertEqual(("EXIT", 3, "adverse_gap_full_exit"), (exit_order.action, exit_order.quantity, exit_order.reason))
        self.assertEqual(("closing-auction", "first-next-bar"), exit_order.evidence_refs)

    def test_close_will_not_enter_on_an_unknown_intervening_day_or_unmet_return(self):
        incomplete = self.calendar[:3] + self.calendar[4:]
        decision = self.decide(self.close, calendar=incomplete)
        self.assertEqual(("WAIT", "close_or_next_open_evidence_unavailable"), (decision.action, decision.reason))
        decision = self.decide(replace(self.close, minimum_close_return_bps=600))
        self.assertEqual(("WAIT", "close_setup_return_below_threshold"), (decision.action, decision.reason))

    def test_close_proxy_uses_the_confirmed_open_and_preopen_known_auction(self):
        position = CloseIpoPosition(self.decide(self.close).setup, 4, 103, 0)
        second = self.bar(self.following, time(10, 1), price=98, revision="second-next-bar")
        late_close = replace(self.closing, available_at=self.opening.available_at)
        for bar, history in ((second, (self.closing,)), (self.opening, (late_close,)),
                             (self.opening, (replace(self.closing, capture_quality="partial"),))):
            with self.subTest(bar=bar.revision_id, history=history):
                decision = self.decide(self.close, bar=bar, history=history, position=position)
                self.assertEqual(("HOLD", "completed_close_or_open_proxy_unavailable"),
                                 (decision.action, decision.reason))
        deadline = self.bar(self.following, time(10, 7), price=104, revision="deadline")
        decision = self.decide(self.close, bar=deadline, history=(), position=position)
        self.assertEqual(("EXIT", 4, "next_session_exit_deadline"), (decision.action, decision.quantity, decision.reason))

    def test_ipo_close_reduces_only_remaining_tranche_and_protects_actual_residual(self):
        setup = self.decide(self.ipo_close).setup
        self.assertEqual(ListingPhase.LISTING_DAY, setup.listing_phase)
        self.assertEqual(("ipo-listing-fact",), setup.listing_refs)
        for owned, already_sold, expected in ((4, 0, ("REDUCE", 2)), (3, 1, ("REDUCE", 1)), (2, 2, ("HOLD", 0))):
            with self.subTest(owned=owned, already_sold=already_sold):
                decision = self.decide(self.close, bar=self.opening, history=(self.closing,),
                                       position=CloseIpoPosition(setup, owned, 103, already_sold))
                self.assertEqual(expected, (decision.action, decision.quantity))
                self.assertIs(setup, decision.setup)

        protection = self.bar(self.following, time(10, 1), price=90, revision="protection")
        decision = self.decide(replace(self.ipo_close, protection_loss_bps=2000), bar=protection,
                               position=CloseIpoPosition(setup, 2, 103, 2), history=(), listings=())
        self.assertEqual(("EXIT", 2, "filled_position_protection"), (decision.action, decision.quantity, decision.reason))

    def test_ipo_breakout_uses_prior_high_without_trigger_or_future_correction(self):
        policy = replace(self.ipo_breakout, exit_minutes_before_close=1)
        correction = replace(self.prior[1], high=110, revision_id="future-high",
                             available_at=(datetime.fromisoformat(self.signal.available_at) + timedelta(seconds=1)).isoformat())
        decision = self.decide(policy, history=(*self.prior, self.signal, correction))
        self.assertEqual(("ENTER", 4, 101), (decision.action, decision.quantity, decision.setup.breakout_level))
        position = CloseIpoPosition(decision.setup, 3, 104, 0)
        failed = self.bar(self.entry, time(15, 17), price=100, revision="failed-prior-high")
        decision = self.decide(policy, bar=failed, position=position)
        self.assertEqual(("EXIT", 3, "pinned_breakout_level_failed"), (decision.action, decision.quantity, decision.reason))
        deadline = self.bar(self.entry, time(15, 18), price=105, revision="breakout-deadline")
        decision = self.decide(policy, bar=deadline, position=position)
        self.assertEqual(("EXIT", 3, "ipo_breakout_deadline"), (decision.action, decision.quantity, decision.reason))

    def test_ipo_breakout_distinguishes_missing_listing_type_phase_and_contiguous_minutes(self):
        future = replace(self.listing, available_at=self.following.starts_at)
        for listing, reason in ((future, "listing_unknown"),
                                (replace(self.listing, listing_type=ListingType.SPAC), "listing_type_or_phase_unsupported"),
                                (replace(self.listing, listing_date=self.friday - timedelta(days=1)),
                                 "listing_type_or_phase_unsupported")):
            with self.subTest(listing=listing):
                decision = self.decide(self.ipo_breakout, listings=(listing,))
                self.assertEqual(("WAIT", reason), (decision.action, decision.reason))
        accepted = replace(self.ipo_breakout, listing_phases=(ListingPhase.AFTER_LISTING_DAY,))
        earlier_listing = replace(self.listing, listing_date=self.friday - timedelta(days=1))
        self.assertEqual(ListingPhase.AFTER_LISTING_DAY, self.decide(accepted, listings=(earlier_listing,)).setup.listing_phase)
        decision = self.decide(self.ipo_breakout, history=(self.prior[1],))
        self.assertEqual(("WAIT", "prior_complete_minutes_unavailable"), (decision.action, decision.reason))

    def test_later_calendar_revision_does_not_rewrite_the_original_setup(self):
        setup = self.decide(self.close).setup
        changed = replace(self.calendar[-1], available_at=self.following.starts_at, revision_id="next-day-corrected")
        result = self.decide(self.close, bar=self.opening, history=(self.closing,),
                             calendar=(*self.calendar, changed), position=CloseIpoPosition(setup, 4, 103, 0))
        self.assertEqual(("HOLD", "pinned_next_session_changed"), (result.action, result.reason))
        self.assertEqual(f"day:{self.tuesday}", result.setup.next_calendar_ref)

    def test_strictly_partial_ipo_policy_and_actual_quantities_are_required(self):
        with self.assertRaisesRegex(ValueError, "strictly partial"):
            replace(self.ipo_close, ipo_reduction_quantity=4)
        with self.assertRaisesRegex(ValueError, "entered plan"):
            CloseIpoPosition(self.decide(self.ipo_close).setup, 3, 103, 2)
