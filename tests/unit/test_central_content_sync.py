from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope
from kiwoom_monitor.infrastructure.central_content_client import (
    CentralContentHttpError,
    CentralContentUnavailableError,
)
from kiwoom_monitor.infrastructure.central_content_sync import (
    CentralContentSyncService,
    _journal_news_link_key,
)
from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.infrastructure.persistence.stock_news_repository import StockNewsRepository


class _Client:
    def __init__(
        self, *, journal_v2: bool = False, journal_news_links_v2: bool = False,
    ) -> None:
        self.saved: dict[str, list[dict[str, object]]] = {}
        self.updated_after: list[tuple[str, float]] = []
        self.journal_v2 = journal_v2
        self.journal_news_links_v2 = journal_news_links_v2

    def capabilities(self) -> dict[str, bool]:
        return {
            "journal_v2_sync": self.journal_v2,
            "journal_news_links_v2": self.journal_news_links_v2,
        }

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
    def test_nas_mode_skips_bulk_news_catalog_but_keeps_local_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            news = root / "news.sqlite3"
            self._news_database(news, count=3)
            client = _Client()
            client.saved["news_article"] = [{
                "owner": "005930", "key": "nas-article", "updated_at": 10.0,
                "document": {
                    "stock_code": "005930", "identity": "nas-article", "title": "NAS 뉴스",
                },
            }]

            service = CentralContentSyncService(client)
            pulled = service.pull(root / "missing-main", news, sync_news_catalog=False)
            pushed = service.push(root / "missing-main", news, sync_news_catalog=False)

            requested = {name for name, _cursor in client.updated_after}
            self.assertNotIn("news_article", requested)
            self.assertNotIn("news_ai", requested)
            self.assertNotIn("news_ai_shared", requested)
            self.assertEqual(0, pulled.news_articles)
            self.assertEqual(0, pushed.news_articles)
            connection = sqlite3.connect(news)
            try:
                self.assertEqual(3, connection.execute("SELECT count(*) FROM stock_news").fetchone()[0])
            finally:
                connection.close()

    def test_noop_pull_does_not_rewrite_cursor_or_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            news = root / "news.sqlite3"
            self._news_database(news)
            service = CentralContentSyncService(_Client())
            service.seed_push_manifest(root / "main", news)

            with patch.object(service, "_save_pull_cursors") as save_cursor, patch.object(
                service, "_save_push_manifest",
            ) as save_manifest:
                result = service.pull(root / "main", news)

            self.assertEqual(0, result.total)
            save_cursor.assert_not_called()
            save_manifest.assert_not_called()

    def test_identical_pulled_hash_skips_local_and_manifest_write_but_change_persists(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            news = root / "news.sqlite3"
            self._news_database(news)
            client = _Client()
            service = CentralContentSyncService(client)
            service.seed_push_manifest(root / "main", news)
            client.saved["news_article"] = [{
                "owner": "005930", "key": "article-0", "updated_at": 12.0,
                "document": {"stock_code": "005930", "identity": "article-0", "title": "title-0"},
            }]

            with patch.object(service, "_save_push_manifest") as save_manifest:
                identical = service.pull(root / "main", news)
            self.assertEqual(0, identical.news_articles)
            save_manifest.assert_not_called()

            client.saved["news_article"][0] = {
                **client.saved["news_article"][0], "updated_at": 13.0,
                "document": {"stock_code": "005930", "identity": "article-0", "title": "changed"},
            }
            with patch.object(service, "_save_push_manifest") as save_manifest:
                changed = service.pull(root / "main", news)
            self.assertEqual(1, changed.news_articles)
            save_manifest.assert_called_once()
            connection = sqlite3.connect(news)
            title = connection.execute("SELECT title FROM stock_news").fetchone()[0]
            connection.close()
            self.assertEqual("changed", title)

    @staticmethod
    def _news_database(path: Path, count: int = 1) -> None:
        connection = sqlite3.connect(path)
        connection.execute(
            "CREATE TABLE stock_news(stock_code TEXT,identity TEXT,title TEXT,"
            "PRIMARY KEY(stock_code,identity))"
        )
        connection.executemany(
            "INSERT INTO stock_news VALUES(?,?,?)",
            [("005930", f"article-{index}", f"title-{index}") for index in range(count)],
        )
        connection.commit()
        connection.close()

    def test_push_manifest_skips_unchanged_bulk_and_sends_only_changed_document(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            news = root / "news.sqlite3"
            self._news_database(news, 100)
            client = _Client()
            service = CentralContentSyncService(client, batch_size=17)

            self.assertEqual(100, service.push(root / "missing-main", news).news_articles)
            self.assertEqual(0, service.push(root / "missing-main", news).news_articles)
            connection = sqlite3.connect(news)
            connection.execute(
                "UPDATE stock_news SET title='changed' WHERE identity='article-42'"
            )
            connection.commit()
            connection.close()
            before = len(client.saved["news_article"])
            self.assertEqual(1, service.push(root / "missing-main", news).news_articles)
            self.assertEqual(1, len(client.saved["news_article"]) - before)

    def test_legacy_seeded_database_baselines_before_first_incremental_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            news = root / "news.sqlite3"
            self._news_database(news, 100)
            (root / ".central_content_seeded").write_text("1\n", encoding="utf-8")
            client = _Client()
            service = CentralContentSyncService(client)
            client.saved["news_article"] = [{
                "owner": "005930", "key": "article-0", "updated_at": 12.0,
                "document": {"stock_code": "005930", "identity": "article-0", "title": "server"},
            }]
            needs_seed = not service.push_manifest_exists(news)

            pulled = service.pull(root / "missing-main", news)
            self.assertEqual(1, pulled.total)
            self.assertTrue(needs_seed)
            self.assertTrue(service.seed_push_manifest(
                root / "missing-main", news, allow_existing=needs_seed,
            ))
            self.assertFalse(service.seed_push_manifest(root / "missing-main", news))
            connection = sqlite3.connect(news)
            connection.execute("UPDATE stock_news SET title='changed' WHERE identity='article-42'")
            connection.commit()
            connection.close()

            result = CentralContentSyncService(client).push(root / "missing-main", news)

            self.assertEqual(1, result.news_articles)
            self.assertEqual("article-42", client.saved["news_article"][-1]["key"])

    def test_failed_push_batch_remains_pending_after_restart(self) -> None:
        class SecondBatchFailsOnce(_Client):
            def __init__(self):
                super().__init__()
                self.calls = 0

            def upsert(self, collection, documents):
                if collection == "news_article":
                    self.calls += 1
                    if self.calls == 2:
                        raise RuntimeError("temporary")
                return super().upsert(collection, documents)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            news = root / "news.sqlite3"
            self._news_database(news, 2)
            client = SecondBatchFailsOnce()
            with self.assertRaisesRegex(RuntimeError, "temporary"):
                CentralContentSyncService(client, batch_size=1).push(root / "main", news)

            result = CentralContentSyncService(client, batch_size=1).push(root / "main", news)
            self.assertEqual(1, result.news_articles)
            self.assertEqual("article-1", client.saved["news_article"][-1]["key"])

    def test_pulled_document_is_recorded_so_database_mtime_does_not_echo_push(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            news = root / "news.sqlite3"
            self._news_database(news, 0)
            client = _Client()
            client.saved["news_article"] = [{
                "owner": "005930", "key": "remote", "updated_at": 12.0,
                "document": {"stock_code": "005930", "identity": "remote", "title": "remote"},
            }]
            CentralContentSyncService(client).pull(root / "main", news)
            before = len(client.saved["news_article"])

            result = CentralContentSyncService(client).push(root / "main", news)

            self.assertEqual(0, result.news_articles)
            self.assertEqual(before, len(client.saved["news_article"]))

    def test_v2_news_key_uses_immutable_origin_not_canonical_alias(self) -> None:
        document = {
            "origin_broker": "kiwoom", "origin_environment": "real",
            "origin_account_ref": "11111111-1111-4111-8111-111111111111",
            "canonical_account_ref": "11111111-1111-4111-8111-111111111111",
            "group_id": "g", "stock_code": "005930", "identity": "article",
        }
        original = _journal_news_link_key(document)
        document["canonical_account_ref"] = "22222222-2222-4222-8222-222222222222"
        self.assertEqual(original, _journal_news_link_key(document))

    def test_account_scoped_news_links_use_v2_and_legacy_links_stay_in_v1(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            news, main = root / "news.sqlite3", root / "missing-main.sqlite3"
            repository = StockNewsRepository(news)
            real = AccountScope(
                "kiwoom", AccountEnvironment.REAL,
                "11111111-1111-4111-8111-111111111111",
            )
            repository.set_journal_link("legacy-group", "005930", "legacy-news", True)
            repository.set_journal_link(
                "real-group", "005930", "real-news", True, account_scope=real,
            )
            client = _Client(journal_v2=True, journal_news_links_v2=True)

            result = CentralContentSyncService(client).push(main, news)  # type: ignore[arg-type]

            self.assertEqual(2, result.journal_news_links)
            self.assertEqual("legacy", client.saved["journal_news_link"][0]["document"]["origin_broker"])
            scoped = client.saved["journal_v2_news_links"][0]
            self.assertEqual(real.account_ref, scoped["owner"])
            self.assertEqual("kiwoom", scoped["document"]["origin_broker"])

    def test_pull_keeps_v1_links_legacy_and_accepts_verified_v2_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            news, main = root / "news.sqlite3", root / "missing-main.sqlite3"
            repository = StockNewsRepository(news)
            real = AccountScope(
                "kiwoom", AccountEnvironment.REAL,
                "11111111-1111-4111-8111-111111111111",
            )
            client = _Client(journal_v2=True, journal_news_links_v2=True)
            client.saved = {
                "journal_news_link": [{
                    "owner": "legacy-group", "key": "005930|legacy-news",
                    "document": {
                        "group_id": "legacy-group", "stock_code": "005930",
                        "identity": "legacy-news", "origin_broker": "kiwoom",
                        "origin_environment": "real", "origin_account_ref": real.account_ref,
                        "canonical_account_ref": real.account_ref,
                    },
                }],
                "journal_v2_news_links": [{
                    "owner": real.account_ref, "key": "real-group|005930|real-news",
                    "document": {
                        "group_id": "real-group", "stock_code": "005930",
                        "identity": "real-news", "origin_broker": "kiwoom",
                        "origin_environment": "real", "origin_account_ref": real.account_ref,
                        "canonical_account_ref": real.account_ref,
                    },
                }],
            }

            result = CentralContentSyncService(client).pull(main, news)  # type: ignore[arg-type]

            self.assertEqual(2, result.journal_news_links)
            self.assertEqual(
                {"legacy-news"}, repository.journal_linked_identities("legacy-group", "005930"),
            )
            self.assertEqual(
                {"real-news"}, repository.journal_linked_identities("real-group", "005930", real),
            )
            self.assertEqual(
                set(), repository.journal_linked_identities("legacy-group", "005930", real),
            )

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
            self.assertEqual(
                "local_projection_sync", client.saved["news_article"][0]["collection_scope"],
            )
            self.assertTrue(str(client.saved["news_article"][0]["collector_id"]).startswith("local-sync:"))
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
                INSERT INTO profile_theme_name_decisions(
                    profile_id,decision_kind,source_name,target_name,decision_source
                ) SELECT profile_id,'alias','인공지능','AI','llm_review'
                  FROM theme_profiles WHERE profile_name='내 테마';
            """)
            connection.close()
            client = _Client()

            CentralContentSyncService(client).replace_themes(main)  # type: ignore[arg-type]

            metadata = client.saved["theme_metadata"][0]["document"]
            self.assertEqual("내 테마", metadata["active_profile"])
            self.assertIn({"alias": "삼전", "code": "005930"}, metadata["aliases"])
            self.assertIn({"code": "005930", "name": "삼성전자", "market": "KOSPI"}, metadata["stock_catalog"])
            profile = next(item for item in metadata["profiles"] if item["name"] == "내 테마")
            self.assertEqual("alias", profile["theme_name_decisions"][0]["kind"])
            self.assertEqual("인공지능", profile["theme_name_decisions"][0]["source"])
            self.assertEqual(
                metadata["created_at"], client.saved["theme_metadata"][0]["effective_at"],
            )
            self.assertTrue(client.saved["theme_metadata"][0]["origin_device"])

            connection = sqlite3.connect(main)
            connection.execute("UPDATE settings SET value='기본 테마' WHERE key='theme_active_profile'")
            connection.execute("DELETE FROM profile_theme_name_decisions")
            connection.commit(); connection.close()
            CentralContentSyncService(client).pull(main, Path(temporary) / "missing-news.sqlite3")  # type: ignore[arg-type]
            connection = sqlite3.connect(main)
            restored = connection.execute(
                "SELECT value FROM settings WHERE key='theme_active_profile'"
            ).fetchone()
            restored_decision = connection.execute(
                "SELECT decision_kind,source_name,target_name,decision_source "
                "FROM profile_theme_name_decisions"
            ).fetchone()
            connection.close()
            self.assertEqual(("내 테마",), restored)
            self.assertEqual(
                ("alias", "인공지능", "AI", "llm_review"), restored_decision,
            )

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

    def test_old_journal_v2_flag_does_not_enable_scoped_news_links(self) -> None:
        class MissingNewFlagClient(_Client):
            def capabilities(self):
                return {"journal_v2_sync": True}

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            news = root / "news.sqlite3"
            repository = StockNewsRepository(news)
            scope = AccountScope(
                "kiwoom", AccountEnvironment.REAL,
                "11111111-1111-4111-8111-111111111111",
            )
            repository.set_journal_link(
                "real-group", "005930", "real-news", True, account_scope=scope,
            )
            for client in (MissingNewFlagClient(), _Client(journal_v2=True)):
                with self.subTest(capabilities=client.capabilities()):
                    result = CentralContentSyncService(client).push(  # type: ignore[arg-type]
                        root / "missing-main", news,
                    )
                    self.assertEqual(0, result.journal_news_links)
                    self.assertNotIn("journal_v2_news_links", client.saved)
                    self.assertNotIn("journal_news_link", client.saved)
                    self.assertEqual(("journal_v2_news_links",), result.pending_collections)

    def test_optional_link_404_keeps_news_and_theme_sync_and_reports_pending(self) -> None:
        class MissingOptionalClient(_Client):
            def upsert(self, collection, documents):
                if collection == "journal_news_link":
                    raise CentralContentHttpError(404, "지원하지 않는 중앙 자료 종류입니다.")
                return super().upsert(collection, documents)

            def load_all(self, collection, **kwargs):
                if collection == "journal_news_link":
                    raise CentralContentHttpError(404, "지원하지 않는 중앙 자료 종류입니다.")
                return super().load_all(collection, **kwargs)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            news, main = root / "news.sqlite3", root / "missing-main.sqlite3"
            connection = sqlite3.connect(news)
            connection.executescript("""
                CREATE TABLE stock_news(stock_code TEXT,identity TEXT,title TEXT,PRIMARY KEY(stock_code,identity));
                INSERT INTO stock_news VALUES('005930','local','로컬 기사');
                CREATE TABLE journal_news_links(group_id TEXT,stock_code TEXT,identity TEXT,linked_at TEXT,
                    PRIMARY KEY(group_id,stock_code,identity));
                INSERT INTO journal_news_links VALUES('g','005930','local','2026-09-13T10:00:00');
            """)
            connection.close()
            client = MissingOptionalClient()
            service = CentralContentSyncService(client)

            pushed = service.push(main, news)  # type: ignore[arg-type]
            self.assertEqual(1, pushed.news_articles)
            self.assertEqual(("journal_news_link",), pushed.pending_collections)

            client.saved["news_article"] = [{
                "owner": "005930", "key": "remote", "updated_at": 12.0,
                "document": {"stock_code": "005930", "identity": "remote", "title": "중앙 기사"},
            }]
            pulled = service.pull(main, news)  # type: ignore[arg-type]
            self.assertEqual(1, pulled.news_articles)
            self.assertEqual(
                ("journal_v2_news_links", "journal_news_link"),
                pulled.pending_collections,
            )
            connection = sqlite3.connect(news)
            self.assertEqual(2, connection.execute("SELECT COUNT(*) FROM stock_news").fetchone()[0])
            connection.close()

    def test_hard_failure_after_first_collection_does_not_advance_cursor_and_retry_refetches(self) -> None:
        class OnceFailingClient(_Client):
            def __init__(self):
                super().__init__()
                self.fail = True

            def load_all(self, collection, **kwargs):
                if collection == "news_ai" and self.fail:
                    self.fail = False
                    raise CentralContentHttpError(500, "temporary")
                return super().load_all(collection, **kwargs)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            news, main = root / "news.sqlite3", root / "missing-main.sqlite3"
            connection = sqlite3.connect(news)
            connection.executescript("""
                CREATE TABLE stock_news(stock_code TEXT,identity TEXT,title TEXT,PRIMARY KEY(stock_code,identity));
                CREATE TABLE stock_news_ai(stock_code TEXT,identity TEXT,summary TEXT,PRIMARY KEY(stock_code,identity));
                CREATE TABLE news_ai_shared(identity TEXT PRIMARY KEY,summary TEXT);
            """)
            connection.close()
            client = OnceFailingClient()
            client.saved["news_article"] = [{
                "owner": "005930", "key": "a", "updated_at": 50.0,
                "document": {"stock_code": "005930", "identity": "a", "title": "뉴스"},
            }]
            service = CentralContentSyncService(client)

            with self.assertRaises(CentralContentHttpError) as caught:
                service.pull(main, news)  # type: ignore[arg-type]
            self.assertEqual(500, caught.exception.status_code)
            self.assertFalse((root / "central_content_cursor.json").exists())

            client.updated_after.clear()
            result = service.pull(main, news)  # type: ignore[arg-type]
            self.assertEqual(1, result.news_articles)
            self.assertIn(("news_article", 0.0), client.updated_after)

    def test_optional_handling_does_not_hide_auth_or_transport_failures(self) -> None:
        for error in (
            CentralContentHttpError(401, "unauthorized"),
            CentralContentHttpError(500, "server error"),
            CentralContentUnavailableError("중앙 자료 서버에 연결할 수 없습니다."),
        ):
            class FailingClient(_Client):
                def load_all(self, collection, **kwargs):
                    if collection == "journal_news_link":
                        raise error
                    return super().load_all(collection, **kwargs)

            with self.subTest(error=type(error).__name__, message=str(error)):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    news = root / "news.sqlite3"
                    StockNewsRepository(news)
                    with self.assertRaises(type(error)):
                        CentralContentSyncService(FailingClient()).pull(  # type: ignore[arg-type]
                            root / "missing-main", news,
                        )

    def test_capability_auth_server_and_transport_failures_are_not_read_as_false(self) -> None:
        for error in (
            CentralContentHttpError(401, "unauthorized"),
            CentralContentHttpError(500, "server error"),
            CentralContentUnavailableError("중앙 자료 서버에 연결할 수 없습니다."),
        ):
            class FailingCapabilityClient(_Client):
                def capabilities(self):
                    raise error

            with self.subTest(error=type(error).__name__, message=str(error)):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    with self.assertRaises(type(error)):
                        CentralContentSyncService(FailingCapabilityClient()).push(  # type: ignore[arg-type]
                            root / "missing-main", root / "missing-news",
                        )


if __name__ == "__main__":
    unittest.main()
