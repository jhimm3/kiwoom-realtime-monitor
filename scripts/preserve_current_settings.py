"""One-off raw settings capture/verify/accept and explicit role installation."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import uuid

from kiwoom_monitor.infrastructure.persistence.sqlite_read_snapshot import readonly_sqlite_snapshot

FORMAT = "preserved-current-settings/v1"
TABLES = {
    "settings": ("key", "value"),
    "column_settings": ("column_name", "visible", "position", "width"),
    "central_setting_versions": ("setting_key", "updated_at"),
    "central_column_setting_versions": ("column_name", "updated_at"),
}
OWNER_FILE = "settings-target-owner.json"


class SettingsPreservationError(ValueError):
    """A raw artifact/selection/target contract is incomplete or inconsistent."""


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _read_json(raw):
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise SettingsPreservationError("duplicate JSON key")
            result[key] = value
        return result
    def reject(_):
        raise SettingsPreservationError("nonfinite JSON value")
    return json.loads(raw, object_pairs_hook=unique, parse_constant=reject)


def _label(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value):
        raise SettingsPreservationError("an explicit opaque owner/operation label is required")
    return value


def encode_value(value):
    """Represent SQLite storage primitives without JSON float/string coercion."""
    if value is None:
        return ["null", None]
    if type(value) is int:
        return ["integer", str(value)]
    if type(value) is float:
        if math.isnan(value):
            raise SettingsPreservationError("unsupported SQLite NaN value")
        return ["real", value.hex()]
    if type(value) is str:
        return ["text", value]
    if type(value) is bytes:
        return ["blob", base64.b64encode(value).decode("ascii")]
    raise SettingsPreservationError("unsupported SQLite storage primitive")


def decode_value(value):
    if not isinstance(value, list) or len(value) != 2:
        raise SettingsPreservationError("invalid SQLite primitive envelope")
    kind, raw = value
    if kind == "null" and raw is None:
        return None
    if kind == "integer" and isinstance(raw, str):
        result = int(raw)
        if str(result) == raw and -(2**63) <= result < 2**63:
            return result
    elif kind == "real" and isinstance(raw, str):
        result = float.fromhex(raw)
        if not math.isnan(result) and result.hex() == raw:
            return result
    elif kind == "text" and isinstance(raw, str):
        return raw
    elif kind == "blob" and isinstance(raw, str):
        result = base64.b64decode(raw, validate=True)
        if base64.b64encode(result).decode("ascii") == raw:
            return result
    raise SettingsPreservationError("invalid SQLite storage primitive")


def _clock(now=None):
    from kiwoom_monitor.domain.time_basis import as_kst, kst_now
    value = now if now is not None else kst_now()  # Explicit offline artifact/receipt only.
    if value.utcoffset() is None:
        raise SettingsPreservationError("offline receipt clock must be aware")
    return as_kst(value).isoformat()


def _summary(records):
    pairs = (("settings", "central_setting_versions"), ("column_settings", "central_column_setting_versions"))
    result = {"records": len(records), "missing_versions": 0, "orphan_versions": 0}
    for values, versions in pairs:
        value_rows = [row for row in records.values() if row["relation"] == values]
        version_rows = [row for row in records.values() if row["relation"] == versions]
        keys = {_json(row["row"][0]) for row in value_rows if row["row"][0][0] != "null"}
        version_keys = {_json(row["row"][0]) for row in version_rows if row["row"][0][0] != "null"}
        result["missing_versions"] += sum(row["row"][0][0] == "null" or _json(row["row"][0]) not in version_keys for row in value_rows)
        result["orphan_versions"] += sum(row["row"][0][0] == "null" or _json(row["row"][0]) not in keys for row in version_rows)
    return result


def verified_settings_rows(directory: Path):
    """Public source-free verifier; returned raw rows are private, never logged."""
    directory = directory.resolve(strict=True)
    manifest_path = directory / "manifest.json"
    if manifest_path.is_symlink():
        raise SettingsPreservationError("redirected manifest")
    raw_manifest = manifest_path.read_bytes()
    manifest = _read_json(raw_manifest)
    return manifest, _verify_streams(directory, manifest), hashlib.sha256(raw_manifest).hexdigest()


def _verify_streams(directory, manifest):
    if (not isinstance(manifest, dict) or manifest.get("format") != FORMAT or manifest.get("state") != "complete"
            or manifest.get("placement") != "UNRESOLVED"
            or not isinstance(manifest.get("streams"), list) or len(manifest["streams"]) != len(TABLES)):
        raise SettingsPreservationError("incomplete or unknown settings artifact")
    _label(manifest["source_instance"])
    _label(manifest["source_owner"])
    records = {}
    for table, spec in zip(TABLES, manifest["streams"], strict=True):
        filename = table + ".jsonl"
        if (not isinstance(spec, dict) or spec.get("file") != filename or spec.get("columns") != list(TABLES[table])
                or any(type(spec.get(key)) is not int or spec[key] < 0 for key in ("rows", "bytes"))
                or not isinstance(spec.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", spec["sha256"])):
            raise SettingsPreservationError("unexpected settings stream schema")
        path = directory / filename
        if path.is_symlink() or not path.is_file():
            raise SettingsPreservationError("missing or redirected settings stream")
        digest, size, count = hashlib.sha256(), 0, 0
        with path.open("rb") as stream:
            for line in stream:
                digest.update(line)
                size += len(line)
                record = _read_json(line)
                expected_id = f"{table}:{count}"
                if (not isinstance(record, dict) or set(record) != {"record_id", "row"} or record["record_id"] != expected_id
                        or not isinstance(record["row"], list) or len(record["row"]) != len(TABLES[table])):
                    raise SettingsPreservationError("settings row identity/schema changed")
                tuple(decode_value(value) for value in record["row"])
                records[expected_id] = {"relation": table, **record}
                count += 1
        if (count, size, digest.hexdigest()) != (spec["rows"], spec["bytes"], spec["sha256"]):
            raise SettingsPreservationError("settings stream count/bytes/digest mismatch")
        if type(spec.get("present")) is not bool or not spec["present"] and count:
            raise SettingsPreservationError("absent settings relation contains rows")
    summary = _summary(records)
    if summary != manifest.get("verification"):
        raise SettingsPreservationError("settings relationship summary changed")
    return records


def verify_artifact(directory: Path):
    manifest, _, digest = verified_settings_rows(directory)
    return {"state": "verified_artifact_only", "manifest_sha256": digest,
            "source_instance": manifest["source_instance"], **manifest["verification"]}


def _capture_settings_streams(connection, stage, metrics, *, available=None, describe_layout=False):
    """Borrow the sole snapshot; settings v1 keeps its exact four stream contract."""
    if available is None:
        available = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?,?,?,?)", tuple(TABLES))}
        metrics["selects"] += 1
    streams = []
    for table, columns in TABLES.items():
        digest, count, size, layout = hashlib.sha256(), 0, 0, None
        with (stage / (table + ".jsonl")).open("xb") as stream:
            if table in available:
                info = connection.execute(f"PRAGMA table_info({table})").fetchall()
                metrics["schema_pragmas"] += 1
                if [row[1] for row in info] != list(columns):
                    raise SettingsPreservationError("unsupported settings source schema")
                if describe_layout:
                    layout = _state_source_layout(connection, table, info, metrics)
                    if not _state_layout_supported(table, layout):
                        raise SettingsPreservationError("unsupported current-state source schema")
                cursor = connection.execute(f"SELECT {','.join(columns)} FROM {table} ORDER BY rowid")
                metrics["selects"] += 1
                metrics["data_selects"] += 1
                while batch := cursor.fetchmany(512):
                    for values in batch:
                        record = {"record_id": f"{table}:{count}", "row": [encode_value(value) for value in values]}
                        line = (_json(record) + "\n").encode("utf-8")
                        stream.write(line)
                        digest.update(line)
                        size += len(line)
                        count += 1
        spec = dict(file=table + ".jsonl", columns=list(columns), present=table in available,
                    rows=count, bytes=size, sha256=digest.hexdigest())
        if describe_layout:
            spec["source_layout"] = layout
        streams.append(spec)
    return streams


def capture_settings(*, database: Path, source_instance: str, source_owner: str,
                     output: Path, sqlite_frozen=False, artifact_now=None):
    _label(source_instance)
    _label(source_owner)
    source = database.resolve(strict=True)
    output = output.resolve()
    if output == source or output.exists() or output.is_symlink() or source in output.parents:
        raise SettingsPreservationError("output must be a new separate artifact")
    stage = output.with_name(output.name + ".partial-" + uuid.uuid4().hex)
    stage.mkdir(parents=True, exist_ok=False)
    metrics = dict(connections=0, begin=0, rollback=0, selects=0, data_selects=0,
                   schema_pragmas=0, source_dml=0, source_ddl=0, checkpoints=0, sql_commits=0)
    try:
        streams = []
        with readonly_sqlite_snapshot(source, frozen=sqlite_frozen, metrics=metrics) as connection:
            streams = _capture_settings_streams(connection, stage, metrics)
        manifest = dict(format=FORMAT, state="complete", artifact_id=uuid.uuid4().hex,
                        source_instance=source_instance, source_owner=source_owner, placement="UNRESOLVED",
                        artifact_at=_clock(artifact_now), clock="explicit offline KST receipt; original values/versions unchanged",
                        snapshot="explicit_frozen_no_sidecars" if sqlite_frozen else "readonly_native_snapshot_honors_WAL",
                        streams=streams, metrics=metrics)
        records = {}
        for table in TABLES:
            for line in (stage / (table + ".jsonl")).read_text(encoding="utf-8").splitlines():
                record = _read_json(line)
                records[record["record_id"]] = {"relation": table, **record}
        manifest["verification"] = _summary(records)
        (stage / "prepared-manifest.json").write_text(_json(manifest) + "\n", encoding="utf-8")
        _verify_streams(stage, manifest)
        # Publish only a verified whole directory, without overwriting a prior output.
        if output.exists():
            raise SettingsPreservationError("output was claimed concurrently")
        stage.rename(output)
        os.link(output / "prepared-manifest.json", output / "manifest.json")
        return {"state": "preserved_not_migrated", "source_instance": source_instance,
                **manifest["verification"], "metrics": metrics}
    except BaseException as error:
        failed = stage if stage.is_dir() else output
        if failed.is_dir() and not (failed / "manifest.json").exists():
            (failed / "failure.json").write_text(_json({"state": "incomplete", "error_type": type(error).__name__, "metrics": metrics}), encoding="utf-8")
        raise


def select_rows(artifact: Path, selection: dict):
    manifest, records, digest = verified_settings_rows(artifact)
    if (not isinstance(selection, dict) or selection.get("format") != "current-settings-selection/v1"
            or selection.get("manifest_sha256") != digest
            or selection.get("source_instance") != manifest["source_instance"]):
        raise SettingsPreservationError("selection does not bind this source artifact")
    role = _label(selection.get("target_role"))
    entries = selection.get("records")
    if not isinstance(entries, list):
        raise SettingsPreservationError("explicit record placement is required")
    selected, seen, reference_pending = set(), set(), set()
    for entry in entries:
        record_id = entry.get("record_id") if isinstance(entry, dict) else None
        if not isinstance(record_id, str) or record_id not in records or record_id in seen:
            raise SettingsPreservationError("unknown or repeated selected record identity")
        seen.add(record_id)
        placement = entry.get("placement")
        if placement == "TRANSFER" and entry.get("target_role") == role:
            selected.add(record_id)
        elif placement == "REFERENCE_PENDING":
            reference_pending.add(record_id)
        elif placement != "UNRESOLVED":
            raise SettingsPreservationError("invalid explicit record placement")
    # Existing matching versions are part of a selected value's exact native state.
    for table, versions in (("settings", "central_setting_versions"), ("column_settings", "central_column_setting_versions")):
        keys = {_json(records[key]["row"][0]) for key in selected
                if records[key]["relation"] == table and records[key]["row"][0][0] != "null"}
        for key, row in records.items():
            if row["relation"] == versions and _json(row["row"][0]) in keys:
                if key in reference_pending or key in seen and key not in selected:
                    raise SettingsPreservationError("selected current value requires its original version")
                selected.add(key)
    rows = {table: [] for table in TABLES}
    for key, record in records.items():
        if key in selected:
            rows[record["relation"]].append(tuple(decode_value(value) for value in record["row"]))
    counts = dict(captured=len(records), selected=len(selected), unselected=len(records)-len(selected),
                  unresolved=len(records)-len(selected), reference_pending=len(reference_pending))
    binding = dict(source_instance=manifest["source_instance"], source_owner=manifest["source_owner"],
                   manifest_sha256=digest, selection_sha256=hashlib.sha256(_json(selection).encode()).hexdigest(),
                   target_role=role, counts=counts)
    return rows, binding


def accept_settings(*, artifact: Path, selection: dict, target: Path, operation_id: str,
                    target_id: str, receipt_now=None):
    rows, binding = select_rows(artifact, selection)
    if not binding["counts"]["selected"]:
        raise SettingsPreservationError("no records explicitly selected")
    operation_id, target_id = _label(operation_id), _label(target_id)
    if target.absolute().resolve() != target.absolute() or target.is_symlink():
        raise SettingsPreservationError("redirected offline target")
    target = target.resolve()
    ownership = dict(format="offline-settings-target/v1", operation_id=operation_id,
                     target_id=target_id, **binding)
    if not target.exists():
        target.mkdir(parents=True, exist_ok=False)
        with (target / OWNER_FILE).open("x", encoding="utf-8") as stream:
            stream.write(_json(ownership) + "\n")
    elif target.is_symlink() or not target.is_dir():
        raise SettingsPreservationError("target must be an owned offline directory")
    from kiwoom_monitor.infrastructure.persistence.settings_backup import SettingsBackupService
    receipt, replayed, metrics = SettingsBackupService(target / "offline.sqlite3").accept_preserved_current_settings(
        rows=rows, ownership=ownership, receipt_at=lambda: _clock(receipt_now))
    # Native receipt is now authoritative; this pre-accept ownership marker has no ongoing role.
    marker = target / OWNER_FILE
    if marker.exists():
        if marker.is_symlink() or _read_json(marker.read_bytes()) != ownership:
            raise SettingsPreservationError("target cleanup ownership conflict")
        marker.unlink()
    return {"state": "selected_raw_settings_accepted_only", "runtime_state": "OFFLINE_UNBOUND",
            "order_authority": False, "overall_P2_complete": False, "replayed": replayed,
            "receipt": receipt, "metrics": metrics}


def install_settings(*, artifact: Path, selection: dict, accepted_target: Path, target: Path, layout: str = "settings"):
    """Install verified selected settings into a separate explicit role store.

    The raw artifact, selection and P2 carrier must agree. This command neither
    adopts a source/role nor changes startup paths, sync, credentials or Runtime.
    The P2 receipt remains initial acceptance evidence, not applied authority.
    """
    if layout not in {"settings", "query"}:
        raise SettingsPreservationError("explicit supported role layout is required")
    rows, binding = select_rows(artifact, selection)
    accepted_target = accepted_target.absolute()
    target = target.absolute()
    if (accepted_target.resolve(strict=True) != accepted_target or accepted_target.is_symlink()
            or target.resolve() != target or target.is_symlink()
            or target == accepted_target or accepted_target in target.parents):
        raise SettingsPreservationError("installation requires a separate explicit role target")
    source = accepted_target / "offline.sqlite3"
    with readonly_sqlite_snapshot(source) as connection:
        objects = connection.execute(
            "SELECT name,type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall()
        if ({name for name, kind in objects if kind == "table"} != {*TABLES, "settings_transition_receipts"}
                or any(kind != "table" for _, kind in objects)):
            raise SettingsPreservationError("not an accepted raw settings carrier")
        accepted = connection.execute("SELECT receipt_json FROM settings_transition_receipts").fetchall()
        if len(accepted) != 1:
            raise SettingsPreservationError("one original acceptance receipt is required")
        receipt = _read_json(accepted[0][0])
        if not isinstance(receipt, dict) or not isinstance(receipt.get("accepted_at"), str):
            raise SettingsPreservationError("original aware acceptance time is required")
        from datetime import datetime
        if datetime.fromisoformat(receipt["accepted_at"]).utcoffset() is None:
            raise SettingsPreservationError("original aware acceptance time is required")
        expected = dict(binding, format="offline-current-settings-receipt/v1",
                        operation_id=_label(receipt.get("operation_id")),
                        target_id=_label(receipt.get("target_id")),
                        state="selected_raw_settings_accepted_only", runtime_state="OFFLINE_UNBOUND",
                        order_authority=False, overall_P2_complete=False,
                        accepted_at=receipt.get("accepted_at"))
        if receipt != expected:
            raise SettingsPreservationError("original acceptance does not bind this selection")
        for table, columns in TABLES.items():
            if [row[1] for row in connection.execute(f"PRAGMA table_info({table})")] != list(columns):
                raise SettingsPreservationError("unsupported accepted settings schema")
            actual = connection.execute(f"SELECT {','.join(columns)} FROM {table} ORDER BY rowid").fetchall()
            if ([[encode_value(value) for value in row] for row in actual]
                    != [[encode_value(value) for value in row] for row in rows[table]]):
                raise SettingsPreservationError("accepted raw settings no longer match original selection")
    from kiwoom_monitor.infrastructure.persistence.database import Database
    initialize = Database(target).initialize_query_role if layout == "query" else Database(target).initialize_role_settings
    return initialize(role=binding["target_role"], preserved_rows=rows, preservation_receipt=receipt)


CURRENT_STATE_FORMAT = "preserved-current-state/v1"
CURRENT_STATE_SELECTION_FORMAT = "current-state-selection/v1"
CURRENT_STATE_TABLES = {
    **TABLES,
    "theme_profiles": ("profile_id", "profile_name"),
    "profile_themes": ("profile_id", "theme_name", "default_color"),
    "profile_stock_themes": ("profile_id", "stock_code", "theme_name", "custom_color"),
    "profile_theme_name_decisions": (
        "profile_id", "decision_kind", "source_name", "target_name", "decision_source", "updated_at"),
    "profile_theme_suggestions": (
        "profile_id", "stock_code", "news_identity", "raw_theme_name", "evidence", "confidence",
        "provider", "model", "body_hash", "analyzed_at", "status", "reviewed_theme_names",
        "reviewed_at", "article_title", "article_published_at", "article_url"),
    "stocks": ("code", "name", "market", "updated_at"),
    "stock_aliases": ("alias", "stock_code"),
}
_STATE_PK = {
    "settings": (0,), "column_settings": (0,), "central_setting_versions": (0,),
    "central_column_setting_versions": (0,), "theme_profiles": (0,),
    "profile_themes": (0, 1), "profile_stock_themes": (0, 1, 2),
    "profile_theme_name_decisions": (0, 1, 2, 3), "profile_theme_suggestions": (0, 1, 2, 3),
    "stocks": (0,), "stock_aliases": (0,),
}
_ASCII_NOCASE = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
_STATE_NOCASE = {"profile_themes": {1}, "profile_stock_themes": {2},
                 "profile_theme_name_decisions": {2, 3}, "profile_theme_suggestions": {3}}


def _state_layout_supported(table, layout):
    if (not isinstance(layout, dict) or not isinstance(layout.get("columns"), list)
            or not all(type(name) is str for name in layout["columns"])
            or len(set(layout["columns"])) != len(layout["columns"])
            or (not set(CURRENT_STATE_TABLES[table]) <= set(layout["columns"]) if table == "stocks"
                else layout["columns"] != list(CURRENT_STATE_TABLES[table]))
            or layout.get("primary_key") != [CURRENT_STATE_TABLES[table][index] for index in _STATE_PK[table]]
            or not isinstance(layout.get("foreign_keys"), list)
            or not isinstance(layout.get("unique_keys"), list)):
        return False
    required = [[CURRENT_STATE_TABLES[table][index], "NOCASE" if index in _STATE_NOCASE.get(table, ()) else "BINARY"]
                for index in _STATE_PK[table]]
    if table == "theme_profiles":
        return layout.get("profile_id_type") == "INTEGER" and [["profile_name", "NOCASE"]] in layout["unique_keys"]
    return required in layout["unique_keys"]


def _state_key(table, row):
    """Mirror native INTEGER identity/TEXT key collations, never strip/casefold."""
    key = []
    for index in _STATE_PK[table]:
        value = row[index]
        integer = table.startswith("profile_") or table == "theme_profiles"
        if integer and index == 0:
            if type(value) is not int:
                return None
        elif type(value) is not str:
            return None
        if index in _STATE_NOCASE.get(table, ()):
            value = value.translate(_ASCII_NOCASE)
        key.append(value)
    return tuple(key)


def _state_references(records, present, selected=None):
    """Private source/selection diagnostics contain row IDs, never invented keys."""
    decoded = {key: tuple(decode_value(value) for value in item["row"]) for key, item in records.items()}
    indexes = {table: {} for table in CURRENT_STATE_TABLES}
    names = {}
    for key, item in records.items():
        row, table = decoded[key], item["relation"]
        identity = _state_key(table, row)
        if identity is not None:
            indexes[table].setdefault(identity, []).append(key)
        if table == "theme_profiles" and type(row[1]) is str:
            names.setdefault((row[1].translate(_ASCII_NOCASE),), []).append(key)
    references = []

    def reference(record_id, name, parent, key, *, kind="native", original_version=False, index=None):
        candidates = (indexes[parent] if index is None else index).get(key, []) if key is not None else []
        if key is None:
            state = "unknown_reference_type"
        elif not present[parent]:
            state = "source_relation_absent"
        elif not candidates:
            state = "missing_in_source"
        elif len(candidates) != 1:
            state = "ambiguous_in_source"
        elif selected is not None and candidates[0] not in selected:
            state = "unselected_reference"
        else:
            state = "selected" if selected is not None else "present_in_source"
        # Missing original versions remain missing, as in settings v1. Existing
        # versions still require explicit placement; no version is regenerated.
        blocking = state not in {"selected", "present_in_source"} and not (
            original_version and state in {"source_relation_absent", "missing_in_source"})
        references.append(dict(record_id=record_id, reference=name, kind=kind,
                               state=state, candidates=candidates, blocking=blocking))

    version_pairs = {
        "settings": ("central_setting_versions", "original_version"),
        "column_settings": ("central_column_setting_versions", "original_version"),
        "central_setting_versions": ("settings", "original_value"),
        "central_column_setting_versions": ("column_settings", "original_value"),
    }
    for key, item in records.items():
        if selected is not None and key not in selected:
            continue
        row, table = decoded[key], item["relation"]
        reference(key, "row_identity", table, _state_key(table, row))
        profile_id = (row[0],) if type(row[0]) is int else None
        if table in {"profile_themes", "profile_theme_name_decisions", "profile_theme_suggestions"}:
            reference(key, "profile", "theme_profiles", profile_id)
        if table == "theme_profiles":
            name = (row[1].translate(_ASCII_NOCASE),) if type(row[1]) is str else None
            reference(key, "unique_profile_name", "theme_profiles", name, index=names)
        elif table == "profile_stock_themes":
            parent = (row[0], row[2].translate(_ASCII_NOCASE)) if profile_id and type(row[2]) is str else None
            reference(key, "theme", "profile_themes", parent)
        if table in {"profile_stock_themes", "profile_theme_suggestions", "stock_aliases"}:
            code = row[1]
            reference(key, "stock_catalog", "stocks", (code,) if type(code) is str else None,
                      kind="native" if table == "stock_aliases" else "consumer_catalog")
        if table == "settings" and row[0] == "theme_active_profile":
            active = (row[1].translate(_ASCII_NOCASE),) if type(row[1]) is str else None
            reference(key, "active_profile", "theme_profiles", active, kind="consumer_active", index=names)
        if table in version_pairs:
            parent, name = version_pairs[table]
            reference(key, name, parent, (row[0],) if type(row[0]) is str else None, original_version=True)
    states = {}
    for item in references:
        states[item["state"]] = states.get(item["state"], 0) + 1
    summary = dict(records=len(records) if selected is None else len(selected),
                   references=len(references), blocking=sum(item["blocking"] for item in references), states=states)
    return {"summary": summary, "references": references}


def verified_current_state_rows(directory: Path):
    """Source-free raw verifier. Returned rows/references are private material."""
    directory = directory.absolute()
    if directory.resolve(strict=True) != directory or directory.is_symlink():
        raise SettingsPreservationError("redirected theme artifact")
    path = directory / "manifest.json"
    if path.is_symlink():
        raise SettingsPreservationError("redirected theme manifest")
    raw = path.read_bytes()
    manifest = _read_json(raw)
    records = _verify_current_state_streams(directory, manifest)
    return manifest, records, hashlib.sha256(raw).hexdigest()


def _verify_current_state_streams(directory, manifest):
    if (not isinstance(manifest, dict) or manifest.get("format") != CURRENT_STATE_FORMAT
            or manifest.get("state") != "complete" or manifest.get("placement") != "UNRESOLVED"
            or not isinstance(manifest.get("streams"), list) or len(manifest["streams"]) != len(CURRENT_STATE_TABLES)):
        raise SettingsPreservationError("incomplete or unknown current-state artifact")
    for field in ("source_instance", "source_owner", "operation_id"):
        _label(manifest.get(field))
    records, present = {}, {}
    for table, spec in zip(CURRENT_STATE_TABLES, manifest["streams"], strict=True):
        columns = CURRENT_STATE_TABLES[table]
        if (not isinstance(spec, dict) or spec.get("file") != table + ".jsonl"
                or spec.get("columns") != list(columns) or type(spec.get("present")) is not bool
                or any(type(spec.get(key)) is not int or spec[key] < 0 for key in ("rows", "bytes"))
                or not isinstance(spec.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", spec["sha256"])):
            raise SettingsPreservationError("unexpected current-state stream schema")
        layout = spec.get("source_layout")
        if spec["present"]:
            if not _state_layout_supported(table, layout):
                raise SettingsPreservationError("unsupported current-state source layout")
        elif layout is not None or spec["rows"]:
            raise SettingsPreservationError("absent theme relation contains material")
        path = directory / spec["file"]
        if path.is_symlink() or not path.is_file():
            raise SettingsPreservationError("missing or redirected theme stream")
        digest, size, count = hashlib.sha256(), 0, 0
        with path.open("rb") as stream:
            for line in stream:
                digest.update(line)
                size += len(line)
                item = _read_json(line)
                key = f"{table}:{count}"
                if (not isinstance(item, dict) or set(item) != {"record_id", "row"}
                        or item["record_id"] != key or not isinstance(item["row"], list)
                        or len(item["row"]) != len(columns)):
                    raise SettingsPreservationError("theme row identity/schema changed")
                values = tuple(decode_value(value) for value in item["row"])
                records[key] = {"relation": table, **item}
                count += 1
        if (count, size, digest.hexdigest()) != (spec["rows"], spec["bytes"], spec["sha256"]):
            raise SettingsPreservationError("theme stream count/bytes/digest mismatch")
        present[table] = spec["present"]
    if _state_references(records, present)["summary"] != manifest.get("verification"):
        raise SettingsPreservationError("current-state relationship summary changed")
    return records


def verify_current_state_artifact(directory: Path):
    manifest, _, digest = verified_current_state_rows(directory)
    return dict(state="verified_current_state_artifact_only", manifest_sha256=digest,
                source_instance=manifest["source_instance"], **manifest["verification"],
                runtime_state="OFFLINE_UNBOUND", consumer_status="NOT_EVALUATED", source_authenticity="UNVERIFIED",
                installed=False, applied=False, order_authority=False)


def _state_source_layout(connection, table, info, metrics):
    """Inspect a borrowed table without repeating table_info or owning a DB."""
    foreign = connection.execute(f"PRAGMA foreign_key_list({table})").fetchall()
    indexes = connection.execute(f"PRAGMA index_list({table})").fetchall()
    metrics["schema_pragmas"] += 2
    layout = dict(columns=[row[1] for row in info],
                  primary_key=[row[1] for row in sorted(info, key=lambda row: row[5]) if row[5]],
                  foreign_keys=[list(row) for row in foreign], unique_keys=[])
    if table == "theme_profiles":
        layout["profile_id_type"] = info[0][2].upper() if info else ""
    for index in indexes:
        if index[2]:
            quoted = '"' + index[1].replace('"', '""') + '"'
            index_info = connection.execute(f"PRAGMA index_xinfo({quoted})").fetchall()
            metrics["schema_pragmas"] += 1
            layout["unique_keys"].append([[row[2], row[4]] for row in index_info if row[5]])
    return layout


def _capture_theme_streams(connection, stage, metrics, available):
    """Borrow the same source cut for the seven non-settings relations."""
    streams = []
    for table, columns in CURRENT_STATE_TABLES.items():
        if table in TABLES:
            continue
        layout, digest, count, size = None, hashlib.sha256(), 0, 0
        with (stage / (table + ".jsonl")).open("xb") as stream:
            if table in available:
                info = connection.execute(f"PRAGMA table_info({table})").fetchall()
                metrics["schema_pragmas"] += 1
                layout = _state_source_layout(connection, table, info, metrics)
                if not _state_layout_supported(table, layout):
                    raise SettingsPreservationError("unsupported current-state source schema")
                where = ""
                if table == "stocks":
                    references = [f"SELECT stock_code FROM {name}" for name in (
                        "profile_stock_themes", "profile_theme_suggestions", "stock_aliases") if name in available]
                    where = " WHERE code IN (" + " UNION ALL ".join(references) + ")" if references else " WHERE 0"
                cursor = connection.execute(f"SELECT {','.join(columns)} FROM {table}{where} ORDER BY rowid")
                metrics["selects"] += 1
                metrics["data_selects"] += 1
                while batch := cursor.fetchmany(512):
                    for values in batch:
                        item = dict(record_id=f"{table}:{count}", row=[encode_value(value) for value in values])
                        line = (_json(item) + "\n").encode("utf-8")
                        stream.write(line)
                        digest.update(line)
                        size += len(line)
                        count += 1
        streams.append(dict(file=table + ".jsonl", columns=list(columns), present=table in available,
                            source_layout=layout, rows=count, bytes=size, sha256=digest.hexdigest()))
    return streams


def capture_current_state(*, database: Path, source_instance: str, source_owner: str,
                          operation_id: str, output: Path, sqlite_frozen=False, artifact_now=None):
    """Preserve settings4 + theme7 in one cut, without selecting or installing.

    The unshipped theme-only artifact is replaced, not maintained as a second
    compatibility format. Settings v1 remains an independent four-table consumer.
    Raw values, including unresolved settings/recovery, are private source data;
    their presence here is neither complete recovery closure nor transfer policy.
    """
    identity = dict(source_instance=_label(source_instance), source_owner=_label(source_owner),
                    operation_id=_label(operation_id))
    output = output.absolute()
    if output.resolve() != output or output.is_symlink():
        raise SettingsPreservationError("redirected current-state output")
    if output.exists():
        manifest, _, digest = verified_current_state_rows(output)
        if any(manifest[key] != value for key, value in identity.items()):
            raise SettingsPreservationError("current-state capture operation conflict")
        return dict(state="verified_current_state_artifact_only", manifest_sha256=digest,
                    source_instance=manifest["source_instance"], **manifest["verification"], replayed=True,
                    runtime_state="OFFLINE_UNBOUND", consumer_status="NOT_EVALUATED", source_authenticity="UNVERIFIED",
                    installed=False, applied=False, order_authority=False)
    source = database.resolve(strict=True)
    if output == source or source in output.parents:
        raise SettingsPreservationError("current-state output must be separate")
    stage = output.with_name(output.name + ".partial-" + uuid.uuid4().hex)
    stage.mkdir(parents=True, exist_ok=False)
    metrics = dict(connections=0, begin=0, rollback=0, selects=0, data_selects=0,
                   schema_pragmas=0, source_dml=0, source_ddl=0, checkpoints=0, sql_commits=0)
    try:
        with readonly_sqlite_snapshot(source, frozen=sqlite_frozen, metrics=metrics) as connection:
            available = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            metrics["selects"] += 1
            streams = _capture_settings_streams(connection, stage, metrics, available=available, describe_layout=True)
            streams.extend(_capture_theme_streams(connection, stage, metrics, available))
        manifest = dict(format=CURRENT_STATE_FORMAT, state="complete", placement="UNRESOLVED", **identity,
                        artifact_at=_clock(artifact_now),
                        clock="explicit offline KST receipt; original clocks/versions unchanged",
                        snapshot="explicit_frozen_no_sidecars" if sqlite_frozen else "readonly_native_snapshot_honors_WAL",
                        cut="settings4 and theme7 read once under the same native readonly snapshot owner",
                        projection="stocks: code/name/market/updated_at for directly referenced codes; other relations raw",
                        excluded="past news/bars/journal/research and unreferenced catalog; no vault files",
                        privacy="raw settings/recovery/edits remain private; explicit selection is separate from capture",
                        streams=streams, metrics=metrics)
        records = {}
        for table in CURRENT_STATE_TABLES:
            for line in (stage / (table + ".jsonl")).read_text(encoding="utf-8").splitlines():
                item = _read_json(line)
                records[item["record_id"]] = {"relation": table, **item}
        manifest["verification"] = _state_references(records, {table: spec["present"] for table, spec in zip(CURRENT_STATE_TABLES, streams, strict=True)})["summary"]
        raw_manifest = (_json(manifest) + "\n").encode("utf-8")
        (stage / "prepared-manifest.json").write_bytes(raw_manifest)
        _verify_current_state_streams(stage, manifest)
        result = dict(state="verified_current_state_artifact_only", manifest_sha256=hashlib.sha256(raw_manifest).hexdigest(),
                      source_instance=manifest["source_instance"], **manifest["verification"], replayed=False,
                      runtime_state="OFFLINE_UNBOUND", consumer_status="NOT_EVALUATED", source_authenticity="UNVERIFIED",
                      installed=False, applied=False, order_authority=False)
        stage.rename(output)
        os.link(output / "prepared-manifest.json", output / "manifest.json")
        return result
    except BaseException as error:
        failed = stage if stage.is_dir() else output
        if failed.is_dir() and not (failed / "manifest.json").exists():
            (failed / "failure.json").write_text(_json(dict(state="incomplete", error_type=type(error).__name__, metrics=metrics)), encoding="utf-8")
        raise


def select_current_state_rows(artifact: Path, selection: dict):
    """Return explicit raw rows/binding/reference diagnostics, never a target write.

    CLOSED covers only explicitly selected records, not all current user material
    or source authenticity. Missing original versions are reported, not invented.
    Name decisions/news identities remain raw evidence, not fabricated FK edges.
    """
    manifest, records, digest = verified_current_state_rows(artifact)
    if (not isinstance(selection, dict) or selection.get("format") != CURRENT_STATE_SELECTION_FORMAT
            or selection.get("manifest_sha256") != digest
            or any(selection.get(key) != manifest[key] for key in ("source_instance", "source_owner", "operation_id"))
            or selection.get("target_layout", "query") not in ("query", "full")
            or not isinstance(selection.get("records"), list)):
        raise SettingsPreservationError("current-state selection does not bind this source operation")
    role, selected, seen, placements = _label(selection.get("target_role")), set(), set(), {}
    for entry in selection["records"]:
        key = entry.get("record_id") if isinstance(entry, dict) else None
        if not isinstance(key, str) or key not in records or key in seen:
            raise SettingsPreservationError("unknown or repeated current-state record identity")
        seen.add(key)
        if entry.get("placement") == "TRANSFER" and entry.get("target_role") == role:
            selected.add(key)
        elif entry.get("placement") not in {"UNRESOLVED", "REFERENCE_PENDING", "RETAIN_SOURCE"}:
            raise SettingsPreservationError("invalid explicit current-state placement")
        placements[key] = entry["placement"]
    present = {table: spec["present"] for table, spec in zip(CURRENT_STATE_TABLES, manifest["streams"], strict=True)}
    closure = _state_references(records, present, selected)
    for item in closure["references"]:
        item["placements"] = [dict(record_id=key, placement=placements.get(key, "UNRESOLVED"))
                              for key in item["candidates"]]
    closure["state"] = "EMPTY" if not selected else "INCOMPLETE" if closure["summary"]["blocking"] else "CLOSED"
    rows = {table: [] for table in CURRENT_STATE_TABLES}
    for key, item in records.items():
        if key in selected:
            rows[item["relation"]].append(tuple(decode_value(value) for value in item["row"]))
    binding = dict(**{key: manifest[key] for key in ("source_instance", "source_owner", "operation_id")},
                   manifest_sha256=digest, selection_sha256=hashlib.sha256(_json(selection).encode()).hexdigest(),
                   target_role=role, captured=len(records), selected=len(selected), unselected=len(records)-len(selected),
                   unresolved=sum(placements.get(key, "UNRESOLVED") == "UNRESOLVED" for key in records),
                   reference_pending=sum(value == "REFERENCE_PENDING" for value in placements.values()),
                   retained_source=sum(value == "RETAIN_SOURCE" for value in placements.values()))
    result = dict(rows=rows, binding=binding, closure=closure, source_relations=present,
                source_layouts={table: spec["source_layout"] for table, spec in zip(CURRENT_STATE_TABLES, manifest["streams"], strict=True)},
                runtime_state="OFFLINE_UNBOUND", consumer_status="NOT_EVALUATED", source_authenticity="UNVERIFIED",
                applied=False, order_authority=False, installed=False)
    if selection.get("target_layout") == "full":
        result["target_layout"] = "full"
    return result


def install_current_state(*, artifact: Path, selection: dict, target: Path,
                          operation_id: str, target_id: str, receipt_now=None, layout="query"):
    """Seed one explicit offline desktop target directly from one verified cut.

    Source capture/selection stays private. This creates no settings carrier,
    applied state, source trust, runtime role, recovery lease or order authority.
    An ambiguous native error is returned only after its owner exits; resolution
    is a separate explicit invocation with the same immutable target plan.
    """
    if layout not in ("query", "full") or not isinstance(selection, dict) or selection.get("target_layout", "query") != layout:
        raise SettingsPreservationError("installation requires an explicitly matching target layout")
    selected = select_current_state_rows(artifact, selection)
    if selected["closure"]["state"] != "CLOSED":
        raise SettingsPreservationError("current-state selection references remain unresolved")
    artifact, target = artifact.absolute(), target.absolute()
    if (target.resolve() != target or target.is_symlink()
            or target == artifact or artifact in target.parents):
        raise SettingsPreservationError("installation requires a separate explicit desktop target")
    selected.update(target_operation_id=_label(operation_id), target_id=_label(target_id))
    from kiwoom_monitor.infrastructure.persistence.database import Database
    initialize = Database(target).initialize_full_role if layout == "full" else Database(target).initialize_query_role
    return initialize(
        role=selected["binding"]["target_role"], preserved_state=selected,
        receipt_at=lambda: _clock(receipt_now),
    )


def verified_journal_settings_rows(artifact: Path):
    """Read the existing fixed3 envelope without opening its source or consumers."""
    from kiwoom_monitor.infrastructure.persistence.journal_database import JOURNAL_SELECTED_SETTING_KEYS
    artifact = Path(artifact)
    if artifact.is_symlink() or not artifact.is_file():
        raise SettingsPreservationError("a source-free fixed3 artifact is required")
    envelope = _read_json(artifact.read_bytes())
    if not isinstance(envelope, dict) or set(envelope) != {"payload", "sha256"}:
        raise SettingsPreservationError("invalid fixed3 envelope")
    payload = envelope["payload"]
    if (not isinstance(payload, dict) or set(payload) != {
            "purpose", "source_instance", "operation_id", "rows", "missing_keys"}
            or payload["purpose"] != "journal3-once"
            or any(not isinstance(payload[key], str)
                   or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", payload[key])
                   for key in ("source_instance", "operation_id"))
            or hashlib.sha256(_json(payload).encode("utf-8")).hexdigest() != envelope["sha256"]
            or not isinstance(payload["rows"], list) or len(payload["rows"]) > 3):
        raise SettingsPreservationError("fixed3 identity or digest conflict")
    rows, seen = [], set()
    for encoded in payload["rows"]:
        if not isinstance(encoded, list) or len(encoded) != 3:
            raise SettingsPreservationError("invalid fixed3 row")
        row = tuple(decode_value(value) for value in encoded)
        if (row[0] not in JOURNAL_SELECTED_SETTING_KEYS or row[0] in seen
                or [encode_value(value) for value in row] != encoded):
            raise SettingsPreservationError("fixed3 key or primitive conflict")
        seen.add(row[0])
        rows.append(row)
    if ([row[0] for row in rows] != sorted(seen)
            or payload["missing_keys"] != sorted(set(JOURNAL_SELECTED_SETTING_KEYS) - seen)):
        raise SettingsPreservationError("fixed3 order or absence conflict")
    return payload, rows, envelope["sha256"]


def install_journal_settings(*, artifact: Path, full_data_dir: Path, full_settings_role: str,
                             operation_id: str, target_id: str, receipt_now=None):
    """Bind fixed3 to an existing offline full owner, never a scratch carrier."""
    payload, rows, digest = verified_journal_settings_rows(artifact)
    role, operation_id, target_id = map(_label, (full_settings_role, operation_id, target_id))
    from kiwoom_monitor.infrastructure.app_paths import AppPaths
    from kiwoom_monitor.infrastructure.persistence.database import Database
    from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository
    from kiwoom_monitor.infrastructure.persistence.strict_restore import AppDataLease
    paths = AppPaths.for_full_role(full_data_dir)
    with AppDataLease(paths.data_dir, exclusive=True):
        monitor = Database(paths.database_path).initialize_full_role(role=role, allow_create=False)
        ownership = dict(
            format="selected-journal-settings-target/v1", operation_id=operation_id, target_id=target_id,
            source_instance=payload["source_instance"], source_operation_id=payload["operation_id"],
            artifact_sha256=digest, monitor_role=role, monitor_seed_sha256=monitor["seed_sha256"],
            monitor_layout_sha256=monitor["owned_layout_sha256"],
            runtime_state="OFFLINE_UNBOUND", order_authority=False,
        )
        return JournalRepository.accept_preserved_current_settings(
            paths.journal_database_path, rows=rows, ownership=ownership,
            receipt_at=lambda: _clock(receipt_now),
        )


def main(argv=None):
    from kiwoom_monitor.infrastructure.persistence.strict_restore import StrictRestoreError
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("capture")
    capture.add_argument("--database", type=Path, required=True)
    capture.add_argument("--source-instance", required=True)
    capture.add_argument("--source-owner", required=True)
    capture.add_argument("--output", type=Path, required=True)
    capture.add_argument("--sqlite-frozen", action="store_true")
    verify = commands.add_parser("verify")
    verify.add_argument("--artifact", type=Path, required=True)
    accept = commands.add_parser("accept")
    accept.add_argument("--artifact", type=Path, required=True)
    accept.add_argument("--selection", type=Path, required=True)
    accept.add_argument("--target", type=Path, required=True)
    accept.add_argument("--operation-id", required=True)
    accept.add_argument("--target-id", required=True)
    install = commands.add_parser("install")
    install.add_argument("--artifact", type=Path, required=True)
    install.add_argument("--selection", type=Path, required=True)
    install.add_argument("--accepted-target", type=Path, required=True)
    install.add_argument("--target", type=Path, required=True)
    install.add_argument("--layout", choices=("settings", "query"), default="settings")
    theme = commands.add_parser("capture-state")
    theme.add_argument("--database", type=Path, required=True)
    theme.add_argument("--source-instance", required=True)
    theme.add_argument("--source-owner", required=True)
    theme.add_argument("--operation-id", required=True)
    theme.add_argument("--output", type=Path, required=True)
    theme.add_argument("--sqlite-frozen", action="store_true")
    theme_verify = commands.add_parser("verify-state")
    theme_verify.add_argument("--artifact", type=Path, required=True)
    theme_select = commands.add_parser("select-state")
    theme_select.add_argument("--artifact", type=Path, required=True)
    theme_select.add_argument("--selection", type=Path, required=True)
    state_install = commands.add_parser("install-state")
    state_install.add_argument("--artifact", type=Path, required=True)
    state_install.add_argument("--selection", type=Path, required=True)
    state_install.add_argument("--target", type=Path, required=True)
    state_install.add_argument("--operation-id", required=True)
    state_install.add_argument("--target-id", required=True)
    state_install.add_argument("--layout", choices=("query", "full"), default="query")
    journal_install = commands.add_parser("install-journal-settings")
    journal_install.add_argument("--artifact", type=Path, required=True)
    journal_install.add_argument("--full-data-dir", required=True)
    journal_install.add_argument("--full-settings-role", required=True)
    journal_install.add_argument("--operation-id", required=True)
    journal_install.add_argument("--target-id", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "capture":
            result = capture_settings(database=args.database, source_instance=args.source_instance,
                                      source_owner=args.source_owner, output=args.output, sqlite_frozen=args.sqlite_frozen)
        elif args.command == "verify":
            result = verify_artifact(args.artifact)
        elif args.command == "accept":
            result = accept_settings(artifact=args.artifact, selection=_read_json(args.selection.read_bytes()),
                                     target=args.target, operation_id=args.operation_id, target_id=args.target_id)
        elif args.command == "capture-state":
            result = capture_current_state(database=args.database, source_instance=args.source_instance,
                source_owner=args.source_owner, operation_id=args.operation_id, output=args.output, sqlite_frozen=args.sqlite_frozen)
        elif args.command == "verify-state":
            result = verify_current_state_artifact(args.artifact)
        elif args.command == "select-state":
            selected = select_current_state_rows(args.artifact, _read_json(args.selection.read_bytes()))
            result = dict(state="current_state_selection_only", binding=selected["binding"],
                          closure_state=selected["closure"]["state"], **selected["closure"]["summary"],
                          runtime_state="OFFLINE_UNBOUND", installed=False, applied=False, order_authority=False,
                          consumer_status="NOT_EVALUATED", source_authenticity="UNVERIFIED")
        elif args.command == "install-state":
            accepted = install_current_state(artifact=args.artifact, selection=_read_json(args.selection.read_bytes()),
                target=args.target, operation_id=args.operation_id, target_id=args.target_id, layout=args.layout)
            # The native/callable receipt stays private. Public CLI output binds
            # this acceptance by opaque hashes and counts, never source schema.
            binding = accepted["receipt"]["plan"]["binding"]
            result = {key: accepted[key] for key in (
                "state", "seed_kind", "seed_sha256", "replayed", "runtime_state",
                "applied", "role_ready", "order_authority", "consumer_status", "source_authenticity")}
            result.update(selected=binding["selected"],
                          relations=len(accepted["receipt"]["plan"]["relation_counts"]),
                          manifest_sha256=binding["manifest_sha256"], selection_sha256=binding["selection_sha256"])
        elif args.command == "install-journal-settings":
            if not args.full_data_dir.strip():
                raise SettingsPreservationError("an explicit nonblank full root is required")
            accepted = install_journal_settings(
                artifact=args.artifact, full_data_dir=Path(args.full_data_dir),
                full_settings_role=args.full_settings_role,
                operation_id=args.operation_id, target_id=args.target_id,
            )
            receipt = accepted["receipt"]
            result = dict(state=accepted["state"], rows=receipt["rows"],
                          missing_keys=len(receipt["missing_keys"]), artifact_sha256=receipt["artifact_sha256"],
                          first_receipt_sha256=hashlib.sha256(_json(receipt).encode("utf-8")).hexdigest(),
                          runtime_state="OFFLINE_UNBOUND", applied=False, order_authority=False)
        else:
            result = install_settings(artifact=args.artifact, selection=_read_json(args.selection.read_bytes()),
                                      accepted_target=args.accepted_target, target=args.target, layout=args.layout)
        print(_json(result))
        return 0
    except (ValueError, OSError, sqlite3.Error, StrictRestoreError) as error:
        # Never echo source DB/driver messages, setting names/values or credential data.
        print(_json({"state": "failed", "error_type": type(error).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
