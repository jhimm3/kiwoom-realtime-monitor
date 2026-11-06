"""Install one verified closed source into an empty native ledger, without authority.

Transaction ownership stays in the existing SQLite/PostgreSQL store. Original
documents remain in the preserved packet; only target cursor coordinates differ.
Starting a runtime or accepting current-order management is a separate boundary.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json

from kiwoom_monitor.application.selected_order_recovery import (
    SelectedOrderIdentityMatch, assess_selected_order_recovery, validate_management_closure,
)
from kiwoom_monitor.domain.selected_order_evidence import COLLECTION, OWNER, encode_selected_order_evidence, selected_plan_orders


INSTALLATIONS = 'execution_selected_order_management_installations'
ASSESSMENTS = 'offline_selected_order_recovery_assessments'
PREPARATIONS = 'execution_selected_order_management_preparations'
CURRENT_MANAGEMENT = 'execution_selected_order_management_current'
ACCEPTANCES = 'execution_selected_order_management_acceptances'


def _management_document(cursor, p, collection, owner, key, *, lock=True):
    cursor.execute(f'SELECT document_json FROM central_documents WHERE collection={p} AND owner={p} AND document_key={p}'
        + (' FOR UPDATE' if p == '%s' and lock else ''), (collection, owner, key))
    row = cursor.fetchone()
    return row[0] if row and isinstance(row[0], dict) else json.loads(row[0]) if row else None


def _write_management_document(cursor, p, collection, owner, key, value, now, *, update=False):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    if update:
        cursor.execute(f'UPDATE central_documents SET updated_at={p},document_json={p} '
            f'WHERE collection={p} AND owner={p} AND document_key={p}',
            (now.timestamp(), encoded, collection, owner, key))
    else:
        cursor.execute('INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) '
            f'VALUES({p},{p},{p},{p},{p})', (collection, owner, key, now.timestamp(), encoded))


def require_selected_management_owner(cursor, p, context, lease_owner, now, *, current_owner):
    """Validate current management references; past closure facts grant no lease."""
    from kiwoom_monitor.domain.selected_order_evidence import (
        SelectedOrderManagementContext, selected_order_management_context_from_dict)
    from kiwoom_monitor.domain.time_basis import same_aware_instant
    from .database_account_identity import _latest_account_binding_row, _account_binding_row

    context = (context if isinstance(context, SelectedOrderManagementContext)
               else selected_order_management_context_from_dict(context))
    current = _management_document(cursor, p, CURRENT_MANAGEMENT,
        context.canonical_account_ref, context.canonical_account_ref)
    original = _management_document(cursor, p, PREPARATIONS, context.account_ref, context.management_id)
    installed = _management_document(cursor, p, INSTALLATIONS, context.account_ref, context.intent_id)
    if (current is None or original is None or installed is None
            or current.get('context') != context.to_dict() or original.get('context') != context.to_dict()
            or current.get('lease_owner_sha256') != hashlib.sha256(lease_owner.encode()).hexdigest()
            or original.get('lease_owner_sha256') != current['lease_owner_sha256']
            or current.get('status') not in {'RECONCILIATION_ONLY', 'MANAGEMENT_READY'}
            or (installed.get('operation_id'), installed.get('packet_sha256'), installed.get('policy_closure_sha256'),
                installed.get('target_account_ref')) != (context.operation_id, context.packet_sha256,
                    context.policy_closure_sha256, context.canonical_account_ref)
            or not callable(current_owner) or not current_owner()):
        raise RuntimeError('SELECTED_MANAGEMENT_OWNER_CHANGED')
    binding_row = _latest_account_binding_row(cursor, context.credential_profile_id, 'kiwoom', 'mock', p)
    binding = _account_binding_row(binding_row) if binding_row is not None else {}
    if ((binding.get('account_ref'), binding.get('binding_revision'), binding.get('verification_method'))
            != (context.canonical_account_ref, context.binding_revision, 'ka00001')
            or not same_aware_instant(binding.get('verified_at', ''), datetime.fromisoformat(context.binding_verified_at))):
        raise RuntimeError('SELECTED_MANAGEMENT_TARGET_BINDING_CHANGED')
    control = _management_document(cursor, p, 'execution_mock_automation_control', context.account_ref, context.account_ref)
    if (control is None or (control.get('account_ref'), control.get('active_spec_id'), control.get('execution_run_id'))
            != (context.account_ref, context.spec_id, context.execution_run_id)
            or not control.get('entry_resume_required') or control.get('account_selection') is not None):
        raise RuntimeError('SELECTED_MANAGEMENT_CONTROL_CHANGED')
    if current['status'] == 'MANAGEMENT_READY' and _management_document(cursor, p, ACCEPTANCES,
            context.account_ref, current.get('accepted_receipt_id', '')) != current:
        raise RuntimeError('SELECTED_MANAGEMENT_ACCEPTANCE_CHANGED')
    view = load_selected_order_management_policy(cursor, p, context, preparation=original, installed=installed)
    from kiwoom_monitor.application.mock_automation_admission import mock_automation_admission_from_dict
    admission = mock_automation_admission_from_dict(next(item['document'] for item in view['documents']
        if (item['collection'], item['owner'], item['key'])
        == ('execution_mock_automation_admission_by_spec', context.account_ref, context.spec_id)))
    return {**current, 'context': context, '_policy_view': view,
        '_native_binding': binding,
        '_native_member_evidence': admission.account_selection_id is not None,
        '_native_checkpoint_key': 'member:' + context.spec_id if admission.account_selection_id is not None else 'current'}


def selected_management_release_owner_key(cursor, p, context, lease_owner):
    """Release an exact original token even after its epoch was withdrawn."""
    from kiwoom_monitor.domain.selected_order_evidence import (
        SelectedOrderManagementContext, selected_order_management_context_from_dict)
    context = (context if isinstance(context, SelectedOrderManagementContext)
               else selected_order_management_context_from_dict(context))
    original = _management_document(cursor, p, PREPARATIONS, context.account_ref, context.management_id)
    if (original is None or original.get('context') != context.to_dict()
            or not lease_owner.startswith(context.execution_run_id + ':')
            or original.get('lease_owner_sha256') != hashlib.sha256(lease_owner.encode()).hexdigest()):
        raise RuntimeError('SELECTED_MANAGEMENT_RELEASE_SCOPE_CHANGED')
    return 'mock:' + context.canonical_account_ref


def load_selected_order_management_policy(cursor, p, context, *, preparation=None, installed=None):
    """Read the explicit immutable policy view; never substitute current state."""
    from kiwoom_monitor.domain.selected_order_evidence import (
        SelectedOrderManagementContext, selected_order_management_context_from_dict)
    context = (context if isinstance(context, SelectedOrderManagementContext)
               else selected_order_management_context_from_dict(context))
    preparation = (preparation if preparation is not None else
        _management_document(cursor, p, PREPARATIONS, context.account_ref, context.management_id, lock=False))
    preserved = _management_document(cursor, p, COLLECTION, OWNER, context.operation_id, lock=False)
    installed = (installed if installed is not None else
        _management_document(cursor, p, INSTALLATIONS, context.account_ref, context.intent_id, lock=False))
    if (preparation is None or preparation.get('context') != context.to_dict() or preserved is None or installed is None
            or preserved['receipt']['sha256'] != context.packet_sha256
            or encode_selected_order_evidence(preserved['packet'])[1] != context.packet_sha256
            or installed['policy_closure_sha256'] != context.policy_closure_sha256):
        raise RuntimeError('SELECTED_MANAGEMENT_POLICY_SOURCE_CHANGED')
    packet = preserved['packet']
    spec, _, admission = validate_management_closure(packet)
    if ((spec.spec_id, admission.admission_id, packet['account_ref'], packet['run_id'],
            packet['order']['intent_id'], packet.get('member_spec_id'), admission.version, admission.account_selection_id)
            != (context.spec_id, context.admission_id, context.account_ref, context.execution_run_id,
                context.intent_id, context.member_spec_id, context.source_admission_version, context.source_account_selection_id)):
        raise RuntimeError('SELECTED_MANAGEMENT_POLICY_SOURCE_CHANGED')
    closure = packet['management_closure']
    actual = hashlib.sha256(json.dumps(closure, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    if actual != context.policy_closure_sha256:
        raise RuntimeError('SELECTED_MANAGEMENT_POLICY_SOURCE_CHANGED')
    return dict(documents=deepcopy(closure['documents']), shadow_candidate=deepcopy(closure['shadow_candidate']),
        current_lease_receipt=deepcopy(preparation['current_lease_receipt']), installation=deepcopy(installed),
        source_plan_position=deepcopy(closure['source_plan_position']),
        source_intent_ids=tuple(order['intent_id'] for order in selected_plan_orders(packet)),
        source_orders=tuple(deepcopy(order) for order in selected_plan_orders(packet)),
        source_events=tuple(deepcopy(event) for event in packet['events']))


def selected_management_policy_document(cursor, p, collection, owner, key, selected_management):
    """Borrow one claim-local view whose current authority was checked separately."""
    if selected_management is None:
        return None
    context = selected_management['context']
    view = selected_management.get('_policy_view')
    if view is None:
        view = load_selected_order_management_policy(cursor, p, context)
        selected_management['_policy_view'] = view
    document = next((item['document'] for item in view['documents']
        if (item['collection'], item['owner'], item['key']) == (collection, owner, key)), None)
    if document is not None:
        return deepcopy(document)
    if collection == 'execution_mock_automation_admissions' and owner == context.account_ref and key == context.admission_id:
        return next((deepcopy(item['document']) for item in view['documents']
            if (item['collection'], item['owner'], item['key'])
            == ('execution_mock_automation_admission_by_spec', context.account_ref, context.spec_id)), None)
    receipt = view['current_lease_receipt']
    if owner == context.account_ref and (
            collection == 'execution_mock_automation_lease_by_admission' and key == context.admission_id
            or collection == 'execution_mock_automation_lease_receipts' and key == receipt['receipt_id']):
        return deepcopy(receipt)
    return None


def load_selected_management_fences(cursor, p, store, management):
    """Project current native state for display without altering its receipt."""
    from .database_account_settings import _load_account_settings
    context = management['context']
    cursor.execute(f'SELECT owner_token,lease_expires_at FROM central_execution_runtime_leases WHERE owner_key={p}',
        ('mock:' + context['canonical_account_ref'],))
    lease = cursor.fetchone()
    lease_matches = False
    if lease is not None:
        expires = lease[1] if isinstance(lease[1], datetime) else datetime.fromisoformat(lease[1])
        lease_matches = (hashlib.sha256(lease[0].encode()).hexdigest() == management['lease_owner_sha256']
            and expires > store._now_provider())
    settings = _load_account_settings(cursor, dict(broker='kiwoom', environment='mock',
        account_ref=context['canonical_account_ref']), p)
    read_settings_match = (settings['revision'] == management.get('settings_revision')
        and settings['active_profile_id'] == context['credential_profile_id']
        and settings['monitor_enabled'])
    return lease_matches, read_settings_match, read_settings_match and settings['mock_order_enabled']


def prepare_selected_order_management(cursor, p, store, operation_id, *, identity, broker_evidence,
        source_proof, current_target_runtime, new_owner_token, current_owner, expected_settings_revision):
    """Atomically hand the present target lease to one original reconciliation run."""
    from kiwoom_monitor.application.mock_automation_admission import build_mock_automation_lease_receipt
    from kiwoom_monitor.domain.selected_order_evidence import SelectedOrderManagementContext
    from .database_account_identity import _execution_runtime_scope_keys
    from .database_account_settings import _load_account_settings

    preserved = _management_document(cursor, p, COLLECTION, OWNER, operation_id)
    if preserved is None:
        raise ValueError('selected management requires its installed original packet')
    packet, digest = preserved['packet'], preserved['receipt']['sha256']
    _, actual_digest = encode_selected_order_evidence(packet)
    spec, _, admission = validate_management_closure(packet)
    origin, canonical = packet['account_ref'], identity.target_account_ref
    if p == '%s':
        cursor.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',
                       ('execution-runtime-owner:mock:' + canonical,))
    installed = _management_document(cursor, p, INSTALLATIONS, origin, packet['order']['intent_id'])
    prior_assessment = (_management_document(cursor, p, ASSESSMENTS, OWNER, installed['assessment_id'])
                        if installed is not None else None)
    if (actual_digest != digest or installed is None or identity.status != 'MATCH'
            or (installed['operation_id'], installed['packet_sha256'], installed['target_account_ref'])
            != (operation_id, digest, canonical)
            or prior_assessment is None or prior_assessment.get('source_identity_proof') != source_proof
            or not callable(current_owner) or not current_owner()
            or type(new_owner_token) is not str or not new_owner_token):
        raise RuntimeError('SELECTED_MANAGEMENT_PREPARATION_CHANGED')
    assessment = assess_selected_order_recovery(packet, digest, identity, source_proof, broker_evidence)
    if assessment['status'] != 'MATCH':
        raise RuntimeError('SELECTED_MANAGEMENT_CURRENT_MATCH_REQUIRED')
    binding = store.load_latest_account_binding(identity.credential_profile_id, 'kiwoom', 'mock', _reader=cursor)
    if (binding is None or (binding['account_ref'], binding['binding_revision'], binding['verification_method'])
            != (canonical, identity.binding_revision, 'ka00001')):
        raise RuntimeError('SELECTED_MANAGEMENT_TARGET_BINDING_CHANGED')
    settings = _load_account_settings(cursor,
        dict(broker='kiwoom', environment='mock', account_ref=canonical), p, lock=True)
    if (type(expected_settings_revision) is not int or settings['revision'] != expected_settings_revision
            or settings['active_profile_id'] != identity.credential_profile_id or not settings['monitor_enabled']):
        raise RuntimeError('ACCOUNT_SETTINGS_REVISION_CONFLICT')
    existing = _management_document(cursor, p, CURRENT_MANAGEMENT, canonical, canonical)
    new_lease_owner = packet['run_id'] + ':' + new_owner_token
    owner_digest = hashlib.sha256(new_lease_owner.encode()).hexdigest()
    if (existing is not None and existing['context']['operation_id'] == operation_id
            and existing['lease_owner_sha256'] == owner_digest):
        require_selected_management_owner(cursor, p, existing['context'], new_lease_owner,
            store._now_provider(), current_owner=current_owner)
        return {'receipt': existing, 'replayed': True}
    if (not isinstance(current_target_runtime, dict)
            or set(current_target_runtime) != {'account_ref', 'run_id', 'owner_token'}
            or current_target_runtime['account_ref'] != canonical):
        raise RuntimeError('SELECTED_MANAGEMENT_CURRENT_RUNTIME_REQUIRED')
    now = store._now_provider()
    scope = _execution_runtime_scope_keys(cursor, p, 'mock:' + origin)
    if scope['canonical_owner_key'] != 'mock:' + canonical:
        raise RuntimeError('SELECTED_MANAGEMENT_CANONICAL_SCOPE_CHANGED')
    related = scope['related_owner_keys']
    cursor.execute('SELECT owner_key,owner_token,lease_expires_at FROM central_execution_runtime_leases '
        f'WHERE owner_key IN ({",".join([p] * len(related))})' + (' FOR UPDATE' if p == '%s' else ''), related)
    leases = cursor.fetchall()
    old_token = current_target_runtime['run_id'] + ':' + current_target_runtime['owner_token']
    if len(leases) != 1 or tuple(leases[0][:2]) != ('mock:' + canonical, old_token):
        raise RuntimeError('SELECTED_MANAGEMENT_CURRENT_LEASE_CHANGED')
    expires = leases[0][2] if isinstance(leases[0][2], datetime) else datetime.fromisoformat(leases[0][2])
    if expires <= now:
        raise RuntimeError('SELECTED_MANAGEMENT_CURRENT_LEASE_EXPIRED')
    control = _management_document(cursor, p, 'execution_mock_automation_control', origin, origin)
    if (control is not None and control.get('desired_state') == 'RUNNING' and existing is not None
            and existing.get('status') == 'MANAGEMENT_READY'
            and existing['context']['operation_id'] == operation_id
            and existing.get('accepted_control_revision') == control.get('control_revision')
            and _management_document(cursor, p, ACCEPTANCES, origin, existing['accepted_receipt_id']) == existing):
        # A restarted target read owner cannot reuse yesterday's acceptance.
        # This explicit preparation withdraws management before its fresh cut.
        control = {**control, 'desired_state': 'STOPPED', 'control_revision': control['control_revision'] + 1,
            'changed_at': now.isoformat(), 'reason': 'explicit selected management preparation after owner replacement'}
        _write_management_document(cursor, p, 'execution_mock_automation_control', origin, origin, control, now, update=True)
    if (control is None or (control.get('desired_state'), control.get('active_spec_id'), control.get('execution_run_id'))
            != ('STOPPED', spec.spec_id, packet['run_id']) or not control.get('entry_resume_required')):
        raise RuntimeError('SELECTED_MANAGEMENT_CONTROL_CHANGED')
    body = dict(operation_id=operation_id, packet_sha256=digest, canonical_account_ref=canonical,
        credential_profile_id=identity.credential_profile_id, credential_revision=identity.credential_revision,
        binding_revision=identity.binding_revision, settings_revision=expected_settings_revision,
        lease_owner_sha256=owner_digest, prepared_at=now.isoformat())
    management_id = 'selected_management_' + hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    context = SelectedOrderManagementContext(management_id, operation_id, origin, canonical, packet['run_id'],
        spec.spec_id, admission.admission_id, packet['order']['intent_id'], packet.get('member_spec_id'),
        identity.credential_profile_id, identity.credential_revision, identity.binding_revision,
        binding['verified_at'], digest, installed['policy_closure_sha256'], admission.version, admission.account_selection_id)
    receipt = dict(version='selected-order-management-preparation/v1', context=context.to_dict(),
        lease_owner_sha256=owner_digest, prepared_at=now.isoformat(), current_assessment=assessment,
        initial_control_revision=control['control_revision'], current_lease_receipt=build_mock_automation_lease_receipt(
            admission, admitted_at=now).to_dict(), status='RECONCILIATION_ONLY', order_authority=False,
        management_ready=False, new_orders_enabled=False, settings_revision=expected_settings_revision)
    _write_management_document(cursor, p, PREPARATIONS, origin, management_id, receipt, now)
    _write_management_document(cursor, p, CURRENT_MANAGEMENT, canonical, canonical, receipt, now,
                               update=existing is not None)
    cursor.execute(f'UPDATE central_execution_runtime_leases SET owner_token={p},updated_at={p} '
        f'WHERE owner_key={p} AND owner_token={p}',
        (new_lease_owner, now.isoformat(), 'mock:' + canonical, old_token))
    if cursor.rowcount != 1 or not current_owner():
        raise RuntimeError('SELECTED_MANAGEMENT_CURRENT_LEASE_CHANGED')
    latest = store.load_latest_account_binding(identity.credential_profile_id, 'kiwoom', 'mock', _reader=cursor)
    if latest != binding:
        raise RuntimeError('SELECTED_MANAGEMENT_TARGET_BINDING_CHANGED')
    if _load_account_settings(cursor, settings['scope'], p, lock=True) != settings:
        raise RuntimeError('ACCOUNT_SETTINGS_REVISION_CONFLICT')
    return {'receipt': receipt, 'replayed': False}


def accept_selected_order_management(cursor, p, store, context, *, identity, source_proof,
        broker_evidence, lease_owner, expected_control_revision, current_owner, expected_settings_revision):
    """Accept only the current original-plan management cut, with new entry OFF."""
    from dataclasses import replace
    from kiwoom_monitor.application.mock_automation_admission import (
        MockAutomationDesiredState, mock_automation_control_from_dict)
    from kiwoom_monitor.application.mock_automation_risk import mock_automation_risk_snapshot_from_dict
    from kiwoom_monitor.domain.order_contract import TERMINAL_ORDER_STATES
    from kiwoom_monitor.domain.selected_order_evidence import (
        SelectedOrderManagementContext, selected_order_management_context_from_dict)
    from kiwoom_monitor.infrastructure.persistence.execution_repository import (
        _record_from_document, execution_broker_legs)
    from .database_account_settings import _load_account_settings
    context = (context if isinstance(context, SelectedOrderManagementContext)
        else selected_order_management_context_from_dict(context))
    canonical, origin = context.canonical_account_ref, context.account_ref
    if p == '%s':
        cursor.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',
            ('execution-runtime-owner:mock:' + canonical,))
    now = store._now_provider()
    current = require_selected_management_owner(cursor, p, context, lease_owner, now, current_owner=current_owner)
    settings = _load_account_settings(cursor,
        dict(broker='kiwoom', environment='mock', account_ref=canonical), p, lock=True)
    if (type(expected_settings_revision) is not int
            or settings['revision'] != expected_settings_revision
            or current.get('settings_revision') != expected_settings_revision
            or settings['active_profile_id'] != context.credential_profile_id):
        raise RuntimeError('ACCOUNT_SETTINGS_REVISION_CONFLICT')
    if not settings['monitor_enabled'] or not settings['mock_order_enabled']:
        raise RuntimeError('MOCK_ORDER_TRANSPORT_DISABLED')
    cursor.execute('SELECT owner_token,lease_expires_at FROM central_execution_runtime_leases '
        f'WHERE owner_key={p}' + (' FOR UPDATE' if p == '%s' else ''), ('mock:' + canonical,))
    row = cursor.fetchone()
    expiry = (row[1] if isinstance(row[1], datetime) else datetime.fromisoformat(row[1])) if row else None
    if row is None or row[0] != lease_owner or expiry <= now:
        raise RuntimeError('SELECTED_MANAGEMENT_CURRENT_LEASE_CHANGED')
    control_document = _management_document(cursor, p, 'execution_mock_automation_control', origin, origin)
    control = mock_automation_control_from_dict(control_document)
    if type(expected_control_revision) is not int or expected_control_revision < 1:
        raise ValueError('selected management acceptance requires its current control revision')
    if current['status'] == 'MANAGEMENT_READY' and control.desired_state.value == 'RUNNING':
        if (control.control_revision != current['accepted_control_revision']
                or expected_control_revision != control.control_revision - 1):
            raise RuntimeError('SELECTED_MANAGEMENT_CONTROL_CHANGED')
        return {'receipt': {key: value for key, value in current.items() if not key.startswith('_')
            and key != 'context'} | {'context': context.to_dict()}, 'replayed': True}
    if (control.control_revision != expected_control_revision
            or control.desired_state.value != 'STOPPED'):
        raise RuntimeError('SELECTED_MANAGEMENT_CONTROL_CHANGED')
    preserved = _management_document(cursor, p, COLLECTION, OWNER, context.operation_id)
    packet = preserved['packet']
    installed = _management_document(cursor, p, INSTALLATIONS, origin, context.intent_id)
    original_assessment = _management_document(cursor, p, ASSESSMENTS, OWNER, installed['assessment_id'])
    if original_assessment is None or original_assessment.get('source_identity_proof') != source_proof:
        raise RuntimeError('SELECTED_MANAGEMENT_SOURCE_PROOF_CHANGED')
    spec, _, _ = validate_management_closure(packet)
    assessment = assess_selected_order_recovery(packet, context.packet_sha256, identity, source_proof, broker_evidence)
    if (assessment['status'] != 'MATCH' or (identity.target_account_ref, identity.credential_profile_id,
            identity.credential_revision, identity.binding_revision) != (canonical, context.credential_profile_id,
                context.credential_revision, context.binding_revision)):
        raise RuntimeError('SELECTED_MANAGEMENT_CURRENT_MATCH_REQUIRED')
    risk_document = _management_document(cursor, p, 'execution_mock_automation_current_risk', origin, origin)
    if risk_document is None:
        raise RuntimeError('SELECTED_MANAGEMENT_CURRENT_RISK_REQUIRED')
    risk = mock_automation_risk_snapshot_from_dict(risk_document)
    if ((risk.account_ref, risk.execution_run_id, risk.binding_revision) != (origin, context.execution_run_id, context.binding_revision)
            or _management_document(cursor, p, 'execution_mock_automation_risk_snapshots', origin, risk.snapshot_id) != risk_document
            or not risk.reconciliation_complete or risk.submission_unknown_count or risk.balance_mismatch_count
            or risk.broker_query_started_at < max(datetime.fromisoformat(current['prepared_at']), control.changed_at)
            or any(not 0 <= (now - at).total_seconds() <= spec.maximum_data_gap_seconds for at in (
                risk.observed_at, risk.account_as_of, broker_evidence.query_started_at, broker_evidence.query_completed_at))):
        raise RuntimeError('SELECTED_MANAGEMENT_CURRENT_RISK_CHANGED')
    from kiwoom_monitor.application.mock_automation_recovery import mock_automation_recovery_decision_from_dict
    recovery_document = _management_document(cursor, p, 'execution_mock_automation_current_recovery', origin, context.admission_id)
    if recovery_document is None:
        raise RuntimeError('SELECTED_MANAGEMENT_CURRENT_RECOVERY_REQUIRED')
    recovery = mock_automation_recovery_decision_from_dict(recovery_document)
    if ((recovery.account_ref, recovery.spec_id, recovery.admission_id, recovery.execution_run_id,
            recovery.lease_receipt_id, recovery.risk_snapshot_id, recovery.risk_reconciliation_revision,
            recovery.observed_at, recovery.account_as_of) != (origin, context.spec_id, context.admission_id,
                context.execution_run_id, current['_policy_view']['current_lease_receipt']['receipt_id'],
                risk.snapshot_id, risk.reconciliation_revision, risk.observed_at, risk.account_as_of)
            or recovery.status.value not in {'CLEARED_ORDERS_DISABLED', 'MANAGE_ONLY_ORDERS_DISABLED'}
            or _management_document(cursor, p, 'execution_mock_automation_recovery_decisions', origin,
                recovery.decision_id) != recovery_document):
        raise RuntimeError('SELECTED_MANAGEMENT_CURRENT_RECOVERY_CHANGED')
    cursor.execute('SELECT COALESCE(MAX(e.accepted_sequence),0) FROM central_execution_events e '
        'JOIN central_execution_intents i ON i.intent_id=e.intent_id '
        f'WHERE i.environment={p} AND i.account_ref={p}', ('mock', origin))
    if cursor.fetchone()[0] != risk.execution_event_high_watermark:
        raise RuntimeError('SELECTED_MANAGEMENT_NATIVE_EVENTS_CHANGED')
    view = current['_policy_view']
    originals = {value['intent_id']: _record_from_document(value) for value in view['source_orders']}
    if context.intent_id not in originals or tuple(originals) != view['source_intent_ids']:
        raise RuntimeError('SELECTED_MANAGEMENT_ORIGINAL_ORDER_CHANGED')
    root = originals[context.intent_id]
    if root.intent.plan is None or root.intent.side.value != 'BUY':
        raise RuntimeError('SELECTED_MANAGEMENT_ORIGINAL_ORDER_CHANGED')
    observed = {order.broker_order_id: order for order in broker_evidence.orders}
    for identity, original in originals.items():
        raw = store.load_execution_intent(identity, _reader=cursor)
        if raw is None:
            raise RuntimeError('SELECTED_MANAGEMENT_ORIGINAL_ORDER_CHANGED')
        record = _record_from_document(raw)
        if (record.intent != original.intent or record.intent.account_ref != origin
                or record.intent.run_id != context.execution_run_id or record.intent.plan is None
                or (record.intent.plan.plan_id, record.intent.plan.policy_ref, record.intent.symbol)
                != (root.intent.plan.plan_id, root.intent.plan.policy_ref, root.intent.symbol)
                or record.state.value not in {'ACCEPTED', 'PARTIALLY_FILLED', 'FILLED', 'CANCELLED', 'REJECTED'}
                or record.amendment is not None and record.amendment.reconciliation_pending):
            raise RuntimeError('SELECTED_MANAGEMENT_ORIGINAL_ORDER_CHANGED')
        events = store.load_execution_events(identity, _reader=cursor)
        for leg in execution_broker_legs(record):
            order = observed.get(leg.broker_order_id)
            fills = {(event['broker_execution_id'], event['quantity'], event['price'],
                datetime.fromisoformat(event['occurred_at'])) for event in events
                if event['event_type'] == 'FILL' and event['broker_order_id'] == leg.broker_order_id}
            if (order is None or (order.symbol, order.venue, order.side, order.ordered_quantity, order.limit_price,
                    order.original_broker_order_id or None) != (record.intent.symbol, record.intent.venue,
                        record.intent.side.value, leg.ordered_quantity, leg.limit_price, leg.original_broker_order_id or None)
                    or {(fill.execution_id, fill.quantity, fill.price, fill.occurred_at) for fill in order.fills} != fills
                    or order.filled_quantity != leg.detailed_filled_quantity
                    or order.remaining_quantity != (0 if leg.state in TERMINAL_ORDER_STATES else leg.remaining_quantity)):
                raise RuntimeError('SELECTED_MANAGEMENT_CURRENT_DETAIL_CHANGED')
    head = _management_document(cursor, p, 'execution_mock_plan_positions', origin, root.intent.plan.plan_id)
    if (head is None or (head.get('run_id'), head.get('symbol'), head.get('policy_ref'))
            != (context.execution_run_id, root.intent.symbol, root.intent.plan.policy_ref)
            or head['quantity'] != sum(dict(risk.owned_position_quantities).values())):
        raise RuntimeError('SELECTED_MANAGEMENT_CURRENT_POSITION_CHANGED')
    updated = replace(control, desired_state=MockAutomationDesiredState.RUNNING,
        control_revision=control.control_revision + 1, changed_at=now,
        reason='explicit current original-plan management acceptance', entry_resume_required=True)
    body = dict(management_id=context.management_id, control_revision=updated.control_revision,
        risk_snapshot_id=risk.snapshot_id, assessment_id=assessment['assessment_id'])
    receipt_id = 'selected_management_acceptance_' + hashlib.sha256(json.dumps(body,
        sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    receipt = {key: value for key, value in current.items() if not key.startswith('_') and key != 'context'}
    receipt.update(context=context.to_dict(), version='selected-order-management-acceptance/v1',
        accepted_receipt_id=receipt_id, accepted_at=now.isoformat(), accepted_control_revision=updated.control_revision,
        current_assessment=assessment, risk_snapshot_id=risk.snapshot_id, status='MANAGEMENT_READY',
        order_authority=True, management_ready=True, new_orders_enabled=False)
    _write_management_document(cursor, p, 'execution_mock_automation_control', origin, origin, updated.to_dict(), now, update=True)
    _write_management_document(cursor, p, ACCEPTANCES, origin, receipt_id, receipt, now)
    _write_management_document(cursor, p, CURRENT_MANAGEMENT, canonical, canonical, receipt, now, update=True)
    latest = store.load_latest_account_binding(context.credential_profile_id, 'kiwoom', 'mock', _reader=cursor)
    if latest != current['_native_binding'] or not current_owner():
        raise RuntimeError('SELECTED_MANAGEMENT_OWNER_CHANGED')
    if _load_account_settings(cursor, settings['scope'], p, lock=True) != settings:
        raise RuntimeError('ACCOUNT_SETTINGS_REVISION_CONFLICT')
    return {'receipt': receipt, 'replayed': False}


def install_selected_order_management(cursor, placeholder, store, packet, *, identity, source_proof,
                                      broker_evidence, current_owner, current_target_runtime=None):
    """Borrow one native write cursor and a server-owned current-epoch fence."""
    encoded, digest = encode_selected_order_evidence(packet)
    packet = json.loads(encoded)
    validate_management_closure(packet)
    if not isinstance(identity, SelectedOrderIdentityMatch) or identity.status != 'MATCH' or not callable(current_owner):
        raise ValueError('selected management bootstrap requires the current verified identity owner')
    assessment = assess_selected_order_recovery(packet, digest, identity, source_proof, broker_evidence)
    if assessment['status'] != 'MATCH':
        raise ValueError('selected management bootstrap requires a complete MATCH: ' + ','.join(assessment['reasons']))
    p, account, order = placeholder, packet['account_ref'], packet['order']
    lock = ' FOR UPDATE' if p == '%s' else ''
    if p == '%s':
        for owner_key in sorted({'mock:' + account, 'mock:' + identity.target_account_ref}):
            cursor.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', ('execution-runtime-owner:' + owner_key,))
    def document(collection, owner, key):
        cursor.execute(f'SELECT document_json FROM central_documents WHERE collection={p} AND owner={p} AND document_key={p}' + lock,
            (collection, owner, key))
        row = cursor.fetchone()
        return row[0] if row and isinstance(row[0], dict) else json.loads(row[0]) if row else None
    def store_document(collection, owner, key, value, at):
        cursor.execute('INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) '
            f'VALUES({p},{p},{p},{p},{p})', (collection, owner, key, at.timestamp(),
                json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)))
    cursor.execute(f'SELECT account_ref FROM central_account_registry WHERE account_ref={p} AND status=\'active\'' + lock,
        (identity.target_account_ref,))
    if cursor.fetchone() is None:
        raise RuntimeError('SELECTED_BOOTSTRAP_TARGET_IDENTITY_CHANGED')
    preserved = document(COLLECTION, OWNER, packet['operation_id'])
    if (preserved is None or preserved['packet'] != packet or preserved['receipt']['sha256'] != digest
            or preserved['receipt']['order_authority'] is not False):
        raise ValueError('selected management bootstrap crossed the preserved native packet receipt')
    binding = store.load_latest_account_binding(identity.credential_profile_id, 'kiwoom', 'mock', _reader=cursor)
    if (binding is None or (binding['account_ref'], binding['binding_revision'], binding['verification_method'])
            != (identity.target_account_ref, identity.binding_revision, 'ka00001') or not current_owner()):
        raise RuntimeError('SELECTED_BOOTSTRAP_TARGET_EPOCH_CHANGED')
    previous = document(INSTALLATIONS, account, order['intent_id'])
    if previous is not None:
        if (previous['packet_sha256'], previous['operation_id'], previous['target_account_ref'],
                previous['credential_profile_id'], previous['credential_revision'], previous['binding_revision']) != (
                digest, packet['operation_id'], identity.target_account_ref, identity.credential_profile_id,
                identity.credential_revision, identity.binding_revision):
            raise ValueError('immutable selected management installation conflict')
        return {'receipt': previous, 'replayed': True}
    # A different UUID/alias is not permission to coexist with another owner.
    # The explicit owner may borrow its one existing read-only target runtime.
    # Its exact token is checked here, never retained in the imported receipt.
    def current_read_runtime():
        cursor.execute('SELECT owner_key,owner_token,lease_expires_at FROM central_execution_runtime_leases' + lock)
        leases = cursor.fetchall()
        if current_target_runtime is None:
            if leases: raise RuntimeError('SELECTED_BOOTSTRAP_REQUIRES_EMPTY_INACTIVE_TARGET')
            cursor.execute('SELECT 1 FROM central_execution_account_snapshots LIMIT 1')
        else:
            context = current_target_runtime
            if (not isinstance(context, dict) or set(context) != {'account_ref', 'run_id', 'owner_token'}
                    or any(type(value) is not str or not value for value in context.values())
                    or context['account_ref'] != identity.target_account_ref or len(leases) != 1):
                raise RuntimeError('SELECTED_BOOTSTRAP_TARGET_RUNTIME_CHANGED')
            key, token, expires = leases[0]
            expires = expires if isinstance(expires, datetime) else datetime.fromisoformat(str(expires))
            if ((key, token) != ('mock:' + identity.target_account_ref, context['run_id'] + ':' + context['owner_token'])
                    or expires <= store._now_provider()):
                raise RuntimeError('SELECTED_BOOTSTRAP_TARGET_RUNTIME_CHANGED')
            cursor.execute('SELECT 1 FROM central_execution_account_snapshots '
                f'WHERE environment<>\'mock\' OR account_ref<>{p} LIMIT 1', (identity.target_account_ref,))
        if cursor.fetchone() is not None:
            raise RuntimeError('SELECTED_BOOTSTRAP_REQUIRES_EMPTY_INACTIVE_TARGET')
    cursor.execute('SELECT 1 FROM central_execution_intents UNION ALL SELECT 1 FROM central_execution_events '
        "UNION ALL SELECT 1 FROM central_documents WHERE collection IN ('execution_mock_automation_control',"
        "'execution_mock_plan_positions') LIMIT 1")
    if cursor.fetchone() is not None:
        raise RuntimeError('SELECTED_BOOTSTRAP_REQUIRES_EMPTY_INACTIVE_TARGET')
    current_read_runtime()
    # Preserve the original intent/physical facts and member identity. Past gate
    # and admission documents stay exclusively inside the immutable packet.
    for native_order in selected_plan_orders(packet):
        cursor.execute('INSERT INTO central_execution_intents(intent_id,run_id,environment,account_ref,state,broker_order_id,'
            'last_broker_as_of,created_at,updated_at,document_json) '
            f'VALUES({",".join([p] * 10)})', (native_order['intent_id'], native_order['run_id'], native_order['environment'], native_order['account_ref'],
                native_order['state'], native_order['broker_order_id'], native_order['last_broker_as_of'], native_order['created_at'], native_order['updated_at'],
                json.dumps(native_order, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)))
    coordinates, target_fill_cursor = [], 0
    for source_event in packet['events']:
        event = {name: value for name, value in source_event.items() if name != 'accepted_sequence'}
        cursor.execute('INSERT INTO central_execution_events(event_id,intent_id,state,occurred_at,received_at,'
            'broker_execution_id,document_json) ' + f'VALUES({",".join([p] * 7)})'
            + (' RETURNING accepted_sequence' if p == '%s' else ''),
            (event['event_id'], event['intent_id'], event['state'], event['occurred_at'], event['received_at'],
                event['broker_execution_id'], json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)))
        sequence = int(cursor.fetchone()[0]) if p == '%s' else int(cursor.lastrowid)
        coordinates.append(dict(event_id=event['event_id'], source_accepted_sequence=source_event['accepted_sequence'],
            target_accepted_sequence=sequence))
        if event['event_type'] == 'FILL': target_fill_cursor = sequence
    now = broker_evidence.query_completed_at
    head = {**packet['management_closure']['source_plan_position'], 'fill_cursor': target_fill_cursor}
    store_document('execution_mock_plan_positions', account, order['plan']['plan_id'], head, now)
    from kiwoom_monitor.application.mock_automation_admission import MockAutomationControl, MockAutomationDesiredState, MOCK_AUTOMATION_CONTROL_VERSION
    control = MockAutomationControl(MOCK_AUTOMATION_CONTROL_VERSION, account, MockAutomationDesiredState.STOPPED, 1,
        packet['source_spec_id'], packet['run_id'], now, 'selected order installed; current native management acceptance required',
        entry_resume_required=True)
    store_document('execution_mock_automation_control', account, account, control.to_dict(), now)
    receipt = dict(version='selected-order-management-installation/v1', operation_id=packet['operation_id'],
        intent_id=order['intent_id'], account_ref=account, target_account_ref=identity.target_account_ref,
        credential_profile_id=identity.credential_profile_id, credential_revision=identity.credential_revision,
        binding_revision=identity.binding_revision, packet_sha256=digest, assessment_id=assessment['assessment_id'],
        ledger_scope=packet['management_closure']['ledger_scope'],
        source_cursor=packet['source_cursor'], source_plan_fill_cursor=packet['plan_position']['fill_cursor'],
        target_plan_fill_cursor=target_fill_cursor, event_coordinates=coordinates, installed_at=now.isoformat(),
        policy_closure_sha256=hashlib.sha256(json.dumps(packet['management_closure'], ensure_ascii=False,
            sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest(),
        status='INSTALLED_RECONCILIATION_REQUIRED', order_authority=False, management_ready=False,
        new_orders_enabled=False, runtime_started=False,
        next_required='verified canonical single lease and fresh native management acceptance')
    store_document(ASSESSMENTS, OWNER, assessment['assessment_id'],
        dict(assessment=assessment, source_identity_proof=deepcopy(source_proof)), now)
    store_document(INSTALLATIONS, account, order['intent_id'], receipt, now)
    # This executes inside the existing owner account lock and immediately before
    # its native transaction can commit; pending key changes cannot be accepted.
    latest_binding = store.load_latest_account_binding(identity.credential_profile_id, 'kiwoom', 'mock', _reader=cursor)
    current_read_runtime()
    if latest_binding != binding or not current_owner(): raise RuntimeError('SELECTED_BOOTSTRAP_TARGET_EPOCH_CHANGED')
    return {'receipt': receipt, 'replayed': False}
