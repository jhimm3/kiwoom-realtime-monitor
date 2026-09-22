from __future__ import annotations

import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from kiwoom_monitor.application.historical_learning_cases import HistoricalLearningCaseDataset
from kiwoom_monitor.application.historical_news_review_decisions import (
    build_historical_news_review_decisions,
    export_historical_news_review_sheet,
    load_historical_news_review_sheet,
    update_historical_news_review_sheet_row,
    write_historical_news_review_decisions,
)
from kiwoom_monitor.application.historical_news_event_split import (
    load_historical_news_event_split,
)
from kiwoom_monitor.application.historical_news_development_inputs import (
    load_historical_news_development_inputs,
)
from kiwoom_monitor.application.historical_news_review_queue import (
    build_historical_news_review_queue,
    write_historical_news_review_queue,
)
from kiwoom_monitor.presentation.historical_news_review_dialog import HistoricalNewsReviewDialog


class _Themes:
    active_profile = "기본 테마"

    @staticmethod
    def list_profiles() -> tuple[str, ...]:
        return ("기본 테마", "단타")


class HistoricalNewsReviewDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_saves_review_and_freezes_human_decision_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            research_dir = Path(directory)
            queue = build_historical_news_review_queue(
                _source(), created_at=datetime(2026, 9, 23, tzinfo=UTC),
            )
            queue_dir = research_dir / "historical-news-review-queues" / "queue-v1"
            write_historical_news_review_queue(queue, queue_dir)
            sheet = research_dir / "historical-news-review-work" / "priority.csv"
            export_historical_news_review_sheet(queue, sheet)

            dialog = HistoricalNewsReviewDialog(
                research_dir, theme_repository=_Themes(),
            )
            self.assertEqual(1, dialog._table.rowCount())
            self.assertEqual("검토 0/1", dialog._progress.text())
            self.assertGreaterEqual(dialog._profile.findText("기본 테마"), 0)

            dialog._decision.setCurrentIndex(dialog._decision.findData("relevant"))
            dialog._event.setCurrentText("event-semiconductor-investment")
            dialog._profile.setCurrentText("기본 테마")
            dialog._themes.setText("HBM|반도체 투자")
            dialog._reviewer.setText("tester")
            self.assertTrue(dialog._save_current(show_message=False, advance=False))

            row = load_historical_news_review_sheet(queue, sheet)[0]
            self.assertEqual("relevant", row["human_decision"])
            self.assertEqual("기본 테마", row["theme_profile_name"])
            self.assertTrue(row["reviewed_at"].endswith("+09:00"))
            self.assertEqual("검토 1/1", dialog._progress.text())

            with patch(
                "kiwoom_monitor.presentation.historical_news_review_dialog.QMessageBox.information"
            ):
                dialog._finalize_decisions()
            outputs = list((research_dir / "historical-news-review-decisions").glob("*/manifest.json"))
            self.assertEqual(1, len(outputs))
            self.assertIn("model_weight_training_ready", outputs[0].read_text(encoding="utf-8"))
            dialog.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_creates_event_split_from_latest_frozen_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            research_dir = Path(directory)
            queue = build_historical_news_review_queue(
                _source(article_count=3), created_at=datetime(2026, 9, 23, tzinfo=UTC),
            )
            queue_dir = research_dir / "historical-news-review-queues" / "queue-v1"
            write_historical_news_review_queue(queue, queue_dir)
            sheet = research_dir / "historical-news-review-work" / "priority.csv"
            export_historical_news_review_sheet(queue, sheet)
            for index, item in enumerate(queue.items, start=1):
                update_historical_news_review_sheet_row(queue, sheet, item["review_item_id"], {
                    "human_decision": "relevant",
                    "canonical_event_id": f"event-{index}",
                    "theme_profile_name": "기본 테마",
                    "theme_names": "반도체 투자",
                    "notes": "원문 확인",
                    "reviewer": "tester",
                    "reviewed_at": f"2026-09-23T10:0{index}:00+09:00",
                })
            decisions = build_historical_news_review_decisions(queue, sheet)
            decision_dir = (
                research_dir / "historical-news-review-decisions"
                / str(decisions.manifest["dataset_id"])
            )
            write_historical_news_review_decisions(decisions, decision_dir)
            dialog = HistoricalNewsReviewDialog(research_dir, theme_repository=_Themes())

            with patch(
                "kiwoom_monitor.presentation.historical_news_review_dialog.QInputDialog.getInt",
                side_effect=((1, True), (1, True)),
            ), patch(
                "kiwoom_monitor.presentation.historical_news_review_dialog.QMessageBox.information"
            ):
                dialog._create_event_split()

            outputs = list((research_dir / "historical-news-event-splits").glob("*.json"))
            self.assertEqual(1, len(outputs))
            plan = load_historical_news_event_split(outputs[0])
            self.assertEqual(["TRAIN", "VALIDATION", "OOS"], [
                row["role"] for row in plan["partitions"]
            ])

            with patch(
                "kiwoom_monitor.presentation.historical_news_review_dialog.QMessageBox.information"
            ):
                dialog._create_development_inputs()

            development_outputs = list(
                (research_dir / "historical-news-development-inputs").glob("*/manifest.json")
            )
            self.assertEqual(1, len(development_outputs))
            dataset = load_historical_news_development_inputs(
                development_outputs[0].parent
            )
            self.assertEqual(1, len(dataset.train))
            self.assertEqual(1, len(dataset.validation))
            self.assertFalse(dataset.manifest["boundaries"]["oos_payload_included"])
            self.assertEqual(plan["plan_id"], dataset.manifest["source"]["event_split"]["plan_id"])
            dialog.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _source(*, article_count: int = 1) -> HistoricalLearningCaseDataset:
    cases = []
    for index in range(1, article_count + 1):
        evidence = {
            "provider": "naver_historical_search",
            "office_id": "001",
            "article_id": f"article-{index}",
            "office_name": "테스트신문",
            "title": f"삼성전자, 반도체 투자 계획 공시 {index}",
            "search_summary": "삼성전자가 신규 설비투자를 결정했다고 밝혔다.",
            "published_at": f"2024-01-0{index + 1}T10:00:00+09:00",
            "published_precision": "second",
            "published_at_source": "json_ld:datePublished",
            "article_url": f"https://example.com/a-{index}",
            "original_url": f"https://example.com/original-{index}",
            "collector_available_at": "2026-09-22T00:00:00+00:00",
            "source_revision_id": f"news-{index}",
            "query_texts": ["삼성전자"],
        }
        cases.append({
            "case_id": f"case-samsung-{index}",
            "selection_date": f"2024-01-0{index + 1}",
            "stock": {"code": "005930", "name": "삼성전자"},
            "model_input": {"news_evidence": [evidence]},
        })
    manifest = {
        "contract_version": "historical_learning_cases/v1",
        "dataset_id": "learning-source",
        "cases_file_hash": "learning-source-hash",
    }
    return HistoricalLearningCaseDataset(manifest, tuple(cases))


if __name__ == "__main__":
    unittest.main()
