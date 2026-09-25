from __future__ import annotations

import asyncio
import sqlite3
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
    async def test_selected_stock_body_is_claimed_before_newer_backlog(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            with patch("kiwoom_monitor.central_server.database.time", return_value=100.0):
                store.upsert_documents("news_article", _article())
            newer = _article("다른 종목 최신 기사")[0]
            newer["owner"] = "000660"
            newer["key"] = "article-2"
            newer["document"] = {
                **newer["document"], "stock_code": "000660", "stock_name": "SK하이닉스",
                "identity": "article-2", "link": "https://news/2",
                "original_link": "https://origin/2",
            }
            with patch("kiwoom_monitor.central_server.database.time", return_value=200.0):
                store.upsert_documents("news_article", [newer])

            selected = store.claim_news_jobs(priority_stock_code="005930", now=300.0)

            self.assertEqual("005930", selected[0]["stock_code"])
            store.close()

    async def test_unselected_watchlist_body_uses_newest_first(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            with patch("kiwoom_monitor.central_server.database.time", return_value=100.0):
                store.upsert_documents("news_article", _article())
            newer = _article("다른 종목 최신 기사")[0]
            newer["owner"] = "000660"
            newer["key"] = "article-2"
            newer["document"] = {
                **newer["document"], "stock_code": "000660", "stock_name": "SK하이닉스",
                "identity": "article-2", "link": "https://news/2",
                "original_link": "https://origin/2",
            }
            with patch("kiwoom_monitor.central_server.database.time", return_value=200.0):
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
            lock = threading.Lock()
            overlapped = threading.Event()
            release = threading.Event()

            def slow_fetch(*_args, **_kwargs):
                nonlocal active, peak
                with lock:
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
            store.retry_news_job(recovered[0]["job_key"], "failed", 0.0)
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
