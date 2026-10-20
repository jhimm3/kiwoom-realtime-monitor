from __future__ import annotations

import threading
import tempfile
import time
import unittest
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.infrastructure.central_content_sync import CentralContentSyncService
from kiwoom_monitor.infrastructure.central_theme_sync import CentralThemeSyncDispatcher
from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.infrastructure.persistence.stock_repository import StockRepository
from kiwoom_monitor.infrastructure.persistence.theme_backup import ThemeBackupService
from kiwoom_monitor.infrastructure.persistence.theme_repository import ThemeRepository


class _Service:
    def __init__(self) -> None:
        self.calls = 0
        self.called = threading.Event()
        self.remote_completed_at = None
        self.applied = 0
        self.upload_thread_id = None

    def load_theme_snapshot(self):
        return {"theme_profile": [], "theme_stock": [], "theme_metadata": []}, self.remote_completed_at

    def apply_theme_snapshot(self, _path: Path, _snapshot, *, expected_revision=None) -> None:
        self.applied += 1
        self.called.set()

    def replace_themes(self, _path: Path) -> None:
        self.calls += 1
        self.upload_thread_id = threading.get_ident()
        self.called.set()


class _FailOnceService(_Service):
    def replace_themes(self, _path: Path) -> None:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary outage")
        self.called.set()


class _FailService(_Service):
    def replace_themes(self, _path: Path) -> None:
        self.calls += 1
        self.called.set()
        raise RuntimeError("offline")


class CentralThemeSyncDispatcherTests(unittest.TestCase):
    def test_cap_noop_allows_remote_import_but_theme_and_catalog_edits_keep_conflict(self) -> None:
        first = datetime.fromisoformat("2030-01-02T09:00:00+09:00")
        received = datetime.fromisoformat("2030-01-02T09:00:01+09:00")
        for edit in ("cap_only", "price_receipt", "market_values", "theme", "catalog"):
            with self.subTest(edit=edit), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "monitor.sqlite3"
                Database(path).initialize()
                with closing(sqlite3.connect(path)) as connection, connection:
                    connection.execute(
                        "INSERT INTO stocks(code,name,market,last_price,last_price_updated_at,"
                        "market_cap,float_ratio,circulating_market_cap,fundamentals_updated_at,"
                        "updated_at) VALUES('000001','fixture','KOSPI',100,?,1000,50,500,?,?)",
                        (first.isoformat(), first.isoformat(), first.isoformat()),
                    )
                themes = ThemeRepository(path, now_provider=lambda: first)
                backup = ThemeBackupService(path)
                revision = backup.revision()
                before_document = backup.export_document()
                before_document.pop("created_at")
                remote = backup.export_document()
                remote["profiles"].append({
                    "name": "원격에서 추가", "themes": [], "stock_themes": [],
                    "theme_name_decisions": [], "theme_suggestions": [],
                })
                observed = {}

                class Client:
                    mutated = False

                    def load_all(self, collection, **_kwargs):
                        if not self.mutated:
                            self.mutated = True
                            StockRepository(path).update_price_cache(
                                {}, {}, {"000001": 1000.0}, first.date(), observed_at=received,
                            )
                            observed["cap_revision"] = backup.revision()
                            document = backup.export_document()
                            document.pop("created_at")
                            observed["cap_document"] = document
                            if edit in {"price_receipt", "market_values"}:
                                StockRepository(path).update_price_cache(
                                    {"000001": 100 if edit == "price_receipt" else 120},
                                    {}, {} if edit == "price_receipt" else {"000001": 1500.0},
                                    first.date(), observed_at=received,
                                )
                            elif edit == "theme":
                                themes.create_profile("사용자가 편집")
                            elif edit == "catalog":
                                StockRepository(path).upsert("000001", "사용자 이름", "KOSDAQ")
                            observed["edited_revision"] = backup.revision()
                        return [{"document": remote, "updated_at": 101.0}] if collection == "theme_metadata" else []

                    def replace(self, *_args, **_kwargs):
                        raise AssertionError("newer remote snapshot must use the import path")

                marker = Path(directory) / ".theme-pending"
                marker.write_text("100.0", encoding="utf-8")
                dispatcher = CentralThemeSyncDispatcher(
                    CentralContentSyncService(Client()), path, pending_path=marker,
                )  # type: ignore[arg-type]
                try:
                    # Keep the actual revision fence/import; only suppress asynchronous retries.
                    with patch.object(dispatcher, "_schedule_locked") as schedule, patch(
                        "kiwoom_monitor.infrastructure.central_theme_sync.time.time", return_value=101.0,
                    ):
                        result = dispatcher.flush_pending()
                    self.assertEqual(revision, observed["cap_revision"])
                    self.assertEqual(before_document, observed["cap_document"])
                    profiles = {profile["name"] for profile in backup.export_document()["profiles"]}
                    if edit in {"cap_only", "price_receipt", "market_values"}:
                        self.assertEqual(revision, observed["edited_revision"])
                        self.assertTrue(result)
                        self.assertEqual(0, schedule.call_count)
                        self.assertFalse(marker.exists())
                        self.assertIn("원격에서 추가", profiles)
                        with closing(sqlite3.connect(path)) as connection:
                            price, receipt, cap = connection.execute(
                                "SELECT last_price,last_price_updated_at,market_cap "
                                "FROM stocks WHERE code='000001'",
                            ).fetchone()
                        self.assertEqual(120 if edit == "market_values" else 100, price)
                        self.assertEqual(
                            received.isoformat() if edit != "cap_only" else first.isoformat(),
                            receipt,
                        )
                        self.assertEqual(1500 if edit == "market_values" else 1000, cap)
                    else:
                        self.assertNotEqual(revision, observed["edited_revision"])
                        self.assertFalse(result)
                        self.assertEqual(1, schedule.call_count)
                        self.assertTrue(marker.exists())
                        self.assertNotIn("원격에서 추가", profiles)
                        if edit == "theme":
                            self.assertIn("사용자가 편집", profiles)
                        else:
                            with closing(sqlite3.connect(path)) as connection:
                                self.assertEqual(("사용자 이름", "KOSDAQ"), connection.execute(
                                    "SELECT name,market FROM stocks WHERE code='000001'").fetchone())
                finally:
                    dispatcher.close()

    def test_edit_during_remote_load_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / ".theme-pending"
            marker.write_text("100.0", encoding="utf-8")
            service = _Service()
            service.remote_completed_at = 101.0
            dispatcher = CentralThemeSyncDispatcher(
                service, Path(directory) / "monitor.sqlite3",
                debounce_seconds=1.0, pending_path=marker,
            )  # type: ignore[arg-type]
            original = service.load_theme_snapshot

            def load_with_edit():
                dispatcher.notify()
                return original()

            service.load_theme_snapshot = load_with_edit  # type: ignore[method-assign]
            self.assertFalse(dispatcher.flush_pending())
            self.assertEqual(0, service.applied)
            self.assertTrue(marker.exists())
            dispatcher.close()

    def test_consecutive_changes_are_debounced_off_the_calling_thread(self) -> None:
        service = _Service()
        dispatcher = CentralThemeSyncDispatcher(service, Path("monitor.sqlite3"), debounce_seconds=0.05)  # type: ignore[arg-type]

        dispatcher.notify()
        dispatcher.notify()

        self.assertTrue(service.called.wait(1.0))
        self.assertEqual(1, service.calls)
        self.assertNotEqual(threading.get_ident(), service.upload_thread_id)
        dispatcher.close()

    def test_failed_upload_is_retried_and_clears_durable_pending_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / ".theme-pending"
            service = _FailOnceService()
            dispatcher = CentralThemeSyncDispatcher(
                service, Path(directory) / "monitor.sqlite3",
                debounce_seconds=0.05, retry_delays=(0.05,), pending_path=marker,
            )  # type: ignore[arg-type]

            dispatcher.notify()

            self.assertTrue(service.called.wait(1.0))
            self.assertEqual(2, service.calls)
            deadline = time.monotonic() + 1.0
            while marker.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(marker.exists())
            dispatcher.close()

    def test_pending_marker_survives_close_and_flushes_on_next_start(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / ".theme-pending"
            failed = _FailService()
            first = CentralThemeSyncDispatcher(
                failed, Path(directory) / "monitor.sqlite3",
                debounce_seconds=0.05, retry_delays=(1.0,), pending_path=marker,
            )  # type: ignore[arg-type]
            first.notify()
            self.assertTrue(failed.called.wait(1.0))
            first.close()
            self.assertTrue(marker.exists())

            recovered = _Service()
            second = CentralThemeSyncDispatcher(
                recovered, Path(directory) / "monitor.sqlite3", pending_path=marker,
            )  # type: ignore[arg-type]
            self.assertTrue(second.flush_pending())
            self.assertEqual(1, recovered.calls)
            self.assertFalse(marker.exists())
            second.close()

    def test_newer_nas_theme_is_applied_instead_of_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / ".theme-pending"
            marker.write_text("100.0", encoding="utf-8")
            service = _Service()
            service.remote_completed_at = 101.0
            dispatcher = CentralThemeSyncDispatcher(
                service, Path(directory) / "monitor.sqlite3", pending_path=marker,
            )  # type: ignore[arg-type]

            self.assertTrue(dispatcher.flush_pending())
            self.assertEqual(0, service.calls)
            self.assertEqual(1, service.applied)
            self.assertFalse(marker.exists())
            dispatcher.close()

    def test_newer_local_theme_is_uploaded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / ".theme-pending"
            marker.write_text("101.0", encoding="utf-8")
            service = _Service()
            service.remote_completed_at = 100.0
            dispatcher = CentralThemeSyncDispatcher(
                service, Path(directory) / "monitor.sqlite3", pending_path=marker,
            )  # type: ignore[arg-type]

            self.assertTrue(dispatcher.flush_pending())
            self.assertEqual(1, service.calls)
            self.assertEqual(0, service.applied)
            self.assertFalse(marker.exists())
            dispatcher.close()

    def test_legacy_nanosecond_pending_marker_is_compared_as_seconds(self) -> None:
        self.assertEqual(100.0, CentralThemeSyncDispatcher._pending_time("100000000000"))


if __name__ == "__main__":
    unittest.main()
