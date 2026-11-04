from __future__ import annotations

import asyncio
import io
import json
import sqlite3
import tempfile
import threading
import unittest
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from unittest.mock import patch

from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.infrastructure.central_content_client import (
    CentralContentClient, CentralContentHttpError, CentralContentUnavailableError,
)
from kiwoom_monitor.infrastructure.central_content_sync import CentralContentSyncService
from tests.unit.test_theme_generation import NOW, theme_document


NAMES = ("theme_profile", "theme_stock", "theme_metadata")


class ThemeCurrentSnapshotContract:
    def actual(self):
        return {"collections": {name: self.store.load_documents(name, limit=10000) for name in NAMES},
                "completed_at": max((float(row["updated_at"]) for row in self.store.load_documents("theme_metadata")), default=None)}

    def publish(self, name="반도체", publication_id="first", expected=None, store=None):
        return (store or self.store).publish_theme_snapshot(
            theme_document(name), publication_id=publication_id,
            expected_snapshot_id=expected, origin_device="pc-a",
        )["snapshot"]

    def test_empty_current_has_no_invented_head(self):
        self.assertEqual({"collections": {name: [] for name in NAMES}, "completed_at": None},
                         self.store.load_current_theme_snapshot())

    def test_native_head_current_rows_keep_original_metadata_and_order(self):
        self.publish()
        expected = self.actual()
        history = self.store.load_theme_snapshots()
        self.assertEqual(expected, self.store.load_current_theme_snapshot())
        self.assertEqual(history, self.store.load_theme_snapshots())
        document = expected["collections"]["theme_metadata"][0]["document"]
        self.assertEqual(theme_document()["stock_catalog"], document["stock_catalog"])
        self.assertEqual(theme_document()["aliases"], document["aliases"])
        self.assertEqual(theme_document()["created_at"], document["created_at"])

    def test_late_legacy_profile_and_stock_updates_are_current_not_old_head(self):
        head = self.publish()
        self.store.upsert_documents("theme_profile", [{"owner": "legacy", "key": "new",
            "document": {"theme_name": "원전", "is_deleted": True, "source": {"legacy": "original"}}}])
        self.store.replace_documents("theme_stock", [{"owner": "legacy", "key": "late",
            "document": {"stock_code": "000660", "theme_name": "원전", "custom_color": None}}])
        self.assertEqual(self.actual(), self.store.load_current_theme_snapshot())
        self.assertEqual(head, self.store.load_accepted_theme_snapshot())
        self.assertEqual(["late"], [row["key"] for row in self.store.load_current_theme_snapshot()["collections"]["theme_stock"]])

    def test_legacy_metadata_clear_keeps_real_empty_values_without_resurrecting_history(self):
        head = self.publish()
        self.store.replace_documents("theme_stock", [])
        self.store.replace_documents("theme_metadata", [])
        current = self.store.load_current_theme_snapshot()
        self.assertEqual(self.actual(), current)
        self.assertEqual([], current["collections"]["theme_stock"])
        self.assertEqual([], current["collections"]["theme_metadata"])
        self.assertIsNone(current["completed_at"])
        self.assertEqual(head, self.store.load_accepted_theme_snapshot())

    def test_legacy_metadata_rows_keep_the_actual_latest_receipt_not_history_completion(self):
        head = self.publish()
        with patch.object(self.store, "_now_provider", lambda: NOW + timedelta(seconds=17)):
            self.store.upsert_documents("theme_metadata", [{
                "owner": "legacy", "key": "partial", "document": {"legacy": "unmodified"},
            }])
        current = self.store.load_current_theme_snapshot()
        self.assertEqual(self.actual(), current)
        self.assertEqual((NOW + timedelta(seconds=17)).timestamp(), current["completed_at"])
        self.assertEqual(head, self.store.load_accepted_theme_snapshot())

    def test_all_rows_beyond_one_old_page_are_preserved_in_existing_order(self):
        self.store.upsert_documents("theme_profile", [{"owner": "legacy", "key": f"theme-{index:04}",
            "document": {"theme_name": f"테마{index}"}} for index in range(1003)])
        current = self.store.load_current_theme_snapshot()
        self.assertEqual(self.actual(), current)
        self.assertEqual(1003, len(current["collections"]["theme_profile"]))

    def test_peer_commit_during_native_read_cannot_mix_the_three_collections(self):
        from kiwoom_monitor.central_server import database_documents

        self.publish()
        before = self.actual()
        peer = self.new_store()
        self.addCleanup(peer.close)
        errors, statements, connections = [], [], []
        finished = threading.Event()

        def write():
            try:
                self.publish("원전", publication_id="second", expected="first", store=peer)
            except BaseException as error:
                errors.append(error)
            finally:
                finished.set()

        writer = threading.Thread(target=write)
        real_read = database_documents._load_current_theme_snapshot
        real_connect = self.store._connect

        def connect():
            connection = real_connect()
            connections.append(connection)
            return connection

        class ObservedCursor:
            def __init__(self, cursor):
                self.cursor = cursor

            def execute(self, sql, parameters):
                statements.append(sql)
                self.cursor.execute(sql, parameters)
                writer.start()
                if not finished.wait(5):
                    raise AssertionError("peer native publication failed to settle during reader snapshot")

            def fetchall(self):
                return self.cursor.fetchall()

        with patch.object(self.store, "_connect", side_effect=connect), patch.object(database_documents, "_load_current_theme_snapshot",
                          side_effect=lambda cursor, placeholder: real_read(ObservedCursor(cursor), placeholder)):
            try:
                current = self.store.load_current_theme_snapshot()
            finally:
                if writer.ident is not None:
                    writer.join(5)
        self.assertFalse(writer.is_alive())
        self.assertEqual([], errors)
        self.assertEqual(1, len(statements))
        self.assertEqual(1, len(connections))
        if hasattr(connections[0], "closed"):
            self.assertTrue(connections[0].closed)
        else:
            with self.assertRaises(sqlite3.ProgrammingError):
                connections[0].execute("SELECT 1")
        self.assertEqual(before, current)
        self.assertNotEqual(current, self.store.load_current_theme_snapshot())
        self.assertEqual(self.actual(), self.store.load_current_theme_snapshot())


class SQLiteThemeCurrentSnapshotTests(ThemeCurrentSnapshotContract, unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "central.sqlite3"
        self.store = self.new_store()
        self.store.initialize()
        self.addCleanup(self.store.close)

    def new_store(self):
        return SQLiteQueryStore(self.path, now_provider=lambda: NOW)


class Response(io.BytesIO):
    def __init__(self, body, headers=None):
        super().__init__(json.dumps(body, ensure_ascii=False).encode())
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class ThemeCurrentSnapshotTransportTests(unittest.TestCase):
    def test_actual_client_and_service_use_one_authenticated_get_preserve_values_and_clock(self):
        payload = {"collections": {name: [] for name in NAMES}, "completed_at": 42.0}
        payload["collections"]["theme_metadata"] = [{"owner": "default", "key": "full", "updated_at": 42,
                                                     "document": {"original": "catalog/aliases"}}]
        calls = []

        def opener(request, **kwargs):
            calls.append((request, kwargs))
            return Response(payload, {"X-Kiwoom-Server-Time": "2026-10-23T09:30:00+09:00"})

        client = CentralContentClient("https://nas.test", "token", opener=opener, timeout_seconds=27)
        with patch("kiwoom_monitor.infrastructure.central_content_client.monotonic", return_value=10):
            collections, completed_at = CentralContentSyncService(client).load_theme_snapshot()
            self.assertEqual("2026-10-23T09:30:00+09:00", client.server_now().isoformat())
        self.assertEqual(payload["collections"], collections)
        self.assertEqual(42, completed_at)
        self.assertEqual(1, len(calls))
        request, kwargs = calls[0]
        self.assertEqual("https://nas.test/api/v1/themes/current-snapshot", request.full_url)
        self.assertEqual("GET", request.method)
        self.assertEqual("Bearer token", request.get_header("Authorization"))
        self.assertEqual("1", request.get_header("X-kiwoom-clock"))
        self.assertEqual(27, kwargs["timeout"])

    def test_only_real_404_falls_back_to_actual_legacy_collection_reads(self):
        calls = []

        def opener(request, **_kwargs):
            calls.append(request.full_url)
            if request.full_url.endswith("current-snapshot"):
                raise HTTPError(request.full_url, 404, "older server", {}, io.BytesIO(b'{}'))
            return Response({"documents": [{"key": request.full_url, "document": {}, "updated_at": 12}]})

        client = CentralContentClient("https://nas.test", "token", opener=opener)
        collections, completed_at = CentralContentSyncService(client, batch_size=17).load_theme_snapshot()
        self.assertEqual(4, len(calls))
        self.assertEqual(12, completed_at)
        self.assertTrue(all(len(rows) == 1 for rows in collections.values()))
        self.assertTrue(all("page_size" not in url and "limit=17" in url for url in calls[1:]))

    def test_errors_and_invalid_payloads_never_fall_back(self):
        invalid = ({}, {"collections": {}, "completed_at": None},
                   {"collections": {name: [] for name in NAMES}},
                   {"collections": {name: [] for name in NAMES}, "completed_at": "12"},
                   {"collections": {name: [None] for name in NAMES}, "completed_at": None})
        for payload in invalid:
            with self.subTest(payload=payload):
                calls = []

                def opener(request, **_kwargs):
                    calls.append(request.full_url)
                    return Response(payload)

                with self.assertRaises(RuntimeError):
                    CentralContentSyncService(CentralContentClient("https://nas.test", "token", opener=opener)).load_theme_snapshot()
                self.assertEqual(1, len(calls))
        for error in (CentralContentHttpError(401), CentralContentHttpError(500),
                      CentralContentUnavailableError("timeout"), RuntimeError("HTTP 404 fake error")):
            class Client:
                def load_current_theme_snapshot(self):
                    raise error

                def load_all(self, *_args, **_kwargs):
                    raise AssertionError("non-404 failure must not use old collections")

            with self.subTest(error=error), self.assertRaises(type(error)):
                CentralContentSyncService(Client()).load_theme_snapshot()

    def test_empty_current_response_is_returned_without_legacy_reads(self):
        calls = []

        def opener(request, **_kwargs):
            calls.append(request.full_url)
            return Response({"collections": {name: [] for name in NAMES}, "completed_at": None})

        self.assertEqual(({name: [] for name in NAMES}, None),
                         CentralContentSyncService(CentralContentClient("https://nas.test", "token", opener=opener)).load_theme_snapshot())
        self.assertEqual(1, len(calls))


class ThemeCurrentSnapshotApiTests(unittest.TestCase):
    def test_cancelled_registered_read_retains_native_connection_until_worker_exits(self):
        import httpx
        from kiwoom_monitor.central_server.app import create_app
        from kiwoom_monitor.central_server import database_documents
        from tests.unit.test_central_server_app import content_contract_app

        with tempfile.TemporaryDirectory() as directory:
            with content_contract_app(create_app, Path(directory) / "central.sqlite3") as (app, store, _clock):
                store.upsert_documents("theme_profile", [{
                    "owner": "pc-a", "key": "original", "document": {"name": "보존"},
                }])
                original_rows = store.load_documents("theme_profile")
                entered, release = threading.Event(), threading.Event()
                connections, statements = [], []
                # The helper's seed store and create_app's store share a file,
                # but are distinct objects. Observe the actual app acquisition.
                real_connect, real_read = type(store)._connect, database_documents._load_current_theme_snapshot

                def connect(owner):
                    connection = real_connect(owner)
                    connections.append(connection)
                    return connection

                class Cursor:
                    def __init__(self, cursor): self.cursor = cursor

                    def execute(self, sql, parameters):
                        statements.append(sql)
                        self.cursor.execute(sql, parameters)
                        entered.set()
                        if not release.wait(5):
                            raise AssertionError("controlled native read was not released")

                    def fetchall(self): return self.cursor.fetchall()

                async def scenario():
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                        task = asyncio.create_task(client.get(
                            "/api/v1/themes/current-snapshot", headers={"Authorization": "Bearer private-token"},
                        ))
                        try:
                            self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                            for _ in range(2):
                                self.assertTrue(task.cancel())
                                done, _pending = await asyncio.wait({task}, timeout=0.05)
                                self.assertEqual(set(), done)
                            release.set()
                            with self.assertRaises(asyncio.CancelledError):
                                await task
                            self.assertEqual(1, len(connections))
                            with self.assertRaises(sqlite3.ProgrammingError):
                                connections[0].execute("SELECT 1")
                        finally:
                            release.set()
                            if not task.done(): task.cancel()
                            try:
                                await task
                            except asyncio.CancelledError:
                                pass

                with patch.object(type(store), "_connect", new=connect), patch.object(
                    database_documents, "_load_current_theme_snapshot",
                    side_effect=lambda cursor, placeholder: real_read(Cursor(cursor), placeholder),
                ):
                    asyncio.run(scenario())
                self.assertEqual(1, len(statements))
                self.assertEqual(original_rows, store.load_documents("theme_profile"))

    def test_registered_route_and_actual_client_return_current_legacy_values_without_mutation(self):
        import httpx
        from kiwoom_monitor.central_server.app import create_app
        from tests.unit.test_central_server_app import content_contract_app

        with tempfile.TemporaryDirectory() as directory:
            with content_contract_app(create_app, Path(directory) / "central.sqlite3") as (app, store, _clock):
                endpoint = "/api/v1/themes/current-snapshot"

                async def request(path, headers):
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                        return await client.get(path, headers=headers)

                self.assertEqual(401, asyncio.run(request(endpoint, {})).status_code)
                calls = []

                def opener(req, **_kwargs):
                    calls.append(req.full_url)
                    response = asyncio.run(request(urlsplit(req.full_url).path, dict(req.header_items())))
                    if response.status_code >= 400:
                        raise HTTPError(req.full_url, response.status_code, "ASGI error", response.headers, io.BytesIO(response.content))
                    return Response(response.json(), response.headers)

                service = CentralContentSyncService(CentralContentClient("http://test", "private-token", opener=opener))
                self.assertEqual(({name: [] for name in NAMES}, None), service.load_theme_snapshot())
                store.publish_theme_snapshot(theme_document(), publication_id="api-head", expected_snapshot_id=None, origin_device="pc-a")
                original_head = store.load_accepted_theme_snapshot()
                store.upsert_documents("theme_profile", [{"owner": "legacy", "key": "late", "document": {"is_deleted": True}}])
                for clear in (False, True):
                    if clear:
                        store.replace_documents("theme_metadata", [])
                    expected = {name: store.load_documents(name) for name in NAMES}
                    history = deepcopy(store.load_theme_snapshots())
                    calls.clear()
                    collections, completed_at = service.load_theme_snapshot()
                    self.assertEqual(expected, collections)
                    self.assertEqual(1, len(calls))
                    self.assertEqual(history, store.load_theme_snapshots())
                    self.assertEqual(original_head, store.load_accepted_theme_snapshot())
                    self.assertEqual(None if clear else original_head["received_at"], completed_at)
