"""Shared current account/physical-order checks; strategy predicates stay with their owner."""
from dataclasses import asdict, dataclass
from collections.abc import Mapping
from datetime import datetime
import json

from .market_session_schedule import KST, mock_order_entry_decision
from .mock_automation_risk import mock_automation_risk_account_revision
from kiwoom_monitor.domain.order_contract import OrderSide, OrderState, TERMINAL_ORDER_STATES
from kiwoom_monitor.infrastructure.persistence.execution_repository import execution_broker_legs


@dataclass(frozen=True)
class MinuteOrderManagementClaim:
    assessment: dict
    recovery: object

    def to_dict(self):
        return json.loads(json.dumps(self.assessment, allow_nan=False))


def frozen_minute_candidate(event):
    value = {key: item for key, item in event.items() if key != 'accepted_sequence'}
    value['expires_at'] = datetime.fromisoformat(value['expires_at']).astimezone(KST).isoformat()
    return value


def minute_current_shadow_matches(spec, event, settings):
    from .section12_minute_research import MinuteShadowConfig
    from kiwoom_monitor.domain.strategy_contract import _id, MINUTE_SHADOW_VERSION, shadow_candidate_config_for_spec

    if not settings or settings.get('shadow_candidate_enabled') is not True:
        return False
    selected = shadow_candidate_config_for_spec(settings, spec.spec_id)
    if not isinstance(selected, Mapping) or selected.get('version') != MINUTE_SHADOW_VERSION:
        return False
    config = MinuteShadowConfig.from_document(selected)
    monitor = _id('section12-minute-shadow', {'config': config.to_dict(), 'session_profile': spec.session_profile,
        'universe_max_age_seconds': settings['shadow_candidate_universe_max_age_seconds']})
    return (event['run_id'] == monitor and config.strategy.family == f"{event['strategy_id']}/{event['strategy_version']}"
            and (config.strategy.family != 'panic_rebound/v1' or event.get('panic_entry_parameters') == config.strategy.to_dict()))


def acknowledged_minute_quote(spec, quote, decided_at, now):
    at = quote.get("available_at")
    if isinstance(at, str):
        at = datetime.fromisoformat(at)
    gap = spec.maximum_data_gap_seconds
    if (not isinstance(at, datetime) or at.utcoffset() is None or decided_at.utcoffset() is None
            or now.utcoffset() is None or gap is None or gap <= 0
            or quote.get("durable") is not False or quote.get("market_status") != "TRADABLE"
            or not isinstance(quote.get("input_ref"), str) or not quote["input_ref"].startswith("collector-trade:")
            or type(quote.get("price_won")) is not int or quote["price_won"] <= 0
            or not 0 <= (now - at).total_seconds() <= gap
            or not 0 <= (now - decided_at).total_seconds() <= gap
            or at > decided_at):
        raise ValueError("CLOSE execution requires a fresh acknowledged 0B quote")
    return {"price_won": quote["price_won"], "available_at": at.isoformat(),
            "input_ref": quote["input_ref"], "durable": False, "market_status": "TRADABLE"}



def verified_current_order_peers(record, recovery, risk, records, control, *,
        selected_management_context=None, selected_management_policy=None):
    """Validate full native physical coverage without changing the account cut."""
    from .mock_automation_admission import mock_automation_control_from_dict
    records = tuple(records)
    if selected_management_context is None and records == (record,) and getattr(record, 'member_spec_id', None) is None:
        return None
    if isinstance(control, Mapping):
        control = mock_automation_control_from_dict(control)
    selection = control.account_selection if control is not None else None
    member = getattr(record, 'member_spec_id', None)
    selected = selected_management_context is not None
    if selected:
        from kiwoom_monitor.domain.selected_order_evidence import SelectedOrderManagementContext
        from kiwoom_monitor.domain.execution_activation import mock_automation_spec_from_dict
        from .mock_automation_admission import mock_automation_admission_from_dict, MockAutomationDesiredState
        from .mock_automation_execution import (
            mock_automation_decision_gate_from_dict, mock_automation_intent_id, mock_automation_order_policy_version,
        )
        from .mock_automation_risk import mock_automation_expected_binding_revision
        context, policy = selected_management_context, selected_management_policy
        if not isinstance(context, SelectedOrderManagementContext) or not isinstance(policy, Mapping):
            raise ValueError('CURRENT_ORDER_SELECTED_SOURCE_CHANGED')
        def document(collection, owner, key):
            matches = [item['document'] for item in policy['documents']
                       if (item['collection'], item['owner'], item['key']) == (collection, owner, key)]
            if len(matches) != 1:
                raise ValueError('CURRENT_ORDER_SELECTED_SOURCE_CHANGED')
            return matches[0]
        spec = mock_automation_spec_from_dict(document('execution_mock_automation_specs', context.account_ref, context.spec_id))
        admission = mock_automation_admission_from_dict(document(
            'execution_mock_automation_admission_by_spec', context.account_ref, context.spec_id))
        from kiwoom_monitor.infrastructure.persistence.execution_repository import _record_from_document
        source_ids, source_orders, source_events = (policy.get(name) for name in
            ('source_intent_ids', 'source_orders', 'source_events'))
        if (not isinstance(source_ids, (tuple, list)) or not source_ids or len(set(source_ids)) != len(source_ids)
                or context.intent_id not in source_ids or not isinstance(source_orders, (tuple, list))
                or tuple(order.get('intent_id') for order in source_orders) != tuple(source_ids)
                or not isinstance(source_events, (tuple, list))
                or any(event.get('intent_id') not in source_ids for event in source_events)):
            raise ValueError('CURRENT_ORDER_SELECTED_SOURCE_CHANGED')
        source_order = next((order for order in source_orders if order['intent_id'] == record.intent.intent_id), None)
        original = _record_from_document(source_order) if source_order is not None else None
        approval_intent_id = record.intent.intent_id if original is not None else context.intent_id
        approval = mock_automation_decision_gate_from_dict(document(
            'execution_mock_automation_approved_gates', context.account_ref, approval_intent_id))
        head = policy['source_plan_position']
        family = document('execution_mock_automation_candidate_packages', spec.strategy_ref,
                          spec.candidate_package_hash)['candidate_spec']['family']
        plan = getattr(record.intent, 'plan', None)
        if (selection is not None or control is None or control.desired_state is not MockAutomationDesiredState.RUNNING
                or not control.entry_resume_required
                or (control.account_ref, control.active_spec_id, control.execution_run_id)
                    != (context.account_ref, context.spec_id, context.execution_run_id)
                or (admission.account_ref, admission.spec_id, admission.admission_id, admission.execution_run_id,
                    admission.version, admission.account_selection_id, admission.candidate_package_hash,
                    admission.strategy_ref, admission.final_batch_id, admission.final_run_id, admission.final_result_hash)
                    != (context.account_ref, spec.spec_id, context.admission_id, context.execution_run_id,
                        context.source_admission_version, context.source_account_selection_id, spec.candidate_package_hash,
                        spec.strategy_ref, spec.final_batch_id, spec.final_run_id, spec.final_result_hash)
                or (approval.account_ref, approval.spec_id, approval.admission_id, approval.execution_run_id, approval.intent_id)
                    != (context.account_ref, context.spec_id, context.admission_id, context.execution_run_id, approval_intent_id)
                or approval.plan is None or plan is None
                or (plan.plan_id, plan.policy_ref, record.intent.symbol)
                    != (head['plan_id'], head['policy_ref'], head['symbol'])
                or (approval.plan.plan_id, approval.plan.policy_ref) != (plan.plan_id, plan.policy_ref)
                or (record.intent.account_ref, record.intent.run_id) != (context.account_ref, context.execution_run_id)
                or record.state not in {OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED, OrderState.CANCEL_PENDING}
                or (original is not None and (record.intent != original.intent
                    or member != getattr(original, 'member_spec_id', None)
                    or member not in {None, context.spec_id}
                    or source_order.get('mock_plan_approval') != approval.gate_id
                    or (record.intent.quantity, record.intent.limit_price, record.intent.symbol, record.intent.decision_id)
                        != (approval.quantity, approval.limit_price, approval.symbol, approval.strategy_decision_id)
                    or record.detailed_filled_quantity < original.detailed_filled_quantity
                    or not set(original.fill_ids).issubset(record.fill_ids)))
                or (original is None and (record.intent.side is not OrderSide.SELL
                    or member != context.member_spec_id
                    or record.intent.intent_id != mock_automation_intent_id(spec.spec_id, record.intent.decision_id)
                    or record.intent.policy_version not in {
                        mock_automation_order_policy_version(family, spec.session_profile), 'mock-protection-exit/v1'}))
                or records != (record,)):
            raise ValueError('CURRENT_ORDER_SELECTED_SCOPE_CHANGED')
        age = spec.maximum_data_gap_seconds
        if (age is None or (risk.account_ref, risk.execution_run_id, risk.binding_revision, risk.data_path)
                != (context.account_ref, context.execution_run_id,
                    mock_automation_expected_binding_revision(spec, context), spec.data_path)
                or not risk.reconciliation_complete or risk.submission_unknown_count or risk.balance_mismatch_count
                or (recovery.account.account_ref, recovery.account.as_of) != (risk.account_ref, risk.account_as_of)
                or risk.account_revision != mock_automation_risk_account_revision(recovery)):
            raise ValueError('CURRENT_ORDER_SELECTED_RISK_CHANGED')
        # A native member always needs complete member facts; legacy source
        # ownership remains the verified single plan in its original run.
        if context.member_spec_id is not None:
            risk.owned_for_member(spec.spec_id)
    elif (selection is None or member not in selection.member_spec_ids
            or control.account_ref != record.intent.account_ref or control.execution_run_id != record.intent.run_id
            or (risk.account_ref, risk.execution_run_id, risk.binding_revision)
            != (record.intent.account_ref, record.intent.run_id, selection.binding_revision)
            or not risk.reconciliation_complete or risk.submission_unknown_count or risk.balance_mismatch_count
            or tuple(row for row in records if getattr(row, 'member_spec_id', None) == member) != (record,)):
        raise ValueError('CURRENT_ORDER_PEER_COVERAGE_CHANGED')
    else:
        age = selection.limits.maximum_data_gap_seconds
    expected, peers, reserved = {}, [], 0
    for row in records:
        if ((not selected and getattr(row, 'member_spec_id', None) not in selection.member_spec_ids)
                or (row.intent.account_ref, row.intent.run_id) != (record.intent.account_ref, record.intent.run_id)
                or row != record and row.state not in {OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED}
                or row.amendment is not None and row.amendment.reconciliation_pending):
            raise ValueError('CURRENT_ORDER_PEER_UNCONFIRMED')
        for leg in execution_broker_legs(row):
            if leg.state in TERMINAL_ORDER_STATES or not leg.remaining_quantity:
                continue
            if leg.broker_order_id in expected:
                raise ValueError('CURRENT_ORDER_PEER_PHYSICAL_DUPLICATE')
            expected[leg.broker_order_id] = (row.intent.symbol, leg.remaining_quantity,
                leg.detailed_filled_quantity, leg.broker_reported_filled_quantity)
            if row != record:
                peers.append(leg.broker_order_id)
            if row.intent.side is OrderSide.BUY:
                reserved += leg.remaining_quantity * leg.limit_price
    working = tuple(order for order in recovery.orders if order.state not in TERMINAL_ORDER_STATES)
    actual = {order.broker_order_id: (order.symbol, order.remaining_quantity, order.filled_quantity,
                                    order.filled_quantity) for order in working}
    if (actual != expected or len(actual) != len(working)
            or reserved != recovery.account.reserved_open_buy_won
            or any(not 0 <= (risk.observed_at - order.as_of).total_seconds()
                   <= age for order in working)):
        raise ValueError('CURRENT_ORDER_PEER_PHYSICAL_COVERAGE_CHANGED')
    if selected:
        return {'account_selection_id': None, 'other_member_open_order_ids': [],
            'selected_management_context': context.to_dict(),
            'selected_plan': {'plan_id': head['plan_id'], 'policy_ref': head['policy_ref']},
            'legacy_evidence_scope': context.source_account_selection_id is None}
    return {'account_selection_id': selection.selection_id, 'other_member_open_order_ids': sorted(peers)}


def assess_current_minute_order(spec, *, run_id, record, head, order_events, recovery, risk,
        quote, decided_at, now, entry_enabled, terms, management_version, entry_proof_id,
        signal_reference_price, entry_deadline, capital_limit, invalid_entry_price=None,
        protection_notice=None, protection_reason=None, exit_anchor=None,
        validate_quote=acknowledged_minute_quote, plan_entry_notional_won=None, verified_peer_orders=None):
    """One reconciled current remainder; no new intent, broker call or mutable state."""
    age = spec.maximum_data_gap_seconds
    account = recovery.account
    selected = (verified_peer_orders or {}).get('selected_management_context')
    from .mock_automation_risk import mock_automation_expected_binding_revision
    binding_revision = mock_automation_expected_binding_revision(spec, selected)
    if selected is not None and (verified_peer_orders.get('selected_plan')
            != {'plan_id': record.intent.plan.plan_id, 'policy_ref': record.intent.plan.policy_ref}):
        raise ValueError('CLOSE current-order selected plan changed')
    if selected is not None and record.intent.side is OrderSide.BUY:
        entry_enabled = False  # A preserved original BUY is management-only, including an original ADD.
    if (age is None or (risk.account_ref, risk.execution_run_id, risk.binding_revision, risk.data_path)
            != (record.intent.account_ref, run_id, binding_revision, spec.data_path)
            or (account.account_ref, account.as_of) != (risk.account_ref, risk.account_as_of)
            or risk.account_revision != mock_automation_risk_account_revision(recovery)
            or risk.submission_unknown_count or risk.balance_mismatch_count or not risk.reconciliation_complete
            or head.fill_cursor > risk.execution_event_high_watermark
            or any(not 0 <= (now - at).total_seconds() <= age for at in (decided_at, risk.observed_at, account.as_of))):
        raise ValueError('CLOSE current-order requires a matching fresh account/Risk cut')
    legs = [leg for leg in execution_broker_legs(record) if leg.state not in TERMINAL_ORDER_STATES and leg.remaining_quantity]
    peer_ids = set(verified_peer_orders['other_member_open_order_ids']) if verified_peer_orders is not None else set()
    working = [order for order in recovery.orders if order.state not in TERMINAL_ORDER_STATES]
    orders = {order.broker_order_id: order for order in working if order.broker_order_id not in peer_ids}
    if len({order.broker_order_id for order in working}) != len(working) or set(orders) != {leg.broker_order_id for leg in legs}:
        raise ValueError('CLOSE current-order broker coverage changed')
    for leg in legs:
        observed = orders[leg.broker_order_id]
        if ((observed.account_ref, observed.symbol, observed.state, observed.filled_quantity, observed.remaining_quantity)
                != (record.intent.account_ref, record.intent.symbol, leg.state, leg.filled_quantity, leg.remaining_quantity)
                or not 0 <= (now - observed.as_of).total_seconds() <= age):
            raise ValueError('CLOSE current-order physical remainder changed')
    cancelled = {row.get('broker_order_id') or record.broker_order_id for row in order_events if row['event_type'] == 'CANCEL_STARTED'}
    available = [leg for leg in legs if leg.broker_order_id not in cancelled and leg.state in {OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED}]
    if not available:
        return None  # No second cancellation/amendment while an earlier cancellation is unresolved.
    leg = available[0]
    action, reason, current_quote = 'AMEND', 'current_conditions_valid', None
    quantities = (risk.owned_for_member(spec.spec_id) if selected is not None and selected.get('member_spec_id') is not None else
                  risk.owned_for_spec(spec.spec_id) if selected is not None else
                  risk.owned_for_member(spec.spec_id) if verified_peer_orders is not None else risk.owned_position_quantities)
    owned = dict(quantities).get(record.intent.symbol, 0)
    if record.intent.side is OrderSide.BUY:
        if protection_reason is not None:
            action, reason = 'CANCEL', protection_reason
        elif not entry_enabled:
            action, reason = 'CANCEL', 'current_entry_context_disabled_or_changed'
        elif now >= entry_deadline:
            action, reason = 'CANCEL', 'original_entry_signal_expired'
        elif record.filled_quantity >= record.intent.quantity:
            action, reason = 'CANCEL', 'original_target_already_filled'
        elif (account.positions.get(record.intent.symbol, 0) != owned
              and not ((plan_entry_notional_won is not None or verified_peer_orders is not None)
                       and account.positions.get(record.intent.symbol, 0) >= owned)):
            action, reason = 'CANCEL', 'current_run_position_unverified'
        elif (risk.daily_net_pnl_won is None or spec.maximum_daily_loss_won is None
                or risk.daily_net_pnl_won <= -spec.maximum_daily_loss_won):
            action, reason = 'CANCEL', 'current_daily_loss_limit_or_evidence'
        elif spec.maximum_reconnect_count is None or risk.reconnect_count > spec.maximum_reconnect_count:
            action, reason = 'CANCEL', 'current_reconnect_limit'
    elif owned < head.quantity or account.positions.get(record.intent.symbol, 0) < head.quantity:
        raise ValueError('CLOSE current SELL owned position changed')
    elif sum(item.remaining_quantity for item in legs) > head.quantity:
        action, reason = 'CANCEL', 'current_sell_remainder_exceeds_plan_position'
    if action == 'AMEND':
        if not mock_order_entry_decision(now, environment='mock', venue='KRX', order_type='LIMIT').allowed:
            return None
        if quote is None:
            return None
        current_quote = validate_quote(spec, quote, decided_at, now)
        price = current_quote['price_won']
        if record.intent.side is OrderSide.BUY:
            pending = sum(item.remaining_quantity for item in legs)
            increase = max(0, price - leg.limit_price) * leg.remaining_quantity
            if price * 10_000 > signal_reference_price * (10_000 + terms.maximum_adverse_slippage_bps):
                action, reason = 'CANCEL', 'current_entry_slippage_bound_exceeded'
            elif invalid_entry_price is not None and (invalid_reason := invalid_entry_price(price)) is not None:
                action, reason = 'CANCEL', invalid_reason
            elif (capital_limit is None
                    or ((owned + pending) * price if plan_entry_notional_won is None
                        else plan_entry_notional_won + pending * price) > capital_limit
                    or increase > account.available_cash_won - account.reserved_open_buy_won):
                action, reason = 'CANCEL', 'current_buy_budget_unavailable'
            elif (spec.maximum_concurrent_positions is None or
                    len([q for q in account.positions.values() if q]) + int(not account.positions.get(record.intent.symbol, 0))
                    > spec.maximum_concurrent_positions):
                action, reason = 'CANCEL', 'current_position_limit'
        else:
            anchor = exit_anchor
            if price * 10_000 < anchor * (10_000 - terms.protective_slippage_bps):
                action, reason = 'WAIT', 'original_exit_slippage_bound_exceeded'
            elif sum(item.remaining_quantity for item in legs) > head.quantity:
                action, reason = 'CANCEL', 'current_sell_remainder_exceeds_plan_position'
        if action == 'AMEND' and (record.state is OrderState.CANCEL_PENDING or price == leg.limit_price):
            action, reason = 'WAIT', 'awaiting_cancellation_or_current_price_already_working'
        if (action == 'AMEND' and record.amendment is not None and record.amendment.status == 'REJECTED'
                and record.amendment.assessment is not None and record.amendment.assessment.get('quote') == current_quote):
            action, reason = 'WAIT', 'same_rejected_quote_already_assessed'
    return {'version': management_version, 'spec_id': spec.spec_id, 'execution_run_id': run_id,
        'intent_id': record.intent.intent_id, 'plan': asdict(head.plan), 'entry_proof_id': entry_proof_id,
        'broker_order_id': leg.broker_order_id, 'quantity': leg.remaining_quantity,
        'limit_price': current_quote['price_won'] if current_quote is not None else leg.limit_price,
        'quote': current_quote, 'decided_at': decided_at.isoformat(), 'action': action, 'reason': reason,
        'entry_enabled': bool(entry_enabled), 'risk_snapshot_id': risk.snapshot_id,
        'risk_reconciliation_revision': risk.reconciliation_revision,
        **({'verified_peer_orders': verified_peer_orders} if verified_peer_orders is not None else {}),
        **({'protection_notice': protection_notice} if protection_notice is not None else {})}
