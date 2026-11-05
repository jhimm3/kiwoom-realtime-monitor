from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta

from kiwoom_monitor.application.micro_execution_evidence import replay_micro_execution


_START = datetime.fromisoformat("2026-09-11T09:10:00+09:00")


def _event(kind, ref, second, *, sequence=None, available_second=None, **data):
    return {
        "kind": kind, "ref": ref, "clock_id": "fixture-clock", "data": data,
        "observed_at": (_START + timedelta(seconds=second)).isoformat(),
        "available_at": (_START + timedelta(seconds=second if available_second is None else available_second)).isoformat(),
        **({"stream_id": "joined-market", "generation": 4, "sequence": sequence} if sequence is not None else {}),
    }


def _scenario(events):
    return {"version": "micro-synthetic-evidence/v1", "symbol": "005930", "venue": "KRX",
            "clock_id": "fixture-clock", "events": events}


def _policy(variant="VI_GAP_UP"):
    policy = {"version": "micro-policy/v1", "variant": variant, "quantity": 5,
              "min_reaction_bps": 50, "max_spread_bps": 125, "stop_loss_bps": 100, "take_profit_bps": 200}
    if variant == "BOOK_SCALP":
        policy["min_bid_ask_ratio_bps"] = 20_000
    return policy


def _entry_market(*, direction="UP", release_price=101, trade_price=103):
    return [
        _event("VI_ACTIVATED", "activation", 0, sequence=1, episode_id="vi", trigger_price_won=100, direction=direction),
        _event("VI_RELEASED", "release", 1, sequence=2, episode_id="vi", release_price_won=release_price),
        _event("BOOK", "book", 2, sequence=3, bids=[[101, 10], [100, 30]], asks=[[102, 4], [103, 4]], tradable=True),
        _event("TRADE", "trade", 3, sequence=4, price_won=trade_price, quantity=500),
    ]


class MicroExecutionEvidenceTests(unittest.TestCase):
    def test_variant_policies_use_distinct_evidence_and_emit_no_guessed_fill(self):
        cases = (
            ("VI_GAP_UP", _entry_market(), ["activation", "release", "book", "trade"]),
            ("VI_FLAT_DOWN_RISE", _entry_market(release_price=100), ["activation", "release", "book", "trade"]),
            ("VI_DOWNSIDE_REBOUND", _entry_market(direction="DOWN", release_price=98), ["activation", "release", "book", "trade"]),
            ("BOOK_SCALP", _entry_market()[2:], ["book", "trade"]),
        )
        for variant, evidence, refs in cases:
            with self.subTest(variant=variant):
                result = replay_micro_execution(_scenario(evidence), _policy(variant))
                self.assertEqual("UNKNOWN", result["status"])
                self.assertEqual("missing_terminal_receipt", result["reason"])
                self.assertEqual("synthetic", result["source_kind"])
                self.assertEqual([(variant, 5, refs)], [
                    (row["reason"], row["quantity"], row["evidence_refs"]) for row in result["decisions"]
                ])
                self.assertEqual([], result["executions"])
                self.assertEqual(0, result["position_quantity"])

    def test_known_non_signals_remain_no_trade(self):
        for events, policy in (
            (_entry_market(release_price=100), _policy()),
            (_entry_market(direction="DOWN"), _policy()),
            (_entry_market(trade_price=101), _policy()),
            (_entry_market(), _policy() | {"max_spread_bps": 1}),
            (_entry_market()[2:], _policy("BOOK_SCALP") | {"min_bid_ask_ratio_bps": 30_000}),
        ):
            with self.subTest(policy=policy, events=events):
                result = replay_micro_execution(_scenario(events), policy)
                self.assertEqual(("NO_TRADE", [], []), (result["status"], result["decisions"], result["executions"]))

    def test_unproven_scope_sequence_and_generation_do_not_become_price_signals(self):
        for changes, reason in (
            ({"stream_id": "independent-book"}, "unconfirmed_market_order"),
            ({"generation": 5}, "unconfirmed_market_order"),
            ({"sequence": 1}, "unconfirmed_market_order"),
            ({"sequence": 5}, "unconfirmed_market_order"),
            ({"kind": "GAP"}, "unconfirmed_market_order"),
            ({"venue": "NXT"}, "unconfirmed_market_scope"),
            ({"clock_id": "other-clock"}, "unconfirmed_market_scope"),
        ):
            with self.subTest(changes=changes):
                events = _entry_market()
                events[2] |= changes
                result = replay_micro_execution(_scenario(events), _policy())
                self.assertEqual(("UNKNOWN", reason, []), (result["status"], result["reason"], result["decisions"]))

    def test_missing_or_untradable_book_and_unconfirmed_release_stay_unknown(self):
        for index, data, reason in (
            (1, {"episode_id": "vi"}, "unconfirmed_vi_episode"),
            (1, {"episode_id": "unmatched", "release_price_won": 101}, "unconfirmed_vi_episode"),
            (2, {"bids": [[101, 10]], "asks": [], "tradable": True}, "unconfirmed_tradable_book"),
            (2, {"bids": [[101, 10], [103, 12]], "asks": [[102, 4]], "tradable": True}, "unconfirmed_tradable_book"),
            (2, {"bids": [[101, 10]], "asks": [[102, 4]], "tradable": False}, "unconfirmed_tradable_book"),
        ):
            with self.subTest(data=data):
                events = _entry_market()
                events[index]["data"] = data
                result = replay_micro_execution(_scenario(events), _policy())
                self.assertEqual(("UNKNOWN", reason, []), (result["status"], result["reason"], result["decisions"]))

    def test_explicit_same_second_order_and_delayed_evidence_preserve_the_decision_cut(self):
        events = _entry_market()
        events[1] = _event("VI_RELEASED", "release", 1, available_second=4, sequence=2,
                           episode_id="vi", release_price_won=101)
        events[2] = _event("BOOK", "book", 1, available_second=4, sequence=3,
                           bids=[[101, 10]], asks=[[102, 4]], tradable=True)
        events[3] = _event("TRADE", "trade", 1, available_second=5, sequence=4, price_won=103, quantity=500)
        for count in range(4):
            self.assertEqual([], replay_micro_execution(_scenario(events[:count]), _policy())["decisions"])
        result = replay_micro_execution(_scenario(events), _policy())
        self.assertEqual("2026-09-11T09:10:05+09:00", result["decisions"][0]["available_at"])
        self.assertEqual("2026-09-11T09:10:01+09:00", result["decisions"][0]["observed_at"])

    def test_partial_fill_cancel_and_protection_close_exact_detailed_quantity(self):
        events = _entry_market() + [
            _event("ORDER_ARRIVED", "arrive-entry", 4, order_id="entry"),
            _event("FILL", "fill-entry", 5, order_id="entry", price_won=104, quantity=2),
            _event("CANCELLED", "cancel-entry", 6, order_id="entry", filled_quantity=2),
            _event("TRADE", "stop", 7, sequence=5, price_won=100, quantity=100),
            _event("ORDER_ARRIVED", "arrive-exit", 8, order_id="exit"),
            _event("FILL", "fill-exit", 9, order_id="exit", price_won=99, quantity=2),
        ]
        open_result = replay_micro_execution(_scenario(events[:7]), _policy())
        self.assertEqual(("OPEN", 2, 2, None), (open_result["status"], open_result["position_quantity"],
                                                open_result["protected_quantity"], open_result["pending_order"]))
        result = replay_micro_execution(_scenario(events), _policy())
        self.assertEqual(("COMPLETED", 0, 0), (result["status"], result["position_quantity"], result["protected_quantity"]))
        self.assertEqual([("ENTRY", 5, "VI_GAP_UP"), ("EXIT", 2, "protective_stop")], [
            (row["action"], row["quantity"], row["reason"]) for row in result["decisions"]
        ])
        self.assertEqual([("fill-entry", 104, 2), ("fill-exit", 99, 2)], [
            (row["ref"], row["price_won"], row["quantity"]) for row in result["executions"] if row["kind"] == "FILL"
        ])
        self.assertEqual(result, replay_micro_execution(json.loads(json.dumps(_scenario(events))), _policy()))

    def test_cancel_aggregate_waits_for_late_detailed_fill_and_deduplicates_it(self):
        events = _entry_market() + [
            _event("ORDER_ARRIVED", "arrive", 4, order_id="entry"),
            _event("CANCELLED", "cancel", 6, order_id="entry", filled_quantity=2),
        ]
        awaiting_details = replay_micro_execution(_scenario(events), _policy())
        self.assertEqual(("UNKNOWN", 0, 0), (awaiting_details["status"], awaiting_details["position_quantity"],
                                             awaiting_details["protected_quantity"]))
        self.assertEqual({"order_id": "entry", "requested_quantity": 5, "detailed_filled_quantity": 0,
                          "cancel_reported_filled_quantity": 2}, awaiting_details["pending_order"])
        late_fill = _event("FILL", "detail", 5, available_second=8, order_id="entry", price_won=104, quantity=2)
        events += [late_fill, late_fill.copy()]
        reconciled = replay_micro_execution(_scenario(events), _policy())
        self.assertEqual(("OPEN", 2, None), (reconciled["status"], reconciled["protected_quantity"], reconciled["pending_order"]))
        self.assertEqual(1, len([row for row in reconciled["executions"] if row["kind"] == "FILL"]))

    def test_stop_during_pending_entry_uses_final_detailed_quantity_after_cancel_reconciliation(self):
        events = _entry_market() + [
            _event("ORDER_ARRIVED", "arrive", 4, order_id="entry"),
            _event("FILL", "first-detail", 5, order_id="entry", price_won=104, quantity=1),
            _event("TRADE", "crossing", 6, sequence=5, price_won=100, quantity=100),
            _event("CANCELLED", "cancel", 8, order_id="entry", filled_quantity=2),
            _event("FILL", "late-detail", 7, available_second=9, order_id="entry", price_won=104, quantity=1),
        ]
        waiting = replay_micro_execution(_scenario(events[:-1]), _policy())
        self.assertEqual(("OPEN", "protective_waiting_entry_reconciliation", 1, ["ENTRY"]), (
            waiting["status"], waiting["reason"], waiting["protected_quantity"],
            [decision["action"] for decision in waiting["decisions"]],
        ))
        result = replay_micro_execution(_scenario(events), _policy())
        exit_decision = result["decisions"][-1]
        self.assertEqual(("EXIT", 2, ["crossing", "late-detail"], "2026-09-11T09:10:09+09:00"), (
            exit_decision["action"], exit_decision["quantity"], exit_decision["evidence_refs"], exit_decision["available_at"],
        ))

    def test_arrival_is_required_and_market_gap_cannot_erase_actual_fill(self):
        market = _entry_market()
        missing_arrival = replay_micro_execution(_scenario(market + [
            _event("FILL", "no-arrival", 5, order_id="entry", price_won=104, quantity=2),
        ]), _policy())
        self.assertEqual(("UNKNOWN", "unconfirmed_broker_receipt", 0),
                         (missing_arrival["status"], missing_arrival["reason"], missing_arrival["protected_quantity"]))
        result = replay_micro_execution(_scenario(market + [
            _event("ORDER_ARRIVED", "arrive", 4, order_id="entry"),
            _event("GAP", "lost-book", 5, sequence=5),
            _event("FILL", "detail", 6, order_id="entry", price_won=104, quantity=2),
        ]), _policy())
        self.assertEqual(("OPEN", "unconfirmed_market_order", 2),
                         (result["status"], result["reason"], result["protected_quantity"]))

    def test_partial_exit_cancel_keeps_residual_position_open(self):
        events = _entry_market() + [
            _event("ORDER_ARRIVED", "entry-arrival", 4, order_id="entry"),
            _event("FILL", "entry-fill", 5, order_id="entry", price_won=104, quantity=5),
            _event("TRADE", "target", 6, sequence=5, price_won=110, quantity=100),
            _event("ORDER_ARRIVED", "exit-arrival", 7, order_id="exit"),
            _event("FILL", "exit-fill", 8, order_id="exit", price_won=109, quantity=4),
            _event("CANCELLED", "exit-cancel", 9, order_id="exit", filled_quantity=4),
        ]
        result = replay_micro_execution(_scenario(events), _policy())
        self.assertEqual("take_profit", result["decisions"][-1]["reason"])
        self.assertEqual(("OPEN", 1, 1, None), (
            result["status"], result["position_quantity"], result["protected_quantity"], result["pending_order"],
        ))

    def test_unversioned_policy_conflicting_refs_and_invalid_clocks_are_invalid_requests(self):
        events = _entry_market()
        with self.assertRaisesRegex(ValueError, "version"):
            replay_micro_execution(_scenario(events), _policy() | {"version": "unversioned"})
        with self.assertRaisesRegex(ValueError, "explicit"):
            replay_micro_execution(_scenario(events), {key: value for key, value in _policy().items() if key != "quantity"})
        with self.assertRaisesRegex(ValueError, "conflicting"):
            replay_micro_execution(_scenario([*events, events[-1] | {"data": {}}]), _policy())
        with self.assertRaisesRegex(ValueError, "recorded availability order"):
            replay_micro_execution(_scenario(list(reversed(events))), _policy())
        with self.assertRaisesRegex(ValueError, "offset-bearing"):
            replay_micro_execution(_scenario([events[0] | {"observed_at": "2026-09-11T09:10:00"}]), _policy())
