"""One NAS-owned daily web research result shared by UI and context readers."""
import asyncio
import logging
import re
from datetime import datetime, timedelta
from time import perf_counter
from uuid import uuid4
from zoneinfo import ZoneInfo

from kiwoom_monitor.application.morning_market_brief import (
    DEFAULT_SETTINGS, SETTINGS_COLLECTION, RUNS_COLLECTION, content_hash,
    render_prompt, validate_output, context_from_run, output_schema, SCHEMA,
)
from kiwoom_monitor.infrastructure.chatgpt_plan import validate_reasoning
from .diagnostic_workloads import is_paused

KST = ZoneInfo("Asia/Seoul")
LOGGER = logging.getLogger(__name__)


async def _join(task):
    """Keep the durable claim and its inference owner alive through cancellation."""
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        task.result()
        raise


class MorningMarketBrief:
    """Owns schedule, immutable run inputs, claim-before-POST and shutdown drain."""
    def __init__(self, store, ai_service, *, clock=None):
        self.store, self.ai = store, ai_service
        self.clock = clock or (lambda: datetime.now(KST))
        rows = store.load_documents(SETTINGS_COLLECTION, "global", 1)
        self.settings = {**DEFAULT_SETTINGS, **(rows[0]["document"] if rows else {})}
        self._lock = asyncio.Lock()
        self._loop_task = None
        self._run_task = None
        self._closing = False
        self._diagnostics = {"attempts": 0, "completed": 0, "failed": 0,
                             "elapsed_seconds": 0.0, "last_status": "idle"}

    def status(self):
        return {**self.settings, "running": self._run_task is not None and not self._run_task.done(),
                "diagnostics": dict(self._diagnostics), "source_mode": "ai_web_search",
                "timezone": "Asia/Seoul", "auto_trade_eligible": False}

    async def update(self, changes, revision):
        async with self._lock:
            if revision != self.settings["revision"]:
                raise RuntimeError("MORNING_BRIEF_REVISION_CONFLICT")
            new = {**self.settings, **changes}
            if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", new["time"]):
                raise ValueError("실행 시각은 HH:MM 형식이어야 합니다.")
            if new["provider"] not in {"chatgpt_plan", "openai"}:
                raise ValueError("직접 웹 검색이 지원되는 공급자를 선택하세요.")
            validate_reasoning(new["model"], new["reasoning_effort"])
            if (not isinstance(new["prompt"], str) or not 50 <= len(new["prompt"].strip()) <= 30_000
                    or not isinstance(new["watchlist"], str) or len(new["watchlist"]) > 5000
                    or type(new["enabled"]) is not bool
                    or type(new["ttl_minutes"]) is not int or not 15 <= new["ttl_minutes"] <= 720):
                raise ValueError("프롬프트 또는 실행 설정을 확인하세요.")
            new["revision"] += 1
            async def save_and_publish():
                await asyncio.to_thread(self.store.upsert_documents,
                    SETTINGS_COLLECTION, [{"owner": "global", "key": "current", "document": new}])
                self.settings = new
            task = asyncio.create_task(save_and_publish())
            await _join(task)
            return self.status()

    async def start(self):
        if self._loop_task is None:
            self._closing = False
            self._loop_task = asyncio.create_task(self._loop(), name="morning-market-brief-schedule")

    async def close(self):
        self._closing = True
        if self._loop_task is not None:
            self._loop_task.cancel()
            await asyncio.gather(self._loop_task, return_exceptions=True)
            self._loop_task = None
        if self._run_task is not None:
            await _join(self._run_task)

    async def latest(self):
        rows = await asyncio.to_thread(self.store.load_documents, RUNS_COLLECTION, "", 20)
        runs = [r["document"] for r in rows]
        latest = max(runs, key=lambda r: r["requested_at"], default=None)
        # Reuse retained provider output under the current research policy without
        # rewriting the original failed run, repeating inference, or editing history.
        if latest and latest.get("status") == "failed" and latest.get("raw_response") and not latest.get("result"):
            try:
                result = validate_output(latest["raw_response"],
                    {"sources": latest.get("sources", []), "cutoff_at": latest["cutoff_at"],
                     "search_calls": latest.get("search_calls", 0), "web_actions": latest.get("web_actions", [])})
            except ValueError:
                pass  # Malformed data remains a processing failure, with its original receipt.
            else:
                latest = {**latest, "result": result, "result_recovered_from_raw_response": True}
        return {"run": latest, "context": context_from_run(latest, self.clock()), "history": runs}

    async def request(self, *, manual=False):
        async with self._lock:
            if self._closing or is_paused("morning_market_brief"):
                raise ValueError("시황 실행이 중지 상태입니다.")
            if self._run_task is not None and not self._run_task.done():
                return {"status": "running"}
            now = self.clock().astimezone(KST)
            settings = dict(self.settings)
            hour, minute = map(int, settings["time"].split(":"))
            cutoff = now if manual else now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if not manual:
                if not settings["enabled"] or not cutoff <= now < cutoff + timedelta(minutes=settings["catchup_minutes"]):
                    return {"status": "not_due"}
            key = uuid4().hex if manual else "scheduled"
            owner = cutoff.date().isoformat()
            if not manual:
                rows = await asyncio.to_thread(self.store.load_documents, RUNS_COLLECTION, owner, 100)
                if any(r["key"] == key for r in rows):
                    return {"status": "already_attempted"}
            run = {"run_id": f"{owner}:{key}", "status": "running", "manual": manual,
                   "requested_at": now.timestamp(), "cutoff_at": cutoff.isoformat(),
                   "settings_revision": settings["revision"], "provider": settings["provider"],
                   "model": settings["model"], "reasoning_effort": settings["reasoning_effort"],
                   "prompt_hash": content_hash(settings["prompt"]), "prompt": settings["prompt"],
                   "available_at": None, "expires_at": (cutoff + timedelta(minutes=settings["ttl_minutes"])).timestamp(),
                   "auto_trade_eligible": False}
            # Durable claim precedes any external inference. A restarted running
            # claim remains uncertain and cannot silently submit a second POST.
            async def claim_and_launch():
                await asyncio.to_thread(self.store.upsert_documents, RUNS_COLLECTION,
                    [{"owner": owner, "key": key, "document": run}])
                self._run_task = asyncio.create_task(self._generate(owner, key, run, settings), name="morning-market-brief-research")
                self._run_task.add_done_callback(self._finished)
            task = asyncio.create_task(claim_and_launch())
            await _join(task)
            return {"status": "running", "run_id": run["run_id"]}

    def _finished(self, task):
        if not task.cancelled():
            task.exception()

    async def _generate(self, owner, key, run, settings):
        started = perf_counter()
        self._diagnostics["attempts"] += 1
        inputs = {"cutoff_at": run["cutoff_at"], "timezone": "Asia/Seoul",
                  "watchlist": settings["watchlist"], "source_mode": "ai_web_search",
                  "portfolio": "보유 비중·계좌 자료 없음; 추정 금지"}
        run["input_hash"] = content_hash(inputs)
        run["inputs"] = inputs
        prompt = render_prompt(settings, inputs)
        run["schema_version"] = SCHEMA
        run["output_contract_hash"] = content_hash(output_schema())
        run["rendered_prompt_hash"] = content_hash(prompt)
        try:
            if self.ai is None:
                raise ValueError("NAS AI 연결을 먼저 설정하세요.")
            reply = await self.ai.research_market(prompt,
                settings["provider"], settings["model"], settings["reasoning_effort"])
            evidence = {"sources": list(reply.sources), "cutoff_at": run["cutoff_at"],
                        "search_calls": reply.search_calls, "web_actions": list(reply.web_actions)}
            run["sources"] = list(reply.sources)
            run["web_actions"] = list(reply.web_actions)
            run["search_calls"] = reply.search_calls
            run["usage"] = reply.usage
            run["raw_response"] = reply.text
            run["result"] = validate_output(reply.text, evidence)
            run["input_quality_ok"] = bool(reply.sources and reply.search_calls
                and any(m["status"] in {"confirmed", "delayed"} for m in run["result"]["market_data"]))
            run["status"] = "completed"
            run["available_at"] = self.clock().timestamp()
            self._diagnostics["completed"] += 1
        except Exception as error:
            run["status"] = "failed"
            # No upstream raw body / credential / private exception in records.
            run["error"] = getattr(error, "code", "market_brief_failed")
            run["input_quality_ok"] = False
            self._diagnostics["failed"] += 1
            LOGGER.warning("아침 시황 실행 실패: %s", run["error"])
        finally:
            run["elapsed_seconds"] = perf_counter() - started
            run["finished_at"] = self.clock().timestamp()
            self._diagnostics.update(elapsed_seconds=run["elapsed_seconds"], last_status=run["status"])
            await asyncio.to_thread(self.store.upsert_documents, RUNS_COLLECTION,
                [{"owner": owner, "key": key, "document": run}])

    async def _loop(self):
        while not self._closing:
            try:
                await self.request()
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.warning("아침 시황 예약 확인 실패")
            await asyncio.sleep(5)


def create_morning_market_brief_router(runtime, authorize):
    from fastapi import APIRouter, Depends, HTTPException
    from pydantic import BaseModel, ConfigDict, Field
    router = APIRouter(dependencies=[Depends(authorize)])

    class Update(BaseModel):
        model_config = ConfigDict(extra="forbid")
        expected_revision: int = Field(ge=0, strict=True)
        enabled: bool = Field(strict=True)
        time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
        provider: str = Field(pattern=r"^(chatgpt_plan|openai)$")
        model: str = Field(min_length=1, max_length=100)
        reasoning_effort: str = Field(pattern=r"^(|none|low|medium|high|xhigh|max)$")
        ttl_minutes: int = Field(ge=15, le=720, strict=True)
        prompt: str = Field(min_length=50, max_length=30_000)
        watchlist: str = Field(max_length=5000)

    @router.get("/api/v1/market/morning-brief/settings")
    async def settings():
        return runtime.status()

    @router.put("/api/v1/market/morning-brief/settings")
    async def save(values: Update):
        try:
            return await runtime.update(values.model_dump(exclude={"expected_revision"}), values.expected_revision)
        except RuntimeError:
            raise HTTPException(409, "MORNING_BRIEF_REVISION_CONFLICT") from None
        except ValueError as error:
            raise HTTPException(422, str(error)) from None

    @router.get("/api/v1/market/morning-brief")
    async def latest():
        return await runtime.latest()

    @router.post("/api/v1/market/morning-brief/run", status_code=202)
    async def run():
        try:
            return await runtime.request(manual=True)
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
    return router
