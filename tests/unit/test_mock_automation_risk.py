from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from kiwoom_monitor.application.mock_automation_risk import (
    VERIFIED_DAILY_PNL_SOURCE,
    build_mock_automation_risk_snapshot,
)
from kiwoom_monitor.application.trade_cost_service import DailyTradeCost
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.domain.order_contract import (
    AccountEnvironment,
    AccountScope,
    AccountSnapshot,
)
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import MockAccountRecovery
from kiwoom_monitor.infrastructure.persistence.execution_repository import AccountExecutionEvent
from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import ForwardEvaluationRepository


ACCOUNT = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
SCOPE = AccountScope("kiwoom", AccountEnvironment.MOCK, ACCOUNT)
UTC = timezone.utc


def event(sequence, execution, side, quantity, price, occurred, *, account=ACCOUNT, run="run-a", kind="FILL"):
    return AccountExecutionEvent(
        sequence, f"event-{sequence}", f"intent-{sequence}", run, f"decision-{sequence}",
        account, "mock", "005930", "KRX", side, kind, "FILLED", occurred,
        occurred, broker_order_id=f"order-{sequence}", broker_execution_id=execution,
        quantity=quantity, price=price,
    )


def cost(day, side, gross, total):
    return DailyTradeCost(day, day, "005930", side, gross, gross, total, 0, total, origin_scope=SCOPE)


class MockAutomationRiskTests(unittest.TestCase):
    def snapshot(self, events, costs, *, positions, observed):
        recovery = MockAccountRecovery(AccountSnapshot(
            ACCOUNT, 1_000_000, 0, positions, observed,
        ), ())
        return build_mock_automation_risk_snapshot(
            recovery, events, costs, (), execution_run_id="run-a", binding_revision=3,
            reconciliation_revision=1, observed_at=observed,
            broker_query_started_at=observed, broker_query_completed_at=observed,
        )

    def test_prior_day_fifo_cost_is_carried_into_today_only(self):
        buy_time = datetime(2026, 9, 20, 1, tzinfo=UTC)
        sell_time = datetime(2026, 9, 21, 1, tzinfo=UTC)
        values = (
            event(1, "fill-buy", "BUY", 10, 100, buy_time),
            event(2, "fill-sell", "SELL", 5, 130, sell_time),
        )
        snapshot = self.snapshot(values, (
            cost(date(2026, 9, 20), "매수", 1_000, 10),
            cost(date(2026, 9, 21), "매도", 650, 5),
        ), positions={"005930": 5}, observed=sell_time)
        self.assertEqual(140, snapshot.daily_net_pnl_won)
        self.assertEqual(VERIFIED_DAILY_PNL_SOURCE, snapshot.daily_net_pnl_source)
        self.assertEqual((("005930", 5),), snapshot.owned_position_quantities)
        self.assertTrue(snapshot.reconciliation_complete)

    def test_missing_cost_and_unresolved_aggregate_stay_unknown(self):
        now = datetime(2026, 9, 21, 1, tzinfo=UTC)
        aggregate = event(2, "", "BUY", 2, 0, now, kind="BROKER_FILL_AGGREGATE")
        snapshot = self.snapshot((event(1, "fill-buy", "BUY", 2, 100, now), aggregate), (),
                                 positions={"005930": 4}, observed=now)
        self.assertIsNone(snapshot.daily_net_pnl_won)
        self.assertFalse(snapshot.cost_complete)
        self.assertFalse(snapshot.reconciliation_complete)
        self.assertTrue(any(reason.startswith("COST_MISSING:") for reason in snapshot.missing_cost_reasons))

    def test_duplicate_fill_identity_is_counted_once_and_account_scope_isolated(self):
        now = datetime(2026, 9, 21, 1, tzinfo=UTC)
        fill = event(1, "fill-buy", "BUY", 2, 100, now)
        snapshot = self.snapshot((fill, fill), (cost(date(2026, 9, 21), "매수", 200, 1),),
                                 positions={"005930": 2}, observed=now)
        self.assertEqual(0, snapshot.daily_net_pnl_won)
        with self.assertRaisesRegex(ValueError, "crossed"):
            self.snapshot((event(1, "foreign", "BUY", 1, 100, now, account=OTHER),), (),
                          positions={}, observed=now)

    def test_kst_date_rollover_and_repository_current_revision(self):
        before = datetime(2026, 9, 20, 14, 59, tzinfo=UTC)
        after = datetime(2026, 9, 20, 15, 1, tzinfo=UTC)
        snapshot = self.snapshot((), (), positions={}, observed=after)
        self.assertEqual(date(2026, 9, 21), snapshot.trading_date)
        store = SQLiteQueryStore(Path(":memory:")); store.initialize()
        try:
            repository = ForwardEvaluationRepository(store)
            self.assertTrue(repository.save_mock_automation_risk_snapshot(snapshot))
            self.assertFalse(repository.save_mock_automation_risk_snapshot(snapshot))
            self.assertEqual(snapshot, repository.load_latest_mock_automation_risk(ACCOUNT))
            other_recovery = MockAccountRecovery(
                AccountSnapshot(OTHER, 500_000, 0, {}, after), (),
            )
            other = build_mock_automation_risk_snapshot(
                other_recovery, (), (), (), execution_run_id="run-other",
                binding_revision=1, reconciliation_revision=1, observed_at=after,
                broker_query_started_at=after, broker_query_completed_at=after,
            )
            repository.save_mock_automation_risk_snapshot(other)
            self.assertEqual(snapshot, repository.load_latest_mock_automation_risk(ACCOUNT))
            self.assertEqual(other, repository.load_latest_mock_automation_risk(OTHER))
            older = self.snapshot((), (), positions={}, observed=before)
            with self.assertRaisesRegex(ValueError, "monotonically"):
                repository.save_mock_automation_risk_snapshot(older)
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
