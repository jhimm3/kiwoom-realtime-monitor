from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any

from kiwoom_monitor.application.news_analysis import NewsAssessment
from kiwoom_monitor.application.news_grouping import group_similar_news
from kiwoom_monitor.infrastructure.dart_disclosures import DartDisclosureClient
from kiwoom_monitor.infrastructure.naver_news import NaverNewsClient, StockNewsItem

from .database import QueryStore


class CentralNewsService:
    """동일 종목의 동시 뉴스 요청을 하나의 네이버 호출로 합친다."""

    def __init__(
        self, client: NaverNewsClient | None, store: QueryStore,
        dart_client: DartDisclosureClient | None = None, *, refresh_seconds: int = 300,
    ) -> None:
        self._client = client
        self._store = store
        self._dart_client = dart_client
        self._dart_enabled = dart_client is not None
        self._lock = asyncio.Lock()
        self._running: dict[tuple[str, str], asyncio.Task[list[dict[str, Any]]]] = {}
        self._refresh_seconds = max(60, int(refresh_seconds))
        self._refresh_task: asyncio.Task[None] | None = None
        self._closing = asyncio.Event()
        self._ai_service: Any = None

    def update_operational_settings(self, *, refresh_seconds: int, dart_enabled: bool) -> None:
        self._refresh_seconds = max(60, int(refresh_seconds))
        self._dart_enabled = bool(dart_enabled)

    def set_ai_service(self, service: Any) -> None:
        self._ai_service = service

    async def start(self) -> None:
        if self._refresh_task is None:
            self._closing.clear()
            self._refresh_task = asyncio.create_task(self._refresh_loop(), name="central-news-refresh")

    async def close(self) -> None:
        self._closing.set()
        task, self._refresh_task = self._refresh_task, None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def search(
        self, code: str, name: str, since: datetime | None,
        automation: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
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
            if task is None:
                task = asyncio.create_task(self._collect(code, name, since))
                self._running[key] = task
        try:
            return await asyncio.shield(task)
        finally:
            if task.done():
                async with self._lock:
                    if self._running.get(key) is task:
                        self._running.pop(key, None)

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
                items = await self.search(code, name, since, automation={
                    "auto_analyze": bool(document.get("auto_analyze", False)),
                    "auto_recent_limit": int(document.get("auto_recent_limit", 10)),
                    "provider": str(document.get("provider", "none")),
                    "model": str(document.get("model", "")),
                })
                await self._auto_analyze(code, name, items, document)
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
        items = tuple(_deserialize(value) for value in values if bool(value.get("relevant", 0)))
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

    async def _collect(self, code: str, name: str, since: datetime | None) -> list[dict[str, Any]]:
        sync_rows = await asyncio.to_thread(self._store.load_documents, "news_sync", code, 1)
        if sync_rows:
            try:
                checked_at = datetime.fromisoformat(str(sync_rows[0]["document"]["checked_at"]))
            except (KeyError, TypeError, ValueError):
                checked_at = None
            if checked_at is not None and datetime.now(UTC) - checked_at.astimezone(UTC) < timedelta(minutes=5):
                return await asyncio.to_thread(self._load_cached, code, since)
        items: list[StockNewsItem] = []
        errors: list[Exception] = []
        if self._client is not None:
            try:
                items.extend(await asyncio.to_thread(self._client.search, name, since=since))
            except Exception as error:  # 외부 뉴스 공급자 하나의 실패는 다른 공급자를 막지 않는다.
                errors.append(error)
        if self._dart_enabled and self._dart_client is not None:
            try:
                items.extend(await asyncio.to_thread(self._dart_client.search, code, name))
            except Exception as error:
                errors.append(error)
        if not items and errors:
            raise RuntimeError(" / ".join(str(error) for error in errors))
        unique = {
            item.original_link or item.link or f"{item.published_at!s}|{item.title}": item
            for item in items
        }
        documents = [_serialize(item) for item in unique.values()]
        await asyncio.to_thread(self._store.upsert_documents, "news_article", [
            {"owner": code, "key": _identity(value), "document": {"stock_code": code, **value}}
            for value in documents
        ])
        await asyncio.to_thread(self._store.upsert_documents, "news_sync", [{
            "owner": code, "key": "latest", "document": {"checked_at": datetime.now(UTC).isoformat()},
        }])
        return documents

    def _load_cached(self, code: str, since: datetime | None) -> list[dict[str, Any]]:
        documents = self._store.load_documents("news_article", code, 1000)
        values = [value["document"] for value in documents if isinstance(value.get("document"), dict)]
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


def _identity(value: dict[str, Any]) -> str:
    return str(value.get("original_link") or value.get("link") or f'{value.get("published_at", "")}|{value.get("title", "")}')


def _serialize(item: StockNewsItem) -> dict[str, Any]:
    assessment = item.assessment
    return {
        "title": item.title, "description": item.description, "link": item.link,
        "original_link": item.original_link,
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "relevant": int(assessment.relevant), "category": assessment.category,
        "outlook": assessment.outlook, "reason": assessment.reason,
        "relevance_score": assessment.relevance_score, "outlook_score": assessment.outlook_score,
    }


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
