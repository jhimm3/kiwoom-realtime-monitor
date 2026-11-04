from __future__ import annotations

import tempfile
import sqlite3
from contextlib import closing
import unittest
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.application.theme_matching import MatchedThemeRow
from kiwoom_monitor.application.theme_preview import preview_theme_changes
from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.infrastructure.persistence.stock_repository import StockRepository
from kiwoom_monitor.infrastructure.persistence.theme_repository import ThemeRepository


class ThemeColorRepositoryTests(unittest.TestCase):
    def test_durable_delete_keeps_fallback_id_colors_alias_clock_and_restart(self):
        from kiwoom_monitor.infrastructure.persistence.settings_repository import SettingsRepository
        from kiwoom_monitor.infrastructure.persistence.theme_backup import ThemeBackupService
        path, stamp, settings, store = self.rename_fixture()
        store.rename_theme("반도체", "반도체 장비")
        store.create_profile("z survivor", copy_current=True)
        store.create_profile("B fallback", copy_current=True)
        with closing(sqlite3.connect(path)) as connection:
            expected = connection.execute("SELECT profile_id,profile_name FROM theme_profiles WHERE profile_name<>'기본 테마' ORDER BY profile_name COLLATE NOCASE").fetchall()
        notified, statements = [], []
        store.set_change_callback(lambda: notified.append(ThemeBackupService(path, settings=settings).export_document()["active_profile"]))
        native = sqlite3.connect
        def connect(*args, **kwargs):
            connection = native(*args, **kwargs)
            connection.set_trace_callback(statements.append)
            return connection
        with patch("kiwoom_monitor.infrastructure.persistence.settings_repository.sqlite3.connect", side_effect=connect):
            self.assertEqual("B fallback", store.delete_profile("기본 테마", settings=settings))
        self.assertEqual(["B fallback"], notified)
        self.assertEqual(1, statements.count("BEGIN IMMEDIATE"))
        self.assertEqual(1, statements.count("COMMIT"))
        self.assertEqual("B fallback", store.active_profile)
        self.assertEqual("B fallback", settings.get("theme_active_profile"))
        self.assertEqual(("반도체 장비",), store.themes_for_stock("005930"))
        self.assertEqual("#123456", store.color_for_theme("반도체 장비"))
        self.assertEqual("#654321", store.color_for_stock_theme("005930", "반도체 장비"))
        self.assertIn(("alias", "반도체", "반도체 장비", "user"), store.theme_name_decisions())
        with closing(native(path)) as connection:
            self.assertEqual(expected, connection.execute("SELECT profile_id,profile_name FROM theme_profiles ORDER BY profile_name COLLATE NOCASE").fetchall())
            self.assertEqual((stamp[0].isoformat(),), connection.execute("SELECT updated_at FROM central_setting_versions WHERE setting_key='theme_active_profile'").fetchone())
            self.assertEqual([], connection.execute("PRAGMA foreign_key_check").fetchall())
        Database(path).initialize_query_role(role="rename-fixture")
        restarted = ThemeRepository.open_existing(path, SettingsRepository(path).get("theme_active_profile"))
        self.assertEqual("B fallback", restarted.active_profile)
        self.assertEqual("#654321", restarted.color_for_stock_theme("005930", "반도체 장비"))

    def test_durable_delete_preserves_later_native_nocase_selection_and_unchanged_version(self):
        path, stamp, settings, store = self.rename_fixture()
        store.create_profile("B")
        store.create_profile("z Current")
        store.select_current_profile("Z cUrReNt", settings=settings)
        with closing(sqlite3.connect(path)) as connection:
            version = connection.execute("SELECT updated_at FROM central_setting_versions WHERE setting_key='theme_active_profile'").fetchone()
        stamp[0] = stamp[0].replace(hour=12)
        self.assertEqual("z Current", store.delete_profile("b", settings=settings))
        self.assertNotIn("B", store.list_profiles())
        self.assertEqual("z Current", settings.get("theme_active_profile"))
        with closing(sqlite3.connect(path)) as connection:
            self.assertEqual(version, connection.execute("SELECT updated_at FROM central_setting_versions WHERE setting_key='theme_active_profile'").fetchone())
        # A confirmation whose captured target was already removed was a no-op.
        self.assertEqual("z Current", store.delete_profile("b", settings=settings))
        self.assertEqual(("z Current", "기본 테마"), store.list_profiles())

    def test_durable_delete_rolls_back_cascade_active_version_and_rejects_last_or_wrong_port(self):
        from kiwoom_monitor.infrastructure.persistence.settings_repository import SettingsNativeWriteError, SettingsRepository
        path, stamp, settings, store = self.rename_fixture()
        before = path.read_bytes()
        with self.assertRaises(SettingsNativeWriteError) as caught:
            store.delete_profile("기본 테마", settings=settings)
        self.assertIs(False, caught.exception.native_committed)
        self.assertIn("마지막 테마 프로필", str(caught.exception))
        self.assertEqual(before, path.read_bytes())
        store.create_profile("B")
        before = path.read_bytes()
        fault = sqlite3.OperationalError("controlled replacement active/version failure")
        with patch.object(settings, "write_many_in_transaction", side_effect=fault), patch.object(store, "notify_changed") as notify:
            with self.assertRaises(SettingsNativeWriteError) as caught:
                store.delete_profile("기본 테마", settings=settings)
        self.assertIs(False, caught.exception.native_committed)
        self.assertIs(fault, caught.exception.__cause__)
        notify.assert_not_called()
        self.assertEqual(before, path.read_bytes())
        self.assertEqual(("반도체",), store.themes_for_stock("005930"))
        other = path.with_name("other.sqlite3")
        Database(other).initialize_query_role(role="delete-other")
        other_before = other.read_bytes()
        with self.assertRaises(SettingsNativeWriteError) as caught:
            store.delete_profile("기본 테마", settings=SettingsRepository(other))
        self.assertIs(False, caught.exception.native_committed)
        self.assertEqual((before, other_before), (path.read_bytes(), other.read_bytes()))
        store.select_profile("B")  # a generic view cannot silently replace saved current
        before = path.read_bytes()
        with self.assertRaises(SettingsNativeWriteError) as caught:
            store.delete_profile("기본 테마", settings=settings)
        self.assertIs(False, caught.exception.native_committed)
        self.assertIn("현재 테마 선택을 확인", str(caught.exception))
        self.assertEqual(before, path.read_bytes())

    def test_durable_delete_ack_close_cache_ram_notify_failures_keep_atomic_native_result(self):
        from kiwoom_monitor.infrastructure.persistence.settings_repository import SettingsNativeWriteError
        for phase in ("ack", "close", "cache", "ram", "notify"):
            with self.subTest(phase=phase):
                path, stamp, settings, store = self.rename_fixture()
                store.create_profile("B", copy_current=True)
                fault = RuntimeError("controlled delete " + phase)
                if phase == "ram":
                    class PublishFault(ThemeRepository):
                        armed = False
                        def __setattr__(instance, name, value):
                            if instance.armed and name == "_profile_name": raise fault
                            super().__setattr__(name, value)
                    store = PublishFault.open_existing(path, store.active_profile, now_provider=lambda: stamp[0])
                    store.armed = True
                native = sqlite3.connect
                commits = []
                class Fault(sqlite3.Connection):
                    def __exit__(connection, kind, error, tb):
                        result = super().__exit__(kind, error, tb)
                        if kind is None:
                            commits.append("native committed")
                            if phase == "ack": raise fault
                        return result
                    def close(connection):
                        super().close()
                        if phase == "close": raise fault
                with patch("kiwoom_monitor.infrastructure.persistence.settings_repository.sqlite3.connect", side_effect=lambda *a, **k: native(*a, **k, factory=Fault)), patch.object(settings, "clear_cache", side_effect=fault if phase == "cache" else settings.clear_cache), patch.object(store, "notify_changed", side_effect=fault if phase == "notify" else store.notify_changed):
                    with self.assertRaises(SettingsNativeWriteError) as caught:
                        store.delete_profile("기본 테마", settings=settings)
                error = caught.exception
                self.assertIs(None if phase == "ack" else True, error.native_committed)
                self.assertIs(fault, error.__cause__)
                self.assertIs(phase != "cache", error.cache_invalidated)
                self.assertIs(fault if phase == "close" else None, error.close_error)
                self.assertEqual(["native committed"], commits)
                with closing(native(path)) as connection:
                    self.assertEqual([("B",)], connection.execute("SELECT profile_name FROM theme_profiles").fetchall())
                    self.assertEqual(("B",), connection.execute("SELECT value FROM settings WHERE key='theme_active_profile'").fetchone())
                    self.assertEqual((stamp[0].isoformat(),), connection.execute("SELECT updated_at FROM central_setting_versions WHERE setting_key='theme_active_profile'").fetchone())

    def test_durable_select_preserves_native_id_colors_clock_noop_restart_and_generic_view(self):
        path, stamp, settings, store = self.rename_fixture()
        store.create_profile("B")
        store.select_profile("B")
        store.replace_for_stock("005930", ("B theme",))
        store.set_stock_theme_color("005930", "B theme", "#112233")
        store.select_profile("기본 테마")
        with closing(sqlite3.connect(path)) as connection:
            ids = connection.execute("SELECT profile_id,profile_name FROM theme_profiles ORDER BY profile_id").fetchall()
        notified = []
        store.set_change_callback(lambda: notified.append(settings.get("theme_active_profile")))
        statements = []
        native = sqlite3.connect
        def connect(*a, **k):
            connection = native(*a, **k)
            connection.set_trace_callback(statements.append)
            return connection
        with patch("kiwoom_monitor.infrastructure.persistence.settings_repository.sqlite3.connect", side_effect=connect):
            self.assertEqual("B", store.select_current_profile("b", settings=settings))
        self.assertEqual(1, sum(sql == "COMMIT" for sql in statements))
        self.assertEqual(["B"], notified)
        self.assertEqual("B", settings.get("theme_active_profile"))
        self.assertEqual("#112233", store.color_for_stock_theme("005930", "B theme"))
        with closing(native(path)) as connection:
            self.assertEqual(ids, connection.execute("SELECT profile_id,profile_name FROM theme_profiles ORDER BY profile_id").fetchall())
            version = connection.execute("SELECT updated_at FROM central_setting_versions WHERE setting_key='theme_active_profile'").fetchone()[0]
        self.assertEqual(stamp[0].isoformat(), version)
        stamp[0] = stamp[0].replace(hour=12)
        store.select_current_profile("B", settings=settings)
        with closing(native(path)) as connection:
            self.assertEqual((version,), connection.execute("SELECT updated_at FROM central_setting_versions WHERE setting_key='theme_active_profile'").fetchone())
        store.select_profile("기본 테마")
        self.assertEqual("B", settings.get("theme_active_profile"))
        ThemeRepository(path, store.active_profile, now_provider=lambda: stamp[0]).select_profile("generic missing")
        self.assertIn("generic missing", store.list_profiles())
        self.assertEqual("B", settings.get("theme_active_profile"))
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            store.select_current_profile("durable missing", settings=settings)
        self.assertEqual(before, path.read_bytes())
        from kiwoom_monitor.infrastructure.persistence.settings_repository import SettingsRepository
        restarted = ThemeRepository.open_existing(path, SettingsRepository(path).get("theme_active_profile"))
        self.assertEqual("B", restarted.active_profile)
        self.assertEqual("#112233", restarted.color_for_stock_theme("005930", "B theme"))

    def test_durable_select_native_normalization_rolls_back_with_active_and_preserves_old_sql_color_winner(self):
        from kiwoom_monitor.infrastructure.persistence.settings_repository import SettingsNativeWriteError
        path, stamp, settings, store = self.rename_fixture()
        store.create_profile("B")
        with closing(sqlite3.connect(path)) as connection, connection:
            target = connection.execute("SELECT profile_id FROM theme_profiles WHERE profile_name='B'").fetchone()[0]
            connection.executemany("INSERT INTO profile_themes(profile_id,theme_name,default_color) VALUES(?,?,?)", ((target,"SS","#111111"),(target,"ß","#222222")))
            connection.executemany("INSERT INTO profile_stock_themes(profile_id,stock_code,theme_name,custom_color) VALUES(?,?,?,?)", ((target,"005930","SS","#333333"),(target,"005930","ß","#444444")))
        before = path.read_bytes()
        fault = sqlite3.OperationalError("controlled selection version failure")
        with patch.object(settings,"write_many_in_transaction",side_effect=fault):
            with self.assertRaises(SettingsNativeWriteError) as caught:
                store.select_current_profile("B", settings=settings)
        self.assertIs(False, caught.exception.native_committed)
        self.assertIs(fault, caught.exception.__cause__)
        self.assertEqual(before, path.read_bytes())
        store.select_current_profile("B", settings=settings)
        with closing(sqlite3.connect(path)) as connection:
            self.assertEqual([("SS","#111111")],connection.execute("SELECT theme_name,default_color FROM profile_themes WHERE profile_id=?",(target,)).fetchall())
            self.assertEqual([("SS","#333333")],connection.execute("SELECT theme_name,custom_color FROM profile_stock_themes WHERE profile_id=?",(target,)).fetchall())

    def test_durable_select_real_native_ack_close_cache_and_notify_failures_keep_phase_and_cause(self):
        from kiwoom_monitor.infrastructure.persistence.settings_repository import SettingsNativeWriteError
        for phase in ("ack","close","cache","ram","notify"):
            with self.subTest(phase=phase):
                path, stamp, settings, store = self.rename_fixture()
                store.create_profile("B")
                native = sqlite3.connect
                fault = RuntimeError("controlled " + phase)
                if phase == "ram":
                    class PublishFault(ThemeRepository):
                        armed = False
                        def __setattr__(instance,name,value):
                            if instance.armed and name == "_profile_name": raise fault
                            super().__setattr__(name,value)
                    store = PublishFault.open_existing(path,store.active_profile,now_provider=lambda:stamp[0])
                    store.armed = True
                commits = []
                class Fault(sqlite3.Connection):
                    def __exit__(connection,kind,error,tb):
                        result = super().__exit__(kind,error,tb)
                        if kind is None:
                            commits.append("native committed")
                            if phase == "ack": raise fault
                        return result
                    def close(connection):
                        super().close()
                        if phase == "close": raise fault
                with patch("kiwoom_monitor.infrastructure.persistence.settings_repository.sqlite3.connect",side_effect=lambda *a,**k:native(*a,**k,factory=Fault)), patch.object(settings,"clear_cache",side_effect=fault if phase=="cache" else settings.clear_cache), patch.object(store,"notify_changed",side_effect=fault if phase=="notify" else store.notify_changed):
                    with self.assertRaises(SettingsNativeWriteError) as caught:
                        store.select_current_profile("B",settings=settings)
                error = caught.exception
                self.assertIs(None if phase=="ack" else True,error.native_committed)
                self.assertIs(fault,error.__cause__)
                self.assertIs(phase!="cache",error.cache_invalidated)
                self.assertIs(fault if phase=="close" else None,error.close_error)
                self.assertEqual(["native committed"],commits)
                with closing(native(path)) as connection:
                    self.assertEqual(("B",),connection.execute("SELECT value FROM settings WHERE key='theme_active_profile'").fetchone())

    def rename_fixture(self):
        from datetime import datetime
        from kiwoom_monitor.infrastructure.persistence.settings_repository import SettingsRepository
        path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "query-role.sqlite3"
        Database(path).initialize_query_role(role="rename-fixture")
        stamp = [datetime.fromisoformat("2031-01-02T03:04:05+09:00")]
        settings = SettingsRepository(path, strict_text=True, now_provider=lambda: stamp[0])
        store = ThemeRepository.open_existing(path, settings.get("theme_active_profile"), now_provider=lambda: stamp[0])
        StockRepository(path).upsert("005930", "삼성전자")
        store.replace_for_stock("005930", ("반도체",))
        store.set_color("반도체", "#123456")
        store.set_stock_theme_color("005930", "반도체", "#654321")
        return path, stamp, settings, store

    def test_current_rename_has_one_native_commit_preserves_id_colors_clock_and_restart(self):
        from kiwoom_monitor.infrastructure.persistence.theme_backup import ThemeBackupService
        path, stamp, settings, store = self.rename_fixture()
        with closing(sqlite3.connect(path)) as connection:
            profile_id = connection.execute("SELECT profile_id FROM theme_profiles").fetchone()[0]
        settings.get("theme_active_profile")
        # Follow the current injected runtime clock, not the constructor value.
        stamp[0] = stamp[0].replace(hour=12)
        statements, notified = [], []
        store.set_change_callback(lambda: notified.append(ThemeBackupService(path, settings=settings).export_document()["active_profile"]))
        native = sqlite3.connect
        def connect(*args, **kwargs):
            connection = native(*args, **kwargs)
            connection.set_trace_callback(statements.append)
            return connection
        with patch("kiwoom_monitor.infrastructure.persistence.settings_repository.sqlite3.connect", side_effect=connect):
            self.assertEqual("User", store.rename_profile("기본 테마", " User ", settings=settings))
        self.assertEqual(["User"], notified)
        # Callback export owns one read snapshot, not another writing COMMIT.
        self.assertEqual(1, sum(sql == "COMMIT" for sql in statements))
        self.assertEqual(1, sum(sql == "BEGIN IMMEDIATE" for sql in statements))
        self.assertEqual("User", settings.get("theme_active_profile"))
        self.assertEqual("User", store.active_profile)
        self.assertEqual(("반도체",), store.themes_for_stock("005930"))
        self.assertEqual("#123456", store.color_for_theme("반도체"))
        self.assertEqual("#654321", store.color_for_stock_theme("005930", "반도체"))
        with closing(native(path)) as connection:
            self.assertEqual((profile_id, "User"), connection.execute("SELECT profile_id,profile_name FROM theme_profiles").fetchone())
            self.assertEqual((stamp[0].isoformat(),), connection.execute("SELECT updated_at FROM central_setting_versions WHERE setting_key='theme_active_profile'").fetchone())
        reopened = Database(path)
        reopened.initialize_query_role(role="rename-fixture")
        restarted = ThemeRepository.open_existing(path, reopened.settings.get("theme_active_profile"))
        self.assertEqual("User", restarted.active_profile)
        self.assertEqual("#654321", restarted.color_for_stock_theme("005930", "반도체"))

    def test_current_rename_rolls_back_name_active_version_before_commit(self):
        from kiwoom_monitor.infrastructure.persistence.settings_repository import SettingsNativeWriteError
        path, stamp, settings, store = self.rename_fixture()
        failure = sqlite3.OperationalError("controlled active/version failure")
        before = path.read_bytes()
        with patch.object(settings, "write_many_in_transaction", side_effect=failure), patch.object(store, "notify_changed") as notify:
            with self.assertRaises(SettingsNativeWriteError) as caught:
                store.rename_profile("기본 테마", "User", settings=settings)
        self.assertIs(False, caught.exception.native_committed)
        self.assertIs(failure, caught.exception.__cause__)
        notify.assert_not_called()
        self.assertEqual(before, path.read_bytes())
        self.assertEqual("기본 테마", settings.get("theme_active_profile"))
        self.assertEqual("기본 테마", store.active_profile)

    def test_current_rename_real_commit_ack_close_and_cache_failure_keep_phase_cause_and_data(self):
        from kiwoom_monitor.infrastructure.persistence.settings_repository import SettingsNativeWriteError
        for phase in ("commit-ack", "close-ack"):
            for cache_failure in (False, True):
                with self.subTest(phase=phase, cache_failure=cache_failure):
                    path, stamp, settings, store = self.rename_fixture()
                    native = sqlite3.connect
                    fault = sqlite3.OperationalError("controlled " + phase)
                    cache_error = RuntimeError("controlled invalidation failure")
                    commits = []
                    class Fault(sqlite3.Connection):
                        def __exit__(connection, kind, error, traceback):
                            result = super().__exit__(kind, error, traceback)
                            if kind is None:
                                commits.append("native committed")
                                if phase == "commit-ack":
                                    raise fault
                            return result
                        def close(connection):
                            super().close()
                            if phase == "close-ack":
                                raise fault
                    with patch("kiwoom_monitor.infrastructure.persistence.settings_repository.sqlite3.connect", side_effect=lambda *a, **k: native(*a, **k, factory=Fault)), patch.object(settings, "clear_cache", side_effect=cache_error if cache_failure else settings.clear_cache), patch.object(store, "notify_changed") as notify:
                        with self.assertRaises(SettingsNativeWriteError) as caught:
                            store.rename_profile("기본 테마", "User", settings=settings)
                    error = caught.exception
                    self.assertIs(None if phase == "commit-ack" else True, error.native_committed)
                    self.assertIs(fault, error.__cause__)
                    self.assertIs(fault if phase == "close-ack" else None, error.close_error)
                    self.assertIs(not cache_failure, error.cache_invalidated)
                    self.assertIs(cache_error if cache_failure else None, error.cache_invalidation_error)
                    self.assertEqual(["native committed"], commits)
                    self.assertNotIn("되돌렸습니다", str(error))
                    notify.assert_not_called()
                    self.assertEqual("기본 테마", store.active_profile)
                    with closing(native(path)) as connection:
                        self.assertEqual([("User",)], connection.execute("SELECT profile_name FROM theme_profiles").fetchall())
                        self.assertEqual(("User",), connection.execute("SELECT value FROM settings WHERE key='theme_active_profile'").fetchone())
                        self.assertEqual((stamp[0].isoformat(),), connection.execute("SELECT updated_at FROM central_setting_versions WHERE setting_key='theme_active_profile'").fetchone())
                    if not cache_failure:
                        self.assertEqual("User", settings.get("theme_active_profile"))
                    reopened = Database(path)
                    reopened.initialize_query_role(role="rename-fixture")
                    self.assertEqual("User", reopened.settings.get("theme_active_profile"))
                    restarted = ThemeRepository.open_existing(path, reopened.settings.get("theme_active_profile"))
                    self.assertEqual(("User",), restarted.list_profiles())
                    self.assertEqual("#654321", restarted.color_for_stock_theme("005930", "반도체"))

    def test_current_rename_ram_and_notify_failure_are_known_committed_publication_failures(self):
        from kiwoom_monitor.infrastructure.persistence.settings_repository import SettingsNativeWriteError
        for phase in ("RAM", "notify"):
            with self.subTest(phase=phase):
                path, stamp, settings, original = self.rename_fixture()
                fault = RuntimeError("controlled " + phase)
                class Store(ThemeRepository):
                    armed = False
                    def __setattr__(instance, name, value):
                        if instance.armed and name == "_profile_name":
                            raise fault
                        super().__setattr__(name, value)
                store = Store.open_existing(path, "기본 테마")
                if phase == "RAM":
                    store.armed = True
                else:
                    store.set_change_callback(lambda: (_ for _ in ()).throw(fault))
                with self.assertRaises(SettingsNativeWriteError) as caught:
                    store.rename_profile("기본 테마", "User", settings=settings)
                self.assertIs(True, caught.exception.native_committed)
                self.assertIs(True, caught.exception.cache_invalidated)
                self.assertIs(fault, caught.exception.__cause__)
                self.assertEqual("User", settings.get("theme_active_profile"))
                with closing(sqlite3.connect(path)) as connection:
                    self.assertEqual([("User",)], connection.execute("SELECT profile_name FROM theme_profiles").fetchall())

    def test_current_rename_rejects_changed_view_or_saved_active_and_uses_native_nocase_identity(self):
        from kiwoom_monitor.infrastructure.persistence.settings_repository import SettingsNativeWriteError
        for changed in ("view", "saved", "both"):
            with self.subTest(changed=changed):
                path, stamp, settings, store = self.rename_fixture()
                store.create_profile("Other")
                if changed in ("view", "both"):
                    store.select_profile("Other")
                if changed in ("saved", "both"):
                    settings.set("theme_active_profile", "Other")
                before = path.read_bytes()
                with self.assertRaises(SettingsNativeWriteError) as caught:
                    store.rename_profile("기본 테마", "User", settings=settings)
                self.assertIs(False, caught.exception.native_committed)
                self.assertEqual(before, path.read_bytes())
                self.assertEqual("Other" if changed in ("view", "both") else "기본 테마", store.active_profile)
                self.assertEqual("Other" if changed in ("saved", "both") else "기본 테마", settings.get("theme_active_profile"))
        path, stamp, settings, store = self.rename_fixture()
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("UPDATE theme_profiles SET profile_name='User'")
            connection.execute("UPDATE settings SET value='uSeR' WHERE key='theme_active_profile'")
        settings.clear_cache()
        store = ThemeRepository.open_existing(path, "USER")
        self.assertEqual("New", store.rename_profile("user", "New", settings=settings))

    def test_current_rename_rejects_wrong_port_and_misdirected_native_connection_before_writing(self):
        from kiwoom_monitor.infrastructure.persistence.settings_repository import SettingsNativeWriteError, SettingsRepository
        path, stamp, settings, store = self.rename_fixture()
        other = path.with_name("other.sqlite3")
        Database(other).initialize_query_role(role="other")
        before = path.read_bytes(), other.read_bytes()
        with self.assertRaises(SettingsNativeWriteError):
            store.rename_profile("기본 테마", "User", settings=SettingsRepository(other))
        native = sqlite3.connect
        with patch("kiwoom_monitor.infrastructure.persistence.settings_repository.sqlite3.connect", side_effect=lambda *a, **k: native(other)):
            with self.assertRaises(SettingsNativeWriteError) as caught:
                store.rename_profile("기본 테마", "User", settings=settings)
        self.assertIs(False, caught.exception.native_committed)
        self.assertEqual(before, (path.read_bytes(), other.read_bytes()))

    def test_current_rename_preserves_blank_duplicate_and_missing_name_rejection(self):
        path, stamp, settings, store = self.rename_fixture()
        store.create_profile("Other")
        before = path.read_bytes()
        for source, target in (("기본 테마", " "), ("기본 테마", "oThEr"), ("missing", "User")):
            with self.subTest(source=source, target=target), self.assertRaises(ValueError):
                store.rename_profile(source, target, settings=settings)
            self.assertEqual(before, path.read_bytes())
        self.assertEqual("기본 테마", store.active_profile)

    def test_shared_native_helper_preserves_exact_backup_revision_conflict_and_cause(self):
        from kiwoom_monitor.infrastructure.persistence.theme_backup import ThemeBackupService, ThemeRevisionConflict
        path, stamp, settings, store = self.rename_fixture()
        service = ThemeBackupService(path, settings=settings)
        document = service.export_document()
        failure = ThemeRevisionConflict("controlled exact rollback conflict")
        before = path.read_bytes()
        with patch.object(service, "_apply_all_profiles", side_effect=failure):
            with self.assertRaises(ThemeRevisionConflict) as caught:
                service.import_document(document)
        self.assertIs(failure, caught.exception)
        self.assertIs(False, caught.exception.native_committed)
        self.assertEqual(before, path.read_bytes())

    def test_shared_catalog_compatibility_keeps_profile_independence_approval_and_fk_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            stocks = StockRepository(path)
            stocks.upsert("A", "예전회사")
            stocks.upsert("A", "신규회사")
            stocks.upsert("B", "현재회사")
            repository = ThemeRepository(path)
            self.assertEqual({"예전회사": None, "신규회사": "A"}, repository.find_codes_by_names(("예전회사", "신규회사")))
            self.assertEqual(("A", "예전회사", "신규회사", "KRX"), repository.pending_name_change(" 예전회사 "))
            self.assertEqual((("A", "신규회사"),), repository.find_stock_candidates("신규"))
            self.assertEqual((("A", "신규회사"), ("B", "현재회사")), repository.find_concatenated_stocks("신규회사현재회사"))
            self.assertEqual(((("A", "신규회사"),), ("오타",)), repository.find_partial_concatenated_stocks("신규회사오타"))
            repository.review_name_changes({("A", "예전회사"): True})
            self.assertEqual("A", repository.find_code_by_name("예전회사"))
            self.assertEqual({"예전회사": "A"}, repository.find_codes_by_names(("예전회사",)))
            self.assertIsNone(repository.pending_name_change("예전회사"))
            repository.create_profile("다른 프로필", copy_current=False)
            repository.select_profile("다른 프로필")
            self.assertEqual("A", repository.find_code_by_name("예전회사"))

            with closing(sqlite3.connect(path)) as seed:
                seed.executemany(
                    "INSERT INTO stock_name_history(stock_code,old_name,new_name,source,decision) VALUES(?,?,?,'KRX','pending')",
                    (("A", "현재회사", "신규회사"), ("A", "원자승인", "신규회사"), ("missing", "고아승인", "없는회사")),
                )
                seed.commit()
            repository.review_name_changes({("A", "현재회사"): True})
            self.assertEqual("B", repository.find_code_by_name("현재회사"))
            with closing(sqlite3.connect(path)) as check:
                self.assertIsNone(check.execute("SELECT stock_code FROM stock_aliases WHERE alias='현재회사'").fetchone())
            with self.assertRaises(sqlite3.IntegrityError):
                repository.review_name_changes({("A", "원자승인"): True, ("missing", "고아승인"): True})
            self.assertEqual(("A", "원자승인", "신규회사", "KRX"), repository.pending_name_change("원자승인"))
            self.assertIsNone(repository.find_code_by_name("원자승인"))

    def test_preview_reads_only_requested_memberships_in_one_profile_and_chunks_native_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            stocks = StockRepository(path)
            for code in ("A", "B", "C", "not-requested"):
                stocks.upsert(code, code)
            repository = ThemeRepository(path)
            repository.replace_many((("A", ("반도체", "AI")), ("B", ("로봇",)), ("not-requested", ("비공개",))))
            rows = (
                MatchedThemeRow("A", "첫 행", ("반도체", "ai")),
                MatchedThemeRow("missing", "미등록", ("신규",)),
                MatchedThemeRow("A", "반복", ()),
                MatchedThemeRow("B", "둘째", ("로봇",)),
                MatchedThemeRow("C", "빈 테마", ()),
            )
            expected = tuple(repository.themes_for_stock(row.code) for row in rows)
            connect = repository._connect
            statements = []

            def limited_connection():
                connection = connect()
                connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 3)
                connection.set_trace_callback(statements.append)
                return connection

            with patch.object(repository, "_connect", side_effect=limited_connection) as opened:
                changes = preview_theme_changes(rows, repository)
            self.assertEqual(1, opened.call_count)
            self.assertEqual(expected, tuple(change.before for change in changes))
            self.assertEqual(("변경 없음", "신규", "테마 변경", "변경 없음", "변경 없음"), tuple(change.status for change in changes))
            reads = [sql for sql in statements if sql.startswith("SELECT stock_code, theme_name")]
            self.assertEqual(2, len(reads))  # Four unique codes, two plus profile per statement.
            self.assertTrue(all("not-requested" not in sql for sql in reads))
            self.assertEqual(3, sum(sql.startswith("SELECT") for sql in statements))
            self.assertFalse(any(sql.startswith(("INSERT", "UPDATE", "DELETE", "BEGIN", "COMMIT")) for sql in statements))
            with patch.object(repository, "_connect", side_effect=AssertionError("empty read")):
                self.assertEqual({}, repository.themes_for_stocks(()))

            repository.create_profile("회사", copy_current=False)
            repository.select_profile("회사")
            repository.replace_for_stock("A", ("회사 테마",))
            self.assertEqual(("회사 테마",), preview_theme_changes(rows, repository)[0].before)
            repository.select_profile("기본 테마")
            self.assertEqual(("AI", "반도체"), preview_theme_changes(rows, repository)[0].before)

    def test_preview_read_error_closes_connection_and_preserves_atomic_approved_save_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            stocks = StockRepository(path)
            for code in ("A", "B"):
                stocks.upsert(code, code)
            repository = ThemeRepository(path)
            repository.replace_many((("A", ("원본 A",)), ("B", ("원본 B",))))
            rows = (MatchedThemeRow("A", "A", ("새 A",)), MatchedThemeRow("B", "B", ("새 B",)))
            changes = preview_theme_changes(rows, repository)
            notifications = []
            repository.set_change_callback(lambda: notifications.append(True))
            connection = repository._connect()
            connection.set_authorizer(lambda action, *_: sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ else sqlite3.SQLITE_OK)
            with patch.object(repository, "_connect", return_value=connection):
                with self.assertRaises(sqlite3.DatabaseError):
                    preview_theme_changes(rows, repository)
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")
            self.assertEqual(("원본 A",), repository.themes_for_stock("A"))

            with closing(sqlite3.connect(path)) as trigger:
                trigger.execute("CREATE TRIGGER fail_second BEFORE INSERT ON profile_stock_themes WHEN NEW.stock_code='B' BEGIN SELECT RAISE(ABORT, 'save failed'); END")
            approved = tuple((change.code, change.after) for change in changes)
            with self.assertRaisesRegex(sqlite3.IntegrityError, "save failed"):
                repository.replace_many(approved)
            self.assertEqual([], notifications)
            self.assertEqual((("원본 A",), ("원본 B",)), tuple(repository.themes_for_stock(code) for code in ("A", "B")))
            with closing(sqlite3.connect(path)) as trigger:
                trigger.execute("DROP TRIGGER fail_second")
            repository.replace_many(approved)
            self.assertEqual((("새 A",), ("새 B",)), tuple(repository.themes_for_stock(code) for code in ("A", "B")))
            self.assertEqual([True], notifications)

    def test_reviews_pending_former_name_and_then_matches_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            stocks = StockRepository(path)
            stocks.upsert("306620", "지아이에스", "코스닥")
            repository = ThemeRepository(path)

            self.assertEqual(
                ("306620", "네온테크", "지아이에스", "기본 과거명 자료"),
                repository.pending_name_change("네온테크"),
            )
            self.assertIsNone(repository.find_code_by_name("네온테크"))
            repository.review_name_changes({("306620", "네온테크"): True})

            self.assertEqual("306620", repository.find_code_by_name("네온테크"))
            self.assertIsNone(repository.pending_name_change("네온테크"))

    def test_keeps_stock_color_override_separate_from_theme_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            StockRepository(path).upsert("005930", "삼성전자")
            repository = ThemeRepository(path)
            repository.replace_for_stock("005930", ("반도체",))
            repository.set_color("반도체", "#CFE2F3")
            repository.set_stock_theme_color("005930", "반도체", "#F4CCCC")

            self.assertEqual("#CFE2F3", repository.color_for_theme("반도체"))
            self.assertEqual("#F4CCCC", repository.color_for_stock_theme("005930", "반도체"))

    def test_badge_colors_share_one_read_and_refresh_after_a_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            StockRepository(path).upsert("005930", "삼성전자")
            repository = ThemeRepository(path)
            repository.replace_for_stock("005930", ("반도체", "AI"))
            with patch.object(repository, "_connect", wraps=repository._connect) as connect:
                repository.color_for_stock_theme("005930", "반도체")
                repository.color_for_stock_theme("005930", "AI")
                self.assertEqual(1, connect.call_count)
            repository.set_stock_theme_color("005930", "반도체", "#F4CCCC")
            self.assertEqual("#F4CCCC", repository.color_for_stock_theme("005930", "반도체"))

    def test_keeps_themes_separate_by_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            StockRepository(path).upsert("005930", "삼성전자")
            repository = ThemeRepository(path)
            repository.replace_for_stock("005930", ("집 테마",))
            repository.create_profile("회사", copy_current=False)
            repository.select_profile("회사")
            repository.replace_for_stock("005930", ("회사 테마",))

            self.assertEqual(("회사 테마",), repository.themes_for_stock("005930"))
            repository.select_profile("기본 테마")
            self.assertEqual(("집 테마",), repository.themes_for_stock("005930"))

    def test_renames_active_profile_without_changing_its_themes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            database = Database(path)
            database.initialize()
            StockRepository(path).upsert("005930", "삼성전자")
            repository = ThemeRepository(path)
            repository.replace_for_stock("005930", ("반도체",))

            repository.rename_profile("기본 테마", "집", settings=database.settings)

            self.assertEqual("집", repository.active_profile)
            self.assertEqual(("집",), repository.list_profiles())
            self.assertEqual(("반도체",), repository.themes_for_stock("005930"))

            self.assertEqual("집", database.settings.get("theme_active_profile"))
            with closing(sqlite3.connect(path)) as connection:
                self.assertIsNotNone(connection.execute("SELECT updated_at FROM central_setting_versions WHERE setting_key='theme_active_profile'").fetchone())

    def test_renames_theme_without_breaking_stock_foreign_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            StockRepository(path).upsert("005930", "삼성전자")
            repository = ThemeRepository(path)
            repository.replace_for_stock("005930", ("동신장비",))

            repository.rename_theme("동신장비", "통신장비")

            self.assertEqual(("통신장비",), repository.themes_for_stock("005930"))
            self.assertIn(("통신장비", repository.color_for_theme("통신장비")), repository.list_themes())

    def test_rename_remembers_alias_for_future_profile_imports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            stocks = StockRepository(path)
            stocks.upsert("005930", "삼성전자")
            stocks.upsert("000660", "SK하이닉스")
            repository = ThemeRepository(path)
            repository.replace_for_stock("005930", ("호남개발",))

            repository.rename_theme("호남개발", "호남클러스터")
            repository.replace_for_stock("000660", ("호남개발",))

            self.assertEqual(("호남클러스터",), repository.themes_for_stock("000660"))
            self.assertIn(
                ("alias", "호남개발", "호남클러스터", "user"),
                repository.theme_name_decisions(),
            )

    def test_does_not_recreate_deleted_default_profile_on_next_start(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            repository = ThemeRepository(path)
            repository.create_profile("회사")
            repository.delete_profile("기본 테마", settings=Database(path).settings)

            Database(path).initialize()

            self.assertEqual(("회사",), repository.list_profiles())

    def test_splits_one_theme_into_multiple_themes_for_every_stock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            stocks = StockRepository(path)
            stocks.upsert("005930", "삼성전자")
            stocks.upsert("000660", "SK하이닉스")
            repository = ThemeRepository(path)
            repository.replace_for_stock("005930", ("해운이란", "이란"))
            repository.replace_for_stock("000660", ("해운이란",))
            repository.set_color("해운이란", "#123456")

            repository.split_theme("해운이란", ("해운", "이란", "해운"))

            self.assertEqual(("이란", "해운"), repository.themes_for_stock("005930"))
            self.assertEqual(("이란", "해운"), repository.themes_for_stock("000660"))
            self.assertNotIn("해운이란", dict(repository.list_themes()))
            self.assertEqual("#123456", repository.color_for_theme("해운"))

    def test_split_expands_future_imports_and_blocks_remerge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            stocks = StockRepository(path)
            stocks.upsert("005930", "삼성전자")
            stocks.upsert("000660", "SK하이닉스")
            repository = ThemeRepository(path)
            repository.replace_for_stock("005930", ("호남개발호남클러스터",))

            repository.split_theme(
                "호남개발호남클러스터", ("호남개발", "호남클러스터"),
            )
            repository.replace_for_stock("000660", ("호남개발호남클러스터",))

            self.assertEqual(
                ("호남개발", "호남클러스터"), repository.themes_for_stock("000660"),
            )
            with self.assertRaisesRegex(ValueError, "분리 유지"):
                repository.set_theme_alias("호남개발", "호남클러스터")

    def test_theme_name_decisions_are_profile_scoped_and_copied_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            repository = ThemeRepository(path)
            repository.set_theme_alias("호남개발", "호남클러스터", decision_source="llm_review")
            repository.create_profile("복사본", copy_current=True)
            repository.create_profile("빈 프로필", copy_current=False)

            repository.select_profile("복사본")
            self.assertEqual(("호남클러스터",), repository.resolve_theme_names("호남개발"))
            repository.select_profile("빈 프로필")
            self.assertEqual(("호남개발",), repository.resolve_theme_names("호남개발"))

    def test_rejects_alias_cycles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            repository = ThemeRepository(path)
            repository.set_theme_alias("호남개발", "호남클러스터")

            with self.assertRaisesRegex(ValueError, "순환"):
                repository.set_theme_alias("호남클러스터", "호남개발")
