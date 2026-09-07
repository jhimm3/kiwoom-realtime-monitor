from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from kiwoom_monitor.application.news_analysis import NewsAssessment
from kiwoom_monitor.infrastructure.naver_news import StockNewsItem
from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.infrastructure.persistence.stock_news_repository import StockNewsRepository
from kiwoom_monitor.infrastructure.persistence.news_ai_repository import news_identity


class StockNewsRepositoryTest(unittest.TestCase):
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
            repository = StockNewsRepository(path)
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
            self.assertIsNone(repository.last_naver_checked_at("005930"))

            checked_at = datetime.now(UTC)
            repository.upsert("005930", (), naver_checked_at=checked_at)
            self.assertEqual(checked_at, repository.last_naver_checked_at("005930"))

            loaded = repository.load("005930")
            self.assertEqual(1, len(loaded))
            self.assertEqual(item.title, loaded[0].title)
            self.assertEqual("호재 가능성 높음", loaded[0].assessment.outlook)

    def test_journal_linked_news_is_not_removed_by_two_hundred_item_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = StockNewsRepository(Path(temporary_directory) / "news.sqlite3")
            pinned = self._item(0)
            repository.set_journal_link("group-1", "005930", news_identity(pinned), True)
            repository.upsert("005930", tuple(self._item(index) for index in range(201)))
            self.assertIn(news_identity(pinned), repository.journal_linked_identities("group-1", "005930"))
            self.assertEqual((pinned.title,), tuple(item.title for item in repository.load_journal_linked("group-1", "005930")))


if __name__ == "__main__":
    unittest.main()
