from __future__ import annotations

import asyncio
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.central_server.database import PostgresQueryStore, SQLiteQueryStore
from kiwoom_monitor.central_server.news_jobs import NewsJobRunner
from kiwoom_monitor.infrastructure.historical_backfill import (
    ArticlePublicationResult, NAVER_HISTORICAL_SEARCH_PROVIDER,
    store_article_publication_result,
)
from scripts.import_historical_news_to_nas import _require_pc_search_scope
from scripts.preprocess_historical_news_to_nas import prepare_job, require_pc_processing_scope
from tests.unit.test_news_observation_history import _article

try:
    from fastapi.testclient import TestClient
except ImportError:
    TestClient = None


class HistoricalNewsPcJobsTests(unittest.TestCase):
    def test_postgres_claim_logs_query_time_without_changing_empty_claim(self) -> None:
        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, _sql, _parameters):
                return None

            def fetchone(self):
                return None

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def cursor(self):
                return Cursor()

        store = PostgresQueryStore("postgresql://unused")
        store._connect = lambda: Connection()  # type: ignore[method-assign]
        with patch("kiwoom_monitor.central_server.database.monotonic",
                   side_effect=[0.0, 0.005, 1.605, 1.610]), \
             self.assertLogs("kiwoom_monitor.central_server.database", level="WARNING") as logs:
            self.assertIsNone(store.claim_external_historical_news_job("BODY", scope="pc_search"))
        self.assertIn("scope=pc_search outcome=no_job", logs.output[0])
        self.assertIn("connect_ms=5 select_ms=1600", logs.output[0])

    def test_postgres_claim_timeout_is_attributed_to_select(self) -> None:
        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, _sql, _parameters):
                raise TimeoutError("query timed out")

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def cursor(self):
                return Cursor()

        store = PostgresQueryStore("postgresql://unused")
        store._connect = lambda: Connection()  # type: ignore[method-assign]
        with patch("kiwoom_monitor.central_server.database.monotonic",
                   side_effect=[0.0, 0.005, 1.605, 1.610]), \
             self.assertLogs("kiwoom_monitor.central_server.database", level="WARNING") as logs:
            with self.assertRaises(TimeoutError):
                store.claim_external_historical_news_job("BODY", scope="pc_search")
        self.assertIn("outcome=TimeoutError", logs.output[0])
        self.assertIn("connect_ms=5 select_ms=1600", logs.output[0])

    def test_pc_backlog_worker_requires_server_ownership_capability(self) -> None:
        with self.assertRaisesRegex(SystemExit, "rebuild the server"):
            require_pc_processing_scope({"historical_news_pc_scopes": ["market", "search"]}, "pc")
        require_pc_processing_scope(
            {"historical_news_pc_scopes": ["market", "search", "legacy_backlog"]}, "pc"
        )

    def test_search_body_uses_archive_snapshot_without_another_http_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "archive.sqlite3"
            text = "삼성전자가 1000억원 공급계약을 체결했다. " * 5
            store_article_publication_result(archive, ArticlePublicationResult(
                NAVER_HISTORICAL_SEARCH_PROVIDER, "001", "123", "published_at_found",
                "2020-01-02T10:31:42+09:00", "second", "meta:article:published_time",
                "2020-01-02T10:31:42+09:00", "https://publisher.test/article",
                "https://publisher.test/article", "2026-09-24T01:00:00+00:00", (),
                text, "https://publisher.test/article", "2020-01-02T10:31:42+09:00",
            ))
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            item = _article("삼성전자, 1000억원 공급계약 체결")[0]
            item["collection_scope"] = "historical_news_pc_backfill"
            item["document"]["historical_source"] = {
                "provider": NAVER_HISTORICAL_SEARCH_PROVIDER,
                "office_id": "001", "article_id": "123",
            }
            store.upsert_documents("news_article", [item])
            body_job = store.claim_external_historical_news_job("BODY", scope="pc_search")
            article = store.load_news_article_revision(body_job["article_revision_id"])
            with patch("scripts.preprocess_historical_news_to_nas.fetch_article_text_with_metadata",
                       side_effect=AssertionError("unexpected HTTP fetch")):
                prepared = prepare_job(body_job, article, search_database=archive)
            self.assertEqual(text, prepared["body_text"])
            self.assertEqual("fulltext", prepared["body_status"])
            store.complete_external_historical_news_job(prepared)
            rule_job = store.claim_external_historical_news_job("RULE", scope="pc_search")
            body = store.load_news_body_revision(rule_job["payload"]["body_revision_id"])
            with patch("scripts.preprocess_historical_news_to_nas.fetch_article_text_with_metadata",
                       side_effect=AssertionError("unexpected HTTP fetch")):
                self.assertEqual("RULE", prepare_job(rule_job, article, body,
                                                      search_database=archive)["stage"])
            store.close()

    def test_market_body_fetches_once_and_rule_reuses_saved_body(self) -> None:
        article = {"stock_code": "GLOBAL", "identity": "market-1", "document": {
            "title": "해외 시황", "description": "목록 요약",
            "link": "https://publisher.test/market", "original_link": "",
        }}
        body_job = {"job_key": "body", "attempts": 1, "stage": "BODY",
                    "processing_version": "article-text-v7"}
        with patch("scripts.preprocess_historical_news_to_nas.fetch_article_text_with_metadata",
                   return_value=("해외 시장의 경제 지표가 발표됐다. " * 5, "")) as fetch:
            prepared = prepare_job(body_job, article)
            self.assertEqual(1, fetch.call_count)
            rule_job = {"job_key": "rule", "attempts": 1, "stage": "RULE",
                        "target_id": "GLOBAL", "payload": {"stock_code": "GLOBAL", "stock_name": "시황"}}
            prepare_job(rule_job, article, {"body_text": prepared["body_text"], "status": "fulltext"})
            self.assertEqual(1, fetch.call_count)

    def test_search_import_requires_server_ownership_capability(self) -> None:
        with patch("scripts.import_historical_news_to_nas.urlopen", return_value=io.BytesIO(b'{"status":"ok"}')):
            with self.assertRaisesRegex(SystemExit, "import is paused"):
                _require_pc_search_scope("http://nas")
        with patch("scripts.import_historical_news_to_nas.urlopen", return_value=io.BytesIO(
            b'{"status":"ok","historical_news_pc_scopes":["market","search"]}'
        )):
            _require_pc_search_scope("http://nas")

    def test_new_search_articles_are_reserved_for_pc_body_and_rule(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            item = _article("삼성전자, 1000억원 공급계약 체결")[0]
            item["collection_scope"] = "historical_news_pc_backfill"
            item["document"]["stock_name"] = "삼성전자"
            store.upsert_documents("news_article", [item])
            self.assertEqual([], store.claim_news_jobs())
            self.assertIsNone(store.claim_external_historical_news_job("BODY", scope="pc_market"))
            body_job = store.claim_external_historical_news_job("BODY", scope="pc_search")
            self.assertIsNotNone(body_job)
            store.complete_external_historical_news_job({
                "job_key": body_job["job_key"], "attempts": body_job["attempts"],
                "stage": "BODY", "body_text": "삼성전자가 공급계약을 체결했다.",
                "body_status": "fulltext",
            })
            self.assertEqual([], store.claim_news_jobs(preferred_stage="RULE"))
            rule_job = store.claim_external_historical_news_job("RULE", scope="pc")
            self.assertIsNotNone(rule_job)
            article = store.load_news_article_revision(rule_job["article_revision_id"])
            body = store.load_news_body_revision(rule_job["payload"]["body_revision_id"])
            store.complete_external_historical_news_job(prepare_job(rule_job, article, body))
            self.assertIsNotNone(store.load_document(
                "news_assessment", "005930", article["article_revision_id"]))
            store.close()

    def test_existing_market_articles_transfer_pending_jobs_to_pc(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            item = {"identity": "https://example.com/existing-market", "document": {
                "title": "기존 속보", "description": "시장 뉴스",
                "link": "https://example.com/existing-market",
                "published_at": "2020-01-01T09:00:00+09:00",
            }, "targets": [], "processing_excluded": False}
            store.save_historical_market_news_batch("flash", "2020-01-01", "a" * 64, [item])
            self.assertIsNone(store.claim_external_historical_news_job("BODY", scope="pc_market"))
            self.assertEqual([], store.claim_news_jobs())
            claimed = store.claim_external_historical_news_job("BODY", scope="pc")
            self.assertIsNotNone(claimed)
            store.complete_external_historical_news_job({
                "job_key": claimed["job_key"], "attempts": claimed["attempts"],
                "stage": "BODY", "body_text": "시장 뉴스 본문", "body_status": "fulltext",
            })
            self.assertEqual([], store.claim_news_jobs(preferred_stage="RULE"))
            rule = store.claim_external_historical_news_job("RULE", scope="pc")
            self.assertIsNotNone(rule)
            article = store.load_news_article_revision(rule["article_revision_id"])
            body = store.load_news_body_revision(rule["payload"]["body_revision_id"])
            with patch("scripts.preprocess_historical_news_to_nas.fetch_article_text_with_metadata",
                       side_effect=AssertionError("completed BODY must not refetch")):
                store.complete_external_historical_news_job(prepare_job(rule, article, body))
            self.assertIsNone(store.claim_external_historical_news_job("BODY", scope="pc"))
            self.assertIsNone(store.claim_external_historical_news_job("RULE", scope="pc"))
            store.close()

    def test_existing_search_article_transfers_to_pc_without_reclaiming_completed_body(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            item = _article("삼성전자, 1000억원 공급계약 체결")[0]
            item["collection_scope"] = "historical_backfill"
            store.upsert_documents("news_article", [item])
            self.assertEqual([], store.claim_news_jobs())
            body_job = store.claim_external_historical_news_job("BODY", scope="pc")
            self.assertIsNotNone(body_job)
            store.complete_external_historical_news_job({
                "job_key": body_job["job_key"], "attempts": body_job["attempts"],
                "stage": "BODY", "body_text": "삼성전자가 공급계약을 체결했다.",
                "body_status": "fulltext",
            })
            self.assertEqual([], store.claim_news_jobs(preferred_stage="RULE"))
            self.assertIsNone(store.claim_external_historical_news_job("BODY", scope="pc"))
            self.assertIsNotNone(store.claim_external_historical_news_job("RULE", scope="pc"))
            store.close()

    def test_new_market_articles_are_reserved_for_pc_body_and_rule(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            item = {"identity": "https://example.com/market-1", "document": {
                "title": "해외 시황", "description": "해외 경제 뉴스",
                "link": "https://example.com/market-1",
                "published_at": "2020-01-01T09:00:00+09:00",
            }, "targets": [], "processing_excluded": False}
            store.save_historical_market_news_batch(
                "world", "2020-01-01", "b" * 64, [item], "pc")
            article = store.load_news_history("article", target="GLOBAL")[0]
            self.assertEqual("historical_market_pc_backfill", article["collection_scope"])
            self.assertEqual([], store.claim_news_jobs())
            body_job = store.claim_external_historical_news_job("BODY", scope="pc_market")
            self.assertIsNotNone(body_job)
            self.assertEqual("GLOBAL", body_job["target_id"])
            store.complete_external_historical_news_job({
                "job_key": body_job["job_key"], "attempts": body_job["attempts"],
                "stage": "BODY", "body_text": "해외 경제 지표가 발표됐다.",
                "body_status": "fulltext",
            })
            self.assertEqual([], store.claim_news_jobs(preferred_stage="RULE"))
            rule_job = store.claim_external_historical_news_job("RULE", scope="pc_market")
            self.assertIsNotNone(rule_job)
            body = store.load_news_body_revision(rule_job["payload"]["body_revision_id"])
            store.complete_external_historical_news_job(prepare_job(rule_job, article, body))
            self.assertIsNotNone(store.load_document(
                "news_assessment", "GLOBAL", article["article_revision_id"]))
            self.assertEqual(1, len(store.load_market_news_feed("world")))
            store.close()

    @unittest.skipIf(TestClient is None, "FastAPI server test dependencies are not installed")
    def test_authenticated_api_claim_and_complete(self) -> None:
        from kiwoom_monitor.central_server.app import create_app
        from kiwoom_monitor.central_server.config import CentralServerSettings
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "central.sqlite3"
            store = SQLiteQueryStore(path)
            store.initialize()
            item = _article()[0]
            item["collection_scope"] = "historical_backfill"
            store.upsert_documents("news_article", [item])
            store.close()
            with TestClient(create_app(CentralServerSettings(f"sqlite:///{path}", "private-token"))) as client:
                url = "/api/v1/news/historical-jobs/claim?stage=BODY"
                self.assertEqual(401, client.post(url).status_code)
                headers = {"Authorization": "Bearer private-token"}
                self.assertIsNone(client.post(url + "&excluded_codes=005930", headers=headers).json()["job"])
                self.assertEqual(422, client.post(url + "&excluded_codes=bad", headers=headers).status_code)
                claimed = client.post(url, headers=headers)
                self.assertEqual(200, claimed.status_code)
                job = claimed.json()["job"]
                self.assertEqual("BODY", job["stage"])
                completed = client.post("/api/v1/news/historical-jobs/complete", headers=headers,
                    json={"job_key": job["job_key"], "attempts": job["attempts"], "stage": "BODY",
                          "body_text": "원문 본문", "body_status": "fulltext"})
                self.assertEqual(200, completed.status_code)
                self.assertEqual("completed", completed.json()["state"])

    def test_body_rule_and_replay_use_existing_revisions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            article_input = _article("삼성전자, 1000억원 공급계약 체결")[0]
            article_input["collection_scope"] = "historical_backfill"
            article_input["document"]["stock_name"] = "삼성전자"
            store.upsert_documents("news_article", [article_input])

            body_job = store.claim_external_historical_news_job("BODY")
            self.assertIsNotNone(body_job)
            article = store.load_news_article_revision(body_job["article_revision_id"])
            with patch("scripts.preprocess_historical_news_to_nas.fetch_article_text_with_metadata",
                       return_value=("삼성전자, 1000억원 공급계약을 체결했다. " * 5,
                                     "2026-09-12T09:00:03+09:00")):
                body_result = prepare_job(body_job, article)
            saved_body = store.complete_external_historical_news_job(body_result)
            self.assertEqual("completed", saved_body["state"])
            self.assertEqual("already_completed", store.complete_external_historical_news_job(body_result)["state"])
            self.assertEqual(1, len(store.load_news_history("body", target=article["article_revision_id"])))

            rule_job = store.claim_external_historical_news_job("RULE")
            self.assertIsNotNone(rule_job)
            body = store.load_news_body_revision(rule_job["payload"]["body_revision_id"])
            rule_result = prepare_job(rule_job, article, body)
            saved_rule = store.complete_external_historical_news_job(rule_result)
            self.assertEqual("completed", saved_rule["state"])
            self.assertEqual("already_completed", store.complete_external_historical_news_job(rule_result)["state"])
            self.assertIsNotNone(store.load_document("news_assessment", "005930", article["article_revision_id"]))
            self.assertEqual(1, len(store.load_news_history("event", target="005930")))
            self.assertIsNone(store.claim_external_historical_news_job("BODY"))
            store.close()

    def test_only_historical_jobs_are_claimed_and_stale_attempt_cannot_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            store.upsert_documents("news_article", _article())
            self.assertIsNone(store.claim_external_historical_news_job("BODY"))
            item = _article("과거 기사")[0]
            item["key"] = item["document"]["identity"] = "historic-2"
            item["collection_scope"] = "historical_backfill"
            store.upsert_documents("news_article", [item])
            job = store.claim_external_historical_news_job("BODY")
            article = store.load_news_article_revision(job["article_revision_id"])
            with patch("scripts.preprocess_historical_news_to_nas.fetch_article_text_with_metadata",
                       return_value=("본문", "")):
                result = prepare_job(job, article)
            with self.assertRaisesRegex(ValueError, "소유권"):
                store.complete_external_historical_news_job({**result, "attempts": job["attempts"] + 1})
            self.assertEqual([], store.load_news_history("body", target=article["article_revision_id"]))
            store.close()

    def test_pc_rule_matches_existing_nas_rule_for_same_article_body(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            item = _article("삼성전자, 1000억원 공급계약 체결")[0]
            item["collection_scope"] = "historical_backfill"
            item["document"]["stock_name"] = "삼성전자"
            body_text = "삼성전자가 1000억원 공급계약을 체결했다. " * 5
            nas = SQLiteQueryStore(Path(directory) / "nas.sqlite3")
            pc = SQLiteQueryStore(Path(directory) / "pc.sqlite3")
            for store in (nas, pc):
                store.initialize()
            nas.upsert_documents("news_article", [{**item, "collection_scope": "live"}])
            pc.upsert_documents("news_article", [item])
            runner = NewsJobRunner(nas, fetcher=lambda *_args, **_kwargs: (body_text, ""))
            asyncio.run(runner.run_once(preferred_stage="BODY"))
            asyncio.run(runner.run_once(preferred_stage="RULE"))
            job = pc.claim_external_historical_news_job("BODY")
            article = pc.load_news_article_revision(job["article_revision_id"])
            with patch("scripts.preprocess_historical_news_to_nas.fetch_article_text_with_metadata",
                       return_value=(body_text, "")):
                pc.complete_external_historical_news_job(prepare_job(job, article))
            rule = pc.claim_external_historical_news_job("RULE")
            pc_body = pc.load_news_body_revision(rule["payload"]["body_revision_id"])
            pc.complete_external_historical_news_job(prepare_job(rule, article, pc_body))
            nas_article = nas.load_news_history("article", target="005930")[0]
            nas_assessment = nas.load_document("news_assessment", "005930", nas_article["article_revision_id"])
            pc_assessment = pc.load_document("news_assessment", "005930", article["article_revision_id"])
            self.assertEqual(nas_assessment["document"]["assessment"], pc_assessment["document"]["assessment"])
            self.assertEqual(nas_assessment["document"]["core_sentences"], pc_assessment["document"]["core_sentences"])
            nas_event = nas.load_news_history("event", target="005930")[0]["result"]
            pc_event = pc.load_news_history("event", target="005930")[0]["result"]
            for key in ("article_revision_id", "body_revision_id"):
                nas_event.pop(key, None)
                pc_event.pop(key, None)
            self.assertEqual(nas_event, pc_event)
            nas.close()
            pc.close()
