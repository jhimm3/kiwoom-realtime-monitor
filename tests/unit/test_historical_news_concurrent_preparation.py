from __future__ import annotations

import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from scripts.preprocess_historical_news_locally import ConcurrentArticlePreparation


class ConcurrentArticlePreparationTests(unittest.TestCase):
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
