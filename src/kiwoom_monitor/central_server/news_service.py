from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from kiwoom_monitor.application.news_analysis import NewsAssessment, assess_stock_news
from kiwoom_monitor.application.news_grouping import group_similar_news
from kiwoom_monitor.infrastructure.dart_disclosures import DartDisclosureClient
from kiwoom_monitor.infrastructure.naver_news import (
    NaverNewsClient, StockNewsItem, is_excluded_provider, news_provider,
    news_provider_domain,
)
from kiwoom_monitor.infrastructure.news_ai import ANALYSIS_PROMPT_VERSION

from .database import QueryStore
from .news_jobs import NewsJobRunner
from .news_sources import DEFAULT_NEWS_QUERY_SET, QuerySetNewsCollector


class CentralNewsService:
    """동일 종목의 동시 뉴스 요청을 하나의 네이버 호출로 합친다."""

    SELECTED_STOCK_CACHE_SECONDS = 60

    def __init__(
        self, client: NaverNewsClient | None, store: QueryStore,
        dart_client: DartDisclosureClient | None = None, *, refresh_seconds: int = 300,
        jobs_enabled: bool = True, query_set_enabled: bool = True,
        query_set: tuple[str, ...] = DEFAULT_NEWS_QUERY_SET, query_set_refresh_seconds: int = 300,
        request_hard_limit: int = 24_000, watchlist_request_limit: int = 8_000,
        query_set_request_limit: int = 16_000,
        processing_excluded_providers: tuple[str, ...] = (),
    ) -> None:
        self._client = client
        self._naver_paused = False
        self._dart_paused = False
        self._store = store
        self._dart_client = dart_client
        self._dart_enabled = dart_client is not None
        self._lock = asyncio.Lock()
        self._running: dict[tuple[str, str], asyncio.Task[list[dict[str, Any]]]] = {}
        self._refresh_seconds = max(60, int(refresh_seconds))
        self._refresh_task: asyncio.Task[None] | None = None
        self._closing = asyncio.Event()
        self._ai_service: Any = None
        self._job_runner = NewsJobRunner(store) if jobs_enabled else None
        self._request_hard_limit = max(1, min(24_000, int(request_hard_limit)))
        self._watchlist_request_limit = max(0, min(self._request_hard_limit, int(watchlist_request_limit)))
        self._processing_excluded_providers = _provider_values(processing_excluded_providers)
        self._query_collector = QuerySetNewsCollector(
            client, store, queries=query_set, enabled=query_set_enabled,
            poll_seconds=query_set_refresh_seconds, hard_limit=self._request_hard_limit,
            query_limit=min(int(query_set_request_limit), self._request_hard_limit - self._watchlist_request_limit),
            processing_excluded_providers=self._processing_excluded_providers,
        )

    async def pause_naver_credentials(self):
        async with self._lock:
            self._naver_paused = True
            tasks = tuple(self._running.values())
        await asyncio.gather(self._query_collector.pause_credentials(),
            *(asyncio.shield(t) for t in tasks), return_exceptions=True)

    def replace_naver_client(self, client):
        if not self._naver_paused:
            raise RuntimeError("NEWS_CREDENTIAL_DRAIN_REQUIRED")
        self._query_collector.replace_client(client)
        self._client = client

    def resume_naver_credentials(self):
        if self._closing.is_set():
            return
        self._query_collector.resume_credentials()
        self._naver_paused = False

    def claim_naver_validation_request(self):
        if self._watchlist_request_limit <= 0:
            return False
        return self._store.claim_news_request("watchlist", scope_limit=self._watchlist_request_limit,
            hard_limit=self._request_hard_limit,
            budget_date=datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat())

    async def pause_dart_credentials(self):
        async with self._lock:
            self._dart_paused = True
            tasks = tuple(self._running.values())
        if tasks:
            await asyncio.gather(*(asyncio.shield(task) for task in tasks), return_exceptions=True)

    def replace_dart_client(self, client):
        if not self._dart_paused:
            raise RuntimeError("NEWS_CREDENTIAL_DRAIN_REQUIRED")
        self._dart_client = client

    def resume_dart_credentials(self):
        if not self._closing.is_set():
            self._dart_paused = False

    def update_operational_settings(self, *, refresh_seconds: int, dart_enabled: bool,
                                    query_set_enabled: bool | None = None,
                                    query_set: tuple[str, ...] | None = None,
                                    query_set_refresh_seconds: int | None = None,
                                    processing_excluded_providers: tuple[str, ...] | None = None) -> None:
        self._refresh_seconds = max(60, int(refresh_seconds))
        self._dart_enabled = bool(dart_enabled)
        if processing_excluded_providers is not None:
            self._processing_excluded_providers = _provider_values(processing_excluded_providers)
        if self._query_collector is not None and query_set_enabled is not None:
            self._query_collector.update(
                enabled=query_set_enabled,
                queries=query_set if query_set is not None else DEFAULT_NEWS_QUERY_SET,
                poll_seconds=query_set_refresh_seconds or self._refresh_seconds,
                processing_excluded_providers=self._processing_excluded_providers,
            )

    def set_ai_service(self, service: Any) -> None:
        self._ai_service = service
        if self._job_runner is not None:
            self._job_runner.set_ai_service(service)

    async def start(self) -> None:
        if self._job_runner is not None:
            await self._job_runner.start()
        if self._query_collector is not None:
            await self._query_collector.start()
        if self._refresh_task is None:
            self._closing.clear()
            self._refresh_task = asyncio.create_task(self._refresh_loop(), name="central-news-refresh")

    async def close(self) -> None:
        self._closing.set()
        self._naver_paused = True
        self._dart_paused = True
        task, self._refresh_task = self._refresh_task, None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if self._query_collector is not None:
            await self._query_collector.close()
        async with self._lock:
            tasks = tuple(self._running.values())
        if tasks:
            await asyncio.gather(*(asyncio.shield(t) for t in tasks), return_exceptions=True)
        if self._job_runner is not None:
            await self._job_runner.close()

    async def search(
        self, code: str, name: str, since: datetime | None,
        automation: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if automation is None and self._job_runner is not None:
            self._job_runner.set_priority_stock(code)
        watch_document = {
            "stock_code": code, "stock_name": name, "registered_at": datetime.now(UTC).isoformat(),
        }
        if automation is None:
            existing = await asyncio.to_thread(self._store.load_documents, "news_watchlist", code, 1)
            if existing and isinstance(existing[0].get("document"), dict):
                watch_document.update(existing[0]["document"])
        else:
            watch_document.update(automation)
        await asyncio.to_thread(self._store.upsert_documents, "news_watchlist", [{
            "owner": code, "key": code, "document": watch_document,
        }])
        key = (code, name.casefold())
        async with self._lock:
            task = self._running.get(key)
            if task is None and not self._naver_paused and not self._dart_paused and not self._closing.is_set():
                dart_client = self._dart_client if self._dart_enabled else None
                task = asyncio.create_task(self._collect(code, name, since, self._client, dart_client))
                self._running[key] = task
                task.add_done_callback(lambda finished: self._collection_finished(key, finished))
        if task is None:
            cached = await asyncio.to_thread(self._load_cached, code, name, since)
            return await asyncio.to_thread(self._merge_confirmed, code, name, cached, since)
        try:
            return await asyncio.shield(task)
        finally:
            if task.done():
                async with self._lock:
                    if self._running.get(key) is task:
                        self._running.pop(key, None)

    def _collection_finished(self, key, task):
        # HTTP waiters may disappear before the owned collection finishes.
        if self._running.get(key) is task:
            self._running.pop(key, None)
        if not task.cancelled():
            task.exception()  # Observe orphan failures; active waiters still receive the exception.

    async def refresh_once(self) -> int:
        rows = await asyncio.to_thread(self._store.load_documents, "news_watchlist", "", 10_000)
        refreshed = 0
        since = datetime.now(UTC) - timedelta(days=2)
        for row in rows:
            document = row.get("document")
            if not isinstance(document, dict):
                continue
            code, name = str(document.get("stock_code", "")), str(document.get("stock_name", "")).strip()
            if not code or not name:
                continue
            try:
                await self.search(code, name, since, automation={
                    "auto_analyze": bool(document.get("auto_analyze", False)),
                    "auto_recent_limit": int(document.get("auto_recent_limit", 10)),
                    "provider": str(document.get("provider", "none")),
                    "model": str(document.get("model", "")),
                })
                owner_items = await asyncio.to_thread(self._load_cached, code, name, since)
                await self._auto_analyze(code, name, owner_items, document)
                refreshed += 1
            except Exception:
                # 한 종목 또는 공급자의 실패가 나머지 자동 수집을 중단하지 않는다.
                continue
        return refreshed

    async def _auto_analyze(
        self, code: str, name: str, values: list[dict[str, Any]], automation: dict[str, Any],
    ) -> None:
        if self._ai_service is None or not bool(automation.get("auto_analyze", False)):
            return
        provider = str(automation.get("provider", "none"))
        if provider == "none":
            return
        limit = max(1, min(1000, int(automation.get("auto_recent_limit", 10))))
        items = tuple(
            item for value in values if bool(value.get("relevant", 0))
            for item in (_deserialize(value),)
            if not is_excluded_provider(
                news_provider_domain(item), news_provider(item), self._processing_excluded_providers,
            )
        )
        # 프롬프트 규칙이 바뀌어도 자동 수집이 과거 분석 전체를 다시 실행하지
        # 않는다. 과거 결과의 재분석은 사용자가 앱에서 명시적으로 요청한다.
        analyzed = {
            str(row.get("key", ""))
            for row in await asyncio.to_thread(
                self._store.load_documents, "news_ai", code, 10_000,
            )
        }
        groups = tuple(
            group for group in group_similar_news(items)
            if _identity(_serialize(group.representative)) not in analyzed
        )[:limit]
        if self._job_runner is not None and hasattr(self._store, "enqueue_news_ai_jobs"):
            processing_version = (
                self._ai_service.job_processing_version(
                    provider, str(automation.get("model", "")),
                )
                if hasattr(self._ai_service, "job_processing_version")
                else f"{ANALYSIS_PROMPT_VERSION}:{provider}:{automation.get('model', '')}"
            )
            jobs = []
            for group in groups:
                representative = _serialize(group.representative)
                jobs.append({
                    "stock_code": code,
                    "identity": _identity(representative),
                    "target_id": code,
                    "processing_version": processing_version,
                    "payload": {
                        "stock_name": name, "provider": provider,
                        "model": str(automation.get("model", "")),
                        "article_count": len(group.items),
                        "event": {
                            "identity": _identity(representative),
                            "title": group.representative.title,
                            "articles": [{
                                "title": item.title, "description": item.description,
                                "link": item.link, "original_link": item.original_link,
                            } for item in group.items],
                        },
                    },
                })
            await asyncio.to_thread(self._store.enqueue_news_ai_jobs, jobs)
            return
        for offset in range(0, len(groups), 20):
            batch = groups[offset:offset + 20]
            events = [{
                "identity": _identity(_serialize(group.representative)),
                "title": group.representative.title,
                "articles": [{
                    "title": item.title, "description": item.description,
                    "link": item.link, "original_link": item.original_link,
                } for item in group.items],
            } for group in batch]
            await self._ai_service.analyze(
                code, name, provider, str(automation.get("model", "")), events,
                sum(len(group.items) for group in batch),
            )

    async def _refresh_loop(self) -> None:
        while not self._closing.is_set():
            try:
                await asyncio.wait_for(self._closing.wait(), timeout=self._refresh_seconds)
            except TimeoutError:
                await self.refresh_once()

    async def _collect(self, code: str, name: str, since: datetime | None, client, dart_client) -> list[dict[str, Any]]:
        sync_rows = await asyncio.to_thread(self._store.load_documents, "news_sync", code, 1)
        if sync_rows:
            try:
                checked_at = datetime.fromisoformat(str(sync_rows[0]["document"]["checked_at"]))
            except (KeyError, TypeError, ValueError):
                checked_at = None
            if (
                checked_at is not None
                and datetime.now(UTC) - checked_at.astimezone(UTC)
                < timedelta(seconds=self.SELECTED_STOCK_CACHE_SECONDS)
            ):
                cached = await asyncio.to_thread(self._load_cached, code, name, None)
                return await asyncio.to_thread(self._merge_confirmed, code, name, cached, since)
        items: list[tuple[StockNewsItem, str]] = []
        errors: list[Exception] = []
        if client is not None:
            try:
                if isinstance(client, NaverNewsClient):
                    claim = lambda: self._store.claim_news_request(
                        "watchlist", scope_limit=self._watchlist_request_limit,
                        hard_limit=self._request_hard_limit,
                        budget_date=datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat(),
                    )
                    found = await asyncio.to_thread(client.search, name, since=since, request_claim=claim)
                else:
                    found = await asyncio.to_thread(client.search, name, since=since)
                items.extend((item, "naver") for item in found)
            except Exception as error:  # 외부 뉴스 공급자 하나의 실패는 다른 공급자를 막지 않는다.
                errors.append(error)
        if dart_client is not None:
            try:
                items.extend((item, "dart") for item in await asyncio.to_thread(
                    dart_client.search, code, name,
                ))
            except Exception as error:
                errors.append(error)
        if not items and errors:
            stored = await asyncio.to_thread(self._merge_confirmed, code, name, [], since)
            if stored:
                return stored
            raise RuntimeError(" / ".join(str(error) for error in errors))
        unique = {
            item.original_link or item.link or f"{item.published_at!s}|{item.title}": (item, collector)
            for item, collector in items
        }
        documents = [(_serialize(item), collector) for item, collector in unique.values()]
        await asyncio.to_thread(self._store.upsert_documents, "news_article", [
            {"owner": code, "key": _identity(value),
             "document": {"stock_code": code, "stock_name": name, **value},
             "collector_id": collector, "collection_scope": "watchlist"}
            for value, collector in documents
        ])
        await asyncio.to_thread(self._store.upsert_documents, "news_sync", [{
            "owner": code, "key": "latest", "document": {"checked_at": datetime.now(UTC).isoformat()},
        }])
        return await asyncio.to_thread(
            self._merge_confirmed, code, name,
            [value for value, _collector in documents], since,
        )

    def _load_cached(self, code: str, name: str, since: datetime | None) -> list[dict[str, Any]]:
        documents = self._store.load_stock_news_articles(code, limit=1000)
        values = [
            _serialize(_stored_item(str(value.get("stock_name") or name), value))
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

    def _merge_confirmed(
        self, code: str, name: str, owner_values: list[dict[str, Any]],
        since: datetime | None,
    ) -> list[dict[str, Any]]:
        stored = self._store.load_confirmed_news_articles(code, limit=1000)
        merged = {_identity(value): value for value in owner_values}
        for document in stored:
            value = _serialize(_stored_item(name, document))
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
        cutoff = since.astimezone(UTC) if since is not None else None
        values = []
        for value in merged.values():
            published = _published_at(value)
            if cutoff is not None and (published is None or published <= cutoff):
                continue
            values.append(value)
        return sorted(values, key=_news_sort_key)[:1000]


def _identity(value: dict[str, Any]) -> str:
    return str(value.get("original_link") or value.get("link") or f'{value.get("published_at", "")}|{value.get("title", "")}')


def _serialize(item: StockNewsItem) -> dict[str, Any]:
    assessment = item.assessment
    return {
        "title": item.title, "description": item.description, "link": item.link,
        "original_link": item.original_link,
        "publisher_domain": news_provider_domain(item), "publisher_name": news_provider(item),
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "relevant": int(assessment.relevant), "category": assessment.category,
        "outlook": assessment.outlook, "reason": assessment.reason,
        "relevance_score": assessment.relevance_score, "outlook_score": assessment.outlook_score,
    }


def _provider_values(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))[:100]


def _deserialize(value: dict[str, Any]) -> StockNewsItem:
    published_at = None
    if value.get("published_at"):
        try:
            published_at = datetime.fromisoformat(str(value["published_at"]))
        except ValueError:
            pass
    return StockNewsItem(
        str(value.get("title", "")), str(value.get("description", "")),
        str(value.get("link", "")), str(value.get("original_link", "")), published_at,
        NewsAssessment(
            bool(value.get("relevant", 0)), str(value.get("category", "")),
            str(value.get("outlook", "")), str(value.get("reason", "")),
            int(value.get("relevance_score", 0)), int(value.get("outlook_score", 0)),
        ),
    )


def _stored_item(stock_name: str, value: dict[str, Any]) -> StockNewsItem:
    from kiwoom_monitor.infrastructure.article_text import clean_article_text

    title, description = str(value.get("title", "")), str(value.get("description", ""))
    body = (
        clean_article_text(str(value.get("_body_text", "")))
        if value.get("_body_status") == "fulltext" else ""
    )
    if len(body) < 40:
        body = ""
    return StockNewsItem(
        title, description, str(value.get("link", "")), str(value.get("original_link", "")),
        _published_at(value), assess_stock_news(stock_name, title, description, article_body=body),
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
