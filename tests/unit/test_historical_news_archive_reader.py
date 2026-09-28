from __future__ import annotations

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from kiwoom_monitor.central_server.historical_news_archive import HistoricalNewsArchiveReader
from scripts.build_prepared_historical_search_projection import build
from scripts.finalize_prepared_historical_news_events import finalize
from scripts.verify_prepared_historical_news_rules import verify
from tests.unit.test_verify_prepared_historical_news_rules import _fixture


class HistoricalNewsArchiveReaderTests(unittest.TestCase):
    def _archive(self, root: Path) -> Path:
        prepared, archive, _, digest = _fixture(root, mismatch=False)
        with closing(sqlite3.connect(archive)) as db:
            db.execute("CREATE TABLE archive_seed_roles (table_name TEXT,row_key TEXT,"
                       "payload_hash TEXT,role TEXT,PRIMARY KEY(table_name,row_key))")
            db.execute(
                "INSERT INTO central_news_article_revisions("
                "article_revision_id,stock_code,identity,content_hash,collector_id,"
                "published_at,received_at,available_at,collection_scope,revision_of,document_json) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("seed-support", "005930", "support", "seed-hash", "seed",
                 "2026-08-01T00:00:00+00:00", 1.0, 1.0, "live", None,
                 json.dumps({"title": "support reference", "identity": "support"})),
            )
            db.execute("INSERT INTO archive_seed_roles VALUES(?,?,?,?)",
                       ("central_news_article_revisions", '["seed-support"]', "seed-hash",
                        "support_only"))
            db.commit()
        verify(prepared, archive, digest)
        finalize(prepared, archive, digest)
        build(archive)
        return archive

    @staticmethod
    def _seal_fixture(archive: Path, dataset_id: str = "fixture-1") -> None:
        with closing(sqlite3.connect(archive)) as db:
            report = json.loads(db.execute(
                "SELECT value_json FROM archive_build_manifest WHERE key='report'"
            ).fetchone()[0])
            report.update({"build_state": "sealed", "dataset_id": dataset_id})
            db.execute("UPDATE archive_build_manifest SET value_json=? WHERE key='report'",
                       (json.dumps(report),))
            db.commit()

    def test_building_archive_is_rejected_and_pages_are_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            archive = self._archive(Path(root))
            with self.assertRaisesRegex(ValueError, "not sealed"):
                HistoricalNewsArchiveReader(archive, cursor_key=b"fixture-key")
            self._seal_fixture(archive)
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
            archive = self._archive(Path(root))
            self._seal_fixture(archive)
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
            archive = self._archive(Path(root))
            self._seal_fixture(archive)
            reader = HistoricalNewsArchiveReader(archive, cursor_key=b"fixture-key")
            info = archive.stat()
            os.utime(archive, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000))
            with self.assertRaisesRegex(ValueError, "file changed"):
                reader.search_page("005930")


if __name__ == "__main__":
    unittest.main()
