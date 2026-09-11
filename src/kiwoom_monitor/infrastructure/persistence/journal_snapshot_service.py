"""매매일지 화면에 필요한 체결 스냅샷 조회와 뉴스 보완을 조립한다."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol

from kiwoom_monitor.domain.snapshot_provenance import mark_news_backfilled
from kiwoom_monitor.infrastructure.persistence.journal_snapshot_repository import TradeEntrySnapshot
from kiwoom_monitor.infrastructure.persistence.stock_news_repository import StockNewsRepository


class JournalSnapshotStore(Protocol):
    def load_entry_snapshots(
        self, code: str, start: datetime, end: datetime,
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
    if repository is None:
        return ()
    try:
        items = repository.load(snapshot.stock_code, limit=50)
    except Exception:
        return ()
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
) -> tuple[TradeEntrySnapshot, ...]:
    """회차 주변 스냅샷을 읽고 뉴스가 비어 있을 때만 장후 자료를 연결한다."""
    try:
        snapshots = repository.load_entry_snapshots(
            stock_code,
            started_at - timedelta(minutes=1),
            ended_at + timedelta(minutes=1),
        )
    except sqlite3.Error:
        return ()
    if news_repository is None:
        return snapshots

    enriched: list[TradeEntrySnapshot] = []
    for snapshot in snapshots:
        if snapshot.news:
            enriched.append(snapshot)
            continue
        news = news_loader(news_repository, snapshot)
        if news:
            repository.save_snapshot_news_backfill(snapshot.execution_key, news)
            snapshot = replace(snapshot, news=mark_news_backfilled(news))
        enriched.append(snapshot)
    return tuple(enriched)
