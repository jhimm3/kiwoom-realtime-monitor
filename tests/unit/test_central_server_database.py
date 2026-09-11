from __future__ import annotations

import tempfile
import time
import unittest
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from kiwoom_monitor.central_server.central_schema import (
    CENTRAL_SCHEMA_BASELINE_NAME,
    CENTRAL_MARKET_METADATA_MIGRATION_NAME,
    CENTRAL_MARKET_STATE_TIME_REPAIR_MIGRATION_NAME,
    CENTRAL_SCHEMA_VERSION,
)
from kiwoom_monitor.central_server.database import SQLiteQueryStore, StoredQuery, create_query_store
from kiwoom_monitor.central_server.schema_migrations import CentralSchemaMigrationError
from kiwoom_monitor.domain.market_data_contract import (
    CandidateUniverse,
    DataCompleteness,
    DataUnit,
    DataValueKind,
    MarketDataMetadata,
    MarketDataObservation,
    MarketDatasetKind,
    ObservationOrigin,
    TradingVenue,
)


class CentralServerDatabaseTests(unittest.TestCase):
    def test_initialize_records_schema_baseline_once_and_preserves_existing_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            store = SQLiteQueryStore(path)
            store.initialize()
            store.save_dataset_snapshot("ranking", "5", "2026-09-10T09:00:00", {"items": [1]})
            store.initialize()
            with closing(sqlite3.connect(path)) as connection:
                versions = connection.execute(
                    "SELECT version,name FROM central_schema_migrations ORDER BY version"
                ).fetchall()
            snapshots = store.load_dataset_snapshots("ranking", "5")

        self.assertEqual(
            [
                (1, CENTRAL_SCHEMA_BASELINE_NAME),
                (2, CENTRAL_MARKET_METADATA_MIGRATION_NAME),
                (CENTRAL_SCHEMA_VERSION, CENTRAL_MARKET_STATE_TIME_REPAIR_MIGRATION_NAME),
            ],
            versions,
        )
        self.assertEqual([1], snapshots[0]["payload"]["items"])

    def test_initialize_rejects_a_newer_central_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            store = SQLiteQueryStore(path)
            store.initialize()
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(
                    "INSERT INTO central_schema_migrations(version,name,applied_at) "
                    "VALUES(4,'future','2026-09-10T00:00:00+00:00')"
                )
                connection.commit()

            with self.assertRaisesRegex(CentralSchemaMigrationError, "newer"):
                store.initialize()

    def test_sqlite_query_cache_round_trip_and_expiry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            value = StoredQuery({"return_code": 0}, True, "next")
            store.save_query("valid", "ka10001", time.time() + 60, value)
            store.save_query("expired", "ka10001", time.time() - 1, value)
            self.assertEqual(value, store.load_query("valid"))
            self.assertIsNone(store.load_query("expired"))

    def test_store_factory_rejects_network_file_paths(self) -> None:
        with self.assertRaisesRegex(ValueError, "sqlite"):
            create_query_store("file://nas/shared/monitor.sqlite3")

    def test_realtime_snapshot_round_trip_filters_by_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            store.save_realtime_snapshots([
                {"event_type": "trade", "item_key": "005930", "received_at": time.time(),
                 "event": {"type": "trade", "payload": {"code": "005930"}}},
                {"event_type": "trade", "item_key": "000660", "received_at": time.time(),
                 "event": {"type": "trade", "payload": {"code": "000660"}}},
                {"event_type": "market_state", "item_key": "kospi", "received_at": time.time(),
                 "event": {"type": "market_state", "payload": {"market": "kospi"}}},
            ])
            events = store.load_realtime_snapshots(["005930"])
        self.assertEqual({"trade", "market_state"}, {event["type"] for event in events})
        self.assertNotIn("000660", str(events))

    def test_minute_bar_upsert_and_market_filter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            base = {"trading_date": "2026-09-08", "minute": "10:01", "code": "005930",
                    "open": 70000, "high": 70100, "low": 69900, "close": 70050,
                    "volume": 10, "trade_value_million_won": 20, "updated_at": time.time()}
            store.save_minute_bars([{**base, "market": "KRX"}, {**base, "market": "NXT", "close": 70060}])
            store.save_minute_bars([{**base, "market": "KRX", "close": 70200}])
            krx = store.load_minute_bars("005930", "2026-09-08", "KRX")
            both = store.load_minute_bars("005930", "2026-09-08")
        self.assertEqual(70200, krx[0]["close"])
        self.assertEqual(20, krx[0]["volume"])
        self.assertEqual(2, len(both))

    def test_bar_and_metadata_are_rolled_back_together(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            value = {
                "trading_date": "2026-09-10", "minute": "10:01", "code": "005930",
                "market": "KRX", "open": 70000, "high": 70100, "low": 69900,
                "close": 70050, "volume": 10, "trade_value_million_won": 20,
                "updated_at": time.time(),
            }
            observation = MarketDataObservation(
                MarketDatasetKind.MINUTE_BAR,
                "005930:KRX",
                value,
                MarketDataMetadata(None, None),
            )

            with self.assertRaisesRegex(ValueError, "observation_key"):
                store.replace_minute_bars(
                    [value], observations=[(" ", observation)]
                )

            self.assertEqual([], store.load_minute_bars("005930", "2026-09-10", "KRX"))

    def test_daily_bar_replace_and_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            rows = []
            for day, close in (("2026-09-07", 70000), ("2026-09-08", 71000)):
                rows.append({"trading_date": day, "code": "005930", "market": "KRX",
                             "open": close - 100, "high": close + 100, "low": close - 200,
                             "close": close, "volume": 100, "trade_value_million_won": 7000,
                             "updated_at": time.time()})
            store.replace_daily_bars(rows)
            latest = store.load_daily_bars("005930", "KRX", 1)
        self.assertEqual(1, len(latest))
        self.assertEqual("2026-09-08", latest[0]["trading_date"])

    def test_dataset_snapshot_upsert_and_filter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            store.save_dataset_snapshot("ranking", "5", "2026-09-08T10:00:00", {"items": [1]})
            store.save_dataset_snapshot("ranking", "5", "2026-09-08T10:00:00", {"items": [2]})
            store.save_dataset_snapshot("ranking", "1", "2026-09-08T10:00:00", {"items": [3]})
            values = store.load_dataset_snapshots("ranking", "5")
        self.assertEqual(1, len(values))
        self.assertEqual([2], values[0]["payload"]["items"])

    def test_market_metadata_round_trip_and_same_key_correction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            store = SQLiteQueryStore(path)
            store.initialize()
            effective_at = datetime(2026, 9, 10, 9, 1, tzinfo=UTC)
            first = MarketDataObservation(
                MarketDatasetKind.CANDIDATE_SET,
                "ranking/5",
                None,
                MarketDataMetadata(None, None),
            )
            corrected_metadata = MarketDataMetadata(
                effective_at,
                effective_at,
                TradingVenue.COMBINED,
                DataUnit.COUNT,
                DataValueKind.ACTUAL,
                DataCompleteness.COMPLETE,
                ObservationOrigin.QUERY,
                "kiwoom-ka00198",
                CandidateUniverse.RANKING_TOP20,
            )
            corrected = MarketDataObservation(
                MarketDatasetKind.CANDIDATE_SET,
                "ranking/5",
                None,
                corrected_metadata,
            )
            store.save_market_data_metadata("2026-09-10T09:01:00", first)
            store.save_market_data_metadata("2026-09-10T09:01:00", corrected)

            loaded = store.load_market_data_metadata(
                MarketDatasetKind.CANDIDATE_SET,
                "ranking/5",
                "2026-09-10T09:01:00",
            )
            with closing(sqlite3.connect(path)) as connection:
                count = connection.execute(
                    "SELECT count(*) FROM central_market_data_observation_meta"
                ).fetchone()[0]

        self.assertEqual(corrected_metadata, loaded)
        self.assertEqual(1, count)

    def test_market_metadata_range_filters_subject_and_effective_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            base = datetime(2026, 9, 10, 0, 0, tzinfo=UTC)
            for key, subject, offset in (
                ("before", "ranking/5", -1),
                ("included", "ranking/5", 1),
                ("other", "ranking/1", 1),
                ("end", "ranking/5", 3),
            ):
                effective = base + timedelta(minutes=offset)
                store.save_market_data_metadata(
                    key,
                    MarketDataObservation(
                        MarketDatasetKind.CANDIDATE_SET,
                        subject,
                        None,
                        MarketDataMetadata(
                            effective,
                            effective,
                            completeness=DataCompleteness.COMPLETE,
                        ),
                    ),
                )

            loaded = store.load_market_data_metadata_range(
                MarketDatasetKind.CANDIDATE_SET,
                "ranking/5",
                base,
                base + timedelta(minutes=3),
            )

        self.assertEqual(["included"], [item.observation_key for item in loaded])

    def test_distinct_ranking_observation_times_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            first_items = [{"stk_cd": "005930", "bigd_rank": "1"}, {"stk_cd": "000660", "bigd_rank": "2"}]
            second_items = [{"stk_cd": "000660", "bigd_rank": "1"}, {"stk_cd": "005930", "bigd_rank": "2"}]
            store.save_dataset_snapshot(
                "ranking", "5", "2026-09-10T09:00:00", {"query_type": "5", "items": first_items},
            )
            store.save_dataset_snapshot(
                "ranking", "5", "2026-09-10T09:00:30", {"query_type": "5", "items": second_items},
            )

            values = store.load_dataset_snapshots("ranking", "5")

        self.assertEqual(2, len(values))
        self.assertEqual("2026-09-10T09:00:30", values[0]["snapshot_key"])
        self.assertEqual(second_items, values[0]["payload"]["items"])
        self.assertEqual(first_items, values[1]["payload"]["items"])

    def test_initialize_repairs_market_state_special_time_without_losing_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            store = SQLiteQueryStore(path)
            store.initialize()
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(
                    "DELETE FROM central_schema_migrations WHERE version=?",
                    (CENTRAL_SCHEMA_VERSION,),
                )
                connection.execute(
                    "INSERT INTO central_dataset_snapshots(kind,subject,snapshot_key,saved_at,payload_json) "
                    "VALUES('market_state','kospi','2026-09-11T88:88',1789108381.0,'{\"value\":1}')"
                )
                connection.execute(
                    "INSERT INTO central_market_data_observation_meta("
                    "dataset_kind,subject,observation_key,effective_at,available_at,venue,unit,"
                    "value_kind,completeness,origin,source,candidate_universe) "
                    "VALUES('market_state','kospi','2026-09-11T88:88',NULL,"
                    "'2026-09-11T15:33:01+09:00','KRX','unknown','actual','complete','realtime',"
                    "'kiwoom-websocket-0J-0U','market_all')"
                )
                connection.commit()

            store.initialize()
            snapshots = store.load_dataset_snapshots("market_state", "kospi")
            repaired_metadata = store.load_market_data_metadata(
                MarketDatasetKind.MARKET_STATE, "kospi", "2026-09-11T15:33",
            )
            invalid_metadata = store.load_market_data_metadata(
                MarketDatasetKind.MARKET_STATE, "kospi", "2026-09-11T88:88",
            )

        self.assertEqual(["2026-09-11T15:33"], [row["snapshot_key"] for row in snapshots])
        self.assertEqual(1, snapshots[0]["payload"]["value"])
        self.assertIsNotNone(repaired_metadata)
        self.assertIsNone(invalid_metadata)

    def test_content_documents_upsert_without_retention_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            store.upsert_documents("news_article", [
                {"owner": "005930", "key": "https://news/1", "document": {"title": "first"}},
                {"owner": "000660", "key": "https://news/2", "document": {"title": "second"}},
            ])
            store.upsert_documents("news_article", [
                {"owner": "005930", "key": "https://news/1", "document": {"title": "updated"}},
            ])
            samsung = store.load_documents("news_article", "005930")
            all_news = store.load_documents("news_article")
        self.assertEqual("updated", samsung[0]["document"]["title"])
        self.assertEqual(2, len(all_news))

    def test_external_market_bars_accumulate_and_upsert_same_observation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            base = {"provider": "yahoo_delayed", "instrument": "NASDAQ_FUTURES",
                    "contract": "MNQU26.CME", "timeframe": "5m", "open": 25000.0,
                    "high": 25010.0, "low": 24990.0, "close": 25005.0, "volume": 10.0,
                    "updated_at": time.time()}
            store.save_external_bars([{**base, "bar_time": "2026-09-10T00:00:00Z"}])
            store.save_external_bars([{**base, "bar_time": "2026-09-10T00:05:00Z", "close": 25020.0}])
            store.save_external_bars([{**base, "bar_time": "2026-09-10T00:05:00Z", "close": 25021.0}])
            rows = store.load_external_bars("NASDAQ_FUTURES", "5m")
            store.close()
        self.assertEqual(2, len(rows))
        self.assertEqual(25021.0, rows[-1]["close"])


if __name__ == "__main__":
    unittest.main()
