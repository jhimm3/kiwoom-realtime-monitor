"""Opt-in market request/catalog tape. Native broker policy remains the owner.

This closes input pairs only; it does not authorize a TOP20 lifecycle runner.
Replay lanes are explicitly bound to recorded actors, never guessed from task IDs.
"""
from __future__ import annotations

import asyncio
import copy
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
import json
from threading import Lock
import time
from uuid import uuid4

from . import diagnostic_trace as trace
from .diagnostic_replay_contract import capture_owner, freeze_payload, operation_identity, thaw_payload

VERSION = "market-request-tape/v1"
EVENT = "rest_input"
MARKET_APIS = frozenset({"ka00198", "ka10016", "ka10001", "ka10100", "ka10080",
    "ka10081", "ka10083", "ka10094", "ka10045", "ka90008", "ka20005", "ka20006"})
MAX_LANES = 32768
MAX_LANE_BYTES = 4 * 1024 * 1024
MAX_SHARED_REQUESTS = 4096
MAX_EVENTS = 50000
MAX_TAPE_BYTES = 32 * 1024 * 1024
MAX_DELAY_NS = 7200 * 1_000_000_000
_ERRORS = {"TimeoutError": TimeoutError, "OSError": OSError, "ValueError": ValueError,
           "RuntimeError": RuntimeError}
_REQUEST = ContextVar("recorded_market_request", default=None)
_TRANSPORT = ContextVar("recorded_market_transport", default=None)
_TAPE_LANE = ContextVar("recorded_market_tape_lane", default=None)
_TASK_BINDINGS = ContextVar("recorded_market_task_bindings", default=None)


@contextmanager
def replay_task_input_context(name):
    """Bind at task creation, never by when concurrent requests happen to arrive."""
    bindings = _TASK_BINDINGS.get()
    if bindings is None:
        yield
        return
    lane = bindings.child(name)
    marker = _TAPE_LANE.set(lane)
    try:
        yield
    finally:
        _TAPE_LANE.reset(marker)


def _emit(receipt, stage, value):
    if receipt is None or receipt.get("trace_id") is None:
        return
    fields = {key: receipt[key] for key in ("input_id", "workload_id", "producer_component", "actor_id")}
    fields.update(input_version=VERSION, input_kind=stage)
    try:
        trace.emit_payload(receipt["trace_id"], EVENT, fields, value)
    except Exception:
        try:
            trace.reject_input(receipt["trace_id"], {**fields, "reason": "market_request_observer_error"})
        except Exception:
            pass


def _new(owner, *, token, kind):
    identity = operation_identity()
    task = asyncio.current_task()
    producer = identity["producer_component"] or f"rest_broker:{id(owner):x}"
    actor = identity["actor_id"] or f"{producer}:{id(task):x}"
    state = getattr(owner, "_market_input_sequences", None)
    if state is None or state[0] != token:
        state = [token, {}, 0]
        owner._market_input_sequences = state
    lanes = state[1]
    # Retain the exact canonical signature compactly. str(kind) expands tuple
    # quoting/escapes and a four-byte character charge overstates ASCII request
    # keys. UTF-8 is reversible; per-signature ordinals and the recorded/replay
    # request identity stay unchanged. The allowance covers key/count/map
    # overhead, while actor strings keep their conservative Unicode allowance.
    if type(kind) is tuple and kind[0] == "request":
        signature = kind[1].encode("utf-8")
        kind = ("request", signature)
        charge = 512 + 4 * len(actor) + len(signature)
    else:
        charge = 512 + 4 * (len(actor) + len(str(kind)))
    lane = (actor, kind)
    if lane not in lanes and (len(lanes) >= MAX_LANES or state[2] + charge > MAX_LANE_BYTES):
        trace.reject_input(token, {"workload_id": identity["workload_id"] or "rest_market",
                                  "reason": "market_request_lane_limit"})
        return None
    if lane not in lanes:
        state[2] += charge
    lanes[lane] = lanes.get(lane, 0) + 1
    return {"trace_id": token, "input_id": uuid4().hex,
            "workload_id": identity["workload_id"] or "rest_market",
            "producer_component": producer, "actor_id": actor,
            "lane": actor, "ordinal": lanes[lane], "parent_input_id": identity["cause_input_id"],
            "origin": "unresolved", "group_id": ""}


def _request_key(namespace, api_id, path, body, cont_yn, next_key, record_response):
    return {"namespace": namespace, "api_id": api_id, "path": path, "body": body,
            "cont_yn": cont_yn, "next_key": next_key, "record_response": record_response}


def _signature(key):
    # Request metadata is small; bound it before canonical encoding. Response
    # serialization still belongs exclusively to the existing trace worker.
    freeze_payload(key, maximum_bytes=65536)
    return json.dumps(key, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def captured_broker_request(function):
    @wraps(function)
    async def run(self, api_id, path, body, *, cont_yn, next_key, record_response):
        token = trace.input_token("top20_inputs")
        lane = _TAPE_LANE.get()
        if self._namespace != "market" or api_id not in MARKET_APIS or (token is None and lane is None):
            return await function(self, api_id, path, body, cont_yn=cont_yn,
                                  next_key=next_key, record_response=record_response)
        key = _request_key(self._namespace, api_id, path, body, cont_yn, next_key, record_response)
        receipt = None
        if token:
            try:
                receipt = _new(self, token=token, kind=("request", _signature(key)))
            except Exception:
                try:
                    trace.reject_input(token, {"workload_id": "rest_market", "reason": "market_request_identity_rejected"})
                except Exception:
                    pass
        if receipt is not None:
            from .rest_broker import _persistent_cache_ttl
            receipt["request"] = key
            _emit(receipt, "logical_start", {**key, "lane": receipt["lane"],
                "ordinal": receipt["ordinal"], "parent_input_id": receipt["parent_input_id"],
                "handler_present": self._response_handler is not None,
                "cache_lookup": bool(record_response and self._store is not None and _persistent_cache_ttl(api_id, cont_yn))})
        elif lane is not None:
            receipt = lane.begin(key)
        marker = _REQUEST.set(receipt)
        scope = (capture_owner(receipt["workload_id"], receipt["producer_component"],
            receipt["actor_id"], cause_input_id=receipt["input_id"])
            if receipt is not None and token else nullcontext())
        outcome, error_type, result_state = "returned", "", {}
        started = time.monotonic_ns()
        try:
            with scope:
                result = await function(self, api_id, path, body, cont_yn=cont_yn,
                                        next_key=next_key, record_response=record_response)
            result_state = {"cache_hit": result.cache_hit, "recording_succeeded": result.recording_succeeded}
            return result
        except BaseException as error:
            outcome = "cancelled" if isinstance(error, asyncio.CancelledError) else "error"
            error_type = type(error).__name__
            raise
        finally:
            if receipt is not None:
                _emit(receipt, "logical_end", {"origin": receipt["origin"],
                    "group_id": receipt["group_id"], "outcome": outcome, "error_type": error_type,
                    "elapsed_ns": time.monotonic_ns() - started, "result_state": result_state})
            _REQUEST.reset(marker)
    return run


@dataclass
class RequestGroup:
    primary: dict
    members: list = field(default_factory=list)
    lock: Lock = field(default_factory=Lock)

    def parents(self):
        with self.lock:
            return [row["input_id"] for row in self.members]


def link_request(future, *, shared=False):
    receipt = _REQUEST.get()
    group = getattr(future, "_market_input_group", None)
    if receipt is None:
        if group is not None:
            _emit(group.primary, "unobserved_shared", {})
        return group
    if group is None:
        if shared:
            receipt["origin"] = "shared_unobserved"
            return None
        group = RequestGroup(receipt)
        future._market_input_group = group
    if receipt["trace_id"] != group.primary["trace_id"]:
        receipt["origin"] = "shared_other_epoch"
        return group
    receipt["group_id"] = group.primary["input_id"]
    receipt["origin"] = "shared_inflight" if shared else "network"
    with group.lock:
        full = len(group.members) >= MAX_SHARED_REQUESTS
        if not full:
            group.members.append(receipt)
    if full:
        receipt["origin"] = "shared_unobserved"
        _emit(group.primary, "unobserved_shared", {})
    return group


def request_origin(value):
    receipt = _REQUEST.get()
    if receipt is not None:
        receipt["origin"] = value


def transport_call(client, job):
    group = job.input_group
    if group is None:
        return client.request_with_continuation(job.api_id, job.path, job.body,
                                               cont_yn=job.cont_yn, next_key=job.next_key)
    primary = group.primary
    _emit(primary, "transport_start", {"parent_request_ids": group.parents()})
    marker = _TRANSPORT.set(primary)
    started = time.monotonic_ns()
    try:
        result = client.request_with_continuation(job.api_id, job.path, job.body,
                                                  cont_yn=job.cont_yn, next_key=job.next_key)
    except BaseException as error:
        _emit(primary, "transport_end", {"outcome": "error", "error_type": type(error).__name__,
            "elapsed_ns": time.monotonic_ns() - started, "parent_request_ids": group.parents()})
        raise
    else:
        _emit(primary, "transport_end", {"outcome": "returned", "result": result,
            "elapsed_ns": time.monotonic_ns() - started, "parent_request_ids": group.parents()})
        return result
    finally:
        _TRANSPORT.reset(marker)


@contextmanager
def recording_scope(group, phase):
    if group is None:
        yield
        return
    primary = group.primary
    _emit(primary, "effect_start", {"phase": phase, "parent_request_ids": group.parents()})
    task = asyncio.current_task()
    scope = (capture_owner(primary["workload_id"], primary["producer_component"],
        f"broker-effect:{id(task):x}", cause_input_id=primary["input_id"])
        if primary["trace_id"] else nullcontext())
    outcome = "returned"
    try:
        with scope:
            yield
    except BaseException:
        outcome = "error"
        raise
    finally:
        _emit(primary, "effect_end", {"phase": phase, "outcome": outcome,
                                      "parent_request_ids": group.parents()})


def captured_catalog_loader(service):
    """Prepare identity on the event-loop thread; invoke the original loader once."""
    token = trace.input_token("top20_inputs")
    if token is None:
        return service._catalog_loader
    receipt = _new(service, token=token, kind="catalog")
    if receipt is None:
        return service._catalog_loader
    _emit(receipt, "catalog_start", {"lane": receipt["lane"], "ordinal": receipt["ordinal"],
        "source_time": service._now(), "parent_input_id": receipt["parent_input_id"]})
    def load():
        started = time.monotonic_ns()
        try:
            rows = service._catalog_loader()
        except BaseException as error:
            _emit(receipt, "catalog_end", {"outcome": "error", "error_type": type(error).__name__,
                                           "elapsed_ns": time.monotonic_ns() - started})
            raise
        else:
            _emit(receipt, "catalog_end", {"outcome": "returned", "rows": rows,
                                           "elapsed_ns": time.monotonic_ns() - started})
            return rows
    load._market_catalog_receipt = receipt
    return load


@contextmanager
def catalog_recording_scope(loader):
    receipt = getattr(loader, "_market_catalog_receipt", None)
    if receipt is None:
        yield
        return
    with capture_owner(receipt["workload_id"], receipt["producer_component"], receipt["actor_id"],
                       cause_input_id=receipt["input_id"]):
        yield


def read_request_tape(events):
    """Validate a complete selected event set before DB access. Hashes are reader-owned upstream."""
    if not 1 <= len(events) <= MAX_EVENTS:
        raise ValueError("market_tape_event_limit")
    groups = {}
    previous = charge = 0
    for row in events:
        if type(row) is not dict or type(row.get("seq")) is not int or row["seq"] <= previous:
            raise ValueError("market_tape_sequence_invalid")
        previous = row["seq"]
        if row.get("event_type") == "input_rejected":
            raise ValueError("market_tape_rejected_input")
        if row.get("event_type") != EVENT:
            continue
        if row.get("input_version") != VERSION or type(row.get("input_id")) is not str or not row["input_id"]:
            raise ValueError("market_tape_version_or_identity_invalid")
        charge += freeze_payload(row.get("payload"), maximum_bytes=MAX_TAPE_BYTES - charge).charge
        groups.setdefault(row["input_id"], []).append((row, thaw_payload(row.get("payload"))))
    if not groups:
        raise ValueError("market_tape_empty")
    calls, catalogs = {}, {}
    for input_id, pairs in groups.items():
        rows, values = zip(*pairs)
        if (any(type(row.get(key)) is not str or not row[key] or len(row[key]) > 160
                for row in rows for key in ("workload_id", "producer_component", "actor_id"))
                or len({(row.get("workload_id"), row.get("producer_component"), row.get("actor_id")) for row in rows}) != 1):
            raise ValueError("market_tape_owner_changed")
        if any(type(value) is not dict for value in values):
            raise ValueError("market_tape_payload_invalid")
        stages = [row.get("input_kind") for row in rows]
        if stages[0] == "catalog_start":
            if stages != ["catalog_start", "catalog_end"]:
                raise ValueError("market_tape_catalog_incomplete")
            rows_out = values[-1].get("rows", ())
            if (type(values[0].get("lane")) is not str or not values[0]["lane"]
                    or type(values[0].get("ordinal")) is not int or values[0]["ordinal"] < 1
                    or values[-1].get("outcome") not in {"returned", "error"}
                    or type(values[-1].get("elapsed_ns")) is not int or not 0 <= values[-1]["elapsed_ns"] <= MAX_DELAY_NS
                    or (values[-1]["outcome"] == "error" and values[-1].get("error_type") not in _ERRORS)):
                raise ValueError("market_tape_catalog_invalid")
            if values[-1].get("outcome") == "returned" and (type(rows_out) not in (tuple, list)
                    or any(type(item) not in (tuple, list) or len(item) != 3
                           or any(type(part) is not str for part in item) for item in rows_out)):
                raise ValueError("market_tape_catalog_rows_invalid")
            catalogs[input_id] = {"start": values[0], "end": values[-1]}
            continue
        if stages[0] != "logical_start" or stages.count("logical_start") != 1 or stages.count("logical_end") != 1:
            raise ValueError("market_tape_request_incomplete")
        start, end = values[0], values[stages.index("logical_end")]
        api_id = start.get("api_id")
        path = "/api/dostk/chart" if api_id in {"ka10080", "ka10081", "ka10083", "ka10094", "ka20005", "ka20006"} else "/api/dostk/mrkcond" if api_id in {"ka10045", "ka90008"} else "/api/dostk/stkinfo"
        if (start.get("namespace") != "market" or start.get("api_id") not in MARKET_APIS
                or start.get("path") != path or type(start.get("next_key")) is not str
                or (start.get("cont_yn") == "Y" and not start.get("next_key"))
                or type(start.get("body")) is not dict or start.get("cont_yn") not in {"N", "Y"}
                or type(start.get("record_response")) is not bool or type(start.get("lane")) is not str or not start["lane"]
                or type(start.get("handler_present")) is not bool or type(start.get("cache_lookup")) is not bool
                or type(start.get("ordinal")) is not int or start["ordinal"] < 1
                or end.get("outcome") not in {"returned", "error"}
                or type(end.get("elapsed_ns")) is not int or end["elapsed_ns"] < 0):
            raise ValueError("market_tape_request_invalid")
        transports = [(stage, value) for stage, value in zip(stages, values) if stage.startswith("transport_")]
        if transports and [stage for stage, _ in transports] != ["transport_start", "transport_end"]:
            raise ValueError("market_tape_transport_incomplete")
        effects = {}
        for stage, value in zip(stages[1:], values[1:]):
            if stage == "unobserved_shared":
                raise ValueError("market_tape_unobserved_shared_request")
            if stage.startswith("effect_"):
                effects.setdefault(value.get("phase"), []).append((stage, value))
            elif stage not in {"logical_end", "transport_start", "transport_end"}:
                raise ValueError("market_tape_stage_invalid")
        for phase, effect in effects.items():
            if ([stage for stage, _ in effect] != ["effect_start", "effect_end"]
                    or phase not in {"cache_read", "cache_ingest", "ingest", "cache_write"}
                    or effect[-1][1].get("outcome") not in {"returned", "error"}):
                raise ValueError("market_tape_effect_incomplete")
        calls[input_id] = {"start": start, "end": end, "transport": transports,
                           "effects": effects, "owner": rows[0]["producer_component"]}
    identities = set()
    ordinals = {}
    for input_id, call in calls.items():
        request = {name: call["start"][name] for name in (
            "namespace", "api_id", "path", "body", "cont_yn", "next_key", "record_response")}
        key = (call["start"]["lane"], _signature(request), call["start"]["ordinal"])
        if key in identities:
            raise ValueError("market_tape_ambiguous_lane")
        identities.add(key)
        ordinals.setdefault(("request", *key[:2]), []).append(key[2])
        end = call["end"]
        if end["origin"] in {"shared_unobserved", "shared_other_epoch", "unresolved"}:
            raise ValueError("market_tape_request_origin_missing")
        group_id = end.get("group_id")
        if end["origin"] not in {"network", "shared_inflight", "persistent_cache", "ram_cache", "ram_cache_after_lookup"}:
            raise ValueError("market_tape_request_origin_invalid")
        if end["origin"] in {"network", "shared_inflight", "persistent_cache", "ram_cache_after_lookup"} and not group_id:
            raise ValueError("market_tape_request_group_missing")
        if group_id:
            primary = calls.get(group_id)
            if primary is None:
                raise ValueError("market_tape_shared_primary_missing")
            if primary["end"].get("group_id") != group_id:
                raise ValueError("market_tape_shared_primary_invalid")
            if end["origin"] == "shared_inflight" and any(call["start"].get(name) != primary["start"].get(name)
                    for name in request):
                raise ValueError("market_tape_shared_identity_changed")
            if end["origin"] == "shared_inflight" and call["transport"]:
                raise ValueError("market_tape_shared_transport_invalid")
            for stage, value in primary["transport"]:
                parents = value.get("parent_request_ids", [])
                if (type(parents) is not list or group_id not in parents or len(parents) != len(set(parents))
                        or any(parent not in calls or calls[parent]["end"].get("group_id") != group_id for parent in parents)):
                    raise ValueError("market_tape_parent_missing")
            for effect in primary["effects"].values():
                parents = effect[0][1].get("parent_request_ids")
                if type(parents) is not list or group_id not in parents or any(parent not in calls for parent in parents):
                    raise ValueError("market_tape_parent_missing")
                if effect[0][1].get("parent_request_ids") != effect[1][1].get("parent_request_ids"):
                    raise ValueError("market_tape_shared_effect_changed")
        if call["transport"]:
            value = call["transport"][-1][1]
            if value.get("outcome") not in {"returned", "error"} or type(value.get("elapsed_ns")) is not int or not 0 <= value["elapsed_ns"] <= MAX_DELAY_NS:
                raise ValueError("market_tape_transport_result_invalid")
            if value["outcome"] == "error" and value.get("error_type") not in _ERRORS:
                raise ValueError("market_tape_error_type_unsupported")
            result = value.get("result")
            if value["outcome"] == "returned" and (type(result) not in (list, tuple) or len(result) != 3
                    or type(result[0]) is not dict or type(result[1]) is not bool or type(result[2]) is not str):
                raise ValueError("market_tape_transport_result_invalid")
        if end["origin"] == "network" and not call["transport"]:
            raise ValueError("market_tape_transport_incomplete")
        if group_id == input_id:
            expected = {"cache_read"} if call["start"]["cache_lookup"] else set()
            returned_transport = call["transport"] and call["transport"][-1][1]["outcome"] == "returned"
            if end["origin"] == "network" and returned_transport and call["start"]["record_response"]:
                if call["start"]["handler_present"]:
                    expected.add("ingest")
                if call["start"]["cache_lookup"]:
                    expected.add("cache_write")
            elif end["origin"] == "persistent_cache" and call["start"]["handler_present"]:
                expected.add("cache_ingest")
            if not expected.issubset(call["effects"]):
                raise ValueError("market_tape_effect_incomplete")
    for record in catalogs.values():
        start = record["start"]
        ordinals.setdefault(("catalog", start["lane"]), []).append(start["ordinal"])
    if any(sorted(values) != list(range(1, len(values) + 1)) for values in ordinals.values()):
        raise ValueError("market_tape_lane_sequence_gap")
    return {"version": VERSION, "calls": calls, "catalogs": catalogs,
            "top20_session_execution_ready": False}


class TapeInputUnavailable(RuntimeError):
    pass


class RequestTapeClient:
    """Explicit lane-bound transport, with a sticky failure even when a service catches it."""
    def __init__(self, tape, *, preserve_transport_delay=True):
        self.tape = tape = copy.deepcopy(tape)
        self.preserve_transport_delay = preserve_transport_delay
        self.failure = None
        self.used = set()
        self._ordinals = {}
        self._lock = Lock()
        self._calls = {(call["start"]["lane"], _signature({key: call["start"][key] for key in
            ("namespace", "api_id", "path", "body", "cont_yn", "next_key", "record_response")}),
            call["start"]["ordinal"]): (input_id, call) for input_id, call in tape["calls"].items()}
        self._catalogs = {(value["start"]["lane"], value["start"]["ordinal"]): (input_id, value)
                          for input_id, value in tape["catalogs"].items()}
        if len(self._calls) != len(tape["calls"]) or len(self._catalogs) != len(tape["catalogs"]):
            self._fail("market_tape_ambiguous_lane")

    @contextmanager
    def lane(self, source_lane):
        marker = _TAPE_LANE.set(_TapeLane(self, source_lane))
        try:
            yield
        finally:
            _TAPE_LANE.reset(marker)

    def task_bindings(self, bindings):
        """Explicit (native task name, spawn ordinal) -> recorded lane contract.

        This does not infer roles from old actor addresses or response bodies.
        A missing binding fails if that task actually requests an input. Tasks
        without external requests, including native broker workers, need none.
        """
        return TaskTapeBindings(self, bindings)

    def _fail(self, reason):
        with self._lock:
            self.failure = self.failure or reason
        raise TapeInputUnavailable(reason)

    def request_with_continuation(self, api_id, path, body, *, cont_yn="N", next_key=""):
        receipt = _TRANSPORT.get()
        if receipt is None or receipt.get("tape") is not self:
            self._fail("market_tape_unbound_transport")
        call = self.tape["calls"][receipt["input_id"]]
        if not call["transport"]:
            self._fail("market_tape_cache_hit_has_no_transport")
        if (api_id, path, body, cont_yn, next_key) != tuple(call["start"][key] for key in
                ("api_id", "path", "body", "cont_yn", "next_key")):
            self._fail("market_tape_transport_identity_changed")
        with self._lock:
            reused = receipt["input_id"] in self.used
            if not reused:
                self.used.add(receipt["input_id"])
        if reused:
            self._fail("market_tape_transport_reused")
        value = call["transport"][-1][1]
        if self.preserve_transport_delay:
            time.sleep(value["elapsed_ns"] / 1e9)
        if value["outcome"] == "error":
            error = _ERRORS.get(value.get("error_type"))
            if error is None:
                self._fail("market_tape_error_type_unsupported")
            raise error("recorded market transport failure")
        return copy.deepcopy(value["result"])

    def catalog_loader(self, source_lane):
        """A loader injection only; the native service still decides whether to call it."""
        def load():
            with self._lock:
                key = (source_lane, "catalog")
                ordinal = self._ordinals.get(key, 0) + 1
                self._ordinals[key] = ordinal
            selected = self._catalogs.get((source_lane, ordinal))
            if selected is None:
                self._fail("market_tape_catalog_input_unavailable")
            input_id, record = selected
            with self._lock:
                self.used.add(input_id)
            value = record["end"]
            if self.preserve_transport_delay:
                time.sleep(value["elapsed_ns"] / 1e9)
            if value["outcome"] == "error":
                raise _ERRORS[value["error_type"]]("recorded catalog failure")
            return copy.deepcopy(value["rows"])
        return load

    def report(self):
        ids = {input_id for input_id, value in self.tape["calls"].items() if value["transport"]}
        ids.update(self.tape["catalogs"])
        with self._lock:
            used, failure = set(self.used), self.failure
        return {"failure": failure, "used_inputs": len(used),
                "unused_external_inputs": sorted(ids - used),
                "top20_session_execution_ready": False}


class _TapeLane:
    def __init__(self, client, lane):
        self.client, self.lane = client, lane

    def begin(self, key):
        client = self.client
        counter = (self.lane, "request", _signature(key))
        with client._lock:
            ordinal = client._ordinals.get(counter, 0) + 1
            client._ordinals[counter] = ordinal
        selected = client._calls.get((self.lane, counter[2], ordinal))
        if selected is None:
            client._fail("market_tape_logical_input_unavailable")
        input_id, value = selected
        return {"trace_id": None, "input_id": input_id, "tape": client,
                "workload_id": "top20", "producer_component": value["owner"], "actor_id": self.lane,
                "origin": "unresolved", "group_id": "", "request": key}


class _UnboundTapeLane:
    def __init__(self, client):
        self.client = client

    def begin(self, key):
        self.client._fail("market_tape_task_binding_missing")


class TaskTapeBindings:
    """Run-local creation order and explicit identities for native child tasks."""
    def __init__(self, client, bindings):
        if type(bindings) is not list or not 1 <= len(bindings) <= 4096:
            raise ValueError("market_tape_task_bindings_invalid")
        self.client, self.bindings = client, copy.deepcopy(bindings)
        self._mapping, self._ordinals, self._used, self._claims = {}, {}, [], {}
        self._claim_lock = Lock()
        source_lanes = {value["start"]["lane"] for values in (client.tape["calls"], client.tape["catalogs"])
                        for value in values.values()}
        for item in self.bindings:
            if (type(item) is not dict or set(item) != {"task_name", "spawn_ordinal", "source_lane"}
                    or type(item["task_name"]) is not str or not item["task_name"] or len(item["task_name"]) > 160
                    or type(item["spawn_ordinal"]) is not int or not 1 <= item["spawn_ordinal"] <= 4096
                    or type(item["source_lane"]) is not str or item["source_lane"] not in source_lanes):
                raise ValueError("market_tape_task_bindings_invalid")
            key = item["task_name"], item["spawn_ordinal"]
            if key in self._mapping:
                raise ValueError("market_tape_task_binding_ambiguous")
            self._mapping[key] = item["source_lane"]

    @contextmanager
    def activate(self):
        if _TASK_BINDINGS.get() is not None:
            raise RuntimeError("market_tape_task_bindings_shared")
        task_token = _TASK_BINDINGS.set(self)
        # Root requests are not allowed to inherit a child's recorded lane.
        lane_token = _TAPE_LANE.set(_UnboundTapeLane(self.client))
        try:
            yield self
        finally:
            _TAPE_LANE.reset(lane_token)
            _TASK_BINDINGS.reset(task_token)

    def child(self, name):
        if type(name) is not str:
            return _UnboundTapeLane(self.client)
        ordinal = self._ordinals.get(name, 0) + 1
        self._ordinals[name] = ordinal
        lane = self._mapping.get((name, ordinal))
        if lane is None:
            return _UnboundTapeLane(self.client)
        self._used.append({"task_name": name, "spawn_ordinal": ordinal, "source_lane": lane})
        return _TaskTapeLane(self, lane, (name, ordinal))

    def claim(self, source_lane, kind, identity):
        # Native undecorated children can inherit the same source actor. Shared
        # actors are safe only for disjoint request signatures/catalog input.
        # Never assign two concurrent tasks the same response ordinal stream.
        key = source_lane, kind
        with self._claim_lock:
            ambiguous = key in self._claims and self._claims[key] != identity
            if not ambiguous:
                self._claims[key] = identity
        if ambiguous:
            self.client._fail("market_tape_shared_task_signature_ambiguous")

    def catalog_loader(self):
        # Native captured_catalog_loader selects this on the event loop; the
        # subsequently owned executor inherits its lane through copy_context.
        lane = _TAPE_LANE.get()
        if not isinstance(lane, _TapeLane) or lane.client is not self.client:
            self.client._fail("market_tape_catalog_task_binding_missing")
        if isinstance(lane, _TaskTapeLane):
            self.claim(lane.lane, "catalog", lane.identity)
        return self.client.catalog_loader(lane.lane)()

    def report(self):
        used = {(item["task_name"], item["spawn_ordinal"]) for item in self._used}
        return {"bound_tasks": copy.deepcopy(self._used), "unused_bindings": [item for item in self.bindings
                if (item["task_name"], item["spawn_ordinal"]) not in used]}


class _TaskTapeLane(_TapeLane):
    def __init__(self, bindings, lane, identity):
        super().__init__(bindings.client, lane)
        self.bindings, self.identity = bindings, identity

    def begin(self, key):
        self.bindings.claim(self.lane, ("request", _signature(key)), self.identity)
        return super().begin(key)
