"""중앙 SQLite/PostgreSQL 스키마를 같은 버전 계약으로 초기화한다."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol


CentralDialect = Literal["sqlite", "postgres"]


class SQLCursor(Protocol):
    def execute(self, query: str, params: object = ...) -> object: ...
    def fetchall(self) -> list[tuple[object, ...]]: ...


class CentralSchemaMigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class CentralSchemaMigration:
    version: int
    name: str
    sqlite_statements: tuple[str, ...]
    postgres_statements: tuple[str, ...]

    def statements(self, dialect: CentralDialect) -> tuple[str, ...]:
        return self.sqlite_statements if dialect == "sqlite" else self.postgres_statements


class CentralSchemaMigrationRunner:
    """호출자가 연 DB 트랜잭션 안에서 중앙 스키마 변경을 순서대로 적용한다."""

    TABLE = "central_schema_migrations"

    def __init__(self, cursor: SQLCursor, dialect: CentralDialect) -> None:
        self._cursor = cursor
        self._dialect = dialect
        self._placeholder = "?" if dialect == "sqlite" else "%s"

    def apply(self, migrations: Iterable[CentralSchemaMigration]) -> tuple[int, ...]:
        plan = tuple(migrations)
        self._validate_plan(plan)
        savepoint = "central_schema_migration_plan"
        self._cursor.execute(f"SAVEPOINT {savepoint}")
        try:
            applied = self._apply_plan(plan)
            self._cursor.execute(f"RELEASE SAVEPOINT {savepoint}")
            return applied
        except Exception:
            self._cursor.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            self._cursor.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise

    def _apply_plan(
        self, plan: tuple[CentralSchemaMigration, ...]
    ) -> tuple[int, ...]:
        self._prepare()
        self._cursor.execute(
            f"SELECT version,name FROM {self.TABLE} ORDER BY version"
        )
        rows = tuple(self._cursor.fetchall())
        latest_supported = plan[-1].version if plan else 0
        if rows and max(int(row[0]) for row in rows) > latest_supported:
            raise CentralSchemaMigrationError(
                f"central database schema is newer than supported {latest_supported}"
            )
        recorded = {int(row[0]): str(row[1] or "") for row in rows}
        newly_applied: list[int] = []
        for migration in plan:
            stored_name = recorded.get(migration.version)
            if stored_name is not None:
                if stored_name != migration.name:
                    raise CentralSchemaMigrationError(
                        f"central migration {migration.version} is recorded as "
                        f"{stored_name!r}, not {migration.name!r}"
                    )
                continue
            for statement in migration.statements(self._dialect):
                self._cursor.execute(statement)
            placeholders = ",".join((self._placeholder,) * 3)
            self._cursor.execute(
                f"INSERT INTO {self.TABLE}(version,name,applied_at) "
                f"VALUES({placeholders})",
                (migration.version, migration.name, datetime.now(UTC).isoformat()),
            )
            newly_applied.append(migration.version)
        return tuple(newly_applied)

    def _prepare(self) -> None:
        applied_at_type = "TEXT" if self._dialect == "sqlite" else "TIMESTAMPTZ"
        self._cursor.execute(
            f"CREATE TABLE IF NOT EXISTS {self.TABLE} ("
            "version INTEGER PRIMARY KEY, name TEXT NOT NULL, "
            f"applied_at {applied_at_type} NOT NULL)"
        )

    @staticmethod
    def _validate_plan(plan: tuple[CentralSchemaMigration, ...]) -> None:
        versions = tuple(migration.version for migration in plan)
        if not versions:
            return
        if versions[0] != 1 or versions != tuple(range(1, versions[-1] + 1)):
            raise CentralSchemaMigrationError(
                "central migration versions must be contiguous and start at 1"
            )
        if any(not migration.name.strip() for migration in plan):
            raise CentralSchemaMigrationError("central migration names must not be empty")
