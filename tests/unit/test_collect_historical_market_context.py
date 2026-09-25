from __future__ import annotations

import importlib.util
import json
import sqlite3
from contextlib import closing
from pathlib import Path



SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "collect_historical_market_context.py"
SPEC = importlib.util.spec_from_file_location("collect_historical_market_context", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)


def _raw(path: Path, code: str, kind: str, bars: list[dict[str, object]]) -> None:
    records = [
        {"record_type": "page", "code": code, "kind": kind,
         "observed_at": "2026-09-23T00:00:00+00:00", "bars": bars},
        {"record_type": "summary", "code": code, "kind": kind,
         "total_bars": len(bars), "provider_has_more": False},
    ]
    path.write_text("\n".join(json.dumps(row) for row in records) + "\n", encoding="utf-8")


def test_imports_only_candidate_day_fundamentals_and_preserves_raw(tmp_path: Path) -> None:
    reference = tmp_path / "reference.sqlite3"
    with closing(sqlite3.connect(reference)) as connection:
        with connection:
            connection.execute("CREATE TABLE candidate_days(code TEXT,dt TEXT)")
            connection.execute("INSERT INTO candidate_days VALUES('005930','2026-09-21')")
    database = tmp_path / "context.sqlite3"
    collector.initialize(database, reference)
    raw = tmp_path / "stock.ndjson"
    _raw(raw, "005930", "stock_daily", [
        {"date": day, "market_cap": 100, "shares": 5846278608,
         "close": 90000, "volume": 1, "trading_value": 2}
        for day in ("2026-09-21", "2026-09-22")
    ])
    count, _digest = collector.import_raw(
        database, raw, kind="stock_daily", code="005930",
        target=collector.target_dates(reference, "005930"),
    )
    assert count == 1
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute(
            "SELECT trade_date,float_shares,raw_file FROM historical_stock_fundamentals"
        ).fetchone() == ("2026-09-21", 5846278608, str(raw))


def test_seeds_adjustments_only_for_candidate_codes_and_deduplicates_events(tmp_path: Path) -> None:
    reference = tmp_path / "reference.sqlite3"
    with closing(sqlite3.connect(reference)) as connection:
        with connection:
            connection.execute("CREATE TABLE candidate_days(code TEXT,dt TEXT)")
            connection.executemany("INSERT INTO candidate_days VALUES(?,?)", [
                ("005930", "2021-01-04"), ("005930", "2026-09-21"),
                ("035720", "2026-09-21"),
            ])
    database = tmp_path / "context.sqlite3"
    collector.initialize(database, reference)
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute(
            "SELECT code FROM context_jobs WHERE kind='stock_adjustment' ORDER BY code"
        ).fetchall() == [("005930",), ("035720",)]
    raw = tmp_path / "adjustment.ndjson"
    _raw(raw, "035720", "stock_adjustment", [
        {"date": "2021-04-16", "raw_adjustment_date": 20210416,
         "adjustment_rate": 100.0},
        {"date": "2021-04-15", "raw_adjustment_date": 20210415,
         "adjustment_rate": 100.0},
        {"date": "2021-04-14", "raw_adjustment_date": 20210414,
         "adjustment_rate": 500.0},
        {"date": "2021-04-13", "raw_adjustment_date": 0,
         "adjustment_rate": 0.0},
    ])
    count, _ = collector.import_raw(
        database, raw, kind="stock_adjustment", code="035720", on_or_after_date="2021-04-15"
    )
    assert count == 1
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute(
            "SELECT code,adjustment_date,adjustment_rate,previous_factor,new_factor "
            "FROM historical_stock_adjustments"
        ).fetchone() == ("035720", "2021-04-15", 20.0, 500.0, 100.0)
    assert collector.reference_date_range(reference) == ("2021-01-04", "2026-09-21")


def test_index_5m_excludes_one_minute_overlap_and_keeps_decimal_price(tmp_path: Path) -> None:
    reference = tmp_path / "reference.sqlite3"
    with closing(sqlite3.connect(reference)) as connection:
        with connection:
            connection.execute("CREATE TABLE candidate_days(code TEXT,dt TEXT)")
    database = tmp_path / "context.sqlite3"
    collector.initialize(database, reference)
    raw = tmp_path / "index.ndjson"
    _raw(raw, "U001", "index_5m", [
        {"bar_time": f"{day}T09:05:00+09:00", "open": 2601.25,
         "high": 2602.5, "low": 2600.75, "close": 2602.25,
         "volume": 3, "trading_value": 4}
        for day in ("2024-08-28", "2024-08-29")
    ])
    count, _digest = collector.import_raw(
        database, raw, kind="index_5m", code="U001", before_date="2024-08-29"
    )
    assert count == 1
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute(
            "SELECT bar_time,open FROM historical_index_bars"
        ).fetchone() == ("2024-08-28T09:05:00+09:00", 2601.25)


def test_incomplete_raw_rolls_back_all_pages(tmp_path: Path) -> None:
    reference = tmp_path / "reference.sqlite3"
    with closing(sqlite3.connect(reference)) as connection:
        with connection:
            connection.execute("CREATE TABLE candidate_days(code TEXT,dt TEXT)")
    database = tmp_path / "context.sqlite3"
    collector.initialize(database, reference)
    raw = tmp_path / "incomplete.ndjson"
    raw.write_text(json.dumps({
        "record_type": "page", "code": "U001", "kind": "index_daily",
        "observed_at": "2026-09-23T00:00:00+00:00",
        "bars": [{"bar_time": "2026-09-22", "open": 1, "high": 2, "low": 1,
                  "close": 2, "volume": 3, "trading_value": 4}],
    }) + "\n", encoding="utf-8")
    try:
        collector.import_raw(database, raw, kind="index_daily", code="U001")
    except ValueError as error:
        assert "Incomplete" in str(error)
    else:
        raise AssertionError("Incomplete raw response was accepted")
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM historical_index_bars").fetchone()[0] == 0


def test_recent_index_overlay_uses_one_minute_key_without_duplicates(tmp_path: Path) -> None:
    reference = tmp_path / "reference.sqlite3"
    with closing(sqlite3.connect(reference)) as connection:
        with connection:
            connection.execute("CREATE TABLE candidate_days(code TEXT,dt TEXT)")
    database = tmp_path / "context.sqlite3"
    collector.initialize(database, reference)
    bars = [{"bar_time": f"2026-09-{day}T09:01:00+09:00", "open": 1,
             "high": 2, "low": 1, "close": 2, "volume": 3, "trading_value": 4}
            for day in ("21", "22")]
    period = tmp_path / "period.ndjson"
    recent = tmp_path / "recent.ndjson"
    _raw(period, "U001", "index_1m", bars[:1])
    _raw(recent, "U001", "index_1m_latest", bars)
    collector.import_raw(database, period, kind="index_1m", code="U001")
    count, _ = collector.import_raw(database, recent, kind="index_1m_latest", code="U001")
    assert count == 1
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute(
            "SELECT COUNT(*),MIN(interval_seconds),MAX(interval_seconds) "
            "FROM historical_index_bars"
        ).fetchone() == (2, 60, 60)


def test_provider_rejected_code_is_terminal_coverage_gap(tmp_path: Path) -> None:
    reference = tmp_path / "reference.sqlite3"
    with closing(sqlite3.connect(reference)) as connection:
        with connection:
            connection.execute("CREATE TABLE candidate_days(code TEXT,dt TEXT)")
            connection.execute("INSERT INTO candidate_days VALUES('008290','2026-09-21')")
    database = tmp_path / "context.sqlite3"
    collector.initialize(database, reference)
    with closing(sqlite3.connect(database)) as connection:
        with connection:
            connection.execute(
                "UPDATE context_jobs SET state='failed',attempts=3,error=? "
                "WHERE kind='stock_daily' AND code='008290'",
                (f"RuntimeError: {collector.PROVIDER_CODE_REJECTION}",),
            )
    collector.initialize(database, reference)
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute(
            "SELECT state FROM context_jobs WHERE kind='stock_daily' AND code='008290'"
        ).fetchone() == ("unavailable",)
    assert collector.claim(database, "stock_daily") is None


def test_non_stock_context_jobs_are_excluded_without_removing_existing_evidence(tmp_path: Path) -> None:
    reference = tmp_path / "reference.sqlite3"
    with closing(sqlite3.connect(reference)) as connection:
        with connection:
            connection.execute("CREATE TABLE candidate_days(code TEXT,dt TEXT)")
            connection.execute("CREATE TABLE stocks(code TEXT,market_code TEXT)")
            connection.executemany("INSERT INTO candidate_days VALUES(?,?)", [
                ("005930", "2026-09-21"), ("500029", "2026-09-21"),
                ("069500", "2026-09-21"),
            ])
            connection.executemany("INSERT INTO stocks VALUES(?,?)", [
                ("005930", "0"), ("500029", "60"), ("069500", "8"),
            ])
    database = tmp_path / "context.sqlite3"
    collector.initialize(database, reference)
    with closing(sqlite3.connect(database)) as connection:
        with connection:
            connection.execute(
                "INSERT INTO context_jobs(kind,code,state,attempts,error,updated_at) "
                "VALUES('stock_daily','500029','unavailable',3,'provider rejection','old')"
            )
            connection.execute(
                "INSERT INTO context_jobs(kind,code,state,attempts,error,updated_at) "
                "VALUES('stock_daily','069500','complete',1,'','old')"
            )
    collector.initialize(database, reference)
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute(
            "SELECT state,attempts,error FROM context_jobs "
            "WHERE kind='stock_daily' AND code='500029'"
        ).fetchone() == ("excluded", 3, "provider rejection")
        assert connection.execute(
            "SELECT COUNT(*) FROM context_jobs WHERE code='500029'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT state FROM context_jobs WHERE kind='stock_daily' AND code='069500'"
        ).fetchone() == ("excluded",)
