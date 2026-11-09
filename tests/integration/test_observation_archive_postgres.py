"""Observation archive native receipts against an explicitly owned PostgreSQL fixture."""
from __future__ import annotations

import asyncio
import os
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from kiwoom_monitor.central_server.database import PostgresQueryStore, SQLiteQueryStore
from kiwoom_monitor.central_server.program_flow_archive import ProgramFlowArchive
from kiwoom_monitor.domain.observation_archive import (
    ObservationArchiveBatch, ObservationArchiveConflict, POSTGRES_ARCHIVE_DURABILITY,
)
from kiwoom_monitor.infrastructure.persistence.postgres_test_target import explicit_dedicated_url
from tests.integration.postgres_test_support import isolated_observation_schema
from tests.unit.test_observation_archive import CUT, END, STREAM, fixture_batch


class PostgresObservationArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = os.environ.get("KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL", "")
        if not url:
            raise RuntimeError("explicit dedicated diagnostic PostgreSQL URL is required; no fallback")
        cls.test_url = explicit_dedicated_url(url)

    def setUp(self):
        self.dsn = self.enterContext(isolated_observation_schema(self.test_url))
        self.store = self.new_store(CUT)
        self.store.initialize()

    def new_store(self, now):
        store = PostgresQueryStore(self.dsn, now_provider=lambda: now)
        self.addCleanup(store.close)
        return store

    @staticmethod
    def accept(store, batch):
        return store.accept_observation_archive(batch.source_stream, batch.raw, batch.content_sha256)

    def test_closed_pc_program_flow_stores_the_same_bytes_after_postgres_reopen(self):
        root = Path(self.enterContext(TemporaryDirectory()))
        source = SQLiteQueryStore(root / "pc.sqlite3", now_provider=lambda: CUT.replace(hour=14, minute=0))
        self.addCleanup(source.close)
        source.initialize()
        source.save_dataset_snapshot("program_flow", "005930", "20261026:REALTIME:140000", {
            "market": "KRX", "rows": [{"trade_time": "140000", "source": "kiwoom_realtime_0w"}],
        })
        day = CUT.date().isoformat()
        archive = ProgramFlowArchive(source, root / "outbox", root / "unused.json", STREAM)
        asyncio.run(archive.freeze_day(day, CUT.replace(hour=20, minute=5)))
        [batch_path] = (root / "outbox" / day).glob("*.batch")
        value = ObservationArchiveBatch.parse(batch_path.read_bytes())
        self.store.prepare_observation_archive()
        first = self.accept(self.store, value)
        reopened = self.new_store(END)
        self.assertEqual(("program_flow", 1, POSTGRES_ARCHIVE_DURABILITY),
                         (first["kind"], first["revision_count"], first["durability"]))
        self.assertEqual(first, self.accept(reopened, value))
        self.assertEqual({"receipt": first, "batch": value.document()},
                         reopened.load_observation_archive(STREAM, value.batch_id, include_body=True))
        self.assertEqual([], reopened.load_dataset_snapshots("program_flow", "005930"))
        self.assertEqual(1, len(source.load_dataset_snapshots("program_flow", "005930")))

    def test_sync_commit_and_rollback_then_commit_ack_loss_keeps_original_body_and_receipt(self):
        import psycopg
        from psycopg.conninfo import conninfo_to_dict

        with self.store._connect() as connection:
            self.assertIsNone(connection.execute("SELECT to_regclass('central_observation_archive_batches')").fetchone()[0])
        self.store.prepare_observation_archive()
        self.store.prepare_observation_archive()
        attempts, connections = [], []
        options = conninfo_to_dict(self.dsn)["options"] + " -c synchronous_commit=off"

        class AckFault(psycopg.Connection):
            def commit(connection):
                attempts.append((connection.execute("SHOW synchronous_commit").fetchone()[0],
                                 connection.execute("SHOW fsync").fetchone()[0],
                                 connection.execute("SELECT count(*) FROM central_observation_archive_batches").fetchone()[0]))
                if len(attempts) == 1:
                    raise OSError("fixture disconnect before native commit")
                super().commit()
                raise OSError("fixture disconnect after native commit")

        def connect():
            connection = AckFault.connect(self.dsn, options=options)
            connections.append(connection)
            return connection

        value = fixture_batch()
        with patch.object(self.store, "_connect", connect), self.assertRaisesRegex(OSError, "before native commit"):
            self.accept(self.store, value)
        self.assertIsNone(self.store.load_observation_archive(STREAM, value.batch_id))
        with patch.object(self.store, "_connect", connect), self.assertRaisesRegex(OSError, "after native commit"):
            self.accept(self.store, value)
        self.assertEqual([("on", "on", 1), ("on", "on", 1)], attempts)
        self.assertTrue(all(connection.closed for connection in connections))
        restarted = self.new_store(END)
        restarted.prepare_observation_archive()
        receipt = restarted.load_observation_archive(STREAM, value.batch_id)
        self.assertEqual((CUT.isoformat(), 1, 17, POSTGRES_ARCHIVE_DURABILITY),
                         (receipt["accepted_at"], receipt["receipt_sequence"],
                          receipt["first_source_accepted_sequence"], receipt["durability"]))
        self.assertEqual(receipt, self.accept(restarted, value))
        with self.assertRaises(ObservationArchiveConflict):
            self.accept(restarted, fixture_batch(code="000660"))
        self.assertEqual({"receipt": receipt, "batch": value.document()},
                         restarted.load_observation_archive(STREAM, value.batch_id, include_body=True))
        with restarted._connect() as connection:
            self.assertEqual([(value.raw,)], connection.execute("SELECT batch_body FROM central_observation_archive_batches").fetchall())
            self.assertEqual(0, connection.execute("SELECT count(*) FROM central_observation_revisions").fetchone()[0])

    def test_same_stream_concurrent_batches_wait_for_commit_then_append_without_reusing_sequence(self):
        import psycopg

        self.store.prepare_observation_archive()
        peer = self.new_store(END)
        ready, release, peer_connected = (threading.Event() for _ in range(3))
        peer_connections = []

        class PausedCommit(psycopg.Connection):
            def commit(connection):
                ready.set()
                if not release.wait(10):
                    raise AssertionError("fixture did not release the winner's commit")
                return super().commit()

        def connect_peer():
            connection = psycopg.connect(self.dsn)
            peer_connections.append(connection)
            peer_connected.set()
            return connection

        first_batch = fixture_batch()
        next_batch = fixture_batch("e922c3e3-0a79-4044-8c46-d8e3c96c93f0")
        with patch.object(self.store, "_connect", lambda: PausedCommit.connect(self.dsn)), \
                patch.object(peer, "_connect", connect_peer), ThreadPoolExecutor(max_workers=2) as workers:
            first = workers.submit(self.accept, self.store, first_batch)
            try:
                self.assertTrue(ready.wait(5))
                following = workers.submit(self.accept, peer, next_batch)
                self.assertTrue(peer_connected.wait(5))
                deadline = time.monotonic() + 5
                blocked = False
                with psycopg.connect(self.test_url, autocommit=True) as observer:
                    while time.monotonic() < deadline:
                        row = observer.execute("SELECT wait_event FROM pg_stat_activity WHERE pid=%s",
                                               (peer_connections[0].info.backend_pid,)).fetchone()
                        if row == ("advisory",):
                            blocked = True
                            break
                        time.sleep(0.01)
                self.assertTrue(blocked, "peer never reached the native stream lock")
                self.assertFalse(following.done())
            finally:
                release.set()
            self.assertEqual((1, 2), (first.result(timeout=10)["receipt_sequence"],
                                     following.result(timeout=10)["receipt_sequence"]))
        self.assertTrue(peer_connections[0].closed)
