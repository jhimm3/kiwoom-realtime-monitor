from __future__ import annotations

import asyncio
import logging
from time import perf_counter
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
from kiwoom_monitor.infrastructure.naver_stock_news import NaverStockNewsClient

from .database import QueryStore
from .news_jobs import NewsJobRunner
from .market_news_sources import MarketFeedNewsCollector
from .news_sources import DEFAULT_NEWS_QUERY_SET, QuerySetNewsCollector


LOGGER = logging.getLogger(__name__)


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
        market_feed_enabled: bool = False,
        naver_api_enabled: bool = True, naver_stock_enabled: bool = False,
        stock_client: NaverStockNewsClient | None = None,
        read_only_search: bool = False, jobs_parallelism: int = 1,
    ) -> None:
        self._client = client
        self._stock_client = stock_client or NaverStockNewsClient()
        self._naver_api_enabled = bool(naver_api_enabled)
        self._naver_stock_enabled = bool(naver_stock_enabled)
        self._read_only_search = bool(read_only_search)
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
        self._job_runner = NewsJobRunner(store, parallelism=jobs_parallelism) if jobs_enabled else None
        self._request_hard_limit = max(1, min(24_000, int(request_hard_limit)))
        self._watchlist_request_limit = max(0, min(self._request_hard_limit, int(watchlist_request_limit)))
        self._processing_excluded_providers = _provider_values(processing_excluded_providers)
        self._query_collector = QuerySetNewsCollector(
            client, store, queries=query_set, enabled=query_set_enabled and self._naver_api_enabled,
            poll_seconds=query_set_refresh_seconds, hard_limit=self._request_hard_limit,
            query_limit=min(int(query_set_request_limit), self._request_hard_limit - self._watchlist_request_limit),
            processing_excluded_providers=self._processing_excluded_providers,
        )
        self._market_collector = MarketFeedNewsCollector(
            store, processing_excluded_providers=self._processing_excluded_providers,
        ) if market_feed_enabled else None

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
        if not self._naver_api_enabled or self._watchlist_request_limit <= 0:
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
                                    naver_api_enabled: bool | None = None,
                                    naver_stock_enabled: bool | None = None,
                                    naver_market_enabled: bool | None = None,
                                    naver_stock_url: str | None = None,
                                    naver_flash_url: str | None = None,
                                    naver_world_url: str | None = None,
                                    query_set_enabled: bool | None = None,
                                    query_set: tuple[str, ...] | None = None,
                                    query_set_refresh_seconds: int | None = None,
                                    processing_excluded_providers: tuple[str, ...] | None = None) -> None:
        self._refresh_seconds = max(60, int(refresh_seconds))
        self._dart_enabled = bool(dart_enabled)
        if naver_api_enabled is not None:
            self._naver_api_enabled = bool(naver_api_enabled)
        if naver_stock_enabled is not None:
            self._naver_stock_enabled = bool(naver_stock_enabled)
        if naver_stock_url is not None:
            self._stock_client.update_endpoint(naver_stock_url)
        if self._market_collector is not None and naver_market_enabled is not None:
            from kiwoom_monitor.infrastructure.naver_stock_market_news import ENDPOINT
            self._market_collector.update_settings(
                enabled=naver_market_enabled,
                endpoints={
                    "flash": naver_flash_url or ENDPOINT["flash"],
                    "world": naver_world_url or ENDPOINT["world"],
                },
            )
        if processing_excluded_providers is not None:
            self._processing_excluded_providers = _provider_values(processing_excluded_providers)
            if self._market_collector is not None:
                self._market_collector.update_excluded_providers(self._processing_excluded_providers)
        if self._query_collector is not None and query_set_enabled is not None:
            self._query_collector.update(
                enabled=query_set_enabled and self._naver_api_enabled,
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
        if self._market_collector is not None:
            await self._market_collector.start()
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
        if self._market_collector is not None:
            await self._market_collector.close()
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
        if self._read_only_search:
            if automation is not None:
                await asyncio.to_thread(self._store.upsert_documents, "news_automation_settings", [{
                    "owner": code, "key": "current", "document": automation,
                }])
            cached = await asyncio.to_thread(self._load_cached, code, name, since)
            return await asyncio.to_thread(self._merge_confirmed, code, name, cached, since)
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
                task = asyncio.create_task(self._collect(
                    code, name, since, self._client if self._naver_api_enabled else None,
                    dart_client,
                ))
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

    async def stored_page(self, code: str, name: str, *, offset: int = 0,
                          limit: int = 200,
                          automation: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return a bounded page from NAS storage without contacting a news source."""
        if offset < 0 or not 1 <= limit <= 200:
            raise ValueError("NEWS_PAGE_RANGE_INVALID")
        started = perf_counter()
        if self._job_runner is not None and offset == 0:
            self._job_runner.set_priority_stock(code)
        priority_ms = (perf_counter() - started) * 1000
        if automation is not None and offset == 0:
            await asyncio.to_thread(self._store.upsert_documents, "news_automation_settings", [{
                "owner": code, "key": "current", "document": automation,
            }])
        automation_ms = (perf_counter() - started) * 1000 - priority_ms
        fetch_limit = offset + limit + 1
        owner = await asyncio.to_thread(self._load_cached, code, name, None, fetch_limit)
        owner_ms = (perf_counter() - started) * 1000 - priority_ms - automation_ms
        merged = await asyncio.to_thread(self._merge_confirmed, code, name, owner, None, fetch_limit)
        total_ms = (perf_counter() - started) * 1000
        if total_ms >= 1000:
            LOGGER.warning(
                "stored news page slow code=%s offset=%s limit=%s priority_ms=%.0f "
                "automation_ms=%.0f owner_ms=%.0f merge_ms=%.0f total_ms=%.0f "
                "owner_count=%s merged_count=%s",
                code, offset, limit, priority_ms, automation_ms, owner_ms,
                total_ms - priority_ms - automation_ms - owner_ms, total_ms,
                len(owner), len(merged),
            )
        return {
            "items": merged[offset:offset + limit],
            "next_offset": offset + limit if len(merged) > offset + limit else None,
        }

    def _collection_finished(self, key, task):
        # HTTP waiters may disappear before the owned collection finishes.
        if self._running.get(key) is task:
            self._running.pop(key, None)
        if not task.cancelled():
            task.exception()  # Observe orphan failures; active waiters still receive the exception.

    async def refresh_once(self) -> int:
        if not (self._naver_api_enabled or self._naver_stock_enabled or self._dart_enabled):
            return 0
        snapshots = await asyncio.to_thread(
            self._store.load_dataset_snapshots, "top20_membership", "", 1,
        ) if hasattr(self._store, "load_dataset_snapshots") else []
        if snapshots and snapshots[0].get("snapshot_key"):
            try:
                observed_at = datetime.fromisoformat(str(snapshots[0]["snapshot_key"]))
                if observed_at.tzinfo is None or datetime.now(UTC) - observed_at.astimezone(UTC) > timedelta(minutes=10):
                    snapshots = []
            except ValueError:
                snapshots = []
        membership = snapshots[0].get("payload", {}) if snapshots else {}
        ranking = membership.get("items", []) if isinstance(membership, dict) else []
        rows = [
            {"document": {"stock_code": str(item.get("stk_cd") or item.get("stk_code") or "").lstrip("A"),
                          "stock_name": str(item.get("stk_nm") or "").strip()}}
            for item in ranking if isinstance(item, dict)
        ]
        if not self._read_only_search:
            rows.extend(await asyncio.to_thread(self._store.load_documents, "news_watchlist", "", 10_000))
        refreshed = 0
        since = datetime.now(UTC) - timedelta(days=2)
        seen: set[str] = set()
        for row in rows:
            document = row.get("document")
            if not isinstance(document, dict):
                continue
            code, name = str(document.get("stock_code", "")), str(document.get("stock_name", "")).strip()
            if not code or not name:
                continue
            if code in seen:
                continue
            seen.add(code)
            try:
                if self._read_only_search:
                    saved = await asyncio.to_thread(
                        self._store.load_documents, "news_automation_settings", code, 1,
                    )
                    if saved and isinstance(saved[0].get("document"), dict):
                        document = {**document, **saved[0]["document"]}
                await self._collect(
                    code, name, since, self._client if self._naver_api_enabled else None,
                    self._dart_client if self._dart_enabled else None,
                )
                owner_items = await asyncio.to_thread(self._load_cached, code, name, since)
                await self._auto_analyze(code, name, owner_items, document)
                refreshed += 1
            except Exception:
                # 한 종목 또는 공급자의 실패가 나머지 자동 수집을 중단하지 않는다.
                continue
            await asyncio.sleep(0.7)
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
                await self.refresh_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("NAS 종목뉴스 순위 갱신 실패")
            try:
                await asyncio.wait_for(self._closing.wait(), timeout=self._refresh_seconds)
            except TimeoutError:
                continue

    async def _collect(self, code: str, name: str, since: datetime | None, client, dart_client) -> list[dict[str, Any]]:
        sync_rows = await asyncio.to_thread(self._store.load_documents, "news_sync", code, 1)
        sync_document = sync_rows[0].get("document", {}) if sync_rows else {}
        checked_at = None
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
        provider_since = since
        if checked_at is not None:
            last_checked = checked_at.astimezone(UTC)
            if provider_since is None or last_checked > provider_since.astimezone(UTC):
                provider_since = last_checked
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
                    found = await asyncio.to_thread(client.search, name, since=provider_since, request_claim=claim)
                else:
                    found = await asyncio.to_thread(client.search, name, since=provider_since)
                items.extend((item, "naver") for item in found)
            except Exception as error:  # 외부 뉴스 공급자 하나의 실패는 다른 공급자를 막지 않는다.
                errors.append(error)
        site_state = dict(sync_document) if isinstance(sync_document, dict) else {}
        if self._naver_stock_enabled:
            try:
                cursor_text = str(site_state.get("site_cursor_published_at") or "")
                window_floor = datetime.now(UTC) - timedelta(days=2)
                try:
                    stock_since = datetime.fromisoformat(cursor_text).astimezone(UTC) if cursor_text else window_floor
                except ValueError:
                    stock_since = window_floor
                expired_cursor = stock_since < window_floor
                stock_since = max(stock_since, window_floor)
                start_page = 1 if expired_cursor else max(1, int(site_state.get("site_next_page") or 1) - 1)
                # The source clock has minute precision and pages can shift while paging.
                found = await asyncio.to_thread(
                    self._stock_client.search, code, name,
                    since=max(window_floor, stock_since - timedelta(minutes=2)), start_page=start_page,
                )
                items.extend((item, "naver_stock") for item in found.items)
                latest = "" if expired_cursor else str(site_state.get("site_pending_latest_published_at") or "")
                if found.latest_published_at is not None and not latest:
                    latest = found.latest_published_at.isoformat()
                site_state["site_next_page"] = found.next_page
                site_state["site_pending_latest_published_at"] = "" if found.complete or found.page_limit_reached else latest
                site_state["site_coverage_complete"] = found.complete
                if found.page_limit_reached:
                    site_state["site_last_page_limit_at"] = datetime.now(UTC).isoformat()
                if (found.complete or found.page_limit_reached) and latest:
                    site_state["site_cursor_published_at"] = latest
            except Exception as error:
                errors.append(error)
        if dart_client is not None:
            try:
                items.extend((item, "dart") for item in await asyncio.to_thread(
                    dart_client.search, code, name,
                ))
            except Exception as error:
                errors.append(error)
        if not items and errors:
            cached = await asyncio.to_thread(self._load_cached, code, name, None)
            stored = await asyncio.to_thread(self._merge_confirmed, code, name, cached, since)
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
            "owner": code, "key": "latest",
            "document": {**site_state, "checked_at": datetime.now(UTC).isoformat()},
        }])
        cached = await asyncio.to_thread(self._load_cached, code, name, None)
        fresh = [value for value, _collector in documents]
        fresh_identities = {_identity(value) for value in fresh}
        owner_values = fresh + [value for value in cached if _identity(value) not in fresh_identities]
        return await asyncio.to_thread(self._merge_confirmed, code, name, owner_values, since)

    def _load_cached(self, code: str, name: str, since: datetime | None,
                     limit: int = 1000) -> list[dict[str, Any]]:
        documents = self._store.load_stock_news_articles(code, limit=limit)
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
        since: datetime | None, limit: int = 1000,
    ) -> list[dict[str, Any]]:
        stored = self._store.load_confirmed_news_articles(code, limit=limit)
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
