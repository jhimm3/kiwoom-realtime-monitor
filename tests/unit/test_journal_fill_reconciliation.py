from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timedelta, timezone

from kiwoom_monitor.application.trade_history_query_service import (
    TradeHistoryQueryService,
    reconcile_trade_fills,
)
from kiwoom_monitor.application.trade_history_service import TradeFill
from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope


KST = timezone(timedelta(hours=9))
DAY = datetime(2026, 9, 16)


def _scope() -> AccountScope:
    return AccountScope("kiwoom", AccountEnvironment.MOCK, str(uuid.uuid4()))


def _summary(
    scope: AccountScope,
    *,
    order: str = "order-1",
    side: str = "매수",
    quantity: int = 3,
    price: int = 100,
    at: datetime = DAY + timedelta(hours=9),
) -> TradeFill:
    return TradeFill(
        order, "005930", "삼성전자", side, at, quantity, price,
        origin_scope=scope,
    )


def _detail(
    scope: AccountScope,
    *,
    execution: str = "execution-1",
    source: str | None = None,
    run: str = "run-1",
    order: str = "order-1",
    side: str = "BUY",
    quantity: int = 3,
    price: int = 100,
    at: datetime = DAY.replace(tzinfo=KST) + timedelta(hours=9),
) -> dict[str, object]:
    return {
        "origin_broker": scope.broker,
        "origin_environment": scope.environment.value,
        "origin_account_ref": scope.account_ref,
        "canonical_account_ref": scope.account_ref,
        "source_event_id": source or f"source-{execution}",
        "broker_execution_id": execution,
        "broker_order_id": order,
        "intent_id": "intent-1",
        "run_id": run,
        "decision_id": "decision-1",
        "stock_code": "005930",
        "venue": "KRX",
        "side": side,
        "occurred_at": at.isoformat(),
        "quantity": quantity,
        "price": price,
    }


class JournalFillReconciliationTests(unittest.TestCase):
    def test_exact_match_uses_only_detailed_fills(self) -> None:
        scope = _scope()
        summary = _summary(scope)
        details = (
            _detail(scope, execution="execution-1", quantity=1),
            _detail(scope, execution="execution-2", quantity=2),
        )

        result = reconcile_trade_fills((summary,), details, scope)

        self.assertEqual("EXACT", result.groups[0].status)
        self.assertTrue(result.groups[0].fill_confirmed)
        self.assertEqual(2, len(result.fills))
        self.assertEqual(3, sum(fill.quantity for fill in result.fills))
        self.assertEqual(300, sum(fill.quantity * fill.price for fill in result.fills))

    def test_partial_detail_does_not_add_summary_quantity(self) -> None:
        scope = _scope()

        result = reconcile_trade_fills(
            (_summary(scope, quantity=3),),
            (_detail(scope, quantity=1),),
            scope,
        )

        group = result.groups[0]
        self.assertEqual("PARTIAL", group.status)
        self.assertFalse(group.fill_confirmed)
        self.assertEqual((3, 1), (group.summary_quantity, group.detailed_quantity))
        self.assertEqual(1, sum(fill.quantity for fill in result.fills))

    def test_same_quantity_with_different_amount_is_conflict(self) -> None:
        scope = _scope()

        result = reconcile_trade_fills(
            (_summary(scope, quantity=2, price=100),),
            (_detail(scope, quantity=2, price=101),),
            scope,
        )

        self.assertEqual("CONFLICT", result.groups[0].status)
        self.assertTrue(result.has_unconfirmed)
        self.assertEqual(202, sum(fill.quantity * fill.price for fill in result.fills))

    def test_summary_only_and_detail_only_remain_separate(self) -> None:
        scope = _scope()

        result = reconcile_trade_fills(
            (_summary(scope, order="summary-only"),),
            (_detail(scope, order="detail-only"),),
            scope,
        )

        by_order = {group.broker_order_id: group for group in result.groups}
        self.assertEqual("SUMMARY_ONLY", by_order["summary-only"].status)
        self.assertEqual("DETAIL_ONLY", by_order["detail-only"].status)
        self.assertFalse(by_order["summary-only"].fill_confirmed)
        self.assertTrue(by_order["detail-only"].fill_confirmed)
        self.assertEqual(6, sum(fill.quantity for fill in result.fills))

    def test_run_change_does_not_duplicate_same_broker_execution(self) -> None:
        scope = _scope()
        first = _detail(scope, source="source-1", run="run-1")
        rebound = _detail(scope, source="source-2", run="run-2")

        result = reconcile_trade_fills((_summary(scope),), (first, rebound), scope)

        self.assertEqual("EXACT", result.groups[0].status)
        self.assertEqual(1, len(result.groups[0].detailed_fills))
        self.assertEqual(3, sum(fill.quantity for fill in result.fills))

    def test_conflicting_duplicate_execution_is_not_double_counted(self) -> None:
        scope = _scope()
        first = _detail(scope, source="source-1", quantity=3)
        conflict = _detail(scope, source="source-2", run="run-2", quantity=4)

        result = reconcile_trade_fills((_summary(scope),), (first, conflict), scope)

        self.assertEqual("CONFLICT", result.groups[0].status)
        self.assertEqual(3, sum(fill.quantity for fill in result.fills))

    def test_different_account_evidence_is_rejected(self) -> None:
        scope = _scope()
        other = _scope()

        with self.assertRaisesRegex(ValueError, "crossed"):
            reconcile_trade_fills((_summary(scope),), (_detail(other),), scope)

    def test_service_reads_one_account_and_keeps_costs_separate(self) -> None:
        scope = _scope()
        repository = _Repository(scope)

        result = TradeHistoryQueryService(repository).load_reconciled(
            DAY, DAY + timedelta(days=1), scope,
        )

        self.assertEqual("EXACT", result.reconciliation.groups[0].status)
        self.assertEqual((), result.costs)
        self.assertEqual(scope, repository.projection_scope)
        self.assertEqual(DAY - timedelta(days=365), repository.projection_range[0])


class _Repository:
    def __init__(self, scope: AccountScope) -> None:
        self.scope = scope
        self.projection_scope: AccountScope | None = None
        self.projection_range: tuple[datetime, datetime] | None = None

    def load_fills_with_entry_context(
        self, start, end, lookback_days=365, account_scope=None,
    ):
        if account_scope != self.scope:
            raise AssertionError("wrong account")
        return (_summary(self.scope),)

    def load_execution_fill_projections(self, account_scope, start=None, end=None):
        self.projection_scope = account_scope
        self.projection_range = (start, end)
        return (_detail(self.scope),)

    def load_trade_costs(self, start, end, account_scope=None):
        if account_scope != self.scope:
            raise AssertionError("wrong account")
        return ()

    def load_group_overrides(self):
        return {}

    def load_review(self, group_id):
        raise AssertionError("not used")


if __name__ == "__main__":
    unittest.main()
