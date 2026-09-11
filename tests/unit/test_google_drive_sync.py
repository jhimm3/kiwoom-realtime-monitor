from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.infrastructure.persistence.google_drive_sync import GoogleDriveSyncError, GoogleDriveSyncService


class GoogleDriveSyncServiceTest(unittest.TestCase):
    def test_every_target_includes_news_ai_backup(self) -> None:
        class Request:
            def execute(self) -> dict[str, object]: return {}

        class Files:
            def __init__(self) -> None: self.names: list[str] = []
            def create(self, *, body, media_body, fields):
                self.names.append(str(body["name"])); return Request()
            def update(self, **_kwargs): return Request()

        class Drive:
            def __init__(self) -> None: self.api = Files()
            def files(self): return self.api

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def write_backup(_service, path, *_args, **_kwargs) -> None:
                path.write_text("{}", encoding="utf-8")

            for target in ("settings", "themes", "both"):
                with self.subTest(target=target):
                    drive = Drive()
                    service = GoogleDriveSyncService(root / "monitor.sqlite3", root / "news.sqlite3")
                    with patch.object(service, "_drive_service", return_value=drive), \
                            patch.object(service, "_ensure_sync_folder", return_value="folder"), \
                            patch.object(service, "_find_remote_file", return_value=None), \
                            patch.object(service, "_media_upload", side_effect=lambda content: content), \
                            patch("kiwoom_monitor.infrastructure.persistence.google_drive_sync.SettingsBackupService.export_to", write_backup), \
                            patch("kiwoom_monitor.infrastructure.persistence.google_drive_sync.NewsAIBackupService.export_to", write_backup):
                        service.upload(target=target)

                    self.assertIn(GoogleDriveSyncService.NEWS_AI_REMOTE_NAME, drive.api.names)
                    self.assertEqual(
                        target in {"settings", "both"},
                        GoogleDriveSyncService.SETTINGS_REMOTE_NAME in drive.api.names,
                    )
                    self.assertEqual(
                        target in {"themes", "both"},
                        GoogleDriveSyncService.THEMES_REMOTE_NAME in drive.api.names,
                    )

    def test_only_machine_specific_settings_are_excluded_from_drive_sync(self) -> None:
        self.assertIn("window_width", GoogleDriveSyncService.LOCAL_ONLY_SETTINGS)
        self.assertIn("window_height", GoogleDriveSyncService.LOCAL_ONLY_SETTINGS)
        self.assertIn("window_x", GoogleDriveSyncService.LOCAL_ONLY_SETTINGS)
        self.assertIn("settings_dialog_width", GoogleDriveSyncService.LOCAL_ONLY_SETTINGS)
        self.assertIn("theme_manager_stock_column_width", GoogleDriveSyncService.LOCAL_ONLY_SETTINGS)
        self.assertIn("google_drive_auto_download", GoogleDriveSyncService.LOCAL_ONLY_SETTINGS)
        self.assertIn("google_drive_auto_upload", GoogleDriveSyncService.LOCAL_ONLY_SETTINGS)
        self.assertIn("google_drive_auto_upload_on_exit", GoogleDriveSyncService.LOCAL_ONLY_SETTINGS)
        self.assertIn("google_drive_sync_target", GoogleDriveSyncService.LOCAL_ONLY_SETTINGS)
        self.assertIn("near_high_sound_fire", GoogleDriveSyncService.LOCAL_ONLY_SETTINGS)
        self.assertNotIn("trade_value_1m_alert_eok", GoogleDriveSyncService.LOCAL_ONLY_SETTINGS)
        self.assertNotIn("theme_active_profile", GoogleDriveSyncService.LOCAL_ONLY_SETTINGS)
        for prefix in ("theme_new_import", "theme_text_import", "theme_excel_import", "theme_image_import"):
            self.assertNotIn(f"{prefix}_custom_separators", GoogleDriveSyncService.LOCAL_ONLY_SETTINGS)
            self.assertNotIn(f"{prefix}_exclusions", GoogleDriveSyncService.LOCAL_ONLY_SETTINGS)

    def test_imports_only_desktop_oauth_client_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = GoogleDriveSyncService(root / "data" / "monitor.sqlite3")
            source = root / "desktop.json"
            source.write_text(json.dumps({"installed": {"client_id": "test", "client_secret": "secret"}}), encoding="utf-8")

            service.import_client_file(source)

            self.assertTrue(service.configured)
            self.assertFalse(service.connected)
            self.assertEqual("test", json.loads((root / "data" / "google_drive_client.json").read_text(encoding="utf-8"))["installed"]["client_id"])

    def test_rejects_non_desktop_oauth_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = GoogleDriveSyncService(root / "data" / "monitor.sqlite3")
            source = root / "web.json"
            source.write_text(json.dumps({"web": {"client_id": "test"}}), encoding="utf-8")

            with self.assertRaises(GoogleDriveSyncError):
                service.import_client_file(source)
