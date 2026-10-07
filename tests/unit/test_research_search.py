from __future__ import annotations

import unittest
from unittest.mock import patch

from kiwoom_monitor.application.research_search import (
    SEARCH_VERSION,
    ExperimentSpec,
    TrialOutcome,
    apply_selection_constraints,
    generate_trials,
    outcome_from_report,
    run_limited_search,
    TrialAttemptInterrupted,
)


def _spec(**changes) -> ExperimentSpec:
    values = {
        "version": SEARCH_VERSION,
        "hypothesis_refs": ("hypothesis-1",),
        "dataset_id": "dataset-1",
        "dataset_hash": "abc",
        "family_allowlist": ("krx_bar_close_breakout/v1",),
        "factor_allowlist": ("rolling_high_breakout/v1", "rank_persistence/v1"),
        "parameter_space": {"lookback_bars": (2, 3), "target_bps": (300, 500)},
        "objective": {"net_pnl_won": "maximize", "max_drawdown_won": "minimize"},
        "constraints": {"minimum_closed_trades": 5},
        "split_version": "chronological_holdout/v1",
        "max_trials": 3,
        "max_seconds": 60,
        "seed": 7,
        "research_context": {
            "family": "krx_bar_close_breakout/v1",
            "baseline_strategy": {"target_bps": 500},
            "execution": {"initial_cash_won": 1_000_000},
            "evaluation": {"folds": ["train"]},
            "implementation_hash": "implementation-1",
        },
    }
    values.update(changes)
    return ExperimentSpec(**values)


class ResearchSearchTests(unittest.TestCase):
    def test_same_spec_and_seed_generate_same_bounded_candidates(self) -> None:
        first = generate_trials(_spec())
        second = generate_trials(_spec())
        self.assertEqual(first, second)
        self.assertEqual({}, first[0].parameters)
        self.assertEqual("no_trade_baseline", first[1].variant)
        self.assertEqual(6, len(first))

    def test_one_parameter_mode_never_changes_two_parameters_in_one_trial(self) -> None:
        trials = generate_trials(_spec(
            generation_mode="one_parameter_at_a_time",
            include_no_trade_baseline=False,
        ))
        parameter_trials = [trial for trial in trials if trial.variant == "single_parameter"]
        self.assertEqual(4, len(parameter_trials))
        self.assertTrue(all(len(trial.parameters) == 1 for trial in parameter_trials))
        self.assertEqual(
            {"lookback_bars", "target_bps"},
            {next(iter(trial.parameters)) for trial in parameter_trials},
        )

    def test_cpu_duty_budget_pauses_between_trials(self) -> None:
        clock = iter((0.0, 0.0, 0.0, 2.0, 2.0))
        pauses = []
        cards = run_limited_search(
            _spec(
                max_trials=1,
                include_no_trade_baseline=False,
                resource_budget={"max_concurrent_trials": 1, "cpu_duty_percent": 50},
            ),
            existing_trials=(),
            evaluate=lambda _: TrialOutcome(status="COMPLETED"),
            record=lambda *_: None,
            monotonic=lambda: next(clock),
            sleeper=pauses.append,
        )
        self.assertEqual(1, len(cards))
        self.assertEqual([2.0], pauses)

    def test_objective_change_creates_new_experiment(self) -> None:
        first = _spec()
        second = _spec(objective={"max_drawdown_won": "minimize"})
        self.assertNotEqual(first.experiment_id, second.experiment_id)

    def test_scientific_context_changes_identity_but_operating_budget_does_not(self) -> None:
        first = _spec()
        changed_strategy = _spec(research_context={
            **first.research_context, "baseline_strategy": {"target_bps": 900},
        })
        changed_execution = _spec(research_context={
            **first.research_context, "execution": {"initial_cash_won": 2_000_000},
        })
        changed_fold = _spec(research_context={
            **first.research_context, "evaluation": {"folds": ["later-train"]},
        })
        operating_change = _spec(
            max_seconds=120,
            resource_budget={"max_concurrent_trials": 1, "cpu_duty_percent": 50},
        )
        self.assertNotEqual(first.experiment_id, changed_strategy.experiment_id)
        self.assertNotEqual(first.experiment_id, changed_execution.experiment_id)
        self.assertNotEqual(first.experiment_id, changed_fold.experiment_id)
        self.assertEqual(first.experiment_id, operating_change.experiment_id)

    def test_candidate_limit_is_checked_before_cartesian_product_is_built(self) -> None:
        bounded = _spec(resource_budget={
            "max_concurrent_trials": 1, "max_generated_candidates": 2,
        })
        with patch(
            "kiwoom_monitor.application.research_search.itertools.product",
            side_effect=AssertionError("product must not be materialized"),
        ):
            with self.assertRaisesRegex(ValueError, "max_generated_candidates"):
                generate_trials(bounded)

    def test_selection_uses_development_folds_and_never_opened_oos(self) -> None:
        development = {
            "role": "VALIDATION", "status": "ELIGIBLE", "net_realized_pnl_won": 10,
            "max_drawdown_won": 3, "closed_trade_count": 2, "active_day_count": 1,
        }
        opened = outcome_from_report("run", {
            "status": "ELIGIBLE", "reasons": [], "fold_reports": [development, {
                "role": "OOS", "status": "ELIGIBLE", "net_realized_pnl_won": 900,
                "max_drawdown_won": 800, "closed_trade_count": 20, "active_day_count": 5,
            }],
        })
        sealed = outcome_from_report("run", {
            "status": "ELIGIBLE_WITH_SEALED_HOLDOUT", "reasons": [],
            "fold_reports": [development, {"role": "OOS", "status": "SEALED"}],
        })
        self.assertEqual("COMPLETED", opened.status)
        self.assertEqual("COMPLETED", sealed.status)
        self.assertEqual(10, opened.net_pnl_won)
        self.assertEqual(10, sealed.net_pnl_won)
        self.assertEqual(3, opened.max_drawdown_won)

    def test_final_failure_and_global_reasons_cannot_change_development_outcome(self):
        development = {
            'name': 'validation', 'role': 'VALIDATION', 'status': 'ELIGIBLE',
            'reasons': [], 'net_realized_pnl_won': 10, 'max_drawdown_won': 3,
            'closed_trade_count': 2, 'active_day_count': 1,
        }
        first = outcome_from_report('run', {
            'status': 'ELIGIBLE', 'reasons': [], 'fold_reports': [development],
        })
        changed_final = outcome_from_report('run', {
            'status': 'INELIGIBLE', 'reasons': ['final_sample_shortage'],
            'data_quality': {'reasons': ['final_missing']},
            'fold_reports': [development, {
                'role': 'OOS', 'status': 'INELIGIBLE', 'reasons': ['final_failure'],
                'net_realized_pnl_won': -10**30, 'max_drawdown_won': 10**30,
                'closed_trade_count': 9999, 'active_day_count': 9999,
            }],
        })
        self.assertEqual(first, changed_final)

    def test_development_failure_cannot_be_overridden_by_global_eligibility(self):
        outcome = outcome_from_report('run', {
            'status': 'ELIGIBLE', 'reasons': [], 'fold_reports': [{
                'role': 'TRAIN', 'status': 'INELIGIBLE', 'reasons': ['training_missing'],
                'closed_trade_count': 0, 'active_day_count': 0,
            }],
        })
        self.assertEqual('INELIGIBLE', outcome.status)
        self.assertIn('training_missing', outcome.reasons)

    def test_opening_final_does_not_change_candidate_selection_input(self):
        development = {
            'role': 'TRAIN', 'status': 'ELIGIBLE', 'reasons': [],
            'net_realized_pnl_won': 12, 'max_drawdown_won': 4,
            'closed_trade_count': 3, 'active_day_count': 1,
        }
        sealed = outcome_from_report('run', {
            'status': 'ELIGIBLE_WITH_SEALED_HOLDOUT', 'reasons': [],
            'fold_reports': [development, {'role': 'OOS', 'status': 'SEALED'}],
        })
        opened = outcome_from_report('run', {
            'status': 'ELIGIBLE', 'reasons': [],
            'fold_reports': [development, {'role': 'OOS', 'status': 'ELIGIBLE'}],
        })
        self.assertEqual(sealed, opened)

    def test_final_canary_leaves_every_candidate_card_unchanged(self):
        def cards(final_status, global_status, final_net):
            def evaluate(trial):
                return outcome_from_report('run-' + trial.trial_id, {
                    'status': global_status, 'reasons': ['final_' + final_status],
                    'fold_reports': [{
                        'role': 'VALIDATION', 'status': 'ELIGIBLE', 'reasons': [],
                        'net_realized_pnl_won': int(trial.parameters.get('target_bps', 0)),
                        'max_drawdown_won': 2, 'closed_trade_count': 6, 'active_day_count': 2,
                    }, {
                        'role': 'OOS', 'status': final_status,
                        'net_realized_pnl_won': final_net, 'max_drawdown_won': abs(final_net),
                    }],
                })
            return run_limited_search(_spec(), existing_trials=(), evaluate=evaluate, record=lambda *_: None)
        self.assertEqual(cards('ELIGIBLE', 'ELIGIBLE', 10**30), cards('INELIGIBLE', 'INELIGIBLE', -10**30))

    def test_unknown_development_status_is_not_eligible(self):
        outcome = outcome_from_report('run', {
            'status': 'ELIGIBLE', 'fold_reports': [{
                'role': 'TRAIN', 'status': 'NEW_UNREGISTERED_STATUS',
            }],
        })
        self.assertEqual('INELIGIBLE', outcome.status)
        self.assertIn('search_development_fold_status_invalid', outcome.reasons)

    def test_final_only_report_has_no_development_metrics(self):
        outcome = outcome_from_report('run', {
            'status': 'ELIGIBLE', 'reasons': ['final_winner'],
            'fold_reports': [{'role': 'OOS', 'status': 'ELIGIBLE', 'net_realized_pnl_won': 'not_a_number'}],
        })
        self.assertEqual('INELIGIBLE', outcome.status)
        self.assertIsNone(outcome.net_pnl_won)
        self.assertEqual(0, outcome.closed_trade_count)
        self.assertEqual(('search_development_folds_missing',), outcome.reasons)

    def test_hard_constraint_cannot_enter_parameter_space(self) -> None:
        with self.assertRaisesRegex(ValueError, "hard strategy constraints"):
            _spec(parameter_space={"quantity": (1, 2)})

    def test_failed_and_ineligible_trials_consume_budget_and_resume_skips_them(self) -> None:
        spec = _spec(max_trials=2)
        recorded = []
        outcomes = iter((
            TrialOutcome(status="FAILED", error="boom"),
            TrialOutcome(status="INELIGIBLE", closed_trade_count=0),
        ))
        cards = run_limited_search(
            spec, existing_trials=(), evaluate=lambda _: next(outcomes),
            record=lambda trial, outcome, card: recorded.append((trial, outcome, card)),
        )
        self.assertEqual(2, len(cards))
        existing = [dict(trial.to_dict(), status=outcome.status) for trial, outcome, _ in recorded]
        resumed = run_limited_search(
            spec, existing_trials=existing,
            evaluate=lambda _: self.fail("completed budget must not be retried"),
            record=lambda *_: None,
        )
        self.assertEqual((), resumed)

    def test_zero_budget_and_elapsed_deadline_run_nothing(self) -> None:
        calls = []
        zero = run_limited_search(
            _spec(max_trials=0), existing_trials=(),
            evaluate=lambda trial: calls.append(trial) or TrialOutcome(status="COMPLETED"),
            record=lambda *_: None,
        )
        clock = iter((10.0, 11.0))
        timed = run_limited_search(
            _spec(max_seconds=1), existing_trials=(),
            evaluate=lambda trial: calls.append(trial) or TrialOutcome(status="COMPLETED"),
            record=lambda *_: None, monotonic=lambda: next(clock),
        )
        self.assertEqual((), zero)
        self.assertEqual((), timed)
        self.assertEqual([], calls)

    def test_slice_expiry_during_trial_stops_only_the_next_trial(self) -> None:
        now = [0.0]
        recorded = []

        def evaluate(_trial):
            now[0] = 2.0
            return TrialOutcome(status="COMPLETED")

        cards = run_limited_search(
            _spec(max_trials=3, max_seconds=1), existing_trials=(), evaluate=evaluate,
            record=lambda trial, *_: recorded.append(trial.trial_id),
            monotonic=lambda: now[0],
        )
        self.assertEqual(1, len(cards))
        self.assertEqual(1, len(recorded))

    def test_interrupted_attempt_is_not_converted_to_failed_result(self) -> None:
        recorded = []
        with self.assertRaises(TrialAttemptInterrupted):
            run_limited_search(
                _spec(max_trials=1), existing_trials=(),
                evaluate=lambda _: (_ for _ in ()).throw(TrialAttemptInterrupted("stop")),
                record=lambda *values: recorded.append(values),
            )
        self.assertEqual([], recorded)

    def test_holdout_history_and_multiple_metrics_are_kept_on_card(self) -> None:
        spec = _spec(
            max_trials=1,
            final_holdout_accessed_at="2026-09-13T00:00:00+00:00",
            final_holdout_access_reason="weekly approval",
        )
        cards = run_limited_search(
            spec, existing_trials=(),
            evaluate=lambda _: TrialOutcome(
                status="COMPLETED", run_id="run-1", net_pnl_won=120,
                max_drawdown_won=40, closed_trade_count=8, active_day_count=3,
            ),
            record=lambda *_: None,
        )
        self.assertEqual(120, cards[0].net_pnl_won)
        self.assertEqual(40, cards[0].max_drawdown_won)
        self.assertEqual(8, cards[0].closed_trade_count)
        self.assertEqual("weekly approval", cards[0].holdout_access_reason)

    def test_selection_constraints_keep_original_metrics_and_mark_ineligible(self) -> None:
        outcome = apply_selection_constraints(
            TrialOutcome(
                status="COMPLETED", net_pnl_won=50, max_drawdown_won=80,
                closed_trade_count=2, active_day_count=1,
            ),
            {"minimum_closed_trades": 3, "maximum_drawdown_won": 70},
        )
        self.assertEqual("INELIGIBLE", outcome.status)
        self.assertEqual(50, outcome.net_pnl_won)
        self.assertIn("search_minimum_closed_trades_not_met", outcome.reasons)
        self.assertIn("search_maximum_drawdown_exceeded", outcome.reasons)

    def test_second_family_has_its_own_registered_parameters(self) -> None:
        second = _spec(
            family_allowlist=("krx_pullback_reacceleration/v1",),
            factor_allowlist=("pullback_reacceleration/v1",),
            parameter_space={
                "lookback_bars": (3,), "minimum_pullback_bps": (300, 500),
                "minimum_reacceleration_bps": (50,),
            },
            generation_mode="free_research",
            execution_environment="historical_simulation",
        )
        self.assertEqual("free_research", second.generation_mode)
        self.assertTrue(generate_trials(second))
        with self.assertRaisesRegex(ValueError, "unregistered parameter"):
            _spec(
                family_allowlist=("krx_pullback_reacceleration/v1",),
                factor_allowlist=("pullback_reacceleration/v1",),
                parameter_space={"buffer_bps": (0,)},
            )

    def test_live_execution_and_unbounded_candidate_plan_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "historical_simulation"):
            _spec(execution_environment="live")
        bounded = _spec(resource_budget={
            "max_concurrent_trials": 1, "max_generated_candidates": 2,
            "max_retained_jobs": 10, "memory_mb": 128,
        })
        with self.assertRaisesRegex(ValueError, "max_generated_candidates"):
            generate_trials(bounded)
        with self.assertRaisesRegex(ValueError, "manual research"):
            _spec(generation_mode="manual")
        with self.assertRaisesRegex(ValueError, "budget"):
            _spec(max_seconds=0)
        with self.assertRaisesRegex(ValueError, "cpu duty"):
            _spec(resource_budget={"max_concurrent_trials": 1, "cpu_duty_percent": 5})


if __name__ == "__main__":
    unittest.main()
