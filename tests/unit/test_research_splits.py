from __future__ import annotations

import unittest

from kiwoom_monitor.application.research_splits import (
    ResearchEvaluationSpec,
    ResearchFoldSpec,
    assign_interval_to_fold,
)


def _spec(**overrides) -> ResearchEvaluationSpec:
    values = {
        "version": "chronological_holdout/v1",
        "folds": (
            ResearchFoldSpec("train", "TRAIN", "2026-09-10T00:00:00+00:00", "2026-09-11T00:00:00+00:00"),
            ResearchFoldSpec("validation", "VALIDATION", "2026-09-11T01:00:00+00:00", "2026-09-12T00:00:00+00:00"),
            ResearchFoldSpec("final", "OOS", "2026-09-12T01:00:00+00:00", "2026-09-13T00:00:00+00:00"),
        ),
        "warmup_seconds": 600,
        "gap_seconds": 3600,
        "purge_seconds": 300,
        "minimum_closed_trades": 1,
        "minimum_active_days": 1,
    }
    values.update(overrides)
    return ResearchEvaluationSpec(**values)


class ResearchSplitTests(unittest.TestCase):
    def test_folds_must_be_chronological_and_respect_gap(self) -> None:
        with self.assertRaisesRegex(ValueError, "configured gap"):
            _spec(gap_seconds=3601)
        with self.assertRaisesRegex(ValueError, "TRAIN, VALIDATION, OOS"):
            ResearchEvaluationSpec(
                "chronological_holdout/v1",
                (
                    ResearchFoldSpec("oos", "OOS", "2026-09-10T00:00:00+00:00", "2026-09-11T00:00:00+00:00"),
                    ResearchFoldSpec("train", "TRAIN", "2026-09-12T00:00:00+00:00", "2026-09-13T00:00:00+00:00"),
                ),
                0, 0, 0, 0, 0,
            )

    def test_result_crossing_purge_boundary_is_not_counted(self) -> None:
        spec = _spec()
        included = assign_interval_to_fold(
            spec, "2026-09-10T23:00:00+00:00", "2026-09-10T23:54:59+00:00",
        )
        purged = assign_interval_to_fold(
            spec, "2026-09-10T23:00:00+00:00", "2026-09-10T23:55:00+00:00",
        )
        self.assertEqual("INCLUDED", included.status)
        self.assertEqual("PURGED", purged.status)

    def test_holdout_access_is_auditable(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires a reason"):
            _spec(final_holdout_accessed_at="2026-09-13T01:00:00+00:00")
        accessed = _spec(
            final_holdout_accessed_at="2026-09-13T01:00:00+00:00",
            final_holdout_access_reason="first locked evaluation",
        )
        restored = ResearchEvaluationSpec.from_dict(accessed.to_dict())
        self.assertEqual(accessed, restored)


if __name__ == "__main__":
    unittest.main()
