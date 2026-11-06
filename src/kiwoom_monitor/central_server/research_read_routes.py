"""Research read HTTP contracts; the app retains storage and monitor ownership."""
from __future__ import annotations

from kiwoom_monitor.domain.time_basis import kst_now, as_kst, aware_iso_kst

import asyncio
import hashlib
import json
from collections.abc import Callable, Awaitable
from datetime import date, datetime, timezone
from typing import Any, Literal, Protocol, TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from .async_lifetime import await_native_call
from kiwoom_monitor.domain.strategy_contract import MINUTE_SHADOW_VERSION
from kiwoom_monitor.domain.research_contract import (
    ReportedResearchResult, ResearchResultConflict,
    REPORTED_RESEARCH_RESULT_COLLECTION, MAX_REPORTED_RESEARCH_RESULT_BYTES,
)

if TYPE_CHECKING:
    from .candidate_monitor import CandidateMonitor
    from .realtime_collector import CentralRealtimeCollector


class ResearchReadStore(Protocol):
    def accept_research_result(self, owner: str, document: dict[str, Any]) -> dict[str, Any]: ...

    def create_observation_export(
        self, start: datetime, end: datetime, kinds: tuple[str, ...], subject: str = "",
    ) -> dict[str, Any]: ...

    def load_observation_export_page(
        self, watermark: str, cursor: int = 0, limit: int = 1000,
    ) -> dict[str, Any]: ...

    def load_shadow_candidates(self, after_sequence: int = 0, limit: int = 100) -> dict[str, Any]: ...

    def load_document(self, collection: str, owner: str, key: str) -> dict[str, Any] | None: ...

    def load_market_event_history(self, kind: str, *, code: str = "", limit: int = 100) -> list[dict[str, Any]]: ...
    def load_observation_revisions(self, kind: str, subject: str, limit: int) -> list[dict[str, Any]]: ...


def _confirmed_krx_day(receipt: dict[str, Any] | None, cut: datetime) -> date | None:
    """Use an observed native 0s receipt only for its own visible KST day."""
    if receipt is None:
        return None
    day = cut.date()
    document, updated_at = receipt.get("document"), receipt.get("updated_at")
    if (not isinstance(document, dict) or document.get("trading_date") != day.isoformat()
            or document.get("source") != "kiwoom_websocket_0s"
            or document.get("status_code") not in ("0", "2", "3", "4", "8", "9", "a", "b", "c", "d")
            or type(updated_at) not in (int, float)):
        return None
    try:
        observed = aware_iso_kst(document["observed_at"])
        available = datetime.fromtimestamp(updated_at, timezone.utc)
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError, OSError):
        return None
    return day if observed.date() == day and observed <= available <= cut else None


def create_research_read_router(
    store: ResearchReadStore, authorize: Callable[..., None],
    get_candidate_monitor: Callable[[], CandidateMonitor | None],
    *, get_market_theme_shadow_runtime: Callable[[], Any] = lambda: None,
    get_realtime_collector: Callable[[], CentralRealtimeCollector | None] = lambda: None,
    input_authorize: Callable[..., None] | None = None,
    result_authorize: Callable[..., str] | None = None,
    check_stock_thesis_sources: Callable[[int, int], Awaitable[dict[str, Any]]] | None = None,
    now_provider=None,
) -> APIRouter:
    """Read the app-owned current monitor after the DB await, including runtime replacement."""
    router = APIRouter()
    now_kst = (lambda: as_kst(now_provider())) if now_provider else kst_now

    @router.get("/api/v1/research/micro-inputs", dependencies=[Depends(authorize)])
    async def research_micro_inputs(
        symbol: str = Query(pattern=r"^[A-Za-z0-9]{6}$"), venue: Literal["KRX", "NXT"] = Query(),
        variant: str = Query(min_length=1, max_length=32),
        lookback_seconds: int = Query(default=20, ge=1, le=60),
        vi_limit: int = Query(default=100, ge=1, le=1000),
    ) -> dict[str, object]:
        from kiwoom_monitor.application.micro_execution_evidence import MICRO_VARIANTS, assess_micro_shadow_inputs

        if variant not in MICRO_VARIANTS:
            raise HTTPException(status_code=422, detail="unsupported MICRO Shadow variant")
        day_receipt, vi_history = None, ()
        if venue == "KRX" and variant != "BOOK_SCALP":
            day_receipt = await asyncio.to_thread(
                store.load_document, "krx_trading_day_observations", now_kst().date().isoformat(), "latest",
            )
            if _confirmed_krx_day(day_receipt, now_kst()) is not None:
                vi_history = await asyncio.to_thread(store.load_market_event_history, "vi", code=symbol, limit=vi_limit)

        # Take the one current RAM cut after native reads so their visibility
        # is established even if a document writer sampled its clock before COMMIT.
        collector = get_realtime_collector()
        snapshot = collector.ram_observation_snapshot(
            (symbol,), seconds=lookback_seconds, include_micro_books=True,
        ) if collector is not None else None
        cut = aware_iso_kst(snapshot["decision_time"]) if snapshot is not None else now_kst()
        trading_date = _confirmed_krx_day(day_receipt, cut)
        result = assess_micro_shadow_inputs(
            symbol=symbol, venue=venue, variant=variant, decision_at=cut, trading_date=trading_date,
            trade_snapshot=snapshot, vi_history=vi_history,
        )
        return {**result, "order_authorized": False}

    @router.post('/api/v1/research/micro-inputs', dependencies=[Depends(authorize)])
    async def research_micro_frozen_inputs(
        request: Request, evidence_sha256: str = Query(pattern=r'^[0-9a-f]{64}$'),
    ) -> dict[str, object]:
        from kiwoom_monitor.application.micro_execution_evidence import (
            MICRO_FROZEN_EVIDENCE_VERSION, replay_micro_execution,
        )
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_REPORTED_RESEARCH_RESULT_BYTES:
                raise HTTPException(status_code=413, detail='MICRO research evidence exceeds publication budget')
            body.extend(chunk)
        if hashlib.sha256(body).hexdigest() != evidence_sha256:
            raise HTTPException(status_code=422, detail='MICRO research evidence SHA256 does not match')
        try:
            document = json.loads(body)
            if (not isinstance(document, dict) or set(document) != {'scenario', 'policy'}
                    or not isinstance(document['scenario'], dict)
                    or document['scenario'].get('version') != MICRO_FROZEN_EVIDENCE_VERSION):
                raise ValueError('invalid MICRO frozen research descriptor')
            result = replay_micro_execution(document['scenario'], document['policy'])
        except (UnicodeError, ValueError, TypeError, KeyError, RecursionError) as error:
            raise HTTPException(status_code=422, detail='invalid MICRO frozen research descriptor') from error
        return {**result, 'execution_scope': 'frozen_micro_execution_research',
                'evidence_sha256': evidence_sha256, 'candidate': None, 'order_authorized': False}

    @router.post("/api/v1/research/daily-decline-setup", dependencies=[Depends(authorize)])
    async def research_daily_decline_setup(
        request: Request, evidence_sha256: str = Query(pattern=r"^[0-9a-f]{64}$"),
    ) -> dict[str, object]:
        from kiwoom_monitor.application.daily_decline import (
            decode_daily_decline_setup_inputs, evaluate_daily_decline_shadow,
        )

        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > 256 * 1024:
                raise HTTPException(status_code=413, detail="DD setup evidence exceeds 256 KiB")
            body.extend(chunk)
        if hashlib.sha256(body).hexdigest() != evidence_sha256:
            raise HTTPException(status_code=422, detail="DD setup evidence SHA256 does not match")
        try:
            document = json.loads(body)
            required = {"version", "policy", "symbol", "venue", "signal_day", "strategy_references", "basis", "benchmark"}
            if (not isinstance(document, dict) or not required <= document.keys()
                    or document.keys() - required - {"catalyst"} or document["version"] != "dd-shadow-setup-evidence/v1"
                    or not isinstance(document["policy"], dict)):
                raise ValueError("invalid DD Shadow setup evidence")
            inputs = decode_daily_decline_setup_inputs(document, document["policy"])
        except (UnicodeError, ValueError, RecursionError) as error:
            raise HTTPException(status_code=422, detail="invalid DD Shadow setup evidence") from error

        symbol, venue = inputs["symbol"], inputs["venue"]
        revision_limit = 5000
        observations = await asyncio.to_thread(store.load_observation_revisions, "daily_bar", f"{symbol}:{venue}", revision_limit)
        cut = now_kst()
        native = [row for row in observations if (row.get("source_id"), row.get("origin")) == ("kiwoom-ka10081", "query")]
        try:
            decision = evaluate_daily_decline_shadow(native, cut=cut, **inputs)
        except ValueError as error:
            raise HTTPException(status_code=422, detail="invalid DD Shadow setup evidence") from error
        return {
            "version": "dd-shadow-setup-result/v1", "execution_scope": "native_setup_assessment",
            "symbol": symbol, "venue": venue, "signal_day": inputs["signal_day"].isoformat(),
            "cut": cut.isoformat(), "evidence_sha256": evidence_sha256,
            "reference_authority": "caller_supplied", "setup": decision.to_document(),
            "native_revision_count": len(native), "other_revision_count": len(observations) - len(native),
            "revision_read_limit_reached": len(observations) == revision_limit,
            "candidate": None, "order_authorized": False,
        }

    if result_authorize is not None:
        @router.post("/api/v1/research/reported-results")
        async def accept_reported_research_result(request: Request, owner: str = Depends(result_authorize)) -> dict[str, Any]:
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > MAX_REPORTED_RESEARCH_RESULT_BYTES:
                    raise HTTPException(status_code=413, detail="reported research result exceeds 256 KiB")
            try:
                document = ReportedResearchResult.from_document(json.loads(body)).document
            except (UnicodeError, ValueError, TypeError, RecursionError) as error:
                raise HTTPException(status_code=400, detail="invalid reported research result") from error
            # A cancelled request still joins its one native transaction before
            # releasing the request lifetime. Retries read its original receipt.
            try:
                return await await_native_call(store.accept_research_result, owner, document)
            except ResearchResultConflict as error:
                raise HTTPException(status_code=409, detail=str(error)) from error

        @router.get("/api/v1/research/reported-results/{result_id}")
        async def load_reported_research_result(result_id: str, owner: str = Depends(result_authorize)) -> dict[str, Any]:
            value = await asyncio.to_thread(store.load_document, REPORTED_RESEARCH_RESULT_COLLECTION, owner, result_id)
            if value is None:
                raise HTTPException(status_code=404, detail="reported research result not found")
            return value["document"]

    @router.get("/api/v1/research/ram-shadow/source-check", dependencies=[Depends(authorize)])
    async def research_stock_thesis_source_check(
        expected_revision: int = Query(ge=0), lookback_seconds: int = Query(default=20, ge=1, le=60),
    ) -> dict[str, Any]:
        if check_stock_thesis_sources is None:
            raise HTTPException(status_code=409, detail="source check is unavailable")
        try:
            return await check_stock_thesis_sources(expected_revision, lookback_seconds)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.get('/api/v2/research/ram-shadow/source-check', dependencies=[Depends(authorize)])
    async def research_minute_thesis_source_check(
        expected_revision: int = Query(ge=0), lookback_minutes: int = Query(ge=1, le=60),
    ) -> dict[str, Any]:
        if check_stock_thesis_sources is None:
            raise HTTPException(status_code=409, detail='source check is unavailable')
        try:
            return await check_stock_thesis_sources(expected_revision, lookback_minutes, True)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.get("/api/v1/research/ram-shadow", dependencies=[Depends(authorize)])
    async def research_ram_shadow(include_recorded: bool = Query(default=False)) -> dict[str, object]:
        from .ram_shadow_selection_config import SHADOW_RESULT_COLLECTION, SHADOW_RESULT_OWNER
        recorded = None
        if include_recorded:
            recorded = await asyncio.to_thread(store.load_document,
                SHADOW_RESULT_COLLECTION, SHADOW_RESULT_OWNER, "latest")
        runtime = get_market_theme_shadow_runtime()
        live = runtime.latest_results if runtime is not None else None
        now = now_kst()
        for envelope in (live, recorded.get("document") if recorded else None):
            if envelope is None:
                continue
            for slot in envelope["results"]:
                decision = slot["decision"]
                cutoff = decision["valid_until"] if decision else envelope["contexts"][slot["context_ref"]]["expires_at"]
                slot["display_state"] = "EXPIRED" if datetime.fromisoformat(cutoff) <= now else "CURRENT"
        return {"runtime": runtime.status if runtime is not None else {"state": "DISABLED"},
                "live": live,
                "recorded": recorded, "recorded_is_live": False,
                "replay_eligible": False, "order_authorized": False}

    @router.get("/api/v1/research/observations", dependencies=[Depends(input_authorize or authorize)])
    async def research_observations(
        start: datetime = Query(),
        end: datetime = Query(),
        kinds: str = Query(min_length=1, max_length=64),
        subject: str = Query(default="", max_length=32),
        cursor: int = Query(default=0, ge=0),
        watermark: str = Query(default="", max_length=64),
        limit: int = Query(default=1000, ge=1, le=1000),
    ) -> dict[str, object]:
        requested_kinds = tuple(dict.fromkeys(
            value.strip() for value in kinds.split(",") if value.strip()
        ))
        try:
            if start.tzinfo is None or end.tzinfo is None:
                raise ValueError("research export timestamps must be timezone-aware")
            if not watermark:
                if cursor:
                    raise ValueError("cursor requires a fixed watermark")
                manifest = await asyncio.to_thread(
                    store.create_observation_export, start, end, requested_kinds, subject,
                )
                watermark = str(manifest["fixed_watermark"])
            page = await asyncio.to_thread(
                store.load_observation_export_page, watermark, cursor, limit,
            )
        except ValueError as error:
            status = 404 if "unknown research export watermark" in str(error) else 400
            raise HTTPException(status_code=status, detail=str(error)) from error
        manifest = page["manifest"]
        expected_range = manifest.get("captured_range", {})
        if (
            list(requested_kinds) != manifest.get("kinds")
            or subject != manifest.get("subject")
            or start.astimezone(timezone.utc).isoformat() != expected_range.get("start")
            or end.astimezone(timezone.utc).isoformat() != expected_range.get("end")
        ):
            raise HTTPException(status_code=409, detail="watermark parameters do not match its fixed dataset")
        return page

    @router.get("/api/v1/research/candidates", dependencies=[Depends(authorize)])
    async def research_candidates(
        after_sequence: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> dict[str, object]:
        page = await asyncio.to_thread(store.load_shadow_candidates, after_sequence, limit)
        now = now_kst()
        for event in page["events"]:
            try:
                expires_at = datetime.fromisoformat(str(event.get("expires_at", "")))
                expired = expires_at.astimezone(timezone.utc) < now
                if event.get("shadow_scope") == MINUTE_SHADOW_VERSION:
                    expired = expires_at <= now
            except ValueError:
                expired = True
            event["status"] = "EXPIRED" if expired else "ACTIVE"
        candidate_monitor = get_candidate_monitor()
        projector = getattr(candidate_monitor, "project_leader_candidate_status", None)
        for event in page["events"]:
            if event["status"] == "ACTIVE":
                if event.get("shadow_scope") == MINUTE_SHADOW_VERSION:
                    if datetime.fromisoformat(event["available_at"]) > now:
                        event["status"] = "WARMUP"
                        continue
                    event["status"] = (candidate_monitor.project_minute_candidate_status(event)
                                       if candidate_monitor is not None else "SUPERSEDED")
                    continue
                projected = projector(event) if callable(projector) else None
                if projected is not None:
                    event["status"] = projected
                elif event.get("strategy_id") == "leader_breakout":
                    # A disabled or legacy owner cannot keep a new-policy
                    # episode active merely because its original TTL remains.
                    event["status"] = "SUPERSEDED"
        page["quality"] = (
            candidate_monitor.quality if candidate_monitor is not None
            else {"status": "DISABLED", "reason": "shadow_candidate_generation_disabled"}
        )
        return page

    return router
