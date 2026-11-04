"""Installed diagnostic tool composition and its read HTTP policy."""
import asyncio
import json
from collections.abc import Callable
from typing import Any, Protocol, TYPE_CHECKING

from . import diagnostic_trace as trace

if TYPE_CHECKING:
    from fastapi import APIRouter


class DiagnosticStorageReadStore(Protocol):
    def storage_size_bytes(self) -> int | None: ...
    def storage_breakdown(self) -> list[dict[str, object]]: ...


def compose_application_inputs(store) -> dict[str, Any]:
    """Install native-store observation and build the app's one tool input set."""
    from . import diagnostic_rest_input, diagnostic_top20_input, diagnostic_top20_flow_input
    from .diagnostic_top20_lifecycle_input import HubInputRecorder, CollectorInputRecorder
    from .diagnostic_top20_subscription_v2 import SubscriptionRecorder
    from .diagnostic_replay_contract import captured_workload, captured_workload_scope, install_store_capture
    from .diagnostic_workloads import is_paused

    install_store_capture(store)
    hub = HubInputRecorder()
    return {
        "hub": hub,
        "broker": diagnostic_rest_input,
        "subscription": SubscriptionRecorder(hub_observer=hub),
        "collector": CollectorInputRecorder(hub_observer=hub),
        "news_scope": captured_workload("news", "news_jobs"),
        "news_paused": lambda: is_paused("news_jobs"),
        "news": {"workload_paused": is_paused},
        "morning_brief": {"workload_paused": is_paused},
        "external_market": {"workload_paused": is_paused},
        "historical_archive": {"workload_paused": is_paused},
        "candidate": {"workload_paused": is_paused, "capture_scope": captured_workload_scope},
        "accounts": {"capture_scope": captured_workload_scope},
        "market_events": {"capture_scope": captured_workload_scope},
        "top20": {
            "capture_scope": captured_workload_scope,
            "ranking_input_observer": diagnostic_top20_input,
            "flow_input_observer": diagnostic_top20_flow_input,
            "catalog_input_observer": diagnostic_rest_input,
            "workload_paused": is_paused,
        },
    }


def operator_state_facts(runs) -> dict[str, dict[str, object]]:
    """Read only the installed owners, without HTTP, report files or product probes."""
    from .diagnostic_workloads import maintenance_status
    return {
        "control": maintenance_status(),
        "trace": trace.maintenance_status(),
        "runs": runs.maintenance_status() if runs is not None else {"state": "not_created"},
    }


def install_application_routes(
    app, store, authorize, database_url, top20_service, news_service,
    external_market_service, current_candidate_monitor, historical_archive, *,
    morning_brief, current_news_job_runner, server_build, logger,
):
    """Keep diagnostic read/control/run registration and run ownership together."""
    from fastapi import HTTPException
    from .diagnostic_workloads import control_path
    from .diagnostic_runs import DiagnosticRuns
    from .diagnostic_control_routes import create_diagnostic_control_router
    from .diagnostic_run_routes import create_diagnostic_run_router

    router, diagnostic_workloads = create_diagnostic_read_router(
        store, authorize, database_url, top20_service, news_service,
        external_market_service, current_candidate_monitor, historical_archive,
        morning_brief=morning_brief, current_news_job_runner=current_news_job_runner)
    app.include_router(router)

    def diagnostic_internal_api(path: str, query: dict | None = None) -> dict:
        query = query or {}
        if path.endswith("/workloads"):
            loop = app.state.diagnostic_loop
            return asyncio.run_coroutine_threadsafe(diagnostic_workloads(), loop).result(timeout=10)
        if path.endswith("/writers"):
            from .diagnostic_writer_registry import writer_registry
            return {"writers": writer_registry(), "coverage": "instrumented_postgres_writers_only"}
        if path.endswith("/db-calls"):
            from .diagnostic_metrics import summarize_db_calls
            return summarize_db_calls(query["start"], query["end"],
                                      mode=query.get("mode", "summary"),
                                      limit=int(query.get("limit", 200)))
        if path.endswith("/market-bar-saves"):
            from .diagnostic_metrics import summarize_market_bar_saves
            return summarize_market_bar_saves(query["start"], query["end"])
        raise ValueError("unsupported_diagnostic_section")

    diagnostic_path = control_path()
    runs = (DiagnosticRuns(diagnostic_path, diagnostic_internal_api, database_url)
            if diagnostic_path else None)

    def require_diagnostic_runs():
        if runs is None:
            raise HTTPException(501, detail="DIAGNOSTIC_CONTROL_UNAVAILABLE")
        return runs

    app.include_router(create_diagnostic_control_router(
        authorize, diagnostic_path, runs, require_diagnostic_runs,
        diagnostic_workloads, database_url, server_build, account_context_store=store))
    app.include_router(create_diagnostic_run_router(
        authorize, require_diagnostic_runs, database_url, server_build, logger))
    return runs


def create_diagnostic_read_router(
    store: DiagnosticStorageReadStore, authorize: Callable[..., None], database_url: str,
    top20_service: Any, news_service: Any, external_market_service: Any,
    current_candidate_monitor: Callable[[], Any], historical_archive: Any, *,
    current_news_job_runner: Callable[[], Any], morning_brief=None,
) -> tuple["APIRouter", Callable[..., Any]]:
    """Return the same workload reader used by HTTP and diagnostic control/run sampling."""
    from fastapi import APIRouter, Depends, HTTPException
    router = APIRouter()

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
            "news_jobs": current_news_job_runner() is not None,
            "news_stock_refresh": bool(news_service and (
                news_service._naver_api_enabled or news_service._naver_stock_enabled
                or news_service._dart_enabled)),
            "news_query_set": bool(news_service and news_service._query_collector._enabled),
            "news_market_feed": bool(news_service and news_service._market_collector
                                     and news_service._market_collector._enabled),
            "external_market": external_market_service is not None,
            "candidate_monitor": current_candidate_monitor() is not None,
            "historical_news_archive": historical_archive is not None,
            "morning_market_brief": bool(morning_brief and morning_brief.settings["enabled"]),
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
            if name == "morning_market_brief" and morning_brief is not None:
                item["runtime"] = morning_brief.status()["diagnostics"]
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
