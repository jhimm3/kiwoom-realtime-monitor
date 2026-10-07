from __future__ import annotations

import asyncio
import threading
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.application.order_lifecycle import OrderLifecycle
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.execution_runtime import ExecutionRuntime, ManualMockOrderGateway
from kiwoom_monitor.domain.order_contract import (
    AccountSnapshot, BrokerOrderSnapshot, BrokerSubmission, OrderIntent, OrderSide, OrderState, OrderType,
)
from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository


NOW = datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc)


class _Transport:
    def __init__(self):
        self.entered, self.release = threading.Event(), threading.Event()
        self.release.set()
        self.submit_calls, self.cancel_calls = 0, 0

    def wait(self):
        self.entered.set()
        if not self.release.wait(3): raise RuntimeError("test transport was not released")

    def submit(self, _):
        self.submit_calls += 1; self.wait()
        return BrokerSubmission("broker-1", NOW)

    def cancel(self, *_):
        self.cancel_calls += 1; self.wait()
        return BrokerSubmission("cancel-1", NOW)


class MockRuntimeBarrierTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = SQLiteQueryStore(Path(":memory:")); self.store.initialize()
        self.repository = ExecutionRepository(self.store)
        self.transport = _Transport()
        self.lifecycle = OrderLifecycle(self.repository, self.transport, now_provider=lambda: NOW)
        self.runtime = ExecutionRuntime(self.lifecycle, self.repository,
            account_ref="account-1", run_id="run-1", owner_token="owner-1")
        self.runtime.start()
        self.account = AccountSnapshot("account-1", 1_000_000, 0, {}, NOW)
        async def refresh(): return self.account
        self.gateway = ManualMockOrderGateway(self.runtime, self.repository, refresh, now_provider=lambda: NOW)
        self.intent = OrderIntent("intent-1", "run-1", "decision-1", "account-1", "mock", "005930", "KRX",
            OrderSide.BUY, 1, OrderType.LIMIT, 70_000, NOW, NOW + timedelta(minutes=1), "policy-v1")

    async def asyncTearDown(self):
        self.transport.release.set()
        await self.gateway.close()
        self.store.close()

    async def command(self, request_id="manual-1"):
        return await self.gateway.submit_limit(request_id=request_id, symbol="005930", side=OrderSide.BUY,
            quantity=1, limit_price=70_000, expires_seconds=60)

    async def entered(self):
        self.assertTrue(await asyncio.to_thread(self.transport.entered.wait, 2))

    async def test_http_cancelled_submit_still_drains_actual_transport_and_ledger(self):
        self.transport.release.clear()
        waiting = asyncio.create_task(self.command()); await self.entered()
        waiting.cancel()
        with self.assertRaises(asyncio.CancelledError): await waiting
        drain = asyncio.create_task(self.gateway.begin_credential_change()); await asyncio.sleep(0)
        self.assertFalse(drain.done())
        with self.assertRaisesRegex(RuntimeError, "IN_PROGRESS"): await self.command("manual-2")
        with self.assertRaisesRegex(RuntimeError, "NOT_COMPLETE"): self.gateway.end_credential_change()
        drain.cancel()
        with self.assertRaises(asyncio.CancelledError): await drain
        self.assertFalse(self.gateway._drain_task.done())
        self.transport.release.set(); await self.gateway.begin_credential_change()
        record = self.gateway.load(self.gateway.intent_id("manual-1"))
        self.assertEqual(OrderState.ACCEPTED, record.state)
        self.assertEqual(1, self.transport.submit_calls)
        self.gateway.end_credential_change()
        repeated = await self.command()
        self.assertEqual(record.intent.intent_id, repeated.intent.intent_id)
        self.assertEqual(1, self.transport.submit_calls)

    async def test_cancelled_cancel_waiter_keeps_command_until_actual_cancel_finished(self):
        first = await self.command()
        self.transport.entered.clear(); self.transport.release.clear()
        waiting = asyncio.create_task(self.gateway.cancel(first.intent.intent_id)); await self.entered()
        waiting.cancel()
        with self.assertRaises(asyncio.CancelledError): await waiting
        closing = asyncio.create_task(self.gateway.close()); await asyncio.sleep(0)
        self.assertFalse(closing.done())
        self.transport.release.set(); await closing
        self.assertEqual(1, self.transport.cancel_calls)
        self.assertEqual(OrderState.CANCEL_PENDING, self.repository.load(first.intent.intent_id).state)
        with self.assertRaisesRegex(RuntimeError, "CLOSED"): self.gateway.end_credential_change()

    async def test_drain_waits_for_account_refresh_before_submission(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def refresh(): entered.set(); await release.wait(); return self.account
        self.gateway._account_refresh = refresh
        waiting = asyncio.create_task(self.command()); await entered.wait()
        drain = asyncio.create_task(self.gateway.begin_credential_change()); await asyncio.sleep(0)
        self.assertFalse(drain.done())
        self.assertEqual(0, self.transport.submit_calls)
        release.set(); await waiting; await drain
        self.assertEqual(1, self.transport.submit_calls)

    async def test_disabled_new_orders_allow_reconcile_and_cancel(self):
        record = await asyncio.to_thread(self.runtime.submit, self.intent, self.account)
        self.runtime.set_new_orders_enabled(False)
        with self.assertRaisesRegex(RuntimeError, "NEW_ORDERS_DISABLED"):
            await asyncio.to_thread(self.runtime.submit, replace(self.intent, intent_id="intent-2"), self.account)
        self.assertIsNone(self.repository.load("intent-2"))
        snapshot = BrokerOrderSnapshot("broker-1", "account-1", "005930", OrderState.ACCEPTED,
            0, 1, NOW + timedelta(seconds=1))
        self.assertEqual(record.state, (await asyncio.to_thread(self.runtime.reconcile, "intent-1", snapshot)).state)
        self.assertEqual(OrderState.CANCEL_PENDING, (await asyncio.to_thread(self.runtime.cancel, "intent-1")).state)

    async def test_stop_waits_for_submit_but_heartbeat_can_renew_during_transport(self):
        self.transport.release.clear()
        submit = asyncio.create_task(asyncio.to_thread(self.runtime.submit, self.intent, self.account))
        await self.entered()
        await asyncio.wait_for(asyncio.to_thread(self.runtime.heartbeat), timeout=1)
        stop = asyncio.create_task(asyncio.to_thread(self.runtime.stop)); await asyncio.sleep(0.02)
        self.assertFalse(stop.done())
        self.transport.release.set(); await submit
        self.assertTrue(await stop)
        with self.assertRaisesRegex(RuntimeError, "RETIRED"): self.runtime.start()
        with self.assertRaises(RuntimeError): self.runtime.heartbeat()
        with self.assertRaises(RuntimeError): self.runtime.cancel("intent-1")
        self.assertTrue(self.repository.claim_runtime("mock", "account-1", "run-1", "owner-2", datetime.now(timezone.utc)))
        self.assertFalse(self.runtime.stop())  # A repeated old stop must not release the new owner.
        self.assertFalse(self.repository.claim_runtime("mock", "account-1", "run-1", "owner-3", datetime.now(timezone.utc)))

    async def test_stop_waits_for_reconcile_database_write(self):
        await asyncio.to_thread(self.runtime.submit, self.intent, self.account)
        entered, release = threading.Event(), threading.Event()
        original = self.repository.apply
        def blocked(*args, **kwargs):
            entered.set()
            if not release.wait(3): raise RuntimeError("test database was not released")
            return original(*args, **kwargs)
        snapshot = BrokerOrderSnapshot("broker-1", "account-1", "005930", OrderState.FILLED,
            1, 0, NOW + timedelta(seconds=1))
        with patch.object(self.repository, "apply", side_effect=blocked):
            reconcile = asyncio.create_task(asyncio.to_thread(self.runtime.reconcile, "intent-1", snapshot))
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            stop = asyncio.create_task(asyncio.to_thread(self.runtime.stop)); await asyncio.sleep(0.02)
            self.assertFalse(stop.done())
            release.set(); await reconcile; self.assertTrue(await stop)
        self.assertEqual(OrderState.FILLED, self.repository.load("intent-1").state)


class ExecutionLeaseReleaseTests(unittest.TestCase):
    def test_release_requires_account_run_and_owner_and_allows_immediate_handover(self):
        store = SQLiteQueryStore(Path(":memory:")); store.initialize()
        self.addCleanup(store.close)
        repository = ExecutionRepository(store)
        self.assertTrue(repository.claim_runtime("mock", "account", "run", "owner-a", NOW))
        self.assertFalse(repository.release_runtime("mock", "account", "other-run", "owner-a"))
        self.assertFalse(repository.release_runtime("mock", "account", "run", "owner-b"))
        self.assertFalse(repository.release_runtime("mock", "other-account", "run", "owner-a"))
        self.assertTrue(repository.release_runtime("mock", "account", "run", "owner-a"))
        self.assertTrue(repository.claim_runtime("mock", "account", "run", "owner-b", NOW))
        self.assertFalse(repository.release_runtime("mock", "account", "run", "owner-a"))
        self.assertFalse(repository.claim_runtime("mock", "account", "run", "owner-c", NOW))

    def test_expired_old_owner_cannot_delete_replacement_owner(self):
        store = SQLiteQueryStore(Path(":memory:")); store.initialize()
        self.addCleanup(store.close)
        repository = ExecutionRepository(store)
        self.assertTrue(repository.claim_runtime("mock", "account", "run", "owner-a", NOW, 1))
        self.assertTrue(repository.claim_runtime("mock", "account", "run-new", "owner-b", NOW + timedelta(seconds=2)))
        self.assertFalse(repository.release_runtime("mock", "account", "run", "owner-a"))
        self.assertTrue(repository.release_runtime("mock", "account", "run-new", "owner-b"))
