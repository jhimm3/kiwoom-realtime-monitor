from __future__ import annotations

import asyncio
from contextlib import contextmanager
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from kiwoom_monitor.central_server.database import PostgresQueryStore, SQLiteQueryStore
from kiwoom_monitor.central_server.news_jobs import NewsJobRunner
from kiwoom_monitor.infrastructure.historical_backfill import (
    ArticlePublicationResult, NAVER_HISTORICAL_SEARCH_PROVIDER,
    store_article_publication_result,
)
from scripts.import_historical_news_to_nas import _require_pc_search_scope
from scripts.preprocess_historical_news_to_nas import prepare_job, require_pc_processing_scope


async def capture_historical_job_http_contract(client, store):
    """Exercise real leases and persistence; keep complete responses for comparison."""
    cases = []
    headers = {"Authorization": "Bearer private-token"}

    async def post(name, path, status, body=None, *, authenticated=True):
        response = await client.post(path, json=body, headers=headers if authenticated else {})
        assert response.status_code == status, (name, response.status_code, response.text)
        document = response.json()
        cases.append({"name": name, "status": response.status_code,
                      "headers": {key: response.headers[key]
                                  for key in ("content-type", "content-length")},
                      "body": document})
        return document

    endpoint = "/api/v1/news/historical-market-articles"
    batch = {"source": "world", "target_date": "2020-01-02", "batch_id": "a" * 64,
             "processing_owner": "pc", "items": [{"identity": "world-1", "document": {
                 "title": "해외시장", "description": "해외 뉴스", "link": "https://example.com/1",
                 "published_at": "2020-01-01T15:00:01+00:00"},
                 "targets": [], "processing_excluded": False}]}
    await post("unauthorized-import", endpoint, 401, batch, authenticated=False)
    await post("invalid-source", endpoint, 422, {**batch, "source": "invalid"})
    await post("wrong-publication-date", endpoint, 422, {**batch, "target_date": "2020-01-03"})
    assert store.load_market_news_feed("world") == [], "invalid batch was stored"
    await post("import", endpoint, 200, batch)
    await post("duplicate-import", endpoint, 200, batch)
    assert len(store.load_market_news_feed("world")) == 1, "duplicate batch added an article"
    claim = "/api/v1/news/historical-jobs/claim"
    await post("unauthorized-claim", claim + "?stage=BODY", 401, authenticated=False)
    await post("invalid-stage", claim + "?stage=invalid", 422)
    await post("invalid-scope", claim + "?stage=BODY&scope=invalid", 422)
    await post("invalid-exclusion", claim + "?stage=BODY&excluded_codes=bad", 422)
    empty = await post("other-scope-empty", claim + "?stage=BODY&scope=pc_search", 200)
    assert empty == {"job": None}
    claimed = await post("body-claim", claim + "?stage=BODY&scope=pc_market", 200)
    job, article = claimed["job"], claimed["article"]
    again = await post("running-job-not-reclaimed", claim + "?stage=BODY&scope=pc_market", 200)
    assert again == {"job": None}
    complete = "/api/v1/news/historical-jobs/complete"
    result = {"job_key": job["job_key"], "attempts": job["attempts"], "stage": "BODY",
              "body_text": "해외 경제 지표가 발표됐다.", "body_status": "fulltext"}
    await post("unauthorized-complete", complete, 401, result, authenticated=False)
    await post("invalid-attempt", complete, 422, {**result, "attempts": 0})
    await post("stale-body-owner", complete, 409, {**result, "attempts": job["attempts"] + 1})
    assert store.load_news_history("body", target=article["article_revision_id"]) == [], \
        "stale completion stored a body"
    await post("body-complete", complete, 200, result)
    rule = await post("rule-claim", claim + "?stage=RULE&scope=pc_market", 200)
    assert rule["body"]["body_revision_id"] == rule["job"]["payload"]["body_revision_id"]
    rule_result = prepare_job(rule["job"], rule["article"], rule["body"])
    await post("stale-rule-owner", complete, 409,
               {**rule_result, "attempts": rule["job"]["attempts"] + 1})
    assert store.load_document("news_assessment", "GLOBAL", article["article_revision_id"]) is None
    await post("rule-complete", complete, 200, rule_result)
    assessment = store.load_document("news_assessment", "GLOBAL", article["article_revision_id"])
    assert assessment is not None, "valid rule completion did not store its result"
    return {"cases": cases,
            "stored": {"body": store.load_news_history("body", target=article["article_revision_id"]),
                       "assessment": assessment, "feed": store.load_market_news_feed("world")}}


@contextmanager
def fixed_historical_job_inputs():
    """Freeze actual input owners; preserve original identities/epoch and raw responses."""
    import uuid
    from contextlib import ExitStack
    from datetime import datetime
    from kiwoom_monitor.domain.time_basis import KST

    fixed = datetime.fromtimestamp(1790300000.0, KST)

    class FixedAppDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz is not None else fixed.replace(tzinfo=None)

    with ExitStack() as stack:
        stack.enter_context(patch("uuid.uuid4", side_effect=(uuid.UUID(int=i) for i in range(1, 1000))))
        stack.enter_context(patch("time.time", return_value=1790300000.0))
        for module in ("database_news_revisions", "database_news_jobs"):
            stack.enter_context(patch(f"kiwoom_monitor.central_server.{module}.time", return_value=1790300000.0))
        # Broker-free app.server_now() passes this existing fallback to the real native store.
        stack.enter_context(patch("kiwoom_monitor.central_server.app.datetime", FixedAppDatetime))
        yield lambda: fixed

def _article(title: str = "첫 제목") -> list[dict[str, object]]:
    document = {
        "stock_code": "005930", "identity": "article-1", "title": title,
        "description": "검색 요약", "link": "https://news/1",
        "original_link": "https://origin/1", "published_at": "2026-09-12T00:00:00+00:00",
    }
    return [{"owner": "005930", "key": "article-1", "document": document,
             "collector_id": "naver", "collection_scope": "watchlist"}]


class HistoricalNewsPcJobsTests(unittest.TestCase):
    def test_http_contract_and_stored_results_match_pre_extraction_baseline(self) -> None:
        from kiwoom_monitor.central_server.app import create_app
        from kiwoom_monitor.central_server.config import CentralServerSettings

        async def verify(path):
            with fixed_historical_job_inputs() as fixed_now:
                app = create_app(CentralServerSettings(f"sqlite:///{path}", "private-token"))
                async with app.router.lifespan_context(app):
                    store = SQLiteQueryStore(path, now_provider=fixed_now)
                    try:
                        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                                     base_url="http://historical-news.test", timeout=5) as client:
                            return await capture_historical_job_http_contract(client, store)
                    finally:
                        store.close()

        baseline_path = (Path(__file__).resolve().parents[1] / "fixtures" / "api_contract_baselines"
                         / "historical_jobs_http_v1.json")
        expected = json.loads(baseline_path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(expected["contract"], asyncio.run(verify(Path(directory) / "central.sqlite3")))

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
        with patch("kiwoom_monitor.central_server.database_historical_news.monotonic",
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
        with patch("kiwoom_monitor.central_server.database_historical_news.monotonic",
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

            async def verify_requests() -> None:
                app = create_app(CentralServerSettings(f"sqlite:///{path}", "private-token"))
                async with app.router.lifespan_context(app):
                    async with httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app),
                        base_url="http://historical-news.test",
                        timeout=5,
                    ) as client:
                        url = "/api/v1/news/historical-jobs/claim?stage=BODY"
                        self.assertEqual(401, (await client.post(url)).status_code)
                        headers = {"Authorization": "Bearer private-token"}
                        excluded = await client.post(
                            url + "&excluded_codes=005930", headers=headers,
                        )
                        self.assertIsNone(excluded.json()["job"])
                        invalid = await client.post(url + "&excluded_codes=bad", headers=headers)
                        self.assertEqual(422, invalid.status_code)
                        claimed = await client.post(url, headers=headers)
                        self.assertEqual(200, claimed.status_code)
                        job = claimed.json()["job"]
                        self.assertEqual("BODY", job["stage"])
                        completed = await client.post(
                            "/api/v1/news/historical-jobs/complete", headers=headers,
                            json={"job_key": job["job_key"], "attempts": job["attempts"],
                                  "stage": "BODY", "body_text": "원문 본문",
                                  "body_status": "fulltext"},
                        )
                        self.assertEqual(200, completed.status_code)
                        self.assertEqual("completed", completed.json()["state"])

            asyncio.run(verify_requests())

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
            self.assertEqual("SUPPLY_CONTRACT", pc_event["event_type"])
            self.assertEqual("CONFIRMED", pc_event["certainty"])
            self.assertEqual(100_000_000_000, pc_event["amount_won"])

            nas.close()
            pc.close()


class HistoricalSourceClockNativeTests(unittest.TestCase):
    """The same public source/claim/finish contract is inherited by actual PG."""
    postgres = False

    def _make_store(self, path):
        return SQLiteQueryStore(path, now_provider=lambda: self.clock[0])

    def _rows(self, store, sql, params=()):
        if self.postgres:
            with store._connect() as connection, connection.cursor() as cursor:
                cursor.execute(sql.replace("?", "%s"), params)
                return cursor.fetchall()
        with store._connection() as connection:
            return connection.execute(sql, params).fetchall()

    def _cases(self):
        from datetime import datetime
        from kiwoom_monitor.domain.time_basis import KST
        before = datetime(2026, 10, 24, 23, 59, 58, tzinfo=KST)
        after = datetime(2026, 10, 25, 0, 0, 2, tzinfo=KST)
        return (("C_after_H", before, after), ("C_before_H", after, before))

    def _seed(self, directory, case, corrected):
        self.clock = [corrected]
        store = self._make_store(Path(directory) / (case + ".sqlite3"))
        store.initialize()
        item = {"identity": "https://example.test/historical-clock/" + case,
                "document": {"title": "삼성전자 500억원 공급계약 체결", "description": "삼성전자 공급계약",
                             "link": "https://example.test/clock", "published_at": "2020-01-02T09:00:03+09:00"},
                "targets": [{"stock_code": "005930", "stock_name": "삼성전자",
                             "relation_status": "confirmed", "rule_version": "exact-v1"}]}
        store.save_historical_market_news_batch("flash", "2020-01-02", "clock-" + case, [item], processing_owner="pc")
        return store

    def test_historical_public_body_rule_receipts_and_ack_keep_one_clock_across_host_skew(self):
        from kiwoom_monitor.central_server import database_historical_news as historical
        for case, host, corrected in self._cases():
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory, \
                    patch.object(historical, "time", return_value=host.timestamp(), create=True), \
                    patch("kiwoom_monitor.central_server.database_news_revisions.time", return_value=host.timestamp()):
                store = self._seed(directory, case, corrected)
                try:
                    job = store.claim_external_historical_news_job("BODY", scope="pc_market")
                    self.assertIsNotNone(job)
                    self.assertEqual(1, job["attempts"])
                    article = store.load_news_article_revision(job["article_revision_id"])
                    self.assertEqual((corrected.timestamp(), "2020-01-02T09:00:03+09:00"),
                                     (article["received_at"], article["published_at"]))
                    wakes = []
                    store.set_news_job_wakeup(lambda: wakes.append(len(store.load_news_history("body", target=article["article_revision_id"]))))
                    result = {"job_key": job["job_key"], "attempts": 1, "stage": "BODY",
                              "body_text": "삼성전자가 500억원 공급계약을 체결했다.", "body_status": "fulltext",
                              "fetched_at": 1577923203.0, "original_published_at": "2020-01-02T09:00:01+09:00",
                              "source_url": "https://example.test/raw"}
                    receipt = store.complete_external_historical_news_job(result)
                    self.assertEqual("completed", receipt["state"])
                    self.assertEqual("already_completed", store.complete_external_historical_news_job(result)["state"])
                    body = store.load_news_body_revision(receipt["output_ref"])
                    self.assertEqual((1577923203.0, corrected.timestamp()), (body["fetched_at"], body["available_at"]))
                    self.assertEqual([1, 1], wakes)  # Existing public BODY wake happens after commit, including replay.
                    jobs = []
                    for _ in range(2):
                        rule = store.claim_external_historical_news_job("RULE", scope="pc_market")
                        self.assertIsNotNone(rule)
                        jobs.append(rule)
                        self.assertEqual(corrected.timestamp(), rule["queued_at"])
                        payload = prepare_job(rule, article, body, allow_network=False)
                        saved = store.complete_external_historical_news_job(payload)
                        self.assertEqual("completed", saved["state"])
                        self.assertEqual("already_completed", store.complete_external_historical_news_job(payload)["state"])
                    self.assertEqual({"GLOBAL", "005930"}, {rule["target_id"] for rule in jobs})
                    self.assertIsNone(store.claim_external_historical_news_job("RULE", scope="pc_market"))
                    self.assertEqual({corrected.timestamp()}, {row[0] for row in self._rows(store,
                        "SELECT updated_at FROM central_news_jobs WHERE article_revision_id=?", (article["article_revision_id"],))})
                    events = store.load_news_history("event", target="005930")
                    self.assertEqual(1, len(events))
                    self.assertEqual(corrected.timestamp(), events[0]["available_at"])
                    publication = store.load_document("news_original_publication", article["article_revision_id"], "published_at")
                    self.assertEqual("2020-01-02T09:00:01+09:00", publication["document"]["published_at"])
                finally:
                    store.close()

    def test_historical_retry_stale120_and_final_failure_use_store_clock_across_host_skew(self):
        from datetime import timedelta
        from kiwoom_monitor.central_server import database_historical_news as historical
        for case, host, corrected in self._cases():
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory, \
                    patch.object(historical, "time", return_value=host.timestamp(), create=True), \
                    patch("kiwoom_monitor.central_server.database_news_revisions.time", return_value=host.timestamp()):
                store = self._seed(directory, case, corrected)
                try:
                    first = store.claim_external_historical_news_job("BODY", scope="pc_market")
                    self.assertIsNotNone(first)
                    error = {"job_key": first["job_key"], "attempts": 1, "stage": "BODY", "error": "fixed failure"}
                    self.assertEqual("pending", store.complete_external_historical_news_job(error)["state"])
                    self.assertEqual([(corrected.timestamp() + 2, corrected.timestamp())], self._rows(store,
                        "SELECT next_retry_at,updated_at FROM central_news_jobs WHERE job_key=?", (first["job_key"],)))
                    self.assertIsNone(store.claim_external_historical_news_job("BODY", scope="pc_market"))
                    self.clock[0] = corrected + timedelta(seconds=2)
                    second = store.claim_external_historical_news_job("BODY", scope="pc_market")
                    self.assertEqual(2, second["attempts"])
                    self.clock[0] += timedelta(seconds=120)
                    self.assertEqual([], store.claim_news_jobs(now=self.clock[0].timestamp()))
                    self.assertIsNone(store.claim_external_historical_news_job("BODY", scope="pc_market"))
                    self.clock[0] += timedelta(milliseconds=1)
                    self.assertEqual([], store.claim_news_jobs(now=self.clock[0].timestamp()))
                    third = store.claim_external_historical_news_job("BODY", scope="pc_market")
                    self.assertEqual(3, third["attempts"])
                    with self.assertRaisesRegex(ValueError, "소유권"):
                        store.complete_external_historical_news_job({**error, "attempts": 2})
                    self.assertEqual("failed", store.complete_external_historical_news_job({**error, "attempts": 3})["state"])
                    self.assertEqual([(self.clock[0].timestamp() + 8, self.clock[0].timestamp(), "FAILED")], self._rows(store,
                        "SELECT next_retry_at,updated_at,state FROM central_news_jobs WHERE job_key=?", (first["job_key"],)))
                    self.assertIsNone(store.claim_external_historical_news_job("BODY", scope="pc_market"))
                    self.assertEqual([], store.load_news_history("body", target=first["article_revision_id"]))
                finally:
                    store.close()


class HistoricalProcessingClockTests(unittest.TestCase):
    def test_prepare_job_uses_supplied_clock_for_new_receipt_and_preserves_snapshot_instant(self):
        from datetime import datetime
        from kiwoom_monitor.domain.time_basis import KST
        corrected = datetime(2026, 10, 25, 0, 0, 2, tzinfo=KST)
        job = {"job_key": "fixed-job", "attempts": 1, "stage": "BODY", "processing_version": "article-text-v7"}
        article = {"document": {"description": "원래 목록 요약", "historical_source": {
            "provider": "naver", "office_id": "001", "article_id": "old"}}}
        with patch("scripts.preprocess_historical_news_to_nas.time.time", return_value=1.0):
            result = prepare_job(job, article, allow_network=False, now_provider=lambda: corrected)
            self.assertEqual(corrected.timestamp(), result["fetched_at"])
            snapshot = {"body_text": "원래 본문", "fetched_at": "2020-01-02T09:00:03",
                        "published_at": "2020-01-02T09:00:01+09:00", "source_url": "https://example.test/old"}
            with patch("scripts.preprocess_historical_news_to_nas.load_article_body_snapshot", return_value=snapshot):
                original = prepare_job(job, article, search_database=Path("unused"), now_provider=lambda: corrected)
            self.assertEqual(datetime(2020, 1, 2, 9, 0, 3, tzinfo=KST).timestamp(), original["fetched_at"])
            self.assertEqual(snapshot["published_at"], original["original_published_at"])
