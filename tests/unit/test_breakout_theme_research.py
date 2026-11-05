from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from kiwoom_monitor.application.breakout_theme_research import (
    BreakoutThemePolicy,
    BreakoutThemePosition,
    ThemeRole,
    ThemeRoleProof,
    evaluate_breakout_theme,
)
from kiwoom_monitor.application.research_replay import KrxMinuteBarFrame
from kiwoom_monitor.domain.time_basis import KST


class BreakoutThemeResearchTests(unittest.TestCase):
    first = datetime(2026, 9, 11, 9, 0, tzinfo=KST)
    symbol = "123456"

    def setUp(self) -> None:
        self.base = BreakoutThemePolicy.from_document({
            "variant": "B0", "quantity": 4, "exit_at_kst": "15:18", "theme_id": None,
            "theme_role": None, "exit_on_dependency_loss": False,
        })
        self.theme = replace(self.base, variant="TH", theme_id="fixture-theme", exit_on_dependency_loss=True)
        self.prior = tuple(self.bar(index) for index in range(11))
        self.signal = self.bar(11, opening=102, close=104, high=110)
        self.proof = self.theme_proof(ThemeRole.DEPENDENT_FOLLOWER, at=self.first, dependency=True)

    def bar(self, minute, *, opening=100, close=100, high=101, available=None):
        start = self.first + timedelta(minutes=minute)
        end = start + timedelta(minutes=1)
        return KrxMinuteBarFrame(
            f"fixture-bar:{minute}", start.isoformat(), self.symbol, start.isoformat(), end.isoformat(),
            (available or end + timedelta(seconds=2)).isoformat(), opening, high, min(opening, close), close,
            100, 1, False, "complete", "fixture", research_session=f"{start.date()}:KRX_REGULAR",
            market_session="KRX_REGULAR", market_phase="CONTINUOUS", schedule_version="fixture:schedule",
        )

    def theme_proof(self, role, *, at, dependency=None, basis="point_in_time", leader="654321"):
        return ThemeRoleProof.from_document({
            "symbol": self.symbol, "theme_id": "fixture-theme", "role": role.value,
            "leader_symbol": self.symbol if role is ThemeRole.OWN_LEADER else leader,
            "effective_at": at.astimezone(timezone.utc).isoformat(), "available_at": at.isoformat(),
            "source": "fixture:theme-source", "revision": f"theme-revision:{role}:{at.isoformat()}",
            "evidence_refs": ["fixture:as-of-membership", "fixture:observed-turnover"],
            "basis": basis, "dependency_valid": dependency,
        })

    def decide(self, *, policy=None, bar=None, history=None, proofs=(), position=None, setup=None):
        return evaluate_breakout_theme(
            policy or self.base, bar=bar or self.signal, history=self.prior if history is None else history,
            proofs=proofs, position=position, setup=setup,
        )

    def test_first_crossing_uses_exact_prior_ten_and_pins_signal_open(self):
        older = replace(self.prior[0], high=120)
        future_revision = replace(self.prior[-1], high=125, revision_id="future-correction",
                                  available_at=(datetime.fromisoformat(self.signal.available_at) + timedelta(seconds=1)).isoformat())
        result = self.decide(history=(older, *self.prior[1:], self.signal, future_revision))
        self.assertEqual(("ENTER", 4), (result.action, result.quantity))
        self.assertEqual((101, 102), (result.setup.breakout_level, result.setup.failure_price))
        self.assertEqual(tuple(row.revision_id for row in self.prior[1:]) + (self.signal.revision_id,), result.evidence_refs)
        self.assertEqual(datetime.fromisoformat(self.signal.available_at), result.setup.signal_available_at)
        self.assertEqual("breakout_prior_high_not_crossed", self.decide(bar=replace(self.signal, close=101, low=101)).reason)

    def test_consecutive_new_high_is_not_a_fresh_crossing(self):
        next_bar = self.bar(12, opening=104, close=111, high=112)
        result = self.decide(bar=next_bar, history=(*self.prior, self.signal))
        self.assertEqual(("WAIT", "breakout_crossing_already_observed"), (result.action, result.reason))

    def test_incomplete_missing_and_wrong_scope_prior_minutes_are_unavailable(self):
        for invalid in (None, replace(self.prior[-1], capture_quality="partial"),
                        replace(self.prior[-1], schedule_version="different-schedule")):
            with self.subTest(invalid=invalid):
                history = self.prior[:-1] + ((invalid,) if invalid is not None else ())
                result = self.decide(history=history)
                self.assertEqual(("WAIT", "breakout_prior_ten_minutes_unavailable"), (result.action, result.reason))

    def test_requires_a_later_actual_fill_and_never_reconstructs_same_bar_execution(self):
        setup = self.decide().setup
        fill_at = self.first + timedelta(minutes=13)
        open_position = BreakoutThemePosition(3, 105, fill_at)
        result = self.decide(position=open_position, setup=setup)
        self.assertEqual(("WAIT", 0, "breakout_fill_outside_observable_execution_cut"),
                         (result.action, result.quantity, result.reason))
        first_fill_bar = self.bar(13, opening=105, close=101, high=106)
        bad = replace(open_position, opened_at=setup.signal_bar_start)
        result = self.decide(bar=first_fill_bar, position=bad, setup=setup)
        self.assertEqual("breakout_fill_outside_observable_execution_cut", result.reason)
        invalid_price = replace(open_position, entry_price=setup.failure_price)
        result = self.decide(bar=first_fill_bar, position=invalid_price, setup=setup)
        self.assertEqual("breakout_initial_risk_distance_unavailable", result.reason)
        result = self.decide(bar=first_fill_bar, position=open_position, setup=setup)
        self.assertEqual(("EXIT", 3, "breakout_pinned_signal_open_failed"), (result.action, result.quantity, result.reason))

    def test_actual_open_position_obeys_its_original_declared_cutoff(self):
        setup = self.decide().setup
        after_cutoff = self.bar(378, opening=105, close=104, high=106)
        result = self.decide(policy=replace(self.base, exit_at_kst="15:19"), bar=after_cutoff,
                             position=BreakoutThemePosition(3, 105, self.first + timedelta(minutes=13)), setup=setup)
        self.assertEqual(("EXIT", 3, "breakout_declared_session_exit"), (result.action, result.quantity, result.reason))

    def test_theme_compares_the_same_breakout_by_as_of_role_and_keeps_source_refs(self):
        base = self.decide()
        for role in ThemeRole:
            with self.subTest(role=role):
                proof = self.theme_proof(role, at=self.first, dependency=True if role is ThemeRole.DEPENDENT_FOLLOWER else None)
                result = self.decide(policy=replace(self.theme, theme_role=role), proofs=(proof,))
                self.assertEqual(("ENTER", base.quantity), (result.action, result.quantity))
                self.assertEqual(base.setup.breakout_level, result.setup.breakout_level)
                self.assertEqual(proof, result.setup.theme_proof)
                self.assertIn("fixture:as-of-membership", result.evidence_refs)
        result = self.decide(policy=replace(self.theme, theme_role=ThemeRole.OWN_LEADER), proofs=(self.proof,))
        self.assertEqual(("WAIT", "theme_role_outside_stratum"), (result.action, result.reason))

    def test_retro_future_and_conflicting_theme_rows_cannot_authorize_the_trigger(self):
        future = replace(self.proof, available_at=self.first + timedelta(minutes=13))
        premature = replace(self.proof, effective_at=self.first + timedelta(minutes=13))
        retrospective = replace(self.proof, basis="retrospective")
        conflict = replace(self.proof, dependency_valid=False, revision="conflicting")
        for proofs in ((future,), (premature,), (retrospective,), (self.proof, conflict),
                       (self.proof, replace(conflict, available_at=None))):
            with self.subTest(proofs=proofs):
                result = self.decide(policy=self.theme, proofs=proofs)
                self.assertEqual(("WAIT", "theme_role_not_known_at_signal"), (result.action, result.reason))
        supported = self.decide(policy=self.theme, proofs=(self.proof, future, retrospective))
        self.assertEqual("ENTER", supported.action)

    def test_dependency_loss_is_optional_and_observed_transition_keeps_original_setup(self):
        setup = self.decide(policy=self.theme, proofs=(self.proof,)).setup
        held_without_dependency_exit = self.decide(
            policy=replace(self.theme, exit_on_dependency_loss=False), proofs=(self.proof,),
        ).setup
        fill_at = self.first + timedelta(minutes=13)
        position = BreakoutThemePosition(3, 105, fill_at)
        bar = self.bar(13, opening=105, close=105, high=106)
        loss = self.theme_proof(ThemeRole.DEPENDENT_FOLLOWER, at=fill_at, dependency=False)
        transition = self.theme_proof(ThemeRole.TRANSITION, at=fill_at)
        lost = self.decide(bar=bar, proofs=(self.proof, loss), position=position, setup=setup)
        self.assertEqual(("EXIT", 3, "theme_pinned_dependency_lost"), (lost.action, lost.quantity, lost.reason))
        unchanged = self.decide(bar=bar, proofs=(self.proof, loss), position=position, setup=held_without_dependency_exit)
        self.assertEqual("HOLD", unchanged.action)
        released = self.decide(bar=bar, proofs=(self.proof, transition), position=position, setup=setup)
        self.assertEqual("theme_dependency_released_by_observed_transition", released.reason)
        self.assertEqual(self.proof, released.setup.theme_proof)
        self.assertEqual(transition, released.setup.transition_proof)
        later = self.bar(14, opening=105, close=105, high=106)
        later_loss = replace(loss, available_at=fill_at + timedelta(minutes=1))
        self.assertEqual("HOLD", self.decide(bar=later, proofs=(transition, later_loss),
                                             position=position, setup=released.setup).action)
