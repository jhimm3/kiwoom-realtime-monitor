from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from kiwoom_monitor.application.news_analysis import NewsAssessment
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.news_jobs import NewsJobRunner
from kiwoom_monitor.central_server.news_service import CentralNewsService
from kiwoom_monitor.infrastructure.naver_news import StockNewsItem


def article(title: str, *, identity: str = "article-1", description: str = ""):
    return [{
        "owner": "005930", "key": identity, "collector_id": "naver", "collection_scope": "watchlist",
        "document": {
            "stock_code": "005930", "stock_name": "테스트기업", "identity": identity,
            "title": title, "description": description, "link": f"https://news/{identity}",
            "original_link": f"https://origin/{identity}", "published_at": "2026-09-12T00:00:00+00:00",
            "relevant": 1, "category": "기업 활동", "outlook": "긍정", "reason": "",
            "relevance_score": 90, "outlook_score": 80,
        },
    }]


class NewsEventHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_authenticated_api_reads_event_and_membership_revisions(self) -> None:
        try:
            from fastapi.testclient import TestClient
            from kiwoom_monitor.central_server.app import create_app
            from kiwoom_monitor.central_server.config import CentralServerSettings
        except ImportError:
            self.skipTest("FastAPI test dependencies are unavailable")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "central.sqlite3"
            store = SQLiteQueryStore(path)
            store.initialize()
            event, _ = await self._record(store, "테스트기업, 삼성전자와 500억원 공급계약 체결")
            store.close()
            settings = CentralServerSettings(
                f"sqlite:///{path}", "private-token", news_history_jobs_enabled=False,
            )
            with TestClient(create_app(settings)) as client:
                self.assertEqual(401, client.get("/api/v1/news/history/event").status_code)
                response = client.get(
                    f"/api/v1/news/history/event?target=005930&identity={event['event_id']}",
                    headers={"Authorization": "Bearer private-token"},
                )
                membership = client.get(
                    f"/api/v1/news/history/membership?target={event['event_id']}",
                    headers={"Authorization": "Bearer private-token"},
                )
            self.assertEqual(event["event_revision_id"], response.json()["revisions"][0]["event_revision_id"])
            self.assertEqual(event["event_id"], membership.json()["revisions"][0]["event_id"])

    async def _record(self, store: SQLiteQueryStore, title: str, *, identity: str = "article-1",
                      body: str = "") -> tuple[dict, dict]:
        store.upsert_documents("news_article", article(title, identity=identity, description=body))
        runner = NewsJobRunner(store, fetcher=lambda *_args, **_kwargs: body or "본문")
        await runner.run_once()  # BODY
        await runner.run_once()  # RULE
        event = store.load_news_history("event", target="005930", limit=1)[0]
        membership = store.load_news_history("membership", target=event["event_id"], limit=1)[0]
        return event, membership

    async def test_event_id_is_persistent_and_not_url_while_revisions_append(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            first, first_membership = await self._record(
                store, "테스트기업, 삼성전자와 500억원 공급계약 체결",
            )
            second, second_membership = await self._record(
                store, "[정정] 테스트기업, 삼성전자와 500억원 공급계약 해지",
            )
            history = store.load_news_history("event", identity=first["event_id"])
            as_known_at_first = store.load_news_history(
                "event", identity=first["event_id"], available_at=first["available_at"],
            )
            store.close()

        self.assertEqual(first["event_id"], second["event_id"])
        self.assertFalse(first["event_id"].startswith("http"))
        self.assertEqual(first["event_revision_id"], second["revision_of"])
        self.assertEqual("UPDATE", second["novelty"])
        self.assertEqual(first_membership["membership_revision_id"], second_membership["revision_of"])
        self.assertEqual(2, len(history))
        self.assertEqual("CONFIRMED", history[-1]["certainty"])
        self.assertEqual([first["event_revision_id"]],
                         [row["event_revision_id"] for row in as_known_at_first])

    async def test_mou_to_contract_progression_reuses_event_only_with_matching_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            mou, _ = await self._record(
                store, "테스트기업, 삼성전자와 500억원 공급 MOU 체결", identity="mou",
            )
            contract, _ = await self._record(
                store, "테스트기업, 삼성전자와 500억원 공급계약 체결", identity="contract",
            )
            store.close()
        self.assertEqual("POTENTIAL", mou["certainty"])
        self.assertEqual(mou["event_id"], contract["event_id"])
        self.assertEqual("UPDATE", contract["novelty"])

    async def test_same_fact_from_another_article_is_marked_republication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            first, _ = await self._record(
                store, "테스트기업, 삼성전자와 500억원 공급계약 체결", identity="source-a",
            )
            repeated, _ = await self._record(
                store, "테스트기업 삼성전자와 500억원 공급계약 체결", identity="source-b",
            )
            store.close()
        self.assertEqual(first["event_id"], repeated["event_id"])
        self.assertEqual("REPUBLICATION", repeated["novelty"])
        self.assertEqual(10, repeated["novelty_score"])

    async def test_different_counterparty_creates_separate_possible_related_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            first, _ = await self._record(
                store, "테스트기업, 삼성전자와 500억원 공급계약 체결", identity="a",
            )
            second, _ = await self._record(
                store, "테스트기업, LG전자와 500억원 공급계약 체결", identity="b",
            )
            store.close()
        self.assertNotEqual(first["event_id"], second["event_id"])
        self.assertIn(first["event_id"], second["result"]["possible_related_event_ids"])

    async def test_service_article_name_reaches_rule_as_direct_target(self) -> None:
        class Client:
            def search(self, _name, *, since=None):
                return (StockNewsItem(
                    "테스트기업, 삼성전자와 500억원 공급계약 체결", "", "https://n/1", "https://o/1",
                    datetime(2026, 9, 12, tzinfo=UTC), NewsAssessment(True, "수주·계약", "긍정", "", 90, 80),
                ),)
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            service = CentralNewsService(Client(), store, jobs_enabled=False)  # type: ignore[arg-type]
            await service.search("005930", "테스트기업", None)
            runner = NewsJobRunner(store, fetcher=lambda *_args, **_kwargs: "공급계약 체결")
            await runner.run_once()
            await runner.run_once()
            event = store.load_news_history("event", target="005930", limit=1)[0]
            store.close()
        self.assertEqual("TARGET_COMPANY", event["scope"])
        self.assertEqual("DIRECT", event["result"]["targets"][0]["directness"])

    async def test_amount_change_is_separate_but_preserved_as_possible_related(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            first, _ = await self._record(
                store, "테스트기업, 삼성전자와 500억원 공급계약 체결", identity="amount-a",
            )
            corrected, _ = await self._record(
                store, "테스트기업, 삼성전자와 550억원 공급계약 체결", identity="amount-b",
            )
            store.close()
        self.assertNotEqual(first["event_id"], corrected["event_id"])
        self.assertIn(first["event_id"], corrected["result"]["possible_related_event_ids"])

    async def test_stale_rule_retry_reuses_committed_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "central.sqlite3"
            store = SQLiteQueryStore(path)
            store.initialize()
            store.upsert_documents("news_article", article("테스트기업, 삼성전자와 500억원 공급계약 체결"))
            runner = NewsJobRunner(store, fetcher=lambda *_args, **_kwargs: "공급계약 체결")
            await runner.run_once()
            rule_job = store.claim_news_jobs()[0]
            committed = await runner._run_rule(rule_job)
            connection = sqlite3.connect(path)
            connection.execute("UPDATE central_news_jobs SET updated_at=0 WHERE job_key=?", (rule_job["job_key"],))
            connection.commit(); connection.close()
            await runner.run_once()
            events = store.load_news_history("event", target="005930")
            connection = sqlite3.connect(path)
            state, output = connection.execute(
                "SELECT state,output_ref FROM central_news_jobs WHERE job_key=?", (rule_job["job_key"],),
            ).fetchone()
            connection.close(); store.close()
        self.assertEqual(1, len(events))
        self.assertEqual(("COMPLETED", committed), (state, output))

    async def test_membership_failure_rolls_back_event_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "central.sqlite3"
            store = SQLiteQueryStore(path)
            store.initialize()
            store.upsert_documents("news_article", article("테스트기업, 삼성전자와 500억원 공급계약 체결"))
            runner = NewsJobRunner(store, fetcher=lambda *_args, **_kwargs: "공급계약 체결")
            await runner.run_once()
            connection = sqlite3.connect(path)
            connection.execute(
                "CREATE TRIGGER reject_membership BEFORE INSERT ON central_news_event_membership_revisions "
                "BEGIN SELECT RAISE(ABORT,'membership rejected'); END"
            )
            connection.commit(); connection.close()
            await runner.run_once()
            self.assertEqual([], store.load_news_history("event", target="005930"))
            self.assertEqual([], store.load_news_history("membership"))
            store.close()


if __name__ == "__main__":
    unittest.main()
