from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import unittest

from kiwoom_monitor.application.account_risk_research import replay_account_risk_experiment
from kiwoom_monitor.domain.time_basis import KST


def _buy(order_id, plan_id, quantity, *, tranche=0, fee_budget_won=0):
    return {
        "kind": "PROPOSE", "order_id": order_id, "plan_id": plan_id, "symbol": "005930", "side": "BUY",
        "quantity": quantity, "tranche": tranche, "limit_price_won": 10, "fee_budget_won": fee_budget_won,
    }


def _sell(order_id, plan_id, quantity):
    return {
        "kind": "PROPOSE", "order_id": order_id, "plan_id": plan_id, "symbol": "005930", "side": "SELL",
        "quantity": quantity, "limit_price_won": 11, "fee_budget_won": 0,
    }


def _fill(order_id, execution_id, quantity, price_won, *, fee_won=0):
    return {
        "kind": "FILL", "order_id": order_id, "execution_id": execution_id,
        "quantity": quantity, "price_won": price_won, "fee_won": fee_won,
    }


def _scenario(*events):
    return {
        "version": "account-risk-scenario/v1", "initial_cash_won": 100, "plan_budget_won": 100,
        "events": [dict(event, sequence=index) for index, event in enumerate(events, 1)],
    }


def _policy(*tranche_bps):
    return {"version": "account-risk-allocation/v1", "tranche_bps": list(tranche_bps)}


def _at(seconds):
    return (datetime(2026, 9, 21, 10, tzinfo=KST) + timedelta(seconds=seconds)).isoformat()


def _mark(mark_id, price_won, observed_second):
    return {
        "kind": "MARK", "mark_id": mark_id, "symbol": "005930",
        "price_won": price_won, "observed_at": _at(observed_second),
    }


def _timed(*events):
    return _scenario(*(dict(event, available_at=_at(second)) for second, event in enumerate(events)))


class AccountRiskResearchTests(unittest.TestCase):
    def test_fixed_and_split_use_the_same_cash_and_different_explicit_fills(self):
        scenario = _scenario(
            _buy("a0", "a", 6), _buy("a1", "a", 4, tranche=1), _buy("b0", "b", 4),
            _fill("a0", "first", 3, 9),
            {"kind": "CANCEL_REQUESTED", "order_id": "a0"},
            {"kind": "CANCEL_CONFIRMED", "order_id": "a0", "filled_quantity": 3},
            _fill("a1", "add", 4, 10), _fill("b0", "other-plan", 4, 10),
            _sell("a-close", "a", 3), _fill("a-close", "exit", 3, 11),
        )
        original = deepcopy(scenario)
        fixed = replay_account_risk_experiment(scenario, _policy(10_000))
        split = replay_account_risk_experiment(scenario, _policy(6_000, 4_000))

        self.assertEqual(original, scenario)
        self.assertNotIn("valuation", fixed)
        self.assertNotIn("valuation", fixed["events"][0])
        self.assertEqual("tranche_disabled", fixed["orders"][1]["rejection_reason"])
        self.assertEqual("account_cash", split["orders"][2]["rejection_reason"])
        self.assertEqual("NOT_APPLIED", fixed["events"][6]["status"])
        self.assertEqual("NOT_APPLIED", split["events"][7]["status"])
        self.assertEqual(
            [{"plan_id": "b", "symbol": "005930", "quantity": 4, "cost_basis_won": 40}],
            fixed["positions"],
        )
        self.assertEqual(
            [{"plan_id": "a", "symbol": "005930", "quantity": 4, "cost_basis_won": 40}],
            split["positions"],
        )
        for result in (fixed, split):
            self.assertEqual(100, result["peak_reserved_cash_won"])
            self.assertEqual(66, result["cash_won"])
            self.assertEqual(0, result["reserved_cash_won"])
            self.assertEqual(6, result["realized_pnl_won"])
            self.assertEqual("CANCELLED", result["orders"][0]["state"])
            self.assertEqual(3, result["orders"][0]["unfilled_quantity"])

    def test_pending_cancellation_keeps_reservation_until_detailed_fills_agree(self):
        first_fill = _fill("a0", "one", 1, 9, fee_won=1)
        scenario = _scenario(
            _buy("a0", "a", 4, fee_budget_won=2),
            {"kind": "CANCEL_REQUESTED", "order_id": "a0"},
            {"kind": "CANCEL_CONFIRMED", "order_id": "a0", "filled_quantity": 2},
            _buy("b0", "b", 6),
            first_fill,
            {"kind": "CANCEL_CONFIRMED", "order_id": "a0", "filled_quantity": 1},
        )
        unresolved = replay_account_risk_experiment(scenario, _policy(10_000))
        self.assertEqual([42, 42, 42, 42, 31, 31], [event["reserved_cash_won"] for event in unresolved["events"]])
        self.assertEqual("cancel_missing_detailed_fills", unresolved["events"][2]["reason"])
        self.assertEqual("account_cash", unresolved["orders"][1]["rejection_reason"])
        self.assertEqual("cancel_quantity_conflicts_with_order", unresolved["events"][5]["reason"])
        self.assertEqual("CANCEL_PENDING", unresolved["orders"][0]["state"])
        self.assertEqual(59, unresolved["available_cash_won"])

        events = scenario["events"] + [
            dict(_fill("a0", "two", 1, 10, fee_won=1), sequence=7),
            dict(first_fill, sequence=8),
        ]
        resolved = replay_account_risk_experiment(dict(scenario, events=events), _policy(10_000))
        self.assertEqual("CANCELLED", resolved["orders"][0]["state"])
        self.assertEqual(2, resolved["orders"][0]["unfilled_quantity"])
        self.assertEqual("duplicate_fill", resolved["events"][-1]["reason"])
        self.assertEqual(79, resolved["cash_won"])
        self.assertEqual(0, resolved["reserved_cash_won"])
        self.assertEqual(21, resolved["positions"][0]["cost_basis_won"])

    def test_sell_proposals_cannot_share_reserved_or_other_plan_shares(self):
        scenario = _scenario(
            _buy("a0", "a", 2), _fill("a0", "a-buy", 2, 10),
            _buy("b0", "b", 1), _fill("b0", "b-buy", 1, 10),
            _sell("sell-a", "a", 2), _sell("overlapping-a", "a", 1),
            _sell("oversized-b", "b", 2),
            _fill("sell-a", "a-sell-part", 1, 11),
        )
        result = replay_account_risk_experiment(scenario, _policy(10_000))
        self.assertEqual("PARTIALLY_FILLED", result["orders"][2]["state"])
        self.assertEqual("plan_sell_quantity", result["orders"][3]["rejection_reason"])
        self.assertEqual("plan_sell_quantity", result["orders"][4]["rejection_reason"])
        self.assertEqual([1, 1], [position["quantity"] for position in result["positions"]])
        self.assertEqual(1, result["realized_pnl_won"])

    def test_observation_order_conflicting_execution_and_cancellation_checkpoint(self):
        scenario = _scenario(_buy("a0", "a", 2), _fill("a0", "same", 1, 10), _fill("a0", "same", 1, 9))
        with self.assertRaisesRegex(ValueError, "conflicting detail"):
            replay_account_risk_experiment(scenario, _policy(10_000))
        reversed_scenario = dict(scenario, events=list(reversed(scenario["events"])))
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            replay_account_risk_experiment(reversed_scenario, _policy(10_000))

        called = []

        def checkpoint():
            called.append(None)
            if len(called) == 2:
                raise InterruptedError("cancelled")

        with self.assertRaisesRegex(InterruptedError, "cancelled"):
            replay_account_risk_experiment(scenario, _policy(10_000), checkpoint=checkpoint)
        self.assertEqual(2, len(called))

    def test_explicit_marks_value_both_plans_once_and_measure_account_drawdown(self):
        scenario = _timed(
            _buy("a0", "a", 2, fee_budget_won=1), _buy("b0", "b", 1),
            _mark("pre-fill", 12, 1),
            _fill("a0", "a-fill", 2, 10, fee_won=1),
            _fill("b0", "b-fill", 1, 10),
            _mark("peak", 14, 5), _mark("trough", 7, 6),
        )
        result = replay_account_risk_experiment(scenario, _policy(10_000))

        self.assertEqual(31, result["events"][1]["reserved_cash_won"])
        self.assertEqual(
            [100, 100, 100, 103, 105, 111, 90],
            [event["valuation"]["equity_won"] for event in result["events"]],
        )
        self.assertEqual({"005930": "pre-fill"}, result["events"][4]["valuation"]["mark_ids_by_symbol"])
        self.assertEqual([2, 1], [position["quantity"] for position in result["positions"]])
        valuation = result["valuation"]
        self.assertEqual("COMPLETE", valuation["status"])
        self.assertEqual(90, valuation["equity_won"])
        self.assertEqual((21, 1891), (valuation["maximum_drawdown_won"], valuation["maximum_drawdown_bps"]))
        self.assertEqual((7, 0), (valuation["known_cut_count"], valuation["unknown_cut_count"]))

    def test_missing_late_duplicate_and_conflicting_marks_keep_the_known_lower_bound(self):
        repeated = _mark("observed-first", 15, 1)
        scenario = _timed(
            _buy("a0", "a", 2), _fill("a0", "a-fill", 2, 10), repeated,
            _mark("older-arrival", 100, 0), repeated, _mark("latest", 5, 5),
            _mark("same-time-conflict", 6, 5),
        )
        result = replay_account_risk_experiment(scenario, dict(_policy(10_000), mark_max_age_seconds=2))

        self.assertEqual(
            [100, None, 110, 110, None, 90, None],
            [event["valuation"]["equity_won"] for event in result["events"]],
        )
        self.assertEqual(["005930"], result["events"][1]["valuation"]["missing_symbols"])
        self.assertEqual("older_mark_observation", result["events"][3]["reason"])
        self.assertEqual("duplicate_mark", result["events"][4]["reason"])
        self.assertEqual(["005930"], result["events"][4]["valuation"]["stale_symbols"])
        self.assertEqual(["005930"], result["events"][-1]["valuation"]["conflicting_symbols"])
        valuation = result["valuation"]
        self.assertEqual("PARTIAL", valuation["status"])
        self.assertIsNone(valuation["maximum_drawdown_won"])
        self.assertIsNone(valuation["maximum_drawdown_bps"])
        self.assertEqual((20, 1818), (
            valuation["known_drawdown_lower_bound_won"], valuation["known_drawdown_lower_bound_bps"],
        ))
        self.assertEqual((4, 3), (valuation["known_cut_count"], valuation["unknown_cut_count"]))

    def test_marked_account_remains_unknown_while_cancel_reports_missing_fills(self):
        scenario = _timed(
            _buy("a0", "a", 4), _mark("a-market", 10, 1),
            {"kind": "CANCEL_CONFIRMED", "order_id": "a0", "filled_quantity": 2},
            _fill("a0", "reported-detail", 2, 10),
        )
        result = replay_account_risk_experiment(scenario, _policy(10_000))

        unresolved = result["events"][2]["valuation"]
        self.assertEqual(["a0"], unresolved["unresolved_execution_order_ids"])
        self.assertIsNone(unresolved["equity_won"])
        self.assertEqual(40, result["events"][2]["reserved_cash_won"])
        self.assertEqual(100, result["valuation"]["equity_won"])
        self.assertEqual("PARTIAL", result["valuation"]["status"])
        self.assertEqual([], result["events"][3]["valuation"]["unresolved_execution_order_ids"])

    def test_timed_valuation_validates_availability_and_immutable_mark_identity(self):
        policy = _policy(10_000)
        with self.assertRaisesRegex(ValueError, "later than available_at"):
            replay_account_risk_experiment(_timed(_mark("future", 10, 1)), policy)
        inconsistent = _timed(_mark("same-id", 10, 0), _mark("same-id", 11, 0))
        with self.assertRaisesRegex(ValueError, "conflicting evidence for the same mark_id"):
            replay_account_risk_experiment(inconsistent, policy)
        out_of_order = _timed(_buy("a0", "a", 1), _mark("earlier", 10, 0))
        out_of_order["events"][0]["available_at"] = _at(2)
        with self.assertRaisesRegex(ValueError, "nondecreasing"):
            replay_account_risk_experiment(out_of_order, policy)

        missing = _timed(_buy("a0", "a", 1), _fill("a0", "a-fill", 1, 10))
        result = replay_account_risk_experiment(missing, dict(policy, mark_max_age_seconds=5))
        self.assertEqual(["005930"], result["events"][-1]["valuation"]["missing_symbols"])
        self.assertEqual("PARTIAL", result["valuation"]["status"])
