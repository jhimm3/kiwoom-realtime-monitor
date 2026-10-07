from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kiwoom_monitor.application.historical_news_development_inputs import (
    build_historical_news_development_inputs,
    load_historical_news_development_inputs,
    write_historical_news_development_inputs,
)
from kiwoom_monitor.application.historical_news_event_split import (
    build_historical_news_event_split,
)
from kiwoom_monitor.application.historical_news_review_decisions import (
    HistoricalNewsReviewDecisions,
)


class HistoricalNewsDevelopmentInputsTests(unittest.TestCase):
    def test_projects_train_validation_and_omits_oos_payload(self) -> None:
        decisions = _decisions()
        split = build_historical_news_event_split(
            decisions, train_events=1, validation_events=1,
        )

        dataset = build_historical_news_development_inputs(decisions, split)

        self.assertEqual(2, len(dataset.train))
        self.assertEqual(1, len(dataset.validation))
        self.assertEqual({"event-a"}, {row["canonical_event_id"] for row in dataset.train})
        self.assertEqual({"event-b"}, {row["canonical_event_id"] for row in dataset.validation})
        self.assertNotIn("decision-4", str(dataset.train) + str(dataset.validation))
        self.assertFalse(dataset.manifest["boundaries"]["oos_included"])
        self.assertFalse(dataset.manifest["boundaries"]["oos_payload_included"])
        self.assertFalse(dataset.manifest["boundaries"]["model_weight_training_ready"])
        self.assertEqual(1, dataset.manifest["counts"]["oos_articles_omitted"])
        self.assertEqual("검색 요약 1", dataset.train[0]["model_input"]["search_summary"])

    def test_round_trip_verifies_hash_and_source_binding(self) -> None:
        decisions = _decisions()
        split = build_historical_news_event_split(
            decisions, train_events=1, validation_events=1,
        )
        dataset = build_historical_news_development_inputs(decisions, split)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "development"
            write_historical_news_development_inputs(dataset, output)
            loaded = load_historical_news_development_inputs(output)
            self.assertEqual(dataset, loaded)
            with (output / "train.jsonl").open("a", encoding="utf-8") as stream:
                stream.write("{}\n")
            with self.assertRaisesRegex(ValueError, "hash"):
                load_historical_news_development_inputs(output)

        changed = dict(split)
        changed["source"] = {**split["source"], "dataset_id": "different"}
        with self.assertRaisesRegex(ValueError, "does not match"):
            build_historical_news_development_inputs(decisions, changed)


def _decisions() -> HistoricalNewsReviewDecisions:
    values = (
        _decision(1, "event-a", "2024-01-02T09:00:00+09:00"),
        _decision(2, "event-a", "2024-01-02T10:00:00+09:00"),
        _decision(3, "event-b", "2024-02-02T09:00:00+09:00"),
        _decision(4, "event-c", "2024-03-02T09:00:00+09:00"),
        _decision(5, "", "2024-04-02T09:00:00+09:00", "not_relevant"),
    )
    return HistoricalNewsReviewDecisions({
        "contract_version": "historical_news_review_decisions/v1",
        "dataset_id": "decisions-test",
        "decisions_file_hash": "b" * 64,
        "decision_count": len(values),
        "boundaries": {
            "included_decisions_human_reviewed": True,
            "source_queue_review_complete": False,
            "model_weight_training_ready": False,
        },
    }, values)


def _decision(
    ordinal: int, event_id: str, published_at: str, value: str = "relevant",
) -> dict[str, object]:
    return {
        "ordinal": ordinal,
        "decision_id": f"decision-{ordinal}",
        "source_queue_ordinal": ordinal,
        "review_item_id": f"review-{ordinal}",
        "article_identity": {
            "provider": "naver_historical_search",
            "office_id": "001",
            "article_id": f"article-{ordinal}",
        },
        "article_evidence": {
            "title": f"검토 기사 {ordinal}",
            "search_summary": f"검색 요약 {ordinal}",
            "published_at": published_at,
            "published_precision": "second",
            "published_at_source": "json_ld:datePublished",
        },
        "case_links": [{
            "case_id": f"case-{ordinal}",
            "selection_date": published_at[:10],
            "stock": {"code": f"00{ordinal:04d}", "name": f"종목 {ordinal}"},
        }],
        "human_review": {
            "decision": value,
            "canonical_event_id": event_id,
            "theme_profile_name": "기본 테마" if value == "relevant" else "",
            "theme_names": ["테스트 테마"] if value == "relevant" else [],
        },
        "eligibility": {
            "human_review_complete": True,
            "model_weight_training_ready": False,
        },
    }


if __name__ == "__main__":
    unittest.main()
