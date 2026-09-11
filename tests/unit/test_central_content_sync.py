from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from kiwoom_monitor.infrastructure.central_content_sync import CentralContentSyncService
from kiwoom_monitor.infrastructure.persistence.database import Database


class _Client:
    def __init__(self) -> None:
        self.saved: dict[str, list[dict[str, object]]] = {}
        self.updated_after: list[tuple[str, float]] = []

    def upsert(self, collection: str, documents: list[dict[str, object]]) -> int:
        self.saved.setdefault(collection, []).extend(documents)
        return len(documents)

    def replace(self, collection: str, documents: list[dict[str, object]]) -> int:
        self.saved[collection] = list(documents)
        return len(documents)

    def load_all(self, collection: str, **kwargs) -> list[dict[str, object]]:
        cursor = float(kwargs.get("updated_after", 0.0))
        self.updated_after.append((collection, cursor))
        return [
            value for value in self.saved.get(collection, [])
            if float(value.get("updated_at", cursor + 1.0)) > cursor
        ]


class CentralContentSyncServiceTest(unittest.TestCase):
    def test_pushes_news_ai_and_profile_themes_with_stable_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            news, main = root / "news.sqlite3", root / "monitor.sqlite3"
            connection = sqlite3.connect(news)
            connection.executescript("""
                CREATE TABLE stock_news(stock_code TEXT,identity TEXT,title TEXT);
                INSERT INTO stock_news VALUES('005930','article-1','삼성전자 뉴스');
                CREATE TABLE stock_news_ai(stock_code TEXT,identity TEXT,summary TEXT);
                INSERT INTO stock_news_ai VALUES('005930','article-1','요약');
                CREATE TABLE news_ai_shared(identity TEXT,summary TEXT);
                INSERT INTO news_ai_shared VALUES('article-1','공통 요약');
                CREATE TABLE news_ai_requests(id INTEGER,requested_at TEXT,provider TEXT,model TEXT);
                INSERT INTO news_ai_requests VALUES(1,'2026-09-08T09:00:00','gemini','flash');
                CREATE TABLE journal_news_links(group_id TEXT,stock_code TEXT,identity TEXT,linked_at TEXT);
                INSERT INTO journal_news_links VALUES('group-1','005930','article-1','2026-09-08T10:00:00');
            """)
            connection.close()
            connection = sqlite3.connect(main)
            connection.executescript("""
                CREATE TABLE theme_profiles(profile_id INTEGER,profile_name TEXT);
                INSERT INTO theme_profiles VALUES(1,'기본 테마');
                CREATE TABLE profile_themes(profile_id INTEGER,theme_name TEXT,default_color TEXT);
                INSERT INTO profile_themes VALUES(1,'반도체','#fff');
                CREATE TABLE profile_stock_themes(profile_id INTEGER,stock_code TEXT,theme_name TEXT,custom_color TEXT);
                INSERT INTO profile_stock_themes VALUES(1,'005930','반도체',NULL);
            """)
            connection.close()

            client = _Client()
            result = CentralContentSyncService(client, batch_size=1).push(main, news)  # type: ignore[arg-type]

            self.assertEqual(result.total, 7)
            self.assertEqual(client.saved["news_article"][0]["owner"], "005930")
            self.assertEqual(client.saved["news_article"][0]["key"], "article-1")
            self.assertEqual(client.saved["theme_stock"][0]["key"], "005930|반도체")
            self.assertEqual(client.saved["journal_news_link"][0]["owner"], "group-1")

    def test_missing_databases_are_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = CentralContentSyncService(_Client()).push(root / "missing-main", root / "missing-news")  # type: ignore[arg-type]
            self.assertEqual(result.total, 0)

    def test_replace_themes_removes_deleted_central_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            main = Path(temporary) / "monitor.sqlite3"
            connection = sqlite3.connect(main)
            connection.executescript("""
                CREATE TABLE theme_profiles(profile_id INTEGER,profile_name TEXT);
                INSERT INTO theme_profiles VALUES(1,'기본 테마');
                CREATE TABLE profile_themes(profile_id INTEGER,theme_name TEXT,default_color TEXT);
                INSERT INTO profile_themes VALUES(1,'원전','#fff');
                CREATE TABLE profile_stock_themes(profile_id INTEGER,stock_code TEXT,theme_name TEXT,custom_color TEXT);
            """)
            connection.close()
            client = _Client()
            client.saved["theme_profile"] = [{"key": "삭제된 테마"}]
            client.saved["theme_stock"] = [{"key": "005930|삭제된 테마"}]

            result = CentralContentSyncService(client).replace_themes(main)  # type: ignore[arg-type]

            self.assertEqual(1, result.theme_profiles)
            self.assertEqual([], client.saved["theme_stock"])
            self.assertEqual("원전", client.saved["theme_profile"][0]["key"])

    def test_load_theme_snapshot_uses_metadata_as_completed_timestamp(self) -> None:
        client = _Client()
        client.saved = {
            "theme_profile": [{"updated_at": 103.0}],
            "theme_stock": [{"updated_at": 104.0}],
            "theme_metadata": [
                {"owner": "default", "key": "full", "updated_at": 102.0, "document": {}},
                {"owner": "default", "key": "old", "updated_at": 101.0, "document": {}},
            ],
        }

        snapshot, completed_at = CentralContentSyncService(client).load_theme_snapshot()  # type: ignore[arg-type]

        self.assertEqual(102.0, completed_at)
        self.assertEqual(1, len(snapshot["theme_profile"]))
        self.assertEqual(
            [("theme_profile", 0.0), ("theme_stock", 0.0), ("theme_metadata", 0.0)],
            client.updated_after,
        )

    def test_full_theme_metadata_preserves_active_profile_aliases_and_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            main = Path(temporary) / "monitor.sqlite3"
            Database(main).initialize()
            connection = sqlite3.connect(main)
            connection.executescript("""
                INSERT OR REPLACE INTO stocks(code,name,market) VALUES('005930','삼성전자','KOSPI');
                INSERT OR REPLACE INTO stock_aliases(alias,stock_code) VALUES('삼전','005930');
                INSERT INTO theme_profiles(profile_name) VALUES('내 테마');
                UPDATE settings SET value='내 테마' WHERE key='theme_active_profile';
            """)
            connection.close()
            client = _Client()

            CentralContentSyncService(client).replace_themes(main)  # type: ignore[arg-type]

            metadata = client.saved["theme_metadata"][0]["document"]
            self.assertEqual("내 테마", metadata["active_profile"])
            self.assertIn({"alias": "삼전", "code": "005930"}, metadata["aliases"])
            self.assertIn({"code": "005930", "name": "삼성전자", "market": "KOSPI"}, metadata["stock_catalog"])

            connection = sqlite3.connect(main)
            connection.execute("UPDATE settings SET value='기본 테마' WHERE key='theme_active_profile'")
            connection.commit(); connection.close()
            CentralContentSyncService(client).pull(main, Path(temporary) / "missing-news.sqlite3")  # type: ignore[arg-type]
            connection = sqlite3.connect(main)
            restored = connection.execute(
                "SELECT value FROM settings WHERE key='theme_active_profile'"
            ).fetchone()
            connection.close()
            self.assertEqual(("내 테마",), restored)

    def test_pulls_central_news_and_themes_into_local_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            news, main = root / "news.sqlite3", root / "monitor.sqlite3"
            connection = sqlite3.connect(news)
            connection.executescript("""
                CREATE TABLE stock_news(stock_code TEXT,identity TEXT,title TEXT,PRIMARY KEY(stock_code,identity));
                CREATE TABLE stock_news_ai(stock_code TEXT,identity TEXT,summary TEXT,PRIMARY KEY(stock_code,identity));
                CREATE TABLE news_ai_shared(identity TEXT PRIMARY KEY,summary TEXT);
                CREATE TABLE journal_news_links(group_id TEXT,stock_code TEXT,identity TEXT,linked_at TEXT,PRIMARY KEY(group_id,stock_code,identity));
            """)
            connection.close()
            connection = sqlite3.connect(main)
            connection.executescript("""
                CREATE TABLE theme_profiles(profile_id INTEGER PRIMARY KEY,profile_name TEXT UNIQUE COLLATE NOCASE);
                CREATE TABLE profile_themes(profile_id INTEGER,theme_name TEXT COLLATE NOCASE,default_color TEXT DEFAULT '#DCE6F1',PRIMARY KEY(profile_id,theme_name));
                CREATE TABLE profile_stock_themes(profile_id INTEGER,stock_code TEXT,theme_name TEXT COLLATE NOCASE,custom_color TEXT,PRIMARY KEY(profile_id,stock_code,theme_name));
            """)
            connection.close()
            client = _Client()
            client.saved = {
                "news_article": [{"owner": "005930", "key": "a", "document": {"stock_code": "005930", "identity": "a", "title": "중앙 뉴스"}}],
                "news_ai": [], "news_ai_shared": [],
                "journal_news_link": [{"owner": "group-1", "key": "005930|a", "document": {"group_id": "group-1", "stock_code": "005930", "identity": "a", "linked_at": "2026-09-08T10:00:00"}}],
                "theme_profile": [{"owner": "기본 테마", "key": "반도체", "document": {"profile_name": "기본 테마", "theme_name": "반도체", "default_color": "#fff"}}],
                "theme_stock": [{"owner": "기본 테마", "key": "005930|반도체", "document": {"profile_name": "기본 테마", "stock_code": "005930", "theme_name": "반도체", "custom_color": None}}],
            }

            result = CentralContentSyncService(client).pull(main, news)  # type: ignore[arg-type]

            self.assertEqual(result.total, 4)
            connection = sqlite3.connect(news)
            self.assertEqual("중앙 뉴스", connection.execute("SELECT title FROM stock_news").fetchone()[0])
            self.assertEqual(("group-1",), connection.execute("SELECT group_id FROM journal_news_links").fetchone())
            connection.close()
            connection = sqlite3.connect(main)
            self.assertEqual("반도체", connection.execute("SELECT theme_name FROM profile_stock_themes").fetchone()[0])
            connection.close()

    def test_pull_cursor_survives_service_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            news, main = root / "news.sqlite3", root / "monitor.sqlite3"
            connection = sqlite3.connect(news)
            connection.executescript("""
                CREATE TABLE stock_news(stock_code TEXT,identity TEXT,title TEXT,PRIMARY KEY(stock_code,identity));
                CREATE TABLE stock_news_ai(stock_code TEXT,identity TEXT,summary TEXT,PRIMARY KEY(stock_code,identity));
                CREATE TABLE news_ai_shared(identity TEXT PRIMARY KEY,summary TEXT);
            """)
            connection.close()
            connection = sqlite3.connect(main)
            connection.executescript("""
                CREATE TABLE theme_profiles(profile_id INTEGER PRIMARY KEY,profile_name TEXT UNIQUE COLLATE NOCASE);
                CREATE TABLE profile_themes(profile_id INTEGER,theme_name TEXT COLLATE NOCASE,default_color TEXT DEFAULT '#DCE6F1',PRIMARY KEY(profile_id,theme_name));
                CREATE TABLE profile_stock_themes(profile_id INTEGER,stock_code TEXT,theme_name TEXT COLLATE NOCASE,custom_color TEXT,PRIMARY KEY(profile_id,stock_code,theme_name));
            """)
            connection.close()
            client = _Client()
            client.saved["news_article"] = [{
                "owner": "005930", "key": "a", "updated_at": 123.0,
                "document": {"stock_code": "005930", "identity": "a", "title": "뉴스"},
            }]

            CentralContentSyncService(client).pull(main, news)  # type: ignore[arg-type]
            client.updated_after.clear()
            result = CentralContentSyncService(client).pull(main, news)  # type: ignore[arg-type]

            self.assertEqual(0, result.total)
            self.assertIn(("news_article", 123.0), client.updated_after)


if __name__ == "__main__":
    unittest.main()
