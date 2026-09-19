from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.central_server.database import SQLiteQueryStore


def _article(title: str = "첫 제목", **extra: object) -> list[dict[str, object]]:
    document = {
        "stock_code": "005930", "identity": "article-1", "title": title,
        "description": "검색 요약", "link": "https://news/1",
        "original_link": "https://origin/1", "published_at": "2026-09-12T00:00:00+00:00",
        **extra,
    }
    return [{"owner": "005930", "key": "article-1", "document": document,
             "collector_id": "naver", "collection_scope": "watchlist"}]


class NewsObservationHistoryTests(unittest.TestCase):
    def test_same_article_is_idempotent_and_correction_appends_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            with patch("kiwoom_monitor.central_server.database.time", side_effect=[100.0, 101.0]):
                store.upsert_documents("news_article", _article())
            store.upsert_documents("news_article", _article())
            with patch("kiwoom_monitor.central_server.database.time", side_effect=[200.0, 201.0]):
                store.upsert_documents("news_article", _article("정정 제목", first_seen_at="2020-01-01"))
            history = store.load_news_history("article", target="005930", identity="article-1")
            past = store.load_news_history(
                "article", target="005930", identity="article-1", available_at=150.0,
            )
            jobs = store.claim_news_jobs(limit=4, now=300.0)
            store.close()

        self.assertEqual(2, len(history))
        self.assertEqual("정정 제목", history[0]["document"]["title"])
        self.assertEqual(200.0, history[0]["received_at"])
        self.assertEqual(201.0, history[0]["available_at"])
        self.assertEqual(history[1]["article_revision_id"], history[0]["revision_of"])
        self.assertEqual(["첫 제목"], [value["document"]["title"] for value in past])
        self.assertEqual(2, len(jobs))

    def test_article_and_body_job_roll_back_together(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "central.sqlite3"
            store = SQLiteQueryStore(path)
            store.initialize()
            connection = sqlite3.connect(path)
            connection.execute(
                "CREATE TRIGGER reject_news_job BEFORE INSERT ON central_news_jobs "
                "BEGIN SELECT RAISE(ABORT,'job rejected'); END"
            )
            connection.commit(); connection.close()

            with self.assertRaisesRegex(sqlite3.IntegrityError, "job rejected"):
                store.upsert_documents("news_article", _article())
            projection = store.load_documents("news_article", "005930")
            history = store.load_news_history("article", target="005930")
            store.close()

        self.assertEqual([], projection)
        self.assertEqual([], history)

    def test_each_collector_keeps_its_first_receipt_without_duplicate_resends(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            store.upsert_documents("news_article", _article())
            dart = [{**_article()[0], "collector_id": "dart"}]
            store.upsert_documents("news_article", dart)
            store.upsert_documents("news_article", dart)
            history = store.load_news_history(
                "article", target="005930", identity="article-1",
            )
            store.close()

        self.assertEqual(2, len(history))
        self.assertEqual({"naver", "dart"}, {row["collector_id"] for row in history})
        self.assertEqual(history[1]["article_revision_id"], history[0]["revision_of"])

    def test_ai_job_key_uses_exact_body_revision_hash_and_processing_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            store.upsert_documents("news_article", _article())
            article = store.load_news_history("article", target="005930")[0]
            body_revision_id = store.save_news_body_revision({
                "article_revision_id": article["article_revision_id"], "status": "summary_only",
                "body_text": "검색 요약", "extractor_version": "test-v1", "fetched_at": 100.0,
            })
            base = {"stock_code": "005930", "identity": "article-1", "target_id": "005930",
                    "payload": {"stock_name": "삼성전자", "event": {"identity": "article-1"}}}
            self.assertEqual(1, store.enqueue_news_ai_jobs([{**base, "processing_version": "v1"}]))
            self.assertEqual(0, store.enqueue_news_ai_jobs([{**base, "processing_version": "v1"}]))
            self.assertEqual(1, store.enqueue_news_ai_jobs([{**base, "processing_version": "v2"}]))
            jobs = store.claim_news_jobs(limit=4, now=10_000_000_000.0)
            store.close()

        ai_jobs = [job for job in jobs if job["stage"] == "AI"]
        self.assertEqual(2, len(ai_jobs))
        self.assertEqual({"v1", "v2"}, {job["processing_version"] for job in ai_jobs})
        self.assertTrue(all(job["payload"]["body_revision_id"] == body_revision_id for job in ai_jobs))


if __name__ == "__main__":
    unittest.main()
