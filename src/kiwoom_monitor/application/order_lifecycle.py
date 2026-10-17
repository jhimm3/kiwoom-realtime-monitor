"""Conservative mock-broker order lifecycle and reconciliation policy."""

from __future__ import annotations

from kiwoom_monitor.domain.time_basis import kst_now

from datetime import datetime, timedelta, timezone
from dataclasses import asdict, dataclass, replace
import hashlib
import json
import math
from typing import Callable, Protocol


from kiwoom_monitor.application.market_session_schedule import mock_order_entry_decision
from kiwoom_monitor.domain.order_contract import (
    AccountSnapshot,
    BrokerOrderSnapshot,
    BrokerSubmission,
    OrderIntent,
    OrderSide,
    OrderState,
    OrderType,
    TERMINAL_ORDER_STATES,
    order_event,
)
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_execution import SubmissionRejected, SubmissionUnknown
from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRecord, ExecutionRepository


def protective_cancel_recovery(record, events, policy, frozen, now):
    """Bounded response policy; native events, never RAM counters, spend retries."""
    counts = {}
    for row in events:
        if row.get('event_type') == 'CANCEL_STARTED':
            key = row['intent_id']; counts[key] = counts.get(key,0) + 1
    retries_used = sum(max(0,count-1) for count in counts.values())
    events = tuple(row for row in events if row.get('intent_id') == record.intent.intent_id)
    claims=[row for row in events if row.get('event_type')=='CANCEL_STARTED']
    responses=[row for row in events if row.get('event_type') in {
        'CANCEL_STARTED','CANCEL_REJECTED','CANCEL_RESPONSE_UNKNOWN','CANCEL_ACCEPTED'}]
    end=min(policy.valid_until,policy.protection_deadline)
    result={'version':'protective-cancel-recovery/v1','root_intent_id':policy.anchor_intent_id,
        'managed_intent_id':record.intent.intent_id,'policy_id':frozen.policy_id,
        'cancel_claims':len(claims),'retries_used':retries_used,
        'last_response_event_id':responses[-1]['event_id'] if responses else None,
        'next_check_at':end.isoformat(),'state':'WAIT_RECONCILIATION','reason':'cancel_confirmation_required',
        'order_authorized':False}
    if policy.purpose!='PROTECTIVE_EXIT' or not claims:
        return {**result,'state':'ESCALATE','reason':'protective_native_cancel_not_established','next_check_at':None}
    if record.state in TERMINAL_ORDER_STATES:
        return {**result,'state':'RESOLVED','reason':'native_terminal_confirmation','next_check_at':None}
    if now>=end:
        return {**result,'state':'ESCALATE','reason':'original_protection_deadline_reached','next_check_at':None}
    if not responses or responses[-1]['event_type']!='CANCEL_REJECTED':
        return result  # ACK/unknown/lost response is never retry or replacement authority.
    if retries_used>=frozen.maximum_protective_cancel_retries:
        return {**result,'state':'ESCALATE','reason':'protective_cancel_retry_budget_exhausted','next_check_at':None}
    due=datetime.fromisoformat(responses[-1]['occurred_at'])+timedelta(seconds=frozen.protective_cancel_retry_delay_seconds)
    if due>=end:
        return {**result,'state':'ESCALATE','reason':'retry_would_exceed_original_deadline','next_check_at':None}
    result.update(next_check_at=due.isoformat(),attempt=len(claims)+1,reject_event_id=responses[-1]['event_id'])
    if now<due:
        return {**result,'state':'WATCH','reason':'protective_cancel_retry_delay'}
    if (record.state not in {OrderState.ACCEPTED,OrderState.PARTIALLY_FILLED}
            or record.filled_quantity!=record.detailed_filled_quantity
            or record.broker_reported_filled_quantity>record.detailed_filled_quantity
            or record.last_broker_as_of is None
            or record.last_broker_as_of<=datetime.fromisoformat(responses[-1]['occurred_at'])):
        return {**result,'state':'WAIT_RECONCILIATION','reason':'post_rejection_confirmation_required'}
    return {**result,'state':'RETRY_READY','reason':'confirmed_rejected_cancel_and_remaining_order'}


UNFILLED_MANAGEMENT_SHADOW_VERSION = 'unfilled-order-management-shadow/v1'
UNFILLED_PREFLIGHT_VERSION = 'unfilled-order-management-preflight/v1'
UNFILLED_PREFLIGHT_MAX_BYTES = 1024 * 1024
UNFILLED_ATTEMPT_PLAN_VERSION = 'unfilled-native-attempt-plan/v1'


def unfilled_plan_intent(value):
    """Decode the existing immutable OrderIntent contract, without queueing it."""
    body = dict(value)
    body['side'] = OrderSide(body['side'])
    body['order_type'] = OrderType(body['order_type'])
    for name in ('created_at', 'expires_at'):
        body[name] = datetime.fromisoformat(body[name])
    return OrderIntent(**body)


def seal_unfilled_attempt_plan(body):
    body = {key: value for key, value in body.items() if key != 'receipt_id'}
    encoded = json.dumps(body, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if len(encoded) > UNFILLED_PREFLIGHT_MAX_BYTES:
        raise ValueError('unfilled_attempt_plan_budget_exceeded')
    return {**body, 'receipt_id': 'unfilled-attempt-plan:' + hashlib.sha256(encoded).hexdigest()}


def validate_unfilled_attempt_plan(document):
    """Fail closed on corrupted/ambiguous manifests; all slots remain non-executable."""
    try:
        if (document['version'] != UNFILLED_ATTEMPT_PLAN_VERSION
                or document['order_authorized'] is not False
                or seal_unfilled_attempt_plan(document) != document):
            raise ValueError('unfilled_attempt_plan_integrity_mismatch')
        anchor = document['anchor']
        root = unfilled_plan_intent(anchor['root_intent'])
        scope = anchor['scope']
        if (scope['account_ref'] != root.account_ref or scope['execution_run_id'] != root.run_id
                or anchor['policy']['anchor_intent_id'] != root.intent_id
                or anchor['policy']['anchor_price_won'] != root.limit_price):
            raise ValueError('unfilled_attempt_plan_anchor_mismatch')
        children = document['children']
        if not isinstance(children, list) or len(children) > min(64, anchor['policy']['maximum_reprices']):
            raise ValueError('unfilled_attempt_plan_slot_budget_exceeded')
        parent_id = root.intent_id
        ids = []
        for ordinal, child in enumerate(children, 1):
            intent = unfilled_plan_intent(child['intent'])
            expected_id = 'unfilled-native-child:' + hashlib.sha256(f'{root.intent_id}:{ordinal}'.encode()).hexdigest()
            if (type(child['attempt']) is not int or child['attempt'] != ordinal
                    or child['parent_intent_id'] != parent_id or child['root_intent_id'] != root.intent_id
                    or (intent.account_ref, intent.run_id, intent.symbol, intent.side) != (
                        root.account_ref, root.run_id, root.symbol, root.side)
                    or intent.order_type is not OrderType.LIMIT
                    or type(intent.quantity) is not int or type(intent.limit_price) is not int
                    or intent.intent_id != expected_id or intent.intent_id in ids):
                raise ValueError('unfilled_attempt_plan_child_lineage_mismatch')
            ids.append(intent.intent_id); parent_id = intent.intent_id
        for field in ('native_claimed_intent_ids', 'native_submitted_intent_ids'):
            observed = document[field]
            if (not isinstance(observed, list) or len(set(observed)) != len(observed)
                    or any(value not in ids for value in observed)):
                raise ValueError('unfilled_attempt_plan_native_history_invalid')
        if not set(document['native_submitted_intent_ids']).issubset(document['native_claimed_intent_ids']):
            raise ValueError('unfilled_attempt_plan_native_history_invalid')
        if document['native_claimed_intent_ids'] != ids[:len(document['native_claimed_intent_ids'])]:
            raise ValueError('unfilled_attempt_plan_native_history_invalid')
        for counter, field in (('native_reprices_used', 'native_claimed_intent_ids'),
                               ('native_submission_attempts', 'native_submitted_intent_ids')):
            if counter in document and (type(document[counter]) is not int or document[counter] != len(document[field])):
                raise ValueError('unfilled_attempt_plan_native_counter_invalid')
        if 'transport_state' in document and document['transport_state'] != 'OFF':
            raise ValueError('unfilled_attempt_plan_transport_must_remain_off')
        gate = document.get('replacement_gate')
        if gate is not None:
            pending = children[-1]['intent'] if len(children) > len(document['native_claimed_intent_ids']) else None
            if (gate['version'] != 'mock-unfilled-replacement-gate/v1'
                    or gate['order_authorized'] is not False or gate['transport_state'] != 'OFF'
                    or gate['child_payload'] != pending
                    or gate['child_intent_id'] != (pending['intent_id'] if pending else None)
                    or gate['root_intent_id'] != root.intent_id):
                raise ValueError('unfilled_replacement_gate_fixed_child_mismatch')
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError('unfilled_attempt_plan_shape_invalid') from error
    return document


def project_unfilled_native_attempts(plan, root_record, native_records, native_events, *, root_events=()):
    """Count native claims conservatively; SUBMISSION_STARTED is a separate proof.

    QUEUED already consumes a slot. A crash before network submission cannot make
    that claim disappear or justify a different child ID. No broker calls here.
    """
    validate_unfilled_attempt_plan(plan)
    from kiwoom_monitor.application.position_target import _json_value
    root = json.loads(json.dumps(asdict(root_record.intent), default=_json_value))
    if root != plan['anchor']['root_intent']:
        raise ValueError('unfilled_native_root_changed')
    if len(native_records) != len(plan['children']) or len(native_events) != len(native_records):
        raise ValueError('unfilled_native_projection_incomplete')
    if len(root_events) > 400 or any(len(events) > 400 for events in native_events):
        raise ValueError('unfilled_native_event_budget_exceeded')
    claimed, submitted = [], []
    parent, parent_events, missing = root_record, root_events, False
    for slot, record, events in zip(plan['children'], native_records, native_events):
        if record is None:
            if events:
                raise ValueError('unfilled_native_events_without_intent')
            missing = True
            continue
        if missing or record.intent != unfilled_plan_intent(slot['intent']):
            raise ValueError('unfilled_native_child_content_or_prefix_conflict')
        historical_cancel = any(
            event.get('intent_id') == parent.intent.intent_id and event.get('state') == OrderState.CANCELLED.value
            and event.get('broker_as_of') and event.get('received_at')
            and datetime.fromisoformat(event['broker_as_of']) <= record.intent.created_at
            and datetime.fromisoformat(event['received_at']) <= record.intent.created_at
            for event in parent_events)
        if (parent.state is not OrderState.CANCELLED or parent.filled_quantity != parent.detailed_filled_quantity
                or parent.broker_reported_filled_quantity > parent.detailed_filled_quantity
                or parent.last_broker_as_of is None
                or (parent.last_broker_as_of > record.intent.created_at and not historical_cancel)
                or record.intent.quantity > parent.intent.quantity - parent.filled_quantity):
            raise ValueError('unfilled_native_child_parent_not_safely_cancelled')
        claimed.append(record.intent.intent_id)
        starts = [event for event in events if event.get('event_type') == 'SUBMISSION_STARTED']
        if (any(event.get('intent_id') != record.intent.intent_id for event in events)
                or len(starts) > 1
                or any(event.get('state') != OrderState.SUBMISSION_UNKNOWN.value for event in starts)):
            raise ValueError('unfilled_native_submission_proof_invalid')
        if starts:
            submitted.append(record.intent.intent_id)
        elif record.state not in {OrderState.QUEUED, OrderState.REJECTED}:
            raise ValueError('unfilled_native_submission_proof_missing')
        parent, parent_events = record, events
    if (not set(plan['native_claimed_intent_ids']).issubset(claimed)
            or not set(plan['native_submitted_intent_ids']).issubset(submitted)):
        raise ValueError('unfilled_native_attempt_history_lost')
    if len(plan['children']) - len(claimed) > 1:
        raise ValueError('unfilled_native_unclaimed_slot_prefix_invalid')
    return {'native_claimed_intent_ids': claimed, 'native_submitted_intent_ids': submitted,
            'native_reprices_used': len(claimed), 'native_submission_attempts': len(submitted),
            'managed_record': parent}


def validate_unfilled_management_preflight(document):
    """Validate a checkpoint receipt, never convert it into execution authority."""
    if not isinstance(document, dict) or document.get('version') != UNFILLED_PREFLIGHT_VERSION:
        raise ValueError('unsupported_unfilled_preflight_receipt')
    if set(document) != {'version', 'anchor', 'native_reprices_used', 'reserved_preview_slots',
                         'reserved_child_intent_id', 'child_preview', 'result', 'order_authorized',
                         'native_owner_authority', 'next_check_at', 'receipt_id'}:
        raise ValueError('invalid_unfilled_preflight_fields')
    body = {key: value for key, value in document.items() if key != 'receipt_id'}
    encoded = json.dumps(body, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if len(encoded) > UNFILLED_PREFLIGHT_MAX_BYTES:
        raise ValueError('unfilled_preflight_receipt_budget_exceeded')
    digest = hashlib.sha256(encoded).hexdigest()
    if document.get('receipt_id') != 'unfilled-preflight:' + digest:
        raise ValueError('unfilled_preflight_integrity_mismatch')
    if (document.get('order_authorized') is not False
            or document.get('native_reprices_used') != 0
            or type(document.get('native_reprices_used')) is not int
            or type(document.get('reserved_preview_slots')) is not int
            or document['reserved_preview_slots'] not in (0, 1)):
        raise ValueError('invalid_unfilled_preflight_budget_or_authority')
    try:
        anchor = document['anchor']
        root = anchor['root_intent']['intent_id']
        if (anchor['policy']['anchor_intent_id'] != root
                or anchor['scope']['account_ref'] != anchor['root_intent']['account_ref']
                or anchor['scope']['execution_run_id'] != anchor['root_intent']['run_id']
                or document['native_owner_authority'] != 'not_granted'
                or document['result']['order_authorized'] is not False
                or document['next_check_at'] != document['result']['next_check_at']):
            raise ValueError('invalid_unfilled_preflight_lineage')
        slot_id = document['reserved_child_intent_id']
        if slot_id is not None and slot_id != _unfilled_child_preview_id(anchor):
            raise ValueError('invalid_unfilled_preflight_child_identity')
        if document['reserved_preview_slots'] != int(slot_id is not None):
            raise ValueError('invalid_unfilled_preflight_reservation')
        child = document['child_preview']
        if child is not None and (child['order_authorized'] is not False
                or child['intent_id'] != slot_id or child['attempt'] != 1
                or child['parent_intent_id'] != root or child['root_intent_id'] != root
                or document['result']['action'] != 'REPRICE'
                or child['decision_ref'] != document['result']['decision_id']
                or child['quantity'] != document['result']['replacement_quantity']
                or child['limit_price_won'] != document['result']['replacement_limit_price_won']):
            raise ValueError('invalid_unfilled_preflight_child_payload')
    except (KeyError, TypeError) as error:
        raise ValueError('invalid_unfilled_preflight_shape') from error
    return document


def _unfilled_child_preview_id(anchor):
    return 'unfilled-child-preview:' + hashlib.sha256(json.dumps(
        {'anchor': anchor, 'attempt': 1}, sort_keys=True, separators=(',', ':'),
    ).encode()).hexdigest()


def prepare_unfilled_management_preflight(*, scope, policy, record, broker_snapshot,
                                         target, observation, assessment, previous=None):
    """One durable replacement slot for the transmission-OFF integration phase.

    A preview reservation is not a submission attempt. Native child attempts need
    an owner-fenced execution-ledger projection before this seam can manage them.
    No caller-supplied counter can invent, reset or authorize such attempts.
    """
    if assessment.reprices_used != 0:
        raise ValueError('native_child_attempt_projection_required')
    if (set(scope) != {'account_ref', 'execution_run_id', 'spec_id',
                      'candidate_package_hash', 'control_revision'}
            or type(scope['control_revision']) is not int or scope['control_revision'] <= 0
            or any(type(scope[key]) is not str or not scope[key].strip()
                   for key in scope if key != 'control_revision')
            or scope['account_ref'] != record.intent.account_ref
            or scope['execution_run_id'] != record.intent.run_id):
        raise ValueError('unfilled_preflight_owner_scope_mismatch')
    result = evaluate_unfilled_order_management_shadow(
        policy, record, broker_snapshot, target, observation, assessment,
    )
    anchor = {'scope': dict(scope), 'root_intent': result['inputs']['record']['intent'],
              'policy': result['policy']}
    slot_id = None
    if previous is not None:
        validate_unfilled_management_preflight(previous)
        if previous['anchor'] != anchor:
            raise ValueError('unfilled_preflight_anchor_or_control_changed')
        slot_id = previous['reserved_child_intent_id']
    child = None
    if result['action'] == 'REPRICE':
        if slot_id is None:
            slot_id = _unfilled_child_preview_id(anchor)
        child = {'intent_id': slot_id, 'parent_intent_id': record.intent.intent_id,
                 'root_intent_id': policy.anchor_intent_id, 'attempt': 1,
                 'quantity': result['replacement_quantity'],
                 'limit_price_won': result['replacement_limit_price_won'],
                 'decision_ref': result['decision_id'], 'order_authorized': False}
    body = {'version': UNFILLED_PREFLIGHT_VERSION, 'anchor': anchor,
            'native_reprices_used': 0, 'reserved_preview_slots': int(slot_id is not None),
            'reserved_child_intent_id': slot_id, 'child_preview': child,
            'result': result, 'order_authorized': False,
            'native_owner_authority': 'not_granted',
            'next_check_at': result['next_check_at']}
    encoded = json.dumps(body, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if len(encoded) > UNFILLED_PREFLIGHT_MAX_BYTES:
        raise ValueError('unfilled_preflight_receipt_budget_exceeded')
    body['receipt_id'] = 'unfilled-preflight:' + hashlib.sha256(encoded).hexdigest()
    return body


@dataclass(frozen=True)
class UnfilledOrderManagementPolicy:
    """Explicit purpose profile for one anchored LIMIT order; no execution permission."""
    version: str
    purpose: str
    anchor_intent_id: str
    anchor_price_won: int
    anchor_price_input_ref: str
    anchor_available_at: datetime
    valid_until: datetime
    wait_until: datetime
    protection_deadline: datetime
    maximum_adverse_slippage_bps: int
    protective_slippage_bps: int
    maximum_reprices: int
    max_snapshot_age_seconds: int
    minimum_net_advantage_won: int
    uncertainty_multiplier: float
    minimum_paired_samples: int
    reprice_horizon_seconds: int

    def __post_init__(self):
        from kiwoom_monitor.application.position_target import _integer, _time
        if self.version != 'unfilled-purpose-policy/v1' or self.purpose not in {'BUY_OPPORTUNITY','TAKE_PROFIT','PROTECTIVE_EXIT'}:
            raise ValueError('unsupported unfilled purpose policy')
        for name in ('anchor_intent_id','anchor_price_input_ref'):
            if type(getattr(self,name)) is not str or not getattr(self,name).strip():
                raise ValueError(f'{name} must be explicit')
        for name in ('anchor_price_won','max_snapshot_age_seconds','reprice_horizon_seconds'):
            _integer(getattr(self,name),name,1)
        for name in ('maximum_adverse_slippage_bps','protective_slippage_bps','maximum_reprices','minimum_net_advantage_won'):
            _integer(getattr(self,name),name)
        _integer(self.minimum_paired_samples,'minimum_paired_samples',2)
        if self.minimum_paired_samples > 10_000:
            raise ValueError('unfilled paired sample budget exceeded')
        if type(self.uncertainty_multiplier) not in (int,float) or self.uncertainty_multiplier < 0:
            raise ValueError('invalid unfilled uncertainty multiplier')
        try:
            if not math.isfinite(self.uncertainty_multiplier): raise ValueError('nonfinite uncertainty multiplier')
        except OverflowError as error:
            raise ValueError('nonfinite uncertainty multiplier') from error
        start = _time(self.anchor_available_at)
        for name in ('valid_until','wait_until','protection_deadline'):
            if _time(getattr(self,name)) <= start:
                raise ValueError('unfilled policy deadline must follow anchor availability')
        if max(_time(self.wait_until),_time(self.protection_deadline)) > _time(self.valid_until):
            raise ValueError('unfilled deadlines exceed policy validity')


def anchored_unfilled_policy(intent, frozen, purpose):
    """Same anchored deadlines for native registration and explicit Paper facts."""
    end=intent.created_at+timedelta(seconds=frozen.maximum_management_seconds)
    return UnfilledOrderManagementPolicy('unfilled-purpose-policy/v1',purpose,intent.intent_id,
        intent.limit_price,'original-decision:'+intent.decision_id,intent.created_at,end,
        min(end,intent.expires_at),min(end,intent.created_at+timedelta(seconds=frozen.max_snapshot_age_seconds)),
        frozen.maximum_adverse_slippage_bps,frozen.protective_slippage_bps,frozen.maximum_reprices,
        frozen.max_snapshot_age_seconds,frozen.minimum_net_advantage_won,frozen.uncertainty_multiplier,
        frozen.minimum_paired_samples,frozen.reprice_horizon_seconds)


@dataclass(frozen=True)
class UnfilledRepriceEvidence:
    """Supplied paired WAIT-vs-REPRICE summary, distinct from R4 HOLD/action EV."""
    version: str
    input_ref: str
    population_ref: str
    intent_id: str
    pending_quantity: int
    candidate_limit_price_won: int
    horizon_seconds: int
    available_at: datetime
    paired_sample_count: int
    net_advantage_won: float
    net_standard_error_won: float
    cost_model_ref: str

    def __post_init__(self):
        from kiwoom_monitor.application.position_target import _integer,_time
        if self.version != 'unfilled-WAIT-vs-REPRICE/v1':
            raise ValueError('unfilled EV cannot reuse position action means')
        for name in ('input_ref','population_ref','intent_id','cost_model_ref'):
            if type(getattr(self,name)) is not str or not getattr(self,name).strip(): raise ValueError(f'{name} required')
        for name in ('pending_quantity','candidate_limit_price_won','horizon_seconds'):
            _integer(getattr(self,name),name,1)
        _integer(self.paired_sample_count,'paired_sample_count')
        _time(self.available_at)
        for name in ('net_advantage_won','net_standard_error_won'):
            value = getattr(self,name)
            try:
                valid = type(value) in (int,float) and math.isfinite(value)
            except OverflowError:
                valid = False
            if not valid: raise ValueError('unfilled evidence values must be finite')
        if self.net_standard_error_won < 0: raise ValueError('negative unfilled standard error')


def prepare_unfilled_reprice_evidence(outcomes, *, policy, record, pending_quantity, held_quantity,
                                     target_quantity, candidate_price, cost_model, population_ref,
                                     available_at, decision_time):
    """Bind distinct measured WAIT/REPRICE branches to a current pending order.

    Reuse the common paired statistics and fee subtraction. Source publication
    time is immutable; account/quote updates never renew the estimate's TTL.
    """
    from kiwoom_monitor.application.research_evaluation import (
        UnfilledActionOutcome, compare_position_action_outcomes, position_action_cost_model_ref,
    )
    from kiwoom_monitor.application.research_execution import SimulationCostModel
    from kiwoom_monitor.application.position_target import _time,_integer,_json_value
    for name,value,minimum in (('pending',pending_quantity,1),('held',held_quantity,0),
                               ('target',target_quantity,0),('candidate_price',candidate_price,1)):
        _integer(value,name,minimum)
    cut,published=_time(decision_time),_time(available_at)
    if (not isinstance(outcomes,tuple) or len(outcomes)>10_000
            or any(not isinstance(row,UnfilledActionOutcome) for row in outcomes)
            or len({row.input_resolution for row in outcomes})>1
            or not isinstance(population_ref,str) or not population_ref.strip()
            or published>cut or policy.purpose not in {'BUY_OPPORTUNITY','TAKE_PROFIT'}):
        raise ValueError('explicit bounded unfilled paired population required')
    if cost_model is not None and not isinstance(cost_model,SimulationCostModel):
        raise ValueError('registered unfilled cost model required')
    if (cut-published).total_seconds()>policy.max_snapshot_age_seconds:
        return None,{'version':'unfilled-paired-source/v1','ev_status':'EV_UNAVAILABLE',
                     'reason':'unfilled_source_publication_expired','available_at':published.isoformat(),'order_authorized':False}
    model=cost_model.to_dict() if cost_model else None
    cost_ref=position_action_cost_model_ref(model)
    valid=bool(cost_model and cost_model.source and cost_model.rate_basis!='unspecified'
               and cost_model.valid_from and _time(datetime.fromisoformat(cost_model.valid_from))<=cut
               <_time(datetime.fromisoformat(cost_model.valid_to)))
    active=record.state in {OrderState.ACCEPTED,OrderState.PARTIALLY_FILLED}
    binding={'policy_version':UNFILLED_MANAGEMENT_SHADOW_VERSION,'environment':'simulation','venue':'KRX',
        'source_kind':'simulation','execution_model_ref':'unfilled-paired-limit/v1',
        'initial_quantity':held_quantity,'pending_buy_quantity':pending_quantity if active and record.intent.side is OrderSide.BUY else 0,
        'pending_sell_quantity':pending_quantity if active and record.intent.side is OrderSide.SELL else 0,
        'target_quantity':target_quantity,'cost_model_ref':cost_ref,
        'cost_valid_from':cost_model.valid_from if cost_model else '',
        'cost_valid_to':cost_model.valid_to if cost_model else '',
        'symbol':record.intent.symbol,'side':record.intent.side.value,'purpose':policy.purpose,
        'pending_quantity':pending_quantity,'existing_limit_price_won':record.intent.limit_price,
        'candidate_limit_price_won':candidate_price,
        'order_state':record.state.value,
        'comparison_fields':('symbol','side','purpose','pending_quantity','existing_limit_price_won','candidate_limit_price_won','order_state'),
        'reasons':() if valid else ('current_unfilled_cost_model_unavailable',)}
    rows=compare_position_action_outcomes(outcomes,{'WAIT':binding,'REPRICE':binding},baseline_action='WAIT',
        decision_time=published,horizon_seconds=policy.reprice_horizon_seconds,population_ref=population_ref,
        input_resolution=outcomes[0].input_resolution if outcomes else 'second',minimum_samples=policy.minimum_paired_samples)
    result=next(row for row in rows if row['action']=='REPRICE')
    body={'version':'unfilled-paired-source/v1','root_intent_id':policy.anchor_intent_id,
        'managed_intent_id':record.intent.intent_id,'binding':binding,'population_ref':population_ref,
        'available_at':published.isoformat(),'comparison':result,'source_durability':'not_verified','order_authorized':False}
    encoded=json.dumps(body,sort_keys=True,separators=(',',':'),default=_json_value).encode()
    ref='unfilled-paired-source:'+hashlib.sha256(encoded).hexdigest()
    evidence=None
    if result['ev_status']=='EMPIRICAL_ESTIMATE':
        estimate=result['paired_delta']
        evidence=UnfilledRepriceEvidence('unfilled-WAIT-vs-REPRICE/v1',ref,population_ref,record.intent.intent_id,
            pending_quantity,candidate_price,policy.reprice_horizon_seconds,published,result['paired_sample_count'],
            estimate['mean_net_change_won'],estimate['net_standard_error_won'],cost_ref)
    return evidence,{'input_ref':ref,**body}


@dataclass(frozen=True)
class UnfilledOrderAssessment:
    thesis_valid: bool | None
    trend_allows_wait: bool | None
    risk_allows_new_risk: bool | None
    protection_required: bool
    thesis_input_ref: str
    thesis_available_at: datetime | None
    thesis_valid_until: datetime | None
    risk_input_ref: str
    risk_available_at: datetime | None
    risk_valid_until: datetime | None
    market_status: str
    candidate_limit_price_won: int | None
    price_input_ref: str
    price_available_at: datetime | None
    reprices_used: int
    reprice_evidence: UnfilledRepriceEvidence | None = None

    def __post_init__(self):
        from kiwoom_monitor.application.position_target import _integer,_time
        for name in ('thesis_valid','trend_allows_wait','risk_allows_new_risk'):
            if getattr(self,name) is not None and type(getattr(self,name)) is not bool: raise ValueError('assessment truth must be explicit')
        if type(self.protection_required) is not bool: raise ValueError('protection flag must be explicit')
        if self.market_status not in {'TRADABLE','HALTED','NO_LIQUIDITY','UNKNOWN'}: raise ValueError('unknown market condition')
        _integer(self.reprices_used,'reprices_used')
        if self.candidate_limit_price_won is not None: _integer(self.candidate_limit_price_won,'candidate_limit_price_won',1)
        for name in ('thesis_available_at','thesis_valid_until','risk_available_at','risk_valid_until','price_available_at'):
            if getattr(self,name) is not None: _time(getattr(self,name))
        for name in ('thesis_input_ref','risk_input_ref','price_input_ref'):
            if type(getattr(self,name)) is not str: raise ValueError('assessment input refs must be strings')
        if self.reprice_evidence is not None and not isinstance(self.reprice_evidence,UnfilledRepriceEvidence):
            raise ValueError('unfilled evidence must be typed')


def evaluate_unfilled_order_management_shadow(policy, record, broker_snapshot, target, observation, assessment, *, root_record=None):
    """Order-free purpose decision over existing lifecycle and account-wide DTOs."""
    from kiwoom_monitor.application.position_target import _integer,_time,_json_value,evaluate_position_target_shadow
    intent = record.intent
    anchor_intent = (root_record or record).intent
    cut = _time(target.decision_time)
    if (intent.account_ref,intent.environment,intent.venue,intent.symbol) != (
            target.account_ref,target.environment,target.venue,target.symbol) or observation.account.account_ref != intent.account_ref:
        raise ValueError('unfilled management scope mismatch')
    if policy.anchor_intent_id != anchor_intent.intent_id:
        raise ValueError('unfilled policy does not belong to original intent')
    if (anchor_intent.account_ref, anchor_intent.run_id, anchor_intent.side, anchor_intent.symbol) != (
            intent.account_ref, intent.run_id, intent.side, intent.symbol):
        raise ValueError('unfilled root/managed order scope mismatch')
    if target.account_input_ref != observation.input_ref or target.orders_input_ref != observation.input_ref:
        raise ValueError('unfilled target must bind the same combined account/orders observation')
    if not isinstance(record.state,OrderState) or not isinstance(intent.side,OrderSide):
        raise ValueError('unfilled state/side must use existing typed contracts')
    _integer(intent.quantity,'intent_quantity',1)
    if (intent.order_type is not OrderType.LIMIT or anchor_intent.order_type is not OrderType.LIMIT
            or policy.anchor_price_won != anchor_intent.limit_price or (
            _time(policy.anchor_available_at) > _time(anchor_intent.created_at))):
        raise ValueError('unfilled price anchor must preserve the original LIMIT intent')
    buy = intent.side is OrderSide.BUY
    if (policy.purpose == 'BUY_OPPORTUNITY') != buy or target.action not in ({'ADD','REENTER'} if buy else {'REDUCE','EXIT'}):
        raise ValueError('unfilled purpose/target side mismatch')
    _integer(record.filled_quantity,'filled_quantity')
    if record.filled_quantity > intent.quantity: raise ValueError('unfilled fills exceed intent')
    if len(observation.open_orders)>400 or len(observation.broker_open_orders or ())>400:
        raise ValueError('unfilled account order budget exceeded')
    protective = not buy and (policy.purpose == 'PROTECTIVE_EXIT' or assessment.protection_required)
    protect_position = protective or assessment.protection_required
    body = {'policy_version':UNFILLED_MANAGEMENT_SHADOW_VERSION,'decision_time':cut.isoformat(),
        'purpose': 'PROTECTIVE_EXIT' if protective else policy.purpose,
        'intent_ref':intent.intent_id,'broker_order_ref':record.broker_order_id,
        'action':'WAIT','status':'BLOCKED','after_cancel':None,'replacement_quantity':None,
        'replacement_limit_price_won':None,'target_decision_ref':None,'ev_status':'EV_UNAVAILABLE',
        'penalized_net_advantage_won':None,'risk_path_required':protect_position,
        'liquidation_confirmed':False,'requires_operator_attention':False,'reasons':[],
        'next_check_at':min(_time(policy.wait_until),_time(policy.protection_deadline) if protect_position else _time(policy.valid_until)).isoformat(),
        'scope':{'environment':'mock','venue':'KRX','execution_support':'shadow_only','source_durability':'not_verified'},
        'risk_authority':'supplied_research_constraint_only','order_authorized':False,
        'policy':asdict(policy),'inputs':{'record':asdict(record),'broker_snapshot':asdict(broker_snapshot) if broker_snapshot else None,
            'account_input_ref':observation.input_ref,'account':asdict(observation.account),
            'open_orders':[asdict(r) for r in observation.open_orders],
            'broker_open_orders':[asdict(r) for r in observation.broker_open_orders] if observation.broker_open_orders is not None else None,
            'account_quality':{'observed_at':observation.observed_at,'reconciliation_complete':observation.reconciliation_complete,
                'open_orders_complete':observation.open_orders_complete,'reasons':observation.reasons},
            'target':asdict(target),'assessment':asdict(assessment)}}
    if root_record is not None:
        body['inputs']['root_record'] = asdict(root_record)
    def finish(action,reason,status='PROPOSED',after=None,**extra):
        body.update(action=action,status=status,after_cancel=after,**extra)
        body['reasons'].append(reason)
        digest=hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':'),default=_json_value).encode()).hexdigest()
        return {'decision_id':'unfilled-order-management-shadow:'+digest,**json.loads(json.dumps(body,default=_json_value))}
    def blocked(reason):
        return finish('WAIT',reason,'BLOCKED',requires_operator_attention=protect_position)
    def fresh(at):
        return at is not None and 0 <= (cut-_time(at)).total_seconds() <= policy.max_snapshot_age_seconds
    def available(ref,at,end):
        return bool(ref) and fresh(at) and end is not None and cut < _time(end)
    if cut < _time(policy.anchor_available_at): return blocked('policy_anchor_from_future')
    if not fresh(record.updated_at) or not fresh(observation.observed_at): return blocked('order_record_or_observation_stale_or_future')
    if record.state in {OrderState.QUEUED,OrderState.SUBMISSION_UNKNOWN}:
        return blocked('unconfirmed_submission_requires_existing_reconciliation')
    if record.state is OrderState.CANCEL_PENDING:
        return blocked('cancel_ack_is_not_cancellation_confirmation')
    if broker_snapshot is None: return blocked('broker_order_confirmation_missing')
    if (broker_snapshot.account_ref,broker_snapshot.symbol,broker_snapshot.broker_order_id) != (
            intent.account_ref,intent.symbol,record.broker_order_id): raise ValueError('unfilled broker identity mismatch')
    if not fresh(broker_snapshot.as_of) or not fresh(observation.account.as_of):
        return blocked('broker_or_account_cut_stale_or_future')
    if broker_snapshot.state not in {OrderState.ACCEPTED,OrderState.PARTIALLY_FILLED,OrderState.CANCEL_PENDING,
            OrderState.CANCELLED,OrderState.FILLED,OrderState.REJECTED}:
        return blocked('broker_order_state_unconfirmed')
    reconciled_state=OrderLifecycle._snapshot_state(broker_snapshot,record.filled_quantity,intent.quantity)
    if (record.state != reconciled_state or record.filled_quantity != broker_snapshot.filled_quantity
            or record.last_broker_as_of != broker_snapshot.as_of or observation.account.as_of < broker_snapshot.as_of):
        return blocked('broker_fill_or_cancel_not_yet_reconciled')
    remaining = broker_snapshot.remaining_quantity
    _integer(remaining,'broker_remaining_quantity')
    _integer(broker_snapshot.filled_quantity,'broker_filled_quantity')
    if broker_snapshot.filled_quantity+remaining > intent.quantity: raise ValueError('broker remaining exceeds original intent')
    if record.state in TERMINAL_ORDER_STATES and remaining:
        return blocked('terminal_broker_order_has_remaining_exposure')
    held = observation.account.positions.get(intent.symbol,0)
    _integer(held,'actual_held_quantity')
    if record.state is OrderState.FILLED:
        confirmed=(protective and held == 0 and observation.reconciliation_complete and observation.open_orders_complete
                and not any(r.intent.symbol == intent.symbol and r.state not in TERMINAL_ORDER_STATES for r in observation.open_orders)
                and not any(r.symbol == intent.symbol for r in observation.broker_open_orders or ()))
        return finish('WAIT','original_order_filled_reassess_position','COMPLETED',
            liquidation_confirmed=confirmed,risk_path_required=protect_position and not confirmed)
    if record.state is OrderState.REJECTED: return blocked('rejected_order_requires_new_position_decision')
    active = record.state in {OrderState.ACCEPTED,OrderState.PARTIALLY_FILLED}
    if active and not remaining: return blocked('nonterminal_order_without_confirmed_remaining')
    if protective and target.action != 'EXIT': return blocked('protective_exit_requires_current_exit_target')
    risk_ok = available(assessment.risk_input_ref,assessment.risk_available_at,assessment.risk_valid_until)
    thesis_ok = available(assessment.thesis_input_ref,assessment.thesis_available_at,assessment.thesis_valid_until)
    price_ok = bool(assessment.price_input_ref) and assessment.candidate_limit_price_won is not None and fresh(assessment.price_available_at)
    policy_ok = cut < _time(policy.valid_until)
    if buy and (not policy_ok or cut >= _time(policy.wait_until) or cut >= _time(intent.expires_at)
            or not risk_ok or assessment.risk_allows_new_risk is not True
            or not thesis_ok or assessment.thesis_valid is not True or assessment.protection_required):
        return finish('CANCEL' if active else 'ABANDON','buy_opportunity_or_risk_no_longer_admitted',after='ABANDON' if active else None)
    if not policy_ok: return blocked('management_policy_expired')
    if not buy and (not risk_ok or (not protective and not thesis_ok)):
        return blocked('current_sell_risk_or_thesis_evidence_unavailable')
    if assessment.market_status != 'TRADABLE' or not price_ok:
        return blocked('replacement_market_liquidity_or_price_unavailable')
    session=mock_order_entry_decision(cut,environment=intent.environment,venue=intent.venue,order_type='LIMIT')
    if not session.allowed or session.reason == 'mock_after_broker_probe':
        if buy: return finish('CANCEL' if active else 'ABANDON','replacement_session_not_verified',after='ABANDON' if active else None)
        return blocked('replacement_session_not_verified')
    price=assessment.candidate_limit_price_won
    adverse = max(0,price-policy.anchor_price_won) if buy else max(0,policy.anchor_price_won-price)
    limit = policy.protective_slippage_bps if protective else policy.maximum_adverse_slippage_bps
    if adverse*10_000 > policy.anchor_price_won*limit:
        if buy: return finish('CANCEL' if active else 'ABANDON','anchored_slippage_limit_exceeded',after='ABANDON' if active else None)
        return blocked('anchored_sell_slippage_limit_exceeded')
    sized = None
    comparison_quantity = remaining
    if not active:
        if record.state is not OrderState.CANCELLED: return blocked('replacement_requires_confirmed_cancelled_order')
        if record.filled_quantity != record.detailed_filled_quantity or record.broker_reported_filled_quantity > record.detailed_filled_quantity:
            return blocked('cancel_race_fills_not_fully_reconciled')
        request=replace(target,reference_price_won=price,reference_price_available_at=assessment.price_available_at,
            reconciliation_complete=target.reconciliation_complete and observation.reconciliation_complete,
            open_orders_complete=target.open_orders_complete and observation.open_orders_complete)
        sized=evaluate_position_target_shadow(request,observation.account,observation.open_orders,
            broker_open_orders=observation.broker_open_orders)
        if sized['quality']['status'] != 'CALCULATED' or sized['funding_shortfall_quantity']:
            body['reasons'].extend(sized['quality']['reasons'])
            return blocked('replacement_target_not_fully_verified_or_funded')
        if not sized['incremental_quantity']:
            confirmed=protective and held == 0 and sized['pending_buy_quantity']==0 and sized['pending_sell_quantity']==0
            return finish('WAIT','target_already_covered_by_current_position_or_orders','COMPLETED',
                liquidation_confirmed=confirmed,risk_path_required=protect_position and not confirmed)
        comparison_quantity = sized['incremental_quantity']
        if buy and comparison_quantity > intent.quantity-record.filled_quantity:
            return blocked('replacement_buy_exceeds_original_unfilled_budget')
    evidence=assessment.reprice_evidence
    improvement=False
    from kiwoom_monitor.application.research_evaluation import position_action_cost_model_ref
    cost_ref=position_action_cost_model_ref(target.cost_model.to_dict() if target.cost_model else None)
    model=target.cost_model
    cost_valid=model is not None and (not model.valid_from or
        _time(datetime.fromisoformat(model.valid_from)) <= cut < _time(datetime.fromisoformat(model.valid_to)))
    if evidence is not None and fresh(evidence.available_at) and (
            evidence.intent_id,evidence.pending_quantity,evidence.candidate_limit_price_won) == (
            intent.intent_id,comparison_quantity,price) and evidence.paired_sample_count >= policy.minimum_paired_samples and (
            evidence.horizon_seconds == policy.reprice_horizon_seconds and
            evidence.cost_model_ref == cost_ref and cost_valid and
            evidence.horizon_seconds <= (_time(policy.wait_until)-cut).total_seconds()):
        score=evidence.net_advantage_won-policy.uncertainty_multiplier*evidence.net_standard_error_won
        if math.isfinite(score):
            body.update(ev_status='SUPPLIED_PAIRED_UNFILLED_ESTIMATE',penalized_net_advantage_won=score)
            improvement=score > policy.minimum_net_advantage_won
    if not protective and not improvement:
        if active and cut < _time(policy.wait_until) and (buy or assessment.trend_allows_wait is True):
            return finish('WAIT','bounded_existing_order_wait_without_supported_reprice')
        if buy: return finish('ABANDON','no_supported_reprice_after_confirmed_cancel')
        return blocked('profit_reprice_evidence_unavailable_or_insufficient')
    if assessment.reprices_used >= policy.maximum_reprices:
        if buy: return finish('CANCEL' if active else 'ABANDON','reprice_budget_exhausted',after='ABANDON' if active else None)
        return blocked('protective_or_profit_reprice_budget_exhausted')
    if active:
        if price == intent.limit_price: return finish('WAIT','existing_order_already_at_candidate_price',
            requires_operator_attention=protective and cut >= _time(policy.protection_deadline))
        return finish('CANCEL','protection_precedes_ev' if protective else 'supported_reprice_requires_confirmed_cancel',after='REPRICE',
            requires_operator_attention=protective and cut >= _time(policy.protection_deadline))
    expected_side = 'BUY' if buy else 'SELL'
    if sized['incremental_side'] != expected_side: return blocked('replacement_target_side_changed')
    return finish('REPRICE','protection_precedes_ev' if protective else 'verified_remaining_target_after_cancel',
        replacement_quantity=sized['incremental_quantity'],replacement_limit_price_won=price,target_decision_ref=sized['decision_id'],
        requires_operator_attention=protective and cut >= _time(policy.protection_deadline))


def evaluate_unfilled_order_management_document(document):
    """Strict fixed-input preview adapter; no lifecycle, repository or transport creation."""
    from kiwoom_monitor.application.position_target import (
        PositionAccountObservation,_position_account_orders_from_dict,_position_target_request_from_dict,
    )
    from kiwoom_monitor.domain.order_contract import BrokerFill
    if set(document) != {'schema','policy','record','broker_snapshot','target','account','open_orders','assessment'} or (
            document['schema'] != UNFILLED_MANAGEMENT_SHADOW_VERSION):
        raise ValueError('unsupported unfilled management document')
    raw_policy=dict(document['policy'])
    for name in ('anchor_available_at','valid_until','wait_until','protection_deadline'):
        raw_policy[name]=datetime.fromisoformat(raw_policy[name])
    policy=UnfilledOrderManagementPolicy(**raw_policy)
    if not isinstance(document['open_orders'],list) or len(document['open_orders'])>399:
        raise ValueError('fixed unfilled document order budget exceeded')
    account,records=_position_account_orders_from_dict(document['account'],[*document['open_orders'],document['record']])
    target=_position_target_request_from_dict(document['target'])
    observation=PositionAccountObservation(account,records[:-1],target.account_input_ref,account.as_of,
        target.reconciliation_complete,target.open_orders_complete,())
    raw=dict(document['assessment'])
    for name in ('thesis_available_at','thesis_valid_until','risk_available_at','risk_valid_until','price_available_at'):
        if raw[name] is not None: raw[name]=datetime.fromisoformat(raw[name])
    if raw.get('reprice_evidence') is not None:
        evidence=dict(raw['reprice_evidence']);evidence['available_at']=datetime.fromisoformat(evidence['available_at'])
        raw['reprice_evidence']=UnfilledRepriceEvidence(**evidence)
    assessment=UnfilledOrderAssessment(**raw)
    snapshot=None
    if document['broker_snapshot'] is not None:
        raw=dict(document['broker_snapshot']);raw['state']=OrderState(raw['state'])
        raw['as_of']=datetime.fromisoformat(raw['as_of'])
        if len(raw.get('fills',()))>400: raise ValueError('unfilled broker fill budget exceeded')
        raw['fills']=tuple(BrokerFill(**{**fill,'occurred_at':datetime.fromisoformat(fill['occurred_at'])}) for fill in raw.get('fills',()))
        snapshot=BrokerOrderSnapshot(**raw)
    return evaluate_unfilled_order_management_shadow(policy,records[-1],snapshot,target,observation,assessment)


class MockOrderTransport(Protocol):
    def submit(self, intent: OrderIntent) -> BrokerSubmission: ...
    def cancel(self, intent: OrderIntent, broker_order_id: str, quantity: int = 0) -> BrokerSubmission: ...


class OrderLifecycle:
    def __init__(
        self, repository: ExecutionRepository, transport: MockOrderTransport | None,
        *, now_provider: Callable[[], datetime] = lambda: kst_now(),
    ) -> None:
        self._repository = repository
        self._transport = transport
        self._now = now_provider

    def queue(self, intent: OrderIntent) -> ExecutionRecord:
        record = self._repository.create(intent)
        if self._repository.events(intent.intent_id):
            return record
        event = order_event(
            intent.intent_id, "QUEUED", OrderState.QUEUED,
            intent.created_at, self._now(),
        )
        return self._repository.apply(record, event)

    def submit(
        self, intent_id: str, account: AccountSnapshot, *, reference_price: int | None = None,
    ) -> ExecutionRecord:
        record = self._required(intent_id)
        if record.state is not OrderState.QUEUED:
            return record
        now = self._now()
        rejection = self._preflight_error(record.intent, account, now, reference_price)
        if rejection:
            return self._apply(record, "PRECHECK_REJECTED", OrderState.REJECTED, reason=rejection)
        transport = self._required_transport()

        # Persist unknown before crossing the network boundary. A crash can then cause a
        # missed submission, but never an automatic duplicate order.
        record = self._apply(record, "SUBMISSION_STARTED", OrderState.SUBMISSION_UNKNOWN)
        try:
            submission = transport.submit(record.intent)
        except SubmissionUnknown as error:
            return self._apply(record, "SUBMISSION_RESPONSE_UNKNOWN", OrderState.SUBMISSION_UNKNOWN, reason=str(error))
        except SubmissionRejected as error:
            return self._apply(record, "SUBMISSION_REJECTED", OrderState.REJECTED, reason=str(error))
        return self._apply(
            record, "ORDER_ACCEPTED", OrderState.ACCEPTED,
            broker_order_id=submission.broker_order_id, occurred_at=submission.accepted_at,
        )

    def cancel(
        self, intent_id: str, quantity: int = 0, *, after_cancel_claim: Callable[[], None] | None = None,
    ) -> ExecutionRecord:
        record = self._required(intent_id)
        if record.state in TERMINAL_ORDER_STATES:
            return record
        if record.state not in {OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED} or not record.broker_order_id:
            raise ValueError("order must be reconciled and accepted before cancellation")
        transport = self._required_transport()
        record = self._apply(record, "CANCEL_STARTED", OrderState.CANCEL_PENDING)
        if after_cancel_claim is not None:
            after_cancel_claim()  # Durable claim precedes transport; later STOP must not block reconciliation.
        try:
            transport.cancel(record.intent, record.broker_order_id, quantity)
        except SubmissionUnknown as error:
            return self._apply(record, "CANCEL_RESPONSE_UNKNOWN", OrderState.CANCEL_PENDING, reason=str(error))
        except SubmissionRejected as error:
            fallback = OrderState.PARTIALLY_FILLED if record.filled_quantity else OrderState.ACCEPTED
            return self._apply(record, "CANCEL_REJECTED", fallback, reason=str(error))
        return self._apply(record, "CANCEL_ACCEPTED", OrderState.CANCEL_PENDING)

    def reconcile(self, intent_id: str, snapshot: BrokerOrderSnapshot) -> ExecutionRecord:
        record = self._required(intent_id)
        intent = record.intent
        if snapshot.account_ref != intent.account_ref or snapshot.symbol != intent.symbol:
            raise ValueError("broker snapshot does not match the order account and symbol")
        if record.broker_order_id and snapshot.broker_order_id != record.broker_order_id:
            raise ValueError("broker snapshot order id does not match")
        if record.last_broker_as_of is not None and snapshot.as_of <= record.last_broker_as_of:
            return record

        broker_order_id = record.broker_order_id or snapshot.broker_order_id
        fill_ids = list(record.fill_ids)
        detailed_filled_quantity = record.detailed_filled_quantity
        broker_reported_filled_quantity = record.broker_reported_filled_quantity
        filled_quantity = max(detailed_filled_quantity, broker_reported_filled_quantity)
        for fill in sorted(snapshot.fills, key=lambda value: (value.occurred_at, value.execution_id)):
            if fill.execution_id in fill_ids:
                continue
            if detailed_filled_quantity + fill.quantity > intent.quantity:
                raise ValueError("broker fills exceed the requested quantity")
            detailed_filled_quantity += fill.quantity
            filled_quantity = max(detailed_filled_quantity, broker_reported_filled_quantity)
            fill_ids.append(fill.execution_id)
            state = OrderState.FILLED if filled_quantity == intent.quantity else OrderState.PARTIALLY_FILLED
            record = self._apply(
                record, "FILL", state, broker_order_id=broker_order_id,
                broker_execution_id=fill.execution_id, quantity=fill.quantity, price=fill.price,
                occurred_at=fill.occurred_at, broker_as_of=snapshot.as_of,
                filled_quantity=filled_quantity, fill_ids=tuple(fill_ids),
                detailed_filled_quantity=detailed_filled_quantity,
                broker_reported_filled_quantity=broker_reported_filled_quantity,
                last_broker_as_of=snapshot.as_of,
            )

        if snapshot.filled_quantity > intent.quantity:
            raise ValueError("broker cumulative fill exceeds the requested quantity")
        if snapshot.filled_quantity > broker_reported_filled_quantity:
            before = filled_quantity
            broker_reported_filled_quantity = snapshot.filled_quantity
            filled_quantity = max(detailed_filled_quantity, broker_reported_filled_quantity)
            aggregate_quantity = filled_quantity - before
            if aggregate_quantity > 0:
                record = self._apply(
                    record, "BROKER_FILL_AGGREGATE",
                    OrderState.FILLED if filled_quantity == intent.quantity else OrderState.PARTIALLY_FILLED,
                    broker_order_id=broker_order_id, quantity=aggregate_quantity,
                    reason="broker cumulative fill; exact execution identity unavailable",
                    occurred_at=snapshot.as_of, broker_as_of=snapshot.as_of,
                    filled_quantity=filled_quantity, fill_ids=tuple(fill_ids),
                    detailed_filled_quantity=detailed_filled_quantity,
                    broker_reported_filled_quantity=broker_reported_filled_quantity,
                    last_broker_as_of=snapshot.as_of,
                )

        final_state = self._snapshot_state(snapshot, filled_quantity, intent.quantity)
        if (
            record.state is not final_state
            or record.last_broker_as_of != snapshot.as_of
            or record.broker_reported_filled_quantity != broker_reported_filled_quantity
        ):
            record = self._apply(
                record, "BROKER_RECONCILED", final_state,
                broker_order_id=broker_order_id, broker_as_of=snapshot.as_of,
                filled_quantity=filled_quantity, fill_ids=tuple(fill_ids),
                detailed_filled_quantity=detailed_filled_quantity,
                broker_reported_filled_quantity=broker_reported_filled_quantity,
                last_broker_as_of=snapshot.as_of,
            )
        return record

    @staticmethod
    def _snapshot_state(snapshot: BrokerOrderSnapshot, filled: int, requested: int) -> OrderState:
        if filled >= requested:
            return OrderState.FILLED
        if snapshot.state is OrderState.CANCELLED:
            return OrderState.CANCELLED
        if snapshot.state is OrderState.REJECTED:
            return OrderState.REJECTED
        if snapshot.state is OrderState.CANCEL_PENDING:
            return OrderState.CANCEL_PENDING
        return OrderState.PARTIALLY_FILLED if filled else OrderState.ACCEPTED

    @staticmethod
    def _preflight_error(
        intent: OrderIntent, account: AccountSnapshot, now: datetime, reference_price: int | None,
    ) -> str:
        session_decision = mock_order_entry_decision(
            now,
            environment=intent.environment,
            venue=intent.venue,
            order_type=intent.order_type.value,
        )
        if not session_decision.allowed:
            return session_decision.evidence
        if account.account_ref != intent.account_ref:
            return "account mismatch"
        if account.as_of > now:
            return "account snapshot is from the future"
        if now >= intent.expires_at:
            return "intent expired before transmission"
        if intent.side is OrderSide.SELL:
            if int(account.positions.get(intent.symbol, 0)) < intent.quantity:
                return "insufficient position"
            return ""
        price = intent.limit_price or reference_price
        if price is None or price <= 0:
            return "market order requires a positive reference price for cash reservation"
        required = price * intent.quantity
        if required > account.available_cash_won - account.reserved_open_buy_won:
            return "insufficient available cash after open-order reservation"
        return ""

    def _required(self, intent_id: str) -> ExecutionRecord:
        record = self._repository.load(intent_id)
        if record is None:
            raise KeyError(f"unknown order intent: {intent_id}")
        return record

    def _required_transport(self) -> MockOrderTransport:
        if self._transport is None:
            raise RuntimeError("order transport is not configured")
        return self._transport

    def _apply(self, record: ExecutionRecord, event_type: str, state: OrderState, **values: object) -> ExecutionRecord:
        received_at = self._now()
        occurred_at = values.pop("occurred_at", received_at)
        event_fields = {
            key: values[key] for key in (
                "broker_order_id", "broker_execution_id", "quantity", "price", "reason", "broker_as_of",
            ) if key in values
        }
        event = order_event(
            record.intent.intent_id, event_type, state, occurred_at, received_at, **event_fields,
        )
        return self._repository.apply(record, event, **values)
