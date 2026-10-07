from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from kiwoom_monitor.application.breakout_strategy import default_shadow_breakout_config
from kiwoom_monitor.application.market_session_schedule import research_session_profile_document
from kiwoom_monitor.application.mock_automation_candidate import (
    CandidateEligibilityStatus,
    MockAutomationCandidatePackage,
    MockAutomationEligibilityPolicy,
    candidate_package_from_dict,
    eligibility_policy_from_dict,
    eligibility_receipt_from_dict,
    evaluate_candidate_eligibility,
    validate_publication_size,
)
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
from kiwoom_monitor.central_server.app import create_app
from kiwoom_monitor.central_server.config import CentralServerSettings
from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import ForwardEvaluationRepository


NOW = datetime(2026, 9, 20, tzinfo=timezone.utc)
ACCOUNT_REF = "af64a3fa-197f-49df-8e8b-65b71bee02d9"


def _hash(value):
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()


def _candidate_spec() -> dict:
    execution = SimulationExecutionConfig(
        EXECUTION_MODEL_VERSION, SAME_BAR_PATH_VERSION, 10_000_000,
        SimulationCostModel(
            COST_MODEL_VERSION, 2, 18, 5, "official_period_rule", "fixture",
            "2026-01-01T00:00:00+00:00", "2027-01-01T00:00:00+00:00",
        ),
    )
    return {
        "version": "final_candidate/v1",
        "family": BREAKOUT_FAMILY_ID,
        "parameters": default_shadow_breakout_config().to_dict(),
        "execution_model": execution.to_dict(),
        "session_profile": research_session_profile_document("krx-regular/v1"),
        "implementation_hash": "a" * 64,
    }


def _package() -> MockAutomationCandidatePackage:
    spec = _candidate_spec()
    evaluation = ResearchEvaluationSpec(
        "chronological_holdout/v1",
        (ResearchFoldSpec("final", "OOS", "2026-09-01T00:00:00+00:00", "2026-09-10T00:00:00+00:00"),),
        60, 0, 0, 10, 3,
        final_holdout_accessed_at="2026-09-11T00:00:00+00:00",
        final_holdout_access_reason="locked final evaluation",
    )
    return MockAutomationCandidatePackage(
        strategy_ref="strategy-1", candidate_spec=spec,
        candidate_spec_hash=_hash(spec), scientific_implementation_hash="a" * 64,
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


def _policy(package: MockAutomationCandidatePackage, **changes) -> MockAutomationEligibilityPolicy:
    values = dict(
        strategy_ref=package.strategy_ref, candidate_spec_hash=package.candidate_spec_hash,
        frozen_at=NOW - timedelta(seconds=1), minimum_closed_trades=10,
        minimum_active_days=3, minimum_net_realized_pnl_won=0,
        maximum_drawdown_ppm=100_000,
    )
    values.update(changes)
    return MockAutomationEligibilityPolicy(**values)


class MockAutomationCandidateTests(unittest.TestCase):
    def test_package_policy_and_receipt_round_trip_with_distinct_hashes(self) -> None:
        package = _package()
        policy = _policy(package)
        receipt = evaluate_candidate_eligibility(package, policy, account_ref=ACCOUNT_REF)

        self.assertNotEqual(package.package_hash, package.candidate_spec_hash)
        self.assertEqual(CandidateEligibilityStatus.ELIGIBLE, receipt.status)
        self.assertEqual(package, candidate_package_from_dict(package.to_dict()))
        self.assertEqual(policy, eligibility_policy_from_dict(policy.to_dict()))
        self.assertEqual(receipt, eligibility_receipt_from_dict(receipt.to_dict()))

    def test_tbd_late_or_failed_thresholds_are_blocked(self) -> None:
        package = _package()
        policies = (
            _policy(package, minimum_closed_trades=None),
            _policy(package, frozen_at=NOW + timedelta(seconds=1)),
            _policy(package, minimum_net_realized_pnl_won=120_001),
        )
        reasons = []
        for policy in policies:
            receipt = evaluate_candidate_eligibility(package, policy, account_ref=ACCOUNT_REF)
            self.assertEqual(CandidateEligibilityStatus.BLOCKED, receipt.status)
            reasons.extend(receipt.reasons)
        self.assertIn("TBD:minimum_closed_trades", reasons)
        self.assertIn("POLICY_FROZEN_AFTER_FINAL_STARTED", reasons)
        self.assertIn("MINIMUM_NET_PNL_NOT_MET", reasons)

    def test_hash_and_payload_guards_reject_mutation_and_oversize(self) -> None:
        package = _package()
        changed = deepcopy(package.to_dict())
        changed["candidate_spec"]["parameters"]["buffer_bps"] += 1
        with self.assertRaises(ValueError):
            candidate_package_from_dict(changed)
        with self.assertRaisesRegex(ValueError, "too large"):
            validate_publication_size(
                {"payload": "x" * (256 * 1024)}, {}, {},
            )

    def test_repository_publication_is_idempotent_and_binding_scoped(self) -> None:
        store = SQLiteQueryStore(Path(":memory:")); store.initialize(); self.addCleanup(store.close)
        store.register_account_identity({
            "broker": "kiwoom", "environment": "mock", "identity_fingerprint": "c" * 64,
            "account_ref": ACCOUNT_REF, "created_at": NOW.isoformat(),
        })
        binding = store.append_account_binding({
            "credential_profile_id": "nas-mock-default", "broker": "kiwoom",
            "environment": "mock", "account_ref": ACCOUNT_REF,
            "verified_at": NOW.isoformat(), "verification_method": "ka00001",
        })
        package = _package(); policy = _policy(package)
        receipt = evaluate_candidate_eligibility(package, policy, account_ref=ACCOUNT_REF)
        repository = ForwardEvaluationRepository(store)

        self.assertTrue(repository.publish_mock_automation_candidate(
            package, policy, receipt, credential_profile_id="nas-mock-default",
            expected_binding_revision=binding["binding_revision"],
        ))
        self.assertFalse(repository.publish_mock_automation_candidate(
            package, policy, receipt, credential_profile_id="nas-mock-default",
            expected_binding_revision=binding["binding_revision"],
        ))
        self.assertEqual(package, repository.load_mock_automation_candidate_package(
            package.strategy_ref, package.package_hash,
        ))
        publications = repository.load_mock_automation_candidate_publications(ACCOUNT_REF)
        self.assertEqual(((package, policy, receipt),), publications)
        self.assertEqual((), repository.load_mock_automation_candidate_publications(
            "00000000-0000-0000-0000-000000000000",
        ))
        with self.assertRaisesRegex(ValueError, "current verified mock binding"):
            repository.publish_mock_automation_candidate(
                package, policy, receipt, credential_profile_id="wrong",
                expected_binding_revision=binding["binding_revision"],
            )

        forged = replace(receipt, observed_metrics={**receipt.observed_metrics,
                                                    "closed_trade_count": 999})
        with self.assertRaisesRegex(ValueError, "does not match policy evaluation"):
            repository.publish_mock_automation_candidate(
                package, policy, forged, credential_profile_id="nas-mock-default",
                expected_binding_revision=binding["binding_revision"],
            )

        lowered = replace(policy, minimum_closed_trades=1)
        lowered_receipt = evaluate_candidate_eligibility(
            package, lowered, account_ref=ACCOUNT_REF,
        )
        with self.assertRaisesRegex(ValueError, "immutable document conflict"):
            repository.publish_mock_automation_candidate(
                package, lowered, lowered_receipt,
                credential_profile_id="nas-mock-default",
                expected_binding_revision=binding["binding_revision"],
            )

        conflicting = receipt.to_dict(); conflicting["reasons"] = ["changed"]
        with self.assertRaises(ValueError):
            eligibility_receipt_from_dict(conflicting)

    def test_authenticated_api_publishes_without_starting_orders(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "central.sqlite3"
            store = SQLiteQueryStore(path); store.initialize()
            store.register_account_identity({
                "broker": "kiwoom", "environment": "mock",
                "identity_fingerprint": "c" * 64, "account_ref": ACCOUNT_REF,
                "created_at": NOW.isoformat(),
            })
            binding = store.append_account_binding({
                "credential_profile_id": "nas-mock-default", "broker": "kiwoom",
                "environment": "mock", "account_ref": ACCOUNT_REF,
                "verified_at": NOW.isoformat(), "verification_method": "ka00001",
            })
            store.close()
            package = _package(); policy = _policy(package)
            receipt = evaluate_candidate_eligibility(package, policy, account_ref=ACCOUNT_REF)
            body = {
                "account_ref": ACCOUNT_REF,
                "credential_profile_id": "nas-mock-default",
                "expected_binding_revision": binding["binding_revision"],
                "package": package.to_dict(),
                "eligibility_policy": policy.to_dict(),
                "eligibility_receipt": receipt.to_dict(),
            }
            app = create_app(CentralServerSettings(f"sqlite:///{path}", "private-token"))
            with patch("kiwoom_monitor.application.research_implementation.research_implementation_hash", return_value="a" * 64), \
                    TestClient(app) as client:
                self.assertEqual(401, client.post(
                    "/api/v1/research/mock-automation-candidates", json=body,
                ).status_code)
                response = client.post(
                    "/api/v1/research/mock-automation-candidates", json=body,
                    headers={"Authorization": "Bearer private-token"},
                )
                duplicate = client.post(
                    "/api/v1/research/mock-automation-candidates", json=body,
                    headers={"Authorization": "Bearer private-token"},
                )
                listed = client.get(
                    f"/api/v1/research/mock-automation-candidates/{ACCOUNT_REF}",
                    params={"credential_profile_id": "nas-mock-default"},
                    headers={"Authorization": "Bearer private-token"},
                )
                wrong_profile = client.get(
                    f"/api/v1/research/mock-automation-candidates/{ACCOUNT_REF}",
                    params={"credential_profile_id": "wrong-profile"},
                    headers={"Authorization": "Bearer private-token"},
                )
                capability = client.get(
                    "/api/v1/capabilities",
                    headers={"Authorization": "Bearer private-token"},
                )
                conflict = client.post(
                    "/api/v1/research/mock-automation-candidates",
                    json={**body, "account_ref": "00000000-0000-0000-0000-000000000000"},
                    headers={"Authorization": "Bearer private-token"},
                )
            self.assertEqual((200, "saved", False), (
                response.status_code, response.json()["status"], response.json()["orders_started"],
            ))
            self.assertEqual((200, "unchanged"), (duplicate.status_code, duplicate.json()["status"]))
            self.assertEqual(200, listed.status_code)
            self.assertEqual(binding["binding_revision"], listed.json()["binding"]["binding_revision"])
            self.assertEqual(package.to_dict(), listed.json()["candidates"][0]["package"])
            self.assertEqual(409, wrong_profile.status_code)
            self.assertEqual(409, conflict.status_code)
            self.assertTrue(capability.json()["capabilities"]["mock_automation_candidate_publish_v1"])
            self.assertTrue(capability.json()["capabilities"]["mock_automation_candidate_read_v1"])


if __name__ == "__main__":
    unittest.main()
