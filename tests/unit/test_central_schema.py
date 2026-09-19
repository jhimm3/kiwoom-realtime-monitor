from __future__ import annotations

import unittest

from kiwoom_monitor.central_server.central_schema import (
    CENTRAL_SCHEMA_BASELINE_NAME,
    CENTRAL_MARKET_METADATA_MIGRATION_NAME,
    CENTRAL_MARKET_STATE_TIME_REPAIR_MIGRATION_NAME,
    CENTRAL_SECOND_TRADE_BARS_MIGRATION_NAME,
    CENTRAL_THEME_HISTORY_MIGRATION_NAME,
    CENTRAL_NEWS_HISTORY_MIGRATION_NAME,
    CENTRAL_NEWS_EVENT_MIGRATION_NAME,
    CENTRAL_NEWS_SOURCE_MIGRATION_NAME,
    CENTRAL_MARKET_EVENT_MIGRATION_NAME,
    CENTRAL_NEWS_REUSE_MIGRATION_NAME,
    CENTRAL_N3_STOCK_NEWS_MIGRATION_NAME,
    CENTRAL_OBSERVATION_HISTORY_MIGRATION_NAME,
    CENTRAL_RESEARCH_EXPORT_MIGRATION_NAME,
    CENTRAL_MINUTE_BAR_REVISION_MIGRATION_NAME,
    CENTRAL_SCHEMA_VERSION,
    CENTRAL_SHADOW_CANDIDATE_MIGRATION_NAME,
    CENTRAL_MOCK_EXECUTION_MIGRATION_NAME,
    CENTRAL_ACCOUNT_IDENTITY_MIGRATION_NAME,
    CENTRAL_ACCOUNT_SCOPE_ALIAS_MIGRATION_NAME,
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

        self.assertEqual(19, len(migrations))
        self.assertEqual(1, migrations[0].version)
        self.assertEqual(CENTRAL_SCHEMA_BASELINE_NAME, migrations[0].name)
        self.assertEqual(sqlite_schema_statements(), migrations[0].sqlite_statements)
        self.assertEqual(postgres_schema_statements(), migrations[0].postgres_statements)
        self.assertEqual(2, migrations[1].version)
        self.assertEqual(CENTRAL_MARKET_METADATA_MIGRATION_NAME, migrations[1].name)
        self.assertIn("effective_at TEXT", migrations[1].sqlite_statements[0])
        self.assertIn("effective_at TIMESTAMPTZ", migrations[1].postgres_statements[0])
        self.assertEqual(3, migrations[2].version)
        self.assertEqual(CENTRAL_MARKET_STATE_TIME_REPAIR_MIGRATION_NAME, migrations[2].name)
        self.assertIn("T88:88", "\n".join(migrations[2].sqlite_statements))
        self.assertIn("Asia/Seoul", "\n".join(migrations[2].postgres_statements))
        self.assertEqual(4, migrations[3].version)
        self.assertEqual(CENTRAL_SECOND_TRADE_BARS_MIGRATION_NAME, migrations[3].name)
        self.assertIn("trade_value_won INTEGER", migrations[3].sqlite_statements[0])
        self.assertIn("trade_value_won BIGINT", migrations[3].postgres_statements[0])
        self.assertEqual(5, migrations[4].version)
        self.assertEqual(CENTRAL_THEME_HISTORY_MIGRATION_NAME, migrations[4].name)
        self.assertIn("document_json TEXT", migrations[4].sqlite_statements[0])
        self.assertIn("document_json JSONB", migrations[4].postgres_statements[0])
        self.assertEqual(6, migrations[5].version)
        self.assertEqual(CENTRAL_NEWS_HISTORY_MIGRATION_NAME, migrations[5].name)
        self.assertIn("central_news_jobs", "\n".join(migrations[5].sqlite_statements))
        self.assertIn("JSONB", "\n".join(migrations[5].postgres_statements))
        self.assertEqual(7, migrations[6].version)
        self.assertEqual(CENTRAL_NEWS_EVENT_MIGRATION_NAME, migrations[6].name)
        self.assertIn("central_news_event_revisions", "\n".join(migrations[6].sqlite_statements))
        self.assertIn("amount_won BIGINT", "\n".join(migrations[6].postgres_statements))
        self.assertEqual(8, migrations[7].version)
        self.assertEqual(CENTRAL_NEWS_SOURCE_MIGRATION_NAME, migrations[7].name)
        self.assertIn("central_news_source_cursors", "\n".join(migrations[7].sqlite_statements))
        self.assertIn("document_json JSONB", "\n".join(migrations[7].postgres_statements))
        self.assertEqual(9, migrations[8].version)
        self.assertEqual(CENTRAL_MARKET_EVENT_MIGRATION_NAME, migrations[8].name)
        self.assertIn("central_vi_event_revisions", "\n".join(migrations[8].sqlite_statements))
        self.assertIn("central_upper_limit_fact_revisions", "\n".join(migrations[8].postgres_statements))
        self.assertEqual(10, migrations[9].version)
        self.assertEqual(CENTRAL_NEWS_REUSE_MIGRATION_NAME, migrations[9].name)
        self.assertIn("source_id,identity", migrations[9].sqlite_statements[0])
        self.assertIn("source_id,identity", migrations[9].postgres_statements[0])
        self.assertEqual(11, migrations[10].version)
        self.assertEqual(CENTRAL_N3_STOCK_NEWS_MIGRATION_NAME, migrations[10].name)
        self.assertIn("stock_code,relation_status", migrations[10].sqlite_statements[0])
        self.assertIn("stock_code,relation_status", migrations[10].postgres_statements[0])
        self.assertEqual(12, migrations[11].version)
        self.assertEqual(CENTRAL_OBSERVATION_HISTORY_MIGRATION_NAME, migrations[11].name)
        self.assertIn("central_observation_revisions", migrations[11].sqlite_statements[0])
        self.assertIn("TIMESTAMPTZ", migrations[11].postgres_statements[0])
        self.assertEqual(13, migrations[12].version)
        self.assertEqual(CENTRAL_RESEARCH_EXPORT_MIGRATION_NAME, migrations[12].name)
        self.assertIn("central_research_exports", migrations[12].sqlite_statements[0])
        self.assertIn("central_research_export_members", migrations[12].postgres_statements[1])
        self.assertEqual(14, migrations[13].version)
        self.assertEqual(CENTRAL_MINUTE_BAR_REVISION_MIGRATION_NAME, migrations[13].name)
        self.assertIn("central_minute_bar_operations", migrations[13].sqlite_statements[0])
        self.assertIn("TIMESTAMPTZ", migrations[13].postgres_statements[0])
        self.assertEqual(15, migrations[14].version)
        self.assertEqual(CENTRAL_SHADOW_CANDIDATE_MIGRATION_NAME, migrations[14].name)
        self.assertIn("central_shadow_candidate_events", "\n".join(migrations[14].sqlite_statements))
        self.assertIn("BIGSERIAL", "\n".join(migrations[14].postgres_statements))
        self.assertEqual(16, migrations[15].version)
        self.assertEqual(CENTRAL_MOCK_EXECUTION_MIGRATION_NAME, migrations[15].name)
        self.assertIn("central_execution_intents", "\n".join(migrations[15].sqlite_statements))
        self.assertIn("central_execution_runtime_leases", "\n".join(migrations[15].postgres_statements))
        self.assertEqual(17, migrations[16].version)
        self.assertEqual(CENTRAL_ACCOUNT_IDENTITY_MIGRATION_NAME, migrations[16].name)
        self.assertIn("central_account_registry", "\n".join(migrations[16].sqlite_statements))
        self.assertIn("TIMESTAMPTZ", "\n".join(migrations[16].postgres_statements))
        self.assertEqual(18, migrations[17].version)
        self.assertEqual(CENTRAL_SCHEMA_VERSION, migrations[18].version)
        self.assertEqual(CENTRAL_ACCOUNT_SCOPE_ALIAS_MIGRATION_NAME, migrations[17].name)
        self.assertIn("central_account_scope_aliases", "\n".join(migrations[17].sqlite_statements))
        self.assertIn("TIMESTAMPTZ", "\n".join(migrations[17].postgres_statements))


if __name__ == "__main__":
    unittest.main()
