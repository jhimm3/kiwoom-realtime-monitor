from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from scripts.query_store_source import method_sources


class QueryStoreSourceTests(unittest.TestCase):
    def test_aggregate_resolves_direct_leaf_protocol_and_preserves_physical_owner(self) -> None:
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            directory = Path(temporary) / "src/kiwoom_monitor/central_server"
            directory.mkdir(parents=True)
            (directory / "database.py").write_text(
                "from typing import Protocol\n"
                "from .database_query_cache import QueryCacheStore\n"
                "class QueryStore(QueryCacheStore, Protocol):\n"
                " def load_bars(self, key: str) -> list[str]: ...\n"
                "class SQLiteQueryStore(QueryStore): pass\n"
                "class PostgresQueryStore(QueryStore): pass\n",
                encoding="utf-8",
            )
            (directory / "database_query_cache.py").write_text(
                "from typing import Protocol\n"
                "class QueryCacheStore(Protocol):\n"
                " def load_query(self, key: str) -> str | None: ...\n",
                encoding="utf-8",
            )

            methods = method_sources(Path(temporary))["QueryStore"]
            self.assertEqual({"load_bars", "load_query"}, set(methods))
            self.assertEqual("QueryCacheStore", methods["load_query"].class_name)
            self.assertEqual("QueryStore", methods["load_query"].logical_owner)
            self.assertEqual("src/kiwoom_monitor/central_server/database_query_cache.py",
                             methods["load_query"].file)

            (directory / "database.py").write_text(
                (directory / "database.py").read_text(encoding="utf-8").replace(
                    "class QueryStore(QueryCacheStore, Protocol):\n"
                    " def load_bars(self, key: str) -> list[str]: ...",
                    "class QueryStore(QueryCacheStore, Protocol):\n"
                    " def load_query(self, key: str) -> str | None: ...\n"
                    " def load_bars(self, key: str) -> list[str]: ...",
                ), encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate QueryStore protocol method"):
                method_sources(Path(temporary))

    def test_query_store_protocol_rejects_unresolved_and_nested_leaves(self) -> None:
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            directory = Path(temporary) / "src/kiwoom_monitor/central_server"
            directory.mkdir(parents=True)
            database = directory / "database.py"
            database.write_text(
                "from typing import Protocol\n"
                "class QueryStore(QueryCacheStore, Protocol): pass\n"
                "class SQLiteQueryStore(QueryStore): pass\n"
                "class PostgresQueryStore(QueryStore): pass\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "unresolved or ambiguous"):
                method_sources(Path(temporary))

            (directory / "database_query_cache.py").write_text(
                "from typing import Protocol\n"
                "class QueryCacheStore(Protocol): pass\n",
                encoding="utf-8",
            )
            database.write_text(
                "from typing import Protocol\n"
                "from .database_query_cache import QueryCacheStore\n"
                "class QueryStore(QueryCacheStore, Protocol): pass\n"
                "class SQLiteQueryStore(QueryStore): pass\n"
                "class PostgresQueryStore(QueryStore): pass\n",
                encoding="utf-8",
            )
            (directory / "database_query_cache.py").write_text(
                "from typing import Protocol\n"
                "class Parent(Protocol):\n"
                " def load_query(self, key: str): ...\n"
                "class QueryCacheStore(Parent): pass\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "nested Protocol inheritance"):
                method_sources(Path(temporary))

    def test_repository_aggregate_retains_all_inherited_protocol_methods(self) -> None:
        root = Path(__file__).resolve().parents[2]
        sources = method_sources(root)
        self.assertEqual(101, len(sources["QueryStore"]))
        self.assertEqual(105, sum(name != "__init__" for name in sources["SQLiteQueryStore"]))
        self.assertEqual(107, sum(name != "__init__" for name in sources["PostgresQueryStore"]))
        self.assertEqual("QueryCacheStore",
                         sources["QueryStore"]["load_query"].class_name)

    def test_local_mixins_are_counted_without_protocol_stub_or_runtime_import(self) -> None:
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            directory = Path(temporary) / "src/kiwoom_monitor/central_server"
            directory.mkdir(parents=True)
            database = directory / "database.py"
            mixins = directory / "database_external_market.py"
            database.write_text(
                "from .database_external_market import SQLiteBars, PostgresBars\n"
                "class QueryStore:\n"
                " def load_bars(self, limit: int = 1): ...\n"
                "class SQLiteQueryStore(SQLiteBars, QueryStore): pass\n"
                "class PostgresQueryStore(PostgresBars, QueryStore): pass\n",
                encoding="utf-8",
            )
            mixins.write_text(
                "class SQLiteBars:\n"
                " def load_bars(self, limit: int = 1): return []\n"
                "class PostgresBars:\n"
                " def load_bars(self, limit: int = 1): return []\n",
                encoding="utf-8",
            )

            sources = method_sources(Path(temporary))
            self.assertEqual({"load_bars"}, set(sources["SQLiteQueryStore"]))
            self.assertEqual({"load_bars"}, set(sources["PostgresQueryStore"]))
            self.assertTrue(sources["PostgresQueryStore"]["load_bars"].file.endswith(
                "database_external_market.py"
            ))

            database.write_text(database.read_text(encoding="utf-8").replace(
                "class PostgresQueryStore(PostgresBars, QueryStore): pass",
                "class PostgresQueryStore(PostgresBars, QueryStore):\n"
                " def load_bars(self, limit: int = 1): return []",
            ), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "override"):
                method_sources(Path(temporary))

            database.write_text(database.read_text(encoding="utf-8").replace(
                "class PostgresQueryStore(PostgresBars, QueryStore):\n"
                " def load_bars(self, limit: int = 1): return []",
                "class PostgresQueryStore(MissingBars, QueryStore): pass",
            ), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unresolved"):
                method_sources(Path(temporary))

    def test_moved_postgres_writer_remains_in_all_source_inventories(self) -> None:
        from scripts.audit_postgres_access import inventory as postgres_inventory
        from scripts.audit_kiwoom_storage_paths import inventory as storage_inventory

        root = Path(__file__).resolve().parents[2]
        source = method_sources(root)["PostgresQueryStore"]["save_external_bars"]
        self.assertEqual("PostgresExternalMarketStoreMixin", source.class_name)
        self.assertEqual("src/kiwoom_monitor/central_server/database_external_market.py",
                         source.file)

        postgres = postgres_inventory(root)
        writer = next(row for row in postgres["postgres_store_methods"]
                      if row["function"] == "PostgresQueryStore.save_external_bars")
        self.assertEqual(source.file, writer["file"])
        self.assertIn("_external_bar_values", writer["reachable_local_helpers"])
        self.assertIn("central_external_bars", writer["reachable_literal_tables_candidates"])

        storage = storage_inventory(root)
        writer = next(row for row in storage["postgres_methods_with_literal_write_sql"]
                      if row["method"] == "save_external_bars")
        self.assertEqual(source.file, writer["file"])
        self.assertEqual(["central_external_bars"], writer["tables_in_literal_sql"])

        market_bar_source = method_sources(root)["PostgresQueryStore"]["replace_minute_bars"]
        self.assertEqual("PostgresMarketBarStoreMixin", market_bar_source.class_name)
        self.assertEqual(
            "src/kiwoom_monitor/central_server/database_market_bars.py",
            market_bar_source.file,
        )
        market_bar_writer = next(
            row for row in postgres["postgres_store_methods"]
            if row["function"] == "PostgresQueryStore.replace_minute_bars"
        )
        self.assertEqual(market_bar_source.file, market_bar_writer["file"])
        self.assertIn("central_minute_bars", market_bar_writer["reachable_literal_tables_candidates"])
        self.assertIn("central_market_data_observation_meta",
                      market_bar_writer["reachable_literal_tables_candidates"])


if __name__ == "__main__":
    unittest.main()
