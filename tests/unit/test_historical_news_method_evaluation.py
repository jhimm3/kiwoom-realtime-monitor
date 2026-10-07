from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kiwoom_monitor.application.historical_news_blind_validation import (
    build_historical_news_blind_validation,
)
from kiwoom_monitor.application.historical_news_development_inputs import (
    build_historical_news_development_inputs,
)
from kiwoom_monitor.application.historical_news_event_split import (
    build_historical_news_event_split,
)
from kiwoom_monitor.application.historical_news_method_evaluation import (
    build_historical_news_method_evaluation,
    load_historical_news_method_evaluation,
    write_historical_news_method_evaluation,
)
from kiwoom_monitor.application.historical_news_method_results import (
    build_historical_news_method_results,
    load_historical_news_method_results,
    write_historical_news_method_results,
)
from kiwoom_monitor.application.historical_news_review_decisions import (
    HistoricalNewsReviewDecisions,
)


class HistoricalNewsMethodEvaluationTests(unittest.TestCase):
    def test_opaque_event_labels_and_theme_sets_score_without_oos(self) -> None:
        development, blind = _datasets()
        sample_ids = [row["sample_id"] for row in blind.requests]
        results = build_historical_news_method_results(
            blind,
            method=_method(),
            predictions=(
                _prediction(sample_ids[0], "opaque-a", "테마 B"),
                _prediction(sample_ids[1], "opaque-a", "테마 B"),
                _prediction(sample_ids[2], "opaque-b", "테마 C"),
            ),
        )

        report = build_historical_news_method_evaluation(development, blind, results)

        metrics = report["metrics"]
        self.assertEqual(1, metrics["event_pair_true_positive"])
        self.assertEqual(1_000_000, metrics["event_pair_f1_ppm"])
        self.assertEqual(1_000_000, metrics["theme_f1_ppm"])
        self.assertEqual(1_000_000, metrics["theme_exact_set_ppm"])
        self.assertFalse(report["boundaries"]["oos_used"])
        self.assertTrue(report["boundaries"]["event_cluster_labels_are_opaque"])
        self.assertFalse(report["boundaries"]["semantic_relevance_metric_supported"])

    def test_imperfect_predictions_are_counted_and_artifacts_round_trip(self) -> None:
        development, blind = _datasets()
        sample_ids = [row["sample_id"] for row in blind.requests]
        predictions = (
            _prediction(sample_ids[0], "one-cluster", "테마 B"),
            _prediction(sample_ids[1], "one-cluster", "잘못된 테마"),
            _prediction(sample_ids[2], "one-cluster", "테마 C"),
        )
        results = build_historical_news_method_results(
            blind, method=_method(), predictions=predictions,
        )
        report = build_historical_news_method_evaluation(development, blind, results)
        self.assertEqual(333_333, report["metrics"]["event_pair_precision_ppm"])
        self.assertEqual(500_000, report["metrics"]["event_pair_f1_ppm"])
        self.assertEqual(666_666, report["metrics"]["theme_f1_ppm"])
        self.assertEqual(666_666, report["metrics"]["theme_exact_set_ppm"])

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result_path = root / "results"
            report_path = root / "evaluation.json"
            write_historical_news_method_results(results, result_path)
            self.assertEqual(results, load_historical_news_method_results(result_path))
            write_historical_news_method_evaluation(report, report_path)
            self.assertEqual(report, load_historical_news_method_evaluation(report_path))

        changed_interpretation = {
            **report,
            "interpretation": {
                **report["interpretation"],
                "event_metric": "changed-after-scoring",
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "interpretation"):
                write_historical_news_method_evaluation(
                    changed_interpretation, Path(directory) / "changed.json",
                )

        with self.assertRaisesRegex(ValueError, "cover"):
            build_historical_news_method_results(
                blind, method=_method(), predictions=predictions[:-1],
            )


def _datasets():
    decisions = HistoricalNewsReviewDecisions(
        {
            "contract_version": "historical_news_review_decisions/v1",
            "dataset_id": "decisions-method-test",
            "decisions_file_hash": "d" * 64,
            "decision_count": 5,
            "boundaries": {
                "included_decisions_human_reviewed": True,
                "source_queue_review_complete": True,
                "model_weight_training_ready": False,
            },
        },
        (
            _decision(1, "event-a", "테마 A", "2024-01-02T09:00:00+09:00"),
            _decision(2, "event-b", "테마 B", "2024-02-02T09:00:00+09:00"),
            _decision(3, "event-b", "테마 B", "2024-02-02T10:00:00+09:00"),
            _decision(4, "event-c", "테마 C", "2024-03-02T09:00:00+09:00"),
            _decision(5, "event-d", "테마 D", "2024-04-02T09:00:00+09:00"),
        ),
    )
    split = build_historical_news_event_split(
        decisions, train_events=1, validation_events=2,
    )
    development = build_historical_news_development_inputs(decisions, split)
    return development, build_historical_news_blind_validation(development)


def _method() -> dict[str, str]:
    return {
        "kind": "rag",
        "provider": "local",
        "model": "fixture-model",
        "implementation_version": "rag-fixture/v1",
        "artifact_id": "fixture-index-sha256",
    }


def _prediction(sample_id: str, cluster: str, theme: str) -> dict[str, object]:
    return {
        "sample_id": sample_id,
        "abstained": False,
        "event_cluster_id": cluster,
        "theme_profile_name": "기본 테마",
        "theme_names": [theme],
    }


def _decision(index: int, event_id: str, theme: str, published_at: str) -> dict[str, object]:
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
            "canonical_event_id": event_id,
            "theme_profile_name": "기본 테마",
            "theme_names": [theme],
        },
        "eligibility": {
            "human_review_complete": True,
            "model_weight_training_ready": False,
        },
    }


if __name__ == "__main__":
    unittest.main()
