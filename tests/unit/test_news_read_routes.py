from __future__ import annotations

import unittest
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException

from kiwoom_monitor.central_server.news_read_routes import create_news_read_router


class MinimalNewsReadStore:
    """Only the four reads required by the router; deliberately no QueryStore base."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def load_news_history(self, kind: str, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("load_news_history", (kind,), kwargs))
        return [{"revision_id": "article-r1"}]

    def load_document(self, collection: str, owner: str, key: str) -> dict[str, Any]:
        self.calls.append(("load_document", (collection, owner, key), {}))
        return {"document": {"category": "supply_contract"}}

    def load_news_source_diagnostics(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("load_news_source_diagnostics", (), kwargs))
        return {"scope": "query_set", "runs": []}

    def load_market_news_feed(self, source: str, *, limit: int = 200) -> list[dict[str, Any]]:
        self.calls.append(("load_market_news_feed", (source,), {"limit": limit}))
        return [{"title": "고정 속보"}]


class NewsReadRoutesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.store = MinimalNewsReadStore()

        def authorize(authorization: str = Header(default="")) -> None:
            if authorization != "Bearer route-fixture-token":
                raise HTTPException(status_code=401, detail="인증이 필요합니다.")

        app = FastAPI()
        app.include_router(create_news_read_router(self.store, authorize))
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://routes.test",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()

    async def test_router_uses_only_the_four_read_methods_and_keeps_response_mapping(self) -> None:
        headers = {"Authorization": "Bearer route-fixture-token"}
        history = await self.client.get(
            "/api/v1/news/history/body?target=article-r1&stock_code=005930&limit=5",
            headers=headers,
        )
        sources = await self.client.get(
            "/api/v1/news/sources?source_id=flash&days=31&limit=20", headers=headers,
        )
        feed = await self.client.get(
            "/api/v1/news/market-feed?source=flash&limit=20", headers=headers,
        )

        self.assertEqual(200, history.status_code)
        self.assertEqual({
            "kind": "body", "target": "article-r1", "as_of": None,
            "known": True, "revisions": [{"revision_id": "article-r1"}],
            "assessment": {"category": "supply_contract"},
        }, history.json())
        self.assertEqual(200, sources.status_code)
        self.assertEqual({"scope": "query_set", "runs": []}, sources.json())
        self.assertEqual(200, feed.status_code)
        self.assertEqual({"source": "flash", "items": [{"title": "고정 속보"}]}, feed.json())
        self.assertEqual([
            ("load_news_history", ("body",), {
                "target": "article-r1", "identity": "", "available_at": None, "limit": 5,
            }),
            ("load_document", ("news_assessment", "005930", "article-r1"), {}),
            ("load_news_source_diagnostics", (), {"source_id": "flash", "days": 31, "limit": 20}),
            ("load_market_news_feed", ("flash",), {"limit": 20}),
        ], self.store.calls)

    async def test_authentication_and_validation_reject_before_store_access(self) -> None:
        denied = await self.client.get("/api/v1/news/history/article")
        self.assertEqual(401, denied.status_code)

        headers = {"Authorization": "Bearer route-fixture-token"}
        unsupported = await self.client.get(
            "/api/v1/news/history/unsupported", headers=headers,
        )
        invalid_query = await self.client.get(
            "/api/v1/news/sources?days=32", headers=headers,
        )

        self.assertEqual(404, unsupported.status_code)
        self.assertEqual(422, invalid_query.status_code)
        self.assertEqual([], self.store.calls)


if __name__ == "__main__":
    unittest.main()
