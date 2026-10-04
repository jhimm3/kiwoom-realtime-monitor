"""Credential profile metadata and atomic activation receipts for both DB backends.

The caller owns each connection and transaction. Activation borrows the same
cursor for account binding and settings; encrypted credential files belong to
the separate vault service.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from time import time
from typing import Any

from kiwoom_monitor.central_server.database_account_identity import (
    _account_binding_document, _account_binding_values, _canonical_account_ref, _stable_id,
)
from kiwoom_monitor.central_server.database_account_settings import (
    _load_account_settings, _load_market_profile_settings, _save_account_settings,
)

_CREDENTIAL_ACTIVATION_COLUMNS = (
    "operation_id", "provider", "credential_revision", "request_id", "request_digest",
    "profile_id", "account_ref", "run_id", "binding_revision", "committed_at",
)


def _credential_activation_row(row: tuple[object, ...]) -> dict[str, Any]:
    document = dict(zip(_CREDENTIAL_ACTIVATION_COLUMNS, row, strict=True))
    committed_at = document["committed_at"]
    document["committed_at"] = (
        committed_at.isoformat() if isinstance(committed_at, datetime) else str(committed_at)
    )
    return document


def _register_credential_profile(cursor: Any, provider: str, profile_id: str, created_at: str, p: str) -> None:
    from kiwoom_monitor.domain.credential_contract import PROVIDER_FIELDS
    import re
    if provider not in PROVIDER_FIELDS or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", profile_id):
        raise ValueError("invalid credential profile")
    datetime.fromisoformat(created_at)
    environment = provider.removeprefix("kiwoom_") if provider.startswith("kiwoom_") else None
    cursor.execute(f"SELECT provider,environment FROM central_credential_profiles WHERE profile_id={p}", (profile_id,))
    existing = cursor.fetchone()
    if existing:
        if tuple(existing) != (provider, environment):
            raise ValueError("credential profile provider is immutable")
        return
    cursor.execute("INSERT INTO central_credential_profiles(profile_id,provider,environment,label,lifecycle_state,created_at) "
                   f"VALUES({','.join([p] * 6)}) ON CONFLICT(profile_id) DO NOTHING",
                   (profile_id, provider, environment, "", "active", created_at))


def _load_credential_activations(cursor: Any, profile_id: str, placeholder: str) -> list[dict[str, Any]]:
    cursor.execute(f"SELECT {','.join(_CREDENTIAL_ACTIVATION_COLUMNS)} FROM "
                   f"central_credential_activations WHERE profile_id={placeholder} "
                   "ORDER BY credential_revision", (profile_id,))
    return [_credential_activation_row(row) for row in cursor.fetchall()]


def _list_credential_profiles(cursor: Any) -> list[dict[str, Any]]:
    columns = ("profile_id", "provider", "environment", "label", "lifecycle_state")
    cursor.execute(f"SELECT {','.join(columns)} FROM central_credential_profiles ORDER BY profile_id")
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _archive_credential_profile(cursor: Any, provider: str, profile_id: str, p: str) -> dict[str, Any]:
    cursor.execute(
        f"SELECT provider,lifecycle_state FROM central_credential_profiles WHERE profile_id={p}",
        (profile_id,),
    )
    row = cursor.fetchone()
    if row is None or row[0] != provider:
        raise ValueError("PROFILE_NOT_FOUND")
    if row[1] != "archived":
        cursor.execute(
            "UPDATE central_credential_profiles SET lifecycle_state='archived',archived_at=" + p
            + f" WHERE profile_id={p} AND provider={p}",
            (datetime.now(timezone.utc).isoformat(), profile_id, provider),
        )
    return {"provider": provider, "profile_id": profile_id, "lifecycle_state": "archived"}


def _rename_credential_profile(
    cursor: Any, provider: str, profile_id: str, label: str, p: str,
) -> dict[str, Any]:
    label = label.strip() if isinstance(label, str) else ""
    if not label or len(label) > 120:
        raise ValueError("PROFILE_LABEL_INVALID")
    cursor.execute(
        f"SELECT provider,lifecycle_state,label FROM central_credential_profiles WHERE profile_id={p}",
        (profile_id,),
    )
    row = cursor.fetchone()
    if row is None or row[0] != provider or row[1] == "archived":
        raise ValueError("PROFILE_NOT_FOUND")
    if row[2] != label:
        cursor.execute(
            f"UPDATE central_credential_profiles SET label={p} WHERE profile_id={p} AND provider={p}",
            (label, profile_id, provider),
        )
    return {"provider": provider, "profile_id": profile_id, "label": label}


def _find_credential_activation(cursor: Any, operation_id: str, provider: str, profile_id: str,
                                request_id: str, p: str) -> dict[str, Any] | None:
    where = f"operation_id={p}" if operation_id else f"provider={p} AND profile_id={p} AND request_id={p}"
    parameters = (operation_id,) if operation_id else (provider, profile_id, request_id)
    cursor.execute(f"SELECT {','.join(_CREDENTIAL_ACTIVATION_COLUMNS)} FROM central_credential_activations WHERE {where}", parameters)
    row = cursor.fetchone()
    return _credential_activation_row(row) if row else None


def _create_credential_profile(cursor: Any, provider: str, request_id: str, label: str,
                               digest: str, p: str) -> dict[str, Any]:
    from kiwoom_monitor.domain.credential_contract import PROVIDER_FIELDS
    if provider not in PROVIDER_FIELDS or len(label) > 120:
        raise ValueError("invalid credential profile")
    uuid.UUID(request_id)
    cursor.execute("SELECT document_json FROM central_documents WHERE collection='credential_profile_requests' "
                   f"AND owner={p} AND document_key={p}", (provider, request_id))
    row = cursor.fetchone()
    if row:
        document = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        if document["request_digest"] != digest:
            raise ValueError("PROFILE_REQUEST_CONFLICT")
        return {key: document[key] for key in ("provider", "profile_id", "label")}
    if len(_list_credential_profiles(cursor)) >= 64:
        raise ValueError("PROFILE_LIMIT")
    profile_id = str(uuid.uuid4())
    environment = provider.removeprefix("kiwoom_") if provider.startswith("kiwoom_") else None
    cursor.execute("INSERT INTO central_credential_profiles(profile_id,provider,environment,label,lifecycle_state,created_at) "
                   f"VALUES({','.join([p] * 6)})",
                   (profile_id, provider, environment, label, "draft", datetime.now(timezone.utc).isoformat()))
    document = {"provider": provider, "profile_id": profile_id, "label": label, "request_digest": digest}
    cursor.execute("INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
                   f"VALUES({','.join([p] * 5)})",
                   ("credential_profile_requests", provider, request_id, time(), json.dumps(document)))
    return {key: document[key] for key in ("provider", "profile_id", "label")}


def _claim_account_settings(cursor: Any, activation: dict[str, Any], p: str,
                            *, replay: bool = False, disabled: bool = False) -> None:
    if activation["provider"] not in {"kiwoom_real", "kiwoom_mock"}:
        return
    scope = {"broker": "kiwoom", "environment": activation["provider"].removeprefix("kiwoom_"),
             "account_ref": activation["account_ref"]}
    current = _load_account_settings(cursor, scope, p)
    if replay and current["revision"]:
        return  # Completed replay must not restore a setting changed afterwards.
    if disabled:
        if current["active_profile_id"] not in {None, activation["profile_id"]}:
            return  # Disabling an old profile cannot disable a subsequently admitted account profile.
        _save_account_settings(cursor, {
            "scope": scope, "active_profile_id": None,
            "monitor_enabled": False, "mock_order_enabled": False,
        }, current["revision"], p)
        return
    _save_account_settings(cursor, {
        "scope": scope, "active_profile_id": activation["profile_id"],
        "monitor_enabled": current["monitor_enabled"] if current["revision"] else True,
        "mock_order_enabled": current["mock_order_enabled"] if current["revision"] else False,
    }, current["revision"], p)


def _finalize_credential_activation(cursor: Any, value: dict[str, Any], p: str) -> dict[str, Any]:
    """Binding and activation in one transaction, reusable by both database dialects."""
    from kiwoom_monitor.domain.credential_contract import PROVIDER_FIELDS
    import re
    allowed = set(_CREDENTIAL_ACTIVATION_COLUMNS) | {"environment", "label", "verification_method", "disabled"}
    if set(value) - allowed or value.get("provider") not in PROVIDER_FIELDS:
        raise ValueError("invalid credential activation fields")
    document = {key: value.get(key) for key in _CREDENTIAL_ACTIVATION_COLUMNS}
    for key in ("operation_id", "profile_id", "request_id"):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", str(document[key] or "")):
            raise ValueError("invalid activation identity")
    if not re.fullmatch(r"[0-9a-f]{64}", str(document["request_digest"] or "")):
        raise ValueError("invalid keyed request digest")
    if type(document["credential_revision"]) is not int or document["credential_revision"] < 1:
        raise ValueError("invalid credential revision")
    document["committed_at"] = datetime.fromisoformat(str(document["committed_at"])).isoformat()
    provider, profile_id = document["provider"], document["profile_id"]
    disabled = value.get("disabled", False)
    if type(disabled) is not bool:
        raise ValueError("invalid credential disabled flag")
    environment = value.get("environment")
    account_provider = provider in {"kiwoom_real", "kiwoom_mock"}
    if environment != (provider.removeprefix("kiwoom_") if account_provider else None):
        raise ValueError("credential environment mismatch")
    if not account_provider and any(document[key] is not None for key in ("account_ref", "run_id", "binding_revision")):
        raise ValueError("nonaccount activation cannot bind account")
    for stored in _load_credential_activations(cursor, profile_id, p):
        if stored["operation_id"] == document["operation_id"] or stored["request_id"] == document["request_id"]:
            if any(stored[key] != document[key] for key in (
                "operation_id", "provider", "request_id", "request_digest", "credential_revision", "account_ref", "run_id",
            )):
                raise ValueError("credential activation idempotency conflict")
            _claim_account_settings(cursor, stored, p, replay=True, disabled=disabled)
            return stored
        if int(stored["credential_revision"]) >= document["credential_revision"]:
            raise ValueError("credential activation revision conflict")
    if (disabled and provider == "kiwoom_real"
            and _load_market_profile_settings(cursor, p)["market_profile_id"] == profile_id):
        raise ValueError("MARKET_PROFILE_REQUIRED")
    cursor.execute(f"SELECT provider,environment,lifecycle_state FROM central_credential_profiles WHERE profile_id={p}", (profile_id,))
    profile = cursor.fetchone()
    if profile is not None and (tuple(profile[:2]) != (provider, environment) or profile[2] not in {"active", "draft"}):
        raise ValueError("credential profile is immutable or archived")
    if account_provider:
        document["account_ref"] = _canonical_account_ref(document["account_ref"])
        cursor.execute(f"SELECT 1 FROM central_account_registry WHERE account_ref={p} AND broker='kiwoom' "
                       f"AND environment={p} AND status='active'", (document["account_ref"], environment))
        if cursor.fetchone() is None:
            raise ValueError("account activation requires verified identity")
        cursor.execute(f"SELECT binding_revision,account_ref FROM central_account_binding_revisions "
                       f"WHERE credential_profile_id={p} AND broker='kiwoom' AND environment={p} "
                       "ORDER BY binding_revision DESC LIMIT 1", (profile_id, environment))
        latest = cursor.fetchone()
        if latest and latest[1] != document["account_ref"]:
            raise ValueError("ACCOUNT_CHANGED")
        if disabled and latest is None:
            raise ValueError("ACCOUNT_IDENTITY_UNVERIFIED")
        binding = _account_binding_document({
            "credential_profile_id": profile_id, "broker": "kiwoom", "environment": environment,
            "account_ref": document["account_ref"], "verified_at": document["committed_at"],
            "verification_method": value.get("verification_method", "ka00001"),
        })
        binding["binding_revision"] = int(latest[0]) + 1 if latest else 1
        binding["binding_id"] = _stable_id("account_binding", binding)
        if not disabled:
            cursor.execute("INSERT INTO central_account_binding_revisions(binding_id,credential_profile_id,broker,"
                           "environment,account_ref,binding_revision,verified_at,verification_method) "
                           f"VALUES({','.join([p] * 8)})", _account_binding_values(binding))
        document["binding_revision"] = int(latest[0]) if disabled else binding["binding_revision"]
    if profile is None:
        cursor.execute("INSERT INTO central_credential_profiles(profile_id,provider,environment,label,lifecycle_state,created_at) "
                       f"VALUES({','.join([p] * 6)})", (profile_id, provider, environment, str(value.get("label", ""))[:120],
                                                        "active", document["committed_at"]))
    else:
        cursor.execute(f"UPDATE central_credential_profiles SET lifecycle_state='active' WHERE profile_id={p}", (profile_id,))
    cursor.execute(f"INSERT INTO central_credential_activations({','.join(_CREDENTIAL_ACTIVATION_COLUMNS)}) "
                   f"VALUES({','.join([p] * len(_CREDENTIAL_ACTIVATION_COLUMNS))})",
                   tuple(document[key] for key in _CREDENTIAL_ACTIVATION_COLUMNS))
    _claim_account_settings(cursor, document, p, disabled=disabled)
    return document


class SQLiteCredentialStoreMixin:
    def find_credential_activation(self, *, operation_id: str = "", provider: str = "", profile_id: str = "", request_id: str = "") -> dict[str, Any] | None:
        with self._lock, self._connection() as connection:
            return _find_credential_activation(connection.cursor(), operation_id, provider, profile_id, request_id, "?")

    def list_credential_profiles(self) -> list[dict[str, Any]]:
        with self._lock, self._connection() as connection:
            return _list_credential_profiles(connection.cursor())

    def create_credential_profile(self, provider: str, request_id: str, label: str, digest: str) -> dict[str, Any]:
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return _create_credential_profile(connection.cursor(), provider, request_id, label, digest, "?")

    def archive_credential_profile(self, provider: str, profile_id: str) -> dict[str, Any]:
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return _archive_credential_profile(connection.cursor(), provider, profile_id, "?")

    def rename_credential_profile(self, provider: str, profile_id: str, label: str) -> dict[str, Any]:
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return _rename_credential_profile(connection.cursor(), provider, profile_id, label, "?")

    def register_credential_profile(self, provider: str, profile_id: str, created_at: str) -> None:
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            _register_credential_profile(connection.cursor(), provider, profile_id, created_at, "?")

    def finalize_credential_activation(self, value: dict[str, Any]) -> dict[str, Any]:
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return _finalize_credential_activation(connection.cursor(), value, "?")

    def load_credential_activations(self, profile_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connection() as connection:
            return _load_credential_activations(connection.cursor(), profile_id, "?")

class PostgresCredentialStoreMixin:
    def find_credential_activation(self, *, operation_id: str = "", provider: str = "", profile_id: str = "", request_id: str = "") -> dict[str, Any] | None:
        from .postgres_access import DBWriterContext, open_observed_connection

        reader = DBWriterContext(
            writer_family="read.credential", writer_kind="activation_lookup",
            operation="find_credential_activation", access_mode="read",
        )
        with open_observed_connection(self._connect, reader) as connection, connection.cursor() as cursor:
            return _find_credential_activation(cursor, operation_id, provider, profile_id, request_id, "%s")

    def list_credential_profiles(self) -> list[dict[str, Any]]:
        from .postgres_access import DBWriterContext, open_observed_connection

        reader = DBWriterContext(
            writer_family="read.credential", writer_kind="profile_list",
            operation="list_credential_profiles", access_mode="read",
        )
        with open_observed_connection(self._connect, reader) as connection, connection.cursor() as cursor:
            return _list_credential_profiles(cursor)

    def create_credential_profile(self, provider: str, request_id: str, label: str, digest: str) -> dict[str, Any]:
        from .postgres_access import DBWriterContext, open_observed_connection

        writer = DBWriterContext(
            writer_family="credential.profile", writer_kind="credential_profile_create",
            operation="create_credential_profile", rows_attempted=1,
        )
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("credential-activation",))
            return _create_credential_profile(cursor, provider, request_id, label, digest, "%s")

    def archive_credential_profile(self, provider: str, profile_id: str) -> dict[str, Any]:
        from .postgres_access import DBWriterContext, open_observed_connection

        writer = DBWriterContext(
            writer_family="credential.profile", writer_kind="credential_profile_archive",
            operation="archive_credential_profile", rows_attempted=1,
        )
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("credential-activation",))
            return _archive_credential_profile(cursor, provider, profile_id, "%s")

    def rename_credential_profile(self, provider: str, profile_id: str, label: str) -> dict[str, Any]:
        from .postgres_access import DBWriterContext, open_observed_connection

        writer = DBWriterContext(
            writer_family="credential.profile", writer_kind="credential_profile_rename",
            operation="rename_credential_profile", rows_attempted=1,
        )
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("credential-activation",))
            return _rename_credential_profile(cursor, provider, profile_id, label, "%s")

    def register_credential_profile(self, provider: str, profile_id: str, created_at: str) -> None:
        from .postgres_access import DBWriterContext, open_observed_connection

        writer = DBWriterContext(
            writer_family="credential.profile", writer_kind="credential_profile_register",
            operation="register_credential_profile", rows_attempted=1,
        )
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("credential-activation",))
            _register_credential_profile(cursor, provider, profile_id, created_at, "%s")

    def finalize_credential_activation(self, value: dict[str, Any]) -> dict[str, Any]:
        from .postgres_access import DBWriterContext, open_observed_connection

        writer = DBWriterContext(
            writer_family="credential.activation", writer_kind="credential_activation_finalize",
            operation="finalize_credential_activation", rows_attempted=1,
        )
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("credential-activation",))
            return _finalize_credential_activation(cursor, value, "%s")

    def load_credential_activations(self, profile_id: str) -> list[dict[str, Any]]:
        from .postgres_access import DBWriterContext, open_observed_connection

        reader = DBWriterContext(
            writer_family="read.credential", writer_kind="profile_activations",
            operation="load_credential_activations", access_mode="read",
        )
        with open_observed_connection(self._connect, reader) as connection, connection.cursor() as cursor:
            return _load_credential_activations(cursor, profile_id, "%s")
