from __future__ import annotations

import asyncio
import sqlite3
import time
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from kiwoom_monitor.central_server.ai_service import CentralAIService
from kiwoom_monitor.central_server.config import CentralServerSettings
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.news_jobs import NewsJobRunner
from kiwoom_monitor.infrastructure.news_ai import AINewsAnalysis, AIRequestUsage
from tests.unit.test_news_observation_history import _article


class _AI:
    def __init__(self, *, delay: float = 0.0, fail: bool = False) -> None:
        self.delay, self.fail, self.calls = delay, fail, 0

    async def analyze(self, *_args):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("provider failed")
        return {"analysis_revision_ids": ["analysis-1"]}


class NewsJobRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_reclaimed_attempt_cannot_be_finished_or_retried_by_old_runner(self) -> None:
        for old_fails in (False, True):
            for new_finished in (False, True):
                with self.subTest(old_fails=old_fails, new_finished=new_finished), tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "central.sqlite3"
                    first = SQLiteQueryStore(path)
                    first.initialize()
                    first.upsert_documents("news_article", _article())
                    old_wakes = []
                    first.set_news_job_wakeup(lambda: old_wakes.append("unexpected stale wake"))
                    second = SQLiteQueryStore(path)
                    old, new = NewsJobRunner(first), NewsJobRunner(second)
                    old_entered, old_release = asyncio.Event(), asyncio.Event()
                    new_entered, new_release = asyncio.Event(), asyncio.Event()
                    async def old_body(_job):
                        old_entered.set()
                        await old_release.wait()
                        if old_fails:
                            raise RuntimeError("old owner failed")
                        return "old-owner-output"
                    async def new_body(_job):
                        new_entered.set()
                        await new_release.wait()
                        return "new-owner-output"
                    def row():
                        with first._connection() as connection:
                            return connection.execute(
                                "SELECT state,attempts,output_ref,error,next_retry_at,updated_at FROM central_news_jobs"
                            ).fetchone()
                    clock = [time.time() + 1]
                    tasks = []
                    try:
                        with patch("kiwoom_monitor.central_server.database_news_jobs.time", side_effect=lambda: clock[0]), \
                                patch.object(old, "_run_body", side_effect=old_body), \
                                patch.object(new, "_run_body", side_effect=new_body):
                            tasks.append(asyncio.create_task(old.run_once()))
                            await asyncio.wait_for(old_entered.wait(), 2)
                            self.assertEqual(("RUNNING", 1), row()[:2])
                            clock[0] += 121
                            tasks.append(asyncio.create_task(new.run_once()))
                            await asyncio.wait_for(new_entered.wait(), 2)
                            self.assertEqual(("RUNNING", 2), row()[:2])
                            if new_finished:
                                new_release.set()
                                self.assertEqual(1, await tasks[1])
                            expected = row()
                            old_release.set()
                            self.assertEqual(1, await tasks[0])
                            self.assertEqual(expected, row(), "a stale runner changed the current claim")
                            self.assertEqual([], old_wakes)
                            new_release.set()
                            self.assertEqual(1, await tasks[1])
                            self.assertEqual(("COMPLETED", 2, "new-owner-output", ""), row()[:4])
                    finally:
                        old_release.set()
                        new_release.set()
                        await asyncio.gather(*tasks, return_exceptions=True)
                        await old.close()
                        await new.close()
                        first.close()
                        second.close()

    async def test_empty_claim_backoff_is_bounded(self) -> None:
        runner = NewsJobRunner(object(), poll_seconds=1, max_idle_poll_seconds=5)
        runner.run_once = AsyncMock(return_value=0)
        delays = []

        async def capture_wait(awaitable, *, timeout):
            awaitable.close()
            delays.append(timeout)
            if len(delays) == 4:
                runner._closing.set()
            raise TimeoutError

        with patch("kiwoom_monitor.central_server.news_jobs.asyncio.wait_for",
                   side_effect=capture_wait):
            await runner._loop(wake=asyncio.Event())

        self.assertEqual([1, 2, 4, 5], delays)
        self.assertEqual(4, runner.run_once.await_count)

    async def test_finish_ack_loss_does_not_reopen_completed_body_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "central.sqlite3"
            store = SQLiteQueryStore(path)
            store.initialize()
            store.upsert_documents("news_article", _article())
            wakes = []
            store.set_news_job_wakeup(lambda: wakes.append("wake"))
            native_finish = store.finish_news_job
            def finish_with_lost_ack(*args, **kwargs):
                native_finish(*args, **kwargs)
                raise ConnectionError("finish committed, response lost")
            runner = NewsJobRunner(store, fetcher=lambda *_args, **_kwargs: "immutable body")
            with patch.object(store, "finish_news_job", side_effect=finish_with_lost_ack):
                self.assertEqual(1, await runner.run_once())
            with store._connection() as connection:
                state = connection.execute(
                    "SELECT state,attempts,output_ref,error FROM central_news_jobs WHERE stage='BODY'"
                ).fetchone()
            self.assertEqual(("COMPLETED", 1, ""), (state[0], state[1], state[3]))
            body = store.load_news_body_revision(state[2])
            self.assertEqual("immutable body", body["body_text"])
            self.assertEqual(1, len(wakes))  # Only BODY publication enqueues/wakes RULE.
            store.upsert_documents("news_article", _article())
            await runner.close()
            store.close()
            restarted = SQLiteQueryStore(path)
            after_restart = NewsJobRunner(restarted, fetcher=lambda *_args, **_kwargs: self.fail("completed BODY was fetched again"))
            try:
                self.assertEqual(1, await after_restart.run_once())  # The existing RULE job.
                self.assertEqual([], restarted.claim_news_jobs())
                bodies = restarted.load_news_history("body", target=body["article_revision_id"])
                self.assertEqual([state[2]], [value["body_revision_id"] for value in bodies])
                with restarted._connection() as connection:
                    self.assertEqual(state, connection.execute(
                        "SELECT state,attempts,output_ref,error FROM central_news_jobs WHERE stage='BODY'"
                    ).fetchone())
            finally:
                await after_restart.close()
                restarted.close()

    async def test_cancelled_claim_is_recovered_only_by_existing_stale_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            store.upsert_documents("news_article", _article())
            runner = NewsJobRunner(store)
            entered = asyncio.Event()
            async def paused(_job):
                entered.set()
                await asyncio.Event().wait()
            base = time.time() + 1
            try:
                with patch("kiwoom_monitor.central_server.database_news_jobs.time", return_value=base), \
                        patch.object(runner, "_run_body", side_effect=paused):
                    task = asyncio.create_task(runner.run_once())
                    await asyncio.wait_for(entered.wait(), 2)
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                self.assertEqual([], store.claim_news_jobs(now=base + 50))
                recovered = store.claim_news_jobs(now=base + 121)[0]
                self.assertEqual(2, recovered["attempts"])
                store.finish_news_job(recovered["job_key"], "recovered", attempts=recovered["attempts"])
                self.assertEqual([], store.claim_news_jobs(now=base + 242))
            finally:
                await runner.close()
                store.close()

    async def test_wakeup_arriving_during_claim_is_not_lost(self) -> None:
        import threading

        class Store:
            callback = None

            def set_news_job_wakeup(self, callback):
                self.callback = callback

        store = Store()
        runner = NewsJobRunner(store, poll_seconds=1, max_idle_poll_seconds=5)
        claim_started = asyncio.Event()
        release_claim = threading.Event()
        claimed_again = asyncio.Event()
        calls = 0

        async def run_once():
            nonlocal calls
            calls += 1
            if calls == 1:
                claim_started.set()
                await asyncio.to_thread(release_claim.wait, 2)
                return 0
            claimed_again.set()
            return 0

        runner.run_once = run_once
        await runner.start()
        try:
            await asyncio.wait_for(claim_started.wait(), 1)
            await asyncio.to_thread(store.callback)
            release_claim.set()
            await asyncio.wait_for(claimed_again.wait(), 0.5)
        finally:
            release_claim.set()
            await runner.close()
        self.assertIsNone(store.callback)
        self.assertGreaterEqual(calls, 2)

    async def test_article_enqueue_wakeup_runs_after_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            visible_pending = []

            def on_wake():
                with store._connection() as connection:
                    visible_pending.append(connection.execute(
                        "SELECT COUNT(*) FROM central_news_jobs WHERE state='PENDING'"
                    ).fetchone()[0])

            store.set_news_job_wakeup(on_wake)
            store.upsert_documents("news_article", _article())
            self.assertEqual([1], visible_pending)
            with self.assertRaises(Exception):
                store.upsert_documents("news_article", [{"owner": "bad"}])
            self.assertEqual([1], visible_pending)
            store.close()

    async def test_diagnostic_pause_resumes_claiming_without_restarting_worker(self) -> None:
        runner = NewsJobRunner(object(), poll_seconds=0.05)
        runner.run_once = AsyncMock(return_value=0)
        paused = {"value": True}
        with patch("kiwoom_monitor.central_server.news_jobs.is_paused",
                   side_effect=lambda _name: paused["value"]):
            await runner.start()
            try:
                await asyncio.sleep(0.12)
                self.assertEqual(0, runner.run_once.await_count)
                paused["value"] = False
                await asyncio.sleep(0.12)
                self.assertGreater(runner.run_once.await_count, 0)
            finally:
                await runner.close()

    async def test_selected_stock_body_is_claimed_before_newer_backlog(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            with patch.object(store, "_now_provider", return_value=datetime.fromtimestamp(100.0, timezone.utc)):
                store.upsert_documents("news_article", _article())
            newer = _article("다른 종목 최신 기사")[0]
            newer["owner"] = "000660"
            newer["key"] = "article-2"
            newer["document"] = {
                **newer["document"], "stock_code": "000660", "stock_name": "SK하이닉스",
                "identity": "article-2", "link": "https://news/2",
                "original_link": "https://origin/2",
            }
            with patch.object(store, "_now_provider", return_value=datetime.fromtimestamp(200.0, timezone.utc)):
                store.upsert_documents("news_article", [newer])

            selected = store.claim_news_jobs(priority_stock_code="005930", now=300.0)

            self.assertEqual("005930", selected[0]["stock_code"])
            store.close()

    async def test_unselected_watchlist_body_uses_newest_first(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            with patch.object(store, "_now_provider", return_value=datetime.fromtimestamp(100.0, timezone.utc)):
                store.upsert_documents("news_article", _article())
            newer = _article("다른 종목 최신 기사")[0]
            newer["owner"] = "000660"
            newer["key"] = "article-2"
            newer["document"] = {
                **newer["document"], "stock_code": "000660", "stock_name": "SK하이닉스",
                "identity": "article-2", "link": "https://news/2",
                "original_link": "https://origin/2",
            }
            with patch.object(store, "_now_provider", return_value=datetime.fromtimestamp(200.0, timezone.utc)):
                store.upsert_documents("news_article", [newer])

            claimed = store.claim_news_jobs(now=300.0)

            self.assertEqual("000660", claimed[0]["stock_code"])
            store.close()

    async def test_rule_lane_claims_completed_body_despite_body_backlog(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            store.upsert_documents("news_article", _article())
            runner = NewsJobRunner(store, fetcher=lambda *_args, **_kwargs: "본문 " * 100)
            self.assertEqual(1, await runner.run_once())
            second = _article("새 본문 대기 기사")[0]
            second["key"] = "article-2"
            second["document"] = {
                **second["document"], "identity": "article-2",
                "link": "https://news/2", "original_link": "https://origin/2",
            }
            store.upsert_documents("news_article", [second])

            selected = store.claim_news_jobs(preferred_stage="RULE")

            self.assertEqual("RULE", selected[0]["stage"])
            store.close()

    async def test_parallel_body_lanes_overlap_without_duplicate_claim(self) -> None:
        import threading

        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            for number in range(3):
                item = _article(f"기사 {number}")[0]
                item["key"] = f"article-{number}"
                item["document"] = {
                    **item["document"], "identity": f"article-{number}",
                    "link": f"https://news/{number}",
                    "original_link": f"https://origin/{number}",
                }
                store.upsert_documents("news_article", [item])
            active = 0
            peak = 0
            fetched_urls = []
            lock = threading.Lock()
            overlapped = threading.Event()
            release = threading.Event()

            def slow_fetch(url, **_kwargs):
                nonlocal active, peak
                with lock:
                    fetched_urls.append(url)
                    active += 1
                    peak = max(peak, active)
                    if active >= 2:
                        overlapped.set()
                release.wait(timeout=3)
                with lock:
                    active -= 1
                return "본문 " * 100

            runner = NewsJobRunner(store, fetcher=slow_fetch, parallelism=3,
                                   busy_pause_seconds=0.02)
            await runner.start()
            try:
                concurrent = await asyncio.to_thread(overlapped.wait, 2)
            finally:
                release.set()
                await runner.close()

            self.assertTrue(concurrent)
            self.assertGreaterEqual(peak, 2)
            self.assertEqual(len(fetched_urls), len(set(fetched_urls)),
                             "parallel lanes claimed the same article body twice")
            store.close()

    async def test_continuous_backlog_is_paced_and_does_not_starve_event_loop(self) -> None:
        class BusyRunner(NewsJobRunner):
            def __init__(self) -> None:
                super().__init__(object(), busy_pause_seconds=0.02)
                self.calls = 0

            async def run_once(self) -> int:
                self.calls += 1
                return 1

        runner = BusyRunner()
        await runner.start()
        await asyncio.sleep(0.075)
        await runner.close()

        self.assertGreaterEqual(runner.calls, 2)
        self.assertLessEqual(runner.calls, 6)

    async def test_summary_body_is_reused_and_ai_job_completes_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            store.upsert_documents("news_article", _article())
            runner = NewsJobRunner(store, fetcher=lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("blocked")))

            self.assertEqual(1, await runner.run_once())
            article = store.load_news_history("article", target="005930", identity="article-1")[0]
            body = store.load_latest_news_body(article["article_revision_id"])
            self.assertEqual("summary_only", body["status"])
            store.enqueue_news_ai_jobs([{
                "stock_code": "005930", "identity": "article-1", "target_id": "005930",
                "processing_version": "prompt-v1:gemini:model",
                "payload": {"stock_name": "삼성전자", "provider": "gemini", "model": "model",
                            "article_count": 1, "event": {"identity": "article-1", "title": "제목"}},
            }])
            ai = _AI()
            runner.set_ai_service(ai)
            self.assertEqual(1, await runner.run_once())
            self.assertEqual(1, ai.calls)
            self.assertEqual([], store.claim_news_jobs(limit=4, now=10_000.0))
            store.close()

    async def test_slow_body_does_not_hold_title_ingestion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            store.upsert_documents("news_article", _article())

            def slow_fetch(*_args, **_kwargs):
                import time
                time.sleep(0.15)
                return "본문 " * 100

            runner = NewsJobRunner(store, fetcher=slow_fetch)
            running = asyncio.create_task(runner.run_once())
            await asyncio.sleep(0.02)
            store.upsert_documents("news_article", [{
                **_article("두 번째 제목")[0], "key": "article-2",
                "document": {**_article("두 번째 제목")[0]["document"], "identity": "article-2"},
            }])
            self.assertEqual(1, len(store.load_news_history("article", identity="article-2")))
            await running
            store.close()

    async def test_slow_failing_ai_does_not_hold_later_title_ingestion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            store.upsert_documents("news_article", _article())
            runner = NewsJobRunner(
                store, fetcher=lambda *_args, **_kwargs: "본문", ai_service=_AI(delay=0.15, fail=True),
            )
            await runner.run_once()
            store.enqueue_news_ai_jobs([{
                "stock_code": "005930", "identity": "article-1", "target_id": "005930",
                "processing_version": "prompt-v1:gemini:model",
                "payload": {"stock_name": "삼성전자", "provider": "gemini", "model": "model",
                            "article_count": 1, "event": {"identity": "article-1", "title": "제목"}},
            }])

            running = asyncio.create_task(runner.run_once())
            await asyncio.sleep(0.02)
            second = _article("AI 대기 중 수집된 제목")[0]
            store.upsert_documents("news_article", [{
                **second, "key": "article-2",
                "document": {**second["document"], "identity": "article-2"},
            }])
            self.assertEqual(1, len(store.load_news_history("article", identity="article-2")))
            await running
            store.close()

    async def test_stale_running_job_is_recovered_and_failure_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            store.upsert_documents("news_article", _article(description=""))
            base = time.time()
            first = store.claim_news_jobs(now=base)
            self.assertEqual([], store.claim_news_jobs(now=base + 50.0))
            recovered = store.claim_news_jobs(now=base + 121.0)
            self.assertEqual(first[0]["job_key"], recovered[0]["job_key"])
            store.retry_news_job(recovered[0]["job_key"], "failed", 0.0, attempts=recovered[0]["attempts"])
            runner = NewsJobRunner(store, fetcher=lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("blocked")))
            await runner.run_once()
            body = store.load_news_history("body", target=first[0]["article_revision_id"])
            self.assertEqual("failed", body[0]["status"])
            connection = sqlite3.connect(Path(directory) / "central.sqlite3")
            output_ref = connection.execute(
                "SELECT output_ref FROM central_news_jobs WHERE job_key=?", (first[0]["job_key"],),
            ).fetchone()[0]
            connection.close()
            self.assertEqual(body[0]["body_revision_id"], output_ref)
            self.assertEqual([], store.claim_news_jobs(now=10_000.0))
            store.close()

    async def test_stale_ai_retry_reuses_committed_revision_as_output_ref(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "central.sqlite3"
            store = SQLiteQueryStore(path)
            store.initialize()
            store.upsert_documents("news_article", _article())
            runner = NewsJobRunner(store, fetcher=lambda *_args, **_kwargs: "본문")
            await runner.run_once()
            store.enqueue_news_ai_jobs([{
                "stock_code": "005930", "identity": "article-1", "target_id": "005930",
                "processing_version": "target-company-v2:gemini:model",
                "payload": {"stock_name": "삼성전자", "provider": "gemini", "model": "model",
                            "article_count": 1,
                            "event": {"identity": "article-1", "title": "제목"}},
            }])
            ai_service = CentralAIService(
                CentralServerSettings("sqlite:///:memory:", "token", gemini_api_key="key"), store,
            )
            runner.set_ai_service(ai_service)
            ai_job = next(job for job in store.claim_news_jobs(limit=4) if job["stage"] == "AI")
            analysis = AINewsAnalysis("요약", "긍정", 80, "이유")
            with patch(
                "kiwoom_monitor.central_server.ai_service.analyze_articles",
                return_value=((analysis,), AIRequestUsage(3, 2, 5)),
            ) as analyze:
                committed_revision_id = await runner._run_ai(ai_job)
            self.assertEqual(1, analyze.call_count)
            self.assertEqual(1, len(store.load_news_history("ai", target="005930")))

            connection = sqlite3.connect(path)
            connection.execute(
                "UPDATE central_news_jobs SET updated_at=0 WHERE job_key=?", (ai_job["job_key"],),
            )
            connection.commit(); connection.close()
            with patch("kiwoom_monitor.central_server.ai_service.analyze_articles") as analyze_again:
                self.assertEqual(1, await runner.run_once())
            self.assertEqual(0, analyze_again.call_count)
            self.assertEqual(1, len(store.load_news_history("ai", target="005930")))
            connection = sqlite3.connect(path)
            state, output_ref = connection.execute(
                "SELECT state,output_ref FROM central_news_jobs WHERE job_key=?", (ai_job["job_key"],),
            ).fetchone()
            connection.close(); store.close()

        self.assertEqual("COMPLETED", state)
        self.assertEqual(committed_revision_id, output_ref)


if __name__ == "__main__":
    unittest.main()
