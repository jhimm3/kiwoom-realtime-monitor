"""Read-only PostgreSQL execute benchmark for the opt-in DB observation pilot.

Diagnostic code, never imported by the server. Only the dedicated test DB is
accepted. This measures cursor execute/fetch overhead on a reused connection;
it is not a query-cache writer or COMMIT throughput benchmark.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
import uuid
from math import ceil
from pathlib import Path
from statistics import median
from urllib.parse import urlsplit

import psycopg

from kiwoom_monitor.central_server.diagnostic_metrics import (
    refresh_capture_state, summarize_db_calls,
)
from kiwoom_monitor.central_server.diagnostic_workloads import instance_id
from kiwoom_monitor.central_server.postgres_access import (
    DBWriterContext, open_observed_connection,
)


TEST_DATABASE = "kiwoom_monitor_diagnostic_test"
MODE_ORDERS = (
    ("raw", "observed_off", "observed_on"),
    ("observed_off", "observed_on", "raw"),
    ("observed_on", "raw", "observed_off"),
)


def _check_database(url: str) -> None:
    if urlsplit(url).path.lstrip("/") != TEST_DATABASE:
        raise RuntimeError("refusing benchmark outside dedicated diagnostic DB")
    with psycopg.connect(url, connect_timeout=5,
                         options="-c default_transaction_read_only=on") as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            if cursor.fetchone()[0] != TEST_DATABASE:
                raise RuntimeError("connected database is not the diagnostic DB")


def _set_capture(path: Path, enabled: bool) -> None:
    expiry = time.time() + 900
    payload: dict[str, object] = {
        "schema": 1,
        "instance_id": instance_id(),
        "diagnostic_tool": {"expires_at": expiry, "session_id": uuid.uuid4().hex},
    }
    if enabled:
        payload["capture"] = {"expires_at": expiry}
    path.write_text(json.dumps(payload), encoding="utf-8")
    status = refresh_capture_state(force=True)
    if bool(status["enabled"]) != enabled:
        raise RuntimeError("benchmark capture state did not match requested mode")


def _describe(values: list[float]) -> dict[str, float | int]:
    ordered = sorted(values)
    return {
        "n": len(ordered), "min_ms": round(ordered[0], 3),
        "p50_ms": round(median(ordered), 3),
        "p95_ms": round(ordered[ceil(len(ordered) * .95) - 1], 3),
        "p99_ms": round(ordered[ceil(len(ordered) * .99) - 1], 3),
        "max_ms": round(ordered[-1], 3),
    }


def _run_block(url: str, mode: str, operations: int,
               control: Path) -> tuple[list[float], dict[str, object]]:
    _set_capture(control, mode == "observed_on")

    def connect() -> psycopg.Connection:
        return psycopg.connect(url, connect_timeout=5,
                               options="-c default_transaction_read_only=on")

    context = DBWriterContext("test.benchmark", "benchmark:select_one",
                              "select_one", rows_attempted=0)
    connection = (connect() if mode == "raw" else
                  open_observed_connection(connect, context))
    samples: list[float] = []
    started_at = time.time()
    started = time.perf_counter()
    try:
        with connection.cursor() as cursor:
            for _ in range(operations):
                began = time.perf_counter()
                cursor.execute("SELECT 1")
                if cursor.fetchone() != (1,):
                    raise RuntimeError("unexpected SELECT 1 result")
                samples.append((time.perf_counter() - began) * 1000)
        connection.commit()
    finally:
        connection.close()
    block: dict[str, object] = {
        "mode": mode, "operations": operations,
        "total_ms": round((time.perf_counter() - started) * 1000, 3),
        "latency_ms": _describe(samples),
    }
    if mode == "observed_on":
        observed = summarize_db_calls(started_at - 1, time.time() + 1,
                                      mode="raw", limit=20)
        matches = [call for call in observed["calls"]
                   if call["call_id"] == context.call_id]
        if (len(matches) != 1 or matches[0]["sql_calls"] != operations
                or matches[0]["commits"] != 1 or observed["dropped"]):
            raise RuntimeError("captured call count, SQL count, or commit mismatch")
        block["capture_recorded"] = True
    return samples, block


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operations", type=int, default=1000)
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.operations <= 1000 or not 1 <= args.rounds <= 3:
        parser.error("operations must be 1..1000 and rounds 1..3")
    url = os.environ.get("KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL", "")
    _check_database(url)
    previous_control = os.environ.get("KIWOOM_DIAGNOSTIC_WORKLOAD_PATH")
    samples: dict[str, list[float]] = {mode: [] for mode in MODE_ORDERS[0]}
    blocks: list[dict[str, object]] = []
    try:
        with tempfile.TemporaryDirectory(prefix="kiwoom-db-benchmark-") as temp:
            control = Path(temp) / "controls.json"
            os.environ["KIWOOM_DIAGNOSTIC_WORKLOAD_PATH"] = str(control)
            for mode in MODE_ORDERS[0]:
                _run_block(url, mode, 20, control)  # warm-up, excluded
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
        "workload": "read_only_select_1_execute_fetch",
        "database": TEST_DATABASE,
        "operations_per_block": args.operations, "rounds": args.rounds,
        "mode_order": [list(order) for order in MODE_ORDERS[:args.rounds]],
        "samples": {mode: _describe(values) for mode, values in samples.items()},
        "blocks": blocks,
        "scope_note": "read-only reused-connection microbenchmark; no writer or COMMIT gate",
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
