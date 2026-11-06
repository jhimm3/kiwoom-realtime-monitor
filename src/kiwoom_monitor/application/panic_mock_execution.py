"""Native EX-PANIC decisions bound to the original minute setup and actual plan fills."""
from dataclasses import asdict
from datetime import datetime, timedelta
import json

from .breakout_strategy import StrategyState
from .mock_automation_execution import (
    MockAutomationGateStatus, assess_mock_automation_decision_from_risk,
    dispatch_mock_automation_decision_from_risk, mock_automation_intent_id,
)
from .mock_automation_risk import mock_owned_buy_basis
from .mock_automation_session_clock import content_id
from .panic_rebound import PanicSetup, matches_panic_shadow, parse_panic_parameters
from .market_session_schedule import KST
from .minute_order_management import (
    MinuteOrderManagementClaim, assess_current_minute_order, acknowledged_minute_quote as acknowledged_quote,
    frozen_minute_candidate as frozen_candidate,
)
from kiwoom_monitor.domain.execution_activation import (
    PANIC_NATIVE_PLAN_MODE, PANIC_INPUT_PROTOCOL, PANIC_ORDER_MANAGEMENT_VERSION,
    PANIC_ENTRY_COLLECTION, PANIC_EXIT_COLLECTION,
)
from kiwoom_monitor.domain.order_contract import OrderPlanRef, OrderSide, OrderState, OrderType, PlannedOrderIntent, TERMINAL_ORDER_STATES
from kiwoom_monitor.domain.strategy_contract import StrategyDecision

PANIC_ORDER_POLICY_VERSION = 'panic_execution_adapter/v1'


def build_panic_trigger(spec, *, run_id, event, quote, observed_at):
    """The original setup's first actual 0B failure/rebound, independent of fill cost."""
    current = acknowledged_quote(spec, quote, observed_at, observed_at)
    if datetime.fromisoformat(current['available_at']) < datetime.fromisoformat(event['available_at']):
        raise ValueError('PANIC trigger predates the original signal')
    setup = PanicSetup.from_dict(event['panic_entry_setup'])
    if current['price_won'] <= setup.failure_price:
        reason = 'panic_failure_reached_0B'
    elif current['price_won'] >= setup.target_price:
        reason = 'panic_rebound_reached_0B'
    else:
        return None
    body = dict(version='panic-setup-trigger/v1', spec_id=spec.spec_id, execution_run_id=run_id,
        candidate_event_id=event['event_id'], candidate_snapshot_id=event['snapshot_id'], symbol=event['symbol'],
        plan_id=panic_entry_plan_id(spec.spec_id, run_id, event['event_id']), quote=current,
        observed_at=observed_at.isoformat(), reason=reason)
    return {**body, 'proof_id': content_id('panic_trigger', body)}


def validate_panic_trigger(spec, *, run_id, event, trigger, now):
    if not isinstance(trigger, dict): raise ValueError('PANIC first trigger is invalid')
    observed = datetime.fromisoformat(trigger['observed_at'])
    if observed.utcoffset() is None or observed > now:
        raise ValueError('PANIC first trigger is from the future')
    original = build_panic_trigger(spec, run_id=run_id, event=event, quote=trigger['quote'], observed_at=observed)
    if original is None or original != trigger:
        raise ValueError('PANIC first trigger or original setup changed')
    return trigger['quote']


def assess_panic_order_management(spec, *, run_id, parameters, event, entry_evidence, record, head,
        order_events, recovery, risk, quote, decided_at, now, entry_enabled, exit_evidence=None, root=None,
        panic_trigger=None, verified_peer_orders=None):
    terms = spec.panic_execution_policy
    if terms is None or terms.accepted_order_management != 'reassess_then_amend_price/v1':
        raise ValueError('PANIC current-order policy is not selected')
    entered_at = datetime.fromisoformat(entry_evidence['decision']['decided_at'])
    original, decision, plan = build_panic_entry_evidence(spec, run_id=run_id, parameters=parameters,
        event=event, quote=entry_evidence['snapshot']['quote'], decided_at=entered_at, now=entered_at)
    if (original != entry_evidence or record is None or not isinstance(record.intent, PlannedOrderIntent)
            or record.intent.policy_version != PANIC_ORDER_POLICY_VERSION
            or (record.intent.account_ref, record.intent.run_id) != (spec.account_scope.account_ref, run_id)
            or (record.intent.plan.plan_id, record.intent.plan.policy_ref, record.intent.symbol)
            != (plan.plan_id, plan.policy_ref, event['symbol'])
            or (head.plan.plan_id, head.plan.policy_ref, head.symbol) != (plan.plan_id, plan.policy_ref, event['symbol'])
            or head.unreconciled_sell_quantity or record.state not in {
                OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED, OrderState.CANCEL_PENDING}
            or record.amendment is not None and record.amendment.reconciliation_pending
            or record.detailed_filled_quantity != record.filled_quantity):
        raise ValueError('PANIC current-order origin or fill reconciliation changed')
    if record.intent.side is OrderSide.BUY:
        if ((record.intent.intent_id, record.intent.decision_id)
                != (mock_automation_intent_id(spec.spec_id, decision.decision_id), decision.decision_id)
                or head.quantity != record.detailed_filled_quantity):
            raise ValueError('PANIC current BUY plan quantity changed')
    elif (exit_evidence is None or root is None or root.intent.plan != plan
            or root.state not in TERMINAL_ORDER_STATES
            or exit_evidence['proof_id'] != content_id('panic_exit', {key: value for key, value in exit_evidence.items() if key != 'proof_id'})
            or exit_evidence['snapshot']['entry_proof_id'] != entry_evidence['proof_id']
            or exit_evidence['snapshot']['plan'] != asdict(record.intent.plan)
            or (record.intent.intent_id, record.intent.decision_id, record.intent.quantity, record.intent.limit_price)
            != (mock_automation_intent_id(spec.spec_id, exit_evidence['decision']['decision_id']),
                exit_evidence['decision']['decision_id'], exit_evidence['decision']['quantity'],
                exit_evidence['decision']['signal_reference_price'])):
        raise ValueError('PANIC current SELL original exit changed')
    setup = PanicSetup.from_dict(event['panic_entry_setup'])
    if panic_trigger is not None:
        validate_panic_trigger(spec, run_id=run_id, event=event, trigger=panic_trigger, now=now)
    if (record.intent.side is OrderSide.SELL
            and panic_trigger != exit_evidence['snapshot'].get('panic_trigger')):
        raise ValueError('PANIC current SELL original trigger changed')

    def invalid_price(price):
        if price <= setup.failure_price:
            return 'current_panic_failure_reached'
        if price >= setup.target_price:
            return 'current_panic_rebound_already_reached'
        return None

    capital = min(spec.maximum_capital_won, parameters['capital_won']) if spec.maximum_capital_won is not None else None
    assessment = assess_current_minute_order(spec, run_id=run_id, record=record, head=head, order_events=order_events,
        recovery=recovery, risk=risk, quote=quote, decided_at=decided_at, now=now, entry_enabled=entry_enabled,
        terms=terms, management_version=PANIC_ORDER_MANAGEMENT_VERSION, entry_proof_id=entry_evidence['proof_id'],
        signal_reference_price=event['signal_reference_price'], entry_deadline=datetime.fromisoformat(event['expires_at']),
        capital_limit=capital, invalid_entry_price=invalid_price,
        protection_reason=panic_trigger['reason'] if panic_trigger is not None else None,
        exit_anchor=exit_evidence['snapshot']['trigger_quote']['price_won'] if exit_evidence is not None else None,
        verified_peer_orders=verified_peer_orders)
    if assessment is not None and panic_trigger is not None:
        assessment['panic_trigger'] = panic_trigger
    return assessment


def prepare_panic_order_management_claim(repository, runtime, execution, spec, record, *, parameters, event,
        quote, recovery, risk, entry_enabled, panic_trigger=None, verified_peer_orders=None):
    entry = repository.load_panic_entry_evidence(runtime.account_ref, record.intent.plan.plan_id)
    if entry is None: raise ValueError('PANIC original entry evidence is missing')
    root = execution.load(mock_automation_intent_id(spec.spec_id, entry['decision']['decision_id']))
    exit_proof = (repository.load_panic_exit_evidence(runtime.account_ref, panic_exit_key(record.intent.plan))
                  if record.intent.side is OrderSide.SELL else None)
    if record.intent.side is OrderSide.SELL:
        panic_trigger = exit_proof['snapshot'].get('panic_trigger') if exit_proof is not None else None
    now = runtime.server_now()
    assessment = assess_panic_order_management(spec, run_id=runtime.run_id, parameters=parameters, event=event,
        entry_evidence=entry, record=record,
        head=execution.load_plan_position(runtime.account_ref, runtime.run_id, record.intent.symbol, record.intent.plan),
        order_events=execution.events(record.intent.intent_id), recovery=recovery, risk=risk, quote=quote,
        decided_at=now, now=now, entry_enabled=entry_enabled, root=root,
        exit_evidence=exit_proof, panic_trigger=panic_trigger, verified_peer_orders=verified_peer_orders)
    return MinuteOrderManagementClaim(assessment, recovery) if assessment is not None else None


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
                              trigger_quote, quote, decided_at, now, panic_trigger=None):
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
    trigger = (validate_panic_trigger(spec, run_id=run_id, event=event, trigger=panic_trigger, now=now)
               if panic_trigger is not None else acknowledged_quote(spec, trigger_quote, decided_at, now))
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
    if panic_trigger is not None: snapshot['panic_trigger'] = panic_trigger
    decision = _decision(run_id, snapshot, 'EXIT', head.quantity, current['price_won'], (reason,), decided_at)
    return _evidence('panic_exit', snapshot, decision), decision, head.plan


def dispatch_panic_exit(repository, runtime, execution, spec, plan, *, parameters, event, trigger_quote, quote, recovery, risk,
                        panic_trigger=None):
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
        panic_trigger = saved['snapshot'].get('panic_trigger')
        decided_at = datetime.fromisoformat(decision.decided_at)
    entry = repository.load_panic_entry_evidence(runtime.account_ref, plan.plan_id)
    if entry is None: raise ValueError('PANIC original entry evidence is missing')
    root_id = mock_automation_intent_id(spec.spec_id, entry['decision']['decision_id'])
    head = execution.load_plan_position(runtime.account_ref, runtime.run_id, event['symbol'], plan)
    if head.plan != plan: raise ValueError('PANIC plan revision changed before management')
    prepared = build_panic_exit_evidence(spec, run_id=runtime.run_id, parameters=parameters, event=event,
        entry_evidence=entry, root=execution.load(root_id), head=head, fill_events=execution.events(root_id),
        trigger_quote=trigger_quote, quote=quote, decided_at=decided_at, now=now, panic_trigger=panic_trigger)
    if prepared is None: return None, None, None
    evidence, decision, plan = prepared
    gate = assess_mock_automation_decision_from_risk(repository, runtime, decision, recovery, risk, plan=plan, **scope)
    if gate.status is MockAutomationGateStatus.BLOCKED: return gate, None, None
    repository.save_panic_exit_evidence(runtime.account_ref, panic_exit_key(plan), evidence)
    return dispatch_mock_automation_decision_from_risk(repository, runtime, decision, recovery, risk, plan=plan, **scope)
