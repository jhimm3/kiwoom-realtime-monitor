from __future__ import annotations

import asyncio
import json
import threading
import unittest
from dataclasses import replace
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import patch

from kiwoom_monitor.central_server.credential_runtime import CredentialOperationError
from kiwoom_monitor.central_server.database import _save_real_account_recovery
from kiwoom_monitor.central_server.market_observations import as_kst
from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope
import test_real_account_reads as support


class RealAccountMonitorTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = support.RealAccountReadsTests.asyncSetUp
    asyncTearDown = support.RealAccountReadsTests.asyncTearDown
    ready = support.RealAccountReadsTests.ready
    apply = support.RealAccountReadsTests.apply
    active = support.RealAccountReadsTests.active
    admitted = support.RealAccountReadsTests.admitted

    async def until(self, predicate):
        async with asyncio.timeout(3):
            while not predicate():
                await asyncio.sleep(0.01)

    def settings(self, context):
        return self.store.load_account_settings(context.binding.scope.to_dict())

    def documents(self, context):
        return self.store.load_documents("real_account_recovery",
            "kiwoom:real:" + context.binding.scope.account_ref)

    async def toggle(self, context, enabled, revision=None):
        current = self.settings(context)
        return await self.owner.update_settings(current["scope"], {
            "active_profile_id": current["active_profile_id"], "monitor_enabled": enabled,
            "mock_order_enabled": False}, current["revision"] if revision is None else revision)

    async def test_automatic_reads_persist_separate_scopes_without_mock_ledger_or_market_restart(self):
        self.owner._account_poll_interval = 0.02
        source, target = await self.admitted()
        await self.until(lambda: self.documents(source) and self.documents(target))
        for context, quantity in ((source, 10), (target, 20)):
            document = self.documents(context)[0]["document"]
            self.assertEqual(context.binding.scope.to_dict(), document["scope"])
            self.assertEqual({"005930": quantity}, document["recovery"]["account"]["positions"])
            self.assertEqual("kiwoom_rest", document["source"])
            self.assertEqual(1, self.owner.applied_settings_revision(document["scope"]))
            self.assertNotIn("2345678901", json.dumps(document))
        with self.store._connection() as connection:
            self.assertEqual(0, connection.execute("SELECT count(*) FROM central_execution_account_snapshots").fetchone()[0])
        self.assertIs(self.market_broker, source.broker)
        self.assertEqual(1, self.collector.end_credential_change.await_count)

    async def test_off_on_and_noop_preserve_clients_brokers_and_ranking_collector(self):
        self.owner._account_poll_interval = 0.02
        source, target = await self.admitted()
        await self.until(lambda: self.documents(target))
        client, broker, queries = target.client, target.broker, target.account_queries
        reconnects = self.collector.end_credential_change.await_count
        stopped = await self.toggle(target, False)
        self.assertEqual(2, stopped["applied_revision"])
        self.assertEqual("off", stopped["monitor_status"]["state"])
        calls = len(client.account_calls)
        await asyncio.sleep(0.08)
        self.assertEqual(calls, len(client.account_calls))
        self.assertEqual(2, (await self.toggle(target, False))["settings"]["revision"])
        started = await self.toggle(target, True)
        self.assertEqual(3, started["applied_revision"])
        await self.until(lambda: len(client.account_calls) > calls)
        self.assertEqual((client, broker, queries), (target.client, target.broker, target.account_queries))
        self.assertEqual(reconnects, self.collector.end_credential_change.await_count)
        self.assertTrue(source.monitor_enabled)

    async def test_invalid_policy_and_stale_revision_do_not_change_running_monitor(self):
        context = await self.active()
        before = context.monitor_task
        with self.assertRaisesRegex(CredentialOperationError, "ACCOUNT_SETTINGS_REVISION_CONFLICT"):
            await self.toggle(context, False, revision=0)
        value = {"active_profile_id": self.profile, "monitor_enabled": True, "mock_order_enabled": True}
        with self.assertRaisesRegex(CredentialOperationError, "ACCOUNT_SETTINGS_INVALID"):
            await self.owner.update_settings(context.binding.scope.to_dict(), value, 1)
        self.assertIs(before, context.monitor_task)
        self.assertEqual(1, self.settings(context)["revision"])

    async def test_storage_rejects_wrong_scope_binding_policy_and_naive_clock_without_writing(self):
        context = await self.active()
        recovery = await self.owner.read_account(self.profile, expected_binding_revision=1)
        now = as_kst(context.client.server_now())
        wrong_account = replace(recovery, account=replace(recovery.account, account_ref="wrong"))
        mock_binding = replace(context.binding, scope=AccountScope("kiwoom", AccountEnvironment.MOCK,
                                                                  context.binding.scope.account_ref))
        for binding, result, received, revision in (
                (context.binding, wrong_account, now, 1), (replace(context.binding, binding_revision=2), recovery, now, 1),
                (mock_binding, recovery, now, 1), (context.binding, recovery, now.replace(tzinfo=None), 1),
                (context.binding, recovery, now - timedelta(days=1), 1), (context.binding, recovery, now, 2)):
            with self.assertRaises(ValueError):
                self.store.save_real_account_recovery(binding, result, received, settings_revision=revision)
        self.assertEqual([], self.documents(context))
        await self.toggle(context, False)
        with self.assertRaisesRegex(ValueError, "ACCOUNT_CONTEXT_MISMATCH"):
            self.store.save_real_account_recovery(context.binding, recovery, now, settings_revision=2)

    async def test_storage_retry_is_idempotent_and_dialect_helper_matches(self):
        context = await self.active()
        recovery = await self.owner.read_account(self.profile, expected_binding_revision=1)
        now = as_kst(context.client.server_now())
        first = self.store.save_real_account_recovery(context.binding, recovery, now, settings_revision=1)
        second = self.store.save_real_account_recovery(context.binding, recovery, now, settings_revision=1)
        self.assertEqual(first, second)
        self.assertEqual(1, len(self.documents(context)))
        class PgCursor:
            def __init__(self, cursor):
                self.cursor = cursor
            def execute(self, sql, parameters):
                return self.cursor.execute(sql.replace("%s", "?"), parameters)
            def fetchone(self):
                return self.cursor.fetchone()
        with self.store._lock, self.store._connection() as connection:
            result = _save_real_account_recovery(PgCursor(connection.cursor()), context.binding, recovery, now, 1, "%s")
        self.assertEqual(first, result)
        self.assertEqual(1, len(self.documents(context)))

    async def test_cancelled_off_waiter_still_finishes_read_storage_and_policy(self):
        self.owner._account_poll_interval = 0.02
        entered, unblock = threading.Event(), threading.Event()
        context = await self.active()
        support.support.RealFakeClient.query_entered, support.support.RealFakeClient.query_release = entered, unblock
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            waiter = asyncio.create_task(self.toggle(context, False))
            await self.until(lambda: context.account_reads_paused)
            waiter.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiter
            self.assertEqual(1, self.settings(context)["revision"])
            unblock.set()
            await self.until(lambda: self.settings(context)["revision"] == 2)
            await self.until(lambda: not self.owner._settings_tasks)
            self.assertTrue(self.documents(context))
            self.assertEqual(2, self.owner.applied_settings_revision(context.binding.scope.to_dict()))
        finally:
            unblock.set()

    async def test_precommit_failure_restores_monitor_without_broker_or_market_fence(self):
        context = await self.active()
        with patch.object(self.store, "save_account_settings", side_effect=ValueError("ACCOUNT_SETTINGS_REVISION_CONFLICT")):
            with self.assertRaisesRegex(CredentialOperationError, "ACCOUNT_SETTINGS_REVISION_CONFLICT"):
                await self.toggle(context, False)
        self.assertFalse(context.account_reads_paused)
        self.assertFalse(context.broker._credential_paused)
        self.assertTrue(context.monitor_enabled)
        self.assertEqual(1, self.owner.applied_settings_revision(context.binding.scope.to_dict()))

    async def test_ambiguous_policy_write_fences_account_collection_and_sanitizes_error(self):
        context = await self.active(profile="nas-real-default")
        original = self.store.save_account_settings
        def uncertain(value, *, expected_revision):
            original(value, expected_revision=expected_revision)
            raise RuntimeError("secret-must-not-be-returned")
        reconnects = self.collector.begin_credential_change.await_count
        with patch.object(self.store, "save_account_settings", side_effect=uncertain):
            with self.assertRaisesRegex(CredentialOperationError, "^ACCOUNT_SETTINGS_RECOVERY_REQUIRED$"):
                await self.toggle(context, False)
        self.assertEqual(2, self.settings(context)["revision"])
        self.assertIsNone(self.owner.applied_settings_revision(context.binding.scope.to_dict()))
        self.assertFalse(context.broker._credential_paused)
        self.assertEqual(reconnects, self.collector.begin_credential_change.await_count)

    async def test_failed_cycle_does_not_store_partial_and_next_cycle_recovers(self):
        self.owner._account_poll_interval = 0.02
        context = await self.active()
        original = context.client.request_with_continuation
        def broken(api_id, path, body, **kwargs):
            return ({}, False, "") if api_id == "ka10076" else original(api_id, path, body, **kwargs)
        with patch.object(context.client, "request_with_continuation", side_effect=broken):
            await self.until(lambda: context.monitor_error_code is not None)
            self.assertEqual([], self.documents(context))
        await self.until(lambda: context.monitor_last_success_at is not None)
        self.assertIsNone(context.monitor_error_code)

    async def test_role_change_and_key_rotation_restart_one_monitor_per_account(self):
        source, target = await self.admitted()
        old = source.monitor_task, target.monitor_task
        await self.owner.change_market_role(self.profile, expected_revision=0, expected_binding_revision=1)
        self.assertTrue(all(task.done() for task in old))
        self.assertFalse(source.monitor_task.done())
        self.assertFalse(target.monitor_task.done())
        previous = target.monitor_task
        replacement = await self.ready(key="b2", revision=1)
        await self.runtime.apply(replacement.operation_id, 1, replacement.candidate.account_ref)
        await asyncio.wait_for(replacement.task, 5)
        self.assertTrue(previous.done())
        self.assertFalse(target.monitor_task.done())
        self.assertEqual(2, target.binding.binding_revision)
        self.assertEqual(1, self.owner.applied_settings_revision(target.binding.scope.to_dict()))

    async def test_real_only_https_owner_routes_settings_and_rejects_mock_or_plain_http(self):
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        from kiwoom_monitor.central_server.credential_runtime import install_credential_routes
        context = await self.active()
        app = FastAPI()
        async def authorize():
            return None
        install_credential_routes(app, self.runtime, authorize, real_account_owner=self.owner)
        path = "/api/v1/settings/accounts/" + context.binding.scope.account_ref
        value = {"expected_revision": 1, "active_profile_id": self.profile,
                 "monitor_enabled": False, "mock_order_enabled": False}
        async with AsyncClient(transport=ASGITransport(app), base_url="https://localhost") as client:
            response = await client.put(path + "?environment=real", json=value)
            self.assertEqual(200, response.status_code, response.text)
            self.assertEqual(2, response.json()["applied_revision"])
            self.assertEqual("rest_poll", response.json()["monitor_status"]["mode"])
            response = await client.put(path + "?environment=mock", json=value)
            self.assertEqual(503, response.status_code)
            response = await client.put(path + "?environment=real", json={**value, "secret": "not-for-output"})
            self.assertEqual(422, response.status_code)
            self.assertNotIn("not-for-output", response.text)
        async with AsyncClient(transport=ASGITransport(app), base_url="http://localhost") as client:
            response = await client.put(path + "?environment=real", json=value)
            self.assertEqual(426, response.status_code)

    async def test_key_rotation_waits_for_actual_background_snapshot_write(self):
        self.owner._account_poll_interval = 0.02
        context = await self.active()
        original = self.store.save_real_account_recovery
        entered, unblock = threading.Event(), threading.Event()
        def blocked(*args, **kwargs):
            entered.set()
            unblock.wait(timeout=5)
            return original(*args, **kwargs)
        try:
            with patch.object(self.store, "save_real_account_recovery", side_effect=blocked):
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                replacement = await self.ready(key="a2", revision=1)
                self.assertEqual("READY", replacement.state, replacement.error_code)
                await self.runtime.apply(replacement.operation_id, 1, replacement.candidate.account_ref)
                await self.until(lambda: context.account_reads_paused)
                self.assertEqual(1, self.vault.load("kiwoom_real", self.profile).revision)
                self.assertFalse(replacement.task.done())
                unblock.set()
                await asyncio.wait_for(replacement.task, 5)
            self.assertEqual(2, context.binding.binding_revision)
            self.assertTrue(any(value["document"]["binding_revision"] == 1 for value in self.documents(context)))
        finally:
            unblock.set()

    async def test_failed_precommit_monitor_restore_removes_account_admission(self):
        context = await self.active()
        operation = await self.ready(key="a2", revision=1)
        await self.owner.drain(self.profile, operation.candidate)
        with patch.object(self.owner, "_restart_account_monitors", side_effect=RuntimeError("restore failed")):
            with self.assertRaisesRegex(RuntimeError, "restore failed"):
                await self.owner.resume(self.profile)
        self.assertIsNone(self.owner.bundle(self.profile))
        self.assertTrue(context.account_reads_paused)
        self.assertIsNone(self.owner.applied_settings_revision(context.binding.scope.to_dict()))

    async def test_postgres_checker_exercises_real_recovery_and_rolls_back(self):
        from scripts.check_postgres_integration import _exercise_real_recovery_transaction
        await self.active()
        store = self.store
        class Cursor:
            def __init__(self, cursor):
                self.cursor = cursor
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.cursor.close()
            def execute(self, sql, parameters):
                if sql.startswith("SELECT pg_advisory_xact_lock"):
                    return self.cursor.execute("SELECT 1")
                return self.cursor.execute(sql.replace("%s", "?"), parameters)
            def fetchone(self):
                return self.cursor.fetchone()
        class Connection:
            def __init__(self, connection):
                self.connection = connection
            def cursor(self):
                return Cursor(self.connection.cursor())
            def rollback(self):
                self.connection.rollback()
        class Store:
            def load_account_bindings(self):
                return store.load_account_bindings()
            def load_documents(self, *args):
                return store.load_documents(*args)
            @contextmanager
            def _connect(self):
                with store._connection() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    yield Connection(connection)
        checks = _exercise_real_recovery_transaction(Store(), self.profile)
        self.assertEqual(6, len(checks))
        self.assertTrue(all(checks.values()), checks)
        self.assertEqual([], self.store.load_documents("real_account_recovery"))


if __name__ == "__main__":
    unittest.main()
