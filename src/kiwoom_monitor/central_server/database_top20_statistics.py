"""TOP20 statistics aggregation and daily cache for the existing store backends.

Snapshot writers retain cache invalidation in their own transaction. This module
uses the backend connection/lock supplied by the store and lower DB helpers.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from time import time
from zoneinfo import ZoneInfo


def _top20_statistics_document(
    top20_rows: list[tuple[object, object, object]],
    market_rows: list[tuple[object, object]],
) -> dict[str, object]:
    """Aggregate stored NAS TOP20 minutes without sending raw yearly rows to the app."""
    hourly_totals: dict[str, float] = defaultdict(float)
    hourly_counts: dict[str, int] = defaultdict(int)
    hourly_days: dict[str, set[str]] = defaultdict(set)
    daily_values: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    for subject, snapshot_key, raw_payload in top20_rows:
        payload = raw_payload if isinstance(raw_payload, dict) else json.loads(str(raw_payload))
        if not isinstance(payload, dict) or payload.get("capture_state") != "realtime_complete":
            continue
        minute = str(payload.get("minute") or snapshot_key)
        clock = minute[11:16]
        values = payload.get("market_values")
        if not isinstance(values, (list, tuple)) or len(values) < 3:
            continue
        amounts = [float(values[index] or 0.0) for index in range(3)]
        if sum(amounts) <= 0:
            continue
        day = str(subject)
        if "08:00" <= clock < "20:00":
            hour = f"{clock[:2]}:00"
            hourly_totals[hour] += sum(amounts)
            hourly_counts[hour] += 1
            hourly_days[hour].add(day)
        # The 15:30 closing-auction execution belongs to the KRX regular day.
        if "09:00" <= clock < "15:31":
            for index, amount in enumerate(amounts):
                daily_values[day][index] += amount

    market_values: dict[str, dict[str, float]] = defaultdict(dict)
    for subject, raw_payload in market_rows:
        subject_text = str(subject)
        raw_day, separator, market = subject_text.partition(":")
        if not separator or market not in {"kospi", "kosdaq"}:
            continue
        payload = raw_payload if isinstance(raw_payload, dict) else json.loads(str(raw_payload))
        daily = payload.get("daily") if isinstance(payload, dict) else None
        if not isinstance(daily, list):
            continue
        record = next(
            (value for value in daily if isinstance(value, dict) and str(value.get("dt", "")) == raw_day),
            None,
        )
        if record is None:
            continue
        try:
            market_values[f"{raw_day[:4]}-{raw_day[4:6]}-{raw_day[6:8]}"][market] = (
                abs(float(str(record.get("trde_prica", 0)).replace(",", ""))) / 100
            )
        except (TypeError, ValueError):
            continue

    hourly = [
        {
            "hour": hour,
            "average_eok": hourly_totals[hour] / hourly_counts[hour],
            "day_count": len(hourly_days[hour]),
            "sample_count": hourly_counts[hour],
        }
        for hour in sorted(hourly_totals, key=lambda value: hourly_totals[value] / hourly_counts[value], reverse=True)
    ]
    comparisons = []
    for day in sorted(daily_values):
        top20 = daily_values[day]
        market = market_values.get(day, {})
        comparisons.append({
            "trade_date": day,
            "top20_eok": sum(top20),
            "top20_market_values": top20,
            "kospi_eok": float(market.get("kospi", 0.0)),
            "kosdaq_eok": float(market.get("kosdaq", 0.0)),
        })
    return {"hourly": hourly, "comparisons": comparisons}


def _top20_statistics_days(start_date: str, end_date: str) -> list[str]:
    first, last = date.fromisoformat(start_date), min(date.fromisoformat(end_date), _top20_today())
    if last < first:
        return []
    return [(first + timedelta(days=offset)).isoformat() for offset in range((last - first).days + 1)]


def _top20_today() -> date:
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _top20_statistics_merge(daily: dict[str, dict[str, object]], days: list[str]) -> dict[str, object]:
    sums: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    comparisons: list[dict[str, object]] = []
    for day in days:
        document = daily.get(day, {})
        for row in document.get("hourly", []):
            hour, count = str(row["hour"]), int(row["sample_count"])
            sums[hour][0] += float(row["average_eok"]) * count
            sums[hour][1] += count
            sums[hour][2] += int(row["day_count"])
        comparisons.extend(document.get("comparisons", []))
    return {
        "hourly": [
            {"hour": hour, "average_eok": values[0] / values[1], "day_count": int(values[2])}
            for hour, values in sorted(sums.items(), key=lambda item: item[1][0] / item[1][1], reverse=True)
            if values[1] > 0
        ],
        "comparisons": comparisons,
    }


def _top20_statistics_group_days(
    days: list[str], top20_rows: list[tuple[object, object, object]],
    market_rows: list[tuple[object, object]],
) -> dict[str, dict[str, object]]:
    top20_by_day: dict[str, list[tuple[object, object, object]]] = defaultdict(list)
    market_by_day: dict[str, list[tuple[object, object]]] = defaultdict(list)
    for row in top20_rows:
        top20_by_day[str(row[0])].append(row)
    for row in market_rows:
        raw_day = str(row[0])[:8]
        market_by_day[f"{raw_day[:4]}-{raw_day[4:6]}-{raw_day[6:8]}"].append(row)
    return {
        day: _top20_statistics_document(top20_by_day.get(day, []), market_by_day.get(day, []))
        for day in days
    }


def _top20_statistics_cache_day(kind: str, subject: str) -> str:
    if kind == "top20_index":
        return subject[:10]
    if kind == "market_index_chart" and len(subject) >= 8:
        raw = subject[:8]
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    return ""


class SQLiteTop20StatisticsStoreMixin:
    def load_top20_statistics(self, start_date: str, end_date: str) -> dict[str, object]:
        days = _top20_statistics_days(start_date, end_date)
        if not days:
            return {"hourly": [], "comparisons": []}
        completed = [day for day in days if day < _top20_today().isoformat()]
        with self._lock, self._connection() as connection:
            cached = {
                str(day): json.loads(str(payload)) for day, payload in connection.execute(
                    "SELECT subject,payload_json FROM central_dataset_snapshots "
                    "WHERE kind='top20_statistics_day' AND subject>=? AND subject<=?",
                    (days[0], days[-1]),
                )
            }
            missing = [day for day in completed if day not in cached]
            needed = [*missing, *[day for day in days if day >= _top20_today().isoformat()]]
            if needed:
                start, end = min(needed), max(needed)
                top20_rows = connection.execute(
                    "SELECT subject,snapshot_key,payload_json FROM central_dataset_snapshots "
                    "WHERE kind='top20_index' AND subject>=? AND subject<=? ORDER BY snapshot_key",
                    (start, end),
                ).fetchall()
                market_rows = connection.execute(
                    "SELECT subject,payload_json FROM central_dataset_snapshots "
                    "WHERE kind='market_index_chart' AND subject>=? AND subject<? ORDER BY subject",
                    (start.replace("-", ""), end.replace("-", "") + "~"),
                ).fetchall()
                computed = _top20_statistics_group_days(
                    needed, [row for row in top20_rows if str(row[0]) in needed],
                    [row for row in market_rows if _top20_statistics_cache_day("market_index_chart", str(row[0])) in needed],
                )
                cached.update(computed)
                connection.executemany(
                    "INSERT INTO central_dataset_snapshots(kind,subject,snapshot_key,saved_at,payload_json) "
                    "VALUES('top20_statistics_day',?,?,?,?) ON CONFLICT(kind,subject,snapshot_key) "
                    "DO UPDATE SET saved_at=excluded.saved_at,payload_json=excluded.payload_json",
                    ((day, day, time(), json.dumps(computed[day], ensure_ascii=False)) for day in missing),
                )
        return _top20_statistics_merge(cached, days)


class PostgresTop20StatisticsStoreMixin:
    def load_top20_statistics(self, start_date: str, end_date: str) -> dict[str, object]:
        days = _top20_statistics_days(start_date, end_date)
        if not days:
            return {"hourly": [], "comparisons": []}
        completed = [day for day in days if day < _top20_today().isoformat()]
        from .postgres_access import DBWriterContext, open_observed_connection

        writer = DBWriterContext(
            writer_family="dataset.statistics_cache",
            writer_kind="dataset:top20_statistics_day",
            operation="load_top20_statistics",
        )
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT subject,payload_json FROM central_dataset_snapshots "
                "WHERE kind='top20_statistics_day' AND subject>=%s AND subject<=%s",
                (days[0], days[-1]),
            )
            cached = {str(day): payload if isinstance(payload, dict) else json.loads(str(payload))
                      for day, payload in cursor.fetchall()}
            missing = [day for day in completed if day not in cached]
            needed = [*missing, *[day for day in days if day >= _top20_today().isoformat()]]
            if needed:
                for day in sorted(missing):
                    cursor.execute(
                        "SELECT pg_advisory_xact_lock(%s,%s)",
                        (902025, int(day.replace("-", ""))),
                    )
                start, end = min(needed), max(needed)
                cursor.execute(
                    "SELECT subject,snapshot_key,payload_json FROM central_dataset_snapshots "
                    "WHERE kind='top20_index' AND subject>=%s AND subject<=%s ORDER BY snapshot_key",
                    (start, end),
                )
                top20_rows = cursor.fetchall()
                cursor.execute(
                    "SELECT subject,payload_json FROM central_dataset_snapshots "
                    "WHERE kind='market_index_chart' AND subject>=%s AND subject<%s ORDER BY subject",
                    (start.replace("-", ""), end.replace("-", "") + "~"),
                )
                market_rows = cursor.fetchall()
                computed = _top20_statistics_group_days(
                    needed, [row for row in top20_rows if str(row[0]) in needed],
                    [row for row in market_rows if _top20_statistics_cache_day("market_index_chart", str(row[0])) in needed],
                )
                cached.update(computed)
                cursor.executemany(
                    "INSERT INTO central_dataset_snapshots(kind,subject,snapshot_key,saved_at,payload_json) "
                    "VALUES('top20_statistics_day',%s,%s,%s,%s) ON CONFLICT(kind,subject,snapshot_key) "
                    "DO UPDATE SET saved_at=EXCLUDED.saved_at,payload_json=EXCLUDED.payload_json",
                    [(day, day, time(), json.dumps(computed[day], ensure_ascii=False)) for day in missing],
                )
        return _top20_statistics_merge(cached, days)
