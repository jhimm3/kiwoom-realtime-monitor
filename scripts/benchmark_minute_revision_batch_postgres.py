"""Compare legacy row-wise and current batch revision writes in a disposable DB.

Requires KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL to point exactly at
kiwoom_monitor_diagnostic_test. Only synthetic revision rows are written and
removed; no market-bar or operational tables are touched.
"""

from __future__ import annotations

import json
import math
import os
import statistics
import time
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlsplit

import psycopg

from kiwoom_monitor.central_server.database import (
    _insert_postgres_observation_revision,
    _insert_postgres_observation_revisions_batch,
    _load_postgres_latest_revisions,
)
from kiwoom_monitor.central_server.market_observations import (
    bar_observation_key,
    minute_bar_observation,
    minute_bar_revision_payload,
)
from kiwoom_monitor.domain.market_data_contract import (
    DataCompleteness,
    DataValueKind,
    ObservationOrigin,
)
from kiwoom_monitor.domain.research_contract import ObservationRevisionSource


DATABASE_NAME = "kiwoom_monitor_diagnostic_test"
SOURCE = "kiwoom-ka10080;trade_value=ohlcv_estimate"
ROWS = 900
CHANGED_ROWS = 584
ROUNDS = 3


def _check_database(url: str) -> None:
    if urlsplit(url).path.lstrip("/") != DATABASE_NAME:
        raise RuntimeError("refusing benchmark outside dedicated diagnostic database")
    with psycopg.connect(url, connect_timeout=5) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            if cursor.fetchone()[0] != DATABASE_NAME:
                raise RuntimeError("connected database is not the dedicated diagnostic database")


def _sources(code: str, *, changed: bool) -> list[ObservationRevisionSource]:
    start = datetime(2099, 1, 5, 9, 0)
    result = []
    for index in range(ROWS):
        bar_time = start + timedelta(days=index // 300, minutes=index % 300)
        close = 10_000 + index
        high = close + 100
        if changed and index < CHANGED_ROWS:
            close += 1
            high += 1
        value = {
            "trading_date": bar_time.date().isoformat(),
            "minute": bar_time.strftime("%H:%M"),
            "code": code,
            "market": "KRX",
            "open": 10_000 + index,
            "high": high,
            "low": 9_900 + index,
            "close": close,
            "volume": 100 + index,
            "trade_value_million_won": 1 + index,
            "updated_at": 1_790_000_000.0,
        }
        observation = minute_bar_observation(
            value,
            origin=ObservationOrigin.QUERY,
            completeness=DataCompleteness.COMPLETE,
            source=SOURCE,
            value_kind=DataValueKind.ESTIMATED,
        )
        payload = minute_bar_revision_payload(
            value,
            window_closed=True,
            capture_quality="complete",
            finalization_source="synthetic_revision_benchmark",
        )
        result.append(ObservationRevisionSource.from_observation(
            "minute_bar", observation.subject, bar_observation_key(observation),
            payload, observation,
        ))
    return result


def _key(source: ObservationRevisionSource) -> tuple[str, str, str]:
    return source.subject, source.observation_key, source.source_id


def _seed(url: str, sources: list[ObservationRevisionSource]) -> None:
    with psycopg.connect(url, connect_timeout=5) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = '30s'")
            none_by_key = { _key(source): None for source in sources }
            statements, inserted = _insert_postgres_observation_revisions_batch(
                cursor, sources, none_by_key,
            )
            if inserted != ROWS or statements != 1:
                raise RuntimeError(f"unexpected seed result: statements={statements}, rows={inserted}")


def _current_batch(url: str, sources: list[ObservationRevisionSource]) -> dict[str, int | float | None]:
    with psycopg.connect(url, connect_timeout=5) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = '30s'")
            started = time.perf_counter()
            timings: dict[str, float] = {}
            latest = _load_postgres_latest_revisions(cursor, sources, timings=timings)
            execute_seconds = [0.0]
            statements, inserted = _insert_postgres_observation_revisions_batch(
                cursor, sources, latest, execute_seconds=execute_seconds,
            )
            operation_ms = round((time.perf_counter() - started) * 1000, 3)
            commit_started = time.perf_counter()
            connection.commit()
            commit_ms = round((time.perf_counter() - commit_started) * 1000, 3)
            if inserted != CHANGED_ROWS:
                raise RuntimeError(f"batch inserted {inserted}; expected {CHANGED_ROWS}")
            return {
                "lookup_statements": 1,
                "lookup_ms": round(timings.get("lookup_seconds", 0) * 1000, 3),
                "lock_ms": round(timings.get("lock_seconds", 0) * 1000, 3),
                "insert_statements": statements,
                "insert_rows": inserted,
                "insert_execute_ms": round(execute_seconds[0] * 1000, 3),
                "operation_ms": operation_ms,
                "commit_ms": commit_ms,
                "total_ms": round(operation_ms + commit_ms, 3),
            }


def _legacy_rowwise(url: str, sources: list[ObservationRevisionSource]) -> dict[str, int | float | None]:
    with psycopg.connect(url, connect_timeout=5) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = '30s'")
            subject = sources[0].subject
            source_id = sources[0].source_id
            lock_key = json.dumps(
                ("minute_bar", subject, source_id),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            started = time.perf_counter()
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (lock_key,),
            )
            lookup_ms = insert_ms = 0.0
            inserted = 0
            for source in sources:
                lookup_started = time.perf_counter()
                cursor.execute(
                    "SELECT revision_id,payload_hash FROM central_observation_revisions "
                    "WHERE kind=%s AND subject=%s AND observation_key=%s AND source_id=%s "
                    "ORDER BY accepted_sequence DESC LIMIT 1",
                    (source.kind, source.subject, source.observation_key, source.source_id),
                )
                latest = cursor.fetchone()
                lookup_ms += time.perf_counter() - lookup_started
                execute_seconds = [0.0]
                if _insert_postgres_observation_revision(
                    cursor, source, latest, execute_seconds=execute_seconds,
                ) is not None:
                    inserted += 1
                insert_ms += execute_seconds[0]
            operation_ms = round((time.perf_counter() - started) * 1000, 3)
            commit_started = time.perf_counter()
            connection.commit()
            commit_ms = round((time.perf_counter() - commit_started) * 1000, 3)
            if inserted != CHANGED_ROWS:
                raise RuntimeError(f"row-wise inserted {inserted}; expected {CHANGED_ROWS}")
            return {
                "lookup_statements": ROWS,
                "lookup_ms": round(lookup_ms * 1000, 3),
                "lock_ms": None,
                "insert_statements": inserted,
                "insert_rows": inserted,
                "insert_execute_ms": round(insert_ms * 1000, 3),
                "operation_ms": operation_ms,
                "commit_ms": commit_ms,
                "total_ms": round(operation_ms + commit_ms, 3),
            }


def _summary(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    p95_index = max(0, min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1))
    return {
        "min": round(ordered[0], 3),
        "median": round(statistics.median(ordered), 3),
        "p95_nearest_rank": round(ordered[p95_index], 3),
        "max": round(ordered[-1], 3),
    }


def main() -> None:
    url = os.environ.get("KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL", "")
    if not url:
        raise RuntimeError("KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL is required")
    _check_database(url)
    runs: dict[str, list[dict[str, int | float | None]]] = {"rowwise": [], "batch": []}
    subjects: list[str] = []
    try:
        # One unreported warm-up pair; alternate measured order to reduce cache/order bias.
        for trial in range(ROUNDS + 1):
            targets: dict[str, list[ObservationRevisionSource]] = {}
            for strategy in ("batch", "rowwise"):
                code = f"9{uuid.uuid4().int % 100000:05d}"
                seed = _sources(code, changed=False)
                targets[strategy] = _sources(code, changed=True)
                subjects.append(seed[0].subject)
                _seed(url, seed)
            order = ("batch", "rowwise") if trial % 2 == 0 else ("rowwise", "batch")
            for strategy in order:
                result = (_current_batch(url, targets[strategy]) if strategy == "batch"
                          else _legacy_rowwise(url, targets[strategy]))
                if trial > 0:
                    runs[strategy].append(result)
    finally:
        if subjects:
            with psycopg.connect(url, connect_timeout=5) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM central_observation_revisions "
                        "WHERE kind='minute_bar' AND subject = ANY(%s)", (subjects,),
                    )
    result = {
        "database": DATABASE_NAME,
        "rows_per_trial": ROWS,
        "changed_rows_per_trial": CHANGED_ROWS,
        "measured_rounds_per_strategy": ROUNDS,
        "warmup_rounds_per_strategy": 1,
        "method": "alternating isolated synthetic subjects; one transaction per strategy; "
                  "seed rows excluded from timings; revision rows deleted after run",
        "rowwise": {
            "samples": runs["rowwise"],
            "summary_ms": {
                key: _summary([float(item[key]) for item in runs["rowwise"]])
                for key in ("lookup_ms", "insert_execute_ms", "operation_ms", "commit_ms", "total_ms")
            },
        },
        "batch": {
            "samples": runs["batch"],
            "summary_ms": {
                key: _summary([float(item[key]) for item in runs["batch"]])
                for key in ("lookup_ms", "insert_execute_ms", "operation_ms", "commit_ms", "total_ms")
            },
        },
        "note": "Synthetic test-database timing isolates revision read/insert work; it is not NAS live-load attribution.",
    }
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
