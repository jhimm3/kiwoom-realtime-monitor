from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from kiwoom_monitor.application.daily_bar_coverage import (
    COLLECTION, DailySourceWindow, assess_daily_coverage,
)
from kiwoom_monitor.application.daily_high_service import DailyHighService
from kiwoom_monitor.central_server.autonomous_top20 import AutonomousTop20Service, KST
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.market_ingest import MarketDataIngestor
from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
from kiwoom_monitor.central_server.rest_broker import BrokerResult


def source_rows(count, *, end=date(2026, 9, 30)):
    return [{"dt": (end - timedelta(days=i)).strftime("%Y%m%d"), "open_pric": "100",
             "high_pric": str(110 + i), "low_pric": "90", "cur_prc": "105", "trde_qty": "10",
             "trde_prica": "200"} for i in range(count)]


def window_with(count, *, end=date(2026, 9, 30), exhausted=True):
    window = DailySourceWindow(end.isoformat(), end.isoformat())
    window.add_page({"stk_dt_pole_chart_qry": source_rows(count, end=end)}, not exhausted, "next" if not exhausted else "")
    return window


class DailyCoverageTests(unittest.TestCase):
    def test_independent_periods_and_provisional_bars(self):
        for count in (3, 20, 249, 250):
            with self.subTest(count=count):
                window = window_with(count)
                evidence = window.evidence(code="005930", market="KRX", scope="initial", checked_at="now")
                result = assess_daily_coverage(window.rows, evidence, code="005930", market="KRX", query_basis_date="2026-09-30")
                self.assertTrue(result["collection_verified"])
                self.assertEqual(["provisional"] * 3,
                                 [result["periods"][str(n)]["status"] for n in (5, 20, 250)])
                self.assertEqual(min(count, 250), result["periods"]["250"]["available_count"])

    def test_prefix_survives_deleted_or_changed_tail_but_not_changed_latest(self):
        window = window_with(250)
        evidence = window.evidence(code="005930", market="KRX", scope="final", checked_at="now")
        for rows in (window.rows[:20], [*window.rows[:249], {**window.rows[-1], "high": 999}]):
            result = assess_daily_coverage(rows, evidence, code="005930", market="KRX", query_basis_date="2026-09-30")
            self.assertEqual("ready", result["periods"]["20"]["status"])
            self.assertEqual("unverified", result["periods"]["250"]["status"])
            self.assertFalse(result["collection_verified"])
        changed = [{**window.rows[0], "high": 999}, *window.rows[1:]]
        self.assertEqual("unverified", assess_daily_coverage(changed, evidence, code="005930", market="KRX",
                                                            query_basis_date="2026-09-30")["periods"]["5"]["status"])

    def test_stale_basis_small_limit_and_timestamp_only_change(self):
        window = window_with(250)
        evidence = window.evidence(code="005930", market="KRX", scope="initial", checked_at="now")
        self.assertFalse(assess_daily_coverage(window.rows, evidence, code="005930", market="KRX",
                                             query_basis_date="2026-10-01")["collection_verified"])
        rows = [{**row, "updated_at": 99999} for row in window.rows]
        self.assertTrue(assess_daily_coverage(rows, evidence, code="005930", market="KRX",
                                            query_basis_date="2026-09-30")["collection_verified"])
        result = assess_daily_coverage(rows[:5], evidence, code="005930", market="KRX", query_basis_date="2026-09-30")
        self.assertEqual("provisional", result["periods"]["5"]["status"])
        self.assertEqual("unverified", result["periods"]["20"]["status"])

    def test_continuation_overlap_and_invalid_progress(self):
        rows = source_rows(250)
        window = DailySourceWindow("2026-09-30", "2026-09-30")
        window.add_page({"stk_dt_pole_chart_qry": rows[:100]}, True, "page2")
        window.add_page({"stk_dt_pole_chart_qry": rows[99:]}, False, "")
        self.assertEqual(250, len(window.rows))
        for payload, key in ((rows[:100], "page3"), (rows[100:], "page2"), (rows[100:], "")):
            with self.subTest(key=key, day=payload[0]["dt"]):
                w = DailySourceWindow("2026-09-30", "2026-09-30")
                w.add_page({"stk_dt_pole_chart_qry": rows[:100]}, True, "page2")
                with self.assertRaises(ValueError):
                    w.add_page({"stk_dt_pole_chart_qry": payload}, True, key)

    def test_empty_malformed_conflicting_future_and_page_cap(self):
        for rows in ([], [{**source_rows(1)[0], "dt": "20260230"}],
                     [{**source_rows(1)[0], "high_pric": "bad"}],
                     [source_rows(1)[0], {**source_rows(1)[0], "high_pric": "999"}],
                     source_rows(1, end=date(2026, 10, 1))):
            with self.subTest(rows=rows):
                with self.assertRaises(ValueError):
                    DailySourceWindow("2026-09-30", "2026-09-30").add_page({"stk_dt_pole_chart_qry": rows}, False, "")
        window = DailySourceWindow("2026-09-30", "2026-09-30")
        rows = source_rows(8)
        for i in range(7):
            window.add_page({"stk_dt_pole_chart_qry": [rows[i]]}, True, str(i))
        with self.assertRaises(ValueError):
            window.add_page({"stk_dt_pole_chart_qry": [rows[7]]}, True, "last")

    def test_nas_missing_coverage_does_not_fallback_to_tr(self):
        class OldNas:
            def load_stored_daily_bars(self, *args):
                return window_with(250).rows

            def request(self, *args):
                raise AssertionError("NAS read must not start PC TR")

        targets = DailyHighService(OldNas()).load("005930")
        self.assertIsNone(targets.high_250_price)
        self.assertEqual("unverified", targets.period_status("5"))

    def test_direct_continuation_short_nxt_and_partial_failure(self):
        today = date.today()

        class Direct:
            fail_nxt = False

            def request_with_continuation(self, api_id, path, body, **kwargs):
                if str(body["stk_cd"]).endswith("_NX"):
                    if self.fail_nxt:
                        raise RuntimeError("offline")
                    return {"stk_dt_pole_chart_qry": source_rows(3, end=today)}, False, ""
                rows = source_rows(250, end=today)
                second = kwargs["cont_yn"] == "Y"
                return {"stk_dt_pole_chart_qry": rows[100:] if second else rows[:100]}, not second, "next" if not second else ""

        client = Direct()
        service = DailyHighService(client, include_nxt=True, cached_high_250_loader=lambda _: 5000)
        valid = service.load("005930")
        self.assertTrue(valid.collection_verified)
        self.assertEqual(359, valid.high_250_price)
        client.fail_nxt = True
        failed = service.load("005930")
        self.assertIsNone(failed.high_250_price)
        self.assertEqual(5000, failed.cached_high_250_price)
        self.assertFalse(failed.collection_verified)

    def test_short_whole_source_computes_all_periods_but_unknown_end_does_not(self):
        class Direct:
            def request_with_continuation(self, *args, **kwargs):
                return {"stk_dt_pole_chart_qry": source_rows(3, end=date.today())}, False, ""

        valid = DailyHighService(Direct()).load("005930")
        self.assertEqual((112, 112, 112), (valid.high_5_price, valid.high_20_price, valid.high_250_price))
        self.assertTrue(valid.collection_verified)
        self.assertTrue(valid.source_exhausted)

        class NoHeaders:
            def request(self, *args):
                return {"stk_dt_pole_chart_qry": source_rows(3, end=date.today())}

        unknown = DailyHighService(NoHeaders()).load("005930")
        self.assertEqual((None, None, None), (unknown.high_5_price, unknown.high_20_price, unknown.high_250_price))
        self.assertFalse(unknown.source_exhausted)
        self.assertFalse(unknown.collection_verified)


class DailyCollectorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = SQLiteQueryStore(Path(self.directory.name) / "central.sqlite3")
        self.store.initialize()
        self.now = datetime(2026, 9, 30, 7, 0, tzinfo=KST)
        store = self.store

        class Broker:
            calls = []
            fail_save = False
            started = None
            release = None

            async def request(self, api_id, path, body, **kwargs):
                self.calls.append((body, kwargs))
                if self.started is not None:
                    self.started.set()
                    await self.release.wait()
                rows = source_rows(250, end=date(2026, 9, 29))
                second = kwargs["cont_yn"] == "Y"
                payload = {"stk_dt_pole_chart_qry": rows[100:] if second else rows[:100]}
                if not self.fail_save:
                    MarketDataIngestor(store).ingest(api_id, body, payload)
                return BrokerResult(payload, not second, "page2" if not second else "", recording_succeeded=True)

        self.broker = Broker()
        self.broker.calls = []
        self.service = AutonomousTop20Service(self.broker, RealtimeHub(), self.store, now_provider=lambda: self.now)

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    async def test_old_rows_are_refreshed_once_premarket_then_reused_and_reverified(self):
        MarketDataIngestor(self.store).ingest("ka10081", {"stk_cd": "005930"},
                                            {"stk_dt_pole_chart_qry": source_rows(250, end=date(2026, 8, 1))})
        for _ in range(2):
            result = await self.service._ensure_daily_history("005930", "2026-09-30", "KRX", scope="initial")
        self.assertTrue(result["collection_verified"])
        self.assertEqual("ready", result["periods"]["250"]["status"])
        self.assertEqual(2, len(self.broker.calls))
        self.assertEqual("20260930", self.broker.calls[0][0]["base_dt"])
        with self.store._connection() as connection:
            connection.execute("DELETE FROM central_daily_bars WHERE code='005930' AND trading_date='2026-09-29'")
        await self.service._ensure_daily_history("005930", "2026-09-30", "KRX", scope="initial")
        self.assertEqual(4, len(self.broker.calls))

    async def test_save_failure_never_publishes_evidence(self):
        self.broker.fail_save = True
        with self.assertRaisesRegex(RuntimeError, "저장값"):
            await self.service._ensure_daily_history("005930", "2026-09-30", "KRX", scope="initial")
        self.assertEqual([], self.store.load_documents(COLLECTION, "005930:KRX", 2))

    async def test_new_basis_at_midnight_rechecks_without_requiring_today_bar(self):
        await self.service._ensure_daily_history("005930", "2026-09-30", "KRX", scope="initial")
        self.now = datetime(2026, 10, 1, 0, 1, tzinfo=KST)
        result = await self.service._ensure_daily_history("005930", "2026-10-01", "KRX", scope="initial")
        self.assertEqual("2026-10-01", result["query_basis_date"])
        self.assertEqual("2026-09-29", result["latest_bar_date"])
        self.assertTrue(result["collection_verified"])
        self.assertEqual("20261001", self.broker.calls[-1][0]["base_dt"])

    async def test_initial_and_final_overlap_keep_distinct_scopes(self):
        self.now = datetime(2026, 9, 30, 21, 0, tzinfo=KST)
        self.store.upsert_documents("krx_trading_day_observations", [{"owner": "2026-09-29", "key": "observed",
                                   "document": {"trading_date": "2026-09-29"}}])
        self.broker.started, self.broker.release = asyncio.Event(), asyncio.Event()
        initial = asyncio.create_task(self.service._ensure_daily_history("005930", "2026-09-29", "KRX", scope="initial"))
        await self.broker.started.wait()
        final = asyncio.create_task(self.service._ensure_daily_history("005930", "2026-09-29", "KRX", scope="final"))
        self.broker.release.set()
        first, second = await asyncio.gather(initial, final)
        self.assertEqual(("initial", "final"), (first["scope"], second["scope"]))
        self.assertEqual(4, len(self.broker.calls))
        self.assertEqual(2, len(self.store.load_documents(COLLECTION, "005930:KRX", 2)))
        self.assertEqual({}, self.service._daily_history_locks)

    async def test_concurrent_waiter_cancellation_keeps_owner_lock_and_reuses(self):
        self.broker.started, self.broker.release = asyncio.Event(), asyncio.Event()
        owner = asyncio.create_task(self.service._ensure_daily_history("005930", "2026-09-30", "KRX", scope="initial"))
        await self.broker.started.wait()
        waiter = asyncio.create_task(self.service._ensure_daily_history("005930", "2026-09-30", "KRX", scope="initial"))
        await asyncio.sleep(0)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        peer = asyncio.create_task(self.service._ensure_daily_history("005930", "2026-09-30", "KRX", scope="initial"))
        self.broker.release.set()
        await asyncio.gather(owner, peer)
        self.assertEqual(2, len(self.broker.calls))
        self.assertEqual({}, self.service._daily_history_locks)

    async def test_final_scope_requires_trading_evidence_and_target_day(self):
        self.now = datetime(2026, 9, 30, 21, 0, tzinfo=KST)
        with self.assertRaisesRegex(RuntimeError, "장후 확정"):
            await self.service._ensure_daily_history("005930", "2026-09-29", "KRX", scope="final")
        self.store.upsert_documents("krx_trading_day_observations", [{"owner": "2026-09-29", "key": "observed",
                                   "document": {"trading_date": "2026-09-29"}}])
        self.service._krx_trading_day_cache.clear()
        result = await self.service._ensure_daily_history("005930", "2026-09-29", "KRX", scope="final")
        self.assertTrue(result["collection_verified"])
        self.assertEqual("final", result["scope"])
        self.assertTrue(all(call[0]["base_dt"] == "20260930" for call in self.broker.calls))

    async def test_owner_cancel_and_evidence_write_failure_publish_no_completion(self):
        self.broker.started, self.broker.release = asyncio.Event(), asyncio.Event()
        task = asyncio.create_task(self.service._ensure_daily_history("005930", "2026-09-30", "KRX", scope="initial"))
        await self.broker.started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual({}, self.service._daily_history_locks)
        self.assertEqual([], self.store.load_documents(COLLECTION, "005930:KRX", 2))
        self.broker.release.set()
        original = self.store.upsert_documents

        def fail_evidence(collection, *args, **kwargs):
            if collection == COLLECTION:
                raise RuntimeError("evidence unavailable")
            return original(collection, *args, **kwargs)

        self.store.upsert_documents = fail_evidence
        with self.assertRaisesRegex(RuntimeError, "evidence unavailable"):
            await self.service._ensure_daily_history("005930", "2026-09-30", "KRX", scope="initial")
        self.assertEqual([], self.store.load_documents(COLLECTION, "005930:KRX", 2))
        self.assertEqual(250, len(self.store.load_daily_bars("005930", "KRX", 250)))


if __name__ == "__main__":
    unittest.main()
