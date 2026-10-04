from __future__ import annotations

import json
import tempfile
import time
import unittest
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

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
    CENTRAL_FIVE_MINUTE_BARS_MIGRATION_NAME,
    CENTRAL_SCHEMA_VERSION,
    central_schema_migrations,
)
from kiwoom_monitor.central_server.database import (
    POSTGRES_MULTIROW_UPSERT_ROWS,
    SQLITE_MULTIROW_UPSERT_ROWS,
    PostgresQueryStore,
    SQLiteQueryStore,
    StoredQuery,
    _append_postgres_observation_revision,
    _insert_postgres_observation_revision,
    _insert_postgres_observation_revisions_batch,
    _insert_sqlite_observation_revisions_batch,
    _load_postgres_latest_revisions,
    _execute_multirow_upsert,
    _uses_async_dataset_commit,
    create_query_store,
)
from kiwoom_monitor.central_server.market_observations import (
    bar_observation_key, daily_bar_observation, minute_bar_observation,
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
from kiwoom_monitor.domain.research_contract import ObservationRevisionSource


class CentralServerDatabaseTests(unittest.TestCase):
    def test_multirow_upsert_uses_one_postgres_statement_and_bounded_sqlite_batches(self) -> None:
        class Executor:
            def __init__(self) -> None:
                self.calls: list[tuple[str, tuple[object, ...]]] = []

            def execute(self, sql, parameters) -> None:
                self.calls.append((sql, tuple(parameters)))

        rows = [(index, index + 1, index + 2) for index in range(900)]
        postgres = Executor()
        sqlite = Executor()

        postgres_statements = _execute_multirow_upsert(
            postgres, "INSERT INTO sample VALUES", rows,
            "ON CONFLICT(id) DO NOTHING", placeholder="%s",
            batch_size=POSTGRES_MULTIROW_UPSERT_ROWS,
        )
        sqlite_statements = _execute_multirow_upsert(
            sqlite, "INSERT INTO sample VALUES", rows,
            "ON CONFLICT(id) DO NOTHING", placeholder="?",
            batch_size=SQLITE_MULTIROW_UPSERT_ROWS,
        )

        self.assertEqual(1, postgres_statements)
        self.assertEqual(12, sqlite_statements)
        self.assertEqual(2_700, len(postgres.calls[0][1]))
        self.assertEqual(240, len(sqlite.calls[0][1]))

    def test_multirow_upsert_batch_boundaries_fit_driver_bind_limits(self) -> None:
        class Executor:
            def __init__(self) -> None:
                self.calls = []

            def execute(self, sql, parameters) -> None:
                self.calls.append((sql, parameters))

        for count in (1, 80, 81, 900, 1001):
            rows = [tuple([index] * 12) for index in range(count)]
            for placeholder, batch_size, bind_limit in (
                ("?", SQLITE_MULTIROW_UPSERT_ROWS, 999),
                ("%s", POSTGRES_MULTIROW_UPSERT_ROWS, 65535),
            ):
                with self.subTest(count=count, placeholder=placeholder):
                    executor = Executor()
                    statements = _execute_multirow_upsert(
                        executor, "INSERT INTO sample VALUES", rows,
                        "ON CONFLICT(id) DO NOTHING", placeholder=placeholder,
                        batch_size=batch_size,
                    )
                    self.assertEqual((count + batch_size - 1) // batch_size, statements)
                    self.assertEqual(count * 12, sum(len(params) for _, params in executor.calls))
                    self.assertTrue(all(len(params) <= bind_limit for _, params in executor.calls))

    def test_sqlite_multirow_sql_executes_at_batch_boundaries(self) -> None:
        for count in (1, 80, 81, 900, 1001):
            with self.subTest(count=count), closing(sqlite3.connect(":memory:")) as connection:
                connection.execute(
                    "CREATE TABLE sample (id INTEGER PRIMARY KEY," +
                    ",".join(f"field_{index} INTEGER" for index in range(11)) + ")"
                )
                rows = [(index, *([index + 1] * 11)) for index in range(count)]
                statements = _execute_multirow_upsert(
                    connection, "INSERT INTO sample VALUES", rows,
                    "ON CONFLICT(id) DO UPDATE SET field_0=excluded.field_0",
                    placeholder="?", batch_size=SQLITE_MULTIROW_UPSERT_ROWS,
                )
                self.assertEqual((count + 79) // 80, statements)
                self.assertEqual(count, connection.execute("SELECT count(*) FROM sample").fetchone()[0])
                self.assertEqual(count, connection.execute(
                    "SELECT field_0 FROM sample WHERE id=?", (count - 1,),
                ).fetchone()[0])

    def test_sqlite_duplicate_minute_transition_preserves_final_update_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            original = {
                "trading_date": "2099-01-06", "minute": "10:00",
                "code": "005930", "market": "KRX", "open": 100,
                "high": 110, "low": 90, "close": 100, "volume": 10,
                "trade_value_million_won": 1, "updated_at": 1000.0,
            }
            store.replace_minute_bars([original])
            changed = {**original, "close": 101, "updated_at": 1001.0}
            restored = {**original, "updated_at": 1002.0}
            store.replace_minute_bars([changed, restored])
            [saved] = store.load_minute_bars("005930", "2099-01-06", "KRX")
            store.close()
        self.assertEqual(100, saved["close"])
        self.assertEqual(1002.0, saved["updated_at"])

    def test_postgres_revision_helper_reports_insert_without_changing_deduplication(self) -> None:
        value = {
            "trading_date": "2026-09-25", "minute": "09:30", "code": "005930",
            "market": "KRX", "open": 100, "high": 101, "low": 99,
            "close": 100, "volume": 10, "trade_value_million_won": 1,
            "updated_at": 1790300000.0,
        }
        observation = minute_bar_observation(
            value, origin=ObservationOrigin.QUERY,
            completeness=DataCompleteness.COMPLETE,
            source="kiwoom-ka10080", value_kind=DataValueKind.ACTUAL,
        )
        payload = {"close": value["close"]}
        source = ObservationRevisionSource.from_observation(
            "minute_bar", observation.subject, "2026-09-25T09:30", payload, observation,
        )

        class Cursor:
            def __init__(self, latest):
                self.latest = latest
                self.insert_count = 0

            def execute(self, sql, parameters):
                if sql.startswith("SELECT revision_id,payload_hash"):
                    return None
                if sql.startswith("INSERT INTO central_observation_revisions"):
                    self.insert_count += 1
                    return None
                raise AssertionError(f"unexpected query: {sql}")

            def fetchone(self):
                return self.latest

        unchanged = Cursor(("existing", source.payload_hash))
        self.assertFalse(_append_postgres_observation_revision(
            unchanged, "minute_bar", observation.subject, "2026-09-25T09:30",
            payload, observation,
        ))
        self.assertEqual(0, unchanged.insert_count)

        changed = Cursor(("existing", "different-hash"))
        self.assertTrue(_append_postgres_observation_revision(
            changed, "minute_bar", observation.subject, "2026-09-25T09:30",
            payload, observation,
        ))
        self.assertEqual(1, changed.insert_count)

    def test_postgres_revision_batch_preserves_duplicate_key_chain(self) -> None:
        def source(close: int, minute: str = "09:30") -> ObservationRevisionSource:
            value = {
                "trading_date": "2026-09-25", "minute": minute, "code": "005930",
                "market": "KRX", "open": 100, "high": max(110, close), "low": 90,
                "close": close, "volume": 10, "trade_value_million_won": 1,
                "updated_at": 1790300000.0,
            }
            observation = minute_bar_observation(
                value, origin=ObservationOrigin.QUERY,
                completeness=DataCompleteness.COMPLETE,
                source="kiwoom-ka10080", value_kind=DataValueKind.ACTUAL,
            )
            return ObservationRevisionSource.from_observation(
                "minute_bar", observation.subject, f"2026-09-25T{minute}",
                {"close": close}, observation,
            )

        class Cursor:
            def __init__(self) -> None:
                self.statements = []
                self.inserted = []
                self.inserted_sequences = []
                self.result = []
                self.next_sequence = 1000

            def execute(self, sql, params):
                self.statements.append((sql, params))
                if "LEFT JOIN LATERAL" in sql:
                    self.result = [(*key, None, None) for key in zip(*params)]
                if "FROM generate_series" in sql:
                    self.result = [(number,) for number in range(
                        self.next_sequence, self.next_sequence + params[0]
                    )]
                    self.next_sequence += params[0]
                if sql.startswith("INSERT INTO central_observation_revisions"):
                    self.inserted_sequences.extend(
                        params[offset]
                        for offset in range(0, len(params), 24)
                    )
                    self.inserted.extend(
                        params[offset + 1:offset + 24]
                        for offset in range(0, len(params), 24)
                    )

            def fetchall(self):
                return self.result

        cursor = Cursor()
        sources = [source(100), source(101), source(101), source(102, "09:31")]
        timings = {}
        latest = _load_postgres_latest_revisions(cursor, sources, timings=timings)
        self.assertEqual(2, len(latest))
        self.assertGreaterEqual(timings["lock_seconds"], 0)
        self.assertGreaterEqual(timings["lookup_seconds"], 0)
        self.assertEqual(1, sum("LEFT JOIN LATERAL" in sql for sql, _ in cursor.statements))
        self.assertEqual(1, sum("pg_advisory_xact_lock" in sql for sql, _ in cursor.statements))
        insert_execute_seconds = [0.0]
        statement_count, inserted_rows = _insert_postgres_observation_revisions_batch(
            cursor, sources, latest, execute_seconds=insert_execute_seconds,
        )
        self.assertEqual(1, statement_count)
        self.assertEqual(3, inserted_rows)
        self.assertEqual(3, len(cursor.inserted))
        self.assertEqual([1000, 1001, 1002], cursor.inserted_sequences)
        self.assertGreaterEqual(insert_execute_seconds[0], 0)
        self.assertIsNone(cursor.inserted[0][12])
        self.assertEqual(cursor.inserted[0][0], cursor.inserted[1][12])
        self.assertIsNone(cursor.inserted[2][12])

        # A repeated key must keep one chain even when the batch size is crossed.
        earlier_rows = len(cursor.inserted)
        statements, rows = _insert_postgres_observation_revisions_batch(
            cursor, [source(102 + index) for index in range(1001)], latest,
        )
        self.assertEqual((2, 1001), (statements, rows))
        self.assertEqual(cursor.inserted[earlier_rows + 999][0],
                         cursor.inserted[earlier_rows + 1000][12])
        self.assertEqual(list(range(1000, 2004)), cursor.inserted_sequences)

    def test_only_reconstructable_live_snapshots_use_async_commit(self) -> None:
        self.assertTrue(_uses_async_dataset_commit([
            ("top20_membership", "2026-09-22", "key", {}, None),
            ("program_flow", "005930", "key", {}, None),
        ]))
        self.assertFalse(_uses_async_dataset_commit([
            ("top20_membership", "2026-09-22", "key", {}, None),
            ("investor_flow", "005930", "key", {}, None),
        ]))

    def test_storage_breakdown_groups_shared_documents_without_deleting_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            store.upsert_documents("news_article", [{
                "owner": "005930", "key": "article-1", "document": {"title": "뉴스"},
            }])
            store.upsert_documents("journal_v2_reviews", [{
                "owner": "account-1", "key": "review-1", "document": {"memo": "복기"},
            }])
            store.save_dataset_snapshot(
                "top20_membership", "2026-09-21", "2026-09-21T10:00:00",
                {"codes": ["005930"]},
            )

            breakdown = {item["category"]: item for item in store.storage_breakdown()}
            store.close()

        self.assertEqual({"news", "market", "research", "account", "other"}, set(breakdown))
        self.assertGreaterEqual(breakdown["news"]["rows"], 1)
        self.assertGreaterEqual(breakdown["account"]["rows"], 1)
        self.assertGreaterEqual(breakdown["market"]["rows"], 1)

    def test_top20_statistics_use_regular_close_and_finalized_market_daily_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            for minute, values in (("15:29", [10.0, 5.0, 0.0]), ("15:30", [3.0, 2.0, 0.0])):
                key = f"2026-09-08T{minute}"
                store.save_dataset_snapshot("top20_index", "2026-09-08", key, {
                    "minute": key, "market_values": values,
                    "capture_state": "realtime_complete",
                })
            for market, amount in (("kospi", "26187833"), ("kosdaq", "8152341")):
                store.save_dataset_snapshot(
                    "market_index_chart", f"20260908:{market}", "20260908",
                    {"daily": [{"dt": "20260908", "trde_prica": amount}]},
                )
            store.save_dataset_snapshot("top20_index", "2026-09-07", "2026-09-07T09:00", {
                "minute": "2026-09-07T09:00", "market_values": [0.0, 0.0, 0.0],
                "capture_state": "realtime_complete",
            })

            result = store.load_top20_statistics("2026-09-08", "2026-09-08")
            again = store.load_top20_statistics("2026-09-08", "2026-09-08")
            cached = store.load_dataset_snapshots("top20_statistics_day", "2026-09-08")
            store.save_dataset_snapshot("top20_index", "2026-09-08", "2026-09-08T15:30", {
                "minute": "2026-09-08T15:30", "market_values": [4.0, 2.0, 0.0],
                "capture_state": "realtime_complete",
            })
            invalidated = store.load_dataset_snapshots("top20_statistics_day", "2026-09-08")
            refreshed = store.load_top20_statistics("2026-09-08", "2026-09-08")
            holiday = store.load_top20_statistics("2026-09-07", "2026-09-07")
            combined = store.load_top20_statistics("2026-09-07", "2026-09-08")
            store.close()

        comparison = result["comparisons"][0]
        self.assertEqual(20.0, comparison["top20_eok"])
        self.assertEqual([13.0, 7.0, 0.0], comparison["top20_market_values"])
        self.assertEqual(261878.33, comparison["kospi_eok"])
        self.assertEqual(81523.41, comparison["kosdaq_eok"])
        self.assertEqual(result, again)
        self.assertEqual(1, len(cached))
        self.assertEqual([], invalidated)
        self.assertEqual(21.0, refreshed["comparisons"][0]["top20_eok"])
        self.assertEqual([], holiday["hourly"])
        self.assertEqual([], holiday["comparisons"])
        self.assertEqual(refreshed, combined)

    def test_archived_credential_profile_is_retained_with_history_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            created = store.create_credential_profile(
                "kiwoom_mock", "11111111-1111-4111-8111-111111111111", "이전 계좌", "digest",
            )

            archived = store.archive_credential_profile("kiwoom_mock", created["profile_id"])
            profiles = store.list_credential_profiles()
            store.close()

        self.assertEqual("archived", archived["lifecycle_state"])
        stored = next(value for value in profiles if value["profile_id"] == created["profile_id"])
        self.assertEqual("archived", stored["lifecycle_state"])

    def test_credential_profile_rename_changes_only_display_label(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            created = store.create_credential_profile(
                "kiwoom_mock", "11111111-1111-4111-8111-111111111111", "처음 이름", "digest",
            )

            renamed = store.rename_credential_profile(
                "kiwoom_mock", created["profile_id"], "  단타 모의  ",
            )
            stored = next(
                value for value in store.list_credential_profiles()
                if value["profile_id"] == created["profile_id"]
            )
            store.close()

        self.assertEqual({
            "provider": "kiwoom_mock",
            "profile_id": created["profile_id"],
            "label": "단타 모의",
        }, renamed)
        self.assertEqual("단타 모의", stored["label"])
        self.assertEqual("draft", stored["lifecycle_state"])

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

    def test_stock_news_page_query_orders_by_publication_not_ingestion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            store.upsert_documents("news_article", [
                {"owner": "005930", "key": key, "collection_scope": "watchlist",
                 "document": {"title": key, "link": f"https://n/{key}", "published_at": published}}
                for key, published in (
                    ("newest", "2026-09-23T10:00:00+09:00"),
                    ("oldest", "2026-09-21T10:00:00+09:00"),
                    ("middle", "2026-09-22T01:00:00+00:00"),
                )
            ])
            first_two = store.load_stock_news_articles("005930", limit=2)
            store.close()
        self.assertEqual(["newest", "middle"], [item["title"] for item in first_two])

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
                (19, "encrypted_credential_activation_ledger"),
                (20, CENTRAL_FIVE_MINUTE_BARS_MIGRATION_NAME),
            ],
            versions,
        )
        self.assertEqual([1], snapshots[0]["payload"]["items"])

    def test_five_minute_migration_keeps_price_basis_separate_from_live_minutes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            store = SQLiteQueryStore(path)
            store.initialize()
            sample = {
                "trading_date": "2024-08-28", "minute": "09:05", "code": "005930",
                "market": "KRX", "provider": "daishin_creon",
                "bar_time_semantics": "interval_end", "open": 100, "high": 101,
                "low": 99, "close": 100, "volume": 10,
                "trading_value_raw": 1000, "observed_at": "2026-09-24T00:00:00+00:00",
            }
            store.save_five_minute_bars([
                {**sample, "adjustment_mode": "raw"},
                {**sample, "adjustment_mode": "adjusted", "close": 95},
            ])
            store.save_five_minute_bars([{**sample, "adjustment_mode": "adjusted", "close": 96}])
            adjusted = store.load_five_minute_bars("005930", "2024-08-28")
            raw = store.load_five_minute_bars("005930", "2024-08-28", "raw")
            with closing(sqlite3.connect(path)) as connection:
                live_count = connection.execute("SELECT COUNT(*) FROM central_minute_bars").fetchone()[0]
            store.close()
        self.assertEqual(96, adjusted[0]["close"])
        self.assertEqual(100, raw[0]["close"])
        self.assertEqual("09:05", adjusted[0]["minute"])
        self.assertEqual(0, live_count)

    def test_initialize_rejects_a_newer_central_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            store = SQLiteQueryStore(path)
            store.initialize()
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(
                    "INSERT INTO central_schema_migrations(version,name,applied_at) "
                    "VALUES(?,'future','2026-09-10T00:00:00+00:00')",
                    (CENTRAL_SCHEMA_VERSION + 1,),
                )
                connection.commit()

            with self.assertRaisesRegex(CentralSchemaMigrationError, "newer"):
                store.initialize()

    def test_sqlite_checkpoint_keeps_version_20_and_old_runner_compatibility(self) -> None:
        old_plan = central_schema_migrations("postgres")[:20]
        document = {"schema_version": 1, "cursor": 42, "bars": [
            {"code": "005930", "observation_key": "old-frame", "close": 100},
        ], "strategy_state": {"emitted_candidate_keys": ["preserve"]}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "central.sqlite3"
            with closing(sqlite3.connect(path)) as connection:
                with connection:
                    CentralSchemaMigrationRunner(connection.cursor(), "sqlite").apply(old_plan)
            store = SQLiteQueryStore(path)
            store.initialize()
            store.save_shadow_monitor_state("old-monitor", document)
            store.initialize()
            with closing(sqlite3.connect(path)) as connection:
                with connection:
                    # Simulate the exact migration plan supported by the prior
                    # source: a PostgreSQL-only addition must not reject it.
                    self.assertEqual((), CentralSchemaMigrationRunner(
                        connection.cursor(), "sqlite",
                    ).apply(old_plan))
                self.assertEqual(20, connection.execute(
                    "SELECT MAX(version) FROM central_schema_migrations",
                ).fetchone()[0])
                encoded = connection.execute(
                    "SELECT document_json FROM central_shadow_monitor_state WHERE monitor_id=?",
                    ("old-monitor",),
                ).fetchone()[0]
                self.assertEqual(document, json.loads(encoded))
                self.assertEqual(0, connection.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'central_shadow_checkpoint_%'",
                ).fetchone()[0])
            self.assertEqual(document, store.load_shadow_monitor_state("old-monitor"))
            store.close()

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

    def test_latest_market_cap_survives_realtime_snapshot_freshness_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            observed_at = time.time() - 3_600
            store.save_realtime_snapshots([{
                "event_type": "trade", "item_key": "005930", "received_at": observed_at,
                "event": {"type": "trade", "payload": {
                    "code": "005930", "current_price": 70_000,
                    "market_cap_eok": 4_321_000,
                }},
            }])

            fresh_events = store.load_realtime_snapshots(["005930"])
            market_caps = store.load_latest_market_caps(["005930"])

        self.assertEqual([], fresh_events)
        self.assertEqual("005930", market_caps[0]["code"])
        self.assertEqual(4_321_000, market_caps[0]["market_cap_eok"])
        self.assertIn("+00:00", market_caps[0]["observed_at"])

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

    def test_sqlite_open_minute_0b_values_accumulate_as_provisional(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            values = [
                {
                    "trading_date": "2026-09-08", "minute": "09:31", "code": "005930",
                    "market": "KRX", "open": 100, "high": 105, "low": 99, "close": 104,
                    "volume": 10, "trade_value_million_won": 100, "updated_at": 1_790_000_000.0,
                    "operation_id": "sqlite-open-0b-1",
                },
                {
                    "trading_date": "2026-09-08", "minute": "09:31", "code": "005930",
                    "market": "KRX", "open": 100, "high": 110, "low": 98, "close": 108,
                    "volume": 7, "trade_value_million_won": 70, "updated_at": 1_790_000_001.0,
                    "operation_id": "sqlite-open-0b-2",
                },
            ]
            observations = []
            for value in values:
                observation = minute_bar_observation(
                    value, origin=ObservationOrigin.REALTIME,
                    completeness=DataCompleteness.IN_PROGRESS,
                    source="kiwoom-websocket-0B", value_kind=DataValueKind.ACTUAL,
                )
                observations.append((bar_observation_key(observation), observation))

            for value, observation in zip(values, observations, strict=True):
                store.save_minute_bars([value], observations=[observation])

            bar = store.load_minute_bars("005930", "2026-09-08", "KRX")[0]
            with store._lock, store._connection() as connection:
                metadata = connection.execute(
                    "SELECT completeness,origin,source FROM central_market_data_observation_meta "
                    "WHERE dataset_kind='minute_bar' AND subject=? AND observation_key=?",
                    ("005930:KRX", "2026-09-08T09:31"),
                ).fetchone()
            store.close()

        self.assertEqual((100, 110, 98, 108, 17, 170), tuple(
            bar[name] for name in ("open", "high", "low", "close", "volume", "trade_value_million_won")
        ))
        self.assertEqual(("in_progress", "realtime", "kiwoom-websocket-0B"), metadata)

    def test_sqlite_completed_ka10080_bar_rejects_late_0b_and_finalization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            base = {
                "trading_date": "2026-09-08", "minute": "09:31", "code": "005930",
                "market": "KRX", "open": 100, "high": 110, "low": 90, "close": 105,
                "volume": 10, "trade_value_million_won": 1, "updated_at": 1_790_000_000.0,
            }
            provisional = {**base, "volume": 5, "trade_value_million_won": 50,
                           "operation_id": "sqlite-provisional-0b"}
            provisional_observation = minute_bar_observation(
                provisional, origin=ObservationOrigin.REALTIME,
                completeness=DataCompleteness.IN_PROGRESS,
                source="kiwoom-websocket-0B", value_kind=DataValueKind.ACTUAL,
            )
            store.save_minute_bars(
                [provisional], observations=[(bar_observation_key(provisional_observation), provisional_observation)],
            )

            query_value = {
                **base, "open": 101, "high": 120, "low": 95, "close": 118,
                "volume": 80, "trade_value_million_won": 9_876,
                "updated_at": 1_790_000_010.0,
            }
            query_observation = minute_bar_observation(
                query_value, origin=ObservationOrigin.QUERY,
                completeness=DataCompleteness.COMPLETE,
                source="kiwoom-ka10080;trade_value=ohlcv_estimate",
                value_kind=DataValueKind.ESTIMATED,
            )
            key = bar_observation_key(query_observation)
            store.replace_minute_bars([query_value], observations=[(key, query_observation)])

            with store._lock, store._connection() as connection:
                revisions_before_late_events = connection.execute(
                    "SELECT count(*) FROM central_observation_revisions "
                    "WHERE kind='minute_bar' AND subject=? AND observation_key=?",
                    ("005930:KRX", key),
                ).fetchone()[0]

            late_realtime = {
                **base, "open": 100, "high": 999, "low": 1, "close": 2,
                "volume": 50_000, "trade_value_million_won": 900_000,
                "updated_at": 1_790_000_020.0, "operation_id": "sqlite-late-0b",
            }
            late_observation = minute_bar_observation(
                late_realtime, origin=ObservationOrigin.REALTIME,
                completeness=DataCompleteness.IN_PROGRESS,
                source="kiwoom-websocket-0B", value_kind=DataValueKind.ACTUAL,
            )
            store.save_minute_bars(
                [late_realtime], observations=[(key, late_observation)],
            )
            store.finalize_minute_bars([{
                "trading_date": "2026-09-08", "minute": "09:31", "code": "005930",
                "market": "KRX",
                "available_at": datetime(2026, 9, 8, 9, 32, tzinfo=ZoneInfo("Asia/Seoul")).timestamp(),
                "capture_quality": "complete", "finalization_source": "timer",
                "operation_id": "sqlite-late-finalize",
            }])

            final_bar = store.load_minute_bars("005930", "2026-09-08", "KRX")[0]
            with store._lock, store._connection() as connection:
                metadata = connection.execute(
                    "SELECT completeness,origin,source,value_kind FROM central_market_data_observation_meta "
                    "WHERE dataset_kind='minute_bar' AND subject=? AND observation_key=?",
                    ("005930:KRX", key),
                ).fetchone()
                revisions_after_late_events = connection.execute(
                    "SELECT count(*) FROM central_observation_revisions "
                    "WHERE kind='minute_bar' AND subject=? AND observation_key=?",
                    ("005930:KRX", key),
                ).fetchone()[0]
            store.close()

        self.assertEqual((101, 120, 95, 118, 80, 9_876), tuple(
            final_bar[name] for name in ("open", "high", "low", "close", "volume", "trade_value_million_won")
        ))
        self.assertEqual(
            ("complete", "query", "kiwoom-ka10080;trade_value=ohlcv_estimate", "estimated"),
            metadata,
        )
        self.assertEqual(revisions_before_late_events, revisions_after_late_events)

    def test_sqlite_minute_revision_batch_preserves_chain_replay_and_batch_counts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            traced_sql: list[str] = []
            original_connect = store._connect

            def traced_connect():
                connection = original_connect()
                connection.set_trace_callback(traced_sql.append)
                return connection

            store._connect = traced_connect  # type: ignore[method-assign]
            values = []
            observations = []
            for index in range(100):
                value = {
                    "trading_date": "2026-09-08", "minute": f"{9 + index // 60:02d}:{index % 60:02d}",
                    "code": "005930", "market": "KRX", "open": 100,
                    "high": 110 + index, "low": 90, "close": 100 + index,
                    "volume": index, "trade_value_million_won": index,
                    "updated_at": 1_790_000_000.0 + index,
                }
                observation = minute_bar_observation(
                    value, origin=ObservationOrigin.QUERY,
                    completeness=DataCompleteness.COMPLETE,
                    source="kiwoom-ka10080;trade_value=ohlcv_estimate",
                    value_kind=DataValueKind.ESTIMATED,
                )
                values.append(value)
                observations.append((bar_observation_key(observation), observation))

            store.replace_minute_bars(values, observations=observations)
            first_minute_observation = observations[0][1]
            first_minute_value = values[0]
            initial_lookup_queries = [
                sql for sql in traced_sql
                if sql.startswith("WITH requested(kind,subject,observation_key,source_id)")
            ]
            initial_insert_queries = [
                sql for sql in traced_sql
                if sql.startswith("INSERT INTO central_observation_revisions(accepted_sequence")
            ]
            traced_sql.clear()

            chain_values = [
                {**first_minute_value, "close": 101, "updated_at": first_minute_value["updated_at"] + 1},
                {**first_minute_value, "close": 102, "updated_at": first_minute_value["updated_at"] + 2},
                {**first_minute_value, "close": 102, "updated_at": first_minute_value["updated_at"] + 3},
            ]
            chain_observations = []
            for value in chain_values:
                observation = minute_bar_observation(
                    value, origin=ObservationOrigin.QUERY,
                    completeness=DataCompleteness.COMPLETE,
                    source="kiwoom-ka10080;trade_value=ohlcv_estimate",
                    value_kind=DataValueKind.ESTIMATED,
                )
                chain_observations.append((bar_observation_key(observation), observation))
            store.replace_minute_bars(chain_values, observations=chain_observations)
            store.replace_minute_bars([chain_values[-1]], observations=[chain_observations[-1]])

            with store._lock, store._connection() as connection:
                revisions = connection.execute(
                    "SELECT accepted_sequence,revision_id,revision_of,payload_hash "
                    "FROM central_observation_revisions WHERE kind='minute_bar' "
                    "AND subject='005930:KRX' AND observation_key=? AND source_id=? "
                    "ORDER BY accepted_sequence",
                    (chain_observations[0][0], "kiwoom-ka10080;trade_value=ohlcv_estimate"),
                ).fetchall()
            store.close()

        revision_lookup_queries = [
            sql for sql in traced_sql if sql.startswith("WITH requested(kind,subject,observation_key,source_id)")
        ]
        revision_insert_queries = [
            sql for sql in traced_sql if sql.startswith("INSERT INTO central_observation_revisions(accepted_sequence")
        ]
        self.assertEqual(
            3, len(revisions),
        )  # 100 initial rows plus two changed payloads; exact replay adds none.
        self.assertIsNone(revisions[0][2])
        self.assertEqual(revisions[0][1], revisions[1][2])
        self.assertEqual(revisions[1][1], revisions[2][2])
        self.assertLess(revisions[0][0], revisions[1][0])
        self.assertLess(revisions[1][0], revisions[2][0])
        self.assertEqual(2, len(initial_lookup_queries))
        self.assertEqual(3, len(initial_insert_queries))
        self.assertEqual(2, len(revision_lookup_queries))  # one for changed input, one replay
        self.assertEqual(1, len(revision_insert_queries))  # two revisions in a single SQL statement

    def test_sqlite_minute_revision_batch_rolls_back_bar_and_metadata_together(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            value = {
                "trading_date": "2026-09-08", "minute": "09:31", "code": "005930",
                "market": "KRX", "open": 100, "high": 110, "low": 90,
                "close": 105, "volume": 10, "trade_value_million_won": 1,
                "updated_at": 1_790_000_000.0,
            }
            observation = minute_bar_observation(
                value, origin=ObservationOrigin.QUERY,
                completeness=DataCompleteness.COMPLETE,
                source="kiwoom-ka10080;trade_value=ohlcv_estimate",
                value_kind=DataValueKind.ESTIMATED,
            )
            original_batch = _insert_sqlite_observation_revisions_batch

            def fail_after_revision_insert(connection, sources, latest_by_key):
                original_batch(connection, sources, latest_by_key)
                raise RuntimeError("revision batch failed")

            with patch(
                "kiwoom_monitor.central_server.database_market_bars._insert_sqlite_observation_revisions_batch",
                side_effect=fail_after_revision_insert,
            ), self.assertRaisesRegex(RuntimeError, "revision batch failed"):
                store.replace_minute_bars(
                    [value], observations=[(bar_observation_key(observation), observation)],
                )

            self.assertEqual([], store.load_minute_bars("005930", "2026-09-08", "KRX"))
            with store._lock, store._connection() as connection:
                metadata_count = connection.execute(
                    "SELECT count(*) FROM central_market_data_observation_meta "
                    "WHERE dataset_kind='minute_bar' AND subject=?",
                    (observation.subject,),
                ).fetchone()[0]
                revision_count = connection.execute(
                    "SELECT count(*) FROM central_observation_revisions "
                    "WHERE kind='minute_bar' AND subject=?",
                    (observation.subject,),
                ).fetchone()[0]
            store.close()

        self.assertEqual(0, metadata_count)
        self.assertEqual(0, revision_count)

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

    def test_daily_bar_replay_preserves_available_at_until_value_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            original = {
                "trading_date": "2026-09-08", "code": "005930", "market": "KRX",
                "open": 70000, "high": 70100, "low": 69900, "close": 70050,
                "volume": 100, "trade_value_million_won": 7000,
                "updated_at": 1_790_000_000.0,
            }

            def save(value: dict[str, object]) -> None:
                observation = daily_bar_observation(
                    value, completeness=DataCompleteness.COMPLETE,
                )
                store.replace_daily_bars(
                    [value], observations=[(bar_observation_key(observation), observation)],
                )

            save(original)
            replay = {**original, "updated_at": original["updated_at"] + 60}
            save(replay)
            [unchanged] = store.load_daily_bars("005930", "KRX", 1)
            with store._lock, store._connection() as connection:
                metadata = connection.execute(
                    "SELECT available_at FROM central_market_data_observation_meta "
                    "WHERE dataset_kind='daily_bar' AND subject='005930:KRX' "
                    "AND observation_key='2026-09-08'"
                ).fetchone()

            corrected = {**replay, "close": 70060, "updated_at": replay["updated_at"] + 60}
            save(corrected)
            [changed] = store.load_daily_bars("005930", "KRX", 1)
            with store._lock, store._connection() as connection:
                corrected_metadata = connection.execute(
                    "SELECT available_at FROM central_market_data_observation_meta "
                    "WHERE dataset_kind='daily_bar' AND subject='005930:KRX' "
                    "AND observation_key='2026-09-08'"
                ).fetchone()
            store.close()

        self.assertEqual(original["updated_at"], unchanged["updated_at"])
        original_observation = daily_bar_observation(original, completeness=DataCompleteness.COMPLETE)
        corrected_observation = daily_bar_observation(corrected, completeness=DataCompleteness.COMPLETE)
        self.assertEqual(original_observation.metadata.available_at.isoformat(), metadata[0])
        self.assertEqual(corrected_observation.metadata.available_at.isoformat(), corrected_metadata[0])
        self.assertEqual(70060, changed["close"])
        self.assertEqual(corrected["updated_at"], changed["updated_at"])

    def test_minute_bar_metadata_replay_preserves_time_until_state_or_source_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            original = {
                "trading_date": "2026-09-08", "minute": "09:30", "code": "005930",
                "market": "KRX", "open": 100, "high": 110, "low": 90, "close": 105,
                "volume": 10, "trade_value_million_won": 1,
                "updated_at": 1_790_000_000.0,
            }

            def save(value, completeness, source):
                observation = minute_bar_observation(
                    value, origin=ObservationOrigin.QUERY,
                    completeness=completeness, source=source,
                    value_kind=DataValueKind.ESTIMATED,
                )
                store.replace_minute_bars(
                    [value], observations=[(bar_observation_key(observation), observation)],
                )
                return store.load_market_data_metadata(
                    MarketDatasetKind.MINUTE_BAR, "005930:KRX", "2026-09-08T09:30",
                )

            initial = save(original, DataCompleteness.IN_PROGRESS, "source-a")
            replay = {**original, "updated_at": original["updated_at"] + 60}
            unchanged = save(replay, DataCompleteness.IN_PROGRESS, "source-a")
            completed = save(replay, DataCompleteness.COMPLETE, "source-a")
            sourced = save({**replay, "updated_at": replay["updated_at"] + 60},
                           DataCompleteness.COMPLETE, "source-b")
            store.close()

        self.assertEqual(initial.available_at, unchanged.available_at)
        self.assertEqual(replay["updated_at"], completed.available_at.timestamp())
        self.assertEqual(DataCompleteness.COMPLETE, completed.completeness)
        self.assertEqual("source-b", sourced.source)
        self.assertEqual(replay["updated_at"] + 60, sourced.available_at.timestamp())

    def test_daily_bar_batch_keeps_last_duplicate_and_rolls_back_with_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            base = {
                "trading_date": "2026-09-08", "code": "005930", "market": "KRX",
                "open": 70000, "high": 70100, "low": 69900, "close": 70050,
                "volume": 100, "trade_value_million_won": 7000,
                "updated_at": 1_790_000_000.0,
            }
            last = {**base, "close": 70100, "updated_at": base["updated_at"] + 60}
            first_observation = daily_bar_observation(base, completeness=DataCompleteness.COMPLETE)
            last_observation = daily_bar_observation(last, completeness=DataCompleteness.COMPLETE)
            key = bar_observation_key(first_observation)
            store.replace_daily_bars(
                [base, last], observations=[
                    (key, first_observation), (key, last_observation),
                ],
            )
            [saved] = store.load_daily_bars("005930", "KRX", 1)
            with store._lock, store._connection() as connection:
                metadata = connection.execute(
                    "SELECT available_at FROM central_market_data_observation_meta "
                    "WHERE dataset_kind='daily_bar' AND subject=? AND observation_key=?",
                    (last_observation.subject, key),
                ).fetchone()

            invalid = {**base, "trading_date": "2026-09-09"}
            invalid_observation = daily_bar_observation(
                invalid, completeness=DataCompleteness.COMPLETE,
            )
            with self.assertRaisesRegex(ValueError, "observation_key"):
                store.replace_daily_bars(
                    [invalid], observations=[(" ", invalid_observation)],
                )
            rolled_back = store.load_daily_bars("005930", "KRX", 10)
            store.close()

        self.assertEqual(last["close"], saved["close"])
        self.assertEqual(last["updated_at"], saved["updated_at"])
        self.assertEqual(last_observation.metadata.available_at.isoformat(), metadata[0])
        self.assertEqual(["2026-09-08"], [row["trading_date"] for row in rolled_back])

    def test_postgres_daily_bar_and_metadata_use_one_statement_each_for_900_rows(self) -> None:
        class Cursor:
            def __init__(self) -> None:
                self.statements: list[tuple[str, tuple[object, ...]]] = []

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, sql, parameters):
                self.statements.append((sql, tuple(parameters)))
                self.last_parameters = tuple(parameters)
                return self

            def fetchall(self):
                return [tuple(self.last_parameters[index:index + 3])
                        for index in range(0, len(self.last_parameters), 10)]

        class Connection:
            def __init__(self, cursor) -> None:
                self._cursor = cursor
                self.commits = 0
                self.rollbacks = 0
                self.closed = False

            def cursor(self):
                return self._cursor

            def commit(self) -> None:
                self.commits += 1

            def rollback(self) -> None:
                self.rollbacks += 1

            def close(self) -> None:
                self.closed = True

        values = []
        observations = []
        for index in range(900):
            value = {
                "trading_date": "2026-09-08", "code": f"{index:06d}", "market": "KRX",
                "open": 100, "high": 110, "low": 90, "close": 105,
                "volume": index, "trade_value_million_won": index,
                "updated_at": 1_790_000_000.0,
            }
            observation = daily_bar_observation(value, completeness=DataCompleteness.COMPLETE)
            values.append(value)
            observations.append((bar_observation_key(observation), observation))
        cursor = Cursor()
        connection = Connection(cursor)
        store = PostgresQueryStore("postgresql://unused")
        store._connect = lambda: connection  # type: ignore[method-assign]

        store.replace_daily_bars(values, observations=observations)

        self.assertEqual(2, len(cursor.statements))
        self.assertIn("INSERT INTO central_daily_bars", cursor.statements[0][0])
        self.assertEqual(9_000, len(cursor.statements[0][1]))
        self.assertIn("INSERT INTO central_market_data_observation_meta", cursor.statements[1][0])
        self.assertEqual(10_800, len(cursor.statements[1][1]))
        self.assertEqual(1, connection.commits)
        self.assertEqual(0, connection.rollbacks)
        self.assertTrue(connection.closed)

    def test_sqlite_daily_bar_and_metadata_use_bounded_batches_for_900_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            traced_sql: list[str] = []
            original_connect = store._connect

            def traced_connect():
                connection = original_connect()
                connection.set_trace_callback(traced_sql.append)
                return connection

            store._connect = traced_connect  # type: ignore[method-assign]
            values = []
            observations = []
            for index in range(900):
                value = {
                    "trading_date": "2026-09-08", "code": f"{index:06d}", "market": "KRX",
                    "open": 100, "high": 110, "low": 90, "close": 105,
                    "volume": index, "trade_value_million_won": index,
                    "updated_at": 1_790_000_000.0,
                }
                observation = daily_bar_observation(
                    value, completeness=DataCompleteness.COMPLETE,
                )
                values.append(value)
                observations.append((bar_observation_key(observation), observation))

            store.replace_daily_bars(values, observations=observations)
            store.close()

        canonical = [sql for sql in traced_sql if sql.startswith("INSERT INTO central_daily_bars")]
        metadata = [
            sql for sql in traced_sql
            if sql.startswith("INSERT INTO central_market_data_observation_meta")
        ]
        self.assertEqual(12, len(canonical))
        self.assertEqual(12, len(metadata))

    def test_sqlite_minute_bar_and_metadata_use_bounded_batches_and_keep_last_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            traced_sql: list[str] = []
            original_connect = store._connect

            def traced_connect():
                connection = original_connect()
                connection.set_trace_callback(traced_sql.append)
                return connection

            store._connect = traced_connect  # type: ignore[method-assign]
            values = []
            observations = []
            for index in range(900):
                minute = f"{index // 60:02d}:{index % 60:02d}"
                value = {
                    "trading_date": "2026-09-08", "minute": minute,
                    "code": "005930", "market": "KRX", "open": 100,
                    "high": 110, "low": 90, "close": 105,
                    "volume": index, "trade_value_million_won": index,
                    "updated_at": 1_790_000_000.0 + index,
                }
                observation = minute_bar_observation(
                    value, origin=ObservationOrigin.QUERY,
                    completeness=DataCompleteness.COMPLETE,
                    source="kiwoom-ka10080;trade_value=ohlcv_estimate",
                    value_kind=DataValueKind.ESTIMATED,
                )
                values.append(value)
                observations.append((bar_observation_key(observation), observation))

            duplicate = {**values[0], "close": 106, "updated_at": values[0]["updated_at"] + 900}
            duplicate_observation = minute_bar_observation(
                duplicate, origin=ObservationOrigin.QUERY,
                completeness=DataCompleteness.COMPLETE,
                source="kiwoom-ka10080;trade_value=ohlcv_estimate",
                value_kind=DataValueKind.ESTIMATED,
            )
            values.append(duplicate)
            observations.append((bar_observation_key(duplicate_observation), duplicate_observation))

            store.replace_minute_bars(values, observations=observations)
            [saved] = store.load_minute_bars("005930", "2026-09-08", "KRX")[:1]
            with store._lock, store._connection() as connection:
                metadata = connection.execute(
                    "SELECT available_at FROM central_market_data_observation_meta "
                    "WHERE dataset_kind='minute_bar' AND subject=? AND observation_key=?",
                    (duplicate_observation.subject, bar_observation_key(duplicate_observation)),
                ).fetchone()
            invalid = {**values[1], "minute": "15:00"}
            invalid_observation = minute_bar_observation(
                invalid, origin=ObservationOrigin.QUERY,
                completeness=DataCompleteness.COMPLETE,
                source="kiwoom-ka10080;trade_value=ohlcv_estimate",
                value_kind=DataValueKind.ESTIMATED,
            )
            canonical_statement_count = sum(
                sql.startswith("INSERT INTO central_minute_bars") for sql in traced_sql
            )
            metadata_statement_count = sum(
                sql.startswith("INSERT INTO central_market_data_observation_meta")
                for sql in traced_sql
            )
            traced_sql.clear()
            with self.assertRaisesRegex(ValueError, "observation_key"):
                store.replace_minute_bars(
                    [invalid], observations=[(" ", invalid_observation)],
                )
            after_rollback = store.load_minute_bars("005930", "2026-09-08", "KRX")
            store.close()

        self.assertEqual(13, canonical_statement_count)
        self.assertEqual(12, metadata_statement_count)
        self.assertEqual(106, saved["close"])
        self.assertEqual(duplicate_observation.metadata.available_at.isoformat(), metadata[0])
        self.assertFalse(any(row["minute"] == "15:00" for row in after_rollback))

    def test_postgres_minute_bar_and_metadata_use_one_multirow_statement_each_for_900_rows(self) -> None:
        class Cursor:
            def __init__(self) -> None:
                self.statements: list[tuple[str, tuple[object, ...]]] = []

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, sql, parameters=()):
                self.statements.append((sql, tuple(parameters or ())))
                self.last_parameters = tuple(parameters or ())
                return self

            def fetchall(self):
                return [tuple(self.last_parameters[index:index + 4])
                        for index in range(0, len(self.last_parameters), 11)]

        class Connection:
            def __init__(self, cursor) -> None:
                self._cursor = cursor
                self.commits = 0
                self.rollbacks = 0
                self.closed = False

            def cursor(self):
                return self._cursor

            def commit(self) -> None:
                self.commits += 1

            def rollback(self) -> None:
                self.rollbacks += 1

            def close(self) -> None:
                self.closed = True

        values = []
        observations = []
        for index in range(900):
            minute = f"{index // 60:02d}:{index % 60:02d}"
            value = {
                "trading_date": "2026-09-08", "minute": minute,
                "code": "005930", "market": "KRX", "open": 100,
                "high": 110, "low": 90, "close": 105,
                "volume": index, "trade_value_million_won": index,
                "updated_at": 1_790_000_000.0 + index,
            }
            observation = minute_bar_observation(
                value, origin=ObservationOrigin.QUERY,
                completeness=DataCompleteness.COMPLETE,
                source="kiwoom-ka10080;trade_value=ohlcv_estimate",
                value_kind=DataValueKind.ESTIMATED,
            )
            values.append(value)
            observations.append((bar_observation_key(observation), observation))
        values.append({**values[0], "close": 106, "updated_at": values[0]["updated_at"] + 900})
        observations.append((observations[0][0], observations[0][1]))

        cursor = Cursor()
        connection = Connection(cursor)
        store = PostgresQueryStore("postgresql://unused", observation_history_enabled=False)
        store._connect = lambda: connection  # type: ignore[method-assign]
        store.replace_minute_bars(values, observations=observations)

        canonical = [(sql, params) for sql, params in cursor.statements if sql.startswith("INSERT INTO central_minute_bars")]
        metadata = [(sql, params) for sql, params in cursor.statements
                    if sql.startswith("INSERT INTO central_market_data_observation_meta")]
        self.assertEqual(2, len(canonical))
        self.assertEqual(1, len(metadata))
        self.assertEqual(11, len(canonical[0][1]))
        self.assertEqual(900 * 11, len(canonical[1][1]))
        self.assertEqual(900 * 12, len(metadata[0][1]))
        self.assertEqual(900, canonical[1][0].count("),(" ) + 1)
        self.assertEqual(900, metadata[0][0].count("),(" ) + 1)
        self.assertEqual(1, connection.commits)
        self.assertEqual(0, connection.rollbacks)
        self.assertTrue(connection.closed)

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

    def test_dataset_snapshot_batch_is_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            with self.assertRaises(TypeError):
                store.save_dataset_snapshots([
                    ("program_flow", "005930", "first", {"rows": []}, None),
                    ("program_flow", "000660", "second", {"invalid": {1}}, None),
                ])
            values = store.load_dataset_snapshots("program_flow")
            store.close()

        self.assertEqual([], values)

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
            with patch("kiwoom_monitor.central_server.database_documents.time", return_value=10.0):
                store.upsert_documents("news_article", [value])
            with patch("kiwoom_monitor.central_server.database_documents.time", return_value=20.0):
                store.upsert_documents("news_article", [value])
            unchanged = store.load_documents("news_article")[0]
            with patch("kiwoom_monitor.central_server.database_documents.time", return_value=30.0):
                store.upsert_documents("news_article", [{
                    **value, "document": {"title": "changed"},
                }])
            changed = store.load_documents("news_article")[0]
            store.close()

        self.assertEqual(10.0, unchanged["updated_at"])
        self.assertEqual(30.0, changed["updated_at"])

    def test_postgres_query_cache_save_reports_slow_phase_timings(self) -> None:
        class Cursor:
            statements: list[tuple[str, tuple[object, ...]]] = []

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, sql, parameters) -> None:
                self.statements.append((sql, parameters))

        class Connection:
            def __init__(self, cursor) -> None:
                self._cursor = cursor
                self.committed = False
                self.closed = False

            def cursor(self):
                return self._cursor

            def commit(self) -> None:
                self.committed = True

            def close(self) -> None:
                self.closed = True

        cursor = Cursor()
        connection = Connection(cursor)
        store = PostgresQueryStore("postgresql://unused")
        store._connect = lambda: connection  # type: ignore[method-assign]
        with patch(
            "kiwoom_monitor.central_server.database_query_cache.monotonic",
            side_effect=[index * 0.25 for index in range(12)],
        ), patch("kiwoom_monitor.central_server.database_query_cache.logger.warning") as warning:
            store.save_query("cache-key", "ka10081", time.time() + 30, StoredQuery({"rows": [1]}, False, ""))

        self.assertEqual(2, len(cursor.statements))
        self.assertIn("INSERT INTO central_api_query_cache", cursor.statements[0][0])
        self.assertIn("DELETE FROM central_api_query_cache", cursor.statements[1][0])
        self.assertTrue(connection.committed)
        self.assertTrue(connection.closed)
        warning.assert_called_once()
        self.assertEqual(
            ("ka10081", 250, 250, 250, 250, 250, 2750),
            warning.call_args.args[1:],
        )

    def test_postgres_minute_bar_save_reports_slow_phase_timings(self) -> None:
        class Cursor:
            def __init__(self) -> None:
                self.sql = ""

            def execute(self, sql, _parameters=()) -> None:
                if sql.startswith("INSERT INTO central_minute_bars"):
                    self.sql = sql
                else:
                    self.assertion_lock_sql = sql

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        class Connection:
            def __init__(self, cursor) -> None:
                self._cursor = cursor
                self.committed = False
                self.rolled_back = False
                self.closed = False

            def cursor(self):
                return self._cursor

            def commit(self) -> None:
                self.committed = True

            def rollback(self) -> None:
                self.rolled_back = True

            def close(self) -> None:
                self.closed = True

        cursor = Cursor()
        connection = Connection(cursor)
        store = PostgresQueryStore("postgresql://unused")
        store._connect = lambda: connection  # type: ignore[method-assign]
        with patch(
            "kiwoom_monitor.central_server.database_market_bars.monotonic",
            side_effect=[index * 0.1 for index in range(14)],
        ), patch(
            "kiwoom_monitor.central_server.database_market_bars.bar_value_rows",
            return_value=[(
                "2026-09-25", "09:30", "005930", "KRX", 100, 100, 100,
                100, 1, 1, 1_790_000_000.0,
            )],
        ), patch("kiwoom_monitor.central_server.database_market_bars.logger.warning") as warning:
            store.replace_minute_bars([{"trading_date": "2026-09-25", "minute": "09:30", "code": "005930"}])

        self.assertIn("INSERT INTO central_minute_bars", cursor.sql)
        self.assertIn("IS DISTINCT FROM", cursor.sql)
        self.assertIn("central_minute_bars.trade_value_million_won", cursor.sql)
        self.assertNotIn("central_minute_bars.updated_at", cursor.sql)
        self.assertTrue(connection.committed)
        self.assertFalse(connection.rolled_back)
        self.assertTrue(connection.closed)
        warning.assert_called_once()
        self.assertEqual(
            ("minute", 1, 0, 100, 100, 100, 100,
             0, 0, 0, 0, 0, 0, 0, 0, 0,
             100, 100, 1300),
            warning.call_args.args[1:],
        )

    def test_commit_diagnostics_are_capture_gated_and_wal_permission_failure_is_nonfatal(self) -> None:
        class Cursor:
            def __init__(self) -> None:
                self.statements: list[str] = []

            def execute(self, sql, _parameters=None):
                self.statements.append(sql)
                if sql == "SET LOCAL track_wal_io_timing TO on":
                    raise RuntimeError("permission denied")
                self.last_parameters = tuple(_parameters or ())
                return self

            def fetchall(self):
                return [tuple(self.last_parameters[:4])]

            def fetchone(self):
                return ("off",)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def executemany(self, *_args) -> None:
                return None

        class Connection:
            def __init__(self, cursor) -> None:
                from types import SimpleNamespace
                self._cursor = cursor
                self.info = SimpleNamespace(backend_pid=4321)
                self.committed = False
                self.rolled_back = False
                self.closed = False

            def cursor(self):
                return self._cursor

            def commit(self) -> None:
                self.committed = True

            def rollback(self) -> None:
                self.rolled_back = True

            def close(self) -> None:
                self.closed = True

        cursor = Cursor()
        connection = Connection(cursor)
        store = PostgresQueryStore("postgresql://unused")
        store._connect = lambda: connection  # type: ignore[method-assign]
        probe_thread = Mock()
        store._observation_history_enabled = False
        value = {
            "trading_date": "2026-09-25", "minute": "09:30", "code": "005930",
            "market": "KRX", "open": 1, "high": 2, "low": 1, "close": 2,
            "volume": 10, "trade_value_million_won": 2, "updated_at": time.time(),
        }
        observation = minute_bar_observation(
            value, origin=ObservationOrigin.QUERY,
            completeness=DataCompleteness.COMPLETE, source="kiwoom-ka10080",
            value_kind=DataValueKind.ESTIMATED,
        )
        with patch("kiwoom_monitor.central_server.diagnostic_metrics.refresh_capture_state",
                   return_value={"enabled": True}), \
                patch("kiwoom_monitor.central_server.database_market_bars.Thread", return_value=probe_thread), \
                patch("kiwoom_monitor.central_server.postgres_access.Thread", return_value=probe_thread), \
                patch("kiwoom_monitor.central_server.diagnostic_metrics.record_market_bar_save") as record:
            store.replace_minute_bars(
                [value], observations=[(bar_observation_key(observation), observation)],
            )

        self.assertIn("SAVEPOINT diagnostic_wal_timing", cursor.statements)
        self.assertIn("ROLLBACK TO SAVEPOINT diagnostic_wal_timing", cursor.statements)
        self.assertIn("RELEASE SAVEPOINT diagnostic_wal_timing", cursor.statements)
        self.assertTrue(connection.committed)
        self.assertFalse(connection.rolled_back)
        self.assertTrue(connection.closed)
        self.assertEqual(3, probe_thread.start.call_count)  # bar, metadata, COMMIT windows
        self.assertEqual(1, len(record.call_args.kwargs["metadata_statement_diagnostics"]))
        probe_thread.join.assert_not_called()
        self.assertFalse(record.call_args.kwargs["wal_timing_for_commit"])
        self.assertEqual("RuntimeError", record.call_args.kwargs["wal_timing_error"])

        inactive_cursor = Cursor()
        inactive_connection = Connection(inactive_cursor)
        store._connect = lambda: inactive_connection  # type: ignore[method-assign]
        with patch("kiwoom_monitor.central_server.diagnostic_metrics.refresh_capture_state",
                   return_value={"enabled": False}), \
                patch("kiwoom_monitor.central_server.database_market_bars.Thread") as inactive_thread, \
                patch("kiwoom_monitor.central_server.database_market_bars.Event") as inactive_event:
            store.replace_minute_bars([value])
        self.assertTrue(inactive_connection.committed)
        self.assertNotIn("SAVEPOINT diagnostic_wal_timing", inactive_cursor.statements)
        inactive_thread.assert_not_called()
        inactive_event.assert_not_called()

        broken_probe_cursor = Cursor()
        broken_probe_connection = Connection(broken_probe_cursor)
        store._connect = lambda: broken_probe_connection  # type: ignore[method-assign]
        with patch("kiwoom_monitor.central_server.diagnostic_metrics.refresh_capture_state",
                   return_value={"enabled": True}), \
                patch("kiwoom_monitor.central_server.database_market_bars.Thread") as broken_thread, \
                patch("kiwoom_monitor.central_server.diagnostic_metrics.record_market_bar_save") as record:
            broken_thread.return_value.start.side_effect = RuntimeError("probe unavailable")
            store.replace_minute_bars([value])
        self.assertTrue(broken_probe_connection.committed)
        self.assertFalse(broken_probe_connection.rolled_back)
        self.assertEqual(["RuntimeError"], record.call_args.kwargs["commit_probe_errors"])

    def test_minute_metadata_diagnostic_gate_only_skips_metadata(self) -> None:
        class Cursor:
            def __init__(self) -> None:
                self.statements: list[str] = []

            def execute(self, sql, _parameters=()):
                self.statements.append(sql)
                self.last_parameters = tuple(_parameters or ())
                return self

            def fetchall(self):
                return [tuple(self.last_parameters[:4])]

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        class Connection:
            def __init__(self) -> None:
                self.query = Cursor()
                self.commits = 0

            def cursor(self):
                return self.query

            def commit(self) -> None:
                self.commits += 1

            def rollback(self) -> None:
                raise AssertionError("unexpected rollback")

            def close(self) -> None:
                pass

        store = PostgresQueryStore("postgresql://unused")
        connections: list[Connection] = []

        def connect():
            connection = Connection()
            connections.append(connection)
            return connection

        store._connect = connect  # type: ignore[method-assign]
        store._observation_history_enabled = False
        value = {
            "trading_date": "2026-09-25", "minute": "09:30", "code": "005930",
            "market": "KRX", "open": 1, "high": 2, "low": 1, "close": 2,
            "volume": 10, "trade_value_million_won": 2, "updated_at": time.time(),
        }
        observation = minute_bar_observation(
            value, origin=ObservationOrigin.QUERY,
            completeness=DataCompleteness.COMPLETE, source="kiwoom-ka10080",
            value_kind=DataValueKind.ESTIMATED,
        )
        rows = [(bar_observation_key(observation), observation)]
        with patch("kiwoom_monitor.central_server.diagnostic_workloads.is_paused",
                   side_effect=[False, True, False]), \
                patch("kiwoom_monitor.central_server.database_market_bars._save_postgres_metadata") as save_metadata, \
                patch("kiwoom_monitor.central_server.diagnostic_metrics.record_market_bar_save") as record:
            for _ in range(3):
                store.replace_minute_bars([value], observations=rows)

        self.assertEqual(2, save_metadata.call_count)
        self.assertEqual([0, 1, 0], [call.kwargs["metadata_suppressed_rows"]
                                    for call in record.call_args_list])
        self.assertEqual([1, 1, 1], [connection.commits for connection in connections])
        self.assertTrue(all(any("INSERT INTO central_minute_bars" in sql
                                for sql in connection.query.statements)
                            for connection in connections))

    def test_postgres_minute_bar_save_rolls_back_and_closes_on_error(self) -> None:
        class Cursor:
            def execute(self, *_args) -> None:
                return None

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, sql, _parameters=()) -> None:
                if sql.startswith("INSERT INTO central_minute_bars"):
                    raise RuntimeError("write failed")

        class Connection:
            def __init__(self) -> None:
                self.committed = False
                self.rolled_back = False
                self.closed = False

            def cursor(self):
                return Cursor()

            def commit(self) -> None:
                self.committed = True

            def rollback(self) -> None:
                self.rolled_back = True

            def close(self) -> None:
                self.closed = True

        connection = Connection()
        store = PostgresQueryStore("postgresql://unused")
        store._connect = lambda: connection  # type: ignore[method-assign]
        with patch(
            "kiwoom_monitor.central_server.database_market_bars.bar_value_rows",
            return_value=[("2026-09-25", "09:30", "005930", "KRX", 1, 1, 1, 1, 1, 1, 1)],
        ):
            with self.assertRaisesRegex(RuntimeError, "write failed"):
                store.replace_minute_bars([{"trading_date": "2026-09-25", "minute": "09:30", "code": "005930"}])

        self.assertFalse(connection.committed)
        self.assertTrue(connection.rolled_back)
        self.assertTrue(connection.closed)

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

    def test_postgres_latest_market_cap_reads_persisted_trade_without_age_filter(self) -> None:
        class Cursor:
            sql = ""
            parameters = ()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, sql, parameters) -> None:
                self.sql = sql
                self.parameters = parameters

            def fetchall(self):
                return [("005930", 1.0, {
                    "type": "trade", "payload": {"market_cap_eok": 4_321_000},
                })]

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

        values = store.load_latest_market_caps(["005930"])

        self.assertIn("event_type='trade'", cursor.sql)
        self.assertNotIn("received_at>", cursor.sql)
        self.assertEqual((["005930"],), cursor.parameters)
        self.assertEqual(4_321_000, values[0]["market_cap_eok"])

    def test_postgres_vi_duplicate_ignores_either_unique_identity(self) -> None:
        class Cursor:
            rowcount = 0
            sql = ""

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, sql, _parameters) -> None:
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
        store.append_vi_events([{
            "event_id": "same-event", "event_key": "same-key", "stock_code": "005930",
            "event_kind": "TRIGGER", "vi_type": "STATIC", "effective_at": "2026-09-21T10:00:00",
            "received_at": 1.0, "available_at": 1.0, "price": 1000, "direction": "+",
            "trigger_count": 1, "exchange": "KRX", "source": "test", "document": {},
        }])

        self.assertIn("ON CONFLICT DO NOTHING", cursor.sql)
        self.assertNotIn("ON CONFLICT(event_key)", cursor.sql)

    def test_external_market_bars_skip_unchanged_values_and_keep_null_safe_corrections(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            base = {"provider": "yahoo_delayed", "instrument": "NASDAQ_FUTURES",
                    "contract": "MNQU26.CME", "timeframe": "5m", "open": 25000.0,
                    "high": 25010.0, "low": 24990.0, "close": 25005.0, "volume": 10.0,
                    "updated_at": 100.0}
            store.save_external_bars([{**base, "bar_time": "2026-09-10T00:00:00Z"}])
            observed = {**base, "bar_time": "2026-09-10T00:05:00Z", "close": 25020.0}
            store.save_external_bars([observed])
            store.save_external_bars([{**observed, "updated_at": 200.0}])
            correction = {**observed, "close": 25021.0, "updated_at": 300.0}
            store.save_external_bars([correction])
            store.save_external_bars([{**correction, "updated_at": 400.0}])
            null_correction = {**correction, "volume": None, "updated_at": 500.0}
            store.save_external_bars([null_correction])
            store.save_external_bars([{**null_correction, "updated_at": 600.0}])
            rows = store.load_external_bars("NASDAQ_FUTURES", "5m")
            store.close()
        self.assertEqual(2, len(rows))
        self.assertEqual(25021.0, rows[-1]["close"])
        self.assertIsNone(rows[-1]["volume"])
        self.assertEqual(500.0, rows[-1]["updated_at"])


if __name__ == "__main__":
    unittest.main()
