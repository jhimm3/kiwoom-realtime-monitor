from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from kiwoom_monitor.application.news_analysis import assess_stock_news
from kiwoom_monitor.domain.time_basis import KST
from kiwoom_monitor.infrastructure.naver_news import StockNewsItem
from kiwoom_monitor.infrastructure.news_ai import (
    AINewsAnalysis, AICompanyImpact, AIRequestUsage, AIThemeCandidate, analysis_body_hash,
)
from kiwoom_monitor.infrastructure.persistence.news_ai_repository import NewsAIRepository, news_identity
from kiwoom_monitor.infrastructure.persistence.stock_news_repository import StockNewsRepository


class NewsAIRepositoryTests(unittest.TestCase):
    def test_theme_candidates_are_persisted_for_profile_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "news.sqlite3"
            repository = NewsAIRepository(path)
            item = StockNewsItem(
                "지역 개발 기사", "복수 기업 참여", "https://example.com/theme", "",
                datetime.now(UTC), assess_stock_news("테스트", "지역 개발 기사", "복수 기업 참여"),
            )
            StockNewsRepository(path).upsert("000001", (item,))
            repository.save(
                "000001", item, "openai", "model", analysis_body_hash("테스트", "본문"),
                AINewsAnalysis(
                    "요약", "긍정", 80, "근거", (), (), "산업·정책", (),
                    (AIThemeCandidate("호남클러스터", 84, "복수 상장사 참여"),),
                ),
            )

            values = repository.list_theme_suggestions()

            self.assertEqual(1, len(values))
            self.assertEqual("호남클러스터", values[0].raw_theme_name)
            self.assertEqual("복수 상장사 참여", values[0].evidence)
            self.assertEqual("지역 개발 기사", values[0].article_title)
            self.assertEqual(item.published_at, values[0].article_published_at)
            self.assertEqual("https://example.com/theme", values[0].article_url)

    def test_same_url_reuses_company_specific_impact_for_another_stock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = NewsAIRepository(Path(directory) / "monitor.sqlite3")
            item = StockNewsItem(
                "공동 기사", "두 회사 계약", "https://example.com/shared", "https://example.com/shared",
                datetime.now(UTC), assess_stock_news("A회사", "공동 기사", "두 회사 계약"),
            )
            repository.save(
                "000001", item, "gemini", "model", analysis_body_hash("A회사", "본문"),
                AINewsAnalysis(
                    "공동 요약", "긍정", 90, "A회사 수주", (), (), "수주·계약",
                    (AICompanyImpact("A회사", "긍정", 90, "수주"), AICompanyImpact("B회사", "부정", 75, "경쟁 심화")),
                ),
            )

            loaded = repository.load_many("000002", (item,), "B회사")

            shared = loaded[news_identity(item)].analysis
            self.assertEqual("공동 요약", shared.summary)
            self.assertEqual("부정", shared.outlook)
            self.assertEqual("경쟁 심화", shared.reason)

    def test_request_log_counts_one_batch_as_one_request(self) -> None:
        class UtcHostDateTime(datetime):
            def astimezone(self, tz=None):
                return super().astimezone(UTC if tz is None else tz)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            now = UtcHostDateTime(2026, 10, 18, 0, 30, tzinfo=KST)
            repository = NewsAIRepository(path, now_provider=lambda: now)
            # An earlier KST day belongs to its own usage budget, even though
            # the simulated UTC host still sees October 17 for both requests.
            NewsAIRepository(path, now_provider=lambda: now - timedelta(hours=1)).log_request(
                "gemini", "flash-lite", "batch", 1, 1, AIRequestUsage(1, 2, 3),
            )
            repository.log_request("gemini", "flash-lite", "batch", 10, 27, AIRequestUsage(66570, 4200, 70770))

            self.assertEqual(1, repository.daily_count())
            self.assertEqual((1, 66570, 4200, 70770), repository.daily_usage())
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(
                    [("2026-10-17T23:30:00+09:00",), ("2026-10-18T00:30:00+09:00",)],
                    connection.execute("SELECT requested_at FROM news_ai_requests ORDER BY id").fetchall(),
                )

    def test_load_many_reads_saved_results_in_one_batch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = NewsAIRepository(Path(directory) / "monitor.sqlite3")
            items = tuple(
                StockNewsItem(
                    f"기사 {index}", "공급계약", f"https://example.com/{index}",
                    f"https://example.com/{index}", datetime.now(UTC),
                    assess_stock_news("테스트", f"기사 {index}", "공급계약"),
                )
                for index in range(2)
            )
            for index, item in enumerate(items):
                repository.save(
                    "000000", item, "gemini", "model", analysis_body_hash("테스트", "본문"),
                    AINewsAnalysis(f"요약 {index}", "긍정", 80, "이유", (), (), "수주·계약"),
                )

            loaded = repository.load_many("000000", items)

            self.assertEqual({news_identity(item) for item in items}, set(loaded))
            self.assertEqual("요약 0", loaded[news_identity(items[0])].analysis.summary)
            self.assertTrue(all(value.provenance == {"status": "UNKNOWN"} for value in loaded.values()))
            repository.save("000000", items[0], "gemini", "model", analysis_body_hash("테스트", "본문"),
                            loaded[news_identity(items[0])].analysis,
                            provenance={"status": "ACCEPTED", "analysis_revision_id": "old-reference"})
            repository.save("000000", items[0], "gemini", "model", analysis_body_hash("테스트", "본문"),
                            loaded[news_identity(items[0])].analysis)
            self.assertEqual({"status": "UNKNOWN"}, repository.load("000000", items[0]).provenance)

    def test_old_prompt_results_remain_visible_but_are_marked_stale(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = NewsAIRepository(Path(directory) / "monitor.sqlite3")
            item = StockNewsItem(
                "시장 기사", "삼성전자 단순 나열", "https://example.com/old", "",
                datetime.now(UTC), assess_stock_news("삼성전자", "시장 기사", "삼성전자 주가"),
            )
            repository.save(
                "005930", item, "gemini", "model", "legacy-hash",
                AINewsAnalysis("시장 전체 요약", "부정", 70, "시장 하락"),
            )

            stored = repository.load_many("005930", (item,), "삼성전자")[news_identity(item)]
            self.assertFalse(stored.uses_current_prompt)


class NewsAIInferenceBatchTests(unittest.TestCase):
    @staticmethod
    def _batch(*, summary="first", inference_id="00000000-0000-0000-0000-000000000001", computed_at=100.25):
        from kiwoom_monitor.domain.news_ai_contract import (
            ANALYSIS_PROMPT_VERSION, NewsAIResultEnvelope, freeze_news_ai_input,
            news_ai_article_input, news_ai_source_member, news_ai_analysis_payload,
        )
        from kiwoom_monitor.domain.news_observation import NEWS_ANALYSIS_SCHEMA_VERSION
        items = tuple(StockNewsItem(f"제목 {index}", "원기사 요약", f"https://example.com/{index}", "", None,
                                   assess_stock_news("A회사", f"제목 {index}", "원기사 요약")) for index in range(2))
        results = tuple(AINewsAnalysis(f"{summary} {index}", "긍정", 81, "계약 근거", ("원문 근거",), (), "수주·계약",
                                     (AICompanyImpact("A회사", "긍정", 81, "계약"),),
                                     (AIThemeCandidate("신규산업", 82, "명시 근거"),)) for index in range(2))
        bodies = ("실제 공급자 입력 0 😀\n끝", "실제 공급자 입력 1\n끝")
        hashes = tuple(analysis_body_hash("A회사", body) for body in bodies)
        inputs = []
        for index, (item, body, input_hash) in enumerate(zip(items, bodies, hashes, strict=True)):
            article = news_ai_article_input(item)
            member = news_ai_source_member(0, article, body, "FETCHED", input_text=body,
                                          span=(0, len(body)), observed_at=90.125, clock_source="injected")
            inputs.append(freeze_news_ai_input(news_identity(item), item.title, body, input_hash, [member], kind="single"))
        raw = {"input_envelope_version": 1, "result_schema_version": "news-ai-value-v1",
               "inference_id": inference_id, "target_id": "000001", "stock_name": "A회사", "provider": "gemini",
               "model": "model", "prompt_version": ANALYSIS_PROMPT_VERSION, "schema_version": NEWS_ANALYSIS_SCHEMA_VERSION,
               "computed_at": computed_at, "inputs": inputs, "outputs": [news_ai_analysis_payload(result) for result in results]}
        return items, NewsAIResultEnvelope(results, hashes, "gemini", "model", AIRequestUsage(10, 20, 30), len(items), inference=raw)

    def test_grouped_batch_counts_all_three_source_articles_including_omitted_member(self) -> None:
        import json
        from dataclasses import replace
        from kiwoom_monitor.domain.news_ai_contract import (
            ANALYSIS_PROMPT_VERSION, NewsAIResultEnvelope, freeze_news_ai_input,
            news_ai_article_input, news_ai_source_member, news_ai_analysis_payload,
        )
        from kiwoom_monitor.domain.news_observation import NEWS_ANALYSIS_SCHEMA_VERSION
        representatives = tuple(StockNewsItem(title, "대표 기사 요약", f"https://example.com/group-{index}", "", None,
                                              assess_stock_news("A회사", title, "대표 기사 요약"))
                                for index, title in enumerate(("첫 사건 대표", "두 번째 사건 대표")))
        omitted = StockNewsItem("첫 사건 생략 기사", "원문 요약도 보존", "https://example.com/group-0-omitted", "", None,
                                assess_stock_news("A회사", "첫 사건 생략 기사", "원문 요약도 보존"))
        bodies = ("[기사 1]\n첫 사건의 실제 사용 원문 α\n끝", "[기사 1]\n둘째 사건의 실제 사용 원문")
        source_bodies = ("첫 사건의 실제 사용 원문 α\n끝", "둘째 사건의 실제 사용 원문")
        frozen_inputs = []
        for index, (item, body, source_body) in enumerate(zip(representatives, bodies, source_bodies, strict=True)):
            start = body.index(source_body)
            members = [news_ai_source_member(0, news_ai_article_input(item), source_body, "FETCHED",
                                            input_text=source_body, span=(start, start + len(source_body)),
                                            observed_at=90.125, clock_source="injected")]
            if index == 0:
                members.append(news_ai_source_member(1, news_ai_article_input(omitted), "생략되었지만 보존할 원문",
                                                     "original-omitted-status", observed_at=91.5, clock_source="injected"))
            frozen_inputs.append(freeze_news_ai_input(news_identity(item), item.title, body,
                analysis_body_hash("A회사", body), members, kind="original-event-bundle-kind"))
        results = (AINewsAnalysis("첫 사건 분석", "긍정", 80, "첫 근거"),
                   AINewsAnalysis("둘째 사건 분석", "중립", 60, "둘째 근거"))
        raw = {"input_envelope_version": 1, "result_schema_version": "news-ai-value-v1",
               "inference_id": "00000000-0000-0000-0000-000000000003", "target_id": "000001", "stock_name": "A회사",
               "provider": "gemini", "model": "model", "prompt_version": ANALYSIS_PROMPT_VERSION,
               "schema_version": NEWS_ANALYSIS_SCHEMA_VERSION, "computed_at": 100.25,
               "inputs": frozen_inputs, "outputs": [news_ai_analysis_payload(value) for value in results]}
        envelope = NewsAIResultEnvelope(results, tuple(source["input_hash"] for source in frozen_inputs),
                                       "gemini", "model", AIRequestUsage(10, 20, 30), 3, inference=raw)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "grouped.sqlite3"
            repository = NewsAIRepository(path)
            # Fixed independent counts: two events, three articles, two provider
            # spans and one omitted article retained as source evidence.
            self.assertEqual(2, len(representatives))
            self.assertEqual(3, envelope.article_count)
            self.assertEqual([2, 1], [len(source["source_snapshot"]["members"]) for source in frozen_inputs])
            self.assertEqual([True, False], [member["used"] for member in frozen_inputs[0]["source_snapshot"]["members"]])
            for wrong_count in (2, 4):
                with self.subTest(article_count=wrong_count), self.assertRaises(ValueError):
                    repository.save_batch("000001", representatives, replace(envelope, article_count=wrong_count))
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM news_ai_inferences").fetchone()[0])
                self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM stock_news_ai").fetchone()[0])
            receipt = repository.save_batch("000001", representatives, envelope)
            self.assertTrue(receipt.published)
            self.assertEqual([0, 1], [value["inference_ref"]["input_index"] for value in receipt.provenance])
            with closing(sqlite3.connect(path)) as connection:
                rows = connection.execute("SELECT document_json FROM news_ai_inferences").fetchall()
                self.assertEqual(1, len(rows))
                self.assertEqual(2, connection.execute("SELECT COUNT(*) FROM stock_news_ai").fetchone()[0])
                stored = json.loads(rows[0][0])
            self.assertEqual(raw, stored)
            stored_omitted = stored["inputs"][0]["source_snapshot"]["members"][1]
            self.assertFalse(stored_omitted["used"])
            self.assertIsNone(stored_omitted["span"])
            self.assertEqual("생략되었지만 보존할 원문", stored_omitted["raw_body"])
            self.assertEqual("original-omitted-status", stored_omitted["body_status"])
            self.assertEqual("original-event-bundle-kind", stored["inputs"][0]["source_snapshot"]["kind"])
            self.assertEqual("첫 사건 분석", repository.load("000001", representatives[0]).analysis.summary)
            self.assertEqual("둘째 사건 분석", repository.load("000001", representatives[1]).analysis.summary)


    def test_local_batch_stores_raw_once_and_all_own_shared_refs_atomically(self) -> None:
        import json
        import hashlib
        from contextlib import contextmanager
        from unittest.mock import patch
        from kiwoom_monitor.infrastructure.persistence.sqlite_connections import sqlite_transaction
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "news.sqlite3"
            now = datetime(2026, 10, 29, 12, 0, tzinfo=KST)
            repository = NewsAIRepository(path, now_provider=lambda: now)
            items, envelope = self._batch()
            statements = []
            @contextmanager
            def traced(*args, **kwargs):
                with sqlite_transaction(*args, **kwargs) as connection:
                    connection.set_trace_callback(statements.append)
                    yield connection
            with patch("kiwoom_monitor.infrastructure.persistence.news_ai_repository.sqlite_transaction", traced):
                receipt = repository.save_batch("000001", items, envelope)
            canonical = json.dumps(envelope.inference, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            expected_hash = hashlib.sha256(canonical).hexdigest()
            self.assertTrue(receipt.published)
            self.assertEqual(now.timestamp(), receipt.available_at)
            self.assertEqual({"inference_id": envelope.inference["inference_id"], "content_hash": expected_hash}, receipt.inference_ref)
            with closing(sqlite3.connect(path)) as connection:
                rows = connection.execute("SELECT content_hash,computed_at,available_at,document_json FROM news_ai_inferences").fetchall()
                self.assertEqual([(expected_hash, 100.25, now.timestamp(), json.dumps(envelope.inference, ensure_ascii=False, sort_keys=True, separators=(",", ":")))], rows)
                self.assertEqual(2, connection.execute("SELECT COUNT(*) FROM stock_news_ai").fetchone()[0])
                self.assertEqual(2, connection.execute("SELECT COUNT(*) FROM news_ai_shared").fetchone()[0])
                self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM news_ai_requests").fetchone()[0])
            self.assertEqual(1, sum(statement.lstrip().upper().startswith(("SELECT", "WITH")) for statement in statements))
            self.assertEqual(1, sum(statement.lstrip().upper().startswith("BEGIN") for statement in statements))
            self.assertEqual(1, sum(statement.lstrip().upper().startswith("COMMIT") for statement in statements))
            for index, item in enumerate(items):
                own = repository.load("000001", item)
                shared = repository.load_many("000002", (item,), "A회사")[news_identity(item)]
                self.assertEqual(receipt.provenance[index], own.provenance)
                self.assertEqual(own.provenance, shared.provenance)
                self.assertEqual("REPORTED", own.provenance["status"])
                self.assertEqual("repository_recorded", own.provenance["origin"])
                self.assertNotIn("analysis_revision_id", own.provenance)
                self.assertNotIn("source_snapshot", own.provenance)
                self.assertEqual(index, own.provenance["inference_ref"]["input_index"])
                self.assertEqual(100.25, own.provenance["computed_at"])
                self.assertEqual(now.timestamp(), own.provenance["available_at"])
                self.assertEqual(envelope.inference["inputs"][index]["input_binding"], own.provenance["input_binding"])

    def test_second_shared_write_failure_rolls_back_raw_and_every_projection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "news.sqlite3"
            repository = NewsAIRepository(path)
            items, envelope = self._batch()
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("CREATE TRIGGER fail_second_shared BEFORE INSERT ON news_ai_shared WHEN NEW.identity='https://example.com/1' BEGIN SELECT RAISE(ABORT,'second shared rejected'); END")
            with self.assertRaisesRegex(sqlite3.IntegrityError, "second shared rejected"):
                repository.save_batch("000001", items, envelope)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual([0, 0, 0], [connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                                            for table in ("news_ai_inferences", "stock_news_ai", "news_ai_shared")])

    def test_replayed_old_ack_returns_first_receipt_without_rewinding_current(self) -> None:
        from contextlib import contextmanager
        from unittest.mock import patch
        from kiwoom_monitor.infrastructure.persistence.sqlite_connections import sqlite_transaction
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "news.sqlite3"
            now = [datetime(2026, 10, 29, 12, 0, tzinfo=KST)]
            repository = NewsAIRepository(path, now_provider=lambda: now[0])
            items, original = self._batch()
            first = repository.save_batch("000001", items, original)
            now[0] += timedelta(seconds=20)
            _, newer = self._batch(summary="newer", inference_id="00000000-0000-0000-0000-000000000002", computed_at=120.25)
            repository.save_batch("000001", items, newer)
            statements = []
            @contextmanager
            def traced(*args, **kwargs):
                with sqlite_transaction(*args, **kwargs) as connection:
                    connection.set_trace_callback(statements.append)
                    yield connection
            now[0] += timedelta(seconds=20)
            with patch("kiwoom_monitor.infrastructure.persistence.news_ai_repository.sqlite_transaction", traced):
                replayed = repository.save_batch("000001", items, original)
            self.assertFalse(replayed.published)
            self.assertEqual(first.provenance, replayed.provenance)
            self.assertEqual(first.available_at, replayed.available_at)
            self.assertEqual(1, sum(statement.lstrip().upper().startswith("SELECT") for statement in statements))
            self.assertFalse(any("INSERT INTO stock_news_ai" in statement or "INSERT INTO news_ai_shared" in statement for statement in statements))
            self.assertEqual("newer 0", repository.load("000001", items[0]).analysis.summary)
            self.assertEqual("newer 0", repository.load_many("000002", (items[0],), "A회사")[news_identity(items[0])].analysis.summary)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(2, connection.execute("SELECT COUNT(*) FROM news_ai_inferences").fetchone()[0])

    def test_late_old_body_batch_preserves_matching_projection_and_keeps_both_raw_inferences(self):
        from copy import deepcopy
        from dataclasses import replace
        from kiwoom_monitor.domain.news_ai_contract import freeze_news_ai_input, news_ai_article_input, news_ai_source_member
        from kiwoom_monitor.domain.news_observation import stable_document_hash
        from kiwoom_monitor.infrastructure.persistence.stock_news_repository import StockNewsRepository
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "news.sqlite3"
            repository, article_repository = NewsAIRepository(path), StockNewsRepository(path)
            captured, original = self._batch()
            def versioned(envelope, revision):
                raw = deepcopy(envelope.inference)
                items = []
                for index, item in enumerate(captured):
                    body = raw["inputs"][index]["body"]
                    source = {"version": 1, "article_revision_id": f"article-{index}",
                              "body_revision_id": f"body-{revision}-{index}", "body_content_hash": stable_document_hash({"body": body})}
                    item = replace(item, input_source=source)
                    member = news_ai_source_member(0, news_ai_article_input(item), body, "fulltext", input_text=body, span=(0, len(body)))
                    raw["inputs"][index] = freeze_news_ai_input(news_identity(item), item.title, body,
                        raw["inputs"][index]["input_hash"], [member], kind="single")
                    items.append(item)
                return tuple(items), replace(envelope, inference=raw)
            old_items, old_envelope = versioned(original, "old")
            _, new_value = self._batch(summary="new current", inference_id="00000000-0000-0000-0000-000000000002")
            new_items, new_envelope = versioned(new_value, "new")
            article_repository.upsert("000001", new_items)
            current = repository.save_batch("000001", new_items, new_envelope)
            late = repository.save_batch("000001", old_items, old_envelope)
            self.assertFalse(late.published)
            self.assertEqual((), late.published_indices)
            for index, item in enumerate(new_items):
                stored = repository.load("000001", item)
                self.assertEqual(f"new current {index}", stored.analysis.summary)
                self.assertEqual(current.provenance[index], stored.provenance)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(2, connection.execute("SELECT COUNT(*) FROM news_ai_inferences").fetchone()[0])
            replay = repository.save_batch("000001", old_items, old_envelope)
            self.assertFalse(replay.published)
            self.assertEqual(late.provenance, replay.provenance)
            self.assertEqual(late.available_at, replay.available_at)

    def test_global_article_shared_fallback_and_own_destinations_are_guarded_independently(self):
        from copy import deepcopy
        from dataclasses import replace
        from kiwoom_monitor.domain.news_ai_contract import (
            freeze_news_ai_input, news_ai_article_input, news_ai_source_member, news_ai_analysis_payload,
        )
        from kiwoom_monitor.domain.news_observation import stable_document_hash
        from kiwoom_monitor.infrastructure.persistence.stock_news_repository import StockNewsRepository
        def versioned(target, name, revision, label, inference_id, impacts):
            from uuid import NAMESPACE_URL, uuid5
            inference_id = str(uuid5(NAMESPACE_URL, inference_id))
            captured, envelope = self._batch(summary=label, inference_id=inference_id)
            raw = deepcopy(envelope.inference)
            raw.update(target_id=target, stock_name=name)
            items, hashes, results = [], [], tuple(replace(value, company_impacts=impacts) for value in envelope.results)
            raw["outputs"] = [news_ai_analysis_payload(value) for value in results]
            for index, item in enumerate(captured):
                body = raw["inputs"][index]["body"]
                item = replace(item, input_source={"version": 1, "article_revision_id": f"GLOBAL-{index}",
                    "body_revision_id": f"body-{revision}-{index}", "body_content_hash": stable_document_hash({"body": body})})
                member = news_ai_source_member(0, news_ai_article_input(item), body, "fulltext", input_text=body, span=(0, len(body)))
                input_hash = analysis_body_hash(name, body)
                raw["inputs"][index] = freeze_news_ai_input(news_identity(item), item.title, body,
                    input_hash, [member], kind="single")
                items.append(item)
                hashes.append(input_hash)
            return tuple(items), replace(envelope, results=results, body_hashes=tuple(hashes), inference=raw, stock_name=name)
        for mode in ("applicable-fallback", "not-applicable", "own-first"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "news.sqlite3"
                repository, articles = NewsAIRepository(path), StockNewsRepository(path)
                impacts = (AICompanyImpact("A회사", "긍정", 81, "A current"),)
                if mode != "not-applicable":
                    impacts += (AICompanyImpact("B 회사", "긍정", 82, "B shared current"),)
                current_items, current = versioned("000001", "A회사", "new", "A current", "source-A-current", impacts)
                articles.upsert("000001", current_items)
                articles.upsert("000002", current_items)
                repository.save_batch("000001", current_items, current)
                with closing(sqlite3.connect(path)) as connection:
                    shared_before = connection.execute("SELECT * FROM news_ai_shared ORDER BY identity").fetchall()
                if mode == "own-first":
                    own_items, own_current = versioned("000002", "B회사", "new", "B own current", "source-B-current", ())
                    repository.save_batch("000002", own_items, own_current)
                before = repository.load_many("000002", current_items, "B-회사")
                old_items, older = versioned("000002", "B회사", "old", "B previous", "source-B-previous",
                    (AICompanyImpact("B회사", "부정", 77, "B previous"),))
                receipt = repository.save_batch("000002", old_items, older)
                from kiwoom_monitor.infrastructure.central_content_sync import CentralContentSyncService
                wire_path = Path(directory) / "delayed-copy" / "news.sqlite3"
                wire = NewsAIRepository(wire_path)
                StockNewsRepository(wire_path).upsert("000002", old_items)
                wire.save_batch("000002", old_items, replace(older, inference=None, provenance=receipt.provenance))
                copied = CentralContentSyncService._read_news(wire_path)
                for value in copied["news_ai"]:
                    self.assertNotIn("stock_name", value["document"])
                    value["target_name"] = "B-회사"  # Native bulk-read target context; not an AI/provenance field.
                CentralContentSyncService._write_news(path, {name: copied[name] for name in ("news_ai", "news_ai_shared")})
                after = repository.load_many("000002", current_items, "B-회사")
                self.assertEqual((), receipt.shared_published_indices)
                with closing(sqlite3.connect(path)) as connection:
                    self.assertEqual(shared_before, connection.execute("SELECT * FROM news_ai_shared ORDER BY identity").fetchall())
                if mode == "not-applicable":
                    self.assertEqual({}, before)
                    self.assertEqual((0, 1), receipt.published_indices)
                    self.assertTrue(all(value.analysis.summary.startswith("B previous") for value in after.values()))
                else:
                    self.assertEqual(before, after)
                    self.assertEqual((), receipt.published_indices)
                with closing(sqlite3.connect(path)) as connection:
                    self.assertEqual(3 if mode == "own-first" else 2,
                        connection.execute("SELECT COUNT(*) FROM news_ai_inferences").fetchone()[0])

    def test_same_inference_id_with_changed_raw_is_rejected_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "news.sqlite3"
            repository = NewsAIRepository(path)
            items, original = self._batch()
            first = repository.save_batch("000001", items, original)
            _, altered = self._batch(summary="changed")
            with self.assertRaisesRegex(ValueError, "원본이 기존 기록과 다릅니다"):
                repository.save_batch("000001", items, altered)
            self.assertEqual("first 0", repository.load("000001", items[0]).analysis.summary)
            self.assertEqual(first.provenance[0], repository.load("000001", items[0]).provenance)

    def test_actual_target_provider_identity_order_input_and_output_mismatch_are_rejected(self) -> None:
        from copy import deepcopy
        from dataclasses import replace
        mutations = (
            lambda raw: raw.update(target_id="000002"),
            lambda raw: raw.update(provider="openai"),
            lambda raw: raw.update(model="other-model"),
            lambda raw: raw["inputs"][0].update(identity="other-identity"),
            lambda raw: raw["inputs"].reverse(),
            lambda raw: raw["inputs"][0].update(input_hash="other-input-hash"),
            lambda raw: raw["outputs"][0].update(summary="other-output"),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "news.sqlite3"
            repository = NewsAIRepository(path)
            items, envelope = self._batch()
            for mutation in mutations:
                with self.subTest(mutation=mutations.index(mutation)):
                    raw = deepcopy(envelope.inference)
                    mutation(raw)
                    with self.assertRaises(ValueError):
                        repository.save_batch("000001", items, replace(envelope, inference=raw))
            with self.assertRaises(ValueError):
                repository.save_batch("000001", items, replace(envelope, results=envelope.results[:1]))
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual([0, 0, 0], [connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                                            for table in ("news_ai_inferences", "stock_news_ai", "news_ai_shared")])

    def test_native_reference_only_batch_keeps_remote_values_and_creates_no_local_raw(self) -> None:
        from dataclasses import replace
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "news.sqlite3"
            repository = NewsAIRepository(path)
            items, envelope = self._batch()
            references = tuple({"version": 1, "status": "ACCEPTED", "origin": "native_accepted",
                                "analysis_revision_id": f"native-analysis-{index}", "target_id": "000001",
                                "article_revision_id": f"native-article-{index}", "body_revision_id": f"native-body-{index}",
                                "provider": "gemini", "model": "model", "prompt_version": "native-prompt", "schema_version": "native-schema",
                                "input_hash": envelope.body_hashes[index], "output_hash": f"native-output-{index}",
                                "computed_at": 20.5, "available_at": 21.75, "requester": {"kind": "direct"},
                                "inference_ref": {"inference_id": "native-id", "content_hash": "native-hash", "input_index": index}}
                               for index in range(2))
            receipt = repository.save_batch("000001", items, replace(envelope, inference=None, provenance=references))
            self.assertTrue(receipt.published)
            self.assertEqual(references, receipt.provenance)
            for index, item in enumerate(items):
                self.assertEqual(references[index], repository.load("000001", item).provenance)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM news_ai_inferences").fetchone()[0])

    def test_v4_backup_restores_exact_raw_hash_refs_and_first_receipt(self) -> None:
        import json
        from kiwoom_monitor.infrastructure.persistence.news_ai_backup import NewsAIBackupService
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target, backup = root / "source.sqlite3", root / "target.sqlite3", root / "ai.json"
            now = datetime(2026, 10, 29, 12, 0, tzinfo=KST)
            repository = NewsAIRepository(source, now_provider=lambda: now)
            items, envelope = self._batch()
            original = repository.save_batch("000001", items, envelope)
            NewsAIBackupService(source).export_to(backup)
            document = json.loads(backup.read_text(encoding="utf-8"))
            self.assertEqual(4, document["version"])
            self.assertEqual(1, len(document["inferences"]))
            self.assertEqual(envelope.inference, json.loads(document["inferences"][0]["document_json"]))
            self.assertEqual(4, NewsAIBackupService(target).import_from(backup))
            restored = NewsAIRepository(target, now_provider=lambda: now + timedelta(days=1))
            replayed = restored.save_batch("000001", items, envelope)
            self.assertFalse(replayed.published)
            self.assertEqual(original.provenance, replayed.provenance)
            self.assertEqual(original.available_at, replayed.available_at)
            for index, item in enumerate(items):
                self.assertEqual(original.provenance[index], restored.load("000001", item).provenance)
            with closing(sqlite3.connect(source)) as left, closing(sqlite3.connect(target)) as right:
                for table in ("news_ai_inferences", "stock_news_ai", "news_ai_shared"):
                    self.assertEqual(left.execute(f"SELECT * FROM {table}").fetchall(), right.execute(f"SELECT * FROM {table}").fetchall())

    def test_v4_backup_reads_analyses_shared_and_raw_from_one_snapshot(self) -> None:
        import json
        from unittest.mock import patch
        from kiwoom_monitor.infrastructure.persistence.news_ai_backup import NewsAIBackupService
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, backup = root / "source.sqlite3", root / "ai.json"
            repository = NewsAIRepository(source)
            with closing(sqlite3.connect(source)) as connection:
                connection.execute("PRAGMA journal_mode=WAL")
            items, first = self._batch()
            _, newer = self._batch(summary="newer", inference_id="00000000-0000-0000-0000-000000000002")
            repository.save_batch("000001", items, first)
            service = NewsAIBackupService(source)
            real_connect = sqlite3.connect
            injected = []
            class SnapshotConnection(sqlite3.Connection):
                def execute(self, sql, parameters=()):
                    cursor = super().execute(sql, parameters)
                    if sql.startswith("SELECT stock_code,") and not injected:
                        injected.append(True)
                        repository.save_batch("000001", items, newer)
                    return cursor
            def connect(*args, **kwargs):
                return real_connect(*args, **kwargs, factory=SnapshotConnection)
            with patch("kiwoom_monitor.infrastructure.persistence.news_ai_backup.sqlite3.connect", connect):
                service.export_to(backup)
            document = json.loads(backup.read_text(encoding="utf-8"))
            self.assertEqual([True], injected)
            self.assertEqual(["first 0", "first 1"], [row["summary"] for row in document["analyses"]])
            self.assertEqual(["first 0", "first 1"], [row["summary"] for row in document["shared_analyses"]])
            self.assertEqual([first.inference["inference_id"]], [row["inference_id"] for row in document["inferences"]])
            self.assertEqual("newer 0", repository.load("000001", items[0]).analysis.summary)
            NewsAIBackupService.validate_file(backup)

    def test_v4_restore_keeps_historical_prompt_input_hash_and_raw_bytes(self) -> None:
        import hashlib
        import json
        from kiwoom_monitor.domain.news_ai_contract import freeze_news_ai_input
        from kiwoom_monitor.infrastructure.persistence.news_ai_backup import NewsAIBackupService
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target, backup = root / "source.sqlite3", root / "target.sqlite3", root / "ai.json"
            items, envelope = self._batch()
            NewsAIRepository(source).save_batch("000001", items, envelope)
            NewsAIBackupService(source).export_to(backup)
            document = json.loads(backup.read_text(encoding="utf-8"))
            record = document["inferences"][0]
            raw = json.loads(record["document_json"])
            raw["prompt_version"] = "historical-prompt-v0"
            for index, old in enumerate(raw["inputs"]):
                old_hash = "historical-prompt-v0:" + hashlib.sha256(("historical-prompt-v0\0A회사\0" + old["body"]).encode()).hexdigest()
                raw["inputs"][index] = freeze_news_ai_input(old["identity"], old["title"], old["body"], old_hash,
                    old["source_snapshot"]["members"], kind=old["source_snapshot"]["kind"],
                    article_revision_id=old["article_revision_id"], body_revision_id=old["body_revision_id"])
            record["document_json"] = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            record["content_hash"] = hashlib.sha256(record["document_json"].encode()).hexdigest()
            for row in document["analyses"] + document["shared_analyses"]:
                provenance = json.loads(row["provenance_json"])
                source_input = raw["inputs"][provenance["inference_ref"]["input_index"]]
                provenance["prompt_version"] = raw["prompt_version"]
                provenance["input_hash"] = row["body_hash"] = source_input["input_hash"]
                provenance["input_binding"] = source_input["input_binding"]
                provenance["inference_ref"]["content_hash"] = record["content_hash"]
                row["provenance_json"] = json.dumps(provenance, ensure_ascii=False, separators=(",", ":"))
            backup.write_text(json.dumps(document), encoding="utf-8")
            NewsAIBackupService(target).import_from(backup)
            with closing(sqlite3.connect(target)) as connection:
                self.assertEqual((record["content_hash"], record["document_json"]), connection.execute("SELECT content_hash,document_json FROM news_ai_inferences").fetchone())
            restored = NewsAIRepository(target).load("000001", items[0])
            self.assertEqual("historical-prompt-v0", restored.provenance["prompt_version"])
            self.assertEqual(raw["inputs"][0]["input_hash"], restored.body_hash)
            self.assertFalse(restored.uses_current_prompt)

    def test_v4_restore_rejects_index_mismatch_and_rolls_back_immutable_id_collision(self) -> None:
        import json
        from kiwoom_monitor.infrastructure.persistence.news_ai_backup import NewsAIBackupService
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target, backup, corrupt = (root / name for name in ("source.sqlite3", "target.sqlite3", "ai.json", "corrupt.json"))
            source_repository = NewsAIRepository(source)
            items, first = self._batch()
            _, second = self._batch(summary="second", inference_id="00000000-0000-0000-0000-000000000002")
            source_repository.save_batch("000001", items, first)
            source_repository.save_batch("000001", items, second)
            NewsAIBackupService(source).export_to(backup)
            document = json.loads(backup.read_text(encoding="utf-8"))
            metadata = json.loads(document["analyses"][0]["provenance_json"])
            metadata["inference_ref"]["input_index"] = 1
            document["analyses"][0]["provenance_json"] = json.dumps(metadata)
            corrupt.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(ValueError):
                NewsAIBackupService.validate_file(corrupt)
            destination = NewsAIRepository(target)
            _, conflict = self._batch(summary="destination", inference_id="00000000-0000-0000-0000-000000000002")
            destination.save_batch("000001", items, conflict)
            with self.assertRaisesRegex(ValueError, "백업 원본이 기존 기록과 다릅니다"):
                NewsAIBackupService(target).import_from(backup)
            self.assertEqual("destination 0", destination.load("000001", items[0]).analysis.summary)
            with closing(sqlite3.connect(target)) as connection:
                self.assertEqual([(conflict.inference["inference_id"],)], connection.execute("SELECT inference_id FROM news_ai_inferences").fetchall())

    def test_schema7_empty_install_and_failed_upgrade_leave_legacy_rows_unchanged(self) -> None:
        from unittest.mock import patch
        from kiwoom_monitor.infrastructure.persistence import news_schema as schema
        from kiwoom_monitor.infrastructure.persistence.schema_migrations import SQLiteMigration, SQLiteMigrationRunner
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fresh, old = root / "fresh.sqlite3", root / "v6.sqlite3"
            schema.initialize_news_schema(fresh)
            with closing(sqlite3.connect(fresh)) as connection:
                self.assertEqual(7, connection.execute("SELECT MAX(version) FROM news_schema_migrations").fetchone()[0])
                self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM news_ai_inferences").fetchone()[0])
            with closing(sqlite3.connect(old)) as connection:
                SQLiteMigrationRunner(connection, "news_schema_migrations").apply(tuple(
                    SQLiteMigration(version, name, function) for version, name, function in (
                        (1, schema.NEWS_SCHEMA_BASELINE_NAME, schema._apply_v1_baseline),
                        (2, schema.NEWS_ACCOUNT_SCOPE_NAME, schema._apply_v2_account_scope),
                        (3, schema.NEWS_LINK_TOMBSTONE_NAME, schema._apply_v3_link_tombstones),
                        (4, schema.NEWS_AI_THEME_CANDIDATES_NAME, schema._apply_v4_ai_theme_candidates),
                        (5, schema.NEWS_AI_PROVENANCE_NAME, schema._apply_v5_ai_provenance),
                        (6, schema.NEWS_INPUT_SOURCE_NAME, schema._apply_v6_input_source),
                    )))
                connection.execute("INSERT INTO stock_news_ai(stock_code,identity,provider,model,summary,outlook,confidence,reason,body_hash,analyzed_at) VALUES('000001','legacy','p','m','old','중립',50,'old','oldhash','2020-01-01T10:00:00+09:00')")
                connection.commit()
                before = connection.execute("SELECT * FROM stock_news_ai").fetchall()
                self.assertIsNone(before[0][-1])
            original_record = SQLiteMigrationRunner.record_applied
            def fail_record(runner, version, name):
                if version == 7:
                    raise RuntimeError("after raw DDL before migration receipt")
                return original_record(runner, version, name)
            with patch.object(SQLiteMigrationRunner, "record_applied", fail_record):
                with self.assertRaisesRegex(RuntimeError, "after raw DDL"):
                    schema.initialize_news_schema(old)
            with closing(sqlite3.connect(old)) as connection:
                self.assertEqual(6, connection.execute("SELECT MAX(version) FROM news_schema_migrations").fetchone()[0])
                self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='news_ai_inferences'").fetchone())
                self.assertEqual(before, connection.execute("SELECT * FROM stock_news_ai").fetchall())
            schema.initialize_news_schema(old)
            schema.initialize_news_schema(old)
            with closing(sqlite3.connect(old)) as connection:
                self.assertEqual(before, connection.execute("SELECT * FROM stock_news_ai").fetchall())
                self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM news_ai_inferences").fetchone()[0])



if __name__ == "__main__":
    unittest.main()
