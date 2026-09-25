from __future__ import annotations

import base64
import json
import os
import re
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from kiwoom_monitor.application.news_analysis import NewsAssessment, assess_stock_news
from kiwoom_monitor.infrastructure.kiwoom_rest.local_config import _protect, _unprotect
from kiwoom_monitor.infrastructure.system_ssl import system_ssl_context


@dataclass(frozen=True)
class NaverNewsCredentials:
    client_id: str = ""
    client_secret: str = ""


@dataclass(frozen=True)
class NewsFilterSettings:
    enabled: bool = True
    excluded_words: tuple[str, ...] = (
        "광고", "체험단", "이벤트", "할인", "쿠폰", "추천 상품", "구매 후기",
    )
    excluded_providers: tuple[str, ...] = ()
    provider_filter_enabled: bool = True
    visible_columns: tuple[str, ...] = ("time", "provider", "category", "outlook", "title")
    positive_color: str = "#C00000"
    negative_color: str = "#0070C0"
    mixed_color: str = "#7030A0"
    neutral_color: str = "#666666"
    stored_news_limit: int = 200


@dataclass(frozen=True)
class NewsAISettings:
    provider: str = "none"
    api_key: str = ""
    model: str = ""
    daily_limit: int = 30
    auto_recent_limit: int = 10
    auto_analyze: bool = False
    request_mode: str = "single"
    batch_size: int = 10


@dataclass(frozen=True)
class OfficialNewsSettings:
    dart_api_key: str = ""
    dart_enabled: bool = False


@dataclass(frozen=True)
class LocalNewsSources:
    stock_name_enabled: bool = True
    common_enabled: bool = False
    common_queries: tuple[str, ...] = ()
    stock_site_enabled: bool = False
    market_enabled: bool = False
    stock_site_url: str = "https://stock.naver.com/api/domestic/detail/news"
    flash_url: str = "https://stock.naver.com/api/domestic/news/list"
    world_url: str = "https://stock.naver.com/api/foreign/news/worldNews"


@dataclass(frozen=True)
class StockNewsItem:
    title: str
    description: str
    link: str
    original_link: str
    published_at: datetime | None
    assessment: NewsAssessment


@dataclass(frozen=True)
class NaverNewsPage:
    items: tuple[StockNewsItem, ...]
    total: int
    start: int
    display: int


class LocalNaverNewsConfig:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> NaverNewsCredentials:
        values = self._load_values()
        return NaverNewsCredentials(str(values.get("client_id", "")), str(values.get("client_secret", "")))

    def load_filter(self) -> NewsFilterSettings:
        values = self._load_values()
        raw_words = values.get("excluded_words")
        words = tuple(str(word).strip() for word in raw_words if str(word).strip()) \
            if isinstance(raw_words, list) else NewsFilterSettings().excluded_words
        raw_providers = values.get("excluded_providers")
        providers = tuple(str(provider).strip() for provider in raw_providers if str(provider).strip()) \
            if isinstance(raw_providers, list) else ()
        return NewsFilterSettings(
            bool(values.get("ad_filter_enabled", True)), words, providers,
            bool(values.get("provider_filter_enabled", True)),
            tuple(
                key for key in values.get("visible_columns", ("time", "provider", "category", "outlook", "title"))
                if key in {"time", "provider", "category", "outlook", "title"}
            ),
            _color_setting(values, "news_positive_color", "#C00000"),
            _color_setting(values, "news_negative_color", "#0070C0"),
            _color_setting(values, "news_mixed_color", "#7030A0"),
            _color_setting(values, "news_neutral_color", "#666666"),
            _stored_news_limit(values.get("stored_news_limit")),
        )

    def load_ai(self) -> NewsAISettings:
        values = self._load_values()
        try:
            daily_limit = int(values.get("ai_daily_limit", 30))
        except (TypeError, ValueError):
            daily_limit = 30
        try:
            auto_recent_limit = int(values.get("ai_auto_recent_limit", 10))
        except (TypeError, ValueError):
            auto_recent_limit = 10
        try:
            batch_size = int(values.get("ai_batch_size", 10))
        except (TypeError, ValueError):
            batch_size = 10
        return NewsAISettings(
            str(values.get("ai_provider", "none")), str(values.get("ai_api_key", "")),
            str(values.get("ai_model", "")), max(0, min(1_000_000, daily_limit)),
            max(1, min(1000, auto_recent_limit)),
            bool(values.get("ai_auto_analyze", False)),
            str(values.get("ai_request_mode", "single")) if values.get("ai_request_mode") in {"single", "batch"} else "single",
            max(2, min(20, batch_size)),
        )

    @property
    def directory(self) -> Path:
        return self._path.parent

    def load_official(self) -> OfficialNewsSettings:
        values = self._load_values()
        return OfficialNewsSettings(
            str(values.get("dart_api_key", "")), bool(values.get("dart_enabled", False)),
        )

    def load_shortcuts(self) -> tuple[tuple[str, str], ...]:
        values = self._load_values()
        raw = values.get("news_shortcuts")
        if not isinstance(raw, list):
            return (
                ("KIND", "https://kind.krx.co.kr/"),
                ("공모주 일정", "https://www.38.co.kr/html/fund/index.htm?o=nw"),
            )
        shortcuts: list[tuple[str, str]] = []
        for item in raw[:5]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            url = str(item.get("url", "")).strip()
            if name and url:
                shortcuts.append((name, url))
        return tuple(shortcuts)

    def load_sources(self) -> LocalNewsSources:
        values = self._load_values()
        defaults = LocalNewsSources()
        queries = values.get("local_news_common_queries")
        return LocalNewsSources(
            bool(values.get("local_news_stock_name_enabled", defaults.stock_name_enabled)),
            bool(values.get("local_news_common_enabled", defaults.common_enabled)),
            tuple(str(value).strip() for value in queries if str(value).strip())
            if isinstance(queries, list) else defaults.common_queries,
            bool(values.get("local_news_stock_site_enabled", defaults.stock_site_enabled)),
            bool(values.get("local_news_market_enabled", defaults.market_enabled)),
            str(values.get("local_news_stock_site_url") or defaults.stock_site_url),
            str(values.get("local_news_flash_url") or defaults.flash_url),
            str(values.get("local_news_world_url") or defaults.world_url),
        )

    def _load_values(self) -> dict[str, object]:
        if not self._path.exists():
            return {}
        raw = self._path.read_text(encoding="utf-8").strip()
        if not raw.startswith("NAVER_NEWS_CONFIG_ENCRYPTED="):
            return {}
        payload = _unprotect(base64.b64decode(raw.split("=", 1)[1])).decode("utf-8")
        values = json.loads(payload)
        return values if isinstance(values, dict) else {}

    def save(
        self, credentials: NaverNewsCredentials, news_filter: NewsFilterSettings | None = None,
        ai: NewsAISettings | None = None, official: OfficialNewsSettings | None = None,
        shortcuts: tuple[tuple[str, str], ...] | None = None,
        sources: LocalNewsSources | None = None,
    ) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        current_filter = news_filter or self.load_filter()
        current_ai = ai or self.load_ai()
        current_official = official or self.load_official()
        current_shortcuts = self.load_shortcuts() if shortcuts is None else shortcuts[:5]
        values = {**self._load_values(),
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
            "ad_filter_enabled": current_filter.enabled,
            "excluded_words": list(current_filter.excluded_words),
            "excluded_providers": list(current_filter.excluded_providers),
            "provider_filter_enabled": current_filter.provider_filter_enabled,
            "visible_columns": list(current_filter.visible_columns),
            "news_positive_color": current_filter.positive_color,
            "news_negative_color": current_filter.negative_color,
            "news_mixed_color": current_filter.mixed_color,
            "news_neutral_color": current_filter.neutral_color,
            "stored_news_limit": _stored_news_limit(current_filter.stored_news_limit),
            "ai_provider": current_ai.provider,
            "ai_api_key": current_ai.api_key,
            "ai_model": current_ai.model,
            "ai_daily_limit": current_ai.daily_limit,
            "ai_auto_recent_limit": current_ai.auto_recent_limit,
            "ai_auto_analyze": current_ai.auto_analyze,
            "ai_request_mode": current_ai.request_mode,
            "ai_batch_size": current_ai.batch_size,
            "dart_api_key": current_official.dart_api_key,
            "dart_enabled": current_official.dart_enabled,
            "news_shortcuts": [{"name": name, "url": url} for name, url in current_shortcuts],
        }
        if sources is not None:
            values.update(
                local_news_stock_name_enabled=sources.stock_name_enabled,
                local_news_common_enabled=sources.common_enabled,
                local_news_common_queries=list(sources.common_queries),
                local_news_stock_site_enabled=sources.stock_site_enabled,
                local_news_market_enabled=sources.market_enabled,
                local_news_stock_site_url=sources.stock_site_url,
                local_news_flash_url=sources.flash_url,
                local_news_world_url=sources.world_url,
            )
        payload = json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        encrypted = base64.b64encode(_protect(payload)).decode("ascii")
        temporary = self._path.with_name(
            f".{self._path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        temporary.write_text(f"NAVER_NEWS_CONFIG_ENCRYPTED={encrypted}\n", encoding="utf-8")
        temporary.replace(self._path)


def _color_setting(values: dict[str, object], key: str, default: str) -> str:
    value = str(values.get(key, default)).strip().upper()
    return value if re.fullmatch(r"#[0-9A-F]{6}", value) else default


def _stored_news_limit(value: object) -> int:
    try:
        return max(50, min(1000, int(value)))
    except (TypeError, ValueError):
        return 200


def is_excluded_news(item: StockNewsItem, settings: NewsFilterSettings) -> bool:
    text = f"{item.title} {item.description}".casefold()
    if settings.enabled and any(word.casefold() in text for word in settings.excluded_words if word):
        return True
    if not settings.provider_filter_enabled:
        return False
    provider_text = f"{news_provider(item)} {news_provider_domain(item)}".casefold()
    return any(provider.casefold() in provider_text for provider in settings.excluded_providers if provider)


_PROVIDER_NAMES = {
    "yna.co.kr": "연합뉴스",
    "newsis.com": "뉴시스",
    "edaily.co.kr": "이데일리",
    "hankyung.com": "한국경제",
    "mk.co.kr": "매일경제",
    "sedaily.com": "서울경제",
    "mt.co.kr": "머니투데이",
    "fnnews.com": "파이낸셜뉴스",
    "asiae.co.kr": "아시아경제",
    "etnews.com": "전자신문",
    "thebell.co.kr": "더벨",
    "hansbiz.co.kr": "한스경제",
    "news.einfomax.co.kr": "연합인포맥스",
    "businesspost.co.kr": "비즈니스포스트",
    "newdaily.co.kr": "뉴데일리",
    "bigtanews.co.kr": "빅터뉴스",
    "stoo.com": "스포츠투데이",
    "newsprime.co.kr": "프라임경제",
    "skyedaily.com": "스카이데일리",
    "dnews.co.kr": "대한경제",
    "peoplewatch.co.kr": "피플워치",
    "dart.fss.or.kr": "DART 공시",
}


def news_provider_domain(item: StockNewsItem) -> str:
    return provider_domain(item.original_link, item.link)


def provider_domain(original_link: str, link: str = "") -> str:
    domain = urlparse(original_link or link).hostname or ""
    return domain.casefold().removeprefix("www.")


def provider_name(domain: str) -> str:
    normalized = domain.casefold().removeprefix("www.")
    for suffix, name in _PROVIDER_NAMES.items():
        if normalized == suffix or normalized.endswith(f".{suffix}"):
            return name
    return normalized or "제공처 미확인"


def news_provider(item: StockNewsItem) -> str:
    return provider_name(news_provider_domain(item))


def is_excluded_provider(domain: str, name: str, excluded: tuple[str, ...]) -> bool:
    provider_text = f"{name} {domain}".casefold()
    return any(value.strip().casefold() in provider_text for value in excluded if value.strip())


class NaverNewsClient:
    HUB_ENDPOINT = "https://naverapihub.apigw.ntruss.com/search/v1/news"
    LEGACY_ENDPOINT = "https://openapi.naver.com/v1/search/news.json"

    def __init__(self, credentials: NaverNewsCredentials, *, timeout_seconds: float = 8.0) -> None:
        self._credentials = credentials
        self._timeout_seconds = timeout_seconds

    def search(
        self, stock_name: str, *, since: datetime | None = None, page_size: int = 100,
        max_results: int = 1000, request_claim: Callable[[], bool] | None = None,
    ) -> tuple[StockNewsItem, ...]:
        if not self._credentials.client_id or not self._credentials.client_secret:
            raise ValueError("네이버 뉴스 API Client ID와 Client Secret을 먼저 입력하세요.")
        page_size = max(1, min(100, page_size))
        max_results = max(1, min(1000, max_results))
        cutoff = since.astimezone(UTC) if since is not None else None
        results: list[StockNewsItem] = []
        seen: set[str] = set()
        start = 1
        reached_cutoff = False
        while start <= max_results and not reached_cutoff:
            display = min(page_size, max_results - start + 1)
            if request_claim is None:
                page = self.search_page(stock_name, start=start, display=display)
            else:
                page = self.search_page(
                    stock_name, start=start, display=display, request_claim=request_claim,
                )
            for item in page.items:
                if cutoff is not None and item.published_at is not None and item.published_at.astimezone(UTC) <= cutoff:
                    reached_cutoff = True
                    break
                identity = item.original_link or item.link or item.title
                if not identity or identity in seen:
                    continue
                seen.add(identity)
                results.append(item)
            if page.display < display:
                break
            start += display
        return tuple(results)

    def search_page(
        self, query_text: str, *, start: int = 1, display: int = 100,
        request_claim: Callable[[], bool] | None = None,
    ) -> NaverNewsPage:
        if not self._credentials.client_id or not self._credentials.client_secret:
            raise ValueError("네이버 뉴스 API Client ID와 Client Secret을 먼저 입력하세요.")
        start, display = max(1, min(1000, int(start))), max(1, min(100, int(display)))
        query = urlencode({"query": query_text, "display": display, "start": start, "sort": "date"})
        payload = (
            self._fetch_with_legacy_fallback(query)
            if request_claim is None
            else self._fetch_with_legacy_fallback(query, request_claim=request_claim)
        )
        raw_items = payload.get("items", ())
        if not isinstance(raw_items, list):
            raise ValueError("네이버 뉴스 응답 형식이 올바르지 않습니다.")
        items: list[StockNewsItem] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            published_at = _parse_published_at(str(raw.get("pubDate", "")))
            if published_at is None:
                continue
            title = _clean_html(str(raw.get("title", "")))
            description = _clean_html(str(raw.get("description", "")))
            items.append(StockNewsItem(
                title, description, str(raw.get("link", "")).strip(),
                str(raw.get("originallink", "")).strip(), published_at,
                assess_stock_news(query_text, title, description),
            ))
        return NaverNewsPage(tuple(items), int(payload.get("total") or 0),
                             int(payload.get("start") or start), len(raw_items))

    def _fetch_with_legacy_fallback(
        self, query: str, *, request_claim: Callable[[], bool] | None = None,
    ) -> dict[str, object]:
        self._claim_request(request_claim)
        try:
            return self._fetch(
                self.HUB_ENDPOINT, query, "X-NCP-APIGW-API-KEY-ID", "X-NCP-APIGW-API-KEY",
            )
        except HTTPError as error:
            if error.code not in {401, 403}:
                raise
            # 2026-07 이전에 발급한 개발자센터 키도 유예기간 동안 지원한다.
            self._claim_request(request_claim)
            return self._fetch(
                self.LEGACY_ENDPOINT, query, "X-Naver-Client-Id", "X-Naver-Client-Secret",
            )

    @staticmethod
    def _claim_request(request_claim: Callable[[], bool] | None) -> None:
        if request_claim is not None and not request_claim():
            raise RuntimeError("네이버 뉴스 일일 요청 예산을 모두 사용했습니다.")

    def _fetch(self, endpoint: str, query: str, id_header: str, secret_header: str) -> dict[str, object]:
        request = Request(
            f"{endpoint}?{query}",
            headers={
                id_header: self._credentials.client_id,
                secret_header: self._credentials.client_secret,
                "User-Agent": "KiwoomRealtimeMonitor/NewsPrototype",
            },
        )
        with urlopen(request, timeout=self._timeout_seconds, context=system_ssl_context()) as response:
            return json.loads(response.read().decode("utf-8"))


def _clean_html(value: str) -> str:
    return " ".join(unescape(value).replace("<b>", "").replace("</b>", "").split())


def _parse_published_at(value: str) -> datetime | None:
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
