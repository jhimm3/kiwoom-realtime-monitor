"""매매일지 화면에 필요한 체결 스냅샷 조회와 뉴스 보완을 조립한다."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol

from kiwoom_monitor.domain.order_contract import AccountScope
from kiwoom_monitor.domain.snapshot_provenance import mark_news_backfilled
from kiwoom_monitor.infrastructure.naver_news import StockNewsItem
from kiwoom_monitor.infrastructure.persistence.journal_snapshot_repository import TradeEntrySnapshot
from kiwoom_monitor.infrastructure.persistence.stock_news_repository import StockNewsRepository


class JournalSnapshotStore(Protocol):
    def load_entry_snapshots(
        self, code: str, start: datetime, end: datetime,
        account_scope: AccountScope | None = None,
    ) -> tuple[TradeEntrySnapshot, ...]: ...

    def save_snapshot_news_backfill(
        self, execution_key: str, news: tuple[dict[str, object], ...],
    ) -> None: ...


NewsAtExecution = Callable[
    [StockNewsRepository | None, TradeEntrySnapshot],
    tuple[dict[str, object], ...],
]


KST = timezone(timedelta(hours=9))


def news_at_execution(
    repository: StockNewsRepository | None,
    snapshot: TradeEntrySnapshot,
) -> tuple[dict[str, object], ...]:
    """체결 직전 48시간부터 직후 5분까지의 관련 뉴스만 최대 10건 고른다."""
    return _news_items_at_execution(_load_news_items(repository, snapshot.stock_code), snapshot)


def _load_news_items(repository: StockNewsRepository | None, stock_code: str) -> tuple[StockNewsItem, ...]:
    if repository is None:
        return ()
    try:
        return repository.load(stock_code, limit=50)
    except Exception:
        return ()


def _news_items_at_execution(
    items: tuple[StockNewsItem, ...], snapshot: TradeEntrySnapshot,
) -> tuple[dict[str, object], ...]:
    executed = (
        snapshot.executed_at.replace(tzinfo=KST)
        if snapshot.executed_at.tzinfo is None
        else snapshot.executed_at.astimezone(KST)
    )
    lower = executed - timedelta(hours=48)
    result: list[dict[str, object]] = []
    for item in items:
        published = item.published_at
        if published is None:
            continue
        published = published.replace(tzinfo=KST) if published.tzinfo is None else published.astimezone(KST)
        if not lower <= published <= executed + timedelta(minutes=5):
            continue
        result.append({
            "title": item.title,
            "link": item.original_link or item.link,
            "published_at": published.isoformat(),
            "relevant": item.assessment.relevant,
            "category": item.assessment.category,
            "outlook": item.assessment.outlook,
            "reason": item.assessment.reason,
        })
        if len(result) >= 10:
            break
    return tuple(result)


def load_episode_entry_snapshots(
    repository: JournalSnapshotStore,
    news_repository: StockNewsRepository | None,
    stock_code: str,
    started_at: datetime,
    ended_at: datetime,
    *,
    news_loader: NewsAtExecution = news_at_execution,
    account_scope: AccountScope | None = None,
    on_backfill_error: Callable[[str, Exception], None] | None = None,
) -> tuple[TradeEntrySnapshot, ...]:
    """회차 주변 스냅샷을 읽고 뉴스가 비어 있을 때만 장후 자료를 연결한다."""
    try:
        if account_scope is None:
            snapshots = repository.load_entry_snapshots(
                stock_code, started_at - timedelta(minutes=1), ended_at + timedelta(minutes=1),
            )
        else:
            snapshots = repository.load_entry_snapshots(
                stock_code, started_at - timedelta(minutes=1), ended_at + timedelta(minutes=1),
                account_scope,
            )
    except sqlite3.Error:
        return ()
    if news_repository is None:
        return snapshots

    enriched: list[TradeEntrySnapshot] = []
    news_items_by_code: dict[str, tuple[StockNewsItem, ...]] = {}
    for index, snapshot in enumerate(snapshots):
        if snapshot.news:
            enriched.append(snapshot)
            continue
        if news_loader is news_at_execution:
            if snapshot.stock_code not in news_items_by_code:
                news_items_by_code[snapshot.stock_code] = _load_news_items(news_repository, snapshot.stock_code)
            news = _news_items_at_execution(news_items_by_code[snapshot.stock_code], snapshot)
        else:
            news = news_loader(news_repository, snapshot)
        if news:
            try:
                repository.save_snapshot_news_backfill(snapshot.execution_key, news)
            except (OSError, sqlite3.Error) as error:
                if on_backfill_error is None:
                    raise
                on_backfill_error(snapshot.execution_key, error)
                return (*enriched, *snapshots[index:])
            snapshot = replace(snapshot, news=mark_news_backfilled(news))
        enriched.append(snapshot)
    return tuple(enriched)
