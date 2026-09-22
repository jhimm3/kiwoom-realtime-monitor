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
from kiwoom_monitor.infrastructure.research_data_source import FrozenResearchDataset


def _dataset() -> FrozenResearchDataset:
    dates = ("2024-01-02", "2025-01-02", "2026-01-02")
    observations = []
    for index, day in enumerate(dates, start=1):
        start = day + "T09:00:00+09:00"
        end = day + "T15:30:00+09:00"
        observations.extend((
            {"kind": "historical_candidate_population", "available_at": start,
             "payload": {"selection_date": day}},
            {"kind": "minute_bar", "available_at": end,
             "payload": {"historical_case_date": day}},
        ))
    return FrozenResearchDataset({
        "runtime_input_version": "historical_reconstruction_strategy/v1",
        "dataset_id": "historical-strategy-test",
        "revision_ids_hash": "a" * 64,
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


if __name__ == "__main__":
    unittest.main()
