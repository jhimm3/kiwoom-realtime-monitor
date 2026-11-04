"""Common helpers work without tools; installed scopes keep their real ownership."""
import ast
import asyncio
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from threading import Event
import unittest

from kiwoom_monitor.central_server import async_lifetime as common


class AsyncLifetimeBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_async_task_finishes_after_repeated_waiter_cancel_and_retains_error(self):
        entered, release = asyncio.Event(), asyncio.Event()
        error = RuntimeError("owned async failure")
        async def work():
            entered.set()
            await release.wait()
            raise error
        task = asyncio.create_task(work(), name="caller-owned")
        waiting = asyncio.create_task(common.await_owned_task(task))
        await asyncio.wait_for(entered.wait(), 2)
        try:
            waiting.cancel()
            await asyncio.sleep(0)
            waiting.cancel()
            await asyncio.sleep(0)
            self.assertFalse(waiting.done())
            self.assertFalse(task.done())
            self.assertFalse(task.cancelled())
        finally:
            release.set()
        with self.assertRaises(asyncio.CancelledError) as raised:
            await waiting
        self.assertTrue(task.done())
        self.assertFalse(task.cancelled())
        self.assertIs(error, task.exception())
        self.assertIs(error, raised.exception.__cause__)

    async def test_accepted_native_call_joins_actual_thread_after_repeated_cancel_and_retains_error(self):
        entered, release, exited = Event(), Event(), Event()
        error = OSError("native failure")
        calls = []
        def native(value, *, scale):
            calls.append(value * scale)
            entered.set()
            try:
                if not release.wait(2):
                    raise TimeoutError("test did not release native call")
                raise error
            finally:
                exited.set()
        request = asyncio.create_task(common.await_native_call(native, 7, scale=3))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            request.cancel()
            await asyncio.sleep(0)
            request.cancel()
            await asyncio.sleep(0)
            self.assertFalse(request.done())
            self.assertFalse(exited.is_set())
        finally:
            release.set()
        with self.assertRaises(asyncio.CancelledError) as raised:
            await request
        self.assertIs(error, raised.exception.__cause__)
        self.assertTrue(exited.is_set())
        self.assertEqual([21], calls)

    async def test_common_helpers_work_with_both_tool_sources_physically_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "kiwoom_monitor" / "central_server"
            package.mkdir(parents=True)
            (package.parent / "__init__.py").write_text("", encoding="utf-8")
            (package / "__init__.py").write_text("", encoding="utf-8")
            shutil.copy2(common.__file__, package / "async_lifetime.py")
            self.assertEqual({"__init__.py", "async_lifetime.py"}, {p.name for p in package.iterdir()})
            program = r'''
import asyncio, importlib.util, json, sys
from contextvars import ContextVar
from pathlib import Path
from threading import Event
root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
from kiwoom_monitor.central_server import async_lifetime as h
assert Path(h.__file__).resolve() == root / 'kiwoom_monitor/central_server/async_lifetime.py'
for name in ('diagnostic_replay_runtime', 'diagnostic_rest_input'):
    assert importlib.util.find_spec('kiwoom_monitor.central_server.' + name) is None
value = ContextVar('boundary_value', default=0)
async def exercise():
    value.set(17)
    async def child():
        return value.get(), asyncio.current_task().get_name()
    assert await h.owned_create_task(child(), name='ordinary-child', shutdown=True) == (17, 'ordinary-child')
    assert await h.owned_to_thread(value.get) == 17
    entered, release, finished = Event(), Event(), Event()
    def work():
        entered.set()
        try:
            assert release.wait(2)
        finally:
            finished.set()
    waiting = h.owned_create_task(h.owned_to_thread(work), name='ordinary-thread')
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        waiting.cancel()
        try:
            await waiting
            raise AssertionError('ordinary cancellation was swallowed')
        except asyncio.CancelledError:
            pass
        assert not finished.is_set()
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 2)
asyncio.run(exercise())
assert not any(x in sys.modules for x in ('kiwoom_monitor.central_server.diagnostic_replay_runtime',
                                         'kiwoom_monitor.central_server.diagnostic_rest_input'))
print(json.dumps({'common_origin': h.__file__, 'tools_absent': True, 'ordinary_context_and_cancel': True}))
'''
            result = await asyncio.to_thread(subprocess.run, [sys.executable, "-B", "-c", program, str(root)],
                                             capture_output=True, text=True, timeout=15)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIn('"tools_absent": true', result.stdout)

    async def test_runtime_and_tape_bindings_preserve_nested_context_credits_and_reset(self):
        from kiwoom_monitor.central_server import diagnostic_rest_input as tape
        from kiwoom_monitor.central_server.diagnostic_replay_runtime import ReplayRuntimeScope, owned_payload_credit
        runtime = ReplayRuntimeScope()
        client = SimpleNamespace(tape={"calls": {"a": {"start": {"lane": "A"}},
                                                "b": {"start": {"lane": "B"}}}, "catalogs": {}})
        bindings = tape.TaskTapeBindings(client, [
            {"task_name": "parent", "spawn_ordinal": 1, "source_lane": "A"},
            {"task_name": "child", "spawn_ordinal": 1, "source_lane": "B"},
        ])
        seen = []
        async def child():
            seen.append(("child", tape._TAPE_LANE.get().lane))
            return await common.owned_to_thread(lambda: tape._TAPE_LANE.get().lane)
        async def parent():
            seen.append(("parent", tape._TAPE_LANE.get().lane))
            result = await common.owned_create_task(child(), name="child")
            seen.append(("parent-return", tape._TAPE_LANE.get().lane))
            return result
        with runtime.activate():
            credit = owned_payload_credit()
            with runtime.activate():
                self.assertIs(credit, owned_payload_credit())
                with bindings.activate():
                    with self.assertRaisesRegex(RuntimeError, "market_tape_task_bindings_shared"):
                        with bindings.activate():
                            pass
                    self.assertEqual("B", await common.owned_create_task(parent(), name="parent"))
                self.assertIsNone(tape._TAPE_LANE.get())
            self.assertIs(credit, owned_payload_credit())
            with self.assertRaisesRegex(RuntimeError, "replay_runtime_shared_owner"):
                with ReplayRuntimeScope().activate():
                    pass
            self.assertEqual(19, await common.owned_to_thread(lambda: 19))
        self.assertEqual([("parent", "A"), ("child", "B"), ("parent-return", "A")], seen)
        self.assertEqual([], bindings.report()["unused_bindings"])
        runtime.begin_shutdown()
        receipt = await runtime.drain(timeout=2)
        self.assertTrue(receipt["execution_succeeded"])
        self.assertEqual((0, 0), (receipt["pending_tasks"], receipt["pending_threads"]))
        self.assertEqual(23, await common.owned_create_task(asyncio.sleep(0, result=23), name="outside"))
        self.assertEqual(29, await common.owned_to_thread(lambda: 29))

    def test_normal_callers_import_common_functions_and_replay_aliases_are_identical(self):
        from kiwoom_monitor.central_server import diagnostic_replay_runtime as replay
        self.assertIs(common.owned_create_task, replay.owned_create_task)
        self.assertIs(common.owned_to_thread, replay.owned_to_thread)
        for name in ("autonomous_top20", "realtime_collector", "program_snapshot_writer", "rest_broker", "market_events"):
            path = Path(common.__file__).with_name(name + ".py")
            tree = ast.parse(path.read_bytes())
            imports = [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
            helper_imports = [n for n in imports if any(x.name in {"owned_create_task", "owned_to_thread"} for x in n.names)]
            self.assertEqual(1, len(helper_imports), name)
            self.assertEqual("async_lifetime", helper_imports[0].module, name)


if __name__ == "__main__":
    unittest.main()
