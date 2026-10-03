"""Bounded replay of recorded DB calls against the dedicated diagnostic DB.

The first supported calls are empty news claims and inline shadow checkpoints.
Unsupported calls are counted, never replaced with generic SQL.
"""

from __future__ import annotations

import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, time as clock_time, timedelta, timezone
from threading import Event
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid4, uuid5
from zoneinfo import ZoneInfo

from .database import PostgresQueryStore


TEST_DATABASE_NAME = "kiwoom_monitor_diagnostic_test"
MAX_REPLAY_SECONDS = 120
MAX_REPLAY_CALLS = 500
MAX_SHADOW_BYTES = 1_500_000
MAX_QUERY_MINUTE_ROWS = 1000
MAX_QUERY_MINUTE_TOTAL_ROWS = 30_000
QUERY_MINUTE_SCENARIOS = frozenset({"unchanged_page", "one_changed_bar", "fresh_page", "recorded_counts"})


def require_after_hours(now: datetime | None = None) -> None:
    """Avoid synthetic NAS load during KRX/NXT collection hours."""
    local = (now or datetime.now(ZoneInfo("Asia/Seoul"))).astimezone(
        ZoneInfo("Asia/Seoul"))
    if local.weekday() < 5 and clock_time(7, 30) <= local.time() < clock_time(20, 30):
        raise ValueError("replay_allowed_after_market_hours_only")


@dataclass(frozen=True)
class ReplayCall:
    offset_seconds: float
    kind: str
    payload_bytes: int = 0
    rows_attempted: int = 0
    source_call_id: str = ""
    flush_id: str = ""
    observed_domain_counts: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class ReplayProfile:
    report_id: str
    duration_seconds: float
    source_calls: int
    window_calls: int
    supported_calls: tuple[ReplayCall, ...]
    omitted_kinds: dict[str, int]
    window_start_seconds: float
    window_end_seconds: float
    selected_writer_kinds: tuple[str, ...]
    excluded_writer_kinds: tuple[str, ...]
    fidelity: str = "synthetic_shape"
    query_minute_scenario: str = ""


TRACE_SYNTHETIC_KINDS = frozenset({
    "news_job_claim", "shadow_monitor_state", "dataset:program_flow",
    "realtime_latest", "realtime_second_bar",
    "query_minute",
})


def _recorded_query_minute_shape(call: ReplayCall) -> dict[str, int]:
    """Validate the supported count recipe, without guessing missing source state.

    Duplicate occurrences use the same final value. Distinct-value duplicates,
    partial observations and suppressed metadata need a different input recipe.
    The trace has no original keys or values, so every call gets an independent
    seeded subject; canonical and revision baselines are prepared separately.
    """
    counts = dict(call.observed_domain_counts)
    required = {"bar_shape_version", "bar_changed_rows", "observations",
                "metadata_suppressed_rows", "revision_insert_rows",
                "revision_history_enabled", "duplicate_input_keys"}
    if (not required <= counts.keys()
            or any(type(counts[key]) is not int for key in required)
            or len(counts) != len(call.observed_domain_counts)):
        raise ValueError("replay_query_minute_recorded_shape_missing_or_invalid")
    rows = call.rows_attempted
    if type(rows) is not int or not 1 <= rows <= MAX_QUERY_MINUTE_ROWS:
        raise ValueError("replay_query_minute_recorded_shape_unsupported")
    duplicates = counts["duplicate_input_keys"]
    unique = rows - duplicates
    if (counts["bar_shape_version"] != 1
            or not 0 <= duplicates < rows
            or counts["observations"] != rows
            or counts["metadata_suppressed_rows"] != 0
            or counts["revision_history_enabled"] not in (0, 1)
            or not 0 <= counts["bar_changed_rows"] <= unique
            or not 0 <= counts["revision_insert_rows"] <= unique
            or not counts["revision_history_enabled"] and counts["revision_insert_rows"]):
        raise ValueError("replay_query_minute_recorded_shape_unsupported")
    return {key: counts[key] for key in sorted(required)}


def compile_trace_replay_profile(trace_id: str, seconds: int, *,
                                 window_start_seconds: float,
                                 window_end_seconds: float,
                                 include_writer_kinds: tuple[str, ...],
                                 exclude_writer_kinds: tuple[str, ...] = (),
                                 query_minute_scenario: str = "") -> ReplayProfile:
    """Verify a finished trace and select a bounded synthetic DB-call window.

    The trace contains timing and input shape, never original SQL parameters.
    Every selected call must have a committed end and an explicit adapter.
    """
    from .diagnostic_trace import chunk_bytes, status as trace_status

    if (not include_writer_kinds or len(include_writer_kinds) > 20
            or len(exclude_writer_kinds) > 20
            or set(include_writer_kinds) & set(exclude_writer_kinds)
            or any(not kind or len(kind) > 100 for kind in
                   (*include_writer_kinds, *exclude_writer_kinds))):
        raise ValueError("invalid_replay_writer_selection")
    minute_selected = "query_minute" in include_writer_kinds
    if (minute_selected and query_minute_scenario not in QUERY_MINUTE_SCENARIOS
            or not minute_selected and query_minute_scenario):
        raise ValueError("replay_query_minute_scenario_required_or_invalid")
    manifest = trace_status(trace_id)
    duration = float(manifest.get("finished_at", 0)) - float(manifest.get("started_at", 0))
    selection = window_end_seconds - window_start_seconds
    if (manifest.get("state") != "complete" or manifest.get("known_dropped") != 0
            or manifest.get("unknown_tail_loss")
            or manifest.get("accepted") != manifest.get("written")
            or manifest.get("last_seq") != manifest.get("written")
            or not 0 <= window_start_seconds < window_end_seconds <= duration
            or not all(math.isfinite(value) for value in
                       (duration, window_start_seconds, window_end_seconds))
            or selection < 1 or seconds < selection + 5
            or seconds > MAX_REPLAY_SECONDS):
        raise ValueError("replay_trace_incomplete_or_window_out_of_bounds")

    starts: dict[str, dict] = {}
    ends: dict[str, dict] = {}
    domains: dict[str, dict] = {}
    source_calls = 0
    previous_seq = 0
    event_count = 0
    for chunk in manifest.get("chunks", []):
        rows = chunk_bytes(trace_id, chunk["name"]).splitlines()
        if (len(rows) != chunk["count"] or not rows
                or json.loads(rows[0])["seq"] != chunk["first_seq"]
                or json.loads(rows[-1])["seq"] != chunk["last_seq"]):
            raise ValueError("replay_trace_chunk_boundary_invalid")
        for line in rows:
            row = json.loads(line)
            if row["seq"] != previous_seq + 1:
                raise ValueError("replay_trace_sequence_gap")
            previous_seq = row["seq"]
            event_count += 1
            call_id = str(row.get("call_id") or "")
            if row["event_type"] == "call_start":
                source_calls += 1
                offset = row["wall_ns"] / 1_000_000_000 - manifest["started_at"]
                kind = row.get("writer_kind")
                if (window_start_seconds <= offset < window_end_seconds
                        and kind in include_writer_kinds
                        and kind not in exclude_writer_kinds):
                    if call_id in starts:
                        raise ValueError("replay_trace_duplicate_call_id")
                    starts[call_id] = {**row, "offset": offset}
                    if len(starts) > MAX_REPLAY_CALLS:
                        raise ValueError("replay_profile_selected_call_limit")
            elif row["event_type"] == "call_end" and call_id in starts:
                if call_id in ends:
                    raise ValueError("replay_trace_duplicate_call_end")
                ends[call_id] = row
            elif row["event_type"] == "domain" and call_id in starts:
                if call_id in domains:
                    raise ValueError("replay_trace_duplicate_call_domain")
                domains[call_id] = row
    if previous_seq != manifest["last_seq"] or event_count != manifest["written"]:
        raise ValueError("replay_trace_manifest_count_mismatch")

    supported = []
    for call_id, first in starts.items():
        last = ends.get(call_id)
        kind = first["writer_kind"]
        if (last is None or last.get("outcome") != "committed"
                or last.get("commits") != 1 or first.get("access_mode") != "write"
                or kind not in TRACE_SYNTHETIC_KINDS):
            raise ValueError("replay_selected_calls_unsupported_or_incomplete")
        rows = first.get("rows_attempted")
        sql_calls = last.get("sql_calls")
        payload_bytes = domains.get(call_id, {}).get("bytes_payload_estimate") or 0
        expected_sql = {"news_job_claim": 2, "shadow_monitor_state": 2,
                        "realtime_latest": 1, "realtime_second_bar": 1}.get(kind)
        if kind == "dataset:program_flow" and isinstance(rows, int):
            # One SET LOCAL followed by one UPSERT per snapshot in this writer.
            expected_sql = rows + 1
        if kind == "query_minute":
            if (first.get("writer_family") != "rest.market_bars.minute"
                    or first.get("operation") != "replace_minute_bars"
                    or type(rows) is not int or not 1 <= rows <= MAX_QUERY_MINUTE_ROWS
                    or type(sql_calls) is not int or sql_calls < 1):
                raise ValueError("replay_selected_call_shape_unsupported")
        elif (sql_calls != expected_sql
                or kind == "shadow_monitor_state" and not 0 < payload_bytes <= MAX_SHADOW_BYTES
                or kind in {"dataset:program_flow", "realtime_latest", "realtime_second_bar"}
                and not isinstance(rows, int)
                or kind in {"dataset:program_flow", "realtime_latest", "realtime_second_bar"}
                and not 1 <= rows <= 200):
            raise ValueError("replay_selected_call_shape_unsupported")
        supported.append(ReplayCall(
            first["offset"] - window_start_seconds, kind, int(payload_bytes),
            int(rows or 0), call_id, str(domains.get(call_id, {}).get("flush_id") or ""),
            tuple((key, value) for key, value in
                  domains.get(call_id, {}).get("domain_counts", {}).items()
                  if key in {"bar_shape_version", "bar_changed_rows", "observations",
                             "metadata_suppressed_rows", "revision_insert_rows",
                             "revision_history_enabled", "duplicate_input_keys"}
                  and type(value) is int),
        ))
        if kind == "query_minute" and query_minute_scenario == "recorded_counts":
            _recorded_query_minute_shape(supported[-1])
    if not supported:
        raise ValueError("replay_profile_has_no_supported_calls")
    if sum(call.rows_attempted for call in supported if call.kind == "query_minute") > MAX_QUERY_MINUTE_TOTAL_ROWS:
        raise ValueError("replay_query_minute_total_row_limit")
    supported.sort(key=lambda item: (item.offset_seconds, item.source_call_id))
    return ReplayProfile(
        trace_id, selection, source_calls, len(starts), tuple(supported), {},
        window_start_seconds, window_end_seconds,
        include_writer_kinds, exclude_writer_kinds,
        query_minute_scenario=query_minute_scenario,
    )


def compile_replay_profile(report: dict, seconds: int, *,
                           window_start_seconds: float = 0,
                           window_end_seconds: float | None = None,
                           include_writer_kinds: tuple[str, ...] = (),
                           exclude_writer_kinds: tuple[str, ...] = ()) -> ReplayProfile:
    """Use timing/size metadata only; the diagnostic report has no SQL parameters."""
    if report.get("state") != "completed" or report.get("kind") != "measure":
        raise ValueError("replay_profile_requires_completed_measurement")
    phase = report.get("result", {}).get("phase", {})
    raw = phase.get("db_calls_raw", {})
    if (phase.get("state") != "complete" or phase.get("db_calls", {}).get("state") != "complete"
            or raw.get("coverage") != "opt_in_observed_calls_only"
            or raw.get("dropped") != 0 or raw.get("raw_truncated")
            or raw.get("truncated") or not isinstance(raw.get("calls"), list)):
        raise ValueError("replay_profile_capture_incomplete")
    duration = float(phase.get("elapsed_seconds", 0))
    end = duration if window_end_seconds is None else window_end_seconds
    if (not 5 <= duration <= MAX_REPLAY_SECONDS - 5
            or not math.isfinite(window_start_seconds) or not math.isfinite(end)
            or not 0 <= window_start_seconds < end <= duration
            or seconds < end - window_start_seconds + 5
            or seconds > MAX_REPLAY_SECONDS):
        raise ValueError("replay_profile_duration_out_of_bounds")
    if (len(include_writer_kinds) > 20 or len(exclude_writer_kinds) > 20
            or any(not kind or len(kind) > 100 for kind in
                   (*include_writer_kinds, *exclude_writer_kinds))
            or set(include_writer_kinds) & set(exclude_writer_kinds)):
        raise ValueError("invalid_replay_writer_selection")
    source_calls = raw["calls"]
    if len(source_calls) > 5000:
        raise ValueError("replay_profile_call_limit")
    started = datetime.fromisoformat(phase["started_at_utc"]).timestamp()
    shadow_sizes = {
        sample.get("db_call_id"): sample.get("payload_bytes_estimated")
        for sample in phase.get("market_bar_saves", {}).get("writer_transactions", {})
            .get("shadow_monitor_state", {}).get("call_samples", [])
    }
    supported: list[ReplayCall] = []
    omitted: dict[str, int] = {}
    window_calls = 0
    for call in source_calls:
        kind = str(call.get("writer_kind", "UNREGISTERED"))
        offset = float(call.get("started_at", -1)) - started
        if not window_start_seconds <= offset <= end:
            continue
        if include_writer_kinds and kind not in include_writer_kinds:
            continue
        if kind in exclude_writer_kinds:
            continue
        window_calls += 1
        is_success = (call.get("access_mode") == "write"
                      and call.get("outcome") == "committed"
                      and call.get("commits") == 1)
        payload_bytes = shadow_sizes.get(call.get("call_id"))
        if is_success and kind == "news_job_claim" and call.get("sql_calls") == 2:
            supported.append(ReplayCall(
                offset - window_start_seconds, kind,
                rows_attempted=int(call.get("rows_attempted") or 0),
                source_call_id=str(call.get("call_id") or ""),
            ))
        elif (is_success and kind == "shadow_monitor_state" and call.get("sql_calls") == 2
              and isinstance(payload_bytes, (int, float))
              and 0 < payload_bytes <= MAX_SHADOW_BYTES):
            supported.append(ReplayCall(offset - window_start_seconds, kind,
                                        int(payload_bytes),
                                        rows_attempted=int(call.get("rows_attempted") or 0),
                                        source_call_id=str(call.get("call_id") or "")))
        else:
            omitted[kind] = omitted.get(kind, 0) + 1
    if include_writer_kinds and omitted:
        raise ValueError("replay_selected_calls_unsupported")
    if not supported:
        raise ValueError("replay_profile_has_no_supported_calls")
    if len(supported) > MAX_REPLAY_CALLS:
        raise ValueError("replay_profile_selected_call_limit")
    return ReplayProfile(str(report["run_id"]), end - window_start_seconds,
                         len(source_calls), window_calls,
                         tuple(sorted(supported, key=lambda item: item.offset_seconds)),
                         omitted, window_start_seconds, end,
                         include_writer_kinds, exclude_writer_kinds)


def dedicated_database_url(live_url: str, explicit_url: str = "") -> str:
    """Reuse the existing integration runner's strict URL validation."""
    from scripts.run_postgres_access_integration import _resolve_target_url

    target = _resolve_target_url(live_url, explicit_url)
    if urlsplit(target).path.lstrip("/") != TEST_DATABASE_NAME:
        raise ValueError("replay_requires_dedicated_database")
    return target


class _ReplayStore(PostgresQueryStore):
    def _connect(self):
        import psycopg

        return psycopg.connect(self._database_url, connect_timeout=5,
                               options="-c statement_timeout=5000")


def _shadow_document(target_bytes: int) -> dict:
    origin = datetime(2026, 10, 1, 9, 0, tzinfo=timezone(timedelta(hours=9)))
    bars = []
    # The shape follows the real checkpoint frame contract. This is a size
    # approximation, not a copy of market data or a claim of identical WAL.
    for index in range(min(2200, max(1, target_bytes // 620))):
        code = f"{index % 20:06d}"
        started = origin + timedelta(minutes=index // 20)
        ended = started + timedelta(minutes=1)
        key = f"{started.isoformat()}/{code}"
        bars.append({
            "revision_id": str(uuid5(NAMESPACE_URL, key)), "observation_key": key,
            "code": code, "bar_start": started.isoformat(), "bar_end": ended.isoformat(),
            "available_at": ended.isoformat(), "open": 10000 + index,
            "high": 10100 + index, "low": 9900 + index, "close": 10050 + index,
            "volume": 100000 + index, "trade_value_million_won": 100 + index,
            "session_finalized": True, "capture_quality": "complete",
            "finalization_source": "market_event", "session_profile": "krx-regular/v1",
            "research_session": "2026-10-01", "market_session": "regular",
            "market_phase": "continuous", "schedule_version": "krx-v1",
        })
    return {"schema_version": 1, "cursor": 0, "session_profile": "krx-regular/v1",
            "strategy_config": {}, "quality": {"reason": "diagnostic_replay"},
            "strategy_state": {"emitted_candidate_keys": []}, "universes": [], "bars": bars}


def _synthetic_rows(call: ReplayCall, prefix: str, index: int) -> list[dict]:
    """Version 1 representative shapes; original market payloads are unavailable."""
    origin = datetime(2099, 1, 1, 9, tzinfo=timezone(timedelta(hours=9)))
    observed = origin.timestamp() + call.offset_seconds
    values = []
    for item in range(call.rows_attempted):
        code = f"{prefix}{item:03d}"
        if call.kind == "realtime_latest":
            values.append({"event_type": "trade", "item_key": code,
                           "received_at": observed,
                           "event": {"type": "trade", "payload": {
                               "code": code, "price": 10000 + index,
                               "volume": 100 + index, "market_cap_eok": 100000,
                           }}})
        elif call.kind == "realtime_second_bar":
            second = origin + timedelta(seconds=int(call.offset_seconds))
            values.append({"trading_date": "2099-01-01", "trade_second": second.strftime("%H:%M:%S"),
                           "code": code, "market": "KRX", "open": 10000,
                           "high": 10100 + index, "low": 9900, "close": 10000 + index,
                           "volume": 100 + index, "trade_value_won": 1_000_000 + index,
                           "trade_count": index + 1, "available_at": observed})
        elif call.kind == "dataset:program_flow":
            second = origin + timedelta(seconds=int(call.offset_seconds))
            values.append({"subject": code, "snapshot_key": f"20990101:REALTIME:{second:%H%M%S}",
                           "payload": {"market": "KRX", "rows": [{
                               "available": True, "source": "diagnostic_synthetic_0w",
                               "trade_time": second.strftime("%H%M%S"), "market": "KRX",
                               "net_buy_quantity": index + 1, "net_buy_quantity_change": 1,
                               "net_buy_amount_million_won": index + 1,
                               "net_buy_amount_change_million_won": 1,
                           }]}})
    return values


def _query_minute_rows(rows: int, prefix: str, index: int, scenario: str) -> list[dict]:
    """A declared synthetic page recipe; original keys and mutations are unknown."""
    origin = datetime(2099, 1, 5, 8, tzinfo=timezone(timedelta(hours=9)))
    code = f"{prefix}minute-{index}" if scenario == "fresh_page" else f"{prefix}minute"
    available = (origin + timedelta(days=2)).timestamp() + max(0, index + 1)
    values = []
    for item in range(rows):
        minute = origin + timedelta(minutes=item)
        close = 10000 + (max(0, index + 1) if scenario == "one_changed_bar" and item == 0 else 0)
        values.append({"trading_date": minute.date().isoformat(), "minute": minute.strftime("%H:%M"),
                       "code": code, "market": "KRX", "open": 10000,
                       "high": max(10100, close), "low": 9900, "close": close,
                       "volume": 100, "trade_value_million_won": 1,
                       "updated_at": available, "session_finalized": True})
    return values


def _query_minute_observations(values: list[dict]) -> list[tuple]:
    from kiwoom_monitor.domain.market_data_contract import (
        DataCompleteness, DataValueKind, ObservationOrigin,
    )
    from .market_observations import bar_observation_key, minute_bar_observation

    observations = [minute_bar_observation(
        value, origin=ObservationOrigin.QUERY, completeness=DataCompleteness.COMPLETE,
        source="kiwoom-ka10080;trade_value=ohlcv_estimate", value_kind=DataValueKind.ESTIMATED,
    ) for value in values]
    return [(bar_observation_key(observation), observation) for observation in observations]


def _recorded_query_minute_rows(call: ReplayCall, prefix: str, index: int,
                                phase: str = "replay") -> list[dict]:
    counts = _recorded_query_minute_shape(call)
    unique = call.rows_attempted - counts["duplicate_input_keys"]
    values = _query_minute_rows(unique, prefix, index, "fresh_page")
    if phase in {"bar_seed", "revision_seed"}:
        changed = counts["bar_changed_rows" if phase == "bar_seed" else "revision_insert_rows"]
        for position, value in enumerate(values):
            value["updated_at"] -= 1
            if position < changed:
                value["close"] = 9999 if phase == "bar_seed" else 9998
    elif phase == "replay":
        # All extra occurrences match their key's final value. The real writer
        # still executes its existing duplicate-key ordering and revision logic.
        values += [dict(values[position % unique])
                   for position in range(counts["duplicate_input_keys"])]
    else:
        raise ValueError("invalid_replay_query_minute_fixture_phase")
    return values


def run_replay(profile: ReplayProfile, database_url: str, run_id: str,
               stop: Event, ready: Event, started: Event,
               measurement_done: Event) -> dict:
    """Run real store methods; every connection is forced to the test DB."""
    import psycopg

    minute_calls = [call for call in profile.supported_calls if call.kind == "query_minute"]
    if (not re.fullmatch(r"[A-Za-z0-9-]{1,80}", run_id)
            or not profile.supported_calls or len(profile.supported_calls) > MAX_REPLAY_CALLS
            or not 0 < profile.duration_seconds <= MAX_REPLAY_SECONDS - 5
            or any(call.kind not in TRACE_SYNTHETIC_KINDS
                   or not math.isfinite(call.offset_seconds)
                   or not 0 <= call.offset_seconds <= profile.duration_seconds
                   or call.kind in {"realtime_latest", "realtime_second_bar", "dataset:program_flow"}
                   and not 1 <= call.rows_attempted <= 200
                   or call.kind == "query_minute" and
                   (type(call.rows_attempted) is not int or not 1 <= call.rows_attempted <= MAX_QUERY_MINUTE_ROWS)
                   for call in profile.supported_calls)
            or minute_calls and profile.query_minute_scenario not in QUERY_MINUTE_SCENARIOS
            or not minute_calls and profile.query_minute_scenario
            or sum(call.rows_attempted for call in minute_calls) > MAX_QUERY_MINUTE_TOTAL_ROWS
            or list(profile.supported_calls) != sorted(profile.supported_calls,
                                                       key=lambda call: call.offset_seconds)):
        raise ValueError("invalid_replay_execution_profile")
    recorded_counts = profile.query_minute_scenario == "recorded_counts"
    if recorded_counts:
        for call in minute_calls:
            _recorded_query_minute_shape(call)
    selected_kinds = {call.kind for call in profile.supported_calls}
    required_tables = {"news_job_claim": "central_news_jobs",
                       "shadow_monitor_state": "central_shadow_monitor_state",
                       "realtime_latest": "central_realtime_latest",
                       "realtime_second_bar": "central_second_trade_bars",
                       "dataset:program_flow": "central_dataset_snapshots",
                       "query_minute": "central_minute_bars"}
    prefix = f"diagnostic-replay-{run_id}-"
    minute_codes = sorted({value["code"] for index, call in enumerate(profile.supported_calls)
                           if call.kind == "query_minute"
                           for value in _query_minute_rows(
                               1, prefix, index, "fresh_page" if recorded_counts else profile.query_minute_scenario)})
    minute_subjects = [f"{code}:KRX" for code in minute_codes]
    with psycopg.connect(database_url, autocommit=True, connect_timeout=5,
                         options="-c default_transaction_read_only=on -c statement_timeout=5000") as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            if cursor.fetchone()[0] != TEST_DATABASE_NAME:
                raise RuntimeError("replay_test_database_preflight_failed")
            for kind in selected_kinds:
                cursor.execute("SELECT to_regclass(%s)", (required_tables[kind],))
                if cursor.fetchone()[0] is None:
                    raise RuntimeError("replay_test_database_table_missing")
            if minute_calls:
                for table in ("central_market_data_observation_meta", "central_observation_revisions"):
                    cursor.execute("SELECT to_regclass(%s)", (table,))
                    if cursor.fetchone()[0] is None:
                        raise RuntimeError("replay_test_database_table_missing")
                for table, column, values in (
                    ("central_minute_bars", "code", minute_codes),
                    ("central_market_data_observation_meta", "subject", minute_subjects),
                    ("central_observation_revisions", "subject", minute_subjects),
                ):
                    cursor.execute(f"SELECT 1 FROM {table} WHERE {column}=ANY(%s) LIMIT 1", (values,))
                    if cursor.fetchone():
                        raise RuntimeError("replay_query_minute_scope_not_empty")
            if any(call.kind == "news_job_claim" for call in profile.supported_calls):
                cursor.execute("SELECT 1 FROM central_news_jobs LIMIT 1")
                if cursor.fetchone():
                    raise RuntimeError("replay_requires_empty_news_job_test_table")
    store = _ReplayStore(database_url, shadow_checkpoint_frames_enabled=False)
    # Keep the history switch on separate store instances. Other replay lanes
    # may run concurrently and must never observe a temporary flag mutation.
    no_history_store = (_ReplayStore(database_url, observation_history_enabled=False,
                                    shadow_checkpoint_frames_enabled=False)
                        if recorded_counts else None)
    monitor_id = f"diagnostic-replay-{run_id}"
    counts = {kind: 0 for kind in sorted(selected_kinds)}
    skipped_backpressure = {kind: 0 for kind in counts}
    errors: list[str] = []
    shadow_document = _shadow_document(max((call.payload_bytes for call in profile.supported_calls
                                            if call.kind == "shadow_monitor_state"), default=0))
    generated_bytes = len(json.dumps(shadow_document, ensure_ascii=False).encode("utf-8"))
    pending: list[tuple[object, ReplayCall, int]] = []
    completed_indices: set[int] = set()
    schedule_lags: list[float] = []
    submitted = 0
    verification: dict[str, dict] = {}
    replayed_calls = [
        {"source_call_id": call.source_call_id or None,
         "replay_call_id": uuid4().hex,
         "writer_kind": call.kind,
         "rows_attempted": call.rows_attempted,
         "source_domain_counts": dict(call.observed_domain_counts),
         "expected_domain_counts": (_recorded_query_minute_shape(call)
                                     if recorded_counts and call.kind == "query_minute" else None),
         "state": "queued"}
        for call in profile.supported_calls
    ]
    seed_rows = max((call.rows_attempted for call in minute_calls), default=0)
    minute_expected: dict[tuple, dict] = {}
    seeded_revision_rows = 0
    seeded_bar_rows = 0

    def minute_values(call: ReplayCall, index: int) -> list[dict]:
        return (_recorded_query_minute_rows(call, prefix, index) if recorded_counts else
                _query_minute_rows(call.rows_attempted, prefix, index, profile.query_minute_scenario))

    def remember_minutes(values: list[dict]) -> None:
        for value in values:
            key = (value["trading_date"], value["minute"], value["code"])
            # Unchanged canonical values preserve their original updated_at.
            previous = minute_expected.get(key)
            if previous is None or any(previous[name] != value[name] for name in
                                       ("open", "high", "low", "close", "volume", "trade_value_million_won")):
                minute_expected[key] = value

    def collect(future, call: ReplayCall, index: int) -> None:
        try:
            schedule_lags.append(future.result())
            counts[call.kind] += 1
            completed_indices.add(index)
            replayed_calls[index]["state"] = "committed"
        except Exception as error:
            errors.append(type(error).__name__)
            replayed_calls[index]["state"] = "failed"
            replayed_calls[index]["error_type"] = type(error).__name__
            stop.set()

    try:
        if recorded_counts:
            for index, call in enumerate(profile.supported_calls):
                if call.kind != "query_minute":
                    continue
                if stop.is_set():
                    break
                shape = _recorded_query_minute_shape(call)
                if shape["revision_history_enabled"]:
                    seed = _recorded_query_minute_rows(call, prefix, index, "revision_seed")
                    store.replace_minute_bars(seed, observations=_query_minute_observations(seed))
                    seeded_revision_rows += len(seed)
                seed = _recorded_query_minute_rows(call, prefix, index, "bar_seed")
                no_history_store.replace_minute_bars(seed, observations=_query_minute_observations(seed))
                seeded_bar_rows += len(seed)
                remember_minutes(seed)
        elif minute_calls and profile.query_minute_scenario != "fresh_page":
            seed = _query_minute_rows(seed_rows, prefix, -1, profile.query_minute_scenario)
            store.replace_minute_bars(seed, observations=_query_minute_observations(seed))
            remember_minutes(seed)
        ready.set()
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="diag-claim") as claims, \
                ThreadPoolExecutor(max_workers=1, thread_name_prefix="diag-shadow") as shadows, \
                ThreadPoolExecutor(max_workers=1, thread_name_prefix="diag-realtime") as realtime, \
                ThreadPoolExecutor(max_workers=1, thread_name_prefix="diag-program") as program, \
                ThreadPoolExecutor(max_workers=1, thread_name_prefix="diag-query-minute") as minutes:
            if not started.wait(timeout=10):
                raise RuntimeError("replay_sampler_did_not_start")
            start = time.monotonic()

            def execute(call: ReplayCall, index: int) -> float:
                from .postgres_access import db_call_request_id

                lag = max(0.0, time.monotonic() - start - call.offset_seconds) * 1000
                with db_call_request_id(replayed_calls[index]["replay_call_id"]):
                    if call.kind == "news_job_claim":
                        if store.claim_news_jobs(limit=1):
                            raise RuntimeError("replay_claimed_unrelated_test_job")
                    elif call.kind == "shadow_monitor_state":
                        store.save_shadow_monitor_state(monitor_id, {**shadow_document, "cursor": index})
                    elif call.kind == "realtime_latest":
                        store.save_realtime_snapshots(_synthetic_rows(call, prefix, index))
                    elif call.kind == "realtime_second_bar":
                        store.save_second_trade_bars(_synthetic_rows(call, prefix, index))
                    elif call.kind == "dataset:program_flow":
                        store.save_dataset_snapshots([
                            ("program_flow", value["subject"], value["snapshot_key"], value["payload"], None)
                            for value in _synthetic_rows(call, prefix, index)
                        ])
                    elif call.kind == "query_minute":
                        values = minute_values(call, index)
                        observations = _query_minute_observations(values)
                        lag = max(0.0, time.monotonic() - start - call.offset_seconds) * 1000
                        minute_store = (no_history_store if recorded_counts and not
                                        _recorded_query_minute_shape(call)["revision_history_enabled"] else store)
                        minute_store.replace_minute_bars(values, observations=observations)
                return lag

            for index, call in enumerate(profile.supported_calls):
                if stop.wait(max(0, start + call.offset_seconds - time.monotonic())):
                    break
                completed = [item for item in pending if item[0].done()]
                for item in completed:
                    collect(*item)
                pending[:] = [item for item in pending if not item[0].done()]
                if stop.is_set():
                    break
                executor = (claims if call.kind == "news_job_claim" else
                            shadows if call.kind == "shadow_monitor_state" else
                            program if call.kind == "dataset:program_flow" else
                            minutes if call.kind == "query_minute" else realtime)
                # At most MAX_REPLAY_CALLS futures are queued. Realtime writers
                # share one FIFO lane, matching the collector's serialized flush.
                pending.append((executor.submit(execute, call, index), call, index))
                replayed_calls[index]["state"] = "submitted"
                submitted += 1
            for item in pending:
                collect(*item)
    finally:
        # Validation and cleanup stay outside the measured interval.
        measurement_done.wait(timeout=MAX_REPLAY_SECONDS + 30)
        with store._connect() as connection, connection.cursor() as cursor:
            if minute_calls:
                completed_minutes = [(index, profile.supported_calls[index])
                                     for index in sorted(completed_indices)
                                     if profile.supported_calls[index].kind == "query_minute"]
                for index, call in completed_minutes:
                    remember_minutes(minute_values(call, index))
                try:
                    cursor.execute("SELECT trading_date::text,to_char(minute,'HH24:MI'),code,"
                                   "open,high,low,close,volume,trade_value_million_won,updated_at "
                                   "FROM central_minute_bars WHERE code=ANY(%s)", (minute_codes,))
                    saved = cursor.fetchall()
                    columns = ("open", "high", "low", "close", "volume", "trade_value_million_won", "updated_at")
                    values_match = len(saved) == len(minute_expected) and all(
                        tuple(minute_expected.get(tuple(row[:3]), {}).get(name) for name in columns) == tuple(row[3:])
                        for row in saved)
                    cursor.execute("SELECT count(*) FROM central_market_data_observation_meta WHERE subject=ANY(%s)", (minute_subjects,))
                    metadata_rows = cursor.fetchone()[0]
                    cursor.execute("SELECT count(*) FROM central_observation_revisions WHERE subject=ANY(%s)", (minute_subjects,))
                    revision_rows = cursor.fetchone()[0]
                    if recorded_counts:
                        expected_revisions = seeded_revision_rows + sum(
                            _recorded_query_minute_shape(call)["revision_insert_rows"]
                            for _, call in completed_minutes)
                    elif profile.query_minute_scenario == "fresh_page":
                        expected_revisions = sum(call.rows_attempted for _, call in completed_minutes)
                    else:
                        expected_revisions = seed_rows + (
                            len(completed_minutes) if profile.query_minute_scenario == "one_changed_bar" else 0)
                    verification["query_minute"] = {"expected_rows": len(minute_expected), "actual_rows": len(saved),
                                                     "metadata_rows": metadata_rows, "revision_rows": revision_rows,
                                                     "expected_revision_rows": expected_revisions, "values_match": values_match}
                    if not values_match or metadata_rows != len(minute_expected) or revision_rows != expected_revisions:
                        errors.append("replay_query_minute_fixture_mismatch")
                except Exception as error:
                    errors.append("replay_query_minute_verification_" + type(error).__name__)
                    # A failed verification SELECT must not prevent scoped cleanup.
                    connection.rollback()
                for table, column, values in (
                    ("central_observation_revisions", "subject", minute_subjects),
                    ("central_market_data_observation_meta", "subject", minute_subjects),
                    ("central_minute_bars", "code", minute_codes),
                ):
                    cursor.execute(f"DELETE FROM {table} WHERE {column}=ANY(%s)", (values,))
                    cursor.execute(f"SELECT count(*) FROM {table} WHERE {column}=ANY(%s)", (values,))
                    if cursor.fetchone()[0]:
                        raise RuntimeError("replay_cleanup_incomplete")
            codes = [f"{prefix}{item:03d}" for item in range(200)]
            scopes = {
                "shadow_monitor_state": ("central_shadow_monitor_state", "monitor_id=%s", (monitor_id,)),
                "realtime_latest": ("central_realtime_latest", "event_type='trade' AND item_key=ANY(%s)", (codes,)),
                "realtime_second_bar": ("central_second_trade_bars", "trading_date='2099-01-01' AND code=ANY(%s)", (codes,)),
                "dataset:program_flow": ("central_dataset_snapshots", "kind='program_flow' AND subject=ANY(%s)", (codes,)),
            }
            for kind in sorted(selected_kinds - {"news_job_claim", "query_minute"}):
                expected = set()
                for index in completed_indices:
                    call = profile.supported_calls[index]
                    if call.kind != kind:
                        continue
                    if kind == "shadow_monitor_state":
                        expected.add(monitor_id)
                    else:
                        for value in _synthetic_rows(call, prefix, index):
                            expected.add(value["item_key"] if kind == "realtime_latest" else
                                         (value["code"], value["trade_second"]) if kind == "realtime_second_bar" else
                                         (value["subject"], value["snapshot_key"]))
                table, where, parameters = scopes[kind]
                cursor.execute(f"SELECT count(*) FROM {table} WHERE {where}", parameters)
                actual = cursor.fetchone()[0]
                verification[kind] = {"expected_rows": len(expected), "actual_rows": actual}
                if actual != len(expected):
                    errors.append("replay_fixture_row_count_mismatch")
                cursor.execute(f"DELETE FROM {table} WHERE {where}", parameters)
                cursor.execute(f"SELECT count(*) FROM {table} WHERE {where}", parameters)
                if cursor.fetchone()[0]:
                    raise RuntimeError("replay_cleanup_incomplete")
    ordered_lags = sorted(schedule_lags)
    return {"state": "aborted" if stop.is_set() or errors else "complete",
            "completed_calls": counts, "skipped_backpressure": skipped_backpressure,
            "submitted_calls": submitted, "unsubmitted_calls": len(profile.supported_calls) - submitted,
            "schedule_lag_ms": {"max": max(ordered_lags, default=0),
                                "p95": ordered_lags[(len(ordered_lags) * 95 + 99) // 100 - 1] if ordered_lags else 0},
            "timing_preserved": max(ordered_lags, default=0) <= 100 and submitted == len(profile.supported_calls),
            "fixture_verification": verification, "fidelity": profile.fidelity,
            "replayed_calls": replayed_calls,
            "query_minute_recipe": ({"version": 2 if recorded_counts else 1, "scenario": profile.query_minute_scenario,
                                      "input_rows": sum(call.rows_attempted for call in minute_calls),
                                      "seed_rows": (seeded_bar_rows if recorded_counts else
                                                    seed_rows if profile.query_minute_scenario != "fresh_page" else 0),
                                      "seed_revision_rows": seeded_revision_rows if recorded_counts else None,
                                      "source_shape_calls": sum(bool(call.observed_domain_counts) for call in minute_calls),
                                      "source_shape_missing_calls": sum(not call.observed_domain_counts for call in minute_calls),
                                      "scope_note": ("recorded per-call attempted/changed/duplicate/revision counts; independent seeded subject per call; identical duplicate occurrences; canonical and revision baselines prepared independently; synthetic KRX complete observations; original new/update split, values, venue, key overlap and lock/WAL distribution are unknown"
                                                     if recorded_counts else
                                                     "recorded row counts; synthetic KRX complete query observations; one shared subject or fresh subject per call; original changes, venue, key overlap and metadata flags are unknown")}
                                     if minute_calls else None),
            "errors": errors, "generated_shadow_payload_bytes": generated_bytes,
            "source_calls": profile.source_calls,
            "window_calls": profile.window_calls,
            "selected_calls": len(profile.supported_calls),
            "omitted_kinds": profile.omitted_kinds,
            "window_start_seconds": profile.window_start_seconds,
            "window_end_seconds": profile.window_end_seconds,
            "selected_writer_kinds": profile.selected_writer_kinds,
            "excluded_writer_kinds": profile.excluded_writer_kinds,
            "scope_note": "representative inputs and recorded start offsets; realtime writers share one lane; original keys, JSON size, WAL and lock overlap are not reconstructed"}
