from __future__ import annotations

import tempfile
import unittest
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope
from kiwoom_monitor.infrastructure.persistence.entry_snapshot_writer import EntrySnapshotWriter
from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository
from kiwoom_monitor.infrastructure.persistence.journal_snapshot_repository import (
    TradeEntrySnapshot,
    scoped_snapshot_execution_key,
)


class EntrySnapshotWriterTests(unittest.TestCase):
    def test_scoped_snapshot_and_enrichment_use_same_explicit_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite3"
            scope = AccountScope(
                "kiwoom", AccountEnvironment.REAL, str(uuid.uuid4()),
            )
            executed_at = datetime(2026, 9, 14, 10, 30)
            snapshot = TradeEntrySnapshot(
                scoped_snapshot_execution_key("source-fill-1", scope),
                "order-1", "005930", "삼성전자", "매수", executed_at,
                70_500, 1, "KRX", origin_scope=scope,
            )
            failures: list[str] = []
            writer = EntrySnapshotWriter(
                path,
                investor_loader=lambda _code, _observed_at: {
                    "available": True, "source": "test",
                },
            )
            writer.failed.connect(failures.append)
            writer.start()
            writer.enqueue(snapshot)
            writer.requestInterruption()

            self.assertTrue(writer.wait(5_000))
            stored = JournalRepository(path).load_entry_snapshots(
                "005930", executed_at, executed_at + timedelta(seconds=1), scope,
            )
            self.assertEqual([], failures)
            self.assertEqual(1, len(stored))
            self.assertEqual("realtime_enriched", stored[0].capture_state)
            self.assertEqual(scope, stored[0].origin_scope)


if __name__ == "__main__":
    unittest.main()
