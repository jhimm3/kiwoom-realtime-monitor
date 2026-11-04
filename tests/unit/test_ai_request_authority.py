"""Independent fake-provider / real-native AI requester ownership oracles.

Uses no external provider and never touches an operational database.
"""
from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from contextlib import closing
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.central_server.ai_service import AIJobResultError, CentralAIService
from kiwoom_monitor.central_server.config import CentralServerSettings
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.news_jobs import NewsJobRunner
from kiwoom_monitor.infrastructure.news_ai import AINewsAnalysis, AIRequestUsage, analysis_body_hash
from kiwoom_monitor.domain.news_observation import stable_document_hash


class AIRequestAuthorityTests(unittest.IsolatedAsyncioTestCase):
    native_rejection_error = sqlite3.IntegrityError
    settlement_error_type = "IntegrityError"
    previous_schema_version = 20

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "authority.sqlite3"
        self.clock = datetime(2026, 10, 23, 9, tzinfo=timezone(timedelta(hours=9)))
        self.store = self._make_store(self.path)
        self.store.initialize()
        self.service = CentralAIService(
            CentralServerSettings("sqlite:///:memory:", "token", gemini_api_key="fake-key"),
            self.store, now_provider=lambda: self.clock,
        )
        self.runner = NewsJobRunner(self.store, ai_service=self.service, now_provider=lambda: self.clock)
        self.entered, self.release = threading.Event(), threading.Event()
        self.release.set()
        self.calls = []
        self.failure = None
        self.gates = [self.release]
        self.tasks = []
        self.provider = patch("kiwoom_monitor.central_server.ai_service.analyze_articles", side_effect=self._provider)
        self.provider.start()

    async def asyncTearDown(self):
        for gate in self.gates:
            gate.set()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.runner.close()
        await self.service.close()
        self.provider.stop()
        self.store.close()
        self.temp.cleanup()

    def _make_store(self, path):
        return SQLiteQueryStore(path, now_provider=lambda: self.clock)

    def _peer(self):
        return SQLiteQueryStore(self.path, now_provider=lambda: self.clock)

    def _reject_native(self, name, table, event, condition, message):
        self._sql(f"CREATE TRIGGER {name} BEFORE {event} ON {table} WHEN {condition} "
                  f"BEGIN SELECT RAISE(ABORT,'{message}'); END")

    def _provider(self, settings, stock_name, inputs, **_kwargs):
        self.calls.append((settings.provider, settings.model, stock_name, inputs))
        self.entered.set()
        if not self.release.wait(5):
            raise RuntimeError("fake provider gate not released")
        if self.failure is not None:
            raise self.failure
        return ((AINewsAnalysis("unchanged output", "긍정", 80, "independent reason"),), AIRequestUsage(3, 2, 5))

    async def _until(self, predicate):
        async def wait():
            while not predicate():
                await asyncio.sleep(0)
        await asyncio.wait_for(wait(), 2)

    async def _shared_requesters(self, count=2):
        # _accepted precedes preparation and attachment. This gate proves both
        # requesters joined the very same produced task before release.
        await self._until(lambda: len(self.service._running) == 1 and
                          len(next(iter(self.service._running.values())).requests) == count)
        self.assertEqual(count, len(next(iter(self.service._running.values())).requests))

    def _task(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.append(task)
        return task

    def _sql(self, statement, parameters=()):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            cursor = connection.execute(statement, parameters)
            return cursor.fetchall() if cursor.description else []

    def _seed(self, title="initial title", *, version="prompt:gemini:model"):
        self.store.upsert_documents("news_article", [{
            "owner": "005930", "key": "same-identity", "collector_id": "oracle",
            "collection_scope": "watchlist", "document": {
                "stock_code": "005930", "stock_name": "삼성전자", "identity": "same-identity",
                "title": title, "description": "original summary", "link": "https://invalid.test/news",
                "published_at": "2026-10-23T08:00:00+09:00",
            },
        }])
        article = self.store.load_news_history("article", target="005930")[0]
        body = self.store.save_news_body_revision({
            "article_revision_id": article["article_revision_id"], "status": "fulltext",
            "body_text": "same exact provider body", "extractor_version": "oracle-v1",
            "fetched_at": self.clock.timestamp(),
        })
        with patch("kiwoom_monitor.central_server.database_news_jobs.time", return_value=self.clock.timestamp()):
            self.assertEqual(1, self.store.enqueue_news_ai_jobs([{
                "stock_code": "005930", "identity": "same-identity", "target_id": "005930",
                "processing_version": version, "payload": {
                    "stock_name": "삼성전자", "provider": "gemini", "model": "model", "article_count": 1,
                    "event": {"identity": "same-identity", "title": title},
                },
            }]))
        jobs = self.store.claim_news_jobs(now=self.clock.timestamp() + 1, preferred_stage="AI")
        self.assertEqual(1, len(jobs))
        self.assertEqual("AI", jobs[0]["stage"])
        self.assertEqual(body, jobs[0]["payload"]["body_revision_id"])
        return jobs[0]

    def _job_row(self, key):
        return self._sql("SELECT * FROM central_news_jobs WHERE job_key=?", (key,))

    def _next_body_job(self, previous, text):
        body = self.store.save_news_body_revision({
            "article_revision_id": previous["article_revision_id"], "status": "fulltext",
            "body_text": text, "extractor_version": "oracle-v1", "fetched_at": self.clock.timestamp(),
        })
        with patch("kiwoom_monitor.central_server.database_news_jobs.time", return_value=self.clock.timestamp()):
            self.assertEqual(1, self.store.enqueue_news_ai_jobs([{
                "stock_code": "005930", "identity": "same-identity", "target_id": "005930",
                "processing_version": previous["processing_version"], "payload": {
                    "stock_name": "삼성전자", "provider": "gemini", "model": "model", "article_count": 1,
                    "event": {"identity": "same-identity", "title": "initial title"},
                },
            }]))
        jobs = self.store.claim_news_jobs(now=self.clock.timestamp() + 1, preferred_stage="AI")
        self.assertEqual(1, len(jobs))
        self.assertEqual(body, jobs[0]["payload"]["body_revision_id"])
        return jobs[0]

    def _manual_event(self, job):
        body = self.store.load_news_body_revision(job["payload"]["body_revision_id"])
        return {"identity": "same-identity", "title": "initial title", "body": body["body_text"],
                "_raw_body": body["body_text"], "body_status": body["status"], "_native_body": body,
                "article_revision_id": job["article_revision_id"], "body_revision_id": body["body_revision_id"]}

    async def test_waiting_automatic_job_rechecks_a_judgment_accepted_before_provider_start(self):
        first = self._seed()
        publication_entered, publication_release = asyncio.Event(), asyncio.Event()
        self.gates.append(publication_release)
        original_publish = self.service._publish_job
        async def held_publish(stock, prepared, outcome, job):
            if job["job_key"] == first["job_key"]:
                publication_entered.set()
                await publication_release.wait()
            return await original_publish(stock, prepared, outcome, job)
        with patch.object(self.service, "_publish_job", side_effect=held_publish):
            old = self._task(self.runner._run_ai(first))
            await asyncio.wait_for(publication_entered.wait(), 2)
            self.assertEqual(1, len(self.calls))
            await self.service._execution_lock.acquire()
            try:
                newer = self._next_body_job(first, "corrected body awaiting explicit reanalysis")
                waiting = self._task(self.runner._run_ai(newer))
                await self._until(lambda: len(self.service._running) == 2)
                publication_release.set()
                self.assertTrue(await old)  # Real native acceptance occurs while the other job waits.
                original_projection = self.store.load_document("news_ai", "005930", "same-identity")
            finally:
                publication_release.set()
                self.service._execution_lock.release()
            self.assertEqual("", await waiting)
        self.assertEqual(1, len(self.calls))
        self.assertEqual(1, len(self.store.load_documents("news_request_usage", limit=10)))
        self.assertEqual(1, len(self.store.load_news_history("ai", target="005930")))
        self.assertEqual(original_projection, self.store.load_document("news_ai", "005930", "same-identity"))
        self.assertEqual([("COMPLETED", "", "manual_reanalysis_required")], self._sql(
            "SELECT state,output_ref,error FROM central_news_jobs WHERE job_key=?", (newer["job_key"],)))

    async def test_skipped_automatic_request_does_not_cancel_shared_explicit_manual_inference(self):
        first = self._seed()
        old_ref = await self.runner._run_ai(first)
        newer = self._next_body_job(first, "new BODY explicitly requested by user")
        await self.service._execution_lock.acquire()
        try:
            automatic = self._task(self.runner._run_ai(newer))
            manual = self._task(self.service.analyze("005930", "삼성전자", "gemini", "model",
                [self._manual_event(newer)], 1))
            await self._shared_requesters()
        finally:
            self.service._execution_lock.release()
        auto_ref, manual_response = await asyncio.gather(automatic, manual)
        self.assertEqual("", auto_ref)
        self.assertEqual(2, len(self.calls))  # Original initial analysis and one explicit new analysis.
        self.assertEqual(2, len(self.store.load_documents("news_request_usage", limit=10)))
        refs = {row["analysis_revision_id"] for row in self.store.load_news_history("ai", target="005930")}
        self.assertEqual({old_ref, manual_response["analysis_revision_ids"][0]}, refs)
        self.assertEqual([("COMPLETED", "", "manual_reanalysis_required")], self._sql(
            "SELECT state,output_ref,error FROM central_news_jobs WHERE job_key=?", (newer["job_key"],)))

    async def test_late_old_body_publication_retains_matching_current_and_both_immutable_results(self):
        old_job = self._seed()
        old_body = self.store.load_news_body_revision(old_job["payload"]["body_revision_id"])
        publication_entered, publication_release = asyncio.Event(), asyncio.Event()
        self.gates.append(publication_release)
        original_publish = self.service._publish_direct
        async def held_publish(stock, prepared, outcome):
            if prepared[0]["body_revision_id"] == old_body["body_revision_id"]:
                publication_entered.set()
                await publication_release.wait()
            return await original_publish(stock, prepared, outcome)
        with patch.object(self.service, "_publish_direct", side_effect=held_publish):
            older = self._task(self.service.analyze("005930", "삼성전자", "gemini", "model", [self._manual_event(old_job)], 1))
            await asyncio.wait_for(publication_entered.wait(), 2)
            newer_job = self._next_body_job(old_job, "new BODY accepted before old result publication")
            newer = await self.service.analyze("005930", "삼성전자", "gemini", "model", [self._manual_event(newer_job)], 1)
            matching = self.store.load_document("news_ai", "005930", "same-identity")
            publication_release.set()
            old_response = await older
        self.assertEqual(matching, self.store.load_document("news_ai", "005930", "same-identity"))
        self.assertEqual(newer["analysis_revision_ids"][0], json.loads(matching["document"]["provenance_json"])["analysis_revision_id"])
        self.assertEqual(2, len(self.store.load_news_history("ai", target="005930")))
        self.assertEqual(2, len(self.store.load_news_history("inference", target="005930")))
        self.assertEqual(2, len(self.store.load_documents("news_request_usage", limit=10)))
        self.assertNotEqual(old_response["analysis_revision_ids"], newer["analysis_revision_ids"])
        self.assertEqual(old_body, self.store.load_news_body_revision(old_body["body_revision_id"]))
        # Both native acceptances use the same injected time; version lineage, not a later timestamp, chooses current.
        self.assertEqual({self.clock.timestamp()}, {row["available_at"] for row in self.store.load_news_history("ai", target="005930")})

    async def test_default_catalog_copy_uses_native_body_cut_and_cannot_rewind_source_own_or_shared(self):
        from dataclasses import replace
        from kiwoom_monitor.domain.news_ai_contract import AICompanyImpact, native_news_source_sequence
        from kiwoom_monitor.infrastructure.central_content_sync import CentralContentSyncService
        from kiwoom_monitor.infrastructure.persistence.news_ai_repository import NewsAIRepository
        from kiwoom_monitor.infrastructure.persistence.stock_news_repository import StockNewsRepository
        identity = "https://fixture.invalid/same-article"
        self.store.upsert_documents("news_article", [{"owner": "005930", "key": identity,
            "collector_id": "oracle", "collection_scope": "watchlist", "document": {
                "stock_code": "005930", "stock_name": "삼성전자", "identity": identity,
                "title": "same article", "description": "same summary", "link": identity, "original_link": identity,
                "published_at": "2026-10-23T08:00:00+09:00", "relevant": 1, "category": "실적",
                "outlook": "호재 가능성", "reason": "원규칙", "relevance_score": 8, "outlook_score": 3,
            }}])
        article = self.store.load_news_history("article", target="005930", identity=identity)[0]
        def event(text):
            ref = self.store.save_news_body_revision({"article_revision_id": article["article_revision_id"],
                "body_text": text, "status": "fulltext", "extractor_version": "oracle", "fetched_at": self.clock.timestamp()})
            body = self.store.load_news_body_revision(ref)
            return {"identity": identity, "title": "same article", "body": text, "_raw_body": text,
                    "body_status": "fulltext", "_native_body": body, "article_revision_id": article["article_revision_id"], "body_revision_id": ref}
        def with_impact(*args, **kwargs):
            values, usage = self._provider(*args, **kwargs)
            return ((replace(values[0], company_impacts=(AICompanyImpact("삼성전자", "긍정", 80, "original impact"),)),), usage)
        def snapshot():
            return {name: self.store.load_documents(name, limit=1000) for name in ("news_article", "news_ai", "news_ai_shared")}
        def copy_shared():
            value = self.store.load_document("news_ai", "005930", identity)
            self.store.upsert_documents("news_ai_shared", [{"owner": "shared", "key": identity, "document": deepcopy(value["document"])}])
        native_store = self.store
        class NativeClient:
            def capabilities(self): return {"journal_v2_sync": False, "journal_news_links_v2": False}
            def load_all(self, collection, **kwargs): return native_store.load_documents(collection, limit=10000)
            def upsert(self, collection, values):
                native_store.upsert_documents(collection, values)
                return len(values)
        old_pc = Path(self.temp.name) / "old-pc-copy" / "news.sqlite3"
        NewsAIRepository(old_pc)
        stale_push = CentralContentSyncService(NativeClient())
        stale_push._load_push_manifest(old_pc)  # Captured before another sync's successful ACK/manifest.
        with patch("kiwoom_monitor.central_server.ai_service.analyze_articles", side_effect=with_impact):
            old_event = event("old actual BODY")
            await self.service.analyze("005930", "삼성전자", "gemini", "model", [old_event], 1)
            copy_shared()
            seed_sync = CentralContentSyncService(NativeClient())
            seed_sync.pull(Path(self.temp.name) / "unused-main", old_pc)
            seed_sync.push(Path(self.temp.name) / "unused-main", old_pc)
            old = snapshot()
            new_event = event("new actual BODY")
            response = await self.service.analyze("005930", "삼성전자", "gemini", "model", [new_event], 1)
            copy_shared()
            current = snapshot()
        native_before = {name: self.store.load_news_history(name, target="005930" if name != "body" else article["article_revision_id"])
                         for name in ("article", "body", "ai", "inference")}
        current_ref = response["analysis_revision_ids"][0]
        old_cut, new_cut = (packet["news_article"][0]["native_source_cut"] for packet in (old, current))
        self.assertEqual("삼성전자", current["news_ai"][0]["target_name"])
        self.assertEqual(native_news_source_sequence(old_cut)["authority"], native_news_source_sequence(new_cut)["authority"])
        self.assertLess(native_news_source_sequence(old_cut)["sequence"], native_news_source_sequence(new_cut)["sequence"])
        stale_push.push(Path(self.temp.name) / "unused-main", old_pc)
        forged = deepcopy(old["news_article"])
        original_collector = CentralContentSyncService._read_news(old_pc)["news_article"][0]["collector_id"]
        for value in forged:
            value.update(collector_id=original_collector, collection_scope="local_projection_sync")
            value["document"]["input_source_json"] = json.dumps({**old_cut,
                "native_sequence": {**old_cut["native_sequence"], "sequence": 999999}})
        self.store.upsert_documents("news_article", forged)
        self.assertEqual(current_ref, json.loads(self.store.load_document("news_ai", "005930", identity)["document"]["provenance_json"])["analysis_revision_id"])
        verified = json.loads(self.store.load_documents("news_article")[0]["document"]["input_source_json"])
        self.assertEqual(new_event["body_revision_id"], verified["body_revision_id"])
        self.assertEqual(new_cut["native_sequence"]["sequence"], verified["native_sequence"]["sequence"])
        self.assertEqual(native_before, {name: self.store.load_news_history(name, target="005930" if name != "body" else article["article_revision_id"])
                                        for name in native_before})
        class SnapshotClient:
            def __init__(self, values): self.values = values
            def capabilities(self): return {"journal_v2_sync": False, "journal_news_links_v2": False}
            def load_all(self, collection, **kwargs): return deepcopy(self.values.get(collection, []))
        path = Path(self.temp.name) / "pc-copy" / "news.sqlite3"
        NewsAIRepository(path)
        delayed = CentralContentSyncService(SnapshotClient(old))
        delayed._load_pull_cursors(path)  # Request began before the matching newer snapshot was applied.
        CentralContentSyncService(SnapshotClient(current)).pull(Path(self.temp.name) / "unused-main", path)
        delayed.pull(Path(self.temp.name) / "unused-main", path)
        item = StockNewsRepository(path).load("005930")[0]
        self.assertEqual(new_event["body_revision_id"], item.input_source["body_revision_id"])
        local = NewsAIRepository(path)
        self.assertEqual(current_ref, local.load("005930", item).provenance["analysis_revision_id"])
        self.assertEqual(current_ref, local.load_many("000002", (item,), "삼성전자")[identity].provenance["analysis_revision_id"])
        with closing(sqlite3.connect(path)) as connection:
            self.assertEqual(new_event["body_revision_id"], json.loads(connection.execute(
                "SELECT provenance_json FROM news_ai_shared WHERE identity=?", (identity,)).fetchone()[0])["body_revision_id"])
        foreign = deepcopy(old)
        foreign["news_article"][0]["native_source_cut"]["native_sequence"]["authority"] = "different-native-authority"
        CentralContentSyncService._write_news(path, foreign)
        unknown = StockNewsRepository(path).load("005930")[0]
        self.assertEqual("UNKNOWN", unknown.input_source["status"])
        self.assertEqual(new_event["body_revision_id"], unknown.input_source["body_revision_id"])
        self.assertEqual(current_ref, local.load("005930", unknown).provenance["analysis_revision_id"])

    async def test_unexecuted_automatic_reanalysis_completes_without_result_or_usage_and_keeps_first_reason(self):
        job = self._seed()
        previous = {"summary": "original judgment", "body_hash": "old-body", "provider": "gemini", "model": "model"}
        self.store.upsert_documents("news_ai", [{"owner": "005930", "key": "same-identity", "document": previous}])
        before = self.store.load_documents("news_ai", "005930")
        self.assertEqual("", await self.runner._run_ai(job))
        self.assertEqual([], self.calls)
        self.assertEqual([], self.store.load_documents("news_request_usage", limit=10))
        self.assertEqual([], self.store.load_news_history("ai", target="005930"))
        self.assertEqual(before, self.store.load_documents("news_ai", "005930"))
        row = self._job_row(job["job_key"])
        self.assertEqual([("COMPLETED", "", "manual_reanalysis_required")], self._sql(
            "SELECT state,output_ref,error FROM central_news_jobs WHERE job_key=?", (job["job_key"],)))
        self.clock += timedelta(seconds=30)
        self.assertEqual("", await self.runner._run_ai(job))
        self.assertEqual(row, self._job_row(job["job_key"]))
        self.assertEqual([], self.calls)
        # A preferred stage orders ready work; this fixture also queued one BODY.
        fallback = self.store.claim_news_jobs(now=self.clock.timestamp(), preferred_stage="AI")
        self.assertEqual([("BODY", job["article_revision_id"], 1)], [
            (item["stage"], item["article_revision_id"], item["attempts"]) for item in fallback])
        self.assertEqual(row, self._job_row(job["job_key"]))
        self.assertEqual([], self._sql(
            "SELECT job_key FROM central_news_jobs WHERE stage='AI' AND state IN ('PENDING','RUNNING')"))

    async def test_manual_request_after_automatic_skip_can_retry_and_failure_preserves_original_judgment(self):
        job = self._seed()
        previous = {"summary": "original judgment", "body_hash": "old-body", "provider": "gemini", "model": "model"}
        self.store.upsert_documents("news_ai", [{"owner": "005930", "key": "same-identity", "document": previous}])
        await self.runner._run_ai(job)
        before = self.store.load_documents("news_ai", "005930")
        self.failure = RuntimeError("new provider failure")
        with self.assertRaisesRegex(RuntimeError, "new provider failure"):
            await self.service.analyze("005930", "삼성전자", "gemini", "model",
                [{"identity": "same-identity", "title": "manual request", "body": "new body"}], 1)
        self.assertEqual(1, len(self.calls))
        self.assertEqual(before, self.store.load_documents("news_ai", "005930"))
        self.failure = None
        response = await self.service.analyze("005930", "삼성전자", "gemini", "model",
            [{"identity": "same-identity", "title": "manual request", "body": "new body"}], 1)
        self.assertEqual(2, len(self.calls))
        self.assertEqual("unchanged output", response["results"][0]["summary"])

    async def test_shared_inference_keeps_each_requesters_exact_article_and_body(self):
        first = self._seed()
        first_body = self.store.load_news_body_revision(first["payload"]["body_revision_id"])
        self.release.clear()
        one = self._task(self.runner._run_ai(first))
        await self._until(self.entered.is_set)
        second = self._seed("revised title with unchanged body")
        two = self._task(self.runner._run_ai(second))
        await self._shared_requesters()
        self.release.set()
        refs = await asyncio.gather(one, two)
        self.assertEqual(1, len(self.calls))
        self.assertEqual((("initial title", "same exact provider body"),), self.calls[0][3])
        revisions = self.store.load_news_history("ai", target="005930")
        self.assertEqual(2, len(revisions))
        self.assertEqual(set(refs), {row["analysis_revision_id"] for row in revisions})
        self.assertEqual({(job["article_revision_id"], job["payload"]["body_revision_id"]) for job in (first, second)},
                         {(row["article_revision_id"], row["body_revision_id"]) for row in revisions})
        self.assertEqual(1, len(self.store.load_documents("news_request_usage", limit=10)))
        self.assertEqual(first_body, self.store.load_news_body_revision(first_body["body_revision_id"]))
        self.assertEqual([("COMPLETED",)], self._sql("SELECT DISTINCT state FROM central_news_jobs WHERE stage='AI'"))
        produced = self.store.load_news_history("inference", target="005930")
        self.assertEqual(1, len(produced))
        inference = produced[0]["document"]
        self.assertEqual(stable_document_hash(inference), produced[0]["content_hash"])
        self.assertEqual("initial title", inference["inputs"][0]["title"])
        self.assertEqual("same exact provider body", inference["inputs"][0]["body"])
        self.assertEqual(first["article_revision_id"], inference["inputs"][0]["article_revision_id"])
        self.assertEqual(first["payload"]["body_revision_id"], inference["inputs"][0]["body_revision_id"])
        self.assertNotEqual(second["article_revision_id"], inference["inputs"][0]["article_revision_id"])
        self.assertEqual({(job["job_key"], job["attempts"]) for job in (first, second)},
                         {(row["usage"]["requester"]["job_key"], row["usage"]["requester"]["attempts"]) for row in revisions})
        self.assertEqual({produced[0]["inference_id"]},
                         {row["usage"]["inference_ref"]["inference_id"] for row in revisions})
        self.assertEqual({0}, {row["usage"]["inference_ref"]["input_index"] for row in revisions})
        frozen = inference["inputs"][0]
        self.assertEqual(1, inference["input_envelope_version"])
        self.assertEqual(first_body["content_hash"], frozen["source_snapshot"]["members"][0]["native_body"]["content_hash"])
        self.assertEqual(first_body["available_at"], frozen["source_snapshot"]["members"][0]["native_body"]["available_at"])
        self.assertTrue(all(row["usage"]["input_binding"] == frozen["input_binding"] for row in revisions))

    async def test_deadline_keeps_running_until_accepted_publication_and_close_finish(self):
        job = self._seed()
        self.release.clear()
        self.runner._ai_timeout = .001
        waiter = self._task(self.runner._run_ai(job))
        await self._until(self.entered.is_set)
        self.assertEqual("", await waiter)
        self.assertEqual([("RUNNING", 1)], self._sql("SELECT state,attempts FROM central_news_jobs WHERE job_key=?", (job["job_key"],)))
        self.assertEqual(1, len(self.service._accepted["gemini"]))
        closing = self._task(self.service.close())
        await self._until(lambda: self.service._closing)
        self.assertFalse(closing.done())
        self.release.set()
        await closing
        self.assertEqual(1, len(self.calls))
        self.assertEqual([("COMPLETED",)], self._sql("SELECT state FROM central_news_jobs WHERE job_key=?", (job["job_key"],)))
        self.assertFalse(self.service._running)

    async def test_distinct_jobs_with_same_exact_pins_share_one_inference_and_keep_two_authorities(self):
        first = self._seed()
        self.release.clear()
        one = self._task(self.runner._run_ai(first))
        await self._until(self.entered.is_set)
        with patch("kiwoom_monitor.central_server.database_news_jobs.time", return_value=self.clock.timestamp()):
            self.assertEqual(1, self.store.enqueue_news_ai_jobs([{
                "stock_code": "005930", "identity": "same-identity", "target_id": "005930",
                "processing_version": "different-job-policy-same-effective-model", "payload": {
                    "stock_name": "삼성전자", "provider": "gemini", "model": "model", "article_count": 1,
                    "event": {"identity": "same-identity", "title": "initial title"},
                },
            }]))
        jobs = self.store.claim_news_jobs(now=self.clock.timestamp() + 1, preferred_stage="AI")
        self.assertEqual(1, len(jobs))
        second = jobs[0]
        self.assertNotEqual(first["job_key"], second["job_key"])
        self.assertEqual(first["article_revision_id"], second["article_revision_id"])
        self.assertEqual(first["payload"]["body_revision_id"], second["payload"]["body_revision_id"])
        two = self._task(self.runner._run_ai(second))
        await self._shared_requesters()
        self.release.set()
        refs = await asyncio.gather(one, two)
        self.assertEqual(2, len(set(refs)))
        self.assertEqual(1, len(self.calls))
        self.assertEqual(1, len(self.store.load_news_history("inference", target="005930")))
        revisions = self.store.load_news_history("ai", target="005930")
        self.assertEqual(2, len(revisions))
        self.assertEqual({first["job_key"], second["job_key"]},
                         {row["usage"]["requester"]["job_key"] for row in revisions})

    async def test_cancelled_queued_waiter_keeps_native_work_owned_through_close(self):
        job = self._seed()
        self.release.clear()
        waiter = self._task(self.runner._run_ai(job))
        await self._until(self.entered.is_set)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertEqual(1, len(self.service._accepted["gemini"]))
        closing = self._task(self.service.close())
        await self._until(lambda: self.service._closing)
        self.assertFalse(closing.done())
        self.release.set()
        await closing
        self.assertEqual([("COMPLETED",)], self._sql("SELECT state FROM central_news_jobs WHERE job_key=?", (job["job_key"],)))
        self.assertEqual(1, len(self.calls))
        self.assertEqual(1, len(self.store.load_news_history("inference", target="005930")))
        self.assertFalse(self.service._accepted["gemini"])
        self.assertFalse(self.service._running)

    async def test_produced_outcome_stays_owned_while_native_publication_waits(self):
        job = self._seed()
        entered, release = threading.Event(), threading.Event()
        self.gates.append(release)
        native = self.store.accept_news_job_result
        def blocked(*args, **kwargs):
            if args[1].get("check_unexecuted_reanalysis") is True:
                receipt = native(*args, **kwargs)
                self.assertEqual("continue", receipt["state"])
                return receipt
            self.assertEqual("AI", args[1]["stage"])
            self.assertEqual(1, len(args[1].get("revisions", ())))
            self.assertIsNotNone(args[1].get("usage_ref"))
            entered.set()
            if not release.wait(5):
                raise RuntimeError("native publication gate not released")
            return native(*args, **kwargs)
        with patch.object(self.store, "accept_news_job_result", side_effect=blocked):
            one = self._task(self.runner._run_ai(job))
            await self._until(entered.is_set)
            self.assertEqual(1, len(self.store.load_documents("news_request_usage", limit=10)))
            self.assertEqual([], self.store.load_documents("news_ai", "005930"))
            self.assertEqual([], self.store.load_news_history("ai", target="005930"))
            two = self._task(self.runner._run_ai(job))
            await self._shared_requesters()
            self.assertEqual(1, len(self.calls))
            self.assertEqual(1, len(self.service._running))
            release.set()
            refs = await asyncio.gather(one, two)
        self.assertEqual(refs[0], refs[1])
        self.assertEqual(1, len(self.calls))
        self.assertEqual(1, len(self.store.load_news_history("ai", target="005930")))

    async def test_stale_attempt_cannot_publish_even_though_usage_is_real(self):
        job = self._seed()
        self.release.clear()
        old = self._task(self.runner._run_ai(job))
        await self._until(self.entered.is_set)
        self.clock += timedelta(seconds=122)
        peer = self._peer()
        try:
            recovered = peer.claim_news_jobs(now=self.clock.timestamp(), preferred_stage="AI")
        finally:
            peer.close()
        self.assertEqual(1, len(recovered))
        new = recovered[0]
        self.assertEqual((job["job_key"], 2), (new["job_key"], new["attempts"]))
        before = self._job_row(job["job_key"])
        entered, release = threading.Event(), threading.Event()
        self.gates.append(release)
        native = self.store.accept_news_job_result
        def block_current(key, result, *, attempts):
            if attempts == 2 and "error" not in result:
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("current owner gate not released")
            return native(key, result, attempts=attempts)
        with patch.object(self.store, "accept_news_job_result", side_effect=block_current):
            current = self._task(self.runner._run_ai(new))
            await self._shared_requesters()
            self.release.set()
            await self._until(entered.is_set)
            self.assertEqual("", await old)
            self.assertEqual(before, self._job_row(job["job_key"]))
            self.assertEqual([], self.store.load_documents("news_ai", "005930"))
            self.assertEqual([], self.store.load_news_history("ai", target="005930"))
            self.assertEqual(1, len(self.store.load_documents("news_request_usage", limit=10)))
            release.set()
            current_ref = await current
        self.assertTrue(current_ref)
        self.assertEqual(1, len(self.calls))
        self.assertEqual([("COMPLETED", 2, current_ref)], self._sql(
            "SELECT state,attempts,output_ref FROM central_news_jobs WHERE job_key=?", (job["job_key"],)))

    async def test_usage_and_terminal_ack_loss_keep_first_receipt_and_original_error(self):
        job = self._seed()
        save, accept = self.store.save_news_ai_results, self.store.accept_news_job_result
        lost = {"usage": False, "terminal": False}
        terminal_error = ConnectionError("AI terminal committed, ACK lost")
        def usage_ack(*args, **kwargs):
            value = save(*args, **kwargs)
            if not lost["usage"]:
                lost["usage"] = True
                raise ConnectionError("usage committed, ACK lost")
            return value
        def terminal_ack(*args, **kwargs):
            value = accept(*args, **kwargs)
            if args[1].get("check_unexecuted_reanalysis") is True:
                self.assertEqual("continue", value["state"])
                return value
            if not lost["terminal"]:
                self.assertEqual("completed", value["state"])
                lost["terminal"] = True
                raise terminal_error
            return value
        with patch.object(self.store, "save_news_ai_results", side_effect=usage_ack), \
                patch.object(self.store, "accept_news_job_result", side_effect=terminal_ack), \
                patch.object(self.store, "finish_news_job", wraps=self.store.finish_news_job) as finish:
            with self.assertRaises(AIJobResultError) as caught:
                await self.runner._run_ai(job)
        self.assertIs(terminal_error, caught.exception.original_error)
        self.assertEqual("already_completed", caught.exception.receipt["state"])
        self.assertEqual(0, finish.call_count)
        before = self._job_row(job["job_key"])
        self.clock += timedelta(seconds=17)
        duplicate = self.store.accept_news_job_result(job["job_key"], {"stage": "AI", "error": "late error"}, attempts=1)
        self.assertEqual(caught.exception.receipt, duplicate)
        self.assertEqual(before, self._job_row(job["job_key"]))
        self.assertEqual(1, len(self.store.load_documents("news_request_usage", limit=10)))
        self.assertEqual(1, len(self.store.load_news_history("ai", target="005930")))
        self.assertEqual(1, len(self.store.load_news_history("inference", target="005930")))

    async def test_terminal_failure_rolls_back_projection_and_revision_preserving_usage(self):
        job = self._seed()
        self._reject_native("reject_terminal", "central_news_jobs", "UPDATE",
                            "NEW.stage='AI' AND NEW.state='COMPLETED'", "independent terminal rejected")
        with self.assertRaises(AIJobResultError) as caught:
            await self.runner._run_ai(job)
        self.assertIn("independent terminal rejected", str(caught.exception.original_error))
        self.assertEqual("pending", caught.exception.receipt["state"])
        self.assertEqual([], self.store.load_documents("news_ai", "005930"))
        self.assertEqual([], self.store.load_news_history("ai", target="005930"))
        self.assertEqual(1, len(self.store.load_documents("news_request_usage", limit=10)))
        self.assertEqual([("PENDING", 1, self.clock.timestamp() + 2)], self._sql(
            "SELECT state,attempts,next_retry_at FROM central_news_jobs WHERE job_key=?", (job["job_key"],)))

    async def test_failed_usage_thread_is_drained_before_original_pending_error_receipt(self):
        job = self._seed()
        self._reject_native("reject_usage", "central_documents", "INSERT",
                            "NEW.collection='news_request_usage'", "independent usage rejected")
        entered, release = threading.Event(), threading.Event()
        self.gates.append(release)
        native = self.store.save_news_ai_results
        def blocked(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("usage gate not released")
            return native(*args, **kwargs)
        with patch.object(self.store, "save_news_ai_results", side_effect=blocked):
            request = self._task(self.runner._run_ai(job))
            await self._until(entered.is_set)
            closing = self._task(self.service.close())
            await self._until(lambda: self.service._closing)
            self.assertFalse(closing.done())
            release.set()
            with self.assertRaises(AIJobResultError) as caught:
                await request
            await closing
        self.assertIsInstance(caught.exception.original_error, self.native_rejection_error)
        self.assertIn("independent usage rejected", str(caught.exception.original_error))
        self.assertEqual({"state": "pending", "output_ref": ""}, caught.exception.receipt)
        self.assertEqual([], self.store.load_documents("news_request_usage", limit=10))
        self.assertEqual([], self.store.load_news_history("inference", target="005930"))
        self.assertEqual([], self.store.load_documents("news_ai", "005930"))
        self.assertEqual([], self.store.load_news_history("ai", target="005930"))
        self.assertFalse(self.service._accepted["gemini"])

    async def test_actual_provider_failure_keeps_original_third_attempt_failure_policy(self):
        job = self._seed()
        self.failure = RuntimeError("original provider failure")
        for attempt in (1, 2, 3):
            self.assertEqual(attempt, job["attempts"])
            with self.assertRaises(AIJobResultError) as caught:
                await self.runner._run_ai(job)
            self.assertEqual("failed" if attempt == 3 else "pending", caught.exception.receipt["state"])
            if attempt < 3:
                self.clock += timedelta(seconds=(2 ** attempt) + 2)
                jobs = self.store.claim_news_jobs(now=self.clock.timestamp(), preferred_stage="AI")
                self.assertEqual(1, len(jobs))
                self.assertEqual(job["job_key"], jobs[0]["job_key"])
                job = jobs[0]
        self.assertIs(self.failure, caught.exception.original_error)
        self.assertEqual({"state": "failed", "output_ref": ""}, caught.exception.receipt)
        before = self._job_row(job["job_key"])
        self.clock += timedelta(seconds=19)
        replay = self.store.accept_news_job_result(job["job_key"], {"stage": "AI", "error": "replacement"}, attempts=3)
        self.assertEqual({"state": "already_failed", "output_ref": ""}, replay)
        self.assertEqual(before, self._job_row(job["job_key"]))
        self.assertEqual(3, len(self.calls))
        self.assertEqual([("FAILED", 3, "original provider failure", self.clock.timestamp() - 19 + 8)], self._sql(
            "SELECT state,attempts,error,next_retry_at FROM central_news_jobs WHERE job_key=?", (job["job_key"],)))

    async def test_legacy_unlinked_cache_keeps_original_raw_body_fallback_without_fake_revision(self):
        job = self._seed()
        analysis_hash = analysis_body_hash("삼성전자", "same exact provider body")
        self.assertNotEqual(job["input_hash"], analysis_hash)
        self.store.upsert_documents("news_ai", [{
            "owner": "005930", "key": "same-identity", "document": {
                "provider": "gemini", "model": "model", "body_hash": analysis_hash,
                "summary": "legacy result without immutable links",
            },
        }])
        output_ref = await self.runner._run_ai(job)
        self.assertEqual(job["input_hash"], output_ref)
        self.assertEqual([], self.calls)
        self.assertEqual([], self.store.load_news_history("ai", target="005930"))
        self.assertEqual([], self.store.load_documents("news_request_usage", limit=10))
        self.assertEqual([("COMPLETED", output_ref)], self._sql(
            "SELECT state,output_ref FROM central_news_jobs WHERE job_key=?", (job["job_key"],)))

    def _reject_settlement(self, job):
        # Recreate an untouched queued fixture so actual run_once owns claim.
        self._sql("UPDATE central_news_jobs SET state='PENDING',attempts=0 WHERE job_key=?", (job["job_key"],))
        self._reject_native("reject_ai_settlement", "central_news_jobs", "UPDATE",
                            "OLD.stage='AI' AND OLD.state='RUNNING' AND NEW.state IN ('PENDING','FAILED')",
                            "sensitive-native-message")

    async def test_run_once_observes_both_unknown_settlement_errors_without_secret_messages(self):
        class ProviderSentinel(RuntimeError):
            pass
        job = self._seed()
        self._reject_settlement(job)
        self.failure = ProviderSentinel("sensitive-key-token-DSN-article-body")
        with self.assertLogs("kiwoom_monitor.central_server.ai_service", level="ERROR") as logs, \
                patch.object(self.store, "accept_news_job_result", wraps=self.store.accept_news_job_result) as accept, \
                patch.object(self.store, "retry_news_job", wraps=self.store.retry_news_job) as retry:
            self.assertEqual(1, await self.runner.run_once(preferred_stage="AI"))
            await self.service.close()
        self.assertEqual(1, sum(call.args[1].get("check_unexecuted_reanalysis") is True for call in accept.call_args_list))
        self.assertEqual(1, sum("error" in call.args[1] for call in accept.call_args_list))
        self.assertEqual(0, retry.call_count)
        self.assertEqual(1, len(logs.output))
        self.assertIn("source_type=ProviderSentinel", logs.output[0])
        self.assertIn("settlement_type=" + self.settlement_error_type, logs.output[0])
        self.assertIn(job["job_key"], logs.output[0])
        self.assertNotIn("sensitive", logs.output[0])
        self.assertEqual([("RUNNING", 1)], self._sql("SELECT state,attempts FROM central_news_jobs WHERE job_key=?", (job["job_key"],)))
        self.assertEqual([], self.store.load_news_history("ai", target="005930"))
        self.assertFalse(self.service._accepted["gemini"])

    async def test_late_unknown_settlement_is_logged_inside_owner_and_close_drains_it(self):
        class LateProviderSentinel(RuntimeError):
            pass
        job = self._seed()
        self._reject_settlement(job)
        self.failure = LateProviderSentinel("sensitive-provider-message")
        self.runner._ai_timeout = .001
        self.release.clear()
        with self.assertLogs("kiwoom_monitor.central_server.ai_service", level="ERROR") as logs, \
                patch.object(self.store, "accept_news_job_result", wraps=self.store.accept_news_job_result) as accept:
            run = self._task(self.runner.run_once(preferred_stage="AI"))
            await self._until(self.entered.is_set)
            self.assertEqual(1, await run)
            self.assertEqual(1, len(self.service._accepted["gemini"]))
            closing = self._task(self.service.close())
            await self._until(lambda: self.service._closing)
            self.assertFalse(closing.done())
            self.release.set()
            await closing
        self.assertEqual(1, sum(call.args[1].get("check_unexecuted_reanalysis") is True for call in accept.call_args_list))
        self.assertEqual(1, sum("error" in call.args[1] for call in accept.call_args_list))
        self.assertEqual(1, len(logs.output))
        self.assertIn("source_type=LateProviderSentinel", logs.output[0])
        self.assertIn("settlement_type=" + self.settlement_error_type, logs.output[0])
        self.assertNotIn("sensitive", logs.output[0])
        self.assertEqual([("RUNNING", 1)], self._sql("SELECT state,attempts FROM central_news_jobs WHERE job_key=?", (job["job_key"],)))
        self.assertFalse(self.service._running)
        self.assertFalse(self.service._accepted["gemini"])

    async def test_inference_is_immutable_and_generic_usage_sync_cannot_rewrite_it(self):
        job = self._seed()
        saved = []
        native = self.store.save_news_ai_results
        def capture(documents, revisions, usages=None):
            if usages:
                saved.extend(deepcopy(usages))
            return native(documents, revisions, usages)
        with patch.object(self.store, "save_news_ai_results", side_effect=capture):
            await self.runner._run_ai(job)
        before = self.store.load_news_history("inference", target="005930")
        self.assertEqual(1, len(before))
        self.clock += timedelta(seconds=19)
        native([], [], saved)
        self.assertEqual(before, self.store.load_news_history("inference", target="005930"))
        changed = deepcopy(saved[0])
        changed["inference"]["inputs"][0]["title"] = "fabricated inference title"
        changed["document"]["inference_ref"]["content_hash"] = stable_document_hash(changed["inference"])
        with self.assertRaisesRegex(ValueError, "inference ID"):
            native([], [], [changed])
        self.assertEqual(before, self.store.load_news_history("inference", target="005930"))
        self.store.upsert_documents("news_request_usage", [{
            "owner": saved[0]["owner"], "key": saved[0]["key"], "document": {"legacy_sync": True},
        }])
        self.assertEqual(before, self.store.load_news_history("inference", target="005930"))
        self.assertEqual(1, len(self.calls))

    async def test_fresh_inference_rejects_invalid_binding_and_raw_before_commit(self):
        job = self._seed()
        saved = []
        native = self.store.save_news_ai_results
        def capture(documents, revisions, usages=None):
            if usages:
                saved.extend(deepcopy(usages))
            return native(documents, revisions, usages)
        with patch.object(self.store, "save_news_ai_results", side_effect=capture):
            await self.runner._run_ai(job)
        before = self.store.load_news_history("inference", target="005930")
        usage_before = self.store.load_documents("news_request_usage")
        self.assertEqual(1, len(before))
        self.assertEqual(1, len(usage_before))
        cases = (("binding", "^AI input binding differs from the produced source snapshot$"),
                 ("raw", "^AI raw source body differs from its frozen hash$"))
        for mutation, error in cases:
            with self.subTest(mutation=mutation):
                changed = deepcopy(saved[0])
                inference = changed["inference"]
                fresh_id = inference["inference_id"] + "-fresh-invalid-" + mutation
                changed["key"] = fresh_id
                inference["inference_id"] = fresh_id
                inference["usage_key"] = fresh_id
                if mutation == "binding":
                    inference["inputs"][0]["title"] = "fabricated fresh inference title"
                else:
                    inference["inputs"][0]["source_snapshot"]["members"][0]["raw_body"] += " fabricated raw"
                changed["document"]["inference_ref"] = {
                    "inference_id": fresh_id, "content_hash": stable_document_hash(inference),
                }
                with self.assertRaisesRegex(ValueError, error):
                    native([], [], [changed])
                self.assertEqual(before, self.store.load_news_history("inference", target="005930"))
                self.assertEqual(usage_before, self.store.load_documents("news_request_usage"))
        self.assertEqual(1, len(self.calls))

    async def test_direct_public_shape_stays_compatible_and_revision_names_actual_inference(self):
        job = self._seed()
        response = await self.service.analyze("005930", "삼성전자", "gemini", "model", [{
            "identity": "same-identity", "title": "initial title", "body": "same exact provider body",
            "article_revision_id": job["article_revision_id"],
            "body_revision_id": job["payload"]["body_revision_id"],
        }], 1)
        self.assertEqual({"provider", "model", "results", "credential_revision", "body_hashes", "usage", "cache_hit", "analysis_revision_ids", "analysis_provenance"}, set(response))
        self.assertEqual({"stock_code", "identity", "provider", "model", "summary", "category", "outlook", "confidence", "reason",
                          "positive_evidence", "negative_evidence", "company_impacts", "theme_candidates", "body_hash", "analyzed_at", "credential_revision"},
                         set(response["results"][0]))
        revisions = self.store.load_news_history("ai", target="005930")
        self.assertEqual(1, len(revisions))
        self.assertEqual("direct", revisions[0]["usage"]["requester"]["kind"])
        self.assertEqual(response["results"][0], revisions[0]["output"])
        reference = response["analysis_provenance"][0]
        self.assertEqual("ACCEPTED", reference["status"])
        for name in ("analysis_revision_id", "target_id", "article_revision_id", "body_revision_id",
                     "provider", "model", "prompt_version", "schema_version", "input_hash",
                     "computed_at", "available_at"):
            self.assertEqual(revisions[0][name], reference[name], name)
        self.assertEqual(revisions[0]["usage"]["inference_ref"], reference["inference_ref"])
        self.assertEqual(stable_document_hash(revisions[0]["output"]), reference["output_hash"])
        first_current = self.store.load_document("news_ai", "005930", "same-identity")
        projected = deepcopy(first_current["document"])
        self.assertEqual(reference, json.loads(projected.pop("provenance_json")))
        self.assertEqual("native_accepted", reference["origin"])
        self.assertEqual(response["results"][0], projected)
        self.assertTrue({"body", "title", "prompt", "api_key", "access_token"}.isdisjoint(reference))
        self.clock += timedelta(seconds=19)
        duplicate = self.store.save_news_ai_results([{
            "owner": "005930", "key": "same-identity", "document": deepcopy(revisions[0]["output"]),
        }], [deepcopy(revisions[0])])
        self.assertEqual([reference], duplicate)
        self.assertEqual(first_current, self.store.load_document("news_ai", "005930", "same-identity"))
        after_duplicate = self.store.load_news_history("ai", target="005930")
        self.assertEqual(revisions, after_duplicate)
        self.assertEqual(1, len(after_duplicate))
        self.assertEqual(reference["available_at"], duplicate[0]["available_at"])
        for pins in ({}, {"article_revision_id": job["article_revision_id"],
                          "body_revision_id": job["payload"]["body_revision_id"]}):
            with self.subTest(pinned=bool(pins)), patch.object(self.store, "find_news_ai_revision",
                    side_effect=AssertionError("metadata cache must reuse its exact stored envelope")):
                cached = await self.service.analyze("005930", "삼성전자", "gemini", "model", [{
                    "identity": "same-identity", "title": "initial title", "body": "same exact provider body", **pins,
                }], 1)
                self.assertTrue(cached["cache_hit"])
                self.assertEqual(response["results"], cached["results"])
                self.assertEqual([reference], cached["analysis_provenance"])
        self.assertEqual(1, len(self.calls))
        produced = self.store.load_news_history("inference", target="005930")
        self.assertEqual(produced[0]["inference_id"], revisions[0]["usage"]["inference_ref"]["inference_id"])
        self.assertEqual([("RUNNING",)], self._sql("SELECT state FROM central_news_jobs WHERE job_key=?", (job["job_key"],)))

    async def test_native_rejects_fabricated_requester_attempt_before_projection(self):
        job = self._seed()
        native = self.store.accept_news_job_result
        def tamper(key, result, *, attempts):
            if "revisions" in result:
                result = deepcopy(result)
                result["revisions"][0]["usage"]["requester"]["attempts"] = attempts + 1
            return native(key, result, attempts=attempts)
        with patch.object(self.store, "accept_news_job_result", side_effect=tamper):
            with self.assertRaises(AIJobResultError) as caught:
                await self.runner._run_ai(job)
        self.assertIn("native 작업", str(caught.exception.original_error))
        self.assertEqual("pending", caught.exception.receipt["state"])
        self.assertEqual([], self.store.load_documents("news_ai", "005930"))
        self.assertEqual([], self.store.load_news_history("ai", target="005930"))
        self.assertEqual(1, len(self.store.load_news_history("inference", target="005930")))


    async def test_generic_ai_copies_cannot_claim_native_origin_or_keep_old_metadata(self):
        job = self._seed()
        await self.runner._run_ai(job)
        native_rows = self.store.load_news_history("ai", target="005930")
        inference_rows = self.store.load_news_history("inference", target="005930")
        first_current = self.store.load_document("news_ai", "005930", "same-identity")
        current = deepcopy(first_current["document"])
        source = json.loads(current["provenance_json"])
        self.assertEqual("native_accepted", source["origin"])
        self.clock += timedelta(seconds=19)
        self.store.upsert_documents("news_ai", [{
            "owner": "005930", "key": "same-identity", "document": deepcopy(current),
        }])
        self.assertEqual(first_current, self.store.load_document("news_ai", "005930", "same-identity"))
        self.assertFalse(self.store.insert_immutable_document("news_ai", "005930", "same-identity", deepcopy(current)))
        self.assertEqual(first_current, self.store.load_document("news_ai", "005930", "same-identity"))
        same_reported_copy = {**current, "provenance_json": json.dumps({
            **source, "origin": "reported_copy", "status": "REPORTED",
            "reported_origin": "native_accepted", "reported_status": "ACCEPTED",
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))}
        self.assertFalse(self.store.insert_immutable_document("news_ai", "005930", "same-identity", same_reported_copy))
        preserved = self.store.load_document("news_ai", "005930", "same-identity")
        self.assertEqual(first_current, preserved)
        self.assertEqual(source["available_at"], json.loads(preserved["document"]["provenance_json"])["available_at"])
        forged = {**source, "analysis_revision_id": "unverified-client-id", "available_at": 999.0}
        copied = {**current, "provenance_json": json.dumps(forged)}
        for operation in ("upsert", "replace", "immutable"):
            with self.subTest(operation=operation):
                value = {"owner": "005930", "key": "same-identity", "document": copied}
                if operation == "upsert":
                    self.store.upsert_documents("news_ai", [value])
                elif operation == "replace":
                    self.store.replace_documents("news_ai", [value])
                else:
                    self.assertTrue(self.store.insert_immutable_document("news_ai", "005930", "immutable-copy", copied))
                    self.assertFalse(self.store.insert_immutable_document("news_ai", "005930", "immutable-copy", copied))
                    with self.assertRaisesRegex(ValueError, "immutable document conflict"):
                        self.store.insert_immutable_document("news_ai", "005930", "immutable-copy", {
                            **copied, "provenance_json": json.dumps({**forged, "available_at": 1000.0}),
                        })
                key = "immutable-copy" if operation == "immutable" else "same-identity"
                stored_record = self.store.load_document("news_ai", "005930", key)
                stored = json.loads(stored_record["document"]["provenance_json"])
                self.assertEqual("reported_copy", stored["origin"])
                self.assertEqual("REPORTED", stored["status"])
                self.assertEqual("native_accepted", stored["reported_origin"])
                self.assertEqual("ACCEPTED", stored["reported_status"])
                self.assertEqual("unverified-client-id", stored["analysis_revision_id"])
                self.assertEqual(999.0, stored["available_at"])
                if operation in {"upsert", "immutable"}:
                    for retry_kind, retry_document in (("original", deepcopy(copied)),
                                                       ("readback", deepcopy(stored_record["document"]))):
                        with self.subTest(retry=retry_kind):
                            self.clock += timedelta(seconds=19)
                            if operation == "upsert":
                                self.store.upsert_documents("news_ai", [{
                                    "owner": "005930", "key": key, "document": retry_document,
                                }])
                            else:
                                self.assertFalse(self.store.insert_immutable_document("news_ai", "005930", key, retry_document))
                            self.assertEqual(stored_record, self.store.load_document("news_ai", "005930", key))
        response = await self.service.analyze("005930", "삼성전자", "gemini", "model", [{
            "identity": "same-identity", "title": "initial title", "body": "same exact provider body",
        }], 1)
        self.assertTrue(response["cache_hit"])
        self.assertEqual("REPORTED", response["analysis_provenance"][0]["status"])
        self.assertEqual("unverified-client-id", response["analysis_provenance"][0]["analysis_revision_id"])
        self.assertEqual(999.0, response["analysis_provenance"][0]["available_at"])
        plain = {name: value for name, value in current.items() if name != "provenance_json"}
        self.store.upsert_documents("news_ai", [{"owner": "005930", "key": "same-identity",
                                                "document": {**plain, "summary": "changed reported result"}}])
        changed = json.loads(self.store.load_document("news_ai", "005930", "same-identity")["document"]["provenance_json"])
        self.assertEqual("UNKNOWN", changed["status"])
        self.assertNotIn("analysis_revision_id", changed)
        self.assertEqual("reported_copy", changed["origin"])
        self.assertEqual(native_rows, self.store.load_news_history("ai", target="005930"))
        self.assertEqual(inference_rows, self.store.load_news_history("inference", target="005930"))
        self.assertEqual(1, len(self.calls))

    async def test_cached_job_metadata_does_not_label_another_response_output_accepted(self):
        job = self._seed()
        event = {"identity": "same-identity", "title": "initial title", "body": "same exact provider body",
                 "article_revision_id": job["article_revision_id"],
                 "body_revision_id": job["payload"]["body_revision_id"]}
        original = await self.service.analyze("005930", "삼성전자", "gemini", "model", [event], 1)
        event["body_hash"] = original["body_hashes"][0]
        reference = original["analysis_provenance"][0]
        changed = {**original["results"][0], "summary": "older cached response value"}
        outcome = {"provider": "gemini", "model": "model", "credential_revision": original["credential_revision"],
                   "results": [changed], "body_hashes": original["body_hashes"], "usage": {},
                   "fresh_indices": (), "cache_hit": True}
        response = await self.service._publish_job("005930", [event], outcome, {
            **job, "output_ref_fallback": "original-raw-fallback",
        })
        self.assertEqual("older cached response value", response["results"][0]["summary"])
        self.assertEqual([{"status": "UNKNOWN"}], response["analysis_provenance"])
        self.assertEqual({"state": "completed", "output_ref": reference["analysis_revision_id"]}, response["job_receipt"])
        self.assertEqual(original["results"][0], self.store.load_news_history("ai", target="005930")[0]["output"])
        self.assertEqual(1, len(self.calls))

    async def test_previous_schema_migrates_without_backfill_or_rewriting_existing_rows(self):
        from kiwoom_monitor.central_server.central_schema import central_schema_migrations
        path = Path(self.temp.name) / "previous.sqlite3"
        previous = self._make_store(path)
        plan = central_schema_migrations("sqlite" if self.previous_schema_version == 20 else "postgres")
        with patch("kiwoom_monitor.central_server.database.central_schema_migrations", return_value=plan[:self.previous_schema_version]):
            previous.initialize()
        previous.upsert_documents("news_request_usage", [{"owner": "legacy", "key": "old", "document": {"unknown": True}}])
        before = previous.load_documents("news_request_usage")
        previous.initialize()
        self.assertEqual(before, previous.load_documents("news_request_usage"))
        self.assertEqual([], previous.load_news_history("inference", target="005930"))
        previous.initialize()
        self.assertEqual(before, previous.load_documents("news_request_usage"))
        previous.close()

    async def test_body_receipt_and_legacy_id_share_native_owner_and_preserve_first_row_values(self):
        job = self._seed()
        self.clock += timedelta(seconds=11)
        receipt = self.store.save_news_body_revision_receipt({"article_revision_id": job["article_revision_id"],
            "body_text": "new representative source only", "status": "summary_only", "extractor_version": "source-v7", "fetched_at": 102.125})
        row = self.store.load_news_body_revision(receipt["body_revision_id"])
        self.assertEqual({name: row[name] for name in receipt}, receipt)
        self.assertEqual((102.125, self.clock.timestamp()), (receipt["fetched_at"], receipt["available_at"]))
        self.assertEqual("new representative source only", row["body_text"])
        self.assertNotIn("body_text", receipt)
        self.clock += timedelta(seconds=19)
        self.assertEqual(receipt, {name: self.store.load_news_body_revision(receipt["body_revision_id"])[name] for name in receipt})
        legacy = self.store.save_news_body_revision({"article_revision_id": job["article_revision_id"],
            "body_text": "legacy source", "status": "fulltext", "fetched_at": 103.25})
        self.assertIsInstance(legacy, str)
        self.assertEqual("legacy source", self.store.load_news_body_revision(legacy)["body_text"])


if __name__ == "__main__":
    unittest.main()
