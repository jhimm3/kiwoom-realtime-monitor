from __future__ import annotations

import tempfile
import time
import unittest
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.central_server.central_schema import (
    CENTRAL_SCHEMA_BASELINE_NAME,
    CENTRAL_MARKET_METADATA_MIGRATION_NAME,
    CENTRAL_MARKET_STATE_TIME_REPAIR_MIGRATION_NAME,
    CENTRAL_SECOND_TRADE_BARS_MIGRATION_NAME,
    CENTRAL_THEME_HISTORY_MIGRATION_NAME,
    CENTRAL_NEWS_HISTORY_MIGRATION_NAME,
    CENTRAL_NEWS_EVENT_MIGRATION_NAME,
    CENTRAL_NEWS_SOURCE_MIGRATION_NAME,
    CENTRAL_MARKET_EVENT_MIGRATION_NAME,
    CENTRAL_NEWS_REUSE_MIGRATION_NAME,
    CENTRAL_N3_STOCK_NEWS_MIGRATION_NAME,
    CENTRAL_OBSERVATION_HISTORY_MIGRATION_NAME,
    CENTRAL_RESEARCH_EXPORT_MIGRATION_NAME,
    CENTRAL_SHADOW_CANDIDATE_MIGRATION_NAME,
    CENTRAL_MOCK_EXECUTION_MIGRATION_NAME,
    CENTRAL_ACCOUNT_IDENTITY_MIGRATION_NAME,
    CENTRAL_ACCOUNT_SCOPE_ALIAS_MIGRATION_NAME,
    CENTRAL_MINUTE_BAR_REVISION_MIGRATION_NAME,
    CENTRAL_SCHEMA_VERSION,
    central_schema_migrations,
)
from kiwoom_monitor.central_server.database import (
    PostgresQueryStore,
    SQLiteQueryStore,
    StoredQuery,
    create_query_store,
)
from kiwoom_monitor.central_server.schema_migrations import CentralSchemaMigrationError, CentralSchemaMigrationRunner
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
    def test_stock_news_articles_load_latest_identity_with_latest_body(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            store.upsert_documents("news_article", [{
                "owner": "005930", "key": "article-1", "collector_id": "naver",
                "collection_scope": "watchlist",
                "document": {
                    "stock_code": "005930", "stock_name": "삼성전자",
                    "title": "첫 제목", "description": "첫 요약",
                    "link": "https://n/1", "original_link": "https://o/1",
                },
            }])
            article = store.load_news_history("article", target="005930", limit=1)[0]
            store.save_news_body_revision({
                "article_revision_id": article["article_revision_id"],
                "extractor_version": "test-v1", "status": "fulltext",
                "body_text": "삼성전자가 새 공급계약 체결을 발표했습니다.", "error": "",
            })

            loaded = store.load_stock_news_articles("005930")
            store.close()

        self.assertEqual(1, len(loaded))
        self.assertEqual("첫 제목", loaded[0]["title"])
        self.assertEqual("fulltext", loaded[0]["_body_status"])
        self.assertIn("새 공급계약", loaded[0]["_body_text"])

    def test_version_10_fixture_adds_confirmed_stock_news_index_without_losing_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            with closing(sqlite3.connect(path)) as connection:
                CentralSchemaMigrationRunner(connection.cursor(), "sqlite").apply(central_schema_migrations()[:10])
                connection.execute(
                    "INSERT INTO central_news_article_revisions(article_revision_id,stock_code,identity,"
                    "content_hash,collector_id,published_at,received_at,available_at,collection_scope,"
                    "revision_of,document_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    ("article-v10", "GLOBAL", "identity-v10", "hash-v10", "naver", None,
                     10.0, 11.0, "query_set", None, '{"title":"legacy"}'),
                )
                connection.execute(
                    "INSERT INTO central_news_article_target_revisions(target_revision_id,article_revision_id,"
                    "identity,stock_code,stock_name,relation_status,evidence_text,rule_version,available_at,"
                    "revision_of,document_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    ("target-v10", "article-v10", "identity-v10", "005930", "삼성전자",
                    "confirmed", "삼성전자", "exact-v1", 11.0, None, '{}'),
                )
                connection.execute(
                    "INSERT INTO central_news_body_revisions(body_revision_id,article_revision_id,content_hash,"
                    "extractor_version,fetched_at,available_at,status,body_text,error) VALUES(?,?,?,?,?,?,?,?,?)",
                    ("body-v10", "article-v10", "body-hash-v10", "extractor-v1", 11.0, 12.0,
                     "fulltext", "삼성전자 장중 주가 기사 원문", ""),
                )
                connection.commit()

            store = SQLiteQueryStore(path)
            store.initialize()
            loaded = store.load_confirmed_news_articles("005930")
            with closing(sqlite3.connect(path)) as connection:
                migration = connection.execute(
                    "SELECT version,name FROM central_schema_migrations WHERE version=11",
                ).fetchone()
                index = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' "
                    "AND name='idx_central_news_targets_stock_lookup'",
                ).fetchone()
            store.close()

        self.assertEqual("legacy", loaded[0]["title"])
        self.assertEqual("fulltext", loaded[0]["_body_status"])
        self.assertEqual("삼성전자 장중 주가 기사 원문", loaded[0]["_body_text"])
        self.assertEqual((11, CENTRAL_N3_STOCK_NEWS_MIGRATION_NAME), migration)
        self.assertIsNotNone(index)

    def test_version_9_fixture_adds_news_source_reuse_index_without_losing_observation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            with closing(sqlite3.connect(path)) as connection:
                CentralSchemaMigrationRunner(connection.cursor(), "sqlite").apply(central_schema_migrations()[:9])
                connection.execute(
                    "INSERT INTO central_news_article_revisions(article_revision_id,stock_code,identity,"
                    "content_hash,collector_id,published_at,received_at,available_at,collection_scope,"
                    "revision_of,document_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    ("article-v9", "GLOBAL", "identity-v9", "hash-v9", "naver", None,
                     10.0, 11.0, "query_set", None, '{"title":"legacy"}'),
                )
                connection.execute(
                    "INSERT INTO central_news_source_observations(observation_id,run_id,source_id,query_text,"
                    "page_start,article_revision_id,identity,published_at,received_at,available_at,content_hash,"
                    "duplicate,document_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("observation-v9", "run-v9", "source-v9", "증권", 1, "article-v9",
                     "identity-v9", None, 10.0, 11.0, "hash-v9", 0, '{}'),
                )
                connection.commit()

            store = SQLiteQueryStore(path)
            store.initialize()
            with closing(sqlite3.connect(path)) as connection:
                observation = connection.execute(
                    "SELECT observation_id,article_revision_id,identity,content_hash "
                    "FROM central_news_source_observations",
                ).fetchone()
                migration = connection.execute(
                    "SELECT version,name FROM central_schema_migrations WHERE version=10",
                ).fetchone()
                index = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' "
                    "AND name='idx_central_news_source_identity_lookup'",
                ).fetchone()
            store.close()

        self.assertEqual(("observation-v9", "article-v9", "identity-v9", "hash-v9"), observation)
        self.assertEqual((10, CENTRAL_NEWS_REUSE_MIGRATION_NAME), migration)
        self.assertIsNotNone(index)

    def test_version_8_fixture_adds_market_event_tables(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            with closing(sqlite3.connect(path)) as connection:
                CentralSchemaMigrationRunner(connection.cursor(), "sqlite").apply(central_schema_migrations()[:8])
                connection.commit()
            store = SQLiteQueryStore(path)
            store.initialize()
            with closing(sqlite3.connect(path)) as connection:
                tables = {row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'central_%cohort%' "
                    "OR name='central_vi_event_revisions' OR name='central_upper_limit_fact_revisions'",
                ).fetchall()}
            store.close()
        self.assertEqual({"central_hot_cohort_current", "central_hot_cohort_revisions",
                          "central_vi_event_revisions", "central_upper_limit_fact_revisions"}, tables)

    def test_version_7_fixture_adds_query_sources_without_losing_event_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            with closing(sqlite3.connect(path)) as connection:
                CentralSchemaMigrationRunner(connection.cursor(), "sqlite").apply(central_schema_migrations()[:7])
                connection.commit()
            store = SQLiteQueryStore(path)
            store.initialize()
            with closing(sqlite3.connect(path)) as connection:
                migration = connection.execute(
                    "SELECT name FROM central_schema_migrations WHERE version=8",
                ).fetchone()
                tables = {row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'central_news_source%'",
                ).fetchall()}
                event_table = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='central_news_event_revisions'",
                ).fetchone()
            store.close()
        self.assertEqual((CENTRAL_NEWS_SOURCE_MIGRATION_NAME,), migration)
        self.assertEqual({"central_news_source_cursors", "central_news_source_runs",
                          "central_news_source_observations"}, tables)
        self.assertIsNotNone(event_table)

    def test_version_6_fixture_migrates_to_event_history_without_losing_articles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            with closing(sqlite3.connect(path)) as connection:
                CentralSchemaMigrationRunner(connection.cursor(), "sqlite").apply(central_schema_migrations()[:6])
                connection.execute(
                    "INSERT INTO central_news_article_revisions(article_revision_id,stock_code,identity,"
                    "content_hash,collector_id,published_at,received_at,available_at,collection_scope,"
                    "revision_of,document_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    ("article-v6", "005930", "legacy", "hash", "naver", None, 10.0, 11.0,
                     "watchlist", None, '{"title":"legacy"}'),
                )
                connection.commit()

            store = SQLiteQueryStore(path)
            store.initialize()
            articles = store.load_news_history("article", target="005930")
            with closing(sqlite3.connect(path)) as connection:
                migration = connection.execute(
                    "SELECT name FROM central_schema_migrations WHERE version=7",
                ).fetchone()
                event_table = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='central_news_event_revisions'",
                ).fetchone()
            store.close()

        self.assertEqual("legacy", articles[0]["identity"])
        self.assertEqual((CENTRAL_NEWS_EVENT_MIGRATION_NAME,), migration)
        self.assertIsNotNone(event_table)

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
                (3, CENTRAL_MARKET_STATE_TIME_REPAIR_MIGRATION_NAME),
                (4, CENTRAL_SECOND_TRADE_BARS_MIGRATION_NAME),
                (5, CENTRAL_THEME_HISTORY_MIGRATION_NAME),
                (6, CENTRAL_NEWS_HISTORY_MIGRATION_NAME),
                (7, CENTRAL_NEWS_EVENT_MIGRATION_NAME),
                (8, CENTRAL_NEWS_SOURCE_MIGRATION_NAME),
                (9, CENTRAL_MARKET_EVENT_MIGRATION_NAME),
                (10, CENTRAL_NEWS_REUSE_MIGRATION_NAME),
                (11, CENTRAL_N3_STOCK_NEWS_MIGRATION_NAME),
                (12, CENTRAL_OBSERVATION_HISTORY_MIGRATION_NAME),
                (13, CENTRAL_RESEARCH_EXPORT_MIGRATION_NAME),
                (14, CENTRAL_MINUTE_BAR_REVISION_MIGRATION_NAME),
                (15, CENTRAL_SHADOW_CANDIDATE_MIGRATION_NAME),
                (16, CENTRAL_MOCK_EXECUTION_MIGRATION_NAME),
                (17, CENTRAL_ACCOUNT_IDENTITY_MIGRATION_NAME),
                (18, CENTRAL_ACCOUNT_SCOPE_ALIAS_MIGRATION_NAME),
                (CENTRAL_SCHEMA_VERSION, "encrypted_credential_activation_ledger"),
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
                    "VALUES(20,'future','2026-09-10T00:00:00+00:00')"
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
                    (3,),
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

    def test_identical_content_upsert_keeps_server_updated_at(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            value = {"owner": "005930", "key": "article", "document": {"title": "same"}}
            with patch("kiwoom_monitor.central_server.database.time", return_value=10.0):
                store.upsert_documents("news_article", [value])
            with patch("kiwoom_monitor.central_server.database.time", return_value=20.0):
                store.upsert_documents("news_article", [value])
            unchanged = store.load_documents("news_article")[0]
            with patch("kiwoom_monitor.central_server.database.time", return_value=30.0):
                store.upsert_documents("news_article", [{
                    **value, "document": {"title": "changed"},
                }])
            changed = store.load_documents("news_article")[0]
            store.close()

        self.assertEqual(10.0, unchanged["updated_at"])
        self.assertEqual(30.0, changed["updated_at"])

    def test_postgres_content_upsert_has_same_idempotent_guard(self) -> None:
        class Cursor:
            def __init__(self) -> None:
                self.sql = ""

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def executemany(self, sql, _rows) -> None:
                self.sql = sql

        class Connection:
            def __init__(self, cursor) -> None:
                self._cursor = cursor

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def cursor(self):
                return self._cursor

        cursor = Cursor()
        store = PostgresQueryStore("postgresql://unused")
        store._connect = lambda: Connection(cursor)  # type: ignore[method-assign]

        store.upsert_documents("fixture", [{
            "owner": "owner", "key": "key", "document": {"title": "same"},
        }])

        self.assertIn(
            "central_documents.document_json IS DISTINCT FROM EXCLUDED.document_json",
            cursor.sql,
        )

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
