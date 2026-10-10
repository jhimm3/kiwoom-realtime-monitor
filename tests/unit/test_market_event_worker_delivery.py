"""Cohort workers retain native failures and drain admitted work through ACK."""
from __future__ import annotations

import asyncio
from datetime import datetime
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.market_events import MarketEventService
from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
from tests.unit.test_market_events import _Broker


async def until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(.002)


class MarketEventWorkerDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SQLiteQueryStore(Path(self.temp.name) / "workers.sqlite3")
        self.store.initialize()
        self.now = datetime(2026, 10, 8, 23, 59, 59)
        self.broker = _Broker()
        self.service = MarketEventService(self.broker, RealtimeHub(), self.store,
                                          now_provider=lambda: self.now)
        self.service._selected = ("7", "condition")
        self.service._cohort_retry_seconds = .01
        self.service._cohort_drain_warning_seconds = .02
        self.releases = []
        await self.service.start()

    async def asyncTearDown(self):
        for release in self.releases:
            release.set()
        await self.service.close()
        self.store.close()
        self.temp.cleanup()

    def held_retry(self, event_type, *, ack_loss=False):
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        native, attempts = self.store.record_hot_cohort_revision, []

        def save(revision, current):
            if revision["event_type"] != event_type or revision["stock_code"] != "005930":
                return native(revision, current)
            attempts.append(json.loads(json.dumps([revision, current])))
            if len(attempts) == 1:
                if ack_loss:
                    native(revision, current)
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("worker failure fixture not released")
                raise OSError("controlled lost ACK" if ack_loss else "controlled save failure")
            return native(revision, current)

        return patch.object(self.store, "record_hot_cohort_revision", side_effect=save), entered, release, attempts

    async def failure_recovery(self, kind, *, ack_loss=False):
        event_type = "ENTERED" if kind == "signal" else "ELIGIBILITY"
        queue = self.service._signal_queue if kind == "signal" else self.service._metadata_queue
        invocation = self.now
        patcher, entered, release, attempts = self.held_retry(event_type, ack_loss=ack_loss)
        with patcher:
            try:
                self.service._queue_signal("005930", "I", "REAL", "")
                await until(entered.is_set)
                self.assertEqual(1, queue._unfinished_tasks)
                current = self.store.load_hot_cohort(active_only=True)
                if kind == "metadata":
                    self.assertIsNone(self.service._cohort["005930"]["nxt_eligible"],
                                      "RAM eligibility was published before native ACK")
                    self.assertEqual(True if ack_loss else None, current[0]["nxt_eligible"])
                else:
                    self.assertNotIn("005930", self.service._cohort)
                    self.assertEqual(int(ack_loss), len(current))
                # Both retry and a following queued signal must keep their admitted date.
                self.service._queue_signal("000660", "I", "REAL", "")
                self.service._selected = ("8", "next-condition")
                self.now = datetime(2026, 10, 9, 0, 1)
            finally:
                release.set()
            await asyncio.wait_for(self.service._signal_queue.join(), 5)
            await asyncio.wait_for(self.service._metadata_queue.join(), 5)
        self.assertEqual(2, len(attempts), "the failed input was discarded instead of retried")
        self.assertEqual(attempts[0], attempts[1], "retry changed its native input")
        current = next(row for row in self.store.load_hot_cohort(active_only=True)
                       if row["stock_code"] == "005930")
        self.assertTrue(current["nxt_eligible"])
        self.assertEqual(current["last_signal_at"], self.service._cohort["005930"]["last_signal_at"])
        for code in ("005930", "000660"):
            row = self.service._cohort[code]
            self.assertEqual(invocation.timestamp(), row["last_signal_at"])
            self.assertEqual("2026-10-08", row["entry_session"])
            self.assertEqual("condition", row["condition_name"])
        history = self.store.load_market_event_history("cohort", code="005930")
        self.assertEqual(2, len(history))
        self.assertEqual({"ENTERED", "ELIGIBILITY"}, {row["event_type"] for row in history})
        self.assertEqual(0, queue._unfinished_tasks)
        self.assertEqual(1, self.service.condition_status()["cohort_collection"]["save_failures"])

    async def test_signal_failure_retains_input_and_queue_until_native_ack(self):
        await self.failure_recovery("signal")

    async def test_metadata_failure_publishes_eligibility_only_after_native_ack(self):
        await self.failure_recovery("metadata")

    async def test_signal_commit_ack_loss_retries_identical_revision_without_duplicate(self):
        await self.failure_recovery("signal", ack_loss=True)

    async def test_metadata_commit_ack_loss_retries_identical_revision_without_duplicate(self):
        await self.failure_recovery("metadata", ack_loss=True)

    async def test_metadata_lookup_failure_keeps_unfinished_input_for_retry(self):
        native, calls = self.broker.request, []

        async def request(api, *args, **kwargs):
            if api == "ka10100":
                calls.append(api)
                if len(calls) == 1:
                    raise OSError("controlled lookup failure")
            return await native(api, *args, **kwargs)

        with patch.object(self.broker, "request", side_effect=request):
            self.service._queue_signal("005930", "I", "REAL", "")
            await asyncio.wait_for(self.service._signal_queue.join(), 5)
            await asyncio.wait_for(self.service._metadata_queue.join(), 5)
        self.assertEqual(2, len(calls))
        self.assertTrue(self.store.load_hot_cohort(active_only=True)[0]["nxt_eligible"])

    async def test_active_worker_cancellation_retains_failed_head_and_following_signal(self):
        patcher, entered, release, attempts = self.held_retry("ENTERED")
        worker = next(task for task in self.service._tasks if task.get_coro().__name__ == "_signal_loop")
        with patcher:
            try:
                self.service._queue_signal("005930", "I", "REAL", "")
                self.service._queue_signal("000660", "I", "REAL", "")
                await until(entered.is_set)
                worker.cancel()
                await asyncio.sleep(.01)
                worker.cancel()
                self.assertEqual(2, self.service._signal_queue._unfinished_tasks)
                self.assertFalse(worker.done())
            finally:
                release.set()
            await asyncio.wait_for(self.service._signal_queue.join(), 5)
            await asyncio.wait_for(self.service._metadata_queue.join(), 5)
        self.assertEqual(2, len(attempts))
        self.assertEqual({"005930", "000660"}, set(self.service._cohort))

    async def test_persistent_failure_keeps_close_pending_until_database_recovers(self):
        native = self.store.record_hot_cohort_revision
        recovered = asyncio.Event()
        failed = threading.Event()

        def save(revision, current):
            if not recovered.is_set():
                failed.set()
                raise OSError("database unavailable")
            return native(revision, current)

        with patch.object(self.store, "record_hot_cohort_revision", side_effect=save):
            self.service._queue_signal("005930", "I", "REAL", "")
            await until(failed.is_set)
            closing = asyncio.create_task(self.service.close())
            try:
                await asyncio.sleep(.06)
                self.assertFalse(closing.done())
                self.assertEqual(1, self.service._signal_queue._unfinished_tasks)
                status = self.service.condition_status()["cohort_collection"]
                self.assertEqual("OSError", status["last_error_types"]["save"])
                self.assertTrue(status["admission_closed"])
                with self.assertRaisesRegex(RuntimeError, "admission_closed"):
                    self.service._queue_signal("000660", "I", "REAL", "")
            finally:
                recovered.set()
                await asyncio.wait_for(closing, 5)
        self.assertTrue(self.store.load_hot_cohort(active_only=True)[0]["nxt_eligible"])

    async def test_close_drains_metadata_admitted_by_signal_before_start(self):
        service = MarketEventService(self.broker, RealtimeHub(), self.store, now_provider=lambda: self.now)
        await service.record_condition_signal("005930", "I", source="INITIAL")
        self.assertEqual(1, service._metadata_queue._unfinished_tasks)
        closing = asyncio.create_task(service.close())
        try:
            await asyncio.wait_for(asyncio.shield(closing), .5)
        finally:
            # Release a failed reference fixture without abandoning its owned close.
            if not closing.done():
                service._tasks.append(asyncio.create_task(service._metadata_loop()))
            await asyncio.wait_for(closing, 5)
        self.assertTrue(self.store.load_hot_cohort(active_only=True)[0]["nxt_eligible"])
        self.assertEqual(0, service._metadata_queue._unfinished_tasks)

    async def test_cancelled_direct_signal_and_close_drain_later_metadata_native_work(self):
        native = self.store.record_hot_cohort_revision
        entered = {kind: threading.Event() for kind in ("ENTERED", "ELIGIBILITY")}
        released = {kind: threading.Event() for kind in entered}
        self.releases.extend(released.values())
        active = set()

        def save(revision, current):
            kind = revision["event_type"]
            if kind in entered:
                active.add(kind)
                entered[kind].set()
                try:
                    if not released[kind].wait(5):
                        raise TimeoutError("native drain fixture not released")
                    return native(revision, current)
                finally:
                    active.remove(kind)
            return native(revision, current)

        with patch.object(self.store, "record_hot_cohort_revision", side_effect=save):
            waiter = asyncio.create_task(self.service.record_condition_signal("005930", "I", source="REAL"))
            closing = None
            try:
                await until(entered["ENTERED"].is_set)
                waiter.cancel()
                await asyncio.sleep(.01)
                waiter.cancel()
                await asyncio.sleep(.01)
                self.assertFalse(waiter.done(), "cancelled waiter abandoned its native save")
                closing = asyncio.create_task(self.service.close())
                await asyncio.sleep(.05)  # pass the warning threshold, still own the native work
                closing.cancel()
                await asyncio.sleep(.01)
                closing.cancel()
                self.assertFalse(closing.done())
                released["ENTERED"].set()
                await until(entered["ELIGIBILITY"].is_set)
                await asyncio.sleep(.05)
                self.assertFalse(self.service._close_task.done(), "close skipped newly admitted metadata")
            finally:
                for release in released.values():
                    release.set()
                await asyncio.gather(waiter, *([closing] if closing else []), return_exceptions=True)
                await until(lambda: not active)
        self.assertTrue(waiter.cancelled())
        self.assertTrue(closing.cancelled())
        await self.service.close()
        self.assertEqual(set(), active)
        self.assertEqual(0, self.service._signal_queue._unfinished_tasks)
        self.assertEqual(0, self.service._metadata_queue._unfinished_tasks)
        self.assertTrue(self.store.load_hot_cohort(active_only=True)[0]["nxt_eligible"])
