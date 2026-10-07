from __future__ import annotations

import unittest
from dataclasses import replace

from kiwoom_monitor.application.breakout_strategy import default_shadow_breakout_config
from kiwoom_monitor.application.pullback_reacceleration_strategy import (
    FAMILY_ID as PULLBACK_FAMILY_ID,
    PullbackReaccelerationConfig,
)
from kiwoom_monitor.application.research_families import BREAKOUT_FAMILY_ID
from kiwoom_monitor.application.research_evaluation import (
    DevelopmentEvidence,
    build_development_evidence,
)
from kiwoom_monitor.application.research_hypotheses import (
    DevelopmentEvidenceRef,
    FollowupHypothesisGenerationRequest,
    HypothesisGenerationRequest,
    ResearchHypothesis,
    build_development_evidence_snapshot,
    generate_followup_hypotheses,
    generate_research_hypotheses,
)


def _request(**changes):
    values = {
        "research_scope_id": "campaign:test-campaign",
        "family_id": BREAKOUT_FAMILY_ID,
        "factor_allowlist": ("rolling_high_breakout/v1", "rank_persistence/v1"),
        "baseline_parameters": default_shadow_breakout_config().to_dict(),
        "allowed_parameter_values": {
            "lookback_bars": (3, 5, 7),
            "buffer_bps": (0, 10),
            "target_bps": (400, 500, 600),
        },
        "development_evidence_refs": (DevelopmentEvidenceRef("development-run-1"),),
        "seed": 17,
        "max_variants": 20,
    }
    values.update(changes)
    return HypothesisGenerationRequest(**values)


class ResearchHypothesisTests(unittest.TestCase):
    def test_generation_creates_one_baseline_and_only_single_parameter_variants(self) -> None:
        hypotheses = generate_research_hypotheses(_request())

        self.assertEqual(6, len(hypotheses))
        root = hypotheses[0]
        self.assertEqual("BASELINE", root.kind)
        self.assertEqual((), root.parent_ids)
        for hypothesis in hypotheses[1:]:
            self.assertEqual("ONE_PARAMETER_VARIANT", hypothesis.kind)
            self.assertEqual((root.hypothesis_id,), hypothesis.parent_ids)
            changed = [
                key for key, value in hypothesis.parameters.items()
                if root.parameters[key] != value
            ]
            self.assertEqual([hypothesis.changed_parameter], changed)

    def test_same_request_is_content_and_order_deterministic(self) -> None:
        first = generate_research_hypotheses(_request())
        second = generate_research_hypotheses(_request())
        self.assertEqual(first, second)

    def test_second_registered_family_uses_the_same_generation_contract(self) -> None:
        baseline = PullbackReaccelerationConfig(
            strategy_version="v1", pullback_factor_version="v1", rank_factor_version="v1",
            lookback_bars=3, minimum_pullback_bps=500, minimum_reacceleration_bps=100,
            rank_persistence_enabled=False, rank_persistence_required=False,
            rank_top_k=None, rank_window_seconds=None, rank_max_gap_seconds=None,
            rank_min_residency_seconds=None, stop_loss_bps=300, target_bps=500,
            max_hold_minutes=10, quantity=1, capital_won=1_000_000,
            signal_valid_seconds=60, cooldown_seconds=30,
        )
        result = generate_research_hypotheses(HypothesisGenerationRequest(
            research_scope_id="campaign:test-campaign",
            family_id=PULLBACK_FAMILY_ID,
            factor_allowlist=("pullback_reacceleration/v1", "rank_persistence/v1"),
            baseline_parameters=baseline.to_dict(),
            allowed_parameter_values={"minimum_pullback_bps": (300, 500, 700)},
            development_evidence_refs=(DevelopmentEvidenceRef("development-pullback-1"),),
            seed=3,
            max_variants=10,
        ))
        self.assertEqual(3, len(result))
        self.assertEqual(
            {300, 700},
            {item.changed_to for item in result[1:]},
        )
        self.assertTrue(all(item.family_id == PULLBACK_FAMILY_ID for item in result))

    def test_seed_changes_order_but_not_the_hypothesis_identity_set(self) -> None:
        first = generate_research_hypotheses(_request(seed=1))
        second = generate_research_hypotheses(_request(seed=2))
        self.assertNotEqual(
            [item.hypothesis_id for item in first[1:]],
            [item.hypothesis_id for item in second[1:]],
        )
        self.assertEqual(
            {item.hypothesis_id for item in first},
            {item.hypothesis_id for item in second},
        )

    def test_duplicate_and_baseline_values_do_not_create_duplicate_variants(self) -> None:
        result = generate_research_hypotheses(_request(
            allowed_parameter_values={"lookback_bars": (5, 7, 7, 5)},
        ))
        self.assertEqual(2, len(result))
        self.assertEqual(7, result[1].changed_to)

    def test_max_variants_is_a_hard_bound_but_baseline_is_always_present(self) -> None:
        self.assertEqual(1, len(generate_research_hypotheses(_request(max_variants=0))))
        self.assertEqual(3, len(generate_research_hypotheses(_request(max_variants=2))))

    def test_unregistered_or_invalid_allowed_value_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "factor"):
            _request(factor_allowlist=("future_factor/v1",))
        with self.assertRaisesRegex(ValueError, "unregistered parameter"):
            _request(allowed_parameter_values={"quantity": (2,)})
        with self.assertRaises(ValueError):
            _request(allowed_parameter_values={"lookback_bars": (0,)})
        with self.assertRaisesRegex(ValueError, "integers"):
            _request(allowed_parameter_values={"lookback_bars": (True,)})
        with self.assertRaisesRegex(ValueError, "DEVELOPMENT"):
            DevelopmentEvidenceRef("final-run-1", scope="FINAL")

    def test_round_trip_revalidates_content_id_and_exact_fields(self) -> None:
        original = generate_research_hypotheses(_request(max_variants=1))[1]
        self.assertEqual(original, ResearchHypothesis.from_dict(original.to_dict()))
        tampered = original.to_dict()
        tampered["changed_to"] = int(tampered["changed_to"]) + 1
        with self.assertRaises(ValueError):
            ResearchHypothesis.from_dict(tampered)
        extra = original.to_dict()
        extra["future"] = True
        with self.assertRaisesRegex(ValueError, "fields"):
            ResearchHypothesis.from_dict(extra)

    def test_status_and_parent_cannot_be_tampered_after_generation(self) -> None:
        original = generate_research_hypotheses(_request(max_variants=1))[1]
        with self.assertRaises(ValueError):
            replace(original, status="PROMOTED")
        with self.assertRaises(ValueError):
            replace(original, parent_ids=(original.hypothesis_id,))

    def test_completed_parent_generates_only_direct_single_parameter_children(self) -> None:
        parent = generate_research_hypotheses(_request(max_variants=0))[0]
        snapshot = build_development_evidence_snapshot(
            'run-development-1',
            DevelopmentEvidence(
                status='INELIGIBLE', reasons=('minimum_closed_trades_not_met',),
                fold_refs=(('train', 'TRAIN'), ('validation', 'VALIDATION')),
                net_pnl_won=-10, max_drawdown_won=50,
                closed_trade_count=1, active_day_count=1,
            ),
        )
        request = FollowupHypothesisGenerationRequest(
            parent=parent,
            allowed_parameter_values={
                'lookback_bars': (3, 5, 7),
                'target_bps': (400, 500, 600),
            },
            development_evidence_refs=(DevelopmentEvidenceRef(snapshot.evidence_id),),
            seed=11,
            max_variants=10,
        )
        first = generate_followup_hypotheses(request)
        second = generate_followup_hypotheses(request)
        self.assertEqual(first, second)
        self.assertEqual(4, len(first))
        self.assertTrue(all(item.parent_ids == (parent.hypothesis_id,) for item in first))
        self.assertTrue(all(item.evidence_refs == (snapshot.evidence_id,) for item in first))
        self.assertTrue(all(item.baseline_parameters == parent.parameters for item in first))

    def test_development_evidence_snapshot_is_content_addressed_and_excludes_raw_report(self) -> None:
        evidence = DevelopmentEvidence(
            status='ELIGIBLE', reasons=(), fold_refs=(('validation', 'VALIDATION'),),
            net_pnl_won=100, max_drawdown_won=20,
            closed_trade_count=4, active_day_count=3,
        )
        first = build_development_evidence_snapshot('run-1', evidence)
        second = build_development_evidence_snapshot('run-1', evidence)
        self.assertEqual(first, second)
        self.assertNotIn('fold_reports', first.to_dict())
        self.assertNotIn('final', str(first.to_dict()).lower())
        with self.assertRaisesRegex(ValueError, 'immutable content'):
            replace(first, source_run_id='run-2')

    def test_final_fold_changes_cannot_change_followup_evidence_identity(self) -> None:
        development = {
            'name': 'validation', 'role': 'VALIDATION', 'status': 'ELIGIBLE',
            'reasons': [], 'net_realized_pnl_won': 100, 'max_drawdown_won': 20,
            'closed_trade_count': 4, 'active_day_count': 3,
        }
        first = build_development_evidence_snapshot(
            'run-1', build_development_evidence({'fold_reports': [
                development,
                {'name': 'final', 'role': 'FINAL', 'status': 'ELIGIBLE',
                 'net_realized_pnl_won': 1, 'max_drawdown_won': 1},
            ]}),
        )
        second = build_development_evidence_snapshot(
            'run-1', build_development_evidence({'fold_reports': [
                development,
                {'name': 'final', 'role': 'FINAL', 'status': 'INELIGIBLE',
                 'net_realized_pnl_won': -999_999, 'max_drawdown_won': 999_999},
            ]}),
        )
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
