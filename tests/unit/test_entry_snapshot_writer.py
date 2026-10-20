from __future__ import annotations

import tempfile
import sqlite3
import unittest
import uuid
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope
from kiwoom_monitor.infrastructure.persistence.entry_snapshot_writer import EntrySnapshotWriter
from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository
from kiwoom_monitor.infrastructure.persistence.journal_snapshot_repository import (
    TradeEntrySnapshot,
    scoped_snapshot_execution_key,
)


class EntrySnapshotWriterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_failed_base_and_enriched_writes_keep_exact_payload_until_explicit_retry(self) -> None:
        for stage in ("base", "enriched"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "journal.sqlite3"
                repository = JournalRepository(path)
                scope = AccountScope("kiwoom", AccountEnvironment.MOCK, str(uuid.uuid4()))
                at = datetime(2026, 10, 19, 10, 30)
                snapshot = TradeEntrySnapshot(
                    scoped_snapshot_execution_key("native-031", scope), "order-031", "005930",
                    "삼성전자", "매수", at, 70000, 1, "KRX", origin_scope=scope,
                    market_state={"source": "original"}, orderbook={"bid": 69900},
                    investor_flow={"program_trade": {"available": True, "source": "original-0w"}},
                )
                following = replace(snapshot, execution_key=scoped_snapshot_execution_key("next-031", scope),
                                    stock_code="000660", stock_name="SK하이닉스", investor_flow={})
                condition = "1" if stage == "base" else "NEW.capture_state='realtime_enriched'"
                with closing(sqlite3.connect(path)) as con:
                    con.execute(f"CREATE TRIGGER fail_snapshot BEFORE INSERT ON trade_entry_snapshots "
                                f"WHEN NEW.stock_code='005930' AND {condition} "
                                "BEGIN SELECT RAISE(ABORT,'031 snapshot fault'); END")
                    con.commit()
                loads, saves, failures = [], [], []
                def investor(code, observed):
                    loads.append((code, observed))
                    return {"available": True, "source": "original-investor"} if code == "005930" else {}
                news = ({"title": "original news", "published_at": "2026-10-19T10:29:00+09:00"},)
                def news_loader(_repository, value):
                    return news if value.stock_code == "005930" else ()
                native_save = JournalRepository.save_entry_snapshot
                def save(value, account_scope=None):
                    saves.append(value)
                    return native_save(repository, value, account_scope=account_scope)
                writer = EntrySnapshotWriter(path, investor_loader=investor)
                writer.failed.connect(failures.append)
                try:
                    with patch.object(JournalRepository, "save_entry_snapshot", side_effect=save), \
                            patch("kiwoom_monitor.infrastructure.persistence.entry_snapshot_writer.news_at_execution", side_effect=news_loader):
                        writer.enqueue(snapshot)
                        writer.enqueue(following)
                        writer.start()
                        writer.requestInterruption()
                        self.assertTrue(writer.wait(5000))
                        self.app.processEvents()
                        self.assertTrue(any("031 snapshot fault" in error for error in failures))
                        self.assertTrue(writer.has_pending_work())
                        self.assertEqual(1, writer._queue.qsize())
                        self.assertEqual(5 if stage == "base" else 2, len(saves))
                        self.assertEqual(0 if stage == "base" else 1, len(loads))
                        pending = writer._pending_job
                        self.assertEqual(snapshot if stage == "base" else saves[-1], pending)
                        if stage == "enriched":
                            self.assertEqual(news, pending.news)
                            self.assertEqual("original-investor", pending.investor_flow["source"])
                        with closing(sqlite3.connect(path)) as con:
                            con.execute("DROP TRIGGER fail_snapshot")
                            con.commit()
                        writer.start()
                        writer.requestInterruption()
                        self.assertTrue(writer.wait(5000))
                    self.assertFalse(writer.has_pending_work())
                    self.assertEqual(1, sum(code == "005930" for code, _ in loads))
                    stored = repository.load_entry_snapshots("005930", at, at + timedelta(seconds=1), scope)[0]
                    self.assertEqual(news, stored.news)
                    self.assertEqual("original-0w", stored.investor_flow["program_trade"]["source"])
                    self.assertEqual("original-investor", stored.investor_flow["source"])
                    self.assertEqual(snapshot.origin_scope, stored.origin_scope)
                    self.assertEqual(snapshot.market_state, stored.market_state)
                    self.assertEqual(snapshot.orderbook, stored.orderbook)
                    self.assertEqual(snapshot.execution_key, stored.execution_key)
                    with closing(sqlite3.connect(path)) as con:
                        self.assertEqual(2, con.execute("SELECT COUNT(*) FROM trade_entry_snapshots").fetchone()[0])
                finally:
                    writer.requestInterruption()
                    self.assertTrue(writer.wait(5000))

    def test_optional_backfill_loader_failure_keeps_existing_continue_contract(self) -> None:
        for kind in ("program", "investor"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "journal.sqlite3"
                repository = JournalRepository(path)
                scope = AccountScope("kiwoom", AccountEnvironment.MOCK, str(uuid.uuid4()))
                at = datetime(2026, 10, 19, 10, 30)
                snapshot = TradeEntrySnapshot(scoped_snapshot_execution_key("loader-031", scope), "order-031",
                    "005930", "삼성전자", "매수", at, 70000, 1, "KRX", origin_scope=scope)
                repository.save_entry_snapshot(snapshot, account_scope=scope)
                following = replace(snapshot, execution_key=scoped_snapshot_execution_key("after-loader-031", scope),
                                    stock_code="000660", stock_name="SK하이닉스")
                def unavailable(*_args):
                    raise OSError("optional loader unavailable")
                failures = []
                writer = EntrySnapshotWriter(path, **{f"{kind}_loader": unavailable})
                writer.failed.connect(failures.append)
                writer.enqueue_program_backfill(at.date(), ("005930",)) if kind == "program" else writer.enqueue_investor_backfill(at.date(), ("005930",))
                writer.enqueue(following)
                writer.start()
                writer.requestInterruption()
                self.assertTrue(writer.wait(5000))
                self.app.processEvents()
                self.assertTrue(any("optional loader unavailable" in error for error in failures))
                self.assertFalse(writer.has_pending_work())
                self.assertEqual(1, len(repository.load_entry_snapshots("000660", at, at + timedelta(seconds=1), scope)))

    def test_native_database_prepare_failure_keeps_queue_for_explicit_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory) / "unavailable-parent"
            parent.write_text("fixture blocks directory creation", encoding="utf-8")
            path = parent / "journal.sqlite3"
            scope = AccountScope("kiwoom", AccountEnvironment.MOCK, str(uuid.uuid4()))
            at = datetime(2026, 10, 19, 10, 30)
            snapshot = TradeEntrySnapshot(scoped_snapshot_execution_key("prepare-031", scope), "order-031",
                "005930", "삼성전자", "매수", at, 70000, 1, "KRX", origin_scope=scope,
                theme_ranks={}, investor_flow={}, orderbook={}, market_state={})
            failures = []
            writer = EntrySnapshotWriter(path)
            writer.failed.connect(failures.append)
            writer.enqueue(snapshot)
            writer.start()
            writer.requestInterruption()
            self.assertTrue(writer.wait(5000))
            self.app.processEvents()
            self.assertTrue(any("DB 준비 실패" in error for error in failures))
            self.assertTrue(writer.has_pending_work())
            self.assertEqual(1, writer._queue.qsize())
            parent.unlink()
            parent.mkdir()
            writer.start()
            writer.requestInterruption()
            self.assertTrue(writer.wait(5000))
            self.assertFalse(writer.has_pending_work())
            stored = JournalRepository(path).load_entry_snapshots("005930", at, at + timedelta(seconds=1), scope)
            self.assertEqual((snapshot,), stored)

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
