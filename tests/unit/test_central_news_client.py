from __future__ import annotations

import io
import json
import unittest
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

from kiwoom_monitor.infrastructure.central_news_client import CentralNewsClient
from kiwoom_monitor.infrastructure.naver_news import NewsAISettings


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class CentralNewsClientTests(unittest.TestCase):
    def test_historical_archive_uses_separate_cursor_and_exact_ids(self) -> None:
        requests = []

        def opener(request, **_kwargs):
            requests.append(request)
            if len(requests) == 1:
                return _Response(json.dumps({
                    "dataset_id": "generation-1", "items": [{
                        "article_revision_id": "article/1", "display": {"title": "기사"},
                    }],
                    "next_cursor": "signed+/cursor",
                }).encode())
            return _Response(json.dumps({
                "dataset_id": "generation-1", "article_revision_id": "article/1",
                "document": {"title": "기사"},
                "body": {"body_revision_id": "body 1"},
            }).encode())

        client = CentralNewsClient("https://nas.test", "token", opener=opener)
        page = client.historical_archive_page("005930", limit=1, cursor="older+/cursor")
        detail = client.historical_archive_article(
            page["dataset_id"], page["items"][0]["article_revision_id"],
            body_revision_id="body 1",
        )
        self.assertEqual("generation-1", detail["dataset_id"])
        page_url, detail_url = (urlsplit(request.full_url) for request in requests)
        self.assertEqual("/api/v1/news/historical-archive/search", page_url.path)
        self.assertEqual({"stock_code": ["005930"], "limit": ["1"],
                          "cursor": ["older+/cursor"]}, parse_qs(page_url.query))
        self.assertEqual("/api/v1/news/historical-archive/articles/article%2F1", detail_url.path)
        self.assertEqual({"dataset_id": ["generation-1"], "body_revision_id": ["body 1"]},
                         parse_qs(detail_url.query))
        self.assertTrue(all(request.get_header("Authorization") == "Bearer token"
                            for request in requests))

    def test_historical_archive_rejects_mismatched_response_without_fallback(self) -> None:
        def opener(_request, **_kwargs):
            return _Response(json.dumps({"dataset_id": "another-generation",
                                         "article_revision_id": "article-1"}).encode())

        client = CentralNewsClient("https://nas.test", "token", opener=opener)
        with self.assertRaisesRegex(RuntimeError, "dataset"):
            client.historical_archive_article("generation-1", "article-1")

    def test_search_converts_server_document(self) -> None:
        requests = []

        def opener(request, **_kwargs):
            requests.append(request)
            return _Response(json.dumps({"items": [{
                "title": "제목", "description": "요약", "link": "https://n/1",
                "original_link": "https://o/1", "published_at": "2026-09-08T01:00:00+00:00",
                "relevant": 1, "category": "실적", "outlook": "호재 가능성", "reason": "근거",
                "relevance_score": 8, "outlook_score": 3,
            }]}).encode())

        item = CentralNewsClient("https://nas.test", "token", opener=opener).search(
            "005930", "삼성전자", datetime(2026, 9, 7, tzinfo=UTC),
            NewsAISettings("gemini", "", "flash", auto_analyze=True, auto_recent_limit=20),
        )[0]

        self.assertEqual("제목", item.title)
        self.assertEqual("호재 가능성", item.assessment.outlook)
        self.assertIn(b'"stock_code": "005930"', requests[0].data)
        self.assertIn(b'"ai_auto_recent_limit": 20', requests[0].data)


if __name__ == "__main__":
    unittest.main()
