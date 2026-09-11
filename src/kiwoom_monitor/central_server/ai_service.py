from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from kiwoom_monitor.infrastructure.naver_news import NewsAISettings
from kiwoom_monitor.infrastructure.news_ai import (
    AICompanyImpact, AINewsAnalysis, AIRequestUsage, DEFAULT_MODELS,
    analysis_body_hash, analyze_articles,
)
from kiwoom_monitor.infrastructure.article_text import fetch_article_text

from .config import CentralServerSettings
from .database import QueryStore


class CentralAIService:
    """AI 판정 캐시와 실행 중 요청을 중앙에서 공유한다."""

    def __init__(self, settings: CentralServerSettings, store: QueryStore) -> None:
        self._settings, self._store = settings, store
        self._provider = settings.ai_provider
        self._model = settings.ai_model
        self._daily_limit = settings.ai_daily_limit
        self._lock = asyncio.Lock()
        self._execution_lock = asyncio.Lock()
        self._running: dict[tuple[object, ...], asyncio.Task[dict[str, Any]]] = {}

    def update_operational_settings(self, *, provider: str, model: str, daily_limit: int) -> None:
        self._provider, self._model = provider, model
        self._daily_limit = max(0, int(daily_limit))

    async def analyze(
        self, stock_code: str, stock_name: str, provider: str, model: str,
        events: list[dict[str, Any]], article_count: int,
    ) -> dict[str, Any]:
        prepared = await asyncio.to_thread(_prepare_events, events, stock_name)
        # NAS 운영 설정을 실제 호출 기준으로 사용한다. 공급자를 '사용 안 함'으로
        # 둔 경우에만 기존 앱별 설정을 보조값으로 받아 이전 동작을 유지한다.
        selected = (self._provider if self._provider != "none" else provider).casefold()
        selected_model = self._model or model or DEFAULT_MODELS.get(selected, "")
        if not self._settings.ai_key(selected) or selected not in DEFAULT_MODELS:
            raise ValueError("중앙 서버에 선택한 AI 공급자의 API 키가 설정되지 않았습니다.")
        key = (stock_code, selected, selected_model, *(str(value["identity"]) for value in prepared))
        async with self._lock:
            task = self._running.get(key)
            if task is None:
                task = asyncio.create_task(self._run(
                    stock_code, stock_name, selected, selected_model, prepared, article_count,
                ))
                self._running[key] = task
        try:
            return await asyncio.shield(task)
        finally:
            if task.done():
                async with self._lock:
                    if self._running.get(key) is task:
                        self._running.pop(key, None)

    async def _run(self, stock_code: str, stock_name: str, provider: str, model: str,
                   events: list[dict[str, Any]], article_count: int) -> dict[str, Any]:
        cached_rows = await asyncio.to_thread(self._store.load_documents, "news_ai", stock_code, 10_000)
        cached = {str(row["key"]): row["document"] for row in cached_rows}
        results: list[dict[str, Any] | None] = [None] * len(events)
        missing: list[tuple[int, dict[str, Any]]] = []
        for index, event in enumerate(events):
            value = cached.get(str(event["identity"]))
            if isinstance(value, dict) and value.get("body_hash") == event.get("body_hash") \
                    and value.get("provider") == provider and value.get("model") == model:
                results[index] = value
            else:
                missing.append((index, event))
        usage = AIRequestUsage()
        if missing:
            # 공급자 RPM과 일일 상한은 모든 PC를 합친 중앙 기준으로 적용한다.
            async with self._execution_lock:
                usage_owner = datetime.now().astimezone().date().isoformat()
                if self._daily_limit > 0:
                    used = await asyncio.to_thread(
                        self._store.load_documents, "news_request_usage", usage_owner, 10_000,
                    )
                    if len(used) >= self._daily_limit:
                        raise ValueError(f"NAS AI 일일 상한 {self._daily_limit}회를 모두 사용했습니다.")
                ai_settings = NewsAISettings(provider, self._settings.ai_key(provider), model)
                analyses, usage = await asyncio.to_thread(
                    analyze_articles, ai_settings, stock_name,
                    tuple((str(event["title"]), str(event["body"])) for _, event in missing),
                )
                now = datetime.now(UTC).isoformat()
                documents = []
                for (index, event), analysis in zip(missing, analyses, strict=True):
                    value = _analysis_document(
                        stock_code, str(event["identity"]), provider, model,
                        str(event.get("body_hash", "")), now, analysis,
                    )
                    results[index] = value
                    documents.append({"owner": stock_code, "key": str(event["identity"]), "document": value})
                await asyncio.to_thread(self._store.upsert_documents, "news_ai", documents)
                await asyncio.to_thread(self._store.upsert_documents, "news_request_usage", [{
                    "owner": usage_owner, "key": str(uuid4()),
                    "document": {
                        "requested_at": datetime.now().astimezone().isoformat(), "provider": provider,
                        "model": model, "request_mode": "batch" if len(missing) > 1 else "single",
                        "event_count": len(missing), "article_count": article_count,
                        "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
                        "total_tokens": usage.total_tokens,
                    },
                }])
        return {
            "provider": provider, "model": model, "results": results,
            "body_hashes": [str(value.get("body_hash", "")) for value in events],
            "usage": usage.__dict__, "cache_hit": not missing,
        }


def _analysis_document(stock_code: str, identity: str, provider: str, model: str,
                       body_hash: str, analyzed_at: str, analysis: AINewsAnalysis) -> dict[str, Any]:
    return {
        "stock_code": stock_code, "identity": identity, "provider": provider, "model": model,
        "summary": analysis.summary, "category": analysis.category, "outlook": analysis.outlook,
        "confidence": analysis.confidence, "reason": analysis.reason,
        "positive_evidence": json.dumps(analysis.positive_evidence, ensure_ascii=False),
        "negative_evidence": json.dumps(analysis.negative_evidence, ensure_ascii=False),
        "company_impacts": [impact.__dict__ for impact in analysis.company_impacts],
        "body_hash": body_hash, "analyzed_at": analyzed_at,
    }


def _prepare_events(events: list[dict[str, Any]], stock_name: str = "") -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for event in events:
        body = str(event.get("body", "")).strip()
        if not body:
            sections: list[str] = []
            articles = event.get("articles", ())
            if isinstance(articles, list):
                for index, article in enumerate(articles, start=1):
                    if not isinstance(article, dict):
                        continue
                    title = str(article.get("title", ""))
                    article_body = ""
                    for url in dict.fromkeys((str(article.get("link", "")), str(article.get("original_link", "")))):
                        if not url:
                            continue
                        try:
                            article_body = fetch_article_text(url)
                            break
                        except Exception:
                            continue
                    if not article_body:
                        article_body = str(article.get("description", "")).strip()
                    if article_body:
                        sections.append(f"[관련 기사 {index}/{len(articles)}: {title}]\n{article_body}")
            body = "\n\n".join(sections)
        if not body:
            raise ValueError("기사 본문과 검색 요약을 가져오지 못했습니다.")
        value = dict(event)
        value["body"] = body
        value["body_hash"] = analysis_body_hash(stock_name, body)
        prepared.append(value)
    return prepared
