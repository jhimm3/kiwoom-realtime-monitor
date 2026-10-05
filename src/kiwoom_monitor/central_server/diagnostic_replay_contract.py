"""Typed recorded inputs and selection boundaries; no SQL or network execution.

The registry describes captureable native store operations. A compiled plan is
not an executable run: dedicated baseline/clock/ID adapters remain separate gates.
"""
from __future__ import annotations

import asyncio
import inspect
import math
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, fields
from datetime import date, datetime
from functools import wraps
from threading import Lock
from typing import Any
from uuid import uuid4

from kiwoom_monitor.domain.market_data_contract import (
    CandidateUniverse, CoverageObservation, DataCompleteness, DataUnit, DataValueKind,
    MarketDataMetadata, MarketDataObservation, MarketDatasetKind, ObservationOrigin, TradingVenue,
)
from .database_query_cache import StoredQuery

CODEC_VERSION = "store-input/v1"
MAX_COPY_BYTES = 8 * 1024 * 1024
MAX_NODES = 120_000
MAX_DEPTH = 32
MAX_STRING_CHARS = 1_048_576
_ENUMS = {value.__name__: value for value in (
    CandidateUniverse, DataCompleteness, DataUnit, DataValueKind, MarketDatasetKind,
    ObservationOrigin, TradingVenue,
)}
_OBJECTS = {value.__name__: value for value in (
    CoverageObservation, MarketDataMetadata, MarketDataObservation, StoredQuery,
)}

# Explicit names, never an arbitrary method supplied by a trace or request.
_METHOD_GROUPS = {
    "realtime": (
        "save_realtime_snapshots", "save_minute_bars", "finalize_minute_bars", "save_second_trade_bars",
    ),
    "rest_market": (
        "save_query", "load_query", "replace_minute_bars", "replace_daily_bars",
        "save_five_minute_bars", "load_five_minute_bars", "save_market_data_metadata",
        "load_market_data_metadata", "load_market_data_metadata_range",
    ),
    "market_data": ("save_dataset_snapshot", "save_dataset_snapshots", "load_dataset_snapshots"),
    "readers": (
        "load_minute_bars", "load_daily_bars", "load_realtime_snapshots", "load_latest_market_caps",
        "load_top20_statistics", "load_observation_revisions", "load_observation_revisions_after",
        "load_shadow_candidates", "load_shadow_monitor_state", "load_external_bars",
        "load_hot_cohort", "load_market_event_history", "load_theme_snapshots",
        "load_mock_automation_control", "storage_size_bytes", "storage_breakdown",
    ),
    "documents": ("upsert_documents", "replace_documents", "load_documents", "load_document"),
    "news": (
        "enqueue_news_ai_jobs", "claim_news_jobs", "finish_news_job", "retry_news_job",
        "save_news_body_revision", "save_news_ai_results", "save_news_event_revision",
        "save_news_source_page", "load_news_source_cursor", "load_news_source_diagnostics",
        "load_market_news_feed", "load_news_history", "load_latest_news_body",
        "load_news_body_revision", "load_news_article_revision", "load_stock_news_articles",
        "load_confirmed_news_articles", "claim_news_request", "news_request_count", "find_news_ai_revision",
    ),
    "shadow": ("save_shadow_monitor_state", "save_shadow_evaluation"),
    "market_events": ("append_vi_events", "record_hot_cohort_revision", "append_upper_limit_facts"),
    "external_market": ("save_external_bars",),
}
OPERATIONS = {name: group for group, names in _METHOD_GROUPS.items() for name in names}
EXCLUDED_METHODS = frozenset({
    "initialize", "close", "find_credential_activation", "list_credential_profiles",
    "create_credential_profile", "archive_credential_profile", "rename_credential_profile",
    "register_credential_profile", "finalize_credential_activation", "load_credential_activations",
    "save_real_account_recovery", "save_real_account_event", "save_account_settings", "load_account_settings",
    "save_market_profile_settings", "load_market_profile_settings", "set_news_job_wakeup",
    "create_execution_intent", "append_execution_event", "save_execution_account_snapshot",
    "save_mock_automation_control", "register_account_identity", "append_account_binding",
    "register_account_scope_alias", "resolve_account_scope", "load_account_bindings",
    "acquire_execution_runtime", "release_execution_runtime",
})
_DOCUMENT_COLLECTIONS = frozenset({
    "stock_fundamentals", "stock_nxt_eligibility", "stock_price_references", "stock_catalog",
    "top20_daily_entrants", "market_data_coverage", "market_data_coverage_daily",
    "market_data_coverage_intraday", "daily_bar_history_coverage", "historical_highs",
    "candidate_flow_capture", "candidate_flow_finalization", "market_index_chart_coverage",
    "minute_trade_value_comparisons", "condition_search_status", "market_event_sessions",
    "external_market_collection_status", "external_market_roll_state", "krx_trading_day_observations",
    "news_article", "news_ai", "news_ai_shared", "news_original_publication", "news_assessment",
    "news_request_usage", "news_sync", "news_watchlist", "news_automation_settings",
    "theme_profile", "theme_stock", "theme_metadata",
})
_DATASET_KINDS = frozenset({
    "market_state", "new_high", "program_flow", "ranking", "top20_membership", "top20_index",
    "market_index_chart", "investor_flow", "stock_fundamentals", "nxt_eligibility", "top20_statistics_day",
})
_COLLECTOR_SINKS = frozenset(_METHOD_GROUPS["realtime"])
_SECRET_FIELDS = frozenset({
    "password", "passwd", "secret", "secret_key", "app_secret", "app_key", "authorization",
    "access_token", "refresh_token", "api_key", "owner_token", "dsn", "database_url",
    "account_number", "acnt_no", "9201",
})


class InputRejected(ValueError):
    pass


@dataclass(frozen=True)
class FrozenPayload:
    value: Any
    charge: int


def freeze_payload(value: Any, *, maximum_bytes: int = MAX_COPY_BYTES) -> FrozenPayload:
    """Bounded immutable copy. No JSON, hash, I/O, user conversion, or generator consumption."""
    charge = nodes = 0
    active: set[int] = set()

    def reserve(size: int) -> None:
        nonlocal charge, nodes
        nodes += 1
        charge += size
        if charge > maximum_bytes or nodes > MAX_NODES:
            raise InputRejected("payload_budget_exceeded")

    def visit(item: Any, depth: int) -> Any:
        if depth > MAX_DEPTH:
            raise InputRejected("payload_depth_exceeded")
        kind = type(item)
        if item is None or kind in (bool, int):
            reserve(64)
            if kind is int and item.bit_length() > 128:
                raise InputRejected("integer_out_of_bounds")
            return item
        if kind is float:
            reserve(64)
            if not math.isfinite(item):
                raise InputRejected("nonfinite_number")
            return item
        if kind is str:
            if len(item) > MAX_STRING_CHARS:
                raise InputRejected("string_out_of_bounds")
            reserve(128 + 4 * len(item))
            return item
        if kind in _ENUMS.values():
            reserve(256)
            return ("enum", kind.__name__, item.value)
        if kind in (date, datetime):
            reserve(256)
            return ("datetime" if kind is datetime else "date", item.isoformat(), getattr(item, "fold", 0))
        if kind not in (dict, list, tuple) and kind not in _OBJECTS.values():
            raise InputRejected("unsupported_payload_type")
        marker = id(item)
        if marker in active:
            raise InputRejected("cyclic_payload")
        active.add(marker)
        try:
            if kind is dict:
                reserve(128 + 80 * len(item))
                pairs = []
                for key, child in item.items():
                    if type(key) is not str:
                        raise InputRejected("nonstring_key")
                    if key.casefold() in _SECRET_FIELDS:
                        raise InputRejected("sensitive_payload_field")
                    pairs.append((visit(key, depth + 1), visit(child, depth + 1)))
                return ("map", tuple(pairs))
            if kind in (list, tuple):
                reserve(128 + 16 * len(item))
                return ("list" if kind is list else "tuple", tuple(visit(child, depth + 1) for child in item))
            reserve(512)
            return ("object", kind.__name__, visit(
                {field.name: getattr(item, field.name) for field in fields(kind)}, depth + 1,
            ))
        finally:
            active.remove(marker)

    frozen = visit(value, 0)
    return FrozenPayload(frozen, charge)


def thaw_payload(node: Any) -> Any:
    """Decode only explicit tags/types, with the same bounds as capture."""
    budget = [0]

    def decode(value: Any, depth: int) -> Any:
        budget[0] += 1
        if depth > MAX_DEPTH or budget[0] > MAX_NODES:
            raise InputRejected("invalid_payload_bounds")
        if value is None or type(value) in (bool, int, float, str):
            return value
        if type(value) not in (list, tuple) or not value or type(value[0]) is not str:
            raise InputRejected("invalid_payload_tag")
        tag = value[0]
        if tag in {"list", "tuple"} and len(value) == 2 and type(value[1]) in (list, tuple):
            items = [decode(child, depth + 1) for child in value[1]]
            return items if tag == "list" else tuple(items)
        if tag == "map" and len(value) == 2 and type(value[1]) in (list, tuple):
            result = {}
            for pair in value[1]:
                if type(pair) not in (list, tuple) or len(pair) != 2 or type(pair[0]) is not str or pair[0] in result:
                    raise InputRejected("invalid_payload_map")
                result[pair[0]] = decode(pair[1], depth + 1)
            return result
        if tag == "enum" and len(value) == 3 and type(value[1]) is str and value[1] in _ENUMS:
            try:
                return _ENUMS[value[1]](value[2])
            except (ValueError, TypeError) as error:
                raise InputRejected("invalid_payload_enum") from error
        if tag in {"date", "datetime"} and len(value) == 3:
            try:
                if tag == "date":
                    return date.fromisoformat(value[1])
                return datetime.fromisoformat(value[1]).replace(fold=value[2])
            except (ValueError, TypeError) as error:
                raise InputRejected("invalid_payload_datetime") from error
        if tag == "object" and len(value) == 3 and type(value[1]) is str and value[1] in _OBJECTS:
            values = decode(value[2], depth + 1)
            cls = _OBJECTS[value[1]]
            if type(values) is not dict or set(values) != {field.name for field in fields(cls)}:
                raise InputRejected("invalid_payload_object")
            try:
                return cls(**values)
            except (ValueError, TypeError) as error:
                raise InputRejected("invalid_payload_object") from error
        raise InputRejected("invalid_payload_tag")

    result = decode(node, 0)
    freeze_payload(result)  # Recheck byte limits, secrets, and primitive validity after decoding.
    return result


def validate_operation(method: str, arguments: dict) -> str:
    if type(method) is not str or method not in OPERATIONS or type(arguments) is not dict:
        raise InputRejected("unsupported_operation")
    group = OPERATIONS[method]
    if group == "documents":
        collection = arguments.get("collection")
        if type(collection) is not str or collection not in _DOCUMENT_COLLECTIONS:
            raise InputRejected("unsupported_document_collection")
        if collection.startswith("news_"):
            return "news"
        if collection.startswith(("theme_",)):
            return "documents"
        return "top20" if collection in {"top20_daily_entrants", "candidate_flow_capture", "candidate_flow_finalization"} else "rest_market"
    if group == "market_data":
        if method == "save_dataset_snapshots":
            values = arguments.get("values")
            if type(values) not in (list, tuple) or any(type(row) not in (list, tuple) or not row for row in values):
                raise InputRejected("unsupported_dataset_shape")
            kinds = [row[0] for row in values]
        else:
            kinds = [arguments.get("kind")]
        if not kinds or any(not isinstance(kind, str) or kind not in _DATASET_KINDS for kind in kinds):
            raise InputRejected("unsupported_dataset_kind")
        if all(kind in {"ranking", "top20_membership", "top20_index", "top20_statistics_day"} for kind in kinds):
            return "top20"
        return "rest_market"
    if method == "save_query" and arguments.get("api_id") not in {
        "ka00198", "ka10080", "ka10081", "ka10083", "ka10094", "ka10045", "ka90008",
        "ka10016", "ka10001", "ka10100", "ka10054", "ka20005", "ka20006",
    }:
        raise InputRejected("unsupported_query_api")
    return group


@dataclass(frozen=True)
class CaptureOwner:
    workload_id: str
    producer_component: str
    actor_id: str
    cause_input_id: str = ""


_OWNER: ContextVar[CaptureOwner | None] = ContextVar("replay_capture_owner", default=None)
_OPERATION: ContextVar[str] = ContextVar("replay_input_operation", default="")
_SEQUENCE_LOCK = Lock()
_ACTOR_SEQUENCES: dict[tuple[str, str], int] = {}


def operation_identity() -> dict[str, str]:
    owner = _OWNER.get()
    return {"input_operation_id": _OPERATION.get(),
            "workload_id": owner.workload_id if owner else "",
            "producer_component": owner.producer_component if owner else "",
            "actor_id": owner.actor_id if owner else "",
            "cause_input_id": owner.cause_input_id if owner else ""}


@contextmanager
def capture_owner(workload_id: str, producer_component: str, actor_id: str, *, cause_input_id: str = ""):
    if any(type(value) is not str or not value or len(value) > 160 for value in (workload_id, producer_component, actor_id)):
        raise ValueError("invalid_capture_owner")
    token = _OWNER.set(CaptureOwner(workload_id, producer_component, actor_id, cause_input_id))
    try:
        yield
    finally:
        _OWNER.reset(token)


def captured_workload(workload_id: str, component: str):
    """Give an actual owned task a stable actor before asyncio.to_thread copies context."""
    def decorate(function):
        @wraps(function)
        async def run(self, *args, **kwargs):
            task = asyncio.current_task()
            producer = f"{component}:{id(self):x}"
            actor = f"{producer}:{id(task):x}"
            from .diagnostic_trace import input_token
            high_water = getattr(self, "_recorded_input_high_water", (None, ""))
            cause = high_water[1] if high_water[0] == input_token("collector_inputs") else ""
            with capture_owner(workload_id, producer, actor, cause_input_id=cause):
                return await function(self, *args, **kwargs)
        return run
    return decorate


def _actor_sequence(trace_id: str, actor_id: str) -> int:
    with _SEQUENCE_LOCK:
        key = (trace_id, actor_id)
        if key not in _ACTOR_SEQUENCES and len(_ACTOR_SEQUENCES) >= 32_768:
            raise InputRejected("actor_limit_exceeded")
        number = _ACTOR_SEQUENCES.get(key, 0) + 1
        _ACTOR_SEQUENCES[key] = number
        return number


def reset_actor_sequences() -> None:
    with _SEQUENCE_LOCK:
        _ACTOR_SEQUENCES.clear()


def _result_summary(method: str, result: Any) -> dict:
    # No arbitrary object repr or full reader result copying on the return path.
    if result is None or type(result) in (bool, int):
        return {"result": result}
    if type(result) in (list, tuple, dict):
        return {"result_type": type(result).__name__, "result_count": len(result)}
    if type(result) is str and method in {"save_news_body_revision", "save_news_event_revision", "find_news_ai_revision"}:
        return {"result_binding": {"operation": method, "source_id": result[:160]}}
    return {"result_type": type(result).__name__[:80]}


def install_store_capture(store: Any) -> None:
    """Instrument explicit entry boundaries, preserving the original bound native method.

    Unknown/excluded public methods produce metadata only when payload capture is on.
    Private connection/cursor methods and property access are never wrapped.
    """
    if getattr(store, "_recorded_input_installed", False):
        return
    from . import diagnostic_trace as trace
    for name in dir(type(store)):
        if name.startswith("_"):
            continue
        descriptor = inspect.getattr_static(type(store), name)
        if not inspect.isfunction(descriptor):
            continue
        original = getattr(store, name)
        signature = inspect.signature(original)

        def wrapper(*args, __name=name, __original=original, __signature=signature, **kwargs):
            trace_id = trace.input_token("store_inputs")
            if trace_id is None or _OPERATION.get():
                return __original(*args, **kwargs)
            import time
            entered_wall, entered_mono = time.time_ns(), time.monotonic_ns()
            operation_id = uuid4().hex
            owner = _OWNER.get()
            fields = {"operation_id": operation_id, "method": __name, "codec_version": CODEC_VERSION,
                      "entered_wall_ns": entered_wall, "entered_mono_ns": entered_mono,
                      "producer_component": owner.producer_component if owner else "unknown",
                      "actor_id": owner.actor_id if owner else f"unknown:{operation_id}",
                      "actor_known": owner is not None,
                      "cause_input_id": owner.cause_input_id if owner else "",
                      "workload_id": owner.workload_id if owner else OPERATIONS.get(__name, "unsupported")}
            try:
                fields["actor_sequence"] = _actor_sequence(trace_id, fields["actor_id"])
                if __name in EXCLUDED_METHODS:
                    raise InputRejected("excluded_operation")
                if __name not in OPERATIONS:
                    raise InputRejected("unsupported_operation")
                bound = __signature.bind(*args, **kwargs)
                bound.apply_defaults()
                arguments = dict(bound.arguments)
                group = validate_operation(__name, arguments)
                fields["workload_id"] = owner.workload_id if owner else group
                from .postgres_access import current_db_call_tags
                fields.update(current_db_call_tags())
                trace.emit_payload(trace_id, "operation_start", fields, arguments)
            except Exception as error:
                reason = str(error) if isinstance(error, InputRejected) else "capture_boundary_error"
                try:
                    trace.reject_input(trace_id, {**fields, "reason": reason})
                except Exception:
                    pass  # Observation must never change the native store outcome.
            token = _OPERATION.set(operation_id)
            try:
                result = __original(*args, **kwargs)
            except BaseException as error:
                try:
                    trace.emit(trace_id, "operation_end", {**fields, "outcome": "failed",
                               "exception_type": type(error).__name__, "finished_mono_ns": time.monotonic_ns()})
                except Exception:
                    pass
                raise
            else:
                try:
                    trace.emit(trace_id, "operation_end", {**fields, "outcome": "returned",
                               "finished_mono_ns": time.monotonic_ns(), **_result_summary(__name, result)})
                except Exception:
                    pass
                return result
            finally:
                _OPERATION.reset(token)

        setattr(store, name, wraps(original)(wrapper))
    store._recorded_input_installed = True


@dataclass(frozen=True)
class RecordedPlan:
    mode: str
    operation_ids: tuple[str, ...]
    collector_input_ids: tuple[str, ...]
    excluded_operation_ids: tuple[str, ...]
    replaced_operation_ids: tuple[str, ...]
    selected_workloads: tuple[str, ...]
    actor_known: bool
    warnings: tuple[str, ...]
    execution_ready: bool = False  # Baseline/clock/ref adapters and public runner are not yet wired.


@contextmanager
def replay_operation_identity(operation_id: str):
    """Bind a new replay operation to native DB spans, without recapturing input."""
    token = _OPERATION.set(operation_id)
    try:
        yield
    finally:
        _OPERATION.reset(token)


def compile_recorded_plan(events: list[dict], *, started_mono_ns: int,
                          window_start_seconds: float, window_end_seconds: float,
                          include_workloads: tuple[str, ...] = (), exclude_workloads: tuple[str, ...] = (),
                          mode: str = "recorded_operations", collector_components: tuple[str, ...] = ()) -> RecordedPlan:
    if (mode not in {"recorded_operations", "collector_with_background"}
            or not all(math.isfinite(value) for value in (window_start_seconds, window_end_seconds))
            or not 0 <= window_start_seconds < window_end_seconds <= 7200
            or window_end_seconds - window_start_seconds > 600
            or set(include_workloads) & set(exclude_workloads)
            or len(set(include_workloads)) != len(include_workloads)
            or len(set(exclude_workloads)) != len(exclude_workloads)):
        raise ValueError("invalid_recorded_selection")
    if mode == "collector_with_background" and window_end_seconds > 900:
        raise ValueError("recorded_collector_prefix_exceeds_limit")
    starts, ends, inputs, rejected = {}, {}, [], []
    seen_sequences = set()
    for row in events:
        seq = row.get("seq")
        if type(seq) is not int or seq <= 0 or seq in seen_sequences:
            raise ValueError("recorded_event_sequence_invalid")
        seen_sequences.add(seq)
        kind = row.get("event_type")
        if kind in {"operation_start", "operation_end"}:
            target = starts if kind == "operation_start" else ends
            identifier = row.get("operation_id")
            if type(identifier) is not str or identifier in target:
                raise ValueError("recorded_operation_pair_invalid")
            target[identifier] = row
        elif kind == "collector_input":
            inputs.append(row)
        elif kind == "input_rejected":
            rejected.append(row)
    if seen_sequences and seen_sequences != set(range(1, max(seen_sequences) + 1)):
        raise ValueError("recorded_event_sequence_gap")

    def in_window(row):
        at = row.get("entered_mono_ns", row.get("mono_ns"))
        if type(at) is not int:
            raise ValueError("recorded_time_missing")
        return window_start_seconds <= (at - started_mono_ns) / 1e9 < window_end_seconds

    known = {row.get("workload_id") for row in [*starts.values(), *rejected, *inputs] if in_window(row)}
    if any(type(value) is not str or not value for value in known):
        raise ValueError("recorded_workload_missing")
    requested = set(include_workloads) if include_workloads else known
    if (set(include_workloads) | set(exclude_workloads)) - known:
        raise ValueError("recorded_workload_not_observed")
    selected = requested - set(exclude_workloads)
    if not selected:
        raise ValueError("recorded_selection_empty")
    if any(row.get("workload_id") in selected and in_window(row) for row in rejected):
        raise ValueError("recorded_selected_input_unsupported")
    operations, excluded, replaced, included_inputs = [], [], [], []
    actor_known = True
    components = set(collector_components)
    if mode == "recorded_operations" and components:
        raise ValueError("recorded_frontier_conflict")
    if mode == "collector_with_background":
        if not components or "realtime" not in selected:
            raise ValueError("recorded_collector_selection_invalid")
        input_components = {row.get("producer_component") for row in inputs}
        if components - input_components:
            raise ValueError("recorded_collector_input_missing")
        if any(row.get("producer_component") in components and
               (row["mono_ns"] - started_mono_ns) / 1e9 < window_end_seconds for row in rejected):
            raise ValueError("recorded_collector_prefix_incomplete")
        initial_components = set()
        input_ids = set()
        for row in inputs:
            if row.get("producer_component") in components:
                if (row["mono_ns"] - started_mono_ns) / 1e9 >= window_end_seconds:
                    continue
                identifier = row.get("input_id")
                if type(identifier) is not str or not identifier or identifier in input_ids or "payload" not in row:
                    raise ValueError("recorded_collector_input_invalid")
                input_ids.add(identifier)
                value = thaw_payload(row["payload"])
                if type(value) is not dict or row.get("input_kind") not in {"initial_state", "message", "source_approval", "capture_gap"}:
                    raise ValueError("recorded_collector_input_invalid")
                if row["input_kind"] == "initial_state":
                    initial_components.add(row["producer_component"])
                if row.get("excluded_types", {}).get("0w", 0) or any(
                    row.get("excluded_types", {}).get(name, 0) for name in ("0J", "0U")
                ):
                    raise ValueError("recorded_collector_mixed_latest_unsupported")
                # Keep prefix to reconstruct source resets before the selected window.
                if (row["mono_ns"] - started_mono_ns) / 1e9 < window_end_seconds:
                    included_inputs.append(row["input_id"])
        if initial_components != components:
            raise ValueError("recorded_collector_initial_state_missing")
    for identifier, row in sorted(starts.items(), key=lambda item: (item[1]["entered_mono_ns"], item[1]["seq"])):
        if not in_window(row):
            continue
        if row["workload_id"] not in selected:
            excluded.append(identifier)
            continue
        if identifier not in ends or ends[identifier].get("codec_version") != CODEC_VERSION:
            raise ValueError("recorded_operation_censored_or_codec_invalid")
        ending = ends[identifier]
        if (any(ending.get(key) != row.get(key) for key in
                ("method", "workload_id", "producer_component", "actor_id"))
                or ending.get("finished_mono_ns", 0) < row["entered_mono_ns"]):
            raise ValueError("recorded_operation_pair_invalid")
        if row.get("codec_version") != CODEC_VERSION or "payload" not in row:
            raise ValueError("recorded_payload_missing")
        arguments = thaw_payload(row["payload"])
        validate_operation(row["method"], arguments)
        if mode == "collector_with_background" and row.get("producer_component") in components and row["method"] in _COLLECTOR_SINKS:
            if row["method"] == "save_realtime_snapshots" and any(
                value.get("event_type") != "trade" for value in arguments.get("values", ())
            ):
                raise ValueError("recorded_collector_mixed_latest_unsupported")
            replaced.append(identifier)
        else:
            operations.append(identifier)
        actor_known = actor_known and row.get("actor_known") is True
    if not operations and not included_inputs:
        raise ValueError("recorded_selection_empty")
    warnings = ["baseline_clock_and_reference_adapters_pending"]
    if not actor_known:
        warnings.append("causal_actor_unconfirmed")
    if mode == "collector_with_background":
        warnings.append("collector_initial_state_cold_with_prefix")
    return RecordedPlan(mode, tuple(operations), tuple(included_inputs), tuple(excluded), tuple(replaced),
                        tuple(sorted(selected)), actor_known, tuple(warnings))
