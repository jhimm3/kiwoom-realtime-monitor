from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from kiwoom_monitor.infrastructure.naver_news import (
    LocalNaverNewsConfig,
    NaverNewsCredentials,
    NewsAISettings,
    NewsFilterSettings,
    OfficialNewsSettings,
)
from kiwoom_monitor.presentation.news_settings_dialog import NaverNewsSettingsDialog
from qt_settings_test_support import dispose_dialogs, wait_until
from threading import Event
from PySide6.QtCore import QTimer


class _OperationalClient:
    def __init__(self) -> None:
        self.values: dict[str, object] = {
            "ai_provider": "gemini",
            "ai_model": "gemini-3.5-flash-lite",
            "ai_daily_limit": 500,
            "news_refresh_seconds": 300,
            "dart_enabled": True,
            "news_processing_excluded_providers": ["thebell.co.kr"],
        }
        self.changes: dict[str, object] | None = None

    def load(self) -> dict[str, object]:
        return dict(self.values)

    def update(self, changes: dict[str, object]) -> dict[str, object]:
        self.changes = dict(changes)
        self.values.update(changes)
        return dict(self.values)


class NewsSettingsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        dispose_dialogs()

    def test_nas_overlapping_settings_are_loaded_saved_and_mirrored_locally(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = LocalNaverNewsConfig(Path(directory) / "naver_news.dat")
            config.save(
                NaverNewsCredentials("client", "secret"),
                ai=NewsAISettings("openai", "ai-secret", "old-model", 30),
                official=OfficialNewsSettings("dart-secret", False),
            )
            central = _OperationalClient()

            dialog = NaverNewsSettingsDialog(config, operational_client=central)  # type: ignore[arg-type]
            dialog._load_operational_settings()
            wait_until(lambda: dialog._operations_worker is None)
            self.assertEqual("gemini", config.load_ai().provider)
            self.assertEqual("gemini-3.5-flash-lite", dialog._ai_model.currentData())
            self.assertEqual(500, dialog._ai_limit.value())
            self.assertTrue(dialog._dart_enabled.isChecked())
            self.assertEqual("thebell.co.kr", dialog._processing_excluded_providers.toPlainText())

            dialog._ai_limit.setValue(321)
            dialog._dart_enabled.setChecked(False)
            dialog._processing_excluded_providers.setPlainText("thebell.co.kr, 연합인포맥스")
            dialog._save()
            wait_until(lambda: dialog._operations_worker is None)

            self.assertEqual(321, central.changes["ai_daily_limit"] if central.changes else None)
            self.assertFalse(central.changes["dart_enabled"] if central.changes else True)
            self.assertEqual(
                ["thebell.co.kr", "연합인포맥스"],
                central.changes["news_processing_excluded_providers"] if central.changes else None,
            )
            self.assertEqual(321, config.load_ai().daily_limit)
            self.assertFalse(config.load_official().dart_enabled)
            self.assertEqual("ai-secret", config.load_ai().api_key)
            self.assertEqual("dart-secret", config.load_official().dart_api_key)

    def test_connection_section_saves_visible_keys_without_touching_hidden_news_options(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = LocalNaverNewsConfig(Path(directory) / "naver_news.dat")
            news_filter = NewsFilterSettings(False, ("광고",), ("example.com",), False)
            config.save(
                NaverNewsCredentials("old", "old-secret"), news_filter,
                NewsAISettings("none", "", "", 77, 4, True, "batch", 3),
                OfficialNewsSettings("old-dart", True),
            )
            dialog = NaverNewsSettingsDialog(config, section="connections")
            dialog._client_id.setText("new")
            dialog._client_secret.setText("new-secret")
            dialog._ai_provider.setCurrentIndex(dialog._ai_provider.findData("gemini"))
            dialog._ai_key.setText("new-ai-key")
            dialog._dart_key.setText("new-dart")

            dialog._save()

            self.assertEqual(NaverNewsCredentials("new", "new-secret"), config.load())
            self.assertEqual(news_filter, config.load_filter())
            self.assertEqual(77, config.load_ai().daily_limit)
            self.assertTrue(config.load_ai().auto_analyze)
            self.assertEqual("new-ai-key", config.load_ai().api_key)
            self.assertEqual("new-dart", config.load_official().dart_api_key)
            self.assertTrue(config.load_official().dart_enabled)

    def test_slow_nas_load_does_not_block_constructor_or_gui(self):
        with tempfile.TemporaryDirectory() as directory:
            client = _OperationalClient()
            original_load = client.load
            started, release = Event(), Event()

            def delayed_load():
                started.set()
                release.wait(2)
                return original_load()

            client.load = delayed_load
            dialog = NaverNewsSettingsDialog(
                LocalNaverNewsConfig(Path(directory) / "news.dat"), operational_client=client,
            )
            self.assertFalse(started.is_set())
            dialog._load_operational_settings()
            try:
                wait_until(started.is_set)
                ticks = []
                QTimer.singleShot(0, lambda: ticks.append(True))
                wait_until(lambda: bool(ticks))
                dialog._save()
                self.assertIsNone(client.changes)
            finally:
                release.set()
                wait_until(lambda: dialog._operations_worker is None)

    def test_connection_section_with_nas_preserves_hidden_operational_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            config = LocalNaverNewsConfig(Path(directory) / "news.dat")
            config.save(NaverNewsCredentials("client", "secret"),
                        ai=NewsAISettings("gemini", "key", "gemini-3.5-flash-lite", 500))
            client = _OperationalClient()
            dialog = NaverNewsSettingsDialog(config, section="connections", operational_client=client)
            dialog._load_operational_settings()
            wait_until(lambda: dialog._operations_worker is None)
            dialog._client_id.setText("changed-client")
            dialog._save()
            wait_until(lambda: dialog._operations_worker is None)
            self.assertEqual({}, client.changes)
            self.assertEqual("changed-client", config.load().client_id)


if __name__ == "__main__":
    unittest.main()
