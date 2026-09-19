from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
