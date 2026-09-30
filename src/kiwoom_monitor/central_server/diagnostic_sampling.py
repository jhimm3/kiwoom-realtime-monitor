"""Shared, bounded NAS diagnostic snapshots and phase correlation."""
from __future__ import annotations

import os
import json
import time
from hashlib import sha256
from pathlib import Path


def _db_calls_report(before: dict, after: dict, session_id: str,
                     control_before: dict, control_after: dict) -> dict:
    """Keep the server's bounded result without presenting a partial window as complete."""
    for response in (before, after):
        if not isinstance(response, dict) or response.get("unavailable"):
            return {"state": "unavailable", "reason": (
                response.get("unavailable", "invalid_response")
                if isinstance(response, dict) else "invalid_response")}
        if (not isinstance(response.get("producer"), dict)
                or not response["producer"].get("process_id")
                or not isinstance(response.get("capture"), dict)):
            return {"state": "unavailable", "reason": "metadata_missing"}
    summary = after
    if before["producer"] != after["producer"]:
        return {"state": "incomplete", "reason": "server_process_changed",
                "summary": summary}
    if (not before["capture"].get("enabled")
            or not after["capture"].get("enabled")
            or before["capture"].get("session_id") != session_id
            or after["capture"].get("session_id") != session_id
            or not control_before["metrics_capture"].get("enabled")
            or not control_after["metrics_capture"].get("enabled")
            or control_before["metrics_capture"].get("session_id") != session_id
            or control_after["metrics_capture"].get("session_id") != session_id):
        return {"state": "incomplete", "reason": "capture_session_ended_or_changed",
                "summary": summary}
    if control_before["control_revision"] != control_after["control_revision"]:
        return {"state": "incomplete", "reason": "diagnostic_control_changed",
                "summary": summary}
    if summary.get("truncated"):
        return {"state": "incomplete", "reason": "bounded_capture_truncated",
                "summary": summary}
    return {"state": "complete", "summary": summary}


def _device_stats() -> dict[str, list[int]]:
    result = {}
    mapped = _storage_mapping()
    names: set[str] = set()

    def visit(graph: object) -> None:
        if not isinstance(graph, dict) or len(names) >= 24:
            return
        name = graph.get("name")
        if isinstance(name, str) and name and "/" not in name:
            names.add(name)
        sysfs = graph.get("sysfs")
        if isinstance(sysfs, str):
            parent = Path(sysfs).parent
            if parent.parent.name == "block":
                names.add(parent.name)
        for child in graph.get("slaves", []):
            visit(child)

    if mapped.get("available"):
        for item in mapped["paths"].values():
            visit(item.get("block_graph") if isinstance(item, dict) else None)
    else:
        names.update(("dm-4", "md4", "nvme0n1", "nvme1n1"))
    for name in sorted(names)[:24]:
        try:
            result[name] = [int(value) for value in
                            (Path("/sys/block") / name / "stat").read_text().split()]
        except (OSError, ValueError):
            pass
    return result


def _device_delta(before: dict[str, list[int]], after: dict[str, list[int]],
                  elapsed: float) -> dict[str, dict]:
    result = {}
    for name, current in after.items():
        previous = before.get(name)
        if previous is None or len(current) < 11 or len(previous) < 11:
            continue
        writes = current[4] - previous[4]
        write_ms = current[7] - previous[7]
        weighted_ms = current[10] - previous[10]
        busy_ms = current[9] - previous[9]
        value = {"writes": writes, "write_sectors": current[6] - previous[6],
                 "write_await_ms": round(write_ms / writes, 2) if writes > 0 else None,
                 "average_queue": round(weighted_ms / (elapsed * 1000), 2) if elapsed > 0 else None,
                 "busy_percent": round(100 * busy_ms / (elapsed * 1000), 2) if elapsed > 0 else None}
        if len(current) >= 17 and len(previous) >= 17:
            flushes = current[15] - previous[15]
            value["flushes"] = flushes
            value["flush_await_ms"] = round((current[16] - previous[16]) / flushes, 2) if flushes > 0 else None
        result[name] = value
    return result


def _correlate_commit_device_samples(bar_saves: dict, samples: list[dict]) -> dict:
    """Attach measured host-device intervals around each SQL execution window."""
    kinds = bar_saves.get("kinds", {}) if isinstance(bar_saves, dict) else {}
    for kind_data in kinds.values():
        if not isinstance(kind_data, dict):
            continue
        for call in kind_data.get("call_samples", []):
            details = call.get("commit_diagnostics", {})
            window = details.get("commit_window") if isinstance(details, dict) else None
            windows = []
            if isinstance(window, dict):
                windows.append((details, window))
            for statement in call.get("bar_statement_diagnostics", []):
                windows.append((statement, statement))
            for target, bounds in windows:
                start_at = float(bounds["started_at"])
                end_at = float(bounds["ended_at"])
                before = [sample for sample in samples if float(sample["at"]) <= start_at]
                after = [sample for sample in samples if float(sample["at"]) >= end_at]
                if not before or not after:
                    target["storage_device_window"] = {
                        "available": False,
                        "reason": "measurement window lacks device samples around this SQL phase",
                    }
                    continue
                start_sample = before[-1]
                end_sample = after[0]
                elapsed = max(0.001, float(end_sample["at"]) - float(start_sample["at"]))
                target["storage_device_window"] = {
                    "available": True,
                    "target_sample_interval_ms": 250,
                    "observed_interval_ms": round(elapsed * 1000, 1),
                    "boundary_padding_ms": round(
                        (start_at - float(start_sample["at"])
                         + float(end_sample["at"]) - end_at) * 1000, 1,
                    ),
                    "host_device_delta": _device_delta(
                        start_sample["stats"], end_sample["stats"], elapsed,
                    ),
                    "scope_note": (
                        "host-wide device counters over a window aligned to this SQL phase; "
                        "overlapping writes from other processes are included"
                    ),
                }
    return bar_saves


def _host_usage() -> dict:
    result = {"load_average": [round(value, 2) for value in os.getloadavg()]}
    try:
        values = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, _, raw = line.partition(":")
            if key in {"MemTotal", "MemAvailable"}:
                values[key] = int(raw.strip().split()[0]) * 1024
        result.update(values)
    except (OSError, ValueError):
        pass
    return result


def _uncontrolled_importers() -> list[str]:
    names = ("run_prepared_historical_news_imports.py",
             "import_prepared_historical_news_to_nas.py",
             "publish_historical_intelligence_to_nas.py")
    found = []
    for path in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            command = path.read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        for name in names:
            if name in command and "nas_workload_diagnostic.py" not in command:
                found.append(name)
    return sorted(set(found))


def _snapshot(cursor) -> dict[str, int]:
    cursor.execute(
        "SELECT wal_records,wal_fpi,wal_bytes,wal_buffers_full,wal_write,wal_sync,"
        "wal_write_time,wal_sync_time,stats_reset "
        "FROM pg_stat_wal"
    )
    wal = cursor.fetchone()
    cursor.execute(
        "SELECT xact_commit,blks_read,blks_hit,temp_bytes,deadlocks,stats_reset "
        "FROM pg_stat_database WHERE datname=current_database()"
    )
    database = cursor.fetchone()
    names = ("wal_records", "wal_fpi", "wal_bytes", "wal_buffers_full",
             "wal_write", "wal_sync",
             "wal_write_time_ms", "wal_sync_time_ms", "transactions",
             "blocks_read", "blocks_hit", "temp_bytes", "deadlocks")
    counters = dict(zip(names, (float(value or 0) for value in (*wal[:-1], *database[:-1])),
                        strict=True))
    counters["stats_reset"] = {
        "wal": wal[-1].isoformat() if wal[-1] is not None else None,
        "database": database[-1].isoformat() if database[-1] is not None else None,
    }
    return counters


_PG_STAT_IO_COUNTERS = (
    "reads", "read_time_ms", "writes", "write_time_ms", "writebacks",
    "writeback_time_ms", "extends", "extend_time_ms", "fsyncs", "fsync_time_ms",
    "hits", "evictions", "reuses",
)


def _pg_stat_io_snapshot(cursor) -> dict[str, object]:
    """Read PostgreSQL relation-I/O counters by backend, object, and context."""
    cursor.execute(
        "SELECT backend_type,object,context,reads,read_time,writes,write_time,"
        "writebacks,writeback_time,extends,extend_time,fsyncs,fsync_time,"
        "hits,evictions,reuses,stats_reset FROM pg_stat_io"
    )
    result = {}
    for row in cursor.fetchall():
        backend_type, object_name, context, *values, stats_reset = row
        key = (str(backend_type or ""), str(object_name or ""), str(context or ""))
        result[key] = {
            # PostgreSQL uses NULL for operations this backend cannot perform.
            # Treating those cells as zero invents an I/O measurement.
            name: None if value is None else float(value)
            for name, value in zip(_PG_STAT_IO_COUNTERS, values, strict=True)
        }
        result[key]["stats_reset"] = str(stats_reset) if stats_reset is not None else None
    return {"available": True, "rows": result}


def _checkpointer_snapshot(cursor) -> dict[str, object]:
    """Read PostgreSQL 17 checkpointer counters without changing server settings."""
    cursor.execute(
        "SELECT num_timed,num_requested,restartpoints_timed,restartpoints_req,"
        "restartpoints_done,write_time,sync_time,buffers_written,stats_reset "
        "FROM pg_stat_checkpointer"
    )
    row = cursor.fetchone()
    names = (
        "num_timed", "num_requested", "restartpoints_timed", "restartpoints_req",
        "restartpoints_done", "write_time_ms", "sync_time_ms", "buffers_written",
    )
    return {
        "available": True,
        "counters": dict(zip(names, (None if value is None else float(value) for value in row[:-1]), strict=True)),
        "stats_reset": str(row[-1]) if row[-1] is not None else None,
    }


def _counter_delta(before: dict, after: dict) -> dict:
    """Return counter deltas and flag statistics resets instead of reporting negatives."""
    if not before.get("available") or not after.get("available"):
        return {"available": False,
                "reason": before.get("reason") or after.get("reason") or "statistics_unavailable"}
    window = {
        "started_at": before.get("captured_at"),
        "ended_at": after.get("captured_at"),
    }
    if window["started_at"] is not None and window["ended_at"] is not None:
        window["duration_ms"] = round(
            max(0.0, float(window["ended_at"]) - float(window["started_at"])) * 1000, 3
        )
    before_rows = before.get("rows")
    after_rows = after.get("rows")
    if before_rows is not None and after_rows is not None:
        result = {}
        for key in sorted(set(before_rows) | set(after_rows)):
            old = before_rows.get(key, {})
            new = after_rows.get(key, {})
            old_reset = old.get("stats_reset")
            new_reset = new.get("stats_reset")
            reset = old_reset is not None and new_reset is not None and old_reset != new_reset
            counters = {}
            decreased = False
            for name in _PG_STAT_IO_COUNTERS:
                old_value = old.get(name)
                new_value = new.get(name)
                if reset or old_value is None or new_value is None:
                    counters[name] = None
                elif float(new_value) < float(old_value):
                    counters[name] = None
                    decreased = True
                else:
                    counters[name] = round(float(new_value) - float(old_value), 3)
            result["|".join(key)] = {
                "counters": counters, "stats_reset_changed": reset,
                "missing_boundary": key not in before_rows or key not in after_rows,
                "counter_decreased": decreased,
            }
        return {"available": True, **window,
                "scope": "cluster-wide relation I/O by backend_type/object/context",
                "groups": result,
                "note": (
                    "pg_stat_io is not per-query attribution; overlapping database work is "
                    "included. Active backend counters may lag until that backend flushes its "
                    "statistics, and timing fields require track_io_timing during the I/O"
                )}

    old = before.get("counters", {})
    new = after.get("counters", {})
    reset = before.get("stats_reset") != after.get("stats_reset")
    decreased = False
    counters = {}
    for name, value in old.items():
        current = new.get(name)
        if reset or value is None or current is None:
            counters[name] = None
        elif float(current) < float(value):
            counters[name] = None
            decreased = True
        else:
            counters[name] = round(float(current) - float(value), 3)
    return {
        "available": True,
        **window,
        "counters": counters,
        "stats_reset_changed": reset,
        "counter_decreased": decreased,
        "scope": "cluster-wide checkpointer activity, not per-query attribution",
        "note": (
            "active backend statistics may lag until flushed; checkpointer write_time_ms "
            "and sync_time_ms measure checkpoint phases independently of track_io_timing"
        ),
    }


def _optional_io_snapshot(cursor, reader) -> dict[str, object]:
    import psycopg

    try:
        result = reader(cursor)
        result["captured_at"] = time.time()
        return result
    except psycopg.Error as error:
        return {"available": False, "reason": type(error).__name__, "captured_at": time.time()}


def _wal_timing_status(cursor) -> dict[str, object]:
    cursor.execute("SHOW track_wal_io_timing")
    enabled = str(cursor.fetchone()[0]).lower() == "on"
    return {"enabled": enabled, "setting_scope": "measurement_connection",
            "time_values_available": enabled,
            "note": "enabled describes this measurement connection; pg_stat_wal is cluster-wide"}


def _io_timing_status(cursor) -> dict[str, object]:
    try:
        cursor.execute("SHOW track_io_timing")
        enabled = str(cursor.fetchone()[0]).lower() == "on"
    except Exception as error:
        # This setting is explanatory only. A failed diagnostic read must not
        # turn an otherwise valid measurement into an aborted phase.
        return {"measurement_connection_enabled": None,
                "available": False, "reason": type(error).__name__,
                "scope": "measurement connection only"}
    return {
        "available": True,
        "measurement_connection_enabled": enabled,
        "scope": "measurement connection only",
        "note": (
            "pg_stat_io timing fields depend on track_io_timing at the time "
            "each server process performs I/O; this connection setting does not establish "
            "that all processes collected timing"
        ),
    }


def _wal_timing_report(status: dict, delta: dict, market_bar_saves: dict) -> dict:
    """Explain cluster WAL timing when writers enable timing transaction-locally."""
    local_enabled_calls = 0
    for kind in market_bar_saves.get("kinds", {}).values():
        for call in kind.get("call_samples", []):
            if call.get("commit_diagnostics", {}).get("wal_io_timing_enabled_for_commit"):
                local_enabled_calls += 1
    time_deltas = {
        "wal_write_time_ms": float(delta.get("wal_write_time_ms", 0.0)),
        "wal_sync_time_ms": float(delta.get("wal_sync_time_ms", 0.0)),
    }
    delta_observed = any(value != 0.0 for value in time_deltas.values())
    available = bool(status.get("enabled") or local_enabled_calls or delta_observed)
    return {
        **status,
        "time_values_available": available,
        "transaction_local_timing_calls": local_enabled_calls,
        "cluster_timing_delta_nonzero": delta_observed,
        "cluster_wal_time_delta_ms": time_deltas,
        "note": (
            "enabled describes the measurement connection; pg_stat_wal is cluster-wide, "
            "so transaction-local timing enabled by instrumented writers contributes even "
            "when this connection reports off"
        ),
    }


def _storage_mapping() -> dict[str, object]:
    path = Path(os.environ.get("KIWOOM_DIAGNOSTIC_STORAGE_MAP_PATH",
                               "/app/data/maintenance/diagnostic-storage-mapping.json"))
    try:
        mapping = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {"available": False, "reason": "host storage mapping has not been captured"}
    if not isinstance(mapping, dict) or not isinstance(mapping.get("paths"), dict) \
            or not {"PGDATA", "pg_wal"}.issubset(mapping["paths"]):
        return {"available": False, "reason": "host storage mapping is invalid"}
    return {"available": True, "captured_at_utc": mapping.get("captured_at_utc"),
            "paths": mapping["paths"],
            "scope_note": "host connectivity graph, not per-transaction I/O attribution"}


def _log_position() -> tuple[Path, int]:
    path = Path(os.environ.get("CENTRAL_SERVER_LOG_DIR", "/app/data/logs")) / "server.log"
    try:
        return path, path.stat().st_size
    except OSError:
        return path, 0


def _log_counts(path: Path, offset: int) -> dict[str, int]:
    labels = {
        "ka10080_completed": "api_id=ka10080",
        "minute_save_slow": "slow PostgreSQL market bar save kind=minute",
        "news_job_stage_report": "뉴스 작업 단계별 처리",
        "news_source_error": "뉴스 수집 주기에 실패",
    }
    counts = {name: 0 for name in labels}
    try:
        with path.open("rb") as source:
            if source.seek(0, 2) < offset:
                offset = 0  # log rotated; this phase cannot be compared by line count
            source.seek(offset)
            for line in source:
                decoded = line.decode("utf-8", errors="replace")
                for name, needle in labels.items():
                    counts[name] += needle in decoded
    except OSError:
        pass
    return counts


def _query_kind(statement: str) -> str:
    text = statement.lower()
    if "skip locked" in text and "central_news_jobs" in text:
        return "news_job_claim"
    for table in ("central_news_jobs", "central_news_article_revisions",
                  "central_news_body_revisions", "central_news_event_revisions",
                  "central_minute_bars", "central_documents"):
        if table in text:
            return table.removeprefix("central_")
    return "other"


def read_postgres_snapshot(database_url: str, *, sections: frozenset[str],
                           pid: int | None = None) -> dict[str, object]:
    """Fixed, read-only probes; never return query text or bind values."""
    import psycopg

    result: dict[str, object] = {"captured_at": time.time(), "sections": {},
                                 "scope": "current_database_and_cluster_counters"}
    with psycopg.connect(database_url, autocommit=True, connect_timeout=5) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET default_transaction_read_only=on")
            cursor.execute("SET statement_timeout TO '2000ms'")
            cursor.execute("SELECT current_database(),current_setting('server_version')")
            database, version = cursor.fetchone()
            result["database"] = database
            result["postgresql"] = version
            if "postgres" in sections:
                counters = _snapshot(cursor)
                result["sections"]["postgres"] = {
                    "counters": counters,
                    "pg_stat_io": _optional_io_snapshot(cursor, _pg_stat_io_snapshot),
                    "checkpointer": _optional_io_snapshot(cursor, _checkpointer_snapshot),
                    "wal_timing": _wal_timing_status(cursor),
                    "io_timing": _io_timing_status(cursor),
                    "scope_note": "WAL and I/O counters include other database work",
                }
            if "activity" in sections:
                cursor.execute(
                    "SELECT pid,backend_type,backend_start,state,"
                    "COALESCE(wait_event_type,''),COALESCE(wait_event,''),"
                    "EXTRACT(EPOCH FROM clock_timestamp()-query_start)*1000,"
                    "EXTRACT(EPOCH FROM clock_timestamp()-xact_start)*1000,"
                    "pg_blocking_pids(pid),query FROM pg_stat_activity "
                    "WHERE datname=current_database() AND pid<>pg_backend_pid() "
                    "AND (state<>'idle' OR pid=%s) AND (%s<0 OR pid=%s) "
                    "ORDER BY query_start NULLS LAST LIMIT 201",
                    (pid or -1, pid or -1, pid or -1),
                )
                rows = cursor.fetchall()
                selected = []
                for process_id, backend, backend_start, state, wait_type, wait, query_age, xact_age, blockers, query in rows:
                    if pid is not None and process_id != pid:
                        continue
                    selected.append({
                        "pid": process_id, "backend": backend,
                        "backend_start": backend_start.isoformat() if backend_start else None,
                        "state": state, "wait_type": wait_type, "wait_event": wait,
                        "query_age_ms": round(query_age) if query_age is not None else None,
                        "transaction_age_ms": round(xact_age) if xact_age is not None else None,
                        "blocking_pids": blockers, "query_kind": _query_kind(query or ""),
                        "query_sha256_12": sha256((query or "").encode()).hexdigest()[:12],
                        "query_template": (
                            "SELECT job candidates FROM central_news_jobs FOR UPDATE SKIP LOCKED"
                            if _query_kind(query or "") == "news_job_claim" else None),
                    })
                result["sections"]["activity"] = {
                    "rows": selected[:200], "truncated": len(rows) > 200,
                    "query_template_scope": "unavailable_for_unrecognized_sql",
                }
            if "news_jobs" in sections:
                cursor.execute(
                    "SELECT schemaname,n_live_tup,n_dead_tup,seq_scan,idx_scan,last_analyze,last_autoanalyze "
                    "FROM pg_stat_user_tables WHERE relid=to_regclass('central_news_jobs')"
                )
                table = cursor.fetchone()
                cursor.execute(
                    "SELECT indexrelname,idx_scan,idx_tup_read,idx_tup_fetch "
                    "FROM pg_stat_user_indexes WHERE relid=to_regclass('central_news_jobs') "
                    "ORDER BY indexrelname"
                )
                indexes = cursor.fetchall()
                result["sections"]["news_jobs"] = {
                    "table": ({"schema": table[0], "live_estimate": table[1], "dead_estimate": table[2],
                               "seq_scans": table[3], "index_scans": table[4],
                               "last_analyze": table[5].isoformat() if table[5] else None,
                               "last_autoanalyze": table[6].isoformat() if table[6] else None}
                              if table else None),
                    "indexes": [dict(zip(("name", "scans", "tuples_read", "tuples_fetched"),
                                         row, strict=True)) for row in indexes],
                    "scope_note": "statistics resolved for the same search_path relation as the application query",
                }
    return result


def read_host_snapshot() -> dict[str, object]:
    mapping = _storage_mapping()
    return {"captured_at": time.time(), "usage": _host_usage(),
            "device_counters": _device_stats(), "storage_mapping": mapping,
            "uncontrolled_importers": _uncontrolled_importers(),
            "scope_note": "host counters and saved mapping are not per-transaction attribution"}

