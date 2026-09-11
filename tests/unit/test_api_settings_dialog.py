from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from kiwoom_monitor.infrastructure.central_server_config import DataSourceConfig, DataSourceSettings
from kiwoom_monitor.infrastructure.naver_news import (
    LocalNaverNewsConfig,
    NaverNewsCredentials,
    NewsAISettings,
    OfficialNewsSettings,
)
from kiwoom_monitor.presentation.main_window import ApiSettingsDialog


class ApiSettingsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_personal_server_failover_can_be_toggled_and_saved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            api_path = Path(directory) / "api.env"
            source_config = DataSourceConfig(api_path.with_name("data_source.json"))
            source_config.save(DataSourceSettings(
                "personal_server", "https://nas.example.test", "token", False,
            ))
            dialog = ApiSettingsDialog(api_path, section="nas")
            self.assertTrue(dialog._local_fallback.isEnabled())
            dialog._local_fallback.setChecked(True)
            dialog._parallel_validation.setChecked(True)
            dialog._mock_app_key.setText("local-key")
            dialog._mock_secret_key.setText("local-secret")
            dialog._validate_and_accept()
            self.assertTrue(source_config.load().local_fallback_enabled)
            self.assertTrue(source_config.load().parallel_validation_enabled)

    def test_direct_mode_does_not_offer_failover(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dialog = ApiSettingsDialog(Path(directory) / "api.env", section="nas")
            self.assertFalse(dialog._local_fallback.isEnabled())
            self.assertFalse(dialog.data_source_values.local_fallback_enabled)

    def test_local_server_mode_fills_safe_local_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dialog = ApiSettingsDialog(Path(directory) / "api.env", section="nas")
            dialog._data_source_mode.setCurrentIndex(dialog._data_source_mode.findData("local_server"))
            values = dialog.data_source_values
            self.assertEqual("http://127.0.0.1:8787", values.server_url)
            self.assertGreaterEqual(len(values.access_token), 32)
            self.assertFalse(dialog._local_fallback.isEnabled())

    def test_personal_server_mode_exposes_server_and_failover_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dialog = ApiSettingsDialog(Path(directory) / "api.env", section="nas")
            dialog._data_source_mode.setCurrentIndex(dialog._data_source_mode.findData("personal_server"))
            dialog._server_url.setText("https://nas.example.test/")
            dialog._server_token.setText("server-token")
            dialog._local_fallback.setChecked(True)
            self.assertTrue(dialog._server_url.isEnabled())
            self.assertTrue(dialog._local_fallback.isEnabled())
            self.assertEqual("https://nas.example.test", dialog.data_source_values.server_url.rstrip("/"))

    def test_nas_ai_provider_offers_recommended_models(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dialog = ApiSettingsDialog(Path(directory) / "api.env", section="nas")
            dialog._nas_ai_provider.setCurrentIndex(dialog._nas_ai_provider.findData("gemini"))
            labels = tuple(dialog._nas_ai_model.itemText(index) for index in range(dialog._nas_ai_model.count()))
            self.assertTrue(any("추천" in label for label in labels))
            self.assertEqual("gemini-3.5-flash-lite", dialog._nas_ai_model.currentData())

    def test_loaded_nas_operations_are_mirrored_to_local_news_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api_path = root / "api.env"
            DataSourceConfig(root / "data_source.json").save(DataSourceSettings(
                "personal_server", "https://nas.example.test", "token",
            ))
            news = LocalNaverNewsConfig(root / "naver_news.dat")
            news.save(
                NaverNewsCredentials("id", "secret"),
                ai=NewsAISettings("openai", "ai-secret", "old", 10),
                official=OfficialNewsSettings("dart-secret", False),
            )
            dialog = ApiSettingsDialog(api_path, section="nas")
            dialog._nas_request = lambda *_args, **_kwargs: {
                "ai_provider": "gemini", "ai_model": "gemini-3.5-flash-lite",
                "ai_daily_limit": 321, "news_refresh_seconds": 600, "dart_enabled": True,
            }

            dialog._load_nas_operational_settings()

            self.assertEqual("gemini", news.load_ai().provider)
            self.assertEqual(321, news.load_ai().daily_limit)
            self.assertEqual("ai-secret", news.load_ai().api_key)
            self.assertTrue(news.load_official().dart_enabled)
            self.assertEqual("dart-secret", news.load_official().dart_api_key)


if __name__ == "__main__":
    unittest.main()
