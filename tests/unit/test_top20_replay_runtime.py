"""Actual executor completion, cancellation and native shutdown ownership."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from threading import Event
import unittest
from unittest.mock import MagicMock, patch

from kiwoom_monitor.central_server.diagnostic_replay_runtime import (
    ReplayRuntimeScope, close_top20_replay_resources, owned_create_task, owned_to_thread,
)
from kiwoom_monitor.central_server import diagnostic_replay_baseline as baseline
from tests.unit.test_recorded_replay_baseline import Connection, TOKEN, URL


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(.001)


class Top20ReplayRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def lease(self, runtime):
        lease = baseline.ReplayDatabaseLease(URL, TOKEN)
        lease._active, lease._run_ready, lease._generation = True, True, 1
        lease.connection = MagicMock()
        lease.bind_runtime(runtime)
        return lease

    async def test_cancelled_preconnect_thread_blocks_reset_and_retirement_until_actual_exit(self):
        runtime = ReplayRuntimeScope()
        lease = self.lease(runtime)
        store = lease.store()
        started, release = Event(), Event()
        marker = ContextVar('test_source_call', default=None)
        seen = []
        def write():
            started.set()
            if not release.wait(2):
                raise AssertionError('release missing')
            seen.append(marker.get())
            connection = store._connect()
            connection.close()
        connection = Connection()
        with patch.object(baseline._OwnedConnection, 'connect', return_value=connection) as connect:
            with runtime.activate():
                token = marker.set('source-call-1')
                task = owned_create_task(owned_to_thread(write))
                marker.reset(token)
            try:
                await until(started.is_set)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertEqual(0, lease._open_count)
                self.assertEqual(1, runtime.status()['pending_threads'])
                for action in (lambda: lease._maintenance(MagicMock()), lease._retire):
                    with self.assertRaisesRegex(RuntimeError, 'not_drained'):
                        action()
                lease.connection.close.assert_not_called()
                self.assertTrue(lease._active)
                with self.assertRaisesRegex(RuntimeError, 'unowned_native'):
                    store._connect()
                connect.assert_not_called()
                runtime.begin_shutdown()
                with self.assertRaisesRegex(RuntimeError, 'not_drained'):
                    await runtime.drain(timeout=.02)
            finally:
                release.set()
            receipt = await runtime.drain(timeout=2)
        self.assertEqual(['source-call-1'], seen)
        connect.assert_called_once()
        self.assertTrue(connection.closed)
        self.assertTrue(receipt['timed_out'])
        self.assertFalse(receipt['execution_succeeded'])
        lease._retire()
        self.assertFalse(lease._active)

    async def test_executor_queue_is_owned_before_a_thread_or_connection_exists(self):
        loop = asyncio.get_running_loop()
        executor = ThreadPoolExecutor(max_workers=1)
        loop.set_default_executor(executor)
        blocker_started, release = Event(), Event()
        def blocker():
            blocker_started.set()
            if not release.wait(2):
                raise AssertionError('release missing')
        blocked = loop.run_in_executor(None, blocker)
        runtime = ReplayRuntimeScope()
        ran = []
        try:
            await until(blocker_started.is_set)
            with runtime.activate():
                task = owned_create_task(owned_to_thread(lambda: ran.append('native')))
            await until(lambda: runtime.status()['submitted_threads'] == 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            runtime.begin_shutdown()
            with self.assertRaisesRegex(RuntimeError, 'not_drained'):
                await runtime.drain(timeout=.02)
            self.assertEqual([], ran)
            self.assertEqual(1, runtime.status()['pending_threads'])
        finally:
            release.set()
            await blocked
        await runtime.drain(timeout=2)
        self.assertEqual(['native'], ran)

    async def test_capacity_late_work_and_swallowed_thread_failure_remain_visible(self):
        runtime = ReplayRuntimeScope(capacity=1)
        stop = asyncio.Event()
        with runtime.activate():
            task = owned_create_task(stop.wait())
            with self.assertRaisesRegex(RuntimeError, 'capacity'):
                owned_create_task(asyncio.sleep(0))
        stop.set()
        await task
        await asyncio.sleep(0)
        def failed():
            raise OSError('COMMIT acknowledgement lost')
        with runtime.activate():
            with self.assertRaises(OSError):
                await owned_to_thread(failed)
        runtime.begin_shutdown()
        with runtime.activate(), self.assertRaisesRegex(RuntimeError, 'new_work_fenced'):
            owned_create_task(asyncio.sleep(0))
        receipt = await runtime.drain()
        self.assertEqual(3, receipt['error_count'])
        self.assertFalse(receipt['execution_succeeded'])
        runtime.require_drained()  # Failed execution can still be safely restored.

    async def test_cancelled_close_waiter_does_not_cancel_native_cleanup_or_restore_early(self):
        runtime = ReplayRuntimeScope()
        started, release = Event(), Event()
        order = []
        class Resource:
            def __init__(self, name):
                self.name = name
            async def close(self):
                order.append(self.name)
                if self.name == 'service':
                    def flush():
                        started.set()
                        if not release.wait(2):
                            raise AssertionError('release missing')
                    await owned_to_thread(flush)
        waiter = asyncio.create_task(close_top20_replay_resources(runtime,
            Resource('service'), Resource('broker'), collector=Resource('collector'), timeout=2))
        try:
            await until(started.is_set)
            waiter.cancel()
            await asyncio.sleep(.01)
            self.assertFalse(waiter.done())
            with self.assertRaisesRegex(RuntimeError, 'not_drained'):
                runtime.require_drained()
        finally:
            release.set()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        runtime.require_drained()
        self.assertEqual(['service', 'collector', 'broker'], order)
        self.assertFalse(runtime.status()['execution_succeeded'])

    async def test_close_failure_still_drains_other_resources(self):
        runtime = ReplayRuntimeScope()
        order = []
        class Resource:
            def __init__(self, name):
                self.name = name
            async def close(self):
                order.append(self.name)
                if self.name == 'service':
                    raise OSError('native close failed')
                await owned_to_thread(lambda: None)
        receipt = await close_top20_replay_resources(runtime, Resource('service'), Resource('broker'))
        self.assertEqual(['service', 'broker'], order)
        self.assertFalse(receipt['execution_succeeded'])
        self.assertEqual(1, receipt['error_count'])

    async def test_close_timeout_quarantines_but_admitted_cleanup_can_still_finish(self):
        runtime = ReplayRuntimeScope()
        started, release = Event(), Event()
        order = []
        class Resource:
            def __init__(self, name):
                self.name = name
            async def close(self):
                def native():
                    if self.name == 'service':
                        started.set()
                        if not release.wait(2):
                            raise AssertionError('release missing')
                    order.append(self.name)
                await owned_to_thread(native)
        try:
            with self.assertRaisesRegex(RuntimeError, 'not_drained'):
                await close_top20_replay_resources(runtime, Resource('service'), Resource('broker'), timeout=.02)
            self.assertTrue(started.is_set())
            self.assertEqual('quarantined', runtime.status()['phase'])
            with self.assertRaisesRegex(RuntimeError, 'not_drained'):
                await runtime.drain(timeout=.02)
            with runtime.activate(), self.assertRaisesRegex(RuntimeError, 'new_work_fenced'):
                owned_create_task(asyncio.sleep(0))
        finally:
            release.set()
        await until(lambda: runtime.status()['pending_tasks'] == 0)
        receipt = await runtime.drain()
        self.assertEqual(['service', 'broker'], order)
        self.assertFalse(receipt['execution_succeeded'])
        self.assertTrue(receipt['timed_out'])

    async def test_inactive_helpers_keep_asyncio_results_and_context(self):
        self.assertEqual(7, await owned_to_thread(lambda: 7))
        self.assertEqual(8, await owned_create_task(asyncio.sleep(0, result=8)))

    async def test_native_broker_finishes_response_and_cache_after_shutdown_starts(self):
        from kiwoom_monitor.central_server.rest_broker import CentralRestBroker
        started, release = Event(), Event()
        order = []
        class Client:
            def request_with_continuation(self, *args, **kwargs):
                started.set()
                if not release.wait(2):
                    raise AssertionError('release missing')
                order.append('response')
                return {'return_code': 0}, False, ''
        class Store:
            def load_query(self, key):
                return None
            def save_query(self, *args):
                order.append('cache')
        runtime = ReplayRuntimeScope()
        broker = CentralRestBroker(Client(), Store(),
            response_handler=lambda *args: order.append('ingest'), ranking_reservation=False)
        with runtime.activate():
            request = owned_create_task(broker.request('ka10001', '/api/dostk/stkinfo', {'stk_cd': '005930'}))
        try:
            await until(started.is_set)
            closing = asyncio.create_task(close_top20_replay_resources(runtime, None, broker, timeout=2))
            await until(lambda: runtime.status()['phase'] == 'shutdown')
        finally:
            release.set()
        result = await request
        receipt = await closing
        self.assertTrue(result.recording_succeeded)
        self.assertEqual(['response', 'ingest', 'cache'], order)
        self.assertTrue(receipt['execution_succeeded'])
