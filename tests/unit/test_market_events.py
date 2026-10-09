from __future__ import annotations

import asyncio
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.market_events import MarketEventService, select_condition
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
    def test_condition_selection_never_guesses(self) -> None:
        values = [("1", "급등 15%"), ("2", "거래량")]
        self.assertEqual(("1", "급등 15%"), select_condition(values)[0])
        self.assertEqual("NO_MATCH", select_condition([("2", "거래량")])[1])
        self.assertEqual("AMBIGUOUS", select_condition(values + [("3", "15% 돌파")])[1])
        self.assertEqual(("2", "거래량"), select_condition(values, exact_name="거래량")[0])
        self.assertEqual("EXACT_NOT_FOUND", select_condition(values, exact_name="없는조건")[1])

    def test_vi_wire_parser_preserves_official_fields(self) -> None:
        values = {"9001": "A005930", "302": "삼성전자", "9068": "1", "1225": "2",
                  "1221": "+75000", "1223": "091501", "1224": "091701", "9069": "+",
                  "1490": "2", "9081": "KRX", "13": "100", "14": "7500000"}
        events = parse_vi_events({"trnm": "REAL", "data": [{"type": "1h", "values": values}]})
        self.assertEqual(1, len(events))
        self.assertEqual(("005930", "ACTIVATED", "DYNAMIC", 75000, 2),
                         (events[0].code, events[0].event_kind, events[0].vi_type,
                          events[0].trigger_price, events[0].trigger_count))


class MarketEventServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = SQLiteQueryStore(Path(self.temp.name) / "central.sqlite3")
        self.store.initialize()
        self.hub = RealtimeHub()
        self.broker = _Broker()
        self.now = [datetime(2026, 9, 11, 10, 0)]  # 금요일
        self.service = MarketEventService(
            self.broker, self.hub, self.store, now_provider=lambda: self.now[0],
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

    async def test_initial_continuation_realtime_delete_and_seq_change(self) -> None:
        socket = _Socket()
        await self.service.on_ws_connected(socket)
        await self.service.handle_ws_message(
            {"trnm": "CNSRLST", "data": [["7", "상승 15%"]]}, socket,
        )
        self.assertEqual({"trnm": "CNSRREQ", "seq": "7", "search_type": "1", "stex_tp": "K"},
                         socket.messages[-1])
        await self.service.handle_ws_message(
            {"trnm": "CNSRREQ", "data": [{"jmcode": "A005930"}],
             "cont_yn": "Y", "next_key": "page2"}, socket,
        )
        self.assertEqual("Y", socket.messages[-1]["cont_yn"])
        await self.service.handle_ws_message({"trnm": "REAL", "data": [{
            "type": "02", "values": {"841": "7", "9001": "A005930", "843": "D"},
        }]}, socket)
        await self.service.handle_ws_message({"trnm": "CNSRREQ", "seq": "7", "data": [], "cont_yn": "N"}, socket)
        await asyncio.wait_for(self.service._signal_queue.join(), timeout=2)
        current = self.store.load_hot_cohort(active_only=True)
        self.assertEqual(1, len(current))
        self.assertEqual("D", current[0]["last_signal"])
        # 재연결 뒤 같은 이름의 변경된 seq를 다시 해석한다.
        await self.service.on_ws_connected(socket)
        await self.service.handle_ws_message(
            {"trnm": "CNSRLST", "data": [["19", "상승 15%"]]}, socket,
        )
        self.assertEqual("19", socket.messages[-1]["seq"])

    async def test_metadata_snapshot_cannot_overwrite_later_condition_signal(self) -> None:
        metadata_entered, release_metadata = asyncio.Event(), asyncio.Event()
        delete_started, delete_store_entered = asyncio.Event(), asyncio.Event()
        native_to_thread = asyncio.to_thread

        async def controlled_to_thread(function, *args, **kwargs):
            if getattr(function, "__name__", "") == "record_hot_cohort_revision":
                if args[0]["event_type"] == "ELIGIBILITY":
                    metadata_entered.set()
                    await release_metadata.wait()
                elif args[0]["event_type"] == "D":
                    delete_store_entered.set()
            return await native_to_thread(function, *args, **kwargs)

        async def delete():
            delete_started.set()
            await self.service.record_condition_signal("005930", "D", source="REAL")

        self.service._selected = ("7", "상승 15%")
        signal_time = self.now[0]
        deleting = None
        with patch("kiwoom_monitor.central_server.market_events.asyncio.to_thread",
                   side_effect=controlled_to_thread):
            try:
                await self.service.record_condition_signal("005930", "I", source="INITIAL")
                await asyncio.wait_for(metadata_entered.wait(), 5)
                deleting = asyncio.create_task(delete())
                # The started waiter resumes after delete reaches its first await.
                # Metadata still owns the old snapshot and has not reached DB ACK.
                await asyncio.wait_for(delete_started.wait(), 5)
                self.assertFalse(delete_store_entered.is_set(), "signal write bypassed metadata state ownership")
                self.now[0] = datetime(2026, 9, 12, 0, 1)
            finally:
                release_metadata.set()
                if deleting is not None:
                    await asyncio.wait_for(deleting, 5)
                await asyncio.wait_for(self.service._metadata_queue.join(), 5)
        current = self.store.load_hot_cohort(active_only=True)[0]
        self.assertEqual("D", current["last_signal"])
        self.assertEqual(signal_time.timestamp(), current["last_signal_at"])
        self.assertTrue(current["nxt_eligible"])
        self.assertEqual("D", self.service._cohort["005930"]["last_signal"])
        history = self.store.load_market_event_history("cohort", code="005930", limit=10)
        self.assertEqual({"ENTERED", "ELIGIBILITY", "D"}, {row["event_type"] for row in history})
        self.assertEqual("2026-09-11", next(row for row in history if row["event_type"] == "D")["session_id"])

    async def test_retention_uses_next_observed_session_not_calendar(self) -> None:
        self.service._selected = ("7", "상승 15%")
        await self.service.record_condition_signal("005930", "I", source="INITIAL")
        self.service.mark_krx_session_observed("2026-09-11")
        await self.service.close_krx_session("2026-09-11")
        self.assertTrue(self.store.load_hot_cohort(active_only=True))
        # 월요일이 휴일이었다는 가정: 실제로 관측된 화요일 세션 종료 때만 만료된다.
        self.now[0] = datetime(2026, 9, 15, 15, 30)
        self.service.mark_krx_session_observed("2026-09-15")
        await self.service.close_krx_session("2026-09-15")
        self.assertFalse(self.store.load_hot_cohort(active_only=True))
        self.assertEqual("next_observed_krx_session_close",
                         self.store.load_market_event_history("cohort", code="005930")[0]["basis"])

    async def test_regular_close_does_not_expire_cohort_before_full_day_close(self) -> None:
        self.service._selected = ("7", "상승 15%")
        await self.service.record_condition_signal("005930", "I", source="INITIAL")
        self.service.mark_krx_session_observed("2026-09-11")
        await self.service.close_observation_day("2026-09-11")
        self.now[0] = datetime(2026, 9, 14, 15, 30)
        self.service.mark_krx_session_observed("2026-09-14")
        await self.service.close_krx_regular_session("2026-09-14")
        self.assertTrue(self.store.load_hot_cohort(active_only=True))
        self.now[0] = datetime(2026, 9, 14, 20, 0)
        await self.service.close_observation_day("2026-09-14")
        self.assertFalse(self.store.load_hot_cohort(active_only=True))

    async def test_nxt_after_tick_cannot_be_used_as_krx_regular_close(self) -> None:
        self.service._selected = ("7", "상승 15%")
        await self.service.record_condition_signal("005930", "I", source="INITIAL")
        await asyncio.wait_for(self.service._metadata_queue.join(), timeout=2)
        self.service.mark_krx_session_observed("2026-09-11")
        nxt_tick = TradeTick(
            "005930", 130000, 1, 1, 1, 130000, "195959", 20.0, market="NXT",
        )
        self.service._last_ticks[("005930", "NXT")] = nxt_tick
        await self.service.close_krx_regular_session("2026-09-11")
        statuses = {
            value["status"] for value in self.store.load_market_event_history("upper_limit", code="005930")
        }
        self.assertNotIn("CLOSED_AT_LIMIT", statuses)

    async def test_hub_union_nxt_and_upper_limit_facts_are_deduplicated(self) -> None:
        top20 = self.hub.connect()
        self.hub.update_subscription(top20, ["005930"], ["005930"])
        self.service._selected = ("7", "상승 15%")
        await self.service.record_condition_signal("005930", "I", source="INITIAL")
        await asyncio.wait_for(self.service._metadata_queue.join(), timeout=2)
        codes, nxt = self.hub.requested_codes()
        self.assertEqual(("005930",), codes)
        self.assertEqual(("005930",), nxt)
        below = TradeTick("005930", 120000, 1, 1, 1, 125000, "095900", 14.0)
        await self.service.observe_trade(below)
        self.assertTrue(self.store.load_hot_cohort(active_only=True))
        tick = TradeTick("005930", 130000, 1, 1, 1, 130000, "100000", 29.97)
        await self.service.observe_trade(tick)
        await self.service.observe_trade(tick)
        self.service._last_ticks["005930"] = tick
        self.service.mark_krx_session_observed("2026-09-11")
        await self.service.close_krx_session("2026-09-11")
        history = self.store.load_market_event_history("upper_limit", code="005930")
        self.assertEqual({"UNKNOWN", "TOUCHED", "CURRENT", "CLOSED_AT_LIMIT"},
                         {value["status"] for value in history})
        self.assertEqual(4, len(history))
        self.assertFalse(any(api_id.lower().startswith(("kt", "order")) for api_id, _ in self.broker.calls))

    async def test_reset_change_rate_does_not_create_fact_from_old_upper_limit(self) -> None:
        self.service._cohort["005930"] = {"active": True}
        self.service._upper_limits["005930"] = 130_000

        await self.service.observe_trade(
            TradeTick("005930", 130_000, 1, 1, 1, 130_000, "163000", 0.0),
        )

        self.assertEqual(
            [], self.store.load_market_event_history("upper_limit", code="005930"),
        )

    async def test_0g_replaces_upper_limit_basis_for_tracked_stock(self) -> None:
        self.service._cohort["005930"] = {"active": True}
        self.service._upper_limits["005930"] = 130_000

        handled = await self.service.handle_ws_message({
            "trnm": "REAL",
            "data": [{"type": "0g", "item": "005930", "values": {
                "305": "+135000", "306": "-73000", "307": "104000",
            }}],
        }, _Socket())

        self.assertTrue(handled)
        self.assertEqual(135_000, self.service._upper_limits["005930"])

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
