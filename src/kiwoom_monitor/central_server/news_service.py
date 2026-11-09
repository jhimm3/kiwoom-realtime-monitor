from __future__ import annotations

import asyncio
import logging
from time import perf_counter
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any, Awaitable, Callable, Protocol

from kiwoom_monitor.domain.news_ai_contract import decode_news_input_source, news_ai_article_input
from kiwoom_monitor.domain.ranking import normalize_stock_code
from kiwoom_monitor.domain.time_basis import aware_iso_kst, kst_now

from kiwoom_monitor.application.news_analysis import NewsAssessment, assess_stock_news
from kiwoom_monitor.application.news_grouping import group_similar_news
from kiwoom_monitor.infrastructure.dart_disclosures import DartDisclosureClient
from kiwoom_monitor.infrastructure.naver_news import (
    NaverNewsClient, StockNewsItem, is_excluded_provider, news_provider,
    news_provider_domain,
)
from kiwoom_monitor.infrastructure.news_ai import ANALYSIS_PROMPT_VERSION
from kiwoom_monitor.infrastructure.naver_stock_news import NaverStockNewsClient

from .market_news_sources import MarketFeedNewsCollector
from .news_sources import (
    DEFAULT_NEWS_QUERY_SET, QuerySetNewsCollector, automatic_news_open, automatic_news_wait,
    load_premarket_stock_names, news_resume_at,
)
from .market_observations import as_kst
from .news_read_routes import (
    load_cached_news, merge_confirmed_news, _identity, _serialize,
)


LOGGER = logging.getLogger(__name__)


class CentralNewsStore(Protocol):
    """뉴스 수집 서비스와 두 수집원이 사용하는 저장 계약."""

    def upsert_documents(self, collection: str, values: list[dict[str, Any]]) -> None: ...

    def load_documents(
        self, collection: str, owner: str = "", limit: int = 1000, offset: int = 0,
        updated_after: float = 0.0,
    ) -> list[dict[str, Any]]: ...

    def load_dataset_snapshots(
        self, kind: str, subject: str = "", limit: int = 100,
    ) -> list[dict[str, Any]]: ...

    def load_stock_news_articles(
        self, stock_code: str, *, limit: int = 1000,
    ) -> list[dict[str, Any]]: ...

    def load_confirmed_news_articles(
        self, stock_code: str, *, limit: int = 1000,
    ) -> list[dict[str, Any]]: ...

    def claim_news_request(
        self, scope: str, *, scope_limit: int, hard_limit: int, budget_date: str,
    ) -> bool: ...

    def news_request_count(self, budget_date: str) -> int: ...

    def load_news_source_cursor(self, source_id: str) -> dict[str, Any] | None: ...

    def save_news_source_page(self, value: dict[str, Any]) -> dict[str, Any]: ...

    def load_document(self, collection: str, owner: str, key: str) -> dict[str, Any] | None: ...


class CentralNewsService:
    """동일 종목의 동시 뉴스 요청을 하나의 네이버 호출로 합친다."""

    SELECTED_STOCK_CACHE_SECONDS = 60

    def __init__(
        self, client: NaverNewsClient | None, store: CentralNewsStore,
        dart_client: DartDisclosureClient | None = None, *, refresh_seconds: int = 300,
        query_set_enabled: bool = True,
        query_set: tuple[str, ...] = DEFAULT_NEWS_QUERY_SET, query_set_refresh_seconds: int = 300,
        request_hard_limit: int = 24_000, watchlist_request_limit: int = 8_000,
        query_set_request_limit: int = 16_000,
        processing_excluded_providers: tuple[str, ...] = (),
        market_feed_enabled: bool = False,
        naver_api_enabled: bool = True, naver_stock_enabled: bool = False,
        stock_client: NaverStockNewsClient | None = None,
        read_only_search: bool = False,
        prioritize_stock: Callable[[str], None] | None = None,
        enqueue_ai_jobs: Callable[[list[dict[str, Any]]], int] | None = None,
        held_news_codes: Callable[[datetime], Awaitable[set[str]]] | None = None,
        now_provider: Callable[[], datetime] | None = None,
        workload_paused: Callable[[str], bool] | None = None,
    ) -> None:
        self._now = (lambda: as_kst(now_provider())) if now_provider else kst_now
        self._workload_paused = workload_paused or (lambda _workload: False)
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
        self._prioritize_stock = prioritize_stock
        self._enqueue_ai_jobs = enqueue_ai_jobs
        self._held_news_codes = held_news_codes
        self._request_hard_limit = max(1, min(24_000, int(request_hard_limit)))
        self._watchlist_request_limit = max(0, min(self._request_hard_limit, int(watchlist_request_limit)))
        self._processing_excluded_providers = _provider_values(processing_excluded_providers)
        self._query_collector = QuerySetNewsCollector(
            client, store, queries=query_set, enabled=query_set_enabled and self._naver_api_enabled,
            poll_seconds=query_set_refresh_seconds, hard_limit=self._request_hard_limit,
            query_limit=min(int(query_set_request_limit), self._request_hard_limit - self._watchlist_request_limit),
            processing_excluded_providers=self._processing_excluded_providers,
            now_provider=self._now,
            workload_paused=self._workload_paused,
        )
        self._market_collector = MarketFeedNewsCollector(
            store, processing_excluded_providers=self._processing_excluded_providers,
            now_provider=self._now,
            workload_paused=self._workload_paused,
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
            budget_date=self._now().date().isoformat())

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
            from kiwoom_monitor.infrastructure.naver_stock_market_news_source import ENDPOINT
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

    async def start(self) -> None:
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

    async def search(
        self, code: str, name: str, since: datetime | None,
        automation: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if automation is None and self._prioritize_stock is not None:
            self._prioritize_stock(code)
        if self._read_only_search:
            if automation is not None:
                await asyncio.to_thread(self._store.upsert_documents, "news_automation_settings", [{
                    "owner": code, "key": "current", "document": automation,
                }])
            cached = await asyncio.to_thread(load_cached_news, self._store, code, name, since)
            return await asyncio.to_thread(merge_confirmed_news, self._store, code, name, cached, since)
        watch_document = {
            "stock_code": code, "stock_name": name, "registered_at": self._now().isoformat(),
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
            cached = await asyncio.to_thread(load_cached_news, self._store, code, name, since)
            return await asyncio.to_thread(merge_confirmed_news, self._store, code, name, cached, since)
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
        if self._prioritize_stock is not None and offset == 0:
            self._prioritize_stock(code)
        priority_ms = (perf_counter() - started) * 1000
        if automation is not None and offset == 0:
            await asyncio.to_thread(self._store.upsert_documents, "news_automation_settings", [{
                "owner": code, "key": "current", "document": automation,
            }])
        automation_ms = (perf_counter() - started) * 1000 - priority_ms
        fetch_limit = offset + limit + 1
        owner = await asyncio.to_thread(load_cached_news, self._store, code, name, None, fetch_limit)
        owner_ms = (perf_counter() - started) * 1000 - priority_ms - automation_ms
        merged = await asyncio.to_thread(merge_confirmed_news, self._store, code, name, owner, None, fetch_limit)
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
        now = self._now()
        if not automatic_news_open(now) or self._workload_paused("news_stock_refresh"):
            return 0
        if not (self._naver_api_enabled or self._naver_stock_enabled or self._dart_enabled):
            return 0
        snapshots = await asyncio.to_thread(
            self._store.load_dataset_snapshots, "top20_membership", "", 1,
        ) if hasattr(self._store, "load_dataset_snapshots") else []
        rank_fresh = bool(snapshots)
        if snapshots and snapshots[0].get("snapshot_key"):
            try:
                observed_at = datetime.fromisoformat(str(snapshots[0]["snapshot_key"]))
                morning = now.hour == 7
                visible = observed_at.tzinfo is not None and observed_at <= now
                rank_fresh = visible and now - observed_at <= timedelta(minutes=10)
                if not visible or (not morning and not rank_fresh):
                    snapshots = []
            except ValueError:
                snapshots = []
        membership = snapshots[0].get("payload", {}) if snapshots else {}
        ranking = membership.get("items", []) if isinstance(membership, dict) else []
        rows = [
            {"document": {"stock_code": normalize_stock_code(item.get("stk_cd") or item.get("stk_code")),
                          "stock_name": str(item.get("stk_nm") or "").strip()}}
            for item in ranking if isinstance(item, dict)
        ]
        normal_ai_codes = {row["document"]["stock_code"] for row in rows} if rank_fresh else set()
        if self._held_news_codes is not None:
            try:
                held = await self._held_news_codes(now)
            except Exception:
                LOGGER.exception("보유 종목 뉴스 입력을 읽지 못했습니다.")
                held = set()
            known = {row["document"]["stock_code"]: row["document"]["stock_name"] for row in rows}
            if any(not known.get(code) for code in held):
                premarket = await asyncio.to_thread(
                    load_premarket_stock_names, self._store, now.date().isoformat(),
                )
            else:
                premarket = {}
            held_rows = []
            for code in sorted(held):
                name = known.get(code) or premarket.get(code)
                if not name:
                    entry = await asyncio.to_thread(self._store.load_document, "stock_catalog", "krx", code)
                    data = (entry or {}).get("document", {})
                    if data.get("instrument_type") in {"ETF", "ETN", "ELW"}:
                        continue
                    name = str(data.get("name") or code)
                held_rows.append({"document": {"stock_code": code, "stock_name": name}})
            rows = held_rows + rows
        if not self._read_only_search:
            rows.extend(await asyncio.to_thread(self._store.load_documents, "news_watchlist", "", 10_000))
        refreshed = 0
        since = self._now() - timedelta(days=2)
        seen: set[str] = set()
        for row in rows:
            if not automatic_news_open(self._now()) or self._workload_paused("news_stock_refresh"):
                break
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
                automatic_analysis = not self._read_only_search or code in normal_ai_codes
                if self._read_only_search and automatic_analysis:
                    saved = await asyncio.to_thread(
                        self._store.load_documents, "news_automation_settings", code, 1,
                    )
                    if saved and isinstance(saved[0].get("document"), dict):
                        document = {**document, **saved[0]["document"]}
                owner_items = await self._collect(
                    code, name, since, self._client if self._naver_api_enabled else None,
                    self._dart_client if self._dart_enabled else None,
                    owner_only=True,
                )
                if automatic_analysis:
                    await self._auto_analyze(code, name, owner_items, document)
                refreshed += 1
            except Exception:
                # 한 종목 또는 공급자의 실패가 나머지 자동 수집을 중단하지 않는다.
                LOGGER.exception("Stock news refresh failed code=%s", code)
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
        if self._enqueue_ai_jobs is not None:
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
                            "articles": [news_ai_article_input(item) for item in group.items],
                        },
                    },
                })
            await asyncio.to_thread(self._enqueue_ai_jobs, jobs)
            return
        for offset in range(0, len(groups), 20):
            batch = groups[offset:offset + 20]
            events = [{
                "identity": _identity(_serialize(group.representative)),
                "title": group.representative.title,
                "articles": [news_ai_article_input(item) for item in group.items],
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
                await asyncio.wait_for(self._closing.wait(),
                                       timeout=automatic_news_wait(self._now(), self._refresh_seconds))
            except TimeoutError:
                continue

    async def _collect(
        self, code: str, name: str, since: datetime | None, client, dart_client,
        *, owner_only: bool = False,
    ) -> list[dict[str, Any]]:
        started = self._now()
        sync_rows = await asyncio.to_thread(self._store.load_documents, "news_sync", code, 1)
        state = dict(sync_rows[0].get("document") or {}) if sync_rows else {}
        checked_at = _news_timestamp(state.get("checked_at"))
        enabled = tuple(source for source, active in (("naver", client is not None),
                        ("site", self._naver_stock_enabled), ("dart", dart_client is not None)) if active)
        cache_recent = (checked_at is not None and timedelta(0) <= started - checked_at
                        < timedelta(seconds=self.SELECTED_STOCK_CACHE_SECONDS)
                        and (not owner_only or checked_at >= news_resume_at(started)))
        if cache_recent and all(state.get(f"{source}_coverage_complete", not owner_only) for source in enabled):
            if owner_only:
                return await asyncio.to_thread(load_cached_news, self._store, code, name, since)
            cached = await asyncio.to_thread(load_cached_news, self._store, code, name, None)
            return await asyncio.to_thread(merge_confirmed_news, self._store, code, name, cached, since)

        previous_day = started.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)

        def scan_since(source):
            last_success = _news_timestamp(state.get(f"{source}_success_at"))
            if last_success is not None and last_success <= started:
                return last_success - timedelta(minutes=2)
            return previous_day if owner_only else since

        def needs_scan(source):
            last_success = _news_timestamp(state.get(f"{source}_success_at"))
            return (not cache_recent or not state.get(f"{source}_coverage_complete")
                    or last_success is None or not timedelta(0) <= started - last_success
                    < timedelta(seconds=self.SELECTED_STOCK_CACHE_SECONDS))

        def finished(source, complete):
            state[f"{source}_checked_at"] = started.isoformat()
            state[f"{source}_coverage_complete"] = complete
            if complete:
                state[f"{source}_success_at"] = started.isoformat()

        items: list[tuple[StockNewsItem, str]] = []
        errors: list[Exception] = []
        if client is not None and not self._naver_paused and needs_scan("naver"):
            try:
                if isinstance(client, NaverNewsClient):
                    claim = lambda: self._store.claim_news_request(
                        "watchlist", scope_limit=self._watchlist_request_limit,
                        hard_limit=self._request_hard_limit,
                        budget_date=self._now().date().isoformat(),
                    )
                    found, complete = await asyncio.to_thread(
                        client.search_with_coverage, name, since=scan_since("naver"), request_claim=claim,
                    )
                else:
                    found = await asyncio.to_thread(client.search, name, since=scan_since("naver"))
                    complete = True
                items.extend((item, "naver") for item in found)
                finished("naver", complete)
            except Exception as error:  # 외부 뉴스 공급자 하나의 실패는 다른 공급자를 막지 않는다.
                finished("naver", False)
                errors.append(error)
        if self._naver_stock_enabled and not self._naver_paused and needs_scan("site"):
            try:
                window_floor = started - timedelta(days=2)
                cursor_at = _news_timestamp(state.get("site_cursor_published_at"))
                stock_since = scan_since("site") if state.get("site_success_at") else cursor_at
                stock_since = stock_since or (previous_day if owner_only else window_floor)
                expired_cursor = stock_since < window_floor
                last_scan = _news_timestamp(state.get("site_checked_at"))
                fresh_morning = owner_only and (last_scan is None or last_scan < news_resume_at(started))
                if expired_cursor:
                    state["site_gap_before"] = window_floor.isoformat()
                restart = expired_cursor or fresh_morning
                start_page = 1 if restart else max(1, int(state.get("site_next_page") or 1) - 1)
                # The source clock has minute precision and pages can shift while paging.
                found = await asyncio.to_thread(
                    self._stock_client.search, code, name,
                    since=max(window_floor, stock_since - timedelta(minutes=2)), start_page=start_page,
                )
                items.extend((item, "naver_stock") for item in found.items)
                latest = "" if restart else str(state.get("site_pending_latest_published_at") or "")
                if found.latest_published_at is not None and not latest:
                    latest = found.latest_published_at.isoformat()
                state["site_next_page"] = found.next_page
                state["site_pending_latest_published_at"] = "" if found.complete or found.page_limit_reached else latest
                finished("site", found.complete)
                if found.page_limit_reached:
                    state["site_last_page_limit_at"] = started.isoformat()
                if found.complete and latest:
                    state["site_cursor_published_at"] = latest
            except Exception as error:
                finished("site", False)
                errors.append(error)
        if dart_client is not None and not self._dart_paused and needs_scan("dart"):
            try:
                since_dart = scan_since("dart")
                bounds = ({"begin": as_kst(since_dart).date(), "end": started.date()}
                          if since_dart is not None and isinstance(dart_client, DartDisclosureClient) else {})
                items.extend((item, "dart") for item in await asyncio.to_thread(
                    dart_client.search, code, name, **bounds,
                ))
                finished("dart", True)
            except Exception as error:
                finished("dart", False)
                errors.append(error)
        unique = {
            item.original_link or item.link or f"{item.published_at!s}|{item.title}": (item, collector)
            for item, collector in items
        }
        documents = [(_serialize(item), collector) for item, collector in unique.values()]
        if documents:
            await asyncio.to_thread(self._store.upsert_documents, "news_article", [
                {"owner": code, "key": _identity(value),
                 "document": {"stock_code": code, "stock_name": name, **value},
                 "collector_id": collector, "collection_scope": "watchlist"}
                for value, collector in documents
            ])
        await asyncio.to_thread(self._store.upsert_documents, "news_sync", [{
            "owner": code, "key": "latest",
            "document": {**state, "checked_at": started.isoformat()},
        }])
        if not items and errors:
            cached = await asyncio.to_thread(load_cached_news, self._store, code, name, None)
            stored = await asyncio.to_thread(merge_confirmed_news, self._store, code, name, cached, since)
            if stored:
                if owner_only:
                    return await asyncio.to_thread(load_cached_news, self._store, code, name, since)
                return stored
            raise RuntimeError(" / ".join(str(error) for error in errors))
        if owner_only:
            # Automatic AI consumes committed owner revisions, not the fresh/confirmed display merge.
            return await asyncio.to_thread(load_cached_news, self._store, code, name, since)
        cached = await asyncio.to_thread(load_cached_news, self._store, code, name, None)
        fresh = [value for value, _collector in documents]
        fresh_identities = {_identity(value) for value in fresh}
        owner_values = fresh + [value for value in cached if _identity(value) not in fresh_identities]
        return await asyncio.to_thread(merge_confirmed_news, self._store, code, name, owner_values, since)








def _news_timestamp(value: object) -> datetime | None:
    try:
        return aware_iso_kst(value) if isinstance(value, str) else None
    except ValueError:
        return None


def load_held_news_codes(store, real_bindings, mock_account_refs, cut):
    """Project only positive symbols from existing snapshots for admitted accounts."""
    held = set()

    def add(snapshot, received_at):
        observed = _news_timestamp(snapshot.get("as_of"))
        received = _news_timestamp(received_at)
        if observed is None or received is None or not observed <= received <= cut:
            return
        for raw, quantity in snapshot.get("positions", {}).items():
            code = normalize_stock_code(raw)
            if type(quantity) is int and quantity > 0 and len(code) == 6 and code.isascii() and code.isalnum():
                held.add(code)

    for binding in real_bindings:
        owner = f"kiwoom:real:{binding.scope.account_ref}"
        rows = store.load_documents("real_account_recovery", owner, 1)
        if rows:
            document = rows[0]["document"]
            if (document.get("binding_revision") == binding.binding_revision
                    and document.get("credential_profile_id") == binding.credential_profile_id):
                add(document["recovery"]["account"], document.get("received_at"))
    for account_ref in mock_account_refs:
        snapshot = store.load_mock_news_position_snapshot(account_ref, cut)
        if snapshot is not None:
            add(snapshot, snapshot.get("received_at"))
    return held


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
        input_source=decode_news_input_source(value.get("input_source")),
    )
