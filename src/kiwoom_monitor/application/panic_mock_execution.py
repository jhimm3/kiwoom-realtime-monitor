"""Native EX-PANIC decisions bound to the original minute setup and actual plan fills."""
from dataclasses import asdict
from datetime import datetime, timedelta
import json

from .breakout_strategy import StrategyState
from .close_mock_execution import _close_quote as acknowledged_quote, frozen_close_candidate as frozen_candidate
from .mock_automation_execution import (
    MockAutomationGateStatus, assess_mock_automation_decision_from_risk,
    dispatch_mock_automation_decision_from_risk, mock_automation_intent_id,
)
from .mock_automation_risk import mock_owned_buy_basis
from .mock_automation_session_clock import content_id
from .panic_rebound import PanicSetup, matches_panic_shadow, parse_panic_parameters
from .market_session_schedule import KST
from kiwoom_monitor.domain.execution_activation import PANIC_NATIVE_PLAN_MODE
from kiwoom_monitor.domain.order_contract import OrderPlanRef, OrderSide, OrderType, TERMINAL_ORDER_STATES
from kiwoom_monitor.domain.strategy_contract import StrategyDecision

PANIC_ORDER_POLICY_VERSION = 'panic_execution_adapter/v1'
PANIC_ENTRY_COLLECTION = 'execution_panic_entry_evidence'
PANIC_EXIT_COLLECTION = 'execution_panic_exit_evidence'


def panic_entry_plan_id(spec_id, run_id, event_id):
    return content_id('panic_plan', dict(spec_id=spec_id, run_id=run_id, event_id=event_id))


def panic_exit_key(plan):
    return content_id('panic_exit_slot', asdict(plan))


def _decision(run_id, snapshot, action, quantity, price, reasons, decided_at):
    state = StrategyState().to_dict()
    body = dict(run_id=run_id, snapshot_id=content_id('snapshot', snapshot), symbol=snapshot['symbol'],
        proposal=action, final_action=action, quantity=quantity, signal_reference_price=price,
        required_capital_won=quantity * price if action == 'ENTER' else 0, reasons=tuple(reasons),
        constraints=(PANIC_ORDER_POLICY_VERSION,), state_before=state, state_after=state, decided_at=decided_at.isoformat())
    return StrategyDecision(decision_id=content_id('decision', body), **body)


def _evidence(kind, snapshot, decision):
    document = json.loads(json.dumps(dict(snapshot=snapshot, decision=decision.to_dict()), allow_nan=False))
    return {**document, 'proof_id': content_id(kind, document)}


def build_panic_entry_evidence(spec, *, run_id, parameters, event, quote, decided_at, now):
    if spec.execution_plan_mode != PANIC_NATIVE_PLAN_MODE or spec.panic_execution_policy is None:
        raise ValueError('PANIC native execution terms are missing')
    if not matches_panic_shadow(parameters, event):
        raise ValueError('PANIC entry does not match its stored Shadow setup')
    if not isinstance(event.get('panic_entry_parameters'), dict):
        raise ValueError('PANIC entry requires retained current strategy and news evidence')
    current = acknowledged_quote(spec, quote, decided_at, now)
    available, expires = (datetime.fromisoformat(event[name]) for name in ('available_at', 'expires_at'))
    setup = PanicSetup.from_dict(event['panic_entry_setup'])
    terms = spec.panic_execution_policy
    if (not available <= decided_at < expires or now >= expires
            or decided_at.astimezone(KST).date() != available.astimezone(KST).date()
            or current['price_won'] <= setup.failure_price or current['price_won'] >= setup.target_price
            or current['price_won'] * 10_000 > event['signal_reference_price'] * (10_000 + terms.maximum_adverse_slippage_bps)
            or event['quantity'] * current['price_won'] > parameters['capital_won']):
        raise ValueError('PANIC current entry failed, rebounded, expired or exceeded its price/capital bound')
    snapshot = dict(version='panic_entry_snapshot/v1', spec_id=spec.spec_id,
        candidate_package_hash=spec.candidate_package_hash, account_ref=spec.account_scope.account_ref,
        execution_run_id=run_id, symbol=event['symbol'], candidate_event_id=event['event_id'],
        candidate_snapshot_id=event['snapshot_id'], candidate=frozen_candidate(event), quote=current)
    decision = _decision(run_id, snapshot, 'ENTER', event['quantity'], current['price_won'], event['decision_reasons'], decided_at)
    evidence = _evidence('panic_entry', snapshot, decision)
    plan = OrderPlanRef(panic_entry_plan_id(spec.spec_id, run_id, event['event_id']), evidence['proof_id'], 0)
    return evidence, decision, plan


def dispatch_panic_entry(repository, runtime, spec, *, parameters, event, quote, recovery, risk):
    scope = dict(account_ref=runtime.account_ref, spec_id=spec.spec_id, strategy_ref=spec.strategy_ref)
    plan_id = panic_entry_plan_id(spec.spec_id, runtime.run_id, event['event_id'])
    saved = repository.load_panic_entry_evidence(runtime.account_ref, plan_id)
    now = decided_at = runtime.server_now()
    if saved is not None:
        quote = saved['snapshot']['quote']; decided_at = datetime.fromisoformat(saved['decision']['decided_at'])
        original, decision, plan = build_panic_entry_evidence(spec, run_id=runtime.run_id, parameters=parameters,
            event=event, quote=quote, decided_at=decided_at, now=decided_at)
        if saved != original: raise ValueError('stored PANIC entry evidence changed')
        if runtime.load_intent(mock_automation_intent_id(spec.spec_id, decision.decision_id)) is not None:
            return dispatch_mock_automation_decision_from_risk(repository, runtime, decision, recovery, risk, plan=plan, **scope)
    evidence, decision, plan = build_panic_entry_evidence(spec, run_id=runtime.run_id, parameters=parameters,
        event=event, quote=quote, decided_at=decided_at, now=now)
    gate = assess_mock_automation_decision_from_risk(repository, runtime, decision, recovery, risk, plan=plan, **scope)
    if gate.status is MockAutomationGateStatus.BLOCKED: return gate, None, None
    repository.save_panic_entry_evidence(runtime.account_ref, plan.plan_id, evidence)
    return dispatch_mock_automation_decision_from_risk(repository, runtime, decision, recovery, risk, plan=plan, **scope)


def build_panic_exit_evidence(spec, *, run_id, parameters, event, entry_evidence, root, head, fill_events,
                              trigger_quote, quote, decided_at, now):
    entered = datetime.fromisoformat(entry_evidence['decision']['decided_at'])
    original, entry_decision, original_plan = build_panic_entry_evidence(spec, run_id=run_id, parameters=parameters,
        event=event, quote=entry_evidence['snapshot']['quote'], decided_at=entered, now=entered)
    if (original != entry_evidence or root is None or root.state not in TERMINAL_ORDER_STATES
            or (root.intent.intent_id, root.intent.account_ref, root.intent.run_id, root.intent.side, root.intent.environment,
                root.intent.symbol, root.intent.policy_version, getattr(root.intent, 'plan', None),
                root.intent.decision_id, root.intent.quantity, root.intent.limit_price, root.intent.venue, root.intent.order_type, root.intent.created_at)
            != (mock_automation_intent_id(spec.spec_id, entry_decision.decision_id), spec.account_scope.account_ref,
                run_id, OrderSide.BUY, 'mock', event['symbol'], PANIC_ORDER_POLICY_VERSION, original_plan,
                entry_decision.decision_id, entry_decision.quantity, entry_decision.signal_reference_price, 'KRX', OrderType.LIMIT, entered)
            or (head.plan.plan_id, head.plan.policy_ref, head.symbol)
            != (original_plan.plan_id, original_plan.policy_ref, event['symbol'])
            or head.quantity <= 0 or head.unreconciled_sell_quantity):
        raise ValueError('PANIC exit requires the original terminal entry and confirmed plan quantity')
    fills = [row for row in fill_events if row['event_type'] == 'FILL']
    quantity = basis = 0
    for row in fills:
        if not row['broker_order_id'] or not row['broker_execution_id'] or row['quantity'] <= 0 or row['price'] <= 0:
            raise ValueError('PANIC entry detailed fill basis is invalid')
        basis = mock_owned_buy_basis(quantity, basis, row['quantity'], row['price'])
        quantity += row['quantity']
    identities = [(row['broker_order_id'], row['broker_execution_id']) for row in fills]
    if (quantity != root.detailed_filled_quantity or quantity < head.quantity or len(identities) != len(set(identities))
            or tuple(row['broker_execution_id'] for row in fills) != root.fill_ids):
        raise ValueError('PANIC entry detailed fill basis is incomplete')
    trigger = acknowledged_quote(spec, trigger_quote, decided_at, now)
    current = acknowledged_quote(spec, quote, decided_at, now)
    if (datetime.fromisoformat(trigger['available_at']) > datetime.fromisoformat(current['available_at'])
            or current['price_won'] * 10_000 < trigger['price_won'] * (10_000 - spec.panic_execution_policy.protective_slippage_bps)):
        raise ValueError('PANIC exit quote exceeds the original protective price bound')
    setup = PanicSetup.from_dict(event['panic_entry_setup'])
    _, _, rules, _, _ = parse_panic_parameters(parameters)
    opened = min(datetime.fromisoformat(row['occurred_at']) for row in fills)
    if opened > decided_at: raise ValueError('PANIC position fill is from the future')
    if decided_at.astimezone(KST).date() > setup.trigger_bar_start.astimezone(KST).date():
        reason = 'panic_intraday_management_overdue_0B'
    elif decided_at >= opened + timedelta(minutes=rules.max_hold_minutes):
        reason = 'panic_max_hold_reached_0B'
    elif trigger['price_won'] <= setup.failure_price:
        reason = 'panic_failure_reached_0B'
    elif trigger['price_won'] >= setup.target_price:
        reason = 'panic_rebound_reached_0B'
    else:
        return None
    snapshot = dict(version='panic_exit_snapshot/v1', spec_id=spec.spec_id,
        candidate_package_hash=spec.candidate_package_hash, account_ref=spec.account_scope.account_ref,
        execution_run_id=run_id, symbol=event['symbol'], entry_proof_id=entry_evidence['proof_id'],
        plan=asdict(head.plan), entry_intent_id=root.intent.intent_id, quantity=head.quantity,
        fill_cursor=head.fill_cursor, entry_price_won=basis, entry_fill_ref=content_id('panic_fills', fills),
        trigger_quote=trigger, quote=current, reason=reason)
    decision = _decision(run_id, snapshot, 'EXIT', head.quantity, current['price_won'], (reason,), decided_at)
    return _evidence('panic_exit', snapshot, decision), decision, head.plan


def dispatch_panic_exit(repository, runtime, execution, spec, plan, *, parameters, event, trigger_quote, quote, recovery, risk):
    scope = dict(account_ref=runtime.account_ref, spec_id=spec.spec_id, strategy_ref=spec.strategy_ref)
    saved = repository.load_panic_exit_evidence(runtime.account_ref, panic_exit_key(plan))
    now = decided_at = runtime.server_now()
    if saved is not None:
        if (saved['proof_id'] != content_id('panic_exit', {key: value for key, value in saved.items() if key != 'proof_id'})
                or saved['snapshot']['plan'] != asdict(plan) or saved['snapshot']['spec_id'] != spec.spec_id
                or saved['snapshot']['execution_run_id'] != runtime.run_id or saved['snapshot']['account_ref'] != runtime.account_ref):
            raise ValueError('stored PANIC exit evidence changed')
        decision = StrategyDecision(**{**saved['decision'], 'reasons': tuple(saved['decision']['reasons']),
            'constraints': tuple(saved['decision']['constraints'])})
        if runtime.load_intent(mock_automation_intent_id(spec.spec_id, decision.decision_id)) is not None:
            return dispatch_mock_automation_decision_from_risk(repository, runtime, decision, recovery, risk, plan=plan, **scope)
        trigger_quote, quote = saved['snapshot']['trigger_quote'], saved['snapshot']['quote']
        decided_at = datetime.fromisoformat(decision.decided_at)
    entry = repository.load_panic_entry_evidence(runtime.account_ref, plan.plan_id)
    if entry is None: raise ValueError('PANIC original entry evidence is missing')
    root_id = mock_automation_intent_id(spec.spec_id, entry['decision']['decision_id'])
    head = execution.load_plan_position(runtime.account_ref, runtime.run_id, event['symbol'], plan)
    if head.plan != plan: raise ValueError('PANIC plan revision changed before management')
    prepared = build_panic_exit_evidence(spec, run_id=runtime.run_id, parameters=parameters, event=event,
        entry_evidence=entry, root=execution.load(root_id), head=head, fill_events=execution.events(root_id),
        trigger_quote=trigger_quote, quote=quote, decided_at=decided_at, now=now)
    if prepared is None: return None, None, None
    evidence, decision, plan = prepared
    gate = assess_mock_automation_decision_from_risk(repository, runtime, decision, recovery, risk, plan=plan, **scope)
    if gate.status is MockAutomationGateStatus.BLOCKED: return gate, None, None
    repository.save_panic_exit_evidence(runtime.account_ref, panic_exit_key(plan), evidence)
    return dispatch_mock_automation_decision_from_risk(repository, runtime, decision, recovery, risk, plan=plan, **scope)
