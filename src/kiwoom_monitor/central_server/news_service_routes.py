"""HTTP request/response adapters for the app-owned news and AI services."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from kiwoom_monitor.infrastructure.news_ai import NewsAIProviderError

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
    articles: list[dict[str, str]] = Field(default_factory=list, max_length=100)


class AIAnalysisRequest(BaseModel):
    stock_code: str = Field(min_length=6, max_length=12)
    stock_name: str = Field(min_length=1, max_length=100)
    provider: str = Field(default="", max_length=20)
    model: str = Field(default="", max_length=100)
    events: list[AIEventInput] = Field(min_length=1, max_length=20)
    article_count: int = Field(default=0, ge=0, le=10_000)


def create_news_search_router(
    news_service: CentralNewsService | None, authorize: Callable[..., None],
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

    @router.post("/api/v1/news/stored-page", dependencies=[Depends(authorize)])
    async def news_stored_page(query: NewsStoredPageRequest) -> dict[str, object]:
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
        try:
            return await ai_service.analyze(
                query.stock_code, query.stock_name, query.provider, query.model,
                [value.model_dump() for value in query.events], query.article_count,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except NewsAIProviderError as error:
            raise HTTPException(status_code=error.status_code, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

    return router
