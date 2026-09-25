"""Date-scoped Naver Stock flash and world news archive.

The site endpoints are not the Naver Search API. Keep their raw responses and
their own publication timestamps; never infer a publication time from the day
being collected.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from html import unescape
from pathlib import Path
from time import sleep
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from kiwoom_monitor.infrastructure.system_ssl import system_ssl_context


KST = ZoneInfo("Asia/Seoul")
SOURCES = ("flash", "world")
PAGE_SIZE = {"flash": 15, "world": 100}
ENDPOINT = {
    "flash": "https://stock.naver.com/api/domestic/news/list",
    "world": "https://stock.naver.com/api/foreign/news/worldNews",
}


@dataclass(frozen=True)
class MarketNewsArticle:
    source: str
    office_id: str
    article_id: str
    title: str
    summary: str
    publisher: str
    published_at: str
    url: str
    raw_json: str


@dataclass(frozen=True)
class MarketNewsPage:
    source: str
    target_date: str
    page: int
    endpoint: str
    observed_at: str
    raw_json: str
    articles: tuple[MarketNewsArticle, ...]
    invalid_count: int
    older_count: int


def page_url(source: str, target_date: str, page: int, *,
             endpoints: dict[str, str] | None = None) -> str:
    if source not in SOURCES:
        raise ValueError(f"unsupported Naver Stock news source: {source}")
    day = date.fromisoformat(target_date)
    if page < 1:
        raise ValueError("page must be positive")
    params: dict[str, str | int] = {
        "date": day.strftime("%Y%m%d"), "page": page, "pageSize": PAGE_SIZE[source],
    }
    if source == "flash":
        params = {"category": "FLASHNEWS", "page": page,
                  "pageSize": PAGE_SIZE[source], "date": day.strftime("%Y%m%d")}
    return (endpoints or ENDPOINT)[source] + "?" + urlencode(params)


def _publication_time(source: str, raw: dict[str, Any]) -> str:
    value = str(raw.get("datetime" if source == "flash" else "dt") or "").strip()
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S" if source == "flash" else "%Y%m%d%H%M%S")
    except ValueError:
        return ""
    return parsed.replace(tzinfo=KST).isoformat()


def parse_page(source: str, target_date: str, page: int, payload: Any,
               *, observed_at: str | None = None, endpoint: str | None = None) -> MarketNewsPage:
    page_url(source, target_date, page)
    rows = payload.get("articles") if source == "flash" and isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError(f"unexpected {source} news response shape")
    articles: list[MarketNewsArticle] = []
    invalid = 0
    older = 0
    for raw in rows:
        if not isinstance(raw, dict):
            invalid += 1
            continue
        office = str(raw.get("officeId" if source == "flash" else "oid") or "").strip()
        article = str(raw.get("articleId" if source == "flash" else "aid") or "").strip()
        published = _publication_time(source, raw)
        if not office or not article or not published:
            invalid += 1
            continue
        if published[:10] < target_date:
            older += 1
            continue
        if published[:10] > target_date:
            invalid += 1
            continue
        url = (f"https://n.news.naver.com/article/{office}/{article}" if source == "flash"
               else f"https://stock.naver.com/news/worldnews/{article}")
        articles.append(MarketNewsArticle(
            source, office, article, unescape(str(raw.get("title" if source == "flash" else "tit") or "")),
            unescape(str(raw.get("subcontent") or "")),
            str(raw.get("officeHname" if source == "flash" else "ohnm") or ""),
            published, url, json.dumps(raw, ensure_ascii=False, sort_keys=True),
        ))
    return MarketNewsPage(
        source, target_date, page, endpoint or page_url(source, target_date, page),
        observed_at or datetime.now(UTC).isoformat(),
        json.dumps(payload, ensure_ascii=False, sort_keys=True), tuple(articles), invalid, older,
    )


def fetch_page(source: str, target_date: str, page: int, *, timeout: float = 20,
               opener: Callable[..., Any] = urlopen,
               endpoints: dict[str, str] | None = None) -> MarketNewsPage:
    endpoint = page_url(source, target_date, page, endpoints=endpoints)
    request = Request(endpoint, headers={
        "Accept": "application/json", "Referer": "https://stock.naver.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) KiwoomMonitor/2.1",
    })
    with opener(request, timeout=timeout, context=system_ssl_context()) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return parse_page(source, target_date, page, payload, endpoint=endpoint)


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
                on_page: Callable[[MarketNewsPage], None] | None = None) -> dict[str, Any]:
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
                on_page(prior)
    for _ in range(max_pages):
        try:
            for attempt in range(5):
                try:
                    page = fetcher(source, target_date, page_number)
                    break
                except HTTPError as error:
                    if error.code not in {429, 500, 502, 503, 504} or attempt == 4:
                        raise
                    sleep(min(30.0, 2 ** attempt))
            if page.invalid_count:
                raise ValueError(f"page {page_number} has {page.invalid_count} invalid or other-date rows")
            if page.older_count and page_number == 1 and not page.articles:
                raise ValueError("first page contains only earlier-date rows; target coverage unknown")
            last = (page.articles[-1].office_id, page.articles[-1].article_id) if page.articles else None
            if last is not None and last == seen_last:
                raise ValueError(f"page {page_number} repeats the previous page")
            seen_last = last
            boundary_complete = bool(page.older_count)
            complete = boundary_complete or len(page.articles) < PAGE_SIZE[source]
            store_page(path, page, complete=complete, boundary_complete=boundary_complete)
            if on_page is not None:
                on_page(page)
            if complete:
                return day_state(path, source, target_date) or {}
            page_number += 1
            if delay_seconds:
                sleep(delay_seconds)
        except Exception as error:
            record_error(path, source, target_date, page_number, error)
            raise
    return day_state(path, source, target_date) or {}
