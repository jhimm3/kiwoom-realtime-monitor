from __future__ import annotations

import tempfile
import unittest
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kiwoom_monitor.application.breakout_strategy import (
    BreakoutStrategyConfig,
    StrategyState,
    evaluate_breakout_bar,
)
from kiwoom_monitor.application.research_replay import (
    CandidateUniverseFrame,
    KrxMinuteBarFrame,
)
from kiwoom_monitor.infrastructure.persistence.research_repository import (
    ResearchRepository,
    _MIGRATIONS,
    _migration_v1,
)
from kiwoom_monitor.infrastructure.persistence.schema_migrations import (
    SQLiteMigration,
    SQLiteMigrationRunner,
)


UTC = timezone.utc


def _evaluation(run_id: str = "run-1"):
    def bar(index: int, close: int, high: int) -> KrxMinuteBarFrame:
        start = datetime(2026, 9, 12, 0, index, tzinfo=UTC)
        end = start + timedelta(minutes=1)
        return KrxMinuteBarFrame(
            revision_id=f"bar-{index}", observation_key=start.isoformat(), code="005930",
            bar_start=start.isoformat(), bar_end=end.isoformat(),
            available_at=(end + timedelta(seconds=2)).isoformat(), open=close,
            high=high, low=close, close=close, volume=1, trade_value_million_won=1,
            session_finalized=False, capture_quality="complete", finalization_source="timer",
        )
    config = BreakoutStrategyConfig(
        "v1", "v1", "v1", 2, 0, False, False, 1, 60, 30, 0,
        300, 500, 10, 1, 1_000_000, 60, 30,
    )
    current = bar(2, 1030, 1040)
    universe = CandidateUniverseFrame(
        "rank", "rank", datetime(2026, 9, 12, 0, 2, tzinfo=UTC).isoformat(), ("005930",),
    )
    return evaluate_breakout_bar(
        run_id=run_id, evaluation_bar=current,
        bar_history=(bar(0, 1000, 1010), bar(1, 1010, 1020)),
        universe_frames=(universe,), config=config, state=StrategyState(),
    )


class ResearchRepositoryTests(unittest.TestCase):
    def test_search_experiment_and_terminal_trial_are_immutable_and_resumable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = ResearchRepository(Path(directory) / "research.sqlite3")
            spec = {"version": "limited_search/v1", "seed": 1}
            self.assertTrue(repository.save_search_experiment("experiment-1", spec))
            self.assertFalse(repository.save_search_experiment("experiment-1", spec))
            trial = {
                "trial_id": "trial-1", "experiment_id": "experiment-1",
                "ordinal": 0, "parameters": {}, "variant": "baseline",
            }
            outcome = {"status": "INELIGIBLE", "run_id": "", "reasons": ["no_trade"]}
            card = {
                "card_id": "card-1", "experiment_id": "experiment-1",
                "trial_id": "trial-1", "status": "INELIGIBLE",
            }
            self.assertTrue(repository.append_search_result(trial, outcome, card))
            self.assertFalse(repository.append_search_result(trial, outcome, card))
            self.assertEqual("INELIGIBLE", repository.load_search_trials("experiment-1")[0]["outcome"]["status"])
            self.assertEqual("card-1", repository.load_candidate_cards("experiment-1")[0]["card_id"])

    def test_existing_v1_run_is_preserved_when_v2_tables_are_added(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "research.sqlite3"
            with closing(sqlite3.connect(path)) as connection:
                SQLiteMigrationRunner(
                    connection, table="research_schema_migrations",
                ).apply((SQLiteMigration(1, "research_run_ledger", _migration_v1),))
                connection.execute(
                    "INSERT INTO research_runs(run_id,status,started_at,spec_json,input_manifest_json) "
                    "VALUES('old-run','running','now','{}','{}')"
                )
                connection.commit()

            repository = ResearchRepository(path)
            self.assertEqual(23, repository.schema_version())
            self.assertEqual("old-run", repository.load_run("old-run")["run_id"])

    def test_v8_job_is_preserved_when_attempt_fencing_is_added(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "research.sqlite3"
            with closing(sqlite3.connect(path)) as connection:
                SQLiteMigrationRunner(
                    connection, table="research_schema_migrations",
                ).apply(_MIGRATIONS[:8])
                connection.execute(
                    "INSERT INTO research_search_experiments(experiment_id,created_at,spec_json) "
                    "VALUES('experiment-v8','now','{}')"
                )
                connection.execute(
                    "INSERT INTO research_search_jobs("
                    "job_id,experiment_id,dataset_id,dataset_hash,status,created_at,updated_at"
                    ") VALUES('job-v8','experiment-v8','dataset','hash','queued','now','now')"
                )
                connection.commit()
            repository = ResearchRepository(path)
            job = repository.load_search_job("job-v8")
            version = repository.schema_version()
            self.assertEqual(23, version)
        self.assertEqual("queued", job["status"])
        self.assertEqual(0, job["generation"])
        self.assertEqual("", job["owner_token"])

    def test_migration_and_atomic_idempotent_append(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = ResearchRepository(Path(directory) / "research.sqlite3")
            evaluation = _evaluation()
            repository.start_run("run-1", {"mode": "fixture"}, {"dataset_id": "fixture"})
            self.assertTrue(repository.append_evaluation(evaluation))
            self.assertFalse(repository.append_evaluation(evaluation))
            repository.finish_run("run-1", "hash-1")
            self.assertFalse(repository.append_evaluation(evaluation))

            self.assertEqual(23, repository.schema_version())
            self.assertEqual(1, len(repository.load_evaluations("run-1")))
            self.assertEqual(1, len(repository.load_candidate_events("run-1")))
            self.assertEqual("completed", repository.load_run("run-1")["status"])

    def test_run_inputs_and_completed_hash_are_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = ResearchRepository(Path(directory) / "research.sqlite3")
            repository.start_run("run-1", {"a": 1}, {"dataset_id": "fixture"})
            with self.assertRaisesRegex(ValueError, "different immutable inputs"):
                repository.start_run("run-1", {"a": 2}, {"dataset_id": "fixture"})
            repository.finish_run("run-1", "hash-1")
            with self.assertRaisesRegex(ValueError, "immutable"):
                repository.finish_run("run-1", "hash-2")
            with self.assertRaisesRegex(ValueError, "completed run"):
                repository.append_evaluation(_evaluation("run-1"))

    def test_cancelled_run_can_resume_same_immutable_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = ResearchRepository(Path(directory) / "research.sqlite3")
            repository.start_run("run-1", {"a": 1}, {"dataset_id": "fixture"})
            repository.cancel_run("run-1")
            self.assertEqual("cancelled", repository.load_run("run-1")["status"])
            repository.start_run("run-1", {"a": 1}, {"dataset_id": "fixture"})
            self.assertEqual("running", repository.load_run("run-1")["status"])


if __name__ == "__main__":
    unittest.main()
