from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kiwoom_monitor.application.breakout_strategy import default_shadow_breakout_config
from kiwoom_monitor.application.market_session_schedule import research_session_profile_document
from kiwoom_monitor.application.mock_automation_candidate import (
    MockAutomationCandidatePackage,
    MockAutomationEligibilityPolicy,
    evaluate_candidate_eligibility,
)
from kiwoom_monitor.application.mock_automation_admission import (
    admit_mock_automation,
    execution_run_id_for_spec,
)
from kiwoom_monitor.application.mock_automation_execution import (
    MockAutomationGateStatus,
    dispatch_mock_automation_decision_from_risk,
)
from kiwoom_monitor.application.mock_automation_recovery import (
    record_mock_automation_recovery_from_risk,
)
from kiwoom_monitor.application.mock_automation_risk import build_mock_automation_risk_snapshot
from kiwoom_monitor.application.breakout_strategy import StrategyDecision
from kiwoom_monitor.application.journal_enrichment import sync_journal_execution_projection
from kiwoom_monitor.application.mock_automation_specification import (
    build_mock_automation_publication_documents,
    matching_shadow_events,
    publish_ready_mock_automation_spec,
)
from kiwoom_monitor.application.research_execution import (
    COST_MODEL_VERSION,
    EXECUTION_MODEL_VERSION,
    SAME_BAR_PATH_VERSION,
    SimulationCostModel,
    SimulationExecutionConfig,
)
from kiwoom_monitor.application.research_families import (
    BREAKOUT_FAMILY_ID,
    get_research_family,
    shadow_monitor_id_for_config,
)
from kiwoom_monitor.application.research_splits import ResearchEvaluationSpec, ResearchFoldSpec
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.execution_runtime import ExecutionRuntime
from kiwoom_monitor.domain.execution_activation import (
    ForwardCriteria,
    ForwardEvaluationSpec,
    MOCK_CANDIDATE_TRANSITION_POLICY,
    MOCK_RECOVERY_POLICY,
    MOCK_STOP_POLICY,
    MockAutomationOperatingSpec,
    MockAutomationReadinessStatus,
    StrategyLifecycleStage,
    strategy_stage_revision,
)
from kiwoom_monitor.domain.order_contract import (
    AccountEnvironment, AccountScope, AccountSnapshot, BrokerFill,
    BrokerOrderSnapshot, BrokerSubmission, OrderState,
)
from kiwoom_monitor.application.order_lifecycle import OrderLifecycle
from kiwoom_monitor.application.trade_cost_service import DailyTradeCost
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import MockAccountRecovery
from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository
from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import (
    ForwardEvaluationRepository,
)
from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository


NOW = datetime(2026, 9, 21, tzinfo=timezone.utc)
ACCOUNT_REF = "af64a3fa-197f-49df-8e8b-65b71bee02d9"
PROFILE_ID = "nas-mock-default"


def _automation_decision(run_id: str, action: str, decided_at: datetime) -> StrategyDecision:
    body = {
        "run_id": run_id, "snapshot_id": f"snapshot-{action.lower()}",
        "symbol": "005930", "proposal": action, "final_action": action,
        "quantity": 1, "signal_reference_price": 70_000 if action == "ENTER" else 70_500,
        "required_capital_won": 70_000 if action == "ENTER" else 0,
        "reasons": (), "constraints": ("KRX",), "state_before": {}, "state_after": {},
        "decided_at": decided_at.isoformat(),
    }
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return StrategyDecision(
        decision_id=f"decision_{hashlib.sha256(encoded).hexdigest()}", **body,
    )


class _FakeBrokerTransport:
    def __init__(self, now_provider) -> None:
        self._now = now_provider
        self.calls = []

    def submit(self, intent):
        order_id = f"fake-order-{len(self.calls) + 1}"
        self.calls.append((order_id, intent.side.value, intent.symbol, intent.quantity))
        return BrokerSubmission(order_id, self._now())

    def cancel(self, *_args, **_kwargs):
        raise AssertionError("the fake integration does not cancel orders")


class _ExecutionPageSource:
    def __init__(self, repository: ExecutionRepository, account_ref: str) -> None:
        self._repository = repository
        self._account_ref = account_ref

    def load_mock_execution_events(self, *, after_sequence=0, limit=500):
        return self._repository.account_events(
            "mock", self._account_ref, after_sequence=after_sequence, limit=limit,
        ).to_dict()


def _hash(value):
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()


class MockAutomationSpecificationTests(unittest.TestCase):
    def setUp(self):
        self.store = SQLiteQueryStore(Path(":memory:")); self.store.initialize()
        self.addCleanup(self.store.close)
        self.store.register_account_identity({
            "broker": "kiwoom", "environment": "mock",
            "identity_fingerprint": "c" * 64, "account_ref": ACCOUNT_REF,
            "created_at": (NOW - timedelta(days=1)).isoformat(),
        })
        self.binding = self.store.append_account_binding({
            "credential_profile_id": PROFILE_ID, "broker": "kiwoom",
            "environment": "mock", "account_ref": ACCOUNT_REF,
            "verified_at": (NOW - timedelta(days=1)).isoformat(),
            "verification_method": "ka00001",
        })
        self.repository = ForwardEvaluationRepository(self.store)
        self.config = default_shadow_breakout_config()
        execution = SimulationExecutionConfig(
            EXECUTION_MODEL_VERSION, SAME_BAR_PATH_VERSION, 10_000_000,
            SimulationCostModel(
                COST_MODEL_VERSION, 2, 18, 5, "official_period_rule", "fixture",
                "2026-01-01T00:00:00+00:00", "2027-01-01T00:00:00+00:00",
            ),
        )
        candidate = {
            "version": "final_candidate/v1", "family": BREAKOUT_FAMILY_ID,
            "parameters": self.config.to_dict(), "execution_model": execution.to_dict(),
            "session_profile": research_session_profile_document("krx-regular/v1"),
            "implementation_hash": "a" * 64,
        }
        evaluation = ResearchEvaluationSpec(
            "chronological_holdout/v1",
            (ResearchFoldSpec(
                "final", "OOS", "2026-09-01T00:00:00+00:00",
                "2026-09-10T00:00:00+00:00",
            ),), 60, 0, 0, 10, 3,
            final_holdout_accessed_at="2026-09-11T00:00:00+00:00",
            final_holdout_access_reason="locked final evaluation",
        )
        self.package = MockAutomationCandidatePackage(
            strategy_ref="strategy-1", candidate_spec=candidate,
            candidate_spec_hash=_hash(candidate), scientific_implementation_hash="a" * 64,
            source_final_batch_id="batch-1", source_final_run_id="run-1",
            source_final_result_hash="b" * 64,
            source_run_started_at=NOW - timedelta(hours=3),
            source_run_finished_at=NOW - timedelta(hours=2),
            evaluation_evidence={
                "report_id": "report-1", "report_status": "ELIGIBLE",
                "evaluation_spec": evaluation.to_dict(),
                "oos_fold": {
                    "role": "OOS", "status": "ELIGIBLE", "closed_trade_count": 12,
                    "active_day_count": 4, "net_realized_pnl_won": 120_000,
                    "max_drawdown_ppm": 50_000,
                },
            },
        )
        self.policy = MockAutomationEligibilityPolicy(
            strategy_ref="strategy-1", candidate_spec_hash=self.package.candidate_spec_hash,
            frozen_at=NOW - timedelta(hours=4), minimum_closed_trades=10,
            minimum_active_days=3, minimum_net_realized_pnl_won=0,
            maximum_drawdown_ppm=100_000,
        )
        self.receipt = evaluate_candidate_eligibility(
            self.package, self.policy, account_ref=ACCOUNT_REF,
        )
        self.repository.publish_mock_automation_candidate(
            self.package, self.policy, self.receipt,
            credential_profile_id=PROFILE_ID,
            expected_binding_revision=self.binding["binding_revision"],
        )
        family = get_research_family(BREAKOUT_FAMILY_ID)
        self.profile = ForwardEvaluationSpec(
            strategy_ref="strategy-1", family_version=BREAKOUT_FAMILY_ID,
            factor_versions=family.factor_ids, policy_version=self.policy.policy_id,
            data_path="nas", account_ref=ACCOUNT_REF, environment="mock",
            evaluation_start=NOW + timedelta(hours=2),
            evaluation_end=NOW + timedelta(days=7),
            frozen_at=NOW - timedelta(hours=1),
            criteria=ForwardCriteria(
                minimum_comparable_observations=100, minimum_coverage_ppm=990_000,
                maximum_p95_delay_ms=2_000, maximum_gap_count=1,
                maximum_submission_unknown_count=0, maximum_rejected_order_count=1,
                maximum_cancel_failure_count=0, maximum_reconnect_count=2,
                maximum_balance_mismatch_count=0, minimum_active_day_count=5,
                minimum_closed_trade_count=10, minimum_adjusted_net_pnl_won=0,
                maximum_drawdown_ppm=100_000, maximum_exposure_ppm=500_000,
            ),
            session_profile="krx-regular/v1",
        )
        self.shadow_event = {
            "event_id": "shadow-event-1",
            "run_id": shadow_monitor_id_for_config(
                BREAKOUT_FAMILY_ID, self.config, "krx-regular/v1",
            ),
            "strategy_id": "krx_bar_close_breakout", "strategy_version": "v1",
            "transition": "flat->candidate", "quantity": 1,
            "available_at": NOW.isoformat(),
        }
        evaluated = strategy_stage_revision(
            strategy_ref="strategy-1", previous_stage=StrategyLifecycleStage.DRAFT,
            stage=StrategyLifecycleStage.EVALUATED,
            evidence_refs=(self.package.package_hash,), reason="final candidate evaluated",
            decided_at=NOW - timedelta(hours=2),
        )
        validated = strategy_stage_revision(
            strategy_ref="strategy-1", previous_stage=StrategyLifecycleStage.EVALUATED,
            stage=StrategyLifecycleStage.VALIDATED,
            evidence_refs=(self.receipt.receipt_id,), reason="eligibility validated",
            decided_at=NOW - timedelta(hours=1),
        )
        shadow = strategy_stage_revision(
            strategy_ref="strategy-1", previous_stage=StrategyLifecycleStage.VALIDATED,
            stage=StrategyLifecycleStage.SHADOW,
            evidence_refs=(self.shadow_event["event_id"],), reason="shadow event observed",
            decided_at=NOW,
        )
        self.stages = (evaluated, validated, shadow)
        binding_verified_at = self.binding["verified_at"]
        if isinstance(binding_verified_at, str):
            binding_verified_at = datetime.fromisoformat(binding_verified_at)
        self.spec = MockAutomationOperatingSpec(
            strategy_ref="strategy-1", candidate_package_hash=self.package.package_hash,
            final_result_hash=self.package.source_final_result_hash,
            final_batch_id=self.package.source_final_batch_id,
            final_run_id=self.package.source_final_run_id,
            forward_profile_id=self.profile.profile_id,
            account_scope=AccountScope("kiwoom", AccountEnvironment.MOCK, ACCOUNT_REF),
            credential_profile_id=PROFILE_ID,
            binding_revision=self.binding["binding_revision"],
            binding_verified_at=binding_verified_at,
            frozen_at=NOW + timedelta(hours=1), shadow_evidence_ref=shadow.revision_id,
            maximum_concurrent_strategies=1, maximum_concurrent_positions=1,
            maximum_capital_won=10_000_000, maximum_daily_loss_won=300_000,
            maximum_data_gap_seconds=5, maximum_submission_unknown_count=0,
            maximum_reconnect_count=2, maximum_balance_mismatch_count=0,
            supported_venue="KRX", session_profile="krx-regular/v1", data_path="nas",
            candidate_transition_policy=MOCK_CANDIDATE_TRANSITION_POLICY,
            stop_policy=MOCK_STOP_POLICY, recovery_policy=MOCK_RECOVERY_POLICY,
        )

    def test_candidate_publication_list_returns_complete_account_scoped_lineage(self) -> None:
        publications = self.repository.load_mock_automation_candidate_publications(ACCOUNT_REF)

        self.assertEqual(((self.package, self.policy, self.receipt),), publications)
        self.assertEqual((), self.repository.load_mock_automation_candidate_publications(
            "00000000-0000-0000-0000-000000000000",
        ))

    def test_pc_builder_freezes_complete_ready_documents_from_selected_evidence(self) -> None:
        publication = {
            "package": self.package.to_dict(),
            "eligibility_policy": self.policy.to_dict(),
            "eligibility_receipt": self.receipt.to_dict(),
        }
        limits = {
            name: getattr(self.spec, name)
            for name in (
                "maximum_concurrent_strategies", "maximum_concurrent_positions",
                "maximum_capital_won", "maximum_daily_loss_won",
                "maximum_data_gap_seconds", "maximum_submission_unknown_count",
                "maximum_reconnect_count", "maximum_balance_mismatch_count",
            )
        }
        documents = build_mock_automation_publication_documents(
            candidate_publication=publication,
            binding={
                **self.binding, "broker": "kiwoom", "environment": "mock",
                "account_ref": ACCOUNT_REF,
            },
            credential_profile_id=PROFILE_ID, shadow_event=self.shadow_event,
            evaluation_start=self.profile.evaluation_start,
            evaluation_end=self.profile.evaluation_end,
            frozen_at=self.spec.frozen_at,
            criteria=self.profile.criteria, limits=limits,
        )

        self.assertEqual(ACCOUNT_REF, documents["account_ref"])
        self.assertEqual(
            documents["forward_profile"]["profile_id"],
            documents["operating_spec"]["forward_profile_id"],
        )
        self.assertEqual(self.package.package_hash,
                         documents["operating_spec"]["candidate_package_hash"])
        self.assertEqual(self.spec.frozen_at.isoformat(),
                         documents["operating_spec"]["frozen_at"])
        self.assertEqual(3, len(documents["stage_revisions"]))
        self.assertEqual(
            (self.shadow_event,), matching_shadow_events(publication, (self.shadow_event,)),
        )

    def test_full_fake_cycle_reaches_account_scoped_journal_projection(self) -> None:
        readiness, changed = publish_ready_mock_automation_spec(
            self.repository, profile=self.profile, stage_revisions=self.stages,
            spec=self.spec, shadow_event=self.shadow_event, current_binding=self.binding,
        )
        self.assertTrue(changed)
        self.assertEqual(MockAutomationReadinessStatus.READY, readiness.status)

        clock = [self.profile.evaluation_start + timedelta(minutes=1)]
        execution = ExecutionRepository(self.store)
        transport = _FakeBrokerTransport(lambda: clock[0])
        run_id = execution_run_id_for_spec(self.spec.spec_id)
        runtime = ExecutionRuntime(
            OrderLifecycle(execution, transport, now_provider=lambda: clock[0]),
            execution, account_ref=ACCOUNT_REF, run_id=run_id,
            owner_token="fake-integration-owner", now_provider=lambda: clock[0],
        )
        self.addCleanup(runtime.stop)
        admit_mock_automation(
            self.repository, self.repository, runtime,
            account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id, requested_at=clock[0],
        )

        scope = AccountScope("kiwoom", AccountEnvironment.MOCK, ACCOUNT_REF)
        recovery = MockAccountRecovery(
            AccountSnapshot(ACCOUNT_REF, 10_000_000, 0, {}, clock[0]), (),
        )
        risk = build_mock_automation_risk_snapshot(
            recovery, (), (), (), execution_run_id=run_id,
            binding_revision=self.binding["binding_revision"], reconciliation_revision=1,
            observed_at=clock[0], broker_query_started_at=clock[0],
            broker_query_completed_at=clock[0],
        )
        self.repository.save_mock_automation_risk_snapshot(risk)
        record_mock_automation_recovery_from_risk(
            self.repository, runtime, recovery, risk,
            account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id,
        )
        enter_gate, buy, _ = dispatch_mock_automation_decision_from_risk(
            self.repository, runtime, _automation_decision(run_id, "ENTER", clock[0]),
            recovery, risk, account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id,
            strategy_ref=self.spec.strategy_ref,
        )
        self.assertEqual(MockAutomationGateStatus.APPROVED_FOR_SINGLE_SUBMISSION,
                         enter_gate.status)
        self.assertIsNotNone(buy)

        clock[0] += timedelta(seconds=1)
        buy = runtime.reconcile(buy.intent.intent_id, BrokerOrderSnapshot(
            buy.broker_order_id, ACCOUNT_REF, "005930", OrderState.FILLED, 1, 0, clock[0],
            (BrokerFill("fake-buy-fill", 1, 70_000, clock[0]),),
        ))
        self.assertEqual(OrderState.FILLED, buy.state)

        buy_events = execution.account_events("mock", ACCOUNT_REF, limit=1000).events
        recovery = MockAccountRecovery(
            AccountSnapshot(ACCOUNT_REF, 9_930_000, 0, {"005930": 1}, clock[0]), (),
        )
        costs = (DailyTradeCost(
            clock[0].astimezone(timezone(timedelta(hours=9))).date(),
            clock[0].date(), "005930", "매수", 70_000, 70_000, 0, 0, 0,
            origin_scope=scope,
        ),)
        risk = build_mock_automation_risk_snapshot(
            recovery, buy_events, costs, (), execution_run_id=run_id,
            binding_revision=self.binding["binding_revision"], reconciliation_revision=2,
            observed_at=clock[0], broker_query_started_at=clock[0],
            broker_query_completed_at=clock[0],
        )
        self.repository.save_mock_automation_risk_snapshot(risk)
        record_mock_automation_recovery_from_risk(
            self.repository, runtime, recovery, risk,
            account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id,
        )
        clock[0] += timedelta(seconds=1)
        exit_gate, sell, _ = dispatch_mock_automation_decision_from_risk(
            self.repository, runtime,
            _automation_decision(run_id, "EXIT", risk.observed_at),
            recovery, risk, account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id,
            strategy_ref=self.spec.strategy_ref,
        )
        self.assertEqual(MockAutomationGateStatus.APPROVED_FOR_SINGLE_SUBMISSION,
                         exit_gate.status, exit_gate.reasons)
        self.assertIsNotNone(sell)

        clock[0] += timedelta(seconds=1)
        sell = runtime.reconcile(sell.intent.intent_id, BrokerOrderSnapshot(
            sell.broker_order_id, ACCOUNT_REF, "005930", OrderState.FILLED, 1, 0, clock[0],
            (BrokerFill("fake-sell-fill", 1, 70_500, clock[0]),),
        ))
        self.assertEqual(OrderState.FILLED, sell.state)
        self.assertEqual(
            [("fake-order-1", "BUY", "005930", 1),
             ("fake-order-2", "SELL", "005930", 1)],
            transport.calls,
        )

        with tempfile.TemporaryDirectory() as directory:
            journal = JournalRepository(Path(directory) / "journal.sqlite3")
            projected = sync_journal_execution_projection(
                _ExecutionPageSource(execution, ACCOUNT_REF), journal, scope,
                page_limit=1000, now=clock[0],
            )
            fills = journal.load_execution_fill_projections(scope)
        self.assertEqual(2, projected.detailed_fill_count)
        self.assertEqual(("fake-buy-fill", "fake-sell-fill"), tuple(
            row["broker_execution_id"] for row in fills
        ))
        self.assertEqual({run_id}, {row["run_id"] for row in fills})

    def test_complete_chain_is_saved_idempotently_as_ready(self):
        first = publish_ready_mock_automation_spec(
            self.repository, profile=self.profile, stage_revisions=self.stages,
            spec=self.spec, shadow_event=self.shadow_event, current_binding=self.binding,
        )
        second = publish_ready_mock_automation_spec(
            self.repository, profile=self.profile, stage_revisions=self.stages,
            spec=self.spec, shadow_event=self.shadow_event, current_binding=self.binding,
        )
        self.assertEqual(MockAutomationReadinessStatus.READY, first[0].status)
        self.assertTrue(first[1]); self.assertFalse(second[1])
        self.assertEqual(self.spec, self.repository.load_mock_automation_spec(
            ACCOUNT_REF, self.spec.spec_id,
        ))

    def test_wrong_shadow_owner_is_rejected_before_profile_or_spec_is_saved(self):
        changed = {**self.shadow_event, "run_id": "shadow:other"}
        with self.assertRaisesRegex(ValueError, "shadow evidence"):
            publish_ready_mock_automation_spec(
                self.repository, profile=self.profile, stage_revisions=self.stages,
                spec=self.spec, shadow_event=changed, current_binding=self.binding,
            )
        self.assertIsNone(self.repository.load_profile(
            self.profile.strategy_ref, self.profile.profile_id,
        ))
        self.assertIsNone(self.repository.load_mock_automation_spec(ACCOUNT_REF, self.spec.spec_id))

    def test_invalid_shadow_quantity_is_rejected_as_validation_error(self):
        changed = {**self.shadow_event, "quantity": "one"}
        with self.assertRaisesRegex(ValueError, "shadow evidence"):
            publish_ready_mock_automation_spec(
                self.repository, profile=self.profile, stage_revisions=self.stages,
                spec=self.spec, shadow_event=changed, current_binding=self.binding,
            )

    def test_existing_different_stage_history_is_not_overwritten(self):
        conflicting = strategy_stage_revision(
            strategy_ref="strategy-1", previous_stage=StrategyLifecycleStage.DRAFT,
            stage=StrategyLifecycleStage.EVALUATED, evidence_refs=("other",),
            reason="another evaluation", decided_at=NOW - timedelta(hours=2),
        )
        self.repository.save_stage_revision(conflicting)
        with self.assertRaisesRegex(ValueError, "stage history conflicts"):
            publish_ready_mock_automation_spec(
                self.repository, profile=self.profile, stage_revisions=self.stages,
                spec=self.spec, shadow_event=self.shadow_event, current_binding=self.binding,
            )
        self.assertEqual((conflicting,), self.repository.load_stage_revisions("strategy-1"))


if __name__ == "__main__":
    unittest.main()
