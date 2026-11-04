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

    def test_native_page_keeps_body_refs_clock_and_legacy_collector_serialization(self):
        import hashlib
        import json
        from kiwoom_monitor.central_server.news_read_routes import load_stored_news_page, _serialize, _stored_item
        from kiwoom_monitor.infrastructure.central_news_client import _deserialize
        from kiwoom_monitor.infrastructure.persistence.stock_news_repository import StockNewsRepository
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = SQLiteQueryStore(root / "native.sqlite3")
            store.initialize()
            try:
                document = {"stock_code": "005930", "identity": "https://origin/1", "title": "title", "description": "summary",
                            "link": "https://portal/1", "original_link": "https://origin/1",
                            "published_at": "2026-10-28T08:00:00+09:00", "relevant": 1}
                store.upsert_documents("news_article", [{"owner": "005930", "key": "https://origin/1", "collector_id": "oracle",
                                                         "collection_scope": "watchlist", "document": document}])
                article_id = store.load_news_history("article", target="005930")[0]["article_revision_id"]
                body_id = store.save_news_body_revision({"article_revision_id": article_id, "status": "summary_only",
                                                        "body_text": "actual raw summary", "extractor_version": "source-v7", "fetched_at": 101.5})
                with closing(sqlite3.connect(root / "native.sqlite3")) as connection, connection:
                    connection.execute("UPDATE central_news_article_revisions SET available_at=100.25")
                    connection.execute("UPDATE central_news_body_revisions SET available_at=102.75")
                item = load_stored_news_page(store, "005930", "company")["items"][0]
                source = item["input_source"]
                self.assertEqual(article_id, source["article_revision_id"])
                self.assertEqual(body_id, source["body_revision_id"])
                self.assertEqual((100.25, 101.5, 102.75), (source["article_available_at"], source["body_fetched_at"], source["body_available_at"]))
                self.assertEqual("summary_only", source["body_status"])
                self.assertEqual("source-v7", source["body_extractor_version"])
                self.assertEqual(hashlib.sha256(b'{"body":"actual raw summary"}').hexdigest(), source["body_content_hash"])
                local = root / "pc.sqlite3"
                StockNewsRepository(local).upsert("005930", (_deserialize(item),))
                reopened = StockNewsRepository(local).load("005930")[0]
                self.assertEqual(source, reopened.input_source)
                self.assertNotIn("input_source", _serialize(reopened), "legacy collector content/hash serializer stays explicit")
                self.assertEqual(source, _serialize(reopened, include_input_source=True)["input_source"])
                self.assertIsNone(_stored_item("company", document).input_source)
                original_history = store.load_news_history("article", target="005930")
                echoed = {**document, "input_source_contract": "v1", "input_source_json": json.dumps(source)}
                store.upsert_documents("news_article", [{"owner": "005930", "key": "https://origin/1", "collector_id": "oracle",
                                                         "collection_scope": "local_projection_sync", "document": echoed}])
                repeated = load_stored_news_page(store, "005930", "company")["items"][0]["input_source"]
                self.assertEqual(source, repeated, "native source echo must not add redundant reported metadata on every poll")
                self.assertEqual(original_history, store.load_news_history("article", target="005930"))
                reported = {"version": 1, "article_revision_id": "reported-foreign-article", "body_revision_id": "reported-foreign-body",
                            "article_available_at": 12.5, "body_available_at": 13.75}
                copied = {**document, "input_source_contract": "v1", "input_source_json": json.dumps(reported)}
                store.upsert_documents("news_article", [{"owner": "005930", "key": "https://origin/1", "collector_id": "oracle",
                                                         "collection_scope": "local_projection_sync", "document": copied}])
                self.assertEqual(original_history, store.load_news_history("article", target="005930"))
                same_content = load_stored_news_page(store, "005930", "company")["items"][0]["input_source"]
                self.assertEqual(article_id, same_content["article_revision_id"])
                self.assertEqual(body_id, same_content["body_revision_id"])
                self.assertEqual(102.75, same_content["body_available_at"])
                self.assertEqual("reported-foreign-body", same_content["reported_source"]["body_revision_id"])
                store.upsert_documents("news_article", [{"owner": "005930", "key": "https://origin/1", "collector_id": "oracle",
                                                         "collection_scope": "local_projection_sync", "document": {**copied, "title": "different actual content"}}])
                mismatch = load_stored_news_page(store, "005930", "company")["items"][0]["input_source"]
                self.assertEqual(article_id, mismatch["article_revision_id"])
                self.assertNotIn("reported_source", mismatch, "a current copied claim must match the selected original content")
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
