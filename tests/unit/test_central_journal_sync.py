from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from kiwoom_monitor.infrastructure.central_journal_sync import (
    CentralJournalSyncRunner,
    CentralJournalSyncService,
)
from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository


class _Client:
    def __init__(self) -> None:
        self.remote: dict[str, list[dict[str, object]]] = {}

    def load_all(self, collection: str) -> list[dict[str, object]]:
        return list(self.remote.get(collection, []))

    def upsert(self, collection: str, documents: list[dict[str, object]]) -> int:
        self.remote.setdefault(collection, []).extend(documents)
        return len(documents)


class CentralJournalSyncServiceTests(unittest.TestCase):
    def test_deleted_group_override_is_not_restored_by_older_remote_value(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "journal.sqlite3"
            repository = JournalRepository(path)
            client = _Client()
            repository.assign_group(("fill-1",), "manual:one")
            CentralJournalSyncService(client).sync(path)  # type: ignore[arg-type]

            repository.clear_group_assignments(("fill-1",))
            CentralJournalSyncService(client).sync(path)  # type: ignore[arg-type]
            CentralJournalSyncService(client).sync(path)  # type: ignore[arg-type]

            self.assertEqual({}, repository.load_group_overrides())
            states = client.remote["journal_sync_states"]
            self.assertTrue(states[-1]["document"]["is_deleted"])

    def test_runner_rejects_duplicate_schedule_until_current_sync_finishes(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        class Service:
            def sync(self, _path: Path) -> int:
                entered.set()
                release.wait(2)
                return 1

        runner = CentralJournalSyncRunner(Service(), Path("journal.sqlite3"))  # type: ignore[arg-type]

        self.assertTrue(runner.schedule())
        self.assertTrue(entered.wait(1))
        self.assertTrue(runner.is_running)
        self.assertFalse(runner.schedule())
        release.set()
        for _ in range(100):
            if not runner.is_running:
                break
            threading.Event().wait(0.01)
        self.assertFalse(runner.is_running)

    def test_runner_clears_state_and_reports_supported_sync_error(self) -> None:
        received: list[str] = []
        finished = threading.Event()

        class Service:
            def sync(self, _path: Path) -> int:
                try:
                    raise sqlite3.Error("locked")
                finally:
                    finished.set()

        runner = CentralJournalSyncRunner(
            Service(), Path("journal.sqlite3"), lambda error: received.append(str(error)),  # type: ignore[arg-type]
        )

        self.assertTrue(runner.schedule())
        self.assertTrue(finished.wait(1))
        for _ in range(100):
            if not runner.is_running:
                break
            threading.Event().wait(0.01)
        self.assertEqual(["locked"], received)
        self.assertFalse(runner.is_running)

    def test_runner_without_service_does_not_start(self) -> None:
        runner = CentralJournalSyncRunner(None, Path("journal.sqlite3"))

        self.assertFalse(runner.schedule())
        self.assertFalse(runner.is_running)

    def test_newer_local_strategy_setting_is_preserved_and_uploaded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "journal.sqlite3"
            connection = sqlite3.connect(path)
            connection.executescript("""
                CREATE TABLE journal_settings(setting_key TEXT PRIMARY KEY,value_json TEXT,updated_at TEXT);
                INSERT INTO journal_settings VALUES('strategy_packs','[\"local\"]','2026-09-08T10:00:00');
            """)
            connection.close()
            client = _Client()
            client.remote["journal_settings"] = [{
                "owner": "default", "key": "strategy_packs",
                "document": {"setting_key": "strategy_packs", "value_json": "[\"old\"]", "updated_at": "2026-09-08T09:00:00"},
            }]

            saved = CentralJournalSyncService(client).sync(path)  # type: ignore[arg-type]

            self.assertEqual(1, saved)
            self.assertEqual('["local"]', client.remote["journal_settings"][-1]["document"]["value_json"])

    def test_newer_remote_setting_is_merged_into_local_database(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "journal.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute("CREATE TABLE journal_settings(setting_key TEXT PRIMARY KEY,value_json TEXT,updated_at TEXT)")
            connection.close()
            client = _Client()
            client.remote["journal_settings"] = [{
                "owner": "default", "key": "personal_trade_rules",
                "document": {"setting_key": "personal_trade_rules", "value_json": "[\"rule\"]", "updated_at": "2026-09-08T10:00:00"},
            }]

            CentralJournalSyncService(client).sync(path)  # type: ignore[arg-type]

            connection = sqlite3.connect(path)
            self.assertEqual('["rule"]', connection.execute(
                "SELECT value_json FROM journal_settings WHERE setting_key='personal_trade_rules'"
            ).fetchone()[0])
            connection.close()

    def test_fills_reviews_and_entry_snapshots_are_uploaded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "journal.sqlite3"
            connection = sqlite3.connect(path)
            connection.executescript("""
                CREATE TABLE trade_fills(order_no TEXT,stock_code TEXT,stock_name TEXT,side TEXT,filled_at TEXT,quantity INTEGER,price INTEGER,order_type TEXT,market TEXT,PRIMARY KEY(order_no,stock_code,filled_at,side));
                INSERT INTO trade_fills VALUES('1','005930','삼성전자','매수','2026-09-08T09:01:00',1,1000,'','KRX');
                CREATE TABLE trade_reviews(group_id TEXT PRIMARY KEY,reason TEXT,review TEXT,tags TEXT,rating TEXT,status TEXT,updated_at TEXT);
                INSERT INTO trade_reviews VALUES('g1','','원칙 준수','','좋음','완료','2026-09-08T10:00:00');
                CREATE TABLE trade_entry_snapshots(execution_key TEXT PRIMARY KEY,captured_at TEXT);
                INSERT INTO trade_entry_snapshots VALUES('e1','2026-09-08T09:01:01');
            """)
            connection.close()
            client = _Client()

            saved = CentralJournalSyncService(client).sync(path)  # type: ignore[arg-type]

            self.assertEqual(3, saved)
            self.assertEqual("1|005930|2026-09-08T09:01:00|매수", client.remote["journal_fills"][0]["key"])
            self.assertEqual("g1", client.remote["journal_reviews"][0]["key"])
            self.assertEqual("e1", client.remote["journal_entry_snapshots"][0]["key"])


if __name__ == "__main__":
    unittest.main()
