from __future__ import annotations

import hashlib
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from kiwoom_monitor.application.breakout_strategy import StrategyState, default_shadow_breakout_config
from kiwoom_monitor.application.market_session_schedule import research_session_profile_document
from kiwoom_monitor.application.mock_automation_candidate import MockAutomationCandidatePackage
from kiwoom_monitor.application.research_execution import (
    COST_MODEL_VERSION,
    EXECUTION_MODEL_VERSION,
    SAME_BAR_PATH_VERSION,
    SimulationCostModel,
    SimulationExecutionConfig,
)
from kiwoom_monitor.application.research_families import BREAKOUT_FAMILY_ID
from kiwoom_monitor.application.research_splits import ResearchEvaluationSpec, ResearchFoldSpec
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.mock_automation_runner import MockAutomationRunner
from kiwoom_monitor.domain.execution_activation import (
    MOCK_CANDIDATE_TRANSITION_POLICY,
    MOCK_RECOVERY_POLICY,
    MOCK_STOP_POLICY,
    MockAutomationOperatingSpec,
)
from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope
from kiwoom_monitor.infrastructure.persistence.execution_repository import (
    AccountExecutionEvent,
    AccountExecutionEventPage,
)
from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import (
    ForwardEvaluationRepository,
)


NOW = datetime(2026, 9, 21, 1, 0, tzinfo=timezone.utc)
ACCOUNT_REF = "af64a3fa-197f-49df-8e8b-65b71bee02d9"


def _hash(value):
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()


def _package() -> MockAutomationCandidatePackage:
    execution = SimulationExecutionConfig(
        EXECUTION_MODEL_VERSION, SAME_BAR_PATH_VERSION, 10_000_000,
        SimulationCostModel(
            COST_MODEL_VERSION, 2, 18, 5, "official_period_rule", "fixture",
            "2026-01-01T00:00:00+00:00", "2027-01-01T00:00:00+00:00",
        ),
    )
    candidate = {
        "version": "final_candidate/v1",
        "family": BREAKOUT_FAMILY_ID,
        "parameters": default_shadow_breakout_config().to_dict(),
        "execution_model": execution.to_dict(),
        "session_profile": research_session_profile_document("krx-regular/v1"),
        "implementation_hash": "a" * 64,
    }
    evaluation = ResearchEvaluationSpec(
        "chronological_holdout/v1",
        (ResearchFoldSpec(
            "final", "OOS", "2026-09-01T00:00:00+00:00",
            "2026-09-10T00:00:00+00:00",
        ),),
        60, 0, 0, 10, 3,
        final_holdout_accessed_at="2026-09-11T00:00:00+00:00",
        final_holdout_access_reason="locked final evaluation",
    )
    return MockAutomationCandidatePackage(
        strategy_ref="strategy-1", candidate_spec=candidate,
        candidate_spec_hash=_hash(candidate), scientific_implementation_hash="a" * 64,
        source_final_batch_id="batch-1", source_final_run_id="run-1",
        source_final_result_hash="b" * 64,
        source_run_started_at=NOW, source_run_finished_at=NOW + timedelta(minutes=1),
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


class _Events:
    def __init__(self):
        self.values = []

    def account_events(self, environment, account_ref, *, after_sequence, limit):
        values = tuple(value for value in self.values if value.accepted_sequence > after_sequence)[:limit]
        cursor = values[-1].accepted_sequence if values else after_sequence
        return AccountExecutionEventPage(values, cursor, False)

    def active_intents(self, environment, account_ref, run_id):
        return ()


class MockAutomationRunnerTests(unittest.TestCase):
    def setUp(self):
        self.store = SQLiteQueryStore(Path(":memory:")); self.store.initialize()
        self.addCleanup(self.store.close)
        self.package = _package()
        self.store.upsert_documents(
            ForwardEvaluationRepository.MOCK_AUTOMATION_CANDIDATE_COLLECTION,
            [{"owner": self.package.strategy_ref, "key": self.package.package_hash,
              "document": self.package.to_dict()}],
        )
        self.run_id = "mock_auto_run_" + "c" * 64
        self.spec = MockAutomationOperatingSpec(
            strategy_ref=self.package.strategy_ref,
            candidate_package_hash=self.package.package_hash,
            final_result_hash=self.package.source_final_result_hash,
            final_batch_id=self.package.source_final_batch_id,
            final_run_id=self.package.source_final_run_id,
            forward_profile_id="profile-1",
            account_scope=AccountScope("kiwoom", AccountEnvironment.MOCK, ACCOUNT_REF),
            credential_profile_id="profile-a", binding_revision=1,
            binding_verified_at=NOW, frozen_at=NOW,
            shadow_evidence_ref="shadow-1", maximum_concurrent_strategies=1,
            maximum_concurrent_positions=1, maximum_capital_won=1_000_000,
            maximum_daily_loss_won=100_000, maximum_data_gap_seconds=5,
            maximum_submission_unknown_count=0, maximum_reconnect_count=1,
            maximum_balance_mismatch_count=0, supported_venue="KRX",
            session_profile="krx-regular/v1", data_path="nas",
            candidate_transition_policy=MOCK_CANDIDATE_TRANSITION_POLICY,
            stop_policy=MOCK_STOP_POLICY, recovery_policy=MOCK_RECOVERY_POLICY,
        )
        self.events = _Events()
        self.bundle = SimpleNamespace(
            account_ref=ACCOUNT_REF, run_id=self.run_id,
            automation_context=SimpleNamespace(
                execution_run_id=self.run_id, spec_id=self.spec.spec_id,
            ),
            repository=self.events,
        )

    def event(self, sequence, execution_id, side, quantity, price):
        return AccountExecutionEvent(
            accepted_sequence=sequence, source_event_id=f"event-{sequence}",
            intent_id=f"intent-{sequence}", run_id=self.run_id,
            decision_id=f"decision-{sequence}", account_ref=ACCOUNT_REF,
            environment="mock", symbol="005930", venue="KRX", side=side,
            event_type="FILL", state="FILLED", occurred_at=NOW + timedelta(seconds=sequence),
            received_at=NOW + timedelta(seconds=sequence), broker_execution_id=execution_id,
            quantity=quantity, price=price,
        )

    def test_checkpoint_restores_fill_state_and_duplicate_fill_is_not_reapplied(self):
        runner = MockAutomationRunner(self.store, self.bundle, self.spec)
        runner._state = StrategyState(
            status="candidate", symbol="005930", candidate_key="candidate-1",
            candidate_expires_at=(NOW + timedelta(minutes=1)).isoformat(),
            emitted_candidate_keys=("candidate-1",),
        )
        self.events.values.append(self.event(1, "fill-1", "BUY", 2, 10_000))
        runner._apply_new_fills()
        self.assertEqual("open", runner._state.status)
        self.assertEqual(2, runner._state.position_quantity)
        runner._save_checkpoint()

        restarted = MockAutomationRunner(self.store, self.bundle, self.spec)
        self.assertEqual("open", restarted._state.status)
        self.assertEqual(1, restarted._fill_cursor)
        self.events.values.append(self.event(2, "fill-1", "BUY", 2, 10_000))
        restarted._apply_new_fills()
        self.assertEqual(2, restarted._state.position_quantity)
        self.events.values.append(self.event(3, "fill-2", "SELL", 2, 10_500))
        restarted._apply_new_fills()
        self.assertEqual("cooldown", restarted._state.status)
        self.assertEqual(3, restarted._fill_cursor)

    def test_checkpoint_from_other_spec_is_never_reused(self):
        first = MockAutomationRunner(self.store, self.bundle, self.spec)
        first._cursor = 99
        first._save_checkpoint()
        changed = SimpleNamespace(**{
            **self.bundle.__dict__,
            "automation_context": SimpleNamespace(
                execution_run_id=self.run_id, spec_id="different-spec",
            ),
        })
        with self.assertRaisesRegex(ValueError, "CONTEXT_MISMATCH"):
            MockAutomationRunner(self.store, changed, self.spec)


class MockAutomationRunnerAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_restart_resume_consumes_only_unseen_observations(self):
        case = MockAutomationRunnerTests("test_checkpoint_from_other_spec_is_never_reused")
        case.setUp()
        self.addCleanup(case.store.close)
        control_state = {"value": "RUNNING"}
        observations = [
            {"kind": "top20_membership", "accepted_sequence": sequence}
            for sequence in (1, 2, 3)
        ]
        loaded_after = []
        consumed = []

        def configure(runner):
            runner._repository.load_mock_automation_control = lambda _account: SimpleNamespace(
                active_spec_id=case.spec.spec_id,
                desired_state=SimpleNamespace(value=control_state["value"]),
            )

            async def consume(observation):
                consumed.append(observation["accepted_sequence"])

            runner._consume = consume

        def load_after(cursor, _kinds, _limit):
            loaded_after.append(cursor)
            return [value for value in observations if value["accepted_sequence"] > cursor]

        case.store.load_observation_revisions_after = load_after
        runner = MockAutomationRunner(case.store, case.bundle, case.spec)
        configure(runner)
        observations[:] = observations[:2]
        self.assertEqual(2, await runner.run_once())
        self.assertEqual([1, 2], consumed)
        self.assertEqual(2, runner.status["input_cursor"])

        control_state["value"] = "STOPPED"
        observations.append({"kind": "top20_membership", "accepted_sequence": 3})
        reads_before_stop = len(loaded_after)
        self.assertEqual(0, await runner.run_once())
        self.assertEqual(reads_before_stop, len(loaded_after))
        self.assertEqual([1, 2], consumed)

        restarted = MockAutomationRunner(case.store, case.bundle, case.spec)
        configure(restarted)
        self.assertEqual("STOPPED", restarted.status["state"])
        self.assertEqual(2, restarted.status["input_cursor"])
        self.assertEqual(0, await restarted.run_once())
        self.assertEqual(reads_before_stop, len(loaded_after))

        control_state["value"] = "RUNNING"
        self.assertEqual(1, await restarted.run_once())
        self.assertEqual(2, loaded_after[-1])
        self.assertEqual([1, 2, 3], consumed)
        self.assertEqual(3, restarted.status["input_cursor"])
        self.assertEqual(0, await restarted.run_once())
        self.assertEqual([1, 2, 3], consumed)

    async def test_idle_blocked_runner_does_not_rewrite_checkpoint_each_poll(self):
        case = MockAutomationRunnerTests("test_checkpoint_from_other_spec_is_never_reused")
        case.setUp()
        self.addCleanup(case.store.close)
        runner = MockAutomationRunner(case.store, case.bundle, case.spec)
        await runner.run_once()
        revision = runner._checkpoint_revision
        await runner.run_once()
        self.assertEqual(revision, runner._checkpoint_revision)

    async def test_status_measures_observation_age_without_idle_checkpoint_writes(self):
        case = MockAutomationRunnerTests("test_checkpoint_from_other_spec_is_never_reused")
        case.setUp()
        self.addCleanup(case.store.close)
        runner = MockAutomationRunner(case.store, case.bundle, case.spec)
        runner._repository.load_mock_automation_control = lambda _account: SimpleNamespace(
            active_spec_id=case.spec.spec_id,
            desired_state=SimpleNamespace(value="RUNNING"),
        )
        observation = {
            "kind": "minute_bar", "accepted_sequence": 1,
            "available_at": (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat(),
        }
        case.store.load_observation_revisions_after = lambda *_args: [observation]

        async def consume(_observation):
            return None

        runner._consume = consume
        self.assertEqual(1, await runner.run_once())
        latency = runner.status["latency"]
        self.assertEqual(1, latency["observation_count"])
        self.assertEqual(1, latency["minute_bar_count"])
        self.assertGreaterEqual(latency["last_bar_age_ms"], 2000)
        self.assertEqual(0, latency["future_or_invalid_bar_time_count"])
        self.assertGreaterEqual(latency["last_poll_duration_ms"], latency["load_duration_ms"])
        revision = runner._checkpoint_revision
        case.store.load_observation_revisions_after = lambda *_args: []
        self.assertEqual(0, await runner.run_once())
        self.assertEqual(revision, runner._checkpoint_revision)
        self.assertEqual(latency["last_bar_processed_at"], runner.status["latency"]["last_bar_processed_at"])


if __name__ == "__main__":
    unittest.main()
