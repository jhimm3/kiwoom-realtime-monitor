"""Market dataset read policy; storage and live TOP20 lifecycle remain app-owned."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol, TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query

if TYPE_CHECKING:
    from .autonomous_top20 import AutonomousTop20Service


class MarketDatasetReadStore(Protocol):
    def load_dataset_snapshots(self, kind: str, subject: str = "", limit: int = 100) -> list[dict[str, Any]]: ...

    def load_top20_statistics(self, start_date: str, end_date: str) -> dict[str, object]: ...


def create_market_dataset_read_router(
    store: MarketDatasetReadStore, authorize: Callable[..., None],
    top20_service: AutonomousTop20Service | None,
) -> APIRouter:
    """Use the same app-selected store and live service without a new runtime owner."""
    router = APIRouter()

    @router.get("/api/v1/market/snapshots/{kind}", dependencies=[Depends(authorize)])
    async def dataset_snapshots(
        kind: str, subject: str = Query(default="", max_length=32),
        limit: int = Query(default=100, ge=1, le=5000),
        prefer_live: bool = Query(default=False),
    ) -> dict[str, object]:
        allowed = {
            "ranking", "top20_membership", "top20_index", "market_state",
            "investor_flow", "program_flow", "new_high", "stock_fundamentals",
            "nxt_eligibility", "market_index_chart",
        }
        if kind not in allowed:
            raise HTTPException(status_code=404, detail="지원하지 않는 중앙 시장 자료입니다.")
        if kind == "top20_membership" and limit == 1 and prefer_live and top20_service is not None:
            latest = top20_service.latest_membership_snapshot(subject)
            if latest is not None:
                return {"kind": kind, "subject": subject, "snapshots": [latest]}
        values = await asyncio.to_thread(store.load_dataset_snapshots, kind, subject, limit)
        return {"kind": kind, "subject": subject, "snapshots": values}

    @router.get("/api/v1/market/top20-statistics", dependencies=[Depends(authorize)])
    async def top20_statistics(start_date: str, end_date: str) -> dict[str, object]:
        try:
            start = datetime.fromisoformat(start_date).date()
            end = datetime.fromisoformat(end_date).date()
        except ValueError as error:
            raise HTTPException(status_code=422, detail="TOP20 통계 날짜 형식이 올바르지 않습니다.") from error
        if end < start or (end - start).days > 366:
            raise HTTPException(status_code=422, detail="TOP20 통계 범위는 최대 367일입니다.")
        result = await asyncio.to_thread(
            store.load_top20_statistics, start.isoformat(), end.isoformat(),
        )
        return {"start_date": start.isoformat(), "end_date": end.isoformat(), **result}

    return router
