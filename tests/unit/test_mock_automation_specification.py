from __future__ import annotations

from dataclasses import replace

import hashlib
import json
import tempfile
import unittest
from types import SimpleNamespace
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

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
    emergency_stop_mock_automation,
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
from kiwoom_monitor.central_server.candidate_monitor import CandidateMonitor
from kiwoom_monitor.central_server.execution_runtime import ExecutionRuntime
from kiwoom_monitor.central_server.mock_automation_runner import MockAutomationRunner
from kiwoom_monitor.central_server.mock_account_monitor import MockAccountMonitor
from kiwoom_monitor.central_server.database_observation_readers import ObservationRevisionPage
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
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_execution import SubmissionUnknown
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


def mock_specification_inputs(store, *, scientific_hash="a" * 64, publish_candidate=True,
                              unfilled_execution_policy=None):
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
        unfilled_execution_policy=unfilled_execution_policy,
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
        scientific_hash = expected["stored"][
            ForwardEvaluationRepository.MOCK_AUTOMATION_CANDIDATE_COLLECTION
        ][0]["document"]["scientific_implementation_hash"]
        # Freeze only the source identity input for the historical HTTP contract.
        # The separate native test below checks admission against the actual source hash.
        with patch("kiwoom_monitor.application.research_implementation.research_implementation_hash",
                   return_value=scientific_hash), tempfile.TemporaryDirectory() as directory:
            with mock_publication_contract_app(create_app, Path(directory) / "publication.sqlite3") as (app, store, inputs):
                with TestClient(app) as client:
                    actual = capture_mock_publication_http_contract(client, store, inputs)
        self.assertEqual([case["name"] for case in expected["cases"]], [case["name"] for case in actual["cases"]])
        for original, observed in zip(expected["cases"], actual["cases"], strict=True):
            with self.subTest(case=original["name"]):
                self.assertEqual(original, observed)
        for field in ("stored", "binding", "shadow", "control"):
            self.assertEqual(expected[field], actual[field])

    def test_native_publication_checks_current_scientific_hash_without_override(self):
        from dataclasses import replace
        from kiwoom_monitor.application.research_implementation import research_implementation_hash
        scientific_hash = research_implementation_hash("krx-regular/v1")
        with tempfile.TemporaryDirectory() as directory:
            with mock_publication_contract_app(create_app, Path(directory) / "publication.sqlite3") as (app, store, inputs):
                self.assertEqual(scientific_hash, inputs.package.scientific_implementation_hash)
                with TestClient(app) as client:
                    wrong_spec = deepcopy(inputs.package.candidate_spec)
                    wrong_spec["implementation_hash"] = "a" * 64
                    wrong_package = replace(inputs.package, candidate_spec=wrong_spec,
                                            candidate_spec_hash=_hash(wrong_spec),
                                            scientific_implementation_hash="a" * 64)
                    wrong_policy = replace(inputs.policy, candidate_spec_hash=wrong_package.candidate_spec_hash)
                    wrong_receipt = evaluate_candidate_eligibility(wrong_package, wrong_policy, account_ref=ACCOUNT_REF)
                    mismatch = client.post("/api/v1/research/mock-automation-candidates",
                        headers={"Authorization": "Bearer private-token"}, json={
                            "account_ref": ACCOUNT_REF, "credential_profile_id": PROFILE_ID,
                            "expected_binding_revision": inputs.binding["binding_revision"],
                            "package": wrong_package.to_dict(), "eligibility_policy": wrong_policy.to_dict(),
                            "eligibility_receipt": wrong_receipt.to_dict(),
                        })
                    self.assertEqual(409, mismatch.status_code)
                    self.assertEqual("candidate scientific implementation hash does not match this server",
                                     mismatch.json()["detail"])
                    self.assertEqual([], store.load_documents(
                        ForwardEvaluationRepository.MOCK_AUTOMATION_CANDIDATE_COLLECTION, limit=1000,
                    ))
                    actual = capture_mock_publication_http_contract(client, store, inputs)
                cases = {case["name"]: case for case in actual["cases"]}
                mismatch = cases["candidate-wrong-scientific-hash"]
                self.assertEqual(400, mismatch["status"])
                self.assertEqual(mismatch["stored_before"], mismatch["stored_after"])
                self.assertIn("scientific implementation hash", mismatch["body"]["detail"])
                self.assertEqual(200, cases["candidate-save"]["status"])
                self.assertEqual(200, cases["candidate-repeat"]["status"])
                self.assertEqual(inputs.package.package_hash, cases["candidate-save"]["body"]["package_hash"])
                documents = actual["stored"][ForwardEvaluationRepository.MOCK_AUTOMATION_CANDIDATE_COLLECTION]
                self.assertEqual(1, len(documents))
                self.assertEqual(scientific_hash, documents[0]["document"]["scientific_implementation_hash"])


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


class MockAutomationWorkflowTests(unittest.IsolatedAsyncioTestCase):
    """Actual breakout evaluator and order lifecycle, with isolated broker inputs."""

    def setUp(self):
        self.store = SQLiteQueryStore(Path(":memory:"))
        self.store.initialize()
        self.addCleanup(self.store.close)
        inputs = mock_specification_inputs(self.store, unfilled_execution_policy=getattr(self, 'unfilled_execution_policy', None))
        self.__dict__.update(vars(inputs))
        publish_ready_mock_automation_spec(
            self.repository, profile=self.profile, stage_revisions=self.stages,
            spec=self.spec, shadow_event=self.shadow_event, current_binding=self.binding,
        )
        self.clock = self.profile.evaluation_start
        self.execution = ExecutionRepository(self.store)
        self.transport = _FakeBrokerTransport(lambda: self.clock)
        self.run_id = execution_run_id_for_spec(self.spec.spec_id)
        self.runtime = ExecutionRuntime(
            OrderLifecycle(self.execution, self.transport, now_provider=lambda: self.clock),
            self.execution, account_ref=ACCOUNT_REF, run_id=self.run_id,
            owner_token="workflow-test-owner", now_provider=lambda: self.clock,
        )
        self.addCleanup(self.runtime.stop)
        admit_mock_automation(
            self.repository, self.repository, self.runtime,
            account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id,
            requested_at=self.clock, lease_seconds=300,
        )
        self.positions = {}
        self.cash = 10_000_000
        self.costs = []
        self.risk_revision = 0
        self.observations = []
        # Bootstrap has real complete bars and rank history, but must not order.
        for minute in range(-5, 1):
            self._rank(self.profile.evaluation_start + timedelta(minutes=minute, seconds=2))
            if minute < 0:
                self._bar(self.profile.evaluation_start + timedelta(minutes=minute), 69_000, 70_000)
        seed = tuple(sorted(self.observations, key=lambda row: row["accepted_sequence"]))
        bootstrap = patch.object(
            self.store, "load_observation_bootstrap",
            return_value=ObservationRevisionPage(rows=seed, safe_through=len(seed)),
        )
        bootstrap.start(); self.addCleanup(bootstrap.stop)
        live = patch.object(
            self.store, "load_observation_revisions_after",
            side_effect=lambda cursor, kinds, limit: [
                row for row in self.observations
                if row["accepted_sequence"] > cursor and row["kind"] in kinds
            ][:limit],
        )
        live.start(); self.addCleanup(live.stop)
        self.bundle = SimpleNamespace(
            account_ref=ACCOUNT_REF, run_id=self.run_id, repository=self.execution,
            runtime=self.runtime, monitor=SimpleNamespace(refresh_recovery=self._refresh_recovery),
            automation_context=SimpleNamespace(spec_id=self.spec.spec_id, execution_run_id=self.run_id),
        )
        self.runner = MockAutomationRunner(self.store, self.bundle, self.spec)

    def _rank(self, available):
        sequence = len(self.observations) + 1
        self.observations.append({
            "accepted_sequence": sequence, "revision_id": f"rank-{sequence}",
            "kind": "top20_membership", "observation_key": f"rank-{sequence}",
            "available_at": available.isoformat(),
            "payload": {"codes": ["005930", *(f"{code:06d}" for code in range(1, 20))]},
        })

    def _bar(self, started_at, close, high):
        sequence = len(self.observations) + 1
        ended_at = started_at + timedelta(minutes=1)
        self.observations.append({
            "accepted_sequence": sequence, "revision_id": f"bar-{sequence}",
            "kind": "minute_bar", "subject": "005930:KRX", "venue": "KRX",
            "observation_key": started_at.isoformat(),
            "available_at": (ended_at + timedelta(seconds=2)).isoformat(),
            "completeness": "complete", "value_kind": "actual",
            "payload": {
                "market": "KRX", "code": "005930", "bar_start": started_at.isoformat(),
                "bar_end": ended_at.isoformat(), "open": close - 50, "high": high,
                "low": close - 100, "close": close, "volume": 100,
                "trade_value_million_won": 7, "window_closed": True,
                "capture_quality": "complete", "finalization_source": "timer",
            },
        })

    async def _refresh_recovery(self):
        recovery = MockAccountRecovery(
            AccountSnapshot(ACCOUNT_REF, self.cash, 0, dict(self.positions), self.clock), (),
        )
        self.risk_revision += 1
        risk = build_mock_automation_risk_snapshot(
            recovery, self.execution.account_events("mock", ACCOUNT_REF, limit=1000).events,
            tuple(self.costs), self.execution.active_intents("mock", ACCOUNT_REF, self.run_id),
            execution_run_id=self.run_id, binding_revision=self.binding["binding_revision"],
            reconciliation_revision=self.risk_revision, observed_at=self.clock,
            broker_query_started_at=self.clock, broker_query_completed_at=self.clock,
        )
        self.repository.save_mock_automation_risk_snapshot(risk)
        return recovery

    async def _poll_bar(self, minute, close, high):
        started_at = self.profile.evaluation_start + timedelta(minutes=minute)
        self.clock = started_at + timedelta(minutes=1, seconds=2)
        self.runtime.heartbeat()
        self._rank(self.clock)
        self._bar(started_at, close, high)
        await self.runner.run_once()

    def _fill_pending(self, side, price):
        record = self.execution.load(self.runner.status["pending_intent_id"])
        self.assertIsNotNone(record)
        self.clock += timedelta(seconds=1)
        self.runtime.reconcile(record.intent.intent_id, BrokerOrderSnapshot(
            record.broker_order_id, ACCOUNT_REF, "005930", OrderState.FILLED, 1, 0, self.clock,
            (BrokerFill(f"workflow-{side}-fill", 1, price, self.clock),),
        ))
        self.positions = {"005930": 1} if side == "BUY" else {}
        self.cash += -price if side == "BUY" else price
        self.costs.append(DailyTradeCost(
            self.clock.astimezone(timezone(timedelta(hours=9))).date(), self.clock.date(),
            "005930", "매수" if side == "BUY" else "매도", price, price, 0, 0, 0,
            origin_scope=AccountScope("kiwoom", AccountEnvironment.MOCK, ACCOUNT_REF),
        ))

    def _cost_failing_monitor(self):
        case = self

        class Reader:
            async def read(self):
                return MockAccountRecovery(
                    AccountSnapshot(ACCOUNT_REF, case.cash, 0, dict(case.positions), case.clock), (),
                )

            async def read_trade_cost_pages(self, *_args):
                raise OSError("controlled cost query failure")

        return MockAccountMonitor(
            Reader(), self.runtime, self.execution, now_provider=lambda: self.clock,
            automation_risk_repository=self.repository,
            automation_account_scope=AccountScope("kiwoom", AccountEnvironment.MOCK, ACCOUNT_REF),
            automation_binding_revision=self.binding["binding_revision"],
        )

    async def test_shadow_and_runner_agree_on_entry_but_only_runner_applies_fills(self):
        shadow = CandidateMonitor(
            self.store, self.runner._config, poll_seconds=1, universe_max_age_seconds=300,
        )
        shadow_id = shadow.monitor_id
        self.assertNotEqual(self.run_id, shadow_id)
        self.assertEqual([], self.transport.calls, "both bootstraps must be order-free")

        with patch.object(
            self.store, "save_shadow_evaluation", wraps=self.store.save_shadow_evaluation,
        ) as shadow_write, patch(
            "kiwoom_monitor.central_server.mock_automation_runner."
            "dispatch_mock_automation_decision_from_risk",
            wraps=dispatch_mock_automation_decision_from_risk,
        ) as dispatch:
            await self._poll_bar(0, 71_000, 71_200)
            before_shadow = self.execution.account_events("mock", ACCOUNT_REF, limit=1000)
            self.assertEqual(2, shadow.run_once())
            self.assertEqual(before_shadow, self.execution.account_events("mock", ACCOUNT_REF, limit=1000))
            self.assertEqual(1, shadow_write.call_count)
            self.assertEqual(1, dispatch.call_count)
            shadow_decision = shadow_write.call_args.args[1]
            runner_decision = dispatch.call_args.args[2].to_dict()
            self.assertEqual("ENTER", runner_decision["final_action"])
            # Separate owners give distinct content IDs; all judgment/state fields agree.
            identity_fields = {"run_id", "snapshot_id", "decision_id"}
            self.assertEqual(
                {k: v for k, v in runner_decision.items() if k not in identity_fields},
                {k: v for k, v in shadow_decision.items() if k not in identity_fields},
            )
            self.assertNotEqual(runner_decision["decision_id"], shadow_decision["decision_id"])

        self.assertEqual(["BUY"], [call[1] for call in self.transport.calls])
        self.assertEqual("candidate", shadow._state.status)
        self.assertEqual("candidate", self.runner._state.status)
        self._fill_pending("BUY", 71_000)
        self.runner = MockAutomationRunner(self.store, self.bundle, self.spec)
        await self.runner.run_once()
        restarted_shadow = CandidateMonitor(
            self.store, self.runner._config, poll_seconds=1, universe_max_age_seconds=300,
        )
        self.assertEqual(0, restarted_shadow.run_once())
        self.assertEqual(shadow_id, restarted_shadow.monitor_id)
        self.assertEqual("open", self.runner._state.status)
        self.assertEqual(1, self.runner._state.position_quantity)
        self.assertEqual(shadow._state, restarted_shadow._state)
        self.assertIsNone(restarted_shadow._state.position_quantity)
        self.assertEqual(["BUY"], [call[1] for call in self.transport.calls])

    async def test_missing_or_mismatched_lineage_blocks_gate_before_new_writes(self):
        await self._poll_bar(0, 71_000, 71_200)
        gate = self.repository.load_mock_automation_decision_gates(ACCOUNT_REF)[0]
        lineage = self.store.load_mock_automation_lineage(
            ACCOUNT_REF, spec_id=gate.spec_id, admission_id=gate.admission_id,
            lease_receipt_id=gate.lease_receipt_id, recovery_decision_id=gate.recovery_decision_id,
        )
        cases = {"missing_" + role: {k: v for k, v in lineage.items() if k != role}
                 for role in lineage}
        mismatched = deepcopy(lineage)
        mismatched["admission"]["execution_run_id"] = "other-run"
        cases["mismatched_run"] = mismatched
        for name, documents in cases.items():
            with self.subTest(name=name), patch.object(
                self.store, "load_mock_automation_lineage", return_value=documents,
            ), patch.object(self.store, "upsert_documents", wraps=self.store.upsert_documents) as write:
                with self.assertRaises(ValueError):
                    self.repository.save_mock_automation_decision_gate(gate)
                write.assert_not_called()
        self.assertEqual(["BUY"], [call[1] for call in self.transport.calls])

    async def test_lineage_read_does_not_cache_or_replace_current_risk_recheck(self):
        await self._poll_bar(0, 71_000, 71_200)
        gate = self.repository.load_mock_automation_decision_gates(ACCOUNT_REF)[0]
        await self._refresh_recovery()  # A newer, native persisted reconciliation revision.
        with patch.object(self.store, "upsert_documents", wraps=self.store.upsert_documents) as write:
            with self.assertRaisesRegex(ValueError, "current risk evidence"):
                self.repository.save_mock_automation_decision_gate(gate)
            write.assert_not_called()
        self.assertEqual(["BUY"], [call[1] for call in self.transport.calls])

    async def test_breakout_runner_round_trip_restart_and_journal(self):
        self.assertEqual([], self.transport.calls, "bootstrap must not trade historical bars")
        await self._poll_bar(0, 71_000, 71_200)
        self.assertEqual("ORDER_SUBMITTED", self.runner.status["state"], self.runner.status)
        self.assertEqual("candidate", self.runner.status["strategy_state"])
        self._fill_pending("BUY", 71_000)
        # Restore the native checkpoint; the durable fill must create the position once.
        self.runner = MockAutomationRunner(self.store, self.bundle, self.spec)
        await self._poll_bar(1, 75_000, 75_200)
        self.assertEqual("ORDER_SUBMITTED", self.runner.status["state"], self.runner.status)
        self.assertEqual("open", self.runner.status["strategy_state"])
        self.assertEqual(["BUY", "SELL"], [call[1] for call in self.transport.calls])
        self._fill_pending("SELL", 75_000)
        await self.runner.run_once()
        self.assertEqual("cooldown", self.runner.status["strategy_state"])
        await self.runner.run_once()
        self.assertEqual(2, len(self.transport.calls), "idle polling must not resubmit orders")
        with tempfile.TemporaryDirectory() as directory:
            journal = JournalRepository(Path(directory) / "journal.sqlite3")
            projected = sync_journal_execution_projection(
                _ExecutionPageSource(self.execution, ACCOUNT_REF), journal,
                AccountScope("kiwoom", AccountEnvironment.MOCK, ACCOUNT_REF),
                page_limit=1000, now=self.clock,
            )
            fills = journal.load_execution_fill_projections(
                AccountScope("kiwoom", AccountEnvironment.MOCK, ACCOUNT_REF),
            )
        self.assertEqual(2, projected.detailed_fill_count)
        self.assertEqual(("workflow-BUY-fill", "workflow-SELL-fill"),
                         tuple(row["broker_execution_id"] for row in fills))

    async def test_checkpoint_commit_failure_after_submission_does_not_resubmit_order(self):
        from tests.unit.test_mock_automation_runner import _checkpoint_commit_failures

        native_write = self.store.upsert_documents
        failing_write, evidence = _checkpoint_commit_failures(self.store, 1)

        def fail_submitted_checkpoint(collection, values):
            if (collection == "execution_mock_automation_runner_current"
                    and values[0]["document"]["status"].get("state") == "ORDER_SUBMITTED"):
                return failing_write(collection, values)
            return native_write(collection, values)

        with patch.object(self.store, "upsert_documents", side_effect=fail_submitted_checkpoint):
            with self.assertRaisesRegex(OSError, "checkpoint COMMIT failure"):
                await self._poll_bar(0, 71_000, 71_200)
            self.assertEqual(["BUY"], [call[1] for call in self.transport.calls])
            self.assertTrue(self.runner._checkpoint_pending)
            consumed_cursor = self.runner._cursor
            self.assertEqual(0, await self.runner.run_once())
        self.assertFalse(self.runner._checkpoint_pending)
        self.runner = MockAutomationRunner(self.store, self.bundle, self.spec)
        self.assertEqual(consumed_cursor, self.runner._cursor)
        self.assertEqual(0, await self.runner.run_once())
        self.assertEqual(["BUY"], [call[1] for call in self.transport.calls])
        self.assertEqual("candidate", self.runner._state.status)
        self.assertEqual(1, len(self.execution.active_intents("mock", ACCOUNT_REF, self.run_id)))
        self.assertEqual(0, evidence["remaining"])

    async def test_unfilled_entry_blocks_new_signal_without_creating_position(self):
        await self._poll_bar(0, 71_000, 71_200)
        self.assertEqual("ORDER_SUBMITTED", self.runner.status["state"], self.runner.status)
        await self._poll_bar(1, 75_000, 75_200)
        self.assertEqual("WAITING_ORDER", self.runner.status["state"])
        self.assertNotEqual("open", self.runner.status["strategy_state"])
        self.assertEqual(1, len(self.transport.calls))

    async def test_cost_query_failure_preserves_recovery_and_blocks_entry(self):
        self.bundle.monitor = self._cost_failing_monitor()
        await self._poll_bar(0, 71_000, 71_200)
        self.assertEqual("BLOCKED", self.runner.status["state"], self.runner.status)
        self.assertIn("DAILY_NET_PNL_UNKNOWN", self.runner.status["reason"])
        risk = self.repository.load_latest_mock_automation_risk(ACCOUNT_REF)
        self.assertIsNone(risk.daily_net_pnl_won)
        self.assertFalse(risk.cost_complete)
        self.assertTrue(risk.reconciliation_complete)
        self.assertIn("COST_QUERY_UNAVAILABLE", risk.missing_cost_reasons)
        self.assertEqual([], self.transport.calls)

    async def test_cost_query_failure_allows_exit_only_for_verified_held_quantity(self):
        await self._poll_bar(0, 71_000, 71_200)
        self.assertEqual("ORDER_SUBMITTED", self.runner.status["state"], self.runner.status)
        self._fill_pending("BUY", 71_000)
        self.bundle.monitor = self._cost_failing_monitor()
        await self._poll_bar(1, 75_000, 75_200)
        self.assertEqual("ORDER_SUBMITTED", self.runner.status["state"], self.runner.status)
        self.assertEqual(["BUY", "SELL"], [call[1] for call in self.transport.calls])
        self.assertEqual(1, self.transport.calls[-1][3])
        self.assertIsNone(self.repository.load_latest_mock_automation_risk(ACCOUNT_REF).daily_net_pnl_won)

    async def test_cost_outage_downgrades_prior_zero_and_recovers_after_successful_query(self):
        monitor = self._cost_failing_monitor()
        with patch.object(monitor._reader, "read_trade_cost_pages", new=AsyncMock(return_value=())):
            await monitor.refresh_recovery()
        before = self.repository.load_latest_mock_automation_risk(ACCOUNT_REF)
        self.assertEqual(0, before.daily_net_pnl_won)
        await monitor.refresh_recovery()
        unavailable = self.repository.load_latest_mock_automation_risk(ACCOUNT_REF)
        self.assertIsNone(unavailable.daily_net_pnl_won)
        self.assertFalse(unavailable.cost_complete)
        self.assertGreater(unavailable.reconciliation_revision, before.reconciliation_revision)
        with patch.object(monitor._reader, "read_trade_cost_pages", new=AsyncMock(return_value=())):
            await monitor.refresh_recovery()
        restored = self.repository.load_latest_mock_automation_risk(ACCOUNT_REF)
        self.assertEqual(0, restored.daily_net_pnl_won)
        self.assertTrue(restored.cost_complete)
        self.assertGreater(restored.reconciliation_revision, unavailable.reconciliation_revision)

    async def test_failed_account_recovery_is_not_treated_as_only_a_cost_outage(self):
        monitor = self._cost_failing_monitor()
        with patch.object(monitor._reader, "read", new=AsyncMock(side_effect=OSError("balance unavailable"))):
            with self.assertRaisesRegex(OSError, "balance unavailable"):
                await monitor.refresh_recovery()
        self.assertIsNone(self.repository.load_latest_mock_automation_risk(ACCOUNT_REF))
        self.assertEqual([], self.transport.calls)

    async def test_unknown_submission_is_not_retried_after_new_bars_or_restart(self):
        with patch.object(self.transport, "submit", side_effect=SubmissionUnknown("response lost")) as submit:
            await self._poll_bar(0, 71_000, 71_200)
            pending = self.execution.load(self.runner.status["pending_intent_id"])
            self.assertEqual(OrderState.SUBMISSION_UNKNOWN, pending.state)
            self.runner = MockAutomationRunner(self.store, self.bundle, self.spec)
            await self._poll_bar(1, 75_000, 75_200)
            await self.runner.run_once()
            self.assertEqual("WAITING_ORDER", self.runner.status["state"])
            self.assertEqual(1, submit.call_count)
            self.assertNotEqual("open", self.runner.status["strategy_state"])

    async def test_persisted_stop_survives_restart_without_submitting_or_cancelling(self):
        await self._poll_bar(0, 71_000, 71_200)
        self.assertEqual("ORDER_SUBMITTED", self.runner.status["state"], self.runner.status)
        self._fill_pending("BUY", 71_000)
        emergency_stop_mock_automation(
            self.repository, self.runtime, account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id,
            stopped_at=self.clock, reason="workflow stop",
        )
        self.runner = MockAutomationRunner(self.store, self.bundle, self.spec)
        await self._poll_bar(1, 75_000, 75_200)
        self.assertEqual("STOPPED", self.runner.status["state"])
        self.assertEqual("open", self.runner.status["strategy_state"])
        self.assertEqual(1, len(self.transport.calls))


class MockUnfilledPurposeGateTests(unittest.IsolatedAsyncioTestCase):
    _rank = MockAutomationWorkflowTests._rank
    _bar = MockAutomationWorkflowTests._bar
    _refresh_recovery = MockAutomationWorkflowTests._refresh_recovery

    def setUp(self):
        MockAutomationWorkflowTests.setUp(self)

    async def approved_buy(self, price=1000):
        from tests.unit.test_order_lifecycle import UnfilledOrderManagementTests
        self.base = UnfilledOrderManagementTests()
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        recovery = await self._refresh_recovery()
        risk = self.repository.load_latest_mock_automation_risk(ACCOUNT_REF)
        record_mock_automation_recovery_from_risk(self.repository, self.runtime, recovery, risk,
                                                account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id)
        body = _automation_decision(self.run_id, 'ENTER', self.clock).to_dict()
        body.pop('decision_id')
        body.update(quantity=10, signal_reference_price=price, required_capital_won=10 * price)
        decision = StrategyDecision(decision_id='decision_' + _hash(body), **body)
        gate, record, _ = dispatch_mock_automation_decision_from_risk(
            self.repository, self.runtime, decision, recovery, risk, account_ref=ACCOUNT_REF,
            spec_id=self.spec.spec_id, strategy_ref=self.spec.strategy_ref)
        self.assertEqual('APPROVED_FOR_SINGLE_SUBMISSION', gate.status.value, gate.reasons)
        self.intent = record.intent
        self.base.target = replace(self.base.target, reference_price_won=price)
        self.base.assessment = replace(self.base.assessment, candidate_limit_price_won=price + 1,
                                       reprice_evidence=replace(self.base.assessment.reprice_evidence,
                                                               candidate_limit_price_won=price + 1))
        self.policy = replace(self.base.policy, anchor_intent_id=self.intent.intent_id,
                              anchor_price_won=price,
                              anchor_available_at=self.clock, valid_until=self.clock + timedelta(seconds=60),
                              wait_until=self.clock + timedelta(seconds=30),
                              protection_deadline=self.clock + timedelta(seconds=15))
        self.transport.cancel = lambda *_args: None
        MockUnfilledNativeAttemptTests.confirm_cancel(self, record, filled=3)

    async def test_approved_buy_binds_fixed_child_without_relabelling_add_or_dispatch(self):
        await self.approved_buy()
        before = len(self.transport.calls)
        plan = await MockUnfilledNativeAttemptTests.preview(self)
        gate = plan['replacement_gate']
        self.assertEqual('VALIDATED_FOR_OFFLINE_REPLACEMENT', gate['status'], gate['reasons'])
        self.assertEqual('ADD', gate['action'])
        self.assertIn('NON_ACTION_DECISION', plan['native_gate']['reasons'])
        self.assertEqual(plan['children'][-1]['intent'], gate['child_payload'])
        self.assertEqual(7, gate['child_payload']['quantity'])
        self.assertFalse(gate['order_authorized'])
        self.assertEqual(before, len(self.transport.calls))
        self.assertIsNone(self.execution.load(gate['child_intent_id']))
        self.assertEqual(1, len(self.repository.load_mock_automation_decision_gates(ACCOUNT_REF)))

    async def test_original_buy_approval_is_required(self):
        await self.approved_buy()
        with patch.object(self.repository, 'load_mock_automation_approved_gate', return_value=None), patch.object(
                self.runner._repository, 'load_mock_automation_approved_gate', return_value=None):
            plan = await MockUnfilledNativeAttemptTests.preview(self)
        self.assertIn('ORIGINAL_BUY_APPROVAL_LINEAGE_UNVERIFIED', plan['replacement_gate']['reasons'])

    async def test_total_post_replacement_capital_includes_already_held_shares(self):
        self.cash = 20_000_000  # Funding is sufficient; operating capital is capped at 10M.
        await self.approved_buy(price=999500)
        price = 1000001
        self.base.assessment = replace(self.base.assessment, candidate_limit_price_won=price,
                                       reprice_evidence=replace(self.base.assessment.reprice_evidence, candidate_limit_price_won=price))
        plan = await MockUnfilledNativeAttemptTests.preview(self)
        self.assertLess(7 * price, self.spec.maximum_capital_won)
        self.assertIn('CAPITAL_LIMIT_EXCEEDED_OR_UNKNOWN', plan['replacement_gate']['reasons'])

    async def test_fixed_child_is_not_silently_repriced_on_a_new_cut(self):
        await self.approved_buy()
        first = await MockUnfilledNativeAttemptTests.preview(self)
        self.base.assessment = replace(self.base.assessment, candidate_limit_price_won=1002,
                                      reprice_evidence=replace(self.base.assessment.reprice_evidence, candidate_limit_price_won=1002))
        second = await MockUnfilledNativeAttemptTests.preview(self)
        self.assertEqual(first['children'], second['children'])
        self.assertEqual('BLOCKED', second['replacement_gate']['status'])
        self.assertIn('FIXED_CHILD_NOT_CURRENT_OR_EXPIRED', second['replacement_gate']['reasons'])

    async def test_protection_pending_blocks_opportunity_gate_without_changing_fixed_child(self):
        await self.approved_buy()
        first = await MockUnfilledNativeAttemptTests.preview(self)
        self.runner.request_protection_review(reason='hard_risk', input_ref='risk:hard-1', requested_at=self.clock)
        second = await MockUnfilledNativeAttemptTests.preview(self)
        self.assertEqual(first['children'], second['children'])
        self.assertIn('PROTECTION_REVIEW_BLOCKS_OPPORTUNITY', second['replacement_gate']['reasons'])
        self.assertFalse(second['order_authorized'])

    async def test_profit_reduce_is_distinct_from_protective_exit(self):
        MockUnfilledNativeAttemptTests.setUp(self)
        MockUnfilledNativeAttemptTests.setup_sell(self, False)
        plan = await MockUnfilledNativeAttemptTests.preview(self)
        self.assertEqual('VALIDATED_FOR_OFFLINE_REPLACEMENT', plan['replacement_gate']['status'], plan['replacement_gate']['reasons'])
        self.assertEqual(('TAKE_PROFIT', 'REDUCE', 1), (plan['replacement_gate']['purpose'],
                         plan['replacement_gate']['action'], plan['replacement_gate']['child_payload']['quantity']))
        self.assertIn('NON_ACTION_DECISION', plan['native_gate']['reasons'])

    async def test_protective_exit_does_not_wait_for_ev_or_daily_profit_evidence(self):
        MockUnfilledNativeAttemptTests.setUp(self)
        MockUnfilledNativeAttemptTests.setup_sell(self, True)
        self.costs = []
        plan = await MockUnfilledNativeAttemptTests.preview(self)
        gate = plan['replacement_gate']
        self.assertEqual('VALIDATED_FOR_OFFLINE_REPLACEMENT', gate['status'], gate['reasons'])
        self.assertEqual('PROTECTIVE_EXIT', gate['purpose'])
        self.assertTrue(gate['risk_path_required'])
        self.assertFalse(gate['order_authorized'])
        self.assertEqual('OFF', gate['transport_state'])

    async def test_tampered_gate_payload_cannot_be_restored_as_a_fixed_child(self):
        from kiwoom_monitor.application.order_lifecycle import seal_unfilled_attempt_plan, validate_unfilled_attempt_plan
        await self.approved_buy()
        plan = await MockUnfilledNativeAttemptTests.preview(self)
        plan['replacement_gate']['child_payload'] = {**plan['replacement_gate']['child_payload'], 'quantity': 999}
        with self.assertRaisesRegex(ValueError, 'fixed_child_mismatch'):
            validate_unfilled_attempt_plan(seal_unfilled_attempt_plan(plan))

    def confirm_cancel(self, *args, **kwargs):
        return MockUnfilledNativeAttemptTests.confirm_cancel(self, *args, **kwargs)

    async def inputs(self):
        return await MockUnfilledNativeAttemptTests.inputs(self)


class MockProtectionReviewTests(unittest.IsolatedAsyncioTestCase):
    _rank = MockAutomationWorkflowTests._rank
    _bar = MockAutomationWorkflowTests._bar
    _refresh_recovery = MockAutomationWorkflowTests._refresh_recovery

    def setUp(self):
        MockAutomationWorkflowTests.setUp(self)

    def request(self, **changes):
        arguments = dict(reason='hard_risk_limit', input_ref='native-risk-cut-1', requested_at=self.clock)
        arguments.update(changes)
        return self.runner.request_protection_review(**arguments)

    async def flat_cut(self):
        recovery = await self._refresh_recovery()
        risk = self.repository.load_latest_mock_automation_risk(ACCOUNT_REF)
        record_mock_automation_recovery_from_risk(self.repository, self.runtime, recovery, risk,
                                                account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id)
        return recovery, risk

    async def test_pending_request_preempts_observations_and_strategy_and_has_no_transport(self):
        first = self.request()
        cursor = self.runner._cursor
        with patch.object(self.store, 'load_observation_revisions_after', side_effect=AssertionError('loaded bars')), patch.object(
                self.runner, '_family', SimpleNamespace(evaluate_bar=lambda **_: self.fail('evaluated strategy'))):
            self.assertEqual(0, await self.runner.run_once())
        self.assertEqual('PROTECTION_REVIEW_REQUIRED', self.runner.status['state'])
        self.assertEqual(cursor, self.runner._cursor)
        self.assertEqual([], self.transport.calls)
        with patch.object(self.store, 'upsert_documents', wraps=self.store.upsert_documents) as writer:
            self.assertEqual(first, self.request())
            await self.runner.run_once()
            writer.assert_not_called()
        self.runner = MockAutomationRunner(self.store, self.bundle, self.spec)
        self.assertEqual(first, self.runner.status['protection_review'])

    async def test_existing_idle_loop_wakes_without_waiting_for_poll_or_a_new_bar(self):
        import asyncio
        self.runner._poll_seconds = 60
        idle = asyncio.Event(); processed = asyncio.Event()
        original = self.runner.run_once
        async def iteration():
            value = await original()
            if self.runner._protection_pending():
                processed.set()
            else:
                idle.set()
            return value
        with patch.object(self.runner, 'run_once', side_effect=iteration):
            await self.runner.start()
            await asyncio.wait_for(idle.wait(), 3)
            self.request()
            await asyncio.wait_for(processed.wait(), 3)
            self.assertEqual('PROTECTION_REVIEW_REQUIRED', self.runner.status['state'])
            await self.runner.close()
        self.assertEqual([], self.transport.calls)

    async def test_request_remains_immediately_visible_while_native_read_is_drained(self):
        import asyncio, threading
        entered = threading.Event(); release = threading.Event()
        native = self.runner._apply_new_fills
        def held():
            entered.set(); release.wait(5); native()
        with patch.object(self.runner, '_apply_new_fills', side_effect=held):
            iteration = asyncio.create_task(self.runner.run_once())
            self.assertTrue(await asyncio.to_thread(entered.wait, 3))
            notice = self.request()
            self.assertEqual(notice, self.runner.status['protection_review'])
            self.assertFalse(iteration.done())
            release.set(); await iteration
        self.assertEqual('PROTECTION_REVIEW_REQUIRED', self.runner.status['state'])
        self.assertEqual([], self.transport.calls)

    async def test_request_during_broker_recovery_prevents_new_dispatch(self):
        self.clock = self.profile.evaluation_start + timedelta(minutes=1, seconds=2)
        self._rank(self.clock)
        self._bar(self.profile.evaluation_start, 70100, 70200)
        original = self._refresh_recovery
        async def interrupt():
            recovery = await original()
            self.request()
            return recovery
        with patch.object(self.bundle.monitor, 'refresh_recovery', side_effect=interrupt):
            await self.runner.run_once()
        self.assertTrue(self.runner._protection_pending())
        self.assertEqual([], self.transport.calls)

    async def test_flat_resolution_requires_current_native_cut_and_rearms_on_restart(self):
        notice = self.request()
        await self.runner.run_once()
        recovery, risk = await self.flat_cut()
        result = self.runner.resolve_protection_review(expected_revision=notice['revision'], recovery=recovery, risk=risk)
        self.assertEqual('RESOLVED_FLAT', result['state'])
        self.assertFalse(self.runner._protection_pending())
        self.runner = MockAutomationRunner(self.store, self.bundle, self.spec)
        self.assertTrue(self.runner._protection_pending())
        self.clock += timedelta(seconds=6)
        with self.assertRaisesRegex(ValueError, 'native_flat_unverified'):
            self.runner.resolve_protection_review(expected_revision=notice['revision'], recovery=recovery, risk=risk)

    async def test_unknown_foreign_order_stop_and_superseding_request_cannot_clear_protection(self):
        notice = self.request()
        recovery, risk = await self.flat_cut()
        for active in ((('foreign-queued-order',), True), ((), False)):
            with patch.object(self.execution, 'account_active_intents', return_value=active):
                with self.assertRaisesRegex(ValueError, 'native_flat_unverified'):
                    self.runner.resolve_protection_review(expected_revision=notice['revision'], recovery=recovery, risk=risk)
        self.request(input_ref='native-risk-cut-2')
        with self.assertRaisesRegex(ValueError, 'revision_changed'):
            self.runner.resolve_protection_review(expected_revision=notice['revision'], recovery=recovery, risk=risk)
        emergency_stop_mock_automation(self.repository, self.runtime, account_ref=ACCOUNT_REF,
                                       spec_id=self.spec.spec_id, stopped_at=self.clock, reason='test stop')
        await self.runner.run_once()
        self.assertEqual('STOPPED', self.runner.status['state'])
        self.assertTrue(self.runner._protection_pending())

    async def test_ack_loss_and_native_change_cannot_silently_disarm_on_restart(self):
        notice = self.request()
        await self.runner.run_once()
        recovery, risk = await self.flat_cut()
        native = self.store.upsert_documents
        def lost_ack(collection, rows):
            native(collection, rows)
            raise OSError('controlled protection ACK loss')
        with patch.object(self.store, 'upsert_documents', side_effect=lost_ack):
            with self.assertRaisesRegex(OSError, 'ACK loss'):
                self.runner.resolve_protection_review(expected_revision=notice['revision'], recovery=recovery, risk=risk)
        self.assertTrue(self.runner._protection_pending())
        self.runner = MockAutomationRunner(self.store, self.bundle, self.spec)
        self.assertTrue(self.runner._protection_pending())

    async def test_new_request_during_resolution_ack_keeps_newer_revision_armed(self):
        notice = self.request()
        await self.runner.run_once()
        recovery, risk = await self.flat_cut()
        native = self.store.upsert_documents
        def new_risk_during_ack(collection, rows):
            native(collection, rows)
            self.request(input_ref='new-hard-risk-cut')
        with patch.object(self.store, 'upsert_documents', side_effect=new_risk_during_ack):
            with self.assertRaisesRegex(ValueError, 'native_state_changed'):
                self.runner.resolve_protection_review(expected_revision=notice['revision'], recovery=recovery, risk=risk)
        self.assertTrue(self.runner._protection_pending())
        self.assertEqual(2, self.runner.status['protection_review']['revision'])

    async def test_protection_notice_is_durable_even_while_bootstrap_is_not_ready(self):
        self.runner._bootstrap_pending = True
        self.request()
        await self.runner.run_once()
        self.runner = MockAutomationRunner(self.store, self.bundle, self.spec)
        self.assertTrue(self.runner._bootstrap_pending)
        self.assertTrue(self.runner._protection_pending())

    async def test_resolution_rechecks_owner_and_risk_age_after_slow_ack(self):
        notice = self.request()
        await self.runner.run_once()
        recovery, risk = await self.flat_cut()
        native = self.store.upsert_documents
        def slow_ack(collection, rows):
            native(collection, rows)
            self.clock += timedelta(seconds=6)
        with patch.object(self.store, 'upsert_documents', side_effect=slow_ack):
            with self.assertRaisesRegex(ValueError, 'cut_expired_during_ack'):
                self.runner.resolve_protection_review(expected_revision=notice['revision'], recovery=recovery, risk=risk)
        self.assertTrue(self.runner._protection_pending())

    async def test_resolution_keeps_ram_armed_until_native_ack_and_revalidation(self):
        import asyncio, threading
        notice = self.request()
        await self.runner.run_once()
        recovery, risk = await self.flat_cut()
        entered, release = threading.Event(), threading.Event()
        native = self.store.upsert_documents
        def held_ack(collection, rows):
            entered.set(); release.wait(5)
            return native(collection, rows)
        with patch.object(self.store, 'upsert_documents', side_effect=held_ack):
            work = asyncio.create_task(asyncio.to_thread(self.runner.resolve_protection_review,
                expected_revision=notice['revision'], recovery=recovery, risk=risk))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                self.assertTrue(self.runner._protection_pending())
                self.assertTrue(self.runner.status['protection_review_required'])
            finally:
                release.set()
            result = await work
        self.assertEqual('RESOLVED_FLAT', result['state'])
        self.assertFalse(self.runner._protection_pending())
        self.runner = MockAutomationRunner(self.store, self.bundle, self.spec)
        self.assertTrue(self.runner._protection_pending())

    async def test_malformed_checkpoint_and_closed_runner_do_not_drop_or_accept_requests(self):
        self.request()
        await self.runner.run_once()
        rows = self.store.load_documents('execution_mock_automation_runner_current', ACCOUNT_REF, 1)
        rows[0]['document']['protection_review']['account_ref'] = 'wrong-account'
        self.store.upsert_documents('execution_mock_automation_runner_current', rows)
        with self.assertRaisesRegex(ValueError, 'invalid_mock_protection_review'):
            MockAutomationRunner(self.store, self.bundle, self.spec)
        await self.runner.close()
        with self.assertRaisesRegex(RuntimeError, 'CLOSED'):
            self.request()


class MockNativeRiskProtectionTests(unittest.IsolatedAsyncioTestCase):
    _rank = MockAutomationWorkflowTests._rank
    _bar = MockAutomationWorkflowTests._bar
    _refresh_recovery = MockAutomationWorkflowTests._refresh_recovery
    setUp = MockAutomationWorkflowTests.setUp

    async def asyncSetUp(self):
        await self.runner.close()
        case = self
        class Reader:
            calls = 0
            cost_calls = 0
            async def read(self):
                self.calls += 1
                return MockAccountRecovery(AccountSnapshot(ACCOUNT_REF, case.cash, 0, dict(case.positions), case.clock), ())
            async def read_trade_cost_pages(self, *_args):
                self.cost_calls += 1
                return ({'trst_ovrl_trde_prps_array': [{
                    'cntr_dt': cost.fill_date.strftime('%Y%m%d'), 'trde_dt': cost.settlement_date.strftime('%Y%m%d'),
                    'stk_cd': cost.stock_code, 'io_tp_nm': cost.side, 'trde_amt': str(cost.gross_amount),
                    'exct_amt': str(cost.settlement_amount), 'cmsn': str(cost.commission),
                    'trde_agri_tax': str(cost.tax), 'tax_sum_cmsn': str(cost.total_cost)} for cost in case.costs]},)
        self.reader = Reader()
        self.monitor = MockAccountMonitor(self.reader, self.runtime, self.execution, now_provider=lambda: self.clock,
            automation_risk_repository=self.repository, automation_binding_revision=self.spec.binding_revision,
            automation_account_scope=self.spec.account_scope, new_orders_enabled_on_start=False)
        self.bundle.monitor = self.monitor
        self.runner = MockAutomationRunner(self.store, self.bundle, self.spec)

    async def asyncTearDown(self):
        await self.runner.close()
        await self.monitor.close()

    def make_loss(self):
        from tests.unit.test_order_lifecycle import _intent
        from kiwoom_monitor.domain.order_contract import OrderSide
        for key, side, quantity, price in (('native-loss-buy', OrderSide.BUY, 10, 70000),
                                           ('native-loss-sell', OrderSide.SELL, 9, 30000)):
            intent = replace(_intent(), intent_id=key, decision_id=key + '-decision', account_ref=ACCOUNT_REF,
                run_id=self.run_id, side=side, quantity=quantity, limit_price=price,
                created_at=self.clock, expires_at=self.clock + timedelta(seconds=60))
            record = self.runtime.submit_automation_intent(intent,
                AccountSnapshot(ACCOUNT_REF, self.cash, 0, dict(self.positions), self.clock),
                spec_id=self.spec.spec_id, control_revision=1)
            self.clock += timedelta(seconds=1)
            self.runtime.reconcile(intent.intent_id, BrokerOrderSnapshot(record.broker_order_id, ACCOUNT_REF,
                '005930', OrderState.FILLED, quantity, 0, self.clock,
                (BrokerFill(key + '-fill', quantity, price, self.clock),)))
            buy = side is OrderSide.BUY
            self.positions['005930'] = self.positions.get('005930', 0) + (quantity if buy else -quantity)
            amount = quantity * price
            self.cash += -amount if buy else amount
            self.costs.append(DailyTradeCost(self.clock.astimezone(timezone(timedelta(hours=9))).date(),
                self.clock.date(), '005930', '매수' if buy else '매도', amount, amount, 0, 0, 0,
                origin_scope=self.spec.account_scope))

    async def test_acknowledged_daily_loss_automatically_wakes_existing_runner_without_bars(self):
        import asyncio
        self.runner._poll_seconds = 60
        idle, processed = asyncio.Event(), asyncio.Event()
        native = self.runner.run_once
        async def iteration():
            result = await native()
            (processed if self.runner._protection_pending() else idle).set()
            return result
        with patch.object(self.runner, 'run_once', side_effect=iteration):
            await self.runner.start()
            await asyncio.wait_for(idle.wait(), 3)
            self.make_loss()
            calls = len(self.transport.calls)
            with patch.object(self.store, 'load_observation_revisions_after', side_effect=AssertionError('Risk waited for bars')):
                await self.monitor.refresh_recovery()
                await asyncio.wait_for(processed.wait(), 3)
        notice = self.runner.status['protection_review']
        self.assertEqual('EXIT_REQUIRED', notice['risk_trigger']['disposition'])
        self.assertEqual(['DAILY_LOSS_LIMIT_REACHED'], notice['risk_trigger']['reasons'])
        self.assertEqual(1, self.runner._state.position_quantity)
        self.assertFalse(notice['order_authorized'])
        self.assertEqual(calls, len(self.transport.calls))

    async def test_risk_listener_preserves_existing_position_and_runtime_listeners(self):
        notifications = []
        self.monitor.set_position_observation_listener(lambda: notifications.append('position'))
        self.make_loss()
        await self.monitor.refresh_recovery()
        self.assertIn('position', notifications)
        self.assertEqual(self.monitor._runtime_order_changed, self.runtime._observation_listener)
        self.assertTrue(self.runner._protection_pending())

    async def test_repeated_native_revision_coalesces_and_ram_reads_have_no_db(self):
        self.make_loss()
        await self.monitor.refresh_recovery()
        await self.runner.run_once()
        first = deepcopy(self.runner.status['protection_review'])
        await self.monitor.refresh_recovery()
        self.assertEqual(first, self.runner.status['protection_review'])
        with patch.object(self.store, '_connect', side_effect=AssertionError('RAM delivery queried DB')):
            for _ in range(100):
                cut = self.monitor.latest_automation_risk_cut
                self.runner._on_native_risk(cut)
                self.assertEqual({'risk', 'account'}, set(cut))
                cut['account'].positions['005930'] = 999
        self.assertEqual(1, self.monitor.latest_automation_risk_cut['account'].positions['005930'])
        with patch.object(self.store, 'upsert_documents', wraps=self.store.upsert_documents) as writes:
            await self.runner.run_once()
            writes.assert_not_called()

    async def test_ack_failure_does_not_publish_an_unconfirmed_risk_cut(self):
        self.make_loss()
        native = self.repository.save_mock_automation_risk_snapshot
        def lost_ack(snapshot):
            native(snapshot); raise OSError('controlled native Risk ACK lost')
        with patch.object(self.repository, 'save_mock_automation_risk_snapshot', side_effect=lost_ack):
            with self.assertRaisesRegex(OSError, 'ACK lost'):
                await self.monitor.refresh_recovery()
        self.assertIsNone(self.monitor.latest_automation_risk_cut)
        self.assertFalse(self.runner._protection_pending())
        self.assertIsNotNone(self.repository.load_latest_mock_automation_risk(ACCOUNT_REF))

    async def test_repeated_same_balance_mismatch_counter_does_not_create_new_requests(self):
        self.positions = {'005930': 1}  # Broker position with no run-owned FILL.
        await self.monitor.refresh_recovery()
        first = deepcopy(self.runner.status['protection_review'])
        await self.monitor.refresh_recovery()
        self.assertGreaterEqual(self.monitor.latest_automation_risk_cut['risk'].balance_mismatch_count, 2)
        self.assertEqual(first, self.runner.status['protection_review'])
        self.positions = {'005930': 2}  # Actual exposure changed: wake a new review.
        await self.monitor.refresh_recovery()
        self.assertEqual(2, self.runner.status['protection_review']['revision'])

    async def test_first_recovery_before_runner_registration_is_delivered_without_another_read(self):
        await self.runner.close()
        self.make_loss()
        await self.monitor.refresh_recovery()
        counts = self.reader.calls, self.reader.cost_calls
        self.runner = MockAutomationRunner(self.store, self.bundle, self.spec)
        self.assertTrue(self.runner._protection_pending())
        self.assertEqual(counts, (self.reader.calls, self.reader.cost_calls))

    async def test_unknown_exposure_reconciles_first_and_does_not_duplicate_submission(self):
        from tests.unit.test_order_lifecycle import _intent
        from kiwoom_monitor.infrastructure.kiwoom_rest.mock_execution import SubmissionUnknown
        intent = replace(_intent(), account_ref=ACCOUNT_REF, run_id=self.run_id,
                         created_at=self.clock, expires_at=self.clock + timedelta(seconds=60))
        with patch.object(self.transport, 'submit', side_effect=SubmissionUnknown('native unknown fixture')):
            self.runtime.submit_automation_intent(intent, AccountSnapshot(ACCOUNT_REF, self.cash, 0, {}, self.clock),
                                                 spec_id=self.spec.spec_id, control_revision=1)
        await self.monitor.refresh_recovery()
        await self.runner.run_once()
        trigger = self.runner.status['protection_review']['risk_trigger']
        self.assertEqual('RECONCILE_REQUIRED', trigger['disposition'])
        self.assertIn('UNKNOWN_EXPOSURE', trigger['reasons'])
        self.assertEqual([], self.transport.calls)

    async def test_wrong_binding_run_or_account_cut_is_rejected(self):
        await self.monitor.refresh_recovery()
        cut = self.monitor.latest_automation_risk_cut
        for change in ({'binding_revision': self.spec.binding_revision + 1}, {'execution_run_id': 'other-run'},
                       {'account_ref': '11111111-1111-4111-8111-111111111111'}):
            with self.assertRaisesRegex(ValueError, 'mismatch'):
                self.runner._on_native_risk({**cut, 'risk': replace(cut['risk'], **change)})
        self.assertFalse(self.runner._protection_pending())

    async def test_cost_outage_alone_does_not_create_a_false_hard_exit(self):
        self.make_loss()
        with patch.object(self.reader, 'read_trade_cost_pages', side_effect=OSError('cost outage')):
            await self.monitor.refresh_recovery()
        self.assertFalse(self.runner._protection_pending())
        risk = self.monitor.latest_automation_risk_cut['risk']
        self.assertIsNone(risk.daily_net_pnl_won)
        self.assertTrue(risk.reconciliation_complete)

    async def test_stop_is_not_resumed_by_a_later_automatic_risk_request(self):
        self.make_loss()
        emergency_stop_mock_automation(self.repository, self.runtime, account_ref=ACCOUNT_REF,
            spec_id=self.spec.spec_id, stopped_at=self.clock, reason='native risk stop test')
        await self.monitor.refresh_recovery()
        await self.runner.run_once()
        self.assertEqual('STOPPED', self.runner.status['state'])
        self.assertTrue(self.runner._protection_pending())
        self.assertEqual(2, len(self.transport.calls))

    async def test_listener_ownership_close_and_restart_preserve_the_armed_trigger(self):
        self.make_loss()
        await self.monitor.refresh_recovery()
        await self.runner.run_once()
        with self.assertRaisesRegex(RuntimeError, 'ALREADY_OWNED'):
            MockAutomationRunner(self.store, self.bundle, self.spec)
        self.assertEqual(self.runner._native_risk_listener, self.monitor._automation_risk_listener)
        await self.runner.close()
        self.runner = MockAutomationRunner(self.store, self.bundle, self.spec)
        self.assertEqual(1, self.runner.status['protection_review']['revision'])
        self.assertTrue(self.runner._protection_pending())

    async def test_runtime_change_invalidates_cached_native_risk_cut(self):
        await self.monitor.refresh_recovery()
        self.assertIsNotNone(self.monitor.latest_automation_risk_cut)
        self.make_loss()
        self.assertIsNone(self.monitor.latest_automation_risk_cut)
        await self.monitor.refresh_recovery()
        self.assertTrue(self.runner._protection_pending())

    async def test_corrupt_saved_risk_trigger_cannot_be_silently_dropped(self):
        self.make_loss()
        await self.monitor.refresh_recovery()
        await self.runner.run_once()
        await self.runner.close()
        rows = self.store.load_documents('execution_mock_automation_runner_current', ACCOUNT_REF, 1)
        rows[0]['document']['protection_review']['risk_trigger']['binding_revision'] += 1
        self.store.upsert_documents('execution_mock_automation_runner_current', rows)
        with self.assertRaisesRegex(ValueError, 'invalid_mock_native_risk_trigger'):
            MockAutomationRunner(self.store, self.bundle, self.spec)


class MockUnfilledOffIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """OFF acceptance: compose native gates, runner and persisted attempts.

    Broker calls are made explicitly by the isolated fixture only. No live
    producer, automatic deadline dispatcher or account activation is implied.
    """
    _rank = MockAutomationWorkflowTests._rank
    _bar = MockAutomationWorkflowTests._bar
    _refresh_recovery = MockAutomationWorkflowTests._refresh_recovery
    setUp = MockUnfilledPurposeGateTests.setUp
    approved_buy = MockUnfilledPurposeGateTests.approved_buy
    inputs = MockUnfilledPurposeGateTests.inputs
    confirm_cancel = MockUnfilledPurposeGateTests.confirm_cancel

    @contextmanager
    def count_native(self):
        statements, connections = [], []
        connect = self.store._connect
        def traced():
            connection = connect()
            connections.append(1)
            connection.set_trace_callback(statements.append)
            return connection
        counts = {}
        with patch.object(self.store, '_connect', side_effect=traced):
            yield counts
        counts.update(connections=len(connections),
                      selects=sum(row.lstrip().upper().startswith('SELECT ') for row in statements),
                      commits=statements.count('COMMIT'))

    async def preview(self):
        return await MockUnfilledNativeAttemptTests.preview(self)

    async def test_status_and_real_observation_polling_never_repeat_native_preflight(self):
        await self.approved_buy()
        self.runner._apply_new_fills()
        # The bootstrap fixture is RAM history, but polling uses the actual
        # SQLite observation reader here, rather than its fixture lambda.
        def native_observations(*args):
            return SQLiteQueryStore.load_observation_revisions_after(self.store, *args)
        before, after = [], []
        with patch.object(self.store, 'load_observation_revisions_after', side_effect=native_observations):
            for _ in range(5):
                with self.count_native() as counts:
                    await self.runner.run_once()
                before.append(counts)
            plan = await self.preview()
            self.assertEqual('VALIDATED_FOR_OFFLINE_REPLACEMENT', plan['replacement_gate']['status'])
            with patch.object(self.runner, 'preview_unfilled_execution', side_effect=AssertionError('poll repeated full preflight')):
                for _ in range(5):
                    with self.count_native() as counts:
                        await self.runner.run_once()
                    after.append(counts)
                with self.count_native() as display:
                    for _ in range(100):
                        self.runner.status
        self.assertEqual(before, after)
        self.assertEqual({'connections': 0, 'selects': 0, 'commits': 0}, display)
        self.assertEqual(1, len(self.transport.calls))
        self.poll_counts = {'before': before, 'after': after, 'status_100': display}

    async def test_due_hint_and_duplicate_protection_cue_do_not_poll_the_full_gate(self):
        MockUnfilledNativeAttemptTests.setUp(self)
        MockUnfilledNativeAttemptTests.setup_sell(self, True)
        plan = await self.preview()
        self.clock = datetime.fromisoformat(plan['next_check_at'])
        calls = len(self.transport.calls)
        with self.count_native() as cue:
            self.assertTrue(self.runner.status['unfilled_management_clock']['unfilled_management_due'])
            for _ in range(100):
                notice = self.runner.request_protection_review(reason='protection_deadline_due',
                    input_ref='deadline:' + plan['receipt_id'], requested_at=self.clock)
        self.assertEqual({'connections': 0, 'selects': 0, 'commits': 0}, cue)
        self.assertEqual(1, notice['revision'])
        with patch.object(self.runner, 'preview_unfilled_execution', side_effect=AssertionError('cue ran full gate')), patch.object(
                self.runner._repository, 'load_latest_mock_automation_risk', side_effect=AssertionError('cue queried full Risk')):
            with self.count_native() as changed:
                await self.runner.run_once()
            with self.count_native() as repeated:
                for _ in range(10):
                    await self.runner.run_once()
        self.assertEqual(1, changed['commits'])
        self.assertEqual(0, repeated['commits'])
        self.assertEqual('PROTECTION_REVIEW_REQUIRED', self.runner.status['state'])
        self.assertEqual(calls, len(self.transport.calls))
        self.protection_counts = {'due_hint_and_100_cues': cue, 'first_processing': changed,
                                  'ten_repeated_polls': repeated, 'automatic_gate_calls': 0}

    async def test_active_runner_rejects_explicit_full_preflight_before_native_io(self):
        import asyncio
        await self.approved_buy()
        await self.preview()
        arguments = await self.inputs()
        self.runner._poll_seconds = 60
        idle = asyncio.Event()
        native = self.runner.run_once
        async def iteration():
            result = await native(); idle.set(); return result
        with patch.object(self.runner, 'run_once', side_effect=iteration):
            await self.runner.start()
            await asyncio.wait_for(idle.wait(), 3)
            with self.count_native() as rejected:
                with self.assertRaisesRegex(RuntimeError, 'requires_idle_ready_runner|already_in_progress'):
                    self.runner.preview_unfilled_execution(**arguments)
            await self.runner.close()
        self.assertEqual({'connections': 0, 'selects': 0, 'commits': 0}, rejected)
        self.assertEqual(1, len(self.transport.calls))

    async def test_partial_fill_ack_restart_unknown_and_stop_join_without_adapter_submission(self):
        from kiwoom_monitor.infrastructure.kiwoom_rest.mock_execution import SubmissionUnknown
        await self.approved_buy()
        first = await self.preview()
        self.confirm_cancel(MockUnfilledNativeAttemptTests.submit_child_fixture(self, first), filled=2)
        write = self.store.upsert_documents
        def lost_ack(collection, rows):
            write(collection, rows)
            if collection == 'execution_mock_automation_runner_current':
                raise OSError('controlled OFF integration ACK loss')
        with patch.object(self.store, 'upsert_documents', side_effect=lost_ack):
            with self.assertRaisesRegex(OSError, 'ACK loss'):
                await self.preview()
        reserved = deepcopy(self.runner._unfilled_attempt_plan['children'])
        self.runner = MockAutomationRunner(self.store, self.bundle, self.spec)
        second = await self.preview()
        self.assertEqual(reserved, second['children'])
        self.assertEqual(5, second['replacement_gate']['child_payload']['quantity'])
        self.assertEqual((1, 1), (second['native_reprices_used'], second['native_submission_attempts']))
        with patch.object(self.transport, 'submit', side_effect=SubmissionUnknown('fixture lost submit response')):
            self.record = MockUnfilledNativeAttemptTests.submit_child_fixture(self, second)
        self.snapshot = None
        self.runner = MockAutomationRunner(self.store, self.bundle, self.spec)
        unknown = await self.preview()
        self.assertEqual('WAIT', unknown['result']['action'])
        self.assertEqual('BLOCKED', unknown['replacement_gate']['status'])
        self.assertEqual(reserved, unknown['children'])
        self.assertEqual((2, 2), (unknown['native_reprices_used'], unknown['native_submission_attempts']))
        self.runner.request_protection_review(reason='unknown_exposure', input_ref='native-unknown:2', requested_at=self.clock)
        await self.runner.run_once()
        emergency_stop_mock_automation(self.repository, self.runtime, account_ref=ACCOUNT_REF,
            spec_id=self.spec.spec_id, stopped_at=self.clock, reason='OFF integration stop')
        await self.runner.run_once()
        self.runner = MockAutomationRunner(self.store, self.bundle, self.spec)
        await self.runner.run_once()
        self.assertEqual('STOPPED', self.runner.status['state'])
        self.assertTrue(self.runner.status['protection_review_required'])
        self.assertFalse(unknown['order_authorized'])
        self.assertEqual(2, len(self.transport.calls))  # Root + first child, fixture only.


class MockUnfilledNativeAttemptTests(unittest.IsolatedAsyncioTestCase):
    _rank = MockAutomationWorkflowTests._rank
    _bar = MockAutomationWorkflowTests._bar
    _refresh_recovery = MockAutomationWorkflowTests._refresh_recovery

    def setUp(self):
        from dataclasses import replace
        from tests.unit.test_order_lifecycle import UnfilledOrderManagementTests
        MockAutomationWorkflowTests.setUp(self)
        self.base = UnfilledOrderManagementTests(); self.base.setUp(); self.addCleanup(self.base.doCleanups)
        self.intent = replace(self.base.intent, account_ref=ACCOUNT_REF, run_id=self.run_id,
                              created_at=self.clock, expires_at=self.clock + timedelta(seconds=60))
        self.policy = replace(self.base.policy, anchor_available_at=self.clock,
                              valid_until=self.clock + timedelta(seconds=60),
                              wait_until=self.clock + timedelta(seconds=30),
                              protection_deadline=self.clock + timedelta(seconds=15))
        self.transport.cancel = lambda *_args: None
        self.record = self.runtime.submit_automation_intent(
            self.intent, AccountSnapshot(ACCOUNT_REF, self.cash, 0, {}, self.clock),
            spec_id=self.spec.spec_id, control_revision=1,
        )
        self.confirm_cancel(self.record, filled=3)

    def confirm_cancel(self, record, filled=0):
        self.runtime.cancel(record.intent.intent_id)
        self.clock += timedelta(seconds=1)
        fills = (BrokerFill('fill-' + record.intent.intent_id, filled, record.intent.limit_price, self.clock),) if filled else ()
        self.snapshot = BrokerOrderSnapshot(record.broker_order_id, ACCOUNT_REF, '005930', OrderState.CANCELLED,
                                            filled, 0, self.clock, fills)
        self.record = self.runtime.reconcile(record.intent.intent_id, self.snapshot)
        if filled:
            buy = record.intent.side.value == 'BUY'
            self.positions['005930'] = self.positions.get('005930', 0) + (filled if buy else -filled)
            amount = filled * record.intent.limit_price
            self.cash += -amount if buy else amount
            self.costs.append(DailyTradeCost(
                self.clock.astimezone(timezone(timedelta(hours=9))).date(), self.clock.date(), '005930',
                '매수' if buy else '매도', amount, amount, 0, 0, 0, origin_scope=AccountScope('kiwoom', AccountEnvironment.MOCK, ACCOUNT_REF),
            ))

    async def inputs(self):
        from dataclasses import replace
        from kiwoom_monitor.application.position_target import PositionAccountObservation
        recovery = await self._refresh_recovery()
        risk = self.repository.load_latest_mock_automation_risk(ACCOUNT_REF)
        record_mock_automation_recovery_from_risk(self.repository, self.runtime, recovery, risk,
                                                  account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id)
        record = self.record
        active = self.execution.active_intents('mock', ACCOUNT_REF, self.run_id)
        observation = PositionAccountObservation(recovery.account, active, 'native-cut:' + str(self.risk_revision),
                                                 self.clock, risk.reconciliation_complete, True, (), risk_snapshot=risk)
        target = replace(self.base.target, account_ref=ACCOUNT_REF, decision_time=self.clock,
                         reference_price_available_at=self.clock, account_input_ref=observation.input_ref,
                         orders_input_ref=observation.input_ref, reconciliation_complete=risk.reconciliation_complete)
        compare_quantity = (self.positions.get('005930', 0) - target.target_quantity
                            if self.policy.purpose == 'TAKE_PROFIT' else record.intent.quantity - record.filled_quantity)
        evidence = replace(self.base.assessment.reprice_evidence, intent_id=record.intent.intent_id,
                           pending_quantity=compare_quantity,
                           available_at=self.clock) if self.base.assessment.reprice_evidence and record.intent.quantity > record.filled_quantity else None
        assessment = replace(self.base.assessment, thesis_available_at=self.clock,
                             thesis_valid_until=self.policy.valid_until, risk_available_at=self.clock,
                             risk_valid_until=self.policy.valid_until, price_available_at=self.clock,
                             reprice_evidence=evidence)
        return {'policy': self.policy, 'broker_snapshot': self.snapshot, 'target': target,
                'observation': observation, 'assessment': assessment, 'recovery': recovery, 'risk': risk}

    async def preview(self, **changes):
        arguments = await self.inputs(); arguments.update(changes)
        if self.runner._unfilled_preflight is None:
            self.runner.preview_unfilled_management(**{k: v for k, v in arguments.items() if k not in {'recovery', 'risk'}})
        return self.runner.preview_unfilled_execution(**arguments)

    def setup_sell(self, protective):
        from dataclasses import replace
        from kiwoom_monitor.domain.order_contract import OrderSide
        self.intent = replace(self.intent, intent_id='native-sell-root', side=OrderSide.SELL, quantity=3,
                              created_at=self.clock, expires_at=self.clock + timedelta(seconds=60))
        self.policy = replace(self.policy, purpose='PROTECTIVE_EXIT' if protective else 'TAKE_PROFIT',
                              anchor_intent_id=self.intent.intent_id, anchor_available_at=self.clock,
                              valid_until=self.clock + timedelta(seconds=60), wait_until=self.clock + timedelta(seconds=30),
                              protection_deadline=self.clock + timedelta(seconds=15))
        self.base.target = replace(self.base.target, action='EXIT' if protective else 'REDUCE', target_quantity=0 if protective else 1)
        evidence = self.base.assessment.reprice_evidence
        self.base.assessment = replace(self.base.assessment, candidate_limit_price_won=990,
                                       reprice_evidence=None if protective else replace(evidence, candidate_limit_price_won=990))
        sell = self.runtime.submit_automation_intent(self.intent, AccountSnapshot(ACCOUNT_REF, self.cash, 0, self.positions, self.clock),
                                                     spec_id=self.spec.spec_id, control_revision=1)
        self.confirm_cancel(sell, filled=1)

    async def test_protective_sell_reuses_native_exit_gate_without_ev_and_without_dispatch(self):
        self.setup_sell(True)
        before = len(self.transport.calls)
        plan = await self.preview()
        self.assertEqual('EXIT', plan['native_gate']['action'])
        self.assertTrue(plan['result']['risk_path_required'])
        self.assertEqual(2, plan['children'][0]['intent']['quantity'])
        self.assertEqual('SELL', plan['children'][0]['intent']['side'])
        self.assertEqual(before, len(self.transport.calls))
        self.assertFalse(plan['order_authorized'])

    async def test_profit_reduce_stays_unsupported_by_legacy_gate(self):
        self.setup_sell(False)
        plan = await self.preview()
        self.assertEqual('REDUCE', plan['native_gate']['action'])
        self.assertIn('NON_ACTION_DECISION', plan['native_gate']['reasons'])
        self.assertEqual(1, plan['children'][0]['intent']['quantity'])
        self.assertFalse(plan['order_authorized'])

    def submit_child_fixture(self, plan):
        from kiwoom_monitor.application.order_lifecycle import unfilled_plan_intent
        intent = unfilled_plan_intent(plan['children'][-1]['intent'])
        return self.runtime.submit_automation_intent(intent, AccountSnapshot(ACCOUNT_REF, self.cash, 0, self.positions, self.clock),
                                                      spec_id=self.spec.spec_id, control_revision=1)

    async def test_existing_add_gate_is_not_relabelled_and_preview_never_dispatches(self):
        before = list(self.transport.calls)
        with patch.object(self.runtime, 'submit_automation_intent', side_effect=AssertionError('preview submitted')):
            plan = await self.preview()
        self.assertEqual('REPRICE', plan['result']['action'])
        self.assertEqual('ADD', plan['native_gate']['action'])
        self.assertIn('NON_ACTION_DECISION', plan['native_gate']['reasons'])
        self.assertEqual(0, plan['native_reprices_used'])
        self.assertFalse(plan['order_authorized'])
        self.assertEqual(before, self.transport.calls)
        self.assertIsNone(self.execution.load(plan['children'][0]['intent']['intent_id']))
        self.assertEqual((), self.repository.load_mock_automation_decision_gates(ACCOUNT_REF))

    async def test_two_native_attempts_partial_fill_restart_and_anchored_budget(self):
        first = await self.preview()
        self.confirm_cancel(self.submit_child_fixture(first), filled=2)
        second = await self.preview()
        self.assertEqual((1, 1, 2), (second['native_reprices_used'], second['native_submission_attempts'], len(second['children'])))
        self.assertEqual(5, second['children'][-1]['intent']['quantity'])
        self.confirm_cancel(self.submit_child_fixture(second), filled=1)
        self.runner = MockAutomationRunner(self.store, self.bundle, self.spec)
        third = await self.preview()
        self.assertEqual((2, 2, 3), (third['native_reprices_used'], third['native_submission_attempts'], len(third['children'])))
        self.assertEqual(4, third['children'][-1]['intent']['quantity'])
        self.assertEqual(6, self.runner._state.position_quantity)
        self.confirm_cancel(self.submit_child_fixture(third))
        exhausted = await self.preview()
        self.assertEqual(3, exhausted['native_reprices_used'])
        self.assertEqual('ABANDON', exhausted['result']['action'])
        self.assertEqual(3, len(exhausted['children']))
        self.assertEqual(4, len(self.transport.calls))

    async def test_queued_consumes_budget_without_submission_and_unknown_never_adds_slot(self):
        from kiwoom_monitor.application.order_lifecycle import unfilled_plan_intent
        first = await self.preview()
        intent = unfilled_plan_intent(first['children'][0]['intent'])
        self.runtime._lifecycle.queue(intent)  # Native owner-fenced QUEUED fixture, no transport.
        self.record = self.execution.load(intent.intent_id)
        self.snapshot = None
        queued = await self.preview()
        self.assertEqual((1, 0, 1), (queued['native_reprices_used'], queued['native_submission_attempts'], len(queued['children'])))
        self.assertEqual('WAIT', queued['result']['action'])
        from kiwoom_monitor.infrastructure.kiwoom_rest.mock_execution import SubmissionUnknown
        with patch.object(self.transport, 'submit', side_effect=SubmissionUnknown('controlled unknown')):
            self.record = self.submit_child_fixture(queued)
        self.runner = MockAutomationRunner(self.store, self.bundle, self.spec)
        unknown = await self.preview()
        self.assertEqual((1, 1, 1), (unknown['native_reprices_used'], unknown['native_submission_attempts'], len(unknown['children'])))
        self.assertEqual('WAIT', unknown['result']['action'])
        self.assertEqual(1, len(self.transport.calls))

    async def test_lost_native_claim_and_wrong_child_content_fail_closed(self):
        from dataclasses import replace
        from kiwoom_monitor.application.order_lifecycle import unfilled_plan_intent, project_unfilled_native_attempts
        plan = await self.preview()
        intent = unfilled_plan_intent(plan['children'][0]['intent'])
        self.execution.create(replace(intent, quantity=intent.quantity + 1))
        with self.assertRaisesRegex(ValueError, 'content_or_prefix_conflict'):
            await self.preview()

    async def test_ack_loss_after_second_slot_preserves_native_history_and_ids(self):
        first = await self.preview()
        self.confirm_cancel(self.submit_child_fixture(first), filled=2)
        native = self.store.upsert_documents
        def lost_ack(collection, rows):
            native(collection, rows)
            if collection == 'execution_mock_automation_runner_current':
                raise OSError('controlled native plan ACK loss')
        with patch.object(self.store, 'upsert_documents', side_effect=lost_ack):
            with self.assertRaisesRegex(OSError, 'ACK loss'):
                await self.preview()
        saved = self.runner._unfilled_attempt_plan
        self.runner = MockAutomationRunner(self.store, self.bundle, self.spec)
        retry = await self.preview()
        self.assertEqual(saved['children'], retry['children'])
        self.assertEqual((1, 1), (retry['native_reprices_used'], retry['native_submission_attempts']))
        self.assertEqual(2, len(self.transport.calls))

    async def test_history_loss_and_corrupted_manifest_never_reset_budget(self):
        from kiwoom_monitor.application.order_lifecycle import project_unfilled_native_attempts
        first = await self.preview()
        self.confirm_cancel(self.submit_child_fixture(first), filled=2)
        observed = await self.preview()
        root = self.execution.load(self.intent.intent_id)
        with self.assertRaisesRegex(ValueError, 'history_lost'):
            project_unfilled_native_attempts(observed, root, [None, None], [[], []])
        row = self.store.load_document('execution_mock_automation_runner_current', ACCOUNT_REF, 'current')
        row['document']['unfilled_execution_preflight']['native_claimed_intent_ids'] = []
        self.store.upsert_documents('execution_mock_automation_runner_current', [row])
        with self.assertRaisesRegex(ValueError, 'integrity_mismatch'):
            MockAutomationRunner(self.store, self.bundle, self.spec)

    async def test_reserved_payload_price_and_deadline_are_not_renewed_with_new_cut(self):
        from dataclasses import replace
        first = await self.preview()
        arguments = await self.inputs()
        evidence = replace(arguments['assessment'].reprice_evidence, candidate_limit_price_won=1009)
        changed = self.runner.preview_unfilled_execution(**{**arguments, 'assessment': replace(
            arguments['assessment'], candidate_limit_price_won=1009, reprice_evidence=evidence)})
        self.assertEqual(first['children'], changed['children'])
        self.assertFalse(changed['pending_child_matches_current_cut'])
        self.clock += timedelta(seconds=6)
        expired = await self.preview()
        self.assertEqual(first['children'], expired['children'])
        self.assertFalse(expired['pending_child_matches_current_cut'])
        self.assertEqual(0, expired['native_reprices_used'])

    async def test_control_change_during_checkpoint_cannot_become_submission_approval(self):
        native = self.store.upsert_documents
        def stopped(collection, rows):
            native(collection, rows)
            if collection == 'execution_mock_automation_runner_current':
                emergency_stop_mock_automation(self.repository, self.runtime, account_ref=ACCOUNT_REF,
                                               spec_id=self.spec.spec_id, stopped_at=self.clock, reason='during ACK')
        arguments = await self.inputs()
        self.runner.preview_unfilled_management(**{k: v for k, v in arguments.items() if k not in {'recovery', 'risk'}})
        with patch.object(self.store, 'upsert_documents', side_effect=stopped):
            with self.assertRaisesRegex(ValueError, 'changed_during_checkpoint'):
                self.runner.preview_unfilled_execution(**arguments)
        self.runner = MockAutomationRunner(self.store, self.bundle, self.spec)
        with self.assertRaisesRegex(ValueError, 'control_changed'):
            await self.preview()
        self.assertEqual(1, len(self.transport.calls))

    async def test_slow_checkpoint_cannot_return_a_stale_native_cut(self):
        arguments = await self.inputs()
        self.runner.preview_unfilled_management(**{k: v for k, v in arguments.items() if k not in {'recovery', 'risk'}})
        native = self.store.upsert_documents
        def slow_ack(collection, rows):
            native(collection, rows)
            if collection == 'execution_mock_automation_runner_current':
                self.clock += timedelta(seconds=6)
        with patch.object(self.store, 'upsert_documents', side_effect=slow_ack):
            with self.assertRaisesRegex(ValueError, 'cut_expired_during_ack'):
                self.runner.preview_unfilled_execution(**arguments)
        self.assertEqual(1, len(self.transport.calls))

    async def test_missing_manifest_cannot_create_fresh_id_for_existing_native_attempt(self):
        first = await self.preview()
        self.confirm_cancel(self.submit_child_fixture(first), filled=2)
        self.runner._unfilled_attempt_plan = None
        with self.assertRaisesRegex(ValueError, 'without_preserved_manifest'):
            await self.preview()
        self.assertEqual(2, len(self.transport.calls))

    async def test_repeated_submission_start_proof_is_rejected_instead_of_counting_twice(self):
        from kiwoom_monitor.application.order_lifecycle import project_unfilled_native_attempts
        plan = await self.preview()
        child = self.submit_child_fixture(plan)
        events = list(self.execution.events(child.intent.intent_id))
        start = next(event for event in events if event['event_type'] == 'SUBMISSION_STARTED')
        with self.assertRaisesRegex(ValueError, 'submission_proof_invalid'):
            project_unfilled_native_attempts(plan, self.execution.load(self.intent.intent_id), [child], [events + [dict(start)]])

    async def test_stop_stale_risk_and_owner_loss_reject_guarded_preflight(self):
        first = await self.preview()
        arguments = await self.inputs()
        await self._refresh_recovery()
        with self.assertRaisesRegex(RuntimeError, 'risk snapshot changed'):
            self.runner.preview_unfilled_execution(**arguments)
        emergency_stop_mock_automation(self.repository, self.runtime, account_ref=ACCOUNT_REF,
                                       spec_id=self.spec.spec_id, stopped_at=self.clock, reason='test stop')
        with self.assertRaisesRegex(ValueError, 'control_changed'):
            await self.preview()
        arguments = await self.inputs()
        self.runtime.stop()
        with self.assertRaisesRegex(RuntimeError, 'acquired ownership'):
            self.runner.preview_unfilled_execution(**arguments)
        self.assertEqual(1, len(self.transport.calls))


class MockUnfilledReplacementDispatchTests(unittest.IsolatedAsyncioTestCase):
    from kiwoom_monitor.domain.execution_activation import MockUnfilledExecutionPolicy
    unfilled_execution_policy = MockUnfilledExecutionPolicy(3, 100, 300, 60, 5, 9, 1, 2, 10, True)
    _rank = MockAutomationWorkflowTests._rank
    _bar = MockAutomationWorkflowTests._bar
    _refresh_recovery = MockAutomationWorkflowTests._refresh_recovery
    approved_buy = MockUnfilledPurposeGateTests.approved_buy
    confirm_cancel = MockUnfilledNativeAttemptTests.confirm_cancel
    inputs = MockUnfilledNativeAttemptTests.inputs
    setup_sell = MockUnfilledNativeAttemptTests.setup_sell

    def setUp(self):
        MockAutomationWorkflowTests.setUp(self)

    def rebuild_native_policy(self):
        while self._cleanups:
            function, args, kwargs = self._cleanups.pop()
            function(*args, **kwargs)
        self.setUp()

    async def prepare(self, *, sell=None):
        await self.approved_buy()
        if sell is not None:
            self.setup_sell(sell)
        self.policy = replace(self.policy, max_snapshot_age_seconds=5)
        inputs = await self.inputs()
        self.runner.preview_unfilled_management(**{k: v for k, v in inputs.items() if k not in {'recovery', 'risk'}})
        return inputs

    async def test_native_approved_buy_submits_fixed_add_once_after_partial_cancel(self):
        inputs = await self.prepare()
        gate, record, receipt = self.runner.execute_unfilled_replacement(**inputs)
        self.assertEqual('APPROVED_FOR_SINGLE_SUBMISSION', gate.status.value, gate.reasons)
        self.assertEqual('ADD', gate.action)
        self.assertEqual('BUY_OPPORTUNITY', gate.replacement_binding['purpose'])
        self.assertEqual((7, 'ACCEPTED', 2), (record.intent.quantity, record.state.value, len(self.transport.calls)))
        self.assertEqual(record.intent.intent_id, receipt.intent_id)
        self.assertFalse(self.runner._unfilled_attempt_plan['order_authorized'])

    async def test_native_take_profit_submits_reduce_without_relabelling_exit(self):
        inputs = await self.prepare(sell=False)
        gate, record, _ = self.runner.execute_unfilled_replacement(**inputs)
        self.assertEqual('APPROVED_FOR_SINGLE_SUBMISSION', gate.status.value, gate.reasons)
        self.assertEqual(('REDUCE', 1, 'SELL'), (gate.action, record.intent.quantity, record.intent.side.value))

    async def test_native_protective_exit_without_ev_or_known_costs(self):
        inputs = await self.prepare(sell=True)
        self.costs = []
        inputs = await self.inputs()
        self.runner.request_protection_review(reason='hard_risk', input_ref='native-protection', requested_at=self.clock)
        gate, record, _ = self.runner.execute_unfilled_replacement(**inputs)
        self.assertEqual('APPROVED_FOR_SINGLE_SUBMISSION', gate.status.value, gate.reasons)
        self.assertEqual(('EXIT', 'PROTECTIVE_EXIT', 2), (gate.action, gate.replacement_binding['purpose'], record.intent.quantity))

    async def test_legacy_operating_spec_remains_off_despite_valid_preview(self):
        self.unfilled_execution_policy = None
        # Rebuild the native publication/admission; do not alter an admitted spec.
        self.rebuild_native_policy()
        inputs = await self.prepare()
        gate, record, receipt = self.runner.execute_unfilled_replacement(**inputs)
        self.assertIn('UNFILLED_EXECUTION_POLICY_OFF', gate.reasons)
        self.assertIsNone(record); self.assertIsNone(receipt)
        self.assertEqual(1, len(self.transport.calls))

    async def test_supplied_paired_summary_needs_explicit_mock_policy_permission(self):
        self.unfilled_execution_policy = replace(self.unfilled_execution_policy, allow_supplied_paired_mock_evidence=False)
        self.rebuild_native_policy()
        inputs = await self.prepare()
        gate, record, _ = self.runner.execute_unfilled_replacement(**inputs)
        self.assertIn('UNFILLED_PAIRED_EVIDENCE_NOT_ADMITTED_FOR_MOCK', gate.reasons)
        self.assertIsNone(record)

    async def test_policy_cannot_weaken_frozen_limits(self):
        inputs = await self.prepare()
        self.runner._unfilled_preflight = None
        self.policy = replace(self.policy, maximum_adverse_slippage_bps=101)
        inputs = await self.inputs()
        self.runner.preview_unfilled_management(**{k: v for k, v in inputs.items() if k not in {'recovery', 'risk'}})
        gate, record, _ = self.runner.execute_unfilled_replacement(**inputs)
        self.assertIn('UNFILLED_EXECUTION_FROZEN_LIMIT_EXCEEDED', gate.reasons)
        self.assertIsNone(record)

    async def test_unknown_repeat_and_restart_do_not_resubmit_or_create_new_child(self):
        inputs = await self.prepare()
        submit = self.transport.submit
        def unknown(intent):
            submit(intent); raise SubmissionUnknown('lost response')
        with patch.object(self.transport, 'submit', side_effect=unknown):
            gate, record, _ = self.runner.execute_unfilled_replacement(**inputs)
        self.assertEqual('SUBMISSION_UNKNOWN', record.state.value)
        for _ in range(3):
            self.assertEqual(record, self.runner.execute_unfilled_replacement(**inputs)[1])
        self.runner = MockAutomationRunner(self.store, self.bundle, self.spec)
        self.assertEqual(record, self.runner.execute_unfilled_replacement(**inputs)[1])
        self.assertEqual(2, len(self.transport.calls))
        self.assertEqual(1, len(self.runner._unfilled_attempt_plan['children']))

    async def test_queued_claim_ack_loss_is_consumed_without_network_retry(self):
        inputs = await self.prepare(); create = self.store.create_execution_intent
        def lost_ack(value, **kwargs):
            result = create(value, **kwargs)
            if value['intent_id'].startswith('unfilled-native-child:'):
                raise OSError('QUEUED commit ACK lost')
            return result
        with patch.object(self.store, 'create_execution_intent', side_effect=lost_ack):
            with self.assertRaisesRegex(OSError, 'ACK lost'):
                self.runner.execute_unfilled_replacement(**inputs)
        self.runner = MockAutomationRunner(self.store, self.bundle, self.spec)
        result = self.runner.execute_unfilled_replacement(**inputs)
        self.assertEqual('QUEUED', result[1].state.value)
        self.assertEqual(1, len(self.transport.calls))

    async def test_approved_gate_ack_loss_reuses_immutable_approval_before_first_claim(self):
        inputs = await self.prepare(); save = self.repository.save_mock_automation_decision_gate
        def lost_ack(gate):
            result = save(gate)
            if gate.version == 'mock_automation_replacement_gate/v3':
                raise OSError('approval ACK lost')
            return result
        with patch.object(self.runner._repository, 'save_mock_automation_decision_gate', side_effect=lost_ack):
            with self.assertRaisesRegex(OSError, 'ACK lost'):
                self.runner.execute_unfilled_replacement(**inputs)
        gate, record, _ = self.runner.execute_unfilled_replacement(**inputs)
        self.assertEqual('ACCEPTED', record.state.value)
        self.assertEqual(2, len(self.transport.calls))

    async def test_approval_ack_loss_renews_risk_gate_but_preserves_fixed_approval(self):
        inputs = await self.prepare(); save = self.repository.save_mock_automation_decision_gate
        def lost_ack(gate):
            save(gate); raise OSError('approval ACK lost')
        with patch.object(self.runner._repository, 'save_mock_automation_decision_gate', side_effect=lost_ack):
            with self.assertRaisesRegex(OSError, 'ACK lost'):
                self.runner.execute_unfilled_replacement(**inputs)
        slot = self.runner._unfilled_attempt_plan['children'][-1]['intent']
        original = self.repository.load_mock_automation_approved_gate(ACCOUNT_REF, slot['intent_id'])
        inputs = await self.inputs()
        gate, record, receipt = self.runner.execute_unfilled_replacement(**inputs)
        self.assertNotEqual(original.gate_id, gate.gate_id)
        self.assertEqual(inputs['risk'].snapshot_id, gate.risk_snapshot_id)
        self.assertEqual(original, self.repository.load_mock_automation_approved_gate(ACCOUNT_REF, slot['intent_id']))
        self.assertEqual(gate.gate_id, receipt.gate_id)
        again = self.runner.execute_unfilled_replacement(**inputs)
        self.assertEqual(gate, again[0])
        self.assertEqual(record, again[1])
        self.assertEqual(2, len(self.transport.calls))

    async def test_fresh_risk_gate_is_recovered_from_eventless_queued_claim_after_restart(self):
        inputs = await self.prepare(); save = self.repository.save_mock_automation_decision_gate
        def lost_approval(gate):
            save(gate); raise OSError('approval ACK lost')
        with patch.object(self.runner._repository, 'save_mock_automation_decision_gate', side_effect=lost_approval):
            with self.assertRaises(OSError):
                self.runner.execute_unfilled_replacement(**inputs)
        slot = self.runner._unfilled_attempt_plan['children'][-1]['intent']
        first = self.repository.load_mock_automation_approved_gate(ACCOUNT_REF, slot['intent_id'])
        inputs = await self.inputs(); create = self.store.create_execution_intent
        def lost_claim(value, **kwargs):
            result = create(value, **kwargs)
            if value['intent_id'].startswith('unfilled-native-child:'):
                raise OSError('QUEUED ACK lost')
            return result
        with patch.object(self.store, 'create_execution_intent', side_effect=lost_claim):
            with self.assertRaises(OSError):
                self.runner.execute_unfilled_replacement(**inputs)
        self.runner = MockAutomationRunner(self.store, self.bundle, self.spec)
        gate, record, receipt = self.runner.execute_unfilled_replacement(**inputs)
        self.assertNotEqual(first.gate_id, gate.gate_id)
        self.assertEqual((inputs['risk'].snapshot_id, 'QUEUED', gate.gate_id),
                         (gate.risk_snapshot_id, record.state.value, receipt.gate_id))
        self.assertEqual((), self.execution.events(record.intent.intent_id))
        self.assertEqual(1, len(self.transport.calls))

    async def test_claim_reference_survives_submit_fill_cancel_and_newer_reconciliation(self):
        inputs = await self.prepare(); gate, child, _ = self.runner.execute_unfilled_replacement(**inputs)
        expected = {'gate_id': gate.gate_id, 'risk_snapshot_id': gate.risk_snapshot_id,
                    'risk_reconciliation_revision': gate.risk_reconciliation_revision}
        self.assertEqual(expected, self.execution.claim_approval(child.intent.intent_id))

        self.confirm_cancel(child, filled=2)
        self.assertEqual(expected, self.execution.claim_approval(child.intent.intent_id))
        self.clock += timedelta(seconds=1)
        self.runtime.reconcile(child.intent.intent_id, replace(self.snapshot, as_of=self.clock))
        self.assertEqual(expected, self.execution.claim_approval(child.intent.intent_id))

    async def test_managed_child_cannot_become_new_root_to_reset_retry_budget(self):
        inputs = await self.prepare(sell=True)
        _, child, _ = self.runner.execute_unfilled_replacement(**inputs)
        self.confirm_cancel(child)
        self.policy = replace(self.policy, anchor_intent_id=child.intent.intent_id,
                              anchor_price_won=child.intent.limit_price, anchor_available_at=child.intent.created_at)
        self.runner._unfilled_preflight = None; self.runner._unfilled_attempt_plan = None
        inputs = await self.inputs()
        self.runner.preview_unfilled_management(**{k: v for k, v in inputs.items() if k not in {'recovery', 'risk'}})
        with self.assertRaisesRegex(ValueError, 'cannot_reset_original_anchor'):
            self.runner.execute_unfilled_replacement(**inputs)
        self.assertEqual(3, len(self.transport.calls))

    async def test_parent_cancel_race_partial_fills_recompute_next_fixed_child(self):
        inputs = await self.prepare()
        _, first, _ = self.runner.execute_unfilled_replacement(**inputs)
        self.confirm_cancel(first, filled=2)
        self.costs = [replace(self.costs[0], gross_amount=sum(row.gross_amount for row in self.costs),
                             settlement_amount=sum(row.settlement_amount for row in self.costs))]
        inputs = await self.inputs()
        gate, second, _ = self.runner.execute_unfilled_replacement(**inputs)
        self.assertIsNotNone(second, gate.reasons)
        self.assertEqual(5, second.intent.quantity)
        self.assertEqual(first.intent.intent_id, gate.replacement_binding['parent_intent_id'])
        self.assertEqual((3, 2), (len(self.transport.calls), gate.replacement_binding['attempt']))

    async def test_native_queued_transaction_rejects_changed_risk(self):
        inputs = await self.prepare(); create = self.store.create_execution_intent
        def changed(value, **kwargs):
            if value['intent_id'].startswith('unfilled-native-child:'):
                self.store.upsert_documents('execution_mock_automation_current_risk', [{
                    'owner': ACCOUNT_REF, 'key': ACCOUNT_REF,
                    'document': {**inputs['risk'].to_dict(), 'snapshot_id': 'replaced-native-cut'},
                }])
            return create(value, **kwargs)
        with patch.object(self.store, 'create_execution_intent', side_effect=changed):
            with self.assertRaisesRegex(RuntimeError, 'MOCK_REPLACEMENT_RISK_CHANGED'):
                self.runner.execute_unfilled_replacement(**inputs)
        self.assertEqual(1, len(self.transport.calls))

    async def test_native_queued_transaction_rejects_foreign_run_open_order(self):
        inputs = await self.prepare(); create = self.store.create_execution_intent
        def changed(value, **kwargs):
            if value['intent_id'].startswith('unfilled-native-child:'):
                other = ExecutionRepository(self.store)
                other.create(replace(self.intent, intent_id='other-run-pending', run_id='older-run'))
            return create(value, **kwargs)
        with patch.object(self.store, 'create_execution_intent', side_effect=changed):
            with self.assertRaisesRegex(RuntimeError, 'MOCK_REPLACEMENT_ACCOUNT_ORDER_IN_FLIGHT'):
                self.runner.execute_unfilled_replacement(**inputs)
        self.assertEqual(1, len(self.transport.calls))

    async def test_newer_terminal_rest_confirmation_does_not_invalidate_existing_child(self):
        inputs = await self.prepare()
        _, child, _ = self.runner.execute_unfilled_replacement(**inputs)
        self.clock += timedelta(seconds=1)
        self.runtime.reconcile(self.record.intent.intent_id, replace(self.snapshot, as_of=self.clock))
        self.assertEqual(child, self.runner.execute_unfilled_replacement(**inputs)[1])
        self.assertEqual(2, len(self.transport.calls))

    async def test_native_queued_transaction_rejects_changed_fixed_payload(self):
        inputs = await self.prepare(); create = self.store.create_execution_intent
        def changed(value, **kwargs):
            if value['intent_id'].startswith('unfilled-native-child:'):
                value = {**value, 'quantity': value['quantity'] + 1}
            return create(value, **kwargs)
        with patch.object(self.store, 'create_execution_intent', side_effect=changed):
            with self.assertRaisesRegex(RuntimeError, 'MOCK_REPLACEMENT_FIXED_CHILD_CHANGED'):
                self.runner.execute_unfilled_replacement(**inputs)
        self.assertEqual(1, len(self.transport.calls))

    async def test_native_queued_transaction_rejects_parent_no_longer_cancelled(self):
        from kiwoom_monitor.domain.order_contract import order_event
        inputs = await self.prepare(); create = self.store.create_execution_intent
        def changed(value, **kwargs):
            if value['intent_id'].startswith('unfilled-native-child:'):
                other = ExecutionRepository(self.store)
                other.apply(other.load(self.intent.intent_id), order_event(
                    self.intent.intent_id, 'EXTERNAL_CHANGED', OrderState.CANCEL_PENDING, self.clock, self.clock))
            return create(value, **kwargs)
        with patch.object(self.store, 'create_execution_intent', side_effect=changed):
            with self.assertRaisesRegex(RuntimeError, 'MOCK_REPLACEMENT_PARENT_CANCEL_OR_FILLS_CHANGED'):
                self.runner.execute_unfilled_replacement(**inputs)
        self.assertEqual(1, len(self.transport.calls))

    async def test_stop_during_native_queued_claim_blocks_submission(self):
        inputs = await self.prepare(); create = self.store.create_execution_intent
        def stopped(value, **kwargs):
            if value['intent_id'].startswith('unfilled-native-child:'):
                emergency_stop_mock_automation(self.repository, self.runtime, account_ref=ACCOUNT_REF,
                    spec_id=self.spec.spec_id, stopped_at=self.clock, reason='stop before claim')
            return create(value, **kwargs)
        with patch.object(self.store, 'create_execution_intent', side_effect=stopped):
            with self.assertRaisesRegex(RuntimeError, 'MOCK_AUTOMATION_CONTROL_CHANGED'):
                self.runner.execute_unfilled_replacement(**inputs)
        self.assertEqual(1, len(self.transport.calls))

    async def test_protection_cue_during_approval_blocks_opportunity_but_keeps_child(self):
        inputs = await self.prepare(); save = self.repository.save_mock_automation_decision_gate
        def armed(gate):
            result = save(gate)
            self.runner.request_protection_review(reason='new_risk', input_ref='native-risk-next', requested_at=self.clock)
            return result
        with patch.object(self.runner._repository, 'save_mock_automation_decision_gate', side_effect=armed):
            with self.assertRaisesRegex(ValueError, 'changed_or_expired'):
                self.runner.execute_unfilled_replacement(**inputs)
        self.assertEqual(1, len(self.transport.calls))
        self.assertTrue(self.runner.status['protection_review_required'])

    async def test_approval_ack_delay_cannot_renew_fixed_child_expiry(self):
        inputs = await self.prepare(); save = self.repository.save_mock_automation_decision_gate
        def delayed(gate):
            result = save(gate); self.clock += timedelta(seconds=6); return result
        with patch.object(self.runner._repository, 'save_mock_automation_decision_gate', side_effect=delayed):
            with self.assertRaisesRegex(ValueError, 'changed_or_expired'):
                self.runner.execute_unfilled_replacement(**inputs)
        self.assertEqual(1, len(self.transport.calls))

    async def test_dispatch_receipt_ack_loss_never_repeats_native_submit(self):
        inputs = await self.prepare(); save = self.repository.save_mock_automation_dispatch_receipt
        def lost_ack(receipt):
            save(receipt); raise OSError('dispatch ACK lost')
        with patch.object(self.runner._repository, 'save_mock_automation_dispatch_receipt', side_effect=lost_ack):
            with self.assertRaisesRegex(OSError, 'ACK lost'):
                self.runner.execute_unfilled_replacement(**inputs)
        self.runner = MockAutomationRunner(self.store, self.bundle, self.spec)
        result = self.runner.execute_unfilled_replacement(**inputs)
        self.assertEqual('ACCEPTED', result[1].state.value)
        self.assertEqual(2, len(self.transport.calls))

    async def test_gate_cannot_relabel_add_as_legacy_enter_or_mutate_fixed_price(self):
        from kiwoom_monitor.application.mock_automation_execution import mock_automation_decision_gate_from_dict
        inputs = await self.prepare(); gate, _, _ = self.runner.execute_unfilled_replacement(**inputs)
        for field, value in (('action', 'ENTER'), ('limit_price', gate.limit_price + 1), ('replacement_binding', None)):
            with self.subTest(field=field), self.assertRaises((ValueError, TypeError)):
                mock_automation_decision_gate_from_dict({**gate.to_dict(), field: value})

    async def test_frozen_policy_changes_spec_identity_and_legacy_hash_is_preserved(self):
        from kiwoom_monitor.domain.execution_activation import mock_automation_spec_from_dict
        legacy = replace(self.spec, unfilled_execution_policy=None)
        self.assertNotIn('unfilled_execution_policy', legacy.to_dict())
        self.assertEqual(legacy, mock_automation_spec_from_dict(legacy.to_dict()))
        self.assertNotEqual(legacy.spec_id, self.spec.spec_id)
        self.assertEqual(self.spec, mock_automation_spec_from_dict(self.spec.to_dict()))
        with self.assertRaisesRegex(ValueError, 'freshness exceeds'):
            replace(self.spec, unfilled_execution_policy=replace(self.unfilled_execution_policy, max_snapshot_age_seconds=6))

    async def test_existing_parent_history_is_required_when_confirmation_has_advanced(self):
        from kiwoom_monitor.application.order_lifecycle import project_unfilled_native_attempts
        inputs = await self.prepare(); _, child, _ = self.runner.execute_unfilled_replacement(**inputs)
        self.clock += timedelta(seconds=1)
        root = self.runtime.reconcile(self.record.intent.intent_id, replace(self.snapshot, as_of=self.clock))
        with self.assertRaisesRegex(ValueError, 'parent_not_safely_cancelled'):
            project_unfilled_native_attempts(self.runner._unfilled_attempt_plan, root, [child],
                                            [self.execution.events(child.intent.intent_id)])

    async def test_postgres_claim_placeholder_and_row_locks_use_same_native_proofs(self):
        from kiwoom_monitor.central_server.database_execution import _require_execution_ownership
        from kiwoom_monitor.infrastructure.persistence.execution_repository import _record_document
        inputs = await self.prepare(); gate, child, _ = self.runner.execute_unfilled_replacement(**inputs)
        ownership = {**self.execution._ownership, 'active_spec_id': self.spec.spec_id, 'control_revision': 1,
                     'replacement_claim': {'gate_id': gate.gate_id, 'risk_snapshot_id': inputs['risk'].snapshot_id,
                                          'risk_reconciliation_revision': inputs['risk'].reconciliation_revision}}
        statements = []
        with self.store._connection() as connection:
            native = connection.cursor()
            class Cursor:
                def execute(_self, sql, params):
                    statements.append(sql); native.execute(sql.replace('%s', '?').replace(' FOR UPDATE', ''), params)
                def fetchone(_self): return native.fetchone()
            _require_execution_ownership(Cursor(), ownership, _record_document(child), '%s')
        self.assertGreaterEqual(sum(sql.endswith(' FOR UPDATE') for sql in statements), 7)
        self.assertTrue(any('execution_mock_automation' not in sql and 'central_documents' in sql for sql in statements))

    async def test_runner_close_waits_for_native_submit_and_checkpoint(self):
        import asyncio, threading
        inputs = await self.prepare(); entered = threading.Event(); release = threading.Event()
        submit = self.transport.submit
        def held(intent):
            entered.set(); release.wait(5); return submit(intent)
        with patch.object(self.transport, 'submit', side_effect=held):
            command = asyncio.create_task(asyncio.to_thread(self.runner.execute_unfilled_replacement, **inputs))
            self.assertTrue(await asyncio.to_thread(entered.wait, 3))
            closing = asyncio.create_task(self.runner.close()); await asyncio.sleep(0.02)
            self.assertFalse(closing.done())
            release.set(); result = await command; await closing
        self.assertEqual('ACCEPTED', result[1].state.value)
        self.assertFalse(self.runtime._new_orders_enabled)
        self.assertEqual(result[1].intent.intent_id, self.runner.status['pending_intent_id'])

    async def test_close_during_approval_drains_without_creating_claim(self):
        import asyncio, threading
        inputs = await self.prepare(); entered = threading.Event(); release = threading.Event()
        save = self.repository.save_mock_automation_decision_gate
        def held(gate):
            result = save(gate); entered.set(); release.wait(5); return result
        with patch.object(self.runner._repository, 'save_mock_automation_decision_gate', side_effect=held):
            command = asyncio.create_task(asyncio.to_thread(self.runner.execute_unfilled_replacement, **inputs))
            self.assertTrue(await asyncio.to_thread(entered.wait, 3))
            closing = asyncio.create_task(self.runner.close()); await asyncio.sleep(0.02)
            self.assertFalse(closing.done())
            release.set()
            with self.assertRaisesRegex(RuntimeError, 'closed_before_claim'):
                await command
            await closing
        self.assertEqual(1, len(self.transport.calls))


if __name__ == "__main__":
    unittest.main()


class MockUnfilledRunningDispatchTests(unittest.IsolatedAsyncioTestCase):
    unfilled_execution_policy = MockUnfilledReplacementDispatchTests.unfilled_execution_policy
    setUp = MockUnfilledReplacementDispatchTests.setUp
    _rank = MockAutomationWorkflowTests._rank
    _bar = MockAutomationWorkflowTests._bar
    _refresh_recovery = MockAutomationWorkflowTests._refresh_recovery
    approved_buy = MockUnfilledPurposeGateTests.approved_buy
    confirm_cancel = MockUnfilledNativeAttemptTests.confirm_cancel
    inputs = MockUnfilledNativeAttemptTests.inputs
    setup_sell = MockUnfilledNativeAttemptTests.setup_sell
    prepare = MockUnfilledReplacementDispatchTests.prepare
    rebuild_native_policy = MockUnfilledReplacementDispatchTests.rebuild_native_policy
    count_native = MockUnfilledOffIntegrationTests.count_native

    async def test_running_owner_wakes_without_bar_and_public_dispatch_stays_idle_only(self):
        import asyncio
        inputs = await self.prepare()
        self.runner._poll_seconds = 60
        idle, submitted = asyncio.Event(), asyncio.Event()
        native = self.runner.run_once
        async def iteration():
            value = await native()
            (submitted if len(self.transport.calls) == 2 else idle).set()
            return value
        with patch.object(self.runner, 'run_once', side_effect=iteration):
            await self.runner.start()
            try:
                await asyncio.wait_for(idle.wait(), 3)
                with self.assertRaisesRegex(RuntimeError, 'idle_acknowledged'):
                    self.runner.execute_unfilled_replacement(**inputs)
                self.assertTrue(self.runner.offer_unfilled_execution_inputs(**inputs))
                await asyncio.wait_for(submitted.wait(), 5)
                self.assertEqual('PROCESSED', self.runner.status['unfilled_input_delivery']['state'])
                self.assertEqual(7, self.transport.calls[-1][-1])
            finally:
                await self.runner.close()
        self.assertEqual(2, len(self.transport.calls))

    async def test_one_cut_creates_anchor_and_submits_without_idle_preview_api(self):
        inputs = await self.prepare()
        self.runner._unfilled_preflight = None
        self.runner.offer_unfilled_execution_inputs(**inputs)
        with patch.object(self.runner, 'preview_unfilled_management', side_effect=AssertionError('public idle preview')), patch.object(
                self.runner, 'execute_unfilled_replacement', side_effect=AssertionError('public idle dispatcher')):
            await self.runner.run_once()
        self.assertEqual(2, len(self.transport.calls))
        self.assertEqual('ADD', next(gate.action for gate in self.repository.load_mock_automation_decision_gates(ACCOUNT_REF) if gate.replacement_binding is not None))

    async def test_take_profit_running_owner_submits_reduce(self):
        inputs = await self.prepare(sell=False)
        self.runner.offer_unfilled_execution_inputs(**inputs)
        await self.runner.run_once()
        self.assertEqual('REDUCE', next(gate.action for gate in self.repository.load_mock_automation_decision_gates(ACCOUNT_REF) if gate.replacement_binding is not None))
        self.assertEqual('PROCESSED', self.runner.status['unfilled_input_delivery']['state'])

    async def test_protective_cut_precedes_pending_review_without_ev(self):
        inputs = await self.prepare(sell=True)
        self.costs = []
        inputs = await self.inputs()
        self.runner.request_protection_review(reason='hard_risk', input_ref='risk', requested_at=self.clock)
        self.runner.offer_unfilled_execution_inputs(**inputs)
        with patch.object(self.store, 'load_observation_revisions_after', side_effect=AssertionError('waited for minute')):
            await self.runner.run_once()
        self.assertEqual('EXIT', next(gate.action for gate in self.repository.load_mock_automation_decision_gates(ACCOUNT_REF) if gate.replacement_binding is not None))
        self.assertEqual('PROCESSED', self.runner.status['unfilled_input_delivery']['state'])
        self.assertTrue(self.runner._protection_pending())  # Submission is not actual liquidation.

    async def test_duplicate_notifications_and_idle_polls_do_not_repeat_full_gate(self):
        inputs = await self.prepare()
        with self.count_native() as offer:
            self.assertTrue(self.runner.offer_unfilled_execution_inputs(**inputs))
            for _ in range(100):
                self.assertFalse(self.runner.offer_unfilled_execution_inputs(**inputs))
        self.assertEqual({'connections': 0, 'selects': 0, 'commits': 0}, offer)
        await self.runner.run_once()
        with patch.object(self.runner, '_execute_unfilled_replacement_locked', side_effect=AssertionError('repeat gate')):
            with self.count_native() as idle:
                for _ in range(10):
                    await self.runner.run_once()
        self.assertEqual(0, idle['commits'])
        self.assertEqual(2, len(self.transport.calls))
        self.load_counts = {'offer_101': offer, 'idle_10': idle}

    async def test_newer_cut_coalesces_and_late_same_cut_cannot_rearm(self):
        old = await self.prepare()
        self.runner.offer_unfilled_execution_inputs(**old)
        new = await self.inputs()
        self.runner.offer_unfilled_execution_inputs(**new)
        with self.assertRaisesRegex(ValueError, 'out_of_order'):
            self.runner.offer_unfilled_execution_inputs(**old)
        await self.runner.run_once()
        self.assertEqual(new['risk'].reconciliation_revision, self.runner.status['unfilled_input_delivery']['risk_revision'])
        self.assertFalse(self.runner.offer_unfilled_execution_inputs(**new))
        self.assertEqual(2, len(self.transport.calls))

    async def test_mutated_cut_with_same_time_revision_is_rejected_and_payload_is_copied(self):
        inputs = await self.prepare()
        self.runner.offer_unfilled_execution_inputs(**inputs)
        altered = {**inputs, 'assessment': replace(inputs['assessment'], candidate_limit_price_won=1002)}
        with self.assertRaisesRegex(ValueError, 'conflicting_cut'):
            self.runner.offer_unfilled_execution_inputs(**altered)
        inputs['observation'].account.positions['005930'] = 0
        await self.runner.run_once()
        self.assertEqual('PROCESSED', self.runner.status['unfilled_input_delivery']['state'])

    async def test_input_expiry_blocks_once_without_gate_or_order(self):
        inputs = await self.prepare()
        self.runner.offer_unfilled_execution_inputs(**inputs)
        self.clock += timedelta(seconds=6)
        with patch.object(self.runner, '_execute_unfilled_replacement_locked', side_effect=AssertionError('expired gate')):
            await self.runner.run_once()
            await self.runner.run_once()
        self.assertEqual('BLOCKED', self.runner.status['unfilled_input_delivery']['state'])
        self.assertIn('expired', self.runner.status['unfilled_input_delivery']['reason'])
        self.assertEqual(1, len(self.transport.calls))

    async def test_due_without_cut_has_zero_additional_io_and_keeps_session_clock(self):
        await self.prepare()
        self.clock = self.policy.wait_until
        with self.count_native() as due:
            for _ in range(100):
                self.assertFalse(await self.runner._unfilled_call())
        self.assertEqual({'connections': 0, 'selects': 0, 'commits': 0}, due)
        self.assertEqual('CURRENT_INPUT_REQUIRED', self.runner.status['unfilled_input_delivery']['state'])
        clock = AsyncMock(return_value=False)
        with patch.object(self.runner, '_clock_call', clock):
            await self.runner.run_once()
        clock.assert_awaited_once()
        self.due_counts = due

    async def test_protection_abandons_confirmed_buy_before_replacement_gate(self):
        inputs = await self.prepare()
        self.runner.request_protection_review(reason='hard_risk', input_ref='risk', requested_at=self.clock)
        self.runner.offer_unfilled_execution_inputs(**inputs)
        with patch.object(self.runner, '_execute_unfilled_replacement_locked', side_effect=AssertionError('opportunity gate')):
            await self.runner.run_once()
        self.assertEqual('ABANDONED', self.runner.status['unfilled_input_delivery']['state'])
        self.assertEqual(1, len(self.transport.calls))

    async def test_stop_and_restart_do_not_replay_ram_cut(self):
        inputs = await self.prepare()
        self.runner.offer_unfilled_execution_inputs(**inputs)
        emergency_stop_mock_automation(self.repository, self.runtime, account_ref=ACCOUNT_REF,
            spec_id=self.spec.spec_id, stopped_at=self.clock, reason='test stop')
        await self.runner.run_once()
        self.assertEqual(1, len(self.transport.calls))
        await self.runner.close()
        with self.assertRaisesRegex(RuntimeError, 'CLOSED'):
            self.runner.offer_unfilled_execution_inputs(**inputs)
        restored = MockAutomationRunner(self.store, self.bundle, self.spec)
        self.assertIsNone(restored._unfilled_input)
        await restored.close()

    async def test_legacy_spec_cannot_offer_executable_inputs(self):
        self.unfilled_execution_policy = None
        self.rebuild_native_policy()
        inputs = await self.prepare()
        with self.assertRaisesRegex(ValueError, 'policy_off'):
            self.runner.offer_unfilled_execution_inputs(**inputs)
        self.assertEqual(1, len(self.transport.calls))

    async def test_unknown_on_new_cut_does_not_submit_again(self):
        inputs = await self.prepare()
        submit = self.transport.submit
        def unknown(intent):
            submit(intent); raise SubmissionUnknown('lost response')
        self.runner.offer_unfilled_execution_inputs(**inputs)
        with patch.object(self.transport, 'submit', side_effect=unknown):
            await self.runner.run_once()
        inputs = await self.inputs()
        self.runner.offer_unfilled_execution_inputs(**inputs)
        await self.runner.run_once()
        self.assertEqual(2, len(self.transport.calls))
        self.assertEqual(1, len(self.runner._unfilled_attempt_plan['children']))

    async def test_stale_native_risk_is_not_authorized_by_current_ram_offer(self):
        inputs = await self.prepare()
        self.runner.offer_unfilled_execution_inputs(**inputs)
        await self._refresh_recovery()
        with self.assertRaisesRegex(RuntimeError, 'risk snapshot changed'):
            await self.runner.run_once()
        self.assertEqual('ERROR', self.runner.status['unfilled_input_delivery']['state'])
        self.assertEqual(1, len(self.transport.calls))

    async def test_failed_checkpoint_keeps_one_pending_cut_until_ack(self):
        inputs = await self.prepare()
        self.runner.offer_unfilled_execution_inputs(**inputs)
        self.runner._checkpoint_pending = True
        with patch.object(self.runner, '_save_checkpoint', side_effect=OSError('lost ACK')):
            with self.assertRaises(OSError):
                await self.runner.run_once()
        self.assertTrue(self.runner.status['unfilled_input_delivery']['pending'])
        self.assertEqual(1, len(self.transport.calls))
        await self.runner.run_once()
        self.assertFalse(self.runner.status['unfilled_input_delivery']['pending'])
        self.assertEqual(2, len(self.transport.calls))

    async def test_wrong_scope_and_missing_confirmation_are_rejected_in_ram(self):
        inputs = await self.prepare()
        with self.count_native() as invalid:
            with self.assertRaisesRegex(ValueError, 'confirmation_missing'):
                self.runner.offer_unfilled_execution_inputs(**{**inputs, 'broker_snapshot': None})
            with self.assertRaisesRegex(ValueError, 'scope_or_time'):
                self.runner.offer_unfilled_execution_inputs(**{**inputs, 'target': replace(inputs['target'], account_ref='wrong')})
        self.assertEqual({'connections': 0, 'selects': 0, 'commits': 0}, invalid)
        self.assertIsNone(self.runner._unfilled_input)

    async def test_close_joins_running_submission_after_native_claim(self):
        import asyncio, threading
        inputs = await self.prepare()
        entered, release = threading.Event(), threading.Event()
        submit = self.transport.submit
        def held(intent):
            entered.set(); release.wait(5)
            return submit(intent)
        self.runner.offer_unfilled_execution_inputs(**inputs)
        with patch.object(self.transport, 'submit', side_effect=held):
            await self.runner.start()
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                closing = asyncio.create_task(self.runner.close())
                await asyncio.sleep(0.05)
                self.assertFalse(closing.done())
            finally:
                release.set()
            await asyncio.wait_for(closing, 5)
        self.assertEqual(2, len(self.transport.calls))
        child = self.runner._unfilled_attempt_plan['children'][0]['intent']['intent_id']
        self.assertIsNotNone(self.repository.load_mock_automation_dispatch_receipt(ACCOUNT_REF, child))

    async def test_close_during_preview_ack_never_claims_native_child(self):
        import asyncio, threading
        inputs = await self.prepare()
        entered, release = threading.Event(), threading.Event()
        save = self.store.upsert_documents
        def held(collection, rows):
            if collection == 'execution_mock_automation_runner_current' and not self.runner._closing:
                entered.set(); release.wait(5)
            return save(collection, rows)
        self.runner.offer_unfilled_execution_inputs(**inputs)
        with patch.object(self.store, 'upsert_documents', side_effect=held):
            await self.runner.start()
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                closing = asyncio.create_task(self.runner.close())
                await asyncio.sleep(0.05)
                self.assertFalse(closing.done())
            finally:
                release.set()
            await asyncio.wait_for(closing, 5)
        self.assertEqual(1, len(self.transport.calls))
        child = self.runner._unfilled_attempt_plan['children'][0]['intent']['intent_id']
        self.assertIsNone(self.execution.load(child))

    async def test_healthy_rest_revision_and_quote_refresh_only_renew_ram_until_event(self):
        inputs = await self.prepare()
        self.runner.offer_unfilled_execution_inputs(**inputs)
        await self.runner.run_once()
        with patch.object(self.runner, '_execute_unfilled_replacement_locked', side_effect=AssertionError('refresh repeated full gate')):
            for _ in range(6):
                self.clock += timedelta(milliseconds=100)
                inputs = await self.inputs()
                with self.count_native() as renewed:
                    self.runner.offer_unfilled_execution_inputs(**inputs)
                    self.assertFalse(await self.runner._unfilled_call())
                self.assertEqual({'connections': 0, 'selects': 0, 'commits': 0}, renewed)
        self.assertEqual(2, len(self.transport.calls))
        self.assertTrue(self.runner.status['unfilled_input_delivery']['pending'])

    async def test_due_with_fresh_cut_is_consumed_once_not_every_rest_refresh(self):
        inputs = await self.prepare()
        self.runner.offer_unfilled_execution_inputs(**inputs)
        await self.runner.run_once()
        self.clock = self.policy.wait_until
        inputs = await self.inputs()
        self.runner.offer_unfilled_execution_inputs(**inputs)
        native = self.runner._execute_unfilled_replacement_locked
        with patch.object(self.runner, '_execute_unfilled_replacement_locked', wraps=native) as dispatch:
            await self.runner.run_once()
            self.assertEqual(1, dispatch.call_count)
            inputs = await self.inputs()
            self.runner.offer_unfilled_execution_inputs(**inputs)
            self.assertFalse(await self.runner._unfilled_call())
            self.assertEqual(1, dispatch.call_count)
        self.assertEqual(self.policy.wait_until.isoformat(), self.runner._unfilled_processed_deadline)
        self.assertEqual(2, len(self.transport.calls))

    async def test_protective_deadline_without_input_arms_one_review_without_full_gate(self):
        await self.prepare(sell=True)
        self.clock = self.policy.protection_deadline
        with self.count_native() as due:
            for _ in range(100):
                self.assertFalse(await self.runner._unfilled_call())
        self.assertEqual({'connections': 0, 'selects': 0, 'commits': 0}, due)
        self.assertEqual(1, self.runner.status['protection_review']['revision'])
        with patch.object(self.runner, '_execute_unfilled_replacement_locked', side_effect=AssertionError('missing-input gate')):
            with self.count_native() as first:
                await self.runner.run_once()
            with self.count_native() as repeated:
                for _ in range(10):
                    await self.runner.run_once()
        self.assertEqual(1, first['commits'])
        self.assertEqual(0, repeated['commits'])
        self.protection_deadline_counts = {'ram_due_100': due, 'first_poll': first, 'ten_polls': repeated}


class MockUnfilledRunningCancelTests(unittest.IsolatedAsyncioTestCase):
    unfilled_execution_policy = MockUnfilledReplacementDispatchTests.unfilled_execution_policy
    setUp = MockUnfilledReplacementDispatchTests.setUp
    _rank = MockAutomationWorkflowTests._rank
    _bar = MockAutomationWorkflowTests._bar
    _refresh_recovery = MockAutomationWorkflowTests._refresh_recovery
    approved_buy = MockUnfilledPurposeGateTests.approved_buy
    inputs = MockUnfilledNativeAttemptTests.inputs
    setup_sell = MockUnfilledNativeAttemptTests.setup_sell
    count_native = MockUnfilledOffIntegrationTests.count_native
    rebuild_native_policy = MockUnfilledReplacementDispatchTests.rebuild_native_policy

    def confirm_cancel(self, record, filled=0):
        if record.state is OrderState.CANCEL_PENDING:
            with patch.object(self.runtime, 'cancel', return_value=record):
                return MockUnfilledNativeAttemptTests.confirm_cancel(self, record, filled)
        return MockUnfilledNativeAttemptTests.confirm_cancel(self, record, filled)

    def retain_active(self, record, filled=0):
        self.record = record
        self.snapshot = BrokerOrderSnapshot(record.broker_order_id, ACCOUNT_REF, '005930',
            OrderState.ACCEPTED, 0, record.intent.quantity, self.clock, ())
        self.record = self.runtime.reconcile(record.intent.intent_id, self.snapshot)

    async def prepare_active(self, sell=None):
        from kiwoom_monitor.central_server.execution_runtime import ManualMockOrderGateway
        if sell is None:
            with patch.object(MockUnfilledNativeAttemptTests, 'confirm_cancel', side_effect=lambda _case, record, **_kw: self.retain_active(record)):
                await self.approved_buy()
        else:
            await self.approved_buy()
            with patch.object(self, 'confirm_cancel', side_effect=self.retain_active):
                self.setup_sell(sell)
        self.policy = replace(self.policy, max_snapshot_age_seconds=5)
        self.cancel_calls = 0
        self.refreshes = 0
        self.refresh_hook = None
        def cancel(*_args):
            self.cancel_calls += 1
        self.transport.cancel = cancel
        async def refresh():
            self.refreshes += 1
            if self.refresh_hook:
                self.refresh_hook()
            self.clock += timedelta(milliseconds=50)
            current = self.runtime.load_intent(self.record.intent.intent_id)
            self.snapshot = replace(self.snapshot, as_of=self.clock)
            self.record = self.runtime.reconcile(current.intent.intent_id, self.snapshot)
            latest = await self.inputs()
            self.bundle.monitor.latest_position_observation = latest['observation']
            self.bundle.monitor.latest_automation_risk_cut = {'risk': latest['risk'], 'account': latest['recovery'].account}
            return latest['recovery'].account
        self.bundle.gateway = ManualMockOrderGateway(self.runtime, self.execution, refresh, now_provider=lambda: self.clock)
        self.addAsyncCleanup(self.bundle.gateway.close)
        inputs = await self.inputs()
        if sell is False:
            inputs['assessment'] = replace(inputs['assessment'], reprice_evidence=replace(
                inputs['assessment'].reprice_evidence, pending_quantity=self.record.intent.quantity))
        return inputs

    async def consume(self, inputs):
        self.runner.offer_unfilled_execution_inputs(**inputs)
        await self.runner.run_once()
        return self.runner.status['unfilled_input_delivery']

    async def test_buy_reprice_cancels_once_waits_confirmation_then_submits_residual(self):
        inputs = await self.prepare_active()
        self.assertEqual('CANCEL_REQUESTED', (await self.consume(inputs))['state'])
        self.assertEqual((1, 1, 1), (self.cancel_calls, self.refreshes, len(self.transport.calls)))
        self.assertEqual(OrderState.CANCEL_PENDING, self.execution.load(self.intent.intent_id).state)
        self.assertEqual('REPRICE', self.runner.status['after_cancel'])
        self.snapshot = replace(self.snapshot, state=OrderState.CANCEL_PENDING)
        self.record = self.execution.load(self.intent.intent_id)
        inputs = await self.inputs()
        self.assertEqual('BLOCKED', (await self.consume(inputs))['state'])
        self.assertEqual(1, self.cancel_calls)
        self.confirm_cancel(self.record, filled=2)
        inputs = await self.inputs()
        self.assertEqual('PROCESSED', (await self.consume(inputs))['state'])
        self.assertEqual(8, self.transport.calls[-1][-1])
        self.assertEqual((1, 2), (self.cancel_calls, len(self.transport.calls)))

    async def test_profit_reprice_cancels_without_relabelling_exit(self):
        inputs = await self.prepare_active(False)
        delivery = await self.consume(inputs)
        self.assertEqual('CANCEL_REQUESTED', delivery['state'], delivery)
        self.assertEqual('TAKE_PROFIT', self.runner._unfilled_attempt_plan['result']['purpose'])
        self.assertEqual((1, 2), (self.cancel_calls, len(self.transport.calls)))

    async def test_protective_cancel_without_ev_then_exit_after_confirmed_cancel(self):
        inputs = await self.prepare_active(True)
        self.runner.request_protection_review(reason='hard_risk', input_ref='risk', requested_at=self.clock)
        delivery = await self.consume(inputs)
        self.assertEqual('CANCEL_REQUESTED', delivery['state'], delivery)
        self.assertEqual(1, self.cancel_calls)
        self.record = self.execution.load(self.intent.intent_id)
        self.confirm_cancel(self.record, filled=1)
        self.assertEqual('PROCESSED', (await self.consume(await self.inputs()))['state'])
        self.assertEqual(('SELL', 2), (self.transport.calls[-1][1], self.transport.calls[-1][-1]))
        self.assertTrue(self.runner._protection_pending())

    async def test_buy_wait_without_ev_creates_no_fixed_child_or_cancel(self):
        inputs = await self.prepare_active()
        inputs['assessment'] = replace(inputs['assessment'], reprice_evidence=None)
        delivery = await self.consume(inputs)
        self.assertEqual('WAITING', delivery['state'], delivery)
        self.assertEqual([], self.runner._unfilled_attempt_plan['children'])
        self.assertEqual((0, 0), (self.cancel_calls, self.refreshes))

    async def test_profit_without_ev_and_trend_waits_without_forced_exit(self):
        inputs = await self.prepare_active(False)
        inputs['assessment'] = replace(inputs['assessment'], reprice_evidence=None, trend_allows_wait=True)
        self.assertEqual('WAITING', (await self.consume(inputs))['state'])
        self.assertEqual((0, 2), (self.cancel_calls, len(self.transport.calls)))

    async def test_invalid_buy_thesis_cancels_to_abandon_without_ev_or_price(self):
        inputs = await self.prepare_active()
        inputs['assessment'] = replace(inputs['assessment'], thesis_valid=False, reprice_evidence=None,
                                       candidate_limit_price_won=None, price_input_ref="", price_available_at=None)
        self.assertEqual('CANCEL_REQUESTED', (await self.consume(inputs))['state'])
        self.assertEqual('ABANDON', self.runner.status['after_cancel'])
        self.assertEqual(1, self.cancel_calls)

    async def test_pending_protection_allows_buy_cancel_but_no_replacement(self):
        inputs = await self.prepare_active()
        self.runner.request_protection_review(reason='hard_risk', input_ref='risk', requested_at=self.clock)
        self.assertEqual('CANCEL_REQUESTED', (await self.consume(inputs))['state'])
        self.assertEqual('ABANDON', self.runner.status['after_cancel'])
        self.assertEqual(1, len(self.transport.calls))

    async def test_expired_buy_opportunity_cancels_and_abandons_after_confirmation(self):
        await self.prepare_active()
        self.clock = self.policy.wait_until
        await self.bundle.gateway._account_refresh()
        inputs = await self.inputs()
        self.assertEqual('CANCEL_REQUESTED', (await self.consume(inputs))['state'])
        self.assertEqual('ABANDON', self.runner.status['after_cancel'])
        self.confirm_cancel(self.execution.load(self.intent.intent_id))
        self.assertEqual('ABANDONED', (await self.consume(await self.inputs()))['state'])
        self.assertEqual(1, len(self.transport.calls))

    async def test_fresh_rest_delay_does_not_renew_old_quote_thesis_ev(self):
        inputs = await self.prepare_active(True)
        self.refresh_hook = lambda: setattr(self, 'clock', self.clock + timedelta(seconds=6))
        delivery = await self.consume(inputs)
        self.assertEqual('BLOCKED', delivery['state'], delivery)
        self.assertIn('expired', delivery['reason'])
        self.assertEqual(0, self.cancel_calls)
        self.assertFalse(any(e['event_type']=='CANCEL_STARTED' for e in self.execution.events(self.intent.intent_id)))

    async def test_stop_during_rest_blocks_cancel_claim(self):
        inputs = await self.prepare_active()
        self.refresh_hook = lambda: emergency_stop_mock_automation(self.repository, self.runtime,
            account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id, stopped_at=self.clock, reason='test stop')
        with self.assertRaisesRegex(RuntimeError, 'CONTROL_CHANGED'):
            await self.consume(inputs)
        self.assertEqual(0, self.cancel_calls)

    async def test_partial_fill_during_refresh_requires_new_native_assessment(self):
        inputs = await self.prepare_active()
        def fill():
            self.snapshot = replace(self.snapshot, state=OrderState.PARTIALLY_FILLED, filled_quantity=1,
                remaining_quantity=9, fills=(BrokerFill('race-fill', 1, 1000, self.clock),))
            self.positions['005930'] = 1
            self.cash -= 1000
        self.refresh_hook = fill
        delivery = await self.consume(inputs)
        self.assertEqual('BLOCKED', delivery['state'], delivery)
        self.assertIn('ASSESSED_ORDER_CHANGED', delivery['reason'])
        self.assertEqual(0, self.cancel_calls)

    async def test_missing_owner_cut_or_gateway_is_blocked(self):
        inputs = await self.prepare_active()
        gateway = self.bundle.gateway
        self.bundle.gateway = None
        self.assertEqual('BLOCKED', (await self.consume(inputs))['state'])
        self.assertEqual(0, self.refreshes)
        self.bundle.gateway = gateway
        self.clock += timedelta(milliseconds=100)
        inputs = await self.inputs()
        inputs['assessment'] = replace(inputs['assessment'], thesis_valid=False)
        original = gateway._account_refresh
        async def missing():
            account = await original()
            self.bundle.monitor.latest_position_observation = None
            return account
        gateway._account_refresh = missing
        self.assertEqual('BLOCKED', (await self.consume(inputs))['state'])
        self.assertEqual(0, self.cancel_calls)

    async def test_weaker_frozen_policy_never_reaches_gateway(self):
        await self.prepare_active()
        self.policy = replace(self.policy, maximum_adverse_slippage_bps=101)
        inputs = await self.inputs()
        delivery = await self.consume(inputs)
        self.assertEqual('BLOCKED', delivery['state'], delivery)
        self.assertIn('FROZEN_LIMIT', delivery['reason'])
        self.assertEqual(0, self.refreshes)

    async def test_halted_protective_order_stays_blocked_and_never_cancels_blindly(self):
        inputs = await self.prepare_active(True)
        inputs['assessment'] = replace(inputs['assessment'], market_status='HALTED')
        self.assertEqual('BLOCKED', (await self.consume(inputs))['state'])
        self.assertEqual((0, 0), (self.cancel_calls, self.refreshes))

    async def test_rejected_protective_cancel_arms_review_and_never_retries(self):
        from kiwoom_monitor.infrastructure.kiwoom_rest.mock_execution import SubmissionRejected
        inputs = await self.prepare_active(True)
        def reject(*_args):
            self.cancel_calls += 1
            raise SubmissionRejected('broker rejected')
        self.transport.cancel = reject
        delivery = await self.consume(inputs)
        self.assertEqual('CANCEL_UNCONFIRMED', delivery['state'], delivery)
        self.assertTrue(self.runner._protection_pending())
        self.clock += timedelta(milliseconds=100)
        await self.consume(await self.inputs())
        self.assertEqual((1, 1), (self.cancel_calls, self.refreshes))

    async def test_real_owner_monitor_supplies_reconciled_risk_account_for_protective_cancel(self):
        from kiwoom_monitor.central_server.execution_runtime import ManualMockOrderGateway
        inputs = await self.prepare_active(True)
        await self.bundle.gateway.close()
        case = self
        class Reader:
            calls = 0
            async def read(self):
                self.calls += 1
                case.clock += timedelta(milliseconds=50)
                return MockAccountRecovery(AccountSnapshot(ACCOUNT_REF, case.cash, 0, dict(case.positions), case.clock),
                    (replace(case.snapshot, as_of=case.clock),))
            async def read_trade_cost_pages(self, *_args):
                raise OSError('cost source unavailable; protection still manages risk')
        reader = Reader()
        monitor = MockAccountMonitor(reader, self.runtime, self.execution, now_provider=lambda: self.clock,
            automation_risk_repository=self.repository, automation_binding_revision=self.binding['binding_revision'],
            automation_account_scope=AccountScope('kiwoom', AccountEnvironment.MOCK, ACCOUNT_REF))
        monitor.set_account_order_observation_enabled(True)
        self.bundle.monitor = monitor
        self.addAsyncCleanup(monitor.close)
        self.bundle.gateway = ManualMockOrderGateway(self.runtime, self.execution, monitor.refresh_account,
                                                     now_provider=lambda: self.clock)
        self.addAsyncCleanup(self.bundle.gateway.close)
        delivery = await self.consume(inputs)
        self.assertEqual('CANCEL_REQUESTED', delivery['state'], delivery)
        self.assertEqual((1, 1), (reader.calls, self.cancel_calls))
        self.assertTrue(self.runner.status['cancel_risk_snapshot_id'])
        self.assertIsNone(monitor.latest_automation_risk_cut)  # Actual native cancel invalidates the cut.

    async def test_unadmitted_mock_ev_cannot_cancel_for_reprice(self):
        self.unfilled_execution_policy = replace(self.unfilled_execution_policy, allow_supplied_paired_mock_evidence=False)
        self.rebuild_native_policy()
        delivery = await self.consume(await self.prepare_active())
        self.assertEqual('BLOCKED', delivery['state'], delivery)
        self.assertIn('NOT_ADMITTED', delivery['reason'])
        self.assertEqual((0, 0), (self.cancel_calls, self.refreshes))

    async def test_thesis_expiry_after_rest_is_not_refreshed_by_new_risk(self):
        inputs = await self.prepare_active(False)
        inputs['assessment'] = replace(inputs['assessment'], thesis_valid_until=self.clock + timedelta(seconds=1))
        self.refresh_hook = lambda: setattr(self, 'clock', self.clock + timedelta(seconds=2))
        delivery = await self.consume(inputs)
        self.assertEqual('BLOCKED', delivery['state'], delivery)
        self.assertIn('assessment_changed', delivery['reason'])
        self.assertEqual(0, self.cancel_calls)

    async def test_running_close_joins_cancel_transport_and_native_receipt(self):
        import asyncio, threading
        inputs = await self.prepare_active(True)
        entered, release = threading.Event(), threading.Event()
        def held(*_args):
            self.cancel_calls += 1
            entered.set(); release.wait(5)
        self.transport.cancel = held
        self.runner.offer_unfilled_execution_inputs(**inputs)
        await self.runner.start()
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 3))
            closing = asyncio.create_task(self.runner.close())
            await asyncio.sleep(0.05)
            self.assertFalse(closing.done())
        finally:
            release.set()
        await asyncio.wait_for(closing, 5)
        self.assertEqual((1, 2), (self.cancel_calls, len(self.transport.calls)))
        self.assertEqual('CANCEL_ACCEPTED', self.execution.events(self.intent.intent_id)[-1]['event_type'])

    async def test_close_during_account_refresh_blocks_native_cancel_claim(self):
        import asyncio
        inputs = await self.prepare_active()
        entered, release = asyncio.Event(), asyncio.Event()
        refresh = self.bundle.gateway._account_refresh
        async def held():
            entered.set(); await release.wait()
            return await refresh()
        self.bundle.gateway._account_refresh = held
        self.runner.offer_unfilled_execution_inputs(**inputs)
        await self.runner.start()
        try:
            await asyncio.wait_for(entered.wait(), 3)
            closing = asyncio.create_task(self.runner.close())
            await asyncio.sleep(0.05)
            self.assertFalse(closing.done())
        finally:
            release.set()
        await asyncio.wait_for(closing, 5)
        self.assertEqual(0, self.cancel_calls)
        self.assertFalse(any(e['event_type']=='CANCEL_STARTED' for e in self.execution.events(self.intent.intent_id)))


class MockUnfilledNativeSourceTests(unittest.IsolatedAsyncioTestCase):
    unfilled_execution_policy = MockUnfilledReplacementDispatchTests.unfilled_execution_policy
    setUp = MockUnfilledReplacementDispatchTests.setUp
    _rank = MockAutomationWorkflowTests._rank
    _bar = MockAutomationWorkflowTests._bar
    _refresh_recovery = MockAutomationWorkflowTests._refresh_recovery
    approved_buy = MockUnfilledPurposeGateTests.approved_buy
    confirm_cancel = MockUnfilledRunningCancelTests.confirm_cancel
    inputs = MockUnfilledNativeAttemptTests.inputs
    setup_sell = MockUnfilledNativeAttemptTests.setup_sell
    count_native = MockUnfilledOffIntegrationTests.count_native
    retain_active = MockUnfilledRunningCancelTests.retain_active
    prepare_active = MockUnfilledRunningCancelTests.prepare_active

    async def native_setup(self, sell=None, *, partial=False):
        from kiwoom_monitor.central_server.execution_runtime import ManualMockOrderGateway
        from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
        from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
        await self.prepare_active(sell)
        await self.runner.close(); await self.bundle.gateway.close()
        case = self
        class Reader:
            calls = 0
            async def read(self):
                self.calls += 1; case.clock += timedelta(milliseconds=50)
                reserved = (case.snapshot.remaining_quantity * case.intent.limit_price
                    if case.intent.side.value == 'BUY' and case.snapshot.state not in {OrderState.CANCELLED,OrderState.FILLED} else 0)
                return MockAccountRecovery(AccountSnapshot(ACCOUNT_REF, case.cash, reserved, dict(case.positions), case.clock),
                    (replace(case.snapshot, as_of=case.clock),))
            async def read_trade_cost_pages(self, *_args):
                raise OSError('no paired/cost forecast; protective policy remains independent')
        self.reader = Reader()
        self.monitor = MockAccountMonitor(self.reader, self.runtime, self.execution, now_provider=lambda: self.clock,
            automation_risk_repository=self.repository, automation_binding_revision=self.binding['binding_revision'],
            automation_account_scope=AccountScope('kiwoom', AccountEnvironment.MOCK, ACCOUNT_REF))
        self.bundle.monitor = self.monitor
        self.addAsyncCleanup(self.monitor.close)
        self.bundle.gateway = ManualMockOrderGateway(self.runtime,self.execution,self.monitor.refresh_account,now_provider=lambda:self.clock)
        self.addAsyncCleanup(self.bundle.gateway.close)
        self.collector = CentralRealtimeCollector(lambda:'unused','real',RealtimeHub(),lambda:self.clock)
        self.collector._approved_trade_sources = {('005930','KRX')}
        self.collector._continuous_from = {('005930','KRX'):self.clock}
        self.quote(1001 if sell is None else 990)
        self.runner = MockAutomationRunner(self.store,self.bundle,self.spec,price_source=self.collector.automation_price_observation)
        self.addAsyncCleanup(self.runner.close)
        if sell is not None:
            from kiwoom_monitor.application.mock_automation_execution import MockAutomationDecisionGate
            # The isolated SELL root fixture predates a native original EXIT gate.
            # Build that approval using the same real application gate, not a v3 child.
            self.runtime.reconcile(self.intent.intent_id, replace(self.snapshot,state=OrderState.CANCELLED,remaining_quantity=0))
            self.snapshot = replace(self.snapshot,state=OrderState.CANCELLED,remaining_quantity=0)
            recovery = await self.monitor.refresh_recovery()
            risk = self.repository.load_latest_mock_automation_risk(ACCOUNT_REF)
            record_mock_automation_recovery_from_risk(self.repository,self.runtime,recovery,risk,account_ref=ACCOUNT_REF,spec_id=self.spec.spec_id)
            body = _automation_decision(self.run_id,'EXIT',self.clock).to_dict();body.pop('decision_id')
            body.update(quantity=3,signal_reference_price=1000,required_capital_won=0,
                        reasons=('stop_loss_reached',) if sell else ('target_reached',))
            decision = StrategyDecision(decision_id='decision_'+_hash(body),**body)
            gate,record,_ = self.runner._dispatch_original_and_register(decision,recovery,risk)
            self.assertIsNotNone(record,gate.reasons)
            self.intent = record.intent;self.record=record
            self.snapshot = BrokerOrderSnapshot(record.broker_order_id,ACCOUNT_REF,'005930',OrderState.ACCEPTED,0,3,self.clock,())
            self.quote(990)
        if partial:
            self.seed_partial_buy()
        await self.monitor.refresh_recovery()

    def quote(self, price):
        self.collector._latest_snapshots[('trade','005930')] = {'received_at':self.clock.timestamp(),
            'event':{'type':'trade','payload':{'market':'KRX','code':'005930','current_price':price}}}

    def seed_partial_buy(self):
        self.snapshot = replace(self.snapshot,state=OrderState.PARTIALLY_FILLED,filled_quantity=1,remaining_quantity=9,
            fills=(BrokerFill('first-partial',1,1000,self.clock),))
        self.positions={'005930':1}
        self.cash -= 1000

    async def test_first_approved_unfilled_buy_is_discovered_and_cancelled_without_risk_upgrade(self):
        await self.native_setup()
        with patch.object(self.runner,'offer_unfilled_execution_inputs',wraps=self.runner.offer_unfilled_execution_inputs) as offer:
            await self.runner.run_once()
        self.assertEqual(1,offer.call_count)
        self.assertEqual('BUY_OPPORTUNITY',self.runner._unfilled_registration['purpose'])
        self.assertEqual('CANCEL_REQUESTED',self.runner.status['unfilled_input_delivery']['state'])
        self.assertFalse(self.repository.load_latest_mock_automation_risk(ACCOUNT_REF).reconciliation_complete)
        self.assertEqual('ABANDON',self.runner.status['after_cancel'])
        self.assertEqual('UNAVAILABLE',self.runner.status['unfilled_native_source']['paired_ev'])
        self.assertEqual(1,self.cancel_calls)

    async def test_original_buy_deadline_cancels_without_price_or_ev_and_no_duplicate(self):
        await self.native_setup(partial=True)
        await self.runner.run_once()
        self.assertEqual('WAITING',self.runner.status['unfilled_input_delivery']['state'])
        policy = self.runner._validate_unfilled_registration(self.runner._unfilled_registration)
        self.clock = policy.wait_until
        self.collector._latest_snapshots.clear()
        await self.monitor.refresh_recovery()
        await self.runner.run_once()
        self.assertEqual(1,self.cancel_calls)
        self.assertEqual('ABANDON',self.runner.status['after_cancel'])
        self.assertEqual(OrderState.CANCEL_PENDING,self.execution.load(self.intent.intent_id).state)
        with patch.object(self.runner,'_manage_unfilled_inputs',side_effect=AssertionError('idle repeated management')):
            with self.count_native() as idle:
                for _ in range(10):await self.runner.run_once()
        self.assertEqual(0,idle['commits'])
        self.assertEqual(1,self.cancel_calls)
        self.native_idle_counts = idle

    async def test_protective_root_registers_from_real_exit_and_cancels_without_minute_or_ev(self):
        await self.native_setup(True)
        self.runner._bootstrap_pending=True
        self.assertEqual('PROTECTIVE_EXIT',self.runner._unfilled_registration['purpose'])
        with patch.object(self.store,'load_observation_revisions_after',side_effect=AssertionError('minute needed')):
            await self.runner.run_once()
        self.assertEqual(1,self.cancel_calls)
        self.assertEqual('CANCEL_REQUESTED',self.runner.status['unfilled_input_delivery']['state'])

    async def test_profit_root_registers_independently_and_blocks_missing_paired_ev(self):
        await self.native_setup(False)
        await self.runner.run_once()
        self.assertEqual('TAKE_PROFIT',self.runner._unfilled_registration['purpose'])
        self.assertEqual('BLOCKED',self.runner.status['unfilled_input_delivery']['state'])
        self.assertEqual(0,self.cancel_calls)

    async def test_protective_native_source_waits_for_cancel_confirmation_then_replaces_only_remaining(self):
        await self.native_setup(True)
        before=len(self.transport.calls)
        await self.runner.run_once()
        self.assertEqual(before,len(self.transport.calls))
        self.assertEqual(OrderState.CANCEL_PENDING,self.execution.load(self.intent.intent_id).state)
        self.snapshot=replace(self.snapshot,state=OrderState.CANCELLED,filled_quantity=1,remaining_quantity=0,
            fills=(BrokerFill('sell-during-cancel',1,1000,self.clock),))
        self.positions={'005930':2};self.cash+=1000
        await self.monitor.refresh_recovery()
        self.quote(990)
        await self.runner.run_once()
        self.assertEqual(before+1,len(self.transport.calls),self.runner.status)
        child=self.execution.load(self.runner._unfilled_attempt_plan['children'][-1]['intent']['intent_id'])
        self.assertEqual(2,child.intent.quantity)
        self.assertEqual(OrderState.ACCEPTED,child.state)
        for _ in range(5):await self.runner.run_once()
        self.assertEqual(before+1,len(self.transport.calls))

    async def test_protective_missing_price_becomes_ready_without_repeated_gate_on_ticks(self):
        await self.native_setup(True)
        self.collector._latest_snapshots.clear()
        await self.runner.run_once()
        self.assertEqual(0,self.cancel_calls)
        self.clock += timedelta(milliseconds=10)
        self.quote(990)
        await self.runner.run_once()
        self.assertEqual(1,self.cancel_calls)
        with patch.object(self.runner,'offer_unfilled_execution_inputs',side_effect=AssertionError('tick repeats delivery')):
            for price in (991,992,993):
                self.quote(price)
                await self.runner.run_once()

    async def test_healthy_rest_revisions_do_not_repeat_producer_or_full_gate(self):
        await self.native_setup(partial=True)
        await self.runner.run_once()
        with patch.object(self.runner,'offer_unfilled_execution_inputs',side_effect=AssertionError('healthy revision delivery')):
            for _ in range(5):
                await self.monitor.refresh_recovery()
                await self.runner.run_once()
        self.assertEqual(0,self.cancel_calls)

    async def test_flat_first_buy_exception_rejects_unknown_balance_and_aggregate_gaps(self):
        await self.native_setup()
        control = self.repository.load_mock_automation_control(ACCOUNT_REF)
        await self.runner._produce_native_unfilled_inputs(control)
        policy=self.runner._validate_unfilled_registration(self.runner._unfilled_registration)
        cut=self.monitor.latest_automation_execution_cut
        self.assertTrue(self.runner._flat_unfilled_buy_cancel_cut(policy,cut['observation'],cut['risk']))
        for risk in (replace(cut['risk'],submission_unknown_count=1),replace(cut['risk'],balance_mismatch_count=1)):
            self.assertFalse(self.runner._flat_unfilled_buy_cancel_cut(policy,cut['observation'],risk))
        for observation in (replace(cut['observation'],reconciliation_complete=False),
                            replace(cut['observation'],unmapped_broker_order_ids=('unknown',)),
                            replace(cut['observation'],open_orders=(replace(self.record,broker_reported_filled_quantity=1),))):
            self.assertFalse(self.runner._flat_unfilled_buy_cancel_cut(policy,observation,cut['risk']))

    async def test_registration_rejects_rehashed_goal_and_extended_deadline(self):
        from kiwoom_monitor.application.mock_automation_session_clock import content_id
        await self.native_setup()
        control=self.repository.load_mock_automation_control(ACCOUNT_REF)
        await self.runner._produce_native_unfilled_inputs(control)
        for change in ('goal','deadline'):
            value=deepcopy(self.runner._unfilled_registration)
            if change=='goal':value['target_quantity']+=1
            else:value['policy']['valid_until']=(self.clock+timedelta(days=1)).isoformat()
            value['registration_id']=content_id('unfilled_registration',{k:v for k,v in value.items() if k!='registration_id'})
            with self.assertRaisesRegex(ValueError,'frozen_policy_mismatch'):
                self.runner._validate_unfilled_registration(value)

    async def test_registration_restore_preserves_root_policy_time_and_detects_tamper(self):
        await self.native_setup()
        await self.runner.run_once()
        original = deepcopy(self.runner._unfilled_registration)
        await self.runner.close()
        self.runner = MockAutomationRunner(self.store,self.bundle,self.spec,price_source=self.collector.automation_price_observation)
        self.assertEqual(original,self.runner._unfilled_registration)
        await self.runner.close()
        rows = self.store.load_documents('execution_mock_automation_runner_current',ACCOUNT_REF,1)
        rows[0]['document']['unfilled_registration']['target_quantity'] += 1
        self.store.upsert_documents('execution_mock_automation_runner_current',rows)
        with self.assertRaisesRegex(ValueError,'invalid_unfilled_native_registration'):
            MockAutomationRunner(self.store,self.bundle,self.spec)

    async def test_collector_borrow_does_no_io_and_refuses_unapproved_nxt_gap_or_missing_price(self):
        await self.native_setup()
        with self.count_native() as reads:
            for _ in range(100):
                self.assertEqual(1001,self.collector.automation_price_observation('005930')['price_won'])
        self.assertEqual(0,reads['selects']);self.assertEqual(0,reads['commits'])
        self.collector._latest_snapshots[('trade','005930')]['event']['payload']['market']='NXT'
        self.assertIsNone(self.collector.automation_price_observation('005930'))
        self.quote(1001);self.collector._approved_trade_sources.clear()
        self.assertIsNone(self.collector.automation_price_observation('005930'))
        self.collector._approved_trade_sources.add(('005930','KRX'))
        self.collector._ram_observation_gap_at=self.clock+timedelta(seconds=1)
        self.assertIsNone(self.collector.automation_price_observation('005930'))

    async def test_owner_cut_invalidates_after_native_change_and_cannot_be_mutated_by_reader(self):
        await self.native_setup()
        cut=self.monitor.latest_automation_execution_cut
        self.assertTrue(cut['observation'].open_orders_complete)
        cut['recovery'].account.positions['005930']=999
        self.assertNotEqual(999,self.monitor.latest_automation_execution_cut['recovery'].account.positions.get('005930'))
        self.runtime.reconcile(self.intent.intent_id,self.snapshot)
        self.assertIsNone(self.monitor.latest_automation_execution_cut)

    async def test_close_disables_only_automation_scope_and_keeps_position_selection(self):
        await self.native_setup()
        self.monitor.set_account_order_observation_enabled(True)
        await self.runner.close()
        self.assertFalse(self.monitor._automation_execution_inputs_enabled)
        self.assertTrue(self.monitor._position_order_scope_requested)

    async def test_native_thread_recovery_requests_are_coalesced_on_owner_loop(self):
        import asyncio
        from threading import get_ident
        await self.native_setup()
        while not self.monitor._queue.empty():self.monitor._queue.get_nowait()
        self.monitor._recovery_queued=False
        owner=get_ident();threads=[]
        enqueue=self.monitor._queue.put_nowait
        def checked(item):
            threads.append(get_ident());enqueue(item)
        with patch.object(self.monitor._queue,'put_nowait',side_effect=checked):
            await asyncio.to_thread(lambda:[self.monitor.request_automation_execution_recovery() for _ in range(100)])
            await asyncio.sleep(0)
        self.assertEqual([owner],threads)
        self.assertEqual(1,self.monitor._queue.qsize())

    async def test_new_native_ack_during_producer_work_is_retained_and_old_cut_not_delivered(self):
        import kiwoom_monitor.central_server.mock_automation_runner as runner_module
        await self.native_setup(partial=True)
        original=runner_module.drain_thread_call
        changed=False
        async def interleaved(function,*args,**kwargs):
            nonlocal changed
            result=await original(function,*args,**kwargs)
            if function is record_mock_automation_recovery_from_risk and not changed:
                changed=True
                self.snapshot=replace(self.snapshot,filled_quantity=2,remaining_quantity=8,
                    fills=(*self.snapshot.fills,BrokerFill('second-partial',1,1000,self.clock)))
                self.positions={'005930':2};self.cash-=1000
                await self.monitor.refresh_recovery()
            return result
        control=self.repository.load_mock_automation_control(ACCOUNT_REF)
        with patch.object(runner_module,'drain_thread_call',side_effect=interleaved):
            with patch.object(self.runner,'offer_unfilled_execution_inputs',side_effect=AssertionError('superseded cut delivered')):
                await self.runner._produce_native_unfilled_inputs(control)
        self.assertTrue(self.runner._unfilled_source_pending)
        self.assertEqual('native_account_cut_superseded',self.runner.status['unfilled_native_source']['reason'])
        await self.runner.run_once()
        self.assertEqual('WAITING',self.runner.status['unfilled_input_delivery']['state'])
        self.assertEqual(0,self.cancel_calls)


class MockFirstProtectionExitTests(unittest.IsolatedAsyncioTestCase):
    unfilled_execution_policy=MockUnfilledReplacementDispatchTests.unfilled_execution_policy
    setUp=MockAutomationWorkflowTests.setUp
    _rank=MockAutomationWorkflowTests._rank
    _bar=MockAutomationWorkflowTests._bar
    _refresh_recovery=MockAutomationWorkflowTests._refresh_recovery
    make_loss=MockNativeRiskProtectionTests.make_loss
    count_native=MockUnfilledOffIntegrationTests.count_native

    async def asyncSetUp(self):
        from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
        from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
        from kiwoom_monitor.central_server.execution_runtime import ManualMockOrderGateway
        await MockNativeRiskProtectionTests.asyncSetUp(self)
        self.store._execution_wall_now=lambda:self.clock
        await self.runner.close()
        self.collector=CentralRealtimeCollector(lambda:'unused','real',RealtimeHub(),lambda:self.clock)
        self.collector._approved_trade_sources={('005930','KRX')}
        self.collector._continuous_from={('005930','KRX'):self.clock}
        self.runner=MockAutomationRunner(self.store,self.bundle,self.spec,price_source=self.collector.automation_price_observation)
        self.bundle.gateway=ManualMockOrderGateway(self.runtime,self.execution,self.monitor.refresh_account,now_provider=lambda:self.clock)
        self.make_loss();self.quote()
        await self.monitor.refresh_recovery()

    async def asyncTearDown(self):
        await self.runner.close();await self.monitor.close();await self.bundle.gateway.close()

    def quote(self):
        self.collector._latest_snapshots[('trade','005930')]={'received_at':self.clock.timestamp(),
            'event':{'type':'trade','payload':{'market':'KRX','code':'005930','current_price':30000}}}

    async def test_native_loss_creates_first_protective_exit_without_bars_or_ev_once(self):
        before=len(self.transport.calls)
        self.runner._bootstrap_pending=True
        with patch.object(self.store,'load_observation_revisions_after',side_effect=AssertionError('minute wait')):
            await self.runner.run_once()
        self.assertEqual(before+1,len(self.transport.calls),self.runner.status)
        self.assertEqual('ORDER_SUBMITTED',self.runner.status['first_protection_exit_state'])
        self.assertEqual('PROTECTIVE_EXIT',self.runner._unfilled_registration['purpose'])
        self.assertEqual(1,self.runner._unfilled_registration['root_intent']['quantity'])
        await self.runner.run_once()  # Consume the actual QUEUED/ACK event cursor once.
        with self.count_native() as idle:
            for _ in range(10):await self.runner.run_once()
        self.assertEqual(0,idle['commits']);self.assertEqual(before+1,len(self.transport.calls))

    async def test_lathed_loss_protection_does_not_wait_for_unavailable_costs(self):
        async def unavailable(*_args):raise OSError('cost not available')
        self.reader.read_trade_cost_pages=unavailable
        await self.monitor.refresh_recovery()
        self.assertIsNone(self.repository.load_latest_mock_automation_risk(ACCOUNT_REF).daily_net_pnl_won)
        await self.runner.run_once()
        self.assertEqual('ORDER_SUBMITTED',self.runner.status['first_protection_exit_state'],self.runner.status)

    async def test_unknown_or_unmapped_account_order_blocks_new_protective_submit(self):
        original=self.reader.read
        async def unknown():
            value=await original()
            return replace(value,orders=(BrokerOrderSnapshot('unmapped',ACCOUNT_REF,'005930',OrderState.ACCEPTED,0,1,self.clock,()),))
        self.reader.read=unknown
        await self.monitor.refresh_recovery()
        before=len(self.transport.calls)
        await self.runner.run_once()
        self.assertEqual(before,len(self.transport.calls));self.assertIsNone(self.runner._protection_exit)

    async def test_missing_price_and_later_quote_request_one_existing_recovery(self):
        self.collector._latest_snapshots.clear()
        await self.runner.run_once()
        self.assertIsNone(self.runner._protection_exit)
        self.clock+=timedelta(milliseconds=50);self.quote()
        requests=self.monitor.request_automation_execution_recovery
        with patch.object(self.monitor,'request_automation_execution_recovery',wraps=requests) as queued:
            for _ in range(10):await self.runner.run_once()
        self.assertEqual(1,queued.call_count)
        await self.monitor.refresh_recovery()
        await self.runner.run_once()
        self.assertEqual('ORDER_SUBMITTED',self.runner.status['first_protection_exit_state'],self.runner.status)

    async def test_fixed_checkpoint_ack_failure_never_submits_and_reuses_decision(self):
        save=self.store.upsert_documents;failed=False
        def fail(collection,rows):
            nonlocal failed
            if collection=='execution_mock_automation_runner_current' and rows[0]['document'].get('protection_exit') and not failed:
                failed=True;raise OSError('prepared ACK failed')
            return save(collection,rows)
        before=len(self.transport.calls)
        with patch.object(self.store,'upsert_documents',side_effect=fail):await self.runner.run_once()
        fixed=deepcopy(self.runner._protection_exit)
        self.assertEqual(before,len(self.transport.calls))
        await self.runner.close()
        self.runner=MockAutomationRunner(self.store,self.bundle,self.spec,price_source=self.collector.automation_price_observation)
        await self.monitor.refresh_recovery()
        await self.runner.run_once()
        self.assertEqual(fixed,self.runner._protection_exit)
        self.assertEqual(before+1,len(self.transport.calls),self.runner.status)

    async def test_queued_transaction_rejects_new_risk_after_approval(self):
        create=self.store.create_execution_intent
        def changed(value,**kwargs):
            if value.get('policy_version')=='mock-protection-exit/v1':
                current=self.repository.load_latest_mock_automation_risk(ACCOUNT_REF)
                from kiwoom_monitor.application.mock_automation_risk import mock_automation_risk_snapshot_from_dict
                body=current.to_dict();body.pop('snapshot_id');body['reconciliation_revision']+=1
                body['snapshot_id']=_hash(body)
                self.repository.save_mock_automation_risk_snapshot(mock_automation_risk_snapshot_from_dict(body))
            return create(value,**kwargs)
        before=len(self.transport.calls)
        with patch.object(self.store,'create_execution_intent',side_effect=changed):await self.runner.run_once()
        self.assertEqual(before,len(self.transport.calls))
        self.assertIn('MOCK_PROTECTION_RISK_OR_FIXED_CUT_CHANGED',self.runner.status['first_protection_exit_reason'])

    async def test_native_proof_survives_events_and_restart_without_second_submission(self):
        from kiwoom_monitor.application.mock_automation_execution import mock_automation_intent_id
        await self.runner.run_once();fixed=deepcopy(self.runner._protection_exit)
        intent_id=mock_automation_intent_id(self.spec.spec_id,fixed['decision']['decision_id'])
        with self.store._connection() as connection:
            stored=json.loads(connection.execute('SELECT document_json FROM central_execution_intents WHERE intent_id=?',(intent_id,)).fetchone()[0])
        self.assertEqual(fixed['workflow_id'],stored['mock_protection_claim']['workflow_id'])
        before=len(self.transport.calls)
        await self.runner.close()
        self.runner=MockAutomationRunner(self.store,self.bundle,self.spec,price_source=self.collector.automation_price_observation)
        for _ in range(5):await self.runner.run_once()
        self.assertEqual(fixed,self.runner._protection_exit);self.assertEqual(before,len(self.transport.calls))

    async def test_unknown_original_response_is_registered_and_never_resubmitted(self):
        from kiwoom_monitor.infrastructure.kiwoom_rest.mock_execution import SubmissionUnknown
        submit=self.transport.submit
        def unknown(intent):submit(intent);raise SubmissionUnknown('unknown protective response')
        before=len(self.transport.calls)
        with patch.object(self.transport,'submit',side_effect=unknown):await self.runner.run_once()
        root=self.execution.load(self.runner._unfilled_registration['root_intent']['intent_id'])
        self.assertEqual(OrderState.SUBMISSION_UNKNOWN,root.state)
        for _ in range(5):await self.runner.run_once()
        self.assertEqual(before+1,len(self.transport.calls))

    async def test_queued_ack_loss_restores_same_native_claim_without_transport(self):
        create=self.store.create_execution_intent
        def lost(value,**kwargs):
            result=create(value,**kwargs)
            if value.get('policy_version')=='mock-protection-exit/v1':raise OSError('QUEUED ACK lost')
            return result
        before=len(self.transport.calls)
        with patch.object(self.store,'create_execution_intent',side_effect=lost):await self.runner.run_once()
        frozen=deepcopy(self.runner._protection_exit)
        self.assertEqual(before,len(self.transport.calls));self.assertIsNone(self.runner._unfilled_registration)
        await self.runner.close()
        self.runner=MockAutomationRunner(self.store,self.bundle,self.spec,price_source=self.collector.automation_price_observation)
        await self.runner.run_once()
        self.assertEqual(frozen,self.runner._protection_exit)
        self.assertEqual(OrderState.QUEUED,self.execution.load(self.runner._unfilled_registration['root_intent']['intent_id']).state)
        self.assertEqual(before,len(self.transport.calls))

    async def test_receipt_ack_loss_recovers_original_purpose_without_second_order(self):
        save=self.runner._repository.save_mock_automation_dispatch_receipt
        def lost(receipt):save(receipt);raise OSError('receipt ACK lost')
        before=len(self.transport.calls)
        with patch.object(self.runner._repository,'save_mock_automation_dispatch_receipt',side_effect=lost):await self.runner.run_once()
        self.assertEqual(before+1,len(self.transport.calls));self.assertIsNone(self.runner._unfilled_registration)
        await self.runner.run_once()
        self.assertEqual('PROTECTIVE_EXIT',self.runner._unfilled_registration['purpose'])
        self.assertEqual(before+1,len(self.transport.calls))

    async def test_actual_fills_and_full_flat_cut_confirm_liquidation_not_submit_ack(self):
        await self.runner.run_once()
        self.assertNotEqual('LIQUIDATION_CONFIRMED',self.runner.status['first_protection_exit_state'])
        root=self.execution.load(self.runner._unfilled_registration['root_intent']['intent_id'])
        self.clock+=timedelta(milliseconds=100)
        snapshot=BrokerOrderSnapshot(root.broker_order_id,ACCOUNT_REF,'005930',OrderState.FILLED,1,0,self.clock,
            (BrokerFill('protect-fill',1,30000,self.clock),))
        self.runtime.reconcile(root.intent.intent_id,snapshot)
        self.positions={};self.cash+=30000
        read=self.reader.read
        async def confirmed():return replace(await read(),orders=(snapshot,))
        self.reader.read=confirmed
        await self.monitor.refresh_recovery()
        await self.runner.run_once()
        self.assertEqual('LIQUIDATION_CONFIRMED',self.runner.status['first_protection_exit_state'],self.runner.status)
        self.assertTrue(self.runner._protection_pending())  # No automatic BUY reactivation.
        await self.runner.close()
        self.runner=MockAutomationRunner(self.store,self.bundle,self.spec,price_source=self.collector.automation_price_observation)
        self.assertEqual('REVALIDATION_REQUIRED',self.runner.status['first_protection_exit_state'])

    async def test_control_stop_during_prepared_ack_prevents_native_claim(self):
        save=self.store.upsert_documents;stopped=False
        def stop(collection,rows):
            nonlocal stopped
            result=save(collection,rows)
            if collection=='execution_mock_automation_runner_current' and rows[0]['document'].get('protection_exit') and not stopped:
                stopped=True
                emergency_stop_mock_automation(self.repository,self.runtime,account_ref=ACCOUNT_REF,
                    spec_id=self.spec.spec_id,stopped_at=self.clock,reason='stop before first protective claim')
            return result
        before=len(self.transport.calls)
        with patch.object(self.store,'upsert_documents',side_effect=stop):await self.runner.run_once()
        self.assertEqual(before,len(self.transport.calls));self.assertIsNone(self.runner._unfilled_registration)

    async def test_frozen_first_decision_is_not_renewed_after_expiry(self):
        with patch.object(self.runner,'_dispatch_original_and_register',side_effect=OSError('before queue')):
            await self.runner.run_once()
        fixed=deepcopy(self.runner._protection_exit)
        self.clock+=timedelta(seconds=self.spec.maximum_data_gap_seconds+1)
        self.quote();await self.monitor.refresh_recovery()
        before=len(self.transport.calls)
        await self.runner.run_once()
        self.assertEqual(before,len(self.transport.calls));self.assertEqual(fixed,self.runner._protection_exit)

    async def test_postgres_claim_uses_same_proofs_and_row_locks_in_isolated_cursor(self):
        from kiwoom_monitor.central_server.database_execution import _require_mock_protection_claim
        create=self.store.create_execution_intent;case=self;statements=[]
        class Cursor:
            def execute(self,sql,args):
                statements.append(sql)
                if 'central_documents' in sql:
                    row=case.store.load_document(args[0],args[1],args[2]);self.result=(json.dumps(row['document']),) if row else None
                else:self.result=None
            def fetchone(self):return self.result
        def checked(value,**kwargs):
            if value.get('policy_version')=='mock-protection-exit/v1':
                _require_mock_protection_claim(Cursor(),kwargs['ownership'],value,'%s',now_provider=lambda:self.clock,new_intent_claim=True)
            return create(value,**kwargs)
        with patch.object(self.store,'create_execution_intent',side_effect=checked):await self.runner.run_once()
        self.assertTrue(statements);self.assertTrue(all('FOR UPDATE' in sql for sql in statements))
        self.assertEqual('ORDER_SUBMITTED',self.runner.status['first_protection_exit_state'],self.runner.status)

    async def test_native_queue_rejects_mutated_fixed_expiry(self):
        create=self.store.create_execution_intent
        def extended(value,**kwargs):
            if value.get('policy_version')=='mock-protection-exit/v1':
                value={**value,'expires_at':(self.clock+timedelta(hours=1)).isoformat()}
            return create(value,**kwargs)
        before=len(self.transport.calls)
        with patch.object(self.store,'create_execution_intent',side_effect=extended):await self.runner.run_once()
        self.assertEqual(before,len(self.transport.calls))
        self.assertIn('MOCK_PROTECTION_FIXED_APPROVAL_CHANGED',self.runner.status['first_protection_exit_reason'])

    async def test_close_joins_prepared_ack_and_blocks_first_order_before_claim(self):
        import asyncio
        from threading import Event
        entered,release=Event(),Event();save=self.store.upsert_documents
        def blocked(collection,rows):
            if collection=='execution_mock_automation_runner_current' and rows[0]['document'].get('protection_exit') and not entered.is_set():
                entered.set()
                if not release.wait(5):raise RuntimeError('test ACK wait timed out')
            return save(collection,rows)
        before=len(self.transport.calls)
        with patch.object(self.store,'upsert_documents',side_effect=blocked):
            await self.runner.start()
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait,3))
                closing=asyncio.create_task(self.runner.close());await asyncio.sleep(.05)
                self.assertFalse(closing.done())
            finally:release.set()
            await asyncio.wait_for(closing,5)
        self.assertEqual(before,len(self.transport.calls))

class MockProtectiveCancelRecoveryTests(unittest.IsolatedAsyncioTestCase):
    unfilled_execution_policy = replace(MockUnfilledReplacementDispatchTests.unfilled_execution_policy,
        maximum_protective_cancel_retries=1, protective_cancel_retry_delay_seconds=1)
    setUp = MockUnfilledReplacementDispatchTests.setUp
    _rank = MockAutomationWorkflowTests._rank
    _bar = MockAutomationWorkflowTests._bar
    _refresh_recovery = MockAutomationWorkflowTests._refresh_recovery
    approved_buy = MockUnfilledPurposeGateTests.approved_buy
    confirm_cancel = MockUnfilledRunningCancelTests.confirm_cancel
    inputs = MockUnfilledNativeAttemptTests.inputs
    setup_sell = MockUnfilledNativeAttemptTests.setup_sell
    count_native = MockUnfilledOffIntegrationTests.count_native
    retain_active = MockUnfilledRunningCancelTests.retain_active
    prepare_active = MockUnfilledRunningCancelTests.prepare_active
    native_setup = MockUnfilledNativeSourceTests.native_setup
    quote = MockUnfilledNativeSourceTests.quote
    rebuild_native_policy = MockUnfilledReplacementDispatchTests.rebuild_native_policy

    async def reject_first(self, *, unknown=False, repeat=False, sell=True):
        from kiwoom_monitor.infrastructure.kiwoom_rest.mock_execution import SubmissionRejected, SubmissionUnknown
        await self.native_setup(sell)
        self.store._execution_wall_now = lambda: self.clock
        def cancel(*_args):
            self.cancel_calls += 1
            if self.cancel_calls == 1 or repeat:
                raise (SubmissionUnknown if unknown else SubmissionRejected)('fixture cancel response')
        self.transport.cancel = cancel
        await self.runner.run_once()
        self.assertEqual(1,self.cancel_calls,self.runner.status)
        return self.runner.status.get('protective_cancel_recovery')

    async def current_cut(self, seconds=1.1):
        self.clock += timedelta(seconds=seconds)
        await self.monitor.refresh_recovery()
        self.quote(990)

    async def test_confirmed_reject_retries_only_cancel_and_persists_native_proof(self):
        cue = await self.reject_first()
        self.assertEqual(('WATCH',0),(cue['state'],cue['retries_used']))
        before = len(self.transport.calls)
        await self.current_cut()
        await self.runner.run_once()
        self.assertEqual((2,before),(self.cancel_calls,len(self.transport.calls)),self.runner.status)
        events = self.execution.events(self.intent.intent_id)
        starts = [row for row in events if row['event_type']=='CANCEL_STARTED']
        self.assertEqual(2,len(starts))
        self.assertEqual(2,starts[-1]['mock_cancel_retry_claim']['attempt'])
        self.assertEqual('WAIT_RECONCILIATION',self.runner._cancel_recovery['state'])
        self.assertEqual(OrderState.CANCEL_PENDING,self.runtime.load_intent(self.intent.intent_id).state)

    async def test_retry_budget_exhaustion_survives_restart_and_cannot_submit_third_cancel(self):
        await self.reject_first(repeat=True)
        await self.current_cut(); await self.runner.run_once()
        self.assertEqual((2,'ESCALATE'),(self.cancel_calls,self.runner._cancel_recovery['state']),self.runner.status)
        await self.runner.close()
        self.runner = MockAutomationRunner(self.store,self.bundle,self.spec,price_source=self.collector.automation_price_observation)
        self.addAsyncCleanup(self.runner.close)
        await self.current_cut(.2)
        for _ in range(5):await self.runner.run_once()
        self.assertEqual(2,self.cancel_calls)
        self.assertTrue(self.runner.status['requires_operator_attention'])

    async def test_unknown_cancel_never_retries_even_if_rest_still_reports_accepted(self):
        await self.reject_first(unknown=True)
        await self.current_cut(); await self.runner.run_once()
        self.assertEqual((1,'WAIT_RECONCILIATION'),(self.cancel_calls,self.runner._cancel_recovery['state']))
        self.assertEqual(1,sum(row['event_type']=='CANCEL_STARTED' for row in self.execution.events(self.intent.intent_id)))

    async def test_accepted_cancel_response_never_retries_before_cancel_confirmation(self):
        await self.reject_first()
        await self.current_cut(); await self.runner.run_once()
        await self.current_cut(.2); await self.runner.run_once()
        self.assertEqual(2,self.cancel_calls)
        self.assertFalse(self.runner._unfilled_attempt_plan['children'])

    async def test_original_deadline_escalates_without_new_order_or_cancel(self):
        cue = await self.reject_first(unknown=True)
        end = datetime.fromisoformat(cue['next_check_at'])
        self.clock = end + timedelta(milliseconds=1)
        await self.monitor.refresh_recovery(); self.quote(990)
        await self.runner.run_once()
        self.assertEqual((1,'ESCALATE'),(self.cancel_calls,self.runner._cancel_recovery['state']))
        self.assertTrue(self.runner.status['requires_operator_attention'])

    async def test_idle_polls_do_not_repeat_full_cancel_review_or_commit(self):
        await self.reject_first()
        await self.runner.run_once()  # Drain the once-only native ACK cursor.
        with self.count_native() as counts:
            for _ in range(10):await self.runner.run_once()
        self.assertEqual((1,0),(self.cancel_calls,counts['commits']),counts)

    async def test_sql_fence_rejects_risk_change_after_callback_before_cancel_claim(self):
        await self.reject_first()
        await self.current_cut()
        apply = self.store.append_execution_event
        def changed(intent,event,**kw):
            if (kw.get('ownership') or {}).get('cancel_retry_claim'):
                from kiwoom_monitor.application.mock_automation_risk import mock_automation_risk_snapshot_from_dict
                body=self.repository.load_latest_mock_automation_risk(ACCOUNT_REF).to_dict()
                body.pop('snapshot_id');body['reconciliation_revision']+=1;body['snapshot_id']=_hash(body)
                self.repository.save_mock_automation_risk_snapshot(mock_automation_risk_snapshot_from_dict(body))
            return apply(intent,event,**kw)
        with patch.object(self.store,'append_execution_event',side_effect=changed):
            with self.assertRaisesRegex(RuntimeError,'MOCK_PROTECTIVE_CANCEL_RETRY_NATIVE_CUT_CHANGED'):
                await self.runner.run_once()
        self.assertEqual(1,self.cancel_calls)
        self.assertEqual(1,sum(row['event_type']=='CANCEL_STARTED' for row in self.execution.events(self.intent.intent_id)))

    async def test_partial_fill_during_retry_rest_blocks_stale_cancel_quantity(self):
        await self.reject_first(); await self.current_cut()
        refresh = self.bundle.gateway._account_refresh
        async def filled():
            self.snapshot = replace(self.snapshot,state=OrderState.PARTIALLY_FILLED,filled_quantity=1,remaining_quantity=2,
                fills=(BrokerFill('retry-partial',1,990,self.clock),))
            self.positions={'005930':2};self.cash += 990
            return await refresh()
        self.bundle.gateway._account_refresh=filled
        await self.runner.run_once()
        self.assertEqual(1,self.cancel_calls)
        self.assertEqual('MOCK_AUTOMATION_CANCEL_ASSESSED_ORDER_CHANGED',self.runner.status['unfilled_input_delivery']['reason'])

    async def test_default_policy_preserves_identity_and_disallows_retry(self):
        from kiwoom_monitor.domain.execution_activation import MockUnfilledExecutionPolicy
        frozen = MockUnfilledReplacementDispatchTests.unfilled_execution_policy
        self.assertNotIn('maximum_protective_cancel_retries',frozen.to_dict())
        self.assertEqual(frozen.policy_id,MockUnfilledExecutionPolicy(**frozen.to_dict()).policy_id)
        self.assertNotEqual(frozen.policy_id,self.unfilled_execution_policy.policy_id)
        self.unfilled_execution_policy=frozen
        self.rebuild_native_policy()
        await self.reject_first();await self.current_cut();await self.runner.run_once()
        self.assertEqual((1,'ESCALATE'),(self.cancel_calls,self.runner._cancel_recovery['state']))

    async def test_invalid_retry_policy_is_rejected(self):
        for change in ({'maximum_protective_cancel_retries':4},{'maximum_protective_cancel_retries':True},
                       {'protective_cancel_retry_delay_seconds':0},{'protective_cancel_retry_delay_seconds':61},
                       {'maximum_protective_cancel_retries':0}):
            with self.subTest(change=change),self.assertRaises(ValueError):replace(self.unfilled_execution_policy,**change)

    async def test_take_profit_without_ev_is_not_promoted_by_retry_enabled_spec(self):
        await self.native_setup(False)
        await self.current_cut();await self.runner.run_once()
        self.assertEqual(0,self.cancel_calls)
        self.assertIsNone(self.runner._cancel_recovery)

    async def test_sql_fence_rechecks_latest_cancel_response_in_native_transaction(self):
        from kiwoom_monitor.domain.order_contract import order_event
        await self.reject_first(); await self.current_cut()
        append = self.store.append_execution_event
        def changed(intent,event,**kw):
            if (kw.get('ownership') or {}).get('cancel_retry_claim'):
                current=self.execution.load(self.intent.intent_id)
                row=order_event(current.intent.intent_id,'CANCEL_RESPONSE_UNKNOWN',current.state,self.clock,self.clock,reason='new uncertainty')
                self.execution.clear_automation_control()
                self.execution.apply(current,row)
            return append(intent,event,**kw)
        with patch.object(self.store,'append_execution_event',side_effect=changed):
            with self.assertRaisesRegex(RuntimeError,'MOCK_PROTECTIVE_CANCEL_RETRY_NATIVE_CUT_CHANGED'):
                await self.runner.run_once()
        self.assertEqual(1,self.cancel_calls)

    async def test_retry_claim_ack_loss_spends_budget_without_repeat_transport(self):
        append = self.store.append_execution_event
        await self.reject_first(); await self.current_cut()
        def lost(intent,event,**kw):
            result=append(intent,event,**kw)
            if (kw.get('ownership') or {}).get('cancel_retry_claim'):
                raise OSError('lost native retry ACK')
            return result
        with patch.object(self.store,'append_execution_event',side_effect=lost):
            with self.assertRaisesRegex(OSError,'lost native retry ACK'):await self.runner.run_once()
        self.assertEqual(1,self.cancel_calls)
        await self.runner.close()
        self.runner=MockAutomationRunner(self.store,self.bundle,self.spec,price_source=self.collector.automation_price_observation)
        self.addAsyncCleanup(self.runner.close)
        await self.current_cut(.2);await self.runner.run_once()
        self.assertEqual(1,self.cancel_calls)
        self.assertEqual(2,sum(row['event_type']=='CANCEL_STARTED' for row in self.execution.events(self.intent.intent_id)))
        self.assertEqual('WAIT_RECONCILIATION',self.runner._cancel_recovery['state'])

    async def test_corrupt_recovery_checkpoint_cannot_reset_or_redirect_root(self):
        await self.reject_first();await self.runner.close()
        rows=self.store.load_documents('execution_mock_automation_runner_current',ACCOUNT_REF,1)
        rows[0]['document']['protective_cancel_recovery']['managed_intent_id']='foreign-root'
        self.store.upsert_documents('execution_mock_automation_runner_current',rows)
        with self.assertRaisesRegex(ValueError,'invalid_protective_cancel_recovery_checkpoint'):
            MockAutomationRunner(self.store,self.bundle,self.spec)

    async def test_cancel_confirmed_during_retry_refresh_returns_terminal_without_another_cancel(self):
        await self.reject_first();await self.current_cut()
        refresh=self.bundle.gateway._account_refresh
        async def cancelled():
            self.snapshot=replace(self.snapshot,state=OrderState.CANCELLED,remaining_quantity=0)
            return await refresh()
        self.bundle.gateway._account_refresh=cancelled
        await self.runner.run_once()
        self.assertEqual((1,'RESOLVED'),(self.cancel_calls,self.runner._cancel_recovery['state']))
        self.assertFalse(self.runner._unfilled_attempt_plan['children'])

    async def test_parent_retry_budget_is_not_reset_for_a_child(self):
        from kiwoom_monitor.application.order_lifecycle import protective_cancel_recovery
        await self.reject_first(repeat=True);await self.current_cut();await self.runner.run_once()
        events=self.execution.events(self.intent.intent_id)
        child=replace(self.execution.load(self.intent.intent_id),intent=replace(self.intent,intent_id='child-fixture'))
        child_events=tuple({**row,'intent_id':child.intent.intent_id} for row in events
            if row['event_type'] in {'CANCEL_STARTED','CANCEL_REJECTED'})[:2]
        cue=protective_cancel_recovery(child,(*events,*child_events),
            self.runner._validate_unfilled_registration(self.runner._unfilled_registration),self.unfilled_execution_policy,self.clock)
        self.assertEqual((1,'ESCALATE'),(cue['retries_used'],cue['state']))

    async def test_close_during_retry_refresh_prevents_second_native_claim(self):
        import asyncio
        await self.reject_first();await self.current_cut()
        entered,release=asyncio.Event(),asyncio.Event();refresh=self.bundle.gateway._account_refresh
        async def held():
            entered.set();await release.wait();return await refresh()
        self.bundle.gateway._account_refresh=held
        await self.runner.start()
        try:
            await asyncio.wait_for(entered.wait(),3)
            closing=asyncio.create_task(self.runner.close());await asyncio.sleep(.05)
            self.assertFalse(closing.done())
        finally:release.set()
        await asyncio.wait_for(closing,5)
        self.assertEqual(1,self.cancel_calls)
        self.assertEqual(1,sum(row['event_type']=='CANCEL_STARTED' for row in self.execution.events(self.intent.intent_id)))

    async def test_postgres_retry_fence_borrows_same_cursor_and_row_locks(self):
        from kiwoom_monitor.central_server.database_execution import _require_mock_cancel_retry
        from kiwoom_monitor.infrastructure.persistence.execution_repository import _record_document
        await self.reject_first();await self.current_cut()
        policy=self.runner._validate_unfilled_registration(self.runner._unfilled_registration)
        record=self.execution.load(self.intent.intent_id);events=self.execution.events(self.intent.intent_id)
        risk=self.repository.load_latest_mock_automation_risk(ACCOUNT_REF)
        claim={'root_intent_id':policy.anchor_intent_id,'policy_id':self.unfilled_execution_policy.policy_id,
            'reject_event_id':next(row['event_id'] for row in events if row['event_type']=='CANCEL_REJECTED'),
            'attempt':2,'risk_snapshot_id':risk.snapshot_id,'risk_reconciliation_revision':risk.reconciliation_revision}
        ownership={**self.execution._ownership,'active_spec_id':self.spec.spec_id,'control_revision':1,'cancel_retry_claim':claim}
        pending=_record_document(replace(record,state=OrderState.CANCEL_PENDING))
        class Cursor:
            def __init__(self,cursor):self.cursor=cursor;self.queries=[]
            def execute(self,sql,args):
                self.queries.append(sql);return self.cursor.execute(sql.replace(' FOR UPDATE','').replace('%s','?'),args)
            def fetchone(self):return self.cursor.fetchone()
            def fetchall(self):return self.cursor.fetchall()
        with self.store._connection() as connection:
            cursor=Cursor(connection.cursor())
            event=_require_mock_cancel_retry(cursor,ownership,pending,
                {'event_type':'CANCEL_STARTED','intent_id':self.intent.intent_id},'%s',now_provider=lambda:self.clock)
        self.assertEqual(claim,event['mock_cancel_retry_claim'])
        self.assertTrue(cursor.queries)
        self.assertTrue(all('%s' in sql and sql.endswith(' FOR UPDATE') for sql in cursor.queries))

    async def test_retry_claim_never_authorizes_creation_of_another_sell(self):
        from kiwoom_monitor.central_server.database_execution import _require_execution_ownership
        with self.assertRaisesRegex(RuntimeError,'MOCK_PROTECTIVE_CANCEL_RETRY_CANNOT_CREATE_ORDER'):
            _require_execution_ownership(None,{'cancel_retry_claim':{}},{},'?',new_intent_claim=True)

    async def test_due_clock_requests_one_existing_owner_recovery_and_consumes_its_ack(self):
        import asyncio
        await self.reject_first();await self.runner.run_once()
        self.clock+=timedelta(seconds=1.1);self.quote(990)
        with patch.object(self.monitor,'request_automation_execution_recovery',wraps=self.monitor.request_automation_execution_recovery) as request:
            for _ in range(3):await self.runner.run_once()
        self.assertEqual(1,request.call_count)
        self.assertEqual(1,self.cancel_calls)
        # Run the actual existing coalesced queue consumer, without its network
        # collector or heartbeat; recovery itself uses the fixture REST reader.
        self.monitor._task=asyncio.create_task(self.monitor._run())
        await asyncio.wait_for(self.monitor._queue.join(),3)
        await self.runner.run_once()
        self.assertEqual(2,self.cancel_calls,self.runner.status)

    async def test_sql_fence_rejects_resealed_protection_deadline_extension(self):
        from kiwoom_monitor.application.order_lifecycle import seal_unfilled_attempt_plan
        await self.reject_first();await self.current_cut()
        append=self.store.append_execution_event
        def extended(intent,event,**kw):
            if (kw.get('ownership') or {}).get('cancel_retry_claim'):
                rows=self.store.load_documents('execution_mock_automation_runner_current',ACCOUNT_REF,1)
                plan=rows[0]['document']['unfilled_execution_preflight']
                end=datetime.fromisoformat(plan['anchor']['policy']['protection_deadline'])+timedelta(seconds=1)
                plan['anchor']['policy']['protection_deadline']=end.isoformat()
                rows[0]['document']['unfilled_execution_preflight']=seal_unfilled_attempt_plan(plan)
                self.store.upsert_documents('execution_mock_automation_runner_current',rows)
            return append(intent,event,**kw)
        with patch.object(self.store,'append_execution_event',side_effect=extended):
            with self.assertRaisesRegex(RuntimeError,'MOCK_PROTECTIVE_CANCEL_RETRY_POLICY_CHANGED'):
                await self.runner.run_once()
        self.assertEqual(1,self.cancel_calls)

class MockUnfilledPairedInputTests(unittest.IsolatedAsyncioTestCase):
    unfilled_execution_policy=replace(MockUnfilledReplacementDispatchTests.unfilled_execution_policy,reprice_horizon_seconds=1)
    setUp=MockUnfilledReplacementDispatchTests.setUp
    _rank=MockAutomationWorkflowTests._rank
    _bar=MockAutomationWorkflowTests._bar
    _refresh_recovery=MockAutomationWorkflowTests._refresh_recovery
    approved_buy=MockUnfilledPurposeGateTests.approved_buy
    confirm_cancel=MockUnfilledRunningCancelTests.confirm_cancel
    inputs=MockUnfilledNativeAttemptTests.inputs
    setup_sell=MockUnfilledNativeAttemptTests.setup_sell
    count_native=MockUnfilledOffIntegrationTests.count_native
    retain_active=MockUnfilledRunningCancelTests.retain_active
    prepare_active=MockUnfilledRunningCancelTests.prepare_active
    native_setup=MockUnfilledNativeSourceTests.native_setup
    quote=MockUnfilledNativeSourceTests.quote
    rebuild_native_policy=MockUnfilledReplacementDispatchTests.rebuild_native_policy

    async def ready(self,sell=False):
        await self.native_setup(sell)
        async def costs(*_args):
            return ({'trst_ovrl_trde_prps_array':[{'cntr_dt':c.fill_date.strftime('%Y%m%d'),
                'trde_dt':c.settlement_date.strftime('%Y%m%d'),'stk_cd':c.stock_code,'io_tp_nm':c.side,
                'trde_amt':str(c.gross_amount),'exct_amt':str(c.settlement_amount),'cmsn':str(c.commission),
                'trde_agri_tax':str(c.tax),'tax_sum_cmsn':str(c.total_cost)} for c in self.costs]},)
        self.reader.read_trade_cost_pages=costs
        await self.monitor.refresh_recovery();self.quote(990 if sell is not None else 1001)
        self.store._execution_wall_now=lambda:self.clock

    def population_args(self,**changes):
        from tests.unit.test_order_lifecycle import UnfilledPairedEvidenceTests
        record=self.execution.load(self.intent.intent_id)
        policy=self.runner._validate_unfilled_registration(self.runner._unfilled_registration)
        held=self.positions.get('005930',0)
        quantity=record.intent.quantity-record.filled_quantity
        target=self.runner._unfilled_registration['target_quantity']
        price=990 if record.intent.side.value=='SELL' else 1001
        rows=UnfilledPairedEvidenceTests.outcomes_for(record,policy,self.runner._unfilled_cost_model,self.clock,quantity,held,target,price)
        args=dict(root_intent_id=self.intent.intent_id,managed_intent_id=self.intent.intent_id,
                  population_ref='cohort:unfilled',available_at=self.clock,outcomes=rows)
        args.update(changes)
        return args

    def publish(self,**changes):
        return self.runner.offer_unfilled_reprice_outcomes(**self.population_args(**changes))

    async def test_tp_population_drives_cancel_and_confirmed_state_needs_new_pair_before_child(self):
        await self.ready();self.assertTrue(self.publish())
        await self.runner.run_once()
        self.assertEqual(1,self.cancel_calls,self.runner.status)
        self.assertEqual('REPRICE',self.runner.status['after_cancel'])
        before=len(self.transport.calls)
        self.snapshot=replace(self.snapshot,state=OrderState.CANCELLED,remaining_quantity=0)
        await self.monitor.refresh_recovery();self.quote(990)
        await self.runner.run_once()
        self.assertEqual(before,len(self.transport.calls))
        self.assertFalse(self.runner._unfilled_attempt_plan['children'])
        self.clock+=timedelta(milliseconds=1)
        self.assertTrue(self.publish())
        await self.monitor.refresh_recovery()
        await self.runner.run_once()
        self.assertEqual(before+1,len(self.transport.calls),self.runner.status)
        child=self.execution.load(self.runner._unfilled_attempt_plan['children'][-1]['intent']['intent_id'])
        self.assertEqual((3,990,'SELL'),(child.intent.quantity,child.intent.limit_price,child.intent.side.value))

    async def test_missing_population_keeps_ev_unknown_despite_current_cost_model(self):
        await self.ready();await self.runner.run_once()
        self.assertEqual(0,self.cancel_calls)
        status=self.runner.status['unfilled_native_source']
        self.assertTrue(status['cost_model_available'])
        self.assertEqual('UNAVAILABLE',status['paired_ev'])

    async def test_native_actual_bar_hold_supplies_bounded_wait_without_orders_or_state_mutation(self):
        await self.ready()
        self.runner._apply_new_fills()
        prior=self.runner._state
        self._bar(self.clock-timedelta(seconds=62),1005,1010)
        observation=self.observations[-1]
        await self.runner._consume(observation)
        self.assertEqual('HOLD',self.runner._unfilled_judgment['final_action'])
        self.assertEqual(prior,self.runner._state)
        await self.runner.run_once()
        self.assertEqual(('WAIT',0),(self.runner._unfilled_attempt_plan['result']['action'],self.cancel_calls))
        self.assertTrue(self.runner._unfilled_attempt_plan['result']['inputs']['assessment']['trend_allows_wait'])

    async def test_model_expiry_cannot_support_native_paired_reprice(self):
        await self.ready();self.publish()
        self.runner._unfilled_cost_model=replace(self.runner._unfilled_cost_model,valid_to=self.clock.isoformat())
        await self.runner.run_once()
        self.assertEqual(0,self.cancel_calls)
        self.assertFalse(self.runner.status['unfilled_native_source']['cost_model_available'])

    async def test_fresh_account_cannot_renew_population_publication_or_recompute_same_pair(self):
        await self.ready();self.publish()
        # Keep actual cost unavailable so this exercises preview without a cancel.
        async def missing(*_args):raise OSError('fixture actual cost absent')
        self.reader.read_trade_cost_pages=missing
        await self.monitor.refresh_recovery()
        with patch('kiwoom_monitor.central_server.mock_automation_runner.prepare_unfilled_reprice_evidence',
                   wraps=__import__('kiwoom_monitor.application.order_lifecycle',fromlist=['prepare_unfilled_reprice_evidence']).prepare_unfilled_reprice_evidence) as prepare:
            await self.runner.run_once()
            published=self.runner._unfilled_population['available_at']
            for _ in range(5):
                await self.monitor.refresh_recovery();self.quote(990);await self.runner.run_once()
        self.assertEqual(1,prepare.call_count)
        self.assertEqual(published,self.runner._unfilled_population['available_at'])
        self.assertEqual(0,self.cancel_calls)

    async def test_duplicate_population_notifications_do_no_db_io_and_keep_one_slot(self):
        await self.ready()
        args=self.population_args()
        with self.count_native() as counts:
            self.assertTrue(self.runner.offer_unfilled_reprice_outcomes(**args))
            for _ in range(100):self.assertFalse(self.runner.offer_unfilled_reprice_outcomes(**args))
        self.assertEqual((0,0),(counts['selects'],counts['commits']))
        self.assertIsNotNone(self.runner._unfilled_population)

    async def test_scope_order_and_duplicate_sample_are_rejected_before_publication(self):
        await self.ready()
        from tests.unit.test_order_lifecycle import UnfilledPairedEvidenceTests
        record=self.execution.load(self.intent.intent_id);policy=self.runner._validate_unfilled_registration(self.runner._unfilled_registration)
        rows=UnfilledPairedEvidenceTests.outcomes_for(record,policy,self.runner._unfilled_cost_model,self.clock,3,3,0,990)
        for changes in ({'root_intent_id':'foreign'},{'managed_intent_id':'foreign'},
                        {'available_at':self.clock+timedelta(microseconds=1)},{'outcomes':(*rows,rows[0])}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):self.publish(**changes)
        self.assertIsNone(self.runner._unfilled_population)

    async def test_unselected_supplied_evidence_and_protection_episode_cannot_accept_population(self):
        self.unfilled_execution_policy=replace(self.unfilled_execution_policy,allow_supplied_paired_mock_evidence=False)
        self.rebuild_native_policy();await self.ready()
        with self.assertRaises(ValueError):
            self.runner.offer_unfilled_reprice_outcomes(root_intent_id=self.intent.intent_id,
                managed_intent_id=self.intent.intent_id,population_ref='forbidden-protective',available_at=self.clock,outcomes=())

    async def test_restart_does_not_replay_empirical_population_or_cached_current_judgment(self):
        await self.ready();self.publish();await self.runner.run_once();await self.runner.close()
        self.runner=MockAutomationRunner(self.store,self.bundle,self.spec,price_source=self.collector.automation_price_observation)
        self.addAsyncCleanup(self.runner.close)
        self.assertIsNone(self.runner._unfilled_population)
        self.assertIsNone(self.runner._unfilled_judgment)
        self.assertIsNone(self.runner._unfilled_evidence_cache)

    async def test_protection_bypasses_population_cost_and_family_calculation(self):
        await self.ready(True)
        self.runner._unfilled_cost_model=None
        with patch.object(self.runner,'_prepare_native_unfilled_evidence',side_effect=AssertionError('protective EV delay')):
            await self.runner.run_once()
        self.assertEqual(1,self.cancel_calls)
        self.assertEqual('PROTECTIVE_EXIT',self.runner._unfilled_attempt_plan['result']['purpose'])
        with self.assertRaises(ValueError):
            self.runner.offer_unfilled_reprice_outcomes(root_intent_id=self.intent.intent_id,
                managed_intent_id=self.intent.intent_id,population_ref='forbidden-protective',available_at=self.clock,outcomes=())

    async def test_current_actual_cost_failure_still_blocks_ordinary_cancel_with_positive_pair(self):
        await self.native_setup(False);self.publish()
        await self.runner.run_once()
        self.assertEqual(0,self.cancel_calls)
        self.assertIn('unfilled_cancel_current_cost_evidence_unavailable',self.runner.status['unfilled_input_delivery']['reason'])

    async def test_current_pair_price_binding_changes_once_without_tick_recompute(self):
        await self.ready();self.publish()
        self.quote(980)
        with patch('kiwoom_monitor.central_server.mock_automation_runner.prepare_unfilled_reprice_evidence',
                   wraps=__import__('kiwoom_monitor.application.order_lifecycle',fromlist=['prepare_unfilled_reprice_evidence']).prepare_unfilled_reprice_evidence) as prepare:
            await self.runner.run_once()
            for _ in range(10):self.quote(980);await self.runner.run_once()
        self.assertEqual(1,prepare.call_count)
        self.assertEqual(0,self.cancel_calls)
        self.assertEqual('UNAVAILABLE',self.runner.status['unfilled_native_source']['paired_ev'])

    async def test_population_arriving_in_same_cut_requests_one_actual_ack_without_invented_time(self):
        import asyncio
        await self.ready();await self.runner.run_once()
        previous=self.runner._unfilled_input_identity
        self.publish()
        with patch.object(self.monitor,'request_automation_execution_recovery',wraps=self.monitor.request_automation_execution_recovery) as request:
            for _ in range(3):await self.runner.run_once()
        self.assertEqual(1,request.call_count)
        self.assertEqual(previous,self.runner._unfilled_input_identity)
        self.assertEqual(0,self.cancel_calls)
        self.monitor._task=asyncio.create_task(self.monitor._run())
        await __import__('asyncio').wait_for(self.monitor._queue.join(),3)
        await self.runner.run_once()
        self.assertEqual(1,self.cancel_calls,self.runner.status)

    async def test_expired_population_cannot_be_renewed_by_healthy_account_cut(self):
        await self.ready();self.publish()
        original=self.runner._unfilled_population['available_at']
        self.clock+=timedelta(seconds=6);await self.monitor.refresh_recovery();self.quote(990)
        await self.runner.run_once()
        self.assertEqual(original,self.runner._unfilled_population['available_at'])
        self.assertEqual(0,self.cancel_calls)
        self.assertEqual('UNAVAILABLE',self.runner.status['unfilled_native_source']['paired_ev'])

    async def test_registered_strategy_stop_loss_uses_protection_before_positive_ev(self):
        await self.ready();self.publish();self.runner._apply_new_fills()
        self._bar(self.clock-timedelta(seconds=62),960,1010)
        await self.runner._consume(self.observations[-1]);self.quote(980)
        self.assertTrue(self.runner._protection_pending())
        with patch.object(self.runner,'_prepare_native_unfilled_evidence',side_effect=AssertionError('protection waited on EV')):
            await self.runner.run_once()
        self.assertEqual(1,self.cancel_calls,self.runner.status)
        self.assertEqual('PROTECTIVE_EXIT',self.runner._unfilled_attempt_plan['result']['purpose'])



    async def test_healthy_alignment_ack_wakes_pending_runner_even_without_business_fact_change(self):
        import asyncio
        await self.ready();await self.runner.run_once();self.publish()
        await self.runner.run_once()
        self.assertEqual('awaiting_aligned_native_decision_cut',self.runner._unfilled_source_status['reason'])
        self.runner._wake_loop=asyncio.get_running_loop();self.runner._wake_event=asyncio.Event()
        await self.monitor.refresh_recovery()
        await asyncio.wait_for(self.runner._wake_event.wait(),.2)
        self.assertTrue(self.runner._unfilled_source_pending)


class MockImmediateStopLossTests(unittest.IsolatedAsyncioTestCase):
    setUp = MockAutomationWorkflowTests.setUp
    _rank = MockAutomationWorkflowTests._rank
    _bar = MockAutomationWorkflowTests._bar
    _refresh_recovery = MockAutomationWorkflowTests._refresh_recovery
    unfilled_execution_policy = MockFirstProtectionExitTests.unfilled_execution_policy
    count_native = MockFirstProtectionExitTests.count_native
    asyncSetUp = MockFirstProtectionExitTests.asyncSetUp
    asyncTearDown = MockFirstProtectionExitTests.asyncTearDown

    def make_loss(self):
        from tests.unit.test_order_lifecycle import _intent
        from kiwoom_monitor.domain.order_contract import OrderSide
        intent=replace(_intent(),intent_id='stop-buy',decision_id='stop-buy-decision',account_ref=ACCOUNT_REF,
            run_id=self.run_id,side=OrderSide.BUY,quantity=10,limit_price=70000,
            created_at=self.clock,expires_at=self.clock+timedelta(seconds=60))
        record=self.runtime.submit_automation_intent(intent,
            AccountSnapshot(ACCOUNT_REF,self.cash,0,dict(self.positions),self.clock),
            spec_id=self.spec.spec_id,control_revision=1)
        self.clock+=timedelta(seconds=1)
        self.runtime.reconcile(intent.intent_id,BrokerOrderSnapshot(record.broker_order_id,ACCOUNT_REF,
            '005930',OrderState.FILLED,10,0,self.clock,(BrokerFill('stop-buy-fill',10,70000,self.clock),)))
        self.positions['005930']=10;self.cash-=700000
        self.costs.append(DailyTradeCost(self.clock.astimezone(timezone(timedelta(hours=9))).date(),
            self.clock.date(),'005930','매수',700000,700000,0,0,0,origin_scope=self.spec.account_scope))

    def quote(self, price=70000):
        self.collector._latest_snapshots[('trade','005930')]={'received_at':self.clock.timestamp(),
            'event':{'type':'trade','payload':{'market':'KRX','code':'005930','current_price':price}}}

    def tick(self,price,symbol='005930'):
        self.collector._publish_parsed({'trnm':'REAL','data':[{'type':'0B','item':symbol,
            'values':{'10':str(price),'14':'100000','15':'2','20':'101530'}}]})

    def connect(self):
        from kiwoom_monitor.central_server.mock_automation_supervisor import MockAutomationSupervisor
        self.supervisor=MockAutomationSupervisor(self.store,None)
        self.supervisor._runners[ACCOUNT_REF]=self.runner
        self.collector.set_automation_price_listener(self.supervisor.observe_automation_price)

    def stop_price(self):
        return 70000*(10000-self.runner._config.stop_loss_bps)//10000

    async def test_native_fill_basis_and_old_snapshot_hash_compatibility(self):
        from kiwoom_monitor.application.mock_automation_risk import mock_automation_risk_snapshot_from_dict
        risk=self.repository.load_latest_mock_automation_risk(ACCOUNT_REF)
        self.assertEqual((('005930',70000),),risk.owned_entry_prices)
        body=risk.to_dict();body.pop('owned_entry_prices');body.pop('snapshot_id');body['snapshot_id']=_hash(body)
        legacy=mock_automation_risk_snapshot_from_dict(body)
        self.assertEqual(body,legacy.to_dict());self.assertFalse(legacy.owned_entry_prices)
        body['owned_entry_prices']=[['005930',69999]]
        with self.assertRaises(ValueError):mock_automation_risk_snapshot_from_dict(body)

    async def test_approved_tick_arms_and_wakes_without_db_bar_shadow_or_transport(self):
        import asyncio
        self.connect();self.runner._wake_loop=asyncio.get_running_loop();self.runner._wake_event=asyncio.Event()
        before=len(self.transport.calls)
        with self.count_native() as counts:
            self.tick(self.stop_price())
        await asyncio.wait_for(self.runner._wake_event.wait(),.2)
        self.assertEqual({'connections':0,'selects':0,'commits':0},counts)
        notice=self.runner._protection_snapshot()
        self.assertEqual(['SYMBOL_STOP_LOSS_REACHED'],notice['risk_trigger']['reasons'])
        self.assertEqual('EXIT_REQUIRED',notice['risk_trigger']['disposition'])
        self.assertEqual(before,len(self.transport.calls));self.assertFalse(notice['order_authorized'])

    async def test_stalled_eight_consumer_r4_cannot_delay_native_stop_and_first_exit(self):
        """Independent owners share the process, never an approval or account cut."""
        import asyncio
        import threading
        from tests.unit.test_position_runner_state import RunnerStateRamTests
        from kiwoom_monitor.central_server import market_theme_shadow_inputs as inputs

        shadow_case=RunnerStateRamTests();self.addCleanup(shadow_case.doCleanups)
        shadow,_,_,_,packet,_,_=shadow_case.prepare(8)
        shadow.offer_allocation_models(packet,inputs.decode_dynamic_allocation_model_publication(
            packet,shadow._allocation,1))
        entered,release=threading.Event(),threading.Event()
        original=inputs.dynamic_allocation_inputs_from_ram
        def held(*args,**kwargs):
            entered.set()
            if not release.wait(10):raise TimeoutError('controlled R4 stall not drained')
            return original(*args,**kwargs)

        self.connect();self.runner._wake_loop=asyncio.get_running_loop();self.runner._wake_event=asyncio.Event()
        before=len(self.transport.calls);pending=None
        try:
            with patch.object(inputs,'dynamic_allocation_inputs_from_ram',side_effect=held):
                pending=asyncio.create_task(shadow._dispatch(shadow._snapshot()))
                self.assertTrue(await asyncio.to_thread(entered.wait,2))
                self.clock+=timedelta(milliseconds=20)
                with self.count_native() as counts:self.tick(self.stop_price())
                await asyncio.wait_for(self.runner._wake_event.wait(),.2)
                self.assertEqual({'connections':0,'selects':0,'commits':0},counts)
                first=deepcopy(self.runner._protection_snapshot())
                self.assertFalse(pending.done())
                with (patch.object(self.store,'load_observation_revisions_after',side_effect=AssertionError('minute wait')),
                      patch.object(self.runner,'_prepare_native_unfilled_evidence',side_effect=AssertionError('EV wait'))):
                    await self.runner.run_once()
                    await self.monitor.refresh_recovery()
                    await self.runner.run_once()
                self.assertFalse(pending.done(),'native protection waited for R4 to finish')
                self.assertEqual(before+1,len(self.transport.calls),self.runner.status)
                self.assertEqual('ORDER_SUBMITTED',self.runner.status['first_protection_exit_state'])
                self.assertEqual('PROTECTIVE_EXIT',self.runner._unfilled_registration['purpose'])
                self.assertEqual(10,self.runner._unfilled_registration['root_intent']['quantity'])
                self.assertEqual(first,self.runner._protection_snapshot())
                self.assertEqual(10,self.runner._state.position_quantity,'submission is not liquidation')
                release.set();await asyncio.wait_for(pending,5)
            receipt=shadow.latest_results['position_allocation']
            self.assertEqual('EV_UNAVAILABLE',receipt['r4_action_comparison']['status'])
            self.assertFalse(receipt['input_bundle']['model_application_verified'])
            self.assertFalse(receipt['order_authorized'])
        finally:
            release.set()
            if pending is not None:await asyncio.gather(pending,return_exceptions=True)

    async def test_first_exit_uses_original_stop_quote_after_rebound_without_bars_or_ev(self):
        self.connect();self.clock+=timedelta(milliseconds=20);self.tick(self.stop_price())
        original=deepcopy(self.runner._protection_snapshot());before=len(self.transport.calls)
        with patch.object(self.store,'load_observation_revisions_after',side_effect=AssertionError('bar wait')):
            with patch.object(self.monitor,'request_automation_execution_recovery',wraps=self.monitor.request_automation_execution_recovery) as queued:
                for _ in range(10):await self.runner.run_once()
            self.assertEqual(1,queued.call_count)
            self.clock+=timedelta(milliseconds=20);self.tick(80000)
            await self.monitor.refresh_recovery();await self.runner.run_once()
        self.assertEqual(before+1,len(self.transport.calls),self.runner.status)
        self.assertEqual('ORDER_SUBMITTED',self.runner.status['first_protection_exit_state'])
        self.assertEqual(original,self.runner._protection_snapshot())
        self.assertEqual(self.stop_price(),self.runner._protection_exit['snapshot']['price_won'])
        self.assertEqual('PROTECTIVE_EXIT',self.runner._unfilled_registration['purpose'])
        self.assertEqual(10,self.runner._unfilled_registration['root_intent']['quantity'])

    async def test_thousand_healthy_and_duplicate_ticks_do_not_query_commit_or_rearm(self):
        self.connect()
        with self.count_native() as counts:
            for _ in range(500):self.tick(70000)
            self.assertIsNone(self.runner._protection_snapshot())
            self.tick(self.stop_price());original=deepcopy(self.runner._protection_snapshot())
            for _ in range(500):self.tick(self.stop_price()-1)
        self.assertEqual({'connections':0,'selects':0,'commits':0},counts)
        self.assertEqual(original,self.runner._protection_snapshot())

    async def test_unapproved_nxt_paused_and_nonpositive_quotes_do_not_arm(self):
        self.connect();self.collector._subscription_filter_active=True
        self.tick(self.stop_price(),'000660');self.tick(self.stop_price(),'005930_NX');self.tick(0)
        self.collector._credential_paused=True;self.tick(self.stop_price())
        self.assertIsNone(self.runner._protection_snapshot())

    async def test_missing_basis_and_prefill_quote_do_not_invent_stop(self):
        self.connect();risk,positions=self.runner._stop_loss_cut
        self.runner._stop_loss_cut=(replace(risk,owned_entry_prices=()),positions)
        self.tick(self.stop_price());self.assertIsNone(self.runner._protection_snapshot())
        self.runner._stop_loss_cut=(risk,positions)
        price=self.collector.automation_price_observation('005930')
        self.runner.observe_automation_price('005930',{**price,'available_at':risk.observed_at-timedelta(milliseconds=1)})
        self.assertIsNone(self.runner._protection_snapshot())

    async def test_missing_costs_cannot_delay_native_stop(self):
        async def unavailable(*_args):raise OSError('cost unavailable')
        self.reader.read_trade_cost_pages=unavailable
        await self.monitor.refresh_recovery();self.connect();self.tick(self.stop_price())
        await self.runner.run_once()
        self.assertEqual('ORDER_SUBMITTED',self.runner.status['first_protection_exit_state'],self.runner.status)

    async def test_expired_first_quote_never_refreshes_on_later_ticks_or_ack(self):
        self.connect();self.tick(self.stop_price());original=deepcopy(self.runner._protection_snapshot())
        self.clock+=timedelta(seconds=6);self.tick(self.stop_price()-100)
        await self.monitor.refresh_recovery();before=len(self.transport.calls);await self.runner.run_once()
        self.assertEqual(before,len(self.transport.calls));self.assertIsNone(self.runner._protection_exit)
        self.assertEqual(original,self.runner._protection_snapshot())
        self.assertEqual('protection_exit_price_unavailable',self.runner.status['first_protection_exit_reason'])

    async def test_native_unknown_blocks_stop_submission_until_reconciled(self):
        self.connect();self.tick(self.stop_price())
        original=self.reader.read
        async def unknown():
            value=await original()
            return replace(value,orders=(BrokerOrderSnapshot('unmapped',ACCOUNT_REF,'005930',OrderState.ACCEPTED,0,1,self.clock,()),))
        self.reader.read=unknown;await self.monitor.refresh_recovery()
        before=len(self.transport.calls);await self.runner.run_once()
        self.assertEqual(before,len(self.transport.calls));self.assertIsNone(self.runner._protection_exit)

    async def test_queue_transaction_rejects_changed_basis_even_with_rehashed_risk(self):
        from kiwoom_monitor.application.mock_automation_risk import mock_automation_risk_snapshot_from_dict
        self.connect();self.tick(self.stop_price());create=self.store.create_execution_intent
        def changed(value,**kwargs):
            if value.get('policy_version')=='mock-protection-exit/v1':
                risk=self.repository.load_latest_mock_automation_risk(ACCOUNT_REF)
                body=risk.to_dict();body.pop('snapshot_id');body['reconciliation_revision']+=1
                body['owned_entry_prices']=[['005930',70001]];body['snapshot_id']=_hash(body)
                self.repository.save_mock_automation_risk_snapshot(mock_automation_risk_snapshot_from_dict(body))
            return create(value,**kwargs)
        before=len(self.transport.calls)
        with patch.object(self.store,'create_execution_intent',side_effect=changed):await self.runner.run_once()
        self.assertEqual(before,len(self.transport.calls))
        self.assertIn('mock_stop_loss_native_basis_changed',self.runner.status['first_protection_exit_reason'])

    async def test_restart_and_unknown_response_do_not_submit_second_stop(self):
        from kiwoom_monitor.infrastructure.kiwoom_rest.mock_execution import SubmissionUnknown
        self.connect();self.tick(self.stop_price());submit=self.transport.submit
        def unknown(intent):submit(intent);raise SubmissionUnknown('unknown stop ACK')
        before=len(self.transport.calls)
        with patch.object(self.transport,'submit',side_effect=unknown):await self.runner.run_once()
        frozen=deepcopy(self.runner._protection_exit);await self.runner.close()
        self.runner=MockAutomationRunner(self.store,self.bundle,self.spec,price_source=self.collector.automation_price_observation)
        self.supervisor._runners[ACCOUNT_REF]=self.runner
        for _ in range(5):self.tick(self.stop_price()-100);await self.runner.run_once()
        self.assertEqual(frozen,self.runner._protection_exit);self.assertEqual(before+1,len(self.transport.calls))

    async def test_closed_supervisor_and_runner_do_not_accept_price(self):
        self.connect();self.supervisor._closing=True;self.tick(self.stop_price())
        self.assertIsNone(self.runner._protection_snapshot())
        self.supervisor._closing=False;await self.runner.close();self.tick(self.stop_price())
        self.assertIsNone(self.runner._protection_snapshot())


    async def test_stale_native_cut_queues_once_and_fresh_ack_upgrades_original_stop(self):
        self.connect();self.clock+=timedelta(seconds=6)
        with patch.object(self.monitor,'request_automation_execution_recovery',wraps=self.monitor.request_automation_execution_recovery) as queued:
            with self.count_native() as counts:
                self.tick(self.stop_price());first=deepcopy(self.runner._protection_snapshot())
                for _ in range(100):self.tick(self.stop_price()-100)
        self.assertEqual(1,queued.call_count);self.assertEqual(0,counts['selects']);self.assertEqual(0,counts['commits'])
        self.assertEqual('RECONCILE_REQUIRED',first['risk_trigger']['disposition'])
        await self.monitor.refresh_recovery()
        upgraded=self.runner._protection_snapshot()
        self.assertEqual('EXIT_REQUIRED',upgraded['risk_trigger']['disposition'])
        self.assertEqual(first['risk_trigger']['stop_loss'],upgraded['risk_trigger']['stop_loss'])
        await self.runner.run_once();self.assertEqual('ORDER_SUBMITTED',self.runner.status['first_protection_exit_state'],self.runner.status)

    async def test_unmapped_order_recovery_keeps_first_stop_after_rebound(self):
        self.connect();self.tick(self.stop_price());first=deepcopy(self.runner._protection_snapshot())
        original=self.reader.read
        async def unknown():
            value=await original()
            return replace(value,orders=(BrokerOrderSnapshot('unmapped',ACCOUNT_REF,'005930',OrderState.ACCEPTED,0,1,self.clock,()),))
        self.reader.read=unknown;await self.monitor.refresh_recovery()
        self.assertEqual(first,self.runner._protection_snapshot())
        before=len(self.transport.calls);await self.runner.run_once()
        self.assertEqual(before,len(self.transport.calls));self.assertIsNone(self.runner._protection_exit)
        self.clock+=timedelta(milliseconds=10);self.tick(80000);self.reader.read=original
        await self.monitor.refresh_recovery();await self.runner.run_once()
        self.assertEqual('ORDER_SUBMITTED',self.runner.status['first_protection_exit_state'],self.runner.status)
        self.assertEqual(first['risk_trigger']['stop_loss']['price_won'],self.runner._protection_exit['snapshot']['price_won'])

    async def test_policy_and_basis_validation_never_accept_caller_threshold(self):
        from kiwoom_monitor.application.mock_automation_risk import validate_mock_stop_loss_proof
        self.connect();self.tick(self.stop_price());proof=deepcopy(self.runner._protection_snapshot()['risk_trigger']['stop_loss'])
        proof['stop_loss_bps']-=1
        with self.assertRaisesRegex(ValueError,'mock_stop_loss_proof_invalid'):
            validate_mock_stop_loss_proof(proof,self.spec,self.runner._stop_loss_cut[0],stop_loss_bps=self.runner._config.stop_loss_bps)
        self.assertEqual(1,len(self.transport.calls))


    async def test_postgres_stop_claim_borrows_same_cursor_with_candidate_row_lock(self):
        self.connect();self.tick(self.stop_price())
        await MockFirstProtectionExitTests.test_postgres_claim_uses_same_proofs_and_row_locks_in_isolated_cursor(self)

    async def test_native_candidate_mutation_before_queue_blocks_stop(self):
        self.connect();self.tick(self.stop_price());create=self.store.create_execution_intent
        def changed(value,**kwargs):
            if value.get('policy_version')=='mock-protection-exit/v1':
                collection='execution_mock_automation_candidate_packages'
                row=self.store.load_document(collection,self.spec.strategy_ref,self.spec.candidate_package_hash)
                document=deepcopy(row['document']);document['candidate_spec']['parameters']['stop_loss_bps']+=1
                self.store.upsert_documents(collection,[{'owner':self.spec.strategy_ref,'key':self.spec.candidate_package_hash,'document':document}])
            return create(value,**kwargs)
        before=len(self.transport.calls)
        with patch.object(self.store,'create_execution_intent',side_effect=changed):await self.runner.run_once()
        self.assertEqual(before,len(self.transport.calls));self.assertEqual('BLOCKED',self.runner.status['first_protection_exit_state'])

    async def test_partial_sell_and_additional_buy_keep_runner_and_native_basis_identical(self):
        from tests.unit.test_order_lifecycle import _intent
        from kiwoom_monitor.domain.order_contract import OrderSide
        for key,side,quantity,price in (('basis-add',OrderSide.BUY,3,70013),('basis-reduce',OrderSide.SELL,4,71000)):
            intent=replace(_intent(),intent_id=key,decision_id=key+'-decision',account_ref=ACCOUNT_REF,run_id=self.run_id,
                side=side,quantity=quantity,limit_price=price,created_at=self.clock,expires_at=self.clock+timedelta(seconds=60))
            record=self.runtime.submit_automation_intent(intent,AccountSnapshot(ACCOUNT_REF,self.cash,0,dict(self.positions),self.clock),
                spec_id=self.spec.spec_id,control_revision=1)
            self.clock+=timedelta(milliseconds=10)
            self.runtime.reconcile(intent.intent_id,BrokerOrderSnapshot(record.broker_order_id,ACCOUNT_REF,'005930',OrderState.FILLED,
                quantity,0,self.clock,(BrokerFill(key+'-fill',quantity,price,self.clock),)))
            self.positions['005930']+=quantity if side is OrderSide.BUY else -quantity
        await self.monitor.refresh_recovery();self.runner._apply_new_fills()
        risk=self.repository.load_latest_mock_automation_risk(ACCOUNT_REF)
        self.assertEqual((('005930',70003),),risk.owned_entry_prices)
        self.assertEqual(70003,self.runner._state.entry_price);self.assertEqual(9,self.runner._state.position_quantity)
        self.assertEqual((('005930',9),),risk.owned_position_quantities)
