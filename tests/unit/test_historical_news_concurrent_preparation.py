from __future__ import annotations

import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import Future
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from scripts.preprocess_historical_news_locally import (
    ConcurrentArticlePreparation, _open_results, save_prepared_batch,
)


class ConcurrentArticlePreparationTests(unittest.TestCase):
    def test_submit_reuses_open_results_connection_for_ready_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "prepared.sqlite3"
            with ConcurrentArticlePreparation(
                output=output, search_database=output, market_database=output,
                matcher=(), workers=1, allow_network=False,
            ) as preparation:
                with patch("scripts.preprocess_historical_news_locally._prepare",
                           return_value=({"identity": "article-0"}, {}, [])):
                    with patch("scripts.preprocess_historical_news_locally.sqlite3.connect",
                               side_effect=AssertionError("opened another results connection")):
                        preparation.submit("historical_market_backfill", "GLOBAL", "article-0")
                    preparation.drain(all_pending=True)
                    with patch("scripts.preprocess_historical_news_locally.sqlite3.connect",
                               side_effect=AssertionError("opened another results connection")):
                        preparation.submit("historical_market_backfill", "GLOBAL", "article-0")
                self.assertEqual(1, preparation.ready)
                self.assertEqual(1, preparation.skipped)

    def test_capacity_drain_waits_for_worker_sized_batch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "prepared.sqlite3"
            with ConcurrentArticlePreparation(
                output=output, search_database=output, market_database=output,
                matcher=(), workers=4, allow_network=False,
            ) as preparation:
                futures = [Future() for _ in range(8)]
                for index, future in enumerate(futures):
                    key = ("historical_market_backfill", "GLOBAL", f"article-{index}")
                    preparation.pending[future] = key
                    preparation.queued.add(key)
                for index, future in enumerate(futures[:3]):
                    future.set_result(({"index": index}, {}, []))
                timer = threading.Timer(0.02, lambda: futures[3].set_result(({"index": 3}, {}, [])))
                timer.start()
                try:
                    preparation.drain(wait_for_one=True)
                finally:
                    timer.join()
                self.assertEqual(4, preparation.ready)
                self.assertEqual(4, len(preparation.pending))
                with closing(sqlite3.connect(output)) as reader:
                    self.assertEqual(4, reader.execute("SELECT COUNT(*) FROM prepared_news").fetchone()[0])
                for index, future in enumerate(futures[4:], 4):
                    future.set_result(({"index": index}, {}, []))
            self.assertEqual(8, preparation.ready)
            self.assertFalse(preparation.pending)

    def test_failed_drain_retains_results_for_retry_after_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "prepared.sqlite3"
            with ConcurrentArticlePreparation(
                output=output, search_database=output, market_database=output,
                matcher=(), workers=1, allow_network=False,
            ) as preparation:
                future = Future()
                future.set_result(({"identity": "article-0"}, {}, []))
                key = ("historical_market_backfill", "GLOBAL", "article-0")
                preparation.pending[future] = key
                preparation.queued.add(key)

                def fail_after_insert(connection, _rows):
                    connection.execute(
                        "INSERT INTO prepared_news VALUES(?,?,?,?,?,?,?,?,?)",
                        (*key, "ready", "{}", "{}", "[]", "", 1),
                    )
                    raise RuntimeError("injected write failure")

                with patch("scripts.preprocess_historical_news_locally._write_prepared_rows",
                           side_effect=fail_after_insert):
                    with self.assertRaisesRegex(RuntimeError, "injected write failure"):
                        preparation.drain()
                self.assertIn(future, preparation.pending)
                self.assertIn(key, preparation.queued)
                self.assertEqual(0, preparation.ready)
                with closing(sqlite3.connect(output)) as reader:
                    self.assertEqual(0, reader.execute("SELECT COUNT(*) FROM prepared_news").fetchone()[0])
                preparation.drain()
            with closing(sqlite3.connect(output)) as reader:
                self.assertEqual(1, reader.execute("SELECT COUNT(*) FROM prepared_news").fetchone()[0])

    def test_persistent_output_connection_commits_each_drain_and_closes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "prepared.sqlite3"
            with ConcurrentArticlePreparation(
                output=output, search_database=output, market_database=output,
                matcher=(), workers=1, allow_network=False,
            ) as preparation:
                writer = preparation._output_connection
                self.assertIsNotNone(writer)
                for index in range(2):
                    future = Future()
                    future.set_result(({"index": index}, {"body_status": "fulltext"}, []))
                    preparation.pending[future] = ("historical_market_backfill", "GLOBAL", f"article-{index}")
                    preparation.drain()
                    self.assertIs(writer, preparation._output_connection)
                    with closing(sqlite3.connect(output)) as reader:
                        self.assertEqual(index + 1, reader.execute(
                            "SELECT COUNT(*) FROM prepared_news"
                        ).fetchone()[0])
            self.assertIsNone(preparation._output_connection)
            with patch("scripts.preprocess_historical_news_locally._prepare") as prepare:
                with ConcurrentArticlePreparation(
                    output=output, search_database=output, market_database=output,
                    matcher=(), workers=1, allow_network=False,
                ) as restarted:
                    restarted.submit("historical_market_backfill", "GLOBAL", "article-0")
                    self.assertEqual(1, restarted.skipped)
                    prepare.assert_not_called()

    def test_persistent_save_rolls_back_failed_batch_and_allows_next_save(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "prepared.sqlite3"
            with closing(_open_results(output)) as writer:
                def fail_after_insert(connection, _rows):
                    connection.execute(
                        "INSERT INTO prepared_news VALUES(?,?,?,?,?,?,?,?,?)",
                        ("historical_market_backfill", "GLOBAL", "failed", "ready", "{}", "{}", "[]", "", 1),
                    )
                    raise RuntimeError("injected write failure")

                with patch("scripts.preprocess_historical_news_locally._write_prepared_rows",
                           side_effect=fail_after_insert):
                    with self.assertRaisesRegex(RuntimeError, "injected write failure"):
                        save_prepared_batch(output, [("historical_market_backfill", "GLOBAL", "failed",
                                                      {}, {}, [], "")], connection=writer)
                self.assertEqual(0, writer.execute("SELECT COUNT(*) FROM prepared_news").fetchone()[0])
                save_prepared_batch(output, [("historical_market_backfill", "GLOBAL", "ready",
                                              {"identity": "ready"}, {}, [], "")], connection=writer)
                with closing(sqlite3.connect(output)) as reader:
                    self.assertEqual([("ready", "ready")], reader.execute(
                        "SELECT identity,state FROM prepared_news"
                    ).fetchall())

    def test_empty_ready_drain_skips_database_save_and_keeps_pending(self) -> None:
        events = []
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "prepared.sqlite3"
            future = Future()
            with ConcurrentArticlePreparation(
                output=output, search_database=output, market_database=output,
                matcher=(), workers=1, allow_network=False,
                timing_observer=lambda event, fields: events.append((event, fields)),
            ) as preparation:
                preparation.pending[future] = ("historical_market_backfill", "GLOBAL", "article-1")
                with patch("scripts.preprocess_historical_news_locally.save_prepared_batch") as save:
                    preparation.drain()
                    save.assert_not_called()
                    self.assertIn(future, preparation.pending)
                    future.set_result(({"identity": "article-1"}, {"body_status": "fulltext"}, []))
                    preparation.drain()
                    save.assert_called_once()
            self.assertEqual([0, 1], [fields["rows"] for event, fields in events
                                      if event == "market_prepare_drain"])

    def test_capacity_wait_and_sqlite_batch_time_are_reported_without_losing_rows(self) -> None:
        events = []

        def prepare(_scope, _code, identity, *_args, **_kwargs):
            time.sleep(0.01)
            return ({"identity": identity}, {"body_status": "fulltext"}, [])

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "prepared.sqlite3"
            with patch("scripts.preprocess_historical_news_locally._prepare", side_effect=prepare):
                with ConcurrentArticlePreparation(
                    output=output, search_database=output, market_database=output,
                    matcher=(), workers=1, allow_network=False,
                    timing_observer=lambda event, fields: events.append((event, fields)),
                ) as preparation:
                    for index in range(6):
                        preparation.submit("historical_market_backfill", "GLOBAL", f"article-{index}")
                self.assertEqual(6, preparation.ready)
            with closing(sqlite3.connect(output)) as database:
                self.assertEqual(6, database.execute("SELECT COUNT(*) FROM prepared_news").fetchone()[0])
        self.assertEqual(6, sum(event == "market_prepare_submit" and fields["state"] == "submitted"
                                for event, fields in events))
        self.assertTrue(any(event == "market_prepare_drain" and fields["mode"] == "capacity"
                            and fields["future_wait_ms"] >= 0 and fields["save_ms"] >= 0
                            for event, fields in events))

    def test_multiple_articles_run_together_and_duplicate_identity_is_stored_once(self) -> None:
        active = peak = 0
        guard = threading.Lock()

        def prepare(_scope, _code, identity, *_args, **_kwargs):
            nonlocal active, peak
            with guard:
                active += 1
                peak = max(peak, active)
            time.sleep(0.025)
            with guard:
                active -= 1
            return ({"identity": identity}, {"body_status": "fulltext"}, [])

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "prepared.sqlite3"
            with patch("scripts.preprocess_historical_news_locally._prepare", side_effect=prepare):
                with ConcurrentArticlePreparation(
                    output=output, search_database=output, market_database=output,
                    matcher=(), workers=4, allow_network=False,
                ) as preparation:
                    for index in range(8):
                        preparation.submit("historical_backfill", "005930", f"article-{index}")
                    preparation.submit("historical_backfill", "005930", "article-0")
                self.assertEqual(preparation.ready, 8)
                self.assertEqual(preparation.failed, 0)
            with closing(sqlite3.connect(output)) as database:
                self.assertEqual(database.execute("SELECT COUNT(*) FROM prepared_news").fetchone()[0], 8)
            self.assertGreaterEqual(peak, 2)


if __name__ == "__main__":
    unittest.main()
