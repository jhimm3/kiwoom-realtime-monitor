from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.infrastructure.persistence.stock_repository import StockRepository
from kiwoom_monitor.infrastructure.persistence.theme_repository import ThemeRepository


class ThemeColorRepositoryTests(unittest.TestCase):
    def test_reviews_pending_former_name_and_then_matches_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            stocks = StockRepository(path)
            stocks.upsert("306620", "지아이에스", "코스닥")
            repository = ThemeRepository(path)

            self.assertEqual(
                ("306620", "네온테크", "지아이에스", "기본 과거명 자료"),
                repository.pending_name_change("네온테크"),
            )
            self.assertIsNone(repository.find_code_by_name("네온테크"))
            repository.review_name_changes({("306620", "네온테크"): True})

            self.assertEqual("306620", repository.find_code_by_name("네온테크"))
            self.assertIsNone(repository.pending_name_change("네온테크"))

    def test_keeps_stock_color_override_separate_from_theme_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            StockRepository(path).upsert("005930", "삼성전자")
            repository = ThemeRepository(path)
            repository.replace_for_stock("005930", ("반도체",))
            repository.set_color("반도체", "#CFE2F3")
            repository.set_stock_theme_color("005930", "반도체", "#F4CCCC")

            self.assertEqual("#CFE2F3", repository.color_for_theme("반도체"))
            self.assertEqual("#F4CCCC", repository.color_for_stock_theme("005930", "반도체"))

    def test_keeps_themes_separate_by_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            StockRepository(path).upsert("005930", "삼성전자")
            repository = ThemeRepository(path)
            repository.replace_for_stock("005930", ("집 테마",))
            repository.create_profile("회사", copy_current=False)
            repository.select_profile("회사")
            repository.replace_for_stock("005930", ("회사 테마",))

            self.assertEqual(("회사 테마",), repository.themes_for_stock("005930"))
            repository.select_profile("기본 테마")
            self.assertEqual(("집 테마",), repository.themes_for_stock("005930"))

    def test_renames_active_profile_without_changing_its_themes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            StockRepository(path).upsert("005930", "삼성전자")
            repository = ThemeRepository(path)
            repository.replace_for_stock("005930", ("반도체",))

            repository.rename_profile("기본 테마", "집")

            self.assertEqual("집", repository.active_profile)
            self.assertEqual(("집",), repository.list_profiles())
            self.assertEqual(("반도체",), repository.themes_for_stock("005930"))

    def test_renames_theme_without_breaking_stock_foreign_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            StockRepository(path).upsert("005930", "삼성전자")
            repository = ThemeRepository(path)
            repository.replace_for_stock("005930", ("동신장비",))

            repository.rename_theme("동신장비", "통신장비")

            self.assertEqual(("통신장비",), repository.themes_for_stock("005930"))
            self.assertIn(("통신장비", repository.color_for_theme("통신장비")), repository.list_themes())

    def test_rename_remembers_alias_for_future_profile_imports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            stocks = StockRepository(path)
            stocks.upsert("005930", "삼성전자")
            stocks.upsert("000660", "SK하이닉스")
            repository = ThemeRepository(path)
            repository.replace_for_stock("005930", ("호남개발",))

            repository.rename_theme("호남개발", "호남클러스터")
            repository.replace_for_stock("000660", ("호남개발",))

            self.assertEqual(("호남클러스터",), repository.themes_for_stock("000660"))
            self.assertIn(
                ("alias", "호남개발", "호남클러스터", "user"),
                repository.theme_name_decisions(),
            )

    def test_does_not_recreate_deleted_default_profile_on_next_start(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            repository = ThemeRepository(path)
            repository.create_profile("회사")
            repository.delete_profile("기본 테마")

            Database(path).initialize()

            self.assertEqual(("회사",), repository.list_profiles())

    def test_splits_one_theme_into_multiple_themes_for_every_stock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            stocks = StockRepository(path)
            stocks.upsert("005930", "삼성전자")
            stocks.upsert("000660", "SK하이닉스")
            repository = ThemeRepository(path)
            repository.replace_for_stock("005930", ("해운이란", "이란"))
            repository.replace_for_stock("000660", ("해운이란",))
            repository.set_color("해운이란", "#123456")

            repository.split_theme("해운이란", ("해운", "이란", "해운"))

            self.assertEqual(("이란", "해운"), repository.themes_for_stock("005930"))
            self.assertEqual(("이란", "해운"), repository.themes_for_stock("000660"))
            self.assertNotIn("해운이란", dict(repository.list_themes()))
            self.assertEqual("#123456", repository.color_for_theme("해운"))

    def test_split_expands_future_imports_and_blocks_remerge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            stocks = StockRepository(path)
            stocks.upsert("005930", "삼성전자")
            stocks.upsert("000660", "SK하이닉스")
            repository = ThemeRepository(path)
            repository.replace_for_stock("005930", ("호남개발호남클러스터",))

            repository.split_theme(
                "호남개발호남클러스터", ("호남개발", "호남클러스터"),
            )
            repository.replace_for_stock("000660", ("호남개발호남클러스터",))

            self.assertEqual(
                ("호남개발", "호남클러스터"), repository.themes_for_stock("000660"),
            )
            with self.assertRaisesRegex(ValueError, "분리 유지"):
                repository.set_theme_alias("호남개발", "호남클러스터")

    def test_theme_name_decisions_are_profile_scoped_and_copied_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            repository = ThemeRepository(path)
            repository.set_theme_alias("호남개발", "호남클러스터", decision_source="llm_review")
            repository.create_profile("복사본", copy_current=True)
            repository.create_profile("빈 프로필", copy_current=False)

            repository.select_profile("복사본")
            self.assertEqual(("호남클러스터",), repository.resolve_theme_names("호남개발"))
            repository.select_profile("빈 프로필")
            self.assertEqual(("호남개발",), repository.resolve_theme_names("호남개발"))

    def test_rejects_alias_cycles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            repository = ThemeRepository(path)
            repository.set_theme_alias("호남개발", "호남클러스터")

            with self.assertRaisesRegex(ValueError, "순환"):
                repository.set_theme_alias("호남클러스터", "호남개발")
