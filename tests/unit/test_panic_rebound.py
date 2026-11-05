from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from kiwoom_monitor.application.panic_rebound import (
    PanicNewsProof,
    PanicReboundRules,
    evaluate_panic_rebound,
)
from kiwoom_monitor.application.research_replay import KrxMinuteBarFrame
from kiwoom_monitor.domain.time_basis import KST


class PanicReboundTests(unittest.TestCase):
    def setUp(self) -> None:
        self.open = datetime(2026, 9, 10, 9, tzinfo=KST)
        self.rows = tuple(self.bar(index, price) for index, price in enumerate((1000, 990, 980, 900)))
        self.rules = PanicReboundRules("P0", 3, 1000, 3_000_000, 10, 500, 5)
        self.proof = PanicNewsProof(
            "123456", "reviewed-source", "assessment-1", "assessment-r1",
            self.open + timedelta(minutes=2), self.open + timedelta(minutes=3),
            "supported_nonfundamental", ("article-r1", "body-r1", "rule-r1"),
        )

    def bar(self, minute: int, close: int, *, low: int | None = None, high: int | None = None) -> KrxMinuteBarFrame:
        start = self.open + timedelta(minutes=minute)
        end = start + timedelta(minutes=1)
        return KrxMinuteBarFrame(
            f"bar-{minute}", f"key-{minute}", "123456", start.isoformat(), end.isoformat(), end.isoformat(),
            close, high if high is not None else close, low if low is not None else close - 5, close,
            20, 1, False, "complete", "fixture", "krx-regular/v1", "2026-09-10:KRX_REGULAR",
            "KRX_REGULAR", "CONTINUOUS", "fixture-schedule/v1",
        )

    def test_p0_pins_decline_proof_and_does_not_use_future_price_or_news(self) -> None:
        future_adverse = replace(
            self.proof, revision="adverse-r2", disposition="fundamental_adverse",
            available_at=(self.open + timedelta(minutes=4, microseconds=1)).astimezone(timezone.utc),
        )
        future_correction = replace(
            self.rows[0], close=940, low=935, high=940, revision_id="bar-0-later",
            available_at=(self.open + timedelta(minutes=5)).isoformat(),
        )
        result = evaluate_panic_rebound(
            evaluation_bar=self.rows[-1], bar_history=(*self.rows, self.bar(4, 700), future_correction),
            rules=self.rules, news_proofs=(future_adverse, self.proof),
        )
        self.assertEqual(("ENTER", "panic_decline_reached"), (result.action, result.reason))
        self.assertEqual((900, 895, 945), (result.setup.trigger_close, result.setup.failure_price, result.setup.target_price))
        self.assertEqual(("bar-0", "bar-1", "bar-2", "bar-3"), result.setup.input_refs)
        self.assertEqual((*result.setup.input_refs, "article-r1", "assessment-r1", "body-r1", "rule-r1"), result.input_refs)

    def test_p1_arms_then_requires_the_next_positive_completed_close(self) -> None:
        rules = replace(self.rules, variant="P1")
        armed = evaluate_panic_rebound(evaluation_bar=self.rows[-1], bar_history=self.rows, rules=rules)
        self.assertEqual("ARM", armed.action)
        for minute, close, action, reason in (
            (4, 910, "ENTER", "panic_next_completed_close_recovered"),
            (4, 900, "NO_TRADE", "panic_positive_close_recovery_unconfirmed"),
            (5, 920, "NO_TRADE", "panic_next_completed_close_unproven"),
        ):
            with self.subTest(minute=minute, close=close):
                result = evaluate_panic_rebound(
                    evaluation_bar=self.bar(minute, close, high=970), bar_history=self.rows,
                    rules=rules, news_proofs=(self.proof,), setup=armed.setup,
                )
                self.assertEqual((action, reason), (result.action, result.reason))
                if action == "ENTER":
                    self.assertIs(armed.setup, result.setup)
                    self.assertEqual("bar-4", result.input_refs[4])

    def test_p1_cannot_resurrect_an_observed_trigger_after_its_prefix_is_revised(self) -> None:
        rules = replace(self.rules, variant="P1")
        armed = evaluate_panic_rebound(evaluation_bar=self.rows[-1], bar_history=self.rows, rules=rules)
        recovery = self.bar(4, 910)
        correction = replace(
            self.rows[0], revision_id="bar-0-revised", capture_quality="partial",
            available_at=(self.open + timedelta(minutes=4, seconds=30)).isoformat(),
        )
        adverse = replace(
            self.proof, revision="adverse-r2", disposition="fundamental_adverse",
            available_at=self.open + timedelta(minutes=4, seconds=30),
        )
        for prefix, proofs, reason in (
            ((*self.rows, correction), (self.proof,), "panic_trigger_revised_or_unproven"),
            (self.rows, (self.proof, adverse), "panic_fundamental_adverse"),
        ):
            with self.subTest(reason=reason):
                result = evaluate_panic_rebound(
                    evaluation_bar=recovery, bar_history=prefix, rules=rules, news_proofs=proofs, setup=armed.setup,
                )
                self.assertEqual(("NO_TRADE", reason), (result.action, result.reason))
                self.assertIsNone(result.setup)

    def test_entry_requires_supported_news_at_the_decision_cut(self) -> None:
        cutoff = self.open + timedelta(minutes=4)
        unresolved = replace(self.proof, revision="assessment-r2", available_at=cutoff, disposition="unresolved")
        conflict = replace(self.proof, revision="assessment-other", disposition="fundamental_adverse")
        undated = replace(self.proof, revision="assessment-undated", available_at=None)
        previous_day = replace(
            self.proof, effective_at=self.proof.effective_at - timedelta(days=1),
            available_at=self.proof.available_at - timedelta(days=1),
        )
        unsupported = replace(self.proof, revision="unsupported-r2", available_at=cutoff, evidence_refs=())
        unverified_adverse = replace(
            self.proof, revision="adverse-r2", available_at=cutoff, effective_at=None,
            disposition="fundamental_adverse",
        )
        for proofs, reason in (
            ((), "panic_news_unresolved"),
            ((self.proof, unresolved), "panic_news_unresolved"),
            ((self.proof, conflict), "panic_news_conflict"),
            ((self.proof, undated), "panic_news_unresolved"),
            ((previous_day,), "panic_news_unresolved"),
            ((self.proof, unsupported), "panic_news_unresolved"),
            ((self.proof, unverified_adverse), "panic_news_unresolved"),
        ):
            with self.subTest(proofs=proofs):
                result = evaluate_panic_rebound(
                    evaluation_bar=self.rows[-1], bar_history=self.rows, rules=self.rules, news_proofs=proofs,
                )
                self.assertEqual(("NO_TRADE", reason), (result.action, result.reason))
        result = evaluate_panic_rebound(
            evaluation_bar=self.rows[-1], bar_history=self.rows, rules=self.rules, news_proofs=(self.proof, unresolved),
        )
        self.assertIn(unresolved.revision, result.input_refs)
        self.assertNotIn(self.proof.revision, result.input_refs)

    def test_price_window_needs_continuous_current_complete_bars_and_turnover(self) -> None:
        latest_partial = replace(
            self.rows[1], revision_id="bar-1-partial", capture_quality="partial",
            available_at=(self.open + timedelta(minutes=3, seconds=30)).isoformat(),
        )
        auction = replace(self.rows[-1], market_phase="AUCTION_ORDER_ENTRY")
        for bar, history, rules, reason in (
            (self.rows[-1], self.rows[1:], self.rules, "panic_decline_window_unproven"),
            (self.rows[-1], (*self.rows, latest_partial), self.rules, "panic_decline_window_unproven"),
            (auction, self.rows, self.rules, "panic_complete_krx_bar_required"),
            (self.rows[-1], self.rows, replace(self.rules, minimum_turnover_won=3_000_001), "panic_turnover_below_minimum"),
        ):
            with self.subTest(reason=reason):
                result = evaluate_panic_rebound(
                    evaluation_bar=bar, bar_history=history, rules=rules, news_proofs=(self.proof,),
                )
                self.assertEqual(("NO_TRADE", reason), (result.action, result.reason))

    def test_held_position_uses_pinned_prices_and_elapsed_time_without_news(self) -> None:
        entry = evaluate_panic_rebound(
            evaluation_bar=self.rows[-1], bar_history=self.rows, rules=self.rules, news_proofs=(self.proof,),
        )
        opened_at = self.open + timedelta(minutes=4)
        for minute, close, action, reason in (
            (4, 915, "HOLD", "panic_position_within_pinned_range"),
            (5, 895, "EXIT", "panic_failure_close_reached"),
            (5, 945, "EXIT", "panic_rebound_close_reached"),
            (8, 915, "EXIT", "panic_max_hold_reached"),
        ):
            with self.subTest(minute=minute, close=close):
                result = evaluate_panic_rebound(
                    evaluation_bar=self.bar(minute, close, low=800, high=1150), bar_history=(),
                    rules=self.rules, setup=entry.setup, position_opened_at=opened_at,
                )
                self.assertEqual((action, reason), (result.action, result.reason))
                self.assertIs(entry.setup, result.setup)


if __name__ == "__main__":
    unittest.main()
