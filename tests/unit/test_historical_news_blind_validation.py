from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kiwoom_monitor.application.historical_news_blind_validation import (
    HistoricalNewsBlindValidation,
    build_historical_news_blind_validation,
    load_historical_news_blind_validation,
    write_historical_news_blind_validation,
)
from kiwoom_monitor.application.historical_news_development_inputs import (
    build_historical_news_development_inputs,
)
from kiwoom_monitor.application.historical_news_event_split import (
    build_historical_news_event_split,
)
from kiwoom_monitor.application.historical_news_review_decisions import (
    HistoricalNewsReviewDecisions,
)


class HistoricalNewsBlindValidationTests(unittest.TestCase):
    def test_projects_only_validation_inputs_without_targets(self) -> None:
        source = _development_inputs()

        dataset = build_historical_news_blind_validation(source)

        self.assertEqual(1, len(dataset.requests))
        self.assertEqual(source.validation[0]["sample_id"], dataset.requests[0]["sample_id"])
        self.assertEqual(source.validation[0]["model_input"], dataset.requests[0]["model_input"])
        encoded = str(dataset.requests)
        self.assertNotIn("human_target", encoded)
        self.assertNotIn("canonical_event_id", encoded)
        self.assertNotIn("theme_names", encoded)
        self.assertFalse(dataset.manifest["boundaries"]["human_target_included"])
        self.assertFalse(dataset.manifest["boundaries"]["oos_included"])
        self.assertFalse(dataset.manifest["boundaries"]["training_allowed"])

    def test_round_trip_detects_tampering_and_target_leak(self) -> None:
        dataset = build_historical_news_blind_validation(_development_inputs())
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "blind"
            write_historical_news_blind_validation(dataset, output)
            self.assertEqual(dataset, load_historical_news_blind_validation(output))
            with (output / "requests.jsonl").open("a", encoding="utf-8") as stream:
                stream.write("{}\n")
            with self.assertRaisesRegex(ValueError, "hash"):
                load_historical_news_blind_validation(output)

        leaked_row = {
            **dataset.requests[0],
            "model_input": {
                **dataset.requests[0]["model_input"],
                "theme_names": ["정답 누수"],
            },
        }
        leaked = HistoricalNewsBlindValidation(dataset.manifest, (leaked_row,))
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "leaks target data"):
                write_historical_news_blind_validation(leaked, Path(directory) / "leaked")


def _development_inputs():
    decisions = HistoricalNewsReviewDecisions(
        {
            "contract_version": "historical_news_review_decisions/v1",
            "dataset_id": "decisions-blind-test",
            "decisions_file_hash": "c" * 64,
            "decision_count": 3,
            "boundaries": {
                "included_decisions_human_reviewed": True,
                "source_queue_review_complete": True,
                "model_weight_training_ready": False,
            },
        },
        tuple(_decision(index) for index in range(1, 4)),
    )
    split = build_historical_news_event_split(
        decisions, train_events=1, validation_events=1,
    )
    return build_historical_news_development_inputs(decisions, split)


def _decision(index: int) -> dict[str, object]:
    published_at = f"2024-0{index}-02T09:00:00+09:00"
    return {
        "ordinal": index,
        "decision_id": f"decision-{index}",
        "source_queue_ordinal": index,
        "review_item_id": f"review-{index}",
        "article_identity": {
            "provider": "naver_historical_search",
            "office_id": "001",
            "article_id": f"article-{index}",
        },
        "article_evidence": {
            "title": f"검토 기사 {index}",
            "search_summary": f"검색 요약 {index}",
            "published_at": published_at,
            "published_precision": "second",
            "published_at_source": "json_ld:datePublished",
        },
        "case_links": [{
            "case_id": f"case-{index}",
            "selection_date": published_at[:10],
            "stock": {"code": f"00{index:04d}", "name": f"종목 {index}"},
        }],
        "human_review": {
            "decision": "relevant",
            "canonical_event_id": f"event-{index}",
            "theme_profile_name": "기본 테마",
            "theme_names": ["테스트 테마"],
        },
        "eligibility": {
            "human_review_complete": True,
            "model_weight_training_ready": False,
        },
    }


if __name__ == "__main__":
    unittest.main()
