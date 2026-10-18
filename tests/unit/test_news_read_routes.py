from __future__ import annotations

import unittest
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException

from kiwoom_monitor.central_server.news_read_routes import create_news_read_router
from kiwoom_monitor.central_server.database import SQLiteQueryStore


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
    async def test_revision_cursor_keeps_tied_rows_and_existing_as_of_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "news.sqlite3"
            store = SQLiteQueryStore(path)
            store.initialize()
            try:
                store.upsert_documents("news_article", [
                    {"owner": "005930", "key": str(index), "document": {
                        "identity": str(index), "title": f"title {index}",
                        "published_at": "2026-10-18T00:00:00+09:00",
                    }} for index in range(1005)
                ])
                with closing(sqlite3.connect(path)) as connection, connection:
                    connection.execute("UPDATE central_news_article_revisions SET available_at=10.0")
                app = FastAPI()
                app.include_router(create_news_read_router(store, lambda: None))
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
                    first = await client.get("/api/v1/news/history/article?limit=1000&as_of=10")
                    rows = first.json()["revisions"]
                    second = await client.get("/api/v1/news/history/article", params={
                        "limit": 1000, "as_of": 10, "before_revision_id": rows[-1]["article_revision_id"],
                    })
                    older = await client.get("/api/v1/news/history/article?as_of=9")
                self.assertEqual((200, 200, 200), (first.status_code, second.status_code, older.status_code))
                self.assertEqual(1000, len(rows))
                self.assertEqual(5, len(second.json()["revisions"]))
                self.assertEqual(1005, len({value["article_revision_id"] for value in rows + second.json()["revisions"]}))
                self.assertEqual([], older.json()["revisions"])
            finally:
                store.close()

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
