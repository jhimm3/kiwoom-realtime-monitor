"""Refresh and retain official KRX equity schedules for strategy consumers."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from datetime import datetime, time, timedelta
from hashlib import sha256

from kiwoom_monitor.application.market_session_schedule import (
    KST, KRX_CALENDAR_AFTER_CLOSE, MarketPhase, MarketSession, SessionSupport, SessionWindow, TradingDayEvidence,
    decode_strategy_references, krx_calendar_valid_through, schedule_version_for,
)
from kiwoom_monitor.domain.time_basis import as_kst

from .async_lifetime import await_native_call


logger = logging.getLogger(__name__)
_COLLECTION = "krx_equity_calendar"
_REFRESH_WINDOWS = ((time(7), time(7, 40)), (KRX_CALENDAR_AFTER_CLOSE, time(20, 30)))


class KrxCalendarEvidence:
    def __init__(self, store, provider, now_provider) -> None:
        self._store = store
        self._provider = provider
        self._now = now_provider
        self._task = None
        self._rows = ()
        self._auctions = {}
        self._validity = None
        self._restored = False
        self._refresh_failed = False

    def as_of(self, cutoff):
        if not self._current(cutoff):
            return ()
        return tuple(row for row in self._rows if row.available_at <= cutoff)

    def auction_at(self, at):
        if not self._current(at):
            return None
        row, window = self._auctions.get(as_kst(at).date(), (None, None))
        return window if row is not None and row.available_at <= at else None

    def _current(self, at):
        validity = self._validity
        return (not self._refresh_failed and validity is not None
                and validity[0] <= at and as_kst(at).date() <= validity[1])

    def start(self, needed) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(needed), name="krx-equity-calendar")

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self, needed):
        next_attempt = None
        while needed():
            now = as_kst(self._now())
            slot = next((now.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
                         for start, end in _REFRESH_WINDOWS if now.weekday() < 5 and start <= now.time() < end), None)
            if next_attempt is None or now >= next_attempt:
                try:
                    if not self._restored:
                        await await_native_call(self.restore)
                    if slot is not None and (self._validity is None or self._validity[0] < slot):
                        await await_native_call(self.refresh, now)
                except (OSError, RuntimeError, ValueError, TypeError, KeyError, ImportError) as error:
                    self._refresh_failed = True
                    logger.warning("KRX 주식시장 일정 갱신 실패: %s", type(error).__name__)
                    next_attempt = now + timedelta(minutes=5)
            await asyncio.sleep(30)

    def restore(self) -> None:
        if self._restored:
            return
        saved = self._store.load_documents(_COLLECTION, "krx", 1)
        if saved:
            document = saved[0]["document"]
            rows, _ = decode_strategy_references(document)
            self._publish(rows, as_kst(datetime.fromisoformat(document["checked_at"])))
        self._restored = True

    def refresh(self, now) -> None:
        self.restore()
        now = as_kst(now)
        day = now.date()
        first = day - timedelta(days=14)
        facts = self._provider.load(first, day + timedelta(days=14), as_of=day)
        if day not in facts:
            raise ValueError("current KRX holiday table is unavailable")
        observed = as_kst(self._now())
        if observed.date() != day:
            return
        rows = [row for row in self._rows if row.trading_date >= first]
        latest = {row.trading_date: row for row in rows}
        for effective_day in sorted(latest.keys() | facts.keys()):
            prior = latest.get(effective_day)
            if effective_day not in facts:
                # Preserve the prior revision for past decisions and publish the gap.
                if prior is not None and prior.sessions is not None:
                    rows.append(replace(prior, available_at=observed, revision_id=prior.revision_id + ":unresolved",
                                        sessions=None))
                continue
            hours, source = facts[effective_day]
            row = _evidence(effective_day, hours, source, observed)
            if prior is None or prior.revision_id != row.revision_id:
                rows.append(row)
        rows = tuple(rows)
        if rows != self._rows or self._validity is None or self._validity[0] != observed:
            self._store.upsert_documents(_COLLECTION, [{"owner": "krx", "key": "schedule", "document": {
                "version": "strategy-reference/v1", "checked_at": observed.isoformat(), "listings": [],
                "calendar": [_encode(row) for row in rows],
            }}])
        self._publish(rows, observed)

    def _publish(self, rows, checked):
        latest = {row.trading_date: row for row in rows}
        self._auctions = {day: (row, next((window for window in row.sessions or ()
                                         if window.session is MarketSession.KRX_CLOSING_AUCTION), None))
                          for day, row in latest.items()}
        self._rows = rows
        self._validity = (checked, krx_calendar_valid_through(checked, rows))
        self._refresh_failed = False


def _evidence(day, hours, source, observed):
    version = schedule_version_for(day)
    revision = sha256(f"{day}|{hours}|{source}|{version}".encode()).hexdigest()
    windows = ()
    if hours is not None:
        start, end = (datetime.combine(day, clock, tzinfo=KST) for clock in hours)
        auction_start = end - timedelta(minutes=10)
        windows = (
            SessionWindow("KRX", day, MarketSession.KRX_REGULAR, MarketPhase.CONTINUOUS,
                          start, auction_start, version, SessionSupport.SUPPORTED),
            SessionWindow("KRX", day, MarketSession.KRX_CLOSING_AUCTION, MarketPhase.AUCTION_ORDER_ENTRY,
                          auction_start, end, version, SessionSupport.SUPPORTED),
        )
    return TradingDayEvidence("KRX", day, observed, f"krx-equity:{revision}", source, windows)


def _encode(row):
    return {
        "venue": row.venue, "trading_date": row.trading_date.isoformat(), "available_at": row.available_at.isoformat(),
        "revision_id": row.revision_id, "evidence_ref": row.evidence_ref,
        "sessions": None if row.sessions is None else [{
            "session": window.session.value, "phase": window.phase.value, "starts_at": window.starts_at.isoformat(),
            "ends_at": window.ends_at.isoformat(), "schedule_version": window.schedule_version,
            "support": window.support.value,
        } for window in row.sessions],
    }
