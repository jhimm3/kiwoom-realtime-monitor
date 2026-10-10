from __future__ import annotations

import asyncio
import io
import itertools
import json
import os
import sqlite3
import tempfile
import time
import unittest
import uuid
from contextlib import closing, contextmanager, ExitStack
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from urllib.error import HTTPError
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from kiwoom_monitor.central_server.app import (
    _archived_chart_response,
    _stored_market_response,
    create_app,
)
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.config import CentralServerSettings
from kiwoom_monitor.central_server.market_observations import KST, ranking_observation
from kiwoom_monitor.application.breakout_strategy import default_shadow_breakout_config
from kiwoom_monitor.domain.market_data_contract import (
    DataCompleteness,
    MarketDataMetadata,
    MarketDataObservation,
    MarketDatasetKind,
)
from kiwoom_monitor.infrastructure.news_ai import NewsAIProviderError
from kiwoom_monitor.infrastructure.central_news_client import CentralNewsClient
from kiwoom_monitor.infrastructure.central_content_client import CentralContentClient
from kiwoom_monitor.infrastructure.central_journal_sync import CentralJournalSyncService
from kiwoom_monitor.infrastructure.central_content_sync import _journal_news_link_key
from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository


CONTENT_BASELINE = Path(__file__).resolve().parents[2] / "tests/fixtures/api_contract_baselines/content_http_v1.json"
MARKET_DATASET_BASELINE = Path(__file__).resolve().parents[2] / "tests/fixtures/api_contract_baselines/market_datasets_http_v1.json"
MARKET_READ_BASELINE = Path(__file__).resolve().parents[1] / "fixtures/api_contract_baselines/market_reads_http_v1.json"
MARKET_EVENT_BASELINE = Path(__file__).resolve().parents[1] / "fixtures/api_contract_baselines/market_events_http_v1.json"
MARKET_QUERY_BASELINE = Path(__file__).resolve().parents[1] / "fixtures/api_contract_baselines/market_query_http_v1.json"
ACCOUNT_QUERY_BASELINE = Path(__file__).resolve().parents[1] / "fixtures/api_contract_baselines/account_query_http_v1.json"
OPERATIONS_BASELINE = Path(__file__).resolve().parents[1] / "fixtures/api_contract_baselines/operations_http_v1.json"
MOCK_ORDERS_BASELINE = Path(__file__).resolve().parents[1] / "fixtures/api_contract_baselines/mock_orders_http_v1.json"
DIAGNOSTIC_READ_BASELINE = Path(__file__).resolve().parents[1] / "fixtures/api_contract_baselines/diagnostic_reads_http_v1.json"
DIAGNOSTIC_CONTROL_BASELINE = Path(__file__).resolve().parents[1] / "fixtures/api_contract_baselines/diagnostic_control_http_v1.json"
DIAGNOSTIC_RUN_BASELINE = Path(__file__).resolve().parents[1] / "fixtures/api_contract_baselines/diagnostic_runs_http_v1.json"
REALTIME_BASELINE = Path(__file__).resolve().parents[1] / "fixtures/api_contract_baselines/realtime_asgi_v1.json"
LIFESPAN_BASELINE = Path(__file__).resolve().parents[1] / "fixtures/api_contract_baselines/server_lifespan_v1.json"
LIFESPAN_CASES = (
    [("legacy", ""), ("external-disabled", ""), ("vault", "")]
    + [("legacy", name) for name in ("trace.recover", "broker.start", "identity.verify",
        "mock-bundle.start", "top20.start", "market.start", "collector.start", "news.start",
        "external.start", "candidate.start", "yield", "trace.stop:server_shutdown", "candidate.close", "broker.close")]
    + [("vault", name) for name in ("credentials.start", "real.start", "mock-owner.start",
        "supervisor.start", "credentials.close", "real.close")]
)


async def capture_server_lifespan_contract(app_factory, database_path, mode, failure="", *, probe_vault=False,
                                         original_behavior=False, also_fail=""):
    """Actual app lifespan/SQLite/vault; control network owners' start/close methods."""
    import importlib
    import threading
    from kiwoom_monitor.infrastructure.kiwoom_rest.account_identity import VerifiedAccountIdentity
    from kiwoom_monitor.domain.order_contract import AccountEnvironment
    from kiwoom_monitor.central_server.credential_store import CredentialStore

    events, injected = [], []
    loop_thread = threading.get_ident()
    store = SQLiteQueryStore(database_path)
    native_close = store.close
    vault = None
    native_vault_close = CredentialStore.close
    def event(name):
        events.append(name)
        if name in (failure, also_fail):
            injected.append(name)
            raise RuntimeError("controlled-" + name)
    def close_store():
        event("store.close")
        native_close()
    def close_vault(value):
        event("vault.close")
        native_vault_close(value)
    async def operation(name):
        event(name)
    async def verify(reader):
        event("identity.verify")
        return VerifiedAccountIdentity("kiwoom", AccountEnvironment.REAL, "1" * 64,
            datetime(2026, 10, 10, 3, tzinfo=timezone.utc))
    def recover():
        assert threading.get_ident() != loop_thread
        event("trace.recover")
    def stop(reason, timeout=10):
        assert threading.get_ident() != loop_thread
        assert original_behavior or timeout is None, "server shutdown must wait for actual trace completion"
        event("trace.stop:" + reason)
    class FixedNow(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime(2026, 10, 10, 3, tzinfo=timezone.utc)
            return value.astimezone(tz) if tz else value
    # Class methods are the real lifecycle seams; no closure-cell inspection.
    resources = (
        ("rest_broker", "CentralRestBroker", "broker"),
        ("real_runtime", "RealCredentialOwner", "real"),
        ("mock_runtime", "MockCredentialOwner", "mock-owner"),
        ("mock_runtime", "MockAccountBundle", "mock-bundle"),
        ("mock_automation_supervisor", "MockAutomationSupervisor", "supervisor"),
        ("autonomous_top20", "AutonomousTop20Service", "top20"),
        ("market_events", "MarketEventService", "market"),
        ("realtime_collector", "CentralRealtimeCollector", "collector"),
        ("news_service", "CentralNewsService", "news"),
        ("external_market_collector", "YahooDelayedMarketCollector", "external"),
        ("candidate_monitor", "CandidateMonitor", "candidate"),
        ("ai_service", "CentralAIService", "ai"),
        ("account_query", "AccountQuerySessionManager", "account-query"),
        ("credential_runtime", "CredentialRuntime", "credentials"),
    )
    outcome = "normal"
    error_notes = []
    try:
        with ExitStack() as stack:
            stack.enter_context(patch.dict(app_factory.__globals__, {"create_query_store": lambda *a, **k: store}))
            stack.enter_context(patch.object(store, "close", side_effect=close_store))
            stack.enter_context(patch.object(CredentialStore, "close", close_vault))
            for module_name, class_name, label in resources:
                cls = getattr(importlib.import_module("kiwoom_monitor.central_server." + module_name), class_name)
                for action in ("start", "close"):
                    if hasattr(cls, action):
                        async def owned_operation(*args, name=label + "." + action, **kwargs):
                            await operation(name)
                        stack.enter_context(patch.object(cls, action, owned_operation))
            stack.enter_context(patch("kiwoom_monitor.infrastructure.kiwoom_rest.account_identity.KiwoomAccountIdentityReader.verify", verify))
            stack.enter_context(patch("kiwoom_monitor.central_server.diagnostic_trace.recover_interrupted", recover))
            stack.enter_context(patch("kiwoom_monitor.central_server.diagnostic_trace.stop", stop))
            stack.enter_context(patch("kiwoom_monitor.central_server.schema_migrations.datetime", FixedNow))
            stack.enter_context(patch("uuid.uuid4", side_effect=(uuid.UUID(int=i) for i in range(1, 1000))))
            settings = CentralServerSettings(f"sqlite:///{database_path}", "private-token",
                kiwoom_app_key="controlled-key", kiwoom_secret_key="controlled-secret",
                credential_directory=str(database_path.parent / "vault") if mode == "vault" else "",
                account_identity_registry_enabled=True, account_identity_hmac_key="x" * 32,
                kiwoom_mock_app_key="controlled-mock-key", kiwoom_mock_secret_key="controlled-mock-secret",
                mock_account_monitor_enabled=True, mock_account_ref=str(uuid.UUID(int=99)),
                mock_execution_run_id=str(uuid.UUID(int=100)), top20_outbox_path=str(database_path.parent / "outbox.json"),
                naver_news_client_id="controlled-news-id", naver_news_client_secret="controlled-news-secret",
                ai_provider="openai", openai_api_key="controlled-ai-key", shadow_candidate_enabled=True,
                external_market_enabled=mode != "external-disabled", news_history_jobs_enabled=False)
            app = app_factory(settings)
            vault = app.state.credential_runtime.vault if app.state.credential_runtime is not None else None
            before_tasks = set(asyncio.all_tasks())
            try:
                async with app.router.lifespan_context(app):
                    event("yield")
                    # Observe publication through public app state, not private closure slots.
                    events.append("mock-bundle:" + ("present" if app.state.mock_account_bundle is not None else "absent"))
                    events.append("bindings:" + str(len(app.state.verified_account_bindings)))
            except RuntimeError as error:
                outcome = str(error)
                error_notes = list(getattr(error, "__notes__", []))
            await asyncio.sleep(0)
            assert set(asyncio.all_tasks()) <= before_tasks
            expected_injections = 2 if original_behavior and mode == "vault" and failure in ("credentials.close", "real.close") else 1
            expected_faults = ([failure] * expected_injections if failure else []) + ([also_fail] if also_fail else [])
            assert injected == expected_faults, (expected_faults, injected, events)
            # A mock bundle start error is intentionally isolated; other injected errors propagate.
            isolated_mock = failure == "mock-bundle.start" and not also_fail
            assert outcome == ("normal" if not failure or isolated_mock else "controlled-" + failure)
            result = {"mode": mode, "failure": failure, "events": events, "outcome": outcome,
                      "injected": injected, "owned_tasks_remaining": 0}
            if also_fail:
                result["shutdown"] = dict(app.state.shutdown_status)
                result["error_notes"] = error_notes
            if probe_vault and vault is not None:
                from kiwoom_monitor.central_server.credential_store import CredentialStoreError
                try:
                    second = CredentialStore(database_path.parent / "vault", store)
                except CredentialStoreError as error:
                    result["vault_reacquire"] = str(error)
                else:
                    native_vault_close(second)
                    result["vault_reacquire"] = "acquired"
            return result
    finally:
        # Fixture cleanup is separate from the recorded production lifecycle, even on assertion failure.
        if vault is not None:
            native_vault_close(vault)
        native_close()


def expected_server_lifespan_contract(mode, failure=""):
    """Reviewed failure policy, independent of the implementation's observed output."""
    startup = (["credentials.start", "trace.recover", "broker.start", "real.start",
                "mock-owner.start", "supervisor.start"] if mode == "vault" else
               ["trace.recover", "broker.start", "identity.verify", "mock-bundle.start"])
    startup += ["top20.start", "market.start", "collector.start", "news.start"]
    if mode != "external-disabled":
        startup += ["external.start"]
    startup += ["candidate.start", "yield", "mock-bundle:present",
                "bindings:0" if mode == "vault" else "bindings:1"]
    shutdown = ["trace.stop:server_shutdown"]
    shutdown += (["credentials.close", "supervisor.close", "real.close", "mock-owner.close"]
                 if mode == "vault" else ["mock-bundle.close"])
    shutdown += ["candidate.close", "external.close", "news.close", "ai.close", "top20.close",
                 "collector.close", "market.close"]
    if mode != "vault":
        shutdown += ["account-query.close"]
    shutdown += ["broker.close"]
    if mode == "vault":
        shutdown += ["vault.close"]
    shutdown += ["store.close"]
    if failure == "mock-bundle.start":
        index = startup.index(failure)
        startup.insert(index + 1, "mock-bundle.close")
        startup[startup.index("mock-bundle:present")] = "mock-bundle:absent"
        shutdown.remove("mock-bundle.close")
    elif failure in startup:
        startup = startup[:startup.index(failure) + 1]
    elif failure in shutdown:
        shutdown = shutdown[:shutdown.index(failure) + 1]
    return {"mode": mode, "failure": failure, "events": startup + shutdown,
            "outcome": "normal" if failure in ("", "mock-bundle.start") else "controlled-" + failure,
            "injected": [failure] if failure else [], "owned_tasks_remaining": 0}


async def capture_realtime_asgi_contract(app_factory, database_path, with_collector):
    """Native ASGI frames, hub and SQLite; only upstream collector/network are controlled."""
    from contextlib import asynccontextmanager
    from kiwoom_monitor.central_server.realtime_hub import RealtimeSubscriber

    records, tasks, calls = [], [], []
    store = SQLiteQueryStore(database_path)
    store.initialize()
    fresh = {"type": "trade", "payload": {"code": "005930", "current_price": 70000}}
    market = {"type": "market_state", "payload": {"market": "kospi"}}
    memory = {"type": "trade", "payload": {"code": "005930", "current_price": 71000}}
    store.save_realtime_snapshots([
        {"event_type": "market_state", "item_key": "kospi", "received_at": 1999, "event": market},
        {"event_type": "trade", "item_key": "005930", "received_at": 1999, "event": fresh},
        {"event_type": "trade", "item_key": "000660", "received_at": 100, "event": {"type": "stale"}},
        {"event_type": "trade", "item_key": "035420", "received_at": 1999, "event": {"type": "unrequested"}},
    ])

    class Collector:
        async def start(self): calls.append("collector.start")
        async def close(self): calls.append("collector.close")
        def credential_connection_status(self): return {"state": "controlled-upstream"}
        def initial_realtime_snapshots(self, snapshots, codes):
            calls.append({"initial": snapshots, "codes": codes})
            return [market, memory]

    @asynccontextmanager
    async def socket(app, *, authorization="", query=""):
        incoming, outgoing = asyncio.Queue(), asyncio.Queue()
        scope = {"type": "websocket", "asgi": {"version": "3.0", "spec_version": "2.4"},
                 "scheme": "ws", "path": "/api/v1/realtime", "raw_path": b"/api/v1/realtime",
                 "query_string": query.encode(), "root_path": "", "http_version": "1.1",
                 "headers": [(b"authorization", authorization.encode())],
                 "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 8787), "state": {}}
        await incoming.put({"type": "websocket.connect"})
        task = asyncio.create_task(app(scope, incoming.get, outgoing.put))
        tasks.append(task)
        async def read():
            frame = await asyncio.wait_for(outgoing.get(), 3)
            if frame["type"] == "websocket.send":
                frame = {"type": frame["type"], "document": json.loads(frame["text"])}
            records.append(frame)
            return frame
        async def write(document):
            await incoming.put({"type": "websocket.receive", "text": json.dumps(document)})
        try:
            yield read, write
        finally:
            await incoming.put({"type": "websocket.disconnect", "code": 1000})
            await asyncio.wait_for(task, 3)

    try:
        with ExitStack() as stack:
            stack.enter_context(patch.dict(app_factory.__globals__, {"create_query_store": lambda *a, **k: store,
                "CentralRealtimeCollector": lambda *a, **k: Collector()}))
            stack.enter_context(patch("kiwoom_monitor.central_server.database_realtime_snapshot.time", return_value=2000.0))
            settings = CentralServerSettings(f"sqlite:///{database_path}", "private-token",
                kiwoom_app_key="controlled-key" if with_collector else "",
                kiwoom_secret_key="controlled-secret" if with_collector else "",
                autonomous_top20_enabled=False, market_event_collection_enabled=False,
                external_market_symbols="", news_naver_stock_enabled=False)
            app = app_factory(settings)
            schema = app.openapi()
            routes = list(schema["paths"])
            hub = app.state.realtime_hub
            # A two-slot queue deterministically exercises native hub drop accounting.
            stack.enter_context(patch("kiwoom_monitor.central_server.realtime_hub.RealtimeSubscriber",
                side_effect=lambda **k: RealtimeSubscriber(queue=asyncio.Queue(maxsize=2), **k)))
            before_tasks = set(asyncio.all_tasks())
            async with app.router.lifespan_context(app):
                for authorization, query in (("", ""), ("Bearer bad", "token=private-token")):
                    async with socket(app, authorization=authorization, query=query) as (read, write):
                        assert (await read())["code"] == 4401
                        assert hub.client_count == 0
                async with socket(app, authorization="bEaReR private-token") as (read, write):
                    assert (await read())["type"] == "websocket.accept"
                    ready = (await read())["document"]
                    assert ready == {"type": "ready", "schema_version": 1,
                        "connection_status": {"state": "controlled-upstream"} if with_collector else None}
                    await write({"type": "PiNg"})
                    assert (await read())["document"] == {"type": "pong"}
                    hub.set_upstream_ready(("005930", "000660"), True)
                    await write({"type": "subscribe", "codes": ["005930", "000660", "005930", " "],
                                 "nxt_codes": ["005930"]})
                    assert (await read())["document"] == {"type": "subscribed", "codes": ["000660", "005930"],
                        "nxt_codes": ["005930"], "upstream_code_count": 2, "upstream_nxt_code_count": 1}
                    assert (await read())["document"] == market
                    assert (await read())["document"] == (memory if with_collector else fresh)
                    assert (await read())["document"]["type"] == "central_ready"
                    assert (await read())["document"] == {"type": "connection_opened", "scope": "client",
                        "codes": ["000660", "005930"]}
                    # Publish without yielding: oldest is dropped, gap precedes retained events.
                    for value in range(3): hub.publish({"type": "trade", "value": value}, "005930")
                    assert (await read())["document"] == {"type": "realtime_gap", "dropped_events": 1}
                    assert (await read())["document"] == {"type": "trade", "value": 1}
                    assert (await read())["document"] == {"type": "trade", "value": 2}
                    async with socket(app, authorization="Basic ignored", query="token=private-token") as (read2, write2):
                        await read2()
                        await read2()
                        assert hub.client_count == 2
                        await write2({"type": "subscribe", "codes": ["005930"], "nxt_codes": []})
                        assert (await read2())["document"]["upstream_code_count"] == 2
                        assert (await read2())["document"] == market
                        assert (await read2())["document"] == (memory if with_collector else fresh)
                        assert (await read2())["document"]["type"] == "central_ready"
                        assert (await read2())["document"]["type"] == "connection_opened"
                    assert hub.client_count == 1
                    hub.set_upstream_ready((), False)
                    await write({"type": "subscribe", "codes": [], "nxt_codes": []})
                    assert (await read())["document"]["codes"] == []
                    assert (await read())["document"] == market
                    if with_collector: assert (await read())["document"] == memory
                    assert (await read())["document"]["type"] == "central_ready"
                    # Ping is next, proving no connection_opened was sent while upstream is unready.
                    await write({"type": "ping"})
                    assert (await read())["document"] == {"type": "pong"}
                assert hub.client_count == 0
                assert hub.requested_codes() == ((), ())
            await asyncio.sleep(0)
            assert all(task.done() and not task.cancelled() for task in tasks)
            assert set(asyncio.all_tasks()) <= before_tasks
            with closing(sqlite3.connect(database_path)) as connection:
                rows = connection.execute("SELECT event_type,item_key,event_json,received_at FROM central_realtime_latest ORDER BY event_type,item_key").fetchall()
            return {"frames": records, "collector_calls": calls, "snapshot_rows": rows,
                    "schema": schema, "route_order": routes, "tasks_finished": len(tasks)}
    finally:
        store.close()


@contextmanager
def diagnostic_run_contract_app(app_factory, database_path, mode):
    """Native report/history readers; SQLite store and controlled sampler/start seams."""
    from kiwoom_monitor.central_server import diagnostic_sampling as sampling
    from kiwoom_monitor.central_server import diagnostic_workloads as workloads
    control_path = database_path.parent / "diagnostic.json"
    store = SQLiteQueryStore(database_path)
    state = {"calls": [], "start_error": None, "pg_error": False}
    class FixedNow(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime(2026, 10, 10, 3, tzinfo=timezone.utc)
            return value.astimezone(tz) if tz else value
    def host():
        state["calls"].append({"action": "host"})
        return {"cpu_percent": 3, "storage_mapping": {"disk": "controlled-disk"}}
    def postgres(database_url, *, sections, pid):
        state["calls"].append({"action": "postgres", "database_url": database_url, "sections": sorted(sections), "pid": pid})
        if state["pg_error"]: raise RuntimeError("injected-sensitive-diagnostic-detail")
        return {"sections": {key: {"value": key} for key in sorted(sections & {"postgres", "activity", "news_jobs"})}}
    def start(**kwargs):
        state["calls"].append({"action": "start", "kwargs": kwargs})
        if state["start_error"]: raise ValueError(state["start_error"])
        return {"run_id": "measure-report", "state": "running"}
    try:
        with ExitStack() as stack:
            stack.enter_context(patch("kiwoom_monitor.central_server.schema_migrations.datetime", FixedNow))
            stack.enter_context(patch("kiwoom_monitor.central_server.database_documents.time", return_value=2000.0))
            stack.enter_context(patch.object(workloads, "control_path", return_value=control_path if mode != "absent" else None))
            stack.enter_context(patch.object(sampling, "read_host_snapshot", side_effect=host))
            stack.enter_context(patch.object(sampling, "read_postgres_snapshot", side_effect=postgres))
            stack.enter_context(patch.dict(app_factory.__globals__, {"create_query_store": lambda *args, **kwargs: store}))
            stack.enter_context(patch("kiwoom_monitor.central_server.news_service.CentralNewsService.start", new_callable=AsyncMock))
            store.initialize()
            settings = CentralServerSettings("postgresql://contract.invalid/diagnostic_contract" if mode == "postgres-seam" else f"sqlite:///{database_path}",
                "private-token", autonomous_top20_enabled=False, market_event_collection_enabled=False,
                external_market_symbols="", news_history_jobs_enabled=False, news_naver_api_enabled=False,
                news_naver_stock_enabled=False, news_naver_market_enabled=False, news_query_set_enabled=False)
            app = app_factory(settings)
            runs = app.state.diagnostic_runs
            if runs is not None: stack.enter_context(patch.object(runs, "start", side_effect=start))
            directory = control_path.parent / "diagnostic-results"
            directory.mkdir()
            phase = {"db_calls_raw": {"calls": [1]}, "db_calls_raw_last_checkpoint": {"calls": [2]},
                     "market_bar_saves": {"kinds": {"minute": {"rows": 5, "call_samples": [3]}}}, "state": "complete"}
            for name, result in (("measure-report", {"kind": "measure", "phase": phase}),
                                 ("compare-report", {"kind": "compare", "phases": [phase, "non-dict", phase]}),
                                 ("legacy-report", {"kind": "measure", "phase": phase})):
                document = {"run_id": name, "kind": result["kind"], "state": "completed", "created_at": 2000, "schema": 1}
                document.update(result if name == "legacy-report" else {"result": result})
                (directory / (name + ".json")).write_text(json.dumps(document), encoding="utf-8")
            (control_path.parent / "diagnostic-history.jsonl").write_text('\n'.join([
                json.dumps({"command": "event-" + str(index), "at_utc": "2026-10-10T03:00:00Z"}) for index in range(3)
            ]) + '\ninvalid-json\n', encoding="utf-8")
            yield app, state, database_path, control_path, mode
            if runs is not None:
                assert runs._worker is None, "HTTP fixture unexpectedly started an actual sampling worker"
    finally:
        store.close()


def capture_diagnostic_run_http_contract(client, app, state, database_path, control_path, mode):
    import hashlib
    cases = []
    def files():
        return {str(path.relative_to(control_path.parent)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(control_path.parent.glob("diagnostic-results/*.json"))} | {
            "history": hashlib.sha256((control_path.parent / "diagnostic-history.jsonl").read_bytes()).hexdigest()}
    def request(name, method, path, *, status=200, authenticated=True, **kwargs):
        state["calls"].clear()
        before = files()
        with closing(sqlite3.connect(database_path)) as connection:
            storage = hashlib.sha256("\n".join(connection.iterdump()).encode()).hexdigest()
        response = client.request(method, "/api/v1/diagnostics/" + path,
            headers={"Authorization": "Bearer private-token"} if authenticated else {}, **kwargs)
        assert response.status_code == status, (name, status, response.status_code, response.text)
        assert files() == before, (name, "HTTP mutated report or history files")
        with closing(sqlite3.connect(database_path)) as connection:
            assert storage == hashlib.sha256("\n".join(connection.iterdump()).encode()).hexdigest(), (name, "HTTP mutated application DB")
        cases.append({"name": name, "status": response.status_code, "headers": dict(response.headers), "body": response.json(),
                      "files": before, "storage_hash": storage, "calls": list(state["calls"])})
        return response.json()
    paths = (("GET", "snapshot"), ("POST", "runs"), ("GET", "runs/missing"), ("POST", "runs/missing/cancel"),
             ("GET", "reports"), ("GET", "reports/missing"), ("GET", "history"))
    for method, path in paths: request("unauthorized-" + path, method, path, authenticated=False, status=401)
    request("invalid-run-model", "POST", "runs", status=422, json={"kind": "measure", "seconds": 4})
    request("extra-run-field", "POST", "runs", status=422, json={"kind": "measure", "seconds": 5, "extra": True})
    request("invalid-scenario", "POST", "runs", status=422, json={"kind": "replay", "seconds": 5, "query_minute_scenario": "unknown"})
    for params in ({"sections": ""}, {"sections": "unknown"}, {"sections": "host," * 30 + "host"}, {"pid": 0}, {"pid": -1}):
        request("snapshot-invalid-" + str(params), "GET", "snapshot", params=params, status=400)
    request("snapshot-host-storage", "GET", "snapshot", params={"sections": " host , storage "})
    request("snapshot-host", "GET", "snapshot", params={"sections": "host"})
    request("snapshot-storage", "GET", "snapshot", params={"sections": "storage"})
    request("invalid-report-mode-before-unavailable", "GET", "reports/missing", params={"mode": "other"}, status=400)
    if mode == "absent":
        for method, path in paths[1:]: request("control-unavailable-" + path, method, path, status=501, **({"json": {"kind": "measure", "seconds": 5}} if path == "runs" else {}))
        request("postgres-unavailable", "GET", "snapshot", status=501)
        return cases
    if mode == "postgres-seam":
        request("snapshot-all", "GET", "snapshot", params={"pid": 17})
        request("snapshot-subset", "GET", "snapshot", params={"sections": "activity,news_jobs", "pid": 18})
        state["pg_error"] = True
        request("snapshot-failure-sanitized", "GET", "snapshot", params={"sections": "postgres"}, status=503)
        state["pg_error"] = False
    else:
        request("snapshot-postgres-unavailable", "GET", "snapshot", status=501)
        request("run-postgres-unavailable", "POST", "runs", status=501, json={"kind": "measure", "seconds": 5})
        assert not state["calls"]
    for name in ("measure-report", "compare-report", "legacy-report"):
        request("summary-" + name, "GET", "reports/" + name)
        request("raw-after-summary-" + name, "GET", "reports/" + name, params={"mode": "raw"})
    request("status-persisted", "GET", "runs/measure-report")
    request("cancel-completed", "POST", "runs/measure-report/cancel", status=202)
    for method, path in (("GET", "runs/missing"), ("POST", "runs/missing/cancel"), ("GET", "reports/missing")):
        request("missing-" + path, method, path, status=404)
    for path in ("reports", "history"):
        request("page-" + path, "GET", path, params={"limit": 1, "offset": 0})
        request("page-tail-" + path, "GET", path, params={"limit": 1, "offset": 2})
        for params in ({"limit": 0}, {"limit": 101}, {"offset": -1}, {"offset": 100001}):
            request("invalid-page-" + path + str(params), "GET", path, params=params, status=400)
    if mode == "postgres-seam":
        body = {"kind": "replay", "seconds": 30, "label": "contract", "workload": "trace_synthetic",
                "request_id": "request-contract", "profile_report_id": "", "profile_trace_id": "trace-contract",
                "window_start_seconds": 1.5, "window_end_seconds": 31.5,
                "include_writer_kinds": ["query_minute"], "exclude_writer_kinds": ["news_job_claim"],
                "query_minute_scenario": "fresh_page", "expected_session": "session-contract", "expected_revision": 7}
        request("run-all-options", "POST", "runs", json=body, status=202)
        for error, status in (("invalid_diagnostic_run", 400), ("diagnostic_run_busy", 409), ("diagnostic_control_conflict", 409)):
            state["start_error"] = error
            request("run-error-" + error, "POST", "runs", json=body, status=status)
        state["start_error"] = None
    return json.loads(json.dumps(cases))


@contextmanager
def diagnostic_control_contract_app(app_factory, database_path, available):
    """Native control CAS/history; trace worker seam records calls without starting capture."""
    from types import SimpleNamespace
    from kiwoom_monitor.central_server import diagnostic_workloads as workloads
    from kiwoom_monitor.central_server import diagnostic_trace as trace
    control_path = database_path.parent / "diagnostic.json"
    state = {"trace": {"state": "idle"}, "calls": [], "start_error": None, "failure_state": "idle", "history_error": False}
    native_history = workloads._history
    class FixedNow(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime(2026, 10, 10, 3, tzinfo=timezone.utc)
            return value.astimezone(tz) if tz else value
    def history(*args, **kwargs):
        if state["history_error"]: raise OSError("injected history write failure")
        return native_history(*args, **kwargs)
    def start(**kwargs):
        state["calls"].append({"action": "start", "kwargs": kwargs})
        if state["start_error"]:
            state["trace"] = {"state": state["failure_state"]}
            raise ValueError(state["start_error"])
        state["trace"] = {"state": "running", "trace_id": "contract-trace", "options": kwargs}
        return dict(state["trace"])
    def status(trace_id=None):
        if trace_id is not None: state["calls"].append({"action": "status", "trace_id": trace_id})
        if trace_id == "missing": raise KeyError(trace_id)
        return dict(state["trace"])
    def stop(*args, **kwargs):
        state["calls"].append({"action": "stop"})
        state["trace"] = {**state["trace"], "state": "complete"}
        return dict(state["trace"])
    def chunk(trace_id, chunk_name):
        state["calls"].append({"action": "chunk", "trace_id": trace_id, "chunk_name": chunk_name})
        if chunk_name == "missing": raise KeyError(chunk_name)
        if chunk_name == "incomplete": raise ValueError("trace_chunk_incomplete")
        return b'{"metric":1}\n'
    with ExitStack() as stack:
        stack.enter_context(patch.object(workloads, "control_path", return_value=control_path if available else None))
        stack.enter_context(patch.object(workloads, "time", SimpleNamespace(time=lambda: 2000000000.0, monotonic=lambda: 10000.0)))
        stack.enter_context(patch.object(workloads, "datetime", FixedNow))
        stack.enter_context(patch.object(workloads, "uuid4", return_value=uuid.UUID(int=1)))
        stack.enter_context(patch.object(workloads, "_history", side_effect=history))
        for name, function in (("start", start), ("status", status), ("stop", stop), ("chunk_bytes", chunk)):
            stack.enter_context(patch.object(trace, name, side_effect=function))
        with diagnostic_read_contract_app(app_factory, database_path, False) as (app, store, request_store, _, _, _):
            yield app, control_path, state, database_path, available


def capture_diagnostic_control_http_contract(client, app, control_path, state, database_path, available):
    import hashlib
    cases = []
    prefix = "/api/v1/diagnostics/"
    def files():
        result = {}
        for filename in ("diagnostic.json", "diagnostic-history.jsonl"):
            path = control_path.parent / filename
            result[filename] = path.read_text(encoding="utf-8") if path.exists() else None
        return result
    def request(name, method, path, *, status=200, authenticated=True, **kwargs):
        with closing(sqlite3.connect(database_path)) as connection:
            before = hashlib.sha256("\n".join(connection.iterdump()).encode()).hexdigest()
        previous = files()
        state["calls"].clear()
        response = client.request(method, prefix + path, headers={"Authorization": "Bearer private-token"} if authenticated else {}, **kwargs)
        assert response.status_code == status, (name, status, response.status_code, response.text)
        with closing(sqlite3.connect(database_path)) as connection:
            assert before == hashlib.sha256("\n".join(connection.iterdump()).encode()).hexdigest(), (name, "control API changed application DB")
        if method == "GET" or status in {401, 422, 501}:
            assert previous == files(), (name, "read/rejected request changed native control")
        cases.append({"name": name, "status": response.status_code, "headers": dict(response.headers),
                      "body": response.json() if response.headers.get("content-type", "").startswith("application/json") else response.text,
                      "control_and_history": files(), "trace_calls": list(state["calls"]), "storage_hash": before})
        return response
    for method, path in (("GET", "capabilities"), ("PUT", "control"), ("POST", "trace"),
                         ("GET", "trace"), ("POST", "trace/stop"), ("GET", "trace/missing"), ("GET", "trace/id/chunks/name")):
        request("unauthorized-" + method + path, method, path, status=401, authenticated=False)
    request("capabilities", "GET", "capabilities")
    request("invalid-control-model", "PUT", "control", status=422, json={"target": "master", "enabled": True})
    request("strict-trace-model", "POST", "trace", status=422, json={"seconds": 60, "expected_session": "s", "store_inputs": "yes"})
    request("trace-initial", "GET", "trace")
    request("trace-manifest-missing", "GET", "trace/missing", status=404)
    request("trace-chunk-missing", "GET", "trace/id/chunks/missing", status=404)
    request("trace-chunk-conflict", "GET", "trace/id/chunks/incomplete", status=409)
    request("trace-chunk", "GET", "trace/id/chunks/chunk.ndjson")
    if not available:
        request("control-unavailable", "PUT", "control", status=501, json={"target": "master", "enabled": True, "expected_revision": 0})
        request("trace-unavailable", "POST", "trace", status=501, json={"seconds": 60, "expected_session": "s"})
        return cases
    master = {"target": "master", "enabled": True, "expected_revision": 0, "ttl_seconds": 7200, "expected_instance": "contract-instance"}
    response = request("master-enable", "PUT", "control", json=master).json()
    session = response["diagnostic_tool"]["session_id"]
    revision = lambda: json.loads(control_path.read_text(encoding="utf-8"))["control_revision"]
    request("master-repeat-idempotent", "PUT", "control", json={**master, "expected_revision": revision()})
    request("control-invalid-combination", "PUT", "control", status=422, json={**master, "paused": True})
    request("child-ttl-bounded", "PUT", "control", status=422, json={"target": "capture", "enabled": True, "expected_revision": revision(), "ttl_seconds": 3601})
    child = {"target": "capture", "enabled": True, "expected_revision": revision(), "expected_session": session}
    request("child-session-required", "PUT", "control", status=409, json={**child, "expected_session": None})
    request("child-stale-revision", "PUT", "control", status=409, json={**child, "expected_revision": 0})
    request("child-wrong-instance", "PUT", "control", status=409, json={**child, "expected_instance": "other"})
    request("capture-enable", "PUT", "control", json=child)
    workload = {"target": "workload", "paused": True, "workload": "external_market", "expected_session": session, "expected_revision": revision()}
    request("workload-pause", "PUT", "control", json=workload)
    state["history_error"] = True
    response = request("history-error-after-native-change", "PUT", "control", json={**workload, "paused": False, "expected_revision": revision()}).json()
    assert response["history_error_type"] == "OSError"
    state["history_error"] = False
    body = {"seconds": 60, "expected_session": session, "store_inputs": True, "collector_inputs": True, "top20_inputs": True, "persist_at": 2000000100.0}
    request("trace-stale-session", "POST", "trace", status=409, json={**body, "expected_session": "wrong"})
    for busy in ("running", "stopping", "awaiting_persistence", "persisting"):
        state["trace"] = {"state": busy}
        before = files()
        request("trace-busy-" + busy, "POST", "trace", status=409, json=body)
        assert before == files() and not state["calls"]
    state["trace"] = {"state": "idle"}
    state["start_error"] = "injected_trace_start_failure"
    request("trace-failure-rollback", "POST", "trace", status=409, json=body)
    assert json.loads(control_path.read_text(encoding="utf-8"))["trace"] is None
    state["failure_state"] = "persisting"
    request("trace-failure-owned-by-another-operation", "POST", "trace", status=409, json=body)
    assert json.loads(control_path.read_text(encoding="utf-8"))["trace"] is not None
    state["trace"], state["start_error"] = {"state": "idle"}, None
    request("trace-start-all-options", "POST", "trace", json=body)
    assert state["calls"] == [{"action": "start", "kwargs": {key: value for key, value in body.items() if key != "expected_session"}}]
    request("trace-current", "GET", "trace")
    request("trace-manifest", "GET", "trace/contract-trace")
    request("trace-stop", "POST", "trace/stop")
    request("master-disable-revokes-children", "PUT", "control", json={**master, "enabled": False, "expected_session": session, "expected_revision": revision()})
    return cases


@contextmanager
def diagnostic_read_contract_app(app_factory, database_path, configured):
    """Native storage/control readers; controlled host input and isolated metrics buffers."""
    from collections import deque
    from types import SimpleNamespace
    from kiwoom_monitor.central_server import diagnostic_metrics as metrics
    control_path = database_path.parent / "diagnostic.json"
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {"KIWOOM_DIAGNOSTIC_WORKLOAD_PATH": str(control_path)}))
        stack.enter_context(patch("kiwoom_monitor.central_server.diagnostic_workloads.instance_id", return_value="contract-instance"))
        for name in ("_SAVES", "_WRITERS", "_DB_CALLS"):
            stack.enter_context(patch.object(metrics, name, deque(maxlen=getattr(metrics, name).maxlen)))
        for name in ("_DROPPED_SAVES", "_DROPPED_WRITERS", "_DROPPED_DB_CALLS", "_CAPTURE_REFRESH_AT", "_CAPTURE_EXPIRES_MONOTONIC"):
            stack.enter_context(patch.object(metrics, name, 0))
        stack.enter_context(patch.object(metrics, "_CAPTURE_ENABLED", False))
        stack.enter_context(patch.object(metrics, "_CAPTURE_SESSION_ID", None))
        stack.enter_context(patch.object(metrics, "_PRODUCER_ID", "contract-producer"))
        stack.enter_context(patch.object(metrics, "getpid", return_value=123))
        def host(path, size): return {"host_input": "controlled-host", "database_size_bytes": size}
        stack.enter_context(patch("kiwoom_monitor.central_server.resource_usage.resource_usage", side_effect=host))
        stack.enter_context(patch.dict(app_factory.__globals__, {"resource_usage": host}))
        with operations_contract_app(app_factory, database_path, configured) as (app, store, request_store, services, events, monitors, condition):
            news = services["news"]
            news._job_runner = configured
            news._naver_api_enabled, news._naver_stock_enabled, news._dart_enabled = False, configured, False
            news._query_collector = SimpleNamespace(_enabled=False)
            news._market_collector = SimpleNamespace(_enabled=False)
            services["external"].diagnostic_status.return_value = {
                "configured": True, "operational_enabled": True, "running": True,
                "poll_seconds": 300, "collection_attempts": 2, "collection_completions": 1,
                "collection_saved_rows_total": 3, "last_collection_started_at": "2026-10-10T03:00:00Z",
                "last_collection_completed_at": "2026-10-10T03:00:01Z", "last_collection_saved_rows": 3,
                "last_collection_error": None,
            }
            yield app, store, request_store, control_path, configured, database_path


def capture_diagnostic_read_http_contract(client, app, store, request_store, control_path, configured, database_path):
    import hashlib
    cases = []
    prefix = "/api/v1/diagnostics/"
    def snapshot():
        with closing(sqlite3.connect(database_path)) as connection:
            return hashlib.sha256("\n".join(connection.iterdump()).encode()).hexdigest()
    def request(name, path, *, status=200, authenticated=True, **kwargs):
        before = snapshot()
        response = client.get(prefix + path, headers={"Authorization": "Bearer private-token"} if authenticated else {}, **kwargs)
        assert status == response.status_code, (name, status, response.status_code, response.text)
        assert before == snapshot(), (name, "diagnostic read changed application database")
        cases.append({"name": name, "status": response.status_code, "headers": dict(response.headers),
                      "body": response.json(), "storage_hash": before})
        return response.json()
    paths = ("resources", "workloads", "market-bar-saves", "writers", "db-calls",
             "news-job-claim-plan", "news-job-claim-readonly-analyze")
    for path in paths:
        request("unauthorized-" + path, path, authenticated=False, status=401)
    request("resources", "resources")
    request("writers", "writers")
    request("workloads-disabled", "workloads")
    for path in ("market-bar-saves", "db-calls"):
        request("missing-" + path, path, status=422)
        for start, end in ((10, 10), (10, 9), (0, 1801)):
            request("range-" + path + str(end), path, params={"start": start, "end": end}, status=400)
        request("valid-" + path, path, params={"start": 0, "end": 1800})
    for values in ({"mode": "bad"}, {"limit": 0}, {"limit": 501}, {"slow_ms": 0}, {"slow_ms": 30001}):
        request("db-bounded-" + str(values), "db-calls", params={"start": 0, "end": 1, **values}, status=400)
    for mode in ("verbose", "raw"):
        request("db-mode-" + mode, "db-calls", params={"start": 0, "end": 1, "mode": mode, "limit": 500, "slow_ms": 30000})
    request("sqlite-plan-unavailable", "news-job-claim-plan", status=501)
    request("sqlite-analyze-unavailable-first", "news-job-claim-readonly-analyze", params={"stage": "AI"}, status=501)
    with patch.object(request_store, "explain_news_job_claim_plan", create=True, return_value={"nodes": ["controlled-plan"]}) as plan:
        request("postgres-plan-seam", "news-job-claim-plan")
        plan.assert_called_once_with()
        plan.return_value = {"large": "x" * 1_048_577}
        request("plan-too-large", "news-job-claim-plan", status=503)
    with patch.object(request_store, "analyze_news_job_claim_read_only", create=True, return_value={"mode": "controlled-readonly", "nodes": []}) as analyze:
        for stage in ("BODY", "RULE"):
            request("analyze-" + stage, "news-job-claim-readonly-analyze", params={"stage": stage})
        request("analyze-invalid", "news-job-claim-readonly-analyze", params={"stage": "AI"}, status=400)
        assert [call.args for call in analyze.call_args_list] == [("BODY",), ("RULE",)]
    control_path.write_text(json.dumps({"schema": 1, "instance_id": "contract-instance", "control_revision": 7,
        "diagnostic_tool": {"expires_at": 4102444800, "owner": "contract", "session_id": "contract-session"},
        "leases": {"external_market": 4102444800}, "capture": {"expires_at": 4102444800, "owner": "contract"}}), encoding="utf-8")
    document = request("workloads-paused", "workloads")
    assert document["workloads"]["external_market"]["paused_by_diagnostic"]
    assert not document["workloads"]["external_market"]["effective"]
    if configured:
        response = client.put("/api/v1/settings/operations", headers={"Authorization": "Bearer private-token"},
                              json={"shadow_candidate_enabled": True})
        assert response.status_code == 200, response.text
        assert app.state.candidate_monitor is not None
        document = request("workloads-after-candidate-publication", "workloads")
        assert document["workloads"]["candidate_monitor"]["configured"]
    return cases


@contextmanager
def mock_orders_contract_app(app_factory, database_path, enabled):
    """Native ledger/runtime/gateways; fake external transport and owner publication seam."""
    from types import SimpleNamespace
    from tests.unit.test_market_profile_settings import account_settings_contract_app
    from kiwoom_monitor.application.order_lifecycle import OrderLifecycle
    from kiwoom_monitor.central_server.execution_runtime import ExecutionRuntime, ManualMockOrderGateway
    from kiwoom_monitor.domain.order_contract import AccountBinding, AccountEnvironment, AccountScope, AccountSnapshot, BrokerSubmission, OrderIntent, OrderSide, OrderType
    from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository
    from kiwoom_monitor.infrastructure.kiwoom_rest.mock_execution import SubmissionUnknown
    from datetime import timedelta
    now = datetime(2026, 9, 15, tzinfo=timezone.utc)
    class FixedNow(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now

    with account_settings_contract_app(app_factory, database_path, "mock" if enabled else "absent") as (app, store, request_store, _, owner, refs):
        bundles, transports = {}, {}
        publication = {"current": "contract-mock", "during_submit": None}
        with ExitStack() as stack:
            stack.enter_context(patch("kiwoom_monitor.central_server.execution_runtime.datetime", FixedNow))
            stack.enter_context(patch("kiwoom_monitor.central_server.database_execution.datetime", FixedNow))
            for index, profile in enumerate(("contract-mock", "contract-mock-b"), 1):
                ref = refs["mock"] if index == 1 else str(uuid.UUID(int=22))
                run = "run-" + profile
                repository = ExecutionRepository(store)
                # A previous run is real stored data, deliberately outside the current bundle.
                repository.create(OrderIntent("old-" + profile, "old-run", "old-decision", ref, "mock", "005930", "KRX",
                    OrderSide.BUY, 1, OrderType.LIMIT, 70000, now, now + timedelta(minutes=1), "test/v1"))
                class Transport:
                    def __init__(self, name):
                        self.name, self.calls, self.unknown, self.serial = name, [], False, 0
                    def submit(self, intent):
                        self.serial += 1
                        self.calls.append({"action": "submit", "intent": vars(intent)})
                        if publication["during_submit"] is not None:
                            publication["current"] = publication["during_submit"]
                            publication["during_submit"] = None
                        if self.unknown: raise SubmissionUnknown("injected response lost")
                        return BrokerSubmission("broker-" + self.name + "-" + str(self.serial), now)
                    def cancel(self, intent, broker_order_id, quantity=0):
                        self.calls.append({"action": "cancel", "intent": vars(intent), "broker_order_id": broker_order_id, "quantity": quantity})
                        return BrokerSubmission("cancel-" + self.name, now)
                transport = Transport(profile)
                lifecycle = OrderLifecycle(repository, transport, now_provider=lambda: now)
                runtime = ExecutionRuntime(lifecycle, repository, account_ref=ref, run_id=run, owner_token="owner-" + profile, now_provider=lambda: now)
                runtime.start()
                async def refresh(ref=ref): return AccountSnapshot(ref, 1000000, 0, {}, now)
                gateway = ManualMockOrderGateway(runtime, repository, refresh, now_provider=lambda: now)
                binding = AccountBinding(profile, AccountScope("kiwoom", AccountEnvironment.MOCK, ref), 1, now)
                bundles[profile] = SimpleNamespace(binding=binding, account_ref=ref, run_id=run, repository=repository, gateway=gateway, runtime=runtime, account_queries=None)
                transports[profile] = transport
            if owner is not None:
                owner.bundle.side_effect = lambda profile=None: bundles.get(profile or publication["current"])
            try:
                yield app, store, bundles, transports, publication
            finally:
                for bundle in bundles.values():
                    asyncio.run(bundle.gateway.close())
                    assert not bundle.gateway._commands and bundle.gateway._closed
                    bundle.runtime.stop()


def capture_mock_orders_http_contract(client, app, store, bundles, transports, publication, database_path, enabled):
    import hashlib
    cases = []
    legacy = "/api/v1/mock/orders"
    a, b = bundles["contract-mock"], bundles["contract-mock-b"]
    owned_gateways = tuple(bundle.gateway for bundle in bundles.values())
    def order_url(bundle): return "/api/v2/mock/accounts/" + bundle.account_ref + "/orders"
    def scope(bundle): return {"account_scope": bundle.binding.scope.to_dict(), "credential_profile_id": bundle.binding.credential_profile_id, "expected_binding_revision": 1}
    def query(bundle): return {"credential_profile_id": bundle.binding.credential_profile_id, "expected_binding_revision": 1, "environment": "mock"}
    command = {"request_id": "http-first", "symbol": "005930", "side": "BUY", "quantity": 1, "limit_price": 70000}
    def request(name, method, path, *, status=200, authenticated=True, **kwargs):
        with closing(sqlite3.connect(database_path)) as connection:
            before = hashlib.sha256("\n".join(connection.iterdump()).encode()).hexdigest()
        for transport in transports.values(): transport.calls.clear()
        response = client.request(method, path, headers={"Authorization": "Bearer private-token"} if authenticated else {}, **kwargs)
        assert response.status_code == status, (name, status, response.status_code, response.text)
        with closing(sqlite3.connect(database_path)) as connection:
            after = hashlib.sha256("\n".join(connection.iterdump()).encode()).hexdigest()
        if method == "GET" or status in {401, 404, 422}:
            assert before == after, (name, "read/rejected request changed native ledger")
        assert all(not gateway._commands for gateway in owned_gateways), "owned command still running after response"
        cases.append({"name": name, "status": response.status_code, "headers": dict(response.headers), "body": response.json(),
            "storage_hash": after, "transport_calls": {profile: list(transport.calls) for profile, transport in transports.items()}})
        return response.json()
    events = "/api/v2/mock/accounts/" + a.account_ref + "/execution-events"
    for method, path, body in (("POST", legacy, command), ("GET", legacy + "/missing", None),
            ("POST", legacy + "/missing/cancel", {}), ("POST", order_url(a), {**command, **scope(a)}),
            ("GET", order_url(a) + "/missing", None), ("POST", order_url(a) + "/missing/cancel", scope(a)), ("GET", events, None)):
        request("unauthorized-" + method + path, method, path, authenticated=False, status=401, **({"json": body} if body is not None else {}))
    request("invalid-legacy", "POST", legacy, json={}, status=422)
    request("strict-scoped-quantity", "POST", order_url(a), json={**command, **scope(a), "quantity": "1"}, status=422)
    request("scoped-extra", "POST", order_url(a), json={**command, **scope(a), "extra": True}, status=422)
    request("missing-read-context", "GET", order_url(a) + "/missing", status=422)
    request("invalid-event-limit", "GET", events, params={**query(a), "limit": 1001}, status=422)
    request("invalid-cancel", "POST", order_url(a) + "/missing/cancel", json={**scope(a), "quantity": -1}, status=422)
    if not enabled:
        request("legacy-disabled", "POST", legacy, json=command, status=503)
        request("scoped-owner-absent", "POST", order_url(a), json={**command, **scope(a)}, status=503)
        return json.loads(json.dumps(cases))
    request("wrong-binding", "POST", order_url(a), json={**command, **scope(a), "expected_binding_revision": 2}, status=409)
    request("wrong-account", "POST", order_url(a), json={**command, **scope(b)}, status=409)
    first = request("legacy-submit", "POST", legacy, json=command)
    request("legacy-duplicate-no-resend", "POST", legacy, json=command)
    assert not transports["contract-mock"].calls
    request("legacy-request-conflict", "POST", legacy, json={**command, "quantity": 2}, status=400)
    request("legacy-read", "GET", legacy + "/" + first["intent_id"])
    request("legacy-missing", "GET", legacy + "/missing", status=404)
    request("legacy-cancel", "POST", legacy + "/" + first["intent_id"] + "/cancel", json={"quantity": 0})
    owned = request("scoped-submit", "POST", order_url(a), json={**command, **scope(a)})
    request("scoped-duplicate-no-resend", "POST", order_url(a), json={**command, **scope(a)})
    assert not transports["contract-mock"].calls
    foreign = request("second-account-same-request", "POST", order_url(b), json={**command, **scope(b)})
    assert owned["intent_id"] != foreign["intent_id"]
    request("foreign-record-hidden", "GET", order_url(a) + "/" + foreign["intent_id"], params=query(a), status=404)
    request("old-run-hidden", "GET", order_url(a) + "/old-contract-mock", params=query(a), status=404)
    request("foreign-cancel-rejected", "POST", order_url(a) + "/" + foreign["intent_id"] + "/cancel", json=scope(a), status=404)
    assert not any(transport.calls for transport in transports.values())
    request("scoped-read", "GET", order_url(a) + "/" + owned["intent_id"], params=query(a))
    page = request("event-first-page", "GET", events, params={**query(a), "limit": 1})
    request("event-next-page", "GET", events, params={**query(a), "after_sequence": page["next_cursor"], "limit": 100})
    request("scoped-cancel", "POST", order_url(a) + "/" + owned["intent_id"] + "/cancel", json=scope(a))
    for error, legacy_status, scoped_status in ((ValueError("private error"), 400, 409), (RuntimeError("private error"), 502, 503)):
        with patch.object(a.gateway, "submit_limit", new=AsyncMock(side_effect=error)) as fail:
            request("legacy-error-" + type(error).__name__, "POST", legacy, json={**command, "request_id": "error"}, status=legacy_status)
            request("scoped-error-" + type(error).__name__, "POST", order_url(a), json={**command, **scope(a)}, status=scoped_status)
            assert fail.await_count == 2
    for error, legacy_status, scoped_status in ((KeyError("private error"), 404, 404), (ValueError("private error"), 409, 409), (RuntimeError("private error"), 502, 503)):
        with patch.object(a.gateway, "cancel", new=AsyncMock(side_effect=error)) as fail:
            request("legacy-cancel-error-" + type(error).__name__, "POST", legacy + "/" + first["intent_id"] + "/cancel", json={}, status=legacy_status)
            request("scoped-cancel-error-" + type(error).__name__, "POST", order_url(a) + "/" + owned["intent_id"] + "/cancel", json=scope(a), status=scoped_status)
            assert fail.await_count == 2
    transports["contract-mock"].unknown = True
    unknown_command = {**command, "request_id": "unknown"}
    unknown = request("response-loss", "POST", legacy, json=unknown_command)
    assert unknown["state"] == "SUBMISSION_UNKNOWN"
    request("unknown-duplicate-no-resend", "POST", legacy, json=unknown_command)
    assert not transports["contract-mock"].calls
    transports["contract-mock"].unknown = False
    publication["during_submit"] = "contract-mock-b"
    request("publication-during-submit-keeps-gateway", "POST", legacy, json={**command, "request_id": "publication"})
    publication["current"] = "contract-mock"
    gateway = a.gateway
    a.gateway = None
    try:
        request("scoped-read-with-orders-off", "GET", order_url(a) + "/" + owned["intent_id"], params=query(a))
        request("scoped-submit-orders-off", "POST", order_url(a), json={**command, **scope(a)}, status=503)
        request("scoped-cancel-orders-off", "POST", order_url(a) + "/" + owned["intent_id"] + "/cancel", json=scope(a), status=503)
    finally: a.gateway = gateway
    return json.loads(json.dumps(cases, default=lambda value: value.isoformat(), ensure_ascii=False))


@contextmanager
def operations_contract_app(app_factory, database_path, configured):
    """Native settings writes and candidate construction; isolate network/task startup."""
    from kiwoom_monitor.central_server.candidate_monitor import CandidateMonitor
    from kiwoom_monitor.central_server.rest_broker import CentralRestBroker
    from kiwoom_monitor.central_server.market_events import MarketEventService
    from kiwoom_monitor.central_server.external_market_collector import YahooDelayedMarketCollector
    from kiwoom_monitor.central_server.news_service import CentralNewsService
    from kiwoom_monitor.central_server.ai_service import CentralAIService
    class FixedNow(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime(2026, 10, 10, 3, tzinfo=timezone.utc)
            return value.astimezone(tz) if tz else value

    store = SQLiteQueryStore(database_path)
    events, monitors, app_stores = [], [], []
    services = {name: MagicMock(spec=cls) for name, cls in (("condition", MarketEventService),
                ("external", YahooDelayedMarketCollector), ("news", CentralNewsService), ("ai", CentralAIService))}
    for name, service in services.items():
        service.start, service.close = AsyncMock(), AsyncMock()
        def update(*args, name=name, **kwargs):
            events.append({"action": name + "-apply", "args": args, "kwargs": kwargs})
        service.update_operational_settings = AsyncMock(side_effect=update) if name == "external" else MagicMock(side_effect=update)
    condition = {"apply_status": "ACTIVE"}
    services["condition"].condition_status.side_effect = lambda: dict(condition)
    broker = MagicMock(spec=CentralRestBroker)
    broker.start, broker.close = AsyncMock(), AsyncMock()
    broker._client = MagicMock()
    broker._client.server_now.return_value = FixedNow.now(KST)
    original_store, original_candidate = app_factory.__globals__["create_query_store"], CandidateMonitor.from_json

    def owned_store(*args, **kwargs):
        value = original_store(*args, **kwargs)
        app_stores.append(value)
        return value

    def candidate(*args, **kwargs):
        monitor = original_candidate(*args, **kwargs)
        index = len(monitors)
        monitor.start = AsyncMock(side_effect=lambda: events.append({"action": "candidate-start", "index": index}))
        monitor.close = AsyncMock(side_effect=lambda: events.append({"action": "candidate-close", "index": index}))
        monitors.append(monitor)
        return monitor

    try:
        with ExitStack() as stack:
            stack.enter_context(patch("kiwoom_monitor.central_server.schema_migrations.datetime", FixedNow))
            stack.enter_context(patch("kiwoom_monitor.central_server.database_shadow_state.datetime", FixedNow))
            stack.enter_context(patch("kiwoom_monitor.central_server.database_documents.time", return_value=2000.0))
            stack.enter_context(patch.dict(app_factory.__globals__, {"create_query_store": owned_store,
                "CentralRestBroker": MagicMock(return_value=broker), "MarketEventService": MagicMock(return_value=services["condition"]),
                "YahooDelayedMarketCollector": MagicMock(return_value=services["external"])}))
            stack.enter_context(patch("kiwoom_monitor.central_server.news_service.CentralNewsService", return_value=services["news"]))
            stack.enter_context(patch("kiwoom_monitor.central_server.ai_service.CentralAIService", return_value=services["ai"]))
            stack.enter_context(patch("kiwoom_monitor.central_server.realtime_collector.CentralRealtimeCollector.start", new_callable=AsyncMock))
            stack.enter_context(patch.object(CandidateMonitor, "from_json", side_effect=candidate))
            store.initialize()
            app = app_factory(CentralServerSettings(f"sqlite:///{database_path}", "private-token",
                kiwoom_app_key="contract-key" if configured else "", kiwoom_secret_key="contract-secret" if configured else "",
                openai_api_key="contract-ai" if configured else "", autonomous_top20_enabled=False,
                market_event_collection_enabled=configured, external_market_symbols="TEST:TEST.CME" if configured else "",
                news_history_jobs_enabled=False, news_naver_stock_enabled=configured, news_query_set_enabled=False))
            assert len(app_stores) == 1
            app.openapi()
            yield app, store, app_stores[0], services, events, monitors, condition
        if any(monitor._task is not None for monitor in monitors):
            raise AssertionError("isolated candidate fixture unexpectedly started work")
    finally:
        store.close()


def capture_operations_http_contract(client, app, store, request_store, services, events, monitors, condition, database_path, configured):
    import hashlib
    cases = []
    endpoint = "/api/v1/settings/operations"
    def snapshot():
        with closing(sqlite3.connect(database_path)) as connection:
            return hashlib.sha256("\n".join(connection.iterdump()).encode()).hexdigest()

    def request(name, method="PUT", *, status=200, authenticated=True, **kwargs):
        events.clear()
        def save(*args, **values):
            events.append({"action": "native-save", "args": args, "kwargs": values})
            return original_save(*args, **values)
        original_save = request_store.upsert_documents
        before = snapshot()
        with patch.object(request_store, "upsert_documents", side_effect=save):
            response = client.request(method, endpoint, headers={"Authorization": "Bearer private-token"} if authenticated else {}, **kwargs)
        assert response.status_code == status, (name, status, response.status_code, response.text)
        after = snapshot()
        if method == "GET" or status in {401, 409, 422}:
            assert before == after, (name, "rejected/read request changed storage")
        current = app.state.candidate_monitor
        cases.append({"name": name, "status": response.status_code, "headers": dict(response.headers), "body": response.json(),
            "storage_hash": after, "stored": store.load_documents("server_operational_settings", "global", 1),
            "events": list(events), "candidate_index": next((i for i, monitor in enumerate(monitors) if monitor is current), None)})
        return response.json()

    request("unauthorized-read", "GET", status=401, authenticated=False)
    request("unauthorized-write", status=401, authenticated=False, json={})
    initial = request("initial", "GET")
    for name, values in (("invalid-provider", {"ai_provider": "invalid"}), ("invalid-limit", {"ai_daily_limit": -1}),
            ("strict-bool", {"news_naver_api_enabled": "false"}), ("strict-poll", {"external_market_poll_seconds": "60"}),
            ("insecure-url", {"news_naver_stock_url": "http://naver.test/news"}),
            ("url-credentials", {"news_naver_flash_url": "https://user:secret@naver.test/news"}),
            ("url-query", {"news_naver_world_url": "https://naver.test/news?key=secret"}),
            ("url-fragment", {"news_naver_stock_url": "https://naver.test/news#fragment"})):
        request(name, status=422, json=values)
    request("no-change", json={"expected_revision": initial["revision"]})
    changed = request("save-and-apply", json={"expected_revision": initial["revision"], "ai_daily_limit": 123,
        "news_refresh_seconds": 601, "news_query_set": ["증권", "코스피"], "news_processing_excluded_providers": ["thebell.co.kr"]})
    request("stale-revision", status=409, json={"expected_revision": initial["revision"], "dart_enabled": True})
    request("explicit-null-does-not-change", json={"expected_revision": changed["revision"], "ai_model": None})
    with patch.object(request_store, "upsert_documents", side_effect=OSError("injected native write failure")) as failed_save:
        request("native-save-failure", status=503, json={"ai_daily_limit": 321})
        failed_save.assert_called_once()
    preserved = request("state-after-failed-save", "GET")
    assert preserved == changed
    request("condition-not-ready-or-empty-selection", status=422, json={"hot_cohort_condition_enabled": True,
        "hot_cohort_condition_name": "", "hot_cohort_condition_substring": ""})
    if configured:
        request("condition-change", json={"hot_cohort_condition_enabled": True, "hot_cohort_condition_name": "Contract condition"})
        request("external-change", json={"external_market_enabled": True, "external_market_poll_seconds": 120,
            "external_market_auto_roll_enabled": False, "external_market_roll_confirmations": 3})
        original = services["news"].update_operational_settings.side_effect
        def failed_apply(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("injected runtime failure")
        services["news"].update_operational_settings.side_effect = failed_apply
        request("saved-but-apply-pending", status=503, json={"ai_model": "changed-model"})
        pending = request("pending-state", "GET")
        assert pending["revision"] != pending["applied_revision"] and pending["apply_status"] == "RECOVERY_REQUIRED"
        services["news"].update_operational_settings.side_effect = original
        recovered = request("retry-same-persisted-revision", json={"expected_revision": pending["revision"]})
        assert recovered["revision"] == pending["revision"] and recovered["apply_status"] == "ACTIVE"
        condition["apply_status"] = "RECOVERY_REQUIRED"
        request("condition-runtime-pending", "GET")
        request("condition-recovery-reapply", json={})
        condition["apply_status"] = "ACTIVE"
    else:
        request("external-unconfigured", status=422, json={"external_market_enabled": True})
    request("candidate-enable", json={"shadow_candidate_enabled": True})
    before = app.state.candidate_monitor
    request("news-only-keeps-candidate", json={"news_refresh_seconds": 602})
    assert before is app.state.candidate_monitor
    request("invalid-candidate-preserves-active", status=422, json={"shadow_candidate_config": {"strategy_version": "v999"}})
    assert before is app.state.candidate_monitor
    request("candidate-replacement", json={"shadow_candidate_poll_seconds": 3})
    assert before is not app.state.candidate_monitor
    request("candidate-disable", json={"shadow_candidate_enabled": False})
    assert app.state.candidate_monitor is None
    request("final-state", "GET")
    return json.loads(json.dumps(cases, ensure_ascii=False))


@contextmanager
def account_query_contract_app(app_factory, database_path, mode):
    """Native sessions/storage; constructor doubles expose owner publication to HTTP."""
    from types import SimpleNamespace
    from tests.unit.test_market_profile_settings import account_settings_contract_app
    from kiwoom_monitor.central_server.account_query import AccountQuerySessionManager
    from kiwoom_monitor.central_server.rest_broker import CentralRestBroker, BrokerResult
    from kiwoom_monitor.domain.order_contract import AccountBinding, AccountEnvironment, AccountScope
    from kiwoom_monitor.central_server import real_runtime

    with account_settings_contract_app(app_factory, database_path, mode) as (app, store, request_store, real, mock, refs):
        contexts, managers, brokers = {}, {}, {}
        clock = [1000.0]
        for profile, environment in (("contract-mock", "mock"), ("contract-real", "real"), ("nas-real-default", "real")):
            binding = AccountBinding(profile, AccountScope("kiwoom", AccountEnvironment(environment), refs[environment]),
                                     1, datetime(2026, 10, 10, 3, tzinfo=timezone.utc))
            current = [binding]
            broker = MagicMock(spec=CentralRestBroker)
            broker.request = AsyncMock(return_value=BrokerResult({"profile": profile}, True, "cursor-1", False))
            manager = AccountQuerySessionManager(broker, lambda value=current: value[0], monotonic_provider=lambda: clock[0])
            contexts[profile] = SimpleNamespace(binding=binding, identity=None, account_queries=manager, current=current)
            managers[profile], brokers[profile] = manager, broker
        store.rename_credential_profile("kiwoom_mock", "contract-mock", "Mock desk")
        store.rename_credential_profile("kiwoom_real", "contract-real", "Real desk")
        if mock is not None:
            mock.bundle.side_effect = lambda profile: contexts.get(profile) if profile == "contract-mock" else None
            mock.account_bindings.side_effect = lambda: (contexts["contract-mock"].binding,)
        if real is not None:
            real.bundle.side_effect = lambda profile: contexts.get(profile) if profile == "contract-real" else None
            real.account_bindings.side_effect = lambda: (contexts["contract-real"].binding,)
            publish = real_runtime.RealCredentialOwner.call_args.kwargs["on_change"]
            # Publication occurs at startup, after the HTTP handlers were assembled.
            real.start.side_effect = lambda: publish("nas-real-default", contexts["nas-real-default"])
        else:
            publish = None
        with patch("uuid.uuid4", side_effect=(uuid.UUID(int=value) for value in range(100, 1000))):
            try:
                yield app, store, request_store, contexts, managers, brokers, clock, publish
            finally:
                for manager in managers.values():
                    asyncio.run(manager.close())
                    if manager._tasks or manager._sessions or not manager._closed:
                        raise AssertionError("fixture-owned account session failed to finish")


def capture_account_query_http_contract(client, app, store, request_store, contexts, managers, brokers, clock, publish, database_path, mode):
    import hashlib
    from dataclasses import replace
    cases = []

    def stored_hash():
        with closing(sqlite3.connect(database_path)) as connection:
            return hashlib.sha256("\n".join(connection.iterdump()).encode()).hexdigest()

    def request(name, method, path, *, status=200, authenticated=True, **kwargs):
        before = stored_hash()
        for broker in brokers.values():
            broker.request.reset_mock()
        with patch.object(request_store, "list_credential_profiles", wraps=request_store.list_credential_profiles) as reads:
            response = client.request(method, path, headers={"Authorization": "Bearer private-token"} if authenticated else {}, **kwargs)
        if response.status_code != status:
            raise AssertionError((mode, name, status, response.status_code, response.text))
        after = stored_hash()
        if before != after:
            raise AssertionError((name, "account read changed native storage"))
        calls = {profile: [(call.args, call.kwargs) for call in broker.request.await_args_list] for profile, broker in brokers.items()}
        if any(manager._tasks for manager in managers.values()):
            raise AssertionError((name, "native query task still running after response"))
        cases.append({"name": name, "status": response.status_code, "headers": dict(response.headers), "body": response.json(),
                      "storage_hash": after, "profile_reads": [(call.args, call.kwargs) for call in reads.call_args_list], "broker_calls": calls})
        return response.json()

    accounts, v2, v3 = "/api/v3/kiwoom/accounts", "/api/v2/kiwoom/account-query", "/api/v3/kiwoom/account-query"
    valid = {"api_id": "kt00007", "path": "/api/dostk/acnt", "body": {"ord_dt": "20261010"}}
    target = contexts["contract-mock"].binding
    scoped = {**valid, "account_scope": target.scope.to_dict(), "credential_profile_id": target.credential_profile_id, "expected_binding_revision": 1}
    for method, path in (("GET", accounts), ("POST", v2), ("POST", v3)):
        request("unauthorized-" + path, method, path, authenticated=False, status=401, **({"json": scoped} if method == "POST" else {}))
    request("unknown-route", "GET", accounts + "/missing", status=404)
    request("account-list", "GET", accounts)
    for path in (v2, v3):
        for name, invalid in (("empty", {}), ("wrong-api", {**scoped, "api_id": "ka10001"}), ("negative-page", {**scoped, "page_index": -1})):
            request(path + "-" + name, "POST", path, json=invalid, status=422)
    request("strict-revision", "POST", v3, json={**scoped, "expected_binding_revision": "1"}, status=422)
    request("extra-scoped-field", "POST", v3, json={**scoped, "unexpected": True}, status=422)
    request("invalid-account-uuid", "POST", v3, json={**scoped, "account_scope": {**target.scope.to_dict(), "account_ref": "x" * 36}}, status=400)
    request("unknown-profile", "POST", v3, json={**scoped, "credential_profile_id": "not-ready"}, status=503)
    request("legacy-startup-publication", "POST", v2, json=valid, status=200 if mode == "real" else 503)
    if mode == "absent":
        request("scoped-owner-absent", "POST", v3, json=scoped, status=503)
        return json.loads(json.dumps(cases))
    request("wrong-binding-revision", "POST", v3, json={**scoped, "expected_binding_revision": 2}, status=409)
    request("wrong-account", "POST", v3, json={**scoped, "account_scope": {**target.scope.to_dict(), "account_ref": str(uuid.UUID(int=99))}}, status=409)
    first = request("first-page", "POST", v3, json=scoped)
    continuation = {**scoped, "batch_id": first["batch_id"], "page_index": 1, "next_key": "cursor-1"}
    request("body-mismatch-before-broker", "POST", v3, json={**continuation, "body": {}}, status=409)
    request("cursor-mismatch-before-broker", "POST", v3, json={**continuation, "next_key": "wrong"}, status=409)
    brokers["contract-mock"].request.return_value = replace(brokers["contract-mock"].request.return_value, has_next=False)
    request("complete-second-page", "POST", v3, json=continuation)
    request("completed-cursor-reuse", "POST", v3, json=continuation, status=409)
    request("invalid-endpoint", "POST", v3, json={**scoped, "path": "/wrong"}, status=409)
    brokers["contract-mock"].request.return_value = replace(brokers["contract-mock"].request.return_value, has_next=True)
    expiring = request("expiring-first-page", "POST", v3, json=scoped)
    clock[0] += 121
    request("expired-cursor", "POST", v3, json={**continuation, "batch_id": expiring["batch_id"]}, status=409)
    for error, status in ((ValueError("private body failure"), 409), (RuntimeError("private transport failure"), 409), (OSError("private network failure"), 502)):
        brokers["contract-mock"].request.side_effect = error
        request("broker-failure-" + type(error).__name__, "POST", v3, json=scoped, status=status)
        if managers["contract-mock"]._sessions:
            raise AssertionError("native failed query retained cursor")
    brokers["contract-mock"].request.side_effect = None
    asyncio.run(managers["contract-mock"].begin_credential_change())
    request("credential-drain-busy", "POST", v3, json=scoped, status=503)
    managers["contract-mock"].end_credential_change(invalidate_cursors=True)
    if mode == "real":
        real_target = contexts["contract-real"].binding
        request("independent-real-profile", "POST", v3, json={**valid, "account_scope": real_target.scope.to_dict(), "credential_profile_id": "contract-real", "expected_binding_revision": 1})
        # Only the selected native session's returned context is corrupted here.
        with patch.object(managers["contract-real"], "query", new=AsyncMock(return_value={"context": {}})) as query:
            request("late-response-context-guard", "POST", v3, json={**valid, "account_scope": real_target.scope.to_dict(), "credential_profile_id": "contract-real", "expected_binding_revision": 1}, status=409)
            query.assert_awaited_once()
        publish("nas-real-default", None)
        request("legacy-after-unpublication", "POST", v2, json=valid, status=503)
        request("list-after-unpublication", "GET", accounts)
        publish("nas-real-default", contexts["nas-real-default"])
        request("legacy-after-republication", "POST", v2, json=valid)
    asyncio.run(managers["contract-mock"].close())
    request("closed-session", "POST", v3, json=scoped, status=503)
    return json.loads(json.dumps(cases))


@contextmanager
def market_query_contract_app(app_factory, database_path, *, configured):
    """Native archive readers; broker/status doubles exercise HTTP transport policy."""
    import sys
    from kiwoom_monitor.central_server.rest_broker import CentralRestBroker, BrokerResult

    class FixedNow(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime(2026, 10, 10, 3, tzinfo=timezone.utc)
            return value.astimezone(tz) if tz else value

    store = SQLiteQueryStore(database_path)
    broker = MagicMock(spec=CentralRestBroker)
    broker.start, broker.close = AsyncMock(), AsyncMock()
    broker.request = AsyncMock(return_value=BrokerResult({"source": "broker", "value": 9}, True, "cursor-2", True))
    broker._credential_paused = False
    broker._client = MagicMock()
    broker._client.server_now.return_value = FixedNow.now(KST)
    original_factory = app_factory.__globals__["create_query_store"]
    app_stores = []

    def owned_store(*args, **kwargs):
        value = original_factory(*args, **kwargs)
        app_stores.append(value)
        return value

    try:
        with ExitStack() as stack:
            for module in ("schema_migrations", "database_market_bars"):
                stack.enter_context(patch(f"kiwoom_monitor.central_server.{module}.datetime", FixedNow))
            stack.enter_context(patch("kiwoom_monitor.central_server.database_observation_writes.datetime", FixedNow))
            stack.enter_context(patch("uuid.uuid4", side_effect=(uuid.UUID(int=value) for value in range(1, 1000))))
            stack.enter_context(patch("kiwoom_monitor.central_server.database_documents.time", return_value=2000.0))
            stack.enter_context(patch.dict(app_factory.__globals__, {"create_query_store": owned_store,
                "CentralRestBroker": MagicMock(return_value=broker)}))
            stack.enter_context(patch("kiwoom_monitor.central_server.realtime_collector.CentralRealtimeCollector.start", new_callable=AsyncMock))
            store.initialize()
            for collection, payload in (("stock_fundamentals", {"mac": "1000", "dstr_rt": "40"}),
                                        ("stock_nxt_eligibility", {"nxtEnable": "Y"})):
                for code, observed_at in (("005930", "2026-10-10T12:00:00+09:00"), ("000660", "2020-01-01T09:00:00+09:00")):
                    store.upsert_documents(collection, [{"owner": code, "key": "latest",
                        "document": {"observed_at": observed_at, "payload": payload}}])
                store.upsert_documents(collection, [{"owner": "035420", "key": "latest",
                    "document": {"observed_at": "2026-10-10T12:00:00+09:00", "payload": "invalid"}}])
            base = {"code": "005930", "trading_date": "2026-09-14", "minute": "10:00", "market": "KRX",
                    "open": 100, "high": 110, "low": 90, "close": 105, "volume": 10,
                    "trade_value_million_won": 20, "updated_at": 1.0}
            store.save_minute_bars([base, {**base, "minute": "10:01", "volume": 11}])
            store.replace_daily_bars([base, {**base, "trading_date": "2026-09-11"}])
            store.upsert_documents("market_data_coverage", [{"owner": "2026-09-14:005930:KRX", "key": "complete",
                "document": {"kind": "minute", "window_closed": True, "session_finalized": True, "as_of": "2026-09-14"}}])
            store.upsert_documents("market_data_coverage_daily", [{"owner": "005930:KRX", "key": "complete",
                "document": {"kind": "daily", "window_closed": True, "session_finalized": True,
                             "rows": 2, "as_of": "2026-09-14"}}])
            app = app_factory(CentralServerSettings(f"sqlite:///{database_path}", "private-token",
                kiwoom_app_key="contract-key" if configured else "", kiwoom_secret_key="contract-secret" if configured else "",
                autonomous_top20_enabled=False, market_event_collection_enabled=False, news_history_jobs_enabled=False))
            app.openapi()
            stack.enter_context(patch.dict(app_factory.__globals__, {"datetime": FixedNow}))
            module = sys.modules.get("kiwoom_monitor.central_server.market_query_routes")
            if module is not None:
                stack.enter_context(patch.dict(vars(module), {"datetime": FixedNow}))
            if len(app_stores) != 1:
                raise AssertionError("expected one app-owned native store")
            yield app, store, broker if configured else None, app_stores[0]
        if configured:
            broker.start.assert_awaited_once()
            broker.close.assert_awaited_once()
    finally:
        store.close()


def capture_market_query_http_contract(client, app, store, broker, request_store, database_path):
    import hashlib
    cases = []

    def stored_hash():
        with closing(sqlite3.connect(database_path)) as connection:
            return hashlib.sha256("\n".join(connection.iterdump()).encode()).hexdigest()

    def request(name, body, *, status=200, authenticated=True, path="/api/v1/kiwoom/query"):
        before = stored_hash()
        if broker is not None:
            broker.request.reset_mock()
        with ExitStack() as stack:
            spies = {method: stack.enter_context(patch.object(request_store, method, wraps=getattr(request_store, method)))
                     for method in ("load_documents", "load_minute_bars", "load_daily_bars")}
            response = client.post(path, json=body, headers={"Authorization": "Bearer private-token"} if authenticated else {})
        if response.status_code != status:
            raise AssertionError((name, response.status_code, response.text))
        if stored_hash() != before:
            raise AssertionError((name, "HTTP query changed native archive"))
        cases.append({"name": name, "status": response.status_code, "headers": dict(response.headers), "body": response.json(),
            "storage_hash": before, "store_calls": {key: [(call.args, call.kwargs) for call in spy.call_args_list]
                                                       for key, spy in spies.items()},
            "broker_calls": [(call.args, call.kwargs) for call in broker.request.call_args_list] if broker is not None else []})

    valid = {"api_id": "ka10001", "path": "/api/dostk/stkinfo", "body": {"stk_cd": "005930"}}
    request("unauthorized", valid, authenticated=False, status=401)
    request("unknown-path", valid, path="/api/v1/kiwoom/query/unknown", status=404)
    for name, body in (("missing-fields", {}), ("short-id", {**valid, "api_id": "short"}),
                       ("invalid-body", {**valid, "body": []}), ("null-path", {**valid, "path": None})):
        request(name, body, status=422)
    for api in ("ka10075", "ka10076", "kt00018", "kt00001"):
        request("account-scope-required-" + api, {**valid, "api_id": api}, status=400)
    fallback = 200 if broker is not None else 503
    for api in ("ka10001", "ka10100"):
        for name, code in (("fresh", "005930"), ("fresh-NX", "005930_NX"), ("fresh-AL", "005930_AL"),
                           ("stale", "000660"), ("malformed", "035420"), ("missing", "999999"), ("empty", "")):
            request(api + "-" + name, {**valid, "api_id": api, "body": {"stk_cd": code}},
                    status=200 if name.startswith("fresh") else fallback)
        request(api + "-continuation-bypasses-archive", {**valid, "api_id": api, "cont_yn": "Y", "next_key": "cursor-1"}, status=fallback)
    for api in ("ka10080", "ka10081"):
        for name, body in (("complete", {"stk_cd": "005930", "base_dt": "20260914"}),
                           ("NXT-unconfirmed", {"stk_cd": "005930_NX", "base_dt": "20260914"}),
                           ("invalid-date", {"stk_cd": "005930", "base_dt": "invalid"}),
                           ("missing-code", {"base_dt": "20260914"})):
            request(api + "-" + name, {**valid, "api_id": api, "body": body}, status=200 if name == "complete" else fallback)
    request("broker-only-api", {**valid, "api_id": "ka10004", "body": {"stk_cd": "005930"}}, status=fallback)
    if broker is not None:
        for error, status in ((ValueError("invalid-query"), 400), (RuntimeError("query-unavailable"), 502)):
            broker.request.side_effect = error
            request("broker-error-" + str(error), {**valid, "api_id": "ka10004"}, status=status)
        broker.request.side_effect = None
        broker._credential_paused = True
        collector = app.state.realtime_collector
        with patch.object(collector, "credential_connection_status", return_value={"planned_reconnect": True, "phase": "RECONNECTING"}) as status:
            request("reconnecting-blocks-broker", {**valid, "api_id": "ka10004"}, status=503)
            status.assert_called_once_with()
            broker.request.assert_not_awaited()
            status.reset_mock()
            request("archive-precedes-reconnecting-gate", valid)
            status.assert_not_called()
            broker.request.assert_not_awaited()
        with patch.object(collector, "credential_connection_status", return_value={"planned_reconnect": False}) as status:
            request("paused-without-planned-reconnect", {**valid, "api_id": "ka10004"})
            status.assert_called_once_with()
        broker._credential_paused = False
    return json.loads(json.dumps(cases, ensure_ascii=False))


@contextmanager
def market_event_contract_app(app_factory, database_path, *, configured):
    """Native event history/current cohort; isolate existing network producers only."""
    from kiwoom_monitor.central_server.market_events import MarketEventService
    store = SQLiteQueryStore(database_path)
    service = MagicMock(spec=MarketEventService) if configured else None
    app_stores = []
    original_factory = app_factory.__globals__["create_query_store"]

    def owned_store(*args, **kwargs):
        value = original_factory(*args, **kwargs)
        app_stores.append(value)
        return value

    if service is not None:
        service.start = AsyncMock()
        service.close = AsyncMock()
        service.condition_status.return_value = {"apply_status": "ACTIVE", "policy_revision": 3}

    class FixedNow(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime(2026, 10, 10, 3, tzinfo=timezone.utc)
            return value.astimezone(tz) if tz else value

    try:
        with ExitStack() as stack:
            stack.enter_context(patch("kiwoom_monitor.central_server.schema_migrations.datetime", FixedNow))
            stack.enter_context(patch("kiwoom_monitor.central_server.database_documents.time", return_value=2000.0))
            stack.enter_context(patch.dict(app_factory.__globals__, {
                "MarketEventService": MagicMock(return_value=service), "create_query_store": owned_store,
            }))
            stack.enter_context(patch("kiwoom_monitor.central_server.app.CentralRestBroker.start", new_callable=AsyncMock))
            stack.enter_context(patch("kiwoom_monitor.central_server.app.CentralRealtimeCollector.start", new_callable=AsyncMock))
            store.initialize()
            for index, (code, active) in enumerate((("005930", True), ("000660", False), ("005930", False)), 1):
                at = 1000.0 + index
                store.append_vi_events([{"event_id": f"vi-{index}", "event_key": f"vi-key-{index}",
                    "stock_code": code, "event_kind": "ACTIVATED", "vi_type": "DYNAMIC", "price": 75000,
                    "effective_at": at, "received_at": at, "available_at": at, "source": "fixture"}])
                store.record_hot_cohort_revision({"revision_id": f"cohort-{index}", "revision_key": f"cohort-key-{index}",
                    "stock_code": code, "event_type": "ENTER" if active else "EXIT", "session_id": "2026-10-09",
                    "effective_at": at, "available_at": at}, current={"stock_code": code, "stock_name": code,
                    "first_seen_at": at, "entry_session": "2026-10-09", "last_signal_at": at,
                    "active": active, "expired_at": None if active else at})
                store.append_upper_limit_facts([{"fact_id": f"limit-{index}", "fact_key": f"limit-key-{index}",
                    "stock_code": code, "session_id": "2026-10-09", "status": "OBSERVED", "upper_limit_price": 78000,
                    "effective_at": at, "available_at": at, "source": "fixture"}])
            app = app_factory(CentralServerSettings(
                f"sqlite:///{database_path}", "private-token", autonomous_top20_enabled=False,
                kiwoom_app_key="contract-key" if configured else "",
                kiwoom_secret_key="contract-secret" if configured else "",
                market_event_collection_enabled=configured, news_history_jobs_enabled=False,
            ))
            if len(app_stores) != 1:
                raise AssertionError("expected one app-owned native store")
            yield app, store, service, app_stores[0]
        if service is not None:
            service.start.assert_awaited_once()
            service.close.assert_awaited_once()
    finally:
        store.close()


def capture_market_event_http_contract(client, store, service, database_path, request_store):
    import hashlib

    def stored_hash():
        with closing(sqlite3.connect(database_path)) as connection:
            return hashlib.sha256("\n".join(connection.iterdump()).encode()).hexdigest()

    before = stored_hash()
    cases = []

    def request(name, params, *, authenticated=True, status=200, path="/api/v1/market/events"):
        if service is not None:
            service.condition_status.reset_mock()
        with patch.object(request_store, "load_market_event_history", wraps=request_store.load_market_event_history) as history, \
                patch.object(request_store, "load_hot_cohort", wraps=request_store.load_hot_cohort) as current, \
                patch.object(request_store, "load_documents", wraps=request_store.load_documents) as documents:
            response = client.get(path, params=params,
                headers={"Authorization": "Bearer private-token"} if authenticated else {})
        if response.status_code != status:
            raise AssertionError((name, response.status_code, response.text))
        if stored_hash() != before:
            raise AssertionError((name, "event read changed persistent state"))
        runtime_calls = service.condition_status.call_count if service is not None else 0
        expected_calls = int(service is not None and status == 200 and params.get("kind") == "cohort")
        if runtime_calls != expected_calls:
            raise AssertionError((name, "runtime invocation mismatch", runtime_calls, expected_calls))
        valid = status == 200
        cohort = valid and params.get("kind") == "cohort"
        expected_history = [((params["kind"],), {"code": params.get("code", ""), "limit": params.get("limit", 100)})] if valid else []
        if [(call.args, call.kwargs) for call in history.call_args_list] != expected_history:
            raise AssertionError((name, "history query arguments", history.call_args_list))
        if [(call.args, call.kwargs) for call in current.call_args_list] != ([((), {"active_only": False})] if cohort else []):
            raise AssertionError((name, "current cohort query arguments", current.call_args_list))
        if [(call.args, call.kwargs) for call in documents.call_args_list] != ([(('condition_search_status', 'hot_cohort', 1), {})] if cohort else []):
            raise AssertionError((name, "condition document query arguments", documents.call_args_list))
        cases.append({"name": name, "request": {"path": path, "params": params, "authenticated": authenticated},
            "status": response.status_code, "headers": {k: response.headers[k] for k in ("content-type", "content-length")},
            "body": response.json(), "database_sha256": before, "runtime_calls": runtime_calls,
            "store_calls": {key: [{"args": call.args, "kwargs": call.kwargs} for call in spy.call_args_list]
                            for key, spy in (("history", history), ("current", current), ("condition", documents))}})

    for kind in ("vi", "cohort", "upper_limit"):
        request(kind + "-auth", {"kind": kind}, authenticated=False, status=401)
        for label, params in (("all", {}), ("filter-limit", {"code": "005930", "limit": 1}),
                              ("missing", {"code": "999999"}), ("inactive", {"code": "000660"})):
            request(kind + "-" + label, {"kind": kind, **params})
    for label, params in (("missing-kind", {}), ("invalid-kind", {"kind": "invalid"}),
                          ("long-code", {"kind": "vi", "code": "x" * 13}),
                          ("zero-limit", {"kind": "cohort", "limit": 0}),
                          ("large-limit", {"kind": "upper_limit", "limit": 1001})):
        request(label, params, status=422)
    request("unknown-path", {}, path="/api/v1/market/events/unknown", status=404)
    store.upsert_documents("condition_search_status", [{"owner": "hot_cohort", "key": "status",
        "document": {"status": "OBSERVED", "condition_name": "fixture-condition"}}])
    before = stored_hash()
    request("stored-condition", {"kind": "cohort"})
    if service is not None:
        service.condition_status.return_value = {"apply_status": "RECOVERY_REQUIRED", "policy_revision": 4}
        request("changed-runtime-condition", {"kind": "cohort"})
    return cases


@contextmanager
def market_read_contract_app(app_factory, database_path):
    """Real stored bars/metadata and real unsaved RAM, without starting network producers."""
    import sys
    from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
    from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
    from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import TradeTick
    store = SQLiteQueryStore(database_path)
    at = datetime(2026, 9, 14, 10, 4, 40)
    clock = datetime(2026, 9, 14, 16, tzinfo=KST).timestamp()

    class FixedNow(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime(2026, 10, 10, 3, tzinfo=timezone.utc)
            return value.astimezone(tz) if tz else value

    collectors = []
    try:
        with ExitStack() as stack:
            stack.enter_context(patch("kiwoom_monitor.central_server.schema_migrations.datetime", FixedNow))
            stack.enter_context(patch("kiwoom_monitor.central_server.database_market_bars.datetime", FixedNow))
            stack.enter_context(patch("kiwoom_monitor.central_server.database_observation_writes.datetime", FixedNow))
            stack.enter_context(patch("kiwoom_monitor.central_server.minute_bars.uuid.uuid4",
                                     side_effect=(uuid.UUID(int=value) for value in range(1, 100))))
            store.initialize()
            for module in ("database_documents", "database_datasets"):
                stack.enter_context(patch("kiwoom_monitor.central_server." + module + ".time", return_value=clock))
            base = {"code": "005930", "trading_date": "2026-09-14", "minute": "10:00", "open": 100,
                    "high": 110, "low": 90, "close": 105, "volume": 10, "trade_value_million_won": 20, "updated_at": 1.0}
            store.save_minute_bars([{**base, "market": "KRX"}, {**base, "market": "NXT", "volume": 5},
                                    {**base, "market": "SOR", "volume": 12},
                                    {**base, "minute": "10:01", "market": "KRX"},
                                    {**base, "minute": "10:01", "market": "NXT", "volume": 4},
                                    {**base, "trading_date": "2026-09-11", "market": "KRX"}])
            store.replace_daily_bars([{**base, "market": "KRX", "trading_date": day} for day in ("2026-09-11", "2026-09-14")])
            store.save_realtime_snapshots([{"event_type": "trade", "item_key": "005930", "received_at": 1.0,
                "event": {"type": "trade", "payload": {"code": "005930", "current_price": 70000, "market_cap_eok": 4321000}}}])
            store.upsert_documents("market_data_coverage", [{"owner": "2026-09-14:005930:KRX", "key": "complete",
                "document": {"kind": "minute", "window_closed": True, "session_finalized": True, "as_of": "2026-09-14"}}])
            store.upsert_documents("minute_trade_value_comparisons", [{"owner": "2026-09-14:005930", "key": str(index),
                "document": {"query_scope": scope, "compared_at": "2026-09-14T10:02:00+09:00",
                             "realtime_trade_value_million_won": realtime, "query_trade_value_million_won": query,
                             "difference_percent": difference}} for index, (scope, realtime, query, difference) in enumerate(
                                 (("KRX+NXT", 110, 100, 10.0), ("KRX+NXT", 90, 100, -10.0), ("KRX", 999, 50, 1898.0)))])
            store.save_external_bars([{"provider": "yahoo_delayed", "instrument": "NASDAQ_FUTURES", "contract": "MNQU26.CME",
                "timeframe": "5m", "bar_time": "2026-09-14T00:00:00Z", "open": 100., "high": 101., "low": 99.,
                "close": 100., "volume": 10., "updated_at": 1.0}])
            key = "2026-09-14T10:00:00+09:00"
            payload = {"query_type": "5", "items": [{"stk_cd": "005930"}]}
            store.save_dataset_snapshot("ranking", "5", key, payload, observation=ranking_observation(
                "5", key, payload, datetime(2026, 9, 14, 1, tzinfo=timezone.utc), source="fixture"))
            for volume in (13, 23):
                collector = CentralRealtimeCollector(lambda: "unused", "real", RealtimeHub(), lambda: at, store)
                collector._minute_bars.add(TradeTick("005930", 100, None, None, volume, None, "100440", market="SOR"), at, at.timestamp())
                collectors.append(collector)
            settings = CentralServerSettings(f"sqlite:///{database_path}", "private-token", autonomous_top20_enabled=False,
                market_event_collection_enabled=False, news_history_jobs_enabled=False)
            app = app_factory(settings)
            # Included routers may resolve request annotations lazily. Resolve
            # their schema with real types before replacing only request clocks.
            app.openapi()
            stack.enter_context(patch.dict(app_factory.__globals__, {"datetime": FixedNow}))
            module = sys.modules.get("kiwoom_monitor.central_server.market_read_routes")
            if module is not None:
                stack.enter_context(patch.dict(vars(module), {"datetime": FixedNow}))
            yield app, store, collectors
    finally:
        for collector in collectors:
            asyncio.run(collector.close())
        store.close()


def capture_market_read_http_contract(client, app, store, collectors, database_path):
    import hashlib
    cases = []

    def stored_hash():
        with closing(sqlite3.connect(database_path)) as connection:
            return hashlib.sha256("\n".join(connection.iterdump()).encode()).hexdigest()

    def request(name, path, *, authenticated=True, **params):
        before = stored_hash()
        response = client.get("/api/v1/market/" + path, params=params,
            headers={"Authorization": "Bearer private-token"} if authenticated else {})
        cases.append({"name": name, "status": response.status_code, "headers": dict(response.headers),
                      "body": response.json(), "stored_before": before, "stored_after": stored_hash()})

    common = {"code": "005930", "trading_date": "2026-09-14"}
    paths = {"minute-bars": common, "recent-minute-bars": {"code": "005930", "end_date": "2026-09-14", "trading_days": 2},
             "latest-market-caps": {"codes": ["005930"]}, "trade-value-comparisons": common,
             "daily-bars": {"code": "005930"}, "coverage": {"kind": "minute_bar", "subject": "005930:KRX",
                 "start": "2026-09-14T00:00:00+09:00", "end": "2026-09-15T00:00:00+09:00"},
             "external-bars": {"instrument": "nasdaq_futures", "timeframe": "5m"}}
    for path, params in paths.items():
        request("unauthorized-" + path, path, authenticated=False, **params)
        request("stored-" + path, path, **params)
    for market in ("", "KRX", "NXT", "SOR", "COMBINED"):
        request("minute-" + (market or "all"), "minute-bars", **common, market=market)
    request("minute-empty", "minute-bars", code="999999", trading_date="2026-09-14")
    request("minute-invalid-market", "minute-bars", **common, market="INVALID")
    request("minute-invalid-code", "minute-bars", code="x", trading_date="2026-09-14")
    request("recent-limited", "recent-minute-bars", code="005930", end_date="2026-09-14", trading_days=1, market="COMBINED")
    request("recent-empty", "recent-minute-bars", code="999999", end_date="2026-09-14")
    request("recent-invalid-days", "recent-minute-bars", code="005930", end_date="2026-09-14", trading_days=6)
    for codes in ([], ["bad"], ["005930"] * 201, [f"{code:06d}" for code in range(201)], ["005930", "000660", "005930"]):
        request("caps-" + str(len(codes)) + "-" + str(codes[:1]), "latest-market-caps", codes=codes)
    request("comparison-limit", "trade-value-comparisons", **common, limit=1)
    request("comparison-empty", "trade-value-comparisons", code="999999", trading_date="2026-09-14")
    request("daily-filter-limit", "daily-bars", code="005930", market="krx", limit=1)
    request("daily-empty", "daily-bars", code="999999", market="NXT")
    request("daily-invalid-limit", "daily-bars", code="005930", limit=0)
    coverage = paths["coverage"]
    for name, changes in (("coverage-unknown", {"kind": "unknown"}), ("coverage-unrecognized", {"kind": "bad"}),
                          ("coverage-reversed", {"end": coverage["start"]}), ("coverage-cadence-rejected", {"expected_seconds": 60}),
                          ("coverage-early-cutoff", {"available_by": "2026-09-14T15:00:00+09:00"}),
                          ("coverage-late-cutoff", {"available_by": "2026-09-14T17:00:00+09:00"}),
                          ("coverage-invalid-time", {"start": "bad"}),
                          ("coverage-candidate-cadence", {"kind": "candidate_set", "subject": "5", "expected_seconds": 30})):
        request(name, "coverage", **{**coverage, **changes})
    request("external-whitespace", "external-bars", instrument=" nasdaq_futures ", timeframe="5m", limit=1)
    request("external-empty", "external-bars", instrument="unknown", timeframe="1d")
    request("external-invalid-timeframe", "external-bars", instrument="NASDAQ_FUTURES", timeframe="1m")
    for generation, collector in enumerate(collectors, 1):
        app.state.realtime_collector = collector
        request("live-minute-" + str(generation), "minute-bars", **common, market="COMBINED")
        request("live-recent-" + str(generation), "recent-minute-bars", code="005930", end_date="2026-09-14", trading_days=1, market="COMBINED")
    app.state.realtime_collector = None
    request("collector-removed-fallback", "minute-bars", **common, market="COMBINED")
    return {"cases": cases, "pending": [collector._minute_bars.pending_bars("005930", "2026-09-14") for collector in collectors],
            "stored_minutes": store.load_minute_bars("005930", "2026-09-14")}


@contextmanager
def market_dataset_contract_app(app_factory, database_path, *, configured):
    """Native snapshot/statistics storage; isolate background network producers only."""
    from datetime import date
    from kiwoom_monitor.central_server.autonomous_top20 import AutonomousTop20Service
    store = SQLiteQueryStore(database_path)
    store.initialize()
    service = MagicMock(spec=AutonomousTop20Service)
    service.start = AsyncMock()
    service.close = AsyncMock()
    constructor = MagicMock(return_value=service)
    original_factory = app_factory.__globals__["create_query_store"]
    app_stores = []

    def owned_store(*args, **kwargs):
        value = original_factory(*args, **kwargs)
        app_stores.append(value)
        return value
    settings = CentralServerSettings(
        f"sqlite:///{database_path}", "private-token", autonomous_top20_enabled=configured,
        kiwoom_app_key="contract-key" if configured else "",
        kiwoom_secret_key="contract-secret" if configured else "",
        market_event_collection_enabled=False, news_history_jobs_enabled=False,
        top20_outbox_path=str(database_path.with_suffix(".outbox")),
    )
    try:
        with patch("kiwoom_monitor.central_server.database_datasets.time", return_value=2000.0), \
                patch("kiwoom_monitor.central_server.database_top20_statistics.time", return_value=2000.0), \
                patch("kiwoom_monitor.central_server.database_top20_statistics._top20_today", return_value=date(2026, 10, 10)), \
                patch.dict(app_factory.__globals__, {"AutonomousTop20Service": constructor, "create_query_store": owned_store}), \
                patch("kiwoom_monitor.central_server.app.CentralRestBroker.start", new_callable=AsyncMock), \
                patch("kiwoom_monitor.central_server.app.CentralRealtimeCollector.start", new_callable=AsyncMock):
            for day in ("2026-09-08", "2026-09-09"):
                store.save_dataset_snapshot("ranking", day, day + "T10:00", {"codes": ["005930"]})
                store.save_dataset_snapshot("top20_membership", day, day + "T10:00", {"source": "persisted", "codes": ["000660"]})
            for kind in ("market_state", "investor_flow", "program_flow", "new_high", "stock_fundamentals", "nxt_eligibility"):
                store.save_dataset_snapshot(kind, "005930", "2026-09-08T10:00", {"kind": kind, "value": 7})
            store.save_dataset_snapshot("top20_index", "2026-09-08", "2026-09-08T15:30", {
                "minute": "2026-09-08T15:30", "market_values": [3.0, 2.0, 0.0], "capture_state": "realtime_complete",
            })
            store.save_dataset_snapshot("top20_index", "2026-09-08", "2026-09-08T15:29", {
                "minute": "2026-09-08T15:29", "market_values": [999.0, 0.0, 0.0], "capture_state": "partial",
            })
            store.save_dataset_snapshot("market_index_chart", "20260908:kospi", "20260908", {
                "daily": [{"dt": "20260908", "trde_prica": "26187833"}],
            })
            app = app_factory(settings)
            assert len(app_stores) == 1
            yield app, store, service if configured else None, app_stores[0]
            assert constructor.call_count == int(configured)
            assert service.start.await_count == int(configured)
            assert service.close.await_count == int(configured)
    finally:
        store.close()


def capture_market_dataset_http_contract(client, store, service, request_store):
    """Compare full HTTP, actual DB/live reads and independently observed cache writes."""
    cases = []
    # Diagnostic capture binds instance methods at construction. Spy on the real
    # app-owned methods after that binding, preserving the native implementations.
    with patch.object(request_store, "load_dataset_snapshots", wraps=request_store.load_dataset_snapshots) as snapshots, \
            patch.object(request_store, "load_top20_statistics", wraps=request_store.load_top20_statistics) as statistics:
        def request(name, path, *, authenticated=True, **params):
            snapshots.reset_mock()
            statistics.reset_mock()
            if service is not None:
                service.latest_membership_snapshot.reset_mock()
            response = client.get(path, params=params, headers={"Authorization": "Bearer private-token"} if authenticated else {})
            cases.append({"name": name, "status": response.status_code, "headers": dict(response.headers),
                          "body": response.json(), "snapshot_reads": [list(call.args) for call in snapshots.call_args_list],
                          "statistics_reads": [list(call.args) for call in statistics.call_args_list],
                          "live_reads": [list(call.args) for call in service.latest_membership_snapshot.call_args_list] if service else []})

        endpoint = "/api/v1/market/snapshots/"
        request("unauthorized-snapshot", endpoint + "ranking", authenticated=False)
        request("unknown-kind", endpoint + "unknown")
        for params in ({"limit": 0}, {"limit": 5001}, {"subject": "x" * 33}, {"prefer_live": "invalid"}):
            request("invalid-snapshot-" + next(iter(params)), endpoint + "ranking", **params)
        for kind in ("ranking", "top20_membership", "top20_index", "market_state", "investor_flow", "program_flow",
                     "new_high", "stock_fundamentals", "nxt_eligibility", "market_index_chart"):
            request("stored-" + kind, endpoint + kind)
        request("subject-filter", endpoint + "ranking", subject="2026-09-08", limit=1)
        request("empty-subject-result", endpoint + "ranking", subject="missing", limit=5000)
        if service is not None:
            service.latest_membership_snapshot.return_value = {
                "subject": "2026-09-08", "snapshot_key": "2026-09-08T10:01", "saved_at": 2001.0,
                "payload": {"source": "live", "codes": ["035420"]}, "persistence_state": "pending",
            }
        request("latest-live", endpoint + "top20_membership", subject="2026-09-08", limit=1, prefer_live=True)
        request("prefer-stored", endpoint + "top20_membership", subject="2026-09-08", limit=1, prefer_live=False)
        request("multiple-stored", endpoint + "top20_membership", limit=2, prefer_live=True)
        request("other-kind-stored", endpoint + "ranking", limit=1, prefer_live=True)
        if service is not None:
            service.latest_membership_snapshot.return_value = None
        request("missing-live-fallback", endpoint + "top20_membership", subject="2026-09-08", limit=1, prefer_live=True)
        if service is not None:
            service.latest_membership_snapshot.return_value = {"subject": "2026-09-09", "payload": {"source": "next-live"}}
        request("updated-live", endpoint + "top20_membership", subject="2026-09-09", limit=1, prefer_live=True)
        endpoint = "/api/v1/market/top20-statistics"
        request("unauthorized-statistics", endpoint, authenticated=False, start_date="2026-09-08", end_date="2026-09-08")
        request("missing-dates", endpoint)
        for name, start, end in (("invalid-start", "bad", "2026-09-08"), ("invalid-end", "2026-09-08", "bad"),
                                 ("reversed", "2026-09-09", "2026-09-08"), ("too-wide", "2025-09-07", "2026-09-09"),
                                 ("same-day", "2026-09-08", "2026-09-08"), ("cached-repeat", "2026-09-08", "2026-09-08"),
                                 ("datetime-inputs", "2026-09-08T23:00:00+09:00", "2026-09-09T00:00:00+09:00"),
                                 ("maximum-range", "2025-09-08", "2026-09-09"), ("empty-range", "2026-09-10", "2026-09-10")):
            request(name, endpoint, start_date=start, end_date=end)
    return {"cases": cases, "stored": {kind: store.load_dataset_snapshots(kind, limit=5000) for kind in
            ("ranking", "top20_membership", "top20_index", "market_index_chart", "top20_statistics_day")}}


@contextmanager
def content_contract_app(app_factory, database_path):
    """Use native SQLite and public account bindings, with reproducible write inputs."""
    canonical = "11111111-1111-4111-8111-111111111111"
    origin = "22222222-2222-4222-8222-222222222222"
    store = SQLiteQueryStore(database_path)
    store.initialize()
    at = "2026-10-10T00:00:00+00:00"
    store.register_account_identity({
        "broker": "kiwoom", "environment": "mock", "account_ref": canonical,
        "identity_fingerprint": "a" * 64, "created_at": at,
    })
    binding = store.append_account_binding({
        "credential_profile_id": "contract-alias", "broker": "kiwoom", "environment": "mock",
        "account_ref": canonical, "verified_at": at, "verification_method": "ka00001",
    })
    store.register_account_scope_alias({
        "origin_account_ref": origin, "canonical_account_ref": canonical,
        "broker": "kiwoom", "environment": "mock", "credential_profile_id": "contract-alias",
        "binding_revision": binding["binding_revision"], "verified_at": at,
        "verification_method": "ka00001",
    })
    settings = CentralServerSettings(
        f"sqlite:///{database_path}", "private-token", autonomous_top20_enabled=False,
        market_event_collection_enabled=False, news_history_jobs_enabled=False,
    )
    ids = itertools.count(1)
    clock = [1000.0]
    try:
        with patch("kiwoom_monitor.central_server.database_documents.time", side_effect=lambda: clock[0]), \
                patch("uuid.uuid4", side_effect=lambda: uuid.UUID(int=next(ids))):
            yield app_factory(settings), store, clock
    finally:
        store.close()


def capture_content_http_contract(client, store, clock):
    """Capture complete responses and independently read stored results; never normalize them."""
    headers = {"Authorization": "Bearer private-token"}
    cases = []

    def request(name, method, path, *, authenticated=True, **kwargs):
        clock[0] += 10.0
        response = client.request(method, path, headers=headers if authenticated else {}, **kwargs)
        cases.append({"name": name, "status": response.status_code,
                      "headers": dict(response.headers), "body": response.json()})
        return response

    def document(owner, key, value):
        return {"owner": owner, "key": key, "document": value}

    endpoint = "/api/v1/content/"
    request("unauthorized-read", "GET", endpoint + "app_settings", authenticated=False)
    request("unauthorized-write", "POST", endpoint + "app_settings", authenticated=False,
            json={"documents": [document("pc", "a", {"value": 1})]})
    request("unauthorized-replace", "PUT", endpoint + "theme_stock", authenticated=False,
            json={"documents": []})
    request("unknown-read", "GET", endpoint + "unknown")
    request("unknown-write", "POST", endpoint + "unknown", json={"documents": [document("pc", "a", {})]})
    request("unsupported-replace", "PUT", endpoint + "app_settings", json={"documents": []})
    request("invalid-limit", "GET", endpoint + "app_settings", params={"limit": 0})
    request("invalid-offset", "GET", endpoint + "app_settings", params={"offset": -1})
    request("empty-batch", "POST", endpoint + "app_settings", json={"documents": []})
    request("invalid-key", "POST", endpoint + "app_settings", json={"documents": [document("pc", "", {})]})
    batch = {"documents": [document("pc", "a", {"value": 1}), document("pc", "b", {"value": 2})]}
    request("settings-write", "POST", endpoint + "app_settings", json=batch)
    unchanged = store.load_documents("app_settings", "pc")
    request("identical-write", "POST", endpoint + "app_settings", json=batch)
    if store.load_documents("app_settings", "pc") != unchanged:
        raise AssertionError("identical document write changed its stored timestamp/content")
    request("settings-page", "GET", endpoint + "app_settings", params={"owner": "pc", "limit": 1, "offset": 1})
    request("settings-changed", "POST", endpoint + "app_settings",
            json={"documents": [document("pc", "b", {"value": 3})]})
    request("settings-delta", "GET", endpoint + "app_settings", params={"owner": "pc", "updated_after": 1130})

    canonical = "11111111-1111-4111-8111-111111111111"
    origin = "22222222-2222-4222-8222-222222222222"
    scope = {"origin_broker": "kiwoom", "origin_environment": "mock",
             "origin_account_ref": origin, "canonical_account_ref": canonical}
    journal = document(origin, "fill-1", {**scope, "group_id": "group-1"})
    journal_path = endpoint + "journal_v2_group_overrides"
    request("verified-alias", "POST", journal_path, json={"documents": [journal]})
    for name, changes in (
        ("missing-scope", {"canonical_account_ref": ""}),
        ("invalid-uuid", {"origin_account_ref": "invalid"}),
        ("wrong-broker", {"origin_broker": "other"}),
        ("wrong-environment", {"origin_environment": "real"}),
        ("unverified-alias", {"canonical_account_ref": "33333333-3333-4333-8333-333333333333"}),
    ):
        request(name, "POST", journal_path,
                json={"documents": [{**journal, "document": {**journal["document"], **changes}}]})
    request("wrong-owner", "POST", journal_path, json={"documents": [{**journal, "owner": canonical}]})
    request("mixed-invalid-batch", "POST", journal_path, json={"documents": [
        {**journal, "key": "must-not-save"}, {**journal, "owner": canonical},
    ]})
    request("journal-read", "GET", journal_path, params={"owner": origin})
    legacy = document("group-1", "005930|article-1", {"group_id": "group-1", "stock_code": "005930", "identity": "article-1"})
    request("legacy-link", "POST", endpoint + "journal_news_link", json={"documents": [legacy]})
    request("legacy-wrong-key", "POST", endpoint + "journal_news_link", json={"documents": [{**legacy, "key": "wrong"}]})
    request("legacy-wrong-scope", "POST", endpoint + "journal_news_link", json={"documents": [
        {**legacy, "document": {**legacy["document"], "origin_broker": "kiwoom"}},
    ]})
    link_doc = {**scope, "group_id": "group-1", "stock_code": "005930", "identity": "article-1"}
    link = document(origin, _journal_news_link_key(link_doc), link_doc)
    request("v2-link", "POST", endpoint + "journal_v2_news_links", json={"documents": [link]})
    request("v2-link-wrong-key", "POST", endpoint + "journal_v2_news_links", json={"documents": [{**link, "key": "wrong"}]})
    state = document(origin, "fill-1", {**scope, "collection": "journal_v2_group_overrides",
                                       "owner": origin, "document_key": "fill-1", "is_deleted": True})
    request("v2-deletion-state", "POST", endpoint + "journal_v2_sync_states", json={"documents": [state]})
    request("v2-state-wrong-namespace", "POST", endpoint + "journal_v2_sync_states", json={"documents": [
        {**state, "document": {**state["document"], "collection": "journal_group_overrides"}},
    ]})
    request("v2-state-unverified-alias", "POST", endpoint + "journal_v2_sync_states", json={"documents": [
        {**state, "document": {**state["document"], "canonical_account_ref": "33333333-3333-4333-8333-333333333333"}},
    ]})
    legacy_state = document("legacy", "fill-1", {"collection": "journal_group_overrides", "owner": "legacy", "document_key": "fill-1"})
    request("legacy-deletion-state", "POST", endpoint + "journal_sync_states", json={"documents": [legacy_state]})
    request("legacy-state-wrong-owner", "POST", endpoint + "journal_sync_states", json={"documents": [{**legacy_state, "owner": "other"}]})

    request("unauthorized-history", "GET", "/api/v1/themes/history", authenticated=False)
    request("invalid-history-date", "GET", "/api/v1/themes/history", params={"as_of": -1})
    from kiwoom_monitor.domain.research_contract import THEME_BACKUP_FORMAT, THEME_BACKUP_VERSION
    theme = document("default", "full", {"format": THEME_BACKUP_FORMAT, "version": THEME_BACKUP_VERSION,
                                         "profiles": [], "active_profile": "default"})
    request("theme-snapshot", "PUT", endpoint + "theme_metadata", json={"documents": [theme]})
    request("theme-repeat", "PUT", endpoint + "theme_metadata", json={"documents": [theme]})
    request("theme-revision", "PUT", endpoint + "theme_metadata", json={"documents": [
        {**theme, "document": {**theme["document"], "active_profile": "second"}},
    ]})
    request("theme-history", "GET", "/api/v1/themes/history", params={"limit": 10})
    request("theme-history-before", "GET", "/api/v1/themes/history", params={"as_of": 1000})
    request("theme-clear", "PUT", endpoint + "theme_metadata", json={"documents": []})
    request("theme-read-empty", "GET", endpoint + "theme_metadata")
    collections = ("app_settings", "journal_v2_group_overrides", "journal_news_link", "journal_v2_news_links",
                   "journal_v2_sync_states", "journal_sync_states", "theme_metadata")
    stored = {collection: store.load_documents(collection) for collection in collections}
    stored["theme_history"] = store.load_theme_snapshots()
    if any(row["key"] == "must-not-save" for row in stored["journal_v2_group_overrides"]):
        raise AssertionError("part of a rejected batch was saved")
    return {"cases": cases, "stored": stored}


@contextmanager
def research_contract_app(app_factory, database_path):
    """Use real observation/candidate storage; isolate monitor tasks at their owner boundary."""
    from kiwoom_monitor.central_server.candidate_monitor import CandidateMonitor

    class FixedObservationClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 10, tzinfo=timezone.utc).astimezone(tz)

    ids = itertools.count(1)
    monitors = [MagicMock(spec=CandidateMonitor), MagicMock(spec=CandidateMonitor)]
    for index, monitor in enumerate(monitors, 1):
        monitor.quality = {"status": "WARMUP", "reason": f"fixture-generation-{index}", "monitor_id": f"monitor-{index}"}
    store = SQLiteQueryStore(database_path)
    store.initialize()
    try:
        with patch("uuid.uuid4", side_effect=lambda: uuid.UUID(int=next(ids))), \
                patch("kiwoom_monitor.central_server.database_observation_writes.datetime", FixedObservationClock), \
                patch("kiwoom_monitor.central_server.database_research_export.datetime", FixedObservationClock), \
                patch.object(CandidateMonitor, "from_json", side_effect=monitors):
            for second, code in ((0, "005930"), (30, "000660")):
                save_research_contract_observation(store, second, code)
            for index, expiry in enumerate(("2000-01-01T00:00:00+00:00", "2100-01-01T00:00:00+00:00", "invalid"), 1):
                store.save_shadow_evaluation("fixture", {
                    "decision_id": f"decision-{index}", "decided_at": "2026-09-12T00:00:00+00:00",
                }, {"event_id": f"event-{index}", "available_at": "2026-09-12T00:00:00+00:00",
                    "stock_code": "005930"}, expiry)
            settings = CentralServerSettings(f"sqlite:///{database_path}", "private-token",
                autonomous_top20_enabled=False, market_event_collection_enabled=False,
                news_history_jobs_enabled=False)
            yield app_factory(settings), store, monitors
        if any(monitor.start.await_count != 1 or monitor.close.await_count != 1 for monitor in monitors):
            raise AssertionError("each replaced candidate monitor must start/close exactly once")
    finally:
        store.close()


def save_research_contract_observation(store, second, code):
    key = f"2026-09-12T09:00:{second:02d}"
    payload = {"query_type": "5", "items": [{"stk_cd": code}]}
    store.save_dataset_snapshot("ranking", "5", key, payload, observation=ranking_observation(
        "5", key, payload, datetime(2026, 9, 12, 0, 0, second, tzinfo=timezone.utc), source="fixture"))


def capture_research_read_http_contract(client, store, monitors):
    headers = {"Authorization": "Bearer private-token"}
    cases = []

    def request(name, path, *, authenticated=True, **kwargs):
        response = client.get(path, headers=headers if authenticated else {}, **kwargs)
        cases.append({"name": name, "status": response.status_code,
                      "headers": dict(response.headers), "body": response.json()})
        return response

    observations = "/api/v1/research/observations"
    candidates = "/api/v1/research/candidates"
    params = {"start": "2026-09-12T00:00:00+00:00", "end": "2026-09-13T00:00:00+00:00",
              "kinds": "ranking", "subject": "5", "limit": 1}
    request("observations-unauthorized", observations, authenticated=False, params=params)
    request("missing-parameters", observations)
    for name, changes in (
        ("invalid-limit", {"limit": 1001}), ("negative-cursor", {"cursor": -1}),
        ("naive-time", {"start": "2026-09-12T00:00:00"}),
        ("reversed-range", {"end": "2026-09-11T00:00:00+00:00"}),
        ("unknown-kind", {"kinds": "invalid"}), ("empty-kinds", {"kinds": " , "}),
        ("cursor-without-watermark", {"cursor": 1}), ("unknown-watermark", {"watermark": "unknown"}),
    ):
        request(name, observations, params={**params, **changes})
    first = request("first-page", observations, params=params).json()
    watermark = first["watermark"]
    save_research_contract_observation(store, 45, "035420")
    request("second-page-after-new-observation", observations,
            params={**params, "watermark": watermark, "cursor": first["next_cursor"]})
    request("exhausted-page", observations, params={**params, "watermark": watermark, "cursor": 100})
    for name, changes in (
        ("different-subject", {"subject": "other"}), ("different-kinds", {"kinds": "minute_bar"}),
        ("different-range", {"end": "2026-09-14T00:00:00+00:00"}),
    ):
        request(name, observations, params={**params, "watermark": watermark, **changes})
    request("equivalent-timezone-and-duplicate-kind", observations, params={**params, "watermark": watermark,
        "start": "2026-09-12T09:00:00+09:00", "end": "2026-09-13T09:00:00+09:00", "kinds": " ranking,ranking "})
    request("new-export-includes-late-observation", observations, params={**params, "limit": 1000})
    request("empty-export", observations, params={**params, "subject": "empty"})
    request("candidates-unauthorized", candidates, authenticated=False)
    request("candidates-invalid-limit", candidates, params={"limit": 0})
    request("candidates-negative-sequence", candidates, params={"after_sequence": -1})
    request("candidates-disabled", candidates)
    request("candidates-first-page", candidates, params={"limit": 1})
    request("candidates-next-page", candidates, params={"after_sequence": 1, "limit": 1})
    request("candidates-exhausted", candidates, params={"after_sequence": 3})
    for name, update in (
        ("candidates-generation-one", {"shadow_candidate_enabled": True,
            "shadow_candidate_config": default_shadow_breakout_config().to_dict(), "shadow_candidate_poll_seconds": 2}),
        ("candidates-generation-two", {"shadow_candidate_poll_seconds": 3}),
        ("candidates-disabled-again", {"shadow_candidate_enabled": False}),
    ):
        client.put("/api/v1/settings/operations", headers=headers, json=update).raise_for_status()
        request(name, candidates)
    frozen = store.load_observation_export_page(watermark, 0, 1000)
    if frozen["manifest"]["revision_count"] != 2 or len(frozen["observations"]) != 2:
        raise AssertionError("fixed watermark membership changed after a new observation")
    return {"cases": cases, "stored": {"fixed_export": frozen,
        "raw_candidates": store.load_shadow_candidates(),
        "monitor_lifecycle": [{"start": monitor.start.await_count, "close": monitor.close.await_count} for monitor in monitors]}}


class CentralServerAppTests(unittest.TestCase):
    def test_server_lifespan_preserves_startup_and_repairs_reviewed_cleanup_gaps(self):
        # Retain the original 23 scenarios and baseline; reviewed changes concern shutdown only.
        original = json.loads(LIFESPAN_BASELINE.read_text(encoding="utf-8"))["results"]
        self.assertEqual([(item["mode"], item["failure"]) for item in original], LIFESPAN_CASES)
        for mode, failure in LIFESPAN_CASES:
            with self.subTest(mode=mode, failure=failure), tempfile.TemporaryDirectory() as directory:
                expected = expected_server_lifespan_contract(mode, failure)
                actual = asyncio.run(capture_server_lifespan_contract(
                    create_app, Path(directory) / "lifecycle.sqlite3", mode, failure))
                self.assertEqual(expected, actual)
                before = next(item for item in original if (item["mode"], item["failure"]) == (mode, failure))
                self.assertEqual(before["outcome"], actual["outcome"])
                # The start/HTTP publication prefix must remain byte-for-byte the same.
                def prefix(events):
                    end = next(i for i, event in enumerate(events)
                               if event.endswith(".close") or event == "trace.stop:server_shutdown")
                    return events[:end]
                self.assertEqual(prefix(before["events"]), prefix(actual["events"]))

    def test_server_lifespan_stops_before_dependencies_after_each_close_failure(self):
        for mode, stages in (("legacy", ("mock-bundle.close", "external.close", "news.close", "ai.close",
                                          "top20.close", "collector.close", "market.close", "account-query.close",
                                          "store.close")),
                             ("vault", ("supervisor.close", "mock-owner.close", "vault.close"))):
            for failure in stages:
                with self.subTest(mode=mode, failure=failure), tempfile.TemporaryDirectory() as directory:
                    actual = asyncio.run(capture_server_lifespan_contract(
                        create_app, Path(directory) / "lifecycle.sqlite3", mode, failure, probe_vault=True))
                    expected = expected_server_lifespan_contract(mode, failure)
                    if mode == "vault":
                        expected["vault_reacquire"] = "VAULT_ALREADY_OWNED"
                    self.assertEqual(expected, actual)

    def test_server_lifespan_retains_primary_failure_when_cleanup_also_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            actual = asyncio.run(capture_server_lifespan_contract(
                create_app, Path(directory) / "lifecycle.sqlite3", "vault", "real.start",
                also_fail="credentials.close", probe_vault=True))
        self.assertEqual(actual["outcome"], "controlled-real.start")
        self.assertEqual(actual["injected"], ["real.start", "credentials.close"])
        self.assertEqual(actual["shutdown"], {"state": "failed", "stage": "credentials"})
        self.assertEqual(["SERVER_SHUTDOWN_FAILED:credentials"], actual["error_notes"])
        self.assertEqual(actual["vault_reacquire"], "VAULT_ALREADY_OWNED")
        self.assertNotIn("store.close", actual["events"])

    def test_cancelled_shutdown_owner_and_failed_close_never_release_store_or_report_success(self):
        from kiwoom_monitor.central_server.ai_service import CentralAIService

        async def exercise(directory, cancel_waiter):
            entered, release = asyncio.Event(), asyncio.Event()
            store = SQLiteQueryStore(directory / "lifecycle.sqlite3")
            native_close = store.close
            parent = None
            before_tasks = set(asyncio.all_tasks())
            async def fail_close(_service):
                entered.set()
                await release.wait()
                if cancel_waiter:
                    raise RuntimeError("private cleanup error must not appear in notes")
                raise asyncio.CancelledError()
            try:
                with patch("kiwoom_monitor.central_server.app.create_query_store", return_value=store), \
                     patch.object(store, "close", side_effect=native_close) as close, \
                     patch.object(CentralAIService, "close", fail_close), \
                     patch("kiwoom_monitor.central_server.diagnostic_trace.recover_interrupted", return_value=0), \
                     patch("kiwoom_monitor.central_server.diagnostic_trace.stop", return_value={"state": "off"}):
                    app = create_app(CentralServerSettings(
                        f"sqlite:///{directory / 'lifecycle.sqlite3'}", "private-token", external_market_symbols="",
                        news_naver_stock_enabled=False, news_history_jobs_enabled=False,
                        ai_provider="openai", openai_api_key="controlled-ai-key"))
                    async def run_lifespan():
                        async with app.router.lifespan_context(app):
                            pass
                    parent = asyncio.create_task(run_lifespan())
                    await asyncio.wait_for(entered.wait(), 3)
                    if cancel_waiter:
                        parent.cancel()
                        await asyncio.sleep(0)
                        self.assertFalse(parent.done())
                    release.set()
                    with self.assertRaises(asyncio.CancelledError) as caught:
                        await parent
                    self.assertEqual(["SERVER_SHUTDOWN_FAILED:ai"] if cancel_waiter else [],
                                     getattr(caught.exception, "__notes__", []))
                    self.assertEqual({"state": "failed", "stage": "ai"}, app.state.shutdown_status)
                    close.assert_not_called()
                    await asyncio.sleep(0)
                    self.assertLessEqual(set(asyncio.all_tasks()), before_tasks)
            finally:
                release.set()
                if parent is not None:
                    await asyncio.gather(parent, return_exceptions=True)
                native_close()
        for cancel_waiter in (False, True):
            with self.subTest(cancel_waiter=cancel_waiter), tempfile.TemporaryDirectory() as directory:
                asyncio.run(exercise(Path(directory), cancel_waiter))

    def test_failed_optional_bundle_close_is_not_retried_or_published_as_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            actual = asyncio.run(capture_server_lifespan_contract(
                create_app, Path(directory) / "lifecycle.sqlite3", "legacy", "mock-bundle.start",
                also_fail="mock-bundle.close"))
        self.assertEqual(actual["outcome"], "controlled-mock-bundle.start")
        self.assertEqual(actual["injected"], ["mock-bundle.start", "mock-bundle.close"])
        self.assertEqual(1, actual["events"].count("mock-bundle.close"))
        self.assertEqual({"state": "failed", "stage": "mock_accounts"}, actual["shutdown"])
        self.assertNotIn("mock-bundle:absent", actual["events"])
        self.assertNotIn("top20.start", actual["events"])
        self.assertNotIn("broker.close", actual["events"])
        self.assertNotIn("store.close", actual["events"])

    def test_server_lifespan_drains_native_diagnostic_parent_before_store_close(self):
        import importlib
        import threading
        from kiwoom_monitor.central_server import diagnostic_workloads

        async def exercise(directory):
            store = SQLiteQueryStore(directory / "lifecycle.sqlite3")
            entered, release, committed, joined = (threading.Event() for _ in range(4))
            yielded, finish_body = asyncio.Event(), asyncio.Event()
            native_join, native_close = threading.Thread.join, store.close
            parent = worker = app = None
            close_calls, failures = [], []

            def write():
                entered.set()
                try:
                    if not release.wait(10):
                        raise TimeoutError("test diagnostic writer not released")
                    store.upsert_documents("app_settings", [{"owner": "diagnostic", "key": "final",
                        "document": {"value": 23}}])
                    committed.set()
                except BaseException as error:
                    failures.append(error)

            def join(thread, timeout=None):
                if thread is worker:
                    joined.set()
                    if timeout is not None:
                        return  # Reproduce a bounded join expiring while native work remains.
                return native_join(thread, timeout)

            def close_store():
                close_calls.append("store")
                self.assertTrue(committed.is_set(), "DB released while diagnostic writer was alive")
                self.assertFalse(worker.is_alive())
                native_close()

            try:
                with ExitStack() as stack:
                    stack.enter_context(patch("kiwoom_monitor.central_server.app.create_query_store", return_value=store))
                    stack.enter_context(patch.object(store, "close", side_effect=close_store))
                    stack.enter_context(patch.object(diagnostic_workloads, "control_path", return_value=directory / "diagnostic.json"))
                    # Only external startup is controlled; DiagnosticRuns.close and native join are real.
                    for module_name, class_name in (("rest_broker", "CentralRestBroker"),
                            ("realtime_collector", "CentralRealtimeCollector"),
                            ("autonomous_top20", "AutonomousTop20Service"),
                            ("market_events", "MarketEventService"), ("news_service", "CentralNewsService")):
                        cls = getattr(importlib.import_module("kiwoom_monitor.central_server." + module_name), class_name)
                        stack.enter_context(patch.object(cls, "start", new=AsyncMock()))
                    stack.enter_context(patch("kiwoom_monitor.central_server.diagnostic_trace.recover_interrupted", return_value=0))
                    stack.enter_context(patch("kiwoom_monitor.central_server.diagnostic_trace.stop", return_value={"state": "off"}))
                    app = create_app(CentralServerSettings(f"sqlite:///{directory / 'lifecycle.sqlite3'}", "private-token",
                        external_market_symbols="", news_naver_stock_enabled=False, news_history_jobs_enabled=False,
                        shadow_candidate_enabled=False, top20_outbox_path=str(directory / "outbox.json")))
                    async def lifespan():
                        async with app.router.lifespan_context(app):
                            yielded.set()
                            await finish_body.wait()
                    parent = asyncio.create_task(lifespan())
                    await asyncio.wait_for(yielded.wait(), 3)
                    worker = threading.Thread(target=write, name="test-owned-diagnostic-writer")
                    app.state.diagnostic_runs._worker = worker
                    worker.start()
                    self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                    with patch.object(threading.Thread, "join", new=join):
                        finish_body.set()
                        self.assertTrue(await asyncio.to_thread(joined.wait, 3))
                        for _ in range(3):
                            parent.cancel()
                            await asyncio.sleep(0)
                            self.assertFalse(parent.done())
                        self.assertEqual([], close_calls)
                        self.assertTrue(worker.is_alive())
                        self.assertFalse(committed.is_set())
                        release.set()
                        with self.assertRaises(asyncio.CancelledError):
                            await asyncio.wait_for(asyncio.shield(parent), 3)
                    self.assertEqual([], failures)
                    self.assertEqual(["store"], close_calls)
                    self.assertEqual({"state": "completed", "stage": "store"}, app.state.shutdown_status)
                    self.assertEqual({"value": 23}, store.load_documents("app_settings", "diagnostic")[0]["document"])
            finally:
                release.set()
                finish_body.set()
                if worker is not None:
                    native_join(worker, 5)
                if parent is not None:
                    await asyncio.gather(parent, return_exceptions=True)
                native_close()

        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(exercise(Path(directory)))

    def test_server_lifespan_waits_for_native_commit_despite_repeated_cancellation(self):
        import threading
        from kiwoom_monitor.central_server.credential_store import CredentialStore, CredentialStoreError
        from kiwoom_monitor.central_server.autonomous_top20 import AutonomousTop20Service
        from kiwoom_monitor.central_server.real_runtime import RealCredentialOwner
        from kiwoom_monitor.central_server.mock_runtime import MockCredentialOwner
        from kiwoom_monitor.central_server.mock_automation_supervisor import MockAutomationSupervisor
        from kiwoom_monitor.central_server.news_service import CentralNewsService
        from kiwoom_monitor.central_server.rest_broker import CentralRestBroker
        from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
        from kiwoom_monitor.central_server.market_events import MarketEventService

        async def exercise(directory, scenario):
            store = SQLiteQueryStore(directory / "lifecycle.sqlite3")
            native_close, native_save = store.close, store.save_dataset_snapshots
            entered, release, committed = threading.Event(), threading.Event(), threading.Event()
            yielded, finish_body = asyncio.Event(), asyncio.Event()
            close_calls, primary, notes = [], [], []
            parent, app = None, None
            body_error = RuntimeError("controlled body failure")
            before_tasks = set(asyncio.all_tasks())

            def blocked_save(values):
                entered.set()
                if not release.wait(10):
                    raise TimeoutError("native write was not released by test")
                native_save(values)
                committed.set()

            def close_store():
                close_calls.append("store")
                self.assertTrue(committed.is_set(), "store released before actual COMMIT")
                native_close()

            try:
                with ExitStack() as stack:
                    stack.enter_context(patch("kiwoom_monitor.central_server.app.create_query_store", return_value=store))
                    stack.enter_context(patch.object(store, "close", side_effect=close_store))
                    stack.enter_context(patch.object(store, "save_dataset_snapshots", side_effect=blocked_save))
                    # No close/drain method is mocked; only prevent external startup work.
                    for cls in (CentralRestBroker, CentralRealtimeCollector, AutonomousTop20Service,
                                MarketEventService, RealCredentialOwner, MockCredentialOwner,
                                MockAutomationSupervisor, CentralNewsService):
                        stack.enter_context(patch.object(cls, "start", new=AsyncMock()))
                    stack.enter_context(patch("kiwoom_monitor.central_server.diagnostic_trace.recover_interrupted", return_value=0))
                    stack.enter_context(patch("kiwoom_monitor.central_server.diagnostic_trace.stop", return_value={"state": "off"}))
                    app = create_app(CentralServerSettings(
                        f"sqlite:///{directory / 'lifecycle.sqlite3'}", "private-token",
                        credential_directory=str(directory / "vault"), account_identity_registry_enabled=True,
                        account_identity_hmac_key="x" * 32, external_market_symbols="",
                        news_naver_stock_enabled=False, news_history_jobs_enabled=False,
                        shadow_candidate_enabled=False, top20_outbox_path=str(directory / "outbox.json")))
                    service = app.state.autonomous_top20_service
                    self.assertIsNotNone(service)
                    # Feed the same pending storage contract exercised by the native TOP20 shutdown tests.
                    service._program_snapshots._pending["005930"] = {
                        "subject": "005930", "snapshot_key": "20261010:REALTIME:120000",
                        "payload": {"market": "KRX", "rows": [{"trade_time": "120000",
                                      "net_buy_amount_million_won": 10}]}}

                    async def run_lifespan():
                        try:
                            async with app.router.lifespan_context(app):
                                yielded.set()
                                await finish_body.wait()
                                if scenario == "body-error":
                                    raise body_error
                        except BaseException as error:
                            primary.append(error)
                            notes.extend(getattr(error, "__notes__", []))
                            raise

                    parent = asyncio.create_task(run_lifespan())
                    await asyncio.wait_for(yielded.wait(), 3)
                    if scenario == "body-cancel":
                        parent.cancel()
                    else:
                        finish_body.set()
                    self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                    for _ in range(3):
                        parent.cancel()
                        await asyncio.sleep(0)
                        self.assertFalse(parent.done(), "cancelled waiter abandoned actual storage")
                    self.assertFalse(close_calls)
                    self.assertFalse(committed.is_set())
                    with self.assertRaisesRegex(CredentialStoreError, "VAULT_ALREADY_OWNED"):
                        CredentialStore(directory / "vault", store)
                    release.set()
                    with self.assertRaises(RuntimeError if scenario == "body-error" else asyncio.CancelledError):
                        await asyncio.wait_for(asyncio.shield(parent), 3)
                    self.assertEqual(["store"], close_calls)
                    self.assertEqual(1, len(primary))
                    if scenario == "body-error":
                        self.assertIs(primary[0], body_error)
                    else:
                        self.assertIsInstance(primary[0], asyncio.CancelledError)
                    self.assertEqual([], notes)
                    self.assertEqual({"state": "completed", "stage": "store"}, app.state.shutdown_status)
                    rows = store.load_dataset_snapshots("program_flow", "005930", 10)
                    self.assertEqual(["20261010:REALTIME:120000"], [row["snapshot_key"] for row in rows])
                    self.assertEqual(10, rows[0]["payload"]["rows"][0]["net_buy_amount_million_won"])
                    second = CredentialStore(directory / "vault", store)
                    second.close()
                    await asyncio.sleep(0)
                    self.assertLessEqual(set(asyncio.all_tasks()), before_tasks)
            finally:
                release.set()
                if parent is not None:
                    if not yielded.is_set():
                        parent.cancel()
                    finish_body.set()
                    await asyncio.gather(parent, return_exceptions=True)
                if app is not None:
                    app.state.credential_runtime.vault.close()
                native_close()

        for scenario in ("shutdown-cancel", "body-cancel", "body-error"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                asyncio.run(exercise(Path(directory), scenario))

    def test_realtime_frames_and_owned_tasks_match_pre_extraction_baseline(self):
        expected = json.loads(REALTIME_BASELINE.read_text(encoding="utf-8"))["results"]
        actual = []
        for configured in (False, True):
            with tempfile.TemporaryDirectory() as directory:
                actual.append(asyncio.run(capture_realtime_asgi_contract(
                    create_app, Path(directory) / "realtime.sqlite3", configured)))
        self.assertEqual(expected, json.loads(json.dumps(actual)))

    def test_diagnostic_run_native_reports_and_forwarding_match_pre_extraction_baseline(self):
        expected = json.loads(DIAGNOSTIC_RUN_BASELINE.read_text(encoding="utf-8"))["results"]
        actual = []
        for mode in ("absent", "sqlite", "postgres-seam"):
            with tempfile.TemporaryDirectory() as directory:
                with diagnostic_run_contract_app(create_app, Path(directory) / "diagnostic.sqlite3", mode) as fixture:
                    with TestClient(fixture[0]) as client:
                        actual.append(capture_diagnostic_run_http_contract(client, *fixture))
        self.assertEqual(expected, actual)

    def test_diagnostic_control_native_cas_and_trace_contract_match_pre_extraction_baseline(self):
        expected = json.loads(DIAGNOSTIC_CONTROL_BASELINE.read_text(encoding="utf-8"))["results"]
        actual = []
        for available in (False, True):
            with tempfile.TemporaryDirectory() as directory:
                with diagnostic_control_contract_app(create_app, Path(directory) / "diagnostic.sqlite3", available) as fixture:
                    with TestClient(fixture[0]) as client:
                        actual.append(capture_diagnostic_control_http_contract(client, *fixture))
        self.assertEqual(expected, actual)

    def test_diagnostic_read_responses_match_pre_extraction_baseline(self):
        expected = json.loads(DIAGNOSTIC_READ_BASELINE.read_text(encoding="utf-8"))["results"]
        actual = []
        for configured in (False, True):
            with tempfile.TemporaryDirectory() as directory:
                with diagnostic_read_contract_app(create_app, Path(directory) / "diagnostic.sqlite3", configured) as fixture:
                    with TestClient(fixture[0]) as client:
                        actual.append(capture_diagnostic_read_http_contract(client, *fixture))
        self.assertEqual(expected, actual)

    def test_mock_order_native_ledger_and_gateway_selection_match_pre_extraction_baseline(self) -> None:
        expected = json.loads(MOCK_ORDERS_BASELINE.read_text(encoding="utf-8"))["results"]
        for enabled, baseline in zip((False, True), expected, strict=True):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "mock-orders.sqlite3"
                with mock_orders_contract_app(create_app, path, enabled) as (app, store, bundles, transports, publication):
                    with TestClient(app) as client:
                        actual = capture_mock_orders_http_contract(client, app, store, bundles, transports, publication, path, enabled)
                    self.assertEqual(baseline, actual)

    def test_operational_settings_save_apply_recovery_match_pre_extraction_baseline(self) -> None:
        expected = json.loads(OPERATIONS_BASELINE.read_text(encoding="utf-8"))["results"]
        for configured, baseline in zip((False, True), expected, strict=True):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "operations.sqlite3"
                with operations_contract_app(create_app, path, configured) as (app, store, request_store, services, events, monitors, condition):
                    with TestClient(app) as client:
                        actual = capture_operations_http_contract(client, app, store, request_store, services, events, monitors, condition, path, configured)
                    self.assertEqual(baseline, actual)

    def test_account_queries_native_sessions_and_owner_publication_match_pre_extraction_baseline(self) -> None:
        expected = json.loads(ACCOUNT_QUERY_BASELINE.read_text(encoding="utf-8"))["results"]
        for mode, baseline in zip(("absent", "mock", "real"), expected, strict=True):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "accounts.sqlite3"
                with account_query_contract_app(create_app, path, mode) as (app, store, request_store, contexts, managers, brokers, clock, publish):
                    with TestClient(app) as client:
                        actual = capture_account_query_http_contract(client, app, store, request_store, contexts, managers, brokers, clock, publish, path, mode)
                    self.assertEqual(baseline, actual)

    def test_market_query_responses_archive_and_transport_match_pre_extraction_baseline(self) -> None:
        expected = json.loads(MARKET_QUERY_BASELINE.read_text(encoding="utf-8"))["results"]
        for configured, baseline in zip((False, True), expected, strict=True):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "query.sqlite3"
                with market_query_contract_app(create_app, path, configured=configured) as (app, store, broker, request_store):
                    with TestClient(app) as client:
                        actual = capture_market_query_http_contract(client, app, store, broker, request_store, path)
            self.assertEqual([case["name"] for case in baseline], [case["name"] for case in actual])
            for original, observed in zip(baseline, actual, strict=True):
                with self.subTest(configured=configured, case=original["name"]):
                    self.assertEqual(original, observed)

    def test_market_event_responses_and_native_reads_match_pre_extraction_baseline(self) -> None:
        expected = json.loads(MARKET_EVENT_BASELINE.read_text(encoding="utf-8"))["results"]
        for configured, baseline in zip((False, True), expected, strict=True):
            with tempfile.TemporaryDirectory() as directory:
                database_path = Path(directory) / "events.sqlite3"
                with market_event_contract_app(create_app, database_path, configured=configured) as (app, store, service, request_store):
                    with TestClient(app) as client:
                        actual = capture_market_event_http_contract(client, store, service, database_path, request_store)
            # JSON normalizes recorded call arguments to arrays, as in the HTTP baseline.
            actual = json.loads(json.dumps(actual, ensure_ascii=False))
            self.assertEqual([case["name"] for case in baseline], [case["name"] for case in actual])
            for original, observed in zip(baseline, actual, strict=True):
                with self.subTest(configured=configured, case=original["name"]):
                    self.assertEqual(original, observed)

    def test_market_read_http_storage_and_current_collector_match_pre_extraction_baseline(self) -> None:
        expected = json.loads(MARKET_READ_BASELINE.read_text(encoding="utf-8"))["result"]
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "market.sqlite3"
            with market_read_contract_app(create_app, database_path) as (app, store, collectors):
                with TestClient(app) as client:
                    actual = capture_market_read_http_contract(client, app, store, collectors, database_path)
        self.assertEqual([case["name"] for case in expected["cases"]], [case["name"] for case in actual["cases"]])
        for original, observed in zip(expected["cases"], actual["cases"], strict=True):
            with self.subTest(case=original["name"]):
                self.assertEqual(original, observed)
        self.assertEqual(expected["pending"], actual["pending"])
        self.assertEqual(expected["stored_minutes"], actual["stored_minutes"])

    def test_market_dataset_http_storage_and_read_selection_match_pre_extraction_baseline(self) -> None:
        expected = json.loads(MARKET_DATASET_BASELINE.read_text(encoding="utf-8"))["results"]
        for configured, baseline in zip((True, False), expected, strict=True):
            with tempfile.TemporaryDirectory() as directory:
                with market_dataset_contract_app(create_app, Path(directory) / "market.sqlite3", configured=configured) as (app, store, service, request_store):
                    with TestClient(app) as client:
                        actual = capture_market_dataset_http_contract(client, store, service, request_store)
            self.assertEqual([case["name"] for case in baseline["cases"]], [case["name"] for case in actual["cases"]])
            for original, observed in zip(baseline["cases"], actual["cases"], strict=True):
                with self.subTest(configured=configured, case=original["name"]):
                    self.assertEqual(original, observed)
            self.assertEqual(baseline["stored"], actual["stored"])

    def test_research_read_http_and_fixed_dataset_match_pre_extraction_baseline(self) -> None:
        baseline = Path(__file__).resolve().parents[2] / "tests/fixtures/api_contract_baselines/research_reads_http_v1.json"
        expected = json.loads(baseline.read_text(encoding="utf-8"))["result"]
        with tempfile.TemporaryDirectory() as directory:
            with research_contract_app(create_app, Path(directory) / "research.sqlite3") as (app, store, monitors):
                with TestClient(app) as client:
                    actual = capture_research_read_http_contract(client, store, monitors)
        self.assertEqual([case["name"] for case in expected["cases"]], [case["name"] for case in actual["cases"]])
        for original, observed in zip(expected["cases"], actual["cases"], strict=True):
            with self.subTest(case=original["name"]):
                self.assertEqual(original, observed)
        self.assertEqual(expected["stored"], actual["stored"])

    def test_content_http_and_storage_match_pre_extraction_baseline(self) -> None:
        expected = json.loads(CONTENT_BASELINE.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            with content_contract_app(create_app, Path(directory) / "content.sqlite3") as (app, store, clock):
                with TestClient(app) as client:
                    actual = capture_content_http_contract(client, store, clock)
        self.assertEqual([case["name"] for case in expected["result"]["cases"]],
                         [case["name"] for case in actual["cases"]])
        for original, observed in zip(expected["result"]["cases"], actual["cases"], strict=True):
            with self.subTest(case=original["name"]):
                self.assertEqual(original, observed)
        self.assertEqual(expected["result"]["stored"], actual["stored"])

    def test_minute_routes_restore_current_ram_minute_without_persisting_or_marking_complete(self) -> None:
        from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
        from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
        from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import TradeTick
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'live.sqlite3'
            store = SQLiteQueryStore(path)
            store.initialize()
            at = datetime(2026, 10, 6, 10, 4, 40)
            collector = CentralRealtimeCollector(lambda: 'token', 'real', RealtimeHub(), lambda: at, store)
            for minute, volume in ((3, 7), (4, 13)):
                seen = at.replace(minute=minute)
                collector._minute_bars.add(TradeTick('005930', 100, None, None, volume, None,
                                                    seen.strftime('%H%M%S'), market='SOR'), seen, seen.timestamp())
                if minute == 3:
                    store.save_minute_bars(collector._minute_bars.drain_dirty())
            settings = CentralServerSettings(f'sqlite:///{path}', 'private-token',
                                             autonomous_top20_enabled=False, market_event_collection_enabled=False)
            app = create_app(settings)
            with TestClient(app) as client:
                app.state.realtime_collector = collector
                headers = {'Authorization': 'Bearer private-token'}
                for route, date_key in (('minute-bars', 'trading_date'), ('recent-minute-bars', 'end_date')):
                    params = {'code': '005930', date_key: '2026-10-06', 'market': 'COMBINED'}
                    if route == 'recent-minute-bars':
                        params['trading_days'] = 1
                    self.assertEqual(401, client.get('/api/v1/market/' + route, params=params).status_code)
                    response = client.get('/api/v1/market/' + route, params=params, headers=headers)
                    self.assertEqual(200, response.status_code, response.text)
                    data = response.json()
                    self.assertEqual([('10:03', 7), ('10:04', 13)],
                                     [(bar['minute'], bar['volume']) for bar in data['bars']])
                    if route == 'minute-bars':
                        self.assertFalse(data['coverage']['complete'])
            self.assertEqual(['10:03'], [bar['minute'] for bar in store.load_minute_bars('005930', '2026-10-06')])
            self.assertEqual(1, len(collector._minute_bars.pending_bars('005930', '2026-10-06')))
            store.close()

    def test_verified_account_alias_round_trips_journal_content_and_rejects_wrong_scope(self) -> None:
        import uuid

        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2]) as directory:
            database_path = Path(directory) / "central.sqlite3"
            account_ref, origin = str(uuid.uuid4()), str(uuid.uuid4())
            now = datetime.now(timezone.utc).isoformat()
            store = SQLiteQueryStore(database_path)
            store.initialize()
            store.register_account_identity({
                "broker": "kiwoom", "environment": "mock", "account_ref": account_ref,
                "identity_fingerprint": "a" * 64, "created_at": now,
            })
            binding = store.append_account_binding({
                "credential_profile_id": "alias-test", "broker": "kiwoom", "environment": "mock",
                "account_ref": account_ref, "verified_at": now, "verification_method": "ka00001",
            })
            store.register_account_scope_alias({
                "origin_account_ref": origin, "canonical_account_ref": account_ref,
                "broker": "kiwoom", "environment": "mock", "credential_profile_id": "alias-test",
                "binding_revision": binding["binding_revision"], "verified_at": now,
                "verification_method": "ka00001",
            })
            store.close()
            settings = CentralServerSettings(
                f"sqlite:///{database_path}", "private-token", autonomous_top20_enabled=False,
                market_event_collection_enabled=False,
            )
            endpoint = "/api/v1/content/journal_v2_group_overrides"
            headers = {"Authorization": "Bearer private-token"}
            document = {
                "fill_id": "fill-1", "group_id": "manual:alias",
                "origin_broker": "kiwoom", "origin_environment": "mock",
                "origin_account_ref": origin, "canonical_account_ref": account_ref,
            }
            with TestClient(create_app(settings)) as client:
                body = {"documents": [{"owner": origin, "key": "fill-1", "document": document}]}
                self.assertEqual(401, client.post(endpoint, json=body).status_code)
                saved = client.post(endpoint, headers=headers, json=body)
                self.assertEqual(200, saved.status_code, saved.text)
                self.assertEqual(1, saved.json()["saved"])
                for changes in ({"canonical_account_ref": str(uuid.uuid4())},
                                {"origin_environment": "real"}):
                    response = client.post(endpoint, headers=headers, json={"documents": [{
                        "owner": origin, "key": "fill-1", "document": {**document, **changes},
                    }]})
                    self.assertEqual(422, response.status_code, response.text)
                loaded = client.get(endpoint, params={"owner": origin}, headers=headers)
                self.assertEqual(200, loaded.status_code)
                self.assertEqual(document, loaded.json()["documents"][0]["document"])

    def test_external_market_bars_round_trip_through_authenticated_route(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2]) as directory:
            database_path = Path(directory) / "monitor.sqlite3"
            store = SQLiteQueryStore(database_path)
            store.initialize()
            base = {
                "provider": "yahoo_delayed", "instrument": "NASDAQ_FUTURES",
                "contract": "MNQU26.CME", "timeframe": "5m", "open": 100.0,
                "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10.0,
                "updated_at": 100.0,
            }
            store.save_external_bars([
                {**base, "bar_time": "2026-10-04T00:00:00Z"},
                {**base, "bar_time": "2026-10-04T00:05:00Z", "close": 101.0},
            ])
            settings = CentralServerSettings(f"sqlite:///{database_path}", "private-token")
            endpoint = "/api/v1/market/external-bars"
            with TestClient(create_app(settings)) as client:
                params = {"instrument": "nasdaq_futures", "timeframe": "5m"}
                self.assertEqual(401, client.get(endpoint, params=params).status_code)
                response = client.get(endpoint, params=params,
                                      headers={"Authorization": "Bearer private-token"})
                self.assertEqual(200, response.status_code)
                document = response.json()
                self.assertEqual("NASDAQ_FUTURES", document["instrument"])
                self.assertEqual("5m", document["timeframe"])
                self.assertEqual(["2026-10-04T00:00:00Z", "2026-10-04T00:05:00Z"],
                                 [row["bar_time"] for row in document["bars"]])
                self.assertEqual(101.0, document["bars"][-1]["close"])
                self.assertEqual(11, len(document["bars"][-1]))
                limited = client.get(endpoint, params={**params, "limit": 1},
                                     headers={"Authorization": "Bearer private-token"})
                self.assertEqual(["2026-10-04T00:05:00Z"],
                                 [row["bar_time"] for row in limited.json()["bars"]])

    def test_db_call_diagnostic_requires_auth_and_rejects_unbounded_requests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
            )
            with TestClient(create_app(settings)) as client:
                endpoint = "/api/v1/diagnostics/db-calls"
                now = time.time()
                self.assertEqual(401, client.get(endpoint, params={"start": now - 10,
                                                                 "end": now}).status_code)
                headers = {"Authorization": "Bearer private-token"}
                response = client.get(endpoint, params={"start": now - 10, "end": now},
                                      headers=headers)
                self.assertEqual(200, response.status_code)
                self.assertEqual("opt_in_observed_calls_only", response.json()["coverage"])
                self.assertEqual(os.getpid(), response.json()["producer"]["pid"])
                self.assertTrue(response.json()["producer"]["process_id"])
                self.assertEqual({}, response.json()["writers"])
                self.assertEqual({}, response.json()["readers"])
                self.assertEqual(400, client.get(endpoint,
                    params={"start": now - 3600, "end": now}, headers=headers).status_code)
                self.assertEqual(400, client.get(endpoint,
                    params={"start": now - 10, "end": now, "mode": "invalid"},
                    headers=headers).status_code)

    def test_news_job_claim_plan_api_requires_auth_and_postgres(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
            )
            with TestClient(create_app(settings)) as client:
                endpoint = "/api/v1/diagnostics/news-job-claim-plan"
                self.assertEqual(401, client.get(endpoint).status_code)
                response = client.get(endpoint, headers={"Authorization": "Bearer private-token"})
        self.assertEqual(501, response.status_code)
        self.assertEqual("POSTGRES_DIAGNOSTIC_UNAVAILABLE", response.json()["detail"])

    def test_news_job_claim_readonly_analyze_api_requires_auth_and_postgres(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
            )
            with TestClient(create_app(settings)) as client:
                endpoint = "/api/v1/diagnostics/news-job-claim-readonly-analyze"
                self.assertEqual(401, client.get(endpoint).status_code)
                response = client.get(endpoint, headers={"Authorization": "Bearer private-token"})
        self.assertEqual(501, response.status_code)
        self.assertEqual("POSTGRES_DIAGNOSTIC_UNAVAILABLE", response.json()["detail"])
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
            )
            result = {"mode": "read_only_analyze_without_row_lock", "nodes": []}
            with patch.object(SQLiteQueryStore, "analyze_news_job_claim_read_only",
                              create=True, return_value=result) as analyze:
                with TestClient(create_app(settings)) as client:
                    headers = {"Authorization": "Bearer private-token"}
                    self.assertEqual(result, client.get(endpoint, headers=headers).json())
                    self.assertEqual(400, client.get(endpoint, params={"stage": "AI"},
                                                     headers=headers).status_code)
            analyze.assert_called_once_with("BODY")

    def test_diagnostic_status_reads_master_and_children_together(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "monitor.sqlite3"
            control = Path(directory) / "diagnostic.json"
            settings = CentralServerSettings(f"sqlite:///{database}", "private-token")
            with patch.dict("os.environ", {"KIWOOM_DIAGNOSTIC_WORKLOAD_PATH": str(control)}):
                with TestClient(create_app(settings)) as client:
                    response = client.get("/api/v1/diagnostics/workloads",
                                          headers={"Authorization": "Bearer private-token"})
        self.assertEqual(200, response.status_code)
        self.assertFalse(response.json()["diagnostic_tool"]["enabled"])
        self.assertFalse(response.json()["metrics_capture"]["enabled"])
        external = response.json()["workloads"]["external_market"]
        self.assertTrue(external["configured"])
        self.assertFalse(external["effective"])
        self.assertFalse(external["runtime"]["operational_enabled"])
        self.assertFalse(external["runtime"]["running"])
        self.assertEqual(300, external["runtime"]["poll_seconds"])

    def test_latest_market_caps_returns_old_0b_reference_without_old_price(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            store = SQLiteQueryStore(path)
            store.initialize()
            store.save_realtime_snapshots([{
                "event_type": "trade", "item_key": "005930", "received_at": 1.0,
                "event": {"type": "trade", "payload": {
                    "code": "005930", "current_price": 70_000,
                    "change_rate": 3.5, "market_cap_eok": 4_321_000,
                }},
            }])
            store.close()
            settings = CentralServerSettings(f"sqlite:///{path}", "private-token")
            with TestClient(create_app(settings)) as client:
                response = client.get(
                    "/api/v1/market/latest-market-caps",
                    params=[("codes", "005930")],
                    headers={"Authorization": "Bearer private-token"},
                )

        self.assertEqual(200, response.status_code)
        self.assertEqual(4_321_000, response.json()["market_caps"][0]["market_cap_eok"])
        self.assertNotIn("current_price", response.text)
        self.assertNotIn("change_rate", response.text)

    def test_realtime_subscription_restores_only_fresh_requested_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            store = SQLiteQueryStore(path)
            store.initialize()
            now = time.time()
            fresh = {"type": "trade", "payload": {"code": "005930", "current_price": 70000}}
            market_state = {"type": "market_state", "payload": {"market": "kospi"}}
            store.save_realtime_snapshots([
                {"event_type": "market_state", "item_key": "kospi", "received_at": now - 2,
                 "event": market_state},
                {"event_type": "trade", "item_key": "005930", "received_at": now - 1,
                 "event": fresh},
                {"event_type": "trade", "item_key": "000660", "received_at": now - 3600,
                 "event": {"type": "trade", "payload": {"code": "000660"}}},
                {"event_type": "trade", "item_key": "035420", "received_at": now,
                 "event": {"type": "trade", "payload": {"code": "035420"}}},
            ])
            store.close()
            settings = CentralServerSettings(f"sqlite:///{path}", "private-token")
            with TestClient(create_app(settings)) as client:
                with client.websocket_connect(
                    "/api/v1/realtime", headers={"Authorization": "Bearer private-token"},
                ) as socket:
                    self.assertEqual("ready", socket.receive_json()["type"])
                    socket.send_json({"type": "subscribe", "codes": ["005930", "000660"]})
                    self.assertEqual("subscribed", socket.receive_json()["type"])
                    self.assertEqual(market_state, socket.receive_json())
                    self.assertEqual(fresh, socket.receive_json())
                    self.assertEqual("central_ready", socket.receive_json()["type"])

    def test_combined_minute_bars_prefer_sor_and_never_add_all_three_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            store = SQLiteQueryStore(path)
            store.initialize()
            base = {
                "trading_date": "2026-09-14", "minute": "10:00", "code": "005930",
                "open": 100, "high": 110, "low": 90, "close": 105,
                "volume": 10, "trade_value_million_won": 20, "updated_at": 1.0,
            }
            store.save_minute_bars([
                {**base, "market": "KRX"},
                {**base, "market": "NXT", "trade_value_million_won": 5},
                {**base, "market": "SOR", "trade_value_million_won": 22},
            ])
            store.upsert_documents("market_data_coverage", [{
                "owner": "2026-09-14:005930:KRX", "key": "complete", "document": {
                    "kind": "minute", "window_closed": True, "session_finalized": True,
                    "as_of": "2026-09-14",
                },
            }])
            store.close()
            settings = CentralServerSettings(f"sqlite:///{path}", "private-token")
            with TestClient(create_app(settings)) as client:
                response = client.get(
                    "/api/v1/market/minute-bars",
                    params={"code": "005930", "trading_date": "2026-09-14", "market": "COMBINED"},
                    headers={"Authorization": "Bearer private-token"},
                )

        self.assertEqual(200, response.status_code)
        self.assertEqual(22, response.json()["bars"][0]["trade_value_million_won"])
        self.assertEqual("SOR", response.json()["bars"][0]["source_market"])
        self.assertTrue(response.json()["coverage"]["complete"])

    def test_trade_value_comparison_summary_excludes_partial_venue_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            store = SQLiteQueryStore(path)
            store.initialize()
            owner = "2026-09-14:005930"
            store.upsert_documents("minute_trade_value_comparisons", [
                {
                    "owner": owner, "key": "10:00", "document": {
                        "query_scope": "KRX+NXT", "compared_at": "2026-09-14T10:01:00+09:00",
                        "realtime_trade_value_million_won": 110,
                        "query_trade_value_million_won": 100,
                        "difference_percent": 10.0,
                    },
                },
                {
                    "owner": owner, "key": "10:01", "document": {
                        "query_scope": "KRX+NXT", "compared_at": "2026-09-14T10:02:00+09:00",
                        "realtime_trade_value_million_won": 90,
                        "query_trade_value_million_won": 100,
                        "difference_percent": -10.0,
                    },
                },
                {
                    "owner": owner, "key": "10:02", "document": {
                        "query_scope": "KRX", "compared_at": "2026-09-14T10:03:00+09:00",
                        "realtime_trade_value_million_won": 50,
                        "query_trade_value_million_won": 40,
                        "difference_percent": 25.0,
                    },
                },
            ])
            store.close()
            settings = CentralServerSettings(f"sqlite:///{path}", "private-token")
            with TestClient(create_app(settings)) as client:
                response = client.get(
                    "/api/v1/market/trade-value-comparisons",
                    params={"code": "005930", "trading_date": "2026-09-14"},
                    headers={"Authorization": "Bearer private-token"},
                )

        self.assertEqual(200, response.status_code)
        summary = response.json()["summary"]
        self.assertEqual(2, summary["complete_count"])
        self.assertEqual(1, summary["partial_count"])
        self.assertEqual({"KRX": 1, "KRX+NXT": 2}, summary["scope_counts"])
        self.assertEqual(200, summary["total_realtime_trade_value_million_won"])
        self.assertEqual(200, summary["total_query_trade_value_million_won"])
        self.assertEqual(0.0, summary["total_difference_percent"])
        self.assertEqual(0.0, summary["average_difference_percent"])
        self.assertEqual(10.0, summary["mean_absolute_difference_percent"])
        self.assertEqual(10.0, summary["max_absolute_difference_percent"])

    def test_recent_minute_bars_reads_two_stored_trading_days_without_broker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            store = SQLiteQueryStore(path)
            store.initialize()
            for day, minute in (("2026-09-11", "15:20"), ("2026-09-14", "10:00")):
                store.save_minute_bars([{
                    "trading_date": day, "minute": minute, "code": "005930",
                    "market": "KRX", "open": 100, "high": 110, "low": 90,
                    "close": 105, "volume": 10, "trade_value_million_won": 20,
                    "updated_at": 1.0,
                }])
            store.close()
            settings = CentralServerSettings(
                f"sqlite:///{path}", "private-token",
            )
            with TestClient(create_app(settings)) as client:
                response = client.get(
                    "/api/v1/market/recent-minute-bars",
                    params={
                        "code": "005930", "end_date": "2026-09-14",
                        "market": "KRX", "trading_days": 2,
                    },
                    headers={"Authorization": "Bearer private-token"},
                )

        self.assertEqual(200, response.status_code)
        self.assertEqual(
            ["2026-09-11", "2026-09-14"], response.json()["trading_days"],
        )
        self.assertEqual(2, len(response.json()["bars"]))

    def test_authenticated_news_search_returns_confirmed_n3_article_to_central_client(self) -> None:
        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.close()

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            store = SQLiteQueryStore(path)
            store.initialize()
            store.save_news_source_page({
                "source_id": "source", "query_text": "증권", "run_id": "run",
                "items": [{
                    "identity": "https://o/n3",
                    "document": {
                        "title": "삼성전자 공급계약 체결", "description": "500억원 수주",
                        "link": "https://n/n3", "original_link": "https://o/n3",
                        "published_at": "2026-09-12T01:00:00+00:00",
                    },
                    "targets": [{
                        "stock_code": "005930", "stock_name": "삼성전자",
                        "relation_status": "confirmed", "rule_version": "exact-v1",
                    }],
                }],
            })
            store.close()
            settings = CentralServerSettings(
                f"sqlite:///{path}", "private-token",
                naver_news_client_id="configured", naver_news_client_secret="configured",
                news_query_set_enabled=False, news_history_jobs_enabled=False,
                news_naver_market_enabled=False,
            )
            with patch(
                "kiwoom_monitor.infrastructure.naver_news.NaverNewsClient.search", return_value=(),
            ), TestClient(create_app(settings)) as api_client:
                # Startup may enqueue background news work. Only attribute new
                # jobs created after the client is ready to these reads.
                with closing(sqlite3.connect(path)) as connection:
                    jobs_before = int(connection.execute("SELECT COUNT(*) FROM central_news_jobs").fetchone()[0])
                def opener(request, **_kwargs):
                    path_and_query = urlsplit(request.full_url)
                    response = api_client.request(
                        request.get_method(), path_and_query.path + (f"?{path_and_query.query}" if path_and_query.query else ""),
                        headers=dict(request.header_items()), content=request.data,
                    )
                    return Response(response.content)

                items = CentralNewsClient(
                    "http://testserver", "private-token", opener=opener,
                ).search("005930", "삼성전자")
                stored_items, next_offset = CentralNewsClient(
                    "http://testserver", "private-token", opener=opener,
                ).stored_page("005930", "삼성전자")
                with closing(sqlite3.connect(path)) as connection:
                    jobs_after = int(connection.execute("SELECT COUNT(*) FROM central_news_jobs").fetchone()[0])

        self.assertEqual("https://o/n3", items[0].original_link)
        self.assertEqual("수주·계약", items[0].assessment.category)
        self.assertEqual("https://o/n3", stored_items[0].original_link)
        self.assertIsNone(next_offset)
        self.assertEqual(jobs_before, jobs_after)

    def test_app_starts_with_kiwoom_and_autonomous_top20_enabled(self) -> None:
        """Protect the NAS deployment combination that creates the persistent outbox."""
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
                kiwoom_app_key="app-key", kiwoom_secret_key="secret-key",
                autonomous_top20_enabled=True,
                autonomous_top20_minute_backfill_enabled=False,
                top20_outbox_path=str(Path(directory) / "top20-outbox.json"),
            )

            app = create_app(settings)

        self.assertIsNotNone(app)
        self.assertFalse(app.state.autonomous_top20_service._minute_backfill_enabled)

    def test_live_top20_endpoint_does_not_wait_for_database_projection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
                kiwoom_app_key="app-key", kiwoom_secret_key="secret-key",
                autonomous_top20_enabled=True, market_event_collection_enabled=False,
                top20_outbox_path=str(Path(directory) / "top20-outbox.json"),
            )
            app = create_app(settings)
            service = app.state.autonomous_top20_service
            self.assertIsNotNone(service)
            service._latest_membership_snapshot = {
                "subject": "2026-09-22",
                "snapshot_key": "2026-09-22T18:00:00",
                "saved_at": 1.0,
                "payload": {"observed_at": "2026-09-22T18:00:00", "codes": ["005930"],
                            "items": [{"stk_cd": "005930", "stk_nm": "삼성전자"}] * 20},
                "persistence_state": "pending",
            }
            async def read_snapshots():
                import httpx
                # As before, inspect the already-created real service without
                # starting network producers; use the public HTTP contract.
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                    results = []
                    for prefer_live in (True, False):
                        response = await client.get(
                            "/api/v1/market/snapshots/top20_membership",
                            params={"subject": "2026-09-22", "limit": 1, "prefer_live": prefer_live},
                            headers={"Authorization": "Bearer private-token"},
                        )
                        self.assertEqual(200, response.status_code)
                        results.append(response.json())
                    return results

            live, persisted = asyncio.run(read_snapshots())

        self.assertEqual("pending", live["snapshots"][0]["persistence_state"])
        self.assertEqual([], persisted["snapshots"])

    def test_mock_account_monitor_is_wired_only_with_explicit_mock_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
                kiwoom_environment="real", kiwoom_app_key="real-key", kiwoom_secret_key="real-secret",
                kiwoom_mock_app_key="mock-key", kiwoom_mock_secret_key="mock-secret",
                autonomous_top20_enabled=False, market_event_collection_enabled=False,
                mock_account_monitor_enabled=True, mock_account_ref="opaque-account",
                mock_execution_run_id="forward-run",
            )
            app = create_app(settings)
        self.assertIsNotNone(app.state.mock_account_monitor)
        self.assertIsNotNone(app.state.mock_account_realtime)
        account_client = app.state.mock_account_monitor._reader._broker._client
        self.assertEqual("mock", account_client.environment)
        self.assertEqual(1.0, account_client._base_request_interval_seconds)

    def test_mock_identity_token_failure_disables_only_mock_features(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
                kiwoom_mock_app_key="mock-key", kiwoom_mock_secret_key="mock-secret",
                autonomous_top20_enabled=False, market_event_collection_enabled=False,
                mock_account_monitor_enabled=True, mock_order_transport_enabled=True,
                mock_account_ref="opaque-account", mock_execution_run_id="forward-run",
                account_identity_registry_enabled=True,
                account_identity_hmac_key="x" * 32,
            )
            with patch(
                "kiwoom_monitor.infrastructure.kiwoom_rest.account_identity."
                "KiwoomAccountIdentityReader.verify",
                new=AsyncMock(side_effect=RuntimeError("mock token unavailable")),
            ), TestClient(create_app(settings)) as client:
                health = client.get("/health").json()
                order = client.post(
                    "/api/v1/mock/orders",
                    headers={"Authorization": "Bearer private-token"},
                    json={
                        "request_id": "req-1", "symbol": "005930", "side": "BUY",
                        "quantity": 1, "limit_price": 1000,
                    },
                )

        self.assertEqual("ok", health["status"])
        self.assertFalse(health["mock_account_available"])
        self.assertEqual("MOCK_ACCOUNT_STARTUP_FAILED", health["mock_account_error"])
        self.assertEqual(503, order.status_code)

    def test_mock_identity_mismatch_keeps_server_alive_without_publishing_binding(self) -> None:
        from kiwoom_monitor.infrastructure.kiwoom_rest.account_identity import VerifiedAccountIdentity
        from kiwoom_monitor.domain.order_contract import AccountEnvironment

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "monitor.sqlite3"
            settings = CentralServerSettings(
                f"sqlite:///{database}", "private-token",
                kiwoom_mock_app_key="mock-key", kiwoom_mock_secret_key="mock-secret",
                autonomous_top20_enabled=False, market_event_collection_enabled=False,
                mock_account_monitor_enabled=True, mock_order_transport_enabled=True,
                mock_account_ref="different-account", mock_execution_run_id="forward-run",
                account_identity_registry_enabled=True, account_identity_hmac_key="x" * 32,
            )
            identity = VerifiedAccountIdentity(
                "kiwoom", AccountEnvironment.MOCK, "a" * 64, datetime.now(timezone.utc),
            )
            with patch(
                "kiwoom_monitor.infrastructure.kiwoom_rest.account_identity."
                "KiwoomAccountIdentityReader.verify", new=AsyncMock(return_value=identity),
            ), TestClient(create_app(settings)) as client:
                health = client.get("/health").json()
                self.assertEqual("ok", health["status"])
                self.assertFalse(health["mock_account_available"])
                self.assertEqual("ACCOUNT_CONTEXT_MISMATCH", health["mock_account_error"])
            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(0, connection.execute(
                    "SELECT COUNT(*) FROM central_account_binding_revisions"
                ).fetchone()[0])

    def test_mock_monitor_start_failure_is_isolated_and_error_is_safe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
                kiwoom_mock_app_key="mock-key", kiwoom_mock_secret_key="mock-secret",
                autonomous_top20_enabled=False, market_event_collection_enabled=False,
                mock_account_monitor_enabled=True, mock_account_ref="account-1",
                mock_execution_run_id="run-1",
            )
            with patch(
                "kiwoom_monitor.central_server.mock_account_monitor.MockAccountMonitor.start",
                new=AsyncMock(side_effect=RuntimeError("fake-sensitive-upstream-response")),
            ), self.assertLogs("kiwoom_monitor.central_server.app", level="ERROR") as logged:
                with TestClient(create_app(settings)) as client:
                    health = client.get("/health").json()
                    self.assertEqual("ok", health["status"])
                    self.assertFalse(health["mock_account_available"])
                    self.assertEqual("MOCK_ACCOUNT_STARTUP_FAILED", health["mock_account_error"])
                    self.assertIsNone(client.app.state.mock_account_bundle)
            self.assertNotIn("fake-sensitive-upstream-response", str(logged.output))

    def test_mock_order_response_keeps_original_gateway_after_runtime_publication(self) -> None:
        from types import SimpleNamespace
        from kiwoom_monitor.domain.order_contract import OrderSide, OrderType, OrderState

        with tempfile.TemporaryDirectory() as directory:
            now = datetime.now(timezone.utc)
            record = SimpleNamespace(
                intent=SimpleNamespace(
                    intent_id="intent-1", decision_id="manual:request-1", run_id="run-1",
                    environment="mock", symbol="005930", venue="KRX", side=OrderSide.BUY,
                    quantity=1, order_type=OrderType.LIMIT, limit_price=1000,
                    policy_version="test/v1", created_at=now, expires_at=now,
                ), state=OrderState.SUBMISSION_UNKNOWN, broker_order_id="", filled_quantity=0,
                updated_at=now,
            )
            class Gateway:
                async def submit_limit(self, **kwargs):
                    publication["current"] = "disabled-profile"
                    return record
                def events(self, intent_id):
                    return ({"source": "original-account"},)
            with mock_orders_contract_app(create_app, Path(directory) / 'monitor.sqlite3', True) as (
                app, _, bundles, _, publication,
            ):
                bundle = bundles["contract-mock"]
                native_gateway = bundle.gateway
                bundle.gateway = Gateway()
                try:
                    with TestClient(app) as client:
                        response = client.post("/api/v1/mock/orders",
                            headers={"Authorization": "Bearer private-token"},
                            json={"request_id": "request-1", "symbol": "005930", "side": "BUY",
                                  "quantity": 1, "limit_price": 1000})
                        self.assertEqual("disabled-profile", publication["current"])
                        self.assertIsNone(app.state.mock_credential_owner.bundle())
                finally:
                    bundle.gateway = native_gateway
            self.assertEqual(200, response.status_code)
            self.assertEqual([{"source": "original-account"}], response.json()["events"])

    def test_main_identity_start_failure_closes_mock_bundle_before_database(self) -> None:
        settings = CentralServerSettings(
            "sqlite:///:memory:", "private-token", kiwoom_app_key="real-key",
            kiwoom_secret_key="real-secret", kiwoom_environment="real",
            kiwoom_mock_app_key="mock-key", kiwoom_mock_secret_key="mock-secret",
            autonomous_top20_enabled=False, market_event_collection_enabled=False,
            mock_account_monitor_enabled=True, mock_account_ref="account-1",
            mock_execution_run_id="run-1", account_identity_registry_enabled=True,
            account_identity_hmac_key="x" * 32,
        )
        app = create_app(settings)
        with patch(
            "kiwoom_monitor.infrastructure.kiwoom_rest.account_identity."
            "KiwoomAccountIdentityReader.verify",
            new=AsyncMock(side_effect=RuntimeError("main-identity-failed")),
        ), self.assertRaisesRegex(RuntimeError, "main-identity-failed"):
            with TestClient(app): pass
        self.assertTrue(app.state.mock_account_bundle._close_task.done())

    def test_mock_account_monitor_refuses_missing_kiwoom_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
                kiwoom_environment="real", mock_account_monitor_enabled=True,
                mock_account_ref="opaque-account", mock_execution_run_id="forward-run",
            )
            with self.assertRaisesRegex(ValueError, "별도 모의투자 앱 키"):
                create_app(settings)

    def test_mock_order_transport_refuses_missing_account_monitor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
                mock_order_transport_enabled=True,
            )
            with self.assertRaisesRegex(ValueError, "모의계좌 모니터"):
                create_app(settings)

    def test_mock_account_monitor_does_not_require_real_market_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
                kiwoom_environment="real",
                kiwoom_mock_app_key="mock-key", kiwoom_mock_secret_key="mock-secret",
                autonomous_top20_enabled=False, market_event_collection_enabled=False,
                mock_account_monitor_enabled=True, mock_account_ref="opaque-account",
                mock_execution_run_id="forward-run",
            )
            app = create_app(settings)
        self.assertIsNotNone(app.state.mock_account_monitor)
        self.assertIsNotNone(app.state.mock_account_realtime)

    def test_mock_order_gateway_uses_the_mock_account_client_only_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
                kiwoom_mock_app_key="mock-key", kiwoom_mock_secret_key="mock-secret",
                autonomous_top20_enabled=False, market_event_collection_enabled=False,
                mock_account_monitor_enabled=True, mock_order_transport_enabled=True,
                mock_account_ref="opaque-account", mock_execution_run_id="forward-run",
            )
            app = create_app(settings)
        gateway = app.state.mock_order_gateway
        self.assertIsNotNone(gateway)
        account_client = app.state.mock_account_monitor._reader._broker._client
        transport_client = gateway._runtime._lifecycle._transport._client
        self.assertIs(account_client, transport_client)
        self.assertEqual("mock", transport_client.environment)

    def test_mock_order_endpoint_is_closed_when_transport_is_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory, TestClient(create_app(CentralServerSettings(
            f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
        ))) as client:
            response = client.post(
                "/api/v1/mock/orders",
                headers={"Authorization": "Bearer private-token"},
                json={
                    "request_id": "manual-1", "symbol": "005930", "side": "BUY",
                    "quantity": 1, "limit_price": 70000,
                },
            )
        self.assertEqual(503, response.status_code)

    def test_mock_order_api_submits_once_reads_and_cancels_with_fresh_account(self) -> None:
        class FakeClient:
            instances = []

            def __init__(self, settings):
                self.environment = settings.environment
                self.order_calls = []
                self.query_calls = []
                self.now = datetime(2026, 9, 14, 9, 0, 0)
                self.__class__.instances.append(self)

            def server_now(self):
                return self.now

            def get_access_token(self):
                return "mock-token"

            def request_with_continuation(self, api_id, _path, _body, *, cont_yn="N", next_key=""):
                self.query_calls.append((api_id, cont_yn, next_key))
                payload = {
                    "ka10075": {"oso": []},
                    "ka10076": {"cntr": []},
                    "kt00018": {"acnt_evlt_remn_indv_tot": []},
                    "kt00001": {"ord_alow_amt": "1000000"},
                }[api_id]
                return payload, False, ""

            def request_once(self, api_id, path, body):
                self.order_calls.append((api_id, path, body))
                return {"return_code": 0, "ord_no": "mock-order-1"}

        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
                kiwoom_mock_app_key="mock-key", kiwoom_mock_secret_key="mock-secret",
                autonomous_top20_enabled=False, market_event_collection_enabled=False,
                mock_account_monitor_enabled=True, mock_order_transport_enabled=True,
                mock_account_ref="opaque-account", mock_execution_run_id="forward-run",
            )
            headers = {"Authorization": "Bearer private-token"}
            body = {
                "request_id": "manual-api-1", "symbol": "005930", "side": "BUY",
                "quantity": 1, "limit_price": 70000, "expires_seconds": 120,
            }
            with patch(
                "kiwoom_monitor.infrastructure.kiwoom_rest.KiwoomRestClient", FakeClient,
            ), patch(
                "kiwoom_monitor.central_server.mock_account_monitor."
                "MockAccountRealtimeCollector.start", new=AsyncMock(),
            ), TestClient(create_app(settings)) as client:
                first = client.post("/api/v1/mock/orders", headers=headers, json=body)
                duplicate = client.post("/api/v1/mock/orders", headers=headers, json=body)
                intent_id = first.json()["intent_id"]
                loaded = client.get(f"/api/v1/mock/orders/{intent_id}", headers=headers)
                FakeClient.instances[0].now = datetime(2026, 9, 14, 16, 0, 0)
                after_probe = client.post(
                    "/api/v1/mock/orders", headers=headers,
                    json={**body, "request_id": "manual-api-after-1"},
                )
                cancelled = client.post(
                    f"/api/v1/mock/orders/{intent_id}/cancel", headers=headers, json={"quantity": 0},
                )

        self.assertEqual(200, first.status_code)
        self.assertEqual("ACCEPTED", first.json()["state"])
        self.assertEqual(intent_id, duplicate.json()["intent_id"])
        self.assertEqual("ACCEPTED", loaded.json()["state"])
        self.assertEqual("ACCEPTED", after_probe.json()["state"])
        self.assertEqual("ORDER_ACCEPTED", after_probe.json()["events"][-1]["event_type"])
        self.assertIn(
            "manual-mock-krx-after-limit-probe/v1",
            after_probe.json()["policy_version"],
        )
        self.assertEqual("CANCEL_PENDING", cancelled.json()["state"])
        self.assertEqual(3, len(FakeClient.instances[0].order_calls))
        self.assertEqual(
            ["kt10000", "kt10000", "kt10003"],
            [call[0] for call in FakeClient.instances[0].order_calls],
        )

    def test_market_feed_returns_only_requested_stored_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            today = datetime.now().date().isoformat()
            path = Path(directory) / "monitor.sqlite3"
            store = SQLiteQueryStore(path)
            store.initialize()
            store.save_news_source_page({
                "source_id": f"naver-stock:flash:{today}", "query_text": "flash",
                "run_id": "flash-run", "items": [{
                    "identity": "https://example.com/flash-1",
                    "document": {"title": "속보", "description": "저장된 요약",
                                 "link": "https://example.com/flash-1",
                                 "published_at": f"{today}T10:00:00+09:00"},
                    "targets": [],
                }],
            })
            store.close()
            settings = CentralServerSettings(f"sqlite:///{path}", "private-token",
                                             news_query_set_enabled=False)
            with TestClient(create_app(settings)) as client:
                headers = {"Authorization": "Bearer private-token"}
                flash = client.get("/api/v1/news/market-feed?source=flash", headers=headers)
                world = client.get("/api/v1/news/market-feed?source=world", headers=headers)
                denied = client.get("/api/v1/news/market-feed?source=flash")
            self.assertEqual(200, flash.status_code)
            self.assertEqual("저장된 요약", flash.json()["items"][0]["description"])
            self.assertEqual([], world.json()["items"])
            self.assertEqual(401, denied.status_code)

    def test_public_api_route_contract_is_stable(self) -> None:
        """Protect the paths and methods used by released desktop clients."""
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
            )
            app = create_app(settings)
        # OpenAPI enumerates effective HTTP routes, including nested APIRouters.
        actual = {
            (method.upper(), path)
            for path, operations in app.openapi()["paths"].items()
            for method in operations
            if method in {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
            and (path == "/health" or path.startswith("/api/v1/"))
        }
        def registered_routes(routes):
            # FastAPI keeps included routers nested; enumerate their effective WS routes.
            for route in routes:
                included = getattr(route, "original_router", None)
                if included is not None:
                    yield from registered_routes(included.routes)
                else:
                    yield route

        actual.update(
            ("WEBSOCKET", route.path)
            for route in registered_routes(app.routes)
            if getattr(route, "path", "").startswith("/api/v1/")
            and getattr(route, "methods", None) is None
        )
        expected = {
            ("GET", "/health"),
            ("GET", "/api/v1/capabilities"),
            ("POST", "/api/v1/mock/orders"),
            ("GET", "/api/v1/mock/orders/{intent_id}"),
            ("POST", "/api/v1/mock/orders/{intent_id}/cancel"),
            ("GET", "/api/v1/settings/operations"),
            ("GET", "/api/v1/settings/market-profile"),
            ("PUT", "/api/v1/settings/market-profile"),
            ("PUT", "/api/v1/settings/operations"),
            ("GET", "/api/v1/settings/accounts/{account_ref}"),
            ("GET", "/api/v1/settings/credentials"),
            ("POST", "/api/v1/settings/credentials/{provider}/profiles"),
            ("PUT", "/api/v1/settings/credentials/{provider}/profiles/{profile_id}"),
            ("DELETE", "/api/v1/settings/credentials/{provider}/profiles/{profile_id}"),
            ("POST", "/api/v1/settings/credentials/{provider}/profiles/{profile_id}/prepare"),
            ("GET", "/api/v1/settings/credential-operations/{operation_id}"),
            ("POST", "/api/v1/settings/credential-operations/{operation_id}/apply"),
            ("DELETE", "/api/v1/settings/credential-operations/{operation_id}"),
            ("GET", "/api/v1/diagnostics/resources"),
            ("GET", "/api/v1/diagnostics/workloads"),
            ("GET", "/api/v1/diagnostics/market-bar-saves"),
            ("GET", "/api/v1/diagnostics/writers"),
            ("GET", "/api/v1/diagnostics/db-calls"),
            ("GET", "/api/v1/diagnostics/news-job-claim-plan"),
            ("GET", "/api/v1/diagnostics/news-job-claim-readonly-analyze"),
            ("POST", "/api/v1/diagnostics/trace"),
            ("GET", "/api/v1/diagnostics/trace"),
            ("POST", "/api/v1/diagnostics/trace/stop"),
            ("GET", "/api/v1/diagnostics/trace/{trace_id}"),
            ("GET", "/api/v1/diagnostics/trace/{trace_id}/chunks/{chunk_name}"),
            ("GET", "/api/v1/diagnostics/capabilities"),
            ("PUT", "/api/v1/diagnostics/control"),
            ("GET", "/api/v1/diagnostics/snapshot"),
            ("POST", "/api/v1/diagnostics/runs"),
            ("GET", "/api/v1/diagnostics/runs/{run_id}"),
            ("POST", "/api/v1/diagnostics/runs/{run_id}/cancel"),
            ("GET", "/api/v1/diagnostics/reports"),
            ("GET", "/api/v1/diagnostics/reports/{report_id}"),
            ("GET", "/api/v1/diagnostics/history"),
            ("POST", "/api/v1/kiwoom/query"),
            ("POST", "/api/v1/news/search"),
            ("POST", "/api/v1/news/stored-page"),
            ("GET", "/api/v1/news/historical-archive/search"),
            ("GET", "/api/v1/news/historical-archive/articles/{article_revision_id}"),
            ("POST", "/api/v1/news/analyze"),
            ("POST", "/api/v1/news/historical-jobs/claim"),
            ("POST", "/api/v1/news/historical-jobs/complete"),
            ("POST", "/api/v1/news/historical-market-articles"),
            ("GET", "/api/v1/news/history/{kind}"),
            ("GET", "/api/v1/news/sources"),
            ("GET", "/api/v1/news/market-feed"),
            ("GET", "/api/v1/market/minute-bars"),
            ("GET", "/api/v1/market/recent-minute-bars"),
            ("GET", "/api/v1/market/latest-market-caps"),
            ("GET", "/api/v1/market/trade-value-comparisons"),
            ("GET", "/api/v1/market/events"),
            ("GET", "/api/v1/market/daily-bars"),
            ("GET", "/api/v1/market/coverage"),
            ("GET", "/api/v1/market/external-bars"),
            ("GET", "/api/v1/research/observations"),
            ("GET", "/api/v1/research/candidates"),
            ("POST", "/api/v1/research/mock-automation-candidates"),
            ("GET", "/api/v1/research/mock-automation-candidates/{account_ref}"),
            ("POST", "/api/v1/research/mock-automation-specs"),
            ("GET", "/api/v1/research/mock-automation-specs/{account_ref}"),
            ("GET", "/api/v1/mock-automation/accounts/{account_ref}"),
            ("POST", "/api/v1/mock-automation/start"),
            ("POST", "/api/v1/mock-automation/stop"),
            ("POST", "/api/v1/mock-automation/resume"),
            ("GET", "/api/v1/market/top20-statistics"),
            ("GET", "/api/v1/market/snapshots/{kind}"),
            ("GET", "/api/v1/content/{collection}"),
            ("POST", "/api/v1/content/{collection}"),
            ("PUT", "/api/v1/content/{collection}"),
            ("GET", "/api/v1/themes/history"),
            ("WEBSOCKET", "/api/v1/realtime"),
        }
        self.assertEqual(expected, actual)

    def test_research_observation_export_is_authenticated_and_page_stable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            store = SQLiteQueryStore(path)
            store.initialize()
            for second, code in ((0, "005930"), (30, "000660")):
                key = f"2026-09-12T09:00:{second:02d}"
                payload = {"query_type": "5", "items": [{"stk_cd": code}]}
                store.save_dataset_snapshot(
                    "ranking", "5", key, payload,
                    observation=ranking_observation(
                        "5", key, payload,
                        datetime(2026, 9, 12, 0, 0, second, tzinfo=timezone.utc),
                        source="fixture",
                    ),
                )
            store.close()

            settings = CentralServerSettings(f"sqlite:///{path}", "private-token")
            parameters = {
                "start": "2026-09-12T00:00:00+00:00",
                "end": "2026-09-13T00:00:00+00:00",
                "kinds": "ranking", "subject": "5", "limit": 1,
            }
            headers = {"Authorization": "Bearer private-token"}
            with TestClient(create_app(settings)) as client:
                self.assertEqual(401, client.get(
                    "/api/v1/research/observations", params=parameters,
                ).status_code)
                first = client.get(
                    "/api/v1/research/observations", params=parameters, headers=headers,
                )
                self.assertEqual(200, first.status_code)
                first_document = first.json()
                second = client.get(
                    "/api/v1/research/observations",
                    params={**parameters, "watermark": first_document["watermark"],
                            "cursor": first_document["next_cursor"]},
                    headers=headers,
                )
                mismatch = client.get(
                    "/api/v1/research/observations",
                    params={**parameters, "subject": "1", "watermark": first_document["watermark"]},
                    headers=headers,
                )

        self.assertEqual(2, first_document["manifest"]["revision_count"])
        self.assertEqual(1, len(first_document["observations"]))
        self.assertEqual(1, len(second.json()["observations"]))
        self.assertIsNone(second.json()["next_cursor"])
        self.assertEqual(409, mismatch.status_code)

    def test_shadow_candidate_api_is_authenticated_and_reports_disabled_generation(self) -> None:
        with tempfile.TemporaryDirectory() as directory, TestClient(create_app(CentralServerSettings(
            f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
        ))) as client:
            unauthorized = client.get("/api/v1/research/candidates")
            response = client.get(
                "/api/v1/research/candidates?after_sequence=0&limit=10",
                headers={"Authorization": "Bearer private-token"},
            )

        self.assertEqual(401, unauthorized.status_code)
        self.assertEqual(200, response.status_code)
        self.assertEqual([], response.json()["events"])
        self.assertEqual("DISABLED", response.json()["quality"]["status"])

    def test_operational_settings_can_be_changed_and_are_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
                ai_provider="gemini", ai_model="old-model", ai_daily_limit=500,
            )
            headers = {"Authorization": "Bearer private-token"}
            body = {
                "ai_provider": "gemini", "ai_model": "new-model", "ai_daily_limit": 321,
                "news_refresh_seconds": 600, "dart_enabled": True,
                "news_query_set_enabled": False, "news_query_set": ["증권", "코스피"],
                "news_query_set_refresh_seconds": 900,
                "news_processing_excluded_providers": ["thebell.co.kr", "연합인포맥스"],
            }
            with TestClient(create_app(settings)) as client:
                self.assertEqual(401, client.get("/api/v1/settings/operations").status_code)
                self.assertEqual(200, client.put("/api/v1/settings/operations", headers=headers, json=body).status_code)
            with TestClient(create_app(settings)) as client:
                loaded = client.get("/api/v1/settings/operations", headers=headers).json()
            for key, value in body.items():
                self.assertEqual(value, loaded[key])
            self.assertFalse(loaded["shadow_candidate_enabled"])
            self.assertEqual(
                default_shadow_breakout_config().to_dict(),
                loaded["shadow_candidate_config"],
            )

    def test_operational_settings_conflict_and_failed_write_preserve_latest_state(self):
        from unittest.mock import patch
        from kiwoom_monitor.central_server.database import SQLiteQueryStore

        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "monitor.sqlite3"
            settings = CentralServerSettings(
                f"sqlite:///{database_path}", "private-token",
            )
            headers = {"Authorization": "Bearer private-token"}
            store = SQLiteQueryStore(database_path)
            with patch("kiwoom_monitor.central_server.app.create_query_store", return_value=store):
                with TestClient(create_app(settings)) as client:
                    original = client.get("/api/v1/settings/operations", headers=headers).json()
                    saved = client.put("/api/v1/settings/operations", headers=headers, json={
                        "expected_revision": original["revision"], "ai_daily_limit": 123,
                    })
                    self.assertEqual(200, saved.status_code)
                    self.assertEqual(original["revision"] + 1, saved.json()["revision"])
                    stale = client.put("/api/v1/settings/operations", headers=headers, json={
                        "expected_revision": original["revision"], "dart_enabled": True,
                    })
                    self.assertEqual(409, stale.status_code)
                    with patch.object(store, "upsert_documents", side_effect=OSError("write failed")) as failed_write:
                        failed = client.put("/api/v1/settings/operations", headers=headers, json={
                            "ai_daily_limit": 321,
                        })
                    failed_write.assert_called_once()
                    self.assertEqual(503, failed.status_code)
                    latest = client.get("/api/v1/settings/operations", headers=headers).json()
                    self.assertEqual(saved.json(), latest)
                    self.assertEqual(latest["revision"], latest["applied_revision"])
            with TestClient(create_app(settings)) as client:
                self.assertEqual(latest, client.get("/api/v1/settings/operations", headers=headers).json())

    def test_operational_apply_failure_can_retry_persisted_revision(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
            )
            headers = {"Authorization": "Bearer private-token"}
            with TestClient(create_app(settings)) as client:
                with patch("kiwoom_monitor.central_server.app.CandidateMonitor.start",
                           side_effect=RuntimeError("runtime failure")):
                    failed = client.put("/api/v1/settings/operations", headers=headers, json={
                        "shadow_candidate_enabled": True,
                    })
                self.assertEqual(503, failed.status_code)
                pending = client.get("/api/v1/settings/operations", headers=headers).json()
                self.assertEqual("RECOVERY_REQUIRED", pending["apply_status"])
                retried = client.put("/api/v1/settings/operations", headers=headers, json={
                    "expected_revision": pending["revision"],
                })
                self.assertEqual(200, retried.status_code)
                self.assertEqual(pending["revision"], retried.json()["revision"])
                self.assertEqual("ACTIVE", retried.json()["apply_status"])

    def test_shadow_candidate_can_start_and_stop_without_server_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
            )
            headers = {"Authorization": "Bearer private-token"}
            with TestClient(create_app(settings)) as client:
                enabled = client.put("/api/v1/settings/operations", headers=headers, json={
                    "shadow_candidate_enabled": True,
                    "shadow_candidate_config": default_shadow_breakout_config().to_dict(),
                    "shadow_candidate_poll_seconds": 2,
                    "shadow_candidate_universe_max_age_seconds": 90,
                })
                active = client.get("/api/v1/research/candidates", headers=headers)
                monitor_before_news_change = client.app.state.candidate_monitor
                news_only = client.put(
                    "/api/v1/settings/operations", headers=headers,
                    json={"news_refresh_seconds": 601},
                )
                monitor_after_news_change = client.app.state.candidate_monitor
                invalid = client.put("/api/v1/settings/operations", headers=headers, json={
                    "shadow_candidate_config": {"strategy_version": "v999"},
                })
                active_after_invalid = client.get(
                    "/api/v1/research/candidates", headers=headers,
                )
                disabled = client.put("/api/v1/settings/operations", headers=headers, json={
                    "shadow_candidate_enabled": False,
                })
                inactive = client.get("/api/v1/research/candidates", headers=headers)

            self.assertEqual(200, enabled.status_code)
            self.assertTrue(enabled.json()["shadow_candidate_enabled"])
            self.assertNotEqual("DISABLED", active.json()["quality"]["status"])
            self.assertEqual(200, news_only.status_code)
            self.assertIs(monitor_before_news_change, monitor_after_news_change)
            self.assertEqual(422, invalid.status_code)
            self.assertNotEqual("DISABLED", active_after_invalid.json()["quality"]["status"])
            self.assertEqual(200, disabled.status_code)
            self.assertFalse(disabled.json()["shadow_candidate_enabled"])
            self.assertEqual("DISABLED", inactive.json()["quality"]["status"])

    def test_health_is_public_and_capabilities_require_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
            )
            with TestClient(create_app(settings)) as client:
                self.assertEqual(200, client.get("/health").status_code)
                self.assertEqual(401, client.get("/api/v1/capabilities").status_code)
                response = client.get(
                    "/api/v1/capabilities", headers={"Authorization": "Bearer private-token"},
                )
        self.assertEqual(200, response.status_code)
        self.assertFalse(response.json()["capabilities"]["kiwoom_rest"])
        self.assertTrue(response.json()["capabilities"]["news_archive"])
        self.assertTrue(response.json()["capabilities"]["ai_analysis_archive"])
        self.assertTrue(response.json()["capabilities"]["themes"])
        self.assertTrue(response.json()["capabilities"]["trade_journal"])
        self.assertTrue(response.json()["capabilities"]["shared_settings"])
        self.assertTrue(response.json()["capabilities"]["journal_v2_sync"])
        self.assertTrue(response.json()["capabilities"]["journal_news_links_v2"])
        self.assertTrue(response.json()["capabilities"]["combined_minute_bars"])
        self.assertTrue(response.json()["capabilities"]["trade_value_comparisons"])
        self.assertTrue(response.json()["capabilities"]["mock_automation_candidate_publish_v1"])
        self.assertTrue(response.json()["capabilities"]["mock_automation_candidate_read_v1"])
        self.assertTrue(response.json()["capabilities"]["mock_automation_spec_publish_v1"])
        self.assertFalse(response.json()["capabilities"]["mock_automation_runtime_v1"])

    def test_query_reports_unconfigured_kiwoom_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
            )
            with TestClient(create_app(settings)) as client:
                response = client.post(
                    "/api/v1/kiwoom/query",
                    headers={"Authorization": "Bearer private-token"},
                    json={"api_id": "ka10001", "path": "/api/dostk/stkinfo", "body": {"stk_cd": "005930"}},
                )
        self.assertEqual(503, response.status_code)

    def test_resource_diagnostics_are_authenticated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
            )
            with TestClient(create_app(settings)) as client:
                self.assertEqual(401, client.get("/api/v1/diagnostics/resources").status_code)
                response = client.get(
                    "/api/v1/diagnostics/resources",
                    headers={"Authorization": "Bearer private-token"},
                )
        self.assertEqual(200, response.status_code)
        self.assertIn("process_memory_bytes", response.json())
        self.assertGreater(response.json()["database_size_bytes"], 0)
        self.assertEqual(
            ["news", "market", "research", "account", "other"],
            [item["category"] for item in response.json()["storage_categories"]],
        )
        self.assertFalse(response.json()["retention_policy"]["automatic_deletion_enabled"])

    def test_ai_endpoint_reports_unconfigured_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
            )
            with TestClient(create_app(settings)) as client:
                response = client.post(
                    "/api/v1/news/analyze", headers={"Authorization": "Bearer private-token"},
                    json={"stock_code": "005930", "stock_name": "삼성전자", "events": [{
                        "identity": "a", "title": "제목", "body": "본문", "body_hash": "h",
                    }]},
                )
        self.assertEqual(503, response.status_code)

    def test_ai_endpoint_preserves_provider_rate_limit_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
                gemini_api_key="key", ai_provider="gemini", ai_model="model",
            )
            with patch(
                "kiwoom_monitor.central_server.ai_service.CentralAIService.analyze",
                new=AsyncMock(side_effect=NewsAIProviderError(429)),
            ), TestClient(create_app(settings)) as client:
                response = client.post(
                    "/api/v1/news/analyze", headers={"Authorization": "Bearer private-token"},
                    json={"stock_code": "005930", "stock_name": "삼성전자", "events": [{
                        "identity": "a", "title": "제목", "body": "본문", "body_hash": "h",
                    }]},
                )
        self.assertEqual(429, response.status_code)
        self.assertIn("호출 한도", response.json()["detail"])

    def test_minute_bars_endpoint_is_authenticated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
            )
            with TestClient(create_app(settings)) as client:
                path = "/api/v1/market/minute-bars?code=005930&trading_date=2026-09-08"
                self.assertEqual(401, client.get(path).status_code)
                response = client.get(path, headers={"Authorization": "Bearer private-token"})
        self.assertEqual(200, response.status_code)
        self.assertEqual([], response.json()["bars"])

    def test_market_event_diagnostics_are_authenticated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
            )
            with TestClient(create_app(settings)) as client:
                self.assertEqual(401, client.get("/api/v1/market/events?kind=cohort").status_code)
                response = client.get(
                    "/api/v1/market/events?kind=cohort",
                    headers={"Authorization": "Bearer private-token"},
                )
        self.assertEqual(200, response.status_code)
        self.assertEqual("NOT_OBSERVED", response.json()["condition"]["status"])

    def test_daily_bars_endpoint_is_authenticated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
            )
            with TestClient(create_app(settings)) as client:
                response = client.get(
                    "/api/v1/market/daily-bars?code=005930&limit=250",
                    headers={"Authorization": "Bearer private-token"},
                )
        self.assertEqual(200, response.status_code)
        self.assertEqual([], response.json()["bars"])

    def test_daily_bars_coverage_is_read_only_and_validates_returned_prefixes(self):
        from kiwoom_monitor.application.daily_bar_coverage import COLLECTION
        from tests.unit.test_daily_bar_coverage import window_with
        today = datetime.now(KST).date()
        window = window_with(20, end=today)
        evidence = window.evidence(code="005930", market="KRX", scope="initial", checked_at="now")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "central.sqlite3"
            store = SQLiteQueryStore(path)
            store.initialize()
            store.replace_daily_bars([{**row, "code": "005930", "market": "KRX", "updated_at": 1} for row in window.rows])
            store.upsert_documents(COLLECTION, [{"owner": "005930:KRX", "key": "initial", "document": evidence}])
            with TestClient(create_app(CentralServerSettings(f"sqlite:///{path}", "private-token",
                                                           autonomous_top20_enabled=False))) as client:
                headers = {"Authorization": "Bearer private-token"}
                before = store.load_documents(COLLECTION, "005930:KRX", 2)
                response = client.get("/api/v1/market/daily-bars?code=005930&market=KRX&limit=250", headers=headers).json()
                self.assertTrue(response["coverage"]["collection_verified"])
                self.assertEqual("source_exhausted_short_history", response["coverage"]["periods"]["250"]["reason"])
                short = client.get("/api/v1/market/daily-bars?code=005930&market=KRX&limit=5", headers=headers).json()
                self.assertEqual("unverified", short["coverage"]["periods"]["20"]["status"])
                self.assertEqual(before, store.load_documents(COLLECTION, "005930:KRX", 2))
            store.close()

    def test_market_coverage_requires_authentication_and_stays_conservative(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
                autonomous_top20_enabled=False,
            )
            path = (
                "/api/v1/market/coverage?kind=minute_bar&subject=005930%3AKRX"
                "&start=2026-09-10T09%3A00%3A00%2B09%3A00"
                "&end=2026-09-10T15%3A30%3A00%2B09%3A00"
            )
            with TestClient(create_app(settings)) as client:
                self.assertEqual(401, client.get(path).status_code)
                response = client.get(
                    path, headers={"Authorization": "Bearer private-token"},
                )
        self.assertEqual(200, response.status_code)
        self.assertEqual("missing", response.json()["state"])
        self.assertEqual("no_trade_or_no_observation", response.json()["absence_meaning"])

    def test_market_coverage_tags_metadata_read_source_across_thread_boundary(self) -> None:
        from kiwoom_monitor.central_server.postgres_access import DBWriterContext

        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "monitor.sqlite3"
            store = SQLiteQueryStore(database_path)
            original_load = store.load_market_data_metadata_range
            observed_sources: list[str] = []

            def capture_source(*args: object, **kwargs: object):
                observed_sources.append(DBWriterContext(
                    "read.market_data_metadata", "metadata_range",
                    "load_market_data_metadata_range", access_mode="read",
                ).source)
                return original_load(*args, **kwargs)

            settings = CentralServerSettings(
                f"sqlite:///{database_path}", "private-token",
                autonomous_top20_enabled=False,
            )
            with patch(
                "kiwoom_monitor.central_server.app.create_query_store",
                return_value=store,
            ), patch.object(
                store, "load_market_data_metadata_range", side_effect=capture_source,
            ), TestClient(create_app(settings)) as client:
                response = client.get(
                    "/api/v1/market/coverage?kind=minute_bar&subject=005930%3AKRX"
                    "&start=2026-09-10T00%3A00%3A00%2B09%3A00"
                    "&end=2026-09-11T00%3A00%3A00%2B09%3A00",
                    headers={"Authorization": "Bearer private-token"},
                )

        self.assertEqual(200, response.status_code)
        self.assertEqual(["api.market.coverage"], observed_sources)

    def test_market_coverage_uses_explicit_completed_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "monitor.sqlite3"
            store = SQLiteQueryStore(database_path)
            store.initialize()
            store.upsert_documents("market_data_coverage", [{
                "owner": "2026-09-10:005930:KRX", "key": "complete",
                "document": {"kind": "minute"},
            }])
            store.close()
            settings = CentralServerSettings(
                f"sqlite:///{database_path}", "private-token",
                autonomous_top20_enabled=False,
            )
            with TestClient(create_app(settings)) as client:
                response = client.get(
                    "/api/v1/market/coverage?kind=minute_bar&subject=005930%3AKRX"
                    "&start=2026-09-10T00%3A00%3A00%2B09%3A00"
                    "&end=2026-09-11T00%3A00%3A00%2B09%3A00",
                    headers={"Authorization": "Bearer private-token"},
                )
        self.assertEqual(200, response.status_code)
        self.assertEqual("complete", response.json()["state"])
        self.assertTrue(response.json()["explicit_complete"])
        self.assertEqual(
            "no_trade_with_complete_coverage", response.json()["absence_meaning"],
        )

    def test_market_coverage_does_not_use_completion_saved_after_cutoff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "monitor.sqlite3"
            store = SQLiteQueryStore(database_path)
            store.initialize()
            store.upsert_documents("market_data_coverage", [{
                "owner": "2026-09-10:005930:KRX", "key": "complete",
                "document": {"kind": "minute"},
            }])
            store.close()
            settings = CentralServerSettings(
                f"sqlite:///{database_path}", "private-token",
                autonomous_top20_enabled=False,
            )
            with TestClient(create_app(settings)) as client:
                response = client.get(
                    "/api/v1/market/coverage?kind=minute_bar&subject=005930%3AKRX"
                    "&start=2026-09-10T09%3A00%3A00%2B09%3A00"
                    "&end=2026-09-10T15%3A30%3A00%2B09%3A00"
                    "&available_by=2026-09-10T15%3A30%3A00%2B09%3A00",
                    headers={"Authorization": "Bearer private-token"},
                )
        self.assertEqual(200, response.status_code)
        self.assertEqual("missing", response.json()["state"])
        self.assertFalse(response.json()["explicit_complete"])

    def test_market_coverage_rejects_inferred_bar_cadence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
                autonomous_top20_enabled=False,
            )
            with TestClient(create_app(settings)) as client:
                response = client.get(
                    "/api/v1/market/coverage?kind=minute_bar&subject=005930%3AKRX"
                    "&start=2026-09-10T09%3A00%3A00%2B09%3A00"
                    "&end=2026-09-10T09%3A01%3A00%2B09%3A00&expected_seconds=60",
                    headers={"Authorization": "Bearer private-token"},
                )
        self.assertEqual(400, response.status_code)

    def test_completed_minute_archive_is_reused_as_kiwoom_shape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            store.replace_minute_bars([{
                "trading_date": "2026-09-10", "minute": "09:01", "code": "005930", "market": "KRX",
                "open": 70000, "high": 70100, "low": 69900, "close": 70050, "volume": 10,
                "trade_value_million_won": 1, "updated_at": 1.0,
            }])
            store.upsert_documents("market_data_coverage", [{
                "owner": "2026-09-10:005930:KRX", "key": "complete", "document": {
                    "kind": "minute", "as_of": "2026-09-10",
                    "window_closed": True, "session_finalized": True,
                },
            }])
            result = _archived_chart_response(
                store, "ka10080", {"stk_cd": "005930", "base_dt": "20260910"},
            )
            store.close()
        self.assertEqual("20260910090100", result["stk_min_pole_chart_qry"][0]["cntr_tm"])

    def test_incomplete_archive_does_not_bypass_kiwoom(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            result = _archived_chart_response(
                store, "ka10080", {"stk_cd": "005930", "base_dt": "20260910"},
            )
            store.close()
        self.assertIsNone(result)

    def test_existing_bars_with_unfinalized_coverage_do_not_bypass_kiwoom(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            store.replace_minute_bars([{
                "trading_date": "2026-09-10", "minute": "09:01", "code": "005930", "market": "KRX",
                "open": 70000, "high": 70100, "low": 69900, "close": 70050, "volume": 10,
                "trade_value_million_won": 1, "updated_at": 1.0,
            }])
            store.upsert_documents("market_data_coverage", [{
                "owner": "2026-09-10:005930:KRX", "key": "complete",
                "document": {"kind": "minute", "as_of": "2026-09-10",
                             "window_closed": False, "session_finalized": False},
            }])
            result = _archived_chart_response(
                store, "ka10080", {"stk_cd": "005930", "base_dt": "20260910"},
            )
            store.close()
        self.assertIsNone(result)

    def test_empty_completed_minute_archive_does_not_bypass_kiwoom(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            store.upsert_documents("market_data_coverage", [{
                "owner": "2026-09-10:005930:KRX", "key": "complete",
                "document": {"kind": "minute", "rows": 0, "as_of": "2026-09-10"},
            }])
            result = _archived_chart_response(
                store, "ka10080", {"stk_cd": "005930", "base_dt": "20260910"},
            )
            store.close()
        self.assertIsNone(result)

    def test_empty_completed_daily_archive_does_not_bypass_kiwoom(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            store.upsert_documents("market_data_coverage_daily", [{
                "owner": "000660:NXT", "key": "complete",
                "document": {"kind": "daily", "rows": 0, "as_of": "2026-09-09"},
            }])

            result = _archived_chart_response(
                store, "ka10081", {"stk_cd": "000660_NX", "base_dt": "20260909"},
            )
            store.close()
        self.assertIsNone(result)

    def test_daily_archive_excludes_bars_after_requested_date(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            store.replace_daily_bars([{
                "trading_date": day, "code": "005930", "market": "KRX",
                "open": 70000, "high": 71000, "low": 69000, "close": close,
                "volume": 10, "trade_value_million_won": 1, "updated_at": 1.0,
            } for day, close in (("2026-09-10", 70500), ("2026-09-09", 69500))])
            store.upsert_documents("market_data_coverage_daily", [{
                "owner": "005930:KRX", "key": "complete",
                "document": {"kind": "daily", "rows": 2, "as_of": "2026-09-10",
                             "window_closed": True, "session_finalized": True},
            }])
            result = _archived_chart_response(
                store, "ka10081", {"stk_cd": "005930", "base_dt": "20260909"},
            )
            store.close()

        self.assertEqual(1, len(result["stk_dt_pole_chart_qry"]))
        self.assertEqual("20260909", result["stk_dt_pole_chart_qry"][0]["date"])

    def test_daily_archive_newer_than_coverage_does_not_bypass_kiwoom(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            store.replace_daily_bars([{
                "trading_date": "2026-09-10", "code": "005930", "market": "KRX",
                "open": 70000, "high": 71000, "low": 69000, "close": 70500,
                "volume": 10, "trade_value_million_won": 1, "updated_at": 1.0,
            }])
            store.upsert_documents("market_data_coverage_daily", [{
                "owner": "005930:KRX", "key": "complete",
                "document": {"kind": "daily", "rows": 1, "as_of": "2026-09-10"},
            }])
            result = _archived_chart_response(
                store, "ka10081", {"stk_cd": "005930", "base_dt": "20260911"},
            )
            store.close()
        self.assertIsNone(result)

    def test_market_snapshot_endpoint_rejects_unknown_kind(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
            )
            with TestClient(create_app(settings)) as client:
                response = client.get(
                    "/api/v1/market/snapshots/unknown",
                    headers={"Authorization": "Bearer private-token"},
                )
        self.assertEqual(404, response.status_code)

    def test_top20_statistics_endpoint_returns_nas_aggregate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            settings = CentralServerSettings(f"sqlite:///{path}", "private-token")
            store = SQLiteQueryStore(path)
            store.initialize()
            store.save_dataset_snapshot("top20_index", "2026-09-08", "2026-09-08T15:30", {
                "minute": "2026-09-08T15:30", "market_values": [3.0, 2.0, 0.0],
                "capture_state": "realtime_complete",
            })
            store.save_dataset_snapshot("market_index_chart", "20260908:kospi", "20260908", {
                "daily": [{"dt": "20260908", "trde_prica": "26187833"}],
            })
            store.close()

            with TestClient(create_app(settings)) as client:
                response = client.get(
                    "/api/v1/market/top20-statistics?start_date=2026-09-08&end_date=2026-09-08",
                    headers={"Authorization": "Bearer private-token"},
                )

        self.assertEqual(200, response.status_code)
        self.assertEqual(5.0, response.json()["comparisons"][0]["top20_eok"])
        self.assertEqual(261878.33, response.json()["comparisons"][0]["kospi_eok"])

    def test_stored_fundamentals_and_nxt_documents_bypass_broker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
            )
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            store.upsert_documents("stock_fundamentals", [{
                "owner": "005930", "key": "latest",
                "document": {
                    "observed_at": datetime.now(KST).isoformat(),
                    "payload": {"mac": "1000", "dstr_rt": "40"},
                },
            }])
            store.upsert_documents("stock_nxt_eligibility", [{
                "owner": "005930", "key": "latest",
                "document": {
                    "observed_at": datetime.now(KST).isoformat(),
                    "payload": {"nxtEnable": "Y"},
                },
            }])
            store.close()
            headers = {"Authorization": "Bearer private-token"}
            with TestClient(create_app(settings)) as client:
                fundamentals = client.post("/api/v1/kiwoom/query", headers=headers, json={
                    "api_id": "ka10001", "path": "/api/dostk/stkinfo",
                    "body": {"stk_cd": "005930"},
                })
                nxt = client.post("/api/v1/kiwoom/query", headers=headers, json={
                    "api_id": "ka10100", "path": "/api/dostk/stkinfo",
                    "body": {"stk_cd": "005930"},
                })

        self.assertEqual(200, fundamentals.status_code)
        self.assertEqual("1000", fundamentals.json()["payload"]["mac"])
        self.assertTrue(fundamentals.json()["archive_hit"])
        self.assertEqual("Y", nxt.json()["payload"]["nxtEnable"])

    def test_stale_fundamentals_document_is_not_reused_as_a_fresh_query(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            store.upsert_documents("stock_fundamentals", [{
                "owner": "005930", "key": "latest",
                "document": {
                    "observed_at": "2020-01-01T09:00:00+09:00",
                    "payload": {"mac": "1000"},
                },
            }])
            result = _stored_market_response(store, "ka10001", {"stk_cd": "005930"})
            store.close()

        self.assertIsNone(result)

    def test_basic_archive_refresh_at_0700_does_not_invalidate_nxt_archive(self) -> None:
        store = MagicMock()
        store.load_documents.return_value = [{"document": {
            "observed_at": "2026-10-01T06:59:59+09:00", "payload": {"mac": "1000"},
        }}]
        clock = MagicMock()
        clock.now.return_value = datetime.fromisoformat("2026-10-01T07:00:00+09:00")
        with patch.dict(_stored_market_response.__globals__, {"datetime": clock}):
            self.assertIsNone(_stored_market_response(store, "ka10001", {"stk_cd": "005930"}))
            self.assertIsNotNone(_stored_market_response(store, "ka10100", {"stk_cd": "005930"}))

    def test_content_api_upserts_and_loads_news(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
            )
            headers = {"Authorization": "Bearer private-token"}
            with TestClient(create_app(settings)) as client:
                saved = client.post(
                    "/api/v1/content/news_article", headers=headers,
                    json={"documents": [{"owner": "005930", "key": "article-1", "document": {"title": "뉴스"}}]},
                )
                loaded = client.get(
                    "/api/v1/content/news_article?owner=005930", headers=headers,
                )
        self.assertEqual(1, saved.json()["saved"])
        self.assertEqual("뉴스", loaded.json()["documents"][0]["document"]["title"])

    def test_news_history_api_is_authenticated_and_reads_revisions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
                news_history_jobs_enabled=False,
            )
            headers = {"Authorization": "Bearer private-token"}
            with TestClient(create_app(settings)) as client:
                self.assertEqual(401, client.get("/api/v1/news/history/article").status_code)
                client.post(
                    "/api/v1/content/news_article", headers=headers,
                    json={"documents": [{"owner": "005930", "key": "article-1",
                                         "document": {"title": "뉴스"},
                                         "collector_id": "local-test"}]},
                ).raise_for_status()
                result = client.get(
                    "/api/v1/news/history/article?target=005930&identity=article-1",
                    headers=headers,
                ).json()

        self.assertTrue(result["known"])
        self.assertEqual("local-test", result["revisions"][0]["collector_id"])
        self.assertEqual("뉴스", result["revisions"][0]["document"]["title"])

    def test_content_api_accepts_shared_columns_and_journal_news_links(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
            )
            headers = {"Authorization": "Bearer private-token"}
            with TestClient(create_app(settings)) as client:
                columns = client.post(
                    "/api/v1/content/app_column_settings", headers=headers,
                    json={"documents": [{"owner": "main_table", "key": "stock", "document": {"visible": True, "position": 1}}]},
                )
                links = client.post(
                    "/api/v1/content/journal_news_link", headers=headers,
                    json={"documents": [{"owner": "group-1", "key": "005930|article-1", "document": {
                        "group_id": "group-1", "stock_code": "005930", "identity": "article-1",
                    }}]},
                )
                scope = "11111111-1111-4111-8111-111111111111"
                scoped_document = {
                    "group_id": "group-1", "stock_code": "005930", "identity": "article-1",
                    "origin_broker": "kiwoom", "origin_environment": "real",
                    "origin_account_ref": scope, "canonical_account_ref": scope,
                }
                scoped_links = client.post(
                    "/api/v1/content/journal_v2_news_links", headers=headers,
                    json={"documents": [{
                        "owner": scope, "key": _journal_news_link_key(scoped_document),
                        "document": scoped_document,
                    }]},
                )
                sync_states = client.post(
                    "/api/v1/content/journal_sync_states", headers=headers,
                    json={"documents": [{
                        "owner": "legacy", "key": "fill-1",
                        "document": {
                            "collection": "journal_group_overrides", "owner": "legacy",
                            "document_key": "fill-1", "origin_broker": "legacy",
                            "origin_environment": "unknown", "origin_account_ref": "legacy-unassigned",
                            "canonical_account_ref": "legacy-unassigned",
                            "is_deleted": False, "updated_at": "2026-09-13T10:00:00",
                        },
                    }]},
                )
        self.assertEqual(1, columns.json()["saved"])
        self.assertEqual(1, links.json()["saved"])
        self.assertEqual(1, scoped_links.json()["saved"])
        self.assertEqual(1, sync_states.json()["saved"])

    def test_current_fastapi_and_content_client_complete_journal_round_trip(self) -> None:
        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.close()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = CentralServerSettings(
                f"sqlite:///{root / 'server.sqlite3'}", "private-token",
            )
            source_path, target_path = root / "source.sqlite3", root / "target.sqlite3"
            source = JournalRepository(source_path)
            target = JournalRepository(target_path)
            source.assign_group(("fill-1",), "manual:round-trip")

            with TestClient(create_app(settings)) as api:
                def opener(request, **_kwargs):
                    parsed = urlsplit(request.full_url)
                    response = api.request(
                        request.method, parsed.path + (f"?{parsed.query}" if parsed.query else ""),
                        headers=dict(request.header_items()), content=request.data,
                    )
                    if response.status_code >= 400:
                        raise HTTPError(
                            request.full_url, response.status_code, response.reason_phrase,
                            dict(response.headers), io.BytesIO(response.content),
                        )
                    return Response(response.content)

                client = CentralContentClient(
                    "https://in-process.test", "private-token", opener=opener,
                )
                self.assertGreater(CentralJournalSyncService(client).sync(source_path), 0)
                self.assertGreater(CentralJournalSyncService(client).sync(target_path), 0)

            self.assertEqual(
                {"fill-1": "manual:round-trip"}, target.load_group_overrides(),
            )

    def test_v2_sync_state_rejects_mismatch_and_round_trips_valid_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
            )
            headers = {"Authorization": "Bearer private-token"}
            scope = "11111111-1111-4111-8111-111111111111"
            document = {
                "collection": "journal_v2_group_overrides", "owner": scope,
                "document_key": "fill:v2:key", "origin_broker": "kiwoom",
                "origin_environment": "real", "origin_account_ref": scope,
                "canonical_account_ref": scope, "is_deleted": True,
                "updated_at": "2026-09-13T10:00:00",
            }
            with TestClient(create_app(settings)) as client:
                malformed = client.post(
                    "/api/v1/content/journal_v2_sync_states", headers=headers,
                    json={"documents": [{"owner": scope, "key": "wrong", "document": document}]},
                )
                valid = client.post(
                    "/api/v1/content/journal_v2_sync_states", headers=headers,
                    json={"documents": [{
                        "owner": scope, "key": "fill:v2:key", "document": document,
                    }]},
                )
                loaded = client.get(
                    "/api/v1/content/journal_v2_sync_states", headers=headers,
                )
            self.assertEqual(422, malformed.status_code)
            self.assertEqual(1, valid.json()["saved"])
            self.assertEqual(document, loaded.json()["documents"][0]["document"])


if __name__ == "__main__":
    unittest.main()
