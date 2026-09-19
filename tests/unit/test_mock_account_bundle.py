from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from kiwoom_monitor.application.account_identity import prepare_verified_account_identity
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.mock_runtime import MockAccountBundle
from kiwoom_monitor.domain.order_contract import AccountEnvironment
from kiwoom_monitor.infrastructure.kiwoom_rest import KiwoomSettings
from kiwoom_monitor.infrastructure.kiwoom_rest.account_identity import (
    VerifiedAccountIdentity, account_identity_fingerprint,
)


class FakeClient:
    def __init__(self, settings):
        self.environment = settings.environment

    def server_now(self):
        return datetime(2026, 9, 15, 9)

    def get_access_token(self):
        raise AssertionError("WebSocket token must not be requested in bundle tests")

    def request_with_continuation(self, api_id, path, body, **kwargs):
        return {
            "ka10075": {"oso": []}, "ka10076": {"cntr": []},
            "kt00018": {"acnt_evlt_remn_indv_tot": []},
            "kt00001": {"ord_alow_amt": "1000000"},
        }[api_id], False, ""


class MockAccountBundleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = SQLiteQueryStore(Path(":memory:")); self.store.initialize()
        self.bundles = []
        self.client_patch = patch(
            "kiwoom_monitor.infrastructure.kiwoom_rest.KiwoomRestClient", FakeClient,
        )
        self.client_patch.start()
        self.ws_patch = patch(
            "kiwoom_monitor.central_server.mock_runtime.MockAccountRealtimeCollector.start",
            new=AsyncMock(),
        )
        self.ws_patch.start()
        self.hmac_key = b"x" * 32

    async def asyncTearDown(self):
        for bundle in self.bundles:
            await bundle.close()
        self.ws_patch.stop(); self.client_patch.stop(); self.store.close()

    def identity(self, account_number):
        return VerifiedAccountIdentity(
            "kiwoom", AccountEnvironment.MOCK,
            account_identity_fingerprint(account_number, AccountEnvironment.MOCK, self.hmac_key),
            datetime.now(timezone.utc),
        )

    def bundle(self, identity=None, *, account_ref=None):
        if identity is not None and account_ref is None:
            account_ref = prepare_verified_account_identity(
                identity, self.store, credential_profile_id="nas-mock-default",
            )["account_ref"]
        bundle = MockAccountBundle(
            self.store, settings=KiwoomSettings("fake", "fake", "mock"),
            account_ref=account_ref or "account-1", run_id="run-1",
            identity_hmac_key=self.hmac_key if identity else None,
        )
        self.bundles.append(bundle)
        return bundle

    async def test_fixed_identity_resolver_rejects_other_account_and_closed_epoch(self):
        first = self.identity("1234567890"); second = self.identity("2345678901")
        one = self.bundle(first); two = self.bundle(second)
        with patch(
            "kiwoom_monitor.central_server.mock_runtime.KiwoomAccountIdentityReader.verify",
            new=AsyncMock(side_effect=[first, second]),
        ):
            await one.start(); await two.start()
        self.assertEqual(one.account_ref, one._resolve_scope("1234567890").account_ref)
        self.assertIsNone(one._resolve_scope("2345678901"))
        self.assertEqual(two.account_ref, two._resolve_scope("2345678901").account_ref)
        self.assertIsNot(one.client, two.client)
        self.assertIsNot(one.broker, two.broker)
        await one.close()
        self.assertIsNone(one._resolve_scope("1234567890"))
        self.assertIsNotNone(two._resolve_scope("2345678901"))

    async def test_mismatch_does_not_publish_binding_or_claim_lease(self):
        bundle = self.bundle(self.identity("1234567890"), account_ref="wrong-account")
        with patch(
            "kiwoom_monitor.central_server.mock_runtime.KiwoomAccountIdentityReader.verify",
            new=AsyncMock(return_value=self.identity("1234567890")),
        ):
            with self.assertRaisesRegex(RuntimeError, "ACCOUNT_CONTEXT_MISMATCH"):
                await bundle.start()
        self.assertEqual([], self.store.load_account_bindings())
        self.assertIsNone(bundle.monitor._task)
        self.assertIsNone(bundle.binding)

    async def test_cancelled_start_waiter_does_not_abandon_identity_work(self):
        identity = self.identity("1234567890"); bundle = self.bundle(identity)
        entered = asyncio.Event(); release = asyncio.Event()
        async def verify(_reader):
            entered.set(); await release.wait(); return identity
        with patch(
            "kiwoom_monitor.central_server.mock_runtime.KiwoomAccountIdentityReader.verify",
            new=verify,
        ):
            waiter = asyncio.create_task(bundle.start())
            await asyncio.wait_for(entered.wait(), 2)
            waiter.cancel()
            with self.assertRaises(asyncio.CancelledError): await waiter
            closer = asyncio.create_task(bundle.close())
            await asyncio.sleep(0)
            self.assertFalse(closer.done())
            release.set(); await asyncio.wait_for(closer, 2)
        self.assertIsNone(bundle.monitor._task)
        with self.assertRaisesRegex(RuntimeError, "MOCK_BUNDLE_CLOSED"):
            await bundle.start()

    async def test_close_waits_for_gateway_before_socket_monitor_and_lease_release(self):
        bundle = self.bundle(); await bundle.start()
        await asyncio.wait_for(bundle.monitor._queue.join(), 2)
        entered = asyncio.Event(); release = asyncio.Event()
        async def close_gateway(): entered.set(); await release.wait()
        bundle.gateway = SimpleNamespace(close=close_gateway)
        waiter = asyncio.create_task(bundle.close())
        await asyncio.wait_for(entered.wait(), 2)
        self.assertIsNotNone(bundle.monitor._task)
        self.assertTrue(bundle.runtime._active)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError): await waiter
        self.assertFalse(bundle._close_task.done())
        release.set(); await asyncio.wait_for(bundle.close(), 2)
        self.assertIsNone(bundle.monitor._task)
        self.assertFalse(bundle.runtime._active)

    async def test_new_bundle_defaults_to_no_order_transport(self):
        bundle = self.bundle()
        self.assertIsNone(bundle.gateway)
        self.assertIsNone(bundle.runtime._lifecycle._transport)

    async def test_real_settings_are_rejected_before_client_creation(self):
        with self.assertRaisesRegex(ValueError, "MOCK_ENVIRONMENT_REQUIRED"):
            MockAccountBundle(
                self.store, settings=KiwoomSettings("fake", "fake", "real"),
                account_ref="account", run_id="run",
            )
