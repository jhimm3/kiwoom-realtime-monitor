"""Cold RAM and actual source-driver shutdown, without network or database."""
import asyncio
from datetime import datetime
import unittest

from kiwoom_monitor.central_server.diagnostic_replay_runtime import ReplayRuntimeScope, owned_create_task
from kiwoom_monitor.central_server.diagnostic_top20_execution import (
    _require_fresh_collector, _stop_subscription_driver,
)
from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
from kiwoom_monitor.central_server.realtime_hub import RealtimeHub


class Top20ExecutionBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def test_previous_ram_and_accumulator_state_cannot_pass_cold_gate(self):
        collector = CentralRealtimeCollector(lambda: self.fail("network forbidden"), "real",
            RealtimeHub(), lambda: datetime.fromisoformat("2026-09-10T09:30:01+09:00"))
        _require_fresh_collector(collector)
        for owner, name in ((collector, "_latest_snapshots"), (collector, "_continuous_from"),
                (collector, "_ram_trade_baselines"), (collector._minute_bars, "_cumulative"),
                (collector._minute_bars, "_bars"), (collector, "_ram_stock_references"),
                (collector, "_pending_snapshots"), (collector, "_pending_minute_retries"),
                (collector, "_pending_minute_finalizations")):
            with self.subTest(field=name):
                target = getattr(owner, name)
                target["previous-run"] = 1
                with self.assertRaisesRegex(ValueError, "non_cold_or_missing"):
                    _require_fresh_collector(collector)
                target.clear()
        collector._credential_shutdown = True
        with self.assertRaisesRegex(ValueError, "credential_shutdown"):
            _require_fresh_collector(collector)

    async def test_inflight_and_owned_tasks_remain_non_cold_after_pending_is_empty(self):
        collector = CentralRealtimeCollector(lambda: self.fail("network forbidden"), "real",
            RealtimeHub(), lambda: datetime.fromisoformat("2026-09-10T09:30:01+09:00"))
        for owner, name, value in (
                (collector, "_inflight_minute_bars", [{"code": "005930"}]),
                (collector, "_approved_trade_sources", {("005930", "KRX")}),
                (collector._minute_bars, "_dirty", {("005930", "KRX")}),
                (collector, "_ram_trades", ["previous-trade"])):
            with self.subTest(field=name):
                target = getattr(owner, name)
                if isinstance(target, set):
                    target.update(value)
                else:
                    target.extend(value)
                with self.assertRaisesRegex(ValueError, name):
                    _require_fresh_collector(collector)
                target.clear()
        task = asyncio.create_task(asyncio.sleep(0))
        try:
            for finished in (False, True):
                if finished:
                    await task
                for name in ("_token_tasks", "_flush_tasks", "_credential_drain_task",
                             "_credential_resume_task", "_reconnect_expiry_task"):
                    with self.subTest(field=name, finished=finished):
                        previous = getattr(collector, name)
                        setattr(collector, name, {task} if isinstance(previous, set) else task)
                        try:
                            with self.assertRaisesRegex(ValueError, name):
                                _require_fresh_collector(collector)
                        finally:
                            setattr(collector, name, previous)
        finally:
            await task
        _require_fresh_collector(collector)

    async def test_two_cancellations_wait_for_actual_subscription_stop_before_drain(self):
        runtime, entered, stopping, release = ReplayRuntimeScope(), asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def source():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopping.set()
                await release.wait()
        with runtime.activate():
            worker = owned_create_task(source(), name="input-driver")
        await entered.wait()
        waiter = asyncio.create_task(_stop_subscription_driver(runtime, worker, timeout=2))
        try:
            await stopping.wait()
            for _ in range(2):
                waiter.cancel()
                await asyncio.sleep(.005)
                self.assertFalse(waiter.done())
            with self.assertRaisesRegex(RuntimeError, "not_drained"):
                runtime.require_drained()
            self.assertEqual(1, runtime.status()["pending_tasks"])
        finally:
            release.set()
            cancelled = await waiter
        self.assertTrue(cancelled)
        runtime.begin_shutdown()
        result = await runtime.drain(timeout=2)
        self.assertEqual(0, result["pending_tasks"])
        self.assertFalse(result["execution_succeeded"])

    async def test_stop_timeout_quarantines_run_until_actual_driver_exit(self):
        runtime, entered, release = ReplayRuntimeScope(), asyncio.Event(), asyncio.Event()
        async def source():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                await release.wait()
        with runtime.activate():
            worker = owned_create_task(source(), name="input-driver")
        await entered.wait()
        try:
            with self.assertRaisesRegex(RuntimeError, "not_drained"):
                await _stop_subscription_driver(runtime, worker, timeout=.02)
            self.assertEqual("quarantined", runtime.status()["phase"])
            with self.assertRaisesRegex(RuntimeError, "not_drained"):
                runtime.require_drained()
        finally:
            release.set()
            result = await runtime.drain(timeout=2)
        self.assertFalse(result["execution_succeeded"])
        self.assertTrue(result["timed_out"])
