from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.infrastructure.persistence.stock_repository import StockRepository


class StockRepositoryTests(unittest.TestCase):
    def test_resolves_saved_excel_alias(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "monitor.sqlite3"
            Database(database_path).initialize()
            stocks = StockRepository(database_path)
            stocks.upsert("005930", "삼성전자")
            stocks.save_alias("삼성 전자", "005930")
            stocks.update_fundamentals("005930", 4_000_000, 75.0)

            self.assertEqual("005930", stocks.find_code_by_name("삼성 전자"))
