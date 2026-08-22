from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from kiwoom_monitor.infrastructure.persistence.google_drive_sync import GoogleDriveSyncError, GoogleDriveSyncService


class GoogleDriveSyncServiceTest(unittest.TestCase):
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
