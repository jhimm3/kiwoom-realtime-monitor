from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.infrastructure.persistence.journal_backup import JournalBackupService


class JournalBackupTests(unittest.TestCase):
    def test_round_trip_keeps_unassigned_and_scoped_manual_with_v2_v3_immutable_originals(self):
        from datetime import datetime
        from kiwoom_monitor.application.journal_enrichment import journal_analysis_revision
        from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope, LEGACY_ACCOUNT_SCOPE
        from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository
        scope = AccountScope("kiwoom", AccountEnvironment.MOCK, "11111111-1111-4111-8111-111111111111")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, backup, restored = root / "source.db", root / "backup.db", root / "restored.db"
            repository = JournalRepository(source)
            now = datetime(2026, 10, 23, 10)
            repository.save_trade_setup_cycle_override("same", 0, "상따", now=now)
            repository.save_trade_setup_cycle_override("same", 0, "눌림", now=now, account_scope=scope)
            revisions = tuple(journal_analysis_revision("same", "trade_setup", {"version": version}, version,
                {"selected_types": ["돌파"]}, now=now, account_scope=scope) for version in ("trade-analysis/v2", "trade-analysis/v3"))
            for revision in revisions: repository.save_journal_analysis_revision(revision)
            JournalBackupService(source).export_to(backup)
            JournalBackupService(restored).import_from(backup)
            reopened = JournalRepository(restored)
            self.assertEqual({0: "상따"}, reopened.load_trade_setup_cycle_overrides("same", LEGACY_ACCOUNT_SCOPE))
            self.assertEqual({0: "눌림"}, reopened.load_trade_setup_cycle_overrides("same", scope))
            self.assertCountEqual(revisions, reopened.load_journal_analysis_revisions("same", scope))
            for table in ("trade_setup_cycle_overrides", "journal_analysis_revisions", "journal_sync_states"):
                with closing(sqlite3.connect(source)) as before, closing(sqlite3.connect(restored)) as after:
                    self.assertEqual(before.execute(f"SELECT * FROM {table} ORDER BY 1,2").fetchall(), after.execute(f"SELECT * FROM {table} ORDER BY 1,2").fetchall())

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
