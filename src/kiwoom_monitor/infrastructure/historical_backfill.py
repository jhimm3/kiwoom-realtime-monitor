from __future__ import annotations

import ctypes
import difflib
import hashlib
import html
import json
import platform
import re
import sqlite3
import sys
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from kiwoom_monitor.infrastructure.system_ssl import system_ssl_context


NAVER_STOCK_NEWS_ENDPOINT = "https://stock.naver.com/api/domestic/detail/news"
NAVER_HISTORICAL_SEARCH_ENDPOINT = "https://s.search.naver.com/p/newssearch/3/api/tab/more"
NAVER_STOCK_NEWS_PROVIDER = "naver_stock"
NAVER_HISTORICAL_SEARCH_PROVIDER = "naver_historical_search"
DAISHIN_PROVIDER = "daishin_creon"


@dataclass(frozen=True)
class CandidateDatabaseOverview:
    first_date: str
    last_date: str
    candidate_days: int
    candidate_rows: int
    candidate_stocks: int
    daily_bar_rows: int
    minute_bar_rows: int


@dataclass(frozen=True)
class CandidateRow:
    date: str
    code: str
    name: str
    score: float
    reasons: str


@dataclass(frozen=True)
class NewsBackfillJob:
    code: str
    target_date: str
    query_text: str
    name_source: str
    name_source_ref: str
    attempts: int


@dataclass(frozen=True)
class NaverStockNewsItem:
    office_id: str
    article_id: str
    office_name: str
    published_at: str
    title: str
    summary: str
    article_url: str
    image_url: str
    cluster_index: int
    related_index: int


@dataclass(frozen=True)
class NaverStockNewsPage:
    code: str
    page: int
    page_size: int
    reported_total: int
    cluster_count: int
    items: tuple[NaverStockNewsItem, ...]
    observed_at: str
    raw_json: str


@dataclass(frozen=True)
class NaverHistoricalNewsItem:
    article_key: str
    office_id: str
    article_id: str
    office_name: str
    title: str
    summary: str
    article_url: str
    original_url: str
    portal_url: str
    position: int


@dataclass(frozen=True)
class NaverHistoricalNewsPage:
    code: str
    query: str
    target_date: str
    start: int
    items: tuple[NaverHistoricalNewsItem, ...]
    has_structured_news: bool
    observed_at: str
    raw_json: str


@dataclass(frozen=True)
class ArticleFetchAttempt:
    url_role: str
    requested_url: str
    final_url: str
    status: str
    http_status: int | None
    error: str
    published_at: str
    published_precision: str
    published_at_source: str
    published_at_raw: str


@dataclass(frozen=True)
class ArticlePublicationResult:
    provider: str
    office_id: str
    article_id: str
    status: str
    published_at: str
    published_precision: str
    published_at_source: str
    published_at_raw: str
    source_url: str
    final_url: str
    fetched_at: str
    attempts: tuple[ArticleFetchAttempt, ...] = ()


@dataclass(frozen=True)
class DaishinEnvironment:
    windows: bool
    python_bitness: int
    elevated: bool | None
    pywin32_available: bool
    registered_progids: tuple[str, ...]
    missing_progids: tuple[str, ...]
    connected: bool | None
    detail: str

    @property
    def ready(self) -> bool:
        return (
            self.windows
            and self.pywin32_available
            and not self.missing_progids
            and self.connected is True
        )


def open_candidate_database(path: Path) -> sqlite3.Connection:
    resolved = path.expanduser().resolve(strict=True)
    connection = sqlite3.connect(
        f"file:{resolved.as_posix()}?mode=ro&immutable=1", uri=True, timeout=1
    )
    connection.row_factory = sqlite3.Row
    return connection


def inspect_candidate_database(path: Path) -> CandidateDatabaseOverview:
    with closing(open_candidate_database(path)) as connection:
        first_date, last_date, day_count, row_count, stock_count = connection.execute(
            """
            SELECT MIN(dt), MAX(dt), COUNT(DISTINCT dt), COUNT(*), COUNT(DISTINCT code)
            FROM candidate_days
            """
        ).fetchone()
        daily_count = connection.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0]
        minute_count = connection.execute("SELECT COUNT(*) FROM minute_bars").fetchone()[0]
    return CandidateDatabaseOverview(
        str(first_date or ""), str(last_date or ""), int(day_count), int(row_count),
        int(stock_count), int(daily_count), int(minute_count),
    )


def latest_candidates(path: Path, limit: int = 5) -> tuple[CandidateRow, ...]:
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")
    with closing(open_candidate_database(path)) as connection:
        rows = connection.execute(
            """
            SELECT c.dt, c.code, COALESCE(s.name, ''), c.score, COALESCE(c.reasons, '')
            FROM candidate_days AS c
            LEFT JOIN stocks AS s ON s.code = c.code
            WHERE c.dt = (SELECT MAX(dt) FROM candidate_days)
            ORDER BY c.score DESC, c.code
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return tuple(
        CandidateRow(str(row[0]), str(row[1]), str(row[2]), float(row[3]), str(row[4]))
        for row in rows
    )


def seed_news_backfill_jobs(
    candidate_database: Path,
    output_database: Path,
    *,
    start_date: str = "",
    end_date: str = "",
    name_transition_days: int = 14,
) -> int:
    start = _iso_date(start_date) if start_date else "0000-01-01"
    end = _iso_date(end_date) if end_date else "9999-12-31"
    if start > end:
        raise ValueError("start_date must not be after end_date")
    if name_transition_days < 0 or name_transition_days > 365:
        raise ValueError("name_transition_days must be between 0 and 365")
    initialize_probe_database(output_database)
    with closing(open_candidate_database(candidate_database)) as source:
        has_aliases = source.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='stock_aliases'"
        ).fetchone() is not None
        if has_aliases:
            primary_rows = source.execute(
                """
                SELECT DISTINCT c.code, c.dt,
                    TRIM(COALESCE(NULLIF(a.stock_name, ''), s.name, '')),
                    CASE WHEN a.stock_name IS NULL THEN 'stocks.current'
                         ELSE COALESCE(NULLIF(a.source, ''), 'stock_aliases') END,
                    COALESCE(a.source_ref, '')
                FROM candidate_days AS c
                LEFT JOIN stocks AS s ON s.code = c.code
                LEFT JOIN stock_aliases AS a
                  ON a.stock_code = c.code
                 AND (a.valid_from IS NULL OR a.valid_from = '' OR a.valid_from <= c.dt)
                 AND (a.valid_to IS NULL OR a.valid_to = '' OR a.valid_to >= c.dt)
                WHERE c.dt BETWEEN ? AND ?
                  AND TRIM(COALESCE(NULLIF(a.stock_name, ''), s.name, '')) <> ''
                ORDER BY c.dt DESC, c.code
                """,
                (start, end),
            ).fetchall()
            transition_rows = source.execute(
                """
                SELECT DISTINCT c.code, c.dt, TRIM(a.stock_name),
                    'name_transition_window:' ||
                        COALESCE(NULLIF(a.source, ''), 'stock_aliases'),
                    COALESCE(a.source_ref, '')
                FROM candidate_days AS c
                JOIN stock_aliases AS a ON a.stock_code = c.code
                WHERE c.dt BETWEEN ? AND ?
                  AND TRIM(COALESCE(a.stock_name, '')) <> ''
                  AND (
                    (NULLIF(a.valid_from, '') IS NOT NULL AND
                     c.dt BETWEEN date(a.valid_from, ?)
                              AND date(a.valid_from, ?))
                    OR
                    (NULLIF(a.valid_to, '') IS NOT NULL AND
                     c.dt BETWEEN date(a.valid_to, ?)
                              AND date(a.valid_to, ?))
                  )
                ORDER BY c.dt DESC, c.code, a.stock_name
                """,
                (
                    start, end,
                    f"-{name_transition_days} days", f"+{name_transition_days} days",
                    f"-{name_transition_days} days", f"+{name_transition_days} days",
                ),
            ).fetchall()
            # Insert the date-valid name first. A transition-window duplicate then
            # leaves the stronger provenance on the existing primary-key row.
            rows = [*primary_rows, *transition_rows]
        else:
            rows = source.execute(
                """
                SELECT c.code, c.dt, TRIM(COALESCE(s.name, '')),
                       'stocks.current', ''
                FROM candidate_days AS c
                LEFT JOIN stocks AS s ON s.code = c.code
                WHERE c.dt BETWEEN ? AND ? AND TRIM(COALESCE(s.name, '')) <> ''
                ORDER BY c.dt DESC, c.code
                """,
                (start, end),
            ).fetchall()
    now = datetime.now(UTC).isoformat()
    inserted = 0
    with closing(sqlite3.connect(output_database)) as connection:
        with connection:
            for row in rows:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO news_backfill_jobs
                    (code, target_date, query_text, name_source, name_source_ref,
                     state, updated_at)
                    VALUES (?, ?, ?, ?, ?, 'pending', ?)
                    """,
                    (
                        str(row[0]), str(row[1]), str(row[2]), str(row[3]),
                        str(row[4]), now,
                    ),
                )
                inserted += max(0, cursor.rowcount)
    return inserted


def clear_news_backfill_jobs(output_database: Path) -> int:
    initialize_probe_database(output_database)
    with closing(sqlite3.connect(output_database)) as connection:
        with connection:
            cursor = connection.execute("DELETE FROM news_backfill_jobs")
            return max(0, cursor.rowcount)


def claim_news_backfill_job(output_database: Path) -> NewsBackfillJob | None:
    initialize_probe_database(output_database)
    claimed_at = datetime.now(UTC)
    now = claimed_at.isoformat()
    stale_before = (claimed_at - timedelta(hours=2)).isoformat()
    with closing(sqlite3.connect(output_database)) as connection:
        connection.row_factory = sqlite3.Row
        with connection:
            connection.execute(
                """
                UPDATE news_backfill_jobs
                SET state='failed', last_error='collector_stale_running_recovered', updated_at=?
                WHERE state='running' AND updated_at < ?
                """,
                (now, stale_before),
            )
            row = connection.execute(
                """
                SELECT code, target_date, query_text, name_source, name_source_ref, attempts
                FROM news_backfill_jobs
                WHERE state IN ('pending', 'failed') AND attempts < 3
                ORDER BY target_date DESC, code
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            updated = connection.execute(
                """
                UPDATE news_backfill_jobs
                SET state='running', attempts=attempts+1, last_error='', updated_at=?
                WHERE code=? AND target_date=? AND query_text=?
                  AND state IN ('pending', 'failed')
                """,
                (now, row["code"], row["target_date"], row["query_text"]),
            )
            if updated.rowcount != 1:
                return None
            return NewsBackfillJob(
                str(row["code"]), str(row["target_date"]), str(row["query_text"]),
                str(row["name_source"]), str(row["name_source_ref"]),
                int(row["attempts"]) + 1,
            )


def finish_news_backfill_job(
    output_database: Path,
    job: NewsBackfillJob,
    *,
    state: str,
    pages_observed: int = 0,
    items_observed: int = 0,
    usable_articles: int = 0,
    unreadable_articles: int = 0,
    missing_time_articles: int = 0,
    error: str = "",
) -> None:
    if state not in {"complete", "failed", "truncated"}:
        raise ValueError("news backfill job state must be complete, failed or truncated")
    with closing(sqlite3.connect(output_database)) as connection:
        with connection:
            connection.execute(
                """
                UPDATE news_backfill_jobs SET
                    state=?, pages_observed=?, items_observed=?, usable_articles=?,
                    unreadable_articles=?, missing_time_articles=?, last_error=?, updated_at=?
                WHERE code=? AND target_date=? AND query_text=?
                """,
                (
                    state, pages_observed, items_observed, usable_articles,
                    unreadable_articles, missing_time_articles, error,
                    datetime.now(UTC).isoformat(), job.code, job.target_date, job.query_text,
                ),
            )


def article_publication_is_resolved(
    output_database: Path,
    item: NaverHistoricalNewsItem,
) -> bool:
    initialize_probe_database(output_database)
    with closing(sqlite3.connect(output_database)) as connection:
        row = connection.execute(
            """
            SELECT article_fetch_status FROM news_articles
            WHERE provider=? AND office_id=? AND article_id=?
            """,
            (NAVER_HISTORICAL_SEARCH_PROVIDER, item.office_id, item.article_id),
        ).fetchone()
    return row is not None and str(row[0]) not in {"", "not_fetched"}


def article_publication_status(
    output_database: Path,
    item: NaverHistoricalNewsItem,
) -> str:
    initialize_probe_database(output_database)
    with closing(sqlite3.connect(output_database)) as connection:
        row = connection.execute(
            """
            SELECT article_fetch_status FROM news_articles
            WHERE provider=? AND office_id=? AND article_id=?
            """,
            (NAVER_HISTORICAL_SEARCH_PROVIDER, item.office_id, item.article_id),
        ).fetchone()
    return str(row[0]) if row is not None else "not_fetched"


def parse_naver_stock_news_page(
    code: str,
    page: int,
    page_size: int,
    payload: Mapping[str, Any],
    *,
    observed_at: datetime | None = None,
) -> NaverStockNewsPage:
    normalized_code = _stock_code(code)
    clusters = payload.get("clusters")
    if not isinstance(clusters, list):
        raise ValueError("Naver stock news response has no clusters list")
    observed = (observed_at or datetime.now(UTC)).astimezone(UTC).isoformat()
    parsed: list[NaverStockNewsItem] = []
    for cluster_index, cluster in enumerate(clusters):
        if not isinstance(cluster, Mapping):
            continue
        items = cluster.get("items")
        if not isinstance(items, list):
            continue
        for related_index, item in enumerate(items):
            if not isinstance(item, Mapping):
                continue
            office_id = str(item.get("officeId", "")).strip()
            article_id = str(item.get("articleId", "")).strip()
            published_at = _naver_datetime(str(item.get("datetime", "")))
            title = str(item.get("title", "")).strip()
            if not office_id or not article_id or not published_at or not title:
                continue
            parsed.append(NaverStockNewsItem(
                office_id=office_id,
                article_id=article_id,
                office_name=str(item.get("officeName", "")).strip(),
                published_at=published_at,
                title=title,
                summary=str(item.get("body", "")).strip(),
                article_url=f"https://n.news.naver.com/article/{office_id}/{article_id}",
                image_url=str(item.get("imageOriginLink", "")).strip(),
                cluster_index=cluster_index,
                related_index=related_index,
            ))
    try:
        total = int(payload.get("total", 0))
    except (TypeError, ValueError):
        total = 0
    raw_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return NaverStockNewsPage(
        code=normalized_code,
        page=page,
        page_size=page_size,
        reported_total=total,
        cluster_count=len(clusters),
        items=tuple(parsed),
        observed_at=observed,
        raw_json=raw_json,
    )


def fetch_naver_stock_news_page(
    code: str,
    page: int = 1,
    page_size: int = 20,
    *,
    timeout: float = 15,
    opener: Callable[..., Any] = urlopen,
) -> NaverStockNewsPage:
    normalized_code = _stock_code(code)
    if page < 1:
        raise ValueError("page must be positive")
    if page_size < 1 or page_size > 100:
        raise ValueError("page_size must be between 1 and 100")
    query = urlencode({"itemCode": normalized_code, "page": page, "pageSize": page_size})
    request = Request(
        f"{NAVER_STOCK_NEWS_ENDPOINT}?{query}",
        headers={
            "Accept": "application/json",
            "Referer": f"https://stock.naver.com/domestic/stock/{normalized_code}/news",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) KiwoomMonitor/2.1",
        },
    )
    with opener(request, timeout=timeout, context=system_ssl_context()) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("Naver stock news response is not an object")
    return parse_naver_stock_news_page(normalized_code, page, page_size, payload)


def parse_naver_historical_search_page(
    code: str,
    query: str,
    target_date: str,
    start: int,
    payload: Mapping[str, Any],
    *,
    observed_at: datetime | None = None,
) -> NaverHistoricalNewsPage:
    normalized_code = _stock_code(code)
    normalized_date = _iso_date(target_date)
    if start < 1:
        raise ValueError("start must be positive")
    collections = payload.get("collection")
    if collections is None:
        collections = []
    if not isinstance(collections, list):
        raise ValueError("Naver historical search response has invalid collection")
    results: dict[str, NaverHistoricalNewsItem] = {}
    structured = False
    position = 0
    for collection in collections:
        if not isinstance(collection, Mapping):
            continue
        script = str(collection.get("script", ""))
        if '"contentHref"' not in script:
            continue
        structured = True
        root = _bootstrap_payload(script)
        for node in _mapping_nodes(root):
            href = html.unescape(str(node.get("contentHref", ""))).strip()
            title = _plain_text(str(node.get("title", "")))
            if not href.startswith(("http://", "https://")) or len(title) < 5:
                continue
            portal_url = next(
                (
                    html.unescape(str(child.get("textHref", ""))).strip()
                    for child in _mapping_nodes(node)
                    if "n.news.naver.com/" in str(child.get("textHref", ""))
                ),
                href if "n.news.naver.com/" in href else "",
            )
            original_url = "" if "n.news.naver.com/" in href else href
            office_id, article_id = _naver_article_ids(portal_url or href)
            article_key = (
                f"NAVER:{office_id}:{article_id}"
                if office_id and article_id
                else "URL:" + hashlib.sha256(href.encode("utf-8")).hexdigest()
            )
            source_profile = node.get("sourceProfile")
            office_name = ""
            if isinstance(source_profile, Mapping):
                office_name = _plain_text(str(source_profile.get("title", "")))
            item = NaverHistoricalNewsItem(
                article_key=article_key,
                office_id=office_id or "url",
                article_id=article_id or hashlib.sha256(href.encode("utf-8")).hexdigest(),
                office_name=office_name,
                title=title,
                summary=_plain_text(str(node.get("content", ""))),
                article_url=portal_url or original_url,
                original_url=original_url,
                portal_url=portal_url,
                position=position,
            )
            position += 1
            old = results.get(article_key)
            if old is None or len(item.title) > len(old.title):
                results[article_key] = item
    if structured and not results:
        raise ValueError("Naver historical search structure was present but no articles parsed")
    observed = (observed_at or datetime.now(UTC)).astimezone(UTC).isoformat()
    raw_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return NaverHistoricalNewsPage(
        normalized_code, str(query).strip(), normalized_date, start, tuple(results.values()),
        structured, observed, raw_json,
    )


def _naver_historical_search_url(query: str, target_date: str, start: int) -> str:
    normalized_date = _iso_date(target_date)
    compact = normalized_date.replace("-", "")
    dotted = normalized_date.replace("-", ".")
    params = {
        "de": dotted,
        "ds": dotted,
        "field": "0",
        "pd": "3",
        "query": str(query).strip(),
        "sort": "1",
        "ssc": "tab.news.all",
        "start": start,
        "nso": f"so:dd,p:from{compact}to{compact},a:all",
    }
    return f"{NAVER_HISTORICAL_SEARCH_ENDPOINT}?{urlencode(params)}"


def fetch_naver_historical_search_page(
    code: str,
    query: str,
    target_date: str,
    start: int = 1,
    *,
    timeout: float = 20,
    opener: Callable[..., Any] = urlopen,
) -> NaverHistoricalNewsPage:
    normalized_date = _iso_date(target_date)
    request = Request(
        _naver_historical_search_url(query, normalized_date, start),
        headers={
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "ko-KR,ko;q=0.9",
            "Referer": "https://search.naver.com/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) KiwoomMonitor/2.1",
        },
    )
    with opener(request, timeout=timeout, context=system_ssl_context()) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("Naver historical search response is not an object")
    return parse_naver_historical_search_page(code, query, normalized_date, start, payload)


class _ArticleMetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.candidates: list[tuple[str, str]] = []
        self.titles: list[str] = []
        self.json_ld: list[str] = []
        self._script_parts: list[str] | None = None
        self._title_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): (value or "") for key, value in attrs}
        if tag.lower() == "meta":
            key = (
                values.get("property") or values.get("name") or values.get("itemprop") or ""
            ).lower()
            content = values.get("content", "").strip()
            if key in {"article:published_time", "og:article:published_time", "datepublished", "pubdate"} and content:
                self.candidates.append((f"meta:{key}", content))
            if key in {"og:title", "twitter:title"} and content:
                self.titles.append(content)
        if tag.lower() == "time" and values.get("datetime"):
            self.candidates.append(("time:datetime", values["datetime"].strip()))
        for attribute in ("data-date-time", "data-published-time", "data-published-at"):
            if values.get(attribute):
                self.candidates.append((f"attribute:{attribute}", values[attribute].strip()))
        if tag.lower() == "script" and "ld+json" in values.get("type", "").lower():
            self._script_parts = []
        if tag.lower() == "title":
            self._title_parts = []

    def handle_data(self, data: str) -> None:
        if self._script_parts is not None:
            self._script_parts.append(data)
        if self._title_parts is not None:
            self._title_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._script_parts is not None:
            self.json_ld.append("".join(self._script_parts))
            self._script_parts = None
        if tag.lower() == "title" and self._title_parts is not None:
            title = _plain_text("".join(self._title_parts))
            if title:
                self.titles.append(title)
            self._title_parts = None


def parse_article_publication_html(
    document: str,
    *,
    expected_title: str = "",
) -> tuple[str, str, str, str]:
    parser = _ArticleMetadataParser()
    parser.feed(document)
    if expected_title and parser.titles and not any(
        _titles_compatible(expected_title, title) for title in parser.titles
    ):
        return "", "", "", "title_mismatch"
    candidates: list[tuple[str, str]] = []
    for script in parser.json_ld:
        try:
            value = json.loads(script.strip())
        except (json.JSONDecodeError, TypeError):
            continue
        for node in _mapping_nodes(value):
            raw = str(node.get("datePublished", "")).strip()
            if raw:
                candidates.append(("json_ld:datePublished", raw))
    candidates.extend(parser.candidates)
    for source, raw in candidates:
        parsed = _publication_datetime(raw)
        if parsed is not None:
            value, precision = parsed
            return value, precision, source, raw
    return "", "", "", "time_not_found"


def fetch_article_publication(
    item: NaverHistoricalNewsItem,
    *,
    timeout: float = 15,
    opener: Callable[..., Any] = urlopen,
) -> ArticlePublicationResult:
    fetched_at = datetime.now(UTC).isoformat()
    urls = [
        (role, url) for role, url in (
            ("publisher_original", item.original_url), ("naver_archive", item.portal_url),
        ) if url
    ]
    attempts: list[ArticleFetchAttempt] = []
    last_status = "no_source_url"
    last_url = ""
    seen_urls: set[str] = set()
    for url_role, source_url in urls:
        if source_url in seen_urls:
            continue
        seen_urls.add(source_url)
        request = Request(source_url, headers={
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ko-KR,ko;q=0.9",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) KiwoomMonitor/2.1",
        })
        try:
            with opener(request, timeout=timeout, context=system_ssl_context()) as response:
                body = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
                document = body.decode(charset, errors="replace")
                final_url = str(response.geturl())
                http_status = getattr(response, "status", None)
        except Exception as error:
            code = getattr(error, "code", None)
            last_status = "blocked" if code in {401, 403, 429} else (
                "article_unavailable" if code in {404, 410} else "fetch_error"
            )
            last_url = source_url
            attempts.append(ArticleFetchAttempt(
                url_role, source_url, source_url, last_status,
                int(code) if isinstance(code, int) else None,
                f"{type(error).__name__}: {error}", "", "", "", "",
            ))
            continue
        lowered = document.lower()
        if any(marker in lowered for marker in ("captcha", "robots.txt", "비정상적인 접근")):
            last_status = "blocked"
            last_url = final_url
            attempts.append(ArticleFetchAttempt(
                url_role, source_url, final_url, last_status,
                int(http_status) if isinstance(http_status, int) else None,
                "blocked page marker", "", "", "", "",
            ))
            continue
        if any(marker in document for marker in ("삭제된 기사", "페이지를 찾을 수 없습니다", "존재하지 않는 기사")):
            last_status = "article_unavailable"
            last_url = final_url
            attempts.append(ArticleFetchAttempt(
                url_role, source_url, final_url, last_status,
                int(http_status) if isinstance(http_status, int) else None,
                "unavailable page marker", "", "", "", "",
            ))
            continue
        published_at, precision, source, raw_or_status = parse_article_publication_html(
            document, expected_title=item.title,
        )
        if published_at:
            attempts.append(ArticleFetchAttempt(
                url_role, source_url, final_url, "published_at_found",
                int(http_status) if isinstance(http_status, int) else None, "",
                published_at, precision, source, raw_or_status,
            ))
            return ArticlePublicationResult(
                NAVER_HISTORICAL_SEARCH_PROVIDER, item.office_id, item.article_id,
                "published_at_found", published_at, precision, source, raw_or_status,
                source_url, final_url, fetched_at, tuple(attempts),
            )
        last_status = raw_or_status
        last_url = final_url
        attempts.append(ArticleFetchAttempt(
            url_role, source_url, final_url, last_status,
            int(http_status) if isinstance(http_status, int) else None, "",
            "", "", "", "",
        ))
    return ArticlePublicationResult(
        NAVER_HISTORICAL_SEARCH_PROVIDER, item.office_id, item.article_id,
        last_status, "", "", "", "", urls[0][1] if urls else "", last_url, fetched_at,
        tuple(attempts),
    )


def initialize_probe_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        with connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS source_pages (
                    provider TEXT NOT NULL,
                    request_key TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    response_sha256 TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    item_count INTEGER NOT NULL,
                    reported_total INTEGER,
                    PRIMARY KEY (provider, request_key, observed_at)
                );
                CREATE TABLE IF NOT EXISTS news_articles (
                    provider TEXT NOT NULL,
                    office_id TEXT NOT NULL,
                    article_id TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    published_precision TEXT NOT NULL,
                    office_name TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    article_url TEXT NOT NULL,
                    original_url TEXT NOT NULL DEFAULT '',
                    portal_url TEXT NOT NULL DEFAULT '',
                    image_url TEXT NOT NULL,
                    published_at_source TEXT NOT NULL DEFAULT '',
                    published_at_raw TEXT NOT NULL DEFAULT '',
                    publication_source_url TEXT NOT NULL DEFAULT '',
                    article_fetch_status TEXT NOT NULL DEFAULT 'not_fetched',
                    article_fetched_at TEXT NOT NULL DEFAULT '',
                    training_eligible INTEGER NOT NULL DEFAULT 0,
                    training_exclusion_reason TEXT NOT NULL DEFAULT 'publication_time_unverified',
                    first_observed_at TEXT NOT NULL,
                    last_observed_at TEXT NOT NULL,
                    PRIMARY KEY (provider, office_id, article_id)
                );
                CREATE TABLE IF NOT EXISTS news_article_symbols (
                    provider TEXT NOT NULL,
                    office_id TEXT NOT NULL,
                    article_id TEXT NOT NULL,
                    code TEXT NOT NULL,
                    page INTEGER NOT NULL,
                    cluster_index INTEGER NOT NULL,
                    related_index INTEGER NOT NULL,
                    observed_at TEXT NOT NULL,
                    PRIMARY KEY (provider, office_id, article_id, code)
                );
                CREATE TABLE IF NOT EXISTS news_search_observations (
                    provider TEXT NOT NULL,
                    office_id TEXT NOT NULL,
                    article_id TEXT NOT NULL,
                    code TEXT NOT NULL,
                    source_date TEXT NOT NULL,
                    query_text TEXT NOT NULL,
                    start INTEGER NOT NULL,
                    position INTEGER NOT NULL,
                    observed_at TEXT NOT NULL,
                    PRIMARY KEY (
                        provider, office_id, article_id, code, source_date, query_text
                    )
                );
                CREATE TABLE IF NOT EXISTS news_article_fetch_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    office_id TEXT NOT NULL,
                    article_id TEXT NOT NULL,
                    attempted_at TEXT NOT NULL,
                    url_role TEXT NOT NULL,
                    requested_url TEXT NOT NULL,
                    final_url TEXT NOT NULL,
                    http_status INTEGER,
                    status TEXT NOT NULL,
                    error TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    published_precision TEXT NOT NULL,
                    published_at_source TEXT NOT NULL,
                    published_at_raw TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_news_article_fetch_attempts_article
                    ON news_article_fetch_attempts(provider, office_id, article_id, attempted_at);
                CREATE TABLE IF NOT EXISTS news_backfill_jobs (
                    code TEXT NOT NULL,
                    target_date TEXT NOT NULL,
                    query_text TEXT NOT NULL,
                    name_source TEXT NOT NULL DEFAULT 'stocks.current',
                    name_source_ref TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    pages_observed INTEGER NOT NULL DEFAULT 0,
                    items_observed INTEGER NOT NULL DEFAULT 0,
                    usable_articles INTEGER NOT NULL DEFAULT 0,
                    unreadable_articles INTEGER NOT NULL DEFAULT 0,
                    missing_time_articles INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (code, target_date, query_text)
                );
                CREATE INDEX IF NOT EXISTS idx_news_backfill_jobs_state
                    ON news_backfill_jobs(state, target_date, code);
                CREATE TABLE IF NOT EXISTS market_bars (
                    provider TEXT NOT NULL,
                    code TEXT NOT NULL,
                    venue TEXT NOT NULL,
                    session_scope TEXT NOT NULL,
                    interval_seconds INTEGER NOT NULL,
                    adjustment_mode TEXT NOT NULL,
                    bar_time TEXT NOT NULL,
                    bar_time_semantics TEXT NOT NULL DEFAULT 'provider_value_unverified',
                    raw_date INTEGER,
                    raw_time INTEGER,
                    open INTEGER,
                    high INTEGER,
                    low INTEGER,
                    close INTEGER,
                    volume INTEGER,
                    trading_value INTEGER,
                    observed_at TEXT NOT NULL,
                    available_at TEXT NOT NULL,
                    PRIMARY KEY (
                        provider, code, venue, session_scope, interval_seconds,
                        adjustment_mode, bar_time
                    )
                );
                """
            )
            columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(news_articles)")
            }
            if "original_url" not in columns:
                connection.execute(
                    "ALTER TABLE news_articles ADD COLUMN original_url TEXT NOT NULL DEFAULT ''"
                )
            if "portal_url" not in columns:
                connection.execute(
                    "ALTER TABLE news_articles ADD COLUMN portal_url TEXT NOT NULL DEFAULT ''"
                )
            additions = {
                "published_at_source": "TEXT NOT NULL DEFAULT ''",
                "published_at_raw": "TEXT NOT NULL DEFAULT ''",
                "publication_source_url": "TEXT NOT NULL DEFAULT ''",
                "article_fetch_status": "TEXT NOT NULL DEFAULT 'not_fetched'",
                "article_fetched_at": "TEXT NOT NULL DEFAULT ''",
                "training_eligible": "INTEGER NOT NULL DEFAULT 0",
                "training_exclusion_reason": "TEXT NOT NULL DEFAULT 'publication_time_unverified'",
            }
            for column, declaration in additions.items():
                if column not in columns:
                    connection.execute(
                        f"ALTER TABLE news_articles ADD COLUMN {column} {declaration}"
                    )
            bar_columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(market_bars)")
            }
            bar_additions = {
                "bar_time_semantics": "TEXT NOT NULL DEFAULT 'provider_value_unverified'",
                "raw_date": "INTEGER",
                "raw_time": "INTEGER",
            }
            for column, declaration in bar_additions.items():
                if column not in bar_columns:
                    connection.execute(
                        f"ALTER TABLE market_bars ADD COLUMN {column} {declaration}"
                    )
            job_columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(news_backfill_jobs)")
            }
            job_additions = {
                "name_source": "TEXT NOT NULL DEFAULT 'stocks.current'",
                "name_source_ref": "TEXT NOT NULL DEFAULT ''",
            }
            for column, declaration in job_additions.items():
                if column not in job_columns:
                    connection.execute(
                        f"ALTER TABLE news_backfill_jobs ADD COLUMN {column} {declaration}"
                    )


def store_naver_stock_news_page(path: Path, page: NaverStockNewsPage) -> None:
    initialize_probe_database(path)
    request_key = f"code={page.code}&page={page.page}&page_size={page.page_size}"
    digest = hashlib.sha256(page.raw_json.encode("utf-8")).hexdigest()
    endpoint = f"{NAVER_STOCK_NEWS_ENDPOINT}?{urlencode({'itemCode': page.code, 'page': page.page, 'pageSize': page.page_size})}"
    with closing(sqlite3.connect(path)) as connection:
        with connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO source_pages
                (provider, request_key, observed_at, endpoint, response_sha256, payload_json,
                 item_count, reported_total)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    NAVER_STOCK_NEWS_PROVIDER, request_key, page.observed_at, endpoint, digest,
                    page.raw_json, len(page.items), page.reported_total,
                ),
            )
            for item in page.items:
                connection.execute(
                    """
                    INSERT INTO news_articles
                    (provider, office_id, article_id, published_at, published_precision,
                     office_name, title, summary, article_url, original_url, portal_url, image_url,
                     published_at_source, published_at_raw, training_eligible,
                     training_exclusion_reason, first_observed_at, last_observed_at)
                    VALUES (?, ?, ?, ?, 'minute', ?, ?, ?, ?, '', ?, ?,
                            'naver_stock_api:datetime', ?, 1, '', ?, ?)
                    ON CONFLICT(provider, office_id, article_id) DO UPDATE SET
                        published_at=excluded.published_at,
                        office_name=excluded.office_name,
                        title=excluded.title,
                        summary=excluded.summary,
                        article_url=excluded.article_url,
                        portal_url=excluded.portal_url,
                        image_url=excluded.image_url,
                        published_at_source=excluded.published_at_source,
                        published_at_raw=excluded.published_at_raw,
                        training_eligible=1,
                        training_exclusion_reason='',
                        last_observed_at=excluded.last_observed_at
                    """,
                    (
                        NAVER_STOCK_NEWS_PROVIDER, item.office_id, item.article_id,
                        item.published_at, item.office_name, item.title, item.summary,
                        item.article_url, item.article_url, item.image_url,
                        item.published_at, page.observed_at, page.observed_at,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO news_article_symbols
                    (provider, office_id, article_id, code, page, cluster_index,
                     related_index, observed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(provider, office_id, article_id, code) DO UPDATE SET
                        page=MIN(news_article_symbols.page, excluded.page),
                        cluster_index=excluded.cluster_index,
                        related_index=excluded.related_index,
                        observed_at=excluded.observed_at
                    """,
                    (
                        NAVER_STOCK_NEWS_PROVIDER, item.office_id, item.article_id, page.code,
                        page.page, item.cluster_index, item.related_index, page.observed_at,
                    ),
                )


def store_naver_historical_search_page(path: Path, page: NaverHistoricalNewsPage) -> None:
    initialize_probe_database(path)
    request_key = (
        f"code={page.code}&date={page.target_date}&query={page.query}&start={page.start}"
    )
    digest = hashlib.sha256(page.raw_json.encode("utf-8")).hexdigest()
    endpoint = _naver_historical_search_url(page.query, page.target_date, page.start)
    with closing(sqlite3.connect(path)) as connection:
        with connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO source_pages
                (provider, request_key, observed_at, endpoint, response_sha256, payload_json,
                 item_count, reported_total)
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    NAVER_HISTORICAL_SEARCH_PROVIDER, request_key, page.observed_at, endpoint,
                    digest, page.raw_json, len(page.items),
                ),
            )
            for item in page.items:
                connection.execute(
                    """
                    INSERT INTO news_articles
                    (provider, office_id, article_id, published_at, published_precision,
                     office_name, title, summary, article_url, original_url, portal_url, image_url,
                     published_at_source, published_at_raw, first_observed_at, last_observed_at)
                    VALUES (?, ?, ?, ?, 'date', ?, ?, ?, ?, ?, ?, '',
                            'naver_search_target_date', ?, ?, ?)
                    ON CONFLICT(provider, office_id, article_id) DO UPDATE SET
                        office_name=excluded.office_name,
                        title=excluded.title,
                        summary=excluded.summary,
                        article_url=excluded.article_url,
                        original_url=excluded.original_url,
                        portal_url=excluded.portal_url,
                        last_observed_at=excluded.last_observed_at
                    """,
                    (
                        NAVER_HISTORICAL_SEARCH_PROVIDER, item.office_id, item.article_id,
                        page.target_date, item.office_name, item.title, item.summary,
                        item.article_url, item.original_url, item.portal_url,
                        page.target_date, page.observed_at, page.observed_at,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO news_search_observations
                    (provider, office_id, article_id, code, source_date, query_text,
                     start, position, observed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(
                        provider, office_id, article_id, code, source_date, query_text
                    ) DO UPDATE SET
                        start=MIN(news_search_observations.start, excluded.start),
                        position=excluded.position,
                        observed_at=excluded.observed_at
                    """,
                    (
                        NAVER_HISTORICAL_SEARCH_PROVIDER, item.office_id, item.article_id,
                        page.code, page.target_date, page.query, page.start, item.position,
                        page.observed_at,
                    ),
                )


def store_article_publication_result(path: Path, result: ArticlePublicationResult) -> None:
    initialize_probe_database(path)
    with closing(sqlite3.connect(path)) as connection:
        with connection:
            if result.published_at:
                connection.execute(
                    """
                    UPDATE news_articles SET
                        published_at=?, published_precision=?, published_at_source=?,
                        published_at_raw=?, publication_source_url=?, article_fetch_status=?,
                        article_fetched_at=?, training_eligible=1,
                        training_exclusion_reason=''
                    WHERE provider=? AND office_id=? AND article_id=?
                    """,
                    (
                        result.published_at, result.published_precision,
                        result.published_at_source, result.published_at_raw,
                        result.final_url or result.source_url, result.status, result.fetched_at,
                        result.provider, result.office_id, result.article_id,
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE news_articles SET
                        publication_source_url=?, article_fetch_status=?, article_fetched_at=?,
                        training_eligible=0, training_exclusion_reason=?
                    WHERE provider=? AND office_id=? AND article_id=?
                    """,
                    (
                        result.final_url or result.source_url, result.status, result.fetched_at,
                        result.status,
                        result.provider, result.office_id, result.article_id,
                    ),
                )
            for position, attempt in enumerate(result.attempts):
                identity = "|".join((
                    result.provider, result.office_id, result.article_id, result.fetched_at,
                    str(position), attempt.url_role, attempt.requested_url,
                ))
                attempt_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
                connection.execute(
                    """
                    INSERT OR IGNORE INTO news_article_fetch_attempts
                    (attempt_id, provider, office_id, article_id, attempted_at, url_role,
                     requested_url, final_url, http_status, status, error, published_at,
                     published_precision, published_at_source, published_at_raw)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        attempt_id, result.provider, result.office_id, result.article_id,
                        result.fetched_at, attempt.url_role, attempt.requested_url,
                        attempt.final_url, attempt.http_status, attempt.status, attempt.error,
                        attempt.published_at, attempt.published_precision,
                        attempt.published_at_source, attempt.published_at_raw,
                    ),
                )


def store_daishin_probe_payload(path: Path, payload: Mapping[str, Any]) -> int:
    if payload.get("provider") != DAISHIN_PROVIDER:
        raise ValueError("unexpected Daishin provider")
    code = _stock_code(str(payload.get("code", "")))
    interval_seconds = int(payload.get("interval_seconds", 0))
    if interval_seconds not in {60, 300}:
        raise ValueError("Daishin interval must be 60 or 300 seconds")
    venue = str(payload.get("venue", ""))
    session_scope = str(payload.get("session_scope", ""))
    adjustment_mode = str(payload.get("adjustment_mode", ""))
    observed_at = str(payload.get("observed_at", ""))
    bar_time_semantics = str(payload.get("bar_time_semantics", "provider_value_unverified"))
    bars = payload.get("bars")
    if venue not in {"A", "K", "N"}:
        raise ValueError("invalid Daishin venue")
    if session_scope not in {"regular", "regular_and_after"}:
        raise ValueError("invalid Daishin session scope")
    if adjustment_mode not in {"raw", "adjusted"}:
        raise ValueError("invalid Daishin adjustment mode")
    if bar_time_semantics not in {"provider_value_unverified", "interval_end"}:
        raise ValueError("invalid Daishin bar time semantics")
    if not observed_at or not isinstance(bars, list):
        raise ValueError("incomplete Daishin probe payload")
    initialize_probe_database(path)
    saved = 0
    with closing(sqlite3.connect(path)) as connection:
        with connection:
            for bar in bars:
                if not isinstance(bar, Mapping):
                    continue
                bar_time = str(bar.get("bar_time", ""))
                if not bar_time:
                    continue
                connection.execute(
                    """
                    INSERT INTO market_bars
                    (provider, code, venue, session_scope, interval_seconds,
                     adjustment_mode, bar_time, bar_time_semantics, raw_date, raw_time,
                     open, high, low, close, volume, trading_value, observed_at, available_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(
                        provider, code, venue, session_scope, interval_seconds,
                        adjustment_mode, bar_time
                    ) DO UPDATE SET
                        bar_time_semantics=excluded.bar_time_semantics,
                        raw_date=excluded.raw_date,
                        raw_time=excluded.raw_time,
                        open=excluded.open,
                        high=excluded.high,
                        low=excluded.low,
                        close=excluded.close,
                        volume=excluded.volume,
                        trading_value=excluded.trading_value,
                        observed_at=excluded.observed_at,
                        available_at=excluded.available_at
                    """,
                    (
                        DAISHIN_PROVIDER, code, venue, session_scope, interval_seconds,
                        adjustment_mode, bar_time, bar_time_semantics,
                        bar.get("raw_date"), bar.get("raw_time"), bar.get("open"),
                        bar.get("high"), bar.get("low"), bar.get("close"), bar.get("volume"),
                        bar.get("trading_value"), observed_at, observed_at,
                    ),
                )
                saved += 1
    return saved


def import_daishin_backfill_ndjson(
    artifact_path: Path,
    database_path: Path,
    *,
    before_date: str = "",
) -> dict[str, Any]:
    cutoff = _iso_date(before_date) if before_date else ""
    pages = 0
    observed_bars = 0
    selected_bars = 0
    saved_bars = 0
    oldest = ""
    newest = ""
    summary: dict[str, Any] = {}
    with artifact_path.open("r", encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"invalid NDJSON at line {line_number}: {error}") from error
            if not isinstance(record, Mapping):
                raise ValueError(f"invalid NDJSON record at line {line_number}")
            record_type = str(record.get("record_type", ""))
            if record_type == "error":
                raise ValueError(f"Daishin backfill failed: {record.get('error', '')}")
            if record_type == "summary":
                summary = dict(record)
                continue
            if record_type != "page":
                raise ValueError(f"unknown NDJSON record type at line {line_number}")
            bars = record.get("bars")
            if not isinstance(bars, list):
                raise ValueError(f"Daishin page has no bars at line {line_number}")
            pages += 1
            observed_bars += len(bars)
            selected = [
                bar for bar in bars
                if isinstance(bar, Mapping)
                and (not cutoff or str(bar.get("bar_time", ""))[:10] < cutoff)
            ]
            selected_bars += len(selected)
            if selected:
                page_newest = str(selected[0].get("bar_time", ""))
                page_oldest = str(selected[-1].get("bar_time", ""))
                newest = max(newest, page_newest) if newest else page_newest
                oldest = min(oldest, page_oldest) if oldest else page_oldest
            payload = dict(record)
            payload["bars"] = selected
            saved_bars += store_daishin_probe_payload(database_path, payload)
    if not summary:
        raise ValueError("Daishin backfill artifact has no summary record")
    return {
        "artifact": str(artifact_path.resolve()),
        "code": str(summary.get("code", "")),
        "interval_seconds": int(summary.get("interval_seconds", 0)),
        "pages": pages,
        "observed_bars": observed_bars,
        "selected_bars": selected_bars,
        "saved_bars": saved_bars,
        "before_date": cutoff,
        "newest": newest,
        "oldest": oldest,
        "provider_has_more": bool(summary.get("provider_has_more")),
        "stopped_by_max_pages": bool(summary.get("stopped_by_max_pages")),
    }


def inspect_daishin_environment(*, try_connection: bool = True) -> DaishinEnvironment:
    progids = ("CpUtil.CpCybos", "CpSysDib.StockChart", "CpUtil.CpCodeMgr")
    if sys.platform != "win32":
        return DaishinEnvironment(
            False, 64 if sys.maxsize > 2**32 else 32, None, False, (), progids, None,
            "Daishin CREON Plus COM is available only on Windows.",
        )
    import winreg

    registered: list[str] = []
    for progid in progids:
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{progid}\CLSID"):
                registered.append(progid)
        except OSError:
            pass
    try:
        elevated: bool | None = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        elevated = None
    try:
        import win32com.client  # type: ignore[import-not-found]
    except ImportError:
        pywin32_available = False
    else:
        pywin32_available = True
    connected: bool | None = None
    detail = ""
    missing = tuple(progid for progid in progids if progid not in registered)
    if missing:
        detail = "CREON Plus COM ProgID is not registered: " + ", ".join(missing)
    elif not pywin32_available:
        detail = "pywin32 is not available in this Python interpreter."
    elif try_connection:
        try:
            cybos = win32com.client.Dispatch("CpUtil.CpCybos")
            connected = int(cybos.IsConnect) == 1
            detail = "CREON Plus is connected." if connected else "CREON Plus is installed but not connected."
        except Exception as error:  # COM errors vary by installed pywin32 version.
            detail = f"CREON Plus connection check failed: {type(error).__name__}: {error}"
    else:
        detail = "CREON Plus registration is present; connection was not attempted."
    return DaishinEnvironment(
        True,
        64 if platform.architecture()[0] == "64bit" else 32,
        elevated,
        pywin32_available,
        tuple(registered),
        missing,
        connected,
        detail,
    )


def _stock_code(code: str) -> str:
    normalized = str(code).strip().upper()
    if normalized.startswith("A") and len(normalized) == 7:
        normalized = normalized[1:]
    if len(normalized) != 6 or not normalized.isdigit():
        raise ValueError("stock code must be six digits")
    return normalized


def _naver_datetime(raw: str) -> str:
    value = raw.strip()
    if len(value) != 12 or not value.isdigit():
        return ""
    parsed = datetime.strptime(value, "%Y%m%d%H%M")
    return parsed.isoformat(timespec="minutes") + "+09:00"


def _publication_datetime(raw: str) -> tuple[str, str] | None:
    value = html.unescape(str(raw)).strip()
    if not re.search(r"(?:\d{1,2}:\d{2}|T\d{2}:\d{2}|\d{12,14})", value):
        return None
    normalized = value.replace("Z", "+00:00")
    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        pass
    if parsed is None:
        korean = re.sub(r"\s+", " ", value.replace("오전", "AM").replace("오후", "PM"))
        formats = (
            "%Y.%m.%d. %p %I:%M:%S", "%Y.%m.%d. %p %I:%M",
            "%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M",
            "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
            "%Y%m%d%H%M%S", "%Y%m%d%H%M",
        )
        for date_format in formats:
            try:
                parsed = datetime.strptime(korean, date_format)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone(timedelta(hours=9)))
    has_seconds = bool(re.search(r"(?:\d{2}:\d{2}:\d{2}|\d{14})(?:\D|$)", value))
    precision = "second" if has_seconds else "minute"
    return parsed.isoformat(timespec="seconds" if has_seconds else "minutes"), precision


def _titles_compatible(expected: str, actual: str) -> bool:
    def normalized(value: str) -> str:
        return "".join(character.lower() for character in _plain_text(value) if character.isalnum())

    left = normalized(expected)
    right = normalized(actual)
    if not left or not right:
        return False
    if left in right or right in left:
        return True
    return difflib.SequenceMatcher(None, left, right).ratio() >= 0.6


def _iso_date(raw: str) -> str:
    value = str(raw).strip()
    return datetime.strptime(value, "%Y-%m-%d").date().isoformat()


def _bootstrap_payload(script: str) -> Mapping[str, Any]:
    marker = "entry.bootstrap("
    marker_index = script.find(marker)
    if marker_index < 0:
        raise ValueError("Naver historical search bootstrap call is missing")
    object_index = script.find("{", marker_index + len(marker))
    if object_index < 0:
        raise ValueError("Naver historical search bootstrap payload is missing")
    value, _ = json.JSONDecoder().raw_decode(script[object_index:])
    if not isinstance(value, Mapping):
        raise ValueError("Naver historical search bootstrap payload is not an object")
    return value


def _mapping_nodes(value: Any):
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _mapping_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _mapping_nodes(child)


def _plain_text(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", html.unescape(value))
    return re.sub(r"\s+", " ", without_tags).strip()


def _naver_article_ids(url: str) -> tuple[str, str]:
    match = re.search(r"/(?:mnews/)?article/(\d+)/(\d+)", url)
    return (match.group(1), match.group(2)) if match else ("", "")
