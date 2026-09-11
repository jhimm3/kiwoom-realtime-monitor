"""NAS가 데스크톱 앱과 무관하게 TOP20 시장 자료를 수집한다."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from datetime import datetime, time as clock_time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from kiwoom_monitor.application.minute_trade_value import MinuteTradeValueAggregator
from kiwoom_monitor.application.top20_trade_value_collector import Top20MinuteRecord, Top20TradeValueCollector
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import TradeTick
from kiwoom_monitor.infrastructure.krx.stock_catalog import fetch_krx_stock_catalog

from .database import QueryStore
from .market_observations import ranking_observation, top20_index_observation
from .persistent_outbox import JsonRecordOutbox
from .realtime_hub import RealtimeHub, RealtimeSubscriber
from .rest_broker import CentralRestBroker


logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))


class AutonomousTop20Service:
    """08:00~20:00 순위·0B 수집과 장후 편입종목 차트 보완을 담당한다."""

    def __init__(
        self, broker: CentralRestBroker, hub: RealtimeHub, store: QueryStore,
        *, now_provider: Callable[[], datetime] | None = None,
        catalog_loader: Callable[[], tuple[tuple[str, str, str], ...]] = fetch_krx_stock_catalog,
        outbox_path: Path | None = None,
    ) -> None:
        self._broker = broker
        self._hub = hub
        self._store = store
        self._now = now_provider or (lambda: datetime.now(KST))
        self._catalog_loader = catalog_loader
        self._outbox = JsonRecordOutbox(outbox_path) if outbox_path is not None else None
        self._subscriber: RealtimeSubscriber | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._collector = Top20TradeValueCollector()
        self._collector_lock = asyncio.Lock()
        self._trade_values = MinuteTradeValueAggregator(max_minutes=1_440)
        self._markets: dict[str, str] = {}
        self._market_catalog_day = ""
        self._nxt_eligible: dict[str, bool] = {}
        self._last_ranking_slot = ""
        self._last_backfill_day = ""
        self._entrants_day = ""
        self._entrant_first_seen: dict[str, str] = {}
        self._pending_index_records: dict[str, dict[str, Any]] = {}

    async def start(self) -> None:
        if self._tasks:
            return
        if self._outbox is not None:
            self._pending_index_records.update(await asyncio.to_thread(self._outbox.load))
            if self._pending_index_records:
                try:
                    await self._flush_pending_indexes()
                except Exception as error:
                    logger.warning("NAS TOP20 영속 재시도 복구 실패(계속 실행): %s", error)
        self._subscriber = self._hub.connect()
        self._tasks = [
            asyncio.create_task(self._schedule_loop(), name="nas-top20-schedule"),
            asyncio.create_task(self._event_loop(), name="nas-top20-events"),
            asyncio.create_task(self._index_loop(), name="nas-top20-index"),
        ]

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
        if self._subscriber is not None:
            self._hub.disconnect(self._subscriber)
            self._subscriber = None

    async def refresh_ranking_once(self, observed_at: datetime | None = None) -> tuple[str, ...]:
        now = observed_at or self._now()
        await self._ensure_market_catalog(now.date().isoformat())
        result = await self._broker.request("ka00198", "/api/dostk/stkinfo", {"qry_tp": "5"})
        raw_items = result.payload.get("item_inq_rank", result.payload.get("result_list", []))
        items = [row for row in raw_items if isinstance(row, dict)] if isinstance(raw_items, list) else []
        codes = tuple(dict.fromkeys(filter(None, (_stock_code(row) for row in items))))[:20]
        snapshot_key = _ranking_snapshot_key(items, now, result.payload)
        day = snapshot_key[:10]
        membership = {
            "observed_at": snapshot_key, "codes": list(codes), "items": items,
        }
        await asyncio.to_thread(
            self._store.save_dataset_snapshot,
            "top20_membership",
            day,
            snapshot_key,
            membership,
            observation=ranking_observation(
                day,
                snapshot_key,
                membership,
                self._now(),
                source="nas-autonomous-ka00198",
            ),
        )
        if codes:
            if self._entrants_day != day:
                previous = await asyncio.to_thread(self._store.load_documents, "top20_daily_entrants", day, 5000)
                self._entrant_first_seen = {
                    str(value.get("key", "")): str(value.get("document", {}).get("first_seen_at", snapshot_key))
                    for value in previous if value.get("key")
                }
                self._entrants_day = day
            await asyncio.to_thread(self._store.upsert_documents, "top20_daily_entrants", [
                {"owner": day, "key": code, "document": {
                    "code": code, "first_seen_at": self._entrant_first_seen.setdefault(code, snapshot_key),
                    "last_seen_at": snapshot_key,
                }}
                for code in codes
            ])
        async with self._collector_lock:
            update = self._advance(now)
            if update.completed is not None:
                try:
                    await self._save_index(update.completed)
                except Exception as error:
                    logger.warning("NAS TOP20 지수 저장 실패(재시도 예정): %s", error)
            self._collector.prepare(codes, now, enabled=True, collection_open=_collection_open(now))
        await self._update_subscription(codes)
        return codes

    async def _ensure_market_catalog(self, day: str) -> None:
        if self._market_catalog_day == day and self._markets:
            return
        stored = await asyncio.to_thread(
            self._store.load_documents, "stock_catalog", "krx", 10_000,
        )
        meta_day = ""
        markets: dict[str, str] = {}
        for value in stored:
            document = value.get("document", {})
            if not isinstance(document, dict):
                continue
            if value.get("key") == "_meta":
                meta_day = str(document.get("as_of", ""))
                continue
            code = str(document.get("code", value.get("key", ""))).strip()
            market = str(document.get("market", "")).strip()
            if code and market:
                markets[code] = market
        if markets:
            self._markets = markets
        if meta_day == day and markets:
            self._market_catalog_day = day
            return
        try:
            rows = await asyncio.to_thread(self._catalog_loader)
        except Exception as error:
            # 이미 보존된 카탈로그가 있으면 순위 수집은 계속한다. 시장이 없는
            # 종목만 기존처럼 '시장 미확인'으로 남겨 잘못 확정하지 않는다.
            logger.warning("NAS KRX 종목 카탈로그 갱신 실패(기존 자료 사용): %s", error)
            self._market_catalog_day = day if markets else ""
            return
        documents = [
            {"owner": "krx", "key": code, "document": {
                "code": code, "name": name, "market": market, "as_of": day,
            }}
            for code, name, market in rows
        ]
        documents.append({
            "owner": "krx", "key": "_meta",
            "document": {"as_of": day, "rows": len(rows)},
        })
        await asyncio.to_thread(self._store.replace_documents, "stock_catalog", documents)
        self._markets = {code: market for code, _name, market in rows if market}
        self._market_catalog_day = day

    async def backfill_day(self, day: str) -> None:
        documents = await asyncio.to_thread(self._store.load_documents, "top20_daily_entrants", day, 5000)
        codes = tuple(dict.fromkeys(str(value.get("key", "")) for value in documents if value.get("key")))
        for code in codes:
            if self._now().time().replace(tzinfo=None) >= clock_time(7, 40) and self._now().date().isoformat() != day:
                break
            try:
                eligible = await self._nxt_enabled(code)
                await self._backfill_daily(code, day, "KRX")
                await self._backfill_minutes(code, day, "KRX")
                if eligible:
                    await self._backfill_daily(code, day, "NXT")
                    await self._backfill_minutes(code, day, "NXT")
            except Exception as error:
                logger.warning("TOP20 편입종목 장후 보완 실패: %s %s", code, error)

    async def _schedule_loop(self) -> None:
        while True:
            now = self._now()
            if _collection_open(now) and now.second in {0, 30}:
                slot = now.strftime("%Y-%m-%dT%H:%M:%S")
                if slot != self._last_ranking_slot:
                    self._last_ranking_slot = slot
                    try:
                        await self.refresh_ranking_once(now)
                    except Exception as error:
                        logger.warning("NAS TOP20 순위 조회 실패: %s", error)
            if now.weekday() < 5 and now.time().replace(tzinfo=None) >= clock_time(20, 5):
                day = now.date().isoformat()
                if self._last_backfill_day != day:
                    self._last_backfill_day = day
                    await self.backfill_day(day)
            elif now.weekday() < 5 and now.time().replace(tzinfo=None) < clock_time(7, 40):
                day = _previous_trading_day(now).date().isoformat()
                if self._last_backfill_day != day:
                    self._last_backfill_day = day
                    await self.backfill_day(day)
            await asyncio.sleep(0.25)

    async def _event_loop(self) -> None:
        while True:
            subscriber = self._subscriber
            if subscriber is None:
                await asyncio.sleep(0.1)
                continue
            event = await subscriber.queue.get()
            if event.get("type") != "trade" or not isinstance(event.get("payload"), dict):
                continue
            payload = event["payload"]
            allowed = {name for name in TradeTick.__dataclass_fields__}
            try:
                tick = TradeTick(**{key: value for key, value in payload.items() if key in allowed})
            except TypeError:
                continue
            self._trade_values.ingest(tick, self._now())

    async def _index_loop(self) -> None:
        while True:
            try:
                await self._flush_pending_indexes()
                now = self._now()
                async with self._collector_lock:
                    update = self._advance(now)
                    if update.completed is not None:
                        await self._save_index(update.completed)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning("NAS TOP20 지수 수집 실패(계속 실행): %s", error)
            await asyncio.sleep(0.25)

    def _advance(self, now: datetime):
        return self._collector.advance(
            now, enabled=True,
            # 순위 요청 성공만으로는 1분 거래대금이 완전하지 않다. NAS와
            # 키움 WebSocket 사이가 끊긴 동안에는 행을 만들지 않아 차트와
            # 과거 분석에서 실제 수집 공백으로 드러나게 한다.
            collection_open=_collection_open(now) and self._hub.upstream_ready,
            value_provider=lambda code, minute: self._trade_values.bucket_trade_value_eok(code, 1, minute),
            market_provider=lambda code: self._markets.get(code, ""),
        )

    async def _save_index(self, record: Top20MinuteRecord) -> None:
        payload = asdict(record)
        payload["minute"] = record.minute.isoformat(timespec="minutes")
        payload["total"] = record.total
        snapshot_key = str(payload["minute"])
        self._pending_index_records[snapshot_key] = payload
        if self._outbox is not None:
            await asyncio.to_thread(self._outbox.put, snapshot_key, payload)
        await self._flush_pending_indexes()

    async def _flush_pending_indexes(self) -> None:
        for snapshot_key, payload in tuple(self._pending_index_records.items()):
            await self._write_index(payload)
            if self._outbox is not None:
                await asyncio.to_thread(self._outbox.remove, snapshot_key)
            if self._pending_index_records.get(snapshot_key) is payload:
                self._pending_index_records.pop(snapshot_key, None)

    async def _write_index(self, payload: dict[str, Any]) -> None:
        snapshot_key = str(payload["minute"])
        subject = snapshot_key[:10]
        await asyncio.to_thread(
            self._store.save_dataset_snapshot,
            "top20_index",
            subject,
            snapshot_key,
            payload,
            observation=top20_index_observation(
                subject,
                snapshot_key,
                payload,
                self._now(),
                str(payload.get("capture_state", "partial")),
            ),
        )

    async def _update_subscription(self, ranking_codes: tuple[str, ...]) -> None:
        if self._subscriber is None:
            return
        realtime_codes = self._collector.realtime_codes((), enabled=True)
        for code in ranking_codes:
            if code not in self._nxt_eligible:
                try:
                    await self._nxt_enabled(code)
                except Exception:
                    self._nxt_eligible[code] = False
        nxt_codes = [code for code in realtime_codes if self._nxt_eligible.get(code, False)]
        self._hub.update_subscription(self._subscriber, list(realtime_codes), nxt_codes)

    async def _nxt_enabled(self, code: str) -> bool:
        if code in self._nxt_eligible:
            return self._nxt_eligible[code]
        result = await self._broker.request("ka10100", "/api/dostk/stkinfo", {"stk_cd": code})
        enabled = str(result.payload.get("nxtEnable", result.payload.get("nxt_enable", ""))).upper() == "Y"
        self._nxt_eligible[code] = enabled
        return enabled

    async def _backfill_minutes(self, code: str, day: str, market: str) -> None:
        owner = f"{day}:{code}:{market}"
        if await asyncio.to_thread(self._store.load_documents, "market_data_coverage", owner, 1):
            return
        body = {"stk_cd": f"{code}_NX" if market == "NXT" else code,
                "tic_scope": "1", "upd_stkpc_tp": "1", "base_dt": day.replace("-", "")}
        cont_yn, next_key, pages = "N", "", 0
        complete = False
        while pages < 8:
            result = await self._request_with_retries("ka10080", "/api/dostk/chart", body, cont_yn, next_key)
            pages += 1
            rows = result.payload.get("stk_min_pole_chart_qry", [])
            dates = {_raw_bar_date(row, day) for row in rows if isinstance(row, dict)} if isinstance(rows, list) else set()
            if not result.has_next or not result.next_key or any(value < day.replace("-", "") for value in dates if value):
                complete = True
                break
            cont_yn, next_key = "Y", result.next_key
        if not complete:
            raise RuntimeError("분봉 연속조회 8페이지 안에 대상일 종료점을 확인하지 못했습니다.")
        await asyncio.to_thread(self._store.upsert_documents, "market_data_coverage", [{
            "owner": owner, "key": "complete", "document": {"kind": "minute", "pages": pages, "completed_at": self._now().isoformat()}
        }])

    async def _backfill_daily(self, code: str, day: str, market: str) -> None:
        owner = f"{code}:{market}"
        coverage = await asyncio.to_thread(self._store.load_documents, "market_data_coverage_daily", owner, 1)
        if coverage and str(coverage[0].get("document", {}).get("as_of", "")) >= day:
            return
        body = {"stk_cd": f"{code}_NX" if market == "NXT" else code,
                "base_dt": day.replace("-", ""), "upd_stkpc_tp": "1"}
        await self._request_with_retries("ka10081", "/api/dostk/chart", body, "N", "")
        bars = await asyncio.to_thread(self._store.load_daily_bars, code, market, 250)
        if not bars:
            # NXT 가능 종목도 빈 응답을 완료로 기록하면 이후 모든 조회가
            # rows=0 캐시를 재사용해 KRX 단독 신고가로 내려간다. 어느
            # 시장이든 실제 일봉이 저장된 뒤에만 coverage를 확정한다.
            raise RuntimeError(f"일봉 응답은 성공했지만 저장된 {market} 일봉이 없습니다.")
        await asyncio.to_thread(self._store.upsert_documents, "market_data_coverage_daily", [{
            "owner": owner, "key": "complete", "document": {"kind": "daily", "as_of": day, "rows": len(bars), "completed_at": self._now().isoformat()}
        }])

    async def _request_with_retries(self, api_id: str, path: str, body: dict[str, Any], cont_yn: str, next_key: str):
        error: Exception | None = None
        for attempt in range(3):
            try:
                return await self._broker.request(api_id, path, body, cont_yn=cont_yn, next_key=next_key)
            except Exception as current:
                error = current
                if attempt < 2:
                    await asyncio.sleep(1 + attempt * 2)
        assert error is not None
        raise error


def _collection_open(value: datetime) -> bool:
    current = value.time().replace(tzinfo=None)
    return value.weekday() < 5 and clock_time(8) <= current < clock_time(20)


def _stock_code(row: dict[str, Any]) -> str:
    raw = str(row.get("stk_cd", row.get("code", ""))).strip()
    return raw.removeprefix("A").removesuffix("_NX").removesuffix("_AL")


def _ranking_snapshot_key(items: list[dict[str, Any]], fallback: datetime, payload: dict[str, Any] | None = None) -> str:
    first = items[0] if items else {}
    payload = payload or {}
    raw_date = str(first.get("dt", payload.get("base_date", ""))).strip()
    raw_time = str(first.get("tm", payload.get("base_time", ""))).strip().zfill(6)
    if len(raw_date) == 8 and raw_date.isdigit() and len(raw_time) == 6 and raw_time.isdigit():
        return f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}T{raw_time[:2]}:{raw_time[2:4]}:{raw_time[4:]}"
    return fallback.isoformat(timespec="seconds")


def _raw_bar_date(row: dict[str, Any], fallback_day: str) -> str:
    raw = str(row.get("cntr_tm", "")).strip()
    return raw[:8] if len(raw) >= 14 and raw[:8].isdigit() else fallback_day.replace("-", "")


def _previous_trading_day(value: datetime) -> datetime:
    candidate = value - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate
