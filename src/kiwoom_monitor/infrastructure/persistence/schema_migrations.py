"""SQLite 스키마 변경을 버전 순서와 트랜잭션으로 적용한다."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime


MigrationStep = Callable[[sqlite3.Connection], None]


class SchemaMigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class SQLiteMigration:
    version: int
    name: str
    upgrade: MigrationStep


class SQLiteMigrationRunner:
    def __init__(self, connection: sqlite3.Connection, table: str = "schema_migrations") -> None:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
            raise ValueError("migration table name must be a plain SQL identifier")
        self._connection = connection
        self._table = table

    def prepare(self) -> None:
        self._connection.execute(
            f"CREATE TABLE IF NOT EXISTS {self._table} ("
            "version INTEGER PRIMARY KEY, name TEXT NOT NULL DEFAULT '', "
            "applied_at TEXT NOT NULL DEFAULT '')"
        )
        columns = {
            str(row[1]) for row in self._connection.execute(f"PRAGMA table_info({self._table})")
        }
        if "name" not in columns:
            self._connection.execute(
                f"ALTER TABLE {self._table} ADD COLUMN name TEXT NOT NULL DEFAULT ''"
            )
        if "applied_at" not in columns:
            self._connection.execute(
                f"ALTER TABLE {self._table} ADD COLUMN applied_at TEXT NOT NULL DEFAULT ''"
            )

    def ensure_compatible(self, latest_version: int) -> None:
        row = self._connection.execute(
            f"SELECT MAX(version) FROM {self._table}"
        ).fetchone()
        current = int(row[0] or 0)
        if current > latest_version:
            raise SchemaMigrationError(
                f"database schema version {current} is newer than supported {latest_version}"
            )

    def record_applied(self, version: int, name: str) -> None:
        if version <= 0 or not name.strip():
            raise ValueError("migration version and name are required")
        row = self._connection.execute(
            f"SELECT name FROM {self._table} WHERE version=?", (version,)
        ).fetchone()
        if row is None:
            self._connection.execute(
                f"INSERT INTO {self._table}(version,name,applied_at) VALUES(?,?,?)",
                (version, name, datetime.now(UTC).isoformat()),
            )
            return
        stored_name = str(row[0] or "")
        if stored_name and stored_name != name:
            raise SchemaMigrationError(
                f"migration {version} is already recorded as {stored_name!r}, not {name!r}"
            )
        if not stored_name:
            self._connection.execute(
                f"UPDATE {self._table} SET name=?,applied_at=? WHERE version=?",
                (name, datetime.now(UTC).isoformat(), version),
            )

    def apply(self, migrations: Iterable[SQLiteMigration]) -> tuple[int, ...]:
        plan = tuple(migrations)
        self._validate_plan(plan)
        self.prepare()
        self.ensure_compatible(plan[-1].version if plan else 0)
        # migration 메타 테이블 준비를 각 실제 변경의 savepoint와 분리한다.
        self._connection.commit()
        applied = {
            int(row[0]) for row in self._connection.execute(
                f"SELECT version FROM {self._table}"
            )
        }
        newly_applied: list[int] = []
        for migration in plan:
            if migration.version in applied:
                self.record_applied(migration.version, migration.name)
                continue
            savepoint = f"schema_migration_{migration.version}"
            self._connection.execute(f"SAVEPOINT {savepoint}")
            try:
                migration.upgrade(self._connection)
                self.record_applied(migration.version, migration.name)
                self._connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            except Exception:
                self._connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                self._connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                raise
            newly_applied.append(migration.version)
        return tuple(newly_applied)

    @staticmethod
    def _validate_plan(plan: tuple[SQLiteMigration, ...]) -> None:
        versions = tuple(migration.version for migration in plan)
        if not versions:
            return
        if versions[0] != 1 or versions != tuple(range(1, versions[-1] + 1)):
            raise SchemaMigrationError("migration versions must be contiguous and start at 1")
        if any(not migration.name.strip() for migration in plan):
            raise SchemaMigrationError("migration names must not be empty")
