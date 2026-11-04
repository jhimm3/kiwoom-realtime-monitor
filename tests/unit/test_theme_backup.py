from __future__ import annotations

import json
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


class _CloseTrackingConnection(sqlite3.Connection):
    close_count = 0

    def close(self) -> None:
        self.close_count += 1
        super().close()


class QueryRoleThemeBackupTests(unittest.TestCase):
    def fixture(self):
        from kiwoom_monitor.infrastructure.persistence.settings_repository import SettingsRepository
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        path = root / "query-role.sqlite3"
        Database(path).initialize_query_role(role="fixture-query")
        stamp = datetime.fromisoformat("2031-01-02T03:04:05+09:00")
        settings = SettingsRepository(path, strict_text=True, now_provider=lambda: stamp)
        return path, stamp, settings, ThemeBackupService(path, settings=settings, now_provider=lambda: stamp)

    def document(self, service):
        document = service.export_document()
        document["profiles"][0]["name"] = "복원된 프로필"
        document["active_profile"] = "복원된 프로필"
        return document

    def test_same_native_restore_versions_active_then_invalidates_actual_ui_cache_and_reopens_existing(self):
        path, stamp, settings, service = self.fixture()
        self.assertEqual("기본 테마", settings.get("theme_active_profile"))
        document = self.document(service)
        service.import_document(document)
        self.assertEqual("복원된 프로필", settings.get("theme_active_profile"))
        repository = ThemeRepository.open_existing(path, settings.get("theme_active_profile"), now_provider=lambda: stamp)
        self.assertIsNotNone(repository)
        with closing(sqlite3.connect(path)) as connection:
            self.assertEqual((stamp.isoformat(),), connection.execute("SELECT updated_at FROM central_setting_versions WHERE setting_key='theme_active_profile'").fetchone())
            self.assertEqual([("복원된 프로필",)], connection.execute("SELECT profile_name FROM theme_profiles").fetchall())
        reopened = Database(path)
        reopened.initialize_query_role(role="fixture-query")
        self.assertEqual("복원된 프로필", reopened.settings.get("theme_active_profile"))

    def test_invalid_active_and_write_failure_roll_back_profiles_active_and_native_version(self):
        path, stamp, settings, service = self.fixture()
        settings.get("theme_active_profile")
        original = service.export_document()
        document = self.document(service)
        for fail in ("active", "version"):
            with self.subTest(fail=fail):
                attempt = json.loads(json.dumps(document))
                if fail == "active":
                    attempt["active_profile"] = "선택에 없는 이름"
                    context = patch.object(settings, "write_many_in_transaction", wraps=settings.write_many_in_transaction)
                else:
                    context = patch.object(settings, "write_many_in_transaction", side_effect=sqlite3.OperationalError("controlled active write failure"))
                with context, self.assertRaises(ThemeBackupError) as failure:
                    service.import_document(attempt)
                self.assertIs(False, failure.exception.native_committed)
                self.assertEqual(original, service.export_document())
                self.assertEqual("기본 테마", settings.get("theme_active_profile"))
                with closing(sqlite3.connect(path)) as connection:
                    self.assertEqual([], connection.execute("SELECT * FROM central_setting_versions").fetchall())

    def test_post_commit_cache_publication_failure_reports_durable_state_without_claiming_rollback(self):
        path, stamp, settings, service = self.fixture()
        document = self.document(service)
        settings.get("theme_active_profile")
        with patch.object(settings, "clear_cache", side_effect=RuntimeError("controlled publication failure")):
            with self.assertRaises(ThemeBackupError) as failure:
                service.import_document(document)
        self.assertIs(True, failure.exception.native_committed)
        self.assertIs(False, failure.exception.cache_invalidated)
        self.assertIsInstance(failure.exception.cache_invalidation_error, RuntimeError)
        with closing(sqlite3.connect(path)) as connection:
            self.assertEqual(("복원된 프로필",), connection.execute("SELECT value FROM settings WHERE key='theme_active_profile'").fetchone())
            self.assertEqual((stamp.isoformat(),), connection.execute("SELECT updated_at FROM central_setting_versions WHERE setting_key='theme_active_profile'").fetchone())

    def test_real_native_commit_ack_loss_is_unknown_and_invalidates_same_port_without_retry(self):
        for cache_failure in (False, True):
            with self.subTest(cache_failure=cache_failure):
                path, stamp, settings, service = self.fixture()
                document = self.document(service)
                self.assertEqual("기본 테마", settings.get("theme_active_profile"))
                native = sqlite3.connect
                commit_exits = []
                class LostAck(sqlite3.Connection):
                    def __exit__(connection, kind, error, traceback):
                        result = super().__exit__(kind, error, traceback)
                        if kind is None:
                            commit_exits.append("real native COMMIT completed")
                            raise sqlite3.OperationalError("controlled native COMMIT ACK loss")
                        return result
                def connect(*args, **kwargs):
                    return native(*args, **kwargs, factory=LostAck)
                cache_error = RuntimeError("controlled cache invalidation failure")
                with patch("kiwoom_monitor.infrastructure.persistence.theme_backup.sqlite3.connect", side_effect=connect), patch.object(settings, "clear_cache", side_effect=cache_error if cache_failure else settings.clear_cache) as invalidation:
                    with self.assertRaises(ThemeBackupError) as failure:
                        service.import_document(document)
                error = failure.exception
                self.assertIsNone(error.native_committed)
                self.assertIs(not cache_failure, error.cache_invalidated)
                self.assertIs(cache_error if cache_failure else None, error.cache_invalidation_error)
                self.assertIsNone(error.close_error)
                self.assertIsInstance(error.__cause__, sqlite3.OperationalError)
                self.assertIn("COMMIT 결과", str(error))
                self.assertNotIn("되돌렸습니다", str(error))
                invalidation.assert_called_once_with()
                self.assertEqual(["real native COMMIT completed"], commit_exits)
                # Independent native read, never an emulated DB result.
                with closing(native(path)) as connection:
                    self.assertEqual([("복원된 프로필",)], connection.execute("SELECT profile_name FROM theme_profiles").fetchall())
                    self.assertEqual(("복원된 프로필",), connection.execute("SELECT value FROM settings WHERE key='theme_active_profile'").fetchone())
                    self.assertEqual((stamp.isoformat(),), connection.execute("SELECT updated_at FROM central_setting_versions WHERE setting_key='theme_active_profile'").fetchone())
                if not cache_failure:
                    self.assertEqual("복원된 프로필", settings.get("theme_active_profile"))

    def test_real_native_close_ack_failure_keeps_known_commit_and_invalidates_same_port(self):
        path, stamp, settings, service = self.fixture()
        document = self.document(service)
        settings.get("theme_active_profile")
        native = sqlite3.connect
        closes = []
        class CloseAckLost(sqlite3.Connection):
            def close(connection):
                super().close()
                closes.append("real close completed")
                raise sqlite3.OperationalError("controlled close ACK loss")
        with patch("kiwoom_monitor.infrastructure.persistence.theme_backup.sqlite3.connect", side_effect=lambda *a, **k: native(*a, **k, factory=CloseAckLost)):
            with self.assertRaises(ThemeBackupError) as failure:
                service.import_document(document)
        error = failure.exception
        self.assertIs(True, error.native_committed)
        self.assertIs(True, error.cache_invalidated)
        self.assertIs(error.__cause__, error.close_error)
        self.assertIsNone(error.cache_invalidation_error)
        self.assertEqual(["real close completed"], closes)
        self.assertEqual("복원된 프로필", settings.get("theme_active_profile"))
        with closing(native(path)) as connection:
            self.assertEqual((stamp.isoformat(),), connection.execute("SELECT updated_at FROM central_setting_versions WHERE setting_key='theme_active_profile'").fetchone())

    def test_export_checks_active_with_the_same_profile_list_and_native_nocase(self):
        path, stamp, settings, service = self.fixture()
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("UPDATE theme_profiles SET profile_name='User'")
            connection.execute("UPDATE settings SET value='uSeR' WHERE key='theme_active_profile'")
        statements = []
        with closing(sqlite3.connect(path)) as connection:
            connection.set_trace_callback(statements.append)
            connection.execute("BEGIN")
            document = service.export_document(connection)
            self.assertEqual((1,), connection.execute("SELECT 1").fetchone())
        self.assertEqual('uSeR', document['active_profile'])
        lists = [sql for sql in statements if 'FROM theme_profiles' in sql]
        self.assertEqual(1, len(lists))
        self.assertIn('profile_name=', lists[0])
        self.assertIn('COLLATE NOCASE', lists[0])

    def test_injected_port_path_and_actual_connection_target_must_match_and_snapshot_never_reads_repo_cache(self):
        path, stamp, settings, service = self.fixture()
        other = path.with_name("other.sqlite3")
        Database(other).initialize_query_role(role="fixture-query")
        with self.assertRaises(ThemeBackupError):
            ThemeBackupService(other, settings=settings)
        with closing(sqlite3.connect(other)) as connection:
            with self.assertRaises(ThemeBackupError):
                service.export_document(connection)
            with self.assertRaises(ThemeBackupError):
                service.apply_document_in_transaction(connection, self.document(service))
        with patch.object(settings, "get", side_effect=AssertionError("snapshot must not roundtrip through another connection")):
            self.assertEqual("기본 테마", service.export_document()["active_profile"])

    def test_missing_selected_theme_is_not_created_or_exported_and_current_restore_can_prepare_it(self):
        path, stamp, settings, service = self.fixture()
        document = self.document(service)
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("DELETE FROM theme_profiles")
        before = path.read_bytes()
        self.assertIsNone(ThemeRepository.open_existing(path, "기본 테마"))
        self.assertEqual(before, path.read_bytes())
        target = path.with_name("must-not-write.json")
        with self.assertRaises(ThemeBackupError):
            service.export_to(target)
        self.assertFalse(target.exists())
        self.assertEqual(before, path.read_bytes())
        service.import_document(document)
        self.assertIsNotNone(ThemeRepository.open_existing(path, "복원된 프로필"))

    def test_existing_only_owner_never_recreates_concurrently_deleted_profile_during_reads_or_merge(self):
        path, stamp, settings, service = self.fixture()
        repository = ThemeRepository.open_existing(path, "기본 테마")
        self.assertIsNotNone(repository)
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("DELETE FROM theme_profiles")
        before = path.read_bytes()
        for read in (repository.all_by_name, repository._merge_case_insensitive_themes,
                     lambda: repository.themes_for_stock("005930")):
            with self.subTest(read=read), self.assertRaisesRegex(ValueError, "미준비"):
                read()
        self.assertEqual(before, path.read_bytes())
        # The normal owner still implements explicit fresh/default creation.
        self.assertIsNotNone(ThemeRepository(path))


class ThemeBackupServiceTest(unittest.TestCase):
    def test_single_profile_public_round_trip_preserves_other_profiles_and_closes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            StockRepository(path).upsert("005930", "삼성전자", "KOSPI")
            repository = ThemeRepository(path)
            repository.replace_for_stock("005930", ("다른 프로필 테마",))
            original_profiles = ThemeBackupService(path).export_document()["profiles"]
            repository.create_profile("개별 백업")
            repository.select_profile("개별 백업")
            repository.replace_for_stock("005930", ("반도체",))
            repository.set_color("반도체", "#123456")
            repository.set_stock_theme_color("005930", "반도체", "#654321")
            repository.set_theme_alias("칩", "반도체")
            service = ThemeBackupService(path, profile_name="개별 백업")
            backup_path = Path(directory) / "profile.json"
            service.export_to(backup_path)
            document = json.loads(backup_path.read_text(encoding="utf-8"))
            self.assertEqual("개별 백업", document["profile"])
            self.assertNotIn("profiles", document)
            repository.replace_for_stock("005930", ("바뀐 테마",))

            connection = sqlite3.connect(path, factory=_CloseTrackingConnection)
            try:
                with patch(
                    "kiwoom_monitor.infrastructure.persistence.theme_backup.sqlite3.connect",
                    return_value=connection,
                ) as connect:
                    service.import_from(backup_path)
                connect.assert_called_once_with(path)
                self.assertEqual(1, connection.close_count)
                with self.assertRaises(sqlite3.ProgrammingError):
                    connection.execute("SELECT 1")
            finally:
                if not connection.close_count:
                    connection.close()

            self.assertEqual(("반도체",), repository.themes_for_stock("005930"))
            self.assertEqual("#123456", repository.color_for_theme("반도체"))
            self.assertEqual("#654321", repository.color_for_stock_theme("005930", "반도체"))
            self.assertEqual(("반도체",), repository.resolve_theme_names("칩"))
            profiles = ThemeBackupService(path).export_document()["profiles"]
            for profile in original_profiles:
                self.assertIn(profile, profiles)
            restored_path = Path(directory) / "restored-profile.json"
            service.export_to(restored_path)
            restored = json.loads(restored_path.read_text(encoding="utf-8"))
            document.pop("created_at")
            restored.pop("created_at")
            self.assertEqual(document, restored)

    def test_single_profile_native_failure_rolls_back_preserves_cause_and_closes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            StockRepository(path).upsert("005930", "삼성전자", "KOSPI")
            repository = ThemeRepository(path)
            repository.create_profile("개별 백업")
            repository.select_profile("개별 백업")
            repository.replace_for_stock("005930", ("실패 테마",))
            service = ThemeBackupService(path, profile_name="개별 백업")
            backup_path = Path(directory) / "profile.json"
            service.export_to(backup_path)
            repository.replace_for_stock("005930", ("기존 테마",))
            repository.set_theme_alias("기존 별칭", "기존 테마")
            revision = ThemeBackupService(path).revision()
            with closing(sqlite3.connect(path)) as setup, setup:
                setup.execute(
                    "CREATE TRIGGER fail_single_profile BEFORE INSERT ON profile_themes "
                    "WHEN NEW.theme_name='실패 테마' "
                    "BEGIN SELECT RAISE(ABORT,'native profile import fault'); END",
                )

            connection = sqlite3.connect(path, factory=_CloseTrackingConnection)
            try:
                with patch(
                    "kiwoom_monitor.infrastructure.persistence.theme_backup.sqlite3.connect",
                    return_value=connection,
                ) as connect:
                    with self.assertRaises(ThemeBackupError) as failure:
                        service.import_from(backup_path)
                connect.assert_called_once_with(path)
                self.assertIsInstance(failure.exception.__cause__, sqlite3.IntegrityError)
                self.assertEqual("native profile import fault", str(failure.exception.__cause__))
                self.assertEqual(1, connection.close_count)
                with self.assertRaises(sqlite3.ProgrammingError):
                    connection.execute("SELECT 1")
            finally:
                if not connection.close_count:
                    connection.close()

            self.assertEqual(revision, ThemeBackupService(path).revision())
            self.assertEqual(("기존 테마",), repository.themes_for_stock("005930"))
            self.assertEqual(("기존 테마",), repository.resolve_theme_names("기존 별칭"))

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
