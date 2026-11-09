import tempfile
import unittest

import json
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from kiwoom_monitor.infrastructure.dart_disclosures import DartDisclosureClient


class TestDartDisclosureFilter(unittest.TestCase):
    def setUp(self) -> None:
        self._test_temp_directory = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._test_temp_directory.name)

    def tearDown(self) -> None:
        self._test_temp_directory.cleanup()

    def test_list_disclosures_can_limit_to_exchange_filings(self):
        cache = self.tmp_path / "corp_codes.json"
        cache.write_text(json.dumps({"123456": "01234567"}), encoding="utf-8")
        client = DartDisclosureClient("test-key", cache)
        queries = []

        def response(url):
            queries.append(parse_qs(urlsplit(url).query))
            return {"status": "013"}

        client._json = response
        assert client.list_disclosures("123456", date(2025, 1, 1), date(2025, 1, 31),
                                       disclosure_type="I") == ()
        assert queries[0]["pblntf_ty"] == ["I"]
        assert client.list_disclosures("123456", date(2025, 1, 1), date(2025, 1, 31)) == ()
        assert "pblntf_ty" not in queries[1]

    def test_news_search_covers_all_dated_pages_and_uses_kst_receipt_day(self):
        client = DartDisclosureClient("fixture-key", self.tmp_path / "unused.json")
        client._corp_codes = lambda: {"123456": "01234567"}
        queries = []

        def response(url):
            query = parse_qs(urlsplit(url).query)
            queries.append(query)
            page = int(query["page_no"][0])
            return {"status": "000", "total_page": 2, "list": [
                {"rcept_no": f"2026092100000{page}", "rcept_dt": "20260921", "report_nm": "영업정지"},
            ]}

        client._json = response
        items = client.search("123456", "테스트", begin=date(2026, 9, 21), end=date(2026, 9, 22))
        self.assertEqual(["1", "2"], [row["page_no"][0] for row in queries])
        self.assertEqual({("20260921", "20260922")}, {(row["bgn_de"][0], row["end_de"][0]) for row in queries})
        self.assertEqual(2, len(items))
        self.assertEqual("2026-09-21T00:00:00+09:00", items[-1].published_at.isoformat())
        with self.assertRaisesRegex(ValueError, "company identity"):
            client.search("999999", "미확인", begin=date(2026, 9, 21), end=date(2026, 9, 22))
