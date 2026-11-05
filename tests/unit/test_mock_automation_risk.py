from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

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
    BrokerOrderSnapshot, OrderSide, OrderState,
)
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import MockAccountRecovery
from kiwoom_monitor.infrastructure.persistence.execution_repository import AccountExecutionEvent, ExecutionBrokerLeg, ExecutionRecord
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
    def snapshot(self, events, costs, *, positions, observed, cost_query_complete=True, revision=1):
        recovery = MockAccountRecovery(AccountSnapshot(
            ACCOUNT, 1_000_000, 0, positions, observed,
        ), ())
        return build_mock_automation_risk_snapshot(
            recovery, events, costs, (), execution_run_id="run-a", binding_revision=3,
            reconciliation_revision=revision, observed_at=observed,
            broker_query_started_at=observed, broker_query_completed_at=observed,
            cost_query_complete=cost_query_complete,
        )

    def test_failed_cost_query_never_trusts_matching_cached_costs(self):
        now = datetime(2026, 9, 21, 1, tzinfo=UTC)
        values = (event(1, "buy", "BUY", 1, 100, now),
                  event(2, "sell", "SELL", 1, 130, now))
        cached = (cost(date(2026, 9, 21), "매수", 100, 1),
                  cost(date(2026, 9, 21), "매도", 130, 2))
        confirmed = self.snapshot(values, cached, positions={}, observed=now)
        self.assertEqual(27, confirmed.daily_net_pnl_won)
        unavailable = self.snapshot(values, cached, positions={}, observed=now,
                                    cost_query_complete=False)
        self.assertIsNone(unavailable.daily_net_pnl_won)
        self.assertIsNone(unavailable.daily_net_pnl_source)
        self.assertFalse(unavailable.cost_complete)
        self.assertTrue(unavailable.reconciliation_complete)
        self.assertEqual(("COST_QUERY_UNAVAILABLE",), unavailable.missing_cost_reasons)

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

    def test_owned_net_quantities_ignore_input_order_other_runs_and_oversold_basis(self):
        now = datetime(2026, 9, 21, 1, tzinfo=UTC)
        # Accepted-order ownership and chronological account FIFO deliberately differ.
        facts = (
            ("005930", "run-a", "BUY", 2, 100, 4),
            ("005930", "run-a", "SELL", 5, 120, 1),
            ("005930", "run-a", "BUY", 4, 101, 3),
            ("005930", "run-b", "BUY", 7, 90, 0),
            ("000660", "run-a", "BUY", 3, 200, 2),
            ("000660", "run-a", "SELL", 3, 210, 5),
            ("035420", "run-a", "SELL", 2, 300, 0),
            ("035420", "run-a", "BUY", 1, 290, 6),
            ("051910", "run-b", "BUY", 9, 400, 0),
            ("068270", "run-a", "BUY", 5, 500, 3),
        )
        values = tuple(replace(event(sequence, f"fill-{sequence}", side, quantity, price,
            now.replace(second=second), run=run), symbol=symbol)
            for sequence, (symbol, run, side, quantity, price, second) in enumerate(facts, 1))
        # Independent inventory totals: (2+4)-5=1, 3-3=0, 1-2=-1, other run=0, 5=5.
        expected = (("005930", 1), ("068270", 5))
        for ordered in (values, tuple(reversed(values)), tuple(sorted(values, key=lambda value: value.occurred_at))):
            with self.subTest(order=tuple(value.accepted_sequence for value in ordered)):
                snapshot = self.snapshot(ordered, (), positions={}, observed=now.replace(second=10))
                self.assertEqual(expected, snapshot.owned_position_quantities)
                self.assertEqual((("068270", 500),), snapshot.owned_entry_prices)
                self.assertIsNone(snapshot.daily_net_pnl_won)
                self.assertFalse(snapshot.reconciliation_complete)

    def test_fill_identity_is_scoped_to_physical_order_but_conflicting_same_order_is_rejected(self):
        now = datetime(2026, 9, 21, 1, tzinfo=UTC)
        first = event(1, 'shared', 'BUY', 1, 100, now)
        second = replace(event(2, 'shared', 'BUY', 2, 110, now), intent_id=first.intent_id)
        snapshot = self.snapshot((first, second), (cost(now.date(), '매수', 320, 0),),
                                 positions={'005930': 3}, observed=now)
        self.assertTrue(snapshot.reconciliation_complete)
        self.assertEqual((('005930', 3),), snapshot.owned_position_quantities)
        with self.assertRaisesRegex(ValueError, 'conflicting'):
            self.snapshot((first, replace(second, broker_order_id=first.broker_order_id)), (),
                          positions={'005930': 3}, observed=now)

    def test_child_detail_cannot_settle_original_order_aggregate_debt(self):
        now = datetime(2026, 9, 21, 1, tzinfo=UTC)
        aggregate = event(1, '', 'BUY', 2, 0, now, kind='BROKER_FILL_AGGREGATE')
        detail = replace(event(2, 'shared', 'BUY', 2, 100, now), intent_id=aggregate.intent_id)
        for physical, complete in ((detail.broker_order_id, False), (aggregate.broker_order_id, True)):
            with self.subTest(physical=physical):
                snapshot = self.snapshot((aggregate, replace(detail, broker_order_id=physical)),
                    (cost(now.date(), '매수', 200, 0),), positions={'005930': 2}, observed=now)
                self.assertEqual(complete, snapshot.reconciliation_complete)

    def test_zero_fill_known_buy_is_confirmed_while_unknown_duplicate_and_unowned_orders_are_not(self):
        from tests.unit.test_order_lifecycle import _intent
        now = datetime(2026, 9, 21, 1, tzinfo=UTC)
        intent = replace(_intent(), account_ref=ACCOUNT, run_id='run-a', quantity=2, limit_price=100)
        record = ExecutionRecord(intent, OrderState.ACCEPTED, 'accepted-buy', 0, (), None, now)
        order = BrokerOrderSnapshot('accepted-buy', ACCOUNT, intent.symbol, OrderState.ACCEPTED, 0, 2, now)
        for orders, records, expected in (((order,), (record,), True), ((order,), (), False),
                ((replace(order, state=OrderState.SUBMISSION_UNKNOWN),), (record,), False),
                ((order,), (replace(record, state=OrderState.CANCEL_PENDING),), False),
                ((replace(order, state=OrderState.CANCEL_PENDING),), (record,), False),
                ((order, order), (record,), False), ((replace(order, remaining_quantity=1),), (record,), False)):
            with self.subTest(orders=orders, mapped=bool(records)):
                recovery = MockAccountRecovery(AccountSnapshot(ACCOUNT, 1_000, 200, {}, now), orders)
                risk = build_mock_automation_risk_snapshot(recovery, (), (), records,
                    execution_run_id='run-a', binding_revision=3, reconciliation_revision=1,
                    observed_at=now, broker_query_started_at=now, broker_query_completed_at=now)
                self.assertEqual(expected, risk.reconciliation_complete)
                self.assertEqual(0 if expected else None, risk.daily_net_pnl_won)

    def test_pending_sells_use_only_active_physical_remainders(self):
        from tests.unit.test_order_lifecycle import _intent
        now = datetime(2026, 9, 21, 1, tzinfo=UTC)
        intent = replace(_intent(), account_ref=ACCOUNT, run_id='run-a', side=OrderSide.SELL, quantity=6)
        # The cancelled original still records its immutable allocation of six.
        original = ExecutionBrokerLeg('original', '', 6, 100, OrderState.CANCELLED, transferred_quantity=3)
        child = ExecutionBrokerLeg('child', 'original', 3, 99, OrderState.ACCEPTED)
        record = ExecutionRecord(intent, OrderState.ACCEPTED, 'original', 0, (), None, now, broker_legs=(original, child))
        recovery = MockAccountRecovery(AccountSnapshot(ACCOUNT, 1_000, 0, {'005930': 6}, now), ())
        risk = build_mock_automation_risk_snapshot(recovery, (event(1, 'buy', 'BUY', 6, 100, now),),
            (cost(now.date(), '매수', 600, 0),), (record,), execution_run_id='run-a', binding_revision=3,
            reconciliation_revision=1, observed_at=now, broker_query_started_at=now, broker_query_completed_at=now)
        self.assertEqual((('005930', 3),), risk.pending_sell_quantities)

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

    def test_overlapping_publishers_cannot_move_current_risk_backwards(self):
        now = datetime(2026, 9, 21, 1, tzinfo=UTC)
        first = self.snapshot((), (), positions={}, observed=now)
        for delayed_revision, competing_revision in ((2, 2), (2, 3), (3, 2)):
            with self.subTest(delayed=delayed_revision, competing=competing_revision), TemporaryDirectory() as directory:
                path = Path(directory) / "risk.sqlite3"
                store = SQLiteQueryStore(path)
                peer_store = SQLiteQueryStore(path)
                try:
                    store.initialize()
                    repository = ForwardEvaluationRepository(store)
                    peer = ForwardEvaluationRepository(peer_store)
                    repository.save_mock_automation_risk_snapshot(first)
                    delayed = self.snapshot((), (), positions={}, observed=now.replace(second=1),
                                            revision=delayed_revision)
                    competing = self.snapshot((), (), positions={}, observed=now.replace(second=2),
                                              revision=competing_revision)
                    insert = store.insert_immutable_document

                    def publish_peer_after_history(*args):
                        result = insert(*args)
                        self.assertTrue(peer.save_mock_automation_risk_snapshot(competing))
                        return result

                    with patch.object(store, "insert_immutable_document", side_effect=publish_peer_after_history):
                        if delayed_revision > competing_revision:
                            self.assertTrue(repository.save_mock_automation_risk_snapshot(delayed))
                        else:
                            with self.assertRaisesRegex(ValueError, "monotonically"):
                                repository.save_mock_automation_risk_snapshot(delayed)
                    latest = delayed if delayed_revision > competing_revision else competing
                    self.assertEqual(latest, repository.load_latest_mock_automation_risk(ACCOUNT))
                    self.assertCountEqual((first, delayed, competing),
                                          repository.load_mock_automation_risk_snapshots(ACCOUNT))
                finally:
                    peer_store.close()
                    store.close()


if __name__ == "__main__":
    unittest.main()
