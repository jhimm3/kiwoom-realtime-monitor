"""Historical news processing HTTP contracts; the app owns the native store."""
from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .market_observations import KST


class HistoricalNewsProcessingStore(Protocol):
    def claim_external_historical_news_job(
        self, stage: str, excluded_codes: tuple[str, ...] = (), scope: str = "all",
    ) -> dict[str, Any] | None: ...

    def load_news_article_revision(self, article_revision_id: str) -> dict[str, Any] | None: ...

    def load_news_body_revision(self, body_revision_id: str) -> dict[str, Any] | None: ...

    def save_historical_market_news_batch(
        self, source: str, target_date: str, batch_id: str,
        items: list[dict[str, Any]], processing_owner: str = "nas",
    ) -> dict[str, Any]: ...

    def complete_external_historical_news_job(self, value: dict[str, Any]) -> dict[str, str]: ...


class HistoricalNewsJobResult(BaseModel):
    job_key: str = Field(min_length=64, max_length=64)
    attempts: int = Field(ge=1)
    stage: str = Field(pattern="^(BODY|RULE)$")
    body_text: str = Field(default="", max_length=2_000_000)
    body_status: str = Field(default="", max_length=20)
    fetched_at: float | None = None
    original_published_at: str = Field(default="", max_length=100)
    source_url: str = Field(default="", max_length=2000)
    assessment: dict[str, Any] | None = None
    core_sentences: list[str] | None = Field(default=None, max_length=10)
    rule_result: dict[str, Any] | None = None
    error: str = Field(default="", max_length=1000)


class HistoricalMarketNewsBatch(BaseModel):
    source: str = Field(pattern="^(flash|world)$")
    target_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    batch_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    processing_owner: str = Field(default="nas", pattern="^(nas|pc)$")
    items: list[dict[str, Any]] = Field(min_length=1, max_length=100)


def create_historical_news_processing_router(
    store: HistoricalNewsProcessingStore, authorize: Callable[..., None],
) -> APIRouter:
    router = APIRouter()

    @router.post("/api/v1/news/historical-jobs/claim", dependencies=[Depends(authorize)])
    async def claim_historical_news_job(
        stage: str = Query(pattern="^(BODY|RULE)$"),
        excluded_codes: str = Query(default="", max_length=6000),
        scope: str = Query(default="all", pattern="^(all|pc_market|pc_search|pc)$"),
    ) -> dict[str, object]:
        excluded = tuple(dict.fromkeys(code.strip().upper() for code in excluded_codes.split(",") if code.strip()))
        if len(excluded) > 500 or any(not re.fullmatch(r"[0-9A-Z]{6}", code) for code in excluded):
            raise HTTPException(status_code=422, detail="제외 종목코드 형식이 올바르지 않습니다.")
        job = await asyncio.to_thread(store.claim_external_historical_news_job, stage, excluded, scope)
        if job is None:
            return {"job": None}
        article = await asyncio.to_thread(store.load_news_article_revision, job["article_revision_id"])
        if article is None:
            raise HTTPException(status_code=409, detail="기사 리비전이 없어 작업을 처리할 수 없습니다.")
        response: dict[str, object] = {"job": job, "article": article}
        if stage == "RULE":
            body_id = str(job["payload"].get("body_revision_id") or "")
            response["body"] = await asyncio.to_thread(store.load_news_body_revision, body_id)
        return response

    @router.post("/api/v1/news/historical-market-articles", dependencies=[Depends(authorize)])
    async def import_historical_market_articles(batch: HistoricalMarketNewsBatch) -> dict[str, Any]:
        for item in batch.items:
            document = item.get("document")
            published = str(document.get("published_at") or "") if isinstance(document, dict) else ""
            try:
                publication_time = datetime.fromisoformat(published)
                matching_date = (publication_time.tzinfo is not None
                                 and publication_time.astimezone(KST).date().isoformat() == batch.target_date)
            except ValueError:
                matching_date = False
            if (not isinstance(document, dict) or not str(item.get("identity") or "")
                    or not str(document.get("title") or "")
                    or not matching_date
                    or not isinstance(item.get("targets"), list)):
                raise HTTPException(status_code=422, detail="과거 시황 기사 형식·날짜가 올바르지 않습니다.")
        try:
            return await asyncio.to_thread(store.save_historical_market_news_batch,
                                           batch.source, batch.target_date, batch.batch_id, batch.items,
                                           batch.processing_owner)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.post("/api/v1/news/historical-jobs/complete", dependencies=[Depends(authorize)])
    async def complete_historical_news_job(result: HistoricalNewsJobResult) -> dict[str, str]:
        try:
            return await asyncio.to_thread(store.complete_external_historical_news_job, result.model_dump())
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    return router
