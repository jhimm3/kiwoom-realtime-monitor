from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kiwoom_monitor.application.research_queue import (
    build_research_job_identity,
    should_notify_research_result,
)
from kiwoom_monitor.application.research_search import ExperimentSpec, SEARCH_VERSION
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository


def _spec(dataset_id: str = "dataset-1") -> ExperimentSpec:
    return ExperimentSpec(
        version=SEARCH_VERSION, hypothesis_refs=("hypothesis-1",),
        dataset_id=dataset_id, dataset_hash=f"hash-{dataset_id}",
        family_allowlist=("krx_bar_close_breakout/v1",),
        factor_allowlist=("rolling_high_breakout/v1",),
        parameter_space={"lookback_bars": (3,)},
        objective={"net_pnl_won": "maximize"}, constraints={},
        split_version="chronological_holdout/v1", max_trials=2,
        max_seconds=10, seed=1,
        research_context={"locked_request": "context-1"},
    )


class ResearchQueueTests(unittest.TestCase):
    def test_job_lifecycle_is_persistent_and_completed_manifest_is_not_requeued(self) -> None:
        spec = _spec()
        identity = build_research_job_identity(spec)
        with tempfile.TemporaryDirectory() as directory:
            repository = ResearchRepository(Path(directory) / "research.sqlite3")
            repository.save_search_experiment(spec.experiment_id, spec.to_dict())
            self.assertTrue(repository.enqueue_search_job(
                identity.job_id, identity.experiment_id, identity.dataset_id,
                identity.dataset_hash, max_retained_jobs=2,
            ))
            generation = repository.start_search_job(
                identity.job_id, owner_token="worker-a", lease_seconds=60,
            )
            self.assertEqual(1, generation)
            self.assertTrue(repository.finish_search_job(
                identity.job_id, "completed", {"used_trials": 2},
                owner_token="worker-a", generation=generation,
            ))
            self.assertFalse(repository.enqueue_search_job(
                identity.job_id, identity.experiment_id, identity.dataset_id,
                identity.dataset_hash, max_retained_jobs=2,
            ))
            job = repository.load_search_job(identity.job_id)
            events = repository.load_search_job_events(identity.job_id)
        self.assertEqual("completed", job["status"])
        self.assertEqual(1, job["attempt_count"])
        self.assertEqual(["queued", "running", "completed"], [row["status"] for row in events])

    def test_cancelled_job_can_be_requeued_and_retention_limit_rejects_new_job(self) -> None:
        first = _spec("one")
        second = _spec("two")
        first_id = build_research_job_identity(first)
        second_id = build_research_job_identity(second)
        with tempfile.TemporaryDirectory() as directory:
            repository = ResearchRepository(Path(directory) / "research.sqlite3")
            repository.save_search_experiment(first.experiment_id, first.to_dict())
            repository.save_search_experiment(second.experiment_id, second.to_dict())
            repository.enqueue_search_job(
                first_id.job_id, first_id.experiment_id, first_id.dataset_id,
                first_id.dataset_hash, max_retained_jobs=1,
            )
            generation = repository.start_search_job(
                first_id.job_id, owner_token="worker-a", lease_seconds=60,
            )
            repository.finish_search_job(
                first_id.job_id, "cancelled", {}, owner_token="worker-a",
                generation=generation,
            )
            self.assertTrue(repository.enqueue_search_job(
                first_id.job_id, first_id.experiment_id, first_id.dataset_id,
                first_id.dataset_hash, max_retained_jobs=1,
            ))
            with self.assertRaisesRegex(ValueError, "retention limit"):
                repository.enqueue_search_job(
                    second_id.job_id, second_id.experiment_id, second_id.dataset_id,
                    second_id.dataset_hash, max_retained_jobs=1,
                )

    def test_stale_owner_cannot_renew_or_finish_requeued_job(self) -> None:
        spec = _spec()
        identity = build_research_job_identity(spec)
        with tempfile.TemporaryDirectory() as directory:
            repository = ResearchRepository(Path(directory) / "research.sqlite3")
            repository.save_search_experiment(spec.experiment_id, spec.evidence_dict())
            repository.enqueue_search_job(
                identity.job_id, identity.experiment_id, identity.dataset_id,
                identity.dataset_hash, max_retained_jobs=2,
            )
            first_generation = repository.start_search_job(
                identity.job_id, owner_token="worker-a", lease_seconds=60,
            )
            repository.finish_search_job(
                identity.job_id, "cancelled", {}, owner_token="worker-a",
                generation=first_generation,
            )
            repository.enqueue_search_job(
                identity.job_id, identity.experiment_id, identity.dataset_id,
                identity.dataset_hash, max_retained_jobs=2,
            )
            second_generation = repository.start_search_job(
                identity.job_id, owner_token="worker-b", lease_seconds=60,
            )
            self.assertEqual(2, second_generation)
            self.assertFalse(repository.renew_search_job(
                identity.job_id, owner_token="worker-a", generation=first_generation,
                lease_seconds=60,
            ))
            self.assertFalse(repository.finish_search_job(
                identity.job_id, "failed", {}, owner_token="worker-a",
                generation=first_generation,
            ))
            self.assertTrue(repository.finish_search_job(
                identity.job_id, "completed", {}, owner_token="worker-b",
                generation=second_generation,
            ))

    def test_interrupted_attempt_can_retry_and_only_result_commit_is_terminal(self) -> None:
        spec = _spec()
        identity = build_research_job_identity(spec)
        trial = {
            "trial_id": "trial-1", "experiment_id": spec.experiment_id,
            "ordinal": 0, "parameters": {}, "variant": "baseline",
        }
        outcome = {"status": "INELIGIBLE", "run_id": "", "reasons": ["no_trade"]}
        card = {
            "card_id": "card-1", "experiment_id": spec.experiment_id,
            "trial_id": "trial-1", "status": "INELIGIBLE",
        }
        with tempfile.TemporaryDirectory() as directory:
            repository = ResearchRepository(Path(directory) / "research.sqlite3")
            repository.save_search_experiment(spec.experiment_id, spec.evidence_dict())
            repository.enqueue_search_job(
                identity.job_id, identity.experiment_id, identity.dataset_id,
                identity.dataset_hash, max_retained_jobs=2,
            )
            first_generation = repository.start_search_job(
                identity.job_id, owner_token="worker-a", lease_seconds=60,
            )
            first_attempt = repository.start_trial_attempt(
                identity.job_id, spec.experiment_id, "trial-1",
                owner_token="worker-a", generation=first_generation,
            )
            self.assertTrue(repository.interrupt_trial_attempt(
                first_attempt, "user_requested", owner_token="worker-a",
                generation=first_generation,
            ))
            self.assertEqual((), repository.load_search_trials(spec.experiment_id))
            repository.finish_search_job(
                identity.job_id, "cancelled", {}, owner_token="worker-a",
                generation=first_generation,
            )
            repository.enqueue_search_job(
                identity.job_id, identity.experiment_id, identity.dataset_id,
                identity.dataset_hash, max_retained_jobs=2,
            )
            second_generation = repository.start_search_job(
                identity.job_id, owner_token="worker-b", lease_seconds=60,
            )
            second_attempt = repository.start_trial_attempt(
                identity.job_id, spec.experiment_id, "trial-1",
                owner_token="worker-b", generation=second_generation,
            )
            self.assertTrue(repository.commit_trial_result(
                second_attempt, trial, outcome, card, owner_token="worker-b",
                generation=second_generation,
            ))
            attempts = repository.load_trial_attempts(spec.experiment_id, "trial-1")
            results = repository.load_search_trials(spec.experiment_id)
        self.assertEqual(["INTERRUPTED", "INELIGIBLE"], [row["status"] for row in attempts])
        self.assertEqual(1, len(results))

    def test_notification_is_only_proposed_for_failure_or_changed_completed_cards(self) -> None:
        card = {"trial_id": "t1", "status": "COMPLETED", "net_pnl_won": 10}
        self.assertFalse(should_notify_research_result(
            terminal_status="cancelled", previous_cards=(), current_cards=(card,),
        ))
        self.assertTrue(should_notify_research_result(
            terminal_status="failed", previous_cards=(card,), current_cards=(card,),
        ))
        self.assertFalse(should_notify_research_result(
            terminal_status="completed", previous_cards=(card,), current_cards=(card,),
        ))
        self.assertTrue(should_notify_research_result(
            terminal_status="completed", previous_cards=(), current_cards=(card,),
        ))

    def test_completed_jobs_do_not_consume_active_backlog_limit(self) -> None:
        first, second = _spec("one"), _spec("two")
        first_id, second_id = build_research_job_identity(first), build_research_job_identity(second)
        with tempfile.TemporaryDirectory() as directory:
            repository = ResearchRepository(Path(directory) / "research.sqlite3")
            for spec in (first, second):
                repository.save_search_experiment(spec.experiment_id, spec.evidence_dict())
            repository.enqueue_search_job(
                first_id.job_id, first_id.experiment_id, first_id.dataset_id,
                first_id.dataset_hash, max_retained_jobs=1,
            )
            generation = repository.start_search_job(
                first_id.job_id, owner_token="worker-a", lease_seconds=60,
            )
            repository.finish_search_job(
                first_id.job_id, "completed", {}, owner_token="worker-a",
                generation=generation,
            )
            self.assertTrue(repository.enqueue_search_job(
                second_id.job_id, second_id.experiment_id, second_id.dataset_id,
                second_id.dataset_hash, max_retained_jobs=1,
            ))


if __name__ == "__main__":
    unittest.main()
