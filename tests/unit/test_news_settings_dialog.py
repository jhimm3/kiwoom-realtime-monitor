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


class _OperationalClient:
    def __init__(self) -> None:
        self.values: dict[str, object] = {
            "ai_provider": "gemini",
            "ai_model": "gemini-3.5-flash-lite",
            "ai_daily_limit": 500,
            "news_refresh_seconds": 300,
            "dart_enabled": True,
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
            self.assertEqual("gemini", config.load_ai().provider)
            self.assertEqual("gemini-3.5-flash-lite", dialog._ai_model.currentData())
            self.assertEqual(500, dialog._ai_limit.value())
            self.assertTrue(dialog._dart_enabled.isChecked())

            dialog._ai_limit.setValue(321)
            dialog._dart_enabled.setChecked(False)
            dialog._save()

            self.assertEqual(321, central.changes["ai_daily_limit"] if central.changes else None)
            self.assertFalse(central.changes["dart_enabled"] if central.changes else True)
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


if __name__ == "__main__":
    unittest.main()
