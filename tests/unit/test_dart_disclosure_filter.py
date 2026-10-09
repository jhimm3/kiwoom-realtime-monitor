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
