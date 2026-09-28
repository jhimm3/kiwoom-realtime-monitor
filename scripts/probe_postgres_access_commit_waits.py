"""Correlate dedicated-test-DB cache commits with their backend wait events.

Diagnostic only. This copies the existing delayed pg_stat_activity sampler's
query into a bounded, opt-in DBSlowHook trial; remove this script when the
common hook has a verified reusable diagnostic implementation.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
import uuid
from collections import Counter
from pathlib import Path
from threading import Event, Thread

import psycopg

from kiwoom_monitor.central_server.diagnostic_metrics import (
    refresh_capture_state, summarize_db_calls,
)
from kiwoom_monitor.central_server.postgres_access import (
    DBWriterContext, open_observed_connection,
)

if __package__:
    from scripts.benchmark_postgres_access_pilot import _set_capture
    from scripts.benchmark_postgres_access_cache_writer import (
        DELETE_EXPIRED, UPSERT, _cleanup, _connect, _preflight,
    )
else:
    from benchmark_postgres_access_pilot import _set_capture
    from benchmark_postgres_access_cache_writer import (
        DELETE_EXPIRED, UPSERT, _cleanup, _connect, _preflight,
    )


INITIAL_DELAY_SECONDS = 0.1
SAMPLE_INTERVAL_SECONDS = 0.025
MAX_SAMPLES = 400


def _sample_waits(url: str, pid: int, stop: Event,
                  samples: list[dict[str, object]], errors: list[str]) -> None:
    if stop.wait(INITIAL_DELAY_SECONDS):
        return
    try:
        with psycopg.connect(
            url, autocommit=True, connect_timeout=2,
            application_name="kiwoom-access-wait-probe",
        ) as connection, connection.cursor() as cursor:
            cursor.execute("SET statement_timeout TO '500ms'")
            while not stop.is_set() and len(samples) < MAX_SAMPLES:
                cursor.execute(
                    "SELECT clock_timestamp(),state,COALESCE(wait_event_type,''),"
                    "COALESCE(wait_event,''),pg_blocking_pids(pid) "
                    "FROM pg_stat_activity WHERE pid=%s",
                    (pid,),
                )
                row = cursor.fetchone()
                if row is None:
                    errors.append("backend_disappeared")
                    return
                samples.append({
                    "at": row[0].timestamp(), "state": str(row[1]),
                    "wait_type": str(row[2]), "wait_event": str(row[3]),
                    "blocking_pids": tuple(int(value) for value in row[4]),
                })
                if stop.wait(SAMPLE_INTERVAL_SECONDS):
                    return
    except Exception as error:
        errors.append(type(error).__name__)


class CommitWaitHook:
    def __init__(self, url: str) -> None:
        self.url = url
        self.active: dict[str, dict[str, object]] = {}
        self.finished: dict[str, dict[str, object]] = {}
        self.records: dict[str, dict[str, object]] = {}

    def stage_started(self, call_id: str, stage: str, at: float,
                      backend_pid: int | None) -> None:
        if stage != "commit":
            return
        stop = Event()
        samples: list[dict[str, object]] = []
        errors: list[str] = []
        trial: dict[str, object] = {
            "started_at": at, "backend_pid": backend_pid,
            "stop": stop, "samples": samples, "errors": errors,
        }
        if backend_pid is None:
            errors.append("backend_pid_unavailable")
        else:
            thread = Thread(
                target=_sample_waits,
                args=(self.url, backend_pid, stop, samples, errors),
                name="access-commit-wait-probe", daemon=True,
            )
            trial["thread"] = thread
            thread.start()
        self.active[call_id] = trial

    def stage_finished(self, call_id: str, stage: str, at: float,
                       duration_ms: float) -> None:
        if stage != "commit":
            return
        trial = self.active.pop(call_id, None)
        if trial is None:
            return
        stop = trial["stop"]
        stop.set()
        thread = trial.get("thread")
        if thread is not None:
            thread.join(timeout=0.25)
        trial["finished_at"] = at
        trial["commit_ms"] = duration_ms
        trial["probe_pending"] = bool(thread is not None and thread.is_alive())
        trial["samples"] = list(trial["samples"])
        trial["errors"] = list(trial["errors"])
        self.finished[call_id] = trial

    def call_finished(self, record: dict[str, object]) -> None:
        self.records[str(record["call_id"])] = record


def _describe_waits(trial: dict[str, object]) -> dict[str, object]:
    began = float(trial["started_at"])
    ended = float(trial["finished_at"])
    window = [sample for sample in trial["samples"]
              if began <= float(sample["at"]) <= ended]
    counts: Counter[tuple[str, str, str, tuple[int, ...]]] = Counter()
    offsets: dict[tuple[str, str, str, tuple[int, ...]], list[int]] = {}
    for sample in window:
        key = (str(sample["state"]), str(sample["wait_type"]),
               str(sample["wait_event"]), tuple(sample["blocking_pids"]))
        counts[key] += 1
        offsets.setdefault(key, []).append(round((float(sample["at"]) - began) * 1000))
    summary = [
        {"state": state, "wait_type": wait_type or "NONE",
         "wait_event": wait_event or "NONE", "blocking_pids": list(blockers),
         "samples": count, "first_offset_ms": min(offsets[key]),
         "last_offset_ms": max(offsets[key])}
        for key, count in counts.most_common()
        for state, wait_type, wait_event, blockers in (key,)
    ]
    return {
        "backend_pid": trial["backend_pid"],
        "commit_started_at": began, "commit_finished_at": ended,
        "commit_ms": trial["commit_ms"],
        "sampling_interval_ms": round(SAMPLE_INTERVAL_SECONDS * 1000),
        "initial_delay_ms": round(INITIAL_DELAY_SECONDS * 1000),
        "sample_count": len(window),
        "samples_truncated": len(trial["samples"]) >= MAX_SAMPLES,
        "probe_pending": trial["probe_pending"],
        "probe_errors": trial["errors"],
        "waits": summary,
    }


def _run(url: str, calls: int) -> dict[str, object]:
    hook = CommitWaitHook(url)
    key = f"diagnostic-common-access-wait-{uuid.uuid4().hex}"
    output: list[dict[str, object]] = []
    removed = None
    try:
        for _ in range(calls):
            context = DBWriterContext(
                "test.benchmark", "benchmark:query_cache_wait",
                "probe_cache_commit", rows_attempted=1, api_id="ka10081",
            )
            connection = open_observed_connection(lambda: _connect(url), context,
                                                  hook=hook)
            try:
                with connection.cursor() as cursor:
                    cursor.execute(UPSERT, (key, "ka10081", time.time() + 3600,
                                            '{"benchmark":true}', False, ""))
                    cursor.execute(DELETE_EXPIRED, (time.time(),))
                connection.commit()
            finally:
                connection.close()
            record = hook.records.get(context.call_id)
            trial = hook.finished.get(context.call_id)
            if (record is None or trial is None or record["commits"] != 1
                    or record["sql_calls"] != 2 or record["outcome"] != "committed"):
                raise RuntimeError("commit wait trial did not record its DB call")
            output.append({"call_id": context.call_id,
                           "connection_acquire_ms": record["connection_acquire_ms"],
                           "execute_ms": record["execute_ms"],
                           **_describe_waits(trial)})
    finally:
        removed = _cleanup(url, key)
    if removed != 1:
        raise RuntimeError("commit wait trial expected one cleaned test row")
    return {"calls": output, "cleanup_rows": removed}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calls", type=int, default=12)
    args = parser.parse_args()
    if not 1 <= args.calls <= 24:
        parser.error("calls must be 1..24")
    url = os.environ.get("KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL", "")
    _preflight(url)
    previous_control = os.environ.get("KIWOOM_DIAGNOSTIC_WORKLOAD_PATH")
    try:
        with tempfile.TemporaryDirectory(prefix="kiwoom-db-wait-probe-") as temp:
            control = Path(temp) / "controls.json"
            os.environ["KIWOOM_DIAGNOSTIC_WORKLOAD_PATH"] = str(control)
            _set_capture(control, True)
            started = time.time() - 1
            result = _run(url, args.calls)
            captured = summarize_db_calls(started, time.time() + 1,
                                          mode="summary")
            if captured["dropped"] or sum(row["calls"] for row in
                                           captured["writers"].values()) != args.calls:
                raise RuntimeError("commit wait capture count or drop mismatch")
            _set_capture(control, False)
    finally:
        if previous_control is None:
            os.environ.pop("KIWOOM_DIAGNOSTIC_WORKLOAD_PATH", None)
        else:
            os.environ["KIWOOM_DIAGNOSTIC_WORKLOAD_PATH"] = previous_control
        refresh_capture_state(force=True)
    print(json.dumps({
        "workload": "query_cache_commit_wait_probe",
        "database": "kiwoom_monitor_diagnostic_test", "calls_attempted": args.calls,
        **result,
        "scope_note": "diagnostic SQL equivalent; wait samples are a concurrent "
                      "observation, not proof of storage cause; no production DB write",
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
