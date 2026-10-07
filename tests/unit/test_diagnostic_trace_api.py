"""Authenticated trace opt-ins reach native store and collector capture hooks."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from urllib.parse import urlsplit

from kiwoom_monitor.central_server import diagnostic_trace as trace
from kiwoom_monitor.central_server.app import create_app
from kiwoom_monitor.central_server.config import CentralServerSettings
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.diagnostic_replay_contract import capture_owner, thaw_payload
from kiwoom_monitor.central_server.diagnostic_workloads import _set_tool, control_snapshot
from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
from kiwoom_monitor.central_server.realtime_hub import RealtimeHub


HEADERS = {"Authorization": "Bearer private-token"}


def request_api(app, method, target, *, headers=None, **options):
    """Run the real HTTP ASGI stack; no HTTP client dependency or lifespan."""
    url = urlsplit(target)
    body = json.dumps(options['json']).encode() if 'json' in options else b''
    request_headers = {key.lower(): val for key, val in (headers or {}).items()}
    if body:
        request_headers['content-type'] = 'application/json'
    scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'},
             'http_version': '1.1', 'method': method, 'scheme': 'http',
             'path': url.path, 'raw_path': url.path.encode(), 'root_path': '',
             'query_string': url.query.encode(), 'state': {},
             'headers': [(key.encode(), val.encode()) for key, val in request_headers.items()],
             'client': ('127.0.0.1', 12345), 'server': ('testserver', 80)}

    async def exchange():
        messages = []
        consumed = False
        completed = asyncio.Event()

        async def receive():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {'type': 'http.request', 'body': body, 'more_body': False}
            await completed.wait()
            return {'type': 'http.disconnect'}

        async def send(message):
            messages.append(message)
            if message['type'] == 'http.response.body' and not message.get('more_body', False):
                completed.set()

        await asyncio.wait_for(app(scope, receive, send), timeout=15)
        if not completed.is_set():
            raise AssertionError('ASGI response was not complete')
        status = next(m['status'] for m in messages if m['type'] == 'http.response.start')
        content = b''.join(m.get('body', b'') for m in messages if m['type'] == 'http.response.body')
        return SimpleNamespace(status_code=status, content=content, text=content.decode(),
                               json=lambda: json.loads(content))

    return asyncio.run(exchange())


@contextmanager
def trace_api():
    with tempfile.TemporaryDirectory() as directory:
        control = Path(directory) / "control.json"
        store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
        store.initialize()
        settings = CentralServerSettings(f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token")
        with patch.dict(os.environ, {"KIWOOM_DIAGNOSTIC_WORKLOAD_PATH": str(control)}), \
                patch("kiwoom_monitor.central_server.app.create_query_store", return_value=store):
            # No lifespan: no workers, broker, operational DB, or external requests.
            app = create_app(settings)
            session = _set_tool(control, True, 300)["diagnostic_tool"]["session_id"]
            try:
                yield app, store, control, session
            finally:
                trace.stop()
                _set_tool(control, False)
                store.close()


class DiagnosticTraceApiTests(unittest.TestCase):
    def test_opt_ins_persist_native_operations_and_whitelisted_0b_independently(self):
        for options in ({}, {"store_inputs": True}, {"collector_inputs": True},
                        {"store_inputs": True, "collector_inputs": True}):
            with self.subTest(options=options), trace_api() as (app, store, _, session):
                response = request_api(app, "POST", "/api/v1/diagnostics/trace", headers=HEADERS,
                                       json={"seconds": 60, "expected_session": session, **options})
                self.assertEqual(200, response.status_code, response.text)
                started = response.json()
                flags = {key: options.get(key, False) for key in ("store_inputs", "collector_inputs")}
                self.assertEqual(flags, started["payload_capture"])
                self.assertEqual(2 if any(flags.values()) else 1, started["schema_version"])
                self.assertEqual(session, started["master_session"])
                for key, enabled in flags.items():
                    self.assertEqual(started["trace_id"] if enabled else None, trace.input_token(key))

                now = datetime(2026, 10, 6, 1, 4, 15, tzinfo=timezone.utc)
                collector = CentralRealtimeCollector(lambda: "", "real", RealtimeHub(), lambda: now, store)
                message = {"trnm": "REAL", "data": [
                    {"type": "0B", "item": "005930_AL", "values": {
                        "10": "10000", "13": "10", "14": "1", "15": "2", "17": "10000",
                        "20": "100415", "290": "2", "access_token": "excluded-0b-secret"}},
                    {"type": "00", "values": {"9201": "private-account", "access_token": "private-token"}},
                ]}
                collector._publish_parsed(message)
                payload = {"value": 42}
                with capture_owner("top20", "top20:test", "test-actor"):
                    store.save_dataset_snapshot("ranking", "api-opt-in", "key", payload)
                message["data"][0]["values"]["10"] = "77777"
                payload["value"] = 99

                stopped = request_api(app, "POST", "/api/v1/diagnostics/trace/stop", headers=HEADERS)
                self.assertEqual(200, stopped.status_code, stopped.text)
                manifest = stopped.json()
                self.assertEqual("complete", manifest["state"])
                self.assertEqual(0, manifest["known_dropped"])
                self.assertFalse(manifest["input_capture_censored"])
                self.assertEqual(42, store.load_dataset_snapshots("ranking", "api-opt-in")[0]["payload"]["value"])
                events = []
                if any(flags.values()):
                    _, events = trace.recorded_events(started["trace_id"])
                else:
                    self.assertEqual(0, manifest["payload_accepted"])
                    self.assertEqual({}, manifest["blobs"])
                operations = [row for row in events if row["event_type"] == "operation_start"]
                inputs = [row for row in events if row.get("input_kind") == "message"]
                self.assertEqual(int(flags["store_inputs"]), len(operations))
                self.assertEqual(int(flags["collector_inputs"]), len(inputs))
                if operations:
                    self.assertEqual("save_dataset_snapshot", operations[0]["method"])
                    self.assertEqual("test-actor", operations[0]["actor_id"])
                    ends = [row for row in events if row["event_type"] == "operation_end"]
                    self.assertEqual([operations[0]["operation_id"]], [row["operation_id"] for row in ends])
                    self.assertEqual("returned", ends[0]["outcome"])
                    self.assertEqual({"value": 42}, thaw_payload(operations[0]["payload"])["payload"])
                if inputs:
                    captured = thaw_payload(inputs[0]["payload"])
                    self.assertEqual("10000", captured["message"]["data"][0]["values"]["10"])
                    self.assertEqual({"00": 1}, inputs[0]["excluded_types"])
                for secret in ("private-token", "private-account", "excluded-0b-secret"):
                    self.assertNotIn(secret, json.dumps(events))
                for chunk in manifest["chunks"]:
                    downloaded = request_api(app, "GET", 
                        f"/api/v1/diagnostics/trace/{started['trace_id']}/chunks/{chunk['name']}", headers=HEADERS)
                    self.assertEqual(trace.chunk_bytes(started["trace_id"], chunk["name"]), downloaded.content)

    def test_auth_session_and_strict_boolean_errors_do_not_start_or_change_control(self):
        with trace_api() as (app, _, control, session):
            before = control.read_bytes()
            body = {"seconds": 60, "expected_session": session, "store_inputs": True, "collector_inputs": True}
            with patch.object(trace, "start") as start:
                self.assertEqual(401, request_api(app, "POST", "/api/v1/diagnostics/trace", json=body).status_code)
                self.assertEqual(409, request_api(app, "POST", "/api/v1/diagnostics/trace", headers=HEADERS,
                                                json={**body, "expected_session": "wrong"}).status_code)
                for key in ("store_inputs", "collector_inputs"):
                    for value in (1, "true", None):
                        with self.subTest(key=key, value=value):
                            self.assertEqual(422, request_api(app, "POST", "/api/v1/diagnostics/trace", headers=HEADERS,
                                                            json={**body, key: value}).status_code)
                start.assert_not_called()
            self.assertEqual(before, control.read_bytes())
            self.assertFalse(control_snapshot(control)["trace_capture"]["enabled"])

    def test_failed_start_clears_child_without_disabling_master(self):
        with trace_api() as (app, _, control, session):
            with patch.object(trace, "start", side_effect=ValueError("trace_storage_quota_exceeded")):
                response = request_api(app, "POST", "/api/v1/diagnostics/trace", headers=HEADERS,
                                       json={"seconds": 60, "expected_session": session, "store_inputs": True})
            self.assertEqual(409, response.status_code)
            self.assertEqual("trace_storage_quota_exceeded", response.json()["detail"])
            snapshot = control_snapshot(control)
            self.assertFalse(snapshot["trace_capture"]["enabled"])
            self.assertEqual(session, snapshot["diagnostic_tool"]["session_id"])
            # The real recorder rejects insufficient TTL and the API rolls back its child gate.
            _set_tool(control, False)
            session = _set_tool(control, True, 60)["diagnostic_tool"]["session_id"]
            response = request_api(app, "POST", "/api/v1/diagnostics/trace", headers=HEADERS,
                                   json={"seconds": 60, "expected_session": session, "collector_inputs": True})
            self.assertEqual(409, response.status_code)
            self.assertEqual("diagnostic_master_ttl_too_short", response.json()["detail"])
            snapshot = control_snapshot(control)
            self.assertFalse(snapshot["trace_capture"]["enabled"])
            self.assertTrue(snapshot["diagnostic_tool"]["enabled"])

    def test_capabilities_require_auth_and_advertise_scope_without_claiming_overhead_acceptance(self):
        with trace_api() as (app, _, control, _):
            before = control.read_bytes()
            self.assertEqual(401, request_api(app, "GET", "/api/v1/diagnostics/capabilities").status_code)
            response = request_api(app, "GET", "/api/v1/diagnostics/capabilities", headers=HEADERS)
            self.assertEqual(200, response.status_code)
            capture = response.json()["trace_input_capture"]
            self.assertEqual(3, capture["schema_version"])
            self.assertEqual({"store_inputs": False, "collector_inputs": False, "top20_inputs": False}, capture["options"])
            self.assertEqual(["0B", "0w", "0J", "0U"], capture["collector_event_types"])
            self.assertEqual("observed_paths_only", capture["coverage"])
            self.assertFalse(capture["overhead_verified"])
            self.assertEqual(before, control.read_bytes())
