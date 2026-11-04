from __future__ import annotations

import os
import sqlite3
import tempfile
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from kiwoom_monitor.application.theme_preview import ThemeChange
from kiwoom_monitor.application.theme_suggestions import ProfileThemeSuggestion, ThemeSuggestion
from kiwoom_monitor.domain.theme_import import ThemeImportRow
from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.infrastructure.persistence.stock_repository import StockRepository
from kiwoom_monitor.infrastructure.persistence.theme_repository import ThemeRepository
from kiwoom_monitor.presentation.theme_dialogs import ThemeManagerDialog, ThemePreviewDialog, ThemeSuggestionReviewDialog, TextThemeImportDialog, review_image_theme_rows
from qt_settings_test_support import wait_until


def _preserve_delete_observations(path, label, observations, *, before=None):
    """Keep independent native evidence inside this worker's owned TEMP."""
    from contextlib import closing
    import hashlib
    import json
    artifact = Path(tempfile.gettempdir()) / label
    artifact.mkdir(exist_ok=False)
    if before is not None:
        (artifact / "before.sqlite3").write_bytes(before)
    with closing(sqlite3.connect(path)) as connection:
        native = {
            "profiles": connection.execute("SELECT profile_id,profile_name FROM theme_profiles ORDER BY profile_id").fetchall(),
            "active": connection.execute("SELECT value FROM settings WHERE key='theme_active_profile'").fetchone(),
            "version": connection.execute("SELECT updated_at FROM central_setting_versions WHERE setting_key='theme_active_profile'").fetchone(),
            "memberships": connection.execute("SELECT * FROM profile_stock_themes ORDER BY profile_id,stock_code,theme_name").fetchall(),
        }
        with closing(sqlite3.connect(artifact / "observed.sqlite3")) as backup:
            connection.backup(backup)
    (artifact / "observations.json").write_text(json.dumps({
        "source_database": str(path), "post_body_database_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "before_database_sha256": hashlib.sha256(before).hexdigest() if before is not None else None,
        "native": native, "observations": observations,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class ThemePreviewDialogTests(unittest.TestCase):
    def test_current_profile_delete_has_one_native_commit_and_notifies_coherent_selection(self):
        from kiwoom_monitor.infrastructure.persistence.theme_backup import ThemeBackupService
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "delete.sqlite3"
            stamp = datetime.fromisoformat("2031-01-02T03:04:05+09:00")
            database = Database(path, now_provider=lambda: stamp)
            database.initialize_query_role(role="delete-fixture")
            store = ThemeRepository(path, now_provider=lambda: stamp)
            source = store.active_profile
            store.create_profile("B")
            observed, statements = [], []
            store.set_change_callback(lambda: observed.append(ThemeBackupService(path).export_document()["active_profile"]))
            dialog = ThemeManagerDialog(store, database.settings)
            native = sqlite3.connect
            def connect(*args, **kwargs):
                connection = native(*args, **kwargs)
                connection.set_trace_callback(statements.append)
                return connection
            try:
                with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), patch.object(QMessageBox, "warning") as warning, patch("kiwoom_monitor.infrastructure.persistence.settings_repository.sqlite3.connect", side_effect=connect):
                    dialog._delete_current_theme_profile()
                _preserve_delete_observations(path, "profile-delete-desired-coherence", {
                    "source": source, "notifications": observed, "COMMITs": statements.count("COMMIT"),
                    "statements": statements, "repository_active": store.active_profile,
                    "cached_active": database.settings.get("theme_active_profile"),
                    "combo_active": dialog._theme_profile.currentText(),
                })
                # Same real UI body is a desired regression on the frozen old
                # source: it reports [source, B] and two writing COMMITs there.
                self.assertEqual({"notifications": ["B"], "commits": 1, "active": "B", "profiles": ("B",)},
                                 {"notifications": observed, "commits": statements.count("COMMIT"), "active": database.settings.get("theme_active_profile"), "profiles": store.list_profiles()})
                self.assertNotIn(source, store.list_profiles())
                self.assertEqual("B", store.active_profile)
                self.assertEqual("B", dialog._theme_profile.currentText())
                warning.assert_not_called()
            finally:
                dialog.close()

    def test_current_profile_delete_rolls_back_source_when_replacement_active_write_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "delete.sqlite3"
            stamp = datetime.fromisoformat("2031-01-02T03:04:05+09:00")
            database = Database(path, now_provider=lambda: stamp)
            database.initialize_query_role(role="delete-fixture")
            store = ThemeRepository(path, now_provider=lambda: stamp)
            source = store.active_profile
            store.create_profile("B")
            unapplied = []
            dialog = ThemeManagerDialog(store, database.settings, on_theme_unapplied=lambda: unapplied.append(True))
            before = path.read_bytes()
            fault = sqlite3.OperationalError("controlled delete replacement active/version failure")
            try:
                with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), patch.object(QMessageBox, "warning") as warning, patch.object(database.settings, "write_many_in_transaction", side_effect=fault):
                    dialog._delete_current_theme_profile()
                _preserve_delete_observations(path, "profile-delete-desired-rollback", {
                    "source": source, "repository_active": store.active_profile,
                    "cached_active": database.settings.get("theme_active_profile"),
                    "combo_active": dialog._theme_profile.currentText(), "unapplied": unapplied,
                    "enabled": dialog.isEnabled(), "warning": warning.call_args.args[2],
                }, before=before)
                # Desired before/after oracle: a failed replacement write must
                # retain the source and its original current selection together.
                self.assertEqual(before, path.read_bytes())
                self.assertEqual(("B", source), store.list_profiles())
                self.assertEqual(source, database.settings.get("theme_active_profile"))
                self.assertEqual(source, store.active_profile)
                self.assertEqual(source, dialog._theme_profile.currentText())
                self.assertEqual([], unapplied)
                self.assertTrue(dialog.isEnabled())
                warning.assert_called_once()
                self.assertIn("COMMIT 전 변경은 되돌렸습니다", warning.call_args.args[2])
            finally:
                dialog.close()

    def test_current_profile_delete_keeps_confirmed_target_when_nested_loop_selects_another_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "delete.sqlite3"
            stamp = datetime.fromisoformat("2031-01-02T03:04:05+09:00")
            database = Database(path, now_provider=lambda: stamp)
            database.initialize_query_role(role="delete-fixture")
            store = ThemeRepository(path, now_provider=lambda: stamp)
            source = store.active_profile
            store.create_profile("B first")
            store.create_profile("Z selected")
            dialog = ThemeManagerDialog(store, database.settings)
            prompts = []
            def confirm(_parent, _title, prompt):
                prompts.append(prompt)
                dialog._theme_profile.setCurrentText("Z selected")
                return QMessageBox.StandardButton.Yes
            try:
                with patch.object(QMessageBox, "question", side_effect=confirm), patch.object(QMessageBox, "warning") as warning:
                    dialog._delete_current_theme_profile()
                self.assertIn("'" + source + "'", prompts[0])
                self.assertEqual(("B first", "Z selected"), store.list_profiles())
                self.assertEqual("Z selected", store.active_profile)
                self.assertEqual("Z selected", database.settings.get("theme_active_profile"))
                self.assertEqual("Z selected", dialog._theme_profile.currentText())
                warning.assert_not_called()
            finally:
                dialog.close()

    def test_current_profile_rename_uses_same_port_and_blocked_refresh_without_reselect_or_second_write(self):
        from contextlib import closing
        from kiwoom_monitor.infrastructure.persistence.theme_backup import ThemeBackupService
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            database = Database(path)
            database.initialize_query_role(role="rename-fixture")
            store = ThemeRepository(path)
            observed = []
            store.set_change_callback(lambda: observed.append(ThemeBackupService(path).export_document()["active_profile"]))
            dialog = ThemeManagerDialog(store, database.settings)
            statements = []
            native = sqlite3.connect
            def connect(*args, **kwargs):
                connection = native(*args, **kwargs)
                connection.set_trace_callback(statements.append)
                return connection
            try:
                with patch("kiwoom_monitor.presentation.theme_dialogs.QInputDialog.getText", return_value=("User", True)), patch.object(database.settings, "set", wraps=database.settings.set) as separate_save, patch.object(store, "select_profile", wraps=store.select_profile) as select, patch("kiwoom_monitor.infrastructure.persistence.settings_repository.sqlite3.connect", side_effect=connect):
                    dialog._rename_current_theme_profile()
                separate_save.assert_not_called()
                select.assert_not_called()
                self.assertEqual(["User"], observed)
                self.assertEqual("User", dialog._theme_profile.currentText())
                self.assertEqual("User", database.settings.get("theme_active_profile"))
                self.assertEqual(1, sum(sql == "COMMIT" for sql in statements))
            finally:
                dialog.close()

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_active_profile_selection_notifies_generation_owner_after_actual_setting_save(self) -> None:
        from types import SimpleNamespace
        from kiwoom_monitor.presentation.main_window import MainWindow
        from kiwoom_monitor.infrastructure.persistence.theme_backup import ThemeBackupService
        from kiwoom_monitor.infrastructure.central_settings_sync import is_shared_setting
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            database = Database(path); database.initialize()
            repository = ThemeRepository(path)
            repository.create_profile("shared selection")
            accepted_inputs = []
            repository.set_change_callback(lambda: accepted_inputs.append(ThemeBackupService(path).export_document()))
            owner = SimpleNamespace(_theme_store=repository, _refresh_theme_badges=lambda: None,
                                    _refresh_rankings=lambda: None, _schedule_google_drive_upload=lambda _target: None)
            dialog = ThemeManagerDialog(repository, database.settings,
                                        on_profile_selected=lambda: MainWindow._on_themes_changed(owner, notify_repository=False))
            try:
                dialog._select_theme_profile("shared selection")
                self.assertEqual("shared selection", database.settings.get("theme_active_profile"))
                self.assertEqual("shared selection", repository.active_profile)
                self.assertEqual(["shared selection"], [d["active_profile"] for d in accepted_inputs])
                self.assertFalse(is_shared_setting("theme_active_profile"))
            finally:
                dialog.close(); dialog.deleteLater(); self.app.processEvents()

    def test_manual_import_batches_preview_and_only_saves_approved_changed_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            database = Database(path)
            database.initialize()
            stocks = StockRepository(path)
            stocks.upsert("005930", "삼성전자")
            stocks.upsert("000660", "SK하이닉스")
            store = ThemeRepository(path)
            store.replace_many((("005930", ("AI", "반도체")), ("000660", ("원본",))))
            changed = []
            dialog = ThemeManagerDialog(store, database.settings, stock_lookup=stocks,
                                        on_themes_changed=lambda: changed.append(True))
            observed = []

            class Preview:
                approve = False

                def __init__(self, changes, *_args):
                    self._changes = changes
                    observed.append(changes)

                def exec(self):
                    return self.approve

                def changes(self, _separators):
                    return self._changes

            try:
                with patch("kiwoom_monitor.presentation.theme_dialogs.ThemePreviewDialog", Preview), \
                        patch("kiwoom_monitor.presentation.theme_dialogs.QMessageBox.information"), \
                        patch.object(store, "themes_for_stocks", wraps=store.themes_for_stocks) as batch, \
                        patch.object(store, "replace_many", wraps=store.replace_many) as save:
                    raw = (("삼성전자", "반도체,ai"), ("SK하이닉스", "신규"))
                    dialog._apply_import_rows(raw, ",", "theme_new_import_exclusions")
                    self.assertEqual((), tuple(changed))
                    save.assert_not_called()
                    self.assertEqual(("원본",), store.themes_for_stock("000660"))
                    Preview.approve = True
                    dialog._apply_import_rows(raw, ",", "theme_new_import_exclusions")
                    wait_until(lambda: dialog._save_request is None)
                    self.assertEqual(2, batch.call_count)
                    save.assert_called_once_with((("000660", ("신규",)),))
                self.assertEqual(observed[0], observed[1])
                self.assertEqual(("변경 없음", "테마 변경"), tuple(change.status for change in observed[0]))
                self.assertEqual(("AI", "반도체"), store.themes_for_stock("005930"))
                self.assertEqual(("신규",), store.themes_for_stock("000660"))
                self.assertEqual([True], changed)
                # An unmatched manual row reaches the common split branch before
                # the same existing preview/cancel and asynchronous save path.
                with patch("kiwoom_monitor.presentation.theme_dialogs.ThemePreviewDialog", Preview), \
                        patch("kiwoom_monitor.presentation.theme_dialogs.QMessageBox.information"), \
                        patch("kiwoom_monitor.presentation.theme_dialogs.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), \
                        patch.object(store, "replace_many", wraps=store.replace_many) as save:
                    Preview.approve = False
                    dialog._apply_import_rows((("삼성전자SK하이닉스", "분리"),), ",", "theme_new_import_exclusions")
                    save.assert_not_called()
                    self.assertEqual(("신규",), store.themes_for_stock("000660"))
                    Preview.approve = True
                    dialog._apply_import_rows((("삼성전자SK하이닉스", "분리"),), ",", "theme_new_import_exclusions")
                    wait_until(lambda: dialog._save_request is None)
                    save.assert_called_once_with((("005930", ("분리",)), ("000660", ("분리",))))
                self.assertEqual([True, True], changed)
                with patch("kiwoom_monitor.presentation.theme_dialogs.QMessageBox.question", side_effect=(QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)), \
                        patch("kiwoom_monitor.presentation.theme_dialogs.choose_similar_stock", return_value=(("000660", "SK하이닉스"), False)), \
                        patch("kiwoom_monitor.presentation.theme_dialogs.QInputDialog.getText", side_effect=AssertionError("manual import must retain its picker")):
                    resolved, cancelled = dialog._resolve_unmatched_new_rows((ThemeImportRow("삼성전자SK하이닉수", ("수동",)),))
                self.assertFalse(cancelled)
                self.assertEqual((("000660", "SK하이닉스", ("수동",)),), tuple((row.code, row.name, row.themes) for row in resolved))
                self.assertIsNone(stocks.find_code_by_name("삼성전자SK하이닉수"))
            finally:
                dialog.close()

    def test_image_ocr_review_returns_only_approved_changes_without_saving(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            database = Database(path)
            database.initialize()
            lookup = StockRepository(path)
            lookup.upsert("005930", "삼성전자")
            store = ThemeRepository(path)

            class Editor:
                def __init__(self, rows, *_args):
                    self._rows = rows

                def exec(self):
                    return True

                def rows(self):
                    return self._rows

            class Preview:
                approve = True

                def __init__(self, changes, *_args):
                    self._changes = changes

                def exec(self):
                    return self.approve

                def changes(self, _separators):
                    return self._changes

            status: list[str] = []
            with patch("kiwoom_monitor.presentation.theme_dialogs.ImageThemeRowsDialog", Editor), \
                    patch("kiwoom_monitor.presentation.theme_dialogs.ThemePreviewDialog", Preview):
                approved = review_image_theme_rows(
                    (("삼성전자", "반도체"),), None, database.settings, lookup, store, status.append,
                )
                Preview.approve = False
                cancelled = review_image_theme_rows(
                    (("삼성전자", "AI"),), None, database.settings, lookup, store, status.append,
                )
            self.assertEqual((("005930", ("반도체",)),), approved)
            self.assertIsNone(cancelled)
            self.assertEqual((), store.themes_for_stock("005930"))
            self.assertEqual(1, len(status))
            lookup.upsert("000660", "SK하이닉스")
            Preview.approve = True
            with patch("kiwoom_monitor.presentation.theme_dialogs.ImageThemeRowsDialog", Editor), \
                    patch("kiwoom_monitor.presentation.theme_dialogs.ThemePreviewDialog", Preview), \
                    patch("kiwoom_monitor.presentation.theme_dialogs.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
                split = review_image_theme_rows((("삼성전자SK하이닉스", "분리"),), None, database.settings, lookup, store, status.append)
            self.assertEqual((("005930", ("분리",)), ("000660", ("분리",))), split)
            self.assertEqual((), store.themes_for_stock("000660"))
            # Distinct unmatched rows pass validation. The first correction
            # persists an alias that the second row enters as its correction.
            typo = "삼성전자SK하이닉수"
            with patch("kiwoom_monitor.presentation.theme_dialogs.ImageThemeRowsDialog", Editor), \
                    patch("kiwoom_monitor.presentation.theme_dialogs.ThemePreviewDialog", Preview), \
                    patch("kiwoom_monitor.presentation.theme_dialogs.QMessageBox.question", side_effect=(QMessageBox.StandardButton.No,) * 2), \
                    patch("kiwoom_monitor.presentation.theme_dialogs.QInputDialog.getText", side_effect=(("삼성전자", True), (typo, True))), \
                    patch("kiwoom_monitor.presentation.theme_dialogs.choose_similar_stock", side_effect=AssertionError("fresh alias should resolve the corrected input")):
                corrected = review_image_theme_rows(((typo, "교정"), ("다른미확인", "교정")), None, database.settings, lookup, store, status.append)
            self.assertEqual((("005930", ("교정",)), ("005930", ("교정",))), corrected)
            self.assertEqual("005930", lookup.find_code_by_name(typo))
            self.assertEqual((), store.themes_for_stock("005930"))

    def test_excel_import_from_theme_manager_applies_only_after_preview(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            database = Database(path)
            database.initialize()
            lookup = StockRepository(path)
            lookup.upsert("005930", "삼성전자")
            lookup.upsert("000660", "SK하이닉스")
            store = ThemeRepository(path)
            changed: list[bool] = []
            dialog = ThemeManagerDialog(
                store, database.settings, on_themes_changed=lambda: changed.append(True),
                stock_lookup=lookup,
            )

            class Workbook:
                theme = "반도체"
                name = "삼성전자"

                def __init__(self, _path):
                    pass

                def load_header_and_rows(self):
                    return ("종목명", "테마"), ((self.name, self.theme),)

            class Editor:
                def __init__(self, rows, *_args):
                    self._rows = rows

                def setWindowTitle(self, _title):
                    pass

                def findChildren(self, _kind):
                    return []

                def exec(self):
                    return True

                def rows(self):
                    return self._rows

            class Preview:
                approve = True

                def __init__(self, changes, *_args):
                    self._changes = changes

                def exec(self):
                    return self.approve

                def changes(self, _separators):
                    return self._changes

            with patch("kiwoom_monitor.presentation.theme_dialogs.QFileDialog.getOpenFileName", return_value=(str(path.with_suffix(".xlsx")), "")), \
                    patch("kiwoom_monitor.presentation.theme_dialogs.ExcelThemeRepository", Workbook), \
                    patch("kiwoom_monitor.presentation.theme_dialogs.ImageThemeRowsDialog", Editor), \
                    patch("kiwoom_monitor.presentation.theme_dialogs.ThemePreviewDialog", Preview), \
                    patch("kiwoom_monitor.presentation.theme_dialogs.QMessageBox.information"):
                dialog._import_excel()
                wait_until(lambda: dialog._save_request is None)
                Workbook.theme = "AI"
                Preview.approve = False
                dialog._import_excel()
            self.assertEqual(("반도체",), store.themes_for_stock("005930"))
            self.assertEqual([True], changed)
            Workbook.name = "삼성전자SK하이닉스"
            Workbook.theme = "분리"
            with patch("kiwoom_monitor.presentation.theme_dialogs.QFileDialog.getOpenFileName", return_value=(str(path.with_suffix(".xlsx")), "")), \
                    patch("kiwoom_monitor.presentation.theme_dialogs.ExcelThemeRepository", Workbook), \
                    patch("kiwoom_monitor.presentation.theme_dialogs.ImageThemeRowsDialog", Editor), \
                    patch("kiwoom_monitor.presentation.theme_dialogs.ThemePreviewDialog", Preview), \
                    patch("kiwoom_monitor.presentation.theme_dialogs.QMessageBox.information"), \
                    patch("kiwoom_monitor.presentation.theme_dialogs.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
                dialog._import_excel()
                self.assertEqual(("반도체",), store.themes_for_stock("005930"))
                self.assertEqual((), store.themes_for_stock("000660"))
                Preview.approve = True
                dialog._import_excel()
                wait_until(lambda: dialog._save_request is None)
            self.assertEqual(("분리",), store.themes_for_stock("005930"))
            self.assertEqual(("분리",), store.themes_for_stock("000660"))
            self.assertEqual([True, True], changed)
            dialog.close()

    def test_theme_save_keeps_gui_responsive_during_sqlite_contention(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            database = Database(path)
            database.initialize()
            StockRepository(path).upsert("005930", "삼성전자")
            store = ThemeRepository(path)
            dialog = ThemeManagerDialog(store, database.settings)
            lock = sqlite3.connect(path)
            try:
                lock.execute("BEGIN IMMEDIATE")
                started = time.monotonic()
                dialog._submit_theme_change(lambda: store.replace_for_stock("005930", ("반도체",)))
                self.assertLess(time.monotonic() - started, 0.5)
            finally:
                lock.rollback()
                lock.close()
            wait_until(lambda: dialog._save_request is None)
            self.assertEqual(("반도체",), store.themes_for_stock("005930"))
            dialog.close()

    def test_text_theme_options_do_not_write_on_each_keystroke_or_block_accept(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            database = Database(path)
            database.initialize()
            dialog = TextThemeImportDialog(database.settings)
            lock = sqlite3.connect(path)
            try:
                lock.execute("BEGIN IMMEDIATE")
                started = time.monotonic()
                dialog._heading_marker.setText("⭐")
                self.assertLess(time.monotonic() - started, 0.5)
                started = time.monotonic()
                dialog._save_and_accept()
                self.assertLess(time.monotonic() - started, 0.5)
                self.assertNotEqual(QDialog.DialogCode.Accepted, dialog.result())
            finally:
                lock.rollback()
                lock.close()
            wait_until(lambda: dialog.result() == QDialog.DialogCode.Accepted)
            self.assertEqual("⭐", database.settings.get("theme_text_heading_marker"))

    def test_edited_changes_are_returned_without_name_error(self) -> None:
        original = ThemeChange("005930", "삼성전자", ("반도체",), ("AI",), "테마 변경")
        dialog = ThemePreviewDialog((original,), 0)

        changes = dialog.changes(",")

        self.assertEqual(1, len(changes))
        self.assertEqual(("반도체", "AI"), changes[0].after)
        self.assertEqual("테마 추가", changes[0].status)
        dialog.close()

    def test_theme_suggestion_review_returns_edited_approval_and_rejection(self) -> None:
        approved = ProfileThemeSuggestion(
            "005930", "삼성전자", "news-1", "호남클러스터", ("호남클러스터",),
            "복수 기업 참여", 85, "pending", "openai", "model", datetime.now(UTC),
        )
        rejected = ProfileThemeSuggestion(
            "000660", "SK하이닉스", "news-2", "주가상승", ("주가상승",),
            "주가 움직임만 언급", 20, "pending", "openai", "model", datetime.now(UTC),
        )
        dialog = ThemeSuggestionReviewDialog((approved, rejected))
        dialog._table.item(0, dialog.TARGET_COLUMN).setText("호남개발")
        dialog._table.cellWidget(0, dialog.ACTION_COLUMN).setCurrentIndex(1)
        dialog._table.cellWidget(1, dialog.ACTION_COLUMN).setCurrentIndex(2)

        decisions = dialog.decisions()

        self.assertEqual((approved, "approved", ("호남개발",)), decisions[0])
        self.assertEqual((rejected, "rejected", ()), decisions[1])
        dialog.close()

    def test_unchanged_target_is_re_resolved_when_an_earlier_row_updates_alias(self) -> None:
        first = ProfileThemeSuggestion(
            "000001", "첫 종목", "news-1", "호남클러스터", ("호남클러스터",),
            "첫 근거", 80, "pending", "openai", "model", datetime.now(UTC),
        )
        second = ProfileThemeSuggestion(
            "000002", "둘째 종목", "news-2", "호남클러스터", ("호남클러스터",),
            "둘째 근거", 75, "pending", "openai", "model", datetime.now(UTC),
        )
        dialog = ThemeSuggestionReviewDialog((first, second))
        dialog._table.item(0, dialog.TARGET_COLUMN).setText("호남개발")
        dialog._table.cellWidget(0, dialog.ACTION_COLUMN).setCurrentIndex(1)
        dialog._table.cellWidget(1, dialog.ACTION_COLUMN).setCurrentIndex(1)

        decisions = dialog.decisions()

        self.assertEqual(("호남개발",), decisions[0][2])
        self.assertEqual((), decisions[1][2])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "theme.sqlite3"
            Database(path).initialize()
            stocks = StockRepository(path)
            for row in (first, second):
                stocks.upsert(row.stock_code, row.stock_name)
            repository = ThemeRepository(path)
            repository.import_ai_theme_suggestions(tuple(ThemeSuggestion(row.stock_code, row.news_identity, row.raw_theme_name, row.evidence, row.confidence, row.provider, row.model, "body", row.analyzed_at) for row in (first, second)))
            for suggestion, decision, names in decisions:
                repository.review_ai_theme_suggestion(suggestion.key, approved=decision == "approved", theme_names=names)
            reviewed = repository.list_ai_theme_suggestions("approved")
            self.assertEqual(2, len(reviewed))
            self.assertEqual({("호남개발",)}, {row.resolved_theme_names for row in reviewed})
        dialog.close()


if __name__ == "__main__":
    unittest.main()
