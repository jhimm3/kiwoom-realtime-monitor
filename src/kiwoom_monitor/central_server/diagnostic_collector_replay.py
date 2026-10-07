"""Fixed 0B inputs through the real collector and its owned persistence loop.

This is a collector correctness/load fixture, not a reconstruction of old DB
traces. Public execution is restricted to the existing dedicated test database.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Event, Lock
from urllib.parse import urlsplit

from .diagnostic_metrics import CURRENT_FLUSH_ID
from .diagnostic_replay import TEST_DATABASE_NAME, _ReplayStore
from .postgres_access import db_call_request_id, db_call_source
from .realtime_collector import CentralRealtimeCollector
from .realtime_hub import RealtimeHub


FIXTURE_VERSION = "collector-0b/v1"
FIXTURE_SECONDS = 360
MAX_CALL_RECORDS = 1024
_MARKETS = ("KRX", "NXT", "SOR")
_TABLES = ("central_minute_bars", "central_second_trade_bars", "central_realtime_latest",
           "central_market_data_observation_meta", "central_observation_revisions",
           "central_minute_bar_operations")


@dataclass(frozen=True)
class CollectorFixture:
    code: str
    origin: datetime
    messages: tuple[tuple[float, dict], ...]
    minute_rows: tuple[dict, ...]
    second_rows: tuple[dict, ...]
    duration_seconds: int = FIXTURE_SECONDS


def build_collector_fixture(run_id: str) -> CollectorFixture:
    if not re.fullmatch(r"[A-Za-z0-9-]{1,80}", run_id):
        raise ValueError("invalid_collector_replay_identifier")
    code = f"diagnostic-collector-{run_id}-000"
    origin = datetime(2026, 10, 6, 9, 59, 58, tzinfo=timezone(timedelta(hours=9)))
    cumulative = {market: 5000 for market in _MARKETS}
    messages, minutes, seconds = [], {}, {}

    def row(market, at, price, volume):
        suffix = {"KRX": "", "NXT": "_NX", "SOR": "_AL"}[market]
        return {"type": "0B", "item": code + suffix,
                "values": {"10": str(price), "13": str(1_000_000 + cumulative[market]),
                           "14": str(cumulative[market]), "15": str(volume),
                           "20": at.strftime("%H%M%S"), "311": "12345"}}

    def expected(market, at, price, volume, value_delta, *, duplicate=False):
        # Independent fixture ledger: explicit trades and known million-won
        # increments, without calling either accumulator or the storage writer.
        for target, key, amount in ((minutes, at.strftime("%H:%M"), value_delta),
                                    (seconds, at.strftime("%H:%M:%S"), price * volume)):
            if target is seconds and duplicate:
                continue  # The existing second accumulator deduplicates provider signatures.
            record = target.setdefault((key, market), {
                "code": code, "trading_date": origin.date().isoformat(), "market": market,
                "minute" if target is minutes else "trade_second": key,
                "open": price, "high": price, "low": price, "close": price,
                "volume": 0, "trade_value_million_won" if target is minutes else "trade_value_won": 0,
                **({"trade_count": 0} if target is seconds else {}),
            })
            record["high"] = max(record["high"], price)
            record["low"] = min(record["low"], price)
            record["close"] = price
            record["volume"] += volume
            record["trade_value_million_won" if target is minutes else "trade_value_won"] += amount
            if target is seconds:
                record["trade_count"] += 1

    for offset in range(FIXTURE_SECONDS):
        at = origin + timedelta(seconds=offset)
        price = 1_000_000 if offset % 2 == 0 else 2_000_000
        rows = []
        for market in _MARKETS:
            cumulative[market] += price // 1_000_000
            rows.append(row(market, at, price, 1))
            expected(market, at, price, 1, 0 if offset == 0 else price // 1_000_000)
        messages.append((float(offset), {"trnm": "REAL", "data": rows}))
        if offset == 40:
            # Minute volume currently includes repeat 0B; seconds deduplicate it.
            messages.append((40.01, {"trnm": "REAL", "data": rows}))
            for market in _MARKETS:
                expected(market, at, price, 1, 0, duplicate=True)
        if offset == 63:
            late_at = origin + timedelta(seconds=61)  # Prior minute, two seconds late.
            late_rows = []
            for market in _MARKETS:
                cumulative[market] += 6
                late_rows.append(row(market, late_at, 3_000_000, 2))
                expected(market, late_at, 3_000_000, 2, 6)
            messages.append((63.01, {"trnm": "REAL", "data": late_rows}))
    return CollectorFixture(code, origin, tuple(messages), tuple(minutes.values()), tuple(seconds.values()))


class _ReplayClock:
    correctness_only = False

    def __init__(self, origin: datetime):
        self.origin = origin
        self.started = time.monotonic()

    def elapsed(self):
        return time.monotonic() - self.started

    def now(self):
        return self.origin + timedelta(seconds=self.elapsed())

    async def sleep(self, seconds):
        await asyncio.sleep(seconds)

    async def wait_until(self, offset, stop):
        while not stop.is_set() and self.elapsed() < offset:
            await asyncio.sleep(min(0.1, offset - self.elapsed()))


class _MeasuredStore:
    """Count real writer invocations without merging connections/transactions."""

    def __init__(self, store, clock):
        self.store, self.clock = store, clock
        self.phase = "measurement"
        self.input_seq = 0
        self.calls = []
        self.operation_ids = set()
        self.records_dropped = 0
        self._lock = Lock()

    def __getattr__(self, name):
        return getattr(self.store, name)

    def _write(self, name, kind, values, **kwargs):
        record = {"kind": kind, "flush_id": CURRENT_FLUSH_ID.get(), "phase": self.phase,
                  "source_time": self.clock.now().isoformat(), "input_seq_high_water": self.input_seq,
                  "rows_attempted": len(values), "state": "failed"}
        started = time.perf_counter()
        with self._lock:
            self.operation_ids.update(str(value["operation_id"]) for value in values
                                      if isinstance(value, dict) and value.get("operation_id"))
        try:
            result = getattr(self.store, name)(values, **kwargs)
            record["state"] = "returned"
            return result
        except Exception as error:
            record["error_type"] = type(error).__name__
            raise
        finally:
            record["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
            # Actual changes, SQL/COMMIT and db_call_id belong to the existing
            # observer. The API integration joins them by request_id/flush_id.
            record["database_metrics"] = "requires_db_call_observer_join"
            with self._lock:
                if len(self.calls) < MAX_CALL_RECORDS:
                    self.calls.append(record)
                else:
                    self.records_dropped += 1

    def save_realtime_snapshots(self, values):
        return self._write("save_realtime_snapshots", "realtime_latest", values)

    def save_minute_bars(self, values, *, observations=None):
        return self._write("save_minute_bars", "realtime_minute", values, observations=observations)

    def finalize_minute_bars(self, values):
        return self._write("finalize_minute_bars", "realtime_minute_finalize", values)

    def save_second_trade_bars(self, values):
        return self._write("save_second_trade_bars", "realtime_second_bar", values)


async def _owned_close(collector):
    task = asyncio.create_task(collector.close())
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    task.result()
    if cancelled:
        raise asyncio.CancelledError


async def _exercise_fixture(measured, fixture, stop, clock):
    def no_token():
        raise AssertionError("collector replay attempted a Kiwoom token request")

    collector = CentralRealtimeCollector(no_token, "real", RealtimeHub(), clock.now,
                                         measured, snapshot_sleep=clock.sleep)
    inputs, lags, pending_peak = 0, [], 0
    with db_call_source("diagnostic.collector_replay"), db_call_request_id(fixture.code):
        await collector.start_input_replay()
        collector.accept_replay_sources({(fixture.code, market) for market in _MARKETS}, reset_all=True)
        try:
            for offset, message in fixture.messages:
                await clock.wait_until(offset, stop)
                if stop.is_set():
                    break
                lags.append(max(0, (clock.elapsed() - offset) * 1000))
                inputs += 1
                measured.input_seq = inputs
                collector.accept_replay_message(message)
                pending_peak = max(pending_peak, collector.input_replay_status()["pending_records"])
            if not stop.is_set():
                await clock.wait_until(fixture.duration_seconds, stop)
        finally:
            measured.phase = "drain"
            drain_started = time.perf_counter()
            await _owned_close(collector)
            drain_ms = (time.perf_counter() - drain_started) * 1000
    status = collector.input_replay_status()
    return {"state": "aborted" if stop.is_set() else "complete", "fixture_version": FIXTURE_VERSION,
            "fidelity": "correctness_only" if clock.correctness_only else "synthetic_collector_inputs_1x",
            "initial_state": "cold", "database_state": "isolated_fixture", "input_messages": inputs,
            "expected_input_messages": len(fixture.messages), "input_rows": inputs * len(_MARKETS),
            "input_timing_preserved": not clock.correctness_only and not stop.is_set()
                and inputs == len(fixture.messages) and max(lags, default=0) <= 100,
            "input_schedule_lag_ms_max": max(lags, default=0), "pending_records_peak": pending_peak,
            "pending_records_after_drain": status["pending_records"], "drain_ms": round(drain_ms, 3),
            "calls": measured.calls, "call_records_dropped": measured.records_dropped,
            "scope_note": "0B parser, RAM accumulators and actual collector persistence loop; no Kiwoom network, news, TOP20 REST or original NAS/DB state"}


def _preflight(database_url, fixture):
    import psycopg

    if urlsplit(database_url).path.lstrip("/") != TEST_DATABASE_NAME:
        raise ValueError("collector_replay_requires_dedicated_database")
    with psycopg.connect(database_url, autocommit=True, connect_timeout=5,
                         options="-c default_transaction_read_only=on -c statement_timeout=5000") as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            if cursor.fetchone()[0] != TEST_DATABASE_NAME:
                raise RuntimeError("collector_replay_database_mismatch")
            for table in _TABLES:
                cursor.execute("SELECT to_regclass(%s)", (table,))
                if cursor.fetchone()[0] is None:
                    raise RuntimeError("collector_replay_table_missing")
            for table, where, values in _scope(fixture, ()):
                cursor.execute(f"SELECT 1 FROM {table} WHERE {where} LIMIT 1", values)
                if cursor.fetchone():
                    raise RuntimeError("collector_replay_scope_not_empty")


def _scope(fixture, operation_ids):
    subjects = [f"{fixture.code}:{market}" for market in _MARKETS]
    return (("central_observation_revisions", "kind='minute_bar' AND subject=ANY(%s)", (subjects,)),
            ("central_market_data_observation_meta", "dataset_kind='minute_bar' AND subject=ANY(%s)", (subjects,)),
            ("central_minute_bar_operations", "operation_id=ANY(%s)", (list(operation_ids),)),
            ("central_minute_bars", "code=%s AND trading_date=%s", (fixture.code, fixture.origin.date().isoformat())),
            ("central_second_trade_bars", "code=%s AND trading_date=%s", (fixture.code, fixture.origin.date().isoformat())),
            ("central_realtime_latest", "event_type='trade' AND item_key=%s", (fixture.code,)))


def _verify_fixture(store, fixture):
    actual_minutes = store.load_minute_bars(fixture.code, fixture.origin.date().isoformat())
    def signature(rows, key):
        return sorted(tuple(value[name] for name in (key, "market", "open", "high", "low", "close", "volume",
                        "trade_value_million_won" if key == "minute" else "trade_value_won",
                        *(('trade_count',) if key == 'trade_second' else ()))) for value in rows)
    with closing(store._connect()) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT to_char(trade_second,'HH24:MI:SS'),market,open,high,low,close,volume,trade_value_won,trade_count "
                       "FROM central_second_trade_bars WHERE code=%s AND trading_date=%s",
                       (fixture.code, fixture.origin.date().isoformat()))
        actual_seconds = sorted(tuple(row) for row in cursor.fetchall())
        cursor.execute("SELECT event_json FROM central_realtime_latest WHERE event_type='trade' AND item_key=%s",
                       (fixture.code,))
        latest = cursor.fetchone()
        event = latest[0] if latest and isinstance(latest[0], dict) else json.loads(latest[0]) if latest else {}
        cursor.execute("SELECT payload_json FROM central_observation_revisions WHERE kind='minute_bar' AND subject=ANY(%s)",
                       ([f"{fixture.code}:{market}" for market in _MARKETS],))
        closed = sum(bool((row[0] if isinstance(row[0], dict) else json.loads(row[0])).get("window_closed"))
                     for row in cursor.fetchall())
    result = {"minute_rows": len(actual_minutes), "second_rows": len(actual_seconds),
              "minute_values_match": signature(actual_minutes, "minute") == signature(fixture.minute_rows, "minute"),
              "second_values_match": actual_seconds == signature(fixture.second_rows, "trade_second"),
              "latest_matches": event.get("payload", {}).get("market") == "SOR"
                  and event.get("payload", {}).get("trade_time") == (fixture.origin + timedelta(seconds=359)).strftime("%H%M%S"),
              "closed_revision_rows": closed}
    result["passed"] = all(result[key] for key in ("minute_values_match", "second_values_match", "latest_matches")) and closed >= 18
    return result


def _cleanup(store, fixture, operation_ids):
    with closing(store._connect()) as connection, connection.cursor() as cursor:
        for table, where, values in _scope(fixture, operation_ids):
            cursor.execute(f"DELETE FROM {table} WHERE {where}", values)
            cursor.execute(f"SELECT count(*) FROM {table} WHERE {where}", values)
            if cursor.fetchone()[0]:
                raise RuntimeError("collector_replay_cleanup_incomplete")
        connection.commit()


def run_collector_fixture(database_url: str, run_id: str, stop: Event,
                          ready: Event, started: Event, measurement_done: Event) -> dict:
    """Same ready/start handshake as DB-call replay; runtime remains real 1x."""
    fixture = build_collector_fixture(run_id)
    _preflight(database_url, fixture)
    store = _ReplayStore(database_url)
    measured = None
    result = {"state": "aborted", "fixture_version": FIXTURE_VERSION}
    ready.set()
    try:
        while not started.wait(0.1):
            if stop.is_set():
                return result
        clock = _ReplayClock(fixture.origin)
        measured = _MeasuredStore(store, clock)
        result = asyncio.run(_exercise_fixture(measured, fixture, stop, clock))
        result["fixture_verification"] = _verify_fixture(store, fixture)
        if result["state"] == "complete" and (not result["fixture_verification"]["passed"] or result["call_records_dropped"]):
            result["state"] = "incomplete"
        return result
    except Exception as error:
        result.update({"state": "failed", "error_type": type(error).__name__,
                       "calls": measured.calls if measured else []})
        return result
    finally:
        # The asyncio lifetime above always drains before these scoped deletes.
        # A failed verification is also cleaned; operation IDs include failed ACKs.
        _cleanup(store, fixture, measured.operation_ids if measured else ())
        result["cleanup"] = "passed"
        measurement_done.set()
