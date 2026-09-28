from __future__ import annotations

import unittest

from scripts.select_historical_news_page_keys import _page_observation_keys


class HistoricalNewsPageKeysTests(unittest.TestCase):
    def test_exact_day_and_range_request_keys(self) -> None:
        self.assertEqual(
            [("000050", "2025-05-09", "경방", 11)],
            _page_observation_keys("code=000050&date=2025-05-09&query=경방&start=11"),
        )
        self.assertEqual(
            [("000050", "2025-05-08", "경방", 1),
             ("000050", "2025-05-09", "경방", 1)],
            _page_observation_keys(
                "code=000050&from=2025-05-08&to=2025-05-09&query=경방&start=1"),
        )

    def test_bad_range_fails(self) -> None:
        with self.assertRaisesRegex(ValueError, "date range"):
            _page_observation_keys("code=000050&from=2025-05-09&to=2025-05-08&query=x&start=1")

    def test_unescaped_query_symbols_are_preserved(self) -> None:
        self.assertEqual(
            [("000050", "2025-05-08", "C&C+100%", 21)],
            _page_observation_keys(
                "code=000050&date=2025-05-08&query=C&C+100%&start=21"),
        )


if __name__ == "__main__":
    unittest.main()
