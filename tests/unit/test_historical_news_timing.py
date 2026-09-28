from __future__ import annotations

import json
from concurrent.futures import Future
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.report_historical_news_timing import summarize
from scripts.preprocess_historical_news_locally import ConcurrentArticlePreparation, _prepare
from scripts.preprocess_historical_news_to_nas import prepare_job
from kiwoom_monitor.domain.news_observation import ARTICLE_BODY_EXTRACTOR_VERSION


class HistoricalNewsTimingTest(unittest.TestCase):
    def test_market_preparation_reports_lookup_body_rule_and_failure_stage(self) -> None:
        events = []
        article = {"targets": [("GLOBAL", "시황")], "document": {"title": "example"}}

        def prepare_stage(job, *_args, **_kwargs):
            if job["stage"] == "BODY":
                return {"body_text": "body", "body_status": "fulltext"}
            return {"assessment": {}, "core_sentences": [], "rule_result": {}}

        observer = lambda event, fields: events.append((event, fields))
        with (patch("scripts.preprocess_historical_news_locally._market_article", return_value=article),
              patch("scripts.preprocess_historical_news_locally.prepare_job", side_effect=prepare_stage)):
            result = _prepare("historical_market_backfill", "GLOBAL", "article-1",
                              Path("unused"), Path("unused"), (), allow_network=False,
                              timing_observer=observer)
        self.assertEqual("fulltext", result[1]["body_status"])
        event, fields = events[0]
        self.assertEqual("market_prepare_article", event)
        self.assertEqual("ok", fields["status"])
        self.assertTrue(all(name in fields for name in
                            ("lookup_ms", "body_ms", "rule_ms", "total_ms")))

        with (patch("scripts.preprocess_historical_news_locally._market_article", return_value=article),
              patch("scripts.preprocess_historical_news_locally.prepare_job",
                    side_effect=RuntimeError("body failed"))):
            with self.assertRaisesRegex(RuntimeError, "body failed"):
                _prepare("historical_market_backfill", "GLOBAL", "article-2",
                         Path("unused"), Path("unused"), (), allow_network=False,
                         timing_observer=observer)
        self.assertEqual("body", events[-1][1]["failed_phase"])
        self.assertEqual("error", events[-1][1]["status"])

    def test_report_summarizes_preparation_substages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "timing.jsonl"
            path.write_text(json.dumps({"event": "market_prepare_article", "lookup_ms": 12,
                                        "body_ms": 32, "rule_ms": 8, "total_ms": 52}) + "\n",
                            encoding="utf-8")
            report = summarize(path)
        self.assertEqual(12, report["preparation"]["market_prepare_article.lookup_ms"]["median_ms"])
        self.assertEqual(32, report["preparation"]["market_prepare_article.body_ms"]["median_ms"])

    def test_market_body_timeout_is_reported_and_summary_is_kept(self) -> None:
        attempts = []
        job = {"job_key": "market-1", "attempts": 1, "stage": "BODY",
               "processing_version": ARTICLE_BODY_EXTRACTOR_VERSION}
        article = {"document": {"link": "https://stock.naver.com/news/worldnews/1",
                                "original_link": "", "description": "목록 요약"}}
        with patch("scripts.preprocess_historical_news_to_nas.fetch_article_text_with_metadata",
                   side_effect=TimeoutError("timed out")) as fetch:
            result = prepare_job(job, article, body_timeout_seconds=2.0,
                                 body_request_observer=lambda *parts: attempts.append(parts))
        self.assertEqual(2.0, fetch.call_args.kwargs["timeout_seconds"])
        self.assertEqual("summary_only", result["body_status"])
        self.assertEqual("목록 요약", result["body_text"])
        self.assertEqual(1, len(attempts))
        self.assertEqual("error", attempts[0][1])
        self.assertIn("TimeoutError", attempts[0][3])

    def test_body_diagnostic_failure_does_not_change_prepared_result(self) -> None:
        job = {"job_key": "market-1", "attempts": 1, "stage": "BODY",
               "processing_version": ARTICLE_BODY_EXTRACTOR_VERSION}
        article = {"document": {"link": "https://stock.naver.com/news/worldnews/1",
                                "original_link": "", "description": "목록 요약"}}
        with patch("scripts.preprocess_historical_news_to_nas.fetch_article_text_with_metadata",
                   return_value=("기사 본문", "")):
            result = prepare_job(job, article, body_timeout_seconds=2.0,
                                 body_request_observer=lambda *_: 1 / 0)
        self.assertEqual("fulltext", result["body_status"])
        self.assertEqual("기사 본문", result["body_text"])

    def test_preparation_failure_is_reported_and_still_saved_as_failure(self) -> None:
        failures = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worker = ConcurrentArticlePreparation(
                output=root / "prepared.sqlite3", search_database=root / "search.sqlite3",
                market_database=root / "market.sqlite3", matcher=(), workers=1,
                error_observer=lambda *parts: failures.append(parts),
            )
            future = Future()
            future.set_exception(RuntimeError("prepare failed"))
            worker.pending[future] = ("historical_backfill", "005930", "article-1")
            with patch("scripts.preprocess_historical_news_locally.save_prepared_batch") as save:
                worker.drain(all_pending=True)
                self.assertEqual("RuntimeError: prepare failed", save.call_args.args[1][0][-1])
            worker.close()
        self.assertEqual(("historical_backfill", "005930", "article-1",
                          "RuntimeError: prepare failed"), failures[0])

    def test_reports_network_time_separately_from_cooldown_and_skips(self) -> None:
        rows = [
            {"event": "search_request", "host": "s.search.naver.com",
             "status": "http_error", "fetch_ms": 100},
            {"event": "search_cooldown", "http_status": 429, "cooldown_ms": 60000},
            {"event": "search_request", "host": "s.search.naver.com",
             "status": "ok", "fetch_ms": 200},
            {"event": "article_request", "host": "slow.example",
             "url_role": "publisher_original", "status": "fetch_error", "elapsed_ms": 5000},
            {"event": "article_request", "host": "slow.example",
             "url_role": "publisher_original_skipped", "status": "fetch_error"},
            {"event": "search_page_store", "elapsed_ms": 12},
            {"event": "market_page_request", "host": "stock.naver.com",
             "url_role": "market_page", "status": "ok", "elapsed_ms": 85},
            {"event": "market_body_request", "host": "n.news.naver.com",
             "url_role": "market_article", "status": "error", "elapsed_ms": 2002},
            {"event": "market_page_retry_wait", "http_status": 503, "wait_seconds": 1},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "timing.jsonl"
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            report = summarize(path)
        self.assertEqual({"429": 1, "503": 1}, report["cooldowns_by_http_status"])
        search = next(row for row in report["hosts"] if row["role"] == "search")
        self.assertEqual(200, search["p95_ms"])
        slow = next(row for row in report["hosts"] if row["role"] == "publisher_original")
        self.assertEqual(1, slow["at_least_5s"])
        skipped = next(row for row in report["hosts"] if row["role"] == "publisher_original_skipped")
        self.assertEqual(0, skipped["timed_attempts"])
        market = next(row for row in report["hosts"] if row["role"] == "market_article")
        self.assertEqual(1, market["over_2s"])


if __name__ == "__main__":
    unittest.main()
