from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.domain.order_contract import OrderIntent, OrderSide, OrderState, OrderType, order_event
from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository


NOW = datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc)


def _intent() -> OrderIntent:
    return OrderIntent(
        "intent-1", "run-1", "decision-1", "account-1", "mock", "005930", "KRX",
        OrderSide.BUY, 2, OrderType.LIMIT, 70_000, NOW, NOW + timedelta(minutes=1), "policy-v1",
    )


def _scoped_intent(intent_id: str, run_id: str, account_ref: str) -> OrderIntent:
    return OrderIntent(
        intent_id, run_id, "decision-" + intent_id, account_ref, "mock", "005930", "KRX",
        OrderSide.BUY, 2, OrderType.LIMIT, 70_000, NOW, NOW + timedelta(minutes=1), "policy-v1",
    )


class ExecutionRepositoryTests(unittest.TestCase):
    def test_intent_and_events_survive_repository_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            repository = ExecutionRepository(store)
            record = repository.create(_intent())
            event = order_event(
                record.intent.intent_id, "ORDER_ACCEPTED", OrderState.ACCEPTED, NOW, NOW,
                broker_order_id="broker-1",
            )
            repository.apply(record, event, broker_order_id="broker-1")

            restored = ExecutionRepository(SQLiteQueryStore(Path(directory) / "central.sqlite3"))
            loaded = restored.load("intent-1")
            by_broker_id = restored.find_by_broker_order_id(
                "mock", "account-1", "run-1", "broker-1",
            )
            wrong_run = restored.find_by_broker_order_id(
                "mock", "account-1", "older-run", "broker-1",
            )
            event_count = len(restored.events("intent-1"))

        self.assertIsNotNone(loaded)
        self.assertEqual(OrderState.ACCEPTED, loaded.state)
        self.assertEqual("broker-1", loaded.broker_order_id)
        self.assertEqual("intent-1", by_broker_id.intent.intent_id)
        self.assertIsNone(wrong_run)
        self.assertEqual(1, event_count)

    def test_duplicate_event_is_applied_once(self) -> None:
        store = SQLiteQueryStore(Path(":memory:"))
        store.initialize()
        self.addCleanup(store.close)
        repository = ExecutionRepository(store)
        record = repository.create(_intent())
        event = order_event(
            "intent-1", "FILL", OrderState.PARTIALLY_FILLED, NOW, NOW,
            broker_execution_id="fill-1", quantity=1, price=70_000,
        )
        record = repository.apply(record, event, filled_quantity=1, fill_ids=("fill-1",))
        duplicate = repository.apply(record, event, filled_quantity=2, fill_ids=("fill-1", "fill-1"))

        self.assertEqual(1, duplicate.filled_quantity)
        self.assertEqual(1, len(repository.events("intent-1")))

    def test_fill_evidence_fields_survive_repository_restart(self) -> None:
        store = SQLiteQueryStore(Path(":memory:"))
        store.initialize()
        self.addCleanup(store.close)
        repository = ExecutionRepository(store)
        record = repository.create(_intent())
        event = order_event(
            "intent-1", "BROKER_FILL_AGGREGATE", OrderState.PARTIALLY_FILLED, NOW, NOW,
            quantity=1, broker_as_of=NOW,
        )
        repository.apply(
            record, event, filled_quantity=1, detailed_filled_quantity=0,
            broker_reported_filled_quantity=1, last_broker_as_of=NOW,
        )
        loaded = repository.load("intent-1")
        self.assertIsNotNone(loaded)
        self.assertEqual(0, loaded.detailed_filled_quantity)
        self.assertEqual(1, loaded.broker_reported_filled_quantity)

    def test_runtime_lease_blocks_another_owner_until_expiry(self) -> None:
        store = SQLiteQueryStore(Path(":memory:"))
        store.initialize()
        self.addCleanup(store.close)
        repository = ExecutionRepository(store)
        self.assertTrue(repository.claim_runtime("mock", "account", "run", "owner-a", NOW, 60))
        self.assertFalse(repository.claim_runtime("mock", "account", "run", "owner-b", NOW, 60))
        self.assertFalse(repository.claim_runtime("mock", "account", "other-run", "owner-c", NOW, 60))
        self.assertTrue(repository.claim_runtime(
            "mock", "account", "run", "owner-b", NOW + timedelta(seconds=61), 60,
        ))

    def test_account_event_page_is_scoped_across_runs_and_resumes_by_cursor(self) -> None:
        store = SQLiteQueryStore(Path(":memory:"))
        store.initialize()
        self.addCleanup(store.close)
        repository = ExecutionRepository(store)
        for intent_id, run_id, account_ref in (
            ("a-old", "run-old", "account-a"),
            ("foreign", "run-1", "account-b"),
            ("a-new", "run-new", "account-a"),
        ):
            record = repository.create(_scoped_intent(intent_id, run_id, account_ref))
            event = order_event(
                intent_id, "FILL", OrderState.PARTIALLY_FILLED, NOW, NOW,
                broker_order_id="order-" + intent_id,
                broker_execution_id="fill-" + intent_id,
                quantity=1,
                price=70_000,
            )
            repository.apply(
                record, event, broker_order_id="order-" + intent_id,
                filled_quantity=1, detailed_filled_quantity=1,
                fill_ids=("fill-" + intent_id,),
            )

        first = repository.account_events("mock", "account-a", limit=1)
        second = repository.account_events(
            "mock", "account-a", after_sequence=first.next_cursor, limit=10,
        )
        replay = repository.account_events(
            "mock", "account-a", after_sequence=first.next_cursor, limit=10,
        )

        self.assertTrue(first.has_more)
        self.assertEqual(("run-old",), tuple(event.run_id for event in first.events))
        self.assertEqual(("run-new",), tuple(event.run_id for event in second.events))
        self.assertEqual(second, replay)
        self.assertFalse(second.has_more)
        self.assertGreater(second.next_cursor, first.next_cursor)
        self.assertNotIn("account-b", {event.account_ref for event in (*first.events, *second.events)})
        self.assertEqual("fill-a-new", second.events[0].broker_execution_id)

    def test_account_event_page_rejects_invalid_scope_and_cursor(self) -> None:
        store = SQLiteQueryStore(Path(":memory:"))
        store.initialize()
        self.addCleanup(store.close)
        repository = ExecutionRepository(store)
        with self.assertRaises(ValueError):
            repository.account_events("real", "account-a")
        with self.assertRaises(ValueError):
            repository.account_events("mock", "account-a", after_sequence=-1)


if __name__ == "__main__":
    unittest.main()
