"""느린 뉴스 본문·AI 단계를 처리하는 단일 영속 작업 실행기."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from time import time
from typing import Any, Callable

from kiwoom_monitor.application.news_rules import (
    classify_supply_contract,
    grouped_candidate_identities,
    rule_input_hash,
)
from kiwoom_monitor.domain.news_observation import ARTICLE_BODY_EXTRACTOR_VERSION
from kiwoom_monitor.infrastructure.article_text import clean_article_text, fetch_article_text


LOGGER = logging.getLogger(__name__)


class NewsJobRunner:
    def __init__(self, store: Any, *, ai_service: Any = None,
                 fetcher: Callable[..., str] = fetch_article_text,
                 poll_seconds: float = 1.0, body_timeout: float = 15.0,
                 ai_timeout: float = 90.0, busy_pause_seconds: float = 1.0) -> None:
        self._store, self._ai_service, self._fetcher = store, ai_service, fetcher
        self._poll_seconds = max(0.05, float(poll_seconds))
        self._busy_pause_seconds = max(0.01, float(busy_pause_seconds))
        self._body_timeout, self._ai_timeout = body_timeout, ai_timeout
        self._priority_stock_code = ""
        self._task: asyncio.Task[None] | None = None
        self._closing = asyncio.Event()

    def set_ai_service(self, service: Any) -> None:
        self._ai_service = service

    def set_priority_stock(self, stock_code: str) -> None:
        self._priority_stock_code = str(stock_code or "").strip()

    async def start(self) -> None:
        if self._task is None:
            self._closing.clear()
            self._task = asyncio.create_task(self._loop(), name="central-news-jobs")

    async def close(self) -> None:
        self._closing.set()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def run_once(self) -> int:
        jobs = await asyncio.to_thread(
            self._store.claim_news_jobs, limit=1,
            priority_stock_code=self._priority_stock_code,
        )
        for job in jobs:
            try:
                if job["stage"] == "BODY":
                    output_ref = await self._run_body(job)
                elif job["stage"] == "RULE":
                    output_ref = await self._run_rule(job)
                elif job["stage"] == "AI":
                    output_ref = await self._run_ai(job)
                else:
                    raise ValueError(f"지원하지 않는 뉴스 작업 단계입니다: {job['stage']}")
                await asyncio.to_thread(self._store.finish_news_job, job["job_key"], output_ref)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                output_ref = ""
                if job["stage"] == "BODY" and int(job["attempts"]) >= 3:
                    output_ref = await asyncio.to_thread(self._store.save_news_body_revision, {
                        "article_revision_id": job["article_revision_id"],
                        "extractor_version": ARTICLE_BODY_EXTRACTOR_VERSION,
                        "fetched_at": time(), "status": "failed", "body_text": "",
                        "error": str(error),
                    })
                delay = min(60.0, float(2 ** min(int(job["attempts"]), 5)))
                await asyncio.to_thread(
                    self._store.retry_news_job, job["job_key"], str(error), time() + delay,
                    output_ref,
                )
        return len(jobs)

    async def _run_body(self, job: dict[str, Any]) -> str:
        cached = await asyncio.to_thread(
            self._store.load_latest_news_body, str(job["article_revision_id"]),
        )
        if cached and cached.get("status") in {"fulltext", "summary_only"}:
            return str(cached["body_revision_id"])
        payload = job["payload"]
        body = ""
        for url in dict.fromkeys((str(payload.get("link", "")), str(payload.get("original_link", "")))):
            if not url:
                continue
            try:
                body = await asyncio.wait_for(
                    asyncio.to_thread(self._fetcher, url, timeout_seconds=self._body_timeout),
                    timeout=self._body_timeout + 1.0,
                )
                break
            except Exception:
                continue
        status = "fulltext"
        if not body:
            body, status = str(payload.get("description") or "").strip(), "summary_only"
        if not body:
            raise ValueError("기사 본문과 검색 요약을 가져오지 못했습니다.")
        return await asyncio.to_thread(self._store.save_news_body_revision, {
            "article_revision_id": job["article_revision_id"],
            "extractor_version": ARTICLE_BODY_EXTRACTOR_VERSION,
            "fetched_at": time(), "status": status, "body_text": body, "error": "",
        })

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
        event["article_revision_id"] = job["article_revision_id"]
        event["body_revision_id"] = body["body_revision_id"]
        result = await asyncio.wait_for(self._ai_service.analyze(
            str(job["stock_code"]), str(payload.get("stock_name") or job["stock_code"]),
            str(payload.get("provider") or "none"), str(payload.get("model") or ""),
            [event], int(payload.get("article_count") or 1),
        ), timeout=self._ai_timeout)
        revisions = result.get("analysis_revision_ids", [])
        return str(revisions[0]) if revisions else str(event.get("body_hash") or job["input_hash"])

    async def _run_rule(self, job: dict[str, Any]) -> str:
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
        result = classify_supply_contract(document, cleaned_body)
        if result is None:
            return f"ignored:{body_revision_id}"
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
        return await asyncio.to_thread(self._store.save_news_event_revision, {
            "stock_code": target_code,
            "article_revision_id": article["article_revision_id"],
            "body_revision_id": body["body_revision_id"],
            "rule_version": result.rule_version,
            "input_hash": rule_input_hash(article["article_revision_id"], body["body_revision_id"], result),
            "candidate_identities": candidates,
            "result": result.as_document(),
        })

    async def _loop(self) -> None:
        while not self._closing.is_set():
            try:
                processed = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("뉴스 후속 작업 조회에 실패했습니다.")
                processed = 0
            try:
                await asyncio.wait_for(
                    self._closing.wait(),
                    timeout=self._busy_pause_seconds if processed else self._poll_seconds,
                )
            except TimeoutError:
                pass
