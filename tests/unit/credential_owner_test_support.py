"""Shared fakes and fixtures for credential-owner contract tests."""
from __future__ import annotations

import asyncio
import tempfile
import threading
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import AsyncMock, patch

from kiwoom_monitor.application.news_analysis import NewsAssessment
from kiwoom_monitor.central_server.credential_runtime import CredentialRuntime
from kiwoom_monitor.central_server.credential_store import CredentialStore
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.infrastructure.dart_disclosures import DartDisclosureClient
from kiwoom_monitor.infrastructure.news_ai import AINewsAnalysis, AIRequestUsage
from kiwoom_monitor.central_server.real_runtime import RealCredentialOwner
from kiwoom_monitor.central_server.rest_broker import CentralRestBroker
from kiwoom_monitor.infrastructure.kiwoom_rest import KiwoomSettings
from kiwoom_monitor.infrastructure.kiwoom_rest.client import (
    KiwoomRestClient,
    PreparedKiwoomCredentials,
)
from kiwoom_monitor.infrastructure.naver_news import (
    NaverNewsClient, NaverNewsPage, StockNewsItem,
)


class FakeAI:
    def __init__(self):
        self.calls = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.block_first = False
        self.failure = None

    def __call__(self, settings, stock_name, articles):
        self.calls.append((settings.provider, settings.api_key, settings.model, articles))
        if len(self.calls) == 1 and self.block_first:
            self.entered.set()
            if not self.release.wait(5):
                raise RuntimeError("fake gate timed out")
        if self.failure is not None:
            error, self.failure = self.failure, None
            raise error
        return tuple(AINewsAnalysis("가짜 요약", "긍정", 80, "가짜 근거") for _ in articles), AIRequestUsage(3, 2, 5)


def _fake_news_item(identity: str, minute: int = 0) -> StockNewsItem:
    return StockNewsItem(
        "삼성전자 공급계약 체결", "삼성전자와 500억원 공급계약 체결",
        f"https://naver/{identity}", f"https://origin/{identity}",
        datetime(2026, 9, 12, 1, minute, tzinfo=timezone.utc),
        NewsAssessment(True, "수주·계약", "긍정", "", 90, 80),
    )


class FakeNaver(NaverNewsClient):
    def __init__(self, credentials):
        super().__init__(credentials)
        self.marker = credentials.client_id
        self.calls = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.block = False
        self.fail = False
        self.pages = 1

    def search_page(self, query, *, display=100, start=1, request_claim=None):
        if request_claim is not None and not request_claim():
            raise RuntimeError("budget exhausted")
        self.calls.append((query, start, display))
        if display != 1 and self.block:
            self.entered.set()
            if not self.release.wait(3):
                raise RuntimeError("fake gate timed out")
        if self.fail or self.marker == "invalid":
            raise RuntimeError("fake-sensitive-secret")
        item = _fake_news_item(f"{self.marker}-{query}-{start}")
        return NaverNewsPage((item,), 101 if self.pages == 2 else 1, start,
                             100 if self.pages == 2 and start == 1 else 1)

    def search(self, name, *, since=None, request_claim=None):
        return self.search_page(name, request_claim=request_claim).items


class FakeDart(DartDisclosureClient):
    def __init__(self, key, cache_path):
        super().__init__(key, cache_path)
        self.calls = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.block = False

    def _json(self, url):
        query = parse_qs(urlsplit(url).query)
        self.calls.append(query)
        if "corp_code" in query and self.block:
            self.entered.set()
            if not self.release.wait(3):
                raise RuntimeError("fake gate timeout")
        if self._api_key in {"invalid", "quota"}:
            return {"status": "010" if self._api_key == "invalid" else "020", "message": self._api_key}
        return {"status": "000", "list": [{"rcept_no": "2026091500" + self._api_key,
            "report_nm": "삼성전자 공급계약 체결", "flr_nm": "삼성전자", "rcept_dt": "20260915"}]}


class FakeClient(KiwoomRestClient):
    fail_read = False
    read_entered = None
    read_release = None
    validate_entered = None
    validate_release = None
    counter_lock = threading.Lock()
    active_reads = 0
    max_reads = 0

    def prepare_credential_token(self, settings):
        with self._request_lock:
            self._ensure_credential_accepting()
            if settings.app_key == "bad":
                raise RuntimeError("fake-sensitive-error")
            self._last_request_at = time.monotonic()
            return PreparedKiwoomCredentials(settings, "token:" + settings.app_key,
                datetime.now(timezone.utc) + timedelta(hours=1), {}, self._credential_generation)

    def verify_prepared_credentials(self, prepared):
        with self._request_lock:
            if self.validate_entered is not None:
                self.validate_entered.set()
                self.validate_release.wait(timeout=5)
            self._last_request_at = time.monotonic()
            return PreparedKiwoomCredentials(prepared.settings, prepared.token, prepared.expires_at,
                {"acctNo": ("1234567890" if prepared.settings.app_key.startswith("a") else
                            "3456789012" if prepared.settings.app_key.startswith("c") else
                            "2345678901")}, self._credential_generation, True)

    def request_with_continuation(self, api_id, path, body, **kwargs):
        with self._request_lock:
            self._ensure_credential_accepting()
            if api_id == "ka10075" and self.read_entered is not None:
                with FakeClient.counter_lock:
                    FakeClient.active_reads += 1
                    FakeClient.max_reads = max(FakeClient.max_reads, FakeClient.active_reads)
                try:
                    self.read_entered.set()
                    self.read_release.wait(timeout=5)
                finally:
                    with FakeClient.counter_lock:
                        FakeClient.active_reads -= 1
            if self.fail_read:
                raise RuntimeError("fake-sensitive-read-error")
            return {"ka10075": {"oso": []}, "ka10076": {"cntr": []},
                "kt00018": {"acnt_evlt_remn_indv_tot": []},
                "kt00001": {"ord_alow_amt": "1000000"}}[api_id], False, ""


class RealFakeClient(FakeClient):
    query_entered = query_release = None

    def request_with_continuation(self, api_id, path, body, **kwargs):
        with self._request_lock:
            self._ensure_credential_accepting()
            if self.query_entered is not None:
                self.query_entered.set()
                self.query_release.wait(timeout=5)
            return {"credential_marker": self._settings.app_key}, bool(body.get("paged")), "cursor"


class RealCredentialOwnerTestSupport(unittest.IsolatedAsyncioTestCase):
    """Reusable real-owner fixture, independent of any test module's class."""

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = SQLiteQueryStore(self.root / "central.sqlite")
        self.store.initialize()
        self.vault = CredentialStore(self.root / "secrets", self.store)
        self.client_patch = patch(
            "kiwoom_monitor.infrastructure.kiwoom_rest.KiwoomRestClient", RealFakeClient
        )
        self.client_patch.start()
        self.realtime_patch = patch(
            "kiwoom_monitor.central_server.mock_account_monitor.RealAccountRealtimeCollector",
            side_effect=lambda **kwargs: AsyncMock(ready=False, error_code=None),
        )
        self.realtime_patch.start()
        self.market_client = RealFakeClient(KiwoomSettings("", "", "real"))
        self.market_broker = CentralRestBroker(self.market_client)
        self.collector = AsyncMock()
        self.owner = RealCredentialOwner(self.store, self.vault, hmac_key=b"x" * 32,
            market_client=self.market_client, market_broker=self.market_broker,
            market_collector=self.collector)
        self.runtime = CredentialRuntime(self.vault, self.store)
        self.runtime.register("kiwoom_real", self.owner.hooks())
        self.profile = (await self.runtime.create_profile(
            "kiwoom_real", str(uuid.uuid4()), "one"
        ))["profile_id"]

    async def asyncTearDown(self):
        if RealFakeClient.query_release is not None:
            RealFakeClient.query_release.set()
        await self.runtime.close()
        await self.owner.close()
        await self.market_broker.close()
        self.vault.close()
        self.store.close()
        self.client_patch.stop()
        self.realtime_patch.stop()
        self.temp.cleanup()
        RealFakeClient.query_entered = RealFakeClient.query_release = None

    async def ready(self, key="a1", *, profile=None, revision=0, disabled=False):
        document = await self.runtime.prepare("kiwoom_real", profile or self.profile,
            str(uuid.uuid4()), revision,
            {} if disabled else {"app_key": key, "secret_key": uuid.uuid4().hex},
            disabled=disabled)
        operation = self.runtime._operations[document["operation_id"]]
        await asyncio.wait_for(asyncio.shield(operation.task), 5)
        return operation

    async def apply(self, operation):
        self.assertEqual("READY", operation.state, operation.error_code)
        await self.runtime.apply(operation.operation_id, operation.expected_revision,
            operation.candidate.account_ref)
        await asyncio.wait_for(asyncio.shield(operation.task), 5)
        return operation

    async def active(self, **kwargs):
        operation = await self.apply(await self.ready(**kwargs))
        self.assertEqual("ACTIVE", operation.state, operation.error_code)
        return self.owner.bundle(kwargs.get("profile", self.profile))

    async def query(self, context, **kwargs):
        return await context.account_queries.query(api_id="kt00007", path="/api/dostk/acnt",
            body={"paged": True}, **kwargs)
