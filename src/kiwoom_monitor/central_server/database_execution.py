"""Execution ledger, mock control and runtime lease DB implementations.

Each store method retains its native connection and transaction ownership.
Ownership checks borrow that transaction's cursor so lease/control fences and
intent/event writes remain atomic. No broker or order scheduling belongs here.
"""
from __future__ import annotations

from kiwoom_monitor.domain.time_basis import kst_now

import json
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from typing import Any

from kiwoom_monitor.central_server.database_codec import _event_document, _json_document

PLAN_POSITION_COLLECTION = "execution_mock_plan_positions"


def _execution_runtime_lease_scope(cursor, p, owner_key, owner_token, now, *,
                                   selected_management_context=None, selected_management_owner=None):
    """Serialize canonical collisions; explicit current context alone maps authority."""
    from .database_account_identity import _execution_runtime_scope_keys
    scope = _execution_runtime_scope_keys(cursor, p, owner_key)
    canonical = scope['canonical_owner_key']
    if p == '%s':
        cursor.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', ('execution-runtime-owner:' + canonical,))
        scope = _execution_runtime_scope_keys(cursor, p, owner_key)
        if scope['canonical_owner_key'] != canonical:
            raise RuntimeError('EXECUTION_CANONICAL_SCOPE_CHANGED')
    selected = None
    if selected_management_context is not None:
        from .selected_order_bootstrap import require_selected_management_owner
        selected = require_selected_management_owner(cursor, p, selected_management_context, owner_token, now,
            current_owner=selected_management_owner)
        context = selected['context']
        if (owner_key != 'mock:' + context.account_ref or canonical != 'mock:' + context.canonical_account_ref
                or not owner_token.startswith(context.execution_run_id + ':')):
            raise RuntimeError('SELECTED_MANAGEMENT_RUNTIME_SCOPE_CHANGED')
    elif scope['origin_requires_management']:
        raise RuntimeError('SELECTED_MANAGEMENT_CONTEXT_REQUIRED')
    keys = scope['related_owner_keys']
    cursor.execute('SELECT owner_key,lease_expires_at FROM central_execution_runtime_leases '
        f'WHERE owner_key IN ({",".join(p for _ in keys)})' + (' FOR UPDATE' if p == '%s' else ''), keys)
    for key, expires in cursor.fetchall():
        expiry = expires if isinstance(expires, datetime) else datetime.fromisoformat(expires)
        if key != canonical and expiry > now:
            raise RuntimeError('EXECUTION_CANONICAL_OWNERSHIP_CONFLICT')
    return canonical, selected


def _selected_target_management_pending(cursor, p, account, lease_owner):
    """Retained installation markers alone do not permanently lock a flat target."""
    import hashlib
    from kiwoom_monitor.domain.order_contract import TERMINAL_ORDER_STATES
    from kiwoom_monitor.infrastructure.persistence.execution_repository import _record_from_document, execution_broker_legs

    current = _plan_document(cursor, p, 'execution_selected_order_management_current', account, account)
    if (current is not None and current.get('lease_owner_sha256')
            == hashlib.sha256(lease_owner.encode()).hexdigest()):
        return True  # An active selected token cannot shed its restricted context.
    target = "document_json->>'target_account_ref'" if p == '%s' else "json_extract(document_json,'$.target_account_ref')"
    cursor.execute(f'SELECT owner,document_key FROM central_documents WHERE collection={p} AND {target}={p}',
        ('execution_selected_order_management_installations', account))
    installed = tuple(tuple(row) for row in cursor.fetchall())
    if current is not None:
        context = current.get('context') or {}
        if (not isinstance(context, dict) or context.get('canonical_account_ref') != account
                or (context.get('account_ref'), context.get('intent_id')) not in installed):
            return True
    for origin, identity in installed:
        raw = _execution_intent_row(cursor, identity, p)
        if raw is None:
            return True
        original = _json_document(raw[0])
        plan = original.get('plan') or {}
        if (original.get('account_ref') != origin or original.get('environment') != 'mock'
                or not plan.get('plan_id') or not plan.get('policy_ref')):
            return True
        head = _plan_document(cursor, p, PLAN_POSITION_COLLECTION, origin, plan['plan_id'])
        if (head is None or head.get('version') != 'mock_plan_position/v1'
                or (head.get('plan_id'), head.get('run_id'), head.get('symbol'), head.get('policy_ref'))
                != (plan['plan_id'], original['run_id'], original['symbol'], plan['policy_ref'])
                or type(head.get('quantity')) is not int or head['quantity'] != 0
                or type(head.get('unreconciled_sell_quantity', 0)) is not int
                or head.get('unreconciled_sell_quantity', 0) != 0):
            return True
        plan_key = "document_json->'plan'->>'plan_id'" if p == '%s' else "json_extract(document_json,'$.plan.plan_id')"
        before, bought, sold, found = '', 0, 0, False
        while True:
            cursor.execute('SELECT document_json FROM central_execution_intents '
                f'WHERE environment={p} AND account_ref={p} AND run_id={p} AND {plan_key}={p} '
                f'AND intent_id>{p} ORDER BY intent_id LIMIT 400',
                ('mock', origin, original['run_id'], plan['plan_id'], before))
            page = cursor.fetchall()
            for row in page:
                document = _json_document(row[0])
                try:
                    record = _record_from_document(document)
                    if ((record.intent.account_ref, record.intent.run_id, record.intent.symbol)
                            != (origin, original['run_id'], original['symbol'])
                            or record.intent.plan is None or record.intent.plan.policy_ref != plan['policy_ref']
                            or record.state not in TERMINAL_ORDER_STATES
                            or record.detailed_filled_quantity != record.broker_reported_filled_quantity
                            or record.filled_quantity != record.detailed_filled_quantity
                            or record.amendment is not None and record.amendment.reconciliation_pending
                            or any(leg.state not in TERMINAL_ORDER_STATES
                                or leg.detailed_filled_quantity != leg.broker_reported_filled_quantity
                                or leg.filled_quantity != leg.detailed_filled_quantity
                                for leg in execution_broker_legs(record))):
                        return True
                except (TypeError, ValueError, KeyError):
                    return True
                found = True
                if record.intent.side.value == 'BUY': bought += record.detailed_filled_quantity
                else: sold += record.detailed_filled_quantity
                before = record.intent.intent_id
            if len(page) < 400:
                break
        if not found or bought != sold:
            return True
    return False


def _native_execution_scope(ownership, control):
    """Borrow verified control facts without a second lookup or caller scope hints.

    These private fields exist only in the local validator call chain. They are
    overwritten at each native ownership/control entry and never persisted.
    """
    management = control.get('_native_selected_management_context')
    if management is not None:
        return {**ownership, '_native_checkpoint_key': management['_native_checkpoint_key'],
                '_native_member_evidence': management['_native_member_evidence'],
                '_native_account_selection': None,
                '_native_selected_management_context': management}
    selection = control.get('account_selection')
    member = ownership.get('active_spec_id') if selection is not None else None
    legacy_retained = bool(selection is not None and any(
        ref['spec_id'] == member and ref['original_selection_id'] is None
        for ref in selection.get('retained_admission_refs', ())))
    if legacy_retained:
        member = None  # Original checkpoint/proof coordinates stay unchanged.
    return {**ownership, '_native_checkpoint_key': 'member:' + member if member is not None else 'current',
            '_native_member_evidence': member is not None,
            '_native_account_selection': selection, '_native_selected_management_context': None}


def _require_execution_ownership(cursor: Any, ownership: dict[str, Any] | None,
                                 value: dict[str, Any], p: str, *, now_provider=None,
                                 new_intent_claim=False, submission_claim=False, amendment_claim=False,
                                 control_facts=None, event_kind=None, rehearsal=False):
    if rehearsal or value.get('environment') == 'real':
        if (not rehearsal or p != '?' or ownership is None or value.get('environment') != 'real'
                or 'plan' in value or 'mock_account_member_spec_id' in value
                or set(ownership) - {'owner_key', 'owner_token', 'run_id', 'amendment_recovery'}
                or ownership.get('owner_key') != f"rehearsal:{value.get('account_ref')}"
                or not ownership['owner_token'].startswith(f"{ownership['run_id']}:")
                or value.get('run_id', ownership['run_id']) != ownership['run_id']):
            raise RuntimeError('LOCAL_REHEARSAL_OWNERSHIP_REQUIRED')
    if 'mock_account_member_spec_id' in value and (new_intent_claim or 'plan' not in value):
        raise ValueError('account member attribution is assigned only by the native plan claim')
    if new_intent_claim and value.get('broker_venue') is not None:
        raise ValueError('the broker venue must be assigned after order submission')
    if ownership is None:
        if "plan" in value:
            raise RuntimeError("PLANNED_EXECUTION_OWNER_REQUIRED")
        return  # Offline ledger/import callers keep the existing unowned contract.
    now = kst_now() if now_provider is None else now_provider()
    selected = None
    lease_key = ownership['owner_key']
    # Responses to durable STARTED records remain facts after STOP. Existing
    # plan/physical-ledger validators still check them before the native write.
    reconciliation_facts = {'FILL', 'BROKER_FILL_AGGREGATE', 'BROKER_RECONCILED', 'SOR_VENUE_CONFIRMED',
                            'AMENDMENT_FILL_OBSERVED', 'AMENDMENT_RECOVERED',
                            'ORDER_ACCEPTED', 'SUBMISSION_RESPONSE_UNKNOWN', 'SUBMISSION_REJECTED',
                            'CANCEL_ACCEPTED', 'CANCEL_RESPONSE_UNKNOWN', 'CANCEL_REJECTED',
                            'AMENDMENT_ACCEPTED', 'AMENDMENT_RESPONSE_UNKNOWN', 'AMENDMENT_REJECTED'}
    command_claim = (new_intent_claim or submission_claim or amendment_claim
                     or 'intent_id' in value and event_kind not in reconciliation_facts)
    if control_facts is not None:
        control_facts.pop('_native_selected_management_context', None)
    if ownership.get('selected_management_context') is not None:
        lease_key, selected = _execution_runtime_lease_scope(cursor, p, lease_key, ownership['owner_token'], now,
            selected_management_context=ownership['selected_management_context'],
            selected_management_owner=ownership.get('selected_management_owner'))
        context = selected['context']
        accounts = {context.account_ref} if 'intent_id' in value else {context.account_ref, context.canonical_account_ref}
        if (value.get('account_ref') not in accounts or ownership['run_id'] != context.execution_run_id
                or ownership.get('active_spec_id') not in (None, context.spec_id)):
            raise RuntimeError('SELECTED_MANAGEMENT_RUNTIME_SCOPE_CHANGED')
        if value.get('side') == 'BUY' and (new_intent_claim or submission_claim or amendment_claim):
            raise RuntimeError('SELECTED_MANAGEMENT_NEW_BUY_FORBIDDEN')
        if selected['status'] != 'MANAGEMENT_READY' and command_claim:
            raise RuntimeError('SELECTED_MANAGEMENT_NOT_READY')
    elif not rehearsal:
        # An alias or pending installation is collision evidence, never an
        # alternative native order authority, including a hand-inserted lease.
        account = str(value.get('account_ref') or '')
        cursor.execute(f'SELECT 1 FROM central_account_scope_aliases WHERE origin_account_ref={p} '
            f'AND environment={p} UNION ALL SELECT 1 FROM central_documents WHERE collection={p} AND owner={p} LIMIT 1',
            (account, 'mock', 'execution_selected_order_management_installations', account))
        if cursor.fetchone() is not None:
            raise RuntimeError('SELECTED_MANAGEMENT_CONTEXT_REQUIRED')
        if (command_claim and _selected_target_management_pending(cursor, p, account, ownership['owner_token'])):
            raise RuntimeError('SELECTED_MANAGEMENT_CONTEXT_REQUIRED')
    if (new_intent_claim and value.get('policy_version') == 'mock-protection-exit/v1'
            and ownership.get('protection_claim') is None):
        raise RuntimeError('MOCK_PROTECTION_CLAIM_REQUIRED')
    if new_intent_claim and (ownership.get('cancel_retry_claim') is not None
                             or ownership.get("planned_cancel_claim") is not None):
        raise RuntimeError('MOCK_PROTECTIVE_CANCEL_RETRY_CANNOT_CREATE_ORDER')
    if not rehearsal and (not ownership["owner_token"].startswith(f"{ownership['run_id']}:")
            or value.get("environment") != "mock"
            or selected is None and ownership["owner_key"] != f"mock:{value.get('account_ref')}"
            or ("run_id" in value and value["run_id"] != ownership["run_id"])):
        raise RuntimeError("EXECUTION_OWNER_SCOPE_MISMATCH")
    cursor.execute("SELECT owner_token,lease_expires_at FROM central_execution_runtime_leases "
                   f"WHERE owner_key={p}" + (" FOR UPDATE" if p == "%s" else ""), (lease_key,))
    row = cursor.fetchone()
    expiry = row[1] if row and isinstance(row[1], datetime) else datetime.fromisoformat(str(row[1])) if row else None
    if not row or row[0] != ownership["owner_token"] or expiry <= now:
        raise RuntimeError("EXECUTION_OWNERSHIP_LOST")
    if rehearsal:
        return
    control = {}
    control_revision = ownership.get("control_revision")
    entry_claim = (new_intent_claim or submission_claim or amendment_claim) and value.get("side") == "BUY"
    if (control_revision is not None or (entry_claim and ownership["run_id"].startswith("mock_auto_run_"))
            or selected is not None and command_claim):
        cursor.execute(
            "SELECT document_json FROM central_documents WHERE collection=" + p
            + " AND owner=" + p + " AND document_key=" + p
            + (" FOR UPDATE" if p == "%s" else ""),
            (
                "execution_mock_automation_control",
                str(value.get("account_ref") or ""),
                str(value.get("account_ref") or ""),
            ),
        )
        control_row = cursor.fetchone()
        control = _json_document(control_row[0]) if control_row else {}
        control.pop('_native_selected_management_context', None)
        if control_facts is not None:
            control_facts.update(control)
        if (selected is not None and command_claim
                and (control.get('desired_state') != 'RUNNING' or not control.get('entry_resume_required'))):
            raise RuntimeError('SELECTED_MANAGEMENT_CONTROL_CHANGED')
        if selected is not None and command_claim:
            from .database_account_settings import _load_account_settings
            settings = _load_account_settings(cursor, {'broker': 'kiwoom', 'environment': 'mock',
                'account_ref': selected['context'].canonical_account_ref}, p, lock=True)
            if (type(selected.get('settings_revision')) is not int
                    or settings.get('revision') != selected['settings_revision']
                    or settings.get('active_profile_id') != selected['context'].credential_profile_id
                    or settings.get('monitor_enabled') is not True or settings.get('mock_order_enabled') is not True):
                raise RuntimeError('SELECTED_MANAGEMENT_EXECUTION_SETTINGS_CHANGED')
        stopped_close_management = False
        if (control.get('desired_state') == 'STOPPED' and value.get('plan') is not None
                and value.get('policy_version') in {'close_execution_adapter/v1', 'panic_execution_adapter/v1'}
                and (value.get('side') == 'SELL'
                     or (ownership.get('planned_cancel_claim') or {}).get('version') in {
                         'close-entry-deadline-cancel/v1', 'close-entry-protective-cancel/v1',
                         'close-current-order-management/v1', 'panic-current-order-management/v1'})):
            from kiwoom_monitor.domain.execution_activation import (
                allows_stopped_close_management, allows_stopped_panic_management, mock_automation_spec_from_dict,
            )

            spec_doc = _plan_document(cursor, p, 'execution_mock_automation_specs', value['account_ref'],
                                       ownership.get('active_spec_id'))
            if spec_doc is not None:
                bound_spec = mock_automation_spec_from_dict(spec_doc)
                stopped_close_management = (allows_stopped_close_management(bound_spec)
                    if value.get('policy_version') == 'close_execution_adapter/v1' else allows_stopped_panic_management(bound_spec))
        if control_revision is not None and (
            (selected is None and control.get("desired_state") != "RUNNING" and not stopped_close_management)
            or int(control.get("control_revision", 0)) != int(control_revision)
            or selected is None and not _control_member_allowed(cursor, p, control, str(value.get('account_ref') or ''),
                                           ownership.get('active_spec_id'),
                                           entry=(new_intent_claim or submission_claim) and value.get('side') == 'BUY')
            or control.get("execution_run_id") != ownership["run_id"]
        ):
            raise RuntimeError("MOCK_AUTOMATION_CONTROL_CHANGED")
        if (entry_claim and control.get("execution_run_id") == ownership["run_id"]
                and control.get("entry_resume_required", False)):
            raise RuntimeError("CREDENTIAL_CHANGE_REQUIRES_EXPLICIT_ENTRY_RESUME")
    if selected is not None:
        control['_native_selected_management_context'] = selected
        if control_facts is not None:
            control_facts['_native_selected_management_context'] = selected
    ownership = _native_execution_scope(ownership, control)
    if ownership.get('replacement_claim') is not None and 'intent_id' in value:
        _require_mock_replacement_claim(cursor, ownership, value, p, new_intent_claim=new_intent_claim,
            now_provider=now_provider)
    if ownership.get('protection_claim') is not None and 'intent_id' in value and 'plan' not in value:
        _require_mock_protection_claim(cursor, ownership, value, p, now_provider=now_provider,new_intent_claim=new_intent_claim)
    if (new_intent_claim or (submission_claim and "plan" in value)) and value.get('policy_version') == 'leader_execution_adapter/v1':
        _require_leader_execution_claim(cursor, ownership, value, p, now_provider=now_provider)
    if (new_intent_claim or submission_claim) and value.get("policy_version") == "close_execution_adapter/v1":
        _require_close_execution_claim(cursor, p, value, ownership, now_provider=now_provider,
                                       submission_claim=submission_claim)
    if (new_intent_claim or submission_claim) and value.get('policy_version') == 'panic_execution_adapter/v1':
        _require_panic_execution_claim(cursor, p, value, ownership, now_provider=now_provider,
                                       submission_claim=submission_claim)
    if "intent_id" in value:
        plan_projection = "document_json->'plan'" if p == "%s" else "json_extract(document_json,'$.plan')"
        stored_projection = "document_json" if "plan" in value else plan_projection
        cursor.execute(f"SELECT environment,account_ref,run_id,{stored_projection} FROM central_execution_intents WHERE intent_id={p}",
                       (value["intent_id"],))
        stored = cursor.fetchone()
        if stored is not None:
            if tuple(stored[:3]) != (value["environment"], value["account_ref"], value["run_id"]):
                raise RuntimeError("EXECUTION_OWNER_SCOPE_MISMATCH")
            stored_value = _json_document(stored[3]) if "plan" in value else None
            stored_plan = stored_value.get("plan") if stored_value is not None else stored[3]
            if stored_plan != value.get("plan"):
                raise RuntimeError("EXECUTION_PLAN_REFERENCE_CHANGED")
        else:
            stored_value = None
        if 'plan' in value and (new_intent_claim or submission_claim) and (
            value.get('policy_version') == 'mock-protection-exit/v1' or ownership.get('protection_claim') is not None
        ):
            _require_planned_leader_protection_claim(cursor, p, value, ownership, stored=stored_value,
                                                     now_provider=now_provider, submission_claim=submission_claim)
        if selected is not None and not ownership['selected_management_owner']():
            raise RuntimeError('SELECTED_MANAGEMENT_OWNER_CHANGED')
        return stored_value
    if selected is not None and not ownership['selected_management_owner']():
        raise RuntimeError('SELECTED_MANAGEMENT_OWNER_CHANGED')


def _plan_document(cursor, p, collection, account, key, *, selected_management=None):
    cursor.execute(
        f"SELECT document_json FROM central_documents WHERE collection={p} AND owner={p} AND document_key={p}"
        + (" FOR UPDATE" if p == "%s" else ""),
        (collection, account, key),
    )
    row = cursor.fetchone()
    if row is not None:
        return _json_document(row[0])
    if selected_management is not None:
        from .selected_order_bootstrap import selected_management_policy_document
        return selected_management_policy_document(cursor, p, collection, account, key, selected_management)
    return None


def _close_exit_for_intent(cursor, p, account, plan, decision_id, *, selected_management=None):
    from kiwoom_monitor.application.close_mock_execution import CLOSE_EXIT_COLLECTION, close_exit_key

    return (_plan_document(cursor, p, CLOSE_EXIT_COLLECTION, account, close_exit_key(plan, decision_id),
                           selected_management=selected_management)
            or _plan_document(cursor, p, CLOSE_EXIT_COLLECTION, account, close_exit_key(plan),
                              selected_management=selected_management))


def _control_member_allowed(cursor, p, control, account, spec_id, *, entry=False):
    """Read frozen membership in the same native transaction as the order claim."""
    selection = _account_control_selection(cursor, p, control, account)
    return ((selection.allows_entry(spec_id) if entry else spec_id in selection.member_spec_ids) if selection is not None
            else control.get('active_spec_id') == spec_id)


def _control_entry_enabled(control, spec_id):
    from kiwoom_monitor.application.mock_automation_admission import mock_automation_control_from_dict
    selection = mock_automation_control_from_dict(control).account_selection
    return selection is None or selection.allows_entry(spec_id)


def _account_control_limits(control):
    from kiwoom_monitor.application.mock_automation_admission import (
        MOCK_AUTOMATION_ACCOUNT_CONTROL_VERSION, mock_automation_control_from_dict,
    )
    return (mock_automation_control_from_dict(control).account_selection.limits
            if control.get('version') == MOCK_AUTOMATION_ACCOUNT_CONTROL_VERSION else None)


def _account_control_selection(cursor, p, control, account, *, verify_admissions=False, require_current_binding=True):
    from kiwoom_monitor.application.mock_automation_admission import (
        MOCK_AUTOMATION_ACCOUNT_CONTROL_VERSION, MOCK_AUTOMATION_ACCOUNT_ADMISSION_VERSION,
        mock_automation_control_from_dict, mock_automation_admission_from_dict,
        mock_automation_lease_receipt_from_dict,
    )
    from kiwoom_monitor.domain.time_basis import same_aware_instant
    from .database_account_identity import _account_binding_row, _latest_account_binding_row

    if control.get('version') != MOCK_AUTOMATION_ACCOUNT_CONTROL_VERSION:
        if control.get('account_selection') is not None:
            raise RuntimeError('MOCK_ACCOUNT_CONTROL_MEMBERSHIP_CHANGED')
        return None
    value = mock_automation_control_from_dict(control)
    selection = value.account_selection
    stored = _plan_document(cursor, p, 'execution_mock_automation_account_selections', account, selection.selection_id)
    if (value.to_dict() != control or value.account_ref != account or stored != selection.to_dict()):
        raise RuntimeError('MOCK_ACCOUNT_CONTROL_MEMBERSHIP_CHANGED')
    if require_current_binding:
        row = _latest_account_binding_row(cursor, selection.credential_profile_id, 'kiwoom', 'mock', p)
        binding = _account_binding_row(row) if row is not None else {}
        if (binding.get('account_ref') != account or binding.get('binding_revision') != selection.binding_revision
                or binding.get('verification_method') != 'ka00001'
                or not same_aware_instant(str(binding.get('verified_at', '')), selection.binding_verified_at)):
            raise RuntimeError('MOCK_ACCOUNT_CONTROL_BINDING_CHANGED')
    if verify_admissions:
        for spec_id in selection.member_spec_ids:
            document = _plan_document(cursor, p, 'execution_mock_automation_admission_by_spec', account, spec_id)
            if document is None:
                raise RuntimeError('MOCK_ACCOUNT_MEMBER_ADMISSION_MISSING')
            admission = mock_automation_admission_from_dict(document)
            retained = getattr(selection, 'retained_admission_ref', lambda _: None)(spec_id)
            original_standalone = (admission.version == 'mock_automation_admission/v1' and retained is not None
                                   and retained.original_selection_id is None)
            if (admission.version != MOCK_AUTOMATION_ACCOUNT_ADMISSION_VERSION and not original_standalone
                    or admission.spec_id != spec_id or admission.account_ref != account
                    or not selection.matches_admission(spec_id=spec_id, admission_id=admission.admission_id,
                        original_selection_id=admission.account_selection_id, execution_run_id=admission.execution_run_id)
                    or admission.execution_run_id != value.execution_run_id
                    or _plan_document(cursor, p, 'execution_mock_automation_admissions', account,
                                      admission.admission_id) != document):
                raise RuntimeError('MOCK_ACCOUNT_MEMBER_ADMISSION_CHANGED')
            receipt_doc = _plan_document(cursor, p, 'execution_mock_automation_lease_by_admission', account,
                                        admission.admission_id)
            if receipt_doc is None:
                raise RuntimeError('MOCK_ACCOUNT_MEMBER_LEASE_MISSING')
            receipt = mock_automation_lease_receipt_from_dict(receipt_doc)
            if ((receipt.admission_id, receipt.spec_id, receipt.account_ref, receipt.execution_run_id)
                    != (admission.admission_id, spec_id, account, value.execution_run_id)):
                raise RuntimeError('MOCK_ACCOUNT_MEMBER_LEASE_CHANGED')
    return selection


def _require_account_control_change(cursor, p, value, current, account):
    from kiwoom_monitor.application.mock_automation_admission import MOCK_AUTOMATION_ACCOUNT_CONTROL_VERSION
    if current and value.get('account_selection') != current.get('account_selection'):
        raise RuntimeError('MOCK_ACCOUNT_EXPLICIT_HANDOFF_REQUIRED')
    running = value.get('desired_state') == 'RUNNING'
    _account_control_selection(cursor, p, value, account, verify_admissions=running, require_current_binding=running)


def _require_account_replacement_control(cursor, p, value, current, owner_token, now_provider):
    """Only this explicit CAS can extend membership while retaining original admissions."""
    from kiwoom_monitor.domain.execution_activation import MockAutomationAccountSelectionV2
    from kiwoom_monitor.application.mock_automation_admission import mock_automation_control_from_dict

    account = value['account_ref']
    if mock_automation_control_from_dict(current).to_dict() != current:
        raise RuntimeError('MOCK_ACCOUNT_REPLACEMENT_CONTROL_CHANGED')
    previous = _account_control_selection(cursor, p, current, account, verify_admissions=True)
    selected = _account_control_selection(cursor, p, value, account, verify_admissions=True)
    if not isinstance(selected, MockAutomationAccountSelectionV2):
        raise RuntimeError('MOCK_ACCOUNT_REPLACEMENT_SCOPE_CHANGED')
    mutable = {'account_selection', 'control_revision', 'changed_at', 'reason', 'version'}
    if ({key: item for key, item in value.items() if key not in mutable}
            != {key: item for key, item in current.items() if key not in mutable}
            or selected.primary_spec_id != current.get('active_spec_id')
            or datetime.fromisoformat(value['changed_at']) < selected.frozen_at):
        raise RuntimeError('MOCK_ACCOUNT_REPLACEMENT_CONTROL_CHANGED')
    retained = {ref.spec_id for ref in selected.retained_admission_refs}
    previous_ids = set(previous.member_spec_ids if previous is not None else (current['active_spec_id'],))
    if (not previous_ids <= retained
            or not retained <= previous_ids | set(selected.entry_spec_ids)):
        raise RuntimeError('MOCK_ACCOUNT_REPLACEMENT_RETAINED_MEMBERS_CHANGED')
    for identity in retained:
        reference = selected.retained_admission_ref(identity)
        original = _plan_document(cursor, p, 'execution_mock_automation_admissions', account, reference.admission_id)
        if (original is None or original != _plan_document(cursor, p,
                'execution_mock_automation_admission_by_spec', account, identity)
                or original.get('account_selection_id') != reference.original_selection_id
                or original.get('execution_run_id') != current['execution_run_id']):
            raise RuntimeError('MOCK_ACCOUNT_REPLACEMENT_ADMISSION_CHANGED')
    projected_selection, _ = _retained_execution_member_projection(cursor, p, account,
        selection_id=selected.selection_id, expected_control_revision=current['control_revision'])
    if projected_selection != selected:
        raise RuntimeError('MOCK_ACCOUNT_REPLACEMENT_PROJECTION_CHANGED')
    _require_account_resume_control(cursor, p, value, current, owner_token, now_provider, replacement=True)


def _require_account_resume_control(cursor, p, value, current, owner_token, now_provider, *, replacement=False):
    """One account CAS independently rechecks every member against one native cut."""
    from kiwoom_monitor.application.mock_automation_admission import (
        MOCK_AUTOMATION_ACCOUNT_CONTROL_VERSION, mock_automation_control_from_dict,
        mock_automation_admission_from_dict, mock_automation_lease_receipt_from_dict,
    )
    from kiwoom_monitor.application.mock_automation_recovery import (
        MockAutomationAccountRecoveryDecision, MockAutomationRecoveryStatus, mock_automation_recovery_decision_from_dict,
    )
    from kiwoom_monitor.application.mock_automation_risk import mock_automation_risk_snapshot_from_dict
    from kiwoom_monitor.domain.execution_activation import LEADER_NATIVE_PLAN_MODE, mock_automation_spec_from_dict
    from kiwoom_monitor.domain.order_contract import OrderSide, TERMINAL_ORDER_STATES
    from kiwoom_monitor.domain.time_basis import same_aware_instant
    from kiwoom_monitor.infrastructure.persistence.execution_repository import _record_from_document, execution_broker_legs
    from .database_account_identity import _account_binding_row, _latest_account_binding_row

    if not replacement and (current.get('version') != MOCK_AUTOMATION_ACCOUNT_CONTROL_VERSION
            or value.get('desired_state') != 'RUNNING' or value.get('entry_resume_required')
            or (current.get('desired_state') != 'STOPPED' and not current.get('entry_resume_required'))):
        return
    selected = mock_automation_control_from_dict(value)
    selection = selected.account_selection
    account, run_id = selected.account_ref, selected.execution_run_id
    if (not owner_token or (current.get('active_spec_id'), current.get('execution_run_id'))
            != (selection.primary_spec_id, run_id)
            or not replacement and value.get('planned_review_resume_id') is not None):
        raise RuntimeError('MOCK_ACCOUNT_RESUME_SCOPE_CHANGED')
    if p == '%s':
        cursor.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',
                       (f'{selection.credential_profile_id}:kiwoom:mock',))
    binding_row = _latest_account_binding_row(cursor, selection.credential_profile_id, 'kiwoom', 'mock', p)
    binding = _account_binding_row(binding_row) if binding_row else {}
    if ((binding.get('account_ref'), binding.get('binding_revision'), binding.get('verification_method'))
            != (account, selection.binding_revision, 'ka00001')
            or not same_aware_instant(binding.get('verified_at', ''), selection.binding_verified_at)):
        raise RuntimeError('MOCK_ACCOUNT_RESUME_BINDING_CHANGED')

    def document(collection, key):
        result = _plan_document(cursor, p, collection, account, key)
        if result is None:
            raise RuntimeError('MOCK_ACCOUNT_RESUME_PROOF_MISSING')
        return result

    risk_doc = document('execution_mock_automation_current_risk', account)
    risk = mock_automation_risk_snapshot_from_dict(risk_doc)
    now = now_provider()
    if ((risk.account_ref, risk.execution_run_id, risk.binding_revision) != (account, run_id, selection.binding_revision)
            or document('execution_mock_automation_risk_snapshots', risk.snapshot_id) != risk_doc
            or not risk.reconciliation_complete or risk.submission_unknown_count or risk.balance_mismatch_count
            or risk.broker_query_started_at < datetime.fromisoformat(current['changed_at'])):
        raise RuntimeError('MOCK_ACCOUNT_RESUME_RISK_CHANGED')
    cursor.execute('SELECT COALESCE(MAX(e.accepted_sequence),0) FROM central_execution_events e '
        'JOIN central_execution_intents i ON i.intent_id=e.intent_id '
        f'WHERE i.environment={p} AND i.account_ref={p}', ('mock', account))
    if cursor.fetchone()[0] != risk.execution_event_high_watermark:
        raise RuntimeError('MOCK_ACCOUNT_RESUME_NATIVE_EVENTS_CHANGED')
    active = tuple(_record_from_document(_json_document(row[0])) for row in
                   _account_active_execution_rows(cursor, 'mock', account, 401, p))
    if any(ref.original_selection_id is None for ref in getattr(selection, 'retained_admission_refs', ())):
        from kiwoom_monitor.infrastructure.persistence.execution_repository import project_execution_record
        _, projection = _retained_execution_member_projection(cursor, p, account,
            **(dict(selection_id=selection.selection_id, expected_control_revision=current['control_revision'])
               if replacement else {}), intent_ids=tuple(record.intent.intent_id for record in active))
        active = tuple(project_execution_record(record, projection) for record in active)
    if len(active) == 401:
        raise RuntimeError('MOCK_ACCOUNT_RESUME_ORDER_COVERAGE_CHANGED')
    broker_ids, reserved = {}, 0
    for record in active:
        member = getattr(record, 'member_spec_id', None)
        if (member not in selection.member_spec_ids or record.intent.run_id != run_id
                or record.state.value not in {'ACCEPTED', 'PARTIALLY_FILLED'}
                or record.amendment is not None and record.amendment.reconciliation_pending):
            raise RuntimeError('MOCK_ACCOUNT_RESUME_ORDER_COVERAGE_CHANGED')
        legs = execution_broker_legs(record)
        if not legs:
            raise RuntimeError('MOCK_ACCOUNT_RESUME_ORDER_COVERAGE_CHANGED')
        for leg in legs:
            if leg.state in TERMINAL_ORDER_STATES or not leg.remaining_quantity:
                continue
            if leg.broker_order_id in broker_ids or leg.detailed_filled_quantity != leg.broker_reported_filled_quantity:
                raise RuntimeError('MOCK_ACCOUNT_RESUME_ORDER_COVERAGE_CHANGED')
            broker_ids[leg.broker_order_id] = member
            if record.intent.side is OrderSide.BUY:
                reserved += leg.remaining_quantity * leg.limit_price
    reviews = getattr(selected, 'planned_review_resume_ids', {})
    common_cut = None
    for member_id in selection.member_spec_ids:
        spec = mock_automation_spec_from_dict(document('execution_mock_automation_specs', member_id))
        admission = mock_automation_admission_from_dict(document('execution_mock_automation_admission_by_spec', member_id))
        receipt = mock_automation_lease_receipt_from_dict(document('execution_mock_automation_lease_by_admission', admission.admission_id))
        recovery_doc = document('execution_mock_automation_current_recovery', admission.admission_id)
        recovery = mock_automation_recovery_decision_from_dict(recovery_doc)
        if ((spec.account_scope.account_ref, spec.credential_profile_id, spec.binding_revision, spec.data_path)
                != (account, selection.credential_profile_id, selection.binding_revision, risk.data_path)
                or not same_aware_instant(spec.binding_verified_at.isoformat(), selection.binding_verified_at)
                or not isinstance(recovery, MockAutomationAccountRecoveryDecision)
                or (recovery.account_ref, recovery.execution_run_id, recovery.admission_id, recovery.lease_receipt_id,
                    recovery.spec_id, recovery.account_selection_id, recovery.member_spec_ids)
                != (account, run_id, admission.admission_id, receipt.receipt_id, member_id,
                    selection.selection_id, selection.member_spec_ids)
                or (recovery.risk_snapshot_id, recovery.risk_reconciliation_revision, recovery.account_as_of)
                != (risk.snapshot_id, risk.reconciliation_revision, risk.account_as_of)
                or document('execution_mock_automation_recovery_decisions', recovery.decision_id) != recovery_doc
                or recovery.observed_at < datetime.fromisoformat(current['changed_at'])
                or recovery.status not in {MockAutomationRecoveryStatus.CLEARED_ORDERS_DISABLED,
                                           MockAutomationRecoveryStatus.MANAGE_ONLY_ORDERS_DISABLED}
                or recovery.open_order_count != len(broker_ids) or recovery.reserved_open_buy_won != reserved
                or set(recovery.own_open_order_ids) != {identity for identity, member in broker_ids.items() if member == member_id}
                or set(recovery.other_member_open_order_ids) != {identity for identity, member in broker_ids.items() if member != member_id}
                or recovery.own_position_quantities != risk.owned_for_member(member_id)):
            raise RuntimeError('MOCK_ACCOUNT_RESUME_MEMBER_RECOVERY_CHANGED')
        cut = (recovery.recovery_fingerprint, recovery.observation_input_ref, recovery.observed_at)
        if not recovery.observation_input_ref or common_cut is not None and common_cut != cut:
            raise RuntimeError('MOCK_ACCOUNT_RESUME_CUT_CHANGED')
        common_cut = cut
        age = min(selection.limits.maximum_data_gap_seconds, spec.maximum_data_gap_seconds)
        if any(not 0 <= (now - at).total_seconds() <= age for at in (risk.observed_at, risk.account_as_of)):
            raise RuntimeError('MOCK_ACCOUNT_RESUME_RISK_STALE')
        retained = getattr(selection, 'retained_admission_ref', lambda _: None)(member_id)
        checkpoint_key = ('current' if retained is not None and retained.original_selection_id is None
                          else 'member:' + member_id)
        checkpoint = _plan_document(cursor, p, 'execution_mock_automation_runner_current', account, checkpoint_key) or {}
        if replacement:
            continue  # Replacement preserves every existing review and does not resume entries.
        if spec.execution_plan_mode == LEADER_NATIVE_PLAN_MODE:
            _require_planned_resume_control(cursor, p,
                {**value, 'active_spec_id': member_id, 'planned_review_resume_id': reviews.get(member_id)},
                {**current, 'active_spec_id': member_id}, owner_token, now_provider,
                member_context=dict(spec=spec, risk=risk, admission=admission, recovery=recovery,
                                    checkpoint=checkpoint, now=now, active=active))
        else:
            notice = checkpoint.get('protection_review')
            if member_id in reviews or notice and notice.get('state') != 'RESOLVED_FLAT':
                raise RuntimeError('MOCK_ACCOUNT_RESUME_REVIEW_SCOPE_CHANGED')
            if notice and ((checkpoint.get('spec_id'), checkpoint.get('execution_run_id')) != (member_id, run_id)
                    or recovery.own_position_quantities or recovery.own_open_order_ids
                    or risk.pending_sells_for_member(member_id)
                    or risk.broker_query_started_at < datetime.fromisoformat(notice['requested_at'])):
                raise RuntimeError('MOCK_ACCOUNT_RESUME_REVIEW_FLAT_CHANGED')


def _require_planned_resume_control(cursor, p, value, current, owner_token, now_provider, *, member_context=None):
    """Fence the existing RUNNING CAS against one acknowledged flat native review."""
    from kiwoom_monitor.application.mock_automation_admission import mock_automation_admission_from_dict
    from kiwoom_monitor.application.mock_automation_execution import (
        MockAutomationGateStatus, mock_automation_decision_gate_from_dict, mock_automation_intent_id,
    )
    from kiwoom_monitor.application.mock_automation_recovery import (
        MockAutomationRecoveryStatus, mock_automation_recovery_decision_from_dict,
    )
    from kiwoom_monitor.application.mock_automation_risk import (
        PROTECTION_EXIT_VERSION, mock_automation_risk_snapshot_from_dict,
        planned_review_resume_id, read_planned_leader_protection_checkpoint,
    )
    from kiwoom_monitor.domain.execution_activation import LEADER_NATIVE_PLAN_MODE, mock_automation_spec_from_dict
    from kiwoom_monitor.domain.time_basis import same_aware_instant
    from .database_account_identity import _account_binding_row, _latest_account_binding_row

    resume_id = value.get("planned_review_resume_id")
    if (not current or value.get("desired_state") != "RUNNING" or value.get("entry_resume_required")
            or (current.get("desired_state") != "STOPPED" and not current.get("entry_resume_required"))):
        if owner_token is not None or resume_id is not None:
            raise RuntimeError("PLANNED_RESUME_CONTROL_CHANGED")
        return
    account, run_id, spec_id = (value.get(key) for key in ("account_ref", "execution_run_id", "active_spec_id"))
    current_spec_id = current.get("active_spec_id")
    if current_spec_id is None:
        if owner_token is not None or resume_id is not None:
            raise RuntimeError("PLANNED_RESUME_SCOPE_CHANGED")
        return
    spec_doc = (member_context['spec'].to_dict() if member_context is not None else
                _plan_document(cursor, p, "execution_mock_automation_specs", account, current_spec_id))
    if not spec_doc or spec_doc.get("execution_plan_mode") != LEADER_NATIVE_PLAN_MODE:
        if owner_token is not None or resume_id is not None:
            raise RuntimeError("PLANNED_RESUME_SCOPE_CHANGED")
        return
    if not owner_token or (current.get("active_spec_id"), current.get("execution_run_id")) != (spec_id, run_id):
        raise RuntimeError("PLANNED_RESUME_SCOPE_CHANGED")
    if member_context is not None and not member_context['checkpoint'].get('protection_review'):
        if resume_id is not None:
            raise RuntimeError('PLANNED_RESUME_REVIEW_CHANGED')
        return

    def document(collection, key):
        result = _plan_document(cursor, p, collection, account, key)
        if result is None:
            raise RuntimeError("PLANNED_RESUME_PROOF_MISSING")
        return result

    spec = mock_automation_spec_from_dict(spec_doc)
    now = member_context['now'] if member_context is not None else now_provider()
    # The binding writer uses this same key, after the account lease/control
    # acquisition above. A newer binding cannot cross the flat CAS.
    if p == "%s" and member_context is None:
        cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"{spec.credential_profile_id}:kiwoom:mock",))
    if member_context is None:
        binding = _latest_account_binding_row(cursor, spec.credential_profile_id, "kiwoom", "mock", p)
        latest = _account_binding_row(binding) if binding else {}
        if ((latest.get("account_ref"), latest.get("binding_revision"), latest.get("verification_method"))
                != (account, spec.binding_revision, "ka00001")
                or not same_aware_instant(latest.get("verified_at", ""), spec.binding_verified_at)):
            raise RuntimeError("PLANNED_RESUME_BINDING_CHANGED")
        risk = mock_automation_risk_snapshot_from_dict(document("execution_mock_automation_current_risk", account))
        admission = mock_automation_admission_from_dict(document("execution_mock_automation_admission_by_spec", spec_id))
        recovery = mock_automation_recovery_decision_from_dict(document(
            "execution_mock_automation_current_recovery", admission.admission_id))
        occupied = (recovery.open_order_count or recovery.position_count or recovery.reserved_open_buy_won
                    or risk.owned_position_quantities or risk.pending_sell_quantities)
    else:
        risk, admission, recovery = (member_context[name] for name in ('risk', 'admission', 'recovery'))
        occupied = (recovery.own_position_quantities or recovery.own_open_order_ids
                    or risk.owned_for_member(spec_id) or risk.pending_sells_for_member(spec_id)
                    or any(record.member_spec_id == spec_id for record in member_context['active']))
    age = spec.maximum_data_gap_seconds
    if ((risk.account_ref, risk.execution_run_id, risk.binding_revision, risk.data_path)
            != (account, run_id, spec.binding_revision, spec.data_path)
            or (admission.account_ref, admission.execution_run_id, admission.spec_id) != (account, run_id, spec_id)
            or (recovery.account_ref, recovery.execution_run_id, recovery.spec_id) != (account, run_id, spec_id)
            or recovery.status is not MockAutomationRecoveryStatus.CLEARED_ORDERS_DISABLED
            or (recovery.risk_snapshot_id, recovery.risk_reconciliation_revision, recovery.account_as_of)
            != (risk.snapshot_id, risk.reconciliation_revision, risk.account_as_of)
            or occupied or not risk.reconciliation_complete
            or risk.submission_unknown_count or risk.balance_mismatch_count or age is None
            or risk.broker_query_started_at < datetime.fromisoformat(current["changed_at"])
            or any(not 0 <= (now - at).total_seconds() <= age for at in (risk.observed_at, risk.account_as_of))
            or member_context is None and _account_active_execution_rows(cursor, 'mock', account, 1, p)):
        raise RuntimeError("PLANNED_RESUME_NATIVE_FLAT_CHANGED")

    checkpoint = (member_context['checkpoint'] if member_context is not None else
                  _plan_document(cursor, p, "execution_mock_automation_runner_current", account, "current") or {})
    notice = checkpoint.get("protection_review")
    if not notice:
        if resume_id is not None:
            raise RuntimeError("PLANNED_RESUME_REVIEW_CHANGED")
        return
    if ((checkpoint.get("spec_id"), checkpoint.get("execution_run_id")) != (spec_id, run_id)
            or notice.get("state") != "RESOLVED_FLAT" or not resume_id
            or notice.get("resume_from_control_revision") != current["control_revision"]
            or (notice.get("resolved_risk_snapshot_id"), notice.get("resolved_risk_revision"))
            != (risk.snapshot_id, risk.reconciliation_revision)
            or notice.get("planned_review_resume_id") != resume_id or planned_review_resume_id(notice) != resume_id):
        raise RuntimeError("PLANNED_RESUME_REVIEW_CHANGED")
    if "planned_leader_protection_exit" not in notice:
        _require_planned_zero_fill_resume_root(cursor, p, checkpoint, notice, spec, run_id, risk)
        return
    workflow = notice["planned_leader_protection_exit"]
    plan, decision = read_planned_leader_protection_checkpoint(workflow, notice, spec, run_id)
    identity = mock_automation_intent_id(spec_id, decision.decision_id)
    row = _execution_intent_row(cursor, identity, p)
    sell = _json_document(row[0]) if row else {}
    claim = sell.get("mock_protection_claim") or {}
    head = document(PLAN_POSITION_COLLECTION, plan.plan_id)
    gate = mock_automation_decision_gate_from_dict(document("execution_mock_automation_approved_gates", identity))
    if ((sell.get("account_ref"), sell.get("run_id"), sell.get("environment"), sell.get("state"),
         sell.get("side"), sell.get("policy_version"), sell.get("plan"), sell.get("decision_id"))
            != (account, run_id, "mock", "FILLED", "SELL", PROTECTION_EXIT_VERSION,
                workflow["snapshot"]["plan"], decision.decision_id)
            or not sell.get("broker_order_id")
            or not decision.quantity == sell.get("quantity") == sell.get("filled_quantity")
            == sell.get("broker_reported_filled_quantity") == sell.get("detailed_filled_quantity")
            or gate.status is not MockAutomationGateStatus.APPROVED_FOR_SINGLE_SUBMISSION
            or (claim.get("workflow_id"), claim.get("gate_id"), gate.intent_id, gate.plan,
                gate.account_ref, gate.execution_run_id, gate.spec_id, gate.action)
            != (workflow["workflow_id"], gate.gate_id, identity, plan, account, run_id, spec_id, "EXIT")
            or (claim.get("risk_snapshot_id"), claim.get("risk_reconciliation_revision"))
            != (workflow["snapshot"]["risk_snapshot_id"], workflow["snapshot"]["risk_reconciliation_revision"])
            or (head.get("version"), head.get("plan_id"), head.get("policy_ref"), head.get("run_id"), head.get("symbol"))
            != ("mock_plan_position/v1", plan.plan_id, plan.policy_ref, run_id, decision.symbol)
            or head["quantity"] != 0 or head.get("unreconciled_sell_quantity")
            or not 0 < head["fill_cursor"] <= risk.execution_event_high_watermark
            or head["revision"] <= plan.expected_revision):
        raise RuntimeError("PLANNED_RESUME_TERMINAL_SELL_CHANGED")


def _require_planned_zero_fill_resume_root(cursor, p, checkpoint, notice, spec, run_id, risk):
    from kiwoom_monitor.application.mock_automation_execution import mock_automation_decision_gate_from_dict
    from kiwoom_monitor.application.mock_automation_risk import (
        is_planned_zero_fill_reconciliation_review, planned_zero_fill_root_receipt,
    )
    from kiwoom_monitor.infrastructure.persistence.execution_repository import _record_from_document

    account = spec.account_scope.account_ref
    pin = checkpoint.get("leader_position_proof") or {}
    identity = pin.get("entry_intent_id")
    if (not is_planned_zero_fill_reconciliation_review(notice) or not identity
            or (pin.get("account_ref"), pin.get("run_id"), pin.get("spec_id")) != (account, run_id, spec.spec_id)
            or _plan_document(cursor, p, "execution_leader_position_context", account, identity) != pin):
        raise RuntimeError("PLANNED_RESUME_ZERO_FILL_SCOPE_CHANGED")
    row = _execution_intent_row(cursor, identity, p)
    value = _json_document(row[0]) if row else {}
    gate_doc = _plan_document(cursor, p, "execution_mock_automation_approved_gates", account, identity) or {}
    head = _plan_document(cursor, p, PLAN_POSITION_COLLECTION, account, identity) or {}
    if (not value or not gate_doc or value.get("mock_plan_approval") != gate_doc.get("gate_id")
            or (head.get("version"), head.get("plan_id"), head.get("policy_ref"), head.get("run_id"), head.get("symbol"))
            != ("mock_plan_position/v1", identity, pin["proof_id"], run_id, value.get("symbol"))
            or head.get("quantity") != 0 or head.get("fill_cursor") != 0 or head.get("unreconciled_sell_quantity")
            or type(head.get("revision")) is not int or head["revision"] <= 1):
        raise RuntimeError("PLANNED_RESUME_ZERO_FILL_HEAD_CHANGED")
    cursor.execute(f"SELECT accepted_sequence,document_json FROM central_execution_events WHERE intent_id={p} "
                   "ORDER BY accepted_sequence", (identity,))
    history = [(sequence, _json_document(event)) for sequence, event in cursor.fetchall()]
    receipt = planned_zero_fill_root_receipt(
        _record_from_document(value), mock_automation_decision_gate_from_dict(gate_doc), pin,
        tuple(event for _, event in history), account_ref=account, run_id=run_id, spec_id=spec.spec_id,
    )
    terminal_sequence = next(sequence for sequence, event in history if event["event_id"] == receipt["terminal_event_id"])
    if receipt != notice.get("planned_zero_fill_root") or terminal_sequence > risk.execution_event_high_watermark:
        raise RuntimeError("PLANNED_RESUME_ZERO_FILL_RECEIPT_CHANGED")


def _save_plan_position(cursor, p, account, document, changed_at):
    cursor.execute(
        f"INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
        f"VALUES({p},{p},{p},{p},{p}) ON CONFLICT(collection,owner,document_key) DO UPDATE SET "
        "updated_at=excluded.updated_at,document_json=excluded.document_json",
        (PLAN_POSITION_COLLECTION, account, document["plan_id"], changed_at.timestamp(), _event_document(document)),
    )


def _require_plan_approval(cursor, p, value, ownership, now_provider, *, account_limits=None):
    from dataclasses import asdict

    from kiwoom_monitor.application.mock_automation_execution import (
        MOCK_AUTOMATION_DECISION_GATE_VERSION,
        MOCK_AUTOMATION_PLAN_ACTION_GATE_VERSION,
        MockAutomationGateStatus,
        mock_automation_decision_gate_from_dict,
        mock_automation_intent_id,
        mock_automation_order_policy_version,
    )
    from kiwoom_monitor.application.mock_automation_recovery import (
        MockAutomationAccountRecoveryDecision,
        MockAutomationRecoveryStatus,
        mock_account_recovery_fingerprint,
        mock_automation_recovery_decision_from_dict,
    )
    from kiwoom_monitor.application.mock_automation_risk import PROTECTION_EXIT_VERSION, mock_automation_risk_snapshot_from_dict
    from kiwoom_monitor.domain.execution_activation import forward_spec_from_dict, mock_automation_spec_from_dict, LEADER_NATIVE_PLAN_MODE
    from kiwoom_monitor.domain.order_contract import TERMINAL_ORDER_STATES

    recovery = ownership.get("plan_recovery") if ownership is not None else None
    if recovery is None:
        raise RuntimeError("PLANNED_EXECUTION_APPROVAL_REQUIRED")
    account_ref = value["account_ref"]
    management = ownership.get('_native_selected_management_context')
    context = management['context'] if management is not None else None
    if management is not None:
        if management['status'] != 'MANAGEMENT_READY' or value['side'] != 'SELL':
            raise RuntimeError('SELECTED_MANAGEMENT_SELL_ONLY')
        from .selected_order_bootstrap import selected_management_policy_document
    gate_doc = _plan_document(cursor, p, "execution_mock_automation_approved_gates", account_ref, value["intent_id"])
    if gate_doc is None:
        raise RuntimeError("PLANNED_EXECUTION_APPROVAL_REQUIRED")
    gate = mock_automation_decision_gate_from_dict(gate_doc)
    if (
        gate.plan is None
        or asdict(gate.plan) != value["plan"]
        or gate.status is not MockAutomationGateStatus.APPROVED_FOR_SINGLE_SUBMISSION
        or gate.version not in {MOCK_AUTOMATION_DECISION_GATE_VERSION, MOCK_AUTOMATION_PLAN_ACTION_GATE_VERSION}
        or gate.spec_id != ownership.get("active_spec_id")
        or gate.control_revision != ownership.get("control_revision")
        or (gate.account_ref, gate.execution_run_id, gate.strategy_decision_id,
            gate.intent_id, gate.symbol, gate.quantity, gate.limit_price) != (
            account_ref, value["run_id"], value["decision_id"], value["intent_id"],
            value["symbol"], value["quantity"], value["limit_price"])
        or value["intent_id"] != mock_automation_intent_id(gate.spec_id, gate.strategy_decision_id)
        or value["side"] != {"ENTER": "BUY", "ADD": "BUY", "REDUCE": "SELL", "EXIT": "SELL"}.get(gate.action)
        or value["order_type"] != "LIMIT"
        or gate.decided_at != datetime.fromisoformat(value["created_at"])
    ):
        raise RuntimeError("PLANNED_EXECUTION_APPROVAL_CHANGED")
    spec_doc = (selected_management_policy_document(cursor, p, 'execution_mock_automation_specs', account_ref,
                    gate.spec_id, management) if management is not None
                else _plan_document(cursor, p, 'execution_mock_automation_specs', account_ref, gate.spec_id))
    risk_doc = _plan_document(cursor, p, "execution_mock_automation_current_risk", account_ref, account_ref)
    recovery_doc = _plan_document(
        cursor, p, "execution_mock_automation_current_recovery", account_ref, gate.admission_id,
    )
    if spec_doc is None or risk_doc is None or recovery_doc is None:
        raise RuntimeError("PLANNED_EXECUTION_RISK_OR_RECOVERY_MISSING")
    spec = mock_automation_spec_from_dict(spec_doc)
    if management is None:
        profile_doc = _plan_document(cursor, p, 'execution_forward_profiles', spec.strategy_ref, spec.forward_profile_id)
        if profile_doc is None:
            raise RuntimeError('PLANNED_EXECUTION_FORWARD_PROFILE_MISSING')
        profile = forward_spec_from_dict(profile_doc)
        family = profile.family_version
    else:
        from kiwoom_monitor.application.mock_automation_candidate import candidate_package_from_dict
        from kiwoom_monitor.domain.execution_activation import CLOSE_NATIVE_PLAN_MODE, PANIC_NATIVE_PLAN_MODE
        package_doc = selected_management_policy_document(cursor, p,
            'execution_mock_automation_candidate_packages', spec.strategy_ref, spec.candidate_package_hash, management)
        if package_doc is None:
            raise RuntimeError('SELECTED_MANAGEMENT_ORIGINAL_PACKAGE_MISSING')
        package = candidate_package_from_dict(package_doc)
        family = package.candidate_spec['family']
        allowed = {LEADER_NATIVE_PLAN_MODE: {'leader_breakout/v1'},
                   CLOSE_NATIVE_PLAN_MODE: {'close_betting/v1', 'ipo_close/v1', 'ipo_breakout/v1'},
                   PANIC_NATIVE_PLAN_MODE: {'panic_rebound/v1'}}
        if (family not in allowed.get(spec.execution_plan_mode, set())
                or context.spec_id != spec.spec_id or context.admission_id != gate.admission_id
                or context.execution_run_id != value['run_id']
                or package.package_hash != spec.candidate_package_hash
                or value['plan']['plan_id'] != management['_policy_view']['source_plan_position']['plan_id']):
            raise RuntimeError('SELECTED_MANAGEMENT_ORIGINAL_POLICY_CHANGED')
        profile = None
    if (gate.version == MOCK_AUTOMATION_PLAN_ACTION_GATE_VERSION and (
            spec.execution_plan_mode != LEADER_NATIVE_PLAN_MODE
            or family != 'leader_breakout/v1' or gate.action not in {'ADD', 'REDUCE'})):
        raise RuntimeError('PLANNED_POSITION_ACTION_FAMILY_CHANGED')
    if gate.version == MOCK_AUTOMATION_PLAN_ACTION_GATE_VERSION:
        source = _plan_document(cursor, p, 'execution_leader_decisions', account_ref,
                                value['intent_id'] if ownership.get('_native_member_evidence') else value['decision_id']) or {}
        raw = (source.get('snapshot') or {}).get('feature_values', [{}])[0].get('value', {})
        if not raw.get('position_step') or raw.get('proposal') != gate.action:
            raise RuntimeError('PLANNED_POSITION_ACTION_EVIDENCE_MISSING')
    policy = mock_automation_order_policy_version(family, spec.session_profile)
    if policy == 'leader_execution_adapter/v1' and value['side'] == 'SELL' and ownership.get('protection_claim') is not None:
        policy = PROTECTION_EXIT_VERSION  # The ownership transaction already validated the typed proof.
    if (
        (profile is not None and
         (profile.profile_id, profile.account_ref, profile.strategy_ref, profile.environment, profile.session_profile)
         != (spec.forward_profile_id, account_ref, spec.strategy_ref, value['environment'], spec.session_profile))
        or value["policy_version"] != policy
    ):
        raise RuntimeError("PLANNED_EXECUTION_FORWARD_PROFILE_CHANGED")
    risk = mock_automation_risk_snapshot_from_dict(risk_doc)
    active = None
    if account_limits is not None:
        rows = _account_active_execution_rows(cursor, 'mock', account_ref, 401, p)
        if len(rows) > 400:
            raise RuntimeError('EXECUTION_ACCOUNT_OPEN_ORDERS_INCOMPLETE')
        active = [_json_document(row[0]) for row in rows]
        unknown = sum(row['state'] == 'SUBMISSION_UNKNOWN' for row in active)
        if unknown > account_limits.maximum_submission_unknown_count:
            raise RuntimeError('MOCK_ACCOUNT_SUBMISSION_UNKNOWN_LIMIT')
        if spec.maximum_submission_unknown_count is not None and unknown > spec.maximum_submission_unknown_count:
            raise RuntimeError('EXECUTION_PLAN_SUBMISSION_UNKNOWN_LIMIT')
        if (risk.submission_unknown_count > account_limits.maximum_submission_unknown_count
                or risk.balance_mismatch_count > account_limits.maximum_balance_mismatch_count
                or value['side'] == 'BUY' and (
                    risk.daily_net_pnl_won is None
                    or risk.daily_net_pnl_won <= -account_limits.maximum_daily_loss_won
                    or risk.data_gap_seconds is None
                    or risk.data_gap_seconds > account_limits.maximum_data_gap_seconds
                    or risk.reconnect_count > account_limits.maximum_reconnect_count)):
            raise RuntimeError('MOCK_ACCOUNT_RISK_LIMIT')
    current = mock_automation_recovery_decision_from_dict(recovery_doc)
    selection = ownership.get('_native_account_selection')
    if selection is not None and active is not None:
        _, projection = _retained_execution_member_projection(cursor, p, account_ref,
            intent_ids=tuple(row['intent_id'] for row in active),
            _selection=selection)
        active = [{**row, 'mock_account_member_spec_id': projection[row['intent_id']]}
                  if row['intent_id'] in projection else row for row in active]
    peer_orders = set()
    covered_buy_won = 0
    if selection is not None:
        if (not isinstance(current, MockAutomationAccountRecoveryDecision)
                or current.account_selection_id != selection['selection_id']
                or current.member_spec_ids != tuple(selection['member_spec_ids'])
                or gate.spec_id not in current.member_spec_ids
                or current.own_position_quantities != risk.owned_for_member(gate.spec_id)):
            raise RuntimeError('PLANNED_EXECUTION_ACCOUNT_RECOVERY_CHANGED')
        peer_orders = set(current.other_member_open_order_ids)
        covered_buy_won = _account_recovery_order_coverage(active, recovery, current, selection, gate.spec_id)
    now = kst_now() if now_provider is None else now_provider()
    max_age = (min(spec.maximum_data_gap_seconds, account_limits.maximum_data_gap_seconds)
               if account_limits is not None and spec.maximum_data_gap_seconds is not None
               else spec.maximum_data_gap_seconds)
    expiry = datetime.fromisoformat(value["expires_at"])
    fingerprint = mock_account_recovery_fingerprint(recovery)
    statuses = {MockAutomationRecoveryStatus.CLEARED_ORDERS_DISABLED}
    if value["side"] == "SELL" or (gate.version == MOCK_AUTOMATION_PLAN_ACTION_GATE_VERSION and gate.action == 'ADD'):
        statuses.add(MockAutomationRecoveryStatus.MANAGE_ONLY_ORDERS_DISABLED)
    if (
        spec.account_scope.account_ref != account_ref or spec.spec_id != gate.spec_id
        or spec.strategy_ref != gate.strategy_ref or value["venue"] != spec.supported_venue
        or risk.binding_revision != (context.binding_revision if context is not None else spec.binding_revision)
        or not risk.reconciliation_complete
        or (risk.account_ref, risk.execution_run_id) != (account_ref, value["run_id"])
        or (risk.snapshot_id, risk.reconciliation_revision) != (gate.risk_snapshot_id, gate.risk_reconciliation_revision)
        or (current.account_ref, current.execution_run_id, current.spec_id) != (account_ref, value["run_id"], gate.spec_id)
        or (current.decision_id, current.lease_receipt_id, current.recovery_fingerprint)
        != (gate.recovery_decision_id, gate.lease_receipt_id, fingerprint)
        or (current.risk_snapshot_id, current.risk_reconciliation_revision) != (risk.snapshot_id, risk.reconciliation_revision)
        or current.status not in statuses
        or (gate.recovery_fingerprint, gate.account_as_of) != (fingerprint, recovery.account.as_of)
        or risk.account_as_of != recovery.account.as_of or recovery.account.account_ref != account_ref
        or max_age is None or now >= expiry
        or expiry != gate.decided_at + timedelta(seconds=max(1, int(spec.maximum_data_gap_seconds)))
        or any(not 0 <= (now - at).total_seconds() <= max_age
               for at in (gate.decided_at, gate.server_now, risk.observed_at, risk.account_as_of))
        or any(order.state not in TERMINAL_ORDER_STATES and order.broker_order_id not in peer_orders
               for order in recovery.orders)
    ):
        raise RuntimeError("PLANNED_EXECUTION_RISK_OR_RECOVERY_CHANGED")
    event_type = "e.document_json->>'event_type'" if p == "%s" else "json_extract(e.document_json,'$.event_type')"
    financial_events = "'FILL','BROKER_FILL_AGGREGATE'"
    if selection is not None:
        financial_events += ",'AMENDMENT_STARTED','AMENDMENT_ACCEPTED','AMENDMENT_RECOVERED','AMENDMENT_RESPONSE_UNKNOWN'"
    cursor.execute(
        "SELECT 1 FROM central_execution_events e JOIN central_execution_intents i ON i.intent_id=e.intent_id "
        f"WHERE i.environment='mock' AND i.account_ref={p} AND e.accepted_sequence>{p} "
        f"AND {event_type} IN ({financial_events}) LIMIT 1",
        (account_ref, risk.execution_event_high_watermark),
    )
    if cursor.fetchone() is not None:
        raise RuntimeError("PLANNED_EXECUTION_RISK_OR_RECOVERY_CHANGED")
    return gate.gate_id, spec, gate.action, active, covered_buy_won


def _account_recovery_order_coverage(active, recovery, current, selection, spec_id):
    """Reproduce peer attribution from native physical orders in this transaction."""
    from kiwoom_monitor.infrastructure.persistence.execution_repository import _record_from_document, execution_broker_legs
    from kiwoom_monitor.domain.order_contract import TERMINAL_ORDER_STATES, OrderSide
    working = {order.broker_order_id: order for order in recovery.orders if order.state not in TERMINAL_ORDER_STATES}
    own, peers, covered = set(), set(), set()
    reserved = 0
    for row in active:
        record = _record_from_document(row)
        for leg in execution_broker_legs(record):
            snapshot = working.get(leg.broker_order_id)
            if snapshot is None:
                continue  # A later native reservation remains additional cash usage.
            member = getattr(record, 'member_spec_id', None)
            if (member not in selection['member_spec_ids'] or record.intent.run_id != current.execution_run_id
                    or record.intent.account_ref != current.account_ref
                    or record.state.value not in {'ACCEPTED', 'PARTIALLY_FILLED'}
                    or record.amendment is not None and record.amendment.reconciliation_pending
                    or leg.state in TERMINAL_ORDER_STATES or leg.broker_order_id in covered
                    or (record.intent.symbol, leg.remaining_quantity, leg.detailed_filled_quantity,
                        leg.broker_reported_filled_quantity)
                    != (snapshot.symbol, snapshot.remaining_quantity, snapshot.filled_quantity, snapshot.filled_quantity)):
                raise RuntimeError('PLANNED_EXECUTION_ACCOUNT_ORDER_COVERAGE_CHANGED')
            covered.add(leg.broker_order_id)
            (own if member == spec_id else peers).add(leg.broker_order_id)
            if record.intent.side is OrderSide.BUY:
                reserved += leg.remaining_quantity * leg.limit_price
    if (covered != set(working) or own != set(current.own_open_order_ids)
            or peers != set(current.other_member_open_order_ids) or reserved != recovery.account.reserved_open_buy_won):
        raise RuntimeError('PLANNED_EXECUTION_ACCOUNT_ORDER_COVERAGE_CHANGED')
    return reserved


def _claim_plan_position(cursor, p, value, ownership, now_provider, *, control=None):
    ownership = _native_execution_scope(ownership, control or {})
    account_ref, ref = value["account_ref"], value["plan"]
    from kiwoom_monitor.application.mock_automation_admission import (
        MOCK_AUTOMATION_ACCOUNT_CONTROL_VERSION, mock_automation_control_from_dict,
    )
    selection = (mock_automation_control_from_dict(control).account_selection
                 if control and control.get('version') == MOCK_AUTOMATION_ACCOUNT_CONTROL_VERSION else None)
    gate_id, spec, action, active, covered = _require_plan_approval(cursor, p, value, ownership, now_provider,
        account_limits=selection.limits if selection is not None else None)
    head = _plan_document(cursor, p, PLAN_POSITION_COLLECTION, account_ref, ref["plan_id"])
    if head is None:
        head = {
            "version": "mock_plan_position/v1", "plan_id": ref["plan_id"], "policy_ref": ref["policy_ref"],
            "run_id": value["run_id"], "symbol": value["symbol"],
            "revision": 0, "quantity": 0, "fill_cursor": 0,
            **({'spec_id': spec.spec_id, 'capital_lots': []} if selection is not None else {}),
        }
    if (head["policy_ref"], head["run_id"], head["symbol"], head["revision"]) != (
        ref["policy_ref"], value["run_id"], value["symbol"], ref["expected_revision"],
    ):
        raise RuntimeError("EXECUTION_PLAN_REVISION_CHANGED")
    if head.get("unreconciled_sell_quantity"):
        raise RuntimeError("EXECUTION_PLAN_RECONCILIATION_REQUIRED")
    scoped_head = (_retained_plan_position_view(cursor, p, selection, head)
                   if selection is not None else head)
    if selection is not None and scoped_head.get('spec_id') != spec.spec_id:
        raise RuntimeError('EXECUTION_PLAN_MEMBER_MISMATCH')
    if active is None:
        rows = _account_active_execution_rows(cursor, "mock", account_ref, 401, p)
        if len(rows) > 400:
            raise RuntimeError("EXECUTION_ACCOUNT_OPEN_ORDERS_INCOMPLETE")
        active = [_json_document(row[0]) for row in rows]
    if (spec.maximum_submission_unknown_count is not None
            and sum(row["state"] == "SUBMISSION_UNKNOWN" for row in active) > spec.maximum_submission_unknown_count):
        raise RuntimeError("EXECUTION_PLAN_SUBMISSION_UNKNOWN_LIMIT")
    account = ownership["plan_recovery"].account
    if value["side"] == "BUY":
        if selection is None and any(account.positions.values()) and action != 'ADD':
            raise RuntimeError("PLANNED_ENTRY_REQUIRES_FLAT_ACCOUNT")
        if action == 'ADD' and (head['quantity'] <= 0 or account.positions.get(value['symbol'], 0) < head['quantity']):
            raise RuntimeError('PLANNED_ADDITIONAL_ENTRY_REQUIRES_OWNED_POSITION')
        if any(not row.get("plan") or row["run_id"] != value["run_id"] for row in active):
            raise RuntimeError("PLANNED_EXECUTION_UNVERIFIED_OPEN_ORDER")
        buys = [row for row in active if row["side"] == "BUY"]
        if any(row.get("limit_price") is None for row in buys):
            raise RuntimeError("EXECUTION_ACCOUNT_RESERVATION_UNKNOWN")
        reserved = sum(_planned_buy_reservation(row) if selection is not None
                       else _unfilled_quantity(row) * row['limit_price'] for row in buys)
        required = value["quantity"] * value["limit_price"]
        free = account.available_cash_won - account.reserved_open_buy_won - (reserved - covered)
        capital_limit = spec.maximum_capital_won
        member_committed = reserved
        if selection is not None:
            member_committed = _claim_account_plan_capacity(cursor, p, value, spec, selection, active)
        if required > free or (capital_limit is not None and member_committed + required > capital_limit):
            raise RuntimeError("EXECUTION_PLAN_CASH_UNAVAILABLE")
    else:
        sells = [row for row in active if row["side"] == "SELL" and row["symbol"] == value["symbol"]]
        pending = sum(_unfilled_quantity(row) for row in sells)
        plan_pending = sum(
            _unfilled_quantity(row) for row in sells
            if (row.get("plan") or {}).get("plan_id") == ref["plan_id"]
        )
        if value["quantity"] > min(head["quantity"] - plan_pending, account.positions.get(value["symbol"], 0) - pending):
            raise RuntimeError("EXECUTION_PLAN_QUANTITY_UNAVAILABLE")
    _save_plan_position(
        cursor, p, account_ref, {**head, "revision": head["revision"] + 1},
        datetime.fromisoformat(value["created_at"]),
    )
    management = ownership.get('_native_selected_management_context')
    member = (management['context'].member_spec_id if management is not None
              else spec.spec_id if selection is not None else None)
    return {**value, "mock_plan_approval": gate_id,
            **({'mock_account_member_spec_id': member} if member is not None else {})}


def _retained_plan_position_view(cursor, p, selection, head, *, documents=None):
    """Validate legacy ownership and FIFO without rewriting the mutable head.

    Only current occupied/reserved plans need this transaction-local view. The
    plan ledger and original approvals remain the source; no fabricated lots or
    same-run attribution is accepted.
    """
    from kiwoom_monitor.domain.execution_activation import MockAutomationAccountSelectionV2
    from kiwoom_monitor.infrastructure.persistence.execution_repository import _record_from_document, execution_broker_legs

    if (head.get('spec_id') is not None or 'capital_lots' in head
            or not isinstance(selection, MockAutomationAccountSelectionV2)):
        return head
    account, run_id = selection.account_scope.account_ref, head['run_id']
    orders, pages = _leader_plan_ledger(cursor, p, account, run_id, head['plan_id'])
    if not orders or head.get('unreconciled_sell_quantity'):
        raise RuntimeError('MOCK_RETAINED_PLAN_UNCONFIRMED')
    cache = {} if documents is None else documents
    projection = {}
    identities = tuple(orders)
    # Reuse the existing projection read-page budget, without imposing a new
    # lifetime order-count limit on a plan.
    for offset in range(0, len(identities), 400):
        projection.update(_retained_projection_rows(cursor, p, account, selection, run_id,
            identities[offset:offset + 400], cache))
    members = set(projection.values())
    if set(projection) != set(orders) or len(members) != 1:
        raise RuntimeError('MOCK_RETAINED_PLAN_OWNER_UNCONFIRMED')
    fills = []
    for identity, document in orders.items():
        record = _record_from_document(document)
        intent = record.intent
        if ((intent.account_ref, intent.run_id, intent.symbol, intent.plan.plan_id, intent.plan.policy_ref)
                != (account, run_id, head['symbol'], head['plan_id'], head['policy_ref'])
                or record.filled_quantity != record.detailed_filled_quantity
                or record.broker_reported_filled_quantity != record.detailed_filled_quantity
                or any(leg.broker_reported_filled_quantity != leg.detailed_filled_quantity
                       for leg in execution_broker_legs(record))):
            raise RuntimeError('MOCK_RETAINED_PLAN_DETAILS_UNCONFIRMED')
        detailed = [(sequence, event) for sequence, event in pages[identity] if event['event_type'] == 'FILL']
        if sum(event['quantity'] for _, event in detailed) != record.detailed_filled_quantity:
            raise RuntimeError('MOCK_RETAINED_PLAN_DETAILS_UNCONFIRMED')
        fills.extend((sequence, intent.side.value, event) for sequence, event in detailed)
    lots, seen, last_fill = [], set(), 0
    for sequence, side, event in sorted(fills, key=lambda item: item[0]):
        identity = (event['intent_id'], event['broker_order_id'], event['broker_execution_id'])
        if (identity in seen or not identity[1] or not identity[2]
                or type(event['quantity']) is not int or event['quantity'] <= 0
                or type(event['price']) is not int or event['price'] <= 0):
            raise RuntimeError('MOCK_RETAINED_PLAN_FILL_UNCONFIRMED')
        seen.add(identity)
        if side == 'BUY':
            lots.append([event['quantity'], event['price']])
        else:
            remaining = event['quantity']
            while remaining and lots:
                taken = min(remaining, lots[0][0])
                remaining -= taken
                lots[0][0] -= taken
                if not lots[0][0]:
                    lots.pop(0)
            if remaining:
                raise RuntimeError('MOCK_RETAINED_PLAN_NEGATIVE_POSITION')
        last_fill = sequence
    if (sum(quantity for quantity, _ in lots), last_fill) != (head['quantity'], head['fill_cursor']):
        raise RuntimeError('MOCK_RETAINED_PLAN_FILL_CUT_CHANGED')
    return {**head, 'spec_id': next(iter(members)), 'capital_lots': lots}


def _claim_account_plan_capacity(cursor, p, value, spec, selection, active):
    """Check actual open FIFO basis plus durable reservations under the account lease lock."""
    account, run_id = value['account_ref'], value['run_id']
    buys = [row for row in active if row['side'] == 'BUY']
    active_ids = sorted({row['plan']['plan_id'] for row in buys})
    run_expr = "document_json->>'run_id'" if p == '%s' else "json_extract(document_json,'$.run_id')"
    quantity_expr = "(document_json->>'quantity')::bigint" if p == '%s' else "json_extract(document_json,'$.quantity')"
    pending = (' OR document_key IN (' + ','.join([p] * len(active_ids)) + ')') if active_ids else ''
    cursor.execute(f'SELECT document_json FROM central_documents WHERE collection={p} AND owner={p} '
        f'AND {run_expr}={p} AND ({quantity_expr}>0{pending}) ORDER BY document_key LIMIT {p}',
        (PLAN_POSITION_COLLECTION, account, run_id, *active_ids, selection.limits.maximum_concurrent_positions + 1))
    heads = {head['plan_id']: head for row in cursor.fetchall() for head in [_json_document(row[0])]}
    if len(heads) > selection.limits.maximum_concurrent_positions:
        raise RuntimeError('MOCK_ACCOUNT_POSITION_LIMIT')
    if any(identity not in heads for identity in active_ids):
        raise RuntimeError('MOCK_ACCOUNT_PLAN_RESERVATION_UNVERIFIED')
    documents = {}
    heads = {identity: _retained_plan_position_view(cursor, p, selection, head, documents=documents)
             for identity, head in heads.items()}
    basis = member_basis = 0
    for head in heads.values():
        lots = head.get('capital_lots')
        if (head.get('spec_id') not in selection.member_spec_ids or lots is None
                or head.get('unreconciled_sell_quantity') or any(
                    len(lot) != 2 or type(lot[0]) is not int or type(lot[1]) is not int
                    or lot[0] <= 0 or lot[1] <= 0 for lot in lots)
                or sum(lot[0] for lot in lots) != head['quantity']):
            raise RuntimeError('MOCK_ACCOUNT_PLAN_BASIS_UNVERIFIED')
        cost = sum(quantity * price for quantity, price in lots)
        basis += cost
        if head['spec_id'] == spec.spec_id:
            member_basis += cost
    plan_ids = {*heads, value['plan']['plan_id']}
    members = {head['spec_id'] for head in heads.values()} | {spec.spec_id}
    if len(plan_ids) > selection.limits.maximum_concurrent_positions:
        raise RuntimeError('MOCK_ACCOUNT_POSITION_LIMIT')
    if len(members) > selection.limits.maximum_concurrent_strategies:
        raise RuntimeError('MOCK_ACCOUNT_STRATEGY_LIMIT')
    reserved = sum(_planned_buy_reservation(row) for row in buys)
    required = value['quantity'] * value['limit_price']
    if basis + reserved + required > selection.limits.maximum_capital_won:
        raise RuntimeError('MOCK_ACCOUNT_CAPITAL_UNAVAILABLE')
    return member_basis + sum(_planned_buy_reservation(row) for row in buys
                              if heads[row['plan']['plan_id']]['spec_id'] == spec.spec_id)


def _planned_buy_reservation(row):
    from kiwoom_monitor.infrastructure.persistence.execution_repository import _record_from_document, execution_broker_legs
    from kiwoom_monitor.domain.order_contract import TERMINAL_ORDER_STATES
    record = _record_from_document(row)
    if record.amendment is not None and record.amendment.reconciliation_pending:
        raise RuntimeError('MOCK_ACCOUNT_AMENDMENT_RECONCILIATION_REQUIRED')
    legs = execution_broker_legs(record)
    return (sum(leg.remaining_quantity * leg.limit_price for leg in legs if leg.state not in TERMINAL_ORDER_STATES)
            if legs else _unfilled_quantity(row) * row['limit_price'])


def _unfilled_quantity(order):
    return max(0, order["quantity"] - order.get("detailed_filled_quantity", order.get("filled_quantity", 0)))


def _plan_event_changes_position(event):
    return event["event_type"] == "FILL" or event["state"] in {"CANCELLED", "REJECTED"}


def _validate_plan_event(stored, intent, event):
    if stored is None or event["intent_id"] != intent["intent_id"]:
        raise RuntimeError("EXECUTION_PLAN_EVENT_SCOPE_CHANGED")
    if (intent.get('mock_account_member_spec_id') is not None
            and intent['mock_account_member_spec_id'] != stored.get('mock_account_member_spec_id')):
        raise RuntimeError('EXECUTION_PLAN_MEMBER_ATTRIBUTION_CHANGED')
    fields = ("plan", "decision_id", "symbol", "venue", "side", "quantity", "order_type", "limit_price", "policy_version")
    if (any(intent[name] != stored[name] for name in fields) or intent["state"] != event["state"]
            or (intent["state"] == "QUEUED" and stored["state"] != "QUEUED")):
        raise RuntimeError("EXECUTION_PLAN_EVENT_SCOPE_CHANGED")
    if event["event_type"] == "SUBMISSION_STARTED" and stored["state"] != "QUEUED":
        raise RuntimeError("EXECUTION_PLAN_ALREADY_SUBMITTED")
    if event["event_type"] in {"PLANNED_UNSENT_CANCELLED", "PLANNED_UNSENT_EXPIRED"}:
        if (stored["state"] != "QUEUED" or intent["state"] != "REJECTED"
                or stored["broker_order_id"] or event["broker_order_id"] or stored["fill_ids"]
                or stored["filled_quantity"] or stored["detailed_filled_quantity"]
                or stored["broker_reported_filled_quantity"]
                or (event["event_type"] == "PLANNED_UNSENT_EXPIRED"
                    and datetime.fromisoformat(event["received_at"]) < datetime.fromisoformat(stored["expires_at"]))):
            raise RuntimeError("EXECUTION_PLAN_UNSENT_RELEASE_CHANGED")
    if (event["event_type"] in {"FILL", "BROKER_FILL_AGGREGATE", "BROKER_RECONCILED"}
            and not stored["broker_order_id"] and stored["state"] != "SUBMISSION_UNKNOWN"):
        raise RuntimeError("EXECUTION_PLAN_BROKER_ORDER_UNCONFIRMED")
    if event["event_type"] != "FILL":
        return True
    fill_id, quantity, price = event["broker_execution_id"], event["quantity"], event["price"]
    physical = next((leg for leg in stored.get("broker_legs", ())
                     if leg["broker_order_id"] == event["broker_order_id"]), None)
    if fill_id in (physical["fill_ids"] if physical is not None else stored["fill_ids"]):
        return False
    if (not fill_id or type(quantity) is not int or quantity <= 0 or type(price) is not int or price <= 0
            or intent["fill_ids"] != [*stored["fill_ids"], fill_id]
            or intent["detailed_filled_quantity"] != stored["detailed_filled_quantity"] + quantity
            or intent["detailed_filled_quantity"] > stored["quantity"]):
        raise RuntimeError("EXECUTION_PLAN_FILL_EVIDENCE_CHANGED")
    return True


def _validate_broker_ledger_write(cursor, p, stored, intent, event, ownership, now_provider, control_facts):
    sor = intent.get('venue') == 'SOR'
    if not intent.get('broker_legs') and not sor:
        return stored
    from kiwoom_monitor.infrastructure.persistence.execution_repository import validate_broker_ledger_event

    if stored is None:
        cursor.execute(f"SELECT document_json FROM central_execution_intents WHERE intent_id={p}"
                       + (" FOR UPDATE" if p == "%s" else ""), (intent["intent_id"],))
        row = cursor.fetchone()
        if row is None:
            raise KeyError(f"unknown execution intent: {intent['intent_id']}")
        stored = _json_document(row[0])
    if sor:
        before, after = stored.get('broker_venue'), intent.get('broker_venue')
        kind = event['event_type']
        if (stored.get('venue') != 'SOR' or after not in {None, 'KRX', 'NXT'}
                or (before != after and (before is not None or stored['state'] != 'SUBMISSION_UNKNOWN'
                    or kind not in {'ORDER_ACCEPTED', 'SOR_VENUE_CONFIRMED'}
                    or not event['broker_order_id'] or event['broker_order_id'] != intent['broker_order_id']
                    or event.get('reason') != f'broker_venue={after}'))
                or (kind == 'SOR_VENUE_CONFIRMED' and (before == after or not event['broker_as_of']
                    or event['broker_order_id'] != stored['broker_order_id']))
                or (after is None and (kind in {'FILL', 'BROKER_FILL_AGGREGATE', 'BROKER_RECONCILED', 'CANCEL_STARTED'}
                    or intent.get('broker_legs')))):
            raise RuntimeError('SOR_BROKER_VENUE_EVIDENCE_CHANGED')
    if not intent.get('broker_legs'):
        return stored
    authorized = None
    if event['event_type'] == 'AMENDMENT_STARTED' and (ownership or {}).get('amendment_claim') is not None:
        authorized = _require_minute_order_management_claim(cursor, p, stored, intent, event, ownership,
                                                            now_provider, control_facts)
    validate_broker_ledger_event(stored, intent, event, authorized_assessment=authorized,
        recovery=(ownership or {}).get("amendment_recovery"),
        now=(kst_now() if now_provider is None else now_provider()) if event["event_type"] == "AMENDMENT_RECOVERED" else None,
        rehearsal=intent.get('environment') == 'real')
    return stored


def _verified_native_order_management_peers(cursor, p, record, recovery, risk, control, kind):
    from kiwoom_monitor.application.minute_order_management import verified_current_order_peers
    from kiwoom_monitor.infrastructure.persistence.execution_repository import _record_from_document, project_execution_record
    rows = _account_active_execution_rows(cursor, 'mock', record.intent.account_ref, 401, p)
    if len(rows) > 400:
        raise RuntimeError(f'{kind}_CURRENT_ORDER_ACCOUNT_COVERAGE_CHANGED')
    raw = tuple(_json_document(row[0]) for row in rows)
    management = control.get('_native_selected_management_context') if control else None
    if management is not None:
        try:
            return verified_current_order_peers(record, recovery, risk,
                tuple(_record_from_document(value) for value in raw), control,
                selected_management_context=management['context'], selected_management_policy=management['_policy_view'])
        except (ValueError, TypeError, KeyError) as error:
            raise RuntimeError(f'{kind}_CURRENT_ORDER_ACCOUNT_COVERAGE_CHANGED') from error
    selected = control.get('account_selection') if control else None
    _, projection = _retained_execution_member_projection(cursor, p, record.intent.account_ref,
        intent_ids=tuple(value['intent_id'] for value in raw),
        _selection=selected) if selected is not None else (None, {})
    projected = project_execution_record(record, projection)
    try:
        result = verified_current_order_peers(projected, recovery, risk,
            tuple(project_execution_record(_record_from_document(value), projection) for value in raw), control)
        return ({**result, 'legacy_evidence_scope': True} if record.intent.intent_id in projection else result)
    except (ValueError, TypeError, KeyError) as error:
        raise RuntimeError(f'{kind}_CURRENT_ORDER_ACCOUNT_COVERAGE_CHANGED') from error


def _selected_management_original_claim_view(cursor, p, stored, ownership):
    """Supply an original source marker only in this validated claim's view."""
    management = (ownership or {}).get('_native_selected_management_context')
    if management is None or stored is None or stored.get('mock_plan_approval'):
        return stored
    context = management['context']
    source_ids = management['_policy_view']['source_intent_ids']
    if stored.get('intent_id') not in source_ids:
        return stored  # New SELLs obtain their own current native marker.
    from dataclasses import asdict
    from kiwoom_monitor.application.mock_automation_execution import (
        mock_automation_decision_gate_from_dict, MockAutomationGateStatus)
    document = _plan_document(cursor, p, 'execution_mock_automation_approved_gates',
        context.account_ref, stored['intent_id'], selected_management=management)
    gate = mock_automation_decision_gate_from_dict(document) if document is not None else None
    if (gate is None or gate.status is not MockAutomationGateStatus.APPROVED_FOR_SINGLE_SUBMISSION
            or (gate.intent_id, gate.spec_id, gate.admission_id, gate.account_ref, gate.execution_run_id,
                gate.strategy_decision_id, gate.symbol, gate.quantity, gate.limit_price)
            != (stored['intent_id'], context.spec_id, context.admission_id, context.account_ref,
                context.execution_run_id, stored['decision_id'], stored['symbol'], stored['quantity'], stored['limit_price'])
            or gate.plan is None or asdict(gate.plan) != stored.get('plan')):
        raise RuntimeError('SELECTED_MANAGEMENT_ORIGINAL_APPROVAL_CHANGED')
    return {**stored, 'mock_plan_approval': gate.gate_id}


def _require_minute_order_management_claim(cursor, p, stored, value, event, ownership, now_provider, control):
    ownership = _native_execution_scope(ownership, control or {}) if ownership is not None else None
    stored = _selected_management_original_claim_view(cursor, p, stored, ownership)
    from kiwoom_monitor.domain.execution_activation import (
        CLOSE_ORDER_MANAGEMENT_VERSION, PANIC_ORDER_MANAGEMENT_VERSION, LEADER_ORDER_MANAGEMENT_VERSION,
    )
    from kiwoom_monitor.application.minute_order_management import minute_current_shadow_matches
    from kiwoom_monitor.application.mock_automation_execution import mock_automation_intent_id
    from kiwoom_monitor.application.mock_automation_risk import mock_automation_risk_snapshot_from_dict
    from kiwoom_monitor.domain.order_contract import OrderPlanRef
    from kiwoom_monitor.infrastructure.persistence.execution_repository import _record_from_document, plan_position_from_document

    claim = (ownership or {}).get('amendment_claim' if event['event_type'] == 'AMENDMENT_STARTED' else 'planned_cancel_claim')
    if isinstance(claim, dict) and claim.get('version') == LEADER_ORDER_MANAGEMENT_VERSION:
        return _require_leader_order_management_claim(cursor, p, stored, value, event, ownership, now_provider, control)
    recovery = (ownership or {}).get('plan_recovery')
    panic = isinstance(claim, dict) and claim.get('version') == PANIC_ORDER_MANAGEMENT_VERSION
    kind = 'PANIC' if panic else 'CLOSE'
    version = PANIC_ORDER_MANAGEMENT_VERSION if panic else CLOSE_ORDER_MANAGEMENT_VERSION
    if panic:
        from kiwoom_monitor.application.panic_mock_execution import (
            PANIC_EXIT_COLLECTION as exit_collection, assess_panic_order_management as assess, panic_exit_key as exit_key,
        )
    else:
        from kiwoom_monitor.application.close_mock_execution import (
            assess_close_order_management as assess,
        )
    if (stored is None or not stored.get('plan') or not stored.get('mock_plan_approval')
            or not isinstance(claim, dict) or claim.get('version') != version
            or recovery is None or not control or ownership.get('control_revision') is None):
        raise RuntimeError(f'{kind}_CURRENT_ORDER_CLAIM_REQUIRED')
    account = value['account_ref']
    management = ownership.get('_native_selected_management_context')
    spec, package, entry, candidate = _minute_native_documents(cursor, p, account, ownership['active_spec_id'],
        stored['plan']['plan_id'], kind=kind, selected_management=management)
    approval = _plan_document(cursor, p, 'execution_mock_automation_approved_gates', account, stored['intent_id'],
                              selected_management=management)
    risk_doc = _plan_document(cursor, p, 'execution_mock_automation_current_risk', account, account)
    head_doc = _plan_document(cursor, p, PLAN_POSITION_COLLECTION, account, stored['plan']['plan_id'])
    if (approval is None or approval['gate_id'] != stored['mock_plan_approval']
            or approval['spec_id'] != spec.spec_id or approval.get('plan') != stored['plan']
            or risk_doc is None or head_doc is None):
        raise RuntimeError(f'{kind}_CURRENT_ORDER_ORIGINAL_APPROVAL_CHANGED')
    record = _record_from_document(stored)
    plan = OrderPlanRef(**claim['plan'])
    head = plan_position_from_document(head_doc, stored['run_id'], stored['symbol'], plan)
    if head.plan != plan:
        raise RuntimeError(f'{kind}_CURRENT_ORDER_PLAN_REVISION_CHANGED')
    settings = _plan_document(cursor, p, 'server_operational_settings', 'global', 'current') if stored['side'] == 'BUY' else None
    enabled = (stored['side'] == 'BUY' and control.get('desired_state') == 'RUNNING'
               and not control.get('entry_resume_required', False) and _control_entry_enabled(control, spec.spec_id)
               and minute_current_shadow_matches(spec, candidate, settings))
    root_id = mock_automation_intent_id(spec.spec_id, entry['decision']['decision_id'])
    root = record
    if stored['side'] == 'SELL':
        row = _execution_intent_row(cursor, root_id, p)
        root = _record_from_document(_json_document(row[0])) if row else None
    notice = claim.get('protection_notice')
    risk = mock_automation_risk_snapshot_from_dict(risk_doc)
    peers = _verified_native_order_management_peers(cursor, p, record, recovery, risk, control, kind)
    exit_evidence = None
    if stored['side'] == 'SELL':
        if panic:
            exit_evidence = _plan_document(cursor, p, exit_collection, account, exit_key(record.intent.plan))
        else:
            exit_evidence = _close_exit_for_intent(cursor, p, account, record.intent.plan, record.intent.decision_id,
                                                  selected_management=management)
    panic_trigger = claim.get('panic_trigger')
    if panic and panic_trigger is not None:
        _require_panic_trigger_checkpoint(cursor, p, account, spec, stored['run_id'], candidate, panic_trigger, checkpoint_key=ownership.get('_native_checkpoint_key', 'current'))
    assessed = assess(spec, run_id=stored['run_id'], parameters=package.candidate_spec['parameters'],
        event=candidate, entry_evidence=entry, record=record, head=head,
        order_events=[_json_document(row[0]) for row in _execution_event_rows(cursor, stored['intent_id'], p)],
        recovery=recovery, risk=risk, quote=claim['quote'], decided_at=datetime.fromisoformat(claim['decided_at']),
        verified_peer_orders=peers,
        now=kst_now() if now_provider is None else now_provider(), entry_enabled=enabled, root=root,
        exit_evidence=exit_evidence, **(dict(panic_trigger=panic_trigger) if panic else dict(protection_notice=notice,
        root_fill_events=[_json_document(row[0]) for row in _execution_event_rows(cursor, root_id, p)] if notice is not None else (),
        stop_risk=_close_native_stop_risk(cursor, p, account, spec, notice, root_id,
            checkpoint_key=ownership.get('_native_checkpoint_key', 'current'),
            selected_management=management) if notice is not None else None,
        **_selected_management_close_terms(ownership))))
    expected_action = 'AMEND' if event['event_type'] == 'AMENDMENT_STARTED' else 'CANCEL'
    if (assessed != claim or claim['action'] != expected_action
            or event['broker_order_id'] != claim['broker_order_id']
            or (expected_action == 'AMEND' and (event['quantity'], event['price']) != (claim['quantity'], claim['limit_price']))):
        raise RuntimeError(f'{kind}_CURRENT_ORDER_ASSESSMENT_CHANGED')
    return assessed


def _require_leader_order_management_claim(cursor, p, stored, value, event, ownership, now_provider, control):
    """Validate the current leader remainder in the existing AMEND/CANCEL transaction."""
    from kiwoom_monitor.application.leader_breakout import (
        LeaderBreakoutConfig, leader_current_entry_matches,
    )
    from kiwoom_monitor.application.mock_automation_candidate import candidate_package_from_dict
    from kiwoom_monitor.application.mock_automation_risk import mock_automation_risk_snapshot_from_dict
    from kiwoom_monitor.domain.execution_activation import mock_automation_spec_from_dict
    from kiwoom_monitor.domain.order_contract import OrderPlanRef
    from kiwoom_monitor.infrastructure.persistence.execution_repository import _record_from_document, plan_position_from_document

    from kiwoom_monitor.application.leader_order_management import assess_leader_order_management

    claim = ownership.get('amendment_claim' if event['event_type'] == 'AMENDMENT_STARTED' else 'planned_cancel_claim')
    account = value['account_ref']
    management = ownership.get('_native_selected_management_context')
    def document(collection, key, owner=account):
        result = _plan_document(cursor, p, collection, owner, key, selected_management=management)
        if result is None:
            raise RuntimeError('LEADER_CURRENT_ORDER_EVIDENCE_MISSING')
        return result
    if (stored is None or not stored.get('plan') or not stored.get('mock_plan_approval')
            or ownership.get('plan_recovery') is None or not control or ownership.get('control_revision') is None):
        raise RuntimeError('LEADER_CURRENT_ORDER_CLAIM_REQUIRED')
    spec = mock_automation_spec_from_dict(document('execution_mock_automation_specs', ownership['active_spec_id']))
    package = candidate_package_from_dict(document('execution_mock_automation_candidate_packages', spec.candidate_package_hash, spec.strategy_ref))
    if package.package_hash != spec.candidate_package_hash or package.candidate_spec['family'] != 'leader_breakout/v1':
        raise RuntimeError('LEADER_CURRENT_ORDER_PACKAGE_CHANGED')
    config = LeaderBreakoutConfig.from_dict(package.candidate_spec['parameters'])
    approval = document('execution_mock_automation_approved_gates', stored['intent_id'])
    if (approval['gate_id'] != stored['mock_plan_approval'] or approval['spec_id'] != spec.spec_id
            or approval.get('plan') != stored['plan'] or approval['intent_id'] != stored['intent_id']
            or (approval['execution_run_id'], approval['account_ref'], approval['strategy_decision_id'],
                approval['symbol'], approval['quantity'], approval['limit_price'])
            != (stored['run_id'], account, stored['decision_id'], stored['symbol'], stored['quantity'], stored['limit_price'])):
        raise RuntimeError('LEADER_CURRENT_ORDER_ORIGINAL_APPROVAL_CHANGED')
    record = _record_from_document(stored)
    plan = OrderPlanRef(**claim['plan'])
    head = plan_position_from_document(document(PLAN_POSITION_COLLECTION, stored['plan']['plan_id']), stored['run_id'], stored['symbol'], plan)
    if head.plan != plan:
        raise RuntimeError('LEADER_CURRENT_ORDER_PLAN_REVISION_CHANGED')
    pin = document('execution_leader_position_context', stored['plan']['plan_id'])
    now = kst_now() if now_provider is None else now_provider()
    settings = (document('server_operational_settings', 'current', 'global')
                if stored['side'] == 'BUY' and management is None else None)
    enabled = (stored['side'] == 'BUY' and control.get('desired_state') == 'RUNNING'
        and not control.get('entry_resume_required', False) and _control_entry_enabled(control, spec.spec_id)
        and leader_current_entry_matches(config, pin, settings, now))
    notice = claim.get('protection_notice')
    stop_risk = None
    plan_entry_notional = None
    staged_orders = staged_pages = None
    if hasattr(config.execution_contract, 'position_steps') and stored['side'] == 'BUY':
        staged_orders, staged_pages = _leader_plan_ledger(cursor, p, account, stored['run_id'], stored['plan']['plan_id'])
        plan_entry_notional = sum(row['quantity'] * row['price'] for identity, events in staged_pages.items()
            if staged_orders[identity]['side'] == 'BUY' for _, row in events if row['event_type'] == 'FILL')
    if notice is not None or stored['policy_version'] == 'mock-protection-exit/v1':
        checkpoint = document('execution_mock_automation_runner_current', ownership.get('_native_checkpoint_key', 'current'))
        if ((checkpoint.get('spec_id'), checkpoint.get('execution_run_id'), checkpoint.get('leader_position_proof'))
                != (spec.spec_id, stored['run_id'], pin) or checkpoint.get('protection_review') != notice):
            raise RuntimeError('LEADER_CURRENT_ORDER_PROTECTION_CHANGED')
        if stored['side'] == 'BUY':
            trigger = notice['planned_leader_stop']
            stop_risk = mock_automation_risk_snapshot_from_dict(document('execution_mock_automation_risk_snapshots', trigger['risk_snapshot_id']))
            if staged_pages is not None:
                stop_cursor = _leader_native_risk_high_watermark(ownership, stop_risk,
                    [row for events in staged_pages.values() for row in events])
                quantity = sum((1 if staged_orders[identity]['side'] == 'BUY' else -1) * row['quantity']
                    for identity, events in staged_pages.items() for sequence, row in events
                    if sequence <= stop_cursor and row['event_type'] == 'FILL')
            else:
                cursor.execute(f'SELECT accepted_sequence,document_json FROM central_execution_events WHERE intent_id={p} ORDER BY accepted_sequence',
                               (stored['intent_id'],))
                events = [(sequence, _json_document(raw)) for sequence, raw in cursor.fetchall()]
                stop_cursor = _leader_native_risk_high_watermark(ownership, stop_risk, events)
                quantity = sum(row['quantity'] for sequence, row in events
                    if sequence <= stop_cursor and row['event_type'] == 'FILL' and row['broker_execution_id'])
            if quantity != trigger['stop_loss']['quantity']:
                raise RuntimeError('LEADER_CURRENT_ORDER_ORIGINAL_STOP_FILLS_CHANGED')
    source = (notice['planned_leader_protection_exit'] if stored['policy_version'] == 'mock-protection-exit/v1'
              else document('execution_leader_decisions', stored['intent_id'] if ownership.get('_native_member_evidence') else stored['decision_id']))
    if (source.get('snapshot') or {}).get('feature_values', [{}])[0].get('value', {}).get('position_step'):
        from kiwoom_monitor.application.leader_breakout import leader_position_action_ref
        if (approval.get('version'), approval.get('position_action_ref')) != (
                'mock_automation_plan_action_gate/v1',
                leader_position_action_ref(config, pin, source['snapshot']['feature_values'][0]['value'])):
            raise RuntimeError('LEADER_CURRENT_ORDER_POSITION_ACTION_CHANGED')
    current_risk = mock_automation_risk_snapshot_from_dict(document('execution_mock_automation_current_risk', account))
    peers = _verified_native_order_management_peers(cursor, p, record, ownership['plan_recovery'], current_risk, control, 'LEADER')
    assessed = assess_leader_order_management(spec, run_id=stored['run_id'], config=config, position_proof=pin,
        record=record, head=head, order_events=[_json_document(row[0]) for row in _execution_event_rows(cursor, stored['intent_id'], p)],
        recovery=ownership['plan_recovery'], risk=current_risk, verified_peer_orders=peers,
        quote=claim['quote'], decided_at=datetime.fromisoformat(claim['decided_at']), now=now, entry_enabled=enabled,
        decision_source=source, protection_notice=notice, stop_risk=stop_risk,
        plan_entry_notional_won=plan_entry_notional, **_selected_management_close_terms(ownership))
    action = 'AMEND' if event['event_type'] == 'AMENDMENT_STARTED' else 'CANCEL'
    if (assessed != claim or claim['action'] != action or event['broker_order_id'] != claim['broker_order_id']
            or action == 'AMEND' and (event['quantity'], event['price']) != (claim['quantity'], claim['limit_price'])):
        raise RuntimeError('LEADER_CURRENT_ORDER_ASSESSMENT_CHANGED')
    return assessed


def _broker_order_lookup_sql(p):
    """Keep one round trip and the indexed direct path; search legs only after a miss."""
    scope = f"environment={p} AND account_ref={p} AND run_id={p}"
    physical = (f"SELECT 1 FROM jsonb_array_elements(i.document_json->'broker_legs') AS leg "
                f"WHERE leg->>'broker_order_id'={p}" if p == "%s" else
                f"SELECT 1 FROM json_each(i.document_json,'$.broker_legs') AS leg "
                f"WHERE json_extract(leg.value,'$.broker_order_id')={p}")
    return ("WITH direct AS MATERIALIZED (SELECT document_json FROM central_execution_intents WHERE "
            + scope + f" AND broker_order_id={p} LIMIT 2) "
            "SELECT document_json FROM direct UNION ALL SELECT document_json FROM ("
            "SELECT i.document_json FROM central_execution_intents AS i WHERE " + scope
            + " AND NOT EXISTS (SELECT 1 FROM direct) AND EXISTS (" + physical + ") LIMIT 2) AS amended")


def _apply_plan_execution_event(cursor, p, intent, event, sequence):
    ref = intent["plan"]
    account = intent["account_ref"]
    head = _plan_document(cursor, p, PLAN_POSITION_COLLECTION, account, ref["plan_id"])
    if head is None or (head["policy_ref"], head["run_id"], head["symbol"]) != (
        ref["policy_ref"], intent["run_id"], intent["symbol"],
    ):
        raise RuntimeError("EXECUTION_PLAN_POSITION_MISSING")
    if event["event_type"] == "FILL":
        if 'capital_lots' in head:
            lots = [list(lot) for lot in head['capital_lots']]
            if intent['side'] == 'BUY':
                lots.append([event['quantity'], event['price']])
            else:
                remaining = event['quantity']
                while remaining and lots:
                    taken = min(remaining, lots[0][0])
                    remaining -= taken
                    lots[0][0] -= taken
                    if not lots[0][0]:
                        lots.pop(0)
            head = {**head, 'capital_lots': lots}
        delta = event["quantity"] if intent["side"] == "BUY" else -event["quantity"]
        quantity = head["quantity"] + delta
        if quantity < 0:
            # Preserve a late broker FILL even if a previously released
            # reservation cannot be attributed to the plan's remaining shares.
            head["unreconciled_sell_quantity"] = head.get("unreconciled_sell_quantity", 0) - quantity
        head = {**head, "quantity": max(0, quantity), "fill_cursor": sequence}
    _save_plan_position(
        cursor, p, account, {**head, "revision": head["revision"] + 1},
        datetime.fromisoformat(event["received_at"]),
    )


def _minute_native_documents(cursor, p, account, spec_id, plan_id, *, kind='CLOSE', selected_management=None):
    """Read one original minute-strategy scope in the existing native transaction."""
    from kiwoom_monitor.application.mock_automation_candidate import candidate_package_from_dict
    from kiwoom_monitor.domain.execution_activation import CLOSE_NATIVE_PLAN_MODE, PANIC_NATIVE_PLAN_MODE, mock_automation_spec_from_dict

    if kind == 'PANIC':
        from kiwoom_monitor.application.panic_mock_execution import PANIC_ENTRY_COLLECTION
        collection, mode, families = PANIC_ENTRY_COLLECTION, PANIC_NATIVE_PLAN_MODE, {'panic_rebound/v1'}
    elif kind == 'CLOSE':
        from kiwoom_monitor.application.close_mock_execution import CLOSE_ENTRY_COLLECTION
        collection, mode, families = CLOSE_ENTRY_COLLECTION, CLOSE_NATIVE_PLAN_MODE, {'close_betting/v1', 'ipo_close/v1', 'ipo_breakout/v1'}
    else:
        raise ValueError('unsupported native minute-strategy scope')

    spec_doc = _plan_document(cursor, p, 'execution_mock_automation_specs', account, spec_id,
                              selected_management=selected_management)
    if spec_doc is None:
        raise RuntimeError(f"{kind}_EXECUTION_SPEC_MISSING")
    spec = mock_automation_spec_from_dict(spec_doc)
    package_doc = _plan_document(cursor, p, "execution_mock_automation_candidate_packages",
                                 spec.strategy_ref, spec.candidate_package_hash, selected_management=selected_management)
    proof = _plan_document(cursor, p, collection, account, plan_id, selected_management=selected_management)
    if package_doc is None or proof is None:
        raise RuntimeError(f"{kind}_ENTRY_EVIDENCE_MISSING")
    package = candidate_package_from_dict(package_doc)
    family = package.candidate_spec['family']
    if (spec.execution_plan_mode != mode or family not in families
            or package.package_hash != spec.candidate_package_hash):
        raise RuntimeError(f"{kind}_ENTRY_PACKAGE_CHANGED")
    if selected_management is None:
        cursor.execute(f"SELECT document_json,expires_at FROM central_shadow_candidate_events WHERE event_id={p}"
                       + (" FOR UPDATE" if p == "%s" else ""), (proof['snapshot']['candidate_event_id'],))
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError(f'{kind}_SHADOW_CANDIDATE_MISSING')
        event = _json_document(row[0])
        event['expires_at'] = row[1].isoformat() if isinstance(row[1], datetime) else str(row[1])
    else:
        from copy import deepcopy
        event = deepcopy(selected_management['_policy_view']['shadow_candidate'])
        if event is None or event.get('event_id') != proof['snapshot']['candidate_event_id']:
            raise RuntimeError(f'{kind}_SHADOW_CANDIDATE_MISSING')
    if f"{event.get('strategy_id')}/{event.get('strategy_version')}" != family:
        raise RuntimeError(f'{kind}_ENTRY_PACKAGE_CHANGED')
    return spec, package, proof, event


def _close_native_gap_observations(cursor, p, observations):
    from kiwoom_monitor.central_server.database_codec import _observation_revision_columns, observation_revision_result_rows

    if observations is None:
        return None
    refs = [row['revision_id'] for row in observations]
    if len(refs) != 2 or len(set(refs)) != 2:
        raise RuntimeError('CLOSE_GAP_NATIVE_REVISIONS_MISSING')
    cursor.execute(f"SELECT {_observation_revision_columns(postgres=p == '%s')} FROM central_observation_revisions "
                   f"WHERE revision_id IN ({p},{p})", refs)
    rows = {row['revision_id']: row for row in observation_revision_result_rows(cursor.fetchall())}
    if set(rows) != set(refs):
        raise RuntimeError('CLOSE_GAP_NATIVE_REVISIONS_MISSING')
    return [rows[ref] for ref in refs]


def _selected_management_close_terms(ownership):
    management = (ownership or {}).get('_native_selected_management_context')
    if management is None:
        return {}
    context = management['context']
    return {'selected_management_context': context, 'source_risk_snapshot_ids': tuple(
        item['key'] for item in management['_policy_view']['documents']
        if item['collection'] == 'execution_mock_automation_risk_snapshots' and item['owner'] == context.account_ref)}


def _close_native_stop_risk(cursor, p, account, spec, notice, root_id, *, checkpoint_key='current',
                            selected_management=None):
    """Read the acknowledged stop and the historical native fill cut, never a newer basis."""
    from kiwoom_monitor.application.mock_automation_risk import mock_automation_risk_snapshot_from_dict

    if notice is None:
        return None
    checkpoint = _plan_document(cursor, p, 'execution_mock_automation_runner_current', account, checkpoint_key,
                                selected_management=selected_management)
    document = _plan_document(cursor, p, 'execution_mock_automation_risk_snapshots', account,
                              notice['risk_trigger']['snapshot_id'], selected_management=selected_management)
    if (checkpoint is None or document is None or checkpoint.get('protection_review') != notice
            or (checkpoint.get('account_ref'), checkpoint.get('spec_id'), checkpoint.get('execution_run_id'),
                checkpoint.get('candidate_package_hash'))
            != (account, spec.spec_id, notice['execution_run_id'], spec.candidate_package_hash)):
        raise RuntimeError('CLOSE_DURABLE_STOP_CHANGED')
    risk = mock_automation_risk_snapshot_from_dict(document)
    watermark = risk.execution_event_high_watermark
    if selected_management is not None and any(
            (item['collection'], item['owner'], item['key'])
            == ('execution_mock_automation_risk_snapshots', account, risk.snapshot_id)
            for item in selected_management['_policy_view']['documents']):
        watermark = max((item['target_accepted_sequence']
            for item in selected_management['_policy_view']['installation']['event_coordinates']
            if item['source_accepted_sequence'] <= watermark), default=0)
    cursor.execute(f'SELECT document_json FROM central_execution_events WHERE intent_id={p} '
                   f'AND accepted_sequence<={p} ORDER BY accepted_sequence', (root_id, watermark))
    fills = [_json_document(row[0]) for row in cursor.fetchall()]
    if sum(row['quantity'] for row in fills if row['event_type'] == 'FILL') != notice['risk_trigger']['stop_loss']['quantity']:
        raise RuntimeError('CLOSE_STOP_NATIVE_FILL_CUT_CHANGED')
    return risk


def _require_close_execution_claim(cursor, p, value, ownership, *, now_provider, submission_claim):
    """Recheck entry or owned-position management in the order's native transaction."""
    from dataclasses import asdict, replace
    from kiwoom_monitor.application.close_mock_execution import (
        CloseIpoSetup, build_close_entry_evidence, build_close_exit_evidence, effective_close_exit_schedule,
    )
    from kiwoom_monitor.application.market_session_schedule import KST, decode_strategy_references, krx_calendar_valid_through
    from kiwoom_monitor.application.mock_automation_execution import mock_automation_intent_id
    from kiwoom_monitor.application.section12_minute_research import MinuteShadowConfig
    from kiwoom_monitor.domain.execution_activation import CloseExitCalendarRecheck
    from kiwoom_monitor.domain.order_contract import OrderPlanRef
    from kiwoom_monitor.domain.strategy_contract import _id, shadow_candidate_config_for_spec
    from kiwoom_monitor.infrastructure.persistence.execution_repository import _record_from_document, plan_position_from_document

    if value.get("side") not in {"BUY", "SELL"} or value.get("plan") is None:
        raise RuntimeError("CLOSE_EXECUTION_REQUIRES_NATIVE_PLAN")
    account = value["account_ref"]
    spec, package, proof, event = _minute_native_documents(cursor, p, account, ownership["active_spec_id"],
        value['plan']['plan_id'], selected_management=ownership.get('_native_selected_management_context'))
    now = kst_now() if now_provider is None else now_provider()
    if value["side"] == "BUY":
        settings = _plan_document(cursor, p, "server_operational_settings", "global", "current")
        if settings is None or settings.get("shadow_candidate_enabled") is not True:
            raise RuntimeError("CLOSE_SHADOW_NOT_ENABLED")
        selected = shadow_candidate_config_for_spec(settings, spec.spec_id)
        if selected is None:
            raise RuntimeError("CLOSE_CURRENT_SHADOW_CHANGED")
        config = MinuteShadowConfig.from_document(selected)
        monitor_id = _id("section12-minute-shadow", {"config": config.to_dict(), "session_profile": spec.session_profile,
            "universe_max_age_seconds": settings["shadow_candidate_universe_max_age_seconds"]})
        if event["run_id"] != monitor_id or config.strategy.family != package.candidate_spec['family']:
            raise RuntimeError("CLOSE_CURRENT_SHADOW_CHANGED")
        expected, decision, plan = build_close_entry_evidence(spec, run_id=value["run_id"],
            parameters=package.candidate_spec["parameters"], event=event, quote=proof["snapshot"]["quote"],
            decided_at=datetime.fromisoformat(value["created_at"]), now=now,
            retain_candidate=proof['snapshot']['version'] != 'close_entry_snapshot/v1')
    else:
        plan = OrderPlanRef(**value["plan"])
        exit_proof = _close_exit_for_intent(cursor, p, account, plan, value['decision_id'],
                                           selected_management=ownership.get('_native_selected_management_context'))
        head_doc = _plan_document(cursor, p, PLAN_POSITION_COLLECTION, account, plan.plan_id)
        root_id = mock_automation_intent_id(spec.spec_id, proof["decision"]["decision_id"])
        root_row = _execution_intent_row(cursor, root_id, p)
        if exit_proof is None or head_doc is None or root_row is None:
            raise RuntimeError("CLOSE_EXIT_EVIDENCE_MISSING")
        notice = exit_proof['snapshot'].get('protection_notice')
        stop_risk = _close_native_stop_risk(cursor, p, account, spec, notice, root_id,
            checkpoint_key=ownership.get('_native_checkpoint_key', 'current'),
            selected_management=ownership.get('_native_selected_management_context'))
        head = plan_position_from_document(head_doc, value["run_id"], value["symbol"], plan)
        # Creating the intent reserves exactly one head revision. Submission
        # rechecks that reservation, without treating it as a new position.
        if head.plan.expected_revision != plan.expected_revision + int(submission_claim):
            raise RuntimeError("CLOSE_EXIT_PLAN_REVISION_CHANGED")
        if submission_claim:
            queued = _execution_intent_row(cursor, value["intent_id"], p)
            queued = _json_document(queued[0]) if queued else {}
            if queued.get("plan") != value["plan"] or queued.get("state") != "QUEUED":
                raise RuntimeError("CLOSE_EXIT_RESERVATION_CHANGED")
        calendar = ()
        basis = exit_proof['snapshot'].get('calendar_basis')
        if basis is not None:
            document = _plan_document(cursor, p, 'krx_equity_calendar', 'krx', 'schedule')
            if document is None:
                raise CloseExitCalendarRecheck('CLOSE_EXIT_KRX_CALENDAR_UNAVAILABLE')
            calendar, _ = decode_strategy_references(document)
            checked = datetime.fromisoformat(document['checked_at'])
            if checked > now or now.astimezone(KST).date() > krx_calendar_valid_through(checked, calendar):
                raise CloseExitCalendarRecheck('CLOSE_EXIT_KRX_CALENDAR_UNAVAILABLE')
            _, current_basis = effective_close_exit_schedule(
                CloseIpoSetup.from_document(event['close_ipo_entry_setup']), now, calendar)
            if current_basis != basis:
                raise CloseExitCalendarRecheck('CLOSE_EXIT_KRX_CALENDAR_CHANGED')
        prepared = build_close_exit_evidence(spec, run_id=value["run_id"],
            parameters=package.candidate_spec["parameters"], event=event, entry_evidence=proof,
            root=_record_from_document(_json_document(root_row[0])), head=replace(head, plan=plan),
            fill_events=[_json_document(item[0]) for item in _execution_event_rows(cursor, root_id, p)],
            trigger_quote=exit_proof["snapshot"]["trigger_quote"], quote=exit_proof["snapshot"]["quote"],
            decided_at=datetime.fromisoformat(value["created_at"]), now=now,
            gap_observations=_close_native_gap_observations(cursor, p, exit_proof['snapshot'].get('gap_observations')),
            protection_notice=notice, stop_risk=stop_risk, calendar=calendar, **_selected_management_close_terms(ownership))
        if prepared is None:
            raise RuntimeError("CLOSE_EXIT_TRIGGER_MISSING")
        expected, decision, plan = prepared
        proof = exit_proof
    if (json.dumps(expected, sort_keys=True) != json.dumps(proof, sort_keys=True)
            or value["plan"] != asdict(plan) or value["decision_id"] != decision.decision_id
            or value["symbol"] != decision.symbol or value["quantity"] != decision.quantity
            or value["order_type"] != "LIMIT" or value["limit_price"] != decision.signal_reference_price):
        raise RuntimeError("CLOSE_ENTRY_PROOF_OR_ORDER_CHANGED" if value["side"] == "BUY"
                           else "CLOSE_EXIT_PROOF_OR_ORDER_CHANGED")


def _require_panic_trigger_checkpoint(cursor, p, account, spec, run_id, candidate, trigger=None, *, checkpoint_key='current'):
    from kiwoom_monitor.application.panic_mock_execution import PANIC_INPUT_PROTOCOL, panic_entry_plan_id
    checkpoint = _plan_document(cursor, p, 'execution_mock_automation_runner_current', account, checkpoint_key)
    bound = (isinstance(checkpoint, dict) and checkpoint.get('version') == 'mock_automation_runner_checkpoint/v1'
        and (checkpoint.get('account_ref'), checkpoint.get('spec_id'), checkpoint.get('execution_run_id'),
             checkpoint.get('candidate_package_hash'), checkpoint.get('delivery_protocol'))
        == (account, spec.spec_id, run_id, spec.candidate_package_hash, PANIC_INPUT_PROTOCOL))
    management = checkpoint.get('minute_management') if bound else None
    stored = ((management.get('triggers') or {}).get(panic_entry_plan_id(spec.spec_id, run_id, candidate['event_id']))
              if isinstance(management, dict) and management.get('version') == PANIC_INPUT_PROTOCOL else None)
    if trigger is not None and stored != trigger:
        raise RuntimeError('PANIC_FIRST_TRIGGER_CHECKPOINT_CHANGED')
    return stored


def _require_panic_current_minute_revisions(cursor, p, event, now):
    """Fence only this entry's required minute slots, retaining every revision.

    A later known correction needs a new Shadow decision. Future revisions
    are not current knowledge, and an existing position's SELL never calls here.
    """
    from kiwoom_monitor.central_server.database_codec import _observation_revision_columns, observation_revision_result_rows

    refs = tuple(dict.fromkeys((*event['panic_entry_setup']['input_refs'], event['reference_revision_id'])))
    if not refs or any(not isinstance(ref, str) or not ref for ref in refs):
        raise RuntimeError('PANIC_CURRENT_INPUT_REVISIONS_MISSING')
    slots = ','.join(p for _ in refs)
    cursor.execute(f'SELECT {_observation_revision_columns(postgres=p == "%s")} '
        f'FROM central_observation_revisions WHERE revision_id IN ({slots})', refs)
    rows = observation_revision_result_rows(cursor.fetchall())
    if {row['revision_id'] for row in rows} != set(refs):
        raise RuntimeError('PANIC_CURRENT_INPUT_REVISIONS_MISSING')
    available_column = 'available_at' if p == '%s' else 'julianday(available_at)'
    current_time = p if p == '%s' else f'julianday({p})'
    for row in rows:
        available = datetime.fromisoformat(row['available_at']) if row['available_at'] else None
        if (row['kind'] != 'minute_bar' or row['payload'].get('code') != event['symbol']
                or row['subject'] not in {event['symbol'], event['symbol'] + ':KRX'}
                or row['completeness'] != 'complete' or row['payload'].get('capture_quality') != 'complete'
                or available is None or available.utcoffset() is None or available > now):
            raise RuntimeError('PANIC_CURRENT_INPUT_REVISIONS_UNPROVEN')
        cursor.execute('SELECT revision_id FROM central_observation_revisions '
            f'WHERE kind={p} AND subject={p} AND observation_key={p} '
            f'AND {available_column}<={current_time} '
            f'ORDER BY {available_column} DESC,accepted_sequence DESC LIMIT 1',
            ('minute_bar', row['subject'], row['observation_key'], now if p == '%s' else now.isoformat()))
        latest = cursor.fetchone()
        if latest is None or latest[0] != row['revision_id']:
            raise RuntimeError('PANIC_CURRENT_INPUT_REVISIONS_CHANGED')


def _require_panic_execution_claim(cursor, p, value, ownership, *, now_provider, submission_claim):
    from dataclasses import asdict, replace
    from kiwoom_monitor.application.news_material import RECORDED_CONTRACT_FILINGS, recorded_contract_material
    from kiwoom_monitor.application.panic_rebound import material_panic_proofs
    from kiwoom_monitor.application.panic_mock_execution import (
        PANIC_EXIT_COLLECTION, build_panic_entry_evidence, build_panic_exit_evidence, panic_exit_key,
    )
    from kiwoom_monitor.application.minute_order_management import minute_current_shadow_matches
    from kiwoom_monitor.application.mock_automation_execution import mock_automation_intent_id
    from kiwoom_monitor.domain.order_contract import OrderPlanRef
    from kiwoom_monitor.infrastructure.persistence.execution_repository import _record_from_document, plan_position_from_document

    if value.get('side') not in {'BUY', 'SELL'} or value.get('plan') is None:
        raise RuntimeError('PANIC_EXECUTION_REQUIRES_NATIVE_PLAN')
    account = value['account_ref']
    spec, package, proof, event = _minute_native_documents(cursor, p, account, ownership['active_spec_id'],
        value['plan']['plan_id'], kind='PANIC', selected_management=ownership.get('_native_selected_management_context'))
    now = kst_now() if now_provider is None else now_provider()
    if value['side'] == 'BUY':
        if _require_panic_trigger_checkpoint(cursor, p, account, spec, value['run_id'], event, checkpoint_key=ownership.get('_native_checkpoint_key', 'current')) is not None:
            raise RuntimeError('PANIC_ORIGINAL_SIGNAL_ALREADY_INVALIDATED')
        settings = _plan_document(cursor, p, 'server_operational_settings', 'global', 'current')
        if not minute_current_shadow_matches(spec, event, settings):
            raise RuntimeError('PANIC_CURRENT_SHADOW_CHANGED')
        _require_panic_current_minute_revisions(cursor, p, event, now)
        symbol = event["symbol"]
        cursor.execute(
            f"SELECT document_json FROM central_documents WHERE collection={p} AND owner={p} "
            "ORDER BY updated_at DESC,document_key DESC LIMIT 1000",
            (RECORDED_CONTRACT_FILINGS, symbol),
        )
        material = recorded_contract_material((_json_document(row[0]) for row in cursor.fetchall()), now)
        if any(row.disposition == "fundamental_adverse" for row in material_panic_proofs(material, symbol, now)):
            raise RuntimeError("PANIC_OFFICIAL_ADVERSE_EVENT")
        expected, decision, plan = build_panic_entry_evidence(spec, run_id=value['run_id'],
            parameters=package.candidate_spec['parameters'], event=event, quote=proof['snapshot']['quote'],
            decided_at=datetime.fromisoformat(value['created_at']), now=now)
    else:
        plan = OrderPlanRef(**value['plan'])
        exit_proof = _plan_document(cursor, p, PANIC_EXIT_COLLECTION, account, panic_exit_key(plan))
        head_doc = _plan_document(cursor, p, PLAN_POSITION_COLLECTION, account, plan.plan_id)
        root_id = mock_automation_intent_id(spec.spec_id, proof['decision']['decision_id'])
        root_row = _execution_intent_row(cursor, root_id, p)
        if exit_proof is None or head_doc is None or root_row is None:
            raise RuntimeError('PANIC_EXIT_EVIDENCE_MISSING')
        trigger = exit_proof['snapshot'].get('panic_trigger')
        if trigger is not None:
            _require_panic_trigger_checkpoint(cursor, p, account, spec, value['run_id'], event, trigger, checkpoint_key=ownership.get('_native_checkpoint_key', 'current'))
        head = plan_position_from_document(head_doc, value['run_id'], value['symbol'], plan)
        if head.plan.expected_revision != plan.expected_revision + int(submission_claim):
            raise RuntimeError('PANIC_EXIT_PLAN_REVISION_CHANGED')
        if submission_claim:
            queued = _execution_intent_row(cursor, value['intent_id'], p)
            queued = _json_document(queued[0]) if queued else {}
            if queued.get('plan') != value['plan'] or queued.get('state') != 'QUEUED':
                raise RuntimeError('PANIC_EXIT_RESERVATION_CHANGED')
        prepared = build_panic_exit_evidence(spec, run_id=value['run_id'], parameters=package.candidate_spec['parameters'],
            event=event, entry_evidence=proof, root=_record_from_document(_json_document(root_row[0])),
            head=replace(head, plan=plan), fill_events=[_json_document(row[0]) for row in _execution_event_rows(cursor, root_id, p)],
            trigger_quote=exit_proof['snapshot']['trigger_quote'], quote=exit_proof['snapshot']['quote'],
            decided_at=datetime.fromisoformat(value['created_at']), now=now, panic_trigger=trigger)
        if prepared is None: raise RuntimeError('PANIC_EXIT_TRIGGER_MISSING')
        expected, decision, plan = prepared
        proof = exit_proof
    if (json.dumps(expected, sort_keys=True) != json.dumps(proof, sort_keys=True)
            or value['plan'] != asdict(plan) or value['decision_id'] != decision.decision_id
            or (value['symbol'], value['quantity'], value['order_type'], value['limit_price'])
            != (decision.symbol, decision.quantity, 'LIMIT', decision.signal_reference_price)):
        raise RuntimeError('PANIC_ENTRY_PROOF_OR_ORDER_CHANGED' if value['side'] == 'BUY' else 'PANIC_EXIT_PROOF_OR_ORDER_CHANGED')


def _require_leader_execution_claim(cursor, ownership, value, p, *, now_provider):
    """Bind the already-approved generic gate to the new Family's exact evidence."""
    from kiwoom_monitor.application.leader_breakout import (
        LeaderBreakoutConfig, LeaderBreakoutPolicy, LeaderBreakoutState, evaluate_leader_breakout_trade,
        validate_leader_position_proof, leader_evaluation, evaluate_leader_position_step,
    )
    from kiwoom_monitor.application.research_families import parse_strategy_config
    from kiwoom_monitor.application.mock_automation_candidate import candidate_package_from_dict
    from kiwoom_monitor.domain.execution_activation import mock_automation_spec_from_dict
    from kiwoom_monitor.application.breakout_strategy import StrategyState
    management = ownership.get('_native_selected_management_context')
    def document(collection, owner, key):
        row = _plan_document(cursor, p, collection, owner, key, selected_management=management)
        if row is None:
            raise RuntimeError('LEADER_EXECUTION_EVIDENCE_MISSING')
        return row
    account = value['account_ref']
    spec = mock_automation_spec_from_dict(document('execution_mock_automation_specs', account, ownership['active_spec_id']))
    package = candidate_package_from_dict(document('execution_mock_automation_candidate_packages', spec.strategy_ref, spec.candidate_package_hash))
    config = parse_strategy_config(package.candidate_spec['family'], package.candidate_spec['parameters'])
    if not isinstance(config, LeaderBreakoutConfig) or package.package_hash != spec.candidate_package_hash:
        raise RuntimeError('LEADER_EXECUTION_PACKAGE_CHANGED')
    if hasattr(config.execution_contract, 'position_steps') and (
            spec.execution_plan_mode != 'leader-0b-native-plan/v1' or spec.unfilled_execution_policy is None
            or spec.unfilled_execution_policy.accepted_order_management is None):
        raise RuntimeError('STAGED_LEADER_CURRENT_ORDER_POLICY_REQUIRED')
    record = document('execution_leader_decisions', account, value['intent_id'] if ownership.get('_native_member_evidence') else value['decision_id'])
    raw = record['snapshot']['feature_values'][0]['value']
    policy = LeaderBreakoutPolicy.from_dict(record['policy'])
    configured = LeaderBreakoutConfig.from_dict({**record['policy'], 'execution_contract': config.execution_contract.to_dict()})
    if configured.rules_hash != config.rules_hash:
        raise RuntimeError('LEADER_EXECUTION_RULES_CHANGED')
    proof = record['position_proof']
    context = validate_leader_position_proof(proof, config, spec, ownership['run_id'])
    plan = value.get("plan")
    if plan is not None and (
        plan["plan_id"] != proof["entry_intent_id"] or plan["policy_ref"] != proof["proof_id"]
        or (value["side"] == "BUY" and raw.get('position_step') is None and plan["expected_revision"] != 0)
    ):
        raise RuntimeError("LEADER_EXECUTION_PLAN_PROOF_CHANGED")
    saved = document('execution_leader_position_context', account, proof['entry_intent_id'])
    if saved != proof:
        raise RuntimeError('LEADER_PINNED_CONTEXT_CHANGED')
    if value['side'] == 'SELL':
        root_row = _execution_intent_row(cursor, proof['entry_intent_id'], p)
        root = _json_document(root_row[0]) if root_row else {}
        if ((root.get('environment'), root.get('account_ref'), root.get('run_id'), root.get('symbol'),
             root.get('side'), root.get('policy_version'))
                != ('mock', account, ownership['run_id'], value['symbol'], 'BUY', 'leader_execution_adapter/v1')):
            raise RuntimeError('LEADER_EXIT_ENTRY_ORIGIN_UNCONFIRMED')
        root_plan = root.get('plan')
        if ((root_plan is None) != (plan is None)
                or (root_plan is not None and (root_plan.get('plan_id'), root_plan.get('policy_ref'))
                    != (plan['plan_id'], plan['policy_ref']))):
            raise RuntimeError('LEADER_EXIT_ENTRY_PLAN_CHANGED')
    state = dict(record['decision']['state_before'])
    state['emitted_candidate_keys'] = tuple(state['emitted_candidate_keys'])
    state = StrategyState(**state)
    observation = record['observation']
    if (observation.get('approved') is not True or not observation.get('source')
            or record.get('policy_hash') != policy.fingerprint
            or value['side'] == 'BUY' and type(record.get('operational_revision')) is not int):
        raise RuntimeError('LEADER_INPUT_AUTHORITY_MISSING')
    reproduced, _ = evaluate_leader_breakout_trade(policy, observation['payload'],
        received_at=datetime.fromisoformat(observation['received_at']), source=observation['source'],
        decision_time=datetime.fromisoformat(raw['decided_at']), state=LeaderBreakoutState(**raw['state_before']),
        position_open=state.status == 'open', position_context=context if state.status == 'open' else None)
    from kiwoom_monitor.application.mock_automation_session_clock import session_clock_assessment
    assessment = session_clock_assessment(config, state, datetime.fromisoformat(raw['decided_at']))
    if assessment['exit_required'] and reproduced['status'] in {'HOLD','HARD_EXIT','INVALIDATED'}:
        reproduced = {**reproduced, 'proposal':'EXIT', 'reasons':[*reproduced['reasons'],*assessment['reasons']]}
    if raw.get('position_step') is not None:
        confirmation = raw['position_step'].get('confirmation')
        before = LeaderBreakoutState(**confirmation['state_before']) if confirmation is not None else None
        reproduced, _ = evaluate_leader_position_step(configured, reproduced, state, context,
            source=observation['source'], step_state=before, entry_enabled=True)
    canonical = lambda obj: json.dumps(obj, sort_keys=True, separators=(',', ':'))
    if canonical(reproduced) != canonical(raw):
        raise RuntimeError('LEADER_0B_DECISION_PROOF_CHANGED')
    expected = leader_evaluation(run_id=ownership['run_id'], config=configured, raw=raw, state=state, context=context)
    if canonical(expected.decision.to_dict()) != canonical(record['decision']):
        raise RuntimeError('LEADER_SIZED_DECISION_CHANGED')
    now = kst_now() if now_provider is None else now_provider()
    at = datetime.fromisoformat(raw['available_at'])
    observed = datetime.fromisoformat(raw['observed_at'])
    if (not 0 <= (now-at).total_seconds() <= config.max_data_age_seconds
            or not 0 <= (now-observed).total_seconds() <= config.max_data_age_seconds
            or value['symbol'] != expected.decision.symbol or value['quantity'] != expected.decision.quantity
            or value['decision_id'] != expected.decision.decision_id
            or value['side'] != {'ENTER': 'BUY', 'ADD': 'BUY', 'REDUCE': 'SELL', 'EXIT': 'SELL'}.get(expected.decision.final_action)
            or value['order_type'] != config.execution_contract.order_type
            or value['limit_price'] != expected.decision.signal_reference_price):
        raise RuntimeError('LEADER_PRICE_OR_ORDER_CHANGED')
    if value['side'] == 'BUY':
        if now >= datetime.fromisoformat(raw['valid_until']):
            raise RuntimeError('LEADER_ENTRY_SIGNAL_EXPIRED')
        settings = document('server_operational_settings', 'global', 'current')
        from kiwoom_monitor.domain.strategy_contract import shadow_candidate_config_for_spec
        selected = shadow_candidate_config_for_spec(settings, spec.spec_id)
        if selected is None:
            raise RuntimeError('LEADER_CURRENT_ENTRY_CONTEXT_CHANGED')
        live = LeaderBreakoutPolicy.from_dict(selected)
        if (LeaderBreakoutConfig.from_dict(live.to_dict()).rules_hash != config.rules_hash
                or context not in live.contexts or now >= datetime.fromisoformat(context.valid_until)):
            raise RuntimeError('LEADER_CURRENT_ENTRY_CONTEXT_CHANGED')
        if hasattr(config.execution_contract, 'position_steps') and settings.get('shadow_candidate_enabled') is not True:
            raise RuntimeError('LEADER_CURRENT_ENTRY_DISABLED')
    if raw.get('position_step') is not None:
        _require_leader_position_action(cursor, p, value, spec, configured, proof, raw, state, now,
            member_evidence=ownership.get('_native_member_evidence') is True, selected_management=management)
    elif expected.decision.final_action == 'ENTER':
        from kiwoom_monitor.application.leader_breakout import leader_reentry_enabled
        if leader_reentry_enabled(config):
            _require_leader_reentry_claim(cursor, p, value, ownership, spec, context, state, now)


def _require_leader_reentry_claim(cursor, p, value, ownership, spec, context, state, now):
    """One native setup claim and the latest own completed plan, never all history."""
    from kiwoom_monitor.application.leader_breakout import leader_setup_key, _time, _id
    from kiwoom_monitor.application.mock_automation_execution import _content_id
    from kiwoom_monitor.application.mock_automation_risk import mock_automation_risk_snapshot_from_dict
    from kiwoom_monitor.domain.order_contract import TERMINAL_ORDER_STATES
    from kiwoom_monitor.infrastructure.persistence.execution_repository import _record_from_document, execution_broker_legs, project_execution_record
    account, run_id = value['account_ref'], value['run_id']
    risk_doc = _plan_document(cursor, p, 'execution_mock_automation_current_risk', account, account)
    risk = mock_automation_risk_snapshot_from_dict(risk_doc) if risk_doc else None
    selection = ownership.get('_native_account_selection')
    group = selection is not None
    try:
        owned = (risk.owned_for_member(spec.spec_id) if group else risk.owned_position_quantities) if risk else None
        pending = (risk.pending_sells_for_member(spec.spec_id) if group else risk.pending_sell_quantities) if risk else None
    except ValueError as error:
        raise RuntimeError('LEADER_REENTRY_RISK_UNCONFIRMED') from error
    if (risk is None or not risk.reconciliation_complete or owned or pending
            or risk.submission_unknown_count or risk.balance_mismatch_count
            or state.status != 'flat' or state.position_quantity not in (None, 0)):
        raise RuntimeError('LEADER_REENTRY_REQUIRES_CONFIRMED_OWN_FLAT')
    others = set((ownership.get('_native_account_selection') or {}).get('member_spec_ids', ())) - {spec.spec_id}
    rows = _account_active_execution_rows(cursor, 'mock', account, 401, p)
    if len(rows) > 400:
        raise RuntimeError('LEADER_REENTRY_ACTIVE_ORDER_COVERAGE_UNCONFIRMED')
    active = tuple(_json_document(row[0]) for row in rows)
    selected, projection = (_retained_execution_member_projection(cursor, p, account, _selection=selection,
        intent_ids=tuple(row['intent_id'] for row in active)) if group else (None, {}))
    for row in active:
        record = project_execution_record(_record_from_document(row), projection)
        if record.intent.intent_id == value['intent_id'] or record.intent.run_id != run_id:
            continue
        if not group or getattr(record, 'member_spec_id', None) not in others:
            raise RuntimeError('LEADER_REENTRY_OWN_ORDER_UNCONFIRMED')
    # Follow accepted-sequence keyset pages only until the newest own FILL.
    # A verified retained peer may have no raw member field. Never skip an
    # unassigned/manual row merely because its account/run matches a peer.
    event_type = "e.document_json->>'event_type'" if p == '%s' else "json_extract(e.document_json,'$.event_type')"
    member = "i.document_json->>'mock_account_member_spec_id'" if p == '%s' else "json_extract(i.document_json,'$.mock_account_member_spec_id')"
    predicate, params = '', ['mock', account, run_id, 'FILL']
    if group and others:
        predicate = f' AND ({member} IS NULL OR {member} NOT IN (' + ','.join(p for _ in others) + '))'
        params.extend(sorted(others))
    page_size = (400 if selected is not None and any(ref.original_selection_id is None
                 for ref in selected.retained_admission_refs) else 1)
    latest, before = None, None
    while True:
        boundary = f' AND e.accepted_sequence<{p}' if before is not None else ''
        cursor.execute('SELECT i.document_json,e.accepted_sequence,e.document_json '
            'FROM central_execution_events e JOIN central_execution_intents i ON i.intent_id=e.intent_id '
            f'WHERE i.environment={p} AND i.account_ref={p} AND i.run_id={p} AND {event_type}={p}'
            + predicate + boundary + f' ORDER BY e.accepted_sequence DESC LIMIT {page_size}',
            tuple(params) + ((before,) if before is not None else ()))
        page = cursor.fetchall()
        documents = tuple(_json_document(row[0]) for row in page)
        _, projection = (_retained_execution_member_projection(cursor, p, account, _selection=selection,
            intent_ids=tuple(row['intent_id'] for row in documents)) if group else (None, {}))
        for row, document in zip(page, documents, strict=True):
            previous = project_execution_record(_record_from_document(document), projection)
            if group and getattr(previous, 'member_spec_id', None) in others:
                continue
            latest = row, previous
            break
        if latest is not None or len(page) < page_size:
            break
        before = int(page[-1][1])
    predecessor_id, sequence = '', 0
    if latest is not None:
        latest_row, previous = latest
        event, sequence = _json_document(latest_row[2]), int(latest_row[1])
        plan = getattr(previous.intent, 'plan', None)
        if (previous.intent.side.value != 'SELL' or plan is None
                or group and getattr(previous, 'member_spec_id', None) != spec.spec_id
                or sequence > risk.execution_event_high_watermark):
            raise RuntimeError('LEADER_REENTRY_PREVIOUS_FILL_UNCONFIRMED')
        predecessor_id = plan.plan_id
        head = _plan_document(cursor, p, PLAN_POSITION_COLLECTION, account, predecessor_id) or {}
        if selected is not None:
            head = _retained_plan_position_view(cursor, p, selected, head)
        if ((head.get('run_id'), head.get('symbol'), head.get('policy_ref'), head.get('quantity'),
                head.get('unreconciled_sell_quantity', 0), head.get('fill_cursor'))
                != (run_id, value['symbol'], plan.policy_ref, 0, 0, sequence)
                or group and head.get('spec_id') != spec.spec_id):
            raise RuntimeError('LEADER_REENTRY_PREVIOUS_PLAN_NOT_FLAT')
        orders, pages = _leader_plan_ledger(cursor, p, account, run_id, predecessor_id)
        projection = {}
        if group:
            identities = tuple(orders)
            for offset in range(0, len(identities), 400):
                _, original_members = _retained_execution_member_projection(cursor, p, account,
                    _selection=selection, intent_ids=identities[offset:offset+400])
                projection.update(original_members)
        bought = sold = 0
        required_keys = set()
        for identity, document in orders.items():
            order = project_execution_record(_record_from_document(document), projection)
            if (order.state not in TERMINAL_ORDER_STATES
                    or order.filled_quantity != order.detailed_filled_quantity
                    or order.filled_quantity != order.broker_reported_filled_quantity
                    or group and getattr(order, 'member_spec_id', None) != spec.spec_id
                    or any(leg.state not in TERMINAL_ORDER_STATES
                           or leg.detailed_filled_quantity != leg.broker_reported_filled_quantity
                           for leg in execution_broker_legs(order))):
                raise RuntimeError('LEADER_REENTRY_PREVIOUS_ORDER_UNCONFIRMED')
            for _, item in pages[identity]:
                if item['event_type'] == 'FILL':
                    if order.intent.side.value == 'BUY': bought += item['quantity']
                    else: sold += item['quantity']
            if order.intent.policy_version == 'leader_execution_adapter/v1':
                source = _plan_document(cursor, p, 'execution_leader_decisions', account,
                    order.intent.intent_id if ownership.get('_native_member_evidence') is True
                    else order.intent.decision_id) or {}
                decision = dict(source.get('decision') or {})
                identity = decision.pop('decision_id', None)
                if identity != order.intent.decision_id or identity != _content_id('decision', decision):
                    raise RuntimeError('LEADER_REENTRY_PREVIOUS_DECISION_CHANGED')
                required_keys.update(decision['state_after']['emitted_candidate_keys'])
        boundary = max(_time(event['occurred_at']), _time(event['received_at']))
        if (not bought or bought != sold or not state.cooldown_until
                or _time(state.cooldown_until) != boundary or _time(context.available_at) <= boundary
                or not required_keys <= set(state.emitted_candidate_keys)):
            raise RuntimeError('LEADER_REENTRY_NEW_SETUP_OR_HISTORY_UNCONFIRMED')
    elif state.emitted_candidate_keys or state.cooldown_until:
        raise RuntimeError('LEADER_REENTRY_PREVIOUS_FILL_UNCONFIRMED')
    setup = leader_setup_key(context)
    key = _id('leader-setup-claim/v1', [spec.spec_id, run_id, setup])
    claim = dict(version='leader-setup-claim/v1', spec_id=spec.spec_id, run_id=run_id,
        setup_key=setup, intent_id=value['intent_id'], predecessor_plan_id=predecessor_id,
        predecessor_fill_sequence=sequence)
    collection = 'execution_leader_setup_claims'
    existing = _plan_document(cursor, p, collection, account, key)
    if existing is not None and existing != claim:
        raise RuntimeError('LEADER_REENTRY_SETUP_ALREADY_CLAIMED')
    if existing is None:
        cursor.execute(f'INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) '
            f'VALUES({p},{p},{p},{p},{p}) ON CONFLICT(collection,owner,document_key) DO NOTHING',
            (collection, account, key, now.timestamp(), _event_document(claim)))
        if _plan_document(cursor, p, collection, account, key) != claim:
            raise RuntimeError('LEADER_REENTRY_SETUP_ALREADY_CLAIMED')


def _require_leader_position_action(cursor, p, value, spec, config, pin, raw, state, now, *, member_evidence=False,
                                  selected_management=None):
    """Single-use staged action and cumulative entry budget, in the owning transaction."""
    from dataclasses import asdict
    from kiwoom_monitor.application.leader_breakout import (
        leader_position_action_ref, leader_next_position_step, leader_position_step_key, _id)
    from kiwoom_monitor.application.mock_automation_execution import MOCK_AUTOMATION_PLAN_ACTION_GATE_VERSION
    from kiwoom_monitor.domain.strategy_contract import LeaderBreakoutStagedExecutionContract
    from kiwoom_monitor.domain.order_contract import TERMINAL_ORDER_STATES
    from kiwoom_monitor.infrastructure.persistence.execution_repository import _record_from_document, execution_broker_legs
    terms = config.execution_contract
    if not isinstance(terms, LeaderBreakoutStagedExecutionContract) or not terms.position_steps:
        raise RuntimeError('LEADER_POSITION_ACTION_UNCONFIGURED')
    account, plan = value['account_ref'], value['plan']
    metadata = raw['position_step']
    selected = leader_next_position_step(config, state)
    if selected is None or selected[0] != metadata['index'] or selected[2] != metadata['step_key']:
        raise RuntimeError('LEADER_POSITION_ACTION_SEQUENCE_CHANGED')
    gate = _plan_document(cursor, p, 'execution_mock_automation_approved_gates', account, value['intent_id']) or {}
    action_ref = leader_position_action_ref(config, pin, raw)
    if (gate.get('version'), gate.get('action'), gate.get('position_action_ref')) != (
            MOCK_AUTOMATION_PLAN_ACTION_GATE_VERSION, selected[1].action, action_ref):
        raise RuntimeError('LEADER_POSITION_ACTION_APPROVAL_CHANGED')
    head = _plan_document(cursor, p, PLAN_POSITION_COLLECTION, account, plan['plan_id']) or {}
    if (head.get('policy_ref'), head.get('run_id'), head.get('symbol'), head.get('quantity')) != (
            pin['proof_id'], value['run_id'], value['symbol'], state.position_quantity) or head.get('unreconciled_sell_quantity'):
        raise RuntimeError('LEADER_POSITION_ACTION_OWNED_QUANTITY_CHANGED')
    index = metadata['index']
    prior_id = pin['entry_intent_id']
    if index:
        step = terms.position_steps[index-1]
        key = leader_position_step_key(config, index-1, step, context=pin['context'])
        prior = _plan_document(cursor, p, 'execution_leader_position_actions', account,
            _id('leader-plan-step-claim/v1', [plan['plan_id'], key]), selected_management=selected_management) or {}
        if (prior.get('plan_id'), prior.get('policy_ref'), prior.get('step_key'), prior.get('index')) != (
                plan['plan_id'], pin['proof_id'], key, index-1):
            raise RuntimeError('LEADER_PREVIOUS_POSITION_ACTION_MISSING')
        prior_id = prior.get('intent_id', '')
    previous_row = _execution_intent_row(cursor, prior_id, p)
    previous = _record_from_document(_json_document(previous_row[0])) if previous_row else None
    if (previous is None or previous.state not in TERMINAL_ORDER_STATES
            or getattr(previous.intent, 'plan', None) is None
            or (previous.intent.account_ref, previous.intent.run_id, previous.intent.symbol,
                previous.intent.policy_version) != (account, value['run_id'], value['symbol'], 'leader_execution_adapter/v1')
            or previous.intent.plan.plan_id != plan['plan_id'] or previous.intent.plan.policy_ref != pin['proof_id']
            or previous.filled_quantity != previous.detailed_filled_quantity
            or previous.filled_quantity != previous.broker_reported_filled_quantity
            or any(leg.state not in TERMINAL_ORDER_STATES
                   or leg.detailed_filled_quantity != leg.broker_reported_filled_quantity
                   for leg in execution_broker_legs(previous))):
        raise RuntimeError('LEADER_PREVIOUS_POSITION_ACTION_UNCONFIRMED')
    if index:
        prior_source = _plan_document(cursor, p, 'execution_leader_decisions', account,
            previous.intent.intent_id if member_evidence else previous.intent.decision_id,
            selected_management=selected_management) or {}
        prior_raw = (prior_source.get('snapshot') or {}).get('feature_values', [{}])[0].get('value', {})
        if ((prior_raw.get('position_step') or {}).get('step_key') != key
                or prior.get('decision_id') != previous.intent.decision_id
                or prior.get('position_action_ref') != leader_position_action_ref(config, pin, prior_raw)):
            raise RuntimeError('LEADER_PREVIOUS_POSITION_ACTION_CHANGED')
    if value['side'] == 'BUY':
        orders, pages = _leader_plan_ledger(cursor, p, account, value['run_id'], plan['plan_id'])
        spent = sum(row['quantity'] * row['price'] for identity, events in pages.items()
                    if orders[identity]['side'] == 'BUY' for _, row in events if row['event_type'] == 'FILL')
        if spent + value['quantity'] * value['limit_price'] > min(terms.capital_won, spec.maximum_capital_won):
            raise RuntimeError('LEADER_POSITION_ENTRY_BUDGET_EXCEEDED')
    collection = 'execution_leader_position_actions'
    key = _id('leader-plan-step-claim/v1', [plan['plan_id'], metadata['step_key']])
    claim = dict(version='leader-position-action-claim/v1', plan_id=plan['plan_id'], policy_ref=pin['proof_id'],
        run_id=value['run_id'], spec_id=spec.spec_id, index=index, step_key=metadata['step_key'],
        intent_id=value['intent_id'], decision_id=value['decision_id'], position_action_ref=action_ref,
        action=selected[1].action, quantity=value['quantity'])
    existing = _plan_document(cursor, p, collection, account, key, selected_management=selected_management)
    if existing is not None and existing != claim:
        raise RuntimeError('LEADER_POSITION_ACTION_ALREADY_CLAIMED')
    if head.get('revision') != plan['expected_revision'] + (existing is not None):
        raise RuntimeError('LEADER_POSITION_ACTION_REVISION_CHANGED')
    if existing is None:
        cursor.execute(f'INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) '
            f'VALUES({p},{p},{p},{p},{p}) ON CONFLICT(collection,owner,document_key) DO NOTHING',
            (collection, account, key, now.timestamp(), _event_document(claim)))
        if _plan_document(cursor, p, collection, account, key) != claim:
            raise RuntimeError('LEADER_POSITION_ACTION_ALREADY_CLAIMED')


def _leader_plan_ledger(cursor, p, account, run_id, plan_id):
    """One transaction-scoped plan projection; no new store or account owner."""
    projection = "i.document_json->'plan'->>'plan_id'" if p == '%s' else "json_extract(i.document_json,'$.plan.plan_id')"
    cursor.execute('SELECT i.document_json,e.accepted_sequence,e.document_json '
        'FROM central_execution_intents i LEFT JOIN central_execution_events e ON e.intent_id=i.intent_id '
        f'WHERE i.environment={p} AND i.account_ref={p} AND i.run_id={p} AND {projection}={p} '
        'ORDER BY e.accepted_sequence', ('mock', account, run_id, plan_id))
    orders, pages = {}, {}
    for intent, sequence, event in cursor.fetchall():
        value = _json_document(intent)
        identity = value['intent_id']
        orders[identity] = value
        pages.setdefault(identity, [])
        if event is not None:
            pages[identity].append((sequence, _json_document(event)))
    return orders, pages


def _leader_native_risk_high_watermark(ownership, risk, events):
    """Project an immutable source stop cut onto its exact installed native events."""
    management = (ownership or {}).get('_native_selected_management_context')
    if (management is None or risk.snapshot_id not in
            _selected_management_close_terms(ownership)['source_risk_snapshot_ids']):
        return risk.execution_event_high_watermark
    view = management['_policy_view']
    originals = {event['event_id']: event for event in view['source_events']}
    coordinates = view['installation']['event_coordinates']
    mapped = {item['event_id']: item for item in coordinates}
    if (not originals or len(originals) != len(view['source_events'])
            or len(mapped) != len(coordinates) or set(mapped) != set(originals)
            or any(type(item.get(name)) is not int or item[name] <= 0
                for item in coordinates for name in ('source_accepted_sequence', 'target_accepted_sequence'))
            or any(item['source_accepted_sequence'] != originals[identity]['accepted_sequence']
                for identity, item in mapped.items())):
        raise RuntimeError('LEADER_SELECTED_SOURCE_EVENT_COORDINATES_CHANGED')
    ordered = sorted(coordinates, key=lambda item: item['source_accepted_sequence'])
    if (len({item['source_accepted_sequence'] for item in coordinates}) != len(coordinates)
            or any(left['target_accepted_sequence'] >= right['target_accepted_sequence']
                   for left, right in zip(ordered, ordered[1:]))):
        raise RuntimeError('LEADER_SELECTED_SOURCE_EVENT_COORDINATES_CHANGED')
    boundary = max((item['target_accepted_sequence'] for item in coordinates
                    if item['source_accepted_sequence'] <= risk.execution_event_high_watermark), default=0)
    for sequence, event in events:
        original = originals.get(event['event_id'])
        if original is not None:
            source_event = {key: value for key, value in original.items() if key != 'accepted_sequence'}
            if mapped[event['event_id']]['target_accepted_sequence'] != sequence or event != source_event:
                raise RuntimeError('LEADER_SELECTED_SOURCE_NATIVE_EVENT_CHANGED')
        elif sequence <= boundary:
            raise RuntimeError('LEADER_SELECTED_SOURCE_NATIVE_EVENT_CHANGED')
    return boundary


def _require_mock_replacement_claim(cursor, ownership, value, p, *, new_intent_claim, now_provider=None):
    """Borrow the QUEUED transaction; never create a second order writer."""
    from kiwoom_monitor.application.mock_automation_execution import (
        mock_automation_decision_gate_from_dict, MOCK_AUTOMATION_REPLACEMENT_GATE_VERSION,
        MockAutomationGateStatus,
        _fixed_replacement_binding,
    )
    from kiwoom_monitor.domain.execution_activation import mock_automation_spec_from_dict
    claim = ownership['replacement_claim']
    if (set(claim) != {'gate_id', 'risk_snapshot_id', 'risk_reconciliation_revision'}
            or type(claim['risk_reconciliation_revision']) is not int
            or claim['risk_reconciliation_revision'] <= 0):
        raise RuntimeError('MOCK_REPLACEMENT_CLAIM_INVALID')
    lock = ' FOR UPDATE' if p == '%s' else ''
    def document(collection, key):
        cursor.execute(f'SELECT document_json FROM central_documents WHERE collection={p} AND owner={p} '
                       f'AND document_key={p}' + lock, (collection, value['account_ref'], key))
        row = cursor.fetchone()
        if not row:
            raise RuntimeError('MOCK_REPLACEMENT_NATIVE_PROOF_MISSING')
        return _json_document(row[0])
    fixed = mock_automation_decision_gate_from_dict(document('execution_mock_automation_approved_gates', value['intent_id']))
    gate = mock_automation_decision_gate_from_dict(document('execution_mock_automation_decision_gates', claim['gate_id']))
    if (gate.version != MOCK_AUTOMATION_REPLACEMENT_GATE_VERSION or gate.gate_id != claim['gate_id']
            or gate.status is not MockAutomationGateStatus.APPROVED_FOR_SINGLE_SUBMISSION
            or gate.spec_id != ownership['active_spec_id'] or gate.execution_run_id != ownership['run_id']
            or gate.account_ref != value['account_ref'] or gate.control_revision != ownership['control_revision']):
        raise RuntimeError('MOCK_REPLACEMENT_APPROVAL_MISMATCH')
    if (fixed.version != gate.version or fixed.status is not MockAutomationGateStatus.APPROVED_FOR_SINGLE_SUBMISSION
            or fixed.spec_id != gate.spec_id or fixed.admission_id != gate.admission_id
            or _fixed_replacement_binding(fixed) != _fixed_replacement_binding(gate)):
        raise RuntimeError('MOCK_REPLACEMENT_FIXED_APPROVAL_CHANGED')
    binding = gate.replacement_binding
    if any(value.get(key) != expected for key, expected in binding['child_payload'].items()):
        raise RuntimeError('MOCK_REPLACEMENT_FIXED_CHILD_CHANGED')
    spec = mock_automation_spec_from_dict(document('execution_mock_automation_specs', gate.spec_id))
    frozen = spec.unfilled_execution_policy
    if (frozen is None or frozen.policy_id != binding['operating_policy_id']
            or binding['attempt'] > frozen.maximum_reprices):
        raise RuntimeError('MOCK_REPLACEMENT_POLICY_NOT_ADMITTED')
    risk = document('execution_mock_automation_current_risk', value['account_ref'])
    if (risk.get('snapshot_id') != claim['risk_snapshot_id']
            or risk.get('reconciliation_revision') != claim['risk_reconciliation_revision']
            or risk.get('execution_run_id') != ownership['run_id']
            or gate.risk_snapshot_id != claim['risk_snapshot_id']
            or gate.risk_reconciliation_revision != claim['risk_reconciliation_revision']):
        raise RuntimeError('MOCK_REPLACEMENT_RISK_CHANGED')
    from kiwoom_monitor.application.mock_automation_risk import mock_automation_risk_snapshot_from_dict
    mock_automation_risk_snapshot_from_dict(risk)
    native = []
    for intent_id in (binding['root_intent_id'], binding['parent_intent_id']):
        cursor.execute(f'SELECT document_json FROM central_execution_intents WHERE intent_id={p}' + lock, (intent_id,))
        row = cursor.fetchone()
        if not row:
            raise RuntimeError('MOCK_REPLACEMENT_PARENT_MISSING')
        native.append(_json_document(row[0]))
    root, parent = native
    import hashlib
    expected_parent = (binding['root_intent_id'] if binding['attempt'] == 1 else 'unfilled-native-child:' + hashlib.sha256(
        f'{binding["root_intent_id"]}:{binding["attempt"] - 1}'.encode()).hexdigest())
    if (str(root.get('intent_id', '')).startswith('unfilled-native-child:')
            or binding['parent_intent_id'] != expected_parent
            or any(row.get(key) != value.get(key) for row in native for key in ('environment', 'account_ref', 'run_id', 'symbol', 'side'))
            or parent.get('state') != 'CANCELLED'
            or parent.get('filled_quantity') != parent.get('detailed_filled_quantity')
            or parent.get('broker_reported_filled_quantity', 0) > parent.get('detailed_filled_quantity', 0)
            or not parent.get('last_broker_as_of')
            or datetime.fromisoformat(parent['last_broker_as_of']) > datetime.fromisoformat(value['created_at'])
            or value['quantity'] > parent['quantity'] - parent['filled_quantity']):
        raise RuntimeError('MOCK_REPLACEMENT_PARENT_CANCEL_OR_FILLS_CHANGED')
    if binding['purpose'] in {'PROTECTIVE_EXIT', 'TAKE_PROFIT'}:
        _require_mock_unfilled_management_origin(cursor, ownership, value, p, now_provider=now_provider, spec=spec,
            root_value=root, risk_value=risk)
    cursor.execute(f'SELECT intent_id FROM central_execution_intents WHERE environment={p} AND account_ref={p} '
                   f"AND state NOT IN ('FILLED','CANCELLED','REJECTED','EXPIRED') AND intent_id<>{p} LIMIT 1" + lock,
                   ('mock', value['account_ref'], value['intent_id']))
    if cursor.fetchone():
        raise RuntimeError('MOCK_REPLACEMENT_ACCOUNT_ORDER_IN_FLIGHT')
    if new_intent_claim:
        cursor.execute(f'SELECT intent_id FROM central_execution_intents WHERE intent_id={p}' + lock, (value['intent_id'],))
        if cursor.fetchone():
            raise RuntimeError('MOCK_REPLACEMENT_ALREADY_CLAIMED')


def _require_staged_leader_protection_facts(cursor, p, value, pin, trigger, original_risk, risk, workflow, *, ownership=None):
    """Actual fills from every accepted lot/reduction, with the original stop cut."""
    from kiwoom_monitor.application.leader_order_management import leader_position_order_confirmation_time
    from kiwoom_monitor.infrastructure.persistence.execution_repository import _record_from_document, execution_broker_legs
    from kiwoom_monitor.domain.order_contract import TERMINAL_ORDER_STATES
    plan, account = value['plan'], value['account_ref']
    orders, pages = _leader_plan_ledger(cursor, p, account, value['run_id'], plan['plan_id'])
    original_cursor = _leader_native_risk_high_watermark(ownership, original_risk,
        [row for events in pages.values() for row in events])
    root = orders.get(pin['entry_intent_id']) or {}
    if (root.get('side') != 'BUY' or root.get('state') not in {'FILLED', 'CANCELLED'}
            or not root.get('detailed_filled_quantity') or not root.get('mock_plan_approval')):
        raise RuntimeError('STAGED_LEADER_PROTECTION_ORIGIN_UNCONFIRMED')
    fills, confirmations = [], {}
    for identity, document in orders.items():
        if identity == value['intent_id']:
            continue  # This protection reservation is validated by the shared final claim.
        record = _record_from_document(document)
        if (record.state not in TERMINAL_ORDER_STATES or record.intent.policy_version != 'leader_execution_adapter/v1'
                or record.intent.plan.policy_ref != pin['proof_id'] or record.intent.symbol != value['symbol']
                or record.filled_quantity != record.detailed_filled_quantity
                or record.filled_quantity != record.broker_reported_filled_quantity
                or any(leg.state not in TERMINAL_ORDER_STATES
                       or leg.detailed_filled_quantity != leg.broker_reported_filled_quantity
                       for leg in execution_broker_legs(record))):
            raise RuntimeError('STAGED_LEADER_PROTECTION_LOT_UNCONFIRMED')
        events = pages[identity]
        confirmations[identity] = leader_position_order_confirmation_time(record, tuple(row for _, row in events))
        detailed = [(sequence, row) for sequence, row in events if row['event_type'] == 'FILL']
        if sum(row['quantity'] for _, row in detailed) != record.detailed_filled_quantity:
            raise RuntimeError('STAGED_LEADER_PROTECTION_DETAIL_CHANGED')
        fills.extend((sequence, 1 if record.intent.side.value == 'BUY' else -1, row) for sequence, row in detailed)
    quantity = original_quantity = last_fill = 0
    seen = set()
    for sequence, sign, event in sorted(fills, key=lambda row: row[0]):
        identity = (event['intent_id'], event['broker_order_id'], event['broker_execution_id'])
        if (identity in seen or not identity[2] or event['quantity'] <= 0 or event['price'] <= 0
                or sequence > risk.execution_event_high_watermark):
            raise RuntimeError('STAGED_LEADER_PROTECTION_FILL_CUT_CHANGED')
        seen.add(identity)
        quantity += sign * event['quantity']
        if quantity < 0:
            raise RuntimeError('STAGED_LEADER_PROTECTION_NEGATIVE_POSITION')
        if sequence <= original_cursor:
            original_quantity = quantity
        last_fill = sequence
    head = _plan_document(cursor, p, PLAN_POSITION_COLLECTION, account, plan['plan_id']) or {}
    if (quantity <= 0 or (head.get('quantity'), head.get('fill_cursor')) != (quantity, last_fill)
            or head.get('unreconciled_sell_quantity')
            or original_quantity != trigger.stop_loss['quantity']):
        raise RuntimeError('STAGED_LEADER_PROTECTION_POSITION_CHANGED')
    available = datetime.fromisoformat(workflow['snapshot']['observation']['received_at'])
    if available < max(confirmations.values()):
        raise RuntimeError('STAGED_LEADER_PROTECTION_QUOTE_PREDATES_LOT_CONFIRMATION')
    return root['state'], confirmations[pin['entry_intent_id']], head, original_quantity


def _require_planned_leader_protection_claim(cursor, p, value, ownership, *, stored, now_provider, submission_claim):
    """Reproduce the two proof cuts while holding the existing account owner transaction."""
    from kiwoom_monitor.application.mock_automation_candidate import candidate_package_from_dict
    from kiwoom_monitor.application.mock_automation_risk import (
        PLANNED_LEADER_EXPIRED_ENTRY_CANCEL_VERSION, PROTECTION_EXIT_VERSION,
        PlannedLeaderCancelClaim, PlannedLeaderExpiredEntryCancelClaim, PlannedLeaderStop,
        mock_automation_protection_trigger, mock_automation_risk_snapshot_from_dict,
        prepare_planned_leader_protection_exit, validate_planned_leader_stop, planned_leader_root_confirmation_time,
    )
    from kiwoom_monitor.domain.execution_activation import mock_automation_spec_from_dict
    from kiwoom_monitor.domain.order_contract import OrderPlanRef
    from kiwoom_monitor.domain.strategy_contract import LEADER_BREAKOUT_FAMILY_ID, LeaderBreakoutConfig
    from kiwoom_monitor.infrastructure.persistence.execution_repository import _record_from_document, execution_broker_legs

    claim = ownership.get('protection_claim')
    recovery = ownership.get('plan_recovery')
    if (not isinstance(claim, dict) or set(claim) != {'workflow_id', 'gate_id', 'risk_snapshot_id', 'risk_reconciliation_revision'}
            or recovery is None or value.get('policy_version') != PROTECTION_EXIT_VERSION
            or value.get('side') != 'SELL'
            or (submission_claim and (stored is None or stored.get('mock_protection_claim') != claim))):
        raise RuntimeError('PLANNED_LEADER_PROTECTION_CLAIM_INVALID')
    account, run_id = value['account_ref'], value['run_id']
    plan = OrderPlanRef(**value['plan'])
    management = ownership.get('_native_selected_management_context')
    selected_terms = _selected_management_close_terms(ownership)

    def document(collection, key, owner=account):
        result = _plan_document(cursor, p, collection, owner, key, selected_management=management)
        if result is None:
            raise RuntimeError('PLANNED_LEADER_PROTECTION_PROOF_MISSING')
        return result

    spec = mock_automation_spec_from_dict(document('execution_mock_automation_specs', ownership['active_spec_id']))
    if value.get('venue') != spec.supported_venue:
        raise RuntimeError('PLANNED_LEADER_PROTECTION_VENUE_CHANGED')
    checkpoint = document('execution_mock_automation_runner_current', ownership.get('_native_checkpoint_key', 'current'))
    notice = checkpoint.get('protection_review') or {}
    workflow = notice.get('planned_leader_protection_exit') or {}
    if (workflow.get('workflow_id') != claim['workflow_id']
            or (checkpoint.get('spec_id'), checkpoint.get('execution_run_id')) != (spec.spec_id, run_id)
            or not isinstance(notice.get('planned_leader_stop'), dict)):
        raise RuntimeError('PLANNED_LEADER_PROTECTION_REQUEST_CHANGED')
    trigger = PlannedLeaderStop.from_dict(notice['planned_leader_stop'])
    pin = document('execution_leader_position_context', plan.plan_id)
    package = candidate_package_from_dict(document(
        'execution_mock_automation_candidate_packages', spec.candidate_package_hash, spec.strategy_ref,
    ))
    if package.package_hash != spec.candidate_package_hash or package.candidate_spec.get('family') != LEADER_BREAKOUT_FAMILY_ID:
        raise RuntimeError('PLANNED_LEADER_PROTECTION_PACKAGE_CHANGED')
    config = LeaderBreakoutConfig.from_dict(package.candidate_spec['parameters'])
    risk = mock_automation_risk_snapshot_from_dict(document('execution_mock_automation_current_risk', account))
    original_risk = mock_automation_risk_snapshot_from_dict(document(
        'execution_mock_automation_risk_snapshots', trigger.risk_snapshot_id,
    ))
    gate = document('execution_mock_automation_approved_gates', value['intent_id'])
    if ((gate['gate_id'], risk.snapshot_id, risk.reconciliation_revision)
            != (claim['gate_id'], claim['risk_snapshot_id'], claim['risk_reconciliation_revision'])
            or gate.get('replacement_binding') is not None or gate.get('plan') != value['plan']):
        raise RuntimeError('PLANNED_LEADER_PROTECTION_APPROVAL_CHANGED')

    from kiwoom_monitor.domain.strategy_contract import LeaderBreakoutStagedExecutionContract
    lock = ' FOR UPDATE' if p == '%s' else ''
    if isinstance(config.execution_contract, LeaderBreakoutStagedExecutionContract):
        root_state, confirmed_at, head, original_quantity = _require_staged_leader_protection_facts(
            cursor, p, value, pin, trigger, original_risk, risk, workflow, ownership=ownership)
    else:
        lock = ' FOR UPDATE' if p == '%s' else ''
        cursor.execute(f'SELECT document_json FROM central_execution_intents WHERE intent_id={p}' + lock, (plan.plan_id,))
        row = cursor.fetchone()
        root = _json_document(row[0]) if row else {}
        root = _selected_management_original_claim_view(cursor, p, root, ownership)
        root_plan = root.get('plan') or {}
        root_state = root.get('state')
        if ((root.get('environment'), root.get('account_ref'), root.get('run_id'), root.get('symbol'), root.get('side'),
             root.get('policy_version'), root_plan.get('plan_id'), root_plan.get('policy_ref'))
                != ('mock', account, run_id, value['symbol'], 'BUY', 'leader_execution_adapter/v1',
                    plan.plan_id, plan.policy_ref)
                or root_state not in {'CANCELLED', 'FILLED'}
                or not root.get('broker_order_id') or not root.get('mock_plan_approval')
                or not 0 < root['detailed_filled_quantity'] == root['broker_reported_filled_quantity']
                == root['filled_quantity'] <= root['quantity']
                or (root_state == 'FILLED') != (root['filled_quantity'] == root['quantity'])):
            raise RuntimeError('PLANNED_LEADER_PROTECTION_ROOT_UNCONFIRMED')
        cursor.execute(f'SELECT accepted_sequence,document_json FROM central_execution_events WHERE intent_id={p} '
                       'ORDER BY accepted_sequence', (plan.plan_id,))
        root_events = [(sequence, _json_document(raw)) for sequence, raw in cursor.fetchall()]
        original_cursor = _leader_native_risk_high_watermark(ownership, original_risk, root_events)
        current_management = spec.unfilled_execution_policy.accepted_order_management is not None
        if current_management:
            root_record = _record_from_document(root)
            confirmed_at = planned_leader_root_confirmation_time(root_record, tuple(row for _, row in root_events))
            legs = execution_broker_legs(root_record)
            orders = {order.broker_order_id: order for order in recovery.orders}
            if any(leg.broker_order_id not in orders or leg.state.value not in {'FILLED', 'CANCELLED'}
                   or (orders[leg.broker_order_id].account_ref, orders[leg.broker_order_id].symbol,
                       orders[leg.broker_order_id].state, orders[leg.broker_order_id].filled_quantity,
                       orders[leg.broker_order_id].remaining_quantity)
                   != (account, root['symbol'], leg.state, leg.filled_quantity, 0) for leg in legs):
                raise RuntimeError('PLANNED_LEADER_PROTECTION_PHYSICAL_CUT_CHANGED')
        else:
            confirmed_at = datetime.fromisoformat(root['last_broker_as_of'])
            if not any(order.broker_order_id == root['broker_order_id'] and order.state.value == root_state
                       and order.account_ref == account
                       and order.symbol == root['symbol'] and order.filled_quantity == root['detailed_filled_quantity']
                       and order.remaining_quantity == 0 and order.as_of == confirmed_at for order in recovery.orders):
                raise RuntimeError('PLANNED_LEADER_PROTECTION_BROKER_CUT_CHANGED')
        original_quantity = current_quantity = fill_cursor = cancel_sequence = unknown_sequence = 0
        expired_entry_cancel = False
        expiry_fill_quantity = 0
        fill_ids = []
        event, terminal_sequence = {}, 0
        cancelled_physical = set()
        for sequence, event in root_events:
            terminal_sequence = sequence
            if event['event_type'] == 'CANCEL_STARTED':
                if current_management:
                    current_cancel = event.get('leader_order_management_claim') or {}
                    if (current_cancel.get('version') != 'leader-current-order-management/v1'
                            or current_cancel.get('action') != 'CANCEL'
                            or (current_cancel.get('plan', {}).get('plan_id'), current_cancel.get('entry_proof_id'))
                            != (plan.plan_id, pin['proof_id']) or event['broker_order_id'] in cancelled_physical):
                        raise RuntimeError('PLANNED_LEADER_PROTECTION_CANCEL_PROOF_CHANGED')
                    cancelled_physical.add(event['broker_order_id'])
                    continue
                if 'planned_leader_cancel_claim' not in event or event['state'] != 'CANCEL_PENDING':
                    raise RuntimeError('PLANNED_LEADER_PROTECTION_CANCEL_PROOF_CHANGED')
                cancel_document = event['planned_leader_cancel_claim']
                expired_entry_cancel = cancel_document.get('version') == PLANNED_LEADER_EXPIRED_ENTRY_CANCEL_VERSION
                if expired_entry_cancel:
                    cancel = PlannedLeaderExpiredEntryCancelClaim.from_dict(cancel_document, recovery)
                    expiry_fill_quantity = cancel.detailed_quantity_at_cancel
                    same_stop = expiry_fill_quantity == current_quantity  # Existing FILLs at the expiry cut.
                else:
                    cancel = PlannedLeaderCancelClaim.from_dict(cancel_document)
                    same_stop = (cancel.trigger_id, cancel.notice_revision) == (trigger.trigger_id, notice['revision'])
                if (cancel_sequence or not same_stop or (cancel.plan.plan_id, cancel.plan.policy_ref)
                        != (plan.plan_id, plan.policy_ref)):
                    raise RuntimeError('PLANNED_LEADER_PROTECTION_CANCEL_PROOF_CHANGED')
                cancel_sequence = sequence
            if not current_management and root_state == 'FILLED' and event['event_type'] in {
                'CANCEL_RESPONSE_UNKNOWN', 'CANCEL_ACCEPTED', 'CANCEL_REJECTED',
            }:
                if (not cancel_sequence or unknown_sequence or event['event_type'] != 'CANCEL_RESPONSE_UNKNOWN'
                        or event['state'] != 'CANCEL_PENDING'):
                    raise RuntimeError('PLANNED_LEADER_PROTECTION_CANCEL_PROOF_CHANGED')
                unknown_sequence = sequence
            if event['event_type'] == 'FILL' and event['broker_execution_id']:
                current_quantity += event['quantity']
                fill_ids.append(event['broker_execution_id'])
                fill_cursor = sequence
                if sequence <= original_cursor:
                    original_quantity += event['quantity']
        head = document(PLAN_POSITION_COLLECTION, plan.plan_id)
        if current_management:
            terminal_confirmed = (0 < fill_cursor < terminal_sequence <= risk.execution_event_high_watermark
                                  and event.get('state') == root_state and event.get('event_type') == 'BROKER_RECONCILED')
        elif expired_entry_cancel:
            # An expiry claim cannot arm a stop. The new stop must follow the
            # broker-confirmed terminal root and the Risk must already own its fills.
            terminal_confirmed = (
                cancel_sequence < terminal_sequence <= original_cursor
                and 0 < fill_cursor <= terminal_sequence and (expiry_fill_quantity > 0 or cancel_sequence < fill_cursor)
                and confirmed_at < datetime.fromisoformat(trigger.stop_loss['available_at'])
                and event.get('event_type') in ({'BROKER_RECONCILED'} if root_state == 'CANCELLED' else {'FILL', 'BROKER_RECONCILED'})
                and (root_state == 'CANCELLED' or cancel_sequence < unknown_sequence < fill_cursor)
            )
        elif root_state == 'CANCELLED':
            terminal_confirmed = (original_cursor < cancel_sequence < terminal_sequence
                                  and event.get('event_type') == 'BROKER_RECONCILED')
        else:
            terminal_confirmed = (original_cursor < fill_cursor <= terminal_sequence
                                  and event.get('event_type') in {'FILL', 'BROKER_RECONCILED'}
                                  and (not cancel_sequence or original_cursor < cancel_sequence < unknown_sequence < fill_cursor))
        if (not terminal_confirmed or terminal_sequence > risk.execution_event_high_watermark
                or event.get('state') != root_state
                or (event.get('broker_order_id') not in {leg.broker_order_id for leg in legs} if current_management
                    else event.get('broker_order_id') != root['broker_order_id'])
                or datetime.fromisoformat(event['broker_as_of']) != (
                    datetime.fromisoformat(root['last_broker_as_of']) if current_management else confirmed_at)
                or (current_quantity, fill_ids, fill_cursor) != (root['detailed_filled_quantity'], root['fill_ids'], head['fill_cursor'])
                or head['quantity'] != current_quantity):
            raise RuntimeError('PLANNED_LEADER_PROTECTION_DETAILED_FILLS_UNCONFIRMED')
    validate_planned_leader_stop(trigger, spec=spec, leader_config=config, position_proof=pin,
        original_risk=original_risk, original_fill_quantity=original_quantity,
        selected_management_context=(None if original_risk.snapshot_id in selected_terms.get('source_risk_snapshot_ids', ())
                                     else selected_terms.get('selected_management_context')))
    now = kst_now() if now_provider is None else now_provider()
    active_trigger = mock_automation_protection_trigger(spec, risk, execution_run_id=run_id, now=now,
        selected_management_context=selected_terms.get('selected_management_context'))
    if active_trigger is not None and active_trigger['disposition'] != 'EXIT_REQUIRED':
        raise RuntimeError('PLANNED_LEADER_PROTECTION_RISK_CHANGED')
    fixed = prepare_planned_leader_protection_exit(
        spec, run_id=run_id, control_revision=ownership['control_revision'], notice=notice, plan=plan,
        observation=workflow['snapshot']['observation'], risk=risk, plan_position=head, leader_config=config,
        position_proof=pin, confirmed_root_state=root_state, confirmed_root_at=confirmed_at, now=now,
        reservation_claimed=submission_claim,
        selected_management_context=selected_terms.get('selected_management_context'),
        route_quote=workflow['snapshot'].get('route_quote'),
    )
    decision = fixed['decision']
    if (fixed != workflow or (value['decision_id'], value['symbol'], value['quantity'], value['limit_price'])
            != (decision['decision_id'], decision['symbol'], decision['quantity'], decision['signal_reference_price'])):
        raise RuntimeError('PLANNED_LEADER_PROTECTION_FIXED_CUT_CHANGED')
    plan_key = "document_json->'plan'->>'plan_id'" if p == '%s' else "json_extract(document_json,'$.plan.plan_id')"
    policy = "document_json->>'policy_version'" if p == '%s' else "json_extract(document_json,'$.policy_version')"
    active_scope = "state NOT IN ('FILLED','CANCELLED','REJECTED','EXPIRED')"
    active_params = ()
    selection = ownership.get('_native_account_selection')
    if selection is not None:
        peers = tuple(member for member in selection['member_spec_ids'] if member != spec.spec_id)
        if peers:
            member_key = ("document_json->>'mock_account_member_spec_id'" if p == '%s'
                          else "json_extract(document_json,'$.mock_account_member_spec_id')")
            active_scope += (f" AND NOT (run_id={p} AND state IN ('ACCEPTED','PARTIALLY_FILLED') "
                             f"AND COALESCE({member_key},'') IN ({','.join([p] * len(peers))}))")
            active_params = (run_id, *peers)
    cursor.execute(f"SELECT 1 FROM central_execution_intents WHERE environment='mock' AND account_ref={p} "
                   f"AND intent_id<>{p} AND (({active_scope}) "
                   f"OR (run_id={p} AND {plan_key}={p} AND {policy}={p})) LIMIT 1" + lock,
                   (account, value['intent_id'], *active_params, run_id, plan.plan_id, PROTECTION_EXIT_VERSION))
    if cursor.fetchone() is not None:
        raise RuntimeError('PLANNED_LEADER_PROTECTION_FIRST_EXIT_UNAVAILABLE')


def _require_mock_protection_claim(cursor, ownership, value, p, *, now_provider=None,new_intent_claim=False):
    """Validate the first protective EXIT in the existing native QUEUED transaction."""
    from kiwoom_monitor.application.mock_automation_execution import mock_automation_decision_gate_from_dict, MockAutomationGateStatus
    from kiwoom_monitor.application.mock_automation_risk import (
        PROTECTION_EXIT_VERSION, validate_mock_protection_exit, mock_automation_risk_snapshot_from_dict,
        mock_automation_protection_trigger,
    )
    from kiwoom_monitor.domain.execution_activation import mock_automation_spec_from_dict
    claim=ownership['protection_claim'];lock=' FOR UPDATE' if p=='%s' else ''
    if (set(claim) != {'workflow_id','gate_id','risk_snapshot_id','risk_reconciliation_revision'}
            or type(claim['risk_reconciliation_revision']) is not int or claim['risk_reconciliation_revision'] <= 0):
        raise RuntimeError('MOCK_PROTECTION_CLAIM_INVALID')
    def document(collection,key):
        cursor.execute(f'SELECT document_json FROM central_documents WHERE collection={p} AND owner={p} AND document_key={p}'+lock,
            (collection,value['account_ref'],key))
        row=cursor.fetchone()
        if not row:raise RuntimeError('MOCK_PROTECTION_NATIVE_PROOF_MISSING')
        return _json_document(row[0])
    spec=mock_automation_spec_from_dict(document('execution_mock_automation_specs',ownership['active_spec_id']))
    checkpoint=document('execution_mock_automation_runner_current',ownership.get('_native_checkpoint_key', 'current'))
    notice=checkpoint.get('protection_review') or {}
    if notice.get('planned_leader_stop') is not None:
        raise RuntimeError('MOCK_PROTECTION_REQUIRES_PLANNED_LEADER_CLAIM')
    workflow=checkpoint.get('protection_exit')
    decision=validate_mock_protection_exit(workflow,spec,ownership['run_id'])
    snapshot=workflow['snapshot']
    if (workflow['workflow_id'] != claim['workflow_id'] or notice.get('state') != 'PENDING'
            or notice.get('revision') != snapshot['request_revision'] or notice.get('input_ref') != snapshot['request_input_ref']
            or notice.get('risk_trigger') != snapshot['request_trigger']
            or snapshot['control_revision'] != ownership['control_revision']
            or checkpoint.get('spec_id') != spec.spec_id or checkpoint.get('execution_run_id') != ownership['run_id']):
        raise RuntimeError('MOCK_PROTECTION_REQUEST_CHANGED')
    gate=mock_automation_decision_gate_from_dict(document('execution_mock_automation_approved_gates',value['intent_id']))
    if (gate.gate_id != claim['gate_id'] or gate.replacement_binding is not None
            or gate.status is not MockAutomationGateStatus.APPROVED_FOR_SINGLE_SUBMISSION
            or gate.spec_id != spec.spec_id or gate.account_ref != value['account_ref']
            or gate.execution_run_id != ownership['run_id'] or gate.control_revision != ownership['control_revision']
            or gate.action != 'EXIT' or gate.strategy_decision_id != decision.decision_id
            or gate.intent_id != value['intent_id'] or value.get('decision_id') != decision.decision_id
            or value.get('policy_version') != PROTECTION_EXIT_VERSION or value.get('side') != 'SELL'
            or value.get('venue') != spec.supported_venue or value.get('order_type') != 'LIMIT'
            or value.get('symbol') != decision.symbol or value.get('quantity') != decision.quantity
            or value.get('limit_price') != decision.signal_reference_price
            or gate.quantity != decision.quantity or gate.limit_price != decision.signal_reference_price
            or gate.symbol != decision.symbol or gate.decided_at != datetime.fromisoformat(decision.decided_at)
            or datetime.fromisoformat(value['created_at']) != datetime.fromisoformat(decision.decided_at)
            or datetime.fromisoformat(value['expires_at']) != datetime.fromisoformat(decision.decided_at)+timedelta(seconds=max(1,int(spec.maximum_data_gap_seconds or 1)))):
        raise RuntimeError('MOCK_PROTECTION_FIXED_APPROVAL_CHANGED')
    risk=mock_automation_risk_snapshot_from_dict(document('execution_mock_automation_current_risk',value['account_ref']))
    if 'stop_loss' in snapshot['request_trigger']:
        from kiwoom_monitor.application.mock_automation_risk import validate_mock_stop_loss_proof
        from kiwoom_monitor.application.research_families import parse_strategy_config
        from kiwoom_monitor.application.mock_automation_candidate import candidate_package_from_dict
        cursor.execute(f'SELECT document_json FROM central_documents WHERE collection={p} AND owner={p} AND document_key={p}'+lock,
            ('execution_mock_automation_candidate_packages',spec.strategy_ref,spec.candidate_package_hash))
        row=cursor.fetchone()
        if not row:raise RuntimeError('MOCK_STOP_LOSS_CANDIDATE_MISSING')
        package=candidate_package_from_dict(_json_document(row[0]))
        if package.package_hash != spec.candidate_package_hash:
            raise RuntimeError('MOCK_STOP_LOSS_CANDIDATE_CHANGED')
        config=parse_strategy_config(package.candidate_spec['family'],package.candidate_spec['parameters'])
        from kiwoom_monitor.application.leader_breakout import LeaderBreakoutConfig
        if isinstance(config, LeaderBreakoutConfig):
            proof = snapshot['request_trigger']['stop_loss']
            position = proof.get('position_proof', {})
            cursor.execute(f'SELECT document_json FROM central_documents WHERE collection={p} AND owner={p} AND document_key={p}'+lock,
                ('execution_leader_position_context', value['account_ref'], position.get('entry_intent_id', '')))
            row = cursor.fetchone()
            if not row:
                raise RuntimeError('LEADER_STOP_ENTRY_CONTEXT_MISSING')
            validate_mock_stop_loss_proof(proof, spec, risk, leader_config=config, position_proof=_json_document(row[0]))
        else:
            validate_mock_stop_loss_proof(snapshot['request_trigger']['stop_loss'],spec,risk,stop_loss_bps=config.stop_loss_bps)
        if datetime.fromisoformat(snapshot['price_available_at']) > risk.observed_at:
            raise RuntimeError('MOCK_STOP_LOSS_ACCOUNT_CUT_NOT_ALIGNED')
    now=kst_now() if now_provider is None else now_provider()
    age=min(spec.maximum_data_gap_seconds,spec.unfilled_execution_policy.max_snapshot_age_seconds)
    trigger=mock_automation_protection_trigger(spec,risk,execution_run_id=ownership['run_id'],now=now)
    if (risk.snapshot_id != claim['risk_snapshot_id'] or risk.reconciliation_revision != claim['risk_reconciliation_revision']
            or gate.risk_snapshot_id != risk.snapshot_id or gate.risk_reconciliation_revision != risk.reconciliation_revision
            or risk.account_ref != value['account_ref'] or risk.execution_run_id != ownership['run_id']
            or gate.account_as_of != risk.account_as_of or not risk.reconciliation_complete
            or risk.submission_unknown_count or risk.balance_mismatch_count or risk.pending_sell_quantities
            or dict(risk.owned_position_quantities) != {decision.symbol:decision.quantity}
            or trigger is not None and trigger['disposition'] != 'EXIT_REQUIRED'
            or any(not 0 <= (now-at).total_seconds() <= age for at in (risk.observed_at,risk.account_as_of,
                datetime.fromisoformat(snapshot['price_available_at']),datetime.fromisoformat(decision.decided_at)))
            or now >= datetime.fromisoformat(value['expires_at'])):
        raise RuntimeError('MOCK_PROTECTION_RISK_OR_FIXED_CUT_CHANGED')
    cursor.execute(f'SELECT intent_id FROM central_execution_intents WHERE environment={p} AND account_ref={p} '
        f"AND state NOT IN ('FILLED','CANCELLED','REJECTED','EXPIRED') AND intent_id<>{p} LIMIT 1"+lock,
        ('mock',value['account_ref'],value['intent_id']))
    if cursor.fetchone():raise RuntimeError('MOCK_PROTECTION_ACCOUNT_ORDER_IN_FLIGHT')
    if new_intent_claim:
        cursor.execute(f'SELECT intent_id FROM central_execution_intents WHERE intent_id={p}'+lock,(value['intent_id'],))
        if cursor.fetchone():raise RuntimeError('MOCK_PROTECTION_ALREADY_CLAIMED')


def _retain_execution_claim(cursor, intent, p):
    """Keep the QUEUED proof in the existing mutable intent JSON on later events."""
    if intent.get('policy_version') not in {'unfilled-native-attempt-plan/v1','mock-protection-exit/v1'}:
        return intent
    cursor.execute(f'SELECT document_json FROM central_execution_intents WHERE intent_id={p}'
                   + (' FOR UPDATE' if p == '%s' else ''), (intent['intent_id'],))
    row = cursor.fetchone()
    stored = _json_document(row[0]) if row else {}
    return {**intent, **{key:stored[key] for key in ('mock_replacement_claim','mock_protection_claim') if key in stored}}


def _require_mock_unfilled_management_origin(cursor, ownership, value, p, *, now_provider=None,
                                              spec=None, checkpoint=None, root_value=None, risk_value=None):
    """Keep original enrollment and current management proof in one native claim."""
    from kiwoom_monitor.application.mock_automation_execution import (
        MOCK_AUTOMATION_DECISION_GATE_VERSION, mock_automation_decision_gate_from_dict,
        validate_unfilled_original_approval, validate_unfilled_original_gate, mock_automation_intent_id,
        MOCK_AUTOMATION_REPLACEMENT_GATE_VERSION, MockAutomationGateStatus, _fixed_replacement_binding,
    )
    from kiwoom_monitor.application.order_lifecycle import (
        validate_unfilled_attempt_plan, UnfilledOrderManagementPolicy, UNFILLED_ATTEMPT_PLAN_VERSION, unfilled_plan_intent,
    )
    from kiwoom_monitor.application.mock_automation_risk import mock_automation_risk_snapshot_from_dict
    from kiwoom_monitor.application.mock_automation_admission import mock_automation_admission_from_dict
    from kiwoom_monitor.domain.execution_activation import mock_automation_spec_from_dict
    from kiwoom_monitor.infrastructure.persistence.execution_repository import _record_from_document
    if not ownership or ownership.get('control_revision') is None or value.get('side') not in {'BUY', 'SELL'}:
        return
    source = root_value if root_value is not None else value
    child_control_revision = None
    original_source = (str(source.get('policy_version', '')).startswith(MOCK_AUTOMATION_DECISION_GATE_VERSION + ':')
        and source.get('intent_id') == mock_automation_intent_id(ownership['active_spec_id'], source.get('decision_id', '')))
    if (not original_source and source.get('policy_version') != UNFILLED_ATTEMPT_PLAN_VERSION
            and root_value is None):
        return False  # Existing unregistered/manual native orders retain their cancel contract.
    lock = ' FOR UPDATE' if p == '%s' else ''
    def document(collection, key, *, required=True):
        cursor.execute(f'SELECT document_json FROM central_documents WHERE collection={p} AND owner={p} '
            f'AND document_key={p}' + lock, (collection, value['account_ref'], key))
        row = cursor.fetchone()
        if not row and required:
            raise RuntimeError('MOCK_UNFILLED_MANAGEMENT_PROOF_MISSING')
        return _json_document(row[0]) if row else None
    def native_value(intent_id):
        cursor.execute(f'SELECT document_json FROM central_execution_intents WHERE intent_id={p}' + lock, (intent_id,))
        row = cursor.fetchone()
        if not row:
            raise RuntimeError('MOCK_UNFILLED_MANAGEMENT_ROOT_MISSING')
        return _json_document(row[0])
    if root_value is None and source.get('policy_version') == UNFILLED_ATTEMPT_PLAN_VERSION:
        child = native_value(value['intent_id'])
        claim = child.get('mock_replacement_claim')
        fixed_value = document('execution_mock_automation_approved_gates', value['intent_id'], required=claim is not None)
        if claim is None:
            if fixed_value is not None:
                raise RuntimeError('MOCK_UNFILLED_MANAGEMENT_CHILD_APPROVAL_CHANGED')
            return False  # Existing manually submitted preview child has no native v3 claim.
        fixed = mock_automation_decision_gate_from_dict(fixed_value)
        submitted = mock_automation_decision_gate_from_dict(document('execution_mock_automation_decision_gates', claim['gate_id']))
        if (fixed.version != MOCK_AUTOMATION_REPLACEMENT_GATE_VERSION or submitted.version != fixed.version
                or fixed.status is not MockAutomationGateStatus.APPROVED_FOR_SINGLE_SUBMISSION
                or submitted.status is not MockAutomationGateStatus.APPROVED_FOR_SINGLE_SUBMISSION
                or fixed.spec_id != submitted.spec_id or fixed.admission_id != submitted.admission_id
                or _fixed_replacement_binding(fixed) != _fixed_replacement_binding(submitted)
                or (submitted.account_ref, submitted.spec_id, submitted.execution_run_id) !=
                    (value['account_ref'], ownership['active_spec_id'], ownership['run_id'])
                or submitted.gate_id != claim['gate_id']
                or submitted.risk_snapshot_id != claim['risk_snapshot_id']
                or submitted.risk_reconciliation_revision != claim['risk_reconciliation_revision']
                or unfilled_plan_intent(submitted.replacement_binding['child_payload']) != _record_from_document(child).intent
                or _record_from_document(child).intent != _record_from_document(value).intent):
            raise RuntimeError('MOCK_UNFILLED_MANAGEMENT_CHILD_APPROVAL_CHANGED')
        if submitted.control_revision == ownership['control_revision']:
            return True  # The child's immutable native claim is already current.
        if not 0 < submitted.control_revision < ownership['control_revision']:
            raise RuntimeError('MOCK_UNFILLED_MANAGEMENT_CURRENT_CONTROL_CHANGED')
        child_control_revision = submitted.control_revision
        root_value = native_value(submitted.replacement_binding['root_intent_id'])
        source = root_value
        original_source = (str(source.get('policy_version', '')).startswith(MOCK_AUTOMATION_DECISION_GATE_VERSION + ':')
            and source.get('intent_id') == mock_automation_intent_id(ownership['active_spec_id'], source.get('decision_id', '')))
        if not original_source:
            raise RuntimeError('MOCK_UNFILLED_MANAGEMENT_CHILD_APPROVAL_CHANGED')
    if spec is None:
        spec = mock_automation_spec_from_dict(document('execution_mock_automation_specs', ownership['active_spec_id']))
    if spec.unfilled_execution_policy is None:
        return False
    if not original_source:
        if checkpoint is None:
            checkpoint = document('execution_mock_automation_runner_current', ownership.get('_native_checkpoint_key', 'current'))
        plan = validate_unfilled_attempt_plan(checkpoint.get('unfilled_execution_preflight'))
    if root_value is None and not original_source:
        root_value = native_value(plan['anchor']['root_intent']['intent_id'])
    root = _record_from_document(root_value if root_value is not None else value)
    if (not root.intent.policy_version.startswith(MOCK_AUTOMATION_DECISION_GATE_VERSION + ':')
            or root.intent.intent_id != mock_automation_intent_id(ownership['active_spec_id'], root.intent.decision_id)):
        if plan['anchor']['scope']['control_revision'] != ownership['control_revision']:
            raise RuntimeError('MOCK_PROTECTIVE_CANCEL_RETRY_POLICY_CHANGED')
        return False  # Earlier direct-ledger orders retain only their existing same-revision contract.
    original = mock_automation_decision_gate_from_dict(document('execution_mock_automation_approved_gates', root.intent.intent_id))
    validate_unfilled_original_gate(spec, root, original, account_ref=value['account_ref'],
        execution_run_id=ownership['run_id'], admission_id=original.admission_id,
        control_revision=original.control_revision)
    if child_control_revision is not None and original.control_revision > child_control_revision:
        raise RuntimeError('MOCK_UNFILLED_MANAGEMENT_CHILD_APPROVAL_CHANGED')
    if child_control_revision is None and original.control_revision == ownership['control_revision']:
        # Preserve standalone native v2 and no-minute protection. Their original
        # same-revision contract does not require a registered checkpoint.
        return True
    if checkpoint is None:
        checkpoint = document('execution_mock_automation_runner_current', ownership.get('_native_checkpoint_key', 'current'))
    plan = validate_unfilled_attempt_plan(checkpoint.get('unfilled_execution_preflight'))
    if root_value is None:
        root = _record_from_document(native_value(root.intent.intent_id))
    scope = plan['anchor']['scope']
    if unfilled_plan_intent(plan['anchor']['root_intent']) != root.intent:
        raise RuntimeError('MOCK_UNFILLED_MANAGEMENT_ROOT_CHANGED')
    policy_value = dict(plan['anchor']['policy'])
    for name in ('anchor_available_at', 'valid_until', 'wait_until', 'protection_deadline'):
        policy_value[name] = datetime.fromisoformat(policy_value[name])
    policy = UnfilledOrderManagementPolicy(**policy_value)
    preview = mock_automation_decision_gate_from_dict(plan['native_gate'])
    admission = mock_automation_admission_from_dict(document('execution_mock_automation_admission_by_spec', spec.spec_id))
    validate_unfilled_original_approval(spec, scope, policy, root, original, checkpoint.get('unfilled_registration'),
        admission_id=admission.admission_id, current_control_revision=ownership['control_revision'])
    managed_ids = (root.intent.intent_id, *(child['intent']['intent_id'] for child in plan['children']))
    if (value['intent_id'] not in managed_ids or checkpoint.get('spec_id') != spec.spec_id
            or checkpoint.get('execution_run_id') != ownership['run_id']
            or (preview.account_ref, preview.spec_id, preview.execution_run_id,
                preview.admission_id, preview.control_revision) !=
                (value['account_ref'], spec.spec_id, ownership['run_id'], admission.admission_id, ownership['control_revision'])
            or preview.lease_receipt_id != original.lease_receipt_id):
        raise RuntimeError('MOCK_UNFILLED_MANAGEMENT_CURRENT_CONTROL_CHANGED')
    if (admission.account_ref, admission.spec_id, admission.execution_run_id) != (
            value['account_ref'], spec.spec_id, ownership['run_id']):
        raise RuntimeError('MOCK_UNFILLED_MANAGEMENT_ADMISSION_CHANGED')
    if policy.purpose == 'BUY_OPPORTUNITY':
        # BUY keeps the original mapped-account/Risk callback, including the
        # known-flat cancellation exception. This proof grants no replacement.
        return True  # Original approval proved this is the ordinary profit/protection path.
    if risk_value is None:
        risk_value = document('execution_mock_automation_current_risk', value['account_ref'])
    risk = mock_automation_risk_snapshot_from_dict(risk_value)
    now = kst_now() if now_provider is None else now_provider()
    if (risk.account_ref != value['account_ref'] or risk.execution_run_id != ownership['run_id']
            or not risk.reconciliation_complete or risk.submission_unknown_count or risk.balance_mismatch_count
            or any(not 0 <= (now-at).total_seconds() <= spec.unfilled_execution_policy.max_snapshot_age_seconds
                for at in (risk.observed_at, risk.account_as_of))):
        raise RuntimeError('MOCK_UNFILLED_MANAGEMENT_NATIVE_CUT_CHANGED')
    return True


def _require_planned_leader_cancel(cursor, ownership, value, event, p, *, stored, now_provider):
    """Borrow the existing cancel event transaction for one planned partial BUY."""
    stored = _selected_management_original_claim_view(cursor, p, stored, ownership)
    from kiwoom_monitor.application.mock_automation_candidate import candidate_package_from_dict
    from kiwoom_monitor.application.mock_automation_risk import (
        PlannedLeaderCancelClaim,
        PlannedLeaderStop,
        mock_automation_risk_snapshot_from_dict,
        validate_planned_leader_current_cut,
        validate_planned_leader_stop,
    )
    from kiwoom_monitor.domain.execution_activation import mock_automation_spec_from_dict
    from kiwoom_monitor.domain.strategy_contract import LEADER_BREAKOUT_FAMILY_ID, LeaderBreakoutConfig

    claim = PlannedLeaderCancelClaim.from_dict(ownership["planned_cancel_claim"])
    if (stored is None
            or event.get("event_type") != "CANCEL_STARTED" or value["state"] != "CANCEL_PENDING"
            or event.get("intent_id") != value["intent_id"] or ownership.get("cancel_retry_claim") is not None
            or stored["state"] != "PARTIALLY_FILLED" or stored["side"] != "BUY" or not stored["broker_order_id"]
            or stored["policy_version"] != "leader_execution_adapter/v1" or not stored.get("mock_plan_approval")
            or stored["intent_id"] != claim.plan.plan_id
            or not 0 < stored["detailed_filled_quantity"] == stored["filled_quantity"] < stored["quantity"]
            or (stored.get("plan") or {}).get("plan_id") != claim.plan.plan_id
            or (stored.get("plan") or {}).get("policy_ref") != claim.plan.policy_ref):
        raise RuntimeError("PLANNED_LEADER_CANCEL_ROOT_CHANGED")
    account = value["account_ref"]
    management = ownership.get('_native_selected_management_context')
    selected_terms = _selected_management_close_terms(ownership)

    def document(collection, key, owner=account):
        row = _plan_document(cursor, p, collection, owner, key, selected_management=management)
        if row is None:
            raise RuntimeError("PLANNED_LEADER_CANCEL_PROOF_MISSING")
        return row

    spec = mock_automation_spec_from_dict(document("execution_mock_automation_specs", ownership["active_spec_id"]))
    checkpoint = document("execution_mock_automation_runner_current", ownership.get("_native_checkpoint_key", "current"))
    notice = checkpoint.get("protection_review") or {}
    if ((checkpoint.get("spec_id"), checkpoint.get("execution_run_id")) != (spec.spec_id, value["run_id"])
            or (notice.get("spec_id"), notice.get("account_ref"), notice.get("execution_run_id"))
            != (spec.spec_id, account, value["run_id"])
            or notice.get("state") != "PENDING" or notice.get("revision") != claim.notice_revision
            or notice.get("order_authorized") is not False or notice.get("transport_state") != "OFF"
            or not isinstance(notice.get("planned_leader_stop"), dict)):
        raise RuntimeError("PLANNED_LEADER_CANCEL_NOTICE_CHANGED")
    trigger = PlannedLeaderStop.from_dict(notice["planned_leader_stop"])
    if (trigger.trigger_id != claim.trigger_id or notice.get("input_ref") != trigger.stop_loss.get("input_ref")
            or (trigger.plan_id, trigger.policy_ref) != (claim.plan.plan_id, claim.plan.policy_ref)):
        raise RuntimeError("PLANNED_LEADER_CANCEL_TRIGGER_CHANGED")
    pin = document("execution_leader_position_context", claim.plan.plan_id)
    package = candidate_package_from_dict(document(
        "execution_mock_automation_candidate_packages", spec.candidate_package_hash, spec.strategy_ref,
    ))
    if package.package_hash != spec.candidate_package_hash or package.candidate_spec.get("family") != LEADER_BREAKOUT_FAMILY_ID:
        raise RuntimeError("PLANNED_LEADER_CANCEL_PACKAGE_CHANGED")
    config = LeaderBreakoutConfig.from_dict(package.candidate_spec["parameters"])
    original_risk = mock_automation_risk_snapshot_from_dict(document(
        "execution_mock_automation_risk_snapshots", trigger.risk_snapshot_id,
    ))
    risk = mock_automation_risk_snapshot_from_dict(document("execution_mock_automation_current_risk", account))
    if (risk.snapshot_id, risk.reconciliation_revision) != (claim.risk_snapshot_id, claim.risk_reconciliation_revision):
        raise RuntimeError("PLANNED_LEADER_CANCEL_RISK_CHANGED")
    cursor.execute(
        f"SELECT accepted_sequence,document_json FROM central_execution_events WHERE intent_id={p} "
        "ORDER BY accepted_sequence",
        (stored["intent_id"],),
    )
    events = [(sequence, _json_document(event_doc)) for sequence, event_doc in cursor.fetchall()]
    original_cursor = _leader_native_risk_high_watermark(ownership, original_risk, events)
    original_quantity = 0
    for sequence, proof in events:
        if proof["event_type"] == "CANCEL_STARTED":
            raise RuntimeError("PLANNED_LEADER_CANCEL_ALREADY_CLAIMED")
        if (sequence <= original_cursor and proof["event_type"] == "FILL"
                and proof["broker_execution_id"]):
            original_quantity += proof["quantity"]
    validate_planned_leader_stop(
        trigger, spec=spec, leader_config=config, position_proof=pin, original_risk=original_risk,
        original_fill_quantity=original_quantity,
        selected_management_context=(None if original_risk.snapshot_id in selected_terms.get('source_risk_snapshot_ids', ())
                                     else selected_terms.get('selected_management_context')),
    )
    quantity = validate_planned_leader_current_cut(
        claim.plan, spec=spec, run_id=value["run_id"], symbol=value["symbol"], risk=risk,
        plan_position=document(PLAN_POSITION_COLLECTION, claim.plan.plan_id),
        now=kst_now() if now_provider is None else now_provider(),
        selected_management_context=selected_terms.get('selected_management_context'),
    )
    if quantity != stored["detailed_filled_quantity"]:
        raise RuntimeError("PLANNED_LEADER_CANCEL_QUANTITY_CHANGED")
    return {**event, "planned_leader_cancel_claim": claim.to_dict()}


def _require_planned_leader_expiry_cancel(cursor, ownership, value, event, p, *, stored, now_provider):
    """Cancel the verified remainder of the original BUY after its admitted wait expires."""
    stored = _selected_management_original_claim_view(cursor, p, stored, ownership)
    from kiwoom_monitor.application.leader_breakout import _id
    from kiwoom_monitor.application.mock_automation_execution import (
        MockAutomationGateStatus, _validate_decision_identity, mock_automation_decision_gate_from_dict,
    )
    from kiwoom_monitor.application.mock_automation_risk import (
        PlannedLeaderExpiredEntryCancelClaim, mock_automation_risk_account_revision,
        mock_automation_risk_snapshot_from_dict, validate_planned_leader_current_cut,
    )
    from kiwoom_monitor.application.order_lifecycle import anchored_unfilled_policy
    from kiwoom_monitor.domain.execution_activation import mock_automation_spec_from_dict
    from kiwoom_monitor.domain.order_contract import TERMINAL_ORDER_STATES
    from kiwoom_monitor.domain.strategy_contract import StrategyDecision
    from kiwoom_monitor.infrastructure.persistence.execution_repository import _record_from_document

    claim = PlannedLeaderExpiredEntryCancelClaim.from_dict(ownership["planned_cancel_claim"], ownership.get("plan_recovery"))
    quantity = claim.detailed_quantity_at_cancel
    expected_state = "PARTIALLY_FILLED" if quantity else "ACCEPTED"
    if (stored is None or event.get("event_type") != "CANCEL_STARTED" or value["state"] != "CANCEL_PENDING"
            or event.get("intent_id") != value["intent_id"] or ownership.get("control_revision") is None
            or stored["state"] != expected_state or stored["side"] != "BUY" or not stored["broker_order_id"]
            or stored["policy_version"] != "leader_execution_adapter/v1" or not stored.get("mock_plan_approval")
            or stored["intent_id"] != claim.plan.plan_id or quantity >= stored["quantity"]
            or any(stored[name] != quantity for name in ("filled_quantity", "detailed_filled_quantity", "broker_reported_filled_quantity"))
            or (stored.get("plan") or {}).get("plan_id") != claim.plan.plan_id
            or (stored.get("plan") or {}).get("policy_ref") != claim.plan.policy_ref):
        raise RuntimeError("PLANNED_LEADER_EXPIRY_ROOT_CHANGED")
    account, run_id = value["account_ref"], value["run_id"]
    management = ownership.get('_native_selected_management_context')
    selected_context = management['context'] if management is not None else None

    def document(collection, key):
        row = _plan_document(cursor, p, collection, account, key, selected_management=management)
        if row is None:
            raise RuntimeError("PLANNED_LEADER_EXPIRY_PROOF_MISSING")
        return row

    spec = mock_automation_spec_from_dict(document("execution_mock_automation_specs", ownership["active_spec_id"]))
    gate = mock_automation_decision_gate_from_dict(document("execution_mock_automation_approved_gates", claim.plan.plan_id))
    root = _record_from_document(stored)
    source = document("execution_leader_decisions", root.intent.intent_id
                      if ownership.get('_native_member_evidence') else root.intent.decision_id)
    pin = document("execution_leader_position_context", claim.plan.plan_id)
    decision = StrategyDecision(**source["decision"])
    _validate_decision_identity(decision)
    feature = source["snapshot"]["feature_values"][0]
    if (spec.unfilled_execution_policy is None or gate.status is not MockAutomationGateStatus.APPROVED_FOR_SINGLE_SUBMISSION
            or (gate.gate_id, gate.plan, gate.action, gate.intent_id, gate.strategy_decision_id, gate.spec_id,
                gate.execution_run_id, gate.account_ref) != (stored["mock_plan_approval"], root.intent.plan, "ENTER",
                root.intent.intent_id, root.intent.decision_id, spec.spec_id, run_id, account)
            or decision.decision_id != root.intent.decision_id
            or decision.snapshot_id != _id("leader-snapshot/v1", [run_id, feature, decision.state_before])
            or source.get("position_proof") != pin
            or (pin["entry_intent_id"], pin["proof_id"]) != (claim.plan.plan_id, claim.plan.policy_ref)):
        raise RuntimeError("PLANNED_LEADER_EXPIRY_ORIGINAL_APPROVAL_CHANGED")
    signal_until = datetime.fromisoformat(feature["value"]["valid_until"])
    policy = anchored_unfilled_policy(root.intent, spec.unfilled_execution_policy, "BUY_OPPORTUNITY")
    now = kst_now() if now_provider is None else now_provider()
    if signal_until.tzinfo is None or now < min(signal_until, policy.wait_until):
        raise RuntimeError("PLANNED_LEADER_ENTRY_WAIT_NOT_EXPIRED")

    risk = mock_automation_risk_snapshot_from_dict(document("execution_mock_automation_current_risk", account))
    head = document(PLAN_POSITION_COLLECTION, claim.plan.plan_id)
    broker_account = claim.recovery.account
    orders = [order for order in claim.recovery.orders if order.state not in TERMINAL_ORDER_STATES]
    age = spec.unfilled_execution_policy.max_snapshot_age_seconds
    holdings = {value["symbol"]: quantity} if quantity else {}
    from kiwoom_monitor.application.mock_automation_risk import mock_automation_expected_binding_revision
    if ((risk.snapshot_id, risk.reconciliation_revision) != (claim.risk_snapshot_id, claim.risk_reconciliation_revision)
            or (risk.account_ref, risk.execution_run_id, risk.binding_revision, risk.data_path)
            != (account, run_id, mock_automation_expected_binding_revision(spec, selected_context), spec.data_path)
            or risk.account_revision != mock_automation_risk_account_revision(claim.recovery)
            or (broker_account.account_ref, broker_account.as_of) != (account, risk.account_as_of)
            or {symbol: held for symbol, held in broker_account.positions.items() if held} != holdings
            or dict(risk.owned_position_quantities) != holdings or risk.pending_sell_quantities
            or risk.submission_unknown_count or risk.balance_mismatch_count
            or any(not 0 <= (now - at).total_seconds() <= age for at in (risk.account_as_of, risk.observed_at))
            or (head["version"], head["plan_id"], head["policy_ref"], head["revision"], head["run_id"], head["symbol"],
                head["quantity"]) != ("mock_plan_position/v1", claim.plan.plan_id, claim.plan.policy_ref,
                claim.plan.expected_revision, run_id, value["symbol"], quantity)
            or head.get("unreconciled_sell_quantity") or len(orders) != 1):
        raise RuntimeError("PLANNED_LEADER_EXPIRY_FLAT_CUT_UNCONFIRMED")
    if quantity:
        validate_planned_leader_current_cut(
            claim.plan, spec=spec, run_id=run_id, symbol=value["symbol"], risk=risk, plan_position=head, now=now,
            selected_management_context=selected_context,
        )
    # Risk may be incomplete for a flat first BUY with no fills. This exception
    # can cancel just this mapped order and cannot approve a new order.
    order = orders[0]
    if ((order.account_ref, order.broker_order_id, order.symbol, order.state.value, order.filled_quantity,
         order.remaining_quantity, order.as_of) != (account, root.broker_order_id, root.intent.symbol, expected_state,
         quantity, root.intent.quantity - quantity, root.last_broker_as_of)
            or any(fill.execution_id not in root.fill_ids for fill in order.fills)
            or not 0 <= (now - order.as_of).total_seconds() <= age):
        raise RuntimeError("PLANNED_LEADER_EXPIRY_BROKER_ORDER_CHANGED")
    active = _account_active_execution_rows(cursor, "mock", account, 2, p)
    if len(active) != 1 or _selected_management_original_claim_view(cursor, p,
            _json_document(active[0][0]), ownership) != stored:
        raise RuntimeError("PLANNED_LEADER_EXPIRY_ACCOUNT_ORDER_CHANGED")
    event_type = "document_json->>'event_type'" if p == "%s" else "json_extract(document_json,'$.event_type')"
    cursor.execute(f"SELECT accepted_sequence,document_json FROM central_execution_events WHERE intent_id={p} "
                   f"AND {event_type} IN ('CANCEL_STARTED','FILL','BROKER_FILL_AGGREGATE') ORDER BY accepted_sequence",
                   (claim.plan.plan_id,))
    exact_quantity, fill_cursor, fill_ids = 0, 0, []
    for sequence, event_document in cursor.fetchall():
        previous = _json_document(event_document)
        if previous["event_type"] == "CANCEL_STARTED" or not quantity:
            raise RuntimeError("PLANNED_LEADER_EXPIRY_ROOT_HISTORY_CHANGED")
        if previous["event_type"] == "FILL":
            exact_quantity += previous["quantity"]
            fill_ids.append(previous["broker_execution_id"])
            fill_cursor = sequence
    if (exact_quantity, fill_cursor, tuple(fill_ids)) != (quantity, head["fill_cursor"], root.fill_ids):
        raise RuntimeError("PLANNED_LEADER_EXPIRY_ROOT_HISTORY_CHANGED")
    return {**event, "planned_leader_cancel_claim": claim.to_dict()}


def _require_close_deadline_cancel(cursor, ownership, value, event, p, *, stored, now_provider):
    stored = _selected_management_original_claim_view(cursor, p, stored, ownership)
    from kiwoom_monitor.application.close_mock_execution import CloseEntryDeadlineCancelClaim, validate_close_entry_deadline
    from kiwoom_monitor.application.mock_automation_risk import mock_automation_risk_snapshot_from_dict
    from kiwoom_monitor.infrastructure.persistence.execution_repository import _record_from_document, plan_position_from_document

    claim = CloseEntryDeadlineCancelClaim.from_dict(ownership["planned_cancel_claim"], ownership.get("plan_recovery"))
    if (stored is None or event.get("event_type") != "CANCEL_STARTED" or value["state"] != "CANCEL_PENDING"
            or event["intent_id"] != stored["intent_id"] or ownership.get("control_revision") is None
            or not stored.get("mock_plan_approval")):
        raise RuntimeError("CLOSE_DEADLINE_CANCEL_ROOT_CHANGED")
    account = value["account_ref"]
    spec, package, proof, candidate = _minute_native_documents(cursor, p, account, ownership["active_spec_id"],
        claim.plan.plan_id, selected_management=ownership.get('_native_selected_management_context'))
    risk_doc = _plan_document(cursor, p, "execution_mock_automation_current_risk", account, account)
    head_doc = _plan_document(cursor, p, PLAN_POSITION_COLLECTION, account, claim.plan.plan_id)
    if risk_doc is None or head_doc is None:
        raise RuntimeError("CLOSE_DEADLINE_CURRENT_CUT_MISSING")
    risk = mock_automation_risk_snapshot_from_dict(risk_doc)
    head = plan_position_from_document(head_doc, value["run_id"], value["symbol"], claim.plan)
    if (head.plan != claim.plan or (risk.snapshot_id, risk.reconciliation_revision)
            != (claim.risk_snapshot_id, claim.risk_reconciliation_revision)):
        raise RuntimeError("CLOSE_DEADLINE_CURRENT_CUT_CHANGED")
    validate_close_entry_deadline(spec, run_id=value["run_id"], parameters=package.candidate_spec["parameters"],
        event=candidate, entry_evidence=proof, root=_record_from_document(stored), head=head,
        fill_events=[_json_document(row[0]) for row in _execution_event_rows(cursor, stored["intent_id"], p)],
        recovery=claim.recovery, risk=risk, now=kst_now() if now_provider is None else now_provider(),
        protection_notice=claim.protection_notice,
        stop_risk=_close_native_stop_risk(cursor, p, account, spec, claim.protection_notice, stored['intent_id'],
            checkpoint_key=ownership.get('_native_checkpoint_key', 'current'),
            selected_management=ownership.get('_native_selected_management_context')),
        **_selected_management_close_terms(ownership))
    return {**event, "close_entry_cancel_claim": claim.to_dict()}


def _require_mock_cancel_retry(cursor, ownership, value, event, p, *, now_provider=None, stored=None, control_facts=None):
    """Fence a protective retry in the same native CANCEL_STARTED transaction."""
    if ownership is not None:
        ownership = _native_execution_scope(ownership, control_facts or {})
    if ownership is not None and ownership.get("planned_cancel_claim") is not None:
        if ownership['planned_cancel_claim'].get('version') in {
                'close-current-order-management/v1', 'panic-current-order-management/v1', 'leader-current-order-management/v1'}:
            claim = _require_minute_order_management_claim(cursor, p, stored, value, event, ownership,
                                                          now_provider, control_facts)
            field = {'panic-current-order-management/v1': 'panic_order_management_claim',
                     'close-current-order-management/v1': 'close_order_management_claim',
                     'leader-current-order-management/v1': 'leader_order_management_claim'}[claim['version']]
            return {**event, field: claim}
        if ownership["planned_cancel_claim"].get("version") in {"close-entry-deadline-cancel/v1", 'close-entry-protective-cancel/v1'}:
            return _require_close_deadline_cancel(cursor, ownership, value, event, p, stored=stored,
                                                  now_provider=now_provider)
        if ownership["planned_cancel_claim"].get("version") == "planned-leader-expired-entry-cancel/v1":
            return _require_planned_leader_expiry_cancel(cursor, ownership, value, event, p, stored=stored,
                                                         now_provider=now_provider)
        return _require_planned_leader_cancel(cursor, ownership, value, event, p, stored=stored,
                                              now_provider=now_provider)
    claim = ownership.get('cancel_retry_claim') if ownership else None
    if claim is None:
        if event.get('event_type') == 'CANCEL_STARTED':
            _require_mock_unfilled_management_origin(cursor, ownership, value, p, now_provider=now_provider)
        return event
    from kiwoom_monitor.application.order_lifecycle import (
        protective_cancel_recovery, validate_unfilled_attempt_plan, UnfilledOrderManagementPolicy, unfilled_plan_intent,
    )
    from kiwoom_monitor.application.mock_automation_execution import (
        unfilled_execution_policy_rejections, MOCK_AUTOMATION_DECISION_GATE_VERSION, mock_automation_intent_id,
    )
    from kiwoom_monitor.application.mock_automation_risk import mock_automation_risk_snapshot_from_dict
    from kiwoom_monitor.domain.execution_activation import mock_automation_spec_from_dict
    from kiwoom_monitor.infrastructure.persistence.execution_repository import _record_from_document, _record_document
    from dataclasses import replace
    if (set(claim) != {'root_intent_id','policy_id','reject_event_id','attempt',
                      'risk_snapshot_id','risk_reconciliation_revision'}
            or type(claim['attempt']) is not int or type(claim['risk_reconciliation_revision']) is not int
            or event.get('event_type') != 'CANCEL_STARTED' or event.get('intent_id') != value['intent_id']
            or value.get('state') != 'CANCEL_PENDING'):
        raise RuntimeError('MOCK_PROTECTIVE_CANCEL_RETRY_CLAIM_INVALID')
    lock = ' FOR UPDATE' if p == '%s' else ''
    def document(collection, key):
        cursor.execute(f'SELECT document_json FROM central_documents WHERE collection={p} AND owner={p} '
                       f'AND document_key={p}' + lock, (collection,value['account_ref'],key))
        row = cursor.fetchone()
        if not row:
            raise RuntimeError('MOCK_PROTECTIVE_CANCEL_RETRY_PROOF_MISSING')
        return _json_document(row[0])
    def record(intent_id):
        cursor.execute(f'SELECT document_json FROM central_execution_intents WHERE intent_id={p}' + lock,(intent_id,))
        row = cursor.fetchone()
        if not row:
            raise RuntimeError('MOCK_PROTECTIVE_CANCEL_RETRY_ORDER_MISSING')
        return _record_from_document(_json_document(row[0]))
    spec = mock_automation_spec_from_dict(document('execution_mock_automation_specs',ownership['active_spec_id']))
    checkpoint = document('execution_mock_automation_runner_current',ownership.get('_native_checkpoint_key', 'current'))
    plan = validate_unfilled_attempt_plan(checkpoint.get('unfilled_execution_preflight'))
    scope = plan['anchor']['scope']
    raw = dict(plan['anchor']['policy'])
    for name in ('anchor_available_at','valid_until','wait_until','protection_deadline'):
        raw[name] = datetime.fromisoformat(raw[name])
    policy = UnfilledOrderManagementPolicy(**raw)
    native = record(value['intent_id']); root = record(policy.anchor_intent_id)
    frozen = spec.unfilled_execution_policy
    managed_id = plan['children'][-1]['intent']['intent_id'] if plan['children'] else root.intent.intent_id
    if (frozen is None or frozen.maximum_protective_cancel_retries <= 0
            or claim['policy_id'] != frozen.policy_id or claim['root_intent_id'] != root.intent.intent_id
            or policy.purpose != 'PROTECTIVE_EXIT' or managed_id != native.intent.intent_id
            or native.intent.side.value != 'SELL'
            or replace(_record_from_document(value),state=native.state,updated_at=native.updated_at) != native
            or checkpoint.get('spec_id') != spec.spec_id or checkpoint.get('execution_run_id') != ownership['run_id']
            or scope['account_ref'] != value['account_ref'] or scope['spec_id'] != spec.spec_id
            or scope['execution_run_id'] != ownership['run_id']
            or unfilled_plan_intent(plan['anchor']['root_intent']) != root.intent
            or policy.protection_deadline > root.intent.created_at + timedelta(seconds=frozen.max_snapshot_age_seconds)
            or unfilled_execution_policy_rejections(spec,policy,root)):
        raise RuntimeError('MOCK_PROTECTIVE_CANCEL_RETRY_POLICY_CHANGED')
    ids = (root.intent.intent_id, *(child['intent']['intent_id'] for child in plan['children']))
    cursor.execute('SELECT document_json FROM central_execution_events WHERE intent_id IN ('+
                   ','.join(p for _ in ids)+') ORDER BY accepted_sequence'+lock,ids)
    events = tuple(_json_document(row[0]) for row in cursor.fetchall())
    now = kst_now() if now_provider is None else now_provider()
    cue = protective_cancel_recovery(native,events,policy,frozen,now)
    risk = mock_automation_risk_snapshot_from_dict(document('execution_mock_automation_current_risk',value['account_ref']))
    if (scope['control_revision'] != ownership['control_revision']
            or (root.intent.policy_version.startswith(MOCK_AUTOMATION_DECISION_GATE_VERSION + ':')
                and root.intent.intent_id == mock_automation_intent_id(spec.spec_id, root.intent.decision_id))):
        if not _require_mock_unfilled_management_origin(cursor, ownership, value, p, now_provider=now_provider,
                spec=spec, checkpoint=checkpoint, root_value=_record_document(root), risk_value=risk.to_dict()):
            raise RuntimeError('MOCK_PROTECTIVE_CANCEL_RETRY_POLICY_CHANGED')
    if (cue['state'] != 'RETRY_READY' or cue['attempt'] != claim['attempt']
            or cue['reject_event_id'] != claim['reject_event_id']
            or risk.snapshot_id != claim['risk_snapshot_id'] or risk.reconciliation_revision != claim['risk_reconciliation_revision']
            or risk.account_ref != value['account_ref'] or risk.execution_run_id != ownership['run_id']
            or not risk.reconciliation_complete or risk.submission_unknown_count or risk.balance_mismatch_count
            or native.last_broker_as_of > risk.account_as_of
            or any(not 0 <= (now-at).total_seconds() <= frozen.max_snapshot_age_seconds
                   for at in (native.last_broker_as_of,risk.account_as_of,risk.observed_at))):
        raise RuntimeError('MOCK_PROTECTIVE_CANCEL_RETRY_NATIVE_CUT_CHANGED')
    return {**event,'mock_cancel_retry_claim':dict(claim)}


def _execution_intent_values(value: dict[str, Any], document: str) -> tuple[object, ...]:
    return (
        str(value["intent_id"]), str(value["run_id"]), str(value["environment"]),
        str(value["account_ref"]), str(value["state"]), str(value.get("broker_order_id") or ""),
        value.get("last_broker_as_of"), str(value["created_at"]), str(value["updated_at"]), document,
    )


def _execution_event_values(value: dict[str, Any], document: str) -> tuple[object, ...]:
    return (
        str(value["event_id"]), str(value["intent_id"]), str(value["state"]),
        str(value["occurred_at"]), str(value["received_at"]),
        str(value.get("broker_execution_id") or ""), document,
    )


def _mock_automation_lineage_query(
    account_ref: str, spec_id: str, admission_id: str, lease_receipt_id: str,
    recovery_decision_id: str | None, placeholder: str,
) -> tuple[str, tuple[object, ...]]:
    """One statement snapshot of the exact immutable validation documents.

    Each UNION branch uses the existing collection/owner/key primary key.
    Mutable risk, control and binding evidence deliberately stay outside it.
    """
    keys = [
        ("spec", "execution_mock_automation_specs", spec_id),
        ("admission", "execution_mock_automation_admissions", admission_id),
        ("lease", "execution_mock_automation_lease_receipts", lease_receipt_id),
    ]
    if recovery_decision_id is not None:
        keys.append(("recovery", "execution_mock_automation_recovery_decisions", recovery_decision_id))
    statements, parameters = [], []
    for role, collection, key in keys:
        statements.append(
            f"SELECT '{role}' AS role,document_json FROM central_documents "
            f"WHERE collection={placeholder} AND owner={placeholder} AND document_key={placeholder}"
        )
        parameters.extend((collection, account_ref, key))
    return " UNION ALL ".join(statements), tuple(parameters)


def _retained_projection_document(cursor, p, collection, account, key, cache):
    identity = (collection, key)
    if identity not in cache:
        cursor.execute(f'SELECT document_json FROM central_documents WHERE collection={p} AND owner={p} AND document_key={p}',
                       (collection, account, key))
        rows = cursor.fetchall()
        cache[identity] = _json_document(rows[0][0]) if rows else None
    return cache[identity]


def _retained_projection_admission(cursor, p, account, reference, documents):
    from kiwoom_monitor.application.mock_automation_admission import (
        MOCK_AUTOMATION_ADMISSION_VERSION, mock_automation_admission_from_dict, execution_run_id_for_spec,
    )
    key = ('validated_retained_admission', reference.admission_id)
    if key not in documents:
        original = _retained_projection_document(cursor, p, 'execution_mock_automation_admissions', account,
                                                 reference.admission_id, documents)
        by_spec = _retained_projection_document(cursor, p, 'execution_mock_automation_admission_by_spec', account,
                                                reference.spec_id, documents)
        if original is None or by_spec != original:
            raise RuntimeError('MOCK_RETAINED_ORIGINAL_ADMISSION_MISSING')
        admission = mock_automation_admission_from_dict(original)
        if ((admission.version, admission.account_selection_id) != (MOCK_AUTOMATION_ADMISSION_VERSION, None)
                or (admission.admission_id, admission.spec_id, admission.account_ref, admission.execution_run_id)
                != (reference.admission_id, reference.spec_id, account, reference.execution_run_id)
                or admission.execution_run_id != execution_run_id_for_spec(admission.spec_id)):
            raise RuntimeError('MOCK_RETAINED_ORIGINAL_ADMISSION_CHANGED')
        documents[key] = admission
    return documents[key]


def _retained_member_for_document(cursor, p, value, selection, *, _documents=None, _gate=None,
                                  include_native_member=False):
    """Prove a legacy planned row's original admission, never infer from its run."""
    from dataclasses import asdict
    from kiwoom_monitor.domain.execution_activation import MockAutomationAccountSelectionV2
    from kiwoom_monitor.application.mock_automation_execution import (
        mock_automation_decision_gate_from_dict, mock_automation_intent_id, MockAutomationGateStatus,
    )
    if (not isinstance(selection, MockAutomationAccountSelectionV2)
            or value.get('mock_account_member_spec_id') is not None and not include_native_member
            or not value.get('plan') or not value.get('mock_plan_approval')):
        return None
    cache = {} if _documents is None else _documents
    account = selection.account_scope.account_ref
    gate_doc = (_gate if _gate is not None else _retained_projection_document(cursor, p,
        'execution_mock_automation_approved_gates', account, value['intent_id'], cache))
    if gate_doc is None:
        return None
    gate = mock_automation_decision_gate_from_dict(gate_doc)
    reference = selection.retained_admission_ref(gate.spec_id)
    if reference is None or reference.original_selection_id is not None:
        return None
    admission = _retained_projection_admission(cursor, p, account, reference, cache)
    if (gate.admission_id != admission.admission_id or gate.strategy_ref != admission.strategy_ref
            or value.get('mock_account_member_spec_id') not in (None, reference.spec_id)
            or gate.status is not MockAutomationGateStatus.APPROVED_FOR_SINGLE_SUBMISSION
            or gate.plan is None or asdict(gate.plan) != value['plan']
            or gate.gate_id != value['mock_plan_approval']
            or (gate.account_ref, gate.execution_run_id, gate.intent_id, gate.strategy_decision_id,
                gate.symbol, gate.quantity, gate.limit_price, gate.decided_at) !=
               (value['account_ref'], value['run_id'], value['intent_id'], value['decision_id'],
                value['symbol'], value['quantity'], value['limit_price'], datetime.fromisoformat(value['created_at']))
            or value['environment'] != 'mock' or value['account_ref'] != account
            or value['run_id'] != reference.execution_run_id
            or value['intent_id'] != mock_automation_intent_id(reference.spec_id, value['decision_id'])
            or value['side'] != {'ENTER': 'BUY', 'ADD': 'BUY', 'REDUCE': 'SELL', 'EXIT': 'SELL'}.get(gate.action)):
        raise RuntimeError('MOCK_RETAINED_ORIGINAL_APPROVAL_CHANGED')
    return reference.spec_id


def _retained_execution_member_projection(cursor, p, account_ref, *, selection_id=None,
                                           expected_control_revision=None, intent_ids=None, _selection=None):
    from kiwoom_monitor.application.mock_automation_admission import mock_automation_control_from_dict
    from kiwoom_monitor.domain.execution_activation import (
        MockAutomationAccountSelectionV2, mock_automation_account_selection_from_dict,
    )
    if _selection is not None:
        # Only native callers supply membership already read/verified in this transaction.
        selected = mock_automation_account_selection_from_dict(_selection)
        if not isinstance(selected, MockAutomationAccountSelectionV2):
            return None, {}
        return selected, _retained_projection_rows(cursor, p, account_ref, selected,
            selected.retained_admission_refs[0].execution_run_id, intent_ids, {})
    control_doc = _scoped_mock_automation_control(cursor, account_ref, p)
    if control_doc is None:
        if selection_id is not None:
            raise RuntimeError('MOCK_RETAINED_CONTROL_MISSING')
        return None, {}
    control = mock_automation_control_from_dict(control_doc)
    if expected_control_revision is not None and control.control_revision != expected_control_revision:
        raise RuntimeError('MOCK_RETAINED_CONTROL_CHANGED')
    documents = {}
    if selection_id is not None:
        if expected_control_revision is None:
            raise ValueError('prospective member projection requires the original control revision')
        raw = _retained_projection_document(cursor, p, 'execution_mock_automation_account_selections',
                                             account_ref, selection_id, documents)
        if raw is None:
            raise RuntimeError('MOCK_RETAINED_SELECTION_MISSING')
        selected = mock_automation_account_selection_from_dict(raw)
    else:
        selected = control.account_selection
    if not isinstance(selected, MockAutomationAccountSelectionV2):
        if selection_id is not None:
            raise ValueError('prospective member projection requires an explicit v2 selection')
        return None, {}
    if (selected.account_scope.account_ref != account_ref or selected.primary_spec_id != control.active_spec_id
            or any(ref.execution_run_id != control.execution_run_id for ref in selected.retained_admission_refs)
            or _retained_projection_document(cursor, p, 'execution_mock_automation_account_selections', account_ref,
                                               selected.selection_id, documents) != selected.to_dict()):
        raise RuntimeError('MOCK_RETAINED_SELECTION_CHANGED')
    return selected, _retained_projection_rows(cursor, p, account_ref, selected,
        control.execution_run_id, intent_ids, documents)


def _retained_projection_rows(cursor, p, account_ref, selected, run_id, intent_ids, documents):
    references = tuple(ref for ref in selected.retained_admission_refs if ref.original_selection_id is None)
    for reference in references:
        _retained_projection_admission(cursor, p, account_ref, reference, documents)
    if not references or intent_ids == ():
        return {}
    # One joined read covers all requested immutable original approvals, including terminal FILL roots.
    ids = tuple(dict.fromkeys(intent_ids)) if intent_ids is not None else None
    if ids is not None and len(ids) > 400:
        raise ValueError('retained projection intent page exceeds its existing account budget')
    admission_key = "g.document_json->>'admission_id'" if p == '%s' else "json_extract(g.document_json,'$.admission_id')"
    sql = ('SELECT i.document_json,g.document_json FROM central_execution_intents i JOIN central_documents g '
           f'ON g.collection={p} AND g.owner=i.account_ref AND g.document_key=i.intent_id '
           f'WHERE i.environment={p} AND i.account_ref={p} AND i.run_id={p} AND {admission_key} IN ('
           + ','.join(p for _ in references) + ')')
    args = ('execution_mock_automation_approved_gates', 'mock', account_ref, run_id,
            *(ref.admission_id for ref in references))
    if ids is not None:
        sql += ' AND i.intent_id IN (' + ','.join(p for _ in ids) + ')'
        args += ids
    cursor.execute(sql, args)
    rows = cursor.fetchall()
    mapping = {}
    for raw_intent, raw_gate in rows:
        value = _json_document(raw_intent)
        member = _retained_member_for_document(cursor, p, value, selected, _documents=documents,
                                               _gate=_json_document(raw_gate), include_native_member=True)
        if member is not None:
            mapping[value['intent_id']] = member
    return mapping


def _scoped_mock_automation_control(cursor, account_ref, placeholder):
    cursor.execute(
        "SELECT document_json FROM central_documents WHERE collection=" + placeholder
        + " AND owner=" + placeholder + " AND document_key=" + placeholder,
        ("execution_mock_automation_control", account_ref, account_ref),
    )
    rows = cursor.fetchall()  # Exact PK: finish the read before the next typed validation.
    return _json_document(rows[0][0]) if rows else None


def _execution_intent_row(cursor, intent_id, placeholder):
    cursor.execute(
        "SELECT document_json FROM central_execution_intents WHERE intent_id=" + placeholder, (intent_id,),
    )
    rows = cursor.fetchall()  # The exact-key statement must finish before the next read.
    return rows[0] if rows else None


def _execution_event_rows(cursor, intent_id, placeholder):
    cursor.execute(
        "SELECT document_json FROM central_execution_events WHERE intent_id=" + placeholder
        + " ORDER BY accepted_sequence", (intent_id,),
    )
    return cursor.fetchall()


def _active_execution_rows(cursor, environment, account_ref, run_id, p):
    terminal = ("FILLED", "CANCELLED", "REJECTED", "EXPIRED")
    cursor.execute(
        f"SELECT document_json FROM central_execution_intents WHERE environment={p} "
        f"AND account_ref={p} AND run_id={p} AND state NOT IN ({p},{p},{p},{p}) ORDER BY created_at",
        (environment, account_ref, run_id, *terminal),
    )
    return cursor.fetchall()


def _account_active_execution_rows(cursor, environment, account_ref, limit, p):
    cursor.execute(
        f"SELECT document_json FROM central_execution_intents WHERE environment={p} AND account_ref={p} "
        f"AND state NOT IN ('FILLED','CANCELLED','REJECTED','EXPIRED') ORDER BY created_at,intent_id LIMIT {p}",
        (environment, account_ref, limit),
    )
    return cursor.fetchall()


def _account_execution_event_rows(cursor, environment, account_ref, after_sequence, limit, p):
    cursor.execute(
        "SELECT e.accepted_sequence,i.document_json,e.document_json "
        "FROM central_execution_events e JOIN central_execution_intents i "
        f"ON i.intent_id=e.intent_id WHERE i.environment={p} AND i.account_ref={p} "
        f"AND e.accepted_sequence>{p} ORDER BY e.accepted_sequence LIMIT {p}",
        (environment, account_ref, after_sequence, limit),
    )
    return cursor.fetchall()


def _latest_mock_news_position_snapshot(cursor, account_ref, cut, placeholder):
    # Current and older snapshots can use different ISO offsets.
    instant = "julianday({})" if placeholder == "?" else "CAST({} AS TIMESTAMPTZ)"
    observed = instant.format("as_of")
    received = instant.format("received_at")
    cursor.execute(
        f"SELECT document_json FROM central_execution_account_snapshots WHERE environment='mock' "
        f"AND account_ref={placeholder} AND {observed}<={received} AND {received}<={instant.format(placeholder)} "
        f"ORDER BY {observed} DESC,{received} DESC,snapshot_id DESC LIMIT 1",
        (account_ref, cut.isoformat()),
    )
    row = cursor.fetchone()
    return _json_document(row[0]) if row is not None else None


class SQLiteExecutionStoreMixin:
    def load_mock_news_position_snapshot(self, account_ref, cut):
        with self._lock, self._connection() as connection:
            return _latest_mock_news_position_snapshot(connection.cursor(), account_ref, cut, "?")
    _supports_scoped_active_intents = True

    def load_retained_execution_member_projection(self, account_ref, *, selection_id=None,
            expected_control_revision=None, intent_ids=None, _reader=None):
        kwargs = dict(selection_id=selection_id, expected_control_revision=expected_control_revision, intent_ids=intent_ids)
        with self._lock:
            if _reader is not None:
                return _retained_execution_member_projection(_reader, '?', account_ref, **kwargs)
            with self._connection() as connection:
                return _retained_execution_member_projection(connection.cursor(), '?', account_ref, **kwargs)

    def load_account_active_execution_intents(
        self, environment: str, account_ref: str, limit: int, *, _reader: Any = None,
    ) -> list[dict[str, Any]]:
        if environment != "mock" or not account_ref.strip() or type(limit) is not int or not 1 <= limit <= 401:
            raise ValueError("bounded mock account ledger scope is required")
        with self._lock:
            if _reader is not None:
                rows = _account_active_execution_rows(_reader, environment, account_ref, limit, "?")
            else:
                with self._connection() as connection:
                    rows = _account_active_execution_rows(connection.cursor(), environment, account_ref, limit, "?")
        return [_json_document(row[0]) for row in rows]

    def load_mock_automation_lineage(
        self, account_ref: str, *, spec_id: str, admission_id: str,
        lease_receipt_id: str, recovery_decision_id: str | None = None, _reader: Any = None,
    ) -> dict[str, dict[str, Any]]:
        sql, parameters = _mock_automation_lineage_query(
            account_ref, spec_id, admission_id, lease_receipt_id, recovery_decision_id, "?",
        )
        with self._lock:
            if _reader is not None:
                rows = _reader.execute(sql, parameters).fetchall()
            else:
                with self._connection() as connection:
                    rows = connection.execute(sql, parameters).fetchall()
        return {str(role): _json_document(document) for role, document in rows}

    def release_execution_runtime(self, owner_key: str, owner_token: str, *,
            selected_management_context=None, selected_management_owner=None) -> bool:
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if selected_management_context is not None:
                from .selected_order_bootstrap import selected_management_release_owner_key
                owner_key = selected_management_release_owner_key(connection.cursor(), '?',
                    selected_management_context, owner_token)
            cursor = connection.execute(
                "DELETE FROM central_execution_runtime_leases WHERE owner_key=? AND owner_token=?",
                (owner_key, owner_token),
            )
            return cursor.rowcount == 1

    def create_execution_intent(self, value: dict[str, Any], *, ownership: dict[str, str] | None = None) -> bool:
        document = _event_document(value)
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            control = {}
            stored = _require_execution_ownership(connection.cursor(), ownership, value, "?",
                now_provider=getattr(self, '_rehearsal_lease_now', getattr(self, '_execution_wall_now', None)),
                new_intent_claim=True, control_facts=control,
                rehearsal=getattr(type(self), 'local_order_rehearsal', False))
            if "plan" in value:
                if stored is not None:
                    return False
                value = _claim_plan_position(connection.cursor(), "?", value, ownership,
                                             getattr(self, "_execution_wall_now", None), control=control)
                document = _event_document(value)
            if ownership and ownership.get('replacement_claim') is not None:
                value = {**value, 'mock_replacement_claim': dict(ownership['replacement_claim'])}
                document = _event_document(value)
            if ownership and ownership.get('protection_claim') is not None:
                value = {**value, 'mock_protection_claim': dict(ownership['protection_claim'])}
                document = _event_document(value)
            cursor = connection.execute(
                "INSERT OR IGNORE INTO central_execution_intents("
                "intent_id,run_id,environment,account_ref,state,broker_order_id,last_broker_as_of,"
                "created_at,updated_at,document_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
                _execution_intent_values(value, document),
            )
        return cursor.rowcount == 1

    def append_execution_event(self, intent: dict[str, Any], event: dict[str, Any], *, ownership: dict[str, str] | None = None) -> bool:
        intent_document = _event_document(intent)
        event_document = _event_document(event)
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            control_facts = {}
            stored = _require_execution_ownership(connection.cursor(), ownership, intent, "?",
                                         now_provider=getattr(self, '_rehearsal_lease_now', getattr(self, '_execution_wall_now', None)),
                                         submission_claim=event.get("event_type") == "SUBMISSION_STARTED",
                                         amendment_claim=event.get('event_type') == 'AMENDMENT_STARTED', control_facts=control_facts,
                                         event_kind=event.get('event_type'),
                                         rehearsal=getattr(type(self), 'local_order_rehearsal', False))
            if stored is not None and 'plan' in intent:
                from kiwoom_monitor.infrastructure.persistence.execution_repository import retain_native_plan_claim
                intent = retain_native_plan_claim(stored,intent)
            stored = _validate_broker_ledger_write(connection.cursor(), "?", stored, intent, event, ownership,
                                                  getattr(self, '_execution_wall_now', None), control_facts)
            if "plan" in intent and not _validate_plan_event(stored, intent, event):
                return False
            if "plan" in intent and event["event_type"] == "SUBMISSION_STARTED":
                approval, _, _, _, _ = _require_plan_approval(connection.cursor(), "?", intent, _native_execution_scope(ownership, control_facts),
                    getattr(self, "_execution_wall_now", None), account_limits=_account_control_limits(control_facts))
                if stored is None or stored.get("mock_plan_approval") != approval:
                    raise RuntimeError("PLANNED_EXECUTION_APPROVAL_CHANGED")
            event = _require_mock_cancel_retry(connection.cursor(), ownership, intent, event, '?',
                                              now_provider=getattr(self, '_execution_wall_now', None), stored=stored,
                                              control_facts=control_facts)
            event_document = _event_document(event)
            if connection.execute(
                "SELECT 1 FROM central_execution_events WHERE event_id=?", (str(event["event_id"]),),
            ).fetchone() is not None:
                return False
            if stored is None:
                row = _execution_intent_row(connection.cursor(), intent["intent_id"], "?")
                if row is None:
                    raise KeyError(f"unknown execution intent: {intent['intent_id']}")
                if ownership is None and "plan" in _json_document(row[0]):
                    raise RuntimeError("PLANNED_EXECUTION_OWNER_REQUIRED")
            intent = _retain_execution_claim(connection.cursor(), intent, '?')
            intent_document = _event_document(intent)
            inserted = connection.execute(
                "INSERT INTO central_execution_events("
                "event_id,intent_id,state,occurred_at,received_at,broker_execution_id,document_json) "
                "VALUES(?,?,?,?,?,?,?)",
                _execution_event_values(event, event_document),
            )
            if "plan" in intent and _plan_event_changes_position(event):
                _apply_plan_execution_event(connection.cursor(), "?", intent, event, inserted.lastrowid)
            updated = connection.execute(
                "UPDATE central_execution_intents SET state=?,broker_order_id=?,last_broker_as_of=?,"
                "updated_at=?,document_json=? WHERE intent_id=? "
                "AND coalesce(json_extract(document_json,'$.broker_revision'),0)=?",
                (
                    str(intent["state"]), str(intent.get("broker_order_id") or ""),
                    intent.get("last_broker_as_of"), str(intent["updated_at"]), intent_document,
                    str(intent["intent_id"]),
                    max(0, int(intent.get("broker_revision", 0)) - 1),
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeError("EXECUTION_BROKER_REVISION_CHANGED")
        return True

    def load_execution_intent(self, intent_id: str, *, _reader: Any = None) -> dict[str, Any] | None:
        with self._lock:
            if _reader is not None:
                row = _execution_intent_row(_reader, intent_id, "?")
            else:
                with self._connection() as connection:
                    row = _execution_intent_row(connection.cursor(), intent_id, "?")
        return _json_document(row[0]) if row else None

    def find_execution_intent_by_broker_order_id(
        self, environment: str, account_ref: str, run_id: str, broker_order_id: str,
    ) -> dict[str, Any] | None:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                _broker_order_lookup_sql("?"),
                (environment, account_ref, run_id, broker_order_id) * 2,
            ).fetchall()
        if len(rows) > 1:
            raise ValueError("broker order id matches multiple execution intents")
        return _json_document(rows[0][0]) if rows else None

    def load_execution_events(self, intent_id: str, *, _reader: Any = None) -> list[dict[str, Any]]:
        with self._lock:
            if _reader is not None:
                rows = _execution_event_rows(_reader, intent_id, "?")
            else:
                with self._connection() as connection:
                    rows = _execution_event_rows(connection.cursor(), intent_id, "?")
        return [_json_document(row[0]) for row in rows]

    def load_account_execution_events(
        self, environment: str, account_ref: str, after_sequence: int, limit: int, *, _reader: Any = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            if _reader is not None:
                rows = _account_execution_event_rows(_reader, environment, account_ref, after_sequence, limit, "?")
            else:
                with self._connection() as connection:
                    rows = _account_execution_event_rows(connection.cursor(), environment, account_ref, after_sequence, limit, "?")
        return [
            {"accepted_sequence": int(row[0]), "intent": _json_document(row[1]),
             "event": _json_document(row[2])}
            for row in rows
        ]

    def save_execution_account_snapshot(self, value: dict[str, Any], *, ownership: dict[str, str] | None = None) -> bool:
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            _require_execution_ownership(connection.cursor(), ownership, value, "?",
                                         now_provider=getattr(self, '_rehearsal_lease_now', getattr(self, '_execution_wall_now', None)),
                                         rehearsal=getattr(type(self), 'local_order_rehearsal', False))
            cursor = connection.execute(
                "INSERT OR IGNORE INTO central_execution_account_snapshots("
                "snapshot_id,environment,account_ref,as_of,received_at,document_json) VALUES(?,?,?,?,?,?)",
                (
                    str(value["snapshot_id"]), str(value["environment"]), str(value["account_ref"]),
                    str(value["as_of"]), str(value["received_at"]), _event_document(value),
                ),
            )
        return cursor.rowcount == 1

    def load_mock_automation_control(self, account_ref: str, *, _reader: Any = None) -> dict[str, Any] | None:
        if _reader is not None:
            with self._lock:
                return _scoped_mock_automation_control(_reader, account_ref, '?')
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT document_json FROM central_documents WHERE collection=? AND owner=? "
                "AND document_key=?",
                ("execution_mock_automation_control", account_ref, account_ref),
            ).fetchone()
        return _json_document(row[0]) if row else None

    def load_active_execution_intents(
        self, environment: str, account_ref: str, run_id: str, *, _reader: Any = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            if _reader is not None:
                rows = _active_execution_rows(_reader, environment, account_ref, run_id, "?")
            else:
                with self._connection() as connection:
                    rows = _active_execution_rows(connection.cursor(), environment, account_ref, run_id, "?")
        return [_json_document(row[0]) for row in rows]

    def save_mock_automation_control(
        self, value: dict[str, Any], *, expected_revision: int, resume_owner_token: str | None = None,
    ) -> bool:
        account_ref = str(value.get("account_ref") or "")
        revision = int(value.get("control_revision") or 0)
        if not account_ref or revision != expected_revision + 1:
            raise ValueError("invalid mock automation control revision")
        document = _event_document(value)
        changed_at = datetime.fromisoformat(str(value["changed_at"])).timestamp()
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            now_provider = getattr(self, "_execution_wall_now", None) or kst_now
            if resume_owner_token is not None:
                run_id = value["execution_run_id"]
                _require_execution_ownership(connection.cursor(), {"owner_key": f"mock:{account_ref}",
                    "owner_token": f"{run_id}:{resume_owner_token}", "run_id": run_id},
                    {"environment": "mock", "account_ref": account_ref}, "?", now_provider=now_provider)
            row = connection.execute(
                "SELECT document_json FROM central_documents WHERE collection=? AND owner=? "
                "AND document_key=?",
                ("execution_mock_automation_control", account_ref, account_ref),
            ).fetchone()
            current = _json_document(row[0]) if row else {}
            if int(current.get("control_revision", 0)) != expected_revision:
                return False
            _require_account_control_change(connection.cursor(), "?", value, current, account_ref)
            _require_account_resume_control(connection.cursor(), "?", value, current, resume_owner_token, now_provider)
            if value.get('account_selection') is None:
                _require_planned_resume_control(connection.cursor(), "?", value, current, resume_owner_token, now_provider)
            connection.execute(
                "INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
                "VALUES(?,?,?,?,?) ON CONFLICT(collection,owner,document_key) DO UPDATE SET "
                "updated_at=excluded.updated_at,document_json=excluded.document_json",
                ("execution_mock_automation_control", account_ref, account_ref, changed_at, document),
            )
        return True

    def replace_mock_automation_control(self, value: dict[str, Any], *, expected_revision: int,
            expected_selection_id: str, owner_token: str) -> bool:
        account = str(value.get('account_ref') or '')
        if not account or value.get('control_revision') != expected_revision + 1 or not owner_token:
            raise ValueError('invalid mock account replacement revision or owner')
        with self._lock, self._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            cursor = connection.cursor()
            now_provider = getattr(self, '_execution_wall_now', None) or kst_now
            run_id = value['execution_run_id']
            _require_execution_ownership(cursor, {'owner_key': f'mock:{account}',
                'owner_token': f'{run_id}:{owner_token}', 'run_id': run_id},
                {'environment': 'mock', 'account_ref': account}, '?', now_provider=now_provider)
            current = _plan_document(cursor, '?', 'execution_mock_automation_control', account, account) or {}
            if (current.get('control_revision') != expected_revision
                    or (current.get('account_selection') or {}).get('selection_id') != expected_selection_id):
                return False
            _require_account_replacement_control(cursor, '?', value, current, owner_token, now_provider)
            cursor.execute('UPDATE central_documents SET updated_at=?,document_json=? '
                'WHERE collection=? AND owner=? AND document_key=?',
                (datetime.fromisoformat(value['changed_at']).timestamp(), _event_document(value),
                 'execution_mock_automation_control', account, account))
        return True

    def acquire_execution_runtime(
        self, owner_key: str, owner_token: str, now: str, lease_expires_at: str, *,
        selected_management_context=None, selected_management_owner=None,
    ) -> bool:
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                owner_key, selected = _execution_runtime_lease_scope(connection.cursor(), '?', owner_key, owner_token,
                    datetime.fromisoformat(now), selected_management_context=selected_management_context,
                    selected_management_owner=selected_management_owner)
            except RuntimeError as error:
                if str(error) in {'SELECTED_MANAGEMENT_CONTEXT_REQUIRED', 'EXECUTION_CANONICAL_OWNERSHIP_CONFLICT'}:
                    return False  # Preserve the existing contested-acquire contract.
                raise
            cursor = connection.execute(
                "INSERT INTO central_execution_runtime_leases(owner_key,owner_token,lease_expires_at,updated_at) "
                "VALUES(?,?,?,?) ON CONFLICT(owner_key) DO UPDATE SET owner_token=excluded.owner_token,"
                "lease_expires_at=excluded.lease_expires_at,updated_at=excluded.updated_at "
                "WHERE central_execution_runtime_leases.owner_token=excluded.owner_token OR "
                "julianday(central_execution_runtime_leases.lease_expires_at)<=julianday(excluded.updated_at)",
                (owner_key, owner_token, lease_expires_at, now),
            )
            if selected is not None and not selected_management_owner():
                raise RuntimeError('SELECTED_MANAGEMENT_OWNER_CHANGED')
        return cursor.rowcount == 1

class PostgresExecutionStoreMixin:
    def load_mock_news_position_snapshot(self, account_ref, cut):
        from .postgres_access import DBWriterContext, open_observed_connection

        reader = DBWriterContext(writer_family="read.news_holdings", writer_kind="execution_account_snapshot",
                                 operation="load_mock_news_position_snapshot", access_mode="read")
        with open_observed_connection(self._connect, reader) as connection, connection.cursor() as cursor:
            return _latest_mock_news_position_snapshot(cursor, account_ref, cut, "%s")
    def load_retained_execution_member_projection(self, account_ref, *, selection_id=None,
            expected_control_revision=None, intent_ids=None, _reader=None):
        kwargs = dict(selection_id=selection_id, expected_control_revision=expected_control_revision, intent_ids=intent_ids)
        if _reader is not None:
            return _retained_execution_member_projection(_reader, '%s', account_ref, **kwargs)
        from .postgres_access import DBWriterContext, open_observed_connection
        scope = DBWriterContext(writer_family='read.execution', writer_kind='retained_projection',
            operation='load_retained_execution_member_projection', access_mode='read')
        with open_observed_connection(self._connect, scope) as connection, connection.cursor() as cursor:
            return _retained_execution_member_projection(cursor, '%s', account_ref, **kwargs)
    _supports_scoped_active_intents = True

    @contextmanager
    def _broker_reconciliation_scope(self):
        from .postgres_access import DBWriterContext, owned_postgres_session

        owner = DBWriterContext(
            writer_family="infrastructure.connection", writer_kind="broker_reconciliation",
            operation="broker_reconciliation_scope", access_mode="connection",
        )
        with owned_postgres_session(self._connect, owner) as connection:
            yield connection

    def load_account_active_execution_intents(
        self, environment: str, account_ref: str, limit: int, *, _reader: Any = None,
    ) -> list[dict[str, Any]]:
        if environment != "mock" or not account_ref.strip() or type(limit) is not int or not 1 <= limit <= 401:
            raise ValueError("bounded mock account ledger scope is required")
        from .postgres_access import DBWriterContext, open_observed_connection
        if _reader is not None:
            rows = _account_active_execution_rows(_reader, environment, account_ref, limit, "%s")
        else:
            reader = DBWriterContext(writer_family="read.execution", writer_kind="account_active_intents",
                operation="load_account_active_execution_intents", access_mode="read")
            with open_observed_connection(self._connect, reader) as connection, connection.cursor() as cursor:
                rows = _account_active_execution_rows(cursor, environment, account_ref, limit, "%s")
        return [_json_document(row[0]) for row in rows]

    def load_mock_automation_lineage(
        self, account_ref: str, *, spec_id: str, admission_id: str,
        lease_receipt_id: str, recovery_decision_id: str | None = None, _reader: Any = None,
    ) -> dict[str, dict[str, Any]]:
        from .postgres_access import DBWriterContext, open_observed_connection

        sql, parameters = _mock_automation_lineage_query(
            account_ref, spec_id, admission_id, lease_receipt_id, recovery_decision_id, "%s",
        )
        if _reader is not None:
            _reader.execute(sql, parameters)
            rows = _reader.fetchall()
        else:
            reader = DBWriterContext(
                writer_family="read.mock_automation", writer_kind="immutable_lineage",
                operation="load_mock_automation_lineage", access_mode="read",
            )
            with open_observed_connection(self._connect, reader) as connection, connection.cursor() as cursor:
                cursor.execute(sql, parameters)
                rows = cursor.fetchall()
        return {str(role): _json_document(document) for role, document in rows}

    def release_execution_runtime(self, owner_key: str, owner_token: str, *,
            selected_management_context=None, selected_management_owner=None) -> bool:
        from .postgres_access import DBWriterContext, open_observed_connection

        writer = DBWriterContext(
            writer_family="execution.runtime_lease", writer_kind="execution_runtime_release",
            operation="release_execution_runtime", rows_attempted=1,
        )
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            if selected_management_context is not None:
                from kiwoom_monitor.domain.selected_order_evidence import (
                    SelectedOrderManagementContext, selected_order_management_context_from_dict)
                from .selected_order_bootstrap import selected_management_release_owner_key
                context = (selected_management_context if isinstance(selected_management_context, SelectedOrderManagementContext)
                           else selected_order_management_context_from_dict(selected_management_context))
                # The coordinate is a lock hint only. Validate its immutable
                # preparation after taking the same lock as acquire/prepare.
                cursor.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',
                               ('execution-runtime-owner:mock:' + context.canonical_account_ref,))
                owner_key = selected_management_release_owner_key(cursor, '%s', selected_management_context, owner_token)
            else:
                cursor.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', ('execution-runtime-owner:' + owner_key,))
            cursor.execute(
                "DELETE FROM central_execution_runtime_leases WHERE owner_key=%s AND owner_token=%s",
                (owner_key, owner_token),
            )
            return cursor.rowcount == 1

    def create_execution_intent(self, value: dict[str, Any], *, ownership: dict[str, str] | None = None) -> bool:
        from .postgres_access import DBWriterContext, open_observed_connection

        writer = DBWriterContext(
            writer_family="execution.intent", writer_kind="execution_intent",
            operation="create_execution_intent", rows_attempted=1,
        )
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            control = {}
            stored = _require_execution_ownership(cursor, ownership, value, "%s",
                now_provider=getattr(self, '_execution_wall_now', None), new_intent_claim=True, control_facts=control)
            if "plan" in value:
                if stored is not None:
                    return False
                value = _claim_plan_position(cursor, "%s", value, ownership,
                                             getattr(self, "_execution_wall_now", None), control=control)
            if ownership and ownership.get('replacement_claim') is not None:
                value = {**value, 'mock_replacement_claim': dict(ownership['replacement_claim'])}
            if ownership and ownership.get('protection_claim') is not None:
                value = {**value, 'mock_protection_claim': dict(ownership['protection_claim'])}
            cursor.execute(
                "INSERT INTO central_execution_intents("
                "intent_id,run_id,environment,account_ref,state,broker_order_id,last_broker_as_of,"
                "created_at,updated_at,document_json) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT(intent_id) DO NOTHING",
                _execution_intent_values(value, json.dumps(value, ensure_ascii=False)),
            )
            return cursor.rowcount == 1

    def append_execution_event(
        self, intent: dict[str, Any], event: dict[str, Any], *,
        ownership: dict[str, str] | None = None, _connection: Any = None,
    ) -> bool:
        from .postgres_access import DBWriterContext, observe_existing_transaction, open_observed_connection

        writer = DBWriterContext(
            writer_family="execution.event", writer_kind="execution_event",
            operation="append_execution_event", rows_attempted=1,
        )
        transaction = (open_observed_connection(self._connect, writer) if _connection is None
                       else observe_existing_transaction(_connection, writer, require_idle=True))
        with transaction as connection, connection.cursor() as cursor:
            control_facts = {}
            stored = _require_execution_ownership(cursor, ownership, intent, "%s",
                                         now_provider=getattr(self, '_execution_wall_now', None),
                                         submission_claim=event.get("event_type") == "SUBMISSION_STARTED",
                                         amendment_claim=event.get('event_type') == 'AMENDMENT_STARTED', control_facts=control_facts,
                                         event_kind=event.get('event_type'))
            if stored is not None and 'plan' in intent:
                from kiwoom_monitor.infrastructure.persistence.execution_repository import retain_native_plan_claim
                intent = retain_native_plan_claim(stored,intent)
            stored = _validate_broker_ledger_write(cursor, "%s", stored, intent, event, ownership,
                                                  getattr(self, '_execution_wall_now', None), control_facts)
            if "plan" in intent and not _validate_plan_event(stored, intent, event):
                return False
            if "plan" in intent and event["event_type"] == "SUBMISSION_STARTED":
                approval, _, _, _, _ = _require_plan_approval(cursor, "%s", intent, _native_execution_scope(ownership, control_facts),
                    getattr(self, "_execution_wall_now", None), account_limits=_account_control_limits(control_facts))
                if stored is None or stored.get("mock_plan_approval") != approval:
                    raise RuntimeError("PLANNED_EXECUTION_APPROVAL_CHANGED")
            event = _require_mock_cancel_retry(cursor, ownership, intent, event, '%s',
                                              now_provider=getattr(self, '_execution_wall_now', None), stored=stored,
                                              control_facts=control_facts)
            changes_plan = "plan" in intent and _plan_event_changes_position(event)
            cursor.execute(
                "INSERT INTO central_execution_events("
                "event_id,intent_id,state,occurred_at,received_at,broker_execution_id,document_json) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(event_id) DO NOTHING"
                + (" RETURNING accepted_sequence" if changes_plan else ""),
                _execution_event_values(event, json.dumps(event, ensure_ascii=False)),
            )
            if cursor.rowcount != 1:
                return False
            if changes_plan:
                sequence = cursor.fetchone()[0]
                _apply_plan_execution_event(cursor, "%s", intent, event, sequence)
            intent = _retain_execution_claim(cursor, intent, '%s')
            if "plan" in intent:
                if stored is None:
                    raise KeyError(f"unknown execution intent: {intent['intent_id']}")
            cursor.execute(
                "UPDATE central_execution_intents SET state=%s,broker_order_id=%s,last_broker_as_of=%s,"
                "updated_at=%s,document_json=%s WHERE intent_id=%s"
                " AND coalesce((document_json->>'broker_revision')::bigint,0)=%s"
                + (" AND NOT (document_json ? 'plan')" if ownership is None else ""),
                (
                    str(intent["state"]), str(intent.get("broker_order_id") or ""),
                    intent.get("last_broker_as_of"), str(intent["updated_at"]),
                    json.dumps(intent, ensure_ascii=False), str(intent["intent_id"]),
                    max(0, int(intent.get("broker_revision", 0)) - 1),
                ),
            )
            if cursor.rowcount != 1:
                if intent.get("broker_legs"):
                    raise RuntimeError("EXECUTION_BROKER_REVISION_CHANGED")
                raise KeyError(f"unknown execution intent: {intent['intent_id']}")
            return True

    def load_execution_intent(
        self, intent_id: str, *, _reader: Any = None, _connection: Any = None,
    ) -> dict[str, Any] | None:
        from .postgres_access import DBWriterContext, observe_existing_transaction, open_observed_connection

        if _reader is not None:
            row = _execution_intent_row(_reader, intent_id, "%s")
        else:
            reader = DBWriterContext(
                writer_family="read.execution", writer_kind="intent_by_id",
                operation="load_execution_intent", access_mode="read",
            )
            transaction = (open_observed_connection(self._connect, reader) if _connection is None
                           else observe_existing_transaction(_connection, reader, require_idle=True))
            with transaction as connection, connection.cursor() as cursor:
                row = _execution_intent_row(cursor, intent_id, "%s")
        return _json_document(row[0]) if row else None

    def find_execution_intent_by_broker_order_id(
        self, environment: str, account_ref: str, run_id: str, broker_order_id: str, *, _connection: Any = None,
    ) -> dict[str, Any] | None:
        from .postgres_access import DBWriterContext, observe_existing_transaction, open_observed_connection

        reader = DBWriterContext(
            writer_family="read.execution", writer_kind="intent_by_broker_order",
            operation="find_execution_intent_by_broker_order_id", access_mode="read",
        )
        transaction = (open_observed_connection(self._connect, reader) if _connection is None
                       else observe_existing_transaction(_connection, reader, require_idle=True))
        with transaction as connection, connection.cursor() as cursor:
            cursor.execute(
                _broker_order_lookup_sql("%s"),
                (environment, account_ref, run_id, broker_order_id) * 2,
            )
            rows = cursor.fetchall()
        if len(rows) > 1:
            raise ValueError("broker order id matches multiple execution intents")
        return _json_document(rows[0][0]) if rows else None

    def load_execution_events(self, intent_id: str, *, _reader: Any = None) -> list[dict[str, Any]]:
        from .postgres_access import DBWriterContext, open_observed_connection

        if _reader is not None:
            rows = _execution_event_rows(_reader, intent_id, "%s")
        else:
            reader = DBWriterContext(
                writer_family="read.execution", writer_kind="intent_events",
                operation="load_execution_events", access_mode="read",
            )
            with open_observed_connection(self._connect, reader) as connection, connection.cursor() as cursor:
                rows = _execution_event_rows(cursor, intent_id, "%s")
        return [_json_document(row[0]) for row in rows]

    def load_account_execution_events(
        self, environment: str, account_ref: str, after_sequence: int, limit: int, *, _reader: Any = None,
    ) -> list[dict[str, Any]]:
        from .postgres_access import DBWriterContext, open_observed_connection

        if _reader is not None:
            rows = _account_execution_event_rows(_reader, environment, account_ref, after_sequence, limit, "%s")
        else:
            reader = DBWriterContext(
                writer_family="read.execution", writer_kind="account_events",
                operation="load_account_execution_events", access_mode="read",
            )
            with open_observed_connection(self._connect, reader) as connection, connection.cursor() as cursor:
                rows = _account_execution_event_rows(cursor, environment, account_ref, after_sequence, limit, "%s")
        return [
            {"accepted_sequence": int(row[0]), "intent": _json_document(row[1]),
             "event": _json_document(row[2])}
            for row in rows
        ]

    def save_execution_account_snapshot(self, value: dict[str, Any], *, ownership: dict[str, str] | None = None) -> bool:
        from .postgres_access import DBWriterContext, open_observed_connection

        writer = DBWriterContext(
            writer_family="execution.account_snapshot", writer_kind="execution_account_snapshot",
            operation="save_execution_account_snapshot", rows_attempted=1,
        )
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            _require_execution_ownership(cursor, ownership, value, "%s",
                                         now_provider=getattr(self, '_execution_wall_now', None))
            cursor.execute(
                "INSERT INTO central_execution_account_snapshots("
                "snapshot_id,environment,account_ref,as_of,received_at,document_json) "
                "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(snapshot_id) DO NOTHING",
                (
                    str(value["snapshot_id"]), str(value["environment"]), str(value["account_ref"]),
                    str(value["as_of"]), str(value["received_at"]), json.dumps(value, ensure_ascii=False),
                ),
            )
            return cursor.rowcount == 1

    def load_mock_automation_control(self, account_ref: str, *, _reader: Any = None) -> dict[str, Any] | None:
        if _reader is not None:
            return _scoped_mock_automation_control(_reader, account_ref, '%s')
        from .postgres_access import DBWriterContext, open_observed_connection

        reader = DBWriterContext(
            writer_family="read.mock_automation", writer_kind="control_state",
            operation="load_mock_automation_control", access_mode="read",
        )
        with open_observed_connection(self._connect, reader) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT document_json FROM central_documents WHERE collection=%s AND owner=%s "
                "AND document_key=%s",
                ("execution_mock_automation_control", account_ref, account_ref),
            )
            row = cursor.fetchone()
        return _json_document(row[0]) if row else None

    def load_active_execution_intents(
        self, environment: str, account_ref: str, run_id: str, *, _reader: Any = None,
    ) -> list[dict[str, Any]]:
        from .postgres_access import DBWriterContext, open_observed_connection

        if _reader is not None:
            rows = _active_execution_rows(_reader, environment, account_ref, run_id, "%s")
        else:
            reader = DBWriterContext(
                writer_family="read.execution", writer_kind="active_intents",
                operation="load_active_execution_intents", access_mode="read",
            )
            with open_observed_connection(self._connect, reader) as connection, connection.cursor() as cursor:
                rows = _active_execution_rows(cursor, environment, account_ref, run_id, "%s")
        return [_json_document(row[0]) for row in rows]

    def save_mock_automation_control(
        self, value: dict[str, Any], *, expected_revision: int, resume_owner_token: str | None = None,
    ) -> bool:
        from .postgres_access import DBWriterContext, open_observed_connection

        account_ref = str(value.get("account_ref") or "")
        revision = int(value.get("control_revision") or 0)
        if not account_ref or revision != expected_revision + 1:
            raise ValueError("invalid mock automation control revision")
        writer = DBWriterContext(
            writer_family="mock_automation.control",
            writer_kind="mock_automation_control",
            operation="save_mock_automation_control",
        )
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            now_provider = getattr(self, "_execution_wall_now", None) or kst_now
            if resume_owner_token is not None:
                run_id = value["execution_run_id"]
                _require_execution_ownership(cursor, {"owner_key": f"mock:{account_ref}",
                    "owner_token": f"{run_id}:{resume_owner_token}", "run_id": run_id},
                    {"environment": "mock", "account_ref": account_ref}, "%s", now_provider=now_provider)
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (
                f"mock-automation-control:{account_ref}",
            ))
            cursor.execute(
                "SELECT document_json FROM central_documents WHERE collection=%s AND owner=%s "
                "AND document_key=%s FOR UPDATE",
                ("execution_mock_automation_control", account_ref, account_ref),
            )
            row = cursor.fetchone()
            current = _json_document(row[0]) if row else {}
            if int(current.get("control_revision", 0)) != expected_revision:
                return False
            _require_account_control_change(cursor, "%s", value, current, account_ref)
            _require_account_resume_control(cursor, "%s", value, current, resume_owner_token, now_provider)
            if value.get('account_selection') is None:
                _require_planned_resume_control(cursor, "%s", value, current, resume_owner_token, now_provider)
            cursor.execute(
                "INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
                "VALUES(%s,%s,%s,%s,%s) ON CONFLICT(collection,owner,document_key) DO UPDATE SET "
                "updated_at=EXCLUDED.updated_at,document_json=EXCLUDED.document_json",
                (
                    "execution_mock_automation_control", account_ref, account_ref,
                    datetime.fromisoformat(str(value["changed_at"])).timestamp(),
                    json.dumps(value, ensure_ascii=False),
                ),
            )
        return True

    def replace_mock_automation_control(self, value: dict[str, Any], *, expected_revision: int,
            expected_selection_id: str, owner_token: str) -> bool:
        from .postgres_access import DBWriterContext, open_observed_connection

        account = str(value.get('account_ref') or '')
        if not account or value.get('control_revision') != expected_revision + 1 or not owner_token:
            raise ValueError('invalid mock account replacement revision or owner')
        writer = DBWriterContext(writer_family='mock_automation.control', writer_kind='mock_automation_control',
            operation='replace_mock_automation_control')
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            now_provider = getattr(self, '_execution_wall_now', None) or kst_now
            run_id = value['execution_run_id']
            _require_execution_ownership(cursor, {'owner_key': f'mock:{account}',
                'owner_token': f'{run_id}:{owner_token}', 'run_id': run_id},
                {'environment': 'mock', 'account_ref': account}, '%s', now_provider=now_provider)
            cursor.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (f'mock-automation-control:{account}',))
            current = _plan_document(cursor, '%s', 'execution_mock_automation_control', account, account) or {}
            if (current.get('control_revision') != expected_revision
                    or (current.get('account_selection') or {}).get('selection_id') != expected_selection_id):
                return False
            _require_account_replacement_control(cursor, '%s', value, current, owner_token, now_provider)
            cursor.execute('UPDATE central_documents SET updated_at=%s,document_json=%s '
                'WHERE collection=%s AND owner=%s AND document_key=%s',
                (datetime.fromisoformat(value['changed_at']).timestamp(), json.dumps(value, ensure_ascii=False),
                 'execution_mock_automation_control', account, account))
        return True

    def acquire_execution_runtime(
        self, owner_key: str, owner_token: str, now: str, lease_expires_at: str, *, _connection: Any = None,
        selected_management_context=None, selected_management_owner=None,
    ) -> bool:
        from .postgres_access import DBWriterContext, observe_existing_transaction, open_observed_connection

        writer = DBWriterContext(
            writer_family="execution.runtime_lease", writer_kind="execution_runtime_acquire",
            operation="acquire_execution_runtime", rows_attempted=1,
        )
        transaction = (open_observed_connection(self._connect, writer) if _connection is None
                       else observe_existing_transaction(_connection, writer, require_idle=True))
        with transaction as connection, connection.cursor() as cursor:
            try:
                owner_key, selected = _execution_runtime_lease_scope(cursor, '%s', owner_key, owner_token,
                    datetime.fromisoformat(now), selected_management_context=selected_management_context,
                    selected_management_owner=selected_management_owner)
            except RuntimeError as error:
                if str(error) in {'SELECTED_MANAGEMENT_CONTEXT_REQUIRED', 'EXECUTION_CANONICAL_OWNERSHIP_CONFLICT'}:
                    return False
                raise
            cursor.execute(
                "INSERT INTO central_execution_runtime_leases(owner_key,owner_token,lease_expires_at,updated_at) "
                "VALUES(%s,%s,%s,%s) ON CONFLICT(owner_key) DO UPDATE SET owner_token=EXCLUDED.owner_token,"
                "lease_expires_at=EXCLUDED.lease_expires_at,updated_at=EXCLUDED.updated_at "
                "WHERE central_execution_runtime_leases.owner_token=EXCLUDED.owner_token OR "
                "central_execution_runtime_leases.lease_expires_at<=EXCLUDED.updated_at",
                (owner_key, owner_token, lease_expires_at, now),
            )
            if selected is not None and not selected_management_owner():
                raise RuntimeError('SELECTED_MANAGEMENT_OWNER_CHANGED')
            return cursor.rowcount == 1
