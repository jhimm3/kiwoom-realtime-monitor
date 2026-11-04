"""One desired regression: exact body for old107 FAIL and later candidate PASS."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import threading
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from tests.unit import test_main_window as fixtures
from kiwoom_monitor.presentation.theme_dialogs import ThemeManagerDialog
from qt_settings_test_support import wait_until


class DesiredProfileSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_accepted_theme_write_keeps_original_profile_when_another_editor_selects(self):
        # Existing full fixture uses real AppController/Settings/SQLite, routes
        # all enabled paths to task TEMP, and keeps data_dir=None/full mode.
        root, path, settings, store, window = fixtures.MainWindowTest.full_rename_fixture(self)
        controller = window._app_controller
        source = store.active_profile
        target = "B target"
        producer = selector = None
        entered, release = threading.Event(), threading.Event()
        request = None
        run_dir = Path(os.environ.get("KIWOOM_QUERY_S2_RUN_DIR", root))
        evidence_path = run_dir / "retarget-observations.json"
        evidence = {"desired_test_body_same_before_after": True,
                    "source_profile": source, "target_profile": target,
                    "task_payload": {"stock_code": "005930", "themes": ["A accepted"],
                                     "profile_id_is_captured": False},
                    "full_data_dir": None, "database_path": str(path), "phases": []}

        def capture(phase):
            with closing(sqlite3.connect(path)) as connection:
                profiles = connection.execute("SELECT profile_id,profile_name FROM theme_profiles ORDER BY profile_id").fetchall()
                memberships = connection.execute(
                    "SELECT p.profile_id,p.profile_name,st.stock_code,st.theme_name,st.custom_color "
                    "FROM theme_profiles p LEFT JOIN profile_stock_themes st ON st.profile_id=p.profile_id "
                    "ORDER BY p.profile_id,st.stock_code,st.theme_name").fetchall()
                active = connection.execute("SELECT value FROM settings WHERE key='theme_active_profile'").fetchone()
                version = connection.execute("SELECT updated_at FROM central_setting_versions WHERE setting_key='theme_active_profile'").fetchone()
            state = {"phase": phase, "profiles": profiles, "native_memberships": memberships,
                     "native_active": active[0], "native_active_version": version[0] if version else None,
                     "cached_active": settings.get("theme_active_profile"), "repository_active": store.active_profile,
                     "main_themes": dict(window._themes), "producer_combo": producer._theme_profile.currentText(),
                     "selector_combo": selector._theme_profile.currentText(),
                     "producer_rows": list(producer._rows), "selector_rows": list(selector._rows),
                     "producer_enabled": producer.isEnabled(), "producer_pending": producer.has_pending_theme_save,
                     "main_pending": window._theme_save_request is not None,
                     "request_running": request.isRunning() if request is not None else None}
            evidence["phases"].append(state)
            evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return state

        def accepted_write():
            entered.set()
            if not release.wait(5):
                raise AssertionError("accepted theme write was not released")
            # Real native writer resolves store._profile_name only now. The
            # accepted payload deliberately matches current production captures.
            store.replace_for_stock("005930", ("A accepted",))

        try:
            store.replace_for_stock("005930", ("A original",))
            store.create_profile(target)
            store.select_profile(target)  # generic setup view; no saved selection
            store.replace_for_stock("005930", ("B original",))
            store.select_profile(source)
            window._on_themes_changed()
            window._open_settings()
            producer = window._settings_dialog.findChild(ThemeManagerDialog)
            window._open_theme_manager()
            selector = window._theme_manager_dialog
            self.assertIs(store, producer._repository)
            self.assertIs(store, selector._repository)
            self.assertTrue(selector.isEnabled())
            initial = capture("before_accepted_write")
            # Mock only warning presentation so the later correct product may
            # decline selection without blocking an unattended modal dialog.
            with patch("kiwoom_monitor.presentation.theme_dialogs.QMessageBox.warning"):
                producer._submit_theme_change(accepted_write)
                request = producer._save_request
                self.assertTrue(entered.wait(5))
                selector._theme_profile.setCurrentText(target)  # actual signal/caller
                held = capture("B_selection_attempt_while_A_write_accepted")
                release.set()
                self.assertTrue(request.wait(5000))
                before_queued = capture("native_done_before_queued_publication")
                # Thread completed; do not dereference QObject after its queued
                # finished/deleteLater. The actual pending owner still exists.
                request = None
                QCoreApplication.processEvents()
                wait_until(lambda: not producer.has_pending_theme_save)
                published = capture("queued_publication_complete_before_explicit_reselect")
                # Keep a real SQLite artifact as well as independent SELECT
                # rows, before any desired assertion or temporary cleanup.
                with closing(sqlite3.connect(path)) as connection, closing(sqlite3.connect(run_dir / "retarget-observed.sqlite3")) as backup:
                    connection.backup(backup)
                selector._theme_profile.setCurrentText(target)  # explicit retry after completion
                QCoreApplication.processEvents()
                after = capture("explicit_B_request_after_work_completed")
            evidence["desired_assertions_begin_after_all_observations"] = True
            evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            def values(state, profile):
                return tuple(row[3] for row in state["native_memberships"]
                             if row[1] == profile and row[2] == "005930")
            # The first desired assertion reads actual native memberships. Old
            # product should FAIL here: A stays original and B receives A data.
            self.assertEqual(("A accepted",), values(published, source))
            self.assertEqual(("B original",), values(published, target))
            self.assertEqual(source, held["repository_active"])
            self.assertEqual(source, held["native_active"])
            self.assertEqual(source, held["selector_combo"])
            self.assertEqual(initial["native_active_version"], held["native_active_version"])
            self.assertTrue(before_queued["producer_pending"])
            self.assertFalse(before_queued["request_running"])
            self.assertEqual(source, published["repository_active"])
            self.assertEqual(source, published["native_active"])
            self.assertEqual({"삼성전자": "A accepted"}, published["main_themes"])
            self.assertFalse(published["producer_pending"])
            self.assertEqual(target, after["repository_active"])
            self.assertEqual(target, after["native_active"])
            self.assertEqual({"삼성전자": "B original"}, after["main_themes"])
            self.assertEqual(target, after["producer_combo"])
            self.assertEqual(target, after["selector_combo"])
            self.assertFalse(controller.closing)
        finally:
            release.set()
            if request is not None:
                request.wait(5000)
            QCoreApplication.processEvents()
            window.close()
            wait_until(lambda: controller._shutdown_stage == "ready")
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            QApplication.processEvents()
