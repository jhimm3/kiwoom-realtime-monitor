"""NAVER query-set collection policy and its independently-lived cursor loop."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, Callable

from kiwoom_monitor.domain.time_basis import as_kst, kst_now
from kiwoom_monitor.infrastructure.naver_news import (
    NaverNewsClient, StockNewsItem,
)
from kiwoom_monitor.infrastructure.krx.stock_catalog import fetch_krx_stock_catalog
from kiwoom_monitor.infrastructure.news_source_documents import (
    TARGET_RULE_VERSION,
    article_identity as _identity,
    article_marker as _marker,
    article_document as _article_document,
    article_targets as _targets,
    page_items as _page_items,
    storage_skip_reason as _storage_skip_reason,
    exact_company_name as _exact_company_name,
    company_name_pattern as _company_name_pattern,
)


DEFAULT_NEWS_QUERY_SET = (
    "증권", "코스피", "코스닥", "상장사", "수주 계약", "유상증자", "인수합병", "실적 전망", "최대주주",
)
LOGGER = logging.getLogger(__name__)


class QuerySetNewsCollector:
    """Configured queries are collected without depending on any desktop selection."""

    def __init__(self, client: NaverNewsClient | None, store: Any, *, queries: tuple[str, ...] = DEFAULT_NEWS_QUERY_SET,
                 enabled: bool = True, poll_seconds: int = 300, hard_limit: int = 24_000,
                 query_limit: int = 16_000,
                 processing_excluded_providers: tuple[str, ...] = (),
                 catalog_loader: Callable[[], tuple[tuple[str, str, str], ...]] = fetch_krx_stock_catalog,
                 catalog_retry_seconds: int = 3600,
                 now_provider: Callable[[], datetime] | None = None,
                 workload_paused: Callable[[str], bool] | None = None) -> None:
        self._client, self._store = client, store
        # Standalone/offline collection uses the common explicit KST fallback.
        self._now = (lambda: as_kst(now_provider())) if now_provider else kst_now
        self._workload_paused = workload_paused or (lambda _workload: False)
        self._queries = _queries(queries)
        self._enabled = bool(enabled)
        self._poll_seconds = max(60, int(poll_seconds))
        self._hard_limit = max(1, min(24_000, int(hard_limit)))
        self._query_limit = max(0, min(self._hard_limit, int(query_limit)))
        self._processing_excluded_providers = _provider_values(processing_excluded_providers)
        self._catalog_loader = catalog_loader
        self._catalog_retry_seconds = max(60, int(catalog_retry_seconds))
        self._catalog_retry_after = 0.0
        self._closing = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._credential_paused = False
        self._collecting: set[asyncio.Task] = set()

    async def pause_credentials(self):
        self._credential_paused = True
        if self._collecting:
            await asyncio.gather(*(asyncio.shield(t) for t in tuple(self._collecting)), return_exceptions=True)

    def replace_client(self, client):
        if not self._credential_paused:
            raise RuntimeError("NEWS_CREDENTIAL_DRAIN_REQUIRED")
        self._client = client

    def resume_credentials(self):
        self._credential_paused = False

    def update(self, *, enabled: bool, queries: tuple[str, ...], poll_seconds: int,
               processing_excluded_providers: tuple[str, ...] | None = None) -> None:
        self._enabled, self._queries = bool(enabled), _queries(queries)
        self._poll_seconds = max(60, int(poll_seconds))
        if processing_excluded_providers is not None:
            self._processing_excluded_providers = _provider_values(processing_excluded_providers)

    async def start(self) -> None:
        if self._task is None:
            self._closing.clear()
            self._task = asyncio.create_task(self._loop(), name="central-news-query-set")

    async def close(self) -> None:
        self._closing.set()
        self._credential_paused = True
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if self._collecting:
            await asyncio.gather(*(asyncio.shield(t) for t in tuple(self._collecting)), return_exceptions=True)

    async def run_once(self) -> int:
        if self._workload_paused("news_query_set"):
            return 0
        if self._credential_paused or self._closing.is_set() or self._client is None or not self._enabled or not self._queries or self._query_limit <= 0:
            return 0
        completed = 0
        policy = (self._enabled, self._queries, self._poll_seconds)
        for query in policy[1]:
            if self._workload_paused("news_query_set"):
                break
            if policy != (self._enabled, self._queries, self._poll_seconds):
                break
            source_id = _source_id(query)
            cursor = await asyncio.to_thread(self._store.load_news_source_cursor, source_id)
            scheduled = float((cursor or {}).get("next_schedule_at") or 0)
            if (cursor and scheduled > 0 and cursor.get("last_success") is not None
                    and int(cursor.get("next_start") or 1) == 1 and not cursor.get("error")):
                scheduled = float(cursor["last_success"]) + policy[2]
            if scheduled > self._now().timestamp():
                continue
            if (self._credential_paused or self._closing.is_set() or self._client is None
                    or policy != (self._enabled, self._queries, self._poll_seconds)):
                break
            task = asyncio.create_task(self._run_query(query, source_id, cursor or {}, self._client))
            self._collecting.add(task)
            task.add_done_callback(self._collecting.discard)
            completed += int(await asyncio.shield(task))
        return completed

    async def _run_query(self, query, source_id, cursor, client):
        try:
            await self._collect_query(query, source_id, cursor, client)
            return True
        except Exception as error:
            await self._record_error(query, source_id, cursor, error)
            return False

    async def _collect_query(self, query: str, source_id: str, cursor: dict[str, Any], client) -> None:
        catalog = await asyncio.to_thread(self._load_catalog)
        start = max(1, min(1000, int(cursor.get("next_start") or 1)))
        old_marker = (str(cursor.get("cursor_published_at") or ""), str(cursor.get("cursor_identity") or ""))
        pending = (str(cursor.get("pending_published_at") or ""), str(cursor.get("pending_identity") or ""))
        run_id, checked_at = uuid.uuid4().hex, self._now().timestamp()
        while start <= 1000:
            if self._workload_paused("news_query_set"):
                return
            requests = 0

            def claim() -> bool:
                nonlocal requests
                accepted = self._store.claim_news_request(
                    "query_set", scope_limit=self._query_limit, hard_limit=self._hard_limit,
                    budget_date=self._now().date().isoformat(),
                )
                requests += int(accepted)
                return accepted

            try:
                page = await asyncio.to_thread(
                    client.search_page, query, start=start, display=100, request_claim=claim,
                )
            except Exception as error:
                setattr(error, "news_request_count", requests)
                raise
            items = list(page.items)
            if not pending[1] and items:
                pending = _marker(items[0])
            reached = bool(old_marker[1]) and any(_marker(item) == old_marker for item in items)
            exhausted = page.display < 100 or start + page.display > page.total
            capped = start + max(page.display, 100) > 1000 and not reached and not exhausted
            completed = reached or exhausted or capped
            truncated = bool(capped)
            coverage = "query_set_truncated" if truncated else ("query_set_cursor_reached" if reached else "query_set")
            gap_seconds = _gap_seconds(items[-1] if items else None, old_marker) if truncated else 0.0
            next_start = 1 if completed else start + 100
            promoted = pending if completed else old_marker
            remaining = await asyncio.to_thread(self._budget_remaining)
            filter_stats: dict[str, int] = {}
            stored_items = await asyncio.to_thread(
                _page_items, items, catalog, self._processing_excluded_providers, filter_stats,
            )
            await asyncio.to_thread(self._store.save_news_source_page, {
                "source_id": source_id, "scope": "query_set", "query_text": query,
                "run_id": run_id, "page_start": start, "checked_at": checked_at,
                "completed_at": self._now().timestamp(), "request_count": requests, "budget_remaining": remaining,
                "coverage": coverage, "truncated": truncated, "error": "",
                "cursor_published_at": promoted[0] or None, "cursor_identity": promoted[1],
                "pending_published_at": None if completed else (pending[0] or None),
                "pending_identity": "" if completed else pending[1], "next_start": next_start,
                "next_schedule_at": self._now().timestamp() + self._poll_seconds if completed else self._now().timestamp(),
                "last_success": self._now().timestamp() if completed else cursor.get("last_success"),
                "document": {"total": page.total, "display": page.display, "gap_seconds": gap_seconds,
                             "missing_gap": truncated,
                             "raw_items": len(items), "stored_items": len(stored_items),
                             "skipped_title_only": filter_stats.get("title_only", 0),
                             "skipped_irrelevant": filter_stats.get("irrelevant", 0),
                             "scope_statement": "configured NAVER query set; not a complete market feed"},
                "items": stored_items,
            })
            if completed:
                return
            start = next_start

    async def _record_error(self, query: str, source_id: str, cursor: dict[str, Any], error: Exception) -> None:
        now = self._now().timestamp()
        latest = await asyncio.to_thread(self._store.load_news_source_cursor, source_id)
        if latest:
            cursor = latest
        text = f"{type(error).__name__}: {error}"
        await asyncio.to_thread(self._store.save_news_source_page, {
            "source_id": source_id, "scope": "query_set", "query_text": query,
            "run_id": uuid.uuid4().hex, "page_start": int(cursor.get("next_start") or 1),
            "checked_at": now, "completed_at": self._now().timestamp(),
            "request_count": int(getattr(error, "news_request_count", 0)),
            "budget_remaining": await asyncio.to_thread(self._budget_remaining),
            "coverage": "query_set_error", "truncated": False, "error": text,
            "cursor_published_at": cursor.get("cursor_published_at"),
            "cursor_identity": str(cursor.get("cursor_identity") or ""),
            "pending_published_at": cursor.get("pending_published_at"),
            "pending_identity": str(cursor.get("pending_identity") or ""),
            "next_start": int(cursor.get("next_start") or 1), "next_schedule_at": now + self._poll_seconds,
            "last_success": cursor.get("last_success"), "document": {"error_kind": _error_kind(error)}, "items": [],
        })

    def _load_catalog(self) -> tuple[tuple[str, str], ...]:
        rows = self._store.load_documents("stock_catalog", "krx", 10_000)
        result = []
        for row in rows:
            document = row.get("document")
            if isinstance(document, dict):
                code, name = str(document.get("code") or ""), str(document.get("name") or "").strip()
                if code and name:
                    result.append((code, name))
        if result:
            return tuple(result)
        now = self._now().timestamp()
        if now < self._catalog_retry_after:
            return ()
        try:
            fetched = self._catalog_loader()
            if not fetched:
                raise ValueError("KRX 상장종목 목록이 비어 있습니다.")
            # 다운로드 중 TOP20 경로가 먼저 채웠다면 그 목록을 그대로 사용한다.
            refreshed = self._store.load_documents("stock_catalog", "krx", 10_000)
            refreshed_usable = []
            existing_by_code: dict[str, dict[str, Any]] = {}
            for row in refreshed:
                document = row.get("document")
                if not isinstance(document, dict):
                    continue
                code = str(document.get("code") or row.get("key") or "").strip()
                name = str(document.get("name") or "").strip()
                if code:
                    existing_by_code[code] = dict(document)
                if code and name:
                    refreshed_usable.append((code, name))
            if refreshed_usable:
                return tuple(refreshed_usable)
            as_of = self._now().date().isoformat()
            usable = [(code, name, market) for code, name, market in fetched if code and name]
            if not usable:
                raise ValueError("KRX 상장종목 목록에 유효한 종목이 없습니다.")
            documents = []
            for code, name, market in usable:
                document = existing_by_code.get(code, {})
                document.update({"code": code, "name": name, "as_of": as_of})
                if market or not document.get("market"):
                    document["market"] = market
                documents.append({"owner": "krx", "key": code, "document": document})
            documents.append({
                "owner": "krx", "key": "_meta",
                "document": {"as_of": as_of, "rows": len(usable)},
            })
            self._store.upsert_documents("stock_catalog", documents)
            self._catalog_retry_after = 0.0
            return tuple((code, name) for code, name, _market in usable)
        except Exception as error:
            self._catalog_retry_after = now + self._catalog_retry_seconds
            LOGGER.warning("뉴스 종목 연결용 KRX 카탈로그 초기화 실패(미확정 유지): %s", error)
            return ()

    def _budget_remaining(self) -> int:
        budget_date = self._now().date().isoformat()
        used = self._store.news_request_count(budget_date)
        return max(0, self._hard_limit - used)

    async def _loop(self) -> None:
        while not self._closing.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("query_set 뉴스 수집 주기에 실패했습니다.")
            try:
                await asyncio.wait_for(self._closing.wait(), timeout=min(60, self._poll_seconds))
            except TimeoutError:
                pass


def _queries(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))[:50]


def _provider_values(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))[:100]


def _source_id(query: str) -> str:
    return "naver-query:" + hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]


def _gap_seconds(oldest: StockNewsItem | None, old_marker: tuple[str, str]) -> float:
    if oldest is None or oldest.published_at is None or not old_marker[0]:
        return 0.0
    try:
        old = datetime.fromisoformat(old_marker[0]).astimezone(UTC)
    except ValueError:
        return 0.0
    return max(0.0, (oldest.published_at.astimezone(UTC) - old).total_seconds())


def _error_kind(error: Exception) -> str:
    code = getattr(error, "code", None)
    if code in {401, 403}:
        return "auth"
    if code == 429:
        return "rate_limit"
    if "예산" in str(error):
        return "budget"
    return "provider"
