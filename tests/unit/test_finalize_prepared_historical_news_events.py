from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import uuid

from kiwoom_monitor.application.news_rules import SupplyContractRuleResult, rule_input_hash
from kiwoom_monitor.central_server.database import _save_sqlite_news_event, stable_document_hash
from scripts.finalize_prepared_historical_news_events import (
    _acquire_run_lease, _freeze, _group_candidates, _ordered_worklist,
    _release_run_lease, finalize,
)
from scripts.verify_prepared_historical_news_rules import verify
from tests.unit.historical_news_test_support import prepared_news_rules_fixture


class FinalizePreparedHistoricalEventsTests(unittest.TestCase):
    def test_identity_and_event_key_disagreement_is_ambiguous(self) -> None:
        with closing(sqlite3.connect(":memory:")) as db:
            db.execute("CREATE TABLE central_news_article_revisions "
                       "(article_revision_id TEXT,identity TEXT)")
            db.execute("CREATE TABLE central_news_event_revisions "
                       "(event_revision_id TEXT,event_id TEXT,stock_code TEXT,event_key TEXT)")
            db.execute("CREATE TABLE central_news_event_membership_revisions "
                       "(article_revision_id TEXT,event_revision_id TEXT,event_id TEXT)")
            db.execute("INSERT INTO central_news_article_revisions VALUES('article-a','identity')")
            db.executemany("INSERT INTO central_news_event_revisions VALUES(?,?,?,?)", (
                ("revision-a", "event-a", "005930", "other-key"),
                ("revision-b", "event-b", "005930", "target-key"),
            ))
            db.execute("INSERT INTO central_news_event_membership_revisions "
                       "VALUES('article-a','revision-a','event-a')")
            self.assertEqual(("identity_event_key", ("event-a", "event-b")),
                             _group_candidates(db, "005930", "identity", "target-key"))

    def test_worklist_keeps_parent_before_earlier_published_child(self) -> None:
        parent = ("005930", "parent", "hash-a", "fingerprint-a", "article-a",
                  "body-a", "2024-01-03T09:00:00+09:00", None)
        child = ("005930", "child", "hash-b", "fingerprint-b", "article-b",
                 "body-b", "2024-01-02T09:00:00+09:00", "article-a")
        self.assertEqual([parent, child], _ordered_worklist([child, parent]))

    def test_event_and_null_resume_without_new_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            prepared, archive, _, digest = prepared_news_rules_fixture(Path(root), mismatch=False)
            verify(prepared, archive, digest)
            first = finalize(prepared, archive, digest, max_rows=1)
            self.assertEqual(1, first["remaining"])
            self.assertEqual(1, first["outcomes_now"]["new_group"])
            second = finalize(prepared, archive, digest)
            self.assertEqual(1, second["outcomes_now"]["no_event"])
            self.assertEqual(0, finalize(prepared, archive, digest)["processed_now"])
            with closing(sqlite3.connect(archive)) as db:
                self.assertEqual(1, db.execute(
                    "SELECT COUNT(*) FROM central_news_event_revisions").fetchone()[0])
                self.assertEqual(1, db.execute(
                    "SELECT COUNT(*) FROM central_news_event_membership_revisions").fetchone()[0])
                self.assertEqual(2, db.execute(
                    "SELECT COUNT(*) FROM archive_input_progress WHERE progress_state='event_done'"
                ).fetchone()[0])
                self.assertEqual(0, db.execute(
                    "SELECT COUNT(*) FROM central_news_jobs").fetchone()[0])

    def test_mismatch_blocks_worklist_before_any_event(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            prepared, archive, _, digest = prepared_news_rules_fixture(Path(root))
            verify(prepared, archive, digest)
            with self.assertRaisesRegex(ValueError, "unverified or mismatched"):
                finalize(prepared, archive, digest)
            with closing(sqlite3.connect(archive)) as db:
                self.assertEqual(0, db.execute(
                    "SELECT COUNT(*) FROM central_news_event_revisions").fetchone()[0])

    def test_same_key_appends_parent_and_membership_chain(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            prepared, archive, _, digest = prepared_news_rules_fixture(
                Path(root), mismatch=False, same_event=True,
            )
            verify(prepared, archive, digest)
            finalize(prepared, archive, digest)
            with closing(sqlite3.connect(archive)) as db:
                events = db.execute(
                    "SELECT event_revision_id,event_id,revision_of,novelty "
                    "FROM central_news_event_revisions ORDER BY accepted_sequence"
                ).fetchall()
                members = db.execute(
                    "SELECT membership_revision_id,event_id,event_revision_id "
                    "FROM central_news_event_membership_revisions ORDER BY accepted_sequence"
                ).fetchall()
                self.assertEqual(2, len(events))
                self.assertEqual(events[0][1], events[1][1])
                self.assertEqual(events[0][0], events[1][2])
                self.assertEqual("REPUBLICATION", events[1][3])
                self.assertEqual([event[0] for event in events], [m[2] for m in members])

    def test_exact_seed_reuse_preserves_seed_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            prepared, archive, _, digest = prepared_news_rules_fixture(Path(root), mismatch=False)
            verify(prepared, archive, digest)
            with closing(sqlite3.connect(archive)) as db:
                article_id, body_id, payload = db.execute(
                    "SELECT article_revision_id,body_revision_id,computed_json "
                    "FROM archive_verified_rule_inputs WHERE identity='match'"
                ).fetchone()
                result = json.loads(payload)["rule_result"]
                typed = SupplyContractRuleResult(**result)
                db.execute("BEGIN IMMEDIATE")
                seed_id = _save_sqlite_news_event(db, {
                    "stock_code": "005930", "article_revision_id": article_id,
                    "body_revision_id": body_id, "rule_version": typed.rule_version,
                    "input_hash": rule_input_hash(article_id, body_id, typed),
                    "candidate_identities": (), "result": result,
                })
                db.commit()
                before = db.execute(
                    "SELECT * FROM central_news_event_revisions WHERE event_revision_id=?",
                    (seed_id,),
                ).fetchone()
            final = finalize(prepared, archive, digest)
            self.assertEqual(1, final["outcomes_now"]["exact_reuse"])
            with closing(sqlite3.connect(archive)) as db:
                self.assertEqual(before, db.execute(
                    "SELECT * FROM central_news_event_revisions WHERE event_revision_id=?",
                    (seed_id,),
                ).fetchone())
                self.assertEqual(1, db.execute(
                    "SELECT COUNT(*) FROM central_news_event_revisions").fetchone()[0])

    def test_event_resolution_failure_rolls_back_event_and_progress(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            prepared, archive, _, digest = prepared_news_rules_fixture(Path(root), mismatch=False)
            verify(prepared, archive, digest)
            with closing(sqlite3.connect(archive)) as db:
                _freeze(db, digest)
                db.execute("CREATE TRIGGER reject_resolution BEFORE INSERT ON "
                           "archive_event_resolution BEGIN "
                           "SELECT RAISE(ABORT,'injected resolution failure'); END")
                db.commit()
            with self.assertRaisesRegex(sqlite3.IntegrityError, "injected resolution failure"):
                finalize(prepared, archive, digest)
            with closing(sqlite3.connect(archive)) as db:
                self.assertEqual(0, db.execute(
                    "SELECT COUNT(*) FROM central_news_event_revisions").fetchone()[0])
                self.assertEqual(0, db.execute(
                    "SELECT COUNT(*) FROM central_news_event_membership_revisions").fetchone()[0])
                self.assertEqual(2, db.execute(
                    "SELECT COUNT(*) FROM archive_input_progress "
                    "WHERE progress_state='assessment_done'").fetchone()[0])

    def test_ambiguous_seed_key_blocks_without_guessing_group(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            prepared, archive, _, digest = prepared_news_rules_fixture(Path(root), mismatch=False)
            verify(prepared, archive, digest)
            with closing(sqlite3.connect(archive)) as db:
                article_id, body_id, payload = db.execute(
                    "SELECT article_revision_id,body_revision_id,computed_json "
                    "FROM archive_verified_rule_inputs WHERE identity='match'"
                ).fetchone()
                result = json.loads(payload)["rule_result"]
                typed = SupplyContractRuleResult(**result)
                source_doc = json.loads(db.execute(
                    "SELECT document_json FROM central_news_article_revisions "
                    "WHERE article_revision_id=?", (article_id,),
                ).fetchone()[0])
                for index in (1, 2):
                    doc = dict(source_doc, identity=f"seed-identity-{index}")
                    db.execute(
                        "INSERT INTO central_news_article_revisions("
                        "article_revision_id,stock_code,identity,content_hash,collector_id,"
                        "published_at,received_at,available_at,collection_scope,revision_of,document_json) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (f"seed-article-{index}", "005930", doc["identity"],
                         stable_document_hash(doc), "fixture", doc["published_at"],
                         1.0, 1.0, "historical_news_pc_backfill", None, json.dumps(doc)),
                    )
                    db.execute(
                        "INSERT INTO central_news_body_revisions("
                        "body_revision_id,article_revision_id,content_hash,extractor_version,"
                        "fetched_at,available_at,status,body_text,error) "
                        "SELECT ?,?,content_hash,extractor_version,fetched_at,available_at,"
                        "status,body_text,error FROM central_news_body_revisions "
                        "WHERE body_revision_id=?",
                        (f"seed-body-{index}", f"seed-article-{index}", body_id),
                    )
                first_id = _save_sqlite_news_event(db, {
                    "stock_code": "005930", "article_revision_id": "seed-article-1",
                    "body_revision_id": "seed-body-1", "rule_version": typed.rule_version,
                    "input_hash": rule_input_hash("seed-article-1", "seed-body-1", typed),
                    "candidate_identities": (), "result": result,
                })
                event_columns = [row[1] for row in db.execute(
                    "PRAGMA table_info(central_news_event_revisions)"
                ) if row[1] != "accepted_sequence"]
                event_values = list(db.execute(
                    f"SELECT {','.join(event_columns)} FROM central_news_event_revisions "
                    "WHERE event_revision_id=?", (first_id,),
                ).fetchone())
                changed = {"event_revision_id": uuid.uuid4().hex,
                           "event_id": uuid.uuid4().hex,
                           "article_revision_id": "seed-article-2",
                           "body_revision_id": "seed-body-2",
                           "input_hash": rule_input_hash("seed-article-2", "seed-body-2", typed)}
                for name, value in changed.items():
                    event_values[event_columns.index(name)] = value
                db.execute(
                    f"INSERT INTO central_news_event_revisions({','.join(event_columns)}) "
                    f"VALUES({','.join('?' for _ in event_columns)})", event_values,
                )
                member_columns = [row[1] for row in db.execute(
                    "PRAGMA table_info(central_news_event_membership_revisions)"
                ) if row[1] != "accepted_sequence"]
                member_values = list(db.execute(
                    f"SELECT {','.join(member_columns)} "
                    "FROM central_news_event_membership_revisions WHERE event_revision_id=?",
                    (first_id,),
                ).fetchone())
                for name, value in {"membership_revision_id": uuid.uuid4().hex,
                                    "event_id": changed["event_id"],
                                    "event_revision_id": changed["event_revision_id"],
                                    "article_revision_id": "seed-article-2",
                                    "body_revision_id": "seed-body-2"}.items():
                    member_values[member_columns.index(name)] = value
                db.execute(
                    f"INSERT INTO central_news_event_membership_revisions({','.join(member_columns)}) "
                    f"VALUES({','.join('?' for _ in member_columns)})", member_values,
                )
                db.commit()
            first = finalize(prepared, archive, digest)
            self.assertEqual(1, first["outcomes_now"]["conflict"])
            self.assertIsNotNone(first["blocked_at"])
            self.assertEqual(0, finalize(prepared, archive, digest)["processed_now"])
            with closing(sqlite3.connect(archive)) as db:
                self.assertEqual(2, db.execute(
                    "SELECT COUNT(*) FROM central_news_event_revisions").fetchone()[0])
                self.assertEqual(("assessment_done",), db.execute(
                    "SELECT progress_state FROM archive_input_progress WHERE identity='match'"
                ).fetchone())
                stored = db.execute(
                    "SELECT reason,candidate_event_ids_json FROM archive_event_resolution "
                    "WHERE identity='match'"
                ).fetchone()
                self.assertEqual("ambiguous_event_key", stored[0])
                self.assertEqual(2, len(json.loads(stored[1])))

    def test_only_one_builder_runs_and_expired_lease_can_resume(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            prepared, archive, _, digest = prepared_news_rules_fixture(Path(root), mismatch=False)
            verify(prepared, archive, digest)
            with closing(sqlite3.connect(archive)) as db:
                _freeze(db, digest)
                stale_owner = _acquire_run_lease(db)
            with self.assertRaisesRegex(ValueError, "another archive event builder"):
                finalize(prepared, archive, digest)
            with closing(sqlite3.connect(archive)) as db:
                db.execute("UPDATE archive_event_builder_lease SET expires_at=0")
                db.commit()
            self.assertEqual(0, finalize(prepared, archive, digest)["remaining"])
            with closing(sqlite3.connect(archive)) as db:
                _release_run_lease(db, stale_owner)
                self.assertEqual(0, db.execute(
                    "SELECT COUNT(*) FROM archive_event_builder_lease").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
