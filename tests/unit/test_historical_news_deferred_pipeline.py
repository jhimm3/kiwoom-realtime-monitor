from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from kiwoom_monitor.infrastructure.historical_backfill import (
    ArticleFetchAttempt,
    ArticlePublicationResult,
    NAVER_HISTORICAL_SEARCH_PROVIDER,
    NewsBackfillJob,
    NaverHistoricalNewsItem,
    NaverHistoricalNewsPage,
    claim_news_article_pipeline,
    defer_news_article_host_403,
    finalize_news_article_jobs,
    finish_news_article_pipeline,
    initialize_probe_database,
    news_job_key,
    store_article_publication_result,
    store_naver_historical_search_page,
)
from scripts.probe_historical_backfill import _run_deferred_articles, main


def _page(code: str, *, article: str = "one") -> NaverHistoricalNewsPage:
    item = NaverHistoricalNewsItem(
        article, "001", article, "publisher", "title", "summary",
        f"https://publisher.test/{article}",
        f"https://publisher.test/{article}",
        f"https://n.news.naver.com/{article}", 1, "2022-11-01",
    )
    return NaverHistoricalNewsPage(
        code, "sample", "2022-11-01", 1, (item,), False,
        "2026-09-28T00:00:00+00:00", "{}",
    )


def _result(article: str = "one") -> ArticlePublicationResult:
    return ArticlePublicationResult(
        NAVER_HISTORICAL_SEARCH_PROVIDER, "001", article,
        "published_at_found", "2022-11-01T09:00:00+09:00", "minute",
        "publisher_original", "2022-11-01 09:00",
        f"https://publisher.test/{article}",
        f"https://publisher.test/{article}",
        "2026-09-28T00:01:00+00:00", (), "body text",
        f"https://publisher.test/{article}",
        "2022-11-01T09:00:00+09:00",
    )


class DeferredHistoricalNewsTests(unittest.TestCase):
    def test_search_only_persists_work_without_starting_article_or_preparation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "news.sqlite3"
            initialize_probe_database(db)
            with closing(sqlite3.connect(db)) as connection, connection:
                connection.execute(
                    "INSERT INTO news_backfill_jobs(code,target_date,query_text,state,updated_at) "
                    "VALUES('005930','2022-11-01','sample','pending','2026-09-28')"
                )
            argv = ["probe_historical_backfill.py", "news-run", "--jobs", "1",
                    "--search-only", "--output", str(db)]
            with patch("sys.argv", argv), patch(
                "scripts.probe_historical_backfill._SearchPagePool.fetch_batch",
                return_value=[(0, _page("005930"))],
            ), patch(
                "scripts.probe_historical_backfill._ArticleFetchPool",
                side_effect=AssertionError("article pool created by search"),
            ), patch(
                "scripts.probe_historical_backfill.ConcurrentArticlePreparation",
                side_effect=AssertionError("preparation created by search"),
            ):
                self.assertEqual(0, main())
            with closing(sqlite3.connect(db)) as connection, connection:
                self.assertEqual("search_complete", connection.execute(
                    "SELECT state FROM news_backfill_jobs"
                ).fetchone()[0])
                self.assertEqual(("pending", "not_fetched"), connection.execute(
                    "SELECT q.state,a.article_fetch_status FROM news_article_pipeline q "
                    "JOIN news_articles a ON a.office_id=q.office_id "
                    "AND a.article_id=q.article_id"
                ).fetchone())

    def test_claim_recovery_and_replay_only_finalize_after_preparation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "news.sqlite3"
            job_key = news_job_key(NewsBackfillJob(
                "005930", "2022-11-01", "sample", "test", "", 1,
            ))
            store_naver_historical_search_page(db, _page("005930"),
                                               deferred_job_key=job_key)
            with closing(sqlite3.connect(db)) as connection, connection:
                connection.execute(
                    "INSERT INTO news_backfill_jobs(code,target_date,query_text,state,updated_at) "
                    "VALUES('005930','2022-11-01','sample','search_complete','2026-09-28')"
                )
            first = claim_news_article_pipeline(db)[0]
            self.assertEqual(0, finalize_news_article_jobs(db, {job_key}))
            self.assertTrue(finish_news_article_pipeline(db, first, error="retry"))
            second = claim_news_article_pipeline(db)[0]
            self.assertNotEqual(first.claim_token, second.claim_token)
            self.assertFalse(finish_news_article_pipeline(db, first))
            store_article_publication_result(db, _result())
            self.assertTrue(finish_news_article_pipeline(db, second))
            self.assertEqual(1, finalize_news_article_jobs(db))
            self.assertEqual(0, finalize_news_article_jobs(db))
            with closing(sqlite3.connect(db)) as connection, connection:
                self.assertEqual(("complete", 1), connection.execute(
                    "SELECT state,usable_articles FROM news_backfill_jobs"
                ).fetchone())

    def test_one_original_is_fetched_once_for_two_codes_and_prepared_for_each(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "news.sqlite3"
            store_naver_historical_search_page(db, _page("005930"),
                                               deferred_job_key="day:first")
            store_naver_historical_search_page(db, _page("035720"),
                                               deferred_job_key="day:second")
            with closing(sqlite3.connect(db)) as connection, connection:
                connection.executemany(
                    "INSERT INTO news_backfill_jobs(code,target_date,query_text,state,updated_at) "
                    "VALUES(?, '2022-11-01','sample','search_complete','2026-09-28')",
                    [("005930",), ("035720",)],
                )
            args = SimpleNamespace(
                output=db, prepared_output=Path(directory) / "prepared.sqlite3",
                diagnostic_log=None, limit=10, article_workers=2,
                article_delay=0, prepare_workers=1,
            )
            with patch("scripts.probe_historical_backfill._fetch_primary_article",
                       return_value=_result()) as fetch, patch(
                "scripts.probe_historical_backfill.ConcurrentArticlePreparation"
            ) as prepare_type:
                prepare_type.return_value.__enter__.return_value = prepare_type.return_value
                self.assertEqual(0, _run_deferred_articles(args))
                self.assertEqual(1, fetch.call_count)
                self.assertEqual({"005930", "035720"}, {
                    call.args[1] for call in prepare_type.return_value.submit.call_args_list
                })
            with closing(sqlite3.connect(db)) as connection, connection:
                self.assertEqual(2, connection.execute(
                    "SELECT COUNT(*) FROM news_backfill_jobs WHERE state='complete'"
                ).fetchone()[0])
                self.assertEqual(2, connection.execute(
                    "SELECT COUNT(*) FROM news_article_pipeline WHERE state='complete'"
                ).fetchone()[0])

    def test_preparation_failure_retries_without_refetching_original(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "news.sqlite3"
            store_naver_historical_search_page(db, _page("005930"),
                                               deferred_job_key="day:retry")
            args = SimpleNamespace(
                output=db, prepared_output=Path(directory) / "prepared.sqlite3",
                diagnostic_log=None, limit=10, article_workers=1,
                article_delay=0, prepare_workers=1,
            )
            with patch("scripts.probe_historical_backfill._fetch_primary_article",
                       return_value=_result()) as fetch, patch(
                "scripts.probe_historical_backfill.ConcurrentArticlePreparation"
            ) as prepare_type:
                worker = prepare_type.return_value
                worker.__enter__.return_value = worker
                worker.drain.side_effect = RuntimeError("injected preparation failure")
                with self.assertRaisesRegex(RuntimeError, "injected preparation failure"):
                    _run_deferred_articles(args)
                with closing(sqlite3.connect(db)) as connection:
                    self.assertEqual("pending", connection.execute(
                        "SELECT state FROM news_article_pipeline"
                    ).fetchone()[0])
                    self.assertEqual("published_at_found", connection.execute(
                        "SELECT article_fetch_status FROM news_articles"
                    ).fetchone()[0])
                worker.drain.side_effect = None
                self.assertEqual(0, _run_deferred_articles(args))
                self.assertEqual(1, fetch.call_count)
                self.assertEqual(2, worker.submit.call_count)

    def test_three_403s_defer_host_across_runs_without_losing_articles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "news.sqlite3"
            source = _page("005930")
            items = tuple(NaverHistoricalNewsItem(
                str(index), "001", str(index), "publisher", "title", "summary",
                f"https://{'blocked' if index < 5 else 'working'}.test/{index}",
                f"https://{'blocked' if index < 5 else 'working'}.test/{index}",
                "", index, "2022-11-01",
            ) for index in range(6))
            page = NaverHistoricalNewsPage(
                source.code, source.query, source.target_date, source.start,
                items, False, source.observed_at, source.raw_json,
            )
            store_naver_historical_search_page(db, page, deferred_job_key="day:403")
            args = SimpleNamespace(
                output=db, prepared_output=Path(directory) / "prepared.sqlite3",
                diagnostic_log=None, limit=10, article_workers=2,
                article_delay=0, prepare_workers=1,
            )
            requested = []

            def fetch(item):
                requested.append(item.article_id)
                blocked = item.article_id != "5"
                return ArticlePublicationResult(
                    NAVER_HISTORICAL_SEARCH_PROVIDER, item.office_id,
                    item.article_id,
                    "blocked" if blocked else "published_at_found",
                    "" if blocked else "2022-11-01T09:00:00+09:00",
                    "" if blocked else "minute",
                    "" if blocked else "publisher_original", "", item.original_url,
                    item.original_url, "2026-09-28T00:01:00+00:00",
                    (ArticleFetchAttempt(
                        "publisher_original", item.original_url,
                        item.original_url, "blocked" if blocked else "published_at_found",
                        403 if blocked else 200, "HTTPError: 403" if blocked else "",
                        "", "", "", "",
                    ),),
                )

            with patch("scripts.probe_historical_backfill._fetch_primary_article",
                       side_effect=fetch), patch(
                "scripts.probe_historical_backfill.ConcurrentArticlePreparation"
            ) as prepare_type:
                prepare_type.return_value.__enter__.return_value = prepare_type.return_value
                self.assertEqual(0, _run_deferred_articles(args))
                self.assertEqual({"0", "1", "2", "5"}, set(requested))
                with closing(sqlite3.connect(db)) as connection:
                    self.assertEqual((5, 1), connection.execute(
                        "SELECT SUM(state='pending'),SUM(state='complete') "
                        "FROM news_article_pipeline"
                    ).fetchone())
                    self.assertEqual(3, connection.execute(
                        "SELECT COUNT(*) FROM news_article_fetch_attempts "
                        "WHERE http_status=403"
                    ).fetchone()[0])
                    self.assertEqual(5, connection.execute(
                        "SELECT COUNT(*) FROM news_articles WHERE article_fetch_status='not_fetched'"
                    ).fetchone()[0])
                    self.assertIsNotNone(connection.execute(
                        "SELECT retry_after FROM news_article_host_cooldowns "
                        "WHERE host='blocked.test'"
                    ).fetchone())
                self.assertEqual([], claim_news_article_pipeline(db))
                self.assertEqual(0, finalize_news_article_jobs(db, {"day:403"}))
                with closing(sqlite3.connect(db)) as connection, connection:
                    connection.execute(
                        "UPDATE news_article_host_cooldowns SET "
                        "retry_after='2020-01-01T00:00:00+00:00'"
                    )
                self.assertEqual(5, len(claim_news_article_pipeline(db)))

    def test_page_store_rolls_back_article_and_queue_together(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "news.sqlite3"
            page = _page("005930")
            broken = NaverHistoricalNewsPage(
                page.code, page.query, page.target_date, page.start,
                (NaverHistoricalNewsItem(
                    page.items[0].article_key, page.items[0].office_id,
                    page.items[0].article_id, page.items[0].office_name,
                    page.items[0].title, page.items[0].summary,
                    page.items[0].article_url, page.items[0].original_url,
                    page.items[0].portal_url, None, page.items[0].search_published_date,
                ),), page.has_structured_news, page.observed_at, page.raw_json,
            )
            with self.assertRaises(sqlite3.IntegrityError):
                store_naver_historical_search_page(
                    db, broken, deferred_job_key="day:broken",
                )
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual([0, 0, 0], [connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0] for table in (
                    "source_pages", "news_articles", "news_article_pipeline",
                )])

    def test_stale_claim_recovery_rejects_previous_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "news.sqlite3"
            store_naver_historical_search_page(db, _page("005930"),
                                               deferred_job_key="day:stale")
            first = claim_news_article_pipeline(db)[0]
            self.assertEqual([], claim_news_article_pipeline(db))
            with closing(sqlite3.connect(db)) as connection, connection:
                connection.execute(
                    "UPDATE news_article_pipeline SET claimed_at='2020-01-01T00:00:00+00:00'"
                )
            second = claim_news_article_pipeline(db)[0]
            self.assertFalse(finish_news_article_pipeline(db, first))
            self.assertTrue(finish_news_article_pipeline(db, second))

    def test_403_cooldown_rolls_back_if_one_claim_token_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "news.sqlite3"
            first_page = _page("005930", article="one")
            second_page = _page("005930", article="two")
            store_naver_historical_search_page(
                db, first_page, deferred_job_key="day:rollback",
            )
            store_naver_historical_search_page(
                db, second_page, deferred_job_key="day:rollback",
            )
            entries = claim_news_article_pipeline(db)
            with closing(sqlite3.connect(db)) as connection, connection:
                connection.execute(
                    "UPDATE news_article_pipeline SET claim_token='new-owner' "
                    "WHERE queue_id=?", (entries[1].queue_id,),
                )
            with self.assertRaisesRegex(RuntimeError, "claim changed"):
                defer_news_article_host_403(db, entries, host="publisher.test")
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(2, connection.execute(
                    "SELECT COUNT(*) FROM news_article_pipeline WHERE state='running'"
                ).fetchone()[0])
                self.assertEqual(0, connection.execute(
                    "SELECT COUNT(*) FROM news_article_host_cooldowns"
                ).fetchone()[0])

    def test_pipeline_schema_adds_attempts_to_earlier_local_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "news.sqlite3"
            initialize_probe_database(db)
            with closing(sqlite3.connect(db)) as connection, connection:
                connection.execute("ALTER TABLE news_article_pipeline DROP COLUMN attempts")
            initialize_probe_database(db)
            with closing(sqlite3.connect(db)) as connection:
                columns = {row[1] for row in connection.execute(
                    "PRAGMA table_info(news_article_pipeline)"
                )}
            self.assertIn("attempts", columns)


if __name__ == "__main__":
    unittest.main()
