from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.domain.order_contract import AccountSnapshot, OrderIntent, OrderSide, OrderState, OrderType, order_event
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
    def test_kst_snapshot_receipt_preserves_the_legacy_id_and_duplicate_noop(self):
        import json
        from kiwoom_monitor.domain.time_basis import KST
        store = SQLiteQueryStore(Path(":memory:")); store.initialize(); self.addCleanup(store.close)
        repository = ExecutionRepository(store)
        snapshot = AccountSnapshot("account", 1000000, 0, {}, NOW)
        self.assertTrue(repository.save_account_snapshot("mock", snapshot, NOW.astimezone(KST)))
        self.assertFalse(repository.save_account_snapshot("mock", snapshot, NOW))
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            repository.save_account_snapshot("mock", snapshot, NOW.replace(tzinfo=None))
        with store._connection() as connection:
            rows = connection.execute(
                "SELECT received_at,document_json FROM central_execution_account_snapshots").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], NOW.astimezone(KST).isoformat())
        self.assertEqual(json.loads(rows[0][1])["received_at"], NOW.astimezone(KST).isoformat())

    def test_runtime_lease_compares_instants_across_existing_utc_and_kst_rows(self):
        from kiwoom_monitor.central_server.market_observations import KST
        store = SQLiteQueryStore(Path(':memory:')); store.initialize(); self.addCleanup(store.close)
        repository = ExecutionRepository(store)
        self.assertTrue(repository.claim_runtime('mock', 'account', 'run', 'old', NOW, 60))
        self.assertFalse(repository.claim_runtime('mock', 'account', 'run', 'new', NOW.astimezone(KST), 60))
        self.assertTrue(repository.claim_runtime('mock', 'account', 'run', 'new',
            (NOW + timedelta(seconds=61)).astimezone(KST), 60))
        # Test the reverse representation too: neither offset may steal a live lease.
        self.assertFalse(repository.claim_runtime('mock', 'account', 'run', 'old', NOW + timedelta(seconds=62), 60))

    def test_account_active_view_includes_eventless_queued_and_other_runs_without_writes(self):
        store = SQLiteQueryStore(Path(":memory:")); store.initialize(); self.addCleanup(store.close)
        repository = ExecutionRepository(store)
        for value in (_scoped_intent("queued", "run-current", "account-1"),
                      _scoped_intent("old", "run-retired", "account-1"),
                      _scoped_intent("other", "run-other", "account-2")):
            repository.create(value)
        self.assertEqual((), repository.account_events("mock", "account-1").events)
        sql = []; connect = store._connect
        def traced():
            connection = connect(); connection.set_trace_callback(sql.append); return connection
        with patch.object(store, "_connect", side_effect=traced) as reads:
            records, complete = repository.account_active_intents("account-1")
            self.assertEqual(1, reads.call_count)
        self.assertTrue(complete)
        self.assertEqual({"queued", "old"}, {r.intent.intent_id for r in records})
        self.assertEqual(0, sum(statement == "COMMIT" for statement in sql))
        self.assertEqual(1, len(repository.active_intents("mock", "account-1", "run-current")))

    def test_account_active_view_budget_is_explicit_and_never_returns_confirmed_empty(self):
        store = SQLiteQueryStore(Path(":memory:")); store.initialize(); self.addCleanup(store.close)
        repository = ExecutionRepository(store)
        for index in range(401):
            repository.create(_scoped_intent(f"queued-{index:04}", f"run-{index}", "account-1"))
        records, complete = repository.account_active_intents("account-1")
        self.assertFalse(complete)
        self.assertEqual(400, len(records))

    def test_postgres_account_active_reader_has_account_scope_bound_and_native_error_cleanup(self):
        from tests.unit.test_postgres_access import PostgresAccessTests, FakeCursor
        from kiwoom_monitor.central_server.database import PostgresQueryStore
        connection = PostgresAccessTests()._native_context_connection()
        store = PostgresQueryStore("postgresql://unused")
        captured = []; execute = FakeCursor.execute
        def traced(cursor, sql, parameters=()):
            captured.append((sql, parameters)); return execute(cursor, sql, parameters)
        with patch.object(store, "_connect", return_value=connection), patch.object(FakeCursor, "execute", traced):
            self.assertEqual([], store.load_account_active_execution_intents("mock", "account-1", 401))
        self.assertNotIn("run_id=", captured[0][0])
        self.assertEqual(("mock", "account-1", 401), captured[0][1])
        self.assertEqual((1, 0, 1), (connection.commits, connection.rollbacks, connection.closes))
        failing = PostgresAccessTests()._native_context_connection(); failing.fail_execute = 1
        with patch.object(store, "_connect", return_value=failing):
            with self.assertRaises(ValueError): store.load_account_active_execution_intents("mock", "account-1", 401)
        self.assertEqual((0, 1, 1), (failing.commits, failing.rollbacks, failing.closes))

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
