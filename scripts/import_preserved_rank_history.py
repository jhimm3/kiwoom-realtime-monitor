"""Import one preserved rank artifact into an explicit separate offline target.

No source DB is opened and no current-source/head/startup switch is implemented.
One bounded stream, one native data transaction and its first immutable receipt;
there are no committed data chunks or background owners to hide partial success.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import uuid

from scripts import export_preserved_rank_history as preserved
from kiwoom_monitor.central_server.central_schema import central_schema_migrations
from kiwoom_monitor.central_server.schema_migrations import CentralSchemaMigrationRunner
from kiwoom_monitor.central_server.database_rank_import import (
    PRESERVED_RANK_COLUMNS, PRESERVED_RANK_COLUMNS_V3, PreservedRankImportConflict, prepare_preserved_rank_target,
    accept_preserved_rank_history, preserved_rank_receipt,
)
from kiwoom_monitor.domain.time_basis import as_kst, kst_now
from kiwoom_monitor.infrastructure.persistence.backup_file import write_json_backup


CONTRACT = "preserved_rank_import/v1"
_JSON_COLUMNS = {"payload_json", "quality_flags_json", "source_ref_json"}


class _CommitUncertain(RuntimeError):
    """The native owner attempted COMMIT; only exact receipt may resolve it."""


def _lossless(value):
    """JSON codec: never route a Decimal/large JSON number through float."""
    if value is None or isinstance(value, (str, bool, int)):
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("nonfinite artifact number")
        return str(value)
    if isinstance(value, list):
        return "[" + ",".join(_lossless(item) for item in value) + "]"
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return "{" + ",".join(json.dumps(key, ensure_ascii=False) + ":" + _lossless(value[key]) for key in sorted(value)) + "}"
    raise ValueError("unsupported artifact JSON value")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _stat(path):
    value = path.stat()
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns


def _no_redirect(path):
    absolute = path.absolute()
    if any(part.is_symlink() or part.is_junction() for part in (absolute, *absolute.parents)):
        raise PreservedRankImportConflict("redirected target/artifact path is not supported")
    return absolute.resolve()


def freeze_artifact(artifact: Path, *, source_instance: str, manifest_sha256: str,
                    destination_uuid: str, operation_id: str, postgres: bool):
    artifact = _no_redirect(artifact)
    preserved._label(source_instance)
    preserved._label(operation_id)
    destination_uuid = uuid.UUID(destination_uuid).hex
    if not re.fullmatch(r"[0-9a-f]{64}", manifest_sha256):
        raise ValueError("explicit expected manifest SHA256 is required")
    manifest_path = artifact / "manifest.json"
    if _sha(manifest_path.read_bytes()) != manifest_sha256:
        raise PreservedRankImportConflict("artifact manifest differs from explicit expected digest")
    preserved.verify_artifact(artifact)  # Source-free P0 integrity/relationship oracle.
    manifest = preserved._object(manifest_path.read_text(encoding="utf-8"))
    if manifest["source_instance"] != source_instance:
        raise PreservedRankImportConflict("artifact source_instance differs from explicit selection")
    artifact_format = manifest["format"]
    expected_columns = (PRESERVED_RANK_COLUMNS_V3 if artifact_format == preserved.FORMAT_V3 else PRESERVED_RANK_COLUMNS)
    schemas = {}
    for table, column, *_ in manifest["source"]["schema"]:
        schemas.setdefault(table, []).append(column)
    for table, columns in schemas.items():
        if table not in expected_columns or set(columns) != set(expected_columns[table]):
            raise PreservedRankImportConflict("source schema requires an explicit new field mapping")
    native_ddl = [statement for migration in central_schema_migrations("postgres" if postgres else "sqlite")
                  for statement in migration.statements("postgres" if postgres else "sqlite")]
    plan = {"contract": CONTRACT, "artifact_format": artifact_format, "destination_uuid": destination_uuid, "operation_id": operation_id,
            "dialect": "postgres" if postgres else "sqlite", "source_instance": source_instance,
            "artifact_id": manifest["artifact_id"], "manifest_sha256": manifest_sha256,
            "streams": manifest["streams"], "source_schema": manifest["source"]["schema"],
            "source_database_identity_sha256": manifest["source"]["database_identity_sha256"],
            "source_schema_sha256": manifest["source"]["schema_sha256"],
            "global_visible_highwater": manifest["global_visible_accepted_highwater"],
            "original_reference_meanings": manifest["verification"],
            "native_schema_sha256": _sha(json.dumps(native_ddl, ensure_ascii=False).encode("utf-8")),
            "counts": {table: stream["rows"] for table, stream in zip(PRESERVED_RANK_COLUMNS, manifest["streams"])},
            "mapping": "original IDs/sequences; SQLite TEXT exact; native PG time instants/JSONB values with original artifact as representation evidence",
            "naive_time_boundary": "documented legacy native SQLite KST, never host timezone/fallback",
            "atomicity": "single data transaction plus receipt; no activation/chunk commits/T0/primary selection"}
    plan_sha = _sha(json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    paths = [manifest_path, *(artifact / name for name in preserved.FILES)]
    identities = {path: _stat(path) for path in paths}
    def unchanged():
        if any(path.is_symlink() or _stat(path) != value for path, value in identities.items()):
            raise PreservedRankImportConflict("frozen artifact changed during import")
        if _sha(manifest_path.read_bytes()) != manifest_sha256:
            raise PreservedRankImportConflict("artifact manifest changed during import")
    def rows(table):
        ordinal = list(PRESERVED_RANK_COLUMNS).index(table)
        spec = manifest["streams"][ordinal]
        digest, count, size = hashlib.sha256(), 0, 0
        path = artifact / preserved.FILES[ordinal]
        unchanged()
        with path.open("rb") as stream:
            for line in stream:
                digest.update(line)
                count += 1
                size += len(line)
                record = preserved._object(line.decode("utf-8"))
                row = record["row"]
                if set(record) != {"row", "references"} or set(row) != set(expected_columns[table]):
                    raise PreservedRankImportConflict("artifact fields changed/require new mapping")
                normalized = dict(row)
                for key, value in row.items():
                    if key in _JSON_COLUMNS and value is not None:
                        # Original SQLite JSON TEXT stays byte-for-byte. PG JSONB
                        # values use exact decimal JSON, not default float dumps.
                        if isinstance(value, str):
                            preserved._decode(value)
                        else:
                            normalized[key] = _lossless(value)
                    elif key == "saved_at":
                        if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
                            raise PreservedRankImportConflict("invalid original native DOUBLE value")
                        normalized[key] = float(value)
                        if not math.isfinite(normalized[key]):
                            raise PreservedRankImportConflict("unrepresentable native DOUBLE value")
                    elif key in {"accepted_sequence", "schema_version"}:
                        if type(value) is not int:
                            raise PreservedRankImportConflict("integer identity/version cannot be coerced")
                    elif value is not None and not isinstance(value, str):
                        raise PreservedRankImportConflict("source TEXT/time requires an explicit mapping")
                yield normalized
        if (count, size, digest.hexdigest()) != (spec["rows"], spec["bytes"], spec["sha256"]):
            raise PreservedRankImportConflict("frozen artifact stream digest/count changed")
        unchanged()
    unchanged()
    return plan, plan_sha, rows, unchanged


def _directory(artifact, target):
    directory = _no_redirect(target)
    source = _no_redirect(artifact)
    if directory == source or directory.is_relative_to(source) or source.is_relative_to(directory):
        raise PreservedRankImportConflict("offline target collides with preserved artifact")
    return directory


@contextmanager
def _target_connection(*, directory=None, dsn=None, schema=None):
    if directory is not None:
        path = directory / "rank.sqlite3"
        connection = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=10)
        try:
            connection.execute("PRAGMA journal_mode=WAL")  # Destination only.
            connection.execute("PRAGMA synchronous=FULL")
            yield connection
        finally:
            connection.close()
    else:
        import psycopg
        from psycopg.conninfo import conninfo_to_dict
        from kiwoom_monitor.central_server.postgres_access import DBWriterContext, owned_connection_scope
        options = conninfo_to_dict(dsn).get("options", "")
        options += " -c timezone=Asia/Seoul -c search_path=" + schema
        context = DBWriterContext(writer_family="operator.rank_import", writer_kind="rank_target",
                                  operation="offline_target_transaction", access_mode="write")
        with owned_connection_scope(lambda: psycopg.connect(dsn, options=options.strip(), connect_timeout=10), context) as connection:
            yield connection


def _begin(connection, pg):
    connection.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED" if pg else "BEGIN IMMEDIATE")


def _owner(directory, plan, plan_sha):
    path = directory / "owner.json"
    if not path.is_file() or path.is_symlink():
        raise PreservedRankImportConflict("existing directory is not an owned offline target")
    owner = json.loads(path.read_text(encoding="utf-8"))
    if owner.get("plan_sha256") != plan_sha or owner.get("plan") != plan:
        raise PreservedRankImportConflict("target UUID/operation/immutable plan differs")
    db = directory / "rank.sqlite3"
    if _no_redirect(db) != db or not db.is_file() or list(_stat(db)[:2]) != owner["file_identity"]:
        raise PreservedRankImportConflict("owned target file identity changed")
    return owner


def prepare_target(artifact, *, plan, plan_sha256, directory=None, dsn=None, create=False, now_provider=kst_now):
    """Only explicit new offline file/schema, or exact owned UUID resume."""
    pg = directory is None
    schema = "rank_import_" + plan["destination_uuid"] if pg else None
    if not pg:
        directory = _directory(artifact, directory)
        if create:
            directory.mkdir(exist_ok=False)
            database = directory / "rank.sqlite3"
            with database.open("xb"):
                pass
            owner = {"contract": "offline_rank_file/v1", "plan_sha256": plan_sha256, "plan": plan,
                     "file_identity": list(_stat(database)[:2]), "schema_ready": False}
            write_json_backup(directory / "owner.json", owner)
        owner = _owner(directory, plan, plan_sha256)
        initialize = not owner["schema_ready"]
        if not create and not initialize:
            return {"directory": directory, "dsn": None, "schema": None}
    else:
        initialize = create
        if not create:
            # A resume may not adopt/initialize a foreign empty schema. The
            # actual data transaction requires its existing exact descriptor.
            return {"directory": None, "dsn": dsn, "schema": schema}
    with _target_connection(directory=directory, dsn=dsn, schema=schema) as connection:
        try:
            _begin(connection, pg)
            cursor = connection.cursor()
            try:
                if pg and create:
                    from psycopg import sql
                    cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
                    cursor.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema)))
                if initialize:
                    CentralSchemaMigrationRunner(cursor, "postgres" if pg else "sqlite").apply(
                        central_schema_migrations("postgres" if pg else "sqlite"))
                prepare_preserved_rank_target(cursor, postgres=pg, plan=plan, plan_sha256=plan_sha256, now_provider=now_provider)
            finally:
                cursor.close()
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    if not pg and not owner["schema_ready"]:
        write_json_backup(directory / "owner.json", {**owner, "schema_ready": True})
    return {"directory": directory, "dsn": dsn, "schema": schema}


def _accept(target, plan, plan_sha, rows, unchanged, now_provider):
    pg = target["directory"] is None
    uncertain = connection = None
    try:
        with _target_connection(**target) as connection:
            try:
                _begin(connection, pg)
                cursor = connection.cursor()
                try:
                    result = accept_preserved_rank_history(cursor, postgres=pg, plan=plan, plan_sha256=plan_sha,
                        rows_factory=rows, validate_frozen_input=unchanged, now_provider=now_provider)
                finally:
                    cursor.close()
                try:
                    connection.commit()
                except Exception as error:
                    uncertain = _CommitUncertain("native import COMMIT outcome requires exact receipt")
                    raise uncertain from error
                return result
            except BaseException as error:
                try:
                    connection.rollback()
                except Exception as cleanup_error:
                    error.add_note("rollback cleanup also failed: " + type(cleanup_error).__name__)
                raise
    except BaseException as error:
        # A disconnected COMMIT may also make rollback/context cleanup fail.
        # Keep its original cause, but never open a receipt resolver while this
        # acquired owner is still live or its release cannot be established.
        closed = (bool(connection.closed) if pg else False) if connection is not None else False
        if not pg and connection is not None:
            try:
                connection.in_transaction
            except sqlite3.ProgrammingError:
                closed = True
        if uncertain is not None and closed:
            if error is not uncertain:
                uncertain.add_note("owner cleanup also failed: " + type(error).__name__)
            raise uncertain
        if uncertain is not None:
            raise PreservedRankImportConflict("uncertain COMMIT owner release is not confirmed") from error
        raise


def import_artifact(artifact: Path, *, source_instance: str, manifest_sha256: str, destination_uuid: str,
                    operation_id: str, directory: Path | None = None, dsn: str | None = None,
                    create: bool, now_provider=kst_now):
    if (directory is None) == (dsn is None):
        raise ValueError("one explicit new/offline target is required")
    plan, plan_sha, rows, unchanged = freeze_artifact(artifact, source_instance=source_instance,
        manifest_sha256=manifest_sha256, destination_uuid=destination_uuid, operation_id=operation_id, postgres=directory is None)
    target = prepare_target(artifact, plan=plan, plan_sha256=plan_sha, directory=directory,
                            dsn=dsn, create=create, now_provider=now_provider)
    try:
        return _accept(target, plan, plan_sha, rows, unchanged, now_provider)
    except _CommitUncertain:
        # _accept has rolled back/closed its actual owner. Read/revalidate only;
        # an absent receipt never starts another write attempt automatically.
        with _target_connection(**target) as connection:
            _begin(connection, directory is None)
            cursor = connection.cursor()
            try:
                existing = preserved_rank_receipt(cursor, postgres=directory is None,
                    destination_uuid=plan["destination_uuid"], operation_id=operation_id,
                    plan_sha256=plan_sha, require_complete=False)
            finally:
                cursor.close()
            connection.rollback()
        if existing is None:
            raise
        # Native replay verifies every target field against the same frozen
        # artifact and returns its original receipt; no source or timestamp regen.
        return {**_accept(target, plan, plan_sha, rows, unchanged, now_provider), "recovered_after_ack_loss": True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--source-instance", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--destination-uuid", required=True)
    parser.add_argument("--operation-id", required=True)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--sqlite-target-directory", type=Path)
    target.add_argument("--postgres-target-dsn-env")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--create-offline-target", action="store_true")
    mode.add_argument("--resume-owned-target", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = import_artifact(args.artifact, source_instance=args.source_instance,
            manifest_sha256=args.manifest_sha256, destination_uuid=args.destination_uuid, operation_id=args.operation_id,
            directory=args.sqlite_target_directory,
            dsn=os.environ[args.postgres_target_dsn_env] if args.postgres_target_dsn_env else None,
            create=args.create_offline_target)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except KeyboardInterrupt:
        print(json.dumps({"state": "interrupted_offline_target_requires_exact_receipt_resume"}))
        return 130
    except Exception as error:
        print(json.dumps({"state": "failed_offline_target_not_activated", "error_type": type(error).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
