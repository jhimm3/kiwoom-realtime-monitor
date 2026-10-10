"""Account preferences, market role selection and fenced real-account evidence.

Credential activation borrows the cursor-based helpers in its existing transaction.
Each store method retains the backend connection, lock and commit ownership.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime
from enum import Enum
from time import time
from typing import Any

from kiwoom_monitor.central_server.database_account_identity import _canonical_account_ref


def _verified_settings_scope(cursor: Any, scope: Any, p: str) -> dict[str, str]:
    if (not isinstance(scope, dict) or set(scope) != {"broker", "environment", "account_ref"}
            or scope.get("broker") != "kiwoom" or not isinstance(scope.get("environment"), str)
            or scope["environment"] not in {"real", "mock"}):
        raise ValueError("ACCOUNT_SETTINGS_SCOPE_INVALID")
    try:
        account_ref = _canonical_account_ref(scope["account_ref"])
    except (ValueError, TypeError, AttributeError):
        raise ValueError("ACCOUNT_SETTINGS_SCOPE_INVALID") from None
    cursor.execute(f"SELECT 1 FROM central_account_registry WHERE account_ref={p} AND broker={p} "
                   f"AND environment={p} AND status='active'",
                   (account_ref, scope["broker"], scope["environment"]))
    if cursor.fetchone() is None:
        raise ValueError("ACCOUNT_IDENTITY_UNVERIFIED")
    return {"broker": "kiwoom", "environment": scope["environment"], "account_ref": account_ref}


def _account_settings_owner(scope: dict[str, str]) -> str:
    return f"{scope['broker']}:{scope['environment']}:{scope['account_ref']}"


def _verify_real_account_write(cursor, binding, settings_revision, p):
    from kiwoom_monitor.domain.order_contract import AccountBinding, AccountEnvironment
    if (not isinstance(binding, AccountBinding) or binding.scope.environment != AccountEnvironment.REAL
            or type(settings_revision) is not int):
        raise ValueError("REAL_ACCOUNT_RECOVERY_INVALID")
    scope = binding.scope.to_dict()
    settings = _load_account_settings(cursor, scope, p)
    if (settings["active_profile_id"] != binding.credential_profile_id
            or settings["revision"] != settings_revision or not settings["monitor_enabled"]):
        raise ValueError("ACCOUNT_CONTEXT_MISMATCH")
    cursor.execute(f"SELECT account_ref,binding_revision FROM central_account_binding_revisions "
                   f"WHERE credential_profile_id={p} AND broker={p} AND environment='real' "
                   "ORDER BY binding_revision DESC LIMIT 1", (binding.credential_profile_id, scope["broker"]))
    row = cursor.fetchone()
    if row is None or tuple(row) != (scope["account_ref"], binding.binding_revision):
        raise ValueError("ACCOUNT_CONTEXT_MISMATCH")
    return scope


def _save_real_account_recovery(cursor, binding, recovery, received_at, settings_revision, p, *, wall_time=None):
    """REST evidence, never a mock execution-ledger reconciliation."""
    from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import AccountRecovery
    if (not isinstance(recovery, AccountRecovery) or not isinstance(received_at, datetime)
            or received_at.utcoffset() is None):
        raise ValueError("REAL_ACCOUNT_RECOVERY_INVALID")
    scope = _verify_real_account_write(cursor, binding, settings_revision, p)
    observations = (recovery.account, *recovery.orders)
    if any(item.account_ref != scope["account_ref"] or item.as_of > received_at for item in observations):
        raise ValueError("REAL_ACCOUNT_RECOVERY_INVALID")
    def encode(value):
        if isinstance(value, datetime):
            if value.utcoffset() is None:
                raise ValueError("REAL_ACCOUNT_RECOVERY_INVALID")
            return value.isoformat()
        if isinstance(value, Enum):
            return value.value
        raise ValueError("REAL_ACCOUNT_RECOVERY_INVALID")
    document = {"scope": scope, "credential_profile_id": binding.credential_profile_id,
                "binding_revision": binding.binding_revision, "settings_revision": settings_revision,
                "source": "kiwoom_rest", "received_at": received_at.isoformat(),
                "recovery": asdict(recovery)}
    serialized = json.dumps(document, default=encode, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    key = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    cursor.execute("INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
                   f"VALUES({','.join([p] * 5)}) ON CONFLICT(collection,owner,document_key) DO NOTHING",
                   ("real_account_recovery", _account_settings_owner(scope), key,
                    time() if wall_time is None else wall_time(), serialized))
    return json.loads(serialized)


def _save_real_account_event(cursor, binding, event_type, event, received_at, settings_revision, p, *, wall_time=None):
    from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import AccountBalanceChange, OrderExecution
    expected = {"order_execution": OrderExecution, "account_balance": AccountBalanceChange}.get(event_type)
    if (expected is None or type(event) is not expected or not isinstance(received_at, datetime)
            or received_at.utcoffset() is None):
        raise ValueError("REAL_ACCOUNT_EVENT_INVALID")
    scope = _verify_real_account_write(cursor, binding, settings_revision, p)
    if event.origin_scope.to_dict() != scope:
        raise ValueError("ACCOUNT_CONTEXT_MISMATCH")
    payload = asdict(event)
    payload["origin_scope"] = scope
    document = {"scope": scope, "credential_profile_id": binding.credential_profile_id,
                "binding_revision": binding.binding_revision, "settings_revision": settings_revision,
                "source": "kiwoom_websocket", "event_type": event_type, "received_at": received_at.isoformat(),
                "event": payload}
    serialized = json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    key = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    cursor.execute("INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
                   f"VALUES({','.join([p] * 5)}) ON CONFLICT(collection,owner,document_key) DO NOTHING",
                   ("real_account_event", _account_settings_owner(scope), key,
                    time() if wall_time is None else wall_time(), serialized))
    return document


def _load_market_profile_settings(cursor: Any, p: str) -> dict[str, Any]:
    """Role policy is independent of UI account selection and legacy query routing."""
    import re
    cursor.execute("SELECT document_json FROM central_documents WHERE collection='server_market_profile_settings' "
                   "AND owner='global' AND document_key='settings'", ())
    row = cursor.fetchone()
    if row is None:
        return {"market_profile_id": "nas-real-default", "legacy_real_profile_id": "nas-real-default", "revision": 0}
    try:
        value = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        if (not isinstance(value, dict) or set(value) != {"market_profile_id", "legacy_real_profile_id", "revision"}
                or type(value["revision"]) is not int or not 1 <= value["revision"] <= 2**63 - 1
                or not isinstance(value["market_profile_id"], str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", value["market_profile_id"])
                or value["legacy_real_profile_id"] != "nas-real-default"):
            raise ValueError
    except (ValueError, TypeError, KeyError):
        raise ValueError("MARKET_PROFILE_SETTINGS_RECOVERY_REQUIRED") from None
    return value


def _save_market_profile_settings(cursor: Any, value: dict[str, Any], expected_revision: int,
                                  p: str) -> dict[str, Any]:
    """Validate and CAS persisted role intent; the runtime applies it separately."""
    import re
    if (not isinstance(value, dict) or set(value) != {"market_profile_id", "expected_binding_revision"}
            or not isinstance(value["market_profile_id"], str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", value["market_profile_id"])
            or type(expected_revision) is not int or not 0 <= expected_revision <= 2**63 - 1
            or type(value["expected_binding_revision"]) is not int
            or not 1 <= value["expected_binding_revision"] <= 2**63 - 1):
        raise ValueError("MARKET_PROFILE_SETTINGS_INVALID")
    current = _load_market_profile_settings(cursor, p)
    if current["revision"] != expected_revision:
        raise ValueError("MARKET_PROFILE_SETTINGS_REVISION_CONFLICT")
    profile_id = value["market_profile_id"]
    cursor.execute(f"SELECT provider,environment,lifecycle_state FROM central_credential_profiles WHERE profile_id={p}",
                   (profile_id,))
    profile = cursor.fetchone()
    if profile is None or tuple(profile) != ("kiwoom_real", "real", "active"):
        raise ValueError("MARKET_PROFILE_UNAVAILABLE")
    cursor.execute(f"SELECT account_ref,binding_revision FROM central_account_binding_revisions "
                   f"WHERE credential_profile_id={p} AND broker='kiwoom' AND environment='real' "
                   "ORDER BY binding_revision DESC LIMIT 1", (profile_id,))
    binding = cursor.fetchone()
    if binding is None:
        raise ValueError("ACCOUNT_IDENTITY_UNVERIFIED")
    if binding[1] != value["expected_binding_revision"]:
        raise ValueError("ACCOUNT_CONTEXT_MISMATCH")
    settings = _load_account_settings(cursor, {
        "broker": "kiwoom", "environment": "real", "account_ref": binding[0]}, p)
    if settings["active_profile_id"] != profile_id:
        raise ValueError("MARKET_PROFILE_UNAVAILABLE")
    if expected_revision and current["market_profile_id"] == profile_id:
        return current
    if expected_revision == 2**63 - 1:
        raise ValueError("MARKET_PROFILE_SETTINGS_REVISION_EXHAUSTED")
    document = {"market_profile_id": profile_id, "legacy_real_profile_id": current["legacy_real_profile_id"],
                "revision": expected_revision + 1}
    cursor.execute("INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
                   f"VALUES({','.join([p] * 5)}) ON CONFLICT(collection,owner,document_key) "
                   "DO UPDATE SET updated_at=excluded.updated_at, document_json=excluded.document_json",
                   ("server_market_profile_settings", "global", "settings", time(), json.dumps(document)))
    return document


def _load_account_settings(cursor: Any, scope: dict[str, str], p: str) -> dict[str, Any]:
    scope = _verified_settings_scope(cursor, scope, p)
    cursor.execute("SELECT document_json FROM central_documents WHERE collection='server_account_settings' "
                   f"AND owner={p} AND document_key='settings'", (_account_settings_owner(scope),))
    row = cursor.fetchone()
    if row is None:
        return {"scope": scope, "active_profile_id": None, "monitor_enabled": False,
                "mock_order_enabled": False, "revision": 0}
    try:
        document = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        if (not isinstance(document, dict) or set(document) != {
                "scope", "active_profile_id", "monitor_enabled", "mock_order_enabled", "revision"}
                or document["scope"] != scope or type(document["revision"]) is not int
                or not 1 <= document["revision"] <= 2**63 - 1
                or type(document["monitor_enabled"]) is not bool
                or type(document["mock_order_enabled"]) is not bool
                or (document["active_profile_id"] is not None
                    and (not isinstance(document["active_profile_id"], str) or not document["active_profile_id"]))
                or (document["monitor_enabled"] and document["active_profile_id"] is None)
                or (document["mock_order_enabled"]
                    and (scope["environment"] != "mock" or not document["monitor_enabled"]))):
            raise ValueError
    except (ValueError, TypeError, KeyError):
        raise ValueError("ACCOUNT_SETTINGS_RECOVERY_REQUIRED") from None
    return document


def _save_account_settings(cursor: Any, value: dict[str, Any], expected_revision: int,
                           p: str) -> dict[str, Any]:
    if (not isinstance(value, dict) or set(value) != {
            "scope", "active_profile_id", "monitor_enabled", "mock_order_enabled"}
            or type(expected_revision) is not int or not 0 <= expected_revision <= 2**63 - 1
            or type(value["monitor_enabled"]) is not bool or type(value["mock_order_enabled"]) is not bool):
        raise ValueError("ACCOUNT_SETTINGS_INVALID")
    current = _load_account_settings(cursor, value["scope"], p)
    if current["revision"] != expected_revision:
        raise ValueError("ACCOUNT_SETTINGS_REVISION_CONFLICT")
    scope = current["scope"]
    profile_id = value["active_profile_id"]
    if (profile_id is None and scope["environment"] == "real" and current["active_profile_id"] is not None
            and _load_market_profile_settings(cursor, p)["market_profile_id"] == current["active_profile_id"]):
        raise ValueError("MARKET_PROFILE_REQUIRED")
    if (profile_id is not None and (not isinstance(profile_id, str) or not profile_id or len(profile_id) > 200)
            or (profile_id is None and (value["monitor_enabled"] or value["mock_order_enabled"]))
            or (value["mock_order_enabled"] and (scope["environment"] != "mock" or not value["monitor_enabled"]))):
        raise ValueError("ACCOUNT_SETTINGS_INVALID")
    if profile_id is not None:
        cursor.execute(f"SELECT provider,environment,lifecycle_state FROM central_credential_profiles WHERE profile_id={p}",
                       (profile_id,))
        profile = cursor.fetchone()
        if profile is None or tuple(profile) != (f"kiwoom_{scope['environment']}", scope["environment"], "active"):
            raise ValueError("ACCOUNT_PROFILE_UNAVAILABLE")
        cursor.execute(f"SELECT account_ref FROM central_account_binding_revisions WHERE credential_profile_id={p} "
                       f"AND broker={p} AND environment={p} ORDER BY binding_revision DESC LIMIT 1",
                       (profile_id, scope["broker"], scope["environment"]))
        binding = cursor.fetchone()
        if binding is None or binding[0] != scope["account_ref"]:
            raise ValueError("ACCOUNT_PROFILE_SCOPE_MISMATCH")
        if current["active_profile_id"] not in {None, profile_id}:
            raise ValueError("ACCOUNT_PROFILE_CONFLICT")
    document = {**value, "scope": scope, "revision": expected_revision}
    if expected_revision and document == current:
        return current  # Unchanged configuration does not create another revision.
    if expected_revision == 2**63 - 1:
        raise ValueError("ACCOUNT_SETTINGS_REVISION_EXHAUSTED")
    document["revision"] += 1
    cursor.execute("INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
                   f"VALUES({','.join([p] * 5)}) ON CONFLICT(collection,owner,document_key) "
                   "DO UPDATE SET updated_at=excluded.updated_at, document_json=excluded.document_json",
                   ("server_account_settings", _account_settings_owner(scope), "settings", time(), json.dumps(document)))
    return document


class SQLiteAccountSettingsStoreMixin:
    def load_account_settings(self, scope: dict[str, str]) -> dict[str, Any]:
        with self._lock, self._connection() as connection:
            return _load_account_settings(connection.cursor(), scope, "?")

    def save_real_account_recovery(self, binding, recovery, received_at, *, settings_revision):
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return _save_real_account_recovery(connection.cursor(), binding, recovery, received_at,
                                              settings_revision, "?",
                                              wall_time=getattr(self, '_account_input_wall_time', None))

    def save_real_account_event(self, binding, event_type, event, received_at, *, settings_revision):
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return _save_real_account_event(connection.cursor(), binding, event_type, event, received_at,
                                            settings_revision, "?",
                                            wall_time=getattr(self, '_account_input_wall_time', None))

    def save_account_settings(self, value: dict[str, Any], *, expected_revision: int) -> dict[str, Any]:
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return _save_account_settings(connection.cursor(), value, expected_revision, "?")

    def load_market_profile_settings(self) -> dict[str, Any]:
        with self._lock, self._connection() as connection:
            return _load_market_profile_settings(connection.cursor(), "?")

    def save_market_profile_settings(self, value: dict[str, Any], *, expected_revision: int) -> dict[str, Any]:
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return _save_market_profile_settings(connection.cursor(), value, expected_revision, "?")


class PostgresAccountSettingsStoreMixin:
    def load_account_settings(self, scope: dict[str, str]) -> dict[str, Any]:
        from .postgres_access import DBWriterContext, open_observed_connection

        reader = DBWriterContext(
            writer_family="read.account", writer_kind="account_settings",
            operation="load_account_settings", access_mode="read",
        )
        with open_observed_connection(self._connect, reader) as connection, connection.cursor() as cursor:
            return _load_account_settings(cursor, scope, "%s")

    def save_real_account_recovery(self, binding, recovery, received_at, *, settings_revision):
        from .postgres_access import DBWriterContext, open_observed_connection

        writer = DBWriterContext(
            writer_family="account.real_monitor", writer_kind="real_account_recovery",
            operation="save_real_account_recovery", rows_attempted=1,
        )
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("credential-activation",))
            return _save_real_account_recovery(cursor, binding, recovery, received_at, settings_revision, "%s",
                                              wall_time=getattr(self, '_account_input_wall_time', None))

    def save_real_account_event(self, binding, event_type, event, received_at, *, settings_revision):
        from .postgres_access import DBWriterContext, open_observed_connection

        writer = DBWriterContext(
            writer_family="account.real_monitor", writer_kind="real_account_event",
            operation="save_real_account_event", rows_attempted=1,
        )
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("credential-activation",))
            return _save_real_account_event(cursor, binding, event_type, event, received_at, settings_revision, "%s",
                                           wall_time=getattr(self, '_account_input_wall_time', None))

    def save_account_settings(self, value: dict[str, Any], *, expected_revision: int) -> dict[str, Any]:
        from .postgres_access import DBWriterContext, open_observed_connection

        writer = DBWriterContext(
            writer_family="account.settings", writer_kind="account_settings_save",
            operation="save_account_settings", rows_attempted=1,
        )
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("credential-activation",))
            return _save_account_settings(cursor, value, expected_revision, "%s")

    def load_market_profile_settings(self) -> dict[str, Any]:
        from .postgres_access import DBWriterContext, open_observed_connection

        reader = DBWriterContext(
            writer_family="read.account", writer_kind="market_profile_settings",
            operation="load_market_profile_settings", access_mode="read",
        )
        with open_observed_connection(self._connect, reader) as connection, connection.cursor() as cursor:
            return _load_market_profile_settings(cursor, "%s")

    def save_market_profile_settings(self, value: dict[str, Any], *, expected_revision: int) -> dict[str, Any]:
        from .postgres_access import DBWriterContext, open_observed_connection

        writer = DBWriterContext(
            writer_family="account.settings", writer_kind="market_profile_settings_save",
            operation="save_market_profile_settings", rows_attempted=1,
        )
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("credential-activation",))
            return _save_market_profile_settings(cursor, value, expected_revision, "%s")
