from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from kiwoom_monitor.central_server.app import create_app
from kiwoom_monitor.central_server.config import CentralServerSettings

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


def mock_specification_inputs(store, *, scientific_hash="a" * 64, publish_candidate=True):
    """One independent set of immutable inputs, using the caller-owned native store."""
    from types import SimpleNamespace
    value = SimpleNamespace(store=store)
    value.store.register_account_identity({
        "broker": "kiwoom", "environment": "mock",
        "identity_fingerprint": "c" * 64, "account_ref": ACCOUNT_REF,
        "created_at": (NOW - timedelta(days=1)).isoformat(),
    })
    value.binding = value.store.append_account_binding({
        "credential_profile_id": PROFILE_ID, "broker": "kiwoom",
        "environment": "mock", "account_ref": ACCOUNT_REF,
        "verified_at": (NOW - timedelta(days=1)).isoformat(),
        "verification_method": "ka00001",
    })
    value.repository = ForwardEvaluationRepository(value.store)
    value.config = default_shadow_breakout_config()
    execution = SimulationExecutionConfig(
        EXECUTION_MODEL_VERSION, SAME_BAR_PATH_VERSION, 10_000_000,
        SimulationCostModel(
            COST_MODEL_VERSION, 2, 18, 5, "official_period_rule", "fixture",
            "2026-01-01T00:00:00+00:00", "2027-01-01T00:00:00+00:00",
        ),
    )
    candidate = {
        "version": "final_candidate/v1", "family": BREAKOUT_FAMILY_ID,
        "parameters": value.config.to_dict(), "execution_model": execution.to_dict(),
        "session_profile": research_session_profile_document("krx-regular/v1"),
        "implementation_hash": scientific_hash,
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
    value.package = MockAutomationCandidatePackage(
        strategy_ref="strategy-1", candidate_spec=candidate,
        candidate_spec_hash=_hash(candidate), scientific_implementation_hash=scientific_hash,
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
    value.policy = MockAutomationEligibilityPolicy(
        strategy_ref="strategy-1", candidate_spec_hash=value.package.candidate_spec_hash,
        frozen_at=NOW - timedelta(hours=4), minimum_closed_trades=10,
        minimum_active_days=3, minimum_net_realized_pnl_won=0,
        maximum_drawdown_ppm=100_000,
    )
    value.receipt = evaluate_candidate_eligibility(
        value.package, value.policy, account_ref=ACCOUNT_REF,
    )
    if publish_candidate:
        value.repository.publish_mock_automation_candidate(
            value.package, value.policy, value.receipt,
            credential_profile_id=PROFILE_ID,
            expected_binding_revision=value.binding["binding_revision"],
        )
    family = get_research_family(BREAKOUT_FAMILY_ID)
    value.profile = ForwardEvaluationSpec(
        strategy_ref="strategy-1", family_version=BREAKOUT_FAMILY_ID,
        factor_versions=family.factor_ids, policy_version=value.policy.policy_id,
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
    value.shadow_event = {
        "event_id": "shadow-event-1",
        "run_id": shadow_monitor_id_for_config(
            BREAKOUT_FAMILY_ID, value.config, "krx-regular/v1",
        ),
        "strategy_id": "krx_bar_close_breakout", "strategy_version": "v1",
        "transition": "flat->candidate", "quantity": 1,
        "available_at": NOW.isoformat(),
    }
    evaluated = strategy_stage_revision(
        strategy_ref="strategy-1", previous_stage=StrategyLifecycleStage.DRAFT,
        stage=StrategyLifecycleStage.EVALUATED,
        evidence_refs=(value.package.package_hash,), reason="final candidate evaluated",
        decided_at=NOW - timedelta(hours=2),
    )
    validated = strategy_stage_revision(
        strategy_ref="strategy-1", previous_stage=StrategyLifecycleStage.EVALUATED,
        stage=StrategyLifecycleStage.VALIDATED,
        evidence_refs=(value.receipt.receipt_id,), reason="eligibility validated",
        decided_at=NOW - timedelta(hours=1),
    )
    shadow = strategy_stage_revision(
        strategy_ref="strategy-1", previous_stage=StrategyLifecycleStage.VALIDATED,
        stage=StrategyLifecycleStage.SHADOW,
        evidence_refs=(value.shadow_event["event_id"],), reason="shadow event observed",
        decided_at=NOW,
    )
    value.stages = (evaluated, validated, shadow)
    binding_verified_at = value.binding["verified_at"]
    if isinstance(binding_verified_at, str):
        binding_verified_at = datetime.fromisoformat(binding_verified_at)
    value.spec = MockAutomationOperatingSpec(
        strategy_ref="strategy-1", candidate_package_hash=value.package.package_hash,
        final_result_hash=value.package.source_final_result_hash,
        final_batch_id=value.package.source_final_batch_id,
        final_run_id=value.package.source_final_run_id,
        forward_profile_id=value.profile.profile_id,
        account_scope=AccountScope("kiwoom", AccountEnvironment.MOCK, ACCOUNT_REF),
        credential_profile_id=PROFILE_ID,
        binding_revision=value.binding["binding_revision"],
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
    return value


PUBLICATION_BASELINE = Path(__file__).resolve().parents[1] / "fixtures/api_contract_baselines/mock_publication_http_v1.json"


@contextmanager
def mock_publication_contract_app(app_factory, database_path):
    from kiwoom_monitor.application.research_implementation import research_implementation_hash
    store = SQLiteQueryStore(database_path)
    store.initialize()
    settings = CentralServerSettings(
        f"sqlite:///{database_path}", "private-token", autonomous_top20_enabled=False,
        market_event_collection_enabled=False, news_history_jobs_enabled=False,
    )
    try:
        with patch("kiwoom_monitor.central_server.database_documents.time", return_value=1000.0):
            inputs = mock_specification_inputs(
                store, scientific_hash=research_implementation_hash("krx-regular/v1"), publish_candidate=False,
            )
            store.save_shadow_evaluation(
                inputs.shadow_event["run_id"], {"decision_id": "decision-contract", "decided_at": NOW.isoformat()},
                inputs.shadow_event, (NOW + timedelta(minutes=5)).isoformat(),
            )
            yield app_factory(settings), store, inputs
    finally:
        store.close()


def capture_mock_publication_http_contract(client, store, inputs):
    """Run real validation/repository writes, and independently inspect every publication."""
    collections = (
        ForwardEvaluationRepository.MOCK_AUTOMATION_CANDIDATE_COLLECTION,
        ForwardEvaluationRepository.MOCK_AUTOMATION_ELIGIBILITY_POLICY_COLLECTION,
        ForwardEvaluationRepository.MOCK_AUTOMATION_ELIGIBILITY_RECEIPT_COLLECTION,
        ForwardEvaluationRepository.PROFILE_COLLECTION, ForwardEvaluationRepository.STAGE_COLLECTION,
        ForwardEvaluationRepository.MOCK_AUTOMATION_SPEC_COLLECTION,
    )

    def stored():
        return {name: store.load_documents(name, limit=1000) for name in collections}

    cases = []

    def request(name, method, path, *, authenticated=True, **kwargs):
        before = _hash(stored())
        response = client.request(method, path, headers={"Authorization": "Bearer private-token"} if authenticated else {}, **kwargs)
        cases.append({"name": name, "status": response.status_code, "headers": dict(response.headers),
                      "body": response.json(), "stored_before": before, "stored_after": _hash(stored())})

    candidate_path = "/api/v1/research/mock-automation-candidates"
    spec_path = "/api/v1/research/mock-automation-specs"
    candidate = {
        "account_ref": ACCOUNT_REF, "credential_profile_id": PROFILE_ID,
        "expected_binding_revision": inputs.binding["binding_revision"],
        "package": inputs.package.to_dict(), "eligibility_policy": inputs.policy.to_dict(),
        "eligibility_receipt": inputs.receipt.to_dict(),
    }
    spec = {
        "account_ref": ACCOUNT_REF, "credential_profile_id": PROFILE_ID,
        "expected_binding_revision": inputs.binding["binding_revision"], "shadow_event_id": inputs.shadow_event["event_id"],
        "forward_profile": inputs.profile.to_dict(), "stage_revisions": [stage.to_dict() for stage in inputs.stages],
        "operating_spec": inputs.spec.to_dict(),
    }
    for path, payload in ((candidate_path, candidate), (spec_path, spec)):
        request("unauthorized-" + ("candidate" if path == candidate_path else "spec"), "POST", path, authenticated=False, json=payload)
        for field, value in (("expected_binding_revision", True), ("expected_binding_revision", "1"),
                             ("credential_profile_id", "bad profile"), ("extra", 1)):
            invalid = deepcopy(payload)
            invalid[field] = value
            request("validation-" + path.rsplit("/", 1)[-1] + "-" + field + "-" + str(value), "POST", path, json=invalid)
    request("unauthorized-candidate-list", "GET", candidate_path + "/" + ACCOUNT_REF, authenticated=False, params={"credential_profile_id": PROFILE_ID})
    request("unauthorized-spec-list", "GET", spec_path + "/" + ACCOUNT_REF, authenticated=False)
    request("candidate-list-missing-profile", "GET", candidate_path + "/" + ACCOUNT_REF)
    request("candidate-list-invalid-profile", "GET", candidate_path + "/" + ACCOUNT_REF, params={"credential_profile_id": "bad profile"})
    request("candidate-list-empty", "GET", candidate_path + "/" + ACCOUNT_REF, params={"credential_profile_id": PROFILE_ID})
    request("spec-list-empty", "GET", spec_path + "/" + ACCOUNT_REF)
    request("spec-without-candidate", "POST", spec_path, json=spec)
    for name, field, value in (("candidate-other-account", "account_ref", "00000000-0000-0000-0000-000000000000"),
                               ("candidate-missing-binding", "credential_profile_id", "missing"),
                               ("candidate-stale-binding", "expected_binding_revision", 2),
                               ("candidate-invalid-package", "package", {})):
        invalid = deepcopy(candidate)
        invalid[field] = value
        request(name, "POST", candidate_path, json=invalid)
    invalid = deepcopy(candidate)
    invalid["package"]["scientific_implementation_hash"] = "a" * 64
    invalid["package"].pop("package_hash")
    request("candidate-wrong-scientific-hash", "POST", candidate_path, json=invalid)
    request("candidate-save", "POST", candidate_path, json=candidate)
    request("candidate-repeat", "POST", candidate_path, json=candidate)
    request("candidate-list", "GET", candidate_path + "/" + ACCOUNT_REF, params={"credential_profile_id": PROFILE_ID})
    request("candidate-list-other-account", "GET", candidate_path + "/00000000-0000-0000-0000-000000000000", params={"credential_profile_id": PROFILE_ID})
    for name, field, value in (("spec-other-account", "account_ref", "00000000-0000-0000-0000-000000000000"),
                               ("spec-other-profile", "credential_profile_id", "missing"),
                               ("spec-stale-binding", "expected_binding_revision", 2),
                               ("spec-missing-shadow", "shadow_event_id", "missing"),
                               ("spec-invalid-profile", "forward_profile", {}),
                               ("spec-short-stage-chain", "stage_revisions", [spec["stage_revisions"][0]])):
        invalid = deepcopy(spec)
        invalid[field] = value
        request(name, "POST", spec_path, json=invalid)
    invalid = deepcopy(spec)
    invalid["stage_revisions"] = list(reversed(invalid["stage_revisions"]))
    request("spec-reversed-stage-chain", "POST", spec_path, json=invalid)
    request("spec-save", "POST", spec_path, json=spec)
    request("spec-repeat", "POST", spec_path, json=spec)
    request("spec-list", "GET", spec_path + "/" + ACCOUNT_REF)
    request("spec-list-other-account", "GET", spec_path + "/00000000-0000-0000-0000-000000000000")
    store.append_account_binding({**inputs.binding, "verified_at": (NOW + timedelta(days=1)).isoformat()})
    request("candidate-after-binding-change", "POST", candidate_path, json=candidate)
    request("spec-after-binding-change", "POST", spec_path, json=spec)
    request("candidate-list-current-binding", "GET", candidate_path + "/" + ACCOUNT_REF, params={"credential_profile_id": PROFILE_ID})
    return {"cases": cases, "stored": stored(), "binding": store.load_account_bindings(),
            "shadow": store.load_shadow_candidates(),
            "control": store.load_mock_automation_control(ACCOUNT_REF)}


class MockPublicationHTTPContractTests(unittest.TestCase):
    def test_http_and_native_publication_results_match_pre_extraction_baseline(self):
        expected = json.loads(PUBLICATION_BASELINE.read_text(encoding="utf-8"))["result"]
        with tempfile.TemporaryDirectory() as directory:
            with mock_publication_contract_app(create_app, Path(directory) / "publication.sqlite3") as (app, store, inputs):
                with TestClient(app) as client:
                    actual = capture_mock_publication_http_contract(client, store, inputs)
        self.assertEqual([case["name"] for case in expected["cases"]], [case["name"] for case in actual["cases"]])
        for original, observed in zip(expected["cases"], actual["cases"], strict=True):
            with self.subTest(case=original["name"]):
                self.assertEqual(original, observed)
        for field in ("stored", "binding", "shadow", "control"):
            self.assertEqual(expected[field], actual[field])


class MockAutomationSpecificationTests(unittest.TestCase):
    def setUp(self):
        self.store = SQLiteQueryStore(Path(":memory:")); self.store.initialize()
        self.addCleanup(self.store.close)
        inputs = mock_specification_inputs(self.store)
        self.__dict__.update(vars(inputs))

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
