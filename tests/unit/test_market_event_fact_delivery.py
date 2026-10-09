"""Real hub/SQLite gates for feature-owned fact admission, ACK and drain."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.diagnostic_replay_contract import capture_owner, operation_identity
from kiwoom_monitor.central_server.diagnostic_replay_runtime import ReplayRuntimeScope
from kiwoom_monitor.central_server.market_events import MarketEventService
from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import TradeTick


def event(code, price):
    return {"type": "trade", "payload": asdict(TradeTick(
        code, price, 1, 1, 1, price, "090001", 29.97))}


async def until(predicate):
    async with asyncio.timeout(10):
        while not predicate():
            await asyncio.sleep(.002)


class MarketEventFactDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SQLiteQueryStore(Path(self.temp.name) / "facts.sqlite3")
        self.store.initialize()
        self.hub = RealtimeHub()
        self.now = datetime(2026, 10, 8, 9, 0, 1)
        self.service = MarketEventService(None, self.hub, self.store, now_provider=lambda: self.now)
        self.service._cohort = {"005930": {}, "000660": {}}
        self.service._upper_limits = {code: 130000 for code in self.service._cohort}
        self.subscriber = self.hub.connect()
        self.service._subscriber = self.subscriber
        self.hub.update_subscription(self.subscriber, list(self.service._cohort), [], program_codes=[])
        self.service._tasks = [asyncio.create_task(self.service._event_loop(), name="hot-cohort-events")]
        self.releases = []

    async def asyncTearDown(self):
        for release in self.releases:
            release.set()
        if self.service._close_task is None:
            await self.service.close()
        elif not self.service._close_task.done():
            await self.service.close()
        self.store.close()
        self.temp.cleanup()

    def held_save(self, *, ack_loss=False, failure=False):
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        append, attempts = self.store.append_upper_limit_facts, []
        def save(values):
            attempts.append(json.loads(json.dumps(values)))
            if len(attempts) == 1:
                entered.set()
                if not release.wait(10):
                    raise TimeoutError("held fact was never released")
                if failure:
                    raise OSError("injected statement failure")
            result = append(values)
            if len(attempts) == 1 and ack_loss:
                raise OSError("injected lost COMMIT acknowledgement")
            return result
        return patch.object(self.store, "append_upper_limit_facts", side_effect=save), entered, release, attempts

    def publish(self, code, price):
        self.hub.publish(event(code, price), code)

    def rows(self, code=""):
        return self.store.load_market_event_history("upper_limit", code=code)

    async def test_same_cadence_held_commit_preserves_transient_current_and_reception(self):
        fixture = [event("000660", 130000)] + [event("005930", 120000) for _ in range(1000)]
        digest = hashlib.sha256(json.dumps(fixture, sort_keys=True).encode()).hexdigest()
        self.assertEqual("93d721686645c21262f8021b29ffe9f25f474a3b2eea4f2b23e8e18a1dec3498", digest)
        patcher, entered, release, attempts = self.held_save()
        with patcher:
            self.publish("005930", 120000)
            await until(entered.is_set)
            high_water = 0
            for value in fixture:
                self.hub.publish(value, value["payload"]["code"])
                high_water = max(high_water, self.subscriber.queue.qsize())
                await asyncio.sleep(.001)
            await until(self.subscriber.queue.empty)
            self.assertEqual(0, self.subscriber.dropped_events)
            self.assertEqual(3, len(self.service._pending_facts))
            self.assertEqual([], self.rows("000660"))
            self.assertFalse(self.service._tasks[0].done())
            self.assertEqual(130000, self.service._last_ticks[("000660", "KRX")].current_price)
            release.set()
            await self.service.close()
        self.assertEqual(["CURRENT", "TOUCHED"], sorted(row["status"] for row in self.rows("000660")))
        self.assertEqual(3, len(attempts))
        print(json.dumps({"upper_limit_delivery_control": {"input_hash": digest,
            "input_count": len(fixture), "queue_high_water": high_water,
            "dropped": self.subscriber.dropped_events, "native_calls": len(attempts),
            "scope": "same controlled cadence, not recorded whole-app replay"}}))

    async def test_failure_and_lost_ack_retry_identical_first_document_and_shared_completion(self):
        patcher, entered, release, attempts = self.held_save(ack_loss=True)
        with patcher:
            first = asyncio.create_task(self.service.observe_trade(TradeTick(**event("005930", 120000)["payload"])))
            await until(entered.is_set)
            duplicate = asyncio.create_task(self.service.observe_trade(TradeTick(**event("005930", 121000)["payload"])))
            await asyncio.sleep(.01)
            self.assertEqual(1, len(self.service._pending_facts))
            self.assertFalse(first.done())
            self.assertFalse(duplicate.done())
            self.now = datetime(2026, 10, 9, 9, 0, 1)
            self.service._upper_limits["005930"] = 140000
            release.set()
            await asyncio.wait_for(asyncio.gather(first, duplicate), 5)
        self.assertEqual(attempts[0], attempts[1])
        self.assertEqual(1, len(self.rows()))
        self.assertEqual("2026-10-08", self.rows()[0]["session_id"])
        self.assertEqual(1, self.service._fact_failures)
        self.assertFalse(self.service._tasks[0].done())
        self.assertEqual(0, self.service.condition_status()["fact_collection"]["pending"])

    async def test_failed_save_keeps_receiver_alive_and_close_waits_for_retry(self):
        patcher, entered, release, attempts = self.held_save(failure=True)
        with patcher:
            self.publish("005930", 120000)
            await until(entered.is_set)
            self.publish("000660", 130000)
            await until(self.subscriber.queue.empty)
            release.set()
            await until(lambda: self.service._fact_failures == 1)
            self.assertFalse(self.service._tasks[0].done())
            self.assertEqual([], self.rows())
            self.assertTrue(self.service.condition_status()["fact_collection"]["recovery_required"])
            self.assertEqual("WAITING_CONNECTION", self.service.condition_status()["apply_status"])
            closing = asyncio.create_task(self.service.close())
            await asyncio.sleep(.01)
            self.assertFalse(closing.done())
            await asyncio.wait_for(closing, 5)
        self.assertEqual(attempts[0], attempts[1])
        self.assertEqual(3, len(self.rows()))
        self.assertFalse(self.service._pending_facts)

    async def test_count_backpressure_freezes_whole_event_before_midnight(self):
        self.service._fact_capacity = 1
        patcher, entered, release, attempts = self.held_save()
        with patcher:
            self.publish("005930", 120000)
            await until(entered.is_set)
            self.publish("000660", 130000)
            await until(lambda: self.service._fact_saturations > 0)
            self.assertEqual(1, len(self.service._pending_facts))
            self.now = datetime(2026, 10, 9, 9, 0, 1)
            self.service._upper_limits["000660"] = 140000
            release.set()
            await self.service.close()
        rows = self.rows("000660")
        self.assertEqual(2, len(rows))
        self.assertTrue(all(row["session_id"] == "2026-10-08" for row in rows))
        self.assertTrue(all(row["upper_limit_price"] == 130000 for row in rows))
        self.assertEqual(1, self.service._fact_high_water)

    async def test_payload_budget_and_waiter_cancellation_do_not_retire_accepted_fact(self):
        patcher, entered, release, attempts = self.held_save()
        with patcher:
            waiter = asyncio.create_task(self.service.observe_trade(TradeTick(**event("005930", 120000)["payload"])))
            await until(entered.is_set)
            self.service._fact_payload_limit = self.service._fact_payload_bytes + 10
            self.publish("000660", 130000)
            await until(lambda: self.service._fact_saturations > 0)
            waiter.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiter
            self.assertFalse(next(iter(self.service._pending_facts.values())).completion.cancelled())
            self.assertEqual(1, self.service._fact_high_water)
            release.set()
            await self.service.close()
        self.assertEqual(3, len(self.rows()))
        self.assertLessEqual(self.service._fact_bytes_high_water, self.service._fact_payload_limit)

    async def test_repeated_cancelled_close_drains_queued_decisions_and_real_commit(self):
        patcher, entered, release, attempts = self.held_save()
        with patcher:
            self.publish("005930", 120000)
            await until(entered.is_set)
            self.publish("000660", 130000)
            closing = asyncio.create_task(self.service.close())
            await until(lambda: self.subscriber not in self.hub._subscribers)
            closing.cancel()
            await asyncio.sleep(.01)
            closing.cancel()
            other = asyncio.create_task(self.service.close())
            await asyncio.sleep(.01)
            self.assertFalse(closing.done())
            self.assertFalse(other.done())
            self.assertTrue(self.service._fact_native is not None)
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(closing, 5)
            await other
        self.assertEqual(3, len(self.rows()))
        self.assertFalse(self.service._pending_facts)
        self.assertIsNone(self.service._fact_native)
        self.assertIsNone(self.service._subscriber)

    async def test_failed_peer_task_does_not_skip_disconnect_or_fact_drain(self):
        async def broken_peer():
            raise OSError("failed peer")
        peer = asyncio.create_task(broken_peer())
        self.service._tasks.append(peer)
        self.publish("005930", 120000)
        await asyncio.sleep(.01)
        with self.assertRaisesRegex(OSError, "failed peer"):
            await self.service.close()
        self.assertNotIn(self.subscriber, self.hub._subscribers)
        self.assertEqual(1, len(self.rows()))
        self.assertFalse(self.service._pending_facts)
        self.assertFalse(self.service._tasks)

    async def test_restart_keeps_acknowledged_first_write_and_never_claims_ram_recovery(self):
        tick = TradeTick(**event("005930", 130000)["payload"])
        await self.service.observe_trade(tick)
        before = self.rows("005930")
        await self.service.close()
        replacement = MarketEventService(None, self.hub, self.store, now_provider=lambda: datetime(2026, 10, 8, 10))
        replacement._upper_limits["005930"] = 130000
        try:
            await replacement.observe_trade(tick)
            self.assertEqual(before, self.rows("005930"))
            self.assertEqual(2, len(replacement._facts))
            self.assertEqual("unverified", replacement.condition_status()["fact_collection"]["restart_coverage"])
        finally:
            await replacement.close()

    async def test_origin_is_small_and_old_trace_cause_is_not_reused_on_retry(self):
        token, identities, attempts = ["trace-a"], [], []
        append = self.store.append_upper_limit_facts
        def save(values):
            identities.append(operation_identity())
            attempts.append(values)
            if len(attempts) == 1:
                raise OSError("change trace before retry")
            return append(values)
        with patch("kiwoom_monitor.central_server.market_events.input_token", side_effect=lambda _: token[0]), \
                patch.object(self.store, "append_upper_limit_facts", side_effect=save):
            with capture_owner("market_events", "actual-owner", "actual-actor", cause_input_id="cause-a"):
                public = asyncio.create_task(self.service.observe_trade(TradeTick(**event("005930", 120000)["payload"])))
            await until(lambda: self.service._fact_failures == 1)
            token[0] = "trace-b"
            await asyncio.wait_for(public, 5)
        self.assertEqual("cause-a", identities[0]["cause_input_id"])
        self.assertEqual("", identities[1]["cause_input_id"])
        self.assertEqual("actual-owner", identities[1]["producer_component"])
        self.assertEqual(attempts[0], attempts[1])

    async def test_no_yield_raw_burst_still_reports_partial_coverage(self):
        for _ in range(1001):
            self.publish("005930", 120000)
        self.assertEqual(1, self.subscriber.dropped_events)
        await self.service.close()
        status = self.service.condition_status()["fact_collection"]
        self.assertEqual("partial", status["coverage"])
        self.assertTrue(status["recovery_required"])
        self.assertEqual(1, status["dropped_events"])
        self.assertEqual(1, len(self.rows()))

    async def test_owned_runtime_shutdown_drains_previously_admitted_facts(self):
        runtime = ReplayRuntimeScope()
        patcher, entered, release, attempts = self.held_save()
        with runtime.activate(), patcher:
            first = asyncio.create_task(self.service.observe_trade(TradeTick(**event("005930", 120000)["payload"])))
            await until(entered.is_set)
            second = asyncio.create_task(self.service.observe_trade(TradeTick(**event("000660", 130000)["payload"])))
            await until(lambda: len(self.service._pending_facts) == 3)
            runtime.begin_shutdown()
            closing = asyncio.create_task(self.service.close())
            await until(lambda: self.service._event_draining)
            release.set()
            await asyncio.wait_for(asyncio.gather(first, second, closing), 5)
            await runtime.drain(timeout=5)
            runtime.require_drained()
        self.assertEqual(3, len(attempts))
        self.assertEqual(3, len(self.rows()))

    async def test_regular_close_marker_waits_for_closed_fact_ack(self):
        tick = TradeTick(**event("005930", 130000)["payload"])
        await self.service._process_event(event("005930", 130000))
        await self.service.observe_trade(tick)
        self.service._observed_sessions.add("2026-10-08")
        patcher, entered, release, attempts = self.held_save()
        with patcher:
            marker = asyncio.create_task(self.service.close_krx_regular_session("2026-10-08"))
            await until(entered.is_set)
            self.assertFalse(marker.done())
            self.assertEqual([], self.store.load_documents("market_event_sessions", "krx", 1))
            self.assertEqual(2, len(self.rows("005930")))
            release.set()
            await asyncio.wait_for(marker, 5)
        self.assertEqual("CLOSED_AT_LIMIT", attempts[0][0]["status"])
        self.assertEqual(3, len(self.rows("005930")))
        self.assertTrue(self.store.load_documents("market_event_sessions", "krx", 1)[0]["document"]["regular_closed_at"])

    async def test_actual_pending_envelope_reports_payload_and_linux_rss(self):
        """Measure real entries; fake executor isolates RAM admission from DB speed."""
        loop = asyncio.get_running_loop()
        self.addCleanup(loop.set_debug, loop.get_debug())
        loop.set_debug(False)  # Production futures do not retain unittest stack traces.
        def linux_memory():
            if not Path("/proc/self/status").is_file():
                return {"rss_bytes": None, "host_available_bytes": None}
            rss = next(int(line.split()[1]) * 1024 for line in
                       Path("/proc/self/status").read_text().splitlines() if line.startswith("VmRSS:"))
            available = next(int(line.split()[1]) * 1024 for line in
                             Path("/proc/meminfo").read_text().splitlines() if line.startswith("MemAvailable:"))
            return {"rss_bytes": rss, "host_available_bytes": available}
        entered, release = asyncio.Event(), asyncio.Event()
        calls = 0
        async def fake_executor(_function, _values):
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                await release.wait()
            return 0
        before = linux_memory()
        started, cpu = time.perf_counter(), time.process_time()
        with patch("kiwoom_monitor.central_server.market_events.owned_to_thread", side_effect=fake_executor):
            tick = TradeTick(**event("005930", 130000)["payload"])
            for number in range(5000):
                code = f"{number:06d}"
                self.service._upper_limits[code] = 130000
                for status in ("UNKNOWN", "TOUCHED", "CURRENT", "CLOSED_AT_LIMIT"):
                    plan = self.service._prepare_limit_fact(code, "2026-10-08", status, tick, "capacity-fixture")
                    await self.service._admit_limit_fact(plan)
                if number == 0:
                    await entered.wait()
            after = linux_memory()
            self.assertEqual(20000, len(self.service._pending_facts))
            self.assertLess(self.service._fact_payload_bytes, self.service._fact_payload_limit)
            report = {"scope": "actual pending entries, fake executor, not whole-app RSS or performance",
                "entries": 20000, "payload_bytes": self.service._fact_payload_bytes,
                "limit_reached_first": "entry_count", "before": before, "after": after,
                "admission_ms": (time.perf_counter() - started) * 1000,
                "admission_cpu_ms": (time.process_time() - cpu) * 1000}
            release.set()
            await self.service.close()
        self.assertEqual(20000, calls)
        self.assertFalse(self.service._pending_facts)
        print(json.dumps({"upper_limit_pending_capacity": report}))
