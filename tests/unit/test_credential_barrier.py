from __future__ import annotations

import asyncio
import json
import threading
import time
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from dataclasses import replace

from kiwoom_monitor.central_server.rest_broker import CentralRestBroker, BrokerCredentialBusyError
from kiwoom_monitor.infrastructure.kiwoom_rest.client import KiwoomRestClient, KiwoomApiError, KiwoomCredentialBusyError
from kiwoom_monitor.infrastructure.kiwoom_rest.settings import KiwoomSettings


class Response:
    headers = {}
    def __init__(self, payload): self.payload = payload
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def read(self): return json.dumps(self.payload).encode()


class Opener:
    def __init__(self):
        self.requests = []
        self.tokens = []
    def __call__(self, request, **_):
        self.requests.append(request)
        if request.full_url.endswith("/oauth2/token"):
            token = uuid.uuid4().hex
            self.tokens.append(token)
            return Response({"token": token, "expires_dt": (datetime.now(UTC) + timedelta(days=1)).strftime("%Y%m%d%H%M%S")})
        return Response({"return_code": 0, "acctNo": "".join(str(uuid.uuid4().int)[:10])})


def settings(environment="mock"):
    return KiwoomSettings(uuid.uuid4().hex, uuid.uuid4().hex, environment)


class ClientCredentialBarrierTests(unittest.TestCase):
    def test_token_only_and_expired_candidates_cannot_activate(self):
        client = KiwoomRestClient(settings(), opener=Opener(), request_interval_seconds=0)
        token_only = client.prepare_credential_token(settings())
        verified = client.verify_prepared_credentials(token_only)
        expired = replace(verified, expires_at=datetime.now(UTC) - timedelta(seconds=1))
        with self.assertRaisesRegex(KiwoomApiError, "CANDIDATE_TOKEN_EXPIRED"):
            client.verify_prepared_credentials(expired)
        client.begin_credential_change()
        with self.assertRaisesRegex(KiwoomApiError, "CANDIDATE_ACCOUNT_QUERY_REQUIRED"):
            client.activate_prepared_credentials(token_only)
        with self.assertRaisesRegex(KiwoomApiError, "CANDIDATE_TOKEN_EXPIRED"):
            client.activate_prepared_credentials(expired)
        client.end_credential_change()
        self.assertEqual(0, client._credential_generation)
    def test_candidate_isolated_and_uses_original_lock_and_rate_history(self):
        opener = Opener()
        client = KiwoomRestClient(settings(), opener=opener)
        slots = []
        def slot(snapshot):
            slots.append((id(snapshot._request_lock), snapshot._last_request_at))
            snapshot._last_request_at = (snapshot._last_request_at or 0) + 1
        with patch.object(KiwoomRestClient, "_wait_for_request_slot", slot):
            old_token = client.get_access_token()
            old_settings = client._settings
            candidate = client.prepare_credentials(settings())
            self.assertEqual(old_token, client.get_access_token())
            self.assertIs(old_settings, client._settings)
            self.assertEqual([(id(client._request_lock), None), (id(client._request_lock), 1), (id(client._request_lock), 2)], slots)
            self.assertEqual(3, client._last_request_at)
            self.assertEqual(1.0, client._request_interval_seconds)
            self.assertNotIn(candidate.token, repr(candidate))
            self.assertNotIn(candidate.settings.secret_key, repr(candidate))
            client.begin_credential_change()
            for request in (
                client.get_access_token,
                lambda: client.request("ka00001", "/api/dostk/acnt", {}),
                lambda: client.request_once("kt10000", "/api/dostk/ordr", {}),
                lambda: client.request_with_continuation("ka00001", "/api/dostk/acnt", {}),
            ):
                with self.assertRaises(KiwoomCredentialBusyError): request()
            count = len(opener.requests)
            self.assertEqual(1, client.activate_prepared_credentials(candidate))
            with self.assertRaises(KiwoomCredentialBusyError): client.activate_prepared_credentials(candidate)
            client.end_credential_change()
            self.assertEqual(candidate.token, client.get_access_token())
            self.assertEqual(count, len(opener.requests))
            self.assertEqual(3, client._last_request_at)

    def test_invalid_candidate_does_not_replace_active_and_error_is_safe(self):
        opener = Opener()
        client = KiwoomRestClient(settings(), opener=opener, request_interval_seconds=0)
        token = client.get_access_token()
        active = client._settings
        candidate = settings()
        def rejected(request, **_):
            return Response({"return_code": 3, "return_msg": candidate.secret_key})
        client._opener = rejected
        with self.assertRaisesRegex(KiwoomApiError, "^CANDIDATE_VALIDATION_FAILED$") as caught:
            client.prepare_credentials(candidate)
        self.assertNotIn(candidate.secret_key, str(caught.exception))
        self.assertIs(active, client._settings)
        self.assertEqual(token, client.get_access_token())
        with self.assertRaises(KiwoomApiError): client.prepare_credentials(settings("real"))


async def wait_until(predicate):
    deadline = time.monotonic() + 3
    while not predicate():
        if time.monotonic() > deadline: raise AssertionError("test deadline")
        await asyncio.sleep(0.001)


class BrokerCredentialBarrierTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_resume_finishes_and_next_cycle_is_not_skipped(self):
        started, release = threading.Event(), threading.Event()
        class Client:
            resumed = 0
            def begin_credential_change(self): pass
            def end_credential_change(self): started.set(); release.wait(3); self.resumed += 1
        client = Client()
        broker = CentralRestBroker(client)
        try:
            await broker.begin_credential_change()
            resume = asyncio.create_task(broker.end_credential_change())
            await wait_until(started.is_set)
            resume.cancel()
            with self.assertRaises(asyncio.CancelledError): await resume
            self.assertTrue(broker._credential_paused)
            release.set()
            await wait_until(lambda: not broker._credential_paused)
            await broker.begin_credential_change()
            await broker.end_credential_change()
            self.assertEqual(2, client.resumed)
        finally:
            release.set()
            await broker.close()
    async def test_cancelled_close_does_not_cancel_actual_job(self):
        started, release = threading.Event(), threading.Event()
        class Client:
            def request_with_continuation(self, *_, **__): started.set(); release.wait(3); return {}, False, ""
        persisted = []
        broker = CentralRestBroker(Client(), response_handler=lambda *_: persisted.append(True))
        request = asyncio.create_task(broker.request("ka00001", "/api/dostk/acnt", {}))
        try:
            await wait_until(started.is_set)
            close = asyncio.create_task(broker.close())
            await wait_until(lambda: broker._close_task is not None)
            close.cancel()
            with self.assertRaises(asyncio.CancelledError): await close
            self.assertFalse(broker._close_task.done())
            with self.assertRaises(BrokerCredentialBusyError): await broker.request("ka00001", "/api/dostk/acnt", {})
            release.set()
            await request
            await broker.close()
            self.assertEqual([True], persisted)
        finally:
            release.set()
            await broker.close()

    async def test_ranking_arriving_during_candidate_oauth_precedes_identity_query(self):
        started, release = threading.Event(), threading.Event()
        class Client:
            def __init__(self): self.calls = []
            def prepare_credential_token(self, _):
                self.calls.append("oauth"); started.set(); release.wait(3); return object()
            def verify_prepared_credentials(self, candidate): self.calls.append("ka00001"); return candidate
            def request_with_continuation(self, api_id, *_, **__): self.calls.append(api_id); return {}, False, ""
        client = Client()
        broker = CentralRestBroker(client)
        candidate = asyncio.create_task(broker.prepare_credentials(settings("real")))
        try:
            await wait_until(started.is_set)
            ranking = asyncio.create_task(broker.request("ka00198", "/api/dostk/stkinfo", {}))
            await wait_until(lambda: broker._queue.qsize() == 1)
            release.set()
            await ranking; await candidate
            self.assertEqual(["oauth", "ka00198", "ka00001"], client.calls)
        finally:
            release.set()
            await broker.close()

    async def test_token_refresh_thread_is_included_in_drain(self):
        started, release = threading.Event(), threading.Event()
        opener = Opener()
        def blocked(request, **kwargs): started.set(); release.wait(3); return opener(request, **kwargs)
        client = KiwoomRestClient(settings(), opener=blocked, request_interval_seconds=0)
        broker = CentralRestBroker(client)
        refresh = asyncio.create_task(asyncio.to_thread(client.get_access_token))
        try:
            await wait_until(started.is_set)
            drain = asyncio.create_task(broker.begin_credential_change())
            await wait_until(lambda: broker._credential_paused)
            await asyncio.sleep(0.01)
            self.assertFalse(drain.done())
            release.set()
            await refresh; await drain
            with self.assertRaises(KiwoomCredentialBusyError): client.get_access_token()
            await broker.end_credential_change()
        finally:
            release.set()
            await broker.close()

    async def test_cancelled_request_and_drain_wait_for_actual_http_and_db(self):
        http_started, release_http, db_started, release_db = [threading.Event() for _ in range(4)]
        class Client:
            paused = False
            def request_with_continuation(self, *_, **__):
                http_started.set()
                release_http.wait(3)
                return {"return_code": 0}, False, ""
            def begin_credential_change(self): self.paused = True
            def end_credential_change(self): self.paused = False
        def persist(*_):
            db_started.set()
            release_db.wait(3)
        client = Client()
        broker = CentralRestBroker(client, response_handler=persist)
        request = asyncio.create_task(broker.request("ka00001", "/api/dostk/acnt", {}))
        try:
            await wait_until(http_started.is_set)
            request.cancel()
            with self.assertRaises(asyncio.CancelledError): await request
            drain = asyncio.create_task(broker.begin_credential_change())
            await wait_until(lambda: broker._credential_paused)
            with self.assertRaises(BrokerCredentialBusyError): await broker.request("ka00198", "/api/dostk/stkinfo", {})
            drain.cancel()
            with self.assertRaises(asyncio.CancelledError): await drain
            with self.assertRaises(BrokerCredentialBusyError): await broker.end_credential_change()
            self.assertFalse(client.paused)
            release_http.set()
            await wait_until(db_started.is_set)
            self.assertFalse(broker._drain_task.done())
            release_db.set()
            await broker.begin_credential_change()
            self.assertTrue(client.paused)
            await broker.end_credential_change()
            self.assertFalse(client.paused)
        finally:
            release_http.set()
            release_db.set()
            await broker.close()

    async def test_management_job_ranking_priority_and_generation_cache_fence(self):
        started, release = threading.Event(), threading.Event()
        class Client:
            def __init__(self): self.calls = []; self.generation = 0
            def request_with_continuation(self, api_id, *_, **__):
                if not self.calls:
                    started.set(); release.wait(3)
                self.calls.append(api_id)
                return {"generation": self.generation}, False, ""
            def prepare_credential_token(self, _): self.calls.append("candidate-token"); return object()
            def verify_prepared_credentials(self, candidate): self.calls.append("candidate-identity"); return candidate
            def begin_credential_change(self): pass
            def activate_prepared_credentials(self, _): self.generation += 1; return self.generation
            def end_credential_change(self): pass
        client = Client()
        broker = CentralRestBroker(client)
        first = asyncio.create_task(broker.request("ka10083", "/api/dostk/chart", {}))
        try:
            await wait_until(started.is_set)
            candidate_task = asyncio.create_task(broker.prepare_credentials(settings("real")))
            ranking = asyncio.create_task(broker.request("ka00198", "/api/dostk/stkinfo", {}))
            await wait_until(lambda: broker._queue.qsize() == 2)
            release.set()
            await first; await ranking
            candidate = await candidate_task
            self.assertEqual(["ka10083", "ka00198", "candidate-token", "candidate-identity"], client.calls)
            old_key = broker._fingerprint("ka10083", "/api/dostk/chart", {}, "N", "")
            await broker.begin_credential_change()
            self.assertEqual(1, await broker.activate_prepared_credentials(candidate))
            self.assertEqual(1, await broker.activate_prepared_credentials(candidate))
            with self.assertRaises(BrokerCredentialBusyError): await broker.activate_prepared_credentials(object())
            await broker.end_credential_change()
            self.assertNotEqual(old_key, broker._fingerprint("ka10083", "/api/dostk/chart", {}, "N", ""))
            result = await broker.request("ka10083", "/api/dostk/chart", {})
            self.assertEqual(1, result.payload["generation"])
            self.assertFalse(result.cache_hit)
        finally:
            release.set()
            await broker.close()

    async def test_cancelled_activation_cannot_resume_until_thread_finishes(self):
        started, release = threading.Event(), threading.Event()
        class Client:
            def begin_credential_change(self): pass
            def activate_prepared_credentials(self, _): started.set(); release.wait(3); return 1
            def end_credential_change(self): pass
        broker = CentralRestBroker(Client())
        try:
            await broker.begin_credential_change()
            activation = asyncio.create_task(broker.activate_prepared_credentials(object()))
            await wait_until(started.is_set)
            activation.cancel()
            with self.assertRaises(asyncio.CancelledError): await activation
            with self.assertRaises(BrokerCredentialBusyError): await broker.end_credential_change()
            release.set()
            await wait_until(broker._activation_task.done)
            await broker.end_credential_change()
            self.assertFalse(broker._credential_paused)
        finally:
            release.set()
            await broker.close()
