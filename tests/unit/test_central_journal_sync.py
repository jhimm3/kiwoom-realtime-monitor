from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from copy import deepcopy
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

from kiwoom_monitor.infrastructure.central_journal_sync import (
    CentralJournalSyncRunner,
    CentralJournalSyncService,
)
from kiwoom_monitor.infrastructure.central_content_client import (
    CentralContentHttpError,
    CentralContentUnavailableError,
)
from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository
from kiwoom_monitor.infrastructure.persistence.journal_schema import initialize_journal_database
from kiwoom_monitor.infrastructure.persistence.journal_backup import JournalBackupService
from kiwoom_monitor.application.trade_history_service import TradeFill
from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope


class _Client:
    def __init__(self) -> None:
        self.remote: dict[str, list[dict[str, object]]] = {}

    def load_all(self, collection: str) -> list[dict[str, object]]:
        return list(self.remote.get(collection, []))

    def server_now(self) -> datetime:
        return datetime.fromisoformat("2030-01-02T09:01:30+09:00")

    def upsert(self, collection: str, documents: list[dict[str, object]]) -> int:
        self.remote.setdefault(collection, []).extend(documents)
        return len(documents)


class _SnapshotClient(_Client):
    """Detached HTTP snapshots with the server's unique owner/key storage."""

    def __init__(self) -> None:
        super().__init__()
        self.uploads: list[tuple[str, list[dict[str, object]]]] = []

    def load_all(self, collection: str) -> list[dict[str, object]]:
        return deepcopy(super().load_all(collection))

    def upsert(self, collection: str, documents: list[dict[str, object]]) -> int:
        self.uploads.append((collection, deepcopy(documents)))
        rows = {
            (value["owner"], value["key"]): value
            for value in self.remote.get(collection, [])
        }
        rows.update({(value["owner"], value["key"]): deepcopy(value) for value in documents})
        self.remote[collection] = list(rows.values())
        return len(documents)


def _settings_rows(path: Path) -> None:
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS journal_settings("
            "setting_key TEXT PRIMARY KEY,value_json TEXT,updated_at TEXT)"
        )
        connection.executemany("INSERT INTO journal_settings VALUES(?,?,?)", (
            (f"k{index}", f'"value-{index}"', "2026-09-13T09:00:00+09:00")
            for index in range(3)
        ))


class CentralJournalSyncServiceTests(unittest.TestCase):
    def test_same_round_snapshot_skips_unchanged_and_sends_only_full_document_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "journal.sqlite3"
            _settings_rows(path)
            client = _SnapshotClient()
            service = CentralJournalSyncService(client)  # type: ignore[arg-type]
            self.assertEqual(3, service.sync(path))
            self.assertEqual(0, service.sync(path))
            self.assertEqual(0, CentralJournalSyncService(client).sync(path))  # type: ignore[arg-type]
            self.assertEqual(1, len(client.uploads))

            # A content edit at the same revision is still an upload; a revision
            # alone is also part of the transmitted document, never excluded.
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("UPDATE journal_settings SET value_json=? WHERE setting_key='k1'", ('"edited"',))
            self.assertEqual(1, service.sync(path))
            self.assertEqual(["k1"], [row["key"] for row in client.uploads[-1][1]])
            self.assertEqual('"edited"', client.uploads[-1][1][0]["document"]["value_json"])
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("UPDATE journal_settings SET updated_at=? WHERE setting_key='k2'", (
                    "2026-09-13T09:01:00+09:00",
                ))
            self.assertEqual(1, service.sync(path))
            self.assertEqual(["k2"], [row["key"] for row in client.uploads[-1][1]])
            self.assertEqual("2026-09-13T09:01:00+09:00", client.uploads[-1][1][0]["document"]["updated_at"])
            self.assertEqual(0, service.sync(path))
            self.assertEqual({"journal.sqlite3"}, {item.name for item in Path(temporary).iterdir()})

    def test_wire_identity_and_every_json_document_value_are_compared(self) -> None:
        for difference in ("none", "owner", "key", "missing-field", "boolean-vs-integer"):
            with self.subTest(difference=difference), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "journal.sqlite3"
                with closing(sqlite3.connect(path)) as connection, connection:
                    connection.execute("CREATE TABLE journal_settings(setting_key TEXT PRIMARY KEY,value_json TEXT,updated_at TEXT,flag INTEGER)")
                    connection.execute("INSERT INTO journal_settings VALUES(?,?,?,?)", (
                        "setting", '"kept"', "2026-09-13T09:00:00+09:00", 0,
                    ))
                document = {"flag": 0, "updated_at": "2026-09-13T09:00:00+09:00", "value_json": '"kept"', "setting_key": "setting"}
                remote = {"owner": "default", "key": "setting", "updated_at": 123.0, "document": document}
                if difference in {"owner", "key"}:
                    remote[difference] = "other"
                elif difference == "missing-field":
                    del document["flag"]
                elif difference == "boolean-vs-integer":
                    document["flag"] = False
                client = _SnapshotClient()
                client.remote["journal_settings"] = [remote]
                self.assertEqual(int(difference != "none"), CentralJournalSyncService(client).sync(path))  # type: ignore[arg-type]
                if difference != "none":
                    self.assertEqual(("journal_settings", "default", "setting"), (
                        client.uploads[-1][0], client.uploads[-1][1][0]["owner"], client.uploads[-1][1][0]["key"],
                    ))
                    self.assertIs(type(client.uploads[-1][1][0]["document"]["flag"]), int)

    def test_partial_batch_and_lost_ack_refetch_before_retrying_only_missing_documents(self) -> None:
        for lost_ack in (False, True):
            with self.subTest(lost_ack=lost_ack), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "journal.sqlite3"
                _settings_rows(path)

                class FailingClient(_SnapshotClient):
                    attempts = 0

                    def upsert(self, collection, documents):
                        self.attempts += 1
                        if self.attempts == 2:
                            if lost_ack:
                                super().upsert(collection, documents)
                            raise RuntimeError("lost ACK" if lost_ack else "batch rejected")
                        return super().upsert(collection, documents)

                client = FailingClient()
                with self.assertRaisesRegex(RuntimeError, "lost ACK|batch rejected"):
                    CentralJournalSyncService(client, batch_size=1).sync(path)  # type: ignore[arg-type]
                completed = len(client.uploads)
                self.assertEqual(1 if lost_ack else 2, CentralJournalSyncService(client, batch_size=1).sync(path))  # type: ignore[arg-type]
                self.assertEqual(["k2"] if lost_ack else ["k1", "k2"], [
                    rows[0]["key"] for _, rows in client.uploads[completed:]
                ])
                self.assertEqual({"k0", "k1", "k2"}, {row["key"] for row in client.remote["journal_settings"]})
                self.assertEqual(0, CentralJournalSyncService(client).sync(path))  # type: ignore[arg-type]

    def test_edit_during_upload_survives_old_snapshot_and_is_sent_on_next_sync(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "journal.sqlite3"
            _settings_rows(path)

            class EditingClient(_SnapshotClient):
                def upsert(self, collection, documents):
                    saved = super().upsert(collection, documents)
                    if len(self.uploads) == 1:
                        with closing(sqlite3.connect(path)) as connection, connection:
                            connection.execute("UPDATE journal_settings SET value_json=?,updated_at=? WHERE setting_key='k0'", (
                                '"during-upload"', "2026-09-13T09:02:00+09:00",
                            ))
                    return saved

            client = EditingClient()
            service = CentralJournalSyncService(client, batch_size=1)  # type: ignore[arg-type]
            self.assertEqual(3, service.sync(path))
            self.assertEqual('"value-0"', client.remote["journal_settings"][0]["document"]["value_json"])
            self.assertEqual(1, service.sync(path))
            self.assertEqual("k0", client.uploads[-1][1][0]["key"])
            self.assertEqual('"during-upload"', client.uploads[-1][1][0]["document"]["value_json"])
            self.assertEqual(0, service.sync(path))

    def test_legacy_and_v2_delete_state_snapshots_skip_only_unchanged_scoped_documents(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "journal.sqlite3"
            repository = JournalRepository(path)
            real = AccountScope("kiwoom", AccountEnvironment.REAL, "11111111-1111-4111-8111-111111111111")
            mock = AccountScope("kiwoom", AccountEnvironment.MOCK, "22222222-2222-4222-8222-222222222222")
            with closing(sqlite3.connect(path)) as connection, connection:
                repository._record_sync_state(connection, "journal_group_overrides", "same", True, "2026-09-13T09:00:00+09:00")
                for scope in (real, mock):
                    repository._record_sync_state(connection, "journal_group_overrides", "same", True, "2026-09-13T09:00:00+09:00", scope)

            class V2Client(_SnapshotClient):
                def capabilities(self):
                    return {"journal_v2_sync": True}

            client = V2Client()
            self.assertEqual(3, CentralJournalSyncService(client).sync(path))  # type: ignore[arg-type]
            self.assertEqual({("journal_sync_states", "legacy"), ("journal_v2_sync_states", real.account_ref), ("journal_v2_sync_states", mock.account_ref)}, {
                (collection, row["owner"]) for collection, rows in client.uploads for row in rows
            })
            self.assertEqual(0, CentralJournalSyncService(client).sync(path))  # type: ignore[arg-type]
            with closing(sqlite3.connect(path)) as connection, connection:
                repository._record_sync_state(connection, "journal_group_overrides", "same", True, "2026-09-13T09:01:00+09:00", real)
            self.assertEqual(1, CentralJournalSyncService(client).sync(path))  # type: ignore[arg-type]
            self.assertEqual("journal_v2_sync_states", client.uploads[-1][0])
            self.assertEqual([real.account_ref], [row["owner"] for row in client.uploads[-1][1]])
            self.assertTrue(client.uploads[-1][1][0]["document"]["is_deleted"])
            self.assertEqual(0, CentralJournalSyncService(client).sync(path))  # type: ignore[arg-type]

    def test_restored_database_refetches_newer_remote_without_persistent_ack_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "journal.sqlite3"
            JournalRepository(path)
            _settings_rows(path)
            backup = Path(temporary) / "before.sqlite3"
            JournalBackupService(path).export_to(backup)
            client = _SnapshotClient()
            self.assertEqual(3, CentralJournalSyncService(client).sync(path))  # type: ignore[arg-type]
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("UPDATE journal_settings SET value_json=?,updated_at=? WHERE setting_key='k1'", (
                    '"newer"', "2026-09-13T09:01:00+09:00",
                ))
            self.assertEqual(1, CentralJournalSyncService(client).sync(path))  # type: ignore[arg-type]
            JournalBackupService(path).import_from(backup)
            # Importing a newer setting creates the existing non-deleted sync
            # state. Send that new state once, while the setting itself has no echo.
            self.assertEqual(1, CentralJournalSyncService(client).sync(path))  # type: ignore[arg-type]
            self.assertEqual(("journal_sync_states", [{
                "owner": "legacy", "key": "k1", "document": {
                    "collection": "journal_settings", "owner": "legacy", "document_key": "k1",
                    "origin_broker": "legacy", "origin_environment": "unknown",
                    "origin_account_ref": "legacy-unassigned", "canonical_account_ref": "legacy-unassigned",
                    "is_deleted": False, "updated_at": "2026-09-13T09:01:00+09:00",
                },
            }]), client.uploads[-1])
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(('"newer"', "2026-09-13T09:01:00+09:00"), connection.execute(
                    "SELECT value_json,updated_at FROM journal_settings WHERE setting_key='k1'",
                ).fetchone())
            self.assertEqual(0, CentralJournalSyncService(client).sync(path))  # type: ignore[arg-type]
            self.assertEqual(3, len(client.uploads))

    def test_missing_v2_state_keeps_legacy_delta_and_pending_capability(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "journal.sqlite3"
            repository = JournalRepository(path)
            repository.assign_group(("fill-1",), "manual:local")

            class OldV2Client(_SnapshotClient):
                def capabilities(self):
                    return {"journal_v2_sync": True}

                def load_all(self, collection):
                    if collection == "journal_v2_sync_states":
                        raise CentralContentHttpError(404, "unsupported")
                    return super().load_all(collection)

            client = OldV2Client()
            service = CentralJournalSyncService(client)  # type: ignore[arg-type]
            self.assertEqual(2, service.sync(path))
            self.assertEqual(("journal_v2_sync_states",), service.pending_collections)
            self.assertEqual(0, service.sync(path))
            self.assertEqual(("journal_v2_sync_states",), service.pending_collections)
            self.assertTrue(all(not collection.startswith("journal_v2_") for collection, _ in client.uploads))

    def test_legacy_import_receipt_uses_client_clock_and_seen_revision_keeps_original_receipt(self) -> None:
        fixed = datetime.fromisoformat("2030-01-02T09:01:30+09:00")
        for supplied in (fixed, fixed.replace(tzinfo=None), fixed.astimezone(timezone.utc)):
            with self.subTest(supplied=supplied), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "journal.sqlite3"
                JournalRepository(path)
                client = _Client()
                clock = Mock(return_value=supplied)
                client.server_now = clock
                client.remote["journal_group_overrides"] = [{
                    "owner": "default", "key": "fill-1", "updated_at": 123.0,
                    "document": {"fill_key": "fill-1", "group_id": "manual:one",
                                 "updated_at": "2026-09-13T10:00:00"},
                }]
                service = CentralJournalSyncService(client)  # type: ignore[arg-type]
                service.sync(path)
                clock.assert_called_once_with()
                with closing(sqlite3.connect(path)) as connection:
                    expected = (
                        "journal_group_overrides", "default", "fill-1",
                        "c80abef4114403b0f135160ee1fb3314b0b77898a41ef472d167bceb6de2edcc",
                        "123.0", "2026-09-13T10:00:00", "journal_group_overrides",
                        "fill-1", "IMPORTED", fixed.isoformat(),
                    )
                    self.assertEqual([expected], connection.execute(
                        "SELECT source_collection,source_owner,source_key,source_content_hash,"
                        "source_observed_at,source_modified_at,target_collection,target_key,status,"
                        "imported_at FROM journal_legacy_imports").fetchall())
                    self.assertEqual(("manual:one", "2026-09-13T10:00:00"), connection.execute(
                        "SELECT group_id,updated_at FROM trade_group_overrides").fetchone())
                clock.reset_mock()
                clock.side_effect = AssertionError("seen source must retain its receipt")
                service.sync(path)
                clock.assert_not_called()
                with closing(sqlite3.connect(path)) as connection:
                    self.assertEqual([(fixed.isoformat(),)], connection.execute(
                        "SELECT imported_at FROM journal_legacy_imports").fetchall())

    def test_legacy_import_receipt_failure_rolls_back_target_then_retry_uses_client_clock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "journal.sqlite3"
            JournalRepository(path)
            client = _Client()
            client.server_now = Mock(side_effect=RuntimeError("clock failure"))
            client.remote["journal_group_overrides"] = [{
                "owner": "default", "key": "fill-1",
                "document": {"fill_key": "fill-1", "group_id": "manual:one",
                             "updated_at": "2026-09-13T10:00:00"},
            }]
            service = CentralJournalSyncService(client)  # type: ignore[arg-type]
            with self.assertRaisesRegex(RuntimeError, "clock failure"):
                service.sync(path)
            with closing(sqlite3.connect(path)) as connection:
                for table in ("trade_group_overrides", "journal_legacy_imports"):
                    self.assertEqual(0, connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            client.server_now.side_effect = None
            client.server_now.return_value = datetime.fromisoformat("2030-01-02T09:01:30+09:00")
            service.sync(path)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(("2030-01-02T09:01:30+09:00",), connection.execute(
                    "SELECT imported_at FROM journal_legacy_imports").fetchone())
                self.assertEqual(("manual:one",), connection.execute(
                    "SELECT group_id FROM trade_group_overrides").fetchone())

    def test_same_document_key_real_and_mock_tombstones_coexist(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "journal.sqlite3"
            repository = JournalRepository(path)
            real = AccountScope("kiwoom", AccountEnvironment.REAL, "11111111-1111-4111-8111-111111111111")
            mock = AccountScope("kiwoom", AccountEnvironment.MOCK, "22222222-2222-4222-8222-222222222222")
            connection = sqlite3.connect(path)
            with connection:
                repository._record_sync_state(
                    connection, "journal_group_overrides", "same", True, "2026-09-13T10:00:00", real,
                )
                repository._record_sync_state(
                    connection, "journal_group_overrides", "same", True, "2026-09-13T10:00:01", mock,
                )
            rows = connection.execute(
                "SELECT collection,owner,document_key FROM journal_sync_states ORDER BY owner"
            ).fetchall()
            connection.close()
            self.assertEqual(2, len(rows))
            self.assertEqual({"journal_v2_group_overrides"}, {row[0] for row in rows})

    def test_other_account_tombstone_does_not_delete_verified_local_row(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "journal.sqlite3"
            repository = JournalRepository(path)
            real = AccountScope("kiwoom", AccountEnvironment.REAL, "11111111-1111-4111-8111-111111111111")
            mock = AccountScope("kiwoom", AccountEnvironment.MOCK, "22222222-2222-4222-8222-222222222222")
            repository.upsert_history_sync((TradeFill(
                "1", "005930", "삼성전자", "매수", datetime(2026, 9, 13, 9, 1), 1, 70000,
                origin_scope=real,
            ),), (), account_scope=real)
            connection = sqlite3.connect(path)
            key = connection.execute("SELECT fill_key FROM trade_fills").fetchone()[0]
            connection.execute(
                "INSERT INTO journal_sync_states VALUES(?,?,?,?,?,?,?,?,?)",
                ("journal_v2_fills", mock.account_ref, key, "kiwoom", "mock", mock.account_ref,
                 mock.account_ref, 1, "2026-09-13T20:00:00"),
            )
            connection.commit(); connection.close()

            CentralJournalSyncService(_Client()).sync(path)  # type: ignore[arg-type]

            connection = sqlite3.connect(path)
            self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM trade_fills").fetchone()[0])
            connection.close()
    def test_v1_document_cannot_overwrite_verified_row_with_same_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "journal.sqlite3"
            repository = JournalRepository(path)
            scope = AccountScope(
                "kiwoom", AccountEnvironment.REAL,
                "11111111-1111-4111-8111-111111111111",
            )
            repository.upsert_history_sync((TradeFill(
                "1", "005930", "삼성전자", "매수", datetime(2026, 9, 13, 9, 1),
                1, 70_000, origin_scope=scope,
            ),), (), account_scope=scope)
            connection = sqlite3.connect(path)
            fill_key = connection.execute("SELECT fill_key FROM trade_fills").fetchone()[0]
            connection.close()
            client = _Client()
            client.remote["journal_fills"] = [{
                "owner": "005930", "key": fill_key,
                "document": {
                    "fill_key": fill_key, "order_no": "1", "stock_code": "005930",
                    "stock_name": "오염", "side": "매수", "filled_at": "2026-09-13T09:01:00",
                    "quantity": 99, "price": 1,
                },
            }]

            CentralJournalSyncService(client).sync(path)  # type: ignore[arg-type]

            connection = sqlite3.connect(path)
            row = connection.execute(
                "SELECT stock_name,quantity,origin_account_ref FROM trade_fills"
            ).fetchone()
            status = connection.execute(
                "SELECT status FROM journal_legacy_imports WHERE target_key=?", (fill_key,)
            ).fetchone()
            connection.close()
            self.assertEqual(("삼성전자", 1, scope.account_ref), row)
            self.assertEqual(("CONFLICT",), status)

    def test_missing_remote_delete_state_defers_all_journal_merge_and_upload(self) -> None:
        class OldServerClient(_Client):
            def __init__(self):
                super().__init__()
                self.calls: list[tuple[str, str]] = []

            def load_all(self, collection):
                self.calls.append(("load", collection))
                if collection == "journal_sync_states":
                    raise CentralContentHttpError(404, "unsupported")
                return super().load_all(collection)

            def upsert(self, collection, documents):
                self.calls.append(("upsert", collection))
                return super().upsert(collection, documents)

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "journal.sqlite3"
            repository = JournalRepository(path)
            repository.assign_group(("fill-1",), "manual:local")
            client = OldServerClient()
            client.remote["journal_group_overrides"] = [{
                "owner": "default", "key": "fill-remote",
                "document": {
                    "fill_key": "fill-remote", "group_id": "manual:remote",
                    "updated_at": "2026-09-13T10:00:00",
                },
            }]
            service = CentralJournalSyncService(client)  # type: ignore[arg-type]

            self.assertEqual(0, service.sync(path))
            self.assertEqual(("journal_sync_states",), service.pending_collections)
            self.assertEqual([("load", "journal_sync_states")], client.calls)
            self.assertEqual({"fill-1": "manual:local"}, repository.load_group_overrides())

    def test_delete_state_auth_server_and_transport_errors_are_not_treated_as_optional(self) -> None:
        errors = (
            CentralContentHttpError(401, "unauthorized"),
            CentralContentHttpError(500, "server error"),
            CentralContentUnavailableError("중앙 자료 서버에 연결할 수 없습니다."),
        )
        for error in errors:
            class FailingClient(_Client):
                def load_all(self, collection):
                    if collection == "journal_sync_states":
                        raise error
                    return super().load_all(collection)

            with self.subTest(error=type(error).__name__, message=str(error)):
                with tempfile.TemporaryDirectory() as temporary:
                    path = Path(temporary) / "journal.sqlite3"
                    JournalRepository(path)
                    with self.assertRaises(type(error)):
                        CentralJournalSyncService(FailingClient()).sync(path)  # type: ignore[arg-type]

    def test_verified_account_rows_use_v2_collection_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "journal.sqlite3"
            repository = JournalRepository(path)
            scope = AccountScope(
                "kiwoom", AccountEnvironment.REAL,
                "11111111-1111-1111-1111-111111111111",
            )
            repository.upsert_history_sync((TradeFill(
                "1", "005930", "삼성전자", "매수",
                datetime(2026, 9, 13, 9, 1), 1, 70_000,
                origin_scope=scope,
            ),), (), account_scope=scope)

            class V2Client(_Client):
                def capabilities(self):
                    return {"journal_v2_sync": True}

            client = V2Client()
            CentralJournalSyncService(client).sync(path)  # type: ignore[arg-type]

            self.assertNotIn("journal_fills", client.remote)
            uploaded = client.remote["journal_v2_fills"][0]["document"]
            self.assertEqual(scope.account_ref, uploaded["origin_account_ref"])
            self.assertEqual(scope.account_ref, uploaded["canonical_account_ref"])

    def test_deleted_group_override_is_not_restored_by_older_remote_value(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "journal.sqlite3"
            repository = JournalRepository(path)
            client = _Client()
            repository.assign_group(("fill-1",), "manual:one")
            CentralJournalSyncService(client).sync(path)  # type: ignore[arg-type]

            repository.clear_group_assignments(("fill-1",))
            CentralJournalSyncService(client).sync(path)  # type: ignore[arg-type]
            CentralJournalSyncService(client).sync(path)  # type: ignore[arg-type]

            self.assertEqual({}, repository.load_group_overrides())
            states = client.remote["journal_sync_states"]
            self.assertTrue(states[-1]["document"]["is_deleted"])

    def test_v7_v1_tombstone_survives_v8_migration_restart_and_old_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "journal.sqlite3"
            repository = JournalRepository(path)
            repository.assign_group(("fill-1",), "manual:one", datetime(2026, 9, 13, 9))
            repository.clear_group_assignments(("fill-1",))
            with closing(sqlite3.connect(path)) as connection:
                with connection:
                    connection.execute(
                        "INSERT INTO trade_group_overrides("
                        "fill_key,group_id,updated_at,origin_broker,origin_environment,"
                        "origin_account_ref,canonical_account_ref) VALUES(?,?,?,?,?,?,?)",
                        ("fill-1", "manual:old", "2026-09-13T09:00:00", "legacy", "unknown",
                         "legacy-unassigned", "legacy-unassigned"),
                    )
                    connection.execute(
                        "ALTER TABLE journal_sync_states RENAME TO journal_sync_states_v8_fixture"
                    )
                    connection.execute(
                        "CREATE TABLE journal_sync_states("
                        "collection TEXT NOT NULL,document_key TEXT NOT NULL,is_deleted INTEGER NOT NULL,"
                        "updated_at TEXT NOT NULL,PRIMARY KEY(collection,document_key))"
                    )
                    connection.execute(
                        "INSERT INTO journal_sync_states "
                        "SELECT collection,document_key,is_deleted,updated_at "
                        "FROM journal_sync_states_v8_fixture"
                    )
                    connection.execute("DROP TABLE journal_sync_states_v8_fixture")
                    connection.execute(
                        "ALTER TABLE journal_legacy_imports RENAME TO journal_legacy_imports_v8_fixture"
                    )
                    connection.execute(
                        "CREATE TABLE journal_legacy_imports("
                        "source_collection TEXT NOT NULL,source_key TEXT NOT NULL,"
                        "source_revision TEXT NOT NULL,target_collection TEXT NOT NULL,"
                        "target_key TEXT NOT NULL,imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,"
                        "PRIMARY KEY(source_collection,source_key,source_revision))"
                    )
                    connection.execute("DROP TABLE journal_legacy_imports_v8_fixture")
                    connection.execute("DELETE FROM journal_schema_migrations WHERE version=8")

            initialize_journal_database(path)
            with closing(sqlite3.connect(path)) as connection:
                state = connection.execute(
                    "SELECT owner,origin_broker,origin_environment,origin_account_ref,"
                    "canonical_account_ref FROM journal_sync_states "
                    "WHERE collection='journal_group_overrides' AND document_key='fill-1'"
                ).fetchone()
            self.assertEqual(
                ("legacy", "legacy", "unknown", "legacy-unassigned", "legacy-unassigned"), state,
            )

            client = _Client()
            client.remote["journal_group_overrides"] = [{
                "owner": "default", "key": "fill-1",
                "document": {
                    "fill_key": "fill-1", "group_id": "manual:old",
                    "updated_at": "2026-09-13T09:00:00",
                },
            }]
            CentralJournalSyncService(client).sync(path)  # type: ignore[arg-type]
            self.assertEqual({}, JournalRepository(path).load_group_overrides())
            CentralJournalSyncService(client).sync(path)  # type: ignore[arg-type]
            self.assertEqual({}, JournalRepository(path).load_group_overrides())

    def test_same_v1_revision_is_not_reimported_after_legacy_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "journal.sqlite3"
            repository = JournalRepository(path)
            client = _Client()
            client.remote["journal_group_overrides"] = [{
                "owner": "default", "key": "fill-1",
                "document": {
                    "fill_key": "fill-1", "group_id": "manual:one",
                    "updated_at": "2026-09-13T10:00:00",
                },
            }]

            CentralJournalSyncService(client).sync(path)  # type: ignore[arg-type]
            self.assertEqual({"fill-1": "manual:one"}, repository.load_group_overrides())
            repository.clear_group_assignments(("fill-1",))
            # 원본 revision 원장이 tombstone과 독립적으로 같은 구 앱 문서를 기억하는지 검증한다.
            connection = sqlite3.connect(path)
            try:
                connection.execute(
                    "DELETE FROM journal_sync_states WHERE collection='journal_group_overrides'"
                )
                connection.commit()
            finally:
                connection.close()

            CentralJournalSyncService(client).sync(path)  # type: ignore[arg-type]

            self.assertEqual({}, repository.load_group_overrides())
            connection = sqlite3.connect(path)
            try:
                source = connection.execute(
                    "SELECT source_collection,source_owner,source_key,source_modified_at,"
                    "target_key,status "
                    "FROM journal_legacy_imports"
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(
                (
                    "journal_group_overrides", "default", "fill-1",
                    "2026-09-13T10:00:00", "fill-1", "IMPORTED",
                ),
                source,
            )

    def test_runner_rejects_duplicate_schedule_until_current_sync_finishes(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        class Service:
            def sync(self, _path: Path) -> int:
                entered.set()
                release.wait(2)
                return 1

        runner = CentralJournalSyncRunner(Service(), Path("journal.sqlite3"))  # type: ignore[arg-type]

        self.assertTrue(runner.schedule())
        self.assertTrue(entered.wait(1))
        self.assertTrue(runner.is_running)
        self.assertFalse(runner.schedule())
        release.set()
        for _ in range(100):
            if not runner.is_running:
                break
            threading.Event().wait(0.01)
        self.assertFalse(runner.is_running)

    def test_runner_clears_state_and_reports_supported_sync_error(self) -> None:
        received: list[str] = []
        finished = threading.Event()

        class Service:
            def sync(self, _path: Path) -> int:
                try:
                    raise sqlite3.Error("locked")
                finally:
                    finished.set()

        runner = CentralJournalSyncRunner(
            Service(), Path("journal.sqlite3"), lambda error: received.append(str(error)),  # type: ignore[arg-type]
        )

        self.assertTrue(runner.schedule())
        self.assertTrue(finished.wait(1))
        for _ in range(100):
            if not runner.is_running:
                break
            threading.Event().wait(0.01)
        self.assertEqual(["locked"], received)
        self.assertFalse(runner.is_running)

    def test_runner_without_service_does_not_start(self) -> None:
        runner = CentralJournalSyncRunner(None, Path("journal.sqlite3"))

        self.assertFalse(runner.schedule())
        self.assertFalse(runner.is_running)
        runner.close()
        runner.close()
        self.assertFalse(runner.schedule())

    def test_runner_close_joins_actual_thread_outside_lock_and_refuses_later_work(self) -> None:
        entered, release, closed = (threading.Event() for _ in range(3))
        calls = []

        class Service:
            def sync(self, path: Path) -> int:
                calls.append(path)
                entered.set()
                release.wait(2)
                return 1

        runner = CentralJournalSyncRunner(Service(), Path("journal.sqlite3"))  # type: ignore[arg-type]
        closer = threading.Thread(target=lambda: (runner.close(), closed.set()))
        try:
            self.assertTrue(runner.schedule())
            self.assertTrue(entered.wait(1))
            closer.start()
            self.assertFalse(closed.wait(0.05))
            self.assertTrue(runner.is_running)
        finally:
            release.set()
            if closer.ident is not None:
                closer.join(2)
            runner.close()
        self.assertFalse(closer.is_alive())
        self.assertTrue(closed.is_set())
        self.assertFalse(runner.is_running)
        runner.close()
        self.assertFalse(runner.schedule())
        self.assertEqual([Path("journal.sqlite3")], calls)

    def test_runner_start_failure_preserves_exception_and_restores_retry_or_close(self) -> None:
        from unittest.mock import Mock, patch
        for retry in (False, True):
            with self.subTest(retry=retry):
                service = Mock()
                sentinel = RuntimeError("controlled native thread start failure")
                runner = CentralJournalSyncRunner(service, Path("journal.sqlite3"))
                with patch.object(threading.Thread, "start", side_effect=sentinel):
                    with self.assertRaises(RuntimeError) as caught:
                        runner.schedule()
                self.assertIs(sentinel, caught.exception)
                self.assertFalse(runner.is_running)
                if retry:
                    self.assertTrue(runner.schedule())
                runner.close()
                runner.close()
                self.assertFalse(runner.schedule())
                self.assertEqual(int(retry), service.sync.call_count)

    def test_runner_close_serializes_with_in_progress_start_and_prevents_new_admission(self) -> None:
        from unittest.mock import Mock, patch
        start_entered, allow_start, service_entered, release_service, closed = (
            threading.Event() for _ in range(5))
        real_start = threading.Thread.start
        results = []

        class Service:
            def sync(self, _path: Path) -> int:
                service_entered.set()
                release_service.wait(2)
                return 1

        def start(thread):
            if thread.name == "central-journal-settings-sync":
                start_entered.set()
                allow_start.wait(2)
            return real_start(thread)

        runner = CentralJournalSyncRunner(Service(), Path("journal.sqlite3"))  # type: ignore[arg-type]
        scheduler = threading.Thread(target=lambda: results.append(runner.schedule()))
        closer = threading.Thread(target=lambda: (runner.close(), closed.set()))
        try:
            with patch.object(threading.Thread, "start", start):
                scheduler.start()
                self.assertTrue(start_entered.wait(1))
                closer.start()
                self.assertFalse(closed.wait(0.05))
                allow_start.set()
                self.assertTrue(service_entered.wait(1))
                self.assertFalse(closed.is_set())
        finally:
            allow_start.set()
            release_service.set()
            scheduler.join(2)
            if closer.ident is not None:
                closer.join(2)
            runner.close()
        self.assertFalse(scheduler.is_alive())
        self.assertFalse(closer.is_alive())
        self.assertEqual([True], results)
        self.assertTrue(closed.is_set())
        self.assertFalse(runner.schedule())
        never_started = CentralJournalSyncRunner(Mock(), Path("journal.sqlite3"))
        never_started.close()
        with patch.object(threading.Thread, "start") as launch:
            self.assertFalse(never_started.schedule())
        launch.assert_not_called()

    def test_newer_local_strategy_setting_is_preserved_and_uploaded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "journal.sqlite3"
            connection = sqlite3.connect(path)
            connection.executescript("""
                CREATE TABLE journal_settings(setting_key TEXT PRIMARY KEY,value_json TEXT,updated_at TEXT);
                INSERT INTO journal_settings VALUES('strategy_packs','[\"local\"]','2026-09-08T10:00:00');
            """)
            connection.close()
            client = _Client()
            client.remote["journal_settings"] = [{
                "owner": "default", "key": "strategy_packs",
                "document": {"setting_key": "strategy_packs", "value_json": "[\"old\"]", "updated_at": "2026-09-08T09:00:00"},
            }]

            saved = CentralJournalSyncService(client).sync(path)  # type: ignore[arg-type]

            self.assertEqual(1, saved)
            self.assertEqual('["local"]', client.remote["journal_settings"][-1]["document"]["value_json"])

    def test_newer_remote_setting_is_merged_into_local_database(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "journal.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute("CREATE TABLE journal_settings(setting_key TEXT PRIMARY KEY,value_json TEXT,updated_at TEXT)")
            connection.execute("INSERT INTO journal_settings VALUES(?,?,?)", (
                "personal_trade_rules", '["old"]', "2026-09-08T10:00:00+09:00",
            ))
            connection.commit()
            connection.close()
            client = _Client()
            client.remote["journal_settings"] = [{
                "owner": "default", "key": "personal_trade_rules",
                "document": {"setting_key": "personal_trade_rules", "value_json": "[\"rule\"]", "updated_at": "2026-09-08T02:00:00Z"},
            }]

            CentralJournalSyncService(client).sync(path)  # type: ignore[arg-type]

            connection = sqlite3.connect(path)
            self.assertEqual('["rule"]', connection.execute(
                "SELECT value_json FROM journal_settings WHERE setting_key='personal_trade_rules'"
            ).fetchone()[0])
            connection.close()

    def test_mixed_offset_tombstones_compare_instants_without_resurrection(self) -> None:
        for value_at, deletion_at, deleted, undelete in (
            ("2026-09-08T10:00:00+09:00", "2026-09-08T02:00:00Z", True, False),
            ("2026-09-08T02:00:00Z", "2026-09-08T10:00:00+09:00", False, False),
            ("2026-09-08T02:00:00Z", "2026-09-08T10:00:00+09:00", False, True),
            ("2026-09-08T10:00:00", "2026-09-08T01:00:00Z", True, False),
        ):
            with self.subTest(value_at=value_at, deletion_at=deletion_at), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "journal.sqlite3"
                JournalRepository(path)
                with closing(sqlite3.connect(path)) as connection, connection:
                    connection.execute("INSERT INTO journal_settings VALUES(?,?,?)", ("strategy_packs", '["kept"]', value_at))
                    connection.execute(
                        "INSERT INTO journal_sync_states(collection,owner,document_key,origin_broker,origin_environment,origin_account_ref,canonical_account_ref,is_deleted,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                        ("journal_settings", "legacy", "strategy_packs", "legacy", "unknown", "legacy-unassigned", "legacy-unassigned", 1, deletion_at),
                    )
                client = _Client()
                client.remote["journal_settings"] = [{"owner": "default", "key": "strategy_packs", "document": {
                    "setting_key": "strategy_packs", "value_json": '["kept"]', "updated_at": value_at,
                }}]
                # A newer remote non-deletion must also replace an older state.
                if undelete:
                    client.remote["journal_sync_states"] = [{"owner": "legacy", "key": "strategy_packs", "document": {
                        "collection": "journal_settings", "owner": "legacy", "document_key": "strategy_packs",
                        "is_deleted": False, "updated_at": value_at,
                    }}]
                for _ in range(2):
                    CentralJournalSyncService(client).sync(path)
                    with closing(sqlite3.connect(path)) as connection:
                        row = connection.execute("SELECT value_json,updated_at FROM journal_settings WHERE setting_key='strategy_packs'").fetchone()
                        state = connection.execute("SELECT is_deleted,updated_at FROM journal_sync_states WHERE collection='journal_settings' AND owner='legacy' AND document_key='strategy_packs'").fetchone()
                    self.assertEqual(None if deleted else ('["kept"]', value_at), row)
                    self.assertEqual(int(deleted), state[0])
                    self.assertEqual(deletion_at if deleted else value_at, state[1])

    def test_fills_reviews_and_entry_snapshots_are_uploaded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "journal.sqlite3"
            connection = sqlite3.connect(path)
            connection.executescript("""
                CREATE TABLE trade_fills(order_no TEXT,stock_code TEXT,stock_name TEXT,side TEXT,filled_at TEXT,quantity INTEGER,price INTEGER,order_type TEXT,market TEXT,PRIMARY KEY(order_no,stock_code,filled_at,side));
                INSERT INTO trade_fills VALUES('1','005930','삼성전자','매수','2026-09-08T09:01:00',1,1000,'','KRX');
                CREATE TABLE trade_reviews(group_id TEXT PRIMARY KEY,reason TEXT,review TEXT,tags TEXT,rating TEXT,status TEXT,updated_at TEXT);
                INSERT INTO trade_reviews VALUES('g1','','원칙 준수','','좋음','완료','2026-09-08T10:00:00');
                CREATE TABLE trade_entry_snapshots(execution_key TEXT PRIMARY KEY,captured_at TEXT);
                INSERT INTO trade_entry_snapshots VALUES('e1','2026-09-08T09:01:01');
            """)
            connection.close()
            client = _Client()

            saved = CentralJournalSyncService(client).sync(path)  # type: ignore[arg-type]

            self.assertEqual(3, saved)
            self.assertEqual("1|005930|2026-09-08T09:01:00|매수", client.remote["journal_fills"][0]["key"])
            self.assertEqual("g1", client.remote["journal_reviews"][0]["key"])
            self.assertEqual("e1", client.remote["journal_entry_snapshots"][0]["key"])


if __name__ == "__main__":
    unittest.main()
