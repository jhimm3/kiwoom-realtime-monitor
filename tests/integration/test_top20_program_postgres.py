"""TOP20 shutdown ownership against an existing, dedicated PostgreSQL schema."""
from __future__ import annotations

import asyncio
import os
import unittest
from datetime import datetime
from urllib.parse import urlsplit
from uuid import uuid4
from unittest.mock import Mock

from kiwoom_monitor.central_server.autonomous_top20 import AutonomousTop20Service
from kiwoom_monitor.central_server.database import PostgresQueryStore
from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
from tests.unit.test_top20_program_shutdown import _ControlledStore, _snapshot


class Top20ProgramPostgresTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        url = os.environ.get("KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL", "")
        if not url:
            self.skipTest("dedicated PostgreSQL test database not configured")
        if urlsplit(url).path.lstrip("/") != "kiwoom_monitor_diagnostic_test":
            raise RuntimeError("refusing TOP20 writes outside the dedicated diagnostic DB")
        self.store = PostgresQueryStore(url)
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT current_database(), to_regclass('central_dataset_snapshots')")
            name, relation = cursor.fetchone()
            if name != "kiwoom_monitor_diagnostic_test" or relation is None:
                raise RuntimeError("dedicated test DB or existing dataset schema is unavailable")
        self.codes = [f"DIAG-TOP20-{uuid4().hex}:{i}" for i in range(3)]
        self.addCleanup(self.store.close)
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM central_dataset_snapshots WHERE kind='program_flow' AND subject=ANY(%s)",
                (self.codes,),
            )
            cursor.execute(
                "SELECT count(*) FROM central_dataset_snapshots WHERE kind='program_flow' AND subject=ANY(%s)",
                (self.codes,),
            )
            self.assertEqual(0, cursor.fetchone()[0])

    async def _run_shutdown(self, failure: str) -> None:
        first, newer, peer = self.codes
        peer_value = _snapshot(peer)
        self.store.save_dataset_snapshots([
            ("program_flow", peer, peer_value["snapshot_key"], peer_value["payload"], None),
        ])
        peer_before = self.store.load_dataset_snapshots("program_flow", peer, 10)
        blocked = _ControlledStore(self.store, failure=failure)
        service = AutonomousTop20Service(
            Mock(), RealtimeHub(), blocked,
            now_provider=lambda: datetime.fromisoformat("2026-10-06T10:01:00+09:00"),
        )
        service._pending_program_snapshots[first] = _snapshot(first)
        service._tasks = [asyncio.create_task(service._index_loop())]
        self.assertTrue(await asyncio.to_thread(blocked.entered.wait, 2))
        service._pending_program_snapshots[newer] = _snapshot(newer, "100101", 77)
        closing = asyncio.create_task(service.close())
        try:
            await asyncio.sleep(0.05)
            self.assertFalse(closing.done())
            # Another service/connection can commit while this one owns a blocked save.
            self.store.save_dataset_snapshots([
                ("program_flow", peer, peer_value["snapshot_key"], peer_value["payload"], None),
            ])
            peer_before = self.store.load_dataset_snapshots("program_flow", peer, 10)
        finally:
            blocked.release.set()
            await closing
        self.assertEqual(2, len(blocked.calls))
        for code, clock, amount in ((first, "100100", 10), (newer, "100101", 77)):
            rows = self.store.load_dataset_snapshots("program_flow", code, 10)
            self.assertEqual(1, len(rows))
            self.assertEqual(_snapshot(code, clock, amount)["payload"], rows[0]["payload"])
        self.assertEqual(peer_before, self.store.load_dataset_snapshots("program_flow", peer, 10))
        self.assertEqual({}, service._pending_program_snapshots)

    async def test_close_drains_commit_ack_loss_and_preserves_independent_peer(self) -> None:
        await self._run_shutdown("after_commit")

    async def test_close_retries_failed_save_and_drains_new_pending(self) -> None:
        await self._run_shutdown("before_commit")


if __name__ == "__main__":
    unittest.main()
