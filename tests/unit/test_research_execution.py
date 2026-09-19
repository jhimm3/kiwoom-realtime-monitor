from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from kiwoom_monitor.application.breakout_strategy import (
    BreakoutStrategyConfig,
    StrategyDecision,
    StrategyState,
)
from kiwoom_monitor.application.research_execution import (
    PaperExecutionEngine,
    SimulationCostModel,
    SimulationExecutionConfig,
)
from kiwoom_monitor.application.market_session_schedule import KRX_FULL_DAY_RESEARCH_PROFILE
from kiwoom_monitor.application.research_replay import KrxMinuteBarFrame


UTC = timezone.utc


def _strategy() -> BreakoutStrategyConfig:
    return BreakoutStrategyConfig(
        "v1", "v1", "v1", 2, 0, False, False, 1, 60, 30, 0,
        500, 500, 10, 10, 1_000_000, 60, 30,
    )


def _execution(*, commission: int = 0, tax: int = 0, cost=True) -> SimulationExecutionConfig:
    return SimulationExecutionConfig(
        "next_tradable_bar_open/v1", "conservative_with_optimistic_bound/v1", 100_000,
        SimulationCostModel("fixed_bps/v1", commission, tax, 0) if cost else None,
    )


def _decision(at: str = "2026-09-12T00:03:02+00:00") -> StrategyDecision:
    state = StrategyState(status="candidate", symbol="005930").to_dict()
    return StrategyDecision(
        decision_id="decision-enter", run_id="run-1", snapshot_id="snapshot-1",
        symbol="005930", proposal="ENTER", final_action="ENTER", quantity=10,
        signal_reference_price=1000, required_capital_won=10_000,
        reasons=("rolling_high_breakout",), constraints=("max_one_position",),
        state_before=StrategyState().to_dict(), state_after=state, decided_at=at,
    )


def _bar(minute: int, *, open_: int = 1000, low: int = 999, high: int = 1001) -> KrxMinuteBarFrame:
    start = datetime(2026, 9, 12, 0, minute, tzinfo=UTC)
    end = start + timedelta(minutes=1)
    return KrxMinuteBarFrame(
        revision_id=f"bar-{minute}", observation_key=start.isoformat(), code="005930",
        bar_start=start.isoformat(), bar_end=end.isoformat(),
        available_at=(end + timedelta(seconds=2)).isoformat(), open=open_, high=high,
        low=low, close=open_, volume=100, trade_value_million_won=1,
        session_finalized=False, capture_quality="complete", finalization_source="timer",
    )


def _profile_bar(start: str, *, session: str, phase: str) -> KrxMinuteBarFrame:
    at = datetime.fromisoformat(start)
    return KrxMinuteBarFrame(
        revision_id=f"bar-{start}", observation_key=start, code="005930",
        bar_start=start, bar_end=(at + timedelta(minutes=1)).isoformat(),
        available_at=(at + timedelta(minutes=1, seconds=2)).isoformat(),
        open=1000, high=1010, low=990, close=1000, volume=1,
        trade_value_million_won=1, session_finalized=False,
        capture_quality="complete", finalization_source="timer",
        session_profile=KRX_FULL_DAY_RESEARCH_PROFILE, research_session=session,
        market_session=session.split(":")[-1], market_phase=phase,
        schedule_version="krx-nxt-schedule/2026-09-14",
    )


class ResearchExecutionTests(unittest.TestCase):
    def test_unregistered_execution_and_cost_models_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unregistered cost model"):
            SimulationCostModel("unknown", 0, 0, 0)
        with self.assertRaisesRegex(ValueError, "unregistered execution model"):
            SimulationExecutionConfig(
                "unknown", "conservative_with_optimistic_bound/v1", 1000,
                SimulationCostModel("fixed_bps/v1", 0, 0, 0),
            )

    def test_same_inputs_produce_same_execution_events_and_state(self) -> None:
        def run_once():
            engine = PaperExecutionEngine("run-1", _execution(), _strategy())
            submitted = engine.process_decision(
                _decision(), StrategyState(status="candidate", symbol="005930"),
            )
            filled = engine.process_bar(_bar(4, open_=1000, low=900, high=1100))
            return submitted + filled, engine.portfolio, engine.strategy_state

        self.assertEqual(run_once(), run_once())

    def test_fill_waits_for_first_bar_start_after_decision_time(self) -> None:
        engine = PaperExecutionEngine("run-1", _execution(), _strategy())
        submitted = engine.process_decision(
            _decision(), StrategyState(status="candidate", symbol="005930"),
        )
        skipped = engine.process_bar(_bar(3))
        filled = engine.process_bar(_bar(4))

        self.assertEqual("ORDER_SUBMITTED", submitted[0].event_type)
        self.assertEqual((), skipped)
        self.assertEqual("FILL", filled[0].event_type)
        self.assertEqual("2026-09-12T00:04:00+00:00", filled[0].occurred_at)
        self.assertEqual("open", engine.strategy_state.status)

    def test_cash_reservation_and_fill_never_exceed_available_cash(self) -> None:
        execution = SimulationExecutionConfig(
            "next_tradable_bar_open/v1", "conservative_with_optimistic_bound/v1", 5_000,
            SimulationCostModel("fixed_bps/v1", 0, 0, 0),
        )
        engine = PaperExecutionEngine("run-1", execution, _strategy())
        events = engine.process_decision(
            _decision(), StrategyState(status="candidate", symbol="005930"),
        )
        self.assertEqual("ORDER_REJECTED", events[0].event_type)
        self.assertEqual("cash_reservation_exceeded", events[0].reason)
        self.assertEqual(0, engine.portfolio.reserved_cash_won)

    def test_same_bar_stop_and_target_uses_stop_and_keeps_optimistic_bound(self) -> None:
        engine = PaperExecutionEngine("run-1", _execution(), _strategy())
        engine.process_decision(_decision(), StrategyState(status="candidate", symbol="005930"))
        events = engine.process_bar(_bar(4, open_=1000, low=900, high=1100))

        self.assertEqual(["FILL", "RISK_FILL"], [event.event_type for event in events])
        self.assertEqual(950, events[1].price)
        self.assertEqual(1050, events[1].optimistic_exit_price)
        self.assertEqual(-500, events[1].realized_pnl_won)
        self.assertEqual(99_500, engine.portfolio.cash_won)
        self.assertEqual("cooldown", engine.strategy_state.status)

    def test_explicit_cost_increase_reduces_realized_result(self) -> None:
        def result(commission: int, tax: int) -> int:
            engine = PaperExecutionEngine(
                "run-1", _execution(commission=commission, tax=tax), _strategy(),
            )
            engine.process_decision(
                _decision(), StrategyState(status="candidate", symbol="005930"),
            )
            engine.process_bar(_bar(4, open_=1000, low=900, high=1100))
            return engine.portfolio.realized_pnl_won

        self.assertLess(result(10, 20), result(0, 0))

    def test_missing_cost_model_marks_fill_unsupported(self) -> None:
        engine = PaperExecutionEngine("run-1", _execution(cost=False), _strategy())
        engine.process_decision(_decision(), StrategyState(status="candidate", symbol="005930"))
        events = engine.process_bar(_bar(4))
        self.assertEqual("ORDER_UNSUPPORTED", events[0].event_type)
        self.assertEqual("cost_model_missing", events[0].reason)

    def test_profiled_pending_does_not_cross_regular_to_after_or_trading_day(self) -> None:
        regular_source = _profile_bar(
            "2026-09-14T06:28:00+00:00", session="2026-09-14:KRX_REGULAR",
            phase="CONTINUOUS",
        )
        after = _profile_bar(
            "2026-09-14T07:00:00+00:00", session="2026-09-14:KRX_AFTER",
            phase="CONTINUOUS",
        )
        decision = _decision("2026-09-14T06:29:02+00:00")
        engine = PaperExecutionEngine(
            "run-1", _execution(), _strategy(),
            session_profile=KRX_FULL_DAY_RESEARCH_PROFILE,
        )
        engine.process_decision(
            decision, StrategyState(status="candidate", symbol="005930"),
            source_bar=regular_source,
        )
        [event] = engine.process_bar(after)
        self.assertEqual(("ORDER_CENSORED", "session_boundary_before_fill"),
                         (event.event_type, event.reason))
        self.assertIsNone(engine.portfolio.pending_order)

        next_day = _profile_bar(
            "2026-09-15T00:00:00+00:00", session="2026-09-15:KRX_REGULAR",
            phase="CONTINUOUS",
        )
        engine = PaperExecutionEngine(
            "run-2", _execution(), _strategy(),
            session_profile=KRX_FULL_DAY_RESEARCH_PROFILE,
        )
        engine.process_decision(
            decision, StrategyState(status="candidate", symbol="005930"),
            source_bar=regular_source,
        )
        self.assertEqual("ORDER_CENSORED", engine.process_bar(next_day)[0].event_type)

    def test_profiled_next_bar_open_is_unsupported_for_auction_bar(self) -> None:
        source = _profile_bar(
            "2026-09-14T06:18:00+00:00", session="2026-09-14:KRX_REGULAR",
            phase="CONTINUOUS",
        )
        auction = _profile_bar(
            "2026-09-14T06:20:00+00:00", session="2026-09-14:KRX_REGULAR",
            phase="AUCTION_ORDER_ENTRY",
        )
        engine = PaperExecutionEngine(
            "run-1", _execution(), _strategy(),
            session_profile=KRX_FULL_DAY_RESEARCH_PROFILE,
        )
        engine.process_decision(
            _decision("2026-09-14T06:19:02+00:00"),
            StrategyState(status="candidate", symbol="005930"), source_bar=source,
        )
        [event] = engine.process_bar(auction)
        self.assertEqual("ORDER_UNSUPPORTED", event.event_type)
        self.assertEqual("minute_bar_phase_cannot_prove_next_open_fill", event.reason)

        fixed = _profile_bar(
            "2026-09-14T06:40:00+00:00", session="",
            phase="FIXED_PRICE",
        )
        engine = PaperExecutionEngine(
            "run-2", _execution(), _strategy(),
            session_profile=KRX_FULL_DAY_RESEARCH_PROFILE,
        )
        engine.process_decision(
            _decision("2026-09-14T06:19:02+00:00"),
            StrategyState(status="candidate", symbol="005930"), source_bar=source,
        )
        event = engine.process_bar(fixed)[0]
        self.assertEqual("ORDER_CENSORED", event.event_type)
        self.assertNotEqual("FILL", event.event_type)


if __name__ == "__main__":
    unittest.main()
