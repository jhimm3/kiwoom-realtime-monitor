"""Diagnostic sampling/run/report HTTP policy; the app owns the native run lifetime."""
import asyncio
from collections.abc import Callable
from logging import Logger
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import APIRouter
    from .diagnostic_runs import DiagnosticRuns


def create_diagnostic_run_router(
    authorize: Callable[..., None], require_diagnostic_runs: Callable[[], "DiagnosticRuns"],
    database_url: str, server_build: str, logger: Logger,
) -> "APIRouter":
    """Use the same run owner and preserve raw/summary fields and failure precedence."""
    from fastapi import APIRouter, Depends, HTTPException
    from pydantic import BaseModel, ConfigDict, Field

    router = APIRouter()

    class DiagnosticRunRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        kind: str = Field(pattern=r"^(measure|compare|replay)$")
        seconds: int = Field(ge=5, le=1100)
        label: str = Field(default="manual", max_length=80)
        workload: str = ""
        profile_report_id: str = Field(default="", max_length=80)
        profile_trace_id: str = Field(default="", max_length=80)
        window_start_seconds: float = Field(default=0, ge=0, le=7200)
        window_end_seconds: float | None = Field(default=None, gt=0, le=7200)
        include_writer_kinds: list[str] = Field(default_factory=list, max_length=20)
        exclude_writer_kinds: list[str] = Field(default_factory=list, max_length=20)
        query_minute_scenario: str = Field(default="", pattern=r"^(|unchanged_page|one_changed_bar|fresh_page|recorded_counts)$")
        request_id: str = Field(default="", max_length=80)
        expected_session: str | None = None
        expected_revision: int | None = Field(default=None, ge=0)

    @router.get("/api/v1/diagnostics/snapshot", dependencies=[Depends(authorize)])
    async def diagnostic_snapshot(sections: str = "postgres,activity,news_jobs,host,storage",
                                  pid: int | None = None) -> dict[str, object]:
        from .diagnostic_sampling import read_host_snapshot, read_postgres_snapshot
        selected = frozenset(part.strip() for part in sections.split(","))
        valid = {"postgres", "activity", "news_jobs", "host", "storage"}
        if not selected or not selected.issubset(valid) or len(sections) > 100 or (pid is not None and pid <= 0):
            raise HTTPException(400, detail="DIAGNOSTIC_SECTION_INVALID")
        result: dict[str, object] = {"server_build": server_build, "sections": {}}
        if selected & {"postgres", "activity", "news_jobs"}:
            if not database_url.startswith("postgres"):
                raise HTTPException(501, detail="POSTGRES_DIAGNOSTIC_UNAVAILABLE")
            try:
                database = await asyncio.to_thread(read_postgres_snapshot, database_url,
                                                   sections=selected, pid=pid)
            except Exception as error:
                logger.warning("diagnostic PostgreSQL snapshot unavailable: %s", type(error).__name__)
                raise HTTPException(503, detail="DIAGNOSTIC_POSTGRES_SNAPSHOT_UNAVAILABLE") from error
            result.update(database)
        if selected & {"host", "storage"}:
            host = await asyncio.to_thread(read_host_snapshot)
            if "host" in selected:
                result["sections"]["host"] = {
                    key: value for key, value in host.items() if key != "storage_mapping"}
            if "storage" in selected:
                result["sections"]["storage"] = host["storage_mapping"]
        return result

    @router.post("/api/v1/diagnostics/runs", status_code=202,
              dependencies=[Depends(authorize)])
    async def diagnostic_run_start(body: DiagnosticRunRequest) -> dict[str, object]:
        runs = require_diagnostic_runs()
        if not database_url.startswith("postgres"):
            raise HTTPException(501, detail="POSTGRES_DIAGNOSTIC_UNAVAILABLE")
        try:
            return await asyncio.to_thread(runs.start, kind=body.kind, seconds=body.seconds,
                                           label=body.label, workload=body.workload,
                                           request_id=body.request_id,
                                           profile_report_id=body.profile_report_id,
                                           profile_trace_id=body.profile_trace_id,
                                           window_start_seconds=body.window_start_seconds,
                                           window_end_seconds=body.window_end_seconds,
                                           include_writer_kinds=tuple(body.include_writer_kinds),
                                           exclude_writer_kinds=tuple(body.exclude_writer_kinds),
                                           query_minute_scenario=body.query_minute_scenario,
                                           expected_session=body.expected_session,
                                           expected_revision=body.expected_revision)
        except ValueError as error:
            raise HTTPException(409 if "busy" in str(error) or "conflict" in str(error)
                                else 400, detail=str(error)) from error

    @router.get("/api/v1/diagnostics/runs/{run_id}", dependencies=[Depends(authorize)])
    async def diagnostic_run_status(run_id: str) -> dict[str, object]:
        try:
            return await asyncio.to_thread(require_diagnostic_runs().status, run_id)
        except KeyError as error:
            raise HTTPException(404, detail="DIAGNOSTIC_RUN_NOT_FOUND") from error

    @router.post("/api/v1/diagnostics/runs/{run_id}/cancel", status_code=202,
              dependencies=[Depends(authorize)])
    async def diagnostic_run_cancel(run_id: str) -> dict[str, object]:
        try:
            return await asyncio.to_thread(require_diagnostic_runs().cancel, run_id)
        except KeyError as error:
            raise HTTPException(404, detail="DIAGNOSTIC_RUN_NOT_FOUND") from error

    @router.get("/api/v1/diagnostics/reports", dependencies=[Depends(authorize)])
    async def diagnostic_reports(limit: int = 100, offset: int = 0) -> dict[str, object]:
        try:
            return await asyncio.to_thread(require_diagnostic_runs().reports,
                                           limit=limit, offset=offset)
        except ValueError as error:
            raise HTTPException(400, detail=str(error)) from error

    @router.get("/api/v1/diagnostics/reports/{report_id}", dependencies=[Depends(authorize)])
    async def diagnostic_report(report_id: str, mode: str = "summary") -> dict[str, object]:
        if mode not in {"summary", "raw"}:
            raise HTTPException(400, detail="DIAGNOSTIC_REPORT_MODE_INVALID")
        try:
            report = await asyncio.to_thread(require_diagnostic_runs().report, report_id)
        except KeyError as error:
            raise HTTPException(404, detail="DIAGNOSTIC_REPORT_NOT_FOUND") from error
        if mode == "summary":
            result = report.get("result", report)
            if isinstance(result, dict):
                phases = ([result.get("phase", {})] if result.get("kind") == "measure"
                          else result.get("phases", []))
                for phase in phases:
                    if not isinstance(phase, dict):
                        continue
                    phase.pop("db_calls_raw", None)
                    phase.pop("db_calls_raw_last_checkpoint", None)
                    for kind in phase.get("market_bar_saves", {}).get("kinds", {}).values():
                        kind.pop("call_samples", None)
        return report

    @router.get("/api/v1/diagnostics/history", dependencies=[Depends(authorize)])
    async def diagnostic_history(limit: int = 100, offset: int = 0) -> dict[str, object]:
        try:
            return await asyncio.to_thread(require_diagnostic_runs().history,
                                           limit=limit, offset=offset)
        except ValueError as error:
            raise HTTPException(400, detail=str(error)) from error

    return router
