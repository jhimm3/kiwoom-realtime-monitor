from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.infrastructure.persistence.stock_repository import StockRepository
from kiwoom_monitor.application.historical_high_service import HistoricalHighEvidence


class StockRepositoryTests(unittest.TestCase):
    def test_matches_neontech_former_name_to_current_stock_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "monitor.sqlite3"
            Database(database_path).initialize()
            repository = StockRepository(database_path)

            repository.upsert("306620", "지아이에스", "코스닥")

            self.assertIsNone(repository.find_code_by_name("네온테크"))
            repository.review_name_changes({("306620", "네온테크"): True})
            self.assertEqual("306620", repository.find_code_by_name("네온테크"))

    def test_catalog_rename_requires_review_before_becoming_alias(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "monitor.sqlite3"
            Database(database_path).initialize()
            repository = StockRepository(database_path)
            repository.upsert("123456", "예전회사", "코스닥")

            repository.upsert_many((("123456", "새회사", "코스닥"),))

            self.assertIsNone(repository.find_code_by_name("예전회사"))
            self.assertEqual("123456", repository.find_code_by_name("새회사"))
            self.assertEqual((("123456", "예전회사", "새회사", "KRX"),), repository.pending_name_changes())
            repository.review_name_changes({("123456", "예전회사"): True})
            self.assertEqual("123456", repository.find_code_by_name("예전회사"))
            import sqlite3
            connection = sqlite3.connect(database_path)
            try:
                history = connection.execute(
                    "SELECT old_name,new_name,source,decision FROM stock_name_history WHERE stock_code='123456'"
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(("예전회사", "새회사", "KRX", "approved"), history)

    def test_rejected_catalog_rename_does_not_become_alias(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "monitor.sqlite3"
            Database(database_path).initialize()
            repository = StockRepository(database_path)
            repository.upsert("123456", "예전회사", "코스닥")
            repository.upsert("123456", "새회사", "코스닥")
            repository.review_name_changes({("123456", "예전회사"): False})

            self.assertIsNone(repository.find_code_by_name("예전회사"))
            self.assertEqual((), repository.pending_name_changes())

    def test_known_former_names_are_seeded_when_current_codes_exist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "monitor.sqlite3"
            Database(database_path).initialize()
            repository = StockRepository(database_path)
            repository.upsert_many((("030200", "케이티", "코스피"), ("150900", "파수AI", "코스닥")))

            self.assertIsNone(repository.find_code_by_name("KT"))
            self.assertIsNone(repository.find_code_by_name("파수"))
            repository.review_name_changes({("030200", "KT"): True, ("150900", "파수"): True})
            self.assertEqual("030200", repository.find_code_by_name("KT"))
            self.assertEqual("150900", repository.find_code_by_name("파수"))

    def test_saves_historical_high_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "monitor.sqlite3"
            Database(database_path).initialize()
            stocks = StockRepository(database_path)
            stocks.upsert("003350", "한국화장품제조")
            stocks.update_historical_high_price(
                "003350", 17_000, 1978, 2026, "2026-08-25", occurred_on="20260501",
                evidence=(HistoricalHighEvidence("month", "20260501", 17_000),),
            )
            import sqlite3
            connection = sqlite3.connect(database_path)
            try:
                saved = connection.execute("SELECT historical_high_price,historical_high_occurred_on FROM stocks WHERE code='003350'").fetchone()
                evidence = connection.execute("SELECT period,trade_date,high_price FROM historical_high_evidence WHERE stock_code='003350'").fetchone()
            finally:
                connection.close()
            self.assertEqual((17_000, "20260501"), saved)
            self.assertEqual(("month", "20260501", 17_000), evidence)
            cache = stocks.load_historical_high_cache("003350")
            self.assertIsNotNone(cache)
            assert cache is not None
            self.assertEqual(17_000, cache.target.price)
            self.assertEqual("2026-08-25", cache.checked_on)

    def test_resolves_saved_excel_alias(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "monitor.sqlite3"
            Database(database_path).initialize()
            stocks = StockRepository(database_path)
            stocks.upsert("005930", "삼성전자")
            stocks.save_alias("삼성 전자", "005930")
            stocks.update_fundamentals("005930", 4_000_000, 75.0)

            self.assertEqual("005930", stocks.find_code_by_name("삼성 전자"))

    def test_loads_saved_fundamentals_without_api_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "monitor.sqlite3"
            Database(database_path).initialize()
            stocks = StockRepository(database_path)
            stocks.upsert("005930", "삼성전자")
            stocks.update_fundamentals("005930", 2_000_000, 55.5, 72_000, 4_424_699_000)
            saved = stocks.load_fundamentals(("005930",))
            self.assertEqual(2_000_000, saved["005930"].market_cap_eok)
            self.assertEqual(55.5, saved["005930"].float_ratio_percent)
            self.assertEqual(72_000, saved["005930"].high_250_price)
            self.assertEqual(4_424_699_000, saved["005930"].float_shares)
            self.assertEqual(72_000, stocks.load_high_250_price("005930"))
