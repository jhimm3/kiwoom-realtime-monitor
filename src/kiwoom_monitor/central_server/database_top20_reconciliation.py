"""Consistent reconciliation reads and opt-in display statistics cache."""
from __future__ import annotations

from datetime import date, datetime
import json
from time import time
from typing import Any

from kiwoom_monitor.domain.top20_reconciliation import (
    KST, RECONCILED_KIND, Top20ReconciliationInput, display_statistics, select_top20_series,
)
from .database_codec import MINUTE_BAR_COLUMNS
from .database_top20_statistics import _top20_market_values, _top20_statistics_days, _top20_statistics_merge, _top20_today
from .postgres_access import DBWriterContext, open_observed_connection
from .diagnostic_workloads import is_paused
from .diagnostic_top20_display_input import environment_value


def _document(value: object) -> dict[str, Any]:
    result = value if isinstance(value, dict) else json.loads(str(value))
    if not isinstance(result, dict):
        raise ValueError("TOP20 stored document is not an object")
    return result


def _series(cursor: Any, day: str, marker: str, basis: str) -> dict[str, Any]:
    cursor.execute("SELECT kind,payload_json FROM central_dataset_snapshots WHERE subject=" + marker
                   + " AND kind IN ('top20_index','top20_index_reconciled') ORDER BY snapshot_key", (day,))
    raw, reconciled = [], None
    for kind, payload in cursor.fetchall():
        if kind == RECONCILED_KIND:
            reconciled = _document(payload)
        else:
            raw.append(_document(payload))
    return select_top20_series(day, raw, reconciled, basis=basis)


def _inputs(cursor: Any, day: str, marker: str) -> Top20ReconciliationInput:
    cursor.execute("SELECT snapshot_key,payload_json FROM central_dataset_snapshots "
                   "WHERE kind='top20_index' AND subject=" + marker + " ORDER BY snapshot_key", (day,))
    cohorts = {str(key)[:16]: _document(value) for key, value in cursor.fetchall()}
    codes: set[str] = set()
    for payload in cohorts.values():
        for segment in payload.get("cohort_segments", []):
            if isinstance(segment, (tuple, list)) and len(segment) == 2 and isinstance(segment[1], (tuple, list)):
                codes.update(str(code) for code in segment[1])
    if len(codes) >= 5000:
        raise ValueError("TOP20 reconciliation input limit reached")
    pairs: dict[str, dict[str, Any]] = {}
    eligibility: dict[str, bool | None] = {}
    markets: dict[str, str] = {}
    sources: list[dict[str, Any]] = []
    sorted_codes = sorted(codes)
    meta_names = ("venue", "unit", "value_kind", "completeness", "origin", "source")
    for offset in range(0, len(sorted_codes), 200):
        chunk = sorted_codes[offset:offset + 200]
        slots = ",".join([marker] * len(chunk))
        day_column = "b.trading_date::text" if marker == "%s" else "b.trading_date"
        minute_column = "to_char(b.minute,'HH24:MI')" if marker == "%s" else "b.minute"
        columns = [day_column, minute_column, *("b." + key for key in MINUTE_BAR_COLUMNS[2:])]
        cursor.execute("SELECT " + ",".join(columns)
                       + "," + ",".join("m." + key for key in meta_names)
                       + " FROM central_minute_bars b LEFT JOIN central_market_data_observation_meta m "
                       "ON m.dataset_kind='minute_bar' AND m.subject=b.code||':'||b.market "
                       "AND m.observation_key=" + day_column + "||'T'||" + minute_column + " WHERE b.trading_date=" + marker
                       + " AND b.code IN (" + slots + ") AND b.market IN ('KRX','NXT')", (day, *chunk))
        for raw in cursor.fetchall():
            bar = dict(zip(MINUTE_BAR_COLUMNS, raw[:len(MINUTE_BAR_COLUMNS)], strict=True))
            subject = str(bar["code"]) + ":" + str(bar["market"])
            pair = pairs.setdefault(subject, {"bars": {}, "metadata": {}, "coverage": {}})
            pair["bars"][bar["minute"]] = bar
            pair["metadata"][bar["minute"]] = dict(zip(meta_names, raw[len(MINUTE_BAR_COLUMNS):], strict=True))
        cursor.execute("SELECT subject,payload_json FROM central_dataset_snapshots WHERE subject IN ("
                       + slots + ") AND kind='nxt_eligibility' AND snapshot_key=" + marker, (*chunk, day))
        for code, raw in cursor.fetchall():
            value = _document(raw)
            if str(value.get("observed_at", ""))[:10] == day and isinstance(value.get("enabled"), bool):
                eligibility[str(code)] = value["enabled"]
                sources.append({"kind": "nxt_eligibility", "subject": str(code), "enabled": value["enabled"]})
        cursor.execute("SELECT document_key,document_json FROM central_documents WHERE collection='stock_catalog' "
                       "AND owner='krx' AND document_key IN (" + slots + ")", tuple(chunk))
        for code, raw in cursor.fetchall():
            value = _document(raw)
            if value.get("as_of") == day:
                markets[str(code)] = str(value.get("market", ""))
    for offset in range(0, len(sorted_codes), 200):
        chunk = sorted_codes[offset:offset + 200]
        owners = tuple(f"{day}:{code}:{venue}" for code in chunk for venue in ("KRX", "NXT"))
        slots = ",".join([marker] * len(owners))
        cursor.execute("SELECT owner,document_json FROM central_documents WHERE collection='market_data_coverage' "
                       "AND document_key='complete' AND owner IN (" + slots + ")", owners)
        coverage = {str(owner): _document(raw) for owner, raw in cursor.fetchall()}
        for code in chunk:
            for venue in ("KRX", "NXT"):
                subject = f"{code}:{venue}"
                document = coverage.get(f"{day}:{subject}")
                if document is not None:
                    pairs.setdefault(subject, {"bars": {}, "metadata": {}, "coverage": {}})["coverage"] = document
    return Top20ReconciliationInput(day, cohorts, pairs, eligibility, markets,
                                    environment_value('computed_at', lambda: datetime.now(KST).isoformat()), sources)


def _statistics(cursor: Any, start: str, end: str, marker: str) -> dict[str, Any]:
    days = _top20_statistics_days(start, end,
        today=date.fromisoformat(environment_value('today', lambda: _top20_today().isoformat())))
    results = {}
    for day in days:
        historical = day < environment_value('today', lambda: _top20_today().isoformat())
        if historical and marker == "%s":
            cursor.execute("SELECT pg_advisory_xact_lock(%s,%s)", (902025, int(day.replace("-", ""))))
        cursor.execute("SELECT payload_json FROM central_dataset_snapshots "
                       "WHERE kind='top20_statistics_display_day' AND subject=" + marker, (day,))
        cached = cursor.fetchone() if historical else None
        if cached is not None:
            results[day] = _document(cached[0])
            continue
        series = _series(cursor, day, marker, "display")
        raw_day = day.replace("-", "")
        cursor.execute("SELECT subject,payload_json FROM central_dataset_snapshots WHERE kind='market_index_chart' "
                       "AND subject>=" + marker + " AND subject<" + marker, (raw_day, raw_day + "~"))
        amounts = _top20_market_values(cursor.fetchall()).get(day, {})
        document = display_statistics(series, {"kospi_eok": amounts.get("kospi", 0.0),
                                               "kosdaq_eok": amounts.get("kosdaq", 0.0),
                                               "market_complete": all(key in amounts for key in ("kospi", "kosdaq"))})
        results[day] = document
        if historical and not environment_value('cache_write_paused', lambda: is_paused("top20_after_close")):
            cursor.execute("INSERT INTO central_dataset_snapshots(kind,subject,snapshot_key,saved_at,payload_json) "
                           "VALUES('top20_statistics_display_day'," + ",".join([marker] * 4) + ") "
                           "ON CONFLICT(kind,subject,snapshot_key) DO UPDATE SET saved_at=excluded.saved_at,"
                           "payload_json=excluded.payload_json", (day, day, environment_value('saved_at', time), json.dumps(document)))
    return {"basis": "display", **_top20_statistics_merge(results, days, include_sample_count=True)}


class SQLiteTop20ReconciliationStoreMixin:
    def load_top20_reconciliation_inputs(self, trading_date: str) -> Top20ReconciliationInput:
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN")
            return _inputs(connection.cursor(), trading_date, "?")

    def load_top20_series(self, trading_date: str, *, basis: str = "display") -> dict[str, Any]:
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN")
            return _series(connection.cursor(), trading_date, "?", basis)

    def load_top20_display_statistics(self, start_date: str, end_date: str) -> dict[str, Any]:
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN")
            return _statistics(connection.cursor(), start_date, end_date, "?")


class PostgresTop20ReconciliationStoreMixin:
    def load_top20_reconciliation_inputs(self, trading_date: str) -> Top20ReconciliationInput:
        context = DBWriterContext(writer_family="read.top20_reconciliation", writer_kind="top20_inputs",
                                  operation="load_top20_reconciliation_inputs", access_mode="read")
        with open_observed_connection(self._connect, context) as connection, connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            return _inputs(cursor, trading_date, "%s")

    def load_top20_series(self, trading_date: str, *, basis: str = "display") -> dict[str, Any]:
        context = DBWriterContext(writer_family="read.top20_reconciliation", writer_kind="top20_series",
                                  operation="load_top20_series", access_mode="read")
        with open_observed_connection(self._connect, context) as connection, connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            return _series(cursor, trading_date, "%s", basis)

    def load_top20_display_statistics(self, start_date: str, end_date: str) -> dict[str, Any]:
        context = DBWriterContext(writer_family="dataset.display_statistics_cache", writer_kind="dataset:top20_statistics_display_day",
                                  operation="load_top20_display_statistics")
        with open_observed_connection(self._connect, context) as connection, connection.cursor() as cursor:
            return _statistics(cursor, start_date, end_date, "%s")
