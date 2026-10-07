from __future__ import annotations

import importlib.util
import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/run_daishin_candidate_collection.py"
SPEC = importlib.util.spec_from_file_location("daishin_candidate_collection", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)


class DaishinCandidateCollectionTest(unittest.TestCase):
    def test_non_stock_jobs_are_excluded_using_reference_market_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "reference.sqlite3"
            database = Path(directory) / "jobs.sqlite3"
            with closing(sqlite3.connect(reference)) as connection, connection:
                connection.execute("CREATE TABLE candidate_days(code TEXT)")
                connection.execute("CREATE TABLE stocks(code TEXT,market_code TEXT)")
                connection.executemany("INSERT INTO candidate_days VALUES(?)", [
                    ("005930",), ("500029",), ("700013",), ("069500",), ("145270",),
                ])
                connection.executemany("INSERT INTO stocks VALUES(?,?)", [
                    ("005930", "0"), ("500029", "60"), ("700013", "90"),
                    ("069500", "8"), ("145270", "6"),
                ])
            collector._initialize_jobs(reference, database)
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute(
                    "INSERT INTO market_backfill_jobs(code,state,attempts,updated_at) "
                    "VALUES('500029','failed',3,'old')"
                )
                connection.execute(
                    "INSERT INTO market_backfill_jobs(code,state,attempts,updated_at) "
                    "VALUES('069500','complete',1,'old')"
                )
            collector._initialize_jobs(reference, database)
            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(list(connection.execute(
                    "SELECT code,state,attempts FROM market_backfill_jobs ORDER BY code"
                )), [("005930", "pending", 0), ("069500", "excluded", 1),
                     ("500029", "excluded", 3)])

    def test_ranges_uses_provider_prefix_instead_of_full_market_scan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "bars.sqlite3"
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute(
                    "CREATE TABLE market_bars(provider TEXT,code TEXT,venue TEXT,"
                    "interval_seconds INTEGER,bar_time TEXT,"
                    "PRIMARY KEY(provider,code,venue,interval_seconds,bar_time))"
                )
                connection.executemany(
                    "INSERT INTO market_bars VALUES(?,?,?,?,?)",
                    [("daishin_creon", "005930", "K", 60, "2026-09-22"),
                     ("other", "005930", "K", 60, "2026-09-21")],
                )
            self.assertEqual((1, "2026-09-22", "2026-09-22", 0, None, None),
                             collector._ranges(database, "005930"))
            with closing(sqlite3.connect(database)) as connection:
                plan = connection.execute(
                    "EXPLAIN QUERY PLAN SELECT COUNT(*),MIN(bar_time),MAX(bar_time) "
                    "FROM market_bars WHERE provider='daishin_creon' "
                    "AND code='005930' AND interval_seconds=60"
                ).fetchall()
            self.assertTrue(any("SEARCH market_bars" in row[-1] for row in plan))

    def test_job_seed_includes_six_character_alphanumeric_candidate_codes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "reference.sqlite3"
            database = Path(directory) / "jobs.sqlite3"
            with closing(sqlite3.connect(reference)) as connection, connection:
                connection.execute("CREATE TABLE candidate_days(code TEXT)")
                connection.executemany(
                    "INSERT INTO candidate_days VALUES(?)",
                    [("005930",), ("00499K",), ("0001a0",), ("12345",), ("bad-code",)],
                )

            collector._initialize_jobs(reference, database)

            with closing(sqlite3.connect(database)) as connection:
                codes = tuple(row[0] for row in connection.execute(
                    "SELECT code FROM market_backfill_jobs ORDER BY code"
                ))
            self.assertEqual(("0001A0", "00499K", "005930"), codes)

    def test_reads_connection_failure_from_bridge_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "1m.ndjson"
            output.write_text(json.dumps({
                "record_type": "error",
                "error": collector.CREON_CONNECTION_ERROR,
            }), encoding="utf-8")
            completed = type("Completed", (), {"returncode": 1, "stderr": ""})()
            with patch.object(collector.subprocess, "run", return_value=completed):
                with self.assertRaises(collector.DaishinEnvironmentUnavailable):
                    collector._run_backfill("005930", 1, output)

    def test_five_minute_download_ends_before_oldest_one_minute_day(self) -> None:
        self.assertEqual(
            "20240828",
            collector._five_minute_to_date("2024-08-29T09:01:00+09:00"),
        )

        completed = type("Completed", (), {"returncode": 0, "stderr": ""})()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "5m.ndjson"
            with patch.object(
                collector.subprocess, "run", return_value=completed,
            ) as run:
                collector._run_backfill(
                    "005930", 5, output, to_date="20240828",
                )

        command = run.call_args.args[0]
        self.assertEqual("20240828", command[command.index("-ToDate") + 1])

    def test_partial_one_minute_import_is_not_inferred_complete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "reference.sqlite3"
            database = Path(directory) / "jobs.sqlite3"
            with closing(sqlite3.connect(reference)) as connection, connection:
                connection.execute("CREATE TABLE candidate_days(code TEXT)")
                connection.execute("INSERT INTO candidate_days VALUES('005305')")

            collector._initialize_jobs(reference, database)
            collector.store_daishin_probe_payload(database, {
                "provider": "daishin_creon",
                "code": "005305",
                "venue": "K",
                "session_scope": "regular",
                "interval_seconds": 60,
                "adjustment_mode": "raw",
                "observed_at": "2026-09-22T12:28:25+09:00",
                "bars": [{
                    "bar_time": "2024-08-29T09:01:00+09:00",
                    "raw_date": 20240829,
                    "raw_time": 901,
                    "open": 100,
                    "high": 101,
                    "low": 99,
                    "close": 100,
                    "volume": 10,
                    "trading_value": 1000,
                }],
            })

            collector._initialize_jobs(reference, database)

            statements: list[str] = []
            original_connect = collector._connect_database

            def tracked_connect(path: Path) -> sqlite3.Connection:
                connection = original_connect(path)
                connection.set_trace_callback(statements.append)
                return connection

            with patch.object(collector, "_connect_database", side_effect=tracked_connect):
                collector._initialize_jobs(reference, database)

            with closing(sqlite3.connect(database)) as connection:
                state = connection.execute(
                    "SELECT state FROM market_backfill_jobs WHERE code='005305'"
                ).fetchone()[0]
            self.assertEqual("pending", state)
            self.assertFalse(any("GROUP BY code,interval_seconds" in sql for sql in statements))

    def test_environment_failure_returns_claim_without_charging_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "jobs.sqlite3"
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute(
                    "CREATE TABLE market_backfill_jobs("
                    "code TEXT PRIMARY KEY,state TEXT,attempts INTEGER,raw_directory TEXT,"
                    "last_error TEXT,updated_at TEXT)"
                )
                connection.execute(
                    "INSERT INTO market_backfill_jobs VALUES"
                    "('005930','running',2,'','','2026-09-22T00:00:00+00:00')"
                )
            raw = Path(directory) / "raw"
            collector._defer_for_environment(
                database, "005930", raw, collector.CREON_CONNECTION_ERROR,
            )
            with closing(sqlite3.connect(database)) as connection:
                row = connection.execute(
                    "SELECT state,attempts,raw_directory,last_error "
                    "FROM market_backfill_jobs WHERE code='005930'"
                ).fetchone()
            self.assertEqual("pending", row[0])
            self.assertEqual(1, row[1])
            self.assertEqual(str(raw), row[2])
            self.assertIn("not connected", row[3])

    def test_job_update_waits_for_a_transient_database_writer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "jobs.sqlite3"
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute(
                    "CREATE TABLE market_backfill_jobs("
                    "code TEXT PRIMARY KEY,state TEXT,attempts INTEGER,raw_directory TEXT,"
                    "last_error TEXT,updated_at TEXT)"
                )
                connection.execute(
                    "INSERT INTO market_backfill_jobs VALUES"
                    "('005930','running',2,'','','2026-09-22T00:00:00+00:00')"
                )

            locked = threading.Event()
            release = threading.Event()

            def hold_writer_lock() -> None:
                with closing(sqlite3.connect(database)) as connection:
                    connection.execute("BEGIN EXCLUSIVE")
                    connection.execute(
                        "UPDATE market_backfill_jobs SET last_error='other_writer' "
                        "WHERE code='005930'"
                    )
                    locked.set()
                    release.wait(timeout=2)
                    connection.commit()

            writer = threading.Thread(target=hold_writer_lock)
            writer.start()
            self.assertTrue(locked.wait(timeout=1))
            timer = threading.Timer(0.1, release.set)
            timer.start()
            try:
                collector._defer_for_environment(
                    database, "005930", Path(directory) / "raw", "temporary outage",
                )
            finally:
                release.set()
                timer.cancel()
                writer.join(timeout=2)

            with closing(sqlite3.connect(database)) as connection:
                row = connection.execute(
                    "SELECT state,attempts,last_error FROM market_backfill_jobs "
                    "WHERE code='005930'"
                ).fetchone()
            self.assertEqual(("pending", 1, "temporary outage"), row)


if __name__ == "__main__":
    unittest.main()
