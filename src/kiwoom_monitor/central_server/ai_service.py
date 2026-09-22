from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from kiwoom_monitor.infrastructure.naver_news import NewsAISettings
from kiwoom_monitor.infrastructure.news_ai import (
    AICompanyImpact, AINewsAnalysis, AIRequestUsage, DEFAULT_MODELS,
    ANALYSIS_PROMPT_VERSION, analysis_body_hash, analyze_articles, NewsAIProviderError,
)
from kiwoom_monitor.domain.news_observation import (
    ARTICLE_BODY_EXTRACTOR_VERSION,
    NEWS_ANALYSIS_SCHEMA_VERSION,
)
from kiwoom_monitor.infrastructure.article_text import fetch_article_text

from .config import CentralServerSettings
from .database import QueryStore


@dataclass(frozen=True)
class _AIRequestCredential:
    provider: str
    model: str
    revision: int
    api_key: str = field(repr=False)


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
        self._keys = {provider: settings.ai_key(provider) for provider in DEFAULT_MODELS}
        self._credential_revisions = {provider: 0 for provider in DEFAULT_MODELS}
        self._credential_validation = {provider: "UNVERIFIED" for provider in DEFAULT_MODELS}
        self._accepted: dict[str, set[asyncio.Task]] = {provider: set() for provider in DEFAULT_MODELS}
        self._credential_paused: set[str] = set()
        self._closing = False

    async def pause_credentials(self, provider: str) -> None:
        async with self._lock:
            self._credential_paused.add(provider)
            tasks = tuple(self._accepted[provider])
        if tasks:
            await asyncio.gather(*(asyncio.shield(task) for task in tasks), return_exceptions=True)

    def replace_credentials(self, provider: str, api_key: str, revision: int | None, validation: str) -> None:
        if provider not in self._credential_paused:
            raise RuntimeError("AI_CREDENTIAL_DRAIN_REQUIRED")
        self._keys[provider] = api_key
        self._credential_revisions[provider] = revision
        self._credential_validation[provider] = validation

    def resume_credentials(self, provider: str) -> None:
        if not self._closing:
            self._credential_paused.discard(provider)

    async def close(self) -> None:
        async with self._lock:
            self._closing = True
            self._credential_paused.update(DEFAULT_MODELS)
            tasks = tuple(task for values in self._accepted.values() for task in values)
        if tasks:
            await asyncio.gather(*(asyncio.shield(task) for task in tasks), return_exceptions=True)

    def update_operational_settings(self, *, provider: str, model: str, daily_limit: int) -> None:
        self._provider, self._model = provider, model
        self._daily_limit = max(0, int(daily_limit))

    def job_processing_version(self, provider: str, model: str) -> str:
        selected = (self._provider if self._provider != "none" else provider).casefold()
        selected_model = self._model or model or DEFAULT_MODELS.get(selected, "")
        return f"{ANALYSIS_PROMPT_VERSION}:{selected}:{selected_model}"

    async def analyze(
        self, stock_code: str, stock_name: str, provider: str, model: str,
        events: list[dict[str, Any]], article_count: int,
    ) -> dict[str, Any]:
        # NAS 운영 설정을 실제 호출 기준으로 사용한다. 공급자를 '사용 안 함'으로
        # 둔 경우에만 기존 앱별 설정을 보조값으로 받아 이전 동작을 유지한다.
        async with self._lock:
            selected = (self._provider if self._provider != "none" else provider).casefold()
            selected_model = self._model or model or DEFAULT_MODELS.get(selected, "")
            if self._closing or selected in self._credential_paused:
                raise ValueError("NAS AI 연결을 갱신 중입니다. 잠시 후 다시 요청하세요.")
            if not self._keys.get(selected) or self._credential_revisions.get(selected) is None:
                raise ValueError("중앙 서버에 선택한 AI 공급자의 API 키가 설정되지 않았습니다.")
            credential = _AIRequestCredential(selected, selected_model,
                self._credential_revisions[selected], self._keys[selected])
            request = asyncio.create_task(self._prepare_and_run(
                stock_code, stock_name, events, article_count, credential,
            ))
            self._accepted[selected].add(request)
            request.add_done_callback(lambda finished: self._request_finished(selected, finished))
        return await asyncio.shield(request)

    def _request_finished(self, provider, task):
        self._accepted[provider].discard(task)
        if not task.cancelled():
            task.exception()

    def _run_finished(self, key, task):
        if self._running.get(key) is task:
            self._running.pop(key, None)
        if not task.cancelled():
            task.exception()

    async def _prepare_and_run(self, stock_code, stock_name, events, article_count, credential):
        prepared = await asyncio.to_thread(_prepare_events, events, stock_name)
        key = (stock_code, credential.provider, credential.model, credential.revision,
               *((str(value["identity"]), str(value["body_hash"])) for value in prepared))
        async with self._lock:
            task = self._running.get(key)
            if task is None:
                task = asyncio.create_task(self._run(
                    stock_code, stock_name, credential.provider, credential.model, prepared, article_count,
                    credential,
                ))
                self._running[key] = task
                task.add_done_callback(lambda finished: self._run_finished(key, finished))
        return await asyncio.shield(task)

    async def _run(self, stock_code: str, stock_name: str, provider: str, model: str,
                   events: list[dict[str, Any]], article_count: int, credential: _AIRequestCredential) -> dict[str, Any]:
        cached_rows = await asyncio.to_thread(self._store.load_documents, "news_ai", stock_code, 10_000)
        cached = {str(row["key"]): row["document"] for row in cached_rows}
        results: list[dict[str, Any] | None] = [None] * len(events)
        analysis_revision_ids: list[str | None] = [None] * len(events)
        missing: list[tuple[int, dict[str, Any]]] = []
        for index, event in enumerate(events):
            value = cached.get(str(event["identity"]))
            if isinstance(value, dict) and value.get("body_hash") == event.get("body_hash") \
                    and value.get("provider") == provider and value.get("model") == model:
                results[index] = value
                article_revision_id = str(event.get("article_revision_id") or "")
                body_revision_id = str(event.get("body_revision_id") or "")
                if article_revision_id and body_revision_id \
                        and hasattr(self._store, "find_news_ai_revision"):
                    analysis_revision_ids[index] = await asyncio.to_thread(
                        self._store.find_news_ai_revision,
                        target_id=stock_code, article_revision_id=article_revision_id,
                        body_revision_id=body_revision_id, provider=provider, model=model,
                        prompt_version=ANALYSIS_PROMPT_VERSION,
                        schema_version=NEWS_ANALYSIS_SCHEMA_VERSION,
                        input_hash=str(event.get("body_hash") or ""),
                    )
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
                if hasattr(self._store, "load_news_history"):
                    for _index, event in missing:
                        article_revision_id = str(event.get("article_revision_id") or "")
                        if not article_revision_id:
                            article_rows = await asyncio.to_thread(
                                self._store.load_news_history, "article", target=stock_code,
                                identity=str(event["identity"]), limit=1,
                            )
                            if article_rows:
                                article_revision_id = str(article_rows[0]["article_revision_id"])
                                event["article_revision_id"] = article_revision_id
                        if article_revision_id and not event.get("body_revision_id") \
                                and hasattr(self._store, "save_news_body_revision"):
                            event["body_revision_id"] = await asyncio.to_thread(
                                self._store.save_news_body_revision, {
                                    "article_revision_id": article_revision_id,
                                    "extractor_version": ARTICLE_BODY_EXTRACTOR_VERSION,
                                    "fetched_at": datetime.now(UTC).timestamp(),
                                    "status": str(event.get("body_status") or "fulltext"),
                                    "body_text": str(event["body"]), "error": "",
                                },
                            )
                ai_settings = NewsAISettings(provider, credential.api_key, model)
                try:
                    analyses, usage = await asyncio.to_thread(
                        analyze_articles, ai_settings, stock_name,
                        tuple((str(event["title"]), str(event["body"])) for _, event in missing),
                    )
                except NewsAIProviderError as error:
                    if self._credential_revisions[provider] == credential.revision:
                        self._credential_validation[provider] = (
                            "INVALID_CREDENTIAL" if error.status_code == 401 else
                            "ACCESS_DENIED" if error.status_code == 403 else
                            "RETRYABLE" if error.status_code == 429 or error.status_code >= 500 else "REQUEST_FAILED"
                        )
                    raise
                except OSError:
                    if self._credential_revisions[provider] == credential.revision:
                        self._credential_validation[provider] = "RETRYABLE"
                    raise
                if self._credential_revisions[provider] == credential.revision:
                    self._credential_validation[provider] = "VERIFIED"
                now = datetime.now(UTC).isoformat()
                documents = []
                for (index, event), analysis in zip(missing, analyses, strict=True):
                    value = _analysis_document(
                        stock_code, str(event["identity"]), provider, model,
                        str(event.get("body_hash", "")), now, analysis,
                    )
                    results[index] = value
                    value["credential_revision"] = credential.revision
                    documents.append({"owner": stock_code, "key": str(event["identity"]), "document": value})
                usage_documents = [{
                    "owner": usage_owner, "key": str(uuid4()),
                    "document": {
                        "requested_at": datetime.now().astimezone().isoformat(), "provider": provider,
                        "model": model, "request_mode": "batch" if len(missing) > 1 else "single",
                        "credential_revision": credential.revision,
                        "event_count": len(missing), "article_count": article_count,
                        "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
                        "total_tokens": usage.total_tokens,
                    },
                }]
                if hasattr(self._store, "save_news_ai_results"):
                    revisions = []
                    for (index, event), document in zip(missing, documents, strict=True):
                        article_revision_id = str(event.get("article_revision_id") or "")
                        body_revision_id = str(event.get("body_revision_id") or "")
                        if not article_revision_id or not body_revision_id:
                            continue
                        revision_id = uuid4().hex
                        analysis_revision_ids[index] = revision_id
                        revisions.append({
                            "analysis_revision_id": revision_id,
                            "target_id": stock_code,
                            "article_revision_id": article_revision_id,
                            "body_revision_id": body_revision_id,
                            "provider": provider, "model": model,
                            "prompt_version": ANALYSIS_PROMPT_VERSION,
                            "schema_version": NEWS_ANALYSIS_SCHEMA_VERSION,
                            "input_hash": str(event.get("body_hash", "")),
                            "computed_at": datetime.now(UTC).timestamp(),
                            "output": document["document"], "usage": usage.__dict__,
                        })
                    await asyncio.to_thread(
                        self._store.save_news_ai_results, documents, revisions, usage_documents,
                    )
                else:
                    await asyncio.to_thread(self._store.upsert_documents, "news_ai", documents)
                    await asyncio.to_thread(
                        self._store.upsert_documents, "news_request_usage", usage_documents,
                    )
        return {
            "provider": provider, "model": model, "results": results,
            "credential_revision": credential.revision,
            "body_hashes": [str(value.get("body_hash", "")) for value in events],
            "usage": usage.__dict__, "cache_hit": not missing,
            "analysis_revision_ids": [value for value in analysis_revision_ids if value],
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
        "theme_candidates": [candidate.__dict__ for candidate in analysis.theme_candidates],
        "body_hash": body_hash, "analyzed_at": analyzed_at,
    }


def _prepare_events(events: list[dict[str, Any]], stock_name: str = "") -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for event in events:
        body = str(event.get("body", "")).strip()
        body_status = "fulltext"
        if not body:
            sections: list[str] = []
            saw_fulltext = False
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
                            if article_body:
                                saw_fulltext = True
                                break
                        except Exception:
                            continue
                    if not article_body:
                        article_body = str(article.get("description", "")).strip()
                    if article_body:
                        sections.append(f"[관련 기사 {index}/{len(articles)}: {title}]\n{article_body}")
            body = "\n\n".join(sections)
            body_status = "fulltext" if saw_fulltext else "summary_only"
        if not body:
            raise ValueError("기사 본문과 검색 요약을 가져오지 못했습니다.")
        value = dict(event)
        value["body"] = body
        value["body_hash"] = analysis_body_hash(stock_name, body)
        value["body_status"] = body_status
        prepared.append(value)
    return prepared
