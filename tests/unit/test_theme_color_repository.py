from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.infrastructure.persistence.stock_repository import StockRepository
from kiwoom_monitor.infrastructure.persistence.theme_repository import ThemeRepository


class ThemeColorRepositoryTests(unittest.TestCase):
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

