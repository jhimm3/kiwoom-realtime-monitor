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


def _require_execution_ownership(cursor: Any, ownership: dict[str, Any] | None,
                                 value: dict[str, Any], p: str, *, now_provider=None,
                                 new_intent_claim=False, submission_claim=False):
    if ownership is None:
        if "plan" in value:
            raise RuntimeError("PLANNED_EXECUTION_OWNER_REQUIRED")
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
            kst_now() if now_provider is None else now_provider()):
        raise RuntimeError("EXECUTION_OWNERSHIP_LOST")
    control_revision = ownership.get("control_revision")
    entry_claim = (new_intent_claim or submission_claim) and value.get("side") == "BUY"
    if control_revision is not None or (entry_claim and ownership["run_id"].startswith("mock_auto_run_")):
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
        if control_revision is not None and (
            control.get("desired_state") != "RUNNING"
            or int(control.get("control_revision", 0)) != int(control_revision)
            or control.get("active_spec_id") != ownership.get("active_spec_id")
            or control.get("execution_run_id") != ownership["run_id"]
        ):
            raise RuntimeError("MOCK_AUTOMATION_CONTROL_CHANGED")
        if (entry_claim and control.get("execution_run_id") == ownership["run_id"]
                and control.get("entry_resume_required", False)):
            raise RuntimeError("CREDENTIAL_CHANGE_REQUIRES_EXPLICIT_ENTRY_RESUME")
    if ownership.get('replacement_claim') is not None and 'intent_id' in value:
        _require_mock_replacement_claim(cursor, ownership, value, p, new_intent_claim=new_intent_claim,
            now_provider=now_provider)
    if ownership.get('protection_claim') is not None and 'intent_id' in value:
        _require_mock_protection_claim(cursor, ownership, value, p, now_provider=now_provider,new_intent_claim=new_intent_claim)
    if (new_intent_claim or (submission_claim and "plan" in value)) and value.get('policy_version') == 'leader_execution_adapter/v1':
        _require_leader_execution_claim(cursor, ownership, value, p, now_provider=now_provider)
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
            return stored_value


def _plan_document(cursor, p, collection, account, key):
    cursor.execute(
        f"SELECT document_json FROM central_documents WHERE collection={p} AND owner={p} AND document_key={p}"
        + (" FOR UPDATE" if p == "%s" else ""),
        (collection, account, key),
    )
    row = cursor.fetchone()
    return _json_document(row[0]) if row is not None else None


def _save_plan_position(cursor, p, account, document, changed_at):
    cursor.execute(
        f"INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
        f"VALUES({p},{p},{p},{p},{p}) ON CONFLICT(collection,owner,document_key) DO UPDATE SET "
        "updated_at=excluded.updated_at,document_json=excluded.document_json",
        (PLAN_POSITION_COLLECTION, account, document["plan_id"], changed_at.timestamp(), _event_document(document)),
    )


def _require_plan_approval(cursor, p, value, ownership, now_provider):
    from dataclasses import asdict

    from kiwoom_monitor.application.mock_automation_execution import (
        MOCK_AUTOMATION_DECISION_GATE_VERSION,
        MockAutomationGateStatus,
        mock_automation_decision_gate_from_dict,
        mock_automation_intent_id,
        mock_automation_order_policy_version,
    )
    from kiwoom_monitor.application.mock_automation_recovery import (
        MockAutomationRecoveryStatus,
        mock_account_recovery_fingerprint,
        mock_automation_recovery_decision_from_dict,
    )
    from kiwoom_monitor.application.mock_automation_risk import mock_automation_risk_snapshot_from_dict
    from kiwoom_monitor.domain.execution_activation import forward_spec_from_dict, mock_automation_spec_from_dict
    from kiwoom_monitor.domain.order_contract import TERMINAL_ORDER_STATES

    recovery = ownership.get("plan_recovery") if ownership is not None else None
    if recovery is None:
        raise RuntimeError("PLANNED_EXECUTION_APPROVAL_REQUIRED")
    account_ref = value["account_ref"]
    gate_doc = _plan_document(cursor, p, "execution_mock_automation_approved_gates", account_ref, value["intent_id"])
    if gate_doc is None:
        raise RuntimeError("PLANNED_EXECUTION_APPROVAL_REQUIRED")
    gate = mock_automation_decision_gate_from_dict(gate_doc)
    if (
        gate.plan is None
        or asdict(gate.plan) != value["plan"]
        or gate.status is not MockAutomationGateStatus.APPROVED_FOR_SINGLE_SUBMISSION
        or gate.version != MOCK_AUTOMATION_DECISION_GATE_VERSION
        or gate.spec_id != ownership.get("active_spec_id")
        or gate.control_revision != ownership.get("control_revision")
        or (gate.account_ref, gate.execution_run_id, gate.strategy_decision_id,
            gate.intent_id, gate.symbol, gate.quantity, gate.limit_price) != (
            account_ref, value["run_id"], value["decision_id"], value["intent_id"],
            value["symbol"], value["quantity"], value["limit_price"])
        or value["intent_id"] != mock_automation_intent_id(gate.spec_id, gate.strategy_decision_id)
        or value["side"] != {"ENTER": "BUY", "EXIT": "SELL"}.get(gate.action)
        or value["order_type"] != "LIMIT"
        or gate.decided_at != datetime.fromisoformat(value["created_at"])
    ):
        raise RuntimeError("PLANNED_EXECUTION_APPROVAL_CHANGED")
    spec_doc = _plan_document(cursor, p, "execution_mock_automation_specs", account_ref, gate.spec_id)
    risk_doc = _plan_document(cursor, p, "execution_mock_automation_current_risk", account_ref, account_ref)
    recovery_doc = _plan_document(
        cursor, p, "execution_mock_automation_current_recovery", account_ref, gate.admission_id,
    )
    if spec_doc is None or risk_doc is None or recovery_doc is None:
        raise RuntimeError("PLANNED_EXECUTION_RISK_OR_RECOVERY_MISSING")
    spec = mock_automation_spec_from_dict(spec_doc)
    profile_doc = _plan_document(cursor, p, "execution_forward_profiles", spec.strategy_ref, spec.forward_profile_id)
    if profile_doc is None:
        raise RuntimeError("PLANNED_EXECUTION_FORWARD_PROFILE_MISSING")
    profile = forward_spec_from_dict(profile_doc)
    if (
        (profile.profile_id, profile.account_ref, profile.strategy_ref, profile.environment, profile.session_profile)
        != (spec.forward_profile_id, account_ref, spec.strategy_ref, value["environment"], spec.session_profile)
        or value["policy_version"] != mock_automation_order_policy_version(profile.family_version, spec.session_profile)
    ):
        raise RuntimeError("PLANNED_EXECUTION_FORWARD_PROFILE_CHANGED")
    risk = mock_automation_risk_snapshot_from_dict(risk_doc)
    current = mock_automation_recovery_decision_from_dict(recovery_doc)
    now = kst_now() if now_provider is None else now_provider()
    max_age = spec.maximum_data_gap_seconds
    expiry = datetime.fromisoformat(value["expires_at"])
    fingerprint = mock_account_recovery_fingerprint(recovery)
    statuses = {MockAutomationRecoveryStatus.CLEARED_ORDERS_DISABLED}
    if value["side"] == "SELL":
        statuses.add(MockAutomationRecoveryStatus.MANAGE_ONLY_ORDERS_DISABLED)
    if (
        spec.account_scope.account_ref != account_ref or spec.spec_id != gate.spec_id
        or spec.strategy_ref != gate.strategy_ref or value["venue"] != spec.supported_venue
        or risk.binding_revision != spec.binding_revision or not risk.reconciliation_complete
        or (risk.account_ref, risk.execution_run_id) != (account_ref, value["run_id"])
        or (risk.snapshot_id, risk.reconciliation_revision) != (gate.risk_snapshot_id, gate.risk_reconciliation_revision)
        or (current.account_ref, current.execution_run_id, current.spec_id) != (account_ref, value["run_id"], gate.spec_id)
        or (current.decision_id, current.lease_receipt_id, current.recovery_fingerprint)
        != (gate.recovery_decision_id, gate.lease_receipt_id, fingerprint)
        or (current.risk_snapshot_id, current.risk_reconciliation_revision) != (risk.snapshot_id, risk.reconciliation_revision)
        or current.status not in statuses
        or (gate.recovery_fingerprint, gate.account_as_of) != (fingerprint, recovery.account.as_of)
        or risk.account_as_of != recovery.account.as_of or recovery.account.account_ref != account_ref
        or max_age is None or now >= expiry or expiry != gate.decided_at + timedelta(seconds=max(1, int(max_age)))
        or any(not 0 <= (now - at).total_seconds() <= max_age
               for at in (gate.decided_at, gate.server_now, risk.observed_at, risk.account_as_of))
        or any(order.state not in TERMINAL_ORDER_STATES for order in recovery.orders)
    ):
        raise RuntimeError("PLANNED_EXECUTION_RISK_OR_RECOVERY_CHANGED")
    event_type = "e.document_json->>'event_type'" if p == "%s" else "json_extract(e.document_json,'$.event_type')"
    cursor.execute(
        "SELECT 1 FROM central_execution_events e JOIN central_execution_intents i ON i.intent_id=e.intent_id "
        f"WHERE i.environment='mock' AND i.account_ref={p} AND e.accepted_sequence>{p} "
        f"AND {event_type} IN ('FILL','BROKER_FILL_AGGREGATE') LIMIT 1",
        (account_ref, risk.execution_event_high_watermark),
    )
    if cursor.fetchone() is not None:
        raise RuntimeError("PLANNED_EXECUTION_RISK_OR_RECOVERY_CHANGED")
    return gate.gate_id, spec


def _claim_plan_position(cursor, p, value, ownership, now_provider):
    gate_id, spec = _require_plan_approval(cursor, p, value, ownership, now_provider)
    account_ref, ref = value["account_ref"], value["plan"]
    head = _plan_document(cursor, p, PLAN_POSITION_COLLECTION, account_ref, ref["plan_id"])
    if head is None:
        head = {
            "version": "mock_plan_position/v1", "plan_id": ref["plan_id"], "policy_ref": ref["policy_ref"],
            "run_id": value["run_id"], "symbol": value["symbol"],
            "revision": 0, "quantity": 0, "fill_cursor": 0,
        }
    if (head["policy_ref"], head["run_id"], head["symbol"], head["revision"]) != (
        ref["policy_ref"], value["run_id"], value["symbol"], ref["expected_revision"],
    ):
        raise RuntimeError("EXECUTION_PLAN_REVISION_CHANGED")
    if head.get("unreconciled_sell_quantity"):
        raise RuntimeError("EXECUTION_PLAN_RECONCILIATION_REQUIRED")
    rows = _account_active_execution_rows(cursor, "mock", account_ref, 401, p)
    if len(rows) > 400:
        raise RuntimeError("EXECUTION_ACCOUNT_OPEN_ORDERS_INCOMPLETE")
    active = [_json_document(row[0]) for row in rows]
    if (spec.maximum_submission_unknown_count is not None
            and sum(row["state"] == "SUBMISSION_UNKNOWN" for row in active) > spec.maximum_submission_unknown_count):
        raise RuntimeError("EXECUTION_PLAN_SUBMISSION_UNKNOWN_LIMIT")
    account = ownership["plan_recovery"].account
    if value["side"] == "BUY":
        if any(account.positions.values()):
            raise RuntimeError("PLANNED_ENTRY_REQUIRES_FLAT_ACCOUNT")
        if any(not row.get("plan") or row["run_id"] != value["run_id"] for row in active):
            raise RuntimeError("PLANNED_EXECUTION_UNVERIFIED_OPEN_ORDER")
        buys = [row for row in active if row["side"] == "BUY"]
        if any(row.get("limit_price") is None for row in buys):
            raise RuntimeError("EXECUTION_ACCOUNT_RESERVATION_UNKNOWN")
        reserved = sum(_unfilled_quantity(row) * row["limit_price"] for row in buys)
        required = value["quantity"] * value["limit_price"]
        free = account.available_cash_won - account.reserved_open_buy_won - reserved
        capital_limit = spec.maximum_capital_won
        if required > free or (capital_limit is not None and reserved + required > capital_limit):
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
    return {**value, "mock_plan_approval": gate_id}


def _unfilled_quantity(order):
    return max(0, order["quantity"] - order.get("detailed_filled_quantity", order.get("filled_quantity", 0)))


def _plan_event_changes_position(event):
    return event["event_type"] == "FILL" or event["state"] in {"CANCELLED", "REJECTED"}


def _validate_plan_event(stored, intent, event):
    if stored is None or event["intent_id"] != intent["intent_id"]:
        raise RuntimeError("EXECUTION_PLAN_EVENT_SCOPE_CHANGED")
    fields = ("plan", "decision_id", "symbol", "side", "quantity", "order_type", "limit_price", "policy_version")
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
    if fill_id in stored["fill_ids"]:
        return False
    if (not fill_id or type(quantity) is not int or quantity <= 0 or type(price) is not int or price <= 0
            or intent["fill_ids"] != [*stored["fill_ids"], fill_id]
            or intent["detailed_filled_quantity"] != stored["detailed_filled_quantity"] + quantity
            or intent["detailed_filled_quantity"] > stored["quantity"]):
        raise RuntimeError("EXECUTION_PLAN_FILL_EVIDENCE_CHANGED")
    return True


def _apply_plan_execution_event(cursor, p, intent, event, sequence):
    ref = intent["plan"]
    account = intent["account_ref"]
    head = _plan_document(cursor, p, PLAN_POSITION_COLLECTION, account, ref["plan_id"])
    if head is None or (head["policy_ref"], head["run_id"], head["symbol"]) != (
        ref["policy_ref"], intent["run_id"], intent["symbol"],
    ):
        raise RuntimeError("EXECUTION_PLAN_POSITION_MISSING")
    if event["event_type"] == "FILL":
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


def _require_leader_execution_claim(cursor, ownership, value, p, *, now_provider):
    """Bind the already-approved generic gate to the new Family's exact evidence."""
    from kiwoom_monitor.application.leader_breakout import (
        LeaderBreakoutConfig, LeaderBreakoutPolicy, LeaderBreakoutState, evaluate_leader_breakout_trade,
        validate_leader_position_proof, leader_evaluation,
    )
    from kiwoom_monitor.application.research_families import parse_strategy_config
    from kiwoom_monitor.application.mock_automation_candidate import candidate_package_from_dict
    from kiwoom_monitor.domain.execution_activation import mock_automation_spec_from_dict
    from kiwoom_monitor.application.breakout_strategy import StrategyState
    lock = ' FOR UPDATE' if p == '%s' else ''
    def document(collection, owner, key):
        cursor.execute(f'SELECT document_json FROM central_documents WHERE collection={p} AND owner={p} AND document_key={p}'+lock,
            (collection, owner, key))
        row = cursor.fetchone()
        if not row:
            raise RuntimeError('LEADER_EXECUTION_EVIDENCE_MISSING')
        return _json_document(row[0])
    account = value['account_ref']
    spec = mock_automation_spec_from_dict(document('execution_mock_automation_specs', account, ownership['active_spec_id']))
    package = candidate_package_from_dict(document('execution_mock_automation_candidate_packages', spec.strategy_ref, spec.candidate_package_hash))
    config = parse_strategy_config(package.candidate_spec['family'], package.candidate_spec['parameters'])
    if not isinstance(config, LeaderBreakoutConfig) or package.package_hash != spec.candidate_package_hash:
        raise RuntimeError('LEADER_EXECUTION_PACKAGE_CHANGED')
    record = document('execution_leader_decisions', account, value['decision_id'])
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
        or (value["side"] == "BUY" and plan["expected_revision"] != 0)
    ):
        raise RuntimeError("LEADER_EXECUTION_PLAN_PROOF_CHANGED")
    saved = document('execution_leader_position_context', account, proof['entry_intent_id'])
    if saved != proof:
        raise RuntimeError('LEADER_PINNED_CONTEXT_CHANGED')
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
            or value['side'] != {'ENTER': 'BUY', 'EXIT': 'SELL'}.get(expected.decision.final_action)
            or value['order_type'] != config.execution_contract.order_type
            or value['limit_price'] != expected.decision.signal_reference_price):
        raise RuntimeError('LEADER_PRICE_OR_ORDER_CHANGED')
    if value['side'] == 'BUY':
        if now >= datetime.fromisoformat(raw['valid_until']):
            raise RuntimeError('LEADER_ENTRY_SIGNAL_EXPIRED')
        settings = document('server_operational_settings', 'global', 'current')
        live = LeaderBreakoutPolicy.from_dict(settings['shadow_candidate_config'])
        if (LeaderBreakoutConfig.from_dict(live.to_dict()).rules_hash != config.rules_hash
                or context not in live.contexts or now >= datetime.fromisoformat(context.valid_until)):
            raise RuntimeError('LEADER_CURRENT_ENTRY_CONTEXT_CHANGED')


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
            checkpoint = document('execution_mock_automation_runner_current', 'current')
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
        checkpoint = document('execution_mock_automation_runner_current', 'current')
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


def _require_mock_cancel_retry(cursor, ownership, value, event, p, *, now_provider=None):
    """Fence a protective retry in the same native CANCEL_STARTED transaction."""
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


class SQLiteExecutionStoreMixin:
    _supports_scoped_active_intents = True

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
            stored = _require_execution_ownership(connection.cursor(), ownership, value, "?",
                                         now_provider=getattr(self, '_execution_wall_now', None), new_intent_claim=True)
            if "plan" in value:
                if stored is not None:
                    return False
                value = _claim_plan_position(connection.cursor(), "?", value, ownership,
                                             getattr(self, "_execution_wall_now", None))
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
            stored = _require_execution_ownership(connection.cursor(), ownership, intent, "?",
                                         now_provider=getattr(self, '_execution_wall_now', None),
                                         submission_claim=event.get("event_type") == "SUBMISSION_STARTED")
            if "plan" in intent and not _validate_plan_event(stored, intent, event):
                return False
            if "plan" in intent and event["event_type"] == "SUBMISSION_STARTED":
                approval, _ = _require_plan_approval(connection.cursor(), "?", intent, ownership,
                                                    getattr(self, "_execution_wall_now", None))
                if stored is None or stored.get("mock_plan_approval") != approval:
                    raise RuntimeError("PLANNED_EXECUTION_APPROVAL_CHANGED")
            event = _require_mock_cancel_retry(connection.cursor(), ownership, intent, event, '?',
                                              now_provider=getattr(self, '_execution_wall_now', None))
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
            if "plan" in intent:
                intent = {**intent, "mock_plan_approval": stored["mock_plan_approval"]}
            intent_document = _event_document(intent)
            inserted = connection.execute(
                "INSERT INTO central_execution_events("
                "event_id,intent_id,state,occurred_at,received_at,broker_execution_id,document_json) "
                "VALUES(?,?,?,?,?,?,?)",
                _execution_event_values(event, event_document),
            )
            if "plan" in intent and _plan_event_changes_position(event):
                _apply_plan_execution_event(connection.cursor(), "?", intent, event, inserted.lastrowid)
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
                "SELECT document_json FROM central_execution_intents "
                "WHERE environment=? AND account_ref=? AND run_id=? AND broker_order_id=? LIMIT 2",
                (environment, account_ref, run_id, broker_order_id),
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
                "julianday(central_execution_runtime_leases.lease_expires_at)<=julianday(excluded.updated_at)",
                (owner_key, owner_token, lease_expires_at, now),
            )
        return cursor.rowcount == 1

class PostgresExecutionStoreMixin:
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
            stored = _require_execution_ownership(cursor, ownership, value, "%s",
                                         now_provider=getattr(self, '_execution_wall_now', None), new_intent_claim=True)
            if "plan" in value:
                if stored is not None:
                    return False
                value = _claim_plan_position(cursor, "%s", value, ownership,
                                             getattr(self, "_execution_wall_now", None))
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
            stored = _require_execution_ownership(cursor, ownership, intent, "%s",
                                         now_provider=getattr(self, '_execution_wall_now', None),
                                         submission_claim=event.get("event_type") == "SUBMISSION_STARTED")
            if "plan" in intent and not _validate_plan_event(stored, intent, event):
                return False
            if "plan" in intent and event["event_type"] == "SUBMISSION_STARTED":
                approval, _ = _require_plan_approval(cursor, "%s", intent, ownership,
                                                    getattr(self, "_execution_wall_now", None))
                if stored is None or stored.get("mock_plan_approval") != approval:
                    raise RuntimeError("PLANNED_EXECUTION_APPROVAL_CHANGED")
            event = _require_mock_cancel_retry(cursor, ownership, intent, event, '%s',
                                              now_provider=getattr(self, '_execution_wall_now', None))
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
                intent = {**intent, "mock_plan_approval": stored["mock_plan_approval"]}
            cursor.execute(
                "UPDATE central_execution_intents SET state=%s,broker_order_id=%s,last_broker_as_of=%s,"
                "updated_at=%s,document_json=%s WHERE intent_id=%s"
                + (" AND NOT (document_json ? 'plan')" if ownership is None else ""),
                (
                    str(intent["state"]), str(intent.get("broker_order_id") or ""),
                    intent.get("last_broker_as_of"), str(intent["updated_at"]),
                    json.dumps(intent, ensure_ascii=False), str(intent["intent_id"]),
                ),
            )
            if cursor.rowcount != 1:
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
                "SELECT document_json FROM central_execution_intents "
                "WHERE environment=%s AND account_ref=%s AND run_id=%s AND broker_order_id=%s LIMIT 2",
                (environment, account_ref, run_id, broker_order_id),
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
        self, owner_key: str, owner_token: str, now: str, lease_expires_at: str, *, _connection: Any = None,
    ) -> bool:
        from .postgres_access import DBWriterContext, observe_existing_transaction, open_observed_connection

        writer = DBWriterContext(
            writer_family="execution.runtime_lease", writer_kind="execution_runtime_acquire",
            operation="acquire_execution_runtime", rows_attempted=1,
        )
        transaction = (open_observed_connection(self._connect, writer) if _connection is None
                       else observe_existing_transaction(_connection, writer, require_idle=True))
        with transaction as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO central_execution_runtime_leases(owner_key,owner_token,lease_expires_at,updated_at) "
                "VALUES(%s,%s,%s,%s) ON CONFLICT(owner_key) DO UPDATE SET owner_token=EXCLUDED.owner_token,"
                "lease_expires_at=EXCLUDED.lease_expires_at,updated_at=EXCLUDED.updated_at "
                "WHERE central_execution_runtime_leases.owner_token=EXCLUDED.owner_token OR "
                "central_execution_runtime_leases.lease_expires_at<=EXCLUDED.updated_at",
                (owner_key, owner_token, lease_expires_at, now),
            )
            return cursor.rowcount == 1
