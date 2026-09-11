from __future__ import annotations

import io
import json
import unittest
from datetime import UTC, datetime

from kiwoom_monitor.infrastructure.central_news_client import CentralNewsClient
from kiwoom_monitor.infrastructure.naver_news import NewsAISettings


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class CentralNewsClientTests(unittest.TestCase):
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
