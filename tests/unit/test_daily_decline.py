from dataclasses import asdict, replace
from datetime import UTC, date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from kiwoom_monitor.application.daily_decline import (
    DailyDeclineBasis,
    DailyDeclineBenchmark,
    DailyDeclineCatalyst,
    DailyDeclineFill,
    DailyDeclineProtection,
    DailyDeclineQuote,
    DailyDeclineSpec,
    DailyDeclineState,
    apply_daily_decline_fill,
    apply_daily_decline_order,
    evaluate_daily_decline_shadow,
    finish_daily_decline_order,
    propose_daily_decline_action,
    replay_daily_decline,
    select_daily_decline_setup,
)
from kiwoom_monitor.domain.time_basis import KST


SYMBOL = "005930"
VENUE = "KRX"
SIGNAL_DAY = date(2026, 9, 18)
HISTORY = (date(2026, 9, 16), date(2026, 9, 17))
CUT = datetime(2026, 9, 18, 20, 2, tzinfo=KST)
SINGLE = DailyDeclineSpec(2, 2_000, 1_000, 20_000, 10_000, (10_000,), (0,), 2, False)
SPLIT = replace(SINGLE, tranche_weights_bps=(3_500, 3_500, 3_000),
                tranche_drop_units_bps=(0, 10_000, 20_000))
BASIS = DailyDeclineBasis(SYMBOL, VENUE, HISTORY[0], SIGNAL_DAY, CUT, "adjusted-basis", "1")
BENCHMARK = DailyDeclineBenchmark(VENUE, SIGNAL_DAY, CUT, "market-close", -200)
PROTECTION = DailyDeclineProtection(SYMBOL, VENUE, CUT, "raw-failure-and-rebound", 750, 1_150)


def daily(day, *, sequence, high, low, close):
    return {
        "kind": "daily_bar", "subject": f"{SYMBOL}:{VENUE}", "venue": VENUE,
        "observation_key": day.isoformat(), "revision_id": f"revision-{sequence}",
        "accepted_sequence": sequence, "source_id": "kiwoom-ka10081",
        "available_at": datetime(day.year, day.month, day.day, 20, 1, tzinfo=KST).isoformat(),
        "completeness": "complete", "value_kind": "actual",
        "payload": {"version": "daily_bar/v1", "code": SYMBOL, "market": VENUE,
                    "trading_date": day.isoformat(), "adjustment_mode": "1",
                    "open": close, "high": high, "low": low, "close": close},
    }


BARS = (
    daily(HISTORY[0], sequence=1, high=1_200, low=1_100, close=1_150),
    daily(HISTORY[1], sequence=2, high=1_150, low=1_050, close=1_100),
    daily(SIGNAL_DAY, sequence=3, high=1_100, low=850, close=900),
)


def select(rows=BARS, **changes):
    inputs = dict(spec=SINGLE, symbol=SYMBOL, venue=VENUE, signal_day=SIGNAL_DAY,
                  prior_days=HISTORY, cut=CUT, basis=BASIS, benchmark=BENCHMARK)
    return select_daily_decline_setup(rows, **{**inputs, **changes})


def quote(price, *, session=1, minute=0, cost=None, tradable=True, session_end=False):
    # The fixture's calendar skips the intervening nontrading days. The index is explicit.
    at = datetime(2026, 9, 21 + session, 9, minute, tzinfo=KST)
    return DailyDeclineQuote(SYMBOL, VENUE, at, at, price, price if cost is None else cost,
                             session, session_end, tradable, f"raw-{session}-{minute}-{price}")


def propose(setup, state, raw, **changes):
    return propose_daily_decline_action(setup, state, raw,
                                       **{"cut": raw.available_at, "protection": PROTECTION, **changes})


def synthetic_scenario(*events):
    calendar = []
    for day_number in range(16, 24):
        day = date(2026, 9, day_number).isoformat()
        windows = [{"session": "KRX_REGULAR", "phase": "CONTINUOUS", "support": "SUPPORTED",
                    "starts_at": f"{day}T09:00:00+09:00", "ends_at": f"{day}T15:20:00+09:00",
                    "schedule_version": "fixture-session/v1"}] if day_number in {16, 17, 18, 22, 23} else []
        calendar.append({"venue": VENUE, "trading_date": day, "available_at": "2026-09-01T09:00:00+09:00",
                         "revision_id": f"calendar-{day}", "evidence_ref": "calendar-fixture", "sessions": windows})
    return json.loads(json.dumps({
        "version": "dd-synthetic-evidence/v1", "symbol": SYMBOL, "venue": VENUE,
        "signal_day": SIGNAL_DAY, "prior_days": HISTORY, "cut": CUT, "daily_revisions": BARS,
        "basis": asdict(BASIS), "benchmark": asdict(BENCHMARK), "protection": asdict(PROTECTION),
        "calendar": calendar, "events": events,
    }, default=lambda value: value.isoformat()))


def quote_receipt(raw):
    return {"kind": "QUOTE", "evidence_id": raw.evidence_id, "observed_at": raw.observed_at,
            "available_at": raw.available_at, "price_won": raw.price_won,
            "buy_unit_cost_won": raw.buy_unit_cost_won, "session_end": raw.session_end, "tradable": raw.tradable}


class DailyDeclineTests(unittest.TestCase):
    def test_native_shadow_setup_uses_committed_daily_revisions_and_pinned_calendar(self):
        from kiwoom_monitor.central_server.database import SQLiteQueryStore
        from kiwoom_monitor.central_server.market_ingest import MarketDataIngestor
        from kiwoom_monitor.infrastructure.research_data_source import load_frozen_strategy_references

        days = (date(2026, 9, 17), date(2026, 9, 18), date(2026, 9, 21))
        signal_day = days[-1]
        cut = datetime(2026, 9, 21, 20, 2, tzinfo=KST)
        now = cut
        calendar = []
        for day_number in range(17, 22):
            day = date(2026, 9, day_number)
            windows = [{"session": "KRX_REGULAR", "phase": "CONTINUOUS", "support": "SUPPORTED",
                        "starts_at": f"{day}T09:00:00+09:00", "ends_at": f"{day}T15:20:00+09:00",
                        "schedule_version": "fixture-session/v1"},
                       {"session": "KRX_AFTER_MARKET", "phase": "CONTINUOUS", "support": "SUPPORTED",
                        "starts_at": f"{day}T16:00:00+09:00", "ends_at": f"{day}T20:00:00+09:00",
                        "schedule_version": "fixture-session/v1"}] if day in days else []
            calendar.append({"venue": VENUE, "trading_date": day.isoformat(), "sessions": windows,
                             "available_at": "2026-09-01T09:00:00+09:00", "revision_id": f"calendar-{day}",
                             "evidence_ref": "calendar-fixture"})
        basis = DailyDeclineBasis(SYMBOL, VENUE, days[0], signal_day, cut, "admitted-adjusted-basis", "1")
        benchmark = DailyDeclineBenchmark(VENUE, signal_day, cut, "admitted-benchmark", -200)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = SQLiteQueryStore(root / "native.sqlite3", now_provider=lambda: now)
            store.initialize()
            self.addCleanup(store.close)
            ingest = MarketDataIngestor(store, now_provider=lambda: now)
            reference_path = root / "references.json"
            encoded = json.dumps({"version": "strategy-reference/v1", "calendar": calendar,
                                  "listings": []}).encode()
            reference_path.write_bytes(encoded)
            references = load_frozen_strategy_references(reference_path, expected_sha256=hashlib.sha256(encoded).hexdigest())

            def write(day, row, mode="1"):
                payload = {"dt": day.strftime("%Y%m%d"), "open_pric": str(row["open"]),
                           "high_pric": str(row["high"]), "low_pric": str(row["low"]), "cur_prc": str(row["close"])}
                ingest.ingest("ka10081", {"stk_cd": SYMBOL, "upd_stkpc_tp": mode}, {"stk_dt_pole_chart_qry": [payload]})

            def evaluate(**changes):
                supplied = dict(spec=SINGLE, symbol=SYMBOL, venue=VENUE, signal_day=signal_day,
                                cut=cut, calendar=references.calendar, basis=basis, benchmark=benchmark)
                observations = store.load_observation_revisions("daily_bar", f"{SYMBOL}:{VENUE}")
                return evaluate_daily_decline_shadow(observations, **{**supplied, **changes})

            for day, row in zip(days, BARS, strict=True):
                now = datetime(day.year, day.month, day.day, 20, 1, tzinfo=KST)
                write(day, row["payload"])
            result = evaluate()
            self.assertEqual("QUALIFIED", result.status)
            self.assertEqual((2_500, 1_618), (result.setup.drawdown_bps, result.setup.relative_decline_bps))
            self.assertIn("calendar-2026-09-20", result.setup.evidence_ids)
            self.assertEqual("signal_session_still_open", evaluate(cut=cut - timedelta(minutes=3)).reason)
            incomplete = tuple(day for day in references.calendar if day.trading_date != date(2026, 9, 20))
            self.assertEqual("session_window_unverified", evaluate(calendar=incomplete).reason)
            self.assertEqual("adjusted_price_basis_unverified", evaluate(basis=None).reason)

            signal = references.calendar[-1]
            extended = replace(signal, sessions=(*signal.sessions[:-1], replace(signal.sessions[-1],
                               ends_at=signal.sessions[-1].ends_at + timedelta(minutes=10))))
            self.assertEqual("daily_window_unavailable", evaluate(
                cut=cut + timedelta(minutes=10), calendar=(*references.calendar[:-1], extended)).reason)
            now = cut + timedelta(minutes=1)
            write(signal_day, BARS[-1]["payload"], mode="0")
            self.assertEqual(result, evaluate())
            self.assertEqual("daily_window_unavailable", evaluate(cut=now).reason)

    def test_selects_latest_visible_revision_before_quality_and_holds_fixed_evidence(self):
        expected = select().setup
        self.assertEqual((2_500, 1_618, 909), (expected.drawdown_bps, expected.relative_decline_bps,
                                               expected.volatility_bps))
        self.assertEqual(("revision-1", "revision-2", "revision-3", "adjusted-basis", "market-close"),
                         expected.evidence_ids)
        partial = {**BARS[-1], "revision_id": "partial", "accepted_sequence": 4,
                   "completeness": "partial", "available_at": CUT.astimezone(UTC).isoformat()}
        earlier = CUT - timedelta(seconds=1)
        self.assertEqual("QUALIFIED", select((*BARS, partial), cut=earlier,
                                             basis=replace(BASIS, available_at=earlier),
                                             benchmark=replace(BENCHMARK, available_at=earlier)).status)
        self.assertEqual("daily_window_unavailable", select((*BARS, partial)).reason)
        self.assertEqual("daily_window_unavailable", select((partial, *BARS)).reason)
        corrected = {**partial, "revision_id": "complete-after-cut", "accepted_sequence": 5,
                     "completeness": "complete", "available_at": (CUT + timedelta(minutes=1)).isoformat()}
        self.assertEqual(expected, select((*BARS, corrected)).setup)

    def test_basis_market_and_catalyst_remain_separate_point_in_time_evidence(self):
        raw_basis = replace(BASIS, adjustment_mode="0")
        self.assertEqual("adjusted_price_basis_unverified", select(basis=raw_basis).reason)
        mixed = {**BARS[-1], "payload": {**BARS[-1]["payload"], "adjustment_mode": "0"}}
        self.assertEqual("daily_window_unavailable", select((*BARS[:-1], mixed)).reason)
        self.assertEqual("benchmark_unavailable", select(benchmark=replace(BENCHMARK,
                         available_at=CUT + timedelta(seconds=1))).reason)
        self.assertEqual("NOT_QUALIFIED", select(benchmark=replace(BENCHMARK, return_bps=-1_900)).status)
        self.assertEqual("session_window_unverified", select(prior_days=HISTORY[1:]).reason)
        damaged = DailyDeclineCatalyst("DAMAGED", CUT, "negative-event")
        self.assertEqual("DAMAGED", select(catalyst=damaged).setup.catalyst_state)
        intact_only = replace(SINGLE, require_intact_catalyst=True)
        self.assertEqual("UNKNOWN", select(spec=intact_only).status)
        self.assertEqual("NOT_QUALIFIED", select(spec=intact_only, catalyst=damaged).status)
        self.assertEqual("QUALIFIED", select(spec=intact_only, catalyst=replace(damaged, state="INTACT")).status)

    def test_single_and_split_use_equal_total_budget_and_raw_price(self):
        single, split = select(spec=SINGLE).setup, select(spec=SPLIT).setup
        empty = DailyDeclineState()
        raw = quote(1_000)
        one, first = propose(single, empty, raw), propose(split, empty, raw)
        self.assertEqual((10_000, 10_000), (one.budget_won, first.budget_won))
        self.assertEqual((10, 3, 1_000), (one.quantity, first.quantity, first.price_won))
        self.assertEqual("raw_protection_unavailable", propose(split, empty, raw, protection=None).reason)
        accepted = apply_daily_decline_order(empty, first)
        self.assertEqual((0, 3_000), (accepted.owned_quantity, accepted.reserved_won))
        part = DailyDeclineFill("entry-part", first.action_id, 2, 990, 20, 1)
        partial = apply_daily_decline_fill(accepted, part)
        self.assertEqual((2, 2_000, 1_000), (partial.owned_quantity, partial.spent_won, partial.reserved_won))
        self.assertEqual("order_reconciliation_pending", propose(split, partial, quote(900, minute=1)).reason)
        terminal = finish_daily_decline_order(partial, first.action_id)
        self.assertEqual("next_tranche_decline_not_met", propose(split, terminal, quote(950, minute=1)).reason)
        second = propose(split, terminal, quote(900, minute=2, cost=905))
        self.assertEqual((1, 3, 2_715), (second.stage, second.quantity, second.reserved_won))
        bought = apply_daily_decline_order(terminal, second)
        filled = apply_daily_decline_fill(bought, DailyDeclineFill("add-fill", second.action_id, 3, 900, 15, 1))
        finished = finish_daily_decline_order(filled, second.action_id)
        third = propose(split, finished, quote(800, session=2))
        self.assertEqual((2, 3, 2_400), (third.stage, third.quantity, third.reserved_won))
        self.assertLessEqual(finished.spent_won + third.reserved_won, one.budget_won)

    def test_protection_cancels_pending_buy_before_selling_only_owned_shares(self):
        setup, empty = select(spec=SPLIT).setup, DailyDeclineState()
        first = propose(setup, empty, quote(1_000))
        accepted = apply_daily_decline_order(empty, first)
        accepted = apply_daily_decline_fill(accepted, DailyDeclineFill("buy-fill", first.action_id, 1, 1_000, 0, 1))
        gap = quote(740, session=2)
        moved = replace(PROTECTION, failure_price_won=700, evidence_id="late-replacement")
        cancel = propose(setup, accepted, gap, protection=moved)
        self.assertEqual(("CANCEL", first.action_id), (cancel.kind, cancel.target_action_id))
        cancelling = apply_daily_decline_order(accepted, cancel)
        self.assertEqual(2_000, cancelling.reserved_won)
        self.assertEqual(cancelling, apply_daily_decline_order(cancelling, cancel))
        reconciled = finish_daily_decline_order(cancelling, first.action_id)
        late = DailyDeclineFill("late-fill", first.action_id, 1, 1_000, 0, 1)
        recovered = apply_daily_decline_fill(reconciled, late)
        self.assertEqual(recovered, apply_daily_decline_fill(recovered, late))
        self.assertEqual("tradeability_unknown", propose(setup, recovered, quote(700, session=2, minute=1,
                                                                               tradable=None)).reason)
        exit_proposal = propose(setup, recovered, quote(900, session=2, minute=2), protection=None)
        self.assertEqual(("SELL", "raw_failure_level", 2),
                         (exit_proposal.kind, exit_proposal.reason, exit_proposal.quantity))
        self.assertEqual(2, apply_daily_decline_order(recovered, exit_proposal).owned_quantity)
        with self.assertRaisesRegex(ValueError, "fill exceeds"):
            apply_daily_decline_fill(recovered, replace(late, fill_id="excess-fill", quantity=2))

    def test_counts_verified_sessions_from_first_fill_and_exits_after_held_deadline(self):
        setup, empty = select().setup, DailyDeclineState()
        first = propose(setup, empty, quote(1_000))
        state = apply_daily_decline_order(empty, first)
        state = apply_daily_decline_fill(state, DailyDeclineFill("buy", first.action_id, 3, 1_000, 0, 1))
        state = finish_daily_decline_order(state, first.action_id)
        self.assertEqual("NONE", propose(setup, state, quote(950, minute=1, session_end=True)).kind)
        self.assertEqual("NONE", propose(setup, state, quote(950, session=2)).kind)
        at_deadline = propose(setup, state, quote(950, session=2, minute=1, session_end=True))
        self.assertEqual(("SELL", "holding_sessions_elapsed", 3),
                         (at_deadline.kind, at_deadline.reason, at_deadline.quantity))
        state = apply_daily_decline_order(state, at_deadline)
        state = apply_daily_decline_fill(state, DailyDeclineFill("sell", at_deadline.action_id, 2, 950, 0, 2))
        self.assertEqual(1, state.owned_quantity)
        state = finish_daily_decline_order(state, at_deadline.action_id)
        replacement = propose(setup, state, quote(900, session=3))
        self.assertEqual(1, replacement.quantity)
        state = apply_daily_decline_order(state, replacement)
        late = DailyDeclineFill("late-sell", at_deadline.action_id, 1, 950, 0, 2)
        state = apply_daily_decline_fill(state, late)
        cancel = propose(setup, state, quote(890, session=3, minute=1))
        self.assertEqual((0, "CANCEL", replacement.action_id),
                         (state.owned_quantity, cancel.kind, cancel.target_action_id))

    def test_synthetic_replay_uses_calendar_and_recorded_fill_receipts_for_result(self):
        raw_entry = quote(1_000)
        raw_exit = quote(740, session=2)
        buy_receipt = {"kind": "FILL", "available_at": raw_entry.available_at, "order_ref": "entry",
                       "fill_id": "buy-2", "quantity": 2, "price_won": 1_000, "fee_won": 0, "session_index": 1}
        fixture = synthetic_scenario(
            quote_receipt(raw_entry),
            {"kind": "ACCEPT", "available_at": raw_entry.available_at, "quote_ref": raw_entry.evidence_id,
             "order_ref": "entry"},
            buy_receipt, buy_receipt,
            {"kind": "TERMINAL", "available_at": raw_entry.available_at, "order_ref": "entry"},
            quote_receipt(raw_exit),
            {"kind": "ACCEPT", "available_at": raw_exit.available_at, "quote_ref": raw_exit.evidence_id,
             "order_ref": "exit"},
            {"kind": "FILL", "available_at": raw_exit.available_at, "order_ref": "exit",
             "fill_id": "sell-2", "quantity": 2, "price_won": 740, "fee_won": 2, "session_index": 2},
            {"kind": "TERMINAL", "available_at": raw_exit.available_at, "order_ref": "exit"},
        )
        result = replay_daily_decline(fixture, asdict(SPLIT))
        self.assertEqual(result, replay_daily_decline(fixture, asdict(SPLIT)))
        self.assertEqual(result, json.loads(json.dumps(result)))
        self.assertEqual("QUALIFIED", result["setup"]["status"])
        self.assertEqual(("BUY", 0, 3_000), (result["events"][0]["action"]["kind"],
                         result["events"][0]["owned_quantity"], result["events"][1]["reserved_won"]))
        self.assertEqual(2, result["events"][3]["owned_quantity"])
        exit_action = result["events"][5]["action"]
        self.assertEqual(("SELL", 2, 2), (exit_action["kind"], exit_action["quantity"], exit_action["session_index"]))
        self.assertEqual((0, 2, -522), (result["summary"]["owned_quantity"], result["summary"]["fill_count"],
                                       result["summary"]["realized_pnl_won"]))

    def test_replay_defaults_missing_quote_cost_and_fill_fees(self):
        events = []
        for raw, ref, session in ((quote(1000), "entry", 1), (quote(740, session=2), "exit", 2)):
            quote_event = quote_receipt(raw)
            quote_event.pop("buy_unit_cost_won")
            common = {"available_at": raw.available_at, "order_ref": ref}
            events.extend((quote_event,
                           {**common, "kind": "ACCEPT", "quote_ref": raw.evidence_id},
                           {**common, "kind": "FILL", "fill_id": ref, "quantity": 20,
                            "price_won": raw.price_won, "session_index": session},
                           {**common, "kind": "TERMINAL"}))
        result = replay_daily_decline(synthetic_scenario(*events), asdict(replace(SPLIT, budget_won=60_000)))
        first_buy = result["events"][0]["action"]
        self.assertEqual(("BUY", 20, 20_020), (first_buy["kind"], first_buy["quantity"], first_buy["reserved_won"]))
        fills = [row for row in result["events"] if row["kind"] == "FILL"]
        self.assertEqual([3, 31], [row["fee_won"] for row in fills])
        self.assertTrue(all(row["cost_provenance"]["source_kind"] == "model_estimate" for row in fills))
        self.assertEqual(-5_234, result["summary"]["realized_pnl_won"])

    def test_material_news_and_price_only_use_the_same_dd_observations_and_fills(self):
        entry, before, after = quote(1000), quote(970, minute=1), quote(970, minute=2)
        scenario = synthetic_scenario(
            quote_receipt(entry), {"kind": "ACCEPT", "available_at": entry.available_at,
                                   "quote_ref": entry.evidence_id, "order_ref": "entry"},
            {"kind": "FILL", "available_at": entry.available_at, "order_ref": "entry", "fill_id": "buy",
             "quantity": 2, "price_won": 1000, "fee_won": 0, "session_index": 1},
            {"kind": "TERMINAL", "available_at": entry.available_at, "order_ref": "entry"},
            quote_receipt(before), quote_receipt(after),
        )

        def row(ref, parent, stage, at, effect):
            return {"symbol": SYMBOL, "ref": ref, "revises": parent, "stage": stage, "effect": effect,
                    "effective_at": at.isoformat(), "available_at": at.isoformat(),
                    "source_ref": f"source:{ref}", "source_version": "fixture/v1"}

        rows = [row("report", None, "REPORT", CUT - timedelta(hours=2), "UNKNOWN"),
                row("confirmed", "report", "CONFIRMED", CUT - timedelta(hours=1), "INTACT"),
                row("corrected", "confirmed", "CORRECTED", before.available_at, "INTACT"),
                row("withdrawn", "corrected", "WITHDRAWN", after.available_at, "DAMAGED"),
                row("other", None, "CONFIRMED", after.available_at, "INTACT")]
        with_material = scenario | {"material_news": {"event_ref": "report", "revisions": rows}}
        price = replay_daily_decline(scenario, asdict(SINGLE))
        news = replay_daily_decline(with_material, asdict(replace(SINGLE, require_intact_catalyst=True)))
        advisory = replay_daily_decline(with_material, asdict(SINGLE))
        self.assertEqual(("QUALIFIED", "INTACT"), (news["setup"]["status"], news["setup"]["catalyst_state"]))
        self.assertEqual(["BUY", "NONE", "NONE"], [row["action"]["kind"] for row in price["events"] if row["kind"] == "QUOTE"])
        self.assertEqual(["BUY", "NONE", "NONE"], [row["action"]["kind"] for row in advisory["events"] if row["kind"] == "QUOTE"])
        actions = [row for row in news["events"] if row["kind"] == "QUOTE"]
        self.assertEqual([("BUY", "INTACT"), ("NONE", "INTACT"), ("SELL", "DAMAGED")],
                         [(row["action"]["kind"], row["material_catalyst_state"]) for row in actions])
        self.assertEqual("catalyst_damaged", actions[-1]["action"]["reason"])
        self.assertEqual(("withdrawn",), actions[-1]["material_evidence_refs"])
        self.assertEqual(2, news["summary"]["owned_quantity"])
        self.assertIsNone(news["summary"]["realized_pnl_won"])

    def test_synthetic_replay_neither_skips_unknown_calendar_nor_infers_fill(self):
        raw = quote(1_000)
        fixture = synthetic_scenario(quote_receipt(raw))
        observed = replay_daily_decline(fixture, asdict(SINGLE))
        self.assertEqual("BUY", observed["events"][0]["action"]["kind"])
        self.assertEqual(0, observed["summary"]["fill_count"])
        self.assertIsNone(observed["summary"]["realized_pnl_won"])
        fixture["calendar"] = [day for day in fixture["calendar"] if day["trading_date"] != "2026-09-21"]
        missing = replay_daily_decline(fixture, asdict(SINGLE))
        self.assertEqual("NONE", missing["events"][0]["action"]["kind"])
        fixture["events"].append({"kind": "FILL", "available_at": raw.available_at.isoformat(),
                                  "order_ref": "never-accepted"})
        with self.assertRaisesRegex(ValueError, "accepted order reference"):
            replay_daily_decline(fixture, asdict(SINGLE))
