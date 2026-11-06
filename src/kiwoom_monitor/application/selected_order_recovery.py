"""Compare preserved facts with owner-verified account and dated broker evidence.

This module creates no account, intent, approval, lease or execution cursor.
The source proof is issued only by the existing native identity owner. Broker
evidence comes from an internal provider, never request-body trust flags.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
import hashlib
import hmac
import json

from kiwoom_monitor.domain.order_contract import BrokerFill, OrderState
from kiwoom_monitor.domain.selected_order_evidence import encode_selected_order_evidence


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _mac(key, purpose, value):
    if type(key) is not bytes or len(key) < 32:
        raise ValueError('selected recovery requires the existing protected identity namespace')
    return hmac.new(key, purpose.encode() + b'\0' + _canonical(value), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class DatedBrokerOrderEvidence:
    broker_order_id: str
    trading_date: date | None
    original_broker_order_id: str | None
    symbol: str
    venue: str | None
    side: str | None
    ordered_quantity: int | None
    limit_price: int | None
    filled_quantity: int
    remaining_quantity: int
    state: str
    fills: tuple[BrokerFill, ...]

    def __post_init__(self):
        if (not self.broker_order_id or type(self.fills) is not tuple
                or any(not isinstance(fill, BrokerFill) for fill in self.fills)
                or self.trading_date is not None and type(self.trading_date) is not date
                or any(value is not None and (type(value) is not int or value <= 0)
                       for value in (self.ordered_quantity, self.limit_price))
                or any(type(value) is not int or value < 0 for value in (self.filled_quantity, self.remaining_quantity))):
            raise ValueError('invalid dated broker order facts')

    def to_dict(self):
        return {**asdict(self), 'trading_date': self.trading_date.isoformat() if self.trading_date else None,
            'fills': [{**asdict(fill), 'occurred_at': fill.occurred_at.isoformat()} for fill in self.fills]}


@dataclass(frozen=True)
class DatedBrokerEvidence:
    account_ref: str
    credential_profile_id: str
    credential_revision: int
    binding_revision: int
    query_started_at: datetime
    query_completed_at: datetime
    covered_dates: tuple[date, ...]
    coverage: str
    source_ref: str
    orders: tuple[DatedBrokerOrderEvidence, ...]

    def __post_init__(self):
        if (self.coverage not in {'FULL', 'PARTIAL', 'UNAVAILABLE'} or type(self.source_ref) is not str or not self.source_ref
                or type(self.orders) is not tuple or type(self.covered_dates) is not tuple
                or any(not isinstance(order, DatedBrokerOrderEvidence) for order in self.orders)
                or any(type(day) is not date for day in self.covered_dates)
                or any(not isinstance(value, datetime) or value.utcoffset() is None
                       for value in (self.query_started_at, self.query_completed_at))
                or self.query_started_at > self.query_completed_at
                or type(self.credential_revision) is not int or self.credential_revision < 1
                or type(self.binding_revision) is not int or self.binding_revision < 1):
            raise ValueError('invalid dated broker coverage')

    def to_dict(self):
        return {**asdict(self), 'query_started_at': self.query_started_at.isoformat(),
            'query_completed_at': self.query_completed_at.isoformat(),
            'covered_dates': [day.isoformat() for day in self.covered_dates],
            'orders': [order.to_dict() for order in self.orders]}


def broker_evidence_from_dict(value):
    if value is None:
        return None
    raw = dict(value)
    raw['query_started_at'] = datetime.fromisoformat(raw['query_started_at'])
    raw['query_completed_at'] = datetime.fromisoformat(raw['query_completed_at'])
    raw['covered_dates'] = tuple(date.fromisoformat(day) for day in raw['covered_dates'])
    orders = []
    for item in raw['orders']:
        order = dict(item)
        order['trading_date'] = date.fromisoformat(order['trading_date']) if order['trading_date'] else None
        order['fills'] = tuple(BrokerFill(**{**fill, 'occurred_at': datetime.fromisoformat(fill['occurred_at'])}) for fill in order['fills'])
        orders.append(DatedBrokerOrderEvidence(**order))
    raw['orders'] = tuple(orders)
    return DatedBrokerEvidence(**raw)


def validate_source_identity_proof(value):
    """Keep signed bytes' values intact while rejecting secrets and trust flags."""
    if value is None:
        return value
    fields = {'version', 'packet_sha256', 'namespace_id', 'source_account_ref', 'binding_id', 'binding_revision',
        'verified_at', 'credential_profile_id', 'broker', 'environment', 'account_token', 'broker_evidence', 'signature'}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError('invalid selected source identity proof fields')
    if (value['version'] != 'selected-order-source-identity/v1' or value['broker'] != 'kiwoom'
            or value['environment'] != 'mock' or type(value['binding_revision']) is not int or value['binding_revision'] < 1
            or type(value['source_account_ref']) is not str or len(value['source_account_ref']) != 36):
        raise ValueError('invalid selected source native identity reference')
    for name in ('packet_sha256', 'namespace_id', 'account_token', 'signature'):
        text = value[name]
        if type(text) is not str or len(text) != 64 or any(character not in '0123456789abcdef' for character in text):
            raise ValueError('invalid selected source proof digest')
    try:
        broker_evidence_from_dict(value['broker_evidence'])
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise ValueError('invalid signed dated broker fields') from error
    return value


def issue_source_identity_proof(packet, digest, *, binding, identity_fingerprint, identity_key, broker_evidence=None):
    _, actual = encode_selected_order_evidence(packet)
    if (actual != digest or binding['account_ref'] != packet['account_ref']
            or binding['broker'] != 'kiwoom' or binding['environment'] != 'mock'
            or binding['verification_method'] != 'ka00001'):
        raise ValueError('source identity proof does not match the native packet binding')
    if broker_evidence is not None and (
            not isinstance(broker_evidence, DatedBrokerEvidence)
            or (broker_evidence.account_ref, broker_evidence.credential_profile_id, broker_evidence.binding_revision)
            != (binding['account_ref'], binding['credential_profile_id'], binding['binding_revision'])):
        raise ValueError('source broker evidence crossed its native verified binding')
    body = dict(version='selected-order-source-identity/v1', packet_sha256=digest,
        namespace_id=_mac(identity_key, 'selected-order-namespace/v1', {}),
        source_account_ref=packet['account_ref'], binding_id=binding['binding_id'],
        binding_revision=binding['binding_revision'], verified_at=binding['verified_at'],
        credential_profile_id=binding['credential_profile_id'], broker='kiwoom', environment='mock',
        account_token=_mac(identity_key, 'selected-order-account/v1', [digest, identity_fingerprint]),
        broker_evidence=broker_evidence.to_dict() if broker_evidence is not None else None)
    return {**body, 'signature': _mac(identity_key, 'selected-order-source-proof/v1', body)}


@dataclass(frozen=True)
class SelectedOrderIdentityMatch:
    status: str
    reasons: tuple[str, ...]
    packet_sha256: str
    target_account_ref: str
    credential_profile_id: str
    credential_revision: int
    binding_revision: int
    verified_at: datetime


def verify_source_identity_proof(packet, digest, proof, *, identity_key, target_identity, target_binding, credential_revision):
    reasons, status = [], 'MATCH'
    _, actual = encode_selected_order_evidence(packet)
    if actual != digest:
        raise ValueError('selected packet digest changed')
    if not isinstance(proof, dict):
        reasons, status = ['SOURCE_IDENTITY_PROOF_MISSING'], 'UNKNOWN'
    elif proof.get('namespace_id') != _mac(identity_key, 'selected-order-namespace/v1', {}):
        reasons, status = ['PROTECTED_NAMESPACE_INCOMPARABLE'], 'UNKNOWN'
    else:
        body = {key: value for key, value in proof.items() if key != 'signature'}
        expected = _mac(identity_key, 'selected-order-source-proof/v1', body)
        if (not hmac.compare_digest(str(proof.get('signature', '')), expected)
                or proof.get('version') != 'selected-order-source-identity/v1'
                or proof.get('packet_sha256') != digest or proof.get('source_account_ref') != packet['account_ref']
                or (proof.get('broker'), proof.get('environment')) != ('kiwoom', 'mock')):
            reasons, status = ['SOURCE_IDENTITY_PROOF_CONFLICT'], 'CONFLICT'
        elif not hmac.compare_digest(str(proof.get('account_token', '')),
                _mac(identity_key, 'selected-order-account/v1', [digest, target_identity.identity_fingerprint])):
            reasons, status = ['VERIFIED_BROKER_ACCOUNT_DIFFERS'], 'CONFLICT'
    return SelectedOrderIdentityMatch(status, tuple(reasons), digest, target_binding['account_ref'],
        target_binding['credential_profile_id'], credential_revision, target_binding['binding_revision'], target_identity.verified_at)


def management_checkpoint_matches(packet, value):
    return isinstance(value, dict) and (value.get('account_ref'), value.get('spec_id'), value.get('execution_run_id')) == (
        packet['account_ref'], packet['source_spec_id'], packet['run_id'])


def management_checkpoint_references(packet, documents):
    """Only the selected runner's actual historical recovery dependencies."""
    from kiwoom_monitor.domain.order_contract import OrderPlanRef
    account, member = packet['account_ref'], packet['source_spec_id']
    key = 'member:' + member if 'member_spec_id' in packet else 'current'
    refs = set()
    def risk(identity):
        if identity: refs.add(('execution_mock_automation_risk_snapshots', account, identity))
    def notice_references(notice):
        if not isinstance(notice, dict) or notice.get('state') != 'PENDING': return False
        risk((notice.get('risk_trigger') or {}).get('snapshot_id'))
        risk((notice.get('planned_leader_stop') or {}).get('risk_snapshot_id'))
        risk(((notice.get('planned_leader_protection_exit') or {}).get('snapshot') or {}).get('risk_snapshot_id'))
        if notice.get('stop_revision_id'):
            refs.add(('execution_mock_automation_stop_revisions', account, notice['stop_revision_id']))
        return True
    runner = documents.get(('execution_mock_automation_runner_current', account, key))
    if runner is not None:
        if not management_checkpoint_matches(packet, runner):
            raise ValueError('selected management runner checkpoint crossed its original spec/run')
        if notice_references(runner.get('protection_review')):
            risk(((runner.get('protection_exit') or {}).get('snapshot') or {}).get('risk_snapshot_id'))
            if packet['order']['policy_version'] in {'close_execution_adapter/v1', 'panic_execution_adapter/v1'}:
                panic = packet['order']['policy_version'].startswith('panic_')
                if panic:
                    from .panic_mock_execution import panic_exit_key as exit_key
                else:
                    from .close_mock_execution import close_exit_key as exit_key
                head = packet['management_closure']['source_plan_position']
                plan = OrderPlanRef(head['plan_id'], head['policy_ref'], head['revision'])
                refs.add(('execution_panic_exit_evidence' if panic else 'execution_close_exit_evidence', account, exit_key(plan)))
    clock = documents.get(('execution_mock_automation_clock_current', account, key))
    if clock is not None:
        if not management_checkpoint_matches(packet, clock):
            raise ValueError('selected management clock checkpoint crossed its original spec/run')
        if clock.get('stop_revision_id'):
            refs.add(('execution_mock_automation_stop_revisions', account, clock['stop_revision_id']))
    # A frozen CLOSE exit can pin the first earlier protection Risk independently
    # of the runner's subsequently updated review. Never substitute current Risk.
    for identity in tuple(refs):
        value = documents.get(identity)
        if value is not None and identity[0] in {'execution_close_exit_evidence', 'execution_panic_exit_evidence'}:
            snapshot = value.get('snapshot') or {}
            notice_references(snapshot.get('protection_notice'))
            risk(snapshot.get('risk_snapshot_id'))
    return refs


def validate_management_closure(packet, *, allow_missing_checkpoint_dependencies=False):
    """Validate retained policies as past facts, without activating their old authority."""
    from kiwoom_monitor.domain.selected_order_evidence import MANAGEMENT_VERSION, PLAN_MANAGEMENT_VERSION, selected_plan_orders
    from kiwoom_monitor.domain.execution_activation import mock_automation_spec_from_dict
    from kiwoom_monitor.application.mock_automation_candidate import candidate_package_from_dict
    from kiwoom_monitor.application.mock_automation_admission import mock_automation_admission_from_dict
    from kiwoom_monitor.application.mock_automation_execution import MockAutomationGateStatus, mock_automation_decision_gate_from_dict, mock_automation_intent_id

    plan_ledger = packet['version'] == PLAN_MANAGEMENT_VERSION
    if packet['version'] not in {MANAGEMENT_VERSION, PLAN_MANAGEMENT_VERSION}:
        raise ValueError('selected management bootstrap requires the original policy closure')
    if plan_ledger: encode_selected_order_evidence(packet)
    closure = packet['management_closure']
    documents = {(item['collection'], item['owner'], item['key']): item['document'] for item in closure['documents']}
    required = set()
    def original(collection, owner, key):
        identity = collection, owner, key
        required.add(identity)
        if identity not in documents: raise ValueError('selected management policy closure is incomplete: ' + collection)
        return documents[identity]
    account, order, member = packet['account_ref'], packet['order'], packet['source_spec_id']
    spec = mock_automation_spec_from_dict(original('execution_mock_automation_specs', account, member))
    package = candidate_package_from_dict(original('execution_mock_automation_candidate_packages', spec.strategy_ref, spec.candidate_package_hash))
    admission = mock_automation_admission_from_dict(original('execution_mock_automation_admission_by_spec', account, member))
    gate = mock_automation_decision_gate_from_dict(original('execution_mock_automation_approved_gates', account, order['intent_id']))
    if ((spec.account_scope.account_ref, spec.spec_id, package.package_hash, admission.spec_id, admission.execution_run_id,
            gate.admission_id, gate.spec_id, gate.intent_id, gate.gate_id, gate.strategy_decision_id)
            != (account, member, spec.candidate_package_hash, member, packet['run_id'], admission.admission_id, member,
                order['intent_id'], closure['origin_approval_id'], order['decision_id'])
            or gate.plan is None or asdict(gate.plan) != order['plan'] or gate.status is not MockAutomationGateStatus.APPROVED_FOR_SINGLE_SUBMISSION
            or packet.get('member_admission_id', admission.admission_id) != admission.admission_id):
        raise ValueError('selected management original policy/admission references changed')
    head = closure['source_plan_position']
    fills = [event for event in packet['events'] if event['event_type'] == 'FILL']
    if not plan_ledger and (head.get('unreconciled_sell_quantity', 0) or head['quantity'] != sum(event['quantity'] for event in fills)
            or head['fill_cursor'] != max((event['accepted_sequence'] for event in fills), default=0)
            or 'capital_lots' in head and head['capital_lots'] != [[event['quantity'], event['price']] for event in fills]):
        raise ValueError('selected management closure requires the complete single-original-intent position')
    if order['policy_version'] in {'close_execution_adapter/v1', 'panic_execution_adapter/v1'}:
        panic = order['policy_version'].startswith('panic_')
        collection = 'execution_panic_entry_evidence' if panic else 'execution_close_entry_evidence'
        entry = original(collection, account, order['plan']['plan_id'])
        event = closure['shadow_candidate']
        if not isinstance(event, dict) or entry['snapshot']['candidate_event_id'] != event['event_id']:
            raise ValueError('selected management original Shadow candidate is missing')
        if panic:
            from .panic_mock_execution import build_panic_entry_evidence as build
            options = {}
        else:
            from .close_mock_execution import build_close_entry_evidence as build
            options = dict(retain_candidate=entry['snapshot']['version'] != 'close_entry_snapshot/v1')
        at = datetime.fromisoformat(entry['decision']['decided_at'])
        proof, decision, plan = build(spec, run_id=packet['run_id'], parameters=package.candidate_spec['parameters'],
            event=event, quote=entry['snapshot']['quote'], decided_at=at, now=at, **options)
        if (proof != entry or asdict(plan) != order['plan'] or mock_automation_intent_id(spec.spec_id, decision.decision_id) != order['intent_id']
                or (decision.quantity, decision.signal_reference_price) != (order['quantity'], order['limit_price'])):
            raise ValueError('selected management original entry policy proof changed')
    elif order['policy_version'] == 'leader_execution_adapter/v1':
        from .leader_breakout import validate_leader_position_proof
        from .research_families import parse_strategy_config
        proof = original('execution_leader_position_context', account, order['plan']['plan_id'])
        decision = original('execution_leader_decisions', account, order['intent_id'] if 'member_spec_id' in packet else order['decision_id'])
        config = parse_strategy_config(package.candidate_spec['family'], package.candidate_spec['parameters'])
        validate_leader_position_proof(proof, config, spec, packet['run_id'])
        if proof['proof_id'] != order['plan']['policy_ref'] or decision['position_proof'] != proof:
            raise ValueError('selected management original leader policy proof changed')
    else:
        raise ValueError('selected management original execution policy is unsupported')
    if plan_ledger:
        for native in selected_plan_orders(packet):
            identity = native['intent_id']
            past = mock_automation_decision_gate_from_dict(original('execution_mock_automation_approved_gates', account, identity))
            if ((past.gate_id, past.spec_id, past.admission_id, past.account_ref, past.execution_run_id,
                    past.strategy_decision_id, past.intent_id, past.symbol, past.quantity, past.limit_price)
                    != (native['mock_plan_approval'], member, admission.admission_id, account, packet['run_id'],
                        native['decision_id'], identity, native['symbol'], native['quantity'], native['limit_price'])
                    or past.status is not MockAutomationGateStatus.APPROVED_FOR_SINGLE_SUBMISSION
                    or past.plan is None or asdict(past.plan) != native['plan']
                    or {'ENTER': 'BUY', 'REENTER': 'BUY', 'ADD': 'BUY', 'REDUCE': 'SELL', 'EXIT': 'SELL'}.get(past.action) != native['side']):
                raise ValueError('selected-plan original per-intent gate changed')
            if native['policy_version'] == 'leader_execution_adapter/v1':
                source = original('execution_leader_decisions', account, identity if 'member_spec_id' in packet else native['decision_id'])
                if source.get('position_proof') != proof:
                    raise ValueError('selected-plan original leader position proof changed')
                if past.position_action_ref is not None:
                    claims = [(key, value) for key, value in documents.items() if key[0] == 'execution_leader_position_actions'
                        and key[1] == account and value.get('intent_id') == identity]
                    if len(claims) != 1:
                        raise ValueError('selected-plan original action claim is missing')
                    key, claim = claims[0]
                    if ((claim.get('plan_id'), claim.get('policy_ref'), claim.get('run_id'), claim.get('spec_id'),
                            claim.get('decision_id'), claim.get('position_action_ref'), claim.get('action'), claim.get('quantity'))
                            != (native['plan']['plan_id'], native['plan']['policy_ref'], packet['run_id'], member,
                                native['decision_id'], past.position_action_ref, past.action, native['quantity'])):
                        raise ValueError('selected-plan original position action claim changed')
                    required.add(key)
    checkpoint = 'member:' + member if 'member_spec_id' in packet else 'current'
    optional = {(name, account, checkpoint) for name in ('execution_mock_automation_runner_current', 'execution_mock_automation_clock_current')}
    dependencies = management_checkpoint_references(packet, documents) | management_plan_references(packet, documents)
    for identity in dependencies:
        value = documents.get(identity)
        # A not-yet-created exit proof is absence, not an invitation to synthesize
        # a policy. An explicit Risk/stop reference must remain recoverable.
        if value is None:
            if identity in management_plan_references(packet, documents) or (identity[0] not in {
                    'execution_close_exit_evidence', 'execution_panic_exit_evidence'} and not allow_missing_checkpoint_dependencies):
                raise ValueError('selected management checkpoint dependency is missing: ' + identity[0])
            continue
        if identity[0] == 'execution_mock_automation_risk_snapshots':
            from .mock_automation_risk import mock_automation_risk_snapshot_from_dict
            risk = mock_automation_risk_snapshot_from_dict(value)
            if (risk.snapshot_id, risk.account_ref, risk.execution_run_id) != (identity[2], account, packet['run_id']):
                raise ValueError('selected management checkpoint Risk reference changed')
        elif identity[0] == 'execution_mock_automation_stop_revisions':
            from .mock_automation_execution import mock_automation_stop_revision_from_dict
            stop = mock_automation_stop_revision_from_dict(value)
            if (stop.revision_id, stop.account_ref, stop.execution_run_id) != (identity[2], account, packet['run_id']):
                raise ValueError('selected management checkpoint stop reference changed')
        elif identity[0] in {'execution_close_exit_evidence', 'execution_panic_exit_evidence'} and (value.get('snapshot', {}).get('account_ref'), value.get('snapshot', {}).get('execution_run_id'),
                value.get('snapshot', {}).get('spec_id')) != (account, packet['run_id'], member):
            raise ValueError('selected management checkpoint exit reference changed')
    if set(documents) - required - optional - dependencies:
        raise ValueError('selected management closure contains unrelated or live authority data')
    return spec, package, admission


def management_plan_references(packet, documents):
    """Exact archived dependencies of the selected plan's native past gates/claims."""
    from kiwoom_monitor.domain.selected_order_evidence import PLAN_MANAGEMENT_VERSION, selected_plan_orders, NATIVE_EVENT_REFERENCES
    if packet.get('version') != PLAN_MANAGEMENT_VERSION: return set()
    account, refs = packet['account_ref'], set()
    for native in selected_plan_orders(packet):
        identity = ('execution_mock_automation_approved_gates', account, native['intent_id'])
        refs.add(identity)
        gates = [documents.get(identity) or {}]
        for field in ('mock_replacement_claim', 'mock_protection_claim'):
            claim = native.get(field)
            if claim is None: continue
            gate = ('execution_mock_automation_decision_gates', account, claim['gate_id'])
            refs.add(gate)
            refs.add(('execution_mock_automation_risk_snapshots', account, claim['risk_snapshot_id']))
            gates.append(documents.get(gate) or {})
        amendment = (native.get('amendment') or {}).get('assessment')
        if isinstance(amendment, dict) and amendment.get('risk_snapshot_id'):
            refs.add(('execution_mock_automation_risk_snapshots', account, amendment['risk_snapshot_id']))
        for gate in gates:
            for field, collection in (('risk_snapshot_id', 'execution_mock_automation_risk_snapshots'),
                    ('recovery_decision_id', 'execution_mock_automation_recovery_decisions'),
                    ('admission_id', 'execution_mock_automation_admissions'),
                    ('lease_receipt_id', 'execution_mock_automation_lease_receipts')):
                if gate.get(field): refs.add((collection, account, gate[field]))
    for event in packet['events']:
        for name in NATIVE_EVENT_REFERENCES & event.keys():
            claim = event[name]
            if claim.get('risk_snapshot_id'):
                refs.add(('execution_mock_automation_risk_snapshots', account, claim['risk_snapshot_id']))
    return refs


def assess_selected_order_recovery(packet, digest, identity, source_proof, target_evidence):
    """Pure comparison of owner-verified inputs. No balances imply order ownership."""
    _, actual = encode_selected_order_evidence(packet)
    if actual != digest or not isinstance(identity, SelectedOrderIdentityMatch) or identity.packet_sha256 != digest:
        raise ValueError('selected recovery assessment crossed its verified packet')
    reasons = list(identity.reasons)
    conflicts = identity.status == 'CONFLICT'
    source = None
    if identity.status == 'MATCH':
        try:
            source = broker_evidence_from_dict(source_proof.get('broker_evidence'))
        except (KeyError, TypeError, ValueError):
            reasons.append('SOURCE_BROKER_EVIDENCE_INVALID'); conflicts = True
    if source is None:
        reasons.append('SOURCE_DATED_BROKER_EVIDENCE_UNAVAILABLE')
    if target_evidence is None:
        reasons.append('TARGET_DATED_DETAIL_PROVIDER_UNAVAILABLE')
    elif not isinstance(target_evidence, DatedBrokerEvidence):
        raise ValueError('target broker facts must come from the typed internal provider')
    elif (target_evidence.account_ref, target_evidence.credential_profile_id,
            target_evidence.credential_revision, target_evidence.binding_revision) != (
                identity.target_account_ref, identity.credential_profile_id, identity.credential_revision, identity.binding_revision):
        reasons.append('TARGET_CREDENTIAL_OR_BINDING_CHANGED'); conflicts = True
    elif target_evidence.query_started_at < identity.verified_at:
        reasons.append('TARGET_BROKER_CUT_PRECEDES_IDENTITY')
    if isinstance(target_evidence, DatedBrokerEvidence):
        if target_evidence.query_started_at < datetime.fromisoformat(packet['stopped_at']):
            reasons.append('TARGET_BROKER_CUT_PRECEDES_SOURCE_STOP')
        if source is not None and target_evidence.query_started_at < source.query_completed_at:
            reasons.append('TARGET_BROKER_CUT_PRECEDES_SOURCE_BROKER_CUT')
    if source is not None and any(datetime.fromisoformat(event['occurred_at']) > source.query_completed_at
            for event in packet['events'] if event['event_type'] in {'FILL', 'AMENDMENT_FILL_OBSERVED'}):
        reasons.append('SOURCE_BROKER_CUT_PRECEDES_KNOWN_FILL')

    from kiwoom_monitor.domain.selected_order_evidence import selected_physical_order_facts, selected_plan_orders
    expected = selected_physical_order_facts(packet)
    if any((native.get('amendment') or {}).get('status') in {'PENDING', 'UNKNOWN'}
            or (native.get('amendment') or {}).get('observed_fills') for native in selected_plan_orders(packet)):
        reasons.append('NATIVE_AMENDMENT_LINEAGE_UNRESOLVED')
    observed = {}
    if source is not None and isinstance(target_evidence, DatedBrokerEvidence):
        for label, evidence in (('SOURCE', source), ('TARGET', target_evidence)):
            if evidence.coverage != 'FULL': reasons.append(label + '_ORDER_COVERAGE_INCOMPLETE')
            orders = {order.broker_order_id: order for order in evidence.orders}
            if len(orders) != len(evidence.orders):
                reasons.append(label + '_PHYSICAL_ORDER_DUPLICATED'); conflicts = True
            observed[label] = orders
            for raw in expected:
                order = orders.get(raw['broker_order_id'])
                if order is None:
                    reasons.append(label + '_ORIGINAL_ORDER_NOT_COVERED'); continue
                wanted = (raw['original_broker_order_id'] or None, raw['symbol'], raw['venue'],
                          raw['side'], raw['ordered_quantity'], raw['limit_price'])
                values = (order.original_broker_order_id, order.symbol, order.venue, order.side, order.ordered_quantity, order.limit_price)
                for index, (value, required) in enumerate(zip(values, wanted, strict=True)):
                    if index > 0 and value is None:
                        reasons.append(label + '_ORDER_BASIS_MISSING')
                    elif value != required:
                        reasons.append(label + '_ORIGINAL_ORDER_CONFLICT'); conflicts = True
                if order.trading_date is None or order.trading_date not in evidence.covered_dates:
                    reasons.append(label + '_AUTHORITATIVE_ORDER_DATE_UNAVAILABLE')
                if order.state not in {state.value for state in OrderState}:
                    reasons.append(label + '_BROKER_ORDER_STATE_UNVERIFIED')
                details = sum(fill.quantity for fill in order.fills)
                if order.filled_quantity < max(raw['detailed_filled_quantity'], raw['broker_reported_filled_quantity']):
                    reasons.append(label + '_NATIVE_AGGREGATE_NOT_COVERED')
                if details < order.filled_quantity:
                    reasons.append(label + '_DETAILED_FILL_COVERAGE_INCOMPLETE')
                elif details > order.filled_quantity:
                    reasons.append(label + '_DETAILED_FILL_TOTAL_CONFLICT'); conflicts = True
                if order.ordered_quantity is not None and order.filled_quantity + order.remaining_quantity > order.ordered_quantity:
                    reasons.append(label + '_BROKER_ORDER_TOTAL_CONFLICT'); conflicts = True
                if order.filled_quantity + raw.get('transferred_quantity', 0) > raw['ordered_quantity']:
                    reasons.append(label + '_PHYSICAL_ALLOCATION_CONFLICT'); conflicts = True
        for raw in expected:
            number = raw['broker_order_id']
            before, after = observed['SOURCE'].get(number), observed['TARGET'].get(number)
            if before is None or after is None: continue
            if before.trading_date is not None and after.trading_date is not None and before.trading_date != after.trading_date:
                reasons.append('BROKER_ORDER_INCARNATION_DATE_CONFLICT'); conflicts = True
            for label, order in (('SOURCE', before), ('TARGET', after)):
                fills = {fill.execution_id: fill for fill in order.fills}
                if len(fills) != len(order.fills):
                    reasons.append(label + '_DETAILED_FILL_ID_DUPLICATED'); conflicts = True
                required = [BrokerFill(event['broker_execution_id'], event['quantity'], event['price'],
                    datetime.fromisoformat(event['occurred_at'])) for event in packet['events']
                    if event['event_type'] in {'FILL', 'AMENDMENT_FILL_OBSERVED'} and event['broker_order_id'] == number]
                if label == 'TARGET': required.extend(before.fills)
                for fill in required:
                    if fill.execution_id not in fills: reasons.append(label + '_KNOWN_FILL_NOT_COVERED')
                    elif fills[fill.execution_id] != fill:
                        reasons.append(label + '_DETAILED_FILL_CONTENT_CONFLICT'); conflicts = True
        for native in selected_plan_orders(packet):
            numbers = {raw['broker_order_id'] for raw in expected if raw['intent_id'] == native['intent_id']}
            if sum(order.filled_quantity for number, order in observed['TARGET'].items() if number in numbers) > native['quantity']:
                reasons.append('PHYSICAL_FILLS_EXCEED_ORIGINAL_ALLOCATION'); conflicts = True
    reasons = list(dict.fromkeys(reasons))
    result = dict(version='selected-order-recovery-assessment/v1', status='CONFLICT' if conflicts else 'UNKNOWN' if reasons else 'MATCH',
        reasons=reasons, packet_sha256=digest, operation_id=packet['operation_id'], source_account_ref=packet['account_ref'],
        target_account_ref=identity.target_account_ref, credential_revision=identity.credential_revision,
        binding_revision=identity.binding_revision, source_cursor=packet['source_cursor'],
        identity_status=identity.status, target_identity_verified_at=identity.verified_at.isoformat(),
        source_identity_proof_sha256=hashlib.sha256(_canonical(source_proof)).hexdigest() if isinstance(source_proof, dict) else None,
        target_query_ref=target_evidence.source_ref if isinstance(target_evidence, DatedBrokerEvidence) else None,
        source_broker_evidence=source.to_dict() if source is not None else None,
        target_broker_evidence=target_evidence.to_dict() if isinstance(target_evidence, DatedBrokerEvidence) else None,
        order_authority=False, runtime_imported=False,
        next_required='explicit native management adoption remains unimplemented')
    return {**result, 'assessment_id': 'selected-order-assessment-' + hashlib.sha256(_canonical(result)).hexdigest()}
