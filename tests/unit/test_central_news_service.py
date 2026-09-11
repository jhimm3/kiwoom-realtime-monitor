from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime

from kiwoom_monitor.application.news_analysis import NewsAssessment
from kiwoom_monitor.central_server.news_service import CentralNewsService
from kiwoom_monitor.infrastructure.naver_news import StockNewsItem


class _NewsClient:
    def __init__(self) -> None:
        self.calls = 0

    def search(self, _name, *, since=None):
        self.calls += 1
        return (StockNewsItem(
            "제목", "요약", "https://n/1", "https://o/1", datetime(2026, 9, 8, tzinfo=UTC),
            NewsAssessment(True, "실적", "호재 가능성", "근거", 8, 3),
        ),)


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


class _DartClient:
    def __init__(self) -> None:
        self.calls = 0

    def search(self, _code, _name):
        self.calls += 1
        return (StockNewsItem(
            "공급계약 공시", "삼성전자 공식 공시", "https://dart/1", "https://dart/1",
            datetime(2026, 9, 8, tzinfo=UTC),
            NewsAssessment(True, "수주·계약", "호재 가능성", "근거", 8, 3),
        ),)


class _AIService:
    def __init__(self) -> None:
        self.calls = []

    async def analyze(self, *args):
        self.calls.append(args)
        return {}


class CentralNewsServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_stock_requests_share_one_collection(self) -> None:
        client, store = _NewsClient(), _Store()
        service = CentralNewsService(client, store)  # type: ignore[arg-type]

        first, second = await asyncio.gather(
            service.search("005930", "삼성전자", None),
            service.search("005930", "삼성전자", None),
        )

        self.assertEqual(1, client.calls)
        self.assertEqual(first, second)
        article = next(value for collection, value in store.values if collection == "news_article")
        self.assertEqual("https://o/1", article["key"])
        self.assertEqual("호재 가능성", first[0]["outlook"])

    async def test_recent_sequential_request_uses_central_cache(self) -> None:
        client, store = _NewsClient(), _Store()
        service = CentralNewsService(client, store)  # type: ignore[arg-type]

        await service.search("005930", "삼성전자", None)
        cached = await service.search("005930", "삼성전자", None)

        self.assertEqual(1, client.calls)
        self.assertEqual("제목", cached[0]["title"])

    async def test_combines_naver_and_dart_once(self) -> None:
        client, dart, store = _NewsClient(), _DartClient(), _Store()
        service = CentralNewsService(client, store, dart)  # type: ignore[arg-type]

        items = await service.search("005930", "삼성전자", None)

        self.assertEqual(2, len(items))
        self.assertEqual(1, client.calls)
        self.assertEqual(1, dart.calls)

    async def test_registered_stock_is_refreshed_without_desktop_window(self) -> None:
        client, store = _NewsClient(), _Store()
        service = CentralNewsService(client, store)  # type: ignore[arg-type]
        await service.search("005930", "삼성전자", None)
        # 오래된 완료 시각으로 만들어 다음 자동 주기에 실제 확인되게 한다.
        for collection, value in store.values:
            if collection == "news_sync":
                value["document"]["checked_at"] = "2026-01-01T00:00:00+00:00"

        refreshed = await service.refresh_once()

        self.assertEqual(1, refreshed)
        self.assertEqual(2, client.calls)
        watchlist = [value for collection, value in store.values if collection == "news_watchlist"]
        self.assertTrue(watchlist)

    async def test_refresh_uses_saved_automatic_analysis_settings(self) -> None:
        client, store, ai = _NewsClient(), _Store(), _AIService()
        service = CentralNewsService(client, store)  # type: ignore[arg-type]
        service.set_ai_service(ai)
        await service.search("005930", "삼성전자", None, automation={
            "auto_analyze": True, "auto_recent_limit": 10,
            "provider": "gemini", "model": "flash",
        })
        for collection, value in store.values:
            if collection == "news_sync":
                value["document"]["checked_at"] = "2026-01-01T00:00:00+00:00"

        await service.refresh_once()

        self.assertEqual(1, len(ai.calls))
        self.assertEqual("gemini", ai.calls[0][2])

    async def test_refresh_does_not_reanalyze_existing_identity_after_prompt_change(self) -> None:
        client, store, ai = _NewsClient(), _Store(), _AIService()
        service = CentralNewsService(client, store)  # type: ignore[arg-type]
        service.set_ai_service(ai)
        await service.search("005930", "삼성전자", None, automation={
            "auto_analyze": True, "auto_recent_limit": 100,
            "provider": "gemini", "model": "flash",
        })
        store.upsert_documents("news_ai", [{
            "owner": "005930", "key": "https://o/1",
            "document": {"body_hash": "older-prompt-hash"},
        }])
        for collection, value in store.values:
            if collection == "news_sync":
                value["document"]["checked_at"] = "2026-01-01T00:00:00+00:00"

        await service.refresh_once()

        self.assertEqual([], ai.calls)


if __name__ == "__main__":
    unittest.main()
