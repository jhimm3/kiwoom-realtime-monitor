from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path

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
from kiwoom_monitor.application.trade_history_service import TradeFill
from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope


class _Client:
    def __init__(self) -> None:
        self.remote: dict[str, list[dict[str, object]]] = {}

    def load_all(self, collection: str) -> list[dict[str, object]]:
        return list(self.remote.get(collection, []))

    def upsert(self, collection: str, documents: list[dict[str, object]]) -> int:
        self.remote.setdefault(collection, []).extend(documents)
        return len(documents)


class CentralJournalSyncServiceTests(unittest.TestCase):
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
            connection.close()
            client = _Client()
            client.remote["journal_settings"] = [{
                "owner": "default", "key": "personal_trade_rules",
                "document": {"setting_key": "personal_trade_rules", "value_json": "[\"rule\"]", "updated_at": "2026-09-08T10:00:00"},
            }]

            CentralJournalSyncService(client).sync(path)  # type: ignore[arg-type]

            connection = sqlite3.connect(path)
            self.assertEqual('["rule"]', connection.execute(
                "SELECT value_json FROM journal_settings WHERE setting_key='personal_trade_rules'"
            ).fetchone()[0])
            connection.close()

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
