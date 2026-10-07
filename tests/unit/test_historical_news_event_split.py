from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from kiwoom_monitor.application.historical_news_event_split import (
    build_historical_news_event_split,
    load_historical_news_event_split,
    write_historical_news_event_split,
)
from kiwoom_monitor.application.historical_news_review_decisions import (
    HistoricalNewsReviewDecisions,
)


class HistoricalNewsEventSplitTests(unittest.TestCase):
    def test_keeps_whole_events_in_chronological_partitions(self) -> None:
        plan = build_historical_news_event_split(
            _source(), train_events=1, validation_events=1,
        )

        self.assertEqual(["TRAIN", "VALIDATION", "OOS"], [
            row["role"] for row in plan["partitions"]
        ])
        self.assertEqual(2, plan["partitions"][0]["article_count"])
        self.assertEqual(["event-a"], plan["partitions"][0]["event_ids"])
        self.assertEqual(["event-b"], plan["partitions"][1]["event_ids"])
        self.assertEqual(["event-c"], plan["partitions"][2]["event_ids"])
        self.assertEqual("SEALED", plan["boundaries"]["oos_status"])
        self.assertFalse(plan["boundaries"]["oos_review_payload_included"])
        self.assertTrue(plan["boundaries"]["partial_review_source"])
        self.assertFalse(plan["boundaries"]["model_weight_training_ready"])
        self.assertEqual(1, plan["counts"]["not_relevant_excluded"])
        self.assertEqual(1, plan["counts"]["uncertain_excluded"])

    def test_round_trip_rejects_tampered_event_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "event-split.json"
            plan = build_historical_news_event_split(
                _source(), train_events=1, validation_events=1,
            )
            write_historical_news_event_split(plan, output)
            self.assertEqual(plan, load_historical_news_event_split(output))

            changed = json.loads(output.read_text(encoding="utf-8"))
            changed["event_assignments"][0]["article_count"] = 99
            output.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "assignment|identity"):
                load_historical_news_event_split(output)

    def test_requires_publication_evidence_and_sealed_oos_event(self) -> None:
        source = _source()
        without_time = [dict(row) for row in source.decisions]
        without_time[0].pop("article_evidence")
        with self.assertRaisesRegex(ValueError, "publication evidence"):
            build_historical_news_event_split(
                HistoricalNewsReviewDecisions(source.manifest, tuple(without_time)),
                train_events=1,
                validation_events=1,
            )
        with self.assertRaisesRegex(ValueError, "sealed OOS"):
            build_historical_news_event_split(
                source, train_events=2, validation_events=1,
            )


def _source() -> HistoricalNewsReviewDecisions:
    decisions = (
        _decision(1, "event-a", "2024-01-02T23:00:00+09:00", "005930"),
        _decision(2, "event-a", "2024-01-02T23:30:00+09:00", "000660"),
        _decision(3, "event-b", "2024-01-02T15:00:00+00:00", "035420"),
        _decision(4, "event-c", "2024-03-01T09:00:00+09:00", "035720"),
        _decision(5, "", "2024-04-01T09:00:00+09:00", "051910", "not_relevant"),
        _decision(6, "", "2024-05-01T09:00:00+09:00", "006400", "uncertain"),
    )
    manifest = {
        "contract_version": "historical_news_review_decisions/v1",
        "dataset_id": "review-decisions-test",
        "decisions_file_hash": "a" * 64,
        "decision_count": len(decisions),
        "boundaries": {
            "included_decisions_human_reviewed": True,
            "source_queue_review_complete": False,
            "model_weight_training_ready": False,
        },
    }
    return HistoricalNewsReviewDecisions(manifest, decisions)


def _decision(
    ordinal: int,
    event_id: str,
    published_at: str,
    stock_code: str,
    value: str = "relevant",
) -> dict[str, object]:
    return {
        "ordinal": ordinal,
        "decision_id": f"decision-{ordinal}",
        "source_queue_dataset_id": "queue-test",
        "source_queue_ordinal": ordinal,
        "review_item_id": f"review-{ordinal}",
        "article_identity": {
            "provider": "naver_historical_search",
            "office_id": "001",
            "article_id": f"article-{ordinal}",
        },
        "article_evidence": {
            "title": f"기사 {ordinal}",
            "published_at": published_at,
            "published_precision": "second",
            "published_at_source": "json_ld:datePublished",
        },
        "case_links": [{
            "case_id": f"case-{ordinal}",
            "selection_date": published_at[:10],
            "stock": {"code": stock_code, "name": f"종목 {ordinal}"},
        }],
        "human_review": {
            "decision": value,
            "canonical_event_id": event_id,
            "theme_profile_name": "기본 테마" if value == "relevant" else "",
            "theme_names": ["테스트"] if value == "relevant" else [],
            "notes": "",
            "reviewer": "tester",
            "reviewed_at": "2026-09-23T00:00:00+00:00",
        },
        "eligibility": {
            "human_review_complete": True,
            "llm_used_for_decision": False,
            "event_split_ready": value != "relevant" or bool(event_id),
            "model_weight_training_ready": False,
        },
    }


if __name__ == "__main__":
    unittest.main()
