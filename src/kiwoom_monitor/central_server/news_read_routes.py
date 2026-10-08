"""Authenticated news reads, depending only on the four queries they use."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Protocol

from fastapi import APIRouter, Depends, HTTPException, Query


class NewsReadStore(Protocol):
    def load_news_history(
        self, kind: str, *, target: str = "", identity: str = "",
        available_at: float | None = None, limit: int = 100,
    ) -> list[dict[str, Any]]: ...

    def load_document(
        self, collection: str, owner: str, key: str,
    ) -> dict[str, Any] | None: ...

    def load_news_source_diagnostics(
        self, *, source_id: str = "", days: int = 7, limit: int = 100,
    ) -> dict[str, Any]: ...

    def load_market_news_feed(
        self, source: str, *, limit: int = 200,
    ) -> list[dict[str, Any]]: ...


def create_news_read_router(
    store: NewsReadStore, authorize: Callable[..., None],
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/v1/news/history/{kind}", dependencies=[Depends(authorize)])
    async def news_history(
        kind: str,
        target: str = Query(default="", max_length=200),
        identity: str = Query(default="", max_length=2000),
        stock_code: str = Query(default="", max_length=20),
        as_of: float | None = Query(default=None, ge=0.0),
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> dict[str, object]:
        if kind not in {"article", "body", "ai", "event", "membership"}:
            raise HTTPException(status_code=404, detail="지원하지 않는 뉴스 이력 종류입니다.")
        values = await asyncio.to_thread(
            store.load_news_history, kind, target=target, identity=identity,
            available_at=as_of, limit=limit,
        )
        result = {"kind": kind, "target": target, "as_of": as_of,
                  "known": bool(values), "revisions": values}
        if kind == "body" and target and stock_code and as_of is None:
            stored = await asyncio.to_thread(
                store.load_document, "news_assessment", stock_code, target,
            )
            result["assessment"] = stored.get("document") if stored else None
        return result

    @router.get("/api/v1/news/sources", dependencies=[Depends(authorize)])
    async def news_sources(
        source_id: str = Query(default="", max_length=200),
        days: int = Query(default=7, ge=1, le=31),
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> dict[str, object]:
        return await asyncio.to_thread(
            store.load_news_source_diagnostics, source_id=source_id, days=days, limit=limit,
        )

    @router.get("/api/v1/news/market-feed", dependencies=[Depends(authorize)])
    async def market_news_feed(
        source: str = Query(pattern="^(common|flash|world)$"),
        limit: int = Query(default=200, ge=1, le=1000),
    ) -> dict[str, object]:
        return {"source": source, "items": await asyncio.to_thread(
            store.load_market_news_feed, source, limit=limit,
        )}

    return router
