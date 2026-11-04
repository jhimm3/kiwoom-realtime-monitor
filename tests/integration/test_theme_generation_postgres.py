from __future__ import annotations

import os
import threading
import unittest
import uuid
from contextlib import contextmanager
from unittest.mock import patch

from kiwoom_monitor.central_server.database import PostgresQueryStore
from kiwoom_monitor.infrastructure.persistence.postgres_test_target import explicit_dedicated_url
from tests.unit.test_theme_generation import NOW, ThemeGenerationContract, theme_document


class PostgresThemeGenerationTests(ThemeGenerationContract, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg

        url = os.environ.get("KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL", "")
        if not url:
            raise unittest.SkipTest("dedicated diagnostic PostgreSQL URL required")
        cls.test_url = explicit_dedicated_url(url)
        cls.native_error = psycopg.errors.RaiseException

    def setUp(self):
        import psycopg
        from psycopg.conninfo import make_conninfo

        self.schema = "ar05_theme_" + uuid.uuid4().hex
        with psycopg.connect(self.test_url, autocommit=True) as connection:
            self.assertEqual("kiwoom_monitor_diagnostic_test",
                             connection.execute("SELECT current_database()").fetchone()[0])
            connection.execute(f"CREATE SCHEMA {self.schema}")
        self.addCleanup(self.drop_schema)
        self.dsn = make_conninfo(self.test_url, options=f"-c search_path={self.schema}")
        self.store = self.new_store()
        self.store.initialize()
        self.addCleanup(self.store.close)

    def drop_schema(self):
        import psycopg

        with psycopg.connect(self.test_url, autocommit=True) as connection:
            connection.execute(f"DROP SCHEMA {self.schema} CASCADE")

    def new_store(self):
        return PostgresQueryStore(self.dsn, now_provider=lambda: NOW)

    @contextmanager
    def native_fault(self, table):
        with self.store._connect() as connection:
            condition = "IF NEW.collection <> 'theme_metadata' THEN RETURN NEW; END IF;" if table == "central_documents" else ""
            connection.execute(
                "CREATE FUNCTION theme_fault() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
                + condition + " RAISE EXCEPTION 'theme native fault'; END $$",
            )
            connection.execute(f"CREATE TRIGGER theme_fault BEFORE INSERT ON {table} "
                               "FOR EACH ROW EXECUTE FUNCTION theme_fault()")
        try:
            yield
        finally:
            with self.store._connect() as connection:
                connection.execute(f"DROP TRIGGER theme_fault ON {table}")
                connection.execute("DROP FUNCTION theme_fault()")

    @contextmanager
    def lost_commit_ack(self):
        import psycopg

        class Connection(psycopg.Connection):
            def commit(connection):
                super().commit()
                connection.close()
                raise RuntimeError("theme commit ACK lost")

        with patch.object(self.store, "_connect", lambda: Connection.connect(self.dsn)):
            yield

    def test_generic_metadata_and_generation_use_the_same_native_lock_order(self):
        self._assert_generic_lock_order("upsert_documents")

    def test_generic_replacement_and_generation_use_the_same_native_lock_order(self):
        self._assert_generic_lock_order("replace_documents")

    def test_standalone_history_helper_keeps_native_lock_and_noop_receipt(self):
        from kiwoom_monitor.central_server.database_documents import _append_postgres_theme_snapshot

        document = theme_document()
        values = [{"owner": "default", "key": "full", "document": document}]
        with self.store._connect() as connection, connection.cursor() as cursor:
            _append_postgres_theme_snapshot(
                cursor, values, received_at=NOW.timestamp(), now_provider=lambda: NOW,
            )
            cursor.execute(
                "SELECT mode FROM pg_locks WHERE pid=pg_backend_pid() "
                "AND relation='central_theme_snapshots'::regclass AND granted",
            )
            self.assertIn("ShareRowExclusiveLock", [row[0] for row in cursor.fetchall()])
        first = self.store.load_theme_snapshots()
        with self.store._connect() as connection, connection.cursor() as cursor:
            _append_postgres_theme_snapshot(
                cursor, values, received_at=NOW.timestamp()+100, now_provider=lambda: NOW,
            )
        self.assertEqual(first, self.store.load_theme_snapshots())

    def _assert_generic_lock_order(self, operation):
        import psycopg

        first = self.publish(theme_document())["snapshot"]
        new_locked, generic_requested = threading.Event(), threading.Event()
        failures, accepted = [], []
        dsn = self.dsn

        class Cursor(psycopg.Cursor):
            def execute(cursor, query, *args, **kwargs):
                is_history_lock = str(query).startswith("LOCK TABLE central_theme_snapshots")
                is_new = threading.current_thread().name == "theme-new-generation"
                if is_history_lock and not is_new:
                    generic_requested.set()
                result = super().execute(query, *args, **kwargs)
                if is_history_lock and is_new:
                    new_locked.set()
                    if not generic_requested.wait(5):
                        raise AssertionError("generic metadata did not reach the native lock")
                return result

        def connect():
            return psycopg.connect(dsn, cursor_factory=Cursor)

        def new_publish():
            peer = self.new_store()
            try:
                with patch.object(peer, "_connect", connect):
                    accepted.append(peer.publish_theme_snapshot(
                        theme_document("원자 게시"), publication_id=uuid.uuid4().hex,
                        expected_snapshot_id=first["snapshot_id"], origin_device="pc-new",
                    ))
            except BaseException as error:
                failures.append(error)

        def generic_write():
            peer = self.new_store()
            try:
                if not new_locked.wait(5):
                    raise AssertionError("new generation did not acquire history lock")
                with patch.object(peer, "_connect", connect):
                    getattr(peer, operation)("theme_metadata", [{
                        "owner": "default", "key": "full", "document": theme_document("기존 게시"),
                    }])
            except BaseException as error:
                failures.append(error)

        workers = [threading.Thread(target=new_publish, name="theme-new-generation"),
                   threading.Thread(target=generic_write, name="theme-generic-metadata")]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(15)
        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual([], [(type(error).__name__, str(error)) for error in failures])
        self.assertEqual(1, len(accepted))
        history = self.store.load_theme_snapshots()
        self.assertEqual(3, len(history))
        self.assertEqual(accepted[0]["snapshot"]["snapshot_id"], history[0]["revision_of"])
        self.assertEqual("기존 게시", history[0]["document"]["profiles"][0]["themes"][0]["name"])
