from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kiwoom_monitor.application.forward_evaluation import (
    build_forward_report,
    forward_evidence_from_sources,
)
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.domain.execution_activation import (
    ForwardCriteria,
    ForwardEvaluationSpec,
    ForwardEvidence,
    ForwardReportStatus,
    StrategyLifecycleStage,
    strategy_stage_revision,
)
from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import (
    ForwardEvaluationRepository,
)


NOW = datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc)


def _criteria(**changes: int | None) -> ForwardCriteria:
    values = {
        "minimum_comparable_observations": 100,
        "minimum_coverage_ppm": 990_000,
        "maximum_p95_delay_ms": 2_000,
        "maximum_gap_count": 1,
        "maximum_submission_unknown_count": 0,
        "maximum_rejected_order_count": 1,
        "maximum_cancel_failure_count": 0,
        "maximum_reconnect_count": 2,
        "maximum_balance_mismatch_count": 0,
        "minimum_active_day_count": 5,
        "minimum_closed_trade_count": 10,
        "minimum_adjusted_net_pnl_won": 50_000,
        "maximum_drawdown_ppm": 100_000,
        "maximum_exposure_ppm": 500_000,
    }
    values.update(changes)
    return ForwardCriteria(**values)


def _spec(criteria: ForwardCriteria | None = None) -> ForwardEvaluationSpec:
    return ForwardEvaluationSpec(
        "strategy-1", "family/v1", ("factor/v1",), "policy/v1", "nas",
        "mock-account", "mock", NOW, NOW + timedelta(days=7), NOW - timedelta(days=1),
        criteria or _criteria(),
    )


def _evidence(spec: ForwardEvaluationSpec, **changes: object) -> ForwardEvidence:
    values = {
        "profile_id": spec.profile_id, "as_of": spec.evaluation_end,
        "finalized": True, "comparable_observation_count": 1_000,
        "coverage_ppm": 999_000, "p95_delay_ms": 500, "gap_count": 0,
        "submission_unknown_count": 0, "rejected_order_count": 0,
        "cancel_failure_count": 0, "reconnect_count": 0,
        "balance_mismatch_count": 0, "no_trade_count": 12,
        "active_day_count": 6, "closed_trade_count": 12,
        "broker_net_pnl_won": 100_000, "broker_reported_cost_won": 15_000,
        "additional_unmodeled_cost_won": 20_000,
        "max_drawdown_ppm": 50_000, "exposure_ppm": 200_000,
    }
    values.update(changes)
    return ForwardEvidence(**values)


class ForwardReportTests(unittest.TestCase):
    def test_completed_evidence_passes_without_double_subtracting_broker_cost(self) -> None:
        spec = _spec()
        report = build_forward_report(spec, _evidence(spec), computed_at=spec.evaluation_end)

        self.assertEqual(ForwardReportStatus.PASSED, report.status)
        self.assertTrue(report.promotion_eligible)
        self.assertEqual(80_000, report.performance_metrics["adjusted_net_pnl_won"])
        self.assertEqual(15_000, report.performance_metrics["broker_reported_cost_won"])
        self.assertEqual(set(), {gate.status for gate in report.gates} - {"PASSED"})

    def test_tbd_threshold_blocks_promotion(self) -> None:
        spec = _spec(_criteria(maximum_drawdown_ppm=None))
        report = build_forward_report(spec, _evidence(spec), computed_at=spec.evaluation_end)

        self.assertEqual(ForwardReportStatus.BLOCKED, report.status)
        self.assertFalse(report.promotion_eligible)
        self.assertIn("criterion_tbd:max_drawdown_ppm", report.reasons)

    def test_incomplete_minimum_is_pending_but_maximum_breach_fails_immediately(self) -> None:
        spec = _spec()
        pending = build_forward_report(
            spec,
            _evidence(
                spec, as_of=NOW + timedelta(days=2), finalized=False,
                active_day_count=2, closed_trade_count=2,
            ),
            computed_at=NOW + timedelta(days=2),
        )
        failed = build_forward_report(
            spec,
            _evidence(
                spec, as_of=NOW + timedelta(days=2), finalized=False,
                submission_unknown_count=1,
            ),
            computed_at=NOW + timedelta(days=2),
        )

        self.assertEqual(ForwardReportStatus.PENDING, pending.status)
        self.assertEqual(ForwardReportStatus.FAILED, failed.status)
        self.assertIn("criterion_not_met:submission_unknown_count", failed.reasons)

    def test_profile_mismatch_is_rejected_and_report_round_trips(self) -> None:
        spec = _spec()
        with self.assertRaisesRegex(ValueError, "different profile"):
            build_forward_report(
                spec, _evidence(spec, profile_id="other"), computed_at=spec.evaluation_end,
            )
        report = build_forward_report(spec, _evidence(spec), computed_at=spec.evaluation_end)
        store = SQLiteQueryStore(Path(":memory:"))
        store.initialize()
        self.addCleanup(store.close)
        repository = ForwardEvaluationRepository(store)
        self.assertTrue(repository.save_report(report))
        self.assertFalse(repository.save_report(report))
        self.assertEqual((report,), repository.load_reports(spec.profile_id))

    def test_v1_summary_and_o1_events_are_adapted_without_duplicate_incidents(self) -> None:
        spec = _spec()
        event = {"event_id": "unknown-1", "event_type": "SUBMISSION_RESPONSE_UNKNOWN"}
        evidence = forward_evidence_from_sources(
            profile_id=spec.profile_id, as_of=spec.evaluation_end, finalized=True,
            validation_summary={
                "comparable_count": 999, "unmatched_count": 1,
                "arrival_gap_ms": {"p95": 499.6},
            },
            execution_events=(
                event, event,
                {"event_id": "reject-1", "event_type": "PRECHECK_REJECTED"},
                {"event_id": "cancel-1", "event_type": "CANCEL_RESPONSE_UNKNOWN"},
                {"event_id": "no-trade-1", "event_type": "NO_TRADE"},
            ),
            performance={
                "active_day_count": 6, "closed_trade_count": 12,
                "broker_net_pnl_won": 100_000, "broker_reported_cost_won": 15_000,
                "additional_unmodeled_cost_won": 20_000,
                "max_drawdown_ppm": 50_000, "exposure_ppm": 200_000,
            },
        )

        self.assertEqual(999_000, evidence.coverage_ppm)
        self.assertEqual(500, evidence.p95_delay_ms)
        self.assertEqual(1, evidence.submission_unknown_count)
        self.assertEqual(1, evidence.rejected_order_count)
        self.assertEqual(1, evidence.cancel_failure_count)
        self.assertEqual(1, evidence.no_trade_count)

    def test_broker_mock_stage_requires_stored_final_pass_for_same_strategy(self) -> None:
        spec = _spec()
        report = build_forward_report(spec, _evidence(spec), computed_at=spec.evaluation_end)
        store = SQLiteQueryStore(Path(":memory:"))
        store.initialize()
        self.addCleanup(store.close)
        repository = ForwardEvaluationRepository(store)
        repository.save_profile(spec)
        stages = (
            (StrategyLifecycleStage.DRAFT, StrategyLifecycleStage.EVALUATED, "research-1"),
            (StrategyLifecycleStage.EVALUATED, StrategyLifecycleStage.VALIDATED, "validation-1"),
            (StrategyLifecycleStage.VALIDATED, StrategyLifecycleStage.SHADOW, "shadow-1"),
        )
        for index, (previous, stage, evidence_ref) in enumerate(stages):
            repository.save_stage_revision(strategy_stage_revision(
                strategy_ref=spec.strategy_ref, previous_stage=previous, stage=stage,
                evidence_refs=(evidence_ref,), reason="reviewed",
                decided_at=NOW + timedelta(minutes=index),
            ))
        promotion = strategy_stage_revision(
            strategy_ref=spec.strategy_ref, previous_stage=StrategyLifecycleStage.SHADOW,
            stage=StrategyLifecycleStage.BROKER_MOCK_VALIDATED,
            evidence_refs=(report.report_id,), reason="forward report reviewed",
            decided_at=NOW + timedelta(minutes=3),
        )
        with self.assertRaisesRegex(ValueError, "stored finalized PASSED"):
            repository.save_stage_revision(promotion)
        repository.save_report(report)
        self.assertTrue(repository.save_stage_revision(promotion))
        self.assertEqual(
            StrategyLifecycleStage.BROKER_MOCK_VALIDATED,
            repository.latest_stage(spec.strategy_ref),
        )


if __name__ == "__main__":
    unittest.main()
