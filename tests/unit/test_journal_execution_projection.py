from __future__ import annotations

import sqlite3
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kiwoom_monitor.application.journal_enrichment import (
    JOURNAL_EXECUTION_PROJECTION,
    journal_execution_projection_page,
    sync_journal_execution_projection,
)
from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope
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
            _event(
                self.scope, 2, "FILL", source_event_id="event-conflict",
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
