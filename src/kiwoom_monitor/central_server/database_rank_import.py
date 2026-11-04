"""Transient offline rank preservation using a caller-owned native cursor.

No runtime writer imports this removable one-off transfer command module.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from decimal import Decimal
from datetime import datetime

from .market_observations import as_kst


# Explicit offline preservation import only. No runtime facade/initializer
# dispatch imports an operator or selects a current rank source from this data.
PRESERVED_RANK_COLUMNS = {
    "central_observation_revisions": (
        "accepted_sequence", "revision_id", "observation_key", "schema_version", "source_id", "source_session_id",
        "source_sequence", "kind", "subject", "venue", "effective_at", "received_at", "available_at", "revision_of",
        "payload_hash", "unit", "value_kind", "completeness", "origin", "candidate_universe", "quality_flags_json",
        "clock_quality", "source_ref_json", "payload_json",
    ),
    "central_dataset_snapshots": ("kind", "subject", "snapshot_key", "saved_at", "payload_json"),
    "central_market_data_observation_meta": ("dataset_kind", "subject", "observation_key", "effective_at", "available_at",
        "venue", "unit", "value_kind", "completeness", "origin", "source", "candidate_universe"),
}
PRESERVED_RANK_COLUMNS_V3 = {
    table: columns + (("revision_id", "slot_order_key") if table == "central_dataset_snapshots" else
                      ("revision_id",) if table == "central_market_data_observation_meta" else ())
    for table, columns in PRESERVED_RANK_COLUMNS.items()
}


def _rank_columns(plan):
    version = plan.get("artifact_format", "preserved-rank-history/v1")
    if version == "preserved-rank-history/v1":
        return PRESERVED_RANK_COLUMNS
    if version == "preserved-rank-history/v3":
        return PRESERVED_RANK_COLUMNS_V3
    raise PreservedRankImportConflict("unsupported rank artifact format")


_RANK_TARGETS = "preserved_rank_targets"
_RANK_RECEIPTS = "data_transition_receipts"
_RANK_JSON = {"payload_json", "quality_flags_json", "source_ref_json"}
_RANK_TIMES = {"effective_at", "available_at", "received_at"}


class PreservedRankImportConflict(ValueError):
    """An offline target, immutable plan, content or receipt does not match."""


def _rank_document(cursor, pg, collection, owner, key):
    p = "%s" if pg else "?"
    cursor.execute(f"SELECT document_json FROM central_documents WHERE collection={p} AND owner={p} AND document_key={p}",
                   (collection, owner, key))
    row = cursor.fetchone()
    return None if row is None else (row[0] if isinstance(row[0], dict) else json.loads(row[0]))


def _rank_insert_document(cursor, pg, collection, owner, key, value, at):
    p = "%s" if pg else "?"
    cursor.execute("INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
                   f"VALUES({p},{p},{p},{p},{p})", (collection, owner, key, at.timestamp(),
                   json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)))


def _rank_lock(cursor, pg, owner):
    if pg:
        if cursor.connection.autocommit or cursor.connection.info.transaction_status.name != "INTRANS":
            raise PreservedRankImportConflict("native rank command requires a caller-owned transaction")
        cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("preserved_rank:" + owner,))
        cursor.execute("SET LOCAL synchronous_commit TO ON")
    else:
        if not cursor.connection.in_transaction:
            raise PreservedRankImportConflict("native rank command requires a caller-owned transaction")
        # The caller acquires BEGIN IMMEDIATE before entering native ownership.
        cursor.execute("PRAGMA synchronous")
        if cursor.fetchone()[0] != 2:
            raise PreservedRankImportConflict("offline import requires SQLite synchronous FULL")


def prepare_preserved_rank_target(cursor, *, postgres: bool, plan: dict, plan_sha256: str, now_provider):
    """Prepare one proven-new offline target in the caller's transaction."""
    pg = postgres
    owner = plan["destination_uuid"]
    _rank_lock(cursor, pg, owner)
    descriptor = _rank_document(cursor, pg, _RANK_TARGETS, owner, "target")
    if descriptor is not None:
        if descriptor.get("plan_sha256") != plan_sha256 or descriptor.get("plan") != plan:
            raise PreservedRankImportConflict("target belongs to another immutable plan")
        return descriptor
    # This is target emptiness, never a source scan or an operating DB inventory.
    for table in (*PRESERVED_RANK_COLUMNS, "central_documents"):
        cursor.execute(f"SELECT 1 FROM {table} LIMIT 1")
        if cursor.fetchone() is not None:
            raise PreservedRankImportConflict("new target has unexpected existing data")
    at = as_kst(now_provider())
    descriptor = {"contract": "preserved_rank_target/v1", "plan_sha256": plan_sha256, "plan": plan,
                  "created_at": at.isoformat(), "state": "INCOMPLETE", "runtime_admission": False}
    _rank_insert_document(cursor, pg, _RANK_TARGETS, owner, "target", descriptor, at)
    return descriptor


def preserved_rank_receipt(cursor, *, postgres: bool, destination_uuid: str, operation_id: str,
                           plan_sha256: str, require_complete: bool = True):
    descriptor = _rank_document(cursor, postgres, _RANK_TARGETS, destination_uuid, "target")
    if descriptor is None or descriptor.get("plan_sha256") != plan_sha256:
        raise PreservedRankImportConflict("offline target identity/plan is not established")
    receipt = _rank_document(cursor, postgres, _RANK_RECEIPTS, destination_uuid, operation_id)
    if receipt is not None and receipt.get("plan_sha256") != plan_sha256:
        raise PreservedRankImportConflict("operation receipt belongs to another plan")
    if require_complete and (receipt is None or descriptor["state"] != "COMPLETE" or receipt.get("state") != "COMPLETE"):
        raise PreservedRankImportConflict("offline rank target is INCOMPLETE")
    return receipt


def _rank_json_equal(left, right):
    if isinstance(left, str):
        left = json.loads(left, parse_float=Decimal)
    if isinstance(right, str):
        right = json.loads(right, parse_float=Decimal)
    def same(a, b):
        if isinstance(a, bool) or isinstance(b, bool):
            return type(a) is type(b) and a == b
        if isinstance(a, (int, Decimal)) and isinstance(b, (int, Decimal)):
            return a == b  # JSONB may normalize number spelling, never through float.
        if type(a) is not type(b):
            return False
        if isinstance(a, dict):
            return a.keys() == b.keys() and all(same(a[key], b[key]) for key in a)
        if isinstance(a, list):
            return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
        return a == b
    return same(left, right)


def _rank_equal(expected, actual, pg):
    for key, value in expected.items():
        other = actual[key]
        if key in _RANK_JSON and pg:
            if not _rank_json_equal(value, other):
                return False
        elif key in _RANK_TIMES and pg and value is not None:
            if other is None or as_kst(datetime.fromisoformat(value)) != as_kst(datetime.fromisoformat(other)):
                return False
        elif key == "saved_at":
            if float(value) != float(other):
                return False
        elif type(value) is not type(other) or value != other:
            return False
    return True


def _rank_rows(cursor, table, pg, columns):
    order = ("accepted_sequence" if table == "central_observation_revisions" else
             ("kind", "subject", "snapshot_key") if table == "central_dataset_snapshots" else ("subject", "observation_key"))
    ordering = order if isinstance(order, str) else ",".join(key + (' COLLATE "C"' if pg else " COLLATE BINARY") for key in order)
    sql = (f"SELECT row_to_json(t)::text FROM (SELECT {','.join(columns)} FROM {table}) t ORDER BY {ordering}" if pg else
           f"SELECT {','.join(columns)} FROM {table} ORDER BY {ordering}")
    reader = cursor.connection.cursor(name="preserved_rank_check_" + uuid.uuid4().hex) if pg else cursor.connection.cursor()
    try:
        reader.execute(sql)
        while batch := reader.fetchmany(256):
            for row in batch:
                yield json.loads(row[0], parse_float=Decimal) if pg else dict(zip(columns, row, strict=True))
    finally:
        reader.close()


def _rank_validate_rows(cursor, pg, plan, rows_factory):
    import itertools
    identity = hashlib.sha256()
    counts = {}
    minimum = maximum = None
    for table, columns in _rank_columns(plan).items():
        count = 0
        # v1 owns old raw columns AND the new target-only deterministic projection.
        original_format = plan.get("artifact_format", "preserved-rank-history/v1") == "preserved-rank-history/v1"
        derived_snapshot = original_format and table == "central_dataset_snapshots"
        inline_metadata = original_format and table == "central_market_data_observation_meta"
        target_columns = (*columns, "revision_id", "slot_order_key") if derived_snapshot else (
            (*columns, "revision_id") if inline_metadata else columns)
        actual, expected_rows = _rank_rows(cursor, table, pg, target_columns), rows_factory(table)
        try:
            for expected, stored in itertools.zip_longest(expected_rows, actual):
                if expected is not None and (derived_snapshot or inline_metadata):
                    expected = {**expected, "revision_id": None}
                    if derived_snapshot:
                        from .database_observation_writes import _rank_slot_order_key
                        expected["slot_order_key"] = _rank_slot_order_key(expected["snapshot_key"])
                if expected is None or stored is None or not _rank_equal(expected, stored, pg):
                    raise PreservedRankImportConflict("native target content differs from frozen rank artifact")
                count += 1
                if table == "central_observation_revisions":
                    seq = expected["accepted_sequence"]
                    minimum = seq if minimum is None else min(minimum, seq)
                    maximum = seq if maximum is None else max(maximum, seq)
                    identity.update(json.dumps([plan["source_instance"], expected["revision_id"], seq],
                                               ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
        finally:
            actual.close()
            getattr(expected_rows, "close", lambda: None)()
        counts[table] = count
        if count != plan["counts"][table]:
            raise PreservedRankImportConflict("native target row count differs from frozen plan")
    return {"counts": counts, "original_min_sequence": minimum, "original_max_sequence": maximum,
            "identity_map": "same source revision_id and accepted_sequence", "identity_map_sha256": identity.hexdigest()}


def _reserve_rank_sequence(cursor, pg, floor):
    if not pg:
        cursor.execute("SELECT seq FROM sqlite_sequence WHERE name='central_observation_revisions'")
        row = cursor.fetchone()
        desired = max(floor, int(row[0]) if row else 0)
        if row:
            cursor.execute("UPDATE sqlite_sequence SET seq=? WHERE name='central_observation_revisions'", (desired,))
        else:
            cursor.execute("INSERT INTO sqlite_sequence(name,seq) VALUES('central_observation_revisions',?)", (desired,))
        return desired
    from psycopg import sql
    cursor.execute("SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                   "WHERE c.oid=pg_get_serial_sequence('central_observation_revisions','accepted_sequence')::regclass")
    schema, sequence = cursor.fetchone()
    cursor.execute(sql.SQL("SELECT last_value,is_called FROM {}").format(sql.Identifier(schema, sequence)))
    last, called = cursor.fetchone()
    desired = max(floor, int(last) if called else int(last) - 1)
    if desired > 0 and (not called or desired > int(last)):
        # setval is NONTRANSACTIONAL. Rollback may burn an owned-target gap;
        # never rewind it to pretend the allocator rolled back with the rows.
        cursor.execute("SELECT setval(pg_get_serial_sequence('central_observation_revisions','accepted_sequence'),%s,true)", (desired,))
    return desired


def accept_preserved_rank_history(cursor, *, postgres: bool, plan: dict, plan_sha256: str,
                                 rows_factory, validate_frozen_input, now_provider):
    """Accept all selected payload and its first receipt in ONE caller transaction.

    rows_factory is a bounded, verified artifact iterator supplied by the
    operator. It is not a runtime script import, DB lookup relay or new worker.
    The caller must join/close this owner before resolving an ambiguous ACK.
    """
    pg = postgres
    _rank_lock(cursor, pg, plan["destination_uuid"])
    receipt = preserved_rank_receipt(cursor, postgres=pg, destination_uuid=plan["destination_uuid"],
                                     operation_id=plan["operation_id"], plan_sha256=plan_sha256, require_complete=False)
    if receipt is not None:
        checked = _rank_validate_rows(cursor, pg, plan, rows_factory)
        _validate_imported_rank_heads(cursor, pg)
        validate_frozen_input()
        if checked != receipt["native_content"]:
            raise PreservedRankImportConflict("first receipt content no longer matches target")
        return {"receipt": receipt, "replayed": True}
    if pg:
        # Offline target only. Prevent normal native INSERT allocation racing
        # reservation; no runtime target writer has been admitted by this P1.
        cursor.execute("LOCK TABLE central_observation_revisions IN ACCESS EXCLUSIVE MODE")
    for table in PRESERVED_RANK_COLUMNS:
        cursor.execute(f"SELECT 1 FROM {table} LIMIT 1")
        if cursor.fetchone() is not None:
            raise PreservedRankImportConflict("unreceipted or unrelated target data is present")
    for table, columns in _rank_columns(plan).items():
        p = "%s" if pg else "?"
        original_inline = table == "central_dataset_snapshots" and plan.get("artifact_format", "preserved-rank-history/v1") == "preserved-rank-history/v1"
        target_columns = (*columns, "slot_order_key") if original_inline else columns
        placeholders = ",".join(p + ("::jsonb" if pg and name in _RANK_JSON else "") for name in target_columns)
        statement = f"INSERT INTO {table}({','.join(target_columns)}) VALUES({placeholders})"
        batch = []
        for row in rows_factory(table):
            if set(row) != set(columns):
                raise PreservedRankImportConflict("unsupported source columns cannot be silently dropped")
            if ((table == "central_market_data_observation_meta" and row["dataset_kind"] != "candidate_set") or
                    (table != "central_market_data_observation_meta" and row["kind"] not in {"ranking", "top20_membership"})):
                raise PreservedRankImportConflict("unselected data in rank import")
            values = []
            for name in columns:
                value = row[name]
                if pg and name in _RANK_TIMES and value is not None:
                    value = as_kst(datetime.fromisoformat(value))
                values.append(value)
            if original_inline:
                from .database_observation_writes import _rank_slot_order_key
                values.append(_rank_slot_order_key(row["snapshot_key"]))
            batch.append(tuple(values))
            if len(batch) == 256:
                cursor.executemany(statement, batch)
                batch.clear()
        if batch:
            cursor.executemany(statement, batch)
    _validate_imported_rank_heads(cursor, pg)
    checked = _rank_validate_rows(cursor, pg, plan, rows_factory)
    validate_frozen_input()
    floor = max(int(plan["global_visible_highwater"] or 0), int(checked["original_max_sequence"] or 0))
    reserved = _reserve_rank_sequence(cursor, pg, floor)
    at = as_kst(now_provider())
    receipt = {"contract": "preserved_rank_acceptance/v1", "state": "COMPLETE", "runtime_admission": False,
               "destination_uuid": plan["destination_uuid"], "operation_id": plan["operation_id"],
               "plan_sha256": plan_sha256, "plan": plan, "native_content": checked, "allocator_floor": reserved,
               "accepted_at": at.isoformat(), "clock": "explicit offline KST operator clock",
               "durability": "PostgreSQL synchronous_commit ON" if pg else "SQLite WAL/synchronous FULL",
               "allocator_rollback": "nontransactional advance may burn gaps; never rewind" if pg else "owning transaction",
               "remaining": "No T0/primary/head/runtime attachment/settings/order authority is selected"}
    _rank_insert_document(cursor, pg, _RANK_RECEIPTS, plan["destination_uuid"], plan["operation_id"], receipt, at)
    descriptor = _rank_document(cursor, pg, _RANK_TARGETS, plan["destination_uuid"], "target")
    descriptor = {**descriptor, "state": "COMPLETE"}
    p = "%s" if pg else "?"
    cursor.execute(f"UPDATE central_documents SET document_json={p},updated_at={p} WHERE collection={p} AND owner={p} AND document_key={p}",
                   (json.dumps(descriptor, ensure_ascii=False, sort_keys=True, separators=(",", ":")), at.timestamp(),
                    _RANK_TARGETS, plan["destination_uuid"], "target"))
    return {"receipt": receipt, "replayed": False}



def _validate_imported_rank_heads(cursor, pg):
    """Bounded native proof; no script import, inferred current or fabricated row."""
    from .database_observation_writes import _validate_rank_head_reference, _validate_metadata_reference, _metadata_projection, _rank_slot_order_key
    cursor.execute("SELECT s.kind,s.subject,s.snapshot_key,r.revision_id,r.kind,r.subject,r.observation_key,s.slot_order_key,s.revision_id "
                   "FROM central_dataset_snapshots s LEFT JOIN central_observation_revisions r "
                   "ON r.revision_id=s.revision_id WHERE s.kind IN ('ranking','top20_membership')")
    while rows := cursor.fetchmany(256):
        for row in rows:
            try:
                if row[8] is not None:
                    _validate_rank_head_reference(row[:3], row[3:7])
                if row[7] is not None and row[7] != _rank_slot_order_key(row[2]):
                    raise ValueError("current slot ordering projection differs from raw key")
            except ValueError as error:
                raise PreservedRankImportConflict(str(error)) from error
    cursor.execute("SELECT " + _metadata_projection() +
                   ",m.revision_id,r.revision_id,r.kind,r.subject,r.observation_key,m.subject,m.observation_key,r.source_id "
                   "FROM central_market_data_observation_meta m LEFT JOIN central_observation_revisions r "
                   "ON r.revision_id=m.revision_id WHERE m.revision_id IS NOT NULL")
    while rows := cursor.fetchmany(256):
        for row in rows:
            try:
                _validate_metadata_reference(row)
            except ValueError as error:
                raise PreservedRankImportConflict(str(error)) from error
