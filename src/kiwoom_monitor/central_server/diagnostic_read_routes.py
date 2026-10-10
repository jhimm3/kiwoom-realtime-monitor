"""Diagnostic read HTTP policy; native metrics, control and storage retain ownership."""
import asyncio
import json
from collections.abc import Callable
from typing import Any, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import APIRouter


class DiagnosticStorageReadStore(Protocol):
    def storage_size_bytes(self) -> int | None: ...
    def storage_breakdown(self) -> list[dict[str, object]]: ...


def create_diagnostic_read_router(
    store: DiagnosticStorageReadStore, authorize: Callable[..., None], database_url: str,
    top20_service: Any, news_service: Any, external_market_service: Any,
    current_candidate_monitor: Callable[[], Any], historical_archive: Any,
) -> tuple["APIRouter", Callable[..., Any]]:
    """Return the same workload reader used by HTTP and diagnostic control/run sampling."""
    from fastapi import APIRouter, Depends, HTTPException
    from .resource_usage import resource_usage

    router = APIRouter()

    @router.get("/api/v1/diagnostics/resources", dependencies=[Depends(authorize)])
    async def diagnostics_resources() -> dict[str, object]:
        database_size, storage_categories = await asyncio.gather(
            asyncio.to_thread(store.storage_size_bytes),
            asyncio.to_thread(store.storage_breakdown),
        )
        return {
            **resource_usage("/app/data", database_size),
            "storage_categories": storage_categories,
            "storage_category_bytes_are_estimates": True,
            "retention_policy": {
                "mode": "unlimited",
                "automatic_deletion_enabled": False,
            },
        }

    @router.get("/api/v1/diagnostics/workloads", dependencies=[Depends(authorize)])
    async def diagnostic_workloads() -> dict[str, object]:
        from .diagnostic_workloads import WORKLOADS, control_path, control_snapshot
        from .diagnostic_metrics import refresh_capture_state

        refresh_capture_state(force=True)
        path = control_path()
        control = control_snapshot(path)
        capture = control["metrics_capture"]
        diagnostic_tool = control["diagnostic_tool"]
        paused = control["paused"]
        configured = {
            "minute_backfill": bool(top20_service and top20_service._minute_backfill_enabled),
            "minute_query_metadata": bool(database_url.startswith("postgres")),
            "top20_after_close": top20_service is not None,
            "news_jobs": bool(news_service and news_service._job_runner),
            "news_stock_refresh": bool(news_service and (
                news_service._naver_api_enabled or news_service._naver_stock_enabled
                or news_service._dart_enabled)),
            "news_query_set": bool(news_service and news_service._query_collector._enabled),
            "news_market_feed": bool(news_service and news_service._market_collector
                                     and news_service._market_collector._enabled),
            "external_market": external_market_service is not None,
            "candidate_monitor": current_candidate_monitor() is not None,
            "historical_news_archive": historical_archive is not None,
        }
        external_runtime = (external_market_service.diagnostic_status()
                            if external_market_service is not None else {
                                "configured": False, "operational_enabled": False,
                                "running": False, "poll_seconds": None,
                                "collection_attempts": 0,
                                "collection_completions": 0,
                                "collection_saved_rows_total": 0,
                                "last_collection_started_at": None,
                                "last_collection_completed_at": None,
                                "last_collection_saved_rows": 0,
                                "last_collection_error": None,
                            })
        expiries = control["workload_expiries"]
        workloads = {}
        for name in sorted(WORKLOADS):
            effective = configured[name] and name not in paused
            item = {"configured": configured[name], "effective": effective,
                    "diagnostic_switch_available": diagnostic_tool["enabled"],
                    "paused_by_diagnostic": name in paused,
                    "expires_at": expiries.get(name)}
            if name == "external_market":
                item["effective"] = bool(external_runtime["operational_enabled"]
                                         and external_runtime["running"]
                                         and name not in paused)
                item["runtime"] = external_runtime
            workloads[name] = item
        return {"diagnostic_tool": diagnostic_tool, "workloads": workloads,
            "metrics_capture": capture, "trace_capture": control["trace_capture"],
            "expires_at": control["expires_at"],
            "control_revision": control["control_revision"]}

    @router.get("/api/v1/diagnostics/market-bar-saves", dependencies=[Depends(authorize)])
    async def diagnostic_market_bar_saves(start: float, end: float) -> dict[str, object]:
        from .diagnostic_metrics import refresh_capture_state, summarize_market_bar_saves

        if end <= start or end - start > 1800:
            raise HTTPException(400, detail="DIAGNOSTIC_TIME_RANGE_INVALID")
        refresh_capture_state(force=True)
        return summarize_market_bar_saves(start, end)

    @router.get("/api/v1/diagnostics/writers", dependencies=[Depends(authorize)])
    async def diagnostic_writers() -> dict[str, object]:
        from .diagnostic_writer_registry import writer_registry

        return {"coverage": "instrumented_postgres_writers_only",
                "writers": writer_registry(),
                "unmeasured": ["raw/unmigrated PostgreSQL writers", "PC SQLite", "Journal SQLite",
                               "errors/retries outside common DB pilot", "payload byte estimates",
                               "per-backend wait attribution"]}

    @router.get("/api/v1/diagnostics/db-calls", dependencies=[Depends(authorize)])
    async def diagnostic_db_calls(start: float, end: float,
                                  mode: str = "summary", limit: int = 200,
                                  slow_ms: int = 500) -> dict[str, object]:
        from .diagnostic_metrics import summarize_db_calls

        if end <= start or end - start > 1800:
            raise HTTPException(400, detail="DIAGNOSTIC_TIME_RANGE_INVALID")
        if (mode not in {"summary", "verbose", "raw"}
                or not 1 <= limit <= 500 or not 1 <= slow_ms <= 30_000):
            raise HTTPException(400, detail="DIAGNOSTIC_DB_MODE_INVALID")
        return summarize_db_calls(start, end, mode=mode, limit=limit, slow_ms=slow_ms)

    @router.get("/api/v1/diagnostics/news-job-claim-plan", dependencies=[Depends(authorize)])
    async def diagnostic_news_job_claim_plan() -> dict[str, object]:
        explain = getattr(store, "explain_news_job_claim_plan", None)
        if not callable(explain):
            raise HTTPException(501, detail="POSTGRES_DIAGNOSTIC_UNAVAILABLE")
        result = await asyncio.to_thread(explain)
        if len(json.dumps(result).encode("utf-8")) > 1_048_576:
            raise HTTPException(503, detail="DIAGNOSTIC_PLAN_TOO_LARGE")
        return result

    @router.get("/api/v1/diagnostics/news-job-claim-readonly-analyze",
             dependencies=[Depends(authorize)])
    async def diagnostic_news_job_claim_readonly_analyze(stage: str = "BODY") -> dict[str, object]:
        analyze = getattr(store, "analyze_news_job_claim_read_only", None)
        if not callable(analyze):
            raise HTTPException(501, detail="POSTGRES_DIAGNOSTIC_UNAVAILABLE")
        if stage not in {"BODY", "RULE"}:
            raise HTTPException(400, detail="NEWS_JOB_STAGE_INVALID")
        return await asyncio.to_thread(analyze, stage)

    return router, diagnostic_workloads
