"""NAS collection of Naver Stock's date-scoped market news feeds."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from time import time
from typing import Any, Callable
from urllib.error import HTTPError
from zoneinfo import ZoneInfo

from kiwoom_monitor.application.news_analysis import assess_stock_news
from kiwoom_monitor.infrastructure.naver_news import StockNewsItem
from kiwoom_monitor.infrastructure.naver_stock_market_news import (
    ENDPOINT, PAGE_SIZE, SOURCES, MarketNewsPage, fetch_page,
)

from .news_sources import _marker, _page_items


LOGGER = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")


def _source_id(source: str, target_date: str) -> str:
    return f"naver-stock:{source}:{target_date}"


def _news_items(page: MarketNewsPage) -> list[StockNewsItem]:
    return [StockNewsItem(
        article.title, article.summary, article.url, "",
        datetime.fromisoformat(article.published_at),
        assess_stock_news("시황", article.title, article.summary),
    ) for article in page.articles]


class MarketFeedNewsCollector:
    """Owns independent FLASH/WORLD cursors and a low-priority async poll loop."""

    def __init__(self, store: Any, *, poll_seconds: int = 300,
                 fetcher: Callable[..., MarketNewsPage] = fetch_page,
                 processing_excluded_providers: tuple[str, ...] = (),
                 enabled: bool = True, endpoints: dict[str, str] | None = None) -> None:
        self._store = store
        self._poll_seconds = max(60, int(poll_seconds))
        self._fetcher = fetcher
        self._enabled = bool(enabled)
        self._endpoints = dict(endpoints or ENDPOINT)
        self._excluded = processing_excluded_providers
        self._closing = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is None:
            self._closing.clear()
            self._task = asyncio.create_task(self._loop(), name="central-naver-stock-market-news")

    async def close(self) -> None:
        self._closing.set()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    def update_excluded_providers(self, values: tuple[str, ...]) -> None:
        self._excluded = values

    def update_settings(self, *, enabled: bool, endpoints: dict[str, str]) -> None:
        self._enabled = bool(enabled)
        self._endpoints = dict(endpoints)

    async def run_once(self, *, now: datetime | None = None) -> None:
        if not self._enabled:
            return
        current = (now or datetime.now(KST)).astimezone(KST)
        dates = [current.date().isoformat()]
        if current.hour < 2:
            dates.append((current.date() - timedelta(days=1)).isoformat())
        for target_date in dates:
            for source in SOURCES:
                if self._closing.is_set() or not self._enabled:
                    return
                try:
                    await self._collect(source, target_date)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    LOGGER.exception("네이버 증권 %s %s 뉴스 목록 수집 실패", source, target_date)

    async def _collect(self, source: str, target_date: str) -> None:
        source_id = _source_id(source, target_date)
        cursor = await asyncio.to_thread(self._store.load_news_source_cursor, source_id) or {}
        if float(cursor.get("next_schedule_at") or 0) > time():
            return
        page_number = max(1, int(cursor.get("next_start") or 1))
        old_marker = (str(cursor.get("cursor_published_at") or ""), str(cursor.get("cursor_identity") or ""))
        pending = (str(cursor.get("pending_published_at") or ""), str(cursor.get("pending_identity") or ""))
        run_id, checked_at = uuid.uuid4().hex, time()
        last_marker: tuple[str, str] | None = None
        catalog = await asyncio.to_thread(self._catalog)
        pages_this_run = 0
        while page_number <= 300 and pages_this_run < 10 and not self._closing.is_set() and self._enabled:
            try:
                for attempt in range(3):
                    try:
                        if self._fetcher is fetch_page:
                            page = await asyncio.to_thread(
                                self._fetcher, source, target_date, page_number,
                                endpoints=self._endpoints,
                            )
                        else:
                            page = await asyncio.to_thread(self._fetcher, source, target_date, page_number)
                        break
                    except HTTPError as error:
                        if error.code not in {429, 500, 502, 503, 504} or attempt == 2:
                            raise
                        await asyncio.sleep(2 ** attempt)
                if page.invalid_count and not page.articles:
                    raise ValueError(f"{page.invalid_count} invalid or other-date rows")
                if page.older_count and page_number == 1 and not page.articles:
                    raise ValueError("first page contains only earlier-date rows")
                items = _news_items(page)
                marker = _marker(items[-1]) if items else None
                repeat = marker is not None and marker == last_marker
                if repeat:
                    items = []
                elif marker is not None:
                    last_marker = marker
                if not pending[1] and items:
                    pending = _marker(items[0])
                reached = bool(old_marker[1]) and any(_marker(item) == old_marker for item in items)
                exhausted = (len(items) < PAGE_SIZE[source] or bool(page.older_count)) and not repeat
                capped = page_number == 300
                completed = reached or exhausted or repeat or capped
                truncated = repeat or capped
                promoted = pending if completed else old_marker
                accepted = await asyncio.to_thread(
                    _page_items, items, catalog, self._excluded, retain_market_articles=True,
                )
                now = time()
                await asyncio.to_thread(self._store.save_news_source_page, {
                    "source_id": source_id, "scope": "naver_stock_market", "query_text": source,
                    "run_id": run_id, "page_start": page_number, "checked_at": checked_at,
                    "completed_at": now, "request_count": 1, "budget_remaining": 0,
                    "coverage": "repeat" if repeat else "page_limit" if capped else "cursor_reached" if reached else "previous_date_boundary" if page.older_count else "exhausted" if exhausted else "continuing",
                    "truncated": truncated, "error": "",
                    "cursor_published_at": promoted[0] or None, "cursor_identity": promoted[1],
                    "pending_published_at": None if completed else (pending[0] or None),
                    "pending_identity": "" if completed else pending[1],
                    "next_start": 1 if completed else page_number + 1,
                    "next_schedule_at": now + self._poll_seconds if completed else now,
                    "last_success": now if completed else cursor.get("last_success"),
                    "document": {"target_date": target_date, "source": source,
                                 "raw_items": len(page.articles), "invalid_items": page.invalid_count,
                                 "older_date_items": page.older_count,
                                 "stored_items": len(accepted),
                                 "raw_sha256": hashlib.sha256(page.raw_json.encode()).hexdigest(),
                                 "scope_statement": "Naver Stock market category feed; date-scoped"},
                    "items": accepted,
                })
                if completed:
                    return
                page_number += 1
                pages_this_run += 1
                await asyncio.sleep(0.7)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                now = time()
                await asyncio.to_thread(self._store.save_news_source_page, {
                    "source_id": source_id, "scope": "naver_stock_market", "query_text": source,
                    "run_id": run_id, "page_start": page_number, "checked_at": checked_at,
                    "completed_at": now, "request_count": 0, "budget_remaining": 0,
                    "coverage": "error", "truncated": False,
                    "error": f"{type(error).__name__}: {error}",
                    "cursor_published_at": old_marker[0] or None, "cursor_identity": old_marker[1],
                    "pending_published_at": pending[0] or None, "pending_identity": pending[1],
                    "next_start": page_number, "next_schedule_at": now + self._poll_seconds,
                    "last_success": cursor.get("last_success"),
                    "document": {"target_date": target_date, "source": source}, "items": [],
                })
                raise

    def _catalog(self) -> tuple[tuple[str, str], ...]:
        rows = self._store.load_documents("stock_catalog", "krx", 10_000)
        return tuple((str(doc.get("code") or ""), str(doc.get("name") or ""))
                     for row in rows if isinstance(doc := row.get("document"), dict)
                     if doc.get("code") and doc.get("name"))

    async def _loop(self) -> None:
        while not self._closing.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("네이버 증권 시장 뉴스 수집 주기 실패")
            try:
                await asyncio.wait_for(self._closing.wait(), timeout=60)
            except TimeoutError:
                pass
