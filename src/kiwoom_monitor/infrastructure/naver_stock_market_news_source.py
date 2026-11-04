"""Live Naver Stock FLASH/WORLD source values, parsing and HTTP adapter.

Historical collection ledgers and resumable jobs live in naver_stock_market_news.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from html import unescape
from typing import Any, Callable
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
