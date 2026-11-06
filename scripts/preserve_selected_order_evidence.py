"""Explicit offline capture and acceptance of one unresolved planned mock order."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable

from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.database_execution import PLAN_POSITION_COLLECTION
from kiwoom_monitor.domain.selected_order_evidence import (
    AMENDED_ORDER_FIELDS, AMENDED_VERSION, MEMBER_VERSION, MANAGEMENT_VERSION, COLLECTION, EVENT_FIELDS, ORDER_FIELDS, OWNER, PLAN_FIELDS, VERSION,
    encode_selected_order_evidence, PLAN_MANAGEMENT_VERSION, pack_selected_plan_order,
)
from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository
from kiwoom_monitor.infrastructure.persistence.sqlite_read_snapshot import readonly_sqlite_snapshot


def capture_selected_order_evidence(
    *, source: Path, source_instance: str, operation_id: str, account_ref: str, run_id: str, intent_id: str,
) -> tuple[dict[str, Any], str]:
    """Read source-owned order evidence and its cursor from a single frozen SQLite cut."""
    packet, digest, _ = _capture_selected_order_evidence(source=source, source_instance=source_instance,
        operation_id=operation_id, account_ref=account_ref, run_id=run_id, intent_id=intent_id)
    return packet, digest


def _capture_selected_order_evidence(*, source, source_instance, operation_id, account_ref, run_id, intent_id,
                                     identity_owner=None, credential_profile_id=None, broker_evidence=None, management=False,
                                     plan_ledger=False):
    store = SQLiteQueryStore(source)
    repository = ExecutionRepository(store)
    with readonly_sqlite_snapshot(source, frozen=True) as connection, closing(connection.cursor()) as reader:
        control = store.load_mock_automation_control(account_ref, _reader=reader)
        if (not control or (control["account_ref"], control["execution_run_id"], control["desired_state"])
                != (account_ref, run_id, "STOPPED")):
            raise ValueError("selected-order source account and run must be stopped")
        if reader.execute("SELECT 1 FROM central_execution_runtime_leases WHERE owner_key=?",
                          (f"mock:{account_ref}",)).fetchone() is not None:
            raise ValueError("selected-order source still holds a runtime lease")

        active, complete = repository.account_active_intents(account_ref, _reader=reader)
        order = store.load_execution_intent(intent_id, _reader=reader)
        if (not complete or not order or (order["account_ref"], order["run_id"]) != (account_ref, run_id)
                or not order.get("plan") or not order.get("mock_plan_approval")
                or order.get("mock_replacement_claim") or order.get("mock_protection_claim")
                or not plan_ledger and all(record.intent.intent_id != intent_id for record in active)):
            raise ValueError("selected-order source has no unambiguous original unresolved intent")
        plan_id = order["plan"]["plan_id"]
        if not plan_ledger and any(record.intent.intent_id != intent_id and getattr(record.intent, "plan", None)
               and record.intent.plan.plan_id == plan_id for record in active):
            raise ValueError("selected-order source plan has another active intent")
        position = store.load_document(PLAN_POSITION_COLLECTION, account_ref, plan_id, _reader=reader)
        if position is None or position["document"].get("unreconciled_sell_quantity"):
            raise ValueError("selected-order source plan position is missing or unreconciled")

        source_cursor = 0
        sequences = {}
        while True:
            page = repository.account_events("mock", account_ref, after_sequence=source_cursor, limit=1000,
                                             _reader=reader)
            for event in page.events:
                if event.plan is None or event.plan.plan_id != plan_id:
                    continue
                if (not plan_ledger and event.intent_id != intent_id or event.run_id != run_id
                        or event.source_event_id in sequences):
                    raise ValueError("selected-order source plan has conflicting event lineage")
                sequences[event.source_event_id] = event.accepted_sequence
            source_cursor = page.next_cursor
            if not page.has_more:
                break

        plan_orders = None
        if plan_ledger:
            rows = reader.execute("SELECT document_json FROM central_execution_intents WHERE environment='mock' AND account_ref=? "
                "AND json_extract(document_json,'$.plan.plan_id')=?", (account_ref, plan_id)).fetchall()
            originals = [json.loads(row[0]) for row in rows]
            raw_events = tuple(event for original in originals for event in repository.events(original['intent_id'], _reader=reader))
            raw_events = tuple(sorted(raw_events, key=lambda event: sequences.get(event['event_id'], 0)))
            first = {original['intent_id']: min((sequences[event['event_id']] for event in raw_events
                if event['intent_id'] == original['intent_id']), default=0) for original in originals}
            plan_orders = [pack_selected_plan_order(original) for original in sorted(originals,
                key=lambda value: (first[value['intent_id']], value['intent_id']))]
        else:
            raw_events = repository.events(intent_id, _reader=reader)
        if len(raw_events) != len(sequences) or {event["event_id"] for event in raw_events} != sequences.keys():
            raise ValueError("selected-order source event cursors are incomplete")
        # Keep raw facts; v2 also retains the structured observation parent/revision
        # and typed amendment assessment. Other free-form reasons and native authority stay at the source.
        events = ([{**event, 'accepted_sequence': sequences[event['event_id']]} for event in raw_events] if plan_ledger else [
            {**{key: event[key] for key in EVENT_FIELDS}, "accepted_sequence": sequences[event["event_id"]],
             **({'reason': event['reason']} if order.get('broker_legs') and event['event_type'] == 'AMENDMENT_FILL_OBSERVED' else {})}
            for event in raw_events
        ])
        packet = {
            "version": AMENDED_VERSION if order.get('broker_legs') else VERSION,
            "source_instance": source_instance, "operation_id": operation_id,
            "account_ref": account_ref, "run_id": run_id, "source_spec_id": control["active_spec_id"],
            "stopped_at": control["changed_at"],
            "order": {key: order[key] for key in (AMENDED_ORDER_FIELDS if order.get('broker_legs') else ORDER_FIELDS)},
            "events": events, "source_cursor": source_cursor,
            "plan_position": {key: position["document"][key] for key in PLAN_FIELDS},
        }
        member = order.get('mock_account_member_spec_id')
        if member is not None:
            from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import ForwardEvaluationRepository
            evidence_repository = ForwardEvaluationRepository(store)
            admission = evidence_repository.load_mock_automation_admission_for_spec(account_ref, member, _reader=reader)
            selection = control.get('account_selection') or {}
            if (admission is None or member not in selection.get('member_spec_ids', ())
                    or admission.execution_run_id != run_id
                    or admission.account_selection_id != selection.get('selection_id')
                    or position['document'].get('spec_id') != member):
                raise ValueError('selected-order native member lineage is incomplete')
            packet.update(version=MEMBER_VERSION, source_spec_id=member, member_spec_id=member,
                member_admission_id=admission.admission_id, account_selection_id=admission.account_selection_id,
                anchor_spec_id=control['active_spec_id'])
        if management:
            documents = []
            def retain(collection, owner, key, *, required=True):
                for item in documents:
                    if (item['collection'], item['owner'], item['key']) == (collection, owner, key):
                        return item['document']
                row = store.load_document(collection, owner, key, _reader=reader)
                if row is None:
                    if required: raise ValueError('selected-order management policy closure is missing: ' + collection)
                    return None
                if collection in {'execution_mock_automation_runner_current', 'execution_mock_automation_clock_current'}:
                    from kiwoom_monitor.application.selected_order_recovery import management_checkpoint_matches
                    if not management_checkpoint_matches(packet, row['document']): return None
                documents.append(dict(collection=collection, owner=owner, key=key, document=row['document']))
                return row['document']
            spec = retain('execution_mock_automation_specs', account_ref, packet['source_spec_id'])
            retain('execution_mock_automation_candidate_packages', spec['strategy_ref'], spec['candidate_package_hash'])
            retain('execution_mock_automation_admission_by_spec', account_ref, packet['source_spec_id'])
            retain('execution_mock_automation_approved_gates', account_ref, intent_id)
            shadow = None
            kind = order['policy_version']
            if kind in {'close_execution_adapter/v1', 'panic_execution_adapter/v1'}:
                proof = retain('execution_close_entry_evidence' if kind.startswith('close_') else 'execution_panic_entry_evidence', account_ref, plan_id)
                row = reader.execute('SELECT document_json,expires_at FROM central_shadow_candidate_events WHERE event_id=?',
                    (proof['snapshot']['candidate_event_id'],)).fetchone()
                if row is None: raise ValueError('selected-order original Shadow candidate is missing')
                shadow = {**json.loads(row[0]), 'expires_at': row[1]}
            elif kind == 'leader_execution_adapter/v1':
                retain('execution_leader_position_context', account_ref, plan_id)
                retain('execution_leader_decisions', account_ref, intent_id if member is not None else order['decision_id'])
            else:
                raise ValueError('selected-order management policy is unsupported')
            if plan_ledger:
                for item in plan_orders:
                    original = item['order']
                    if original['intent_id'] != intent_id:
                        retain('execution_mock_automation_approved_gates', account_ref, original['intent_id'])
                        if original['policy_version'] == 'leader_execution_adapter/v1':
                            retain('execution_leader_decisions', account_ref,
                                original['intent_id'] if member is not None else original['decision_id'])
                selected_ids = {item['order']['intent_id'] for item in plan_orders}
                rows = reader.execute("SELECT document_key,document_json FROM central_documents "
                    "WHERE collection='execution_leader_position_actions' AND owner=?", (account_ref,)).fetchall()
                for key, raw in rows:
                    value = json.loads(raw)
                    if value.get('intent_id') in selected_ids and value.get('plan_id') == plan_id:
                        retain('execution_leader_position_actions', account_ref, key)
            checkpoint_key = 'member:' + member if member is not None else 'current'
            retain('execution_mock_automation_runner_current', account_ref, checkpoint_key, required=False)
            retain('execution_mock_automation_clock_current', account_ref, checkpoint_key, required=False)
            packet.update(version=MANAGEMENT_VERSION, management_closure=dict(version='selected-order-management-closure/v1',
                ledger_scope='single-original-intent/v1', documents=documents, source_plan_position=position['document'],
                origin_approval_id=order['mock_plan_approval'], shadow_candidate=shadow))
            if plan_ledger:
                packet.update(version=PLAN_MANAGEMENT_VERSION, plan_orders=plan_orders)
                packet['management_closure'].update(version='selected-order-management-closure/v2', ledger_scope='single-plan-ledger/v1')
            from kiwoom_monitor.application.selected_order_recovery import (
                validate_management_closure, management_checkpoint_references, management_plan_references)
            inspected = set()
            while True:
                values = {(item['collection'], item['owner'], item['key']): item['document'] for item in documents}
                references = (management_checkpoint_references(packet, values) | management_plan_references(packet, values)) - inspected
                if not references: break
                for collection, owner, key in sorted(references):
                    inspected.add((collection, owner, key))
                    retain(collection, owner, key, required=False)
            validate_management_closure(packet, allow_missing_checkpoint_dependencies=True)
        _, digest = encode_selected_order_evidence(packet)
        proof = None
        if identity_owner is not None:
            proof = identity_owner.issue_selected_order_identity_proof(packet, digest,
                credential_profile_id=credential_profile_id, broker_evidence=broker_evidence)
    return packet, digest, proof


def capture_selected_order_recovery_bundle(*, source, source_instance, operation_id, account_ref, run_id, intent_id,
                                           identity_owner, credential_profile_id, broker_evidence=None):
    """Capture an owner-issued detached identity proof in the same closed source cut.

    The existing source identity owner retains its key and reads its own native
    facts. Neither its key nor the registry fingerprint is exported.
    """
    return _capture_selected_order_evidence(source=source, source_instance=source_instance, operation_id=operation_id,
        account_ref=account_ref, run_id=run_id, intent_id=intent_id, identity_owner=identity_owner,
        credential_profile_id=credential_profile_id, broker_evidence=broker_evidence)


def capture_selected_order_management_bundle(*, source, source_instance, operation_id, account_ref, run_id, intent_id,
                                             identity_owner, credential_profile_id, broker_evidence=None):
    """Export only this plan's verified immutable policy closure; import grants no authority."""
    return _capture_selected_order_evidence(source=source, source_instance=source_instance, operation_id=operation_id,
        account_ref=account_ref, run_id=run_id, intent_id=intent_id, identity_owner=identity_owner,
        credential_profile_id=credential_profile_id, broker_evidence=broker_evidence, management=True)


def capture_selected_plan_management_bundle(*, source, source_instance, operation_id, account_ref, run_id, intent_id,
                                            identity_owner, credential_profile_id, broker_evidence=None):
    """One selected native plan's complete ledger; old packet entry points stay unchanged."""
    return _capture_selected_order_evidence(source=source, source_instance=source_instance, operation_id=operation_id,
        account_ref=account_ref, run_id=run_id, intent_id=intent_id, identity_owner=identity_owner,
        credential_profile_id=credential_profile_id, broker_evidence=broker_evidence, management=True, plan_ledger=True)


def accept_selected_order_evidence(
    *, target: Path, packet: dict[str, Any], expected_sha256: str, account_ref: str, run_id: str,
    now_provider: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Store the packet without populating execution tables. Retry with the original packet and digest."""
    with closing(SQLiteQueryStore(target, now_provider=now_provider)) as store:
        return store.accept_selected_order_evidence(packet, expected_sha256=expected_sha256,
                                                     account_ref=account_ref, run_id=run_id)


def inspect_selected_order_evidence(*, target: Path, operation_id: str) -> dict[str, Any] | None:
    """Read the retained packet and receipt from a closed target without opening a writable store."""
    with readonly_sqlite_snapshot(target, frozen=True) as connection, closing(connection.cursor()) as reader:
        row = SQLiteQueryStore(target).load_document(COLLECTION, OWNER, operation_id, _reader=reader)
        return row["document"] if row is not None else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("capture", help="capture a selected order from a stopped source")
    capture.add_argument("--source", type=Path, required=True)
    capture.add_argument("--source-instance", required=True)
    capture.add_argument("--operation-id", required=True)
    capture.add_argument("--intent-id", required=True)
    accept = commands.add_parser("accept", help="accept pinned evidence in an offline target")
    accept.add_argument("--target", type=Path, required=True)
    accept.add_argument("--sha256", required=True)
    inspect = commands.add_parser("inspect", help="read the first acceptance receipt")
    inspect.add_argument("--target", type=Path, required=True)
    inspect.add_argument("--operation-id", required=True)
    for command in (capture, accept):
        command.add_argument("--account-ref", required=True)
        command.add_argument("--run-id", required=True)
        command.add_argument("--packet", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "capture":
            packet, digest = capture_selected_order_evidence(
                source=args.source, source_instance=args.source_instance, operation_id=args.operation_id,
                account_ref=args.account_ref, run_id=args.run_id, intent_id=args.intent_id,
            )
            encoded, _ = encode_selected_order_evidence(packet)
            with TemporaryDirectory(dir=args.packet.parent) as stage:
                staged = Path(stage) / "packet.json"
                staged.write_bytes(encoded)
                os.link(staged, args.packet)
            result = {"operation_id": args.operation_id, "sha256": digest}
        elif args.command == "accept":
            if args.target.resolve() == args.packet.resolve():
                raise ValueError("the packet and offline target must be different files")
            encoded = args.packet.read_bytes()
            if sha256(encoded).hexdigest() != args.sha256:
                raise ValueError("selected-order packet file SHA256 does not match")
            result = accept_selected_order_evidence(
                target=args.target, packet=json.loads(encoded), expected_sha256=args.sha256,
                account_ref=args.account_ref, run_id=args.run_id,
            )
        else:
            evidence = inspect_selected_order_evidence(target=args.target, operation_id=args.operation_id)
            result = {"receipt": evidence["receipt"] if evidence else None}
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
