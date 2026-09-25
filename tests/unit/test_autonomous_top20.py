from __future__ import annotations

import asyncio
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from kiwoom_monitor.central_server.autonomous_top20 import (
    AutonomousTop20Service,
    _collection_open,
    _ranking_collection_due,
    _ranking_expected_at,
    _new_high_schedule_slot,
)
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
from kiwoom_monitor.central_server.rest_broker import BrokerResult
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
    async def test_new_high_schedule_uses_one_delayed_after_close_slot(self) -> None:
        self.assertEqual("", _new_high_schedule_slot(datetime(2026, 9, 24, 8, 59)))
        self.assertEqual("2026-09-24T15:29", _new_high_schedule_slot(datetime(2026, 9, 24, 15, 29)))
        self.assertEqual("", _new_high_schedule_slot(datetime(2026, 9, 24, 15, 30)))
        self.assertEqual("", _new_high_schedule_slot(datetime(2026, 9, 24, 15, 59)))
        self.assertEqual("2026-09-24:after_close", _new_high_schedule_slot(datetime(2026, 9, 24, 16, 0)))
        self.assertEqual("2026-09-24:after_close", _new_high_schedule_slot(datetime(2026, 9, 24, 19, 59)))
        self.assertEqual("", _new_high_schedule_slot(datetime(2026, 9, 24, 20, 0)))
        self.assertEqual("", _new_high_schedule_slot(datetime(2026, 9, 25, 7, 59)))
        self.assertEqual("", _new_high_schedule_slot(datetime(2026, 9, 26, 10, 0)))

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
            service = AutonomousTop20Service(
                _Broker(), hub, store, catalog_loader=lambda: (),
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
                self.assertEqual(20, len(latest["payload"]["items"]))
                requested, _nxt = hub.requested_codes()
                self.assertEqual(set(latest["payload"]["codes"]), set(requested))
                self.assertFalse(refresh.done())
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
        service.refresh_ranking_once = AsyncMock(return_value=())  # type: ignore[method-assign]
        backfill_started = asyncio.Event()

        async def blocked_backfill(_day: str) -> None:
            backfill_started.set()
            await asyncio.Event().wait()

        service.backfill_day = blocked_backfill  # type: ignore[method-assign]
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
        for task in tuple(service._fundamentals_tasks):
            task.cancel()
        await asyncio.gather(*tuple(service._fundamentals_tasks), return_exceptions=True)

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
            service = AutonomousTop20Service(broker, RealtimeHub(), store)
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
            service = AutonomousTop20Service(broker, RealtimeHub(), store)
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
            service = AutonomousTop20Service(broker, RealtimeHub(), store)
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

    async def test_nas_refreshes_all_new_high_periods(self) -> None:
        class NewHighBroker(_Broker):
            async def request(self, api_id, path, body, **kwargs):
                if api_id == "ka10016":
                    self.calls.append((api_id, dict(body), kwargs))
                    return BrokerResult({"ntl_pric": []}, False, "")
                return await super().request(api_id, path, body, **kwargs)

        broker = NewHighBroker()
        service = AutonomousTop20Service(broker, RealtimeHub(), object())  # type: ignore[arg-type]
        await service._refresh_new_highs()

        self.assertEqual(["5", "20", "250"], [call[1]["dt"] for call in broker.calls])

    async def test_nas_collects_due_nondefault_rankings(self) -> None:
        broker = _Broker()
        service = AutonomousTop20Service(broker, RealtimeHub(), object())  # type: ignore[arg-type]

        await service._refresh_aux_rankings(("4", "1", "2", "3"))

        self.assertEqual(["4", "1", "2", "3"], [call[1]["qry_tp"] for call in broker.calls])

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
                "document": {"enabled": False, "payload": {"nxtEnable": "N"}},
            }])
            broker = HistoricalBroker()
            service = AutonomousTop20Service(broker, RealtimeHub(), store)

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
                    return BrokerResult({}, False, "")
                return await super().request(api_id, path, body, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            broker = FlowBroker()
            service = AutonomousTop20Service(
                broker, RealtimeHub(), store,
                now_provider=lambda: datetime(2026, 9, 14, 20, 5),
            )

            await service._capture_candidate_investor_flow("005930", "2026-09-14")
            await service._capture_candidate_investor_flow("005930", "2026-09-14")
            await service._backfill_candidate_flows("005930", "2026-09-14")
            await service._backfill_candidate_flows("005930", "2026-09-14")
            initial = store.load_documents(
                "candidate_flow_capture", "2026-09-14:005930", 1,
            )
            final = store.load_documents(
                "candidate_flow_finalization", "2026-09-14:005930", 1,
            )
            store.close()

        self.assertEqual(["ka10045", "ka10045", "ka90008"], [call[0] for call in broker.calls])
        self.assertEqual("candidate_first_seen", initial[0]["document"]["scope"])
        self.assertEqual("candidate_after_close", final[0]["document"]["scope"])

    async def test_realtime_program_snapshot_is_archived_without_tr(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            service = AutonomousTop20Service(_Broker(), RealtimeHub(), store)
            service._pending_program_snapshots["005930"] = {
                "subject": "005930", "snapshot_key": "20260914:REALTIME:100100",
                "payload": {"market": "KRX", "rows": [{"trade_time": "100100"}]},
            }

            await service._flush_program_snapshots()
            values = store.load_dataset_snapshots("program_flow", "005930", 10)
            store.close()

        self.assertEqual("100100", values[0]["payload"]["rows"][0]["trade_time"])
    async def test_entry_minute_backfill_is_owned_by_nas_and_runs_once(self) -> None:
        class EntryBroker(_Broker):
            async def request(self, api_id, path, body, **kwargs):
                if api_id == "ka10080":
                    self.calls.append((api_id, body, kwargs))
                    return BrokerResult({"stk_min_pole_chart_qry": [{
                        "cntr_tm": "20260914100100", "open_pric": "100",
                        "high_pric": "110", "low_pric": "90", "cur_prc": "105",
                        "trde_qty": "10",
                    }]}, False, "")
                return await super().request(api_id, path, body, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            store.upsert_documents("stock_nxt_eligibility", [{
                "owner": "005930", "key": "latest",
                "document": {"enabled": False, "payload": {"nxtEnable": "N"}},
            }])
            broker = EntryBroker()
            service = AutonomousTop20Service(
                broker, RealtimeHub(), store,
                now_provider=lambda: datetime(2026, 9, 14, 10, 2),
            )

            await service._backfill_entry_minutes("005930", "2026-09-14")
            await service._backfill_entry_minutes("005930", "2026-09-14")
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
                "document": {"enabled": True, "payload": {"nxtEnable": "Y"}},
            }])
            broker = _Broker()
            service = AutonomousTop20Service(broker, RealtimeHub(), store)

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
            async def request(self, api_id, path, body, **kwargs):
                self.calls.append((api_id, body, kwargs))
                if api_id == "ka10081":
                    return BrokerResult({"stk_dt_pole_chart_qry": []}, False, "")
                return await super().request(api_id, path, body, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            broker = DailyBroker()
            service = AutonomousTop20Service(broker, RealtimeHub(), store)
            service._nxt_eligible["005930"] = False

            await service._ensure_entry_daily_history("005930", "2026-09-14")
            store.close()

        calls = [call for call in broker.calls if call[0] == "ka10081"]
        self.assertEqual(1, len(calls))
        self.assertEqual("005930", calls[0][1]["stk_cd"])

    async def test_ranking_is_archived_and_keeps_an_internal_subscription(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            hub = RealtimeHub()
            broker = _Broker()
            service = AutonomousTop20Service(
                broker, hub, store,
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
            service = AutonomousTop20Service(
                broker, hub, store, catalog_loader=slow_catalog,
            )
            service._subscriber = hub.connect()
            try:
                await asyncio.wait_for(
                    service.refresh_ranking_once(datetime(2026, 9, 10, 9, 0, 0)),
                    timeout=0.5,
                )
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

            await service.backfill_day("2026-09-14")
            store.close()

        self.assertEqual([], broker.calls)

    async def test_legacy_minute_coverage_is_upgraded_before_it_is_reused(self) -> None:
        class MinuteBroker:
            def __init__(self) -> None:
                self.calls = 0

            async def request(self, api_id, path, body, **kwargs):
                self.calls += 1
                return BrokerResult({"stk_min_pole_chart_qry": [{
                    "cntr_tm": "20260914195900", "open_pric": "1",
                    "high_pric": "1", "low_pric": "1", "cur_prc": "1",
                    "trde_qty": "1",
                }]}, False, "")

        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            store.upsert_documents("market_data_coverage", [{
                "owner": "2026-09-14:001210:KRX", "key": "complete",
                "document": {"kind": "minute", "pages": 2},
            }])
            broker = MinuteBroker()
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


if __name__ == "__main__":
    unittest.main()
