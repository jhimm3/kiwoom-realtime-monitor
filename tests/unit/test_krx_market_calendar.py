from contextlib import closing
from dataclasses import asdict
from datetime import date, datetime, time, timedelta
import asyncio
import json
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

from kiwoom_monitor.application.market_session_schedule import KST, next_trading_day
from kiwoom_monitor.application.section12_minute_research import CLOSE_FAMILY, MINUTE_SHADOW_VERSION
from kiwoom_monitor.central_server.candidate_monitor import CandidateMonitor
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.krx_calendar_evidence import KrxCalendarEvidence
from kiwoom_monitor.central_server.market_observations import minute_bar_revision_payload
from kiwoom_monitor.central_server.minute_plan_management import MinutePlanManagement
from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
from kiwoom_monitor.infrastructure.krx.market_calendar import KrxMarketCalendar
from tests.unit.test_candidate_monitor import FakeStore


class KrxMarketCalendarTests(unittest.TestCase):
    def test_refresh_is_limited_to_morning_and_after_close_windows(self):
        from types import SimpleNamespace

        friday = date(2026, 11, 13)
        monday = friday + timedelta(days=3)
        tuesday = monday + timedelta(days=1)
        clock = [datetime.combine(friday, time(6, 59), KST)]
        calls = []
        facts = {friday + timedelta(days=offset): (None, "fixture:holiday") for offset in (1, 2)}
        facts.update({day: ((time(9), time(15, 30)), "fixture:calendar") for day in (friday, monday, tuesday)})

        def load(*_args, **_kwargs):
            calls.append(clock[0])
            if clock[0].time() == time(7):
                raise OSError("first morning request fails")
            return facts

        async def run_at(owner, moments):
            values = iter(moments)
            clock[0] = next(values, None)

            async def advance(_seconds):
                clock[0] = next(values, None)

            with patch("kiwoom_monitor.central_server.krx_calendar_evidence.asyncio.sleep", advance):
                await owner._run(lambda: clock[0] is not None)

        provider = SimpleNamespace(load=load)
        friday_at = lambda hour, minute: datetime.combine(friday, time(hour, minute), KST)
        with tempfile.TemporaryDirectory() as folder, closing(SQLiteQueryStore(Path(folder) / "schedule.sqlite3")) as store:
            store.initialize()
            owner = KrxCalendarEvidence(store, provider, lambda: clock[0])
            asyncio.run(run_at(owner, tuple(friday_at(*slot) for slot in (
                (6, 59), (7, 0), (7, 4), (7, 5), (7, 20), (8, 0), (9, 30), (15, 0), (20, 4),
                (20, 5), (20, 10), (20, 30), (22, 0),
            ))))
            self.assertEqual([friday_at(7, 0), friday_at(7, 5), friday_at(20, 5)], calls)
            checked_at = store.load_document("krx_equity_calendar", "krx", "schedule")["document"]["checked_at"]
            self.assertEqual(friday_at(20, 5).isoformat(), checked_at)

            restored = KrxCalendarEvidence(store, provider, lambda: clock[0])
            monday_open = datetime.combine(monday, time(9), KST)
            saturday = friday + timedelta(days=1)
            asyncio.run(run_at(restored, (friday_at(20, 10), datetime.combine(saturday, time(7), KST),
                                          datetime.combine(saturday, time(20, 5), KST), monday_open)))
            self.assertEqual(3, len(calls))
            self.assertEqual(monday, next_trading_day(friday, venue="KRX", as_of=monday_open,
                                                     evidence=restored.as_of(monday_open)).trading_date)
            self.assertIsNotNone(restored.auction_at(monday_open))
            self.assertEqual((), restored.as_of(datetime.combine(tuesday, time(9), KST)))

    def test_holiday_table_and_special_equity_notice_are_used_together(self):
        saturday, sunday = date(2026, 11, 14), date(2026, 11, 15)
        holiday, special = date(2026, 11, 16), date(2026, 11, 17)
        calls = []
        closures = [holiday]

        def request(url, referer, fields):
            calls.append((url, fields))
            if fields is None:
                return b"mock PDF"
            bld = fields["bld"]
            if "mkd01100305" in bld:
                result = {"block1": [{"calnd_dd": day.isoformat(), "holdy_nm": "휴장일"} for day in closures]}
            elif bld.endswith("_03"):
                self.assertEqual("1234", fields["seq"])
                result = {"block1": [{"file_nm": "notice.pdf", "file_path": "/obk/dyn/noti/", "save_file_nm": "test.pdf"}]}
            else:
                result = {"output": [{"noti_dd": "2026/11/11", "noti_no": "1234", "totCnt": "1",
                                      "title": "증권시장(11.17) 거래시간 임시 변경"}]
                          if fields["sch_word"] == "거래시간" else []}
            return json.dumps(result).encode()

        notice = ("1) 유가증권시장, 코스닥시장 및 코넥스시장\n"
                  "정규시장* (대량·바스켓매매 포함) 09:00~15:30 10:00~16:30\n"
                  "2) 파생상품시장\n08:45~15:45 09:45~16:45")
        with patch("kiwoom_monitor.infrastructure.krx.market_calendar._pdf_text", return_value=notice):
            days = KrxMarketCalendar(request=request).load(saturday, special)
        self.assertTrue(all(days[day][0] is None for day in (saturday, sunday, holiday)))
        self.assertEqual(((time(10), time(16, 30)), "https://inc.krx.co.kr/attach/obk/dyn/noti/test.pdf"), days[special])
        self.assertEqual(1, sum(fields is None for _, fields in calls))
        closures.append(special)
        with patch("kiwoom_monitor.infrastructure.krx.market_calendar._pdf_text", return_value=notice):
            corrected = KrxMarketCalendar(request=request).load(saturday, special)
        self.assertIsNone(corrected[special][0])

    def test_live_close_consumes_refreshed_calendar_and_detects_revisions(self):
        from types import SimpleNamespace
        from tests.unit.test_close_and_ipo import CloseAndIpoTests

        fixture = CloseAndIpoTests()
        fixture.setUp()
        day = date(2026, 11, 13)
        following = day + timedelta(days=4)
        now = [datetime.combine(day, time(8), KST)]
        source = "https://inc.krx.co.kr/attach/obk/dyn/noti/test.pdf"
        facts = {day: ((time(10), time(16, 30)), source), following: ((time(10), time(15, 30)), source)}
        facts.update({day + timedelta(days=n): (None, source) for n in (1, 2, 3)})

        with tempfile.TemporaryDirectory() as folder, closing(SQLiteQueryStore(Path(folder) / "close-calendar.sqlite3")) as store:
            store.initialize()
            provider = SimpleNamespace(load=lambda _start, _end, *, as_of: facts)
            calendar = KrxCalendarEvidence(store, provider, lambda: now[0])
            calendar.refresh(now[0])
            rows = []
            for n, price in enumerate((100, 101, 104), 14):
                stamp = datetime.combine(day, time(16, n), KST)
                available = stamp + timedelta(minutes=1, seconds=2)
                payload = minute_bar_revision_payload({
                    "trading_date": day.isoformat(), "minute": stamp.strftime("%H:%M"), "code": fixture.symbol,
                    "market": "KRX", "open": price, "high": price, "low": price, "close": price, "volume": 100,
                    "trade_value_million_won": 1, "available_at": available.isoformat(),
                }, window_closed=True, capture_quality="complete", finalization_source="timer")
                rows.append({"kind": "minute_bar", "subject": f"{fixture.symbol}:KRX", "venue": "KRX",
                             "accepted_sequence": n, "revision_id": f"minute-{n}", "observation_key": stamp.isoformat(),
                             "available_at": available.isoformat(), "value_kind": "actual", "completeness": "complete",
                             "origin": "realtime", "source_id": "kiwoom-websocket-0B", "payload": payload})

            shadow_store = FakeStore(rows)
            config = {"version": MINUTE_SHADOW_VERSION, "family": CLOSE_FAMILY, "symbols": [fixture.symbol],
                      "signal_valid_seconds": 30, "strategy": {"capital_won": 100_000, "policy": asdict(fixture.close)},
                      "references": {"version": "strategy-reference/v1", "calendar": [], "listings": []}}
            monitor = CandidateMonitor.from_json(shadow_store, json.dumps(config), poll_seconds=1,
                                                universe_max_age_seconds=90, now_provider=lambda: now[0])
            monitor.set_krx_calendar_source(calendar.as_of)
            now[0] = datetime.fromisoformat(rows[-1]["available_at"])
            monitor.run_once()
            monitor.run_once()
            self.assertEqual(1, len(shadow_store.candidates), monitor.quality)
            [event] = shadow_store.candidates
            self.assertEqual("close_betting", event["strategy_id"])
            setup = event["close_ipo_entry_setup"]
            self.assertEqual("2026-11-13T16:20:00+09:00", setup["entry_deadline"])
            self.assertEqual("2026-11-13T16:30:00+09:00", setup["close_auction"]["ends_at"])
            self.assertEqual("2026-11-17T10:00:00+09:00", setup["next_open_at"])
            self.assertTrue(monitor.close_calendar_basis_current(event))

            facts[following] = ((time(11), time(15, 30)), source)
            now[0] += timedelta(seconds=1)
            calendar.refresh(now[0])
            self.assertFalse(monitor.close_calendar_basis_current(event))
            persisted = {**event, "accepted_sequence": 1, "expires_at": (now[0] + timedelta(seconds=20)).isoformat()}
            plan_store = SimpleNamespace(load_shadow_candidates=lambda *_args: {"events": [persisted]})

            def unexpected_price(_symbol):
                self.fail("An obsolete calendar reached the order preparation path")

            plan = MinutePlanManagement(plan_store, None, None, None, parameters={}, price_source=unexpected_price,
                                        now_provider=lambda: now[0], candidate_current=monitor.close_calendar_basis_current)
            control = SimpleNamespace(desired_state=SimpleNamespace(value="RUNNING"))
            with patch.object(plan._adapter, "matches_close_shadow", return_value=True):
                self.assertEqual(1, asyncio.run(plan.advance(control, limit=1, allow_entry=lambda: True)))

            restored = KrxCalendarEvidence(store, provider, lambda: now[0])
            restored.refresh(now[0])
            confirmed = next_trading_day(day, venue="KRX", as_of=now[0], evidence=restored.as_of(now[0]))
            self.assertEqual(time(11), confirmed.sessions[0].starts_at.time())

            now[0] = datetime.combine(day, time(16, 30, 5), KST)
            collector = CentralRealtimeCollector(lambda: "unused", "mock", RealtimeHub(), lambda: now[0], store,
                                                 closing_auction_source=restored.auction_at)
            collector._approved_trade_sources = {(fixture.symbol, "KRX")}
            collector._continuous_from = {(fixture.symbol, "KRX"): now[0] - timedelta(minutes=1)}

            async def accept_trade():
                collector._publish_parsed({"trnm": "REAL", "data": [{"type": "0B", "item": fixture.symbol,
                    "values": {"20": "163004", "10": "105", "13": "100", "14": "1", "15": "1"}}]})
                await collector._flush_snapshots(flush_latest=False)

            asyncio.run(accept_trade())
            [observed] = store.load_observation_revisions("closing_auction_price", f"{fixture.symbol}:KRX")
            self.assertEqual(setup["close_auction"]["ends_at"], observed["payload"]["auction_end"])

    def test_app_automatically_refreshes_after_close_selection(self):
        from fastapi.testclient import TestClient
        from kiwoom_monitor.central_server.app import create_app
        from kiwoom_monitor.central_server.config import CentralServerSettings
        from tests.unit.test_close_and_ipo import CloseAndIpoTests

        cut = datetime(2026, 11, 18, 7, tzinfo=KST)
        tomorrow = cut.date() + timedelta(days=1)
        checked = Event()
        refresh = KrxCalendarEvidence.refresh

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return cut.astimezone(tz)

        def publish(owner, now):
            refresh(owner, now)
            checked.set()

        fixture = CloseAndIpoTests()
        fixture.setUp()
        config = {"version": MINUTE_SHADOW_VERSION, "family": CLOSE_FAMILY, "symbols": [fixture.symbol],
                  "signal_valid_seconds": 30, "strategy": {"capital_won": 100_000, "policy": asdict(fixture.close)},
                  "references": {"version": "strategy-reference/v1", "calendar": [], "listings": []}}
        facts = {cut.date(): ((time(9), time(15, 30)), "krx:holidays"),
                 tomorrow: ((time(10), time(16, 30)), "krx:special")}
        with (tempfile.TemporaryDirectory() as folder,
              patch("kiwoom_monitor.central_server.app.datetime", Clock),
              patch.object(KrxMarketCalendar, "load", return_value=facts) as load,
              patch.object(KrxCalendarEvidence, "refresh", publish)):
            settings = CentralServerSettings(f"sqlite:///{Path(folder) / 'calendar-server.sqlite3'}", "private-token")
            with TestClient(create_app(settings)) as client:
                load.assert_not_called()
                enabled = client.put("/api/v1/settings/operations", headers={"Authorization": "Bearer private-token"},
                                     json={"shadow_candidate_enabled": True, "shadow_candidate_config": config})
                self.assertEqual(200, enabled.status_code, enabled.text)
                self.assertTrue(checked.wait(4), "CLOSE did not start the KRX refresh")
                monitor = client.app.state.candidate_monitor
                following = next_trading_day(cut.date(), venue="KRX", as_of=cut, evidence=monitor._calendar_evidence())
                self.assertEqual((tomorrow, time(10), time(16, 30)), (following.trading_date,
                                 following.sessions[0].starts_at.time(), following.sessions[-1].ends_at.time()))
            load.assert_called_once_with(cut.date() - timedelta(days=14), cut.date() + timedelta(days=14), as_of=cut.date())
