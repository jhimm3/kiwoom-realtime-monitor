from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.application.news_analysis import assess_stock_news
from kiwoom_monitor.infrastructure.naver_news import StockNewsItem
from kiwoom_monitor.infrastructure.news_ai import AINewsAnalysis, AICompanyImpact, AIThemeCandidate
from kiwoom_monitor.infrastructure.persistence.news_ai_backup import NewsAIBackupService
from kiwoom_monitor.infrastructure.persistence.news_ai_repository import NewsAIRepository, news_identity
from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.infrastructure.persistence.news_database import (
    initialize_news_database,
    migrate_legacy_news_database,
)
from kiwoom_monitor.infrastructure.persistence.news_schema import (
    NEWS_ACCOUNT_SCOPE_NAME,
    NEWS_LINK_TOMBSTONE_NAME,
    NEWS_AI_THEME_CANDIDATES_NAME,
    NEWS_AI_PROVENANCE_NAME,
    NEWS_INPUT_SOURCE_NAME,
    NEWS_AI_INFERENCE_NAME,
    NEWS_SCHEMA_BASELINE_NAME,
    NEWS_SCHEMA_VERSION,
)
from kiwoom_monitor.infrastructure.persistence.schema_migrations import SchemaMigrationError
from kiwoom_monitor.infrastructure.persistence.stock_news_repository import StockNewsRepository


class NewsDatabaseTests(unittest.TestCase):
    def test_ai_backup_invalid_utf8_is_reported_without_changing_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, backup = root / "news.sqlite3", root / "ai.json"
            item = StockNewsItem(
                "공급계약", "100억원 계약", "https://example.com/1", "https://example.com/1",
                datetime.now(UTC), assess_stock_news("회사", "공급계약", "100억원 계약"),
            )
            repository = NewsAIRepository(source)
            repository.save(
                "000001", item, "gemini", "model", "hash",
                AINewsAnalysis("요약", "긍정", 80, "이유", ("계약",), (), "수주·계약"),
            )
            backup.write_bytes(b"\xff")

            with self.assertRaisesRegex(ValueError, "뉴스 AI 백업 파일을 읽을 수 없습니다"):
                NewsAIBackupService(source).import_from(backup)

            self.assertIsNotNone(repository.load("000001", item))

    def test_ai_backup_export_failure_keeps_previous_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, backup = root / "news.sqlite3", root / "ai.json"
            NewsAIRepository(source)
            backup.write_text("previous backup", encoding="utf-8")
            with patch("os.replace", side_effect=OSError("replace failed")):
                with self.assertRaises(OSError):
                    NewsAIBackupService(source).export_to(backup)
            self.assertEqual("previous backup", backup.read_text(encoding="utf-8"))
            self.assertEqual([], list(root.glob(".ai.json.*.tmp")))

    def test_existing_news_survives_schema_baseline_registration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "news.sqlite3"
            item = StockNewsItem(
                "공급계약", "100억원 계약", "https://example.com/1", "https://example.com/1",
                datetime.now(UTC), assess_stock_news("회사", "공급계약", "100억원 계약"),
            )
            StockNewsRepository(path).upsert("000001", (item,))
            NewsAIRepository(path).save(
                "000001", item, "gemini", "model", "hash",
                AINewsAnalysis("요약", "긍정", 80, "이유", (), (), "수주·계약"),
            )
            with closing(sqlite3.connect(path)) as connection:
                with connection:
                    connection.execute("DROP TABLE news_schema_migrations")

            initialize_news_database(path)
            with closing(sqlite3.connect(path)) as connection:
                versions = connection.execute(
                    "SELECT version,name FROM news_schema_migrations ORDER BY version"
                ).fetchall()

            self.assertEqual(
                [
                    (1, NEWS_SCHEMA_BASELINE_NAME), (2, NEWS_ACCOUNT_SCOPE_NAME),
                    (3, NEWS_LINK_TOMBSTONE_NAME),
                    (4, NEWS_AI_THEME_CANDIDATES_NAME),
                    (5, NEWS_AI_PROVENANCE_NAME),
                    (6, NEWS_INPUT_SOURCE_NAME),
                    (NEWS_SCHEMA_VERSION, NEWS_AI_INFERENCE_NAME),
                ],
                versions,
            )
            self.assertEqual(1, len(StockNewsRepository(path).load("000001")))
            self.assertIsNotNone(NewsAIRepository(path).load("000001", item))

    def test_news_repository_rejects_a_newer_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "news.sqlite3"
            initialize_news_database(path)
            with closing(sqlite3.connect(path)) as connection:
                with connection:
                    connection.execute(
                        "INSERT INTO news_schema_migrations(version,name,applied_at) "
                        "VALUES(8,'future','2026-09-10T00:00:00+00:00')"
                    )

            with self.assertRaisesRegex(SchemaMigrationError, "newer"):
                StockNewsRepository(path)

    def test_v1_journal_news_links_migrate_to_legacy_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "news.sqlite3"
            with closing(sqlite3.connect(path)) as connection:
                with connection:
                    connection.executescript("""
                        CREATE TABLE journal_news_links(
                            group_id TEXT NOT NULL, stock_code TEXT NOT NULL,
                            identity TEXT NOT NULL, linked_at TEXT NOT NULL,
                            PRIMARY KEY(group_id,stock_code,identity));
                        CREATE TABLE stock_news_ai(stock_code TEXT,identity TEXT);
                        CREATE TABLE news_ai_shared(identity TEXT);
                        INSERT INTO journal_news_links VALUES(
                            'group-1','005930','article-1','2026-09-10T00:00:00');
                        CREATE TABLE news_schema_migrations(
                            version INTEGER PRIMARY KEY,name TEXT NOT NULL,applied_at TEXT NOT NULL);
                        INSERT INTO news_schema_migrations VALUES(
                            1,'current_news_schema_baseline','2026-09-10T00:00:00');
                    """)

            initialize_news_database(path)

            with closing(sqlite3.connect(path)) as connection:
                row = connection.execute(
                    "SELECT origin_broker,origin_environment,origin_account_ref,"
                    "canonical_account_ref FROM journal_news_links"
                ).fetchone()
            self.assertEqual(
                ("legacy", "unknown", "legacy-unassigned", "legacy-unassigned"), row,
            )

    def test_migrates_news_tables_out_of_main_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            main, news = root / "monitor.sqlite3", root / "news.sqlite3"
            Database(main).initialize()
            item = StockNewsItem(
                "공급계약", "100억원 계약", "https://example.com/1", "https://example.com/1",
                datetime.now(UTC), assess_stock_news("회사", "공급계약", "100억원 계약"),
            )
            StockNewsRepository(main).upsert("000001", (item,))
            NewsAIRepository(main).save(
                "000001", item, "gemini", "model", "hash",
                AINewsAnalysis("요약", "긍정", 80, "이유", (), (), "수주·계약"),
            )

            migrate_legacy_news_database(main, news)

            self.assertEqual(1, len(StockNewsRepository(news).load("000001")))
            self.assertIsNotNone(NewsAIRepository(news).load("000001", item))
            connection = sqlite3.connect(main)
            try:
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            finally:
                connection.close()
            self.assertNotIn("stock_news", tables)
            self.assertNotIn("stock_news_ai", tables)
            with closing(sqlite3.connect(main)) as connection:
                marker = connection.execute(
                    "SELECT version,name,completed_at FROM legacy_news_transfer_migrations"
                ).fetchone()
            self.assertEqual((1, "main_news_tables_to_news_database"), marker[:2])
            self.assertTrue(marker[2])
            migrate_legacy_news_database(main, news)
            with closing(sqlite3.connect(main)) as connection:
                self.assertEqual(marker, connection.execute(
                    "SELECT version,name,completed_at FROM legacy_news_transfer_migrations"
                ).fetchone())
            news.unlink()
            with self.assertRaisesRegex(RuntimeError, "대상 파일이 없습니다"):
                migrate_legacy_news_database(main, news)
            self.assertFalse(news.exists())

    def test_ai_backup_restores_results_without_news_articles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target, backup = root / "source.sqlite3", root / "target.sqlite3", root / "ai.json"
            item = StockNewsItem(
                "공급계약", "100억원 계약", "https://example.com/1", "https://example.com/1",
                datetime.now(UTC), assess_stock_news("회사", "공급계약", "100억원 계약"),
            )
            analysis = AINewsAnalysis(
                "요약", "긍정", 80, "이유", ("계약",), ("실행 위험",), "수주·계약",
                (AICompanyImpact("회사", "긍정", 80, "이유"),),
                (AIThemeCandidate("신규 산업", 84, "계약 근거"),),
            )
            provenance = {"version": 1, "status": "ACCEPTED", "origin": "native_accepted", "analysis_revision_id": "analysis-original",
                          "target_id": "000001", "article_revision_id": "article-original",
                          "body_revision_id": "body-original", "available_at": 105.0,
                          "computed_at": 100.0, "input_hash": "hash", "output_hash": "original-output-hash",
                          "provider": "gemini", "model": "model", "prompt_version": "original-prompt",
                          "schema_version": "original-schema", "requester": {"kind": "direct"},
                          "inference_ref": {"inference_id": "inference-original", "content_hash": "input-original", "input_index": 0}}
            NewsAIRepository(source).save(
                "000001", item, "gemini", "model", "hash",
                analysis, provenance=provenance,
            )
            expected = NewsAIRepository(source).load("000001", item)

            NewsAIBackupService(source).export_to(backup)
            restored = NewsAIBackupService(target).import_from(backup)

            self.assertEqual(2, restored)  # Stock analysis and shared company impacts.
            self.assertEqual(expected, NewsAIRepository(target).load("000001", item))
            self.assertEqual(provenance, NewsAIRepository(target).load("000001", item).provenance)
            self.assertEqual(provenance, NewsAIRepository(target).load_many("000002", (item,), "회사")[news_identity(item)].provenance)
            self.assertEqual(
                NewsAIRepository(source).list_theme_suggestions(),
                NewsAIRepository(target).list_theme_suggestions(),
            )
            self.assertEqual(
                analysis.company_impacts,
                NewsAIRepository(target).load_many("000002", (item,), "회사")[news_identity(item)].analysis.company_impacts,
            )
            self.assertEqual((), StockNewsRepository(target).load("000001"))

            # Older backups never contained candidates; importing one must not
            # erase candidates already restored from a newer backup.
            legacy = json.loads(backup.read_text(encoding="utf-8"))
            for value in legacy["analyses"]:
                value.pop("theme_candidates", None)
                value.pop("provenance_json", None)
            for value in legacy["shared_analyses"]:
                value.pop("provenance_json", None)
            for version in (1, 2, 3):
                with self.subTest(legacy_version=version):
                    legacy["version"] = version
                    backup.write_text(json.dumps(legacy), encoding="utf-8")
                    NewsAIBackupService(target).import_from(backup)
                    self.assertEqual(replace(expected, provenance={"status": "UNKNOWN"}),
                                     NewsAIRepository(target).load("000001", item))

    def test_accepted_metadata_migration_keeps_legacy_values_and_rolls_back_partial_alter(self) -> None:
        from kiwoom_monitor.infrastructure.persistence import news_schema as schema
        from kiwoom_monitor.infrastructure.persistence.schema_migrations import SQLiteMigration, SQLiteMigrationRunner
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old-v4.sqlite3"
            with closing(sqlite3.connect(path)) as connection:
                SQLiteMigrationRunner(connection, "news_schema_migrations").apply((
                    SQLiteMigration(1, schema.NEWS_SCHEMA_BASELINE_NAME, schema._apply_v1_baseline),
                    SQLiteMigration(2, schema.NEWS_ACCOUNT_SCOPE_NAME, schema._apply_v2_account_scope),
                    SQLiteMigration(3, schema.NEWS_LINK_TOMBSTONE_NAME, schema._apply_v3_link_tombstones),
                    SQLiteMigration(4, schema.NEWS_AI_THEME_CANDIDATES_NAME, schema._apply_v4_ai_theme_candidates),
                ))
                connection.execute("INSERT INTO stock_news_ai(stock_code,identity,provider,model,summary,outlook,confidence,reason,body_hash,analyzed_at) "
                                   "VALUES('005930','legacy','provider','model','original','중립',50,'original','original-hash','2026-10-20T10:00:00+09:00')")
                before = connection.execute("SELECT * FROM stock_news_ai").fetchone()
                connection.execute("DROP TABLE news_ai_shared")
                connection.commit()
            with self.assertRaises(sqlite3.OperationalError):
                schema.initialize_news_schema(path)
            with closing(sqlite3.connect(path)) as connection:
                self.assertNotIn("provenance_json", {row[1] for row in connection.execute("PRAGMA table_info(stock_news_ai)")})
                self.assertEqual(before, connection.execute("SELECT * FROM stock_news_ai").fetchone())
                self.assertEqual(4, connection.execute("SELECT MAX(version) FROM news_schema_migrations").fetchone()[0])
                schema._apply_v1_baseline(connection)
                schema._apply_v4_ai_theme_candidates(connection)
                connection.commit()
            schema.initialize_news_schema(path)
            schema.initialize_news_schema(path)
            with closing(sqlite3.connect(path)) as connection:
                after = connection.execute("SELECT * FROM stock_news_ai").fetchone()
                self.assertEqual(before, after[:-1])
                self.assertIsNone(after[-1])
                item = StockNewsItem("legacy", "", "legacy", "", None,
                                     assess_stock_news("fixture", "legacy", ""))
                self.assertEqual({"status": "UNKNOWN"}, NewsAIRepository(path).load("005930", item).provenance)
                self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM news_schema_migrations WHERE version=5").fetchone()[0])

    def test_input_source_migration_keeps_null_legacy_and_rolls_back_alter_with_ledger_failure(self):
        from kiwoom_monitor.infrastructure.persistence import news_schema as schema
        from kiwoom_monitor.infrastructure.persistence.schema_migrations import SQLiteMigration, SQLiteMigrationRunner
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source-v5.sqlite3"
            with closing(sqlite3.connect(path)) as connection:
                SQLiteMigrationRunner(connection, "news_schema_migrations").apply((
                    SQLiteMigration(1, schema.NEWS_SCHEMA_BASELINE_NAME, schema._apply_v1_baseline),
                    SQLiteMigration(2, schema.NEWS_ACCOUNT_SCOPE_NAME, schema._apply_v2_account_scope),
                    SQLiteMigration(3, schema.NEWS_LINK_TOMBSTONE_NAME, schema._apply_v3_link_tombstones),
                    SQLiteMigration(4, schema.NEWS_AI_THEME_CANDIDATES_NAME, schema._apply_v4_ai_theme_candidates),
                    SQLiteMigration(5, schema.NEWS_AI_PROVENANCE_NAME, schema._apply_v5_ai_provenance),
                ))
                connection.execute("INSERT INTO stock_news(stock_code,identity,title,published_at) VALUES('005930','legacy','original','2026-10-28T08:00:00+09:00')")
                before = connection.execute("SELECT * FROM stock_news").fetchone()
                connection.execute("CREATE TRIGGER reject_source_migration BEFORE INSERT ON news_schema_migrations WHEN NEW.version=6 BEGIN SELECT RAISE(ABORT,'source ledger rejected'); END")
                connection.commit()
            with self.assertRaisesRegex(sqlite3.IntegrityError, "source ledger rejected"):
                schema.initialize_news_schema(path)
            with closing(sqlite3.connect(path)) as connection:
                self.assertNotIn("input_source_json", {row[1] for row in connection.execute("PRAGMA table_info(stock_news)")})
                self.assertEqual(before, connection.execute("SELECT * FROM stock_news").fetchone())
                self.assertEqual(5, connection.execute("SELECT MAX(version) FROM news_schema_migrations").fetchone()[0])
                connection.execute("DROP TRIGGER reject_source_migration")
                connection.commit()
            schema.initialize_news_schema(path)
            schema.initialize_news_schema(path)
            with closing(sqlite3.connect(path)) as connection:
                after = connection.execute("SELECT * FROM stock_news").fetchone()
                self.assertEqual(before, after[:-1])
                self.assertIsNone(after[-1])
            self.assertIsNone(StockNewsRepository(path).load("005930")[0].input_source)


if __name__ == "__main__":
    unittest.main()
