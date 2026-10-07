from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from kiwoom_monitor.application.account_identity import (
    bind_verified_account_identity,
    prepare_verified_account_identity,
)
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.mock_runtime import (
    MOCK_ACCOUNT_RUNTIME_CONTEXT_VERSION,
    MockAccountBundle,
    MockAccountRuntimeMode,
    MockAutomationRuntimeContext,
    MockCredentialOwner,
)
from kiwoom_monitor.domain.order_contract import AccountEnvironment, OrderSide
from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import ForwardEvaluationRepository
from kiwoom_monitor.infrastructure.kiwoom_rest import KiwoomSettings
from kiwoom_monitor.infrastructure.kiwoom_rest.account_identity import (
    VerifiedAccountIdentity, account_identity_fingerprint,
)


class FakeClient:
    def __init__(self, settings):
        self.environment = settings.environment
        self._settings = settings

    def server_now(self):
        return datetime(2026, 9, 15, 9)

    def get_access_token(self):
        raise AssertionError("WebSocket token must not be requested in bundle tests")

    def request_with_continuation(self, api_id, path, body, **kwargs):
        return {
            "ka10075": {"oso": []}, "ka10076": {"cntr": []},
            "kt00018": {"acnt_evlt_remn_indv_tot": []},
            "kt00001": {"ord_alow_amt": "1000000"},
            "kt00015": {"trst_ovrl_trde_prps_array": []},
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

    async def test_owner_drains_manual_bundle_before_automatic_owner_replaces_it(self):
        identity = self.identity("1234567890")
        self.store.register_credential_profile(
            "kiwoom_mock", "profile-a", datetime.now(timezone.utc).isoformat(),
        )
        prepared = prepare_verified_account_identity(
            identity, self.store, credential_profile_id="profile-a",
        )
        binding = bind_verified_account_identity(
            identity, self.store, credential_profile_id="profile-a",
        )
        settings = self.store.save_account_settings({
            "scope": binding.scope.to_dict(), "active_profile_id": "profile-a",
            "monitor_enabled": True, "mock_order_enabled": True,
        }, expected_revision=0)
        old = MockAccountBundle(
            self.store, settings=KiwoomSettings("fake", "fake", "mock"),
            account_ref=prepared["account_ref"], run_id="manual-run",
            credential_profile_id="profile-a", identity_hmac_key=self.hmac_key,
            order_transport_enabled=True, identity=identity, binding=binding,
        )
        self.bundles.append(old)
        await old.start(); await asyncio.wait_for(old.monitor._queue.join(), 2)
        owner = MockCredentialOwner(self.store, object(), hmac_key=self.hmac_key)
        owner._contexts["profile-a"] = owner._bundles["profile-a"] = old
        owner._revisions["profile-a"] = 1
        owner._settings_revisions["profile-a"] = settings["revision"]
        context = MockAutomationRuntimeContext(
            MOCK_ACCOUNT_RUNTIME_CONTEXT_VERSION, old.account_ref,
            "mock_auto_run_" + "a" * 64, "spec-a", "admission-a", 1, 1,
        )
        new = await owner.switch_execution_mode(
            "profile-a", MockAccountRuntimeMode.AUTOMATIC,
            expected_settings_revision=settings["revision"], credential_revision=1,
            automation_context=context,
        )
        self.bundles.append(new)
        self.assertFalse(old.runtime._active)
        self.assertTrue(new.runtime._active)
        self.assertEqual(MockAccountRuntimeMode.AUTOMATIC, new.runtime_mode)
        self.assertFalse(new.runtime._new_orders_enabled)
        with self.assertRaisesRegex(RuntimeError, "AUTOMATIC_MODE"):
            await new.gateway.submit_limit(
                request_id="manual-during-auto", symbol="005930",
                side=OrderSide.BUY,
                quantity=1, limit_price=1000, expires_seconds=30,
            )
        self.assertIsNotNone(
            ForwardEvaluationRepository(self.store).load_latest_mock_automation_risk(old.account_ref)
        )
        first_risk = ForwardEvaluationRepository(
            self.store
        ).load_latest_mock_automation_risk(old.account_ref)
        await new.close(close_broker=False)
        restarted = MockAccountBundle(
            self.store, settings=new.client._settings,
            account_ref=new.account_ref, run_id=new.run_id,
            credential_profile_id="profile-a", identity_hmac_key=self.hmac_key,
            order_transport_enabled=True, client=new.client, broker=new.broker,
            identity=identity, binding=binding, require_initial_read=True,
            runtime_mode=MockAccountRuntimeMode.AUTOMATIC,
            automation_context=context,
        )
        self.bundles.append(restarted)
        await restarted.start()
        second_risk = ForwardEvaluationRepository(
            self.store
        ).load_latest_mock_automation_risk(old.account_ref)
        self.assertEqual(
            first_risk.reconciliation_revision + 1,
            second_risk.reconciliation_revision,
        )
        owner._bundles.clear(); owner._contexts.clear()
        await owner._probe.close()
