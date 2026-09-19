from __future__ import annotations

import asyncio
import threading
import unittest
import uuid
from dataclasses import replace
from unittest.mock import patch

import test_real_credential_owner as support
from kiwoom_monitor.central_server.credential_runtime import CredentialOperationError


class MarketRoleBarrierTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = support.RealCredentialOwnerTests.asyncSetUp
    asyncTearDown = support.RealCredentialOwnerTests.asyncTearDown
    ready = support.RealCredentialOwnerTests.ready
    apply = support.RealCredentialOwnerTests.apply
    active = support.RealCredentialOwnerTests.active

    async def admitted(self):
        source = await self.active(profile="nas-real-default")
        target = await self.active(key="b1")
        return source, target

    def reservation(self, **kwargs):
        return self.owner.market_role_change(self.profile, expected_revision=kwargs.get("revision", 0),
            expected_binding_revision=kwargs.get("binding_revision", 1))

    async def test_reservation_is_read_only_and_excludes_credential_and_role_candidates(self):
        source, target = await self.admitted()
        snapshot = self.store.load_market_profile_settings()
        bindings = self.store.load_account_bindings()
        self.collector.reset_mock()
        async with self.reservation() as plan:
            self.assertIs(source, plan.source)
            self.assertIs(target, plan.target)
            await self.owner.validate_market_role(plan)
            self.assertNotIn("token", repr(plan))
            self.assertNotIn("secret", repr(plan))
            operation = await self.ready(key="b2", revision=1)
            self.assertEqual("FAILED", operation.state)
            self.assertEqual("PROFILE_BUSY", operation.error_code)
            with self.assertRaisesRegex(CredentialOperationError, "PROFILE_BUSY"):
                async with self.reservation():
                    self.fail("overlapping role reservation admitted")
            self.assertIs(source, self.owner.bundle("nas-real-default"))
            self.assertIs(target, self.owner.bundle(self.profile))
            self.assertFalse(self.market_broker._credential_paused)
            self.collector.begin_credential_change.assert_not_awaited()
        self.assertEqual(snapshot, self.store.load_market_profile_settings())
        self.assertEqual(bindings, self.store.load_account_bindings())
        await self.apply(await self.ready(key="b2", revision=1))

    async def test_ready_credential_blocks_role_until_cancelled(self):
        await self.admitted()
        operation = await self.ready(key="b2", revision=1)
        with self.assertRaisesRegex(CredentialOperationError, "PROFILE_BUSY"):
            async with self.reservation():
                self.fail("READY credential must retain its reservation")
        await self.runtime.cancel(operation.operation_id)
        async with self.reservation() as plan:
            await self.owner.validate_market_role(plan)

    async def test_invalid_and_stale_inputs_release_reservation(self):
        await self.admitted()
        for options, error in [({"revision": True}, "MARKET_PROFILE_SETTINGS_INVALID"),
                               ({"binding_revision": False}, "MARKET_PROFILE_SETTINGS_INVALID"),
                               ({"revision": 1}, "MARKET_PROFILE_SETTINGS_REVISION_CONFLICT"),
                               ({"binding_revision": 2}, "ACCOUNT_CONTEXT_MISMATCH")]:
            with self.subTest(options=options), self.assertRaisesRegex(CredentialOperationError, error):
                async with self.reservation(**options):
                    self.fail("invalid snapshot admitted")
            self.assertIsNone(self.owner._market_role_reservation)
        async with self.reservation() as plan:
            await self.owner.validate_market_role(plan)
        with self.assertRaisesRegex(CredentialOperationError, "PROFILE_BUSY"):
            await self.owner.validate_market_role(plan)

    async def test_unadmitted_or_vault_revision_not_applied_cannot_be_reserved(self):
        with self.assertRaisesRegex(CredentialOperationError, "PROFILE_RUNTIME_NOT_READY"):
            async with self.reservation():
                self.fail("keyless runtime admitted")
        await self.admitted()
        self.vault.save("kiwoom_real", self.profile, {"app_key": "b2", "secret_key": uuid.uuid4().hex},
                        expected_revision=1)
        with self.assertRaisesRegex(CredentialOperationError, "PROFILE_RUNTIME_NOT_READY"):
            async with self.reservation():
                self.fail("unapplied vault revision admitted")
        self.assertIsNone(self.owner._market_role_reservation)

    async def test_precommit_revalidation_rejects_binding_and_role_drift(self):
        await self.admitted()
        original = self.store.load_account_bindings
        async with self.reservation() as plan:
            def drifted():
                return [{**item, "binding_revision": item["binding_revision"] + 1}
                        if item["credential_profile_id"] == self.profile else item for item in original()]
            with patch.object(self.store, "load_account_bindings", side_effect=drifted):
                with self.assertRaisesRegex(CredentialOperationError, "ACCOUNT_CONTEXT_MISMATCH"):
                    await self.owner.validate_market_role(plan)
            self.store.save_market_profile_settings({"market_profile_id": self.profile,
                "expected_binding_revision": 1}, expected_revision=0)
            with self.assertRaisesRegex(CredentialOperationError, "MARKET_PROFILE_SETTINGS_REVISION_CONFLICT"):
                await self.owner.validate_market_role(plan)
        with self.assertRaisesRegex(CredentialOperationError, "MARKET_ROLE_RUNTIME_NOT_READY"):
            async with self.reservation(revision=1):
                self.fail("persisted intent is not live application")

    async def test_cancelled_preflight_and_body_exception_release_reservation(self):
        await self.admitted()
        entered, unblock = asyncio.Event(), asyncio.Event()
        original = self.owner._check_market_role
        async def blocked(*args):
            entered.set()
            await unblock.wait()
            return await original(*args)
        async def reserve():
            async with self.reservation():
                self.fail("cancelled preflight should not yield")
        with patch.object(self.owner, "_check_market_role", side_effect=blocked):
            task = asyncio.create_task(reserve())
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(self.owner._market_role_idle.is_set())
        with self.assertRaisesRegex(RuntimeError, "temporary failure"):
            async with self.reservation():
                raise RuntimeError("temporary failure")
        async with self.reservation() as plan:
            await self.owner.validate_market_role(plan)

    async def test_close_waits_for_role_boundary_and_rejects_new_work(self):
        await self.admitted()
        async with self.reservation() as plan:
            task = asyncio.create_task(self.owner.close())
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            self.assertIsNotNone(self.owner.bundle(self.profile))
            with self.assertRaisesRegex(CredentialOperationError, "PROFILE_BUSY"):
                await self.owner.validate_market_role(plan)
        await asyncio.wait_for(task, 3)
        self.assertEqual((), self.owner.account_bindings())

    async def test_cancelled_role_body_releases_for_subsequent_credentials(self):
        await self.admitted()
        entered = asyncio.Event()
        async def reserve():
            async with self.reservation():
                entered.set()
                await asyncio.Event().wait()
        task = asyncio.create_task(reserve())
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(self.owner._market_role_idle.is_set())
        operation = await self.apply(await self.ready(key="b2", revision=1))
        self.assertEqual("ACTIVE", operation.state)

    async def test_same_binding_replacement_context_does_not_validate_old_plan(self):
        _, target = await self.admitted()
        async with self.reservation() as plan:
            with patch.dict(self.owner._bundles, {self.profile: replace(target)}):
                with self.assertRaisesRegex(CredentialOperationError, "ACCOUNT_CONTEXT_MISMATCH"):
                    await self.owner.validate_market_role(plan)
            await self.owner.validate_market_role(plan)

    async def test_disabled_policy_read_is_reserved_before_await(self):
        await self.admitted()
        entered, unblock = threading.Event(), threading.Event()
        original = self.store.load_market_profile_settings
        def blocked():
            entered.set()
            if not unblock.wait(3):
                raise RuntimeError("test did not release policy read")
            return original()
        record = self.vault.load("kiwoom_real", self.profile)
        with patch.object(self.store, "load_market_profile_settings", side_effect=blocked):
            task = asyncio.create_task(self.owner.prepare(self.profile, record, {}, True))
            candidate = None
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                with self.assertRaisesRegex(CredentialOperationError, "PROFILE_BUSY"):
                    async with self.reservation():
                        self.fail("credential policy read was not reserved")
            finally:
                unblock.set()
                candidate = await asyncio.wait_for(task, 3)
                self.owner.release(self.profile, candidate)
        async with self.reservation() as plan:
            await self.owner.validate_market_role(plan)

    async def test_archived_profile_is_rejected_before_transport_drain(self):
        await self.admitted()
        self.collector.reset_mock()
        with self.store._connection() as connection:
            connection.execute("UPDATE central_credential_profiles SET lifecycle_state='archived' WHERE profile_id=?",
                               (self.profile,))
        with self.assertRaisesRegex(CredentialOperationError, "MARKET_PROFILE_UNAVAILABLE"):
            async with self.reservation():
                self.fail("archived profile admitted")
        self.collector.begin_credential_change.assert_not_awaited()
        self.assertIsNone(self.owner._market_role_reservation)

    async def test_close_during_preflight_does_not_yield_a_new_role_transaction(self):
        await self.admitted()
        entered, unblock = asyncio.Event(), asyncio.Event()
        original = self.owner._check_market_role
        async def blocked(*args):
            plan = await original(*args)
            entered.set()
            await unblock.wait()
            return plan
        async def reserve():
            async with self.reservation():
                self.fail("role transaction yielded after close began")
        with patch.object(self.owner, "_check_market_role", side_effect=blocked):
            task = asyncio.create_task(reserve())
            await asyncio.wait_for(entered.wait(), 3)
            closing = asyncio.create_task(self.owner.close())
            await asyncio.sleep(0)
            self.assertFalse(closing.done())
            unblock.set()
            with self.assertRaisesRegex(CredentialOperationError, "PROFILE_BUSY"):
                await task
            await asyncio.wait_for(closing, 3)


if __name__ == "__main__":
    unittest.main()
