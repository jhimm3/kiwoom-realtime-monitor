from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.infrastructure.persistence.theme_backup import (
    ThemeBackupError, ThemeBackupService, ThemeRevisionConflict,
)
from kiwoom_monitor.infrastructure.persistence.theme_repository import ThemeRepository
from kiwoom_monitor.infrastructure.persistence.stock_repository import StockRepository
from kiwoom_monitor.application.theme_suggestions import ThemeSuggestion


class ThemeBackupServiceTest(unittest.TestCase):
    def test_revision_keeps_exported_edits_and_rolls_back_failed_remote_import(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            StockRepository(path).upsert("005930", "삼성전자", "KOSPI")
            themes = ThemeRepository(path)
            themes.replace_for_stock("005930", ("반도체",))
            themes.set_theme_alias("칩", "반도체")
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("INSERT INTO stock_aliases VALUES('삼전','005930')")
                connection.execute(
                    "INSERT INTO profile_theme_suggestions(profile_id,stock_code,news_identity,"
                    "raw_theme_name,evidence,confidence,provider,model,body_hash,analyzed_at) "
                    "SELECT profile_id,'005930','article','칩','근거',80,'fixture','fixture',"
                    "'body','2030-01-02T09:00:00+09:00' FROM theme_profiles LIMIT 1",
                )
            backup = ThemeBackupService(path)
            changes = (
                "UPDATE stocks SET name='새 이름'",
                "UPDATE stocks SET market='KOSDAQ'",
                "INSERT INTO stocks(code,name,market) VALUES('000001','새 종목','KOSPI')",
                "UPDATE stock_aliases SET alias='새 별칭'",
                "UPDATE profile_themes SET default_color='#123456'",
                "UPDATE profile_stock_themes SET custom_color='#654321'",
                "UPDATE profile_theme_name_decisions SET decision_source='새 승인'",
                "UPDATE profile_theme_suggestions SET evidence='새 근거'",
                "UPDATE theme_profiles SET profile_name='새 프로필'",
                "UPDATE settings SET value='새 프로필' WHERE key='theme_active_profile'",
            )
            for statement in changes:
                with self.subTest(statement=statement):
                    remote = backup.export_document()
                    revision = backup.revision()
                    with closing(sqlite3.connect(path)) as writer, writer:
                        self.assertGreater(writer.execute(statement).rowcount, 0)
                    current = backup.export_document()
                    self.assertNotEqual(revision, backup.revision())
                    with self.assertRaises(ThemeRevisionConflict):
                        backup.import_document(remote, expected_revision=revision)
                    observed = backup.export_document()
                    current.pop("created_at")
                    observed.pop("created_at")
                    self.assertEqual(current, observed)

            remote = backup.export_document()
            remote["profiles"].append({"name": "원격", "themes": [{"name": "실패"}]})
            revision = backup.revision()
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute(
                    "CREATE TRIGGER fail_remote_theme BEFORE INSERT ON profile_themes "
                    "WHEN NEW.theme_name='실패' BEGIN SELECT RAISE(ABORT,'native import fault'); END",
                )
            with self.assertRaises(ThemeBackupError):
                backup.import_document(remote, expected_revision=revision)
            self.assertEqual(revision, backup.revision())
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("DROP TRIGGER fail_remote_theme")
            backup.import_document(remote, expected_revision=revision)
            self.assertIn("원격", [item["name"] for item in backup.export_document()["profiles"]])

    def test_export_replacement_failure_keeps_previous_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "monitor.sqlite3"
            Database(database_path).initialize()
            backup_path = Path(directory) / "themes.json"
            backup_path.write_text("previous backup", encoding="utf-8")
            with patch("os.replace", side_effect=OSError("replace failed")):
                with self.assertRaises(OSError):
                    ThemeBackupService(database_path).export_to(backup_path)
            self.assertEqual("previous backup", backup_path.read_text(encoding="utf-8"))
            self.assertEqual([], list(Path(directory).glob(".themes.json.*.tmp")))

    def test_invalid_utf8_backup_reports_read_error_without_replacing_themes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            store = ThemeRepository(path)
            store.create_profile("유지할 테마")
            backup_path = Path(directory) / "themes.json"
            backup_path.write_bytes(b"\xff")
            with self.assertRaises(ThemeBackupError):
                ThemeBackupService(path).import_from(backup_path)
            self.assertIn("유지할 테마", store.list_profiles())

    def test_remote_snapshot_cannot_replace_edit_committed_after_revision_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            backup = ThemeBackupService(path)
            remote = backup.export_document()
            revision = backup.revision()
            ThemeRepository(path).create_profile("새 편집")
            with self.assertRaises(ThemeRevisionConflict):
                backup.import_document(remote, expected_revision=revision)
            self.assertIn("새 편집", [profile["name"] for profile in backup.export_document()["profiles"]])

    def test_export_and_import_changes_only_theme_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "data" / "monitor.sqlite3"
            database_path.parent.mkdir()
            Database(database_path).initialize()
            connection = sqlite3.connect(database_path)
            try:
                connection.execute("UPDATE settings SET value = '3' WHERE key = 'decimal_strength'")
                connection.commit()
            finally:
                connection.close()
            StockRepository(database_path).upsert("005930", "삼성전자", "KOSPI")
            repository = ThemeRepository(database_path)
            repository.replace_for_stock("005930", ("반도체",))
            repository.set_color("반도체", "#123456")
            repository.set_stock_theme_color("005930", "반도체", "#654321")
            repository.create_profile("회사")
            repository.select_profile("회사")
            repository.replace_for_stock("005930", ("AI",))
            repository.set_theme_alias("인공지능", "AI", decision_source="llm_review")
            repository.import_ai_theme_suggestions((ThemeSuggestion(
                "005930", "https://example.com/theme", "인공지능", "기사 근거", 88,
                "openai", "model", "body-hash", datetime.now(UTC), "AI 산업 기사",
                datetime(2026, 9, 22, 9, 30, tzinfo=UTC), "https://example.com/original",
            ),))
            repository.review_ai_theme_suggestion(
                repository.list_ai_theme_suggestions()[0].key, approved=True,
            )
            connection = sqlite3.connect(database_path)
            try:
                connection.execute("UPDATE settings SET value='회사' WHERE key='theme_active_profile'")
                connection.commit()
            finally:
                connection.close()
            backup_path = Path(directory) / "themes.json"
            ThemeBackupService(database_path).export_to(backup_path)
            repository.select_profile("기본 테마")

            connection = sqlite3.connect(database_path)
            try:
                connection.execute("DELETE FROM profile_stock_themes")
                connection.execute("DELETE FROM profile_themes")
                connection.execute("UPDATE settings SET value = '1' WHERE key = 'decimal_strength'")
                connection.execute("UPDATE settings SET value = '기본 테마' WHERE key = 'theme_active_profile'")
                connection.commit()
            finally:
                connection.close()

            ThemeBackupService(database_path).import_from(backup_path)

            connection = sqlite3.connect(database_path)
            try:
                self.assertEqual(('1',), connection.execute("SELECT value FROM settings WHERE key = 'decimal_strength'").fetchone())
                self.assertEqual(2, connection.execute("SELECT COUNT(*) FROM theme_profiles").fetchone()[0])
                self.assertEqual(('회사',), connection.execute("SELECT value FROM settings WHERE key = 'theme_active_profile'").fetchone())
            finally:
                connection.close()
            repository.select_profile("기본 테마")
            self.assertEqual(("반도체",), repository.themes_for_stock("005930"))
            self.assertEqual("#123456", repository.color_for_theme("반도체"))
            self.assertEqual("#654321", repository.color_for_stock_theme("005930", "반도체"))
            repository.select_profile("회사")
            self.assertEqual(("AI",), repository.themes_for_stock("005930"))
            self.assertEqual(("AI",), repository.resolve_theme_names("인공지능"))
            self.assertIn(
                ("alias", "인공지능", "AI", "llm_review"),
                repository.theme_name_decisions(),
            )
            approved = repository.list_ai_theme_suggestions("approved")
            self.assertEqual(1, len(approved))
            self.assertEqual(("AI",), approved[0].resolved_theme_names)
            self.assertEqual("AI 산업 기사", approved[0].article_title)
            self.assertEqual("https://example.com/original", approved[0].article_url)
