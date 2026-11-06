"""Declared additional lots and reductions, independent of order replacement."""
from dataclasses import replace
from datetime import datetime, timedelta
import json
import unittest
from unittest.mock import patch

from tests.unit.test_leader_breakout import AT, execution_config, trade
from kiwoom_monitor.application.breakout_strategy import StrategyState
from kiwoom_monitor.application.leader_breakout import (
    LeaderBreakoutConfig, LeaderTradeInputs, evaluate_leader_breakout_trade,
    evaluate_leader_position_step, leader_evaluation, leader_next_position_step,
    evaluate_leader_breakout_bar_step,
)


def staged_config(*, budget=10000, reduce_quantity=1):
    raw = execution_config().to_dict()
    raw['execution_contract'].update(version='leader_breakout_execution/v2', capital_won=budget,
        position_steps=[
            dict(step_id='reconfirmation-lot', action='ADD', trigger='observed_cross_above',
                 reference_price=1050, quantity=1, reason_ref='synthetic:reconfirmation'),
            dict(step_id='declared-reduction', action='REDUCE', trigger='price_at_or_above',
                 reference_price=1100, quantity=reduce_quantity, reason_ref='synthetic:partial-profit')])
    return LeaderBreakoutConfig.from_dict(raw)


def held_state(quantity=2, keys=('initial-candidate',)):
    return StrategyState(status='open', symbol='005930', entry_price=1001,
        position_quantity=quantity, opened_at=AT.isoformat(), emitted_candidate_keys=keys)


def held_tick(config, price, state, *, offset=0, source='approved:0B', before=None, enabled=True):
    at = AT + timedelta(seconds=offset)
    raw, _ = evaluate_leader_breakout_trade(config.policy, trade(price, at=at), received_at=at,
        source=source, decision_time=at, position_open=True, position_context=config.contexts[0])
    raw, after = evaluate_leader_position_step(config, raw, state, config.contexts[0],
        source=source, step_state=before, entry_enabled=enabled)
    return leader_evaluation(run_id='staged-run', config=config, raw=raw,
        state=state, context=config.contexts[0]), after


class LeaderPositionStepTests(unittest.TestCase):
    def test_v1_keys_and_hash_stay_frozen_and_v2_has_only_explicit_steps(self):
        original = execution_config()
        self.assertNotIn('position_steps', original.execution_contract.to_dict())
        self.assertEqual(original.to_dict(), LeaderBreakoutConfig.from_dict(original.to_dict()).to_dict())
        self.assertEqual(original.execution_contract.fingerprint,
            LeaderBreakoutConfig.from_dict(original.to_dict()).execution_contract.fingerprint)
        current = staged_config()
        self.assertEqual(current.to_dict(), LeaderBreakoutConfig.from_dict(json.loads(json.dumps(current.to_dict()))).to_dict())
        self.assertNotEqual(original.execution_contract.fingerprint, current.execution_contract.fingerprint)
        self.assertEqual((), current.execution_missing('0B'))
        raw = current.to_dict()
        raw['execution_contract']['position_steps'] = None
        self.assertIn('execution.position_steps', LeaderBreakoutConfig.from_dict(raw).execution_missing('0B'))

    def test_explicit_steps_reject_missing_identity_duplicate_unknown_rule_and_boolean_quantity(self):
        for field, value in [('step_id', ''), ('trigger', 'automatic_best_price'), ('quantity', True),
                             ('reference_price', 0), ('reason_ref', '')]:
            with self.subTest(field=field):
                raw = staged_config().to_dict()
                raw['execution_contract']['position_steps'][0][field] = value
                with self.assertRaises(ValueError):
                    LeaderBreakoutConfig.from_dict(raw)
        raw = staged_config().to_dict()
        raw['execution_contract']['position_steps'][1]['step_id'] = 'reconfirmation-lot'
        with self.assertRaises(ValueError):
            LeaderBreakoutConfig.from_dict(raw)

    def test_add_requires_new_observed_reconfirmation_and_keeps_current_owned_quantity_until_fill(self):
        config, state = staged_config(), held_state()
        wait, before = held_tick(config, 1049, state)
        self.assertEqual('HOLD', wait.decision.final_action)
        added, _ = held_tick(config, 1051, state, offset=1, before=before)
        self.assertEqual(('ADD', 1, 1051), (added.decision.final_action, added.decision.quantity, added.decision.required_capital_won))
        self.assertEqual(2, added.state.position_quantity)
        self.assertEqual(0, added.snapshot.feature_values[0]['value']['position_step']['index'])
        self.assertEqual('synthetic:reconfirmation', added.snapshot.feature_values[0]['value']['position_step']['reason_ref'])
        self.assertEqual(1, leader_next_position_step(config, added.state)[0])

    def test_disabled_current_entry_and_source_change_do_not_bridge_an_old_cross(self):
        config, state = staged_config(), held_state()
        _, before = held_tick(config, 1049, state)
        disabled, after = held_tick(config, 1051, state, offset=1, before=before, enabled=False)
        self.assertEqual('HOLD', disabled.decision.final_action)
        self.assertIsNone(after)
        changed, after = held_tick(config, 1051, state, offset=2, before=before, source='approved:new')
        self.assertEqual('HOLD', changed.decision.final_action)
        resumed, _ = held_tick(config, 1052, state, offset=3, before=after, source='approved:new')
        self.assertEqual('HOLD', resumed.decision.final_action)

    def test_unclaimed_action_can_observe_a_new_cross_without_advancing_or_renewing_old_signal(self):
        config, state = staged_config(), held_state()
        _, before = held_tick(config, 1049, state)
        proposal, before = held_tick(config, 1051, state, offset=1, before=before)
        self.assertEqual('ADD', proposal.decision.final_action)
        still_high, before = held_tick(config, 1052, state, offset=2, before=before)
        self.assertEqual('HOLD', still_high.decision.final_action)
        _, before = held_tick(config, 1049, state, offset=3, before=before)
        new_proposal, _ = held_tick(config, 1051, state, offset=4, before=before)
        self.assertEqual('ADD', new_proposal.decision.final_action)
        self.assertNotEqual(proposal.decision.decision_id, new_proposal.decision.decision_id)
        self.assertEqual(0, leader_next_position_step(config, state)[0])

    def test_partial_reduction_is_separate_from_full_pinned_stop_and_never_sells_all_as_a_partial(self):
        config = staged_config()
        key = leader_next_position_step(config, held_state())[2]
        state = held_state(3, ('initial-candidate', key))
        reduced, _ = held_tick(config, 1101, state)
        self.assertEqual(('REDUCE', 1, 3), (reduced.decision.final_action, reduced.decision.quantity, reduced.state.position_quantity))
        stopped, _ = held_tick(config, 940, state, offset=1)
        self.assertEqual(('EXIT', 3), (stopped.decision.final_action, stopped.decision.quantity))
        invalid, _ = held_tick(staged_config(reduce_quantity=3), 1101,
            held_state(3, ('initial-candidate', leader_next_position_step(staged_config(reduce_quantity=3), held_state())[2])))
        self.assertEqual(('NO_TRADE', 0), (invalid.decision.final_action, invalid.decision.quantity))

    def test_standard_incremental_paper_executes_initial_add_partial_and_remaining_stop_with_actual_costs(self):
        from kiwoom_monitor.application.research_execution import PaperExecutionEngine
        from tests.unit.test_research_execution import _execution, _bar
        config = staged_config()
        engine = PaperExecutionEngine(run_id='staged-run', execution=_execution(commission=10, tax=20), strategy=config)
        cursor, events, actions = None, [], []
        # Completed closes propose; the next real bar open executes. No synthetic ticks.
        for index, price in enumerate((990, 1001, 1040, 1049, 1051, 1060, 1101, 1110, 940, 930)):
            start = AT + timedelta(minutes=index)
            frame = replace(_bar(index, open_=price), revision_id=f'staged:{index}', observation_key=start.isoformat(),
                bar_start=start.isoformat(), bar_end=(start+timedelta(minutes=1)).isoformat(),
                available_at=(start+timedelta(minutes=1, seconds=2)).isoformat(),
                open=price, high=price, low=price, close=price, trade_value_million_won=100)
            events.extend(engine.process_bar(frame))
            evaluation, cursor = evaluate_leader_breakout_bar_step(run_id='staged-run', evaluation_bar=frame,
                config=config, state=engine.strategy_state, minute_state=cursor)
            if evaluation.decision.final_action in {'ENTER', 'ADD', 'REDUCE', 'EXIT'}:
                actions.append((evaluation.decision.final_action, evaluation.decision.quantity))
            events.extend(engine.process_decision(evaluation.decision, evaluation.state, source_bar=frame))
        self.assertEqual([('ENTER', 2), ('ADD', 1), ('REDUCE', 1), ('EXIT', 2)], actions)
        fills = [event for event in events if event.event_type == 'FILL']
        self.assertEqual([('BUY', 2), ('BUY', 1), ('SELL', 1), ('SELL', 2)], [(event.side, event.quantity) for event in fills])
        self.assertIsNone(engine.portfolio.position)
        self.assertEqual('flat', engine.strategy_state.status)
        self.assertGreater(engine.portfolio.total_cost_won, 0)
        self.assertEqual(2 * 1040 + 1060, engine._leader_entry_notional)

    def test_unfilled_replay_restores_actual_gross_buys_after_partial_reduction(self):
        from kiwoom_monitor.application import research_execution as execution_module
        from kiwoom_monitor.application.research_evaluation import unfilled_action_outcome_from_events
        from kiwoom_monitor.application.research_splits import ResearchEvaluationSpec, ResearchFoldSpec
        from tests.unit.test_research_execution import _execution, _bar
        config = staged_config(budget=5000)
        execution = _execution(commission=10, tax=20)
        execution = replace(execution, cost_model=replace(execution.cost_model,
            rate_basis='model_estimate', source='synthetic-declared-costs',
            valid_from=(AT-timedelta(days=1)).isoformat(), valid_to=(AT+timedelta(days=1)).isoformat()))
        engine_type = execution_module.PaperExecutionEngine
        engine = engine_type('staged-replay', execution, config, 'krx-regular/v1')
        def frame(index, price):
            start = AT+timedelta(minutes=index)
            return replace(_bar(index, open_=price), revision_id=f'replay:{index}', observation_key=start.isoformat(),
                bar_start=start.isoformat(), bar_end=(start+timedelta(minutes=1)).isoformat(),
                available_at=(start+timedelta(minutes=1, seconds=2)).isoformat(),
                open=price, high=price, low=price, close=price, trade_value_million_won=100,
                session_profile='krx-regular/v1', research_session='2026-10-16:KRX:regular', market_phase='CONTINUOUS')
        cursor, events = None, []
        for index, price in enumerate((990, 1001, 1040, 1049, 1051, 1060, 1101, 1110)):
            bar = frame(index, price)
            events.extend(engine.process_bar(bar))
            evaluation, cursor = evaluate_leader_breakout_bar_step(run_id=engine.run_id, evaluation_bar=bar,
                config=config, state=engine.strategy_state, minute_state=cursor)
            events.extend(engine.process_decision(evaluation.decision, evaluation.state, source_bar=bar))
        self.assertEqual((2, 3140), (engine.portfolio.position.quantity, engine._leader_entry_notional))
        self.assertLess(engine.portfolio.position.entry_notional, engine._leader_entry_notional)
        submit = execution_module.UnfilledResearchAction('SUBMIT', '005930', 'BUY', 1, 1000, 1000, 3,
            datetime.fromisoformat(bar.available_at), 'replay-case', 'synthetic-population')
        events.extend(engine.process_unfilled_action(submit, source_bar=bar))
        source = frame(8, 1050)
        events.extend(engine.process_bar(source))
        wait = replace(submit, action='WAIT', decision_time=datetime.fromisoformat(source.available_at))
        baseline, = engine.process_unfilled_action(wait, source_bar=source)
        events.append(baseline)
        end = frame(9, 990)
        events.extend((engine.observe_unfilled_bar(end), *engine.process_bar(end)))
        terminal = events[-1]
        self.assertEqual(('MARK', 3, 4130),
            (terminal.event_type, terminal.position_quantity_after, engine._leader_entry_notional))
        context = dict(run_id=engine.run_id, unfilled_execution=execution, unfilled_strategy=config,
            unfilled_action_plan=(submit, wait), revision_ids_hash='a'*64, unfilled_source_code_hash='b'*64,
            cost_model_ref='synthetic-costs', finished_at=datetime.fromisoformat(end.available_at),
            evaluation=ResearchEvaluationSpec('chronological_holdout/v1',
                (ResearchFoldSpec('train', 'TRAIN', AT.isoformat(), (AT+timedelta(hours=1)).isoformat()),),
                60, 0, 0, 1, 1).to_dict())
        restored = []
        def observe_engine(*args, **kwargs):
            actual = engine_type(*args, **kwargs)
            restored.append(actual)
            return actual
        with patch.object(execution_module, 'PaperExecutionEngine', side_effect=observe_engine):
            outcome = unfilled_action_outcome_from_events(context, [event.to_dict() for event in events],
                start_event_id=baseline.event_id, end_event_id=terminal.event_id, action='WAIT',
                case_ref='replay-case', population_ref='synthetic-population', horizon_seconds=60, symbol='005930')
        self.assertEqual(('COMPLETE', 2, 1, 3),
            (outcome.status, outcome.initial_quantity, outcome.pending_buy_quantity, outcome.target_quantity))
        self.assertEqual(4130, restored[0]._leader_entry_notional)
        self.assertEqual(engine.portfolio, restored[0].portfolio)


if __name__ == '__main__':
    unittest.main()
