"""실시간 화면을 막지 않고 매매 진입 스냅샷을 저장한다."""

from __future__ import annotations

from pathlib import Path
from queue import Empty, Queue
import time
from dataclasses import replace
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from typing import Callable

from PySide6.QtCore import QThread, Signal

from .journal_database import JournalRepository, TradeEntrySnapshot
from .stock_news_repository import StockNewsRepository


class EntrySnapshotWriter(QThread):
    failed = Signal(str)

    def __init__(
        self, database_path: Path, news_database_path: Path | None = None,
        investor_loader: Callable[[str, datetime], dict[str, object]] | None = None,
        program_loader: Callable[[str, date], tuple[dict[str, object], ...]] | None = None,
    ) -> None:
        super().__init__()
        self._database_path = database_path
        self._news_database_path = news_database_path
        self._investor_loader = investor_loader
        self._program_loader = program_loader
        self._queue: Queue[object] = Queue()
        self._investor_cache: dict[str, tuple[float, dict[str, object]]] = {}

    def enqueue(self, snapshot: TradeEntrySnapshot) -> None:
        self._queue.put(snapshot)

    def enqueue_program_backfill(self, trade_date: date, codes: tuple[str, ...]) -> None:
        self._queue.put(("program_backfill", trade_date, codes))

    def enqueue_investor_backfill(self, trade_date: date, codes: tuple[str, ...]) -> None:
        self._queue.put(("investor_backfill", trade_date, codes))

    def set_investor_loader(self, loader: Callable[[str, datetime], dict[str, object]] | None) -> None:
        self._investor_loader = loader
        self._investor_cache.clear()

    def set_program_loader(self, loader: Callable[[str, date], tuple[dict[str, object], ...]] | None) -> None:
        self._program_loader = loader

    def run(self) -> None:
        try:
            repository = JournalRepository(self._database_path)
            news_repository = StockNewsRepository(self._news_database_path) if self._news_database_path else None
        except Exception as error:
            self.failed.emit(f"진입 스냅샷 DB 준비 실패: {error}")
            return
        while not self.isInterruptionRequested() or not self._queue.empty():
            try:
                snapshot = self._queue.get(timeout=0.1)
            except Empty:
                continue
            if isinstance(snapshot, tuple) and len(snapshot) == 3 and snapshot[0] == "program_backfill":
                self._backfill_program(repository, snapshot[1], snapshot[2])
                continue
            if isinstance(snapshot, tuple) and len(snapshot) == 3 and snapshot[0] == "investor_backfill":
                self._backfill_investor(repository, snapshot[1], snapshot[2])
                continue
            if not isinstance(snapshot, TradeEntrySnapshot):
                continue
            for attempt in range(5):
                try:
                    repository.save_entry_snapshot(snapshot)
                    break
                except Exception as error:
                    if attempt == 4:
                        self.failed.emit(f"진입 스냅샷 저장 실패: {error}")
                    else:
                        time.sleep(0.1 * (attempt + 1))
            news = news_at_execution(news_repository, snapshot)
            investor = self._investor_at_execution(snapshot)
            program = dict((snapshot.investor_flow or {}).get("program_trade", {}))
            if program:
                investor = {**investor, "program_trade": program}
            if news or investor:
                state = "realtime_enriched" if investor.get("available") else "realtime_partial"
                enriched = replace(snapshot, news=news, investor_flow=investor, capture_state=state)
                try:
                    repository.save_entry_snapshot(enriched)
                except Exception as error:
                    self.failed.emit(f"진입 스냅샷 보충 저장 실패: {error}")

    def _backfill_program(self, repository: JournalRepository, trade_date: date, codes: tuple[str, ...]) -> None:
        if self._program_loader is None:
            return
        candidates = repository.program_backfill_candidates(trade_date, codes)
        by_code: dict[str, list[TradeEntrySnapshot]] = {}
        for snapshot in candidates:
            by_code.setdefault(snapshot.stock_code, []).append(snapshot)
        for code, snapshots in by_code.items():
            try:
                rows = self._program_loader(code, trade_date)
            except Exception as error:
                self.failed.emit(f"{code} 장 마감 프로그램매매 보완 실패: {error}")
                continue
            for snapshot in snapshots:
                target = snapshot.executed_at.strftime("%H%M%S")
                eligible = [row for row in rows if str(row.get("trade_time", "")) <= target]
                if not eligible:
                    continue
                nearest = max(eligible, key=lambda row: str(row.get("trade_time", "")))
                repository.save_program_trade_backfill(snapshot.execution_key, {**nearest, "backfilled": True})

    def _backfill_investor(self, repository: JournalRepository, trade_date: date, codes: tuple[str, ...]) -> None:
        if self._investor_loader is None:
            return
        # 장 마감 시 순위표에서 빠진 체결 종목도 누락하지 않는다.
        target_codes = tuple(sorted(set(codes) | set(repository.entry_snapshot_codes(trade_date))))
        candidates = repository.investor_backfill_candidates(trade_date, target_codes)
        by_code: dict[str, list[TradeEntrySnapshot]] = {}
        for snapshot in candidates:
            by_code.setdefault(snapshot.stock_code, []).append(snapshot)
        observed_at = datetime.combine(trade_date, datetime_time(20, 5))
        for code, snapshots in by_code.items():
            try:
                value = self._investor_loader(code, observed_at)
            except Exception as error:
                self.failed.emit(f"{code} 장 마감 외국인·기관 수급 보완 실패: {error}")
                continue
            if not bool(value.get("available")):
                continue
            value = {**value, "backfilled": True}
            for snapshot in snapshots:
                repository.save_investor_flow_backfill(snapshot.execution_key, value)

    @staticmethod
    def _news_at_execution(repository: StockNewsRepository | None, snapshot: TradeEntrySnapshot) -> tuple[dict[str, object], ...]:
        return news_at_execution(repository, snapshot)

    def _investor_at_execution(self, snapshot: TradeEntrySnapshot) -> dict[str, object]:
        if self._investor_loader is None:
            return {}
        cached = self._investor_cache.get(snapshot.stock_code)
        if cached and time.monotonic() - cached[0] < 60:
            return {**cached[1], "cached": True}
        try:
            value = self._investor_loader(snapshot.stock_code, snapshot.executed_at)
        except Exception as error:
            return {"available": False, "error": str(error)}
        self._investor_cache[snapshot.stock_code] = (time.monotonic(), value)
        return value


KST = timezone(timedelta(hours=9))


def news_at_execution(repository: StockNewsRepository | None, snapshot: TradeEntrySnapshot) -> tuple[dict[str, object], ...]:
    if repository is None:
        return ()
    try:
        items = repository.load(snapshot.stock_code, limit=50)
    except Exception:
        return ()
    executed = snapshot.executed_at.replace(tzinfo=KST) if snapshot.executed_at.tzinfo is None else snapshot.executed_at.astimezone(KST)
    lower = executed - timedelta(hours=48)
    result = []
    for item in items:
        published = item.published_at
        if published is None:
            continue
        published = published.replace(tzinfo=KST) if published.tzinfo is None else published.astimezone(KST)
        if not lower <= published <= executed + timedelta(minutes=5):
            continue
        result.append({
            "title": item.title, "link": item.original_link or item.link,
            "published_at": published.isoformat(), "relevant": item.assessment.relevant,
            "category": item.assessment.category, "outlook": item.assessment.outlook,
            "reason": item.assessment.reason,
        })
        if len(result) >= 10:
            break
    return tuple(result)
