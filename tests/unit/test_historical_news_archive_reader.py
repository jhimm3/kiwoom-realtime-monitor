from __future__ import annotations

from contextlib import closing
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from kiwoom_monitor.central_server.historical_news_archive import HistoricalNewsArchiveReader
from tests.unit.historical_news_test_support import (
    historical_news_archive_fixture, seal_historical_news_archive_fixture,
)


class HistoricalNewsArchiveReaderTests(unittest.TestCase):
    def test_building_archive_is_rejected_and_pages_are_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            archive = historical_news_archive_fixture(Path(root))
            with self.assertRaisesRegex(ValueError, "not sealed"):
                HistoricalNewsArchiveReader(archive, cursor_key=b"fixture-key")
            seal_historical_news_archive_fixture(archive)
            before = hashlib.sha256(archive.read_bytes()).hexdigest()
            reader = HistoricalNewsArchiveReader(archive, cursor_key=b"fixture-key")
            first = reader.search_page("005930", limit=1)
            self.assertEqual("fixture-1", first["dataset_id"])
            self.assertEqual(1, len(first["items"]))
            self.assertIsNotNone(first["next_cursor"])
            second = reader.search_page("005930", limit=1, cursor=first["next_cursor"])
            self.assertEqual(1, len(second["items"]))
            self.assertIsNone(second["next_cursor"])
            self.assertNotEqual(first["items"][0]["identity"], second["items"][0]["identity"])
            article_id = first["items"][0]["article_revision_id"]
            detail = reader.article_by_id(article_id)
            self.assertIsNotNone(detail)
            self.assertEqual(article_id, detail["article_revision_id"])
            self.assertEqual("verified", detail["assessment_status"])
            self.assertEqual(first["items"][0]["body_revision_id"],
                             detail["body"]["body_revision_id"])
            self.assertIsNone(reader.article_by_id("missing-article"))
            support = reader.article_by_id("seed-support")
            self.assertIsNotNone(support)
            self.assertEqual("support reference", support["document"]["title"])
            self.assertEqual("unassessed", support["assessment_status"])
            self.assertIsNone(support["body"])
            with self.assertRaisesRegex(ValueError, "does not belong"):
                reader.article_by_id(article_id, body_revision_id="different-body")
            with closing(reader._connect()) as db:
                with self.assertRaises(sqlite3.OperationalError):
                    db.execute("UPDATE archive_search_projection SET identity='changed'")
            self.assertEqual(before, hashlib.sha256(archive.read_bytes()).hexdigest())

    def test_cursor_cannot_be_changed_or_reused_with_another_filter(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            archive = historical_news_archive_fixture(Path(root))
            seal_historical_news_archive_fixture(archive)
            reader = HistoricalNewsArchiveReader(archive, cursor_key=b"fixture-key")
            cursor = reader.search_page("005930", limit=1)["next_cursor"]
            self.assertIsNotNone(cursor)
            with self.assertRaisesRegex(ValueError, "invalid historical archive cursor"):
                reader.search_page("000660", limit=1, cursor=cursor)
            payload, signature = cursor.split(".")
            changed = ("A" if signature[0] != "A" else "B") + signature[1:]
            with self.assertRaisesRegex(ValueError, "invalid historical archive cursor"):
                reader.search_page("005930", limit=1, cursor=payload + "." + changed)
            with self.assertRaisesRegex(ValueError, "invalid historical archive cursor"):
                reader.search_page("005930", limit=1, cursor="%%%%.%%%%")
            with self.assertRaisesRegex(ValueError, "invalid historical archive cursor"):
                reader.search_page("005930", limit=1, cursor="x" * 2049)
            with self.assertRaisesRegex(ValueError, "invalid historical archive cursor"):
                reader.search_page("005930", limit=1, cursor="")

    def test_reader_rejects_changed_file_after_initialization(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            archive = historical_news_archive_fixture(Path(root))
            seal_historical_news_archive_fixture(archive)
            reader = HistoricalNewsArchiveReader(archive, cursor_key=b"fixture-key")
            info = archive.stat()
            os.utime(archive, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000))
            with self.assertRaisesRegex(ValueError, "file changed"):
                reader.search_page("005930")


if __name__ == "__main__":
    unittest.main()
