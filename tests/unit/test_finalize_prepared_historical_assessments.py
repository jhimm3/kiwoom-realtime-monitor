from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts.finalize_prepared_historical_article_bodies import finalize as finalize_bodies
from scripts.finalize_prepared_historical_assessments import finalize
from tests.unit.test_finalize_prepared_historical_article_bodies import _fixtures


class PreparedAssessmentFinalizerTests(unittest.TestCase):
    def test_assessment_resume_keeps_seed_and_creates_no_jobs_or_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            prepared, archive, raw, digest = _fixtures(Path(directory))
            finalize_bodies(prepared, archive, raw, digest, 2)
            first = finalize(prepared, archive, digest, max_rows=1)
            self.assertEqual(1, first["processed_now"])
            self.assertEqual(3, first["remaining_article_body_done"])
            with closing(sqlite3.connect(archive)) as db:
                seed = json.loads(db.execute(
                    "SELECT document_json FROM central_documents WHERE collection='news_assessment' "
                    "AND document_key='seed-article'"
                ).fetchone()[0])
                self.assertEqual("seed-body", seed["body_revision_id"])
            second = finalize(prepared, archive, digest)
            self.assertEqual({"has_event_candidate": 1, "no_event_candidate": 2},
                             second["outcomes_now"])
            self.assertEqual(0, finalize(prepared, archive, digest)["processed_now"])
            with closing(sqlite3.connect(archive)) as db:
                self.assertEqual(4, db.execute(
                    "SELECT COUNT(*) FROM archive_prepared_rule_inputs"
                ).fetchone()[0])
                self.assertEqual(4, db.execute(
                    "SELECT COUNT(*) FROM central_documents WHERE collection='news_assessment'"
                ).fetchone()[0])
                self.assertEqual(0, db.execute(
                    "SELECT COUNT(*) FROM central_news_jobs"
                ).fetchone()[0])
                self.assertEqual(0, db.execute(
                    "SELECT COUNT(*) FROM central_news_event_revisions"
                ).fetchone()[0])
                self.assertEqual(4, db.execute(
                    "SELECT COUNT(*) FROM archive_input_progress "
                    "WHERE progress_state='assessment_done'"
                ).fetchone()[0])

    def test_failure_rolls_back_document_rule_input_and_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            prepared, archive, raw, digest = _fixtures(Path(directory))
            finalize_bodies(prepared, archive, raw, digest, 2)
            finalize(prepared, archive, digest, max_rows=1)
            with closing(sqlite3.connect(archive)) as db:
                db.execute("CREATE TRIGGER reject_rule BEFORE INSERT ON archive_prepared_rule_inputs "
                           "BEGIN SELECT RAISE(ABORT,'injected rule failure'); END")
                db.commit()
            with self.assertRaisesRegex(sqlite3.IntegrityError, "injected rule failure"):
                finalize(prepared, archive, digest, max_rows=1)
            with closing(sqlite3.connect(archive)) as db:
                self.assertEqual(None, db.execute(
                    "SELECT 1 FROM central_documents WHERE collection='news_assessment' "
                    "AND document_key=(SELECT article_revision_id FROM archive_input_progress "
                    "WHERE identity='b')"
                ).fetchone())
                self.assertEqual(("article_body_done",), db.execute(
                    "SELECT progress_state FROM archive_input_progress WHERE identity='b'"
                ).fetchone())

    def test_seed_assessment_conflict_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            prepared, archive, raw, digest = _fixtures(Path(directory))
            finalize_bodies(prepared, archive, raw, digest, 2)
            with closing(sqlite3.connect(archive)) as db:
                db.execute(
                    "INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
                    "VALUES('news_assessment','005930','seed-article',1,?)",
                    (json.dumps({"assessment": "different"}),),
                )
                db.commit()
            with self.assertRaisesRegex(ValueError, "seed assessment conflicts"):
                finalize(prepared, archive, digest, max_rows=1)
            with closing(sqlite3.connect(archive)) as db:
                self.assertEqual(("article_body_done",), db.execute(
                    "SELECT progress_state FROM archive_input_progress WHERE identity='a'"
                ).fetchone())
                self.assertEqual((json.dumps({"assessment": "different"}),), db.execute(
                    "SELECT document_json FROM central_documents WHERE collection='news_assessment' "
                    "AND document_key='seed-article'"
                ).fetchone())


if __name__ == "__main__":
    unittest.main()
