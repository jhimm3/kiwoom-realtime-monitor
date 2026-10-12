from __future__ import annotations

from copy import deepcopy
from datetime import datetime, time
import json
import unittest
from unittest.mock import patch

from scripts import summarize_historical_baseline_results as review
from scripts.prepare_historical_baseline_requests import prepare_daily_plan, prepare_daily_requests
from tests.unit import test_historical_baseline_requests as request_fixtures


def closed_trade(*, risk=False):
    buy = dict(event_id="buy", event_type="FILL", symbol="005930", side="BUY",
               occurred_at="2024-09-03T09:01:00+09:00", quantity=1, notional_won=1000,
               commission_won=1, tax_won=0, total_cost_won=1, realized_pnl_won=0,
               decision_id="enter", reason="")
    sell = dict(event_id="sell", event_type="RISK_FILL" if risk else "FILL", symbol="005930", side="SELL",
                occurred_at="2024-09-03T00:11:00+00:00", quantity=1, notional_won=1100,
                commission_won=2, tax_won=3, total_cost_won=5, realized_pnl_won=94,
                decision_id="" if risk else "exit", reason="stop_loss_hit" if risk else "")
    decisions = [dict(decision_id="enter", symbol="005930", final_action="ENTER", reasons=["breakout_passed"])]
    if not risk:
        decisions.append(dict(decision_id="exit", symbol="005930", final_action="EXIT", reasons=["max_hold_reached"]))
    return [buy, sell], decisions


class ClosedTradeLossTests(unittest.TestCase):
    def test_native_fifo_and_normalized_times_preserve_costs_once(self):
        events, decisions = closed_trade()
        result = review.execution_loss_summary(list(reversed(events)), decisions)
        self.assertEqual((1, 100, 3, 3, 94), tuple(result[k] for k in (
            "closed_trade_count", "gross_at_fill_prices_won", "commission_won", "tax_won", "net_pnl_won")))
        trade = result["trades"][0]
        self.assertEqual(600, trade["exposure_seconds"])
        self.assertEqual(["max_hold_reached"], trade["exit_reasons"])
        self.assertEqual(["breakout_passed"], trade["entry_reasons"])
        self.assertTrue(result["slippage_already_in_fill_prices"])

    def test_risk_exit_uses_event_reason_without_fabricating_decision(self):
        events, decisions = closed_trade(risk=True)
        trade = review.execution_loss_summary(events, decisions)["trades"][0]
        self.assertEqual(["stop_loss_hit"], trade["exit_reasons"])
        self.assertEqual("", trade["exit_decision_id"])

    def test_open_entry_is_not_a_closed_trade_or_a_fake_exit(self):
        events, decisions = closed_trade()
        result = review.execution_loss_summary(events[:1], decisions[:1])
        self.assertEqual([], result["trades"])
        self.assertEqual(0, result["closed_trade_count"])
        self.assertEqual(0, result["net_pnl_won"])
        self.assertEqual(0, result["commission_won"])

    def test_missing_or_wrong_decision_reference_fails(self):
        for index in (0, 1):
            events, decisions = closed_trade()
            with self.subTest(missing=index), self.assertRaisesRegex(ValueError, "판단 참조"):
                review.execution_loss_summary(events, decisions[:index] + decisions[index + 1:])
        events, decisions = closed_trade()
        decisions[1]["symbol"] = "000660"
        with self.assertRaisesRegex(ValueError, "판단 참조"):
            review.execution_loss_summary(events, decisions)

    def test_duplicate_decisions_or_same_timestamp_fills_fail(self):
        events, decisions = closed_trade()
        with self.assertRaisesRegex(ValueError, "중복"):
            review.execution_loss_summary(events, [*decisions, decisions[0]])
        other = {**events[0], "event_id": "ambiguous", "occurred_at": "2024-09-03T00:01:00+00:00"}
        with self.assertRaisesRegex(ValueError, "모호"):
            review.execution_loss_summary([*events, other], decisions)

    def test_naive_timestamp_is_rejected(self):
        events, decisions = closed_trade()
        events[0]["occurred_at"] = "2024-09-03T09:01:00"
        with self.assertRaisesRegex(ValueError, "시간대"):
            review.execution_loss_summary(events, decisions)

    def test_partial_allocation_is_explicitly_unsupported(self):
        events, decisions = closed_trade()
        events[0]["quantity"] = 2
        events[0]["notional_won"] = 2000
        with self.assertRaisesRegex(ValueError, "부분 체결"):
            review.execution_loss_summary(events, decisions)

    def test_inconsistent_or_negative_costs_and_realized_pnl_fail(self):
        for index, key, value in ((1, "realized_pnl_won", 95), (0, "total_cost_won", 2),
                                  (0, "commission_won", -1), (1, "notional_won", 1101)):
            events, decisions = closed_trade()
            events[index][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "비용 계산"):
                review.execution_loss_summary(events, decisions)

    def test_missing_or_string_exit_reason_is_rejected(self):
        for reasons in ([], "max_hold_reached"):
            events, decisions = closed_trade()
            decisions[1]["reasons"] = reasons
            with self.subTest(reasons=reasons), self.assertRaisesRegex(ValueError, "사유"):
                review.execution_loss_summary(events, decisions)


class DailyLossBoundaryTests(unittest.TestCase):
    # 기존 runner의 작고 실제적인 고정 일별 입력 fixture를 재사용한다.
    setUp = request_fixtures.DailyDevelopmentRequestTests.setUp

    def test_oos_and_malformed_plan_are_rejected_before_database_or_replay(self):
        plan_path = prepare_daily_plan(self.selection, self.output)
        original = json.loads(plan_path.read_text(encoding="utf-8"))
        for variant in ("oos", "selection", "unsealed", "future_case"):
            plan = deepcopy(original)
            if variant == "oos": plan["evaluation"]["folds"][0]["role"] = "OOS"
            if variant == "selection": plan["selection"] = []
            if variant == "unsealed": plan["selection"]["oos"]["status"] = "OPEN"
            if variant == "future_case": plan["selection"]["cases"][0]["outcome_date"] = "2026-02-01"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            with self.subTest(variant=variant), patch.object(review, "ResearchRepository", side_effect=AssertionError("no DB")):
                with self.assertRaises(ValueError): review.inspect_daily_development_losses(self.output)
                with self.assertRaises(ValueError): review.inspect_breakout_loss_attribution(self.output)
                with self.assertRaises(ValueError): review.inspect_training_breakout_paths(self.output)

    def test_changed_strategy_or_database_request_is_rejected_before_query(self):
        paths = prepare_daily_requests(self.selection, self.source, self.output)
        original = json.loads(paths[0].read_text(encoding="utf-8"))
        for variant in ("strategy", "database"):
            request = deepcopy(original)
            if variant == "strategy": request["request"]["strategy"]["max_hold_minutes"] += 1
            if variant == "database": request["request"]["database"] = str(self.root / "other.sqlite3")
            paths[0].write_text(json.dumps(request), encoding="utf-8")
            with self.subTest(variant=variant), patch.object(review, "ResearchRepository", side_effect=AssertionError("no DB")):
                with self.assertRaises(ValueError): review.inspect_daily_development_losses(self.output)
                with self.assertRaises(ValueError): review.inspect_breakout_loss_attribution(self.output)
                with self.assertRaises(ValueError): review.inspect_training_breakout_paths(self.output)

    def test_actual_daily_runner_ledger_is_read_only_and_keeps_terminal_censor(self):
        from kiwoom_monitor.research_process import load_development_validation_request, execute_development_validation
        paths = prepare_daily_requests(self.selection, self.source, self.output)
        (self.output / "results").mkdir()
        for path in paths:
            result = execute_development_validation(load_development_validation_request(path), owner_token="loss-review")
            (self.output / "results" / path.name).write_text(json.dumps(result), encoding="utf-8")
        database = self.output / "research.sqlite3"
        before = database.read_bytes()
        with (patch("kiwoom_monitor.infrastructure.persistence.research_repository.SQLiteMigrationRunner", side_effect=AssertionError("no migration")),
              patch("kiwoom_monitor.infrastructure.research_data_source.load_research_input", side_effect=AssertionError("no replay"))):
            result = review.inspect_daily_development_losses(self.output)
        self.assertEqual(before, database.read_bytes())
        self.assertEqual(4, len(result["runs"]))
        self.assertFalse(result["oos_queried"])
        self.assertFalse(result["candidate_eligibility_evaluated"])
        terminal = [r["position_observations"] for r in result["runs"]
                    if r["position_observations"]["status"] == "OPEN_POSITION_CENSORED"]
        self.assertTrue(terminal)
        for position in terminal:
            due = datetime.fromisoformat(position["max_hold_due_at"])
            opened = datetime.fromisoformat(position["entry_at"])
            self.assertEqual(600, (due - opened).total_seconds())
            self.assertEqual(due.time() >= time(15, 20), position["max_hold_due_in_or_after_auction"])
        self.assertTrue(all(r["continuous_session_end"][11:19] == "15:20:00" for r in terminal))
        self.assertTrue(all(r["current_code_matches"] for r in result["runs"]))
        self.assertFalse(any(self.output.rglob("*.csv")))
        original_load = review.ResearchRepository.load_run
        def changed_policy(repository, run_id):
            run = original_load(repository, run_id)
            run["spec"]["evaluation_spec"]["minimum_closed_trades"] += 1
            return run
        with patch.object(review.ResearchRepository, "load_run", changed_policy):
            with self.assertRaisesRegex(ValueError, "native 원장"):
                review.inspect_daily_development_losses(self.output)

    def test_native_price_attribution_preserves_db_and_reconciles_each_group(self):
        from kiwoom_monitor.research_process import load_development_validation_request, execute_development_validation
        paths = prepare_daily_requests(self.selection, self.source, self.output)
        (self.output / "results").mkdir()
        for path in paths:
            result = execute_development_validation(load_development_validation_request(path), owner_token="price-review")
            (self.output / "results" / path.name).write_text(json.dumps(result), encoding="utf-8")
        database = self.output / "research.sqlite3"
        before = database.read_bytes()
        with (patch("kiwoom_monitor.infrastructure.persistence.research_repository.SQLiteMigrationRunner", side_effect=AssertionError("no migration")),
              patch("kiwoom_monitor.infrastructure.research_data_source.load_research_input", side_effect=AssertionError("no replay"))):
            result = review.inspect_breakout_loss_attribution(self.output)
        self.assertEqual(before, database.read_bytes())
        self.assertEqual(2, len(result["runs"]))
        self.assertFalse(result["oos_queried"])
        self.assertFalse(result["strategy_changed"])
        from contextlib import redirect_stdout
        import io
        output = io.StringIO()
        with patch("sys.argv", ["review", "--root", str(self.output), "--inspect-breakout-losses"]), redirect_stdout(output):
            self.assertEqual(0, review.main())
        cli_report = json.loads(output.getvalue())
        self.assertEqual("historical_breakout_loss_attribution/v1", cli_report["version"])
        self.assertEqual(result["roles"], cli_report["roles"])
        self.assertEqual(result["current_code_hash"], cli_report["current_code_hash"])
        self.assertFalse(cli_report["oos_queried"])
        self.assertEqual(before, database.read_bytes())
        self.assertFalse(any(self.output.rglob("*.csv")))
        for role in result["roles"]:
            totals = role["totals"]
            self.assertEqual(totals["net_pnl_won"], totals["gross_at_fill_prices_won"] - totals["commission_won"] - totals["tax_won"])
            for dimension in ("exit_reasons", "entry_time"):
                groups = [group for group in role["groups"] if group["dimension"] == dimension]
                for key in ("closed_trade_count", "net_pnl_won", "gross_at_fill_prices_won"):
                    self.assertEqual(totals[key], sum(group[key] for group in groups))
        with patch.object(review, "research_implementation_hash", return_value="changed-code"):
            with self.assertRaisesRegex(ValueError, "현재 구현"):
                review.inspect_breakout_loss_attribution(self.output)


class DecisionFillPriceAttributionTests(unittest.TestCase):
    def fixture(self, *, risk=False):
        events, decisions = closed_trade(risk=risk)
        for event in events:
            event["price"] = event["notional_won"]
        for decision in decisions:
            entering = decision["final_action"] == "ENTER"
            decision.update(quantity=1, signal_reference_price=995 if entering else 1110,
                            decided_at=events[0 if entering else 1]["occurred_at"])
        return events, decisions

    def test_signal_move_and_both_fill_effects_reconcile_without_double_slippage(self):
        events, decisions = self.fixture()
        result = review.execution_loss_summary(events, decisions, attribute_prices=True)
        attribution = result["trades"][0]["price_attribution"]
        self.assertEqual("COMPLETE", attribution["status"])
        self.assertEqual((-5, 115, -10, 0), tuple(attribution[key] for key in (
            "entry_signal_to_fill_won", "signal_price_move_won", "exit_signal_to_fill_won", "unattributed_after_entry_won")))
        self.assertEqual(94, result["net_pnl_won"])
        self.assertNotIn("price_attribution", review.execution_loss_summary(events, decisions)["trades"][0])

    def test_risk_exit_keeps_missing_signal_distinct_from_zero(self):
        events, decisions = self.fixture(risk=True)
        trade = review.execution_loss_summary(events, decisions, attribute_prices=True)["trades"][0]
        attribution = trade["price_attribution"]
        self.assertEqual("EXIT_SIGNAL_UNAVAILABLE", attribution["status"])
        self.assertIsNone(attribution["signal_price_move_won"])
        self.assertIsNone(attribution["exit_signal_to_fill_won"])
        self.assertEqual(105, attribution["unattributed_after_entry_won"])
        totals = review._loss_attribution_totals([trade])
        self.assertEqual((0, 1), (totals["complete_price_attribution_count"], totals["exit_signal_unavailable_count"]))

    def test_invalid_reference_price_quantity_time_and_fill_notional_are_rejected(self):
        for key, value in (("signal_reference_price", None), ("signal_reference_price", True),
                           ("signal_reference_price", 0), ("quantity", 2),
                           ("decided_at", "2024-09-03T09:02:00+09:00"),
                           ("decided_at", "2024-09-03T09:01:00")):
            events, decisions = self.fixture()
            decisions[0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                review.execution_loss_summary(events, decisions, attribute_prices=True)
        events, decisions = self.fixture()
        events[1]["price"] = 1101
        with self.assertRaisesRegex(ValueError, "체결 가격"):
            review.execution_loss_summary(events, decisions, attribute_prices=True)

    def test_multiple_exit_reasons_are_not_counted_as_multiple_trades(self):
        events, decisions = self.fixture()
        decisions[1]["reasons"] = ["max_hold_reached", "target_reached"]
        trade = review.execution_loss_summary(events, decisions, attribute_prices=True)["trades"][0]
        self.assertEqual(1, review._loss_attribution_totals([trade])["closed_trade_count"])
        self.assertEqual(["max_hold_reached", "target_reached"], trade["exit_reasons"])

    def test_profit_erased_by_fees_is_separate_from_flat_price_loss(self):
        events, decisions = self.fixture()
        template = review.execution_loss_summary(events, decisions, attribute_prices=True)["trades"][0]
        rows = [{**template, "gross_at_fill_prices_won": gross, "net_pnl_won": net}
                for gross, net in ((5, -1), (6, 0), (7, 1), (0, -6), (-1, -7))]
        totals = review._loss_attribution_totals(rows)
        self.assertEqual((3, 1, 2, 1), tuple(totals[key] for key in (
            "gross_positive_trade_count", "net_positive_trade_count", "gross_profit_erased_by_fees_count", "flat_gross_net_loss_count")))

    def test_kst_time_buckets_use_half_open_fixed_boundaries(self):
        for time_text, expected in (("00:00:00", "09:00-10:00"), ("00:59:59", "09:00-10:00"),
                                    ("01:00:00", "10:00-12:00"), ("03:00:00", "12:00-14:00"),
                                    ("05:00:00", "14:00-15:20"), ("06:19:59", "14:00-15:20"),
                                    ("06:20:00", "outside_continuous_session")):
            with self.subTest(time=time_text):
                self.assertEqual(expected, review._entry_time_bucket({"opened_at": f"2024-09-03T{time_text}+00:00"}))


class TrainingTradePathTests(unittest.TestCase):
    def fixture(self, *, risk=False):
        from dataclasses import replace
        from tests.unit.test_research_factors import _bar
        from kiwoom_monitor.application.breakout_strategy import default_shadow_breakout_config
        from kiwoom_monitor.application.research_execution import SimulationCostModel
        bars = [_bar(index, close=10000, high=10000, available_offset=0) for index in range(2)]
        bars.append(_bar(2, close=10100, high=10110, available_offset=0))
        for index in range(3, 14):
            close = 10220 if index == 3 else 10090
            bars.append(replace(_bar(index, close=close, high=close + 5, available_offset=0),
                                open=10200 if index == 3 else 10100, low=min(close, 10100)))
        events, decisions = closed_trade(risk=risk)
        buy, sell = events
        buy.update(price=10206, notional_won=10206, source_revision_id='bar-3', occurred_at=bars[3].bar_start)
        sell.update(price=10094, notional_won=10094, realized_pnl_won=-118,
                    source_revision_id='bar-12' if risk else 'bar-13', occurred_at=bars[13].bar_start)
        decisions[0].update(quantity=1, signal_reference_price=10100, decided_at=bars[2].available_at)
        if not risk:
            decisions[1].update(quantity=1, signal_reference_price=10090, decided_at=bars[13].bar_start)
        trade = review.execution_loss_summary(events, decisions, attribute_prices=True)['trades'][0]
        return trade, {event['event_id']: event for event in events}, {decision['decision_id']: decision for decision in decisions}, bars, replace(default_shadow_breakout_config(), lookback_bars=2), SimulationCostModel('fixed_bps/v1', 1, 2, 5)

    def test_real_holding_path_reuses_factor_and_separates_gap_from_model_slippage(self):
        row = review._training_trade_path(*self.fixture())
        self.assertEqual('COMPLETE_OBSERVED_WINDOW', row['path_status'])
        self.assertEqual((10, 10), (row['expected_full_bar_count'], row['observed_full_bar_count']))
        self.assertEqual(100, row['entry_factor']['value']['distance_bps'])
        self.assertEqual({'entry_next_bar_gap_won': -100, 'entry_modeled_slippage_won': -6,
                          'exit_next_bar_gap_won': 10, 'exit_modeled_slippage_won': -6}, row['raw_fill_effects'])
        self.assertTrue(row['positive_close_seen_net_loss'])
        self.assertGreater(row['peak_to_last_close_bps'], 0)
        self.assertEqual([1, 3, 5, 10], [point['minutes'] for point in row['horizons']])
        self.assertEqual([13, -114, -114, -114], [point['close_delta_bps'] for point in row['horizons']])

    def test_after_exit_prices_cannot_raise_the_observed_peak(self):
        from tests.unit.test_research_factors import _bar
        args = list(self.fixture())
        original = review._training_trade_path(*args)
        args[3].append(_bar(14, close=999999, high=999999, available_offset=0))
        self.assertEqual(original, review._training_trade_path(*args))

    def test_missing_or_late_horizon_is_unknown_and_not_forward_filled(self):
        from dataclasses import replace
        for variant in ('missing', 'late'):
            args = list(self.fixture())
            if variant == 'missing':
                args[3] = [bar for bar in args[3] if bar.revision_id != 'bar-5']
            else:
                args[3] = [replace(bar, available_at=args[0]['closed_at']) if bar.revision_id == 'bar-5' else bar for bar in args[3]]
                # 종료 시각과 같은 시각은 보유 안이므로 명확히 종료 후로 지정한다.
                args[3][5] = replace(args[3][5], available_at='2026-09-12T00:14:00+00:00')
            row = review._training_trade_path(*args)
            self.assertEqual('GAPS_OR_LATE_AVAILABILITY', row['path_status'])
            self.assertEqual('BAR_UNAVAILABLE', row['horizons'][1]['status'])
            self.assertIsNone(row['horizons'][1]['close_delta_bps'])

    def test_risk_exit_bar_is_excluded_from_intrabar_peak_and_horizon(self):
        from dataclasses import replace
        args = list(self.fixture(risk=True))
        args[3][12] = replace(args[3][12], close=999999, high=999999)
        row = review._training_trade_path(*args)
        self.assertEqual(9, row['observed_full_bar_count'])
        self.assertEqual('RISK_EXIT_BAR_EXCLUDED', row['horizons'][-1]['status'])
        self.assertIsNone(row['horizons'][-1]['close_delta_bps'])
        self.assertIsNone(row['raw_fill_effects']['exit_next_bar_gap_won'])
        self.assertLess(row['best_observed_close_bps'], 100)

    def test_fill_revision_cost_and_signal_price_mismatches_fail(self):
        for variant in ('reference', 'cost', 'signal', 'duplicate'):
            args = list(self.fixture())
            if variant == 'reference': args[1]['buy']['source_revision_id'] = 'missing'
            if variant == 'cost': args[1]['buy']['price'] += 1
            if variant == 'signal': args[2]['enter']['signal_reference_price'] += 1
            if variant == 'duplicate': args[3].append(args[3][2])
            with self.subTest(variant=variant), self.assertRaises(ValueError):
                review._training_trade_path(*args)


class TrainingPathBoundaryTests(unittest.TestCase):
    setUp = request_fixtures.DailyDevelopmentRequestTests.setUp

    def seed(self):
        import hashlib
        from datetime import date
        from tests.unit.test_research_bundle_execution import rows_for
        from kiwoom_monitor.infrastructure.historical_reconstruction import write_historical_research_input
        from kiwoom_monitor.infrastructure.research_data_source import FrozenResearchDataset, load_research_input
        from kiwoom_monitor.research_process import load_development_validation_request, execute_development_validation
        old = load_research_input(self.source, session_profile='krx-regular/v1')
        rows = [row for row in old.observations if row['payload'].get('selection_date', row['payload'].get('historical_case_date')) == '2024-10-02']
        for index, row in enumerate(rows_for(date(2024, 9, 3), 25)):
            if index == 0:
                row['kind'] = 'historical_candidate_population'
                row['payload'].update(codes=['005930'], selection_date='2024-09-02')
            else:
                price = 1000 if index <= 5 else 1020
                row['payload'].update(code='005930', historical_case_date='2024-09-02', open=price, high=price, low=price, close=price)
                row['subject'] = '005930:KRX'
                row['available_at'] = row['payload']['bar_end']
            rows.append(row)
        self.source = self.root / 'early-source'
        write_historical_research_input(FrozenResearchDataset({
            'schema_version': 3, 'runtime_input_version': 'historical_reconstruction_strategy/v1',
            'dataset_id': 'training-path-fixture', 'selected_dates': ['2024-09-02', '2024-10-02'],
            'revision_count': len(rows), 'revision_ids_hash': hashlib.sha256('\n'.join(row['revision_id'] for row in rows).encode()).hexdigest(),
            'excluded_cases': [], 'not_contemporaneous_top20': True,
            'research_session_profile': old.manifest['research_session_profile'],
        }, tuple(rows)), self.source)
        paths = prepare_daily_requests(self.selection, self.source, self.output)
        (self.output / 'results').mkdir()
        training_ids = set()
        for path in paths:
            result = execute_development_validation(load_development_validation_request(path), owner_token='training-path-fixture')
            (self.output / 'results' / path.name).write_text(json.dumps(result), encoding='utf-8')
            if path.name == 'daily-breakout.json':
                training_ids = {step['run_id'] for step in result['steps'] if step['role'] == 'TRAIN'}
        return training_ids

    def test_native_training_paths_never_query_validation_or_pullback_performance(self):
        training_ids = self.seed()
        database = self.output / 'research.sqlite3'
        before = database.read_bytes()
        names = ('load_run', 'load_execution_events', 'load_evaluations', 'load_independent_development_comparison')
        originals = {name: getattr(review.ResearchRepository, name) for name in names}
        def guard(name):
            def load(repository, value):
                ids = set(value) if isinstance(value, tuple) else {value}
                self.assertTrue(ids <= training_ids, (name, ids))
                return originals[name](repository, value)
            return load
        from contextlib import ExitStack, redirect_stdout
        import io
        with ExitStack() as stack:
            for name in names:
                stack.enter_context(patch.object(review.ResearchRepository, name, guard(name)))
            stack.enter_context(patch('kiwoom_monitor.infrastructure.persistence.research_repository.SQLiteMigrationRunner', side_effect=AssertionError('no migration')))
            report = review.inspect_training_breakout_paths(self.output)
            output = io.StringIO()
            with patch('sys.argv', ['review', '--root', str(self.output), '--inspect-training-paths']), redirect_stdout(output):
                self.assertEqual(0, review.main())
        self.assertEqual(before, database.read_bytes())
        self.assertGreater(report['closed_trade_count'], 0)
        self.assertEqual(1, report['observed_day_count'])
        self.assertEqual(training_ids, set(report['run_ids']))
        self.assertTrue(report['training_only'])
        self.assertFalse(report['validation_performance_queried'])
        self.assertFalse(report['oos_queried'])
        cli_report = json.loads(output.getvalue())
        for key in ('version', 'run_ids', 'horizon_summary', 'strength_groups', 'raw_fill_effect_totals', 'closed_trade_count'):
            self.assertEqual(report[key], cli_report[key])
        for horizon in report['horizon_summary']:
            self.assertEqual(report['closed_trade_count'], sum(horizon['status_counts'].values()))
        self.assertEqual(report['closed_trade_count'], sum(group['closed_trade_count'] for group in report['strength_groups']))
        self.assertFalse(any(self.output.rglob('*.csv')))

    def test_changed_frozen_input_or_native_implementation_is_rejected(self):
        self.seed()
        with patch.object(review, 'research_implementation_hash', return_value='changed-code'):
            with self.assertRaisesRegex(ValueError, '현재 구현'):
                review.inspect_training_breakout_paths(self.output)
        manifest = json.loads((self.source / 'manifest.json').read_text(encoding='utf-8'))
        observations = self.source / manifest['observations_file']
        observations.write_bytes(observations.read_bytes() + b'\n')
        with self.assertRaisesRegex(ValueError, 'hash'):
            review.inspect_training_breakout_paths(self.output)


def cost_reference_fixture(plan, manifest):
    return {"version": "historical_stock_cost_reference/v1",
            "asset_scope": "kospi_kosdaq_individual_stocks/v1",
            **{key: manifest[key] for key in ("dataset_id", "revision_ids_hash", "observations_file_hash")},
            "checked_at": "2026-10-12T00:00:00+00:00", "commission_bps": "1.50",
            "commission_source_urls": ["https://www.kiwoom.com/standard-fees"],
            "dates": [{"outcome_date": case["outcome_date"], "sell_tax_bps": "18",
                       "source_urls": ["https://www.korea.kr/tax-schedule"]}
                      for case in plan["selection"]["cases"]]}


class DailyCostReferenceTests(unittest.TestCase):
    setUp = request_fixtures.DailyDevelopmentRequestTests.setUp

    def prepare_reference(self):
        path = self.source / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest["individual_stocks_only"] = True
        path.write_text(json.dumps(manifest))
        prepare_daily_requests(self.selection, self.source, self.output)
        plan = json.loads((self.output / "daily-plan.json").read_text())
        self.reference = self.root / "costs.json"
        document = cost_reference_fixture(plan, manifest)
        self.reference.write_text(json.dumps(document))
        return document

    def test_reference_mismatch_keeps_model_and_account_verification_unapproved(self):
        self.prepare_reference()
        before = {path: path.read_bytes() for directory in (self.output, self.source)
                  for path in directory.rglob("*") if path.is_file()}
        result = review.inspect_daily_development_inputs(self.output, cost_reference=self.reference)
        self.assertEqual(2, result["cost_reference"]["tax_mismatch_day_count"])
        self.assertTrue(result["cost_reference"]["commission_matches"])
        self.assertFalse(result["cost_reference"]["account_costs_verified"])
        self.assertFalse(result["cost_reference"]["slippage_verified"])
        self.assertEqual("20", result["cost_model"]["sell_tax_bps"])
        self.assertIn("model_costs_differ_from_date_reference", result["reasons"])
        self.assertIn("불일치 2일", review.daily_input_review_text(result))
        self.assertEqual(before, {path: path.read_bytes() for directory in (self.output, self.source)
                                 for path in directory.rglob("*") if path.is_file()})

    def test_equal_decimal_rates_still_do_not_approve_account_costs(self):
        document = self.prepare_reference()
        for row in document["dates"]:
            row["sell_tax_bps"] = "20.000"
        self.reference.write_text(json.dumps(document))
        result = review.inspect_daily_development_inputs(self.output, cost_reference=self.reference)
        self.assertEqual("MATCH", result["cost_reference"]["status"])
        self.assertFalse(result["broker_costs_verified"])
        self.assertIn("broker_costs_unverified", result["reasons"])

    def test_wrong_source_dates_and_invalid_reference_fail_closed(self):
        document = self.prepare_reference()
        plan, _ = review._daily_development_requests(self.output)
        manifest = json.loads((self.source / "manifest.json").read_text())
        costs = review.load_development_validation_request(self.output / "requests/daily-breakout.json").request.execution.cost_model
        bad_documents = []
        for key, value in (("dataset_id", "other"), ("observations_file_hash", "other"),
                           ("asset_scope", "all_assets"), ("checked_at", "2026-10-12"),
                           ("commission_bps", True), ("commission_bps", "NaN"),
                           ("commission_source_urls", ["http://example.com"]),
                           ("dates", document["dates"][:1]),
                           ("dates", [document["dates"][0]] * 2)):
            bad_documents.append({**document, key: value})
        for value in ("Infinity", "-1", "10001", True):
            changed = deepcopy(document)
            changed["dates"][0]["sell_tax_bps"] = value
            bad_documents.append(changed)
        for changed in bad_documents:
            with self.subTest(document=changed):
                self.reference.write_text(json.dumps(changed))
                with self.assertRaises(ValueError):
                    review.assess_development_cost_reference(plan, manifest, costs, self.reference)


class DailyInputReviewTests(unittest.TestCase):
    setUp = request_fixtures.DailyDevelopmentRequestTests.setUp

    def test_real_frozen_input_review_has_no_database_results_or_writes(self):
        prepare_daily_requests(self.selection, self.source, self.output)
        before = {path: path.read_bytes() for directory in (self.source, self.output)
                  for path in directory.rglob("*") if path.is_file()}
        with patch.object(review, "ResearchRepository", side_effect=AssertionError("no DB")):
            result = review.inspect_daily_development_inputs(self.output)
        self.assertEqual(before, {path: path.read_bytes() for directory in (self.source, self.output)
                                 for path in directory.rglob("*") if path.is_file()})
        self.assertEqual("REVIEW_REQUIRED", result["status"])
        self.assertEqual(2, len(result["stock_days"]))
        self.assertTrue(all(p["readiness"]["status"] == "READY" for p in result["partitions"]))
        self.assertTrue(result["cost_validity_covers_windows"])
        self.assertEqual("1.5", result["cost_model"]["commission_bps"])
        self.assertFalse(result["full_session_completeness_verified"])
        self.assertFalse(result["broker_costs_verified"])
        self.assertFalse(result["oos_queried"])
        self.assertFalse(result["candidate_eligibility_evaluated"])
        for row in result["stock_days"]:
            self.assertEqual(380, row["expected_minute_slots"])
            self.assertEqual(row["unobserved_minute_slots"], row["leading_unobserved_minutes"]
                             + row["trailing_unobserved_minutes"]
                             + sum(gap["unobserved_minutes"] for gap in row["internal_gaps"]))
        late = result["stock_days"][0]
        self.assertEqual("15:20:00", late["continuous_session_end"][11:19])
        self.assertGreater(late["outside_continuous_session_observations"], 0)
        self.assertIn("미검증", review.daily_input_review_text(result))

    def test_oos_plan_is_rejected_before_loading_any_source(self):
        prepare_daily_requests(self.selection, self.source, self.output)
        path = self.output / "daily-plan.json"
        plan = json.loads(path.read_text())
        plan["evaluation"]["folds"][0]["role"] = "OOS"
        path.write_text(json.dumps(plan))
        with patch.object(review, "load_daily_development_source", side_effect=AssertionError("no input")):
            with self.assertRaises(ValueError):
                review.inspect_daily_development_inputs(self.output)

    def test_source_with_oos_date_is_rejected_before_payload_load(self):
        prepare_daily_requests(self.selection, self.source, self.output)
        path = self.source / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest["selected_dates"].append("2026-01-14")
        path.write_text(json.dumps(manifest))
        with patch("scripts.prepare_historical_baseline_requests.load_research_input", side_effect=AssertionError("no input")):
            with self.assertRaisesRegex(ValueError, "OOS"):
                review.inspect_daily_development_inputs(self.output)

    def test_request_source_identity_or_path_mismatch_fails(self):
        paths = prepare_daily_requests(self.selection, self.source, self.output)
        originals = [json.loads(path.read_text()) for path in paths]
        for variant in ("identity", "path", "cost"):
            for index, (path, original) in enumerate(zip(paths, originals)):
                value = deepcopy(original)
                if variant == "identity":
                    value["dataset_id"] = "forged-source"
                if variant == "path" and index == 1:
                    value["request"]["dataset"] = str(self.root / "other")
                if variant == "cost" and index == 1:
                    value["request"]["execution"]["cost_model"]["commission_bps"] = "3"
                path.write_text(json.dumps(value))
            with self.subTest(variant=variant), self.assertRaises(ValueError):
                review.inspect_daily_development_inputs(self.output)

    def test_cost_period_mismatch_is_visible_and_source_label_is_not_proof(self):
        paths = prepare_daily_requests(self.selection, self.source, self.output)
        targets = [self.output / "daily-plan.json", *paths]
        for path in targets:
            document = json.loads(path.read_text())
            execution = document.get("request", document)["execution"]
            execution["cost_model"].update(valid_to="2024-09-04T00:00:00+09:00",
                                           rate_basis="official_period_rule", source="broker verified")
            path.write_text(json.dumps(document))
        result = review.inspect_daily_development_inputs(self.output)
        self.assertFalse(result["cost_validity_covers_windows"])
        self.assertFalse(result["broker_costs_verified"])
        self.assertIn("cost_validity_does_not_cover_development_windows", result["reasons"])

    def test_duplicate_revisions_and_internal_gaps_are_counted_without_fabricating_bars(self):
        from kiwoom_monitor.infrastructure.research_data_source import FrozenResearchDataset
        prepare_daily_requests(self.selection, self.source, self.output)
        plan = json.loads((self.output / "daily-plan.json").read_text())
        dataset = review.load_daily_development_source(plan, self.source)
        observations = [deepcopy(row) for row in dataset.observations]
        bar = next(row for row in observations if row["kind"] == "minute_bar")
        duplicate = deepcopy(bar)
        duplicate["revision_id"] += "-revision"
        duplicate["accepted_sequence"] = max(row["accepted_sequence"] for row in observations) + 1
        observations.append(duplicate)
        removed = next(row for row in observations if row["kind"] == "minute_bar"
                       and row["payload"]["code"] == "000660" and row["payload"]["bar_start"][11:16] == "09:03")
        observations.remove(removed)
        changed = FrozenResearchDataset(dataset.manifest, tuple(observations))
        with patch.object(review, "load_daily_development_source", return_value=changed):
            result = review.inspect_daily_development_inputs(self.output)
        original = review.inspect_daily_development_inputs(self.output)
        self.assertEqual(original["stock_days"][0], result["stock_days"][0])
        early = result["stock_days"][1]
        self.assertEqual(1, len(early["internal_gaps"]))
        self.assertEqual(1, early["internal_gaps"][0]["unobserved_minutes"])
        self.assertEqual(original["stock_days"][1]["observed_unique_minutes"] - 1, early["observed_unique_minutes"])

    def test_cli_outputs_json_without_creating_results(self):
        import io
        from contextlib import redirect_stdout
        prepare_daily_requests(self.selection, self.source, self.output)
        output = io.StringIO()
        with patch("sys.argv", ["review", "--root", str(self.output), "--inspect-daily-inputs"]), redirect_stdout(output):
            self.assertEqual(0, review.main())
        self.assertEqual("historical_daily_input_review/v1", json.loads(output.getvalue())["version"])
        self.assertFalse((self.output / "research.sqlite3").exists())
        self.assertFalse(any(self.output.rglob("*.csv")))

    def test_corrupt_frozen_payload_is_rejected_by_existing_loader(self):
        prepare_daily_requests(self.selection, self.source, self.output)
        manifest = json.loads((self.source / "manifest.json").read_text())
        path = self.source / manifest["observations_file"]
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "hash"):
            review.inspect_daily_development_inputs(self.output)

    def test_missing_bars_preserve_candidate_and_block_minimum_pair(self):
        from kiwoom_monitor.infrastructure.research_data_source import FrozenResearchDataset
        prepare_daily_requests(self.selection, self.source, self.output)
        plan = json.loads((self.output / "daily-plan.json").read_text())
        dataset = review.load_daily_development_source(plan, self.source)
        observations = tuple(row for row in dataset.observations
                             if row["kind"] != "minute_bar" or row["payload"]["code"] != "000660")
        with patch.object(review, "load_daily_development_source", return_value=FrozenResearchDataset(dataset.manifest, observations)):
            result = review.inspect_daily_development_inputs(self.output)
        self.assertEqual(2, len(result["stock_days"]))
        row = result["stock_days"][1]
        self.assertEqual((0, 380, None), (row["observed_unique_minutes"], row["unobserved_minute_slots"], row["first_bar_start"]))
        self.assertEqual("BLOCKED", result["partitions"][1]["readiness"]["status"])
        self.assertIn("minimum_continuous_pair_not_ready", result["reasons"])

    def test_checkpoint_can_cancel_during_loading_without_writes(self):
        prepare_daily_requests(self.selection, self.source, self.output)
        calls = 0
        def checkpoint():
            nonlocal calls
            calls += 1
            if calls == 10:
                raise RuntimeError("cancelled input review")
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            review.inspect_daily_development_inputs(self.output, checkpoint=checkpoint)
        self.assertEqual(10, calls)
        self.assertFalse((self.output / "research.sqlite3").exists())


if __name__ == "__main__":
    unittest.main()
