from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from kiwoom_monitor.application.breakout_strategy import default_shadow_breakout_config
from kiwoom_monitor.application.pullback_reacceleration_strategy import (
    FAMILY_ID as PULLBACK_FAMILY_ID,
    PullbackReaccelerationConfig,
)
from kiwoom_monitor.application.research_families import BREAKOUT_FAMILY_ID
from kiwoom_monitor.application.research_hypotheses import (
    DevelopmentEvidenceRef,
    HypothesisGenerationRequest,
    generate_research_hypotheses,
)
from kiwoom_monitor.application.research_queue import (
    ResearchCampaignPolicy,
    build_hypothesis_experiment_spec,
)
from kiwoom_monitor.application.research_search import ExperimentSpec, SEARCH_VERSION
from kiwoom_monitor.infrastructure.persistence.research_repository import (
    ResearchRepository,
    _MIGRATIONS,
)
from kiwoom_monitor.infrastructure.persistence.schema_migrations import SQLiteMigrationRunner
from kiwoom_monitor.research_process import (
    _campaign_request_from_spec,
    execute_campaign_cycle,
    generate_next_campaign_hypotheses,
    schedule_next_campaign_hypothesis,
)
from test_research_campaign_execution import write_campaign_request


def _template() -> ExperimentSpec:
    strategy = default_shadow_breakout_config().to_dict()
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
            "baseline_strategy": strategy,
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


def _pullback_config() -> PullbackReaccelerationConfig:
    return PullbackReaccelerationConfig(
        strategy_version="v1", pullback_factor_version="v1", rank_factor_version="v1",
        lookback_bars=3, minimum_pullback_bps=500, minimum_reacceleration_bps=100,
        rank_persistence_enabled=False, rank_persistence_required=False,
        rank_top_k=None, rank_window_seconds=None, rank_max_gap_seconds=None,
        rank_min_residency_seconds=None, stop_loss_bps=300, target_bps=500,
        max_hold_minutes=10, quantity=1, capital_won=1_000_000,
        signal_valid_seconds=60, cooldown_seconds=30,
    )


def _hypotheses(campaign_id: str):
    scope = f"campaign:{campaign_id}"
    breakout = generate_research_hypotheses(HypothesisGenerationRequest(
        research_scope_id=scope,
        family_id=BREAKOUT_FAMILY_ID,
        factor_allowlist=("rolling_high_breakout/v1", "rank_persistence/v1"),
        baseline_parameters=default_shadow_breakout_config().to_dict(),
        allowed_parameter_values={"lookback_bars": (7,)},
        development_evidence_refs=(DevelopmentEvidenceRef("development-breakout"),),
        seed=1,
        max_variants=10,
    ))
    pullback = generate_research_hypotheses(HypothesisGenerationRequest(
        research_scope_id=scope,
        family_id=PULLBACK_FAMILY_ID,
        factor_allowlist=("pullback_reacceleration/v1", "rank_persistence/v1"),
        baseline_parameters=_pullback_config().to_dict(),
        allowed_parameter_values={"minimum_pullback_bps": (700,)},
        development_evidence_refs=(DevelopmentEvidenceRef("development-pullback"),),
        seed=2,
        max_variants=10,
    ))
    return breakout, pullback


class ResearchHypothesisCampaignTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repository = ResearchRepository(self.root / "research.sqlite3")
        self.repository.create_campaign(
            "c", "자동 연구", ResearchCampaignPolicy(auto_hypotheses=True),
        )
        self.repository.enqueue_campaign_experiment("c", _template(), self.root / "dataset")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def register(self):
        groups = _hypotheses("c")
        for group in groups:
            self.repository.save_research_hypotheses(group)
        ids = tuple(item.hypothesis_id for group in groups for item in group)
        self.assertEqual(4, self.repository.register_campaign_hypotheses("c", ids))
        return groups, ids

    def claim(self):
        self.repository.set_campaign_desired_state("c", "RUNNING")
        return self.repository.claim_campaign_worker("c", owner_token="worker", lease_seconds=60)

    def test_registered_families_rotate_and_each_node_becomes_one_exact_job(self) -> None:
        groups, ids = self.register()
        claim = self.claim()
        scheduled = [
            schedule_next_campaign_hypothesis(self.repository, "c", claim)
            for _ in range(4)
        ]
        self.assertEqual(
            [BREAKOUT_FAMILY_ID, PULLBACK_FAMILY_ID, BREAKOUT_FAMILY_ID, PULLBACK_FAMILY_ID],
            [row["family_id"] for row in scheduled],
        )
        self.assertEqual(set(ids), {row["hypothesis_id"] for row in scheduled})
        auto_jobs = [
            job for job in self.repository.load_campaign_jobs("c")
            if job["source_kind"] == "auto_hypothesis"
        ]
        self.assertEqual(4, len(auto_jobs))
        for job in auto_jobs:
            spec = ExperimentSpec.from_dict(job["request"])
            hypothesis = self.repository.load_research_hypothesis(spec.hypothesis_refs[0])
            self.assertEqual({}, spec.parameter_space)
            self.assertEqual("manual", spec.generation_mode)
            self.assertTrue(spec.include_no_trade_baseline)
            self.assertEqual(hypothesis.parameters, spec.research_context["baseline_strategy"])
        self.assertEqual(
            "hypothesis_space_exhausted",
            schedule_next_campaign_hypothesis(self.repository, "c", claim)["reason"],
        )

    def test_registration_is_idempotent_and_rejects_another_campaign_scope(self) -> None:
        _, ids = self.register()
        self.assertEqual(0, self.repository.register_campaign_hypotheses("c", ids))
        foreign = _hypotheses("other")[0]
        self.repository.save_research_hypotheses(foreign)
        with self.assertRaisesRegex(ValueError, "another campaign scope"):
            self.repository.register_campaign_hypotheses(
                "c", tuple(item.hypothesis_id for item in foreign),
            )

    def test_disabled_policy_and_running_registration_are_rejected(self) -> None:
        self.repository.create_campaign("disabled", "수동", ResearchCampaignPolicy())
        foreign = _hypotheses("disabled")[0]
        self.repository.save_research_hypotheses(foreign)
        with self.assertRaisesRegex(ValueError, "disabled"):
            self.repository.register_campaign_hypotheses(
                "disabled", tuple(item.hypothesis_id for item in foreign),
            )
        _, ids = self.register()
        self.claim()
        with self.assertRaisesRegex(ValueError, "pause"):
            self.repository.register_campaign_hypotheses("c", ids)

    def test_worker_ownership_is_required_for_automatic_enqueue(self) -> None:
        self.register()
        claim = self.claim()
        self.repository.finish_campaign_worker(
            "c", owner_token=claim["owner_token"], generation=claim["generation"],
            outcome="EXPECTED_EXIT",
        )
        with self.assertRaisesRegex(ValueError, "worker"):
            schedule_next_campaign_hypothesis(self.repository, "c", claim)

    def test_second_family_job_reconstructs_as_a_typed_existing_process_request(self) -> None:
        fixture_root = self.root / "process-fixture"
        fixture_root.mkdir()
        _, request = write_campaign_request(fixture_root)
        pullback = _hypotheses("process")[1][0]
        spec = build_hypothesis_experiment_spec(request.search, pullback)
        reconstructed = _campaign_request_from_spec(
            spec, request.dataset, request.database, request.runs_dir,
        )
        self.assertEqual(PULLBACK_FAMILY_ID, reconstructed.family)
        self.assertEqual(pullback.parameters, reconstructed.strategy.to_dict())
        self.assertEqual((pullback.hypothesis_id,), reconstructed.search.hypothesis_refs)

    def test_no_template_has_an_explicit_wait_reason(self) -> None:
        self.repository.create_campaign(
            "empty", "빈 캠페인", ResearchCampaignPolicy(auto_hypotheses=True),
        )
        groups = _hypotheses("empty")
        self.repository.save_research_hypotheses(groups[0])
        self.repository.register_campaign_hypotheses(
            "empty", tuple(item.hypothesis_id for item in groups[0]),
        )
        self.repository.set_campaign_desired_state("empty", "RUNNING")
        claim = self.repository.claim_campaign_worker("empty", owner_token="empty-worker")
        result = schedule_next_campaign_hypothesis(self.repository, "empty", claim)
        self.assertEqual(("waiting", "hypothesis_template_missing"), (result["status"], result["reason"]))
        campaign = self.repository.load_campaign("empty")
        self.assertEqual(("WAITING_HYPOTHESIS", "hypothesis_template_missing"),
                         (campaign["operational_state"], campaign["reason"]))

    def test_v21_hypothesis_row_is_preserved_by_campaign_queue_migration(self) -> None:
        path = self.root / "v21.sqlite3"
        hypothesis = _hypotheses("legacy")[0][0]
        with closing(sqlite3.connect(path)) as connection:
            SQLiteMigrationRunner(
                connection, table="research_schema_migrations",
            ).apply(_MIGRATIONS[:21])
            connection.execute(
                "INSERT INTO research_hypotheses(hypothesis_id,family_id,status,created_at,document_json) "
                "VALUES(?,?,?,'now',?)",
                (hypothesis.hypothesis_id, hypothesis.family_id, hypothesis.status,
                 json.dumps(hypothesis.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
            )
            connection.commit()
        migrated = ResearchRepository(path)
        self.assertEqual(23, migrated.schema_version())
        self.assertEqual(hypothesis, migrated.load_research_hypothesis(hypothesis.hypothesis_id))

    def test_v22_available_campaign_hypothesis_is_preserved_by_expansion_migration(self) -> None:
        path = self.root / 'v22.sqlite3'
        hypothesis = _hypotheses('legacy-campaign')[0][0]
        policy = {
            'version': 'research_campaign/v1',
            'max_active_jobs': 100, 'max_attempts': 3,
            'retry_initial_seconds': 30, 'retry_max_seconds': 900,
            'auto_final_evaluation': False, 'auto_hypotheses': True,
        }
        with closing(sqlite3.connect(path)) as connection:
            SQLiteMigrationRunner(
                connection, table='research_schema_migrations',
            ).apply(_MIGRATIONS[:22])
            connection.execute(
                "INSERT INTO research_campaigns VALUES("
                "'legacy-campaign','legacy',1,'PAUSED','PAUSED','',0,'now','now')"
            )
            connection.execute(
                'INSERT INTO research_campaign_revisions VALUES(?,?,?,?)',
                ('legacy-campaign', 1, json.dumps(policy), 'now'),
            )
            connection.execute(
                "INSERT INTO research_campaign_workers(campaign_id,updated_at) "
                "VALUES('legacy-campaign','now')"
            )
            connection.execute(
                "INSERT INTO research_hypotheses(hypothesis_id,family_id,status,created_at,document_json) "
                "VALUES(?,?,?,'now',?)",
                (hypothesis.hypothesis_id, hypothesis.family_id, hypothesis.status,
                 json.dumps(hypothesis.to_dict(), ensure_ascii=False, sort_keys=True, separators=(',', ':'))),
            )
            connection.execute(
                "INSERT INTO research_campaign_hypotheses("
                "campaign_id,hypothesis_id,state,accepted_sequence,registered_at) "
                "VALUES(?,?,'AVAILABLE',1,'now')",
                ('legacy-campaign', hypothesis.hypothesis_id),
            )
            connection.commit()
        migrated = ResearchRepository(path)
        self.assertEqual(23, migrated.schema_version())
        self.assertEqual(
            hypothesis.hypothesis_id,
            migrated.load_campaign_hypothesis_bindings('legacy-campaign')[0]['hypothesis_id'],
        )
        self.assertEqual((), migrated.load_campaign_hypothesis_expansions('legacy-campaign'))

    def test_completed_development_baseline_generates_bounded_unique_followups_once(self) -> None:
        fixture_root = self.root / 'automatic-followup'
        fixture_root.mkdir()
        _, request = write_campaign_request(fixture_root)
        repository = ResearchRepository(request.database)
        repository.create_campaign(
            'followup', '자동 후속', ResearchCampaignPolicy(
                auto_hypotheses=True,
                hypothesis_seed=19,
                max_hypotheses=10,
                max_generated_hypotheses_per_cycle=3,
                hypothesis_parameter_values={
                    BREAKOUT_FAMILY_ID: {'target_bps': (400, 500, 600)},
                },
            ),
        )
        repository.enqueue_campaign_experiment('followup', request.search, request.dataset)
        root = generate_research_hypotheses(HypothesisGenerationRequest(
            research_scope_id='campaign:followup',
            family_id=BREAKOUT_FAMILY_ID,
            factor_allowlist=tuple(request.search.factor_allowlist),
            baseline_parameters=request.strategy.to_dict(),
            allowed_parameter_values={'target_bps': (500,)},
            development_evidence_refs=(DevelopmentEvidenceRef('bootstrap-development'),),
            seed=1,
            max_variants=0,
        ))[0]
        repository.save_research_hypotheses((root,))
        repository.register_campaign_hypotheses('followup', (root.hypothesis_id,))
        repository.set_campaign_desired_state('followup', 'RUNNING')
        claim = repository.claim_campaign_worker(
            'followup', owner_token='followup-worker', lease_seconds=60,
        )
        self.assertEqual(
            'scheduled',
            schedule_next_campaign_hypothesis(repository, 'followup', claim)['status'],
        )
        cycle = execute_campaign_cycle(
            repository, 'followup', request.runs_dir, worker_claim=claim,
        )
        self.assertEqual('COMPLETED', cycle['cycle_outcome'])
        cycle = execute_campaign_cycle(
            repository, 'followup', request.runs_dir, worker_claim=claim,
        )
        self.assertEqual('COMPLETED', cycle['cycle_outcome'])

        generated = generate_next_campaign_hypotheses(repository, 'followup', claim)
        self.assertEqual('generated', generated['status'], generated)
        self.assertEqual(2, generated['generated_count'])
        registered = repository.load_campaign_registered_hypotheses('followup')
        self.assertEqual(3, len(registered))
        self.assertEqual({400, 600}, {item.changed_to for item in registered[1:]})
        expansion = repository.load_campaign_hypothesis_expansions('followup')
        self.assertEqual(1, len(expansion))
        self.assertEqual('GENERATED', expansion[0]['state'])
        self.assertNotIn('FINAL', json.dumps(expansion[0]['evidence']))
        # Simulate a crash after child registration but before the expansion commit.
        with closing(sqlite3.connect(repository.path)) as connection:
            connection.execute(
                'DELETE FROM research_campaign_hypothesis_expansions WHERE campaign_id=?',
                ('followup',),
            )
            connection.commit()
        recovered = generate_next_campaign_hypotheses(repository, 'followup', claim)
        self.assertEqual(('generated', 2), (recovered['status'], recovered['generated_count']))
        self.assertEqual(3, len(repository.load_campaign_registered_hypotheses('followup')))
        self.assertEqual(
            'no_completed_hypothesis_to_expand',
            generate_next_campaign_hypotheses(repository, 'followup', claim)['reason'],
        )

        repository.set_campaign_desired_state('followup', 'PAUSED')
        repository.finish_campaign_worker(
            'followup', owner_token=claim['owner_token'], generation=claim['generation'],
            outcome='EXPECTED_EXIT', reason='policy_revision',
        )
        revision = repository.revise_campaign_policy(
            'followup', ResearchCampaignPolicy(
                auto_hypotheses=True,
                hypothesis_seed=19,
                max_hypotheses=10,
                max_generated_hypotheses_per_cycle=3,
                hypothesis_parameter_values={
                    BREAKOUT_FAMILY_ID: {'buffer_bps': (0, 10)},
                },
            ), expected_revision=1,
        )
        self.assertEqual(2, revision)
        repository.set_campaign_desired_state('followup', 'RUNNING')
        revised_claim = repository.claim_campaign_worker(
            'followup', owner_token='revised-worker', lease_seconds=60,
        )
        revised = generate_next_campaign_hypotheses(
            repository, 'followup', revised_claim,
        )
        self.assertEqual(('generated', 1), (revised['status'], revised['generated_count']))
        self.assertEqual(4, len(repository.load_campaign_registered_hypotheses('followup')))
        self.assertEqual(
            {1, 2},
            {row['policy_revision'] for row in repository.load_campaign_hypothesis_expansions('followup')},
        )


if __name__ == "__main__":
    unittest.main()
