from __future__ import annotations

import tempfile
import unittest
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.infrastructure.persistence.stock_repository import RankingStockCache, StockRepository
from kiwoom_monitor.application.historical_high_service import HistoricalHighEvidence
from kiwoom_monitor.application.daily_bar_coverage import daily_query_date


class StockRepositoryTests(unittest.TestCase):
    def test_price_cache_uses_one_transaction_and_keeps_field_and_date_contracts(self) -> None:
        directory = self.enterContext(tempfile.TemporaryDirectory())
        path = Path(directory) / "monitor.sqlite3"
        Database(path).initialize()
        with closing(sqlite3.connect(path)) as con:
            con.executemany(
                "INSERT INTO stocks(code,name,float_ratio,circulating_market_cap,fundamentals_updated_at) "
                "VALUES(?,?,?,?,?)", [("A", "A", 55.5, 10, "original-confirmation"),
                                      ("B", "B", None, 77, "original-confirmation")])
            con.executemany("INSERT INTO intraday_highs(trade_date,stock_code,high_price) VALUES(?,?,?)",
                            [("2026-10-16", "A", 120), ("2026-09-01", "old", 1)])
            con.commit()
        observed = datetime(2026, 10, 16, 21, tzinfo=timezone.utc)
        timestamp = "2026-10-17T06:00:00+09:00"
        statements, connections = [], []
        real_connect = sqlite3.connect

        def traced_connect(*args, **kwargs):
            con = real_connect(*args, **kwargs)
            con.set_trace_callback(statements.append)
            connections.append(con)
            return con

        repository = StockRepository(path)
        with patch("sqlite3.connect", side_effect=traced_connect):
            repository.update_price_cache({"A": 104, "B": 90}, {"A": 110, "orphan": 250},
                                          {"A": 2000, "B": 1000}, date(2026, 10, 16), observed_at=observed)
        self.assertEqual(1, len(connections))
        self.assertEqual(1, statements.count("COMMIT"))
        self.assertEqual(1, sum(sql.startswith("BEGIN") for sql in statements))
        self.assertFalse(any(sql.startswith("SELECT") for sql in statements))
        with closing(real_connect(path)) as con:
            self.assertEqual([("A", 104, 2000., 1110., "original-confirmation", timestamp, timestamp),
                              ("B", 90, 1000., 77., "original-confirmation", timestamp, timestamp)],
                             con.execute("SELECT code,last_price,market_cap,circulating_market_cap,"
                                         "fundamentals_updated_at,last_price_updated_at,updated_at "
                                         "FROM stocks ORDER BY code").fetchall())
            self.assertEqual([("2026-10-16", "A", 120, timestamp), ("2026-10-16", "orphan", 250, timestamp)],
                             con.execute("SELECT * FROM intraday_highs ORDER BY stock_code").fetchall())
        self.assertEqual({"A": 104, "B": 90}, repository.load_last_prices(("A", "B")))
        self.assertEqual({"A": 120, "orphan": 250}, repository.load_intraday_highs(("A", "orphan"), date(2026, 10, 16)))

    def test_empty_or_filtered_price_cache_skips_database_and_partial_batches_preserve_other_values(self) -> None:
        directory = self.enterContext(tempfile.TemporaryDirectory())
        path = Path(directory) / "monitor.sqlite3"
        Database(path).initialize()
        repository = StockRepository(path)
        now = datetime(2026, 10, 16, 10)
        repository.upsert("A", "A")
        with patch("sqlite3.connect") as connect:
            repository.update_price_cache({"": 1, "A": 0}, {"A": -1}, {"A": 0}, now.date(), observed_at=now)
            connect.assert_not_called()
        repository.update_price_cache({"A": 100}, {}, {}, now.date(), observed_at=now)
        repository.update_price_cache({}, {"A": 110}, {}, now.date(), observed_at=now)
        repository.update_price_cache({}, {}, {"A": 1000}, now.date(), observed_at=now)
        with closing(sqlite3.connect(path)) as con:
            self.assertEqual((100, 1000, None), con.execute(
                "SELECT last_price,market_cap,fundamentals_updated_at FROM stocks WHERE code='A'").fetchone())
        self.assertEqual({"A": 110}, repository.load_intraday_highs(("A",), now.date()))

    def test_price_cache_first_middle_last_and_cleanup_errors_rollback_every_field_then_retry(self) -> None:
        directory = self.enterContext(tempfile.TemporaryDirectory())
        path = Path(directory) / "monitor.sqlite3"
        Database(path).initialize()
        with closing(sqlite3.connect(path)) as con:
            con.execute("INSERT INTO stocks(code,name,last_price,market_cap,float_ratio) VALUES('A','A',90,900,50)")
            con.execute("INSERT INTO intraday_highs(trade_date,stock_code,high_price) VALUES('2026-09-01','old',1)")
            con.commit()

        def snapshot():
            with closing(sqlite3.connect(path)) as con:
                return (con.execute("SELECT * FROM stocks").fetchall(),
                        con.execute("SELECT * FROM intraday_highs ORDER BY stock_code").fetchall())

        original = snapshot()
        now = datetime(2026, 10, 16, 10)
        repository = StockRepository(path)
        for operation in ("UPDATE OF last_price ON stocks", "UPDATE OF market_cap ON stocks",
                          "INSERT ON intraday_highs", "DELETE ON intraday_highs"):
            with self.subTest(operation=operation):
                with closing(sqlite3.connect(path)) as con:
                    con.execute(f"CREATE TRIGGER fail_cache BEFORE {operation} BEGIN SELECT RAISE(ABORT,'cache fault'); END")
                    con.commit()
                with self.assertRaisesRegex(sqlite3.IntegrityError, "cache fault"):
                    repository.update_price_cache({"A": 100}, {"A": 110}, {"A": 1000}, now.date(), observed_at=now)
                self.assertEqual(original, snapshot())
                with closing(sqlite3.connect(path)) as con:
                    con.execute("DROP TRIGGER fail_cache")
                    con.commit()
        for _ in range(2):
            repository.update_price_cache({"A": 100}, {"A": 110}, {"A": 1000}, now.date(), observed_at=now)
        with closing(sqlite3.connect(path)) as con:
            self.assertEqual((100, 1000, 500), con.execute(
                "SELECT last_price,market_cap,circulating_market_cap FROM stocks").fetchone())
            self.assertEqual([("A", 110)], con.execute("SELECT stock_code,high_price FROM intraday_highs").fetchall())

    def test_process_exit_before_or_after_price_cache_commit_leaves_whole_batch_and_restart_can_retry(self) -> None:
        script = r'''
import sqlite3, sys
from datetime import datetime
from pathlib import Path
from kiwoom_monitor.infrastructure.persistence.stock_repository import StockRepository
database, ready, phase = sys.argv[1:]
real_connect = sqlite3.connect
class PausingConnection(sqlite3.Connection):
    def __exit__(self, kind, value, traceback):
        if kind is None and phase == 'before':
            Path(ready).write_text(phase)
            sys.stdin.read(1)
        result = super().__exit__(kind, value, traceback)
        if kind is None and phase == 'after':
            Path(ready).write_text(phase)
            sys.stdin.read(1)
        return result
sqlite3.connect = lambda *a, **k: real_connect(*a, factory=PausingConnection, **k)
now = datetime(2026, 10, 16, 10)
StockRepository(Path(database)).update_price_cache({'A':100},{'A':110},{'A':1000},now.date(),observed_at=now)
'''
        for phase in ("before", "after"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "monitor.sqlite3"
                Database(path).initialize()
                with closing(sqlite3.connect(path)) as con:
                    con.execute("INSERT INTO stocks(code,name,last_price,market_cap,float_ratio) VALUES('A','A',90,900,50)")
                    con.commit()
                ready = path.with_suffix(".ready")
                process = subprocess.Popen([sys.executable, "-c", script, str(path), str(ready), phase],
                                           stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    deadline = time.monotonic() + 15
                    while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertTrue(ready.exists(), "child did not reach transaction boundary")
                    process.kill()
                    process.wait(timeout=5)
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.communicate(timeout=5)
                with closing(sqlite3.connect(path)) as con:
                    self.assertEqual((90, 900) if phase == "before" else (100, 1000), con.execute(
                        "SELECT last_price,market_cap FROM stocks").fetchone())
                    self.assertEqual(0 if phase == "before" else 1, con.execute("SELECT COUNT(*) FROM intraday_highs").fetchone()[0])
                now = datetime(2026, 10, 16, 10)
                StockRepository(path).update_price_cache({"A": 100}, {"A": 110}, {"A": 1000}, now.date(), observed_at=now)
                with closing(sqlite3.connect(path)) as con:
                    self.assertEqual((100, 1000, 500), con.execute(
                        "SELECT last_price,market_cap,circulating_market_cap FROM stocks").fetchone())
                    self.assertEqual([("A", 110)], con.execute("SELECT stock_code,high_price FROM intraday_highs").fetchall())

    def test_ranking_cache_preserves_individual_readers_with_fewer_database_reads(self) -> None:
        directory = self.enterContext(tempfile.TemporaryDirectory())
        path = Path(directory) / "monitor.sqlite3"
        Database(path).initialize()
        repository = StockRepository(path)
        day = date(2026, 10, 16)
        codes = ("valid", "zero", "missing", "negative", "stale", "unknown", "orphan")
        market_codes = (*codes, "member")
        with closing(sqlite3.connect(path)) as con:
            con.executemany(
                "INSERT INTO stocks(code,name,market,last_price,market_cap,float_ratio,"
                "high_250_price,float_shares,upper_limit_price,nxt_enabled,nxt_checked_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                [("valid", "A", " kospi ", 100, 2000, 55.5, 120, 500, 130, 1, day.isoformat()),
                 ("zero", "B", "", 0, 0, 0, 0, 0, 0, 0, day.isoformat()),
                 ("missing", "C", "KOSDAQ", None, None, None, None, None, None, None, None),
                 ("negative", "D", "", -1, 100, -1, None, None, None, None, None),
                 ("stale", "E", "", 200, 100, 0, None, None, None, 1, "2026-10-15"),
                 ("unknown", "F", "", None, 100, None, None, None, None, None, day.isoformat()),
                 ("member", "G", " kosdaq ", 300, 1000, 50, 400, 100, 500, 1, day.isoformat())],
            )
            con.executemany(
                "INSERT INTO intraday_highs(trade_date,stock_code,high_price) VALUES(?,?,?)",
                [(day.isoformat(), "valid", 110), (day.isoformat(), "orphan", 250),
                 ("2026-10-15", "missing", 999), (day.isoformat(), "zero", 0)],
            )
            con.commit()

        real_connect = sqlite3.connect
        statements: list[str] = []
        connections: list[sqlite3.Connection] = []

        def tracked_connect(*args, **kwargs):
            con = real_connect(*args, **kwargs)
            con.set_trace_callback(statements.append)
            connections.append(con)
            return con

        with patch("kiwoom_monitor.infrastructure.persistence.stock_repository.sqlite3.connect",
                   side_effect=tracked_connect):
            expected = RankingStockCache(
                repository.load_markets(market_codes), repository.load_last_prices(codes),
                repository.load_intraday_highs(codes, day), repository.load_fundamentals(codes),
                repository.load_nxt_enabled(codes, day.isoformat()),
            )
            self.assertEqual(5, len(connections))
            self.assertEqual(5, len(statements))
            statements.clear()
            connections.clear()
            actual = repository.load_ranking_cache(codes, market_codes, day)
            self.assertEqual(expected, actual)
            self.assertEqual(1, len(connections))
            self.assertEqual(2, len(statements))
            self.assertTrue(all(sql.startswith("SELECT ") for sql in statements))
        self.assertEqual({"valid": True, "zero": False}, actual.nxt_enabled)
        self.assertEqual({"valid": 110, "orphan": 250}, actual.intraday_highs)
        self.assertNotIn("member", actual.last_prices)
        self.assertEqual("KOSDAQ", actual.markets["member"])

        # Every invocation observes new stored data; there is no new freshness cache.
        with closing(real_connect(path)) as con:
            con.execute("UPDATE stocks SET last_price=105,nxt_enabled=0 WHERE code='valid'")
            con.commit()
        updated = repository.load_ranking_cache(codes, market_codes, day)
        self.assertEqual(105, updated.last_prices["valid"])
        self.assertFalse(updated.nxt_enabled["valid"])
        self.assertEqual({}, repository.load_ranking_cache(codes, market_codes, date(2026, 10, 17)).nxt_enabled)

    def test_empty_ranking_cache_skips_database_and_market_only_keeps_scope(self) -> None:
        directory = self.enterContext(tempfile.TemporaryDirectory())
        path = Path(directory) / "monitor.sqlite3"
        Database(path).initialize()
        repository = StockRepository(path)
        repository.upsert("005930", "삼성전자", "KOSPI")
        with patch("kiwoom_monitor.infrastructure.persistence.stock_repository.sqlite3.connect") as connect:
            self.assertEqual(RankingStockCache({}, {}, {}, {}, {}),
                             repository.load_ranking_cache((), (), date(2026, 10, 16)))
            connect.assert_not_called()
        self.assertEqual(RankingStockCache({"005930": "KOSPI"}, {}, {}, {}, {}),
                         repository.load_ranking_cache((), ("005930",), date(2026, 10, 16)))

    def test_last_realtime_market_cap_preserves_fundamentals_refresh_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "monitor.sqlite3"
            Database(database_path).initialize()
            repository = StockRepository(database_path)
            repository.upsert("005930", "삼성전자", "KOSPI")
            repository.update_fundamentals("005930", 4_000_000, 55.0)
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute(
                    "UPDATE stocks SET fundamentals_updated_at=? WHERE code=?",
                    ("2026-09-20 08:00:00", "005930"),
                )
                connection.commit()

            repository.update_last_market_caps({"005930": 4_200_000.0})

            with closing(sqlite3.connect(database_path)) as connection:
                saved = connection.execute(
                    "SELECT market_cap,circulating_market_cap,fundamentals_updated_at "
                    "FROM stocks WHERE code=?",
                    ("005930",),
                ).fetchone()
            self.assertEqual((4_200_000.0, 2_310_000.0, "2026-09-20 08:00:00"), saved)

    def test_blank_market_update_preserves_catalog_market(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "monitor.sqlite3"
            Database(database_path).initialize()
            repository = StockRepository(database_path)
            repository.upsert("005930", "삼성전자", "KOSPI")

            repository.upsert("005930", "삼성전자", "")

            self.assertEqual(repository.load_markets(("005930",)), {"005930": "KOSPI"})
            self.assertFalse(repository.has_missing_markets(("005930",)))

    def test_missing_market_is_detected_for_catalog_repair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "monitor.sqlite3"
            Database(database_path).initialize()
            repository = StockRepository(database_path)
            repository.upsert("005930", "삼성전자", "")

            self.assertTrue(repository.has_missing_markets(("005930",)))

    def test_intraday_high_is_persisted_and_never_lowered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "monitor.sqlite3"
            Database(database_path).initialize()
            repository = StockRepository(database_path)
            repository.upsert("001210", "금호전기", "코스피")
            day = date(2026, 8, 28)
            repository.update_intraday_highs({"001210": 10_680}, day)
            repository.update_intraday_highs({"001210": 9_500}, day)
            self.assertEqual({"001210": 10_680}, repository.load_intraday_highs(("001210",), day))

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
            stocks.update_fundamentals(
                "005930", 2_000_000, 55.5, 72_000, 4_424_699_000, 91_900,
            )
            saved = stocks.load_fundamentals(("005930",))
            self.assertEqual(2_000_000, saved["005930"].market_cap_eok)
            self.assertEqual(55.5, saved["005930"].float_ratio_percent)
            self.assertEqual(72_000, saved["005930"].high_250_price)
            self.assertEqual(4_424_699_000, saved["005930"].float_shares)
            self.assertEqual(91_900, saved["005930"].upper_limit_price)
            self.assertEqual(72_000, stocks.load_high_250_price("005930"))

    def test_missing_upper_limit_price_forces_one_daily_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "monitor.sqlite3"
            Database(database_path).initialize()
            stocks = StockRepository(database_path)
            stocks.upsert("005930", "삼성전자")
            today = daily_query_date().isoformat()

            stocks.update_fundamentals("005930", 2_000_000, 55.5, 72_000)
            self.assertEqual(("005930",), stocks.fundamentals_to_refresh(("005930",), today))

            stocks.update_fundamentals("005930", 2_000_000, 55.5, 72_000, None, 91_900)
            self.assertEqual((), stocks.fundamentals_to_refresh(("005930",), today))

    def test_fundamentals_refresh_date_uses_kst_for_utc_sqlite_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "monitor.sqlite3"
            Database(database_path).initialize()
            stocks = StockRepository(database_path)
            stocks.upsert("005930", "삼성전자")
            stocks.update_fundamentals(
                "005930", 2_000_000, 55.5, 72_000, 4_000_000, 91_900,
            )
            with closing(sqlite3.connect(database_path)) as connection:
                # 22:30 UTC on Sep 30 is 07:30 KST on Oct 1.
                connection.execute(
                    "UPDATE stocks SET fundamentals_updated_at=? WHERE code=?",
                    ("2026-09-30 22:30:00", "005930"),
                )
                connection.commit()

            self.assertEqual((), stocks.fundamentals_to_refresh(("005930",), "2026-10-01"))
            self.assertEqual(("005930",), stocks.fundamentals_to_refresh(("005930",), "2026-09-30"))
