from __future__ import annotations
import sqlite3, tempfile, unittest
from contextlib import closing
from pathlib import Path
from datetime import datetime, timedelta
from kiwoom_monitor.domain.time_basis import KST
from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.infrastructure.persistence.column_settings_repository import ColumnSetting, ColumnSettingsRepository

class ColumnSettingsRepositoryTests(unittest.TestCase):
    def test_saves_and_restores_column_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"; Database(path).initialize()
            now = datetime(2026, 10, 18, 9, tzinfo=KST)
            repository = ColumnSettingsRepository(path, now_provider=lambda: now)
            saved = list(repository.list()); saved[0] = ColumnSetting(saved[0].name, False, 2, 123); repository.save(tuple(saved))
            restored = next(item for item in repository.list() if item.name == saved[0].name)
            self.assertFalse(restored.visible); self.assertEqual(123, restored.width)
            self.assertEqual(2, restored.position)
            connection = sqlite3.connect(path)
            self.assertEqual((now.isoformat(),), connection.execute(
                "SELECT updated_at FROM central_column_setting_versions WHERE column_name=?",
                (saved[0].name,),
            ).fetchone())
            connection.close()
            now += timedelta(seconds=1)
            repository.reset()
            restored = next(item for item in repository.list() if item.name == saved[0].name)
            self.assertTrue(restored.visible); self.assertEqual(32, restored.width); self.assertEqual(0, restored.position)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual((now.isoformat(),), connection.execute(
                    "SELECT updated_at FROM central_column_setting_versions WHERE column_name=?",
                    (saved[0].name,),
                ).fetchone())
