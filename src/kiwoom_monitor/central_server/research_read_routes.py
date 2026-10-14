"""Research read HTTP contracts; the app retains storage and monitor ownership."""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Awaitable
from datetime import datetime, timezone
from typing import Any, Protocol, TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query

if TYPE_CHECKING:
    from .candidate_monitor import CandidateMonitor


class ResearchReadStore(Protocol):
    def create_observation_export(
        self, start: datetime, end: datetime, kinds: tuple[str, ...], subject: str = "",
    ) -> dict[str, Any]: ...

    def load_observation_export_page(
        self, watermark: str, cursor: int = 0, limit: int = 1000,
    ) -> dict[str, Any]: ...

    def load_shadow_candidates(self, after_sequence: int = 0, limit: int = 100) -> dict[str, Any]: ...

    def load_document(self, collection: str, owner: str, key: str) -> dict[str, Any] | None: ...


def create_research_read_router(
    store: ResearchReadStore, authorize: Callable[..., None],
    get_candidate_monitor: Callable[[], CandidateMonitor | None],
    *, get_market_theme_shadow_runtime: Callable[[], Any] = lambda: None,
    check_stock_thesis_sources: Callable[[int, int], Awaitable[dict[str, Any]]] | None = None,
) -> APIRouter:
    """Read the app-owned current monitor after the DB await, including runtime replacement."""
    router = APIRouter()

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

    @router.get("/api/v1/research/ram-shadow", dependencies=[Depends(authorize)])
    async def research_ram_shadow(include_recorded: bool = Query(default=False)) -> dict[str, object]:
        from .market_theme_shadow_monitor import SHADOW_RESULT_COLLECTION, SHADOW_RESULT_OWNER
        recorded = None
        if include_recorded:
            recorded = await asyncio.to_thread(store.load_document,
                SHADOW_RESULT_COLLECTION, SHADOW_RESULT_OWNER, "latest")
        runtime = get_market_theme_shadow_runtime()
        live = runtime.latest_results if runtime is not None else None
        now = datetime.now(timezone.utc)
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

    @router.get("/api/v1/research/observations", dependencies=[Depends(authorize)])
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
        now = datetime.now(timezone.utc)
        for event in page["events"]:
            try:
                expires_at = datetime.fromisoformat(str(event.get("expires_at", "")))
                expired = expires_at.astimezone(timezone.utc) < now
            except ValueError:
                expired = True
            event["status"] = "EXPIRED" if expired else "ACTIVE"
        candidate_monitor = get_candidate_monitor()
        page["quality"] = (
            candidate_monitor.quality if candidate_monitor is not None
            else {"status": "DISABLED", "reason": "shadow_candidate_generation_disabled"}
        )
        return page

    return router
