import asyncio
import copy
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from kiwoom_monitor.central_server.app import create_app
from kiwoom_monitor.central_server.config import CentralServerSettings
from kiwoom_monitor.presentation.api_settings_dialog import ApiSettingsDialog
from test_external_market_collector import _MemoryStore, _SyntheticCollector
from qt_settings_test_support import dispose_dialogs


class GatedCollector(_SyntheticCollector):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.entered, self.release = threading.Event(), threading.Event()
        self.calls = []

    def _fetch(self, instrument, contract, interval, range_value):
        self.calls.append((contract, interval))
        if len(self.calls) == 1:
            self.entered.set()
            if not self.release.wait(5): raise RuntimeError("fake gate timeout")
        return super()._fetch(instrument, contract, interval, range_value)


class ExternalMarketRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = _MemoryStore()
        self.collector = GatedCollector(self.store, {"WTI_FUTURES": "CLV26.NYM"})

    async def asyncTearDown(self):
        self.collector.release.set()
        await self.collector.close()

    async def until(self, condition):
        for _ in range(300):
            if condition(): return
            await asyncio.sleep(.005)
        self.fail("fake work did not reach boundary")

    async def test_cancelled_waiter_then_shutdown_waits_for_full_fetch_and_save(self):
        waiter = asyncio.create_task(self.collector.collect_once())
        await self.until(self.collector.entered.is_set)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError): await waiter
        closing = asyncio.create_task(self.collector.close())
        await self.until(lambda: self.collector._shutdown)
        self.assertFalse(closing.done())
        self.assertEqual([], self.store.bars)
        self.collector.release.set(); await closing
        self.assertEqual(4, len(self.collector.calls))
        self.assertEqual(6, len(self.store.bars))
        self.assertIn(("external_market_roll_state", "WTI_FUTURES"), self.store.documents)
        self.assertIsNone(self.collector._collection)

    async def test_cancelled_update_waiter_still_drains_and_applies_new_settings(self):
        collecting = asyncio.create_task(self.collector.collect_once())
        await self.until(self.collector.entered.is_set)
        updating = asyncio.create_task(self.collector.update_operational_settings(enabled=False,
            poll_seconds=120, auto_roll_enabled=False, roll_confirmations=5))
        await self.until(lambda: self.collector._closing)
        self.assertEqual(300, self.collector._poll_seconds)
        updating.cancel()
        with self.assertRaises(asyncio.CancelledError): await updating
        self.collector.release.set(); await collecting
        await self.until(lambda: not self.collector._updates)
        self.assertEqual(4, len(self.collector.calls))
        self.assertEqual(120, self.collector._poll_seconds)
        self.assertFalse(self.collector._auto_roll_enabled)
        self.assertEqual(5, self.collector._roll_confirmations)
        self.assertIsNone(self.collector._task)

    async def test_disable_enable_preserves_roll_history_daily_references_and_bars(self):
        self.collector.release.set()
        await self.collector.collect_once()
        references = copy.deepcopy(self.collector._daily_reference_closes)
        history = copy.deepcopy(self.store.documents)
        bars = copy.deepcopy(self.store.bars)
        self.collector._last_daily_date = "2026-09-15"
        await self.collector.update_operational_settings(enabled=False, poll_seconds=60,
            auto_roll_enabled=True, roll_confirmations=3)
        self.assertEqual(history, self.store.documents)
        self.assertEqual(bars, self.store.bars)
        with patch.object(self.collector, "_run", new=AsyncMock()):
            await self.collector.update_operational_settings(enabled=True, poll_seconds=90,
                auto_roll_enabled=True, roll_confirmations=3)
        self.assertEqual(references, self.collector._daily_reference_closes)
        self.assertEqual("2026-09-15", self.collector._last_daily_date)
        self.assertEqual(history, self.store.documents)

    async def test_new_daily_request_does_not_reuse_intraday_only_cycle(self):
        first = asyncio.create_task(self.collector.collect_once(include_daily=False))
        await self.until(self.collector.entered.is_set)
        second = asyncio.create_task(self.collector.collect_once(include_daily=True))
        self.collector.release.set()
        await asyncio.gather(first, second)
        self.assertEqual(2, sum(interval == "1d" for _, interval in self.collector.calls))

    async def test_shutdown_waits_for_cancelled_settings_operation(self):
        first = asyncio.create_task(self.collector.collect_once())
        await self.until(self.collector.entered.is_set)
        update = asyncio.create_task(self.collector.update_operational_settings(enabled=True,
            poll_seconds=120, auto_roll_enabled=False, roll_confirmations=2))
        await self.until(lambda: self.collector._updates)
        update.cancel()
        with self.assertRaises(asyncio.CancelledError): await update
        closing = asyncio.create_task(self.collector.close())
        await self.until(lambda: self.collector._shutdown)
        self.assertFalse(closing.done())
        self.collector.release.set(); await first; await closing
        self.assertIsNone(self.collector._task)
        self.assertFalse(self.collector._updates)


class ExternalMarketSettingsAPITests(unittest.TestCase):
    def test_runtime_off_on_and_persisted_off_wins_environment_on(self):
        from fastapi.testclient import TestClient
        with tempfile.TemporaryDirectory() as directory, patch(
                "kiwoom_monitor.central_server.app.YahooDelayedMarketCollector", _SyntheticCollector):
            database = f"sqlite:///{Path(directory) / 'central.sqlite'}"
            headers = {"Authorization": "Bearer token"}
            app = create_app(CentralServerSettings(database, "token", external_market_enabled=False))
            with TestClient(app) as client:
                collector = app.state.external_market_collector
                self.assertIsNone(collector._task)
                self.assertEqual(401, client.put("/api/v1/settings/operations", json={"external_market_enabled": True}).status_code)
                result = client.put("/api/v1/settings/operations", headers=headers, json={"expected_revision": 0,
                    "external_market_enabled": True, "external_market_poll_seconds": 120,
                    "external_market_auto_roll_enabled": False, "external_market_roll_confirmations": 4})
                self.assertEqual(200, result.status_code)
                self.assertEqual("ACTIVE", result.json()["apply_status"])
                self.assertIs(collector, app.state.external_market_collector)
                self.assertIsNotNone(collector._task)
                self.assertEqual(120, collector._poll_seconds)
                self.assertEqual(409, client.put("/api/v1/settings/operations", headers=headers,
                    json={"expected_revision": 0, "external_market_enabled": False}).status_code)
                disabled = client.put("/api/v1/settings/operations", headers=headers,
                    json={"expected_revision": 1, "external_market_enabled": False})
                self.assertEqual(200, disabled.status_code)
                self.assertIsNone(collector._task)
            restarted = create_app(CentralServerSettings(database, "token", external_market_enabled=True))
            with TestClient(restarted) as client:
                self.assertFalse(client.get("/api/v1/settings/operations", headers=headers).json()["external_market_enabled"])
                self.assertIsNone(restarted.state.external_market_collector._task)

    def test_invalid_fields_and_missing_symbols_are_rejected_before_save(self):
        from fastapi.testclient import TestClient
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(CentralServerSettings(f"sqlite:///{Path(directory) / 'central.sqlite'}", "token", external_market_symbols=""))
            headers = {"Authorization": "Bearer token"}
            with TestClient(app) as client:
                for body in ({"external_market_enabled": True}, {"external_market_enabled": 1},
                             {"external_market_poll_seconds": True}, {"external_market_poll_seconds": 59},
                             {"external_market_roll_confirmations": 0}):
                    self.assertEqual(422, client.put("/api/v1/settings/operations", headers=headers, json=body).status_code)
                self.assertEqual(0, client.get("/api/v1/settings/operations", headers=headers).json()["revision"])

    def test_apply_failure_is_pending_and_same_revision_can_recover(self):
        from fastapi.testclient import TestClient
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(CentralServerSettings(f"sqlite:///{Path(directory) / 'central.sqlite'}", "token"))
            headers = {"Authorization": "Bearer token"}
            with TestClient(app) as client:
                with patch.object(app.state.external_market_collector, "update_operational_settings", new=AsyncMock(side_effect=RuntimeError("fake"))):
                    self.assertEqual(503, client.put("/api/v1/settings/operations", headers=headers,
                        json={"external_market_poll_seconds": 120}).status_code)
                saved = client.get("/api/v1/settings/operations", headers=headers).json()
                self.assertEqual("RECOVERY_REQUIRED", saved["apply_status"])
                recovered = client.put("/api/v1/settings/operations", headers=headers,
                    json={"expected_revision": saved["revision"], "external_market_poll_seconds": 120}).json()
                self.assertEqual(saved["revision"], recovered["revision"])
                self.assertEqual("ACTIVE", recovered["apply_status"])

    def test_save_failure_keeps_previous_runtime_and_revision(self):
        from fastapi.testclient import TestClient
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(CentralServerSettings(f"sqlite:///{Path(directory) / 'central.sqlite'}", "token"))
            headers = {"Authorization": "Bearer token"}
            with TestClient(app) as client:
                collector = app.state.external_market_collector
                with patch.object(collector._store, "upsert_documents", side_effect=OSError("fake")):
                    self.assertEqual(503, client.put("/api/v1/settings/operations", headers=headers,
                        json={"external_market_poll_seconds": 120}).status_code)
                self.assertEqual(300, collector._poll_seconds)
                self.assertIsNone(collector._task)
                self.assertEqual(0, client.get("/api/v1/settings/operations", headers=headers).json()["revision"])


class ExternalMarketSettingsUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.qt = QApplication.instance() or QApplication([])

    def tearDown(self):
        dispose_dialogs()

    def test_old_server_does_not_send_new_fields_and_supported_fields_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            dialog = ApiSettingsDialog(Path(directory) / "api.env", section="nas")
            with patch("kiwoom_monitor.presentation.api_settings_dialog.apply_to_local_news"):
                dialog._operations_loaded({"ai_provider": "none"})
                self.assertFalse(dialog._nas_external_enabled.isEnabled())
                self.assertNotIn("external_market_enabled", dialog._nas_operation_values())
                values = {"external_market_enabled": True, "external_market_poll_seconds": 120,
                    "external_market_auto_roll_enabled": False, "external_market_roll_confirmations": 4}
                dialog._operations_loaded({"ai_provider": "none", **values})
                self.assertTrue(dialog._nas_external_enabled.isEnabled())
                self.assertEqual(values, {k: v for k, v in dialog._nas_operation_values().items() if k in values})
                dialog._operations_loaded({"ai_provider": "none", "apply_status": "RECOVERY_REQUIRED", **values})
                self.assertIn("실행 적용 확인", dialog._nas_operations_status.text())
                dialog._set_nas_operations_enabled(False)
                self.assertFalse(dialog._nas_external_poll.isEnabled())
            dialog.reject()
