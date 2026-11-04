"""HTTP request/response adapters for the app-owned news and AI services."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
from typing import Any, TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from .ai_service import CentralAIService
    from .news_service import CentralNewsService


class NewsSearchRequest(BaseModel):
    stock_code: str = Field(min_length=6, max_length=12)
    stock_name: str = Field(min_length=1, max_length=100)
    since: datetime | None = None
    ai_auto_analyze: bool = False
    ai_auto_recent_limit: int = Field(default=10, ge=1, le=1000)
    ai_provider: str = Field(default="none", max_length=20)
    ai_model: str = Field(default="", max_length=100)


class NewsStoredPageRequest(NewsSearchRequest):
    offset: int = Field(default=0, ge=0, le=99_800)


class AIEventInput(BaseModel):
    identity: str = Field(min_length=1, max_length=2000)
    title: str = Field(max_length=1000)
    body: str = Field(default="", max_length=2_000_000)
    body_hash: str = Field(default="", max_length=128)
    body_status: str = ""
    description: str = ""
    link: str = ""
    original_link: str = ""
    published_at: str | None = None
    article_revision_id: str = ""
    body_revision_id: str = ""
    input_source: dict[str, Any] | None = None
    articles: list[dict[str, Any]] = Field(default_factory=list, max_length=100)


    def as_reported_input(self) -> dict[str, Any]:
        # HTTP callers report source evidence. These IDs never grant native
        # article/BODY/job write authority; internal attempt pins stay separate.
        from kiwoom_monitor.domain.news_ai_contract import decode_news_input_source
        # Keep legacy defaults in the service call; newer evidence is forwarded
        # only when the HTTP caller explicitly supplied that field.
        legacy_fields = {"identity", "title", "body", "body_hash", "articles"}
        event = self.model_dump(include=legacy_fields | self.model_fields_set)
        claimed = {name: event.pop(name, "") for name in ("article_revision_id", "body_revision_id")}
        if any(claimed.values()):
            prior = decode_news_input_source(event.get("input_source"))
            if prior and all(not value or prior.get(name) == value for name, value in claimed.items()):
                event["input_source"] = prior
            else:
                event["input_source"] = {"version": 1, **claimed,
                                         **({"reported_source": prior} if prior is not None else {})}
        return event


class AIAnalysisRequest(BaseModel):
    stock_code: str = Field(min_length=6, max_length=12)
    stock_name: str = Field(min_length=1, max_length=100)
    provider: str = Field(default="", max_length=20)
    model: str = Field(default="", max_length=100)
    events: list[AIEventInput] = Field(min_length=1, max_length=20)
    article_count: int = Field(default=0, ge=0, le=10_000)


def create_news_search_router(
    news_service: CentralNewsService | None, authorize: Callable[..., None],
    *, read_authorize=None, store=None,
) -> APIRouter:
    router = APIRouter()

    @router.post("/api/v1/news/search", dependencies=[Depends(authorize)])
    async def news_search(query: NewsSearchRequest) -> dict[str, object]:
        if news_service is None:
            raise HTTPException(status_code=503, detail="서버에 네이버 뉴스 API 키가 설정되지 않았습니다.")
        try:
            items = await news_service.search(query.stock_code, query.stock_name, query.since, automation={
                "auto_analyze": query.ai_auto_analyze,
                "auto_recent_limit": query.ai_auto_recent_limit,
                "provider": query.ai_provider,
                "model": query.ai_model,
            })
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        return {"stock_code": query.stock_code, "items": items}

    @router.post("/api/v1/news/stored-page")
    async def news_stored_page(query: NewsStoredPageRequest,
                              access_scope: str | None = Depends(read_authorize or authorize)) -> dict[str, object]:
        if access_scope == "query":
            if query.model_fields_set & {"ai_auto_analyze", "ai_auto_recent_limit", "ai_provider", "ai_model"}:
                raise HTTPException(status_code=403, detail="뉴스 처리 설정 권한이 필요합니다.")
            if store is None:
                raise HTTPException(status_code=503, detail="서버 뉴스 저장소가 준비되지 않았습니다.")
            from .news_read_routes import load_stored_news_page
            page = await asyncio.to_thread(load_stored_news_page, store,
                query.stock_code, query.stock_name, offset=query.offset)
            return {"stock_code": query.stock_code, **page}
        if news_service is None:
            raise HTTPException(status_code=503, detail="서버 뉴스 저장소가 준비되지 않았습니다.")
        page = await news_service.stored_page(query.stock_code, query.stock_name, offset=query.offset,
            automation={
                "auto_analyze": query.ai_auto_analyze,
                "auto_recent_limit": query.ai_auto_recent_limit,
                "provider": query.ai_provider,
                "model": query.ai_model,
            })
        return {"stock_code": query.stock_code, **page}

    return router


def create_news_analysis_router(
    ai_service: CentralAIService | None, authorize: Callable[..., None],
) -> APIRouter:
    router = APIRouter()

    @router.post("/api/v1/news/analyze", dependencies=[Depends(authorize)])
    async def news_analyze(query: AIAnalysisRequest) -> dict[str, object]:
        if ai_service is None:
            raise HTTPException(status_code=503, detail="서버에 AI API 키가 설정되지 않았습니다.")
        from kiwoom_monitor.infrastructure.news_ai import NewsAIProviderError
        try:
            return await ai_service.analyze(
                query.stock_code, query.stock_name, query.provider, query.model,
                [value.as_reported_input() for value in query.events], query.article_count,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except NewsAIProviderError as error:
            raise HTTPException(status_code=error.status_code, detail=str(error)) from error
        except RuntimeError as error:
            from kiwoom_monitor.infrastructure.chatgpt_auth import ChatGPTPlanError
            if isinstance(error, ChatGPTPlanError):
                raise HTTPException(status_code=error.status_code if 400 <= error.status_code < 600 else 503,
                                    detail=str(error)) from None
            raise HTTPException(status_code=502, detail=str(error)) from error

    return router
