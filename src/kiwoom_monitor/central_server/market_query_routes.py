"""Market query HTTP policy and archive reuse; native broker and storage stay app-owned."""
import asyncio
from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol, TYPE_CHECKING

from .market_ingest import fundamentals_document_is_current, nxt_eligibility_document_is_current
from .market_observations import KST
from .market_read_routes import _archive_coverage_ready

if TYPE_CHECKING:
    from fastapi import APIRouter
    from .rest_broker import CentralRestBroker
    from .realtime_collector import CentralRealtimeCollector


class MarketQueryStore(Protocol):
    def load_documents(self, collection: str, owner: str = "", limit: int = 1000) -> list[dict[str, Any]]: ...

    def load_minute_bars(self, code: str, trading_date: str, market: str = "") -> list[dict[str, Any]]: ...

    def load_daily_bars(self, code: str, market: str = "", limit: int = 250) -> list[dict[str, Any]]: ...


def create_market_query_router(
    store: MarketQueryStore, authorize: Callable[..., None],
    broker: "CentralRestBroker | None", collector: "CentralRealtimeCollector | None",
) -> "APIRouter":
    """Preserve archive-first reads, account-query exclusion and reconnect fencing."""
    from fastapi import APIRouter, Depends, HTTPException
    from pydantic import BaseModel, Field

    router = APIRouter()

    class QueryRequest(BaseModel):
        api_id: str = Field(min_length=7, max_length=7)
        path: str
        body: dict[str, Any] = Field(default_factory=dict)
        cont_yn: str = "N"
        next_key: str = ""

    @router.post("/api/v1/kiwoom/query", dependencies=[Depends(authorize)])
    async def kiwoom_query(query: QueryRequest) -> dict[str, object]:
        from .rest_broker import ACCOUNT_RECOVERY_ENDPOINTS
        if query.api_id in ACCOUNT_RECOVERY_ENDPOINTS:
            raise HTTPException(400, detail="ACCOUNT_QUERY_SCOPE_REQUIRED")
        if query.cont_yn == "N":
            archived = await asyncio.to_thread(
                _stored_market_response, store, query.api_id, query.body,
            )
            if archived is not None:
                return {
                    "payload": archived, "has_next": False, "next_key": "",
                    "cache_hit": False, "archive_hit": True,
                }
        if broker is None:
            raise HTTPException(status_code=503, detail="서버에 키움 API 키가 설정되지 않았습니다.")
        if collector is not None and getattr(broker, "_credential_paused", False):
            connection_status = collector.credential_connection_status()
            if connection_status["planned_reconnect"]:
                raise HTTPException(503, detail={"code": "REALTIME_RECONNECTING", "connection_status": connection_status})
        try:
            result = await broker.request(
                query.api_id, query.path, query.body, cont_yn=query.cont_yn, next_key=query.next_key,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        return {
            "payload": result.payload,
            "has_next": result.has_next,
            "next_key": result.next_key,
            "cache_hit": result.cache_hit,
        }

    return router


def _stored_market_response(
    store: Any, api_id: str, body: dict[str, Any],
) -> dict[str, Any] | None:
    """완료 차트와 최신 종목 문서를 키움 TR보다 먼저 재사용한다."""
    if api_id in {"ka10001", "ka10100"}:
        code = str(body.get("stk_cd", "")).strip().removesuffix("_NX").removesuffix("_AL")
        collection = (
            "stock_fundamentals" if api_id == "ka10001"
            else "stock_nxt_eligibility"
        )
        values = store.load_documents(collection, code, 1) if code else []
        if values:
            document = values[0].get("document", {})
            checked_at = datetime.now(KST)
            if api_id == "ka10001" and not fundamentals_document_is_current(
                document, checked_at.date(), checked_at=checked_at,
            ):
                return None
            if api_id == "ka10100" and not nxt_eligibility_document_is_current(
                document, checked_at.date(),
            ):
                return None
            payload = document.get("payload") if isinstance(document, dict) else None
            if isinstance(payload, dict):
                return payload
    return _archived_chart_response(store, api_id, body)


def _archived_chart_response(store: Any, api_id: str, body: dict[str, Any]) -> dict[str, Any] | None:
    """완료 확인된 NAS 차트를 키움 TR보다 먼저 재사용한다."""
    raw_code = str(body.get("stk_cd", "")).strip()
    market = "NXT" if raw_code.endswith("_NX") else "SOR" if raw_code.endswith("_AL") else "KRX"
    code = raw_code.removesuffix("_NX").removesuffix("_AL")
    if not code:
        return None
    if api_id == "ka10080":
        raw_day = str(body.get("base_dt", "")).strip()
        if len(raw_day) != 8 or not raw_day.isdigit():
            return None
        day = f"{raw_day[:4]}-{raw_day[4:6]}-{raw_day[6:]}"
        coverage = store.load_documents("market_data_coverage", f"{day}:{code}:{market}", 1)
        if not _archive_coverage_ready(coverage, day, "minute"):
            return None
        bars = store.load_minute_bars(code, day, market)
        if not bars:
            return None
        return {"stk_min_pole_chart_qry": [{
            "cntr_tm": f"{raw_day}{str(bar['minute']).replace(':', '')}00",
            "open_pric": str(bar["open"]), "high_pric": str(bar["high"]),
            "low_pric": str(bar["low"]), "cur_prc": str(bar["close"]),
            "trde_qty": str(bar["volume"]),
        } for bar in reversed(bars)]}
    if api_id == "ka10081":
        raw_day = str(body.get("base_dt", "")).strip()
        if len(raw_day) != 8 or not raw_day.isdigit():
            return None
        day = f"{raw_day[:4]}-{raw_day[4:6]}-{raw_day[6:]}"
        coverage = store.load_documents("market_data_coverage_daily", f"{code}:{market}", 1)
        if not _archive_coverage_ready(coverage, day, "daily"):
            return None
        from .postgres_access import db_call_source
        with db_call_source("api.kiwoom.archive_daily_bars"):
            stored_bars = store.load_daily_bars(code, market, 5000)
        bars = [
            bar for bar in stored_bars
            if str(bar.get("trading_date", "")) <= day
        ][:250]
        # 과거 버전이 NXT 빈 응답도 완료(rows=0)로 남긴 경우가 있다.
        # 빈 아카이브는 확정 자료가 아니므로 키움 조회를 우회하지 않는다.
        if not bars:
            return None
        return {"stk_dt_pole_chart_qry": [{
            "date": str(bar["trading_date"]).replace("-", ""),
            "open_pric": str(bar["open"]), "high_pric": str(bar["high"]),
            "low_pric": str(bar["low"]), "cur_prc": str(bar["close"]),
            "trde_qty": str(bar["volume"]),
            "trde_prica": str(bar.get("trade_value_million_won") or 0),
        } for bar in bars]}
    return None
