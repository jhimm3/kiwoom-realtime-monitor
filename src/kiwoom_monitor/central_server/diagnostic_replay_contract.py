"""Typed recorded inputs and selection boundaries; no SQL or network execution.

The registry describes captureable native store operations. A compiled plan is
not an executable run: dedicated baseline/clock/ID adapters remain separate gates.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
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
from .diagnostic_account_input import METHODS as ACCOUNT_METHODS
from kiwoom_monitor.domain.order_contract import (
    AccountBinding, AccountEnvironment, AccountScope, AccountSnapshot,
    BrokerFill, BrokerOrderSnapshot, OrderState,
)
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import AccountRecovery
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import OrderExecution, AccountBalanceChange

CODEC_VERSION = "store-input/v1"
CATALOG_COPY_PROFILE = "stock-catalog-documents/v1"
CATALOG_MAX_COPY_BYTES = 16 * 1024 * 1024
LARGE_COPY_PROFILE = "large-store-input/v1"
LARGE_MAX_COPY_BYTES = 64 * 1024 * 1024
LARGE_MAX_NODES = 960_000
ACCOUNT_CONTEXT_PROFILE = 'account-context-input/v1'
ACCOUNT_CONTEXT_COPY_BYTES = 32 * 1024 * 1024
LARGE_INPUT_METHODS = frozenset({
    "replace_daily_bars", "replace_minute_bars", "save_second_trade_bars",
    "save_shadow_monitor_state",
})
_UNSET_PAYLOAD = object()
COLLECTOR_INPUT_VERSION = "collector-input/v2"
LEGACY_COLLECTOR_INPUT_VERSION = "collector-input/v1"
# Only parser-consumed market fields. Never expand this to arbitrary REAL rows.
COLLECTOR_EVENT_FIELDS = {
    "0B": ("10", "12", "13", "14", "15", "17", "20", "228", "290", "311"),
    "0w": ("20", "210", "211", "212", "213"),
    "0J": ("20", "10", "12", "14", "252", "255", "253"),
    "0U": ("20", "10", "12", "14", "252", "255", "253"),
}


def validate_collector_message(message: Any, *, version: str = COLLECTOR_INPUT_VERSION,
                               allow_empty: bool = False) -> None:
    """Validate recorded market input before any parser or native store runs."""
    allowed = {"0B"} if version == LEGACY_COLLECTOR_INPUT_VERSION else set(COLLECTOR_EVENT_FIELDS)
    if version not in {COLLECTOR_INPUT_VERSION, LEGACY_COLLECTOR_INPUT_VERSION}:
        raise ValueError("recorded_collector_input_version_unsupported")
    data = message.get("data") if type(message) is dict else None
    if (type(message) is not dict or set(message) != {"trnm", "data"}
            or message.get("trnm") != "REAL" or type(data) is not list
            or not (0 if allow_empty else 1) <= len(data) <= 100):
        raise ValueError("recorded_execution_collector_message_invalid")
    for item in data:
        if type(item) is not dict:
            raise ValueError("recorded_execution_collector_message_invalid")
        kind = item.get("type")
        if (type(kind) is not str or kind not in allowed
                or set(item) - {"type", "item", "stk_cd", "code", "values"}
                or type(item.get("values")) is not dict
                or set(item["values"]) - set(COLLECTOR_EVENT_FIELDS[kind])):
            raise ValueError("recorded_execution_collector_message_invalid")


MAX_COPY_BYTES = 8 * 1024 * 1024
MAX_NODES = 120_000
MAX_DEPTH = 32
MAX_STRING_CHARS = 1_048_576
_ENUMS = {value.__name__: value for value in (
    CandidateUniverse, DataCompleteness, DataUnit, DataValueKind, MarketDatasetKind,
    ObservationOrigin, TradingVenue,
    AccountEnvironment, OrderState,
)}
_OBJECTS = {value.__name__: value for value in (
    CoverageObservation, MarketDataMetadata, MarketDataObservation, StoredQuery,
    AccountBinding, AccountScope, AccountSnapshot, BrokerFill, BrokerOrderSnapshot,
    AccountRecovery, OrderExecution, AccountBalanceChange,
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
    "account": tuple(sorted(ACCOUNT_METHODS)),
}
OPERATIONS = {name: group for group, names in _METHOD_GROUPS.items() for name in names}
EXCLUDED_METHODS = frozenset({
    "initialize", "close", "find_credential_activation", "list_credential_profiles",
    "create_credential_profile", "archive_credential_profile", "rename_credential_profile",
    "register_credential_profile", "finalize_credential_activation", "load_credential_activations",
    "save_account_settings", "load_account_settings",
    "save_market_profile_settings", "load_market_profile_settings", "set_news_job_wakeup",
    "save_mock_automation_control", "register_account_identity", "append_account_binding",
    "register_account_scope_alias", "resolve_account_scope", "load_account_bindings",
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
_READ_DOCUMENT_COLLECTIONS = frozenset({
    'app_settings', 'app_column_settings', 'journal_news_link', 'journal_v2_news_links',
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
    def __init__(self, reason: str, *, details: dict[str, int | str] | None = None):
        super().__init__(reason)
        self.details = dict(details or {})


@dataclass(frozen=True)
class FrozenPayload:
    value: Any
    charge: int


def freeze_payload(value: Any, *, maximum_bytes: int = MAX_COPY_BYTES) -> FrozenPayload:
    """Bounded immutable copy. No JSON, hash, I/O, user conversion, or generator consumption."""
    charge = nodes = 0
    maximum_nodes = LARGE_MAX_NODES if maximum_bytes in (LARGE_MAX_COPY_BYTES, ACCOUNT_CONTEXT_COPY_BYTES) else MAX_NODES
    active: set[int] = set()

    def reserve(size: int) -> None:
        nonlocal charge, nodes
        nodes += 1
        charge += size
        if charge > maximum_bytes or nodes > maximum_nodes:
            raise InputRejected("payload_budget_exceeded", details={
                "budget": "bytes" if charge > maximum_bytes else "nodes",
                "observed_bytes": charge,
                "observed_nodes": nodes,
                "maximum_bytes": maximum_bytes,
                "maximum_nodes": maximum_nodes,
            })

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


def thaw_payload(node: Any, *, maximum_bytes: int = MAX_COPY_BYTES) -> Any:
    """Decode only explicit tags/types, with the same bounds as capture."""
    if maximum_bytes not in (MAX_COPY_BYTES, CATALOG_MAX_COPY_BYTES, LARGE_MAX_COPY_BYTES, ACCOUNT_CONTEXT_COPY_BYTES):
        raise InputRejected("invalid_payload_copy_limit")
    maximum_nodes = LARGE_MAX_NODES if maximum_bytes in (LARGE_MAX_COPY_BYTES, ACCOUNT_CONTEXT_COPY_BYTES) else MAX_NODES
    budget = [0]

    def decode(value: Any, depth: int) -> Any:
        budget[0] += 1
        if depth > MAX_DEPTH or budget[0] > maximum_nodes:
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
    freeze_payload(result, maximum_bytes=maximum_bytes)
    return result


def payload_copy_limit(event_type: str, fields: dict, value: Any = _UNSET_PAYLOAD) -> int:
    """Select explicit operation profiles; never infer them from payload size."""
    if "payload_profile" not in fields:
        return MAX_COPY_BYTES
    if fields['payload_profile'] == ACCOUNT_CONTEXT_PROFILE:
        from .diagnostic_account_context import VERSIONS
        if event_type != 'account_context' or fields.get('account_context_version') not in VERSIONS:
            raise InputRejected('invalid_payload_profile')
        if value is not _UNSET_PAYLOAD and type(value) is not dict:
            raise InputRejected('invalid_account_context_payload')
        return ACCOUNT_CONTEXT_COPY_BYTES
    if fields["payload_profile"] == LARGE_COPY_PROFILE:
        if event_type != "operation_start" or fields.get("method") not in LARGE_INPUT_METHODS:
            raise InputRejected("invalid_payload_profile")
        if value is not _UNSET_PAYLOAD and type(value) is not dict:
            raise InputRejected("invalid_large_payload_arguments")
        return LARGE_MAX_COPY_BYTES
    if (fields["payload_profile"] != CATALOG_COPY_PROFILE
            or event_type != "operation_start"
            or fields.get("method") != "replace_documents"
            or fields.get("collection") != "stock_catalog"):
        raise InputRejected("invalid_payload_profile")
    if value is not _UNSET_PAYLOAD and (type(value) is not dict or value.get("collection") != "stock_catalog"):
        raise InputRejected("payload_profile_collection_mismatch")
    return CATALOG_MAX_COPY_BYTES


def thaw_operation_arguments(row: dict, *, owner_bindings=None) -> dict:
    """Validate the operation envelope and decoded binding before native execution."""
    limit = payload_copy_limit(row.get("event_type"), row)
    if row.get("codec_version") != CODEC_VERSION:
        raise InputRejected("invalid_operation_codec")
    from .diagnostic_trace_payload import BlockPayloadReference
    payload = row.get('payload')
    if type(payload) is BlockPayloadReference:
        if row.get('payload_profile') != LARGE_COPY_PROFILE:
            raise InputRejected('invalid_payload_profile')
        payload = payload.load()
    arguments = thaw_payload(payload, maximum_bytes=limit)
    if row.get('method') in ACCOUNT_METHODS:
        from .diagnostic_account_input import restore_account_arguments
        arguments = restore_account_arguments(row, arguments, bindings=owner_bindings)
    payload_copy_limit(row.get("event_type"), row, arguments)
    if "collection" in row and (type(arguments) is not dict
                                or arguments.get("collection") != row["collection"]):
        raise InputRejected("operation_collection_mismatch")
    validate_operation(row.get("method"), arguments)
    return arguments


def validate_operation(method: str, arguments: dict) -> str:
    if type(method) is not str or method not in OPERATIONS or type(arguments) is not dict:
        raise InputRejected("unsupported_operation")
    group = OPERATIONS[method]
    if group == "documents":
        collection = arguments.get("collection")
        if collection == "account_entry_symbols_daily" and method == "load_documents":
            # TOP20 reads a date-owned stock-code cohort, not account state.
            # Capture arguments/count only; writes and response documents stay
            # outside this boundary. Do not broaden the document allowlist.
            owner = arguments.get("owner")
            try:
                valid_day = type(owner) is str and date.fromisoformat(owner).isoformat() == owner
            except ValueError:
                valid_day = False
            if (not valid_day or type(arguments.get("limit")) is not int
                    or not 1 <= arguments["limit"] <= 5000
                    or arguments.get("offset", 0) != 0 or arguments.get("updated_after", 0) != 0):
                raise InputRejected("unsupported_document_collection")
            return "top20"
        read_only = (type(collection) is str and method in {'load_documents', 'load_document'}
                     and collection in _READ_DOCUMENT_COLLECTIONS)
        if type(collection) is not str or collection not in _DOCUMENT_COLLECTIONS and not read_only:
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


def captured_workload(workload_id: str, component: str, *, actor_per_invocation: bool = False):
    """Give an actual owned task a stable actor before asyncio.to_thread copies context."""
    def decorate(function):
        @wraps(function)
        async def run(self, *args, **kwargs):
            task = asyncio.current_task()
            producer = f"{component}:{id(self):x}"
            actor = f"{producer}:{uuid4().hex}" if actor_per_invocation else f"{producer}:{id(task):x}"
            from .diagnostic_trace import input_token
            high_water = getattr(self, "_recorded_input_high_water", (None, ""))
            cause = high_water[1] if high_water[0] == input_token("collector_inputs") else ""
            parent = _OWNER.get()
            if not cause and parent is not None and parent.producer_component == producer:
                cause = parent.cause_input_id
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
            arguments = None
            try:
                fields["actor_sequence"] = _actor_sequence(trace_id, fields["actor_id"])
                if (__name in EXCLUDED_METHODS or __name in ACCOUNT_METHODS
                        and trace.input_token('account_inputs') != trace_id):
                    raise InputRejected("excluded_operation")
                if __name not in OPERATIONS:
                    raise InputRejected("unsupported_operation")
                bound = __signature.bind(*args, **kwargs)
                bound.apply_defaults()
                arguments = dict(bound.arguments)
                if __name in {"load_documents", "load_document", "upsert_documents", "replace_documents"}:
                    collection = arguments.get("collection")
                    if type(collection) is str:
                        fields["collection"] = collection[:160]
                group = validate_operation(__name, arguments)
                fields["workload_id"] = owner.workload_id if owner else group
                from .postgres_access import current_db_call_tags
                fields.update(current_db_call_tags())
                if __name in ACCOUNT_METHODS:
                    arguments, metadata = trace.project_account_input(trace_id, __name, arguments)
                    fields.update(metadata)
                if __name == "replace_documents" and arguments.get("collection") == "stock_catalog":
                    fields["collection"] = "stock_catalog"
                    fields["payload_profile"] = CATALOG_COPY_PROFILE
                elif __name in LARGE_INPUT_METHODS and trace.input_token('large_inputs') == trace_id:
                    fields['payload_profile'] = LARGE_COPY_PROFILE
                trace.emit_payload(trace_id, "operation_start", fields, arguments)
            except Exception as error:
                reason = str(error) if isinstance(error, InputRejected) else "capture_boundary_error"
                details = getattr(error, "details", None)
                if isinstance(details, dict) and details:
                    fields["rejection_detail"] = details
                try:
                    trace.reject_input(trace_id, {**fields, "reason": reason})
                except Exception:
                    pass  # Observation must never change the native store outcome.
            token = _OPERATION.set(operation_id)
            try:
                result = __original(*args, **kwargs)
            except BaseException as error:
                try:
                    from .diagnostic_account_input import native_error_receipt
                    trace.emit(trace_id, "operation_end", {**fields, "outcome": "failed",
                               "exception_type": type(error).__name__, "finished_mono_ns": time.monotonic_ns(),
                               **native_error_receipt(__name, error)})
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
    collector_input_versions: tuple[tuple[str, str], ...] = ()


@contextmanager
def replay_operation_identity(operation_id: str):
    """Bind a new replay operation to native DB spans, without recapturing input."""
    token = _OPERATION.set(operation_id)
    try:
        yield
    finally:
        _OPERATION.reset(token)


_STORE_WINDOW_SEAL = object()


def _store_window_selection(started_mono_ns, window_start_seconds, window_end_seconds,
                            include_workloads, exclude_workloads, mode, collector_components):
    return (started_mono_ns, window_start_seconds, window_end_seconds,
            tuple(include_workloads), tuple(exclude_workloads), mode, tuple(collector_components))


def _store_window_digest(events):
    digest = hashlib.sha256()
    # Do not allocate a second serialization of the full hydrated frontier.
    for part in json.JSONEncoder(sort_keys=True, ensure_ascii=False, separators=(',', ':'),
                                 allow_nan=False).iterencode(events):
        digest.update(part.encode('utf-8'))
    return digest.hexdigest()


class _VerifiedStoreWindow(list):
    """Process-local proof from the checksummed reader, never a JSON admission flag."""

    def __init__(self, rows, *, seal, manifest_hash, selection):
        if seal is not _STORE_WINDOW_SEAL:
            raise ValueError('recorded_window_proof_invalid')
        super().__init__(rows)
        self._seal = seal
        self._manifest_hash = manifest_hash
        self._selection = selection
        self._digest = _store_window_digest([manifest_hash, selection, self])

    def _verify(self, selection):
        if (self._seal is not _STORE_WINDOW_SEAL or self._selection != selection
                or self._digest != _store_window_digest([self._manifest_hash, self._selection, self])):
            raise ValueError('recorded_window_proof_mismatch')


def _seal_store_window(rows, *, manifest_hash, **selection):
    """Only the file reader calls this after complete source integrity validation."""
    if (type(manifest_hash) is not str or len(manifest_hash) != 64
            or any(value not in '0123456789abcdef' for value in manifest_hash)):
        raise ValueError('recorded_window_proof_invalid')
    return _VerifiedStoreWindow(rows, seal=_STORE_WINDOW_SEAL, manifest_hash=manifest_hash,
                                selection=_store_window_selection(**selection))


def compile_recorded_plan(events: list[dict], *, started_mono_ns: int,
                          window_start_seconds: float, window_end_seconds: float,
                          include_workloads: tuple[str, ...] = (), exclude_workloads: tuple[str, ...] = (),
                          mode: str = "recorded_operations", collector_components: tuple[str, ...] = (),
                          _source_sequences_verified: bool = False) -> RecordedPlan:
    if type(events) is _VerifiedStoreWindow:
        events._verify(_store_window_selection(started_mono_ns, window_start_seconds,
            window_end_seconds, include_workloads, exclude_workloads, mode, collector_components))
        _source_sequences_verified = True
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
    starts, ends, inputs, rejected, market_inputs = {}, {}, [], [], []
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
        elif kind == "market_input":
            market_inputs.append(row)
        elif kind == "input_rejected":
            rejected.append(row)
    if _source_sequences_verified and [row["seq"] for row in events] != sorted(seen_sequences):
        raise ValueError("recorded_event_sequence_invalid")
    if not _source_sequences_verified and seen_sequences and seen_sequences != set(range(1, max(seen_sequences) + 1)):
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
    collector_versions = {}
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
                version = row.get("collector_input_version", LEGACY_COLLECTOR_INPUT_VERSION)
                if version not in {LEGACY_COLLECTOR_INPUT_VERSION, COLLECTOR_INPUT_VERSION}:
                    raise ValueError("recorded_collector_input_version_unsupported")
                component = row["producer_component"]
                if collector_versions.setdefault(component, version) != version:
                    raise ValueError("recorded_collector_input_version_mixed")
                if row["input_kind"] == "initial_state":
                    initial_components.add(row["producer_component"])
                omissions = row.get("excluded_types", {})
                if (type(omissions) is not dict or any(type(key) is not str or type(count) is not int
                                                       or count <= 0 for key, count in omissions.items())):
                    raise ValueError("recorded_collector_excluded_types_invalid")
                if omissions.get("0w", 0) or any(
                    omissions.get(name, 0) for name in ("0J", "0U")
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
                ("method", "workload_id", "producer_component", "actor_id", "payload_profile", "collection"))
                or any((key in ending) != (key in row) for key in ("payload_profile", "collection"))
                or ending.get("finished_mono_ns", 0) < row["entered_mono_ns"]):
            raise ValueError("recorded_operation_pair_invalid")
        if row.get("codec_version") != CODEC_VERSION or "payload" not in row:
            raise ValueError("recorded_payload_missing")
        arguments = thaw_operation_arguments(row)
        replace = False
        if mode == "collector_with_background" and row.get("producer_component") in components:
            version = collector_versions[row["producer_component"]]
            replace = row["method"] in _COLLECTOR_SINKS
            if row["method"] == "save_realtime_snapshots":
                allowed = {"trade"} if version == LEGACY_COLLECTOR_INPUT_VERSION else {
                    "trade", "program_trade", "market_state"}
                if any(type(value) is not dict or value.get("event_type") not in allowed
                       for value in arguments.get("values", ())):
                    raise ValueError("recorded_collector_mixed_latest_unsupported")
            if row["method"] == "save_dataset_snapshots":
                kinds = {value[0] for value in arguments["values"]}
                if "market_state" in kinds:
                    if kinds != {"market_state"}:
                        raise ValueError("recorded_collector_mixed_dataset_unsupported")
                    if version != COLLECTOR_INPUT_VERSION:
                        raise ValueError("recorded_collector_market_input_missing")
                    replace = True
        if replace:
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
        warnings.append("hybrid_frontier_not_cross_component_causal_replay")
    if any(row.get("workload_id") in selected and in_window(row) for row in market_inputs):
        warnings.append("top20_market_cause_not_executed_store_calls_only")
    return RecordedPlan(mode, tuple(operations), tuple(included_inputs), tuple(excluded), tuple(replaced),
                        tuple(sorted(selected)), actor_known, tuple(warnings),
                        collector_input_versions=tuple(sorted(collector_versions.items())))


def compile_top20_queue_frontier(events: list[dict], *, trace_id: str, component: str,
                                 manifest: dict | None = None, **selection):
    """Preflight a queue-derived sink frontier, without enabling execution.

    Multiple consumed causes are retained as a set of receipts. No last-tick
    guess is used to classify aggregated program/index transactions. Other
    TOP20 stages still require their own inputs and a complete service runner.
    """
    from .diagnostic_trace import validate_top20_capture_manifest
    validate_top20_capture_manifest(manifest, events, trace_id=trace_id)
    source_start = manifest["started_mono_ns"]
    if selection.get("started_mono_ns", source_start) != source_start:
        raise ValueError("top20_capture_clock_mismatch")
    selection["started_mono_ns"] = source_start
    end = selection.get("window_end_seconds")
    if type(end) not in (int, float) or end * 1_000_000_000 > manifest["finished_mono_ns"] - source_start:
        raise ValueError("top20_capture_window_out_of_bounds")
    return _compile_top20_queue_frontier_verified(events, trace_id=trace_id, component=component,
                                                  **selection)


def _compile_top20_queue_frontier_verified(events, *, trace_id, component,
                                          source_sequences_verified=False, **selection):
    # A sparse list is allowed only by the file reader after the complete
    # manifest/chunk sequence gate. Public in-memory preflight stays strict.
    from .diagnostic_top20_input import compile_delivery_coverage
    coverage = compile_delivery_coverage(events, trace_id=trace_id, component=component,
                                         _source_sequences_verified=source_sequences_verified)
    plan = compile_recorded_plan(events, _source_sequences_verified=source_sequences_verified, **selection)
    if "top20" not in plan.selected_workloads:
        raise ValueError("top20_queue_frontier_not_selected")
    starts = {row["operation_id"]: row for row in events if row.get("event_type") == "operation_start"}
    remaining, replaced = [], list(plan.replaced_operation_ids)
    for identifier in plan.operation_ids:
        row = starts[identifier]
        if row.get("producer_component") != component:
            remaining.append(identifier)
            continue
        if (row.get("workload_id") != "top20" or row.get("actor_known") is not True
                or row.get("shared_owner_components")):
            raise ValueError("top20_queue_frontier_shared_or_unknown_owner")
        value = thaw_payload(row["payload"])
        if row["method"] == "save_dataset_snapshot":
            kinds = {value["kind"]}
        elif row["method"] == "save_dataset_snapshots":
            kinds = {item[0] for item in value["values"]}
        else:
            remaining.append(identifier)
            continue
        sinks = {"top20_index", "program_flow"}
        if kinds & sinks:
            if kinds - sinks:
                raise ValueError("top20_queue_frontier_mixed_transaction")
            replaced.append(identifier)
        else:
            remaining.append(identifier)
    return {"coverage": coverage, "operation_ids": tuple(remaining),
            "source_integrity": "manifest_and_events",
            "replaced_operation_ids": tuple(replaced),
            "excluded_operation_ids": plan.excluded_operation_ids,
            "collector_input_ids": plan.collector_input_ids,
            "scope": "top20_queue_frontier_preflight_only",
            "top20_session_execution_ready": False,
            "warnings": (*plan.warnings, "top20_session_runner_and_other_stage_inputs_pending")}
