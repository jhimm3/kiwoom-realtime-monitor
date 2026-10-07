"""Bounded query-cache SQL benchmark on the dedicated PostgreSQL test DB.

This diagnostic reproduces save_query's two SQL statements, fresh connection,
explicit commit, and close. It does not call the domain method or measure JSON
encoding and legacy metric logging. Never use its numbers as a production gate.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from time import perf_counter

import psycopg

from kiwoom_monitor.central_server.diagnostic_metrics import (
    refresh_capture_state, summarize_db_calls,
)
from kiwoom_monitor.central_server.postgres_access import (
    DBWriterContext, open_observed_connection,
)
if __package__:
    from scripts.benchmark_postgres_access_pilot import (
        MODE_ORDERS, TEST_DATABASE, _check_database, _describe, _set_capture,
    )
else:
    from benchmark_postgres_access_pilot import (
        MODE_ORDERS, TEST_DATABASE, _check_database, _describe, _set_capture,
    )


UPSERT = (
    "INSERT INTO central_api_query_cache(cache_key,api_id,expires_at,payload_json,has_next,next_key) "
    "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(cache_key) DO UPDATE SET "
    "api_id=EXCLUDED.api_id,expires_at=EXCLUDED.expires_at,payload_json=EXCLUDED.payload_json,"
    "has_next=EXCLUDED.has_next,next_key=EXCLUDED.next_key"
)
DELETE_EXPIRED = "DELETE FROM central_api_query_cache WHERE expires_at<=%s"


def _connect(url: str) -> psycopg.Connection:
    return psycopg.connect(url, connect_timeout=5)


def _preflight(url: str) -> None:
    _check_database(url)
    with psycopg.connect(url, connect_timeout=5,
                         options="-c default_transaction_read_only=on") as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('central_api_query_cache')")
            if cursor.fetchone()[0] is None:
                raise RuntimeError("query-cache table is unavailable in diagnostic DB")
            cursor.execute("SELECT COUNT(*) FROM central_api_query_cache WHERE expires_at<=%s",
                           (time.time() + 900,))
            if cursor.fetchone()[0]:
                raise RuntimeError("refusing cleanup while other cache rows may expire")


def _cleanup(url: str, key: str) -> int:
    with _connect(url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM central_api_query_cache WHERE cache_key=%s", (key,))
            removed = cursor.rowcount
            cursor.execute("SELECT COUNT(*) FROM central_api_query_cache WHERE cache_key=%s",
                           (key,))
            if cursor.fetchone()[0]:
                raise RuntimeError("benchmark key cleanup did not remove all rows")
    return removed


def _one_call(url: str, mode: str, key: str) -> tuple[dict[str, float], str | None]:
    context = DBWriterContext("test.benchmark", "benchmark:query_cache",
                              "benchmark_cache_upsert", rows_attempted=1,
                              api_id="ka10081")
    started = perf_counter()
    connection = (_connect(url) if mode == "raw" else
                  open_observed_connection(lambda: _connect(url), context))
    connect_ms = (perf_counter() - started) * 1000
    execute_ms = commit_ms = close_ms = 0.0
    try:
        execute_started = perf_counter()
        with connection.cursor() as cursor:
            cursor.execute(UPSERT, (key, "ka10081", time.time() + 3600,
                                    '{"benchmark":true}', False, ""))
            cursor.execute(DELETE_EXPIRED, (time.time(),))
        execute_ms = (perf_counter() - execute_started) * 1000
        commit_started = perf_counter()
        connection.commit()
        commit_ms = (perf_counter() - commit_started) * 1000
    finally:
        close_started = perf_counter()
        connection.close()
        close_ms = (perf_counter() - close_started) * 1000
    return {
        "connect_ms": connect_ms, "execute_ms": execute_ms,
        "commit_ms": commit_ms, "close_ms": close_ms,
        "total_ms": (perf_counter() - started) * 1000,
    }, context.call_id if mode == "observed_on" else None


def _run_block(url: str, mode: str, operations: int,
               control: Path) -> tuple[list[dict[str, float]], dict[str, object]]:
    _set_capture(control, mode == "observed_on")
    key = f"diagnostic-common-access-benchmark-{uuid.uuid4().hex}"
    samples: list[dict[str, float]] = []
    call_ids: set[str] = set()
    started_at = time.time() - 1
    removed = None
    try:
        for _ in range(operations):
            sample, call_id = _one_call(url, mode, key)
            samples.append(sample)
            if call_id:
                call_ids.add(call_id)
        if mode == "observed_on":
            observed = summarize_db_calls(started_at, time.time() + 1,
                                          mode="raw", limit=20)
            matches = [call for call in observed["calls"]
                       if call["call_id"] in call_ids]
            if (len(matches) != operations or observed["dropped"]
                    or any(call["sql_calls"] != 2 or call["commits"] != 1
                           or call["outcome"] != "committed" for call in matches)):
                raise RuntimeError("cache benchmark capture mismatch")
    finally:
        removed = _cleanup(url, key)
    if removed != 1:
        raise RuntimeError("cache benchmark expected exactly one committed test row")
    block: dict[str, object] = {
        "mode": mode, "operations": operations,
        "latency_ms": {field: _describe([sample[field] for sample in samples])
                       for field in ("connect_ms", "execute_ms", "commit_ms",
                                     "close_ms", "total_ms")},
        "cleanup_rows": removed,
    }
    if mode == "observed_on":
        block["capture_recorded"] = True
    return samples, block


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operations", type=int, default=6)
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.operations <= 10 or not 1 <= args.rounds <= 3:
        parser.error("operations must be 1..10 and rounds 1..3")
    url = os.environ.get("KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL", "")
    _preflight(url)
    previous_control = os.environ.get("KIWOOM_DIAGNOSTIC_WORKLOAD_PATH")
    samples: dict[str, list[dict[str, float]]] = {mode: [] for mode in MODE_ORDERS[0]}
    blocks: list[dict[str, object]] = []
    try:
        with tempfile.TemporaryDirectory(prefix="kiwoom-db-benchmark-") as temp:
            control = Path(temp) / "controls.json"
            os.environ["KIWOOM_DIAGNOSTIC_WORKLOAD_PATH"] = str(control)
            for mode in MODE_ORDERS[0]:
                _run_block(url, mode, 1, control)  # warm-up, excluded
            for round_index in range(args.rounds):
                for mode in MODE_ORDERS[round_index]:
                    values, block = _run_block(url, mode, args.operations, control)
                    samples[mode].extend(values)
                    block["round"] = round_index + 1
                    blocks.append(block)
            _set_capture(control, False)
    finally:
        if previous_control is None:
            os.environ.pop("KIWOOM_DIAGNOSTIC_WORKLOAD_PATH", None)
        else:
            os.environ["KIWOOM_DIAGNOSTIC_WORKLOAD_PATH"] = previous_control
        refresh_capture_state(force=True)
    print(json.dumps({
        "workload": "query_cache_sql_equivalent_fresh_connection",
        "database": TEST_DATABASE,
        "operations_per_block": args.operations, "rounds": args.rounds,
        "mode_order": [list(order) for order in MODE_ORDERS[:args.rounds]],
        "samples": {
            mode: {field: _describe([sample[field] for sample in values])
                   for field in ("connect_ms", "execute_ms", "commit_ms",
                                 "close_ms", "total_ms")}
            for mode, values in samples.items()
        },
        "blocks": blocks,
        "scope_note": "diagnostic SQL equivalent, not the domain save_query method; "
                      "single caller; shared NAS load is uncontrolled",
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
