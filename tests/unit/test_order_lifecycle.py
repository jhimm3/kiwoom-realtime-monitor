from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kiwoom_monitor.application.order_lifecycle import OrderLifecycle
from kiwoom_monitor.application.market_session_schedule import CURRENT_SCHEDULE_VERSION
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.execution_runtime import ExecutionRuntime, ManualMockOrderGateway
from kiwoom_monitor.domain.order_contract import (
    AccountSnapshot, BrokerFill, BrokerOrderSnapshot, BrokerSubmission,
    OrderIntent, OrderSide, OrderState, OrderType,
)
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_execution import SubmissionUnknown
from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository


NOW = datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc)


class _Transport:
    def __init__(self, submit_error: Exception | None = None) -> None:
        self.submit_error = submit_error
        self.submit_calls = 0
        self.cancel_calls = 0

    def submit(self, _intent):
        self.submit_calls += 1
        if self.submit_error:
            raise self.submit_error
        return BrokerSubmission("broker-1", NOW)

    def cancel(self, _intent, _broker_order_id, _quantity=0):
        self.cancel_calls += 1
        return BrokerSubmission("cancel-1", NOW)


def _intent(expires_at: datetime | None = None) -> OrderIntent:
    return OrderIntent(
        "intent-1", "run-1", "decision-1", "account-1", "mock", "005930", "KRX",
        OrderSide.BUY, 3, OrderType.LIMIT, 70_000, NOW - timedelta(seconds=1),
        expires_at or NOW + timedelta(minutes=1), "policy-v1",
    )


def _account(account_ref: str = "account-1") -> AccountSnapshot:
    return AccountSnapshot(account_ref, 1_000_000, 0, {}, NOW - timedelta(seconds=1))


class OrderLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.store = SQLiteQueryStore(Path(self.directory.name) / "central.sqlite3")
        self.store.initialize()
        self.repository = ExecutionRepository(self.store)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_unknown_submission_is_never_automatically_retried_after_restart(self) -> None:
        first_transport = _Transport(SubmissionUnknown("timeout"))
        lifecycle = OrderLifecycle(self.repository, first_transport, now_provider=lambda: NOW)
        lifecycle.queue(_intent())
        record = lifecycle.submit("intent-1", _account())
        self.assertEqual(OrderState.SUBMISSION_UNKNOWN, record.state)

        replacement = _Transport()
        restarted = OrderLifecycle(
            ExecutionRepository(SQLiteQueryStore(Path(self.directory.name) / "central.sqlite3")),
            replacement, now_provider=lambda: NOW,
        )
        record = restarted.submit("intent-1", _account())
        self.assertEqual(OrderState.SUBMISSION_UNKNOWN, record.state)
        self.assertEqual(0, replacement.submit_calls)

    def test_preflight_rejects_expired_or_wrong_account_before_transport(self) -> None:
        transport = _Transport()
        lifecycle = OrderLifecycle(self.repository, transport, now_provider=lambda: NOW)
        lifecycle.queue(_intent(NOW))
        expired = lifecycle.submit("intent-1", _account())
        self.assertEqual(OrderState.REJECTED, expired.state)
        self.assertEqual(0, transport.submit_calls)

    def test_distinct_same_second_fills_are_deduplicated_by_execution_id(self) -> None:
        transport = _Transport()
        lifecycle = OrderLifecycle(self.repository, transport, now_provider=lambda: NOW)
        lifecycle.queue(_intent())
        accepted = lifecycle.submit("intent-1", _account())
        self.assertEqual(OrderState.ACCEPTED, accepted.state)
        fills = (
            BrokerFill("fill-a", 1, 70_000, NOW + timedelta(seconds=1)),
            BrokerFill("fill-b", 2, 70_100, NOW + timedelta(seconds=1)),
        )
        snapshot = BrokerOrderSnapshot(
            "broker-1", "account-1", "005930", OrderState.FILLED, 3, 0,
            NOW + timedelta(seconds=2), fills,
        )
        filled = lifecycle.reconcile("intent-1", snapshot)
        duplicate = lifecycle.reconcile("intent-1", snapshot)
        self.assertEqual(OrderState.FILLED, duplicate.state)
        self.assertEqual(3, duplicate.filled_quantity)
        self.assertEqual(3, duplicate.broker_reported_filled_quantity)
        self.assertEqual(("fill-a", "fill-b"), duplicate.fill_ids)
        self.assertEqual(
            0,
            sum(event["event_type"] == "BROKER_FILL_AGGREGATE"
                for event in self.repository.events("intent-1")),
        )

    def test_fill_wins_cancel_race_and_stale_snapshot_cannot_roll_back(self) -> None:
        transport = _Transport()
        lifecycle = OrderLifecycle(self.repository, transport, now_provider=lambda: NOW)
        lifecycle.queue(_intent())
        lifecycle.submit("intent-1", _account())
        pending = lifecycle.cancel("intent-1")
        self.assertEqual(OrderState.CANCEL_PENDING, pending.state)
        filled = lifecycle.reconcile("intent-1", BrokerOrderSnapshot(
            "broker-1", "account-1", "005930", OrderState.FILLED, 3, 0,
            NOW + timedelta(seconds=3), (BrokerFill("fill-all", 3, 70_000, NOW + timedelta(seconds=2)),),
        ))
        stale = lifecycle.reconcile("intent-1", BrokerOrderSnapshot(
            "broker-1", "account-1", "005930", OrderState.CANCELLED, 0, 3,
            NOW + timedelta(seconds=1), (),
        ))
        self.assertEqual(OrderState.FILLED, filled.state)
        self.assertEqual(OrderState.FILLED, stale.state)

    def test_aggregate_recovery_then_detailed_fills_does_not_double_count(self) -> None:
        transport = _Transport()
        lifecycle = OrderLifecycle(self.repository, transport, now_provider=lambda: NOW)
        lifecycle.queue(_intent())
        lifecycle.submit("intent-1", _account())

        aggregate = lifecycle.reconcile("intent-1", BrokerOrderSnapshot(
            "broker-1", "account-1", "005930", OrderState.FILLED, 3, 0,
            NOW + timedelta(seconds=1), (),
        ))
        detailed = lifecycle.reconcile("intent-1", BrokerOrderSnapshot(
            "broker-1", "account-1", "005930", OrderState.FILLED, 3, 0,
            NOW + timedelta(seconds=2), (
                BrokerFill("fill-a", 1, 70_000, NOW + timedelta(seconds=1)),
                BrokerFill("fill-b", 2, 70_100, NOW + timedelta(seconds=1)),
            ),
        ))

        self.assertEqual(3, aggregate.filled_quantity)
        self.assertEqual(3, detailed.filled_quantity)
        self.assertEqual(3, detailed.detailed_filled_quantity)
        self.assertEqual(3, detailed.broker_reported_filled_quantity)
        self.assertEqual(("fill-a", "fill-b"), detailed.fill_ids)
        self.assertEqual(
            1,
            sum(event["event_type"] == "BROKER_FILL_AGGREGATE" for event in self.repository.events("intent-1")),
        )

    def test_scoped_same_request_and_run_are_separate_for_two_accounts(self):
        async def scenario():
            transport = _Transport()
            gateways = []
            for index, account_ref in enumerate(("account-1", "account-2")):
                repository = ExecutionRepository(self.store)
                runtime = ExecutionRuntime(OrderLifecycle(repository, transport, now_provider=lambda: NOW),
                    repository, account_ref=account_ref, run_id="run-1", owner_token=f"owner-{index}")
                runtime.start()
                async def refresh(ref=account_ref): return _account(ref)
                gateways.append(ManualMockOrderGateway(runtime, repository, refresh, now_provider=lambda: NOW))
            self.assertEqual(gateways[0].intent_id("same"), gateways[1].intent_id("same"))
            records = [await gateway.submit_limit(request_id="same", symbol="005930", side=OrderSide.BUY,
                quantity=1, limit_price=1000, expires_seconds=120, scoped=True) for gateway in gateways]
            self.assertNotEqual(records[0].intent.intent_id, records[1].intent.intent_id)
            self.assertEqual(2, transport.submit_calls)
            self.assertEqual(["account-1", "account-2"], [r.intent.account_ref for r in records])
        asyncio.run(scenario())

    def test_manual_gateway_is_idempotent_and_refreshes_before_submit_and_cancel(self) -> None:
        async def scenario() -> None:
            transport = _Transport()
            runtime = ExecutionRuntime(
                OrderLifecycle(self.repository, transport, now_provider=lambda: NOW),
                self.repository,
                account_ref="account-1", run_id="run-1", owner_token="owner-1",
            )
            runtime.start()
            refreshes = 0

            async def refresh_account() -> AccountSnapshot:
                nonlocal refreshes
                refreshes += 1
                return _account()

            gateway = ManualMockOrderGateway(
                runtime, self.repository, refresh_account, now_provider=lambda: NOW,
            )
            first = await gateway.submit_limit(
                request_id="button-click-1", symbol="005930", side=OrderSide.BUY,
                quantity=3, limit_price=70_000, expires_seconds=120,
            )
            duplicate = await gateway.submit_limit(
                request_id="button-click-1", symbol="005930", side=OrderSide.BUY,
                quantity=3, limit_price=70_000, expires_seconds=120,
            )
            self.assertEqual(first.intent.intent_id, duplicate.intent.intent_id)
            self.assertEqual(1, transport.submit_calls)
            self.assertEqual(1, refreshes)

            cancelled = await gateway.cancel(first.intent.intent_id)
            self.assertEqual(OrderState.CANCEL_PENDING, cancelled.state)
            self.assertEqual(1, transport.cancel_calls)
            self.assertEqual(2, refreshes)

        asyncio.run(scenario())

    def test_manual_gateway_rejects_reusing_request_id_for_another_order(self) -> None:
        async def scenario() -> None:
            runtime = ExecutionRuntime(
                OrderLifecycle(self.repository, _Transport(), now_provider=lambda: NOW),
                self.repository,
                account_ref="account-1", run_id="run-1", owner_token="owner-1",
            )
            runtime.start()

            async def refresh_account() -> AccountSnapshot:
                return _account()

            gateway = ManualMockOrderGateway(
                runtime, self.repository, refresh_account, now_provider=lambda: NOW,
            )
            await gateway.submit_limit(
                request_id="same-id", symbol="005930", side=OrderSide.BUY,
                quantity=1, limit_price=70_000, expires_seconds=120,
            )
            with self.assertRaisesRegex(ValueError, "different mock order"):
                await gateway.submit_limit(
                    request_id="same-id", symbol="005930", side=OrderSide.BUY,
                    quantity=1, limit_price=70_100, expires_seconds=120,
                )

        asyncio.run(scenario())

    def test_new_orders_are_rejected_at_unverified_mock_session_boundaries(self) -> None:
        kst = timezone(timedelta(hours=9))
        rejected_times = (
            datetime(2026, 9, 14, 15, 20, tzinfo=kst),
            datetime(2026, 9, 14, 15, 30, tzinfo=kst),
            datetime(2026, 9, 14, 15, 35, tzinfo=kst),
            datetime(2026, 9, 14, 15, 40, tzinfo=kst),
        )
        for sequence, at in enumerate(rejected_times):
            with self.subTest(at=at):
                intent_id = f"rejected-{sequence}"
                transport = _Transport()
                lifecycle = OrderLifecycle(self.repository, transport, now_provider=lambda at=at: at)
                intent = OrderIntent(
                    intent_id, "run-1", f"decision-{sequence}", "account-1", "mock",
                    "005930", "KRX", OrderSide.BUY, 1, OrderType.LIMIT, 70_000,
                    at - timedelta(seconds=1), at + timedelta(minutes=1), "policy-v1",
                )
                lifecycle.queue(intent)
                record = lifecycle.submit(intent_id, _account())
                self.assertEqual(OrderState.REJECTED, record.state)
                self.assertEqual(0, transport.submit_calls)
                rejection = self.repository.events(intent_id)[-1]
                self.assertEqual("PRECHECK_REJECTED", rejection["event_type"])
                self.assertTrue(rejection["reason"].startswith("UNSUPPORTED|"))
                self.assertIn(f"schedule={CURRENT_SCHEDULE_VERSION}", rejection["reason"])

    def test_manual_mock_krx_after_limit_reaches_transport_as_probe(self) -> None:
        kst = timezone(timedelta(hours=9))
        now = datetime(2026, 9, 14, 16, 0, tzinfo=kst)
        transport = _Transport()
        lifecycle = OrderLifecycle(self.repository, transport, now_provider=lambda: now)
        intent = OrderIntent(
            "after-probe", "run-1", "decision-after", "account-1", "mock",
            "005930", "KRX", OrderSide.BUY, 1, OrderType.LIMIT, 70_000,
            now - timedelta(seconds=1), now + timedelta(minutes=1),
            "manual-mock-krx-after-limit-probe/v1",
        )
        lifecycle.queue(intent)

        record = lifecycle.submit(intent.intent_id, _account())

        self.assertEqual(OrderState.ACCEPTED, record.state)
        self.assertEqual(1, transport.submit_calls)

    def test_session_close_does_not_expire_or_resubmit_and_broker_reconciliation_stays_open(self) -> None:
        kst = timezone(timedelta(hours=9))
        clock = [datetime(2026, 9, 14, 15, 19, 59, tzinfo=kst)]
        transport = _Transport()
        lifecycle = OrderLifecycle(self.repository, transport, now_provider=lambda: clock[0])
        intent = OrderIntent(
            "close-boundary", "run-1", "decision-close", "account-1", "mock", "005930", "KRX",
            OrderSide.BUY, 3, OrderType.LIMIT, 70_000,
            clock[0] - timedelta(seconds=1), clock[0] + timedelta(minutes=10), "policy-v1",
        )
        lifecycle.queue(intent)
        accepted = lifecycle.submit(intent.intent_id, _account())
        self.assertEqual(OrderState.ACCEPTED, accepted.state)
        self.assertEqual(1, transport.submit_calls)

        clock[0] = datetime(2026, 9, 14, 16, 0, tzinfo=kst)
        unchanged = lifecycle.submit(intent.intent_id, _account())
        self.assertEqual(OrderState.ACCEPTED, unchanged.state)
        self.assertEqual(1, transport.submit_calls)

        cancelled = lifecycle.cancel(intent.intent_id)
        self.assertEqual(OrderState.CANCEL_PENDING, cancelled.state)
        self.assertEqual(1, transport.cancel_calls)
        broker_closed = lifecycle.reconcile(intent.intent_id, BrokerOrderSnapshot(
            "broker-1", "account-1", "005930", OrderState.CANCELLED, 0, 0,
            clock[0] + timedelta(seconds=1), (),
        ))
        self.assertEqual(OrderState.CANCELLED, broker_closed.state)
        self.assertEqual(0, broker_closed.filled_quantity)

        late_fill = lifecycle.reconcile(intent.intent_id, BrokerOrderSnapshot(
            "broker-1", "account-1", "005930", OrderState.CANCELLED, 1, 0,
            clock[0] + timedelta(seconds=2), (
                BrokerFill("late-fill-1", 1, 70_000, clock[0] - timedelta(minutes=31)),
            ),
        ))
        event_count = len(self.repository.events(intent.intent_id))
        duplicate = lifecycle.reconcile(intent.intent_id, BrokerOrderSnapshot(
            "broker-1", "account-1", "005930", OrderState.CANCELLED, 1, 0,
            clock[0] + timedelta(seconds=2), (),
        ))
        self.assertEqual(OrderState.CANCELLED, late_fill.state)
        self.assertEqual(1, late_fill.filled_quantity)
        self.assertEqual(1, duplicate.filled_quantity)
        self.assertEqual(event_count, len(self.repository.events(intent.intent_id)))

        restarted_repository = ExecutionRepository(
            SQLiteQueryStore(Path(self.directory.name) / "central.sqlite3"),
        )
        restarted = OrderLifecycle(restarted_repository, _Transport(), now_provider=lambda: clock[0])
        after_restart = restarted.reconcile(intent.intent_id, BrokerOrderSnapshot(
            "broker-1", "account-1", "005930", OrderState.CANCELLED, 1, 0,
            clock[0] + timedelta(seconds=2), (),
        ))
        self.assertEqual(OrderState.CANCELLED, after_restart.state)
        self.assertEqual(1, after_restart.filled_quantity)
        self.assertEqual(event_count, len(restarted_repository.events(intent.intent_id)))


if __name__ == "__main__":
    unittest.main()
