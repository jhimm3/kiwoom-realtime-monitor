import asyncio
import io
import json
import os
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from urllib.error import HTTPError, URLError

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from fastapi.testclient import TestClient
from PySide6.QtWidgets import QApplication, QTableWidget, QTableWidgetItem
from kiwoom_monitor.central_server.app import create_app
from kiwoom_monitor.central_server.config import CentralServerSettings
from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
from kiwoom_monitor.central_server.rest_broker import CentralRestBroker
from kiwoom_monitor.infrastructure.central_server_config import DataSourceSettings
from kiwoom_monitor.infrastructure.kiwoom_rest.central_realtime_worker import CentralRealtimeWorker
from kiwoom_monitor.infrastructure.kiwoom_rest.client import KiwoomApiError
from kiwoom_monitor.infrastructure.kiwoom_rest.failover_client import FailoverKiwoomRestClient
from kiwoom_monitor.infrastructure.kiwoom_rest.remote_client import (
    CentralPlannedReconnect, CentralServerUnavailable, RemoteKiwoomRestClient,
)
from kiwoom_monitor.presentation.main_window import MainWindow
from test_failover_kiwoom_client import _Client
from test_mock_credential_owner import FakeClient


def status(generation=1, remaining=30, **extra):
    return {"generation": generation, "phase": "CONNECTING", "planned_reconnect": True,
            "remaining_seconds": remaining, "observation_expected": True, **extra}


class PlannedReconnectCollectorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hub = RealtimeHub()
        self.subscriber = self.hub.connect()
        self.hub.update_subscription(self.subscriber, ["005930"], [])
        self.collector = CentralRealtimeCollector(lambda: "fake", "real", self.hub,
                                                 lambda: datetime(2026, 9, 16, 10))

    async def asyncTearDown(self):
        await self.collector.close()

    async def expire(self):
        task = self.collector._reconnect_expiry_task
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.collector._reconnect_expiry_task = None
        self.collector._reconnect_deadline = time.monotonic() - 1
        await self.collector._expire_planned_reconnect()

    def events(self):
        values = []
        while not self.subscriber.queue.empty():
            values.append(self.subscriber.queue.get_nowait())
        return values

    async def test_begin_is_bounded_and_repeated_drain_does_not_renew_deadline(self):
        await self.collector.begin_credential_change()
        document = self.collector.credential_connection_status()
        deadline = self.collector._reconnect_deadline
        task = self.collector._reconnect_expiry_task
        self.assertTrue(document["planned_reconnect"])
        self.assertTrue(0 < document["remaining_seconds"] <= 30)
        self.assertEqual("PAUSED", document["phase"])
        await self.collector.begin_credential_change()
        self.assertEqual(deadline, self.collector._reconnect_deadline)
        self.assertIs(task, self.collector._reconnect_expiry_task)
        self.assertEqual(1, self.collector._connection_generation)
        await self.collector.close()
        self.assertTrue(task.done())
        self.assertFalse(self.collector.credential_connection_status()["planned_reconnect"])

    async def test_deadline_during_paused_drain_publishes_fault_without_unfencing(self):
        await self.collector.begin_credential_change()
        await self.expire()
        document = self.collector.credential_connection_status()
        self.assertFalse(document["planned_reconnect"])
        self.assertEqual("deadline_exceeded", document["outcome"])
        self.assertTrue(self.collector._credential_paused)
        self.assertEqual(1, sum(event["type"] == "connection_failed" for event in self.events()))

    async def test_closed_market_expires_to_waiting_without_false_fault(self):
        with patch.object(self.collector, "_market_session", return_value=None):
            await self.collector.begin_credential_change()
            self.assertFalse(self.collector.credential_connection_status()["observation_expected"])
            await self.expire()
        document = self.collector.credential_connection_status()
        self.assertFalse(document["planned_reconnect"])
        self.assertEqual("waiting_market", document["outcome"])
        self.assertFalse(any(event["type"] == "connection_failed" for event in self.events()))

    async def test_resume_start_failure_ends_grace_and_notifies_fault(self):
        await self.collector.begin_credential_change()
        with patch.object(self.collector, "start", new=AsyncMock(side_effect=RuntimeError("fake start"))):
            with self.assertRaises(RuntimeError): await self.collector.end_credential_change()
        document = self.collector.credential_connection_status()
        self.assertFalse(document["planned_reconnect"])
        self.assertEqual("FAILED", document["phase"])
        self.assertTrue(document["paused"])
        self.assertEqual(1, sum(event["type"] == "connection_failed" for event in self.events()))

    async def test_all_registration_acks_end_grace_and_first_trade_measures_gap(self):
        self.collector._last_trade_observed_at = "2026-09-16T01:00:00+00:00"
        self.collector._last_trade_observed_clock = time.monotonic() - 3
        await self.collector.begin_credential_change()
        self.collector._credential_paused = False
        self.collector._credential_phase = "CONNECTING"
        collector, hub = self.collector, self.hub
        finished = False
        assertion = self

        class Socket:
            reads = 0
            async def send(self, raw): pass
            async def recv(self):
                nonlocal finished
                self.reads += 1
                if self.reads == 1: return json.dumps({"return_code": 0})
                if self.reads == 3:
                    assertion.assertTrue(collector.credential_connection_status()["planned_reconnect"])
                    assertion.assertFalse(hub.upstream_ready)
                if self.reads in (2, 3): return json.dumps({"trnm": "REG", "return_code": 0})
                assertion.assertTrue(hub.upstream_ready)
                assertion.assertFalse(collector.credential_connection_status()["planned_reconnect"])
                finished = True
                return json.dumps({"trnm": "REAL", "data": [{"type": "0B", "item": "005930",
                    "values": {"20": "100030", "10": "100", "13": "10000", "15": "3"}}]})
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass

        groups = {"1000": [{"item": ["005930"], "type": ["0B"]}],
                  "1001": [{"item": ["005930"], "type": ["0w"]}]}
        with patch("kiwoom_monitor.central_server.realtime_collector.connect", return_value=Socket()), \
             patch.object(collector, "_send_subscription", new=AsyncMock(return_value=groups)), \
             patch.object(collector, "_market_session", side_effect=lambda: None if finished else "KRX"):
            await collector._receive("KRX")
        document = collector.credential_connection_status()
        self.assertEqual("registered", document["outcome"])
        self.assertIsNotNone(document["first_trade_at"])
        self.assertGreaterEqual(document["gap_seconds"], 3)
        self.assertEqual(3, collector._minute_bars.drain_dirty()[0]["volume"])

    async def test_unknown_previous_trade_is_not_reported_as_zero_gap(self):
        await self.collector.begin_credential_change()
        self.collector._observe_reconnect_trade()
        self.assertIsNone(self.collector.credential_connection_status()["first_trade_at"])
        self.collector._credential_paused = False
        self.collector._credential_phase = "READY"
        self.collector._finish_planned_reconnect("registered")
        self.collector._observe_reconnect_trade()
        self.assertIsNone(self.collector.credential_connection_status()["gap_seconds"])


class PlannedReconnectWorkerTests(unittest.TestCase):
    def worker(self):
        worker = CentralRealtimeWorker(DataSourceSettings("personal_server", "https://nas.test", "fake", True),
                                       ("005930",), fallback_factory=lambda codes, nxt: None)
        worker.failures = []
        worker.connection_failed.connect(worker.failures.append)
        return worker

    def test_planned_upstream_failure_preserves_route_without_failure_signal(self):
        worker = self.worker()
        worker._dispatch({"type": "connection_failed", "message": "temporary", "connection_status": status()})
        self.assertEqual([], worker.failures)
        self.assertEqual(0, worker._consecutive_failures)

    def test_repeated_metadata_cannot_extend_grace_and_deadline_restores_failover(self):
        worker = self.worker()
        with patch("kiwoom_monitor.infrastructure.kiwoom_rest.central_realtime_worker.time.monotonic", return_value=100):
            worker._dispatch({"type": "connection_status", "payload": status()})
        with patch("kiwoom_monitor.infrastructure.kiwoom_rest.central_realtime_worker.time.monotonic", return_value=110):
            worker._dispatch({"type": "connection_status", "payload": status()})
        self.assertEqual(130, worker._planned_deadline)
        with patch("kiwoom_monitor.infrastructure.kiwoom_rest.central_realtime_worker.time.monotonic", return_value=131):
            with self.assertRaises(CentralServerUnavailable): worker._check_planned_deadline()
        self.assertEqual(2, worker._consecutive_failures)
        self.assertEqual(1, len(worker.failures))

    def test_stale_generation_and_late_active_after_ready_cannot_restart_grace(self):
        worker = self.worker()
        worker._dispatch({"type": "connection_status", "payload": status(generation=2)})
        deadline = worker._planned_deadline
        worker._dispatch({"type": "connection_opened", "codes": ["005930"], "connection_status": status(generation=1)})
        self.assertEqual(deadline, worker._planned_deadline)
        worker._dispatch({"type": "connection_opened", "codes": ["005930"]})
        worker._dispatch({"type": "connection_status", "payload": status(generation=2)})
        self.assertEqual(0, worker._planned_deadline)

    def test_invalid_deadlines_are_not_used_to_hide_failure(self):
        for remaining in (True, -1, 0, 31, float("nan"), float("inf"), 10**1000):
            worker = self.worker()
            with self.subTest(remaining=remaining), self.assertRaises(CentralServerUnavailable):
                worker._dispatch({"type": "connection_failed", "connection_status": status(remaining=remaining)})

    def test_closed_market_deadline_is_waiting_not_failure(self):
        worker = self.worker()
        worker._dispatch({"type": "connection_status", "payload": status(observation_expected=False)})
        worker._planned_deadline = time.monotonic() - 1
        worker._check_planned_deadline()
        self.assertEqual([], worker.failures)
        self.assertEqual(0, worker._planned_deadline)

    def test_first_trade_gap_is_displayed_once_and_unknown_is_not_zero(self):
        for gap, expected in ((3.5, "3.50초"), (None, "이전 체결 관측 없음")):
            worker = self.worker()
            messages = []
            worker.status_changed.connect(messages.append)
            event = {"type": "connection_status", "payload": status(planned_reconnect=False,
                phase="READY", first_trade_at="2026-09-16T01:00:00+00:00", gap_seconds=gap)}
            worker._dispatch(event); worker._dispatch(event)
            self.assertEqual(1, len(messages))
            self.assertIn(expected, messages[0])


class PlannedReconnectHTTPTests(unittest.TestCase):
    def test_live_reconnect_error_is_not_transport_failover(self):
        def opener(request, **kwargs):
            raise HTTPError(request.full_url, 503, "fake", {}, io.BytesIO(json.dumps({"detail": {
                "code": "REALTIME_RECONNECTING", "connection_status": status()}}).encode()))
        primary = RemoteKiwoomRestClient("https://nas.test", "fake", opener=opener)
        fallback = _Client(result=({"local": True}, False, ""))
        client = FailoverKiwoomRestClient(primary, fallback)
        with self.assertRaises(CentralPlannedReconnect): client.request("ka10001", "/api/dostk/stkinfo", {})
        self.assertEqual(0, fallback.calls)
        self.assertFalse(client._using_fallback)

    def test_expired_or_unrelated_http_error_does_not_gain_planned_grace(self):
        for code, connection in (("REALTIME_RECONNECTING", status(remaining=0)), ("OTHER", status())):
            def opener(request, **kwargs):
                raise HTTPError(request.full_url, 503, "fake", {}, io.BytesIO(json.dumps({"detail": {
                    "code": code, "connection_status": connection}}).encode()))
            client = RemoteKiwoomRestClient("https://nas.test", "fake", opener=opener)
            with self.assertRaises(KiwoomApiError) as error: client.request("ka10001", "/info", {})
            self.assertNotIsInstance(error.exception, CentralPlannedReconnect)

    def test_real_nas_transport_failure_still_uses_local(self):
        def opener(*args, **kwargs): raise URLError("fake down")
        fallback = _Client(result=({"local": True}, False, ""))
        client = FailoverKiwoomRestClient(RemoteKiwoomRestClient("https://nas.test", "fake", opener=opener), fallback)
        self.assertEqual({"local": True}, client.request("ka10001", "/info", {}))
        self.assertEqual(1, fallback.calls)

    def test_health_capabilities_late_websocket_and_paused_rest_share_status(self):
        collectors = []
        brokers = []
        def factory(*args, **kwargs):
            collector = CentralRealtimeCollector(*args, **kwargs)
            collectors.append(collector)
            return collector
        def broker_factory(*args, **kwargs):
            broker = CentralRestBroker(*args, **kwargs)
            brokers.append(broker)
            return broker
        with tempfile.TemporaryDirectory() as directory, \
             patch("kiwoom_monitor.infrastructure.kiwoom_rest.KiwoomRestClient", FakeClient), \
             patch("kiwoom_monitor.central_server.app.CentralRealtimeCollector", side_effect=factory), \
             patch("kiwoom_monitor.central_server.app.CentralRestBroker", side_effect=broker_factory), \
             patch.object(CentralRealtimeCollector, "start", new=AsyncMock()):
            settings = CentralServerSettings(f"sqlite:///{Path(directory) / 'db.sqlite'}", "fake",
                kiwoom_app_key="a1", kiwoom_secret_key="fake", autonomous_top20_enabled=False,
                market_event_collection_enabled=False)
            app = create_app(settings)
            with TestClient(app, base_url="https://nas.test") as client:
                collector = collectors[0]
                client.portal.call(collector.begin_credential_change)
                client.portal.call(brokers[0].begin_credential_change)
                with patch.object(brokers[0], "request", new=AsyncMock()) as request:
                    self.assertTrue(client.get("/health").json()["realtime_connection"]["planned_reconnect"])
                    headers = {"Authorization": "Bearer fake"}
                    caps = client.get("/api/v1/capabilities", headers=headers).json()
                    self.assertTrue(caps["capabilities"]["planned_reconnect_v1"])
                    response = client.post("/api/v1/kiwoom/query", headers=headers, json={
                        "api_id": "ka10001", "path": "/api/dostk/stkinfo", "body": {"stk_cd": "005930"}})
                    self.assertEqual(503, response.status_code)
                    self.assertEqual("REALTIME_RECONNECTING", response.json()["detail"]["code"])
                    request.assert_not_called()
                    with client.websocket_connect("/api/v1/realtime", headers=headers) as socket:
                        self.assertTrue(socket.receive_json()["connection_status"]["planned_reconnect"])
                        socket.send_json({"type": "subscribe", "codes": ["005930"]})
                        self.assertEqual("subscribed", socket.receive_json()["type"])
                        self.assertTrue(socket.receive_json()["connection_status"]["planned_reconnect"])


class PlannedReconnectTableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def test_main_status_preserves_existing_ranking_cells(self):
        table = QTableWidget(1, 1)
        table.setItem(0, 0, QTableWidgetItem("기존 최신 순위"))
        owner = SimpleNamespace(_table=table, _active_api_route="central", _set_connected_api_status=Mock(),
                                statusBar=lambda: SimpleNamespace(showMessage=Mock()))
        MainWindow._on_realtime_status_changed(owner, "나스 실시간 연결 변경 중 · 기존 순위 유지")
        self.assertEqual("central_waiting", owner._active_api_route)
        self.assertEqual(1, table.rowCount())
        self.assertEqual("기존 최신 순위", table.item(0, 0).text())
        MainWindow._on_realtime_status_changed(owner, "나스 실시간 체결 재개 · 교체 전후 체결 관측 간격 3.50초")
        self.assertEqual("central", owner._active_api_route)
        self.assertEqual("기존 최신 순위", table.item(0, 0).text())
        table.deleteLater()
