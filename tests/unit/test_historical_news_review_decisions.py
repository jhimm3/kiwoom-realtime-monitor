from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from kiwoom_monitor.application.historical_news_review_decisions import (
    build_historical_news_review_decisions,
    export_historical_news_review_sheet,
    load_historical_news_review_decisions,
    write_historical_news_review_decisions,
)
from kiwoom_monitor.application.historical_news_review_queue import HistoricalNewsReviewQueue


class HistoricalNewsReviewDecisionTests(unittest.TestCase):
    def test_exports_priority_sheet_without_overwriting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "review.csv"

            result = export_historical_news_review_sheet(_queue(), output)

            self.assertEqual(1, result["row_count"])
            with output.open("r", encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(["review-1"], [row["review_item_id"] for row in rows])
            self.assertEqual("", rows[0]["human_decision"])
            self.assertEqual("false", str(result["authoritative"]).lower())
            with self.assertRaisesRegex(ValueError, "will not be overwritten"):
                export_historical_news_review_sheet(_queue(), output)

    def test_imports_completed_decisions_and_keeps_training_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sheet = root / "review.csv"
            output = root / "decisions"
            source = _queue()
            export_historical_news_review_sheet(source, sheet, rule_relevant_only=False)
            rows, fields = _read_rows(sheet)
            rows[0].update({
                "human_decision": "relevant",
                "canonical_event_id": "event-semiconductor-investment-20240102",
                "theme_profile_id": "default-profile",
                "theme_names": "HBM|반도체 투자",
                "notes": "직접 투자 공시",
                "reviewer": "tester",
                "reviewed_at": "2026-09-22T21:30:00+09:00",
            })
            rows[1].update({
                "human_decision": "not_relevant",
                "notes": "다른 회사 기사",
                "reviewer": "tester",
                "reviewed_at": "2026-09-22T21:31:00+09:00",
            })
            _write_rows(sheet, fields, rows)

            dataset = build_historical_news_review_decisions(
                source, sheet, created_at=datetime(2026, 9, 22, tzinfo=UTC),
            )
            write_historical_news_review_decisions(dataset, output)
            loaded = load_historical_news_review_decisions(output)

            self.assertEqual(2, len(loaded.decisions))
            self.assertEqual(1, loaded.manifest["counts"]["relevant"])
            self.assertEqual(1, loaded.manifest["counts"]["not_relevant"])
            self.assertTrue(loaded.manifest["boundaries"]["source_queue_review_complete"])
            self.assertFalse(loaded.manifest["boundaries"]["model_weight_training_ready"])
            review = loaded.decisions[0]["human_review"]
            self.assertEqual("event-semiconductor-investment-20240102", review["canonical_event_id"])
            self.assertEqual(["HBM", "반도체 투자"], review["theme_names"])
            with self.assertRaisesRegex(ValueError, "immutable"):
                write_historical_news_review_decisions(dataset, output)
            with (output / "decisions.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"ordinal": 3}) + "\n")
            with self.assertRaisesRegex(ValueError, "hash"):
                load_historical_news_review_decisions(output)

    def test_relevant_decision_requires_event_and_theme_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sheet = Path(directory) / "review.csv"
            source = _queue()
            export_historical_news_review_sheet(source, sheet)
            rows, fields = _read_rows(sheet)
            rows[0].update({
                "human_decision": "relevant",
                "theme_names": "HBM",
                "reviewer": "tester",
                "reviewed_at": "2026-09-22T21:30:00+09:00",
            })
            _write_rows(sheet, fields, rows)

            with self.assertRaisesRegex(ValueError, "requires canonical_event_id"):
                build_historical_news_review_decisions(source, sheet)

            rows[0]["canonical_event_id"] = "event-1"
            _write_rows(sheet, fields, rows)
            with self.assertRaisesRegex(ValueError, "theme names require theme_profile_id"):
                build_historical_news_review_decisions(source, sheet)

    def test_rejects_sheet_bound_to_different_queue_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sheet = Path(directory) / "review.csv"
            source = _queue()
            export_historical_news_review_sheet(source, sheet)
            rows, fields = _read_rows(sheet)
            rows[0]["source_queue_items_file_hash"] = "stale-hash"
            _write_rows(sheet, fields, rows)

            with self.assertRaisesRegex(ValueError, "source queue hash does not match"):
                build_historical_news_review_decisions(source, sheet)

def _queue() -> HistoricalNewsReviewQueue:
    items = (
        _item(1, "review-1", True, "005930", "삼성전자"),
        _item(2, "review-2", False, "000660", "SK하이닉스"),
    )
    manifest = {
        "contract_version": "historical_news_review_queue/v1",
        "dataset_id": "queue-dataset",
        "items_file_hash": "queue-items-hash",
        "item_count": len(items),
    }
    return HistoricalNewsReviewQueue(manifest, items)


def _item(
    ordinal: int, review_item_id: str, relevant: bool, code: str, name: str,
) -> dict[str, object]:
    return {
        "ordinal": ordinal,
        "review_item_id": review_item_id,
        "article": {
            "provider": "naver_historical_search",
            "office_id": "001",
            "article_id": f"article-{ordinal}",
            "title": f"{name} 투자 기사",
            "search_summary": "투자 계획을 발표했다.",
            "published_at": "2024-01-02T10:00:00+09:00",
            "article_url": f"https://example.com/{ordinal}",
        },
        "case_links": [{
            "case_id": f"case-{ordinal}",
            "selection_date": "2024-01-02",
            "stock": {"code": code, "name": name},
        }],
        "priority": {
            "has_rule_relevant_hint": relevant,
            "maximum_rule_relevance_score": 10 if relevant else 0,
        },
    }


def _read_rows(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        return list(reader), list(reader.fieldnames or [])


def _write_rows(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()