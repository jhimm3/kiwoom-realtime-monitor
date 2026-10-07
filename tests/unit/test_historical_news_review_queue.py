from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from kiwoom_monitor.application.historical_learning_cases import HistoricalLearningCaseDataset
from kiwoom_monitor.application.historical_news_review_queue import (
    build_historical_news_review_queue,
    load_historical_news_review_queue,
    write_historical_news_review_queue,
)


class HistoricalNewsReviewQueueTests(unittest.TestCase):
    def test_deduplicates_article_and_keeps_stock_specific_rule_hints(self) -> None:
        dataset = build_historical_news_review_queue(
            _source_dataset(), created_at=datetime(2026, 9, 22, tzinfo=UTC),
        )
        self.assertEqual(1, len(dataset.items))
        item = dataset.items[0]
        self.assertEqual(2, len(item["case_links"]))
        hints = {link["stock"]["code"]: link["rule_hint"] for link in item["case_links"]}
        self.assertTrue(hints["005930"]["relevant"])
        self.assertFalse(hints["000660"]["relevant"])
        self.assertEqual("pending", item["review"]["status"])
        self.assertIsNone(item["review"]["human_decision"])
        self.assertFalse(item["eligibility"]["rule_hint_is_ground_truth"])
        self.assertFalse(item["eligibility"]["llm_used"])
        self.assertFalse(item["eligibility"]["human_review_complete"])
        self.assertFalse(item["eligibility"]["model_weight_training_ready"])
        self.assertEqual(1, dataset.manifest["counts"]["unique_articles"])
        self.assertEqual(2, dataset.manifest["counts"]["case_article_links"])

    def test_export_is_immutable_and_hash_verified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "queue"
            dataset = build_historical_news_review_queue(
                _source_dataset(), created_at=datetime(2026, 9, 22, tzinfo=UTC),
            )
            write_historical_news_review_queue(dataset, output)
            loaded = load_historical_news_review_queue(output)
            self.assertEqual(dataset.manifest["dataset_id"], loaded.manifest["dataset_id"])
            self.assertEqual(dataset.items, loaded.items)
            with self.assertRaisesRegex(ValueError, "immutable"):
                write_historical_news_review_queue(dataset, output)
            with (output / "review_items.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"ordinal": 2}) + "\n")
            with self.assertRaisesRegex(ValueError, "hash"):
                load_historical_news_review_queue(output)


def _source_dataset() -> HistoricalLearningCaseDataset:
    manifest = {
        "contract_version": "historical_learning_cases/v1",
        "dataset_id": "learning-source",
        "cases_file_hash": "learning-source-hash",
    }
    evidence = {
        "provider": "naver_historical_search",
        "office_id": "001",
        "article_id": "article-1",
        "office_name": "테스트신문",
        "title": "삼성전자, 반도체 투자 계획 공시",
        "search_summary": "삼성전자가 신규 설비투자를 결정했다고 밝혔다.",
        "published_at": "2024-01-02T10:00:00+09:00",
        "published_precision": "second",
        "published_at_source": "json_ld:datePublished",
        "article_url": "https://example.com/a",
        "original_url": "https://example.com/original",
        "collector_available_at": "2026-09-22T00:00:00+00:00",
        "source_revision_id": "news-1",
        "query_texts": ["삼성전자"],
    }
    return HistoricalLearningCaseDataset(manifest, (
        _case("case-samsung", "005930", "삼성전자", evidence),
        _case("case-hynix", "000660", "SK하이닉스", evidence),
    ))


def _case(case_id: str, code: str, name: str, evidence: dict[str, object]) -> dict[str, object]:
    return {
        "case_id": case_id,
        "selection_date": "2024-01-02",
        "stock": {"code": code, "name": name},
        "model_input": {"news_evidence": [dict(evidence)]},
    }


if __name__ == "__main__":
    unittest.main()