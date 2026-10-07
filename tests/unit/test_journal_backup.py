from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.infrastructure.persistence.journal_backup import JournalBackupService


class JournalBackupTests(unittest.TestCase):
    def test_export_replacement_failure_keeps_existing_sqlite_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / "journal.sqlite3", root / "backup.sqlite3"
            with closing(sqlite3.connect(source)) as connection:
                connection.execute("CREATE TABLE marker(value TEXT)")
                connection.execute("INSERT INTO marker(value) VALUES ('before')")
                connection.commit()
            service = JournalBackupService(source)
            service.export_to(target)
            previous = target.read_bytes()

            with patch("os.replace", side_effect=OSError("replace failed")):
                with self.assertRaises(OSError):
                    service.export_to(target)

            self.assertEqual(previous, target.read_bytes())
            self.assertEqual([], list(root.glob(".backup.sqlite3.*.tmp")))


if __name__ == "__main__":
    unittest.main()
