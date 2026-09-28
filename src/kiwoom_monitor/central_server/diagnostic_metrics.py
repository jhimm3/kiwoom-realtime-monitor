"""Bounded in-memory measurements for all PostgreSQL market-bar saves."""

from __future__ import annotations

from collections import Counter, deque
from contextvars import ContextVar
from math import ceil
from os import getpid
from statistics import median
from threading import Lock
from time import monotonic, time
from uuid import uuid4


_LOCK = Lock()
_SAVES: deque[dict[str, float | int | str]] = deque(maxlen=50_000)
_WRITERS: deque[dict[str, object]] = deque(maxlen=100_000)
_DB_CALLS: deque[dict[str, object]] = deque(maxlen=50_000)
_DROPPED_SAVES = 0
_DROPPED_WRITERS = 0
_DROPPED_DB_CALLS = 0
_CAPTURE_ENABLED = False
_CAPTURE_SESSION_ID: str | None = None
_CAPTURE_REFRESH_AT = 0.0
_CAPTURE_EXPIRES_MONOTONIC = 0.0
_CAPTURE_LOCK = Lock()
_PRODUCER_ID = uuid4().hex
CURRENT_API_ID: ContextVar[str] = ContextVar("diagnostic_api_id", default="")
CURRENT_FLUSH_ID: ContextVar[str] = ContextVar("diagnostic_flush_id", default="")
_FIELDS = ("connect_ms", "bars_ms", "metadata_ms", "revisions_ms",
           "revision_sources_ms", "revision_locks_ms", "revision_lookup_ms",
           "revision_rows_ms", "revision_insert_execute_ms",
           "commit_ms", "close_ms", "total_ms")


def refresh_capture_state(*, force: bool = False) -> dict[str, object]:
    """Refresh the short-lived capture lease cache and discard data when off."""
    global _CAPTURE_ENABLED, _CAPTURE_REFRESH_AT, _CAPTURE_EXPIRES_MONOTONIC
    global _CAPTURE_SESSION_ID
    now_mono = monotonic()
    if not force and _CAPTURE_ENABLED and now_mono < _CAPTURE_EXPIRES_MONOTONIC:
        # While enabled, inspect the control file at most twice per second so a
        # manual OFF takes effect promptly without filesystem I/O per DB write.
        if now_mono < _CAPTURE_REFRESH_AT:
            return {"enabled": True, "expires_at": None}
    elif not force and not _CAPTURE_ENABLED and now_mono < _CAPTURE_REFRESH_AT:
        return {"enabled": False, "expires_at": None}

    with _CAPTURE_LOCK:
        now_mono = monotonic()
        if not force and _CAPTURE_ENABLED and now_mono < _CAPTURE_EXPIRES_MONOTONIC \
                and now_mono < _CAPTURE_REFRESH_AT:
            return {"enabled": True, "expires_at": None}
        if not force and not _CAPTURE_ENABLED and now_mono < _CAPTURE_REFRESH_AT:
            return {"enabled": False, "expires_at": None}
        from .diagnostic_workloads import capture_status

        status = capture_status()
        was_enabled = _CAPTURE_ENABLED
        previous_session = _CAPTURE_SESSION_ID
        _CAPTURE_ENABLED = bool(status["enabled"])
        _CAPTURE_SESSION_ID = str(status["session_id"]) if status.get("session_id") else None
        remaining = (max(0.0, float(status["expires_at"]) - time())
                     if _CAPTURE_ENABLED and status["expires_at"] is not None else 0.0)
        _CAPTURE_EXPIRES_MONOTONIC = now_mono + remaining
        _CAPTURE_REFRESH_AT = min(_CAPTURE_EXPIRES_MONOTONIC, now_mono + 0.5) \
            if _CAPTURE_ENABLED else now_mono + 0.5
        if was_enabled and (not _CAPTURE_ENABLED or previous_session != _CAPTURE_SESSION_ID):
            clear_metrics()
        return status


def _capture_is_enabled() -> bool:
    if _CAPTURE_ENABLED:
        now_mono = monotonic()
        if now_mono < _CAPTURE_EXPIRES_MONOTONIC and now_mono < _CAPTURE_REFRESH_AT:
            return True
    return bool(refresh_capture_state().get("enabled"))


def clear_metrics() -> None:
    global _DROPPED_SAVES, _DROPPED_WRITERS, _DROPPED_DB_CALLS
    with _LOCK:
        _SAVES.clear()
        _WRITERS.clear()
        _DB_CALLS.clear()
        _DROPPED_SAVES = 0
        _DROPPED_WRITERS = 0
        _DROPPED_DB_CALLS = 0


def capture_session_token() -> tuple[bool, str | None]:
    """Bind a DB call to the capture generation active when it begins."""
    return _capture_is_enabled(), _CAPTURE_SESSION_ID


def record_db_call(record: dict[str, object], *,
                   capture_token: tuple[bool, str | None]) -> None:
    """Append one opt-in DB call without mixing capture sessions."""
    if not capture_token[0]:
        return
    refresh_capture_state(force=True)
    if not _CAPTURE_ENABLED or _CAPTURE_SESSION_ID != capture_token[1]:
        return
    global _DROPPED_DB_CALLS
    with _LOCK:
        if not _CAPTURE_ENABLED or _CAPTURE_SESSION_ID != capture_token[1]:
            return
        if len(_DB_CALLS) == _DB_CALLS.maxlen:
            _DROPPED_DB_CALLS += 1
        _DB_CALLS.append(record)


def summarize_db_calls(start: float, end: float, *, mode: str = "summary",
                       limit: int = 200, slow_ms: int = 500) -> dict[str, object]:
    if mode not in {"summary", "verbose", "raw"}:
        raise ValueError("unsupported DB diagnostic mode")
    capture = refresh_capture_state(force=True)
    with _LOCK:
        rows = [row for row in _DB_CALLS if start <= float(row["at"]) < end]
        retained_from = float(_DB_CALLS[0]["at"]) if _DB_CALLS else None
        dropped = _DROPPED_DB_CALLS

    def describe(values: list[float]) -> dict[str, float | int | None]:
        if not values:
            return {"n": 0, "min": None, "p50": None, "p90": None,
                    "p95": None, "p99": None, "max": None}
        ordered = sorted(values)
        return {"n": len(ordered), "min": ordered[0],
                "p50": median(ordered), "p90": ordered[ceil(len(ordered) * .90) - 1],
                "p95": ordered[ceil(len(ordered) * .95) - 1],
                "p99": ordered[ceil(len(ordered) * .99) - 1], "max": ordered[-1]}

    def aggregate(access_mode: str) -> dict[str, dict[str, object]]:
        selected_rows = [row for row in rows
                         if str(row.get("access_mode", "write")) == access_mode]
        families = sorted({(str(row["writer_family"]), str(row["writer_kind"]))
                           for row in selected_rows})
        groups: dict[str, dict[str, object]] = {}
        for family, kind in families:
            selected = [row for row in selected_rows if row["writer_family"] == family
                        and row["writer_kind"] == kind]
            key = f"{family}/{kind}"
            groups[key] = {
                "writer_family": family, "writer_kind": kind,
                "access_mode": access_mode,
                "calls": len(selected),
                "transactions": sum(int(row["transactions"]) for row in selected
                                    if row["transactions"] is not None),
                "transactions_unavailable_calls": sum(row["transactions"] is None
                                                       for row in selected),
                "commits": sum(int(row["commits"]) for row in selected),
                "rollbacks": sum(int(row["rollbacks"]) for row in selected),
                "rows_attempted": sum(int(row["rows_attempted"]) for row in selected
                                      if row["rows_attempted"] is not None),
                "rows_attempted_unavailable_calls": sum(row["rows_attempted"] is None
                                                          for row in selected),
                "sql_calls": sum(int(row["sql_calls"]) for row in selected),
                "db_error_calls": sum(any(error["stage"] != "observer" for error in row["errors"])
                                      for row in selected),
                "observer_error_calls": sum(any(error["stage"] == "observer" for error in row["errors"])
                                            for row in selected),
                "outcomes": dict(Counter(str(row["outcome"]) for row in selected)),
                **{field: describe([float(row[field]) for row in selected if row[field] is not None])
                   for field in ("connection_acquire_ms", "execute_ms", "commit_ms",
                                 "rollback_ms", "close_ms", "total_ms")},
            }
            if mode in {"verbose", "raw"}:
                groups[key]["slow_calls"] = [
                    {"call_id": row["call_id"], "outcome": row["outcome"],
                     "total_ms": row["total_ms"], "commit_ms": row["commit_ms"],
                     "errors": row["errors"]}
                    for row in selected if float(row["total_ms"]) >= slow_ms
                    or (row["commit_ms"] is not None and float(row["commit_ms"]) >= slow_ms)
                ][:limit]
        return groups

    writers = aggregate("write")
    readers = aggregate("read")

    result: dict[str, object] = {
        "producer": {"pid": getpid(), "process_id": _PRODUCER_ID},
        "capture": capture, "coverage": "opt_in_observed_calls_only",
        "slow_threshold_ms": slow_ms,
        "retained_from": retained_from, "dropped": dropped,
        "truncated": bool(dropped and retained_from is not None and retained_from > start),
        "writers": writers, "readers": readers,
        "unregistered_calls": sum(row["writer_family"] == "UNREGISTERED"
                                                   for row in rows),
        "scope_note": "capture-only pilot; unobserved/raw PostgreSQL calls are not counted",
    }
    if mode == "raw":
        result["calls"] = rows[:limit]
        result["raw_truncated"] = len(rows) > limit
    return result


def copy_db_call_samples(start: float, end: float) -> dict[str, object]:
    """Freeze bounded raw calls without holding the registry lock during report work."""
    with _LOCK:
        rows = [row.copy() for row in _DB_CALLS if start <= float(row["at"]) < end]
        dropped = _DROPPED_DB_CALLS
        retained_from = float(_DB_CALLS[0]["at"]) if _DB_CALLS else None
    return {"calls": rows, "dropped": dropped, "retained_from": retained_from,
            "truncated": bool(dropped and retained_from is not None and retained_from > start),
            "producer": {"pid": getpid(), "process_id": _PRODUCER_ID},
            "session_id": _CAPTURE_SESSION_ID, "captured_at": time()}


def _commit_diagnostic_record(
    samples: list[dict[str, object]] | None, errors: list[str] | None,
    backend_pid: int, started_at: float | None,
    duration_ms: int, wal_timing_enabled: bool | None, wal_timing_error: str,
    *, ended_at: float | None = None, probe_incomplete: bool = False,
) -> dict[str, object]:
    final_at = (ended_at if ended_at is not None else
                float(started_at) + duration_ms / 1000.0 if started_at is not None else None)
    return {
        "backend_pid": backend_pid or None,
        "sampling_interval_ms": 25,
        "initial_delay_ms": 100,
        "commit_window": ({
            "started_at": started_at,
            "ended_at": final_at,
            "duration_ms": duration_ms,
        } if started_at is not None else None),
        "samples": [
            {"offset_ms": round((float(sample["at"])
                                  - float(started_at)) * 1000),
             "state": sample.get("state"),
             "wait_type": sample.get("wait_type"),
             "wait_event": sample.get("wait_event"),
             "blocking_pids": sample.get("blocking_pids", [])}
            for sample in (samples or [])
            if started_at is not None and final_at is not None
            and started_at <= float(sample["at"]) <= final_at
        ],
        "probe_errors": list(errors or []),
        "probe_pending_at_capture": probe_incomplete,
        "wal_io_timing_enabled_for_commit": wal_timing_enabled,
        "wal_io_timing_error": wal_timing_error or None,
    }


def record_market_bar_save(*, kind: str, rows: int, observations: int,
                           connect_ms: int, bars_ms: int, metadata_ms: int,
                           revisions_ms: int, commit_ms: int, close_ms: int,
                           total_ms: int, revision_lookup_statements: int = 0,
                           revision_lookup_keys: int = 0,
                           revision_insert_statements: int = 0,
                           revision_insert_rows: int = 0,
                           revision_sources_ms: int = 0, revision_locks_ms: int = 0,
                           revision_lookup_ms: int = 0, revision_rows_ms: int = 0,
                           revision_insert_execute_ms: int = 0,
                           commit_wait_samples: list[dict[str, object]] | None = None,
                           commit_probe_errors: list[str] | None = None,
                           commit_backend_pid: int = 0,
                           commit_started_at: float | None = None,
                           commit_ended_at: float | None = None,
                           commit_probe_incomplete: bool = False,
                           wal_timing_for_commit: bool | None = None,
                           wal_timing_error: str = "",
                           bar_statement_diagnostics: list[dict[str, object]] | None = None,
                           db_call_id: str | None = None) -> None:
    if not _capture_is_enabled():
        return
    value = {"at": time(), "kind": kind, "api_id": CURRENT_API_ID.get(),
             "db_call_id": db_call_id,
             "rows": rows, "observations": observations,
             "connect_ms": connect_ms, "bars_ms": bars_ms,
             "metadata_ms": metadata_ms, "revisions_ms": revisions_ms,
             "commit_ms": commit_ms, "close_ms": close_ms, "total_ms": total_ms,
             "revision_lookup_statements": revision_lookup_statements,
             "revision_lookup_keys": revision_lookup_keys,
             "revision_insert_statements": revision_insert_statements,
             "revision_insert_rows": revision_insert_rows,
             "revision_sources_ms": revision_sources_ms,
             "revision_locks_ms": revision_locks_ms,
             "revision_lookup_ms": revision_lookup_ms,
             "revision_rows_ms": revision_rows_ms,
             "revision_insert_execute_ms": revision_insert_execute_ms,
             "bar_statement_diagnostics": list(bar_statement_diagnostics or []),
             "commit_diagnostics": _commit_diagnostic_record(
                 commit_wait_samples, commit_probe_errors, commit_backend_pid,
                 commit_started_at, commit_ms, wal_timing_for_commit, wal_timing_error,
                 ended_at=commit_ended_at, probe_incomplete=commit_probe_incomplete,
             )}
    global _DROPPED_SAVES
    with _LOCK:
        if not _CAPTURE_ENABLED:
            return
        if len(_SAVES) == _SAVES.maxlen:
            _DROPPED_SAVES += 1
        _SAVES.append(value)


def record_writer_transaction(kind: str, rows: int, elapsed_ms: int, *,
                              commit_ms: int | None = None,
                              connect_ms: int | None = None,
                              execute_ms: int | None = None,
                              bytes_payload_estimate: int | None = None,
                              db_call_id: str | None = None) -> None:
    """Record one successful writer transaction; unavailable phases stay null.

    Current call sites invoke this only after their PostgreSQL connection context
    exits successfully. Do not infer commit latency from total elapsed time.
    """
    if not _capture_is_enabled():
        return
    global _DROPPED_WRITERS
    with _LOCK:
        if not _CAPTURE_ENABLED:
            return
        if len(_WRITERS) == _WRITERS.maxlen:
            _DROPPED_WRITERS += 1
        _WRITERS.append({"at": time(), "kind": kind,
                         "db_call_id": db_call_id,
                         "api_id": CURRENT_API_ID.get(),
                         "flush_id": CURRENT_FLUSH_ID.get(), "rows": rows,
                         "transactions": 1, "commits": 1,
                         "elapsed_ms": elapsed_ms,
                         "connect_ms": connect_ms, "execute_ms": execute_ms,
                         "commit_ms": commit_ms,
                         "bytes_payload_estimate": bytes_payload_estimate})


def summarize_market_bar_saves(start: float, end: float) -> dict[str, object]:
    capture = refresh_capture_state(force=True)
    with _LOCK:
        selected = [value for value in _SAVES if start <= float(value["at"]) < end]
        oldest = float(_SAVES[0]["at"]) if _SAVES else None
        oldest_writer = float(_WRITERS[0]["at"]) if _WRITERS else None
        dropped_saves = _DROPPED_SAVES
        dropped_writers = _DROPPED_WRITERS
        writers = [value for value in _WRITERS if start <= float(value["at"]) < end]
    def describe(values: list[int]) -> dict[str, float | int | None]:
        if not values:
            return {"median": None, "p90": None, "p95": None, "max": None}
        ordered = sorted(values)
        return {"min": ordered[0], "median": median(ordered),
                "p90": ordered[ceil(len(ordered)*0.90)-1],
                "p95": ordered[ceil(len(ordered)*0.95)-1],
                "p99": ordered[ceil(len(ordered)*0.99)-1], "max": ordered[-1]}
    return {
        "capture": capture,
        "retained_from": oldest,
        "writer_retained_from": oldest_writer,
        "truncated": bool(dropped_saves and oldest is not None and oldest > start),
        "writer_truncated": bool(dropped_writers and oldest_writer is not None and oldest_writer > start),
        "kinds": {
            kind: {
                "count": len(rows), "bar_rows": sum(int(row["rows"]) for row in rows),
                "observation_rows": sum(int(row["observations"]) for row in rows),
                "revision_lookup_statements": sum(
                    int(row["revision_lookup_statements"]) for row in rows
                ),
                "revision_lookup_keys": sum(
                    int(row["revision_lookup_keys"]) for row in rows
                ),
                "revision_insert_statements": sum(
                    int(row["revision_insert_statements"]) for row in rows
                ),
                "revision_insert_rows": sum(
                    int(row["revision_insert_rows"]) for row in rows
                ),
                **{field: describe([int(row[field]) for row in rows]) for field in _FIELDS},
                # Preserve the phase tuple per save so a high revision tail can
                # be compared with that same call's COMMIT and total latency.
                # The source ring is bounded and callers already filter by the
                # requested measurement window.
                "call_samples": [
                    {key: row[key] for key in (
                        "at", "api_id", "db_call_id", "rows", "observations",
                        "revision_lookup_statements", "revision_lookup_keys",
                        "revision_insert_statements", "revision_insert_rows",
                        "connect_ms", "bars_ms",
                        "metadata_ms", "revision_sources_ms", "revision_locks_ms",
                        "revision_lookup_ms", "revision_rows_ms",
                        "revision_insert_execute_ms", "revisions_ms", "commit_ms",
                        "close_ms", "total_ms", "bar_statement_diagnostics",
                    )} | {"commit_diagnostics": row["commit_diagnostics"]}
                    for row in rows
                ],
                "commit_diagnostics": {
                    "calls_with_probe_samples": sum(
                        bool(row["commit_diagnostics"]["samples"]) for row in rows
                    ),
                    "calls_with_probe_errors": sum(
                        bool(row["commit_diagnostics"]["probe_errors"]) for row in rows
                    ),
                    "calls_with_incomplete_probe": sum(
                        bool(row["commit_diagnostics"]["probe_pending_at_capture"])
                        for row in rows
                    ),
                    "wal_io_timing_enabled_calls": sum(
                        row["commit_diagnostics"]["wal_io_timing_enabled_for_commit"] is True
                        for row in rows
                    ),
                    "wal_io_timing_unavailable_calls": sum(
                        row["commit_diagnostics"]["wal_io_timing_enabled_for_commit"] is False
                        for row in rows
                    ),
                    "wait_event_samples": {
                        key: count
                        for key, count in sorted(Counter(
                            f"{sample.get('wait_type') or 'NONE'}:{sample.get('wait_event') or 'NONE'}"
                            for row in rows
                            for sample in row["commit_diagnostics"]["samples"]
                        ).items())
                    },
                    "scope_note": (
                        "wait samples are for the exact market-bar writer backend during its COMMIT; "
                        "sampling is diagnostic-only and may miss waits shorter than 100ms"
                    ),
                },
                "bar_statement_diagnostics": {
                    "execution_windows": sum(
                        len(row["bar_statement_diagnostics"]) for row in rows
                    ),
                    "sql_operations": sum(
                        int(item.get("sql_operations") or 0)
                        for row in rows for item in row["bar_statement_diagnostics"]
                    ),
                    "calls_with_probe_samples": sum(
                        any(item["samples"] for item in row["bar_statement_diagnostics"])
                        for row in rows
                    ),
                    "windows_with_incomplete_probe": sum(
                        bool(item["probe_pending_at_capture"])
                        for row in rows for item in row["bar_statement_diagnostics"]
                    ),
                    "windows_with_probe_errors": sum(
                        bool(item["probe_errors"])
                        for row in rows for item in row["bar_statement_diagnostics"]
                    ),
                    "windows_below_initial_delay": sum(
                        item.get("sampling_status") == "below_initial_delay"
                        for row in rows for item in row["bar_statement_diagnostics"]
                    ),
                    "wait_event_samples": {
                        key: count for key, count in sorted(Counter(
                            f"{sample.get('wait_type') or 'NONE'}:{sample.get('wait_event') or 'NONE'}"
                            for row in rows
                            for item in row["bar_statement_diagnostics"]
                            for sample in item["samples"]
                        ).items())
                    },
                    "scope_note": (
                        "execute windows are individual SQL batches; executemany covers all "
                        "row operations in that call, not each row separately; sampling waits "
                        "100ms before probing and may miss shorter calls"
                    ),
                },
            }
            for kind in ("minute", "daily")
            for rows in ([row for row in selected if row["kind"] == kind],)
        },
        "writer_transactions": {
            kind: {
                "calls": len(rows), "count": len(rows),
                "transactions": sum(int(row["transactions"]) for row in rows),
                "commits": sum(int(row["commits"]) for row in rows),
                "rows_attempted": sum(int(row["rows"]) for row in rows),
                "elapsed_ms": describe([int(row["elapsed_ms"]) for row in rows]),
                "connect_ms": describe([int(row["connect_ms"]) for row in rows
                                         if row["connect_ms"] is not None]),
                "execute_ms": describe([int(row["execute_ms"]) for row in rows
                                         if row["execute_ms"] is not None]),
                "commit_ms": describe([int(row["commit_ms"]) for row in rows
                                        if row["commit_ms"] is not None]),
                "commit_latency_samples": sum(row["commit_ms"] is not None for row in rows),
                "payload_bytes_estimated": (
                    sum(int(row["bytes_payload_estimate"]) for row in rows
                        if row["bytes_payload_estimate"] is not None)
                    if any(row["bytes_payload_estimate"] is not None for row in rows) else None
                ),
                "errors": None, "retries": None,
                "scope_note": "successful instrumented calls only; errors/retries are not yet instrumented",
                "call_samples": [
                    {"at": row["at"], "db_call_id": row["db_call_id"],
                     "elapsed_ms": row["elapsed_ms"], "connect_ms": row["connect_ms"],
                     "execute_ms": row["execute_ms"], "commit_ms": row["commit_ms"]}
                    for row in rows if row.get("db_call_id")
                ],
            }
            for kind in sorted({str(row["kind"]) for row in writers})
            for rows in ([row for row in writers if row["kind"] == kind],)
        },
        "api_writers": {
            api_id: {
                kind: {"count": len(rows), "rows": sum(int(row["rows"]) for row in rows)}
                for kind in sorted({str(row["kind"]) for row in writers if row["api_id"] == api_id})
                for rows in ([row for row in writers if row["api_id"] == api_id and row["kind"] == kind],)
            }
            for api_id in sorted({str(row["api_id"]) for row in writers if row["api_id"]})
        },
        "realtime_flushes": summarize_writer_flushes(writers),
    }


def summarize_writer_flushes(writers: list[dict[str, float | int | str]]) -> dict[str, object]:
    """Count successful instrumented commits per realtime flush cycle.

    An interrupted cycle may have committed a prefix of its writers; this is
    deliberately not labeled a complete cycle or a fixed per-0B cost.
    """
    by_cycle: dict[str, list[dict[str, float | int | str]]] = {}
    for row in writers:
        flush_id = str(row.get("flush_id") or "")
        if flush_id:
            by_cycle.setdefault(flush_id, []).append(row)
    if not by_cycle:
        return {"observed_cycles_with_successful_writes": 0,
                "successful_commits_per_cycle": None,
                "scope_note": "successful instrumented PostgreSQL writers only"}
    counts = sorted(sum(int(row["commits"]) for row in rows) for rows in by_cycle.values())
    return {
        "observed_cycles_with_successful_writes": len(counts),
        "successful_commits_per_cycle": {
            "min": counts[0], "median": median(counts),
            "p90": counts[ceil(len(counts) * .90) - 1],
            "p95": counts[ceil(len(counts) * .95) - 1],
            "p99": counts[ceil(len(counts) * .99) - 1], "max": counts[-1],
        },
        "scope_note": "successful instrumented PostgreSQL writers; failed/zero-write cycles are absent",
    }
