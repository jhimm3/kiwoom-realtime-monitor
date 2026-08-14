from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kiwoom_monitor.infrastructure.persistence.database import Database


class SettingsRepositoryTest(unittest.TestCase):
    def test_settings_are_created_and_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Database(Path(temporary_directory) / "monitor.sqlite3")
            database.initialize()
            self.assertEqual(database.settings.get("refresh_interval_seconds"), "30")

            database.settings.set("refresh_interval_seconds", "60")
            self.assertEqual(database.settings.get("refresh_interval_seconds"), "60")


if __name__ == "__main__":
    unittest.main()
