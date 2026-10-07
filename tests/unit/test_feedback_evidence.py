from __future__ import annotations

import unittest
import uuid
import tempfile
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from kiwoom_monitor.application.breakout_strategy import default_shadow_breakout_config
from kiwoom_monitor.application.forward_evaluation import (
    build_feedback_evidence,
    build_feedback_review_revision,
    feedback_evidence_from_dict,
    feedback_improvement_from_dict,
    feedback_review_from_dict,
    forward_evidence_from_feedback,
    generate_feedback_improvement_proposals,
)
from kiwoom_monitor.application.feedback_strategy_revision import (
    build_feedback_revalidation_spec,
    build_feedback_strategy_version,
    dispatch_feedback_revalidation,
    feedback_revalidation_request_from_dict,
    feedback_revalidation_receipt_from_dict,
    feedback_strategy_version_from_dict,
)
from kiwoom_monitor.application.journal_enrichment import journal_research_link
from kiwoom_monitor.application.trade_cost_service import DailyTradeCost
from kiwoom_monitor.application.trade_history_query_service import (
    TradeHistoryReconciliationResult,
    reconcile_trade_fills,
)
from kiwoom_monitor.application.trade_history_service import TradeFill
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.domain.execution_activation import (
    ForwardCriteria,
    ForwardEvaluationSpec,
)
from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope
from kiwoom_monitor.application.research_families import BREAKOUT_FAMILY_ID
from kiwoom_monitor.application.research_queue import (
    ResearchCampaignPolicy,
    build_research_job_identity,
)
from kiwoom_monitor.application.research_search import ExperimentSpec, SEARCH_VERSION
from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import (
    ForwardEvaluationRepository,
)
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository


KST = timezone(timedelta(hours=9))
START = datetime(2026, 9, 16, tzinfo=KST)
END = START + timedelta(days=1)


def _scope() -> AccountScope:
    return AccountScope("kiwoom", AccountEnvironment.MOCK, str(uuid.uuid4()))


def _summary(scope: AccountScope, order: str, side: str, hour: int, price: int, quantity: int = 1):
    return TradeFill(
        order, "005930", "삼성전자", side,
        datetime(2026, 9, 16, hour), quantity, price,
        origin_scope=scope,
    )


def _detail(
    scope: AccountScope, order: str, execution: str, side: str, hour: int,
    price: int, quantity: int = 1, run_id: str = "run-1",
):
    return {
        "origin_broker": scope.broker,
        "origin_environment": scope.environment.value,
        "origin_account_ref": scope.account_ref,
        "canonical_account_ref": scope.account_ref,
        "source_event_id": f"source-{execution}",
        "broker_execution_id": execution,
        "broker_order_id": order,
        "intent_id": f"intent-{order}",
        "run_id": run_id,
        "decision_id": f"decision-{order}",
        "stock_code": "005930",
        "venue": "KRX",
        "side": side,
        "occurred_at": datetime(2026, 9, 16, hour, tzinfo=KST).isoformat(),
        "quantity": quantity,
        "price": price,
    }


def _cost(scope: AccountScope, side: str, amount: int) -> DailyTradeCost:
    return DailyTradeCost(
        date(2026, 9, 16), date(2026, 9, 18), "005930", side,
        0, 0, 0, 0, amount, origin_scope=scope,
    )


def _history(
    scope: AccountScope, *, partial_buy: bool = False, include_sell_cost: bool = True,
) -> TradeHistoryReconciliationResult:
    summaries = (
        _summary(scope, "buy-1", "매수", 9, 100, 2 if partial_buy else 1),
        _summary(scope, "sell-1", "매도", 10, 110),
    )
    details = (
        _detail(scope, "buy-1", "buy-fill", "BUY", 9, 100),
        _detail(scope, "sell-1", "sell-fill", "SELL", 10, 110),
    )
    costs = (_cost(scope, "매수", 1),)
    if include_sell_cost:
        costs += (_cost(scope, "매도", 2),)
    return TradeHistoryReconciliationResult(
        reconcile_trade_fills(summaries, details, scope), costs,
    )


def _link(scope: AccountScope, timing: str = "at_execution"):
    return journal_research_link(
        "source-buy-fill", run_id="run-1", decision_id="decision-buy-1",
        evidence_timing=timing, account_scope=scope,
        now=datetime(2026, 9, 16, 9, 0),
    )


def _build(scope: AccountScope, **changes):
    values = {
        "account_scope": scope,
        "strategy_ref": "strategy-1",
        "evaluation_start": START,
        "evaluation_end": END,
        "evidence_as_of": END,
        "frozen_at": END + timedelta(minutes=1),
        "history": _history(scope),
        "finalized": True,
        "selected_run_ids": ("run-1",),
        "selection_declared_at": START - timedelta(days=1),
        "research_links": (_link(scope),),
        "research_exposure_status": "DEVELOPMENT_ONLY",
    }
    values.update(changes)
    return build_feedback_evidence(**values)


def _research_template() -> ExperimentSpec:
    return ExperimentSpec(
        version=SEARCH_VERSION,
        hypothesis_refs=("manual-template",),
        dataset_id="dataset-1",
        dataset_hash="dataset-hash-1",
        family_allowlist=(BREAKOUT_FAMILY_ID,),
        factor_allowlist=("rolling_high_breakout/v1", "rank_persistence/v1"),
        parameter_space={"lookback_bars": (3,)},
        objective={"net_pnl_won": "maximize"},
        constraints={},
        split_version="chronological_holdout/v1",
        max_trials=2,
        max_seconds=10,
        seed=7,
        research_context={
            "family": BREAKOUT_FAMILY_ID,
            "baseline_strategy": default_shadow_breakout_config().to_dict(),
            "execution": {"fixture": "execution"},
            "evaluation": {"fixture": "evaluation"},
            "implementation_hash": "fixture",
        },
        resource_budget={
            "max_concurrent_trials": 1,
            "max_generated_candidates": 10,
            "max_retained_jobs": 100,
            "memory_mb": 512,
            "cpu_duty_percent": 50,
        },
    )


class FeedbackEvidenceTests(unittest.TestCase):
    def test_confirmed_closed_trade_freezes_actual_cost_once(self) -> None:
        scope = _scope()

        evidence = _build(scope)

        self.assertTrue(evidence.eligible_for_strategy_feedback)
        self.assertEqual((1, 1), (evidence.active_day_count, evidence.closed_trade_count))
        self.assertEqual(7, evidence.broker_net_pnl_won)
        self.assertEqual(3, evidence.broker_reported_cost_won)
        self.assertEqual({"EXACT": 2, "DETAIL_ONLY": 0, "SUMMARY_ONLY": 0,
                          "PARTIAL": 0, "CONFLICT": 0}, evidence.fill_quality_counts)
        self.assertEqual(("buy-fill", "sell-fill"), evidence.broker_execution_ids)

    def test_partial_fill_or_missing_cost_never_exports_confirmed_pnl(self) -> None:
        scope = _scope()
        partial = _build(scope, history=_history(scope, partial_buy=True))
        missing_cost = _build(scope, history=_history(scope, include_sell_cost=False))

        self.assertIsNone(partial.broker_net_pnl_won)
        self.assertIn("fill_partial", partial.outcomes[0].reasons)
        self.assertFalse(partial.eligible_for_strategy_feedback)
        self.assertIsNone(missing_cost.broker_net_pnl_won)
        self.assertIn("broker_cost_missing", missing_cost.outcomes[0].reasons)

    def test_post_hoc_or_post_trade_selection_is_visible_and_ineligible(self) -> None:
        scope = _scope()
        post_hoc = _build(scope, selection_declared_at=START + timedelta(hours=1))
        post_trade = _build(scope, research_links=(_link(scope, "post_trade"),))
        final_exposed = _build(scope, research_exposure_status="FINAL_EXPOSED")

        self.assertEqual("POST_HOC", post_hoc.selection_bias_status)
        self.assertFalse(post_hoc.eligible_for_strategy_feedback)
        self.assertEqual("POST_TRADE_INCLUDED", post_trade.point_in_time_status)
        self.assertFalse(post_trade.eligible_for_strategy_feedback)
        self.assertEqual("FINAL_EXPOSED", final_exposed.research_exposure_status)
        self.assertFalse(final_exposed.eligible_for_strategy_feedback)

    def test_cross_account_research_link_is_rejected(self) -> None:
        scope = _scope()

        with self.assertRaisesRegex(ValueError, "crossed"):
            _build(scope, research_links=(_link(_scope()),))

    def test_unrelated_research_link_cannot_claim_point_in_time_safety(self) -> None:
        scope = _scope()
        unrelated = journal_research_link(
            "unrelated-execution", run_id="other-run", decision_id="other-decision",
            evidence_timing="at_execution", account_scope=scope,
            now=datetime(2026, 9, 16, 9, 0),
        )

        evidence = _build(scope, research_links=(unrelated,))

        self.assertEqual("UNVERIFIED", evidence.point_in_time_status)
        self.assertEqual((), evidence.research_link_ids)
        self.assertFalse(evidence.eligible_for_strategy_feedback)

    def test_content_id_roundtrip_and_repository_are_immutable(self) -> None:
        scope = _scope()
        evidence = _build(scope)
        self.assertEqual(evidence, feedback_evidence_from_dict(evidence.to_dict()))
        tampered = evidence.to_dict()
        tampered["broker_net_pnl_won"] = 999
        with self.assertRaisesRegex(ValueError, "evidence_id"):
            feedback_evidence_from_dict(tampered)

        store = SQLiteQueryStore(Path(":memory:"))
        store.initialize()
        self.addCleanup(store.close)
        repository = ForwardEvaluationRepository(store)
        self.assertTrue(repository.save_feedback_evidence(evidence))
        self.assertFalse(repository.save_feedback_evidence(evidence))
        self.assertEqual((evidence,), repository.load_feedback_evidence("strategy-1"))

    def test_only_eligible_feedback_can_enter_forward_evaluation(self) -> None:
        scope = _scope()
        feedback = _build(scope)
        spec = ForwardEvaluationSpec(
            "strategy-1", "family/v1", ("factor/v1",), "policy/v1", "nas",
            scope.account_ref, "mock", START, END, START - timedelta(days=2),
            ForwardCriteria(),
        )

        adapted = forward_evidence_from_feedback(
            spec, feedback,
            validation_summary={"comparable_count": 1, "unmatched_count": 0,
                                "arrival_gap_ms": {"p95": 10}},
            execution_events=(), additional_unmodeled_cost_won=0,
            max_drawdown_ppm=0, exposure_ppm=100,
        )

        self.assertEqual(7, adapted.broker_net_pnl_won)
        self.assertEqual(3, adapted.broker_reported_cost_won)
        with self.assertRaisesRegex(ValueError, "not eligible"):
            forward_evidence_from_feedback(
                spec,
                replace(feedback, eligible_for_strategy_feedback=False),
                validation_summary={}, execution_events=(),
            )

    def test_machine_review_is_deterministic_and_uses_broker_net_once(self) -> None:
        scope = _scope()
        feedback = _build(scope)

        first = build_feedback_review_revision(
            feedback, minimum_closed_trade_count=1, minimum_active_day_count=1,
        )
        second = build_feedback_review_revision(
            feedback, minimum_closed_trade_count=1, minimum_active_day_count=1,
        )

        self.assertEqual(first, second)
        self.assertEqual("POSITIVE", first.assessment)
        self.assertTrue(first.eligible_for_improvement_proposal)
        self.assertEqual(10, first.metrics["gross_realized_pnl_won"])
        self.assertEqual(3, first.metrics["broker_reported_cost_won"])
        self.assertEqual(7, first.metrics["broker_net_pnl_won"])
        self.assertEqual(1_000_000, first.metrics["win_rate_ppm"])
        self.assertEqual(300_000, first.metrics["cost_to_abs_gross_ppm"])

    def test_machine_review_blocks_ineligible_or_insufficient_feedback(self) -> None:
        scope = _scope()
        insufficient = build_feedback_review_revision(
            _build(scope), minimum_closed_trade_count=2, minimum_active_day_count=1,
        )
        blocked = build_feedback_review_revision(
            _build(scope, selection_declared_at=START + timedelta(hours=1)),
            minimum_closed_trade_count=1,
            minimum_active_day_count=1,
        )

        self.assertEqual("INSUFFICIENT_SAMPLE", insufficient.assessment)
        self.assertFalse(insufficient.eligible_for_improvement_proposal)
        self.assertIn("minimum_closed_trade_count_not_met", insufficient.reasons)
        self.assertEqual("BLOCKED", blocked.assessment)
        self.assertFalse(blocked.eligible_for_improvement_proposal)
        self.assertIn("source_feedback_not_eligible", blocked.reasons)

    def test_machine_review_roundtrip_requires_stored_source_evidence(self) -> None:
        scope = _scope()
        feedback = _build(scope)
        review = build_feedback_review_revision(
            feedback, minimum_closed_trade_count=1, minimum_active_day_count=1,
        )
        self.assertEqual(review, feedback_review_from_dict(review.to_dict()))
        tampered = review.to_dict()
        tampered["assessment"] = "NEGATIVE"
        with self.assertRaisesRegex(ValueError, "review_id"):
            feedback_review_from_dict(tampered)

        store = SQLiteQueryStore(Path(":memory:"))
        store.initialize()
        self.addCleanup(store.close)
        repository = ForwardEvaluationRepository(store)
        with self.assertRaisesRegex(ValueError, "source evidence"):
            repository.save_feedback_review(review)
        repository.save_feedback_evidence(feedback)
        self.assertTrue(repository.save_feedback_review(review))
        self.assertFalse(repository.save_feedback_review(review))
        self.assertEqual((review,), repository.load_feedback_reviews("strategy-1"))

    def test_feedback_improvements_are_registered_single_parameter_counterfactuals(self) -> None:
        scope = _scope()
        review = build_feedback_review_revision(
            _build(scope), minimum_closed_trade_count=1, minimum_active_day_count=1,
        )
        baseline = default_shadow_breakout_config().to_dict()
        values = {"lookback_bars": (5, 7), "target_bps": (500, 600)}

        proposals = generate_feedback_improvement_proposals(
            review,
            family_id=BREAKOUT_FAMILY_ID,
            factor_allowlist=("rolling_high_breakout/v1", "rank_persistence/v1"),
            baseline_parameters=baseline,
            allowed_parameter_values=values,
            seed=17,
            max_proposals=10,
        )
        reordered = generate_feedback_improvement_proposals(
            review,
            family_id=BREAKOUT_FAMILY_ID,
            factor_allowlist=("rolling_high_breakout/v1", "rank_persistence/v1"),
            baseline_parameters=baseline,
            allowed_parameter_values=values,
            seed=29,
            max_proposals=10,
        )

        self.assertEqual(2, len(proposals))
        self.assertEqual(
            {item.proposal_id for item in proposals},
            {item.proposal_id for item in reordered},
        )
        for proposal in proposals:
            changed = [
                key for key, value in proposal.proposed_parameters.items()
                if proposal.baseline_parameters[key] != value
            ]
            self.assertEqual([proposal.changed_parameter], changed)
            self.assertEqual("READY_FOR_REVIEW", proposal.status)
            self.assertEqual("positive_counterfactual_refinement", proposal.rationale_code)

    def test_feedback_improvement_rejects_blocked_review_and_unregistered_parameter(self) -> None:
        scope = _scope()
        blocked = build_feedback_review_revision(
            _build(scope), minimum_closed_trade_count=2, minimum_active_day_count=1,
        )
        baseline = default_shadow_breakout_config().to_dict()
        common = {
            "family_id": BREAKOUT_FAMILY_ID,
            "factor_allowlist": ("rolling_high_breakout/v1", "rank_persistence/v1"),
            "baseline_parameters": baseline,
            "seed": 1,
            "max_proposals": 1,
        }
        with self.assertRaisesRegex(ValueError, "not eligible"):
            generate_feedback_improvement_proposals(
                blocked, allowed_parameter_values={"lookback_bars": (7,)}, **common,
            )
        eligible = build_feedback_review_revision(
            _build(scope), minimum_closed_trade_count=1, minimum_active_day_count=1,
        )
        with self.assertRaisesRegex(ValueError, "unregistered parameter"):
            generate_feedback_improvement_proposals(
                eligible, allowed_parameter_values={"quantity": (2,)}, **common,
            )

    def test_feedback_improvement_roundtrip_and_repository_require_stored_review(self) -> None:
        scope = _scope()
        feedback = _build(scope)
        review = build_feedback_review_revision(
            feedback, minimum_closed_trade_count=1, minimum_active_day_count=1,
        )
        proposal = generate_feedback_improvement_proposals(
            review,
            family_id=BREAKOUT_FAMILY_ID,
            factor_allowlist=("rolling_high_breakout/v1", "rank_persistence/v1"),
            baseline_parameters=default_shadow_breakout_config().to_dict(),
            allowed_parameter_values={"lookback_bars": (7,)},
            seed=1,
            max_proposals=1,
        )[0]
        self.assertEqual(proposal, feedback_improvement_from_dict(proposal.to_dict()))
        tampered = proposal.to_dict()
        tampered["changed_to"] = 9
        with self.assertRaises(ValueError):
            feedback_improvement_from_dict(tampered)

        store = SQLiteQueryStore(Path(":memory:"))
        store.initialize()
        self.addCleanup(store.close)
        repository = ForwardEvaluationRepository(store)
        with self.assertRaisesRegex(ValueError, "source review"):
            repository.save_feedback_improvement(proposal)
        repository.save_feedback_evidence(feedback)
        repository.save_feedback_review(review)
        self.assertTrue(repository.save_feedback_improvement(proposal))
        self.assertFalse(repository.save_feedback_improvement(proposal))
        self.assertEqual(
            (proposal,), repository.load_feedback_improvements("strategy-1"),
        )

    def test_adopted_proposal_creates_new_version_and_one_revalidation_job(self) -> None:
        scope = _scope()
        feedback = _build(scope)
        review = build_feedback_review_revision(
            feedback, minimum_closed_trade_count=1, minimum_active_day_count=1,
        )
        proposal = generate_feedback_improvement_proposals(
            review,
            family_id=BREAKOUT_FAMILY_ID,
            factor_allowlist=("rolling_high_breakout/v1", "rank_persistence/v1"),
            baseline_parameters=default_shadow_breakout_config().to_dict(),
            allowed_parameter_values={"lookback_bars": (7,)},
            seed=1,
            max_proposals=1,
        )[0]
        store = SQLiteQueryStore(Path(":memory:"))
        store.initialize()
        self.addCleanup(store.close)
        versions = ForwardEvaluationRepository(store)
        versions.save_feedback_evidence(feedback)
        versions.save_feedback_review(review)
        versions.save_feedback_improvement(proposal)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        research = ResearchRepository(root / "research.sqlite3")
        research.create_campaign("feedback", "피드백 재검증", ResearchCampaignPolicy())
        template = _research_template()
        research.enqueue_campaign_experiment("feedback", template, root / "dataset")
        template_job_id = build_research_job_identity(template).job_id

        version, receipt = dispatch_feedback_revalidation(
            versions,
            research,
            proposal,
            campaign_id="feedback",
            template_job_id=template_job_id,
            adoption_reason="모의 체결 피드백의 한 변수 재검증",
        )
        repeated = dispatch_feedback_revalidation(
            versions,
            research,
            proposal,
            campaign_id="feedback",
            template_job_id=template_job_id,
            adoption_reason="모의 체결 피드백의 한 변수 재검증",
        )

        self.assertEqual((version, receipt), repeated)
        self.assertNotEqual(feedback.strategy_ref, version.version_id)
        self.assertEqual(feedback.strategy_ref, version.parent_strategy_ref)
        self.assertEqual(proposal.proposed_parameters, version.parameters)
        self.assertEqual(
            version, feedback_strategy_version_from_dict(version.to_dict()),
        )
        self.assertEqual(
            receipt, feedback_revalidation_receipt_from_dict(receipt.to_dict()),
        )
        requests = versions.load_feedback_revalidation_requests("strategy-1")
        self.assertEqual(1, len(requests))
        self.assertEqual(
            requests[0], feedback_revalidation_request_from_dict(requests[0].to_dict()),
        )
        self.assertEqual(receipt.request_id, requests[0].request_id)
        jobs = research.load_campaign_jobs("feedback")
        self.assertEqual(2, len(jobs))
        queued = next(job for job in jobs if job["job_id"] == receipt.job_id)
        queued_spec = ExperimentSpec.from_dict(queued["request"])
        self.assertEqual("hypothesis", queued["source_kind"])
        self.assertEqual((version.version_id,), queued_spec.hypothesis_refs)
        self.assertEqual({}, queued_spec.parameter_space)
        self.assertEqual(proposal.proposed_parameters, queued_spec.research_context["baseline_strategy"])
        self.assertEqual(
            (version,), versions.load_feedback_strategy_versions("strategy-1"),
        )
        self.assertEqual(
            (receipt,), versions.load_feedback_revalidation_receipts("feedback"),
        )
        research.create_campaign("other", "다른 재검증", ResearchCampaignPolicy())
        research.enqueue_campaign_experiment("other", template, root / "dataset")
        with self.assertRaisesRegex(ValueError, "another revalidation request"):
            dispatch_feedback_revalidation(
                versions,
                research,
                proposal,
                campaign_id="other",
                template_job_id=template_job_id,
                adoption_reason="모의 체결 피드백의 한 변수 재검증",
            )
        self.assertEqual(1, len(research.load_campaign_jobs("other")))

    def test_strategy_version_requires_stored_proposal_and_one_adoption_reason(self) -> None:
        scope = _scope()
        feedback = _build(scope)
        review = build_feedback_review_revision(
            feedback, minimum_closed_trade_count=1, minimum_active_day_count=1,
        )
        proposal = generate_feedback_improvement_proposals(
            review,
            family_id=BREAKOUT_FAMILY_ID,
            factor_allowlist=("rolling_high_breakout/v1", "rank_persistence/v1"),
            baseline_parameters=default_shadow_breakout_config().to_dict(),
            allowed_parameter_values={"lookback_bars": (7,)},
            seed=1,
            max_proposals=1,
        )[0]
        first = build_feedback_strategy_version(proposal, adoption_reason="첫 채택")
        second = build_feedback_strategy_version(proposal, adoption_reason="다른 채택")
        store = SQLiteQueryStore(Path(":memory:"))
        store.initialize()
        self.addCleanup(store.close)
        repository = ForwardEvaluationRepository(store)

        with self.assertRaisesRegex(ValueError, "source proposal"):
            repository.save_feedback_strategy_version(first)
        repository.save_feedback_evidence(feedback)
        repository.save_feedback_review(review)
        repository.save_feedback_improvement(proposal)
        self.assertTrue(repository.save_feedback_strategy_version(first))
        self.assertFalse(repository.save_feedback_strategy_version(first))
        with self.assertRaisesRegex(ValueError, "another adopted"):
            repository.save_feedback_strategy_version(second)

    def test_revalidation_retry_after_receipt_failure_does_not_duplicate_job(self) -> None:
        class FailOnceRepository(ForwardEvaluationRepository):
            fail_receipt = True

            def save_feedback_revalidation_receipt(self, value):
                if self.fail_receipt:
                    self.fail_receipt = False
                    raise RuntimeError("receipt write failed")
                return super().save_feedback_revalidation_receipt(value)

        scope = _scope()
        feedback = _build(scope)
        review = build_feedback_review_revision(
            feedback, minimum_closed_trade_count=1, minimum_active_day_count=1,
        )
        proposal = generate_feedback_improvement_proposals(
            review,
            family_id=BREAKOUT_FAMILY_ID,
            factor_allowlist=("rolling_high_breakout/v1", "rank_persistence/v1"),
            baseline_parameters=default_shadow_breakout_config().to_dict(),
            allowed_parameter_values={"lookback_bars": (7,)},
            seed=1,
            max_proposals=1,
        )[0]
        store = SQLiteQueryStore(Path(":memory:"))
        store.initialize()
        self.addCleanup(store.close)
        versions = FailOnceRepository(store)
        versions.save_feedback_evidence(feedback)
        versions.save_feedback_review(review)
        versions.save_feedback_improvement(proposal)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        research = ResearchRepository(root / "research.sqlite3")
        research.create_campaign("feedback", "피드백 재검증", ResearchCampaignPolicy())
        template = _research_template()
        research.enqueue_campaign_experiment("feedback", template, root / "dataset")
        template_job_id = build_research_job_identity(template).job_id
        arguments = {
            "campaign_id": "feedback",
            "template_job_id": template_job_id,
            "adoption_reason": "receipt 실패 복구",
        }

        with self.assertRaisesRegex(RuntimeError, "receipt write failed"):
            dispatch_feedback_revalidation(
                versions, research, proposal, **arguments,
            )
        self.assertEqual(2, len(research.load_campaign_jobs("feedback")))
        self.assertEqual((), versions.load_feedback_revalidation_receipts("feedback"))
        self.assertEqual(1, len(versions.load_feedback_revalidation_requests("strategy-1")))

        _, receipt = dispatch_feedback_revalidation(
            versions, research, proposal, **arguments,
        )
        self.assertEqual(2, len(research.load_campaign_jobs("feedback")))
        self.assertEqual(
            (receipt,), versions.load_feedback_revalidation_receipts("feedback"),
        )

    def test_revalidation_spec_rejects_final_holdout_template(self) -> None:
        scope = _scope()
        feedback = _build(scope)
        review = build_feedback_review_revision(
            feedback, minimum_closed_trade_count=1, minimum_active_day_count=1,
        )
        proposal = generate_feedback_improvement_proposals(
            review,
            family_id=BREAKOUT_FAMILY_ID,
            factor_allowlist=("rolling_high_breakout/v1", "rank_persistence/v1"),
            baseline_parameters=default_shadow_breakout_config().to_dict(),
            allowed_parameter_values={"lookback_bars": (7,)},
            seed=1,
            max_proposals=1,
        )[0]
        version = build_feedback_strategy_version(proposal, adoption_reason="거절되어야 함")
        template = replace(
            _research_template(),
            final_holdout_accessed_at=END.isoformat(),
            final_holdout_access_reason="sealed result",
        )

        with self.assertRaisesRegex(ValueError, "final holdout"):
            build_feedback_revalidation_spec(template, version)


if __name__ == "__main__":
    unittest.main()
