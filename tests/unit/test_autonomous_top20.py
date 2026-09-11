from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from kiwoom_monitor.central_server.autonomous_top20 import AutonomousTop20Service, _collection_open
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
            return BrokerResult({"item_inq_rank": [
                {"dt": "20260910", "tm": "090000", "stk_cd": "A005930", "bigd_rank": "1"},
                {"dt": "20260910", "tm": "090000", "stk_cd": "000660_AL", "bigd_rank": "2"},
            ]}, False, "")
        if api_id == "ka10100":
            return BrokerResult({"nxtEnable": "Y" if body["stk_cd"] == "005930" else "N"}, False, "")
        raise AssertionError(api_id)


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

            snapshots = store.load_dataset_snapshots("top20_membership", "2026-09-10")
            metadata = store.load_market_data_metadata(
                MarketDatasetKind.CANDIDATE_SET,
                "2026-09-10",
                "2026-09-10T09:00:00",
            )
            entrants = store.load_documents("top20_daily_entrants", "2026-09-10")
            requested, nxt = hub.requested_codes()
            store.close()
        self.assertEqual(("005930", "000660"), codes)
        self.assertEqual(["005930", "000660"], snapshots[0]["payload"]["codes"])
        self.assertIsNotNone(metadata)
        assert metadata is not None
        self.assertEqual(DataCompleteness.COMPLETE, metadata.completeness)
        self.assertEqual(ObservationOrigin.QUERY, metadata.origin)
        self.assertEqual({"005930", "000660"}, {row["key"] for row in entrants})
        self.assertEqual({"005930", "000660"}, set(requested))
        self.assertEqual(("005930",), nxt)
        self.assertEqual("KOSPI", service._markets["005930"])

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

    def test_collection_window_is_weekday_0800_until_2000(self) -> None:
        self.assertTrue(_collection_open(datetime(2026, 9, 10, 8, 0)))
        self.assertTrue(_collection_open(datetime(2026, 9, 10, 19, 59, 59)))
        self.assertFalse(_collection_open(datetime(2026, 9, 10, 20, 0)))
        self.assertFalse(_collection_open(datetime(2026, 9, 12, 10, 0)))

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
