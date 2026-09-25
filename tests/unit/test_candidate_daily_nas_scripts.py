from __future__ import annotations

import importlib.util
import argparse
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from urllib.error import URLError
from unittest.mock import patch


def _script(name: str):
    path = Path(__file__).resolve().parents[2] / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CandidateDailyNasScriptsTest(unittest.TestCase):
    def test_status_replace_retries_transient_windows_file_lock(self) -> None:
        collector = _script("backfill_candidate_daily_to_nas.py")
        with tempfile.TemporaryDirectory() as directory:
            status_path = Path(directory) / "status.json"
            original_replace = os.replace
            attempts = 0

            def replace_after_unlock(source, destination):
                nonlocal attempts
                attempts += 1
                if attempts <= 2:
                    error = PermissionError("temporarily locked")
                    error.winerror = 5
                    raise error
                return original_replace(source, destination)

            with patch.object(collector.os, "replace", side_effect=replace_after_unlock), \
                 patch.object(collector.time, "sleep"):
                collector._save_status(status_path, {"phase": "running"})
            self.assertEqual(attempts, 3)
            self.assertEqual(json.loads(status_path.read_text(encoding="utf-8"))["phase"], "running")
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_collector_stops_on_nas_outage_without_marking_next_code_failed(self) -> None:
        collector = _script("backfill_candidate_daily_to_nas.py")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate_db = root / "candidates.sqlite3"
            with closing(sqlite3.connect(candidate_db)) as connection:
                connection.executescript(
                    "CREATE TABLE stocks(code TEXT,market_code TEXT);"
                    "CREATE TABLE candidate_days(code TEXT,dt TEXT);"
                    "CREATE TABLE daily_bars(code TEXT,dt TEXT);"
                    "INSERT INTO stocks VALUES('000001','0'),('000002','0');"
                    "INSERT INTO candidate_days VALUES('000001','2020-01-06'),"
                    "('000002','2020-01-06');"
                )
            env_file = root / ".env"
            env_file.write_text("MONITOR_SERVER_ACCESS_TOKEN=test\n", encoding="utf-8")
            status_path = root / "status.json"
            args = argparse.Namespace(as_of="2020-01-07", env_file=env_file,
                                      url="http://nas", candidates=candidate_db,
                                      codes="", max_codes=0, status=status_path,
                                      request_delay=0, pause_market_hours=False)
            with patch.object(collector, "_request", side_effect=[
                {"status": "ok"}, URLError("connection refused"), URLError("connection refused"),
            ]):
                self.assertEqual(2, collector.run(args))
            status = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual("nas_unavailable", status["phase"])
            self.assertEqual({}, status["codes"])
            self.assertEqual("000001", status["current_code"])

    def test_candidate_daily_filter_and_archive_guard(self) -> None:
        collector = _script("backfill_candidate_daily_to_nas.py")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "candidates.sqlite3"
            with closing(sqlite3.connect(database)) as connection:
                connection.executescript(
            "CREATE TABLE stocks(code TEXT,market_code TEXT);"
            "CREATE TABLE candidate_days(code TEXT,dt TEXT);"
            "INSERT INTO stocks VALUES('000001','0'),('000002','8'),('000003','90');"
            "INSERT INTO candidate_days VALUES('000001','2020-01-06'),"
            "('000002','2020-01-06'),('000003','2020-01-06');"
                )
                self.assertEqual(["000001"], collector.candidate_codes(connection))
            with patch.object(collector, "_request", return_value={"archive_hit": True}):
                with self.assertRaisesRegex(RuntimeError, "archived 250-row"):
                    collector._collect_code("http://nas", "token", "000001", "2020-01-06",
                                            "2019-01-06", {}, root / "status.json", 0, False)

    def test_empty_success_placeholder_is_unavailable_not_a_valid_bar(self) -> None:
        collector = _script("backfill_candidate_daily_to_nas.py")
        with tempfile.TemporaryDirectory() as directory:
            status = Path(directory) / "status.json"
            with patch.object(collector, "_request", return_value={
                "payload": {"return_code": 0, "stk_dt_pole_chart_qry": [
                    {"dt": "", "open_pric": "", "close_pric": ""}]}, "has_next": False,
            }):
                self.assertEqual((0, "", "2026-09-24"), collector._collect_code(
                    "http://nas", "token", "008290", "2026-09-24", "2019-09-24",
                    {}, status, 0, False,
                ))
            with patch.object(collector, "_request", return_value={
                "payload": {"return_code": 0, "stk_dt_pole_chart_qry": [
                    {"dt": "", "open_pric": "100", "close_pric": ""}]}, "has_next": False,
            }):
                with self.assertRaisesRegex(RuntimeError, "without valid daily dates"):
                    collector._collect_code("http://nas", "token", "008290",
                                            "2026-09-24", "2019-09-24", {}, status, 0, False)

    def test_empty_current_page_retries_last_known_trading_date(self) -> None:
        collector = _script("backfill_candidate_daily_to_nas.py")
        with tempfile.TemporaryDirectory() as directory:
            requests = []

            def response(_url, _token, *, body):
                requests.append(body["body"]["base_dt"])
                if len(requests) == 1:
                    return {"payload": {"stk_dt_pole_chart_qry": [
                        {"dt": "", "open_pric": "", "close_pric": ""}]},
                        "has_next": False}
                return {"payload": {"stk_dt_pole_chart_qry": [
                    {"dt": "20190924", "open_pric": "100", "close_pric": "100"}]},
                    "has_next": False}

            with patch.object(collector, "_request", side_effect=response):
                result = collector._collect_code(
                    "http://nas", "token", "008290", "2026-09-24", "2019-09-24",
                    {}, Path(directory) / "status.json", 0, False, "2026-09-18",
                )
            self.assertEqual(["20260924", "20260918"], requests)
            self.assertEqual((1, "2019-09-24", "2026-09-18"), result)


    def test_event_report_requires_complete_refresh_and_candidate_window(self) -> None:
        reporter = _script("report_candidate_event_dates.py")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate_db = root / "candidates.sqlite3"
            event_db = root / "events.sqlite3"
            status = root / "status.json"
            with closing(sqlite3.connect(candidate_db)) as connection:
                connection.executescript(
            "CREATE TABLE stocks(code TEXT,market_code TEXT);"
            "CREATE TABLE candidate_days(code TEXT,dt TEXT);"
            "CREATE TABLE daily_bars(code TEXT,dt TEXT);"
            "INSERT INTO stocks VALUES('000001','0'),('000002','8');"
            "INSERT INTO candidate_days VALUES('000001','2020-01-06'),('000002','2020-01-06');"
            "INSERT INTO daily_bars VALUES('000001','2020-01-02'),"
            "('000001','2020-01-03'),('000001','2020-01-06'),('000001','2020-01-07');"
                )
            with closing(sqlite3.connect(event_db)) as connection:
                connection.executescript(
            "CREATE TABLE historical_stock_adjustments(provider TEXT,code TEXT,"
            "adjustment_date TEXT,adjustment_rate REAL,previous_factor REAL,new_factor REAL);"
            "CREATE TABLE historical_stock_adjustment_classifications(code TEXT,"
            "adjustment_date TEXT,adjustment_rate REAL,event_type TEXT,classification_status TEXT);"
            "INSERT INTO historical_stock_adjustments VALUES"
            "('creon','000001','2020-01-03',0.5,1,0.5),"
            "('creon','000002','2020-01-03',0.5,1,0.5);"
            "INSERT INTO historical_stock_adjustment_classifications VALUES"
            "('000001','2020-01-03',0.5,'split','classified');"
                )
            status.write_text(json.dumps({"phase": "running", "cutoff": "2019-01-01",
                                          "as_of": "2020-01-07"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "not complete"):
                reporter.report(candidate_db, event_db, status)
            status.write_text(json.dumps({"phase": "complete", "cutoff": "2019-01-01",
                                          "as_of": "2020-01-07"}), encoding="utf-8")
            value = reporter.report(candidate_db, event_db, status)
            self.assertEqual(1, value["event_count"])
            self.assertEqual("000001", value["events"][0]["code"])
            self.assertEqual(1, value["events"][0]["affected_candidate_cases"])


if __name__ == "__main__":
    unittest.main()
