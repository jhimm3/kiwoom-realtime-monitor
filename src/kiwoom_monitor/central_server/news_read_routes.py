"""Authenticated native news reads and the shared accepted-result codec."""
from __future__ import annotations

from kiwoom_monitor.domain.news_ai_contract import decode_news_input_source

import asyncio
from datetime import UTC, datetime
from kiwoom_monitor.application.news_analysis import NewsAssessment, assess_stock_news
from kiwoom_monitor.infrastructure.naver_news import StockNewsItem, news_provider, news_provider_domain
from collections.abc import Callable
from typing import Any, Protocol

from fastapi import APIRouter, Depends, HTTPException, Query


class NewsReadStore(Protocol):
    def load_stock_news_articles(
        self, stock_code: str, *, limit: int = 1000,
    ) -> list[dict[str, Any]]: ...

    def load_confirmed_news_articles(
        self, stock_code: str, *, limit: int = 1000,
    ) -> list[dict[str, Any]]: ...

    def load_news_history(
        self, kind: str, *, target: str = "", identity: str = "",
        available_at: float | None = None, limit: int = 100, before_revision_id: str = "",
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
    *, read_authorize: Callable[..., Any] | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/v1/news/history/{kind}", dependencies=[Depends(read_authorize or authorize)])
    async def news_history(
        kind: str,
        target: str = Query(default="", max_length=200),
        identity: str = Query(default="", max_length=2000),
        stock_code: str = Query(default="", max_length=20),
        as_of: float | None = Query(default=None, ge=0.0),
        limit: int = Query(default=100, ge=1, le=1000),
        before_revision_id: str = Query(default="", max_length=200),
    ) -> dict[str, object]:
        if kind not in {"article", "body", "ai", "event", "membership"}:
            raise HTTPException(status_code=404, detail="지원하지 않는 뉴스 이력 종류입니다.")
        cursor = {"before_revision_id": before_revision_id} if before_revision_id else {}
        values = await asyncio.to_thread(
            store.load_news_history, kind, target=target, identity=identity,
            available_at=as_of, limit=limit, **cursor,
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

    @router.get("/api/v1/news/market-feed", dependencies=[Depends(read_authorize or authorize)])
    async def market_news_feed(
        source: str = Query(pattern="^(common|flash|world)$"),
        limit: int = Query(default=200, ge=1, le=1000),
    ) -> dict[str, object]:
        return {"source": source, "items": await asyncio.to_thread(
            store.load_market_news_feed, source, limit=limit,
        )}

    return router


def load_cached_news(store: NewsReadStore, code: str, name: str, since: datetime | None,
                 limit: int = 1000) -> list[dict[str, Any]]:
    documents = store.load_stock_news_articles(code, limit=limit)
    values = [
        _serialize(_stored_item(str(value.get("stock_name") or name), value), include_input_source=True)
        for value in documents
    ]
    if since is None:
        return values
    cutoff = since.astimezone(UTC)
    result: list[dict[str, Any]] = []
    for value in values:
        try:
            published = datetime.fromisoformat(str(value.get("published_at", ""))).astimezone(UTC)
        except ValueError:
            continue
        if published > cutoff:
            result.append(value)
    return result


def merge_confirmed_news(
    store: NewsReadStore, code: str, name: str, owner_values: list[dict[str, Any]],
    since: datetime | None, limit: int = 1000,
) -> list[dict[str, Any]]:
    stored = store.load_confirmed_news_articles(code, limit=limit)
    merged = {_identity(value): value for value in owner_values}
    for document in stored:
        value = _serialize(_stored_item(name, document), include_input_source=True)
        identity = _identity(value)
        existing = merged.get(identity)
        if existing is None:
            merged[identity] = value
        else:
            # 공급자 응답의 최신 제목·링크는 유지하되 NAS 원문으로 계산한
            # 관련성·방향 판정은 같은 기사에 반영한다.
            for key in (
                "relevant", "category", "outlook", "reason",
                "relevance_score", "outlook_score",
            ):
                existing[key] = value[key]
            assessment = document.get("_assessment")
            if (isinstance(assessment, dict)
                    and assessment.get("published_at_source") == "article_html"
                    and value.get("published_at")):
                existing["published_at"] = value["published_at"]
    cutoff = since.astimezone(UTC) if since is not None else None
    values = []
    for value in merged.values():
        published = _published_at(value)
        if cutoff is not None and (published is None or published <= cutoff):
            continue
        values.append(value)
    return sorted(values, key=_news_sort_key)[:limit]


def _identity(value: dict[str, Any]) -> str:
    return str(value.get("original_link") or value.get("link") or f'{value.get("published_at", "")}|{value.get("title", "")}')

def _serialize(item: StockNewsItem, *, include_input_source: bool = False) -> dict[str, Any]:
    assessment = item.assessment
    value = {
        "title": item.title, "description": item.description, "link": item.link,
        "original_link": item.original_link,
        "publisher_domain": news_provider_domain(item), "publisher_name": news_provider(item),
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "relevant": int(assessment.relevant), "category": assessment.category,
        "outlook": assessment.outlook, "reason": assessment.reason,
        "relevance_score": assessment.relevance_score, "outlook_score": assessment.outlook_score,
    }
    if include_input_source:
        value["input_source"] = decode_news_input_source(getattr(item, "input_source", None))
    return value

def _stored_item(stock_name: str, value: dict[str, Any]) -> StockNewsItem:
    from kiwoom_monitor.infrastructure.article_text import clean_article_text

    title, description = str(value.get("title", "")), str(value.get("description", ""))
    body = (
        clean_article_text(str(value.get("_body_text", "")))
        if value.get("_body_status") == "fulltext" else ""
    )
    if len(body) < 40:
        body = ""
    stored = value.get("_assessment")
    assessed = stored.get("assessment") if isinstance(stored, dict) else None
    if isinstance(assessed, dict):
        assessment = NewsAssessment(
            bool(assessed.get("relevant", False)), str(assessed.get("category") or ""),
            str(assessed.get("outlook") or ""), str(assessed.get("reason") or ""),
            int(assessed.get("relevance_score") or 0), int(assessed.get("outlook_score") or 0),
        )
    elif value.get("stock_code") != "GLOBAL" and "relevant" in value:
        assessment = NewsAssessment(
            bool(value.get("relevant")), str(value.get("category") or ""),
            str(value.get("outlook") or ""), str(value.get("reason") or ""),
            int(value.get("relevance_score") or 0), int(value.get("outlook_score") or 0),
        )
    else:
        # Legacy global articles may predate persisted per-target assessment.
        assessment = assess_stock_news(stock_name, title, description, article_body=body)
    return StockNewsItem(
        title, description, str(value.get("link", "")), str(value.get("original_link", "")),
        _published_at({"published_at": stored.get("published_at")})
        if isinstance(stored, dict) and stored.get("published_at") else _published_at(value),
        assessment,
        input_source=decode_news_input_source(value.get("_input_source", value.get("input_source_json"))),
    )

def _published_at(value: dict[str, Any]) -> datetime | None:
    raw = value.get("published_at")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)

def _news_sort_key(value: dict[str, Any]) -> tuple[int, float, str]:
    published = _published_at(value)
    return (
        1 if published is None else 0,
        -(published.timestamp() if published is not None else 0.0),
        _identity(value),
    )


def load_stored_news_page(store: NewsReadStore, code: str, name: str, *, offset: int = 0, limit: int = 200) -> dict[str, Any]:
    """Read the existing accepted owner/confirmed merge without producer commands."""
    if offset < 0 or not 1 <= limit <= 200:
        raise ValueError("NEWS_PAGE_RANGE_INVALID")
    fetch_limit = offset + limit + 1
    owner = load_cached_news(store, code, name, None, fetch_limit)
    merged = merge_confirmed_news(store, code, name, owner, None, fetch_limit)
    return {"items": merged[offset:offset + limit],
            "next_offset": offset + limit if len(merged) > offset + limit else None}
