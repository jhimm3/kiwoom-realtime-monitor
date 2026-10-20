from __future__ import annotations

import tempfile
import sqlite3
from contextlib import closing
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from kiwoom_monitor.application.news_analysis import NewsAssessment
from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope
from kiwoom_monitor.infrastructure.naver_news import StockNewsItem
from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.infrastructure.persistence.stock_news_repository import StockNewsRepository
from kiwoom_monitor.infrastructure.persistence.news_ai_repository import news_identity


class StockNewsRepositoryTest(unittest.TestCase):
    @staticmethod
    def _observe_native_writes():
        from contextlib import contextmanager
        from unittest.mock import patch
        from kiwoom_monitor.infrastructure.persistence import stock_news_repository as module

        @contextmanager
        def observe():
            metrics = {"connections": 0, "article_rows": 0, "receipt_rows": 0, "deleted_rows": 0}
            statements = []
            original_transaction = module.sqlite_transaction

            class ObservedConnection:
                def __init__(self, connection):
                    self.connection = connection

                def executemany(self, statement, parameters):
                    cursor = self.connection.executemany(statement, parameters)
                    if statement.startswith("INSERT INTO stock_news("):
                        metrics["article_rows"] += cursor.rowcount
                    return cursor

                def execute(self, statement, parameters=()):
                    cursor = self.connection.execute(statement, parameters)
                    if statement.startswith("INSERT INTO stock_news_sync("):
                        metrics["receipt_rows"] += cursor.rowcount
                    elif statement.startswith("DELETE FROM stock_news "):
                        metrics["deleted_rows"] += cursor.rowcount
                    return cursor

            @contextmanager
            def transaction(*args, **kwargs):
                with original_transaction(*args, **kwargs) as connection:
                    metrics["connections"] += 1
                    before = connection.total_changes
                    connection.set_trace_callback(statements.append)
                    yield ObservedConnection(connection)
                    metrics["row_changes"] = connection.total_changes - before

            with patch.object(module, "sqlite_transaction", transaction):
                yield metrics
            normalized = [statement.strip().upper() for statement in statements]
            metrics.update(selects=sum(statement.startswith("SELECT") for statement in normalized),
                           begins=normalized.count("BEGIN"), commits=normalized.count("COMMIT"))
        return observe()

    @staticmethod
    def _item(index: int) -> StockNewsItem:
        return StockNewsItem(
            f"기사 {index}", "요약", f"https://n.news.naver.com/{index}", f"https://example.com/{index}",
            datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=index),
            NewsAssessment(True, "기타 증권뉴스", "판단 자료 부족", "", 50, 0),
        )

    def test_saves_news_and_reuses_it_until_the_next_check(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "monitor.sqlite3"
            Database(path).initialize()
            now = datetime(2026, 10, 18, 9, tzinfo=UTC)
            repository = StockNewsRepository(path, now_provider=lambda: now)
            item = StockNewsItem(
                "삼성전자 공급계약 수주",
                "영업이익 증가 기대",
                "https://n.news.naver.com/article/1",
                "https://example.com/article/1",
                datetime(2026, 8, 26, 9, 10, tzinfo=UTC),
                NewsAssessment(True, "수주·계약", "호재 가능성 높음", "수주 표현을 확인했습니다.", 10, 5),
            )

            self.assertEqual(1, repository.upsert("005930", (item,)))
            self.assertEqual(0, repository.upsert("005930", (item,)))
            self.assertTrue(repository.recently_checked("005930", 180))
            now += timedelta(seconds=179)
            self.assertTrue(repository.recently_checked("005930", 180))
            now += timedelta(seconds=1)
            self.assertFalse(repository.recently_checked("005930", 180))
            self.assertIsNone(repository.last_naver_checked_at("005930"))

            checked_at = now
            repository.upsert("005930", (), naver_checked_at=checked_at)
            self.assertEqual(checked_at, repository.last_naver_checked_at("005930"))

            loaded = repository.load("005930")
            self.assertEqual(1, len(loaded))
            self.assertEqual(item.title, loaded[0].title)
            self.assertEqual("호재 가능성 높음", loaded[0].assessment.outlook)

    def test_identical_batch_preserves_articles_and_advances_only_check_receipts(self) -> None:
        from dataclasses import replace

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "news.sqlite3"
            now = datetime.fromisoformat("2030-01-02T10:00:00+09:00")
            repository = StockNewsRepository(path, now_provider=lambda: now)
            items = tuple(self._item(index) for index in range(5))
            self.assertEqual(5, repository.upsert("005930", items, naver_checked_at=now))
            with closing(sqlite3.connect(path)) as connection:
                original_rows = connection.execute("SELECT * FROM stock_news ORDER BY identity").fetchall()
            next_check = now + timedelta(seconds=10)
            with self._observe_native_writes() as metrics:
                self.assertEqual(0, repository.upsert("005930", items, next_check))
            self.assertEqual({"connections": 1, "article_rows": 0, "receipt_rows": 1, "deleted_rows": 0,
                              "row_changes": 1, "selects": 1, "begins": 1, "commits": 1}, metrics)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(original_rows, connection.execute("SELECT * FROM stock_news ORDER BY identity").fetchall())
                self.assertEqual((next_check.isoformat(), now.isoformat()), connection.execute(
                    "SELECT checked_at,naver_checked_at FROM stock_news_sync WHERE stock_code='005930'"
                ).fetchone())
            corrected = replace(items[0], description="정정된 본문")
            with self._observe_native_writes() as changed:
                self.assertEqual(0, repository.upsert("005930", (corrected, *items[1:]), next_check,
                                                      naver_checked_at=next_check))
            self.assertEqual((1, 1, 2), (changed["article_rows"], changed["receipt_rows"], changed["row_changes"]))
            self.assertEqual(next_check, repository.last_naver_checked_at("005930"))
            self.assertEqual(corrected, next(item for item in repository.load("005930") if item.original_link == corrected.original_link))
            with self._observe_native_writes() as partial:
                self.assertEqual(0, repository.upsert("005930", items[1:3], next_check))
            self.assertEqual((0, 1, 0), (partial["article_rows"], partial["receipt_rows"], partial["deleted_rows"]))
            self.assertEqual(5, len(repository.load("005930")))

    def test_each_content_correction_and_nullable_publication_are_saved_from_current_db(self) -> None:
        from dataclasses import replace

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "news.sqlite3"
            now = datetime.fromisoformat("2030-01-02T10:00:00+09:00")
            repository = StockNewsRepository(path, now_provider=lambda: now)
            item = self._item(0)
            repository.upsert("005930", (item,))
            identity = news_identity(item)
            repository.set_journal_link("g", "005930", identity, True)
            corrections = {"title": "외부 제목", "description": "외부 본문", "link": "https://other.test/page",
                           "original_link": "", "published_at": None, "relevant": 0, "category": "다른분류",
                           "outlook": "다른판단", "reason": "추가근거", "relevance_score": 51, "outlook_score": 1}
            with closing(sqlite3.connect(path)) as connection:
                original_seen = connection.execute("SELECT first_seen_at FROM stock_news").fetchone()[0]
                original_link = connection.execute("SELECT * FROM journal_news_links").fetchall()
            for column, value in corrections.items():
                with self.subTest(corrected_column=column):
                    # Simulate another writer; the submitted object is deliberately unchanged in RAM.
                    with closing(sqlite3.connect(path)) as connection, connection:
                        connection.execute(f"UPDATE stock_news SET {column}=? WHERE stock_code=? AND identity=?",
                                           (value, "005930", identity))
                    with self._observe_native_writes() as metrics:
                        self.assertEqual(0, repository.upsert("005930", (item,)))
                    self.assertEqual((1, 1, 2), (metrics["article_rows"], metrics["receipt_rows"], metrics["row_changes"]))
                    self.assertEqual((item,), repository.load("005930"))
            missing_time = replace(item, published_at=None)
            for submitted, affected in ((missing_time, 1), (missing_time, 0), (item, 1)):
                with self._observe_native_writes() as metrics:
                    self.assertEqual(0, repository.upsert("005930", (submitted,)))
                self.assertEqual(affected, metrics["article_rows"])
                self.assertEqual((submitted,), repository.load_journal_linked("g", "005930"))
            new_item = replace(item, original_link="https://publisher.test/new-identity")
            with self._observe_native_writes() as metrics:
                self.assertEqual(1, repository.upsert("005930", (new_item,)))
            self.assertEqual((1, 1, 2), (metrics["article_rows"], metrics["receipt_rows"], metrics["row_changes"]))
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(original_seen, connection.execute(
                    "SELECT first_seen_at FROM stock_news WHERE stock_code=? AND identity=?", ("005930", identity)
                ).fetchone()[0])
                self.assertEqual(original_link, connection.execute("SELECT * FROM journal_news_links").fetchall())

    def test_failed_receipt_rolls_back_article_changes_and_retry_keeps_original_pin(self) -> None:
        from dataclasses import replace

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "news.sqlite3"
            now = datetime.fromisoformat("2030-01-02T10:00:00+09:00")
            repository = StockNewsRepository(path, now_provider=lambda: now)
            original = self._item(0)
            repository.upsert("005930", (original,), naver_checked_at=now)
            repository.set_journal_link("g", "005930", news_identity(original), True)
            with closing(sqlite3.connect(path)) as connection, connection:
                article_rows = connection.execute("SELECT * FROM stock_news").fetchall()
                receipt_rows = connection.execute("SELECT * FROM stock_news_sync").fetchall()
                links = connection.execute("SELECT * FROM journal_news_links").fetchall()
                connection.execute("CREATE TRIGGER reject_news_receipt BEFORE UPDATE ON stock_news_sync "
                                   "BEGIN SELECT RAISE(ABORT,'receipt failure'); END")
            corrected = replace(original, description="정정 본문", published_at=None,
                                assessment=NewsAssessment(True, "수주·계약", "호재", "정정 근거", 80, 70))
            checked = now + timedelta(minutes=1)
            with self.assertRaisesRegex(sqlite3.IntegrityError, "receipt failure"):
                repository.upsert("005930", (corrected, self._item(1)), checked, naver_checked_at=checked)
            with closing(sqlite3.connect(path)) as connection, connection:
                self.assertEqual(article_rows, connection.execute("SELECT * FROM stock_news").fetchall())
                self.assertEqual(receipt_rows, connection.execute("SELECT * FROM stock_news_sync").fetchall())
                self.assertEqual(links, connection.execute("SELECT * FROM journal_news_links").fetchall())
                connection.execute("DROP TRIGGER reject_news_receipt")
            self.assertEqual(1, repository.upsert("005930", (corrected, self._item(1)), checked,
                                                naver_checked_at=checked))
            self.assertEqual((corrected,), repository.load_journal_linked("g", "005930"))
            with self._observe_native_writes() as retry:
                self.assertEqual(0, repository.upsert("005930", (corrected, self._item(1)), checked))
            self.assertEqual((0, 1, 1), (retry["article_rows"], retry["receipt_rows"], retry["row_changes"]))

    def test_journal_linked_news_is_not_removed_by_two_hundred_item_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = StockNewsRepository(Path(temporary_directory) / "news.sqlite3")
            pinned = self._item(0)
            repository.set_journal_link("group-1", "005930", news_identity(pinned), True)
            repository.upsert("005930", tuple(self._item(index) for index in range(201)))
            self.assertIn(news_identity(pinned), repository.journal_linked_identities("group-1", "005930"))
            self.assertEqual((pinned.title,), tuple(item.title for item in repository.load_journal_linked("group-1", "005930")))
            with self._observe_native_writes() as repeated:
                self.assertEqual(0, repository.upsert("005930", tuple(self._item(index) for index in range(201))))
            self.assertEqual((0, 1, 0), (repeated["article_rows"], repeated["receipt_rows"], repeated["deleted_rows"]))
            self.assertEqual((pinned,), repository.load_journal_linked("group-1", "005930"))

    def test_configured_limit_controls_both_storage_and_default_load(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "news.sqlite3"
            items = tuple(self._item(index) for index in range(250))
            repository = StockNewsRepository(path, stored_news_limit=300)
            repository.upsert("005930", items)
            self.assertEqual(250, len(repository.load("005930")))
            self.assertEqual(200, len(repository.load("005930", limit=200)))

            smaller = StockNewsRepository(path, stored_news_limit=100)
            smaller.upsert("005930", ())
            self.assertEqual(100, len(smaller.load("005930")))
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(100, connection.execute(
                    "SELECT COUNT(*) FROM stock_news WHERE stock_code='005930'",
                ).fetchone()[0])

    def test_journal_news_links_are_separated_by_account_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = StockNewsRepository(Path(temporary_directory) / "news.sqlite3")
            item = self._item(0)
            repository.upsert("005930", (item,))
            real = AccountScope(
                "kiwoom", AccountEnvironment.REAL,
                "11111111-1111-4111-8111-111111111111",
            )
            mock = AccountScope(
                "kiwoom", AccountEnvironment.MOCK,
                "22222222-2222-4222-8222-222222222222",
            )

            repository.set_journal_link(
                "same-group", "005930", news_identity(item), True, account_scope=real,
            )

            self.assertEqual(
                {news_identity(item)},
                repository.journal_linked_identities("same-group", "005930", real),
            )
            self.assertEqual(set(), repository.journal_linked_identities("same-group", "005930", mock))
            self.assertEqual((), repository.load_journal_linked("same-group", "005930", mock))

    def test_unlink_keeps_tombstone_and_explicit_relink_advances_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "news.sqlite3"
            now = datetime(2026, 10, 18, 0, 0, tzinfo=UTC)
            repository = StockNewsRepository(path, now_provider=lambda: now)
            repository.set_journal_link("g", "005930", "article", True)
            now += timedelta(seconds=1)
            repository.set_journal_link("g", "005930", "article", False)
            with closing(sqlite3.connect(path)) as connection:
                deleted = connection.execute(
                    "SELECT linked_at,updated_at,is_deleted FROM journal_news_links"
                ).fetchone()
            self.assertEqual(1, deleted[2])
            self.assertEqual(set(), repository.journal_linked_identities("g", "005930"))

            now += timedelta(seconds=1)
            repository.set_journal_link("g", "005930", "article", True)
            with closing(sqlite3.connect(path)) as connection:
                restored = connection.execute(
                    "SELECT linked_at,updated_at,is_deleted FROM journal_news_links"
                ).fetchone()
            self.assertEqual(deleted[0], restored[0])
            self.assertEqual(now, datetime.fromisoformat(restored[1]))
            self.assertGreater(datetime.fromisoformat(restored[1]), datetime.fromisoformat(deleted[1]))
            self.assertEqual(0, restored[2])


if __name__ == "__main__":
    unittest.main()
