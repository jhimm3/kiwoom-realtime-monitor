from __future__ import annotations

import unittest
from unittest.mock import patch

from kiwoom_monitor.central_server.ai_service import CentralAIService, _prepare_events
from kiwoom_monitor.central_server.config import CentralServerSettings
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


if __name__ == "__main__":
    unittest.main()
