from __future__ import annotations

import sqlite3
import tempfile
import unittest
import uuid
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kiwoom_monitor.application.journal_enrichment import (
    JOURNAL_EXECUTION_PROJECTION,
    journal_execution_projection_page,
    sync_journal_execution_projection,
)
from kiwoom_monitor.application.trade_history_query_service import TradeHistoryQueryService
from kiwoom_monitor.application.trade_history_service import TradeFill
from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope, OrderPlanRef
from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository


KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 16, 10, 0, tzinfo=KST)


def _scope() -> AccountScope:
    return AccountScope("kiwoom", AccountEnvironment.MOCK, str(uuid.uuid4()))


def _event(
    scope: AccountScope,
    sequence: int,
    event_type: str,
    *,
    source_event_id: str | None = None,
    intent_id: str = "intent-1",
    run_id: str = "run-1",
    order_id: str = "order-1",
    execution_id: str = "",
    quantity: int = 1,
    price: int = 70_000,
    occurred_at: datetime = NOW,
) -> dict[str, object]:
    return {
        "accepted_sequence": sequence,
        "source_event_id": source_event_id or f"event-{sequence}",
        "intent_id": intent_id,
        "run_id": run_id,
        "decision_id": "decision-1",
        "account_ref": scope.account_ref,
        "environment": "mock",
        "symbol": "005930",
        "venue": "KRX",
        "side": "BUY",
        "event_type": event_type,
        "state": "PARTIALLY_FILLED",
        "occurred_at": occurred_at.isoformat(),
        "received_at": (occurred_at + timedelta(milliseconds=100)).isoformat(),
        "broker_order_id": order_id,
        "broker_execution_id": execution_id,
        "quantity": quantity,
        "price": price,
        "broker_as_of": (occurred_at + timedelta(milliseconds=50)).isoformat(),
    }


def _page(scope: AccountScope, start: int, events: list[dict[str, object]], has_more=False):
    next_cursor = int(events[-1]["accepted_sequence"]) if events else start
    return journal_execution_projection_page(
        {"events": events, "next_cursor": next_cursor, "has_more": has_more},
        from_cursor=start,
        account_scope=scope,
    )


class _Source:
    def __init__(self, pages: list[dict[str, object]]) -> None:
        self.pages = list(pages)
        self.calls: list[tuple[int, int]] = []

    def load_mock_execution_events(self, *, after_sequence=0, limit=500):
        self.calls.append((after_sequence, limit))
        return self.pages.pop(0)


class JournalExecutionProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "journal.sqlite3"
        self.repository = JournalRepository(self.path)
        self.scope = _scope()

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_same_second_detailed_fills_are_distinct_and_page_replay_is_idempotent(self) -> None:
        page = _page(self.scope, 0, [
            _event(self.scope, 1, "FILL", execution_id="fill-a", quantity=1),
            _event(self.scope, 2, "FILL", execution_id="fill-b", quantity=2),
        ])

        first = self.repository.project_execution_event_page(page, NOW)
        replay = self.repository.project_execution_event_page(page, NOW + timedelta(seconds=1))
        rows = self.repository.load_execution_fill_projections(self.scope)

        self.assertEqual((2, 2, 0), (
            first.inserted_event_count, first.detailed_fill_count, first.aggregate_event_count,
        ))
        self.assertEqual(0, replay.inserted_event_count)
        self.assertEqual(2, len(rows))
        self.assertEqual({"fill-a", "fill-b"}, {row["broker_execution_id"] for row in rows})
        self.assertEqual(2, self.repository.load_execution_projection_cursor(
            JOURNAL_EXECUTION_PROJECTION, self.scope,
        ))

    def test_aggregate_then_late_details_resolve_missing_without_double_quantity(self) -> None:
        aggregate = _page(self.scope, 0, [
            _event(
                self.scope, 1, "BROKER_FILL_AGGREGATE", execution_id="",
                quantity=3, price=0,
            ),
        ])
        first = self.repository.project_execution_event_page(aggregate, NOW)
        details = _page(self.scope, 1, [
            _event(self.scope, 2, "FILL", execution_id="fill-a", quantity=1),
            _event(self.scope, 3, "FILL", execution_id="fill-b", quantity=2),
        ])
        second = self.repository.project_execution_event_page(details, NOW + timedelta(seconds=1))

        self.assertEqual({"intent-1": 3}, first.unresolved_quantity_by_intent)
        self.assertEqual({"intent-1": 0}, second.unresolved_quantity_by_intent)
        self.assertEqual(3, sum(
            int(row["quantity"]) for row in self.repository.load_execution_fill_projections(self.scope)
        ))

    def test_run_id_does_not_make_the_same_broker_fill_new(self) -> None:
        first = _page(self.scope, 0, [
            _event(self.scope, 1, "FILL", execution_id="fill-a", quantity=1),
        ])
        self.repository.project_execution_event_page(first, NOW)
        rebound = _page(self.scope, 1, [
            _event(
                self.scope, 2, "FILL", source_event_id="event-rebound",
                intent_id="intent-new", run_id="run-new", execution_id="fill-a", quantity=1,
            ),
        ])

        result = self.repository.project_execution_event_page(rebound, NOW + timedelta(seconds=1))

        self.assertEqual(0, result.inserted_event_count)
        self.assertEqual(1, len(self.repository.load_execution_fill_projections(self.scope)))
        self.assertEqual(2, result.cursor)

    def test_conflicting_fill_rolls_back_cursor_and_rows(self) -> None:
        first = _page(self.scope, 0, [
            _event(self.scope, 1, "FILL", execution_id="fill-a", quantity=1),
        ])
        self.repository.project_execution_event_page(first, NOW)
        conflict = _page(self.scope, 1, [
            _event(self.scope, 2, "FILL", execution_id="fill-new", quantity=1),
            _event(
                self.scope, 3, "FILL", source_event_id="event-conflict",
                run_id="run-new", execution_id="fill-a", quantity=2,
            ),
        ])

        with self.assertRaises(sqlite3.IntegrityError):
            self.repository.project_execution_event_page(conflict, NOW + timedelta(seconds=1))

        self.assertEqual(1, self.repository.load_execution_projection_cursor(
            JOURNAL_EXECUTION_PROJECTION, self.scope,
        ))
        self.assertEqual(1, len(self.repository.load_execution_fill_projections(self.scope)))

    def test_cursor_gap_and_cross_account_page_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "cursor gap"):
            self.repository.project_execution_event_page(
                _page(self.scope, 2, [_event(self.scope, 3, "FILL", execution_id="fill-a")]),
                NOW,
            )
        other = _scope()
        with self.assertRaisesRegex(ValueError, "crossed account"):
            journal_execution_projection_page(
                {
                    "events": [_event(other, 1, "FILL", execution_id="fill-a")],
                    "next_cursor": 1,
                    "has_more": False,
                },
                from_cursor=0,
                account_scope=self.scope,
            )

    def test_non_fill_events_advance_cursor_without_creating_fill_evidence(self) -> None:
        page = _page(self.scope, 0, [
            _event(self.scope, 1, "ORDER_ACCEPTED", execution_id="", quantity=0, price=0),
        ])

        result = self.repository.project_execution_event_page(page, NOW)

        self.assertEqual(1, result.cursor)
        self.assertEqual(0, result.inserted_event_count)
        self.assertEqual((), self.repository.load_execution_fill_projections(self.scope))

    def test_same_broker_ids_on_another_day_or_account_are_distinct(self) -> None:
        tomorrow = NOW + timedelta(days=1)
        self.repository.project_execution_event_page(_page(self.scope, 0, [
            _event(self.scope, 1, "FILL", execution_id="fill-a"),
        ]), NOW)
        self.repository.project_execution_event_page(_page(self.scope, 1, [
            _event(self.scope, 2, "FILL", source_event_id="event-next-day",
                   execution_id="fill-a", occurred_at=tomorrow),
        ]), tomorrow)
        other = _scope()
        self.repository.project_execution_event_page(_page(other, 0, [
            _event(other, 1, "FILL", source_event_id="event-other", execution_id="fill-a"),
        ]), NOW)

        self.assertEqual(2, len(self.repository.load_execution_fill_projections(self.scope)))
        self.assertEqual(1, len(self.repository.load_execution_fill_projections(other)))

    def test_projection_read_filters_kst_period_and_accepts_canonical_scope(self) -> None:
        canonical = _scope()
        self.repository.project_execution_event_page(
            journal_execution_projection_page(
                {
                    "events": [
                        _event(self.scope, 1, "FILL", execution_id="fill-a"),
                        _event(
                            self.scope, 2, "FILL", execution_id="fill-b",
                            occurred_at=NOW + timedelta(days=1),
                        ),
                    ],
                    "next_cursor": 2,
                    "has_more": False,
                },
                from_cursor=0,
                account_scope=self.scope,
                canonical_scope=canonical,
            ),
            NOW,
        )

        rows = self.repository.load_execution_fill_projections(
            canonical,
            datetime(2026, 9, 16),
            datetime(2026, 9, 17),
        )

        self.assertEqual(("fill-a",), tuple(row["broker_execution_id"] for row in rows))
        self.assertEqual(self.scope.account_ref, rows[0]["origin_account_ref"])
        self.assertEqual(canonical.account_ref, rows[0]["canonical_account_ref"])

    def test_projection_rows_and_cursor_survive_repository_restart(self) -> None:
        self.repository.project_execution_event_page(_page(self.scope, 0, [
            _event(self.scope, 1, "FILL", execution_id="fill-a"),
        ]), NOW)

        restarted = JournalRepository(self.path)

        self.assertEqual(1, restarted.load_execution_projection_cursor(
            JOURNAL_EXECUTION_PROJECTION, self.scope,
        ))
        self.assertEqual("fill-a", restarted.load_execution_fill_projections(
            self.scope,
        )[0]["broker_execution_id"])

    def test_member_fills_keep_native_attribution_across_late_detail_restart_and_query(self) -> None:
        member_a = "mock_automation_spec_" + "a" * 64
        member_b = "mock_automation_spec_" + "b" * 64
        plan_a = {"plan_id": "leader-plan", "policy_ref": "leader/v2", "expected_revision": 2}
        plan_b = {"plan_id": "close-plan", "policy_ref": "close/v1", "expected_revision": 3}

        def native(sequence, member, plan, order, intent, event_type="FILL", quantity=1):
            return {
                **_event(self.scope, sequence, event_type, intent_id=intent, order_id=order,
                         execution_id=f"fill-{sequence}" if event_type == "FILL" else "",
                         quantity=quantity, price=70_000 if event_type == "FILL" else 0),
                "member_spec_id": member, "plan": plan,
            }

        aggregate = self.repository.project_execution_event_page(_page(self.scope, 0, [
            native(1, member_a, plan_a, "order-a", "intent-a", "BROKER_FILL_AGGREGATE", 3),
            native(2, member_b, plan_b, "order-b", "intent-b", "BROKER_FILL_AGGREGATE", 2),
        ]), NOW)
        self.assertEqual({"intent-a": 3, "intent-b": 2}, aggregate.unresolved_quantity_by_intent)
        details = _page(self.scope, 2, [
            native(3, member_a, plan_a, "order-a", "intent-a"),
            native(4, member_b, plan_b, "order-b", "intent-b", quantity=2),
            native(5, member_a, plan_a, "order-a", "intent-a", quantity=2),
        ])
        journal = JournalRepository(self.path)
        result = journal.project_execution_event_page(details, NOW + timedelta(seconds=1))
        self.assertEqual({"intent-a": 0, "intent-b": 0}, result.unresolved_quantity_by_intent)
        self.assertEqual(0, journal.project_execution_event_page(details, NOW).inserted_event_count)
        rows = JournalRepository(self.path).load_execution_fill_projections(self.scope)
        self.assertEqual([member_a, member_b, member_a], [row["member_spec_id"] for row in rows])
        self.assertEqual([plan_a, plan_b, plan_a], [row["plan"] for row in rows])
        self.assertEqual([NOW.isoformat()] * 3, [row["occurred_at"] for row in rows])

        at = NOW.replace(tzinfo=None)
        fills = (
            TradeFill("order-a", "005930", "삼성전자", "매수", at, 3, 70_000, origin_scope=self.scope),
            TradeFill("order-b", "005930", "삼성전자", "매수", at, 2, 70_000, origin_scope=self.scope),
        )
        journal.upsert_fills(fills, account_scope=self.scope)
        query = TradeHistoryQueryService(journal)
        visible = query.load(at, at + timedelta(hours=1), self.scope)
        self.assertEqual({fills[0]: member_a, fills[1]: member_b}, visible.execution_members)
        self.assertEqual({fills[0]: OrderPlanRef(**plan_a), fills[1]: OrderPlanRef(**plan_b)}, visible.execution_plans)
        reconciled = query.load_reconciled(at, at + timedelta(hours=1), self.scope)
        self.assertEqual(5, sum(fill.quantity for fill in reconciled.reconciliation.fills))
        self.assertEqual({member_a, member_b}, {
            detail.member_spec_id for group in reconciled.reconciliation.groups for detail in group.detailed_fills
        })

    def test_same_physical_fill_cannot_be_reassigned_to_another_member(self) -> None:
        first = {**_event(self.scope, 1, "FILL", execution_id="fill-a"), "member_spec_id": "member-a"}
        self.repository.project_execution_event_page(_page(self.scope, 0, [first]), NOW)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "conflicting content"):
            self.repository.project_execution_event_page(_page(self.scope, 1, [
                {**_event(self.scope, 2, "FILL", execution_id="fill-new"), "member_spec_id": "member-b"},
                {**_event(self.scope, 3, "FILL", execution_id="fill-a", run_id="other-run"),
                 "member_spec_id": "member-b"},
            ]), NOW)
        self.assertEqual(1, self.repository.load_execution_projection_cursor(JOURNAL_EXECUTION_PROJECTION, self.scope))
        [row] = self.repository.load_execution_fill_projections(self.scope)
        self.assertEqual(("fill-a", "member-a"), (row["broker_execution_id"], row["member_spec_id"]))

    def test_prior_entry_keeps_its_member_when_selected_period_only_contains_exit(self) -> None:
        tomorrow = NOW + timedelta(days=1)
        plan = {"plan_id": "overnight", "policy_ref": "close/v1", "expected_revision": 1}
        self.repository.project_execution_event_page(_page(self.scope, 0, [
            {**_event(self.scope, 1, "FILL", order_id="buy", execution_id="buy-fill", quantity=2),
             "member_spec_id": "member-a", "plan": plan},
            {**_event(self.scope, 2, "FILL", order_id="sell", execution_id="sell-fill", quantity=2,
                      occurred_at=tomorrow), "side": "SELL", "member_spec_id": "member-a", "plan": plan},
        ]), tomorrow)
        buy = TradeFill("buy", "005930", "삼성전자", "매수", NOW.replace(tzinfo=None), 2, 70_000, origin_scope=self.scope)
        sell = TradeFill("sell", "005930", "삼성전자", "매도", tomorrow.replace(tzinfo=None), 2, 70_000, origin_scope=self.scope)
        self.repository.upsert_fills((buy, sell), account_scope=self.scope)

        visible = TradeHistoryQueryService(self.repository).load(
            tomorrow.replace(tzinfo=None, hour=0), tomorrow.replace(tzinfo=None, hour=0) + timedelta(days=1), self.scope,
        )

        self.assertEqual({buy, sell}, set(visible.episodes[0].fills))
        self.assertEqual({buy: "member-a", sell: "member-a"}, visible.execution_members)
        self.assertEqual({buy: OrderPlanRef(**plan), sell: OrderPlanRef(**plan)}, visible.execution_plans)

    def test_unmatched_summary_is_not_labeled_with_member_ownership(self) -> None:
        self.repository.project_execution_event_page(_page(self.scope, 0, [{
            **_event(self.scope, 1, "FILL", execution_id="fill-a"), "member_spec_id": "member-a",
        }]), NOW)
        at = NOW.replace(tzinfo=None)
        summary = TradeFill("order-1", "005930", "삼성전자", "매수", at, 3, 70_000, origin_scope=self.scope)
        self.repository.upsert_fills((summary,), account_scope=self.scope)
        query = TradeHistoryQueryService(self.repository)
        visible = query.load(at, at + timedelta(hours=1), self.scope)
        self.assertEqual({}, getattr(visible, "execution_members", {}))
        self.assertEqual((summary,), visible.fills)
        [group] = query.load_reconciled(at, at + timedelta(hours=1), self.scope).reconciliation.groups
        self.assertEqual(("PARTIAL", "member-a"), (group.status, group.detailed_fills[0].member_spec_id))

    def test_member_extension_preserves_standalone_shape_physical_hash_and_strict_plan(self) -> None:
        source = _event(self.scope, 1, "FILL", execution_id="fill-a")
        standalone = _page(self.scope, 0, [source]).events[0]
        member = _page(self.scope, 0, [{**source, "member_spec_id": "member-a"}]).events[0]
        self.assertNotIn("member_spec_id", asdict(standalone))
        self.assertEqual((standalone.fill_identity, standalone.content_hash), (member.fill_identity, member.content_hash))
        for invalid in (None, "", "  ", 1):
            with self.subTest(member=invalid), self.assertRaisesRegex(ValueError, "member reference"):
                _page(self.scope, 0, [{**source, "member_spec_id": invalid}])
        with self.assertRaisesRegex(ValueError, "plan reference"):
            _page(self.scope, 0, [{**source, "plan": {
                "plan_id": "plan", "policy_ref": "policy/v1", "expected_revision": 1,
                "member_spec_id": "member-a",
            }}])

    def test_v11_projection_preserves_legacy_row_and_shows_only_matched_plan(self) -> None:
        self.repository.project_execution_event_page(_page(self.scope, 0, [
            _event(self.scope, 1, "FILL", order_id="legacy-order", execution_id="legacy-fill"),
        ]), NOW)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("ALTER TABLE journal_execution_event_projections DROP COLUMN order_plan_json")
            connection.execute("DELETE FROM journal_schema_migrations WHERE version=12")
        journal = JournalRepository(self.path)
        self.assertEqual(1, journal.load_execution_projection_cursor(JOURNAL_EXECUTION_PROJECTION, self.scope))
        self.assertNotIn("plan", journal.load_execution_fill_projections(self.scope)[0])

        plan = {"plan_id": "close-plan", "policy_ref": "close_confirm/v1", "expected_revision": 3}
        new = _page(self.scope, 1, [{
            **_event(self.scope, 2, "FILL", execution_id="planned-fill"), "plan": plan,
        }])
        journal.project_execution_event_page(new, NOW)
        journal.project_execution_event_page(new, NOW)
        [legacy, planned] = JournalRepository(self.path).load_execution_fill_projections(self.scope)
        self.assertNotIn("plan", legacy)
        self.assertEqual(plan, planned["plan"])

        query = TradeHistoryQueryService(journal)
        fill_at = NOW.replace(tzinfo=None)
        summary = TradeFill("order-1", "005930", "삼성전자", "매수", fill_at, 1, 70_000, origin_scope=self.scope)
        self.assertEqual({}, query.load(fill_at, fill_at + timedelta(hours=1), self.scope).execution_plans)
        journal.upsert_fills((summary,), account_scope=self.scope)
        visible = query.load(fill_at, fill_at + timedelta(hours=1), self.scope)
        self.assertEqual((summary,), visible.fills)
        self.assertEqual({summary: OrderPlanRef(**plan)}, visible.execution_plans)

    def test_v12_member_migration_is_atomic_preserves_existing_evidence_and_does_not_guess_attribution(self) -> None:
        self.repository.project_execution_event_page(_page(self.scope, 0, [
            _event(self.scope, 1, "FILL", execution_id="old-fill"),
        ]), NOW)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("ALTER TABLE journal_execution_event_projections DROP COLUMN member_spec_id")
            connection.execute("DELETE FROM journal_schema_migrations WHERE version=13")
            before = connection.execute("SELECT * FROM journal_execution_event_projections").fetchall()
            states = connection.execute("SELECT * FROM journal_execution_projection_states").fetchall()
            receipts = connection.execute("SELECT * FROM journal_schema_migrations ORDER BY version").fetchall()
            connection.execute("CREATE TRIGGER reject_v13 BEFORE INSERT ON journal_schema_migrations WHEN NEW.version=13 BEGIN SELECT RAISE(ABORT,'member receipt rejected'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "member receipt rejected"):
            JournalRepository(self.path)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            self.assertNotIn("member_spec_id", {row[1] for row in connection.execute("PRAGMA table_info(journal_execution_event_projections)")})
            self.assertEqual(before, connection.execute("SELECT * FROM journal_execution_event_projections").fetchall())
            self.assertEqual(receipts, connection.execute("SELECT * FROM journal_schema_migrations ORDER BY version").fetchall())
            connection.execute("DROP TRIGGER reject_v13")
        journal = JournalRepository(self.path)
        JournalRepository(self.path)
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual([(*row, None) for row in before], connection.execute("SELECT * FROM journal_execution_event_projections").fetchall())
            self.assertEqual(states, connection.execute("SELECT * FROM journal_execution_projection_states").fetchall())
            self.assertEqual(receipts, connection.execute("SELECT * FROM journal_schema_migrations WHERE version<=12 ORDER BY version").fetchall())
        [legacy] = journal.load_execution_fill_projections(self.scope)
        self.assertNotIn("member_spec_id", legacy)
        with self.assertRaises(sqlite3.IntegrityError):
            journal.project_execution_event_page(_page(self.scope, 1, [{
                **_event(self.scope, 2, "FILL", execution_id="old-fill"), "member_spec_id": "new-member",
            }]), NOW)
        self.assertEqual(1, journal.load_execution_projection_cursor(JOURNAL_EXECUTION_PROJECTION, self.scope))

    def test_cancellation_after_page_fetch_keeps_last_committed_cursor(self) -> None:
        source = _Source([
            {"events": [_event(self.scope, 1, "FILL", execution_id="fill-a")], "next_cursor": 1, "has_more": True},
            {"events": [_event(self.scope, 2, "FILL", execution_id="fill-b")], "next_cursor": 2, "has_more": False},
        ])

        result = sync_journal_execution_projection(
            source, self.repository, self.scope, now=NOW, cancel_requested=lambda: len(source.calls) == 2,
        )

        self.assertEqual((1, 1), (result.cursor, result.inserted_event_count))
        self.assertEqual(["fill-a"], [row["broker_execution_id"] for row in self.repository.load_execution_fill_projections(self.scope)])

    def test_bounded_sync_resumes_from_repository_cursor_across_source_pages(self) -> None:
        source = _Source([
            {
                "events": [_event(self.scope, 1, "FILL", execution_id="fill-a")],
                "next_cursor": 1,
                "has_more": True,
            },
            {
                "events": [_event(self.scope, 2, "FILL", execution_id="fill-b")],
                "next_cursor": 2,
                "has_more": False,
            },
        ])

        result = sync_journal_execution_projection(
            source, self.repository, self.scope, page_limit=1, now=NOW,
        )

        self.assertEqual([(0, 1), (1, 1)], source.calls)
        self.assertEqual((2, 2), (result.cursor, result.inserted_event_count))
        resumed = _Source([{"events": [], "next_cursor": 2, "has_more": False}])
        replay = sync_journal_execution_projection(
            resumed, JournalRepository(self.path), self.scope, page_limit=1, now=NOW,
        )
        self.assertEqual([(2, 1)], resumed.calls)
        self.assertEqual(0, replay.inserted_event_count)


if __name__ == "__main__":
    unittest.main()
