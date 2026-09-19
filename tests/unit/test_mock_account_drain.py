from __future__ import annotations

import asyncio
import tempfile
import threading
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.application.order_lifecycle import OrderLifecycle
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.execution_runtime import ExecutionRuntime
from kiwoom_monitor.central_server.mock_account_monitor import MockAccountMonitor, MockAccountRealtimeCollector
from kiwoom_monitor.domain.order_contract import AccountSnapshot, BrokerSubmission, OrderIntent, OrderSide, OrderState, OrderType, order_event
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import MockAccountRecovery
from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository


NOW = datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc)


class MockAccountDrainTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.releases = []
        self.store = SQLiteQueryStore(Path(":memory:")); self.store.initialize()
        self.repository = ExecutionRepository(self.store)
        self.runtime = ExecutionRuntime(OrderLifecycle(self.repository, None), self.repository,
            account_ref="account-1", run_id="run-1", owner_token="owner-1")
        self.recovery = MockAccountRecovery(AccountSnapshot("account-1", 1_000_000, 0, {}, NOW), ())
        class Reader:
            async def read(inner): return self.recovery
        self.monitor = MockAccountMonitor(Reader(), self.runtime, self.repository, now_provider=lambda: NOW)

    async def asyncTearDown(self):
        for release in self.releases: release.set()
        await self.monitor.close()
        self.store.close()

    async def started(self):
        await self.monitor.start()
        await asyncio.wait_for(self.monitor._queue.join(), 2)

    async def test_close_waits_for_recovery_read_and_rejects_late_events(self):
        entered, release = asyncio.Event(), asyncio.Event()
        self.releases.append(release)
        async def read(): entered.set(); await release.wait(); return self.recovery
        self.monitor._reader.read = read
        await self.monitor.start(); await entered.wait()
        closing = asyncio.create_task(self.monitor.close()); await asyncio.sleep(0)
        self.assertFalse(closing.done())
        count = self.monitor._queue.qsize()
        self.monitor.handle_realtime("connected", None)
        self.assertEqual(count, self.monitor._queue.qsize())
        with self.assertRaisesRegex(RuntimeError, "CLOSED"): await self.monitor.refresh_account()
        release.set(); await closing
        self.assertEqual(0, self.monitor._queue.qsize())
        self.assertTrue(self.repository.claim_runtime("mock", "account-1", "run-1", "owner-2", datetime.now(timezone.utc)))
        with self.assertRaisesRegex(RuntimeError, "CLOSED"): await self.monitor.start()

    async def test_close_waiter_cancellation_does_not_finish_database_work_early(self):
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        original = self.repository.save_account_snapshot
        def save(*args):
            entered.set()
            if not release.wait(3): raise RuntimeError("test database was not released")
            return original(*args)
        with patch.object(self.repository, "save_account_snapshot", side_effect=save):
            await self.monitor.start()
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            closing = asyncio.create_task(self.monitor.close()); await asyncio.sleep(0)
            closing.cancel()
            with self.assertRaises(asyncio.CancelledError): await closing
            self.assertFalse(self.monitor._close_task.done())
            release.set(); await self.monitor.close()
        self.assertTrue(self.runtime._retired)

    async def test_cancelled_direct_refresh_is_owned_until_database_completion(self):
        await self.started()
        entered, release = asyncio.Event(), asyncio.Event()
        self.releases.append(release)
        async def read(): entered.set(); await release.wait(); return self.recovery
        self.monitor._reader.read = read
        refreshing = asyncio.create_task(self.monitor.refresh_account()); await entered.wait()
        refreshing.cancel()
        with self.assertRaises(asyncio.CancelledError): await refreshing
        closing = asyncio.create_task(self.monitor.close()); await asyncio.sleep(0)
        self.assertFalse(closing.done())
        release.set(); await closing
        self.assertFalse(self.monitor._work)

    async def test_heartbeat_keeps_running_during_recovery_read(self):
        entered, release, renewed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        self.releases.append(release)
        async def read(): entered.set(); await release.wait(); return self.recovery
        self.monitor._reader.read = read
        self.monitor._heartbeat_seconds = 0.01
        original = self.runtime.heartbeat
        def heartbeat():
            original()
            asyncio.run_coroutine_threadsafe(signal(), loop)
        async def signal(): renewed.set()
        loop = asyncio.get_running_loop()
        with patch.object(self.runtime, "heartbeat", side_effect=heartbeat):
            await self.monitor.start(); await entered.wait()
            try: await asyncio.wait_for(renewed.wait(), 1)
            finally: release.set()
            await self.monitor.close()

    async def test_cancelled_start_is_owned_and_closed_before_lease_release(self):
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        original = self.runtime.start
        def start(**kwargs):
            entered.set()
            if not release.wait(3): raise RuntimeError("test start was not released")
            original(**kwargs)
        with patch.object(self.runtime, "start", side_effect=start):
            starting = asyncio.create_task(self.monitor.start())
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            starting.cancel()
            with self.assertRaises(asyncio.CancelledError): await starting
            closing = asyncio.create_task(self.monitor.close()); await asyncio.sleep(0)
            self.assertFalse(closing.done())
            release.set(); await closing
        self.assertTrue(self.runtime._retired)
        self.assertTrue(self.repository.claim_runtime("mock", "account-1", "run-1", "owner-2", datetime.now(timezone.utc)))


class MockRealtimeDrainTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.releases, self.collectors = [], []

    async def asyncTearDown(self):
        for release in self.releases: release.set()
        for collector in self.collectors: await collector.close()

    async def test_close_waits_for_actual_token_thread_and_drops_late_delivery(self):
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        delivered = []
        def token():
            entered.set()
            if not release.wait(3): raise RuntimeError("test token was not released")
            return ""  # No real credentials and no socket should be opened.
        collector = MockAccountRealtimeCollector(token, lambda *args: delivered.append(args), lambda: NOW + timedelta(hours=9))
        self.collectors.append(collector)
        with patch("kiwoom_monitor.central_server.mock_account_monitor.connect") as connect:
            await collector.start()
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            closing = asyncio.create_task(collector.close()); await asyncio.sleep(0)
            closing.cancel()
            with self.assertRaises(asyncio.CancelledError): await closing
            self.assertFalse(collector._close_task.done())
            collector._deliver("connected", None)
            self.assertEqual([], delivered)
            with self.assertRaisesRegex(RuntimeError, "IN_PROGRESS"): await collector.start()
            release.set(); await collector.close()
            connect.assert_not_called()

    async def test_close_waits_for_websocket_context_exit(self):
        entered, exited, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        self.releases.append(release)
        class Socket:
            calls = 0
            async def send(self, _): pass
            async def recv(self):
                self.calls += 1
                if self.calls == 1: return '{"return_code":0}'
                entered.set(); await asyncio.Event().wait()
        class Connection:
            async def __aenter__(self): return Socket()
            async def __aexit__(self, *_): exited.set(); await release.wait()
        collector = MockAccountRealtimeCollector(lambda: "", lambda *_: None, lambda: NOW + timedelta(hours=9))
        self.collectors.append(collector)
        with patch("kiwoom_monitor.central_server.mock_account_monitor.connect", return_value=Connection()):
            await collector.start(); await asyncio.wait_for(entered.wait(), 2)
            closing = asyncio.create_task(collector.close()); await asyncio.wait_for(exited.wait(), 2)
            self.assertFalse(closing.done())
            release.set(); await closing
        self.assertIsNone(collector._task)

    async def test_null_frame_is_empty_batch(self):
        delivered = []
        collector = MockAccountRealtimeCollector(lambda: "", lambda *args: delivered.append(args), lambda: NOW)
        collector._dispatch_realtime({"trnm": "REAL", "data": None})
        self.assertEqual([], delivered)


class ExecutionWriteFenceTests(unittest.TestCase):
    def setUp(self):
        self.store = SQLiteQueryStore(Path(":memory:")); self.store.initialize()
        self.addCleanup(self.store.close)
        self.repository = ExecutionRepository(self.store)
        self.current = datetime.now(timezone.utc)
        self.repository.claim_runtime("mock", "account-1", "run-1", "owner-1", self.current)
        self.repository.bind_runtime_owner("account-1", "run-1", "owner-1")
        self.intent = OrderIntent("intent-1", "run-1", "decision-1", "account-1", "mock", "005930", "KRX",
            OrderSide.BUY, 1, OrderType.LIMIT, 70_000, NOW, NOW + timedelta(minutes=1), "policy-v1")
        self.account = AccountSnapshot("account-1", 1_000_000, 0, {}, NOW)

    def test_replaced_owner_cannot_create_append_or_save_snapshot(self):
        record = self.repository.create(self.intent)
        self.assertTrue(self.repository.release_runtime("mock", "account-1", "run-1", "owner-1"))
        self.assertTrue(self.repository.claim_runtime("mock", "account-1", "run-2", "owner-2", self.current))
        event = order_event("intent-1", "FILL", OrderState.FILLED, NOW, NOW, quantity=1)
        for write in (lambda: self.repository.create(replace(self.intent, intent_id="intent-2")),
                      lambda: self.repository.apply(record, event, filled_quantity=1),
                      lambda: self.repository.save_account_snapshot("mock", self.account, NOW)):
            with self.assertRaisesRegex(RuntimeError, "OWNERSHIP_LOST"): write()
        self.assertIsNone(self.repository.load("intent-2"))
        self.assertEqual(OrderState.QUEUED, self.repository.load("intent-1").state)
        self.assertEqual((), self.repository.events("intent-1"))
        with self.store._connection() as connection:
            self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM central_execution_account_snapshots").fetchone()[0])

    def test_expired_owner_cannot_write_without_renewal(self):
        with self.store._connection() as connection:
            connection.execute("UPDATE central_execution_runtime_leases SET lease_expires_at=?",
                ((self.current - timedelta(seconds=1)).isoformat(),))
        with self.assertRaisesRegex(RuntimeError, "OWNERSHIP_LOST"): self.repository.create(self.intent)
        self.assertTrue(self.repository.claim_runtime("mock", "account-1", "run-1", "owner-1", datetime.now(timezone.utc)))
        self.repository.create(self.intent)

    def test_bound_repository_cannot_change_owner_or_scope(self):
        with self.assertRaisesRegex(RuntimeError, "IMMUTABLE"):
            self.repository.bind_runtime_owner("account-1", "run-2", "owner-2")
        with self.assertRaisesRegex(RuntimeError, "SCOPE_MISMATCH"):
            self.repository.create(replace(self.intent, account_ref="account-2"))
        with self.assertRaisesRegex(RuntimeError, "SCOPE_MISMATCH"):
            self.repository.create(replace(self.intent, run_id="run-2"))

    def test_valid_owned_write_and_offline_read_contract_remain_usable(self):
        record = self.repository.create(self.intent)
        event = order_event("intent-1", "FILL", OrderState.FILLED, NOW, NOW, quantity=1)
        self.repository.apply(record, event, filled_quantity=1)
        self.assertTrue(self.repository.save_account_snapshot("mock", self.account, NOW))
        self.assertEqual(OrderState.FILLED, ExecutionRepository(self.store).load("intent-1").state)

    def test_late_submission_response_after_owner_change_preserves_unknown(self):
        class Transport:
            def submit(inner, _):
                self.repository.release_runtime("mock", "account-1", "run-1", "owner-1")
                self.repository.claim_runtime("mock", "account-1", "run-2", "owner-2", datetime.now(timezone.utc))
                return BrokerSubmission("broker-1", NOW)
        runtime = ExecutionRuntime(OrderLifecycle(self.repository, Transport(), now_provider=lambda: NOW), self.repository,
            account_ref="account-1", run_id="run-1", owner_token="owner-1")
        runtime.start()
        with self.assertRaisesRegex(RuntimeError, "OWNERSHIP_LOST"): runtime.submit(self.intent, self.account)
        record = self.repository.load("intent-1")
        self.assertEqual(OrderState.SUBMISSION_UNKNOWN, record.state)
        self.assertEqual("", record.broker_order_id)
        self.assertNotIn("ORDER_ACCEPTED", [event["event_type"] for event in self.repository.events("intent-1")])

    def test_ownership_check_and_write_hold_one_database_transaction(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "central.sqlite"
            first, second = SQLiteQueryStore(path), SQLiteQueryStore(path)
            first.initialize()
            repository = ExecutionRepository(first)
            repository.claim_runtime("mock", "account-1", "run-1", "owner-1", self.current)
            repository.bind_runtime_owner("account-1", "run-1", "owner-1")
            entered, release, claimed = threading.Event(), threading.Event(), threading.Event()
            from kiwoom_monitor.central_server.database import _execution_intent_values
            def values(*args):
                entered.set()
                if not release.wait(3): raise RuntimeError("test transaction was not released")
                return _execution_intent_values(*args)
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=2) as pool:
                with patch("kiwoom_monitor.central_server.database._execution_intent_values", side_effect=values):
                    writing = pool.submit(repository.create, self.intent)
                    self.assertTrue(entered.wait(2))
                    def replace_owner():
                        result = second.acquire_execution_runtime("mock:account-1", "run-2:owner-2",
                            (self.current + timedelta(seconds=61)).isoformat(), (self.current + timedelta(seconds=121)).isoformat())
                        claimed.set(); return result
                    replacement = pool.submit(replace_owner)
                    try: self.assertFalse(claimed.wait(0.02))
                    finally: release.set()
                    writing.result(2); self.assertTrue(replacement.result(2))
            with self.assertRaisesRegex(RuntimeError, "OWNERSHIP_LOST"):
                repository.save_account_snapshot("mock", self.account, NOW)
            first.close(); second.close()
