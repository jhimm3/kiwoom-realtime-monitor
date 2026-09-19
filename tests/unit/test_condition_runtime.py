from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from kiwoom_monitor.central_server.app import create_app
from kiwoom_monitor.central_server.config import CentralServerSettings
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.market_events import MarketEventService
from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
from kiwoom_monitor.presentation.api_settings_dialog import ApiSettingsDialog
from qt_settings_test_support import dispose_dialogs
from test_market_events import _Broker, _Socket
from test_mock_credential_owner import FakeClient


PAIRS = [["7", "상승 15%"], ["8", "새조건"], ["9", "다음조건"]]


def initial(seq, codes=(), **extra):
    return {"trnm": "CNSRREQ", "seq": seq, "return_code": 0,
            "data": [{"jmcode": code} for code in codes], **extra}


def live(seq, code, signal="I"):
    return {"trnm": "REAL", "data": [{"type": "02", "values": {
        "841": seq, "9001": code, "843": signal}}]}


class ConditionRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SQLiteQueryStore(Path(self.temp.name) / "central.sqlite")
        self.store.initialize()
        self.broker, self.socket = _Broker(), _Socket()
        self.service = MarketEventService(self.broker, RealtimeHub(), self.store,
            now_provider=lambda: datetime(2026, 9, 15, 10))
        await self.service.start()
        await self.service.on_ws_connected(self.socket)
        await self.message({"trnm": "CNSRLST", "data": PAIRS})
        await self.message(initial("7", ["005930"]))
        await self.flush()

    async def asyncTearDown(self):
        await self.service.close()
        self.store.close()
        self.temp.cleanup()

    async def message(self, value):
        return await self.service.handle_ws_message(value, self.socket)

    async def flush(self):
        await asyncio.wait_for(self.service._signal_queue.join(), 2)
        await asyncio.wait_for(self.service._metadata_queue.join(), 2)

    async def change(self, name="새조건", enabled=True, substring=""):
        self.service.update_operational_settings(enabled=enabled, exact_name=name, substring=substring)
        await self.service.poll_condition_updates(self.socket)
        if enabled:
            await self.message({"trnm": "CNSRLST", "data": PAIRS})

    async def clear(self, seq, code=0):
        await self.message({"trnm": "CNSRCLR", "seq": seq, "return_code": code})

    async def test_paged_registration_orders_initial_before_live_and_preserves_old_cohort(self):
        await self.change()
        await self.message(initial("8", ["000660"], cont_yn="Y", next_key="page2"))
        await self.message(live("8", "000660", "D"))
        self.assertEqual(("7", "상승 15%"), self.service._selected)
        self.assertNotIn("000660", self.service._cohort)
        await self.message(initial("8", ["000660"], cont_yn="N"))
        self.assertEqual({"trnm": "CNSRCLR", "seq": "7"}, self.socket.messages[-1])
        await self.clear("7")
        await self.flush()
        self.assertEqual("ACTIVE", self.service.condition_status()["apply_status"])
        self.assertEqual("D", self.service._cohort["000660"]["last_signal"])
        self.assertEqual("새조건", self.service._cohort["000660"]["condition_name"])
        self.assertIn("005930", self.service._cohort)

    async def test_missing_or_ambiguous_selection_keeps_old_registration(self):
        for name, substring in (("없는조건", ""), ("", "조건")):
            await self.change(name=name, substring=substring)
            self.assertEqual("RECOVERY_REQUIRED", self.service.condition_status()["apply_status"])
            self.assertEqual(("7", "상승 15%"), self.service._selected)
        self.assertFalse(any(value["trnm"] == "CNSRCLR" for value in self.socket.messages))

    async def test_malformed_list_does_not_break_shared_websocket(self):
        self.service.update_operational_settings(enabled=True, exact_name="새조건", substring="")
        await self.service.poll_condition_updates(self.socket)
        self.assertTrue(await self.message({"trnm": "CNSRLST", "data": 7}))
        self.assertEqual("RECOVERY_REQUIRED", self.service.condition_status()["apply_status"])
        self.assertEqual(("7", "상승 15%"), self.service._selected)

    async def test_failed_registration_discards_buffer_and_requires_explicit_retry(self):
        await self.change()
        await self.message(live("8", "000660"))
        await self.message(initial("8", return_code=1))
        await self.clear("8")
        count = len(self.socket.messages)
        await self.service.poll_condition_updates(self.socket)
        self.assertEqual(count, len(self.socket.messages))
        self.assertEqual("RECOVERY_REQUIRED", self.service.condition_status()["apply_status"])
        self.assertNotIn("000660", self.service._cohort)
        await self.change()
        await self.message(initial("8"))
        await self.clear("7")
        self.assertEqual("ACTIVE", self.service.condition_status()["apply_status"])

    async def test_wrong_or_missing_runtime_seq_cannot_promote(self):
        await self.change()
        await self.message(initial("7", ["000660"]))
        await self.message(initial("", ["000660"]))
        self.assertEqual(("7", "상승 15%"), self.service._selected)
        self.assertIsNotNone(self.service._pending_condition)
        await self.message(initial("8", data="malformed"))
        self.assertIsNone(self.service._pending_condition)
        self.assertNotIn("000660", self.service._cohort)

    async def test_timeout_preserves_old_and_does_not_hot_loop(self):
        await self.change()
        self.service._condition_deadline = asyncio.get_running_loop().time() - 1
        await self.service.poll_condition_updates(self.socket)
        self.assertEqual("RECOVERY_REQUIRED", self.service.condition_status()["apply_status"])
        count = len(self.socket.messages)
        await self.service.poll_condition_updates(self.socket)
        self.assertEqual(count, len(self.socket.messages))
        self.assertEqual(("7", "상승 15%"), self.service._selected)

    async def test_off_on_keeps_service_tasks_and_saved_cohort(self):
        tasks, subscriber = list(self.service._tasks), self.service._subscriber
        calls = len(self.broker.calls)
        await self.change(enabled=False)
        await self.message(live("7", "000660"))
        await self.clear("7")
        await self.flush()
        self.assertEqual("OFF", self.service.condition_status()["apply_status"])
        self.assertNotIn("000660", self.service._cohort)
        self.assertIn("005930", self.service._cohort)
        self.assertEqual(tasks, self.service._tasks)
        self.assertIs(subscriber, self.service._subscriber)
        self.assertEqual(calls, len(self.broker.calls))
        await self.change()
        await self.message(initial("8"))
        self.assertEqual("ACTIVE", self.service.condition_status()["apply_status"])

    async def test_obsolete_list_snapshot_is_ignored_then_latest_policy_requested(self):
        self.service.update_operational_settings(enabled=True, exact_name="새조건", substring="")
        await self.service.poll_condition_updates(self.socket)
        self.service.update_operational_settings(enabled=True, exact_name="다음조건", substring="")
        await self.message({"trnm": "CNSRLST", "data": PAIRS})
        self.assertIsNone(self.service._pending_condition)
        await self.service.poll_condition_updates(self.socket)
        await self.message({"trnm": "CNSRLST", "data": PAIRS})
        self.assertEqual("9", self.socket.messages[-1]["seq"])

    async def test_obsolete_candidate_is_cleared_before_latest_policy_registration(self):
        await self.change()
        self.service.update_operational_settings(enabled=True, exact_name="다음조건", substring="")
        await self.message(initial("8", ["000660"]))
        await self.service.poll_condition_updates(self.socket)
        await self.clear("8")
        await self.service.poll_condition_updates(self.socket)
        await self.message({"trnm": "CNSRLST", "data": PAIRS})
        self.assertEqual("9", self.socket.messages[-1]["seq"])
        self.assertNotIn("000660", self.service._cohort)

    async def test_missing_or_repeated_continuation_cursor_rejects_partial_cohort(self):
        for cursor in ("", "repeat"):
            await self.change()
            await self.message(initial("8", ["000660"], cont_yn="Y", next_key=cursor))
            if cursor:
                await self.message(initial("8", ["000660"], cont_yn="Y", next_key=cursor))
            self.assertIsNone(self.service._pending_condition)
            self.assertNotIn("000660", self.service._cohort)
            await self.clear("8")

    async def test_queue_capacity_rejects_entire_candidate_before_promotion(self):
        await self.change()
        self.service._signal_queue = asyncio.Queue(maxsize=1)
        await self.message(initial("8", ["000660", "035420"]))
        self.assertEqual(0, self.service._signal_queue.qsize())
        self.assertEqual(("7", "상승 15%"), self.service._selected)

    async def test_queued_signal_keeps_condition_at_acceptance(self):
        self.service._queue_signal("000660", "I", "REAL", "")
        self.service._selected = ("8", "새조건")
        await self.flush()
        self.assertEqual("상승 15%", self.service._cohort["000660"]["condition_name"])

    async def test_existing_receive_loop_applies_policy_without_second_connection(self):
        service = self.service
        complete = asyncio.Event()

        class Socket(_Socket):
            def __init__(self):
                super().__init__()
                self.responses = asyncio.Queue()
                self.changed = False

            async def send(self, raw):
                await super().send(raw)
                value = json.loads(raw)
                if value["trnm"] == "LOGIN":
                    self.responses.put_nowait({"trnm": "LOGIN", "return_code": 0})
                elif value["trnm"] == "CNSRLST":
                    self.responses.put_nowait({"trnm": "CNSRLST", "data": PAIRS})
                elif value["trnm"] == "CNSRREQ":
                    self.responses.put_nowait(initial(value["seq"]))
                elif value["trnm"] == "CNSRCLR":
                    self.responses.put_nowait({"trnm": "CNSRCLR", "seq": value["seq"], "return_code": 0})

            async def recv(self):
                if self.responses.empty() and service.condition_status()["apply_status"] == "ACTIVE":
                    if not self.changed:
                        self.changed = True
                        service.update_operational_settings(enabled=True, exact_name="새조건", substring="")
                        return json.dumps({"trnm": "PING"})
                    if service._selected == ("8", "새조건"):
                        complete.set()
                return json.dumps(await self.responses.get())

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

        socket = Socket()
        collector = CentralRealtimeCollector(lambda: "fake", "real", service._hub,
            lambda: datetime(2026, 9, 15, 10), market_events=service)
        with patch("kiwoom_monitor.central_server.realtime_collector.connect", return_value=socket) as connect, \
                patch.object(collector, "_send_subscription", new=AsyncMock(return_value={})) as subscribe:
            receiving = asyncio.create_task(collector._receive("KRX"))
            try:
                await asyncio.wait_for(complete.wait(), 3)
                self.assertEqual(1, connect.call_count)
                self.assertEqual(1, subscribe.await_count)
                self.assertEqual("ACTIVE", service.condition_status()["apply_status"])
            finally:
                receiving.cancel()
                await asyncio.gather(receiving, return_exceptions=True)

    async def test_failed_clear_ack_is_visible_and_explicit_retry_reuses_active_condition(self):
        await self.change()
        await self.message(initial("8"))
        await self.clear("7", code=1)
        self.assertEqual("RECOVERY_REQUIRED", self.service.condition_status()["apply_status"])
        self.service.update_operational_settings(enabled=True, exact_name="새조건", substring="")
        await self.service.poll_condition_updates(self.socket)
        await self.clear("7")
        await self.service.poll_condition_updates(self.socket)
        await self.message({"trnm": "CNSRLST", "data": PAIRS})
        self.assertEqual("ACTIVE", self.service.condition_status()["apply_status"])


class ConditionOperationsAPITests(unittest.TestCase):
    def test_saved_policy_restart_and_late_registration_failure_retry(self):
        from fastapi.testclient import TestClient
        with tempfile.TemporaryDirectory() as directory, \
                patch("kiwoom_monitor.infrastructure.kiwoom_rest.KiwoomRestClient", FakeClient), \
                patch("kiwoom_monitor.central_server.app.CentralRealtimeCollector.start", new=AsyncMock()) as start:
            settings = CentralServerSettings(f"sqlite:///{Path(directory) / 'central.sqlite'}", "token",
                kiwoom_app_key="fake", kiwoom_secret_key="fake", autonomous_top20_enabled=False)
            app = create_app(settings)
            headers = {"Authorization": "Bearer token"}
            with TestClient(app) as client:
                service, socket = app.state.market_event_service, _Socket()
                service._broker = _Broker()
                client.portal.call(service.on_ws_connected, socket)
                client.portal.call(service.handle_ws_message, {"trnm": "CNSRLST", "data": PAIRS}, socket)
                client.portal.call(service.handle_ws_message, initial("7"), socket)
                self.assertTrue(client.get("/api/v1/settings/operations", headers=headers).json()["condition_runtime_supported"])
                response = client.put("/api/v1/settings/operations", headers=headers, json={
                    "expected_revision": 0, "hot_cohort_condition_name": "새조건", "hot_cohort_condition_substring": ""})
                self.assertEqual(200, response.status_code)
                client.portal.call(service.poll_condition_updates, socket)
                client.portal.call(service.handle_ws_message, {"trnm": "CNSRLST", "data": PAIRS}, socket)
                client.portal.call(service.handle_ws_message, initial("8", return_code=1), socket)
                client.portal.call(service.handle_ws_message, {"trnm": "CNSRCLR", "seq": "8", "return_code": 0}, socket)
                failed = client.get("/api/v1/settings/operations", headers=headers).json()
                self.assertEqual("RECOVERY_REQUIRED", failed["apply_status"])
                self.assertEqual(["7", "상승 15%"], failed["condition_status"]["active"])
                diagnostic = client.get("/api/v1/market/events?kind=cohort", headers=headers).json()
                self.assertEqual("RECOVERY_REQUIRED", diagnostic["condition"]["runtime"]["apply_status"])
                retry = client.put("/api/v1/settings/operations", headers=headers, json={"expected_revision": 1}).json()
                self.assertEqual(1, retry["revision"])
                self.assertEqual("WAITING_LIST", retry["condition_status"]["apply_status"])
                self.assertEqual(1, start.await_count)
                self.assertEqual(409, client.put("/api/v1/settings/operations", headers=headers,
                    json={"expected_revision": 0, "hot_cohort_condition_enabled": False}).status_code)
                policy_revision = service._condition_revision
                self.assertEqual(422, client.put("/api/v1/settings/operations", headers=headers,
                    json={"hot_cohort_condition_name": "", "hot_cohort_condition_substring": ""}).status_code)
                with patch.object(service._store, "upsert_documents", side_effect=OSError("fake")):
                    self.assertEqual(503, client.put("/api/v1/settings/operations", headers=headers,
                        json={"hot_cohort_condition_enabled": False}).status_code)
                self.assertEqual(policy_revision, service._condition_revision)
                self.assertTrue(service._condition_enabled)
                with patch.object(service, "update_operational_settings", side_effect=RuntimeError("fake")):
                    self.assertEqual(503, client.put("/api/v1/settings/operations", headers=headers,
                        json={"expected_revision": 1, "hot_cohort_condition_enabled": False}).status_code)
                saved = client.get("/api/v1/settings/operations", headers=headers).json()
                self.assertEqual("RECOVERY_REQUIRED", saved["apply_status"])
                recovered = client.put("/api/v1/settings/operations", headers=headers,
                    json={"expected_revision": 2, "hot_cohort_condition_enabled": False}).json()
                self.assertEqual(2, recovered["revision"])
                self.assertFalse(service._condition_enabled)
            restarted = create_app(settings)
            with TestClient(restarted) as client:
                restored = client.get("/api/v1/settings/operations", headers=headers).json()
                self.assertEqual("새조건", restored["hot_cohort_condition_name"])
                self.assertFalse(restored["hot_cohort_condition_enabled"])

    def test_auth_strict_fields_unsupported_and_save_failure_leave_old_policy(self):
        from fastapi.testclient import TestClient
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(CentralServerSettings(f"sqlite:///{Path(directory) / 'central.sqlite'}", "token"))
            headers = {"Authorization": "Bearer token"}
            with TestClient(app) as client:
                self.assertEqual(401, client.put("/api/v1/settings/operations", json={"hot_cohort_condition_enabled": False}).status_code)
                for body in ({"hot_cohort_condition_enabled": 1}, {"hot_cohort_condition_name": 1},
                             {"hot_cohort_condition_enabled": False}):
                    self.assertEqual(422, client.put("/api/v1/settings/operations", headers=headers, json=body).status_code)
                self.assertEqual(0, client.get("/api/v1/settings/operations", headers=headers).json()["revision"])


class ConditionSettingsUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.qt = QApplication.instance() or QApplication([])

    def tearDown(self):
        dispose_dialogs()

    def test_old_server_excludes_fields_and_supported_policy_roundtrips(self):
        from PySide6.QtCore import Qt
        with tempfile.TemporaryDirectory() as directory:
            dialog = ApiSettingsDialog(Path(directory) / "api.env", section="nas")
            with patch("kiwoom_monitor.presentation.api_settings_dialog.apply_to_local_news"):
                dialog._operations_loaded({"ai_provider": "none"})
                self.assertFalse(dialog._nas_condition_enabled.isEnabled())
                self.assertNotIn("hot_cohort_condition_enabled", dialog._nas_operation_values())
                fields = {"hot_cohort_condition_enabled": True, "hot_cohort_condition_name": "<새조건>",
                          "hot_cohort_condition_substring": ""}
                dialog._operations_loaded({"ai_provider": "none", "condition_runtime_supported": True,
                    "condition_status": {"apply_status": "RECOVERY_REQUIRED", "active": ["7", "<이전조건>"]}, **fields})
                self.assertEqual(fields, {key: dialog._nas_operation_values()[key] for key in fields})
                self.assertIn("적용 확인 필요", dialog._nas_condition_status.text())
                self.assertEqual(Qt.TextFormat.PlainText, dialog._nas_condition_status.textFormat())
                dialog._set_nas_operations_enabled(False)
                self.assertFalse(dialog._nas_condition_name.isEnabled())
            dialog.reject()
