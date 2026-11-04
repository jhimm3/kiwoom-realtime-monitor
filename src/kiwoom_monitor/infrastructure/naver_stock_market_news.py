"""Date-scoped Naver Stock flash and world news archive.

The site endpoints are not the Naver Search API. Keep their raw responses and
their own publication timestamps; never infer a publication time from the day
being collected.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from time import sleep
from typing import Any, Callable
from urllib.error import HTTPError



from kiwoom_monitor.infrastructure.naver_stock_market_news_source import (
    ENDPOINT, KST, PAGE_SIZE, SOURCES, MarketNewsArticle, MarketNewsPage,
    _publication_time, fetch_page, page_url, parse_page,
)


def initialize_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=60)) as connection:
        with connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS market_news_days (
                    source TEXT NOT NULL, target_date TEXT NOT NULL, state TEXT NOT NULL,
                    next_page INTEGER NOT NULL DEFAULT 1, pages INTEGER NOT NULL DEFAULT 0,
                    articles INTEGER NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL, PRIMARY KEY(source,target_date));
                CREATE TABLE IF NOT EXISTS market_news_pages (
                    source TEXT NOT NULL, target_date TEXT NOT NULL, page INTEGER NOT NULL,
                    observed_at TEXT NOT NULL, endpoint TEXT NOT NULL, response_sha256 TEXT NOT NULL,
                    payload_json TEXT NOT NULL, article_count INTEGER NOT NULL,
                    invalid_count INTEGER NOT NULL, PRIMARY KEY(source,target_date,page));
                CREATE TABLE IF NOT EXISTS market_news_articles (
                    source TEXT NOT NULL, office_id TEXT NOT NULL, article_id TEXT NOT NULL,
                    published_at TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL,
                    publisher TEXT NOT NULL, article_url TEXT NOT NULL, raw_json TEXT NOT NULL,
                    first_observed_at TEXT NOT NULL, last_observed_at TEXT NOT NULL,
                    PRIMARY KEY(source,office_id,article_id));
                CREATE INDEX IF NOT EXISTS idx_market_news_articles_date
                    ON market_news_articles(source,published_at);
                CREATE INDEX IF NOT EXISTS idx_market_news_articles_url
                    ON market_news_articles(article_url);
            """)


def day_state(path: Path, source: str, target_date: str) -> dict[str, Any] | None:
    with closing(sqlite3.connect(path, timeout=60)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM market_news_days WHERE source=? AND target_date=?",
                                 (source, target_date)).fetchone()
        return dict(row) if row else None


def store_page(path: Path, page: MarketNewsPage, *, complete: bool,
               boundary_complete: bool = False) -> None:
    digest = hashlib.sha256(page.raw_json.encode("utf-8")).hexdigest()
    now = datetime.now(UTC).isoformat()
    with closing(sqlite3.connect(path, timeout=60)) as connection:
        with connection:
            old = connection.execute(
                "SELECT response_sha256 FROM market_news_pages WHERE source=? AND target_date=? AND page=?",
                (page.source, page.target_date, page.page),
            ).fetchone()
            if old and old[0] != digest:
                raise ValueError("source page changed during resumable collection")
            connection.execute("""INSERT OR IGNORE INTO market_news_pages VALUES(?,?,?,?,?,?,?,?,?)""", (
                page.source, page.target_date, page.page, page.observed_at, page.endpoint,
                digest, page.raw_json, len(page.articles), page.invalid_count,
            ))
            for item in page.articles:
                connection.execute("""INSERT INTO market_news_articles VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(source,office_id,article_id) DO UPDATE SET
                    last_observed_at=excluded.last_observed_at""", (
                    item.source, item.office_id, item.article_id, item.published_at, item.title,
                    item.summary, item.publisher, item.url, item.raw_json,
                    page.observed_at, page.observed_at,
                ))
            connection.execute("""INSERT INTO market_news_days VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(source,target_date) DO UPDATE SET
                state=excluded.state,next_page=excluded.next_page,pages=excluded.pages,
                articles=excluded.articles,last_error='',updated_at=excluded.updated_at""", (
                    page.source, page.target_date,
                    ("complete_boundary" if boundary_complete else
                     "empty" if not page.articles else "complete") if complete else "running",
                    page.page + 1, page.page,
                    connection.execute(
                        "SELECT COUNT(*) FROM market_news_articles WHERE source=? AND published_at>=? AND published_at<?",
                        (page.source, page.target_date,
                         (date.fromisoformat(page.target_date) + timedelta(days=1)).isoformat()),
                    ).fetchone()[0], "", now,
                ))


def record_error(path: Path, source: str, target_date: str, page: int, error: Exception) -> None:
    now = datetime.now(UTC).isoformat()
    with closing(sqlite3.connect(path, timeout=60)) as connection:
        with connection:
            connection.execute("""INSERT INTO market_news_days VALUES(?,?,'error',?,0,0,?,?)
                ON CONFLICT(source,target_date) DO UPDATE SET
                state='error',last_error=excluded.last_error,updated_at=excluded.updated_at""",
                (source, target_date, page, f"{type(error).__name__}: {error}"[:1000], now))


def collect_day(path: Path, source: str, target_date: str, *, max_pages: int = 300,
                delay_seconds: float = 0.7,
                fetcher: Callable[..., MarketNewsPage] = fetch_page,
                on_page: Callable[[MarketNewsPage], None] | None = None,
                on_diagnostic: Callable[[str, dict[str, Any]], None] | None = None) -> dict[str, Any]:
    def record(event: str, **fields: Any) -> None:
        if on_diagnostic is not None:
            try:
                on_diagnostic(event, {"source": source, "target_date": target_date,
                                      "page": page_number, **fields})
            except Exception:
                pass  # Diagnostics must not change the collector's data path.

    initialize_database(path)
    current = day_state(path, source, target_date)
    if current and current["state"] in {"complete", "complete_boundary", "empty"}:
        return current
    page_number = int(current["next_page"]) if current else 1
    seen_last: tuple[str, str] | None = None
    if page_number > 1:
        with closing(sqlite3.connect(path, timeout=60)) as connection:
            previous = connection.execute(
                "SELECT payload_json FROM market_news_pages WHERE source=? AND target_date=? AND page=?",
                (source, target_date, page_number - 1),
            ).fetchone()
        if previous:
            prior = parse_page(source, target_date, page_number - 1, json.loads(previous[0]))
            if prior.articles:
                seen_last = (prior.articles[-1].office_id, prior.articles[-1].article_id)
            # A crash can occur after the raw page commit but before its BODY/RULE
            # callback. Replaying the last committed page closes that gap; the
            # prepared-results ledger skips articles already completed.
            if on_page is not None:
                callback_started = time.monotonic()
                on_page(prior)
                record("market_page_callback", replayed=True, articles=len(prior.articles),
                       elapsed_ms=round((time.monotonic() - callback_started) * 1000))
    for _ in range(max_pages):
        try:
            for attempt in range(5):
                request_started = time.monotonic()
                try:
                    page = fetcher(source, target_date, page_number)
                    record("market_page_request", attempt=attempt + 1, status="ok",
                           articles=len(page.articles),
                           elapsed_ms=round((time.monotonic() - request_started) * 1000))
                    break
                except HTTPError as error:
                    record("market_page_request", attempt=attempt + 1, status="http_error",
                           http_status=error.code, error=f"HTTP {error.code}",
                           elapsed_ms=round((time.monotonic() - request_started) * 1000))
                    if error.code not in {429, 500, 502, 503, 504} or attempt == 4:
                        raise
                    wait_seconds = min(30.0, 2 ** attempt)
                    record("market_page_retry_wait", attempt=attempt + 1,
                           http_status=error.code, wait_seconds=wait_seconds)
                    sleep(wait_seconds)
                except Exception as error:
                    record("market_page_request", attempt=attempt + 1, status="error",
                           error=f"{type(error).__name__}: {error}"[:400],
                           elapsed_ms=round((time.monotonic() - request_started) * 1000))
                    raise
            if page.invalid_count:
                raise ValueError(f"page {page_number} has {page.invalid_count} invalid or other-date rows")
            if page.older_count and page_number == 1 and not page.articles:
                raise ValueError("first page contains only earlier-date rows; target coverage unknown")
            last = (page.articles[-1].office_id, page.articles[-1].article_id) if page.articles else None
            boundary_complete = bool(page.older_count)
            # Naver may repeat the prior page's final same-day article as the
            # first item at the date boundary. Older rows prove coverage is done.
            if last is not None and last == seen_last and not boundary_complete:
                raise ValueError(f"page {page_number} repeats the previous page")
            seen_last = last
            complete = boundary_complete or len(page.articles) < PAGE_SIZE[source]
            store_started = time.monotonic()
            store_page(path, page, complete=complete, boundary_complete=boundary_complete)
            record("market_page_store", articles=len(page.articles),
                   elapsed_ms=round((time.monotonic() - store_started) * 1000))
            if on_page is not None:
                callback_started = time.monotonic()
                on_page(page)
                record("market_page_callback", replayed=False, articles=len(page.articles),
                       elapsed_ms=round((time.monotonic() - callback_started) * 1000))
            if complete:
                return day_state(path, source, target_date) or {}
            page_number += 1
            if delay_seconds:
                sleep(delay_seconds)
        except Exception as error:
            record("market_page_error", error=f"{type(error).__name__}: {error}"[:400])
            record_error(path, source, target_date, page_number, error)
            raise
    return day_state(path, source, target_date) or {}
