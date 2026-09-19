from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from kiwoom_monitor.application.breakout_strategy import default_shadow_breakout_config
from kiwoom_monitor.application.research_families import BREAKOUT_FAMILY_ID
from kiwoom_monitor.application.research_hypotheses import (
    DevelopmentEvidenceRef,
    HypothesisGenerationRequest,
    generate_research_hypotheses,
)
from kiwoom_monitor.infrastructure.persistence.research_repository import (
    RESEARCH_SCHEMA_VERSION,
    ResearchRepository,
    _MIGRATIONS,
)
from kiwoom_monitor.infrastructure.persistence.schema_migrations import SQLiteMigrationRunner


def _hypotheses(seed: int = 11):
    return generate_research_hypotheses(HypothesisGenerationRequest(
        research_scope_id="campaign:test-campaign",
        family_id=BREAKOUT_FAMILY_ID,
        factor_allowlist=("rolling_high_breakout/v1", "rank_persistence/v1"),
        baseline_parameters=default_shadow_breakout_config().to_dict(),
        allowed_parameter_values={"lookback_bars": (3, 7), "target_bps": (600,)},
        development_evidence_refs=(DevelopmentEvidenceRef("development-report-1"),),
        seed=seed,
        max_variants=20,
    ))


class ResearchHypothesisRepositoryTests(unittest.TestCase):
    def test_v20_database_migrates_without_changing_existing_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "research.sqlite3"
            with closing(sqlite3.connect(path)) as connection:
                SQLiteMigrationRunner(
                    connection, table="research_schema_migrations",
                ).apply(_MIGRATIONS[:20])
                connection.execute(
                    "INSERT INTO research_search_experiments(experiment_id,created_at,spec_json) "
                    "VALUES('existing','now','{}')"
                )
                connection.commit()

            repository = ResearchRepository(path)
            self.assertEqual(23, repository.schema_version())
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(
                    "existing",
                    connection.execute(
                        "SELECT experiment_id FROM research_search_experiments"
                    ).fetchone()[0],
                )

    def test_ordered_batch_persists_parent_lineage_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "research.sqlite3"
            repository = ResearchRepository(path)
            hypotheses = _hypotheses()

            self.assertEqual(len(hypotheses), repository.save_research_hypotheses(hypotheses))
            self.assertEqual(0, repository.save_research_hypotheses(hypotheses))
            self.assertEqual(hypotheses, repository.load_research_hypotheses())
            self.assertEqual(hypotheses[2], repository.load_research_hypothesis(
                hypotheses[2].hypothesis_id,
            ))
            with closing(sqlite3.connect(path)) as connection:
                edges = connection.execute(
                    "SELECT hypothesis_id,parent_id FROM research_hypothesis_parents "
                    "ORDER BY hypothesis_id"
                ).fetchall()
            self.assertEqual(len(hypotheses) - 1, len(edges))
            self.assertEqual({hypotheses[0].hypothesis_id}, {row[1] for row in edges})

    def test_missing_or_later_parent_rolls_back_whole_batch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = ResearchRepository(Path(directory) / "research.sqlite3")
            hypotheses = _hypotheses()
            with self.assertRaisesRegex(ValueError, "parent"):
                repository.save_research_hypotheses(hypotheses[1:])
            self.assertEqual((), repository.load_research_hypotheses())
            with self.assertRaisesRegex(ValueError, "parent"):
                repository.save_research_hypotheses(tuple(reversed(hypotheses)))
            self.assertEqual((), repository.load_research_hypotheses())

    def test_family_filter_and_limit_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = ResearchRepository(Path(directory) / "research.sqlite3")
            hypotheses = _hypotheses()
            repository.save_research_hypotheses(hypotheses)
            self.assertEqual(
                hypotheses[:2],
                repository.load_research_hypotheses(
                    family_id=BREAKOUT_FAMILY_ID, limit=2,
                ),
            )
            self.assertEqual((), repository.load_research_hypotheses(family_id="missing"))
            with self.assertRaises(ValueError):
                repository.load_research_hypotheses(limit=0)

    def test_schema_constant_matches_migration_tail(self) -> None:
        self.assertEqual(23, RESEARCH_SCHEMA_VERSION)
        self.assertEqual(23, _MIGRATIONS[-1].version)


if __name__ == "__main__":
    unittest.main()
