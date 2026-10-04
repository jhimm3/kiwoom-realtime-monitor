"""Execution ledger, mock control and runtime lease DB implementations.

Each store method retains its native connection and transaction ownership.
Ownership checks borrow that transaction's cursor so lease/control fences and
intent/event writes remain atomic. No broker or order scheduling belongs here.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from kiwoom_monitor.central_server.database_codec import _event_document, _json_document

def _require_execution_ownership(cursor: Any, ownership: dict[str, Any] | None,
                                 value: dict[str, Any], p: str) -> None:
    if ownership is None:
        return  # Offline ledger/import callers keep the existing unowned contract.
    if (not ownership["owner_token"].startswith(f"{ownership['run_id']}:")
            or value.get("environment") != "mock" or ownership["owner_key"] != f"mock:{value.get('account_ref')}"
            or ("run_id" in value and value["run_id"] != ownership["run_id"])):
        raise RuntimeError("EXECUTION_OWNER_SCOPE_MISMATCH")
    cursor.execute("SELECT owner_token,lease_expires_at FROM central_execution_runtime_leases "
                   f"WHERE owner_key={p}" + (" FOR UPDATE" if p == "%s" else ""), (ownership["owner_key"],))
    row = cursor.fetchone()
    expiry = row[1] if row and isinstance(row[1], datetime) else datetime.fromisoformat(str(row[1])) if row else None
    if not row or row[0] != ownership["owner_token"] or expiry <= datetime.now(timezone.utc):
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
    if "intent_id" in value:
        cursor.execute(f"SELECT environment,account_ref,run_id FROM central_execution_intents WHERE intent_id={p}",
                       (value["intent_id"],))
        stored = cursor.fetchone()
        if stored is not None and tuple(stored) != (value["environment"], value["account_ref"], value["run_id"]):
            raise RuntimeError("EXECUTION_OWNER_SCOPE_MISMATCH")


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


class SQLiteExecutionStoreMixin:
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
            _require_execution_ownership(connection.cursor(), ownership, value, "?")
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
            _require_execution_ownership(connection.cursor(), ownership, intent, "?")
            if connection.execute(
                "SELECT 1 FROM central_execution_events WHERE event_id=?", (str(event["event_id"]),),
            ).fetchone() is not None:
                return False
            if connection.execute(
                "SELECT 1 FROM central_execution_intents WHERE intent_id=?", (str(intent["intent_id"]),),
            ).fetchone() is None:
                raise KeyError(f"unknown execution intent: {intent['intent_id']}")
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
            _require_execution_ownership(connection.cursor(), ownership, value, "?")
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
            _require_execution_ownership(cursor, ownership, value, "%s")
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
            _require_execution_ownership(cursor, ownership, intent, "%s")
            cursor.execute(
                "INSERT INTO central_execution_events("
                "event_id,intent_id,state,occurred_at,received_at,broker_execution_id,document_json) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(event_id) DO NOTHING",
                _execution_event_values(event, json.dumps(event, ensure_ascii=False)),
            )
            if cursor.rowcount != 1:
                return False
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
            _require_execution_ownership(cursor, ownership, value, "%s")
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
