from __future__ import annotations

import asyncio
import threading
import unittest
from unittest.mock import patch

import test_real_credential_owner as support
from kiwoom_monitor.central_server.credential_runtime import CredentialOperationError
from kiwoom_monitor.central_server.rest_broker import ACCOUNT_RECOVERY_ENDPOINTS


_physical_request = support.RealFakeClient.request_with_continuation


def account_request(client, api_id, path, body, **kwargs):
    result = _physical_request(client, api_id, path, body, **kwargs)
    if api_id not in ACCOUNT_RECOVERY_ENDPOINTS:
        return result
    client.account_calls.append((api_id, dict(body)))
    return {"ka10075": {"oso": []}, "ka10076": {"cntr": []},
            "kt00018": {"acnt_evlt_remn_indv_tot": [{"stk_cd": "005930",
                "rmnd_qty": "10" if client._settings.app_key.startswith("a") else "20"}]},
            "kt00001": {"ord_alow_amt": "500000"}}[api_id], False, ""


class AccountClient(support.RealFakeClient):
    def __init__(self, settings):
        super().__init__(settings)
        self.account_calls = []

    def request_with_continuation(self, api_id, path, body, **kwargs):
        return account_request(self, api_id, path, body, **kwargs)


class RealAccountReadsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        with patch.object(support, "RealFakeClient", AccountClient):
            await support.RealCredentialOwnerTests.asyncSetUp(self)
    asyncTearDown = support.RealCredentialOwnerTests.asyncTearDown
    ready = support.RealCredentialOwnerTests.ready
    apply = support.RealCredentialOwnerTests.apply
    active = support.RealCredentialOwnerTests.active

    async def admitted(self):
        return await self.active(profile="nas-real-default"), await self.active(key="b1")

    async def read(self, profile=None, revision=1):
        return await self.owner.read_account(profile or self.profile, expected_binding_revision=revision)

    async def test_reads_use_existing_market_and_account_brokers_and_separate_scopes(self):
        with patch("kiwoom_monitor.infrastructure.kiwoom_rest.KiwoomRestClient", AccountClient):
            _, target = await self.admitted()
            source_result, target_result = await self.read("nas-real-default"), await self.read()
            self.assertNotEqual(source_result.account.account_ref, target_result.account.account_ref)
            self.assertEqual({"005930": 10}, source_result.account.positions)
            self.assertEqual({"005930": 20}, target_result.account.positions)
            self.assertEqual(500000, target_result.account.available_cash_won)
            self.assertEqual(5, len(self.market_client.account_calls))
            self.assertEqual(5, len(target.client.account_calls))

    async def test_stale_binding_and_keyless_read_are_rejected_before_tr(self):
        with self.assertRaisesRegex(CredentialOperationError, "PROFILE_RUNTIME_NOT_READY"):
            await self.read()
        await self.admitted()
        for revision in (2, True):
            with self.assertRaisesRegex(CredentialOperationError, "ACCOUNT_CONTEXT_MISMATCH"):
                await self.read(revision=revision)

    async def test_cancelled_read_waiter_keeps_physical_read_owned_during_key_rotation(self):
        with patch("kiwoom_monitor.infrastructure.kiwoom_rest.KiwoomRestClient", AccountClient):
            await self.admitted()
            replacement = await self.ready(key="b2", revision=1)
            entered, unblock = threading.Event(), threading.Event()
            support.RealFakeClient.query_entered, support.RealFakeClient.query_release = entered, unblock
            task = asyncio.create_task(self.read())
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                await self.runtime.apply(replacement.operation_id, 1, replacement.candidate.account_ref)
                await asyncio.sleep(0.03)
                self.assertFalse(replacement.task.done())
                self.assertEqual(1, self.vault.load("kiwoom_real", self.profile).revision)
            finally:
                unblock.set()
            await asyncio.wait_for(asyncio.shield(replacement.task), 5)
            self.assertEqual("ACTIVE", replacement.state)
            self.assertEqual(0, len(self.owner.bundle(self.profile).account_reads))

    async def test_single_read_admission_and_role_exchange_wait_for_complete_account_cycle(self):
        with patch("kiwoom_monitor.infrastructure.kiwoom_rest.KiwoomRestClient", AccountClient):
            _, target = await self.admitted()
            entered, unblock = threading.Event(), threading.Event()
            support.RealFakeClient.query_entered, support.RealFakeClient.query_release = entered, unblock
            task = asyncio.create_task(self.read())
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                with self.assertRaisesRegex(CredentialOperationError, "PROFILE_BUSY"):
                    await self.read()
                change = asyncio.create_task(self.owner.change_market_role(self.profile,
                    expected_revision=0, expected_binding_revision=1))
                await asyncio.sleep(0.03)
                self.assertFalse(change.done())
                self.assertEqual(0, self.store.load_market_profile_settings()["revision"])
            finally:
                unblock.set()
            await asyncio.wait_for(task, 5)
            await asyncio.wait_for(change, 5)
            self.assertIs(self.market_broker, target.broker)
            result = await self.read()
            self.assertEqual(target.binding.scope.account_ref, result.account.account_ref)

    async def test_generic_api_cannot_expose_new_unscoped_account_recovery_queries(self):
        from fastapi.testclient import TestClient
        from kiwoom_monitor.central_server.app import create_app
        from kiwoom_monitor.central_server.config import CentralServerSettings
        app = create_app(CentralServerSettings(f"sqlite:///{self.root / 'api.sqlite'}", "token",
            autonomous_top20_enabled=False, market_event_collection_enabled=False))
        with TestClient(app) as client:
            for api in ACCOUNT_RECOVERY_ENDPOINTS:
                response = client.post("/api/v1/kiwoom/query", headers={"Authorization": "Bearer token"},
                    json={"api_id": api, "path": "/api/dostk/acnt", "body": {}})
                self.assertEqual(400, response.status_code)
                self.assertEqual("ACCOUNT_QUERY_SCOPE_REQUIRED", response.json()["detail"])

    async def test_close_waits_for_complete_read_and_rejects_new_reads(self):
        await self.admitted()
        entered, unblock = threading.Event(), threading.Event()
        support.RealFakeClient.query_entered, support.RealFakeClient.query_release = entered, unblock
        read = asyncio.create_task(self.read())
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 3))
            closing = asyncio.create_task(self.owner.close())
            await asyncio.sleep(0.03)
            self.assertFalse(closing.done())
            with self.assertRaisesRegex(CredentialOperationError, "PROFILE_RUNTIME_NOT_READY"):
                await self.read()
        finally:
            unblock.set()
        self.assertEqual({"005930": 20}, (await asyncio.wait_for(read, 5)).account.positions)
        await asyncio.wait_for(closing, 5)
        self.assertEqual((), self.owner.account_bindings())

    async def test_read_failure_releases_slot_without_disabling_admitted_account(self):
        _, target = await self.admitted()
        original = target.client.request_with_continuation
        def missing_deposit(api, path, body, **kwargs):
            result = original(api, path, body, **kwargs)
            return ({}, False, "") if api == "kt00001" else result
        with patch.object(target.client, "request_with_continuation", side_effect=missing_deposit):
            with self.assertRaisesRegex(ValueError, "missing ord_alow_amt"):
                await self.read()
        self.assertIs(target, self.owner.bundle(self.profile))
        self.assertEqual(0, len(target.account_reads))
        self.assertEqual({"005930": 20}, (await self.read()).account.positions)


if __name__ == "__main__":
    unittest.main()
