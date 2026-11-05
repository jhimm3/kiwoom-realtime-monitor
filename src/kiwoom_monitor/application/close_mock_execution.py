"""Bind CLOSE/IPO plans to their original Shadow setup and explicit mock terms."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import json

from .breakout_strategy import StrategyState
from .minute_order_management import (
    MinuteOrderManagementClaim, assess_current_minute_order,
    acknowledged_minute_quote as _close_quote, frozen_minute_candidate as frozen_close_candidate,
    minute_current_shadow_matches as close_current_shadow_matches,
)
from .close_and_ipo import (
    CloseIpoPosition, CloseIpoSetup, evaluate_close_gap, evaluate_close_gap_at_price, matches_close_shadow,
)
from .market_session_schedule import KST, KRX_AUCTION_RANDOM_END_SECONDS, MarketPhase, MarketSession, SessionSupport, mock_order_entry_decision
from .research_replay import KrxMinuteBarFrame
from .mock_automation_execution import (
    MockAutomationGateStatus,
    assess_mock_automation_decision_from_risk,
    dispatch_mock_automation_decision_from_risk,
)
from .mock_automation_session_clock import content_id
from .mock_automation_risk import mock_automation_risk_account_revision, mock_owned_buy_basis
from kiwoom_monitor.domain.execution_activation import (
    CLOSE_NATIVE_PLAN_MODE, CLOSE_ORDER_MANAGEMENT_VERSION, CLOSE_ENTRY_COLLECTION, CLOSE_EXIT_COLLECTION,
)
from kiwoom_monitor.domain.order_contract import OrderPlanRef, OrderSide, OrderState, PlannedOrderIntent, TERMINAL_ORDER_STATES
from kiwoom_monitor.domain.strategy_contract import StrategyDecision


CLOSE_ORDER_POLICY_VERSION = "close_execution_adapter/v1"
CLOSE_CANCEL_VERSION = "close-entry-deadline-cancel/v1"
CLOSE_PROTECTIVE_CANCEL_VERSION = 'close-entry-protective-cancel/v1'


def assess_close_order_management(spec, *, run_id, parameters, event, entry_evidence, record, head,
        order_events, recovery, risk, quote, decided_at, now, entry_enabled,
        exit_evidence=None, root=None, root_fill_events=(), protection_notice=None, stop_risk=None):
    """Reassess this accepted order, without granting a new entry or resetting its price anchor."""
    from .mock_automation_execution import mock_automation_intent_id

    terms = spec.close_execution_policy
    if terms is None or terms.accepted_order_management != 'reassess_then_amend_price/v1':
        raise ValueError('CLOSE current-order policy is not selected')
    entered_at = datetime.fromisoformat(entry_evidence['decision']['decided_at'])
    original, entry_decision, original_plan = build_close_entry_evidence(spec, run_id=run_id,
        parameters=parameters, event=event, quote=entry_evidence['snapshot']['quote'], decided_at=entered_at, now=entered_at,
        retain_candidate=entry_evidence['snapshot']['version'] != 'close_entry_snapshot/v1')
    if (original != entry_evidence or record is None or not isinstance(record.intent, PlannedOrderIntent)
            or record.intent.policy_version != CLOSE_ORDER_POLICY_VERSION
            or record.intent.account_ref != spec.account_scope.account_ref or record.intent.run_id != run_id
            or (record.intent.plan.plan_id, record.intent.plan.policy_ref, record.intent.symbol)
            != (original_plan.plan_id, original_plan.policy_ref, event['symbol'])
            or (head.plan.plan_id, head.plan.policy_ref, head.symbol)
            != (original_plan.plan_id, original_plan.policy_ref, event['symbol'])
            or head.unreconciled_sell_quantity or record.state not in {
                OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED, OrderState.CANCEL_PENDING}
            or record.amendment is not None and record.amendment.reconciliation_pending
            or record.detailed_filled_quantity != record.filled_quantity):
        raise ValueError('CLOSE current-order origin or fill reconciliation changed')
    if record.intent.side is OrderSide.BUY:
        if (record.intent.intent_id != mock_automation_intent_id(spec.spec_id, entry_decision.decision_id)
                or record.intent.decision_id != entry_decision.decision_id or head.quantity != record.detailed_filled_quantity):
            raise ValueError('CLOSE current BUY plan quantity changed')
    else:
        proof = exit_evidence
        if (proof is None or root is None or root.intent.plan != original_plan
                or proof['proof_id'] != content_id('close_exit', {key: value for key, value in proof.items() if key != 'proof_id'})
                or proof['snapshot']['entry_proof_id'] != entry_evidence['proof_id']
                or proof['snapshot']['plan'] != asdict(record.intent.plan)
                or (record.intent.intent_id, record.intent.decision_id, record.intent.quantity, record.intent.limit_price)
                != (mock_automation_intent_id(spec.spec_id, proof['decision']['decision_id']),
                    proof['decision']['decision_id'], proof['decision']['quantity'], proof['decision']['signal_reference_price'])):
            raise ValueError('CLOSE current SELL original exit changed')
    setup = CloseIpoSetup.from_document(event['close_ipo_entry_setup'])
    protection_reason = None
    if protection_notice is not None and record.intent.side is OrderSide.BUY:
        validate_close_stop_notice(spec, run_id=run_id, root=record, fill_events=root_fill_events,
            notice=protection_notice, stop_risk=stop_risk, stop_loss_bps=setup.policy.protection_loss_bps, event=event, now=now)
        protection_reason = 'original_filled_position_protection'

    def invalid_price(price):
        if setup.policy.variant == 'IPO_BREAKOUT' and price * 10_000 <= setup.breakout_level * (10_000 + setup.policy.breakout_buffer_bps):
            return 'current_ipo_breakout_not_confirmed'
        return None

    return assess_current_minute_order(spec, run_id=run_id, record=record, head=head, order_events=order_events,
        recovery=recovery, risk=risk, quote=quote, decided_at=decided_at, now=now, entry_enabled=entry_enabled,
        terms=terms, management_version=CLOSE_ORDER_MANAGEMENT_VERSION, entry_proof_id=entry_evidence['proof_id'],
        signal_reference_price=event['signal_reference_price'],
        entry_deadline=min(datetime.fromisoformat(event['expires_at']), setup.entry_deadline),
        capital_limit=spec.maximum_capital_won, invalid_entry_price=invalid_price,
        protection_notice=protection_notice, protection_reason=protection_reason,
        exit_anchor=exit_evidence['snapshot']['trigger_quote']['price_won'] if exit_evidence is not None else None)


def prepare_close_order_management_claim(repository, runtime, execution, spec, record, *, parameters, event,
        quote, recovery, risk, entry_enabled, protection_notice=None):
    from .mock_automation_execution import mock_automation_intent_id
    entry = repository.load_close_entry_evidence(runtime.account_ref, record.intent.plan.plan_id)
    root = execution.load(mock_automation_intent_id(spec.spec_id, entry['decision']['decision_id']))
    assessment = assess_close_order_management(spec, run_id=runtime.run_id, parameters=parameters, event=event,
        entry_evidence=entry, record=record,
        head=execution.load_plan_position(runtime.account_ref, runtime.run_id, record.intent.symbol, record.intent.plan),
        order_events=execution.events(record.intent.intent_id), recovery=recovery, risk=risk, quote=quote,
        decided_at=runtime.server_now(), now=runtime.server_now(), entry_enabled=entry_enabled, root=root,
        exit_evidence=(repository.load_close_exit_evidence(runtime.account_ref, close_exit_key(record.intent.plan))
                       if record.intent.side is OrderSide.SELL else None),
        root_fill_events=execution.events(root.intent.intent_id) if protection_notice is not None else (),
        protection_notice=protection_notice, stop_risk=(repository.load_mock_automation_risk_snapshot(runtime.account_ref,
            protection_notice['risk_trigger']['snapshot_id']) if protection_notice is not None else None))
    return MinuteOrderManagementClaim(assessment, recovery) if assessment is not None else None


@dataclass(frozen=True)
class CloseEntryDeadlineCancelClaim:
    plan: OrderPlanRef
    risk_snapshot_id: str
    risk_reconciliation_revision: int
    recovery: object
    protection_notice: dict | None = None

    def to_dict(self):
        value = {"version": CLOSE_CANCEL_VERSION, "plan": asdict(self.plan),
                "risk_snapshot_id": self.risk_snapshot_id,
                "risk_reconciliation_revision": self.risk_reconciliation_revision}
        if self.protection_notice is not None:
            value.update(version=CLOSE_PROTECTIVE_CANCEL_VERSION, protection_notice=self.protection_notice)
        return value

    @classmethod
    def from_dict(cls, value, recovery):
        protective = value.get('version') == CLOSE_PROTECTIVE_CANCEL_VERSION
        if (set(value) != {"version", "plan", "risk_snapshot_id", "risk_reconciliation_revision"}
                | ({'protection_notice'} if protective else set())
                or value["version"] not in {CLOSE_CANCEL_VERSION, CLOSE_PROTECTIVE_CANCEL_VERSION} or recovery is None
                or protective and not isinstance(value.get('protection_notice'), dict)
                or type(value["risk_reconciliation_revision"]) is not int
                or value["risk_reconciliation_revision"] <= 0):
            raise ValueError("invalid CLOSE deadline cancellation claim")
        return cls(OrderPlanRef(**value["plan"]), value["risk_snapshot_id"],
                   value["risk_reconciliation_revision"], recovery, value.get('protection_notice'))


def close_entry_plan_id(spec_id: str, run_id: str, event_id: str) -> str:
    return content_id("close_plan", {"spec_id": spec_id, "run_id": run_id, "event_id": event_id})


def build_close_entry_evidence(spec, *, run_id, parameters, event, quote, decided_at, now, retain_candidate=True):
    """Freeze a sized Decision. Native claim independently repeats this pure check."""
    terms = spec.close_execution_policy
    if spec.execution_plan_mode != CLOSE_NATIVE_PLAN_MODE or terms is None:
        raise ValueError("CLOSE native execution terms are missing")
    family = f"{event.get('strategy_id')}/{event.get('strategy_version')}"
    if not matches_close_shadow(parameters, event, family=family):
        raise ValueError("CLOSE entry does not match its stored Shadow setup")
    frozen_quote = _close_quote(spec, quote, decided_at, now)
    gap = spec.maximum_data_gap_seconds
    available = datetime.fromisoformat(event["available_at"])
    expiry = datetime.fromisoformat(event["expires_at"])
    deadline = datetime.fromisoformat(event["close_ipo_entry_setup"]["entry_deadline"])
    if (not 0 <= (now - available).total_seconds() <= gap or decided_at < available
            or now >= min(expiry, deadline)):
        raise ValueError("CLOSE entry signal expired or comes from the future")
    signal_price = event.get("signal_reference_price")
    if (type(signal_price) is not int or signal_price <= 0
            or quote["price_won"] * 10_000 > signal_price * (10_000 + terms.maximum_adverse_slippage_bps)):
        raise ValueError("CLOSE entry quote exceeds the approved signal slippage bound")
    snapshot = {"version": "close_entry_snapshot/v2" if retain_candidate else "close_entry_snapshot/v1", "spec_id": spec.spec_id,
                "candidate_package_hash": spec.candidate_package_hash, "account_ref": spec.account_scope.account_ref,
                "execution_run_id": run_id, "candidate_event_id": event["event_id"],
                "candidate_snapshot_id": event["snapshot_id"], "quote": frozen_quote}
    if retain_candidate:
        snapshot['candidate'] = frozen_close_candidate(event)
    state = StrategyState().to_dict()
    body = {"run_id": run_id, "snapshot_id": content_id("snapshot", snapshot), "symbol": event["symbol"],
            "proposal": "ENTER", "final_action": "ENTER", "quantity": event["quantity"],
            "signal_reference_price": quote["price_won"], "required_capital_won": quote["price_won"] * event["quantity"],
            "reasons": tuple(event['decision_reasons']), "constraints": (CLOSE_ORDER_POLICY_VERSION,),
            "state_before": state, "state_after": state, "decided_at": decided_at.isoformat()}
    decision = StrategyDecision(decision_id=content_id("decision", body), **body)
    document = json.loads(json.dumps({"snapshot": snapshot, "decision": decision.to_dict()}, allow_nan=False))
    proof_id = content_id("close_entry", document)
    plan = OrderPlanRef(close_entry_plan_id(spec.spec_id, run_id, event["event_id"]), proof_id, 0)
    return {**document, "proof_id": proof_id}, decision, plan


def dispatch_close_entry(repository, runtime, spec, *, parameters, event, quote, recovery, risk):
    """Reuse the existing Risk gate, immutable writer and planned order transaction."""
    plan_id = close_entry_plan_id(spec.spec_id, runtime.run_id, event["event_id"])
    saved = repository.load_close_entry_evidence(runtime.account_ref, plan_id)
    now = runtime.server_now()
    decided_at = now
    if saved is not None:
        previous = saved
        quote = previous["snapshot"]["quote"]
        decided_at = datetime.fromisoformat(previous["decision"]["decided_at"])
    scope = {"account_ref": runtime.account_ref, "spec_id": spec.spec_id, "strategy_ref": spec.strategy_ref}
    from .mock_automation_execution import mock_automation_intent_id

    if saved is not None:
        evidence, decision, plan = build_close_entry_evidence(spec, run_id=runtime.run_id, parameters=parameters,
            event=event, quote=quote, decided_at=decided_at, now=decided_at,
            retain_candidate=saved['snapshot']['version'] != 'close_entry_snapshot/v1')
        if evidence != previous:
            raise ValueError("stored CLOSE entry evidence changed")
        if runtime.load_intent(mock_automation_intent_id(spec.spec_id, decision.decision_id)) is not None:
            return dispatch_mock_automation_decision_from_risk(repository, runtime, decision, recovery, risk,
                plan=plan, **scope)
    evidence, decision, plan = build_close_entry_evidence(spec, run_id=runtime.run_id, parameters=parameters,
        event=event, quote=quote, decided_at=decided_at, now=now,
        retain_candidate=saved is None or saved['snapshot']['version'] != 'close_entry_snapshot/v1')
    # A new proof is retained only after the ordinary current Risk gate approves it.
    if runtime.load_intent(mock_automation_intent_id(spec.spec_id, decision.decision_id)) is None:
        gate = assess_mock_automation_decision_from_risk(repository, runtime, decision, recovery, risk, plan=plan, **scope)
        if gate.status is MockAutomationGateStatus.BLOCKED:
            return gate, None, None
    repository.save_close_entry_evidence(runtime.account_ref, plan_id, evidence)
    return dispatch_mock_automation_decision_from_risk(repository, runtime, decision, recovery, risk, plan=plan, **scope)


def close_exit_key(plan: OrderPlanRef) -> str:
    return content_id("close_exit_slot", asdict(plan))


def validate_close_stop_notice(spec, *, run_id, root, fill_events, notice, stop_risk, stop_loss_bps, event, now):
    """Replay the first observed stop against its immutable Risk and original BUY fill prefix."""
    from .mock_automation_risk import _sha256, validate_mock_stop_loss_proof

    if not isinstance(notice, dict) or stop_risk is None:
        raise ValueError('CLOSE historical stop Risk is unavailable')
    trigger = notice.get('risk_trigger') or {}
    proof = trigger.get('stop_loss')
    requested = datetime.fromisoformat(notice['requested_at'])
    if ((notice.get('version'), notice.get('state'), notice.get('account_ref'), notice.get('spec_id'),
         notice.get('execution_run_id'), notice.get('order_authorized'), notice.get('transport_state'))
            != ('mock-protection-review/v1', 'PENDING', spec.account_scope.account_ref, spec.spec_id, run_id, False, 'OFF')
            or requested.utcoffset() is None or requested > now
            or (stop_risk.account_ref, stop_risk.execution_run_id, stop_risk.binding_revision)
            != (spec.account_scope.account_ref, run_id, spec.binding_revision)
            or (trigger.get('snapshot_id'), trigger.get('reconciliation_revision'), trigger.get('source'))
            != (stop_risk.snapshot_id, stop_risk.reconciliation_revision, 'acknowledged_account_monitor')
            or trigger.get('disposition') not in {'EXIT_REQUIRED', 'RECONCILE_REQUIRED'}):
        raise ValueError('CLOSE historical stop scope changed')
    validate_mock_stop_loss_proof(proof, spec, stop_risk, stop_loss_bps=stop_loss_bps,
        ipo_policy=CloseIpoSetup.from_document(event['close_ipo_entry_setup']).policy, ipo_candidate=event)
    if (notice.get('input_ref') != proof['input_ref'] or type(notice.get('revision')) is not int or notice['revision'] <= 0
            or (trigger.get('account_ref'), trigger.get('execution_run_id'), trigger.get('binding_revision'))
            != (stop_risk.account_ref, run_id, spec.binding_revision)
            or trigger.get('cause_signature') != _sha256({'stop_loss': proof,
                'disposition': trigger['disposition'], 'reasons': trigger['reasons']})):
        raise ValueError('CLOSE historical stop observation changed')
    quote = _close_quote(spec, {**proof, 'durable': False, 'market_status': 'TRADABLE'}, requested, requested)
    if (proof['symbol'] != root.intent.symbol
            or datetime.fromisoformat(proof['available_at']) < stop_risk.observed_at
            or not 0 <= (requested - stop_risk.account_as_of).total_seconds() <= spec.maximum_data_gap_seconds
            or not 0 <= (requested - stop_risk.observed_at).total_seconds() <= spec.maximum_data_gap_seconds):
        raise ValueError('CLOSE historical stop predates its acknowledged fill basis')
    quantity = basis = 0
    for fill in (row for row in fill_events if row['event_type'] == 'FILL'):
        if quantity >= proof['quantity']:
            break
        if datetime.fromisoformat(fill['occurred_at']) > stop_risk.account_as_of:
            raise ValueError('CLOSE historical stop uses a later BUY fill')
        basis = mock_owned_buy_basis(quantity, basis, fill['quantity'], fill['price'])
        quantity += fill['quantity']
    if (quantity, basis) != (proof['quantity'], proof['entry_price_won']):
        raise ValueError('CLOSE historical stop original BUY fill prefix changed')
    return quote


def validate_close_entry_deadline(spec, *, run_id, parameters, event, entry_evidence, root, head,
                                  fill_events, recovery, risk, now, protection_notice=None, stop_risk=None):
    """Authorize only cancellation of the original, currently mapped BUY remainder."""
    from .mock_automation_execution import mock_automation_intent_id

    entered_at = datetime.fromisoformat(entry_evidence["decision"]["decided_at"])
    expected, decision, plan = build_close_entry_evidence(spec, run_id=run_id, parameters=parameters,
        event=event, quote=entry_evidence["snapshot"]["quote"], decided_at=entered_at, now=entered_at,
        retain_candidate=entry_evidence['snapshot']['version'] != 'close_entry_snapshot/v1')
    if (expected != entry_evidence or root is None
            or root.state.value not in {"ACCEPTED", "PARTIALLY_FILLED"}
            or (root.intent.intent_id, root.intent.account_ref, root.intent.run_id, root.intent.environment,
                root.intent.symbol, root.intent.side, root.intent.plan, root.intent.policy_version)
            != (mock_automation_intent_id(spec.spec_id, decision.decision_id), spec.account_scope.account_ref,
                run_id, "mock", event["symbol"], OrderSide.BUY, plan, CLOSE_ORDER_POLICY_VERSION)
            or not root.broker_order_id or root.last_broker_as_of is None
            or not 0 <= root.detailed_filled_quantity == root.filled_quantity == root.broker_reported_filled_quantity < root.intent.quantity
            or (head.plan.plan_id, head.plan.policy_ref, head.symbol, head.quantity)
            != (plan.plan_id, plan.policy_ref, event["symbol"], root.detailed_filled_quantity)
            or head.unreconciled_sell_quantity):
        raise ValueError("CLOSE deadline requires the confirmed original BUY remainder")
    deadline = datetime.fromisoformat(event["close_ipo_entry_setup"]["entry_deadline"])
    if protection_notice is not None:
        validate_close_stop_notice(spec, run_id=run_id, root=root, fill_events=fill_events,
            notice=protection_notice, stop_risk=stop_risk, stop_loss_bps=parameters['policy']['protection_loss_bps'], event=event, now=now)
    elif now < deadline:
        raise ValueError("CLOSE entry deadline has not arrived")
    age = spec.maximum_data_gap_seconds
    account = recovery.account
    orders = [order for order in recovery.orders if order.broker_order_id == root.broker_order_id]
    if (len(orders) != 1 or age is None or (risk.account_ref, risk.execution_run_id, risk.binding_revision)
            != (spec.account_scope.account_ref, run_id, spec.binding_revision)
            or risk.account_revision != mock_automation_risk_account_revision(recovery)
            or (account.account_ref, account.as_of) != (risk.account_ref, risk.account_as_of)
            or risk.submission_unknown_count or risk.balance_mismatch_count
            or head.fill_cursor > risk.execution_event_high_watermark
            or dict(risk.owned_position_quantities).get(event["symbol"], 0) < head.quantity
            or account.positions.get(event["symbol"], 0) < head.quantity
            or any(not 0 <= (now - at).total_seconds() <= age
                   for at in (risk.account_as_of, risk.observed_at, root.last_broker_as_of))):
        raise ValueError("CLOSE deadline requires a current matching broker and Risk cut")
    order = orders[0]
    if ((order.account_ref, order.symbol, order.state, order.filled_quantity, order.remaining_quantity, order.as_of)
            != (root.intent.account_ref, root.intent.symbol, root.state, root.detailed_filled_quantity,
                root.intent.quantity - root.detailed_filled_quantity, root.last_broker_as_of)
            or any(fill.execution_id not in root.fill_ids for fill in order.fills)):
        raise ValueError("CLOSE deadline broker order changed")
    fills = [row for row in fill_events if row["event_type"] == "FILL"]
    if (any(row["event_type"] == "CANCEL_STARTED" for row in fill_events)
            or sum(row["quantity"] for row in fills) != head.quantity
            or tuple(row["broker_execution_id"] for row in fills) != root.fill_ids):
        raise ValueError("CLOSE deadline native fill or cancellation history changed")


def prepare_close_entry_deadline_claim(repository, runtime, execution, spec, root, *, parameters, event, recovery, risk,
                                       protection_notice=None):
    entry = repository.load_close_entry_evidence(runtime.account_ref, root.intent.plan.plan_id)
    head = execution.load_plan_position(runtime.account_ref, runtime.run_id, root.intent.symbol, root.intent.plan)
    if entry is None:
        raise ValueError("CLOSE original entry evidence is missing")
    validate_close_entry_deadline(spec, run_id=runtime.run_id, parameters=parameters, event=event,
        entry_evidence=entry, root=root, head=head, fill_events=execution.events(root.intent.intent_id),
        recovery=recovery, risk=risk, now=runtime.server_now(), protection_notice=protection_notice,
        stop_risk=(repository.load_mock_automation_risk_snapshot(runtime.account_ref,
            protection_notice['risk_trigger']['snapshot_id']) if protection_notice is not None else None))
    return CloseEntryDeadlineCancelClaim(head.plan, risk.snapshot_id, risk.reconciliation_revision, recovery, protection_notice)


def cancel_close_entry_deadline(repository, runtime, execution, spec, expected, *, parameters, event,
                                recovery, risk, control_revision):
    """Claim once through the existing cancel primitive; late broker fills remain authoritative."""
    def build_claim(root):
        return prepare_close_entry_deadline_claim(repository, runtime, execution, spec, root,
            parameters=parameters, event=event, recovery=recovery, risk=risk)

    return runtime.cancel_automation_intent(expected, spec_id=spec.spec_id, control_revision=control_revision,
                                          build_planned_cancel_claim=build_claim)


def _completed_close_gap(position, observations, decided_at):
    setup, window = position.setup, position.setup.next_session
    if window is None:
        return None  # Older stored setups retain their 0B protection and deadline path.
    if ((window.venue, window.trading_date, window.starts_at, window.session, window.phase, window.support)
            != ('KRX', setup.next_date, setup.next_open_at, MarketSession.KRX_REGULAR, MarketPhase.CONTINUOUS,
                SessionSupport.SUPPORTED) or decided_at.astimezone(KST).date() != setup.next_date):
        return None
    if len(observations) != 2 or len({row['revision_id'] for row in observations}) != 2:
        raise ValueError('CLOSE gap requires two distinct native bar revisions')
    frames = []
    point = observations[0] if observations[0]['kind'] == 'closing_auction_price' else None
    if point is not None:
        payload, auction = point['payload'], setup.close_auction
        if ((point['subject'], point['venue'], point['unit'], point['value_kind'], point['completeness'],
             point['origin'], point['source_id']) != (setup.symbol + ':KRX', 'KRX', 'won', 'actual', 'complete',
                                                      'realtime', 'kiwoom-websocket-0B')
                or payload.get('version') != 'closing_auction_price/v1' or payload.get('window_closed') is not True
                or payload.get('capture_quality') != 'complete' or payload.get('code') != setup.symbol
                or payload.get('market') != 'KRX' or payload.get('price_unit') != 'won'
                or type(payload.get('price_won')) is not int or payload['price_won'] <= 0
                or not str(payload.get('input_ref', '')).startswith('collector-trade:')
                or (payload.get('session'), payload.get('phase'), payload.get('schedule_version'))
                != (auction.session.value, auction.phase.value, auction.schedule_version)
                or datetime.fromisoformat(payload['auction_start']) != auction.starts_at
                or datetime.fromisoformat(payload['auction_end']) != auction.ends_at
                or datetime.fromisoformat(payload['event_at']) != datetime.fromisoformat(point['effective_at'])
                or not 0 <= (datetime.fromisoformat(point['effective_at']) - auction.ends_at).total_seconds() <= KRX_AUCTION_RANDOM_END_SECONDS
                or not datetime.fromisoformat(point['effective_at']) <= datetime.fromisoformat(point['available_at']) <= setup.next_open_at
                or datetime.fromisoformat(payload['available_at']) != datetime.fromisoformat(point['available_at'])):
            raise ValueError('CLOSE gap requires the original actual closing price observation')
    for row in observations[1:] if point is not None else observations:
        payload = row['payload']
        if (row['subject'] not in {setup.symbol, setup.symbol + ':KRX'}
                or (row['kind'], row['venue'], row['value_kind'], row['completeness'])
                != ('minute_bar', 'KRX', 'actual', 'complete')
                or payload.get('window_closed') is not True or payload.get('capture_quality') != 'complete'
                or payload.get('market') != 'KRX' or payload.get('code') != setup.symbol
                or payload.get('price_unit') != 'won'
                or any(type(payload.get(key)) is not int for key in
                       ('open', 'high', 'low', 'close', 'volume', 'trade_value_million_won'))):
            raise ValueError('CLOSE gap requires completed actual KRX bars in won')
        frames.append(KrxMinuteBarFrame(row['revision_id'], row['observation_key'], setup.symbol,
            payload['bar_start'], payload['bar_end'], row['available_at'],
            payload['open'], payload['high'], payload['low'], payload['close'], payload['volume'],
            payload['trade_value_million_won'], bool(payload.get('session_finalized', False)),
            payload['capture_quality'], payload['finalization_source'], market_session=payload['session'],
            market_phase=payload['phase'], schedule_version=payload['schedule_version']))
    result = (evaluate_close_gap_at_price(position, point['payload']['price_won'], frames,
                  session=window, cut=decided_at, refs=(point['revision_id'],)) if point is not None else
              evaluate_close_gap(position, frames, session=window, cut=decided_at))
    return result if result.action in {'EXIT', 'REDUCE'} else None


def build_close_exit_evidence(spec, *, run_id, parameters, event, entry_evidence, root, head, fill_events,
                              trigger_quote, quote, decided_at, now, gap_observations=None,
                              protection_notice=None, stop_risk=None):
    """Manage only the original plan's detailed fills, independently of new-entry settings."""
    entered_at = datetime.fromisoformat(entry_evidence["decision"]["decided_at"])
    expected_entry, entry_decision, original_plan = build_close_entry_evidence(spec, run_id=run_id,
        parameters=parameters, event=event, quote=entry_evidence["snapshot"]["quote"], decided_at=entered_at, now=entered_at,
        retain_candidate=entry_evidence['snapshot']['version'] != 'close_entry_snapshot/v1')
    from .mock_automation_execution import mock_automation_intent_id

    if (entry_evidence != expected_entry or root is None or root.state not in TERMINAL_ORDER_STATES
            or (root.intent.intent_id, root.intent.run_id, root.intent.account_ref, root.intent.environment,
                root.intent.side, root.intent.symbol, root.intent.policy_version, getattr(root.intent, "plan", None))
            != (mock_automation_intent_id(spec.spec_id, entry_decision.decision_id), run_id,
                spec.account_scope.account_ref, "mock", OrderSide.BUY, event["symbol"], CLOSE_ORDER_POLICY_VERSION, original_plan)
            or (head.plan.plan_id, head.plan.policy_ref) != (original_plan.plan_id, original_plan.policy_ref)
            or head.symbol != event["symbol"] or head.quantity <= 0 or head.unreconciled_sell_quantity):
        raise ValueError("CLOSE exit requires the original terminal entry and confirmed plan quantity")
    fills = [row for row in fill_events if row["event_type"] == "FILL"]
    quantity = basis = 0
    for fill in fills:
        if (not fill.get("broker_execution_id") or type(fill["quantity"]) is not int or fill["quantity"] <= 0
                or type(fill["price"]) is not int or fill["price"] <= 0):
            raise ValueError("CLOSE entry detailed fill basis is invalid")
        basis = mock_owned_buy_basis(quantity, basis, fill["quantity"], fill["price"])
        quantity += fill["quantity"]
    if (quantity != root.detailed_filled_quantity or quantity < head.quantity
            or len(fills) != len(root.fill_ids)
            or {row["broker_execution_id"] for row in fills} != set(root.fill_ids)):
        raise ValueError("CLOSE entry detailed fill basis is incomplete")
    trigger = (validate_close_stop_notice(spec, run_id=run_id, root=root, fill_events=fill_events,
        notice=protection_notice, stop_risk=stop_risk, stop_loss_bps=parameters['policy']['protection_loss_bps'], event=event, now=now)
        if protection_notice is not None else _close_quote(spec, trigger_quote, decided_at, now))
    execution_quote = _close_quote(spec, quote, decided_at, now)
    if (datetime.fromisoformat(trigger["available_at"]) > datetime.fromisoformat(execution_quote["available_at"])
            or execution_quote["price_won"] * 10_000 < trigger["price_won"] * (
                10_000 - spec.close_execution_policy.protective_slippage_bps)):
        raise ValueError("CLOSE exit quote exceeds the approved protective slippage bound")
    setup = CloseIpoSetup.from_document(event["close_ipo_entry_setup"])
    local = decided_at.astimezone(KST)
    exit_quantity = head.quantity
    reduced_quantity = quantity - head.quantity
    gap_result = None
    if protection_notice is not None or trigger["price_won"] * 10_000 <= basis * (10_000 - setup.policy.protection_loss_bps):
        reason = ('ipo_breakout_structure_failed_0B' if protection_notice is not None
                  and protection_notice['risk_trigger']['stop_loss'].get('version') == 'ipo_structure_stop/v1'
                  else 'filled_position_protection_0B')
    elif setup.policy.variant == 'IPO_BREAKOUT':
        if decided_at >= setup.entry_deadline:
            reason = 'ipo_intraday_exit_deadline_0B'
        elif trigger['price_won'] < setup.breakout_level:
            reason = 'ipo_breakout_structure_failed_0B'
        else:
            return None
    elif local.date() > setup.next_date:
        reason = "next_session_management_overdue_0B"
    elif local.date() == setup.next_date and decided_at >= setup.next_open_at + timedelta(
            minutes=setup.policy.exit_minutes_after_open):
        reason = "next_session_exit_deadline_0B"
    elif gap_observations is not None and (gap_result := _completed_close_gap(
            CloseIpoPosition(setup, head.quantity, basis, reduced_quantity), gap_observations, decided_at)) is not None:
        exit_quantity = gap_result.quantity
        reason = ('ipo_adverse_gap_partial_reduction' if gap_result.action == 'REDUCE' else
                  'adverse_gap_full_exit_closing_trade_and_completed_open' if gap_observations[0]['kind'] == 'closing_auction_price'
                  else 'adverse_gap_full_exit_completed_bars')
    else:
        return None
    snapshot = {"version": "close_exit_snapshot/v1", "spec_id": spec.spec_id,
        "candidate_package_hash": spec.candidate_package_hash, "account_ref": spec.account_scope.account_ref,
        "execution_run_id": run_id, "entry_proof_id": entry_evidence["proof_id"], "plan": asdict(head.plan),
        "entry_intent_id": root.intent.intent_id, "quantity": exit_quantity, "fill_cursor": head.fill_cursor,
        "entry_price_won": basis, "entry_fill_ref": content_id("close_fills", fills),
        "trigger_quote": trigger, "quote": execution_quote, "reason": reason}
    if gap_result is not None:
        snapshot['gap_observations'] = gap_observations
    if reason == 'adverse_gap_full_exit_closing_trade_and_completed_open':
        snapshot['version'] = 'close_exit_snapshot/v3'
    if protection_notice is not None:
        snapshot.update(version='close_exit_snapshot/v2', protection_notice=protection_notice)
    if setup.policy.variant != 'CLOSE':
        snapshot.update(version='close_exit_snapshot/v4', position_quantity=head.quantity,
                        reduced_quantity=reduced_quantity)
    state = StrategyState(status="open", symbol=event["symbol"], entry_price=basis,
        position_quantity=head.quantity, opened_at=fills[0]["occurred_at"]).to_dict()
    body = {"run_id": run_id, "snapshot_id": content_id("snapshot", snapshot), "symbol": event["symbol"],
        "proposal": "EXIT", "final_action": "EXIT", "quantity": exit_quantity,
        "signal_reference_price": execution_quote["price_won"], "required_capital_won": 0,
        "reasons": (reason,), "constraints": (CLOSE_ORDER_POLICY_VERSION,), "state_before": state, "state_after": state,
        "decided_at": decided_at.isoformat()}
    decision = StrategyDecision(decision_id=content_id("decision", body), **body)
    document = json.loads(json.dumps({"snapshot": snapshot, "decision": decision.to_dict()}, allow_nan=False))
    return {**document, "proof_id": content_id("close_exit", document)}, decision, head.plan


def dispatch_close_exit(repository, runtime, execution, spec, plan, *, parameters, event, trigger_quote, quote, recovery, risk,
                        gap_observations=None, protection_notice=None):
    """Restore frozen management from its native entry and use the existing SELL gate."""
    from .mock_automation_execution import mock_automation_intent_id

    saved = repository.load_close_exit_evidence(runtime.account_ref, close_exit_key(plan))
    scope = {"account_ref": runtime.account_ref, "spec_id": spec.spec_id, "strategy_ref": spec.strategy_ref}
    now = decided_at = runtime.server_now()
    if saved is not None:
        snapshot = saved["snapshot"]
        if (saved["proof_id"] != content_id("close_exit", {key: value for key, value in saved.items() if key != "proof_id"})
                or snapshot["plan"] != asdict(plan) or snapshot["spec_id"] != spec.spec_id
                or snapshot["execution_run_id"] != runtime.run_id or snapshot["account_ref"] != runtime.account_ref):
            raise ValueError("stored CLOSE exit evidence changed")
        raw = {**saved["decision"], "reasons": tuple(saved["decision"]["reasons"]),
               "constraints": tuple(saved["decision"]["constraints"])}
        decision = StrategyDecision(**raw)
        if runtime.load_intent(mock_automation_intent_id(spec.spec_id, decision.decision_id)) is not None:
            return dispatch_mock_automation_decision_from_risk(repository, runtime, decision, recovery, risk, plan=plan, **scope)
        trigger_quote, quote = snapshot["trigger_quote"], snapshot["quote"]
        gap_observations = snapshot.get('gap_observations')
        protection_notice = snapshot.get('protection_notice')
        decided_at = datetime.fromisoformat(decision.decided_at)
    entry = repository.load_close_entry_evidence(runtime.account_ref, plan.plan_id)
    if entry is None:
        raise ValueError("CLOSE original entry evidence is missing")
    root_id = mock_automation_intent_id(spec.spec_id, entry["decision"]["decision_id"])
    root = execution.load(root_id)
    head = execution.load_plan_position(runtime.account_ref, runtime.run_id, event["symbol"], plan)
    if head.plan != plan:
        raise ValueError("CLOSE plan revision changed before management")
    prepared = build_close_exit_evidence(spec, run_id=runtime.run_id, parameters=parameters, event=event,
        entry_evidence=entry, root=root, head=head, fill_events=execution.events(root_id),
        trigger_quote=trigger_quote, quote=quote, decided_at=decided_at, now=now, gap_observations=gap_observations,
        protection_notice=protection_notice,
        stop_risk=(repository.load_mock_automation_risk_snapshot(runtime.account_ref,
            protection_notice['risk_trigger']['snapshot_id']) if protection_notice is not None else None))
    if prepared is None:
        return None, None, None
    evidence, decision, plan = prepared
    gate = assess_mock_automation_decision_from_risk(repository, runtime, decision, recovery, risk, plan=plan, **scope)
    if gate.status is MockAutomationGateStatus.BLOCKED:
        return gate, None, None
    repository.save_close_exit_evidence(runtime.account_ref, close_exit_key(plan), evidence)
    return dispatch_mock_automation_decision_from_risk(repository, runtime, decision, recovery, risk, plan=plan, **scope)
