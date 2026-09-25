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

from PySide6.QtWidgets import QApplication, QDialog

from kiwoom_monitor.application.theme_preview import ThemeChange
from kiwoom_monitor.application.theme_suggestions import ProfileThemeSuggestion
from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.infrastructure.persistence.stock_repository import StockRepository
from kiwoom_monitor.infrastructure.persistence.theme_repository import ThemeRepository
from kiwoom_monitor.presentation.theme_dialogs import ThemeManagerDialog, ThemePreviewDialog, ThemeSuggestionReviewDialog, TextThemeImportDialog, review_image_theme_rows
from qt_settings_test_support import wait_until


class ThemePreviewDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

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

    def test_excel_import_from_theme_manager_applies_only_after_preview(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            database = Database(path)
            database.initialize()
            lookup = StockRepository(path)
            lookup.upsert("005930", "삼성전자")
            store = ThemeRepository(path)
            changed: list[bool] = []
            dialog = ThemeManagerDialog(
                store, database.settings, on_themes_changed=lambda: changed.append(True),
                stock_lookup=lookup,
            )

            class Workbook:
                theme = "반도체"

                def __init__(self, _path):
                    pass

                def load_header_and_rows(self):
                    return ("종목명", "테마"), (("삼성전자", self.theme),)

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
        dialog.close()


if __name__ == "__main__":
    unittest.main()
