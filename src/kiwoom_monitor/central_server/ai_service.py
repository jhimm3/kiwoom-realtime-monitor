from __future__ import annotations


import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable, Protocol
from uuid import NAMESPACE_URL, uuid4, uuid5
from copy import deepcopy

from kiwoom_monitor.infrastructure.naver_news import NewsAISettings
from kiwoom_monitor.infrastructure.news_ai import (
    AICompanyImpact, AINewsAnalysis, AIRequestUsage, DEFAULT_MODELS,
    ANALYSIS_PROMPT_VERSION, analysis_body_hash, analyze_articles, NewsAIProviderError,
)
from kiwoom_monitor.domain.news_observation import (
    ARTICLE_BODY_EXTRACTOR_VERSION, AIJobResultError,
    NEWS_ANALYSIS_SCHEMA_VERSION, stable_document_hash,
)
from kiwoom_monitor.infrastructure.article_text import fetch_article_text
from kiwoom_monitor.infrastructure.chatgpt_auth import analysis_pause_for_day
from .market_observations import KST, as_kst
from kiwoom_monitor.domain.news_ai_contract import (
    decode_news_ai_provenance, news_ai_source_member, freeze_news_ai_input,
    decode_news_input_source,
)

from .config import CentralServerSettings


LOGGER = logging.getLogger(__name__)


class CentralAIStore(Protocol):
    """CentralAIService가 사용하는 뉴스 문서·revision 저장 계약."""

    def load_documents(
        self, collection: str, owner: str = "", limit: int = 1000, offset: int = 0,
        updated_after: float = 0.0,
    ) -> list[dict[str, Any]]: ...

    def find_news_ai_revision(
        self, *, target_id: str, article_revision_id: str, body_revision_id: str,
        provider: str, model: str, prompt_version: str, schema_version: str,
        input_hash: str,
    ) -> str | None: ...

    def load_news_history(
        self, kind: str, *, target: str = "", identity: str = "",
        available_at: float | None = None, limit: int = 100,
    ) -> list[dict[str, Any]]: ...

    def save_news_body_revision_receipt(self, value: dict[str, Any]) -> dict[str, Any]: ...

    def save_news_ai_results(
        self, documents: list[dict[str, Any]], revisions: list[dict[str, Any]],
        usage_documents: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, object]]: ...

    def load_document(self, collection: str, owner: str, key: str) -> dict[str, Any] | None: ...

    def accept_news_job_result(self, job_key: str, result: dict[str, Any], *,
                               attempts: int) -> dict[str, Any]: ...

    def upsert_documents(self, collection: str, values: list[dict[str, Any]]) -> None: ...


@dataclass(frozen=True)
class _AIRequestCredential:
    provider: str
    model: str
    revision: int
    api_key: str = field(repr=False)





@dataclass
class _AIInFlight:
    task: asyncio.Task | None = None
    requests: dict[asyncio.Task, dict[str, Any] | None] = field(default_factory=dict)
    publications: dict[tuple[object, ...], asyncio.Task] = field(default_factory=dict)


class CentralAIService:
    """AI 판정 캐시와 실행 중 요청을 중앙에서 공유한다."""

    def __init__(self, settings: CentralServerSettings, store: CentralAIStore, *, chatgpt_auth=None,
                 now_provider: Callable[[], datetime] | None = None) -> None:
        self._now = (lambda: as_kst(now_provider())) if now_provider else (lambda: datetime.now(KST))
        self._settings, self._store = settings, store
        self._provider = settings.ai_provider
        self._model = settings.ai_model
        self._daily_limit = settings.ai_daily_limit
        self._lock = asyncio.Lock()
        self._execution_lock = asyncio.Lock()
        self._running: dict[tuple[object, ...], _AIInFlight] = {}
        self._keys = {provider: settings.ai_key(provider) for provider in DEFAULT_MODELS}
        self._credential_revisions = {provider: 0 for provider in DEFAULT_MODELS}
        self._credential_validation = {provider: "UNVERIFIED" for provider in DEFAULT_MODELS}
        self._accepted: dict[str, set[asyncio.Task]] = {provider: set() for provider in DEFAULT_MODELS}
        self._credential_paused: set[str] = set()
        self._closing = False
        self.chatgpt_auth = chatgpt_auth
        self._plan_failure = ""
        self._plan_failure_day = None
        if chatgpt_auth is not None:
            self._accepted["chatgpt_plan"] = set()
            self._credential_revisions["chatgpt_plan"] = 0
            self._credential_validation["chatgpt_plan"] = "UNVERIFIED"

    async def pause_credentials(self, provider: str, *, require_idle: bool = False) -> None:
        async with self._lock:
            tasks = tuple(self._accepted[provider])
            if require_idle and (tasks or self._closing):
                from kiwoom_monitor.infrastructure.chatgpt_auth import ChatGPTPlanError
                raise ChatGPTPlanError("storage_busy")
            self._credential_paused.add(provider)
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
            self._credential_paused.update(self._accepted)
            tasks = tuple(task for values in self._accepted.values() for task in values)
        if tasks:
            await asyncio.gather(*(asyncio.shield(task) for task in tasks), return_exceptions=True)

    def update_operational_settings(self, *, provider: str, model: str, daily_limit: int) -> None:
        self._provider, self._model = provider, model
        self._daily_limit = max(0, int(daily_limit))

    def resume_chatgpt(self) -> None:
        """Explicit operator action; failures never trigger paid fallback/retry."""
        self._plan_failure = ""
        self._plan_failure_day = None

    @property
    def chatgpt_failure(self) -> str:
        return analysis_pause_for_day(self._plan_failure, self._plan_failure_day, self._now().date())

    def _remember_chatgpt_failure(self, code: str) -> None:
        self._plan_failure = code
        self._plan_failure_day = self._now().date().isoformat()

    def job_processing_version(self, provider: str, model: str) -> str:
        selected = (self._provider if self._provider != "none" else provider).casefold()
        selected_model = self._model or model or DEFAULT_MODELS.get(selected, "")
        return f"{ANALYSIS_PROMPT_VERSION}:{selected}:{selected_model}"

    async def analyze(
        self, stock_code: str, stock_name: str, provider: str, model: str,
        events: list[dict[str, Any]], article_count: int, *, job_timeout: float | None = None,
        _job: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # NAS 운영 설정을 실제 호출 기준으로 사용한다. 공급자를 '사용 안 함'으로
        # 둔 경우에만 기존 앱별 설정을 보조값으로 받아 이전 동작을 유지한다.
        async with self._lock:
            selected = (self._provider if self._provider != "none" else provider).casefold()
            selected_model = self._model or model or DEFAULT_MODELS.get(selected, "")
            if provider.casefold() == "chatgpt_plan" and selected != "chatgpt_plan":
                from kiwoom_monitor.infrastructure.chatgpt_auth import ChatGPTPlanError
                raise ChatGPTPlanError("provider_mismatch")
            if selected == "chatgpt_plan" and self._provider == "none":
                selected_model = model
            if self._closing or selected in self._credential_paused:
                raise ValueError("NAS AI 연결을 갱신 중입니다. 잠시 후 다시 요청하세요.")
            if selected == "chatgpt_plan":
                from kiwoom_monitor.infrastructure.chatgpt_auth import ChatGPTPlanError
                if self.chatgpt_auth is None:
                    raise ChatGPTPlanError("not_connected")
                if failure := self.chatgpt_failure:
                    raise ChatGPTPlanError(failure)
                status = await asyncio.to_thread(self.chatgpt_auth.status)
                if failure := status.get("pause_code"):
                    raise ChatGPTPlanError(failure)
                account = next((item for item in status["accounts"] if item["id"] == status["active"]), {})
                if not account.get("connected") or not account.get("permitted") or not selected_model:
                    raise ChatGPTPlanError("permission_required" if account.get("connected") else "not_connected")
                # Stable imported-session generation, separate from token rotation.
                self._credential_revisions[selected] = int(status.get("generation", 0))
            elif not self._keys.get(selected) or self._credential_revisions.get(selected) is None:
                raise ValueError("중앙 서버에 선택한 AI 공급자의 API 키가 설정되지 않았습니다.")
            credential = _AIRequestCredential(selected, selected_model,
                self._credential_revisions[selected], self._keys.get(selected, ""))
            request = asyncio.create_task(self._prepare_and_run(
                stock_code, stock_name, events, article_count, credential, _job,
            ))
            self._accepted[selected].add(request)
            request.add_done_callback(lambda finished: self._request_finished(selected, finished))
        if job_timeout is not None and selected != "chatgpt_plan":
            try:
                return await asyncio.wait_for(asyncio.shield(request), timeout=job_timeout)
            except TimeoutError:
                if _job is not None:
                    if request.done():
                        return request.result()
                    # This is the waiter's deadline. The existing accepted task
                    # still owns RUNNING and will publish or apply real failure.
                    return {"job_receipt": {"state": "in_progress", "output_ref": ""}}
                raise
        # Subscription transport already bounds network/stream lifetime. A job
        # timeout must not enqueue a second POST while the shielded first lives.
        return await asyncio.shield(request)

    async def analyze_job(self, stock_code, stock_name, provider, model, events, article_count, *,
                          job_key: str, attempts: int, output_ref_fallback: str, timeout_seconds: float):
        return await self.analyze(stock_code, stock_name, provider, model, events, article_count,
                                  job_timeout=timeout_seconds,
                                  _job={"job_key": job_key, "attempts": attempts,
                                        "output_ref_fallback": output_ref_fallback})

    async def research_market(self, prompt: str, provider: str, model: str, effort: str):
        """Own a grounded request through credential drain and shared usage limits."""
        from kiwoom_monitor.infrastructure.chatgpt_auth import ChatGPTPlanError
        from kiwoom_monitor.infrastructure.chatgpt_plan import validate_reasoning
        validate_reasoning(model, effort)
        async with self._lock:
            if self._closing or provider in self._credential_paused:
                raise ValueError("NAS AI 연결 갱신 또는 종료 중입니다.")
            if provider == "chatgpt_plan":
                if self.chatgpt_auth is None:
                    raise ChatGPTPlanError("not_connected")
                status = await asyncio.to_thread(self.chatgpt_auth.status)
                if failure := self.chatgpt_failure or status.get("pause_code"):
                    raise ChatGPTPlanError(failure)
                selected = next((a for a in status["accounts"] if a["id"] == status["active"]), {})
                if not selected.get("connected") or not selected.get("permitted"):
                    raise ChatGPTPlanError("not_connected")
                revision = int(status.get("generation", 0))
            elif provider == "openai" and self._keys.get(provider) and self._credential_revisions.get(provider) is not None:
                revision = self._credential_revisions[provider]
            else:
                raise ValueError("시황용 AI 공급자 연결이 필요합니다.")
            credential = _AIRequestCredential(provider, model, revision, self._keys.get(provider, ""))
            task = asyncio.create_task(self._research_market(prompt, credential, effort), name="central-ai-market-research")
            self._accepted[provider].add(task)
            task.add_done_callback(lambda done: self._request_finished(provider, done))
        return await asyncio.shield(task)

    async def _research_market(self, prompt, credential, effort):
        from zoneinfo import ZoneInfo
        from kiwoom_monitor.infrastructure.news_ai import research_market
        from kiwoom_monitor.infrastructure.chatgpt_auth import ChatGPTPlanError
        async with self._execution_lock:
            provider = credential.provider
            if provider == "chatgpt_plan":
                status = await asyncio.to_thread(self.chatgpt_auth.status)
                if failure := self.chatgpt_failure or status.get("pause_code"):
                    raise ChatGPTPlanError(failure)
                if int(status.get("generation", 0)) != credential.revision:
                    raise ChatGPTPlanError("not_connected")
            owner = as_kst(self._now()).date().isoformat()
            used = await asyncio.to_thread(self._store.load_documents, "news_request_usage", owner, 10_000)
            if self._daily_limit > 0 and len(used) >= self._daily_limit:
                raise ValueError("NAS AI 일일 요청 상한에 도달했습니다.")
            settings = NewsAISettings(provider, credential.api_key, credential.model)
            try:
                result = await asyncio.to_thread(research_market, settings, prompt,
                    chatgpt_auth=self.chatgpt_auth, reasoning_effort=effort)
            except Exception as error:
                if provider == "chatgpt_plan":
                    self._remember_chatgpt_failure(
                        error.code if isinstance(error, ChatGPTPlanError) else "response_invalid")
                    await asyncio.to_thread(self.chatgpt_auth.pause_analysis, self._plan_failure)
                if provider != "chatgpt_plan" or getattr(error, "inference_attempted", False):
                    await asyncio.to_thread(self._store.upsert_documents, "news_request_usage", [{
                        "owner": owner, "key": str(uuid4()), "document": {
                            "provider": provider, "model": credential.model, "purpose": "morning_market_brief",
                            "requested_at": self._now().isoformat(), "status": "failed",
                            "usage_measured": False, "request_count": 1}}])
                raise
            await asyncio.to_thread(self._store.upsert_documents, "news_request_usage", [{
                "owner": owner, "key": str(uuid4()), "document": {
                    "provider": provider, "model": credential.model, "purpose": "morning_market_brief",
                    "reasoning_effort": effort, "credential_revision": credential.revision,
                    "requested_at": self._now().isoformat(), "status": "completed",
                    "usage_measured": True, "request_count": 1, **result.usage}}])
            return result

    def _request_finished(self, provider, task):
        self._accepted[provider].discard(task)
        if not task.cancelled():
            task.exception()

    def _run_finished(self, key, entry):
        task = entry.task
        if task is not None and not task.cancelled():
            task.exception()
        if not entry.requests and self._running.get(key) is entry:
            self._running.pop(key, None)

    async def _prepare_and_run(self, stock_code, stock_name, events, article_count, credential, job=None):
        # This existing accepted owner drains the complete requester lifetime,
        # including native acceptance, even if its own waiter is cancelled.
        task = asyncio.create_task(self._execute_request(
            stock_code, stock_name, events, article_count, credential, job,
        ))
        return await self._join_owned(task)

    async def _execute_request(self, stock_code, stock_name, events, article_count, credential, job):
        entry, key = None, None
        request = asyncio.current_task()
        failure_stage = "prepare"
        try:
            prepared = await asyncio.to_thread(_prepare_events, events, stock_name, now_provider=self._now)
            key = (stock_code, credential.provider, credential.model, credential.revision,
                   *((str(value["identity"]), str(value["body_hash"])) for value in prepared))
            # Capture caller authority before a legacy direct producer resolves
            # its own real refs. Unpinned direct waiters share that publication;
            # queued requests always retain their own exact pinned refs.
            binding_key = ("direct", *((str(event["identity"]), str(event.get("article_revision_id") or ""),
                                        str(event.get("body_revision_id") or "")) for event in prepared))
            if job is not None:
                binding_key = ("job", str(job["job_key"]), int(job["attempts"]))
            async with self._lock:
                entry = self._running.get(key)
                if (entry is not None and entry.task.done() and not entry.task.cancelled()
                        and entry.task.exception() is None and "results" not in entry.task.result()):
                    entry = None  # A later explicit request must not inherit a no-result completion.
                if entry is None:
                    entry = _AIInFlight()
                    entry.task = asyncio.create_task(self._run(
                        stock_code, stock_name, credential.provider, credential.model,
                        prepared, article_count, credential, entry,
                    ))
                    self._running[key] = entry
                    entry.task.add_done_callback(lambda finished: self._run_finished(key, entry))
                entry.requests[request] = job
            failure_stage = "inference"
            outcome = await self._join_owned(entry.task)
            if "results" not in outcome:
                # A requester can attach while the shared no-result admission
                # is settling. Re-enter the same owner rather than fabricate an
                # inference response; a completed no-result entry is not reused.
                if job is None or (str(job["job_key"]), int(job["attempts"])) not in outcome["unexecuted_jobs"]:
                    return await self._execute_request(stock_code, stock_name, events, article_count, credential, job)
            if job is not None:
                receipt = outcome.get("unexecuted_jobs", {}).get((str(job["job_key"]), int(job["attempts"])))
                if receipt is not None:
                    return {"job_receipt": receipt}
            failure_stage = "publication"
            async with self._lock:
                publication = entry.publications.get(binding_key)
                if publication is None:
                    publication = asyncio.create_task(
                        self._publish_job(stock_code, prepared, outcome, job) if job is not None
                        else self._publish_direct(stock_code, prepared, outcome)
                    )
                    entry.publications[binding_key] = publication
            return await self._join_owned(publication)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if job is None:
                raise
            try:
                receipt = await self._native_call(self._store.accept_news_job_result, job["job_key"], {
                    "stage": "AI", "error": str(error),
                }, attempts=int(job["attempts"]))
            except Exception as settlement_error:
                # A failed ACK/read leaves authority unknown. Preserve both
                # errors and leave restart recovery to the existing stale claim.
                # The accepted owner records this before any waiter/callback can
                # discard the exception. Messages and provider payloads are never logged.
                key_id = str(job["job_key"])
                key_id = key_id if len(key_id) <= 64 and key_id.isalnum() else "unavailable"
                LOGGER.error("AI accepted settlement unknown stage=%s job=%s attempt=%d source_type=%s settlement_type=%s",
                             failure_stage, key_id, int(job["attempts"]),
                             type(error).__name__[:80], type(settlement_error).__name__[:80])
                raise AIJobResultError(error, None, settlement_error=settlement_error) from settlement_error
            raise AIJobResultError(error, receipt) from error
        finally:
            if entry is not None:
                async with self._lock:
                    entry.requests.pop(request, None)
                    if not entry.requests and entry.task.done() and self._running.get(key) is entry:
                        self._running.pop(key, None)

    async def _join_owned(self, task):
        """An accepted owner cannot leave a native/provider task detached."""
        cancelled = False
        while True:
            try:
                result = await asyncio.shield(task)
                break
            except asyncio.CancelledError:
                if task.cancelled():
                    raise
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError
        return result

    async def _native_call(self, function, *args, **kwargs):
        return await self._join_owned(asyncio.create_task(asyncio.to_thread(function, *args, **kwargs)))

    async def _persist_results(self, documents, revisions, usage_documents=None):
        if hasattr(self._store, "save_news_ai_results"):
            return await self._native_call(self._store.save_news_ai_results, documents, revisions, usage_documents)
        # Preserve existing small test/legacy stores; production has the native command.
        if documents:
            await self._native_call(self._store.upsert_documents, "news_ai", documents)
        if usage_documents:
            await self._native_call(self._store.upsert_documents, "news_request_usage", usage_documents)

    async def _save_usage(self, usage_document):
        try:
            await self._persist_results([], [], [usage_document])
        except Exception as original_error:
            try:
                stored = await self._native_call(self._store.load_document, "news_request_usage",
                                                 usage_document["owner"], usage_document["key"])
            except Exception as read_error:
                raise original_error from read_error
            if not stored or stable_document_hash(stored["document"]) != stable_document_hash(usage_document["document"]):
                raise
            inference = usage_document.get("inference")
            if inference is not None:
                rows = await self._native_call(self._store.load_news_history, "inference",
                                               identity=inference["inference_id"], limit=1)
                if not rows or rows[0]["content_hash"] != stable_document_hash(inference):
                    raise original_error

    def _binding(self, stock_code, event, outcome):
        return {
            "target_id": stock_code, "article_revision_id": str(event.get("article_revision_id") or ""),
            "body_revision_id": str(event.get("body_revision_id") or ""),
            "provider": outcome["provider"], "model": outcome["model"],
            "prompt_version": ANALYSIS_PROMPT_VERSION, "schema_version": NEWS_ANALYSIS_SCHEMA_VERSION,
            "input_hash": str(event["body_hash"]),
        }

    def _fresh_revision(self, binding, value, outcome, *, inference_ref, requester=None):
        authority = requester or {"kind": "direct", "binding": dict(binding)}
        revision_id = uuid5(NAMESPACE_URL, stable_document_hash({
            "request": outcome["request_token"], **binding, "requester": authority,
        })).hex
        return {"analysis_revision_id": revision_id, **binding, "computed_at": outcome["computed_at"],
                "output": deepcopy(value), "usage": {
                    **outcome["usage"], "inference_ref": deepcopy(inference_ref),
                    "input_binding": deepcopy(outcome["produced_bindings_by_ref"][(inference_ref["inference_id"], inference_ref["input_index"])]),
                    "requester": authority,
                }}

    def _response(self, outcome, revisions, provenance=None):
        return {
            "provider": outcome["provider"], "model": outcome["model"],
            "results": deepcopy(outcome["results"]), "credential_revision": outcome["credential_revision"],
            "body_hashes": list(outcome["body_hashes"]), "usage": dict(outcome["usage"]),
            "cache_hit": outcome["cache_hit"], "analysis_revision_ids": [ref for ref in revisions if ref],
            "analysis_provenance": [decode_news_ai_provenance(value) for value in
                                    (provenance or [None] * len(outcome["results"]))],
        }

    async def _publish_direct(self, stock_code, prepared, outcome):
        documents, revisions, refs, provenance = [], [], [], []
        for index, event in enumerate(prepared):
            binding = self._binding(stock_code, event, outcome)
            ref, reference = None, None
            if index in outcome["fresh_indices"]:
                produced_ref = outcome["inference_refs"][index]
                reference = {"status": "UNKNOWN", "origin": "native_input_observed",
                             "target_id": stock_code, "provider": outcome["provider"], "model": outcome["model"],
                             "computed_at": outcome["computed_at"], "input_hash": str(event["body_hash"]),
                             "output_hash": stable_document_hash(outcome["results"][index]),
                             "inference_ref": deepcopy(produced_ref),
                             "input_binding": deepcopy(outcome["produced_bindings_by_ref"][(produced_ref["inference_id"], produced_ref["input_index"])])}
                documents.append({"owner": stock_code, "key": str(event["identity"]),
                                  "input_provenance": reference, "document": deepcopy(outcome["results"][index])})
                if binding["article_revision_id"] and binding["body_revision_id"]:
                    revision = self._fresh_revision(binding, outcome["results"][index], outcome,
                                                    inference_ref=outcome["inference_refs"][index])
                    revisions.append(revision)
                    ref = revision["analysis_revision_id"]
            elif hasattr(self._store, "find_news_ai_revision"):
                prior = outcome["cached_provenance"].get(index, {})
                pinned = bool(binding["article_revision_id"] and binding["body_revision_id"])
                reference = (dict(prior, status="UNKNOWN", reported_status="ACCEPTED")
                             if prior.get("status") == "ACCEPTED" else prior)
                if (prior.get("status") == "ACCEPTED" and prior.get("analysis_revision_id")
                        and prior.get("output_hash") == stable_document_hash(outcome["results"][index])
                        and all(prior.get(name) for name in binding)
                        and all(str(prior[name]) == str(binding[name]) for name in
                                ("target_id", "provider", "model", "prompt_version", "schema_version", "input_hash"))
                        and (not pinned or all(str(prior[name]) == str(binding[name]) for name in
                                               ("article_revision_id", "body_revision_id")))):
                    # This exact envelope was read with its result. Metadata is
                    # a source reference copy, never a new trading/read authority.
                    ref = str(prior["analysis_revision_id"])
                    reference = prior
                elif pinned:
                    # Keep the older flat-ID response, but do not turn a latest
                    # matching row into the missing original source relationship.
                    ref = await self._native_call(self._store.find_news_ai_revision, **binding)
            refs.append(ref)
            provenance.append(reference)
        if documents:
            accepted = await self._persist_results(documents, revisions)
            by_id = {str(value["analysis_revision_id"]): value for value in (accepted or ())}
            provenance = [by_id.get(str(ref), reference) for ref, reference in zip(refs, provenance, strict=True)]
        return self._response(outcome, refs, provenance)

    async def _publish_job(self, stock_code, prepared, outcome, job):
        if len(prepared) != 1:
            raise ValueError("AI 영속 작업은 pinned 기사 한 건을 소유합니다.")
        event = prepared[0]
        binding = self._binding(stock_code, event, outcome)
        fresh = 0 in outcome["fresh_indices"]
        result = {"stage": "AI", "binding": binding, "cached": not fresh,
                  "fallback_ref": str(job["output_ref_fallback"])}
        if fresh:
            revision = self._fresh_revision(binding, outcome["results"][0], outcome,
                                            inference_ref=outcome["inference_refs"][0], requester={
                "kind": "job", "job_key": str(job["job_key"]), "attempts": int(job["attempts"]),
                "binding": dict(binding),
            })
            result.update({"usage_ref": outcome["usage_ref"], "revisions": [revision],
                           "documents": [{"owner": stock_code, "key": str(event["identity"]),
                                          "document": deepcopy(outcome["results"][0])}]})
        receipt = await self._native_call(self._store.accept_news_job_result, job["job_key"], result,
                                          attempts=int(job["attempts"]))
        ref = receipt["output_ref"]
        linked = ref and (fresh or ref != str(job["output_ref_fallback"]))
        reference = receipt.get("analysis_provenance")
        if (not isinstance(reference, dict)
                or reference.get("output_hash") != stable_document_hash(outcome["results"][0])):
            reference = None
        response = self._response(outcome, [ref] if linked else [], [reference])
        response["job_receipt"] = {name: value for name, value in receipt.items() if name != "analysis_provenance"}
        return response

    async def _run(self, stock_code: str, stock_name: str, provider: str, model: str,
                   events: list[dict[str, Any]], article_count: int, credential: _AIRequestCredential,
                   entry: _AIInFlight | None = None) -> dict[str, Any]:
        cached_rows = await asyncio.to_thread(self._store.load_documents, "news_ai", stock_code, 10_000)
        cached = {str(row["key"]): row["document"] for row in cached_rows}
        cached_provenance: dict[int, dict[str, object]] = {}
        results: list[dict[str, Any] | None] = [None] * len(events)
        missing: list[tuple[int, dict[str, Any]]] = []
        for index, event in enumerate(events):
            value = cached.get(str(event["identity"]))
            if isinstance(value, dict) and value.get("body_hash") == event.get("body_hash") \
                    and value.get("provider") == provider and value.get("model") == model \
                    and (provider != "chatgpt_plan" or value.get("credential_revision") == credential.revision):
                value = dict(value)
                reference = decode_news_ai_provenance(value.pop("provenance_json", None))
                cached_provenance[index] = reference
                results[index] = value
            else:
                missing.append((index, event))
        usage = AIRequestUsage()
        inference_refs, produced_bindings_by_ref = {}, {}
        usage_document, request_token, computed_at = None, "", None
        unexecuted_jobs = {}
        def outcome_value():
            return {
                "provider": provider, "model": model, "results": results,
                "credential_revision": credential.revision,
                "body_hashes": [str(value.get("body_hash", "")) for value in events],
                "usage": usage.__dict__, "cache_hit": not missing,
                "fresh_indices": tuple(index for index, event in missing),
                "inference_refs": inference_refs,
                "produced_bindings_by_ref": produced_bindings_by_ref,
                "cached_provenance": cached_provenance,
                "usage_ref": usage_document, "request_token": request_token, "computed_at": computed_at,
                **({"unexecuted_jobs": unexecuted_jobs} if unexecuted_jobs else {}),
            }
        if missing:
            # 공급자 RPM과 일일 상한은 모든 PC를 합친 중앙 기준으로 적용한다.
            async with self._execution_lock:
                if provider == "chatgpt_plan" and (failure := self.chatgpt_failure):
                    from kiwoom_monitor.infrastructure.chatgpt_auth import ChatGPTPlanError
                    raise ChatGPTPlanError(failure)
                usage_owner = as_kst(self._now()).date().isoformat()
                if self._daily_limit > 0:
                    used = await asyncio.to_thread(
                        self._store.load_documents, "news_request_usage", usage_owner, 10_000,
                    )
                    if len(used) >= self._daily_limit:
                        raise ValueError(f"NAS AI 일일 상한 {self._daily_limit}회를 모두 사용했습니다.")
                if hasattr(self._store, "load_news_history"):
                    for _index, event in missing:
                        article_revision_id = str(event.get("article_revision_id") or "")
                        member = next((value for value in event["_source_members"]
                                       if value["used"] and value["identity"] == str(event["identity"])), None)
                        if not article_revision_id and member is not None:
                            article_rows = await asyncio.to_thread(
                                self._store.load_news_history, "article", target=stock_code,
                                identity=str(event["identity"]), limit=1,
                            )
                            if article_rows:
                                row, source = article_rows[0], member["article"]
                                reference = decode_news_input_source(member.get("source_ref")) or {}
                                document = row["document"]
                                if (all(str(source.get(name) or "") == str(document.get(name) or "")
                                        for name in ("title", "description", "link", "original_link"))
                                        and (not reference.get("article_revision_id") or reference["article_revision_id"] == row["article_revision_id"])
                                        and (not reference.get("article_content_hash") or reference["article_content_hash"] == row["content_hash"])):
                                    article_revision_id = str(row["article_revision_id"])
                                    event["article_revision_id"] = article_revision_id
                        if (event["_input_kind"] == "bundle" and article_revision_id
                                and not event.get("body_revision_id") and member is not None
                                and hasattr(self._store, "save_news_body_revision_receipt")):
                            receipt = await asyncio.to_thread(
                                self._store.save_news_body_revision_receipt, {
                                    "article_revision_id": article_revision_id,
                                    "extractor_version": ARTICLE_BODY_EXTRACTOR_VERSION,
                                    "fetched_at": member.get("observed_at") or self._now().timestamp(),
                                    "status": str(member["body_status"]),
                                    "body_text": str(member["raw_body"]), "error": "",
                                },
                            )
                            event["body_revision_id"] = str(receipt["body_revision_id"])
                            member["native_body"] = deepcopy(receipt)
                frozen_inputs = [freeze_news_ai_input(
                    str(event["identity"]), str(event["title"]), str(event["body"]), str(event["body_hash"]),
                    event["_source_members"], kind=str(event["_input_kind"]),
                    article_revision_id=str(event.get("article_revision_id") or ""),
                    body_revision_id=str(event.get("body_revision_id") or ""),
                ) for _, event in missing]
                ai_settings = NewsAISettings(provider, credential.api_key, model,
                    chatgpt_storage_path=self.chatgpt_auth.store.path if provider == "chatgpt_plan" else None)
                # All prior async input/limit work is done, and this request has
                # acquired the existing provider lock. This native check is the
                # admission cut immediately before scheduling paid work.
                eligible = entry is None
                for attached_job in list(entry.requests.values()) if entry is not None else ():
                    if attached_job is None:
                        eligible = True  # An explicit request owns its own paid authority.
                        continue
                    receipt = await self._native_call(self._store.accept_news_job_result, attached_job["job_key"], {
                        "stage": "AI", "check_unexecuted_reanalysis": True,
                    }, attempts=int(attached_job["attempts"]))
                    if receipt["state"] == "continue":
                        eligible = True
                    else:
                        unexecuted_jobs[(str(attached_job["job_key"]), int(attached_job["attempts"]))] = receipt
                if not eligible:
                    return {"unexecuted_jobs": unexecuted_jobs}
                request_token = str(uuid4())
                try:
                    analyses, usage = await asyncio.to_thread(
                        analyze_articles, ai_settings, stock_name,
                        tuple((str(value["title"]), str(value["body"])) for value in frozen_inputs),
                        **({"chatgpt_auth": self.chatgpt_auth} if provider == "chatgpt_plan" else {}),
                    )
                except Exception as error:
                    from kiwoom_monitor.infrastructure.chatgpt_auth import ChatGPTPlanError
                    if provider == "chatgpt_plan":
                        # Also pause invalid news JSON. Already accepted tasks check
                        # this fence again after acquiring the shared execution lock.
                        self._remember_chatgpt_failure(
                            error.code if isinstance(error, ChatGPTPlanError) else "response_invalid")
                        await asyncio.to_thread(self.chatgpt_auth.pause_analysis, self._plan_failure)
                        if (isinstance(error, ChatGPTPlanError) and error.inference_attempted) or isinstance(error, ValueError):
                            await self._save_usage({
                                "owner": usage_owner, "key": request_token, "document": {
                                    "requested_at": self._now().astimezone().isoformat(), "provider": provider,
                                    "model": model, "credential_revision": credential.revision,
                                    "request_mode": "batch" if len(missing) > 1 else "single",
                                    "event_count": len(missing), "article_count": article_count,
                                    "status": "failed", "error_code": self._plan_failure,
                                    "usage_measured": False,
                                },
                            })
                        raise ChatGPTPlanError(self._plan_failure) from None
                    if isinstance(error, NewsAIProviderError):
                        if self._credential_revisions[provider] == credential.revision:
                            self._credential_validation[provider] = (
                                "INVALID_CREDENTIAL" if error.status_code == 401 else
                                "ACCESS_DENIED" if error.status_code == 403 else
                                "RETRYABLE" if error.status_code == 429 or error.status_code >= 500 else "REQUEST_FAILED"
                            )
                    elif isinstance(error, OSError) and self._credential_revisions[provider] == credential.revision:
                        self._credential_validation[provider] = "RETRYABLE"
                    raise
                if self._credential_revisions[provider] == credential.revision:
                    self._credential_validation[provider] = "VERIFIED"
                computed = self._now()
                computed_at, now = computed.timestamp(), computed.isoformat()
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
                    "owner": usage_owner, "key": request_token,
                    "document": {
                        "requested_at": self._now().astimezone().isoformat(), "provider": provider,
                        "model": model, "request_mode": "batch" if len(missing) > 1 else "single",
                        "credential_revision": credential.revision,
                        "event_count": len(missing), "article_count": article_count,
                        "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
                        "total_tokens": usage.total_tokens,
                    },
                }]
                # Freeze exactly what the first provider call saw. Requester pins
                # remain on each publication and may differ under the existing key.
                inference = {
                    "inference_id": request_token, "usage_owner": usage_owner, "usage_key": request_token,
                    "target_id": stock_code, "stock_name": stock_name,
                    "provider": provider, "model": model, "credential_revision": credential.revision,
                    "prompt_version": ANALYSIS_PROMPT_VERSION, "schema_version": NEWS_ANALYSIS_SCHEMA_VERSION,
                    "computed_at": computed_at,
                    "input_envelope_version": 1, "result_schema_version": "news-ai-document-v1",
                    "inputs": deepcopy(frozen_inputs),
                    "outputs": [deepcopy(results[index]) for index, _ in missing],
                }
                inference_hash = stable_document_hash(inference)
                for input_index, (index, _event) in enumerate(missing):
                    produced_bindings_by_ref[(request_token, input_index)] = deepcopy(frozen_inputs[input_index]["input_binding"])
                    inference_refs[index] = {
                        "inference_id": request_token, "content_hash": inference_hash, "input_index": input_index,
                    }
                usage_document = {**usage_documents[0], "inference": inference}
                usage_document["document"]["inference_ref"] = {
                    "inference_id": request_token, "content_hash": inference_hash,
                }
                await self._save_usage(usage_document)
        return outcome_value()



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


def _prepare_events(events: list[dict[str, Any]], stock_name: str = "", *, now_provider=None) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for event in events:
        body = str(event.get("body", "")).strip()
        articles = event.get("articles", ())
        sources: list[dict[str, object]] = []
        if body:
            native = event.get("_native_body")
            if isinstance(native, dict):
                # Reuse the job path's narrow receipt before freezing its binding.
                # Raw BODY text is retained separately in the source snapshot.
                native = {name: deepcopy(native[name]) for name in
                          ("body_revision_id", "article_revision_id", "content_hash", "extractor_version",
                           "fetched_at", "available_at", "status") if name in native}
            status = str(event.get("body_status") or (native.get("status") if isinstance(native, dict) else "") or "UNKNOWN")
            observed = now_provider().timestamp() if now_provider is not None else None
            receiver_clock = "input_receiver_now_provider" if now_provider is not None else "UNKNOWN"
            selected = None
            if isinstance(articles, list):
                for index, article in enumerate(articles):
                    value = deepcopy(article) if isinstance(article, dict) else {"reported_type": type(article).__name__}
                    used = str(value.get("identity") or "") == str(event["identity"])
                    if used and selected is None:
                        selected = index
                        raw = str(event.get("_raw_body", event.get("body", "")))
                        sources.append(news_ai_source_member(index, value, raw, status,
                            input_text=body, span=(0, len(body)),
                            observed_at=observed, clock_source=receiver_clock,
                            native_body=native if isinstance(native, dict) else None))
                    else:
                        sources.append(news_ai_source_member(index, value, "", "not_used"))
            if selected is None:
                article = {name: deepcopy(event[name]) for name in
                           ("identity", "title", "description", "link", "original_link", "published_at", "input_source")
                           if name in event}
                sources.append(news_ai_source_member(len(sources), article,
                    str(event.get("_raw_body", event.get("body", ""))), status,
                    input_text=body, span=(0, len(body)),
                    observed_at=observed, clock_source=receiver_clock,
                    native_body=native if isinstance(native, dict) else None))
            kind, body_status = "article", status
        else:
            sections: list[str] = []
            saw_fulltext = False
            length = 0
            if isinstance(articles, list):
                for index, article in enumerate(articles):
                    if not isinstance(article, dict):
                        sources.append(news_ai_source_member(index, {"reported_type": type(article).__name__}, "", "unavailable"))
                        continue
                    title = str(article.get("title", ""))
                    article_body = ""
                    source_status = "unavailable"
                    for url in dict.fromkeys((str(article.get("link", "")), str(article.get("original_link", "")))):
                        if not url:
                            continue
                        try:
                            article_body = fetch_article_text(url)
                            if article_body:
                                saw_fulltext, source_status = True, "fulltext"
                                break
                        except Exception:
                            continue
                    if not article_body:
                        raw_summary = str(article.get("description", ""))
                        article_body = raw_summary.strip()
                        if article_body:
                            source_status = "summary_only"
                    observed = now_provider().timestamp() if now_provider is not None else None
                    raw_source = article_body if source_status == "fulltext" else raw_summary
                    if article_body:
                        section = f"[관련 기사 {index + 1}/{len(articles)}: {title}]\n{article_body}"
                        start = length + (2 if sections else 0)
                        length = start + len(section)
                        sections.append(section)
                        sources.append(news_ai_source_member(index, article, raw_source, source_status,
                            input_text=section, span=(start, length), observed_at=observed,
                            clock_source="injected_business_clock" if now_provider is not None else "UNKNOWN"))
                    else:
                        sources.append(news_ai_source_member(index, article, raw_source, source_status,
                            observed_at=observed,
                            clock_source="injected_business_clock" if now_provider is not None else "UNKNOWN"))
            body = "\n\n".join(sections)
            kind = "bundle"
            body_status = "fulltext" if saw_fulltext else "summary_only"
        if not body:
            raise ValueError("기사 본문과 검색 요약을 가져오지 못했습니다.")
        value = dict(event)
        value.update(body=body, body_hash=analysis_body_hash(stock_name, body), body_status=body_status,
                     _source_members=sources, _input_kind=kind)
        prepared.append(value)
    return prepared
