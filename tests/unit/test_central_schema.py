from __future__ import annotations

import unittest

from kiwoom_monitor.central_server.central_schema import (
    CENTRAL_SCHEMA_BASELINE_NAME,
    CENTRAL_MARKET_METADATA_MIGRATION_NAME,
    CENTRAL_MARKET_STATE_TIME_REPAIR_MIGRATION_NAME,
    CENTRAL_SCHEMA_VERSION,
    CENTRAL_INDEXES,
    CENTRAL_TABLES,
    central_schema_migrations,
    postgres_schema_statements,
    sqlite_schema_statements,
)


class CentralSchemaTests(unittest.TestCase):
    def test_both_dialects_define_every_contract_object_once(self) -> None:
        migrations = central_schema_migrations()
        for statements in (
            tuple(statement for migration in migrations for statement in migration.sqlite_statements),
            tuple(statement for migration in migrations for statement in migration.postgres_statements),
        ):
            sql = "\n".join(statements)
            for name in CENTRAL_TABLES:
                self.assertEqual(1, sql.count(f"CREATE TABLE IF NOT EXISTS {name} ("), name)
            for name in CENTRAL_INDEXES:
                self.assertEqual(1, sql.count(f"CREATE INDEX IF NOT EXISTS {name} "), name)

    def test_dialects_keep_intentional_storage_type_differences(self) -> None:
        sqlite_sql = "\n".join(sqlite_schema_statements())
        postgres_sql = "\n".join(postgres_schema_statements())

        self.assertIn("payload_json TEXT", sqlite_sql)
        self.assertIn("payload_json JSONB", postgres_sql)
        self.assertIn("trading_date TEXT", sqlite_sql)
        self.assertIn("trading_date DATE", postgres_sql)

    def test_current_schema_keeps_baseline_and_adds_metadata_migration(self) -> None:
        migrations = central_schema_migrations()

        self.assertEqual(3, len(migrations))
        self.assertEqual(1, migrations[0].version)
        self.assertEqual(CENTRAL_SCHEMA_BASELINE_NAME, migrations[0].name)
        self.assertEqual(sqlite_schema_statements(), migrations[0].sqlite_statements)
        self.assertEqual(postgres_schema_statements(), migrations[0].postgres_statements)
        self.assertEqual(2, migrations[1].version)
        self.assertEqual(CENTRAL_MARKET_METADATA_MIGRATION_NAME, migrations[1].name)
        self.assertIn("effective_at TEXT", migrations[1].sqlite_statements[0])
        self.assertIn("effective_at TIMESTAMPTZ", migrations[1].postgres_statements[0])
        self.assertEqual(CENTRAL_SCHEMA_VERSION, migrations[2].version)
        self.assertEqual(CENTRAL_MARKET_STATE_TIME_REPAIR_MIGRATION_NAME, migrations[2].name)
        self.assertIn("T88:88", "\n".join(migrations[2].sqlite_statements))
        self.assertIn("Asia/Seoul", "\n".join(migrations[2].postgres_statements))


if __name__ == "__main__":
    unittest.main()
