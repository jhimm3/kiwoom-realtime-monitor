"""Verified account registry, binding revisions and immutable scope aliases.

Each backend method retains its caller-visible transaction and locking boundary.
Credential activation borrows only the value helpers in its own transaction.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime
from typing import Any


def _account_registry_scope(value: dict[str, Any]) -> tuple[str, str]:
    broker = str(value.get("broker", ""))
    environment = str(value.get("environment", ""))
    if broker != "kiwoom" or environment not in {"real", "mock"}:
        raise ValueError("verified account scope is invalid")
    return broker, environment


def _account_binding_document(value: dict[str, Any]) -> dict[str, Any]:
    broker, environment = _account_registry_scope(value)
    document = {
        "credential_profile_id": str(value.get("credential_profile_id", "")).strip(),
        "broker": broker,
        "environment": environment,
        "account_ref": _canonical_account_ref(value.get("account_ref", "")),
        "verified_at": str(value.get("verified_at", "")).strip(),
        "verification_method": str(value.get("verification_method", "")),
    }
    if not document["credential_profile_id"] or not document["account_ref"]:
        raise ValueError("account binding profile and account_ref are required")
    if not document["verified_at"] or document["verification_method"] != "ka00001":
        raise ValueError("account binding verification is invalid")
    return document


def _account_binding_values(value: dict[str, Any]) -> tuple[object, ...]:
    return (
        value["binding_id"], value["credential_profile_id"], value["broker"],
        value["environment"], value["account_ref"], value["binding_revision"],
        value["verified_at"], value["verification_method"],
    )


def _canonical_account_ref(value: object) -> str:
    try:
        parsed = uuid.UUID(str(value))
    except (ValueError, AttributeError) as exc:
        raise ValueError("account_ref must be a UUID") from exc
    if parsed.int == 0:
        raise ValueError("account_ref must be a non-zero UUID")
    return str(parsed)


def _account_binding_row(row: tuple[object, ...]) -> dict[str, Any]:
    return {
        "binding_id": str(row[0]), "credential_profile_id": str(row[1]),
        "broker": str(row[2]), "environment": str(row[3]), "account_ref": str(row[4]),
        "binding_revision": int(row[5]),
        "verified_at": row[6].isoformat() if isinstance(row[6], datetime) else str(row[6]),
        "verification_method": str(row[7]),
    }


def _scope_document(broker: object, environment: object, account_ref: object) -> dict[str, str]:
    normalized_broker, normalized_environment = _account_registry_scope({
        "broker": broker, "environment": environment,
    })
    return {
        "broker": normalized_broker,
        "environment": normalized_environment,
        "account_ref": _canonical_account_ref(account_ref),
    }


def _account_scope_alias_document(value: dict[str, Any]) -> dict[str, Any]:
    scope = _scope_document(
        value.get("broker", ""), value.get("environment", ""),
        value.get("origin_account_ref", ""),
    )
    document = {
        "origin_account_ref": scope["account_ref"],
        "canonical_account_ref": _canonical_account_ref(value.get("canonical_account_ref", "")),
        "broker": scope["broker"],
        "environment": scope["environment"],
        "credential_profile_id": str(value.get("credential_profile_id", "")).strip(),
        "binding_revision": int(value.get("binding_revision", 0)),
        "verified_at": str(value.get("verified_at", "")).strip(),
        "verification_method": str(value.get("verification_method", "")),
    }
    if document["origin_account_ref"] == document["canonical_account_ref"]:
        raise ValueError("account scope alias must change the account_ref")
    if not document["credential_profile_id"] or document["binding_revision"] <= 0:
        raise ValueError("account scope alias requires a verified binding")
    if not document["verified_at"] or document["verification_method"] != "ka00001":
        raise ValueError("account scope alias verification is invalid")
    return document


def _account_scope_alias_values(value: dict[str, Any]) -> tuple[object, ...]:
    return (
        value["origin_account_ref"], value["canonical_account_ref"], value["broker"],
        value["environment"], value["credential_profile_id"], value["binding_revision"],
        value["verified_at"], value["verification_method"],
    )


def _account_scope_alias_row(origin_account_ref: str, row: tuple[object, ...]) -> dict[str, Any]:
    return {
        "origin_account_ref": origin_account_ref,
        "canonical_account_ref": str(row[0]),
        "broker": str(row[1]),
        "environment": str(row[2]),
        "credential_profile_id": str(row[3]),
        "binding_revision": int(row[4]),
        "verified_at": row[5].isoformat() if isinstance(row[5], datetime) else str(row[5]),
        "verification_method": str(row[6]),
    }


def _account_scope_alias_identity(value: dict[str, Any]) -> tuple[object, ...]:
    return (
        value["origin_account_ref"], value["canonical_account_ref"], value["broker"],
        value["environment"], value["credential_profile_id"], value["binding_revision"],
        value["verification_method"],
    )


def _verify_account_scope_alias_sqlite(connection: sqlite3.Connection, value: dict[str, Any]) -> None:
    if connection.execute(
        "SELECT 1 FROM central_account_registry WHERE account_ref=?",
        (value["origin_account_ref"],),
    ).fetchone() is not None:
        raise ValueError("a verified canonical account cannot become an alias origin")
    if connection.execute(
        "SELECT 1 FROM central_account_scope_aliases WHERE origin_account_ref=?",
        (value["canonical_account_ref"],),
    ).fetchone() is not None:
        raise ValueError("account scope alias chains are not allowed")
    binding = connection.execute(
        "SELECT 1 FROM central_account_binding_revisions b JOIN central_account_registry r "
        "ON r.account_ref=b.account_ref WHERE b.credential_profile_id=? AND b.broker=? "
        "AND b.environment=? AND b.account_ref=? AND b.binding_revision=? "
        "AND b.verification_method=? AND r.status='active'",
        (
            value["credential_profile_id"], value["broker"], value["environment"],
            value["canonical_account_ref"], value["binding_revision"],
            value["verification_method"],
        ),
    ).fetchone()
    if binding is None:
        raise ValueError("account scope alias requires the recorded verified binding")


def _verify_account_scope_alias_postgres(cursor: Any, value: dict[str, Any]) -> None:
    cursor.execute(
        "SELECT 1 FROM central_account_registry WHERE account_ref=%s",
        (value["origin_account_ref"],),
    )
    if cursor.fetchone() is not None:
        raise ValueError("a verified canonical account cannot become an alias origin")
    cursor.execute(
        "SELECT 1 FROM central_account_scope_aliases WHERE origin_account_ref=%s",
        (value["canonical_account_ref"],),
    )
    if cursor.fetchone() is not None:
        raise ValueError("account scope alias chains are not allowed")
    cursor.execute(
        "SELECT 1 FROM central_account_binding_revisions b JOIN central_account_registry r "
        "ON r.account_ref=b.account_ref WHERE b.credential_profile_id=%s AND b.broker=%s "
        "AND b.environment=%s AND b.account_ref=%s AND b.binding_revision=%s "
        "AND b.verification_method=%s AND r.status='active'",
        (
            value["credential_profile_id"], value["broker"], value["environment"],
            value["canonical_account_ref"], value["binding_revision"],
            value["verification_method"],
        ),
    )
    if cursor.fetchone() is None:
        raise ValueError("account scope alias requires the recorded verified binding")


def _stable_id(prefix: str, value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()}"


class SQLiteAccountIdentityStoreMixin:
    def register_account_identity(self, value: dict[str, Any]) -> str:
        broker, environment = _account_registry_scope(value)
        fingerprint = str(value.get("identity_fingerprint", ""))
        if len(fingerprint) != 64:
            raise ValueError("account identity fingerprint is invalid")
        candidate = _canonical_account_ref(value.get("account_ref") or uuid.uuid4())
        created_at = str(value["created_at"])
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO central_account_registry("
                "account_ref,broker,environment,identity_fingerprint,created_at,status) "
                "VALUES(?,?,?,?,?,'active')",
                (candidate, broker, environment, fingerprint, created_at),
            )
            row = connection.execute(
                "SELECT account_ref FROM central_account_registry WHERE broker=? "
                "AND environment=? AND identity_fingerprint=?",
                (broker, environment, fingerprint),
            ).fetchone()
        if row is None:
            raise RuntimeError("verified account identity was not registered")
        return str(row[0])

    def append_account_binding(self, value: dict[str, Any]) -> dict[str, Any]:
        document = _account_binding_document(value)
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            account = connection.execute(
                "SELECT 1 FROM central_account_registry WHERE account_ref=? AND broker=? "
                "AND environment=? AND status='active'",
                (document["account_ref"], document["broker"], document["environment"]),
            ).fetchone()
            if account is None:
                raise ValueError("account binding requires a verified active account")
            row = connection.execute(
                "SELECT binding_revision,account_ref FROM central_account_binding_revisions "
                "WHERE credential_profile_id=? AND broker=? AND environment=? "
                "ORDER BY binding_revision DESC LIMIT 1",
                (
                    document["credential_profile_id"], document["broker"],
                    document["environment"],
                ),
            ).fetchone()
            revision = int(row[0]) + 1 if row else 1
            document["binding_revision"] = revision
            document["binding_id"] = _stable_id("account_binding", document)
            connection.execute(
                "INSERT INTO central_account_binding_revisions("
                "binding_id,credential_profile_id,broker,environment,account_ref,binding_revision,"
                "verified_at,verification_method) VALUES(?,?,?,?,?,?,?,?)",
                _account_binding_values(document),
            )
        return document

    def load_account_bindings(self) -> list[dict[str, Any]]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                "SELECT binding_id,credential_profile_id,broker,environment,account_ref,"
                "binding_revision,verified_at,verification_method "
                "FROM central_account_binding_revisions ORDER BY credential_profile_id,"
                "broker,environment,binding_revision"
            ).fetchall()
        return [_account_binding_row(row) for row in rows]

    def register_account_scope_alias(self, value: dict[str, Any]) -> dict[str, Any]:
        document = _account_scope_alias_document(value)
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT canonical_account_ref,broker,environment,credential_profile_id,"
                "binding_revision,verified_at,verification_method FROM central_account_scope_aliases "
                "WHERE origin_account_ref=?", (document["origin_account_ref"],),
            ).fetchone()
            if existing is not None:
                stored = _account_scope_alias_row(document["origin_account_ref"], existing)
                if _account_scope_alias_identity(stored) != _account_scope_alias_identity(document):
                    raise ValueError("account scope alias is immutable")
                return stored
            _verify_account_scope_alias_sqlite(connection, document)
            connection.execute(
                "INSERT INTO central_account_scope_aliases("
                "origin_account_ref,canonical_account_ref,broker,environment,credential_profile_id,"
                "binding_revision,verified_at,verification_method) VALUES(?,?,?,?,?,?,?,?)",
                _account_scope_alias_values(document),
            )
        return document

    def resolve_account_scope(self, broker: str, environment: str, account_ref: str) -> dict[str, Any]:
        scope = _scope_document(broker, environment, account_ref)
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT a.canonical_account_ref FROM central_account_scope_aliases a "
                "JOIN central_account_registry r ON r.account_ref=a.canonical_account_ref "
                "AND r.broker=a.broker AND r.environment=a.environment AND r.status='active' "
                "WHERE a.origin_account_ref=? AND a.broker=? AND a.environment=?",
                (scope["account_ref"], scope["broker"], scope["environment"]),
            ).fetchone()
            if row is None:
                verified = connection.execute(
                    "SELECT 1 FROM central_account_registry WHERE account_ref=? AND broker=? "
                    "AND environment=? AND status='active'",
                    (scope["account_ref"], scope["broker"], scope["environment"]),
                ).fetchone() is not None
                canonical = scope["account_ref"]
            else:
                verified, canonical = True, str(row[0])
        return {**scope, "origin_account_ref": scope["account_ref"],
                "canonical_account_ref": canonical, "verified": verified}


class PostgresAccountIdentityStoreMixin:
    def register_account_identity(self, value: dict[str, Any]) -> str:
        broker, environment = _account_registry_scope(value)
        fingerprint = str(value.get("identity_fingerprint", ""))
        if len(fingerprint) != 64:
            raise ValueError("account identity fingerprint is invalid")
        candidate = _canonical_account_ref(value.get("account_ref") or uuid.uuid4())
        from .postgres_access import DBWriterContext, open_observed_connection

        writer = DBWriterContext(
            writer_family="account.identity", writer_kind="account_identity_register",
            operation="register_account_identity", rows_attempted=1,
        )
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO central_account_registry("
                "account_ref,broker,environment,identity_fingerprint,created_at,status) "
                "VALUES(%s,%s,%s,%s,%s,'active') ON CONFLICT(broker,environment,identity_fingerprint) "
                "DO NOTHING",
                (candidate, broker, environment, fingerprint, str(value["created_at"])),
            )
            cursor.execute(
                "SELECT account_ref FROM central_account_registry WHERE broker=%s "
                "AND environment=%s AND identity_fingerprint=%s",
                (broker, environment, fingerprint),
            )
            row = cursor.fetchone()
        if row is None:
            raise RuntimeError("verified account identity was not registered")
        return str(row[0])

    def append_account_binding(self, value: dict[str, Any]) -> dict[str, Any]:
        document = _account_binding_document(value)
        from .postgres_access import DBWriterContext, open_observed_connection

        writer = DBWriterContext(
            writer_family="account.binding", writer_kind="account_binding_append",
            operation="append_account_binding", rows_attempted=1,
        )
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                (
                    f"{document['credential_profile_id']}:{document['broker']}:"
                    f"{document['environment']}",
                ),
            )
            cursor.execute(
                "SELECT 1 FROM central_account_registry WHERE account_ref=%s AND broker=%s "
                "AND environment=%s AND status='active'",
                (document["account_ref"], document["broker"], document["environment"]),
            )
            if cursor.fetchone() is None:
                raise ValueError("account binding requires a verified active account")
            cursor.execute(
                "SELECT binding_revision FROM central_account_binding_revisions "
                "WHERE credential_profile_id=%s AND broker=%s AND environment=%s "
                "ORDER BY binding_revision DESC LIMIT 1 FOR UPDATE",
                (
                    document["credential_profile_id"], document["broker"],
                    document["environment"],
                ),
            )
            row = cursor.fetchone()
            document["binding_revision"] = int(row[0]) + 1 if row else 1
            document["binding_id"] = _stable_id("account_binding", document)
            cursor.execute(
                "INSERT INTO central_account_binding_revisions("
                "binding_id,credential_profile_id,broker,environment,account_ref,binding_revision,"
                "verified_at,verification_method) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                _account_binding_values(document),
            )
        return document

    def load_account_bindings(self) -> list[dict[str, Any]]:
        from .postgres_access import DBWriterContext, open_observed_connection

        reader = DBWriterContext(
            writer_family="read.account", writer_kind="account_bindings",
            operation="load_account_bindings", access_mode="read",
        )
        with open_observed_connection(self._connect, reader) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT binding_id,credential_profile_id,broker,environment,account_ref,"
                "binding_revision,verified_at,verification_method "
                "FROM central_account_binding_revisions ORDER BY credential_profile_id,"
                "broker,environment,binding_revision"
            )
            rows = cursor.fetchall()
        return [_account_binding_row(row) for row in rows]

    def register_account_scope_alias(self, value: dict[str, Any]) -> dict[str, Any]:
        document = _account_scope_alias_document(value)
        lock_key = f"account-alias:{document['origin_account_ref']}"
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (lock_key,))
            cursor.execute(
                "SELECT canonical_account_ref,broker,environment,credential_profile_id,"
                "binding_revision,verified_at,verification_method FROM central_account_scope_aliases "
                "WHERE origin_account_ref=%s", (document["origin_account_ref"],),
            )
            existing = cursor.fetchone()
            if existing is not None:
                stored = _account_scope_alias_row(document["origin_account_ref"], existing)
                if _account_scope_alias_identity(stored) != _account_scope_alias_identity(document):
                    raise ValueError("account scope alias is immutable")
                return stored
            _verify_account_scope_alias_postgres(cursor, document)
            cursor.execute(
                "INSERT INTO central_account_scope_aliases("
                "origin_account_ref,canonical_account_ref,broker,environment,credential_profile_id,"
                "binding_revision,verified_at,verification_method) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                _account_scope_alias_values(document),
            )
        return document

    def resolve_account_scope(self, broker: str, environment: str, account_ref: str) -> dict[str, Any]:
        scope = _scope_document(broker, environment, account_ref)
        from .postgres_access import DBWriterContext, open_observed_connection

        reader = DBWriterContext(
            writer_family="read.account", writer_kind="account_scope",
            operation="resolve_account_scope", access_mode="read",
        )
        with open_observed_connection(self._connect, reader) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT a.canonical_account_ref FROM central_account_scope_aliases a "
                "JOIN central_account_registry r ON r.account_ref=a.canonical_account_ref "
                "AND r.broker=a.broker AND r.environment=a.environment AND r.status='active' "
                "WHERE a.origin_account_ref=%s AND a.broker=%s AND a.environment=%s",
                (scope["account_ref"], scope["broker"], scope["environment"]),
            )
            row = cursor.fetchone()
            if row is None:
                cursor.execute(
                    "SELECT 1 FROM central_account_registry WHERE account_ref=%s AND broker=%s "
                    "AND environment=%s AND status='active'",
                    (scope["account_ref"], scope["broker"], scope["environment"]),
                )
                verified = cursor.fetchone() is not None
                canonical = scope["account_ref"]
            else:
                verified, canonical = True, str(row[0])
        return {**scope, "origin_account_ref": scope["account_ref"],
                "canonical_account_ref": canonical, "verified": verified}
