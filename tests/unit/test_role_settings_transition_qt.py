"""Root-only native Qt lane: actual settings/column dialogs on explicit role DB."""
from contextlib import closing
from datetime import datetime
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QTableWidget

from scripts import preserve_current_settings as operator
from kiwoom_monitor.domain.time_basis import KST
from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.infrastructure.persistence.settings_repository import SettingsRepository
from kiwoom_monitor.infrastructure.persistence.column_settings_repository import ColumnSettingsRepository
from kiwoom_monitor.presentation.column_manager_dialog import ColumnManagerDialog
from kiwoom_monitor.presentation.settings_dialog import SettingsDialog
from qt_settings_test_support import wait_until

NOW = datetime(2026, 10, 25, 10, 0, tzinfo=KST)


class RoleSettingsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def installed_fixture(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        source, artifact, carrier, target = root / "source.sqlite3", root / "artifact", root / "carrier", root / "role.sqlite3"
        Database(source).initialize(settings_role="fixture-original")
        operator.capture_settings(database=source, source_instance="fixture-source", source_owner="fixture-original",
                                  output=artifact, sqlite_frozen=True, artifact_now=NOW)
        _, records, digest = operator.verified_settings_rows(artifact)
        selection = dict(format="current-settings-selection/v1", manifest_sha256=digest,
                         source_instance="fixture-source", target_role="fixture-query",
                         records=[dict(record_id=key, placement="TRANSFER", target_role="fixture-query") for key in records])
        operator.accept_settings(artifact=artifact, selection=selection, target=carrier,
                                 operation_id="fixture-install", target_id="fixture-target", receipt_now=NOW)
        operator.install_settings(artifact=artifact, selection=selection, accepted_target=carrier, target=target)
        database = Database(target, now_provider=lambda: NOW)
        database.initialize(settings_role="fixture-query")
        return database, target

    def own_dialog(self, dialog):
        def close():
            dialog.close()
            dialog.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.addCleanup(close)
        return dialog

    def test_selected_role_actual_settings_dialog_accepts_only_after_normal_save_then_reopens(self):
        database, target = self.installed_fixture()
        dialog = self.own_dialog(SettingsDialog(database.settings, query_only=True))
        accepted = []
        dialog.accepted.connect(lambda: accepted.append(database.settings.get("rank_query_type")))
        dialog._rank_query_type.setCurrentIndex(dialog._rank_query_type.findData("2"))
        dialog._save()
        wait_until(lambda: dialog._save_request is None)
        self.assertEqual(["2"], accepted)
        reopened = Database(target)
        metadata = reopened.initialize(settings_role="fixture-query")
        self.assertFalse(metadata["applied"])
        self.assertFalse(metadata["order_authority"])
        self.assertEqual("2", reopened.settings.get("rank_query_type"))
        restored = self.own_dialog(SettingsDialog(reopened.settings, query_only=True))
        self.assertEqual("2", restored._rank_query_type.currentData())

    def test_selected_role_actual_save_failure_has_no_accept_or_false_applied_success(self):
        database, target = self.installed_fixture()
        dialog = self.own_dialog(SettingsDialog(database.settings, query_only=True))
        accepted = []
        dialog.accepted.connect(lambda: accepted.append(True))
        with closing(sqlite3.connect(target)) as connection, connection:
            connection.execute("CREATE TRIGGER controlled_failure BEFORE UPDATE ON settings "
                               "WHEN NEW.key='rank_query_type' BEGIN SELECT RAISE(ABORT,'controlled write failure'); END")
        dialog._rank_query_type.setCurrentIndex(dialog._rank_query_type.findData("2"))
        with patch.object(QMessageBox, "warning") as warning:
            dialog._save()
            wait_until(lambda: dialog._save_request is None)
        warning.assert_called_once()
        self.assertEqual([], accepted)
        self.assertEqual("5", Database(target).settings.get("rank_query_type"))
        self.assertNotEqual(QDialog.DialogCode.Accepted, dialog.result())

    def test_actual_column_save_publishes_after_native_success_and_failure_keeps_applied_projection(self):
        _, target = self.installed_fixture()
        repository = ColumnSettingsRepository(target, now_provider=lambda: NOW)
        table = QTableWidget(0, 2)
        self.addCleanup(table.deleteLater)
        table.setColumnWidth(0, 99)
        applied = []
        dialog = self.own_dialog(ColumnManagerDialog(repository, (("rank", "순위"), ("stock", "종목")), table,
                                on_applied=lambda: applied.append(next(row.width for row in repository.list() if row.name == "rank"))))
        dialog._save()
        self.assertEqual([99], applied)
        before = repository.list()
        with closing(sqlite3.connect(target)) as connection, connection:
            connection.execute("CREATE TRIGGER controlled_failure BEFORE UPDATE ON column_settings "
                               "WHEN NEW.column_name='rank' BEGIN SELECT RAISE(ABORT,'controlled write failure'); END")
        table.setColumnWidth(0, 200)
        with self.assertRaisesRegex(sqlite3.IntegrityError, '^controlled write failure$'):
            dialog._save()
        self.assertEqual([99], applied)
        self.assertEqual(before, repository.list())

    def test_explicit_role_unrepresentable_combo_spin_and_checkbox_values_never_write_or_open_normal_form(self):
        cases = (("rank_query_type", "invalid"), ("ui_mode", "invalid"),
                 ("theme_trade_summary_period", "invalid"), ("decimal_trade_value", "invalid"),
                 ("rank_changed_highlight_seconds", "invalid"), ("rank_changed_highlight_seconds", "11"),
                 ("rank_changed_highlight_seconds", "1.009"), ("near_high_sound_cooldown_seconds", "-1"),
                 ("near_high_sound_cooldown_seconds", "2.5"), ("near_high_sound_cooldown_seconds", "601"),
                 ("near_high_sound_cooldown_seconds", "inf"), ("near_high_sound_cooldown_seconds", "1e309"),
                 ("rank_changed_highlight_enabled", "invalid"), ("near_high_sound_enabled", "invalid"),
                 ("high_header_cycle_periods", "5,unknown"), ("settings_dialog_width", "399"), ("settings_dialog_width", "450"))
        for key, value in cases:
            with self.subTest(key=key, value=value):
                _, target = self.installed_fixture()
                with closing(sqlite3.connect(target)) as connection, connection:
                    connection.execute("UPDATE settings SET value=? WHERE key=?", (value,key))
                before = target.read_bytes()
                with self.assertRaisesRegex(ValueError, key):
                    SettingsDialog(SettingsRepository(target, strict_text=True), query_only=True, data_dir=target.parent)
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                self.assertEqual(before, target.read_bytes())
                self.assertFalse((target.parent / 'query-windows.ini').exists())

    def test_explicit_role_valid_controls_keep_dynamic_raw_rows_and_use_existing_normal_writer(self):
        _, target = self.installed_fixture()
        with closing(sqlite3.connect(target)) as connection, connection:
            connection.execute("INSERT INTO settings VALUES('unrelated:null',NULL)")
        repository = SettingsRepository(target, now_provider=lambda: NOW, strict_text=True)
        dialog = self.own_dialog(SettingsDialog(repository, query_only=True, data_dir=target.parent))
        accepted = []
        dialog.accepted.connect(lambda: accepted.append(True))
        dialog._rank_query_type.setCurrentIndex(dialog._rank_query_type.findData('2'))
        dialog._save()
        wait_until(lambda: dialog._save_request is None)
        self.assertEqual([True], accepted)
        self.assertEqual('2', repository.get('rank_query_type'))
        with closing(sqlite3.connect(target)) as connection:
            self.assertEqual([(None,)], connection.execute("SELECT value FROM settings WHERE key='unrelated:null'").fetchall())
