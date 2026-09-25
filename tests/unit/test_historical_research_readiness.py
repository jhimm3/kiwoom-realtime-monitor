from __future__ import annotations

import unittest

from kiwoom_monitor.infrastructure.historical_research_readiness import (
    assess_historical_development_readiness,
)
from kiwoom_monitor.infrastructure.research_data_source import FrozenResearchDataset


def _population() -> dict:
    return {
        "kind": "historical_candidate_population", "accepted_sequence": 1,
        "revision_id": "population", "available_at": "2024-01-02T09:00:00+09:00",
        "payload": {"codes": ["005930", "000660"]},
    }


def _bar(code: str, minute: int, sequence: int) -> dict:
    return {
        "kind": "minute_bar", "venue": "KRX", "accepted_sequence": sequence,
        "revision_id": f"{code}-{minute}",
        "available_at": f"2024-01-02T09:{minute + 1:02d}:00+09:00",
        "payload": {
            "code": code,
            "bar_start": f"2024-01-02T09:{minute:02d}:00+09:00",
        },
    }


def _dataset(*rows: dict) -> FrozenResearchDataset:
    return FrozenResearchDataset({
        "runtime_input_version": "independent_development_input/v1",
        "development_partition": {
            "universe_kind": "historical_candidate_population",
            "spec": {"fold_name": "train"},
            "evaluation": {"folds": [{"role": "TRAIN"}]},
        },
    }, (_population(), *rows))


class HistoricalResearchReadinessTests(unittest.TestCase):
    def test_blocks_when_a_candidate_has_no_bars_or_continuous_pair(self) -> None:
        report = assess_historical_development_readiness(
            _dataset(_bar("005930", 0, 2), _bar("005930", 1, 3)),
        )

        self.assertEqual("BLOCKED", report.status)
        self.assertEqual(2, report.candidate_code_count)
        self.assertEqual(("000660",), report.missing_bar_codes)
        self.assertEqual(("000660",), report.missing_continuous_pair_codes)

    def test_ready_requires_each_candidate_to_have_a_continuous_minute_pair(self) -> None:
        report = assess_historical_development_readiness(_dataset(
            _bar("005930", 0, 2), _bar("005930", 1, 3),
            _bar("000660", 5, 4), _bar("000660", 6, 5),
        ))

        self.assertEqual("READY", report.status)
        self.assertEqual(1_000_000, report.bar_coverage_ppm)
        self.assertEqual(1_000_000, report.continuous_pair_coverage_ppm)
        self.assertEqual((), report.reasons)

    def test_multiple_cases_check_the_same_stock_on_each_outcome_day(self) -> None:
        first = _population()
        first["payload"]["selection_date"] = "2024-01-01"
        first["payload"]["codes"] = ["005930"]
        second = _population()
        second["accepted_sequence"] = 4
        second["available_at"] = "2024-02-02T09:00:00+09:00"
        second["payload"]["selection_date"] = "2024-02-01"
        second["payload"]["codes"] = ["005930"]
        first_bars = [_bar("005930", minute, minute + 2) for minute in (0, 1)]
        for bar in first_bars:
            bar["payload"]["historical_case_date"] = "2024-01-01"
        second_bar = _bar("005930", 0, 5)
        second_bar["available_at"] = "2024-02-02T09:01:00+09:00"
        second_bar["payload"]["bar_start"] = "2024-02-02T09:00:00+09:00"
        second_bar["payload"]["historical_case_date"] = "2024-02-01"
        dataset = FrozenResearchDataset(_dataset().manifest, (first, *first_bars, second, second_bar))

        report = assess_historical_development_readiness(dataset)

        self.assertEqual("BLOCKED", report.status)
        self.assertEqual(2, report.candidate_code_count)
        self.assertEqual(2, report.codes_with_bars)
        self.assertEqual(1, report.codes_with_continuous_minute_pair)
        self.assertEqual(("2024-02-01:005930",), report.missing_continuous_pair_codes)

    def test_prior_partition_universe_seed_is_not_a_development_case(self) -> None:
        seed = _population()
        seed["payload"]["selection_date"] = "2024-01-01"
        current = _population()
        current["revision_id"] = "current"
        current["payload"]["selection_date"] = "2024-02-01"
        current["payload"]["codes"] = ["005930"]
        bars = [_bar("005930", minute, minute + 2) for minute in (0, 1)]
        for bar in bars:
            bar["payload"]["historical_case_date"] = "2024-02-01"
        descriptor = dict(_dataset().manifest["development_partition"])
        descriptor["universe_seed"] = {"revision_id": "population"}
        dataset = FrozenResearchDataset({
            "runtime_input_version": "independent_development_input/v1",
            "development_partition": descriptor,
        }, (seed, current, *bars))

        report = assess_historical_development_readiness(dataset)

        self.assertEqual("READY", report.status)
        self.assertEqual(1, report.candidate_code_count)


if __name__ == "__main__":
    unittest.main()
