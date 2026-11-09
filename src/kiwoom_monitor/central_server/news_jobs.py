"""느린 뉴스 본문·AI 단계를 처리하는 단일 영속 작업 실행기."""

from __future__ import annotations

from copy import deepcopy

import asyncio
import logging
from dataclasses import asdict
from datetime import datetime
from time import monotonic
from typing import Any, Callable

from kiwoom_monitor.application.news_material import official_filing_ref
from kiwoom_monitor.domain.news_observation import ARTICLE_BODY_EXTRACTOR_VERSION, AIJobResultError, NewsBodyDeliveryError
from kiwoom_monitor.domain.time_basis import as_kst, kst_now
from kiwoom_monitor.infrastructure.article_text import (
    clean_article_text, fetch_article_text_with_metadata, parse_article_text_with_metadata,
)
from kiwoom_monitor.infrastructure.exchange_effective_dates import document_rows
from kiwoom_monitor.infrastructure.official_news_filing import fetch_official_filing


LOGGER = logging.getLogger(__name__)


def official_filing_body_result(filing: dict[str, str], payload: dict[str, Any], fetched_at: float) -> dict[str, Any]:
    body = "\n".join(" | ".join(row) for row in document_rows(filing["filing_html"]))[:24_000]
    status = "fulltext"
    if not body.strip():
        try:
            body, _ = parse_article_text_with_metadata(filing["filing_html"])
        except ValueError:
            body = str(payload.get("description") or payload.get("title") or filing["filing_url"]).strip()
            status = "summary_only"
    return {
        "stage": "BODY", "filing": filing,
        "body": {"extractor_version": ARTICLE_BODY_EXTRACTOR_VERSION, "fetched_at": fetched_at,
                 "status": status, "body_text": body, "error": ""},
    }


class NewsJobRunner:
    def __init__(self, store: Any, *, ai_service: Any = None,
                 fetcher: Callable[..., str | tuple[str, str]] = fetch_article_text_with_metadata,
                 filing_fetcher: Callable[..., dict[str, str]] = fetch_official_filing,
                 poll_seconds: float = 1.0, body_timeout: float = 15.0,
                 ai_timeout: float = 90.0, busy_pause_seconds: float = 1.0,
                 parallelism: int = 1, max_idle_poll_seconds: float = 5.0,
                 now_provider: Callable[[], datetime] | None = None,
                 body_input: Callable | None = None, body_accept: Callable | None = None,
                 diagnostic_scope: Callable | None = None, paused: Callable[[], bool] | None = None) -> None:
        self._store, self._ai_service, self._fetcher = store, ai_service, fetcher
        self._filing_fetcher = filing_fetcher
        self._body_input, self._body_accept = body_input, body_accept
        self._run_loop = (diagnostic_scope(self._loop.__func__).__get__(self,type(self))
                          if diagnostic_scope is not None else self._loop)
        self._paused = paused or (lambda: False)
        # Standalone/offline runners use the common KST fallback. The server
        # supplies its existing corrected clock through CentralNewsService.
        self._now = (lambda: as_kst(now_provider())) if now_provider else kst_now
        self._poll_seconds = max(0.05, float(poll_seconds))
        self._max_idle_poll_seconds = max(self._poll_seconds, float(max_idle_poll_seconds))
        self._busy_pause_seconds = max(0.01, float(busy_pause_seconds))
        self._body_timeout, self._ai_timeout = body_timeout, ai_timeout
        self._priority_stock_code = ""
        self._task: asyncio.Task[None] | None = None
        self._tasks: tuple[asyncio.Task[None], ...] = ()
        self._parallelism = max(1, min(3, int(parallelism)))
        self._closing = asyncio.Event()
        self._wake_events: tuple[asyncio.Event, ...] = ()
        self._loop_owner: asyncio.AbstractEventLoop | None = None
        self._stage_metrics: dict[str, dict[str, float]] = {}
        self._metrics_started_at = monotonic()

    def set_ai_service(self, service: Any) -> None:
        self._ai_service = service

    def set_priority_stock(self, stock_code: str) -> None:
        self._priority_stock_code = str(stock_code or "").strip()

    async def start(self) -> None:
        if self._task is None:
            self._closing.clear()
            self._loop_owner = asyncio.get_running_loop()
            stages = {
                1: (None,), 2: ("BODY", "RULE"), 3: ("BODY", "BODY", "RULE"),
            }[self._parallelism]
            self._wake_events = tuple(asyncio.Event() for _ in stages)
            register = getattr(self._store, "set_news_job_wakeup", None)
            if register is not None:
                register(self.wake)
            self._tasks = tuple(
                asyncio.create_task(self._run_loop(stage, wake), name=f"central-news-jobs-{index + 1}")
                for index, (stage, wake) in enumerate(zip(stages, self._wake_events, strict=True))
            )
            self._task = self._tasks[0]

    async def close(self) -> None:
        self._closing.set()
        register = getattr(self._store, "set_news_job_wakeup", None)
        if register is not None:
            register(None)
        for wake in self._wake_events:
            wake.set()
        tasks, self._tasks = self._tasks, ()
        self._task = None
        for task in tasks:
            await task
        self._wake_events = ()
        self._loop_owner = None

    async def wait(self) -> None:
        """Join this existing runner lifetime without exposing private tasks."""
        if self._tasks:
            await asyncio.gather(*self._tasks)

    def wake(self) -> None:
        """A committed enqueue may arrive from an asyncio.to_thread producer."""
        loop = self._loop_owner
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(self._wake_all)
            except RuntimeError:  # The event loop can close during server shutdown.
                pass

    def _wake_all(self) -> None:
        for wake in self._wake_events:
            wake.set()

    async def run_once(self, *, preferred_stage: str = "") -> int:
        jobs = await asyncio.to_thread(
            self._store.claim_news_jobs, limit=1,
            now=self._now().timestamp(),
            priority_stock_code=self._priority_stock_code,
            **({"preferred_stage": preferred_stage} if preferred_stage else {}),
        )
        for job in jobs:
            started_at = monotonic()
            queued_wait_ms = max(0.0, (
                self._now().timestamp() - float(job.get("queued_at") or self._now().timestamp())
            ) * 1000)
            failed = False
            try:
                if job["stage"] == "BODY":
                    output_ref = await self._run_body(job)
                elif job["stage"] == "RULE":
                    output_ref = await self._run_rule(job)
                elif job["stage"] == "AI":
                    output_ref = await self._run_ai(job)
                else:
                    raise ValueError(f"지원하지 않는 뉴스 작업 단계입니다: {job['stage']}")
            except asyncio.CancelledError:
                raise
            except NewsBodyDeliveryError:
                failed = True
                raise
            except AIJobResultError:
                # The accepted request already owned native error settlement.
                # Preserve its first receipt or unknown ACK; never retry outside
                # its accepted/drain lifetime.
                failed = True
            except Exception as error:
                failed = True
                if job["stage"] == "AI":
                    await self._accept_result(job, {"stage": "AI", "error": str(error)})
                elif job["stage"] in {"BODY", "RULE"}:
                    delay = min(60.0, float(2 ** min(int(job["attempts"]), 5)))
                    next_retry_at = self._now().timestamp() + delay
                    await self._accept_result(job, {
                        "stage": job["stage"], "error": str(error), "next_retry_at": next_retry_at,
                    })
                else:
                    delay = min(60.0, float(2 ** min(int(job["attempts"]), 5)))
                    await asyncio.to_thread(
                        self._store.retry_news_job, job["job_key"], str(error), self._now().timestamp() + delay,
                        attempts=int(job["attempts"]),
                    )
            finally:
                self._record_stage(job, started_at, failed, queued_wait_ms)
        return len(jobs)

    def _record_stage(self, job: dict[str, Any], started_at: float, failed: bool, wait_ms: float) -> None:
        stage = str(job.get("stage") or "UNKNOWN")
        metrics = self._stage_metrics.setdefault(stage, {
            "count": 0, "failed": 0, "elapsed_ms": 0, "max_ms": 0,
            "wait_ms": 0, "max_wait_ms": 0,
        })
        elapsed_ms = max(0.0, (monotonic() - started_at) * 1000)
        metrics["count"] += 1
        metrics["failed"] += int(failed)
        metrics["elapsed_ms"] += elapsed_ms
        metrics["max_ms"] = max(metrics["max_ms"], elapsed_ms)
        metrics["wait_ms"] += wait_ms
        metrics["max_wait_ms"] = max(metrics["max_wait_ms"], wait_ms)
        if monotonic() - self._metrics_started_at >= 60:
            for name, value in self._stage_metrics.items():
                count = int(value["count"])
                LOGGER.info(
                    "뉴스 작업 단계별 처리 stage=%s count=%d failed=%d elapsed_avg_ms=%.0f "
                    "elapsed_max_ms=%.0f wait_avg_ms=%.0f wait_max_ms=%.0f",
                    name, count, int(value["failed"]), value["elapsed_ms"] / count,
                    value["max_ms"], value["wait_ms"] / count, value["max_wait_ms"],
                )
            self._stage_metrics.clear()
            self._metrics_started_at = monotonic()

    async def _accept_result(self, job: dict[str, Any], result: dict[str, Any]) -> str:
        if job["stage"] == "BODY" and self._body_accept is not None:
            receipt = await asyncio.to_thread(self._body_accept, job, result)
            return str(receipt["output_ref"])
        receipt = await asyncio.to_thread(
            self._store.accept_news_job_result, str(job["job_key"]), result,
            attempts=int(job["attempts"]),
        )
        return receipt["output_ref"]

    async def _run_body(self, job: dict[str, Any]) -> str:
        cached = (await asyncio.to_thread(self._body_input, job) if self._body_input is not None
                  else await asyncio.to_thread(self._store.load_latest_news_body, str(job["article_revision_id"])))
        if cached and cached.get("status") in {"fulltext", "summary_only"}:
            return await self._accept_result(job, {
                "stage": "BODY", "output_ref": str(cached["body_revision_id"]),
            })
        payload = job["payload"]
        body = ""
        original_published_at = ""
        fetched_url = ""
        urls = tuple(dict.fromkeys((str(payload.get("link") or ""), str(payload.get("original_link") or ""))))
        filing_url = next((url for url in reversed(urls) if official_filing_ref(url)), "")
        if filing_url:
            filing = await asyncio.wait_for(
                asyncio.to_thread(self._filing_fetcher, filing_url, timeout_seconds=self._body_timeout / 4),
                timeout=self._body_timeout + 1.0,
            )
            return await self._accept_result(job, official_filing_body_result(filing, payload, self._now().timestamp()))
        for url in urls:
            if not url:
                continue
            try:
                fetched = await asyncio.wait_for(
                    asyncio.to_thread(self._fetcher, url, timeout_seconds=self._body_timeout),
                    timeout=self._body_timeout + 1.0,
                )
                body, original_published_at = fetched if isinstance(fetched, tuple) else (fetched, "")
                fetched_url = url
                break
            except Exception:
                continue
        status = "fulltext"
        if not body:
            body, status = str(payload.get("description") or "").strip(), "summary_only"
        if not body:
            raise ValueError("기사 본문과 검색 요약을 가져오지 못했습니다.")
        result = {
            "stage": "BODY",
            "body": {
                "extractor_version": ARTICLE_BODY_EXTRACTOR_VERSION,
                "fetched_at": self._now().timestamp(), "status": status, "body_text": body, "error": "",
            },
        }
        if original_published_at:
            result["original_publication"] = {
                    "published_at": original_published_at, "source_url": fetched_url,
                    "source": "article_html", "precision": "second",
            }
        return await self._accept_result(job, result)

    async def _run_ai(self, job: dict[str, Any]) -> str:
        if self._ai_service is None:
            raise RuntimeError("AI 작업 실행기가 비활성화되어 있습니다.")
        payload = dict(job["payload"])
        body_revision_id = str(payload.get("body_revision_id") or "")
        body = await asyncio.to_thread(
            self._store.load_news_body_revision, body_revision_id,
        ) if body_revision_id else None
        if not body or body.get("status") == "failed":
            raise RuntimeError("본문 작업이 아직 완료되지 않았습니다.")
        event = dict(payload["event"])
        cleaned_body = clean_article_text(str(body["body_text"]))
        event["body"] = cleaned_body or str(event.get("description") or "")
        event["body_status"] = str(body["status"])
        event["_raw_body"] = str(body["body_text"])
        event["_native_body"] = {name: deepcopy(body[name]) for name in
                                 ("body_revision_id", "article_revision_id", "content_hash", "extractor_version",
                                  "fetched_at", "available_at", "status") if name in body}
        event["article_revision_id"] = job["article_revision_id"]
        event["body_revision_id"] = body["body_revision_id"]
        arguments = (
            str(job["stock_code"]), str(payload.get("stock_name") or job["stock_code"]),
            str(payload.get("provider") or "none"), str(payload.get("model") or ""),
            [event], int(payload.get("article_count") or 1),
        )
        result = await self._ai_service.analyze_job(
            *arguments, job_key=str(job["job_key"]), attempts=int(job["attempts"]),
            output_ref_fallback=str(event.get("body_hash") or job["input_hash"]),
            timeout_seconds=self._ai_timeout,
        )
        return str(result["job_receipt"]["output_ref"])

    async def _run_rule(self, job: dict[str, Any]) -> str:
        from kiwoom_monitor.application.news_rules import classify_supply_contract, grouped_candidate_identities, rule_input_hash
        from kiwoom_monitor.application.news_analysis import assess_stock_news, extractive_news_summary
        body_revision_id = str(job["payload"].get("body_revision_id") or "")
        article, body = await asyncio.gather(
            asyncio.to_thread(self._store.load_news_article_revision, str(job["article_revision_id"])),
            asyncio.to_thread(self._store.load_news_body_revision, body_revision_id),
        )
        if article is None or body is None:
            raise RuntimeError("규칙 입력 기사 또는 본문 리비전이 없습니다.")
        document = dict(article["document"])
        target_code = str(job["payload"].get("stock_code") or job.get("target_id") or article["stock_code"])
        target_name = str(job["payload"].get("stock_name") or document.get("stock_name") or target_code)
        document.update({
            "identity": article["identity"], "stock_code": target_code, "stock_name": target_name,
        })
        cleaned_body = clean_article_text(str(body.get("body_text") or ""))
        if not cleaned_body:
            cleaned_body = str(document.get("description") or "")
        published_rows = await asyncio.to_thread(
            self._store.load_documents, "news_original_publication", str(article["article_revision_id"]), 1,
        )
        original_published_at = (
            str(published_rows[0]["document"].get("published_at") or "")
            if published_rows and isinstance(published_rows[0].get("document"), dict) else ""
        )
        assessment = assess_stock_news(
            target_name, str(document.get("title") or ""),
            str(document.get("description") or ""),
            article_body=cleaned_body if body.get("status") == "fulltext" else "",
        )
        assessment_document = {
                "article_revision_id": str(article["article_revision_id"]),
                "body_revision_id": body_revision_id,
                "rule_version": "stock-news-assessment-v1",
                "published_at": original_published_at or str(document.get("published_at") or ""),
                "listing_published_at": str(document.get("published_at") or ""),
                "original_published_at": original_published_at,
                "published_at_source": "article_html" if original_published_at else "listing",
                "assessment": asdict(assessment),
                "core_sentences": list(extractive_news_summary(
                    "", str(document.get("title") or ""), cleaned_body,
                )) if body.get("status") == "fulltext" else [],
        }
        result = None if target_code == "GLOBAL" else classify_supply_contract(document, cleaned_body)
        if result is None:
            return await self._accept_result(job, {"stage": "RULE", "assessment": assessment_document})
        recent_rows = await asyncio.to_thread(
            self._store.load_news_history, "article", target=str(article["stock_code"]), limit=100,
        )
        recent = []
        for row in recent_rows:
            if row["article_revision_id"] == article["article_revision_id"]:
                continue
            item = dict(row["document"])
            item["identity"] = row["identity"]
            recent.append(item)
        candidates = grouped_candidate_identities(document, recent)
        return await self._accept_result(job, {"stage": "RULE", "assessment": assessment_document, "event": {
            "stock_code": target_code,
            "article_revision_id": article["article_revision_id"],
            "body_revision_id": body["body_revision_id"],
            "rule_version": result.rule_version,
            "input_hash": rule_input_hash(article["article_revision_id"], body["body_revision_id"], result),
            "candidate_identities": candidates,
            "result": result.as_document(),
        }})

    async def _loop(self, preferred_stage: str | None = None,
                    wake: asyncio.Event | None = None) -> None:
        empty_polls = 0
        while not self._closing.is_set():
            if wake is not None:
                wake.clear()  # Signals arriving during the claim remain set for the next wait.
            claim_failed = False
            try:
                paused = self._paused()
                processed = 0 if paused else await (
                    self.run_once(preferred_stage=preferred_stage)
                    if preferred_stage else self.run_once()
                )
            except asyncio.CancelledError:
                raise
            except NewsBodyDeliveryError:
                self._closing.set()
                raise
            except Exception:
                LOGGER.exception("뉴스 후속 작업 조회에 실패했습니다.")
                processed = 0
                paused = False
                claim_failed = True
            if processed or paused or claim_failed:
                empty_polls = 0
            else:
                empty_polls += 1
            if processed:
                delay = self._busy_pause_seconds
            elif paused or claim_failed:
                delay = self._poll_seconds
            else:
                delay = min(
                    self._max_idle_poll_seconds,
                    self._poll_seconds * 2 ** min(empty_polls - 1, 16),
                )
            try:
                await asyncio.wait_for(
                    wake.wait() if wake is not None else self._closing.wait(),
                    timeout=delay,
                )
                if wake is not None and wake.is_set():
                    empty_polls = 0
            except TimeoutError:
                pass
