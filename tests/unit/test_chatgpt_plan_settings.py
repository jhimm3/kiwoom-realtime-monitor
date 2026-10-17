from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from kiwoom_monitor.infrastructure.chatgpt_auth import ChatGPTPlanError
from kiwoom_monitor.infrastructure.naver_news import LocalNaverNewsConfig, NaverNewsCredentials, NewsAISettings
from kiwoom_monitor.presentation.news_settings_dialog import NaverNewsSettingsDialog
from kiwoom_monitor.presentation.news_execution import automatic_ai_run_allowed
from qt_settings_test_support import dispose_dialogs, wait_until


class FakeAuth:
    def __init__(self):
        self.active = "registration-1"
        self.connected = True
        self.permitted = True
        self.started = threading.Event()
        self.release = threading.Event(); self.release.set()
        self.selected = []

    def status(self):
        self.started.set()
        self.release.wait(3)
        return {"active": self.active, "accounts": [{"id": self.active, "label": "Test account · registration-1",
                    "connected": self.connected, "permitted": self.permitted}]}

    def select(self, account):
        self.selected.append(account); self.active = account

    def connect(self, cancel, *, account_id=""):
        while not cancel.wait(.01):
            pass
        raise ChatGPTPlanError("cancelled")


class ChatGPTPlanSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.config = LocalNaverNewsConfig(root / "news.dat", chatgpt_storage_path=root / "isolated-oauth" / "credentials.dpapi")
        self.auth = FakeAuth()
        self.models = patch("kiwoom_monitor.presentation.news_settings_dialog.ChatGPTPlanClient.models", return_value=(("Account model", "account-model"),))
        self.models.start(); self.addCleanup(self.models.stop)

    def tearDown(self):
        self.auth.release.set()
        dispose_dialogs()

    def dialog(self, **kwargs):
        return NaverNewsSettingsDialog(self.config, chatgpt_auth=self.auth, **kwargs)

    def plan(self, dialog):
        dialog._ai_provider.setCurrentIndex(dialog._ai_provider.findData("chatgpt_plan"))
        wait_until(lambda: dialog._plan_worker is None)

    def test_select_plan_uses_dynamic_catalog_preserves_paid_key_and_no_token_settings(self):
        import secrets
        key = secrets.token_urlsafe(32)
        self.config.save(NaverNewsCredentials(), ai=NewsAISettings("gemini", key, "paid-model"))
        dialog = self.dialog(section="connections")
        self.plan(dialog)
        self.assertFalse(dialog._ai_key.isEnabled())
        self.assertEqual(dialog._ai_model.currentData(), "account-model")
        dialog._save()
        saved = self.config.load_ai()
        self.assertEqual(saved.provider, "chatgpt_plan")
        self.assertEqual(saved.api_key, key)
        self.assertEqual(saved.model, "account-model")
        values = self.config._load_values()
        self.assertNotIn("chatgpt_storage_path", values)
        self.assertFalse(any("token" in name for name in values))

    def test_partial_permission_blocks_plan_save_without_erasing_paid_settings(self):
        self.config.save(NaverNewsCredentials(), ai=NewsAISettings("gemini", "", "paid-model"))
        self.auth.permitted = False
        dialog = self.dialog(section="connections")
        self.plan(dialog)
        with patch("kiwoom_monitor.presentation.news_settings_dialog.QMessageBox.warning") as warning:
            dialog._save()
        self.assertTrue(warning.called)
        self.assertEqual(self.config.load_ai().provider, "gemini")
        self.assertEqual(dialog._ai_model.count(), 0)

    def test_network_status_does_not_block_gui_or_allow_save(self):
        self.auth.release.clear()
        dialog = self.dialog(section="connections")
        dialog._ai_provider.setCurrentIndex(dialog._ai_provider.findData("chatgpt_plan"))
        try:
            wait_until(self.auth.started.is_set)
            ticks = []
            QTimer.singleShot(0, lambda: ticks.append(True))
            wait_until(lambda: bool(ticks))
            dialog._save()
            self.assertFalse(self.config._path.exists())
        finally:
            self.auth.release.set()
            wait_until(lambda: dialog._plan_worker is None)

    def test_close_cancels_login_and_worker_finishes(self):
        dialog = self.dialog(section="connections")
        self.plan(dialog)
        dialog._plan_connect_account()
        worker = dialog._plan_worker
        finished = threading.Event()
        worker.finished.connect(finished.set)
        dialog.reject()
        wait_until(lambda: dialog._plan_worker is None)
        self.assertTrue(dialog._plan_cancel.is_set())
        wait_until(finished.is_set)

    def test_late_plan_status_does_not_overwrite_paid_provider_model(self):
        self.auth.release.clear()
        dialog = self.dialog(section="connections")
        dialog._ai_provider.setCurrentIndex(dialog._ai_provider.findData("chatgpt_plan"))
        wait_until(self.auth.started.is_set)
        dialog._ai_provider.setCurrentIndex(dialog._ai_provider.findData("gemini"))
        expected = dialog._ai_model.currentData()
        self.auth.release.set()
        wait_until(lambda: dialog._plan_worker is None)
        self.assertEqual(dialog._ai_model.currentData(), expected)

    def test_nas_ui_uses_remote_catalog_and_local_queue_uses_no_api_key(self):
        owner = self
        class Remote:
            def chatgpt_status(self): return owner.auth.status()
            def chatgpt_models(self): return (("NAS model", "nas-model"),)
        dialog = self.dialog(section="connections", operational_client=Remote())
        self.plan(dialog)
        self.assertEqual(dialog._ai_model.currentData(), "nas-model")
        self.assertFalse(dialog._plan_connect.isEnabled())
        self.assertFalse(dialog._plan_add.isEnabled())
        settings = NewsAISettings(provider="chatgpt_plan", model="account-model", auto_analyze=True, chatgpt_storage_path=self.config.chatgpt_storage_path)
        self.assertTrue(automatic_ai_run_allowed(settings, manual_queue=False, central_client_available=False, used_requests=0))
        self.assertTrue(automatic_ai_run_allowed(settings, manual_queue=False, central_client_available=True, used_requests=0))

    def test_subscription_failure_pauses_pending_and_later_automatic_news(self):
        from kiwoom_monitor.presentation.stock_news_window import StockNewsWindow
        window = StockNewsWindow(self.config._path, Path(self.temp.name) / "monitor.sqlite3")
        self.addCleanup(lambda: window.close() if isValid(window) else None)
        window._config.save(NaverNewsCredentials(), ai=NewsAISettings(provider="chatgpt_plan", model="account-model", auto_analyze=True))
        window._ai_request_provider = "chatgpt_plan"
        window._ai_automatic_run = True
        window._auto_ai_identities = {"pending-article"}
        window._on_ai_failed("[subscription_sharing_usage_limit_exceeded; HTTP 429]")
        self.assertTrue(window._chatgpt_ai_paused)
        self.assertFalse(window._ai_continue)
        self.assertEqual(window._auto_ai_identities, {"pending-article"})
        with patch.object(window, "_start_ai_groups") as start:
            window._auto_analyze_next()
            window._auto_ai_identities.add("later-article")
            window._resume_auto_analysis()
            QApplication.processEvents()
            self.assertFalse(start.called)
        with patch.object(window, "_configure_recent_auto_candidates"), patch.object(window, "_resume_auto_analysis") as resume:
            window._toggle_auto_analysis(True)
            self.assertFalse(window._chatgpt_ai_paused)
            self.assertTrue(resume.called)
        window.close()

    def test_failed_request_uses_inflight_provider_and_paid_queue_still_continues(self):
        from types import SimpleNamespace
        from kiwoom_monitor.presentation.stock_news_window import StockNewsWindow
        window = StockNewsWindow(self.config._path, Path(self.temp.name) / "monitor.sqlite3")
        self.addCleanup(lambda: window.close() if isValid(window) else None)
        window._config.save(NaverNewsCredentials(), ai=NewsAISettings(provider="gemini", model="new-provider"))
        window._ai_worker = SimpleNamespace(_settings=NewsAISettings(provider="chatgpt_plan", model="old-model"))
        window._ai_request_provider = "chatgpt_plan"
        with patch.object(window._ai_repository, "log_request") as record:
            window._on_ai_failed("[not_connected; HTTP 0]", True, 1)
            self.assertEqual(record.call_args.args[:2], ("chatgpt_plan", "old-model"))
        window._ai_worker = None
        window._chatgpt_ai_paused = False
        window._ai_request_provider = "gemini"
        window._ai_automatic_run = True
        window._auto_ai_identities = {"next-article"}
        window._on_ai_failed("temporary paid provider failure")
        self.assertTrue(window._ai_continue)
        self.assertFalse(window._chatgpt_ai_paused)
        window.close()
