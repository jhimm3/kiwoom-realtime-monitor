from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from kiwoom_monitor.application.historical_research_split import (
    build_historical_evaluation_plan,
    load_historical_evaluation_plan,
    write_historical_evaluation_plan,
)
from kiwoom_monitor.application.market_session_schedule import research_session_profile_document
from kiwoom_monitor.application.research_evaluation import build_research_report
from kiwoom_monitor.application.research_splits import (
    DEVELOPMENT_PARTITION_VERSION,
    DevelopmentPartitionSpec,
    ResearchEvaluationSpec,
)
from kiwoom_monitor.infrastructure.research_data_source import (
    FrozenResearchDataset,
    load_frozen_research_export,
    prepare_development_partition,
)
from kiwoom_monitor.infrastructure.historical_reconstruction import (
    write_historical_research_input,
)


def _dataset() -> FrozenResearchDataset:
    dates = ("2024-01-02", "2025-01-02", "2026-01-02")
    observations = []
    for index, day in enumerate(dates, start=1):
        start = day + "T09:00:00+09:00"
        end = day + "T15:30:00+09:00"
        observations.extend((
            {"revision_id": f"population-{index}", "kind": "historical_candidate_population",
             "subject": day, "observation_key": day, "effective_at": start,
             "available_at": start, "payload": {"selection_date": day, "codes": ["005930"]}},
            {"revision_id": f"bar-{index}", "kind": "minute_bar", "subject": "005930:KRX",
             "venue": "KRX", "observation_key": day + "T15:29:00+09:00",
             "effective_at": end, "available_at": end, "completeness": "complete",
             "value_kind": "actual", "payload": {"historical_case_date": day,
                 "market": "KRX", "code": "005930", "bar_start": day + "T15:29:00+09:00",
                 "bar_end": end, "window_closed": True, "capture_quality": "complete"}},
        ))
    return FrozenResearchDataset({
        "runtime_input_version": "historical_reconstruction_strategy/v1",
        "dataset_id": "historical-strategy-test",
        "revision_ids_hash": "a" * 64,
        "population_id": "posthoc_candidate_days/v1",
        "not_contemporaneous_top20": True,
        "source_availability_preserved_in_payload": True,
        "research_session_profile": research_session_profile_document("krx-regular/v1"),
        "included_cases": [{"selection_date": day} for day in dates],
    }, tuple(observations))


class HistoricalResearchSplitTest(unittest.TestCase):
    def test_assigns_whole_cases_and_keeps_oos_sealed(self) -> None:
        plan = build_historical_evaluation_plan(
            _dataset(), train_cases=1, validation_cases=1,
        )
        self.assertEqual(["TRAIN", "VALIDATION", "OOS"], [
            row["role"] for row in plan["case_assignments"]
        ])
        self.assertEqual("SEALED", plan["oos_status"])
        self.assertFalse(plan["oos_results_included"])
        self.assertEqual(
            "2024-01-02T15:30:00.000001+09:00",
            plan["case_assignments"][0]["end_exclusive"],
        )

    def test_plan_round_trip_rejects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "split.json"
            plan = build_historical_evaluation_plan(
                _dataset(), train_cases=1, validation_cases=1,
            )
            write_historical_evaluation_plan(plan, output)
            self.assertEqual(plan, load_historical_evaluation_plan(output))
            changed = json.loads(output.read_text(encoding="utf-8"))
            changed["case_assignments"][0]["minute_bar_count"] = 999
            output.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "identity"):
                load_historical_evaluation_plan(output)

    def test_requires_an_oos_case(self) -> None:
        with self.assertRaisesRegex(ValueError, "sealed OOS"):
            build_historical_evaluation_plan(
                _dataset(), train_cases=2, validation_cases=1,
            )

    def test_development_projection_preserves_historical_population_provenance(self) -> None:
        source = _dataset()
        plan = build_historical_evaluation_plan(
            source, train_cases=1, validation_cases=1,
        )
        evaluation = ResearchEvaluationSpec.from_dict(plan["evaluation"])
        partition = DevelopmentPartitionSpec(
            DEVELOPMENT_PARTITION_VERSION, "historical_train",
        )
        selected = partition.evaluation_for(evaluation)
        projected = prepare_development_partition(source, partition, evaluation)

        self.assertEqual(
            "historical_reconstruction_strategy/v1",
            projected.manifest["source_runtime_input_version"],
        )
        self.assertTrue(projected.manifest["not_contemporaneous_top20"])
        self.assertEqual(
            "historical_candidate_population",
            projected.manifest["development_partition"]["universe_kind"],
        )
        report = build_research_report(
            "historical-train-test", input_manifest=projected.manifest,
            observations=projected.observations, candidate_events=(), decisions=(),
            execution_events=(), outcome_labels=(), initial_cash_won=1_000_000,
            cost_model={"rate_basis": "model_estimate", "source": "test",
                        "valid_from": "2024-01-01T00:00:00+09:00",
                        "valid_to": "2027-01-01T00:00:00+09:00"},
            spec=selected, logical_result_hash="a" * 64,
        )
        self.assertNotIn("candidate_universe_missing", report.data_quality["reasons"])
        self.assertIn(
            "posthoc_candidate_population_not_contemporaneous_top20", report.limitations,
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "train"
            write_historical_research_input(projected, output)
            verified = load_frozen_research_export(output)
        self.assertEqual(
            list(range(1, len(verified.observations) + 1)),
            [row["ordinal"] for row in verified.observations],
        )


if __name__ == "__main__":
    unittest.main()
