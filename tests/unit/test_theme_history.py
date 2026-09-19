from __future__ import annotations

import tempfile
import unittest
import sqlite3
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.central_server.app import create_app
from kiwoom_monitor.central_server.config import CentralServerSettings
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.central_schema import central_schema_migrations
from kiwoom_monitor.central_server.schema_migrations import CentralSchemaMigrationRunner
from kiwoom_monitor.domain.research_contract import THEME_BACKUP_FORMAT, THEME_BACKUP_VERSION
from kiwoom_monitor.infrastructure.persistence.theme_backup import ThemeBackupService

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover - server extra가 없는 최소 개발 환경
    TestClient = None  # type: ignore[assignment,misc]


def _document(
    *, created_at: str = "2026-09-12T09:00:00+09:00",
    active: str = "기본 테마", assignments: tuple[str, ...] = ("005930",),
) -> dict[str, object]:
    return {
        "format": "kiwoom-realtime-monitor-theme-db",
        "version": 1,
        "created_at": created_at,
        "active_profile": active,
        "profiles": [
            {
                "name": "기본 테마",
                "themes": [{"name": "반도체", "color": "#fff"}],
                "stock_themes": [
                    {"code": code, "theme": "반도체", "color": None}
                    for code in assignments
                ],
            },
            {"name": "보조", "themes": [], "stock_themes": []},
        ],
        "aliases": [],
        "stock_catalog": [],
    }


def _snapshot(
    document: dict[str, object], *, origin: str = "pc-a", effective_at: str | None = None,
) -> list[dict[str, object]]:
    return [{
        "owner": "default", "key": "full", "document": document,
        "origin_device": origin,
        "effective_at": effective_at or str(document["created_at"]),
    }]


class ThemeHistoryRepositoryTests(unittest.TestCase):
    def test_history_source_contract_matches_theme_backup_format(self) -> None:
        self.assertEqual(ThemeBackupService.FORMAT, THEME_BACKUP_FORMAT)
        self.assertEqual(ThemeBackupService.VERSION, THEME_BACKUP_VERSION)

    def test_v4_database_migrates_without_changing_existing_documents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "central.sqlite3"
            connection = sqlite3.connect(path)
            try:
                CentralSchemaMigrationRunner(connection.cursor(), "sqlite").apply(
                    central_schema_migrations()[:4]
                )
                connection.execute(
                    "INSERT INTO central_documents VALUES(?,?,?,?,?)",
                    ("integration", "owner", "key", 1.0, '{"value":1}'),
                )
                connection.commit()
            finally:
                connection.close()

            store = SQLiteQueryStore(path)
            store.initialize()
            preserved = store.load_documents("integration")
            history = store.load_theme_snapshots()
            store.close()

        self.assertEqual(1, preserved[0]["document"]["value"])
        self.assertEqual([], history)

    def test_same_resend_is_deduplicated_but_deletion_and_active_switch_append(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            first = _document()
            resend = _document(created_at="2026-09-12T09:01:00+09:00")
            deleted = _document(created_at="2026-09-12T09:02:00+09:00", assignments=())
            switched = _document(
                created_at="2026-09-12T09:03:00+09:00", active="보조", assignments=(),
            )

            store.upsert_documents("theme_metadata", _snapshot(first))
            store.replace_documents("theme_metadata", _snapshot(resend))
            store.replace_documents("theme_metadata", _snapshot(deleted))
            store.replace_documents("theme_metadata", _snapshot(switched))
            history = store.load_theme_snapshots()
            store.close()

        self.assertEqual(3, len(history))
        newest, middle, oldest = history
        self.assertEqual("보조", newest["profile_id"])
        self.assertEqual([], middle["document"]["profiles"][0]["stock_themes"])
        self.assertEqual(["005930"], [
            value["code"] for value in oldest["document"]["profiles"][0]["stock_themes"]
        ])
        self.assertEqual(middle["snapshot_id"], newest["revision_of"])
        self.assertEqual(oldest["snapshot_id"], middle["revision_of"])
        self.assertIsNone(oldest["revision_of"])

    def test_late_delivery_keeps_its_real_availability_and_past_query_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            current = _document(created_at="2026-09-12T10:00:00+09:00")
            late = _document(
                created_at="2026-09-12T08:00:00+09:00", assignments=("000660",),
            )
            with patch("kiwoom_monitor.central_server.database.time", side_effect=[100.0, 101.0]):
                store.replace_documents("theme_metadata", _snapshot(current, origin="pc-a"))
            before_late = store.load_theme_snapshots(available_at=150.0)
            with patch("kiwoom_monitor.central_server.database.time", side_effect=[200.0, 201.0]):
                store.replace_documents("theme_metadata", _snapshot(late, origin="pc-b"))
            after_late = store.load_theme_snapshots()
            replay = store.load_theme_snapshots(available_at=150.0)
            store.close()

        self.assertEqual([value["snapshot_id"] for value in before_late], [
            value["snapshot_id"] for value in replay
        ])
        self.assertEqual(201.0, after_late[0]["available_at"])
        self.assertEqual("2026-09-12T08:00:00+09:00", after_late[0]["effective_at"])
        self.assertEqual("pc-b", after_late[0]["origin_device"])

    def test_invalid_replacement_rolls_back_projection_and_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            original = _document()
            store.replace_documents("theme_metadata", _snapshot(original))
            snapshot_id = store.load_theme_snapshots()[0]["snapshot_id"]

            with self.assertRaisesRegex(ValueError, "완전한 테마"):
                store.replace_documents("theme_metadata", [{
                    "owner": "default", "key": "full",
                    "document": {"format": "broken"},
                }])

            projection = store.load_documents("theme_metadata", "default")
            history = store.load_theme_snapshots()
            history[0]["document"]["active_profile"] = "호출자 변조"
            reloaded = store.load_theme_snapshots()
            store.close()

        self.assertEqual(original, projection[0]["document"])
        self.assertEqual([snapshot_id], [value["snapshot_id"] for value in history])
        self.assertEqual("기본 테마", reloaded[0]["document"]["active_profile"])


@unittest.skipIf(TestClient is None, "fastapi server extra is not installed")
class ThemeHistoryApiTests(unittest.TestCase):
    def test_history_endpoint_reports_unknown_before_first_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'central.sqlite3'}", "private-token",
            )
            headers = {"Authorization": "Bearer private-token"}
            with TestClient(create_app(settings)) as client:  # type: ignore[misc]
                unknown = client.get(
                    "/api/v1/themes/history?as_of=0", headers=headers,
                )
                saved = client.put(
                    "/api/v1/content/theme_metadata", headers=headers,
                    json={"documents": _snapshot(_document())},
                )
                known = client.get("/api/v1/themes/history", headers=headers)

        self.assertEqual(200, unknown.status_code)
        self.assertFalse(unknown.json()["known"])
        self.assertEqual(1, saved.json()["saved"])
        self.assertTrue(known.json()["known"])
        self.assertEqual("기본 테마", known.json()["snapshots"][0]["profile_id"])


if __name__ == "__main__":
    unittest.main()
