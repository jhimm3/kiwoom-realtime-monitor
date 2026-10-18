from __future__ import annotations

import tempfile
import unittest
import json
import os
from dataclasses import asdict, replace
from contextlib import closing
from pathlib import Path

from kiwoom_monitor.application.theme_leadership import (
    ThemeLeadershipParameters,
    build_entry_thesis,
    evaluate_entry_thesis,
    evaluate_theme_leadership,
)
from kiwoom_monitor.infrastructure.persistence.research_repository import (
    RESEARCH_SCHEMA_VERSION,
    ResearchRepository,
)
from tests.unit.test_theme_leadership import DAY, initial_points, membership, point
from tests.unit.test_market_research_features import shadow_inputs
from kiwoom_monitor.application.market_theme_shadow import (
    STOCK_THESIS_SHADOW_VERSION, StockThesisShadowRequest,
    evaluate_market_theme_shadow, evaluate_stock_thesis_shadow,
)
from scripts.preview_market_theme_shadow import evaluate_document


PARAMETERS = ThemeLeadershipParameters(display_confirmation_seconds=3)


def first_evaluation():
    return evaluate_theme_leadership(
        membership(), initial_points(), as_of=f"{DAY}T09:00:20+09:00",
        venue="KRX", continuity_status="COMPLETE", parameters=PARAMETERS,
    )


def changed_evaluation(previous):
    switched = (*initial_points(),
        point("005930", 22, 105, 100), point("000660", 22, 102, 1800),
        point("005930", 29, 105, 100), point("000660", 29, 108, 2200),
    )
    candidate = evaluate_theme_leadership(
        membership(), switched, as_of=f"{DAY}T09:00:30+09:00",
        venue="KRX", continuity_status="COMPLETE", parameters=PARAMETERS,
        previous_state=previous.state,
    )
    return evaluate_theme_leadership(
        membership(), (*switched,
            point("005930", 33, 105, 100), point("000660", 33, 109, 2300),
        ), as_of=f"{DAY}T09:00:34+09:00", venue="KRX",
        continuity_status="COMPLETE", parameters=PARAMETERS,
        previous_state=candidate.state,
    )


class EntryThesisTests(unittest.TestCase):
    def test_original_leader_is_frozen_and_policies_make_separate_research_actions(self) -> None:
        first = first_evaluation()
        thesis = build_entry_thesis(
            run_id="run-1", symbol="005930", leadership=first,
            factor_refs=("factor-r1",), hypothesis_refs=("hypothesis-r1",),
            created_at=first.as_of, available_at=first.as_of,
        )
        changed = changed_evaluation(first)
        immediate = evaluate_entry_thesis(thesis, changed, policy_version="IMMEDIATE_EXIT")
        price_only = evaluate_entry_thesis(thesis, changed, policy_version="PRICE_ONLY")
        confirm = evaluate_entry_thesis(thesis, changed, policy_version="CONFIRM_THEN_EXIT")
        self.assertEqual("005930", thesis.leader_id)
        self.assertEqual("000660", changed.displayed_leader_id)
        self.assertEqual("INVALIDATED", immediate.thesis_state)
        self.assertEqual("EXIT", immediate.final_action)
        self.assertEqual("HOLD", price_only.final_action)
        self.assertEqual("EXIT", confirm.final_action)
        self.assertFalse(immediate.order_authorized)

    def test_at_risk_can_wait_or_reduce_without_authorizing_order(self) -> None:
        first = first_evaluation()
        thesis = build_entry_thesis(
            run_id="run-1", symbol="000660", leadership=first,
            factor_refs=(), hypothesis_refs=(), created_at=first.as_of, available_at=first.as_of,
        )
        weakened = evaluate_theme_leadership(
            membership(), (*initial_points(),
                point("005930", 22, 104, 700), point("000660", 22, 101, 100),
                point("005930", 29, 102, 900), point("000660", 29, 101, 100),
            ), as_of=f"{DAY}T09:00:30+09:00", venue="KRX",
            continuity_status="COMPLETE", parameters=PARAMETERS,
            previous_state=first.state,
        )
        wait = evaluate_entry_thesis(thesis, weakened, policy_version="CONFIRM_THEN_EXIT")
        reduce = evaluate_entry_thesis(thesis, weakened, policy_version="REDUCE_ON_RISK")
        self.assertEqual("AT_RISK", wait.thesis_state)
        self.assertEqual("WAIT", wait.final_action)
        self.assertEqual("REDUCE", reduce.final_action)
        self.assertFalse(reduce.order_authorized)

    def test_thesis_rejects_evidence_from_before_entry(self) -> None:
        first = first_evaluation()
        thesis = build_entry_thesis(
            run_id="run-1", symbol="005930", leadership=first,
            factor_refs=(), hypothesis_refs=(),
            created_at=f"{DAY}T09:00:21+09:00",
            available_at=f"{DAY}T09:00:21+09:00",
        )
        with self.assertRaisesRegex(ValueError, "earlier leadership evidence"):
            evaluate_entry_thesis(thesis, first, policy_version="PRICE_ONLY")

    def test_research_repository_persists_leadership_thesis_and_decision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = ResearchRepository(Path(directory) / "research.sqlite3")
            repository.start_run("run-1", {"mode": "fixture"}, {"dataset_id": "fixture"})
            first = first_evaluation()
            thesis = build_entry_thesis(
                run_id="run-1", symbol="005930", leadership=first,
                factor_refs=("factor-r1",), hypothesis_refs=(),
                created_at=first.as_of, available_at=first.as_of,
            )
            changed = changed_evaluation(first)
            decision = evaluate_entry_thesis(
                thesis, changed, policy_version="IMMEDIATE_EXIT",
            )
            self.assertTrue(repository.append_theme_leadership(first))
            self.assertFalse(repository.append_theme_leadership(first))
            self.assertTrue(repository.append_entry_thesis(thesis))
            self.assertTrue(repository.append_theme_leadership(changed))
            self.assertTrue(repository.append_thesis_decision(decision))
            self.assertEqual(RESEARCH_SCHEMA_VERSION, repository.schema_version())
            self.assertEqual("005930", repository.load_entry_theses("run-1")[0]["leader_id"])
            self.assertEqual("EXIT", repository.load_thesis_decisions("run-1")[0]["final_action"])
            self.assertEqual(2, len(repository.load_theme_leadership_revisions(
                theme_id="반도체", as_of=changed.as_of,
            )))


class PositionTargetShadowTests(unittest.TestCase):
    def setUp(self):
        from datetime import datetime, timedelta
        from kiwoom_monitor.application.position_target import (
            PositionTargetShadowRequest, evaluate_position_target_shadow,
        )
        from kiwoom_monitor.application.research_execution import SimulationCostModel
        from kiwoom_monitor.domain.order_contract import AccountSnapshot
        self.now = datetime.fromisoformat(f"{DAY}T09:00:30+09:00")
        self.cost = SimulationCostModel("fixed_bps/v1", 10, 20, 10, rate_basis="model_estimate")
        self.request = PositionTargetShadowRequest(
            "account-r4", "mock", "KRX", "005930", "ADD", 20, 1000, self.now, self.now,
            "explicit-share-target/v1", "source-decision-r1", "account-cut-r1", "orders-cut-r1",
            30, True, True, self.cost,
        )
        self.account = AccountSnapshot("account-r4", 20_000, 0, {"005930": 10}, self.now)
        self.evaluate = evaluate_position_target_shadow

    def record(self, side="BUY", quantity=10, filled=5, state="PARTIALLY_FILLED", symbol="005930"):
        from datetime import timedelta
        from kiwoom_monitor.domain.order_contract import OrderIntent, OrderSide, OrderState, OrderType
        from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRecord
        intent = OrderIntent(
            "intent-r4", "run-r4", "decision-r4", "account-r4", "mock", symbol, "KRX",
            OrderSide(side), quantity, OrderType.LIMIT, 1000,
            self.now - timedelta(seconds=10), self.now + timedelta(seconds=30), "policy-r4",
        )
        return ExecutionRecord(intent, OrderState(state), "broker-r4", filled,
                               ("fill-r4",) if filled else (), self.now, self.now, filled, filled)

    def document(self, request=None, account=None, orders=()):
        from datetime import datetime
        from decimal import Decimal
        from kiwoom_monitor.application.position_target import POSITION_TARGET_SHADOW_VERSION
        return json.loads(json.dumps({
            "schema": POSITION_TARGET_SHADOW_VERSION, "request": asdict(request or self.request),
            "account": asdict(account or self.account), "open_orders": [asdict(order) for order in orders],
        }, default=lambda value: value.isoformat() if isinstance(value, datetime) else str(value)))

    def test_partial_buy_uses_only_remaining_quantity_and_reserved_cash_once(self):
        account = replace(self.account, reserved_open_buy_won=5000)
        result = self.evaluate(self.request, account, (self.record(),))
        self.assertEqual((10, 5, 15, 5, 5, 15_000, 5010), tuple(result[key] for key in (
            "actual_quantity", "pending_buy_quantity", "projected_quantity", "remaining_target_delta",
            "incremental_quantity", "cash_after_reservations_won", "required_buy_cash_won")))
        self.assertEqual("CALCULATED", result["quality"]["status"])
        self.assertEqual("BUY", result["incremental_side"])
        self.assertFalse(result["order_authorized"])
        self.assertEqual("shadow_only", result["execution_support"])
        self.assertEqual("EV_UNAVAILABLE", result["ev_status"])

    def test_partial_sell_and_terminal_cancel_do_not_sell_reserved_shares_twice(self):
        request = replace(self.request, action="REDUCE", target_quantity=3)
        result = self.evaluate(request, self.account, (self.record(side="SELL", quantity=8, filled=3),))
        self.assertEqual((5, 5, -2, 2), tuple(result[key] for key in (
            "pending_sell_quantity", "projected_quantity", "remaining_target_delta", "incremental_quantity")))
        cancelled = self.record(side="SELL", quantity=8, filled=3, state="CANCELLED")
        self.assertEqual(7, self.evaluate(request, self.account, (cancelled,))["incremental_quantity"])
        exit_request = replace(request, action="EXIT", target_quantity=0)
        self.assertEqual(5, self.evaluate(exit_request, self.account, (
            self.record(side="SELL", quantity=8, filled=3),))["incremental_quantity"])

    def test_existing_buy_covering_target_is_a_noop_and_cannot_force_a_sell(self):
        account = replace(self.account, reserved_open_buy_won=5000)
        result = self.evaluate(replace(self.request, target_quantity=15, cost_model=None), account, (self.record(),))
        self.assertEqual(0, result["incremental_quantity"])
        self.assertIsNone(result["incremental_side"])
        result = self.evaluate(replace(self.request, target_quantity=12), account, (self.record(),))
        self.assertIn("existing_order_conflicts_with_target", result["quality"]["reasons"])
        self.assertIsNone(result["incremental_quantity"])

    def test_cost_cash_boundary_uses_registered_integer_and_decimal_rounding(self):
        from kiwoom_monitor.application.research_execution import SimulationCostModel, estimate_buy_cash_won
        for cost in (self.cost, SimulationCostModel("fixed_bps/v2", "0.15", "20.00", "1.25")):
            request = replace(self.request, cost_model=cost)
            exact = estimate_buy_cash_won(1000, 3, cost)
            for cash, expected in ((exact - 1, 2), (exact, 3)):
                result = self.evaluate(request, replace(self.account, available_cash_won=cash), ())
                self.assertEqual(expected, result["incremental_quantity"])
                self.assertEqual(10 - expected, result["funding_shortfall_quantity"])
                self.assertLessEqual(result["funded_buy_cash_won"], cash)

    def test_reenter_and_other_symbol_reservations_use_whole_account_cash(self):
        request = replace(self.request, action="REENTER", target_quantity=5)
        account = replace(self.account, positions={"000660": 2}, available_cash_won=7004,
                          reserved_open_buy_won=5000)
        result = self.evaluate(request, account, (self.record(symbol="000660"),))
        self.assertEqual(0, result["actual_quantity"])
        self.assertEqual(0, result["pending_buy_quantity"])
        self.assertEqual(2, result["incremental_quantity"])
        self.assertEqual(3, result["funding_shortfall_quantity"])

    def test_unknown_target_and_missing_cost_never_become_zero_or_new_exposure(self):
        for request, reason in ((replace(self.request, target_quantity=None), "target_quantity_unknown"),
                                (replace(self.request, cost_model=None), "buy_cost_model_unavailable")):
            result = self.evaluate(request, self.account, ())
            self.assertIn(reason, result["quality"]["reasons"])
            self.assertIsNone(result["incremental_quantity"])
        self.assertIsNone(self.evaluate(replace(self.request, target_quantity=None), self.account, ())["target_quantity"])
        self.assertEqual(10, self.evaluate(replace(self.request, action="EXIT", target_quantity=0, cost_model=None),
                                          self.account, ())["incremental_quantity"])

    def test_unconfirmed_cancel_queued_and_aggregate_orders_block_projection(self):
        for state in ("SUBMISSION_UNKNOWN", "CANCEL_PENDING", "QUEUED"):
            result = self.evaluate(self.request, self.account, (self.record(state=state),))
            self.assertIn("unconfirmed_or_cancel_pending_order", result["quality"]["reasons"])
            self.assertIsNone(result["projected_quantity"])
            self.assertIsNone(result["incremental_quantity"])
        for state in ("PARTIALLY_FILLED", "FILLED"):
            aggregate = replace(self.record(state=state), detailed_filled_quantity=0)
            result = self.evaluate(self.request, replace(self.account, reserved_open_buy_won=5000), (aggregate,))
            self.assertIn("order_fill_reconciliation_incomplete", result["quality"]["reasons"])

    def test_missing_account_cut_reservation_or_oversell_blocks_with_reasons(self):
        from datetime import timedelta
        cases = (
            (replace(self.request, reconciliation_complete=False), self.account, (), "account_reconciliation_incomplete"),
            (replace(self.request, open_orders_complete=False), self.account, (), "account_open_orders_incomplete"),
            (self.request, replace(self.account, as_of=self.now - timedelta(seconds=31)), (), "account_stale"),
            (self.request, replace(self.account, as_of=self.now + timedelta(seconds=1)), (), "account_from_future"),
            (replace(self.request, reference_price_available_at=self.now + timedelta(seconds=1)), self.account, (), "reference_price_from_future"),
            (self.request, replace(self.account, reserved_open_buy_won=20001), (), "reservation_exceeds_cash"),
            (self.request, self.account, (self.record(),), "open_buy_reservation_incomplete"),
            (self.request, self.account, (self.record(side="SELL", quantity=20, filled=5),), "pending_sell_exceeds_actual_position"),
            (self.request, replace(self.account, as_of=self.now - timedelta(seconds=1), reserved_open_buy_won=5000), (self.record(),), "account_predates_order_projection"),
        )
        for request, account, records, reason in cases:
            with self.subTest(reason=reason):
                result = self.evaluate(request, account, records)
                self.assertIn(reason, result["quality"]["reasons"])
                self.assertIsNone(result["incremental_quantity"])

    def test_action_mismatch_and_opposite_orders_never_invent_an_order(self):
        for action, target in (("ADD", 5), ("REDUCE", 0), ("REENTER", 20), ("EXIT", None),
                               ("HOLD", 0), ("WAIT", 20)):
            result = self.evaluate(replace(self.request, action=action, target_quantity=target), self.account, ())
            self.assertEqual("BLOCKED", result["quality"]["status"])
            self.assertIsNone(result["incremental_quantity"])
        request = replace(self.request, action="REDUCE", target_quantity=5)
        result = self.evaluate(request, replace(self.account, reserved_open_buy_won=5000), (self.record(),))
        self.assertIn("existing_order_conflicts_with_target", result["quality"]["reasons"])
        self.assertEqual(0, self.evaluate(replace(self.request, action="HOLD", target_quantity=10), self.account, ())["incremental_quantity"])

    def test_json_and_typed_ids_match_inputs_are_immutable_and_no_io_occurs(self):
        from copy import deepcopy
        from unittest.mock import patch
        account = replace(self.account, reserved_open_buy_won=5000)
        record = self.record()
        document = self.document(account=account, orders=(record,))
        original = deepcopy(document)
        with patch("sqlite3.connect", side_effect=AssertionError("unexpected DB")), \
             patch("kiwoom_monitor.application.order_lifecycle.OrderLifecycle.submit", side_effect=AssertionError("unexpected order")):
            typed = self.evaluate(self.request, account, (record,))
            self.assertEqual(typed, evaluate_document(document))
            self.assertEqual(typed, self.evaluate(self.request, account, (record,)))
        self.assertEqual(original, document)
        changed = self.evaluate(replace(self.request, orders_input_ref="orders-cut-r2"), account, (record,))
        self.assertNotEqual(typed["decision_id"], changed["decision_id"])

    def test_duplicate_cross_account_and_invalid_integer_inputs_are_rejected(self):
        record = self.record()
        for records in ((record, record), (replace(record, intent=replace(record.intent, account_ref="other")),)):
            with self.assertRaises(ValueError):
                self.evaluate(self.request, self.account, records)
        for target in (True, 1.5, -1):
            with self.assertRaises(ValueError):
                replace(self.request, target_quantity=target)
        with self.assertRaises(ValueError):
            self.evaluate(self.request, replace(self.account, positions={"005930": True}), ())
        with self.assertRaises(ValueError):
            self.evaluate(self.request, self.account, (replace(record, filled_quantity=11),))
        for scope in ({"environment": "real"}, {"venue": "NXT"}):
            with self.assertRaisesRegex(ValueError, "explicit mock/KRX"):
                replace(self.request, **scope)
        document = self.document()
        document["order_authorized"] = True
        with self.assertRaises(ValueError):
            evaluate_document(document)
        document = self.document()
        document["open_orders"] = [{}] * 401
        with self.assertRaisesRegex(ValueError, "at most 400"):
            evaluate_document(document)

    def test_reconciled_unchanged_order_uses_fresh_broker_cut_and_ordering_is_stable(self):
        from datetime import timedelta
        first = replace(self.record(), updated_at=self.now - timedelta(seconds=120))
        account = replace(self.account, reserved_open_buy_won=10_000)
        second = replace(self.record(symbol="000660"), broker_order_id="broker-second",
                         intent=replace(self.record(symbol="000660").intent, intent_id="intent-second"))
        left = self.evaluate(self.request, account, (first, second))
        self.assertEqual("CALCULATED", left["quality"]["status"])
        self.assertEqual(left, self.evaluate(self.request, account, (second, first)))
        stale = replace(first, last_broker_as_of=self.now - timedelta(seconds=31))
        result = self.evaluate(self.request, replace(account, reserved_open_buy_won=5000), (stale,))
        self.assertIn("broker_order_stale", result["quality"]["reasons"])

    def test_cost_validity_and_naive_times_are_not_silently_accepted(self):
        from datetime import timedelta
        expired = replace(self.cost, valid_from=(self.now - timedelta(minutes=10)).isoformat(),
                          valid_to=self.now.isoformat())
        result = self.evaluate(replace(self.request, cost_model=expired), self.account, ())
        self.assertIn("buy_cost_model_outside_validity", result["quality"]["reasons"])
        with self.assertRaises(ValueError):
            replace(self.request, decision_time=self.now.replace(tzinfo=None))

    def test_r3_reduce_decision_is_consumed_without_changing_its_policy_or_id(self):
        fixture = StockThesisShadowTests()
        fixture.setUp()
        source = evaluate_stock_thesis_shadow(fixture.weak_inputs(), fixture.request("REDUCE_ON_RISK"))
        request = replace(self.request, action=source["action"], target_quantity=5,
                          source_decision_ref=source["decision_id"], policy_version=source["thesis_policy_version"])
        result = self.evaluate(request, self.account, ())
        self.assertEqual("REDUCE", result["action"])
        self.assertEqual(5, result["incremental_quantity"])
        self.assertEqual(source["decision_id"], result["source_decision_ref"])
        self.assertEqual(source, evaluate_stock_thesis_shadow(fixture.weak_inputs(), fixture.request("REDUCE_ON_RISK")))

    def test_existing_sqlite_ledger_partial_fill_projects_once_without_writes(self):
        from datetime import timedelta
        from kiwoom_monitor.application.order_lifecycle import OrderLifecycle
        from kiwoom_monitor.central_server.database import SQLiteQueryStore
        from kiwoom_monitor.domain.order_contract import BrokerFill, BrokerOrderSnapshot, BrokerSubmission, OrderState
        from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository
        class Transport:
            def submit(inner, intent):
                return BrokerSubmission("broker-r4", self.now - timedelta(seconds=2))
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            repository = ExecutionRepository(store)
            lifecycle = OrderLifecycle(repository, Transport(), now_provider=lambda: self.now - timedelta(seconds=1))
            intent = replace(self.record().intent, created_at=self.now - timedelta(seconds=10))
            lifecycle.queue(intent)
            lifecycle.submit(intent.intent_id, replace(self.account, as_of=self.now - timedelta(seconds=3)))
            lifecycle.reconcile(intent.intent_id, BrokerOrderSnapshot(
                "broker-r4", "account-r4", "005930", OrderState.PARTIALLY_FILLED, 5, 5,
                self.now - timedelta(seconds=1),
                (BrokerFill("fill-r4", 5, 1000, self.now - timedelta(seconds=1)),),
            ))
            record = repository.load(intent.intent_id)
            before = repository.events(intent.intent_id)
            result = self.evaluate(self.request, replace(self.account, reserved_open_buy_won=5000), (record,))
            self.assertEqual(15, result["projected_quantity"])
            self.assertEqual(5, result["incremental_quantity"])
            self.assertEqual(before, repository.events(intent.intent_id))


class AccountWidePositionObservationTests(unittest.TestCase):
    def setUp(self):
        from kiwoom_monitor.application.position_target import reconcile_position_account_observation
        self.fixture = PositionTargetShadowTests(); self.fixture.setUp()
        self.now, self.account = self.fixture.now, self.fixture.account
        self.reconcile = reconcile_position_account_observation

    def recovery(self, orders=(), details=(), account=None):
        from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import AccountRecovery
        return AccountRecovery(account or self.account, orders, details)

    def observation(self, recovery=None, before=(), after=(), **changes):
        return self.reconcile(recovery or self.recovery(), before, after, input_ref="account-wide:1", observed_at=self.now,
            **{"ledger_complete": True, "runtime_stable": True, **changes})

    def target(self, observation, request=None):
        request = replace(request or self.fixture.request, open_orders_complete=observation.open_orders_complete,
                          reconciliation_complete=observation.reconciliation_complete)
        return self.fixture.evaluate(request, observation.account, observation.open_orders,
                                     broker_open_orders=observation.broker_open_orders)

    def test_other_symbol_reservation_and_sell_quantity_must_also_reconcile(self):
        from kiwoom_monitor.domain.order_contract import BrokerOrderSnapshot, OrderSide, OrderState
        from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import BrokerOrderDetails
        for side, reason in ((OrderSide.BUY, "open_buy_reservation_incomplete"),
                             (OrderSide.SELL, "pending_sell_exceeds_actual_position")):
            snapshot = BrokerOrderSnapshot("other-symbol", self.account.account_ref, "000660", OrderState.ACCEPTED, 0, 1, self.now)
            detail = BrokerOrderDetails("other-symbol", 1, side, 1000, "KRX")
            observation = self.observation(self.recovery((snapshot,), (detail,)))
            self.assertFalse(observation.open_orders_complete)
            self.assertIn(reason, observation.reasons)
            self.assertIsNone(self.target(observation)["incremental_quantity"])

    def test_complete_empty_broker_and_whole_ledger_can_calculate_without_order_authority(self):
        observation = self.observation()
        self.assertTrue(observation.open_orders_complete)
        result = self.target(observation)
        self.assertEqual((10, 10, "CALCULATED"), (result["projected_quantity"], result["incremental_quantity"], result["quality"]["status"]))
        self.assertEqual("broker_remaining_quantity/v1", result["scope"]["pending_quantity_basis"])
        self.assertFalse(result["order_authorized"])

    def test_other_run_partial_fill_uses_broker_remaining_without_forging_local_fills(self):
        from datetime import timedelta
        from kiwoom_monitor.domain.order_contract import BrokerOrderSnapshot, OrderState
        record = self.fixture.record(quantity=10, filled=0, state="ACCEPTED")
        record = replace(record, intent=replace(record.intent, run_id="retired-run"), updated_at=self.now + timedelta(seconds=1))
        snapshot = BrokerOrderSnapshot(record.broker_order_id, self.account.account_ref, "005930", OrderState.PARTIALLY_FILLED, 3, 7, self.now)
        account = replace(self.account, reserved_open_buy_won=7000)
        observation = self.observation(self.recovery((snapshot,), account=account), (record,), (record,))
        result = self.target(observation)
        self.assertTrue(observation.open_orders_complete)
        self.assertEqual((10, 7, 17, 3), tuple(result[k] for k in ("actual_quantity", "pending_buy_quantity", "projected_quantity", "incremental_quantity")))
        self.assertEqual(0, record.filled_quantity)
        self.assertEqual(record, observation.open_orders[0])

    def test_broker_only_manual_order_has_no_fabricated_intent_and_reserves_cash_once(self):
        from kiwoom_monitor.domain.order_contract import BrokerOrderSnapshot, OrderState, OrderSide
        from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import BrokerOrderDetails
        snapshot = BrokerOrderSnapshot("manual-buy", self.account.account_ref, "005930", OrderState.PARTIALLY_FILLED, 1, 2, self.now)
        detail = BrokerOrderDetails("manual-buy", 3, OrderSide.BUY, 1000, "KRX")
        account = replace(self.account, reserved_open_buy_won=2000)
        observation = self.observation(self.recovery((snapshot,), (detail,), account))
        result = self.target(observation)
        self.assertTrue(observation.open_orders_complete)
        self.assertEqual((), observation.open_orders)
        self.assertIsNone(observation.broker_open_orders[0].intent_ref)
        self.assertEqual(("manual-buy",), observation.unmapped_broker_order_ids)
        self.assertEqual((2, 12, 8, 18000), tuple(result[k] for k in ("pending_buy_quantity", "projected_quantity", "incremental_quantity", "cash_after_reservations_won")))

    def test_manual_sell_and_confirmed_cancelled_old_run_do_not_overcount_remainders(self):
        from kiwoom_monitor.domain.order_contract import BrokerOrderSnapshot, OrderState, OrderSide
        from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import BrokerOrderDetails
        snapshot = BrokerOrderSnapshot("manual-sell", self.account.account_ref, "005930", OrderState.PARTIALLY_FILLED, 2, 3, self.now)
        detail = BrokerOrderDetails("manual-sell", 5, OrderSide.SELL, None, "KRX")
        observation = self.observation(self.recovery((snapshot,), (detail,)))
        result = self.target(observation, replace(self.fixture.request, action="REDUCE", target_quantity=5))
        self.assertEqual((3, 7, 2), tuple(result[k] for k in ("pending_sell_quantity", "projected_quantity", "incremental_quantity")))
        record = self.fixture.record(side="SELL", quantity=5, filled=0, state="ACCEPTED")
        cancelled = BrokerOrderSnapshot(record.broker_order_id, self.account.account_ref, "005930", OrderState.CANCELLED, 0, 5, self.now)
        observation = self.observation(self.recovery((cancelled,)), (record,), (record,))
        self.assertEqual((), observation.broker_open_orders)
        self.assertEqual(5, self.target(observation, replace(self.fixture.request, action="REDUCE", target_quantity=5))["incremental_quantity"])

    def test_queued_unknown_cancel_pending_and_missing_old_run_orders_remain_blocked(self):
        from kiwoom_monitor.domain.order_contract import OrderState
        for state in (OrderState.QUEUED, OrderState.SUBMISSION_UNKNOWN, OrderState.CANCEL_PENDING, OrderState.ACCEPTED):
            record = self.fixture.record(filled=0, state=state.value)
            observation = self.observation(before=(record,), after=(record,))
            self.assertFalse(observation.open_orders_complete)
            self.assertIsNone(self.target(observation)["incremental_quantity"])

    def test_concurrent_creation_budget_or_read_failure_is_not_confirmed_empty(self):
        record = self.fixture.record(filled=0, state="QUEUED")
        observations = (self.observation(after=(record,)), self.observation(runtime_stable=False),
                        self.observation(ledger_complete=False), self.observation(ledger_complete=False, ledger_read_failed=True))
        for observation in observations:
            self.assertFalse(observation.open_orders_complete)
            self.assertIsNone(self.target(observation)["incremental_quantity"])
        self.assertIn("account_order_ledger_read_unavailable", observations[-1].reasons)

    def test_missing_or_conflicting_manual_side_and_scope_never_becomes_zero_exposure(self):
        from kiwoom_monitor.domain.order_contract import BrokerOrderSnapshot, OrderState, OrderSide
        from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import BrokerOrderDetails
        snapshot = BrokerOrderSnapshot("manual", self.account.account_ref, "005930", OrderState.ACCEPTED, 0, 1, self.now)
        details = (BrokerOrderDetails("manual", 1, OrderSide.BUY, 1000, "KRX"), BrokerOrderDetails("manual", 1, OrderSide.SELL, 1000, "KRX"))
        for values in ((), details, tuple(reversed(details)), (replace(details[0], venue="NXT"),)):
            observation = self.observation(self.recovery((snapshot,), values))
            self.assertFalse(observation.open_orders_complete)
            self.assertIsNone(self.target(observation)["projected_quantity"])
        self.assertEqual(self.target(self.observation(self.recovery((snapshot,), details))),
                         self.target(self.observation(self.recovery((snapshot,), tuple(reversed(details))))))


class NativePositionOutcomeReaderTests(unittest.TestCase):
    def setUp(self):
        from contextlib import closing
        from datetime import datetime, timezone, timedelta
        from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
        self.directory = tempfile.TemporaryDirectory(); self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'native.sqlite3'
        self.writer = ResearchRepository(self.path)
        self.cut = datetime.now(timezone.utc) + timedelta(minutes=1)
        self.connection = lambda: closing(self.writer._connect())

    def source(self, action='HOLD', *, variant=0, minute=0, end_minute=5, skip_minute=None, role='TRAIN', dataset_label=None):
        import hashlib
        from dataclasses import replace
        from kiwoom_monitor.application.research_execution import PaperExecutionEngine, SimulationExecutionConfig
        from kiwoom_monitor.application.breakout_strategy import StrategyState
        from kiwoom_monitor.application.research_evaluation import ResearchReport, _content_id
        from tests.unit.test_research_execution import _bar, _decision, _strategy
        from tests.unit.test_research_independent_comparisons import record
        value = record('train', minute=minute, role=role)
        fixture = PositionTargetShadowTests(); fixture.setUp()
        execution = SimulationExecutionConfig('next_tradable_bar_open/v1',
            'conservative_with_optimistic_bound/v1', 100_000, fixture.cost)
        spec, manifest = value['spec'], value['input_manifest']
        from kiwoom_monitor.application.market_session_schedule import KRX_REGULAR_RESEARCH_PROFILE, research_session_profile_document
        spec['session_profile'] = manifest['research_session_profile'] = research_session_profile_document(KRX_REGULAR_RESEARCH_PROFILE)
        if dataset_label is not None: manifest['dataset_id'] = 'development-'+dataset_label
        spec.update(execution_scope='independent_development_validation/v1', policy_version='native-policy/v1',
            parameters={'action_fixture': action, 'variant': variant}, execution_model=execution.to_dict(),
            cost_model=fixture.cost.to_dict())
        spec['data_manifest_hash'] = hashlib.sha256(json.dumps(manifest, ensure_ascii=False,
            sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        run_id = _content_id('run', {'dataset_id': manifest['dataset_id'],
                                   'revision_ids_hash': manifest['revision_ids_hash'], 'spec': spec})
        engine = PaperExecutionEngine(run_id, execution, _strategy())
        from datetime import datetime, timedelta
        def shifted(at): return (datetime.fromisoformat(at) + timedelta(minutes=minute)).isoformat()
        decision = replace(_decision(shifted('2026-09-14T00:03:02+00:00')), run_id=run_id)
        def bar(minute, price=1000):
            raw = _bar(minute, open_=price, low=price-1, high=price+1)
            return replace(raw, observation_key=raw.observation_key.replace('09-12', '09-14'),
                bar_start=shifted(raw.bar_start.replace('09-12', '09-14')),
                bar_end=shifted(raw.bar_end.replace('09-12', '09-14')), available_at=shifted(raw.available_at.replace('09-12', '09-14')))
        events = list(engine.process_decision(decision, StrategyState(status='candidate', symbol='005930')))
        events += engine.process_bar(bar(4))
        start = events[-1]
        if action == 'EXIT':
            exit_decision = replace(decision, decision_id='decision-exit', proposal='EXIT', final_action='EXIT',
                                    decided_at=shifted('2026-09-14T00:05:02+00:00'))
            events += engine.process_decision(exit_decision, engine.strategy_state, source_bar=bar(4))
        events += engine.process_bar(bar(5, 1010))
        if action == 'EXIT':
            events += engine.process_bar(bar(6, 1010))
            end = events[-1]
        else:
            for last_minute in range(6, end_minute + 1):
                if last_minute != skip_minute: events += engine.process_bar(bar(last_minute, 1010))
            end = events[-1]
        self.writer.start_run(run_id, spec, manifest, claim_independent=True)
        self.writer.append_execution_events(tuple(events))
        fold = value['report']['fold_reports'][0]
        report = ResearchReport('report-'+run_id, run_id, 'ELIGIBLE', (), spec['evaluation_spec'], {},
                                spec['cost_model'], (), (fold,))
        self.writer.save_research_report(report); self.writer.finish_run(run_id, 'logical-'+run_id)
        selection = {'run_id': run_id, 'start_event_id': start.event_id, 'end_event_id': end.event_id,
                     'action': action, 'case_ref': 'case-'+str(minute)}
        return selection, events, fixture.cost

    def read(self, selections, **changes):
        from kiwoom_monitor.application.research_evaluation import read_position_action_outcomes
        from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
        reader = ResearchRepository(self.path, read_only=True)
        return read_position_action_outcomes(reader.load_position_action_evidence, selections,
            **{'decision_time': self.cut, 'horizon_seconds': 60, 'population_ref': 'cohort-r1', 'environment': 'mock', 'symbol': '005930', **changes})

    def test_native_hold_and_exit_measure_portfolio_and_fees_once(self):
        hold, hold_events, _ = self.source()
        exit_, exit_events, _ = self.source('EXIT')
        outcomes, receipt = self.read([hold, exit_])
        self.assertEqual((2, []), (receipt['accepted_count'], receipt['excluded']))
        by_action = {o.action: o for o in outcomes}
        self.assertEqual((100, 0, 10), (by_action['HOLD'].gross_change_won, by_action['HOLD'].fee_tax_won,
                                      by_action['HOLD'].target_quantity))
        filled = exit_events[-1]
        start = next(e for e in exit_events if e.event_id == exit_['start_event_id'])
        net = filled.cash_after_won - (start.cash_after_won + 10 * start.price)
        self.assertEqual((net + filled.total_cost_won, filled.total_cost_won, 0),
            (by_action['EXIT'].gross_change_won, by_action['EXIT'].fee_tax_won, by_action['EXIT'].target_quantity))
        self.assertEqual(by_action['HOLD'].initial_state_ref, by_action['EXIT'].initial_state_ref)
        self.assertFalse(receipt['order_authorized'])

    def test_one_native_snapshot_per_run_zero_commit_and_missing_action_preserved(self):
        from unittest.mock import patch
        from kiwoom_monitor.application.research_evaluation import read_position_action_outcomes
        from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
        selection, _, _ = self.source()
        reader = ResearchRepository(self.path, read_only=True)
        sql = []; original = reader._connect
        def connect():
            value = original(); value.set_trace_callback(sql.append); return value
        unsupported = {**selection, 'action': 'REDUCE', 'start_event_id': 'another-start', 'case_ref': 'another-case'}
        before = self.path.read_bytes()
        with patch.object(reader, '_connect', side_effect=connect) as connects:
            outcomes, receipt = read_position_action_outcomes(reader.load_position_action_evidence,
                [selection, unsupported], decision_time=self.cut, horizon_seconds=60, population_ref='cohort-r1', environment='mock', symbol='005930')
        self.assertEqual(1, connects.call_count)
        self.assertEqual(1, len(outcomes)); self.assertIn('not_supported', receipt['excluded'][0]['reason'])
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(4, sum(s.startswith('SELECT') for s in sql))
        self.assertFalse(any(s.startswith('COMMIT') or s.startswith('INSERT') for s in sql))
        self.assertTrue(any(s == 'ROLLBACK' for s in sql))

    def test_metadata_failure_and_final_overlap_do_not_read_execution_values(self):
        from unittest.mock import patch
        from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
        selection, _, _ = self.source()
        with self.connection() as conn, conn:
            conn.execute("INSERT INTO research_final_holdout_windows VALUES(?,?,?,?,?,?,?)",
                ('final-test', '2026-09-14T00:00:00+00:00', '2026-09-14T00:10:00+00:00', 'FINAL_RESERVED',
                 'final-batch', '{}', self.cut.isoformat()))
        reader = ResearchRepository(self.path, read_only=True)
        sql = []; original = reader._connect
        def connect():
            value = original(); value.set_trace_callback(sql.append); return value
        with patch.object(reader, '_connect', side_effect=connect), self.assertRaisesRegex(ValueError, 'final window'):
            reader.load_position_action_evidence(selection['run_id'], (selection['end_event_id'],), decision_time=self.cut)
        self.assertFalse(any('FROM research_execution_events' in s for s in sql))

    def test_future_failed_and_oos_sources_do_not_produce_values(self):
        selection, _, _ = self.source()
        for column, value in [('finished_at', '2099-01-01T00:00:00+00:00'), ('status', 'failed')]:
            with self.connection() as conn, conn:
                before = conn.execute(f'SELECT {column} FROM research_runs WHERE run_id=?', (selection['run_id'],)).fetchone()[0]
                conn.execute(f'UPDATE research_runs SET {column}=? WHERE run_id=?', (value, selection['run_id']))
            outcomes, receipt = self.read([selection])
            self.assertEqual((), outcomes); self.assertEqual(1, len(receipt['excluded']))
            with self.connection() as conn, conn:
                conn.execute(f'UPDATE research_runs SET {column}=? WHERE run_id=?', (before, selection['run_id']))
        with self.connection() as conn, conn:
            spec = json.loads(conn.execute('SELECT spec_json FROM research_runs WHERE run_id=?', (selection['run_id'],)).fetchone()[0])
            spec['evaluation_spec']['folds'][0]['role'] = 'OOS'
            conn.execute('UPDATE research_runs SET spec_json=? WHERE run_id=?', (json.dumps(spec), selection['run_id']))
        self.assertEqual((), self.read([selection])[0])

    def test_budget_identity_corruption_duplicate_and_horizon_fail_closed(self):
        from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
        selection, _, _ = self.source()
        reader = ResearchRepository(self.path, read_only=True)
        with self.assertRaisesRegex(ValueError, 'budget'):
            reader.load_position_action_evidence(selection['run_id'], (selection['end_event_id'],), decision_time=self.cut, limit=1)
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            self.read([selection, {**selection, 'case_ref': 'fake-extra-case'}])
        self.assertEqual((), self.read([selection], horizon_seconds=120)[0])
        with self.connection() as conn, conn:
            document = json.loads(conn.execute('SELECT document_json FROM research_execution_events WHERE event_id=?',
                (selection['end_event_id'],)).fetchone()[0])
            document['price'] += 1
            conn.execute('UPDATE research_execution_events SET document_json=? WHERE event_id=?',
                         (json.dumps(document), selection['end_event_id']))
        with self.assertRaisesRegex(ValueError, 'content or identity'):
            self.read([selection])

    def test_minute_gap_and_initial_pending_order_are_not_measured_hold(self):
        from kiwoom_monitor.application.research_evaluation import position_action_outcome_from_events, PositionOutcomeUnavailable
        from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
        selection, events, _ = self.source(end_minute=7, skip_minute=6)
        outcomes, receipt = self.read([selection], horizon_seconds=180)
        self.assertEqual((), outcomes); self.assertEqual('native_minute_valuation_gap', receipt['excluded'][0]['reason'])
        context, raw = ResearchRepository(self.path, read_only=True).load_position_action_evidence(
            selection['run_id'], (selection['end_event_id'],), decision_time=self.cut)
        missing_fill = tuple(e for e in raw if not (e['event_type']=='FILL' and e['side']=='BUY'))
        with self.assertRaisesRegex(PositionOutcomeUnavailable, 'initial_open_order'):
            position_action_outcome_from_events(context, missing_fill, start_event_id=selection['start_event_id'],
                end_event_id=selection['end_event_id'], action='HOLD', case_ref='case', population_ref='cohort-r1',
                environment='mock', horizon_seconds=180, symbol='005930')

    def test_selected_role_and_duplicate_effective_evidence_do_not_expand_samples(self):
        selection, _, _ = self.source()
        duplicate, _, _ = self.source(dataset_label='renamed-input')
        duplicate['case_ref'] = 'fake-case'
        outcomes, receipt = self.read([selection, duplicate])
        self.assertEqual(1, len(outcomes)); self.assertEqual('duplicate_effective_native_evidence', receipt['excluded'][0]['reason'])
        validation, _, _ = self.source(role='VALIDATION', variant=2)
        outcomes, receipt = self.read([validation])
        self.assertEqual((), outcomes); self.assertEqual('native_development_role_mismatch', receipt['excluded'][0]['reason'])
        self.assertEqual(1, len(self.read([validation], development_role='VALIDATION')[0]))

    def test_future_endpoint_does_not_read_future_values_or_hide_earlier_sibling(self):
        from datetime import timedelta
        from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
        selection, events, _ = self.source(end_minute=6)
        earlier = next(e for e in events if e.event_type=='MARK' and '00:06:00' in e.occurred_at)
        earlier_selection = {**selection, 'end_event_id': earlier.event_id, 'case_ref': 'earlier-case'}
        with self.connection() as conn, conn:
            document = json.loads(conn.execute('SELECT document_json FROM research_execution_events WHERE event_id=?',
                (selection['end_event_id'],)).fetchone()[0])
            document.update(received_at=(self.cut+timedelta(days=1)).isoformat(), price=999999999)
            conn.execute('UPDATE research_execution_events SET received_at=?,document_json=? WHERE event_id=?',
                (document['received_at'],json.dumps(document),selection['end_event_id']))
        reader = ResearchRepository(self.path,read_only=True)
        context, raw = reader.load_position_action_evidence(selection['run_id'],
            (earlier.event_id,selection['end_event_id']),decision_time=self.cut)
        self.assertNotIn(selection['end_event_id'],[event['event_id'] for event in raw])
        outcomes, receipt = self.read([selection,earlier_selection])
        self.assertEqual(1,len(outcomes)); self.assertEqual('earlier-case',outcomes[0].case_ref)
        self.assertEqual(1,len(receipt['excluded']))

    def test_invalid_role_scope_or_duplicate_selection_never_calls_native_loader(self):
        from unittest.mock import Mock
        from kiwoom_monitor.application.research_evaluation import read_position_action_outcomes
        selection = {'run_id':'not-read','start_event_id':'start','end_event_id':'end','action':'HOLD','case_ref':'case'}
        loader = Mock(side_effect=AssertionError('invalid input read native values'))
        arguments = {'decision_time':self.cut,'horizon_seconds':60,'population_ref':'cohort','environment':'mock','symbol':'005930'}
        for changes in ({'development_role':'OOS'},{'horizon_seconds':10**12},{'environment':'real'},{'symbol':'another'}):
            with self.assertRaises(ValueError): read_position_action_outcomes(loader,[selection],**{**arguments,**changes})
        with self.assertRaises(ValueError): read_position_action_outcomes(loader,[selection,selection],**arguments)
        loader.assert_not_called()

    def test_endpoint_outside_locked_fold_never_loads_its_values(self):
        from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
        selection, events, _ = self.source(end_minute=11)
        reader=ResearchRepository(self.path,read_only=True)
        context, raw=reader.load_position_action_evidence(selection['run_id'],(selection['end_event_id'],),decision_time=self.cut)
        self.assertEqual((),raw)

    def test_checkpoint_interrupts_native_sql_and_closes_read_snapshot(self):
        import sqlite3
        from unittest.mock import patch
        from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
        selection, _, _ = self.source()
        reader=ResearchRepository(self.path,read_only=True)
        in_values=[False]; closed=[]; sql=[]
        class Connection(sqlite3.Connection):
            def set_progress_handler(self,callback,steps):
                return super().set_progress_handler(callback,1 if callback else steps)
            def close(self):
                closed.append(True); return super().close()
        def connect():
            value=sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True,factory=Connection)
            def trace(statement):
                sql.append(statement)
                if 'SELECT * FROM research_execution_events' in statement: in_values[0]=True
            value.set_trace_callback(trace); return value
        def checkpoint():
            if in_values[0]: raise RuntimeError('read cancelled')
        with patch.object(reader,'_connect',side_effect=connect), self.assertRaisesRegex(sqlite3.OperationalError,'interrupted'):
            reader.load_position_action_evidence(selection['run_id'],(selection['end_event_id'],),
                decision_time=self.cut,checkpoint=checkpoint)
        self.assertEqual([True],closed)
        self.assertIn('ROLLBACK',sql)
        self.assertNotIn('COMMIT',sql)

    def test_cli_and_typed_native_comparison_use_same_outcomes_and_id(self):
        import os, subprocess, sys
        from dataclasses import asdict, replace
        from datetime import datetime
        from kiwoom_monitor.application.position_target import evaluate_position_action_comparison_document
        fixture = PositionActionComparisonTests(); fixture.setUp()
        selections = [self.source(action, minute=minute)[0] for minute in (0, 20) for action in ('HOLD', 'EXIT')]
        outcomes, receipt = self.read(selections)
        self.assertEqual(4, len(outcomes))
        document = fixture.document(outcomes=())
        document['account']['as_of'] = self.cut.isoformat()
        binding = {o.action: o for o in outcomes}
        for request in document['requests']:
            action = 'HOLD' if request['action']=='HOLD' else 'EXIT'
            request.update(action=action, target_quantity=binding[action].target_quantity,
                policy_version=binding[action].policy_version, decision_time=self.cut.isoformat(),
                reference_price_available_at=self.cut.isoformat())
        document['parameters']['execution_model_ref'] = outcomes[0].execution_model_ref
        native_document = {**document, 'outcomes': json.loads(json.dumps([asdict(o) for o in outcomes], default=lambda v:v.isoformat()))}
        expected = evaluate_position_action_comparison_document(native_document)
        self.assertTrue(all(row['ev_status']=='EMPIRICAL_ESTIMATE' for row in expected['actions']))
        directory = Path(self.directory.name)
        (directory/'comparison.json').write_text(json.dumps(document),encoding='utf-8')
        (directory/'selection.json').write_text(json.dumps(selections),encoding='utf-8')
        args = [sys.executable, str(Path(__file__).resolve().parents[2]/'scripts/preview_market_theme_shadow.py'),
            '--input',str(directory/'comparison.json'),'--research-db',str(self.path),
            '--position-outcome-selection',str(directory/'selection.json'),'--position-outcome-role','TRAIN']
        result = subprocess.run(args,capture_output=True,text=True,encoding='utf-8',timeout=60,env={**os.environ,'PYTHONUTF8':'1'})
        self.assertEqual(0,result.returncode,result.stderr)
        parsed = json.loads(result.stdout)
        actual_receipt = parsed.pop('position_outcome_source')
        self.assertEqual(json.loads(json.dumps(expected)),parsed)
        self.assertEqual(4,actual_receipt['accepted_count'])
        self.assertEqual('TRAIN',actual_receipt['development_role'])
        self.assertFalse(parsed['order_authorized'])
        document['outcomes'] = native_document['outcomes']
        (directory/'comparison.json').write_text(json.dumps(document),encoding='utf-8')
        invalid = subprocess.run(args,capture_output=True,text=True,encoding='utf-8',timeout=60)
        self.assertEqual(2,invalid.returncode)
        self.assertIn('empty outcomes',invalid.stderr)


class PositionActionComparisonTests(unittest.TestCase):
    def setUp(self):
        from kiwoom_monitor.application.position_target import (
            PositionActionComparisonParameters, evaluate_position_action_comparison_shadow,
        )
        self.fixture = PositionTargetShadowTests()
        self.fixture.setUp()
        self.now, self.account = self.fixture.now, self.fixture.account
        self.parameters = PositionActionComparisonParameters(
            "HOLD", 60, "cohort-r1", "minute", "simulation", "execution-model-r1", 2,
        )
        self.requests = (replace(self.fixture.request, action="HOLD", target_quantity=10),
                         replace(self.fixture.request, action="REDUCE", target_quantity=5))
        self.evaluate = evaluate_position_action_comparison_shadow

    def sample(self, action, case, gross, fee=0):
        from datetime import timedelta
        from kiwoom_monitor.application.research_evaluation import PositionActionOutcome, position_action_cost_model_ref
        request = next(request for request in self.requests if request.action == action)
        started = self.now - timedelta(minutes=10 if case == "a" else 5)
        return PositionActionOutcome(
            input_ref=f"outcome:{action}:{case}", case_ref=case, initial_state_ref=f"initial:{case}",
            action=action, policy_version=request.policy_version, environment="mock", venue="KRX",
            source_kind="simulation", execution_model_ref="execution-model-r1",
            initial_quantity=self.account.positions.get("005930", 0), pending_buy_quantity=0, pending_sell_quantity=0,
            target_quantity=request.target_quantity, horizon_seconds=60, population_ref="cohort-r1",
            input_resolution="minute", cost_model_ref=position_action_cost_model_ref(self.fixture.cost.to_dict()),
            started_at=started, completed_at=started + timedelta(seconds=60),
            available_at=started + timedelta(seconds=61), status="COMPLETE", data_gap_count=0,
            gross_change_won=gross, fee_tax_won=fee,
        )

    def outcomes(self):
        return (self.sample("HOLD", "a", 100), self.sample("HOLD", "b", -100),
                self.sample("REDUCE", "a", 200, 30), self.sample("REDUCE", "b", -50, 30))

    def run_comparison(self, outcomes=None, requests=None, parameters=None, account=None, orders=()):
        return self.evaluate(parameters or self.parameters, requests or self.requests,
                             account or self.account, orders, self.outcomes() if outcomes is None else outcomes)

    def row(self, result, action="REDUCE"):
        return next(row for row in result["actions"] if row["action"] == action)

    def document(self, outcomes=None):
        from datetime import datetime
        from kiwoom_monitor.application.position_target import POSITION_ACTION_COMPARISON_SHADOW_VERSION
        document = {
            "schema": POSITION_ACTION_COMPARISON_SHADOW_VERSION, "parameters": asdict(self.parameters),
            "requests": [asdict(request) for request in self.requests], "account": asdict(self.account),
            "open_orders": [], "outcomes": [asdict(sample) for sample in (self.outcomes() if outcomes is None else outcomes)],
        }
        return json.loads(json.dumps(document, default=lambda value: value.isoformat() if isinstance(value, datetime) else str(value)))

    def test_paired_cost_adjusted_means_and_uncertainty_are_hand_checkable(self):
        result = self.run_comparison()
        row = self.row(result)
        self.assertEqual("COMPARABLE", result["status"])
        self.assertEqual("EMPIRICAL_ESTIMATE", row["ev_status"])
        self.assertEqual(2, row["paired_sample_count"])
        self.assertEqual(75, row["estimate"]["mean_gross_change_won"])
        self.assertEqual(30, row["estimate"]["mean_fee_tax_won"])
        self.assertEqual(45, row["estimate"]["mean_net_change_won"])
        self.assertEqual(0, row["baseline_estimate"]["mean_net_change_won"])
        self.assertGreater(row["baseline_estimate"]["net_stddev_won"], 100)
        self.assertEqual(45, row["paired_delta"]["mean_net_change_won"])
        self.assertAlmostEqual(25, row["paired_delta"]["net_standard_error_won"])
        self.assertEqual((20, 70), (row["paired_delta"]["minimum_net_change_won"], row["paired_delta"]["maximum_net_change_won"]))
        self.assertFalse(result["order_authorized"])
        self.assertTrue(all(not target["order_authorized"] for target in result["targets"].values()))
        self.assertEqual("not_verified", result["probability_calibration"])

    def test_missing_add_data_and_missing_hold_never_become_zero_outcomes(self):
        requests = (*self.requests, self.fixture.request)
        result = self.run_comparison(requests=requests)
        self.assertEqual("PARTIAL", result["status"])
        missing = self.row(result, "ADD")
        self.assertEqual("EV_UNAVAILABLE", missing["ev_status"])
        self.assertIsNone(missing["estimate"])
        self.assertEqual(0, missing["sample_count"])
        result = self.run_comparison(outcomes=tuple(sample for sample in self.outcomes() if sample.action == "REDUCE"))
        self.assertEqual("EV_UNAVAILABLE", result["status"])
        self.assertIsNone(self.row(result)["paired_delta"])

    def test_reenter_flat_account_and_exit_use_their_own_complete_action_data(self):
        for action, target, initial in (("REENTER", 5, 0), ("EXIT", 0, 10)):
            with self.subTest(action=action):
                self.account = replace(self.fixture.account, positions={"005930": initial})
                self.requests = (replace(self.fixture.request, action="HOLD", target_quantity=initial),
                                 replace(self.fixture.request, action=action, target_quantity=target))
                samples = (self.sample("HOLD", "a", 100), self.sample("HOLD", "b", -100),
                           self.sample(action, "a", 100, 30), self.sample(action, "b", 100, 30))
                result = self.run_comparison(outcomes=samples)
                self.assertEqual("COMPARABLE", result["status"])
                self.assertEqual(70, self.row(result, action)["paired_delta"]["mean_net_change_won"])
                self.assertFalse(result["order_authorized"])

    def test_future_incomplete_gapped_and_unknown_cost_samples_are_excluded(self):
        from datetime import timedelta
        first, second, third, fourth = self.outcomes()
        for invalid, reason in (
            (replace(third, available_at=self.now + timedelta(seconds=1)), "outcome_not_available_at_cut"),
            (replace(third, status="CENSORED"), "outcome_not_complete"),
            (replace(third, data_gap_count=1), "outcome_data_gap"),
            (replace(third, fee_tax_won=None), "outcome_value_or_cost_unavailable"),
            (replace(third, completed_at=None), "outcome_time_interval_mismatch"),
        ):
            with self.subTest(reason=reason):
                row = self.row(self.run_comparison(outcomes=(first, second, invalid, fourth)))
                self.assertEqual("EV_UNAVAILABLE", row["ev_status"])
                self.assertEqual(1, row["paired_sample_count"])
                self.assertEqual(1, row["excluded_counts"][reason])
                self.assertIsNone(row["estimate"])

    def test_action_policy_quantity_cost_population_and_model_scope_cannot_mix(self):
        first, second, third, fourth = self.outcomes()
        mismatches = ({"policy_version": "other"}, {"target_quantity": 4}, {"initial_quantity": 9},
                      {"pending_buy_quantity": 1}, {"pending_sell_quantity": 1}, {"cost_model_ref": "other"},
                      {"population_ref": "other"}, {"input_resolution": "second"}, {"horizon_seconds": 120},
                      {"venue": "NXT"}, {"environment": "real"}, {"source_kind": "broker"},
                      {"execution_model_ref": "other"})
        for values in mismatches:
            with self.subTest(values=values):
                row = self.row(self.run_comparison(outcomes=(first, second, replace(third, **values), fourth)))
                self.assertEqual("EV_UNAVAILABLE", row["ev_status"])
                self.assertEqual(1, row["sample_count"])
                self.assertIsNone(row["paired_delta"])

    def test_same_case_ref_requires_same_initial_state_and_time(self):
        from datetime import timedelta
        first, second, third, fourth = self.outcomes()
        for invalid in (replace(third, initial_state_ref="other"),
                        replace(third, started_at=third.started_at - timedelta(seconds=1),
                                completed_at=third.completed_at - timedelta(seconds=1))):
            row = self.row(self.run_comparison(outcomes=(first, second, invalid, fourth)))
            self.assertEqual(1, row["paired_sample_count"])
            self.assertEqual(1, row["excluded_counts"]["paired_initial_state_or_time_mismatch"])
            self.assertIsNone(row["estimate"])

    def test_unpaired_outlier_is_not_used_in_paired_action_mean(self):
        extra = self.sample("REDUCE", "extra", 999_999)
        row = self.row(self.run_comparison(outcomes=(*self.outcomes(), extra)))
        self.assertEqual((3, 2, 1), (row["sample_count"], row["paired_sample_count"], row["unpaired_sample_count"]))
        self.assertEqual(45, row["estimate"]["mean_net_change_won"])

    def test_blocked_unfunded_and_costless_targets_do_not_gain_ev_or_orders(self):
        requests = (self.requests[0], self.fixture.request)
        result = self.run_comparison(requests=requests, account=replace(self.account, available_cash_won=1),
                                     outcomes=tuple(sample for sample in self.outcomes() if sample.action == "HOLD"))
        self.assertIn("target_not_fully_funded", self.row(result, "ADD")["reasons"])
        for changed in (
            tuple(replace(request, reconciliation_complete=False) for request in self.requests),
            tuple(replace(request, cost_model=None) for request in self.requests),
        ):
            result = self.run_comparison(requests=changed)
            self.assertEqual("EV_UNAVAILABLE", result["status"])
            self.assertTrue(all(row["estimate"] is None for row in result["actions"]))

    def test_cost_model_validity_covers_current_cut_and_each_complete_case(self):
        from datetime import timedelta
        from kiwoom_monitor.application.research_evaluation import position_action_cost_model_ref
        cost = replace(self.fixture.cost, valid_from=(self.now - timedelta(minutes=7)).isoformat(),
                       valid_to=(self.now + timedelta(minutes=1)).isoformat())
        requests = tuple(replace(request, cost_model=cost) for request in self.requests)
        ref = position_action_cost_model_ref(cost.to_dict())
        samples = tuple(replace(sample, cost_model_ref=ref) for sample in self.outcomes())
        row = self.row(self.run_comparison(requests=requests, outcomes=samples))
        self.assertEqual(1, row["paired_sample_count"])
        self.assertEqual(1, row["excluded_counts"]["outcome_cost_model_outside_validity"])
        expired = replace(cost, valid_to=self.now.isoformat())
        requests = tuple(replace(request, cost_model=expired) for request in self.requests)
        result = self.run_comparison(requests=requests)
        self.assertIn("cost_model_outside_current_validity", self.row(result)["reasons"])

    def test_json_typed_and_permuted_inputs_match_without_mutation_or_io(self):
        from copy import deepcopy
        from unittest.mock import patch
        document = self.document()
        original = deepcopy(document)
        with patch("sqlite3.connect", side_effect=AssertionError("unexpected DB")), \
             patch("kiwoom_monitor.application.order_lifecycle.OrderLifecycle.submit", side_effect=AssertionError("unexpected order")):
            result = self.run_comparison()
            self.assertEqual(result, evaluate_document(document))
            self.assertEqual(result, self.run_comparison(requests=tuple(reversed(self.requests)),
                                                        outcomes=tuple(reversed(self.outcomes()))))
        self.assertEqual(original, document)
        changed = (*self.outcomes()[:2], replace(self.outcomes()[2], gross_change_won=201), self.outcomes()[3])
        self.assertNotEqual(result["decision_id"], self.run_comparison(outcomes=changed)["decision_id"])

    def test_excluded_future_values_do_not_change_the_comparison_id(self):
        from datetime import timedelta
        samples = list(self.outcomes())
        samples[2] = replace(samples[2], available_at=self.now + timedelta(seconds=1))
        result = self.run_comparison(outcomes=tuple(samples))
        samples[2] = replace(samples[2], gross_change_won=999_999)
        self.assertEqual(result, self.run_comparison(outcomes=tuple(samples)))

    def test_duplicate_unrequested_and_invalid_sample_inputs_are_rejected(self):
        samples = self.outcomes()
        for bad in ((*samples, samples[2]), (*samples, replace(samples[2], input_ref="other"))):
            with self.assertRaisesRegex(ValueError, "duplicate"):
                self.run_comparison(outcomes=bad)
        with self.assertRaises(ValueError):
            replace(samples[0], gross_change_won=True)
        with self.assertRaises(ValueError):
            replace(samples[0], fee_tax_won=-1)
        with self.assertRaises(ValueError):
            replace(samples[0], gross_change_won=float("nan"))
        with self.assertRaises(ValueError):
            replace(samples[0], value_basis="net_pnl_after_fees")
        with self.assertRaises(ValueError):
            replace(samples[0], available_at=self.now.replace(tzinfo=None))
        with self.assertRaises(ValueError):
            self.run_comparison(outcomes=(*samples, replace(samples[2], input_ref="add", case_ref="add", action="ADD")))

    def test_comparison_requires_common_cut_baseline_budget_and_declared_samples(self):
        for values in ({"baseline_action": "EXIT"}, {"horizon_seconds": 1}, {"minimum_samples": 1}):
            with self.assertRaises(ValueError):
                replace(self.parameters, **values)
        changed = replace(self.requests[1], account_input_ref="other")
        with self.assertRaisesRegex(ValueError, "same account/input cut"):
            self.run_comparison(requests=(self.requests[0], changed))
        with self.assertRaises(ValueError):
            self.run_comparison(requests=(self.requests[0], self.requests[0]))
        document = self.document()
        document["outcomes"] = [{}] * 10_001
        with self.assertRaisesRegex(ValueError, "at most 10000"):
            evaluate_document(document)
        with self.assertRaises(ValueError):
            evaluate_document({**self.document(), "order_authorized": True})

    def test_negative_fractional_mean_is_not_floored_and_single_sample_is_unknown(self):
        samples = tuple(replace(sample, gross_change_won=(-1 if sample.case_ref == "a" else 0), fee_tax_won=0)
                        for sample in self.outcomes())
        row = self.row(self.run_comparison(outcomes=samples))
        self.assertEqual(-0.5, row["estimate"]["mean_net_change_won"])
        self.assertEqual(0, row["paired_delta"]["mean_net_change_won"])
        row = self.row(self.run_comparison(outcomes=tuple(sample for sample in samples if sample.case_ref == "a")))
        self.assertIsNone(row["estimate"])

    def test_r3_policy_result_and_target_ids_are_preserved_in_comparison(self):
        from kiwoom_monitor.application.market_theme_shadow import evaluate_market_theme_shadow_batch
        from kiwoom_monitor.application.position_target import evaluate_position_target_shadow
        fixture = StockThesisShadowTests()
        fixture.setUp()
        decisions = evaluate_market_theme_shadow_batch(fixture.weak_inputs(),
                                                       (fixture.request("PRICE_ONLY"), fixture.request("REDUCE_ON_RISK")))
        self.requests = tuple(replace(request, policy_version=source["thesis_policy_version"],
                                      source_decision_ref=source["decision_id"]) for request, source in zip(self.requests, decisions))
        result = self.run_comparison()
        for request, source in zip(self.requests, decisions):
            self.assertEqual(evaluate_position_target_shadow(request, self.account, ()), result["targets"][request.action])
            self.assertEqual(source["decision_id"], result["targets"][request.action]["source_decision_ref"])
        self.assertEqual("COMPARABLE", result["status"])


class PositionActionSelectionTests(unittest.TestCase):
    setUp = PositionActionComparisonTests.setUp
    sample = PositionActionComparisonTests.sample
    outcomes = PositionActionComparisonTests.outcomes
    run_comparison = PositionActionComparisonTests.run_comparison
    document = PositionActionComparisonTests.document

    def policy(self, **changes):
        from datetime import timedelta
        from kiwoom_monitor.application.position_target import PositionActionSelectionPolicy
        first = self.requests[0]
        return replace(PositionActionSelectionPolicy('paired-net-action-selection/v1', 19, 1,
            'ALLOW', 'research-risk:1', first.account_ref, first.symbol,
            first.account_input_ref, first.orders_input_ref,
            self.now - timedelta(seconds=1), self.now + timedelta(seconds=20)), **changes)

    def selection(self, policy=None, **kwargs):
        return self.run_comparison(parameters=replace(self.parameters,
            selection_policy=policy or self.policy()), **kwargs)['action_selection']

    def test_net_cost_and_uncertainty_threshold_are_hand_checkable(self):
        result = self.selection()
        self.assertEqual(('REDUCE', 'SELECTED', False), (result['action'], result['status'], result['order_authorized']))
        self.assertEqual({'net_advantage_won': 45.0, 'standard_error_won': 25.0,
            'penalized_advantage_won': 20.0, 'paired_sample_count': 2}, result['scores']['REDUCE'])
        self.assertEqual('HOLD', self.selection(self.policy(minimum_net_advantage_won=20))['action'])
        self.assertEqual('HOLD', self.selection(self.policy(uncertainty_multiplier=3))['action'])
        costly = tuple(replace(o, fee_tax_won=100) if o.action == 'REDUCE' else o for o in self.outcomes())
        self.assertEqual('HOLD', self.selection(outcomes=costly)['action'])

    def test_protection_bypasses_missing_ev_and_unknown_order_but_never_submits(self):
        for orders in ((), (self.fixture.record(state='SUBMISSION_UNKNOWN'),),
                       (self.fixture.record(state='CANCEL_PENDING'),)):
            result = self.selection(self.policy(risk_constraint='EXIT_REQUIRED'), outcomes=(), orders=orders)
            self.assertEqual(('EXIT', 'RISK_REQUIRED', True, None, False), tuple(result[k] for k in
                ('action', 'status', 'risk_path_required', 'target_decision_ref', 'order_authorized')))

    def test_protection_target_reference_requires_calculated_existing_exit_target(self):
        self.requests += (replace(self.requests[0], action='EXIT', target_quantity=0),)
        result = self.selection(self.policy(risk_constraint='EXIT_REQUIRED'), outcomes=())
        self.assertTrue(result['target_decision_ref'].startswith('position-target-shadow:'))
        result = self.selection(self.policy(risk_constraint='EXIT_REQUIRED'), outcomes=(),
            orders=(self.fixture.record(state='CANCEL_PENDING'),))
        self.assertIsNone(result['target_decision_ref'])

    def test_risk_ref_cut_scope_and_time_cannot_be_refreshed_by_new_quote(self):
        from datetime import timedelta
        for changes, reason in (({'account_ref':'another'}, 'selection_risk_input_cut_mismatch'),
            ({'symbol':'000660'}, 'selection_risk_input_cut_mismatch'),
            ({'account_input_ref':'new'}, 'selection_risk_input_cut_mismatch'),
            ({'orders_input_ref':'new'}, 'selection_risk_input_cut_mismatch'),
            ({'available_at':self.now + timedelta(seconds=1)}, 'selection_risk_input_from_future'),
            ({'valid_until':self.now}, 'selection_risk_input_expired')):
            with self.subTest(changes=changes):
                result = self.selection(self.policy(risk_constraint='EXIT_REQUIRED', **changes))
                self.assertEqual(('WAIT', 'BLOCKED', False), tuple(result[k] for k in ('action','status','risk_path_required')))
                self.assertIn(reason, result['reasons'])

    def test_expansion_constraints_exclude_buy_actions_without_inventing_risk_approval(self):
        self.requests += (replace(self.requests[0], action='ADD', target_quantity=20),)
        samples = (*self.outcomes(), self.sample('ADD','a',1000), self.sample('ADD','b',1000))
        self.assertEqual('ADD', self.selection(outcomes=samples)['action'])
        for constraint in ('NO_NEW_RISK','REDUCE_ONLY'):
            result = self.selection(self.policy(risk_constraint=constraint), outcomes=samples)
            self.assertEqual('REDUCE', result['action'])
            self.assertNotIn('ADD', result['eligible_actions'])
        halted = self.selection(self.policy(risk_constraint='HALT_NEW_ORDERS'), outcomes=samples)
        self.assertEqual('WAIT', halted['action'])
        self.assertEqual({}, halted['scores'])

    def test_missing_future_and_unfunded_samples_do_not_choose_action(self):
        from datetime import timedelta
        for samples in ((), self.outcomes()[:3],
            tuple(replace(o, available_at=self.now+timedelta(seconds=1)) if o.action=='REDUCE' else o for o in self.outcomes())):
            result = self.selection(outcomes=samples)
            self.assertEqual(('WAIT','BLOCKED'), (result['action'],result['status']))
        result = self.selection(orders=(self.fixture.record(state='SUBMISSION_UNKNOWN'),))
        self.assertEqual('WAIT', result['action'])
        self.requests += (replace(self.requests[0], action='ADD', target_quantity=20),)
        samples = (*self.outcomes(), self.sample('ADD','a',1000),self.sample('ADD','b',1000))
        self.assertEqual('WAIT', self.selection(outcomes=samples, account=replace(self.account,available_cash_won=1))['action'])

    def test_candidates_with_different_paired_cases_are_not_ranked(self):
        self.requests += (replace(self.requests[0], action='EXIT', target_quantity=0),)
        samples = (*self.outcomes(),self.sample('HOLD','c',0),self.sample('EXIT','b',1000),self.sample('EXIT','c',1000))
        result = self.selection(outcomes=samples)
        self.assertEqual('WAIT', result['action'])
        self.assertIn('candidate_paired_cohorts_differ', result['reasons'])

    def test_equal_best_actions_and_equal_zero_do_not_churn(self):
        self.requests += (replace(self.requests[0], action='EXIT', target_quantity=0),)
        samples = (*self.outcomes(),self.sample('EXIT','a',200,30),self.sample('EXIT','b',-50,30))
        self.assertIn('best_action_score_tied', self.selection(outcomes=samples)['reasons'])
        self.requests = self.requests[:2]
        equal = tuple(replace(o, gross_change_won=100 if o.case_ref=='a' else -100, fee_tax_won=0) for o in self.outcomes())
        self.assertEqual('HOLD', self.selection(self.policy(minimum_net_advantage_won=0), outcomes=equal)['action'])

    def test_opt_in_document_ids_and_timezone_canonicalization(self):
        from datetime import timezone
        from kiwoom_monitor.application.position_target import evaluate_position_action_comparison_document
        legacy = self.run_comparison()
        self.assertNotIn('action_selection',legacy)
        self.assertNotIn('selection_policy',legacy['parameters'])
        self.parameters = replace(self.parameters,selection_policy=self.policy())
        direct = self.run_comparison()
        self.assertEqual(direct,evaluate_position_action_comparison_document(self.document()))
        self.assertNotEqual(legacy['decision_id'],direct['decision_id'])
        policy = self.parameters.selection_policy
        result = self.run_comparison(parameters=replace(self.parameters, selection_policy=replace(policy,
            available_at=policy.available_at.astimezone(timezone.utc),valid_until=policy.valid_until.astimezone(timezone.utc))))
        self.assertEqual(direct['decision_id'],result['decision_id'])
        changed = self.run_comparison(parameters=replace(self.parameters,selection_policy=replace(policy,minimum_net_advantage_won=20)))
        self.assertNotEqual(direct['decision_id'],changed['decision_id'])

    def test_policy_validation_rejects_ambiguous_and_nonfinite_values(self):
        from datetime import timedelta
        for changes in ({'version':'other'},{'minimum_net_advantage_won':True},
            {'minimum_net_advantage_won':-1},{'uncertainty_multiplier':True},
            {'uncertainty_multiplier':float('nan')},{'uncertainty_multiplier':float('inf')},
            {'uncertainty_multiplier':-1},{'risk_constraint':'CAP'},{'risk_input_ref':''},
            {'valid_until':self.now-timedelta(seconds=2)}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.policy(**changes)


class NativePositionSelectionInputsTests(unittest.TestCase):
    def capability_policy(self, capabilities=(), **changes):
        from kiwoom_monitor.application.position_target import PositionActionCapabilitySelectionPolicy
        raw=asdict(self.parameters.selection_policy)
        raw.update(version='paired-net-action-selection/v2', input_mode='owner_ram_risk',
            required_data_capabilities=capabilities,
            thesis_ref=self.thesis_fixture.thesis.thesis_id if isinstance(capabilities,tuple) and 'stock_thesis' in capabilities else '')
        raw.update(changes)
        return PositionActionCapabilitySelectionPolicy(**raw)

    def test_action_news_requirement_excludes_only_add_and_keeps_supported_reduce(self):
        self.requests+=(replace(self.requests[0],action='ADD',target_quantity=15),)
        outcomes=(*self.outcomes(),self.sample('ADD','a',10000),self.sample('ADD','b',10000))
        policy=self.capability_policy(('sampled_price',),action_required_data_capabilities=(('ADD',('news_context',)),))
        result=self.evaluate(policy=policy,thesis=None,outcomes=outcomes)
        self.assertEqual('COMPARABLE',result['status'])
        self.assertEqual(('SELECTED','REDUCE'),tuple(result['action_selection'][k] for k in ('status','action')))
        self.assertEqual(['REDUCE'],result['action_selection']['eligible_actions'])
        self.assertEqual(['required_data_capability_unavailable:news_context'],result['action_selection']['action_input_rejections']['ADD'])
        self.assertEqual([],result['selection_inputs']['missing_data_capabilities'])
        self.assertEqual(['news_context'],result['selection_inputs']['action_inputs']['ADD']['missing_data_capabilities'])
        self.assertFalse(result['order_authorized'])

    def test_action_thesis_is_scoped_to_candidate_and_missing_thesis_keeps_other_actions(self):
        self.requests+=(replace(self.requests[0],action='ADD',target_quantity=15),)
        outcomes=(*self.outcomes(),self.sample('ADD','a',10000),self.sample('ADD','b',10000))
        policy=self.capability_policy(('sampled_price',),thesis_ref=self.thesis_fixture.thesis.thesis_id,
            action_required_data_capabilities=(('ADD',('stock_thesis',)),))
        for thesis in (None,self.thesis):
            result=self.evaluate(policy=policy,thesis=thesis,outcomes=outcomes)
            self.assertEqual('REDUCE',result['action_selection']['action'])
            self.assertEqual('ALLOW',result['action_selection']['risk_constraint'])
            self.assertTrue(result['selection_inputs']['action_inputs']['ADD']['reasons'])
            self.assertEqual([],result['selection_inputs']['reasons'])
        exit_thesis=evaluate_stock_thesis_shadow(self.thesis_fixture.weak_inputs(),self.thesis_fixture.request('IMMEDIATE_EXIT'))
        result=self.evaluate(policy=policy,thesis=exit_thesis,outcomes=())
        self.assertEqual(('EXIT','RISK_REQUIRED'),tuple(result['action_selection'][k] for k in ('action','status')))

    def test_action_specific_thesis_expiry_shortens_only_selected_action_receipt(self):
        from copy import deepcopy
        from datetime import datetime,timedelta
        near=deepcopy(self.thesis);near['valid_until']=(self.now+timedelta(seconds=1)).isoformat()
        self.requests+=(replace(self.requests[0],action='ADD',target_quantity=15),)
        outcomes=(*self.outcomes(),self.sample('ADD','a',10000),self.sample('ADD','b',10000))
        for action in ('ADD','REDUCE'):
            policy=self.capability_policy(('sampled_price',),thesis_ref=self.thesis_fixture.thesis.thesis_id,
                action_required_data_capabilities=((action,('stock_thesis',)),))
            case_outcomes=outcomes if action=='ADD' else (*self.outcomes(),self.sample('ADD','a',-10000),self.sample('ADD','b',-10000))
            result=self.evaluate(policy=policy,thesis=near,outcomes=case_outcomes)
            chosen=result['action_selection']['action']
            self.assertEqual('REDUCE',chosen)
            limit=datetime.fromisoformat(result['selection_inputs']['expires_at'])
            if chosen==action:self.assertEqual(datetime.fromisoformat(near['valid_until']),limit)
            else:self.assertGreater(limit,datetime.fromisoformat(near['valid_until']))

    def test_action_matrix_baseline_missing_and_native_risk_are_not_bypassed(self):
        policy=self.capability_policy(('sampled_price',),action_required_data_capabilities=(('HOLD',('news_context',)),))
        result=self.evaluate(policy=policy,thesis=None)
        self.assertEqual(('WAIT','BLOCKED'),tuple(result['action_selection'][k] for k in ('action','status')))
        self.assertIn('hold_required_data_unavailable',result['action_selection']['reasons'])
        result=self.evaluate(policy=replace(policy,risk_constraint='EXIT_REQUIRED'),thesis=None,outcomes=())
        self.assertEqual(('EXIT','RISK_REQUIRED'),tuple(result['action_selection'][k] for k in ('action','status')))
        result=self.evaluate(policy=policy,thesis=None,risk=None)
        self.assertIn('native_risk_unavailable',result['selection_inputs']['reasons'])

    def test_action_data_matrix_roundtrip_and_invalid_candidates_are_rejected(self):
        from copy import deepcopy
        from kiwoom_monitor.application.position_target import position_action_comparison_inputs_from_document,evaluate_position_action_comparison_document
        policy=self.capability_policy(('sampled_price',),action_required_data_capabilities=(('REDUCE',('news_context',)),))
        self.parameters=replace(self.parameters,selection_policy=policy)
        document=self.document()
        restored=position_action_comparison_inputs_from_document(document)[0]
        self.assertEqual(self.parameters,restored)
        for matrix in ((),(('REDUCE',('news_context','news_context')),),
            (('REDUCE',('unknown',)),),(('REDUCE',()),('ADD',())),(('WAIT',()),),
            (('ADD',()),('ADD',()))):
            with self.assertRaises(ValueError):self.capability_policy((),action_required_data_capabilities=matrix)
        bad=deepcopy(document);bad['parameters']['selection_policy']['action_required_data_capabilities']=[['ADD',[]]]
        with self.assertRaises(ValueError):position_action_comparison_inputs_from_document(bad)
        self.parameters=replace(self.parameters,selection_policy=replace(policy,action_required_data_capabilities=None))
        legacy_document=self.document();without_optional=deepcopy(legacy_document)
        without_optional['parameters']['selection_policy'].pop('action_required_data_capabilities')
        self.assertEqual(evaluate_position_action_comparison_document(legacy_document),
            evaluate_position_action_comparison_document(without_optional))

    def test_capability_policy_without_theme_or_news_uses_native_risk_and_paired_values(self):
        policy=self.capability_policy(('sampled_price',))
        result=self.evaluate(policy=policy,thesis=None)
        self.assertEqual('REDUCE',result['action_selection']['action'])
        self.assertEqual('SELECTED',result['action_selection']['status'])
        receipt=result['selection_inputs']
        self.assertEqual(['sampled_price'],receipt['required_data_capabilities'])
        self.assertEqual([],receipt['missing_data_capabilities'])
        self.assertIsNone(receipt['thesis_decision_ref'])
        self.assertEqual(self.risk.snapshot_id,receipt['risk_snapshot_ref'])
        self.assertFalse(result['order_authorized'])
        unused={**self.thesis,'action':'EXIT','valid_until':'2000-01-01T00:00:00+00:00'}
        self.assertEqual(result,self.evaluate(policy=policy,thesis=unused))

    def test_capabilities_only_block_declared_missing_context_and_hard_risk_preempts(self):
        for capability in ('stock_thesis','theme_context','news_context','turnover_delta'):
            with self.subTest(capability=capability):
                policy=self.capability_policy((capability,))
                result=self.evaluate(policy=policy,thesis=None)
                self.assertEqual('BLOCKED',result['action_selection']['status'])
                self.assertEqual([capability],result['selection_inputs']['missing_data_capabilities'])
                self.assertIn('required_data_capability_unavailable:'+capability,result['selection_inputs']['reasons'])
        policy=self.capability_policy(('stock_thesis',))
        result=self.evaluate(policy=policy)
        self.assertEqual('REDUCE_ONLY',result['action_selection']['risk_constraint'])
        self.assertEqual([],result['selection_inputs']['missing_data_capabilities'])
        result=self.evaluate(policy=self.capability_policy(('news_context',),risk_constraint='EXIT_REQUIRED'),outcomes=())
        self.assertEqual('RISK_REQUIRED',result['action_selection']['status'])
        self.assertEqual('EXIT',result['action_selection']['action'])
        self.assertTrue(result['action_selection']['risk_path_required'])
        self.assertFalse(result['order_authorized'])

    def test_capability_policy_cannot_omit_native_risk_scope_or_expiry(self):
        from datetime import timedelta
        policy=self.capability_policy(())
        for risk in (None,self.risk_snapshot(observed=self.now-timedelta(seconds=61),account_at=self.now-timedelta(seconds=61)),
                     replace(self.risk,balance_mismatch_count=1),replace(self.risk,submission_unknown_count=1)):
            with self.subTest(risk=risk):
                result=self.evaluate(policy=policy,risk=risk,thesis=None)
                self.assertEqual('BLOCKED',result['action_selection']['status'])
                self.assertFalse(result['order_authorized'])
        result=self.evaluate(policy=replace(policy,valid_until=self.now),thesis=None)
        self.assertEqual('BLOCKED',result['action_selection']['status'])

    def test_capability_json_round_trip_and_legacy_mode_cannot_silently_downgrade(self):
        from copy import deepcopy
        from kiwoom_monitor.application.position_target import position_action_comparison_inputs_from_document
        policy=self.capability_policy(('sampled_price',))
        self.parameters=replace(self.parameters,selection_policy=policy)
        document=self.document()
        restored=position_action_comparison_inputs_from_document(document)[0].selection_policy
        self.assertEqual(policy,restored)
        for caps in (None,('unknown',),('sampled_price','sampled_price'),('stock_thesis','sampled_price')):
            with self.assertRaises(ValueError):self.capability_policy(caps)
        for version,mode in (('paired-net-action-selection/v1','owner_ram_risk'),
                             ('paired-net-action-selection/v2','supplied_fixed')):
            with self.assertRaises(ValueError):self.capability_policy((),version=version,input_mode=mode)
        bad=deepcopy(document);bad['parameters']['selection_policy']['required_data_capabilities']='sampled_price'
        with self.assertRaises(ValueError):position_action_comparison_inputs_from_document(bad)

    policy = PositionActionSelectionTests.policy
    sample = PositionActionComparisonTests.sample
    outcomes = PositionActionComparisonTests.outcomes
    document = PositionActionComparisonTests.document

    def setUp(self):
        from tests.unit.test_mock_automation_risk import ACCOUNT
        PositionActionComparisonTests.setUp(self)
        del self.evaluate  # Base fixture installs a function; use this class's native adapter.
        self.account = replace(self.account, account_ref=ACCOUNT)
        self.requests = tuple(replace(r,account_ref=ACCOUNT) for r in self.requests)
        self.thesis_fixture = StockThesisShadowTests(); self.thesis_fixture.setUp()
        self.thesis = evaluate_stock_thesis_shadow(self.thesis_fixture.weak_inputs(), self.thesis_fixture.request('REDUCE_ON_RISK'))
        self.parameters = replace(self.parameters,selection_policy=self.policy(
            input_mode='owner_ram_risk_and_thesis',thesis_ref=self.thesis_fixture.thesis.thesis_id))
        self.risk = self.risk_snapshot()

    def risk_snapshot(self, *, complete=True, positions=None, observed=None, account_at=None, active=()):
        from tests.unit.test_mock_automation_risk import event,cost
        from kiwoom_monitor.application.mock_automation_risk import build_mock_automation_risk_snapshot
        from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import MockAccountRecovery
        at = observed or self.now
        values = (event(1,'buy','BUY',10,1000,at),)
        costs = (cost(at.astimezone(self.now.tzinfo).date(),'매수',10_000,0),)
        recovery = MockAccountRecovery(replace(self.account,as_of=account_at or at,
            positions=self.account.positions if positions is None else positions),())
        return build_mock_automation_risk_snapshot(recovery,values,costs,active,execution_run_id='run-a',
            binding_revision=3,reconciliation_revision=1,observed_at=at,broker_query_started_at=at,
            broker_query_completed_at=at,cost_query_complete=complete)

    def evaluate(self, *, risk='default', thesis='default', outcomes=None, policy=None, account=None, orders=()):
        from kiwoom_monitor.application.position_target import evaluate_position_action_comparison_shadow
        return evaluate_position_action_comparison_shadow(
            replace(self.parameters,selection_policy=policy or self.parameters.selection_policy),
            self.requests, account or self.account, orders, self.outcomes() if outcomes is None else outcomes,
            risk_snapshot=self.risk if risk=='default' else risk,
            stock_thesis_result=self.thesis if thesis=='default' else thesis)

    def test_native_builder_and_existing_reduce_thesis_bind_without_relabeling_sources(self):
        result = self.evaluate()
        self.assertEqual('REDUCE',result['action_selection']['action'])
        self.assertEqual('REDUCE_ONLY',result['action_selection']['risk_constraint'])
        self.assertEqual(self.risk.snapshot_id,result['selection_inputs']['risk_snapshot_ref'])
        self.assertEqual(self.thesis['decision_id'],result['selection_inputs']['thesis_decision_ref'])
        self.assertEqual('native_observation_not_execution_approval',result['action_selection']['risk_authority'])
        self.assertEqual(self.requests[1].source_decision_ref,result['targets']['REDUCE']['source_decision_ref'])
        self.assertFalse(result['order_authorized'])

    def test_hold_does_not_create_add_or_reenter_admission(self):
        self.requests += (replace(self.requests[0],action='ADD',target_quantity=20),)
        samples = (*self.outcomes(),self.sample('ADD','a',9999),self.sample('ADD','b',9999))
        thesis = evaluate_stock_thesis_shadow(self.thesis_fixture.weak_inputs(),self.thesis_fixture.request('PRICE_ONLY'))
        result = self.evaluate(thesis=thesis,outcomes=samples)
        self.assertEqual('NO_NEW_RISK',result['action_selection']['risk_constraint'])
        self.assertNotIn('ADD',result['action_selection']['eligible_actions'])

    def test_wait_thesis_and_unavailable_risk_do_not_choose_action(self):
        thesis = evaluate_stock_thesis_shadow(self.thesis_fixture.weak_inputs(),self.thesis_fixture.request('CONFIRM_THEN_EXIT'))
        for risk, value in ((self.risk,thesis),(None,self.thesis),(self.risk,None)):
            result = self.evaluate(risk=risk,thesis=value)
            self.assertEqual(('WAIT','BLOCKED'),tuple(result['action_selection'][k] for k in ('action','status')))

    def test_explicit_protection_constraint_precedes_thesis_wait_but_is_not_execution_approval(self):
        thesis = evaluate_stock_thesis_shadow(self.thesis_fixture.weak_inputs(),self.thesis_fixture.request('CONFIRM_THEN_EXIT'))
        policy = replace(self.parameters.selection_policy,risk_constraint='EXIT_REQUIRED')
        result = self.evaluate(thesis=thesis,policy=policy,outcomes=())
        self.assertEqual(('EXIT','RISK_REQUIRED',True,None,False),tuple(result['action_selection'][k] for k in
            ('action','status','risk_path_required','target_decision_ref','order_authorized')))

    def test_valid_protection_thesis_precedes_missing_ev_and_risk_without_order_quantity(self):
        thesis = evaluate_stock_thesis_shadow(self.thesis_fixture.weak_inputs(),self.thesis_fixture.request('IMMEDIATE_EXIT'))
        record = self.fixture.record(state='SUBMISSION_UNKNOWN')
        record = replace(record,intent=replace(record.intent,account_ref=self.account.account_ref))
        result = self.evaluate(risk=None,thesis=thesis,outcomes=(),orders=(record,))
        selection = result['action_selection']
        self.assertEqual(('EXIT','RISK_REQUIRED',True,None,False),tuple(selection[k] for k in
            ('action','status','risk_path_required','target_decision_ref','order_authorized')))
        self.assertIn('native_risk_unavailable',selection['reasons'])

    def test_risk_other_account_cut_future_stale_and_tampered_content_block(self):
        from datetime import timedelta
        for risk in (replace(self.risk,account_ref='another'),replace(self.risk,account_as_of=self.now-timedelta(seconds=1)),
            replace(self.risk,daily_net_pnl_won=999),self.risk_snapshot(observed=self.now+timedelta(seconds=1)),
            self.risk_snapshot(observed=self.now-timedelta(seconds=31)),
            self.risk_snapshot(observed=self.now-timedelta(seconds=1),account_at=self.now)):
            result = self.evaluate(risk=risk)
            self.assertEqual('WAIT',result['action_selection']['action'])
            self.assertIn('native_risk_scope_time_or_content_invalid',result['selection_inputs']['reasons'])

    def test_native_unknown_intent_blocks_even_when_broker_and_target_inputs_look_complete(self):
        record = self.fixture.record(state='SUBMISSION_UNKNOWN')
        record = replace(record,intent=replace(record.intent,account_ref=self.account.account_ref))
        risk = self.risk_snapshot(active=(record,))
        self.assertEqual(1,risk.submission_unknown_count)
        result = self.evaluate(risk=risk)
        self.assertEqual('WAIT',result['action_selection']['action'])
        self.assertIn('native_risk_reconciliation_or_order_unconfirmed',result['selection_inputs']['reasons'])

    def test_missing_native_cost_restricts_expansion_without_blocking_reduction(self):
        result = self.evaluate(risk=self.risk_snapshot(complete=False))
        self.assertEqual('REDUCE',result['action_selection']['action'])
        self.assertIn('native_risk_cost_or_daily_pnl_unverified',result['selection_inputs']['reasons'])
        result = self.evaluate(risk=self.risk_snapshot(positions={}))
        self.assertEqual('WAIT',result['action_selection']['action'])
        self.assertIn('native_risk_reconciliation_or_order_unconfirmed',result['selection_inputs']['reasons'])

    def test_thesis_scope_future_expiry_quality_and_unknown_action_block(self):
        from datetime import timedelta
        for change in ({'thesis_ref':'other'},{'symbol':'000660'},{'thesis_venue':'NXT'},
            {'available_at':(self.now+timedelta(seconds=1)).isoformat()}, {'valid_until':self.now.isoformat()},
            {'quality':{'status':'PARTIAL'}},{'action':'ADD'},{'order_authorized':True}):
            result = self.evaluate(thesis={**self.thesis,**change})
            self.assertEqual('WAIT',result['action_selection']['action'])

    def test_policy_expiry_and_scope_cannot_be_bypassed_by_protection(self):
        thesis = evaluate_stock_thesis_shadow(self.thesis_fixture.weak_inputs(),self.thesis_fixture.request('IMMEDIATE_EXIT'))
        for change in ({'valid_until':self.now},{'account_ref':'other'},{'symbol':'000660'}):
            result = self.evaluate(thesis=thesis,policy=replace(self.parameters.selection_policy,**change))
            self.assertEqual(('WAIT',False),tuple(result['action_selection'][k] for k in ('action','risk_path_required')))

    def test_native_mode_requires_explicit_thesis_and_fixed_document_never_claims_native_risk(self):
        from kiwoom_monitor.application.position_target import evaluate_position_action_comparison_document
        result = evaluate_position_action_comparison_document(self.document())
        self.assertEqual('WAIT',result['action_selection']['action'])
        self.assertIsNone(result['selection_inputs']['risk_snapshot_ref'])
        for changes in ({'input_mode':'other'},{'thesis_ref':''},{'input_mode':'supplied_fixed'}):
            with self.assertRaises(ValueError): replace(self.parameters.selection_policy,**changes)


class StockThesisShadowTests(unittest.TestCase):
    def setUp(self):
        self.first = first_evaluation()
        self.thesis = build_entry_thesis(
            run_id="shadow-run", symbol="005930", leadership=self.first,
            factor_refs=("entry-factor-r1",), hypothesis_refs=("entry-hypothesis-r1",),
            created_at=self.first.as_of, available_at=self.first.as_of,
        )

    def request(self, policy="PRICE_ONLY"):
        return StockThesisShadowRequest(self.thesis, "KRX", policy)

    def weak_inputs(self):
        points = (*initial_points(),
                  point("005930", 22, 104, 700), point("000660", 22, 101, 100),
                  point("005930", 29, 102, 900), point("000660", 29, 101, 100))
        return replace(shadow_inputs(), decision_time=f"{DAY}T09:00:30+09:00",
                       second_points=points, previous_leadership_state=self.first.state)

    def switched_inputs(self):
        points = (*initial_points(),
                  point("005930", 22, 105, 100), point("000660", 22, 102, 1800),
                  point("005930", 29, 105, 100), point("000660", 29, 108, 2200))
        candidate = evaluate_theme_leadership(
            membership(), points, as_of=f"{DAY}T09:00:30+09:00", venue="KRX",
            continuity_status="COMPLETE", parameters=PARAMETERS, previous_state=self.first.state,
        )
        return replace(shadow_inputs(), decision_time=f"{DAY}T09:00:34+09:00",
                       second_points=(*points, point("005930", 33, 105, 100), point("000660", 33, 109, 2300)),
                       previous_leadership_state=candidate.state)

    def test_intact_thesis_is_hold_with_explicit_references_and_no_order(self):
        inputs = shadow_inputs()
        result = evaluate_stock_thesis_shadow(inputs, self.request())
        self.assertEqual("VALID", result["thesis_state"])
        self.assertEqual("HOLD", result["action"])
        self.assertEqual("005930", result["symbol"])
        self.assertIn(self.thesis.thesis_id, result["input_refs"])
        self.assertIn("entry-factor-r1", result["input_refs"])
        self.assertIn("entry-hypothesis-r1", result["input_refs"])
        self.assertFalse(result["order_authorized"])
        self.assertFalse(result["source_decision"]["order_authorized"])
        self.assertEqual(evaluate_market_theme_shadow(inputs), result["context"])

    def test_price_risk_preserves_all_existing_policy_actions(self):
        inputs = self.weak_inputs()
        raw = evaluate_theme_leadership(
            membership(), inputs.second_points, as_of=inputs.decision_time, venue="KRX",
            continuity_status="COMPLETE", parameters=PARAMETERS, previous_state=self.first.state,
        )
        for policy, action in (("PRICE_ONLY", "HOLD"), ("IMMEDIATE_EXIT", "EXIT"),
                               ("REDUCE_ON_RISK", "REDUCE"), ("CONFIRM_THEN_EXIT", "WAIT")):
            with self.subTest(policy=policy):
                result = evaluate_stock_thesis_shadow(inputs, self.request(policy))
                self.assertEqual("AT_RISK", result["thesis_state"])
                self.assertEqual(action, result["action"])
                self.assertEqual(evaluate_entry_thesis(self.thesis, raw, policy_version=policy).to_dict(),
                                 result["source_decision"])
                self.assertFalse(result["order_authorized"])

    def test_leader_replacement_invalidates_entry_thesis(self):
        result = evaluate_stock_thesis_shadow(self.switched_inputs(), self.request("CONFIRM_THEN_EXIT"))
        self.assertEqual("INVALIDATED", result["thesis_state"])
        self.assertEqual("EXIT", result["action"])
        self.assertIn("entry_leader_replaced", result["reasons"])
        self.assertEqual("005930", result["invalidation_conditions"][0]["entry_leader_id"])
        self.assertEqual(changed_evaluation(self.first).revision_id, result["source_decision"]["leadership_revision_id"])

    def test_missing_market_is_not_a_veto_on_existing_thesis_protection(self):
        result = evaluate_stock_thesis_shadow(replace(self.switched_inputs(), market_frames=()),
                                               self.request("IMMEDIATE_EXIT"))
        self.assertEqual("PARTIAL", result["context"]["market"]["quality"]["status"])
        self.assertEqual("COMPLETE", result["quality"]["status"])
        self.assertEqual("EXIT", result["action"])
        self.assertEqual(result["context"]["leadership"]["expires_at"], result["valid_until"])

    def test_missing_leadership_keeps_unknown_without_forced_liquidation(self):
        for inputs in (replace(shadow_inputs(), theme_membership=None),
                       replace(shadow_inputs(), continuity_status="PARTIAL")):
            with self.subTest(inputs=inputs):
                result = evaluate_stock_thesis_shadow(inputs, self.request("IMMEDIATE_EXIT"))
                self.assertEqual("UNKNOWN", result["thesis_state"])
                self.assertEqual("NO_TRADE", result["proposal"])
                self.assertEqual("HOLD", result["action"])
                self.assertEqual(result["decision_time"], result["valid_until"])

    def test_json_and_typed_consumers_are_identical_and_input_is_preserved(self):
        inputs = self.weak_inputs()
        request = self.request("REDUCE_ON_RISK")
        original = asdict(inputs)
        document = json.loads(json.dumps({"schema": STOCK_THESIS_SHADOW_VERSION,
                                         "inputs": original, "stock_thesis": asdict(request)}))
        result = evaluate_stock_thesis_shadow(inputs, request)
        self.assertEqual(result, evaluate_document(document))
        self.assertEqual(original, asdict(inputs))
        self.assertEqual(result, evaluate_stock_thesis_shadow(inputs, request))
        self.assertNotEqual(result["decision_id"], evaluate_stock_thesis_shadow(inputs, self.request("PRICE_ONLY"))["decision_id"])

    def test_future_thesis_and_cross_venue_or_theme_inputs_are_rejected(self):
        future = build_entry_thesis(
            run_id="future-run", symbol="005930", leadership=self.first,
            factor_refs=(), hypothesis_refs=(),
            created_at=f"{DAY}T09:00:21+09:00", available_at=f"{DAY}T09:00:21+09:00",
        )
        with self.assertRaisesRegex(ValueError, "not available"):
            evaluate_stock_thesis_shadow(shadow_inputs(), StockThesisShadowRequest(future, "KRX", "PRICE_ONLY"))
        with self.assertRaisesRegex(ValueError, "venue differ"):
            evaluate_stock_thesis_shadow(shadow_inputs(), replace(self.request(), thesis_venue="NXT"))
        with self.assertRaisesRegex(ValueError, "another thesis theme"):
            evaluate_stock_thesis_shadow(replace(shadow_inputs(), theme_membership=replace(membership(), theme_id="other")), self.request())

    def test_cli_requires_explicit_policy_and_verifies_thesis_identity(self):
        document = json.loads(json.dumps({"schema": STOCK_THESIS_SHADOW_VERSION,
                                         "inputs": asdict(shadow_inputs()), "stock_thesis": asdict(self.request())}))
        document["stock_thesis"].pop("thesis_policy_version")
        with self.assertRaises(TypeError):
            evaluate_document(document)
        with self.assertRaises(ValueError):
            self.request("AUTO")
        document["stock_thesis"]["thesis_policy_version"] = "PRICE_ONLY"
        document["stock_thesis"]["entry_thesis"]["leader_id"] = "000660"
        with self.assertRaisesRegex(ValueError, "immutable content"):
            evaluate_document(document)


class PersistedThesisShadowTests(unittest.TestCase):
    def setUp(self):
        from kiwoom_monitor.central_server.market_theme_shadow_inputs import read_persisted_stock_thesis_shadow_request
        self.read_request = read_persisted_stock_thesis_shadow_request
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "research.sqlite3"
        self.repository = ResearchRepository(self.path)
        self.repository.start_run("persisted-run", {"mode": "offline-fixture"}, {"dataset_id": "fixture"})
        self.leadership = first_evaluation()
        self.thesis = self.save_thesis()

    def save_thesis(self, *, available_at=None, refs=("entry-factor-r1",), leadership=None):
        leadership = leadership or self.leadership
        thesis = build_entry_thesis(run_id="persisted-run", symbol="005930", leadership=leadership,
            factor_refs=refs, hypothesis_refs=("entry-hypothesis-r1",), created_at=leadership.as_of,
            available_at=available_at or leadership.as_of)
        self.repository.append_theme_leadership(leadership)
        self.repository.append_entry_thesis(thesis)
        return thesis

    def load(self, **changes):
        arguments = {"run_id": "persisted-run", "thesis_id": self.thesis.thesis_id,
                     "thesis_policy_version": "PRICE_ONLY", "leadership_venue": "KRX",
                     "decision_time": self.leadership.as_of}
        return self.read_request(ResearchRepository(self.path, read_only=True), **{**arguments, **changes})

    def test_restart_recovers_exact_thesis_and_all_explicit_policies_without_writes(self):
        before = self.path.read_bytes()
        for policy in ("PRICE_ONLY", "IMMEDIATE_EXIT", "REDUCE_ON_RISK", "CONFIRM_THEN_EXIT"):
            request = self.load(thesis_policy_version=policy)
            self.assertEqual(self.thesis, request.entry_thesis)
            expected = evaluate_stock_thesis_shadow(shadow_inputs(), StockThesisShadowRequest(self.thesis, "KRX", policy))
            actual = evaluate_stock_thesis_shadow(shadow_inputs(), request)
            self.assertEqual(expected, actual)
            self.assertFalse(actual["order_authorized"])
            self.assertEqual("not_verified", actual["context"]["scope"]["source_durability"])
        self.assertEqual(before, self.path.read_bytes())
        self.repository.finish_run("persisted-run", "offline-logical-result")
        before = self.path.read_bytes()
        self.assertEqual(self.thesis, self.load().entry_thesis)
        self.assertEqual(before, self.path.read_bytes())

    def test_exact_evidence_uses_one_select_and_no_write_or_commit(self):
        from unittest.mock import patch
        repository = ResearchRepository(self.path, read_only=True)
        statements = []
        connect = repository._connect
        def traced():
            connection = connect()
            connection.set_trace_callback(statements.append)
            return connection
        before = self.path.read_bytes()
        with patch.object(repository, "_connect", side_effect=traced) as reads:
            request = self.read_request(repository, run_id="persisted-run", thesis_id=self.thesis.thesis_id,
                thesis_policy_version="PRICE_ONLY", leadership_venue="KRX", decision_time=self.leadership.as_of)
        self.assertEqual(1, reads.call_count)
        self.assertEqual(1, len(statements))
        self.assertTrue(statements[0].startswith("SELECT "))
        self.assertEqual(self.thesis, request.entry_thesis)
        self.assertEqual(before, self.path.read_bytes())

    def test_missing_cross_run_cross_venue_and_unspecified_policy_are_rejected(self):
        for changes in ({"run_id": "another-run"}, {"thesis_id": "missing"}, {"leadership_venue": "NXT"},
                        {"thesis_policy_version": "AUTO"}, {"decision_time": f"{DAY}T09:00:20"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.load(**changes)

    def test_failed_or_cancelled_run_is_not_automatically_resumed(self):
        for status in ("failed", "cancelled"):
            with closing(self.repository._connect()) as connection, connection:
                connection.execute("UPDATE research_runs SET status=? WHERE run_id='persisted-run'", (status,))
            with self.assertRaisesRegex(ValueError, "usable run"):
                self.load()
            self.assertEqual(status, self.repository.load_run("persisted-run")["status"])

    def test_future_thesis_and_ephemeral_ram_evidence_are_rejected(self):
        future = self.save_thesis(available_at=f"{DAY}T09:00:21+09:00")
        with self.assertRaisesRegex(ValueError, "not available"):
            self.load(thesis_id=future.thesis_id)
        ram = self.save_thesis(refs=("ram:collector:generation:receipt",))
        with self.assertRaisesRegex(ValueError, "ephemeral RAM"):
            self.load(thesis_id=ram.thesis_id)

    def test_corrupted_immutable_document_or_identity_column_is_rejected(self):
        with closing(self.repository._connect()) as connection, connection:
            connection.execute("UPDATE research_entry_theses SET symbol='000660' WHERE thesis_id=?", (self.thesis.thesis_id,))
        with self.assertRaisesRegex(ValueError, "identity columns"):
            self.load()
        document = asdict(self.thesis)
        document["leader_id"] = "000660"
        with closing(self.repository._connect()) as connection, connection:
            connection.execute("UPDATE research_entry_theses SET symbol=?,document_json=? WHERE thesis_id=?",
                               (self.thesis.symbol, json.dumps(document), self.thesis.thesis_id))
        with self.assertRaisesRegex(ValueError, "immutable content"):
            self.load()

    def test_self_consistent_saved_partial_leadership_is_not_promoted_to_complete(self):
        from kiwoom_monitor.application.theme_leadership import EntryThesis, _content_id
        partial = evaluate_theme_leadership(membership(), initial_points(), as_of=self.leadership.as_of,
            venue="KRX", continuity_status="PARTIAL", parameters=PARAMETERS, previous_state=self.leadership.state)
        self.repository.append_theme_leadership(partial)
        body = asdict(self.thesis)
        body.pop("thesis_id")
        body["leadership_revision_id"] = partial.revision_id
        candidate = EntryThesis(thesis_id=_content_id("entry-thesis", body), **body)
        self.repository.append_entry_thesis(candidate)
        with self.assertRaisesRegex(ValueError, "complete leadership evidence"):
            self.load(thesis_id=candidate.thesis_id)

    def test_cli_read_only_selection_matches_existing_typed_evaluator(self):
        import subprocess
        import sys
        before = self.path.read_bytes()
        input_file = self.path.parent / "input.json"
        input_file.write_text(json.dumps({"schema": "market-theme-shadow/v1", "inputs": asdict(shadow_inputs())}))
        script = Path(__file__).resolve().parents[2] / "scripts/preview_market_theme_shadow.py"
        command = [sys.executable, str(script), "--input", str(input_file), "--research-db", str(self.path),
                   "--research-run-id", "persisted-run", "--entry-thesis-id", self.thesis.thesis_id,
                   "--thesis-policy", "REDUCE_ON_RISK"]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=15,
                                env={**os.environ, "PYTHONUTF8": "1"})
        self.assertEqual(0, result.returncode, result.stderr)
        expected = evaluate_stock_thesis_shadow(shadow_inputs(), StockThesisShadowRequest(self.thesis, "KRX", "REDUCE_ON_RISK"))
        self.assertEqual(json.loads(json.dumps(expected)), json.loads(result.stdout))
        self.assertEqual(before, self.path.read_bytes())
        legacy = subprocess.run(command[:4], capture_output=True, text=True, encoding="utf-8", timeout=15,
                                env={**os.environ, "PYTHONUTF8": "1"})
        self.assertEqual(0, legacy.returncode, legacy.stderr)
        self.assertEqual(json.loads(json.dumps(evaluate_market_theme_shadow(shadow_inputs()))), json.loads(legacy.stdout))

    def test_cli_missing_partial_or_ambiguous_selection_fails_without_creating_database(self):
        import subprocess
        import sys
        input_file = self.path.parent / "input.json"
        base = {"schema": "market-theme-shadow/v1", "inputs": asdict(shadow_inputs())}
        input_file.write_text(json.dumps(base))
        script = Path(__file__).resolve().parents[2] / "scripts/preview_market_theme_shadow.py"
        missing = self.path.parent / "not-created.sqlite3"
        all_options = ["--research-db", str(missing), "--research-run-id", "persisted-run",
                       "--entry-thesis-id", self.thesis.thesis_id, "--thesis-policy", "PRICE_ONLY"]
        for options in (all_options[:2], all_options):
            result = subprocess.run([sys.executable, str(script), "--input", str(input_file), *options],
                                    capture_output=True, text=True, encoding="utf-8", timeout=15,
                                    env={**os.environ, "PYTHONUTF8": "1"})
            self.assertEqual(2, result.returncode)
            self.assertFalse(missing.exists())
        base.update(schema="stock-thesis-shadow/v1", stock_thesis=asdict(self.load()))
        input_file.write_text(json.dumps(base))
        all_options[1] = str(self.path)
        result = subprocess.run([sys.executable, str(script), "--input", str(input_file), *all_options],
                                capture_output=True, text=True, encoding="utf-8", timeout=15,
                                env={**os.environ, "PYTHONUTF8": "1"})
        self.assertEqual(2, result.returncode)
        self.assertIn("without an embedded thesis", result.stderr)


class StockThesisSourceCheckTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        from datetime import datetime, timedelta
        from kiwoom_monitor.central_server.database import SQLiteQueryStore
        from kiwoom_monitor.domain.research_contract import THEME_BACKUP_FORMAT, THEME_BACKUP_VERSION
        from kiwoom_monitor.application.theme_leadership import second_trade_points_from_rows, theme_memberships_from_snapshot
        from kiwoom_monitor.central_server.market_theme_shadow_inputs import read_stock_thesis_source_check
        self.check = read_stock_thesis_source_check
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "native.sqlite3"
        self.store = SQLiteQueryStore(self.path)
        self.store.initialize()
        self.addCleanup(self.store.close)
        cut = datetime.fromisoformat(f"{DAY}T09:00:20+09:00")
        document = {"format": THEME_BACKUP_FORMAT, "version": THEME_BACKUP_VERSION,
                    "active_profile": "default", "profiles": [{"name": "default", "stock_themes": [
                        {"code": "005930", "theme": "반도체"}, {"code": "000660", "theme": "반도체"}]}]}
        with patch.object(self.store, "_now_provider", return_value=cut - timedelta(seconds=600)):
            self.store.upsert_documents("theme_metadata", [{"owner": "default", "key": "full", "document": document}])
        [self.snapshot] = self.store.load_theme_snapshots()
        self.assertEqual(cut.timestamp() - 600, self.snapshot["available_at"])
        self.rows = [{"trading_date": DAY, "trade_second": f"09:00:{second:02}", "code": code, "market": "KRX",
                      "open": price, "high": price, "low": price, "close": price,
                      "volume": max(1, value // price), "trade_value_won": value, "trade_count": 1,
                      "available_at": datetime.fromisoformat(f"{DAY}T09:00:{second:02}.100000+09:00").timestamp()}
                     for code, second, price, value in (("005930", 1, 100, 400), ("000660", 1, 100, 500),
                         ("005930", 9, 100, 600), ("000660", 9, 100, 500), ("005930", 12, 101, 900),
                         ("000660", 12, 100, 400), ("005930", 19, 105, 1100), ("000660", 19, 101, 400))]
        self.store.save_second_trade_bars(self.rows)
        leadership = evaluate_theme_leadership(theme_memberships_from_snapshot(self.snapshot)[0],
            second_trade_points_from_rows(self.rows), as_of=cut.isoformat(), venue="KRX",
            continuity_status="COMPLETE", parameters=PARAMETERS)
        research = ResearchRepository(Path(directory.name) / "research.sqlite3")
        research.start_run("source-check", {"mode": "fixture"}, {"dataset_id": "fixture"})
        thesis = build_entry_thesis(run_id="source-check", symbol="005930", leadership=leadership,
            factor_refs=("opaque-factor",), hypothesis_refs=("opaque-hypothesis",),
            created_at=cut.isoformat(), available_at=cut.isoformat())
        research.append_theme_leadership(leadership)
        research.append_entry_thesis(thesis)
        self.saved = research.load_entry_thesis_evidence(thesis.run_id, thesis.thesis_id)

    def report(self, **kwargs):
        return self.check(self.store, self.saved, thesis_policy_version="PRICE_ONLY", leadership_venue="KRX", **kwargs)

    def test_native_exact_hashes_use_two_selects_zero_commits_and_never_approve(self):
        from unittest.mock import patch
        statements = []
        original = self.store._connect
        def connect():
            connection = original()
            connection.set_trace_callback(statements.append)
            return connection
        before = self.path.read_bytes()
        with patch.object(self.store, "_connect", side_effect=connect) as connects:
            report = self.report()
        self.assertEqual(2, connects.call_count)
        self.assertEqual(2, len(statements))
        self.assertTrue(all(sql.startswith("SELECT") for sql in statements))
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual("MATCHED", report["theme"]["status"])
        self.assertEqual((8, 8, []), (report["seconds"]["expected"], report["seconds"]["matched"], report["seconds"]["unmatched_refs"]))
        self.assertEqual(["opaque-factor"], report["unverified_refs"]["factors"])
        self.assertEqual(["opaque-hypothesis"], report["unverified_refs"]["hypotheses"])
        self.assertIn("capture_continuity", report["unverified"])
        self.assertFalse(report["order_authorized"])
        self.assertFalse(report["replay_eligible"])
        self.assertEqual("not_verified", report["source_durability"])

    def test_same_key_correction_and_future_overwrite_cannot_recover_old_hash(self):
        self.store.save_second_trade_bars([{**self.rows[0], "open": 99, "trade_count": 2}])
        report = self.report()
        self.assertEqual(7, report["seconds"]["matched"])
        self.assertEqual(1, len(report["seconds"]["unmatched_refs"]))
        self.store.save_second_trade_bars([{**self.rows[1], "available_at": self.rows[-1]["available_at"] + 10}])
        report = self.report()
        self.assertEqual((6, 7), (report["seconds"]["matched"], report["seconds"]["rows_read"]))
        self.assertIn("second_revision_history", report["unverified"])

    def test_requested_window_is_explicit_and_never_infers_unmatched_cause(self):
        report = self.report(lookback_seconds=5)
        self.assertEqual((8, 2, 6), (report["seconds"]["expected"], report["seconds"]["matched"], len(report["seconds"]["unmatched_refs"])))
        self.assertEqual("bounded_current_native_values", report["scope"])
        self.assertFalse(report["replay_eligible"])

    def test_theme_budget_and_content_corruption_remain_distinct(self):
        from unittest.mock import patch
        from copy import deepcopy
        other = {**self.snapshot, "snapshot_id": "different"}
        with patch.object(self.store, "load_theme_snapshots", return_value=[other]):
            report = self.report(theme_limit=1)
        self.assertEqual("NOT_FOUND_IN_BOUNDED_READ", report["theme"]["status"])
        self.assertTrue(report["theme"]["budget_reached"])
        corrupt = deepcopy(self.snapshot)
        corrupt["document"]["active_profile"] = "corrupt"
        with patch.object(self.store, "load_theme_snapshots", return_value=[corrupt]):
            self.assertEqual("CONTENT_HASH_MISMATCH", self.report()["theme"]["status"])

    def test_invalid_evidence_and_budgets_fail_before_native_reads(self):
        from unittest.mock import patch
        from copy import deepcopy
        with patch.object(self.store, "load_theme_snapshots", side_effect=AssertionError("unexpected source read")):
            for kwargs in ({"lookback_seconds": 0}, {"lookback_seconds": 61}, {"lookback_seconds": True}, {"theme_limit": 1001}):
                with self.assertRaises(ValueError):
                    self.report(**kwargs)
            corrupt = deepcopy(self.saved)
            corrupt["leadership"]["members"][0]["last_price"] += 1
            with self.assertRaisesRegex(ValueError, "immutable content"):
                self.check(self.store, corrupt, thesis_policy_version="PRICE_ONLY", leadership_venue="KRX")

    def test_native_row_overflow_is_not_silently_truncated(self):
        from unittest.mock import patch
        with patch.object(self.store, "load_second_trade_bars", return_value=[self.rows[0]] * 10001):
            with self.assertRaisesRegex(ValueError, "row budget"):
                self.report()


class PersistedThesisRamLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_restarted_binding_reads_thesis_once_then_uses_existing_collector_ram(self):
        from unittest.mock import patch
        from tests.unit.test_candidate_monitor import CollectorRamShadowRuntimeTests
        from kiwoom_monitor.central_server.market_theme_shadow_inputs import read_persisted_stock_thesis_shadow_request
        from kiwoom_monitor.central_server.market_theme_shadow_monitor import MarketThemeShadowMonitor, MarketThemeShadowRuntime
        helper = CollectorRamShadowRuntimeTests()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "research.sqlite3"
            writer = ResearchRepository(path)
            writer.start_run("restart-run", {"mode": "fixture"}, {"dataset_id": "fixture"})
            first = first_evaluation()
            thesis = build_entry_thesis(run_id="restart-run", symbol="005930", leadership=first,
                factor_refs=("stored-factor",), hypothesis_refs=(), created_at=first.as_of, available_at=first.as_of)
            writer.append_theme_leadership(first)
            writer.append_entry_thesis(thesis)
            for _ in range(2):
                reader = ResearchRepository(path, read_only=True)
                with patch.object(reader, "load_entry_thesis_evidence", wraps=reader.load_entry_thesis_evidence) as reads:
                    request = read_persisted_stock_thesis_shadow_request(reader, run_id="restart-run", thesis_id=thesis.thesis_id,
                        thesis_policy_version="PRICE_ONLY", leadership_venue="KRX", decision_time=first.as_of)
                    now, hub, collector, _, _ = helper.fixture(0)
                    monitor = MarketThemeShadowMonitor(theme_id=thesis.theme_id, leadership_venue="KRX", stock_thesis=request)
                    self.assertIsNone(monitor._state)
                    runtime = MarketThemeShadowRuntime(hub, collector, ((monitor, shadow_inputs()),))
                    try:
                        await runtime.start()
                        helper.trades(collector, now)
                        await helper.wait_for(lambda: monitor.status["evaluations"] == 1)
                        self.assertEqual(thesis.thesis_id, monitor.last_result["thesis_ref"])
                        self.assertFalse(monitor.last_result["order_authorized"])
                        self.assertEqual(1, reads.call_count)
                    finally:
                        await runtime.close()


class NativeAutomaticPositionOutcomeTests(unittest.TestCase):
    setUp = NativePositionOutcomeReaderTests.setUp
    read = NativePositionOutcomeReaderTests.read

    def source(self, action='ADD', *, minute=0, flat=False, fill_price=1010, skip_minute=False, cost_override=None, reference_price=1000, terminal_price=None, horizon_minutes=2):
        import hashlib
        from datetime import datetime, timedelta
        from kiwoom_monitor.application.position_target import PositionTargetSizingPolicy, PositionTargetShadowRequest, _json_value
        from kiwoom_monitor.application.research_execution import PaperExecutionEngine, SimulationExecutionConfig, POSITION_TARGET_EXECUTION_VERSION
        from kiwoom_monitor.application.breakout_strategy import StrategyState
        from kiwoom_monitor.application.research_evaluation import ResearchReport, _content_id
        from kiwoom_monitor.application.market_session_schedule import KRX_REGULAR_RESEARCH_PROFILE, research_session_profile_document
        from tests.unit.test_research_execution import _bar, _decision, _strategy
        from tests.unit.test_research_independent_comparisons import record
        value=record('train',minute=minute)
        fixture=PositionTargetShadowTests(); fixture.setUp()
        if cost_override is not None:fixture.cost=cost_override
        execution=SimulationExecutionConfig('next_tradable_bar_open/v1','conservative_with_optimistic_bound/v1',100_000,fixture.cost)
        policy=PositionTargetSizingPolicy('bounded-notional-sizing/v1',5_000 if action=='REDUCE' else 20_000,
            30_000,30,1_000,datetime.fromisoformat('2026-09-14T00:00:00+00:00'),datetime.fromisoformat('2030-01-01T00:00:00+00:00'))
        source_policy='native-bounded-source/v1'
        spec,manifest=value['spec'],value['input_manifest']
        # Keep this two-minute reentry window inside its explicit development/purge boundary.
        fold_end=(datetime.fromisoformat(spec['evaluation_spec']['folds'][0]['end'])+timedelta(minutes=5)).isoformat()
        spec['evaluation_spec']['folds'][0]['end']=fold_end
        manifest['development_partition']['end']=fold_end
        spec['session_profile']=manifest['research_session_profile']=research_session_profile_document(KRX_REGULAR_RESEARCH_PROFILE)
        spec.update(execution_scope='independent_development_validation/v1',policy_version='native-policy/v1',
            parameters={'action_fixture':action,'flat':flat},execution_model=execution.to_dict(),cost_model=fixture.cost.to_dict(),
            position_action_execution={'version':POSITION_TARGET_EXECUTION_VERSION,'source_policy_version':source_policy,
                                       'target_policy':json.loads(json.dumps(asdict(policy),default=_json_value))})
        if reference_price!=1000:spec['parameters']['reference_price']=reference_price
        if terminal_price is not None:spec['parameters']['terminal_price']=terminal_price
        if horizon_minutes!=2:spec['parameters']['horizon_minutes']=horizon_minutes
        spec['data_manifest_hash']=hashlib.sha256(json.dumps(manifest,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        run_id=_content_id('run',{'dataset_id':manifest['dataset_id'],'revision_ids_hash':manifest['revision_ids_hash'],'spec':spec})
        strategy=replace(_strategy(),target_bps=1_000_000) if fill_price>5_000 else _strategy()
        engine=PaperExecutionEngine(run_id,execution,strategy,KRX_REGULAR_RESEARCH_PROFILE)
        def shifted(at): return (datetime.fromisoformat(at)+timedelta(minutes=minute)).isoformat()
        def bar(index,price=None):
            if price is None:price=reference_price
            raw=_bar(index,open_=price,low=price-1,high=price+1)
            return replace(raw,observation_key=shifted(raw.observation_key.replace('09-12','09-14')),
                bar_start=shifted(raw.bar_start.replace('09-12','09-14')),bar_end=shifted(raw.bar_end.replace('09-12','09-14')),
                available_at=shifted(raw.available_at.replace('09-12','09-14')),session_profile=KRX_REGULAR_RESEARCH_PROFILE,
                research_session='2026-09-14:KRX_REGULAR',market_phase='CONTINUOUS')
        decision=replace(_decision(shifted('2026-09-14T00:03:02+00:00')),run_id=run_id)
        events=list(engine.process_decision(decision,StrategyState(status='candidate',symbol='005930')))
        events+=engine.process_bar(bar(4))
        initial_bar=bar(4)
        if flat or action=='REENTER':
            exit_=replace(decision,decision_id='prior-exit',proposal='EXIT',final_action='EXIT',
                decided_at=shifted('2026-09-14T00:05:02+00:00'))
            events+=engine.process_decision(exit_,engine.strategy_state,source_bar=bar(4))
            events+=engine.process_bar(bar(5)); events+=engine.process_bar(bar(6))
            initial_bar=bar(6)
            events.append(engine.observe_position_value(initial_bar))
        start=events[-1]
        request=None
        if action!='HOLD':
            at=datetime.fromisoformat(initial_bar.available_at)
            request=PositionTargetShadowRequest('research-single-position','mock','KRX','005930',action,None,
                initial_bar.close,at,at,source_policy,'source-target','native-account','native-orders',30,True,True,fixture.cost,policy)
            events+=engine.process_position_target(request,source_bar=initial_bar)
        begin=6 if flat or action=='REENTER' else 4
        for index in range(begin+1,begin+horizon_minutes+1):
            price=terminal_price if terminal_price is not None and index==begin+horizon_minutes else fill_price
            actual=engine.process_bar(bar(index,price))
            if not (skip_minute and index==begin+1):
                events+=actual
                if not any(e.event_type=='MARK' for e in actual): events.append(engine.observe_position_value(bar(index,price)))
        end=events[-1]
        self.writer.start_run(run_id,spec,manifest,claim_independent=True)
        self.writer.append_execution_events(tuple(events))
        report=ResearchReport('report-'+run_id,run_id,'ELIGIBLE',(),spec['evaluation_spec'],{},spec['cost_model'],(),(value['report']['fold_reports'][0],))
        self.writer.save_research_report(report); self.writer.finish_run(run_id,'logical-'+run_id)
        selection={'run_id':run_id,'start_event_id':start.event_id,'end_event_id':end.event_id,'action':action,'case_ref':'case-'+str(minute)}
        return selection,events,request,engine

    def test_native_add_and_reduce_cash_shares_and_fees_are_hand_checkable(self):
        add,add_events,_,engine=self.source('ADD')
        reduce,_,_,_=self.source('REDUCE')
        hold,_,_,_=self.source('HOLD')
        outcomes,receipt=self.read([add,reduce,hold],horizon_seconds=120)
        self.assertEqual((3,[]),(receipt['accepted_count'],receipt['excluded']))
        rows={o.action:o for o in outcomes}
        self.assertEqual((80,10,20),(rows['ADD'].gross_change_won,rows['ADD'].fee_tax_won,rows['ADD'].target_quantity))
        self.assertEqual((90,15,5),(rows['REDUCE'].gross_change_won,rows['REDUCE'].fee_tax_won,rows['REDUCE'].target_quantity))
        self.assertEqual((100,0,10),(rows['HOLD'].gross_change_won,rows['HOLD'].fee_tax_won,rows['HOLD'].target_quantity))
        self.assertEqual(rows['ADD'].initial_state_ref,rows['HOLD'].initial_state_ref)
        self.assertEqual(20,engine.strategy_state.position_quantity)
        self.assertEqual(20130,engine.portfolio.position.entry_notional)
        self.assertFalse(receipt['order_authorized'])

    def test_reenter_has_prior_close_and_actual_flat_hold_baseline(self):
        enter,_,_,_=self.source('REENTER')
        hold,_,_,_=self.source('HOLD',flat=True)
        outcomes,receipt=self.read([enter,hold],horizon_seconds=120)
        self.assertEqual(2,receipt['accepted_count'])
        rows={o.action:o for o in outcomes}
        self.assertEqual((0,20,-40,20),(rows['REENTER'].initial_quantity,rows['REENTER'].target_quantity,
            rows['REENTER'].gross_change_won,rows['REENTER'].fee_tax_won))
        self.assertEqual(0,rows['HOLD'].gross_change_won)
        self.assertEqual(rows['REENTER'].initial_state_ref,rows['HOLD'].initial_state_ref)

    def test_native_policy_samples_compare_without_relabeling_and_shared_reads(self):
        from datetime import timedelta
        from kiwoom_monitor.application.position_target import evaluate_position_action_comparison_document
        from kiwoom_monitor.application.research_evaluation import read_position_action_outcomes
        from unittest.mock import patch
        selections=[]
        for minute in (0,20):
            for action in ('HOLD','ADD','REDUCE'): selections.append(self.source(action,minute=minute)[0])
        reader=ResearchRepository(self.path,read_only=True); sql=[]; original=reader._connect
        def connect():
            conn=original(); conn.set_trace_callback(sql.append); return conn
        before=self.path.read_bytes()
        with patch.object(reader,'_connect',side_effect=connect):
            outcomes,receipt=read_position_action_outcomes(reader.load_position_action_evidence,selections,
                decision_time=self.cut,horizon_seconds=120,population_ref='cohort-r1',environment='mock',symbol='005930')
        self.assertEqual(24,sum(s.lstrip().upper().startswith('SELECT') for s in sql))
        self.assertFalse(any(s=='COMMIT' for s in sql)); self.assertEqual(before,self.path.read_bytes())
        fixture=PositionActionComparisonTests(); fixture.setUp()
        by_action={o.action:o for o in outcomes}
        policies={}
        for action in ('ADD','REDUCE'):
            _,_,request,_=self.source(action,minute=40)
            policies[action]=request.target_policy
        fixture.account=replace(fixture.account,as_of=self.cut,available_cash_won=89_970)
        fixture.parameters=replace(fixture.parameters,horizon_seconds=120,execution_model_ref=outcomes[0].execution_model_ref)
        base=replace(fixture.requests[0],decision_time=self.cut,reference_price_available_at=self.cut,
                     policy_version=by_action['HOLD'].policy_version)
        fixture.requests=(base,*(replace(base,action=a,target_quantity=None,
            policy_version='native-bounded-source/v1',target_policy=policies[a]) for a in ('ADD','REDUCE')))
        result=fixture.run_comparison(outcomes=outcomes)
        self.assertEqual('COMPARABLE',result['status'])
        self.assertEqual({'HOLD','ADD','REDUCE'},{r['action'] for r in result['actions'] if r['ev_status']=='EMPIRICAL_ESTIMATE'})
        self.assertFalse(result['order_authorized'])
        self.assertEqual(result,evaluate_position_action_comparison_document(fixture.document(outcomes=outcomes)))

        # Consume the same native values once; action selection adds no reader or writer.
        policy_fixture = PositionActionSelectionTests(); policy_fixture.setUp()
        policy = policy_fixture.policy(minimum_net_advantage_won=0, uncertainty_multiplier=0,
            account_input_ref=base.account_input_ref, orders_input_ref=base.orders_input_ref,
            available_at=self.cut, valid_until=self.cut+timedelta(seconds=20))
        fixture.parameters = replace(fixture.parameters, selection_policy=policy)
        sql_count = len(sql)
        with patch('sqlite3.connect',side_effect=AssertionError('selection reread native DB')):
            selected = evaluate_position_action_comparison_document(fixture.document(outcomes=outcomes))
        self.assertEqual(sql_count,len(sql))
        self.assertEqual('HOLD',selected['action_selection']['action'])
        self.assertEqual((-30.0,-25.0),tuple(selected['action_selection']['scores'][a]['net_advantage_won']
            for a in ('ADD','REDUCE')))
        self.assertFalse(selected['action_selection']['order_authorized'])

    def test_missing_prior_close_gap_and_risk_fill_are_not_complete_target_outcomes(self):
        from kiwoom_monitor.application.research_evaluation import position_action_outcome_from_events,PositionOutcomeUnavailable
        selection,events,_,_=self.source('REENTER')
        context,raw=ResearchRepository(self.path,read_only=True).load_position_action_evidence(selection['run_id'],
            (selection['end_event_id'],),decision_time=self.cut)
        def check(values,action='REENTER'):
            return position_action_outcome_from_events(context,values,start_event_id=selection['start_event_id'],
                end_event_id=selection['end_event_id'],action=action,case_ref='case',population_ref='cohort-r1',
                environment='mock',horizon_seconds=120,symbol='005930')
        with self.assertRaisesRegex(PositionOutcomeUnavailable,'initial_state'):
            check(tuple(e for e in raw if not (e['event_type'] in {'ORDER_SUBMITTED','FILL'} and e['side']=='SELL')))
        with self.assertRaisesRegex(PositionOutcomeUnavailable,'valuation_gap'):
            check(tuple(e for e in raw if not (e['event_type']=='MARK' and e['event_id'] not in {selection['start_event_id'],selection['end_event_id']})))
        mixed=tuple({**e,'event_type':'RISK_FILL'} if e['event_type']=='FILL' and e['side']=='BUY' and e['position_quantity_after']==20 else e for e in raw)
        with self.assertRaisesRegex(PositionOutcomeUnavailable,'mixed_or_censored'): check(mixed)

    def test_tampered_policy_intent_cost_or_cash_is_rejected(self):
        from kiwoom_monitor.application.research_evaluation import position_action_outcome_from_events
        selection,_,_,_=self.source('ADD')
        context,events=ResearchRepository(self.path,read_only=True).load_position_action_evidence(selection['run_id'],
            (selection['end_event_id'],),decision_time=self.cut)
        def check(values):
            return position_action_outcome_from_events(context,values,start_event_id=selection['start_event_id'],
                end_event_id=selection['end_event_id'],action='ADD',case_ref='case',population_ref='cohort-r1',
                environment='mock',horizon_seconds=120,symbol='005930')
        for kind,key,value in (('ORDER_SUBMITTED','decision_id','fake'),('FILL','commission_won',100),('MARK','cash_after_won',1)):
            changed=list(events)
            index=next(i for i,e in enumerate(changed) if i>0 and e['event_type']==kind
                       and (kind!='FILL' or e['position_quantity_after']==20)
                       and (kind!='ORDER_SUBMITTED' or e['reason'].startswith('{')))
            changed[index]={**changed[index],key:value}
            with self.subTest(kind=kind),self.assertRaises(ValueError): check(tuple(changed))
        wrong=dict(context); wrong['position_source_policy']='different'
        with self.assertRaisesRegex(ValueError,'policy/cut'):
            position_action_outcome_from_events(wrong,events,start_event_id=selection['start_event_id'],end_event_id=selection['end_event_id'],
                action='ADD',case_ref='case',population_ref='cohort-r1',environment='mock',horizon_seconds=120,symbol='005930')

    def test_next_open_cash_failure_keeps_existing_holding_and_is_not_measured_add(self):
        selection,_,_,engine=self.source('ADD',fill_price=10_000)
        self.assertEqual(10,engine.portfolio.position.quantity)
        self.assertEqual(10,engine.strategy_state.position_quantity)
        outcomes,receipt=self.read([selection],horizon_seconds=120)
        self.assertEqual((),outcomes)
        self.assertEqual('native_window_mixed_or_censored_execution',receipt['excluded'][0]['reason'])

    def test_pending_duplicate_and_wrong_source_never_change_paper_portfolio(self):
        selection,events,request,engine=self.source('ADD')
        from tests.unit.test_research_execution import _bar
        before=engine.portfolio
        with self.assertRaises(ValueError): engine.process_position_target(request,source_bar=_bar(4))
        self.assertEqual(before,engine.portfolio)
        from kiwoom_monitor.application.research_execution import PendingOrder
        engine.portfolio=replace(before,pending_order=PendingOrder('pending','decision','005930','BUY',1,
            request.decision_time.isoformat(),request.decision_time.isoformat(),1000))
        # A valid native bar still cannot replace an already owned order.
        bar=engine._last_processed_bar
        from datetime import datetime
        request=replace(request,decision_time=datetime.fromisoformat(bar.available_at),
            reference_price_available_at=datetime.fromisoformat(bar.available_at),reference_price_won=bar.close)
        pending_before=engine.portfolio
        with self.assertRaisesRegex(ValueError,'pending order'): engine.process_position_target(request,source_bar=bar)
        self.assertEqual(pending_before,engine.portfolio)

    def test_protective_risk_preempts_target_without_phantom_fill_or_silent_pending_drop(self):
        from kiwoom_monitor.application.research_execution import PaperExecutionEngine,PendingOrder,POSITION_TARGET_EXECUTION_VERSION
        from tests.unit.test_research_execution import _strategy
        _,_,request,original=self.source('ADD')
        engine=PaperExecutionEngine('risk-preempt',original.execution,_strategy(),original.session_profile)
        pending=PendingOrder('pending-add','target-decision','005930','BUY',1,
            '2026-09-14T00:08:02+00:00','2026-09-14T00:08:02+00:00',1010,
            engine.session_profile,'2026-09-14:KRX_REGULAR',1000,POSITION_TARGET_EXECUTION_VERSION)
        engine.portfolio=replace(original.portfolio,reserved_cash_won=1010,pending_order=pending)
        engine.strategy_state=original.strategy_state
        source=original._last_processed_bar
        bar=replace(source,low=800,bar_start='2026-09-14T00:08:00+00:00',
            bar_end='2026-09-14T00:09:00+00:00',available_at='2026-09-14T00:09:02+00:00')
        events=engine.process_bar(bar)
        self.assertEqual(['RISK_FILL','ORDER_CANCELLED'],[e.event_type for e in events])
        self.assertEqual('risk_exit_preempted_target',events[-1].reason)
        self.assertIsNone(engine.portfolio.pending_order); self.assertIsNone(engine.portfolio.position)
        self.assertEqual(0,engine.portfolio.reserved_cash_won)

    def test_existing_research_runner_writes_locked_target_plan_and_native_reader_accepts_it(self):
        from datetime import datetime, timedelta
        from tests.unit.test_research_development_partitions import DevelopmentPartitionTests, _execution, _strategy, PROFILE
        from kiwoom_monitor.application.research_splits import ResearchEvaluationSpec,ResearchFoldSpec
        from kiwoom_monitor.application.position_target import PositionTargetSizingPolicy,PositionTargetShadowRequest
        from scripts.run_research import research_run_identity,execute_research
        helper=DevelopmentPartitionTests(); helper.setUp(); self.addCleanup(helper.doCleanups)
        helper.evaluation=ResearchEvaluationSpec('chronological_holdout/v1',
            (ResearchFoldSpec('train','TRAIN',helper.at(2),helper.at(20)),),120,120,0,1,1)
        dataset=helper.prepare(); evaluation=helper.partition.evaluation_for(helper.evaluation)
        execution=replace(_execution(),cost_model=replace(_execution().cost_model,valid_to='2030-01-01T00:00:00+00:00'))
        strategy=replace(_strategy(),max_hold_minutes=6)
        policy=PositionTargetSizingPolicy('bounded-notional-sizing/v1',20_000,30_000,30,1_000,
            helper.base,datetime.fromisoformat('2030-01-01T00:00:00+00:00'))
        at=datetime.fromisoformat(helper.at(5))+timedelta(seconds=2)
        request=PositionTargetShadowRequest('research-single-position','mock','KRX','005930','ADD',None,
            1020,at,at,'runner-sizing/v1','supplied-target','native-account','native-orders',30,True,True,execution.cost_model,policy)
        scope='independent_development_validation/v1'
        run_id,spec=research_run_identity(dataset,strategy,execution,evaluation,session_profile=PROFILE,
            execution_scope=scope,position_target_plan=(request,))
        self.writer.start_run(run_id,spec,dataset.manifest,claim_independent=True)
        result=execute_research(dataset,self.writer,helper.root/'runs',strategy,execution,evaluation,
            session_profile=PROFILE,execution_scope=scope,position_target_plan=(request,))
        self.assertEqual('ELIGIBLE',result.report_status)
        events=self.writer.load_execution_events(run_id)
        submitted=next(e for e in events if e['event_type']=='ORDER_SUBMITTED' and e['reason'].startswith('{'))
        self.assertEqual('ADD',json.loads(submitted['reason'])['request']['action'])
        start=next(e for e in events if e['event_type']=='MARK' and datetime.fromisoformat(e['occurred_at'])==datetime.fromisoformat(helper.at(5)))
        end=next(e for e in events if e['event_type']=='MARK' and datetime.fromisoformat(e['occurred_at'])==datetime.fromisoformat(helper.at(7)))
        selection={'run_id':run_id,'start_event_id':start['event_id'],'end_event_id':end['event_id'],'action':'ADD','case_ref':'runner-case'}
        outcomes,receipt=self.read([selection],horizon_seconds=120)
        self.assertEqual((1,[]),(receipt['accepted_count'],receipt['excluded']))
        self.assertEqual((10,19),(outcomes[0].initial_quantity,outcomes[0].target_quantity))
        with self.assertRaisesRegex(ValueError,'independent KRX development'):
            research_run_identity(dataset,strategy,execution,evaluation,session_profile=PROFILE,
                execution_scope=scope,position_target_plan=())
        with self.assertRaises(ValueError):
            research_run_identity(dataset,strategy,execution,evaluation,session_profile=PROFILE,position_target_plan=(request,))
        with self.assertRaisesRegex(ValueError,'unique source cuts'):
            research_run_identity(dataset,strategy,execution,evaluation,session_profile=PROFILE,
                execution_scope=scope,position_target_plan=(request,request))
        with self.assertRaisesRegex(ValueError,'256 KiB'):
            research_run_identity(dataset,strategy,execution,evaluation,session_profile=PROFILE,
                execution_scope=scope,position_target_plan=(replace(request,source_decision_ref='x'*(256*1024)),))

    def test_strategy_exit_cancels_owned_target_before_issuing_sell(self):
        from tests.unit.test_research_execution import _decision
        from kiwoom_monitor.application.research_execution import PendingOrder,POSITION_TARGET_EXECUTION_VERSION
        _,_,_,engine=self.source('ADD')
        at=engine._last_processed_bar.available_at
        engine.portfolio=replace(engine.portfolio,reserved_cash_won=1000,pending_order=PendingOrder(
            'owned-target','owned-decision','005930','BUY',1,at,at,1000,engine.session_profile,
            '2026-09-14:KRX_REGULAR',1000,POSITION_TARGET_EXECUTION_VERSION))
        decision=replace(_decision(at),run_id=engine.run_id,proposal='EXIT',final_action='EXIT')
        events=engine.process_decision(decision,engine.strategy_state,source_bar=engine._last_processed_bar)
        self.assertEqual(['ORDER_CANCELLED','ORDER_SUBMITTED'],[e.event_type for e in events])
        self.assertEqual('SELL',events[-1].side)
        self.assertEqual(engine.portfolio.position.quantity,events[-1].quantity)
        self.assertEqual('strategy_exit_preempted_target',events[0].reason)

    def test_native_reenter_and_flat_hold_are_comparable_only_with_their_own_initial_state(self):
        selections=[]
        for minute in (0,20):
            selections.extend((self.source('REENTER',minute=minute)[0],self.source('HOLD',minute=minute,flat=True)[0]))
        outcomes,receipt=self.read(selections,horizon_seconds=120)
        self.assertEqual(4,receipt['accepted_count'])
        self.assertEqual('native-bounded-source/v1',receipt['sources'][0]['position_action_execution']['source_policy_version'])
        fixture=PositionActionComparisonTests(); fixture.setUp()
        fixture.account=replace(fixture.account,positions={},available_cash_won=99_942,as_of=self.cut)
        fixture.parameters=replace(fixture.parameters,horizon_seconds=120,execution_model_ref=outcomes[0].execution_model_ref)
        hold=next(o for o in outcomes if o.action=='HOLD')
        policy=self.source('REENTER',minute=40)[2].target_policy
        base=replace(fixture.requests[0],target_quantity=0,policy_version=hold.policy_version,
                     decision_time=self.cut,reference_price_available_at=self.cut)
        fixture.requests=(base,replace(base,action='REENTER',target_quantity=None,
            policy_version='native-bounded-source/v1',target_policy=policy))
        result=fixture.run_comparison(outcomes=outcomes)
        self.assertEqual('COMPARABLE',result['status'])
        self.assertFalse(result['order_authorized'])
        wrong=replace(fixture.account,positions={'005930':1})
        self.assertEqual('EV_UNAVAILABLE',fixture.run_comparison(outcomes=outcomes,account=wrong)['status'])

    def test_added_entry_basis_and_commission_survive_partial_then_final_sell_exactly(self):
        from datetime import datetime, timedelta
        from tests.unit.test_research_execution import _decision
        _,_,request,engine=self.source('ADD')
        source=engine._last_processed_bar
        at=datetime.fromisoformat(source.available_at)
        reduce=replace(request,action='REDUCE',reference_price_won=source.close,
            reference_price_available_at=at,decision_time=at,
            target_policy=replace(request.target_policy,target_notional_won=5_000))
        engine.process_position_target(reduce,source_bar=source)
        next_bar=replace(source,bar_start=(datetime.fromisoformat(source.bar_start)+timedelta(minutes=2)).isoformat(),
            bar_end=(datetime.fromisoformat(source.bar_end)+timedelta(minutes=2)).isoformat(),
            available_at=(datetime.fromisoformat(source.available_at)+timedelta(minutes=2)).isoformat())
        events=engine.process_bar(next_bar)
        partial=next(e for e in events if e.event_type=='FILL')
        self.assertEqual((4,4026,4),(engine.portfolio.position.quantity,engine.portfolio.position.entry_notional,
                                   engine.portfolio.position.entry_commission))
        exit_=replace(_decision(next_bar.available_at),run_id=engine.run_id,proposal='EXIT',final_action='EXIT')
        engine.process_decision(exit_,engine.strategy_state,source_bar=next_bar)
        final_bar=replace(next_bar,bar_start=(datetime.fromisoformat(next_bar.bar_start)+timedelta(minutes=2)).isoformat(),
            bar_end=(datetime.fromisoformat(next_bar.bar_end)+timedelta(minutes=2)).isoformat(),
            available_at=(datetime.fromisoformat(next_bar.available_at)+timedelta(minutes=2)).isoformat())
        final=next(e for e in engine.process_bar(final_bar) if e.event_type=='FILL')
        self.assertIsNone(engine.portfolio.position)
        self.assertEqual(partial.realized_pnl_won+final.realized_pnl_won,
                         engine.portfolio.cash_won-engine.execution.initial_cash_won)

    def test_sub_millisecond_future_endpoint_is_not_accepted_by_sql_date_rounding(self):
        from datetime import datetime,timedelta
        selection,events,_,_=self.source('ADD')
        endpoint=next(e for e in events if e.event_id==selection['end_event_id'])
        cut=datetime.fromisoformat(endpoint.received_at)
        document=endpoint.to_dict()
        document['received_at']=(cut+timedelta(microseconds=1)).isoformat()
        from kiwoom_monitor.application.research_evaluation import _content_id
        changed_id=_content_id('execution',{k:v for k,v in document.items() if k!='event_id'})
        document['event_id']=changed_id
        with self.connection() as conn,conn:
            conn.execute('UPDATE research_runs SET finished_at=? WHERE run_id=?',
                ((cut-timedelta(seconds=1)).isoformat(),selection['run_id']))
            conn.execute('UPDATE research_execution_events SET event_id=?,received_at=?,document_json=? WHERE event_id=?',
                (changed_id,document['received_at'],json.dumps(document),selection['end_event_id']))
        selection['end_event_id']=changed_id
        outcomes,receipt=self.read([selection],decision_time=cut,horizon_seconds=120)
        self.assertEqual((),outcomes)
        self.assertEqual('native_event_outside_declared_window_or_cut',receipt['excluded'][0]['reason'])


class AutomaticPositionSizingTests(unittest.TestCase):
    def setUp(self):
        from datetime import timedelta
        from kiwoom_monitor.application.position_target import PositionTargetSizingPolicy
        self.base = PositionTargetShadowTests(); self.base.setUp()
        self.policy = PositionTargetSizingPolicy('bounded-notional-sizing/v1', 20_000, 30_000, 30,
            1_000, self.base.now - timedelta(seconds=1), self.base.now + timedelta(seconds=20))
        self.request = replace(self.base.request, target_quantity=None, target_policy=self.policy)

    def evaluate(self, request=None, account=None, orders=(), **kwargs):
        return self.base.evaluate(request or self.request, account or self.base.account, orders, **kwargs)

    def test_legacy_v1_ids_and_optional_null_remain_unchanged(self):
        from kiwoom_monitor.application.position_target import evaluate_position_target_document
        target = evaluate_position_target_document(self.base.document())
        self.assertEqual('position-target-shadow:f1b5227c701550d267e0779166ee9ca3524b97e0296fe44042fe90eb22287678', target['decision_id'])
        self.assertNotIn('target_sizing', target)
        document = self.base.document(); document['request'].pop('target_policy')
        self.assertEqual(target, evaluate_position_target_document(document))
        other = PositionActionComparisonTests(); other.setUp()
        self.assertEqual('position-action-comparison-shadow:b2216a37ef08795210baf5a348f000169436c54cabee70596cd0f1883f566965',
                         other.run_comparison()['decision_id'])

    def test_partial_buy_cash_buffer_and_position_caps_are_hand_checkable(self):
        request = replace(self.request, target_policy=replace(self.policy, target_notional_won=100_000,
            max_position_notional_won=22_000, max_position_quantity=21))
        account = replace(self.base.account, available_cash_won=8_000, reserved_open_buy_won=5_000)
        result = self.evaluate(request, account, (self.base.record(),))
        self.assertEqual((10, 5, 15, 16, 1, 1002), tuple(result[k] for k in (
            'actual_quantity', 'pending_buy_quantity', 'projected_quantity', 'target_quantity',
            'incremental_quantity', 'funded_buy_cash_won')))
        self.assertEqual(['position_limit', 'cash_after_reservations_and_buffer'], result['target_sizing']['limits'])
        self.assertEqual(100, result['target_sizing']['desired_quantity'])
        self.assertEqual('CALCULATED', result['quality']['status'])
        self.assertFalse(result['order_authorized'])

    def test_remaining_buy_is_not_repeated_after_fill_or_other_symbol_reservation(self):
        from kiwoom_monitor.domain.order_contract import OrderState
        first = self.evaluate(account=replace(self.base.account, reserved_open_buy_won=5_000), orders=(self.base.record(),))
        self.assertEqual((20, 5), (first['target_quantity'], first['incremental_quantity']))
        complete = replace(self.base.record(), state=OrderState.FILLED, filled_quantity=10,
            detailed_filled_quantity=10, broker_reported_filled_quantity=10)
        after = self.evaluate(account=replace(self.base.account, positions={'005930':20}), orders=(complete,))
        self.assertIsNone(after['incremental_quantity'])
        self.assertIn('action_target_mismatch', after['quality']['reasons'])
        other = self.base.record(quantity=15, filled=0, symbol='000660')
        result = self.evaluate(account=replace(self.base.account, reserved_open_buy_won=15_000), orders=(other,))
        self.assertEqual((0, 13, 3), (result['pending_buy_quantity'], result['target_quantity'], result['incremental_quantity']))

    def test_reduce_uses_remaining_sell_and_never_promotes_zero_to_exit(self):
        request = replace(self.request, action='REDUCE', target_policy=replace(self.policy, target_notional_won=5_000))
        result = self.evaluate(request, orders=(self.base.record(side='SELL', quantity=5, filled=3),))
        self.assertEqual((8, 5, 3), (result['projected_quantity'], result['target_quantity'], result['incremental_quantity']))
        for action, notional, account in (
            ('REDUCE',0,self.base.account), ('REDUCE',20_000,self.base.account),
            ('ADD',5_000,self.base.account), ('REENTER',20_000,self.base.account)):
            with self.subTest(action=action, notional=notional):
                result = self.evaluate(replace(request, action=action,
                    target_policy=replace(self.policy, target_notional_won=notional)), account)
                self.assertEqual('BLOCKED', result['quality']['status'])
                self.assertIsNone(result['incremental_quantity'])

    def test_reenter_has_no_phantom_position_and_cash_rounding_is_conservative(self):
        account = replace(self.base.account, positions={}, available_cash_won=5_001)
        result = self.evaluate(replace(self.request, action='REENTER'), account)
        self.assertEqual((0,3,3,3006), tuple(result[k] for k in (
            'actual_quantity','target_quantity','incremental_quantity','funded_buy_cash_won')))
        self.assertLessEqual(result['funded_buy_cash_won'], 4_001)
        blocked = self.evaluate(account=replace(self.base.account, available_cash_won=1_000))
        self.assertIsNone(blocked['incremental_quantity'])
        missing = self.evaluate(replace(self.request, cost_model=None))
        self.assertIsNone(missing['target_quantity'])
        self.assertIn('buy_cost_model_unavailable', missing['quality']['reasons'])

    def test_incomplete_unknown_cancel_pending_and_conflicting_orders_block(self):
        for state in ('SUBMISSION_UNKNOWN','CANCEL_PENDING'):
            result = self.evaluate(orders=(self.base.record(state=state),))
            self.assertIsNone(result['target_quantity'])
            self.assertIsNone(result['incremental_quantity'])
        for request in (replace(self.request, reconciliation_complete=False), replace(self.request, open_orders_complete=False)):
            self.assertIsNone(self.evaluate(request)['target_quantity'])
        result = self.evaluate(orders=(self.base.record(side='SELL'),))
        self.assertIn('existing_order_conflicts_with_target', result['quality']['reasons'])
        self.assertIsNone(result['incremental_quantity'])

    def test_policy_times_and_malformed_or_ambiguous_contract_are_rejected(self):
        from datetime import timedelta
        from kiwoom_monitor.application.position_target import evaluate_position_target_document
        for policy, reason in (
            (replace(self.policy, available_at=self.base.now+timedelta(seconds=1)), 'sizing_policy_from_future'),
            (replace(self.policy, valid_until=self.base.now), 'sizing_policy_expired')):
            result = self.evaluate(replace(self.request,target_policy=policy))
            self.assertIsNone(result['target_quantity']); self.assertIn(reason,result['quality']['reasons'])
        for changes in ({'target_notional_won':True}, {'max_position_quantity':-1}, {'cash_buffer_won':1.5},
                        {'version':'unknown'}, {'available_at':self.base.now.replace(tzinfo=None)}):
            with self.subTest(changes=changes), self.assertRaises(ValueError): replace(self.policy,**changes)
        for changes in ({'target_quantity':20}, {'action':'EXIT'}, {'action':'HOLD'}, {'action':'WAIT'}):
            with self.assertRaises(ValueError): replace(self.request,**changes)
        document = self.base.document(request=self.request)
        document['request']['target_policy']['unknown'] = 1
        with self.assertRaises(TypeError): evaluate_position_target_document(document)

    def test_typed_json_cli_and_policy_content_binding_are_identical_without_io(self):
        from unittest.mock import patch
        from kiwoom_monitor.application.position_target import evaluate_position_target_document
        before = asdict(self.request)
        with patch('sqlite3.connect', side_effect=AssertionError('sizing opened DB')):
            result = self.evaluate()
            document = self.base.document(request=self.request)
            self.assertEqual(result, evaluate_position_target_document(document))
            self.assertEqual(result, evaluate_document(document))
        self.assertEqual(before, asdict(self.request))
        changed = self.evaluate(replace(self.request,target_policy=replace(self.policy,cash_buffer_won=2_000)))
        self.assertEqual(result['target_quantity'],changed['target_quantity'])
        self.assertNotEqual(result['target_policy_version'],changed['target_policy_version'])
        self.assertNotEqual(result['decision_id'],changed['decision_id'])

    def test_native_broker_confirmed_remaining_is_used_instead_of_original_quantity(self):
        from kiwoom_monitor.application.position_target import PositionBrokerOrderExposure
        from kiwoom_monitor.domain.order_contract import OrderState, OrderSide
        exposure = PositionBrokerOrderExposure('manual',self.base.account.account_ref,'005930',OrderSide.BUY,
            100,0,5,OrderState.ACCEPTED,1000,self.base.now,None)
        result = self.evaluate(account=replace(self.base.account,reserved_open_buy_won=5_000),
            broker_open_orders=(exposure,))
        self.assertEqual((5,15,20,5),tuple(result[k] for k in (
            'pending_buy_quantity','projected_quantity','target_quantity','incremental_quantity')))
        self.assertEqual('broker_remaining_quantity/v1',result['scope']['pending_quantity_basis'])

    def test_comparison_does_not_relabel_old_policy_samples_and_accepts_only_own_binding(self):
        fixture = PositionActionComparisonTests(); fixture.setUp()
        policy = replace(self.policy, target_notional_won=5_000)
        requests = (fixture.requests[0],replace(fixture.requests[1],target_quantity=None,target_policy=policy))
        first = fixture.run_comparison(requests=requests)
        self.assertEqual('EV_UNAVAILABLE',fixture.row(first)['ev_status'])
        self.assertEqual(5,first['targets']['REDUCE']['target_quantity'])
        binding = first['targets']['REDUCE']['target_policy_version']
        own = tuple(replace(o,policy_version=binding) if o.action=='REDUCE' else o for o in fixture.outcomes())
        second = fixture.run_comparison(requests=requests,outcomes=own)
        self.assertEqual('EMPIRICAL_ESTIMATE',fixture.row(second)['ev_status'])
        self.assertFalse(second['order_authorized'])
        changed = (requests[0],replace(requests[1],target_policy=replace(policy,cash_buffer_won=2_000)))
        self.assertEqual('EV_UNAVAILABLE',fixture.row(fixture.run_comparison(requests=changed,outcomes=own))['ev_status'])


class NativeUnfilledOutcomeTests(unittest.TestCase):
    def setUp(self):
        from tests.unit.test_research_development_partitions import DevelopmentPartitionTests
        from kiwoom_monitor.application.research_splits import ResearchEvaluationSpec,ResearchFoldSpec
        from datetime import datetime,timedelta,timezone
        self.fixture=DevelopmentPartitionTests(); self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.fixture.evaluation=ResearchEvaluationSpec('chronological_holdout/v1',
            (ResearchFoldSpec('train','TRAIN',self.fixture.at(2),self.fixture.at(20)),),120,120,0,1,1)
        # Complete each BUY cycle later in the same development fold. The reader
        # still selects only the earlier pending window and preserves eligibility.
        for row in self.fixture.dataset.observations:
            if row['kind']=='minute_bar':
                minute=datetime.fromisoformat(row['payload']['bar_start']).minute
                if minute>=12:
                    price=800 if minute==12 else 600
                    row['payload'].update(open=price,high=price,low=price,close=price)
        self.dataset=self.fixture.prepare()
        self.evaluation=self.fixture.partition.evaluation_for(self.fixture.evaluation)
        self.path=self.fixture.root/'unfilled.sqlite3'
        self.writer=ResearchRepository(self.path)
        self.cut=datetime.now(timezone.utc)+timedelta(minutes=5)

    def source(self, action, *, case='case-a', minute=5, sell=False, execution_override=None):
        from tests.unit.test_research_development_partitions import _strategy,_execution,PROFILE
        from kiwoom_monitor.application.research_execution import UnfilledResearchAction
        from scripts.run_research import execute_research,research_run_identity
        from datetime import datetime,timedelta
        execution=replace(_execution(),cost_model=replace(_execution().cost_model,commission_bps=10,sell_tax_bps=20,
            valid_to='2030-01-01T00:00:00+00:00'))
        if execution_override is not None:execution=execution_override
        strategy=replace(_strategy(),buffer_bps=0 if sell else 1000,max_hold_minutes=15)
        side='SELL' if sell else 'BUY'
        old,candidate=(2000,1000) if sell else (1000,1100)
        target=0 if sell else 10
        def at(value): return datetime.fromisoformat(self.fixture.at(value))+timedelta(seconds=2)
        plan=(UnfilledResearchAction('SUBMIT','005930',side,10,old,candidate,target,at(minute-2),case,'native-population'),
              UnfilledResearchAction(action,'005930',side,10,old,candidate,target,at(minute),case,'native-population'))
        scope='independent_development_validation/v1'
        run_id,spec=research_run_identity(self.dataset,strategy,execution,self.evaluation,session_profile=PROFILE,
            execution_scope=scope,unfilled_action_plan=plan)
        self.writer.start_run(run_id,spec,self.dataset.manifest,claim_independent=True)
        result=execute_research(self.dataset,self.writer,self.fixture.root/'runs',strategy,execution,self.evaluation,
            session_profile=PROFILE,execution_scope=scope,unfilled_action_plan=plan)
        self.assertEqual('ELIGIBLE',result.report_status)
        events=self.writer.load_execution_events(run_id)
        start=next(e for e in events if e['event_type']=='UNFILLED_BASELINE' and json.loads(e['reason'])['request']['action']==action)
        end=next(e for e in events if e['event_type']=='MARK' and datetime.fromisoformat(e['occurred_at'])==datetime.fromisoformat(self.fixture.at(minute+2)))
        selection={'run_id':run_id,'start_event_id':start['event_id'],'end_event_id':end['event_id'],'action':action,'case_ref':case}
        return selection,events,plan

    def read(self, selections, **kwargs):
        from kiwoom_monitor.application.research_evaluation import read_unfilled_action_outcomes
        reader=ResearchRepository(self.path,read_only=True)
        return read_unfilled_action_outcomes(reader.load_position_action_evidence,selections,
            decision_time=self.cut,horizon_seconds=120,population_ref='native-population',symbol='005930',**kwargs)

    def test_native_wait_and_reprice_are_runtime_generated_and_pair_same_state(self):
        selections=[self.source(a)[0] for a in ('WAIT','REPRICE')]
        outcomes,receipt=self.read(selections)
        self.assertEqual((2,[]),(len(outcomes),receipt['excluded']))
        by_action={o.action:o for o in outcomes}
        self.assertEqual(by_action['WAIT'].initial_state_ref,by_action['REPRICE'].initial_state_ref)
        self.assertEqual(0,by_action['WAIT'].fee_tax_won)
        self.assertGreater(by_action['REPRICE'].fee_tax_won,0)
        self.assertEqual('ACCEPTED',by_action['WAIT'].order_state)
        self.assertEqual('minute',by_action['WAIT'].input_resolution)
        self.assertFalse(receipt['order_authorized'])

    def test_two_native_pairs_use_common_comparison_and_fees_once(self):
        from kiwoom_monitor.application.research_evaluation import compare_position_action_outcomes
        selections=[self.source(a,case=f'case-{m}',minute=m)[0] for m in (5,6) for a in ('WAIT','REPRICE')]
        outcomes,receipt=self.read(selections)
        self.assertEqual((4,[]),(len(outcomes),receipt['excluded']))
        first=outcomes[0]
        binding={k:getattr(first,k) for k in ('initial_quantity','pending_buy_quantity','pending_sell_quantity',
            'target_quantity','policy_version','environment','venue','source_kind','execution_model_ref','cost_model_ref')}
        binding['reasons']=()
        rows=compare_position_action_outcomes(outcomes,{a:binding for a in ('WAIT','REPRICE')},baseline_action='WAIT',
            decision_time=self.cut,horizon_seconds=120,population_ref='native-population',input_resolution='minute',minimum_samples=2)
        self.assertEqual(2,rows[1]['sample_count'])
        from tests.unit.test_order_lifecycle import UnfilledPairedEvidenceTests
        from kiwoom_monitor.application.order_lifecycle import prepare_unfilled_reprice_evidence
        fixture=UnfilledPairedEvidenceTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        reader=ResearchRepository(self.path,read_only=True)
        context,_=reader.load_position_action_evidence(selections[0]['run_id'],(selections[0]['end_event_id'],),decision_time=self.cut)
        published=max(o.available_at for o in outcomes)
        evidence,report=prepare_unfilled_reprice_evidence(outcomes,policy=replace(fixture.policy,reprice_horizon_seconds=120),
            record=fixture.record,pending_quantity=10,held_quantity=0,target_quantity=10,candidate_price=1100,
            cost_model=context['unfilled_execution'].cost_model,population_ref='native-population',
            available_at=published,decision_time=published)
        self.assertEqual((2,-10,0),(evidence.paired_sample_count,evidence.net_advantage_won,evidence.net_standard_error_won))
        self.assertFalse(report['order_authorized'])

    def test_native_sell_pending_uses_actual_prior_fills_and_cancels_before_replacement(self):
        selection,events,_=self.source('REPRICE',minute=7,sell=True)
        outcomes,receipt=self.read([selection])
        self.assertEqual((1,[]),(len(outcomes),receipt['excluded']))
        self.assertEqual(('SELL','TAKE_PROFIT',10,0),(outcomes[0].side,outcomes[0].purpose,
            outcomes[0].initial_quantity,outcomes[0].target_quantity))
        index=next(i for i,e in enumerate(events) if e['event_id']==selection['start_event_id'])
        self.assertEqual(['ORDER_CANCELLED','ORDER_SUBMITTED'],[e['event_type'] for e in events[index+1:index+3]])

    def test_final_scope_legacy_and_duplicate_plan_are_rejected_before_start(self):
        from scripts.run_research import research_run_identity
        from tests.unit.test_research_development_partitions import _strategy,_execution,PROFILE
        _,_,plan=self.source('WAIT')
        for change in ({'execution_scope':None},{'unfilled_action_plan':(plan[0],plan[0])},
                       {'unfilled_action_plan':tuple(reversed(plan))},{'session_profile':None}):
            options={'execution_scope':'independent_development_validation/v1','session_profile':PROFILE,'unfilled_action_plan':plan}
            options.update(change)
            with self.subTest(change=change),self.assertRaises(ValueError):
                research_run_identity(self.dataset,_strategy(),_execution(),self.evaluation,**options)

    def test_native_reader_rejects_wrong_role_cut_missing_end_and_caller_relabel(self):
        from datetime import datetime
        selection,events,_=self.source('WAIT')
        outcomes,receipt=self.read([selection],development_role='VALIDATION')
        self.assertEqual((),outcomes); self.assertIn('role_mismatch',receipt['excluded'][0]['reason'])
        old=self.cut; self.cut=datetime.fromisoformat(self.fixture.at(9))
        outcomes,receipt=self.read([selection]); self.assertEqual((),outcomes)
        self.cut=old
        outcomes,receipt=self.read([{**selection,'end_event_id':'missing'}]); self.assertEqual((),outcomes)
        with self.assertRaisesRegex(ValueError,'locked plan'):
            self.read([{**selection,'case_ref':'relabeled'}])

    def test_native_event_tampering_is_rejected_before_outcome_values(self):
        selection,events,_=self.source('REPRICE')
        with closing(self.writer._connect()) as connection,connection:
            row=connection.execute('SELECT document_json FROM research_execution_events WHERE event_id=?',(selection['end_event_id'],)).fetchone()
            body=json.loads(row[0]); body['cash_after_won']+=1000
            connection.execute('UPDATE research_execution_events SET document_json=? WHERE event_id=?',
                (json.dumps(body),selection['end_event_id']))
        with self.assertRaisesRegex(ValueError,'content or identity'):
            self.read([selection])

    def test_adapter_replays_cash_cost_price_and_reservation_instead_of_trusting_hash(self):
        from kiwoom_monitor.application.research_evaluation import unfilled_action_outcome_from_events
        selection,events,_=self.source('REPRICE')
        reader=ResearchRepository(self.path,read_only=True)
        context,raw=reader.load_position_action_evidence(selection['run_id'],(selection['end_event_id'],),decision_time=self.cut)
        for kind,key in (('FILL','commission_won'),('ORDER_SUBMITTED','price'),('MARK','cash_after_won')):
            changed=[dict(e) for e in raw]
            left=next(i for i,e in enumerate(changed) if e['event_id']==selection['start_event_id'])
            index=next(i for i in range(left+1,len(changed)) if changed[i]['event_type']==kind)
            changed[index][key]+=1
            with self.subTest(kind=kind),self.assertRaises(ValueError):
                unfilled_action_outcome_from_events(context,changed,**{k:selection[k] for k in
                    ('start_event_id','end_event_id','action','case_ref')},population_ref='native-population',horizon_seconds=120,symbol='005930')

    def test_duplicate_effective_source_is_not_counted_twice_and_zero_horizon_rejected(self):
        selection,_,_=self.source('WAIT')
        with self.assertRaisesRegex(ValueError,'duplicate'):
            self.read([selection,selection])
        from kiwoom_monitor.application.research_execution import UnfilledResearchAction
        from kiwoom_monitor.application.research_evaluation import read_unfilled_action_outcomes
        from unittest.mock import Mock
        load = Mock(side_effect=AssertionError('invalid horizon must not read source'))
        with self.assertRaisesRegex(ValueError,'bounded mock/KRX minute source'):
            read_unfilled_action_outcomes(load,[selection],decision_time=self.cut,horizon_seconds=0,
                population_ref='native-population',symbol='005930')
        load.assert_not_called()
        with self.assertRaisesRegex(ValueError,'quantity/price'):
            UnfilledResearchAction('WAIT','005930','BUY',True,1000,1100,10,self.cut,'case','population')

    def test_readonly_snapshot_budget_and_final_overlap_precede_event_values(self):
        from unittest.mock import patch
        from kiwoom_monitor.application.research_evaluation import read_unfilled_action_outcomes
        selection,_,_=self.source('WAIT')
        reader=ResearchRepository(self.path,read_only=True)
        with self.assertRaisesRegex(ValueError,'budget'):
            reader.load_position_action_evidence(selection['run_id'],(selection['end_event_id'],),decision_time=self.cut,limit=1)
        sql=[]; original=reader._connect
        def connect():
            value=original();value.set_trace_callback(sql.append);return value
        with patch.object(reader,'_connect',side_effect=connect):
            outcomes,receipt=read_unfilled_action_outcomes(reader.load_position_action_evidence,[selection],
                decision_time=self.cut,horizon_seconds=120,population_ref='native-population',symbol='005930')
        self.assertEqual(1,len(outcomes))
        self.assertEqual(1,sum(s.startswith('BEGIN') for s in sql))
        self.assertIn('ROLLBACK',sql)
        self.assertFalse(any(s.lstrip().split()[0].upper() in {'INSERT','UPDATE','DELETE','COMMIT'} for s in sql))
        with closing(self.writer._connect()) as connection,connection:
            connection.execute('INSERT INTO research_final_holdout_windows VALUES(?,?,?,?,?,?,?)',
                ('unfilled-final',self.fixture.at(0),self.fixture.at(10),'FINAL_RESERVED','batch','{}',self.cut.isoformat()))
        sql.clear()
        with patch.object(reader,'_connect',side_effect=connect),self.assertRaisesRegex(ValueError,'final window'):
            reader.load_position_action_evidence(selection['run_id'],(selection['end_event_id'],),decision_time=self.cut)
        self.assertFalse(any('FROM research_execution_events' in s for s in sql))

    def test_gap_duplicate_revision_and_locked_action_tampering_do_not_produce_outcomes(self):
        from copy import deepcopy
        from kiwoom_monitor.application.research_evaluation import unfilled_action_outcome_from_events,PositionOutcomeUnavailable
        selection,_,_=self.source('WAIT')
        context,raw=ResearchRepository(self.path,read_only=True).load_position_action_evidence(
            selection['run_id'],(selection['end_event_id'],),decision_time=self.cut)
        for kind in ('gap','revision','action'):
            changed=deepcopy(list(raw))
            if kind=='action':
                index=next(i for i,e in enumerate(changed) if e['event_id']==selection['start_event_id'])
                proof=json.loads(changed[index]['reason']);proof['request']['candidate_limit_price_won']+=1
            else:
                left=next(i for i,e in enumerate(changed) if e['event_id']==selection['start_event_id'])
                index=next(i for i in range(left+1,len(changed)) if changed[i]['event_type']=='UNFILLED_BAR')
                proof=json.loads(changed[index]['reason'])
                if kind=='gap': proof['source_bar']['bar_start']=self.fixture.at(4)
                else:
                    source=json.loads(changed[left]['reason'])['source_bar']
                    proof['source_bar']['revision_id']=source['revision_id']
            changed[index]['reason']=json.dumps(proof)
            with self.subTest(kind=kind),self.assertRaises(ValueError):
                unfilled_action_outcome_from_events(context,changed,**{k:selection[k] for k in
                    ('start_event_id','end_event_id','action','case_ref')},population_ref='native-population',horizon_seconds=120,symbol='005930')

    def test_case_label_cannot_create_additional_effective_samples(self):
        selections=[self.source('WAIT',case=name)[0] for name in ('case-a','renamed-same-case')]
        outcomes,receipt=self.read(selections)
        self.assertEqual(1,len(outcomes))
        self.assertEqual('duplicate_effective_native_evidence',receipt['excluded'][0]['reason'])


class DynamicPositionAllocationTests(unittest.TestCase):
    def test_only_nonzero_context_weights_require_scores_while_theme_risk_keeps_membership(self):
        policy=replace(self.policy,context_weights=(0,0,1))
        candidates=tuple(replace(c,market_strength_bps=None,theme_strength_bps=None) for c in (self.a,self.b))
        result=self.run_allocation(policy=policy,candidates=candidates)
        original=self.run_allocation(policy=policy)
        self.assertEqual('CALCULATED',result['status'])
        self.assertEqual(self.amounts(original),self.amounts(result))
        missing=replace(candidates[0],stock_strength_bps=None)
        denied=self.run_allocation(policy=policy,candidates=(missing,candidates[1]))
        self.assertEqual('BLOCKED',denied['status'])
        self.assertIn('allocation_context_score_unavailable:005930:stock_strength_bps',denied['reasons'])
        with self.assertRaises(ValueError):replace(candidates[0],theme_ids=())
    def setUp(self):
        from datetime import timedelta
        from kiwoom_monitor.application.position_target import DynamicPositionAllocationPolicy,DynamicPositionCandidate,PositionAccountObservation,evaluate_dynamic_position_allocation
        from dataclasses import replace
        self.fixture=PositionTargetShadowTests();self.fixture.setUp();self.now=self.fixture.now
        self.policy=DynamicPositionAllocationPolicy('experimental-allocation-v1','account-r4',100000,10000,10000,
            1000,1000,100000,100000,1000,0,1,(1,1,1),60,30,self.now-timedelta(seconds=1),self.now+timedelta(seconds=30))
        self.a=DynamicPositionCandidate('005930',('theme-a',),'features-a',self.now,1000,1000000000,1000000,60,100000,
            10000,10000,10000,100,10,100,1000)
        self.b=replace(self.a,symbol='000660',theme_ids=('theme-b',),input_ref='features-b',net_ev_bps=50)
        self.observation=PositionAccountObservation(replace(self.fixture.account,available_cash_won=100000,positions={}),(),
            'native-account-cut',self.now,True,True,())
        self.evaluate=evaluate_dynamic_position_allocation

    def run_allocation(self,**kwargs):
        arguments=dict(policy=self.policy,candidates=(self.a,self.b),observation=self.observation,decision_time=self.now,
            cost_model=self.fixture.cost,risk_disposition='ALLOW',risk_input_ref='native-risk-cut')
        arguments.update(kwargs);return self.evaluate(**arguments)

    def amounts(self,result):return {r['symbol']:r for r in result['allocations']}

    def test_automatic_amounts_follow_ev_without_manual_symbol_notional(self):
        result=self.run_allocation();rows=self.amounts(result)
        self.assertEqual('CALCULATED',result['status']);self.assertFalse(result['order_authorized'])
        self.assertGreater(rows[self.a.symbol]['target_notional_won'],rows[self.b.symbol]['target_notional_won'])
        self.assertLessEqual(result['new_buy_cash_won'],99000)
        self.assertLessEqual(result['worst_case_portfolio_notional_won'],100000)
        self.assertEqual('cash_plus_marked_long_positions/v1',result['equity_basis'])

    def test_market_theme_stock_scores_and_volatility_change_weights(self):
        from dataclasses import replace
        for changed in (replace(self.a,market_strength_bps=0),replace(self.a,theme_strength_bps=0),
                        replace(self.a,stock_strength_bps=0),replace(self.a,volatility_bps=200)):
            before=self.amounts(self.run_allocation())[self.a.symbol]['desired_notional_won']
            after=self.amounts(self.run_allocation(candidates=(changed,self.b)))[self.a.symbol]['desired_notional_won']
            self.assertLess(after,before)

    def test_market_cap_turnover_and_full_exit_capacity_cap_target(self):
        from dataclasses import replace
        for changed,cap in ((replace(self.a,market_cap_won=20000),2000),
                            (replace(self.a,recent_turnover_won=30000),3000),
                            (replace(self.a,conservative_exit_capacity_won=4000),4000)):
            row=self.amounts(self.run_allocation(candidates=(changed,self.b)))[self.a.symbol]
            self.assertLessEqual(row['target_notional_won'],cap)

    def test_overlapping_theme_caps_count_entire_position_in_every_theme(self):
        from dataclasses import replace
        a=replace(self.a,theme_ids=('shared','other'));b=replace(self.b,theme_ids=('shared',))
        result=self.run_allocation(policy=replace(self.policy,max_theme_weight_bps=5000),candidates=(a,b))
        self.assertLessEqual(result['worst_case_theme_notional_won']['shared'],50000)
        self.assertEqual(sum(r['worst_case_notional_won'] for r in result['allocations']),result['worst_case_theme_notional_won']['shared'])

    def test_stress_and_volatility_portfolio_budgets_are_rechecked(self):
        from dataclasses import replace
        for policy in (replace(self.policy,max_stress_loss_won=1500),replace(self.policy,max_volatility_risk_won=150)):
            result=self.run_allocation(policy=policy)
            self.assertLessEqual(result['modeled_stress_loss_won'],policy.max_stress_loss_won)
            self.assertLessEqual(result['linear_volatility_risk_won'],policy.max_volatility_risk_won)

    def test_unknown_ev_keeps_existing_position_without_inventing_new_risk(self):
        from dataclasses import replace
        observation=replace(self.observation,account=replace(self.observation.account,positions={self.a.symbol:10}))
        a=replace(self.a,net_ev_bps=None,ev_uncertainty_bps=None)
        result=self.run_allocation(candidates=(a,self.b),observation=observation);row=self.amounts(result)[self.a.symbol]
        self.assertEqual(10000,row['target_notional_won']);self.assertEqual(0,row['new_buy_quantity'])
        self.assertIn('ev_unavailable',row['limits'])

    def test_negative_or_uncertain_ev_never_allocates_new_cash(self):
        from dataclasses import replace
        for a in (replace(self.a,net_ev_bps=-1),replace(self.a,ev_uncertainty_bps=101)):
            row=self.amounts(self.run_allocation(candidates=(a,self.b)))[self.a.symbol]
            self.assertEqual(0,row['new_buy_quantity'])

    def test_pending_sell_never_releases_exposure_risk_or_finances_other_buy(self):
        from dataclasses import replace
        account=replace(self.observation.account,available_cash_won=20000,positions={self.a.symbol:10})
        observation=replace(self.observation,account=account,open_orders=(self.fixture.record('SELL',quantity=10,filled=0,state='ACCEPTED'),))
        b=replace(self.b,net_ev_bps=10000)
        result=self.run_allocation(policy=replace(self.policy,operating_limit_won=25000,cash_buffer_won=0),
            candidates=(self.a,b),observation=observation)
        self.assertEqual('CALCULATED',result['status'],result)
        self.assertEqual(10000,self.amounts(result)[self.a.symbol]['worst_case_notional_won'])
        self.assertLessEqual(result['worst_case_portfolio_notional_won'],25000)
        self.assertLessEqual(self.amounts(result)[self.b.symbol]['new_buy_quantity'],15)

    def test_pending_buy_is_reserved_and_never_duplicated(self):
        from dataclasses import replace
        order=self.fixture.record('BUY',quantity=10,filled=0,state='ACCEPTED')
        observation=replace(self.observation,account=replace(self.observation.account,reserved_open_buy_won=10000),open_orders=(order,))
        row=self.amounts(self.run_allocation(observation=observation))[self.a.symbol]
        self.assertEqual(0,row['new_buy_quantity']);self.assertEqual(10000,row['worst_case_notional_won'])

    def test_existing_portfolio_limit_breach_restricts_new_risk_without_crediting_sales(self):
        from dataclasses import replace
        observation=replace(self.observation,account=replace(self.observation.account,positions={self.a.symbol:50}))
        result=self.run_allocation(policy=replace(self.policy,operating_limit_won=40000),observation=observation)
        self.assertEqual('REDUCE_ONLY',result['status']);self.assertEqual(0,result['new_buy_cash_won'])
        self.assertEqual(50000,result['worst_case_portfolio_notional_won'])

    def test_hard_protection_is_independent_of_ev_and_does_not_authorize_orders(self):
        from dataclasses import replace
        result=self.run_allocation(risk_disposition='EXIT_REQUIRED',cost_model=None,candidates=(replace(self.a,net_ev_bps=None),self.b))
        self.assertTrue(result['protection_required']);self.assertEqual(0,result['new_buy_cash_won']);self.assertFalse(result['order_authorized'])
        for disposition in ('NO_NEW_RISK','REDUCE_ONLY','HALT_NEW_ORDERS'):
            self.assertEqual(0,self.run_allocation(risk_disposition=disposition)['new_buy_cash_won'])

    def test_stale_future_incomplete_window_and_missing_held_symbol_block(self):
        from dataclasses import replace
        from datetime import timedelta
        for kwargs in (dict(candidates=(replace(self.a,available_at=self.now+timedelta(seconds=1)),self.b)),
                       dict(candidates=(replace(self.a,available_at=self.now-timedelta(seconds=31)),self.b)),
                       dict(candidates=(replace(self.a,turnover_window_seconds=30),self.b)),
                       dict(observation=replace(self.observation,reconciliation_complete=False)),
                       dict(observation=replace(self.observation,account=replace(self.observation.account,positions={'100001':1}))),
                       dict(cost_model=None)):
            result=self.run_allocation(**kwargs);self.assertEqual('BLOCKED',result['status']);self.assertEqual(0,result['new_buy_cash_won'])

    def test_unknown_order_and_unverified_reservation_block(self):
        from dataclasses import replace
        for order in (self.fixture.record(state='SUBMISSION_UNKNOWN'),self.fixture.record('BUY',quantity=10,filled=0,state='ACCEPTED')):
            result=self.run_allocation(observation=replace(self.observation,open_orders=(order,)))
            self.assertEqual('BLOCKED',result['status']);self.assertEqual(0,result['new_buy_cash_won'])

    def test_candidates_and_theme_permutations_preserve_result_and_identity(self):
        from dataclasses import replace
        a=replace(self.a,theme_ids=('x','y'));flipped=replace(a,theme_ids=('y','x'))
        self.assertEqual(self.run_allocation(candidates=(a,self.b)),self.run_allocation(candidates=(self.b,flipped)))

    def test_generated_policy_reuses_existing_lower_quantity_and_cost_calculation(self):
        from dataclasses import replace
        from datetime import datetime
        from kiwoom_monitor.application.position_target import PositionTargetSizingPolicy
        result=self.run_allocation()
        for row in result['allocations']:
            values=dict(row['target_policy']);values['available_at']=datetime.fromisoformat(values['available_at']);values['valid_until']=datetime.fromisoformat(values['valid_until'])
            request=replace(self.fixture.request,symbol=row['symbol'],action='REENTER',target_quantity=None,target_policy=PositionTargetSizingPolicy(**values))
            lower=self.fixture.evaluate(request,self.observation.account,())
            self.assertEqual(row['target_notional_won'],lower['target_notional_won']);self.assertEqual(row['new_buy_quantity'],lower['incremental_quantity'])

    def test_overlimit_shared_theme_is_reduced_in_goal_but_not_freed_in_current_risk(self):
        from dataclasses import replace
        a=replace(self.a,theme_ids=('shared',));b=replace(self.b,theme_ids=('shared',))
        observation=replace(self.observation,account=replace(self.observation.account,positions={a.symbol:50,b.symbol:50}))
        result=self.run_allocation(policy=replace(self.policy,max_theme_weight_bps=2500),candidates=(a,b),observation=observation)
        self.assertEqual('REDUCE_ONLY',result['status']);self.assertEqual(0,result['new_buy_cash_won'])
        self.assertEqual(100000,result['worst_case_theme_notional_won']['shared'])
        self.assertLessEqual(result['target_theme_notional_won']['shared'],50000)

    def document(self):
        from dataclasses import asdict
        import json
        from kiwoom_monitor.application.position_target import _json_value
        value={'schema':'dynamic-position-allocation-shadow/v1','policy':asdict(self.policy),
            'candidates':[asdict(self.a),asdict(self.b)],'observation':asdict(self.observation),'decision_time':self.now,
            'cost_model':self.fixture.cost.to_dict(),'risk_disposition':'ALLOW','risk_input_ref':'native-risk-cut'}
        return json.loads(json.dumps(value,default=_json_value))

    def test_existing_preview_cli_uses_same_pure_policy_and_lower_contract(self):
        import json
        from scripts.preview_market_theme_shadow import evaluate_document
        self.assertEqual(json.loads(json.dumps(self.run_allocation())),evaluate_document(self.document()))

    def test_document_rejects_unbound_native_risk_unknown_fields_and_symbol_amounts(self):
        from scripts.preview_market_theme_shadow import evaluate_document
        for mutate in (lambda d:d.update(order_authorized=True),
                       lambda d:d['policy'].update(symbol_investment_won=12345),
                       lambda d:d['observation'].update(risk_snapshot={'source':'native'}),
                       lambda d:d['candidates'][0].update(theme_ids='abc'),
                       lambda d:d['observation'].update(reasons=''),
                       lambda d:d['candidates'][0].update(net_ev_bps=True)):
            d=self.document();mutate(d)
            with self.assertRaises((ValueError,TypeError)):evaluate_document(d)

    def test_exact_source_expiry_blocks_and_repeated_calculation_cannot_renew_ttl(self):
        from datetime import timedelta
        from dataclasses import replace
        first=self.run_allocation();later=self.run_allocation(decision_time=self.now+timedelta(seconds=1))
        self.assertEqual(first['available_at'],later['available_at']);self.assertEqual(first['valid_until'],later['valid_until'])
        expired=self.run_allocation(decision_time=self.now+timedelta(seconds=30),policy=replace(self.policy,valid_until=self.now+timedelta(seconds=60)))
        self.assertEqual('BLOCKED',expired['status']);self.assertIn('allocation_source_expired',expired['reasons'])

    def test_per_share_risk_rounding_is_identical_for_existing_and_proposed_holdings(self):
        from dataclasses import replace
        a=replace(self.a,reference_price_won=1001,stress_loss_bps=1,volatility_bps=1,net_ev_bps=None)
        observation=replace(self.observation,account=replace(self.observation.account,positions={a.symbol:100}))
        result=self.run_allocation(candidates=(a,self.b),observation=observation,policy=replace(self.policy,max_stress_loss_won=20,max_volatility_risk_won=20))
        self.assertEqual('REDUCE_ONLY',result['status']);self.assertEqual(100,result['modeled_stress_loss_won'])
        self.assertEqual(100,result['linear_volatility_risk_won']);self.assertEqual(0,result['new_buy_cash_won'])
        self.assertLessEqual(result['target_modeled_stress_loss_won'],20)

    def test_limits_and_duplicate_candidates_are_strict_and_no_default_leverage(self):
        from dataclasses import replace
        for changes in ({'operating_limit_won':True},{'max_symbol_weight_bps':10001},{'context_weights':(0,0,0)}):
            with self.assertRaises(ValueError):replace(self.policy,**changes)
        with self.assertRaises(ValueError):self.run_allocation(candidates=(self.a,self.a))
        with self.assertRaises(ValueError):self.run_allocation(risk_input_ref='')
        result=self.run_allocation(policy=replace(self.policy,operating_limit_won=100000000))
        self.assertLessEqual(result['worst_case_portfolio_notional_won'],result['equity_won'])


class NativeDynamicAllocationEvTests(unittest.TestCase):
    setUp = NativeAutomaticPositionOutcomeTests.setUp
    source = NativeAutomaticPositionOutcomeTests.source

    def prepare(self, action='ADD',cost_override=None,reference_price=1000,terminal_price=None,horizon_minutes=2):
        from datetime import timedelta
        from kiwoom_monitor.application.research_evaluation import read_dynamic_allocation_ev_evidence
        self.selections=[]
        for minute in (0,20):
            for selected in ('HOLD',action):
                row,_,request,_=self.source(selected,minute=minute,flat=action=='REENTER',cost_override=cost_override,reference_price=reference_price,terminal_price=terminal_price,horizon_minutes=horizon_minutes)
                self.selections.append(row)
                if request is not None:self.request=request
        fixture=PositionTargetShadowTests();fixture.setUp();self.cost=cost_override or fixture.cost
        self.account=replace(fixture.account,account_ref=self.request.account_ref,as_of=self.cut,available_cash_won=89970,
            positions={} if action=='REENTER' else {'005930':10})
        self.request=replace(self.request,decision_time=self.cut,reference_price_available_at=self.cut)
        self.arguments=dict(request=self.request,account=self.account,open_orders=(),horizon_seconds=60*horizon_minutes,
            population_ref='allocation-cohort',minimum_samples=2,max_evidence_age_seconds=3600)
        self.reader=ResearchRepository(self.path,read_only=True)
        self.read_ev=read_dynamic_allocation_ev_evidence
        return self.read_ev(self.reader.load_position_action_evidence,self.selections,**self.arguments)

    def test_real_native_pairs_produce_signed_incremental_ev_without_database_writes(self):
        from unittest.mock import patch
        for action in ('ADD','REENTER'):
            with self.subTest(action=action):
                result=self.prepare(action);evidence=result['evidence']
                self.assertEqual('EMPIRICAL_ESTIMATE',result['quality']['status'])
                self.assertIsNotNone(evidence);self.assertEqual(20,evidence.target_quantity)
                self.assertEqual(-30 if action=='ADD' else -30,evidence.net_ev_bps)
                self.assertEqual(0,evidence.ev_uncertainty_bps)
                before=self.path.read_bytes();sql=[];original=self.reader._connect
                def connect():
                    c=original();c.set_trace_callback(sql.append);return c
                with patch.object(self.reader,'_connect',side_effect=connect):
                    repeated=self.read_ev(self.reader.load_position_action_evidence,self.selections,**self.arguments)
                self.assertEqual(result,repeated);self.assertEqual(before,self.path.read_bytes())
                self.assertEqual(4,sum(s.lstrip().upper().startswith('BEGIN') for s in sql))
                self.assertEqual(4,sum(s.lstrip().upper().startswith('ROLLBACK') for s in sql))
                self.assertFalse(any(s=='COMMIT' for s in sql));self.assertFalse(result['order_authorized'])
                self.assertEqual(2,len(result['missing_producers']))

    def test_price_quantity_cost_missing_pair_and_expiry_cannot_reuse_ev(self):
        from datetime import timedelta
        result=self.prepare();self.assertIsNotNone(result['evidence'])
        changes=(dict(request=replace(self.request,reference_price_won=1001)),
            dict(account=replace(self.account,positions={'005930':11})),
            dict(request=replace(self.request,cost_model=replace(self.cost,commission_bps=2))),
            dict(max_evidence_age_seconds=1))
        for change in changes:
            with self.subTest(change=change):
                value=self.read_ev(self.reader.load_position_action_evidence,self.selections,**{**self.arguments,**change})
                self.assertIsNone(value['evidence']);self.assertEqual('EV_UNAVAILABLE',value['quality']['status'])
        with self.assertRaises(ValueError):
            self.read_ev(self.reader.load_position_action_evidence,self.selections[:2],**self.arguments)
        incomplete=[*self.selections[:-1],self.source('ADD',minute=40)[0]]
        self.assertIsNone(self.read_ev(self.reader.load_position_action_evidence,incomplete,**self.arguments)['evidence'])

    def test_original_availability_and_expiry_do_not_refresh_on_retry(self):
        from datetime import timedelta
        first=self.prepare()['evidence'];self.assertIsNotNone(first)
        later=self.cut+timedelta(seconds=10)
        result=self.read_ev(self.reader.load_position_action_evidence,self.selections,
            **{**self.arguments,'request':replace(self.request,decision_time=later,reference_price_available_at=later),
                'account':replace(self.account,as_of=later)})
        self.assertEqual((first.available_at,first.valid_until),(result['evidence'].available_at,result['evidence'].valid_until))
        at=first.valid_until
        expired=self.read_ev(self.reader.load_position_action_evidence,self.selections,
            **{**self.arguments,'request':replace(self.request,decision_time=at,reference_price_available_at=at),
                'account':replace(self.account,as_of=at)})
        self.assertIsNone(expired['evidence'])

    def test_existing_cli_reads_native_ev_and_preserves_database(self):
        import subprocess,sys
        from kiwoom_monitor.application.position_target import _json_value
        result=self.prepare()
        document={'schema':'dynamic-allocation-native-ev/v1','request':asdict(self.request),
            'account':asdict(self.account),'open_orders':[],**{k:self.arguments[k] for k in
                ('horizon_seconds','population_ref','minimum_samples','max_evidence_age_seconds')}}
        directory=Path(self.directory.name)
        (directory/'input.json').write_text(json.dumps(document,default=_json_value),encoding='utf-8')
        (directory/'selection.json').write_text(json.dumps(self.selections),encoding='utf-8')
        before=self.path.read_bytes()
        args=[sys.executable,'-m','scripts.preview_market_theme_shadow','--input',str(directory/'input.json'),
            '--research-db',str(self.path),'--position-outcome-selection',str(directory/'selection.json'),
            '--position-outcome-role','TRAIN']
        completed=subprocess.run(args,capture_output=True,text=True,encoding='utf-8',timeout=60)
        self.assertEqual(0,completed.returncode,completed.stderr)
        raw=json.loads(completed.stdout)
        self.assertEqual(result['evidence'].net_ev_bps,raw['evidence']['net_ev_bps'])
        self.assertFalse(raw['order_authorized']);self.assertEqual(before,self.path.read_bytes())
        document['caller_ev_bps']=9999
        (directory/'input.json').write_text(json.dumps(document,default=_json_value),encoding='utf-8')
        invalid=subprocess.run(args,capture_output=True,text=True,encoding='utf-8',timeout=60)
        self.assertEqual(2,invalid.returncode);self.assertEqual(before,self.path.read_bytes())

    def test_gapped_native_branch_and_wrong_development_role_are_unavailable(self):
        self.prepare()
        hold=self.source('HOLD',minute=40)[0]
        gapped=self.source('ADD',minute=40,skip_minute=True)[0]
        selected=[*self.selections[:2],hold,gapped]
        result=self.read_ev(self.reader.load_position_action_evidence,selected,**self.arguments)
        self.assertIsNone(result['evidence']);self.assertTrue(result['source_receipt']['excluded'])
        wrong_role=self.read_ev(self.reader.load_position_action_evidence,self.selections,
            **{**self.arguments,'development_role':'VALIDATION'})
        self.assertIsNone(wrong_role['evidence'])

    def test_unfilled_or_exit_labels_are_rejected_before_native_read(self):
        from unittest.mock import Mock
        self.prepare();load=Mock(side_effect=AssertionError('invalid source read'))
        for action in ('WAIT','REPRICE','EXIT'):
            selected=[{**r,'action':action} for r in self.selections]
            with self.assertRaises(ValueError):self.read_ev(load,selected,**self.arguments)
        load.assert_not_called()


class DynamicAllocationEvSizeTests(unittest.TestCase):
    def setUp(self):
        from datetime import timedelta
        from kiwoom_monitor.application.position_target import DynamicAllocationEvEvidence
        from kiwoom_monitor.application.research_evaluation import position_action_cost_model_ref
        self.fixture=DynamicPositionAllocationTests();self.fixture.setUp();self.now=self.fixture.now
        self.fixture.policy=replace(self.fixture.policy,allocation_ev_horizon_seconds=120)
        self.evidence=DynamicAllocationEvEvidence('005930','REENTER',0,20,1000,100,10,
            position_action_cost_model_ref(self.fixture.fixture.cost.to_dict()),120,'cohort','comparison',
            tuple('native-position-outcome_'+str(i) for i in range(4)),self.now-timedelta(seconds=1),self.now+timedelta(seconds=15))
        self.a=replace(self.fixture.a,ev_evidence=self.evidence)

    def run_allocation(self,**changes):
        return self.fixture.run_allocation(candidates=(self.a,),**changes)

    def test_single_high_weight_does_not_extrapolate_ev_to_more_shares(self):
        from unittest.mock import patch
        with patch('sqlite3.connect',side_effect=AssertionError('allocation read DB')):
            result=self.run_allocation()
        row=result['allocations'][0]
        self.assertEqual(20000,row['target_notional_won']);self.assertEqual(20,row['new_buy_quantity'])
        self.assertFalse(row['ev_evidence']['size_interpolation_supported'])
        from datetime import datetime
        self.assertEqual(self.evidence.valid_until,datetime.fromisoformat(result['valid_until']))

    def test_fractional_signed_mean_and_standard_error_round_conservatively(self):
        from kiwoom_monitor.application.research_evaluation import _position_action_estimate
        for values,mean in ((((1,0),(0,0)),1),(((-1,0),(0,0)),-2)):
            result=_position_action_estimate(values,bps_notional_won=3000)
            self.assertEqual((mean,2),(result['mean_net_change_bps_floor'],result['net_standard_error_bps_ceil']))
        self.assertNotIn('mean_net_change_bps_floor',_position_action_estimate(((1,0),(0,0))))

    def test_partial_cash_or_risk_budget_does_not_interpolate_tested_ev(self):
        observation=replace(self.fixture.observation,account=replace(self.fixture.observation.account,available_cash_won=15000))
        for change in (dict(observation=observation),dict(policy=replace(self.fixture.policy,max_stress_loss_won=1500))):
            row=self.run_allocation(**change)['allocations'][0]
            self.assertEqual(0,row['new_buy_quantity'])
            self.assertIn('allocation_ev_exact_tested_size_required',row['limits'])

    def test_price_state_cost_value_or_expired_evidence_blocks(self):
        from datetime import timedelta
        for candidate in (replace(self.a,reference_price_won=1001),replace(self.a,net_ev_bps=101),
                replace(self.a,ev_evidence=replace(self.evidence,valid_until=self.now))):
            self.assertEqual('BLOCKED',self.fixture.run_allocation(candidates=(candidate,))['status'])
        observation=replace(self.fixture.observation,account=replace(self.fixture.observation.account,positions={'005930':1}))
        self.assertEqual('BLOCKED',self.run_allocation(observation=observation)['status'])
        self.assertEqual('BLOCKED',self.run_allocation(policy=replace(self.fixture.policy,allocation_ev_horizon_seconds=60))['status'])

    def test_fixed_document_round_trip_preserves_optional_evidence(self):
        from kiwoom_monitor.application.position_target import evaluate_dynamic_position_allocation_document,_json_value
        f=self.fixture
        document={'schema':'dynamic-position-allocation-shadow/v1','policy':asdict(f.policy),
            'candidates':[asdict(self.a)],'observation':asdict(f.observation),'decision_time':self.now,
            'cost_model':f.fixture.cost.to_dict(),'risk_disposition':'ALLOW','risk_input_ref':'native-risk-cut'}
        raw=json.loads(json.dumps(document,default=_json_value))
        self.assertEqual(self.run_allocation(),evaluate_dynamic_position_allocation_document(raw))
        raw['candidates'][0]['ev_evidence']['input_refs']='not-an-array'
        with self.assertRaises(ValueError):evaluate_dynamic_position_allocation_document(raw)

    def test_negative_add_advantage_retains_holdings_without_inventing_exit_ev(self):
        e=replace(self.evidence,action='ADD',initial_quantity=10,net_ev_bps=-10)
        candidate=replace(self.a,net_ev_bps=-10,ev_evidence=e)
        observation=replace(self.fixture.observation,account=replace(self.fixture.observation.account,positions={'005930':10}))
        result=self.fixture.run_allocation(candidates=(candidate,),observation=observation)
        self.assertEqual((10000,0),(result['allocations'][0]['target_notional_won'],result['allocations'][0]['new_buy_quantity']))
        capped=self.fixture.run_allocation(candidates=(replace(candidate,conservative_exit_capacity_won=5000),),observation=observation)
        self.assertEqual('REDUCE_ONLY',capped['status']);self.assertEqual(5000,capped['allocations'][0]['target_notional_won'])


class NativeFullExitStressTests(unittest.TestCase):
    def prepare(self, execution_override=None):
        from datetime import datetime,timedelta
        from kiwoom_monitor.application.research_execution import FullExitStressPolicy,FullExitStressScenario
        from kiwoom_monitor.application.research_evaluation import read_full_exit_stress_evidence
        self.fixture=NativeUnfilledOutcomeTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        selected,events,_=self.fixture.source('WAIT',execution_override=execution_override)
        start=next(e for e in events if e['event_id']==selected['start_event_id'])
        self.source=json.loads(start['reason'])['source_bar']
        first=datetime.fromisoformat(self.source['available_at']).replace(second=0,microsecond=0)+timedelta(minutes=1)
        deadline=first+timedelta(seconds=120)
        end=next(e for e in events if e['event_type']=='MARK' and datetime.fromisoformat(e['occurred_at'])==deadline)
        self.selection={k:selected[k] for k in ('run_id','start_event_id')};self.selection['end_event_id']=end['event_id']
        self.policy=FullExitStressPolicy('paper-full-exit-stress/v1','native-exit-research','005930',20,self.source['close'],120,
            (FullExitStressScenario('reference',10000,0,0,0,()),FullExitStressScenario('adverse',5000,5000,100,100,())),
            datetime.fromisoformat(self.source['available_at'])-timedelta(seconds=1),datetime.fromisoformat('2030-01-01T00:00:00+00:00'))
        self.reader=ResearchRepository(self.fixture.path,read_only=True)
        self.read_exit=read_full_exit_stress_evidence
        self.args=dict(decision_time=self.fixture.cut,max_evidence_age_seconds=3600)
        return self.read_exit(self.reader.load_position_action_evidence,self.selection,self.policy,**self.args)

    def test_native_market_proofs_are_read_once_without_relabelling_unfilled_ev(self):
        from unittest.mock import patch
        self.prepare();before=self.fixture.path.read_bytes();sql=[];original=self.reader._connect
        def connect():
            c=original();c.set_trace_callback(sql.append);return c
        with patch.object(self.reader,'_connect',side_effect=connect):
            receipt=self.read_exit(self.reader.load_position_action_evidence,self.selection,self.policy,**self.args)
        self.assertEqual('CALCULATED',receipt['quality']['status']);self.assertIsNotNone(receipt['evidence'])
        self.assertEqual(20,receipt['evidence'].tested_position_quantity)
        self.assertEqual(2,receipt['source_receipt']['bar_count'])
        self.assertEqual(4,sum(s.lstrip().upper().startswith('SELECT') for s in sql))
        self.assertEqual(1,sum(s=='BEGIN' for s in sql));self.assertEqual(1,sum(s=='ROLLBACK' for s in sql))
        self.assertFalse(any(s=='COMMIT' for s in sql));self.assertEqual(before,self.fixture.path.read_bytes())
        self.assertNotIn('net_ev_bps',receipt['result']);self.assertFalse(receipt['order_authorized'])

    def test_incomplete_scenario_horizon_wrong_role_and_expiry_have_no_evidence(self):
        self.assertIsNotNone(self.prepare()['evidence'])
        cases=(dict(policy=replace(self.policy,tested_position_quantity=10**6)),
            dict(policy=replace(self.policy,exit_window_seconds=60)),
            dict(args={**self.args,'development_role':'VALIDATION'}),
            dict(args={**self.args,'max_evidence_age_seconds':1}))
        for case in cases:
            receipt=self.read_exit(self.reader.load_position_action_evidence,self.selection,case.get('policy',self.policy),
                **case.get('args',self.args))
            self.assertIsNone(receipt['evidence'])

    def test_repeated_prepare_does_not_refresh_native_source_expiry(self):
        from datetime import timedelta
        first=self.prepare()['evidence']
        later=self.read_exit(self.reader.load_position_action_evidence,self.selection,self.policy,
            **{**self.args,'decision_time':self.fixture.cut+timedelta(seconds=10)})['evidence']
        self.assertEqual(first,later)
        expired=self.read_exit(self.reader.load_position_action_evidence,self.selection,self.policy,
            **{**self.args,'decision_time':first.valid_until})
        self.assertIsNone(expired['evidence'])

    def test_existing_cli_accepts_exact_native_exit_schema_and_rejects_caller_capacity(self):
        import subprocess,sys
        from kiwoom_monitor.application.position_target import _json_value
        result=self.prepare();directory=self.fixture.fixture.root
        document={'schema':'dynamic-allocation-native-exit/v1','policy':asdict(self.policy),
            'decision_time':self.fixture.cut,'max_evidence_age_seconds':3600}
        (directory/'exit.json').write_text(json.dumps(document,default=_json_value),encoding='utf-8')
        (directory/'selection.json').write_text(json.dumps(self.selection),encoding='utf-8')
        args=[sys.executable,'-m','scripts.preview_market_theme_shadow','--input',str(directory/'exit.json'),
            '--research-db',str(self.fixture.path),'--position-outcome-selection',str(directory/'selection.json'),
            '--position-outcome-role','TRAIN']
        before=self.fixture.path.read_bytes()
        completed=subprocess.run(args,capture_output=True,text=True,encoding='utf-8',timeout=60)
        self.assertEqual(0,completed.returncode,completed.stderr);self.assertEqual(before,self.fixture.path.read_bytes())
        self.assertEqual(result['evidence'].source_ref,json.loads(completed.stdout)['evidence']['source_ref'])
        document['conservative_exit_capacity_won']=100000000
        (directory/'exit.json').write_text(json.dumps(document,default=_json_value),encoding='utf-8')
        invalid=subprocess.run(args,capture_output=True,text=True,encoding='utf-8',timeout=60)
        self.assertEqual(2,invalid.returncode);self.assertEqual(before,self.fixture.path.read_bytes())

    def test_real_native_ev_and_exit_producers_join_upper_allocation_without_io(self):
        from datetime import timedelta
        from unittest.mock import patch
        from kiwoom_monitor.application.research_execution import SimulationExecutionConfig
        ev_case=NativeDynamicAllocationEvTests();ev_case.setUp();self.addCleanup(ev_case.doCleanups)
        base=PositionTargetShadowTests();base.setUp()
        shared_cost=replace(base.cost,rate_basis='model_estimate',source='shared-research-cost',
            valid_from='2026-09-14T00:00:00+00:00',valid_to='2030-01-01T00:00:00+00:00')
        execution=SimulationExecutionConfig('next_tradable_bar_open/v1','conservative_with_optimistic_bound/v1',100000,shared_cost)
        exit_receipt=self.prepare(execution_override=execution);exit_evidence=exit_receipt['evidence']
        self.assertIsNotNone(exit_evidence)
        ev=ev_case.prepare(cost_override=shared_cost,reference_price=exit_evidence.reference_price_won)['evidence']
        self.assertIsNotNone(ev)
        self.assertEqual((ev.reference_price_won,ev.cost_model_ref),(exit_evidence.reference_price_won,exit_evidence.cost_model_ref))
        f=DynamicPositionAllocationTests();f.setUp();cut=self.fixture.cut
        account=replace(ev_case.account,as_of=cut)
        observation=replace(f.observation,account=account,observed_at=cut)
        policy=replace(f.policy,account_ref=account.account_ref,available_at=cut-timedelta(seconds=1),
            valid_until=cut+timedelta(seconds=20),allocation_ev_horizon_seconds=ev.horizon_seconds,
            allocation_exit_horizon_seconds=exit_evidence.exit_window_seconds)
        candidate=replace(f.a,available_at=cut,reference_price_won=ev.reference_price_won,
            net_ev_bps=ev.net_ev_bps,ev_uncertainty_bps=ev.ev_uncertainty_bps,
            ev_evidence=ev,conservative_exit_capacity_won=exit_evidence.conservative_exit_capacity_won,
            stress_loss_bps=exit_evidence.stress_loss_bps,full_exit_evidence=exit_evidence)
        with patch('sqlite3.connect',side_effect=AssertionError('joined calculation reread native sources')):
            result=f.run_allocation(policy=policy,candidates=(candidate,),observation=observation,
                decision_time=cut,cost_model=ev_case.cost)
        self.assertEqual('CALCULATED',result['status']);self.assertEqual(0,result['new_buy_cash_won'])
        self.assertEqual(10*ev.reference_price_won,result['allocations'][0]['target_notional_won']);self.assertFalse(result['order_authorized'])


class FullExitAllocationBindingTests(unittest.TestCase):
    def setUp(self):
        from datetime import timedelta
        from kiwoom_monitor.application.position_target import FullExitStressEvidence
        from kiwoom_monitor.application.research_evaluation import position_action_cost_model_ref
        self.fixture=DynamicPositionAllocationTests();self.fixture.setUp();f=self.fixture
        self.evidence=FullExitStressEvidence('005930',20,1000,120,20000,350,
            position_action_cost_model_ref(f.fixture.cost.to_dict()),'native-full-exit-fixture',2,
            f.now-timedelta(seconds=1),f.now+timedelta(seconds=15))
        self.a=replace(f.a,conservative_exit_capacity_won=20000,stress_loss_bps=350,full_exit_evidence=self.evidence)
        f.policy=replace(f.policy,allocation_exit_horizon_seconds=120)

    def test_whole_goal_is_capped_by_tested_exit_size_and_original_ttl(self):
        from datetime import datetime
        from unittest.mock import patch
        with patch('sqlite3.connect',side_effect=AssertionError('exit-bound allocation DB read')):
            result=self.fixture.run_allocation(candidates=(self.a,))
        row=result['allocations'][0]
        self.assertEqual(20000,row['target_notional_won']);self.assertEqual(20,row['new_buy_quantity'])
        self.assertFalse(row['full_exit_evidence']['liquidation_guaranteed'])
        self.assertEqual(self.evidence.valid_until,datetime.fromisoformat(result['valid_until']))

    def test_existing_plus_pending_buy_exceeding_tested_size_is_blocked(self):
        f=self.fixture
        observation=replace(f.observation,account=replace(f.observation.account,positions={'005930':15}),
            open_orders=(f.fixture.record('BUY',quantity=10,filled=0,state='ACCEPTED'),))
        result=f.run_allocation(candidates=(self.a,),observation=observation)
        self.assertEqual('BLOCKED',result['status']);self.assertIn('full_exit_whole_position_or_model_binding_mismatch:005930',result['reasons'])
        sell=replace(observation,account=replace(observation.account,positions={'005930':21}),
            open_orders=(f.fixture.record('SELL',quantity=10,filled=0,state='ACCEPTED'),))
        self.assertEqual('BLOCKED',f.run_allocation(candidates=(self.a,),observation=sell)['status'])

    def test_price_stress_cost_horizon_and_expiry_cannot_reuse_exit_evidence(self):
        f=self.fixture
        for candidate in (replace(self.a,reference_price_won=1001),replace(self.a,stress_loss_bps=100),
                replace(self.a,conservative_exit_capacity_won=100000),
                replace(self.a,full_exit_evidence=replace(self.evidence,cost_model_ref='different-cost')),
                replace(self.a,full_exit_evidence=replace(self.evidence,valid_until=f.now))):
            self.assertEqual('BLOCKED',f.run_allocation(candidates=(candidate,))['status'])
        self.assertEqual('BLOCKED',f.run_allocation(candidates=(self.a,),policy=replace(f.policy,allocation_exit_horizon_seconds=60))['status'])
        protected=f.run_allocation(candidates=(replace(self.a,full_exit_evidence=replace(self.evidence,valid_until=f.now)),),risk_disposition='EXIT_REQUIRED')
        self.assertTrue(protected['protection_required']);self.assertFalse(protected['order_authorized'])

    def test_exact_document_round_trip_preserves_optional_exit_evidence(self):
        from kiwoom_monitor.application.position_target import evaluate_dynamic_position_allocation_document,_json_value
        f=self.fixture
        document={'schema':'dynamic-position-allocation-shadow/v1','policy':asdict(f.policy),'candidates':[asdict(self.a)],
            'observation':asdict(f.observation),'decision_time':f.now,'cost_model':f.fixture.cost.to_dict(),
            'risk_disposition':'ALLOW','risk_input_ref':'native-risk-cut'}
        self.assertEqual(f.run_allocation(candidates=(self.a,)),evaluate_dynamic_position_allocation_document(json.loads(json.dumps(document,default=_json_value))))


class NativePositionStudyCaseTests(unittest.TestCase):
    def setUp(self):
        from datetime import datetime,timedelta,timezone
        from tests.unit.test_research_development_partitions import DevelopmentPartitionTests,_execution,_strategy,PROFILE
        from kiwoom_monitor.application.research_splits import ResearchEvaluationSpec,ResearchFoldSpec
        from kiwoom_monitor.application.position_target import (
            PositionTargetSizingPolicy,PositionTargetShadowRequest,PositionActionStudyPolicy,PositionActionStudyCase)
        self.helper=DevelopmentPartitionTests();self.helper.setUp();self.addCleanup(self.helper.doCleanups)
        self.helper.evaluation=ResearchEvaluationSpec('chronological_holdout/v1',
            (ResearchFoldSpec('train','TRAIN',self.helper.at(2),self.helper.at(20)),),120,180,0,1,1)
        self.dataset=self.helper.prepare();self.evaluation=self.helper.partition.evaluation_for(self.helper.evaluation)
        self.execution=replace(_execution(),cost_model=replace(_execution().cost_model,valid_to='2030-01-01T00:00:00+00:00'))
        self.strategy=replace(_strategy(),max_hold_minutes=10)
        self.profile=PROFILE
        at=datetime.fromisoformat(self.helper.at(5))+timedelta(seconds=2)
        policy=PositionTargetSizingPolicy('bounded-notional-sizing/v1',20_000,30_000,30,1_000,at,at+timedelta(seconds=30))
        request=PositionTargetShadowRequest('research-single-position','mock','KRX','005930','ADD',None,
            1020,at,at,'dynamic-position-allocation-shadow/v1','historical-goal','native-account','native-orders',30,
            True,True,self.execution.cost_model,policy)
        self.study=PositionActionStudyPolicy('position-action-study/v2',request.policy_version,policy.version,
            policy.target_notional_won,policy.max_position_notional_won,policy.max_position_quantity,policy.cash_buffer_won,
            ('ADD','HOLD'),180,30,30,'TRAIN')
        self.case=PositionActionStudyCase('frozen-case-a','ADD',request)
        self.writer=ResearchRepository(self.helper.root/'study.sqlite3')
        self.cut=datetime.now(timezone.utc)+timedelta(minutes=5)

    def identity(self,study=None,case=None,**kwargs):
        from scripts.run_research import research_run_identity
        return research_run_identity(self.dataset,self.strategy,self.execution,self.evaluation,
            session_profile=self.profile,execution_scope='independent_development_validation/v1',
            position_action_study=study or self.study,position_study_case=case or self.case,**kwargs)

    def run_case(self,branch='ADD'):
        from scripts.run_research import execute_research
        case=replace(self.case,branch_action=branch)
        run_id,spec=self.identity(case=case)
        self.writer.start_run(run_id,spec,self.dataset.manifest,claim_independent=True)
        result=execute_research(self.dataset,self.writer,self.helper.root/'runs',self.strategy,self.execution,
            self.evaluation,session_profile=self.profile,execution_scope='independent_development_validation/v1',
            position_action_study=self.study,position_study_case=case)
        self.assertEqual('ELIGIBLE',result.report_status)
        events=self.writer.load_execution_events(run_id)
        proof=next(e for e in events if e['event_type']=='POSITION_STUDY_CASE')
        start=next(e for e in events if e['event_type']=='MARK' and e['occurred_at']==self.helper.at(5))
        end=next(e for e in events if e['event_type']=='MARK' and e['occurred_at']==self.helper.at(8))
        return {'run_id':run_id,'start_event_id':start['event_id'],'end_event_id':end['event_id'],
            'action':branch,'case_ref':case.case_ref},proof,spec

    def read(self,selection,loader=None,cut=None):
        from kiwoom_monitor.application.research_evaluation import read_position_action_study_case
        reader=ResearchRepository(self.writer.path,read_only=True)
        return read_position_action_study_case(loader or reader.load_position_action_evidence,selection,
            decision_time=cut or self.cut,population_ref='frozen-study-cohort',development_role='TRAIN')

    def test_real_native_run_preserves_expired_original_goal_and_native_outcome_identity(self):
        from kiwoom_monitor.application.research_evaluation import position_action_outcome_from_events
        from datetime import datetime
        selection,proof,spec=self.run_case()
        receipt=self.read(selection)
        self.assertEqual('NATIVE_CASE_PROOF',receipt['quality']['status'])
        self.assertTrue(receipt['completion_after_goal_expiry'])
        self.assertLess(datetime.fromisoformat(receipt['original_goal_valid_until']),datetime.fromisoformat(receipt['outcome']['completed_at']))
        self.assertEqual('EV_UNAVAILABLE',receipt['r4_status']);self.assertFalse(receipt['order_authorized'])
        reader=ResearchRepository(self.writer.path,read_only=True)
        context,events=reader.load_position_action_evidence(selection['run_id'],(selection['end_event_id'],),decision_time=self.cut)
        original=position_action_outcome_from_events(context,[e for e in events if e['event_type']!='POSITION_STUDY_CASE'],
            **{k:selection[k] for k in ('start_event_id','end_event_id','action','case_ref')},
            population_ref='frozen-study-cohort',environment='mock',horizon_seconds=180,symbol='005930')
        self.assertEqual(original.input_ref,receipt['outcome']['input_ref'])
        self.assertEqual(original.policy_version,receipt['outcome']['policy_version'])
        self.assertEqual(spec['position_action_study']['study_policy_ref'],receipt['study_policy_ref'])

    def test_same_semantics_new_valid_cut_keeps_study_ref_but_changes_native_run_and_full_decision(self):
        from datetime import timedelta
        from kiwoom_monitor.application.position_target import evaluate_position_target_shadow
        from kiwoom_monitor.domain.order_contract import AccountSnapshot
        first,spec=self.identity()
        request=self.case.request
        later=replace(request,decision_time=request.decision_time+timedelta(seconds=1),
            target_policy=replace(request.target_policy,available_at=request.target_policy.available_at+timedelta(seconds=1),
                valid_until=request.target_policy.valid_until+timedelta(seconds=1)))
        second,changed=self.identity(case=replace(self.case,request=later))
        self.assertNotEqual(first,second)
        self.assertEqual(spec['position_action_study']['study_policy_ref'],changed['position_action_study']['study_policy_ref'])
        account=AccountSnapshot('research-single-position',90_000,0,{'005930':10},request.decision_time)
        a=evaluate_position_target_shadow(request,account,());b=evaluate_position_target_shadow(later,account,())
        self.assertEqual(a['target_quantity'],b['target_quantity']);self.assertNotEqual(a['target_policy_version'],b['target_policy_version'])
        self.assertNotEqual(a['decision_id'],b['decision_id'])

    def test_changed_sizing_and_continuation_are_distinct_studies(self):
        from scripts.run_research import research_run_identity
        _,spec=self.identity()
        policy=replace(self.case.request.target_policy,target_notional_won=19_000)
        _,other=self.identity(study=replace(self.study,target_notional_won=19_000),
            case=replace(self.case,request=replace(self.case.request,target_policy=policy)))
        self.assertNotEqual(spec['position_action_study']['study_policy_ref'],other['position_action_study']['study_policy_ref'])
        _,other=research_run_identity(self.dataset,replace(self.strategy,max_hold_minutes=11),self.execution,self.evaluation,
            session_profile=self.profile,execution_scope='independent_development_validation/v1',
            position_action_study=self.study,position_study_case=self.case)
        self.assertNotEqual(spec['position_action_study']['study_policy_ref'],other['position_action_study']['study_policy_ref'])

    def test_invalid_original_lease_role_and_mixed_v1_plan_are_rejected_before_run(self):
        from datetime import timedelta
        for request in (replace(self.case.request,decision_time=self.case.request.target_policy.valid_until),
            replace(self.case.request,target_policy=replace(self.case.request.target_policy,
                valid_until=self.case.request.target_policy.valid_until+timedelta(seconds=1)))):
            with self.assertRaises(ValueError):self.identity(case=replace(self.case,request=request))
        with self.assertRaises(ValueError):self.identity(study=replace(self.study,development_role='VALIDATION'))
        with self.assertRaises(ValueError):self.identity(position_target_plan=(self.case.request,))

    def test_independent_hold_records_goal_and_actual_state_without_target_submission(self):
        selection,proof,_=self.run_case('HOLD');receipt=self.read(selection)
        self.assertEqual('HOLD',receipt['outcome']['action']);self.assertEqual(10,receipt['outcome']['target_quantity'])
        self.assertEqual(19,receipt['original_goal']['target_quantity'])
        events=self.writer.load_execution_events(selection['run_id'])
        self.assertFalse(any(e['event_type']=='ORDER_SUBMITTED' and e['reason'].startswith('{') for e in events))
        self.assertEqual(10,receipt['decision_proof']['portfolio']['position']['quantity'])

    def test_readonly_single_borrow_has_no_commit_and_does_not_refresh_source_time(self):
        from unittest.mock import patch
        selection,_,_=self.run_case();reader=ResearchRepository(self.writer.path,read_only=True)
        sql=[];connect=reader._connect;before=self.writer.path.read_bytes()
        def observed():
            connection=connect();connection.set_trace_callback(sql.append);return connection
        with patch.object(reader,'_connect',side_effect=observed),patch.object(reader,'load_position_action_evidence',wraps=reader.load_position_action_evidence) as borrow:
            first=self.read(selection,loader=borrow);second=self.read(selection,loader=borrow)
        self.assertEqual(2,borrow.call_count)
        self.assertEqual(8,sum(s.lstrip().upper().startswith('SELECT') for s in sql))
        self.assertFalse(any(s.lstrip().upper().startswith('COMMIT') for s in sql))
        self.assertEqual(before,self.writer.path.read_bytes());self.assertEqual(first,second)

    def test_case_identity_source_goal_and_portfolio_tampering_are_rejected(self):
        from copy import deepcopy
        selection,_,_=self.run_case();reader=ResearchRepository(self.writer.path,read_only=True)
        context,events=reader.load_position_action_evidence(selection['run_id'],(selection['end_event_id'],),decision_time=self.cut)
        for field,value in (('case_ref','other-case'),('action','HOLD')):
            with self.assertRaises(ValueError):self.read({**selection,field:value})
        def altered(change):
            values=deepcopy(events);event=next(e for e in values if e['event_type']=='POSITION_STUDY_CASE')
            proof=json.loads(event['reason']);change(proof);event['reason']=json.dumps(proof)
            return lambda *args,**kwargs:(context,values)
        for change in (lambda p:p['goal'].update(target_quantity=999),lambda p:p['portfolio']['position'].update(quantity=11),
            lambda p:p['source_bar'].update(close=999)):
            with self.assertRaises(ValueError):self.read(selection,loader=altered(change))

    def test_v1_study_missing_proof_and_future_completion_stay_unavailable(self):
        from datetime import datetime
        selection,_,_=self.run_case();reader=ResearchRepository(self.writer.path,read_only=True)
        context,events=reader.load_position_action_evidence(selection['run_id'],(selection['end_event_id'],),decision_time=self.cut)
        old={k:v for k,v in context.items() if not k.startswith('position_study_')}
        with self.assertRaises(ValueError):self.read(selection,loader=lambda *args,**kwargs:(old,events))
        with self.assertRaises(ValueError):self.read(selection,loader=lambda *args,**kwargs:(context,[e for e in events if e['event_type']!='POSITION_STUDY_CASE']))
        from kiwoom_monitor.application.research_evaluation import PositionOutcomeUnavailable
        with self.assertRaises(PositionOutcomeUnavailable):self.read(selection,cut=datetime.fromisoformat(self.helper.at(6)))

    def test_duplicate_or_late_case_proof_and_source_budget_are_rejected(self):
        selection,_,_=self.run_case();reader=ResearchRepository(self.writer.path,read_only=True)
        context,events=reader.load_position_action_evidence(selection['run_id'],(selection['end_event_id'],),decision_time=self.cut)
        proof=next(e for e in events if e['event_type']=='POSITION_STUDY_CASE')
        for values in (list(events)+[proof],[e for e in events if e is not proof]+[proof],events*10000):
            with self.assertRaises(ValueError):self.read(selection,loader=lambda *args,**kwargs:(context,values))

    def test_declared_study_hash_and_original_case_cannot_be_modified(self):
        from copy import deepcopy
        from kiwoom_monitor.application.research_evaluation import position_action_source_context
        selection,_,_=self.run_case()
        record=self.writer.load_run(selection['run_id']);record['report']=self.writer.load_research_report(selection['run_id'])
        # Each altered spec gets a corresponding run digest, so the native study
        # comparison itself must reject the inconsistent declaration.
        from scripts.run_research import _content_id
        for change in (lambda p:p.update(study_policy_ref='position-action-study:'+'0'*64),
            lambda p:p['case']['request'].update(policy_version='different-source-policy')):
            raw=deepcopy(record);change(raw['spec']['position_action_study'])
            raw['run_id']=_content_id('run',{'dataset_id':raw['input_manifest']['dataset_id'],
                'revision_ids_hash':raw['input_manifest']['revision_ids_hash'],'spec':raw['spec']})
            raw['report']['run_id']=raw['run_id']
            with self.assertRaises(ValueError):position_action_source_context(raw,self.cut)


if __name__ == "__main__":
    unittest.main()
