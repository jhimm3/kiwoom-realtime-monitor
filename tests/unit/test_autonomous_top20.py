from __future__ import annotations

import asyncio
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from kiwoom_monitor.central_server.autonomous_top20 import (
    AutonomousTop20Service,
    _collection_open,
    _candidate_investor_flow_due,
    _entry_minute_query_due,
    _ranking_collection_due,
    _ranking_expected_at,
)
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
from kiwoom_monitor.central_server.rest_broker import BrokerResult
from kiwoom_monitor.central_server.market_ingest import MarketDataIngestor
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import MarketOperationTick
from kiwoom_monitor.domain.market_data_contract import (
    DataCompleteness,
    MarketDatasetKind,
    ObservationOrigin,
)


class _Broker:
    def __init__(self) -> None:
        self.calls = []

    async def request(self, api_id, path, body, **kwargs):
        self.calls.append((api_id, body, kwargs))
        if api_id == "ka00198":
            return BrokerResult({"item_inq_rank": _ranking_rows("090000")}, False, "")
        if api_id == "ka10100":
            return BrokerResult({"nxtEnable": "Y" if body["stk_cd"] == "005930" else "N"}, False, "")
        if api_id == "ka10001":
            return BrokerResult({"mac": "1000", "dstr_rt": "40"}, False, "")
        raise AssertionError(api_id)


def _ranking_rows(clock: str) -> list[dict[str, str]]:
    rows = [
        {"dt": "20260910", "tm": clock, "stk_cd": "A005930", "stk_nm": "삼성전자", "bigd_rank": "1"},
        {"dt": "20260910", "tm": clock, "stk_cd": "000660_AL", "stk_nm": "SK하이닉스", "bigd_rank": "2"},
    ]
    rows.extend({
        "dt": "20260910", "tm": clock, "stk_cd": f"{100000 + rank:06d}",
        "stk_nm": f"종목{rank}", "bigd_rank": str(rank),
    } for rank in range(3, 21))
    return rows


class _FlakyIndexStore:
    def __init__(self) -> None:
        self.calls = 0
        self.saved: list[tuple] = []

    def save_dataset_snapshot(self, *args, **kwargs) -> None:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary database failure")
        self.saved.append((args, kwargs))


class AutonomousTop20Tests(unittest.IsolatedAsyncioTestCase):
    async def test_investor_flow_waits_until_premarket_and_resumes_without_false_completion(self) -> None:
        now = [datetime.fromisoformat("2026-09-30T07:59:59+09:00")]
        store = MagicMock()
        store.load_documents.return_value = []
        service = AutonomousTop20Service(_Broker(), RealtimeHub(), store, now_provider=lambda: now[0])
        with patch.object(service, "_ensure_entry_basic", new_callable=AsyncMock), \
                patch.object(service, "_backfill_entry_minutes", new_callable=AsyncMock, return_value=True), \
                patch.object(service, "_ensure_entry_daily_history", new_callable=AsyncMock), \
                patch.object(service, "_capture_candidate_investor_flow", new_callable=AsyncMock, return_value=True) as flow, \
                patch.object(service, "_ensure_historical_high", new_callable=AsyncMock):
            await service._ensure_fundamentals(("005930",), "2026-09-30")
            flow.assert_not_awaited()
            self.assertNotIn(("005930", "flow"), service._entry_stage_ready)
            service._schedule_fundamentals(("005930",), "2026-09-30")
            self.assertFalse(service._fundamentals_tasks)
            now[0] = datetime.fromisoformat("2026-09-30T08:00:00+09:00")
            service._schedule_fundamentals(("005930",), "2026-09-30")
            await asyncio.gather(*service._fundamentals_tasks)
            flow.assert_awaited_once_with("005930", "2026-09-30")
            self.assertEqual("2026-09-30", service._entry_stage_ready[("005930", "flow")])

    async def test_investor_flow_direct_call_outside_market_does_not_request_or_mark_complete(self) -> None:
        for timestamp in ("2026-09-30T00:00:00+09:00", "2026-09-30T07:59:59+09:00",
                          "2026-09-30T20:00:00+09:00", "2026-10-03T10:00:00+09:00"):
            with self.subTest(timestamp=timestamp):
                now = datetime.fromisoformat(timestamp)
                broker, store = _Broker(), MagicMock()
                service = AutonomousTop20Service(broker, RealtimeHub(), store, now_provider=lambda: now)
                self.assertFalse(await service._capture_candidate_investor_flow("005930", now.date().isoformat()))
                self.assertFalse(broker.calls)
                store.load_documents.assert_not_called()
                store.upsert_documents.assert_not_called()

    async def test_investor_flow_does_not_request_after_marker_read_crosses_close(self) -> None:
        now = [datetime.fromisoformat("2026-09-30T19:59:59+09:00")]
        broker, store = _Broker(), MagicMock()
        def load(*args):
            now[0] = datetime.fromisoformat("2026-09-30T20:00:00+09:00")
            return []
        store.load_documents.side_effect = load
        service = AutonomousTop20Service(broker, RealtimeHub(), store, now_provider=lambda: now[0])
        self.assertFalse(await service._capture_candidate_investor_flow("005930", "2026-09-30"))
        self.assertFalse(broker.calls)
        store.upsert_documents.assert_not_called()

    async def test_investor_flow_market_window_includes_pre_and_after_market_in_kst(self) -> None:
        for timestamp, due in (("2026-09-30T07:59:59+09:00", False),
                               ("2026-09-30T08:00:00+09:00", True),
                               ("2026-09-30T15:30:00+09:00", True),
                               ("2026-09-30T19:59:59+09:00", True),
                               ("2026-09-30T20:00:00+09:00", False),
                               ("2026-09-29T23:00:00+00:00", True)):
            with self.subTest(timestamp=timestamp):
                self.assertEqual(due, _candidate_investor_flow_due("2026-09-30", datetime.fromisoformat(timestamp)))
        self.assertFalse(_candidate_investor_flow_due("2026-09-29", datetime(2026, 9, 30, 10)))

    async def test_delayed_ranking_response_keeps_continuously_sampled_minute_complete(self) -> None:
        start = datetime.fromisoformat("2026-09-10T09:00:00+09:00")
        now = [start]
        rows = _ranking_rows("090000")
        codes = tuple(str(row["stk_cd"]).removeprefix("A").removesuffix("_AL") for row in rows)
        hub = RealtimeHub()
        hub.set_upstream_ready(codes, True)
        store = MagicMock()
        service = AutonomousTop20Service(_Broker(), hub, store, now_provider=lambda: now[0])
        service._collector.active_codes = codes
        service._markets = dict.fromkeys(codes, "KOSPI")
        service._trade_values.bucket_trade_value_eok = lambda code, period, minute: min(
            60.0, max(0.0, (now[0] - minute).total_seconds()),
        )
        service._advance(start)

        async def delayed_response(requested_at):
            self.assertEqual(start, requested_at)
            # The index loop continues while the independent REST request waits.
            for quarter in range(1, 14):
                now[0] = start + timedelta(seconds=quarter / 4)
                service._advance(now[0])
            return BrokerResult({"item_inq_rank": rows}, False, ""), rows, False, False

        with patch.object(service, "_select_ranking_response", side_effect=delayed_response), \
                patch.object(service, "_schedule_market_catalog"), \
                patch.object(service, "_schedule_subscription_update"), \
                patch.object(service, "_load_daily_entrant_state", new_callable=AsyncMock,
                             return_value=({}, set())), \
                patch.object(service, "_persist_missing_daily_entrants", new_callable=AsyncMock):
            await service.refresh_ranking_once(start)
        clock_after_response = service._collector.last_observed_at
        for quarter in range(14, 241):
            now[0] = start + timedelta(seconds=quarter / 4)
            update = service._advance(now[0])
        self.assertEqual("realtime_complete", update.completed.capture_state)
        self.assertEqual(1200.0, update.completed.total)
        self.assertEqual(start + timedelta(seconds=3.25), clock_after_response)
        membership_call = store.save_dataset_snapshot.call_args
        self.assertEqual("top20_membership", membership_call.args[0])
        self.assertEqual("2026-09-10T09:00:00", membership_call.args[2])
        self.assertEqual("2026-09-10T09:00:00", membership_call.args[3]["observed_at"])

    async def test_ranking_collection_clock_is_sampled_after_collector_lock_wait(self) -> None:
        start = datetime.fromisoformat("2026-09-10T09:00:00+09:00")
        now = [start + timedelta(seconds=3)]
        rows = _ranking_rows("090000")
        hub = RealtimeHub()
        hub.set_upstream_ready(("005930",), True)
        service = AutonomousTop20Service(_Broker(), hub, MagicMock(), now_provider=lambda: now[0])
        service._collector.active_codes = ("005930",)
        response_ready = asyncio.Event()

        async def response(requested_at):
            response_ready.set()
            return BrokerResult({"item_inq_rank": rows}, False, ""), rows, False, False

        with patch.object(service, "_select_ranking_response", side_effect=response), \
                patch.object(service, "_schedule_market_catalog"), \
                patch.object(service, "_schedule_subscription_update"), \
                patch.object(service, "_load_daily_entrant_state", new_callable=AsyncMock,
                             return_value=({}, set())), \
                patch.object(service, "_persist_missing_daily_entrants", new_callable=AsyncMock):
            async with service._collector_lock:
                task = asyncio.create_task(service.refresh_ranking_once(start))
                await response_ready.wait()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                now[0] = start + timedelta(seconds=6)
            await task
        self.assertEqual(now[0], service._collector.last_observed_at)

    async def test_restart_rechecks_durable_entry_markers_without_new_tr_requests(self) -> None:
        now = datetime.fromisoformat("2026-09-30T09:00:00+09:00")
        class DailyBroker(_Broker):
            async def request(self, api_id, path, body, **kwargs):
                self.calls.append((api_id, body, kwargs))
                if api_id != "ka10081":
                    raise AssertionError(f"unexpected repeated TR {api_id}")
                payload = {"stk_dt_pole_chart_qry": [{
                    "dt": "20260930", "open_pric": "100", "high_pric": "110",
                    "low_pric": "90", "cur_prc": "105", "trde_qty": "10",
                }]}
                MarketDataIngestor(store, now_provider=lambda: now).ingest(api_id, body, payload)
                return BrokerResult(payload, False, "", recording_succeeded=True)

        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            self.addCleanup(store.close)
            broker = DailyBroker()
            for collection, owner, document in (
                ("stock_fundamentals", "005930", {"observed_at": now.isoformat(), "payload": {"mac": "1000"}}),
                ("stock_nxt_eligibility", "005930", {"observed_at": now.isoformat(), "payload": {"nxtEnable": "N"}}),
                ("candidate_flow_capture", "2026-09-30:005930", {"scope": "initial"}),
                ("historical_highs", "005930", {"checked_on": "2026-09-30", "target": 110}),
            ):
                store.upsert_documents(collection, [{"owner": owner, "key": "latest", "document": document}])
            # Create an actual verified source-window marker and canonical bars.
            first = AutonomousTop20Service(broker, RealtimeHub(), store, now_provider=lambda: now)
            await first._ensure_entry_daily_history("005930", "2026-09-30")
            from kiwoom_monitor.application.historical_high_service import HistoricalHighTarget
            with patch("kiwoom_monitor.central_server.autonomous_top20.HistoricalHighService.load",
                       return_value=HistoricalHighTarget(110, 2026, 2026, "2026-09", ())):
                await first._ensure_historical_high("005930", "2026-09-30")
            for _ in range(2):
                restarted = AutonomousTop20Service(
                    broker, RealtimeHub(), store, minute_backfill_enabled=False, now_provider=lambda: now,
                )
                await restarted._ensure_fundamentals(("005930",), "2026-09-30")
                await restarted._ensure_fundamentals(("005930",), "2026-09-30")
                self.assertEqual("2026-09-30", restarted._fundamentals_ready["005930"])
            self.assertEqual(1, len(broker.calls))

    async def test_current_day_entry_minutes_wait_until_0800_without_marking_ready(self) -> None:
        now = [datetime.fromisoformat("2026-09-30T07:59:59+09:00")]
        broker = _Broker()
        store = MagicMock()
        store.load_documents.return_value = []
        service = AutonomousTop20Service(
            broker, RealtimeHub(), store, now_provider=lambda: now[0],
        )
        with patch.object(service, "_backfill_entry_minutes", new_callable=AsyncMock, return_value=True) as minutes, \
                patch.object(service, "_ensure_entry_daily_history", new_callable=AsyncMock) as daily, \
                patch.object(service, "_capture_candidate_investor_flow", new_callable=AsyncMock) as flow, \
                patch.object(service, "_ensure_historical_high", new_callable=AsyncMock) as highs:
            await service._ensure_fundamentals(("005930",), "2026-09-30")
            await service._ensure_fundamentals(("005930",), "2026-09-30")
            service._schedule_fundamentals(("005930",), "2026-09-30")
            self.assertFalse(service._fundamentals_tasks)
            minutes.assert_not_awaited()
            self.assertNotIn("005930", service._fundamentals_ready)
            now[0] = datetime.fromisoformat("2026-09-30T08:00:00+09:00")
            service._schedule_fundamentals(("005930",), "2026-09-30")
            service._schedule_fundamentals(("005930",), "2026-09-30")
            await asyncio.gather(*service._fundamentals_tasks)
            minutes.assert_awaited_once_with("005930", "2026-09-30")
            daily.assert_awaited_once_with("005930", "2026-09-30")
            flow.assert_awaited_once_with("005930", "2026-09-30")
            highs.assert_awaited_once_with("005930", "2026-09-30")
            store.load_documents.assert_called_once_with("stock_fundamentals", "005930", 1)
            self.assertEqual(1, len(broker.calls))
            self.assertEqual("2026-09-30", service._fundamentals_ready["005930"])

        self.assertFalse(_entry_minute_query_due("2026-09-30", datetime.fromisoformat("2026-09-30T07:59:59+09:00")))
        self.assertTrue(_entry_minute_query_due("2026-09-29", datetime.fromisoformat("2026-09-30T07:59:59+09:00")))

    async def test_entry_data_is_reused_while_minutes_are_paused_and_resumed(self) -> None:
        store = MagicMock()
        store.load_documents.return_value = []
        service = AutonomousTop20Service(
            _Broker(), RealtimeHub(), store,
            now_provider=lambda: datetime.fromisoformat("2026-09-30T09:00:00+09:00"),
        )
        paused = [True]
        started, release = asyncio.Event(), asyncio.Event()

        async def minutes_ready(*args):
            started.set()
            await release.wait()
            return True

        with patch.object(service, "_workload_paused", side_effect=lambda _: paused[0]), \
                patch.object(service, "_backfill_entry_minutes", new_callable=AsyncMock, side_effect=minutes_ready) as minutes, \
                patch.object(service, "_ensure_entry_daily_history", new_callable=AsyncMock) as daily, \
                patch.object(service, "_capture_candidate_investor_flow", new_callable=AsyncMock) as flow, \
                patch.object(service, "_ensure_historical_high", new_callable=AsyncMock) as highs:
            service._schedule_fundamentals(("005930",), "2026-09-30")
            await asyncio.gather(*service._fundamentals_tasks)
            service._schedule_fundamentals(("005930",), "2026-09-30")
            self.assertFalse(service._fundamentals_pending)
            minutes.assert_not_awaited()
            self.assertNotIn("005930", service._fundamentals_ready)
            paused[0] = False
            service._schedule_fundamentals(("005930",), "2026-09-30")
            await started.wait()
            service._schedule_fundamentals(("005930",), "2026-09-30")
            self.assertEqual(1, minutes.await_count)
            release.set()
            await asyncio.gather(*service._fundamentals_tasks)
            daily.assert_awaited_once()
            flow.assert_awaited_once()
            highs.assert_awaited_once()
            store.load_documents.assert_called_once()
            self.assertEqual("2026-09-30", service._fundamentals_ready["005930"])

    async def test_deferred_minute_result_is_retried_without_repeating_entry_data(self) -> None:
        store = MagicMock()
        store.load_documents.return_value = []
        service = AutonomousTop20Service(
            _Broker(), RealtimeHub(), store,
            now_provider=lambda: datetime.fromisoformat("2026-09-30T09:00:00+09:00"),
        )
        with patch.object(service, "_backfill_entry_minutes", new_callable=AsyncMock, side_effect=[False, True]) as minutes, \
                patch.object(service, "_ensure_entry_daily_history", new_callable=AsyncMock) as daily, \
                patch.object(service, "_capture_candidate_investor_flow", new_callable=AsyncMock) as flow, \
                patch.object(service, "_ensure_historical_high", new_callable=AsyncMock) as highs:
            await service._ensure_fundamentals(("005930",), "2026-09-30")
            self.assertNotIn("005930", service._fundamentals_ready)
            await service._ensure_fundamentals(("005930",), "2026-09-30")
            self.assertEqual(2, minutes.await_count)
            daily.assert_awaited_once()
            flow.assert_awaited_once()
            highs.assert_awaited_once()
            store.load_documents.assert_called_once()
            self.assertEqual("2026-09-30", service._fundamentals_ready["005930"])

    async def test_failed_entry_data_is_not_cached_before_minutes_are_due(self) -> None:
        store = MagicMock()
        store.load_documents.return_value = []
        service = AutonomousTop20Service(
            _Broker(), RealtimeHub(), store,
            now_provider=lambda: datetime.fromisoformat("2026-09-30T07:00:00+09:00"),
        )
        with patch.object(service, "_backfill_entry_minutes", new_callable=AsyncMock) as minutes, \
                patch.object(service, "_ensure_entry_daily_history", new_callable=AsyncMock,
                             side_effect=[RuntimeError("incomplete daily history"), None]) as daily, \
                patch.object(service, "_capture_candidate_investor_flow", new_callable=AsyncMock) as flow, \
                patch.object(service, "_ensure_historical_high", new_callable=AsyncMock) as highs:
            await service._ensure_fundamentals(("005930",), "2026-09-30")
            self.assertNotIn("005930", service._entry_data_ready)
            await service._ensure_fundamentals(("005930",), "2026-09-30")
            self.assertEqual(2, daily.await_count)
            self.assertEqual(1, store.load_documents.call_count)
            flow.assert_not_awaited()
            self.assertNotIn(("005930", "flow"), service._entry_stage_ready)
            highs.assert_awaited_once()
            minutes.assert_not_awaited()
            self.assertEqual("2026-09-30", service._entry_data_ready["005930"])

    async def test_each_failed_stage_retries_without_repeating_other_successes(self) -> None:
        for failed_stage in ("basic", "minutes", "daily", "flow", "high"):
            with self.subTest(stage=failed_stage):
                store = MagicMock()
                service = AutonomousTop20Service(
                    _Broker(), RealtimeHub(), store,
                    now_provider=lambda: datetime.fromisoformat("2026-09-30T09:00:00+09:00"),
                )
                names = {"basic": "_ensure_entry_basic", "minutes": "_backfill_entry_minutes",
                         "daily": "_ensure_entry_daily_history", "flow": "_capture_candidate_investor_flow",
                         "high": "_ensure_historical_high"}
                preparations = {}
                for stage, name in names.items():
                    prepare = AsyncMock(return_value=True)
                    if stage == failed_stage:
                        prepare.side_effect = [RuntimeError(f"failed {stage}"), True]
                    setattr(service, name, prepare)
                    preparations[stage] = prepare
                service._schedule_fundamentals(("005930",), "2026-09-30")
                await asyncio.gather(*service._fundamentals_tasks)
                self.assertNotIn("005930", service._fundamentals_ready)
                self.assertEqual(0 if failed_stage == "daily" else 1,
                                 preparations["high"].await_count)
                service._schedule_fundamentals(("005930",), "2026-09-30")
                service._schedule_fundamentals(("005930",), "2026-09-30")
                await asyncio.gather(*service._fundamentals_tasks)
                for stage, prepare in preparations.items():
                    self.assertEqual(2 if stage == failed_stage else 1, prepare.await_count, stage)
                self.assertEqual("2026-09-30", service._fundamentals_ready["005930"])
                service._schedule_fundamentals(("005930",), "2026-09-30")
                self.assertFalse(service._fundamentals_pending)

    async def test_daily_markets_retry_independently_and_defer_high_until_verified(self) -> None:
        for failed_market in ("KRX", "NXT"):
            with self.subTest(market=failed_market):
                service = AutonomousTop20Service(
                    _Broker(), RealtimeHub(), MagicMock(), minute_backfill_enabled=False,
                    now_provider=lambda: datetime.fromisoformat("2026-09-30T09:00:00+09:00"),
                )
                markets = []
                async def daily(code, day, market, **kwargs):
                    markets.append(market)
                    if market == failed_market and markets.count(market) == 1:
                        raise RuntimeError(f"{market} unavailable")
                    return {}
                with patch.object(service, "_ensure_entry_basic", new_callable=AsyncMock) as basics, \
                        patch.object(service, "_ensure_daily_history", side_effect=daily), \
                        patch.object(service, "_nxt_enabled", new_callable=AsyncMock, return_value=True), \
                        patch.object(service, "_capture_candidate_investor_flow", new_callable=AsyncMock) as flow, \
                        patch.object(service, "_ensure_historical_high", new_callable=AsyncMock) as high:
                    await service._ensure_fundamentals(("005930",), "2026-09-30")
                    self.assertEqual(["KRX", "NXT"], markets)
                    high.assert_not_awaited()
                    await service._ensure_fundamentals(("005930",), "2026-09-30")
                    self.assertEqual(["KRX", "NXT", failed_market], markets)
                    basics.assert_awaited_once()
                    flow.assert_awaited_once()
                    high.assert_awaited_once()

    async def test_pre0700_basics_refresh_on_reappearance_without_repeating_other_stages(self) -> None:
        now = [datetime.fromisoformat("2026-09-30T06:59:59+09:00")]
        store = MagicMock()
        store.load_documents.return_value = [{"document": {"observed_at": now[0].isoformat()}}]
        broker = _Broker()
        service = AutonomousTop20Service(
            broker, RealtimeHub(), store, minute_backfill_enabled=False, now_provider=lambda: now[0],
        )
        with patch.object(service, "_ensure_entry_daily_history", new_callable=AsyncMock) as daily, \
                patch.object(service, "_capture_candidate_investor_flow", new_callable=AsyncMock) as flow, \
                patch.object(service, "_ensure_historical_high", new_callable=AsyncMock) as high:
            service._schedule_fundamentals(("005930",), "2026-09-30")
            await asyncio.gather(*service._fundamentals_tasks)
            self.assertEqual([], broker.calls)
            now[0] = datetime.fromisoformat("2026-09-30T07:00:00+09:00")
            service._schedule_fundamentals((), "2026-09-30")
            self.assertFalse(service._fundamentals_pending)
            service._schedule_fundamentals(("005930",), "2026-09-30")
            service._schedule_fundamentals(("005930",), "2026-09-30")
            await asyncio.gather(*service._fundamentals_tasks)
            self.assertEqual([("ka10001", {"stk_cd": "005930"}, {})], broker.calls)
            self.assertEqual(2, store.load_documents.call_count)
            daily.assert_awaited_once()
            flow.assert_not_awaited()
            high.assert_awaited_once()
            await service._ensure_fundamentals(("005930",), "2026-09-30")
            self.assertEqual(1, len(broker.calls))
            now[0] = datetime.fromisoformat("2026-09-30T08:00:00+09:00")
            service._schedule_fundamentals(("005930",), "2026-09-30")
            await asyncio.gather(*service._fundamentals_tasks)
            flow.assert_awaited_once()
            daily.assert_awaited_once()
            high.assert_awaited_once()
            self.assertEqual(1, len(broker.calls))

    async def test_failed_basic_persistence_is_retried_without_repeating_other_stages(self) -> None:
        store = MagicMock()
        store.load_documents.return_value = []
        broker = MagicMock()
        broker.request = AsyncMock(side_effect=[
            BrokerResult({"mac": "1000"}, False, "", recording_succeeded=False),
            BrokerResult({"mac": "1000"}, False, "", recording_succeeded=True),
        ])
        service = AutonomousTop20Service(
            broker, RealtimeHub(), store, minute_backfill_enabled=False,
            now_provider=lambda: datetime.fromisoformat("2026-09-30T09:00:00+09:00"),
        )
        with patch.object(service, "_ensure_entry_daily_history", new_callable=AsyncMock) as daily, \
                patch.object(service, "_capture_candidate_investor_flow", new_callable=AsyncMock) as flow, \
                patch.object(service, "_ensure_historical_high", new_callable=AsyncMock) as high:
            await service._ensure_fundamentals(("005930",), "2026-09-30")
            self.assertNotIn(("005930", "basic"), service._entry_stage_ready)
            await service._ensure_fundamentals(("005930",), "2026-09-30")
            self.assertEqual(2, broker.request.await_count)
            daily.assert_awaited_once()
            flow.assert_awaited_once()
            high.assert_awaited_once()

    async def test_entry_data_is_checked_again_after_date_change_or_restart(self) -> None:
        now = [datetime.fromisoformat("2026-09-30T07:00:00+09:00")]
        store = MagicMock()
        store.load_documents.return_value = []
        service = AutonomousTop20Service(_Broker(), RealtimeHub(), store, now_provider=lambda: now[0])
        for selected in (service, AutonomousTop20Service(_Broker(), RealtimeHub(), store, now_provider=lambda: now[0])):
            with patch.object(selected, "_ensure_entry_daily_history", new_callable=AsyncMock) as daily, \
                    patch.object(selected, "_capture_candidate_investor_flow", new_callable=AsyncMock), \
                    patch.object(selected, "_ensure_historical_high", new_callable=AsyncMock):
                day = now[0].date().isoformat()
                await selected._ensure_fundamentals(("005930",), day)
                await selected._ensure_fundamentals(("005930",), day)
                daily.assert_awaited_once_with("005930", day)
                if selected is service:
                    now[0] = datetime.fromisoformat("2026-10-01T07:00:00+09:00")
                    await selected._ensure_fundamentals(("005930",), "2026-10-01")
                    self.assertEqual(2, daily.await_count)
        self.assertEqual(3, store.load_documents.call_count)

    async def test_entry_data_completes_when_minute_backfill_is_disabled(self) -> None:
        store = MagicMock()
        store.load_documents.return_value = []
        service = AutonomousTop20Service(
            _Broker(), RealtimeHub(), store, minute_backfill_enabled=False,
            now_provider=lambda: datetime.fromisoformat("2026-09-30T07:00:00+09:00"),
        )
        with patch.object(service, "_backfill_entry_minutes", new_callable=AsyncMock) as minutes, \
                patch.object(service, "_ensure_entry_daily_history", new_callable=AsyncMock) as daily, \
                patch.object(service, "_capture_candidate_investor_flow", new_callable=AsyncMock), \
                patch.object(service, "_ensure_historical_high", new_callable=AsyncMock), \
                patch.object(service, "_workload_paused", return_value=True):
            await service._ensure_fundamentals(("005930",), "2026-09-30")
            await service._ensure_fundamentals(("005930",), "2026-09-30")
            daily.assert_awaited_once()
            minutes.assert_not_awaited()
            self.assertEqual("2026-09-30", service._fundamentals_ready["005930"])

    async def test_cancelled_minute_preparation_retries_without_repeating_entry_data(self) -> None:
        now = [datetime.fromisoformat("2026-09-30T07:00:00+09:00")]
        store = MagicMock()
        store.load_documents.return_value = []
        service = AutonomousTop20Service(_Broker(), RealtimeHub(), store, now_provider=lambda: now[0])
        started = asyncio.Event()

        async def interrupted_minutes(*args):
            started.set()
            await asyncio.Event().wait()

        with patch.object(service, "_backfill_entry_minutes", new_callable=AsyncMock,
                          side_effect=interrupted_minutes) as minutes, \
                patch.object(service, "_ensure_entry_daily_history", new_callable=AsyncMock) as daily, \
                patch.object(service, "_capture_candidate_investor_flow", new_callable=AsyncMock), \
                patch.object(service, "_ensure_historical_high", new_callable=AsyncMock):
            await service._ensure_fundamentals(("005930",), "2026-09-30")
            now[0] = datetime.fromisoformat("2026-09-30T08:00:00+09:00")
            service._schedule_fundamentals(("005930",), "2026-09-30")
            await started.wait()
            task = next(iter(service._fundamentals_tasks))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertFalse(service._fundamentals_pending)
            self.assertNotIn("005930", service._fundamentals_ready)
            minutes.side_effect = None
            minutes.return_value = True
            service._schedule_fundamentals(("005930",), "2026-09-30")
            await asyncio.gather(*service._fundamentals_tasks)
            self.assertEqual(2, minutes.await_count)
            daily.assert_awaited_once()
            store.load_documents.assert_called_once()
            self.assertEqual("2026-09-30", service._fundamentals_ready["005930"])

    async def test_direct_entry_minute_backfill_does_not_request_before_0800(self) -> None:
        broker = _Broker()
        service = AutonomousTop20Service(
            broker, RealtimeHub(), object(),
            now_provider=lambda: datetime.fromisoformat("2026-09-30T07:59:59+09:00"),
        )
        self.assertFalse(await service._backfill_entry_minutes("005930", "2026-09-30"))
        self.assertEqual([], broker.calls)

    async def test_minute_backfill_can_be_disabled_without_store_or_broker_work(self) -> None:
        broker = _Broker()
        service = AutonomousTop20Service(
            broker, RealtimeHub(), object(), minute_backfill_enabled=False,
        )

        await service._backfill_entry_minutes("005930", "2026-09-14")
        await service._backfill_minutes("005930", "2026-09-14", "KRX")

        self.assertEqual([], broker.calls)

    async def test_stale_nxt_document_is_queried_again(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            store.upsert_documents("stock_nxt_eligibility", [{
                "owner": "005930", "key": "latest",
                "document": {"enabled": False, "observed_at": "2026-09-13T09:00:00+09:00"},
            }])
            broker = _Broker()
            service = AutonomousTop20Service(
                broker, RealtimeHub(), store,
                now_provider=lambda: datetime.fromisoformat("2026-09-14T10:00:00+09:00"),
            )
            self.assertTrue(await service._nxt_enabled("005930"))
            self.assertEqual(1, len(broker.calls))
            store.close()

    async def test_failed_flow_recording_does_not_mark_initial_complete(self) -> None:
        class FailedFlowBroker(_Broker):
            async def request(self, api_id, path, body, **kwargs):
                return BrokerResult(
                    {"stk_orgn_trde_trnsn": [{"dt": "20260914"}]}, False, "",
                    recording_succeeded=False,
                )

        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            service = AutonomousTop20Service(
                FailedFlowBroker(), RealtimeHub(), store,
                now_provider=lambda: datetime(2026, 9, 14, 10),
            )
            with self.assertRaises(RuntimeError):
                await service._capture_candidate_investor_flow("005930", "2026-09-14")
            self.assertEqual([], store.load_documents("candidate_flow_capture", "2026-09-14:005930", 1))
            store.close()

    async def test_empty_market_index_does_not_mark_day_complete(self) -> None:
        class EmptyIndexBroker(_Broker):
            async def request(self, api_id, path, body, **kwargs):
                return BrokerResult(
                    {"inds_min_pole_qry": []} if api_id == "ka20005" else
                    {"inds_dt_pole_qry": []}, False, "",
                )

        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            service = AutonomousTop20Service(EmptyIndexBroker(), RealtimeHub(), store)
            with self.assertRaises(RuntimeError):
                await service._backfill_market_indexes("2026-09-14")
            self.assertEqual([], store.load_documents("market_index_chart_coverage", "2026-09-14", 1))
            store.close()

    async def test_nxt_lookup_failure_remains_retryable(self) -> None:
        class EmptyStore:
            def load_documents(self, *_args):
                return []

        broker = _Broker()
        service = AutonomousTop20Service(broker, RealtimeHub(), EmptyStore())  # type: ignore[arg-type]
        service._subscriber = service._hub.connect()
        service._collector.active_codes = ("005930",)
        original = broker.request
        failures = 0

        async def flaky(api_id, path, body, **kwargs):
            nonlocal failures
            if api_id == "ka10100" and failures == 0:
                failures += 1
                raise TimeoutError("temporary NXT failure")
            return await original(api_id, path, body, **kwargs)

        broker.request = flaky  # type: ignore[method-assign]
        await service._update_subscription()
        self.assertNotIn("005930", service._nxt_eligible)
        await service._update_subscription()
        self.assertTrue(service._nxt_eligible["005930"])

    async def test_failed_backfill_day_is_not_marked_complete_and_retries(self) -> None:
        service = AutonomousTop20Service(_Broker(), RealtimeHub(), object())  # type: ignore[arg-type]
        calls = 0

        async def flaky(_day: str) -> bool:
            nonlocal calls
            calls += 1
            return calls > 1

        service.after_close.run_day = flaky  # type: ignore[method-assign]
        service.after_close.schedule("2026-09-24")
        await service.after_close._backfill_task
        await asyncio.sleep(0)
        self.assertEqual("", service.after_close._last_backfill_day)
        service.after_close._backfill_retry_at = 0.0
        service.after_close.schedule("2026-09-24")
        await service.after_close._backfill_task
        await asyncio.sleep(0)
        self.assertEqual("2026-09-24", service.after_close._last_backfill_day)
        self.assertEqual(2, calls)

    async def test_failed_flow_retry_skips_successful_bar_steps_and_checks_new_codes(self) -> None:
        class CohortStore:
            codes = ["A", "B"]

            def load_dataset_snapshots(self, *_args):
                return []

            def load_observation_revisions(self, *_args):
                return []

            def load_documents(self, collection, _owner, _limit):
                if collection == "top20_daily_entrants":
                    return [{"key": code} for code in self.codes]
                return []

            def load_hot_cohort(self, *, active_only):
                raise AssertionError("retired condition cohort must not expand backfill")

        store = CohortStore()
        service = AutonomousTop20Service(
            _Broker(), RealtimeHub(), store,  # type: ignore[arg-type]
            now_provider=lambda: datetime.fromisoformat("2026-09-14T20:05:00+09:00"),
        )
        service.after_close._backfill_market_indexes = AsyncMock()  # type: ignore[method-assign]
        service.after_close._nxt_enabled = AsyncMock(return_value=False)  # type: ignore[method-assign]
        service.after_close._backfill_daily = AsyncMock()  # type: ignore[method-assign]
        service.after_close._backfill_minutes = AsyncMock()  # type: ignore[method-assign]
        flow_calls: list[str] = []
        # This fixture tests native backfill retry progress, not derived publication.
        service.after_close.reconcile = AsyncMock()  # type: ignore[method-assign]
        fail_b = True

        async def flow(code: str, _day: str) -> None:
            flow_calls.append(code)
            if code == "B" and fail_b:
                raise RuntimeError("empty program flow")

        service.after_close._backfill_candidate_flows = flow  # type: ignore[method-assign]
        self.assertFalse(await service.after_close.run_day("2026-09-14"))
        self.assertFalse(await service.after_close.run_day("2026-09-14"))
        self.assertEqual(["A", "B"], [call.args[0] for call in service.after_close._backfill_daily.await_args_list])
        self.assertEqual(["A", "B", "B"], flow_calls)

        store.codes.append("C")
        fail_b = False
        self.assertTrue(await service.after_close.run_day("2026-09-14"))
        self.assertEqual(["A", "B", "C"], [call.args[0] for call in service.after_close._backfill_daily.await_args_list])
        self.assertEqual(["A", "B", "B", "B", "C"], flow_calls)

        # A later explicit rerun revalidates persisted completion after the retry finishes.
        self.assertTrue(await service.after_close.run_day("2026-09-14"))
        self.assertEqual(["A", "B", "C", "A", "B", "C"],
                         [call.args[0] for call in service.after_close._backfill_daily.await_args_list])

    async def test_weekday_without_0s_trading_evidence_does_not_start_after_close_backfill(self) -> None:
        class EmptyStore:
            def load_documents(self, *_args):
                return []

        service = AutonomousTop20Service(
            _Broker(), RealtimeHub(), EmptyStore(),
            now_provider=lambda: datetime.fromisoformat("2026-09-25T20:06:00+09:00"),
        )  # type: ignore[arg-type]
        service.refresh_ranking_once = AsyncMock(return_value=())  # type: ignore[method-assign]
        service.after_close.run_day = AsyncMock(return_value=True)  # type: ignore[method-assign]

        with patch(
            "kiwoom_monitor.central_server.autonomous_top20.asyncio.sleep",
            side_effect=asyncio.CancelledError,
        ):
            with self.assertRaises(asyncio.CancelledError):
                await service._schedule_loop()

        service.after_close.run_day.assert_not_awaited()

    def test_market_operation_day_is_saved_as_kiwoom_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            service = AutonomousTop20Service(
                _Broker(), RealtimeHub(), store,
                now_provider=lambda: datetime.fromisoformat("2026-09-28T09:00:00+09:00"),
            )

            service._save_market_operation_day(
                "2026-09-28", MarketOperationTick("3", "090000", "000000"),
            )
            values = store.load_documents("krx_trading_day_observations", "2026-09-28", 1)
            store.close()

        self.assertEqual("kiwoom_websocket_0s", values[0]["document"]["source"])
        self.assertEqual("3", values[0]["document"]["status_code"])

    async def test_validated_membership_is_visible_while_database_save_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            entered = threading.Event()
            release = threading.Event()

            def blocked_save(*args, **kwargs):
                entered.set()
                if not release.wait(timeout=2):
                    raise TimeoutError("test did not release dataset save")

            store.save_dataset_snapshot = blocked_save  # type: ignore[method-assign]
            store.upsert_documents = lambda *args, **kwargs: None  # type: ignore[method-assign]
            hub = RealtimeHub()
            notification = MagicMock(side_effect=RuntimeError("optional context sink failed"))
            service = AutonomousTop20Service(
                _Broker(), hub, store, catalog_loader=lambda: (),
                now_provider=lambda: datetime.fromisoformat("2026-09-10T09:00:00+09:00"),
                on_membership_changed=notification,
            )
            service._subscriber = hub.connect()
            service._entrants_day = "2026-09-10"
            refresh = asyncio.create_task(
                service.refresh_ranking_once(datetime(2026, 9, 10, 9, 0, 0)),
            )
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 1))
                latest = service.latest_membership_snapshot("2026-09-10")
                self.assertIsNotNone(latest)
                assert latest is not None
                self.assertEqual("pending", latest["persistence_state"])
                self.assertEqual(
                    datetime.fromisoformat("2026-09-10T09:00:00+09:00").timestamp(),
                    latest["saved_at"],
                )
                self.assertEqual(20, len(latest["payload"]["items"]))
                requested, _nxt = hub.requested_codes()
                self.assertEqual(set(latest["payload"]["codes"]), set(requested))
                self.assertFalse(refresh.done())
                notification.assert_called_once_with()
            finally:
                release.set()
            await refresh
            self.assertEqual(
                "persisted",
                service.latest_membership_snapshot("2026-09-10")["persistence_state"],
            )
            if service._market_catalog_task is not None:
                await service._market_catalog_task
            await service.close()
            store.close()

    async def test_public_close_joins_scheduled_membership_native_commit_after_repeated_cancel(self) -> None:
        from contextlib import ExitStack, closing, contextmanager, nullcontext
        import json
        import sqlite3
        from kiwoom_monitor.central_server import async_lifetime
        from kiwoom_monitor.central_server.diagnostic_replay_contract import _OWNER, captured_workload_scope
        from kiwoom_monitor.central_server.diagnostic_replay_runtime import ReplayRuntimeScope

        with ExitStack() as fixtures:
            try:
                for scoped_owner in (False, True):
                    with self.subTest(scoped_owner=scoped_owner):
                        directory = fixtures.enter_context(tempfile.TemporaryDirectory())
                        clock = datetime.fromisoformat("2026-09-10T18:00:00+09:00")
                        day, key = "2026-09-10", "2026-09-10T18:00:00"
                        path = Path(directory) / "monitor.sqlite3"
                        store = SQLiteQueryStore(path, now_provider=lambda: clock)
                        fixtures.callback(store.close)
                        store.initialize()
                        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
                        writer_context = threading.local()
                        native_connection, native_save = store._connection, store.save_dataset_snapshot
                        staged, calls, events, owners, errors = [], [], [], [], []
                        rows = _ranking_rows("180000")
                        runtime = ReplayRuntimeScope() if scoped_owner else None
                        broker = _Broker()

                        async def external_response(api_id, _path, _body, **_kwargs):
                            if api_id == "ka00198":
                                return BrokerResult({"item_inq_rank": rows}, False, "")
                            if api_id == "ka10100":
                                return BrokerResult({"nxtEnable": "N"}, False, "")
                            raise AssertionError("unexpected external request: " + api_id)

                        broker.request = AsyncMock(side_effect=external_response)
                        service = AutonomousTop20Service(
                            broker, RealtimeHub(), store, now_provider=lambda: clock,
                            catalog_loader=lambda: (), minute_backfill_enabled=False,
                            capture_scope=captured_workload_scope,
                        )

                        def read_native(connection):
                            self.assertEqual([], connection.execute("PRAGMA foreign_key_check").fetchall())
                            return {
                                "snapshots": connection.execute(
                                    "SELECT snapshot_key,payload_json,revision_id FROM central_dataset_snapshots "
                                    "WHERE kind='top20_membership' AND subject=?", (day,)).fetchall(),
                                "metadata": connection.execute(
                                    "SELECT observation_key,effective_at,available_at,source,revision_id "
                                    "FROM central_market_data_observation_meta "
                                    "WHERE dataset_kind='candidate_set' AND subject=?", (day,)).fetchall(),
                                "revisions": connection.execute(
                                    "SELECT revision_id,observation_key,payload_hash,revision_of,payload_json,source_id "
                                    "FROM central_observation_revisions "
                                    "WHERE kind='top20_membership' AND subject=?", (day,)).fetchall(),
                            }

                        def independent_native():
                            with closing(sqlite3.connect(path)) as connection:
                                return read_native(connection)

                        @contextmanager
                        def observed_connection():
                            selected = getattr(writer_context, "membership", False)
                            with native_connection() as connection:
                                yield connection
                                if selected:
                                    staged.append(read_native(connection))
                                    events.append("before_commit")
                                    entered.set()
                                    if not release.wait(timeout=2):
                                        raise TimeoutError("test did not release dataset save")
                            if selected:
                                events.append("commit_returned")

                        def observed_save(*args, **kwargs):
                            selected = args[:3] == ("top20_membership", day, key)
                            writer_context.membership = selected
                            if selected:
                                calls.append(args[:3])
                                owners.append((async_lifetime._WORK_OWNER.get(), _OWNER.get()))
                            try:
                                result = native_save(*args, **kwargs)
                                if selected:
                                    events.append("save_returned")
                                return result
                            except BaseException as error:
                                if selected:
                                    errors.append(error)
                                raise
                            finally:
                                writer_context.membership = False
                                if selected:
                                    finished.set()

                        started, closing_waiters = (), []
                        try:
                            with patch.object(store, "_connection", observed_connection), \
                                    patch.object(store, "save_dataset_snapshot", observed_save), \
                                    runtime.activate() if runtime is not None else nullcontext():
                                await service.start()
                                started = tuple(service._tasks)
                                self.assertEqual(
                                    ["nas-top20-schedule", "nas-top20-events", "nas-top20-index", "nas-top20-persistence"],
                                    [task.get_name() for task in started],
                                )
                                self.assertTrue(await asyncio.to_thread(entered.wait, 1))
                                self.assertEqual([{"snapshots": 1, "metadata": 1, "revisions": 1}],
                                                 [{name: len(values) for name, values in value.items()} for value in staged])
                                self.assertEqual({"snapshots": [], "metadata": [], "revisions": []}, independent_native())
                                self.assertEqual(key, staged[0]["snapshots"][0][0])
                                # A1 stores one immutable payload and two raw references, not JSON copies.
                                revision_id = staged[0]["revisions"][0][0]
                                self.assertIsNone(staged[0]["snapshots"][0][1])
                                self.assertEqual(revision_id, staged[0]["snapshots"][0][2])
                                self.assertIsNone(staged[0]["metadata"][0][3])
                                self.assertEqual(revision_id, staged[0]["metadata"][0][4])
                                self.assertEqual(rows, json.loads(staged[0]["revisions"][0][4])["items"])
                                self.assertEqual("nas-autonomous-ka00198", staged[0]["revisions"][0][5])
                                self.assertEqual("pending", service.latest_membership_snapshot(day)["persistence_state"])
                                work_owner, capture_owner = owners[0]
                                self.assertEqual(None if runtime is None else (runtime.create_task, runtime.to_thread), work_owner)
                                self.assertEqual(f"autonomous_top20:{id(service):x}:{id(started[0]):x}", capture_owner.actor_id)
                                if runtime is not None:
                                    self.assertGreaterEqual(runtime.status()["pending_threads"], 1)
                                    runtime.begin_shutdown()

                                async def close_owner():
                                    await service.close()
                                    self.assertTrue(finished.is_set())
                                    self.assertEqual(staged[0], independent_native())
                                    events.append("close_returned")

                                closing_waiters.append(asyncio.create_task(close_owner()))
                                await asyncio.sleep(.03)
                                self.assertFalse(closing_waiters[-1].done())
                                self.assertFalse(finished.is_set())
                                for _ in range(2):
                                    started[0].cancel()
                                    await asyncio.sleep(0)
                                    self.assertFalse(started[0].done())
                                closing_waiters[-1].cancel()
                                with self.assertRaises(asyncio.CancelledError):
                                    await closing_waiters[-1]
                                closing_waiters.append(asyncio.create_task(close_owner()))
                                await asyncio.sleep(.03)
                                self.assertFalse(closing_waiters[-1].done())
                                self.assertFalse(finished.is_set())
                                release.set()
                                await closing_waiters[-1]
                                self.assertEqual(["before_commit", "commit_returned", "save_returned", "close_returned"], events)
                                self.assertEqual([("top20_membership", day, key)], calls)
                                self.assertEqual([], errors)
                                self.assertTrue(all(task.done() for task in started))
                                self.assertEqual([], service._tasks)
                                self.assertIsNone(service._subscriber)
                                if runtime is not None:
                                    state = await runtime.drain(timeout=2)
                                    self.assertTrue(state["execution_succeeded"])
                                    self.assertEqual((0, 0, 0), tuple(state[name] for name in
                                                     ("pending_tasks", "pending_threads", "submitted_threads")))
                        finally:
                            release.set()
                            await asyncio.gather(*closing_waiters, return_exceptions=True)
                            await service.close()
                            await asyncio.gather(*started, return_exceptions=True)
                            if calls:
                                # The original-source negative run must also join before deleting its fixture.
                                await asyncio.to_thread(finished.wait)
            finally:
                await asyncio.get_running_loop().shutdown_default_executor()

    def test_nonboundary_start_uses_current_half_minute_as_freshness_target(self) -> None:
        self.assertEqual(
            datetime(2026, 9, 15, 1, 5, 0),
            _ranking_expected_at(datetime(2026, 9, 15, 1, 5, 17, 900000)),
        )
        self.assertEqual(
            datetime(2026, 9, 15, 1, 5, 30),
            _ranking_expected_at(datetime(2026, 9, 15, 1, 5, 48, 900000)),
        )

    async def test_schedule_loop_collects_immediately_between_boundaries(self) -> None:
        service = AutonomousTop20Service(
            _Broker(), RealtimeHub(), object(),
            now_provider=lambda: datetime(2026, 9, 15, 1, 5, 17),
        )  # type: ignore[arg-type]
        service.refresh_ranking_once = AsyncMock(side_effect=asyncio.CancelledError())  # type: ignore[method-assign]

        with self.assertRaises(asyncio.CancelledError):
            await service._schedule_loop()

        service.refresh_ranking_once.assert_awaited_once_with(
            datetime(2026, 9, 15, 1, 5, 17),
        )

    async def test_schedule_loop_catches_up_after_refresh_crosses_boundary(self) -> None:
        moments = iter((
            datetime(2026, 9, 15, 10, 21, 29, 900000),
            datetime(2026, 9, 15, 10, 21, 31),
        ))
        current = datetime(2026, 9, 15, 10, 21, 31)

        def now_provider() -> datetime:
            nonlocal current
            current = next(moments, current)
            return current

        service = AutonomousTop20Service(
            _Broker(), RealtimeHub(), object(), now_provider=now_provider,
        )  # type: ignore[arg-type]
        service.refresh_ranking_once = AsyncMock(return_value=())  # type: ignore[method-assign]
        sleep_count = 0

        async def stop_after_two_iterations(_delay: float) -> None:
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 2:
                raise asyncio.CancelledError()

        with patch(
            "kiwoom_monitor.central_server.autonomous_top20.asyncio.sleep",
            side_effect=stop_after_two_iterations,
        ):
            with self.assertRaises(asyncio.CancelledError):
                await service._schedule_loop()

        self.assertEqual(
            [
                datetime(2026, 9, 15, 10, 21, 29, 900000),
                datetime(2026, 9, 15, 10, 21, 31),
            ],
            [call.args[0] for call in service.refresh_ranking_once.await_args_list],
        )

    async def test_after_close_backfill_does_not_block_next_ranking_slot(self) -> None:
        moments = iter((
            datetime(2026, 9, 21, 20, 19, 10),
            datetime(2026, 9, 21, 20, 19, 30),
        ))
        current = datetime(2026, 9, 21, 20, 19, 30)

        def now_provider() -> datetime:
            nonlocal current
            current = next(moments, current)
            return current

        service = AutonomousTop20Service(
            _Broker(), RealtimeHub(), object(), now_provider=now_provider,
        )  # type: ignore[arg-type]
        service.after_close._is_observed_krx_trading_day = AsyncMock(return_value=True)  # type: ignore[method-assign]
        service.refresh_ranking_once = AsyncMock(return_value=())  # type: ignore[method-assign]
        backfill_started = asyncio.Event()

        async def blocked_backfill(_day: str) -> None:
            backfill_started.set()
            await asyncio.Event().wait()

        service.after_close.run_day = blocked_backfill  # type: ignore[method-assign]
        sleep_count = 0

        async def stop_after_two_iterations(_delay: float) -> None:
            nonlocal sleep_count
            sleep_count += 1
            await asyncio.sleep(0)
            if sleep_count >= 2:
                raise asyncio.CancelledError()

        real_sleep = asyncio.sleep

        async def controlled_sleep(delay: float) -> None:
            if delay == 0:
                await real_sleep(0)
                return
            await stop_after_two_iterations(delay)

        with patch(
            "kiwoom_monitor.central_server.autonomous_top20.asyncio.sleep",
            side_effect=controlled_sleep,
        ):
            with self.assertRaises(asyncio.CancelledError):
                await service._schedule_loop()

        self.assertTrue(backfill_started.is_set())
        self.assertEqual(2, service.refresh_ranking_once.await_count)
        service.after_close.request_stop()
        await service.after_close.join()

    async def test_stale_kiwoom_ranking_retries_with_adaptive_delay(self) -> None:
        class StaleRankingBroker(_Broker):
            async def request(self, api_id, path, body, **kwargs):
                self.calls.append((api_id, body, kwargs))
                attempt = len(self.calls)
                clock = "010530" if attempt >= 5 else "010500"
                rows = _ranking_rows(clock)
                for row in rows:
                    row["dt"] = "20260915"
                return BrokerResult({"item_inq_rank": rows}, False, "")

        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            broker = StaleRankingBroker()
            service = AutonomousTop20Service(
                broker, RealtimeHub(), store, catalog_loader=lambda: (),
            )
            with patch(
                "kiwoom_monitor.central_server.autonomous_top20.asyncio.sleep",
                new=AsyncMock(),
            ) as sleeper:
                await service.refresh_ranking_once(datetime(2026, 9, 15, 1, 5, 30))
            if service._market_catalog_task is not None:
                await service._market_catalog_task
            store.close()

        self.assertEqual(5, len(broker.calls))
        self.assertEqual([0.25, 0.25, 0.5, 0.5], [call.args[0] for call in sleeper.await_args_list])

    async def test_latest_timestamp_with_empty_rank_slots_is_retried_before_storage(self) -> None:
        class PartialRankingBroker(_Broker):
            async def request(self, api_id, path, body, **kwargs):
                self.calls.append((api_id, body, kwargs))
                attempt = len(self.calls)
                rows = []
                for rank in range(1, 21):
                    valid = attempt >= 3 or rank <= 3
                    rows.append({
                        "dt": "20260915", "tm": "010530", "bigd_rank": str(rank),
                        "stk_cd": f"{rank:06d}" if valid else "",
                        "stk_nm": f"종목{rank}" if valid else "",
                    })
                return BrokerResult({"item_inq_rank": rows}, False, "")

        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            broker = PartialRankingBroker()
            service = AutonomousTop20Service(
                broker, RealtimeHub(), store, catalog_loader=lambda: (),
            )
            with patch(
                "kiwoom_monitor.central_server.autonomous_top20.asyncio.sleep",
                new=AsyncMock(),
            ) as sleeper:
                codes = await service.refresh_ranking_once(
                    datetime(2026, 9, 15, 1, 5, 30),
                )
            if service._market_catalog_task is not None:
                await service._market_catalog_task
            snapshots = store.load_dataset_snapshots(
                "top20_membership", "2026-09-15", 1,
            )
            store.close()

        self.assertEqual(20, len(codes))
        self.assertEqual(3, len(broker.calls))
        self.assertEqual([0.25, 0.25], [call.args[0] for call in sleeper.await_args_list])
        self.assertEqual(20, len(snapshots[0]["payload"]["items"]))

    async def test_latest_timestamp_with_short_rank_list_is_retried_before_storage(self) -> None:
        class ShortRankingBroker(_Broker):
            async def request(self, api_id, path, body, **kwargs):
                self.calls.append((api_id, body, kwargs))
                count = 20 if len(self.calls) >= 3 else 3
                return BrokerResult({"item_inq_rank": [{
                    "dt": "20260915", "tm": "010530", "bigd_rank": str(rank),
                    "stk_cd": f"{rank:06d}", "stk_nm": f"종목{rank}",
                } for rank in range(1, count + 1)]}, False, "")

        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            broker = ShortRankingBroker()
            service = AutonomousTop20Service(
                broker, RealtimeHub(), store, catalog_loader=lambda: (),
            )
            with patch(
                "kiwoom_monitor.central_server.autonomous_top20.asyncio.sleep",
                new=AsyncMock(),
            ) as sleeper:
                codes = await service.refresh_ranking_once(
                    datetime(2026, 9, 15, 1, 5, 30),
                )
            if service._market_catalog_task is not None:
                await service._market_catalog_task
            snapshots = store.load_dataset_snapshots(
                "top20_membership", "2026-09-15", 1,
            )
            store.close()

        self.assertEqual(20, len(codes))
        self.assertEqual(3, len(broker.calls))
        self.assertEqual([0.25, 0.25], [call.args[0] for call in sleeper.await_args_list])
        self.assertEqual(20, len(snapshots[0]["payload"]["items"]))

    async def test_failed_entry_enrichment_is_not_marked_ready_and_retries(self) -> None:
        class FailingEntryBroker(_Broker):
            async def request(self, api_id, path, body, **kwargs):
                if api_id == "ka10080":
                    self.calls.append((api_id, dict(body), kwargs))
                    raise OSError("temporary chart failure")
                return await super().request(api_id, path, body, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            broker = FailingEntryBroker()
            service = AutonomousTop20Service(broker, RealtimeHub(), store)

            await service._ensure_fundamentals(("005930",), "2026-09-14")
            await service._ensure_fundamentals(("005930",), "2026-09-14")
            store.close()

        self.assertNotIn("005930", service._fundamentals_ready)
        self.assertEqual(
            6, len([call for call in broker.calls if call[0] == "ka10080"]),
        )

    async def test_account_entry_symbol_is_subscribed_without_entering_top20_membership(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            store.upsert_documents("account_entry_symbols_daily", [{
                "owner": "2026-09-10", "key": "035420", "document": {
                    "code": "035420", "environment": "real",
                },
            }])
            hub = RealtimeHub()
            service = AutonomousTop20Service(
                _Broker(), hub, store,
                now_provider=lambda: datetime(2026, 9, 10, 9, 0),
                catalog_loader=lambda: (
                    ("005930", "삼성전자", "KOSPI"),
                    ("000660", "SK하이닉스", "KOSPI"),
                    ("035420", "NAVER", "KOSPI"),
                ),
            )
            service._subscriber = hub.connect()

            await service.refresh_ranking_once(datetime(2026, 9, 10, 9, 0))
            assert service._subscription_task is not None
            await service._subscription_task

            requested, _nxt = hub.requested_codes()
            membership = store.load_dataset_snapshots(
                "top20_membership", "2026-09-10", 1,
            )[0]["payload"]["codes"]
            store.close()

        self.assertIn("035420", requested)
        self.assertNotIn("035420", membership)

    async def test_nas_collects_due_nondefault_rankings(self) -> None:
        broker = _Broker()
        service = AutonomousTop20Service(broker, RealtimeHub(), object())  # type: ignore[arg-type]

        await service._refresh_aux_rankings(("4", "1", "2", "3"))

        self.assertEqual(["4", "1", "2", "3"], [call[1]["qry_tp"] for call in broker.calls])

    async def test_aux_rankings_are_only_scheduled_during_morning_archive_window(self) -> None:
        service = AutonomousTop20Service(_Broker(), RealtimeHub(), object())  # type: ignore[arg-type]
        with patch.object(service, "_refresh_aux_rankings", new_callable=AsyncMock) as refresh:
            for hour, minute in ((7, 54), (8, 6), (9, 0)):
                service._schedule_aux_rankings(datetime(2026, 9, 10, hour, minute, 0))
            refresh.assert_not_awaited()
            service._schedule_aux_rankings(datetime(2026, 9, 10, 7, 55, 0))
            await asyncio.gather(*service._fundamentals_tasks)
            service._schedule_aux_rankings(datetime(2026, 9, 10, 8, 5, 0))
            await asyncio.gather(*service._fundamentals_tasks)
            self.assertEqual(2, refresh.await_count)

    async def test_nas_calculates_and_reuses_historical_high(self) -> None:
        class HistoricalBroker(_Broker):
            async def request(self, api_id, path, body, **kwargs):
                if api_id == "ka10094":
                    self.calls.append((api_id, dict(body), kwargs))
                    return BrokerResult({"stk_yr_pole_chart_qry": [
                        {"dt": "2026", "high_pric": "90000"},
                    ]}, False, "")
                if api_id == "ka10083":
                    self.calls.append((api_id, dict(body), kwargs))
                    return BrokerResult({"stk_mth_pole_chart_qry": [
                        {"dt": "202609", "high_pric": "90000"},
                    ]}, False, "")
                return await super().request(api_id, path, body, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            store.upsert_documents("stock_nxt_eligibility", [{
                "owner": "005930", "key": "latest",
                "document": {"enabled": False, "observed_at": "2026-09-14T09:00:00+09:00", "payload": {"nxtEnable": "N"}},
            }])
            broker = HistoricalBroker()
            service = AutonomousTop20Service(
                broker, RealtimeHub(), store,
                now_provider=lambda: datetime.fromisoformat("2026-09-14T10:00:00+09:00"),
            )

            await service._ensure_historical_high("005930", "2026-09-14")
            await service._ensure_historical_high("005930", "2026-09-14")
            values = store.load_documents("historical_highs", "005930", 1)
            store.close()

        self.assertEqual(90000, values[0]["document"]["target"]["price"])
        self.assertEqual(2, len(broker.calls))

    async def test_nas_backfills_market_indexes_once_per_day(self) -> None:
        class IndexBroker(_Broker):
            async def request(self, api_id, path, body, **kwargs):
                if api_id == "ka20005":
                    self.calls.append((api_id, dict(body), kwargs))
                    return BrokerResult({"inds_min_pole_qry": [{
                        "cntr_tm": "20260914200000", "cur_prc": "100",
                    }]}, False, "")
                if api_id == "ka20006":
                    self.calls.append((api_id, dict(body), kwargs))
                    return BrokerResult({"inds_dt_pole_qry": [{
                        "dt": "20260914", "cur_prc": "100",
                    }]}, False, "")
                return await super().request(api_id, path, body, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            broker = IndexBroker()
            service = AutonomousTop20Service(broker, RealtimeHub(), store)
            await service._backfill_market_indexes("2026-09-14")
            await service._backfill_market_indexes("2026-09-14")
            values = store.load_dataset_snapshots(
                "market_index_chart", "20260914:kospi", 1,
            )
            store.close()

        self.assertEqual(4, len(broker.calls))
        self.assertEqual("20260914200000", values[0]["payload"]["minutes"][0]["cntr_tm"])
    async def test_candidate_flow_capture_and_finalization_are_nas_owned_and_idempotent(self) -> None:
        class FlowBroker(_Broker):
            async def request(self, api_id, path, body, **kwargs):
                if api_id in {"ka10045", "ka90008"}:
                    self.calls.append((api_id, dict(body), kwargs))
                    key = "stk_orgn_trde_trnsn" if api_id == "ka10045" else "stk_tm_prm_trde_trnsn"
                    return BrokerResult({key: [{"dt": "20260914", "cur_prc": "100"}]}, False, "")
                return await super().request(api_id, path, body, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            broker = FlowBroker()
            now = [datetime(2026, 9, 14, 10)]
            service = AutonomousTop20Service(
                broker, RealtimeHub(), store,
                now_provider=lambda: now[0],
            )

            await service._capture_candidate_investor_flow("005930", "2026-09-14")
            await service._capture_candidate_investor_flow("005930", "2026-09-14")
            now[0] = datetime(2026, 9, 14, 20, 5)
            await service._backfill_candidate_flows("005930", "2026-09-14")
            await service._backfill_candidate_flows("005930", "2026-09-14")
            initial = store.load_documents(
                "candidate_flow_capture", "2026-09-14:005930", 1,
            )
            final = store.load_documents(
                "candidate_flow_finalization", "2026-09-14:005930", 1,
            )
            store.close()

        self.assertEqual(["ka10045", "ka90008"], [call[0] for call in broker.calls])
        self.assertEqual("candidate_first_seen", initial[0]["document"]["scope"])
        self.assertEqual("program_after_close", final[0]["document"]["scope"])

    async def test_empty_program_flow_is_recorded_once_only_on_requested_day(self) -> None:
        class EmptyProgramBroker(_Broker):
            async def request(self, api_id, path, body, **kwargs):
                self.calls.append((api_id, dict(body), kwargs))
                if api_id == "ka10045":
                    return BrokerResult({"stk_orgn_trde_trnsn": [{"dt": "20260914"}]}, False, "",
                                        recording_succeeded=True)
                return BrokerResult({"stk_tm_prm_trde_trnsn": []}, False, "",
                                    recording_succeeded=True)

        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            broker = EmptyProgramBroker()
            service = AutonomousTop20Service(
                broker, RealtimeHub(), store,
                now_provider=lambda: datetime.fromisoformat("2026-09-14T20:05:00+09:00"),
            )
            await service._backfill_candidate_flows("005935", "2026-09-14")
            await service._backfill_candidate_flows("005935", "2026-09-14")
            marker = store.load_documents("candidate_flow_finalization", "2026-09-14:005935", 1)
            store.close()

        self.assertEqual(["ka90008"], [call[0] for call in broker.calls])
        self.assertEqual(0, marker[0]["document"]["program_flow_rows"])

    async def test_empty_program_flow_does_not_finalize_unverified_prior_day(self) -> None:
        class EmptyProgramBroker(_Broker):
            async def request(self, api_id, path, body, **kwargs):
                if api_id == "ka10045":
                    return BrokerResult({"stk_orgn_trde_trnsn": [{"dt": "20260914"}]}, False, "",
                                        recording_succeeded=True)
                return BrokerResult({"stk_tm_prm_trde_trnsn": []}, False, "",
                                    recording_succeeded=True)

        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            service = AutonomousTop20Service(
                EmptyProgramBroker(), RealtimeHub(), store,
                now_provider=lambda: datetime.fromisoformat("2026-09-15T05:20:00+09:00"),
            )
            with self.assertRaisesRegex(RuntimeError, "응답이 비었습니다"):
                await service._backfill_candidate_flows("005935", "2026-09-14")
            self.assertEqual([], store.load_documents("candidate_flow_finalization", "2026-09-14:005935", 1))
            store.close()

    async def test_empty_program_flow_with_failed_recording_is_not_finalized(self) -> None:
        class UnrecordedProgramBroker(_Broker):
            async def request(self, api_id, path, body, **kwargs):
                if api_id == "ka10045":
                    return BrokerResult({"stk_orgn_trde_trnsn": [{"dt": "20260914"}]}, False, "",
                                        recording_succeeded=True)
                return BrokerResult({"stk_tm_prm_trde_trnsn": []}, False, "",
                                    recording_succeeded=False)

        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            service = AutonomousTop20Service(
                UnrecordedProgramBroker(), RealtimeHub(), store,
                now_provider=lambda: datetime.fromisoformat("2026-09-14T20:05:00+09:00"),
            )
            with self.assertRaisesRegex(RuntimeError, "원본 저장이 확인되지 않았습니다"):
                await service._backfill_candidate_flows("005935", "2026-09-14")
            self.assertEqual([], store.load_documents("candidate_flow_finalization", "2026-09-14:005935", 1))
            store.close()

    async def test_realtime_program_snapshot_is_archived_without_tr(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            service = AutonomousTop20Service(_Broker(), RealtimeHub(), store)
            service._program_snapshots._pending["005930"] = {
                "subject": "005930", "snapshot_key": "20260914:REALTIME:100100",
                "payload": {"market": "KRX", "rows": [{"trade_time": "100100"}]},
            }

            await service._program_snapshots.flush()
            values = store.load_dataset_snapshots("program_flow", "005930", 10)
            store.close()

        self.assertEqual("100100", values[0]["payload"]["rows"][0]["trade_time"])
    async def test_entry_minute_backfill_is_owned_by_nas_and_runs_once(self) -> None:
        class EntryBroker(_Broker):
            def __init__(self, store) -> None:
                super().__init__()
                self.ingestor = MarketDataIngestor(store)

            async def request(self, api_id, path, body, **kwargs):
                if api_id == "ka10080":
                    self.calls.append((api_id, body, kwargs))
                    payload = {"stk_min_pole_chart_qry": [{
                        "cntr_tm": "20260914100100", "open_pric": "100",
                        "high_pric": "110", "low_pric": "90", "cur_prc": "105",
                        "trde_qty": "10",
                    }]}
                    self.ingestor.ingest(api_id, body, payload)
                    return BrokerResult(payload, False, "", recording_succeeded=True)
                return await super().request(api_id, path, body, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            store.upsert_documents("stock_nxt_eligibility", [{
                "owner": "005930", "key": "latest",
                "document": {"enabled": False, "observed_at": "2026-09-14T09:00:00+09:00", "payload": {"nxtEnable": "N"}},
            }])
            broker = EntryBroker(store)
            service = AutonomousTop20Service(
                broker, RealtimeHub(), store,
                now_provider=lambda: datetime.fromisoformat("2026-09-14T10:02:00+09:00"),
            )

            self.assertTrue(await service._backfill_entry_minutes("005930", "2026-09-14"))
            self.assertTrue(await service._backfill_entry_minutes("005930", "2026-09-14"))
            coverage = store.load_documents(
                "market_data_coverage_intraday", "2026-09-14:005930:KRX", 1,
            )
            store.close()

        minute_calls = [call for call in broker.calls if call[0] == "ka10080"]
        self.assertEqual(1, len(minute_calls))
        self.assertEqual("through_entry", coverage[0]["document"]["scope"])

    async def test_stored_nxt_document_is_reused_after_service_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            store.upsert_documents("stock_nxt_eligibility", [{
                "owner": "005930", "key": "latest",
                "document": {"enabled": True, "observed_at": "2026-09-14T09:00:00+09:00", "payload": {"nxtEnable": "Y"}},
            }])
            broker = _Broker()
            service = AutonomousTop20Service(
                broker, RealtimeHub(), store,
                now_provider=lambda: datetime.fromisoformat("2026-09-14T10:00:00+09:00"),
            )

            enabled = await service._nxt_enabled("005930")
            store.close()

        self.assertTrue(enabled)
        self.assertEqual([], broker.calls)

    async def test_fundamentals_query_runs_once_only_when_central_document_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            store.upsert_documents("stock_fundamentals", [{
                "owner": "005930", "key": "latest",
                "document": {
                    "observed_at": "2026-09-14T09:00:00+09:00",
                    "payload": {"mac": "1000", "dstr_rt": "40"},
                },
            }])
            broker = _Broker()
            service = AutonomousTop20Service(
                broker, RealtimeHub(), store,
                now_provider=lambda: datetime(2026, 9, 14, 10, 0),
            )

            await service._ensure_fundamentals(("005930", "000660"))
            await service._ensure_fundamentals(("005930", "000660"))
            store.close()

        fundamental_calls = [call for call in broker.calls if call[0] == "ka10001"]
        self.assertEqual([("ka10001", {"stk_cd": "000660"}, {})], fundamental_calls)

    async def test_stale_fundamentals_are_refreshed_once_for_the_current_day(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            store.upsert_documents("stock_fundamentals", [{
                "owner": "005930", "key": "latest",
                "document": {
                    "observed_at": "2026-09-13T20:00:00+09:00",
                    "payload": {"mac": "900"},
                },
            }])
            broker = _Broker()
            service = AutonomousTop20Service(
                broker, RealtimeHub(), store,
                now_provider=lambda: datetime(2026, 9, 14, 10, 0),
            )

            await service._ensure_fundamentals(("005930",))
            await service._ensure_fundamentals(("005930",))
            store.close()

        fundamental_calls = [call for call in broker.calls if call[0] == "ka10001"]
        self.assertEqual(1, len(fundamental_calls))

    async def test_missing_entry_daily_history_is_requested_by_nas_once(self) -> None:
        class DailyBroker(_Broker):
            def __init__(self, store) -> None:
                super().__init__()
                self.ingestor = MarketDataIngestor(store)

            async def request(self, api_id, path, body, **kwargs):
                self.calls.append((api_id, body, kwargs))
                if api_id == "ka10081":
                    payload = {"stk_dt_pole_chart_qry": [{
                        "dt": "20260914", "open_pric": "100", "high_pric": "110",
                        "low_pric": "90", "cur_prc": "105", "trde_qty": "10",
                    }]}
                    self.ingestor.ingest(api_id, body, payload)
                    return BrokerResult(payload, False, "", recording_succeeded=True)
                return await super().request(api_id, path, body, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            broker = DailyBroker(store)
            service = AutonomousTop20Service(broker, RealtimeHub(), store)
            service._nxt_eligible["005930"] = False
            service._nxt_checked_on["005930"] = service._now().date().isoformat()

            await service._ensure_entry_daily_history("005930", "2026-09-14")
            store.close()

        calls = [call for call in broker.calls if call[0] == "ka10081"]
        self.assertEqual(1, len(calls))
        self.assertEqual("005930", calls[0][1]["stk_cd"])

    def _entrant_service(self, store, broker, now=None):
        service = AutonomousTop20Service(
            broker, RealtimeHub(), store,
            now_provider=lambda: now or datetime.fromisoformat("2026-09-11T07:00:00+09:00"),
        )
        service._schedule_market_catalog = lambda _day: None
        service._schedule_subscription_update = lambda _day: None
        service.after_close._backfill_market_indexes = AsyncMock()
        service.after_close._nxt_enabled = AsyncMock(return_value=False)
        service.after_close._backfill_daily = AsyncMock()
        service.after_close._backfill_minutes = AsyncMock()
        service.after_close._backfill_candidate_flows = AsyncMock()
        return service

    async def test_daily_entrants_retry_departed_code_skip_replay_and_reset_next_day(self) -> None:
        class Broker(_Broker):
            clock = "090000"
            day = "20260910"
            first = "005930"

            async def request(self, *args, **kwargs):
                rows = _ranking_rows(self.clock)
                for row in rows:
                    row["dt"] = self.day
                rows[0]["stk_cd"] = self.first
                return BrokerResult({"item_inq_rank": rows}, False, "")

        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            self.addCleanup(store.close)
            broker = Broker()
            service = self._entrant_service(store, broker)
            original_save = store.upsert_documents
            with patch.object(store, "upsert_documents", side_effect=RuntimeError("save failed")):
                with self.assertRaisesRegex(RuntimeError, "save failed"):
                    await service.refresh_ranking_once(datetime(2026, 9, 10, 9, 0))
            self.assertEqual(set(), service._entrant_persisted_codes)
            broker.clock, broker.first = "090030", "099999"
            with patch.object(store, "upsert_documents", wraps=original_save) as save:
                await service.refresh_ranking_once(datetime(2026, 9, 10, 9, 0, 30))
                self.assertEqual(21, len(save.call_args.args[1]))
                broker.clock = "090100"
                await service.refresh_ranking_once(datetime(2026, 9, 10, 9, 1))
                self.assertEqual(1, save.call_count)
                restarted = self._entrant_service(store, broker)
                await restarted.refresh_ranking_once(datetime(2026, 9, 10, 9, 1))
                self.assertEqual(1, save.call_count)
                broker.day = "20260911"
                await service.refresh_ranking_once(datetime(2026, 9, 11, 9, 1))
                self.assertEqual(2, save.call_count)
                self.assertEqual({"2026-09-11"}, {row["owner"] for row in save.call_args.args[1]})
            documents = store.load_documents("top20_daily_entrants", "2026-09-10")
            samsung = next(row for row in documents if row["key"] == "005930")
            self.assertEqual("2026-09-10T09:00:00", samsung["document"]["first_seen_at"])
            self.assertNotIn("last_seen_at", samsung["document"])
            self.assertEqual(3, len(store.load_dataset_snapshots("top20_membership", "2026-09-10")))
            store.close()

    async def test_backfill_recovers_failed_entrant_from_corrected_membership_history(self) -> None:
        class Broker(_Broker):
            first = "005930"

            async def request(self, *args, **kwargs):
                rows = _ranking_rows("090000")
                rows[0]["stk_cd"] = self.first
                return BrokerResult({"item_inq_rank": rows}, False, "")

        for history_enabled in (True, False):
            with self.subTest(history_enabled=history_enabled), tempfile.TemporaryDirectory() as directory:
                store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3",
                                         observation_history_enabled=history_enabled)
                store.initialize()
                broker = Broker()
                service = self._entrant_service(store, broker)
                with patch.object(store, "upsert_documents", side_effect=RuntimeError("save failed")):
                    for first in ("005930", "099999"):
                        broker.first = first
                        with self.assertRaisesRegex(RuntimeError, "save failed"):
                            await service.refresh_ranking_once(datetime(2026, 9, 10, 9, 0))
                restarted = self._entrant_service(store, broker)
                self.assertTrue(await restarted.after_close.run_day("2026-09-10"))
                codes = {row["key"] for row in store.load_documents("top20_daily_entrants", "2026-09-10")}
                self.assertIn("099999", codes)
                self.assertEqual(history_enabled, "005930" in codes)
                self.assertEqual(codes, {call.args[0] for call in restarted.after_close._backfill_daily.await_args_list})
                self.assertEqual("", restarted._entrants_day)
                store.close()

    async def test_previous_day_backfill_and_current_ranking_keep_separate_entrant_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            old_day = "2026-09-09"
            store.save_dataset_snapshot("top20_membership", old_day, old_day + "T09:00:00", {
                "observed_at": old_day + "T09:00:00", "codes": ["OLD"],
            })
            service = self._entrant_service(store, _Broker(), datetime(2026, 9, 10, 7, 0))
            entered, release = threading.Event(), threading.Event()
            original_save = store.upsert_documents

            def blocked_save(collection, documents):
                if documents[0]["owner"] == old_day:
                    entered.set()
                    if not release.wait(5):
                        raise TimeoutError("backfill save not released")
                return original_save(collection, documents)

            with patch.object(store, "upsert_documents", side_effect=blocked_save):
                task = asyncio.create_task(service.after_close.run_day(old_day))
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                    codes = await service.refresh_ranking_once(datetime(2026, 9, 10, 9, 0))
                    self.assertEqual(set(codes), service._entrant_persisted_codes)
                    self.assertEqual("2026-09-10", service._entrants_day)
                finally:
                    release.set()
                    completed = await task
                self.assertTrue(completed)
            previous = store.load_documents("top20_daily_entrants", old_day)
            current = store.load_documents("top20_daily_entrants", "2026-09-10")
            self.assertEqual(["OLD"], [row["key"] for row in previous])
            self.assertEqual(old_day + "T09:00:00", previous[0]["document"]["first_seen_at"])
            self.assertEqual(set(codes), {row["key"] for row in current})
            self.assertTrue(all(row["document"]["first_seen_at"].startswith("2026-09-10") for row in current))
            self.assertEqual(["OLD"], [call.args[0] for call in service.after_close._backfill_daily.await_args_list])
            store.close()

    async def test_daily_entrant_recovery_does_not_silently_accept_truncated_history(self) -> None:
        store = MagicMock()
        store.load_documents.return_value = []
        store.load_dataset_snapshots.return_value = [{}] * 5000
        store.load_observation_revisions.return_value = []
        service = self._entrant_service(store, _Broker())
        with self.assertRaisesRegex(RuntimeError, "recovery limit reached"):
            await service.after_close.run_day("2026-09-10")
        store.upsert_documents.assert_not_called()
        service.after_close._backfill_daily.assert_not_awaited()

    async def test_ranking_is_archived_and_keeps_an_internal_subscription(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            hub = RealtimeHub()
            broker = _Broker()
            service = AutonomousTop20Service(
                broker, hub, store,
                now_provider=lambda: datetime(2026, 9, 10, 9, 0),
                catalog_loader=lambda: (
                    ("005930", "삼성전자", "KOSPI"),
                    ("000660", "SK하이닉스", "KOSPI"),
                ),
            )
            service._subscriber = hub.connect()

            codes = await service.refresh_ranking_once(datetime(2026, 9, 10, 9, 0, 0))
            subscription_task = service._subscription_task
            catalog_task = service._market_catalog_task
            assert subscription_task is not None
            await subscription_task
            if catalog_task is not None:
                await catalog_task

            snapshots = store.load_dataset_snapshots("top20_membership", "2026-09-10")
            metadata = store.load_market_data_metadata(
                MarketDatasetKind.CANDIDATE_SET,
                "2026-09-10",
                "2026-09-10T09:00:00",
            )
            entrants = store.load_documents("top20_daily_entrants", "2026-09-10")
            requested, nxt = hub.requested_codes()
            store.close()
        self.assertEqual(("005930", "000660"), codes[:2])
        self.assertEqual(20, len(codes))
        self.assertEqual(["005930", "000660"], snapshots[0]["payload"]["codes"][:2])
        self.assertIsNotNone(metadata)
        assert metadata is not None
        self.assertEqual(DataCompleteness.COMPLETE, metadata.completeness)
        self.assertEqual(ObservationOrigin.QUERY, metadata.origin)
        self.assertEqual(set(codes), {row["key"] for row in entrants})
        self.assertEqual(set(codes), set(requested))
        self.assertEqual(("005930",), nxt)
        self.assertEqual("KOSPI", service._markets["005930"])

    async def test_slow_catalog_and_nxt_enrichment_do_not_block_next_ranking(self) -> None:
        release_catalog = threading.Event()
        release_nxt = asyncio.Event()

        class SlowEnrichmentBroker(_Broker):
            async def request(self, api_id, path, body, **kwargs):
                if api_id == "ka10100":
                    await release_nxt.wait()
                if api_id == "ka00198":
                    self.calls.append((api_id, body, kwargs))
                    clock = "090030" if len([
                        call for call in self.calls if call[0] == "ka00198"
                    ]) >= 2 else "090000"
                    return BrokerResult({"item_inq_rank": _ranking_rows(clock)}, False, "")
                return await super().request(api_id, path, body, **kwargs)

        def slow_catalog():
            release_catalog.wait(5)
            return (("005930", "삼성전자", "KOSPI"),)

        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            broker = SlowEnrichmentBroker()
            hub = RealtimeHub()
            now = [datetime(2026, 9, 10, 9, 0)]
            service = AutonomousTop20Service(
                broker, hub, store, catalog_loader=slow_catalog,
                now_provider=lambda: now[0],
            )
            service._subscriber = hub.connect()
            try:
                await asyncio.wait_for(
                    service.refresh_ranking_once(datetime(2026, 9, 10, 9, 0, 0)),
                    timeout=0.5,
                )
                now[0] = datetime(2026, 9, 10, 9, 0, 30)
                await asyncio.wait_for(
                    service.refresh_ranking_once(datetime(2026, 9, 10, 9, 0, 30)),
                    timeout=0.5,
                )
                ranking_calls = [call for call in broker.calls if call[0] == "ka00198"]
                requested, _nxt = hub.requested_codes()
                self.assertEqual(2, len(ranking_calls))
                self.assertEqual(20, len(requested))
                self.assertEqual({"005930", "000660"}, set(requested[:2]))
            finally:
                release_catalog.set()
                release_nxt.set()
                while service._fundamentals_tasks:
                    await asyncio.gather(
                        *tuple(service._fundamentals_tasks), return_exceptions=True,
                    )
                await service.close()
                store.close()

    async def test_saved_catalog_is_reused_without_network_and_classifies_markets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            store.replace_documents("stock_catalog", [
                {"owner": "krx", "key": "005930", "document": {
                    "code": "005930", "name": "삼성전자", "market": "KOSPI",
                    "as_of": "2026-09-10",
                }},
                {"owner": "krx", "key": "000660", "document": {
                    "code": "000660", "name": "SK하이닉스", "market": "KOSDAQ",
                    "as_of": "2026-09-10",
                }},
                {"owner": "krx", "key": "_meta", "document": {
                    "as_of": "2026-09-10", "rows": 2,
                }},
            ])
            service = AutonomousTop20Service(
                _Broker(), RealtimeHub(), store,
                catalog_loader=lambda: (_ for _ in ()).throw(AssertionError("network")),
            )
            await service._ensure_market_catalog("2026-09-10")
            store.close()
        self.assertEqual({"005930": "KOSPI", "000660": "KOSDAQ"}, service._markets)

    async def test_catalog_loader_duplicate_codes_are_saved_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            service = AutonomousTop20Service(
                _Broker(), RealtimeHub(), store,
                catalog_loader=lambda: (
                    ("0001A0", "중복종목", "KOSPI"),
                    ("0001A0", "중복종목", "KOSPI"),
                    ("005930", "삼성전자", "KOSPI"),
                ),
            )
            await service._ensure_market_catalog("2026-09-14")
            values = store.load_documents("stock_catalog", "krx", 100)
            store.close()

        self.assertEqual(3, len(values))
        self.assertEqual(2, next(
            value["document"]["rows"] for value in values if value["key"] == "_meta"
        ))

    def test_collection_window_is_weekday_0800_until_2000(self) -> None:
        self.assertTrue(_collection_open(datetime(2026, 9, 10, 8, 0)))
        self.assertTrue(_collection_open(datetime(2026, 9, 10, 19, 59, 59)))
        self.assertFalse(_collection_open(datetime(2026, 9, 10, 20, 0)))
        self.assertFalse(_collection_open(datetime(2026, 9, 12, 10, 0)))

    def test_ranking_collection_remains_due_overnight_and_on_weekends(self) -> None:
        self.assertTrue(_ranking_collection_due(datetime(2026, 9, 15, 1, 5, 30)))
        self.assertTrue(_ranking_collection_due(datetime(2026, 9, 12, 10, 0)))
        self.assertFalse(_ranking_collection_due(datetime(2026, 9, 15, 1, 5, 29)))

    def test_top20_collection_is_paused_when_nas_loses_kiwoom_realtime(self) -> None:
        hub = RealtimeHub()
        service = AutonomousTop20Service(_Broker(), hub, _FlakyIndexStore())  # type: ignore[arg-type]
        service._collector.prepare(
            ("005930",), datetime(2026, 9, 10, 9, 0),
            enabled=True, collection_open=True,
        )

        update = service._advance(datetime(2026, 9, 10, 9, 0, 30))

        self.assertFalse(update.collection_open)
        self.assertEqual((), service._collector.active_codes)

    async def test_effective_date_full_day_backfill_waits_for_2000(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            broker = _Broker()
            service = AutonomousTop20Service(
                broker, RealtimeHub(), store,
                now_provider=lambda: datetime(2026, 9, 14, 15, 35),
            )

            await service.after_close.run_day("2026-09-14")
            store.close()

        self.assertEqual([], broker.calls)

    async def test_legacy_minute_coverage_is_upgraded_before_it_is_reused(self) -> None:
        class MinuteBroker:
            def __init__(self, store) -> None:
                self.calls = 0
                self.ingestor = MarketDataIngestor(store)

            async def request(self, api_id, path, body, **kwargs):
                self.calls += 1
                payload = {"stk_min_pole_chart_qry": [{
                    "cntr_tm": "20260914195900", "open_pric": "1",
                    "high_pric": "1", "low_pric": "1", "cur_prc": "1",
                    "trde_qty": "1",
                }]}
                self.ingestor.ingest(api_id, body, payload)
                return BrokerResult(payload, False, "", recording_succeeded=True)

        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            store.upsert_documents("market_data_coverage", [{
                "owner": "2026-09-14:001210:KRX", "key": "complete",
                "document": {"kind": "minute", "pages": 2},
            }])
            broker = MinuteBroker(store)
            service = AutonomousTop20Service(
                broker, RealtimeHub(), store,
                now_provider=lambda: datetime(2026, 9, 15, 5, 20),
            )

            await service._backfill_minutes("001210", "2026-09-14", "KRX")
            await service._backfill_minutes("001210", "2026-09-14", "KRX")
            coverage = store.load_documents(
                "market_data_coverage", "2026-09-14:001210:KRX", 1,
            )
            store.close()

        self.assertEqual(1, broker.calls)
        self.assertTrue(coverage[0]["document"]["window_closed"])
        self.assertTrue(coverage[0]["document"]["session_finalized"])

    async def test_failed_minute_storage_never_finalizes_coverage(self) -> None:
        class UnrecordedMinuteBroker:
            async def request(self, api_id, path, body, **kwargs):
                return BrokerResult({"stk_min_pole_chart_qry": [{
                    "cntr_tm": "20260914195900", "open_pric": "1",
                    "high_pric": "1", "low_pric": "1", "cur_prc": "1", "trde_qty": "1",
                }]}, False, "", recording_succeeded=False)

        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            service = AutonomousTop20Service(UnrecordedMinuteBroker(), RealtimeHub(), store)
            with self.assertRaisesRegex(RuntimeError, "원본 적재"):
                await service._backfill_minutes("001210", "2026-09-14", "KRX")
            self.assertEqual([], store.load_documents("market_data_coverage", "2026-09-14:001210:KRX", 1))
            store.close()

    async def test_completed_index_enters_outbox_before_membership_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            original_save = store.save_dataset_snapshot

            def fail_membership(collection, *args, **kwargs):
                if collection == "top20_membership":
                    raise RuntimeError("membership unavailable")
                return original_save(collection, *args, **kwargs)

            store.save_dataset_snapshot = fail_membership  # type: ignore[method-assign]
            outbox = Path(directory) / "top20-outbox.json"
            service = AutonomousTop20Service(_Broker(), RealtimeHub(), store, outbox_path=outbox)
            # Keep this ordering assertion focused on index persistence; these
            # unrelated scheduled readers would outlive this temporary store.
            service._schedule_market_catalog = lambda _day: None
            service._schedule_subscription_update = lambda _day: None
            service._collector.minute = datetime(2026, 9, 10, 8, 59)
            service._collector.samples = [(30, 0.0)]
            service._collector.active_codes = ("005930",)
            service._collector.minute_codes = {"005930"}
            try:
                with self.assertRaisesRegex(RuntimeError, "membership unavailable"):
                    await service.refresh_ranking_once(datetime(2026, 9, 10, 9, 0))
                self.assertIn("2026-09-10T08:59", service._pending_index_records)
                self.assertIn("2026-09-10T08:59", outbox.read_text(encoding="utf-8"))
            finally:
                try:
                    await service.close()
                finally:
                    store.close()

    async def test_empty_nxt_daily_response_is_not_marked_complete(self) -> None:
        class EmptyDailyBroker:
            async def request(self, api_id, path, body, **kwargs):
                self.body = body
                return BrokerResult({"stk_dt_pole_chart_qry": []}, False, "")

        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            service = AutonomousTop20Service(EmptyDailyBroker(), RealtimeHub(), store)

            with self.assertRaisesRegex(RuntimeError, "NXT 일봉"):
                await service._backfill_daily("000660", "2026-09-09", "NXT")

            coverage = store.load_documents("market_data_coverage_daily", "000660:NXT", 1)
            store.close()
        self.assertEqual([], coverage)

    async def test_daily_coverage_requires_the_requested_day(self) -> None:
        class OlderDailyBroker:
            async def request(self, api_id, path, body, **kwargs):
                return BrokerResult({"stk_dt_pole_chart_qry": [{
                    "dt": "20260913", "open_pric": "1", "high_pric": "1",
                    "low_pric": "1", "cur_prc": "1", "trde_qty": "1",
                }]}, False, "")

        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            service = AutonomousTop20Service(
                OlderDailyBroker(), RealtimeHub(), store,
                now_provider=lambda: datetime(2026, 9, 14, 20, 5),
            )

            with self.assertRaisesRegex(RuntimeError, "KRX 일봉"):
                await service._backfill_daily("005930", "2026-09-14", "KRX")

            coverage = store.load_documents("market_data_coverage_daily", "005930:KRX", 1)
            store.close()
        self.assertEqual([], coverage)

    async def test_top20_index_keeps_partial_capture_metadata(self) -> None:
        from kiwoom_monitor.application.top20_trade_value_collector import Top20MinuteRecord

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            store = SQLiteQueryStore(path)
            store.initialize()
            now = datetime(2026, 9, 10, 10, 2)
            service = AutonomousTop20Service(
                _Broker(), RealtimeHub(), store, now_provider=lambda: now
            )
            record = Top20MinuteRecord(
                minute=datetime(2026, 9, 10, 10, 1),
                market_values=(10.0, 20.0, 0.0),
                codes=("005930", "000660"),
                market_counts=(1, 1, 0),
                cohort_segments=(),
                capture_state="partial",
            )

            await service._save_index(record)
            metadata = store.load_market_data_metadata(
                MarketDatasetKind.TOP20_INDEX,
                "2026-09-10",
                "2026-09-10T10:01",
            )
            store.close()

        self.assertIsNotNone(metadata)
        assert metadata is not None
        self.assertEqual(DataCompleteness.PARTIAL, metadata.completeness)
        self.assertEqual(ObservationOrigin.REALTIME, metadata.origin)

    async def test_failed_top20_index_save_is_retained_for_retry(self) -> None:
        from kiwoom_monitor.application.top20_trade_value_collector import Top20MinuteRecord

        store = _FlakyIndexStore()
        service = AutonomousTop20Service(_Broker(), RealtimeHub(), store)  # type: ignore[arg-type]
        record = Top20MinuteRecord(
            minute=datetime(2026, 9, 10, 10, 1),
            market_values=(10.0, 20.0, 0.0),
            codes=("005930", "000660"),
            market_counts=(1, 1, 0),
            cohort_segments=(),
            capture_state="complete",
        )

        with self.assertRaisesRegex(RuntimeError, "temporary database failure"):
            await service._save_index(record)
        self.assertEqual(1, len(service._pending_index_records))

        await service._flush_pending_indexes()

        self.assertEqual({}, service._pending_index_records)
        self.assertEqual(1, len(store.saved))

    async def test_failed_top20_index_is_recovered_after_service_restart(self) -> None:
        from kiwoom_monitor.application.top20_trade_value_collector import Top20MinuteRecord

        with tempfile.TemporaryDirectory() as directory:
            outbox = Path(directory) / "top20-outbox.json"
            failed_store = _FlakyIndexStore()
            first = AutonomousTop20Service(
                _Broker(), RealtimeHub(), failed_store, outbox_path=outbox,
            )  # type: ignore[arg-type]
            record = Top20MinuteRecord(
                minute=datetime(2026, 9, 10, 10, 1),
                market_values=(10.0, 20.0, 0.0), codes=("005930",),
                market_counts=(1, 0, 0), cohort_segments=(), capture_state="complete",
            )
            with self.assertRaisesRegex(RuntimeError, "temporary database failure"):
                await first._save_index(record)

            recovered_store = _FlakyIndexStore()
            recovered_store.calls = 1
            second = AutonomousTop20Service(
                _Broker(), RealtimeHub(), recovered_store, outbox_path=outbox,
            )  # type: ignore[arg-type]
            await second.start()
            await second.close()

            self.assertEqual(1, len(recovered_store.saved))
            self.assertEqual({}, second._pending_index_records)
            self.assertEqual("{}", outbox.read_text(encoding="utf-8"))

    async def test_index_drain_waits_for_outbox_registration(self) -> None:
        from kiwoom_monitor.application.top20_trade_value_collector import Top20MinuteRecord

        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            outbox = Path(directory) / "top20-outbox.json"
            service = AutonomousTop20Service(_Broker(), RealtimeHub(), store, outbox_path=outbox)
            assert service._outbox is not None
            original_put = service._outbox.put
            entered = threading.Event()
            release = threading.Event()

            def slow_put(key, payload):
                entered.set()
                release.wait(2)
                original_put(key, payload)

            service._outbox.put = slow_put  # type: ignore[method-assign]
            record = Top20MinuteRecord(
                datetime(2026, 9, 10, 10, 1), (1.0, 0.0, 0.0),
                ("005930",), (1, 0, 0), (), "partial",
            )
            queued = asyncio.create_task(service._queue_index(record))
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            drained = asyncio.create_task(service._flush_pending_indexes())
            await asyncio.sleep(0.05)
            self.assertFalse(drained.done())
            release.set()
            await asyncio.gather(queued, drained)
            self.assertEqual({}, service._pending_index_records)
            self.assertEqual("{}", outbox.read_text(encoding="utf-8"))
            store.close()


class _BlockedIndexWrite:
    def __init__(self, native):
        self.native = native
        self.entered, self.release = threading.Event(), threading.Event()
        self.condition = threading.Condition()
        self.calls = self.completed = 0

    def __call__(self, *args, **kwargs):
        with self.condition:
            self.calls += 1
        self.entered.set()
        try:
            if not self.release.wait(3):
                raise TimeoutError("test index write was not released")
            return self.native(*args, **kwargs)
        finally:
            with self.condition:
                self.completed += 1
                self.condition.notify_all()

    def wait_completed(self):
        with self.condition:
            return self.condition.wait_for(lambda: self.completed == self.calls, 3)


class Top20IndexFlushOwnershipTests(unittest.IsolatedAsyncioTestCase):
    def _store_and_service(self, directory):
        now = datetime.fromisoformat("2026-09-10T10:02:00+09:00")
        store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3", now_provider=lambda: now)
        store.initialize()
        service = AutonomousTop20Service(
            _Broker(), RealtimeHub(), store, now_provider=lambda: now,
            outbox_path=Path(directory) / "index-outbox.json",
        )
        return store, service

    def _record(self, total=10.0):
        from kiwoom_monitor.application.top20_trade_value_collector import Top20MinuteRecord
        return Top20MinuteRecord(
            datetime.fromisoformat("2026-09-10T10:01:00+09:00"), (total, 0.0, 0.0),
            ("005930",), (1, 0, 0), (), "partial",
        )

    async def test_unreadable_outbox_start_preserves_source_before_task_start_and_recovers(self):
        import json
        for original, expected in ((b'{"old":', json.JSONDecodeError), (b"[]", ValueError)):
            with self.subTest(original=original), tempfile.TemporaryDirectory() as directory:
                store, service = self._store_and_service(directory)
                path = Path(directory) / "index-outbox.json"
                await service._queue_index(self._record())
                valid = path.read_bytes()
                path.write_bytes(original)
                try:
                    with patch.object(store, "save_dataset_snapshot", wraps=store.save_dataset_snapshot) as save:
                        caught = None
                        try:
                            await service.start()
                        except Exception as error:
                            caught = error
                        self.assertEqual(original, path.read_bytes())
                        self.assertIsInstance(caught, expected)
                        self.assertEqual([], service._tasks)
                        self.assertIsNone(service._subscriber)
                        save.assert_not_called()
                    await service.close()
                    self.assertEqual(original, path.read_bytes())
                    # Explicitly repair this owned temporary fixture; no automatic fallback policy.
                    path.write_bytes(valid)
                    await service.start()
                    await service.close()
                    rows = store.load_dataset_snapshots("top20_index", "2026-09-10", 10)
                    self.assertEqual([10.0], [row["payload"]["total"] for row in rows])
                    self.assertEqual({}, service._outbox.load())
                    self.assertEqual({}, service._pending_index_records)
                finally:
                    await service.close()
                    store.close()


    async def test_outbox_read_failure_keeps_queue_or_native_ack_pending_until_retry(self):
        from dataclasses import replace
        for boundary in ("put", "remove"):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as directory:
                store, service = self._store_and_service(directory)
                path = Path(directory) / "index-outbox.json"
                await service._queue_index(self._record(10.0))
                original = path.read_bytes()
                try:
                    with patch.object(Path, "read_text", side_effect=OSError("controlled outbox read failure")):
                        with self.assertRaisesRegex(OSError, "controlled outbox read failure"):
                            if boundary == "put":
                                later = replace(self._record(20.0), minute=datetime.fromisoformat("2026-09-10T10:02:00+09:00"))
                                await service._queue_index(later)
                            else:
                                await service._flush_pending_indexes()
                    self.assertEqual(original, path.read_bytes())
                    self.assertEqual(2 if boundary == "put" else 1, len(service._pending_index_records))
                    self.assertEqual(0 if boundary == "put" else 1,
                                     len(store.load_dataset_snapshots("top20_index", "2026-09-10", 10)))
                    if boundary == "put":
                        await service.start()  # Restored disk input and current RAM input both save.
                    else:
                        await service._flush_pending_indexes()  # Retry a committed save whose ACK removal failed.
                    await service.close()
                    self.assertEqual({}, service._outbox.load())
                    self.assertEqual({}, service._pending_index_records)
                    rows = store.load_dataset_snapshots("top20_index", "2026-09-10", 10)
                    self.assertEqual([10.0, 20.0] if boundary == "put" else [10.0],
                                     sorted(row["payload"]["total"] for row in rows))
                finally:
                    await service.close()
                    store.close()


    async def test_concurrent_index_flushes_share_one_actual_native_save(self):
        with tempfile.TemporaryDirectory() as directory:
            store, service = self._store_and_service(directory)
            blocked = _BlockedIndexWrite(store.save_dataset_snapshot)
            store.save_dataset_snapshot = blocked
            tasks = []
            try:
                await service._queue_index(self._record())
                tasks.append(asyncio.create_task(service._flush_pending_indexes()))
                self.assertTrue(await asyncio.to_thread(blocked.entered.wait, 1))
                tasks.append(asyncio.create_task(service._flush_pending_indexes()))
                await asyncio.sleep(.03)
                blocked.release.set()
                await asyncio.gather(*tasks)
                self.assertEqual(1, blocked.calls)
                self.assertEqual({}, service._pending_index_records)
                self.assertEqual({}, service._outbox.load())
                rows = store.load_dataset_snapshots("top20_index", "2026-09-10", 10)
                self.assertEqual(1, len(rows))
                self.assertEqual(10.0, rows[0]["payload"]["total"])
            finally:
                blocked.release.set()
                await asyncio.gather(*tasks, return_exceptions=True)
                self.assertTrue(await asyncio.to_thread(blocked.wait_completed))
                store.close()

    async def test_newer_index_payload_survives_an_old_inflight_ack_and_joiner(self):
        with tempfile.TemporaryDirectory() as directory:
            store, service = self._store_and_service(directory)
            blocked = _BlockedIndexWrite(store.save_dataset_snapshot)
            store.save_dataset_snapshot = blocked
            tasks = []
            try:
                await service._queue_index(self._record(10.0))
                tasks.append(asyncio.create_task(service._flush_pending_indexes()))
                self.assertTrue(await asyncio.to_thread(blocked.entered.wait, 1))
                await service._queue_index(self._record(20.0))
                tasks.append(asyncio.create_task(service._flush_pending_indexes()))
                await asyncio.sleep(.03)
                blocked.release.set()
                await asyncio.gather(*tasks)
                # The second caller's entry cut must be durable when it returns.
                self.assertEqual(2, blocked.calls)
                self.assertEqual({}, service._pending_index_records)
                self.assertEqual({}, service._outbox.load())
                self.assertEqual(20.0, store.load_dataset_snapshots("top20_index", "2026-09-10", 1)[0]["payload"]["total"])
            finally:
                blocked.release.set()
                await asyncio.gather(*tasks, return_exceptions=True)
                self.assertTrue(await asyncio.to_thread(blocked.wait_completed))
                store.close()

    async def test_flush_entry_cut_excludes_later_keys_and_preserves_unjoined_payload(self):
        from dataclasses import replace
        with tempfile.TemporaryDirectory() as directory:
            store, service = self._store_and_service(directory)
            blocked = _BlockedIndexWrite(store.save_dataset_snapshot)
            store.save_dataset_snapshot = blocked
            tasks = []
            try:
                await service._queue_index(self._record(10.0))
                tasks.append(asyncio.create_task(service._flush_pending_indexes()))
                self.assertTrue(await asyncio.to_thread(blocked.entered.wait, 1))
                await service._queue_index(self._record(20.0))
                # With no newer caller yet, the old save's ACK cannot consume new input.
                blocked.release.set()
                await tasks[0]
                self.assertEqual(1, blocked.calls)
                self.assertEqual([20.0], [p["total"] for p in service._pending_index_records.values()])
                self.assertEqual([20.0], [p["total"] for p in service._outbox.load().values()])

                blocked.entered.clear()
                blocked.release.clear()
                tasks.append(asyncio.create_task(service._flush_pending_indexes()))
                self.assertTrue(await asyncio.to_thread(blocked.entered.wait, 1))
                await service._queue_index(self._record(25.0))
                tasks.append(asyncio.create_task(service._flush_pending_indexes()))
                await asyncio.sleep(.03)  # The waiting caller has captured the existing key.
                later = replace(self._record(30.0), minute=datetime.fromisoformat("2026-09-10T10:02:00+09:00"))
                await service._queue_index(later)
                blocked.release.set()
                await asyncio.gather(*tasks)
                self.assertEqual(3, blocked.calls)
                rows = store.load_dataset_snapshots("top20_index", "2026-09-10", 10)
                self.assertEqual([25.0], [row["payload"]["total"] for row in rows])
                self.assertEqual([30.0], [p["total"] for p in service._pending_index_records.values()])
                self.assertEqual([30.0], [p["total"] for p in service._outbox.load().values()])
            finally:
                blocked.release.set()
                await asyncio.gather(*tasks, return_exceptions=True)
                self.assertTrue(await asyncio.to_thread(blocked.wait_completed))
                store.close()

    async def test_inflight_failure_preserves_newer_cut_then_explicit_retry_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            store, service = self._store_and_service(directory)
            connection = store._connect()
            try:
                connection.execute("CREATE TRIGGER reject_index BEFORE INSERT ON central_dataset_snapshots "
                                   "BEGIN SELECT RAISE(ABORT,'controlled prior owner failure'); END")
                connection.commit()
            finally:
                connection.close()
            blocked = _BlockedIndexWrite(store.save_dataset_snapshot)
            store.save_dataset_snapshot = blocked
            tasks = []
            try:
                await service._queue_index(self._record(10.0))
                tasks.append(asyncio.create_task(service._flush_pending_indexes()))
                self.assertTrue(await asyncio.to_thread(blocked.entered.wait, 1))
                tasks[0].cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await tasks[0]
                await service._queue_index(self._record(20.0))
                tasks.append(asyncio.create_task(service._flush_pending_indexes()))
                await asyncio.sleep(.03)
                self.assertEqual(1, blocked.calls)
                blocked.release.set()
                with self.assertRaisesRegex(Exception, "controlled prior owner failure"):
                    await tasks[1]
                self.assertEqual(1, blocked.calls)  # Joining a failed active owner adds no implicit retry.
                self.assertEqual([20.0], [p["total"] for p in service._pending_index_records.values()])
                self.assertEqual([20.0], [p["total"] for p in service._outbox.load().values()])
                self.assertEqual([], store.load_dataset_snapshots("top20_index", "2026-09-10", 10))
                connection = store._connect()
                try:
                    connection.execute("DROP TRIGGER reject_index")
                    connection.commit()
                finally:
                    connection.close()
                # A new explicit call retries pending data rather than re-awaiting the failed task forever.
                await service._flush_pending_indexes()
                self.assertEqual(2, blocked.calls)
                self.assertEqual({}, service._pending_index_records)
                self.assertEqual({}, service._outbox.load())
                self.assertEqual(20.0, store.load_dataset_snapshots("top20_index", "2026-09-10", 1)[0]["payload"]["total"])
                await service.close()
                self.assertEqual(2, blocked.calls)
            finally:
                blocked.release.set()
                await asyncio.gather(*tasks, return_exceptions=True)
                self.assertTrue(await asyncio.to_thread(blocked.wait_completed))
                store.close()

    async def test_cancelled_index_waiters_and_close_do_not_abandon_or_overlap_native_save(self):
        for cancel_all, late_failure in ((False, False), (True, False), (True, True)):
            with self.subTest(cancel_all=cancel_all, late_failure=late_failure), tempfile.TemporaryDirectory() as directory:
                store, service = self._store_and_service(directory)
                if late_failure:
                    connection = store._connect()
                    try:
                        connection.execute("CREATE TRIGGER reject_index BEFORE INSERT ON central_dataset_snapshots "
                                           "BEGIN SELECT RAISE(ABORT,'controlled late index failure'); END")
                        connection.commit()
                    finally:
                        connection.close()
                blocked = _BlockedIndexWrite(store.save_dataset_snapshot)
                store.save_dataset_snapshot = blocked
                waiters, closing = [], None
                try:
                    await service._queue_index(self._record())
                    waiters.append(asyncio.create_task(service._flush_pending_indexes()))
                    self.assertTrue(await asyncio.to_thread(blocked.entered.wait, 1))
                    waiters.append(asyncio.create_task(service._flush_pending_indexes()))
                    await asyncio.sleep(.03)
                    for waiter in waiters if cancel_all else waiters[:1]:
                        waiter.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await waiter
                    closing = asyncio.create_task(service.close())
                    await asyncio.sleep(.03)
                    self.assertFalse(closing.done())
                    self.assertEqual(1, blocked.calls)
                    if late_failure:
                        with self.assertLogs("kiwoom_monitor.central_server.autonomous_top20", level="WARNING") as observed:
                            blocked.release.set()
                            await closing
                        self.assertIn("controlled late index failure", " ".join(observed.output))
                    else:
                        blocked.release.set()
                        await closing
                    await asyncio.gather(*waiters, return_exceptions=True)
                    self.assertEqual(1, blocked.calls)
                    self.assertEqual(blocked.calls, blocked.completed)
                    self.assertEqual(1 if late_failure else 0, len(service._pending_index_records))
                    self.assertEqual(1 if late_failure else 0, len(service._outbox.load()))
                    self.assertEqual(0 if late_failure else 1,
                                     len(store.load_dataset_snapshots("top20_index", "2026-09-10", 10)))
                finally:
                    blocked.release.set()
                    await asyncio.gather(*waiters, return_exceptions=True)
                    if closing is not None:
                        await asyncio.gather(closing, return_exceptions=True)
                    self.assertTrue(await asyncio.to_thread(blocked.wait_completed))
                    store.close()

    async def test_index_write_ack_and_outbox_remove_failures_keep_retryable_payload(self):
        for failure in ("write", "lost_ack", "remove"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                store, service = self._store_and_service(directory)
                calls = []
                recovered_store = recovered = None
                native_save = store.save_dataset_snapshot
                native_remove = service._outbox.remove

                def save(*args, **kwargs):
                    calls.append(1)
                    if failure == "write" and len(calls) == 1:
                        raise RuntimeError("controlled index write failure")
                    native_save(*args, **kwargs)
                    if failure == "lost_ack" and len(calls) == 1:
                        raise RuntimeError("controlled index ACK loss")

                def remove(key):
                    if failure == "remove":
                        raise RuntimeError("controlled outbox remove failure")
                    native_remove(key)

                store.save_dataset_snapshot = save
                service._outbox.remove = remove
                try:
                    await service._queue_index(self._record())
                    with self.assertRaisesRegex(RuntimeError, "controlled"):
                        await service._flush_pending_indexes()
                    self.assertEqual(1, len(service._pending_index_records))
                    self.assertEqual(1, len(service._outbox.load()))
                    self.assertEqual(0 if failure == "write" else 1,
                                     len(store.load_dataset_snapshots("top20_index", "2026-09-10", 10)))
                    service._outbox.remove = native_remove
                    await service.close()
                    self.assertEqual(1, len(calls))  # Close joins the owner; it does not add a retry policy.
                    self.assertEqual(1, len(service._pending_index_records))
                    self.assertEqual(1, len(service._outbox.load()))
                    recovered_store, recovered = self._store_and_service(directory)
                    recovered_native = recovered_store.save_dataset_snapshot

                    def recovered_save(*args, **kwargs):
                        calls.append(1)
                        return recovered_native(*args, **kwargs)

                    recovered_store.save_dataset_snapshot = recovered_save
                    await recovered.start()
                    await recovered.close()
                    self.assertEqual(2, len(calls))
                    self.assertEqual({}, recovered._pending_index_records)
                    self.assertEqual({}, recovered._outbox.load())
                    rows = recovered_store.load_dataset_snapshots("top20_index", "2026-09-10", 10)
                    self.assertEqual(1, len(rows))
                    self.assertEqual(10.0, rows[0]["payload"]["total"])
                finally:
                    service._outbox.remove = native_remove
                    if recovered is not None:
                        await recovered.close()
                    if recovered_store is not None:
                        recovered_store.close()
                    store.close()


class Top20CollectorLockBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def _seed_loop(self, directory):
        start = datetime.fromisoformat("2026-09-10T09:00:00+09:00")
        clock = [start]
        store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3", now_provider=lambda: clock[0])
        store.initialize()
        hub = RealtimeHub()
        codes = ("005930", "000660")
        hub.set_upstream_ready(codes, True)
        service = AutonomousTop20Service(
            _Broker(), hub, store, now_provider=lambda: clock[0],
            outbox_path=Path(directory) / "index-outbox.json",
        )
        service._subscriber = hub.connect()
        service._collector.active_codes = codes
        service._markets = {"005930": "KOSPI", "000660": "KOSDAQ"}
        # Use the actual collector kernel with 240 timely quarter-second samples;
        # the injected bucket values grow by 1 eok/second for each original code.
        service._trade_values.bucket_trade_value_eok = lambda _code, _period, minute: min(
            60.0, max(0.0, (clock[0] - minute).total_seconds()),
        )
        for quarter in range(240):
            clock[0] = start + timedelta(seconds=quarter / 4)
            self.assertIsNone(service._advance(clock[0]).completed)
        clock[0] = start + timedelta(minutes=1)
        return store, service, clock

    def _assert_original_record(self, store, *, available_at):
        rows = store.load_dataset_snapshots("top20_index", "2026-09-10", 10)
        self.assertEqual(1, len(rows))
        payload = rows[0]["payload"]
        self.assertEqual("2026-09-10T09:00+09:00", payload["minute"])
        self.assertEqual([60.0, 60.0, 0.0], payload["market_values"])
        self.assertEqual(120.0, payload["total"])
        self.assertEqual({"005930", "000660"}, set(payload["codes"]))
        self.assertEqual([1, 1, 0], payload["market_counts"])
        self.assertEqual([["2026-09-10T09:00:00+09:00", ["005930", "000660"]]], payload["cohort_segments"])
        self.assertEqual("realtime_complete", payload["capture_state"])
        metadata = store.load_market_data_metadata(MarketDatasetKind.TOP20_INDEX, "2026-09-10", payload["minute"])
        self.assertEqual(datetime.fromisoformat("2026-09-10T09:00:00+09:00"), metadata.effective_at)
        self.assertEqual(available_at, metadata.available_at)
        self.assertEqual(DataCompleteness.COMPLETE, metadata.completeness)
        self.assertEqual(ObservationOrigin.REALTIME, metadata.origin)

    async def test_index_loop_storage_does_not_block_ranking_prepare_and_real_subscription(self):
        for stage in ("outbox", "database"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                store, service, clock = self._seed_loop(directory)
                blocked = _BlockedIndexWrite(service._outbox.put if stage == "outbox" else store.save_dataset_snapshot)
                if stage == "outbox":
                    service._outbox.put = blocked
                else:
                    native = store.save_dataset_snapshot
                    store.save_dataset_snapshot = lambda collection, *args, **kwargs: (
                        blocked(collection, *args, **kwargs) if collection == "top20_index"
                        else native(collection, *args, **kwargs)
                    )
                rows = _ranking_rows("090100")
                codes = tuple(row["stk_cd"].removeprefix("A").removesuffix("_AL") for row in rows)
                service._nxt_checked_on.update(dict.fromkeys(codes, "2026-09-10"))
                service._nxt_eligible.update(dict.fromkeys(codes, False))
                subscribed, response_ready = asyncio.Event(), asyncio.Event()
                native_subscription = service._hub.update_subscription
                def subscription(*args, **kwargs):
                    native_subscription(*args, **kwargs)
                    subscribed.set()
                async def response(_requested_at):
                    response_ready.set()
                    return BrokerResult({"item_inq_rank": rows}, False, ""), rows, False, False
                loop = persistence = refresh = None
                try:
                    with patch.object(service, "_select_ranking_response", side_effect=response), \
                            patch.object(service, "_schedule_market_catalog"), \
                            patch.object(service, "_load_daily_entrant_state", new_callable=AsyncMock, return_value=({}, set())), \
                            patch.object(service, "_persist_missing_daily_entrants", new_callable=AsyncMock), \
                            patch.object(service._hub, "update_subscription", side_effect=subscription):
                        loop = asyncio.create_task(service._index_loop())
                        persistence = asyncio.create_task(service._persistence_loop())
                        self.assertTrue(await asyncio.to_thread(blocked.entered.wait, 1))
                        self.assertFalse(service._collector_lock.locked(), "index storage must not own collector RAM lock")
                        clock[0] += timedelta(milliseconds=750)
                        refresh = asyncio.create_task(service.refresh_ranking_once(clock[0]))
                        await asyncio.wait_for(response_ready.wait(), 1)
                        try:
                            await asyncio.wait_for(subscribed.wait(), .5)
                        except TimeoutError:
                            self.fail("fresh ranking prepare/subscription waited for index storage")
                        self.assertFalse(blocked.release.is_set())
                        self.assertEqual(codes, service._collector.next_codes)
                        self.assertTrue(set(codes).issubset(set(service._hub.requested_codes()[0])))
                        self.assertEqual(clock[0], service._collector.last_observed_at)
                        self.assertEqual(list(codes), service._latest_membership_snapshot["payload"]["codes"])
                        blocked.release.set()
                        await refresh
                        await asyncio.wait_for(asyncio.to_thread(blocked.wait_completed), 1)
                        await service._flush_pending_indexes()
                        self._assert_original_record(store, available_at=(
                            clock[0] if stage == "outbox" else clock[0] - timedelta(milliseconds=750)
                        ))
                        self.assertEqual({}, service._pending_index_records)
                        self.assertEqual({}, service._outbox.load())
                finally:
                    blocked.release.set()
                    if loop is not None:
                        loop.cancel()
                    if persistence is not None:
                        persistence.cancel()
                    await asyncio.gather(*(task for task in (loop, persistence, refresh) if task is not None), return_exceptions=True)
                    await service.close()
                    self.assertTrue(await asyncio.to_thread(blocked.wait_completed))
                    store.close()

    async def test_index_loop_close_joins_native_after_cancel_and_preserves_failed_restart(self):
        from kiwoom_monitor.central_server.diagnostic_replay_runtime import ReplayRuntimeScope, owned_create_task
        for late_failure in (False, True):
            with self.subTest(late_failure=late_failure), tempfile.TemporaryDirectory() as directory:
                store, service, clock = self._seed_loop(directory)
                if late_failure:
                    with store._connection() as connection:
                        connection.execute("CREATE TRIGGER reject_index BEFORE INSERT ON central_dataset_snapshots "
                                           "BEGIN SELECT RAISE(ABORT,'controlled loop index failure'); END")
                blocked = _BlockedIndexWrite(store.save_dataset_snapshot)
                store.save_dataset_snapshot = blocked
                runtime = ReplayRuntimeScope()
                loop = persistence = closing = recovered = None
                try:
                    with runtime.activate():
                        loop = owned_create_task(service._index_loop(), name="test-real-index-loop")
                        persistence = owned_create_task(service._persistence_loop(), name="test-real-persistence-loop")
                        service._tasks.extend((loop, persistence))
                        self.assertTrue(await asyncio.to_thread(blocked.entered.wait, 1))
                        self.assertFalse(service._collector_lock.locked())
                        runtime.begin_shutdown()
                        closing = owned_create_task(service.close(), name="test-real-index-close", shutdown=True)
                        await asyncio.sleep(.02)
                        self.assertFalse(closing.done())
                        with self.assertRaisesRegex(RuntimeError, "not_drained"):
                            runtime.require_drained()
                        if late_failure:
                            with self.assertLogs("kiwoom_monitor.central_server.autonomous_top20", level="WARNING") as observed:
                                blocked.release.set()
                                await closing
                            self.assertIn("controlled loop index failure", " ".join(observed.output))
                        else:
                            blocked.release.set()
                            await closing
                        self.assertEqual(blocked.calls, blocked.completed)
                        state = await runtime.drain(timeout=2)
                        self.assertEqual((0, 0, 0), tuple(state[name] for name in ("pending_tasks", "pending_threads", "submitted_threads")))
                        self.assertEqual(not late_failure, state["execution_succeeded"])
                    if late_failure:
                        self.assertEqual(1, len(service._outbox.load()))
                        with store._connection() as connection:
                            connection.execute("DROP TRIGGER reject_index")
                        recovered = AutonomousTop20Service(
                            _Broker(), RealtimeHub(), store, now_provider=lambda: clock[0],
                            outbox_path=Path(directory) / "index-outbox.json",
                        )
                        await recovered.start()
                        await recovered.close()
                        self.assertEqual({}, recovered._outbox.load())
                    self._assert_original_record(store, available_at=clock[0])
                    self.assertEqual(2 if late_failure else 1, blocked.calls)
                finally:
                    blocked.release.set()
                    if loop is not None:
                        loop.cancel()
                    if persistence is not None:
                        persistence.cancel()
                    await asyncio.gather(*(task for task in (loop, persistence, closing) if task is not None), return_exceptions=True)
                    if recovered is not None:
                        await recovered.close()
                    await service.close()
                    self.assertTrue(await asyncio.to_thread(blocked.wait_completed))
                    store.close()

    async def test_slow_index_and_program_native_writes_keep_sampling_the_same_complete_minute(self):
        from tests.unit.test_top20_program_shutdown import _snapshot

        for stage in ("index", "program"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                store, service, clock = self._seed_loop(directory)
                start = clock[0]
                first = service._advance(start).completed
                self.assertIsNotNone(first)
                await service._queue_index(first)
                native = store.save_dataset_snapshot if stage == "index" else store.save_dataset_snapshots
                blocked = _BlockedIndexWrite(native)
                if stage == "index":
                    store.save_dataset_snapshot = blocked
                else:
                    store.save_dataset_snapshots = lambda values: (
                        blocked(values) if any(value[0] == "program_flow" for value in values) else native(values)
                    )
                    service._program_snapshots.enqueue(_snapshot())
                observed = []
                native_advance = service._advance
                def advance(now):
                    observed.append(now)
                    return native_advance(now)
                service._advance = advance
                loops = [asyncio.create_task(service._index_loop()),
                         asyncio.create_task(service._persistence_loop())]
                service._tasks = loops
                try:
                    self.assertTrue(await asyncio.to_thread(blocked.entered.wait, 1))
                    for quarter in range(1, 11):
                        clock[0] = start + timedelta(seconds=quarter / 4)
                        await asyncio.sleep(.225)
                    self.assertFalse(blocked.release.is_set())
                    self.assertFalse(service._collector_lock.locked())
                    self.assertGreaterEqual(service._collector.last_observed_at, start + timedelta(seconds=2))
                    self.assertTrue(service._collector.minute_complete)
                    self.assertTrue(observed)
                    self.assertLessEqual(max((b - a).total_seconds()
                                             for a, b in zip([start] + observed, observed)), 2)
                    blocked.release.set()
                    self.assertTrue(await asyncio.to_thread(blocked.wait_completed))
                    await service.close()
                    completed = None
                    for quarter in range(11, 241):
                        clock[0] = start + timedelta(seconds=quarter / 4)
                        update = native_advance(clock[0])
                        if update.completed is not None:
                            completed = update.completed
                    self.assertIsNotNone(completed)
                    self.assertEqual("realtime_complete", completed.capture_state)
                    self.assertEqual((60.0, 60.0, 0.0), completed.market_values)
                    self.assertEqual(120.0, completed.total)
                    self.assertEqual({"005930", "000660"}, set(completed.codes))
                    self.assertEqual((1, 1, 0), completed.market_counts)
                    self.assertEqual(((start.isoformat(timespec="seconds"), ("005930", "000660")),),
                                     completed.cohort_segments)
                    self._assert_original_record(store, available_at=start)
                    self.assertEqual({}, service._pending_index_records)
                    self.assertEqual({}, service._outbox.load())
                    self.assertEqual(blocked.calls, blocked.completed)
                    self.assertEqual([], service._tasks)
                    self.assertIsNone(service._subscriber)
                    if stage == "program":
                        rows = store.load_dataset_snapshots("program_flow", "005930", 10)
                        self.assertEqual([_snapshot()["payload"]], [row["payload"] for row in rows])
                finally:
                    blocked.release.set()
                    for loop in loops:
                        loop.cancel()
                    await asyncio.gather(*loops, return_exceptions=True)
                    await service.close()
                    self.assertTrue(await asyncio.to_thread(blocked.wait_completed))
                    store.close()

    async def test_failed_index_retry_does_not_block_sampling_or_program_persistence(self):
        from tests.unit.test_top20_program_shutdown import _snapshot

        with tempfile.TemporaryDirectory() as directory:
            store, service, clock = self._seed_loop(directory)
            first = service._advance(clock[0]).completed
            self.assertIsNotNone(first)
            await service._queue_index(first)
            service._program_snapshots.enqueue(_snapshot())
            native_single, native_batch = store.save_dataset_snapshot, store.save_dataset_snapshots
            saved_program = asyncio.Event()
            event_loop = asyncio.get_running_loop()
            def failed_index(*args, **kwargs):
                raise OSError("controlled index remains unavailable")
            def program_batch(values):
                native_batch(values)
                event_loop.call_soon_threadsafe(saved_program.set)
            store.save_dataset_snapshot = failed_index
            store.save_dataset_snapshots = program_batch
            loops = [asyncio.create_task(service._index_loop()),
                     asyncio.create_task(service._persistence_loop())]
            service._tasks = loops
            try:
                with self.assertLogs("kiwoom_monitor.central_server.autonomous_top20", level="WARNING") as logged:
                    await asyncio.wait_for(saved_program.wait(), 1)
                self.assertIn("controlled index remains unavailable", " ".join(logged.output))
                self.assertEqual(clock[0], service._collector.last_observed_at)
                self.assertTrue(service._collector.minute_complete)
                self.assertEqual(1, len(service._pending_index_records))
                self.assertEqual(1, len(service._outbox.load()))
                self.assertEqual([], store.load_dataset_snapshots("top20_index", "2026-09-10", 10))
                rows = store.load_dataset_snapshots("program_flow", "005930", 10)
                self.assertEqual([_snapshot()["payload"]], [row["payload"] for row in rows])
            finally:
                await service.close()
                store.save_dataset_snapshot, store.save_dataset_snapshots = native_single, native_batch
                store.close()


class ProducerOptionalToolBoundaryTests(unittest.TestCase):
    def test_actual_producers_and_sqlite_lifetimes_run_with_all_diagnostic_sources_physically_absent(self):
        import hashlib
        import json
        import os
        import shutil
        import subprocess
        import sys

        original = Path(__file__).resolve().parents[2]
        selected = [
            "tests.unit.test_autonomous_top20.AutonomousTop20Tests.test_ranking_is_archived_and_keeps_an_internal_subscription",
            "tests.unit.test_autonomous_top20.AutonomousTop20Tests.test_catalog_loader_duplicate_codes_are_saved_once",
            "tests.unit.test_autonomous_top20.AutonomousTop20Tests.test_candidate_flow_capture_and_finalization_are_nas_owned_and_idempotent",
            "tests.unit.test_autonomous_top20.Top20IndexFlushOwnershipTests.test_cancelled_index_waiters_and_close_do_not_abandon_or_overlap_native_save",
            "tests.unit.test_autonomous_top20.Top20IndexFlushOwnershipTests.test_index_write_ack_and_outbox_remove_failures_keep_retryable_payload",
            "tests.unit.test_market_events.MarketEventServiceTests.test_vi_live_and_backfill_resends_are_immutable_deduplicated",
            "tests.unit.test_market_events.MarketEventServiceTests.test_vi_failed_owner_allows_waiting_duplicate_to_save",
            "tests.unit.test_market_events.MarketEventServiceTests.test_vi_cancelled_owner_keeps_guard_until_thread_commit_and_close_drains",
            "tests.unit.test_mock_account_monitor.MockAccountMonitorTests.test_startup_recovery_04_refresh_and_00_fill_share_one_runtime",
            "tests.unit.test_candidate_monitor.SelectedRamShadowRecordingTests.test_eight_consumers_one_context_one_commit_and_unchanged_state_no_write",
            "tests.unit.test_news_source_collection.QuerySetNewsCollectorTests.test_service_start_collects_without_desktop_request",
            "tests.unit.test_morning_market_brief.NativeMorningBriefLifetimeTests.test_native_schedule_claim_completion_restart_and_shutdown",
            "tests.unit.test_external_market_collector.ExternalMarketAutomaticRollTests.test_real_zero_and_old_shared_window_keep_existing_roll_rules",
        ]
        with tempfile.TemporaryDirectory(prefix="producer-tools-absent-") as directory:
            root = Path(directory)
            source = root / "src"
            shutil.copytree(original / "src", source,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "diagnostic*.py"))
            self.assertFalse(list(source.rglob("diagnostic*.py")))
            support_files = (
                ("tests/__init__.py", "tests/__init__.py"),
                ("tests/unit/__init__.py", "tests/unit/__init__.py"),
                ("tests/unit/test_autonomous_top20.py", "tests/unit/test_autonomous_top20.py"),
                ("tests/unit/test_market_events.py", "tests/unit/test_market_events.py"),
                ("tests/unit/test_mock_account_monitor.py", "tests/unit/test_mock_account_monitor.py"),
                ("tests/unit/test_candidate_monitor.py", "tests/unit/test_candidate_monitor.py"),
                ("tests/unit/test_theme_leadership.py", "tests/unit/test_theme_leadership.py"),
                ("tests/unit/test_market_research_features.py", "tests/unit/test_market_research_features.py"),
                ("tests/unit/test_news_source_collection.py", "tests/unit/test_news_source_collection.py"),
                ("tests/unit/test_morning_market_brief.py", "tests/unit/test_morning_market_brief.py"),
                ("tests/unit/test_external_market_collector.py", "tests/unit/test_external_market_collector.py"),
                ("tests/unit/test_entry_thesis.py", "tests/unit/test_entry_thesis.py"),
                ("scripts/preview_market_theme_shadow.py", "producer_support/preview_market_theme_shadow.py"),
            )
            import_source = b"\nfrom scripts.preview_market_theme_shadow import evaluate_document"
            import_target = b"\nfrom producer_support.preview_market_theme_shadow import evaluate_document"
            mapped_support = {"tests/unit/test_entry_thesis.py", "tests/unit/test_market_research_features.py"}
            expected, support_proof = {}, []
            for source_relative, destination_relative in support_files:
                content = (original / source_relative).read_bytes()
                source_digest = hashlib.sha256(content).hexdigest()
                if source_relative in mapped_support:
                    self.assertEqual(1, content.count(import_source))
                    content = content.replace(import_source, import_target)
                    self.assertEqual(source_digest, hashlib.sha256(content.replace(import_target, import_source)).hexdigest())
                destination = root / destination_relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
                expected[destination_relative] = hashlib.sha256(content).hexdigest()
                if source_relative not in mapped_support:
                    self.assertEqual(source_digest, expected[destination_relative])
                support_proof.append({"source": source_relative, "destination": destination_relative,
                    "source_raw_sha256": source_digest, "copy_raw_sha256": expected[destination_relative],
                    "import_mappings": int(source_relative in mapped_support)})
            self.assertFalse((root / "scripts").exists())
            for relative in ("src/kiwoom_monitor/__init__.py", "src/kiwoom_monitor/central_server/__init__.py",
                             "src/kiwoom_monitor/central_server/autonomous_top20.py",
                             "src/kiwoom_monitor/central_server/market_events.py",
                             "src/kiwoom_monitor/central_server/database.py",
                             "src/kiwoom_monitor/central_server/mock_account_monitor.py",
                             "src/kiwoom_monitor/central_server/market_theme_shadow_monitor.py",
                             "src/kiwoom_monitor/infrastructure/persistence/execution_repository.py",
                             "src/kiwoom_monitor/central_server/news_service.py",
                             "src/kiwoom_monitor/central_server/news_sources.py",
                             "src/kiwoom_monitor/central_server/morning_market_brief.py",
                             "src/kiwoom_monitor/central_server/external_market_collector.py",
                             "src/kiwoom_monitor/central_server/database_documents.py",
                             "src/kiwoom_monitor/central_server/database_news_sources.py",
                             "src/kiwoom_monitor/central_server/database_external_market.py"):
                expected[relative] = hashlib.sha256((original / relative).read_bytes()).hexdigest()
            child = r'''
import hashlib, io, json, os, sys, unittest
from pathlib import Path
if os.name == 'nt':
    import runpy
    runpy.run_path(os.environ['KIWOOM_REGRESSION_REGISTRY_HELPER'])['join_from_environment']()
root = Path(sys.argv[1]).resolve()
selected, expected = json.loads(sys.argv[2]), json.loads(sys.argv[3])
source = root / 'src'
assert not list(source.rglob('diagnostic*.py'))
assert not (root / 'scripts').exists()
for relative, digest in expected.items():
    assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == digest
sys.path[:0] = [str(source), str(root)]
suite = unittest.TestLoader().loadTestsFromNames(selected)
assert suite.countTestCases() == len(selected)
log = io.StringIO()
class ActualIdResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.actual_ids = []
    def startTest(self, test):
        self.actual_ids.append(test.id())
        super().startTest(test)
result = unittest.TextTestRunner(stream=log, resultclass=ActualIdResult).run(suite)
assert result.wasSuccessful() and result.testsRun == len(selected) and not result.skipped, log.getvalue()
assert result.actual_ids == selected, (result.actual_ids, selected)
assert not any(name.startswith('kiwoom_monitor.') and '.diagnostic' in name for name in sys.modules)
assert not any(name == 'scripts' or name.startswith('scripts.') for name in sys.modules)
origins = {}
for name, module in list(sys.modules.items()):
    if (name == 'kiwoom_monitor' or name.startswith('kiwoom_monitor.')
            or name.startswith('tests.') or name.startswith('producer_support.')):
        path = Path(module.__file__).resolve()
        assert path.is_relative_to(root), (name, str(path))
        if name == 'kiwoom_monitor' or name.startswith('kiwoom_monitor.'):
            assert path.is_relative_to(source), (name, str(path))
        origins[name] = {'file':str(path), 'raw_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
scope = getattr(sys, '_kiwoom_regression_registry_override', None)
assert os.name != 'nt' or scope is not None
description = getattr(scope, 'description', {})
print(json.dumps({'tools_physically_absent':True, 'selected_ids':selected,
    'actual_ids':result.actual_ids,
    'reused_native_tests':result.testsRun, 'origins':origins,
    'joined_owned_HKCU':scope is not None,
    'registry_key':description.get('key'), 'registry_owner_pid':description.get('owner_pid')}))
'''
            result = subprocess.run(
                [sys.executable, "-I", "-B", "-X", "utf8", "-c", child,
                 str(root), json.dumps(selected), json.dumps(expected)],
                cwd=root, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"), capture_output=True,
                text=True, encoding="utf-8", errors="backslashreplace", timeout=15,
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            evidence = json.loads(result.stdout.splitlines()[-1])
            self.assertTrue(evidence["tools_physically_absent"])
            self.assertEqual(selected, evidence["selected_ids"])
            self.assertEqual(selected, evidence["actual_ids"])
            self.assertEqual(len(selected), evidence["reused_native_tests"])
            if os.name == "nt":
                scope = getattr(sys, "_kiwoom_regression_registry_override", None)
                self.assertIsNotNone(scope)
                self.assertTrue(evidence["joined_owned_HKCU"])
                self.assertEqual(scope.description["key"], evidence["registry_key"])
                self.assertEqual(scope.description["owner_pid"], evidence["registry_owner_pid"])
            print("PRODUCER_ABSENCE_EVIDENCE=" + json.dumps({**evidence, "test_support": support_proof}, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
