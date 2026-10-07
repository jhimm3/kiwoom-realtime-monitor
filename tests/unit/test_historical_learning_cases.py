from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from kiwoom_monitor.application.historical_learning_cases import (
    build_historical_learning_cases,
    load_historical_learning_cases,
    write_historical_learning_cases,
)
from kiwoom_monitor.infrastructure.historical_reconstruction import HistoricalReconstructionDataset


class HistoricalLearningCaseTests(unittest.TestCase):
    def test_separates_reconstructed_news_interpretation_and_future_outcome(self) -> None:
        source = _source_dataset()

        dataset = build_historical_learning_cases(
            source, created_at=datetime(2026, 9, 22, tzinfo=UTC),
        )

        self.assertEqual(1, len(dataset.cases))
        case = dataset.cases[0]
        self.assertTrue(case["sample_selection"]["not_contemporaneous_top20"])
        self.assertFalse(case["model_input"]["strict_point_in_time_available"])
        self.assertEqual("not_generated", case["interpretation"]["status"])
        self.assertEqual(1, len(case["model_input"]["news_evidence"]))
        evidence = case["model_input"]["news_evidence"][0]
        self.assertEqual(["삼성전자", "삼성전자 반도체"], evidence["query_texts"])
        self.assertEqual(1, case["model_input"]["excluded_relation_counts"]["duplicate_article_relation"])
        self.assertEqual(1, case["model_input"]["excluded_relation_counts"]["published_after_selection_date"])
        outcome = case["outcome_label"]
        self.assertEqual("observed", outcome["status"])
        self.assertEqual(60, outcome["resolution_seconds"])
        self.assertEqual(2, outcome["bar_count"])
        self.assertEqual(10.0, outcome["close_return_pct"])
        self.assertEqual(12.0, outcome["max_up_pct"])
        self.assertEqual(-2.0, outcome["max_down_pct"])
        self.assertFalse(outcome["label_is_model_input"])
        self.assertFalse(case["eligibility"]["semantic_relevance_reviewed"])
        self.assertFalse(case["eligibility"]["strict_point_in_time_case"])
        self.assertFalse(case["eligibility"]["model_weight_training_ready"])

    def test_export_is_immutable_and_hash_verified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "cases"
            dataset = build_historical_learning_cases(
                _source_dataset(), created_at=datetime(2026, 9, 22, tzinfo=UTC),
            )
            write_historical_learning_cases(dataset, output)

            loaded = load_historical_learning_cases(output)

            self.assertEqual(dataset.manifest["dataset_id"], loaded.manifest["dataset_id"])
            self.assertEqual(dataset.cases, loaded.cases)
            with self.assertRaisesRegex(ValueError, "immutable"):
                write_historical_learning_cases(dataset, output)
            with (output / "cases.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"ordinal": 2}) + "\n")
            with self.assertRaisesRegex(ValueError, "hash"):
                load_historical_learning_cases(output)


def _source_dataset() -> HistoricalReconstructionDataset:
    manifest = {
        "contract_version": "historical_reconstruction/v1",
        "dataset_id": "source-dataset",
        "revision_ids_hash": "source-revisions",
        "population": {"not_contemporaneous_top20": True},
    }
    candidate = {
        "kind": "historical_candidate",
        "available_at": "2026-09-22T00:00:00+00:00",
        "revision_id": "candidate",
        "payload": {
            "date": "2024-01-02", "code": "005930", "name": "삼성전자",
            "score": 0.8, "reasons": "거래대금", "rank_value": 1,
            "rank_gain": 2, "rank_high": 3, "rank_volume_ratio": 4,
            "gain_pct": 5.0, "high_pct": 6.0, "volume_ratio": 7.0,
            "trading_value": 800,
        },
    }
    news = [
        _news("news-1", "2024-01-02T01:00:00+09:00", "삼성전자"),
        _news("news-1-duplicate", "2024-01-02T01:00:00+09:00", "삼성전자 반도체"),
        _news("news-future", "2024-01-03T01:00:00+09:00", "삼성전자", article_id="future"),
    ]
    bars = [
        _bar("bar-1", "2024-01-03T09:01:00+09:00", 100, 105, 98, 104),
        _bar("bar-2", "2024-01-03T09:02:00+09:00", 104, 112, 103, 110),
        _bar("bar-5m", "2024-01-03T09:05:00+09:00", 100, 120, 90, 115, interval=300),
    ]
    return HistoricalReconstructionDataset(manifest, tuple([candidate, *news, *bars]))


def _news(
    revision_id: str, published_at: str, query: str, *, article_id: str = "article-1",
) -> dict[str, object]:
    return {
        "kind": "historical_news_evidence",
        "available_at": "2026-09-22T00:00:00+00:00",
        "revision_id": revision_id,
        "payload": {
            "source_date": "2024-01-02", "code": "005930",
            "provider": "naver_historical_search", "office_id": "001",
            "article_id": article_id, "office_name": "테스트신문",
            "title": "반도체 투자", "summary": "투자 계획 발표",
            "published_at": published_at, "published_precision": "second",
            "published_at_source": "json_ld:datePublished", "article_url": "https://example.com/a",
            "original_url": "https://example.com/original", "query_text": query,
            "publication_time_verified": True, "training_eligible": 1,
            "training_exclusion_reason": "",
        },
    }


def _bar(
    revision_id: str, bar_time: str, open_price: int, high: int, low: int,
    close: int, *, interval: int = 60,
) -> dict[str, object]:
    return {
        "kind": "historical_market_bar",
        "available_at": "2026-09-22T01:00:00+00:00",
        "revision_id": revision_id,
        "payload": {
            "case_date": "2024-01-02", "code": "005930", "phase": "outcome",
            "bar_time": bar_time, "interval_seconds": interval,
            "open": open_price, "high": high, "low": low, "close": close,
        },
    }


if __name__ == "__main__":
    unittest.main()
