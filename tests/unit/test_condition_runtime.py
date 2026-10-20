from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from kiwoom_monitor.central_server.app import create_app
from kiwoom_monitor.central_server.config import CentralServerSettings
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.market_events import MarketEventService
from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
from kiwoom_monitor.central_server import realtime_collector as collector_module
from kiwoom_monitor.presentation.api_settings_dialog import ApiSettingsDialog
from qt_settings_test_support import dispose_dialogs
from test_market_events import _Broker, _Socket, _HeldConditionStatusStore
from credential_owner_test_support import FakeClient


PAIRS = [["7", "상승 15%"], ["8", "새조건"], ["9", "다음조건"]]


def initial(seq, codes=(), **extra):
    return {"trnm": "CNSRREQ", "seq": seq, "return_code": 0,
            "data": [{"jmcode": code} for code in codes], **extra}


def live(seq, code, signal="I"):
    return {"trnm": "REAL", "data": [{"type": "02", "values": {
        "841": seq, "9001": code, "843": signal}}]}


class _CollectorConditionSocket(_Socket):
    def __init__(self):
        super().__init__()
        self.responses = asyncio.Queue()
        self.list_requested, self.trade_received = asyncio.Event(), asyncio.Event()

    async def send(self, raw):
        value = json.loads(raw)
        await super().send(raw)
        if value["trnm"] == "LOGIN":
            self.responses.put_nowait({"trnm": "LOGIN", "return_code": 0})
        elif value["trnm"] in {"REG", "REMOVE"}:
            self.responses.put_nowait({"trnm": value["trnm"], "return_code": 0})
        elif value["trnm"] == "CNSRLST":
            self.list_requested.set()

    async def recv(self):
        value = await self.responses.get()
        if any(row.get("type") == "0B" for row in value.get("data") or () if isinstance(row, dict)):
            self.trade_received.set()
        return json.dumps(value)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


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
        if self.service._condition_status_tail is not None:
            await asyncio.wait_for(asyncio.shield(self.service._condition_status_tail), 2)

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
        static_registered = asyncio.Event()

        class Socket(_Socket):
            def __init__(self):
                super().__init__()
                self.responses = asyncio.Queue()
                self.changed = False

            async def send(self, raw):
                value = json.loads(raw)
                is_static = value["trnm"] == "REG" and any(
                    row["type"] == ["0s"] for row in value["data"])
                if is_static:
                    # Condition promotion can finish before the independent REG send.
                    await complete.wait()
                await super().send(raw)
                if is_static:
                    static_registered.set()
                if value["trnm"] == "LOGIN":
                    self.responses.put_nowait({"trnm": "LOGIN", "return_code": 0})
                elif value["trnm"] in {"REG","REMOVE"}:
                    self.responses.put_nowait({"trnm":value["trnm"],"return_code":0})
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
                patch("kiwoom_monitor.central_server.realtime_collector.REALTIME_REG_INTERVAL_SECONDS", .01):
            receiving = asyncio.create_task(collector._receive("KRX"))
            try:
                await asyncio.wait_for(asyncio.gather(complete.wait(), static_registered.wait()), 3)
                self.assertEqual(1, connect.call_count)
                static_rows = [row for packet in socket.messages if packet['trnm']=='REG'
                               for row in packet['data'] if row['type']==['0s']]
                self.assertEqual(1,len(static_rows))
                self.assertEqual("ACTIVE", service.condition_status()["apply_status"])
            finally:
                receiving.cancel()
                await asyncio.gather(receiving, return_exceptions=True)

    async def test_status_native_ack_does_not_delay_approved_trade_decode_admission_or_publish(self):
        held = _HeldConditionStatusStore(self.store)
        self.service._store = held
        hub, socket = self.service._hub, _CollectorConditionSocket()
        observer = hub.connect()
        hub.update_subscription(observer, ["005930"], [])
        collector = CentralRealtimeCollector(lambda: "fake", "real", hub,
            lambda: datetime.fromisoformat("2026-09-15T10:00:00+09:00"), market_events=self.service)
        ready, decoded, admitted, published, heartbeat = (asyncio.Event() for _ in range(5))
        original_ready, original_publish = hub.set_upstream_ready, hub.publish
        original_filter = collector_module.filter_message
        trade = {"trnm": "REAL", "data": [{"type": "0B", "item": "005930",
                 "values": {"10": "+60700", "15": "2", "20": "100000"}}]}

        def observe_ready(codes, value, **kwargs):
            result = original_ready(codes, value, **kwargs)
            if value and "005930" in codes:
                ready.set()
            return result

        def observe_decode(raw, *args, **kwargs):
            result = json.loads(raw, *args, **kwargs)
            if result == trade:
                decoded.set()
            return result

        def observe_admission(message, allowed):
            result = original_filter(message, allowed)
            if message == trade:
                self.assertEqual(trade, result)
                admitted.set()
            return result

        def observe_publish(event, code=""):
            result = original_publish(event, code)
            if event.get("type") == "trade" and code == "005930":
                published.set()
            return result

        async def pulse():
            await socket.trade_received.wait()
            await asyncio.sleep(0)
            heartbeat.set()

        heartbeat_task = asyncio.create_task(pulse())
        with patch.object(collector_module, "connect", return_value=socket) as connect, \
                patch.object(collector_module, "REALTIME_REG_INTERVAL_SECONDS", .01), \
                patch.object(collector_module, "json", SimpleNamespace(loads=observe_decode, dumps=json.dumps)), \
                patch.object(collector_module, "filter_message", side_effect=observe_admission), \
                patch.object(hub, "set_upstream_ready", side_effect=observe_ready), \
                patch.object(hub, "publish", side_effect=observe_publish):
            await collector.start()
            try:
                await asyncio.wait_for(asyncio.gather(ready.wait(), socket.list_requested.wait()), 3)
                self.assertIn(("005930", "KRX"), collector._approved_trade_sources)
                socket.responses.put_nowait({"trnm": "CNSRLST", "data": PAIRS})
                self.assertTrue(await asyncio.to_thread(held.entered.wait, 2))
                socket.responses.put_nowait(trade)
                await asyncio.wait_for(asyncio.gather(published.wait(), heartbeat.wait()), 3)
                self.assertTrue(socket.trade_received.is_set())
                self.assertTrue(decoded.is_set())
                self.assertTrue(admitted.is_set())
                self.assertFalse(held.release.is_set())
                self.assertFalse(held.first_finished.is_set())
                self.assertEqual((1, 1), (len(held.attempts), held.max_active))
                self.assertEqual(1, connect.call_count)
                self.assertEqual("WAITING_REGISTER", self.service.condition_status()["apply_status"])
                self.assertEqual(("7", 0), (self.service._pending_condition.selected[0], self.service._pending_condition.revision))
                events = []
                while not observer.queue.empty():
                    events.append(observer.queue.get_nowait())
                self.assertEqual(1, sum(value.get("type") == "trade" for value in events))
                self.assertEqual(60700, collector._latest_snapshots[("trade", "005930")]["event"]["payload"]["current_price"])
            finally:
                held.release.set()
                if self.service._condition_status_tail is not None:
                    await asyncio.wait_for(asyncio.shield(self.service._condition_status_tail), 3)
                await collector.close()
                heartbeat_task.cancel()
                await asyncio.gather(heartbeat_task, return_exceptions=True)
                hub.disconnect(observer)
        self.assertEqual((1, 1, 0), (len(held.attempts), held.max_active, held.active))

    async def test_status_failure_keeps_collector_failure_ready_clear_and_three_second_reconnect(self):
        for lost_ack in (False, True):
            with self.subTest(lost_ack=lost_ack):
                held = _HeldConditionStatusStore(self.store, fail_first=True, commit_before_error=lost_ack)
                self.service._store = held
                hub = self.service._hub
                observer = hub.connect()
                hub.update_subscription(observer, ["005930"], [])
                sockets, connected = [_CollectorConditionSocket(), _CollectorConditionSocket()], []
                ready = [asyncio.Event(), asyncio.Event()]
                failed = asyncio.Event()
                failure_times = []
                collector = CentralRealtimeCollector(lambda: "fake", "real", hub,
                    lambda: datetime.fromisoformat("2026-09-15T10:00:00+09:00"), market_events=self.service)
                original_ready, original_publish = hub.set_upstream_ready, hub.publish

                def connect_socket(*args, **kwargs):
                    self.assertLess(len(connected), 2, "one status failure caused repeated reconnects")
                    socket = sockets[len(connected)]
                    connected.append(socket)
                    return socket

                def observe_ready(codes, value, **kwargs):
                    result = original_ready(codes, value, **kwargs)
                    if value and "005930" in codes:
                        ready[len(connected) - 1].set()
                    return result

                def observe_publish(event, code=""):
                    result = original_publish(event, code)
                    if event.get("type") == "connection_failed":
                        self.assertIn("controlled condition status write failure", event["message"])
                        failure_times.append(time.monotonic())
                        failed.set()
                    return result

                with patch.object(collector_module, "connect", side_effect=connect_socket), \
                        patch.object(collector_module, "REALTIME_REG_INTERVAL_SECONDS", .01), \
                        patch.object(hub, "set_upstream_ready", side_effect=observe_ready), \
                        patch.object(hub, "publish", side_effect=observe_publish):
                    await collector.start()
                    try:
                        await asyncio.wait_for(asyncio.gather(ready[0].wait(), sockets[0].list_requested.wait()), 3)
                        sockets[0].responses.put_nowait({"trnm": "CNSRLST", "data": PAIRS})
                        self.assertTrue(await asyncio.to_thread(held.entered.wait, 2))
                        self.assertEqual(1, len(connected))
                        held.release.set()
                        await asyncio.wait_for(failed.wait(), 3)
                        self.assertFalse(hub.upstream_ready_for({"005930"}))
                        self.assertEqual("FAILED", collector.credential_connection_status()["phase"])
                        await asyncio.wait_for(asyncio.gather(ready[1].wait(), sockets[1].list_requested.wait()), 6)
                        self.assertGreaterEqual(time.monotonic() - failure_times[0], 3)
                        sockets[1].responses.put_nowait({"trnm": "CNSRLST", "data": PAIRS})
                        self.assertTrue(await asyncio.to_thread(held.second_entered.wait, 2))
                        await asyncio.wait_for(asyncio.shield(self.service._condition_status_tail), 3)
                        self.assertTrue(hub.upstream_ready_for({"005930"}))
                        self.assertEqual((2, 1, 2, 1), (len(connected), len(failure_times), len(held.attempts), held.max_active))
                        self.assertIsNone(self.service._condition_status_error)
                    finally:
                        held.release.set()
                        await collector.close()
                        hub.disconnect(observer)

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
                kiwoom_app_key="fake", kiwoom_secret_key="fake", autonomous_top20_enabled=False,
                news_naver_market_enabled=False)
            app = create_app(settings)
            headers = {"Authorization": "Bearer token"}
            with TestClient(app) as client:
                service, socket = app.state.market_event_service, _Socket()
                service._broker = _Broker()
                async def status_ack():
                    await asyncio.shield(service._condition_status_tail)

                client.portal.call(service.on_ws_connected, socket)
                client.portal.call(service.handle_ws_message, {"trnm": "CNSRLST", "data": PAIRS}, socket)
                client.portal.call(status_ack)
                client.portal.call(service.handle_ws_message, initial("7"), socket)
                self.assertTrue(client.get("/api/v1/settings/operations", headers=headers).json()["condition_runtime_supported"])
                response = client.put("/api/v1/settings/operations", headers=headers, json={
                    "expected_revision": 0, "hot_cohort_condition_name": "새조건", "hot_cohort_condition_substring": ""})
                self.assertEqual(200, response.status_code)
                client.portal.call(service.poll_condition_updates, socket)
                client.portal.call(service.handle_ws_message, {"trnm": "CNSRLST", "data": PAIRS}, socket)
                client.portal.call(status_ack)
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
            app = create_app(CentralServerSettings(
                f"sqlite:///{Path(directory) / 'central.sqlite'}", "token",
                news_naver_market_enabled=False))
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
