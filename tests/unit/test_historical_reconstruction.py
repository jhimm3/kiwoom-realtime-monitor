from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from kiwoom_monitor.infrastructure.historical_backfill import initialize_probe_database
from kiwoom_monitor.infrastructure.historical_reconstruction import (
    adapt_historical_reconstruction_for_research,
    export_historical_reconstruction,
    load_historical_reconstruction,
    write_historical_research_input,
)
from kiwoom_monitor.infrastructure.research_data_source import load_frozen_research_export


class HistoricalReconstructionTests(unittest.TestCase):
    def test_exports_posthoc_candidates_with_one_real_resolution_and_news_statuses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / "candidate.sqlite3"
            intelligence = root / "intelligence.sqlite3"
            with closing(sqlite3.connect(candidate)) as connection, connection:
                connection.executescript("""
                    CREATE TABLE stocks(code TEXT PRIMARY KEY, name TEXT);
                    CREATE TABLE candidate_days(
                        dt TEXT, code TEXT, score REAL, reasons TEXT,
                        rank_value INTEGER, rank_gain INTEGER, rank_high INTEGER,
                        rank_volume_ratio INTEGER, gain_pct REAL, high_pct REAL,
                        volume_ratio REAL, trading_value INTEGER,
                        PRIMARY KEY(dt, code)
                    );
                    INSERT INTO stocks VALUES('005930','삼성전자'),('000660','SK하이닉스');
                    INSERT INTO candidate_days VALUES
                      ('2024-01-02','005930',9.5,'value',1,2,3,4,5.0,6.0,7.0,800),
                      ('2024-01-02','000660',8.5,'gain',2,1,4,3,4.0,5.0,6.0,700);
                """)
            initialize_probe_database(intelligence)
            with closing(sqlite3.connect(intelligence)) as connection, connection:
                connection.execute(
                    "INSERT INTO market_bars VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("daishin_creon", "005930", "K", "regular", 300, "raw",
                     "2024-01-02T09:05:00+09:00", "interval_end", 20240102, 905,
                     100, 110, 90, 105, 1000, 100000, "2026-09-22T01:00:00+00:00",
                     "2026-09-22T01:00:00+00:00"),
                )
                connection.execute(
                    "INSERT INTO market_bars VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("daishin_creon", "005930", "K", "regular", 60, "raw",
                     "2024-01-02T09:01:00+09:00", "interval_end", 20240102, 901,
                     100, 101, 99, 100, 100, 10000, "2026-09-22T02:00:00+00:00",
                     "2026-09-22T02:00:00+00:00"),
                )
                articles = (
                    "naver_historical_search", "001", "1", "2024-01-02T08:00:00+09:00",
                    "minute", "매체", "검증 기사", "요약", "https://n/1", "https://o/1",
                    "https://n/1", "", "json_ld:datePublished", "raw", "https://o/1",
                    "published_at_found", "2026-09-22T01:00:00+00:00", 1, "",
                    "2026-09-22T00:00:00+00:00", "2026-09-22T00:00:00+00:00",
                )
                connection.execute(
                    "INSERT INTO news_articles VALUES(" + ",".join("?" for _ in articles) + ")",
                    articles,
                )
                connection.execute(
                    "INSERT INTO news_search_observations VALUES(?,?,?,?,?,?,?,?,?)",
                    ("naver_historical_search", "001", "1", "005930", "2024-01-02",
                     "삼성전자", 1, 1, "2026-09-22T00:00:00+00:00"),
                )
            output = root / "export"
            manifest = export_historical_reconstruction(
                candidate, intelligence, output, dates=("2024-01-02",),
                created_at=datetime(2026, 9, 22, 3, tzinfo=UTC),
            )
            dataset = load_historical_reconstruction(output)

            self.assertTrue(manifest["population"]["not_contemporaneous_top20"])
            self.assertFalse(manifest["consumer_contract"]["strict_top20_replay_supported"])
            self.assertEqual({"60": 1, "300": 0, "unavailable": 1}, manifest["resolution_by_candidate_day"])
            self.assertEqual(2, len(dataset.records_of_kind("historical_candidate")))
            bars = dataset.records_of_kind("historical_market_bar")
            self.assertEqual(1, len(bars))
            self.assertEqual(60, bars[0]["payload"]["interval_seconds"])
            self.assertEqual("2026-09-22T02:00:00+00:00", bars[0]["available_at"])
            news = dataset.records_of_kind("historical_news_evidence")
            self.assertTrue(news[0]["payload"]["publication_time_verified"])
            self.assertEqual(1, manifest["news_quality"]["publication_time_verified_relations"])
            self.assertEqual(
                {"published_at_found": 1},
                manifest["news_quality"]["article_fetch_status_counts"],
            )

    def test_loader_rejects_modified_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / "candidate.sqlite3"
            intelligence = root / "intelligence.sqlite3"
            with closing(sqlite3.connect(candidate)) as connection, connection:
                connection.executescript("""
                    CREATE TABLE stocks(code TEXT PRIMARY KEY, name TEXT);
                    CREATE TABLE candidate_days(
                        dt TEXT, code TEXT, score REAL, reasons TEXT,
                        rank_value INTEGER, rank_gain INTEGER, rank_high INTEGER,
                        rank_volume_ratio INTEGER, gain_pct REAL, high_pct REAL,
                        volume_ratio REAL, trading_value INTEGER
                    );
                """)
            initialize_probe_database(intelligence)
            output = root / "export"
            export_historical_reconstruction(candidate, intelligence, output, dates=("2024-01-02",))
            (output / "records.jsonl").write_text(json.dumps({"ordinal": 1}) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "hash"):
                load_historical_reconstruction(output)

    def test_outcome_window_adapts_only_complete_one_minute_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / "candidate.sqlite3"
            intelligence = root / "intelligence.sqlite3"
            with closing(sqlite3.connect(candidate)) as connection, connection:
                connection.executescript("""
                    CREATE TABLE stocks(code TEXT PRIMARY KEY, name TEXT);
                    CREATE TABLE candidate_days(
                        dt TEXT, code TEXT, score REAL, reasons TEXT,
                        rank_value INTEGER, rank_gain INTEGER, rank_high INTEGER,
                        rank_volume_ratio INTEGER, gain_pct REAL, high_pct REAL,
                        volume_ratio REAL, trading_value INTEGER,
                        PRIMARY KEY(dt, code)
                    );
                    INSERT INTO stocks VALUES('005930','삼성전자');
                    INSERT INTO candidate_days VALUES
                      ('2024-01-02','005930',9.5,'value',1,2,3,4,5,6,7,800);
                """)
            initialize_probe_database(intelligence)
            with closing(sqlite3.connect(intelligence)) as connection, connection:
                connection.execute(
                    "CREATE TABLE market_backfill_jobs(code TEXT PRIMARY KEY,state TEXT)"
                )
                connection.execute("INSERT INTO market_backfill_jobs VALUES('005930','complete')")
                for minute, close in ((1, 100), (2, 101), (3, 103)):
                    timestamp = f"2024-01-03T09:{minute:02d}:00+09:00"
                    connection.execute(
                        "INSERT INTO market_bars VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        ("daishin_creon", "005930", "K", "regular", 60, "raw",
                         timestamp, "interval_end", 20240103, 900 + minute,
                         close, close + 1, close - 1, close, 100, 10_000_000,
                         "2026-09-22T02:00:00+00:00", "2026-09-22T02:00:00+00:00"),
                    )
                connection.execute(
                    "INSERT INTO market_bars VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("daishin_creon", "005930", "K", "regular", 300, "raw",
                     "2024-01-03T09:05:00+09:00", "interval_end", 20240103, 905,
                     100, 104, 99, 103, 300, 30_000_000,
                     "2026-09-22T02:00:00+00:00", "2026-09-22T02:00:00+00:00"),
                )
            output = root / "export"
            export_historical_reconstruction(
                candidate, intelligence, output, dates=("2024-01-02",),
                outcome_end_date="2024-01-03",
            )
            source = load_historical_reconstruction(output)
            derived = adapt_historical_reconstruction_for_research(
                source, selected_date="2024-01-02",
            )

            self.assertEqual("2024-01-03", source.manifest["temporal_split"]["outcome_end_date"])
            self.assertEqual(4, len(derived.observations))
            self.assertEqual("historical_candidate_population", derived.observations[0]["kind"])
            self.assertEqual(3, sum(row["kind"] == "minute_bar" for row in derived.observations))
            bar = derived.observations[1]
            self.assertEqual("2024-01-03T09:01:00+09:00", bar["available_at"])
            self.assertEqual("2026-09-22T02:00:00+00:00", bar["payload"]["source_available_at"])
            self.assertEqual(10, bar["payload"]["trade_value_million_won"])
            research_output = root / "research-input"
            write_historical_research_input(derived, research_output)
            verified = load_frozen_research_export(research_output)
            self.assertEqual(derived.manifest["dataset_id"], verified.manifest["dataset_id"])
            self.assertEqual(derived.observations, verified.observations)

    def test_multiple_cases_use_nonoverlapping_outcome_windows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / "candidate.sqlite3"
            intelligence = root / "intelligence.sqlite3"
            with closing(sqlite3.connect(candidate)) as connection, connection:
                connection.executescript("""
                    CREATE TABLE stocks(code TEXT PRIMARY KEY, name TEXT);
                    CREATE TABLE candidate_days(
                        dt TEXT, code TEXT, score REAL, reasons TEXT,
                        rank_value INTEGER, rank_gain INTEGER, rank_high INTEGER,
                        rank_volume_ratio INTEGER, gain_pct REAL, high_pct REAL,
                        volume_ratio REAL, trading_value INTEGER,
                        PRIMARY KEY(dt, code)
                    );
                    INSERT INTO stocks VALUES('005930','삼성전자');
                    INSERT INTO candidate_days VALUES
                      ('2024-01-02','005930',9.5,'value',1,2,3,4,5,6,7,800),
                      ('2024-01-03','005930',9.0,'gain',2,1,4,3,4,5,6,700);
                """)
            initialize_probe_database(intelligence)
            with closing(sqlite3.connect(intelligence)) as connection, connection:
                connection.execute(
                    "CREATE TABLE market_backfill_jobs(code TEXT PRIMARY KEY,state TEXT)"
                )
                connection.execute("INSERT INTO market_backfill_jobs VALUES('005930','complete')")
                for day, close in ((3, 101), (4, 102)):
                    timestamp = f"2024-01-{day:02d}T09:01:00+09:00"
                    connection.execute(
                        "INSERT INTO market_bars VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        ("daishin_creon", "005930", "K", "regular", 60, "raw",
                         timestamp, "interval_end", 20240100 + day, 901,
                         close, close + 1, close - 1, close, 100, 10_000_000,
                         "2026-09-22T02:00:00+00:00", "2026-09-22T02:00:00+00:00"),
                    )
            output = root / "export"
            manifest = export_historical_reconstruction(
                candidate, intelligence, output,
                dates=("2024-01-02", "2024-01-03"), outcome_end_date="2024-01-04",
            )
            source = load_historical_reconstruction(output)
            derived = adapt_historical_reconstruction_for_research(
                source, selected_dates=("2024-01-02", "2024-01-03"),
            )

            windows = manifest["temporal_split"]["case_windows"]
            self.assertEqual("2024-01-03", windows[0]["outcome_end_date"])
            self.assertEqual("2024-01-04", windows[1]["outcome_end_date"])
            self.assertEqual(2, len(derived.manifest["included_cases"]))
            self.assertEqual([], derived.manifest["excluded_cases"])
            self.assertEqual(
                ["2024-01-02", "2024-01-03"], derived.manifest["selected_dates"],
            )
            self.assertEqual(
                2,
                sum(row["kind"] == "historical_candidate_population"
                    for row in derived.observations),
            )
            bars = [row for row in derived.observations if row["kind"] == "minute_bar"]
            self.assertEqual(2, len(bars))
            self.assertEqual(
                {"2024-01-02", "2024-01-03"},
                {row["payload"]["historical_case_date"] for row in bars},
            )


if __name__ == "__main__":
    unittest.main()
