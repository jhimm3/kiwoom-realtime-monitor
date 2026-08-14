from __future__ import annotations
import tempfile, unittest
from pathlib import Path
from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.infrastructure.persistence.column_settings_repository import ColumnSetting, ColumnSettingsRepository

class ColumnSettingsRepositoryTests(unittest.TestCase):
    def test_saves_and_restores_column_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"; Database(path).initialize(); repository = ColumnSettingsRepository(path)
            saved = list(repository.list()); saved[0] = ColumnSetting(saved[0].name, False, 2, 123); repository.save(tuple(saved))
            restored = next(item for item in repository.list() if item.name == saved[0].name)
            self.assertFalse(restored.visible); self.assertEqual(123, restored.width)
