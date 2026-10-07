from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts.import_prepared_historical_news_to_nas import _prepare_articles
from scripts.run_prepared_historical_news_imports import _import_progress


class _Connection:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class _Store:
    def __init__(self, *, reject: str = "") -> None:
        self.calls: list[list[str]] = []
        self.reject = reject

    def upsert_documents(self, collection: str, values: list[dict]) -> None:
        assert collection == "news_article"
        keys = [value["key"] for value in values]
        self.calls.append(keys)
        if self.reject and self.reject in keys:
            raise ValueError("bad prepared article")


def _record(identity: str, scope: str = "historical_backfill") -> dict:
    return {
        "scope": scope,
        "article": {"stock_code": "005930", "identity": identity,
                    "document": {"stock_code": "005930", "identity": identity}},
    }


class PreparedNewsImportBatchTests(unittest.TestCase):
    def test_runner_accepts_only_importer_progress(self) -> None:
        self.assertEqual({"imported": 100, "skipped": 3, "failed": 0, "deferred": 0},
                         _import_progress('{"imported":100,"skipped":3,"failed":0,"deferred":0}'))
        self.assertIsNone(_import_progress("2026-09-25 stage=search begin"))
        self.assertIsNone(_import_progress('{"imported":-1,"skipped":0,"failed":0,"deferred":0}'))

    def test_search_articles_use_bounded_writer_batches_and_preserve_order(self) -> None:
        records = [_record(str(index)) for index in range(26)]
        connection = _Connection()
        store = _Store()
        with patch("scripts.import_prepared_historical_news_to_nas._article_revision",
                   side_effect=lambda _connection, article, _scope: article["identity"]):
            prepared, failed = _prepare_articles(store, connection, records)
        self.assertEqual([], failed)
        self.assertEqual(records, prepared)
        self.assertEqual([list(map(str, range(25))), ["25"]], store.calls)
        self.assertEqual(2, connection.commits)
        self.assertEqual(0, connection.rollbacks)
        self.assertEqual([str(index) for index in range(26)],
                         [row["article_id"] for row in prepared])

    def test_invalid_search_batch_falls_back_to_isolated_articles(self) -> None:
        records = [_record("good-1"), _record("bad"), _record("good-2")]
        connection = _Connection()
        store = _Store(reject="bad")

        def single(_store, _connection, _scope, article):
            if article["identity"] == "bad":
                raise ValueError("bad prepared article")
            _connection.commit()
            return article["identity"]

        with patch("scripts.import_prepared_historical_news_to_nas._prepare_article",
                   side_effect=single):
            prepared, failed = _prepare_articles(store, connection, records)
        self.assertEqual([records[0], records[2]], prepared)
        self.assertEqual("bad", failed[0][0]["article"]["identity"])
        self.assertEqual(1, connection.rollbacks)
        self.assertEqual(2, connection.commits)

    def test_operational_failure_does_not_fan_out_to_many_retries(self) -> None:
        records = [_record(str(index)) for index in range(25)]
        connection = _Connection()
        store = _Store()
        with patch.object(store, "upsert_documents", side_effect=TimeoutError("database unavailable")):
            with self.assertRaises(TimeoutError):
                _prepare_articles(store, connection, records)
        self.assertEqual(0, connection.commits)

    def test_market_items_remain_on_original_path_in_snapshot_order(self) -> None:
        records = [_record("search-1"), _record("market", "historical_market_backfill"),
                   _record("search-2")]
        connection = _Connection()
        store = _Store()
        with patch("scripts.import_prepared_historical_news_to_nas._article_revision",
                   side_effect=lambda _connection, article, _scope: article["identity"]), \
             patch("scripts.import_prepared_historical_news_to_nas._prepare_article",
                   side_effect=lambda _store, _connection, _scope, article: article["identity"]):
            prepared, failed = _prepare_articles(store, connection, records)
        self.assertEqual([], failed)
        self.assertEqual(records, prepared)
        self.assertEqual([["search-1", "search-2"]], store.calls)


if __name__ == "__main__":
    unittest.main()
