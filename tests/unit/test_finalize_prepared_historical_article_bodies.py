from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts.finalize_prepared_historical_article_bodies import finalize
from tests.unit.historical_news_test_support import prepared_article_body_fixture


class PreparedArticleBodyFinalizerTests(unittest.TestCase):
    def test_reuses_seed_and_resumes_without_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            prepared, archive, raw, digest = prepared_article_body_fixture(Path(directory))
            first = finalize(prepared, archive, raw, digest, 2, max_rows=1)
            self.assertEqual(1, first["processed_now"])
            self.assertEqual(3, first["remaining_pending"])
            with closing(sqlite3.connect(archive)) as db:
                self.assertEqual(("seed-article", "seed-body"), db.execute(
                    "SELECT article_revision_id,body_revision_id FROM archive_input_progress "
                    "WHERE identity='a'").fetchone())
                self.assertEqual(("raw_hash_verified",), db.execute(
                    "SELECT source_status FROM archive_body_provenance WHERE identity='a'"
                ).fetchone())
            second = finalize(prepared, archive, raw, digest, 2)
            self.assertEqual(3, second["processed_now"])
            self.assertEqual(0, second["remaining_pending"])
            self.assertEqual(0, finalize(prepared, archive, raw, digest, 2)["processed_now"])
            with closing(sqlite3.connect(archive)) as db:
                self.assertEqual(4, db.execute(
                    "SELECT COUNT(*) FROM central_news_article_revisions").fetchone()[0])
                self.assertEqual(4, db.execute(
                    "SELECT COUNT(*) FROM central_news_body_revisions").fetchone()[0])
                self.assertEqual(0, db.execute(
                    "SELECT COUNT(*) FROM central_news_jobs").fetchone()[0])
                self.assertEqual([("a", "raw_hash_verified"),
                                  ("b", "summary_from_prepared"),
                                  ("c", "version_unverified"),
                                  ("d", "raw_hash_only")], db.execute(
                    "SELECT identity,source_status FROM archive_body_provenance "
                    "ORDER BY identity").fetchall())
                self.assertEqual(("prepared-body-version-unverified-v1",), db.execute(
                    "SELECT extractor_version FROM central_news_body_revisions "
                    "WHERE body_revision_id=(SELECT body_revision_id FROM archive_body_provenance "
                    "WHERE identity='d')").fetchone())

    def test_sql_failure_rolls_back_article_body_and_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            prepared, archive, raw, digest = prepared_article_body_fixture(Path(directory))
            # Initialization creates the provenance table; the first call may
            # finish one row, then the injected failure must leave the next row untouched.
            finalize(prepared, archive, raw, digest, 1, max_rows=1)
            with closing(sqlite3.connect(archive)) as db:
                db.execute("CREATE TRIGGER abort_provenance BEFORE INSERT ON archive_body_provenance "
                           "BEGIN SELECT RAISE(ABORT,'injected'); END")
                db.commit()
            with self.assertRaisesRegex(sqlite3.IntegrityError, "injected"):
                finalize(prepared, archive, raw, digest, 1, max_rows=1)
            with closing(sqlite3.connect(archive)) as db:
                self.assertEqual(1, db.execute(
                    "SELECT COUNT(*) FROM central_news_article_revisions").fetchone()[0])
                self.assertEqual(1, db.execute(
                    "SELECT COUNT(*) FROM central_news_body_revisions").fetchone()[0])
                self.assertEqual(("pending", None, None), db.execute(
                    "SELECT progress_state,article_revision_id,body_revision_id "
                    "FROM archive_input_progress WHERE identity='b'").fetchone())


if __name__ == "__main__":
    unittest.main()
