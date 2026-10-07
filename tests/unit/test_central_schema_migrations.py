from __future__ import annotations

import sqlite3
import unittest
from contextlib import closing

from kiwoom_monitor.central_server.schema_migrations import (
    CentralSchemaMigration,
    CentralSchemaMigrationError,
    CentralSchemaMigrationRunner,
)


class _RecordingCursor:
    def __init__(self) -> None:
        self.queries: list[tuple[str, object]] = []

    def execute(self, query: str, params: object = None) -> None:
        self.queries.append((query, params))

    def fetchall(self) -> list[tuple[object, ...]]:
        return []


class CentralSchemaMigrationTests(unittest.TestCase):
    def test_sqlite_failed_migration_rolls_back_ddl_and_version(self) -> None:
        with closing(sqlite3.connect(":memory:")) as connection:
            migration = CentralSchemaMigration(
                1,
                "broken",
                ("CREATE TABLE unfinished(value TEXT)", "THIS IS NOT SQL"),
                (),
            )
            with self.assertRaises(sqlite3.OperationalError):
                with connection:
                    cursor = connection.cursor()
                    CentralSchemaMigrationRunner(cursor, "sqlite").apply((migration,))
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }

        self.assertNotIn("unfinished", tables)
        self.assertNotIn("central_schema_migrations", tables)

    def test_postgres_uses_timestamptz_placeholders_and_postgres_statements(self) -> None:
        cursor = _RecordingCursor()
        migration = CentralSchemaMigration(
            1,
            "baseline",
            ("SQLITE ONLY",),
            ("CREATE TABLE postgres_only(value JSONB)",),
        )

        applied = CentralSchemaMigrationRunner(cursor, "postgres").apply((migration,))
        sql = "\n".join(query for query, _params in cursor.queries)

        self.assertEqual((1,), applied)
        self.assertIn("applied_at TIMESTAMPTZ", sql)
        self.assertIn("postgres_only(value JSONB)", sql)
        self.assertIn("VALUES(%s,%s,%s)", sql)
        self.assertNotIn("SQLITE ONLY", sql)

    def test_non_contiguous_plan_is_rejected_before_sql(self) -> None:
        cursor = _RecordingCursor()
        with self.assertRaisesRegex(CentralSchemaMigrationError, "contiguous"):
            CentralSchemaMigrationRunner(cursor, "sqlite").apply((
                CentralSchemaMigration(2, "late", (), ()),
            ))
        self.assertEqual([], cursor.queries)


if __name__ == "__main__":
    unittest.main()
