from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts.verify_prepared_historical_news_rules import verify
from tests.unit.historical_news_test_support import prepared_news_rules_fixture


class VerifyPreparedHistoricalRulesTests(unittest.TestCase):
    def test_verified_and_mismatch_are_distinct_and_replay_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            prepared, archive, _, digest = prepared_news_rules_fixture(Path(root))
            result = verify(prepared, archive, digest)
            self.assertEqual({"verified": 1, "mismatch": 1}, result["outcomes_now"])
            self.assertEqual(0, verify(prepared, archive, digest)["processed_now"])
            with closing(sqlite3.connect(archive)) as db:
                self.assertEqual([("match", "verified"), ("mismatch", "mismatch")],
                                 db.execute("SELECT identity,outcome FROM archive_verified_rule_inputs "
                                            "ORDER BY identity").fetchall())
                self.assertEqual(0, db.execute(
                    "SELECT COUNT(*) FROM central_news_event_revisions").fetchone()[0])
                self.assertEqual(0, db.execute(
                    "SELECT COUNT(*) FROM central_news_jobs").fetchone()[0])

    def test_verification_insert_failure_preserves_progress(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            prepared, archive, _, digest = prepared_news_rules_fixture(Path(root))
            verify(prepared, archive, digest, max_rows=1)
            with closing(sqlite3.connect(archive)) as db:
                db.execute("CREATE TRIGGER reject_verified BEFORE INSERT ON "
                           "archive_verified_rule_inputs BEGIN "
                           "SELECT RAISE(ABORT,'injected verification failure'); END")
                db.commit()
            with self.assertRaisesRegex(sqlite3.IntegrityError, "injected verification failure"):
                verify(prepared, archive, digest)
            with closing(sqlite3.connect(archive)) as db:
                self.assertEqual(1, db.execute(
                    "SELECT COUNT(*) FROM archive_verified_rule_inputs").fetchone()[0])
                self.assertEqual(2, db.execute(
                    "SELECT COUNT(*) FROM archive_input_progress "
                    "WHERE progress_state='assessment_done'").fetchone()[0])

    def test_later_completed_lower_key_is_verified_on_resume(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            prepared, archive, _, digest = prepared_news_rules_fixture(Path(root), mismatch=False)
            with closing(sqlite3.connect(archive)) as db:
                db.execute("UPDATE archive_input_progress SET progress_state='article_body_done' "
                           "WHERE identity='match'")
                db.commit()
            self.assertEqual(1, verify(prepared, archive, digest)["processed_now"])
            with closing(sqlite3.connect(archive)) as db:
                db.execute("UPDATE archive_input_progress SET progress_state='assessment_done' "
                           "WHERE identity='match'")
                db.commit()
            self.assertEqual(1, verify(prepared, archive, digest)["processed_now"])
            with closing(sqlite3.connect(archive)) as db:
                self.assertEqual(2, db.execute(
                    "SELECT COUNT(*) FROM archive_verified_rule_inputs "
                    "WHERE outcome='verified'").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
