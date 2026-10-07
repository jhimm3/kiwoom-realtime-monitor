from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from kiwoom_monitor.application.theme_suggestions import ThemeSuggestion
from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.infrastructure.persistence.stock_repository import StockRepository
from kiwoom_monitor.infrastructure.persistence.theme_repository import ThemeRepository


class ThemeSuggestionTests(unittest.TestCase):
    def test_profile_alias_is_applied_before_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            StockRepository(path).upsert("000001", "테스트기업", "KOSDAQ")
            repository = ThemeRepository(path)
            repository.replace_for_stock("000001", ("호남개발",))
            repository.set_theme_alias("호남클러스터", "호남개발")
            repository.import_ai_theme_suggestions((_suggestion("호남클러스터"),))

            pending = repository.list_ai_theme_suggestions()

            self.assertEqual(("호남개발",), pending[0].resolved_theme_names)
            repository.review_ai_theme_suggestion(pending[0].key, approved=True)
            self.assertEqual(("호남개발",), repository.themes_for_stock("000001"))
            self.assertEqual("approved", repository.list_ai_theme_suggestions("approved")[0].status)

    def test_rejection_is_preserved_when_same_analysis_is_imported_again(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            StockRepository(path).upsert("000001", "테스트기업", "KOSDAQ")
            repository = ThemeRepository(path)
            suggestion = _suggestion("지역개발")
            repository.import_ai_theme_suggestions((suggestion,))
            repository.review_ai_theme_suggestion(
                repository.list_ai_theme_suggestions()[0].key, approved=False,
            )

            repository.import_ai_theme_suggestions((suggestion,))

            self.assertEqual((), repository.list_ai_theme_suggestions("pending"))
            self.assertEqual("rejected", repository.list_ai_theme_suggestions("rejected")[0].status)

    def test_edited_approval_becomes_a_profile_alias_for_future_suggestions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            StockRepository(path).upsert("000001", "테스트기업", "KOSDAQ")
            repository = ThemeRepository(path)
            repository.import_ai_theme_suggestions((_suggestion("호남클러스터"),))

            repository.review_ai_theme_suggestion(
                repository.list_ai_theme_suggestions()[0].key,
                approved=True,
                theme_names=("호남개발",),
            )

            self.assertEqual(("호남개발",), repository.resolve_theme_names("호남클러스터"))
            self.assertIn(
                ("alias", "호남클러스터", "호남개발", "user_review"),
                repository.theme_name_decisions(),
            )

    def test_repeated_unchanged_suggestion_uses_alias_created_by_earlier_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            stocks = StockRepository(path)
            stocks.upsert("000001", "첫 종목", "KOSDAQ")
            stocks.upsert("000002", "둘째 종목", "KOSDAQ")
            repository = ThemeRepository(path)
            first = _suggestion("호남클러스터")
            second = ThemeSuggestion(
                "000002", "https://example.com/article-2", "호남클러스터",
                "둘째 기사 근거", 75, "openai", "model", "hash-2", datetime.now(UTC),
            )
            repository.import_ai_theme_suggestions((first, second))
            pending = repository.list_ai_theme_suggestions()
            first_row = next(value for value in pending if value.stock_code == "000001")
            second_row = next(value for value in pending if value.stock_code == "000002")

            repository.review_ai_theme_suggestion(
                first_row.key, approved=True, theme_names=("호남개발",),
            )
            repository.review_ai_theme_suggestion(second_row.key, approved=True)

            self.assertEqual(("호남개발",), repository.themes_for_stock("000001"))
            self.assertEqual(("호남개발",), repository.themes_for_stock("000002"))


def _suggestion(name: str) -> ThemeSuggestion:
    return ThemeSuggestion(
        "000001", "https://example.com/article", name, "복수 기업 참여", 80,
        "openai", "model", "hash", datetime.now(UTC),
    )


if __name__ == "__main__":
    unittest.main()
