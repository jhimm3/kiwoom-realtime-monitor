from __future__ import annotations

import json
import hashlib
import logging
import sqlite3
import uuid
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from enum import Enum
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event, RLock, Thread
from time import monotonic, time
from typing import Any, Protocol
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo

from kiwoom_monitor.domain.market_data_contract import (
    DataCompleteness,
    DataValueKind,
    MarketDataMetadata,
    MarketDataObservation,
    MarketDatasetKind,
    ObservationOrigin,
)
from kiwoom_monitor.domain.research_contract import (
    OBSERVATION_REVISION_SCHEMA_VERSION,
    RESEARCH_OBSERVATION_KINDS,
    ObservationRevisionSource,
    ThemeSnapshotSource,
)
from kiwoom_monitor.domain.news_observation import (
    ARTICLE_BODY_EXTRACTOR_VERSION,
    NEWS_ANALYSIS_SCHEMA_VERSION,
    news_job_key,
    stable_document_hash,
)
from kiwoom_monitor.application.market_data_coverage import CoverageObservation
from kiwoom_monitor.application.news_rules import SUPPLY_CONTRACT_RULE_VERSION
from kiwoom_monitor.infrastructure.market_data_metadata_codec import (
    market_metadata_from_storage_row,
    market_metadata_storage_values,
)

from kiwoom_monitor.central_server.database_codec import (
    BAR_KEY_COLUMNS,
    bar_columns,
    bar_result_rows,
    bar_value_rows,
    bounded_limit,
    dataset_snapshot_result_rows,
    document_result_rows,
    document_select_query,
    document_value_rows,
    json_mapping,
    observation_revision_result_rows,
    second_trade_bar_value_rows,
)
from kiwoom_monitor.central_server.central_schema import (
    central_schema_migrations,
)
from kiwoom_monitor.central_server.schema_migrations import CentralSchemaMigrationRunner
from kiwoom_monitor.central_server.market_observations import (
    bar_observation_key,
    minute_bar_observation,
    minute_bar_revision_payload,
)


@dataclass(frozen=True)
class StoredQuery:
    payload: dict[str, Any]
    has_next: bool
    next_key: str


DatasetSnapshotWrite = tuple[
    str, str, str, dict[str, Any], MarketDataObservation[object] | None,
]

# These live market snapshots are refreshed continuously and can be reconstructed
# from the next poll/realtime frame.  Keeping their commit synchronous makes the
# UI wait behind slow NAS WAL fsyncs; durable documents, account data, orders and
# research exports continue to use PostgreSQL's default synchronous commit.
ASYNC_COMMIT_DATASET_KINDS = frozenset({
    "market_state", "new_high", "program_flow", "ranking", "top20_membership",
})


def _uses_async_dataset_commit(values: list[DatasetSnapshotWrite]) -> bool:
    return bool(values) and all(value[0] in ASYNC_COMMIT_DATASET_KINDS for value in values)


def _sample_postgres_backend_waits(
    database_url: str, backend_pid: int, stop: Event,
    samples: list[tuple[str, str, tuple[int, ...]]],
) -> None:
    """Capture the real PostgreSQL wait event while a TOP20 commit is blocked."""
    if stop.wait(1.0):
        return
    try:
        import psycopg

        with psycopg.connect(
            database_url, autocommit=True,
            application_name="kiwoom-top20-latency-probe",
        ) as connection, connection.cursor() as cursor:
            while not stop.is_set():
                cursor.execute(
                    "SELECT COALESCE(wait_event_type,''),COALESCE(wait_event,''),"
                    "pg_blocking_pids(pid) FROM pg_stat_activity WHERE pid=%s",
                    (backend_pid,),
                )
                row = cursor.fetchone()
                if row is None:
                    return
                samples.append((str(row[0]), str(row[1]), tuple(int(value) for value in row[2])))
                if stop.wait(0.1):
                    return
    except Exception as error:
        samples.append(("DIAGNOSTIC_ERROR", type(error).__name__, ()))


def _postgres_wait_summary(samples: list[tuple[str, str, tuple[int, ...]]]) -> str:
    counts = Counter(samples)
    return ";".join(
        f"{wait_type or 'NONE'}:{wait_event or 'NONE'}"
        f" blockers={','.join(map(str, blockers)) or '-'} samples={count}"
        for (wait_type, wait_event, blockers), count in counts.most_common()
    ) or "no-sample"


def _top20_statistics_document(
    top20_rows: list[tuple[object, object, object]],
    market_rows: list[tuple[object, object]],
) -> dict[str, object]:
    """Aggregate stored NAS TOP20 minutes without sending raw yearly rows to the app."""
    hourly_totals: dict[str, float] = defaultdict(float)
    hourly_counts: dict[str, int] = defaultdict(int)
    hourly_days: dict[str, set[str]] = defaultdict(set)
    daily_values: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    for subject, snapshot_key, raw_payload in top20_rows:
        payload = raw_payload if isinstance(raw_payload, dict) else json.loads(str(raw_payload))
        if not isinstance(payload, dict) or payload.get("capture_state") != "realtime_complete":
            continue
        minute = str(payload.get("minute") or snapshot_key)
        clock = minute[11:16]
        values = payload.get("market_values")
        if not isinstance(values, (list, tuple)) or len(values) < 3:
            continue
        amounts = [float(values[index] or 0.0) for index in range(3)]
        day = str(subject)
        if "08:00" <= clock < "20:00":
            hour = f"{clock[:2]}:00"
            hourly_totals[hour] += sum(amounts)
            hourly_counts[hour] += 1
            hourly_days[hour].add(day)
        # The 15:30 closing-auction execution belongs to the KRX regular day.
        if "09:00" <= clock < "15:31":
            for index, amount in enumerate(amounts):
                daily_values[day][index] += amount

    market_values: dict[str, dict[str, float]] = defaultdict(dict)
    for subject, raw_payload in market_rows:
        subject_text = str(subject)
        raw_day, separator, market = subject_text.partition(":")
        if not separator or market not in {"kospi", "kosdaq"}:
            continue
        payload = raw_payload if isinstance(raw_payload, dict) else json.loads(str(raw_payload))
        daily = payload.get("daily") if isinstance(payload, dict) else None
        if not isinstance(daily, list):
            continue
        record = next(
            (value for value in daily if isinstance(value, dict) and str(value.get("dt", "")) == raw_day),
            None,
        )
        if record is None:
            continue
        try:
            market_values[f"{raw_day[:4]}-{raw_day[4:6]}-{raw_day[6:8]}"][market] = (
                abs(float(str(record.get("trde_prica", 0)).replace(",", ""))) / 100
            )
        except (TypeError, ValueError):
            continue

    hourly = [
        {
            "hour": hour,
            "average_eok": hourly_totals[hour] / hourly_counts[hour],
            "day_count": len(hourly_days[hour]),
        }
        for hour in sorted(hourly_totals, key=lambda value: hourly_totals[value] / hourly_counts[value], reverse=True)
    ]
    comparisons = []
    for day in sorted(daily_values):
        top20 = daily_values[day]
        market = market_values.get(day, {})
        comparisons.append({
            "trade_date": day,
            "top20_eok": sum(top20),
            "top20_market_values": top20,
            "kospi_eok": float(market.get("kospi", 0.0)),
            "kosdaq_eok": float(market.get("kosdaq", 0.0)),
        })
    return {"hourly": hourly, "comparisons": comparisons}


_CREDENTIAL_ACTIVATION_COLUMNS = (
    "operation_id", "provider", "credential_revision", "request_id", "request_digest",
    "profile_id", "account_ref", "run_id", "binding_revision", "committed_at",
)


logger = logging.getLogger(__name__)


def _credential_activation_row(row: tuple[object, ...]) -> dict[str, Any]:
    document = dict(zip(_CREDENTIAL_ACTIVATION_COLUMNS, row, strict=True))
    committed_at = document["committed_at"]
    document["committed_at"] = (
        committed_at.isoformat() if isinstance(committed_at, datetime) else str(committed_at)
    )
    return document


def _register_credential_profile(cursor: Any, provider: str, profile_id: str, created_at: str, p: str) -> None:
    from kiwoom_monitor.central_server.credential_store import PROVIDER_FIELDS
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
    from kiwoom_monitor.central_server.credential_store import PROVIDER_FIELDS
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


def _save_real_account_recovery(cursor, binding, recovery, received_at, settings_revision, p):
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
                   ("real_account_recovery", _account_settings_owner(scope), key, time(), serialized))
    return json.loads(serialized)


def _save_real_account_event(cursor, binding, event_type, event, received_at, settings_revision, p):
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
                   ("real_account_event", _account_settings_owner(scope), key, time(), serialized))
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
    from kiwoom_monitor.central_server.credential_store import PROVIDER_FIELDS
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


class QueryStore(Protocol):
    def find_credential_activation(self, *, operation_id: str = "", provider: str = "", profile_id: str = "", request_id: str = "") -> dict[str, Any] | None: ...
    def list_credential_profiles(self) -> list[dict[str, Any]]: ...
    def create_credential_profile(self, provider: str, request_id: str, label: str, digest: str) -> dict[str, Any]: ...
    def archive_credential_profile(self, provider: str, profile_id: str) -> dict[str, Any]: ...
    def rename_credential_profile(self, provider: str, profile_id: str, label: str) -> dict[str, Any]: ...
    def register_credential_profile(self, provider: str, profile_id: str, created_at: str) -> None: ...
    def finalize_credential_activation(self, value: dict[str, Any]) -> dict[str, Any]: ...
    def load_account_settings(self, scope: dict[str, str]) -> dict[str, Any]: ...
    def save_real_account_recovery(self, binding, recovery, received_at: datetime, *, settings_revision: int) -> dict[str, Any]: ...
    def save_real_account_event(self, binding, event_type, event, received_at: datetime, *, settings_revision: int) -> dict[str, Any]: ...
    def save_account_settings(self, value: dict[str, Any], *, expected_revision: int) -> dict[str, Any]: ...
    def load_market_profile_settings(self) -> dict[str, Any]: ...
    def save_market_profile_settings(self, value: dict[str, Any], *, expected_revision: int) -> dict[str, Any]: ...
    def load_credential_activations(self, profile_id: str) -> list[dict[str, Any]]: ...
    def initialize(self) -> None: ...
    def load_query(self, cache_key: str) -> StoredQuery | None: ...
    def save_query(self, cache_key: str, api_id: str, expires_at: float, value: StoredQuery) -> None: ...
    def save_realtime_snapshots(self, values: list[dict[str, Any]]) -> None: ...
    def load_realtime_snapshots(self, codes: list[str]) -> list[dict[str, Any]]: ...
    def load_latest_market_caps(self, codes: list[str]) -> list[dict[str, Any]]: ...
    def save_minute_bars(
        self, values: list[dict[str, Any]], *,
        observations: list[tuple[str, MarketDataObservation[object]]] | None = None,
    ) -> None: ...
    def save_second_trade_bars(self, values: list[dict[str, Any]]) -> None: ...
    def finalize_minute_bars(self, values: list[dict[str, Any]]) -> None: ...
    def replace_minute_bars(
        self, values: list[dict[str, Any]], *,
        observations: list[tuple[str, MarketDataObservation[object]]] | None = None,
    ) -> None: ...
    def load_minute_bars(self, code: str, trading_date: str, market: str = "") -> list[dict[str, Any]]: ...
    def replace_daily_bars(
        self, values: list[dict[str, Any]], *,
        observations: list[tuple[str, MarketDataObservation[object]]] | None = None,
    ) -> None: ...
    def load_daily_bars(self, code: str, market: str = "", limit: int = 250) -> list[dict[str, Any]]: ...
    def save_dataset_snapshot(
        self, kind: str, subject: str, snapshot_key: str, payload: dict[str, Any],
        *, observation: MarketDataObservation[object] | None = None,
    ) -> None: ...
    def save_dataset_snapshots(self, values: list[DatasetSnapshotWrite]) -> None: ...
    def load_dataset_snapshots(self, kind: str, subject: str = "", limit: int = 100) -> list[dict[str, Any]]: ...
    def load_top20_statistics(self, start_date: str, end_date: str) -> dict[str, object]: ...
    def load_observation_revisions(
        self, kind: str, subject: str = "", limit: int = 100,
    ) -> list[dict[str, Any]]: ...
    def load_observation_revisions_after(
        self, after_sequence: int, kinds: tuple[str, ...], limit: int = 1000,
    ) -> list[dict[str, Any]]: ...
    def load_shadow_monitor_state(self, monitor_id: str) -> dict[str, Any] | None: ...
    def save_shadow_monitor_state(self, monitor_id: str, document: dict[str, Any]) -> None: ...
    def save_shadow_evaluation(
        self, monitor_id: str, decision: dict[str, Any],
        candidate: dict[str, Any] | None, expires_at: str = "",
    ) -> None: ...
    def load_shadow_candidates(self, after_sequence: int = 0, limit: int = 100) -> dict[str, Any]: ...
    def create_observation_export(
        self, start: datetime, end: datetime, kinds: tuple[str, ...], subject: str = "",
    ) -> dict[str, Any]: ...
    def load_observation_export_page(
        self, watermark: str, cursor: int = 0, limit: int = 1000,
    ) -> dict[str, Any]: ...
    def save_market_data_metadata(self, observation_key: str, observation: MarketDataObservation[object]) -> None: ...
    def load_market_data_metadata(self, kind: MarketDatasetKind, subject: str, observation_key: str) -> MarketDataMetadata | None: ...
    def load_market_data_metadata_range(
        self, kind: MarketDatasetKind, subject: str, start: datetime, end: datetime,
    ) -> list[CoverageObservation]: ...
    def upsert_documents(self, collection: str, values: list[dict[str, Any]]) -> None: ...
    def replace_documents(self, collection: str, values: list[dict[str, Any]]) -> None: ...
    def load_documents(self, collection: str, owner: str = "", limit: int = 1000, offset: int = 0,
                       updated_after: float = 0.0) -> list[dict[str, Any]]: ...
    def load_document(
        self, collection: str, owner: str, key: str,
    ) -> dict[str, Any] | None: ...
    def load_theme_snapshots(self, *, available_at: float | None = None,
                             limit: int = 100) -> list[dict[str, Any]]: ...
    def enqueue_news_ai_jobs(self, values: list[dict[str, Any]]) -> int: ...
    def claim_news_jobs(self, *, limit: int = 1, now: float | None = None,
                        priority_stock_code: str = "") -> list[dict[str, Any]]: ...
    def finish_news_job(self, job_key: str, output_ref: str) -> None: ...
    def retry_news_job(self, job_key: str, error: str, next_retry_at: float,
                       output_ref: str = "") -> None: ...
    def save_news_body_revision(self, value: dict[str, Any]) -> str: ...
    def save_news_ai_results(self, documents: list[dict[str, Any]],
                             revisions: list[dict[str, Any]],
                             usage_documents: list[dict[str, Any]] | None = None) -> None: ...
    def load_news_history(self, kind: str, *, target: str = "", identity: str = "",
                          available_at: float | None = None, limit: int = 100) -> list[dict[str, Any]]: ...
    def load_latest_news_body(self, article_revision_id: str) -> dict[str, Any] | None: ...
    def load_news_body_revision(self, body_revision_id: str) -> dict[str, Any] | None: ...
    def load_news_article_revision(self, article_revision_id: str) -> dict[str, Any] | None: ...
    def load_stock_news_articles(self, stock_code: str, *, limit: int = 1000) -> list[dict[str, Any]]: ...
    def load_confirmed_news_articles(self, stock_code: str, *, limit: int = 1000) -> list[dict[str, Any]]: ...
    def save_news_event_revision(self, value: dict[str, Any]) -> str: ...
    def claim_news_request(self, scope: str, *, scope_limit: int, hard_limit: int,
                           budget_date: str) -> bool: ...
    def news_request_count(self, budget_date: str) -> int: ...
    def load_news_source_cursor(self, source_id: str) -> dict[str, Any] | None: ...
    def save_news_source_page(self, value: dict[str, Any]) -> dict[str, Any]: ...
    def load_news_source_diagnostics(self, *, source_id: str = "", days: int = 7,
                                     limit: int = 100) -> dict[str, Any]: ...
    def find_news_ai_revision(
        self, *, target_id: str, article_revision_id: str, body_revision_id: str,
        provider: str, model: str, prompt_version: str, schema_version: str,
        input_hash: str,
    ) -> str | None: ...
    def append_vi_events(self, values: list[dict[str, Any]]) -> int: ...
    def record_hot_cohort_revision(self, value: dict[str, Any],
                                   current: dict[str, Any] | None = None) -> bool: ...
    def load_hot_cohort(self, *, active_only: bool = False) -> list[dict[str, Any]]: ...
    def append_upper_limit_facts(self, values: list[dict[str, Any]]) -> int: ...
    def load_market_event_history(self, kind: str, *, code: str = "",
                                  limit: int = 100) -> list[dict[str, Any]]: ...
    def save_external_bars(self, values: list[dict[str, Any]]) -> None: ...
    def load_external_bars(self, instrument: str, timeframe: str, limit: int = 1000) -> list[dict[str, Any]]: ...
    def create_execution_intent(self, value: dict[str, Any], *, ownership: dict[str, str] | None = None) -> bool: ...
    def append_execution_event(self, intent: dict[str, Any], event: dict[str, Any], *, ownership: dict[str, str] | None = None) -> bool: ...
    def load_execution_intent(self, intent_id: str) -> dict[str, Any] | None: ...
    def load_active_execution_intents(
        self, environment: str, account_ref: str, run_id: str,
    ) -> list[dict[str, Any]]: ...
    def find_execution_intent_by_broker_order_id(
        self, environment: str, account_ref: str, run_id: str, broker_order_id: str,
    ) -> dict[str, Any] | None: ...
    def load_execution_events(self, intent_id: str) -> list[dict[str, Any]]: ...
    def load_account_execution_events(
        self, environment: str, account_ref: str, after_sequence: int, limit: int,
    ) -> list[dict[str, Any]]: ...
    def save_execution_account_snapshot(self, value: dict[str, Any], *, ownership: dict[str, str] | None = None) -> bool: ...
    def load_mock_automation_control(self, account_ref: str) -> dict[str, Any] | None: ...
    def save_mock_automation_control(
        self, value: dict[str, Any], *, expected_revision: int,
    ) -> bool: ...
    def register_account_identity(self, value: dict[str, Any]) -> str: ...
    def append_account_binding(self, value: dict[str, Any]) -> dict[str, Any]: ...
    def load_account_bindings(self) -> list[dict[str, Any]]: ...
    def register_account_scope_alias(self, value: dict[str, Any]) -> dict[str, Any]: ...
    def resolve_account_scope(self, broker: str, environment: str, account_ref: str) -> dict[str, Any]: ...
    def acquire_execution_runtime(
        self, owner_key: str, owner_token: str, now: str, lease_expires_at: str,
    ) -> bool: ...
    def release_execution_runtime(self, owner_key: str, owner_token: str) -> bool: ...
    def close(self) -> None: ...
    def storage_size_bytes(self) -> int | None: ...
    def storage_breakdown(self) -> list[dict[str, object]]: ...


class SQLiteQueryStore:
    def release_execution_runtime(self, owner_key: str, owner_token: str) -> bool:
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "DELETE FROM central_execution_runtime_leases WHERE owner_key=? AND owner_token=?",
                (owner_key, owner_token),
            )
            return cursor.rowcount == 1

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

    def load_account_settings(self, scope: dict[str, str]) -> dict[str, Any]:
        with self._lock, self._connection() as connection:
            return _load_account_settings(connection.cursor(), scope, "?")

    def save_real_account_recovery(self, binding, recovery, received_at, *, settings_revision):
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return _save_real_account_recovery(connection.cursor(), binding, recovery, received_at,
                                              settings_revision, "?")

    def save_real_account_event(self, binding, event_type, event, received_at, *, settings_revision):
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return _save_real_account_event(connection.cursor(), binding, event_type, event, received_at,
                                            settings_revision, "?")

    def save_account_settings(self, value: dict[str, Any], *, expected_revision: int) -> dict[str, Any]:
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return _save_account_settings(connection.cursor(), value, expected_revision, "?")

    def load_credential_activations(self, profile_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connection() as connection:
            return _load_credential_activations(connection.cursor(), profile_id, "?")

    def load_market_profile_settings(self) -> dict[str, Any]:
        with self._lock, self._connection() as connection:
            return _load_market_profile_settings(connection.cursor(), "?")

    def save_market_profile_settings(self, value: dict[str, Any], *, expected_revision: int) -> dict[str, Any]:
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return _save_market_profile_settings(connection.cursor(), value, expected_revision, "?")

    """로컬 중앙 서버가 기존 monitor.sqlite3 안에서 사용하는 조회 캐시."""

    def __init__(self, path: Path, *, observation_history_enabled: bool = True) -> None:
        self._path = path
        self._observation_history_enabled = observation_history_enabled
        self._lock = RLock()
        self._memory_uri = f"file:central-query-store-{id(self)}?mode=memory&cache=shared" if str(path) == ":memory:" else ""
        self._keeper: sqlite3.Connection | None = None

    def initialize(self) -> None:
        if self._memory_uri:
            self._keeper = sqlite3.connect(self._memory_uri, uri=True, check_same_thread=False)
        else:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            with connection as transaction:
                cursor = transaction.cursor()
                CentralSchemaMigrationRunner(cursor, "sqlite").apply(
                    central_schema_migrations()
                )

    def storage_size_bytes(self) -> int | None:
        if self._memory_uri:
            return None
        try:
            return self._path.stat().st_size
        except OSError:
            return None

    def storage_breakdown(self) -> list[dict[str, object]]:
        with self._lock, self._connection() as connection:
            tables = [str(row[0]) for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'central_%'"
            ).fetchall()]
            stats: list[tuple[str, int, int]] = []
            for table in tables:
                quoted = '"' + table.replace('"', '""') + '"'
                rows = int(connection.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0])
                try:
                    size_row = connection.execute(
                        "SELECT COALESCE(SUM(pgsize),0) FROM dbstat WHERE name=? OR name IN ("
                        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?)",
                        (table, table),
                    ).fetchone()
                    size = int(size_row[0]) if size_row else 0
                except sqlite3.DatabaseError:
                    size = 0
                stats.append((table, rows, size))
            shared = connection.execute(
                "SELECT collection,COUNT(*),COALESCE(SUM(length(document_json)+length(collection)+"
                "length(owner)+length(document_key)),0) FROM central_documents GROUP BY collection"
            ).fetchall() if "central_documents" in tables else []
        return _storage_breakdown_rows(stats, shared)

    def load_query(self, cache_key: str) -> StoredQuery | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json,has_next,next_key FROM central_api_query_cache "
                "WHERE cache_key=? AND expires_at>?", (cache_key, time()),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(str(row[0]))
        return StoredQuery(payload, bool(row[1]), str(row[2])) if isinstance(payload, dict) else None

    def save_query(self, cache_key: str, api_id: str, expires_at: float, value: StoredQuery) -> None:
        encoded = json.dumps(value.payload, ensure_ascii=False, separators=(",", ":"))
        with self._lock, self._connection() as connection:
            connection.execute(
                "INSERT INTO central_api_query_cache(cache_key,api_id,expires_at,payload_json,has_next,next_key) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(cache_key) DO UPDATE SET "
                "api_id=excluded.api_id,expires_at=excluded.expires_at,payload_json=excluded.payload_json,"
                "has_next=excluded.has_next,next_key=excluded.next_key",
                (cache_key, api_id, expires_at, encoded, int(value.has_next), value.next_key),
            )
            connection.execute("DELETE FROM central_api_query_cache WHERE expires_at<=?", (time(),))

    def close(self) -> None:
        keeper, self._keeper = self._keeper, None
        if keeper is not None:
            keeper.close()

    def save_realtime_snapshots(self, values: list[dict[str, Any]]) -> None:
        if not values:
            return
        rows = [(
            str(value["event_type"]), str(value["item_key"]), float(value["received_at"]),
            json.dumps(value["event"], ensure_ascii=False, separators=(",", ":")),
        ) for value in values]
        with self._lock, self._connection() as connection:
            connection.executemany(
                "INSERT INTO central_realtime_latest(event_type,item_key,received_at,event_json) "
                "VALUES(?,?,?,?) ON CONFLICT(event_type,item_key) DO UPDATE SET "
                "received_at=excluded.received_at,event_json=excluded.event_json",
                rows,
            )

    def load_realtime_snapshots(self, codes: list[str]) -> list[dict[str, Any]]:
        placeholders = ",".join("?" for _ in codes)
        condition = f"item_key IN ({placeholders}) OR event_type='market_state'" if codes else "event_type='market_state'"
        parameters: list[object] = [*codes, time() - 300]
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                f"SELECT event_json FROM central_realtime_latest WHERE ({condition}) AND received_at>? "
                "ORDER BY received_at", parameters,
            ).fetchall()
        return [value for row in rows if isinstance((value := json.loads(str(row[0]))), dict)]

    def load_latest_market_caps(self, codes: list[str]) -> list[dict[str, Any]]:
        """Return the last persisted 0B market cap without reviving stale prices."""
        if not codes:
            return []
        placeholders = ",".join("?" for _ in codes)
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                "SELECT item_key,received_at,event_json FROM central_realtime_latest "
                f"WHERE event_type='trade' AND item_key IN ({placeholders})",
                codes,
            ).fetchall()
        return _latest_market_cap_rows(rows)

    def save_minute_bars(
        self, values: list[dict[str, Any]], *,
        observations: list[tuple[str, MarketDataObservation[object]]] | None = None,
    ) -> None:
        if not values:
            return
        observation_by_key = dict(observations or ())
        with self._lock, self._connection() as connection:
            for value in values:
                operation_id, operation_hash = _minute_operation(value)
                processed = connection.execute(
                    "SELECT operation_hash FROM central_minute_bar_operations WHERE operation_id=?",
                    (operation_id,),
                ).fetchone()
                if processed is not None:
                    if str(processed[0]) != operation_hash:
                        raise ValueError("minute bar operation_id payload changed")
                    continue
                connection.execute(
                    "INSERT INTO central_minute_bars VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(trading_date,minute,code,market) DO UPDATE SET "
                    "high=MAX(central_minute_bars.high,excluded.high),"
                    "low=MIN(central_minute_bars.low,excluded.low),close=excluded.close,"
                    "volume=central_minute_bars.volume+excluded.volume,"
                    "trade_value_million_won=central_minute_bars.trade_value_million_won+excluded.trade_value_million_won,"
                    "updated_at=excluded.updated_at",
                    bar_value_rows((value,), minute=True)[0],
                )
                key = _minute_key(value)
                observation = observation_by_key.get(key)
                if observation is not None:
                    merged = _load_sqlite_minute_bar(connection, value)
                    merged_observation = MarketDataObservation(
                        observation.kind, observation.subject, merged, observation.metadata,
                    )
                    connection.execute(
                        _market_metadata_upsert_sql("?", "excluded"),
                        market_metadata_storage_values(key, merged_observation),
                    )
                    if self._observation_history_enabled:
                        _append_sqlite_observation_revision(
                            connection, "minute_bar", observation.subject, key,
                            minute_bar_revision_payload(
                                merged, window_closed=False, capture_quality="in_progress",
                                finalization_source="realtime_flush", operation_id=operation_id,
                            ),
                            merged_observation,
                        )
                connection.execute(
                    "INSERT INTO central_minute_bar_operations(operation_id,operation_hash,processed_at) "
                    "VALUES(?,?,?)",
                    (operation_id, operation_hash, datetime.now(timezone.utc).isoformat()),
                )

    def finalize_minute_bars(self, values: list[dict[str, Any]]) -> None:
        if not values:
            return
        with self._lock, self._connection() as connection:
            for closure in values:
                operation_id, operation_hash = _minute_operation(closure, finalization=True)
                processed = connection.execute(
                    "SELECT operation_hash FROM central_minute_bar_operations WHERE operation_id=?",
                    (operation_id,),
                ).fetchone()
                if processed is not None:
                    if str(processed[0]) != operation_hash:
                        raise ValueError("minute bar operation_id payload changed")
                    continue
                merged = _load_sqlite_minute_bar(connection, closure)
                if merged is not None:
                    _save_final_minute_revision_sqlite(
                        connection, merged, closure, self._observation_history_enabled,
                    )
                connection.execute(
                    "INSERT INTO central_minute_bar_operations(operation_id,operation_hash,processed_at) "
                    "VALUES(?,?,?)",
                    (operation_id, operation_hash, datetime.now(timezone.utc).isoformat()),
                )

    def save_second_trade_bars(self, values: list[dict[str, Any]]) -> None:
        if not values:
            return
        with self._lock, self._connection() as connection:
            connection.executemany(
                "INSERT INTO central_second_trade_bars VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(trading_date,trade_second,code,market) DO UPDATE SET "
                "open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,"
                "volume=excluded.volume,trade_value_won=excluded.trade_value_won,"
                "trade_count=excluded.trade_count,available_at=excluded.available_at "
                "WHERE excluded.available_at>central_second_trade_bars.available_at OR "
                "(excluded.available_at=central_second_trade_bars.available_at AND "
                "excluded.trade_count>=central_second_trade_bars.trade_count)",
                second_trade_bar_value_rows(values),
            )

    def load_minute_bars(self, code: str, trading_date: str, market: str = "") -> list[dict[str, Any]]:
        sql = (
            "SELECT trading_date,minute,code,market,open,high,low,close,volume,trade_value_million_won,updated_at "
            "FROM central_minute_bars WHERE code=? AND trading_date=?"
        )
        parameters: list[object] = [code, trading_date]
        if market:
            sql += " AND market=?"
            parameters.append(market)
        sql += " ORDER BY minute"
        with self._lock, self._connection() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return bar_result_rows(rows, minute=True)

    def replace_minute_bars(
        self, values: list[dict[str, Any]], *,
        observations: list[tuple[str, MarketDataObservation[object]]] | None = None,
    ) -> None:
        self._replace_bars(
            "central_minute_bars", values, minute=True, observations=observations
        )

    def replace_daily_bars(
        self, values: list[dict[str, Any]], *,
        observations: list[tuple[str, MarketDataObservation[object]]] | None = None,
    ) -> None:
        self._replace_bars(
            "central_daily_bars", values, minute=False, observations=observations
        )

    def _replace_bars(
        self, table: str, values: list[dict[str, Any]], *, minute: bool,
        observations: list[tuple[str, MarketDataObservation[object]]] | None = None,
    ) -> None:
        if not values:
            return
        columns = bar_columns(minute=minute)
        placeholders = ",".join("?" for _ in columns)
        updates = ",".join(f"{column}=excluded.{column}" for column in columns if column not in BAR_KEY_COLUMNS)
        conflict = "trading_date,minute,code,market" if minute else "trading_date,code,market"
        with self._lock, self._connection() as connection:
            connection.executemany(
                f"INSERT INTO {table}({','.join(columns)}) VALUES({placeholders}) "
                f"ON CONFLICT({conflict}) DO UPDATE SET {updates}",
                bar_value_rows(values, minute=minute),
            )
            _save_sqlite_metadata(connection, observations)
            if minute and observations and self._observation_history_enabled:
                for value, (key, observation) in zip(values, observations, strict=True):
                    _append_sqlite_observation_revision(
                        connection, "minute_bar", observation.subject, key,
                        minute_bar_revision_payload(
                            value,
                            window_closed=observation.metadata.completeness in {
                                DataCompleteness.COMPLETE, DataCompleteness.PARTIAL,
                            },
                            capture_quality=(
                                "complete" if observation.metadata.completeness == DataCompleteness.COMPLETE
                                else observation.metadata.completeness.value
                            ),
                            finalization_source="query_response",
                        ),
                        observation,
                    )

    def load_daily_bars(self, code: str, market: str = "", limit: int = 250) -> list[dict[str, Any]]:
        sql = ("SELECT trading_date,code,market,open,high,low,close,volume,trade_value_million_won,updated_at "
               "FROM central_daily_bars WHERE code=?")
        parameters: list[object] = [code]
        if market:
            sql += " AND market=?"
            parameters.append(market)
        sql += " ORDER BY trading_date DESC LIMIT ?"
        parameters.append(bounded_limit(limit, 5000))
        with self._lock, self._connection() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return bar_result_rows(rows, minute=False)

    def save_dataset_snapshot(
        self, kind: str, subject: str, snapshot_key: str, payload: dict[str, Any],
        *, observation: MarketDataObservation[object] | None = None,
    ) -> None:
        self.save_dataset_snapshots([(kind, subject, snapshot_key, payload, observation)])

    def save_dataset_snapshots(self, values: list[DatasetSnapshotWrite]) -> None:
        if not values:
            return
        saved_at = time()
        with self._lock, self._connection() as connection:
            for kind, subject, snapshot_key, payload, observation in values:
                connection.execute(
                    "INSERT INTO central_dataset_snapshots(kind,subject,snapshot_key,saved_at,payload_json) "
                    "VALUES(?,?,?,?,?) ON CONFLICT(kind,subject,snapshot_key) DO UPDATE SET "
                    "saved_at=excluded.saved_at,payload_json=excluded.payload_json",
                    (kind, subject, snapshot_key, saved_at, json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),
                )
                if observation is not None:
                    connection.execute(
                        _market_metadata_upsert_sql("?", "excluded"),
                        market_metadata_storage_values(snapshot_key, observation),
                    )
                    if self._observation_history_enabled and kind in RESEARCH_OBSERVATION_KINDS:
                        _append_sqlite_observation_revision(
                            connection, kind, subject, snapshot_key, payload, observation,
                        )

    def load_dataset_snapshots(self, kind: str, subject: str = "", limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT subject,snapshot_key,saved_at,payload_json FROM central_dataset_snapshots WHERE kind=?"
        parameters: list[object] = [kind]
        if subject:
            sql += " AND subject=?"
            parameters.append(subject)
        sql += " ORDER BY snapshot_key DESC LIMIT ?"
        parameters.append(bounded_limit(limit, 5000))
        with self._lock, self._connection() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return dataset_snapshot_result_rows(rows)

    def load_top20_statistics(self, start_date: str, end_date: str) -> dict[str, object]:
        with self._lock, self._connection() as connection:
            top20_rows = connection.execute(
                "SELECT subject,snapshot_key,payload_json FROM central_dataset_snapshots "
                "WHERE kind='top20_index' AND subject>=? AND subject<=? ORDER BY snapshot_key",
                (start_date, end_date),
            ).fetchall()
            start_raw, end_raw = start_date.replace("-", ""), end_date.replace("-", "")
            market_rows = connection.execute(
                "SELECT subject,payload_json FROM central_dataset_snapshots "
                "WHERE kind='market_index_chart' AND subject>=? AND subject<? ORDER BY subject",
                (start_raw, end_raw + "~"),
            ).fetchall()
        return _top20_statistics_document(top20_rows, market_rows)

    def load_observation_revisions(
        self, kind: str, subject: str = "", limit: int = 100,
    ) -> list[dict[str, Any]]:
        sql = _observation_revision_select("?")
        parameters: list[object] = [kind]
        if subject:
            sql += " AND subject=?"
            parameters.append(subject)
        sql += " ORDER BY accepted_sequence DESC LIMIT ?"
        parameters.append(bounded_limit(limit, 5000))
        with self._lock, self._connection() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return observation_revision_result_rows(rows)

    def load_observation_revisions_after(
        self, after_sequence: int, kinds: tuple[str, ...], limit: int = 1000,
    ) -> list[dict[str, Any]]:
        normalized = tuple(dict.fromkeys(str(value) for value in kinds if str(value)))
        if not normalized:
            return []
        placeholders = ",".join("?" for _ in normalized)
        sql = (
            f"SELECT {_observation_revision_columns()} FROM central_observation_revisions "
            f"WHERE accepted_sequence>? AND kind IN ({placeholders}) "
            "ORDER BY accepted_sequence LIMIT ?"
        )
        parameters = [max(0, int(after_sequence)), *normalized, bounded_limit(limit, 5000)]
        with self._lock, self._connection() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return observation_revision_result_rows(rows)

    def load_shadow_monitor_state(self, monitor_id: str) -> dict[str, Any] | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT document_json FROM central_shadow_monitor_state WHERE monitor_id=?",
                (monitor_id,),
            ).fetchone()
        return json_mapping(row[0]) if row else None

    def save_shadow_monitor_state(self, monitor_id: str, document: dict[str, Any]) -> None:
        encoded = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self._lock, self._connection() as connection:
            connection.execute(
                "INSERT INTO central_shadow_monitor_state VALUES(?,?,?) "
                "ON CONFLICT(monitor_id) DO UPDATE SET updated_at=excluded.updated_at,document_json=excluded.document_json",
                (monitor_id, datetime.now(timezone.utc).isoformat(), encoded),
            )

    def save_shadow_evaluation(
        self, monitor_id: str, decision: dict[str, Any],
        candidate: dict[str, Any] | None, expires_at: str = "",
    ) -> None:
        with self._lock, self._connection() as connection:
            _save_sqlite_shadow_evaluation(connection, monitor_id, decision, candidate, expires_at)

    def load_shadow_candidates(self, after_sequence: int = 0, limit: int = 100) -> dict[str, Any]:
        page_limit = bounded_limit(limit, 1000)
        with self._lock, self._connection() as connection:
            watermark_row = connection.execute(
                "SELECT COALESCE(MAX(accepted_sequence),0) FROM central_shadow_candidate_events"
            ).fetchone()
            rows = connection.execute(
                "SELECT accepted_sequence,document_json,expires_at FROM central_shadow_candidate_events "
                "WHERE accepted_sequence>? ORDER BY accepted_sequence LIMIT ?",
                (max(0, int(after_sequence)), page_limit),
            ).fetchall()
        return _shadow_candidate_page(rows, int(watermark_row[0]) if watermark_row else 0)

    def create_observation_export(
        self, start: datetime, end: datetime, kinds: tuple[str, ...], subject: str = "",
    ) -> dict[str, Any]:
        normalized_start, normalized_end, normalized_kinds = _observation_export_inputs(
            start, end, kinds,
        )
        placeholders = ",".join("?" for _ in normalized_kinds)
        sql = (
            "SELECT revision_id,source_id,available_at FROM central_observation_revisions "
            f"WHERE kind IN ({placeholders}) AND available_at>=? AND available_at<?"
        )
        parameters: list[object] = [*normalized_kinds, normalized_start.isoformat(), normalized_end.isoformat()]
        if subject:
            sql += " AND subject=?"
            parameters.append(subject)
        sql += " ORDER BY available_at,accepted_sequence,revision_id"
        with self._lock, self._connection() as connection:
            selected = connection.execute(sql, parameters).fetchall()
            manifest = _observation_export_manifest(
                normalized_start, normalized_end, normalized_kinds, subject, selected,
            )
            connection.execute(
                "INSERT INTO central_research_exports VALUES(?,?,?,?,?,?,?,?,?)",
                _sqlite_export_values(manifest),
            )
            connection.executemany(
                "INSERT INTO central_research_export_members VALUES(?,?,?)",
                [(manifest["dataset_id"], ordinal, str(row[0]))
                 for ordinal, row in enumerate(selected, start=1)],
            )
        return manifest

    def load_observation_export_page(
        self, watermark: str, cursor: int = 0, limit: int = 1000,
    ) -> dict[str, Any]:
        page_limit = bounded_limit(limit, 1000)
        with self._lock, self._connection() as connection:
            manifest_row = connection.execute(
                "SELECT manifest_json FROM central_research_exports WHERE dataset_id=?", (watermark,),
            ).fetchone()
            if manifest_row is None:
                raise ValueError("unknown research export watermark")
            rows = connection.execute(
                _observation_export_page_sql("?"),
                (watermark, max(0, int(cursor)), page_limit + 1),
            ).fetchall()
        return _observation_export_page(manifest_row[0], rows, page_limit)

    def save_market_data_metadata(
        self, observation_key: str, observation: MarketDataObservation[object]
    ) -> None:
        values = market_metadata_storage_values(observation_key, observation)
        with self._lock, self._connection() as connection:
            connection.execute(
                _market_metadata_upsert_sql("?", "excluded"),
                values,
            )

    def load_market_data_metadata(
        self, kind: MarketDatasetKind, subject: str, observation_key: str
    ) -> MarketDataMetadata | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT effective_at,available_at,venue,unit,value_kind,completeness,origin,source,"
                "candidate_universe FROM central_market_data_observation_meta "
                "WHERE dataset_kind=? AND subject=? AND observation_key=?",
                (kind.value, subject, observation_key),
            ).fetchone()
        return market_metadata_from_storage_row(row)

    def load_market_data_metadata_range(
        self, kind: MarketDatasetKind, subject: str, start: datetime, end: datetime,
    ) -> list[CoverageObservation]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                "SELECT observation_key,effective_at,available_at,venue,unit,value_kind,"
                "completeness,origin,source,candidate_universe "
                "FROM central_market_data_observation_meta WHERE dataset_kind=? AND subject=? "
                "AND effective_at>=? AND effective_at<? ORDER BY effective_at",
                (kind.value, subject, start.isoformat(), end.isoformat()),
            ).fetchall()
        return [
            CoverageObservation(str(row[0]), _metadata_from_range_row(row))
            for row in rows
        ]

    def upsert_documents(self, collection: str, values: list[dict[str, Any]]) -> None:
        if not values:
            return
        now = time()
        with self._lock, self._connection() as connection:
            connection.executemany(
                "INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
                "VALUES(?,?,?,?,?) ON CONFLICT(collection,owner,document_key) DO UPDATE SET "
                "updated_at=excluded.updated_at,document_json=excluded.document_json "
                "WHERE central_documents.document_json<>excluded.document_json",
                document_value_rows(collection, values, now),
            )
            if collection == "theme_metadata":
                _append_sqlite_theme_snapshot(connection, values, received_at=now)
            elif collection == "news_article":
                _append_sqlite_news_articles(connection, values, received_at=now)

    def replace_documents(self, collection: str, values: list[dict[str, Any]]) -> None:
        """컬렉션 전체를 한 트랜잭션에서 현재 스냅샷으로 교체한다."""
        now = time()
        rows = document_value_rows(collection, values, now)
        with self._lock, self._connection() as connection:
            connection.execute("DELETE FROM central_documents WHERE collection=?", (collection,))
            if rows:
                connection.executemany(
                    "INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
                    "VALUES(?,?,?,?,?)", rows,
                )
            if collection == "theme_metadata":
                _append_sqlite_theme_snapshot(connection, values, received_at=now)

    def load_documents(
        self, collection: str, owner: str = "", limit: int = 1000, offset: int = 0,
        updated_after: float = 0.0,
    ) -> list[dict[str, Any]]:
        sql, parameters = document_select_query(
            collection, owner, limit, offset, updated_after, placeholder="?",
        )
        with self._lock, self._connection() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return document_result_rows(rows)

    def load_document(
        self, collection: str, owner: str, key: str,
    ) -> dict[str, Any] | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT owner,document_key,updated_at,document_json "
                "FROM central_documents WHERE collection=? AND owner=? AND document_key=?",
                (collection, owner, key),
            ).fetchone()
        return document_result_rows((row,))[0] if row is not None else None

    def load_theme_snapshots(
        self, *, available_at: float | None = None, limit: int = 100,
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT snapshot_id,profile_id,content_hash,effective_at,received_at,available_at,"
            "origin_device,revision_of,document_json FROM central_theme_snapshots"
        )
        parameters: list[object] = []
        if available_at is not None:
            sql += " WHERE available_at<=?"
            parameters.append(float(available_at))
        sql += " ORDER BY accepted_sequence DESC LIMIT ?"
        parameters.append(bounded_limit(limit, 1000))
        with self._lock, self._connection() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return _theme_snapshot_result_rows(rows)

    def enqueue_news_ai_jobs(self, values: list[dict[str, Any]]) -> int:
        with self._lock, self._connection() as connection:
            return _enqueue_sqlite_news_ai_jobs(connection, values)

    def claim_news_jobs(self, *, limit: int = 1, now: float | None = None,
                        priority_stock_code: str = "") -> list[dict[str, Any]]:
        claimed_at = float(now if now is not None else time())
        with self._lock, self._connection() as connection:
            connection.execute(
                "UPDATE central_news_jobs SET state='PENDING',updated_at=? "
                "WHERE state='RUNNING' AND updated_at<?",
                (claimed_at, claimed_at - 120.0),
            )
            priority = str(priority_stock_code or "").strip()
            rows = connection.execute(
                "SELECT job_key,article_revision_id,stock_code,target_id,stage,input_hash,"
                "processing_version,attempts,payload_json FROM central_news_jobs "
                "WHERE state='PENDING' AND next_retry_at<=? "
                "ORDER BY CASE "
                "WHEN ?<>'' AND (stock_code=? OR target_id=?) AND stage='BODY' THEN 0 "
                "WHEN ?<>'' AND (stock_code=? OR target_id=?) THEN 1 "
                "WHEN stage='BODY' AND stock_code<>'GLOBAL' THEN 2 "
                "WHEN stage='BODY' THEN 3 WHEN stage='AI' THEN 4 ELSE 5 END,"
                "CASE WHEN stage='BODY' THEN -updated_at ELSE updated_at END LIMIT ?",
                (claimed_at, priority, priority, priority, priority, priority, priority,
                 bounded_limit(limit, 4)),
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE central_news_jobs SET state='RUNNING',attempts=attempts+1,updated_at=? "
                    "WHERE job_key=?", (claimed_at, row[0]),
                )
        return _news_job_rows(rows)

    def finish_news_job(self, job_key: str, output_ref: str) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                "UPDATE central_news_jobs SET state='COMPLETED',output_ref=?,error='',updated_at=? "
                "WHERE job_key=?", (output_ref, time(), job_key),
            )

    def retry_news_job(self, job_key: str, error: str, next_retry_at: float,
                       output_ref: str = "") -> None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT attempts FROM central_news_jobs WHERE job_key=?", (job_key,),
            ).fetchone()
            state = "FAILED" if row is not None and int(row[0]) >= 3 else "PENDING"
            connection.execute(
                "UPDATE central_news_jobs SET state=?,error=?,next_retry_at=?,output_ref=?,updated_at=? "
                "WHERE job_key=?",
                (state, error[:1000], float(next_retry_at), output_ref, time(), job_key),
            )

    def save_news_body_revision(self, value: dict[str, Any]) -> str:
        with self._lock, self._connection() as connection:
            return _save_sqlite_news_body(connection, value)

    def save_news_ai_results(self, documents: list[dict[str, Any]],
                             revisions: list[dict[str, Any]],
                             usage_documents: list[dict[str, Any]] | None = None) -> None:
        now = time()
        with self._lock, self._connection() as connection:
            connection.executemany(
                "INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
                "VALUES(?,?,?,?,?) ON CONFLICT(collection,owner,document_key) DO UPDATE SET "
                "updated_at=excluded.updated_at,document_json=excluded.document_json "
                "WHERE central_documents.document_json<>excluded.document_json",
                document_value_rows("news_ai", documents, now),
            )
            _save_sqlite_news_ai(connection, revisions)
            if usage_documents:
                connection.executemany(
                    "INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
                    "VALUES(?,?,?,?,?) ON CONFLICT(collection,owner,document_key) DO UPDATE SET "
                    "updated_at=excluded.updated_at,document_json=excluded.document_json "
                    "WHERE central_documents.document_json<>excluded.document_json",
                    document_value_rows("news_request_usage", usage_documents, now),
                )

    def load_news_history(self, kind: str, *, target: str = "", identity: str = "",
                          available_at: float | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock, self._connection() as connection:
            return _load_sqlite_news_history(
                connection, kind, target, identity, available_at, limit,
            )

    def load_latest_news_body(self, article_revision_id: str) -> dict[str, Any] | None:
        values = self.load_news_history("body", target=article_revision_id, limit=1)
        return values[0] if values else None

    def load_news_body_revision(self, body_revision_id: str) -> dict[str, Any] | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT body_revision_id,article_revision_id,content_hash,extractor_version,fetched_at,"
                "available_at,status,body_text,error FROM central_news_body_revisions "
                "WHERE body_revision_id=?", (body_revision_id,),
            ).fetchone()
        return _decode_news_history("body", [row])[0] if row else None

    def load_news_article_revision(self, article_revision_id: str) -> dict[str, Any] | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT article_revision_id,stock_code,identity,content_hash,collector_id,published_at,"
                "received_at,available_at,collection_scope,revision_of,document_json "
                "FROM central_news_article_revisions WHERE article_revision_id=?", (article_revision_id,),
            ).fetchone()
            return _decode_news_history("article", [row])[0] if row else None

    def load_stock_news_articles(self, stock_code: str, *, limit: int = 1000) -> list[dict[str, Any]]:
        with self._lock, self._connection() as connection:
            return _load_sqlite_stock_news_articles(connection, stock_code, limit)

    def load_confirmed_news_articles(self, stock_code: str, *, limit: int = 1000) -> list[dict[str, Any]]:
        with self._lock, self._connection() as connection:
            return _load_sqlite_confirmed_news_articles(connection, stock_code, limit)

    def save_news_event_revision(self, value: dict[str, Any]) -> str:
        with self._lock, self._connection() as connection:
            return _save_sqlite_news_event(connection, value)

    def claim_news_request(self, scope: str, *, scope_limit: int, hard_limit: int,
                           budget_date: str) -> bool:
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            total = connection.execute(
                "SELECT COALESCE(SUM(request_count),0) FROM central_news_request_budget WHERE budget_date=?",
                (budget_date,),
            ).fetchone()
            current = connection.execute(
                "SELECT request_count FROM central_news_request_budget WHERE budget_date=? AND scope=?",
                (budget_date, scope),
            ).fetchone()
            if int(total[0]) >= int(hard_limit) or (current and int(current[0]) >= int(scope_limit)):
                return False
            connection.execute(
                "INSERT INTO central_news_request_budget(budget_date,scope,request_count,updated_at) "
                "VALUES(?,?,1,?) ON CONFLICT(budget_date,scope) DO UPDATE SET "
                "request_count=central_news_request_budget.request_count+1,updated_at=excluded.updated_at",
                (budget_date, scope, time()),
            )
            return True

    def news_request_count(self, budget_date: str) -> int:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(request_count),0) FROM central_news_request_budget WHERE budget_date=?",
                (budget_date,),
            ).fetchone()
        return int(row[0]) if row else 0

    def load_news_source_cursor(self, source_id: str) -> dict[str, Any] | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT source_id,scope,query_text,cursor_published_at,cursor_identity,pending_published_at,"
                "pending_identity,next_start,next_schedule_at,checked_at,last_success,coverage,truncated,error,updated_at "
                "FROM central_news_source_cursors WHERE source_id=?", (source_id,),
            ).fetchone()
        return _news_source_cursor(row) if row else None

    def save_news_source_page(self, value: dict[str, Any]) -> dict[str, Any]:
        with self._lock, self._connection() as connection:
            return _save_sqlite_news_source_page(connection, value)

    def load_news_source_diagnostics(self, *, source_id: str = "", days: int = 7,
                                     limit: int = 100) -> dict[str, Any]:
        with self._lock, self._connection() as connection:
            return _load_sqlite_news_source_diagnostics(connection, source_id, days, limit)

    def find_news_ai_revision(
        self, *, target_id: str, article_revision_id: str, body_revision_id: str,
        provider: str, model: str, prompt_version: str, schema_version: str,
        input_hash: str,
    ) -> str | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT analysis_revision_id FROM central_news_ai_revisions WHERE target_id=? "
                "AND article_revision_id=? AND body_revision_id=? AND provider=? AND model=? "
                "AND prompt_version=? AND schema_version=? AND input_hash=? "
                "ORDER BY accepted_sequence DESC LIMIT 1",
                (target_id, article_revision_id, body_revision_id, provider, model,
                 prompt_version, schema_version, input_hash),
            ).fetchone()
        return str(row[0]) if row else None

    def append_vi_events(self, values: list[dict[str, Any]]) -> int:
        inserted = 0
        with self._lock, self._connection() as connection:
            for value in values:
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO central_vi_event_revisions("
                    "event_id,event_key,stock_code,event_kind,vi_type,effective_at,received_at,available_at,"
                    "price,direction,trigger_count,exchange,source,document_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    _vi_event_values(value),
                )
                inserted += max(0, cursor.rowcount)
        return inserted

    def record_hot_cohort_revision(self, value: dict[str, Any],
                                   current: dict[str, Any] | None = None) -> bool:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO central_hot_cohort_revisions("
                "revision_id,revision_key,stock_code,event_type,condition_name,condition_seq,session_id,"
                "effective_at,available_at,document_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
                _cohort_revision_values(value),
            )
            if current is not None:
                connection.execute(
                    "INSERT INTO central_hot_cohort_current(stock_code,stock_name,condition_name,first_seen_at,"
                    "entry_session,last_signal,last_signal_at,active,nxt_eligible,expired_at,document_json) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(stock_code) DO UPDATE SET "
                    "stock_name=excluded.stock_name,condition_name=excluded.condition_name,"
                    "first_seen_at=excluded.first_seen_at,entry_session=excluded.entry_session,"
                    "last_signal=excluded.last_signal,last_signal_at=excluded.last_signal_at,active=excluded.active,"
                    "nxt_eligible=COALESCE(excluded.nxt_eligible,central_hot_cohort_current.nxt_eligible),"
                    "expired_at=excluded.expired_at,document_json=excluded.document_json",
                    _cohort_current_values(current),
                )
            return cursor.rowcount > 0

    def load_hot_cohort(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        sql = ("SELECT stock_code,stock_name,condition_name,first_seen_at,entry_session,last_signal,"
               "last_signal_at,active,nxt_eligible,expired_at,document_json FROM central_hot_cohort_current")
        if active_only:
            sql += " WHERE active=1"
        sql += " ORDER BY first_seen_at,stock_code"
        with self._lock, self._connection() as connection:
            rows = connection.execute(sql).fetchall()
        return _cohort_current_rows(rows)

    def append_upper_limit_facts(self, values: list[dict[str, Any]]) -> int:
        inserted = 0
        with self._lock, self._connection() as connection:
            for value in values:
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO central_upper_limit_fact_revisions("
                    "fact_id,fact_key,stock_code,session_id,status,upper_limit_price,current_price,high_price,"
                    "effective_at,available_at,source,evidence,document_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    _upper_limit_values(value),
                )
                inserted += max(0, cursor.rowcount)
        return inserted

    def load_market_event_history(self, kind: str, *, code: str = "",
                                  limit: int = 100) -> list[dict[str, Any]]:
        table, code_column = _market_event_table(kind)
        sql = f"SELECT document_json FROM {table}"
        parameters: list[object] = []
        if code:
            sql += f" WHERE {code_column}=?"
            parameters.append(code)
        sql += " ORDER BY accepted_sequence DESC LIMIT ?"
        parameters.append(bounded_limit(limit, 1000))
        with self._lock, self._connection() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [json_mapping(row[0]) for row in rows]

    def save_external_bars(self, values: list[dict[str, Any]]) -> None:
        if not values:
            return
        with self._lock, self._connection() as connection:
            connection.executemany(
                "INSERT INTO central_external_bars VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(provider,instrument,contract,timeframe,bar_time) DO UPDATE SET "
                "open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,"
                "volume=excluded.volume,updated_at=excluded.updated_at",
                [_external_bar_values(value) for value in values],
            )

    def load_external_bars(self, instrument: str, timeframe: str, limit: int = 1000) -> list[dict[str, Any]]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                "SELECT provider,instrument,contract,timeframe,bar_time,open,high,low,close,volume,updated_at "
                "FROM central_external_bars WHERE instrument=? AND timeframe=? "
                "ORDER BY bar_time DESC LIMIT ?", (instrument, timeframe, bounded_limit(limit, 10000)),
            ).fetchall()
        return [_external_bar_result(row) for row in reversed(rows)]

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


    def _connect(self) -> sqlite3.Connection:
        connection = (
            sqlite3.connect(self._memory_uri, timeout=10, uri=True, check_same_thread=False)
            if self._memory_uri else sqlite3.connect(self._path, timeout=10)
        )
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()


class PostgresQueryStore:
    def release_execution_runtime(self, owner_key: str, owner_token: str) -> bool:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM central_execution_runtime_leases WHERE owner_key=%s AND owner_token=%s",
                (owner_key, owner_token),
            )
            return cursor.rowcount == 1

    def find_credential_activation(self, *, operation_id: str = "", provider: str = "", profile_id: str = "", request_id: str = "") -> dict[str, Any] | None:
        with self._connect() as connection, connection.cursor() as cursor:
            return _find_credential_activation(cursor, operation_id, provider, profile_id, request_id, "%s")

    def list_credential_profiles(self) -> list[dict[str, Any]]:
        with self._connect() as connection, connection.cursor() as cursor:
            return _list_credential_profiles(cursor)

    def create_credential_profile(self, provider: str, request_id: str, label: str, digest: str) -> dict[str, Any]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("credential-activation",))
            return _create_credential_profile(cursor, provider, request_id, label, digest, "%s")

    def archive_credential_profile(self, provider: str, profile_id: str) -> dict[str, Any]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("credential-activation",))
            return _archive_credential_profile(cursor, provider, profile_id, "%s")

    def rename_credential_profile(self, provider: str, profile_id: str, label: str) -> dict[str, Any]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("credential-activation",))
            return _rename_credential_profile(cursor, provider, profile_id, label, "%s")

    def register_credential_profile(self, provider: str, profile_id: str, created_at: str) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("credential-activation",))
            _register_credential_profile(cursor, provider, profile_id, created_at, "%s")

    def finalize_credential_activation(self, value: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("credential-activation",))
            return _finalize_credential_activation(cursor, value, "%s")

    def load_account_settings(self, scope: dict[str, str]) -> dict[str, Any]:
        with self._connect() as connection, connection.cursor() as cursor:
            return _load_account_settings(cursor, scope, "%s")

    def save_real_account_recovery(self, binding, recovery, received_at, *, settings_revision):
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("credential-activation",))
            return _save_real_account_recovery(cursor, binding, recovery, received_at, settings_revision, "%s")

    def save_real_account_event(self, binding, event_type, event, received_at, *, settings_revision):
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("credential-activation",))
            return _save_real_account_event(cursor, binding, event_type, event, received_at, settings_revision, "%s")

    def save_account_settings(self, value: dict[str, Any], *, expected_revision: int) -> dict[str, Any]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("credential-activation",))
            return _save_account_settings(cursor, value, expected_revision, "%s")

    def load_credential_activations(self, profile_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection, connection.cursor() as cursor:
            return _load_credential_activations(cursor, profile_id, "%s")

    def load_market_profile_settings(self) -> dict[str, Any]:
        with self._connect() as connection, connection.cursor() as cursor:
            return _load_market_profile_settings(cursor, "%s")

    def save_market_profile_settings(self, value: dict[str, Any], *, expected_revision: int) -> dict[str, Any]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("credential-activation",))
            return _save_market_profile_settings(cursor, value, expected_revision, "%s")

    """시놀로지 PostgreSQL에서 사용하는 동일 규격의 조회 캐시."""

    def __init__(self, database_url: str, *, observation_history_enabled: bool = True) -> None:
        self._database_url = database_url
        self._observation_history_enabled = observation_history_enabled

    def initialize(self) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            CentralSchemaMigrationRunner(cursor, "postgres").apply(
                central_schema_migrations()
            )

    def load_query(self, cache_key: str) -> StoredQuery | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT payload_json,has_next,next_key FROM central_api_query_cache "
                "WHERE cache_key=%s AND expires_at>%s", (cache_key, time()),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        return StoredQuery(payload, bool(row[1]), str(row[2]))

    def save_query(self, cache_key: str, api_id: str, expires_at: float, value: StoredQuery) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO central_api_query_cache(cache_key,api_id,expires_at,payload_json,has_next,next_key) "
                "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(cache_key) DO UPDATE SET "
                "api_id=EXCLUDED.api_id,expires_at=EXCLUDED.expires_at,payload_json=EXCLUDED.payload_json,"
                "has_next=EXCLUDED.has_next,next_key=EXCLUDED.next_key",
                (cache_key, api_id, expires_at, json.dumps(value.payload, ensure_ascii=False), value.has_next, value.next_key),
            )
            cursor.execute("DELETE FROM central_api_query_cache WHERE expires_at<=%s", (time(),))

    def close(self) -> None:
        return

    def storage_size_bytes(self) -> int | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT pg_database_size(current_database())")
            row = cursor.fetchone()
        return int(row[0]) if row else None

    def storage_breakdown(self) -> list[dict[str, object]]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT relname,COALESCE(n_live_tup,0)::bigint,"
                "pg_total_relation_size(relid)::bigint "
                "FROM pg_stat_user_tables WHERE schemaname=current_schema() "
                "AND relname LIKE 'central_%' ORDER BY relname"
            )
            stats = [(str(row[0]), int(row[1]), int(row[2])) for row in cursor.fetchall()]
            cursor.execute(
                "SELECT collection,COUNT(*)::bigint,COALESCE(SUM(pg_column_size(document_json)+"
                "octet_length(collection)+octet_length(owner)+octet_length(document_key)),0)::bigint "
                "FROM central_documents GROUP BY collection"
            )
            shared = [(str(row[0]), int(row[1]), int(row[2])) for row in cursor.fetchall()]
        return _storage_breakdown_rows(stats, shared)

    def save_realtime_snapshots(self, values: list[dict[str, Any]]) -> None:
        if not values:
            return
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO central_realtime_latest(event_type,item_key,received_at,event_json) "
                "VALUES(%s,%s,%s,%s) ON CONFLICT(event_type,item_key) DO UPDATE SET "
                "received_at=EXCLUDED.received_at,event_json=EXCLUDED.event_json",
                [(str(v["event_type"]), str(v["item_key"]), float(v["received_at"]),
                  json.dumps(v["event"], ensure_ascii=False)) for v in values],
            )

    def load_realtime_snapshots(self, codes: list[str]) -> list[dict[str, Any]]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT event_json FROM central_realtime_latest "
                "WHERE (item_key=ANY(%s) OR event_type='market_state') AND received_at>%s "
                "ORDER BY received_at", (codes, time() - 300),
            )
            rows = cursor.fetchall()
        return [row[0] if isinstance(row[0], dict) else json.loads(row[0]) for row in rows]

    def load_latest_market_caps(self, codes: list[str]) -> list[dict[str, Any]]:
        """Return the last persisted 0B market cap without a freshness cutoff."""
        if not codes:
            return []
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT item_key,received_at,event_json FROM central_realtime_latest "
                "WHERE event_type='trade' AND item_key=ANY(%s)",
                (codes,),
            )
            rows = cursor.fetchall()
        return _latest_market_cap_rows(rows)

    def save_minute_bars(
        self, values: list[dict[str, Any]], *,
        observations: list[tuple[str, MarketDataObservation[object]]] | None = None,
    ) -> None:
        if not values:
            return
        observation_by_key = dict(observations or ())
        with self._connect() as connection, connection.cursor() as cursor:
            for value in values:
                operation_id, operation_hash = _minute_operation(value)
                cursor.execute(
                    "SELECT operation_hash FROM central_minute_bar_operations WHERE operation_id=%s",
                    (operation_id,),
                )
                processed = cursor.fetchone()
                if processed is not None:
                    if str(processed[0]) != operation_hash:
                        raise ValueError("minute bar operation_id payload changed")
                    continue
                cursor.execute(
                    "INSERT INTO central_minute_bars VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT(trading_date,minute,code,market) DO UPDATE SET "
                    "high=GREATEST(central_minute_bars.high,EXCLUDED.high),"
                    "low=LEAST(central_minute_bars.low,EXCLUDED.low),close=EXCLUDED.close,"
                    "volume=central_minute_bars.volume+EXCLUDED.volume,"
                    "trade_value_million_won=central_minute_bars.trade_value_million_won+EXCLUDED.trade_value_million_won,"
                    "updated_at=EXCLUDED.updated_at",
                    bar_value_rows((value,), minute=True)[0],
                )
                key = _minute_key(value)
                observation = observation_by_key.get(key)
                if observation is not None:
                    merged = _load_postgres_minute_bar(cursor, value)
                    merged_observation = MarketDataObservation(
                        observation.kind, observation.subject, merged, observation.metadata,
                    )
                    cursor.execute(
                        _market_metadata_upsert_sql("%s", "EXCLUDED"),
                        market_metadata_storage_values(key, merged_observation),
                    )
                    if self._observation_history_enabled:
                        _append_postgres_observation_revision(
                            cursor, "minute_bar", observation.subject, key,
                            minute_bar_revision_payload(
                                merged, window_closed=False, capture_quality="in_progress",
                                finalization_source="realtime_flush", operation_id=operation_id,
                            ),
                            merged_observation,
                        )
                cursor.execute(
                    "INSERT INTO central_minute_bar_operations(operation_id,operation_hash,processed_at) "
                    "VALUES(%s,%s,%s)",
                    (operation_id, operation_hash, datetime.now(timezone.utc)),
                )

    def finalize_minute_bars(self, values: list[dict[str, Any]]) -> None:
        if not values:
            return
        with self._connect() as connection, connection.cursor() as cursor:
            for closure in values:
                operation_id, operation_hash = _minute_operation(closure, finalization=True)
                cursor.execute(
                    "SELECT operation_hash FROM central_minute_bar_operations WHERE operation_id=%s",
                    (operation_id,),
                )
                processed = cursor.fetchone()
                if processed is not None:
                    if str(processed[0]) != operation_hash:
                        raise ValueError("minute bar operation_id payload changed")
                    continue
                merged = _load_postgres_minute_bar(cursor, closure)
                if merged is not None:
                    _save_final_minute_revision_postgres(
                        cursor, merged, closure, self._observation_history_enabled,
                    )
                cursor.execute(
                    "INSERT INTO central_minute_bar_operations(operation_id,operation_hash,processed_at) "
                    "VALUES(%s,%s,%s)",
                    (operation_id, operation_hash, datetime.now(timezone.utc)),
                )

    def save_second_trade_bars(self, values: list[dict[str, Any]]) -> None:
        if not values:
            return
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO central_second_trade_bars VALUES("
                "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT(trading_date,trade_second,code,market) DO UPDATE SET "
                "open=EXCLUDED.open,high=EXCLUDED.high,low=EXCLUDED.low,close=EXCLUDED.close,"
                "volume=EXCLUDED.volume,trade_value_won=EXCLUDED.trade_value_won,"
                "trade_count=EXCLUDED.trade_count,available_at=EXCLUDED.available_at "
                "WHERE EXCLUDED.available_at>central_second_trade_bars.available_at OR "
                "(EXCLUDED.available_at=central_second_trade_bars.available_at AND "
                "EXCLUDED.trade_count>=central_second_trade_bars.trade_count)",
                second_trade_bar_value_rows(values),
            )

    def load_minute_bars(self, code: str, trading_date: str, market: str = "") -> list[dict[str, Any]]:
        sql = (
            "SELECT trading_date::text,to_char(minute,'HH24:MI'),code,market,open,high,low,close,volume,"
            "trade_value_million_won,updated_at FROM central_minute_bars WHERE code=%s AND trading_date=%s"
        )
        parameters: list[object] = [code, trading_date]
        if market:
            sql += " AND market=%s"
            parameters.append(market)
        sql += " ORDER BY minute"
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, parameters)
            rows = cursor.fetchall()
        return bar_result_rows(rows, minute=True)

    def replace_minute_bars(
        self, values: list[dict[str, Any]], *,
        observations: list[tuple[str, MarketDataObservation[object]]] | None = None,
    ) -> None:
        self._replace_bars(
            "central_minute_bars", values, minute=True, observations=observations
        )

    def replace_daily_bars(
        self, values: list[dict[str, Any]], *,
        observations: list[tuple[str, MarketDataObservation[object]]] | None = None,
    ) -> None:
        self._replace_bars(
            "central_daily_bars", values, minute=False, observations=observations
        )

    def _replace_bars(
        self, table: str, values: list[dict[str, Any]], *, minute: bool,
        observations: list[tuple[str, MarketDataObservation[object]]] | None = None,
    ) -> None:
        if not values:
            return
        columns = bar_columns(minute=minute)
        placeholders = ",".join("%s" for _ in columns)
        updates = ",".join(f"{column}=EXCLUDED.{column}" for column in columns if column not in BAR_KEY_COLUMNS)
        conflict = "trading_date,minute,code,market" if minute else "trading_date,code,market"
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.executemany(
                f"INSERT INTO {table}({','.join(columns)}) VALUES({placeholders}) "
                f"ON CONFLICT({conflict}) DO UPDATE SET {updates}",
                bar_value_rows(values, minute=minute),
            )
            _save_postgres_metadata(cursor, observations)
            if minute and observations and self._observation_history_enabled:
                for value, (key, observation) in zip(values, observations, strict=True):
                    _append_postgres_observation_revision(
                        cursor, "minute_bar", observation.subject, key,
                        minute_bar_revision_payload(
                            value,
                            window_closed=observation.metadata.completeness in {
                                DataCompleteness.COMPLETE, DataCompleteness.PARTIAL,
                            },
                            capture_quality=(
                                "complete" if observation.metadata.completeness == DataCompleteness.COMPLETE
                                else observation.metadata.completeness.value
                            ),
                            finalization_source="query_response",
                        ),
                        observation,
                    )

    def load_daily_bars(self, code: str, market: str = "", limit: int = 250) -> list[dict[str, Any]]:
        sql = ("SELECT trading_date::text,code,market,open,high,low,close,volume,trade_value_million_won,updated_at "
               "FROM central_daily_bars WHERE code=%s")
        parameters: list[object] = [code]
        if market:
            sql += " AND market=%s"
            parameters.append(market)
        sql += " ORDER BY trading_date DESC LIMIT %s"
        parameters.append(bounded_limit(limit, 5000))
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, parameters)
            rows = cursor.fetchall()
        return bar_result_rows(rows, minute=False)

    def save_dataset_snapshot(
        self, kind: str, subject: str, snapshot_key: str, payload: dict[str, Any],
        *, observation: MarketDataObservation[object] | None = None,
    ) -> None:
        self.save_dataset_snapshots([(kind, subject, snapshot_key, payload, observation)])

    def save_dataset_snapshots(self, values: list[DatasetSnapshotWrite]) -> None:
        if not values:
            return
        saved_at = time()
        started_at = monotonic()
        serialized = [
            (kind, subject, snapshot_key, json.dumps(payload, ensure_ascii=False), payload, observation)
            for kind, subject, snapshot_key, payload, observation in values
        ]
        serialized_at = monotonic()
        connection = self._connect()
        backend_pid = int(getattr(getattr(connection, "info", None), "backend_pid", 0) or 0)
        trace_top20 = backend_pid > 0 and any(value[0] == "top20_membership" for value in values)
        wait_stop = Event()
        wait_samples: list[tuple[str, str, tuple[int, ...]]] = []
        wait_thread = (
            Thread(
                target=_sample_postgres_backend_waits,
                args=(self._database_url, backend_pid, wait_stop, wait_samples),
                name="top20-postgres-wait-probe", daemon=True,
            )
            if trace_top20 else None
        )
        asynchronous_commit = _uses_async_dataset_commit(values)
        connected_at = monotonic()
        snapshot_at = connected_at
        metadata_at = connected_at
        revision_at = connected_at
        if wait_thread is not None:
            wait_thread.start()
        try:
            with connection.cursor() as cursor:
                if asynchronous_commit:
                    cursor.execute("SET LOCAL synchronous_commit TO OFF")
                for kind, subject, snapshot_key, payload_json, payload, observation in serialized:
                    cursor.execute(
                        "INSERT INTO central_dataset_snapshots(kind,subject,snapshot_key,saved_at,payload_json) "
                        "VALUES(%s,%s,%s,%s,%s) ON CONFLICT(kind,subject,snapshot_key) DO UPDATE SET "
                        "saved_at=EXCLUDED.saved_at,payload_json=EXCLUDED.payload_json",
                        (kind, subject, snapshot_key, saved_at, payload_json),
                    )
                    if observation is not None:
                        cursor.execute(
                            _market_metadata_upsert_sql("%s", "EXCLUDED"),
                            market_metadata_storage_values(snapshot_key, observation),
                        )
                        if self._observation_history_enabled and kind in RESEARCH_OBSERVATION_KINDS:
                            _append_postgres_observation_revision(
                                cursor, kind, subject, snapshot_key, payload, observation,
                            )
                snapshot_at = monotonic()
                metadata_at = snapshot_at
                revision_at = snapshot_at
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            wait_stop.set()
            if wait_thread is not None and wait_thread.is_alive():
                wait_thread.join(timeout=1.0)
            connection.close()
        completed_at = monotonic()
        elapsed_ms = round((completed_at - started_at) * 1000)
        if elapsed_ms >= 1000:
            kinds = ",".join(sorted({value[0] for value in values}))
            logger.warning(
                "slow PostgreSQL dataset snapshot batch save kinds=%s count=%d total_ms=%d "
                "serialize_ms=%d connect_ms=%d snapshot_ms=%d metadata_ms=%d revision_ms=%d commit_close_ms=%d",
                kinds, len(values), elapsed_ms,
                round((serialized_at - started_at) * 1000),
                round((connected_at - serialized_at) * 1000),
                round((snapshot_at - connected_at) * 1000),
                round((metadata_at - snapshot_at) * 1000),
                round((revision_at - metadata_at) * 1000),
                round((completed_at - revision_at) * 1000),
            )
            if trace_top20:
                logger.warning(
                    "PostgreSQL TOP20 blocked commit wait trace backend_pid=%d %s",
                    backend_pid, _postgres_wait_summary(wait_samples),
                )

    def load_dataset_snapshots(self, kind: str, subject: str = "", limit: int = 100) -> list[dict[str, Any]]:
        started_at = monotonic()
        sql = "SELECT subject,snapshot_key,saved_at,payload_json FROM central_dataset_snapshots WHERE kind=%s"
        parameters: list[object] = [kind]
        if subject:
            sql += " AND subject=%s"
            parameters.append(subject)
        sql += " ORDER BY snapshot_key DESC LIMIT %s"
        parameters.append(bounded_limit(limit, 5000))
        connection_started_at = monotonic()
        with self._connect() as connection, connection.cursor() as cursor:
            connected_at = monotonic()
            cursor.execute(sql, parameters)
            rows = cursor.fetchall()
            fetched_at = monotonic()
        completed_at = monotonic()
        elapsed_ms = round((completed_at - started_at) * 1000)
        if elapsed_ms >= 1000:
            logger.warning(
                "slow PostgreSQL dataset snapshot load kind=%s subject=%s limit=%d total_ms=%d "
                "prepare_ms=%d connect_ms=%d query_ms=%d commit_close_ms=%d",
                kind, subject, limit, elapsed_ms,
                round((connection_started_at - started_at) * 1000),
                round((connected_at - connection_started_at) * 1000),
                round((fetched_at - connected_at) * 1000),
                round((completed_at - fetched_at) * 1000),
            )
        return dataset_snapshot_result_rows(rows)

    def load_top20_statistics(self, start_date: str, end_date: str) -> dict[str, object]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT subject,snapshot_key,payload_json FROM central_dataset_snapshots "
                "WHERE kind='top20_index' AND subject>=%s AND subject<=%s ORDER BY snapshot_key",
                (start_date, end_date),
            )
            top20_rows = cursor.fetchall()
            start_raw, end_raw = start_date.replace("-", ""), end_date.replace("-", "")
            cursor.execute(
                "SELECT subject,payload_json FROM central_dataset_snapshots "
                "WHERE kind='market_index_chart' AND subject>=%s AND subject<%s ORDER BY subject",
                (start_raw, end_raw + "~"),
            )
            market_rows = cursor.fetchall()
        return _top20_statistics_document(top20_rows, market_rows)

    def load_observation_revisions(
        self, kind: str, subject: str = "", limit: int = 100,
    ) -> list[dict[str, Any]]:
        sql = _observation_revision_select("%s", postgres=True)
        parameters: list[object] = [kind]
        if subject:
            sql += " AND subject=%s"
            parameters.append(subject)
        sql += " ORDER BY accepted_sequence DESC LIMIT %s"
        parameters.append(bounded_limit(limit, 5000))
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, parameters)
            rows = cursor.fetchall()
        return observation_revision_result_rows(rows)

    def load_observation_revisions_after(
        self, after_sequence: int, kinds: tuple[str, ...], limit: int = 1000,
    ) -> list[dict[str, Any]]:
        normalized = tuple(dict.fromkeys(str(value) for value in kinds if str(value)))
        if not normalized:
            return []
        placeholders = ",".join("%s" for _ in normalized)
        sql = (
            f"SELECT {_observation_revision_columns(postgres=True)} "
            "FROM central_observation_revisions WHERE accepted_sequence>%s "
            f"AND kind IN ({placeholders}) ORDER BY accepted_sequence LIMIT %s"
        )
        parameters = [max(0, int(after_sequence)), *normalized, bounded_limit(limit, 5000)]
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, parameters)
            rows = cursor.fetchall()
        return observation_revision_result_rows(rows)

    def load_shadow_monitor_state(self, monitor_id: str) -> dict[str, Any] | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT document_json FROM central_shadow_monitor_state WHERE monitor_id=%s",
                (monitor_id,),
            )
            row = cursor.fetchone()
        return json_mapping(row[0]) if row else None

    def save_shadow_monitor_state(self, monitor_id: str, document: dict[str, Any]) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO central_shadow_monitor_state VALUES(%s,%s,%s) "
                "ON CONFLICT(monitor_id) DO UPDATE SET updated_at=EXCLUDED.updated_at,document_json=EXCLUDED.document_json",
                (monitor_id, datetime.now(timezone.utc), json.dumps(document, ensure_ascii=False)),
            )

    def save_shadow_evaluation(
        self, monitor_id: str, decision: dict[str, Any],
        candidate: dict[str, Any] | None, expires_at: str = "",
    ) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            _save_postgres_shadow_evaluation(cursor, monitor_id, decision, candidate, expires_at)

    def load_shadow_candidates(self, after_sequence: int = 0, limit: int = 100) -> dict[str, Any]:
        page_limit = bounded_limit(limit, 1000)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT COALESCE(MAX(accepted_sequence),0) FROM central_shadow_candidate_events")
            watermark_row = cursor.fetchone()
            cursor.execute(
                "SELECT accepted_sequence,document_json,expires_at::text "
                "FROM central_shadow_candidate_events WHERE accepted_sequence>%s "
                "ORDER BY accepted_sequence LIMIT %s",
                (max(0, int(after_sequence)), page_limit),
            )
            rows = cursor.fetchall()
        return _shadow_candidate_page(rows, int(watermark_row[0]) if watermark_row else 0)

    def create_observation_export(
        self, start: datetime, end: datetime, kinds: tuple[str, ...], subject: str = "",
    ) -> dict[str, Any]:
        normalized_start, normalized_end, normalized_kinds = _observation_export_inputs(
            start, end, kinds,
        )
        placeholders = ",".join("%s" for _ in normalized_kinds)
        sql = (
            "SELECT revision_id,source_id,available_at FROM central_observation_revisions "
            f"WHERE kind IN ({placeholders}) AND available_at>=%s AND available_at<%s"
        )
        parameters: list[object] = [*normalized_kinds, normalized_start, normalized_end]
        if subject:
            sql += " AND subject=%s"
            parameters.append(subject)
        sql += " ORDER BY available_at,accepted_sequence,revision_id"
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, parameters)
            selected = cursor.fetchall()
            manifest = _observation_export_manifest(
                normalized_start, normalized_end, normalized_kinds, subject, selected,
            )
            cursor.execute(
                "INSERT INTO central_research_exports VALUES(" + ",".join(("%s",) * 9) + ")",
                _postgres_export_values(manifest),
            )
            cursor.executemany(
                "INSERT INTO central_research_export_members VALUES(%s,%s,%s)",
                [(manifest["dataset_id"], ordinal, str(row[0]))
                 for ordinal, row in enumerate(selected, start=1)],
            )
        return manifest

    def load_observation_export_page(
        self, watermark: str, cursor: int = 0, limit: int = 1000,
    ) -> dict[str, Any]:
        page_limit = bounded_limit(limit, 1000)
        with self._connect() as connection, connection.cursor() as db_cursor:
            db_cursor.execute(
                "SELECT manifest_json FROM central_research_exports WHERE dataset_id=%s", (watermark,),
            )
            manifest_row = db_cursor.fetchone()
            if manifest_row is None:
                raise ValueError("unknown research export watermark")
            db_cursor.execute(
                _observation_export_page_sql("%s", postgres=True),
                (watermark, max(0, int(cursor)), page_limit + 1),
            )
            rows = db_cursor.fetchall()
        return _observation_export_page(manifest_row[0], rows, page_limit)

    def save_market_data_metadata(
        self, observation_key: str, observation: MarketDataObservation[object]
    ) -> None:
        values = market_metadata_storage_values(observation_key, observation)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                _market_metadata_upsert_sql("%s", "EXCLUDED"),
                values,
            )

    def load_market_data_metadata(
        self, kind: MarketDatasetKind, subject: str, observation_key: str
    ) -> MarketDataMetadata | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT effective_at,available_at,venue,unit,value_kind,completeness,origin,source,"
                "candidate_universe FROM central_market_data_observation_meta "
                "WHERE dataset_kind=%s AND subject=%s AND observation_key=%s",
                (kind.value, subject, observation_key),
            )
            row = cursor.fetchone()
        return market_metadata_from_storage_row(row)

    def load_market_data_metadata_range(
        self, kind: MarketDatasetKind, subject: str, start: datetime, end: datetime,
    ) -> list[CoverageObservation]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT observation_key,effective_at,available_at,venue,unit,value_kind,"
                "completeness,origin,source,candidate_universe "
                "FROM central_market_data_observation_meta WHERE dataset_kind=%s AND subject=%s "
                "AND effective_at>=%s AND effective_at<%s ORDER BY effective_at",
                (kind.value, subject, start, end),
            )
            rows = cursor.fetchall()
        return [
            CoverageObservation(str(row[0]), _metadata_from_range_row(row))
            for row in rows
        ]

    def upsert_documents(self, collection: str, values: list[dict[str, Any]]) -> None:
        if not values:
            return
        now = time()
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
                "VALUES(%s,%s,%s,%s,%s) ON CONFLICT(collection,owner,document_key) DO UPDATE SET "
                "updated_at=EXCLUDED.updated_at,document_json=EXCLUDED.document_json "
                "WHERE central_documents.document_json IS DISTINCT FROM EXCLUDED.document_json",
                document_value_rows(collection, values, now),
            )
            if collection == "theme_metadata":
                _append_postgres_theme_snapshot(cursor, values, received_at=now)
            elif collection == "news_article":
                _append_postgres_news_articles(cursor, values, received_at=now)

    def replace_documents(self, collection: str, values: list[dict[str, Any]]) -> None:
        """컬렉션 전체를 한 트랜잭션에서 현재 스냅샷으로 교체한다."""
        now = time()
        rows = document_value_rows(collection, values, now)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM central_documents WHERE collection=%s", (collection,))
            if rows:
                cursor.executemany(
                    "INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
                    "VALUES(%s,%s,%s,%s,%s)", rows,
                )
            if collection == "theme_metadata":
                _append_postgres_theme_snapshot(cursor, values, received_at=now)

    def load_documents(
        self, collection: str, owner: str = "", limit: int = 1000, offset: int = 0,
        updated_after: float = 0.0,
    ) -> list[dict[str, Any]]:
        sql, parameters = document_select_query(
            collection, owner, limit, offset, updated_after, placeholder="%s",
        )
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, parameters)
            rows = cursor.fetchall()
        return document_result_rows(rows)

    def load_document(
        self, collection: str, owner: str, key: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT owner,document_key,updated_at,document_json "
                "FROM central_documents WHERE collection=%s AND owner=%s AND document_key=%s",
                (collection, owner, key),
            )
            row = cursor.fetchone()
        return document_result_rows((row,))[0] if row is not None else None

    def load_theme_snapshots(
        self, *, available_at: float | None = None, limit: int = 100,
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT snapshot_id,profile_id,content_hash,effective_at,received_at,available_at,"
            "origin_device,revision_of,document_json FROM central_theme_snapshots"
        )
        parameters: list[object] = []
        if available_at is not None:
            sql += " WHERE available_at<=%s"
            parameters.append(float(available_at))
        sql += " ORDER BY accepted_sequence DESC LIMIT %s"
        parameters.append(bounded_limit(limit, 1000))
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, parameters)
            rows = cursor.fetchall()
        return _theme_snapshot_result_rows(rows)

    def enqueue_news_ai_jobs(self, values: list[dict[str, Any]]) -> int:
        with self._connect() as connection, connection.cursor() as cursor:
            return _enqueue_postgres_news_ai_jobs(cursor, values)

    def claim_news_jobs(self, *, limit: int = 1, now: float | None = None,
                        priority_stock_code: str = "") -> list[dict[str, Any]]:
        claimed_at = float(now if now is not None else time())
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE central_news_jobs SET state='PENDING',updated_at=%s "
                "WHERE state='RUNNING' AND updated_at<%s", (claimed_at, claimed_at - 120.0),
            )
            priority = str(priority_stock_code or "").strip()
            cursor.execute(
                "SELECT job_key,article_revision_id,stock_code,target_id,stage,input_hash,"
                "processing_version,attempts,payload_json FROM central_news_jobs "
                "WHERE state='PENDING' AND next_retry_at<=%s "
                "ORDER BY CASE "
                "WHEN %s<>'' AND (stock_code=%s OR target_id=%s) AND stage='BODY' THEN 0 "
                "WHEN %s<>'' AND (stock_code=%s OR target_id=%s) THEN 1 "
                "WHEN stage='BODY' AND stock_code<>'GLOBAL' THEN 2 "
                "WHEN stage='BODY' THEN 3 WHEN stage='AI' THEN 4 ELSE 5 END,"
                "CASE WHEN stage='BODY' THEN -updated_at ELSE updated_at END "
                "FOR UPDATE SKIP LOCKED LIMIT %s",
                (claimed_at, priority, priority, priority, priority, priority, priority,
                 bounded_limit(limit, 4)),
            )
            rows = cursor.fetchall()
            for row in rows:
                cursor.execute(
                    "UPDATE central_news_jobs SET state='RUNNING',attempts=attempts+1,updated_at=%s "
                    "WHERE job_key=%s", (claimed_at, row[0]),
                )
        return _news_job_rows(rows)

    def finish_news_job(self, job_key: str, output_ref: str) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE central_news_jobs SET state='COMPLETED',output_ref=%s,error='',updated_at=%s "
                "WHERE job_key=%s", (output_ref, time(), job_key),
            )

    def retry_news_job(self, job_key: str, error: str, next_retry_at: float,
                       output_ref: str = "") -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT attempts FROM central_news_jobs WHERE job_key=%s", (job_key,))
            row = cursor.fetchone()
            state = "FAILED" if row is not None and int(row[0]) >= 3 else "PENDING"
            cursor.execute(
                "UPDATE central_news_jobs SET state=%s,error=%s,next_retry_at=%s,output_ref=%s,"
                "updated_at=%s WHERE job_key=%s",
                (state, error[:1000], float(next_retry_at), output_ref, time(), job_key),
            )

    def save_news_body_revision(self, value: dict[str, Any]) -> str:
        with self._connect() as connection, connection.cursor() as cursor:
            return _save_postgres_news_body(cursor, value)

    def save_news_ai_results(self, documents: list[dict[str, Any]],
                             revisions: list[dict[str, Any]],
                             usage_documents: list[dict[str, Any]] | None = None) -> None:
        now = time()
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
                "VALUES(%s,%s,%s,%s,%s) ON CONFLICT(collection,owner,document_key) DO UPDATE SET "
                "updated_at=EXCLUDED.updated_at,document_json=EXCLUDED.document_json "
                "WHERE central_documents.document_json IS DISTINCT FROM EXCLUDED.document_json",
                document_value_rows("news_ai", documents, now),
            )
            _save_postgres_news_ai(cursor, revisions)
            if usage_documents:
                cursor.executemany(
                    "INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
                    "VALUES(%s,%s,%s,%s,%s) ON CONFLICT(collection,owner,document_key) DO UPDATE SET "
                    "updated_at=EXCLUDED.updated_at,document_json=EXCLUDED.document_json "
                    "WHERE central_documents.document_json IS DISTINCT FROM EXCLUDED.document_json",
                    document_value_rows("news_request_usage", usage_documents, now),
                )

    def load_news_history(self, kind: str, *, target: str = "", identity: str = "",
                          available_at: float | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as connection, connection.cursor() as cursor:
            return _load_postgres_news_history(
                cursor, kind, target, identity, available_at, limit,
            )

    def load_latest_news_body(self, article_revision_id: str) -> dict[str, Any] | None:
        values = self.load_news_history("body", target=article_revision_id, limit=1)
        return values[0] if values else None

    def load_news_body_revision(self, body_revision_id: str) -> dict[str, Any] | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT body_revision_id,article_revision_id,content_hash,extractor_version,fetched_at,"
                "available_at,status,body_text,error FROM central_news_body_revisions "
                "WHERE body_revision_id=%s", (body_revision_id,),
            )
            row = cursor.fetchone()
        return _decode_news_history("body", [row])[0] if row else None

    def load_news_article_revision(self, article_revision_id: str) -> dict[str, Any] | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT article_revision_id,stock_code,identity,content_hash,collector_id,published_at,"
                "received_at,available_at,collection_scope,revision_of,document_json "
                "FROM central_news_article_revisions WHERE article_revision_id=%s", (article_revision_id,),
            )
            row = cursor.fetchone()
            return _decode_news_history("article", [row])[0] if row else None

    def load_stock_news_articles(self, stock_code: str, *, limit: int = 1000) -> list[dict[str, Any]]:
        with self._connect() as connection, connection.cursor() as cursor:
            return _load_postgres_stock_news_articles(cursor, stock_code, limit)

    def load_confirmed_news_articles(self, stock_code: str, *, limit: int = 1000) -> list[dict[str, Any]]:
        with self._connect() as connection, connection.cursor() as cursor:
            return _load_postgres_confirmed_news_articles(cursor, stock_code, limit)

    def save_news_event_revision(self, value: dict[str, Any]) -> str:
        with self._connect() as connection, connection.cursor() as cursor:
            return _save_postgres_news_event(cursor, value)

    def claim_news_request(self, scope: str, *, scope_limit: int, hard_limit: int,
                           budget_date: str) -> bool:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("LOCK TABLE central_news_request_budget IN EXCLUSIVE MODE")
            cursor.execute(
                "SELECT COALESCE(SUM(request_count),0) FROM central_news_request_budget WHERE budget_date=%s",
                (budget_date,),
            )
            total = int(cursor.fetchone()[0])
            cursor.execute(
                "SELECT request_count FROM central_news_request_budget WHERE budget_date=%s AND scope=%s",
                (budget_date, scope),
            )
            row = cursor.fetchone()
            if total >= int(hard_limit) or (row and int(row[0]) >= int(scope_limit)):
                return False
            cursor.execute(
                "INSERT INTO central_news_request_budget(budget_date,scope,request_count,updated_at) "
                "VALUES(%s,%s,1,%s) ON CONFLICT(budget_date,scope) DO UPDATE SET "
                "request_count=central_news_request_budget.request_count+1,updated_at=EXCLUDED.updated_at",
                (budget_date, scope, time()),
            )
            return True

    def news_request_count(self, budget_date: str) -> int:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT COALESCE(SUM(request_count),0) FROM central_news_request_budget WHERE budget_date=%s",
                (budget_date,),
            )
            row = cursor.fetchone()
        return int(row[0]) if row else 0

    def load_news_source_cursor(self, source_id: str) -> dict[str, Any] | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT source_id,scope,query_text,cursor_published_at,cursor_identity,pending_published_at,"
                "pending_identity,next_start,next_schedule_at,checked_at,last_success,coverage,truncated,error,updated_at "
                "FROM central_news_source_cursors WHERE source_id=%s", (source_id,),
            )
            row = cursor.fetchone()
        return _news_source_cursor(row) if row else None

    def save_news_source_page(self, value: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as connection, connection.cursor() as cursor:
            return _save_postgres_news_source_page(cursor, value)

    def load_news_source_diagnostics(self, *, source_id: str = "", days: int = 7,
                                     limit: int = 100) -> dict[str, Any]:
        with self._connect() as connection, connection.cursor() as cursor:
            return _load_postgres_news_source_diagnostics(cursor, source_id, days, limit)

    def find_news_ai_revision(
        self, *, target_id: str, article_revision_id: str, body_revision_id: str,
        provider: str, model: str, prompt_version: str, schema_version: str,
        input_hash: str,
    ) -> str | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT analysis_revision_id FROM central_news_ai_revisions WHERE target_id=%s "
                "AND article_revision_id=%s AND body_revision_id=%s AND provider=%s AND model=%s "
                "AND prompt_version=%s AND schema_version=%s AND input_hash=%s "
                "ORDER BY accepted_sequence DESC LIMIT 1",
                (target_id, article_revision_id, body_revision_id, provider, model,
                 prompt_version, schema_version, input_hash),
            )
            row = cursor.fetchone()
        return str(row[0]) if row else None

    def append_vi_events(self, values: list[dict[str, Any]]) -> int:
        inserted = 0
        with self._connect() as connection, connection.cursor() as cursor:
            for value in values:
                cursor.execute(
                    "INSERT INTO central_vi_event_revisions(event_id,event_key,stock_code,event_kind,vi_type,"
                    "effective_at,received_at,available_at,price,direction,trigger_count,exchange,source,document_json) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    _vi_event_values(value),
                )
                inserted += max(0, cursor.rowcount)
        return inserted

    def record_hot_cohort_revision(self, value: dict[str, Any],
                                   current: dict[str, Any] | None = None) -> bool:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO central_hot_cohort_revisions(revision_id,revision_key,stock_code,event_type,"
                "condition_name,condition_seq,session_id,effective_at,available_at,document_json) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(revision_key) DO NOTHING",
                _cohort_revision_values(value),
            )
            inserted = cursor.rowcount > 0
            if current is not None:
                cursor.execute(
                    "INSERT INTO central_hot_cohort_current(stock_code,stock_name,condition_name,first_seen_at,"
                    "entry_session,last_signal,last_signal_at,active,nxt_eligible,expired_at,document_json) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(stock_code) DO UPDATE SET "
                    "stock_name=EXCLUDED.stock_name,condition_name=EXCLUDED.condition_name,"
                    "first_seen_at=EXCLUDED.first_seen_at,entry_session=EXCLUDED.entry_session,"
                    "last_signal=EXCLUDED.last_signal,last_signal_at=EXCLUDED.last_signal_at,active=EXCLUDED.active,"
                    "nxt_eligible=COALESCE(EXCLUDED.nxt_eligible,central_hot_cohort_current.nxt_eligible),"
                    "expired_at=EXCLUDED.expired_at,document_json=EXCLUDED.document_json",
                    _cohort_current_values(current),
                )
            return inserted

    def load_hot_cohort(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        sql = ("SELECT stock_code,stock_name,condition_name,first_seen_at,entry_session,last_signal,"
               "last_signal_at,active,nxt_eligible,expired_at,document_json FROM central_hot_cohort_current")
        if active_only:
            sql += " WHERE active=TRUE"
        sql += " ORDER BY first_seen_at,stock_code"
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql)
            rows = cursor.fetchall()
        return _cohort_current_rows(rows)

    def append_upper_limit_facts(self, values: list[dict[str, Any]]) -> int:
        inserted = 0
        with self._connect() as connection, connection.cursor() as cursor:
            for value in values:
                cursor.execute(
                    "INSERT INTO central_upper_limit_fact_revisions(fact_id,fact_key,stock_code,session_id,status,"
                    "upper_limit_price,current_price,high_price,effective_at,available_at,source,evidence,document_json) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(fact_key) DO NOTHING",
                    _upper_limit_values(value),
                )
                inserted += max(0, cursor.rowcount)
        return inserted

    def load_market_event_history(self, kind: str, *, code: str = "",
                                  limit: int = 100) -> list[dict[str, Any]]:
        table, code_column = _market_event_table(kind)
        sql = f"SELECT document_json FROM {table}"
        parameters: list[object] = []
        if code:
            sql += f" WHERE {code_column}=%s"
            parameters.append(code)
        sql += " ORDER BY accepted_sequence DESC LIMIT %s"
        parameters.append(bounded_limit(limit, 1000))
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, parameters)
            rows = cursor.fetchall()
        return [json_mapping(row[0]) for row in rows]

    def save_external_bars(self, values: list[dict[str, Any]]) -> None:
        if not values:
            return
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO central_external_bars VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT(provider,instrument,contract,timeframe,bar_time) DO UPDATE SET "
                "open=EXCLUDED.open,high=EXCLUDED.high,low=EXCLUDED.low,close=EXCLUDED.close,"
                "volume=EXCLUDED.volume,updated_at=EXCLUDED.updated_at",
                [_external_bar_values(value) for value in values],
            )

    def load_external_bars(self, instrument: str, timeframe: str, limit: int = 1000) -> list[dict[str, Any]]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT provider,instrument,contract,timeframe,bar_time::text,open,high,low,close,volume,updated_at "
                "FROM central_external_bars WHERE instrument=%s AND timeframe=%s "
                "ORDER BY bar_time DESC LIMIT %s", (instrument, timeframe, bounded_limit(limit, 10000)),
            )
            rows = cursor.fetchall()
        return [_external_bar_result(row) for row in reversed(rows)]

    def create_execution_intent(self, value: dict[str, Any], *, ownership: dict[str, str] | None = None) -> bool:
        with self._connect() as connection, connection.cursor() as cursor:
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
        with self._connect() as connection, connection.cursor() as cursor:
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
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT document_json FROM central_execution_intents WHERE intent_id=%s", (intent_id,),
            )
            row = cursor.fetchone()
        return _json_document(row[0]) if row else None

    def find_execution_intent_by_broker_order_id(
        self, environment: str, account_ref: str, run_id: str, broker_order_id: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection, connection.cursor() as cursor:
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
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT document_json FROM central_execution_events WHERE intent_id=%s ORDER BY accepted_sequence",
                (intent_id,),
            )
            rows = cursor.fetchall()
        return [_json_document(row[0]) for row in rows]

    def load_account_execution_events(
        self, environment: str, account_ref: str, after_sequence: int, limit: int,
    ) -> list[dict[str, Any]]:
        with self._connect() as connection, connection.cursor() as cursor:
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
        with self._connect() as connection, connection.cursor() as cursor:
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
        with self._connect() as connection, connection.cursor() as cursor:
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
        with self._connect() as connection, connection.cursor() as cursor:
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
        account_ref = str(value.get("account_ref") or "")
        revision = int(value.get("control_revision") or 0)
        if not account_ref or revision != expected_revision + 1:
            raise ValueError("invalid mock automation control revision")
        with self._connect() as connection, connection.cursor() as cursor:
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
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO central_execution_runtime_leases(owner_key,owner_token,lease_expires_at,updated_at) "
                "VALUES(%s,%s,%s,%s) ON CONFLICT(owner_key) DO UPDATE SET owner_token=EXCLUDED.owner_token,"
                "lease_expires_at=EXCLUDED.lease_expires_at,updated_at=EXCLUDED.updated_at "
                "WHERE central_execution_runtime_leases.owner_token=EXCLUDED.owner_token OR "
                "central_execution_runtime_leases.lease_expires_at<=EXCLUDED.updated_at",
                (owner_key, owner_token, lease_expires_at, now),
            )
            return cursor.rowcount == 1

    def register_account_identity(self, value: dict[str, Any]) -> str:
        broker, environment = _account_registry_scope(value)
        fingerprint = str(value.get("identity_fingerprint", ""))
        if len(fingerprint) != 64:
            raise ValueError("account identity fingerprint is invalid")
        candidate = _canonical_account_ref(value.get("account_ref") or uuid.uuid4())
        with self._connect() as connection, connection.cursor() as cursor:
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
        with self._connect() as connection, connection.cursor() as cursor:
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
        with self._connect() as connection, connection.cursor() as cursor:
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
        with self._connect() as connection, connection.cursor() as cursor:
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


    def _connect(self):
        try:
            import psycopg
        except ImportError as error:
            raise RuntimeError("PostgreSQL 서버 의존성을 설치하세요: pip install -e .[server]") from error
        return psycopg.connect(self._database_url)


def _event_document(value: dict[str, Any]) -> str:
    document = dict(value.get("document", {})) if isinstance(value.get("document"), dict) else {}
    document.update({key: item for key, item in value.items() if key != "document"})
    return json.dumps(document, ensure_ascii=False, separators=(",", ":"))


def _json_document(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    decoded = json.loads(str(value))
    if not isinstance(decoded, dict):
        raise ValueError("stored execution document must be an object")
    return decoded


def _latest_market_cap_rows(
    rows: list[tuple[object, object, object]],
) -> list[dict[str, Any]]:
    """Decode only the durable market-cap field from latest 0B snapshots."""
    result: list[dict[str, Any]] = []
    for raw_code, raw_received_at, raw_event in rows:
        try:
            event = _json_document(raw_event)
            payload = event.get("payload")
            raw_market_cap = payload.get("market_cap_eok") if isinstance(payload, dict) else None
            market_cap = float(raw_market_cap)
            received_at = float(raw_received_at)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        code = str(raw_code).strip()
        if not code or market_cap <= 0:
            continue
        result.append({
            "code": code,
            "market_cap_eok": market_cap,
            "observed_at": datetime.fromtimestamp(
                received_at, timezone.utc,
            ).isoformat(),
        })
    return result


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


def _vi_event_values(value: dict[str, Any]) -> tuple[object, ...]:
    return (
        str(value["event_id"]), str(value["event_key"]), str(value["stock_code"]),
        str(value["event_kind"]), str(value["vi_type"]), value.get("effective_at"),
        float(value["received_at"]), float(value["available_at"]), value.get("price"),
        str(value.get("direction", "")), value.get("trigger_count"),
        str(value.get("exchange", "")), str(value.get("source", "")), _event_document(value),
    )


def _cohort_revision_values(value: dict[str, Any]) -> tuple[object, ...]:
    return (
        str(value["revision_id"]), str(value["revision_key"]), str(value.get("stock_code", "")),
        str(value["event_type"]), str(value.get("condition_name", "")),
        str(value.get("condition_seq", "")), str(value["session_id"]),
        float(value["effective_at"]), float(value["available_at"]), _event_document(value),
    )


def _cohort_current_values(value: dict[str, Any]) -> tuple[object, ...]:
    return (
        str(value["stock_code"]), str(value.get("stock_name", "")),
        str(value.get("condition_name", "")), float(value["first_seen_at"]),
        str(value["entry_session"]), str(value.get("last_signal", "")),
        float(value["last_signal_at"]), bool(value.get("active", True)),
        value.get("nxt_eligible"), value.get("expired_at"), _event_document(value),
    )


def _cohort_current_rows(rows: list[tuple[object, ...]]) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        document = json_mapping(row[10])
        document.update({
            "stock_code": str(row[0]), "stock_name": str(row[1]), "condition_name": str(row[2]),
            "first_seen_at": float(row[3]), "entry_session": str(row[4]), "last_signal": str(row[5]),
            "last_signal_at": float(row[6]), "active": bool(row[7]),
            "nxt_eligible": None if row[8] is None else bool(row[8]),
            "expired_at": None if row[9] is None else float(row[9]),
        })
        result.append(document)
    return result


def _upper_limit_values(value: dict[str, Any]) -> tuple[object, ...]:
    return (
        str(value["fact_id"]), str(value["fact_key"]), str(value["stock_code"]),
        str(value["session_id"]), str(value["status"]), value.get("upper_limit_price"),
        value.get("current_price"), value.get("high_price"), float(value["effective_at"]),
        float(value["available_at"]), str(value.get("source", "")),
        str(value.get("evidence", "")), _event_document(value),
    )


def _market_event_table(kind: str) -> tuple[str, str]:
    mapping = {
        "vi": ("central_vi_event_revisions", "stock_code"),
        "cohort": ("central_hot_cohort_revisions", "stock_code"),
        "upper_limit": ("central_upper_limit_fact_revisions", "stock_code"),
    }
    if kind not in mapping:
        raise ValueError(f"지원하지 않는 시장 이벤트 종류입니다: {kind}")
    return mapping[kind]


def create_query_store(
    database_url: str, *, observation_history_enabled: bool = True,
) -> QueryStore:
    if database_url.startswith("sqlite:///"):
        raw = unquote(database_url.removeprefix("sqlite:///"))
        # sqlite:///C:/...와 sqlite:///relative/path를 모두 지원한다.
        return SQLiteQueryStore(
            Path(raw), observation_history_enabled=observation_history_enabled,
        )
    parsed = urlsplit(database_url)
    if parsed.scheme in {"postgres", "postgresql"}:
        return PostgresQueryStore(
            database_url, observation_history_enabled=observation_history_enabled,
        )
    raise ValueError("중앙 DB 주소는 sqlite:/// 또는 postgresql:// 형식이어야 합니다.")


_STORAGE_CATEGORY_LABELS = {
    "news": "뉴스",
    "market": "주식 누적자료",
    "research": "연구·시뮬레이션",
    "account": "계좌·주문",
    "other": "설정·기타",
}


def _storage_category(name: str, *, collection: bool = False) -> str:
    normalized = str(name).strip().lower()
    if (
        normalized.startswith("news_")
        if collection
        else normalized.startswith("central_news_")
    ):
        return "news"
    if collection and normalized.startswith(("journal_", "execution_")):
        return "account"
    if collection and normalized.startswith(("research_", "shadow_")):
        return "research"
    if collection and normalized in {
        "stock_fundamentals", "stock_nxt_eligibility", "historical_highs",
        "top20_daily_entrants", "candidate_flow_capture", "candidate_flow_finalization",
        "market_data_coverage", "market_data_coverage_daily", "market_data_coverage_intraday",
        "market_index_chart_coverage", "condition_search_status", "market_event_sessions",
    }:
        return "market"
    if not collection and normalized.startswith(("central_account_", "central_execution_")):
        return "account"
    if not collection and normalized.startswith(("central_research_", "central_shadow_")):
        return "research"
    if not collection and normalized in {
        "central_second_trade_bars", "central_minute_bars", "central_daily_bars",
        "central_dataset_snapshots", "central_realtime_latest", "central_external_bars",
        "central_market_data_observation_meta", "central_observation_revisions",
        "central_minute_bar_operations", "central_hot_cohort_current",
        "central_hot_cohort_revisions", "central_upper_limit_fact_revisions",
        "central_vi_event_revisions",
    }:
        return "market"
    return "other"


def _storage_breakdown_rows(
    table_stats: list[tuple[str, int, int]],
    shared_documents: list[tuple[object, object, object]],
) -> list[dict[str, object]]:
    totals = {
        key: {"category": key, "label": label, "estimated_bytes": 0, "rows": 0, "tables": 0}
        for key, label in _STORAGE_CATEGORY_LABELS.items()
    }
    shared_table_bytes = 0
    for table, rows, size in table_stats:
        if table == "central_documents":
            shared_table_bytes = max(0, int(size))
            continue
        category = _storage_category(table)
        totals[category]["estimated_bytes"] += max(0, int(size))
        totals[category]["rows"] += max(0, int(rows))
        totals[category]["tables"] += 1

    documents = []
    logical_total = 0
    for collection, rows, logical_bytes in shared_documents:
        category = _storage_category(str(collection), collection=True)
        count = max(0, int(rows))
        amount = max(0, int(logical_bytes))
        documents.append((category, count, amount))
        logical_total += amount
    allocated = 0
    document_categories: set[str] = set()
    for index, (category, count, amount) in enumerate(documents):
        document_categories.add(category)
        if shared_table_bytes and logical_total:
            share = (
                shared_table_bytes - allocated
                if index == len(documents) - 1
                else round(shared_table_bytes * amount / logical_total)
            )
            allocated += share
            totals[category]["estimated_bytes"] += max(0, share)
        totals[category]["rows"] += count
    if shared_table_bytes and not logical_total:
        totals["other"]["estimated_bytes"] += shared_table_bytes
    for category in document_categories:
        totals[category]["tables"] += 1
    return [totals[key] for key in ("news", "market", "research", "account", "other")]


def _external_bar_values(value: dict[str, Any]) -> tuple[object, ...]:
    return (
        str(value["provider"]), str(value["instrument"]), str(value["contract"]),
        str(value["timeframe"]), str(value["bar_time"]), value.get("open"), value.get("high"),
        value.get("low"), value.get("close"), value.get("volume"), float(value["updated_at"]),
    )


def _external_bar_result(row: tuple[object, ...]) -> dict[str, Any]:
    keys = ("provider", "instrument", "contract", "timeframe", "bar_time", "open", "high", "low", "close", "volume", "updated_at")
    return dict(zip(keys, row, strict=True))


def _theme_snapshot_source(values: list[dict[str, Any]]) -> ThemeSnapshotSource | None:
    full = next(
        (
            value for value in reversed(values)
            if str(value.get("owner", "")) == "default"
            and str(value.get("key", "")) == "full"
        ),
        None,
    )
    if full is None:
        return None
    document = full.get("document")
    if not isinstance(document, dict):
        raise ValueError("테마 이력 원본 문서는 JSON 객체여야 합니다.")
    return ThemeSnapshotSource.from_document(
        document,
        effective_at=full.get("effective_at"),
        origin_device=full.get("origin_device"),
    )


def _append_sqlite_theme_snapshot(
    connection: sqlite3.Connection, values: list[dict[str, Any]], *, received_at: float,
) -> None:
    source = _theme_snapshot_source(values)
    if source is None:
        return
    previous = connection.execute(
        "SELECT snapshot_id,content_hash FROM central_theme_snapshots "
        "ORDER BY accepted_sequence DESC LIMIT 1"
    ).fetchone()
    if previous is not None and str(previous[1]) == source.content_hash:
        return
    connection.execute(
        "INSERT INTO central_theme_snapshots("
        "snapshot_id,profile_id,content_hash,effective_at,received_at,available_at,"
        "origin_device,revision_of,document_json) VALUES(?,?,?,?,?,?,?,?,?)",
        (
            uuid.uuid4().hex, source.profile_id, source.content_hash, source.effective_at,
            received_at, time(), source.origin_device,
            str(previous[0]) if previous is not None else None,
            json.dumps(source.document, ensure_ascii=False, separators=(",", ":")),
        ),
    )


def _append_postgres_theme_snapshot(
    cursor: Any, values: list[dict[str, Any]], *, received_at: float,
) -> None:
    source = _theme_snapshot_source(values)
    if source is None:
        return
    # 동시에 도착한 여러 PC의 같은 문서도 직전 hash를 한 순서로 비교한다.
    cursor.execute("LOCK TABLE central_theme_snapshots IN SHARE ROW EXCLUSIVE MODE")
    cursor.execute(
        "SELECT snapshot_id,content_hash FROM central_theme_snapshots "
        "ORDER BY accepted_sequence DESC LIMIT 1"
    )
    previous = cursor.fetchone()
    if previous is not None and str(previous[1]) == source.content_hash:
        return
    cursor.execute(
        "INSERT INTO central_theme_snapshots("
        "snapshot_id,profile_id,content_hash,effective_at,received_at,available_at,"
        "origin_device,revision_of,document_json) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            uuid.uuid4().hex, source.profile_id, source.content_hash, source.effective_at,
            received_at, time(), source.origin_device,
            str(previous[0]) if previous is not None else None,
            json.dumps(source.document, ensure_ascii=False, separators=(",", ":")),
        ),
    )


def _theme_snapshot_result_rows(rows: list[tuple[object, ...]]) -> list[dict[str, Any]]:
    keys = (
        "snapshot_id", "profile_id", "content_hash", "effective_at", "received_at",
        "available_at", "origin_device", "revision_of",
    )
    return [
        {
            **dict(zip(keys, row[:8], strict=True)),
            "document": row[8] if isinstance(row[8], dict) else json.loads(str(row[8])),
        }
        for row in rows
    ]


def _article_source(value: dict[str, Any]) -> tuple[str, str, dict[str, Any], str, str]:
    document = value.get("document")
    if not isinstance(document, dict):
        raise ValueError("뉴스 기사 문서는 JSON 객체여야 합니다.")
    stock_code = str(document.get("stock_code") or value.get("owner") or "").strip()
    identity = str(document.get("identity") or value.get("key") or "").strip()
    if not stock_code or not identity:
        raise ValueError("뉴스 기사에는 종목코드와 identity가 필요합니다.")
    return (
        stock_code, identity, dict(document),
        str(value.get("collector_id") or "unknown").strip() or "unknown",
        str(value.get("collection_scope") or "watchlist").strip() or "watchlist",
    )


def _append_sqlite_news_articles(
    connection: sqlite3.Connection, values: list[dict[str, Any]], *, received_at: float,
) -> None:
    for value in values:
        stock, identity, document, collector, scope = _article_source(value)
        content_hash = stable_document_hash(document)
        previous = connection.execute(
            "SELECT article_revision_id,content_hash FROM central_news_article_revisions "
            "WHERE stock_code=? AND identity=? ORDER BY accepted_sequence DESC LIMIT 1",
            (stock, identity),
        ).fetchone()
        same_source = connection.execute(
            "SELECT 1 FROM central_news_article_revisions WHERE stock_code=? AND identity=? "
            "AND collector_id=? AND content_hash=? LIMIT 1",
            (stock, identity, collector, content_hash),
        ).fetchone()
        if same_source is not None:
            continue
        revision_id, available_at = uuid.uuid4().hex, time()
        connection.execute(
            "INSERT INTO central_news_article_revisions("
            "article_revision_id,stock_code,identity,content_hash,collector_id,published_at,"
            "received_at,available_at,collection_scope,revision_of,document_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (revision_id, stock, identity, content_hash, collector, document.get("published_at"),
             received_at, available_at, scope, str(previous[0]) if previous else None,
             json.dumps(document, ensure_ascii=False, separators=(",", ":"))),
        )
        _insert_sqlite_news_job(
            connection, revision_id, stock, stock, "BODY", content_hash,
            ARTICLE_BODY_EXTRACTOR_VERSION, document, available_at,
        )


def _append_postgres_news_articles(cursor: Any, values: list[dict[str, Any]], *, received_at: float) -> None:
    cursor.execute("LOCK TABLE central_news_article_revisions IN SHARE ROW EXCLUSIVE MODE")
    for value in values:
        stock, identity, document, collector, scope = _article_source(value)
        content_hash = stable_document_hash(document)
        cursor.execute(
            "SELECT article_revision_id,content_hash FROM central_news_article_revisions "
            "WHERE stock_code=%s AND identity=%s ORDER BY accepted_sequence DESC LIMIT 1",
            (stock, identity),
        )
        previous = cursor.fetchone()
        cursor.execute(
            "SELECT 1 FROM central_news_article_revisions WHERE stock_code=%s AND identity=%s "
            "AND collector_id=%s AND content_hash=%s LIMIT 1",
            (stock, identity, collector, content_hash),
        )
        if cursor.fetchone() is not None:
            continue
        revision_id, available_at = uuid.uuid4().hex, time()
        cursor.execute(
            "INSERT INTO central_news_article_revisions("
            "article_revision_id,stock_code,identity,content_hash,collector_id,published_at,"
            "received_at,available_at,collection_scope,revision_of,document_json) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (revision_id, stock, identity, content_hash, collector, document.get("published_at"),
             received_at, available_at, scope, str(previous[0]) if previous else None,
             json.dumps(document, ensure_ascii=False, separators=(",", ":"))),
        )
        _insert_postgres_news_job(
            cursor, revision_id, stock, stock, "BODY", content_hash,
            ARTICLE_BODY_EXTRACTOR_VERSION, document, available_at,
        )


def _insert_sqlite_news_job(connection: sqlite3.Connection, article_revision_id: str,
                            stock_code: str, target_id: str, stage: str, input_hash: str,
                            version: str, payload: dict[str, Any], now: float,
                            input_revision: str | None = None) -> None:
    key = news_job_key(stage, target_id, input_revision or article_revision_id, input_hash, version)
    connection.execute(
        "INSERT INTO central_news_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(job_key) DO NOTHING",
        (key, article_revision_id, stock_code, target_id, stage, input_hash, version, 0, now,
         "PENDING", "", "", json.dumps(payload, ensure_ascii=False, separators=(",", ":")), now),
    )


def _insert_postgres_news_job(cursor: Any, article_revision_id: str, stock_code: str,
                              target_id: str, stage: str, input_hash: str, version: str,
                              payload: dict[str, Any], now: float,
                              input_revision: str | None = None) -> None:
    key = news_job_key(stage, target_id, input_revision or article_revision_id, input_hash, version)
    cursor.execute(
        "INSERT INTO central_news_jobs VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT(job_key) DO NOTHING",
        (key, article_revision_id, stock_code, target_id, stage, input_hash, version, 0, now,
         "PENDING", "", "", json.dumps(payload, ensure_ascii=False, separators=(",", ":")), now),
    )


def _enqueue_sqlite_news_ai_jobs(connection: sqlite3.Connection, values: list[dict[str, Any]]) -> int:
    count = 0
    for value in values:
        stock, identity = str(value["stock_code"]), str(value["identity"])
        row = connection.execute(
            "SELECT article_revision_id,content_hash FROM central_news_article_revisions "
            "WHERE stock_code=? AND identity=? ORDER BY accepted_sequence DESC LIMIT 1", (stock, identity),
        ).fetchone()
        if row is None:
            continue
        body = connection.execute(
            "SELECT body_revision_id,content_hash FROM central_news_body_revisions "
            "WHERE article_revision_id=? AND status IN ('fulltext','summary_only') "
            "ORDER BY accepted_sequence DESC LIMIT 1", (str(row[0]),),
        ).fetchone()
        if body is None:
            continue
        payload = dict(value["payload"])
        payload["body_revision_id"] = str(body[0])
        before = connection.total_changes
        _insert_sqlite_news_job(
            connection, str(row[0]), stock, str(value.get("target_id") or stock), "AI",
            str(body[1]), str(value["processing_version"]), payload, time(), str(body[0]),
        )
        count += int(connection.total_changes > before)
    return count


def _enqueue_postgres_news_ai_jobs(cursor: Any, values: list[dict[str, Any]]) -> int:
    count = 0
    for value in values:
        stock, identity = str(value["stock_code"]), str(value["identity"])
        cursor.execute(
            "SELECT article_revision_id,content_hash FROM central_news_article_revisions "
            "WHERE stock_code=%s AND identity=%s ORDER BY accepted_sequence DESC LIMIT 1", (stock, identity),
        )
        row = cursor.fetchone()
        if row is None:
            continue
        cursor.execute(
            "SELECT body_revision_id,content_hash FROM central_news_body_revisions "
            "WHERE article_revision_id=%s AND status IN ('fulltext','summary_only') "
            "ORDER BY accepted_sequence DESC LIMIT 1", (str(row[0]),),
        )
        body = cursor.fetchone()
        if body is None:
            continue
        payload = dict(value["payload"])
        payload["body_revision_id"] = str(body[0])
        key = news_job_key("AI", str(value.get("target_id") or stock), str(body[0]), str(body[1]),
                           str(value["processing_version"]))
        cursor.execute(
            "INSERT INTO central_news_jobs VALUES(%s,%s,%s,%s,'AI',%s,%s,0,%s,'PENDING','','',%s,%s) "
            "ON CONFLICT(job_key) DO NOTHING RETURNING job_key",
            (key, str(row[0]), stock, str(value.get("target_id") or stock), str(body[1]),
             str(value["processing_version"]), time(),
             json.dumps(payload, ensure_ascii=False, separators=(",", ":")), time()),
        )
        count += int(cursor.fetchone() is not None)
    return count


def _news_job_rows(rows: list[tuple[object, ...]]) -> list[dict[str, Any]]:
    keys = ("job_key", "article_revision_id", "stock_code", "target_id", "stage",
            "input_hash", "processing_version", "attempts")
    return [{**dict(zip(keys, row[:8], strict=True)), "attempts": int(row[7]) + 1,
             "payload": row[8] if isinstance(row[8], dict) else json.loads(str(row[8]))} for row in rows]


def _save_sqlite_news_body(connection: sqlite3.Connection, value: dict[str, Any]) -> str:
    revision_id = uuid.uuid4().hex
    body = str(value.get("body_text") or "")
    content_hash = stable_document_hash({"body": body})
    article_revision_id = str(value["article_revision_id"])
    connection.execute(
        "INSERT INTO central_news_body_revisions(body_revision_id,article_revision_id,content_hash,"
        "extractor_version,fetched_at,available_at,status,body_text,error) VALUES(?,?,?,?,?,?,?,?,?)",
        (revision_id, article_revision_id, content_hash,
         str(value.get("extractor_version") or ARTICLE_BODY_EXTRACTOR_VERSION),
         float(value.get("fetched_at") or time()), time(), str(value["status"]), body,
         str(value.get("error") or "")[:1000]),
    )
    article = connection.execute(
        "SELECT stock_code,document_json FROM central_news_article_revisions WHERE article_revision_id=?",
        (article_revision_id,),
    ).fetchone()
    if article is None:
        raise ValueError("규칙 작업을 예약할 기사 리비전이 없습니다.")
    stock = str(article[0])
    document = article[1] if isinstance(article[1], dict) else json.loads(str(article[1]))
    targets = [(stock, str(document.get("stock_name") or stock))]
    if stock == "GLOBAL":
        targets = [(str(row[0]), str(row[1])) for row in connection.execute(
            "SELECT stock_code,stock_name FROM central_news_article_target_revisions "
            "WHERE article_revision_id=? AND relation_status='confirmed' ORDER BY accepted_sequence", (article_revision_id,),
        ).fetchall()]
    for target_code, target_name in targets:
        _insert_sqlite_news_job(
            connection, article_revision_id, stock, target_code, "RULE", content_hash,
            SUPPLY_CONTRACT_RULE_VERSION,
            {"body_revision_id": revision_id, "stock_code": target_code, "stock_name": target_name},
            time(), revision_id,
        )
    return revision_id


def _save_postgres_news_body(cursor: Any, value: dict[str, Any]) -> str:
    revision_id = uuid.uuid4().hex
    body = str(value.get("body_text") or "")
    content_hash = stable_document_hash({"body": body})
    article_revision_id = str(value["article_revision_id"])
    cursor.execute(
        "INSERT INTO central_news_body_revisions(body_revision_id,article_revision_id,content_hash,"
        "extractor_version,fetched_at,available_at,status,body_text,error) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (revision_id, article_revision_id, content_hash,
         str(value.get("extractor_version") or ARTICLE_BODY_EXTRACTOR_VERSION),
         float(value.get("fetched_at") or time()), time(), str(value["status"]), body,
         str(value.get("error") or "")[:1000]),
    )
    cursor.execute(
        "SELECT stock_code,document_json FROM central_news_article_revisions "
        "WHERE article_revision_id=%s FOR UPDATE",
        (article_revision_id,),
    )
    article = cursor.fetchone()
    if article is None:
        raise ValueError("규칙 작업을 예약할 기사 리비전이 없습니다.")
    stock = str(article[0])
    document = article[1] if isinstance(article[1], dict) else json.loads(str(article[1]))
    targets = [(stock, str(document.get("stock_name") or stock))]
    if stock == "GLOBAL":
        cursor.execute(
            "SELECT stock_code,stock_name FROM central_news_article_target_revisions "
            "WHERE article_revision_id=%s AND relation_status='confirmed' ORDER BY accepted_sequence",
            (article_revision_id,),
        )
        targets = [(str(row[0]), str(row[1])) for row in cursor.fetchall()]
    for target_code, target_name in targets:
        _insert_postgres_news_job(
            cursor, article_revision_id, stock, target_code, "RULE", content_hash,
            SUPPLY_CONTRACT_RULE_VERSION,
            {"body_revision_id": revision_id, "stock_code": target_code, "stock_name": target_name},
            time(), revision_id,
        )
    return revision_id


def _save_sqlite_news_ai(connection: sqlite3.Connection, values: list[dict[str, Any]]) -> None:
    for value in values:
        connection.execute(
            "INSERT INTO central_news_ai_revisions(analysis_revision_id,target_id,article_revision_id,"
            "body_revision_id,provider,model,prompt_version,schema_version,input_hash,computed_at,"
            "available_at,output_json,usage_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (str(value.get("analysis_revision_id") or uuid.uuid4().hex), str(value["target_id"]), str(value["article_revision_id"]),
             value.get("body_revision_id"), str(value["provider"]), str(value["model"]),
             str(value["prompt_version"]), str(value.get("schema_version") or NEWS_ANALYSIS_SCHEMA_VERSION),
             str(value["input_hash"]), float(value.get("computed_at") or time()), time(),
             json.dumps(value["output"], ensure_ascii=False, separators=(",", ":")),
             json.dumps(value.get("usage", {}), ensure_ascii=False, separators=(",", ":"))),
        )


def _save_postgres_news_ai(cursor: Any, values: list[dict[str, Any]]) -> None:
    for value in values:
        cursor.execute(
            "INSERT INTO central_news_ai_revisions(analysis_revision_id,target_id,article_revision_id,"
            "body_revision_id,provider,model,prompt_version,schema_version,input_hash,computed_at,"
            "available_at,output_json,usage_json) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (str(value.get("analysis_revision_id") or uuid.uuid4().hex), str(value["target_id"]), str(value["article_revision_id"]),
             value.get("body_revision_id"), str(value["provider"]), str(value["model"]),
             str(value["prompt_version"]), str(value.get("schema_version") or NEWS_ANALYSIS_SCHEMA_VERSION),
             str(value["input_hash"]), float(value.get("computed_at") or time()), time(),
             json.dumps(value["output"], ensure_ascii=False, separators=(",", ":")),
             json.dumps(value.get("usage", {}), ensure_ascii=False, separators=(",", ":"))),
        )


def _save_sqlite_news_event(connection: sqlite3.Connection, value: dict[str, Any]) -> str:
    article_revision_id = str(value["article_revision_id"])
    body_revision_id = str(value["body_revision_id"])
    rule_version = str(value["rule_version"])
    input_hash = str(value["input_hash"])
    target_stock = str(value.get("stock_code") or "")
    existing = connection.execute(
        "SELECT event_revision_id FROM central_news_event_revisions WHERE article_revision_id=? "
        "AND body_revision_id=? AND rule_version=? AND input_hash=? AND (?='' OR stock_code=?)",
        (article_revision_id, body_revision_id, rule_version, input_hash, target_stock, target_stock),
    ).fetchone()
    if existing:
        return str(existing[0])
    article = connection.execute(
        "SELECT stock_code,identity FROM central_news_article_revisions WHERE article_revision_id=?",
        (article_revision_id,),
    ).fetchone()
    if article is None:
        raise ValueError("사건에 연결할 기사 리비전이 없습니다.")
    stock_code, identity = target_stock or str(article[0]), str(article[1])
    result = dict(value["result"])
    event_key = str(result.get("event_key") or "") or None
    prior = connection.execute(
        "SELECT m.event_id FROM central_news_event_membership_revisions m "
        "JOIN central_news_article_revisions a ON a.article_revision_id=m.article_revision_id "
        "JOIN central_news_event_revisions e ON e.event_revision_id=m.event_revision_id "
        "WHERE e.stock_code=? AND a.identity=? ORDER BY m.accepted_sequence DESC LIMIT 1",
        (stock_code, identity),
    ).fetchone()
    same_identity = prior is not None
    if prior is None and event_key:
        prior = connection.execute(
            "SELECT event_id FROM central_news_event_revisions WHERE stock_code=? AND event_key=? "
            "ORDER BY accepted_sequence DESC LIMIT 1", (stock_code, event_key),
        ).fetchone()
    event_id = str(prior[0]) if prior else uuid.uuid4().hex
    previous = connection.execute(
        "SELECT event_revision_id,certainty FROM central_news_event_revisions WHERE event_id=? "
        "ORDER BY accepted_sequence DESC LIMIT 1", (event_id,),
    ).fetchone()
    if previous and result.get("novelty") == "NEW":
        if not same_identity and str(previous[1]) == str(result.get("certainty")):
            result["novelty"], result["novelty_score"] = "REPUBLICATION", 10
        else:
            result["novelty"], result["novelty_score"] = "UPDATE", 60
    related = _sqlite_possible_related(connection, stock_code, event_id, value.get("candidate_identities", ()))
    result["possible_related_event_ids"] = related
    result["article_revision_id"], result["body_revision_id"] = article_revision_id, body_revision_id
    event_revision_id, available_at = uuid.uuid4().hex, time()
    connection.execute(
        "INSERT INTO central_news_event_revisions(event_revision_id,event_id,event_key,stock_code,event_type,"
        "article_revision_id,body_revision_id,rule_version,input_hash,role,scope,certainty,novelty,amount_won,"
        "counterparty,importance_score,confidence_score,novelty_score,ai_required,available_at,revision_of,result_json) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (event_revision_id, event_id, event_key, stock_code, str(result["event_type"]), article_revision_id,
         body_revision_id, rule_version, input_hash, str(result["role"]), str(result["scope"]),
         str(result["certainty"]), str(result["novelty"]), result.get("amount_won"),
         result.get("counterparty"), int(result["importance_score"]), int(result["confidence_score"]),
         int(result["novelty_score"]), int(bool(result["ai_required"])), available_at,
         str(previous[0]) if previous else None,
         json.dumps(result, ensure_ascii=False, separators=(",", ":"))),
    )
    previous_membership = connection.execute(
        "SELECT membership_revision_id FROM central_news_event_membership_revisions WHERE event_id=? "
        "AND article_revision_id IN (SELECT article_revision_id FROM central_news_article_revisions "
        "WHERE identity=?) ORDER BY accepted_sequence DESC LIMIT 1",
        (event_id, identity),
    ).fetchone()
    membership = {"identity": identity, "relation": "EVIDENCE", "possible_related_event_ids": related}
    connection.execute(
        "INSERT INTO central_news_event_membership_revisions(membership_revision_id,event_id,event_revision_id,"
        "article_revision_id,body_revision_id,relation,available_at,revision_of,document_json) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (uuid.uuid4().hex, event_id, event_revision_id, article_revision_id, body_revision_id, "EVIDENCE",
         available_at, str(previous_membership[0]) if previous_membership else None,
         json.dumps(membership, ensure_ascii=False, separators=(",", ":"))),
    )
    return event_revision_id


def _save_postgres_news_event(cursor: Any, value: dict[str, Any]) -> str:
    cursor.execute("LOCK TABLE central_news_event_revisions IN SHARE ROW EXCLUSIVE MODE")
    article_revision_id, body_revision_id = str(value["article_revision_id"]), str(value["body_revision_id"])
    rule_version, input_hash = str(value["rule_version"]), str(value["input_hash"])
    target_stock = str(value.get("stock_code") or "")
    cursor.execute(
        "SELECT event_revision_id FROM central_news_event_revisions WHERE article_revision_id=%s "
        "AND body_revision_id=%s AND rule_version=%s AND input_hash=%s AND (%s='' OR stock_code=%s)",
        (article_revision_id, body_revision_id, rule_version, input_hash, target_stock, target_stock),
    )
    existing = cursor.fetchone()
    if existing:
        return str(existing[0])
    cursor.execute(
        "SELECT stock_code,identity FROM central_news_article_revisions WHERE article_revision_id=%s",
        (article_revision_id,),
    )
    article = cursor.fetchone()
    if article is None:
        raise ValueError("사건에 연결할 기사 리비전이 없습니다.")
    stock_code, identity = target_stock or str(article[0]), str(article[1])
    result = dict(value["result"])
    event_key = str(result.get("event_key") or "") or None
    cursor.execute(
        "SELECT m.event_id FROM central_news_event_membership_revisions m "
        "JOIN central_news_article_revisions a ON a.article_revision_id=m.article_revision_id "
        "JOIN central_news_event_revisions e ON e.event_revision_id=m.event_revision_id "
        "WHERE e.stock_code=%s AND a.identity=%s ORDER BY m.accepted_sequence DESC LIMIT 1",
        (stock_code, identity),
    )
    prior = cursor.fetchone()
    same_identity = prior is not None
    if prior is None and event_key:
        cursor.execute(
            "SELECT event_id FROM central_news_event_revisions WHERE stock_code=%s AND event_key=%s "
            "ORDER BY accepted_sequence DESC LIMIT 1", (stock_code, event_key),
        )
        prior = cursor.fetchone()
    event_id = str(prior[0]) if prior else uuid.uuid4().hex
    cursor.execute(
        "SELECT event_revision_id,certainty FROM central_news_event_revisions WHERE event_id=%s "
        "ORDER BY accepted_sequence DESC LIMIT 1", (event_id,),
    )
    previous = cursor.fetchone()
    if previous and result.get("novelty") == "NEW":
        if not same_identity and str(previous[1]) == str(result.get("certainty")):
            result["novelty"], result["novelty_score"] = "REPUBLICATION", 10
        else:
            result["novelty"], result["novelty_score"] = "UPDATE", 60
    related = _postgres_possible_related(cursor, stock_code, event_id, value.get("candidate_identities", ()))
    result["possible_related_event_ids"] = related
    result["article_revision_id"], result["body_revision_id"] = article_revision_id, body_revision_id
    event_revision_id, available_at = uuid.uuid4().hex, time()
    cursor.execute(
        "INSERT INTO central_news_event_revisions(event_revision_id,event_id,event_key,stock_code,event_type,"
        "article_revision_id,body_revision_id,rule_version,input_hash,role,scope,certainty,novelty,amount_won,"
        "counterparty,importance_score,confidence_score,novelty_score,ai_required,available_at,revision_of,result_json) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (event_revision_id, event_id, event_key, stock_code, str(result["event_type"]), article_revision_id,
         body_revision_id, rule_version, input_hash, str(result["role"]), str(result["scope"]),
         str(result["certainty"]), str(result["novelty"]), result.get("amount_won"),
         result.get("counterparty"), int(result["importance_score"]), int(result["confidence_score"]),
         int(result["novelty_score"]), bool(result["ai_required"]), available_at,
         str(previous[0]) if previous else None, json.dumps(result, ensure_ascii=False, separators=(",", ":"))),
    )
    cursor.execute(
        "SELECT membership_revision_id FROM central_news_event_membership_revisions WHERE event_id=%s "
        "AND article_revision_id IN (SELECT article_revision_id FROM central_news_article_revisions "
        "WHERE identity=%s) ORDER BY accepted_sequence DESC LIMIT 1",
        (event_id, identity),
    )
    previous_membership = cursor.fetchone()
    membership = {"identity": identity, "relation": "EVIDENCE", "possible_related_event_ids": related}
    cursor.execute(
        "INSERT INTO central_news_event_membership_revisions(membership_revision_id,event_id,event_revision_id,"
        "article_revision_id,body_revision_id,relation,available_at,revision_of,document_json) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (uuid.uuid4().hex, event_id, event_revision_id, article_revision_id, body_revision_id, "EVIDENCE",
         available_at, str(previous_membership[0]) if previous_membership else None,
         json.dumps(membership, ensure_ascii=False, separators=(",", ":"))),
    )
    return event_revision_id


_NEWS_SOURCE_CURSOR_KEYS = (
    "source_id", "scope", "query_text", "cursor_published_at", "cursor_identity",
    "pending_published_at", "pending_identity", "next_start", "next_schedule_at",
    "checked_at", "last_success", "coverage", "truncated", "error", "updated_at",
)


def _news_source_cursor(row: tuple[object, ...]) -> dict[str, Any]:
    result = dict(zip(_NEWS_SOURCE_CURSOR_KEYS, row, strict=True))
    result["next_start"] = int(result["next_start"])
    result["truncated"] = bool(result["truncated"])
    return result


def _source_item(
    value: dict[str, Any],
) -> tuple[str, dict[str, Any], list[dict[str, Any]], bool]:
    identity = str(value.get("identity") or "").strip()
    document = value.get("document")
    if not identity or not isinstance(document, dict):
        raise ValueError("소스 관측에는 identity와 기사 문서가 필요합니다.")
    targets = value.get("targets")
    return (
        identity, dict(document), list(targets) if isinstance(targets, list) else [],
        bool(value.get("processing_excluded", False)),
    )


def _news_article_content_hash(document: dict[str, Any]) -> str:
    # 언론사명·도메인은 URL에서 다시 계산할 수 있는 검색 편의 필드다. 이 값의
    # 추가나 매핑 보정만으로 원문 기사 리비전을 새로 만들지 않는다.
    content = {
        key: value for key, value in document.items()
        if key not in {"publisher_domain", "publisher_name"}
    }
    return stable_document_hash(content)


def _save_sqlite_news_source_page(connection: sqlite3.Connection,
                                  value: dict[str, Any]) -> dict[str, Any]:
    now = time()
    source_id, query_text = str(value["source_id"]), str(value["query_text"])
    run_id, page_start = str(value.get("run_id") or uuid.uuid4().hex), int(value.get("page_start") or 1)
    new_count, duplicate_count = 0, 0
    for raw in value.get("items", []):
        identity, document, targets, processing_excluded = _source_item(raw)
        document.update({"identity": identity, "stock_code": "GLOBAL"})
        content_hash = _news_article_content_hash(document)
        source_latest = connection.execute(
            "SELECT article_revision_id,content_hash FROM central_news_source_observations "
            "WHERE source_id=? AND identity=? ORDER BY accepted_sequence DESC LIMIT 1",
            (source_id, identity),
        ).fetchone()
        latest = connection.execute(
            "SELECT article_revision_id,content_hash FROM central_news_article_revisions "
            "WHERE stock_code='GLOBAL' AND identity=? ORDER BY accepted_sequence DESC LIMIT 1",
            (identity,),
        ).fetchone()
        reusable = source_latest if source_latest and str(source_latest[1]) == content_hash else (
            latest if latest and str(latest[1]) == content_hash else None
        )
        if reusable:
            article_revision_id, duplicate = str(reusable[0]), True
            duplicate_count += 1
        else:
            article_revision_id, duplicate = uuid.uuid4().hex, False
            available_at = time()
            connection.execute(
                "INSERT INTO central_news_article_revisions(article_revision_id,stock_code,identity,content_hash,"
                "collector_id,published_at,received_at,available_at,collection_scope,revision_of,document_json) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (article_revision_id, "GLOBAL", identity, content_hash, "naver", document.get("published_at"),
                 now, available_at, "query_set", str(latest[0]) if latest else None,
                 json.dumps(document, ensure_ascii=False, separators=(",", ":"))),
            )
            new_count += 1
        if not processing_excluded:
            _insert_sqlite_news_job(
                connection, article_revision_id, "GLOBAL", "GLOBAL", "BODY", content_hash,
                ARTICLE_BODY_EXTRACTOR_VERSION, document, time(),
            )
        for target in targets:
            _insert_sqlite_article_target(connection, article_revision_id, identity, target)
            if not processing_excluded and str(target.get("relation_status") or "") == "confirmed":
                _enqueue_sqlite_existing_body_rule(connection, article_revision_id, target)
        connection.execute(
            "INSERT INTO central_news_source_observations(observation_id,run_id,source_id,query_text,page_start,"
            "article_revision_id,identity,published_at,received_at,available_at,content_hash,duplicate,document_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (uuid.uuid4().hex, run_id, source_id, query_text, page_start, article_revision_id, identity,
             document.get("published_at"), now, time(), content_hash, int(duplicate),
             json.dumps({"query_membership": query_text, "targets": targets,
                         "processing_excluded": processing_excluded, **document},
                        ensure_ascii=False, separators=(",", ":"))),
        )
    raw_count = len(value.get("items", []))
    completed = float(value.get("completed_at") or time())
    run_document = dict(value.get("document") or {})
    connection.execute(
        "INSERT INTO central_news_source_runs(run_revision_id,run_id,source_id,scope,page_start,checked_at,completed_at,"
        "raw_count,unique_count,duplicate_count,request_count,budget_remaining,truncated,coverage,error,document_json) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (uuid.uuid4().hex, run_id, source_id, str(value.get("scope") or "query_set"), page_start,
         float(value.get("checked_at") or now), completed, raw_count, new_count, duplicate_count,
         int(value.get("request_count") or 0), int(value.get("budget_remaining") or 0),
         int(bool(value.get("truncated"))), str(value.get("coverage") or "query_set"),
         str(value.get("error") or "")[:1000], json.dumps(run_document, ensure_ascii=False, separators=(",", ":"))),
    )
    _upsert_sqlite_source_cursor(connection, value, now)
    return {"raw_count": raw_count, "unique_count": new_count, "duplicate_count": duplicate_count}


def _insert_sqlite_article_target(connection: sqlite3.Connection, article_revision_id: str,
                                  identity: str, target: dict[str, Any]) -> bool:
    code = str(target.get("stock_code") or "") or None
    name = str(target.get("stock_name") or "") or None
    status = str(target.get("relation_status") or "unresolved")
    version = str(target.get("rule_version") or "exact-krx-company-name-v1")
    exists = connection.execute(
        "SELECT target_revision_id FROM central_news_article_target_revisions WHERE article_revision_id=? "
        "AND COALESCE(stock_code,'')=? AND COALESCE(stock_name,'')=? AND relation_status=? AND rule_version=?",
        (article_revision_id, code or "", name or "", status, version),
    ).fetchone()
    if exists:
        return False
    previous = connection.execute(
        "SELECT target_revision_id FROM central_news_article_target_revisions WHERE identity=? "
        "AND COALESCE(stock_code,'')=? ORDER BY accepted_sequence DESC LIMIT 1", (identity, code or ""),
    ).fetchone()
    document = dict(target)
    connection.execute(
        "INSERT INTO central_news_article_target_revisions(target_revision_id,article_revision_id,identity,stock_code,"
        "stock_name,relation_status,evidence_text,rule_version,available_at,revision_of,document_json) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (uuid.uuid4().hex, article_revision_id, identity, code, name, status,
         str(target.get("evidence_text") or ""), version, time(), str(previous[0]) if previous else None,
         json.dumps(document, ensure_ascii=False, separators=(",", ":"))),
    )
    return True


def _enqueue_sqlite_existing_body_rule(
    connection: sqlite3.Connection, article_revision_id: str, target: dict[str, Any],
) -> None:
    body = connection.execute(
        "SELECT body_revision_id,content_hash FROM central_news_body_revisions "
        "WHERE article_revision_id=? AND status IN ('fulltext','summary_only') "
        "ORDER BY accepted_sequence DESC LIMIT 1", (article_revision_id,),
    ).fetchone()
    target_code = str(target.get("stock_code") or "")
    if body is None or not target_code:
        return
    _insert_sqlite_news_job(
        connection, article_revision_id, "GLOBAL", target_code, "RULE", str(body[1]),
        SUPPLY_CONTRACT_RULE_VERSION,
        {"body_revision_id": str(body[0]), "stock_code": target_code,
         "stock_name": str(target.get("stock_name") or target_code)},
        time(), str(body[0]),
    )


def _upsert_sqlite_source_cursor(connection: sqlite3.Connection, value: dict[str, Any], now: float) -> None:
    connection.execute(
        "INSERT INTO central_news_source_cursors(source_id,scope,query_text,cursor_published_at,cursor_identity,"
        "pending_published_at,pending_identity,next_start,next_schedule_at,checked_at,last_success,coverage,truncated,error,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET scope=excluded.scope,"
        "query_text=excluded.query_text,cursor_published_at=excluded.cursor_published_at,cursor_identity=excluded.cursor_identity,"
        "pending_published_at=excluded.pending_published_at,pending_identity=excluded.pending_identity,next_start=excluded.next_start,"
        "next_schedule_at=excluded.next_schedule_at,checked_at=excluded.checked_at,last_success=excluded.last_success,"
        "coverage=excluded.coverage,truncated=excluded.truncated,error=excluded.error,updated_at=excluded.updated_at",
        (str(value["source_id"]), str(value.get("scope") or "query_set"), str(value["query_text"]),
         value.get("cursor_published_at"), str(value.get("cursor_identity") or ""),
         value.get("pending_published_at"), str(value.get("pending_identity") or ""),
         int(value.get("next_start") or 1), float(value.get("next_schedule_at") or now),
         float(value.get("checked_at") or now), value.get("last_success"),
         str(value.get("coverage") or "query_set"), int(bool(value.get("truncated"))),
         str(value.get("error") or "")[:1000], now),
    )


def _save_postgres_news_source_page(cursor: Any, value: dict[str, Any]) -> dict[str, Any]:
    now = time()
    source_id, query_text = str(value["source_id"]), str(value["query_text"])
    run_id, page_start = str(value.get("run_id") or uuid.uuid4().hex), int(value.get("page_start") or 1)
    new_count, duplicate_count = 0, 0
    cursor.execute("LOCK TABLE central_news_article_revisions IN SHARE ROW EXCLUSIVE MODE")
    for raw in value.get("items", []):
        identity, document, targets, processing_excluded = _source_item(raw)
        document.update({"identity": identity, "stock_code": "GLOBAL"})
        content_hash = _news_article_content_hash(document)
        cursor.execute(
            "SELECT article_revision_id,content_hash FROM central_news_source_observations "
            "WHERE source_id=%s AND identity=%s ORDER BY accepted_sequence DESC LIMIT 1",
            (source_id, identity),
        )
        source_latest = cursor.fetchone()
        cursor.execute(
            "SELECT article_revision_id,content_hash FROM central_news_article_revisions "
            "WHERE stock_code='GLOBAL' AND identity=%s ORDER BY accepted_sequence DESC LIMIT 1 FOR UPDATE", (identity,),
        )
        latest = cursor.fetchone()
        reusable = source_latest if source_latest and str(source_latest[1]) == content_hash else (
            latest if latest and str(latest[1]) == content_hash else None
        )
        if reusable:
            article_revision_id, duplicate = str(reusable[0]), True
            duplicate_count += 1
        else:
            article_revision_id, duplicate = uuid.uuid4().hex, False
            available_at = time()
            cursor.execute(
                "INSERT INTO central_news_article_revisions(article_revision_id,stock_code,identity,content_hash,"
                "collector_id,published_at,received_at,available_at,collection_scope,revision_of,document_json) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (article_revision_id, "GLOBAL", identity, content_hash, "naver", document.get("published_at"),
                 now, available_at, "query_set", str(latest[0]) if latest else None,
                 json.dumps(document, ensure_ascii=False, separators=(",", ":"))),
            )
            new_count += 1
        if not processing_excluded:
            _insert_postgres_news_job(cursor, article_revision_id, "GLOBAL", "GLOBAL", "BODY", content_hash,
                                      ARTICLE_BODY_EXTRACTOR_VERSION, document, time())
        cursor.execute(
            "SELECT article_revision_id FROM central_news_article_revisions "
            "WHERE article_revision_id=%s FOR UPDATE", (article_revision_id,),
        )
        if cursor.fetchone() is None:
            raise ValueError("소스 관측에 연결할 기사 리비전이 없습니다.")
        for target in targets:
            _insert_postgres_article_target(cursor, article_revision_id, identity, target)
            if not processing_excluded and str(target.get("relation_status") or "") == "confirmed":
                _enqueue_postgres_existing_body_rule(cursor, article_revision_id, target)
        cursor.execute(
            "INSERT INTO central_news_source_observations(observation_id,run_id,source_id,query_text,page_start,"
            "article_revision_id,identity,published_at,received_at,available_at,content_hash,duplicate,document_json) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (uuid.uuid4().hex, run_id, source_id, query_text, page_start, article_revision_id, identity,
             document.get("published_at"), now, time(), content_hash, duplicate,
             json.dumps({"query_membership": query_text, "targets": targets,
                         "processing_excluded": processing_excluded, **document},
                        ensure_ascii=False, separators=(",", ":"))),
        )
    raw_count = len(value.get("items", []))
    completed = float(value.get("completed_at") or time())
    cursor.execute(
        "INSERT INTO central_news_source_runs(run_revision_id,run_id,source_id,scope,page_start,checked_at,completed_at,"
        "raw_count,unique_count,duplicate_count,request_count,budget_remaining,truncated,coverage,error,document_json) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (uuid.uuid4().hex, run_id, source_id, str(value.get("scope") or "query_set"), page_start,
         float(value.get("checked_at") or now), completed, raw_count, new_count, duplicate_count,
         int(value.get("request_count") or 0), int(value.get("budget_remaining") or 0), bool(value.get("truncated")),
         str(value.get("coverage") or "query_set"), str(value.get("error") or "")[:1000],
         json.dumps(dict(value.get("document") or {}), ensure_ascii=False, separators=(",", ":"))),
    )
    _upsert_postgres_source_cursor(cursor, value, now)
    return {"raw_count": raw_count, "unique_count": new_count, "duplicate_count": duplicate_count}


def _insert_postgres_article_target(cursor: Any, article_revision_id: str, identity: str,
                                    target: dict[str, Any]) -> bool:
    code, name = str(target.get("stock_code") or "") or None, str(target.get("stock_name") or "") or None
    status, version = str(target.get("relation_status") or "unresolved"), str(target.get("rule_version") or "exact-krx-company-name-v1")
    cursor.execute(
        "SELECT target_revision_id FROM central_news_article_target_revisions WHERE article_revision_id=%s "
        "AND COALESCE(stock_code,'')=%s AND COALESCE(stock_name,'')=%s AND relation_status=%s AND rule_version=%s",
        (article_revision_id, code or "", name or "", status, version),
    )
    if cursor.fetchone():
        return False
    cursor.execute(
        "SELECT target_revision_id FROM central_news_article_target_revisions WHERE identity=%s "
        "AND COALESCE(stock_code,'')=%s ORDER BY accepted_sequence DESC LIMIT 1", (identity, code or ""),
    )
    previous = cursor.fetchone()
    cursor.execute(
        "INSERT INTO central_news_article_target_revisions(target_revision_id,article_revision_id,identity,stock_code,"
        "stock_name,relation_status,evidence_text,rule_version,available_at,revision_of,document_json) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (uuid.uuid4().hex, article_revision_id, identity, code, name, status,
         str(target.get("evidence_text") or ""), version, time(), str(previous[0]) if previous else None,
         json.dumps(dict(target), ensure_ascii=False, separators=(",", ":"))),
    )
    return True


def _enqueue_postgres_existing_body_rule(
    cursor: Any, article_revision_id: str, target: dict[str, Any],
) -> None:
    cursor.execute(
        "SELECT body_revision_id,content_hash FROM central_news_body_revisions "
        "WHERE article_revision_id=%s AND status IN ('fulltext','summary_only') "
        "ORDER BY accepted_sequence DESC LIMIT 1", (article_revision_id,),
    )
    body = cursor.fetchone()
    target_code = str(target.get("stock_code") or "")
    if body is None or not target_code:
        return
    _insert_postgres_news_job(
        cursor, article_revision_id, "GLOBAL", target_code, "RULE", str(body[1]),
        SUPPLY_CONTRACT_RULE_VERSION,
        {"body_revision_id": str(body[0]), "stock_code": target_code,
         "stock_name": str(target.get("stock_name") or target_code)},
        time(), str(body[0]),
    )


def _upsert_postgres_source_cursor(cursor: Any, value: dict[str, Any], now: float) -> None:
    cursor.execute(
        "INSERT INTO central_news_source_cursors(source_id,scope,query_text,cursor_published_at,cursor_identity,"
        "pending_published_at,pending_identity,next_start,next_schedule_at,checked_at,last_success,coverage,truncated,error,updated_at) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(source_id) DO UPDATE SET "
        "scope=EXCLUDED.scope,query_text=EXCLUDED.query_text,cursor_published_at=EXCLUDED.cursor_published_at,"
        "cursor_identity=EXCLUDED.cursor_identity,pending_published_at=EXCLUDED.pending_published_at,"
        "pending_identity=EXCLUDED.pending_identity,next_start=EXCLUDED.next_start,next_schedule_at=EXCLUDED.next_schedule_at,"
        "checked_at=EXCLUDED.checked_at,last_success=EXCLUDED.last_success,coverage=EXCLUDED.coverage,"
        "truncated=EXCLUDED.truncated,error=EXCLUDED.error,updated_at=EXCLUDED.updated_at",
        (str(value["source_id"]), str(value.get("scope") or "query_set"), str(value["query_text"]),
         value.get("cursor_published_at"), str(value.get("cursor_identity") or ""), value.get("pending_published_at"),
         str(value.get("pending_identity") or ""), int(value.get("next_start") or 1),
         float(value.get("next_schedule_at") or now), float(value.get("checked_at") or now), value.get("last_success"),
         str(value.get("coverage") or "query_set"), bool(value.get("truncated")),
         str(value.get("error") or "")[:1000], now),
    )


def _source_diagnostics(cursor_rows: list[tuple[object, ...]], run_rows: list[tuple[object, ...]],
                        observation_rows: list[tuple[object, ...]], budget_rows: list[tuple[object, ...]],
                        job_rows: list[tuple[object, ...]], extra: dict[str, Any],
                        summary_counts: dict[str, Any],
                        source_counts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    cursors = [_news_source_cursor(row) for row in cursor_rows]
    run_keys = ("run_revision_id", "run_id", "source_id", "scope", "page_start", "checked_at",
                "completed_at", "raw_count", "unique_count", "duplicate_count", "request_count",
                "budget_remaining", "truncated", "coverage", "error")
    runs = []
    for row in run_rows:
        document = row[15] if isinstance(row[15], dict) else json.loads(str(row[15]))
        runs.append({**dict(zip(run_keys, row[:15], strict=True)), "truncated": bool(row[12]), "document": document})
    observation_keys = ("observation_id", "run_id", "source_id", "query_text", "page_start",
                        "article_revision_id", "identity", "published_at", "received_at", "available_at",
                        "content_hash", "duplicate")
    observations = [{**dict(zip(observation_keys, row[:12], strict=True)), "duplicate": bool(row[11]),
                     "document": row[12] if isinstance(row[12], dict) else json.loads(str(row[12]))}
                    for row in observation_rows]
    for source in cursors:
        totals = source_counts.get(str(source["source_id"]))
        if totals:
            source.update(totals)
            source["items"] = totals["raw_count"]
        latest = next((run for run in runs if run["source_id"] == source["source_id"]), None)
        if latest:
            source["budget_remaining"] = latest["budget_remaining"]
    raw = int(summary_counts.get("raw_count") or 0)
    duplicates = int(summary_counts.get("duplicate_count") or 0)
    return {
        "scope": "query_set", "coverage": "configured_query_set", "sources": cursors,
        "summary": {**summary_counts,
                    "duplicate_rate": (duplicates / raw if raw else 0.0),
                    "budget": {str(row[0]): int(row[1]) for row in budget_rows},
                    "budget_scope": "today_kst_all_news_sources",
                    "job_queue": {str(row[0]): int(row[1]) for row in job_rows},
                    "job_queue_scope": "all_news_jobs", **extra},
        "runs": runs, "observations": observations,
    }


def _load_sqlite_news_source_diagnostics(connection: sqlite3.Connection, source_id: str,
                                         days: int, limit: int) -> dict[str, Any]:
    cutoff, bounded = time() - max(1, min(31, int(days))) * 86400, bounded_limit(limit, 1000)
    where, params = (" WHERE source_id=?", [source_id]) if source_id else ("", [])
    cursors = connection.execute("SELECT " + ",".join(_NEWS_SOURCE_CURSOR_KEYS) +
                                 " FROM central_news_source_cursors" + where + " ORDER BY source_id", params).fetchall()
    run_where = " WHERE checked_at>=?" + (" AND source_id=?" if source_id else "")
    run_params: list[object] = [cutoff] + ([source_id] if source_id else [])
    runs = connection.execute(
        "SELECT run_revision_id,run_id,source_id,scope,page_start,checked_at,completed_at,raw_count,unique_count,"
        "duplicate_count,request_count,budget_remaining,truncated,coverage,error,document_json FROM central_news_source_runs" +
        run_where + " ORDER BY accepted_sequence DESC LIMIT ?", (*run_params, bounded)).fetchall()
    observations = connection.execute(
        "SELECT observation_id,run_id,source_id,query_text,page_start,article_revision_id,identity,published_at,"
        "received_at,available_at,content_hash,duplicate,document_json FROM central_news_source_observations" +
        run_where.replace("checked_at", "available_at") + " ORDER BY accepted_sequence DESC LIMIT ?",
        (*run_params, bounded),
    ).fetchall()
    aggregate = connection.execute(
        "SELECT COALESCE(SUM(raw_count),0),COALESCE(SUM(unique_count),0),"
        "COALESCE(SUM(duplicate_count),0),COALESCE(SUM(request_count),0),"
        "COALESCE(SUM(truncated),0),COALESCE(SUM(CASE WHEN error<>'' THEN 1 ELSE 0 END),0),"
        "COALESCE(MAX(CAST(json_extract(document_json,'$.gap_seconds') AS REAL)),0) "
        "FROM central_news_source_runs" + run_where, run_params,
    ).fetchone()
    distinct_identity = connection.execute(
        "SELECT COUNT(DISTINCT identity) FROM central_news_source_observations" +
        run_where.replace("checked_at", "available_at"), run_params,
    ).fetchone()
    source_aggregates = connection.execute(
        "SELECT source_id,COALESCE(SUM(raw_count),0),COALESCE(SUM(unique_count),0),"
        "COALESCE(SUM(duplicate_count),0),COALESCE(SUM(request_count),0),"
        "COALESCE(SUM(truncated),0),COALESCE(SUM(CASE WHEN error<>'' THEN 1 ELSE 0 END),0) "
        "FROM central_news_source_runs" + run_where + " GROUP BY source_id", run_params,
    ).fetchall()
    source_identities = connection.execute(
        "SELECT source_id,COUNT(DISTINCT identity) FROM central_news_source_observations" +
        run_where.replace("checked_at", "available_at") + " GROUP BY source_id", run_params,
    ).fetchall()
    budgets = connection.execute(
        "SELECT scope,request_count FROM central_news_request_budget WHERE budget_date=?",
        (datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat(),),
    ).fetchall()
    jobs = connection.execute("SELECT state,COUNT(*) FROM central_news_jobs GROUP BY state").fetchall()
    observed_articles = "SELECT DISTINCT article_revision_id FROM central_news_source_observations" + run_where.replace("checked_at", "available_at")
    body = connection.execute(
        "SELECT b.status,COUNT(*) FROM central_news_body_revisions b WHERE b.article_revision_id IN "
        f"({observed_articles}) GROUP BY b.status", run_params,
    ).fetchall()
    rules = connection.execute(
        "SELECT e.event_type,COUNT(*) FROM central_news_event_revisions e WHERE e.article_revision_id IN "
        f"({observed_articles}) GROUP BY e.event_type", run_params,
    ).fetchall()
    targets = connection.execute(
        "SELECT relation_status,COUNT(*) FROM central_news_article_target_revisions WHERE article_revision_id IN "
        f"({observed_articles}) GROUP BY relation_status", run_params,
    ).fetchall()
    extra = {"body_status": {str(row[0]): int(row[1]) for row in body},
             "rule_results": {str(row[0]): int(row[1]) for row in rules},
             "target_status": {str(row[0]): int(row[1]) for row in targets}}
    summary_counts = {
        "raw_count": int(aggregate[0]), "unique_count": int(aggregate[1]),
        "duplicate_count": int(aggregate[2]), "request_count": int(aggregate[3]),
        "truncation_count": int(aggregate[4]), "error_count": int(aggregate[5]),
        "max_gap_seconds": float(aggregate[6] or 0),
        "distinct_identity_count": int(distinct_identity[0]),
    }
    identity_by_source = {str(row[0]): int(row[1]) for row in source_identities}
    source_counts = {
        str(row[0]): {"raw_count": int(row[1]), "unique_count": int(row[2]),
                      "duplicate_count": int(row[3]), "request_count": int(row[4]),
                      "truncation_count": int(row[5]), "error_count": int(row[6]),
                      "distinct_identity_count": identity_by_source.get(str(row[0]), 0)}
        for row in source_aggregates
    }
    return _source_diagnostics(
        cursors, runs, observations, budgets, jobs, extra, summary_counts, source_counts,
    )


def _load_postgres_news_source_diagnostics(cursor: Any, source_id: str,
                                           days: int, limit: int) -> dict[str, Any]:
    cutoff, bounded = time() - max(1, min(31, int(days))) * 86400, bounded_limit(limit, 1000)
    where, params = (" WHERE source_id=%s", [source_id]) if source_id else ("", [])
    cursor.execute("SELECT " + ",".join(_NEWS_SOURCE_CURSOR_KEYS) +
                   " FROM central_news_source_cursors" + where + " ORDER BY source_id", params)
    cursors = cursor.fetchall()
    run_where = " WHERE checked_at>=%s" + (" AND source_id=%s" if source_id else "")
    run_params: list[object] = [cutoff] + ([source_id] if source_id else [])
    cursor.execute(
        "SELECT run_revision_id,run_id,source_id,scope,page_start,checked_at,completed_at,raw_count,unique_count,"
        "duplicate_count,request_count,budget_remaining,truncated,coverage,error,document_json FROM central_news_source_runs" +
        run_where + " ORDER BY accepted_sequence DESC LIMIT %s", (*run_params, bounded))
    runs = cursor.fetchall()
    cursor.execute(
        "SELECT observation_id,run_id,source_id,query_text,page_start,article_revision_id,identity,published_at,"
        "received_at,available_at,content_hash,duplicate,document_json FROM central_news_source_observations" +
        run_where.replace("checked_at", "available_at") + " ORDER BY accepted_sequence DESC LIMIT %s",
        (*run_params, bounded))
    observations = cursor.fetchall()
    cursor.execute(
        "SELECT COALESCE(SUM(raw_count),0),COALESCE(SUM(unique_count),0),"
        "COALESCE(SUM(duplicate_count),0),COALESCE(SUM(request_count),0),"
        "COALESCE(SUM(CASE WHEN truncated THEN 1 ELSE 0 END),0),"
        "COALESCE(SUM(CASE WHEN error<>'' THEN 1 ELSE 0 END),0),"
        "COALESCE(MAX(COALESCE(NULLIF(document_json->>'gap_seconds','')::DOUBLE PRECISION,0)),0) "
        "FROM central_news_source_runs" + run_where, run_params)
    aggregate = cursor.fetchone()
    cursor.execute(
        "SELECT COUNT(DISTINCT identity) FROM central_news_source_observations" +
        run_where.replace("checked_at", "available_at"), run_params)
    distinct_identity = cursor.fetchone()
    cursor.execute(
        "SELECT source_id,COALESCE(SUM(raw_count),0),COALESCE(SUM(unique_count),0),"
        "COALESCE(SUM(duplicate_count),0),COALESCE(SUM(request_count),0),"
        "COALESCE(SUM(CASE WHEN truncated THEN 1 ELSE 0 END),0),"
        "COALESCE(SUM(CASE WHEN error<>'' THEN 1 ELSE 0 END),0) "
        "FROM central_news_source_runs" + run_where + " GROUP BY source_id", run_params)
    source_aggregates = cursor.fetchall()
    cursor.execute(
        "SELECT source_id,COUNT(DISTINCT identity) FROM central_news_source_observations" +
        run_where.replace("checked_at", "available_at") + " GROUP BY source_id", run_params)
    source_identities = cursor.fetchall()
    cursor.execute("SELECT scope,request_count FROM central_news_request_budget WHERE budget_date=%s",
                   (datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat(),))
    budgets = cursor.fetchall()
    cursor.execute("SELECT state,COUNT(*) FROM central_news_jobs GROUP BY state")
    jobs = cursor.fetchall()
    observed_articles = "SELECT DISTINCT article_revision_id FROM central_news_source_observations" + run_where.replace("checked_at", "available_at")
    cursor.execute(
        "SELECT b.status,COUNT(*) FROM central_news_body_revisions b WHERE b.article_revision_id IN "
        f"({observed_articles}) GROUP BY b.status", run_params)
    body = cursor.fetchall()
    cursor.execute(
        "SELECT e.event_type,COUNT(*) FROM central_news_event_revisions e WHERE e.article_revision_id IN "
        f"({observed_articles}) GROUP BY e.event_type", run_params)
    rules = cursor.fetchall()
    cursor.execute(
        "SELECT relation_status,COUNT(*) FROM central_news_article_target_revisions WHERE article_revision_id IN "
        f"({observed_articles}) GROUP BY relation_status", run_params)
    targets = cursor.fetchall()
    extra = {"body_status": {str(row[0]): int(row[1]) for row in body},
             "rule_results": {str(row[0]): int(row[1]) for row in rules},
             "target_status": {str(row[0]): int(row[1]) for row in targets}}
    summary_counts = {
        "raw_count": int(aggregate[0]), "unique_count": int(aggregate[1]),
        "duplicate_count": int(aggregate[2]), "request_count": int(aggregate[3]),
        "truncation_count": int(aggregate[4]), "error_count": int(aggregate[5]),
        "max_gap_seconds": float(aggregate[6] or 0),
        "distinct_identity_count": int(distinct_identity[0]),
    }
    identity_by_source = {str(row[0]): int(row[1]) for row in source_identities}
    source_counts = {
        str(row[0]): {"raw_count": int(row[1]), "unique_count": int(row[2]),
                      "duplicate_count": int(row[3]), "request_count": int(row[4]),
                      "truncation_count": int(row[5]), "error_count": int(row[6]),
                      "distinct_identity_count": identity_by_source.get(str(row[0]), 0)}
        for row in source_aggregates
    }
    return _source_diagnostics(
        cursors, runs, observations, budgets, jobs, extra, summary_counts, source_counts,
    )


def _sqlite_possible_related(connection: sqlite3.Connection, stock_code: str, event_id: str,
                             identities: Any) -> list[str]:
    values = [str(value) for value in identities if str(value)]
    rows: list[tuple[object, ...]] = []
    if values:
        placeholders = ",".join("?" for _ in values)
        rows = connection.execute(
            "SELECT DISTINCT m.event_id FROM central_news_event_membership_revisions m "
            "JOIN central_news_article_revisions a ON a.article_revision_id=m.article_revision_id "
            "JOIN central_news_event_revisions e ON e.event_revision_id=m.event_revision_id "
            f"WHERE e.stock_code=? AND a.identity IN ({placeholders}) ORDER BY m.accepted_sequence DESC LIMIT 5",
            (stock_code, *values),
        ).fetchall()
    if not rows:
        rows = connection.execute(
            "SELECT event_id FROM central_news_event_revisions WHERE stock_code=? AND event_id<>? "
            "ORDER BY accepted_sequence DESC LIMIT 5", (stock_code, event_id),
        ).fetchall()
    return [str(row[0]) for row in rows if str(row[0]) != event_id]


def _postgres_possible_related(cursor: Any, stock_code: str, event_id: str, identities: Any) -> list[str]:
    values = [str(value) for value in identities if str(value)]
    if values:
        cursor.execute(
            "SELECT DISTINCT m.event_id FROM central_news_event_membership_revisions m "
            "JOIN central_news_article_revisions a ON a.article_revision_id=m.article_revision_id "
            "JOIN central_news_event_revisions e ON e.event_revision_id=m.event_revision_id "
            "WHERE e.stock_code=%s AND a.identity=ANY(%s) ORDER BY m.event_id LIMIT 5",
            (stock_code, values),
        )
        rows = cursor.fetchall()
    else:
        rows = []
    if not rows:
        cursor.execute(
            "SELECT event_id FROM central_news_event_revisions WHERE stock_code=%s AND event_id<>%s "
            "ORDER BY accepted_sequence DESC LIMIT 5", (stock_code, event_id),
        )
        rows = cursor.fetchall()
    return [str(row[0]) for row in rows if str(row[0]) != event_id]


def _news_history_query(kind: str, placeholder: str, target: str, identity: str,
                        available_at: float | None, limit: int) -> tuple[str, list[object]]:
    if kind == "article":
        sql = ("SELECT article_revision_id,stock_code,identity,content_hash,collector_id,published_at,"
               "received_at,available_at,collection_scope,revision_of,document_json "
               "FROM central_news_article_revisions WHERE 1=1")
        target_column, identity_column = "stock_code", "identity"
    elif kind == "body":
        sql = ("SELECT body_revision_id,article_revision_id,content_hash,extractor_version,fetched_at,"
               "available_at,status,body_text,error FROM central_news_body_revisions WHERE 1=1")
        target_column, identity_column = "article_revision_id", ""
    elif kind == "ai":
        sql = ("SELECT analysis_revision_id,target_id,article_revision_id,body_revision_id,provider,model,"
               "prompt_version,schema_version,input_hash,computed_at,available_at,output_json,usage_json "
               "FROM central_news_ai_revisions WHERE 1=1")
        target_column, identity_column = "target_id", ""
    elif kind == "event":
        sql = ("SELECT event_revision_id,event_id,event_key,stock_code,event_type,article_revision_id,"
               "body_revision_id,rule_version,input_hash,role,scope,certainty,novelty,amount_won,counterparty,"
               "importance_score,confidence_score,novelty_score,ai_required,available_at,revision_of,result_json "
               "FROM central_news_event_revisions WHERE 1=1")
        target_column, identity_column = "stock_code", "event_id"
    elif kind == "membership":
        sql = ("SELECT membership_revision_id,event_id,event_revision_id,article_revision_id,body_revision_id,"
               "relation,available_at,revision_of,document_json "
               "FROM central_news_event_membership_revisions WHERE 1=1")
        target_column, identity_column = "event_id", "article_revision_id"
    else:
        raise ValueError("뉴스 이력 종류는 article/body/ai/event/membership 중 하나여야 합니다.")
    parameters: list[object] = []
    if target:
        sql += f" AND {target_column}={placeholder}"
        parameters.append(target)
    if identity and identity_column:
        sql += f" AND {identity_column}={placeholder}"
        parameters.append(identity)
    if available_at is not None:
        sql += f" AND available_at<={placeholder}"
        parameters.append(float(available_at))
    sql += f" ORDER BY accepted_sequence DESC LIMIT {placeholder}"
    parameters.append(bounded_limit(limit, 1000))
    return sql, parameters


def _decode_news_history(kind: str, rows: list[tuple[object, ...]]) -> list[dict[str, Any]]:
    if kind == "article":
        keys = ("article_revision_id", "stock_code", "identity", "content_hash", "collector_id",
                "published_at", "received_at", "available_at", "collection_scope", "revision_of")
        return [{**dict(zip(keys, row[:10], strict=True)),
                 "document": row[10] if isinstance(row[10], dict) else json.loads(str(row[10]))} for row in rows]
    if kind == "body":
        keys = ("body_revision_id", "article_revision_id", "content_hash", "extractor_version",
                "fetched_at", "available_at", "status", "body_text", "error")
        return [dict(zip(keys, row, strict=True)) for row in rows]
    if kind == "event":
        keys = ("event_revision_id", "event_id", "event_key", "stock_code", "event_type",
                "article_revision_id", "body_revision_id", "rule_version", "input_hash", "role", "scope",
                "certainty", "novelty", "amount_won", "counterparty", "importance_score",
                "confidence_score", "novelty_score", "ai_required", "available_at", "revision_of")
        return [{**dict(zip(keys, row[:21], strict=True)), "ai_required": bool(row[18]),
                 "result": json_mapping(row[21])} for row in rows]
    if kind == "membership":
        keys = ("membership_revision_id", "event_id", "event_revision_id", "article_revision_id",
                "body_revision_id", "relation", "available_at", "revision_of")
        return [{**dict(zip(keys, row[:8], strict=True)),
                 "document": json_mapping(row[8])} for row in rows]
    keys = ("analysis_revision_id", "target_id", "article_revision_id", "body_revision_id",
            "provider", "model", "prompt_version", "schema_version", "input_hash", "computed_at",
            "available_at")
    return [{**dict(zip(keys, row[:11], strict=True)),
             "output": row[11] if isinstance(row[11], dict) else json.loads(str(row[11])),
             "usage": row[12] if isinstance(row[12], dict) else json.loads(str(row[12]))} for row in rows]


def _load_sqlite_news_history(connection: sqlite3.Connection, kind: str, target: str,
                              identity: str, available_at: float | None, limit: int) -> list[dict[str, Any]]:
    sql, parameters = _news_history_query(kind, "?", target, identity, available_at, limit)
    return _decode_news_history(kind, connection.execute(sql, parameters).fetchall())


def _load_postgres_news_history(cursor: Any, kind: str, target: str, identity: str,
                                available_at: float | None, limit: int) -> list[dict[str, Any]]:
    sql, parameters = _news_history_query(kind, "%s", target, identity, available_at, limit)
    cursor.execute(sql, parameters)
    return _decode_news_history(kind, cursor.fetchall())


def _confirmed_news_articles_query(placeholder: str) -> str:
    return (
        "WITH ranked AS ("
        "SELECT a.document_json,a.accepted_sequence,a.article_revision_id,"
        "ROW_NUMBER() OVER(PARTITION BY a.identity ORDER BY a.accepted_sequence DESC) AS identity_rank "
        "FROM central_news_article_target_revisions t "
        "JOIN central_news_article_revisions a ON a.article_revision_id=t.article_revision_id "
        f"WHERE t.stock_code={placeholder} AND t.relation_status='confirmed' AND a.stock_code='GLOBAL') "
        "SELECT document_json,(SELECT b.status FROM central_news_body_revisions b "
        "WHERE b.article_revision_id=ranked.article_revision_id "
        "ORDER BY b.accepted_sequence DESC LIMIT 1),"
        "(SELECT b.body_text FROM central_news_body_revisions b "
        "WHERE b.article_revision_id=ranked.article_revision_id "
        "ORDER BY b.accepted_sequence DESC LIMIT 1) "
        "FROM ranked WHERE identity_rank=1 "
        f"ORDER BY accepted_sequence DESC LIMIT {placeholder}"
    )


def _stock_news_articles_query(placeholder: str) -> str:
    return (
        "WITH ranked AS ("
        "SELECT a.document_json,a.accepted_sequence,a.article_revision_id,"
        "ROW_NUMBER() OVER(PARTITION BY a.identity ORDER BY a.accepted_sequence DESC) AS identity_rank "
        "FROM central_news_article_revisions a "
        f"WHERE a.stock_code={placeholder} AND a.collection_scope='watchlist') "
        "SELECT document_json,(SELECT b.status FROM central_news_body_revisions b "
        "WHERE b.article_revision_id=ranked.article_revision_id "
        "ORDER BY b.accepted_sequence DESC LIMIT 1),"
        "(SELECT b.body_text FROM central_news_body_revisions b "
        "WHERE b.article_revision_id=ranked.article_revision_id "
        "ORDER BY b.accepted_sequence DESC LIMIT 1) "
        "FROM ranked WHERE identity_rank=1 "
        f"ORDER BY accepted_sequence DESC LIMIT {placeholder}"
    )


def _decode_confirmed_news_articles(rows: list[tuple[object, ...]]) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        document = row[0] if isinstance(row[0], dict) else json.loads(str(row[0]))
        if isinstance(document, dict):
            value = dict(document)
            value["_body_status"] = str(row[1] or "")
            value["_body_text"] = str(row[2] or "")
            result.append(value)
    return result


def _load_sqlite_stock_news_articles(
    connection: sqlite3.Connection, stock_code: str, limit: int,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        _stock_news_articles_query("?"),
        (stock_code, bounded_limit(limit, 1000)),
    ).fetchall()
    return _decode_confirmed_news_articles(rows)


def _load_postgres_stock_news_articles(
    cursor: Any, stock_code: str, limit: int,
) -> list[dict[str, Any]]:
    cursor.execute(
        _stock_news_articles_query("%s"),
        (stock_code, bounded_limit(limit, 1000)),
    )
    return _decode_confirmed_news_articles(cursor.fetchall())


def _load_sqlite_confirmed_news_articles(
    connection: sqlite3.Connection, stock_code: str, limit: int,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        _confirmed_news_articles_query("?"),
        (stock_code, bounded_limit(limit, 1000)),
    ).fetchall()
    return _decode_confirmed_news_articles(rows)


def _load_postgres_confirmed_news_articles(
    cursor: Any, stock_code: str, limit: int,
) -> list[dict[str, Any]]:
    cursor.execute(
        _confirmed_news_articles_query("%s"),
        (stock_code, bounded_limit(limit, 1000)),
    )
    return _decode_confirmed_news_articles(cursor.fetchall())


def _market_metadata_upsert_sql(placeholder: str, excluded: str) -> str:
    placeholders = ",".join((placeholder,) * 12)
    return (
        f"INSERT INTO central_market_data_observation_meta VALUES({placeholders}) "
        "ON CONFLICT(dataset_kind,subject,observation_key) DO UPDATE SET "
        f"effective_at={excluded}.effective_at,available_at={excluded}.available_at,"
        f"venue={excluded}.venue,unit={excluded}.unit,value_kind={excluded}.value_kind,"
        f"completeness={excluded}.completeness,origin={excluded}.origin,source={excluded}.source,"
        f"candidate_universe={excluded}.candidate_universe"
    )


def _observation_revision_select(placeholder: str, *, postgres: bool = False) -> str:
    return (
        f"SELECT {_observation_revision_columns(postgres=postgres)} "
        f"FROM central_observation_revisions WHERE kind={placeholder}"
    )


def _canonical_document(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _save_sqlite_shadow_evaluation(
    connection: sqlite3.Connection,
    monitor_id: str,
    decision: dict[str, Any],
    candidate: dict[str, Any] | None,
    expires_at: str,
) -> None:
    encoded_decision = _canonical_document(decision)
    existing = connection.execute(
        "SELECT document_json FROM central_shadow_decisions WHERE decision_id=?",
        (str(decision["decision_id"]),),
    ).fetchone()
    if existing is not None and str(existing[0]) != encoded_decision:
        raise ValueError("shadow decision id already has different immutable content")
    connection.execute(
        "INSERT OR IGNORE INTO central_shadow_decisions VALUES(?,?,?,?)",
        (str(decision["decision_id"]), monitor_id, str(decision["decided_at"]), encoded_decision),
    )
    if candidate is None:
        return
    encoded_candidate = _canonical_document(candidate)
    existing = connection.execute(
        "SELECT document_json FROM central_shadow_candidate_events WHERE event_id=?",
        (str(candidate["event_id"]),),
    ).fetchone()
    if existing is not None and str(existing[0]) != encoded_candidate:
        raise ValueError("shadow candidate id already has different immutable content")
    connection.execute(
        "INSERT OR IGNORE INTO central_shadow_candidate_events("
        "event_id,monitor_id,available_at,expires_at,document_json) VALUES(?,?,?,?,?)",
        (str(candidate["event_id"]), monitor_id, str(candidate["available_at"]), expires_at, encoded_candidate),
    )


def _save_postgres_shadow_evaluation(
    cursor: Any,
    monitor_id: str,
    decision: dict[str, Any],
    candidate: dict[str, Any] | None,
    expires_at: str,
) -> None:
    cursor.execute(
        "SELECT document_json FROM central_shadow_decisions WHERE decision_id=%s",
        (str(decision["decision_id"]),),
    )
    existing = cursor.fetchone()
    if existing is not None and _canonical_document(json_mapping(existing[0])) != _canonical_document(decision):
        raise ValueError("shadow decision id already has different immutable content")
    cursor.execute(
        "INSERT INTO central_shadow_decisions VALUES(%s,%s,%s,%s) ON CONFLICT(decision_id) DO NOTHING",
        (str(decision["decision_id"]), monitor_id, str(decision["decided_at"]),
         json.dumps(decision, ensure_ascii=False)),
    )
    if candidate is None:
        return
    cursor.execute(
        "SELECT document_json FROM central_shadow_candidate_events WHERE event_id=%s",
        (str(candidate["event_id"]),),
    )
    existing = cursor.fetchone()
    if existing is not None and _canonical_document(json_mapping(existing[0])) != _canonical_document(candidate):
        raise ValueError("shadow candidate id already has different immutable content")
    cursor.execute(
        "INSERT INTO central_shadow_candidate_events("
        "event_id,monitor_id,available_at,expires_at,document_json) VALUES(%s,%s,%s,%s,%s) "
        "ON CONFLICT(event_id) DO NOTHING",
        (str(candidate["event_id"]), monitor_id, str(candidate["available_at"]), expires_at,
         json.dumps(candidate, ensure_ascii=False)),
    )


def _shadow_candidate_page(rows: list[tuple[object, ...]], high_watermark: int) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    for sequence, raw_document, expires_at in rows:
        document = json_mapping(raw_document)
        document["accepted_sequence"] = int(sequence)
        document["expires_at"] = expires_at.isoformat() if hasattr(expires_at, "isoformat") else str(expires_at)
        events.append(document)
    next_cursor = int(events[-1]["accepted_sequence"]) if events else None
    return {
        "high_watermark": max(0, int(high_watermark)),
        "events": events,
        "next_cursor": next_cursor,
        "has_more": bool(next_cursor is not None and next_cursor < high_watermark),
    }


def _observation_revision_columns(*, prefix: str = "", postgres: bool = False) -> str:
    column = lambda name: f"{prefix}{name}"
    effective_at = f"{column('effective_at')}::text" if postgres else column("effective_at")
    received_at = f"{column('received_at')}::text" if postgres else column("received_at")
    available_at = f"{column('available_at')}::text" if postgres else column("available_at")
    return ",".join((
        column("accepted_sequence"), column("revision_id"), column("observation_key"),
        column("schema_version"), column("source_id"), column("source_session_id"),
        column("source_sequence"), column("kind"), column("subject"), column("venue"),
        effective_at, received_at, available_at, column("revision_of"), column("payload_hash"),
        column("unit"), column("value_kind"), column("completeness"), column("origin"),
        column("candidate_universe"), column("clock_quality"), column("quality_flags_json"),
        column("source_ref_json"), column("payload_json"),
    ))


def _observation_export_inputs(
    start: datetime, end: datetime, kinds: tuple[str, ...],
) -> tuple[datetime, datetime, tuple[str, ...]]:
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("research export timestamps must be timezone-aware")
    normalized_start = start.astimezone(timezone.utc)
    normalized_end = end.astimezone(timezone.utc)
    if normalized_start >= normalized_end:
        raise ValueError("research export start must be before end")
    if (normalized_end - normalized_start).total_seconds() > 86_400:
        raise ValueError("research export range is limited to one session (24 hours)")
    normalized_kinds = tuple(dict.fromkeys(str(kind).strip() for kind in kinds if str(kind).strip()))
    if not normalized_kinds or any(kind not in RESEARCH_OBSERVATION_KINDS for kind in normalized_kinds):
        raise ValueError("research export kinds must be ranking, top20_membership, or minute_bar")
    return normalized_start, normalized_end, normalized_kinds


def _observation_export_manifest(
    start: datetime,
    end: datetime,
    kinds: tuple[str, ...],
    subject: str,
    selected: list[tuple[object, ...]],
) -> dict[str, Any]:
    dataset_id = str(uuid.uuid4())
    revision_ids = [str(row[0]) for row in selected]
    revision_ids_hash = hashlib.sha256("\n".join(revision_ids).encode("utf-8")).hexdigest()
    recorded_from = _iso_value(selected[0][2]) if selected else None
    return {
        "dataset_id": dataset_id,
        "schema_version": 1,
        "captured_range": {"start": start.isoformat(), "end": end.isoformat()},
        "recorded_from": recorded_from,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "source_ids": sorted({str(row[1]) for row in selected}),
        "fixed_watermark": dataset_id,
        "revision_count": len(revision_ids),
        "revision_ids_hash": revision_ids_hash,
        "kinds": list(kinds),
        "subject": subject,
        "universe_rule": (
            "top20-membership-with-minute-bars-v1"
            if "minute_bar" in kinds else "top20_membership-v1"
        ),
        "timezone": "UTC",
        "order_policy_version": "available_at-ingest_sequence-revision_id-v1",
        "quality_summary": {
            "flags": (["coverage_not_evaluated"] if selected else
                      ["coverage_not_evaluated", "no_recorded_observations"]),
            "recording_gap": "unknown",
        },
        "replay_profile": "observed_replay",
    }


def _sqlite_export_values(manifest: dict[str, Any]) -> tuple[object, ...]:
    captured = manifest["captured_range"]
    return (
        manifest["dataset_id"], manifest["exported_at"], captured["start"], captured["end"],
        json.dumps(manifest["kinds"], separators=(",", ":")), manifest["subject"],
        manifest["revision_count"], manifest["revision_ids_hash"],
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":")),
    )


def _postgres_export_values(manifest: dict[str, Any]) -> tuple[object, ...]:
    captured = manifest["captured_range"]
    return (
        manifest["dataset_id"], datetime.fromisoformat(manifest["exported_at"]),
        datetime.fromisoformat(captured["start"]), datetime.fromisoformat(captured["end"]),
        json.dumps(manifest["kinds"], separators=(",", ":")), manifest["subject"],
        manifest["revision_count"], manifest["revision_ids_hash"],
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":")),
    )


def _observation_export_page_sql(placeholder: str, *, postgres: bool = False) -> str:
    return (
        f"SELECT {_observation_revision_columns(prefix='r.', postgres=postgres)},m.ordinal "
        "FROM central_research_export_members m JOIN central_observation_revisions r "
        "ON r.revision_id=m.revision_id "
        f"WHERE m.dataset_id={placeholder} AND m.ordinal>{placeholder} "
        f"ORDER BY m.ordinal LIMIT {placeholder}"
    )


def _observation_export_page(
    raw_manifest: object, rows: list[tuple[object, ...]], page_limit: int,
) -> dict[str, Any]:
    manifest = json_mapping(raw_manifest)
    page_rows = rows[:page_limit]
    observations = observation_revision_result_rows([row[:24] for row in page_rows])
    for observation, row in zip(observations, page_rows, strict=True):
        observation["ordinal"] = int(row[24])
    has_more = len(rows) > page_limit
    return {
        "watermark": manifest["fixed_watermark"],
        "manifest": manifest,
        "observations": observations,
        "next_cursor": observations[-1]["ordinal"] if has_more and observations else None,
    }


def _iso_value(value: object) -> str:
    return value.isoformat() if isinstance(value, datetime) else str(value)


def _minute_key(value: dict[str, Any]) -> str:
    return f"{value['trading_date']}T{value['minute']}"


def _minute_operation(
    value: dict[str, Any], *, finalization: bool = False,
) -> tuple[str, str]:
    operation_id = str(value.get("operation_id") or uuid.uuid4()).strip()
    if not operation_id:
        raise ValueError("minute bar operation_id is required")
    names = (
        ("trading_date", "minute", "code", "market", "available_at", "capture_quality", "finalization_source")
        if finalization else bar_columns(minute=True)
    )
    canonical = {name: value[name] for name in names}
    encoded = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return operation_id, hashlib.sha256(encoded).hexdigest()


def _load_sqlite_minute_bar(
    connection: sqlite3.Connection, value: dict[str, Any],
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT trading_date,minute,code,market,open,high,low,close,volume,"
        "trade_value_million_won,updated_at FROM central_minute_bars "
        "WHERE trading_date=? AND minute=? AND code=? AND market=?",
        tuple(value[name] for name in ("trading_date", "minute", "code", "market")),
    ).fetchone()
    return bar_result_rows((row,), minute=True)[0] if row is not None else None


def _load_postgres_minute_bar(cursor: Any, value: dict[str, Any]) -> dict[str, Any] | None:
    cursor.execute(
        "SELECT trading_date::text,to_char(minute,'HH24:MI'),code,market,open,high,low,close,"
        "volume,trade_value_million_won,updated_at FROM central_minute_bars "
        "WHERE trading_date=%s AND minute=%s AND code=%s AND market=%s",
        tuple(value[name] for name in ("trading_date", "minute", "code", "market")),
    )
    row = cursor.fetchone()
    return bar_result_rows((row,), minute=True)[0] if row is not None else None


def _final_minute_observation(
    merged: dict[str, Any], closure: dict[str, Any],
) -> MarketDataObservation[dict[str, Any]]:
    capture_quality = str(closure.get("capture_quality", ""))
    if capture_quality not in {"complete", "partial"}:
        raise ValueError("minute bar capture_quality must be complete or partial")
    bar_start = datetime.fromisoformat(
        f"{merged['trading_date']}T{merged['minute']}"
    ).replace(tzinfo=ZoneInfo("Asia/Seoul"))
    available_at = datetime.fromtimestamp(
        float(closure["available_at"]), tz=ZoneInfo("Asia/Seoul")
    )
    if available_at < bar_start.replace(second=0, microsecond=0) + timedelta(minutes=1):
        raise ValueError("minute bar cannot be finalized before bar_end")
    available_value = dict(merged)
    available_value["updated_at"] = float(closure["available_at"])
    complete = capture_quality == "complete"
    return minute_bar_observation(
        available_value,
        origin=ObservationOrigin.REALTIME,
        completeness=DataCompleteness.COMPLETE if complete else DataCompleteness.PARTIAL,
        source="kiwoom-websocket-0B",
        value_kind=DataValueKind.ACTUAL,
    )


def _save_final_minute_revision_sqlite(
    connection: sqlite3.Connection, merged: dict[str, Any], closure: dict[str, Any],
    history_enabled: bool,
) -> None:
    observation = _final_minute_observation(merged, closure)
    key = bar_observation_key(observation)
    connection.execute(
        _market_metadata_upsert_sql("?", "excluded"),
        market_metadata_storage_values(key, observation),
    )
    if history_enabled:
        _append_sqlite_observation_revision(
            connection, "minute_bar", observation.subject, key,
            minute_bar_revision_payload(
                merged, window_closed=True,
                capture_quality=str(closure["capture_quality"]),
                finalization_source=str(closure["finalization_source"]),
                operation_id=str(closure["operation_id"]),
            ),
            observation,
        )


def _save_final_minute_revision_postgres(
    cursor: Any, merged: dict[str, Any], closure: dict[str, Any], history_enabled: bool,
) -> None:
    observation = _final_minute_observation(merged, closure)
    key = bar_observation_key(observation)
    cursor.execute(
        _market_metadata_upsert_sql("%s", "EXCLUDED"),
        market_metadata_storage_values(key, observation),
    )
    if history_enabled:
        _append_postgres_observation_revision(
            cursor, "minute_bar", observation.subject, key,
            minute_bar_revision_payload(
                merged, window_closed=True,
                capture_quality=str(closure["capture_quality"]),
                finalization_source=str(closure["finalization_source"]),
                operation_id=str(closure["operation_id"]),
            ),
            observation,
        )


def _append_sqlite_observation_revision(
    connection: sqlite3.Connection,
    kind: str,
    subject: str,
    observation_key: str,
    payload: dict[str, Any],
    observation: MarketDataObservation[object],
) -> None:
    source = ObservationRevisionSource.from_observation(
        kind, subject, observation_key, payload, observation,
    )
    latest = connection.execute(
        "SELECT revision_id,payload_hash FROM central_observation_revisions "
        "WHERE kind=? AND subject=? AND observation_key=? AND source_id=? "
        "ORDER BY accepted_sequence DESC LIMIT 1",
        (source.kind, source.subject, source.observation_key, source.source_id),
    ).fetchone()
    if latest is not None and str(latest[1]) == source.payload_hash:
        return
    connection.execute(
        "INSERT INTO central_observation_revisions("
        "revision_id,observation_key,schema_version,source_id,source_session_id,source_sequence,"
        "kind,subject,venue,effective_at,received_at,available_at,revision_of,payload_hash,unit,"
        "value_kind,completeness,origin,candidate_universe,quality_flags_json,clock_quality,"
        "source_ref_json,payload_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        _observation_revision_values(source, latest, sqlite=True),
    )


def _append_postgres_observation_revision(
    cursor: Any,
    kind: str,
    subject: str,
    observation_key: str,
    payload: dict[str, Any],
    observation: MarketDataObservation[object],
) -> None:
    source = ObservationRevisionSource.from_observation(
        kind, subject, observation_key, payload, observation,
    )
    cursor.execute(
        "SELECT revision_id,payload_hash FROM central_observation_revisions "
        "WHERE kind=%s AND subject=%s AND observation_key=%s AND source_id=%s "
        "ORDER BY accepted_sequence DESC LIMIT 1",
        (source.kind, source.subject, source.observation_key, source.source_id),
    )
    latest = cursor.fetchone()
    if latest is not None and str(latest[1]) == source.payload_hash:
        return
    cursor.execute(
        "INSERT INTO central_observation_revisions("
        "revision_id,observation_key,schema_version,source_id,source_session_id,source_sequence,"
        "kind,subject,venue,effective_at,received_at,available_at,revision_of,payload_hash,unit,"
        "value_kind,completeness,origin,candidate_universe,quality_flags_json,clock_quality,"
        "source_ref_json,payload_json) VALUES(" + ",".join(("%s",) * 23) + ")",
        _observation_revision_values(source, latest, sqlite=False),
    )


def _observation_revision_values(
    source: ObservationRevisionSource,
    latest: tuple[object, ...] | None,
    *,
    sqlite: bool,
) -> tuple[object, ...]:
    effective_at: object = source.effective_at
    available_at: object = source.available_at
    received_at: object = datetime.now(timezone.utc)
    if sqlite:
        effective_at = source.effective_at.isoformat() if source.effective_at else None
        available_at = source.available_at.isoformat() if source.available_at else None
        received_at = received_at.isoformat()
    return (
        str(uuid.uuid4()),
        source.observation_key,
        OBSERVATION_REVISION_SCHEMA_VERSION,
        source.source_id,
        "",
        "",
        source.kind,
        source.subject,
        source.venue,
        effective_at,
        received_at,
        available_at,
        str(latest[0]) if latest is not None else None,
        source.payload_hash,
        source.unit,
        source.value_kind,
        source.completeness,
        source.origin,
        source.candidate_universe,
        "[]",
        "source_timezone_confirmed",
        source.source_ref_json,
        source.payload_json,
    )


def _metadata_from_range_row(row: tuple[object, ...]) -> MarketDataMetadata:
    metadata = market_metadata_from_storage_row(tuple(row[1:]))
    if metadata is None:
        raise ValueError("market metadata row is missing")
    return metadata


def _save_sqlite_metadata(connection, observations) -> None:
    if not observations:
        return
    connection.executemany(
        _market_metadata_upsert_sql("?", "excluded"),
        [market_metadata_storage_values(key, observation) for key, observation in observations],
    )


def _save_postgres_metadata(cursor, observations) -> None:
    if not observations:
        return
    cursor.executemany(
        _market_metadata_upsert_sql("%s", "EXCLUDED"),
        [market_metadata_storage_values(key, observation) for key, observation in observations],
    )
