from __future__ import annotations

import unittest
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone

from kiwoom_monitor.application.breakout_theme_research import (
    BreakoutEntryProof,
    BreakoutThemePolicy,
    BreakoutThemePosition,
    ReconstructedMarketTurnoverRatio,
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

    def decide(self, *, policy=None, bar=None, history=None, proofs=(), position=None, setup=None, entry_proofs=(),
               research_context=False):
        return evaluate_breakout_theme(
            policy or self.base, bar=bar or self.signal, history=self.prior if history is None else history,
            proofs=proofs, position=position, setup=setup, entry_proofs=entry_proofs, research_context=research_context,
        )

    def filtered(self, variant, **changes):
        filters = {"minimum_price_progress_bps": 400, "minimum_symbol_relative_turnover_bps": 20_000}
        if variant in {"B2", "B3"}:
            filters.update(market_scope="fixture:KRX-TOP20", minimum_market_relative_turnover_bps=15_000)
        if variant == "B3":
            filters.update(minimum_daily_headroom_bps=1000, require_catalyst=True)
        filters.update(changes)
        return BreakoutThemePolicy.from_document({**asdict(self.base), "variant": variant, "entry_filters": filters})

    def entry_proof(self, kind, value, **changes):
        return BreakoutEntryProof.from_document({
            "kind": kind, "scope": "fixture:KRX-TOP20" if kind == "market_relative_turnover" else self.symbol,
            "effective_at": self.signal.bar_end if kind == "market_relative_turnover" else self.first.isoformat(),
            "available_at": self.signal.available_at, "source": f"fixture:{kind}", "revision": f"revision:{kind}",
            "evidence_refs": [f"evidence:{kind}"], "basis": "point_in_time", "value": value, **changes,
        })

    def market_proof(self, observed=150, baseline=100, **changes):
        return self.entry_proof("market_relative_turnover", {
            "observed_turnover_won": observed, "baseline_median_turnover_won": baseline,
        }, **changes)

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

    def test_original_policy_documents_keep_shape_and_filtered_fields_are_explicit(self):
        for policy in (self.base, self.theme):
            document = asdict(policy)
            self.assertNotIn("entry_filters", document)
            self.assertEqual(policy, BreakoutThemePolicy.from_document(document))
        for variant in ("B1", "B2", "B3"):
            policy = self.filtered(variant)
            self.assertEqual(policy, BreakoutThemePolicy.from_document(asdict(policy)))
            with self.assertRaisesRegex(ValueError, "invalid breakout research policy fields"):
                BreakoutThemePolicy.from_document({**asdict(self.base), "variant": variant})
        with self.assertRaisesRegex(ValueError, "invalid breakout entry filter fields"):
            self.filtered("B1", market_scope="out-of-variant")
        with self.assertRaisesRegex(ValueError, "headroom or catalyst"):
            self.filtered("B3", minimum_daily_headroom_bps=None, require_catalyst=False)

    def test_b1_compares_native_symbol_turnover_against_same_prior_ten_and_price_progress(self):
        policy = self.filtered("B1")
        bar = replace(self.signal, trade_value_million_won=2)
        other = replace(self.prior[-1], code="654321", trade_value_million_won=10_000)
        future = replace(self.prior[-1], revision_id="later-turnover", trade_value_million_won=10_000,
                         available_at=(datetime.fromisoformat(self.signal.available_at) + timedelta(seconds=1)).isoformat())
        decision = self.decide(policy=policy, bar=bar, history=(*self.prior, other, future))
        self.assertEqual(("ENTER", 4, 101, 102), (decision.action, decision.quantity,
                         decision.setup.breakout_level, decision.setup.failure_price))
        self.assertEqual((100, 2, 10), (decision.setup.previous_close_won,
                         decision.setup.signal_turnover_million_won, decision.setup.prior_turnover_sum_million_won))
        slow = self.decide(policy=self.filtered("B1", minimum_price_progress_bps=401), bar=bar)
        self.assertEqual("breakout_price_progress_below_threshold", slow.reason)
        weak = self.decide(policy=policy, bar=self.signal)
        self.assertEqual("breakout_symbol_relative_turnover_below_threshold", weak.reason)
        zero = tuple(replace(row, trade_value_million_won=0) for row in self.prior)
        self.assertEqual("breakout_symbol_turnover_baseline_unavailable", self.decide(policy=policy, bar=bar, history=zero).reason)
        reconstructed = replace(bar, finalization_source="daishin_completed_history_job")
        self.assertEqual("ENTER", self.decide(policy=policy, bar=reconstructed).action)
        invalid = replace(reconstructed, trade_value_million_won=-1)
        self.assertEqual("breakout_symbol_turnover_unavailable", self.decide(policy=policy, bar=invalid).reason)

    def test_b2_requires_same_market_scope_cut_native_ratio_and_as_of_revision(self):
        policy = self.filtered("B2")
        bar = replace(self.signal, trade_value_million_won=2)
        proof = self.market_proof()
        future = self.market_proof(10_000, available_at=(datetime.fromisoformat(bar.available_at) + timedelta(seconds=1)).isoformat())
        selected = self.decide(policy=policy, bar=bar, entry_proofs=(proof, future))
        self.assertEqual(("ENTER", (proof,)), (selected.action, selected.setup.entry_proofs))
        self.assertIn(proof.revision, selected.evidence_refs)
        for unavailable in (
            (future,), (self.market_proof(scope="fixture:whole-market"),),
            (self.market_proof(effective_at=self.prior[-1].bar_end),), (self.market_proof(baseline=0),),
            (proof, self.market_proof(250, revision="conflicting-turnover")),
            (self.market_proof(basis="retrospective"),),
        ):
            with self.subTest(unavailable=unavailable):
                self.assertEqual("breakout_market_turnover_unavailable",
                                 self.decide(policy=policy, bar=bar, entry_proofs=unavailable).reason)
        weak = self.decide(policy=policy, bar=bar, entry_proofs=(self.market_proof(149),))
        self.assertEqual("breakout_market_relative_turnover_below_threshold", weak.reason)

    def test_reconstructed_b2_proxy_requires_the_exact_replay_cut_and_preserves_pit_default(self):
        scope = "daishin_creon:StockChart:U001"
        policy = self.filtered("B2", market_scope=scope)
        end = datetime.fromisoformat(self.signal.bar_end)
        archive_at = end + timedelta(days=10)
        proxy = ReconstructedMarketTurnoverRatio(scope, end, end, archive_at, 151, 201, ("archived-row",))
        replay = replace(self.signal, available_at=self.signal.bar_end, trade_value_million_won=2,
                         finalization_source="daishin_completed_history_job")

        def decide(bar, value):
            return evaluate_breakout_theme(policy, bar=bar, history=self.prior, proofs=(), position=None, setup=None,
                                           reconstructed_market_proxy=value)

        accepted = decide(replay, proxy)
        self.assertEqual(("ENTER", (proxy,)), (accepted.action, accepted.setup.entry_proofs))
        self.assertIn("archived-row", accepted.evidence_refs)
        self.assertEqual("breakout_market_relative_turnover_below_threshold",
                         decide(replay, replace(proxy, observed_raw=150)).reason)
        self.assertEqual("breakout_market_turnover_unavailable",
                         decide(replay, replace(proxy, scope="daishin_creon:StockChart:U201")).reason)
        self.assertEqual("breakout_market_turnover_unavailable",
                         decide(replace(replay, available_at=self.signal.available_at), proxy).reason)
        self.assertEqual("breakout_market_turnover_unavailable",
                         decide(replace(replay, finalization_source="fixture-native"), proxy).reason)
        self.assertEqual("breakout_market_turnover_unavailable", decide(replay, None).reason)

    def test_b3_compares_context_separately_and_never_uses_late_or_reconstructed_context(self):
        bar = replace(self.signal, trade_value_million_won=2)
        policy = self.filtered("B3")
        market = self.market_proof()
        daily = self.entry_proof("daily_headroom", 115)
        catalyst = self.entry_proof("catalyst", "supported")
        good = self.decide(policy=policy, bar=bar, entry_proofs=(market, daily, catalyst))
        self.assertEqual(("ENTER", (market, daily, catalyst)), (good.action, good.setup.entry_proofs))
        for context, reason in (
            ((catalyst,), "breakout_daily_headroom_unavailable"),
            ((replace(daily, value=110), catalyst), "breakout_daily_headroom_below_threshold"),
            ((daily, replace(catalyst, basis="retrospective")), "breakout_catalyst_unavailable"),
            ((daily, replace(catalyst, available_at=None)), "breakout_catalyst_unavailable"),
            ((daily, replace(catalyst, value="unsupported")), "breakout_catalyst_not_supported"),
        ):
            with self.subTest(context=context):
                self.assertEqual(reason, self.decide(policy=policy, bar=bar, entry_proofs=(market, *context)).reason)
        self.assertEqual("ENTER", self.decide(policy=self.filtered("B3", require_catalyst=False), bar=bar,
                                              entry_proofs=(market, daily)).action)
        self.assertEqual("ENTER", self.decide(policy=self.filtered("B3", minimum_daily_headroom_bps=None), bar=bar,
                                              entry_proofs=(market, catalyst)).action)

    def test_filtered_hold_protection_uses_original_fill_and_failure_without_context(self):
        policy = self.filtered("B3")
        signal = replace(self.signal, trade_value_million_won=2)
        setup = self.decide(policy=policy, bar=signal, entry_proofs=(self.market_proof(),
            self.entry_proof("daily_headroom", 115), self.entry_proof("catalyst", "supported"))).setup
        position = BreakoutThemePosition(3, 105, self.first + timedelta(minutes=13))
        failed = self.bar(13, opening=105, close=101, high=106)
        result = self.decide(policy=policy, bar=failed, position=position, setup=setup, entry_proofs=())
        self.assertEqual(("EXIT", 3, "breakout_pinned_signal_open_failed"), (result.action, result.quantity, result.reason))
        self.assertIs(setup, result.setup)

    def test_research_uses_reconstructed_context_at_the_cut_without_later_or_undated_rows(self):
        bar = replace(self.signal, trade_value_million_won=2)
        policy = self.filtered("B3", minimum_daily_headroom_bps=None)
        market = self.market_proof()
        catalyst = self.entry_proof("catalyst", "supported", basis="reconstructed_as_of")
        late = replace(catalyst, value="unsupported", revision="later",
                       available_at=catalyst.available_at + timedelta(seconds=1))
        unknown = replace(catalyst, value="unsupported", revision="undated", available_at=None)
        rows = (market, catalyst, late, unknown)
        self.assertEqual("breakout_catalyst_unavailable", self.decide(policy=policy, bar=bar, entry_proofs=rows).reason)
        known = self.decide(policy=policy, bar=bar, entry_proofs=rows, research_context=True)
        self.assertEqual(("ENTER", (market, catalyst)), (known.action, known.setup.entry_proofs))
        self.assertNotIn(late.revision, known.evidence_refs)
        self.assertNotIn(unknown.revision, known.evidence_refs)
        self.assertEqual("breakout_catalyst_unavailable", self.decide(
            policy=policy, bar=bar, entry_proofs=(market, late, unknown), research_context=True,
        ).reason)
