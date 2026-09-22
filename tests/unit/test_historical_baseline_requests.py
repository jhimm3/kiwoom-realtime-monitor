from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.prepare_historical_baseline_requests import prepare_requests


class HistoricalBaselineRequestTests(unittest.TestCase):
    def test_prepare_requests_creates_only_train_and_validation_with_rank_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "package"
            for name in ("train", "validation"):
                (package / name).mkdir(parents=True)
            evaluation = lambda name, role: {
                "version": "chronological_holdout/v1",
                "folds": [{
                    "name": name, "role": role,
                    "start": "2024-01-02T09:00:00+09:00",
                    "end": "2024-01-02T15:30:00.000001+09:00",
                }],
                "warmup_seconds": 0, "gap_seconds": 0, "purge_seconds": 0,
                "minimum_closed_trades": 0, "minimum_active_days": 1,
                "fold_state_policy": "continuous_state_and_cash/v1",
                "period_end_position_policy": "censor_open_position/v1",
                "fit_policy": "no_fitted_parameters/v1",
                "final_holdout_accessed_at": "", "final_holdout_access_reason": "",
            }
            (package / "manifest.json").write_text(json.dumps({
                "version": "historical_development_inputs/v1",
                "package_id": "package-1", "split_plan_id": "split-1",
                "oos_included": False,
                "partitions": [
                    {"role": "TRAIN", "path": "train", "evaluation": evaluation("train", "TRAIN")},
                    {"role": "VALIDATION", "path": "validation", "evaluation": evaluation("validation", "VALIDATION")},
                ],
            }), encoding="utf-8")

            paths = prepare_requests(package, root / "output")

            self.assertEqual(4, len(paths))
            documents = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
            self.assertEqual(
                {"TRAIN", "VALIDATION"},
                {item["historical_baseline_context"]["partition_role"] for item in documents},
            )
            self.assertTrue(all(item["historical_baseline_context"]["oos_included"] is False for item in documents))
            self.assertTrue(all(item["strategy"]["rank_persistence_enabled"] is False for item in documents))

    def test_prepare_requests_rejects_package_that_includes_oos(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "package"
            package.mkdir()
            (package / "manifest.json").write_text(json.dumps({
                "version": "historical_development_inputs/v1",
                "oos_included": True,
            }), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "OOS-free"):
                prepare_requests(package, Path(directory) / "output")


if __name__ == "__main__":
    unittest.main()
