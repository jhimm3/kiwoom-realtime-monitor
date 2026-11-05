"""Shared current account/physical-order checks; strategy predicates stay with their owner."""
from dataclasses import asdict, dataclass
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
    from kiwoom_monitor.domain.strategy_contract import _id

    if not settings or settings.get('shadow_candidate_enabled') is not True:
        return False
    config = MinuteShadowConfig.from_document(settings['shadow_candidate_config'])
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



def assess_current_minute_order(spec, *, run_id, record, head, order_events, recovery, risk,
        quote, decided_at, now, entry_enabled, terms, management_version, entry_proof_id,
        signal_reference_price, entry_deadline, capital_limit, invalid_entry_price=None,
        protection_notice=None, protection_reason=None, exit_anchor=None,
        validate_quote=acknowledged_minute_quote):
    """One reconciled current remainder; no new intent, broker call or mutable state."""
    age = spec.maximum_data_gap_seconds
    account = recovery.account
    if (age is None or (risk.account_ref, risk.execution_run_id, risk.binding_revision, risk.data_path)
            != (record.intent.account_ref, run_id, spec.binding_revision, spec.data_path)
            or (account.account_ref, account.as_of) != (risk.account_ref, risk.account_as_of)
            or risk.account_revision != mock_automation_risk_account_revision(recovery)
            or risk.submission_unknown_count or risk.balance_mismatch_count or not risk.reconciliation_complete
            or head.fill_cursor > risk.execution_event_high_watermark
            or any(not 0 <= (now - at).total_seconds() <= age for at in (decided_at, risk.observed_at, account.as_of))):
        raise ValueError('CLOSE current-order requires a matching fresh account/Risk cut')
    legs = [leg for leg in execution_broker_legs(record) if leg.state not in TERMINAL_ORDER_STATES and leg.remaining_quantity]
    orders = {order.broker_order_id: order for order in recovery.orders if order.state not in TERMINAL_ORDER_STATES}
    if len(orders) != len([order for order in recovery.orders if order.state not in TERMINAL_ORDER_STATES]) or set(orders) != {leg.broker_order_id for leg in legs}:
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
    owned = dict(risk.owned_position_quantities).get(record.intent.symbol, 0)
    if record.intent.side is OrderSide.BUY:
        if protection_reason is not None:
            action, reason = 'CANCEL', protection_reason
        elif not entry_enabled:
            action, reason = 'CANCEL', 'current_entry_context_disabled_or_changed'
        elif now >= entry_deadline:
            action, reason = 'CANCEL', 'original_entry_signal_expired'
        elif record.filled_quantity >= record.intent.quantity:
            action, reason = 'CANCEL', 'original_target_already_filled'
        elif account.positions.get(record.intent.symbol, 0) != owned:
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
            elif (capital_limit is None or (owned + pending) * price > capital_limit
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
        **({'protection_notice': protection_notice} if protection_notice is not None else {})}
