from __future__ import annotations

import io
import json
import unittest
from urllib.error import HTTPError

from kiwoom_monitor.infrastructure.central_content_client import CentralContentClient


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
        with self.assertRaisesRegex(RuntimeError, "HTTP 429 · AI 공급자 호출 한도"):
            client.load("news_article")


if __name__ == "__main__":
    unittest.main()
