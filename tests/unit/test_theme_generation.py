from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
import uuid
from contextlib import closing, contextmanager
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.domain.time_basis import KST
from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.infrastructure.persistence.stock_repository import StockRepository
from kiwoom_monitor.infrastructure.persistence.theme_backup import ThemeBackupService
from kiwoom_monitor.infrastructure.persistence.theme_repository import ThemeRepository


NOW = datetime(2026, 10, 23, 10, 30, tzinfo=KST)
COLLECTIONS = ("theme_profile", "theme_stock", "theme_metadata")


def theme_document(name="반도체"):
    return {
        "format": ThemeBackupService.FORMAT, "version": ThemeBackupService.VERSION,
        "created_at": "2026-10-23T09:00:00+09:00", "active_profile": "기본 테마",
        "profiles": [{"name": "기본 테마", "themes": [{"name": name, "color": "#123456"}],
                      "stock_themes": [{"code": "005930", "theme": name, "color": "#654321"}],
                      "theme_name_decisions": [{"kind": "alias", "source": "칩", "target": name}],
                      "theme_suggestions": []}],
        "aliases": [{"alias": "삼전", "code": "005930"}],
        "stock_catalog": [{"code": "005930", "name": "삼성전자", "market": "KOSPI"}],
    }


class ThemeGenerationContract:
    """The same result/failure oracles run on both real native backends."""

    def publish(self, document, *, publication_id=None, expected=None, **kwargs):
        return self.store.publish_theme_snapshot(
            document, publication_id=publication_id or uuid.uuid4().hex,
            expected_snapshot_id=expected, origin_device=kwargs.pop("origin_device", "pc-a"),
            **kwargs,
        )

    def state(self):
        return {"documents": {name: self.store.load_documents(name) for name in COLLECTIONS},
                "history": self.store.load_theme_snapshots()}

    def test_generation_read_and_empty_replacement_preserve_originals(self):
        self.assertIsNone(self.store.load_accepted_theme_snapshot())
        document = theme_document()
        first = self.publish(document)
        snapshot = first["snapshot"]
        self.assertTrue(first["changed"])
        self.assertFalse(first["replayed"])
        self.assertEqual(document, snapshot["document"])
        self.assertEqual("pc-a", snapshot["origin_device"])
        self.assertEqual(document["created_at"], snapshot["effective_at"])
        self.assertEqual(NOW.timestamp(), snapshot["received_at"])
        self.assertEqual(NOW.timestamp(), snapshot["available_at"])
        self.assertEqual(snapshot, self.store.load_accepted_theme_snapshot())
        snapshot["document"]["aliases"].clear()
        stored = self.store.load_accepted_theme_snapshot(snapshot_id=snapshot["snapshot_id"])
        self.assertEqual(document, stored["document"])
        self.assertIsNone(self.store.load_accepted_theme_snapshot(snapshot_id="absent"))
        self.assertEqual("반도체", self.store.load_documents("theme_profile")[0]["key"])
        self.assertEqual("005930|반도체", self.store.load_documents("theme_stock")[0]["key"])
        empty = deepcopy(document)
        empty["profiles"] = []
        empty["active_profile"] = ""
        second = self.publish(empty, expected=stored["snapshot_id"])["snapshot"]
        self.assertEqual([], self.store.load_documents("theme_profile"))
        self.assertEqual([], self.store.load_documents("theme_stock"))
        self.assertEqual(empty, self.store.load_documents("theme_metadata")[0]["document"])
        self.assertEqual(stored["snapshot_id"], second["revision_of"])
        self.assertEqual(document, self.store.load_accepted_theme_snapshot(
            snapshot_id=stored["snapshot_id"],
        )["document"])

    def test_noop_preserves_first_document_completion_and_history(self):
        document = theme_document()
        first = self.publish(document)
        before = self.state()
        resend = deepcopy(document)
        resend["created_at"] = "2026-10-23T10:00:00+09:00"
        result = self.publish(resend, expected=first["snapshot"]["snapshot_id"])
        self.assertFalse(result["changed"])
        self.assertFalse(result["replayed"])
        self.assertEqual(first["snapshot"], result["snapshot"])
        self.assertEqual(before, self.state())

    def test_publication_identity_preserves_first_receipt_and_rejects_logical_conflicts(self):
        document = theme_document()
        publication_id = uuid.uuid4().hex
        effective = "2026-10-23T08:59:00+09:00"
        first = self.publish(document, publication_id=publication_id, effective_at=effective)
        retry = deepcopy(document)
        retry["created_at"] = "2026-10-23T10:00:00+09:00"
        replay = self.publish(retry, publication_id=publication_id, effective_at=effective)
        self.assertTrue(replay["replayed"])
        self.assertEqual(first["snapshot"], replay["snapshot"])
        before = self.state()
        for changed, overrides in (
            (theme_document("원전"), {"effective_at": effective}),
            (document, {"effective_at": "2026-10-23T09:00:00+09:00"}),
            (document, {"effective_at": effective, "origin_device": "pc-b"}),
            (document, {"effective_at": effective, "expected": "other"}),
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaisesRegex(ValueError, "immutable theme publication conflict"):
                    self.publish(changed, publication_id=publication_id, **overrides)
                self.assertEqual(before, self.state())

    def test_stale_predecessor_cannot_replace_or_ack_current_generation(self):
        first = self.publish(theme_document())["snapshot"]
        second = self.publish(theme_document("원전"), expected=first["snapshot_id"])["snapshot"]
        before = self.state()
        for stale in (None, first["snapshot_id"]):
            with self.subTest(stale=stale):
                with self.assertRaisesRegex(ValueError, "theme snapshot revision conflict"):
                    self.publish(theme_document("로봇"), expected=stale)
                self.assertEqual(before, self.state())
        self.assertEqual(second, self.store.load_accepted_theme_snapshot())

    def test_native_fault_rolls_back_all_projections_and_history(self):
        first = self.publish(theme_document())["snapshot"]
        for table in ("central_documents", "central_theme_snapshots"):
            with self.subTest(table=table):
                before = self.state()
                with self.native_fault(table):
                    with self.assertRaisesRegex(self.native_error, "theme native fault"):
                        self.publish(theme_document("실패"), expected=first["snapshot_id"])
                self.assertEqual(before, self.state())
        self.assertTrue(self.publish(theme_document("복구"), expected=first["snapshot_id"])["changed"])

    def test_two_native_editors_with_one_predecessor_accept_exactly_one(self):
        first = self.publish(theme_document())["snapshot"]
        barrier = threading.Barrier(2)
        accepted, failures = [], []

        def edit(name):
            peer = self.new_store()
            try:
                barrier.wait(timeout=5)
                accepted.append(peer.publish_theme_snapshot(
                    theme_document(name), publication_id=uuid.uuid4().hex,
                    expected_snapshot_id=first["snapshot_id"], origin_device=name,
                ))
            except BaseException as error:
                failures.append(error)
            finally:
                peer.close()

        workers = [threading.Thread(target=edit, args=(name,)) for name in ("편집A", "편집B")]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(15)
        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual(1, len(accepted))
        self.assertEqual(1, len(failures))
        self.assertIsInstance(failures[0], ValueError)
        self.assertIn("revision conflict", str(failures[0]))
        self.assertEqual(accepted[0]["snapshot"], self.store.load_accepted_theme_snapshot())
        self.assertEqual(2, len(self.store.load_theme_snapshots()))

    def test_committed_ack_loss_retry_recovers_original_after_peer_publish(self):
        document, publication_id = theme_document(), uuid.uuid4().hex
        with self.lost_commit_ack():
            with self.assertRaisesRegex(RuntimeError, "theme commit ACK lost"):
                self.publish(document, publication_id=publication_id)
        original = self.store.load_accepted_theme_snapshot(snapshot_id=publication_id)
        self.assertEqual(document, original["document"])
        current = self.publish(theme_document("다음"), expected=publication_id)["snapshot"]
        before = self.state()
        replay = self.publish(document, publication_id=publication_id)
        self.assertTrue(replay["replayed"])
        self.assertFalse(replay["changed"])
        self.assertEqual(original, replay["snapshot"])
        self.assertEqual(current, self.store.load_accepted_theme_snapshot())
        self.assertEqual(before, self.state())


    def test_guarded_publication_and_read_reject_late_legacy_projection_without_mutation(self):
        first = self.publish(theme_document())["snapshot"]
        self.store.upsert_documents("theme_profile", [{"owner": "legacy", "key": "late",
                                                     "document": {"original": "late edit"}}])
        before = self.state()
        for command in (
            lambda: self.store.publish_theme_snapshot(theme_document("다음"), publication_id="guarded-next",
                expected_snapshot_id=first["snapshot_id"], origin_device="pc-b", require_current_projection=True),
            lambda: self.store.load_accepted_theme_snapshot(require_current_projection=True),
        ):
            with self.assertRaisesRegex(ValueError, "explicit reconciliation") as caught:
                command()
            self.assertEqual("current_projection_conflict", caught.exception.code)
            self.assertEqual(before, self.state())
        self.assertEqual(first, self.store.load_accepted_theme_snapshot(snapshot_id=first["snapshot_id"]))
        self.assertEqual("late edit", self.store.load_current_theme_snapshot()["collections"]["theme_profile"][0]["document"]["original"])

    def test_guarded_empty_stream_does_not_adopt_existing_legacy_rows(self):
        self.store.upsert_documents("theme_stock", [{"owner": "legacy", "key": "original",
                                                   "document": {"original": "preserve"}}])
        before = self.state()
        with self.assertRaisesRegex(ValueError, "explicit reconciliation"):
            self.store.publish_theme_snapshot(theme_document(), publication_id="initial", expected_snapshot_id=None,
                                              require_current_projection=True)
        self.assertEqual(before, self.state())
        self.assertIsNone(self.store.load_accepted_theme_snapshot())

    def test_guarded_ack_loss_replay_keeps_first_receipt_after_peer_and_legacy_writes(self):
        document = theme_document()
        with self.lost_commit_ack():
            with self.assertRaisesRegex(RuntimeError, "theme commit ACK lost"):
                self.store.publish_theme_snapshot(document, publication_id="lost-guarded", expected_snapshot_id=None,
                                                  origin_device="pc-a", require_current_projection=True)
        original = self.store.load_accepted_theme_snapshot(snapshot_id="lost-guarded")
        self.publish(theme_document("peer"), expected="lost-guarded")
        self.store.replace_documents("theme_stock", [])
        before = self.state()
        replay = self.store.publish_theme_snapshot(document, publication_id="lost-guarded", expected_snapshot_id=None,
                                                   origin_device="pc-a", require_current_projection=True)
        self.assertEqual({"snapshot": original, "changed": False, "replayed": True}, replay)
        self.assertEqual(before, self.state())



    def test_guarded_publication_serializes_legacy_profile_writer_before_current_can_diverge(self):
        from kiwoom_monitor.central_server import database_documents
        first = self.publish(theme_document())["snapshot"]
        entered, release, legacy_done = threading.Event(), threading.Event(), threading.Event()
        receipts, errors = [], []
        original = database_documents._assert_current_theme_projection
        def guard(cursor, placeholder, snapshot):
            original(cursor, placeholder, snapshot)
            entered.set()
            if not release.wait(5): raise AssertionError("guarded writer was not released")
        def publish():
            store = self.new_store()
            try:
                receipts.append(store.publish_theme_snapshot(theme_document("publisher"), publication_id="guarded-race",
                    expected_snapshot_id=first["snapshot_id"], origin_device="pc-a", require_current_projection=True))
            except BaseException as error: errors.append(error)
            finally: store.close()
        def legacy():
            store = self.new_store()
            try:
                store.upsert_documents("theme_profile", [{"owner": "legacy", "key": "late", "document": {"original": 1}}])
                legacy_done.set()
            except BaseException as error: errors.append(error)
            finally: store.close()
        writer = threading.Thread(target=publish); peer = threading.Thread(target=legacy)
        with patch.object(database_documents, "_assert_current_theme_projection", guard):
            try:
                writer.start(); self.assertTrue(entered.wait(5))
                peer.start(); self.assertFalse(legacy_done.wait(0.15))
            finally:
                release.set(); writer.join(15)
                if peer.ident is not None: peer.join(15)
        self.assertFalse(writer.is_alive()); self.assertFalse(peer.is_alive())
        self.assertEqual([], errors)
        self.assertTrue(legacy_done.is_set())
        self.assertEqual(1, len(receipts))
        self.assertEqual("guarded-race", receipts[0]["snapshot"]["snapshot_id"])
        self.assertEqual(receipts[0]["snapshot"], self.store.load_accepted_theme_snapshot())
        with self.assertRaisesRegex(ValueError, "explicit reconciliation"):
            self.store.load_accepted_theme_snapshot(require_current_projection=True)



class SQLiteThemeGenerationTests(ThemeGenerationContract, unittest.TestCase):
    native_error = sqlite3.IntegrityError

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "central.sqlite3"
        self.store = self.new_store()
        self.store.initialize()
        self.addCleanup(self.store.close)

    def new_store(self):
        return SQLiteQueryStore(self.path, now_provider=lambda: NOW)

    @contextmanager
    def native_fault(self, table):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            condition = "WHEN NEW.collection='theme_metadata' " if table == "central_documents" else ""
            connection.execute(
                f"CREATE TRIGGER theme_fault BEFORE INSERT ON {table} {condition}"
                "BEGIN SELECT RAISE(ABORT,'theme native fault'); END",
            )
        try:
            yield
        finally:
            with closing(sqlite3.connect(self.path)) as connection, connection:
                connection.execute("DROP TRIGGER theme_fault")

    @contextmanager
    def lost_commit_ack(self):
        class Connection(sqlite3.Connection):
            def commit(connection):
                super().commit()
                raise RuntimeError("theme commit ACK lost")

        raw_connect = sqlite3.connect
        with patch.object(self.store, "_connect", lambda: raw_connect(self.path, factory=Connection)):
            yield


class ThemeLocalSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "monitor.sqlite3"
        Database(self.path).initialize()
        StockRepository(self.path).upsert("005930", "삼성전자", "KOSPI")
        self.themes = ThemeRepository(self.path)
        self.themes.replace_for_stock("005930", ("반도체",))
        self.backup = ThemeBackupService(self.path, now_provider=lambda: NOW)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("PRAGMA journal_mode=WAL")

    def test_one_connection_snapshot_keeps_revision_and_document_before_concurrent_edit(self):
        before = self.backup.read_sync_snapshot()
        exported = self.backup.export_document
        errors = []

        def edit():
            try:
                ThemeRepository(self.path).replace_for_stock("005930", ("원전",))
                StockRepository(self.path).upsert("005930", "새 이름", "KOSDAQ")
            except BaseException as error:
                errors.append(error)

        def export_after_peer_commit(connection):
            peer = threading.Thread(target=edit)
            peer.start()
            peer.join(5)
            self.assertFalse(peer.is_alive())
            self.assertFalse(errors)
            return exported(connection)

        raw_connect = sqlite3.connect
        connections = []

        def connect(*args, **kwargs):
            connection = raw_connect(*args, **kwargs)
            if threading.current_thread() is threading.main_thread():
                connections.append(connection)
            return connection

        with patch.object(self.backup, "export_document", side_effect=export_after_peer_commit), patch(
            "kiwoom_monitor.infrastructure.persistence.theme_backup.sqlite3.connect", side_effect=connect,
        ):
            captured = self.backup.read_sync_snapshot()
        self.assertEqual(before, captured)
        self.assertEqual(1, len(connections))
        with self.assertRaises(sqlite3.ProgrammingError):
            connections[0].execute("SELECT 1")
        after = self.backup.read_sync_snapshot()
        self.assertNotEqual(captured["revision"], after["revision"])
        self.assertNotEqual(captured["content_hash"], after["content_hash"])
        self.assertEqual("원전", after["document"]["profiles"][0]["stock_themes"][0]["theme"])

    def test_market_observations_keep_narrow_snapshot_identity_and_injected_kst(self):
        before = self.backup.read_sync_snapshot()
        StockRepository(self.path).update_price_cache(
            {"005930": 100}, {}, {"005930": 1000.0}, NOW.date(), observed_at=NOW,
        )
        self.assertEqual(before, self.backup.read_sync_snapshot())
        self.assertEqual(NOW.isoformat(timespec="seconds"), before["document"]["created_at"])
        self.assertEqual({"code", "name", "market"},
                         set(before["document"]["stock_catalog"][0]))
