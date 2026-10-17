from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from fastapi import FastAPI, Header, HTTPException

from kiwoom_monitor.central_server.ai_service import CentralAIService
from kiwoom_monitor.central_server.chatgpt_sessions import create_chatgpt_session_router
from kiwoom_monitor.central_server.config import CentralServerSettings
from kiwoom_monitor.infrastructure.chatgpt_auth import ChatGPTPlanError
from kiwoom_monitor.infrastructure.news_ai import AINewsAnalysis, AIRequestUsage
from tests.unit.test_central_ai_service import _Store


async def request(app, method, path, body=None, *, authorized=True):
    messages = []
    headers = [(b"content-type", b"application/json")]
    if authorized: headers.append((b"authorization", b"Bearer test"))
    async def receive():
        return {"type": "http.request", "body": json.dumps(body).encode() if body is not None else b"", "more_body": False}
    async def send(value): messages.append(value)
    await app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "scheme": "http",
               "method": method, "path": path, "raw_path": path.encode(), "query_string": b"",
               "headers": headers, "client": ("127.0.0.1", 1), "server": ("test", 80)}, receive, send)
    status = next(item["status"] for item in messages if item["type"] == "http.response.start")
    return status, json.loads(b"".join(item.get("body", b"") for item in messages if item["type"] == "http.response.body"))


class Auth:
    def __init__(self):
        self.store = type("Store", (), {"path": Path("isolated-session.enc")})()
        self.generation = 1
        self.connected = True
        self.pause_code = ""

    def pause_analysis(self, code=""):
        self.pause_code = code

    def status(self):
        return {"active": "account", "generation": self.generation, "pause_code": self.pause_code, "accounts": [{
            "id": "account", "label": "Test", "connected": self.connected, "permitted": True}]}

    def disconnect(self):
        self.connected = False
        return {**self.status(), "revocation_confirmed": False}


class NASChatGPTServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.auth, self.store = Auth(), _Store()
        self.service = CentralAIService(CentralServerSettings("sqlite:///:memory:", "test",
            ai_provider="chatgpt_plan", ai_model="catalog-model"), self.store, chatgpt_auth=self.auth)

    async def analyze(self, identity="one"):
        return await self.service.analyze("005930", "삼성전자", "gemini", "paid-fallback", [{
            "identity": identity, "title": "title", "body": "body", "body_hash": identity}], 1)

    async def test_subscription_uses_same_writer_and_cache_fenced_by_session_generation(self):
        analysis = ((AINewsAnalysis("summary", "긍정", 80, "reason"),), AIRequestUsage(1, 2, 3))
        with patch("kiwoom_monitor.central_server.ai_service.analyze_articles", return_value=analysis) as invoke:
            first = await self.analyze()
            cached = await self.analyze()
            self.auth.generation = 2
            changed = await self.analyze()
        self.assertFalse(first["cache_hit"])
        self.assertTrue(cached["cache_hit"])
        self.assertFalse(changed["cache_hit"])
        self.assertEqual(invoke.call_count, 2)
        self.assertEqual(invoke.call_args.kwargs["chatgpt_auth"], self.auth)
        self.assertEqual(invoke.call_args.args[0].api_key, "")
        self.assertEqual([row[1]["document"]["total_tokens"] for row in self.store.values
                          if row[0] == "news_request_usage"], [3, 3])

    async def test_limit_failure_counts_attempt_once_and_fences_accepted_and_future_requests(self):
        started, release = threading.Event(), threading.Event()
        def fail(*args, **kwargs):
            started.set(); release.wait(3)
            raise ChatGPTPlanError("subscription_sharing_usage_limit_exceeded", 429, inference_attempted=True)
        with patch("kiwoom_monitor.central_server.ai_service.analyze_articles", side_effect=fail) as invoke:
            first = asyncio.create_task(self.analyze())
            await asyncio.to_thread(started.wait, 2)
            second = asyncio.create_task(self.analyze("two"))
            await asyncio.sleep(.01)
            release.set()
            results = await asyncio.gather(first, second, return_exceptions=True)
            with self.assertRaises(ChatGPTPlanError): await self.analyze("three")
            self.assertEqual(invoke.call_count, 1)
        self.assertTrue(all(isinstance(value, ChatGPTPlanError) for value in results))
        failed = [row for row in self.store.values if row[0] == "news_request_usage"]
        self.assertEqual(len(failed), 1)
        self.assertFalse(failed[0][1]["document"]["usage_measured"])
        self.assertFalse(any(row[0] == "news_ai" for row in self.store.values))

    async def test_not_connected_does_not_call_provider_or_paid_fallback(self):
        self.auth.connected = False
        with patch("kiwoom_monitor.central_server.ai_service.analyze_articles") as invoke:
            with self.assertRaises(ChatGPTPlanError): await self.analyze()
            invoke.assert_not_called()

    async def test_explicit_subscription_request_cannot_use_operational_paid_provider(self):
        self.service.update_operational_settings(provider="gemini", model="paid", daily_limit=500)
        self.service._keys["gemini"] = "test"
        with patch("kiwoom_monitor.central_server.ai_service.analyze_articles") as invoke:
            with self.assertRaises(ChatGPTPlanError) as failed:
                await self.service.analyze("005930", "삼성전자", "chatgpt_plan", "catalog-model", [], 0)
            self.assertEqual(failed.exception.code, "provider_mismatch")
            invoke.assert_not_called()

    async def test_restart_preserves_pause_until_explicit_resume(self):
        self.auth.pause_analysis("network_error")
        self.service = CentralAIService(CentralServerSettings("sqlite:///:memory:", "test",
            ai_provider="chatgpt_plan", ai_model="catalog-model"), self.store, chatgpt_auth=self.auth)
        with patch("kiwoom_monitor.central_server.ai_service.analyze_articles") as invoke:
            with self.assertRaises(ChatGPTPlanError): await self.analyze()
            invoke.assert_not_called()

    async def test_subscription_job_waits_for_transport_completion_without_timeout_retry(self):
        def delayed(*args, **kwargs):
            import time
            time.sleep(.04)
            return ((AINewsAnalysis("summary", "긍정", 80, "reason"),), AIRequestUsage(1, 2, 3))
        with patch("kiwoom_monitor.central_server.ai_service.analyze_articles", side_effect=delayed) as invoke:
            value = await self.service.analyze_job("005930", "삼성전자", "gemini", "fallback", [{
                "identity": "slow", "title": "title", "body": "body", "body_hash": "slow"}], 1, timeout_seconds=.001)
        self.assertFalse(value["cache_hit"])
        self.assertEqual(invoke.call_count, 1)

    async def test_paid_job_keeps_existing_outer_timeout(self):
        self.service.update_operational_settings(provider="gemini", model="model", daily_limit=0)
        self.service._keys["gemini"] = "test"
        def delayed(*args, **kwargs):
            import time
            time.sleep(.04)
            return ((AINewsAnalysis("summary", "긍정", 80, "reason"),), AIRequestUsage())
        with patch("kiwoom_monitor.central_server.ai_service.analyze_articles", side_effect=delayed):
            with self.assertRaises(TimeoutError):
                await self.service.analyze_job("005930", "삼성전자", "gemini", "model", [{
                    "identity": "paid", "title": "title", "body": "body", "body_hash": "paid"}], 1, timeout_seconds=.001)
            await self.service.close()

    async def test_routes_require_auth_offer_no_http_token_import_and_resume_explicitly(self):
        def authorize(authorization: str = Header(default="")):
            if authorization != "Bearer test": raise HTTPException(401)
        app = FastAPI()
        app.include_router(create_chatgpt_session_router(self.service, authorize))
        self.assertEqual((await request(app, "GET", "/api/v1/news/chatgpt/status", authorized=False))[0], 401)
        self.service._plan_failure = "network_error"
        _status, value = await request(app, "GET", "/api/v1/news/chatgpt/status")
        self.assertTrue(value["paused"])
        self.assertFalse(any("token" in name for name in value))
        self.assertEqual((await request(app, "POST", "/api/v1/news/chatgpt/import", {}))[0], 404)
        with patch("kiwoom_monitor.central_server.chatgpt_sessions.ChatGPTPlanClient.models", return_value=(("Test", "catalog-model"),)):
            self.assertEqual((await request(app, "POST", "/api/v1/news/chatgpt/resume"))[0], 200)
        self.assertEqual(self.service._plan_failure, "")
        # Extra token fields and unknown registrations cannot select an account.
        self.assertEqual((await request(app, "POST", "/api/v1/news/chatgpt/select", {"account_id": "account", "token": "test"}))[0], 422)

    async def test_account_selection_has_resolved_openapi_body_and_rejects_invalid_fields(self):
        def authorize(authorization: str = Header(default="")):
            if authorization != "Bearer test": raise HTTPException(401)
        app = FastAPI()
        app.include_router(create_chatgpt_session_router(self.service, authorize))
        schema = app.openapi()
        body = schema['paths']['/api/v1/news/chatgpt/select']['post']['requestBody']
        self.assertEqual(body['content']['application/json']['schema']['$ref'], '#/components/schemas/AccountSelection')
        self.assertFalse(schema['components']['schemas']['AccountSelection']['additionalProperties'])
        for values in ({'account_id':'with spaces'}, {'account_id':'account','token':'forbidden'}):
            self.assertEqual((await request(app,'POST','/api/v1/news/chatgpt/select',values))[0],422)
