from __future__ import annotations

import json
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

from kiwoom_monitor.application.micro_execution_evidence import assess_micro_shadow_inputs, replay_micro_execution
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.market_events import MarketEventService
from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import parse_vi_events


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


def _frozen_scenario(events, **changes):
    return {**_scenario(events), "version": "micro-frozen-evidence/v1",
            "provenance": {"source_kind": "historical_reconstruction", "source_ref": "frozen-case-17",
                           "source_version": "joined-reconstruction/v2"}, **changes}


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
    def test_frozen_research_consumes_recorded_inputs_without_native_confirmation(self):
        events = _entry_market() + [
            _event("ORDER_ARRIVED", "arrive", 4, order_id="entry"),
            _event("FILL", "detail", 5, order_id="entry", price_won=104, quantity=2),
            _event("CANCELLED", "cancel", 6, order_id="entry", filled_quantity=2),
        ]
        events[1]["provenance"] = {"source_ref": "release-detail-17", "source_version": "frozen/v2"}
        result = replay_micro_execution(_frozen_scenario(events), _policy())
        self.assertEqual(("OPEN", 2, 2), (result["status"], result["position_quantity"], result["protected_quantity"]))
        inputs = result["input_assessment"]
        self.assertEqual(([], True, "RECORDED", 101, False), (inputs["missing_inputs"], inputs["tradable"],
            inputs["cross_stream_order"], inputs["vi_release_execution_price"], inputs["native_confirmation"]))
        self.assertEqual("historical_reconstruction", result["evidence_provenance"]["source_kind"])
        self.assertEqual(events[1]["provenance"], result["evidence"][1]["provenance"])
        self.assertFalse(result["order_authorized"])
        self.assertEqual(result, replay_micro_execution(json.loads(json.dumps(_frozen_scenario(events))), _policy()))

    def test_frozen_cut_missing_inputs_and_unproven_order_do_not_invent_evidence(self):
        cut = (_START + timedelta(seconds=1)).isoformat()
        events = _entry_market()
        result = replay_micro_execution(_frozen_scenario(events, decision_at=cut), _policy())
        self.assertEqual([], result["decisions"])
        self.assertEqual(["book", "trade"], result["input_assessment"]["unavailable_event_refs"])
        self.assertIn("instrument_tradability", result["input_assessment"]["missing_inputs"])
        for change in ({"stream_id": "independent-book"}, {"sequence": 8}):
            rows = _entry_market(); rows[2] |= change
            unverified = replay_micro_execution(_frozen_scenario(rows), _policy())
            self.assertEqual(("UNKNOWN", []), (unverified["status"], unverified["decisions"]))
            self.assertIn("cross_stream_order", unverified["input_assessment"]["missing_inputs"])
        events[1]["data"].pop("release_price_won")
        events[2]["data"].pop("tradable")
        missing = replay_micro_execution(_frozen_scenario(events), _policy())
        self.assertEqual(("UNKNOWN", []), (missing["status"], missing["decisions"]))
        self.assertIsNone(missing["input_assessment"]["vi_release_execution_price"])
        self.assertIsNone(missing["input_assessment"]["tradable"])
        with self.assertRaisesRegex(ValueError, "declared research source"):
            replay_micro_execution(_frozen_scenario(events, provenance={"source_kind": "native_confirmed"}), _policy())

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


class MicroNativeShadowInputTests(unittest.TestCase):
    def test_frozen_http_inputs_use_sha_bound_research_without_changing_native_inputs(self):
        import hashlib
        from fastapi import FastAPI, Header, HTTPException
        from fastapi.testclient import TestClient
        from kiwoom_monitor.central_server.research_read_routes import create_research_read_router

        def authorize(authorization: str = Header(default="")):
            if authorization != "Bearer research":
                raise HTTPException(status_code=401)

        with closing(SQLiteQueryStore(Path(":memory:"))) as store:
            store.initialize()
            app = FastAPI()
            app.include_router(create_research_read_router(store, authorize, lambda: None, now_provider=lambda: _START))
            document = {"scenario": _frozen_scenario(_entry_market()), "policy": _policy()}
            body = json.dumps(document).encode()
            digest = hashlib.sha256(body).hexdigest()
            path = "/api/v1/research/micro-inputs?evidence_sha256=" + digest
            headers = {"Authorization": "Bearer research"}
            with TestClient(app) as client:
                self.assertEqual(401, client.post(path, content=body).status_code)
                response = client.post(path, content=body, headers=headers)
                self.assertEqual(200, response.status_code, response.text)
                result = response.json()
                self.assertEqual(([], 101, False, None), (result["input_assessment"]["missing_inputs"],
                    result["input_assessment"]["vi_release_execution_price"], result["order_authorized"], result["candidate"]))
                self.assertEqual(digest, result["evidence_sha256"])
                self.assertEqual(document["scenario"]["events"], result["evidence"])
                self.assertEqual(422, client.post(path, content=body + b" ", headers=headers).status_code)
                native = client.get("/api/v1/research/micro-inputs?symbol=005930&venue=KRX&variant=VI_GAP_UP", headers=headers)
                self.assertEqual(200, native.status_code, native.text)
                self.assertEqual(("UNKNOWN", None), (native.json()["status"], native.json()["candidate"]))
                self.assertIn("native_instrument_tradability", native.json()["missing_inputs"])
                self.assertIn("native_cross_stream_order", native.json()["missing_inputs"])
                self.assertIn("native_vi_release_execution_price", native.json()["missing_inputs"])

    def test_actual_collector_and_native_vi_store_expose_observations_without_a_candidate(self):
        now = [_START]

        def forbidden_token():
            raise AssertionError("MICRO input assessment must not request broker access")

        collector = CentralRealtimeCollector(forbidden_token, "mock", RealtimeHub(), lambda: now[0])
        collector._apply_trade_source_approval({("005930", "KRX"), ("005930", "NXT")}, _START, reset_all=False)
        retention_owner = object()
        collector.set_ram_observation_retention(10, owner=retention_owner)
        now[0] += timedelta(seconds=5)
        collector._publish_parsed({"trnm": "REAL", "data": [
            {"type": "0B", "item": "005930", "values": {"10": "104", "15": "2", "20": "091003"}},
            {"type": "0B", "item": "005930_NX", "values": {"10": "106", "15": "1", "20": "091004"}},
        ]})
        snapshot = collector.ram_observation_snapshot(("005930",), seconds=10)
        cut = now[0]

        with closing(SQLiteQueryStore(Path(":memory:"))) as store:
            store.initialize()
            vi = MarketEventService(None, store, now_provider=lambda: now[0])
            base = {"9001": "A005930", "9068": "1", "1225": "1", "1221": "+100",
                    "1223": "091000", "1224": "091002", "1490": "1", "9081": "KRX"}

            def receipt(values):
                event, = parse_vi_events({"trnm": "REAL", "data": [{"type": "1h", "values": values}]})
                return vi._vi_value(event)

            activation = receipt(base)
            release = receipt(base | {"9068": "2"})
            other_venue = receipt(base | {"9068": "2", "9081": "NXT"})
            unknown_venue = receipt(base | {"9068": "2", "9081": ""})
            rest = vi._backfill_vi_value({"stk_cd": "A005930", "motn_tp": "2", "motn_tm": "091002",
                                          "motn_pric": "100", "stex_tp": "KRX"})
            now[0] += timedelta(seconds=1)
            late = receipt(base | {"9068": "2", "1490": "2"})
            self.assertEqual(6, store.append_vi_events([activation, release, other_venue, unknown_venue, rest, late]))
            history = store.load_market_event_history("vi", code="005930", limit=6)
            result = assess_micro_shadow_inputs(symbol="005930", venue="KRX", variant="VI_GAP_UP",
                                               decision_at=cut, trading_date=cut.date(), trade_snapshot=snapshot,
                                               vi_history=history)
            self.assertEqual(("UNKNOWN", None), (result["status"], result["candidate"]))
            self.assertEqual([
                "native_quote_depth", "native_instrument_tradability", "native_cross_stream_order",
                "native_vi_release_execution_price",
            ], result["missing_inputs"])
            self.assertEqual(("OBSERVED", 1, snapshot["rows"][0]["input_ref"]), (
                result["trade"]["state"], result["trade"]["count"], result["trade"]["latest"]["ref"],
            ))
            self.assertEqual({
                (activation["event_id"], "ACTIVATED", "1223", "2026-09-11T09:10:00+09:00"),
                (release["event_id"], "RELEASED", "1224", "2026-09-11T09:10:02+09:00"),
            }, {(row["ref"], row["kind"], row["fid"], row["observed_at"]) for row in result["vi"]["observations"]})
            self.assertEqual(history, store.load_market_event_history("vi", code="005930", limit=6))
        self.assertIs(collector._ram_observation_owner, retention_owner)
        self.assertEqual(10, collector._ram_trade_retention_seconds)
        self.assertEqual({("005930", "KRX"), ("005930", "NXT")}, collector._approved_trade_sources)

    def test_missing_or_different_cut_cannot_use_book_like_fields_or_require_vi_for_scalp(self):
        collector = CentralRealtimeCollector(lambda: "unused", "mock", RealtimeHub(), lambda: _START)
        snapshot = collector.ram_observation_snapshot(("005930",), seconds=10)
        snapshot["orderbook"] = {"source": "0B_execution_flow", "bids": [[101, 100]], "asks": [[102, 100]]}
        for cut, expected in ((_START, "trade_source_not_approved"),
                              (_START + timedelta(seconds=1), "collector_cut_unconfirmed")):
            with self.subTest(cut=cut):
                result = assess_micro_shadow_inputs(symbol="005930", venue="KRX", variant="BOOK_SCALP",
                                                   decision_at=cut, trading_date=cut.date(), trade_snapshot=snapshot)
                self.assertEqual(("UNKNOWN", None, None), (result["status"], result["candidate"], result["vi"]))
                self.assertEqual(expected, result["trade"]["reason"])
                self.assertEqual([
                    "native_quote_depth", "native_instrument_tradability", "native_cross_stream_order",
                    "native_0b_observation",
                ], result["missing_inputs"])
