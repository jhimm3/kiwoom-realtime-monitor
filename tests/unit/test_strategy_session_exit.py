from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
import json
import unittest
from unittest.mock import patch

from kiwoom_monitor.application.breakout_strategy import (
    REGULAR_SESSION_EXIT_POLICY, StrategyState, evaluate_factor_entry_bar,
    regular_session_fill_deadline, session_entry_time_rejection,
)
from kiwoom_monitor.application.market_session_schedule import KST, KRX_REGULAR_RESEARCH_PROFILE
from kiwoom_monitor.application.research_execution import PaperExecutionEngine
from kiwoom_monitor.application.research_factors import FactorValue
from kiwoom_monitor.application.research_families import parse_strategy_config
from kiwoom_monitor.application.research_replay import CandidateUniverseFrame
from scripts.prepare_historical_baseline_requests import _strategies, prepare_daily_plan, prepare_daily_requests
from tests.unit import test_research_execution as execution_fixtures
from tests.unit import test_historical_baseline_requests as request_fixtures


def configured_families():
    return [parse_strategy_config(family, parameters) for family, _, parameters in _strategies(60)]


def bar_at(at, *, start=None, code="005930", low=990):
    now = datetime.fromisoformat(at)
    first = datetime.fromisoformat(start) if start else now - timedelta(minutes=1)
    bar = execution_fixtures._profile_bar(first.isoformat(), session=f"{first.date()}:KRX_REGULAR", phase="CONTINUOUS")
    return replace(bar, bar_end=now.isoformat(), available_at=now.isoformat(), code=code, low=low)


def evaluate(config, at, state=StrategyState(), *, code="005930"):
    bar = bar_at(at, code=code)
    factor = FactorValue("rolling_high_breakout", "v1", "fixture", code, at, at,
                         {"breakout": True, "reference_revision_id": "previous-bar"}, "valid", "", ("previous-bar",))
    return evaluate_factor_entry_bar(run_id="run-1", evaluation_bar=bar,
        universe_frames=(CandidateUniverseFrame("universe", at, at, (code,)),), config=config,
        state=state, factors=(factor,), strategy_id="fixture", setup="fixture", signal_key="breakout",
        negative_reason="missing", positive_reason="confirmed")


def filled_legacy_position():
    config = execution_fixtures._strategy()
    engine = PaperExecutionEngine("run-1", execution_fixtures._execution(), config,
                                  session_profile=KRX_REGULAR_RESEARCH_PROFILE)
    decision = execution_fixtures._decision("2026-09-11T15:13:00+09:00")
    engine.process_decision(decision, StrategyState(status="candidate", symbol="005930"))
    entry = bar_at("2026-09-11T15:14:00+09:00", start="2026-09-11T15:13:00+09:00")
    engine.process_bar(entry)
    assert engine.portfolio.position is not None
    engine.strategy = replace(config, session_exit_policy=REGULAR_SESSION_EXIT_POLICY, session_exit_buffer_seconds=60)
    return engine


class SessionExitPolicyTests(unittest.TestCase):
    def test_old_configuration_roundtrip_omits_policy_and_new_is_explicit(self):
        for family, _, parameters in _strategies():
            legacy = parse_strategy_config(family, parameters)
            self.assertEqual(parameters, legacy.to_dict())
            guarded = replace(legacy, session_exit_policy=REGULAR_SESSION_EXIT_POLICY, session_exit_buffer_seconds=60)
            self.assertEqual(guarded, parse_strategy_config(family, guarded.to_dict()))
            self.assertEqual(60, guarded.to_dict()["session_exit_buffer_seconds"])

    def test_unknown_policy_incomplete_policy_and_invalid_buffers_fail(self):
        for config in configured_families():
            for policy, seconds in (("unknown/v1", 60), ("", 60), (REGULAR_SESSION_EXIT_POLICY, 0),
                                    (REGULAR_SESSION_EXIT_POLICY, 59), (REGULAR_SESSION_EXIT_POLICY, True),
                                    (REGULAR_SESSION_EXIT_POLICY, 3601)):
                with self.subTest(policy=policy, seconds=seconds), self.assertRaises(ValueError):
                    replace(config, session_exit_policy=policy, session_exit_buffer_seconds=seconds)

    def test_entry_boundary_includes_signal_validity_hold_and_exit_buffer(self):
        for config in configured_families():
            earlier = evaluate(config, "2026-09-11T15:07:59+09:00")
            boundary = evaluate(config, "2026-09-11T15:08:00+09:00")
            self.assertEqual("ENTER", earlier.decision.final_action)
            self.assertEqual("NO_TRADE", boundary.decision.final_action)
            self.assertEqual(("session_entry_budget_exhausted",), boundary.decision.reasons)
            self.assertIsNone(boundary.candidate_event)
            self.assertEqual(REGULAR_SESSION_EXIT_POLICY, earlier.snapshot.policy_version)

    def test_pending_candidate_cannot_hold_its_slot_past_entry_budget(self):
        state = StrategyState(status="candidate", symbol="005930", candidate_key="seen",
                              candidate_expires_at="2026-09-11T15:09:00+09:00", emitted_candidate_keys=("seen",))
        result = evaluate(configured_families()[0], "2026-09-11T15:08:00+09:00", state)
        self.assertEqual("flat", result.state.status)
        self.assertEqual(("seen",), result.state.emitted_candidate_keys)

    def test_entry_outside_regular_continuous_session_is_blocked(self):
        config = configured_families()[0]
        for at in ("2026-09-11T08:59:59+09:00", "2026-09-11T15:20:00+09:00",
                   "2026-09-11T16:00:00+09:00", "2026-09-12T09:00:00+09:00"):
            with self.subTest(at=at):
                self.assertEqual("outside_regular_continuous_session", session_entry_time_rejection(config, datetime.fromisoformat(at)))

    def test_delayed_trading_day_uses_verified_open_and_auction_boundary(self):
        config = configured_families()[0]
        self.assertEqual("16:20:00", regular_session_fill_deadline(datetime(2025, 11, 13, tzinfo=KST)).strftime("%H:%M:%S"))
        self.assertEqual("outside_regular_continuous_session", session_entry_time_rejection(config, datetime.fromisoformat("2025-11-13T09:59:59+09:00")))
        self.assertEqual("", session_entry_time_rejection(config, datetime.fromisoformat("2025-11-13T16:07:59+09:00")))
        self.assertEqual("session_entry_budget_exhausted", session_entry_time_rejection(config, datetime.fromisoformat("2025-11-13T16:08:00+09:00")))

    def test_naive_deadline_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            regular_session_fill_deadline(datetime(2026, 9, 11))

    def test_exit_before_auction_remains_open_until_fill_and_respects_owner(self):
        state = StrategyState(status="open", symbol="005930", entry_price=1000,
                              position_quantity=1, opened_at="2026-09-11T15:13:00+09:00")
        for config in configured_families():
            before = evaluate(config, "2026-09-11T15:18:59+09:00", state)
            due = evaluate(config, "2026-09-11T15:19:00+09:00", state)
            other = evaluate(config, "2026-09-11T15:19:00+09:00", state, code="000660")
            self.assertEqual("HOLD", before.decision.final_action)
            self.assertEqual(("session_exit_due",), due.decision.reasons)
            self.assertEqual("EXIT", due.decision.final_action)
            self.assertEqual(state, due.state)
            self.assertEqual("HOLD", other.decision.final_action)

    def test_exit_deadline_uses_position_entry_date_not_next_day(self):
        state = StrategyState(status="open", symbol="005930", entry_price=1000,
                              position_quantity=1, opened_at="2026-09-11T15:13:00+09:00")
        due = evaluate(configured_families()[0], "2026-09-14T09:00:00+09:00", state)
        self.assertIn("session_exit_due", due.decision.reasons)
        self.assertEqual("open", due.state.status)

    def test_execution_rejects_raw_late_enter_and_keeps_cash(self):
        engine = PaperExecutionEngine("run-1", execution_fixtures._execution(), configured_families()[0])
        event, = engine.process_decision(execution_fixtures._decision("2026-09-11T15:13:00+09:00"),
                                        StrategyState(status="candidate", symbol="005930"))
        self.assertEqual(("ORDER_REJECTED", "session_entry_budget_exhausted"), (event.event_type, event.reason))
        self.assertIsNone(engine.portfolio.pending_order)
        self.assertEqual(100_000, engine.portfolio.cash_won)
        self.assertEqual("flat", engine.strategy_state.status)

    def test_normal_pre_boundary_exit_uses_actual_next_open(self):
        engine = filled_legacy_position()
        evaluation = evaluate(engine.strategy, "2026-09-11T15:19:00+09:00", engine.strategy_state)
        engine.process_decision(evaluation.decision, evaluation.state)
        events = engine.process_bar(bar_at("2026-09-11T15:20:00+09:00", start="2026-09-11T15:19:00+09:00"))
        self.assertEqual("FILL", events[0].event_type)
        self.assertEqual("2026-09-11T15:19:00+09:00", events[0].occurred_at)
        self.assertIsNone(engine.portfolio.position)

    def test_missing_pre_boundary_bar_keeps_censored_position(self):
        engine = filled_legacy_position()
        evaluation = evaluate(engine.strategy, "2026-09-11T15:19:00+09:00", engine.strategy_state)
        engine.process_decision(evaluation.decision, evaluation.state)
        events = engine.process_bar(bar_at("2026-09-11T15:21:00+09:00", start="2026-09-11T15:20:00+09:00", low=900))
        self.assertEqual("regular_session_fill_deadline_passed", events[0].reason)
        self.assertFalse(any(e.event_type in {"FILL", "RISK_FILL"} for e in events))
        self.assertIsNotNone(engine.portfolio.position)
        self.assertEqual("POSITION_CENSORED", engine.finalize("2026-09-11T15:30:00+09:00")[-1].event_type)

    def test_intrabar_stop_at_cutoff_does_not_fabricate_timely_fill(self):
        engine = filled_legacy_position()
        events = engine.process_bar(bar_at("2026-09-11T15:20:00+09:00", start="2026-09-11T15:19:00+09:00", low=900))
        self.assertEqual("RISK_UNSUPPORTED", events[0].event_type)
        self.assertEqual("regular_session_fill_deadline_unproven", events[0].reason)
        self.assertIsNotNone(engine.portfolio.position)

    def test_recovered_position_cannot_fill_before_regular_open(self):
        engine = filled_legacy_position()
        evaluation = evaluate(engine.strategy, "2026-09-14T08:59:00+09:00", engine.strategy_state)
        engine.process_decision(evaluation.decision, evaluation.state)
        events = engine.process_bar(bar_at("2026-09-14T09:00:00+09:00", start="2026-09-14T08:59:00+09:00"))
        self.assertFalse(any(e.event_type in {"FILL", "RISK_FILL"} for e in events))
        self.assertIsNotNone(engine.portfolio.position)

    def test_actual_buy_fill_does_not_add_signal_validity_twice(self):
        engine = PaperExecutionEngine("run-1", execution_fixtures._execution(), configured_families()[0],
                                      session_profile=KRX_REGULAR_RESEARCH_PROFILE)
        engine.process_decision(execution_fixtures._decision("2026-09-11T15:07:59+09:00"),
                                StrategyState(status="candidate", symbol="005930"))
        events = engine.process_bar(bar_at("2026-09-11T15:09:00+09:00", start="2026-09-11T15:08:00+09:00"))
        self.assertEqual("FILL", events[0].event_type)
        self.assertEqual("BUY", events[0].side)


class DailyPolicyPlanTests(unittest.TestCase):
    setUp = request_fixtures.DailyDevelopmentRequestTests.setUp

    def test_policy_variant_preserves_source_dates_costs_and_other_strategy_settings(self):
        baseline = json.loads(prepare_daily_plan(self.selection, self.output).read_text(encoding="utf-8"))
        paths = prepare_daily_requests(self.selection, self.source, self.root / "variant", regular_exit_buffer_seconds=60)
        variant = json.loads((self.root / "variant" / "daily-plan.json").read_text(encoding="utf-8"))
        for key in ("selection", "evaluation", "execution", "state_policy", "source_selection_sha256"):
            self.assertEqual(baseline[key], variant[key])
        for a, b in zip(baseline["strategies"], variant["strategies"], strict=True):
            parameters = dict(b["strategy"])
            self.assertEqual(REGULAR_SESSION_EXIT_POLICY, parameters.pop("session_exit_policy"))
            self.assertEqual(60, parameters.pop("session_exit_buffer_seconds"))
            self.assertEqual(a["strategy"], parameters)
        self.assertTrue(all(json.loads(p.read_text())["request"]["strategy"]["session_exit_buffer_seconds"] == 60 for p in paths))

    def test_conflicting_policy_does_not_overwrite_locked_plan(self):
        path = prepare_daily_plan(self.selection, self.output)
        before = path.read_bytes()
        with self.assertRaises(ValueError): prepare_daily_plan(self.selection, self.output, regular_exit_buffer_seconds=60)
        self.assertEqual(before, path.read_bytes())

    def test_invalid_policy_buffer_fails_before_reading_input(self):
        with patch("scripts.prepare_historical_baseline_requests.load_research_input", side_effect=AssertionError("no input")):
            with self.assertRaises(ValueError): prepare_daily_requests(self.selection, self.source, self.output, regular_exit_buffer_seconds=59)


if __name__ == "__main__":
    unittest.main()
