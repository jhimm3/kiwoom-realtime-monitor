import asyncio
from pathlib import Path
import unittest
from unittest.mock import patch

from kiwoom_monitor.central_server.ai_service import CentralAIService
from kiwoom_monitor.central_server.config import CentralServerSettings
from kiwoom_monitor.infrastructure.chatgpt_auth import ChatGPTPlanError
from kiwoom_monitor.infrastructure.chatgpt_plan import GroundedResponse
from tests.unit.test_morning_market_brief import Store


class Auth:
    store = type("ProtectedStore", (), {"path": Path("private/session.enc")})()
    def __init__(self):
        self.connected = True
        self.generation = 1
        self.pause_code = ""
    def status(self):
        return {"active": "selected", "generation": self.generation, "pause_code": self.pause_code,
            "accounts": [{"id": "selected", "connected": self.connected, "permitted": True}]}
    def pause_analysis(self, code): self.pause_code = code


class ResearchServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.auth, self.store = Auth(), Store()
        # News retains Gemini; morning explicitly selects subscription usage.
        self.service = CentralAIService(CentralServerSettings("sqlite:///:memory:", "test",
            ai_provider="gemini", ai_model="gemini-3.5-flash-lite"), self.store, chatgpt_auth=self.auth)
        self.reply = GroundedResponse("{}", {"input_tokens":1,"output_tokens":2,"total_tokens":3},
            ({"url":"https://example.test/"},),1)

    async def asyncTearDown(self): await self.service.close()

    async def research(self):
        return await self.service.research_market("prompt", "chatgpt_plan", "gpt-6.1-sol", "high")

    async def test_explicit_morning_provider_has_no_paid_news_fallback(self):
        with patch("kiwoom_monitor.infrastructure.news_ai.research_market",return_value=self.reply) as call:
            reply = await self.research()
        settings = call.call_args.args[0]
        self.assertEqual(settings.provider,"chatgpt_plan")
        self.assertEqual(settings.api_key,"")
        self.assertEqual(call.call_args.kwargs["reasoning_effort"],"high")
        self.assertEqual(reply.search_calls,1)
        rows = self.store.load_documents("news_request_usage")
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["document"]["purpose"],"morning_market_brief")

    async def test_not_connected_never_tries_any_transport(self):
        self.auth.connected=False
        with patch("kiwoom_monitor.infrastructure.news_ai.research_market") as call:
            with self.assertRaises(ChatGPTPlanError): await self.research()
        call.assert_not_called()

    async def test_failure_pauses_and_counts_unmeasured_post_without_retry(self):
        with patch("kiwoom_monitor.infrastructure.news_ai.research_market",
            side_effect=ChatGPTPlanError("subscription_sharing_unsupported_capability",400,inference_attempted=True)) as call:
            with self.assertRaises(ChatGPTPlanError): await self.research()
            with self.assertRaises(ChatGPTPlanError): await self.research()
        self.assertEqual(call.call_count,1)
        self.assertTrue(self.auth.pause_code)
        rows=self.store.load_documents("news_request_usage")
        self.assertEqual(len(rows),1)
        self.assertFalse(rows[0]["document"]["usage_measured"])

    async def test_retryable_pauses_reopen_at_kst_midnight_even_after_restart(self):
        from copy import deepcopy
        from datetime import datetime, timedelta, timezone
        from threading import RLock
        from types import SimpleNamespace
        from kiwoom_monitor.infrastructure.chatgpt_auth import ChatGPTAuth, DAILY_USAGE_PAUSE, DIRECT_SCOPE

        now = datetime(2035, 1, 2, 14, 59, tzinfo=timezone.utc)
        document = {"active": "selected", "accounts": {"selected": {
            "access_token": "test-token", "scopes": [DIRECT_SCOPE]}}}
        lock = RLock()
        def save(value):
            document.clear()
            document.update(deepcopy(value))

        store = SimpleNamespace(path=Path("private/session.enc"), _lock=lock, locked=lambda: lock,
                                load=lambda: deepcopy(document), save=save)
        def new_service():
            auth = ChatGPTAuth(store, now_provider=lambda: now)
            service = CentralAIService(CentralServerSettings("sqlite:///:memory:", "test"),
                                       self.store, chatgpt_auth=auth, now_provider=lambda: now)
            self.addAsyncCleanup(service.close)
            return service

        def request(service):
            return service.research_market("prompt", "chatgpt_plan", "gpt-6.1-sol", "high")

        first = new_service()
        quota_error = ChatGPTPlanError(DAILY_USAGE_PAUSE, 429, inference_attempted=True)
        timeout = ChatGPTPlanError("timeout", inference_attempted=True)
        with patch("kiwoom_monitor.infrastructure.news_ai.research_market",
                   side_effect=[quota_error, self.reply, timeout, self.reply]) as transport:
            with self.assertRaises(ChatGPTPlanError):
                await request(first)
            self.assertEqual("2035-01-02", document["analysis_pause_day"])
            self.assertEqual(DAILY_USAGE_PAUSE, first.chatgpt_failure)
            restarted = new_service()
            with self.assertRaises(ChatGPTPlanError):
                await request(restarted)
            self.assertEqual(1, transport.call_count)

            now += timedelta(minutes=2)
            self.assertEqual("", first.chatgpt_failure)
            self.assertEqual("", restarted.chatgpt_auth.status()["pause_code"])
            self.assertIs(self.reply, await request(restarted))
            with self.assertRaises(ChatGPTPlanError):
                await request(restarted)
            self.assertEqual("2035-01-03", document["analysis_pause_day"])
            self.assertEqual("timeout", restarted.chatgpt_auth.status()["pause_code"])
            with self.assertRaises(ChatGPTPlanError):
                await request(restarted)
            self.assertEqual(3, transport.call_count)

            now += timedelta(days=1)
            self.assertEqual("", restarted.chatgpt_failure)
            self.assertIs(self.reply, await request(new_service()))
            self.assertEqual(4, transport.call_count)

        restarted.chatgpt_auth.pause_analysis("not_connected")
        now += timedelta(days=1)
        self.assertEqual("not_connected", restarted.chatgpt_auth.status()["pause_code"])

    async def test_shared_daily_limit_blocks_before_external_request(self):
        from datetime import datetime, timedelta
        from kiwoom_monitor.domain.time_basis import KST
        now = datetime(2035, 1, 2, 23, 59, tzinfo=KST)
        self.service._now = lambda: now
        self.service._daily_limit=1
        with patch("kiwoom_monitor.infrastructure.news_ai.research_market",return_value=self.reply) as call:
            await self.research()
            with self.assertRaises(ValueError): await self.research()
            rows = self.store.load_documents('news_request_usage', owner='2035-01-02')
            self.assertEqual(1, len(rows))
            now += timedelta(minutes=2)
            await self.research()
        self.assertEqual(call.call_count,2)

    async def test_cancelled_reader_still_drains_owned_inference_on_account_change(self):
        import threading
        started,release=threading.Event(),threading.Event()
        def blocked(*args,**kwargs):
            started.set(); release.wait(3); return self.reply
        with patch("kiwoom_monitor.infrastructure.news_ai.research_market",side_effect=blocked):
            reader=asyncio.create_task(self.research())
            await asyncio.to_thread(started.wait,2)
            reader.cancel()
            with self.assertRaises(asyncio.CancelledError): await reader
            drain=asyncio.create_task(self.service.pause_credentials("chatgpt_plan"))
            await asyncio.sleep(.01); self.assertFalse(drain.done())
            release.set(); await drain
        self.assertEqual(len(self.store.load_documents("news_request_usage")),1)

    async def test_session_change_while_waiting_does_not_send_stale_request(self):
        await self.service._execution_lock.acquire()
        with patch("kiwoom_monitor.infrastructure.news_ai.research_market") as call:
            task=asyncio.create_task(self.research())
            for _ in range(50):
                if self.service._accepted["chatgpt_plan"]: break
                await asyncio.sleep(.01)
            self.auth.generation=2; self.service._execution_lock.release()
            with self.assertRaises(ChatGPTPlanError): await task
        call.assert_not_called()
