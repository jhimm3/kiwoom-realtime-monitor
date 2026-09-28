from __future__ import annotations

import os
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from kiwoom_monitor.presentation.historical_news_archive_dialog import HistoricalNewsArchiveDialog


class HistoricalNewsArchiveDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_background_client_loads_page_and_exact_detail_without_news_storage(self) -> None:
        calls = []

        class Client:
            def capabilities(self):
                calls.append("capability")
                return {"historical_news_archive_v1": True}

            def historical_archive_page(self, code, *, limit, cursor):
                calls.append(("page", code, limit, cursor))
                return {"dataset_id": "dataset-1", "items": [{
                    "article_revision_id": "article-1", "body_revision_id": "body-1",
                    "body_status": "fulltext", "display": {"title": "과거 기사"},
                }], "next_cursor": None}

            def historical_archive_article(self, dataset_id, article_id, *, body_revision_id):
                calls.append(("detail", dataset_id, article_id, body_revision_id))
                return {"dataset_id": dataset_id, "article_revision_id": article_id,
                        "document": {"title": "과거 기사"},
                        "body": {"status": "fulltext", "text": "PC에서 확보한 본문"},
                        "assessment_status": "verified", "events": []}

        def finish_request(dialog):
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                self.app.processEvents()
                if dialog._worker is None and dialog._pending is None:
                    return
                time.sleep(0.01)
            self.fail("archive background request did not finish")

        dialog = HistoricalNewsArchiveDialog(Client())
        dialog.show()
        dialog.set_stock("005930", "삼성전자")
        finish_request(dialog)
        self.assertEqual(["capability", ("page", "005930", 200, None)], calls)
        self.assertEqual(1, dialog._table.rowCount())
        dialog._table.selectRow(0)
        finish_request(dialog)
        self.assertEqual(("detail", "dataset-1", "article-1", "body-1"), calls[-1])
        self.assertIn("PC에서 확보한 본문", dialog._detail.toPlainText())
        dialog.shutdown()

    def test_archive_pages_detail_and_stock_change_stay_separate(self) -> None:
        dialog = HistoricalNewsArchiveDialog(object())
        with patch.object(dialog, "_start_pending"):
            dialog.set_stock("005930", "삼성전자")
            dialog.refresh()
            first_id = dialog._request_id
            dialog._on_completed(first_id, "005930", "page", {
                "dataset_id": "dataset-1", "items": [{
                    "article_revision_id": "article-1", "body_revision_id": "body-1",
                    "body_status": "missing", "display": {"title": "과거 기사", "published_at": None},
                }], "next_cursor": "cursor-1",
            })
            self.assertEqual(1, dialog._table.rowCount())
            self.assertTrue(dialog._more.isEnabled())
            dialog._table.selectRow(0)
            request = dialog._pending
            self.assertEqual("detail", request[2])
            self.assertEqual("dataset-1", request[3]["dataset_id"])
            self.assertEqual("body-1", request[3]["body_revision_id"])
            dialog._on_completed(dialog._request_id, "005930", "detail", {
                "article_revision_id": "article-1", "document": {"title": "과거 기사"},
                "body": None, "assessment_status": "unassessed", "events": [],
            })
            self.assertIn("확보된 본문 없음", dialog._detail.toPlainText())
            self.assertNotIn("처리 중", dialog._detail.toPlainText())
            self.assertTrue(dialog._more.isEnabled())
            dialog.set_stock("000660", "SK하이닉스")
            dialog._on_completed(first_id, "005930", "page", {
                "dataset_id": "dataset-1", "items": [], "next_cursor": None,
            })
            self.assertEqual(0, dialog._table.rowCount())
            self.assertIsNone(dialog._dataset_id)
        dialog.shutdown()

    def test_changed_dataset_page_is_not_appended(self) -> None:
        dialog = HistoricalNewsArchiveDialog(object())
        with patch.object(dialog, "_start_pending"):
            dialog.set_stock("005930", "삼성전자")
            dialog.refresh()
            dialog._on_completed(dialog._request_id, "005930", "page", {
                "dataset_id": "dataset-1", "items": [{
                    "article_revision_id": "article-1", "display": {"title": "첫 기사"},
                }], "next_cursor": "cursor-1",
            })
            dialog.load_more()
            dialog._on_completed(dialog._request_id, "005930", "page", {
                "dataset_id": "dataset-2", "items": [{
                    "article_revision_id": "article-2", "display": {"title": "다른 세대"},
                }], "next_cursor": None,
            })
            self.assertEqual(1, dialog._table.rowCount())
            self.assertIn("세대가 변경", dialog._status.text())
        dialog.shutdown()


if __name__ == "__main__":
    unittest.main()
