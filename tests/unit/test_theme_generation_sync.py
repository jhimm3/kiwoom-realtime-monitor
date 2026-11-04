from __future__ import annotations

import asyncio
import io
import json
import shutil
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing, nullcontext
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlsplit

from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.infrastructure.central_content_client import CentralContentClient, CentralThemeConflict
from kiwoom_monitor.infrastructure.central_content_sync import CentralContentSyncService
from kiwoom_monitor.infrastructure.central_theme_sync import CentralThemeSyncDispatcher
from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.infrastructure.persistence.settings_backup import SettingsBackupService
from kiwoom_monitor.infrastructure.persistence.theme_backup import ThemeBackupError, ThemeBackupService
from kiwoom_monitor.infrastructure.persistence.theme_repository import ThemeRepository
from tests.unit.test_theme_generation import NOW, theme_document
from tests.unit.test_theme_current_snapshot import Response


class NativeThemeClient:
    theme_sync_scope = "https://native-theme.test"

    def __init__(self, store):
        self.store = store
        self.calls = []
        self.after_publish = None
        self.before_receive = None
        self.lose_ack = False

    def server_now(self): return NOW

    def publish_theme_snapshot(self, document, **kwargs):
        self.calls.append(("PUT", deepcopy(kwargs), deepcopy(document)))
        result = self.store.publish_theme_snapshot(document, **kwargs, require_current_projection=True)
        callback, self.after_publish = self.after_publish, None
        if callback is not None: callback()
        if self.lose_ack:
            self.lose_ack = False
            raise RuntimeError("controlled committed ACK loss")
        return result

    def load_accepted_theme_snapshot(self, **kwargs):
        self.calls.append(("GET", deepcopy(kwargs)))
        callback, self.before_receive = self.before_receive, None
        if callback is not None: callback()
        return self.store.load_accepted_theme_snapshot(**kwargs, require_current_projection=not kwargs.get("snapshot_id"))

    def load_all(self, collection, **kwargs):
        self.calls.append(("COLLECTION_GET", collection))
        return self.store.load_documents(collection, updated_after=kwargs.get("updated_after", 0))

    def capabilities(self): return {"journal_news_links_v2": True}

    def upsert(self, collection, documents):
        self.calls.append(("COLLECTION_POST", collection))
        self.store.upsert_documents(collection, documents)
        return len(documents)

    def replace(self, *_args, **_kwargs):
        raise AssertionError("theme generation must never split into collection PUTs")


class ConfirmedThemeReceiveService(CentralContentSyncService):
    """Exercise the existing explicit user-confirmed CAS import, not head adoption."""
    def replace_themes(self, path):
        revision = ThemeBackupService(path).revision()
        snapshot = self._client.load_accepted_theme_snapshot()
        return self.apply_theme_snapshot(path, {"theme_profile": [], "theme_stock": [],
            "theme_metadata": [{"document": snapshot["document"]}]}, expected_revision=revision)


class ThemeGenerationSyncTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.main = self.root / "monitor.sqlite3"
        Database(self.main).initialize()
        self.store = SQLiteQueryStore(self.root / "central.sqlite3", now_provider=lambda: NOW)
        self.store.initialize()
        self.addCleanup(self.store.close)
        self.client = NativeThemeClient(self.store)
        self.service = CentralContentSyncService(self.client)
        self.themes = ThemeRepository(self.main)
        self.themes.create_profile("original local")
        self.backup = ThemeBackupService(self.main, now_provider=lambda: NOW)

    def state(self):
        with closing(sqlite3.connect(self.main)) as connection:
            row = connection.execute("SELECT value FROM settings WHERE key=?", (ThemeBackupService.SYNC_STATE_KEY,)).fetchone()
        return json.loads(row[0]) if row else None

    def dispatcher(self):
        owner = CentralThemeSyncDispatcher(self.service, self.main, pending_path=self.root / ".pending")
        self.addCleanup(owner.close)
        return owner

    def peer(self, name="remote next"):
        previous = self.store.load_accepted_theme_snapshot()
        document = deepcopy(previous["document"])
        document["profiles"].append({"name": name, "themes": [], "stock_themes": [],
                                     "theme_name_decisions": [], "theme_suggestions": []})
        return self.store.publish_theme_snapshot(document, publication_id=name,
            expected_snapshot_id=previous["snapshot_id"], origin_device="peer", require_current_projection=True)["snapshot"]

    def test_fresh_publication_uses_one_http_command_and_original_receipt_without_split_writes(self):
        with patch.object(ThemeBackupService, "export_document", autospec=True,
                          side_effect=ThemeBackupService.export_document) as export:
            result = self.service.replace_themes(self.main)
        self.assertEqual(1, export.call_count)
        self.assertEqual(1, result.theme_metadata)
        self.assertEqual(["PUT"], [call[0] for call in self.client.calls])
        accepted = self.store.load_accepted_theme_snapshot()
        self.assertEqual(self.state()["ack"]["snapshot"], accepted)
        self.assertIsNone(self.state()["prepared"])
        self.assertEqual("2026-10-23T10:30:00+09:00", accepted["document"]["created_at"])
        self.client.calls.clear()
        self.assertEqual(0, CentralContentSyncService(self.client).replace_themes(self.main).total)
        self.assertEqual(["GET"], [call[0] for call in self.client.calls])
        self.assertEqual(accepted, self.store.load_accepted_theme_snapshot())
        self.assertEqual(1, len(self.store.load_theme_snapshots()))

    def test_ack_loss_restart_recovers_exact_pinned_receipt_after_peer_without_rewinding_remote(self):
        self.client.lose_ack = True
        with self.assertRaisesRegex(RuntimeError, "ACK loss"):
            self.service.replace_themes(self.main)
        prepared = self.state()["prepared"]
        first = self.store.load_accepted_theme_snapshot(snapshot_id=prepared["publication_id"])
        peer = self.peer()
        owner = self.dispatcher()
        self.assertTrue(owner.flush_pending(force=True))
        self.assertEqual(first, self.state()["ack"]["snapshot"])
        self.assertEqual(peer, self.store.load_accepted_theme_snapshot())
        self.assertFalse(owner.has_pending)
        self.assertEqual(prepared["publication_id"], self.client.calls[-1][1]["publication_id"])
        with patch.object(owner, "_schedule_locked"):
            self.assertFalse(owner.flush_pending(force=True))
        self.assertNotIn("remote next", {p["name"] for p in self.backup.export_document()["profiles"]})
        self.assertEqual(first, self.state()["ack"]["snapshot"])
        self.assertTrue(owner.has_pending)

    def test_edit_during_publish_keeps_pending_and_next_generation_uses_accepted_predecessor(self):
        owner = self.dispatcher()
        self.client.after_publish = lambda: self.themes.create_profile("during send")
        with patch.object(owner, "_schedule_locked"):
            self.assertFalse(owner.flush_pending(force=True))
        first = self.store.load_accepted_theme_snapshot()
        self.assertNotIn("during send", {p["name"] for p in first["document"]["profiles"]})
        self.assertTrue(owner.has_pending)
        self.assertTrue(owner.flush_pending())
        second = self.store.load_accepted_theme_snapshot()
        self.assertEqual(first["snapshot_id"], second["revision_of"])
        self.assertIn("during send", {p["name"] for p in second["document"]["profiles"]})
        self.assertFalse(owner.has_pending)

    def test_dirty_local_and_peer_divergence_preserve_prepared_and_local_without_winner(self):
        self.service.replace_themes(self.main)
        peer = self.peer()
        self.themes.create_profile("unsent edit")
        owner = self.dispatcher()
        with patch.object(owner, "_schedule_locked"):
            self.assertFalse(owner.flush_pending(force=True))
        self.assertTrue(owner.has_pending)
        self.assertIsNotNone(self.state()["prepared"])
        pinned = deepcopy(self.state()["prepared"])
        self.themes.create_profile("newer local edit")
        with patch.object(owner, "_schedule_locked"):
            self.assertFalse(owner.flush_pending())
        self.assertEqual(pinned, self.state()["prepared"])
        self.assertEqual(peer, self.store.load_accepted_theme_snapshot())
        self.assertIn("newer local edit", {p["name"] for p in self.backup.export_document()["profiles"]})

    def test_remote_receive_local_edit_and_import_failure_do_not_ack_or_lose_pending(self):
        for failure in ("concurrent_edit", "native_failure"):
            with self.subTest(failure=failure):
                if failure == "concurrent_edit":
                    self.service.replace_themes(self.main)
                    self.peer()
                    self.client.before_receive = lambda: self.themes.create_profile("edit during GET")
                else:
                    # Restore a known clean native/local baseline for this independent failure.
                    state = self.state()
                    with closing(sqlite3.connect(self.main)) as connection, connection:
                        connection.execute("DELETE FROM settings WHERE key=?", (ThemeBackupService.SYNC_STATE_KEY,))
                    self.store.replace_documents("theme_metadata", [])
                    self.store.replace_documents("theme_profile", [])
                    self.store.replace_documents("theme_stock", [])
                    # A fresh server is separate from remote reset policy.
                    self.store.close()
                    self.store = SQLiteQueryStore(self.root / "second-central.sqlite3", now_provider=lambda: NOW)
                    self.store.initialize(); self.addCleanup(self.store.close); self.client.store = self.store
                    self.service.replace_themes(self.main)
                    self.peer("remote after native failure")
                before = self.state()
                owner = CentralThemeSyncDispatcher(ConfirmedThemeReceiveService(self.client), self.main,
                                                    pending_path=self.root / ".pending")
                self.addCleanup(owner.close)
                if failure == "native_failure":
                    def fail(backup, connection, document):
                        connection.execute("DELETE FROM profile_themes")
                        raise ThemeBackupError("controlled import failure")
                    context = patch.object(ThemeBackupService, "_apply_all_profiles", fail)
                else:
                    context = nullcontext()
                with patch.object(owner, "_schedule_locked"), context:
                    self.assertFalse(owner.flush_pending(force=True))
                self.assertEqual(before, self.state())
                self.assertTrue(owner.has_pending)
                self.assertNotIn("remote after native failure", {p["name"] for p in self.backup.export_document()["profiles"]})
                if failure == "concurrent_edit":
                    self.assertIn("edit during GET", {p["name"] for p in self.backup.export_document()["profiles"]})
                owner.close()

    def test_unbound_existing_remote_is_preserved_without_adoption(self):
        original = self.store.publish_theme_snapshot(theme_document(), publication_id="existing", expected_snapshot_id=None)["snapshot"]
        owner = self.dispatcher()
        with patch.object(owner, "_schedule_locked"):
            self.assertFalse(owner.flush_pending(force=True))
        self.assertEqual(original, self.store.load_accepted_theme_snapshot())
        self.assertIsNotNone(self.state()["prepared"])
        self.assertTrue(owner.has_pending)
        self.assertIn("original local", {p["name"] for p in self.backup.export_document()["profiles"]})

    def test_coherent_legacy_successor_is_not_adopted_as_native_even_with_matching_lineage(self):
        self.service.replace_themes(self.main)
        first = self.store.load_accepted_theme_snapshot()
        document = deepcopy(first["document"])
        document["aliases"].append({"alias": "legacy change", "code": "005930"})
        self.store.upsert_documents("theme_metadata", [{"owner": "default", "key": "full", "document": document}])
        legacy = self.store.load_accepted_theme_snapshot(require_current_projection=True)
        self.assertEqual(first["snapshot_id"], legacy["revision_of"])
        before = self.state()
        with self.assertRaisesRegex(CentralThemeConflict, "remote_generation_requires_reconciliation"):
            self.service.replace_themes(self.main)
        self.assertEqual(before, self.state())
        self.assertEqual(legacy, self.store.load_accepted_theme_snapshot())
        self.assertNotIn({"alias": "legacy change", "code": "005930"}, self.backup.export_document()["aliases"])

    def test_remote_reset_keeps_local_confirmed_receipt_and_pending(self):
        self.service.replace_themes(self.main)
        before = self.state()
        empty = SQLiteQueryStore(self.root / "reset-central.sqlite3", now_provider=lambda: NOW)
        empty.initialize(); self.addCleanup(empty.close); self.client.store = empty
        owner = self.dispatcher()
        with patch.object(owner, "_schedule_locked"):
            self.assertFalse(owner.flush_pending(force=True))
        self.assertEqual(before, self.state())
        self.assertTrue(owner.has_pending)
        self.assertIsNone(empty.load_accepted_theme_snapshot())
        self.assertIn("original local", {p["name"] for p in self.backup.export_document()["profiles"]})

    def test_generic_push_pull_and_seed_never_read_or_ack_theme_material(self):
        self.service.replace_themes(self.main)
        before = self.state()
        self.client.calls.clear()
        connect = sqlite3.connect
        local_reads = []
        def observed(path, *args, **kwargs):
            if Path(path) == self.main: local_reads.append(path)
            return connect(path, *args, **kwargs)
        with patch.object(sqlite3, "connect", side_effect=observed):
            self.service.push(self.main, self.root / "missing-news.sqlite3")
            self.service.pull(self.main, self.root / "missing-news.sqlite3")
            self.service.seed_push_manifest(self.main, self.root / "missing-news.sqlite3")
        self.assertEqual([], local_reads)
        self.assertFalse(any(len(c) > 1 and isinstance(c[1], str) and c[1].startswith("theme_") for c in self.client.calls))
        self.assertEqual(before, self.state())
        self.assertEqual(1, len(self.store.load_theme_snapshots()))

    def test_invalid_backup_apply_fails_before_cursor_or_receipt_can_advance(self):
        self.service.replace_themes(self.main)
        before = self.state()
        revision = self.backup.revision()
        for expected, collections in ((None, {"theme_metadata": []}),
                                      (revision, {"theme_metadata": [{"document": {"broken": True}}]})):
            with self.subTest(expected=expected), self.assertRaises(ThemeBackupError):
                self.service.apply_theme_snapshot(self.main, collections, expected_revision=expected)
        self.assertEqual(before, self.state())
        self.assertFalse((self.root / "central_content_cursor.json").exists())

    def test_close_drains_actual_prepared_http_and_ack_boundaries_without_restarting(self):
        for stage in ("prepared", "http", "ack"):
            with self.subTest(stage=stage):
                self.themes.create_profile("close boundary " + stage)
                owner = self.dispatcher()
                entered, release, closing, closed = (threading.Event() for _ in range(4))
                outcomes, errors = [], []
                def barrier():
                    entered.set()
                    if not release.wait(4): raise AssertionError("actual theme publication was not released")
                if stage == "http":
                    self.client.after_publish = barrier
                    context = nullcontext()
                else:
                    name = "prepare_theme_publication" if stage == "prepared" else "acknowledge_theme_publication"
                    actual = getattr(ThemeBackupService, name)
                    def guarded(backup, *args, **kwargs):
                        value = actual(backup, *args, **kwargs)
                        barrier()
                        return value
                    context = patch.object(ThemeBackupService, name, guarded)
                def run():
                    try: outcomes.append(owner.flush_pending(force=True))
                    except BaseException as error: errors.append(error)
                def close():
                    closing.set()
                    try: owner.close(); closed.set()
                    except BaseException as error: errors.append(error)
                worker, closer = threading.Thread(target=run), threading.Thread(target=close)
                with context:
                    try:
                        worker.start(); self.assertTrue(entered.wait(3))
                        closer.start(); self.assertTrue(closing.wait(1))
                        self.assertFalse(closed.wait(0.15))
                    finally:
                        release.set(); worker.join(5)
                        if closer.ident is not None: closer.join(5)
                        owner.close()
                self.assertFalse(worker.is_alive()); self.assertFalse(closer.is_alive())
                self.assertEqual([], errors); self.assertEqual([True], outcomes)
                self.assertTrue(closed.is_set()); self.assertFalse(owner.has_pending)
                self.assertEqual(self.store.load_accepted_theme_snapshot(), self.state()["ack"]["snapshot"])
                before = len(self.client.calls)
                owner.notify()
                self.assertFalse(owner.flush_pending(force=True))
                self.assertEqual(before, len(self.client.calls))

    def test_selective_current_state_seed_recovers_original_pending_receipt_after_peer_and_keeps_later_edit_pending(self):
        from scripts import preserve_current_settings as operator

        # Reuse the native client/store fixture; only the existing owner can ACK.
        self.backup.import_document(theme_document())
        settings = Database(self.main, now_provider=lambda: NOW).settings
        settings.set_many({"theme_active_profile": "기본 테마", "rank_query_type": "5"})
        self.service.replace_themes(self.main)
        predecessor = self.store.load_accepted_theme_snapshot()
        self.themes.create_profile("source current edit")
        source_owner = self.dispatcher()
        self.client.lose_ack = True
        with patch.object(source_owner, "_schedule_locked"):
            self.assertFalse(source_owner.flush_pending(force=True))
        source_owner.close()  # No accepted run/timer may mutate the source cut.
        self.assertTrue(source_owner.has_pending)
        pending_marker = (self.root / ".pending").read_bytes()
        with closing(sqlite3.connect(self.main)) as connection:
            original_raw = connection.execute("SELECT value FROM settings WHERE key=?",
                                              (ThemeBackupService.SYNC_STATE_KEY,)).fetchone()[0]
        original_state = json.loads(original_raw)
        prepared = deepcopy(original_state["prepared"])
        self.assertIsNotNone(prepared)
        self.assertEqual(predecessor, original_state["ack"]["snapshot"])
        self.assertEqual(predecessor["snapshot_id"], prepared["expected_snapshot_id"])
        first = self.store.load_accepted_theme_snapshot(snapshot_id=prepared["publication_id"])
        self.assertEqual(prepared["publication_id"], first["snapshot_id"])
        self.assertEqual(prepared["content_hash"], first["content_hash"])
        self.assertEqual(prepared["document"], first["document"])
        self.assertEqual(predecessor["snapshot_id"], first["revision_of"])
        # An original version is part of this fixture, not regenerated by seed.
        settings.set(ThemeBackupService.SYNC_STATE_KEY, original_raw)
        with closing(sqlite3.connect(self.main)) as connection:
            original_version = connection.execute(
                "SELECT updated_at FROM central_setting_versions WHERE setting_key=?",
                (ThemeBackupService.SYNC_STATE_KEY,)).fetchone()[0]
        self.assertEqual(NOW.isoformat(), original_version)
        peer = self.peer()
        generations = self.store.load_theme_snapshots()
        self.assertEqual(3, len(generations))

        artifact, target = self.root / "pending-cut", self.root / "pending-query.sqlite3"
        source_bytes = self.main.read_bytes()
        captured = operator.capture_current_state(database=self.main,
            source_instance="fixture-source", source_owner="fixture-theme-owner",
            operation_id="fixture-pending-cut", output=artifact,
            sqlite_frozen=True, artifact_now=NOW)
        self.assertFalse(captured["applied"])
        manifest, records, digest = operator.verified_current_state_rows(artifact)
        current_keys = {"theme_active_profile", "rank_query_type", ThemeBackupService.SYNC_STATE_KEY}
        theme_relations = set(operator.CURRENT_STATE_TABLES) - set(operator.TABLES)
        entries = []
        for key, item in records.items():
            relation = item["relation"]
            row = tuple(operator.decode_value(value) for value in item["row"])
            chosen = (relation in theme_relations or
                      relation in {"settings", "central_setting_versions"} and row[0] in current_keys)
            entries.append(dict(record_id=key, placement="TRANSFER" if chosen else "RETAIN_SOURCE",
                                **({"target_role": "query-local"} if chosen else {})))
        selection = dict(format="current-state-selection/v1",
            source_instance=manifest["source_instance"], source_owner=manifest["source_owner"],
            operation_id=manifest["operation_id"], manifest_sha256=digest,
            target_role="query-local", records=entries)
        selected = operator.select_current_state_rows(artifact, selection)
        self.assertEqual("CLOSED", selected["closure"]["state"])
        self.assertEqual(0, selected["closure"]["summary"]["blocking"])
        self.assertGreater(selected["binding"]["retained_source"], 0)
        self.assertEqual(current_keys, set(dict(selected["rows"]["settings"])))
        self.assertEqual(original_raw, dict(selected["rows"]["settings"])[ThemeBackupService.SYNC_STATE_KEY])
        self.assertEqual(original_version,
                         dict(selected["rows"]["central_setting_versions"])[ThemeBackupService.SYNC_STATE_KEY])
        self.assertEqual("NOT_EVALUATED", selected["consumer_status"])
        self.assertEqual("OFFLINE_UNBOUND", selected["runtime_state"])
        self.assertFalse(selected["applied"])
        self.assertFalse(selected["order_authority"])

        seeded = operator.install_current_state(artifact=artifact, selection=selection, target=target,
            operation_id="fixture-pending-install", target_id="fixture-pending-target", receipt_now=NOW)
        self.assertEqual("SELECTED_USER_STATE", seeded["seed_kind"])
        self.assertEqual("OFFLINE_UNBOUND", seeded["runtime_state"])
        self.assertFalse(seeded["role_ready"])
        self.assertFalse(seeded["applied"])
        self.assertFalse(seeded["order_authority"])
        self.assertEqual("NOT_EVALUATED", seeded["consumer_status"])
        # Installer has closed its connection; reopen through the existing owner.
        reopened = Database(target).initialize_query_role(role="query-local")
        self.assertEqual(seeded["receipt"], reopened["receipt"])
        with closing(sqlite3.connect(target)) as connection:
            for relation, columns in operator.CURRENT_STATE_TABLES.items():
                self.assertEqual(selected["rows"][relation], connection.execute(
                    f"SELECT {','.join(columns)} FROM {relation} ORDER BY rowid").fetchall())
            self.assertEqual(original_raw, connection.execute("SELECT value FROM settings WHERE key=?",
                (ThemeBackupService.SYNC_STATE_KEY,)).fetchone()[0])
            self.assertEqual(original_version, connection.execute(
                "SELECT updated_at FROM central_setting_versions WHERE setting_key=?",
                (ThemeBackupService.SYNC_STATE_KEY,)).fetchone()[0])

        # CLOSED above describes raw row references. It does not ACK this task,
        # or make a later target edit part of the original prepared document.
        target_themes = ThemeRepository(target, now_provider=lambda: NOW)
        target_themes.create_profile("later target edit")
        target_backup = ThemeBackupService(target, now_provider=lambda: NOW)
        self.assertIn("later target edit", {p["name"] for p in target_backup.export_document()["profiles"]})
        self.assertNotIn("later target edit", {p["name"] for p in prepared["document"]["profiles"]})
        target_service = CentralContentSyncService(self.client)
        self.assertEqual(original_state["server_scope"], self.client.theme_sync_scope)
        target_owner = CentralThemeSyncDispatcher(target_service, target,
                                                 pending_path=self.root / ".target-pending")
        self.addCleanup(target_owner.close)
        calls_before = len(self.client.calls)
        with patch.object(target_owner, "_schedule_locked"):
            self.assertFalse(target_owner.flush_pending(force=True))
        target_owner.close()
        self.assertTrue(target_owner.has_pending)
        self.assertEqual([("PUT", {"publication_id": prepared["publication_id"],
            "expected_snapshot_id": prepared["expected_snapshot_id"],
            "effective_at": prepared["effective_at"], "origin_device": prepared["origin_device"]},
            prepared["document"])], self.client.calls[calls_before:])
        self.assertEqual(prepared["publication_id"], self.client.calls[-1][1]["publication_id"])
        self.assertEqual(prepared["expected_snapshot_id"], self.client.calls[-1][1]["expected_snapshot_id"])
        self.assertEqual(prepared["effective_at"], self.client.calls[-1][1]["effective_at"])
        self.assertEqual(prepared["origin_device"], self.client.calls[-1][1]["origin_device"])
        with closing(sqlite3.connect(target)) as connection:
            recovered = json.loads(connection.execute("SELECT value FROM settings WHERE key=?",
                (ThemeBackupService.SYNC_STATE_KEY,)).fetchone()[0])
        self.assertIsNone(recovered["prepared"])
        self.assertEqual(first, recovered["ack"]["snapshot"])
        self.assertEqual(prepared["local_revision"], recovered["ack"]["local_revision"])
        self.assertEqual(prepared["content_hash"], recovered["ack"]["local_content_hash"])
        self.assertEqual(peer, self.store.load_accepted_theme_snapshot())
        self.assertEqual(generations, self.store.load_theme_snapshots())
        self.assertIn("later target edit", {p["name"] for p in target_backup.export_document()["profiles"]})
        self.assertEqual(seeded["receipt"], Database(target).initialize_query_role(role="query-local")["receipt"])
        self.assertEqual(source_bytes, self.main.read_bytes())
        self.assertEqual(original_state, self.state())
        self.assertEqual(pending_marker, (self.root / ".pending").read_bytes())

    def test_full_settings_backup_preserves_unfinished_publication_recovery_state(self):
        self.client.lose_ack = True
        with self.assertRaises(RuntimeError): self.service.replace_themes(self.main)
        pending = self.state()
        backup_path = self.root / "full-backup.json"
        SettingsBackupService(self.main).export_to(backup_path)
        restored = self.root / "restored.sqlite3"
        Database(restored).initialize()
        SettingsBackupService(restored).import_from(backup_path)
        with closing(sqlite3.connect(restored)) as connection:
            raw = connection.execute("SELECT value FROM settings WHERE key=?", (ThemeBackupService.SYNC_STATE_KEY,)).fetchone()[0]
        self.assertEqual(pending, json.loads(raw))
        recovered = CentralContentSyncService(self.client).replace_themes(restored)
        self.assertFalse(recovered.pending_collections)
        self.assertEqual(1, len(self.store.load_theme_snapshots()))

    def test_malformed_recovery_state_and_server_scope_change_never_overwrite_pending(self):
        for raw in ('{"schema":1,"server_scope":"https://native-theme.test","ack":{},"prepared":null}', '{"broken":true}'):
            with closing(sqlite3.connect(self.main)) as connection, connection:
                connection.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (ThemeBackupService.SYNC_STATE_KEY, raw))
            with self.assertRaises(ThemeBackupError): self.service.replace_themes(self.main)
            with closing(sqlite3.connect(self.main)) as connection:
                self.assertEqual(raw, connection.execute("SELECT value FROM settings WHERE key=?", (ThemeBackupService.SYNC_STATE_KEY,)).fetchone()[0])
        self.assertEqual([], self.client.calls)


class ThemeGenerationApiTests(unittest.TestCase):
    def test_actual_authenticated_client_api_native_receipt_conflict_and_clock(self):
        import httpx
        from kiwoom_monitor.central_server.app import create_app
        from tests.unit.test_central_server_app import content_contract_app
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            Database(root / "monitor.sqlite3").initialize()
            with content_contract_app(create_app, root / "central.sqlite3") as (app, store, _clock):
                calls = []
                async def request(req):
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                        return await client.request(req.method, urlsplit(req.full_url).path +
                            ("?" + urlsplit(req.full_url).query if urlsplit(req.full_url).query else ""),
                            headers=dict(req.header_items()), content=req.data)
                def opener(req, **kwargs):
                    calls.append((req.method, urlsplit(req.full_url).path, dict(req.header_items()), kwargs))
                    response = asyncio.run(request(req))
                    if response.status_code >= 400:
                        raise HTTPError(req.full_url, response.status_code, "ASGI", response.headers, io.BytesIO(response.content))
                    return Response(response.json(), response.headers)
                client = CentralContentClient("http://test", "private-token", opener=opener, timeout_seconds=23)
                service = CentralContentSyncService(client)
                self.assertEqual(1, service.replace_themes(root / "monitor.sqlite3").theme_metadata)
                self.assertEqual([("PUT", "/api/v1/themes/snapshot")], [(c[0], c[1]) for c in calls])
                self.assertEqual("Bearer private-token", calls[0][2]["Authorization"])
                self.assertEqual("1", calls[0][2]["X-kiwoom-clock"])
                self.assertEqual(23, calls[0][3]["timeout"])
                self.assertIsNotNone(client._server_clock)
                receipt = client.load_accepted_theme_snapshot()
                self.assertEqual(store.load_accepted_theme_snapshot(), receipt)
                store.upsert_documents("theme_profile", [{"owner": "legacy", "key": "late", "document": {"original": 1}}])
                with self.assertRaises(CentralThemeConflict) as caught: client.load_accepted_theme_snapshot()
                self.assertEqual(409, caught.exception.status_code)
                self.assertIn("current_projection_conflict", caught.exception.detail)
                self.assertEqual(receipt, client.load_accepted_theme_snapshot(snapshot_id=receipt["snapshot_id"]))
                with self.assertRaisesRegex(RuntimeError, "HTTP 401"):
                    CentralContentClient("http://test", "wrong", opener=opener).load_accepted_theme_snapshot()
                # Match native TEXT identity/device fields; impose no new 200-character policy.
                store.replace_documents("theme_profile", [row for row in store.load_documents("theme_profile")
                    if row["owner"] != "legacy"])
                long_id, long_device = "publication-" + "x" * 250, "device-" + "y" * 250
                next_document = deepcopy(receipt["document"])
                next_document["aliases"].append({"alias": "identity boundary", "code": "005930"})
                accepted = client.publish_theme_snapshot(next_document, publication_id=long_id,
                    expected_snapshot_id=receipt["snapshot_id"], effective_at=receipt["effective_at"], origin_device=long_device)
                self.assertEqual(long_id, accepted["snapshot"]["snapshot_id"])
                self.assertEqual(long_device, accepted["snapshot"]["origin_device"])
                self.assertEqual(accepted["snapshot"], client.load_accepted_theme_snapshot(snapshot_id=long_id))

    def test_cancelled_publication_drains_actual_native_commit_and_original_receipt(self):
        import httpx
        from kiwoom_monitor.central_server.app import create_app
        from kiwoom_monitor.central_server import database_documents
        from tests.unit.test_central_server_app import content_contract_app
        with tempfile.TemporaryDirectory() as temporary:
            with content_contract_app(create_app, Path(temporary) / "central.sqlite3") as (app, store, _clock):
                entered, release = threading.Event(), threading.Event()
                connections = []
                connect, publish = type(store)._connect, database_documents._publish_theme_snapshot
                def observed_connect(owner):
                    connection = connect(owner); connections.append(connection); return connection
                def blocked_publish(*args, **kwargs):
                    result = publish(*args, **kwargs)
                    entered.set()
                    if not release.wait(5): raise AssertionError("publication commit was not released")
                    return result
                async def scenario():
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                        task = asyncio.create_task(client.put("/api/v1/themes/snapshot",
                            headers={"Authorization": "Bearer private-token"}, json={"document": theme_document(),
                            "publication_id": "cancelled-publication", "expected_snapshot_id": None,
                            "effective_at": None, "origin_device": "pc-a"}))
                        try:
                            self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                            for _ in range(2):
                                self.assertTrue(task.cancel())
                                done, _ = await asyncio.wait({task}, timeout=0.05)
                                self.assertEqual(set(), done)
                            release.set()
                            with self.assertRaises(asyncio.CancelledError): await task
                            self.assertEqual(1, len(connections))
                            with self.assertRaises(sqlite3.ProgrammingError): connections[0].execute("SELECT 1")
                        finally:
                            release.set()
                            if not task.done(): task.cancel()
                            try: await task
                            except asyncio.CancelledError: pass
                with patch.object(type(store), "_connect", observed_connect), patch.object(
                    database_documents, "_publish_theme_snapshot", blocked_publish):
                    asyncio.run(scenario())
                receipt = store.load_accepted_theme_snapshot(snapshot_id="cancelled-publication")
                self.assertEqual(theme_document(), receipt["document"])
                replay = store.publish_theme_snapshot(theme_document(), publication_id="cancelled-publication",
                    expected_snapshot_id=None, origin_device="pc-a", require_current_projection=True)
                self.assertEqual({"snapshot": receipt, "changed": False, "replayed": True}, replay)

    def test_unsupported_server_never_falls_back_to_three_collection_writes(self):
        calls = []
        def opener(req, **_kwargs):
            calls.append((req.method, req.full_url))
            raise HTTPError(req.full_url, 404, "old server", {}, io.BytesIO(b'{}'))
        client = CentralContentClient("http://old-server", "token", opener=opener)
        client.server_now = lambda: NOW
        with tempfile.TemporaryDirectory() as temporary:
            main = Path(temporary) / "monitor.sqlite3"
            Database(main).initialize()
            with self.assertRaisesRegex(RuntimeError, "HTTP 404"):
                CentralContentSyncService(client).replace_themes(main)
            self.assertEqual(1, len(calls))
            self.assertEqual("PUT", calls[0][0])
            with closing(sqlite3.connect(main)) as connection:
                state = json.loads(connection.execute("SELECT value FROM settings WHERE key=?", (ThemeBackupService.SYNC_STATE_KEY,)).fetchone()[0])
            self.assertIsNotNone(state["prepared"])
