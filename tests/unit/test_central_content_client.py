from __future__ import annotations

import io
import json
import unittest
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError

from kiwoom_monitor.infrastructure.central_content_client import (
    CentralContentClient,
    CentralContentHttpError,
    CentralContentUnavailableError,
    is_missing_collection_error,
)


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class CentralContentClientTests(unittest.TestCase):
    def test_upsert_and_load_use_authenticated_content_api(self) -> None:
        requests = []

        def opener(request, **_kwargs):
            requests.append(request)
            if request.method == "POST":
                return Response(json.dumps({"saved": 1}).encode())
            return Response(json.dumps({"documents": [{"key": "one"}]}).encode())

        client = CentralContentClient("https://nas.test", "token", opener=opener)
        self.assertEqual(1, client.upsert("news_article", [{"owner": "005930", "key": "one", "document": {}}]))
        self.assertEqual("one", client.load("news_article", "005930")[0]["key"])
        self.assertEqual("Bearer token", requests[0].get_header("Authorization"))

    def test_load_all_reads_every_page(self) -> None:
        requested_urls: list[str] = []

        def opener(request, **_kwargs):
            requested_urls.append(request.full_url)
            offset = len(requested_urls) - 1
            documents = [{"key": str(offset)}] if offset < 2 else []
            return Response(json.dumps({"documents": documents}).encode())

        client = CentralContentClient("https://nas.test", "token", opener=opener)
        loaded = client.load_all("news_article", page_size=1)

        self.assertEqual(["0", "1"], [value["key"] for value in loaded])
        self.assertIn("offset=2", requested_urls[-1])

    def test_http_error_keeps_safe_server_detail(self) -> None:
        def opener(_request, **_kwargs):
            raise HTTPError(
                "https://nas.test/api/v1/news/analyze", 429, "limited", {},
                io.BytesIO(json.dumps({"detail": "AI 공급자 호출 한도"}).encode()),
            )

        client = CentralContentClient("https://nas.test", "token", opener=opener)
        with self.assertRaisesRegex(CentralContentHttpError, "HTTP 429 · AI 공급자 호출 한도") as caught:
            client.load("news_article")
        self.assertEqual(429, caught.exception.status_code)
        self.assertFalse(is_missing_collection_error(caught.exception))

    def test_http_404_is_explicitly_classified_as_missing_collection(self) -> None:
        def opener(_request, **_kwargs):
            raise HTTPError(
                "https://nas.test/api/v1/content/new", 404, "missing", {},
                io.BytesIO(json.dumps({"detail": "지원하지 않는 중앙 자료 종류입니다."}).encode()),
            )

        with self.assertRaises(CentralContentHttpError) as caught:
            CentralContentClient("https://nas.test", "token", opener=opener).load("new")
        self.assertEqual(404, caught.exception.status_code)
        self.assertTrue(is_missing_collection_error(caught.exception))

    def test_network_and_timeout_errors_are_not_classified_as_missing_collection(self) -> None:
        for error in (URLError("offline"), TimeoutError("slow")):
            def opener(_request, **_kwargs):
                raise error

            with self.subTest(error=type(error).__name__):
                with self.assertRaises(CentralContentUnavailableError) as caught:
                    CentralContentClient("https://nas.test", "token", opener=opener).load(
                        "journal_news_link"
                    )
                self.assertFalse(is_missing_collection_error(caught.exception))

    def test_load_theme_history_uses_as_of_without_content_projection_query(self) -> None:
        requests = []

        def opener(request, **_kwargs):
            requests.append(request)
            return Response(json.dumps({"known": False, "snapshots": []}).encode())

        result = CentralContentClient(
            "https://nas.test", "token", opener=opener,
        ).load_theme_history(as_of=123.5, limit=3)

        self.assertFalse(result["known"])
        self.assertIn("/api/v1/themes/history?", requests[0].full_url)
        self.assertIn("as_of=123.5", requests[0].full_url)
        self.assertIn("limit=3", requests[0].full_url)

    def test_load_news_history_uses_revision_endpoint(self) -> None:
        requests = []

        def opener(request, **_kwargs):
            requests.append(request)
            return Response(json.dumps({"known": True, "revisions": [{"identity": "a"}]}).encode())

        result = CentralContentClient(
            "https://nas.test", "token", opener=opener,
        ).load_news_history("article", target="005930", identity="a", as_of=123.5, limit=3)

        self.assertTrue(result["known"])
        self.assertIn("/api/v1/news/history/article?", requests[0].full_url)
        self.assertIn("target=005930", requests[0].full_url)
        self.assertIn("identity=a", requests[0].full_url)
        self.assertIn("as_of=123.5", requests[0].full_url)

    def test_news_history_url_encodes_stock_and_article_identity(self) -> None:
        requests = []

        def opener(request, **_kwargs):
            requests.append(request)
            return Response(json.dumps({"known": False, "revisions": []}).encode())

        CentralContentClient(
            "https://nas.test", "token", opener=opener,
        ).load_news_history(
            "article", target="A&B 종목", identity="https://news.test/a?x=1&y=한글",
        )

        self.assertIn("target=A%26B+%EC%A2%85%EB%AA%A9", requests[0].full_url)
        self.assertIn("identity=https%3A%2F%2Fnews.test%2Fa%3Fx%3D1%26y%3D%ED%95%9C%EA%B8%80", requests[0].full_url)

    def test_research_page_uses_fixed_watermark_and_cursor(self) -> None:
        requests = []

        def opener(request, **_kwargs):
            requests.append(request)
            return Response(json.dumps({"watermark": "fixed", "observations": []}).encode())

        CentralContentClient(
            "https://nas.test", "token", opener=opener,
        ).load_research_observations_page(
            datetime(2026, 9, 12, tzinfo=timezone.utc),
            datetime(2026, 9, 13, tzinfo=timezone.utc),
            ("ranking", "top20_membership"), subject="5", watermark="fixed", cursor=100,
        )

        self.assertIn("/api/v1/research/observations?", requests[0].full_url)
        self.assertIn("watermark=fixed", requests[0].full_url)
        self.assertIn("cursor=100", requests[0].full_url)
        self.assertIn("kinds=ranking%2Ctop20_membership", requests[0].full_url)

    def test_candidate_page_uses_local_consumption_cursor(self) -> None:
        requests = []

        def opener(request, **_kwargs):
            requests.append(request)
            return Response(json.dumps({"high_watermark": 8, "events": []}).encode())

        result = CentralContentClient(
            "https://nas.test", "token", opener=opener,
        ).load_candidate_events(after_sequence=7, limit=50)

        self.assertEqual(8, result["high_watermark"])
        self.assertIn("/api/v1/research/candidates?", requests[0].full_url)
        self.assertIn("after_sequence=7", requests[0].full_url)
        self.assertIn("limit=50", requests[0].full_url)


if __name__ == "__main__":
    unittest.main()
