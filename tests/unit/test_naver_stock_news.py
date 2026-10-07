from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from kiwoom_monitor.infrastructure.naver_stock_news import NaverStockNewsClient


def article(article_id: str, published: str, title: str = "SK하이닉스 공급계약 체결") -> dict:
    return {
        "officeId": "018", "articleId": article_id,
        "datetime": published, "title": title, "body": "500억원 규모 수주",
    }


class NaverStockNewsTests(unittest.TestCase):
    def test_nested_clusters_deduplicate_and_preserve_korean_publication_time(self) -> None:
        calls = []

        def fetch(code, page, size):
            calls.append((code, page, size))
            return {"clusters": [
                {"items": [article("1", "202609221530"), article("2", "202609221500")]},
                {"items": [article("1", "202609221530")]},
            ]}

        batch = NaverStockNewsClient(fetcher=fetch, page_size=15).search(
            "000660", "SK하이닉스", since=datetime(2026, 9, 21, tzinfo=ZoneInfo("Asia/Seoul")),
        )
        self.assertEqual(2, len(batch.items))
        self.assertEqual("https://n.news.naver.com/mnews/article/018/1", batch.items[0].link)
        self.assertEqual("2026-09-22T15:30:00+09:00", batch.items[0].published_at.isoformat())
        self.assertTrue(batch.complete)
        self.assertEqual([("000660", 1, 15)], calls)

    def test_page_100_is_last_allowed_page(self) -> None:
        calls = []

        def fetch(_code, page, size):
            calls.append(page)
            return {"clusters": [{"items": [article(str(page), "202609221530")]}] * size}

        batch = NaverStockNewsClient(fetcher=fetch, page_size=15, max_pages=3).search(
            "000660", "SK하이닉스", start_page=100,
        )
        self.assertEqual([100], calls)
        self.assertTrue(batch.page_limit_reached)
        self.assertFalse(batch.complete)

    def test_stops_at_existing_time_cursor(self) -> None:
        def fetch(_code, _page, _size):
            return {"clusters": [{"items": [article("1", "202609221500")]}] * 15}

        batch = NaverStockNewsClient(fetcher=fetch, page_size=15).search(
            "000660", "SK하이닉스", since=datetime(2026, 9, 22, 15, 30, tzinfo=ZoneInfo("Asia/Seoul")),
        )
        self.assertEqual((), batch.items)
        self.assertTrue(batch.complete)
