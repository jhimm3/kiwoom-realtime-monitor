"""실시간 화면을 막지 않고 매매 진입 스냅샷을 저장한다."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Lock
import time
from dataclasses import dataclass, replace
from datetime import date, datetime, time as datetime_time
from typing import Any, Callable

from PySide6.QtCore import QThread, Signal

from .journal_database import JournalRepository
from .journal_snapshot_repository import TradeEntrySnapshot
from .journal_snapshot_service import news_at_execution
from .stock_news_repository import StockNewsRepository


class SourceRelease:
    """Read-only completion of the retired source's actual loader invocations.

    This is not a writer/native-save completion receipt or permission to close
    any client still installed elsewhere. Only the source owner releases it.
    """

    def __init__(self) -> None:
        self._released = Event()

    @property
    def released(self) -> bool:
        return self._released.is_set()

    def wait(self) -> None:
        """Wait in an owning background task, never in the UI/writer loop."""
        self._released.wait()

    def _release(self) -> None:
        self._released.set()


@dataclass
class _SourceBorrow:
    active: int = 0
    retired: SourceRelease | None = None


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
        self._pending_job: object | None = None
        self._pending_enriched = False
        # Observed values belong only to this unfinished job, not the provider cache.
        self._pending_backfill_values: dict[str, Any] = {}
        self._investor_cache_lock = Lock()
        self._investor_cache: dict[str, tuple[float, dict[str, object]]] = {}
        self._source_borrow = _SourceBorrow()

    def enqueue(self, snapshot: TradeEntrySnapshot) -> None:
        self._queue.put(snapshot)

    def enqueue_program_backfill(self, trade_date: date, codes: tuple[str, ...]) -> None:
        self._queue.put(("program_backfill", trade_date, codes))

    def enqueue_investor_backfill(self, trade_date: date, codes: tuple[str, ...]) -> None:
        self._queue.put(("investor_backfill", trade_date, codes))

    def set_investor_loader(self, loader: Callable[[str, datetime], dict[str, object]] | None) -> None:
        with self._investor_cache_lock:
            self._investor_loader = loader
            # A detached dict is also a generation token, even for the same loader.
            self._investor_cache = {}

    def set_program_loader(self, loader: Callable[[str, date], tuple[dict[str, object], ...]] | None) -> None:
        with self._investor_cache_lock:
            self._program_loader = loader

    @contextmanager
    def _borrow_source(self, *, program: bool = False) -> Iterator[tuple[Any, dict]]:
        # Capture and admission share the exact lock used by replace/rollback.
        with self._investor_cache_lock:
            loader = self._program_loader if program else self._investor_loader
            cache = self._investor_cache
            source = self._source_borrow
            if loader is not None:
                source.active += 1
        try:
            yield loader, cache
        finally:
            if loader is not None:
                with self._investor_cache_lock:
                    source.active -= 1
                    if source.active == 0 and source.retired is not None:
                        source.retired._release()

    @staticmethod
    def _retire_source(source: _SourceBorrow, release: SourceRelease) -> None:
        # Called only while the existing source/cache lock is held. Setting an
        # Event performs no user callback, close, provider query or report I/O.
        source.retired = release
        if source.active == 0:
            release._release()

    @contextmanager
    def replace_sources(
        self, *, investor_loader: Callable[[str, datetime], dict[str, object]] | None,
        program_loader: Callable[[str, date], tuple[dict[str, object], ...]] | None,
    ) -> Iterator[SourceRelease]:
        """Apply one source generation, restoring its values if runtime apply fails.

        The detached cache is the old generation. Rollback copies its values into
        a fresh generation so a late result from either source cannot republish.
        Pending jobs and their already observed enrichment stay with the writer.
        Success retires the previous source; rollback retires the candidate.
        The yielded receipt observes its actual calls without waiting here.
        """
        release = SourceRelease()
        with self._investor_cache_lock:
            previous_investor, previous_program = self._investor_loader, self._program_loader
            previous_cache = self._investor_cache
            previous_source = self._source_borrow
            candidate_source = _SourceBorrow()
            self._investor_loader = investor_loader
            self._program_loader = program_loader
            self._investor_cache = {}
            self._source_borrow = candidate_source
        try:
            yield release
        except Exception:
            with self._investor_cache_lock:
                self._investor_loader = previous_investor
                self._program_loader = previous_program
                self._investor_cache = dict(previous_cache)
                # Restore the old borrow cohort, but keep the cache fresh. A
                # pre-rollback old call must also delay any later retirement.
                self._source_borrow = previous_source
                self._retire_source(candidate_source, release)
            raise
        else:
            with self._investor_cache_lock:
                self._retire_source(previous_source, release)

    def has_pending_work(self) -> bool:
        """The owner checks this after the thread stops, before accepting close."""
        return self._pending_job is not None or not self._queue.empty()

    def run(self) -> None:
        try:
            repository = JournalRepository(self._database_path)
            news_repository = StockNewsRepository(self._news_database_path) if self._news_database_path else None
        except Exception as error:
            self.failed.emit(f"진입 스냅샷 DB 준비 실패: {error}")
            return
        while not self.isInterruptionRequested() or self.has_pending_work():
            if self._pending_job is None:
                try:
                    self._pending_job = self._queue.get(timeout=0.1)
                except Empty:
                    continue
            snapshot = self._pending_job
            try:
                if isinstance(snapshot, tuple) and len(snapshot) == 3 and snapshot[0] == "program_backfill":
                    self._backfill_program(repository, snapshot[1], snapshot[2])
                elif isinstance(snapshot, tuple) and len(snapshot) == 3 and snapshot[0] == "investor_backfill":
                    self._backfill_investor(repository, snapshot[1], snapshot[2])
                elif isinstance(snapshot, TradeEntrySnapshot):
                    if not self._pending_enriched:
                        for attempt in range(5):
                            try:
                                repository.save_entry_snapshot(snapshot, account_scope=snapshot.origin_scope)
                                break
                            except Exception:
                                if attempt == 4:
                                    raise
                                time.sleep(0.1 * (attempt + 1))
                        news = news_at_execution(news_repository, snapshot)
                        investor = self._investor_at_execution(snapshot)
                        program = dict((snapshot.investor_flow or {}).get("program_trade", {}))
                        if program:
                            investor = {**investor, "program_trade": program}
                        if not news and not investor:
                            self._pending_job = None
                            continue
                        state = "realtime_enriched" if investor.get("available") else "realtime_partial"
                        snapshot = replace(snapshot, news=news, investor_flow=investor, capture_state=state)
                        # Keep the already observed payload; retry only its failed write.
                        self._pending_job = snapshot
                        self._pending_enriched = True
                    repository.save_entry_snapshot(
                        snapshot, account_scope=snapshot.origin_scope,
                    )
            except Exception as error:
                self.failed.emit(f"진입 스냅샷 저장 작업 실패: {error}")
                return
            self._pending_job = None
            self._pending_enriched = False
            self._pending_backfill_values.clear()

    def _backfill_program(self, repository: JournalRepository, trade_date: date, codes: tuple[str, ...]) -> None:
        if self._program_loader is None and not self._pending_backfill_values:
            return
        candidates = repository.program_backfill_candidates(trade_date, codes)
        by_code: dict[str, list[TradeEntrySnapshot]] = {}
        for snapshot in candidates:
            by_code.setdefault(snapshot.stock_code, []).append(snapshot)
        for code, snapshots in by_code.items():
            try:
                if code not in self._pending_backfill_values:
                    with self._borrow_source(program=True) as (loader, _cache):
                        if loader is None:
                            continue
                        self._pending_backfill_values[code] = loader(code, trade_date)
                rows = self._pending_backfill_values[code]
            except Exception as error:
                self.failed.emit(f"{code} 장 마감 프로그램매매 보완 실패: {error}")
                continue
            for snapshot in snapshots:
                target = snapshot.executed_at.strftime("%H%M%S")
                eligible = [row for row in rows if str(row.get("trade_time", "")) <= target]
                if not eligible:
                    continue
                nearest = max(eligible, key=lambda row: (
                    str(row.get("trade_time", "")), row.get("source") == "kiwoom_realtime_0w",
                ))
                repository.save_program_trade_backfill(snapshot.execution_key, {**nearest, "backfilled": True})

    def _backfill_investor(self, repository: JournalRepository, trade_date: date, codes: tuple[str, ...]) -> None:
        if self._investor_loader is None and not self._pending_backfill_values:
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
                if code not in self._pending_backfill_values:
                    with self._borrow_source() as (loader, _cache):
                        if loader is None:
                            continue
                        self._pending_backfill_values[code] = loader(code, observed_at)
                value = self._pending_backfill_values[code]
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
        with self._borrow_source() as (loader, cache):
            if loader is None:
                return {}
            with self._investor_cache_lock:
                cached = cache.get(snapshot.stock_code)
                if cached and time.monotonic() - cached[0] < 60:
                    return {**cached[1], "cached": True}
            try:
                value = loader(snapshot.stock_code, snapshot.executed_at)
            except Exception as error:
                return {"available": False, "error": str(error)}
            with self._investor_cache_lock:
                if cache is self._investor_cache and loader is self._investor_loader:
                    cache[snapshot.stock_code] = (time.monotonic(), value)
            return value
