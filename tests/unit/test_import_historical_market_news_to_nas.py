from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from kiwoom_monitor.central_server.database import SQLiteQueryStore
from scripts.import_historical_market_news_to_nas import (
    _batch_id, _catalog_matcher, _initialize_ledger, _item, _mark, _pending,
)
from scripts.preprocess_historical_news_to_nas import prepare_job


class HistoricalMarketNewsImportTests(unittest.TestCase):
    def test_import_api_requires_authentication_and_accepts_market_batch(self) -> None:
        from fastapi.testclient import TestClient
        from kiwoom_monitor.central_server.app import create_app
        from kiwoom_monitor.central_server.config import CentralServerSettings
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "central.sqlite3"
            batch = {"source": "world", "target_date": "2020-01-02", "batch_id": "a" * 64,
                     "items": [{"identity": "world-1", "document": {
                         "title": "해외시장", "description": "해외 뉴스", "link": "https://example.com/1",
                         "published_at": "2020-01-01T15:00:01+00:00"},
                         "targets": [], "processing_excluded": False}]}
            with TestClient(create_app(CentralServerSettings(
                    f"sqlite:///{database}", "private-token"))) as client:
                endpoint = "/api/v1/news/historical-market-articles"
                self.assertEqual(401, client.post(endpoint, json=batch).status_code)
                headers = {"Authorization": "Bearer private-token"}
                self.assertEqual(200, client.post(endpoint, json=batch, headers=headers).status_code)
                self.assertEqual(1, len(client.get(
                    "/api/v1/news/market-feed?source=world", headers=headers).json()["items"]))
                pc_batch = {**batch, "batch_id": "c" * 64, "processing_owner": "pc",
                            "items": [{**batch["items"][0], "identity": "world-2",
                                       "document": {**batch["items"][0]["document"],
                                                    "link": "https://example.com/2"}}]}
                self.assertEqual(200, client.post(endpoint, json=pc_batch, headers=headers).status_code)
                claimed = client.post(
                    "/api/v1/news/historical-jobs/claim?stage=BODY&scope=pc_market",
                    headers=headers).json()
                self.assertEqual("world-2", claimed["article"]["identity"])
                self.assertEqual("historical_market_pc_backfill",
                                 claimed["article"]["collection_scope"])

    def test_archived_market_articles_stay_in_feed_with_or_without_stock_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, candidates = root / "market.sqlite3", root / "candidates.sqlite3"
            with closing(sqlite3.connect(candidates)) as connection, connection:
                connection.executescript(
                    "CREATE TABLE stocks(code TEXT,name TEXT,market_code TEXT);"
                    "CREATE TABLE candidate_days(code TEXT,dt TEXT);"
                    "INSERT INTO stocks VALUES('005930','삼성전자','0'),"
                    "('123456','삼성전자 ETF','8');"
                    "INSERT INTO candidate_days VALUES('005930','2020-01-02'),"
                    "('123456','2020-01-02');"
                )
            with closing(sqlite3.connect(archive)) as connection, connection:
                connection.executescript(
                    "CREATE TABLE market_news_articles (source TEXT,office_id TEXT,article_id TEXT,"
                    "published_at TEXT,title TEXT,summary TEXT,publisher TEXT,article_url TEXT);"
                    "INSERT INTO market_news_articles VALUES"
                    "('flash','001','a','2020-01-02T09:00:01+09:00',"
                    "'삼성전자 투자 확대','삼성전자 설비 투자','신문',"
                    "'https://n.news.naver.com/article/001/a'),"
                    "('flash','001','b','2020-01-02T09:01:01+09:00',"
                    "'코스피 시장 개장','증시 흐름','신문',"
                    "'https://n.news.naver.com/article/001/b'),"
                    "('flash','001','c','2020-01-02T09:02:01+09:00',"
                    "'축구 경기 결과','선수 우승 소식','신문',"
                    "'https://n.news.naver.com/article/001/c'),"
                    "('flash','001','d','2020-01-02T09:03:01+09:00',"
                    "'','본문 없음','신문',"
                    "'https://n.news.naver.com/article/001/d');"
                )
            _initialize_ledger(archive)
            rows = _pending(archive, 10)
            by_name, matcher = _catalog_matcher(candidates)
            prepared = [(row, _item(row, by_name, matcher)) for row in rows]
            self.assertIsNotNone(prepared[2][1])
            self.assertIsNone(prepared[3][1])
            _mark(archive, [prepared[3][0]], "filtered")
            rows = rows[:3]
            items = [item for _, item in prepared[:3]]
            self.assertEqual("005930", items[0]["targets"][0]["stock_code"])
            self.assertIsNone(items[1]["targets"][0]["stock_code"])
            store = SQLiteQueryStore(root / "central.sqlite3")
            store.initialize()
            batch = _batch_id("flash", "2020-01-02", items)
            result = store.save_historical_market_news_batch("flash", "2020-01-02", batch, items)
            self.assertEqual("imported", result["state"])
            self.assertEqual(3, len(store.load_market_news_feed("flash")))
            self.assertEqual("already_imported", store.save_historical_market_news_batch(
                "flash", "2020-01-02", batch, items)["state"])
            body_job = store.claim_external_historical_news_job("BODY")
            article = store.load_news_article_revision(body_job["article_revision_id"])
            store.complete_external_historical_news_job({
                "job_key": body_job["job_key"], "attempts": body_job["attempts"],
                "stage": "BODY", "body_text": "삼성전자 투자 확대. 시장 반응이 좋다.",
                "body_status": "fulltext",
            })
            rule_job = store.claim_external_historical_news_job("RULE")
            body = store.load_news_body_revision(rule_job["payload"]["body_revision_id"])
            store.complete_external_historical_news_job(prepare_job(rule_job, article, body))
            self.assertEqual(3, len(store.load_market_news_feed("flash")))
            self.assertTrue(store.load_market_news_feed("flash")[2].get("core_sentences"))
            _mark(archive, rows, "imported")
            self.assertEqual([], _pending(archive, 10))
            store.close()


if __name__ == "__main__":
    unittest.main()
