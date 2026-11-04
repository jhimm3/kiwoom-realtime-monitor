"""매매일지 전용 SQLite 저장소."""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import uuid
from collections.abc import Callable
from contextlib import closing
from datetime import datetime
from pathlib import Path

from kiwoom_monitor.domain.time_basis import as_kst

from kiwoom_monitor.infrastructure.persistence.journal_bar_repository import (
    BarBackfillState,
    JournalBarRepositoryMixin,
)
from kiwoom_monitor.infrastructure.persistence.journal_schema import (
    JOURNAL_SCHEMA_INDEXES, JOURNAL_SCHEMA_TABLES, initialize_journal_database,
)
from kiwoom_monitor.infrastructure.persistence.journal_snapshot_repository import (
    JournalSnapshotRepositoryMixin,
    TradeEntrySnapshot,
)
from kiwoom_monitor.infrastructure.persistence.journal_strategy_settings_repository import (
    JournalStrategySettingsRepositoryMixin,
)
from kiwoom_monitor.infrastructure.persistence.journal_trade_repository import JournalTradeRepositoryMixin
from kiwoom_monitor.infrastructure.persistence.sqlite_read_snapshot import readonly_sqlite_snapshot


JOURNAL_SELECTED_SETTING_KEYS = (
    "personal_trade_rules", "personal_trade_rules_structured", "strategy_packs",
)
_SELECTED_SETTINGS_SQL = (
    "SELECT setting_key,value_json,updated_at FROM journal_settings "
    "WHERE setting_key IN (?,?,?) ORDER BY setting_key"
)
_SELECTED_SETTINGS_OWNER = "journal-selected-settings-owner.json"


def _selected_document(raw: str) -> dict:
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate selected journal ownership field")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite journal ownership field")))


def _same_selected_rows(actual: list[tuple], expected: list[tuple]) -> bool:
    return len(actual) == len(expected) and all(
        type(left) is type(right)
        and (left.hex() == right.hex() if type(left) is float else left == right)
        for a, b in zip(actual, expected, strict=True)
        for left, right in zip(a, b, strict=True)
    )


def _read_selected_settings_receipt(path: Path, owner: dict, rows: list[tuple]) -> dict | None:
    with readonly_sqlite_snapshot(path, frozen=False) as connection:
        if not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='journal_selected_settings_receipts'"
        ).fetchone():
            return None
        stored = connection.execute(
            "SELECT operation_id,receipt_json FROM journal_selected_settings_receipts LIMIT 2"
        ).fetchall()
        if not stored:
            return None
        if len(stored) != 1 or stored[0][0] != owner["operation_id"]:
            raise ValueError("selected journal receipt ownership conflict")
        receipt = _selected_document(stored[0][1])
        expected = dict(owner, rows=len(rows), missing_keys=sorted(
            set(JOURNAL_SELECTED_SETTING_KEYS) - {row[0] for row in rows}))
        if (not isinstance(receipt, dict) or set(receipt) != set(expected) | {"accepted_at"}
                or any(receipt[key] != value for key, value in expected.items())
                or not isinstance(receipt["accepted_at"], str)
                or datetime.fromisoformat(receipt["accepted_at"]).utcoffset() is None
                or not _same_selected_rows(
                    connection.execute(_SELECTED_SETTINGS_SQL, JOURNAL_SELECTED_SETTING_KEYS).fetchall(), rows)):
            raise ValueError("selected journal raw data or first receipt conflict")
        return receipt


def _require_empty_selected_target(path: Path) -> None:
    # A reservation permits recovery of our interrupted initialization, never
    # adoption of a journal that has since acquired user/business data.
    with readonly_sqlite_snapshot(path, frozen=False) as connection:
        objects = connection.execute(
            "SELECT name,type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall()
        if any(name not in (JOURNAL_SCHEMA_TABLES if kind == "table" else
                            JOURNAL_SCHEMA_INDEXES if kind == "index" else ()) for name, kind in objects):
            raise ValueError("unaccepted journal target contains foreign schema objects")
        names = [name for name, kind in objects if kind == "table" and name != "journal_schema_migrations"]
        if names:
            populated = " OR ".join(
                'EXISTS(SELECT 1 FROM "' + name.replace('"', '""') + '")' for name in names)
            if connection.execute("SELECT " + populated).fetchone()[0]:
                raise ValueError("unaccepted journal target contains data")


class JournalRepository(
    JournalSnapshotRepositoryMixin,
    JournalStrategySettingsRepositoryMixin,
    JournalTradeRepositoryMixin,
    JournalBarRepositoryMixin,
):
    def __init__(self, path: Path) -> None:
        self._path = path
        initialize_journal_database(path)

    @classmethod
    def accept_preserved_current_settings(
        cls, path: Path, *, rows: list[tuple], ownership: dict,
        receipt_at: Callable[[], str],
    ) -> dict:
        """Accept selected raw settings into one reserved offline journal.

        The caller owns the full-root lease and validates its monitor owner.
        This class command deliberately bypasses __init__ on exact replay.
        A reservation is only crash-recovery identity; rows and the first
        acceptance receipt become durable together in the journal transaction.
        """
        if (not isinstance(rows, list) or len(rows) > 3
                or any(not isinstance(row, tuple) or len(row) != 3
                       or row[0] not in JOURNAL_SELECTED_SETTING_KEYS
                       or any(type(value) not in (type(None), int, float, str, bytes)
                              or type(value) is int and not -(2**63) <= value < 2**63
                              or type(value) is float and math.isnan(value) for value in row)
                       for row in rows)
                or [row[0] for row in rows] != sorted({row[0] for row in rows})):
            raise ValueError("invalid selected journal settings rows")
        keys = {"format", "operation_id", "target_id", "source_instance", "source_operation_id",
                "artifact_sha256", "monitor_role", "monitor_seed_sha256", "monitor_layout_sha256",
                "runtime_state", "order_authority"}
        if (not isinstance(ownership, dict) or set(ownership) != keys
                or ownership.get("format") != "selected-journal-settings-target/v1"
                or ownership.get("runtime_state") != "OFFLINE_UNBOUND"
                or ownership.get("order_authority") is not False
                or any(not isinstance(ownership[key], str)
                       or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", ownership[key])
                       for key in ("operation_id", "target_id", "source_instance", "source_operation_id", "monitor_role"))
                or any(not isinstance(ownership[key], str) or not re.fullmatch(r"[0-9a-f]{64}", ownership[key])
                       for key in ("artifact_sha256", "monitor_seed_sha256", "monitor_layout_sha256"))):
            raise ValueError("invalid selected journal settings ownership")
        owner = dict(ownership)
        path = Path(path).absolute()
        if path.resolve() != path or path.is_symlink() or path.name != "journal.sqlite3" or not path.parent.is_dir():
            raise ValueError("redirected or unexpected selected journal target")
        marker = path.parent / _SELECTED_SETTINGS_OWNER
        reserved = marker.exists()
        if marker.is_symlink() or reserved and _selected_document(marker.read_text(encoding="utf-8")) != owner:
            raise ValueError("selected journal target reservation conflict")
        if path.exists():
            receipt = _read_selected_settings_receipt(path, owner, rows)
            if receipt is not None:
                return dict(state="EXACT_NOOP", receipt=receipt, reopened=True)
            if not reserved:
                raise ValueError("existing journal has no matching selected-settings reservation")
            _require_empty_selected_target(path)
        if not reserved:
            temporary = marker.with_name(marker.name + ".partial-" + uuid.uuid4().hex)
            with temporary.open("xb") as stream:
                stream.write(json.dumps(owner, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, marker)  # Same-directory publication never overwrites.
            temporary.unlink()
        initialize_journal_database(path)  # Actual native schema, never old migration receipts.
        failure, cleanup_errors = None, []
        connection = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True)
        receipt = None
        try:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            if (connection.execute("SELECT 1 FROM journal_settings LIMIT 1").fetchone()
                    or connection.execute("SELECT 1 FROM journal_selected_settings_receipts LIMIT 1").fetchone()):
                raise ValueError("unaccepted selected journal target contains data")
            accepted_at = datetime.fromisoformat(receipt_at())
            if accepted_at.utcoffset() is None:
                raise ValueError("aware journal receipt clock is required")
            receipt = dict(owner, accepted_at=as_kst(accepted_at).isoformat(), rows=len(rows),
                           missing_keys=sorted(set(JOURNAL_SELECTED_SETTING_KEYS) - {row[0] for row in rows}))
            for row in rows:
                connection.execute("INSERT INTO journal_settings(setting_key,value_json,updated_at) VALUES(?,?,?)", row)
            if not _same_selected_rows(
                    connection.execute(_SELECTED_SETTINGS_SQL, JOURNAL_SELECTED_SETTING_KEYS).fetchall(), rows):
                raise ValueError("selected journal primitive roundtrip conflict")
            connection.execute(
                "INSERT INTO journal_selected_settings_receipts VALUES(?,?)",
                (owner["operation_id"], json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
            )
            connection.commit()
        except BaseException as error:
            failure = error
        finally:
            if failure is not None:
                try:
                    connection.rollback()
                except BaseException as error:
                    cleanup_errors.append(("rollback", error))
            try:
                connection.close()
            except BaseException as error:
                cleanup_errors.append(("close", error))
        try:
            connection.in_transaction
            native_closed = False
        except sqlite3.ProgrammingError:
            native_closed = True
        except BaseException as error:
            native_closed = False
            cleanup_errors.append(("closed_observation", error))
        outcome = dict(primary_error_type=type(failure).__name__ if failure else None,
                       cleanup_errors=[dict(phase=phase, error_type=type(error).__name__) for phase, error in cleanup_errors],
                       native_connection_closed=native_closed)
        errors = ([failure] if failure is not None else []) + [error for _, error in cleanup_errors]
        control = next((error for error in errors if not isinstance(error, Exception)), None)
        if control is not None:
            control.native_outcome = outcome
            raise control
        if not native_closed:
            error = ValueError("selected journal native owner close is unconfirmed")
            error.native_outcome = outcome
            raise error from (failure or (cleanup_errors[0][1] if cleanup_errors else None))
        recovered = _read_selected_settings_receipt(path, owner, rows)
        if recovered is not None:
            if recovered != receipt:
                raise ValueError("selected journal first receipt changed")
            return dict(state="ACCEPTED_AFTER_ACK_UNCERTAINTY" if failure or cleanup_errors else "ACCEPTED",
                        receipt=recovered, reopened=True, native_outcome=outcome)
        error = ValueError("selected journal native acceptance is unconfirmed")
        error.native_outcome = outcome
        raise error from (failure or (cleanup_errors[0][1] if cleanup_errors else None))

    def remember_stock(self, code: str, name: str, now: datetime) -> None:
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                connection.execute(
                "INSERT INTO journal_stocks VALUES (?, ?, ?, 'opened', ?) "
                "ON CONFLICT(trade_date, stock_code) DO UPDATE SET stock_name=excluded.stock_name, last_opened_at=excluded.last_opened_at",
                    (now.date().isoformat(), code, name, now.isoformat(timespec="seconds")),
                )
