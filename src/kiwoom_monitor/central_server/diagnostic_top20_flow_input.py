"""Opt-in first-entry investor-flow inputs and an owned offline replay boundary.

Replays the real ingestor and TOP20 consumer, not transport/cache scheduling.
Shared requests and uncertain native saves fail preflight rather than being seeded.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from datetime import date, datetime
from functools import wraps
import re
import time
from uuid import uuid4

from . import diagnostic_trace as trace
from .diagnostic_replay_contract import capture_owner, freeze_payload, operation_identity, thaw_payload

VERSION = "top20-candidate-flow-input/v1"
_FLOW: ContextVar[dict | None] = ContextVar("top20_candidate_flow_capture", default=None)
_REPLAY_STAGE: ContextVar[dict | None] = ContextVar("top20_candidate_flow_replay_stage", default=None)


def _emit(cycle, kind, value):
    if cycle is None:
        return
    fields = {key: cycle[key] for key in (
        "input_id", "parent_input_id", "workload_id", "producer_component", "actor_id")}
    fields.update(input_kind=kind, market_input_version=VERSION)
    try:
        trace.emit_payload(cycle["trace_id"], "market_input", fields, value)
    except Exception:
        try:
            trace.reject_input(cycle["trace_id"], {**fields, "reason": "flow_capture_error"})
        except Exception:
            pass


def captured_candidate_flow(function):
    @wraps(function)
    async def run(self, code, day):
        # Candidate-flow events are payload inputs owned by the existing store
        # capture switch.  This keeps the feature usable on the deployed v6
        # trace contract, which has no separate TOP20 capture flag.
        token = trace.input_token("store_inputs")
        if token is None:
            return await function(self, code, day)
        producer = f"autonomous_top20:{id(self):x}"
        cycle = {"trace_id": token, "input_id": uuid4().hex,
                 "parent_input_id": operation_identity()["cause_input_id"],
                 "workload_id": "top20_candidate_flow", "producer_component": producer,
                 "actor_id": f"{producer}:{id(asyncio.current_task()):x}", "stage": "marker_read"}
        marker = _FLOW.set(cycle)
        _emit(cycle, "cycle_start", {"code": code, "day": day, "source_time": self._now()})
        outcome, error_type = "returned", ""
        try:
            with capture_owner(cycle["workload_id"], producer, cycle["actor_id"],
                               cause_input_id=cycle["input_id"]):
                return await function(self, code, day)
        except BaseException as error:
            outcome = "cancelled" if isinstance(error, asyncio.CancelledError) else "error"
            error_type = type(error).__name__
            raise
        finally:
            _emit(cycle, "cycle_end", {"outcome": outcome, "stage": cycle["stage"],
                                       "error_type": error_type})
            _FLOW.reset(marker)
    return run


def flow_marker_state(exists):
    cycle = _FLOW.get()
    if cycle is not None:
        _emit(cycle, "marker_state", {"exists": bool(exists)})


def flow_marker_time(value):
    stage = _REPLAY_STAGE.get()
    if stage is not None:
        stage["value"] = "marker_write"
    cycle = _FLOW.get()
    if cycle is not None:
        cycle["stage"] = "marker_write"
        _emit(cycle, "marker_time", {"source_time": value})
    return value


async def candidate_flow_request(broker, body, attempt):
    cycle = _FLOW.get()
    if cycle is None:
        stage = _REPLAY_STAGE.get()
        if stage is not None:
            stage["value"] = "request"
        result = await broker.request("ka10045", "/api/dostk/mrkcond", body)
        if stage is not None:
            stage["value"] = "verify"
        return result
    cycle["stage"] = "request"
    receipt = {"origin": "unobserved", "handler_called": False,
               "handler_supported": False, "handler_outcome": "none", "shared": False}
    link = {"cycle": cycle, "body": dict(body), "receipt": receipt}
    cycle["attempt_link"] = link
    started = time.monotonic_ns()
    value = {"attempt": attempt, "body": dict(body), "receipt": receipt}
    try:
        result = await broker.request("ka10045", "/api/dostk/mrkcond", body)
    except BaseException as error:
        value.update(outcome="error", error_type=type(error).__name__)
        raise
    else:
        cycle["stage"] = "verify"
        value.update(outcome="response", payload={key: result.payload[key] for key in
                     ("stk_orgn_trde_trnsn",) if key in result.payload},
                     has_next=result.has_next, next_key=result.next_key, cache_hit=result.cache_hit,
                     recording_succeeded=result.recording_succeeded)
        return result
    finally:
        value["elapsed_ns"] = time.monotonic_ns() - started
        _emit(cycle, "attempt", value)
        cycle.pop("attempt_link", None)


def broker_flow_link(api_id, path, body):
    """Small diagnostic receipt; never change native broker behavior."""
    try:
        cycle = _FLOW.get()
        link = cycle.get("attempt_link") if cycle else None
        if (link is not None and api_id == "ka10045" and path == "/api/dostk/mrkcond"
                and body == link["body"] and cycle["trace_id"] == trace.input_token("store_inputs")):
            link["receipt"]["origin"] = "network"
            return link
    except Exception:
        _emit(_FLOW.get(), "capture_error", {"reason": "broker_link_failed"})
    return None


@contextmanager
def flow_recording_scope(link, handler):
    """Carry this job's input cause to the separate native persistence task."""
    if link is None:
        yield
        return
    try:
        from .market_ingest import MarketDataIngestor
        receipt = link["receipt"]
        receipt["handler_called"] = True
        receipt["handler_supported"] = (
            isinstance(getattr(handler, "__self__", None), MarketDataIngestor)
            and getattr(handler, "__func__", None) is MarketDataIngestor.ingest)
        identity = operation_identity()
        scope = (capture_owner(identity["workload_id"], identity["producer_component"],
                               identity["actor_id"], cause_input_id=link["cycle"]["input_id"])
                 if identity["actor_id"] else nullcontext())
    except Exception:
        _emit(link.get("cycle"), "capture_error", {"reason": "broker_scope_failed"})
        yield
        return
    try:
        scope.__enter__()
    except Exception:
        _emit(link.get("cycle"), "capture_error", {"reason": "broker_scope_failed"})
        yield
        return
    try:
        yield
    except BaseException:
        receipt["handler_outcome"] = "error"
        raise
    else:
        receipt["handler_outcome"] = "returned"
    finally:
        scope.__exit__(None, None, None)


def candidate_flow_cycle(events, input_id):
    """Complete, bounded source validation before acquiring a DB lease."""
    if type(input_id) is not str or not input_id or not 1 <= len(events) <= 10_000:
        raise ValueError("recorded_flow_selection_invalid")
    if (any(type(row) is not dict or type(row.get("seq")) is not int for row in events)
            or [row.get("seq") for row in events] != list(range(1, len(events) + 1))):
        raise ValueError("recorded_flow_sequence_gap")
    linked = [row for row in events if row.get("input_id") == input_id]
    if any(row.get("event_type") == "input_rejected" for row in linked):
        raise ValueError("recorded_flow_input_rejected")
    rows = [row for row in linked if row.get("event_type") == "market_input"]
    if (len(rows) < 3 or [row.get("input_kind") for row in rows[:2]] != ["cycle_start", "marker_state"]
            or rows[-1].get("input_kind") != "cycle_end"
            or any(row.get("market_input_version") != VERSION or row.get("workload_id") != "top20_candidate_flow"
                   for row in rows)
            or any(any(type(row.get(key)) is not str or not row[key] or len(row[key]) > 160
                       for key in ("producer_component", "actor_id"))
                   or type(row.get("parent_input_id")) is not str for row in rows)
            or len({(row.get("producer_component"), row.get("actor_id"), row.get("parent_input_id"))
                    for row in rows}) != 1):
        raise ValueError("recorded_flow_cycle_incomplete")
    values = [thaw_payload(row.get("payload")) for row in rows]
    for value in values:
        if type(value) is not dict:
            raise ValueError("recorded_flow_payload_invalid")
        freeze_payload(value)
    header, state, ending = values[0], values[1], values[-1]
    if (set(header) != {"code", "day", "source_time"} or type(header["code"]) is not str
            or not re.fullmatch(r"[0-9]{6}", header["code"]) or type(header["day"]) is not str
            or type(header["source_time"]) is not datetime or header["source_time"].tzinfo is None
            or set(state) != {"exists"} or type(state["exists"]) is not bool
            or set(ending) != {"outcome", "stage", "error_type"}
            or type(ending["error_type"]) is not str
            or ending["outcome"] not in {"returned", "error"}):
        raise ValueError("recorded_flow_cycle_invalid")
    if date.fromisoformat(header["day"]).isoformat() != header["day"]:
        raise ValueError("recorded_flow_day_invalid")
    middle = list(zip(rows[2:-1], values[2:-1]))
    marker_at = None
    if middle and middle[-1][0].get("input_kind") == "marker_time":
        _, marker = middle.pop()
        marker_at = marker.get("source_time")
        if set(marker) != {"source_time"} or type(marker_at) is not datetime or marker_at.tzinfo is None:
            raise ValueError("recorded_flow_clock_invalid")
    if state["exists"]:
        if middle or marker_at is not None or ending != {"outcome": "returned", "stage": "marker_read", "error_type": ""}:
            raise ValueError("recorded_flow_skip_invalid")
    elif not 1 <= len(middle) <= 2:
        raise ValueError("recorded_flow_attempts_invalid")
    attempts = []
    day = header["day"].replace("-", "")
    for index, (row, value) in enumerate(middle):
        expected_body = {"stk_cd": header["code"] + ("_AL" if index == 0 else ""),
                         "strt_dt": day, "end_dt": day, "orgn_prsm_unp_tp": "1", "for_prsm_unp_tp": "1"}
        if (row.get("input_kind") != "attempt" or type(value.get("attempt")) is not int
                or value["attempt"] != index or value.get("body") != expected_body
                or type(value.get("elapsed_ns")) is not int or value["elapsed_ns"] < 0
                or (index and attempts[-1]["outcome"] != "error")):
            raise ValueError("recorded_flow_attempts_invalid")
        receipt = value.get("receipt")
        if (type(receipt) is not dict or set(receipt) != {"origin", "handler_called", "handler_supported", "handler_outcome", "shared"}
                or any(type(receipt[key]) is not bool for key in ("handler_called", "handler_supported", "shared"))
                or receipt["shared"] or receipt["origin"] not in {"network", "ram_cache"}):
            raise ValueError("recorded_flow_broker_receipt_unsupported")
        base_keys = {"attempt", "body", "receipt", "elapsed_ns", "outcome"}
        if value.get("outcome") == "response":
            if (type(value.get("payload")) is not dict or set(value["payload"]) - {"stk_orgn_trde_trnsn"}
                    or set(value) != base_keys | {"payload", "has_next", "next_key", "cache_hit", "recording_succeeded"}
                    or type(value.get("has_next")) is not bool or type(value.get("cache_hit")) is not bool
                    or value["cache_hit"] != (receipt["origin"] == "ram_cache")
                    or type(value.get("next_key")) is not str or value.get("recording_succeeded") is not True
                    or (receipt["origin"] == "network" and
                        (not receipt["handler_called"] or not receipt["handler_supported"] or receipt["handler_outcome"] != "returned"))
                    or (receipt["origin"] == "ram_cache" and (receipt["handler_called"] or receipt["handler_outcome"] != "none"))):
                raise ValueError("recorded_flow_native_save_unconfirmed")
        elif value.get("outcome") == "error":
            if (receipt["handler_called"] or receipt["origin"] != "network"
                    or set(value) != base_keys | {"error_type"}
                    or type(value.get("error_type")) is not str
                    or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", value["error_type"])):
                raise ValueError("recorded_flow_request_error_invalid")
        else:
            raise ValueError("recorded_flow_attempts_invalid")
        attempts.append(value)
    if not state["exists"]:
        if ending["outcome"] == "returned":
            if not attempts or attempts[-1]["outcome"] != "response" or marker_at is None or ending["stage"] != "marker_write" or ending["error_type"]:
                raise ValueError("recorded_flow_return_invalid")
        elif (marker_at is not None or ending["stage"] not in {"request", "verify"}
              or (ending["stage"] == "request" and (len(attempts) != 2 or attempts[-1]["outcome"] != "error"))
              or (ending["stage"] == "verify" and (attempts[-1]["outcome"] != "response" or ending["error_type"] != "RuntimeError"))):
            raise ValueError("recorded_flow_native_failure_not_replayable")
    return header, state, attempts, marker_at, ending


class _RecordedFlowRequestError(RuntimeError):
    pass


async def _execute_candidate_flow(store, events, input_id):
    """Execute under the draining task owned by _replay_candidate_flow."""
    header, state, attempts, marker_at, ending = candidate_flow_cycle(events, input_id)
    from .autonomous_top20 import AutonomousTop20Service
    from .market_ingest import MarketDataIngestor
    from .rest_broker import BrokerResult
    from .postgres_access import db_call_request_id, db_call_source

    existing = await asyncio.to_thread(store.load_documents, "candidate_flow_capture", f"{header['day']}:{header['code']}", 1)
    if bool(existing) != state["exists"]:
        raise ValueError("recorded_flow_baseline_marker_mismatch")
    for value in attempts:
        if value["outcome"] == "response" and value["receipt"]["origin"] == "ram_cache":
            snapshots = await asyncio.to_thread(store.load_dataset_snapshots, "investor_flow", header["code"], 100)
            raw_code = value["body"]["stk_cd"]
            market = "SOR" if raw_code.endswith("_AL") else "KRX"
            key = header["day"].replace("-", "") + ":" + market
            payload = {"market": market, "rows": value["payload"].get("stk_orgn_trde_trnsn", [])}
            if not any(row.get("snapshot_key") == key and row.get("payload") == payload for row in snapshots):
                raise ValueError("recorded_flow_baseline_cache_dependency_missing")
    ingestor = MarketDataIngestor(store)

    class Broker:
        used = 0
        native_error = None
        async def request(self, api_id, path, body):
            if self.used >= len(attempts):
                raise ValueError("recorded_flow_response_missing")
            value = attempts[self.used]
            if (api_id, path, body) != ("ka10045", "/api/dostk/mrkcond", value["body"]):
                raise ValueError("recorded_flow_request_mismatch")
            self.used += 1
            if value["outcome"] == "error":
                raise _RecordedFlowRequestError(value["error_type"])
            if value["receipt"]["handler_called"]:
                try:
                    await asyncio.to_thread(ingestor.ingest, api_id, body, value["payload"])
                except Exception as error:
                    # The production broker returns failed confirmation, not a
                    # transport exception which would request the fallback venue.
                    self.native_error = error
                    return BrokerResult(value["payload"], value["has_next"], value["next_key"],
                                        value["cache_hit"], False)
            return BrokerResult(value["payload"], value["has_next"], value["next_key"],
                                value["cache_hit"], True)

    broker = Broker()
    service = AutonomousTop20Service(broker, None, store, now_provider=lambda: marker_at or header["source_time"])
    request_id = "flow-replay-" + uuid4().hex
    started = time.monotonic_ns()
    async def run():
        # No recorder controls are changed. The offline worker is not a new source input.
        stage = {"value": "marker_read"}
        marker = _REPLAY_STAGE.set(stage)
        capture_marker = _FLOW.set(None)
        with db_call_request_id(request_id), db_call_source("replay.top20_candidate_flow"):
            try:
                await AutonomousTop20Service._capture_candidate_investor_flow.__wrapped__(service, header["code"], header["day"])
            except _RecordedFlowRequestError:
                if ending["outcome"] != "error" or ending["stage"] != "request":
                    raise
                return "error"
            except RuntimeError:
                if broker.native_error is not None:
                    raise broker.native_error
                if ending["outcome"] != "error" or ending["stage"] != "verify" or stage["value"] != "verify":
                    raise
                return "error"
            finally:
                _REPLAY_STAGE.reset(marker)
                _FLOW.reset(capture_marker)
        return "returned"
    outcome = await run()
    if outcome != ending["outcome"] or broker.used != len(attempts):
        raise ValueError("recorded_flow_outcome_or_attempt_count_mismatch")
    descendants = [row for row in events if row.get("event_type") == "operation_start"
                   and row.get("cause_input_id") == input_id]
    return {"source_input_id": input_id, "replay_request_id": request_id,
            "scope": "candidate_flow_consumer_and_ingest", "outcome": outcome,
            "attempts": broker.used, "elapsed_ms": (time.monotonic_ns() - started) / 1_000_000,
            "historical_descendant_operations_not_executed": [row["operation_id"] for row in descendants],
            "source_call_ids": sorted({row["call_id"] for row in events if row.get("event_type") in {"call_start", "call_end"}
                and row.get("input_operation_id") in {value["operation_id"] for value in descendants} and row.get("call_id")}),
            "timing_preserved": False, "source_state_equivalent": False,
            "broker_queue_and_cache_scheduling_replayed": False, "whole_top20_replay": False}


async def _replay_candidate_flow(store, events, input_id):
    """Drain baseline reads as well as writes before the owner can restore."""
    candidate_flow_cycle(events, input_id)
    task = asyncio.create_task(_execute_candidate_flow(store, events, input_id))
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


def run_owned_candidate_flow_experiment(database_url, owner_token, baseline_id, events, input_id, *, config=None):
    """Restore/run/drain/restore only the existing sealed, dedicated replay DB."""
    candidate_flow_cycle(events, input_id)
    from .diagnostic_replay_baseline import ReplayDatabaseLease
    with ReplayDatabaseLease(database_url, owner_token, config=config) as lease:
        baseline = lease.restore(baseline_id)
        try:
            result = asyncio.run(_replay_candidate_flow(lease.store(), events, input_id))
            if lease.status()["owned_connections"]:
                raise RuntimeError("recorded_flow_connections_not_drained")
        finally:
            cleanup = lease.restore(baseline_id)
        return {**result, "baseline": baseline, "baseline_managed": True,
                "database_ownership_verified": True, "cleanup": {"baseline_restored": True, **cleanup}}
