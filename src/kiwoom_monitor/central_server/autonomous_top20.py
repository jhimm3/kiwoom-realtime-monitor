"""NAS가 데스크톱 앱과 무관하게 TOP20 시장 자료를 수집한다."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict
from datetime import datetime, time as clock_time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from kiwoom_monitor.application.minute_trade_value import MinuteTradeValueAggregator
from kiwoom_monitor.application.market_session_schedule import (
    KRX_AFTER_MARKET_EFFECTIVE_DATE,
    full_day_close_at,
)
from kiwoom_monitor.application.historical_high_service import HistoricalHighService
from kiwoom_monitor.application.top20_trade_value_collector import Top20MinuteRecord, Top20TradeValueCollector
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import ProgramTradeTick, TradeTick
from kiwoom_monitor.infrastructure.krx.stock_catalog import fetch_krx_stock_catalog
from kiwoom_monitor.domain.ranking import normalize_stock_code

from .database import QueryStore
from .market_ingest import fundamentals_document_is_current
from .market_observations import ranking_observation, top20_index_observation
from .persistent_outbox import JsonRecordOutbox
from .realtime_hub import RealtimeHub, RealtimeSubscriber
from .rest_broker import CentralRestBroker


logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))


class AutonomousTop20Service:
    """24시간 순위와 08:00~20:00 0B 수집, 장후 차트 보완을 담당한다."""

    STALE_RANKING_RETRY_LIMIT = 20

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
        self._fundamentals_ready: dict[str, str] = {}
        self._fundamentals_pending: set[str] = set()
        self._fundamentals_tasks: set[asyncio.Task[None]] = set()
        self._market_catalog_task: asyncio.Task[None] | None = None
        self._subscription_task: asyncio.Task[None] | None = None
        self._subscription_revision = 0
        self._subscription_day = ""
        self._last_ranking_slot = ""
        self._last_new_high_slot = ""
        self._last_aux_ranking_slots: dict[str, str] = {}
        self._last_backfill_day = ""
        self._backfill_task: asyncio.Task[None] | None = None
        self._entrants_day = ""
        self._entrant_first_seen: dict[str, str] = {}
        self._account_entry_codes: tuple[str, ...] = ()
        self._pending_index_records: dict[str, dict[str, Any]] = {}
        self._pending_program_snapshots: dict[str, dict[str, Any]] = {}
        self._latest_membership_snapshot: dict[str, Any] | None = None

    def latest_membership_snapshot(self, subject: str = "") -> dict[str, Any] | None:
        """Return the latest validated 20-slot projection without waiting for persistence."""
        value = self._latest_membership_snapshot
        if value is None or (subject and str(value["subject"]) != subject):
            return None
        payload = value["payload"]
        return {
            **value,
            "payload": {
                **payload,
                "codes": list(payload["codes"]),
                "items": [dict(item) for item in payload["items"]],
            },
        }

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
        for task in self._fundamentals_tasks:
            task.cancel()
        for task in self._tasks:
            task.cancel()
        for task in tuple(self._fundamentals_tasks):
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._fundamentals_tasks.clear()
        self._fundamentals_pending.clear()
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
        expected_at = _ranking_expected_at(now)
        refresh_started_at = time.monotonic()
        self._schedule_market_catalog(now.date().isoformat())
        items: list[dict[str, Any]] = []
        result = None
        stale_response = False
        partial_response = False
        request = getattr(self._broker, "request_unrecorded", self._broker.request)
        for retry_index in range(self.STALE_RANKING_RETRY_LIMIT + 1):
            # Freshness/20-slot validation comes before persistence. Persisting every
            # stale or partial candidate here used to block the next retry on the
            # PostgreSQL ingest path and made the desktop ranking appear seconds late.
            result = await request("ka00198", "/api/dostk/stkinfo", {"qry_tp": "5"})
            raw_items = result.payload.get("item_inq_rank", result.payload.get("result_list", []))
            items = [row for row in raw_items if isinstance(row, dict)] if isinstance(raw_items, list) else []
            snapshot_at = _ranking_snapshot_at(items, result.payload)
            stale_response = snapshot_at is not None and snapshot_at < expected_at
            partial_response = _ranking_response_has_empty_slots(items)
            if not stale_response and not partial_response:
                break
            if retry_index >= self.STALE_RANKING_RETRY_LIMIT:
                break
            delay = _stale_ranking_retry_delay(retry_index)
            if stale_response:
                logger.info(
                    "NAS ka00198이 이전 기준 스냅샷(%s)을 반환해 %.2f초 뒤 재조회합니다. (%d/%d)",
                    snapshot_at.strftime("%H:%M:%S"), delay,
                    retry_index + 1, self.STALE_RANKING_RETRY_LIMIT,
                )
            else:
                valid_count = sum(
                    bool(_stock_code(row) and str(row.get("stk_nm", "")).strip())
                    for row in items
                )
                logger.info(
                    "NAS ka00198 최신 응답이 부분 자료(%d/20)여서 %.2f초 뒤 재조회합니다. (%d/%d)",
                    valid_count, delay,
                    retry_index + 1, self.STALE_RANKING_RETRY_LIMIT,
                )
            await asyncio.sleep(delay)
        assert result is not None
        if stale_response or partial_response:
            logger.warning(
                "recording_gap: NAS TOP20 최신 완성 순위를 제한 시간 안에 받지 못해 이번 회차를 저장하지 않습니다."
            )
            return tuple(self._collector.active_codes)
        codes = tuple(dict.fromkeys(filter(None, (_stock_code(row) for row in items))))[:20]
        snapshot_key = _ranking_snapshot_key(items, now, result.payload)
        day = snapshot_key[:10]
        membership = {
            "observed_at": snapshot_key, "codes": list(codes), "items": items,
        }
        # Only a fresh, complete 20-slot response reaches this point. Publish
        # that validated projection before PostgreSQL persistence so the
        # desktop's live limit=1 read is not delayed by storage latency.
        projection_saved_at = time.time()
        self._latest_membership_snapshot = {
            "subject": day,
            "snapshot_key": snapshot_key,
            "saved_at": projection_saved_at,
            "payload": membership,
            "persistence_state": "pending",
        }
        persistence_started_at = time.monotonic()
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
        if (
            self._latest_membership_snapshot is not None
            and self._latest_membership_snapshot["snapshot_key"] == snapshot_key
        ):
            self._latest_membership_snapshot = {
                **self._latest_membership_snapshot,
                "persistence_state": "persisted",
            }
        logger.info(
            "NAS TOP20 최신 순위 저장 완료: 회차=%s 종목=%d 조회후_ms=%d 저장_ms=%d",
            snapshot_key, len(codes),
            round((persistence_started_at - refresh_started_at) * 1000),
            round((time.monotonic() - persistence_started_at) * 1000),
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
        self._schedule_subscription_update(day)
        if self._tasks:
            self._schedule_fundamentals(codes, day)
            self._schedule_new_highs(now)
            self._schedule_aux_rankings(now)
        return codes

    def _schedule_market_catalog(self, day: str) -> None:
        task = self._market_catalog_task
        if task is not None and not task.done():
            return
        task = asyncio.create_task(
            self._ensure_market_catalog(day), name="nas-market-catalog",
        )
        self._market_catalog_task = task
        self._fundamentals_tasks.add(task)
        task.add_done_callback(self._market_catalog_done)

    def _market_catalog_done(self, task: asyncio.Task[None]) -> None:
        self._fundamentals_tasks.discard(task)
        if self._market_catalog_task is task:
            self._market_catalog_task = None
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.warning("NAS KRX 종목 카탈로그 준비 실패(순위 수집 계속): %s", error)

    def _schedule_subscription_update(self, day: str) -> None:
        self._subscription_revision += 1
        self._subscription_day = day
        # TOP20의 KRX 구독은 NXT 여부나 계좌 편입 종목 조회를 기다리지 않는다.
        self._publish_known_subscription()
        task = self._subscription_task
        if task is not None and not task.done():
            return
        task = asyncio.create_task(
            self._refresh_subscription_until_current(), name="nas-top20-subscription",
        )
        self._subscription_task = task
        self._fundamentals_tasks.add(task)
        task.add_done_callback(self._subscription_done)

    def _subscription_done(self, task: asyncio.Task[None]) -> None:
        self._fundamentals_tasks.discard(task)
        if self._subscription_task is task:
            self._subscription_task = None
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.warning("NAS TOP20 구독 보완 실패(순위 수집 계속): %s", error)

    async def _refresh_subscription_until_current(self) -> None:
        while True:
            revision = self._subscription_revision
            day = self._subscription_day
            account_entry_codes = await self._load_account_entry_codes(day)
            await self._update_subscription()
            if self._tasks and account_entry_codes:
                self._schedule_fundamentals(account_entry_codes, day)
            if revision == self._subscription_revision:
                return

    def _schedule_aux_rankings(self, now: datetime) -> None:
        due = []
        schedules = {
            "4": now.strftime("%Y-%m-%dT%H:%M:%S") if now.second in {0, 30} else "",
            "1": now.strftime("%Y-%m-%dT%H:%M") if now.second == 0 else "",
            "2": now.strftime("%Y-%m-%dT%H:%M") if now.second == 0 and now.minute % 10 == 0 else "",
            "3": now.strftime("%Y-%m-%dT%H") if now.second == 0 and now.minute == 0 else "",
        }
        for query_type, slot in schedules.items():
            if slot and self._last_aux_ranking_slots.get(query_type) != slot:
                self._last_aux_ranking_slots[query_type] = slot
                due.append(query_type)
        if not due:
            return
        task = asyncio.create_task(
            self._refresh_aux_rankings(tuple(due)), name="nas-aux-rankings",
        )
        self._fundamentals_tasks.add(task)
        task.add_done_callback(self._fundamentals_tasks.discard)

    async def _refresh_aux_rankings(self, query_types: tuple[str, ...]) -> None:
        for query_type in query_types:
            try:
                await self._broker.request(
                    "ka00198", "/api/dostk/stkinfo", {"qry_tp": query_type},
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning("NAS 순위 기준 %s 갱신 실패: %s", query_type, error)

    def _schedule_new_highs(self, now: datetime) -> None:
        slot = now.strftime("%Y-%m-%dT%H:%M")
        if slot == self._last_new_high_slot:
            return
        self._last_new_high_slot = slot
        task = asyncio.create_task(self._refresh_new_highs(), name="nas-new-highs")
        self._fundamentals_tasks.add(task)
        task.add_done_callback(self._fundamentals_tasks.discard)

    async def _refresh_new_highs(self) -> None:
        for period in (5, 20, 250):
            try:
                await self._broker.request("ka10016", "/api/dostk/stkinfo", {
                    "mrkt_tp": "000", "ntl_tp": "1", "high_low_close_tp": "1",
                    "stk_cnd": "0", "trde_qty_tp": "00000", "crd_cnd": "0",
                    "updown_incls": "0", "dt": str(period), "stex_tp": "1",
                })
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning("NAS %d일 신고가 갱신 실패: %s", period, error)

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
        unique_rows: dict[str, tuple[str, str]] = {}
        for code, name, market in rows:
            if code:
                unique_rows.setdefault(code, (name, market))
        documents = [
            {"owner": "krx", "key": code, "document": {
                "code": code, "name": name, "market": market, "as_of": day,
            }}
            for code, (name, market) in unique_rows.items()
        ]
        documents.append({
            "owner": "krx", "key": "_meta",
            "document": {"as_of": day, "rows": len(unique_rows)},
        })
        await asyncio.to_thread(self._store.replace_documents, "stock_catalog", documents)
        self._markets = {
            code: market for code, (_name, market) in unique_rows.items() if market
        }
        self._market_catalog_day = day

    async def backfill_day(self, day: str) -> None:
        target_day = datetime.fromisoformat(day).date()
        now = self._now()
        close_at = full_day_close_at(target_day, venue="KRX")
        if (
            target_day >= KRX_AFTER_MARKET_EFFECTIVE_DATE
            and target_day == now.date()
            and (close_at is None or now.time().replace(tzinfo=None) < close_at.time().replace(tzinfo=None))
        ):
            logger.info("KRX 전체일 차트 보완 대기: %s (20:00 종료 전)", day)
            return
        try:
            await self._backfill_market_indexes(day)
        except Exception as error:
            logger.warning("시장지수 장후 보완 실패: %s %s", day, error)
        documents = await asyncio.to_thread(self._store.load_documents, "top20_daily_entrants", day, 5000)
        account_entries = await asyncio.to_thread(
            self._store.load_documents, "account_entry_symbols_daily", day, 5000,
        )
        cohort = await asyncio.to_thread(self._store.load_hot_cohort, active_only=False)
        codes = tuple(dict.fromkeys([
            *(str(value.get("key", "")) for value in documents if value.get("key")),
            *(str(value.get("key", "")) for value in account_entries if value.get("key")),
            *(str(value.get("stock_code", "")) for value in cohort
              if value.get("stock_code") and value.get("entry_session") == day),
        ]))
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
            try:
                await self._backfill_candidate_flows(code, day)
            except Exception as error:
                logger.warning("TOP20 편입종목 장후 수급 보완 실패: %s %s", code, error)

    async def _schedule_loop(self) -> None:
        first_iteration = True
        while True:
            now = self._now()
            slot = _ranking_expected_at(now).strftime("%Y-%m-%dT%H:%M:%S")
            if first_iteration or slot != self._last_ranking_slot:
                self._last_ranking_slot = slot
                try:
                    await self.refresh_ranking_once(now)
                except Exception as error:
                    logger.warning("recording_gap: NAS TOP20 순위 조회/저장 실패: %s", error)
            first_iteration = False
            if now.weekday() < 5 and now.time().replace(tzinfo=None) >= clock_time(20, 5):
                day = now.date().isoformat()
                if self._last_backfill_day != day:
                    self._last_backfill_day = day
                    self._schedule_backfill(day)
            elif now.weekday() < 5 and now.time().replace(tzinfo=None) < clock_time(7, 40):
                day = _previous_trading_day(now).date().isoformat()
                if self._last_backfill_day != day:
                    self._last_backfill_day = day
                    self._schedule_backfill(day)
            await asyncio.sleep(0.25)

    def _schedule_backfill(self, day: str) -> None:
        task = self._backfill_task
        if task is not None and not task.done():
            return
        task = asyncio.create_task(
            self.backfill_day(day), name=f"nas-top20-backfill-{day}",
        )
        self._backfill_task = task
        self._fundamentals_tasks.add(task)
        task.add_done_callback(self._backfill_done)

    def _backfill_done(self, task: asyncio.Task[None]) -> None:
        self._fundamentals_tasks.discard(task)
        if self._backfill_task is task:
            self._backfill_task = None
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.warning("TOP20 장후 보완 실패(다음 거래일 재확인): %s", error)

    async def _event_loop(self) -> None:
        while True:
            subscriber = self._subscriber
            if subscriber is None:
                await asyncio.sleep(0.1)
                continue
            event = await subscriber.queue.get()
            if not isinstance(event.get("payload"), dict):
                continue
            payload = event["payload"]
            if event.get("type") == "program_trade":
                allowed = {name for name in ProgramTradeTick.__dataclass_fields__}
                try:
                    tick = ProgramTradeTick(**{
                        key: value for key, value in payload.items() if key in allowed
                    })
                except TypeError:
                    continue
                observed_at = self._now()
                self._pending_program_snapshots[tick.code] = {
                    "subject": tick.code,
                    "snapshot_key": (
                        f"{observed_at:%Y%m%d}:REALTIME:"
                        f"{(tick.trade_time or observed_at.strftime('%H%M%S')).replace(':', '')}"
                    ),
                    "payload": {"market": tick.market, "rows": [{
                        "available": True, "source": "kiwoom_realtime_0w",
                        "trade_time": tick.trade_time or "", "market": tick.market,
                        "net_buy_quantity": tick.net_buy_quantity,
                        "net_buy_quantity_change": tick.net_buy_quantity_change,
                        "net_buy_amount_million_won": tick.net_buy_amount_million_won,
                        "net_buy_amount_change_million_won": tick.net_buy_amount_change_million_won,
                    }]},
                }
                continue
            if event.get("type") != "trade":
                continue
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
                await self._flush_program_snapshots()
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

    async def _flush_program_snapshots(self) -> None:
        pending, self._pending_program_snapshots = self._pending_program_snapshots, {}
        if not pending:
            return
        try:
            await asyncio.to_thread(self._write_program_snapshots, tuple(pending.values()))
        except Exception:
            for code, value in pending.items():
                self._pending_program_snapshots.setdefault(code, value)
            raise

    def _write_program_snapshots(self, values: tuple[dict[str, Any], ...]) -> None:
        self._store.save_dataset_snapshots([
            (
                "program_flow", str(value["subject"]), str(value["snapshot_key"]),
                dict(value["payload"]), None,
            )
            for value in values
        ])

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

    def _publish_known_subscription(self) -> None:
        if self._subscriber is None:
            return
        top20_codes = self._collector.realtime_codes((), enabled=True)
        realtime_codes = tuple(dict.fromkeys((
            *top20_codes,
            *self._account_entry_codes,
        )))
        nxt_codes = [code for code in realtime_codes if self._nxt_eligible.get(code, False)]
        self._hub.update_subscription(
            self._subscriber, list(realtime_codes), nxt_codes,
            program_codes=list(realtime_codes),
            priority_codes=list(realtime_codes),
        )

    async def _update_subscription(self) -> None:
        if self._subscriber is None:
            return
        realtime_codes = tuple(dict.fromkeys((
            *self._collector.realtime_codes((), enabled=True),
            *self._account_entry_codes,
        )))
        for code in realtime_codes:
            if code not in self._nxt_eligible:
                try:
                    await self._nxt_enabled(code)
                except Exception:
                    self._nxt_eligible[code] = False
        self._publish_known_subscription()

    async def _load_account_entry_codes(self, day: str) -> tuple[str, ...]:
        """실제 매수 체결 종목을 순위 계산과 분리된 시장자료 수집 대상으로 읽는다."""
        values = await asyncio.to_thread(
            self._store.load_documents, "account_entry_symbols_daily", day, 5000,
        )
        codes = tuple(dict.fromkeys(filter(None, (
            normalize_stock_code(value.get("key")) for value in values
        ))))
        self._account_entry_codes = codes
        return codes

    async def _nxt_enabled(self, code: str) -> bool:
        if code in self._nxt_eligible:
            return self._nxt_eligible[code]
        stored = await asyncio.to_thread(
            self._store.load_documents, "stock_nxt_eligibility", code, 1,
        )
        if stored:
            document = stored[0].get("document", {})
            if isinstance(document, dict):
                payload = document.get("payload", {})
                enabled = document.get("enabled")
                if isinstance(payload, dict) and enabled is None:
                    raw = payload.get("nxtEnable", payload.get("nxt_enable"))
                    if raw is not None:
                        enabled = str(raw).strip().upper() == "Y"
                if isinstance(enabled, bool):
                    self._nxt_eligible[code] = enabled
                    return enabled
        result = await self._broker.request("ka10100", "/api/dostk/stkinfo", {"stk_cd": code})
        enabled = str(result.payload.get("nxtEnable", result.payload.get("nxt_enable", ""))).upper() == "Y"
        self._nxt_eligible[code] = enabled
        return enabled

    def _schedule_fundamentals(self, codes: tuple[str, ...], day: str) -> None:
        missing = tuple(
            code for code in codes
            if self._fundamentals_ready.get(code) != day and code not in self._fundamentals_pending
        )
        if not missing:
            return
        self._fundamentals_pending.update(missing)
        task = asyncio.create_task(
            self._ensure_fundamentals(missing, day),
            name="nas-top20-entry-data",
        )
        self._fundamentals_tasks.add(task)
        task.add_done_callback(self._fundamentals_tasks.discard)

    async def _ensure_fundamentals(self, codes: tuple[str, ...], day: str = "") -> None:
        """추적 종목의 기본정보와 신고가용 일봉을 거래일마다 준비한다."""
        current = self._now()
        current_day = (
            current.date() if current.tzinfo is None
            else current.astimezone(KST).date()
        )
        target_day = day or current_day.isoformat()
        try:
            for code in codes:
                if self._fundamentals_ready.get(code) == target_day:
                    continue
                try:
                    stored = await asyncio.to_thread(
                        self._store.load_documents, "stock_fundamentals", code, 1,
                    )
                    document = stored[0].get("document", {}) if stored else {}
                    if not fundamentals_document_is_current(
                        document, datetime.fromisoformat(target_day).date(),
                    ):
                        await self._broker.request(
                            "ka10001", "/api/dostk/stkinfo", {"stk_cd": code},
                        )
                    if day:
                        await self._backfill_entry_minutes(code, day)
                        await self._ensure_entry_daily_history(code, day)
                        await self._capture_candidate_investor_flow(code, day)
                        await self._ensure_historical_high(code, day)
                    self._fundamentals_ready[code] = target_day
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    logger.warning("TOP20 편입종목 자료 준비 실패: %s %s", code, error)
        finally:
            self._fundamentals_pending.difference_update(codes)

    async def _ensure_entry_daily_history(self, code: str, day: str) -> None:
        """신규 추적 종목의 신고가 계산용 일봉이 비었을 때 NAS가 한 번 채운다."""
        markets = ["KRX"]
        if await self._nxt_enabled(code):
            markets.append("NXT")
        for market in markets:
            stored = await asyncio.to_thread(
                self._store.load_daily_bars, code, market, 1,
            )
            if stored:
                continue
            await self._broker.request(
                "ka10081", "/api/dostk/chart", {
                    "stk_cd": f"{code}_NX" if market == "NXT" else code,
                    "base_dt": day.replace("-", ""), "upd_stkpc_tp": "1",
                },
            )

    async def _backfill_entry_minutes(self, code: str, day: str) -> None:
        """새 편입 종목의 등장 전 당일 분봉을 NAS가 한 번 준비한다."""
        markets = ["KRX"]
        if await self._nxt_enabled(code):
            markets.append("NXT")
        for market in markets:
            owner = f"{day}:{code}:{market}"
            stored = await asyncio.to_thread(
                self._store.load_documents, "market_data_coverage_intraday", owner, 1,
            )
            if stored:
                continue
            body = {
                "stk_cd": f"{code}_NX" if market == "NXT" else code,
                "tic_scope": "1", "upd_stkpc_tp": "1", "base_dt": day.replace("-", ""),
            }
            cont_yn, next_key, pages = "N", "", 0
            while pages < 8:
                result = await self._request_with_retries(
                    "ka10080", "/api/dostk/chart", body, cont_yn, next_key,
                )
                pages += 1
                rows = result.payload.get("stk_min_pole_chart_qry", [])
                dates = {
                    _raw_bar_date(row, day) for row in rows if isinstance(row, dict)
                } if isinstance(rows, list) else set()
                if (
                    not result.has_next or not result.next_key
                    or any(value < day.replace("-", "") for value in dates if value)
                ):
                    break
                cont_yn, next_key = "Y", result.next_key
            else:
                raise RuntimeError("편입 전 분봉 종료점을 8페이지 안에 확인하지 못했습니다.")
            await asyncio.to_thread(self._store.upsert_documents, "market_data_coverage_intraday", [{
                "owner": owner, "key": "entry_backfill", "document": {
                    "kind": "minute", "scope": "through_entry", "pages": pages,
                    "as_of": day, "completed_at": self._now().isoformat(),
                },
            }])

    async def _capture_candidate_investor_flow(self, code: str, day: str) -> None:
        """후보 최초 편입 때 수급 원본을 NAS에서 한 번 확보한다."""
        owner = f"{day}:{code}"
        if await asyncio.to_thread(
            self._store.load_documents, "candidate_flow_capture", owner, 1,
        ):
            return
        raw_day = day.replace("-", "")
        body = {
            "stk_cd": f"{code}_AL", "strt_dt": raw_day, "end_dt": raw_day,
            "orgn_prsm_unp_tp": "1", "for_prsm_unp_tp": "1",
        }
        try:
            await self._broker.request("ka10045", "/api/dostk/mrkcond", body)
        except Exception:
            body["stk_cd"] = code
            await self._broker.request("ka10045", "/api/dostk/mrkcond", body)
        await asyncio.to_thread(self._store.upsert_documents, "candidate_flow_capture", [{
            "owner": owner, "key": "initial", "document": {
                "as_of": day, "captured_at": self._now().isoformat(),
                "scope": "candidate_first_seen",
            },
        }])

    async def _ensure_historical_high(self, code: str, day: str) -> None:
        """TOP20 후보의 수정주가 기준 역사적 고가를 NAS가 하루 한 번 계산한다."""
        stored = await asyncio.to_thread(
            self._store.load_documents, "historical_highs", code, 1,
        )
        if stored and str(stored[0].get("document", {}).get("checked_on", "")) >= day:
            return
        loop = asyncio.get_running_loop()
        adapter = _AsyncBrokerChartAdapter(self._broker, loop)
        bars = await asyncio.to_thread(self._store.load_daily_bars, code, "KRX", 250)
        high_250 = max(
            (int(value.get("high", 0)) for value in bars if value.get("high") is not None),
            default=0,
        ) or None
        include_nxt = await self._nxt_enabled(code)
        target = await asyncio.to_thread(
            HistoricalHighService(
                adapter, include_nxt=include_nxt,
                high_250_loader=lambda _code: high_250,
            ).load,
            code,
        )
        await asyncio.to_thread(self._store.upsert_documents, "historical_highs", [{
            "owner": code, "key": "latest", "document": {
                "checked_on": day, "target": asdict(target),
            },
        }])

    async def _backfill_candidate_flows(self, code: str, day: str) -> None:
        """장후 확정 수급과 0W 누락분을 NAS에서 종목당 한 번 보완한다."""
        owner = f"{day}:{code}"
        coverage = await asyncio.to_thread(
            self._store.load_documents, "candidate_flow_finalization", owner, 1,
        )
        if coverage:
            return
        raw_day = day.replace("-", "")
        investor_body = {
            "stk_cd": f"{code}_AL", "strt_dt": raw_day, "end_dt": raw_day,
            "orgn_prsm_unp_tp": "1", "for_prsm_unp_tp": "1",
        }
        program_body = {"amt_qty_tp": "1", "stk_cd": f"{code}_AL", "date": raw_day}
        try:
            await self._broker.request("ka10045", "/api/dostk/mrkcond", investor_body)
        except Exception:
            investor_body["stk_cd"] = code
            await self._broker.request("ka10045", "/api/dostk/mrkcond", investor_body)
        try:
            await self._broker.request("ka90008", "/api/dostk/mrkcond", program_body)
        except Exception:
            program_body["stk_cd"] = code
            await self._broker.request("ka90008", "/api/dostk/mrkcond", program_body)
        await asyncio.to_thread(self._store.upsert_documents, "candidate_flow_finalization", [{
            "owner": owner, "key": "complete", "document": {
                "as_of": day, "completed_at": self._now().isoformat(),
                "scope": "candidate_after_close",
            },
        }])

    async def _backfill_market_indexes(self, day: str) -> None:
        """코스피·코스닥 분봉과 일봉을 NAS에서 거래일당 한 번 확정한다."""
        if await asyncio.to_thread(
            self._store.load_documents, "market_index_chart_coverage", day, 1,
        ):
            return
        raw_day = day.replace("-", "")
        completed: list[str] = []
        for market, code in (("kospi", "001"), ("kosdaq", "101")):
            minutes: list[dict[str, Any]] = []
            cont_yn, next_key = "N", ""
            for _ in range(8):
                result = await self._broker.request(
                    "ka20005", "/api/dostk/chart",
                    {"inds_cd": code, "tic_scope": "1", "base_dt": raw_day},
                    cont_yn=cont_yn, next_key=next_key,
                )
                rows = result.payload.get("inds_min_pole_qry", [])
                if not isinstance(rows, list):
                    raise RuntimeError(f"{market} 지수 분봉 응답 형식이 올바르지 않습니다.")
                minutes.extend(value for value in rows if isinstance(value, dict))
                dates = {
                    str(value.get("cntr_tm", ""))[:8]
                    for value in rows if isinstance(value, dict)
                }
                if (
                    not result.has_next or not result.next_key
                    or any(value and value < raw_day for value in dates)
                ):
                    break
                cont_yn, next_key = "Y", result.next_key
            daily = await self._broker.request(
                "ka20006", "/api/dostk/chart", {"inds_cd": code, "base_dt": raw_day},
            )
            daily_rows = daily.payload.get("inds_dt_pole_qry", [])
            if not isinstance(daily_rows, list):
                raise RuntimeError(f"{market} 지수 일봉 응답 형식이 올바르지 않습니다.")
            await asyncio.to_thread(
                self._store.save_dataset_snapshot,
                "market_index_chart", f"{raw_day}:{market}", raw_day,
                {
                    "market": market, "as_of": day,
                    "minutes": minutes,
                    "daily": [value for value in daily_rows if isinstance(value, dict)],
                },
            )
            completed.append(market)
        await asyncio.to_thread(self._store.upsert_documents, "market_index_chart_coverage", [{
            "owner": day, "key": "complete", "document": {
                "as_of": day, "markets": completed,
                "completed_at": self._now().isoformat(),
            },
        }])

    async def _backfill_minutes(self, code: str, day: str, market: str) -> None:
        owner = f"{day}:{code}:{market}"
        coverage = await asyncio.to_thread(
            self._store.load_documents, "market_data_coverage", owner, 1,
        )
        if coverage:
            document = coverage[0].get("document", {})
            if (
                isinstance(document, dict)
                and document.get("kind") == "minute"
                and document.get("window_closed") is True
                and document.get("session_finalized") is True
            ):
                return
            # 구 빌드는 kind만 기록했다. 행이 있다는 이유로 건너뛰지 않고
            # 전체 연속조회를 다시 수행해 명시적인 완료 문서로 승격한다.
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
            "owner": owner, "key": "complete", "document": {
                "kind": "minute", "pages": pages, "scope": "full_day",
                "window_closed": True, "session_finalized": True,
                "as_of": day, "completed_at": self._now().isoformat(),
            }
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
        if not any(str(bar.get("trading_date", "")) == day for bar in bars):
            # NXT 가능 종목도 빈 응답을 완료로 기록하면 이후 모든 조회가
            # rows=0 캐시를 재사용해 KRX 단독 신고가로 내려간다. 어느
            # 시장이든 실제 일봉이 저장된 뒤에만 coverage를 확정한다.
            raise RuntimeError(f"일봉 응답은 성공했지만 저장된 {market} 일봉이 없습니다.")
        await asyncio.to_thread(self._store.upsert_documents, "market_data_coverage_daily", [{
            "owner": owner, "key": "complete", "document": {
                "kind": "daily", "scope": "full_day", "as_of": day,
                "window_closed": True, "session_finalized": True,
                "rows": len(bars), "completed_at": self._now().isoformat(),
            }
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


def _ranking_collection_due(value: datetime) -> bool:
    """조회순위는 장 운영시간과 무관하게 매 30초 경계에 갱신한다."""
    return value.second in {0, 30}


def _ranking_expected_at(value: datetime) -> datetime:
    """현재 시각이 속한 30초 순위 회차의 기준 시각을 반환한다."""
    second = 30 if value.second >= 30 else 0
    return value.replace(second=second, microsecond=0, tzinfo=None)


def _ranking_response_has_empty_slots(items: list[dict[str, Any]]) -> bool:
    """20행 응답 안의 빈 순위 자리를 키움 갱신 중 부분 응답으로 판정한다."""
    if len(items) < 20:
        return True
    valid_count = sum(
        bool(_stock_code(row) and str(row.get("stk_nm", "")).strip())
        for row in items[:20]
    )
    return valid_count < 20


def _stock_code(row: dict[str, Any]) -> str:
    raw = str(row.get("stk_cd", row.get("code", ""))).strip()
    return normalize_stock_code(raw)


def _ranking_snapshot_key(items: list[dict[str, Any]], fallback: datetime, payload: dict[str, Any] | None = None) -> str:
    observed_at = _ranking_snapshot_at(items, payload)
    if observed_at is not None:
        return observed_at.isoformat(timespec="seconds")
    return fallback.isoformat(timespec="seconds")


def _ranking_snapshot_at(items: list[dict[str, Any]], payload: dict[str, Any] | None = None) -> datetime | None:
    first = items[0] if items else {}
    payload = payload or {}
    raw_date = str(first.get("dt", payload.get("base_date", ""))).strip()
    raw_time = str(first.get("tm", payload.get("base_time", ""))).strip().zfill(6)
    if len(raw_date) == 8 and raw_date.isdigit() and len(raw_time) == 6 and raw_time.isdigit():
        try:
            return datetime.strptime(f"{raw_date}{raw_time}", "%Y%m%d%H%M%S")
        except ValueError:
            return None
    return None


def _stale_ranking_retry_delay(retry_index: int) -> float:
    index = max(0, retry_index)
    if index < 2:
        return 0.25
    if index < 4:
        return 0.5
    return 0.75


def _raw_bar_date(row: dict[str, Any], fallback_day: str) -> str:
    raw = str(row.get("cntr_tm", "")).strip()
    return raw[:8] if len(raw) >= 14 and raw[:8].isdigit() else fallback_day.replace("-", "")


def _previous_trading_day(value: datetime) -> datetime:
    candidate = value - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


class _AsyncBrokerChartAdapter:
    """동기 계산기를 NAS의 단일 비동기 broker queue에 연결한다."""

    def __init__(self, broker: CentralRestBroker, loop: asyncio.AbstractEventLoop) -> None:
        self._broker, self._loop = broker, loop

    def request_with_continuation(
        self, api_id: str, path: str, body: dict[str, Any], *,
        cont_yn: str = "N", next_key: str = "",
    ) -> tuple[dict[str, Any], bool, str]:
        future = asyncio.run_coroutine_threadsafe(
            self._broker.request(
                api_id, path, body, cont_yn=cont_yn, next_key=next_key,
            ),
            self._loop,
        )
        result = future.result()
        return result.payload, result.has_next, result.next_key
