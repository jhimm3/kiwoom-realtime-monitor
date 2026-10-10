"""Diagnostic switch and trace HTTP policy; native control and capture own their work."""
import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import APIRouter
    from .diagnostic_runs import DiagnosticRuns


def create_diagnostic_control_router(
    authorize: Callable[..., None], diagnostic_path: Path | None,
    diagnostic_runs: "DiagnosticRuns | None", require_diagnostic_runs: Callable[[], "DiagnosticRuns"],
    diagnostic_workloads: Callable[..., Any], database_url: str, server_build: str,
    *, account_context_store: Any = None,
) -> "APIRouter":
    """Preserve native CAS/history, busy capture fencing and conditional rollback."""
    from fastapi import APIRouter, Depends, HTTPException, Response
    from pydantic import BaseModel, ConfigDict, Field
    from .diagnostic_workloads import (
        WORKLOADS, _history as record_diagnostic_history,
        _set as set_diagnostic_workload, _set_capture as set_diagnostic_capture,
        _set_tool as set_diagnostic_master, control_path as diagnostic_control_path,
        instance_id as diagnostic_instance_id,
    )

    router = APIRouter()

    class DiagnosticControlRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        target: str = Field(pattern=r"^(master|capture|workload)$")
        enabled: bool | None = None
        paused: bool | None = None
        workload: str = ""
        ttl_seconds: int = Field(default=600, ge=60, le=7200)
        expected_session: str | None = None
        expected_revision: int = Field(ge=0)
        expected_instance: str | None = None

    class DiagnosticTraceRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        seconds: int = Field(ge=60, le=7200)
        expected_session: str
        store_inputs: bool = Field(default=False, strict=True)
        collector_inputs: bool = Field(default=False, strict=True)
        top20_inputs: bool = Field(default=False, strict=True)
        large_inputs: bool = Field(default=False, strict=True)
        account_inputs: bool = Field(default=False, strict=True)
        persist_at: float | None = Field(default=None, gt=0, allow_inf_nan=False)

    @router.get("/api/v1/diagnostics/capabilities", dependencies=[Depends(authorize)])
    async def diagnostic_capabilities() -> dict[str, object]:
        from .diagnostic_replay import (MAX_QUERY_MINUTE_ROWS, MAX_QUERY_MINUTE_TOTAL_ROWS,
                                        QUERY_MINUTE_SCENARIOS, TRACE_SYNTHETIC_KINDS)
        return {"server_build": server_build, "producer_instance": diagnostic_instance_id(),
                "control_available": diagnostic_runs is not None,
                "postgres_available": database_url.startswith("postgres"),
                "sections": ["postgres", "activity", "news_jobs", "host", "storage"],
                "run_kinds": ["measure", "compare", "replay"],
                "trace_input_capture": {
                    "schema_version": 3,
                    "options": {"store_inputs": False, "collector_inputs": False, "top20_inputs": False},
                    "large_input_capture": {
                        "schema_version": 4, "request_field": "large_inputs", "default": False,
                        "requires": ["store_inputs", "persist_at"],
                        "profile": "large-store-input/v1", "copy_limit_bytes": 64 * 1024 * 1024,
                        "block_bytes": 1024 * 1024, "encoded_limit_bytes": 128 * 1024 * 1024,
                        "capacity_acceptance": "pending",
                    },
                    "account_input_capture": {
                        "schema_version": 4, "request_field": "account_inputs", "default": False,
                        "requires": ["store_inputs", "persist_at"],
                        "input_version": "account-store-input/v1", "scope": "typed_native_db_arguments",
                        "authority_tokens": "session_hmac_aliases", "native_replay_ready": False,
                        "context_baseline_acceptance": "pending",
                        "context_capture": {"version": "account-context/v2", "copy_limit_bytes": 32 * 1024 * 1024,
                                            "row_limit": 50_000, "source_state_equivalent": False,
                                            "postgres_snapshot": "repeatable_read_read_only"},
                    },
                    "top20_input_capture": {"schema_version": 3,
                        "input_version": "top20-ranking-input/v1",
                        "scope": "ranking_validation_only", "downstream_replay_supported": False,
                        "candidate_flow_input_version": "top20-candidate-flow-input/v1",
                        "candidate_flow_scope": "candidate_flow_consumer_and_ingest",
                        "broker_queue_and_cache_scheduling_replayed": False},
                    "causal_input_versions": {"market_request": "market-request-tape/v1",
                        "ranking": "top20-ranking-input/v1", "realtime_lifecycle": "top20-realtime-tape/v1",
                        "delivery": "top20-hub-delivery/v1"},
                    "causal_input_boundaries": ["REST", "catalog", "ranking", "subscription", "lifecycle", "delivery_receipt"],
                    "source_state_equivalence_verified": False,
                    "capacity_acceptance": "pending_sizing_and_NAS_gates",
                    "delivery_retention": "framed_ram_v1_deferred_only",
                    "store_input_profiles": {"stock-catalog-documents/v1": {
                        "method": "replace_documents", "collection": "stock_catalog",
                        "maximum_copy_bytes": 16 * 1024 * 1024}},
                    "collector_input_version": "collector-input/v2",
                    "collector_event_types": ["0B", "0w", "0J", "0U"],
                    "coverage": "observed_paths_only",
                    "overhead_verified": False,
                    "deferred_persistence": {
                        "supported": True, "request_field": "persist_at", "max_delay_seconds": 86400,
                        "memory_limit_bytes": 8 * 1024 * 1024 * 1024,
                        "event_capacity": 5_000_000, "write_bytes_per_second": 1024 * 1024,
                    },
                },
                "trace_replay_writer_kinds": sorted(TRACE_SYNTHETIC_KINDS),
                "query_minute_scenarios": sorted(QUERY_MINUTE_SCENARIOS),
                "limits": {"measure_seconds": 300, "compare_phase_seconds": 1100,
                           "ttl_seconds": 7200, "activity_rows": 200,
                           "query_minute_rows_per_call": MAX_QUERY_MINUTE_ROWS,
                           "query_minute_total_input_rows": MAX_QUERY_MINUTE_TOTAL_ROWS},
                "scope_note": "server-process metrics, database-wide counters and host samples; independent PC/importer memory unavailable"}

    @router.put("/api/v1/diagnostics/control", dependencies=[Depends(authorize)])
    async def diagnostic_control_update(body: DiagnosticControlRequest) -> dict[str, object]:
        require_diagnostic_runs()
        if body.target != "master" and body.ttl_seconds > 3600:
            raise HTTPException(422, detail="DIAGNOSTIC_CHILD_TTL_OUT_OF_BOUNDS")
        if (body.target in {"master", "capture"} and
                (body.enabled is None or body.paused is not None or body.workload)
                or body.target == "workload" and
                (body.paused is None or body.enabled is not None or body.workload not in WORKLOADS)):
            raise HTTPException(422, detail="DIAGNOSTIC_CONTROL_INPUT_INVALID")
        kwargs = {"expected_revision": body.expected_revision,
                  "expected_instance": body.expected_instance}
        try:
            if body.target == "master":
                await asyncio.to_thread(set_diagnostic_master, diagnostic_path,
                                        body.enabled, body.ttl_seconds,
                                        expected_session=body.expected_session, **kwargs)
            elif body.target == "capture":
                if body.expected_session is None:
                    raise ValueError("diagnostic_control_conflict")
                await asyncio.to_thread(set_diagnostic_capture, diagnostic_path,
                                        body.enabled, body.ttl_seconds,
                                        expected_session=body.expected_session, **kwargs)
            else:
                if body.expected_session is None:
                    raise ValueError("diagnostic_control_conflict")
                await asyncio.to_thread(set_diagnostic_workload, diagnostic_path,
                                        body.workload, body.paused, body.ttl_seconds,
                                        expected_session=body.expected_session, **kwargs)
        except ValueError as error:
            raise HTTPException(409, detail=str(error)) from error
        history_error = None
        try:
            record_diagnostic_history("api_control", workload=body.workload,
                                      detail={"target": body.target,
                                              "revision": body.expected_revision})
        except OSError as error:
            history_error = type(error).__name__
        from .diagnostic_metrics import refresh_capture_state
        refresh_capture_state(force=True)
        result = await diagnostic_workloads()
        if history_error:
            result["history_error_type"] = history_error
        return result

    @router.post("/api/v1/diagnostics/trace", dependencies=[Depends(authorize)])
    async def diagnostic_trace_start(body: DiagnosticTraceRequest) -> dict[str, object]:
        from .diagnostic_workloads import _set_trace, control_snapshot
        from .diagnostic_trace import start as start_trace, status as trace_state
        path = diagnostic_control_path()
        if path is None:
            raise HTTPException(501, detail="DIAGNOSTIC_CONTROL_UNAVAILABLE")
        control = control_snapshot(path)
        if control["diagnostic_tool"]["session_id"] != body.expected_session:
            raise HTTPException(409, detail="diagnostic_control_conflict")
        busy_states = {"running", "stopping", "awaiting_persistence", "persisting"}
        if trace_state().get("state") in busy_states:
            raise HTTPException(409, detail="trace_already_running")
        try:
            await asyncio.to_thread(_set_trace, path, True, body.seconds,
                                    expected_session=body.expected_session)
            return await asyncio.to_thread(start_trace, seconds=body.seconds,
                                          store_inputs=body.store_inputs,
                                          collector_inputs=body.collector_inputs,
                                          top20_inputs=body.top20_inputs,
                                          large_inputs=body.large_inputs,
                                          account_inputs=body.account_inputs,
                                          account_context_store=account_context_store if body.account_inputs else None,
                                          persist_at=body.persist_at)
        except ValueError as error:
            if trace_state().get("state") not in busy_states:
                await asyncio.to_thread(_set_trace, path, False, body.seconds,
                                        expected_session=body.expected_session)
            raise HTTPException(409, detail=str(error)) from error

    @router.get("/api/v1/diagnostics/trace", dependencies=[Depends(authorize)])
    async def diagnostic_trace_status() -> dict[str, object]:
        from .diagnostic_trace import status as trace_state
        return await asyncio.to_thread(trace_state)

    @router.post("/api/v1/diagnostics/trace/stop", dependencies=[Depends(authorize)])
    async def diagnostic_trace_stop() -> dict[str, object]:
        from .diagnostic_trace import stop as stop_trace
        return await asyncio.to_thread(stop_trace)

    @router.get("/api/v1/diagnostics/trace/{trace_id}", dependencies=[Depends(authorize)])
    async def diagnostic_trace_manifest(trace_id: str) -> dict[str, object]:
        from .diagnostic_trace import status as trace_state
        try:
            return await asyncio.to_thread(trace_state, trace_id)
        except KeyError as error:
            raise HTTPException(404, detail="TRACE_NOT_FOUND") from error

    @router.get("/api/v1/diagnostics/trace/{trace_id}/chunks/{chunk_name}",
             dependencies=[Depends(authorize)])
    async def diagnostic_trace_chunk(trace_id: str, chunk_name: str) -> Response:
        from .diagnostic_trace import chunk_bytes
        try:
            content = await asyncio.to_thread(chunk_bytes, trace_id, chunk_name)
        except KeyError as error:
            raise HTTPException(404, detail="TRACE_CHUNK_NOT_FOUND") from error
        except ValueError as error:
            raise HTTPException(409, detail=str(error)) from error
        return Response(content=content, media_type="application/x-ndjson")

    return router
