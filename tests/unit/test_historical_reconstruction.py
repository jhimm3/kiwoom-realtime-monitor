from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path, PureWindowsPath
from unittest.mock import patch, Mock
from urllib.parse import urlsplit, unquote

from kiwoom_monitor.infrastructure.historical_backfill import initialize_probe_database
from kiwoom_monitor.infrastructure.historical_reconstruction import (
    HistoricalReconstructionDataset,
    adapt_historical_reconstruction_for_research,
    export_historical_reconstruction,
    load_historical_reconstruction,
    write_historical_research_input,
    _open_readonly,
)
from kiwoom_monitor.infrastructure.research_data_source import load_frozen_research_export
from kiwoom_monitor.application.research_replay import replay_krx_minute_bars


class HistoricalReconstructionTests(unittest.TestCase):
    def test_unc_readonly_uri_has_no_remote_authority_and_preserves_immutable_choice(self):
        path = PureWindowsPath(r"\\192.168.0.5\share\자료 #100%.sqlite3")
        for immutable in (False, True):
            connection = Mock()
            with patch("kiwoom_monitor.infrastructure.historical_reconstruction.sqlite3.connect", return_value=connection) as connect:
                self.assertIs(connection, _open_readonly(path, immutable=immutable))
            uri = connect.call_args.args[0]
            self.assertEqual("", urlsplit(uri).netloc)
            self.assertEqual(path.as_posix(), unquote(urlsplit(uri).path))
            self.assertIn("mode=ro", uri)
            self.assertEqual(immutable, "immutable=1" in uri)
            self.assertEqual({"uri": True, "timeout": 30}, connect.call_args.kwargs)
            connection.execute.assert_called_once_with("PRAGMA query_only=ON")

    def test_special_filename_reader_preserves_data_and_refuses_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "자료 #100%.sqlite3"
            with closing(sqlite3.connect(path)) as writer:
                writer.execute("CREATE TABLE evidence(value TEXT)")
                writer.execute("INSERT INTO evidence VALUES ('original')")
                writer.commit()
            before = path.read_bytes()
            with closing(_open_readonly(path, immutable=False)) as reader:
                self.assertEqual(("original",), reader.execute("SELECT value FROM evidence").fetchone())
                with self.assertRaises(sqlite3.OperationalError):
                    reader.execute("INSERT INTO evidence VALUES ('changed')")
            self.assertEqual(before, path.read_bytes())
            self.assertEqual([path], list(Path(directory).iterdir()))

    def test_published_wal_mode_snapshot_does_not_create_sidecars_with_immutable_reader(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "published.sqlite3"
            with closing(sqlite3.connect(path)) as writer:
                writer.execute("PRAGMA journal_mode=WAL")
                writer.execute("CREATE TABLE evidence(value TEXT)")
                writer.execute("INSERT INTO evidence VALUES ('checkpointed')")
                writer.commit()
            self.assertEqual([path], list(Path(directory).iterdir()))
            before = path.read_bytes()
            with closing(_open_readonly(path, immutable=True)) as reader:
                self.assertEqual(("checkpointed",), reader.execute("SELECT value FROM evidence").fetchone())
                with self.assertRaises(sqlite3.OperationalError):
                    reader.execute("DELETE FROM evidence")
            self.assertEqual(before, path.read_bytes())
            self.assertEqual([path], list(Path(directory).iterdir()))

    def test_immutable_export_rejects_pending_sidecar_before_opening_either_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate, intelligence = root / "candidate.sqlite3", root / "intelligence.sqlite3"
            candidate.touch()
            intelligence.touch()
            sidecar = Path(str(intelligence) + "-wal")
            sidecar.write_bytes(b"pending data must be preserved")
            with patch("kiwoom_monitor.infrastructure.historical_reconstruction._open_readonly", side_effect=AssertionError("no connection")):
                with self.assertRaisesRegex(ValueError, "must not have WAL or SHM"):
                    export_historical_reconstruction(candidate, intelligence, root / "output",
                        dates=("2024-09-02",), intelligence_immutable=True)
            self.assertEqual(b"pending data must be preserved", sidecar.read_bytes())
            self.assertFalse((root / "output").exists())

    def test_immutable_export_flag_requires_explicit_boolean(self):
        with self.assertRaisesRegex(ValueError, "must be a boolean"):
            export_historical_reconstruction(Path("missing"), Path("missing"), Path("output"),
                dates=("2024-09-02",), intelligence_immutable="true")
        with self.assertRaisesRegex(ValueError, "must be a boolean"):
            export_historical_reconstruction(Path("missing"), Path("missing"), Path("output"),
                dates=("2024-09-02",), strategy_outcomes_only="true")
        with self.assertRaisesRegex(ValueError, "later outcome_end_date"):
            export_historical_reconstruction(Path("missing"), Path("missing"), Path("output"),
                dates=("2024-09-02",), strategy_outcomes_only=True)

    def test_delayed_session_1530_is_continuous_and_1630_is_auction(self) -> None:
        case_date = "2025-11-12"
        records = [{"kind": "historical_candidate", "revision_id": "candidate",
                    "available_at": "2026-09-24T00:00:00+00:00",
                    "payload": {"date": case_date, "code": "347850", "market_code": "0"}}]
        for clock in ("15:30", "16:30"):
            records.append({
                "kind": "historical_market_bar", "revision_id": f"bar-{clock}",
                "available_at": "2026-09-24T00:00:00+00:00",
                "payload": {"case_date": case_date, "phase": "outcome", "code": "347850",
                            "interval_seconds": 60, "source_job_state": "complete",
                            "bar_time": f"2025-11-13T{clock}:00+09:00", "open": 100,
                            "high": 100, "low": 100, "close": 100, "volume": 10,
                            "trading_value": 1000},
            })
        source = HistoricalReconstructionDataset(
            {"dataset_id": "delayed-source", "revision_ids_hash": "source-hash",
             "selected_dates": [case_date]}, tuple(records),
        )
        derived = adapt_historical_reconstruction_for_research(source, selected_date=case_date)
        bars = {row["payload"]["bar_end"]: row["payload"] for row in derived.observations
                if row["kind"] == "minute_bar"}
        self.assertEqual("2025-11-13T15:29:00+09:00", bars["2025-11-13T15:30:00+09:00"]["bar_start"])
        self.assertNotIn("phase", bars["2025-11-13T15:30:00+09:00"])
        self.assertEqual("2025-11-13T16:20:00+09:00", bars["2025-11-13T16:30:00+09:00"]["bar_start"])
        self.assertEqual("AUCTION_ORDER_ENTRY", bars["2025-11-13T16:30:00+09:00"]["phase"])
        replay = replay_krx_minute_bars(derived.observations, strict=True)
        self.assertEqual(2, len(replay))
        self.assertEqual("AUCTION_ORDER_ENTRY", replay[-1].market_phase)

    def test_1530_close_print_is_auction_not_fabricated_1529_minute(self) -> None:
        case_date = "2024-01-02"
        source = HistoricalReconstructionDataset({
            "dataset_id": "close-print-source", "revision_ids_hash": "source-hash",
            "selected_dates": [case_date],
        }, (
            {"kind": "historical_candidate", "revision_id": "candidate", "available_at": "2026-09-24T00:00:00+00:00",
             "payload": {"date": case_date, "code": "005930", "market_code": "0"}},
            {"kind": "historical_market_bar", "revision_id": "close", "available_at": "2026-09-24T00:00:00+00:00",
             "payload": {"case_date": case_date, "phase": "outcome", "code": "005930",
                         "interval_seconds": 60, "source_job_state": "complete",
                         "bar_time": "2024-01-03T15:30:00+09:00", "open": 100, "high": 100,
                         "low": 100, "close": 100, "volume": 10, "trading_value": 1000}},
        ))
        derived = adapt_historical_reconstruction_for_research(source, selected_date=case_date)
        bar = next(row for row in derived.observations if row["kind"] == "minute_bar")
        population = next(row for row in derived.observations
                          if row["kind"] == "historical_candidate_population")
        self.assertEqual("2024-01-03T15:20:00+09:00", population["available_at"])
        self.assertEqual("2024-01-03T15:20:00+09:00", bar["payload"]["bar_start"])
        self.assertEqual(600, bar["payload"]["replay_interval_seconds"])
        self.assertEqual("AUCTION_ORDER_ENTRY", bar["payload"]["phase"])
        replay = replay_krx_minute_bars(derived.observations, strict=True)
        self.assertEqual(1, len(replay))
        self.assertEqual("AUCTION_ORDER_ENTRY", replay[0].market_phase)

    def test_excludes_sparse_minute_candidate_from_derived_population_only(self) -> None:
        case_date = "2024-12-24"
        records = []
        for code, minutes in (("005930", (1, 2)), ("000545", (1, 31, 61))):
            records.append({
                "kind": "historical_candidate", "revision_id": f"candidate-{code}",
                "available_at": "2026-09-24T00:00:00+00:00",
                "payload": {"date": case_date, "code": code, "market_code": "0"},
            })
            for minute in minutes:
                bar_time = (datetime.fromisoformat("2024-12-26T09:00:00+09:00")
                            + timedelta(minutes=minute)).isoformat()
                records.append({
                    "kind": "historical_market_bar",
                    "revision_id": f"bar-{code}-{minute}",
                    "available_at": "2026-09-24T00:00:00+00:00",
                    "payload": {"case_date": case_date, "phase": "outcome", "code": code,
                                "interval_seconds": 60, "source_job_state": "complete",
                                "bar_time": bar_time, "open": 100, "high": 100,
                                "low": 100, "close": 100, "volume": 1,
                                "trading_value": 100},
                })
        source = HistoricalReconstructionDataset({
            "dataset_id": "test-source", "revision_ids_hash": "test-hash",
            "selected_dates": [case_date],
        }, tuple(records))
        derived = adapt_historical_reconstruction_for_research(
            source, selected_date=case_date, individual_stocks_only=True,
            exclude_noncontinuous_minute_candidates=True,
        )
        population = next(row for row in derived.observations
                          if row["kind"] == "historical_candidate_population")
        self.assertEqual(["005930"], population["payload"]["codes"])
        self.assertEqual(["005930", "005930"], [row["payload"]["code"]
                         for row in derived.observations if row["kind"] == "minute_bar"])
        self.assertEqual([{
            "selection_date": case_date, "code": "000545",
            "outcome_minute_bar_count": 3,
            "reason": "no_continuous_one_minute_outcome_pair",
        }], derived.manifest["excluded_noncontinuous_minute_candidates"])
        self.assertEqual(2, len(source.records_of_kind("historical_candidate")))

    def test_stock_only_projection_preserves_excluded_candidates_in_source(self) -> None:
        case_date = "2024-01-02"
        records = []
        for code, market_code in (("005930", "0"), ("069500", "8")):
            records.append({
                "kind": "historical_candidate", "revision_id": f"candidate-{code}",
                "available_at": "2026-09-22T00:00:00+00:00",
                "payload": {"date": case_date, "code": code, "market_code": market_code},
            })
            records.append({
                "kind": "historical_market_bar", "revision_id": f"bar-{code}",
                "available_at": "2026-09-22T00:00:00+00:00",
                "payload": {"case_date": case_date, "phase": "outcome", "code": code,
                            "interval_seconds": 60, "source_job_state": "complete",
                            "bar_time": "2024-01-03T09:01:00+09:00", "open": 100,
                            "high": 100, "low": 100, "close": 100, "volume": 1,
                            "trading_value": 100},
            })
        source = HistoricalReconstructionDataset({
            "dataset_id": "test-source", "revision_ids_hash": "test-hash",
            "selected_dates": [case_date],
        }, tuple(records))
        original = adapt_historical_reconstruction_for_research(source, selected_date=case_date)
        stocks = adapt_historical_reconstruction_for_research(
            source, selected_date=case_date, individual_stocks_only=True,
        )
        self.assertEqual(original.manifest["included_cases"][0]["candidate_count"], 2)
        self.assertEqual(stocks.manifest["included_cases"][0]["candidate_count"], 1)
        self.assertEqual(stocks.manifest["excluded_non_stock_candidates"], [
            {"selection_date": case_date, "code": "069500", "market_code": "8"},
        ])
        self.assertEqual([row["payload"]["code"] for row in stocks.observations
                          if row["kind"] == "minute_bar"], ["005930"])
        self.assertEqual(len(source.records), 4)

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
                # A day with only an unsupported interval used to enter the date
                # scan, but never contributed an exported bar.
                connection.execute(
                    "INSERT INTO market_bars VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("daishin_creon", "005930", "K", "regular", 900, "raw",
                     "2024-01-03T09:15:00+09:00", "interval_end", 20240103, 915,
                     200, 210, 190, 205, 1000, 200000,
                     "2026-09-22T02:00:00+00:00", "2026-09-22T02:00:00+00:00"),
                )
            output = root / "export"
            manifest = export_historical_reconstruction(
                candidate, intelligence, output, dates=("2024-01-02",),
                created_at=datetime(2026, 9, 22, 3, tzinfo=UTC),
                outcome_end_date="2024-01-03",
            )
            original_connect = sqlite3.connect
            class LegacyDateConnection(sqlite3.Connection):
                def execute(self, sql, parameters=()):
                    if sql.startswith("SELECT DISTINCT substr(bar_time,1,10)"):
                        sql = sql.replace("AND interval_seconds IN (60,300) ", "")
                    return super().execute(sql, parameters)
            def legacy_connect(*args, **kwargs):
                return original_connect(*args, **kwargs, factory=LegacyDateConnection)
            with patch("kiwoom_monitor.infrastructure.historical_reconstruction.sqlite3.connect", side_effect=legacy_connect):
                legacy = export_historical_reconstruction(
                    candidate, intelligence, root / "legacy-export", dates=("2024-01-02",),
                    created_at=datetime(2026, 9, 22, 3, tzinfo=UTC), outcome_end_date="2024-01-03",
                )
            self.assertEqual(legacy, manifest)
            self.assertEqual((root / "legacy-export" / "records.jsonl").read_bytes(), (output / "records.jsonl").read_bytes())
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
            # The bounded strategy export must preserve exactly the real outcome rows.
            with closing(sqlite3.connect(intelligence)) as connection, connection:
                for minute in (901, 902):
                    connection.execute("INSERT INTO market_bars VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                        "daishin_creon", "005930", "K", "regular", 60, "raw",
                        f"2024-01-03T09:0{minute - 900}:00+09:00", "interval_end", 20240103, minute,
                        100, 101, 99, 100, 100, 10000, "2026-09-22T02:00:00+00:00", "2026-09-22T02:00:00+00:00"))
            before = (candidate.read_bytes(), intelligence.read_bytes())
            full = root / "full-outcomes"
            limited = root / "only-outcomes"
            full_manifest = export_historical_reconstruction(candidate, intelligence, full,
                dates=("2024-01-02",), outcome_end_date="2024-01-03", created_at=datetime(2026, 9, 22, 3, tzinfo=UTC))
            class NoNewsConnection(sqlite3.Connection):
                def execute(self, sql, parameters=()):
                    if "news_search_observations" in sql or "news_articles" in sql:
                        raise AssertionError("strategy export cannot query news")
                    return super().execute(sql, parameters)
            def no_news_connect(*args, **kwargs):
                return original_connect(*args, **kwargs, factory=NoNewsConnection)
            with patch("kiwoom_monitor.infrastructure.historical_reconstruction.sqlite3.connect", side_effect=no_news_connect):
                limited_manifest = export_historical_reconstruction(candidate, intelligence, limited,
                    dates=("2024-01-02",), outcome_end_date="2024-01-03",
                    created_at=datetime(2026, 9, 22, 3, tzinfo=UTC), strategy_outcomes_only=True)
            full_data, limited_data = load_historical_reconstruction(full), load_historical_reconstruction(limited)
            expected = [row for row in full_data.records if row["kind"] == "historical_candidate" or
                        row["kind"] == "historical_market_bar" and row["payload"]["phase"] == "outcome"]
            clean = lambda rows: [{key: value for key, value in row.items() if key != "ordinal"} for row in rows]
            self.assertEqual(clean(expected), clean(limited_data.records))
            self.assertEqual(2, len(limited_data.records_of_kind("historical_market_bar")))
            self.assertEqual((), limited_data.records_of_kind("historical_news_evidence"))
            self.assertNotIn("export_scope", full_manifest)
            self.assertEqual({"market_bars": "strictly_after_selection_date", "news_included": False}, limited_manifest["export_scope"])
            self.assertEqual(before, (candidate.read_bytes(), intelligence.read_bytes()))

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
