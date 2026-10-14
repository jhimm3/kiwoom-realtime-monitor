"""Execution ledger, mock control and runtime lease DB implementations.

Each store method retains its native connection and transaction ownership.
Ownership checks borrow that transaction's cursor so lease/control fences and
intent/event writes remain atomic. No broker or order scheduling belongs here.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from kiwoom_monitor.central_server.database_codec import _event_document, _json_document

def _require_execution_ownership(cursor: Any, ownership: dict[str, Any] | None,
                                 value: dict[str, Any], p: str, *, now_provider=None,
                                 new_intent_claim=False) -> None:
    if ownership is None:
        return  # Offline ledger/import callers keep the existing unowned contract.
    if new_intent_claim and ownership.get('cancel_retry_claim') is not None:
        raise RuntimeError('MOCK_PROTECTIVE_CANCEL_RETRY_CANNOT_CREATE_ORDER')
    if (not ownership["owner_token"].startswith(f"{ownership['run_id']}:")
            or value.get("environment") != "mock" or ownership["owner_key"] != f"mock:{value.get('account_ref')}"
            or ("run_id" in value and value["run_id"] != ownership["run_id"])):
        raise RuntimeError("EXECUTION_OWNER_SCOPE_MISMATCH")
    cursor.execute("SELECT owner_token,lease_expires_at FROM central_execution_runtime_leases "
                   f"WHERE owner_key={p}" + (" FOR UPDATE" if p == "%s" else ""), (ownership["owner_key"],))
    row = cursor.fetchone()
    expiry = row[1] if row and isinstance(row[1], datetime) else datetime.fromisoformat(str(row[1])) if row else None
    if not row or row[0] != ownership["owner_token"] or expiry <= (
            datetime.now(timezone.utc) if now_provider is None else now_provider()):
        raise RuntimeError("EXECUTION_OWNERSHIP_LOST")
    control_revision = ownership.get("control_revision")
    if control_revision is not None:
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
        if (
            control.get("desired_state") != "RUNNING"
            or int(control.get("control_revision", 0)) != int(control_revision)
            or control.get("active_spec_id") != ownership.get("active_spec_id")
            or control.get("execution_run_id") != ownership["run_id"]
        ):
            raise RuntimeError("MOCK_AUTOMATION_CONTROL_CHANGED")
    if ownership.get('replacement_claim') is not None and 'intent_id' in value:
        _require_mock_replacement_claim(cursor, ownership, value, p, new_intent_claim=new_intent_claim)
    if ownership.get('protection_claim') is not None and 'intent_id' in value:
        _require_mock_protection_claim(cursor, ownership, value, p, now_provider=now_provider,new_intent_claim=new_intent_claim)
    if "intent_id" in value:
        cursor.execute(f"SELECT environment,account_ref,run_id FROM central_execution_intents WHERE intent_id={p}",
                       (value["intent_id"],))
        stored = cursor.fetchone()
        if stored is not None and tuple(stored) != (value["environment"], value["account_ref"], value["run_id"]):
            raise RuntimeError("EXECUTION_OWNER_SCOPE_MISMATCH")


def _require_mock_replacement_claim(cursor, ownership, value, p, *, new_intent_claim):
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
    cursor.execute(f'SELECT intent_id FROM central_execution_intents WHERE environment={p} AND account_ref={p} '
                   f"AND state NOT IN ('FILLED','CANCELLED','REJECTED','EXPIRED') AND intent_id<>{p} LIMIT 1" + lock,
                   ('mock', value['account_ref'], value['intent_id']))
    if cursor.fetchone():
        raise RuntimeError('MOCK_REPLACEMENT_ACCOUNT_ORDER_IN_FLIGHT')
    if new_intent_claim:
        cursor.execute(f'SELECT intent_id FROM central_execution_intents WHERE intent_id={p}' + lock, (value['intent_id'],))
        if cursor.fetchone():
            raise RuntimeError('MOCK_REPLACEMENT_ALREADY_CLAIMED')


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
    checkpoint=document('execution_mock_automation_runner_current','current')
    workflow=checkpoint.get('protection_exit')
    decision=validate_mock_protection_exit(workflow,spec,ownership['run_id'])
    notice=checkpoint.get('protection_review',{});snapshot=workflow['snapshot']
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
            or value.get('venue') != 'KRX' or value.get('order_type') != 'LIMIT'
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
        validate_mock_stop_loss_proof(snapshot['request_trigger']['stop_loss'],spec,risk,stop_loss_bps=config.stop_loss_bps)
        if datetime.fromisoformat(snapshot['price_available_at']) > risk.observed_at:
            raise RuntimeError('MOCK_STOP_LOSS_ACCOUNT_CUT_NOT_ALIGNED')
    now=datetime.now(timezone.utc) if now_provider is None else now_provider()
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


def _require_mock_cancel_retry(cursor, ownership, value, event, p, *, now_provider=None):
    """Fence a protective retry in the same native CANCEL_STARTED transaction."""
    claim = ownership.get('cancel_retry_claim') if ownership else None
    if claim is None:
        return event
    from kiwoom_monitor.application.order_lifecycle import (
        protective_cancel_recovery, validate_unfilled_attempt_plan, UnfilledOrderManagementPolicy, unfilled_plan_intent,
    )
    from kiwoom_monitor.application.mock_automation_execution import unfilled_execution_policy_rejections
    from kiwoom_monitor.application.mock_automation_risk import mock_automation_risk_snapshot_from_dict
    from kiwoom_monitor.domain.execution_activation import mock_automation_spec_from_dict
    from kiwoom_monitor.infrastructure.persistence.execution_repository import _record_from_document
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
    checkpoint = document('execution_mock_automation_runner_current','current')
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
            or scope['execution_run_id'] != ownership['run_id'] or scope['control_revision'] != ownership['control_revision']
            or unfilled_plan_intent(plan['anchor']['root_intent']) != root.intent
            or policy.protection_deadline > root.intent.created_at + timedelta(seconds=frozen.max_snapshot_age_seconds)
            or unfilled_execution_policy_rejections(spec,policy,root)):
        raise RuntimeError('MOCK_PROTECTIVE_CANCEL_RETRY_POLICY_CHANGED')
    ids = (root.intent.intent_id, *(child['intent']['intent_id'] for child in plan['children']))
    cursor.execute('SELECT document_json FROM central_execution_events WHERE intent_id IN ('+
                   ','.join(p for _ in ids)+') ORDER BY accepted_sequence'+lock,ids)
    events = tuple(_json_document(row[0]) for row in cursor.fetchall())
    now = datetime.now(timezone.utc) if now_provider is None else now_provider()
    cue = protective_cancel_recovery(native,events,policy,frozen,now)
    risk = mock_automation_risk_snapshot_from_dict(document('execution_mock_automation_current_risk',value['account_ref']))
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


class SQLiteExecutionStoreMixin:
    def load_account_active_execution_intents(self, environment: str, account_ref: str, limit: int) -> list[dict[str, Any]]:
        if environment != "mock" or not account_ref.strip() or type(limit) is not int or not 1 <= limit <= 401:
            raise ValueError("bounded mock account ledger scope is required")
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                "SELECT document_json FROM central_execution_intents WHERE environment=? AND account_ref=? "
                "AND state NOT IN ('FILLED','CANCELLED','REJECTED','EXPIRED') ORDER BY created_at,intent_id LIMIT ?",
                (environment, account_ref, limit)).fetchall()
        return [_json_document(row[0]) for row in rows]

    def load_mock_automation_lineage(
        self, account_ref: str, *, spec_id: str, admission_id: str,
        lease_receipt_id: str, recovery_decision_id: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        sql, parameters = _mock_automation_lineage_query(
            account_ref, spec_id, admission_id, lease_receipt_id, recovery_decision_id, "?",
        )
        with self._lock, self._connection() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return {str(role): _json_document(document) for role, document in rows}

    def release_execution_runtime(self, owner_key: str, owner_token: str) -> bool:
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "DELETE FROM central_execution_runtime_leases WHERE owner_key=? AND owner_token=?",
                (owner_key, owner_token),
            )
            return cursor.rowcount == 1

    def create_execution_intent(self, value: dict[str, Any], *, ownership: dict[str, str] | None = None) -> bool:
        document = _event_document(value)
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            _require_execution_ownership(connection.cursor(), ownership, value, "?",
                                         now_provider=getattr(self, '_execution_wall_now', None), new_intent_claim=True)
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
            _require_execution_ownership(connection.cursor(), ownership, intent, "?",
                                         now_provider=getattr(self, '_execution_wall_now', None))
            event = _require_mock_cancel_retry(connection.cursor(), ownership, intent, event, '?',
                                              now_provider=getattr(self, '_execution_wall_now', None))
            event_document = _event_document(event)
            if connection.execute(
                "SELECT 1 FROM central_execution_events WHERE event_id=?", (str(event["event_id"]),),
            ).fetchone() is not None:
                return False
            if connection.execute(
                "SELECT 1 FROM central_execution_intents WHERE intent_id=?", (str(intent["intent_id"]),),
            ).fetchone() is None:
                raise KeyError(f"unknown execution intent: {intent['intent_id']}")
            intent = _retain_execution_claim(connection.cursor(), intent, '?')
            intent_document = _event_document(intent)
            connection.execute(
                "INSERT INTO central_execution_events("
                "event_id,intent_id,state,occurred_at,received_at,broker_execution_id,document_json) "
                "VALUES(?,?,?,?,?,?,?)",
                _execution_event_values(event, event_document),
            )
            connection.execute(
                "UPDATE central_execution_intents SET state=?,broker_order_id=?,last_broker_as_of=?,"
                "updated_at=?,document_json=? WHERE intent_id=?",
                (
                    str(intent["state"]), str(intent.get("broker_order_id") or ""),
                    intent.get("last_broker_as_of"), str(intent["updated_at"]), intent_document,
                    str(intent["intent_id"]),
                ),
            )
        return True

    def load_execution_intent(self, intent_id: str) -> dict[str, Any] | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT document_json FROM central_execution_intents WHERE intent_id=?", (intent_id,),
            ).fetchone()
        return _json_document(row[0]) if row else None

    def find_execution_intent_by_broker_order_id(
        self, environment: str, account_ref: str, run_id: str, broker_order_id: str,
    ) -> dict[str, Any] | None:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                "SELECT document_json FROM central_execution_intents "
                "WHERE environment=? AND account_ref=? AND run_id=? AND broker_order_id=? LIMIT 2",
                (environment, account_ref, run_id, broker_order_id),
            ).fetchall()
        if len(rows) > 1:
            raise ValueError("broker order id matches multiple execution intents")
        return _json_document(rows[0][0]) if rows else None

    def load_execution_events(self, intent_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                "SELECT document_json FROM central_execution_events WHERE intent_id=? ORDER BY accepted_sequence",
                (intent_id,),
            ).fetchall()
        return [_json_document(row[0]) for row in rows]

    def load_account_execution_events(
        self, environment: str, account_ref: str, after_sequence: int, limit: int,
    ) -> list[dict[str, Any]]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                "SELECT e.accepted_sequence,i.document_json,e.document_json "
                "FROM central_execution_events e JOIN central_execution_intents i "
                "ON i.intent_id=e.intent_id WHERE i.environment=? AND i.account_ref=? "
                "AND e.accepted_sequence>? ORDER BY e.accepted_sequence LIMIT ?",
                (environment, account_ref, after_sequence, limit),
            ).fetchall()
        return [
            {"accepted_sequence": int(row[0]), "intent": _json_document(row[1]),
             "event": _json_document(row[2])}
            for row in rows
        ]

    def save_execution_account_snapshot(self, value: dict[str, Any], *, ownership: dict[str, str] | None = None) -> bool:
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            _require_execution_ownership(connection.cursor(), ownership, value, "?",
                                         now_provider=getattr(self, '_execution_wall_now', None))
            cursor = connection.execute(
                "INSERT OR IGNORE INTO central_execution_account_snapshots("
                "snapshot_id,environment,account_ref,as_of,received_at,document_json) VALUES(?,?,?,?,?,?)",
                (
                    str(value["snapshot_id"]), str(value["environment"]), str(value["account_ref"]),
                    str(value["as_of"]), str(value["received_at"]), _event_document(value),
                ),
            )
        return cursor.rowcount == 1

    def load_mock_automation_control(self, account_ref: str) -> dict[str, Any] | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT document_json FROM central_documents WHERE collection=? AND owner=? "
                "AND document_key=?",
                ("execution_mock_automation_control", account_ref, account_ref),
            ).fetchone()
        return _json_document(row[0]) if row else None

    def load_active_execution_intents(
        self, environment: str, account_ref: str, run_id: str,
    ) -> list[dict[str, Any]]:
        terminal = ("FILLED", "CANCELLED", "REJECTED", "EXPIRED")
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                "SELECT document_json FROM central_execution_intents WHERE environment=? "
                "AND account_ref=? AND run_id=? AND state NOT IN (?,?,?,?) ORDER BY created_at",
                (environment, account_ref, run_id, *terminal),
            ).fetchall()
        return [_json_document(row[0]) for row in rows]

    def save_mock_automation_control(
        self, value: dict[str, Any], *, expected_revision: int,
    ) -> bool:
        account_ref = str(value.get("account_ref") or "")
        revision = int(value.get("control_revision") or 0)
        if not account_ref or revision != expected_revision + 1:
            raise ValueError("invalid mock automation control revision")
        document = _event_document(value)
        changed_at = datetime.fromisoformat(str(value["changed_at"])).timestamp()
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT document_json FROM central_documents WHERE collection=? AND owner=? "
                "AND document_key=?",
                ("execution_mock_automation_control", account_ref, account_ref),
            ).fetchone()
            current = int(_json_document(row[0]).get("control_revision", 0)) if row else 0
            if current != expected_revision:
                return False
            connection.execute(
                "INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
                "VALUES(?,?,?,?,?) ON CONFLICT(collection,owner,document_key) DO UPDATE SET "
                "updated_at=excluded.updated_at,document_json=excluded.document_json",
                ("execution_mock_automation_control", account_ref, account_ref, changed_at, document),
            )
        return True

    def acquire_execution_runtime(
        self, owner_key: str, owner_token: str, now: str, lease_expires_at: str,
    ) -> bool:
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "INSERT INTO central_execution_runtime_leases(owner_key,owner_token,lease_expires_at,updated_at) "
                "VALUES(?,?,?,?) ON CONFLICT(owner_key) DO UPDATE SET owner_token=excluded.owner_token,"
                "lease_expires_at=excluded.lease_expires_at,updated_at=excluded.updated_at "
                "WHERE central_execution_runtime_leases.owner_token=excluded.owner_token OR "
                "central_execution_runtime_leases.lease_expires_at<=excluded.updated_at",
                (owner_key, owner_token, lease_expires_at, now),
            )
        return cursor.rowcount == 1

class PostgresExecutionStoreMixin:
    def load_account_active_execution_intents(self, environment: str, account_ref: str, limit: int) -> list[dict[str, Any]]:
        if environment != "mock" or not account_ref.strip() or type(limit) is not int or not 1 <= limit <= 401:
            raise ValueError("bounded mock account ledger scope is required")
        from .postgres_access import DBWriterContext, open_observed_connection
        reader = DBWriterContext(writer_family="read.execution", writer_kind="account_active_intents",
            operation="load_account_active_execution_intents", access_mode="read")
        with open_observed_connection(self._connect, reader) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT document_json FROM central_execution_intents WHERE environment=%s AND account_ref=%s "
                "AND state NOT IN ('FILLED','CANCELLED','REJECTED','EXPIRED') ORDER BY created_at,intent_id LIMIT %s",
                (environment, account_ref, limit))
            rows = cursor.fetchall()
        return [_json_document(row[0]) for row in rows]

    def load_mock_automation_lineage(
        self, account_ref: str, *, spec_id: str, admission_id: str,
        lease_receipt_id: str, recovery_decision_id: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        from .postgres_access import DBWriterContext, open_observed_connection

        sql, parameters = _mock_automation_lineage_query(
            account_ref, spec_id, admission_id, lease_receipt_id, recovery_decision_id, "%s",
        )
        reader = DBWriterContext(
            writer_family="read.mock_automation", writer_kind="immutable_lineage",
            operation="load_mock_automation_lineage", access_mode="read",
        )
        with open_observed_connection(self._connect, reader) as connection, connection.cursor() as cursor:
            cursor.execute(sql, parameters)
            rows = cursor.fetchall()
        return {str(role): _json_document(document) for role, document in rows}

    def release_execution_runtime(self, owner_key: str, owner_token: str) -> bool:
        from .postgres_access import DBWriterContext, open_observed_connection

        writer = DBWriterContext(
            writer_family="execution.runtime_lease", writer_kind="execution_runtime_release",
            operation="release_execution_runtime", rows_attempted=1,
        )
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
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
            _require_execution_ownership(cursor, ownership, value, "%s",
                                         now_provider=getattr(self, '_execution_wall_now', None), new_intent_claim=True)
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

    def append_execution_event(self, intent: dict[str, Any], event: dict[str, Any], *, ownership: dict[str, str] | None = None) -> bool:
        from .postgres_access import DBWriterContext, open_observed_connection

        writer = DBWriterContext(
            writer_family="execution.event", writer_kind="execution_event",
            operation="append_execution_event", rows_attempted=1,
        )
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            _require_execution_ownership(cursor, ownership, intent, "%s",
                                         now_provider=getattr(self, '_execution_wall_now', None))
            event = _require_mock_cancel_retry(cursor, ownership, intent, event, '%s',
                                              now_provider=getattr(self, '_execution_wall_now', None))
            cursor.execute(
                "INSERT INTO central_execution_events("
                "event_id,intent_id,state,occurred_at,received_at,broker_execution_id,document_json) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(event_id) DO NOTHING",
                _execution_event_values(event, json.dumps(event, ensure_ascii=False)),
            )
            if cursor.rowcount != 1:
                return False
            intent = _retain_execution_claim(cursor, intent, '%s')
            cursor.execute(
                "UPDATE central_execution_intents SET state=%s,broker_order_id=%s,last_broker_as_of=%s,"
                "updated_at=%s,document_json=%s WHERE intent_id=%s",
                (
                    str(intent["state"]), str(intent.get("broker_order_id") or ""),
                    intent.get("last_broker_as_of"), str(intent["updated_at"]),
                    json.dumps(intent, ensure_ascii=False), str(intent["intent_id"]),
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown execution intent: {intent['intent_id']}")
            return True

    def load_execution_intent(self, intent_id: str) -> dict[str, Any] | None:
        from .postgres_access import DBWriterContext, open_observed_connection

        reader = DBWriterContext(
            writer_family="read.execution", writer_kind="intent_by_id",
            operation="load_execution_intent", access_mode="read",
        )
        with open_observed_connection(self._connect, reader) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT document_json FROM central_execution_intents WHERE intent_id=%s", (intent_id,),
            )
            row = cursor.fetchone()
        return _json_document(row[0]) if row else None

    def find_execution_intent_by_broker_order_id(
        self, environment: str, account_ref: str, run_id: str, broker_order_id: str,
    ) -> dict[str, Any] | None:
        from .postgres_access import DBWriterContext, open_observed_connection

        reader = DBWriterContext(
            writer_family="read.execution", writer_kind="intent_by_broker_order",
            operation="find_execution_intent_by_broker_order_id", access_mode="read",
        )
        with open_observed_connection(self._connect, reader) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT document_json FROM central_execution_intents "
                "WHERE environment=%s AND account_ref=%s AND run_id=%s AND broker_order_id=%s LIMIT 2",
                (environment, account_ref, run_id, broker_order_id),
            )
            rows = cursor.fetchall()
        if len(rows) > 1:
            raise ValueError("broker order id matches multiple execution intents")
        return _json_document(rows[0][0]) if rows else None

    def load_execution_events(self, intent_id: str) -> list[dict[str, Any]]:
        from .postgres_access import DBWriterContext, open_observed_connection

        reader = DBWriterContext(
            writer_family="read.execution", writer_kind="intent_events",
            operation="load_execution_events", access_mode="read",
        )
        with open_observed_connection(self._connect, reader) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT document_json FROM central_execution_events WHERE intent_id=%s ORDER BY accepted_sequence",
                (intent_id,),
            )
            rows = cursor.fetchall()
        return [_json_document(row[0]) for row in rows]

    def load_account_execution_events(
        self, environment: str, account_ref: str, after_sequence: int, limit: int,
    ) -> list[dict[str, Any]]:
        from .postgres_access import DBWriterContext, open_observed_connection

        reader = DBWriterContext(
            writer_family="read.execution", writer_kind="account_events",
            operation="load_account_execution_events", access_mode="read",
        )
        with open_observed_connection(self._connect, reader) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT e.accepted_sequence,i.document_json,e.document_json "
                "FROM central_execution_events e JOIN central_execution_intents i "
                "ON i.intent_id=e.intent_id WHERE i.environment=%s AND i.account_ref=%s "
                "AND e.accepted_sequence>%s ORDER BY e.accepted_sequence LIMIT %s",
                (environment, account_ref, after_sequence, limit),
            )
            rows = cursor.fetchall()
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

    def load_mock_automation_control(self, account_ref: str) -> dict[str, Any] | None:
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
        self, environment: str, account_ref: str, run_id: str,
    ) -> list[dict[str, Any]]:
        terminal = ("FILLED", "CANCELLED", "REJECTED", "EXPIRED")
        from .postgres_access import DBWriterContext, open_observed_connection

        reader = DBWriterContext(
            writer_family="read.execution", writer_kind="active_intents",
            operation="load_active_execution_intents", access_mode="read",
        )
        with open_observed_connection(self._connect, reader) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT document_json FROM central_execution_intents WHERE environment=%s "
                "AND account_ref=%s AND run_id=%s AND state NOT IN (%s,%s,%s,%s) ORDER BY created_at",
                (environment, account_ref, run_id, *terminal),
            )
            rows = cursor.fetchall()
        return [_json_document(row[0]) for row in rows]

    def save_mock_automation_control(
        self, value: dict[str, Any], *, expected_revision: int,
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
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (
                f"mock-automation-control:{account_ref}",
            ))
            cursor.execute(
                "SELECT document_json FROM central_documents WHERE collection=%s AND owner=%s "
                "AND document_key=%s FOR UPDATE",
                ("execution_mock_automation_control", account_ref, account_ref),
            )
            row = cursor.fetchone()
            current = int(_json_document(row[0]).get("control_revision", 0)) if row else 0
            if current != expected_revision:
                return False
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

    def acquire_execution_runtime(
        self, owner_key: str, owner_token: str, now: str, lease_expires_at: str,
    ) -> bool:
        from .postgres_access import DBWriterContext, open_observed_connection

        writer = DBWriterContext(
            writer_family="execution.runtime_lease", writer_kind="execution_runtime_acquire",
            operation="acquire_execution_runtime", rows_attempted=1,
        )
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO central_execution_runtime_leases(owner_key,owner_token,lease_expires_at,updated_at) "
                "VALUES(%s,%s,%s,%s) ON CONFLICT(owner_key) DO UPDATE SET owner_token=EXCLUDED.owner_token,"
                "lease_expires_at=EXCLUDED.lease_expires_at,updated_at=EXCLUDED.updated_at "
                "WHERE central_execution_runtime_leases.owner_token=EXCLUDED.owner_token OR "
                "central_execution_runtime_leases.lease_expires_at<=EXCLUDED.updated_at",
                (owner_key, owner_token, lease_expires_at, now),
            )
            return cursor.rowcount == 1
