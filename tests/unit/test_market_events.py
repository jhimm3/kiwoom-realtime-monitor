from __future__ import annotations

import asyncio
import tempfile
import threading
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.application.micro_execution_evidence import project_vi_clock
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.market_events import MarketEventService
from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
from kiwoom_monitor.central_server.rest_broker import BrokerResult
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import TradeTick, parse_vi_events


class _Socket:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def send(self, raw: str) -> None:
        import json
        self.messages.append(json.loads(raw))


class _Broker:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.vi_rows: list[dict[str, object]] = []

    async def request(self, api_id: str, _path: str, body: dict[str, object], **_kwargs) -> BrokerResult:
        self.calls.append((api_id, body))
        if api_id == "ka10100":
            return BrokerResult({"nxtEnable": "Y"}, False, "")
        if api_id == "ka10001":
            return BrokerResult({"upl_pric": "+130000"}, False, "")
        if api_id == "ka10054":
            return BrokerResult({"motn_stk": self.vi_rows}, False, "")
        raise AssertionError(f"unexpected request {api_id}")




class MarketEventContractTests(unittest.TestCase):

    def test_vi_wire_parser_preserves_official_fields(self) -> None:
        values = {"9001": "A005930", "302": "삼성전자", "9068": "1", "1225": "2",
                  "1221": "+75000", "1223": "091501", "1224": "091701", "9069": "+",
                  "1490": "2", "9081": "KRX", "13": "100", "14": "7500000"}
        events = parse_vi_events({"trnm": "REAL", "data": [{"type": "1h", "values": values}]})
        self.assertEqual(1, len(events))
        self.assertEqual(("005930", "ACTIVATED", "DYNAMIC", 75000, 2),
                         (events[0].code, events[0].event_kind, events[0].vi_type,
                          events[0].trigger_price, events[0].trigger_count))

    def test_micro_vi_clock_rejects_missing_malformed_or_unconfirmed_raw_evidence(self) -> None:
        cut = datetime.fromisoformat("2026-09-11T09:18:00+09:00")
        received = datetime.fromisoformat("2026-09-11T09:17:02+09:00").timestamp()
        for raw, trading_date, reason in (
            ({"9068": "2", "1224": "091701"}, cut.date(), "unconfirmed_vi_trigger"),
            ({"9068": "2", "1223": "091561"}, cut.date(), "unconfirmed_vi_trigger"),
            ({"9068": "2", "1223": "91501"}, cut.date(), "unconfirmed_vi_trigger"),
            ({"9068": "2", "1223": "０９１５０１"}, cut.date(), "unconfirmed_vi_trigger"),
            ({"9068": "2", "1223": "091703"}, cut.date(), "unconfirmed_vi_trigger"),
            ({"9068": "9", "1223": "091501"}, cut.date(), "unconfirmed_vi_trigger"),
            ({"9068": "2", "1223": "091501"}, None, "unconfirmed_trading_date"),
            ({"9068": "2", "1223": "091501"}, cut.date() - timedelta(days=1), "unconfirmed_trading_date"),
        ):
            with self.subTest(raw=raw, trading_date=trading_date):
                projection = project_vi_clock({
                    "source": "kiwoom-websocket-1h", "event_kind": "RELEASED",
                    "effective_at": "2026-09-11T09:15:01", "raw_values": raw,
                    "received_at": received, "available_at": received,
                }, trading_date=trading_date, decision_at=cut)
                self.assertEqual((None, None, None, reason),
                                 (projection.observed_at, projection.available_at, projection.fid, projection.reason))
        for category, release, expected in (("2", None, "ACTIVATED"), ("2", "091761", "ACTIVATED"),
                                            ("2", "091703", "ACTIVATED"), ("4", "091701", "EXTENDED")):
            with self.subTest(category=category, release=release):
                projection = project_vi_clock({
                    "source": "kiwoom-websocket-1h", "event_kind": "RELEASED",
                    "raw_values": {"9068": category, "1223": "091501", "1224": release},
                    "received_at": received, "available_at": received,
                }, trading_date=cut.date(), decision_at=cut)
                self.assertEqual((expected, "1223", "2026-09-11T09:15:01+09:00"),
                                 (projection.kind, projection.fid, projection.observed_at.isoformat()))

    def test_micro_vi_clock_does_not_substitute_historical_sources_or_invalid_receipts(self) -> None:
        cut = datetime.fromisoformat("2026-09-11T09:18:00+09:00")
        received = datetime.fromisoformat("2026-09-11T09:17:02+09:00").timestamp()
        receipt = {"source": "kiwoom-websocket-1h", "event_kind": "RELEASED",
                   "raw_values": {"9068": "2", "1224": "091701"},
                   "received_at": received, "available_at": received}
        for fields, reason in (
            ({"source": "kiwoom-rest-ka10054"}, "unsupported_source"),
            ({"source": "krx_data_marketplace"}, "unsupported_source"),
            ({"available_at": received - 1}, "invalid_receipt_time"),
            ({"received_at": float("nan")}, "invalid_receipt_time"),
            ({"available_at": True}, "invalid_receipt_time"),
        ):
            with self.subTest(fields=fields):
                projection = project_vi_clock(receipt | fields, trading_date=cut.date(), decision_at=cut)
                self.assertIsNone(projection.observed_at)
                self.assertEqual(reason, projection.reason)
        with self.assertRaisesRegex(ValueError, "timezone"):
            project_vi_clock(receipt, trading_date=cut.date(), decision_at=cut.replace(tzinfo=None))


class MarketEventServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = SQLiteQueryStore(Path(self.temp.name) / "central.sqlite3")
        self.store.initialize()
        self.hub = RealtimeHub()
        self.broker = _Broker()
        self.now = [datetime(2026, 9, 11, 10, 0, tzinfo=timezone(timedelta(hours=9)))]  # 금요일 KST
        self.service = MarketEventService(
            self.broker, self.store, now_provider=lambda: self.now[0],
        )
        await self.service.start()

    async def asyncTearDown(self) -> None:
        await self.service.close()
        self.store.close()
        self.temp.cleanup()





    async def test_null_realtime_data_is_ignored(self) -> None:
        handled = await self.service.handle_ws_message(
            {"trnm": "REAL", "data": None}, _Socket(),
        )

        self.assertFalse(handled)
        # A historical condition cohort must not revive subscriptions at restart.
        await self.service.close()
        self.store.record_hot_cohort_revision({
            "revision_id": "retired-condition", "revision_key": "retired-condition",
            "stock_code": "005930", "event_type": "ENTER", "session_id": "2026-09-11",
            "effective_at": 1.0, "available_at": 1.0,
        }, current={"stock_code": "005930", "stock_name": "historical",
                    "first_seen_at": 1.0, "entry_session": "2026-09-11",
                    "last_signal_at": 1.0, "active": True})
        self.service = MarketEventService(self.broker, self.store, now_provider=lambda: self.now[0])
        with patch.object(self.store, "load_hot_cohort", side_effect=AssertionError("retired cohort read")):
            await self.service.start()
        self.assertEqual(((), ()), self.hub.requested_codes())
        socket = _Socket()
        await self.service.on_ws_connected(socket)
        self.assertFalse(await self.service.handle_ws_message({"trnm": "CNSRLST", "data": []}, socket))
        self.assertEqual([], socket.messages)
        self.assertEqual(1, len(self.store.load_hot_cohort(active_only=True)))









    async def test_vi_live_and_backfill_resends_are_immutable_deduplicated(self) -> None:
        socket = _Socket()
        message = {"trnm": "REAL", "data": [{"type": "1h", "values": {
            "9001": "A005930", "9068": "1", "1225": "1", "1221": "+75000",
            "1223": "091501", "1490": "1", "9069": "+", "9081": "KRX",
        }}]}
        await self.service.handle_ws_message(message, socket)
        await self.service.handle_ws_message(message, socket)
        if self.service._background:
            await asyncio.gather(*tuple(self.service._background))
        self.assertEqual(1, len(self.store.load_market_event_history("vi", code="005930")))
        self.broker.vi_rows = [{"stk_cd": "A000660", "motn_tp": "1", "motn_pric": "200000",
                                "motn_tm": "091500", "motn_cnt": "1", "stex_tp": "KRX"}]
        await self.service._backfill_vi()
        await self.service._backfill_vi()
        self.assertEqual(1, len(self.store.load_market_event_history("vi", code="000660")))

    async def test_automation_vi_uses_the_wire_release_clock_and_isolates_cycles_and_venues(self):
        socket = _Socket()
        check = self.service.automation_buy_vi_rejection
        received = self.now[0]

        async def frame(code, trigger, release="000000", category="2"):
            await self.service.handle_ws_message({"trnm": "REAL", "data": [{"type": "1h", "values": {
                "9001": code, "9068": category, "1223": trigger, "1224": release,
            }}]}, socket)

        await frame("005930", "095500")
        await frame("005930_AL", "095500")
        await frame("005930_NX", "095500", "095730")
        await frame("000660_NX", "095500")
        self.assertEqual("vi_active", check("005930", received))
        self.assertIsNone(check("000660", received))
        await frame("005930", "095500", "095730")
        self.assertEqual("vi_active", check("005930", received))  # Unmatched integrated event.
        await frame("005930_AL", "095500", "095730")
        await frame("005930", "095500")  # A delayed duplicate must not reopen this cycle.
        self.assertEqual("vi_requires_new_observation", check("005930", received))
        self.assertIsNone(check("005930", received + timedelta(milliseconds=1)))

        await frame("005930", "095900", "100500", category="4")
        await frame("005930", "095500", "095730")
        self.assertEqual("vi_active", check("005930", received))
        self.now[0] += timedelta(minutes=6)
        self.assertEqual("vi_active", check("005930", self.now[0]))  # A clock alone cannot release it.
        await frame("005930", "095900", "100500", category="3")
        self.assertIsNone(check("005930", self.now[0] + timedelta(milliseconds=1)))
        self.assertEqual([], self.broker.calls)

    async def test_micro_vi_clock_projects_release_without_rewriting_native_receipt(self) -> None:
        self.now[0] = datetime.fromisoformat("2026-09-11T09:17:09+09:00")
        trading_date = self.now[0].date()
        values = {"9001": "A005930", "9068": "2", "1225": "1", "1221": "+75000",
                  "1223": "091501", "1224": "000000", "1490": "1", "9081": "KRX"}
        await self.service.handle_ws_message({"trnm": "REAL", "data": [
            {"type": "1h", "values": values}, {"type": "1h", "values": values | {"1224": "091701"}},
        ]}, _Socket())
        await self.service.close()
        rows = self.store.load_market_event_history("vi", code="005930")
        receipts = deepcopy(rows)
        by_kind = {("ACTIVATED" if row["raw_values"]["1224"] == "000000" else "RELEASED"): row for row in rows}
        released = by_kind["RELEASED"]
        self.assertEqual(["RELEASED", "RELEASED"], [row["event_kind"] for row in rows])
        self.assertEqual("2026-09-11T09:15:01", released["effective_at"])
        self.assertEqual(self.now[0].timestamp(), released["received_at"])
        self.assertEqual(self.now[0].timestamp(), released["available_at"])
        self.assertNotEqual(by_kind["ACTIVATED"]["event_id"], released["event_id"])

        early = project_vi_clock(released, trading_date=trading_date,
                                 decision_at=datetime.fromisoformat("2026-09-11T00:17:08+00:00"))
        self.assertEqual((None, None, "not_yet_available"), (early.observed_at, early.available_at, early.reason))
        for kind, fid, expected in (("ACTIVATED", "1223", "09:15:01"), ("RELEASED", "1224", "09:17:01")):
            with self.subTest(kind=kind):
                projected = project_vi_clock(by_kind[kind], trading_date=trading_date,
                                             decision_at=self.now[0].astimezone(timezone.utc))
                self.assertEqual("micro-vi-clock/v2", projected.version)
                self.assertEqual(kind, projected.kind)
                self.assertEqual((fid, f"2026-09-11T{expected}+09:00", self.now[0], ""),
                                 (projected.fid, projected.observed_at.isoformat(), projected.available_at, projected.reason))
        self.assertEqual(receipts, rows)
        self.assertEqual(receipts, self.store.load_market_event_history("vi", code="005930"))

    @staticmethod
    def _vi_message(*codes: str) -> dict:
        return {"trnm": "REAL", "data": [{"type": "1h", "values": {
            "9001": "A" + code, "9068": "1", "1225": "1", "1221": "+75000",
            "1223": "091501", "1490": "1", "9069": "+", "9081": "KRX",
        }} for code in codes]}

    async def test_vi_inflight_duplicates_share_commit_and_later_replay_reaches_store(self) -> None:
        entered, release = threading.Event(), threading.Event()
        append = self.store.append_vi_events
        batches = []

        def save(values):
            batches.append([value["stock_code"] for value in values])
            if len(batches) == 1:
                entered.set()
                if not release.wait(3):
                    raise TimeoutError("test did not release VI commit")
            return append(values)

        with patch.object(self.store, "append_vi_events", side_effect=save):
            try:
                # 같은 프레임의 중복과 COMMIT 대기 중 재수신을 함께 검증한다.
                await self.service.handle_ws_message(self._vi_message("005930", "000660", "005930"), _Socket())
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                await self.service.handle_ws_message(self._vi_message("005930", "000660"), _Socket())
                await asyncio.sleep(0)
            finally:
                release.set()
                await asyncio.gather(*tuple(self.service._background))
            self.assertEqual([["005930", "000660"]], batches)
            self.assertFalse(self.service._vi_inflight)
            before = self.store.load_market_event_history("vi")
            await self.service.handle_ws_message(self._vi_message("005930"), _Socket())
            await asyncio.gather(*tuple(self.service._background))
            self.assertEqual([["005930", "000660"], ["005930"]], batches)
            self.assertEqual(before, self.store.load_market_event_history("vi"))

    async def test_vi_distinct_event_can_commit_while_other_event_is_pending(self) -> None:
        entered, release, other_committed = threading.Event(), threading.Event(), threading.Event()
        append = self.store.append_vi_events

        def save(values):
            if values[0]["stock_code"] == "005930":
                entered.set()
                if not release.wait(3):
                    raise TimeoutError("test did not release VI commit")
            result = append(values)
            if values[0]["stock_code"] == "000660":
                other_committed.set()
            return result

        with patch.object(self.store, "append_vi_events", side_effect=save):
            try:
                await self.service.handle_ws_message(self._vi_message("005930"), _Socket())
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                await self.service.handle_ws_message(self._vi_message("000660"), _Socket())
                self.assertTrue(await asyncio.to_thread(other_committed.wait, 2))
                self.assertFalse(release.is_set())
            finally:
                release.set()
                await asyncio.gather(*tuple(self.service._background))
        self.assertEqual(2, len(self.store.load_market_event_history("vi")))

    async def test_vi_failed_owner_allows_waiting_duplicate_to_save(self) -> None:
        entered, release = threading.Event(), threading.Event()
        append = self.store.append_vi_events
        attempts = []

        def save(values):
            attempts.append(values)
            if len(attempts) == 1:
                entered.set()
                if not release.wait(3):
                    raise TimeoutError("test did not release VI commit")
                raise RuntimeError("failed VI transaction")
            return append(values)

        with patch.object(self.store, "append_vi_events", side_effect=save):
            try:
                await self.service.handle_ws_message(self._vi_message("005930"), _Socket())
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                await self.service.handle_ws_message(self._vi_message("005930"), _Socket())
                await asyncio.sleep(0)
            finally:
                release.set()
                outcomes = await asyncio.gather(*tuple(self.service._background), return_exceptions=True)
        self.assertEqual(2, len(attempts))
        self.assertEqual(1, sum(isinstance(outcome, RuntimeError) for outcome in outcomes))
        self.assertEqual(1, len(self.store.load_market_event_history("vi")))
        self.assertFalse(self.service._vi_inflight)

    async def test_vi_cancelled_owner_keeps_guard_until_thread_commit_and_close_drains(self) -> None:
        entered, release = threading.Event(), threading.Event()
        append = self.store.append_vi_events
        attempts = []

        def save(values):
            attempts.append(values)
            entered.set()
            if not release.wait(3):
                raise TimeoutError("test did not release VI commit")
            return append(values)

        with patch.object(self.store, "append_vi_events", side_effect=save):
            try:
                await self.service.handle_ws_message(self._vi_message("005930"), _Socket())
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                owner = next(task for task in self.service._background if task.get_name() == "vi-live-store")
                owner.cancel()
                await asyncio.sleep(0)
                self.assertTrue(self.service._vi_inflight)
                await self.service.handle_ws_message(self._vi_message("005930"), _Socket())
                closing = asyncio.create_task(self.service.close())
                await asyncio.sleep(0)
                self.assertFalse(closing.done())
            finally:
                release.set()
                if "closing" in locals():
                    await closing
                else:
                    await self.service.close()
        self.assertTrue(owner.cancelled())
        self.assertEqual(1, len(attempts))
        self.assertEqual(1, len(self.store.load_market_event_history("vi")))
        self.assertFalse(self.service._vi_inflight)
