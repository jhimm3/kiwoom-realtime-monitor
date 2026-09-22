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
    export_historical_news_review_sheet,
    load_historical_news_review_sheet,
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


def _source() -> HistoricalLearningCaseDataset:
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
    case = {
        "case_id": "case-samsung",
        "selection_date": "2024-01-02",
        "stock": {"code": "005930", "name": "삼성전자"},
        "model_input": {"news_evidence": [evidence]},
    }
    manifest = {
        "contract_version": "historical_learning_cases/v1",
        "dataset_id": "learning-source",
        "cases_file_hash": "learning-source-hash",
    }
    return HistoricalLearningCaseDataset(manifest, (case,))


if __name__ == "__main__":
    unittest.main()
