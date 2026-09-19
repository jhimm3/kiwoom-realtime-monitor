from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime, timedelta

from kiwoom_monitor.application.news_analysis import NewsAssessment
from kiwoom_monitor.central_server.news_service import CentralNewsService
from kiwoom_monitor.infrastructure.naver_news import StockNewsItem


class _NewsClient:
    def __init__(
        self, published_at: datetime | None = None, *,
        title: str = "제목", description: str = "요약",
    ) -> None:
        self.calls = 0
        self.published_at = published_at or datetime(2026, 9, 8, tzinfo=UTC)
        self.title = title
        self.description = description

    def search(self, _name, *, since=None):
        self.calls += 1
        return (StockNewsItem(
            self.title, self.description, "https://n/1", "https://o/1", self.published_at,
            NewsAssessment(True, "실적", "호재 가능성", "근거", 8, 3),
        ),)


class _FailingNewsClient:
    def search(self, _name, *, since=None):
        raise RuntimeError("provider unavailable")


class _EmptyNewsClient:
    def __init__(self) -> None:
        self.calls = 0

    def search(self, _name, *, since=None):
        self.calls += 1
        return ()


class _Store:
    def __init__(self) -> None:
        self.values = []
        self.confirmed = {}
        self.stock_bodies = {}

    def upsert_documents(self, collection, values):
        self.values.extend((collection, value) for value in values)

    def load_documents(self, collection, owner="", limit=1000):
        return [
            {"owner": value["owner"], "key": value["key"], "document": value["document"]}
            for saved_collection, value in self.values
            if saved_collection == collection and (not owner or value["owner"] == owner)
        ][:limit]

    def load_confirmed_news_articles(self, stock_code, *, limit=1000):
        return list(self.confirmed.get(stock_code, ()))[:limit]

    def load_stock_news_articles(self, stock_code, *, limit=1000):
        return [
            {
                **value["document"],
                "_body_status": "fulltext" if value["key"] in self.stock_bodies else "",
                "_body_text": self.stock_bodies.get(value["key"], ""),
            }
            for saved_collection, value in self.values
            if saved_collection == "news_article" and value["owner"] == stock_code
        ][:limit]


class _JobStore(_Store):
    def __init__(self) -> None:
        super().__init__()
        self.jobs = []

    def enqueue_news_ai_jobs(self, values):
        self.jobs.extend(values)
        return len(values)


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
    async def test_client_none_returns_confirmed_global_news_with_recomputed_assessment(self) -> None:
        store = _Store()
        store.confirmed["005930"] = [{
            "title": "삼성전자 공급계약 체결", "description": "500억원 수주",
            "link": "https://n/n3", "original_link": "https://o/n3",
            "published_at": "2026-09-09T01:00:00+00:00",
        }]
        service = CentralNewsService(None, store)  # type: ignore[arg-type]

        items = await service.search("005930", "삼성전자", None)

        self.assertEqual(["https://o/n3"], [item["original_link"] for item in items])
        self.assertEqual("수주·계약", items[0]["category"])
        self.assertTrue(items[0]["relevant"])

    async def test_owner_identity_wins_and_merged_results_are_stably_sorted(self) -> None:
        client, store = _NewsClient(), _Store()
        store.confirmed["005930"] = [
            {
                "title": "GLOBAL 중복 제목", "description": "GLOBAL 요약",
                "link": "https://n/1", "original_link": "https://o/1",
                "published_at": "2026-09-10T01:00:00+00:00",
            },
            {
                "title": "삼성전자 최신 저장뉴스", "description": "공급계약",
                "link": "https://n/2", "original_link": "https://o/2",
                "published_at": "2026-09-11T01:00:00+00:00",
            },
            {
                "title": "삼성전자 시각 오류 저장뉴스", "description": "공급계약",
                "link": "https://n/3", "original_link": "https://o/3", "published_at": "invalid",
            },
        ]
        service = CentralNewsService(client, store)  # type: ignore[arg-type]

        items = await service.search(
            "005930", "삼성전자", datetime(2026, 9, 7, tzinfo=UTC),
        )

        self.assertEqual(["https://o/2", "https://o/1"], [item["original_link"] for item in items])
        duplicate = next(item for item in items if item["original_link"] == "https://o/1")
        self.assertEqual("제목", duplicate["title"])
        without_since = await service.search("005930", "삼성전자", None)
        self.assertEqual("https://o/3", without_since[-1]["original_link"])

    async def test_owner_article_keeps_provider_fields_but_uses_nas_body_assessment(self) -> None:
        client, store = _NewsClient(), _Store()
        store.confirmed["005930"] = [{
            "title": "삼성전자 7만원선 탈환…장중 급등",
            "description": "매수세가 집중됐다.",
            "link": "https://n/1", "original_link": "https://o/1",
            "published_at": "2026-09-08T00:00:00+00:00",
            "_body_status": "fulltext",
            "_body_text": "삼성전자가 장중 7만원선을 회복했다. 지난달 공급계약이 다시 언급됐다.",
        }]
        service = CentralNewsService(client, store)  # type: ignore[arg-type]

        items = await service.search("005930", "삼성전자", None)

        self.assertEqual("제목", items[0]["title"])
        self.assertFalse(items[0]["relevant"])
        self.assertEqual("시세 반영·시장 요약", items[0]["category"])

    async def test_recent_owner_cache_merges_confirmed_without_another_provider_call(self) -> None:
        client, store = _NewsClient(), _Store()
        service = CentralNewsService(client, store)  # type: ignore[arg-type]
        await service.search("005930", "삼성전자", None)
        store.confirmed["005930"] = [{
            "title": "삼성전자 저장 뉴스", "description": "공급계약",
            "link": "https://n/stored", "original_link": "https://o/stored",
            "published_at": "2026-09-09T01:00:00+00:00",
        }]

        cached = await service.search("005930", "삼성전자", None)

        self.assertEqual(1, client.calls)
        self.assertEqual({"https://o/1", "https://o/stored"}, {item["original_link"] for item in cached})

    async def test_recent_owner_cache_reclassifies_with_latest_stored_body(self) -> None:
        client = _NewsClient()
        client.published_at = datetime(2026, 9, 8, tzinfo=UTC)
        store = _Store()
        service = CentralNewsService(client, store)  # type: ignore[arg-type]
        first = await service.search("005930", "삼성전자", None)
        self.assertTrue(first[0]["relevant"])
        store.stock_bodies["https://o/1"] = (
            "삼성전자가 장중 7만원선을 회복했다. 거래대금 상위 종목에 이름을 올렸다."
        )

        cached = await service.search("005930", "삼성전자", None)

        self.assertEqual(1, client.calls)
        self.assertFalse(cached[0]["relevant"])

    async def test_provider_error_returns_confirmed_archive_but_still_raises_when_archive_is_empty(self) -> None:
        store = _Store()
        store.confirmed["005930"] = [{
            "title": "삼성전자 저장 뉴스", "description": "공급계약",
            "link": "https://n/stored", "original_link": "https://o/stored",
            "published_at": "2026-09-09T01:00:00+00:00",
        }]
        service = CentralNewsService(_FailingNewsClient(), store)  # type: ignore[arg-type]

        items = await service.search("005930", "삼성전자", None)

        self.assertEqual("https://o/stored", items[0]["original_link"])
        empty = CentralNewsService(_FailingNewsClient(), _Store())  # type: ignore[arg-type]
        with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
            await empty.search("000660", "SK하이닉스", None)

    async def test_refresh_shows_confirmed_n3_but_does_not_send_it_to_automatic_ai(self) -> None:
        client, store, ai = _EmptyNewsClient(), _Store(), _AIService()
        store.confirmed["005930"] = [{
            "title": "삼성전자 공급계약 체결", "description": "500억원 수주",
            "link": "https://n/n3", "original_link": "https://o/n3",
            "published_at": datetime.now(UTC).isoformat(),
        }]
        service = CentralNewsService(client, store, jobs_enabled=False)  # type: ignore[arg-type]
        service.set_ai_service(ai)
        visible = await service.search("005930", "삼성전자", None, automation={
            "auto_analyze": True, "auto_recent_limit": 10,
            "provider": "gemini", "model": "flash",
        })
        for collection, value in store.values:
            if collection == "news_sync":
                value["document"]["checked_at"] = "2026-01-01T00:00:00+00:00"

        await service.refresh_once()

        self.assertEqual("https://o/n3", visible[0]["original_link"])
        self.assertEqual([], ai.calls)

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
        self.assertEqual("005930", service._job_runner._priority_stock_code)

    async def test_selected_stock_is_rechecked_after_one_minute(self) -> None:
        client, store = _NewsClient(), _Store()
        service = CentralNewsService(client, store)  # type: ignore[arg-type]
        await service.search("005930", "삼성전자", None)
        for collection, value in store.values:
            if collection == "news_sync":
                value["document"]["checked_at"] = (
                    datetime.now(UTC) - timedelta(seconds=61)
                ).isoformat()

        await service.search("005930", "삼성전자", None)

        self.assertEqual(2, client.calls)

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
        client, store, ai = _NewsClient(
            datetime.now(UTC), title="삼성전자 공급계약 체결", description="500억원 수주",
        ), _Store(), _AIService()
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

    async def test_refresh_does_not_auto_analyze_processing_excluded_provider(self) -> None:
        client, store, ai = _NewsClient(datetime.now(UTC)), _Store(), _AIService()
        service = CentralNewsService(
            client, store, processing_excluded_providers=("o",),
        )  # type: ignore[arg-type]
        service.set_ai_service(ai)
        await service.search("005930", "삼성전자", None, automation={
            "auto_analyze": True, "auto_recent_limit": 10,
            "provider": "gemini", "model": "flash",
        })
        for collection, value in store.values:
            if collection == "news_sync":
                value["document"]["checked_at"] = "2026-01-01T00:00:00+00:00"

        await service.refresh_once()

        self.assertEqual([], ai.calls)

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

    async def test_job_flag_off_keeps_existing_request_driven_analysis(self) -> None:
        store, ai = _JobStore(), _AIService()
        service = CentralNewsService(_NewsClient(), store, jobs_enabled=False)  # type: ignore[arg-type]
        service.set_ai_service(ai)
        values = await service.search("005930", "삼성전자", None, automation={
            "auto_analyze": True, "auto_recent_limit": 10,
            "provider": "gemini", "model": "flash",
        })

        await service._auto_analyze("005930", "삼성전자", values, {
            "auto_analyze": True, "auto_recent_limit": 10,
            "provider": "gemini", "model": "flash",
        })

        self.assertEqual([], store.jobs)
        self.assertEqual(1, len(ai.calls))


if __name__ == "__main__":
    unittest.main()
