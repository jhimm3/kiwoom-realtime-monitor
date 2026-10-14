from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from kiwoom_monitor.application.breakout_strategy import (
    BreakoutStrategyConfig,
    StrategyDecision,
    StrategyState,
    evaluate_breakout_bar,
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


def late_exit_trace(*, with_followup=False):
    """늦은 진입→마감 청산 요청→다른 종목만 있는 다음 평가일을 재현한다."""
    engine = PaperExecutionEngine("run-1", _execution(), _strategy(), session_profile=KRX_FULL_DAY_RESEARCH_PROFILE)
    events = list(engine.process_decision(
        _decision("2026-09-14T06:13:00+00:00"), StrategyState(status="candidate", symbol="005930"),
    ))
    fill = _profile_bar("2026-09-14T06:13:00+00:00", session="2026-09-14:KRX_REGULAR", phase="CONTINUOUS")
    events.extend(engine.process_bar(fill))
    last_continuous = _profile_bar("2026-09-14T06:19:00+00:00", session="2026-09-14:KRX_REGULAR", phase="CONTINUOUS")
    events.extend(engine.process_bar(last_continuous))
    close = replace(_profile_bar("2026-09-14T06:20:00+00:00", session="2026-09-14:KRX_REGULAR", phase="AUCTION_ORDER_ENTRY"),
                    bar_end="2026-09-14T06:30:00+00:00", available_at="2026-09-14T06:30:00+00:00")
    events.extend(engine.process_bar(close))
    exit_evaluation = evaluate_breakout_bar(run_id="run-1", evaluation_bar=close,
                                          bar_history=(fill, last_continuous, close), universe_frames=(),
                                          config=_strategy(), state=engine.strategy_state)
    events.extend(engine.process_decision(exit_evaluation.decision, exit_evaluation.state, source_bar=close))
    other = replace(_profile_bar("2026-09-15T00:00:00+00:00", session="2026-09-15:KRX_REGULAR", phase="CONTINUOUS"), code="000660")
    events.extend(engine.process_bar(other))
    other_evaluation = evaluate_breakout_bar(run_id="run-1", evaluation_bar=other, bar_history=(other,),
                                           universe_frames=(), config=_strategy(), state=engine.strategy_state)
    events.extend(engine.process_decision(other_evaluation.decision, other_evaluation.state, source_bar=other))
    if with_followup:
        for minute in range(3):
            bar = _profile_bar(f"2026-09-15T00:0{minute}:00+00:00", session="2026-09-15:KRX_REGULAR", phase="CONTINUOUS")
            events.extend(engine.process_bar(bar))
            evaluation = evaluate_breakout_bar(run_id="run-1", evaluation_bar=bar, bar_history=(bar,),
                                              universe_frames=(), config=_strategy(), state=engine.strategy_state)
            events.extend(engine.process_decision(evaluation.decision, evaluation.state, source_bar=bar))
    events.extend(engine.finalize("2026-09-15T00:04:00+00:00"))
    return engine, events, exit_evaluation, other_evaluation


class ResearchExecutionTests(unittest.TestCase):
    def test_late_exit_without_held_symbol_followup_keeps_position_and_blocks_other_symbol(self):
        engine, events, exit_evaluation, other_evaluation = late_exit_trace()
        self.assertEqual("EXIT", exit_evaluation.decision.final_action)
        self.assertIn("max_hold_reached", exit_evaluation.decision.reasons)
        self.assertEqual("HOLD", other_evaluation.decision.final_action)
        self.assertIn("different_symbol_position_managed_elsewhere", other_evaluation.decision.reasons)
        self.assertEqual("005930", engine.portfolio.position.symbol)
        self.assertEqual("open", engine.strategy_state.status)
        self.assertIsNone(engine.portfolio.pending_order)
        self.assertFalse(any(event.side == "SELL" and event.state == "FILLED" for event in events))
        self.assertTrue(any(event.side == "SELL" and event.reason == "session_boundary_before_fill" for event in events))
        self.assertEqual("POSITION_CENSORED", events[-1].event_type)
        self.assertEqual(0, engine.portfolio.realized_pnl_won)

    def test_actual_held_symbol_followup_exits_only_on_proven_next_continuous_bar(self):
        engine, events, _, _ = late_exit_trace(with_followup=True)
        fills = [event for event in events if event.side == "SELL" and event.state == "FILLED"]
        self.assertEqual(1, len(fills))
        self.assertEqual("2026-09-15T00:02:00+00:00", fills[0].occurred_at)
        self.assertTrue(fills[0].source_revision_id)
        self.assertIsNone(engine.portfolio.position)
        self.assertFalse(any(event.event_type == "POSITION_CENSORED" for event in events))

    def test_decimal_cost_contract_uses_agreed_fee_and_sell_tax(self) -> None:
        cost = SimulationCostModel("fixed_bps/v2", "1.5", "20", "0")
        self.assertEqual(SimulationCostModel(**cost.to_dict()), cost)
        self.assertEqual(cost.to_dict()["commission_bps"], "1.5")
        engine = PaperExecutionEngine(
            "run-1", replace(_execution(), cost_model=cost, initial_cash_won=2_000_000),
            _strategy(),
        )
        engine.process_decision(
            replace(_decision(), signal_reference_price=100_000,
                    required_capital_won=1_000_000),
            StrategyState(status="candidate", symbol="005930"),
        )
        events = engine.process_bar(_bar(4, open_=100_000, low=90_000, high=110_000))
        self.assertEqual([event.event_type for event in events], ["FILL", "RISK_FILL"])
        self.assertEqual(events[0].notional_won, 1_000_000)
        self.assertEqual(events[0].commission_won, 150)
        self.assertEqual(events[1].tax_won, 1_900)
        with self.assertRaises(ValueError):
            SimulationCostModel("fixed_bps/v1", "1.5", 20, 0)
        with self.assertRaises(ValueError):
            SimulationCostModel("fixed_bps/v2", "1.234", "20", "0")

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

    def test_exact_minute_close_can_fill_at_next_bar_start(self) -> None:
        source = _profile_bar(
            "2026-09-14T06:18:00+00:00", session="2026-09-14:KRX_REGULAR",
            phase="CONTINUOUS",
        )
        next_bar = _profile_bar(
            "2026-09-14T06:19:00+00:00", session="2026-09-14:KRX_REGULAR",
            phase="CONTINUOUS",
        )
        engine = PaperExecutionEngine(
            "run-1", _execution(), _strategy(),
            session_profile=KRX_FULL_DAY_RESEARCH_PROFILE,
        )
        engine.process_decision(
            _decision("2026-09-14T06:19:00+00:00"),
            StrategyState(status="candidate", symbol="005930"), source_bar=source,
        )
        event = engine.process_bar(next_bar)[0]
        self.assertEqual("FILL", event.event_type)
        self.assertEqual("2026-09-14T06:19:00+00:00", event.occurred_at)

    def test_exact_minute_close_still_censors_missing_next_bar(self) -> None:
        source = _profile_bar(
            "2026-09-14T06:18:00+00:00", session="2026-09-14:KRX_REGULAR",
            phase="CONTINUOUS",
        )
        later_bar = _profile_bar(
            "2026-09-14T06:20:00+00:00", session="2026-09-14:KRX_REGULAR",
            phase="CONTINUOUS",
        )
        engine = PaperExecutionEngine(
            "run-1", _execution(), _strategy(),
            session_profile=KRX_FULL_DAY_RESEARCH_PROFILE,
        )
        engine.process_decision(
            _decision("2026-09-14T06:19:00+00:00"),
            StrategyState(status="candidate", symbol="005930"), source_bar=source,
        )
        event = engine.process_bar(later_bar)[0]
        self.assertEqual(("ORDER_CENSORED", "next_tradable_bar_gap"),
                         (event.event_type, event.reason))

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

    def test_pending_close_order_is_censored_by_next_session_other_symbol(self) -> None:
        close = _profile_bar(
            "2025-04-02T06:20:00+00:00", session="2025-04-02:KRX_REGULAR",
            phase="AUCTION_ORDER_ENTRY",
        )
        next_session = replace(_profile_bar(
            "2025-05-07T00:00:00+00:00", session="2025-05-07:KRX_REGULAR",
            phase="CONTINUOUS",
        ), code="000660")
        engine = PaperExecutionEngine(
            "run-1", _execution(), _strategy(),
            session_profile=KRX_FULL_DAY_RESEARCH_PROFILE,
        )
        engine.process_decision(
            _decision("2025-04-02T06:30:00+00:00"),
            StrategyState(status="candidate", symbol="005930"), source_bar=close,
        )
        [event] = engine.process_bar(next_session)
        self.assertEqual(("ORDER_CENSORED", "session_boundary_before_fill"),
                         (event.event_type, event.reason))
        self.assertEqual("", event.source_revision_id)
        self.assertIsNone(engine.portfolio.pending_order)
        self.assertEqual("flat", engine.strategy_state.status)
        [submitted] = engine.process_decision(
            replace(_decision("2025-05-07T00:04:00+00:00"), decision_id="next-day"),
            StrategyState(status="candidate", symbol="005930"),
        )
        self.assertEqual("ORDER_SUBMITTED", submitted.event_type)

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


class RestingLimitResearchTests(unittest.TestCase):
    def setup_engine(self, *, slippage=0):
        from kiwoom_monitor.application.research_execution import UnfilledResearchAction
        execution=replace(_execution(commission=10),cost_model=SimulationCostModel('fixed_bps/v1',10,20,slippage,
            'model_estimate','unit fixture','2026-09-01T00:00:00+00:00','2026-10-01T00:00:00+00:00'))
        self.engine=PaperExecutionEngine('limit-run',execution,_strategy(),'krx-regular/v1')
        self.source=self.bar(0)
        self.engine.process_bar(self.source)
        self.request=UnfilledResearchAction('SUBMIT','005930','BUY',10,990,1010,10,
            datetime.fromisoformat(self.source.available_at),'case','population')
        self.engine.process_unfilled_action(self.request,source_bar=self.source)
        return self.engine

    def bar(self, minute, **kwargs):
        bar=_bar(minute,**kwargs)
        return replace(bar,session_profile='krx-regular/v1',
            bar_start=bar.bar_start.replace('2026-09-12','2026-09-14'),
            bar_end=bar.bar_end.replace('2026-09-12','2026-09-14'),
            available_at=bar.available_at.replace('2026-09-12','2026-09-14'),
            research_session='2026-09-14:KRX_REGULAR',market_phase='CONTINUOUS')

    def manage(self, action='WAIT'):
        bar=self.bar(1)
        self.engine.process_bar(bar)
        request=replace(self.request,action=action,decision_time=datetime.fromisoformat(bar.available_at))
        return self.engine.process_unfilled_action(request,source_bar=bar)

    def test_intrabar_limit_touch_does_not_invent_fill_and_next_open_can_wait(self):
        engine=self.setup_engine()
        engine.process_bar(self.bar(1,low=900))
        events=engine.process_bar(self.bar(2,low=900))
        self.assertFalse(any(e.event_type=='FILL' for e in events))
        self.assertIsNotNone(engine.portfolio.pending_order)
        self.assertEqual(0,engine.portfolio.total_cost_won)
        self.assertEqual('2026-09-14T00:03:00+00:00',engine.portfolio.pending_order.eligible_after)

    def test_cancel_is_recorded_before_reprice_submit_and_common_fill_charges_once(self):
        engine=self.setup_engine()
        events=self.manage('REPRICE')
        self.assertEqual(['UNFILLED_BASELINE','ORDER_CANCELLED','ORDER_SUBMITTED'],[e.event_type for e in events])
        self.assertEqual(self.engine._last_processed_bar.available_at,events[-1].occurred_at)
        self.assertEqual(events[-1].occurred_at,events[-2].occurred_at)
        self.assertLess(events[0].occurred_at,events[-1].occurred_at)
        engine.process_bar(self.bar(2))
        fills=engine.process_bar(self.bar(3))
        fill=next(e for e in fills if e.event_type=='FILL')
        self.assertEqual((10,1000,10),(fill.quantity,fill.price,fill.commission_won))
        self.assertEqual(89990,engine.portfolio.cash_won)
        self.assertEqual(0,engine.portfolio.reserved_cash_won)

    def test_slippage_cannot_cross_limit_even_when_raw_open_crosses(self):
        engine=self.setup_engine(slippage=20)
        self.manage('REPRICE')
        engine.process_bar(self.bar(2,open_=1010,low=1000,high=1010))
        events=engine.process_bar(self.bar(3,open_=1010,low=1000,high=1010))
        self.assertFalse(any(e.event_type=='FILL' for e in events))
        self.assertIsNotNone(engine.portfolio.pending_order)

    def test_gap_incomplete_volume_or_expired_cost_are_censored_not_profitable_evidence(self):
        for kind in ('gap','quality','volume','cost'):
            with self.subTest(kind=kind):
                engine=self.setup_engine()
                engine.process_bar(self.bar(1))
                bar=self.bar(3 if kind=='gap' else 2,open_=980,low=980,high=980)
                if kind=='quality': bar=replace(bar,capture_quality='partial')
                if kind=='volume': bar=replace(bar,volume=1)
                if kind=='cost':
                    engine.execution=replace(engine.execution,cost_model=replace(engine.execution.cost_model,valid_to=bar.available_at))
                events=engine.process_bar(bar)
                self.assertEqual('ORDER_CENSORED',events[0].event_type)
                self.assertIsNone(engine.portfolio.pending_order)
                self.assertEqual(0,engine.portfolio.total_cost_won)

    def test_wrong_quantity_price_target_cost_or_cut_preserves_owned_state(self):
        engine=self.setup_engine(); self.manage()
        before=engine.portfolio; bar=engine._last_processed_bar
        request=replace(self.request,action='WAIT',decision_time=datetime.fromisoformat(bar.available_at))
        for kwargs in ({'quantity':9},{'existing_limit_price_won':991},{'target_quantity':11},
                       {'decision_time':request.decision_time+timedelta(microseconds=1)}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):
                engine.process_unfilled_action(replace(request,**kwargs),source_bar=bar)
            self.assertEqual(before,engine.portfolio)

    def test_existing_nonlimit_pending_and_duplicate_submit_cannot_be_replaced(self):
        from kiwoom_monitor.application.research_execution import PendingOrder
        engine=self.setup_engine(); before=engine.portfolio
        with self.assertRaisesRegex(ValueError,'already exists'):
            engine.process_unfilled_action(self.request,source_bar=self.source)
        engine.portfolio=replace(before,pending_order=replace(before.pending_order,limit_execution_version=''))
        with self.assertRaisesRegex(ValueError,'native pending'):
            engine.process_unfilled_action(replace(self.request,action='REPRICE'),source_bar=self.source)

    def test_cash_reservation_and_missing_cost_fail_before_cancel(self):
        engine=self.setup_engine(); bar=self.bar(1); engine.process_bar(bar)
        before=engine.portfolio
        for limit in (20000,100000):
            request=replace(self.request,action='REPRICE',candidate_limit_price_won=limit,
                decision_time=datetime.fromisoformat(bar.available_at))
            with self.assertRaisesRegex(ValueError,'reservation'):
                engine.process_unfilled_action(request,source_bar=bar)
            self.assertEqual(before,engine.portfolio)
        engine.execution=replace(engine.execution,cost_model=None)
        with self.assertRaises(ValueError):
            engine.process_unfilled_action(replace(self.request,action='WAIT',decision_time=datetime.fromisoformat(bar.available_at)),source_bar=bar)
        self.assertEqual(before,engine.portfolio)

    def test_model_type_validation_and_default_next_open_semantics_remain_separate(self):
        from kiwoom_monitor.application.research_execution import UnfilledResearchAction
        self.setup_engine()
        for kwargs in ({'action':'CANCELLED'},{'quantity':True},{'symbol':'00593Ａ'},{'side':'UNKNOWN'}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError): replace(self.request,**kwargs)
        ordinary=PaperExecutionEngine('ordinary',_execution(),_strategy())
        ordinary.process_decision(_decision(),StrategyState(status='candidate',symbol='005930'))
        events=ordinary.process_bar(_bar(4))
        self.assertEqual('FILL',events[0].event_type)
        self.assertEqual(0,ordinary.portfolio.reserved_cash_won)


class FullExitStressTests(unittest.TestCase):
    def setUp(self):
        from kiwoom_monitor.application.research_execution import FullExitStressPolicy,FullExitStressScenario,simulate_full_exit_stress
        from kiwoom_monitor.application.market_session_schedule import KRX_REGULAR_RESEARCH_PROFILE
        def bar(minute):
            b=_bar(minute)
            return replace(b,bar_start=b.bar_start.replace('09-12','09-14'),bar_end=b.bar_end.replace('09-12','09-14'),
                available_at=b.available_at.replace('09-12','09-14'),session_profile=KRX_REGULAR_RESEARCH_PROFILE,
                research_session='2026-09-14:KRX_REGULAR',market_phase='CONTINUOUS')
        self.source=bar(4);self.waiting_bars=(bar(5),);self.bars=(bar(6),bar(7));self.cut=datetime.fromisoformat(self.bars[-1].available_at)+timedelta(seconds=1)
        self.execution=replace(_execution(),cost_model=SimulationCostModel('fixed_bps/v1',10,20,10,
            'model_estimate','declared-test-cost','2026-09-14T00:00:00+00:00','2030-01-01T00:00:00+00:00'))
        self.scenarios=(FullExitStressScenario('reference',2000,0,0,0,()),
                        FullExitStressScenario('adverse',1000,0,100,200,()))
        self.policy=FullExitStressPolicy('paper-full-exit-stress/v1','explicit-exit-policy','005930',20,1000,120,
            self.scenarios,datetime.fromisoformat(self.source.bar_start),self.cut+timedelta(seconds=30))
        self.simulate=simulate_full_exit_stress

    def run_scenario(self,**changes):
        return self.simulate(**{**dict(policy=self.policy,source_bar=self.source,bars=self.bars,
            execution=self.execution,strategy=_strategy(),decision_time=self.cut,waiting_bars=self.waiting_bars),**changes})

    def test_complete_whole_position_partial_chunks_reuse_paper_cost_basis(self):
        from unittest.mock import patch
        with patch('sqlite3.connect',side_effect=AssertionError('scenario DB read')):
            result=self.run_scenario()
        self.assertEqual(('CALCULATED',20000,350),(result['status'],result['conservative_exit_capacity_won'],result['stress_loss_bps']))
        adverse=result['scenarios'][1]
        self.assertEqual([10,10],[e['quantity'] for e in adverse['fills']])
        self.assertEqual([10,0],[e['position_quantity_after'] for e in adverse['fills']])
        self.assertEqual([969,969],[e['price'] for e in adverse['fills']])
        self.assertEqual((19324,676),(adverse['net_proceeds_won'],adverse['realized_loss_won']))
        self.assertEqual(35,adverse['conservative_unit_loss_won'])
        self.assertFalse(result['order_authorized']);self.assertFalse(result['size_extrapolation_supported'])

    def test_full_quantity_is_required_in_every_scenario_not_only_added_shares(self):
        scenario=replace(self.scenarios[1],volume_haircut_bps=5000)
        result=self.run_scenario(policy=replace(self.policy,scenarios=(self.scenarios[0],scenario)))
        self.assertEqual('INCOMPLETE',result['status']);self.assertIsNone(result['conservative_exit_capacity_won'])
        self.assertIsNone(result['stress_loss_bps']);self.assertEqual(10,result['scenarios'][1]['remaining_quantity'])
        larger=self.run_scenario(policy=replace(self.policy,tested_position_quantity=21))
        self.assertEqual('INCOMPLETE',larger['status']);self.assertEqual(1,larger['scenarios'][1]['remaining_quantity'])

    def test_zero_volume_and_declared_halt_never_claim_liquidation(self):
        for changes in (dict(bars=tuple(replace(b,volume=0) for b in self.bars)),
                dict(policy=replace(self.policy,scenarios=(self.scenarios[0],replace(self.scenarios[1],blocked_minute_offsets=(1,2)))))):
            result=self.run_scenario(**changes)
            self.assertIsNone(result['stress_loss_bps']);self.assertIsNone(result['conservative_exit_capacity_won'])
            self.assertEqual('UNFILLED',result['scenarios'][1]['status'])

    def test_gap_future_partial_phase_duplicate_and_cost_expiry_are_unavailable(self):
        cases=(dict(bars=self.bars[:1]),dict(bars=(self.bars[0],self.bars[0])),
            dict(bars=(replace(self.bars[0],capture_quality='partial'),self.bars[1])),
            dict(bars=(replace(self.bars[0],market_phase='HALT'),self.bars[1])),
            dict(decision_time=self.cut-timedelta(seconds=2)),
            dict(execution=replace(self.execution,cost_model=replace(self.execution.cost_model,valid_to=self.cut.isoformat()))))
        for changes in cases:
            with self.subTest(changes=changes):
                result=self.run_scenario(**changes);self.assertEqual('UNAVAILABLE',result['status'])
                self.assertEqual([],result['scenarios']);self.assertIsNone(result['stress_loss_bps'])

    def test_historical_lows_are_loss_bounds_and_never_fill_prices(self):
        bars=tuple(replace(b,low=500) for b in self.bars)
        result=self.run_scenario(bars=bars)
        self.assertEqual([969,969],[e['price'] for e in result['scenarios'][1]['fills']])
        self.assertGreater(result['stress_loss_bps'],5000)

    def test_waiting_window_loss_is_covered_without_a_pre_eligible_fill(self):
        missing=self.run_scenario(waiting_bars=())
        self.assertEqual('UNAVAILABLE',missing['status']);self.assertIsNone(missing['stress_loss_bps'])
        shock=self.run_scenario(waiting_bars=(replace(self.waiting_bars[0],low=400),))
        self.assertGreater(shock['stress_loss_bps'],6000)
        self.assertEqual([969,969],[e['price'] for e in shock['scenarios'][1]['fills']])
        self.assertTrue(all(datetime.fromisoformat(e['occurred_at'])>=datetime.fromisoformat(shock['eligible_at'])
            for s in shock['scenarios'] for e in s['fills']))
        self.assertFalse(shock['waiting_prices_used_for_fills'])

    def test_policy_parameters_are_explicit_bounded_and_causal(self):
        from kiwoom_monitor.application.research_execution import FullExitStressScenario
        for changes in (dict(exit_window_seconds=1),dict(scenarios=self.scenarios[:1]),dict(tested_position_quantity=True),
                dict(scenarios=(self.scenarios[0],replace(self.scenarios[1],blocked_minute_offsets=(3,))))):
            with self.assertRaises(ValueError):replace(self.policy,**changes)
        with self.assertRaises(ValueError):FullExitStressScenario('bad',10001,0,0,0,())
        with self.assertRaises(ValueError):replace(self.policy,scenarios=(self.scenarios[0],replace(self.scenarios[0],scenario_ref='duplicate-assumptions')))
        result=self.run_scenario(policy=replace(self.policy,available_at=self.cut))
        self.assertEqual('UNAVAILABLE',result['status'])


if __name__ == "__main__":
    unittest.main()
