"""Opt-in ranking decision inputs, distinct from collector and native DB replay.

This first boundary replays freshness/slot validation and retry decisions only.
It does not claim to restore a warm TOP20 service or run its downstream tasks.
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from functools import wraps
import time
from uuid import uuid4

from . import diagnostic_trace as trace
from .diagnostic_replay_contract import capture_owner, freeze_payload, thaw_payload
from .rest_broker import BrokerResult

VERSION = "top20-ranking-input/v1"
_CYCLE: ContextVar[dict | None] = ContextVar("top20_ranking_capture", default=None)

DELIVERY_VERSION = "top20-hub-delivery/v1"
_DELIVERY_TYPES = {"trade", "program_trade", "market_operation", "subscription_ready",
                   "connection_status", "connection_failed", "connection_opened", "codes_added"}


def _delivery_record(token, fields, stage, **extra):
    try:
        from .diagnostic_delivery_record import DeliveryIdentity, MISSING
        if isinstance(fields, DeliveryIdentity):
            trace.emit_delivery(token, fields, stage, outcome=extra.get('outcome', MISSING))
        else:
            trace.emit(token, "top20_delivery", {**fields, "stage": stage, **extra})
    except Exception:
        try:
            trace.reject_input(token, {"workload_id": "top20",
                "producer_component": fields.get("producer_component", ""),
                "reason": "top20_delivery_observer_error"})
        except Exception:
            pass


def queue_delivery(subscriber, event):
    """Attach a subscriber-owned receipt without serializing the event again."""
    if not subscriber.capture_component or event.get("type") not in _DELIVERY_TYPES:
        return event
    token = trace.input_token("top20_inputs")
    if token is None:
        return event
    from .realtime_hub import CapturedHubEvent, CapturedParserReceipt, CapturedControlReceipt
    if subscriber.capture_epoch != token:
        subscriber.capture_epoch, subscriber.capture_sequence = token, 0
    subscriber.capture_sequence += 1
    source = getattr(event, "source_receipt", None)
    valid = (isinstance(source, CapturedParserReceipt) and source.message.trace_id == token
             and source.message.complete and bool(source.message.input_ids)
             and source.kind == event.get("type"))
    fields = {"delivery_version": DELIVERY_VERSION, "trace_id": token, "workload_id": "top20",
              "producer_component": subscriber.capture_component,
              "subscriber_id": f"subscriber:{id(subscriber):x}", "delivery_id": uuid4().hex,
              "delivery_sequence": subscriber.capture_sequence, "event_kind": event.get("type"),
              "source_complete": valid}
    if valid:
        fields.update({"source_component": source.message.producer_component,
                       "message_id": source.message.message_id,
                       "parent_input_ids": source.message.input_ids, "parser_ordinal": source.ordinal})
    elif (isinstance(source, CapturedControlReceipt) and source.trace_id == token and source.complete
            and (source.kind == "market_operation" and event.get("type") == "market_operation"
                 or source.kind == "hub_event" and event.get("type") in _DELIVERY_TYPES)):
        fields.update({"source_complete": True, "source_kind": "top20_realtime",
                       "source_component": source.producer_component, "message_id": source.input_id,
                       "parent_input_ids": (source.input_id,), "parser_ordinal": 0})
    owner = source.message if valid else source if fields['source_complete'] else None
    fields = trace.delivery_identity(token, fields, subscriber, owner) or fields
    _delivery_record(token, fields, "enqueue")
    return CapturedHubEvent(event, source=source, delivery=fields)


def dropped_delivery(subscriber, event):
    if not subscriber.capture_component:
        return
    token = trace.input_token("top20_inputs")
    if token is None:
        return
    fields = getattr(event, "delivery_receipt", None)
    if isinstance(fields, Mapping) and fields.get("trace_id") == token:
        _delivery_record(token, fields, "drop")
    else:
        _delivery_record(token, {"producer_component": subscriber.capture_component,
            "delivery_version": DELIVERY_VERSION, "trace_id": token,
            "subscriber_id": f"subscriber:{id(subscriber):x}"}, "untracked_drop")


@contextmanager
def consumed_delivery(subscriber, event):
    """Transfer causal ownership at the actual dequeue, including spawned tasks.

    Aggregated writers remain service-owned; this receipt is not a claim that
    their entire history came from the last dequeued tick.
    """
    token = trace.input_token("top20_inputs") if subscriber.capture_component else None
    if token is None or event.get("type") not in _DELIVERY_TYPES:
        yield
        return
    fields = getattr(event, "delivery_receipt", None)
    if (not isinstance(fields, Mapping) or fields.get("trace_id") != token
            or fields.get("producer_component") != subscriber.capture_component
            or fields.get("subscriber_id") != f"subscriber:{id(subscriber):x}"):
        _delivery_record(token, {"producer_component": subscriber.capture_component,
            "delivery_version": DELIVERY_VERSION, "trace_id": token,
            "subscriber_id": f"subscriber:{id(subscriber):x}"}, "untracked_dequeue")
        yield
        return
    _delivery_record(token, fields, "dequeue")
    outcome = "failed"
    try:
        task = asyncio.current_task()
        with capture_owner("top20", subscriber.capture_component,
                           f"{subscriber.capture_component}:{id(task):x}",
                           cause_input_id=fields["delivery_id"] if fields["source_complete"] else ""):
            yield
        outcome = "returned"
    finally:
        _delivery_record(token, fields, "consume_end", outcome=outcome)


def compile_delivery_coverage(events, *, trace_id, component, _source_sequences_verified=False):
    """Validate queue causes only. This does not enable a whole-service runner.

    Consume the complete trace prefix: accepting a clipped list would hide an
    earlier gap, queue drop, rejected fragment or delivery waiting at the end.
    """
    from .diagnostic_replay_contract import COLLECTOR_INPUT_VERSION, validate_collector_message
    from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import (
        parse_trade_ticks, parse_program_trade_ticks,
    )
    if not isinstance(events, list) or not events or not trace_id or not component:
        raise ValueError("top20_delivery_sequence_gap")
    sequences = [row.get("seq") for row in events]
    if (any(type(seq) is not int or seq <= 0 for seq in sequences)
            or (sequences != sorted(set(sequences)) if _source_sequences_verified
                else sequences != list(range(1, len(events) + 1)))):
        raise ValueError("top20_delivery_sequence_gap")
    deliveries = [row for row in events if row.get("event_type") == "top20_delivery"
                  and row.get("producer_component") == component]
    if not deliveries:
        raise ValueError("top20_delivery_not_recorded")
    grouped = {}
    for row in deliveries:
        if (row.get("trace_id") != trace_id or row.get("delivery_version") != DELIVERY_VERSION
                or row.get("stage") not in {"enqueue", "dequeue", "consume_end"}
                or not row.get("source_complete")):
            raise ValueError("top20_delivery_incomplete_or_epoch_mismatch")
        identifier = row.get("delivery_id")
        if type(identifier) is not str or not identifier:
            raise ValueError("top20_delivery_identity_invalid")
        grouped.setdefault(identifier, []).append(row)
    inputs = {}
    messages = {}
    declarations = {}
    initial = {}
    for row in events:
        if row.get("event_type") == "collector_input":
            identifier = row.get("input_id")
            if type(identifier) is not str or not identifier or identifier in inputs:
                raise ValueError("top20_delivery_parent_invalid")
            inputs[identifier] = row
            if row.get("input_kind") == "initial_state":
                initial[row.get("producer_component")] = row["seq"]
            elif row.get("input_kind") == "message":
                value = thaw_payload(row.get("payload"))
                if type(value) is not dict or type(value.get("message_id")) is not str:
                    raise ValueError("top20_delivery_message_invalid")
                messages.setdefault(value["message_id"], []).append((row, value))
        elif row.get("event_type") == "collector_message_receipt":
            identifier = row.get("message_id")
            if identifier in declarations:
                raise ValueError("top20_delivery_message_duplicate")
            declarations[identifier] = row
    source_components, parents, observed_positions, subscriber_sequences = set(), set(), set(), {}
    verified_messages, parsed_messages = {}, {}
    control_tape = None
    for identifier, rows in grouped.items():
        if [row.get("stage") for row in rows] != ["enqueue", "dequeue", "consume_end"]:
            raise ValueError("top20_delivery_pair_incomplete")
        first = rows[0]
        keys = ("subscriber_id", "delivery_sequence", "event_kind", "source_component", "source_kind",
                "message_id", "parent_input_ids", "parser_ordinal")
        if (any(any(row.get(key) != first.get(key) for key in keys) for row in rows[1:])
                or rows[-1].get("outcome") != "returned"):
            raise ValueError("top20_delivery_pair_invalid")
        sequence, subscriber = first.get("delivery_sequence"), first.get("subscriber_id")
        if type(sequence) is not int or sequence <= 0 or type(subscriber) is not str or not subscriber:
            raise ValueError("top20_delivery_identity_invalid")
        subscriber_sequences.setdefault(subscriber, []).append(sequence)
        kind = first.get("event_kind")
        if first.get("source_kind") == "top20_realtime":
            from .diagnostic_top20_lifecycle_input import read_realtime_tape, delivery_source
            if control_tape is None:
                control_tape = read_realtime_tape(events)
                if control_tape["trace_id"] != trace_id:
                    raise ValueError("top20_delivery_incomplete_or_epoch_mismatch")
            parent = delivery_source(control_tape, first)
            position = (subscriber, parent, kind, 0)
            if position in observed_positions:
                raise ValueError("top20_delivery_duplicate_parser_position")
            observed_positions.add(position)
            parents.add(parent)
            source_components.add(first["source_component"])
            continue
        if kind not in {"trade", "program_trade"}:
            raise ValueError("top20_delivery_source_unsupported")
        message_id = first.get("message_id")
        declaration = declarations.get(message_id)
        fragments = messages.get(message_id, [])
        references = first.get("parent_input_ids")
        source = first.get("source_component")
        if (not declaration or declaration.get("complete") is not True or not fragments
                or declaration.get("producer_component") != source
                or not isinstance(references, (tuple, list))
                or tuple(references) != tuple(declaration.get("parent_input_ids", ()))
                or tuple(references) != tuple(row["input_id"] for row, _ in fragments)
                or any(row.get("producer_component") != source for row, _ in fragments)
                or source not in initial or initial[source] >= fragments[0][0]["seq"]
                or declaration["seq"] >= first["seq"]):
            raise ValueError("top20_delivery_parent_missing_or_shared")
        source_components.add(source)
        if message_id not in verified_messages:
            data = []
            for row, value in fragments:
                if (row.get("collector_input_version") != COLLECTOR_INPUT_VERSION
                        or value.get("row_offset") != len(data) or row["seq"] >= declaration["seq"]):
                    raise ValueError("top20_delivery_fragment_incomplete")
                message = value.get("message")
                validate_collector_message(message, version=COLLECTOR_INPUT_VERSION, allow_empty=True)
                data.extend(message["data"])
            verified_messages[message_id] = {"trnm": "REAL", "data": data}
        ordinal = first.get("parser_ordinal")
        parser = parse_trade_ticks if kind == "trade" else parse_program_trade_ticks
        if (message_id, kind) not in parsed_messages:
            parsed_messages[message_id, kind] = parser(verified_messages[message_id])
        parsed = parsed_messages[message_id, kind]
        if type(ordinal) is not int or not 0 <= ordinal < len(parsed):
            raise ValueError("top20_delivery_parser_position_invalid")
        position = (subscriber, message_id, kind, ordinal)
        if position in observed_positions:
            raise ValueError("top20_delivery_duplicate_parser_position")
        observed_positions.add(position)
        parents.update(references)
    if any(sorted(values) != list(range(1, len(values) + 1)) for values in subscriber_sequences.values()):
        raise ValueError("top20_delivery_subscriber_sequence_gap")
    if any(row.get("event_type") == "input_rejected"
           and row.get("producer_component") in source_components | {component} for row in events):
        raise ValueError("top20_delivery_input_rejected")
    return {"scope": "top20_hub_delivery_coverage", "component": component, "trace_id": trace_id,
            "delivery_ids": tuple(grouped), "parent_input_ids": tuple(sorted(parents)),
            "message_count": len(verified_messages), "subscriber_count": len(subscriber_sequences),
            "top20_session_execution_ready": False, "source_state_equivalent": False}


def _emit(cycle, kind, value):
    """Observer failure must never change a native request/save outcome."""
    if cycle is None:
        return
    fields = {**cycle, "input_kind": kind, "market_input_version": VERSION}
    try:
        trace.emit_payload(cycle["trace_id"], "market_input", fields, value)
    except Exception:
        try:
            trace.reject_input(cycle["trace_id"], {**fields, "reason": "ranking_capture_error"})
        except Exception:
            pass


def captured_ranking_refresh(function):
    @wraps(function)
    async def run(self, observed_at=None):
        token = trace.input_token("top20_inputs")
        if token is None:
            return await function(self, observed_at)
        now = observed_at or self._now()
        task = asyncio.current_task()
        cycle = {"trace_id": token, "input_id": uuid4().hex, "workload_id": "top20",
                 "producer_component": f"autonomous_top20:{id(self):x}",
                 "actor_id": f"autonomous_top20:{id(self):x}:{id(task):x}"}
        marker = _CYCLE.set(cycle)
        _emit(cycle, "cycle_start", {"source_time": now,
                                     "retry_limit": self.STALE_RANKING_RETRY_LIMIT,
                                     "background_started": bool(self._tasks)})
        outcome = "error"
        try:
            with capture_owner("top20", cycle["producer_component"], cycle["actor_id"],
                               cause_input_id=cycle["input_id"]):
                result = await function(self, now)
            outcome = "returned"
            return result
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        finally:
            _emit(cycle, "cycle_end", {"outcome": outcome})
            _CYCLE.reset(marker)
    return run


async def ranking_request(request, attempt):
    """Capture the logical broker outcome at the consumer, including cache hits.

    No HTTP headers, raw bytes, error messages or authentication are recorded.
    Off mode takes the original broker path without copying its response.
    """
    cycle = _CYCLE.get()
    if cycle is None:
        return await request("ka00198", "/api/dostk/stkinfo", {"qry_tp": "5"})
    started = time.monotonic_ns()
    try:
        result = await request("ka00198", "/api/dostk/stkinfo", {"qry_tp": "5"})
    except BaseException as error:
        if isinstance(error, (Exception, asyncio.CancelledError)):
            _emit(cycle, "attempt", {"attempt": attempt, "outcome": "error",
                  "error_type": type(error).__name__, "elapsed_ns": time.monotonic_ns() - started})
        raise
    if cycle is not None:
        # These are exactly the response values consumed by ranking selection.
        payload = result.payload
        _emit(cycle, "attempt", {"attempt": attempt, "outcome": "response",
              "payload": {key: payload[key] for key in
                          ("item_inq_rank", "result_list", "base_date", "base_time") if key in payload},
              "has_next": result.has_next, "next_key": result.next_key,
              "cache_hit": result.cache_hit, "elapsed_ns": time.monotonic_ns() - started})
    return result


def ranking_cycle(events, input_id):
    """Strict bounded preflight, without constructing a broker or touching DB."""
    if type(input_id) is not str or not input_id or len(events) > 10_000:
        raise ValueError("recorded_ranking_selection_invalid")
    sequences = [row.get("seq") for row in events]
    if (any(type(seq) is not int or seq <= 0 for seq in sequences)
            or sequences != list(range(1, len(events) + 1))):
        raise ValueError("recorded_ranking_sequence_gap")
    rows = [row for row in events if row.get("input_id") == input_id]
    if any(row.get("event_type") == "input_rejected" for row in rows):
        raise ValueError("recorded_ranking_input_rejected")
    rows = [row for row in rows if row.get("event_type") == "market_input"]
    if (len(rows) < 3 or rows[0].get("input_kind") != "cycle_start"
            or rows[-1].get("input_kind") != "cycle_end"
            or any(row.get("market_input_version") != VERSION for row in rows)
            or any(row.get("workload_id") != "top20" for row in rows)
            or len({(row.get("producer_component"), row.get("actor_id")) for row in rows}) != 1):
        raise ValueError("recorded_ranking_cycle_incomplete")
    values = [thaw_payload(row.get("payload")) for row in rows]
    header, ending = values[0], values[-1]
    if (type(header) is not dict or type(header.get("source_time")) is not datetime
            or type(header.get("retry_limit")) is not int or not 0 <= header["retry_limit"] <= 20
            or type(header.get("background_started")) is not bool
            or type(ending) is not dict or ending.get("outcome") not in {"returned", "error"}):
        raise ValueError("recorded_ranking_cycle_invalid")
    attempts = values[1:-1]
    if not 1 <= len(attempts) <= header["retry_limit"] + 1:
        raise ValueError("recorded_ranking_attempts_invalid")
    for index, (row, value) in enumerate(zip(rows[1:-1], attempts)):
        if (row.get("input_kind") != "attempt" or type(value) is not dict
                or value.get("attempt") != index or type(value.get("elapsed_ns")) is not int
                or value["elapsed_ns"] < 0):
            raise ValueError("recorded_ranking_attempts_invalid")
        if value.get("outcome") == "response":
            if (type(value.get("payload")) is not dict
                    or set(value["payload"]) - {"item_inq_rank", "result_list", "base_date", "base_time"}
                    or type(value.get("has_next")) is not bool
                    or type(value.get("cache_hit")) is not bool or type(value.get("next_key")) is not str):
                raise ValueError("recorded_ranking_response_invalid")
        elif value.get("outcome") == "error":
            if (index != len(attempts) - 1 or type(value.get("error_type")) is not str
                    or not value["error_type"] or ending["outcome"] != "error"):
                raise ValueError("recorded_ranking_error_invalid")
        else:
            raise ValueError("recorded_ranking_attempts_invalid")
        freeze_payload(value)  # Reject secret fields and malformed/unbounded decoded inputs too.
    return header, attempts, ending


class _RecordedRankingBroker:
    """No network client exists in this adapter; unsupported requests fail."""
    def __init__(self, attempts):
        self.attempts = attempts
        self.used = 0

    async def request(self, api_id, path, body):
        if (api_id, path, body) != ("ka00198", "/api/dostk/stkinfo", {"qry_tp": "5"}):
            raise ValueError("recorded_ranking_request_unsupported")
        if self.used >= len(self.attempts):
            raise ValueError("recorded_ranking_response_missing")
        value = self.attempts[self.used]
        self.used += 1
        if value["outcome"] == "error":
            raise RecordedRankingRequestError(value["error_type"])
        return BrokerResult(value["payload"], value["has_next"], value["next_key"],
                            cache_hit=value["cache_hit"])


class RecordedRankingRequestError(RuntimeError):
    """Captured transport failure, without importing arbitrary exception classes."""


async def replay_ranking_decisions(events, input_id):
    """Run the production retry/validation function only, with no downstream I/O.

    This result is intentionally not an app load experiment or DB performance run.
    The historical descendant operations are listed, never executed or seeded.
    """
    header, attempts, ending = ranking_cycle(events, input_id)
    from .autonomous_top20 import AutonomousTop20Service
    # Construction starts no timers/tasks. Only the selector below is called;
    # no store, hub, catalog or network client is attached to this instance.
    service = AutonomousTop20Service(_RecordedRankingBroker(attempts), None, None,
                                    now_provider=lambda: header["source_time"])
    service.STALE_RANKING_RETRY_LIMIT = header["retry_limit"]
    error_type = None
    try:
        _, items, stale, partial = await service._select_ranking_response(header["source_time"])
        selection = {"items": items, "stale": stale, "partial": partial,
                     "accepted": not stale and not partial}
    except RecordedRankingRequestError as error:
        error_type = str(error)
        selection = None
    if service._broker.used != len(attempts):
        raise ValueError("recorded_ranking_response_unused")
    return {"input_id": input_id, "scope": "ranking_validation_only", "selection": selection,
            "source_cycle_outcome": ending["outcome"],
            "background_tasks_were_active": header["background_started"],
            "error_type": error_type, "attempts": service._broker.used,
            "historical_descendant_operations_not_executed": [row["operation_id"] for row in events
                if row.get("event_type") == "operation_start" and row.get("cause_input_id") == input_id],
            "downstream_replay_supported": False, "timing_preserved": False,
            "source_state_equivalent": False}
