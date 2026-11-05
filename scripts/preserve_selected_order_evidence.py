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
    COLLECTION, EVENT_FIELDS, ORDER_FIELDS, OWNER, PLAN_FIELDS, VERSION, encode_selected_order_evidence,
)
from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository
from kiwoom_monitor.infrastructure.persistence.sqlite_read_snapshot import readonly_sqlite_snapshot


def capture_selected_order_evidence(
    *, source: Path, source_instance: str, operation_id: str, account_ref: str, run_id: str, intent_id: str,
) -> tuple[dict[str, Any], str]:
    """Read source-owned order evidence and its cursor from a single frozen SQLite cut."""
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
                or all(record.intent.intent_id != intent_id for record in active)):
            raise ValueError("selected-order source has no unambiguous original unresolved intent")
        plan_id = order["plan"]["plan_id"]
        if any(record.intent.intent_id != intent_id and getattr(record.intent, "plan", None)
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
                if (event.intent_id != intent_id or event.run_id != run_id
                        or event.source_event_id in sequences):
                    raise ValueError("selected-order source plan has conflicting event lineage")
                sequences[event.source_event_id] = event.accepted_sequence
            source_cursor = page.next_cursor
            if not page.has_more:
                break

        raw_events = repository.events(intent_id, _reader=reader)
        if len(raw_events) != len(sequences) or {event["event_id"] for event in raw_events} != sequences.keys():
            raise ValueError("selected-order source event cursors are incomplete")
        # Raw event IDs and facts retain historical blanks. Free-form reasons and claim payloads stay at the source.
        events = [
            {**{key: event[key] for key in EVENT_FIELDS}, "accepted_sequence": sequences[event["event_id"]]}
            for event in raw_events
        ]
        packet = {
            "version": VERSION, "source_instance": source_instance, "operation_id": operation_id,
            "account_ref": account_ref, "run_id": run_id, "source_spec_id": control["active_spec_id"],
            "stopped_at": control["changed_at"], "order": {key: order[key] for key in ORDER_FIELDS},
            "events": events, "source_cursor": source_cursor,
            "plan_position": {key: position["document"][key] for key in PLAN_FIELDS},
        }
        _, digest = encode_selected_order_evidence(packet)
    return packet, digest


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
