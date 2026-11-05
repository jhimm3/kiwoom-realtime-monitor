from __future__ import annotations

import unittest

from kiwoom_monitor.application.research_splits import (
    ResearchEvaluationSpec,
    ResearchFoldSpec,
    assign_interval_to_fold,
    validate_source_event_folds,
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

    def test_source_event_links_use_frozen_availability_and_do_not_split_reviewed_occurrences(self):
        rows = (
            {"revision_id": "signal", "available_at": "2026-09-10T20:00:00+09:00"},
            {"revision_id": "follow-up", "available_at": "2026-09-10T22:00:00+00:00"},
            {"revision_id": "validation", "available_at": "2026-09-11T01:00:00+00:00"},
            {"revision_id": "purged", "available_at": "2026-09-10T23:55:00+00:00"},
        )
        first = {"event_id": "review:case-1", "source_occurrence_refs": ["source:01:case-04"],
                 "revision_ids": ["signal"]}
        second = {"event_id": first["event_id"], "source_occurrence_refs": ["source:02:case-01"],
                  "revision_ids": ["follow-up"]}
        document = _spec().to_dict()
        self.assertNotIn("source_event_links", document)
        document["folds"] = document["folds"][:2]

        def check(*links):
            spec = ResearchEvaluationSpec.from_dict({**document, "source_event_links": list(links)})
            validate_source_event_folds(spec, rows)
            return spec

        spec = check(first, second)
        self.assertEqual([first, second], spec.to_dict()["source_event_links"])
        for revision in ("validation", "purged"):
            with self.subTest(revision=revision), self.assertRaisesRegex(ValueError, "eligible evaluation fold"):
                check(first, {**second, "revision_ids": [revision]})
        with self.assertRaisesRegex(ValueError, "absent from the frozen input"):
            check({**first, "revision_ids": ["missing"]})
        with self.assertRaisesRegex(ValueError, "source occurrence belongs to multiple"):
            check(first, {**second, "event_id": "review:case-2", "source_occurrence_refs": first["source_occurrence_refs"]})
        with self.assertRaisesRegex(ValueError, "frozen revision belongs to multiple"):
            check(first, {**second, "event_id": "review:case-2", "revision_ids": ["signal"]})

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
