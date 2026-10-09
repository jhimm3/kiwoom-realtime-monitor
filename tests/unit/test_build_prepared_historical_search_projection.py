from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts.build_prepared_historical_search_projection import _publication, build
from scripts.finalize_prepared_historical_news_events import finalize
from scripts.verify_prepared_historical_news_rules import verify
from tests.unit.historical_news_test_support import prepared_news_rules_fixture


class PreparedHistoricalSearchProjectionTests(unittest.TestCase):
    def test_publication_sort_matches_existing_utc_interpretation(self) -> None:
        self.assertEqual(_publication("2026-09-01T00:00:00+00:00"),
                         _publication("2026-09-01T00:00:00"))
        self.assertEqual((1, 0), _publication("not-a-date"))

    @staticmethod
    def _seed_article(db: sqlite3.Connection, identity: str, role: str) -> str:
        article_id = f"seed-{identity}"
        document = {"title": identity, "identity": identity,
                    "description": "seed description", "published_at": "2026-08-01T00:00:00+00:00"}
        db.execute(
            "INSERT INTO central_news_article_revisions("
            "article_revision_id,stock_code,identity,content_hash,collector_id,"
            "published_at,received_at,available_at,collection_scope,revision_of,document_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (article_id, "005930", identity, f"hash-{identity}", "seed", document["published_at"],
             1.0, 1.0, "historical_backfill", None, json.dumps(document)),
        )
        db.execute("INSERT INTO archive_seed_roles VALUES(?,?,?,?)",
                   ("central_news_article_revisions", json.dumps([article_id], separators=(",", ":")),
                    f"hash-{identity}", role))
        return article_id

    def test_incomplete_events_cannot_create_projection(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            prepared, archive, _, digest = prepared_news_rules_fixture(Path(root), mismatch=False)
            verify(prepared, archive, digest)
            with self.assertRaisesRegex(ValueError, "frozen event/RULE"):
                build(archive)
            with closing(sqlite3.connect(archive)) as db:
                self.assertFalse(db.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='archive_search_projection'"
                ).fetchone())

    def test_projection_is_pc_computed_resumable_and_does_not_seal(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            prepared, archive, _, digest = prepared_news_rules_fixture(Path(root), mismatch=False)
            with closing(sqlite3.connect(archive)) as db:
                db.execute("CREATE TABLE archive_seed_roles (table_name TEXT,row_key TEXT,"
                           "payload_hash TEXT,role TEXT,PRIMARY KEY(table_name,row_key))")
                db.commit()
            verify(prepared, archive, digest)
            finalize(prepared, archive, digest)
            first = build(archive, max_rows=1)
            self.assertEqual("building", first["state"])
            self.assertEqual(1, first["projected"])
            second = build(archive)
            self.assertEqual("complete", second["state"])
            self.assertEqual(2, second["projected"])
            self.assertEqual(0, build(archive)["processed_now"])
            with closing(sqlite3.connect(archive)) as db:
                rows = db.execute(
                    "SELECT identity,body_status,assessment_status,event_revision_id,display_json "
                    "FROM archive_search_projection ORDER BY identity"
                ).fetchall()
                self.assertEqual(2, len(rows))
                self.assertEqual("summary_only", rows[0][1])
                self.assertEqual("verified", rows[0][2])
                self.assertIsNotNone(rows[0][3])
                self.assertIsNone(rows[1][3])
                self.assertEqual("", json.loads(rows[0][4])["body_text"])
                self.assertIsInstance(json.loads(rows[0][4])["assessment"], dict)
                self.assertEqual("building", json.loads(db.execute(
                    "SELECT value_json FROM archive_build_manifest WHERE key='report'"
                ).fetchone()[0])["build_state"])

    def test_seed_historical_is_listed_but_support_only_is_not(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            prepared, archive, _, digest = prepared_news_rules_fixture(Path(root), mismatch=False)
            with closing(sqlite3.connect(archive)) as db:
                db.execute("CREATE TABLE archive_seed_roles (table_name TEXT,row_key TEXT,"
                           "payload_hash TEXT,role TEXT,PRIMARY KEY(table_name,row_key))")
                self._seed_article(db, "historical-seed", "historical")
                self._seed_article(db, "support-seed", "support_only")
                db.commit()
            verify(prepared, archive, digest)
            finalize(prepared, archive, digest)
            self.assertEqual(3, build(archive)["projected"])
            with closing(sqlite3.connect(archive)) as db:
                rows = db.execute("SELECT identity,source_kind,assessment_status "
                                  "FROM archive_search_projection ORDER BY identity").fetchall()
            self.assertEqual([("historical-seed", "seed", "unassessed"),
                              ("match", "prepared", "verified"),
                              ("mismatch", "prepared", "verified")], rows)

    def test_failed_projection_batch_rolls_back_rows_and_manifest(self) -> None:
        def snapshot(db: sqlite3.Connection) -> tuple[list, list, list]:
            return (
                db.execute("SELECT * FROM archive_search_projection "
                           "ORDER BY stock_code,identity").fetchall(),
                db.execute("SELECT key,value_json FROM archive_build_manifest ORDER BY key").fetchall(),
                db.execute("SELECT * FROM archive_search_projection_sources "
                           "ORDER BY stock_code,identity").fetchall(),
            )

        injections = {
            "second_insert": (
                "BEFORE INSERT ON archive_search_projection "
                "WHEN NEW.identity='mismatch' AND "
                "(SELECT COUNT(*) FROM archive_search_projection)=2"
            ),
            "progress_update": (
                "BEFORE UPDATE ON archive_build_manifest "
                "WHEN NEW.key='prepared_search_projection' AND "
                "(SELECT COUNT(*) FROM archive_search_projection)=3"
            ),
        }
        for phase, injection in injections.items():
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as root:
                prepared, archive, _, digest = prepared_news_rules_fixture(Path(root), mismatch=False)
                with closing(sqlite3.connect(archive)) as db:
                    db.execute("CREATE TABLE archive_seed_roles (table_name TEXT,row_key TEXT,"
                               "payload_hash TEXT,role TEXT,PRIMARY KEY(table_name,row_key))")
                    # This row sorts between the two prepared rows, so the failing
                    # batch must have inserted a new row before either failure.
                    self._seed_article(db, "middle", "historical")
                    db.commit()
                verify(prepared, archive, digest)
                finalize(prepared, archive, digest)
                self.assertEqual(1, build(archive, max_rows=1)["projected"])
                with closing(sqlite3.connect(archive)) as db:
                    before = snapshot(db)
                    self.assertEqual(["match"], [row[1] for row in before[0]])
                    self.assertEqual(["match", "middle", "mismatch"],
                                     [row[1] for row in before[2]])
                    db.execute(f"CREATE TRIGGER reject_projection {injection} BEGIN "
                               "SELECT RAISE(ABORT,'injected projection failure'); END")
                    db.commit()
                with self.assertRaisesRegex(sqlite3.IntegrityError, "injected projection failure"):
                    build(archive)
                with closing(sqlite3.connect(archive)) as db:
                    self.assertEqual(before, snapshot(db))
                    state = json.loads(dict(before[1])["prepared_search_projection"])
                    self.assertEqual(1, state["projected"])
                    self.assertEqual([], db.execute(
                        "SELECT * FROM archive_search_projection_lease").fetchall())
                    db.execute("DROP TRIGGER reject_projection")
                    db.commit()
                recovered = build(archive)
                self.assertEqual(("complete", 3, 3, 2),
                                 (recovered["state"], recovered["sources"],
                                  recovered["projected"], recovered["processed_now"]))
                self.assertEqual(0, build(archive)["processed_now"])
                with closing(sqlite3.connect(archive)) as db:
                    after = snapshot(db)
                    self.assertEqual(before[0][0], after[0][0])
                    self.assertEqual(["match", "middle", "mismatch"],
                                     [row[1] for row in after[0]])
                    self.assertEqual(before[2], after[2])
                    self.assertEqual([], db.execute(
                        "SELECT * FROM archive_search_projection_lease").fetchall())


if __name__ == "__main__":
    unittest.main()
