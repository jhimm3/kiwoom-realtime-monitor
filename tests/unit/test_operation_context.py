"""Product correlation state ownership and legacy PostgreSQL import contracts."""
from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, fields
import unittest

from kiwoom_monitor.central_server import postgres_access as legacy
from kiwoom_monitor.domain import operation_context as context


class OperationContextTests(unittest.TestCase):
    def test_legacy_names_alias_the_same_state_and_functions(self):
        for name in ("_DB_CALL_SOURCE", "_DB_CALL_REQUEST_ID", "db_call_source",
                     "db_call_request_id", "current_db_call_tags"):
            with self.subTest(name=name):
                self.assertIs(getattr(legacy, name), getattr(context, name))
        self.assertEqual("db_call_source", context._DB_CALL_SOURCE.name)
        self.assertEqual("db_call_request_id", context._DB_CALL_REQUEST_ID.name)
        self.assertEqual({"source": "", "request_id": ""}, legacy.current_db_call_tags())

    def test_legacy_scope_keeps_raw_source_and_bounded_request_and_resets_tokens(self):
        source = "s" * 105
        request_id = "r" * 105
        with legacy.db_call_source(source), legacy.db_call_request_id(request_id):
            self.assertEqual(source, context.get_operation_source())
            self.assertEqual("r" * 100, context.get_operation_request_id())
            self.assertEqual({"source": "s" * 100, "request_id": "r" * 100},
                             context.current_db_call_tags())
            with self.assertRaisesRegex(ValueError, "inner"):
                with context.db_call_source("inner"), context.db_call_request_id("inner-request"):
                    self.assertEqual({"source": "inner", "request_id": "inner-request"},
                                     legacy.current_db_call_tags())
                    raise ValueError("inner")
            self.assertEqual(source, context.get_operation_source())
            self.assertEqual("r" * 100, context.get_operation_request_id())
        self.assertEqual(context.OperationContextSnapshot(), context.snapshot_operation_context())

    def test_snapshot_is_frozen_and_does_not_follow_later_context(self):
        with context.db_call_source("accepted"), context.db_call_request_id("request-1"):
            accepted = context.snapshot_operation_context()
        self.assertEqual(context.OperationContextSnapshot("accepted", "request-1"), accepted)
        with self.assertRaises(FrozenInstanceError):
            accepted.source = "changed"
        with context.db_call_source("later"), context.db_call_request_id("request-2"):
            with context.operation_context_scope(accepted):
                self.assertEqual(accepted, context.snapshot_operation_context())
            self.assertEqual(context.OperationContextSnapshot("later", "request-2"),
                             context.snapshot_operation_context())
        self.assertEqual(context.OperationContextSnapshot(), context.snapshot_operation_context())

    def test_snapshot_scope_uses_legacy_request_bound_without_validating_trusted_values(self):
        snapshot = context.OperationContextSnapshot("source\nunchanged", "r" * 105)
        with context.operation_context_scope(snapshot):
            self.assertEqual("source\nunchanged", legacy.current_db_call_tags()["source"])
            self.assertEqual("r" * 100, context.get_operation_request_id())
        with context.db_call_request_id("trusted\nlegacy"):
            self.assertEqual("trusted\nlegacy", context.get_operation_request_id())
        self.assertEqual(context.OperationContextSnapshot(), context.snapshot_operation_context())

    def test_concurrent_tasks_keep_distinct_context_and_restore_their_parent(self):
        async def run():
            ready = asyncio.Event()
            entered = []
            async def worker(name):
                with context.db_call_source(name), legacy.db_call_request_id(name + "-request"):
                    entered.append(name)
                    if len(entered) == 2:
                        ready.set()
                    await ready.wait()
                    await asyncio.sleep(0)
                    observed = context.snapshot_operation_context()
                self.assertEqual(context.OperationContextSnapshot("parent", "parent-request"),
                                 context.snapshot_operation_context())
                return observed
            with context.operation_context_scope(context.OperationContextSnapshot("parent", "parent-request")):
                values = await asyncio.gather(worker("A"), worker("B"))
                self.assertEqual(context.OperationContextSnapshot("parent", "parent-request"),
                                 context.snapshot_operation_context())
            return values
        self.assertEqual([context.OperationContextSnapshot("A", "A-request"),
                          context.OperationContextSnapshot("B", "B-request")], asyncio.run(run()))
        self.assertEqual(context.OperationContextSnapshot(), context.snapshot_operation_context())

    def test_cancelled_task_resets_scope_tokens_to_inherited_parent(self):
        async def run():
            entered = asyncio.Event()
            after_cancel = []
            async def worker():
                try:
                    with context.operation_context_scope(context.OperationContextSnapshot("child", "child-request")):
                        entered.set()
                        await asyncio.Future()
                finally:
                    after_cancel.append(context.snapshot_operation_context())
            parent = context.OperationContextSnapshot("parent", "parent-request")
            with context.operation_context_scope(parent):
                task = asyncio.create_task(worker())
                await entered.wait()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertEqual(parent, context.snapshot_operation_context())
            self.assertEqual([parent], after_cancel)
        asyncio.run(run())
        self.assertEqual(context.OperationContextSnapshot(), context.snapshot_operation_context())

    def test_to_thread_copies_same_product_state_and_restores_caller(self):
        async def run():
            accepted = context.OperationContextSnapshot("worker-source", "worker-request")
            with legacy.db_call_source(accepted.source), legacy.db_call_request_id(accepted.request_id):
                observed = await asyncio.to_thread(context.snapshot_operation_context)
                self.assertEqual(accepted, context.snapshot_operation_context())
                return observed
        self.assertEqual(context.OperationContextSnapshot("worker-source", "worker-request"), asyncio.run(run()))
        self.assertEqual(context.OperationContextSnapshot(), context.snapshot_operation_context())

    def test_validator_preserves_safe_opaque_values_and_only_omits_invalid_metadata(self):
        for value in ("opaque-id", "x" * 100, "한글 식별값", " /prefix:request_id. "):
            with self.subTest(value=value):
                self.assertEqual(value, context.validated_request_id(value))
        for value in (None, 1, b"bytes", "", "x" * 101, "before\rafter", "before\nafter"):
            with self.subTest(value=value):
                self.assertEqual("", context.validated_request_id(value))
        for point in (*range(0x20), *range(0x7F, 0xA0)):
            with self.subTest(control=point):
                self.assertEqual("", context.validated_request_id("before" + chr(point) + "after"))
        self.assertEqual(context.OperationContextSnapshot(), context.snapshot_operation_context())

    def test_writer_context_factories_still_use_the_same_contextvars_and_replay_factory(self):
        defaults = {field.name: field.default_factory for field in fields(legacy.DBWriterContext)}
        self.assertIs(context._DB_CALL_SOURCE, defaults["source"].__self__)
        self.assertIs(context._DB_CALL_REQUEST_ID, defaults["request_id"].__self__)
        self.assertIs(legacy._recorded_identity, defaults["recorded_identity"])



class HttpIngressOperationContextTests(unittest.TestCase):
    def middleware(self, app):
        from datetime import datetime
        from kiwoom_monitor.central_server.app import _HttpOperationContextMiddleware
        from kiwoom_monitor.domain.time_basis import KST
        return _HttpOperationContextMiddleware(app, server_now=lambda: datetime(2026, 10, 24, 9, 0, tzinfo=KST),
                                               access_token="configured-bearer")

    def test_wire_validator_preserves_serializable_opaque_values_and_omits_invalid_metadata(self):
        import uuid
        from kiwoom_monitor.infrastructure.central_content_client import _http_request_id
        for value in (" /opaque.42 ", "é" * 100):
            self.assertEqual(value, _http_request_id(value, "configured-bearer"))
        for value in (None, 42, b"bytes", "", "x" * 101, "bad\rID", "한글", "a-configured-bearer-b"):
            wire = _http_request_id(value, "configured-bearer")
            self.assertEqual(uuid.UUID(wire).hex, wire)

    def test_native_thread_downstream_and_actual_send_share_captured_ingress_id(self):
        import io
        import json
        from kiwoom_monitor.infrastructure.central_content_client import CentralContentClient
        observed, messages, sent_contexts, outbound = [], [], [], []
        class Response(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *_): self.close()
        def opener(request, **kwargs):
            outbound.append((request.get_header("X-request-id"), context.snapshot_operation_context()))
            return Response(b'{"status":"ok"}')
        async def route(scope, receive, send):
            observed.append(context.snapshot_operation_context())
            with context.db_call_source("route.native"):
                observed.append(await asyncio.to_thread(context.snapshot_operation_context))
                await asyncio.to_thread(CentralContentClient("https://nas.test", "configured-bearer", opener=opener).load_health)
            await send({"type": "http.response.start", "status": 207, "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": b'{"original":true}'})
        async def send(message):
            messages.append(message)
            sent_contexts.append(context.snapshot_operation_context())
        async def receive(): return {"type": "http.request", "body": b""}
        parent = context.OperationContextSnapshot("parent", "parent-id")
        async def run():
            with context.operation_context_scope(parent):
                await self.middleware(route)({"type": "http", "headers": [(b"x-request-id", b"ingress-42"), (b"x-kiwoom-clock", b"1")]}, receive, send)
                self.assertEqual(parent, context.snapshot_operation_context())
        asyncio.run(run())
        ingress = context.OperationContextSnapshot("http.request", "ingress-42")
        native = context.OperationContextSnapshot("route.native", "ingress-42")
        self.assertEqual([ingress, native], observed)
        self.assertEqual([("ingress-42", native)], outbound)
        self.assertEqual([ingress, ingress], sent_contexts)
        self.assertEqual(207, messages[0]["status"])
        self.assertEqual(b'{"original":true}', messages[1]["body"])
        self.assertEqual(b"ingress-42", dict(messages[0]["headers"])[b"x-request-id"])
        self.assertEqual(b"2026-10-24T09:00:00+09:00", dict(messages[0]["headers"])[b"x-kiwoom-server-time"])

    def test_missing_invalid_and_known_authorization_overlap_continue_with_unique_safe_ids(self):
        import uuid
        seen, messages = [], []
        async def route(scope, receive, send):
            seen.append(context.snapshot_operation_context())
            await send({"type": "http.response.start", "status": 401, "headers": []})
            await send({"type": "http.response.body", "body": b"original-denied"})
        async def send(message): messages.append(message)
        async def receive(): return {"type": "http.request", "body": b""}
        async def run():
            for value in (None, b"bad\nID", b"x" * 101, b"prefix-configured-bearer", b"prefix-supplied-bearer"):
                headers = [(b"authorization", b"Bearer supplied-bearer")]
                if value is not None: headers.append((b"x-request-id", value))
                await self.middleware(route)({"type": "http", "headers": headers}, receive, send)
                self.assertEqual(context.OperationContextSnapshot(), context.snapshot_operation_context())
        asyncio.run(run())
        self.assertEqual(5, len({item.request_id for item in seen}))
        for value, response in zip(seen, messages[::2]):
            self.assertEqual(uuid.UUID(value.request_id).hex, value.request_id)
            self.assertEqual(value.request_id.encode(), dict(response["headers"])[b"x-request-id"])
            self.assertNotIn(b"x-kiwoom-server-time", dict(response["headers"]))
            self.assertEqual(401, response["status"])

    def test_exception_and_actual_cancellation_reset_to_parent_without_rewriting_error(self):
        parent = context.OperationContextSnapshot("parent", "parent-id")
        async def receive(): return {"type": "http.request", "body": b""}
        async def send(message): self.fail("No response should be manufactured")
        failure = ValueError("original failure")
        async def fail(scope, receive, send): raise failure
        async def run():
            with context.operation_context_scope(parent):
                with self.assertRaises(ValueError) as caught:
                    await self.middleware(fail)({"type": "http", "headers": []}, receive, send)
                self.assertIs(failure, caught.exception)
                self.assertEqual(parent, context.snapshot_operation_context())
                entered, reset = asyncio.Event(), []
                async def wait(scope, receive, send):
                    entered.set()
                    await asyncio.Future()
                async def caller():
                    try:
                        await self.middleware(wait)({"type": "http", "headers": []}, receive, send)
                    finally:
                        reset.append(context.snapshot_operation_context())
                task = asyncio.create_task(caller())
                await entered.wait()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError): await task
                self.assertEqual([parent], reset)
                self.assertEqual(parent, context.snapshot_operation_context())
        asyncio.run(run())

    def test_concurrent_http_scopes_and_lifespan_websocket_are_independent(self):
        observed, sent = [], []
        entered = []
        async def route(scope, receive, send):
            if scope["type"] != "http":
                observed.append((scope["type"], context.snapshot_operation_context()))
                return
            entered.append(scope)
            while len(entered) < 2: await asyncio.sleep(0)
            await asyncio.sleep(0)
            observed.append((scope["type"], context.snapshot_operation_context()))
            await send({"type": "http.response.start", "status": 200, "headers": []})
        async def receive(): return {"type": "http.request", "body": b""}
        async def send(message): sent.append(context.snapshot_operation_context())
        parent = context.OperationContextSnapshot("parent", "parent-id")
        async def run():
            middleware = self.middleware(route)
            with context.operation_context_scope(parent):
                await asyncio.gather(*(middleware({"type": "http", "headers": [(b"x-request-id", name)]}, receive, send)
                                       for name in (b"A", b"B")))
                await middleware({"type": "lifespan"}, receive, send)
                await middleware({"type": "websocket"}, receive, send)
                self.assertEqual(parent, context.snapshot_operation_context())
        asyncio.run(run())
        self.assertEqual({context.OperationContextSnapshot("http.request", "A"), context.OperationContextSnapshot("http.request", "B")}, set(sent))
        self.assertEqual([("lifespan", parent), ("websocket", parent)], observed[-2:])


class ActualRegisteredHttpContextTests(unittest.TestCase):
    def test_registered_query_router_carries_ingress_through_actual_broker_and_native_store(self):
        from datetime import datetime
        import json
        import tempfile
        import threading
        from pathlib import Path
        from fastapi import FastAPI, Header, HTTPException
        from kiwoom_monitor.central_server.app import _HttpOperationContextMiddleware
        from kiwoom_monitor.central_server.database import SQLiteQueryStore
        from kiwoom_monitor.central_server.market_query_routes import create_market_query_router
        from kiwoom_monitor.central_server.rest_broker import CentralRestBroker
        from kiwoom_monitor.domain.time_basis import KST
        from tests.unit.test_central_rest_broker import FakeClient

        async def scenario(directory):
            observed, messages = [], []
            loop_thread = threading.get_ident()
            expected = context.OperationContextSnapshot("http.request", "http-native-42")

            class Store(SQLiteQueryStore):
                def load_query(inner, key):
                    observed.append(("lookup", context.snapshot_operation_context(), threading.get_ident()))
                    return super().load_query(key)

                def save_query(inner, *args):
                    observed.append(("cache_write", context.snapshot_operation_context(), threading.get_ident()))
                    return super().save_query(*args)

            class Client(FakeClient):
                def request_with_continuation(inner, *args, **kwargs):
                    observed.append(("transport", context.snapshot_operation_context(), threading.get_ident()))
                    return super().request_with_continuation(*args, **kwargs)

            store = Store(Path(directory) / "context.db")
            store.initialize()

            def record(api_id, body, result):
                observed.append(("ingest", context.snapshot_operation_context(), threading.get_ident()))
                store.upsert_documents("http_context_test", [{
                    "owner": "", "key": "accepted", "document": {"request_id": context.get_operation_request_id(), "payload": result},
                }])

            client = Client()
            broker = CentralRestBroker(client, store, record)

            def authorize(authorization: str | None = Header(default=None)):
                if authorization != "Bearer configured-bearer":
                    raise HTTPException(status_code=401)

            app = FastAPI()
            now = datetime(2026, 10, 24, 9, tzinfo=KST)
            app.include_router(create_market_query_router(store, authorize, broker, None, now_provider=lambda: now))
            app.add_middleware(_HttpOperationContextMiddleware, server_now=lambda: now, access_token="configured-bearer")
            payload = json.dumps({"api_id": "ka10081", "path": "/api/dostk/chart", "body": {"stk_cd": "005930"}}).encode()
            scope = {"type": "http", "http_version": "1.1", "method": "POST", "scheme": "https",
                     "path": "/api/v1/kiwoom/query", "raw_path": b"/api/v1/kiwoom/query", "root_path": "", "query_string": b"",
                     "headers": [(b"content-type", b"application/json"), (b"x-request-id", b"http-native-42"),
                                 (b"authorization", b"Bearer configured-bearer")],
                     "client": ("127.0.0.1", 9000), "server": ("nas.test", 443)}

            async def receive():
                return {"type": "http.request", "body": payload, "more_body": False}

            async def send(message):
                self.assertEqual(expected, context.snapshot_operation_context())
                messages.append(message)

            parent = context.OperationContextSnapshot("parent", "parent-id")
            try:
                with context.operation_context_scope(parent):
                    await app(scope, receive, send)
                    self.assertEqual(parent, context.snapshot_operation_context())
                self.assertEqual(["lookup", "transport", "ingest", "cache_write"], [row[0] for row in observed])
                self.assertTrue(all(row[1] == expected and row[2] != loop_thread for row in observed))
                self.assertEqual(200, messages[0]["status"])
                self.assertEqual(b"http-native-42", dict(messages[0]["headers"])[b"x-request-id"])
                returned = json.loads(b"".join(m.get("body", b"") for m in messages))
                self.assertEqual({"return_code": 0, "api_id": "ka10081"}, returned["payload"])
                self.assertEqual({"request_id": "http-native-42", "payload": returned["payload"]},
                                 store.load_documents("http_context_test", "", 1)[0]["document"])
                self.assertEqual(1, len(client.calls))
                self.assertFalse(broker._inflight)
            finally:
                await broker.close()
                store.close()

        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(scenario(directory))

    async def invoke(self, app, path, request_id="actual-42"):
        messages, sends = [], []
        scope = {"type": "http", "http_version": "1.1", "method": "GET", "scheme": "https",
                 "path": path, "raw_path": path.encode(), "root_path": "", "query_string": b"",
                 "headers": [(b"x-request-id", request_id.encode()), (b"x-kiwoom-clock", b"1"),
                             (b"authorization", b"Bearer configured-bearer")],
                 "client": ("127.0.0.1", 9000), "server": ("nas.test", 443)}
        async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
        async def send(message):
            messages.append(message)
            sends.append(context.snapshot_operation_context())
        error = None
        try:
            await app(scope, receive, send)
        except ValueError as original:
            error = original
        return messages, sends, error

    def test_actual_create_app_registration_preserves_native_context_and_current_theme_route(self):
        import tempfile
        from pathlib import Path
        from kiwoom_monitor.central_server.app import create_app
        from kiwoom_monitor.central_server.app import create_query_store as real_factory
        from kiwoom_monitor.central_server.config import CentralServerSettings
        from unittest.mock import patch
        observed = []
        stores = []
        def track_store(*args, **kwargs):
            store = real_factory(*args, **kwargs)
            stores.append(store)
            return store
        with tempfile.TemporaryDirectory() as directory:
            with patch("kiwoom_monitor.central_server.app.create_query_store", side_effect=track_store):
                app = create_app(CentralServerSettings("sqlite:///" + str(Path(directory) / "central.db"),
                    "configured-bearer", autonomous_top20_enabled=False,
                    market_event_collection_enabled=False, news_history_jobs_enabled=False))
            @app.get("/_context_probe")
            async def probe():
                observed.append(await asyncio.to_thread(context.snapshot_operation_context))
                return {"original": True}
            messages, sends, error = asyncio.run(self.invoke(app, "/_context_probe"))
            self.assertIsNone(error)
            expected = context.OperationContextSnapshot("http.request", "actual-42")
            self.assertEqual([expected], observed)
            self.assertEqual([expected] * len(messages), sends)
            self.assertEqual(b"actual-42", dict(messages[0]["headers"])[b"x-request-id"])
            self.assertEqual(b'{"original":true}', messages[1]["body"])
            theme_messages, _, theme_error = asyncio.run(self.invoke(app, "/api/v1/themes/current-snapshot"))
            self.assertIsNone(theme_error)
            self.assertEqual(200, theme_messages[0]["status"])
            self.assertEqual(b"actual-42", dict(theme_messages[0]["headers"])[b"x-request-id"])
            for store in stores:
                store.close()
        self.assertEqual(context.OperationContextSnapshot(), context.snapshot_operation_context())

    def test_outer_framework_500_body_and_custom_handler_remain_unchanged(self):
        from fastapi import FastAPI, HTTPException
        from fastapi.responses import JSONResponse
        from kiwoom_monitor.central_server.app import _HttpOperationContextMiddleware
        from datetime import datetime
        from kiwoom_monitor.domain.time_basis import KST
        for custom in (False, True):
            app = FastAPI()
            app.add_middleware(_HttpOperationContextMiddleware,
                server_now=lambda: datetime(2026, 10, 24, 9, tzinfo=KST), access_token="configured-bearer")
            original = ValueError("original failure")
            handler_calls = []
            if custom:
                @app.exception_handler(Exception)
                async def custom_handler(request, error):
                    handler_calls.append(error)
                    return JSONResponse({"custom": "preserved"}, status_code=500)
            @app.get("/error")
            async def error(): raise original
            @app.get("/handled")
            async def handled(): raise HTTPException(409, detail="original conflict")
            messages, sends, caught = asyncio.run(self.invoke(app, "/error"))
            self.assertIs(original, caught)
            self.assertEqual(500, messages[0]["status"])
            self.assertEqual(b'{"custom":"preserved"}' if custom else b"Internal Server Error", messages[1]["body"])
            self.assertNotIn(b"x-request-id", dict(messages[0]["headers"]))
            self.assertNotIn(b"x-kiwoom-server-time", dict(messages[0]["headers"]))
            self.assertEqual([original] if custom else [], handler_calls)
            self.assertEqual([context.OperationContextSnapshot()] * len(sends), sends)
            handled_messages, _, caught = asyncio.run(self.invoke(app, "/handled"))
            self.assertIsNone(caught)
            self.assertEqual(409, handled_messages[0]["status"])
            self.assertEqual(b'{"detail":"original conflict"}', handled_messages[1]["body"])
            self.assertEqual(b"actual-42", dict(handled_messages[0]["headers"])[b"x-request-id"])
            self.assertEqual(context.OperationContextSnapshot(), context.snapshot_operation_context())


class HttpIngressBoundaryFailureTests(unittest.TestCase):
    def test_duplicate_authorization_secrets_are_not_echoed_and_first_clock_semantics_stay(self):
        from datetime import datetime
        from kiwoom_monitor.domain.time_basis import KST
        from kiwoom_monitor.central_server.app import _HttpOperationContextMiddleware
        messages = []
        async def route(scope, receive, send):
            await send({"type": "http.response.start", "status": 401, "headers": []})
            await send({"type": "http.response.body", "body": b"original auth failure"})
        async def receive(): return {"type": "http.request", "body": b""}
        async def send(message): messages.append(message)
        async def run():
            middleware = _HttpOperationContextMiddleware(route, access_token="configured-bearer",
                server_now=lambda: datetime(2026, 10, 24, 9, tzinfo=KST))
            await middleware({"type": "http", "headers": [
                (b"authorization", b"Bearer first-known-bearer"), (b"authorization", b"Other ignored"),
                (b"x-request-id", b"prefix-first-known-bearer"), (b"x-request-id", b"second-opaque"),
                (b"x-kiwoom-clock", b"1"), (b"x-kiwoom-clock", b"0")]}, receive, send)
        asyncio.run(run())
        headers = dict(messages[0]["headers"])
        self.assertNotIn(b"first-known-bearer", headers[b"x-request-id"])
        self.assertNotEqual(b"second-opaque", headers[b"x-request-id"])
        self.assertEqual(401, messages[0]["status"])
        self.assertEqual(b"original auth failure", messages[1]["body"])
        self.assertEqual(b"2026-10-24T09:00:00+09:00", headers[b"x-kiwoom-server-time"])
        self.assertEqual(context.OperationContextSnapshot(), context.snapshot_operation_context())

    def test_send_failure_keeps_original_exception_and_restores_scope(self):
        from datetime import datetime
        from kiwoom_monitor.domain.time_basis import KST
        from kiwoom_monitor.central_server.app import _HttpOperationContextMiddleware
        failure = OSError("wire send failed")
        parent = context.OperationContextSnapshot("parent", "parent-id")
        observed = []
        async def route(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
        async def receive(): return {"type": "http.request", "body": b""}
        async def send(message):
            observed.append(context.snapshot_operation_context())
            raise failure
        async def run():
            middleware = _HttpOperationContextMiddleware(route, access_token="configured-bearer",
                server_now=lambda: datetime(2026, 10, 24, 9, tzinfo=KST))
            with context.operation_context_scope(parent):
                with self.assertRaises(OSError) as caught:
                    await middleware({"type": "http", "headers": [(b"x-request-id", b"send-42")]}, receive, send)
                self.assertIs(failure, caught.exception)
                self.assertEqual(parent, context.snapshot_operation_context())
        asyncio.run(run())
        self.assertEqual([context.OperationContextSnapshot("http.request", "send-42")], observed)

if __name__ == "__main__":
    unittest.main()
