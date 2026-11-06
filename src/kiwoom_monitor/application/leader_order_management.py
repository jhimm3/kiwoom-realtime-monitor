"""Current accepted-order claims for a pinned leader plan; no strategy or account owner."""
from datetime import datetime, timedelta

from .leader_breakout import validate_leader_position_proof, replay_leader_stop_observation
from .minute_order_management import MinuteOrderManagementClaim, assess_current_minute_order
from kiwoom_monitor.domain.strategy_contract import _time


def leader_position_order_confirmation_time(record, events):
    """Material terminal receipt for a staged order, independent of repeated reads."""
    from .mock_automation_risk import planned_leader_root_confirmation_time
    from kiwoom_monitor.domain.order_contract import TERMINAL_ORDER_STATES
    if record.detailed_filled_quantity:
        return planned_leader_root_confirmation_time(record, events)
    if (record.state not in TERMINAL_ORDER_STATES or record.filled_quantity
            or record.broker_reported_filled_quantity):
        raise ValueError('leader_position_order_terminal_unconfirmed')
    material = max((index for index, row in enumerate(events) if row['event_type'] in {
        'SUBMISSION_STARTED', 'CANCEL_STARTED', 'AMENDMENT_STARTED'}), default=-1)
    receipt = next((row for row in events[material+1:] if row['state'] == record.state.value
                    and row['event_type'] in {'BROKER_RECONCILED', 'SUBMISSION_REJECTED', 'RELEASED', 'EXPIRED'}), None)
    if receipt is None:
        raise ValueError('leader_position_order_terminal_receipt_missing')
    return max(_time(receipt['received_at']), _time(receipt.get('broker_as_of') or receipt['received_at']))


def assess_leader_order_management(spec, *, run_id, config, position_proof, record, head,
        order_events, recovery, risk, quote, decided_at, now, entry_enabled,
        decision_source, protection_notice=None, stop_risk=None, plan_entry_notional_won=None, verified_peer_orders=None,
        selected_management_context=None, source_risk_snapshot_ids=()):
    """Reassess an existing physical remainder; never create an additional tranche."""
    from .mock_automation_execution import mock_automation_intent_id
    from .mock_automation_session_clock import content_id
    from kiwoom_monitor.domain.execution_activation import LEADER_NATIVE_PLAN_MODE, LEADER_ORDER_MANAGEMENT_VERSION
    from kiwoom_monitor.domain.order_contract import PlannedOrderIntent, OrderSide, OrderState

    terms = spec.unfilled_execution_policy
    context = validate_leader_position_proof(position_proof, config, spec, run_id)
    if (spec.execution_plan_mode != LEADER_NATIVE_PLAN_MODE or terms is None
            or terms.accepted_order_management != 'reassess_then_amend_price/v1'
            or not isinstance(record.intent, PlannedOrderIntent)
            or (record.intent.account_ref, record.intent.run_id, record.intent.symbol,
                record.intent.plan.plan_id, record.intent.plan.policy_ref)
            != (spec.account_scope.account_ref, run_id, context.symbol,
                position_proof['entry_intent_id'], position_proof['proof_id'])
            or (head.plan.plan_id, head.plan.policy_ref, head.symbol)
            != (record.intent.plan.plan_id, record.intent.plan.policy_ref, context.symbol)
            or head.unreconciled_sell_quantity
            or record.state not in {OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED, OrderState.CANCEL_PENDING}
            or record.detailed_filled_quantity != record.filled_quantity
            or record.amendment is not None and record.amendment.reconciliation_pending):
        raise ValueError('LEADER_CURRENT_ORDER_ORIGIN_CHANGED')
    if record.intent.policy_version == 'mock-protection-exit/v1':
        from .mock_automation_risk import read_planned_leader_protection_checkpoint
        if protection_notice is None:
            raise ValueError('LEADER_CURRENT_ORDER_PROTECTION_REQUIRED')
        original_plan, decision = read_planned_leader_protection_checkpoint(
            decision_source, protection_notice, spec, run_id)
        original = decision.to_dict()
        if original_plan != record.intent.plan:
            raise ValueError('LEADER_CURRENT_ORDER_EXIT_PLAN_CHANGED')
    elif record.intent.policy_version == 'leader_execution_adapter/v1':
        original = decision_source['decision']
        body = {key: value for key, value in original.items() if key != 'decision_id'}
        if (decision_source.get('position_proof') != position_proof
                or original['decision_id'] != content_id('decision', body)):
            raise ValueError('LEADER_CURRENT_ORDER_DECISION_CHANGED')
    else:
        raise ValueError('LEADER_CURRENT_ORDER_POLICY_CHANGED')
    staged = hasattr(config.execution_contract, 'position_steps')
    if staged and record.intent.side is OrderSide.BUY and (type(plan_entry_notional_won) is not int or plan_entry_notional_won < 0):
        raise ValueError('LEADER_CURRENT_ORDER_PLAN_ENTRY_NOTIONAL_REQUIRED')
    if ((record.intent.intent_id, record.intent.decision_id, record.intent.quantity, record.intent.limit_price)
            != (mock_automation_intent_id(spec.spec_id, original['decision_id']), original['decision_id'],
                original['quantity'], original['signal_reference_price'])
            or record.intent.side.value != {'ENTER': 'BUY', 'ADD': 'BUY', 'REDUCE': 'SELL', 'EXIT': 'SELL'}.get(original['final_action'])):
        raise ValueError('LEADER_CURRENT_ORDER_ORIGINAL_DECISION_CHANGED')
    if record.intent.side is OrderSide.BUY and not (staged and original['final_action'] == 'ADD') and (
            record.intent.intent_id != position_proof['entry_intent_id'] or head.quantity != record.detailed_filled_quantity):
        raise ValueError('LEADER_CURRENT_BUY_PLAN_QUANTITY_CHANGED')

    protection_reason = None
    if record.intent.side is OrderSide.BUY and protection_notice is not None:
        from .mock_automation_risk import PlannedLeaderStop, validate_planned_leader_stop
        trigger = PlannedLeaderStop.from_dict(protection_notice['planned_leader_stop'])
        if (protection_notice.get('state') != 'PENDING' or stop_risk is None
                or protection_notice.get('input_ref') != trigger.stop_loss['input_ref']):
            raise ValueError('LEADER_CURRENT_ORDER_STOP_CHANGED')
        validate_planned_leader_stop(trigger, spec=spec, leader_config=config, position_proof=position_proof,
            original_risk=stop_risk, original_fill_quantity=trigger.stop_loss['quantity'],
            selected_management_context=(None if stop_risk.snapshot_id in source_risk_snapshot_ids
                                         else selected_management_context))
        protection_reason = 'original_filled_position_protection'

    def current_quote(_spec, value, cut, current_time):
        observation = value['observation']
        raw = replay_leader_stop_observation(config, context, observation)
        age = min(config.max_data_age_seconds, spec.maximum_data_gap_seconds, terms.max_snapshot_age_seconds)
        if (raw['symbol'] != context.symbol or raw['status'] not in {'HOLD', 'HARD_EXIT', 'INVALIDATED'}
                or any(not 0 <= (current_time - _time(raw[name])).total_seconds() <= age
                       for name in ('observed_at', 'available_at', 'decided_at'))
                or _time(raw['decided_at']) > cut):
            raise ValueError('LEADER_CURRENT_ORDER_QUOTE_UNAVAILABLE')
        normalized = {'price_won': raw['signal_reference_price'], 'available_at': raw['available_at'],
            'input_ref': raw['input_ref'], 'observation': observation}
        if set(value) != {'observation'} and value != normalized:
            raise ValueError('LEADER_CURRENT_ORDER_QUOTE_CHANGED')
        return normalized

    def invalid_price(price):
        reference = context.reference_price
        if staged and original['final_action'] == 'ADD':
            metadata = decision_source['snapshot']['feature_values'][0]['value']['position_step']
            reference = config.execution_contract.position_steps[metadata['index']].reference_price
        if price * 10_000 <= reference * (10_000 + config.breakout_buffer_bps):
            return 'current_leader_breakout_not_confirmed'
        observation = quote['observation']
        raw = replay_leader_stop_observation(config, context, observation)
        if raw['turnover_won'] is None or raw['turnover_won'] < config.minimum_turnover_won:
            return 'current_leader_turnover_not_confirmed'
        return None

    assessment = assess_current_minute_order(spec, run_id=run_id, record=record, head=head,
        order_events=order_events, recovery=recovery, risk=risk, quote=quote, decided_at=decided_at, now=now,
        entry_enabled=entry_enabled, terms=terms, management_version=LEADER_ORDER_MANAGEMENT_VERSION,
        entry_proof_id=position_proof['proof_id'], signal_reference_price=record.intent.limit_price,
        # The first submission TTL is not a renewed entry approval. Current
        # conditions must still hold within the original management/context window.
        entry_deadline=min(_time(context.valid_until), record.intent.created_at + timedelta(seconds=terms.maximum_management_seconds)),
        capital_limit=min(config.capital_won, spec.maximum_capital_won), invalid_entry_price=invalid_price,
        protection_notice=protection_notice, protection_reason=protection_reason,
        exit_anchor=record.intent.limit_price, validate_quote=current_quote,
        plan_entry_notional_won=plan_entry_notional_won if staged else None, verified_peer_orders=verified_peer_orders)
    if (assessment is not None and assessment['action'] == 'AMEND'
            and sum(row['event_type'] == 'AMENDMENT_STARTED' for row in order_events) >= terms.maximum_reprices):
        assessment = {**assessment, 'action': 'WAIT', 'reason': 'original_amendment_limit_reached'}
    return assessment


def prepare_leader_order_management_claim(store, runtime, execution, spec, record, *, config, quote,
        recovery, risk, entry_enabled, protection_notice=None, plan_entry_notional_won=None, verified_peer_orders=None,
        selected_management_context=None, selected_management_policy=None):
    from .mock_automation_risk import mock_automation_risk_snapshot_from_dict

    account = spec.account_scope.account_ref
    def document(collection, key):
        row = store.load_document(collection, account, key)
        if row is not None:
            return row['document']
        if selected_management_context is not None and selected_management_policy is not None:
            from copy import deepcopy
            return next((deepcopy(item['document']) for item in selected_management_policy['documents']
                if (item['collection'], item['owner'], item['key']) == (collection, account, key)), None)
        return None
    pin = document('execution_leader_position_context', record.intent.plan.plan_id)
    source = (protection_notice['planned_leader_protection_exit']
              if record.intent.policy_version == 'mock-protection-exit/v1' and protection_notice is not None
              else document('execution_leader_decisions', record.intent.intent_id
                            if getattr(record, 'member_spec_id', None) is not None
                            and not (verified_peer_orders or {}).get('legacy_evidence_scope') else record.intent.decision_id))
    stop_risk = (mock_automation_risk_snapshot_from_dict(document('execution_mock_automation_risk_snapshots',
        protection_notice['planned_leader_stop']['risk_snapshot_id']))
        if protection_notice is not None and record.intent.side.value == 'BUY' else None)
    assessment = assess_leader_order_management(spec, run_id=runtime.run_id, config=config, position_proof=pin,
        record=record, head=execution.load_plan_position(account, runtime.run_id, record.intent.symbol, record.intent.plan),
        order_events=execution.events(record.intent.intent_id), recovery=recovery, risk=risk,
        quote={'observation': quote.observation()} if quote is not None else None,
        decided_at=risk.observed_at if selected_management_context is not None else runtime.server_now(),
        now=runtime.server_now(), entry_enabled=entry_enabled,
        decision_source=source, protection_notice=protection_notice, stop_risk=stop_risk,
        plan_entry_notional_won=plan_entry_notional_won, verified_peer_orders=verified_peer_orders,
        selected_management_context=selected_management_context,
        source_risk_snapshot_ids=tuple(item['key'] for item in (selected_management_policy or {}).get('documents', ())
            if item['collection'] == 'execution_mock_automation_risk_snapshots' and item['owner'] == account))
    return MinuteOrderManagementClaim(assessment, recovery) if assessment is not None else None
