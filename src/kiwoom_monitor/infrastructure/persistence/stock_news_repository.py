from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from kiwoom_monitor.application.news_analysis import NewsAssessment
from kiwoom_monitor.infrastructure.naver_news import StockNewsItem
from kiwoom_monitor.infrastructure.persistence.news_ai_repository import news_identity


class StockNewsRepository:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        connection = sqlite3.connect(self._database_path)
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS stock_news (
                    stock_code TEXT NOT NULL,
                    identity TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    link TEXT NOT NULL DEFAULT '',
                    original_link TEXT NOT NULL DEFAULT '',
                    published_at TEXT,
                    relevant INTEGER NOT NULL DEFAULT 0,
                    category TEXT NOT NULL DEFAULT '',
                    outlook TEXT NOT NULL DEFAULT '',
                    reason TEXT NOT NULL DEFAULT '',
                    relevance_score INTEGER NOT NULL DEFAULT 0,
                    outlook_score INTEGER NOT NULL DEFAULT 0,
                    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(stock_code, identity)
                );
                CREATE TABLE IF NOT EXISTS stock_news_sync (
                    stock_code TEXT PRIMARY KEY,
                    checked_at TEXT NOT NULL,
                    naver_checked_at TEXT
                );
                """
            )
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(stock_news_sync)")}
            if "naver_checked_at" not in columns:
                connection.execute("ALTER TABLE stock_news_sync ADD COLUMN naver_checked_at TEXT")
            connection.commit()
        finally:
            connection.close()

    def load(self, stock_code: str, *, limit: int = 200) -> tuple[StockNewsItem, ...]:
        connection = sqlite3.connect(self._database_path)
        try:
            rows = connection.execute(
                "SELECT title, description, link, original_link, published_at, relevant, category, outlook, reason, relevance_score, outlook_score "
                "FROM stock_news WHERE stock_code=? ORDER BY COALESCE(published_at, first_seen_at) DESC LIMIT ?",
                (stock_code, max(1, limit)),
            ).fetchall()
        finally:
            connection.close()
        return tuple(
            StockNewsItem(
                title=str(row[0]),
                description=str(row[1]),
                link=str(row[2]),
                original_link=str(row[3]),
                published_at=_parse_datetime(row[4]),
                assessment=NewsAssessment(
                    bool(row[5]), str(row[6]), str(row[7]), str(row[8]), int(row[9]), int(row[10])
                ),
            )
            for row in rows
        )

    def recently_checked(self, stock_code: str, max_age_seconds: float) -> bool:
        connection = sqlite3.connect(self._database_path)
        try:
            row = connection.execute(
                "SELECT checked_at FROM stock_news_sync WHERE stock_code=?", (stock_code,)
            ).fetchone()
        finally:
            connection.close()
        checked_at = _parse_datetime(row[0]) if row else None
        return bool(checked_at and datetime.now(UTC) - checked_at.astimezone(UTC) < timedelta(seconds=max_age_seconds))

    def last_naver_checked_at(self, stock_code: str) -> datetime | None:
        connection = sqlite3.connect(self._database_path)
        try:
            row = connection.execute(
                "SELECT naver_checked_at FROM stock_news_sync WHERE stock_code=?", (stock_code,)
            ).fetchone()
        finally:
            connection.close()
        return _parse_datetime(row[0]) if row else None

    def upsert(
        self, stock_code: str, items: tuple[StockNewsItem, ...], checked_at: datetime | None = None,
        *, naver_checked_at: datetime | None = None,
    ) -> int:
        checked_at = checked_at or datetime.now(UTC)
        connection = sqlite3.connect(self._database_path)
        try:
            existing = {
                str(row[0]) for row in connection.execute(
                    "SELECT identity FROM stock_news WHERE stock_code=?", (stock_code,)
                )
            }
            rows = tuple(_row(stock_code, item) for item in items)
            if rows:
                connection.executemany(
                    "INSERT INTO stock_news(stock_code, identity, title, description, link, original_link, published_at, relevant, category, outlook, reason, relevance_score, outlook_score) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(stock_code, identity) DO UPDATE SET "
                    "title=excluded.title, description=excluded.description, link=excluded.link, original_link=excluded.original_link, "
                    "published_at=excluded.published_at, relevant=excluded.relevant, category=excluded.category, outlook=excluded.outlook, "
                    "reason=excluded.reason, relevance_score=excluded.relevance_score, outlook_score=excluded.outlook_score",
                    rows,
                )
            connection.execute(
                "INSERT INTO stock_news_sync(stock_code, checked_at, naver_checked_at) VALUES (?, ?, ?) "
                "ON CONFLICT(stock_code) DO UPDATE SET checked_at=excluded.checked_at, "
                "naver_checked_at=COALESCE(excluded.naver_checked_at, stock_news_sync.naver_checked_at)",
                (stock_code, checked_at.isoformat(), naver_checked_at.isoformat() if naver_checked_at else None),
            )
            # 종목별 최신 200건만 보관해 장기간 사용해도 DB가 불필요하게 커지지 않는다.
            connection.execute(
                "DELETE FROM stock_news WHERE stock_code=? AND identity NOT IN ("
                "SELECT identity FROM stock_news WHERE stock_code=? ORDER BY COALESCE(published_at, first_seen_at) DESC LIMIT 200)",
                (stock_code, stock_code),
            )
            connection.commit()
        finally:
            connection.close()
        return sum(1 for item in items if news_identity(item) not in existing)


def _identity(item: StockNewsItem) -> str:
    return news_identity(item)


def _row(stock_code: str, item: StockNewsItem) -> tuple[object, ...]:
    assessment = item.assessment
    return (
        stock_code,
        _identity(item),
        item.title,
        item.description,
        item.link,
        item.original_link,
        item.published_at.isoformat() if item.published_at else None,
        int(assessment.relevant),
        assessment.category,
        assessment.outlook,
        assessment.reason,
        assessment.relevance_score,
        assessment.outlook_score,
    )


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
