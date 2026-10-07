from __future__ import annotations

import asyncio
import unittest
import threading
import uuid
from unittest.mock import patch

import test_real_credential_owner as support
from kiwoom_monitor.central_server.credential_runtime import CredentialOperationError
from kiwoom_monitor.central_server.real_runtime import RealCredentialOwner


class MarketRoleChangeTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = support.RealCredentialOwnerTests.asyncSetUp
    asyncTearDown = support.RealCredentialOwnerTests.asyncTearDown
    ready = support.RealCredentialOwnerTests.ready
    apply = support.RealCredentialOwnerTests.apply
    active = support.RealCredentialOwnerTests.active
    query = support.RealCredentialOwnerTests.query

    async def admitted(self):
        return await self.active(profile="nas-real-default"), await self.active(key="b1")

    async def change(self, revision=0):
        return await self.owner.change_market_role(self.profile, expected_revision=revision,
                                                   expected_binding_revision=1)

    async def test_role_exchange_preserves_clients_locks_scopes_and_both_cursors(self):
        source, target = await self.admitted()
        clients = source.client, target.client
        locks = tuple(client._request_lock for client in clients)
        bindings = self.store.load_account_bindings()
        pages = await self.query(source), await self.query(target)
        settings = await self.change()
        self.assertEqual(self.profile, self.owner.market_profile_id)
        self.assertEqual("nas-real-default", settings["legacy_real_profile_id"])
        self.assertEqual(1, self.owner.applied_market_role_revision())
        self.assertIs(self.market_broker, target.broker)
        self.assertIs(clients[1], self.market_broker._client)
        self.assertIs(clients[0], source.broker._client)
        self.assertIs(source.client._request_lock, locks[0])
        self.assertIs(target.client._request_lock, locks[1])
        self.assertEqual(bindings, self.store.load_account_bindings())
        for context, page, marker in zip((source, target), pages, ("a1", "b1")):
            response = await self.query(context, batch_id=page["batch_id"], page_index=1,
                                        next_key=page["next_key"])
            self.assertEqual(marker, response["payload"]["credential_marker"])
            self.assertEqual(page["context"], response["context"])
        self.assertIs(self.owner._slots, source.account_queries._read_limiter)
        self.assertIsNone(target.account_queries._read_limiter)

    async def test_same_role_is_noop_and_repeated_switch_has_monotonic_broker_generation(self):
        await self.admitted()
        await self.change()
        before = self.store.load_documents("server_market_profile_settings")
        self.collector.reset_mock()
        await self.change(revision=1)
        self.assertEqual(before, self.store.load_documents("server_market_profile_settings"))
        self.collector.begin_credential_change.assert_not_awaited()
        generations = [self.market_broker._credential_generation]
        await self.owner.change_market_role("nas-real-default", expected_revision=1, expected_binding_revision=1)
        generations.append(self.market_broker._credential_generation)
        await self.change(revision=2)
        generations.append(self.market_broker._credential_generation)
        await self.active(key="b2", revision=1)
        generations.append(self.market_broker._credential_generation)
        self.assertTrue(all(a < b for a, b in zip(generations, generations[1:])), generations)

    async def test_precommit_cas_rejection_restores_previous_role_and_cursor(self):
        source, target = await self.admitted()
        page = await self.query(source)
        with patch.object(self.store, "save_market_profile_settings", side_effect=ValueError(
                "MARKET_PROFILE_SETTINGS_REVISION_CONFLICT")):
            with self.assertRaisesRegex(CredentialOperationError, "MARKET_PROFILE_SETTINGS_REVISION_CONFLICT"):
                await self.change()
        self.assertEqual("nas-real-default", self.owner.market_profile_id)
        self.assertEqual(0, self.store.load_market_profile_settings()["revision"])
        self.assertIs(source, self.owner.bundle("nas-real-default"))
        self.assertIs(target, self.owner.bundle(self.profile))
        response = await self.query(source, batch_id=page["batch_id"], page_index=1, next_key=page["next_key"])
        self.assertEqual("a1", response["payload"]["credential_marker"])

    async def test_postcommit_resume_failure_fences_both_accounts_and_never_rolls_back_role(self):
        source, target = await self.admitted()
        self.collector.end_credential_change.side_effect = RuntimeError("temporary failure")
        with self.assertRaisesRegex(CredentialOperationError, "MARKET_ROLE_RECOVERY_REQUIRED"):
            await self.change()
        self.assertEqual(self.profile, self.store.load_market_profile_settings()["market_profile_id"])
        self.assertIsNone(self.owner.applied_market_role_revision())
        self.assertIsNone(self.owner.bundle("nas-real-default"))
        self.assertIsNone(self.owner.bundle(self.profile))
        for context in (source, target):
            self.assertTrue(context.broker._credential_paused)
            with self.assertRaisesRegex(RuntimeError, "ACCOUNT_QUERY_BUSY"):
                await self.query(context)
        operation = await self.ready(key="b2", revision=1)
        self.assertEqual("PROFILE_RUNTIME_NOT_READY", operation.error_code)

    async def test_unknown_write_outcome_does_not_restore_old_role(self):
        source, target = await self.admitted()
        original = self.store.save_market_profile_settings
        def ambiguous(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError("temporary storage error")
        with patch.object(self.store, "save_market_profile_settings", side_effect=ambiguous):
            with self.assertRaisesRegex(CredentialOperationError, "MARKET_ROLE_RECOVERY_REQUIRED"):
                await self.change()
        self.assertEqual(self.profile, self.store.load_market_profile_settings()["market_profile_id"])
        self.assertTrue(source.broker._credential_paused)
        self.assertTrue(target.broker._credential_paused)

    async def test_cancelled_http_waiter_does_not_abandon_owned_write_or_reconnect(self):
        _, target = await self.admitted()
        entered, unblock = asyncio.Event(), asyncio.Event()
        original = self.owner.validate_market_role
        async def blocked(plan):
            entered.set()
            await unblock.wait()
            return await original(plan)
        with patch.object(self.owner, "validate_market_role", side_effect=blocked):
            task = asyncio.create_task(self.change())
            await asyncio.wait_for(entered.wait(), 3)
            owned = next(iter(self.owner._role_tasks))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertFalse(owned.done())
            unblock.set()
            await asyncio.wait_for(asyncio.shield(owned), 3)
        self.assertIs(self.market_broker, target.broker)
        self.assertEqual(1, self.owner.applied_market_role_revision())

    async def test_restarted_owner_bootstraps_persisted_market_target_and_legacy_separately(self):
        await self.admitted()
        await self.change()
        await self.runtime.close()
        await self.owner.close()
        await self.market_broker.close()
        self.market_client = support.RealFakeClient(support.KiwoomSettings("", "", "real"))
        self.market_broker = support.CentralRestBroker(self.market_client)
        self.owner = RealCredentialOwner(self.store, self.vault, hmac_key=b"x" * 32,
            market_client=self.market_client, market_broker=self.market_broker, market_collector=self.collector)
        await self.owner.start()
        await asyncio.gather(*self.owner._boot_tasks)
        self.assertIs(self.market_broker, self.owner.bundle(self.profile).broker)
        self.assertIsNot(self.market_broker, self.owner.bundle("nas-real-default").broker)
        self.assertEqual("b1", self.market_client._settings.app_key)
        self.assertEqual(1, self.owner.applied_market_role_revision())
        self.assertEqual("a1", (await self.query(self.owner.bundle("nas-real-default")))["payload"]["credential_marker"])

    async def test_role_waits_for_physical_query_before_exchanging_transports(self):
        source, _ = await self.admitted()
        entered, unblock = threading.Event(), threading.Event()
        support.RealFakeClient.query_entered, support.RealFakeClient.query_release = entered, unblock
        query = asyncio.create_task(self.query(source))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 3))
            change = asyncio.create_task(self.change())
            await asyncio.sleep(0.03)
            self.assertFalse(change.done())
            self.assertEqual(0, self.store.load_market_profile_settings()["revision"])
            self.assertIs(source.client, self.market_broker._client)
        finally:
            unblock.set()
        await asyncio.wait_for(query, 3)
        await asyncio.wait_for(change, 3)
        self.assertEqual(1, self.owner.applied_market_role_revision())

    async def test_unknown_precommit_value_error_does_not_leak_upstream_text(self):
        await self.admitted()
        private_text = uuid.uuid4().hex
        self.collector.begin_credential_change.side_effect = ValueError(private_text)
        with self.assertRaises(CredentialOperationError) as raised:
            await self.change()
        self.assertEqual("MARKET_ROLE_CHANGE_FAILED", raised.exception.code)
        self.assertNotIn(private_text, str(raised.exception))
        self.assertEqual(0, self.store.load_market_profile_settings()["revision"])

    async def test_first_account_frame_can_resolve_committed_context_before_resume_returns(self):
        observed = []
        async def first_frame():
            context = self.owner.bundle(self.owner.market_profile_id)
            self.assertIsNotNone(context)
            observed.append(context.binding.scope)
        self.collector.end_credential_change.side_effect = first_frame
        source, target = await self.admitted()
        self.assertEqual([source.binding.scope], observed)
        await self.change()
        self.assertEqual([source.binding.scope, target.binding.scope], observed)

    async def test_precommit_restore_failure_clears_admission_and_applied_credential_revision(self):
        source, target = await self.admitted()
        with patch.object(self.store, "save_market_profile_settings", side_effect=ValueError(
                "MARKET_PROFILE_SETTINGS_REVISION_CONFLICT")), patch.object(
                self.market_broker, "end_credential_change", side_effect=RuntimeError("temporary restore failure")):
            with self.assertRaisesRegex(CredentialOperationError, "MARKET_ROLE_RECOVERY_REQUIRED"):
                await self.change()
        self.assertEqual(0, self.store.load_market_profile_settings()["revision"])
        for candidate_id, context in (("nas-real-default", source), (self.profile, target)):
            self.assertIsNone(self.owner.bundle(candidate_id))
            self.assertIsNone(self.owner.active_revision(candidate_id))
            self.assertTrue(context.account_queries._credential_paused)
            self.assertTrue(context.broker._credential_paused)

    async def test_aborted_credential_change_resolves_previous_scope_on_first_resumed_frame(self):
        source = await self.active(profile="nas-real-default")
        operation = await self.ready(key="a2", profile="nas-real-default", revision=1)
        async def first_frame():
            self.assertIs(source, self.owner.bundle("nas-real-default"))
        self.collector.end_credential_change.side_effect = first_frame
        configuration = self.store.load_account_settings(source.binding.scope.to_dict())
        self.store.save_account_settings({**{key: value for key, value in configuration.items() if key != "revision"},
                                          "monitor_enabled": False}, expected_revision=configuration["revision"])
        await self.runtime.apply(operation.operation_id, 1, operation.candidate.account_ref)
        await asyncio.wait_for(asyncio.shield(operation.task), 3)
        self.assertEqual("FAILED", operation.state)
        self.assertIs(source, self.owner.bundle("nas-real-default"))
        self.assertEqual(1, self.owner.active_revision("nas-real-default"))


if __name__ == "__main__":
    unittest.main()
