from __future__ import annotations

import unittest
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.central_server.ai_service import CentralAIService, _prepare_events
from kiwoom_monitor.central_server.config import CentralServerSettings
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.infrastructure.news_ai import ANALYSIS_PROMPT_VERSION, AINewsAnalysis, AIRequestUsage


class _Store:
    def __init__(self) -> None:
        self.values = []

    def upsert_documents(self, collection, values):
        self.values.extend((collection, value) for value in values)

    def load_documents(self, collection, owner="", limit=1000):
        return [
            {"owner": value["owner"], "key": value["key"], "document": value["document"]}
            for saved_collection, value in self.values
            if saved_collection == collection and (not owner or value["owner"] == owner)
        ][:limit]


class CentralAIServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_server_fetches_article_body_and_calculates_hash(self) -> None:
        with patch("kiwoom_monitor.central_server.ai_service.fetch_article_text", return_value="중앙 원문") as fetch:
            prepared = _prepare_events([{
                "identity": "article", "title": "제목", "articles": [{
                    "title": "관련 제목", "description": "검색 요약",
                    "link": "https://news/1", "original_link": "https://origin/1",
                }],
            }])
        self.assertIn("중앙 원문", prepared[0]["body"])
        self.assertTrue(prepared[0]["body_hash"].startswith(f"{ANALYSIS_PROMPT_VERSION}:"))
        fetch.assert_called_once_with("https://news/1")

    def test_server_uses_search_summary_when_original_is_unavailable(self) -> None:
        with patch("kiwoom_monitor.central_server.ai_service.fetch_article_text", side_effect=ValueError("blocked")):
            prepared = _prepare_events([{
                "identity": "article", "title": "제목", "articles": [{
                    "title": "관련 제목", "description": "검색 요약", "link": "https://news/1",
                }],
            }])
        self.assertIn("검색 요약", prepared[0]["body"])

    async def test_analyzes_once_then_reuses_body_hash_cache(self) -> None:
        settings = CentralServerSettings("sqlite:///:memory:", "token", gemini_api_key="key")
        store = _Store()
        service = CentralAIService(settings, store)  # type: ignore[arg-type]
        events = [{"identity": "article", "title": "제목", "body": "본문", "body_hash": "hash"}]
        analysis = AINewsAnalysis("요약", "긍정", 80, "이유", ("근거",), (), "실적·전망")

        with patch("kiwoom_monitor.central_server.ai_service.analyze_articles", return_value=((analysis,), AIRequestUsage(10, 5, 15))) as analyze:
            first = await service.analyze("005930", "삼성전자", "gemini", "model", events, 1)
            second = await service.analyze("005930", "삼성전자", "gemini", "model", events, 1)

        self.assertEqual(1, analyze.call_count)
        self.assertFalse(first["cache_hit"])
        self.assertTrue(second["cache_hit"])
        self.assertEqual("요약", second["results"][0]["summary"])
        self.assertEqual(1, len([value for collection, value in store.values if collection == "news_request_usage"]))

    async def test_daily_limit_is_shared_by_different_requests(self) -> None:
        settings = CentralServerSettings(
            "sqlite:///:memory:", "token", gemini_api_key="key", ai_daily_limit=1,
        )
        service = CentralAIService(settings, _Store())  # type: ignore[arg-type]
        analysis = AINewsAnalysis("요약", "긍정", 80, "이유")
        with patch("kiwoom_monitor.central_server.ai_service.analyze_articles", return_value=((analysis,), AIRequestUsage())):
            await service.analyze("005930", "삼성전자", "gemini", "model", [{
                "identity": "one", "title": "제목", "body": "본문", "body_hash": "one",
            }], 1)
            with self.assertRaisesRegex(ValueError, "일일 상한"):
                await service.analyze("000660", "SK하이닉스", "gemini", "model", [{
                    "identity": "two", "title": "제목", "body": "본문", "body_hash": "two",
                }], 1)

    async def test_real_store_appends_ai_revision_and_usage_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "central.sqlite3"
            store = SQLiteQueryStore(path)
            store.initialize()
            store.upsert_documents("news_article", [{
                "owner": "005930", "key": "article", "collector_id": "naver",
                "collection_scope": "watchlist",
                "document": {"stock_code": "005930", "identity": "article", "title": "제목"},
            }])
            article = store.load_news_history("article", target="005930")[0]
            body_revision_id = store.save_news_body_revision({
                "article_revision_id": article["article_revision_id"], "status": "fulltext",
                "body_text": "본문", "extractor_version": "test-v1", "fetched_at": 100.0,
            })
            service = CentralAIService(
                CentralServerSettings("sqlite:///:memory:", "token", gemini_api_key="key"), store,
            )
            analysis = AINewsAnalysis("요약", "긍정", 80, "이유")
            event = {"identity": "article", "title": "제목", "body": "본문",
                     "article_revision_id": article["article_revision_id"],
                     "body_revision_id": body_revision_id}
            with patch(
                "kiwoom_monitor.central_server.ai_service.analyze_articles",
                return_value=((analysis,), AIRequestUsage(3, 2, 5)),
            ) as analyze:
                result = await service.analyze("005930", "삼성전자", "gemini", "model", [event], 1)
                second = await service.analyze(
                    "005930", "삼성전자", "gemini", "model-v2", [event], 1,
                )
            revisions = store.load_news_history("ai", target="005930")
            usage = store.load_documents("news_request_usage", limit=10)
            latest = store.load_documents("news_ai", "005930", 10)
            store.close()

        self.assertEqual(2, analyze.call_count)
        self.assertEqual(2, len(revisions))
        self.assertEqual([revisions[1]["analysis_revision_id"]], result["analysis_revision_ids"])
        self.assertEqual([revisions[0]["analysis_revision_id"]], second["analysis_revision_ids"])
        self.assertTrue(all(
            row["article_revision_id"] == article["article_revision_id"] for row in revisions
        ))
        self.assertTrue(all(row["body_revision_id"] == body_revision_id for row in revisions))
        self.assertEqual("요약", revisions[0]["output"]["summary"])
        self.assertEqual(5, revisions[0]["usage"]["total_tokens"])
        self.assertEqual(2, len(usage))
        self.assertEqual("요약", latest[0]["document"]["summary"])

    def test_ai_projection_revision_and_usage_roll_back_together(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "central.sqlite3"
            store = SQLiteQueryStore(path)
            store.initialize()
            connection = sqlite3.connect(path)
            connection.execute(
                "CREATE TRIGGER reject_ai_usage BEFORE INSERT ON central_documents "
                "WHEN NEW.collection='news_request_usage' "
                "BEGIN SELECT RAISE(ABORT,'usage rejected'); END"
            )
            connection.commit(); connection.close()
            with self.assertRaisesRegex(sqlite3.IntegrityError, "usage rejected"):
                store.save_news_ai_results(
                    [{"owner": "005930", "key": "article", "document": {"summary": "요약"}}],
                    [{"analysis_revision_id": "revision", "target_id": "005930",
                      "article_revision_id": "article-revision", "provider": "gemini",
                      "model": "model", "prompt_version": "prompt-v1", "input_hash": "hash",
                      "output": {"summary": "요약"}, "usage": {"total_tokens": 5}}],
                    [{"owner": "2026-09-12", "key": "request", "document": {"total_tokens": 5}}],
                )
            self.assertEqual([], store.load_documents("news_ai", "005930"))
            self.assertEqual([], store.load_news_history("ai", target="005930"))
            store.close()


if __name__ == "__main__":
    unittest.main()
