from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from kiwoom_monitor.application.news_analysis import NewsAssessment
from kiwoom_monitor.infrastructure.naver_news import StockNewsItem
from kiwoom_monitor.infrastructure.persistence.news_ai_repository import news_identity
from kiwoom_monitor.infrastructure.persistence.news_schema import initialize_news_schema
from kiwoom_monitor.infrastructure.persistence.sqlite_connections import (
    sqlite_read_connection,
    sqlite_transaction,
)


class StockNewsRepository:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        initialize_news_schema(self._database_path)

    def load(self, stock_code: str, *, limit: int = 200) -> tuple[StockNewsItem, ...]:
        with sqlite_read_connection(self._database_path) as connection:
            rows = connection.execute(
                "SELECT title, description, link, original_link, published_at, relevant, category, outlook, reason, relevance_score, outlook_score "
                "FROM stock_news WHERE stock_code=? ORDER BY COALESCE(published_at, first_seen_at) DESC LIMIT ?",
                (stock_code, max(1, limit)),
            ).fetchall()
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
        with sqlite_read_connection(self._database_path) as connection:
            row = connection.execute(
                "SELECT checked_at FROM stock_news_sync WHERE stock_code=?", (stock_code,)
            ).fetchone()
        checked_at = _parse_datetime(row[0]) if row else None
        return bool(checked_at and datetime.now(UTC) - checked_at.astimezone(UTC) < timedelta(seconds=max_age_seconds))

    def last_naver_checked_at(self, stock_code: str) -> datetime | None:
        with sqlite_read_connection(self._database_path) as connection:
            row = connection.execute(
                "SELECT naver_checked_at FROM stock_news_sync WHERE stock_code=?", (stock_code,)
            ).fetchone()
        return _parse_datetime(row[0]) if row else None

    def upsert(
        self, stock_code: str, items: tuple[StockNewsItem, ...], checked_at: datetime | None = None,
        *, naver_checked_at: datetime | None = None,
    ) -> int:
        checked_at = checked_at or datetime.now(UTC)
        with sqlite_transaction(self._database_path) as connection:
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
            # 매매일지에 연결한 기사는 최신 200건 밖으로 밀려나도 계속 보존한다.
            connection.execute(
                "DELETE FROM stock_news WHERE stock_code=? AND identity NOT IN ("
                "SELECT identity FROM stock_news WHERE stock_code=? ORDER BY COALESCE(published_at, first_seen_at) DESC LIMIT 200) "
                "AND NOT EXISTS (SELECT 1 FROM journal_news_links l WHERE l.stock_code=stock_news.stock_code AND l.identity=stock_news.identity)",
                (stock_code, stock_code),
            )
        return sum(1 for item in items if news_identity(item) not in existing)

    def set_journal_link(self, group_id: str, stock_code: str, identity: str, linked: bool) -> None:
        with sqlite_transaction(self._database_path) as connection:
            if linked:
                connection.execute(
                    "INSERT OR IGNORE INTO journal_news_links(group_id,stock_code,identity) VALUES (?,?,?)",
                    (group_id, stock_code, identity),
                )
            else:
                connection.execute(
                    "DELETE FROM journal_news_links WHERE group_id=? AND stock_code=? AND identity=?",
                    (group_id, stock_code, identity),
                )

    def journal_linked_identities(self, group_id: str, stock_code: str) -> set[str]:
        with sqlite_read_connection(self._database_path) as connection:
            return {str(row[0]) for row in connection.execute(
                "SELECT identity FROM journal_news_links WHERE group_id=? AND stock_code=?",
                (group_id, stock_code),
            )}

    def load_journal_linked(self, group_id: str, stock_code: str) -> tuple[StockNewsItem, ...]:
        with sqlite_read_connection(self._database_path) as connection:
            rows = connection.execute(
                "SELECT n.title,n.description,n.link,n.original_link,n.published_at,n.relevant,n.category,n.outlook,n.reason,n.relevance_score,n.outlook_score "
                "FROM journal_news_links l JOIN stock_news n ON n.stock_code=l.stock_code AND n.identity=l.identity "
                "WHERE l.group_id=? AND l.stock_code=? ORDER BY COALESCE(n.published_at,n.first_seen_at) DESC",
                (group_id, stock_code),
            ).fetchall()
        return tuple(StockNewsItem(
            str(row[0]), str(row[1]), str(row[2]), str(row[3]), _parse_datetime(row[4]),
            NewsAssessment(bool(row[5]), str(row[6]), str(row[7]), str(row[8]), int(row[9]), int(row[10])),
        ) for row in rows)


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
