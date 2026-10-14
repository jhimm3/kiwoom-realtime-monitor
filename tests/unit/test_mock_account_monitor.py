from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kiwoom_monitor.application.order_lifecycle import OrderLifecycle
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.execution_runtime import ExecutionRuntime
from kiwoom_monitor.central_server.mock_account_monitor import (
    MockAccountMonitor,
    MockAccountRealtimeCollector,
)
from kiwoom_monitor.domain.order_contract import (
    AccountSnapshot,
    BrokerOrderSnapshot,
    OrderIntent,
    OrderSide,
    OrderState,
    OrderType,
    order_event,
)
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import MockAccountRecovery
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import AccountBalanceChange, OrderExecution
from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository


NOW = datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc)


class _Reader:
    def __init__(self, recovery: MockAccountRecovery) -> None:
        self.recovery = recovery
        self.calls = 0

    async def read(self) -> MockAccountRecovery:
        self.calls += 1
        return self.recovery


class MockAccountMonitorTests(unittest.TestCase):
    def test_dedicated_realtime_subscribes_only_to_mock_account_events(self) -> None:
        class Socket:
            def __init__(self) -> None:
                self.sent: list[dict[str, object]] = []

            async def send(self, value: str) -> None:
                self.sent.append(json.loads(value))

        socket = Socket()
        collector = MockAccountRealtimeCollector(
            lambda: "mock-token", lambda *_args: None, lambda: NOW,
        )

        asyncio.run(collector._send_subscription(socket))

        self.assertEqual(
            [{"item": [""], "type": ["00", "04"]}],
            socket.sent[0]["data"],
        )

    def test_dedicated_realtime_forwards_fill_and_balance_without_account_number(self) -> None:
        received: list[tuple[str, object]] = []
        collector = MockAccountRealtimeCollector(
            lambda: "mock-token", lambda kind, value: received.append((kind, value)),
            lambda: NOW,
        )

        collector._dispatch_realtime({
            "trnm": "REAL",
            "data": [
                {"type": "00", "item": "A005930", "values": {
                    "913": "체결", "914": "71000", "915": "1", "905": "+매수",
                    "9203": "broker-1", "909": "fill-1", "302": "삼성전자",
                    "908": "090001", "2135": "KRX", "900": "1", "902": "0",
                }},
                {"type": "04", "item": "A005930", "values": {
                    "9201": "12345678", "930": "1", "933": "1", "931": "71000",
                }},
            ],
        })

        self.assertEqual(
            ["order_execution", "order_changed", "account_balance"],
            [kind for kind, _value in received],
        )
        balance = received[-1][1]
        self.assertIsInstance(balance, AccountBalanceChange)
        self.assertFalse(hasattr(balance, "account_number"))

    def test_dedicated_realtime_does_not_reconnect_outside_mock_session(self) -> None:
        weekend = MockAccountRealtimeCollector(
            lambda: "mock-token", lambda *_args: None,
            lambda: datetime(2026, 9, 13, 10, tzinfo=timezone.utc),
        )
        weekday = MockAccountRealtimeCollector(
            lambda: "mock-token", lambda *_args: None,
            lambda: datetime(2026, 9, 14, 9, tzinfo=timezone.utc),
        )
        self.assertFalse(weekend._session_open())
        self.assertTrue(weekday._session_open())

    def test_startup_recovery_04_refresh_and_00_fill_share_one_runtime(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as directory:
                store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
                store.initialize()
                repository = ExecutionRepository(store)
                intent = OrderIntent(
                    "intent-1", "run-1", "decision-1", "account-1", "mock", "005930", "KRX",
                    OrderSide.BUY, 2, OrderType.LIMIT, 70_000,
                    NOW - timedelta(minutes=1), NOW + timedelta(minutes=5), "policy-v1",
                )
                record = repository.create(intent)
                repository.apply(record, order_event(
                    "intent-1", "ORDER_ACCEPTED", OrderState.ACCEPTED, NOW, NOW,
                    broker_order_id="broker-1",
                ), broker_order_id="broker-1")
                reader = _Reader(MockAccountRecovery(
                    AccountSnapshot("account-1", 1_000_000, 0, {"005930": 1}, NOW),
                    (BrokerOrderSnapshot(
                        "broker-1", "account-1", "005930", OrderState.PARTIALLY_FILLED,
                        1, 1, NOW,
                    ),),
                ))
                runtime = ExecutionRuntime(
                    OrderLifecycle(repository, None, now_provider=lambda: NOW + timedelta(seconds=2)),
                    repository,
                    account_ref="account-1", run_id="run-1", owner_token="owner-1",
                )
                monitor = MockAccountMonitor(
                    reader, runtime, repository,
                    now_provider=lambda: NOW + timedelta(seconds=2),
                )
                await monitor.start()
                await asyncio.wait_for(monitor._queue.join(), timeout=2)
                self.assertEqual(OrderState.PARTIALLY_FILLED, repository.load("intent-1").state)

                monitor.handle_realtime("account_balance", AccountBalanceChange(
                    "005930", "삼성전자", 2, 2, 70_000, 140_000, 500_000, 71_000,
                ))
                await asyncio.wait_for(monitor._queue.join(), timeout=2)
                self.assertEqual(2, reader.calls)

                monitor.handle_realtime("connected", "KRX")
                await asyncio.wait_for(monitor._queue.join(), timeout=2)
                self.assertEqual(3, reader.calls)

                monitor.handle_realtime("order_execution", OrderExecution(
                    "broker-1", "fill-2", "005930", "삼성전자", "매수", 71_000, 1,
                    "090002", "KRX", 2, 0, 2,
                ))
                await asyncio.wait_for(monitor._queue.join(), timeout=2)
                filled = repository.load("intent-1")
                self.assertEqual(OrderState.FILLED, filled.state)
                self.assertEqual(2, filled.filled_quantity)
                self.assertEqual(("fill-2",), filled.fill_ids)
                await monitor.close()
                store.close()

        asyncio.run(scenario())

    def test_read_only_lifecycle_refuses_submit_without_transport(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            repository = ExecutionRepository(store)
            intent = OrderIntent(
                "intent-1", "run-1", "decision-1", "account-1", "mock", "005930", "KRX",
                OrderSide.BUY, 1, OrderType.LIMIT, 70_000,
                NOW - timedelta(minutes=1), NOW + timedelta(minutes=5), "policy-v1",
            )
            lifecycle = OrderLifecycle(repository, None, now_provider=lambda: NOW)
            lifecycle.queue(intent)
            with self.assertRaisesRegex(RuntimeError, "transport"):
                lifecycle.submit(
                    "intent-1", AccountSnapshot("account-1", 1_000_000, 0, {}, NOW),
                )
            self.assertEqual(OrderState.QUEUED, repository.load("intent-1").state)
            store.close()

    def test_ram_observation_reuses_recovery_without_extra_db_or_broker_reads(self):
        async def scenario():
            store = SQLiteQueryStore(Path(":memory:")); store.initialize()
            repository = ExecutionRepository(store)
            reader = _Reader(MockAccountRecovery(AccountSnapshot("account-1", 100_000, 0, {"005930": 3}, NOW), ()))
            runtime = ExecutionRuntime(OrderLifecycle(repository, None), repository,
                account_ref="account-1", run_id="run-1", owner_token="owner-1")
            monitor = MockAccountMonitor(reader, runtime, repository, now_provider=lambda: NOW)
            notifications = []; monitor.set_position_observation_listener(lambda: notifications.append(True))
            try:
                with patch.object(repository, "save_account_snapshot", wraps=repository.save_account_snapshot) as saves:
                    await monitor.refresh_recovery()
                    self.assertEqual((1, 1), (reader.calls, saves.call_count))
                with patch.object(store, "_connect", side_effect=AssertionError("RAM getter accessed DB")):
                    values = [monitor.latest_position_observation for _ in range(8)]
                self.assertEqual(1, reader.calls)
                self.assertEqual([3] * 8, [value.account.positions["005930"] for value in values])
                self.assertFalse(values[0].open_orders_complete)
                self.assertIn("account_wide_intent_coverage_unverified", values[0].reasons)
                values[0].account.positions["005930"] = 99
                self.assertEqual(3, monitor.latest_position_observation.account.positions["005930"])
                monitor.handle_realtime("disconnected", None)
                self.assertIsNone(monitor.latest_position_observation)
                self.assertEqual(2, len(notifications))
                await monitor.refresh_recovery()
                self.assertNotEqual(values[1].input_ref, monitor.latest_position_observation.input_ref)
            finally:
                await monitor.close(); store.close()
            self.assertIsNone(monitor.latest_position_observation)
        asyncio.run(scenario())

    def test_inflight_account_notification_and_failed_refresh_cannot_publish_stale_snapshot(self):
        async def scenario():
            store = SQLiteQueryStore(Path(":memory:")); store.initialize()
            repository = ExecutionRepository(store)
            reader = _Reader(MockAccountRecovery(AccountSnapshot("account-1", 100_000, 0, {}, NOW), ()))
            runtime = ExecutionRuntime(OrderLifecycle(repository, None), repository,
                account_ref="account-1", run_id="run-1", owner_token="owner-1")
            monitor = MockAccountMonitor(reader, runtime, repository, now_provider=lambda: NOW)
            try:
                await monitor.refresh_recovery()
                entered, release = asyncio.Event(), asyncio.Event()
                async def waiting():
                    entered.set(); await release.wait(); return reader.recovery
                with patch.object(reader, "read", side_effect=waiting):
                    task = asyncio.create_task(monitor.refresh_recovery()); await entered.wait()
                    self.assertIsNone(monitor.latest_position_observation)
                    monitor.handle_realtime("disconnected", None)
                    release.set(); await task
                    self.assertIsNone(monitor.latest_position_observation)
                await monitor.refresh_recovery()
                with patch.object(reader, "read", side_effect=OSError("broker read failed")):
                    with self.assertRaises(OSError): await monitor.refresh_recovery()
                self.assertIsNone(monitor.latest_position_observation)
            finally:
                await monitor.close(); store.close()
        asyncio.run(scenario())

    def test_unmapped_broker_orders_and_partial_fills_keep_account_wide_coverage_blocked(self):
        async def scenario():
            store = SQLiteQueryStore(Path(":memory:")); store.initialize()
            repository = ExecutionRepository(store)
            intent = OrderIntent("intent-1", "run-1", "decision-1", "account-1", "mock", "005930", "KRX",
                OrderSide.BUY, 2, OrderType.LIMIT, 1000, NOW - timedelta(minutes=1), NOW + timedelta(minutes=5), "policy-v1")
            record = repository.create(intent)
            repository.apply(record, order_event("intent-1", "ORDER_ACCEPTED", OrderState.ACCEPTED, NOW, NOW,
                broker_order_id="broker-1"), broker_order_id="broker-1")
            reader = _Reader(MockAccountRecovery(AccountSnapshot("account-1", 100_000, 1000, {"005930": 1}, NOW), (
                BrokerOrderSnapshot("broker-1", "account-1", "005930", OrderState.PARTIALLY_FILLED, 1, 1, NOW),
                BrokerOrderSnapshot("manual-order", "account-1", "000660", OrderState.ACCEPTED, 0, 2, NOW))))
            runtime = ExecutionRuntime(OrderLifecycle(repository, None, now_provider=lambda: NOW), repository,
                account_ref="account-1", run_id="run-1", owner_token="owner-1")
            runtime.start()
            monitor = MockAccountMonitor(reader, runtime, repository, now_provider=lambda: NOW)
            try:
                await monitor.refresh_recovery()
                value = monitor.latest_position_observation
                self.assertEqual(("manual-order",), value.unmapped_broker_order_ids)
                self.assertEqual(1, len(value.open_orders))
                self.assertEqual((1, 0), (value.open_orders[0].filled_quantity, value.open_orders[0].detailed_filled_quantity))
                self.assertFalse(value.reconciliation_complete)
                self.assertFalse(value.open_orders_complete)
            finally:
                await monitor.close(); runtime.stop(); store.close()
        asyncio.run(scenario())

    def test_lease_failure_invalidates_published_account_receipt(self):
        async def scenario():
            store = SQLiteQueryStore(Path(":memory:")); store.initialize()
            repository = ExecutionRepository(store)
            runtime = ExecutionRuntime(OrderLifecycle(repository, None), repository,
                account_ref="account-1", run_id="run-1", owner_token="owner-1")
            reader = _Reader(MockAccountRecovery(AccountSnapshot("account-1", 100_000, 0, {}, NOW), ()))
            monitor = MockAccountMonitor(reader, runtime, repository, now_provider=lambda: NOW)
            notifications = []; monitor.set_position_observation_listener(lambda: notifications.append(True))
            try:
                await monitor.refresh_recovery()
                self.assertIsNotNone(monitor.latest_position_observation)
                monitor._heartbeat_seconds = 0.001
                with patch.object(runtime, "heartbeat", side_effect=RuntimeError("lease lost")), \
                     self.assertLogs("kiwoom_monitor.central_server.mock_account_monitor", level="ERROR"):
                    await monitor._heartbeat()
                self.assertIsNone(monitor.latest_position_observation)
                self.assertEqual(2, len(notifications))
                self.assertFalse(monitor._accepting)
            finally:
                await monitor.close(); store.close()
        asyncio.run(scenario())


class AccountWideObservationMonitorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = SQLiteQueryStore(Path(":memory:")); self.store.initialize()
        self.repository = ExecutionRepository(self.store)
        self.reader = _Reader(MockAccountRecovery(AccountSnapshot("account-1", 50_000, 0, {"005930": 10}, NOW), ()))
        self.runtime = ExecutionRuntime(OrderLifecycle(self.repository, None, now_provider=lambda: NOW), self.repository,
            account_ref="account-1", run_id="run-current", owner_token="owner-1")
        self.runtime.start()
        self.monitor = MockAccountMonitor(self.reader, self.runtime, self.repository, now_provider=lambda: NOW)

    async def asyncTearDown(self):
        await self.monitor.close(); self.runtime.stop(); self.store.close()

    async def test_published_native_risk_is_copied_with_account_without_additional_queries(self):
        from dataclasses import replace
        from unittest.mock import AsyncMock
        from tests.unit.test_mock_automation_risk import ACCOUNT,SCOPE
        from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import ForwardEvaluationRepository
        await self.monitor.close(); self.runtime.stop()
        self.repository = ExecutionRepository(self.store)
        self.reader.recovery = replace(self.reader.recovery,account=replace(self.reader.recovery.account,account_ref=ACCOUNT,positions={}))
        self.reader.read_trade_cost_pages = AsyncMock(return_value=())
        self.runtime = ExecutionRuntime(OrderLifecycle(self.repository,None,now_provider=lambda:NOW),self.repository,
            account_ref=ACCOUNT,run_id='run-current',owner_token='owner-1')
        self.runtime.start()
        risk_repository = ForwardEvaluationRepository(self.store)
        self.monitor = MockAccountMonitor(self.reader,self.runtime,self.repository,now_provider=lambda:NOW,
            automation_risk_repository=risk_repository,automation_binding_revision=1,automation_account_scope=SCOPE)
        self.monitor.set_account_order_observation_enabled(True)
        with patch.object(risk_repository,'save_mock_automation_risk_snapshot',wraps=risk_repository.save_mock_automation_risk_snapshot) as saves:
            await self.monitor.refresh_recovery()
            snapshot = saves.call_args.args[0]
            self.assertEqual(1,saves.call_count)
            self.assertEqual(1,self.reader.read_trade_cost_pages.call_count)
        self.assertEqual(snapshot,risk_repository.load_latest_mock_automation_risk(ACCOUNT))
        self.assertEqual(snapshot,self.monitor.latest_position_observation.risk_snapshot)
        with patch.object(self.store,'_connect',side_effect=AssertionError('native risk RAM getter queried DB')):
            for _ in range(8):
                value = self.monitor.latest_position_observation
                self.assertEqual(snapshot,value.risk_snapshot)
                self.assertEqual(value.account.as_of,snapshot.account_as_of)
            self.assertEqual(1,self.reader.calls)
        self.monitor.handle_realtime('disconnected',None)
        self.assertIsNone(self.monitor.latest_position_observation)

    async def test_opt_in_full_recovery_has_two_shared_reads_and_ram_getter_has_none(self):
        with patch.object(self.repository, "account_active_intents", wraps=self.repository.account_active_intents) as reads:
            await self.monitor.refresh_recovery()
            self.assertEqual(0, reads.call_count)
            self.assertFalse(self.monitor.latest_position_observation.open_orders_complete)
            self.monitor.set_account_order_observation_enabled(True)
            await self.monitor.refresh_recovery()
            self.assertEqual(2, reads.call_count)
            self.assertEqual(2, self.reader.calls)
        observation = self.monitor.latest_position_observation
        self.assertTrue(observation.open_orders_complete)
        self.assertTrue(observation.reconciliation_complete)
        with patch.object(self.store, "_connect", side_effect=AssertionError("RAM getter accessed DB")):
            for _ in range(8): self.assertEqual(observation.input_ref, self.monitor.latest_position_observation.input_ref)

    async def test_other_run_is_read_and_confirmed_without_writing_its_ledger(self):
        unowned = ExecutionRepository(self.store)
        intent = OrderIntent("retired", "run-old", "decision-old", "account-1", "mock", "005930", "KRX",
            OrderSide.BUY, 2, OrderType.LIMIT, 1000, NOW - timedelta(minutes=1), NOW + timedelta(minutes=1), "policy-v1")
        record = unowned.create(intent)
        unowned.apply(record, order_event("retired", "ORDER_ACCEPTED", OrderState.ACCEPTED, NOW, NOW,
            broker_order_id="old-broker"), broker_order_id="old-broker")
        before = unowned.load("retired"); events = unowned.events("retired")
        self.reader.recovery = MockAccountRecovery(AccountSnapshot("account-1", 50_000, 1000, {"005930": 10}, NOW),
            (BrokerOrderSnapshot("old-broker", "account-1", "005930", OrderState.PARTIALLY_FILLED, 1, 1, NOW),))
        self.monitor.set_account_order_observation_enabled(True)
        await self.monitor.refresh_recovery()
        observation = self.monitor.latest_position_observation
        self.assertTrue(observation.open_orders_complete)
        self.assertEqual(1, observation.broker_open_orders[0].remaining_quantity)
        self.assertEqual(before, unowned.load("retired"))
        self.assertEqual(events, unowned.events("retired"))

    async def test_existing_current_run_reconcile_does_not_look_like_an_external_mutation(self):
        intent = OrderIntent("current", "run-current", "decision-current", "account-1", "mock", "005930", "KRX",
            OrderSide.BUY, 2, OrderType.LIMIT, 1000, NOW - timedelta(minutes=1), NOW + timedelta(minutes=1), "policy-v1")
        record = self.repository.create(intent)
        self.repository.apply(record, order_event("current", "ORDER_ACCEPTED", OrderState.ACCEPTED, NOW, NOW,
            broker_order_id="current-broker"), broker_order_id="current-broker")
        self.reader.recovery = MockAccountRecovery(AccountSnapshot("account-1", 50_000, 1000, {"005930": 10}, NOW),
            (BrokerOrderSnapshot("current-broker", "account-1", "005930", OrderState.PARTIALLY_FILLED, 1, 1, NOW),))
        self.monitor.set_account_order_observation_enabled(True)
        await self.monitor.refresh_recovery()
        value = self.monitor.latest_position_observation
        self.assertTrue(value.open_orders_complete, value.reasons)
        self.assertEqual(1, value.broker_open_orders[0].remaining_quantity)
        self.assertEqual(OrderState.PARTIALLY_FILLED, self.repository.load("current").state)
        self.assertEqual(2, self.runtime.observation_revision)

    async def test_new_queued_operation_invalidates_complete_receipt_and_next_recovery_blocks(self):
        self.monitor.set_account_order_observation_enabled(True)
        await self.monitor.refresh_recovery()
        self.assertTrue(self.monitor.latest_position_observation.open_orders_complete)
        intent = OrderIntent("queued", "run-current", "decision-new", "account-1", "mock", "005930", "KRX",
            OrderSide.BUY, 1, OrderType.LIMIT, 1000, NOW, NOW + timedelta(minutes=1), "policy-v1")
        with self.assertRaisesRegex(RuntimeError, "transport"):
            await asyncio.to_thread(self.runtime.submit, intent, self.reader.recovery.account)
        self.assertIsNone(self.monitor.latest_position_observation)
        await self.monitor.refresh_recovery()
        self.assertFalse(self.monitor.latest_position_observation.open_orders_complete)
        self.assertIn("unconfirmed_or_cancel_pending_order", self.monitor.latest_position_observation.reasons)

    async def test_execution_during_query_and_failed_ledger_read_do_not_promote_empty(self):
        self.monitor.set_account_order_observation_enabled(True)
        async def changed():
            self.runtime._order_observation_changed()
            return self.reader.recovery
        with patch.object(self.reader, "read", side_effect=changed):
            await self.monitor.refresh_recovery()
        self.assertFalse(self.monitor.latest_position_observation.open_orders_complete)
        self.assertIn("execution_changed_during_account_query", self.monitor.latest_position_observation.reasons)
        with patch.object(self.repository, "account_active_intents", side_effect=OSError("ledger read failed")), \
             self.assertLogs("kiwoom_monitor.central_server.mock_account_monitor", level="ERROR"):
            await self.monitor.refresh_recovery()
        self.assertFalse(self.monitor.latest_position_observation.open_orders_complete)
        self.assertIn("account_order_ledger_read_unavailable", self.monitor.latest_position_observation.reasons)

    async def test_observation_revision_does_not_wait_on_lease_db_lock(self):
        import threading
        held, release = threading.Event(), threading.Event()
        def hold():
            with self.runtime._state_lock:
                held.set(); release.wait(2)
        thread = threading.Thread(target=hold); thread.start(); held.wait(1)
        try:
            revision = await asyncio.wait_for(asyncio.to_thread(lambda: self.runtime.observation_revision), 0.2)
            self.assertEqual(0, revision)
        finally:
            release.set(); thread.join()

    async def test_submit_before_queued_persistence_cannot_publish_complete_empty_account(self):
        import threading
        entered, release = threading.Event(), threading.Event()
        self.monitor.set_account_order_observation_enabled(True)
        await self.monitor.refresh_recovery()
        intent = OrderIntent("delayed", "run-current", "decision-new", "account-1", "mock", "005930", "KRX",
            OrderSide.BUY, 1, OrderType.LIMIT, 1000, NOW, NOW + timedelta(minutes=1), "policy-v1")
        queue = self.runtime._lifecycle.queue
        def delayed(value):
            entered.set()
            if not release.wait(3): raise RuntimeError("test queue wait timeout")
            return queue(value)
        with patch.object(self.runtime._lifecycle, "queue", side_effect=delayed):
            task = asyncio.create_task(asyncio.to_thread(self.runtime.submit, intent, self.reader.recovery.account))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 1))
                self.assertTrue(self.runtime.observation_in_progress)
                self.assertEqual((), self.repository.account_active_intents("account-1")[0])
                await self.monitor.refresh_recovery()
                self.assertIsNone(self.monitor.latest_position_observation)
                self.assertFalse(self.monitor._position_observation.open_orders_complete)
                revision = self.runtime.observation_revision
            finally:
                release.set()
                with self.assertRaisesRegex(RuntimeError, "transport"): await task
        self.assertFalse(self.runtime.observation_in_progress)
        self.assertGreater(self.runtime.observation_revision, revision)
        self.assertIsNone(self.monitor.latest_position_observation)
        await self.monitor.refresh_recovery()
        self.assertFalse(self.monitor.latest_position_observation.open_orders_complete)


if __name__ == "__main__":
    unittest.main()
