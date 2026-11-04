"""One-time preservation of rank evidence; neither a runtime reader nor an importer.

SQLite uses one mode=ro transaction including live WAL, or explicit frozen
immutable mode with no sidecars and unchanged stat; no initialization/checkpoint.
PostgreSQL uses the existing native
connection owner with one read-only repeatable snapshot. Verification reads
only the three exported streams; its bounded external sort uses files, no DB.
The source_instance is an explicit opaque operator label, not a path or DSN.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing, contextmanager, ExitStack
from decimal import Decimal
import hashlib
import heapq
import itertools
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
from time import perf_counter
from typing import Any, Iterator
import uuid


FORMAT = "preserved-rank-history/v1"
FORMAT_V3 = "preserved-rank-history/v3"
KINDS = ("ranking", "top20_membership")
TABLES = ("central_observation_revisions", "central_dataset_snapshots", "central_market_data_observation_meta")
FILES = ("revisions.jsonl", "snapshots.jsonl", "metadata.jsonl")
REQUIRED = (
    ("accepted_sequence", "revision_id", "observation_key", "schema_version", "source_id", "source_session_id",
     "source_sequence", "kind", "subject", "venue", "effective_at", "received_at", "available_at", "revision_of",
     "payload_hash", "unit", "value_kind", "completeness", "origin", "candidate_universe", "quality_flags_json",
     "clock_quality", "source_ref_json", "payload_json"),
    ("kind", "subject", "snapshot_key", "saved_at", "payload_json"),
    ("dataset_kind", "subject", "observation_key", "effective_at", "available_at", "venue", "unit", "value_kind",
     "completeness", "origin", "source", "candidate_universe"),
)
SELECTION = "kind IN ('ranking','top20_membership')"


class PreservationError(ValueError):
    """Invalid/uninterpretable evidence or an incomplete preservation artifact."""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _reject_constant(value: str) -> None:
    raise PreservationError("nonfinite JSON number")


def _decode(raw: str) -> Any:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise PreservationError("duplicate JSON object key")
            result[key] = value
        return result
    return json.loads(raw, parse_float=Decimal, parse_constant=_reject_constant, object_pairs_hook=unique)


def _object(value: Any) -> dict:
    decoded = _decode(value) if isinstance(value, str) else value
    if not isinstance(decoded, dict):
        raise PreservationError("expected stored JSON object")
    return decoded


def _label(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value):
        raise PreservationError("source_instance must be an explicit opaque label")
    return value


def _raw_row(alias: str, pg: bool) -> str:
    # SQLite JSON1 renders REAL with fewer digits than a round-trip Python
    # float. Select native columns; preserve their full value in the IO codec.
    return f"row_to_json({alias})::text" if pg else f"{alias}.*"


def _native_rows(rows, columns: list[str], pg: bool):
    for values in rows:
        if pg:
            yield values
        else:
            yield (_json(dict(zip(columns, values[:len(columns)], strict=True))), *values[len(columns):])


def _queries(columns: dict[str, list[str]], pg: bool) -> list[str]:
    revision, snapshot, metadata = TABLES
    have_r, have_s = revision in columns, snapshot in columns
    rkeys = (f"SELECT kind,subject,observation_key FROM {revision} WHERE {SELECTION} GROUP BY kind,subject,observation_key"
             if have_r else "SELECT NULL AS kind,NULL AS subject,NULL AS observation_key WHERE FALSE")
    skeys = (f"SELECT kind,subject,snapshot_key AS observation_key FROM {snapshot} WHERE {SELECTION}"
             if have_s else "SELECT NULL AS kind,NULL AS subject,NULL AS observation_key WHERE FALSE")
    queries = []
    collation = '"C"' if pg else "BINARY"
    snapshot_order = ",".join(f"s.{key} COLLATE {collation}" for key in ("kind", "subject", "snapshot_key"))
    metadata_order = ",".join(f"m.{key} COLLATE {collation}" for key in ("subject", "observation_key"))
    if have_r:
        ref = (lambda key: f"r.source_ref_json->>'{key}'") if pg else (lambda key: f"json_extract(r.source_ref_json,'$.{key}')")
        joins = (f" LEFT JOIN {revision} p ON p.revision_id=r.revision_of"
                 f" LEFT JOIN ({rkeys}) rp ON rp.kind={ref('kind')} AND rp.subject={ref('subject')} AND rp.observation_key={ref('observation_key')}"
                 f" LEFT JOIN ({skeys}) sp ON sp.kind={ref('kind')} AND sp.subject={ref('subject')} AND sp.observation_key={ref('observation_key')}")
        queries.append(
            f"SELECT {_raw_row('r', pg)},"
            "CASE WHEN r.revision_of IS NULL THEN 'none' WHEN p.revision_id IS NULL THEN 'absent_in_source' "
            "WHEN p.kind IN ('ranking','top20_membership') THEN 'selected' ELSE 'outside_selection' END,"
            f"CASE WHEN {ref('kind')} IS NULL THEN 'none' WHEN {ref('kind')} NOT IN ('ranking','top20_membership') THEN 'outside_selection_not_checked' "
            "WHEN rp.observation_key IS NOT NULL OR sp.observation_key IS NOT NULL THEN 'selected' ELSE 'absent_in_source' END "
            f"FROM {revision} r{joins} WHERE r.{SELECTION} ORDER BY r.accepted_sequence")
    else:
        queries.append("SELECT NULL,NULL,NULL WHERE FALSE")
    if have_s:
        queries.append(
            f"SELECT {_raw_row('s', pg)},CASE WHEN rk.observation_key IS NULL THEN 'absent_in_source' ELSE 'selected' END "
            f"FROM {snapshot} s LEFT JOIN ({rkeys}) rk ON rk.kind=s.kind AND rk.subject=s.subject AND rk.observation_key=s.snapshot_key "
            f"WHERE s.{SELECTION} ORDER BY {snapshot_order}")
    else:
        queries.append("SELECT NULL,NULL WHERE FALSE")
    if metadata in columns:
        queries.append(
            f"SELECT {_raw_row('m', pg)} FROM {metadata} m JOIN "
            f"(SELECT subject,observation_key FROM ({rkeys} UNION {skeys}) k GROUP BY subject,observation_key) k "
            "ON k.subject=m.subject AND k.observation_key=m.observation_key WHERE m.dataset_kind='candidate_set' "
            f"ORDER BY {metadata_order}")
    else:
        queries.append("SELECT NULL WHERE FALSE")
    return queries


@contextmanager
def _source(database: Path | None, dsn: str | None, metrics: dict, *, frozen: bool) -> Iterator[Any]:
    metrics.update(connections=0, begin=0, rollback=0, selects=0, data_selects=0, fetch_batches=0,
                   rows=0, source_dml=0, source_ddl=0, checkpoints=0, sql_commits=0)
    started = perf_counter()
    if database is not None:
        from kiwoom_monitor.infrastructure.persistence.sqlite_read_snapshot import (
            SQLiteSnapshotContractError, readonly_sqlite_snapshot,
        )
        try:
            with readonly_sqlite_snapshot(database, frozen=frozen, metrics=metrics) as connection:
                yield connection
        except SQLiteSnapshotContractError as error:
            raise PreservationError(str(error)) from error
    else:
        import psycopg
        from psycopg.conninfo import conninfo_to_dict
        from kiwoom_monitor.central_server.postgres_access import DBWriterContext, owned_connection_scope
        options = conninfo_to_dict(dsn).get("options", "")
        context = DBWriterContext(writer_family="operator.rank_preservation", writer_kind="rank_export",
                                  operation="read_consistent_snapshot", access_mode="read")
        with owned_connection_scope(lambda: psycopg.connect(
                dsn, connect_timeout=10,
                options=(options + " -c default_transaction_read_only=on -c timezone=Asia/Seoul").strip()), context) as connection:
            metrics["connections"] = 1
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            metrics["begin"] = 1
            metrics["connection_ms"] = round((perf_counter() - started) * 1000, 3)
            try:
                yield connection
            finally:
                connection.rollback()
                metrics["rollback"] = 1
        # The existing owner closes through native __exit__; after rollback its
        # commit() is an idle driver no-op, not another server transaction.
        metrics["native_owner_exit"] = "rollback_then_idle_context_exit"


def _select(connection, sql: str, metrics: dict, *, pg: bool, data: bool = False):
    metrics["selects"] += 1
    if data:
        metrics["data_selects"] += 1
    name = f"preserved_rank_{metrics['selects']}" if pg and data else None
    cursor = connection.cursor(name=name) if name else connection.cursor()
    try:
        cursor.execute(sql)
        while True:
            rows = cursor.fetchmany(512)
            metrics["fetch_batches"] += 1
            if not rows:
                break
            yield from rows
    finally:
        cursor.close()


def _source_description(connection, source: Path | None, metrics: dict) -> tuple[dict, dict[str, list[str]]]:
    pg = source is None
    names = ",".join("'" + table + "'" for table in TABLES)
    sql = ("SELECT table_name,column_name,data_type,is_nullable,ordinal_position FROM information_schema.columns "
           f"WHERE table_schema=current_schema() AND table_name IN ({names}) ORDER BY table_name,ordinal_position" if pg else
           "SELECT m.name,p.name,p.type,p.\"notnull\",p.pk FROM sqlite_master m JOIN pragma_table_info(m.name) p "
           f"WHERE m.type='table' AND m.name IN ({names}) ORDER BY m.name,p.cid")
    schema_rows = list(_select(connection, sql, metrics, pg=pg))
    columns: dict[str, list[str]] = {}
    for row in schema_rows:
        columns.setdefault(row[0], []).append(row[1])
    for table, required in zip(TABLES, REQUIRED):
        if table in columns and not set(required) <= set(columns[table]):
            raise PreservationError("source relation has an unsupported incomplete schema")
    if pg:
        identity_sql = ("SELECT current_database(),current_schema(),pg_current_snapshot()::text,"
                        "current_setting('transaction_read_only'),current_setting('transaction_isolation'),"
                        "current_setting('server_version_num')")
    else:
        identity_sql = ("SELECT sqlite_source_id(),(SELECT user_version FROM pragma_user_version),"
                        "(SELECT application_id FROM pragma_application_id),(SELECT schema_version FROM pragma_schema_version)")
    identity = next(_select(connection, identity_sql, metrics, pg=pg))
    if pg and identity[3:5] != ("on", "repeatable read"):
        raise PreservationError("source snapshot is not read-only repeatable read")
    source_identity = list(identity[:2]) if pg else list(identity[1:]) + [source.stat().st_dev, source.stat().st_ino]
    description = {"dialect": "postgres" if pg else "sqlite", "schema": [list(row) for row in schema_rows],
                   "schema_sha256": hashlib.sha256(_json(schema_rows).encode()).hexdigest(),
                   "database_identity_sha256": hashlib.sha256(_json(source_identity).encode()).hexdigest(),
                   "snapshot": {"protocol": "read_only_repeatable_read" if pg else "mode_ro_begin_read_snapshot_honors_wal",
                                "native_cut": identity[2] if pg else list(identity[1:]),
                                "clock": "source timestamps unchanged; artifact clock is separate"},
                   "sidecars": "SQLite may use existing SHM read locks; no source SQL writes/checkpoint, no immutable-WAL bypass" if not pg else "not applicable"}
    return description, columns


def _new_output(output: Path, source: Path | None) -> tuple[Path, Path]:
    output = output.absolute()
    if output.is_symlink() or output.exists():
        raise FileExistsError("output must be a new directory")
    if source is not None:
        protected = {Path(str(source.resolve(strict=True)) + suffix) for suffix in ("", "-wal", "-shm", "-journal")}
        if output.resolve() in protected:
            raise PreservationError("output aliases a source database or sidecar")
    if not output.parent.is_dir():
        raise FileNotFoundError("output parent must exist")
    output.mkdir()  # Exclusive reservation, unlike rename over an existing POSIX directory.
    staging = output / (".staging-" + uuid.uuid4().hex)
    staging.mkdir()
    return output.resolve(), staging.resolve()


def _write_stream(path: Path, rows, relation: int) -> dict:
    digest = hashlib.sha256()
    count = 0
    with path.open("xb") as stream:
        for values in rows:
            references = ({"revision_of": values[1], "source_ref": values[2]} if relation == 0 else
                          {"snapshot_revision": values[1]} if relation == 1 else {})
            # Native row JSON is not decoded/re-serialized here. PostgreSQL
            # JSONB decimal precision and SQLite stored JSON text stay intact.
            raw = ('{"row":' + values[0] + ',"references":' + _json(references) + '}\n').encode("utf-8")
            stream.write(raw)
            digest.update(raw)
            count += 1
        stream.flush()
    return {"file": path.name, "rows": count, "bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def _records(path: Path):
    with path.open("rb") as stream:
        for line in stream:
            if not line.endswith(b"\n"):
                raise PreservationError("truncated JSONL record")
            value = _decode(line.decode("utf-8"))
            if not isinstance(value, dict) or set(value) != {"row", "references"}:
                raise PreservationError("invalid stream envelope")
            yield value


def _sort_index(raw_path: Path, scratch: Path):
    chunks = []
    with raw_path.open(encoding="utf-8") as stream:
        for ordinal in itertools.count():
            batch = list(itertools.islice(stream, 2048))
            if not batch:
                break
            path = scratch / f"sort-{ordinal}.jsonl"
            batch.sort(key=lambda raw: json.loads(raw)[0])
            path.write_text("".join(batch), encoding="utf-8")
            chunks.append(path)
    def merge(paths):
        with ExitStack() as stack:
            files = [stack.enter_context(path.open(encoding="utf-8")) for path in paths]
            yield from heapq.merge(*(map(json.loads, stream) for stream in files), key=lambda row: row[0])
    generation = 0
    while len(chunks) > 32:
        next_chunks = []
        for offset in range(0, len(chunks), 32):
            paths = chunks[offset:offset + 32]
            target = scratch / f"merge-{generation}-{offset}.jsonl"
            with target.open("x", encoding="utf-8") as stream:
                for row in merge(paths):
                    stream.write(_json(row) + "\n")
            for path in paths:
                path.unlink()
            next_chunks.append(target)
        chunks = next_chunks
        generation += 1
    yield from merge(chunks)


def _verify_directory(directory: Path, manifest: dict) -> dict:
    if manifest.get("format") not in {FORMAT, FORMAT_V3} or manifest.get("selection") != list(KINDS):
        raise PreservationError("unknown artifact format/selection")
    _label(manifest["source_instance"])
    if len(manifest.get("streams", [])) != 3:
        raise PreservationError("missing streams")
    schema = manifest["source"]["schema"]
    if hashlib.sha256(_json(schema).encode()).hexdigest() != manifest["source"]["schema_sha256"]:
        raise PreservationError("schema digest mismatch")
    columns = {}
    for item in schema:
        columns.setdefault(item[0], []).append(item[1])
    if manifest["format"] == FORMAT_V3:
        expected = (REQUIRED[0], (*REQUIRED[1], "revision_id", "slot_order_key"), (*REQUIRED[2], "revision_id"))
        if any(set(columns.get(table, ())) != set(fields) for table, fields in zip(TABLES, expected)):
            raise PreservationError("v3 current-head schema is incomplete or unsupported")
    elif any("revision_id" in columns.get(table, ()) for table in TABLES[1:]):
        raise PreservationError("current-head source requires explicit artifact v3")
    high = manifest["global_visible_accepted_highwater"]
    if high is not None and (type(high) is not int or high < 0):
        raise PreservationError("invalid source highwater")
    sequence = {"first": None, "last": None, "count": 0, "unselected_or_unallocated_positions": 0}
    gap_hash = hashlib.sha256()
    previous_sequence = 0
    reference_claims = Counter()
    with tempfile.TemporaryDirectory(prefix="rank-artifact-verify-") as temporary, ExitStack() as sorting:
        scratch = Path(temporary).resolve()
        index = scratch / "relations.jsonl"
        with index.open("w", encoding="utf-8") as relation_index:
            def entry(key, role, status="", proof=None):
                relation_index.write(_json([_json(key), role, status, proof]) + "\n")
            for ordinal, (table, filename, spec) in enumerate(zip(TABLES, FILES, manifest["streams"])):
                if spec.get("file") != filename:
                    raise PreservationError("unexpected stream path")
                path = directory / filename
                if path.is_symlink() or not path.is_file():
                    raise PreservationError("stream missing or redirected")
                digest, count, size = hashlib.sha256(), 0, 0
                with path.open("rb") as stream:
                    for line in stream:
                        digest.update(line)
                        size += len(line)
                        count += 1
                if (digest.hexdigest(), count, size) != (spec["sha256"], spec["rows"], spec["bytes"]):
                    raise PreservationError("stream count/bytes/digest mismatch")
                last_key = None
                for value in _records(path):
                    row, refs = value["row"], value["references"]
                    if not isinstance(row, dict) or set(row) != set(columns.get(table, ())):
                        raise PreservationError("source columns not preserved")
                    if not isinstance(refs, dict):
                        raise PreservationError("invalid relationship evidence")
                    if ordinal < 2:
                        if row["kind"] not in KINDS:
                            raise PreservationError("unselected kind leaked")
                        if ordinal == 0 or row.get("revision_id") is None or manifest["format"] != FORMAT_V3:
                            _object(row["payload_json"])
                        elif row["payload_json"] is not None:
                            raise PreservationError("current head must own inline payload XOR reference")
                    if ordinal == 0:
                        seq = row["accepted_sequence"]
                        if type(seq) is not int or seq <= previous_sequence or high is None or seq > high:
                            raise PreservationError("accepted sequence changed or source cut exceeded")
                        if seq > previous_sequence + 1:
                            gap_hash.update(_json([previous_sequence + 1, seq - 1]).encode() + b"\n")
                            sequence["unselected_or_unallocated_positions"] += seq - previous_sequence - 1
                        previous_sequence = seq
                        sequence.update(first=sequence["first"] or seq, last=seq, count=sequence["count"] + 1)
                        identity = [row["kind"], row["subject"], row["observation_key"], row["source_id"]]
                        entry(["revision", row["revision_id"]], "U", proof=[identity, seq])
                        entry(["revision_observation", row["kind"], row["subject"], row["observation_key"]], "T")
                        entry(["observation", row["kind"], row["subject"], row["observation_key"]], "T")
                        entry(["metadata", row["subject"], row["observation_key"]], "T")
                        if row["revision_of"] is not None:
                            entry(["revision", row["revision_of"]], "R", refs["revision_of"], [identity, seq])
                        elif refs.get("revision_of") != "none":
                            raise PreservationError("fabricated predecessor reference")
                        source_ref = _object(row["source_ref_json"])
                        if source_ref.get("kind") in KINDS:
                            if not all(key in source_ref for key in ("subject", "observation_key")):
                                raise PreservationError("uninterpretable source reference")
                            entry(["observation", source_ref["kind"], source_ref["subject"], source_ref["observation_key"]], "R", refs["source_ref"])
                        elif refs.get("source_ref") != ("outside_selection_not_checked" if source_ref.get("kind") is not None else "none"):
                            raise PreservationError("misclassified source reference")
                        for field in ("quality_flags_json",):
                            flags = _decode(row[field]) if isinstance(row[field], str) else row[field]
                            if not isinstance(flags, list):
                                raise PreservationError("uninterpretable quality flags")
                    elif ordinal == 1:
                        key = (row["kind"], row["subject"], row["snapshot_key"])
                        if last_key is not None and key <= last_key:
                            raise PreservationError("snapshot order/identity changed")
                        last_key = key
                        if manifest["format"] == FORMAT_V3:
                            if row["slot_order_key"] is not None and row["slot_order_key"] != _artifact_slot_order_key(row["snapshot_key"]):
                                raise PreservationError("current slot ordering projection differs from raw key")
                        if manifest["format"] == FORMAT_V3 and row["revision_id"] is not None:
                            entry(["revision", row["revision_id"]], "H", "selected",
                                  [str(part).strip() for part in key] + [None])
                        entry(["snapshot", *key], "U")
                        entry(["observation", *key], "T")
                        entry(["metadata", row["subject"], row["snapshot_key"]], "T")
                        entry(["revision_observation", *key], "R", refs["snapshot_revision"])
                    else:
                        key = (row["subject"], row["observation_key"])
                        if row["dataset_kind"] != "candidate_set" or (last_key is not None and key <= last_key):
                            raise PreservationError("unselected or duplicate metadata")
                        last_key = key
                        if manifest["format"] == FORMAT_V3:
                            semantic = ("venue", "unit", "value_kind", "completeness", "origin", "source", "candidate_universe")
                            if row["revision_id"] is None:
                                if any(row[field] is None for field in semantic):
                                    raise PreservationError("invalid inline metadata head")
                            else:
                                if any(row[field] is not None for field in semantic):
                                    raise PreservationError("metadata head must own inline fields XOR reference")
                                entry(["revision", row["revision_id"]], "H", "selected",
                                      [None, str(key[0]).strip(), str(key[1]).strip(), None])
                        entry(["metadata", *key], "R", "selected")
                    reference_claims.update(refs.values())
        relationships = Counter()
        ordered = sorting.enter_context(closing(_sort_index(index, scratch)))
        for _, group in itertools.groupby(ordered, key=lambda row: row[0]):
            # One group may contain a long correction chain; aggregate without
            # materializing it. Re-read neither a source DB nor individual rows.
            targets, unique, claims = 0, 0, Counter()
            target_proof, child_identity, earliest_child = None, None, None
            head_identity = [None, None, None, None]
            for _, role, status, proof in group:
                if role in {"T", "U"}:
                    targets += 1
                    unique += role == "U"
                    if proof is not None:
                        target_proof = proof
                elif role == "H":
                    claims[status] += 1
                    for index, expected in enumerate(proof):
                        if expected is not None:
                            if head_identity[index] not in (None, expected):
                                raise PreservationError("head references conflicting source identities")
                            head_identity[index] = expected
                else:
                    claims[status] += 1
                    if proof is not None and status == "selected":
                        if child_identity is not None and child_identity != proof[0]:
                            raise PreservationError("revision predecessor has conflicting identities")
                        child_identity = proof[0]
                        earliest_child = min(earliest_child or proof[1], proof[1])
            if any(part is not None for part in head_identity):
                if target_proof is None or any(expected is not None and expected != actual
                                               for expected, actual in zip(head_identity, target_proof[0])):
                    raise PreservationError("current head references a missing or different source fact")
            if unique > 1:
                raise PreservationError("duplicate immutable or snapshot identity")
            if child_identity is not None and target_proof is not None and (
                    target_proof[0] != child_identity or target_proof[1] >= earliest_child):
                raise PreservationError("revision predecessor identity/order mismatch")
            for status, amount in claims.items():
                if status == "selected" and not targets:
                    raise PreservationError("source-present reference missing from artifact")
                if status in {"absent_in_source", "outside_selection"} and targets:
                    raise PreservationError("source absence contradicts artifact")
                if status not in {"selected", "absent_in_source", "outside_selection"}:
                    raise PreservationError("unknown relationship status")
                relationships[status] += amount
    if high is not None and high > previous_sequence:
        gap_hash.update(_json([previous_sequence + 1, high]).encode() + b"\n")
        sequence["unselected_or_unallocated_positions"] += high - previous_sequence
    sequence["gaps_sha256"] = gap_hash.hexdigest()
    return {"sequence": sequence, "relationships": dict(relationships), "source_reference_claims": dict(reference_claims),
            "meaning": "artifact structure/integrity only; source absence is a snapshot-reported fact, not rediscovered by this verifier"}


def verify_artifact(directory: Path) -> dict:
    directory = directory.resolve(strict=True)
    if (directory / "manifest.json").is_symlink():
        raise PreservationError("redirected manifest")
    manifest = _object((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("state") != "complete":
        raise PreservationError("artifact was not published complete")
    actual = _verify_directory(directory, manifest)
    if actual != manifest.get("verification"):
        raise PreservationError("relationship/sequence summary mismatch")
    return {"artifact_id": manifest["artifact_id"], "state": "verified_artifact_only", **actual}


def export_rank_history(*, source_instance: str, output: Path, database: Path | None = None,
                        postgres_dsn: str | None = None, artifact_now=None, sqlite_frozen: bool = False) -> dict:
    """Read one native snapshot and publish a new artifact; preserve failed stage."""
    _label(source_instance)
    if (database is None) == (postgres_dsn is None):
        raise PreservationError("select exactly one source")
    if sqlite_frozen and database is None:
        raise PreservationError("frozen declaration is SQLite-only")
    output, staging = _new_output(output, database)
    metrics: dict = {}
    started = perf_counter()
    try:
        with _source(database, postgres_dsn, metrics, frozen=sqlite_frozen) as connection:
            description, columns = _source_description(connection, database, metrics)
            if sqlite_frozen:
                description["snapshot"]["protocol"] = "explicit_frozen_immutable_no_sidecars_stat_unchanged"
                description["sidecars"] = "none before/after; explicit owner-frozen declaration, not live-WAL mode"
            high_sql = (f"SELECT MAX(accepted_sequence) FROM {TABLES[0]}" if TABLES[0] in columns else "SELECT NULL")
            high = next(_select(connection, high_sql, metrics, pg=database is None))[0]
            streams = [_write_stream(staging / filename, _native_rows(
                        _select(connection, sql, metrics, pg=database is None, data=True), columns.get(table, []), database is None), ordinal)
                       for ordinal, (filename, table, sql) in enumerate(zip(FILES, TABLES, _queries(columns, database is None)))]
            metrics["rows"] = sum(stream["rows"] for stream in streams)
        # Row writes, flush and close still occur inside the source snapshot.
        # Durability waits only after rollback and the native owner have exited.
        # Reopen only owned staging files writable for Windows os.fsync support.
        for filename in FILES:
            with (staging / filename).open("r+b") as stream:
                os.fsync(stream.fileno())
        if artifact_now is None:
            from kiwoom_monitor.domain.time_basis import kst_now
            artifact_now = kst_now()  # Explicit offline artifact metadata, never original availability.
        if artifact_now.utcoffset() is None:
            raise PreservationError("artifact receipt clock must be aware")
        from kiwoom_monitor.domain.time_basis import as_kst
        artifact_now = as_kst(artifact_now)  # New receipt only; original source clocks are untouched.
        has_heads = any("revision_id" in columns.get(table, ()) for table in TABLES[1:])
        if has_heads and not all("revision_id" in columns.get(table, ()) for table in TABLES[1:]):
            raise PreservationError("partial current-head schema")
        manifest = {"format": FORMAT_V3 if has_heads else FORMAT, "state": "complete", "artifact_id": uuid.uuid4().hex,
                    "source_instance": source_instance, "source": description, "selection": list(KINDS),
                    "global_visible_accepted_highwater": high,
                    "cut_limit": "committed rows visible in one snapshot; not PG allocation ceiling or a live delivery safe-prefix",
                    "gaps": "original selected sequences preserved; absent positions may be other kinds, rollback/allocation gaps or uncommitted rows; no fabricated rows",
                    "created_at": artifact_now.isoformat(), "artifact_clock": "explicit offline KST fallback or injected clock; not a source observation",
                    "streams": streams, "metrics": metrics,
                    "publication": "manifest.json is the final no-replace link to prepared-manifest.json; no cleanup follows publication",
                    "excluded": "all other market/account/news/research/document/cohort rows; no prior history synthesis",
                    "scope": "preservation only; T0/settings/orders/import/latest policy remain unselected and unimplemented"}
        manifest["verification"] = _verify_directory(staging, manifest)
        metrics["elapsed_ms"] = round((perf_counter() - started) * 1000, 3)
        from kiwoom_monitor.infrastructure.persistence.backup_file import write_json_backup
        # Hard-link publication refuses to overwrite a concurrent destination.
        # All fallible staging cleanup precedes the final complete marker.
        for filename in FILES:
            os.link(staging / filename, output / filename)
        if staging.parent != output or not staging.name.startswith(".staging-"):
            raise PreservationError("owned staging escaped output")
        shutil.rmtree(staging)
        write_json_backup(output / "prepared-manifest.json", manifest)
        os.link(output / "prepared-manifest.json", output / "manifest.json")
        return manifest
    except BaseException as error:
        failure_root = staging if staging.is_dir() else output
        if failure_root.is_dir() and not (output / "manifest.json").exists():
            # No source path/DSN or driver message in output diagnostics.
            with (failure_root / "failure.json").open("x", encoding="utf-8") as stream:
                stream.write(_json({"state": "incomplete", "error_type": type(error).__name__, "metrics": metrics}))
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export")
    source = export.add_mutually_exclusive_group(required=True)
    source.add_argument("--sqlite", type=Path)
    source.add_argument("--postgres-dsn-env", help="name of explicit DSN environment variable; never logged")
    export.add_argument("--sqlite-frozen", action="store_true", help="explicit owner-frozen main file; no sidecars and unchanged stat required")
    export.add_argument("--source-instance", required=True)
    export.add_argument("--output", required=True, type=Path)
    verify = commands.add_parser("verify")
    verify.add_argument("artifact", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            result = verify_artifact(args.artifact)
        else:
            dsn = os.environ[args.postgres_dsn_env] if args.postgres_dsn_env else None
            report = export_rank_history(source_instance=args.source_instance, output=args.output,
                                         database=args.sqlite, postgres_dsn=dsn, sqlite_frozen=args.sqlite_frozen)
            result = {"state": "preserved_not_imported", "artifact_id": report["artifact_id"],
                      "counts": [stream["rows"] for stream in report["streams"]], "metrics": report["metrics"]}
        print(_json(result))
        return 0
    except Exception as error:
        print(_json({"state": "failed", "error_type": type(error).__name__}))
        return 1



def _artifact_slot_order_key(raw_key):
    from datetime import datetime
    from kiwoom_monitor.domain.time_basis import as_kst
    try:
        return as_kst(datetime.fromisoformat(str(raw_key).replace("Z", "+00:00"))).isoformat(timespec="microseconds")
    except ValueError:
        return None


if __name__ == "__main__":
    raise SystemExit(main())
