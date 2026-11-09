from __future__ import annotations

import asyncio
import sqlite3
import time
import tempfile
import threading
import uuid
from contextlib import closing, contextmanager
from concurrent.futures import ThreadPoolExecutor
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


class _AI(CentralAIService):
    def __init__(self, store, *, delay: float = 0.0, fail: bool = False) -> None:
        super().__init__(CentralServerSettings("sqlite:///:memory:", "token", gemini_api_key="fake-key"), store)
        self.delay, self.fail, self.calls = delay, fail, 0

    def _invoke(self, *_args, **_kwargs):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise RuntimeError("provider failed")
        return ((AINewsAnalysis("fake summary", "긍정", 80, "fake reason"),), AIRequestUsage())

    async def analyze_job(self, *args, **kwargs):
        with patch("kiwoom_monitor.central_server.ai_service.analyze_articles", side_effect=self._invoke):
            return await super().analyze_job(*args, **kwargs)


class NewsJobRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_kind_timed_halt_flows_from_accepted_body_to_micro_and_buy_checks(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from kiwoom_monitor.central_server.market_events import MarketEventService
        from kiwoom_monitor.central_server.research_read_routes import create_research_read_router

        symbol = "484870"
        source = "https://kind.krx.co.kr/external/2026/06/12/000372/20260612000682/99808.htm"
        accepted = datetime.fromisoformat("2026-06-12T11:07:00+09:00")
        clock = [accepted]
        html = ("<table><tr><td>1. 종목명</td><td>주식회사 엠앤씨솔루션 주권</td></tr>"
                "<tr><td>3. 매매거래정지 일시</td><td>2026-06-12</td><td>11:06</td></tr>"
                "<tr><td>4. 매매거래정지 해제일시</td><td>2026-06-12</td><td>11:36</td></tr></table>")
        filing = {"filing_url": source, "document_url": source, "filing_html": html, "original_filing_url": ""}
        correction = source.replace("20260612000682", "20260612000683")
        filings = {source: filing, correction: {**filing, "filing_url": correction,
                                                "document_url": correction, "original_filing_url": source}}
        with closing(SQLiteQueryStore(Path(":memory:"), now_provider=lambda: clock[0])) as store:
            store.initialize()
            runner = NewsJobRunner(store, now_provider=lambda: clock[0], filing_fetcher=lambda url, **_kw: filings[url])
            self.addAsyncCleanup(runner.close)

            async def accept(url, title):
                item = _article(title)[0]
                item.update(owner=symbol, key=url)
                item["document"].update(stock_code=symbol, stock_name="엠앤씨솔루션", identity=url,
                                        link=url, original_link=url, published_at=clock[0].isoformat())
                store.upsert_documents("news_article", [item])
                self.assertEqual(1, await runner.run_once(preferred_stage="BODY"))

            await accept(source, "[엠앤씨솔루션] 매매거래정지 및 정지해제(중요내용공시)")

            app = FastAPI()
            app.include_router(create_research_read_router(store, lambda: None, lambda: None, now_provider=lambda: clock[0]))
            service = MarketEventService(None, store, now_provider=lambda: clock[0])
            self.addAsyncCleanup(service.close)
            with TestClient(app) as client:
                def micro(venue="KRX", code=symbol):
                    result = client.get("/api/v1/research/micro-inputs", params={
                        "symbol": code, "venue": venue, "variant": "BOOK_SCALP",
                    }).json()
                    self.assertFalse(result["order_authorized"])
                    self.assertIn("native_instrument_tradability", result["missing_inputs"])
                    return result["market_state_evidence"]["kind_halt_resume"]

                clock[0] = accepted.replace(second=0, minute=6)
                self.assertEqual("UNKNOWN", micro()["state"])
                self.assertIsNone(service.automation_buy_market_rejection(symbol, clock[0]))
                clock[0] = accepted
                halt = micro()
                self.assertEqual(("HALTED", source, accepted.isoformat()),
                                 (halt["state"], halt["source_ref"], halt["available_at"]))
                self.assertEqual("kind_trading_halt", service.automation_buy_market_rejection(symbol, clock[0]))
                self.assertEqual("UNKNOWN", micro("NXT")["state"])
                self.assertEqual("UNKNOWN", micro(code="005930")["state"])
                clock[0] = accepted.replace(minute=36)
                self.assertEqual("RESUME_TIME_REACHED", micro()["state"])
                self.assertEqual("kind_halt_requires_new_observation",
                                 service.automation_buy_market_rejection(symbol, accepted))
                self.assertIsNone(service.automation_buy_market_rejection(symbol, clock[0]))
                clock[0] = accepted.replace(minute=12)
                await accept(correction, "[기재정정] 매매거래정지 및 정지해제(중요내용공시)")
                self.assertEqual({"state": "UNKNOWN", "reason": "kind_notice_changed_or_unresolved"}, micro())
            await runner.close()

    def test_kind_toc_recovery_targets_only_unstored_terminal_failures(self):
        from datetime import timedelta

        from kiwoom_monitor.central_server.news_jobs import official_filing_body_result
        from kiwoom_monitor.infrastructure.official_news_filing import KIND_DOCUMENT_UNVERIFIED
        from scripts.recover_kind_toc_filings import recover

        now = datetime.fromisoformat("2026-10-22T07:00:00+09:00")
        root = "https://kind.krx.co.kr"
        body = "<h2>부도발생</h2><table><tr><td>공시 본문</td></tr></table>"
        pages, reads = {}, []

        def filing(number, *, toc=True, wrong_doc=False):
            receipt = f"2026102100{number:04d}"
            doc = f"2026102101{number:04d}"
            viewer = f"{root}/common/disclsviewer.do?method=search&acptno={receipt}"
            contents = f"{root}/common/disclsviewer.do?method=searchContents&docNo={doc}"
            target = f"{root}/external/2026/10/21/{receipt[-6:]}/{doc}/11301.htm"
            index = target.replace(".htm", "_toc.htm") if toc else ""
            selected = target.replace(doc, "20261021999999") if wrong_doc else target
            pages.update({viewer: f'<option value="{doc}|main" selected="selected">부도발생</option>',
                          contents: f"parent.setPath('{index}','{selected}');", target: body})
            return viewer, target

        def read(url):
            reads.append(url)
            return pages[url]

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recovery.sqlite3"
            with closing(SQLiteQueryStore(path, now_provider=lambda: now)) as store:
                store.initialize()

                def seed(url, *, error=KIND_DOCUMENT_UNVERIFIED, archive=None, leave_pending=False):
                    nonlocal now
                    item = _article("부도발생")[0]
                    item["key"] = url
                    item["document"].update(identity=url, link=url, original_link=url,
                                            published_at="2026-10-21T17:00:00+09:00")
                    store.upsert_documents("news_article", [item])
                    for attempt in range(1, 4):
                        job, = store.claim_news_jobs(now=now.timestamp(), preferred_stage="BODY")
                        self.assertEqual(("BODY", attempt), (job["stage"], job["attempts"]))
                        if archive:
                            result = official_filing_body_result(
                                {"filing_url": url, "document_url": archive, "original_filing_url": "", "filing_html": body},
                                job["payload"], now.timestamp(),
                            )
                        else:
                            now += timedelta(seconds=10)
                            result = {"stage": "BODY", "error": error, "next_retry_at": now.timestamp()}
                        receipt = store.accept_news_job_result(job["job_key"], result, attempts=attempt)
                        if archive or leave_pending:
                            break
                    self.assertEqual("completed" if archive else "pending" if leave_pending else "failed", receipt["state"])
                    return job

                repair, target = filing(1)
                failed = seed(repair)
                alternative = repair.replace("method=search&acptno=", "acptno=") + "&method=search"
                seed(alternative)
                plain, plain_target = filing(2, toc=False)
                seed(plain)
                bad, bad_target = filing(3, wrong_doc=True)
                seed(bad)
                ignored, ignored_target = filing(4)
                seed(ignored, error="HTTP timeout")
                saved_view, saved_target = filing(5)
                seed(saved_view)
                seed(saved_target, archive=saved_target)
                seed("https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20261021000009")
                pending, pending_target = filing(6)
                seed(pending, leave_pending=True)
                before, = store.load_news_history("body", target=failed["article_revision_id"])

                with closing(sqlite3.connect(path)) as db:
                    report = recover(db, read=read, now_provider=lambda: now)
                    self.assertCountEqual(["recovered", "already_saved", "already_saved", "toc_not_confirmed", "unresolved"],
                                          [row["status"] for row in report])
                    self.assertEqual(1, reads.count(target))
                    self.assertTrue({plain_target, bad_target, ignored, ignored_target, saved_target, pending, pending_target}
                                    .isdisjoint(reads))
                    self.assertEqual({"COMPLETED": 2, "FAILED": 6, "PENDING": 1}, dict(db.execute(
                        "SELECT state,count(*) FROM central_news_jobs WHERE stage='BODY' GROUP BY state",
                    )))
                    reads.clear()
                    self.assertNotIn("recovered", {row["status"] for row in recover(db, read=read, now_provider=lambda: now)})
                    self.assertTrue({repair, target, saved_target}.isdisjoint(reads))
                archived = store.load_documents("news_official_filing", "005930")
                self.assertEqual({target, saved_target}, {row["document"]["document_url"] for row in archived})
                recovered = next(row["document"] for row in archived if row["document"]["document_url"] == target)
                history = store.load_news_history("body", target=recovered["article_revision_id"])
                self.assertEqual(["fulltext", "failed"], [row["status"] for row in history])
                self.assertEqual("failed", before["status"])

    async def test_official_adverse_events_reach_panic_and_dd_only_after_matching_body_is_saved(self):
        from kiwoom_monitor.application.daily_decline import _material_catalyst
        from kiwoom_monitor.application.news_material import (
            RECORDED_CONTRACT_FILINGS, received_strategy_material, recorded_contract_material,
        )
        from kiwoom_monitor.application.panic_rebound import material_panic_proofs
        from tests.unit.test_news_material import _at, _dart

        examples = (
            ("횡령ㆍ배임혐의발생", "횡령ㆍ배임혐의발생", "embezzlement_breach_allegation"),
            ("주요사항보고서(회생절차개시신청)", "회생절차 개시신청", "rehabilitation_filed"),
            ("회생절차개시결정", "회생절차개시결정", "rehabilitation_opened"),
            ("주요사항보고서(부도발생)", "부도발생", "default"),
            ("영업정지", "영업정지", "business_suspension"),
            ("영업정지", "조회공시 답변", None),
            ("횡령ㆍ배임혐의 진행사항", "횡령ㆍ배임혐의 진행사항", None),
            ("[정정] 영업정지", "영업정지", None),
            ("주주총회소집결의", "주주총회소집결의", None),
        )
        clock = [datetime.fromisoformat(_at(2))]
        with tempfile.TemporaryDirectory() as directory, closing(SQLiteQueryStore(
            Path(directory) / "adverse.sqlite3", now_provider=lambda: clock[0],
        )) as store:
            store.initialize()
            for number, (title, heading, expected) in enumerate(examples, 1):
                with self.subTest(title=title, heading=heading):
                    url = _dart(number)
                    item = _article(title)[0]
                    item["key"] = url
                    item["document"].update(identity=url, link=url, original_link=url, published_at=_at(1))
                    store.upsert_documents("news_article", [item])
                    filing = {"filing_url": url, "document_url": url.replace("/dsaf001/main.do", "/report/viewer.do"),
                              "filing_html": f"<h2><P>{heading}</h2></P><table><tr><td>공시 본문</td></tr></table>",
                              "original_filing_url": ""}
                    runner = NewsJobRunner(store, now_provider=lambda: clock[0], filing_fetcher=lambda *_a, **_kw: filing)
                    self.assertEqual(1, await runner.run_once(preferred_stage="BODY"))
                    ref = "dart:" + url.split("=")[1]
                    row = store.load_document(RECORDED_CONTRACT_FILINGS, "005930", ref)
                    if expected is None:
                        self.assertIsNone(row)
                        continue
                    self.assertEqual(expected, row["document"]["event_kind"])
                    before = datetime.fromisoformat(_at(1))
                    self.assertEqual((), recorded_contract_material((row["document"],), before).chains)
                    material = recorded_contract_material((row["document"],), clock[0])
                    self.assertEqual("DAMAGED", _material_catalyst((ref, material), "005930", clock[0]).state)
                    proof, = material_panic_proofs(material, "005930", clock[0])
                    self.assertEqual("fundamental_adverse", proof.disposition)
            self.assertEqual(len(examples), len(store.load_documents("news_official_filing", "005930")))
            _, context = received_strategy_material(
                "005930", clock[0], **store.load_strategy_material_inputs("005930", clock[0]),
            )
            ordinary, = [item for item in context["events"] if item["title"] == "주주총회소집결의"]
            self.assertEqual(("CONFIRMED", "UNKNOWN"), (ordinary["stage"], ordinary["effect"]))
            unverified, = [item for item in context["recent_reports"] if item["source_ref"] == _dart(6)]
            self.assertEqual(("REPORT", "UNKNOWN", _at(2)), (
                unverified["stage"], unverified["effect"], unverified["original_received_at"],
            ))

    async def test_official_body_is_archived_once_and_changes_strategy_state_only_after_acceptance(self):
        from kiwoom_monitor.application.daily_decline import _material_catalyst
        from kiwoom_monitor.application.news_material import RECORDED_CONTRACT_FILINGS, received_strategy_material
        from kiwoom_monitor.application.panic_rebound import material_panic_proofs
        from tests.unit.test_news_material import _at, _dart

        original, corrected = _dart(1), _dart(2)
        clock = [datetime.fromisoformat(_at(1))]
        html = {
            original: "<table><tr><td>해지금액</td><td>1,000</td></tr></table>",
            corrected: ("<table><tr><th>정정항목</th><th>정정전</th><th>정정후</th></tr>"
                        "<tr><td>계약 상태</td><td>해지</td><td>계약 해지 자체를 철회하였습니다.</td></tr></table>"),
        }

        def fetch(url, **_options):
            clock[0] = datetime.fromisoformat(_at(2 if url == original else 6))
            number = url.split("=")[1]
            return {"filing_url": url, "document_url": f"https://dart.fss.or.kr/report/viewer.do?rcpNo={number}",
                    "filing_html": html[url], "original_filing_url": original if url == corrected else ""}

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "official-filing.sqlite3"
            with closing(SQLiteQueryStore(path, now_provider=lambda: clock[0])) as store:
                store.initialize()
                runner = NewsJobRunner(store, now_provider=lambda: clock[0], filing_fetcher=fetch,
                                       fetcher=lambda *a, **kw: self.fail("portal article used for official filing"))
                report = _article("삼성전자 공급계약 관련 보도")[0]
                report["key"] = "https://press.invalid/story"
                report["document"].update(identity=report["key"], link=report["key"], original_link=report["key"],
                                          published_at=_at(0))
                for url, title in ((original, "단일판매ㆍ공급계약해지"), (corrected, "[기재정정]단일판매ㆍ공급계약해지")):
                    item = _article(title)[0]
                    item["key"] = url
                    item["document"].update(identity=url, link="https://portal.invalid/summary",
                                            original_link=url, published_at=_at(1))
                    store.upsert_documents("news_article", [item])
                    self.assertEqual(1, await runner.run_once(preferred_stage="BODY"))
                    store.upsert_documents("news_article", [item])
                clock[0] = datetime.fromisoformat(_at(7))
                store.upsert_documents("news_article", [report, {
                    **report, "owner": "000660", "key": "https://press.invalid/other",
                }])
                await runner.close()
            with closing(SQLiteQueryStore(path, now_provider=lambda: clock[0])) as reopened:
                archived = [row["document"] for row in reopened.load_documents("news_official_filing", "005930")]
                self.assertEqual(html, {row["filing_url"]: row["filing_html"] for row in archived})
                self.assertEqual({_at(2), _at(6)}, {row["available_at"] for row in archived})
                rows = [row["document"] for row in reopened.load_documents(RECORDED_CONTRACT_FILINGS, "005930")]
                self.assertEqual(2, len(rows))
                event = "dart:20260911000001"
                for minute, state, blocked in ((5, "DAMAGED", True), (6, "INTACT", False), (7, "INTACT", False)):
                    cut = datetime.fromisoformat(_at(minute))
                    material, context = received_strategy_material(
                        "005930", cut, **reopened.load_strategy_material_inputs("005930", cut),
                    )
                    self.assertEqual(state, _material_catalyst((event, material), "005930", cut).state)
                    self.assertEqual(blocked, bool(material_panic_proofs(material, "005930", cut)))
                    observed, = context["events"]
                    self.assertEqual(original if blocked else corrected, observed["source_ref"])
                    self.assertEqual(_at(2 if blocked else 6), observed["available_at"])
                    self.assertEqual("FUNDAMENTAL_ADVERSE" if blocked else "INTACT", observed["effect"])
                    if blocked:
                        pending, = context["recent_reports"]
                        self.assertEqual(("REPORT", "UNKNOWN", corrected),
                                         (pending["stage"], pending["effect"], pending["source_ref"]))
                    elif minute == 7:
                        pending, = context["recent_reports"]
                        self.assertEqual((report["key"], _at(7)), (pending["source_ref"], pending["available_at"]))
                    else:
                        self.assertEqual([], context["recent_reports"])

    async def test_reclaimed_attempt_cannot_be_finished_or_retried_by_old_runner(self) -> None:
        for old_fails in (False, True):
            for new_finished in (False, True):
                with self.subTest(old_fails=old_fails, new_finished=new_finished), tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "central.sqlite3"
                    first = SQLiteQueryStore(path)
                    first.initialize()
                    first.upsert_documents("news_article", _article(description=""))
                    old_wakes = []
                    first.set_news_job_wakeup(lambda: old_wakes.append("unexpected stale wake"))
                    second = SQLiteQueryStore(path)
                    old_entered, old_release = threading.Event(), threading.Event()
                    new_entered, new_release = threading.Event(), threading.Event()
                    def old_body(*_args, **_kwargs):
                        old_entered.set()
                        if not old_release.wait(8):
                            raise TimeoutError("old fetch was not released")
                        if old_fails:
                            raise RuntimeError("old owner failed")
                        return "old-owner-output", "2026-10-23T08:01:00+09:00"
                    def new_body(*_args, **_kwargs):
                        new_entered.set()
                        if not new_release.wait(8):
                            raise TimeoutError("new fetch was not released")
                        return "new-owner-output", "2026-10-23T08:02:00+09:00"
                    def row():
                        with first._connection() as connection:
                            return connection.execute(
                                "SELECT state,attempts,output_ref,error,next_retry_at,updated_at FROM central_news_jobs"
                            ).fetchone()
                    clock = [time.time() + 1]
                    now_provider = lambda: datetime.fromtimestamp(clock[0], timezone.utc)
                    old = NewsJobRunner(first, fetcher=old_body, now_provider=now_provider)
                    new = NewsJobRunner(second, fetcher=new_body, now_provider=now_provider)
                    tasks = []
                    try:
                        with patch("kiwoom_monitor.central_server.database_news_jobs.time", side_effect=lambda: clock[0]):
                            tasks.append(asyncio.create_task(old.run_once()))
                            self.assertTrue(await asyncio.to_thread(old_entered.wait, 2))
                            self.assertEqual(("RUNNING", 1), row()[:2])
                            clock[0] += 121
                            tasks.append(asyncio.create_task(new.run_once()))
                            self.assertTrue(await asyncio.to_thread(new_entered.wait, 2))
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
                            bodies = first.load_news_history("body", target=first.load_news_history("article")[0]["article_revision_id"])
                            self.assertEqual(["new-owner-output"], [value["body_text"] for value in bodies])
                            self.assertEqual(("COMPLETED", 2, bodies[0]["body_revision_id"], ""), row()[:4])
                            publication = first.load_documents("news_original_publication", bodies[0]["article_revision_id"], 1)
                            self.assertEqual("2026-10-23T08:02:00+09:00", publication[0]["document"]["published_at"])
                            with first._connection() as connection:
                                self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM central_news_jobs WHERE stage='RULE'").fetchone()[0])
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
            native_finish = store.accept_news_job_result
            ack_lost = False
            def finish_with_lost_ack(*args, **kwargs):
                nonlocal ack_lost
                receipt = native_finish(*args, **kwargs)
                if not ack_lost:
                    ack_lost = True
                    raise ConnectionError("finish committed, response lost")
                return receipt
            runner = NewsJobRunner(store, fetcher=lambda *_args, **_kwargs: "immutable body")
            with patch.object(store, "accept_news_job_result", side_effect=finish_with_lost_ack):
                self.assertEqual(1, await runner.run_once())
            self.assertTrue(ack_lost)
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
        from kiwoom_monitor.central_server.diagnostic_workloads import is_paused
        paused = {"value": True}
        with patch("kiwoom_monitor.central_server.diagnostic_workloads.is_paused",
                   side_effect=lambda _name: paused["value"]):
            from kiwoom_monitor.central_server.diagnostic_workloads import is_paused
            runner = NewsJobRunner(object(), poll_seconds=0.05, paused=lambda: is_paused("news_jobs"))
            runner.run_once = AsyncMock(return_value=0)
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
            ai = _AI(store)
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
                store, fetcher=lambda *_args, **_kwargs: "본문", ai_service=_AI(store, delay=0.15, fail=True),
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
                # Model the preserved historical split-save interruption. The
                # new accepted native command atomically completes fresh jobs.
                "UPDATE central_news_jobs SET state='RUNNING',output_ref='',updated_at=0 WHERE job_key=?",
                (ai_job["job_key"],),
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


class NewsJobResultNativeTests(unittest.IsolatedAsyncioTestCase):
    """The same output/authority oracle runs on SQLite and real PostgreSQL."""

    postgres = False

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "results.sqlite3"
        self.clock = [datetime(2026, 10, 23, 9, 0, tzinfo=timezone.utc).timestamp()]
        self.store = self._make_store(self.path)
        self.addCleanup(self.store.close)
        self.store.initialize()

    def _now(self):
        return datetime.fromtimestamp(self.clock[0], timezone.utc)

    def _make_store(self, path):
        return SQLiteQueryStore(path, now_provider=self._now)

    def _peer(self):
        target = self.store._database_url if self.postgres else self.path
        peer = type(self.store)(target, now_provider=self._now)
        self.addCleanup(peer.close)
        return peer

    def _sql(self, statement, parameters=()):
        with closing(self.store._connect()) as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(statement.replace("?", "%s") if self.postgres else statement, parameters)
                rows = cursor.fetchall() if cursor.description else []
                connection.commit()
                return rows
            finally:
                cursor.close()

    def _article(self, *, contract=False):
        code, identity = "N" + uuid.uuid4().hex[:12], uuid.uuid4().hex
        value = _article(description="")
        document = {**value[0]["document"], "stock_code": code, "stock_name": "테스트기업",
                    "identity": identity, "title": "테스트기업, 삼성전자와 500억원 공급계약 체결" if contract else "기사 제목"}
        self.store.upsert_documents("news_article", [{**value[0], "owner": code, "key": identity, "document": document}])
        revision = self.store.load_news_history("article", target=code)[0]
        return code, revision

    def _claim(self, code, stage="BODY", store=None):
        jobs = (store or self.store).claim_news_jobs(
            now=self.clock[0] + 1, priority_stock_code=code, preferred_stage=stage,
        )
        self.assertEqual(1, len(jobs))
        self.assertEqual(code, jobs[0]["target_id"])
        self.assertEqual(stage, jobs[0]["stage"])
        return jobs[0]

    @contextmanager
    def _reject(self, table, event, condition):
        name = "ar04_" + uuid.uuid4().hex
        if self.postgres:
            self._sql(f"CREATE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'native result rejected'; END $$")
            self._sql(f"CREATE TRIGGER {name} BEFORE {event} ON {table} FOR EACH ROW WHEN ({condition}) EXECUTE FUNCTION {name}()")
        else:
            self._sql(f"CREATE TRIGGER {name} BEFORE {event} ON {table} WHEN {condition} BEGIN SELECT RAISE(ABORT,'native result rejected'); END")
        try:
            yield
        finally:
            self._sql(f"DROP TRIGGER {name}" + (f" ON {table}" if self.postgres else ""))
            if self.postgres:
                self._sql(f"DROP FUNCTION {name}()")

    async def test_actual_stale_body_cannot_publish_or_enqueue_after_reclaim(self):
        code, article = self._article()
        entered, release = threading.Event(), threading.Event()
        old_wakes = []
        self.store.set_news_job_wakeup(lambda: old_wakes.append("stale wake"))
        def old_fetch(*_args, **_kwargs):
            entered.set()
            if not release.wait(8):
                raise TimeoutError("fetch was not released")
            return "OLD BODY", "2026-10-23T08:01:00+09:00"
        old = NewsJobRunner(self.store, fetcher=old_fetch, now_provider=self._now)
        peer = self._peer()
        new = NewsJobRunner(peer, fetcher=lambda *a, **k: ("NEW BODY", "2026-10-23T08:02:00+09:00"), now_provider=self._now)
        task = asyncio.create_task(old.run_once())
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            self.clock[0] += 121
            self.assertEqual(1, await new.run_once())
            release.set()
            self.assertEqual(1, await task)
            bodies = self.store.load_news_history("body", target=article["article_revision_id"])
            self.assertEqual(["NEW BODY"], [row["body_text"] for row in bodies])
            self.assertEqual([], old_wakes)
            publication = self.store.load_document("news_original_publication", article["article_revision_id"], "published_at")
            self.assertEqual("2026-10-23T08:02:00+09:00", publication["document"]["published_at"])
            self.assertEqual(article, self.store.load_news_article_revision(article["article_revision_id"]))
            self.assertEqual([(1,)], self._sql("SELECT COUNT(*) FROM central_news_jobs WHERE article_revision_id=? AND stage='RULE'", (article["article_revision_id"],)))
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await old.close()
            await new.close()

    async def test_same_attempt_parallel_acceptance_writes_once_and_wakes_after_terminal(self):
        code, article = self._article()
        job = self._claim(code)
        peer, wakes = self._peer(), []
        def wake():
            wakes.append(self._sql("SELECT state,output_ref FROM central_news_jobs WHERE job_key=?", (job["job_key"],))[0])
        self.store.set_news_job_wakeup(wake)
        peer.set_news_job_wakeup(wake)
        result = {"stage": "BODY", "body": {"status": "fulltext", "body_text": "one immutable body"},
                  "original_publication": {"published_at": "2026-10-23T08:02:00+09:00"}}
        def accept(store):
            return store.accept_news_job_result(job["job_key"], result, attempts=job["attempts"])
        with ThreadPoolExecutor(max_workers=2) as executor:
            receipts = list(executor.map(accept, (self.store, peer)))
        self.assertEqual({"completed", "already_completed"}, {r["state"] for r in receipts})
        self.assertEqual(1, len({r["output_ref"] for r in receipts}))
        self.assertEqual([("COMPLETED", receipts[0]["output_ref"])], wakes)
        bodies = self.store.load_news_history("body", target=article["article_revision_id"])
        self.assertEqual(1, len(bodies))
        self.assertEqual(self.clock[0], bodies[0]["available_at"])
        self.assertEqual([(1,)], self._sql("SELECT COUNT(*) FROM central_news_jobs WHERE article_revision_id=? AND stage='RULE'", (article["article_revision_id"],)))

    async def test_body_publication_and_rule_job_roll_back_with_terminal_failure(self):
        code, article = self._article()
        job, wakes = self._claim(code), []
        self.store.set_news_job_wakeup(lambda: wakes.append("wake"))
        runner = NewsJobRunner(self.store, fetcher=lambda *a, **k: ("body", "2026-10-23T08:02:00+09:00"), now_provider=self._now)
        try:
            with self._reject("central_news_jobs", "UPDATE", f"NEW.job_key='{job['job_key']}' AND NEW.state='COMPLETED'"):
                with self.assertRaisesRegex(Exception, "native result rejected"):
                    await runner._run_body(job)
            self.assertEqual([], self.store.load_news_history("body", target=article["article_revision_id"]))
            self.assertIsNone(self.store.load_document("news_original_publication", article["article_revision_id"], "published_at"))
            self.assertEqual([("RUNNING", 1)], self._sql("SELECT state,attempts FROM central_news_jobs WHERE job_key=?", (job["job_key"],)))
            self.assertEqual([(0,)], self._sql("SELECT COUNT(*) FROM central_news_jobs WHERE article_revision_id=? AND stage='RULE'", (article["article_revision_id"],)))
            self.assertEqual([], wakes)
            self.assertTrue(await runner._run_body(job))
            self.assertEqual(["wake"], wakes)
        finally:
            await runner.close()

    async def test_rule_assessment_event_membership_and_terminal_share_attempt(self):
        for failure_table in ("central_news_event_membership_revisions", "central_news_jobs"):
            with self.subTest(failure_table=failure_table):
                code, article = self._article(contract=True)
                runner = NewsJobRunner(self.store, fetcher=lambda *a, **k: "공급계약 체결", now_provider=self._now)
                try:
                    await runner._run_body(self._claim(code))
                    job = self._claim(code, "RULE")
                    condition = (f"NEW.job_key='{job['job_key']}' AND NEW.state='COMPLETED'" if failure_table == "central_news_jobs"
                                 else f"NEW.article_revision_id='{article['article_revision_id']}'")
                    with self._reject(failure_table, "UPDATE" if failure_table == "central_news_jobs" else "INSERT", condition):
                        with self.assertRaisesRegex(Exception, "native result rejected"):
                            await runner._run_rule(job)
                    self.assertIsNone(self.store.load_document("news_assessment", code, article["article_revision_id"]))
                    self.assertEqual([], self.store.load_news_history("event", target=code))
                    self.assertEqual([(0,)], self._sql("SELECT COUNT(*) FROM central_news_event_membership_revisions WHERE article_revision_id=?", (article["article_revision_id"],)))
                    self.assertEqual([("RUNNING", 1)], self._sql("SELECT state,attempts FROM central_news_jobs WHERE job_key=?", (job["job_key"],)))
                    self.clock[0] += 121
                    reclaimed = self._claim(code, "RULE", self._peer())
                    self.assertEqual("", await runner._run_rule(job))
                    self.assertIsNone(self.store.load_document("news_assessment", code, article["article_revision_id"]))
                    ref = await runner._run_rule(reclaimed)
                    self.assertEqual(ref, await runner._run_rule(reclaimed))
                    self.assertEqual([ref], [r["event_revision_id"] for r in self.store.load_news_history("event", target=code)])
                    self.assertEqual([(1,)], self._sql("SELECT COUNT(*) FROM central_news_event_membership_revisions WHERE article_revision_id=?", (article["article_revision_id"],)))
                    assessment = self.store.load_document("news_assessment", code, article["article_revision_id"])
                    self.assertEqual(reclaimed["payload"]["body_revision_id"], assessment["document"]["body_revision_id"])
                finally:
                    await runner.close()

    async def test_third_body_failure_rolls_back_and_rejects_stale_error(self):
        code, article = self._article()
        self._sql("UPDATE central_news_jobs SET attempts=2 WHERE article_revision_id=?", (article["article_revision_id"],))
        job, wakes = self._claim(code), []
        self.store.set_news_job_wakeup(lambda: wakes.append("wake"))
        error_result = {"stage": "BODY", "error": "original fetch failed", "next_retry_at": self.clock[0] + 8}
        self.assertEqual("stale", self.store.accept_news_job_result(job["job_key"], error_result, attempts=2)["state"])
        with self._reject("central_news_jobs", "UPDATE", f"NEW.job_key='{job['job_key']}' AND NEW.state='FAILED'"):
            with self.assertRaisesRegex(Exception, "native result rejected"):
                self.store.accept_news_job_result(job["job_key"], error_result, attempts=3)
        self.assertEqual([], self.store.load_news_history("body", target=article["article_revision_id"]))
        self.assertEqual([], wakes)
        receipt = self.store.accept_news_job_result(job["job_key"], error_result, attempts=3)
        self.assertEqual("failed", receipt["state"])
        terminal_before = self._sql("SELECT * FROM central_news_jobs WHERE job_key=?", (job["job_key"],))
        body_before = self.store.load_news_history("body", target=article["article_revision_id"])
        self.clock[0] += 17
        replay = self.store.accept_news_job_result(job["job_key"], error_result, attempts=3)
        self.assertEqual({"state": "already_failed", "output_ref": receipt["output_ref"]}, replay)
        self.assertEqual(terminal_before, self._sql("SELECT * FROM central_news_jobs WHERE job_key=?", (job["job_key"],)))
        self.assertEqual(body_before, self.store.load_news_history("body", target=article["article_revision_id"]))
        body = self.store.load_news_history("body", target=article["article_revision_id"])
        self.assertEqual([("failed", "original fetch failed")], [(r["status"], r["error"]) for r in body])
        self.assertEqual(body[0]["body_revision_id"], receipt["output_ref"])
        self.assertEqual(["wake"], wakes)
        self.assertEqual([("FAILED", 3, receipt["output_ref"], "original fetch failed")], self._sql("SELECT state,attempts,output_ref,error FROM central_news_jobs WHERE job_key=?", (job["job_key"],)))

    async def test_body_ack_loss_replays_terminal_receipt_without_second_publication(self):
        code, article = self._article()
        native, lost, receipts = self.store.accept_news_job_result, False, []
        def lost_ack(*args, **kwargs):
            nonlocal lost
            receipt = native(*args, **kwargs)
            receipts.append(receipt)
            if not lost:
                lost = True
                raise ConnectionError("native result committed, ACK lost")
            return receipt
        runner = NewsJobRunner(self.store, fetcher=lambda *a, **k: "one committed body", now_provider=self._now)
        with patch.object(self.store, "accept_news_job_result", side_effect=lost_ack):
            self.assertEqual(1, await runner.run_once())
        self.assertEqual(["completed", "already_completed"], [r["state"] for r in receipts])
        self.assertEqual(receipts[0]["output_ref"], receipts[1]["output_ref"])
        await runner.close()
        restarted = NewsJobRunner(self._peer(), fetcher=lambda *a, **k: self.fail("completed BODY fetched again"), now_provider=self._now)
        try:
            self.assertEqual(1, await restarted.run_once())
            self.assertEqual(0, await restarted.run_once())
            self.assertEqual(1, len(self.store.load_news_history("body", target=article["article_revision_id"])))
        finally:
            await restarted.close()


class NewsRunnerCompositionTests(unittest.IsolatedAsyncioTestCase):
    def test_runner_uses_existing_source_branch_flag_and_same_native_callbacks(self):
        from kiwoom_monitor.central_server.app import create_app
        from kiwoom_monitor.central_server.diagnostic_replay_contract import install_store_capture
        from kiwoom_monitor.central_server.news_service import CentralNewsService
        for source_enabled in (False, True):
            for jobs_enabled in (False, True):
                with self.subTest(source=source_enabled, jobs=jobs_enabled):
                    store = SQLiteQueryStore(Path(":memory:"))
                    install_store_capture(store)
                    native_enqueue = store.enqueue_news_ai_jobs
                    try:
                        with patch.dict(create_app.__globals__, {"create_query_store": lambda *_a, **_k: store}), \
                             patch("kiwoom_monitor.central_server.news_jobs.NewsJobRunner", wraps=NewsJobRunner) as constructor, \
                             patch("kiwoom_monitor.central_server.news_service.CentralNewsService", wraps=CentralNewsService) as source:
                            create_app(CentralServerSettings("sqlite:///:memory:", "fixture-token",
                                news_naver_api_enabled=False, news_naver_stock_enabled=source_enabled,
                                news_naver_market_enabled=False, news_query_set_enabled=False,
                                news_history_jobs_enabled=jobs_enabled, autonomous_top20_enabled=False,
                                market_event_collection_enabled=False, external_market_symbols=""))
                            if source_enabled and jobs_enabled:
                                constructor.assert_called_once()
                                self.assertEqual(3, constructor.call_args.kwargs["parallelism"])
                                callbacks = source.call_args.kwargs
                                self.assertIsInstance(callbacks["prioritize_stock"].__self__, NewsJobRunner)
                                self.assertIs(native_enqueue, callbacks["enqueue_ai_jobs"])
                            else:
                                constructor.assert_not_called()
                                if source_enabled:
                                    self.assertIsNone(source.call_args.kwargs["prioritize_stock"])
                                    self.assertIsNone(source.call_args.kwargs["enqueue_ai_jobs"])
                                else:
                                    source.assert_not_called()
                    finally:
                        store.close()

    async def _accepted_close(self, *, source_fails=False, runner_fails=False):
        from kiwoom_monitor.central_server.app import create_app
        from kiwoom_monitor.central_server.diagnostic_replay_contract import install_store_capture
        from kiwoom_monitor.central_server.news_service import CentralNewsService
        fixed = datetime(2026, 10, 24, 1, 0, tzinfo=timezone.utc)
        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "composition.sqlite3"
            store = SQLiteQueryStore(path, now_provider=lambda: fixed)
            install_store_capture(store)
            store.initialize()
            store.upsert_documents("news_article", _article())
            article = store.load_news_history("article", target="005930")[0]
            peer = SQLiteQueryStore(path, now_provider=lambda: fixed)
            entered, release, source_closed = threading.Event(), threading.Event(), asyncio.Event()
            events, runners, sources, ai_owners, wakeups, claims = [], [], [], [], [], []
            primary, secondary = RuntimeError("controlled-source-close"), RuntimeError("controlled-runner-close")
            native_accept, native_claim, native_wakeup = store.accept_news_job_result, store.claim_news_jobs, store.set_news_job_wakeup
            native_enqueue = store.enqueue_news_ai_jobs
            native_close, native_ai_close = store.close, CentralAIService.close
            def accept(*args, **kwargs):
                entered.set()
                if not release.wait(5):
                    raise AssertionError("native acceptance barrier was not released")
                receipt = native_accept(*args, **kwargs)
                events.append("native-accepted")
                return receipt
            def claim(*args, **kwargs):
                result = native_claim(*args, **kwargs)
                claims.extend(result)
                return result
            def wakeup(callback):
                wakeups.append(callback)
                return native_wakeup(callback)
            def close_store():
                events.append("store-close")
                native_close()
            async def close_ai(owner):
                ai_owners.append(owner)
                events.append("ai-close")
                await native_ai_close(owner)
            def create_runner(*args, **kwargs):
                runner = NewsJobRunner(*args, **kwargs, fetcher=lambda *_a, **_k: ("fixture native body", ""))
                original_close = runner.close
                async def close():
                    events.append("runner-close-enter")
                    try:
                        await original_close()
                        if runner_fails:
                            raise secondary
                    finally:
                        events.append("runner-close-exit")
                runner.close = close
                runners.append((runner, kwargs))
                return runner
            def create_source(*args, **kwargs):
                source = CentralNewsService(*args, **kwargs)
                original_close = source.close
                async def close():
                    await original_close()
                    events.append("source-close")
                    source_closed.set()
                    if source_fails:
                        raise primary
                source.close = close
                sources.append(source)
                return source

            closing_task, scope, scope_entered = None, None, False
            try:
                with patch.dict(create_app.__globals__, {"create_query_store": lambda *_a, **_k: store, "datetime": FixedDateTime}), \
                     patch("kiwoom_monitor.central_server.news_jobs.NewsJobRunner", side_effect=create_runner), \
                     patch("kiwoom_monitor.central_server.news_service.CentralNewsService", side_effect=create_source), \
                     patch.object(store, "claim_news_jobs", side_effect=claim), \
                     patch.object(store, "accept_news_job_result", side_effect=accept), \
                     patch.object(store, "set_news_job_wakeup", side_effect=wakeup), \
                     patch.object(store, "close", side_effect=close_store), \
                     patch.object(CentralAIService, "close", close_ai):
                    app = create_app(CentralServerSettings(f"sqlite:///{path}", "fixture-token",
                        news_naver_api_enabled=False, news_naver_stock_enabled=True,
                        news_naver_market_enabled=False, news_query_set_enabled=False,
                        autonomous_top20_enabled=False, market_event_collection_enabled=False,
                        external_market_symbols="", ai_provider="gemini", gemini_api_key="fixture-unused"))
                    scope = app.router.lifespan_context(app)
                    await scope.__aenter__()
                    scope_entered = True
                    self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                    self.assertEqual(1, len(runners))
                    runner, kwargs = runners[0]
                    self.assertEqual(3, kwargs["parallelism"])
                    self.assertEqual(fixed.timestamp(), kwargs["now_provider"]().timestamp())
                    self.assertEqual(9 * 3600, kwargs["now_provider"]().utcoffset().total_seconds())
                    self.assertIs(runner, sources[0]._prioritize_stock.__self__)
                    self.assertIs(native_enqueue, sources[0]._enqueue_ai_jobs)
                    self.assertFalse(hasattr(sources[0], "_job_runner"))
                    closing_task = asyncio.create_task(scope.__aexit__(None, None, None))
                    await asyncio.wait_for(source_closed.wait(), 5)
                    # The source is shut, but its separate processor still owns
                    # a real native acceptance. A peer reader remains usable.
                    self.assertFalse(closing_task.done())
                    self.assertEqual([], peer.load_news_history("body", target=article["article_revision_id"]))
                    self.assertEqual(1, len(peer.load_news_history("article", target="005930")))
                    with closing(sqlite3.connect(path)) as connection:
                        self.assertEqual("RUNNING", connection.execute(
                            "SELECT state FROM central_news_jobs WHERE job_key=?", (claims[0]["job_key"],)).fetchone()[0])
                    self.assertNotIn("ai-close", events)
                    self.assertNotIn("store-close", events)
                    self.assertIsNone(wakeups[-1])
                    release.set()
                    if source_fails:
                        with self.assertRaises(RuntimeError) as raised:
                            await asyncio.wait_for(closing_task, 5)
                        self.assertIs(primary, raised.exception)
                        self.assertEqual({"state": "failed", "stage": "news"}, app.state.shutdown_status)
                        self.assertNotIn("ai-close", events)
                        self.assertNotIn("store-close", events)
                        if runner_fails:
                            self.assertIn("NEWS_RUNNER_CLOSE_FAILED:RuntimeError", primary.__notes__)
                    else:
                        await asyncio.wait_for(closing_task, 5)
                        self.assertEqual("completed", app.state.shutdown_status["state"])
                        self.assertLess(events.index("runner-close-exit"), events.index("ai-close"))
                        self.assertLess(events.index("ai-close"), events.index("store-close"))
                    self.assertLess(events.index("source-close"), events.index("runner-close-enter"))
                    self.assertLess(events.index("native-accepted"), events.index("runner-close-exit"))
                    self.assertEqual((), runner._tasks)
                    self.assertIsNone(runner._task)
                    self.assertIsNone(wakeups[-1])
                    bodies = peer.load_news_history("body", target=article["article_revision_id"])
                    self.assertEqual(1, len(bodies))
                    self.assertEqual(fixed.timestamp(), bodies[0]["fetched_at"])
                    # Reaccepting the same authority returns the original receipt
                    # and cannot replace the accepted BODY after owner close.
                    receipt = peer.accept_news_job_result(claims[0]["job_key"], {
                        "stage": "BODY", "body": {"body_text": "late replacement"},
                    }, attempts=claims[0]["attempts"])
                    self.assertEqual({"state": "already_completed", "output_ref": bodies[0]["body_revision_id"]}, receipt)
                    self.assertEqual(bodies, peer.load_news_history("body", target=article["article_revision_id"]))
            finally:
                release.set()
                if closing_task is None and scope_entered:
                    closing_task = asyncio.create_task(scope.__aexit__(None, None, None))
                if closing_task is not None:
                    await asyncio.gather(closing_task, return_exceptions=True)
                for source in sources:
                    await CentralNewsService.close(source)
                for runner, _ in runners:
                    await NewsJobRunner.close(runner)
                for owner in ai_owners:
                    await native_ai_close(owner)
                peer.close()
                native_close()

    async def test_headless_composition_drains_native_before_ai_store_and_keeps_peer_reader(self):
        await self._accepted_close()

    async def test_source_close_failure_still_drains_runner_and_keeps_primary_fail_stop(self):
        await self._accepted_close(source_fails=True)

    async def test_source_and_runner_close_failures_preserve_original_source_error(self):
        await self._accepted_close(source_fails=True, runner_fails=True)


if __name__ == "__main__":
    unittest.main()
