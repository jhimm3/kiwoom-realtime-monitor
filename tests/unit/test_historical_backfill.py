from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from kiwoom_monitor.infrastructure.historical_backfill import (
    ArticleFetchAttempt,
    ArticlePublicationResult,
    NAVER_HISTORICAL_SEARCH_PROVIDER,
    NAVER_STOCK_NEWS_PROVIDER,
    claim_news_backfill_job,
    finish_news_backfill_job,
    import_daishin_backfill_ndjson,
    parse_naver_historical_search_page,
    parse_naver_stock_news_page,
    release_news_backfill_job,
    seed_news_backfill_jobs,
    parse_article_publication_html,
    store_article_publication_result,
    store_naver_historical_search_page,
    store_naver_stock_news_page,
    store_daishin_probe_payload,
)


class HistoricalBackfillTest(unittest.TestCase):
    def test_news_job_finish_waits_for_a_transient_database_writer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / "candidate.sqlite3"
            output = root / "output.sqlite3"
            with closing(sqlite3.connect(candidate)) as connection, connection:
                connection.executescript(
                    "CREATE TABLE candidate_days(dt TEXT, code TEXT);"
                    "CREATE TABLE stocks(code TEXT, name TEXT);"
                    "INSERT INTO stocks VALUES('005930','삼성전자');"
                    "INSERT INTO candidate_days VALUES('2026-09-22','005930');"
                )
            seed_news_backfill_jobs(candidate, output)
            job = claim_news_backfill_job(output)
            assert job is not None

            locked = threading.Event()
            release = threading.Event()

            def hold_writer_lock() -> None:
                with closing(sqlite3.connect(output)) as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute(
                        "UPDATE news_backfill_jobs SET last_error='other_writer'"
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
                finish_news_backfill_job(output, job, state="complete")
            finally:
                release.set()
                timer.cancel()
                writer.join(timeout=2)

            with closing(sqlite3.connect(output)) as connection:
                state = connection.execute(
                    "SELECT state FROM news_backfill_jobs"
                ).fetchone()[0]
            self.assertEqual("complete", state)

    def test_seeds_and_resumes_candidate_news_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / "candidate.sqlite3"
            with closing(sqlite3.connect(candidate)) as connection:
                connection.executescript(
                    "CREATE TABLE candidate_days(dt TEXT, code TEXT);"
                    "CREATE TABLE stocks(code TEXT, name TEXT);"
                    "CREATE TABLE stock_aliases(stock_code TEXT, stock_name TEXT, "
                    "valid_from TEXT, valid_to TEXT, source TEXT, source_ref TEXT);"
                    "INSERT INTO stocks VALUES('005930','삼성전자'),('004770','써니전자');"
                    "INSERT INTO stock_aliases VALUES"
                    "('004770','옛써니','2019-01-01','2020-01-02','KIND','D001'),"
                    "('004770','새써니','2020-01-03',NULL,'KIND','D001');"
                    "INSERT INTO candidate_days VALUES"
                    "('2020-01-02','004770'),('2020-01-03','005930');"
                )
            output = root / "output.sqlite3"
            self.assertEqual(3, seed_news_backfill_jobs(candidate, output))
            self.assertEqual(0, seed_news_backfill_jobs(candidate, output))
            job = claim_news_backfill_job(output)
            self.assertIsNotNone(job)
            assert job is not None
            self.assertEqual(("005930", "2020-01-03", "삼성전자", "stocks.current", 1), (
                job.code, job.target_date, job.query_text, job.name_source, job.attempts,
            ))
            finish_news_backfill_job(
                output, job, state="complete", pages_observed=2,
                items_observed=12, usable_articles=7, unreadable_articles=3,
                missing_time_articles=2,
            )
            with closing(sqlite3.connect(output)) as connection:
                state = connection.execute(
                    "SELECT state, usable_articles, unreadable_articles, "
                    "missing_time_articles FROM news_backfill_jobs WHERE code='005930'"
                ).fetchone()
                alias_rows = connection.execute(
                    "SELECT query_text, name_source, name_source_ref "
                    "FROM news_backfill_jobs WHERE code='004770' ORDER BY query_text"
                ).fetchall()
        self.assertEqual(("complete", 7, 3, 2), state)
        self.assertEqual(
            [
                ("새써니", "name_transition_window:KIND", "D001"),
                ("옛써니", "KIND", "D001"),
            ],
            alias_rows,
        )

    def test_claim_recovers_only_stale_running_news_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / "candidate.sqlite3"
            with closing(sqlite3.connect(candidate)) as connection, connection:
                connection.executescript(
                    "CREATE TABLE candidate_days(dt TEXT, code TEXT);"
                    "CREATE TABLE stocks(code TEXT, name TEXT);"
                    "INSERT INTO stocks VALUES('005930','삼성전자'),('000660','SK하이닉스');"
                    "INSERT INTO candidate_days VALUES"
                    "('2020-01-02','005930'),('2020-01-02','000660');"
                )
            output = root / "output.sqlite3"
            seed_news_backfill_jobs(candidate, output)
            with closing(sqlite3.connect(output)) as connection, connection:
                connection.execute(
                    "UPDATE news_backfill_jobs SET state='running',attempts=1,updated_at=? "
                    "WHERE code='000660'", ("2020-01-01T00:00:00+00:00",),
                )
                connection.execute(
                    "UPDATE news_backfill_jobs SET state='running',attempts=1,updated_at=? "
                    "WHERE code='005930'", (datetime.now(UTC).isoformat(),),
                )
            job = claim_news_backfill_job(output)
            self.assertIsNotNone(job)
            assert job is not None
            self.assertEqual("000660", job.code)
            self.assertEqual(2, job.attempts)
            with closing(sqlite3.connect(output)) as connection:
                active = connection.execute(
                    "SELECT state FROM news_backfill_jobs WHERE code='005930'"
                ).fetchone()[0]
            self.assertEqual("running", active)

    def test_releases_collector_environment_failure_without_consuming_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / "candidate.sqlite3"
            with closing(sqlite3.connect(candidate)) as connection, connection:
                connection.executescript(
                    "CREATE TABLE candidate_days(dt TEXT, code TEXT);"
                    "CREATE TABLE stocks(code TEXT, name TEXT);"
                    "INSERT INTO stocks VALUES('005930','삼성전자');"
                    "INSERT INTO candidate_days VALUES('2020-01-02','005930');"
                )
            output = root / "output.sqlite3"
            seed_news_backfill_jobs(candidate, output)
            job = claim_news_backfill_job(output)
            assert job is not None

            release_news_backfill_job(
                output, job, error="collector_unavailable: WinError 10013",
            )

            with closing(sqlite3.connect(output)) as connection:
                state = connection.execute(
                    "SELECT state,attempts,last_error FROM news_backfill_jobs"
                ).fetchone()
            self.assertEqual(
                ("pending", 0, "collector_unavailable: WinError 10013"), state,
            )

    def test_extracts_original_publication_time_with_source_and_precision(self) -> None:
        document = """
        <html><head>
          <title>써니전자 관련 기사 - 매체</title>
          <script type="application/ld+json">
            {"@type":"NewsArticle","datePublished":"2020-01-02T10:31:22+09:00"}
          </script>
        </head></html>
        """
        value, precision, source, raw = parse_article_publication_html(
            document, expected_title="써니전자 관련 기사"
        )
        self.assertEqual("2020-01-02T10:31:22+09:00", value)
        self.assertEqual("second", precision)
        self.assertEqual("json_ld:datePublished", source)
        self.assertEqual("2020-01-02T10:31:22+09:00", raw)

    def test_does_not_turn_date_only_metadata_into_midnight(self) -> None:
        document = '<meta property="article:published_time" content="2020-01-02">'
        self.assertEqual(("", "", "", "time_not_found"), parse_article_publication_html(document))

    def test_parses_structured_historical_search_bootstrap(self) -> None:
        article = {
            "content": "<mark>써니전자</mark> 기사 요약",
            "contentHref": "https://example.test/original/1",
            "sourceProfile": {
                "title": "연합뉴스",
                "subTexts": [{
                    "text": "네이버뉴스",
                    "textHref": "https://n.news.naver.com/mnews/article/001/0000000001?sid=101",
                }],
            },
            "title": "<mark>써니전자</mark> 관련 기사",
        }
        bootstrap = {"body": {"props": {"children": [{"props": article}]}}}
        payload = {"collection": [{
            "script": "entry.bootstrap(document.getElementById(\"root\"), "
            + json.dumps(bootstrap, ensure_ascii=False) + ");",
            "html": "",
        }]}

        page = parse_naver_historical_search_page(
            "004770", "써니전자", "2020-01-02", 1, payload,
            observed_at=datetime(2026, 9, 22, 5, 0, tzinfo=UTC),
        )

        self.assertTrue(page.has_structured_news)
        self.assertEqual(1, len(page.items))
        self.assertEqual("NAVER:001:0000000001", page.items[0].article_key)
        self.assertEqual("써니전자 관련 기사", page.items[0].title)
        self.assertEqual("써니전자 기사 요약", page.items[0].summary)
        self.assertEqual("연합뉴스", page.items[0].office_name)
        self.assertEqual("https://example.test/original/1", page.items[0].original_url)
        self.assertIn("n.news.naver.com", page.items[0].portal_url)

    def test_parses_primary_and_related_articles_with_minute_precision(self) -> None:
        payload = {
            "total": 123,
            "clusters": [{
                "itemTotal": "2",
                "items": [
                    {
                        "officeId": "001", "articleId": "0000000001", "officeName": "매체",
                        "datetime": "202609221305", "title": "대표 기사", "body": "요약",
                        "imageOriginLink": "https://example.test/image.jpg",
                    },
                    {
                        "officeId": "002", "articleId": "0000000002", "officeName": "관련 매체",
                        "datetime": "202609221259", "title": "관련 기사", "body": "",
                    },
                ],
            }],
        }
        page = parse_naver_stock_news_page(
            "A005930", 1, 20, payload, observed_at=datetime(2026, 9, 22, 4, 6, tzinfo=UTC)
        )

        self.assertEqual("005930", page.code)
        self.assertEqual(2, len(page.items))
        self.assertEqual("2026-09-22T13:05+09:00", page.items[0].published_at)
        self.assertEqual(0, page.items[0].related_index)
        self.assertEqual(1, page.items[1].related_index)
        self.assertEqual("https://n.news.naver.com/article/001/0000000001", page.items[0].article_url)
        self.assertEqual(123, page.reported_total)

    def test_stores_raw_observation_and_deduplicates_article(self) -> None:
        payload = {
            "total": 1,
            "clusters": [{"items": [{
                "officeId": "001", "articleId": "0000000001", "officeName": "매체",
                "datetime": "202609221305", "title": "기사", "body": "요약",
            }]}],
        }
        first = parse_naver_stock_news_page(
            "005930", 2, 20, payload, observed_at=datetime(2026, 9, 22, 4, 6, tzinfo=UTC)
        )
        second = parse_naver_stock_news_page(
            "005930", 1, 20, payload, observed_at=datetime(2026, 9, 22, 4, 7, tzinfo=UTC)
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "probe.sqlite3"
            store_naver_stock_news_page(path, first)
            store_naver_stock_news_page(path, second)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(2, connection.execute("SELECT COUNT(*) FROM source_pages").fetchone()[0])
                self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM news_articles").fetchone()[0])
                source = connection.execute(
                    "SELECT published_at_source FROM news_articles"
                ).fetchone()[0]
                relation = connection.execute(
                    "SELECT provider, page FROM news_article_symbols"
                ).fetchone()
                raw = connection.execute("SELECT payload_json FROM source_pages LIMIT 1").fetchone()[0]
        self.assertEqual((NAVER_STOCK_NEWS_PROVIDER, 1), relation)
        self.assertEqual("naver_stock_api:datetime", source)
        self.assertEqual(payload, json.loads(raw))

    def test_rejects_response_without_cluster_contract(self) -> None:
        with self.assertRaisesRegex(ValueError, "clusters"):
            parse_naver_stock_news_page("005930", 1, 20, {"total": 0})

    def test_accepts_six_character_alphanumeric_short_code(self) -> None:
        page = parse_naver_stock_news_page(
            "0011A0", 1, 20, {"total": 0, "clusters": []},
        )

        self.assertEqual("0011A0", page.code)

    def test_stores_historical_date_precision_and_search_relation(self) -> None:
        article = {
            "content": "요약", "contentHref": "https://example.test/article/1",
            "sourceProfile": {"title": "매체"}, "title": "써니전자 관련 기사",
        }
        bootstrap = {"body": {"props": {"children": [{"props": article}]}}}
        payload = {"collection": [{"script": (
            "entry.bootstrap(document.getElementById(\"root\"), "
            + json.dumps(bootstrap, ensure_ascii=False) + ");"
        )}]}
        page = parse_naver_historical_search_page(
            "004770", "써니전자", "2020-01-02", 1, payload,
            observed_at=datetime(2026, 9, 22, 5, 0, tzinfo=UTC),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "probe.sqlite3"
            store_naver_historical_search_page(path, page)
            result = ArticlePublicationResult(
                NAVER_HISTORICAL_SEARCH_PROVIDER, page.items[0].office_id,
                page.items[0].article_id, "published_at_found",
                "2020-01-02T10:31+09:00", "minute", "meta:article:published_time",
                "2020-01-02T10:31:00+09:00", page.items[0].original_url,
                page.items[0].original_url, "2026-09-22T05:01:00+00:00",
                (ArticleFetchAttempt(
                    "publisher_original", page.items[0].original_url,
                    page.items[0].original_url, "published_at_found", 200, "",
                    "2020-01-02T10:31+09:00", "minute",
                    "meta:article:published_time", "2020-01-02T10:31:00+09:00",
                ),),
            )
            store_article_publication_result(path, result)
            with closing(sqlite3.connect(path)) as connection:
                article_row = connection.execute(
                    "SELECT provider, published_at, published_precision, published_at_source, "
                    "article_fetch_status, training_eligible, training_exclusion_reason "
                    "FROM news_articles"
                ).fetchone()
                relation = connection.execute(
                    "SELECT source_date, query_text FROM news_search_observations"
                ).fetchone()
                endpoint = connection.execute(
                    "SELECT endpoint FROM source_pages"
                ).fetchone()[0]
                attempt = connection.execute(
                    "SELECT url_role, http_status, status FROM news_article_fetch_attempts"
                ).fetchone()
        self.assertEqual((
            NAVER_HISTORICAL_SEARCH_PROVIDER, "2020-01-02T10:31+09:00", "minute",
            "meta:article:published_time", "published_at_found", 1, "",
        ), article_row)
        self.assertEqual(("2020-01-02", "써니전자"), relation)
        self.assertEqual(("publisher_original", 200, "published_at_found"), attempt)
        self.assertIn("query=%EC%8D%A8%EB%8B%88%EC%A0%84%EC%9E%90", endpoint)
        self.assertIn("from20200102to20200102", endpoint)

    def test_records_readable_article_without_time_as_training_excluded(self) -> None:
        article = {
            "content": "요약", "contentHref": "https://example.test/article/2",
            "sourceProfile": {"title": "매체"}, "title": "써니전자 시간 없는 기사",
        }
        bootstrap = {"body": {"props": {"children": [{"props": article}]}}}
        payload = {"collection": [{"script": (
            "entry.bootstrap(document.getElementById(\"root\"), "
            + json.dumps(bootstrap, ensure_ascii=False) + ");"
        )}]}
        page = parse_naver_historical_search_page(
            "004770", "써니전자", "2020-01-02", 1, payload,
            observed_at=datetime(2026, 9, 22, 5, 0, tzinfo=UTC),
        )
        result = ArticlePublicationResult(
            NAVER_HISTORICAL_SEARCH_PROVIDER, page.items[0].office_id,
            page.items[0].article_id, "time_not_found", "", "", "", "",
            page.items[0].original_url, page.items[0].original_url,
            "2026-09-22T05:01:00+00:00",
            (ArticleFetchAttempt(
                "publisher_original", page.items[0].original_url,
                page.items[0].original_url, "time_not_found", 200, "",
                "", "", "", "",
            ),),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "probe.sqlite3"
            store_naver_historical_search_page(path, page)
            store_article_publication_result(path, result)
            with closing(sqlite3.connect(path)) as connection:
                article_state = connection.execute(
                    "SELECT published_precision, article_fetch_status, training_eligible, "
                    "training_exclusion_reason FROM news_articles"
                ).fetchone()
                attempt_state = connection.execute(
                    "SELECT http_status, status FROM news_article_fetch_attempts"
                ).fetchone()
        self.assertEqual(("date", "time_not_found", 0, "time_not_found"), article_state)
        self.assertEqual((200, "time_not_found"), attempt_state)

    def test_stores_daishin_bars_with_source_dimensions(self) -> None:
        payload = {
            "provider": "daishin_creon", "code": "005930", "interval_seconds": 300,
            "venue": "K", "session_scope": "regular", "adjustment_mode": "raw",
            "bar_time_semantics": "interval_end",
            "observed_at": "2026-09-22T05:30:00+00:00",
            "bars": [{
                "bar_time": "2026-09-21T15:30:00+09:00",
                "raw_date": 20260921, "raw_time": 1530, "open": 100, "high": 110,
                "low": 90, "close": 105, "volume": 1000, "trading_value": 102000,
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "probe.sqlite3"
            self.assertEqual(1, store_daishin_probe_payload(path, payload))
            with closing(sqlite3.connect(path)) as connection:
                row = connection.execute(
                    "SELECT provider, interval_seconds, venue, session_scope, adjustment_mode, "
                    "bar_time_semantics, raw_date, raw_time "
                    "FROM market_bars"
                ).fetchone()
        self.assertEqual((
            "daishin_creon", 300, "K", "regular", "raw", "interval_end", 20260921, 1530,
        ), row)

    def test_imports_only_five_minute_dates_before_one_minute_boundary(self) -> None:
        page = {
            "record_type": "page", "provider": "daishin_creon", "code": "005930",
            "interval_seconds": 300, "venue": "K", "session_scope": "regular",
            "adjustment_mode": "raw", "bar_time_semantics": "interval_end", "page": 1,
            "observed_at": "2026-09-22T06:00:00+00:00", "bars": [
                {"bar_time": "2024-09-02T09:05:00+09:00", "close": 102},
                {"bar_time": "2024-08-30T15:30:00+09:00", "close": 101},
            ],
        }
        summary = {
            "record_type": "summary", "provider": "daishin_creon", "code": "005930",
            "interval_seconds": 300, "provider_has_more": False,
            "stopped_by_max_pages": False,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "bars.ndjson"
            artifact.write_text(
                json.dumps(page) + "\n" + json.dumps(summary) + "\n", encoding="utf-8"
            )
            result = import_daishin_backfill_ndjson(
                artifact, root / "probe.sqlite3", before_date="2024-09-02",
            )
            with closing(sqlite3.connect(root / "probe.sqlite3")) as connection:
                stored = connection.execute("SELECT bar_time FROM market_bars").fetchall()
        self.assertEqual(2, result["observed_bars"])
        self.assertEqual(1, result["selected_bars"])
        self.assertEqual([("2024-08-30T15:30:00+09:00",)], stored)


if __name__ == "__main__":
    unittest.main()
