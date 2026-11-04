from __future__ import annotations

import unittest
import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.central_server.ai_service import CentralAIService, _prepare_events
from kiwoom_monitor.central_server.config import CentralServerSettings
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.infrastructure.news_ai import ANALYSIS_PROMPT_VERSION, AINewsAnalysis, AIRequestUsage


class _Store:
    def __init__(self) -> None:
        self.values = []

    def upsert_documents(self, collection, values):
        self.values.extend((collection, value) for value in values)

    def load_documents(self, collection, owner="", limit=1000):
        return [
            {"owner": value["owner"], "key": value["key"], "document": value["document"]}
            for saved_collection, value in self.values
            if saved_collection == collection and (not owner or value["owner"] == owner)
        ][:limit]


class CentralAIServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_server_fetches_article_body_and_calculates_hash(self) -> None:
        with patch("kiwoom_monitor.central_server.ai_service.fetch_article_text", return_value="중앙 원문") as fetch:
            prepared = _prepare_events([{
                "identity": "article", "title": "제목", "articles": [{
                    "title": "관련 제목", "description": "검색 요약",
                    "link": "https://news/1", "original_link": "https://origin/1",
                }],
            }])
        self.assertIn("중앙 원문", prepared[0]["body"])
        self.assertTrue(prepared[0]["body_hash"].startswith(f"{ANALYSIS_PROMPT_VERSION}:"))
        fetch.assert_called_once_with("https://news/1")

    def test_server_uses_search_summary_when_original_is_unavailable(self) -> None:
        with patch("kiwoom_monitor.central_server.ai_service.fetch_article_text", side_effect=ValueError("blocked")):
            prepared = _prepare_events([{
                "identity": "article", "title": "제목", "articles": [{
                    "title": "관련 제목", "description": "검색 요약", "link": "https://news/1",
                }],
            }])
        self.assertIn("검색 요약", prepared[0]["body"])

    async def test_analyzes_once_then_reuses_body_hash_cache(self) -> None:
        settings = CentralServerSettings("sqlite:///:memory:", "token", gemini_api_key="key")
        store = _Store()
        service = CentralAIService(settings, store)  # type: ignore[arg-type]
        events = [{"identity": "article", "title": "제목", "body": "본문", "body_hash": "hash"}]
        analysis = AINewsAnalysis("요약", "긍정", 80, "이유", ("근거",), (), "실적·전망")

        with patch("kiwoom_monitor.central_server.ai_service.analyze_articles", return_value=((analysis,), AIRequestUsage(10, 5, 15))) as analyze:
            first = await service.analyze("005930", "삼성전자", "gemini", "model", events, 1)
            second = await service.analyze("005930", "삼성전자", "gemini", "model", events, 1)

        self.assertEqual(1, analyze.call_count)
        self.assertFalse(first["cache_hit"])
        self.assertTrue(second["cache_hit"])
        self.assertEqual("요약", second["results"][0]["summary"])
        self.assertEqual(1, len([value for collection, value in store.values if collection == "news_request_usage"]))

    async def test_daily_limit_is_shared_by_different_requests(self) -> None:
        settings = CentralServerSettings(
            "sqlite:///:memory:", "token", gemini_api_key="key", ai_daily_limit=1,
        )
        service = CentralAIService(settings, _Store())  # type: ignore[arg-type]
        analysis = AINewsAnalysis("요약", "긍정", 80, "이유")
        with patch("kiwoom_monitor.central_server.ai_service.analyze_articles", return_value=((analysis,), AIRequestUsage())):
            await service.analyze("005930", "삼성전자", "gemini", "model", [{
                "identity": "one", "title": "제목", "body": "본문", "body_hash": "one",
            }], 1)
            with self.assertRaisesRegex(ValueError, "일일 상한"):
                await service.analyze("000660", "SK하이닉스", "gemini", "model", [{
                    "identity": "two", "title": "제목", "body": "본문", "body_hash": "two",
                }], 1)

    async def test_real_store_appends_ai_revision_and_usage_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "central.sqlite3"
            store = SQLiteQueryStore(path)
            store.initialize()
            store.upsert_documents("news_article", [{
                "owner": "005930", "key": "article", "collector_id": "naver",
                "collection_scope": "watchlist",
                "document": {"stock_code": "005930", "identity": "article", "title": "제목"},
            }])
            article = store.load_news_history("article", target="005930")[0]
            body_revision_id = store.save_news_body_revision({
                "article_revision_id": article["article_revision_id"], "status": "fulltext",
                "body_text": "본문", "extractor_version": "test-v1", "fetched_at": 100.0,
            })
            service = CentralAIService(
                CentralServerSettings("sqlite:///:memory:", "token", gemini_api_key="key"), store,
            )
            analysis = AINewsAnalysis("요약", "긍정", 80, "이유")
            event = {"identity": "article", "title": "제목", "body": "본문",
                     "article_revision_id": article["article_revision_id"],
                     "body_revision_id": body_revision_id}
            with patch(
                "kiwoom_monitor.central_server.ai_service.analyze_articles",
                return_value=((analysis,), AIRequestUsage(3, 2, 5)),
            ) as analyze:
                result = await service.analyze("005930", "삼성전자", "gemini", "model", [event], 1)
                second = await service.analyze(
                    "005930", "삼성전자", "gemini", "model-v2", [event], 1,
                )
            revisions = store.load_news_history("ai", target="005930")
            usage = store.load_documents("news_request_usage", limit=10)
            latest = store.load_documents("news_ai", "005930", 10)
            store.close()

        self.assertEqual(2, analyze.call_count)
        self.assertEqual(2, len(revisions))
        self.assertEqual([revisions[1]["analysis_revision_id"]], result["analysis_revision_ids"])
        self.assertEqual([revisions[0]["analysis_revision_id"]], second["analysis_revision_ids"])
        self.assertTrue(all(
            row["article_revision_id"] == article["article_revision_id"] for row in revisions
        ))
        self.assertTrue(all(row["body_revision_id"] == body_revision_id for row in revisions))
        self.assertEqual("요약", revisions[0]["output"]["summary"])
        self.assertEqual(5, revisions[0]["usage"]["total_tokens"])
        self.assertEqual(2, len(usage))
        self.assertEqual("요약", latest[0]["document"]["summary"])

    def test_ai_projection_revision_and_usage_roll_back_together(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "central.sqlite3"
            store = SQLiteQueryStore(path)
            store.initialize()
            connection = sqlite3.connect(path)
            connection.execute(
                "CREATE TRIGGER reject_ai_usage BEFORE INSERT ON central_documents "
                "WHEN NEW.collection='news_request_usage' "
                "BEGIN SELECT RAISE(ABORT,'usage rejected'); END"
            )
            connection.commit(); connection.close()
            with self.assertRaisesRegex(sqlite3.IntegrityError, "usage rejected"):
                store.save_news_ai_results(
                    [{"owner": "005930", "key": "article", "document": {"summary": "요약"}}],
                    [{"analysis_revision_id": "revision", "target_id": "005930",
                      "article_revision_id": "article-revision", "provider": "gemini",
                      "model": "model", "prompt_version": "prompt-v1", "input_hash": "hash",
                      "output": {"summary": "요약"}, "usage": {"total_tokens": 5}}],
                    [{"owner": "2026-09-12", "key": "request", "document": {"total_tokens": 5}}],
                )
            self.assertEqual([], store.load_documents("news_ai", "005930"))
            self.assertEqual([], store.load_news_history("ai", target="005930"))
            store.close()

    def test_mixed_bundle_freezes_actual_raw_status_omissions_and_unicode_spans(self):
        from datetime import datetime
        from kiwoom_monitor.domain.news_ai_contract import freeze_news_ai_input, validate_news_ai_input
        when = datetime.fromisoformat("2026-10-29T09:00:00.125+09:00")
        articles = [{"identity": "a", "title": "A", "description": "old A", "link": "https://a", "input_source": {
                        "version": 1, "article_revision_id": "article-A", "article_available_at": 100.25}},
                    {"identity": "b", "title": "B", "description": "  summary B  ", "link": "https://b"},
                    {"identity": "c", "title": "C", "description": "", "link": ""}]
        def fetch(url):
            if url == "https://a": return "😀 raw A"
            raise ValueError("source blocked")
        with patch("kiwoom_monitor.central_server.ai_service.fetch_article_text", side_effect=fetch):
            event = _prepare_events([{"identity": "a", "title": "A", "articles": articles}], "company", now_provider=lambda: when)[0]
        first = "[관련 기사 1/3: A]\n😀 raw A"
        second = "[관련 기사 2/3: B]\nsummary B"
        expected = first + "\n\n" + second
        self.assertEqual(expected, event["body"])
        members = event["_source_members"]
        self.assertEqual(["fulltext", "summary_only", "unavailable"], [member["body_status"] for member in members])
        self.assertEqual([True, True, False], [member["used"] for member in members])
        self.assertEqual([[0, len(first)], [len(first) + 2, len(expected)], None], [member["span"] for member in members])
        self.assertEqual(["😀 raw A", "  summary B  ", ""], [member["raw_body"] for member in members])
        self.assertEqual([when.timestamp()] * 3, [member["observed_at"] for member in members])
        self.assertEqual(100.25, members[0]["source_ref"]["article_available_at"])
        frozen = freeze_news_ai_input("a", "A", expected, event["body_hash"], members, kind="bundle")
        validate_news_ai_input(frozen)
        self.assertEqual("unicode_code_points_half_open", frozen["input_binding"]["span_unit"])

    async def test_actual_bundle_stores_only_representative_raw_body_and_cache_keeps_original_sources(self):
        from datetime import datetime, timedelta
        from kiwoom_monitor.domain.news_observation import stable_document_hash
        with tempfile.TemporaryDirectory() as directory:
            clock = [datetime.fromisoformat("2026-10-29T09:00:00.125+09:00")]
            store = SQLiteQueryStore(Path(directory) / "native.sqlite3", now_provider=lambda: clock[0])
            store.initialize()
            service = CentralAIService(CentralServerSettings("sqlite:///:memory:", "token", gemini_api_key="fixture"), store, now_provider=lambda: clock[0])
            articles = []
            original_bodies = []
            try:
                for name, raw, status in (("A", "original A", "fulltext"), ("B", "summary B", "summary_only")):
                    identity = f"https://origin/{name}"
                    document = {"stock_code": "005930", "identity": identity, "title": name, "description": f"summary {name}",
                                "link": f"https://portal/{name}", "original_link": identity, "published_at": "2026-10-28T08:00:00+09:00"}
                    store.upsert_documents("news_article", [{"owner": "005930", "key": identity, "collector_id": "oracle",
                                                             "collection_scope": "watchlist", "document": document}])
                    revision = store.load_news_history("article", target="005930", identity=identity)[0]
                    receipt = store.save_news_body_revision_receipt({"article_revision_id": revision["article_revision_id"],
                        "body_text": raw, "status": status, "fetched_at": 101.25, "extractor_version": "source-v7"})
                    original_bodies.append(store.load_news_body_revision(receipt["body_revision_id"]))
                    articles.append({key: document[key] for key in ("identity", "title", "description", "link", "original_link", "published_at")})
                    articles[-1]["input_source"] = {"version": 1, "article_revision_id": revision["article_revision_id"],
                        "article_content_hash": revision["content_hash"], "article_available_at": revision["available_at"],
                        "body_revision_id": receipt["body_revision_id"], "body_content_hash": receipt["content_hash"],
                        "body_status": status, "body_fetched_at": receipt["fetched_at"], "body_available_at": receipt["available_at"]}
                def fetch(url):
                    if url.endswith("/A"): return "fetched 😀 A"
                    raise ValueError("B summary fallback")
                expected = "[관련 기사 1/2: A]\nfetched 😀 A\n\n[관련 기사 2/2: B]\nsummary B"
                analysis = AINewsAnalysis("model summary", "긍정", 80, "source reason")
                clock[0] += timedelta(seconds=2)
                with patch("kiwoom_monitor.central_server.ai_service.fetch_article_text", side_effect=fetch), \
                     patch("kiwoom_monitor.central_server.ai_service.analyze_articles", return_value=((analysis,), AIRequestUsage(10, 5, 15))) as provider:
                    event = {"identity": articles[0]["identity"], "title": "A", "articles": articles}
                    first = await service.analyze("005930", "company", "gemini", "model", [event], 2)
                    self.assertEqual((("A", expected),), provider.call_args[0][2])
                    rows = store.load_news_history("body")
                    self.assertEqual(3, len(rows))
                    self.assertEqual("fetched 😀 A", rows[0]["body_text"])
                    self.assertTrue(all("[관련 기사" not in row["body_text"] for row in rows))
                    for old in original_bodies:
                        self.assertEqual(old, store.load_news_body_revision(old["body_revision_id"]))
                    produced = store.load_news_history("inference", target="005930")[0]["document"]
                    self.assertEqual(1, produced["input_envelope_version"])
                    actual = produced["inputs"][0]
                    self.assertEqual(expected, actual["body"])
                    self.assertEqual(["fulltext", "summary_only"], [part["body_status"] for part in actual["source_snapshot"]["members"]])
                    self.assertEqual(["DIFFERENT", "MATCH"], [part["body_reference_status"] for part in actual["input_binding"]["members"]])
                    self.assertEqual(rows[0]["body_revision_id"], actual["input_binding"]["members"][0]["native_body"]["body_revision_id"])
                    self.assertEqual(actual["input_binding"], first["analysis_provenance"][0]["input_binding"])
                    self.assertNotIn("raw_body", json.dumps(first["analysis_provenance"]))
                    clock[0] += timedelta(seconds=19)
                    newer = store.save_news_body_revision_receipt({"article_revision_id": articles[1]["input_source"]["article_revision_id"],
                        "body_text": "summary B", "status": "summary_only", "fetched_at": clock[0].timestamp()})
                    articles[1]["input_source"] = {**articles[1]["input_source"], "body_revision_id": newer["body_revision_id"],
                                                    "body_fetched_at": newer["fetched_at"], "body_available_at": newer["available_at"]}
                    before_cache = store.load_news_history("body")
                    cached = await service.analyze("005930", "company", "gemini", "model", [event], 2)
                    self.assertTrue(cached["cache_hit"])
                    self.assertEqual(first["analysis_provenance"], cached["analysis_provenance"])
                    self.assertEqual(before_cache, store.load_news_history("body"))
                    self.assertEqual(1, provider.call_count)
                    self.assertEqual(1, len(store.load_news_history("inference", target="005930")))
                    self.assertEqual(1, len(store.load_documents("news_request_usage")))
            finally:
                await service.close()
                store.close()


    async def test_inline_body_records_receiver_observation_without_fabricating_source_fetch_or_body_row(self):
        from datetime import datetime
        clock = datetime.fromisoformat("2026-10-29T09:00:00.125+09:00")
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "inline.sqlite3", now_provider=lambda: clock)
            store.initialize()
            service = CentralAIService(CentralServerSettings("sqlite:///:memory:", "token", gemini_api_key="fixture"), store, now_provider=lambda: clock)
            try:
                original = {"identity": "inline", "title": "title", "description": "description", "link": "https://inline", "original_link": ""}
                store.upsert_documents("news_article", [{"owner": "005930", "key": "inline", "collector_id": "oracle",
                                                         "collection_scope": "watchlist", "document": original}])
                article = store.load_news_history("article", target="005930")[0]
                event = {**original, "body": "  provided 😀 body  ", "input_source": {
                    "version": 1, "article_revision_id": article["article_revision_id"],
                    "article_content_hash": article["content_hash"], "article_available_at": article["available_at"]}}
                with patch("kiwoom_monitor.central_server.ai_service.fetch_article_text", side_effect=AssertionError("inline input must not fetch")), \
                     patch("kiwoom_monitor.central_server.ai_service.analyze_articles", return_value=((AINewsAnalysis("summary", "중립", 50, "reason"),), AIRequestUsage())) as provider:
                    result = await service.analyze("005930", "company", "gemini", "model", [event], 1)
                self.assertEqual((("title", "provided 😀 body"),), provider.call_args[0][2])
                self.assertEqual([], store.load_news_history("body", target=article["article_revision_id"]))
                raw = store.load_news_history("inference", target="005930")[0]["document"]["inputs"][0]
                member = raw["source_snapshot"]["members"][0]
                self.assertEqual("  provided 😀 body  ", member["raw_body"])
                self.assertEqual(clock.timestamp(), member["observed_at"])
                self.assertEqual("input_receiver_now_provider", member["clock_source"])
                self.assertEqual("UNKNOWN", member["body_status"])
                self.assertIsNone(member["native_body"])
                self.assertNotIn("body_fetched_at", member["source_ref"])
                self.assertEqual("UNKNOWN", result["analysis_provenance"][0]["status"])
                self.assertEqual(raw["input_binding"], result["analysis_provenance"][0]["input_binding"])
            finally:
                await service.close()
                store.close()


if __name__ == "__main__":
    unittest.main()
