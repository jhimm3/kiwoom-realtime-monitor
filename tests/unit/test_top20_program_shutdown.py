from __future__ import annotations

import asyncio
import tempfile
import threading
import unittest
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

from kiwoom_monitor.central_server.autonomous_top20 import AutonomousTop20Service
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import ProgramTradeTick


def _snapshot(code: str = "005930", clock: str = "100100", amount: int = 10) -> dict:
    return {
        "subject": code,
        "snapshot_key": f"20261006:REALTIME:{clock}",
        "payload": {"market": "KRX", "rows": [{
            "trade_time": clock, "net_buy_amount_million_won": amount,
        }]},
    }


class _ControlledStore:
    """Use real snapshot writes with a deterministic blocked/failing first call."""

    def __init__(self, store: SQLiteQueryStore, *, failure: str = "") -> None:
        self.store = store
        self.failure = failure
        self.entered = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        self.calls: list[list] = []

    def save_dataset_snapshots(self, values: list) -> None:
        self.calls.append(values)
        if len(self.calls) == 1:
            self.entered.set()
            try:
                if not self.release.wait(5):
                    raise TimeoutError("test did not release blocked storage")
                if self.failure == "before_commit":
                    raise OSError("injected save failure")
                self.store.save_dataset_snapshots(values)
                if self.failure == "after_commit":
                    raise OSError("injected COMMIT acknowledgement loss")
            finally:
                self.finished.set()
        else:
            self.store.save_dataset_snapshots(values)


class Top20ProgramShutdownTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = SQLiteQueryStore(Path(self.directory.name) / "monitor.sqlite3")
        self.store.initialize()
        self.addCleanup(self.store.close)

    def _service(self, store=None) -> AutonomousTop20Service:
        return AutonomousTop20Service(
            Mock(), RealtimeHub(), store or self.store,
            now_provider=lambda: datetime.fromisoformat("2026-10-06T10:01:00+09:00"),
        )

    async def test_close_flushes_pending_program_snapshot_without_periodic_tick(self) -> None:
        service = self._service()
        service._pending_program_snapshots["005930"] = _snapshot()

        await service.close()

        rows = self.store.load_dataset_snapshots("program_flow", "005930", 10)
        self.assertEqual(["20261006:REALTIME:100100"], [r["snapshot_key"] for r in rows])
        self.assertEqual({}, service._pending_program_snapshots)

    async def test_close_waits_for_actual_storage_and_flushes_new_pending(self) -> None:
        blocked = _ControlledStore(self.store)
        service = self._service(blocked)
        service._pending_program_snapshots["005930"] = _snapshot()
        service._tasks = [asyncio.create_task(service._index_loop())]
        self.assertTrue(await asyncio.to_thread(blocked.entered.wait, 2))
        service._pending_program_snapshots["000660"] = _snapshot("000660")
        closing = asyncio.create_task(service.close())
        try:
            await asyncio.sleep(0.05)
            self.assertFalse(closing.done(), "close returned while the DB thread was still writing")
        finally:
            blocked.release.set()
            await closing
            self.assertTrue(await asyncio.to_thread(blocked.finished.wait, 2))

        self.assertEqual(2, len(blocked.calls))
        for code in ("005930", "000660"):
            self.assertEqual(1, len(self.store.load_dataset_snapshots("program_flow", code, 10)))

    async def test_close_retries_lost_commit_ack_without_duplicate_snapshot(self) -> None:
        blocked = _ControlledStore(self.store, failure="after_commit")
        service = self._service(blocked)
        service._pending_program_snapshots["005930"] = _snapshot()
        service._tasks = [asyncio.create_task(service._index_loop())]
        self.assertTrue(await asyncio.to_thread(blocked.entered.wait, 2))
        closing = asyncio.create_task(service.close())
        await asyncio.sleep(0.05)
        blocked.release.set()
        await closing
        self.assertTrue(await asyncio.to_thread(blocked.finished.wait, 2))

        self.assertEqual(2, len(blocked.calls))
        rows = self.store.load_dataset_snapshots("program_flow", "005930", 10)
        self.assertEqual(1, len(rows))
        self.assertEqual(_snapshot()["payload"], rows[0]["payload"])
        self.assertEqual({}, service._pending_program_snapshots)

    async def test_cancelled_flush_failure_keeps_newer_pending_and_peer(self) -> None:
        blocked = _ControlledStore(self.store, failure="before_commit")
        service = self._service(blocked)
        service._pending_program_snapshots = {
            "005930": _snapshot(), "000660": _snapshot("000660"),
        }
        flushing = asyncio.create_task(service._flush_program_snapshots())
        self.assertTrue(await asyncio.to_thread(blocked.entered.wait, 2))
        flushing.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await flushing
        service._pending_program_snapshots["005930"] = _snapshot(clock="100101", amount=77)
        closing = asyncio.create_task(service.close())
        try:
            await asyncio.sleep(0.05)
            self.assertFalse(closing.done())
            self.assertEqual(1, len(blocked.calls), "retry overlapped an unfinished DB write")
        finally:
            blocked.release.set()
            await closing

        rows = self.store.load_dataset_snapshots("program_flow", "005930", 10)
        self.assertEqual(["20261006:REALTIME:100101"], [r["snapshot_key"] for r in rows])
        self.assertEqual(77, rows[0]["payload"]["rows"][0]["net_buy_amount_million_won"])
        self.assertEqual(1, len(self.store.load_dataset_snapshots("program_flow", "000660", 10)))
        self.assertEqual({}, service._pending_program_snapshots)

    async def test_cancelled_close_waiter_does_not_abandon_final_flush(self) -> None:
        blocked = _ControlledStore(self.store)
        service = self._service(blocked)
        service._pending_program_snapshots["005930"] = _snapshot()
        closing = asyncio.create_task(service.close())
        self.assertTrue(await asyncio.to_thread(blocked.entered.wait, 2))
        closing.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await closing
        joined = asyncio.create_task(service.close())
        try:
            await asyncio.sleep(0.05)
            self.assertFalse(joined.done())
        finally:
            blocked.release.set()
            await joined
        self.assertEqual(1, len(blocked.calls))
        self.assertEqual(1, len(self.store.load_dataset_snapshots("program_flow", "005930", 10)))

    async def test_final_failure_is_explicit_retains_pending_and_allows_retry(self) -> None:
        service = self._service()
        service._pending_program_snapshots["005930"] = _snapshot()
        original = self.store.save_dataset_snapshots
        self.store.save_dataset_snapshots = Mock(side_effect=OSError("database unavailable"))
        with self.assertRaisesRegex(OSError, "database unavailable"):
            await service.close()
        self.assertEqual(_snapshot(), service._pending_program_snapshots["005930"])
        self.assertEqual([], self.store.load_dataset_snapshots("program_flow", "005930", 10))
        self.store.save_dataset_snapshots = original
        await service.close()
        await service.close()
        self.assertEqual(1, len(self.store.load_dataset_snapshots("program_flow", "005930", 10)))

    async def test_event_loop_program_input_is_drained_after_producer_stop(self) -> None:
        service = self._service()
        service._subscriber = service._hub.connect()
        service._tasks = [asyncio.create_task(service._event_loop())]
        service._hub.publish({
            "type": "program_trade",
            "payload": asdict(ProgramTradeTick("005930", "100100", 3, 1, 10, 2)),
        }, code="005930")

        async def wait_for_pending() -> None:
            while not service._pending_program_snapshots:
                await asyncio.sleep(0)

        await asyncio.wait_for(wait_for_pending(), 2)
        await service.close()
        self.assertEqual(0, service._hub.client_count)
        self.assertIsNone(service._subscriber)
        self.assertEqual([], service._tasks)
        rows = self.store.load_dataset_snapshots("program_flow", "005930", 10)
        self.assertEqual(1, len(rows))
        self.assertEqual("kiwoom_realtime_0w", rows[0]["payload"]["rows"][0]["source"])


if __name__ == "__main__":
    unittest.main()
