"""Authenticated HTTP reads of an archive whose setup is owned by the app."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from .historical_news_archive import (
    ArchiveUnavailableError, HistoricalNewsArchiveReader, InvalidArchiveCursorError,
)


def create_historical_news_archive_router(
    historical_archive: HistoricalNewsArchiveReader | None,
    authorize: Callable[..., None],
) -> APIRouter:
    router = APIRouter()

    def archive_reader() -> HistoricalNewsArchiveReader:
        if historical_archive is None:
            raise HTTPException(status_code=503, detail="HISTORICAL_NEWS_ARCHIVE_UNAVAILABLE")
        from .diagnostic_workloads import is_paused
        if is_paused("historical_news_archive"):
            raise HTTPException(status_code=503, detail="HISTORICAL_NEWS_ARCHIVE_PAUSED")
        return historical_archive

    @router.get("/api/v1/news/historical-archive/search", dependencies=[Depends(authorize)])
    async def historical_archive_search(
        stock_code: str = Query(pattern=r"^[0-9A-Z]{6}$"),
        limit: int = Query(default=100, ge=1, le=200),
        cursor: str | None = Query(default=None, max_length=2048),
    ) -> dict[str, object]:
        reader = archive_reader()
        try:
            return await asyncio.to_thread(reader.search_page, stock_code, limit=limit, cursor=cursor)
        except InvalidArchiveCursorError:
            raise HTTPException(status_code=400, detail="HISTORICAL_NEWS_ARCHIVE_CURSOR_INVALID") from None
        except (ArchiveUnavailableError, OSError, sqlite3.DatabaseError):
            raise HTTPException(status_code=503, detail="HISTORICAL_NEWS_ARCHIVE_UNAVAILABLE") from None

    @router.get("/api/v1/news/historical-archive/articles/{article_revision_id}",
                dependencies=[Depends(authorize)])
    async def historical_archive_article(
        article_revision_id: str,
        dataset_id: str = Query(min_length=1, max_length=128),
        body_revision_id: str | None = Query(default=None, max_length=128),
    ) -> dict[str, object]:
        reader = archive_reader()
        if dataset_id != reader.dataset_id:
            raise HTTPException(status_code=409, detail="HISTORICAL_NEWS_ARCHIVE_DATASET_CHANGED")
        try:
            result = await asyncio.to_thread(
                reader.article_by_id, article_revision_id, body_revision_id=body_revision_id,
            )
        except ValueError as error:
            if isinstance(error, ArchiveUnavailableError):
                raise HTTPException(status_code=503, detail="HISTORICAL_NEWS_ARCHIVE_UNAVAILABLE") from None
            raise HTTPException(status_code=400, detail="HISTORICAL_NEWS_ARCHIVE_ID_MISMATCH") from None
        except (OSError, sqlite3.DatabaseError):
            raise HTTPException(status_code=503, detail="HISTORICAL_NEWS_ARCHIVE_UNAVAILABLE") from None
        if result is None:
            raise HTTPException(status_code=404, detail="HISTORICAL_NEWS_ARCHIVE_ARTICLE_NOT_FOUND")
        return result

    return router
