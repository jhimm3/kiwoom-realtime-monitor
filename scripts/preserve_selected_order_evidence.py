"""Explicit offline capture and acceptance of one unresolved planned mock order."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime
from pathlib import Path
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
