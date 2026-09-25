"""Recent per-stock articles from Naver Stock's public news listing."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from kiwoom_monitor.application.news_analysis import assess_stock_news
from kiwoom_monitor.infrastructure.naver_news import StockNewsItem
from kiwoom_monitor.infrastructure.system_ssl import system_ssl_context


KST = ZoneInfo("Asia/Seoul")
ENDPOINT = "https://stock.naver.com/api/domestic/detail/news"


@dataclass(frozen=True)
class StockNewsBatch:
    items: tuple[StockNewsItem, ...]
    latest_published_at: datetime | None
    next_page: int
    complete: bool
    page_limit_reached: bool = False


class NaverStockNewsClient:
    """Read a bounded recent window; this listing is not historical coverage."""

    def __init__(self, *, fetcher=None, page_size: int = 30, max_pages: int = 3,
                 endpoint: str = ENDPOINT) -> None:
        self._fetcher = fetcher or self._fetch
        self._endpoint = endpoint
        self._page_size = max(1, min(100, int(page_size)))
        self._max_pages = max(1, min(10, int(max_pages)))

    def update_endpoint(self, endpoint: str) -> None:
        self._endpoint = endpoint

    def _fetch(self, code: str, page: int, page_size: int) -> dict:
        url = self._endpoint + "?" + urlencode({"itemCode": code, "page": page, "pageSize": page_size})
        request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        with urlopen(request, timeout=12, context=system_ssl_context()) as response:
            return json.load(response)

    def search(self, code: str, stock_name: str, *, since: datetime | None = None,
               start_page: int = 1) -> StockNewsBatch:
        if not (len(code) == 6 and code.isdigit()):
            raise ValueError("종목뉴스 조회에는 6자리 종목코드가 필요합니다.")
        cutoff = since.astimezone(UTC) if since is not None else None
        result: list[StockNewsItem] = []
        seen: set[str] = set()
        latest: datetime | None = None
        start_page = max(1, int(start_page))
        for page in range(start_page, start_page + self._max_pages):
            if page > 100:
                return StockNewsBatch(tuple(result), latest, 1, False, True)
            payload = self._fetcher(code, page, self._page_size)
            clusters = payload.get("clusters")
            if not isinstance(clusters, list):
                raise ValueError("네이버 증권 종목뉴스 응답에 clusters가 없습니다.")
            if not clusters:
                return StockNewsBatch(tuple(result), latest, 1, True)
            reached_cutoff = False
            for cluster in clusters:
                if not isinstance(cluster, dict) or not isinstance(cluster.get("items"), list):
                    continue
                for raw in cluster["items"]:
                    if not isinstance(raw, dict):
                        continue
                    office, article = str(raw.get("officeId") or ""), str(raw.get("articleId") or "")
                    if not (office.isdigit() and article.isdigit()):
                        continue
                    identity = f"{office}/{article}"
                    if identity in seen:
                        continue
                    seen.add(identity)
                    try:
                        published = datetime.strptime(str(raw.get("datetime") or ""), "%Y%m%d%H%M").replace(tzinfo=KST)
                    except ValueError:
                        continue
                    if latest is None or published > latest:
                        latest = published
                    if cutoff is not None and published.astimezone(UTC) <= cutoff:
                        reached_cutoff = True
                        continue
                    title = str(raw.get("title") or "").strip()
                    if not title:
                        continue
                    description = str(raw.get("body") or "").strip()
                    link = f"https://n.news.naver.com/mnews/article/{office}/{article}"
                    result.append(StockNewsItem(
                        title, description, link, "", published,
                        assess_stock_news(stock_name, title, description),
                    ))
            if reached_cutoff or len(clusters) < self._page_size:
                return StockNewsBatch(tuple(result), latest, 1, True)
            if page == 100:
                return StockNewsBatch(tuple(result), latest, 1, False, True)
        return StockNewsBatch(tuple(result), latest, page + 1, False)
