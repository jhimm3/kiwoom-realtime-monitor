"""NAVER query-set collection policy and its independently-lived cursor loop."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import uuid
from contextlib import suppress
from datetime import UTC, datetime
from functools import lru_cache
from time import time
from typing import Any, Callable
from zoneinfo import ZoneInfo

from kiwoom_monitor.infrastructure.naver_news import (
    NaverNewsClient, StockNewsItem, is_excluded_provider, news_provider,
    news_provider_domain,
)
from kiwoom_monitor.infrastructure.krx.stock_catalog import fetch_krx_stock_catalog


DEFAULT_NEWS_QUERY_SET = (
    "증권", "코스피", "코스닥", "상장사", "수주 계약", "유상증자", "인수합병", "실적 전망", "최대주주",
)
TARGET_RULE_VERSION = "exact-krx-company-name-v1"
LOGGER = logging.getLogger(__name__)

# 수집 단계에서는 본문을 아직 읽지 못하므로 분류기보다 넓게 잡는다. 종목을
# 직접 찾았거나 아래 시장·기업사건 문맥이 있으면 저장하고, 명백한 검색 잡음만 버린다.
_MARKET_NEWS_SIGNAL = re.compile(
    r"(?:주식|증권|증시|코스피|코스닥|코넥스|상장(?:사|주|폐지)?|주가|시가총액|거래량|"
    r"거래대금|공시|목표주가|투자의견|매수의견|매도의견|외국인|기관(?:계|투자자)?|"
    r"프로그램\s*매매|공매도|서킷브레이커|사이드카|변동성완화장치|\bVI\b|IPO|기업공개|"
    r"ETF|ETN|펀드|채권|선물|옵션|환율|금리|국채|유가)",
    re.IGNORECASE,
)
_CORPORATE_EVENT_SIGNAL = re.compile(
    r"(?:실적|매출|영업이익|영업익|순이익|흑자|적자|수주|공급계약|납품|계약\s*(?:체결|해지)|"
    r"유상증자|무상증자|감자|자사주|배당|인수|합병|분할|최대주주|경영권|지분|투자\s*(?:유치|계획|결정)|"
    r"임상|품목허가|특허|리콜|제재|횡령|배임|압수수색|거래정지|주주총회|주총|감사위원|"
    r"이사회|대표이사|최고경영자|최고재무책임자|\bCEO\b|\bCFO\b|소송|손해배상|손배소|"
    r"재매각|\bM&A\b|공정위|과징금|담합|입찰|기업|업계|산업|공장|생산|수출|수입|공급망|"
    r"원가|사업|기술|개발|반도체|인공지능|\bAI\b|데이터센터|전력|방산|조선|금융|은행|보험|"
    r"관세|원자재|국제유가|경유|수요|호황|불황)",
    re.IGNORECASE,
)
_NON_INVESTMENT_NEWS = re.compile(
    r"(?:\[(?:포토|화보|골프IN화보|인사)\]|(?:골프|야구|축구|배구)[^.!?]{0,35}(?:대회|경기|선수|우승)|"
    r"(?:특별\s*)?할인\s*이벤트|쿠폰\s*(?:행사|이벤트)|시승기|먹거리\s*이야기|"
    r"\[미리보는\s+[^]]*신문\]|\[오늘의\s*주요일정[^]]*\])",
    re.IGNORECASE,
)


class QuerySetNewsCollector:
    """Configured queries are collected without depending on any desktop selection."""

    def __init__(self, client: NaverNewsClient | None, store: Any, *, queries: tuple[str, ...] = DEFAULT_NEWS_QUERY_SET,
                 enabled: bool = True, poll_seconds: int = 300, hard_limit: int = 24_000,
                 query_limit: int = 16_000,
                 processing_excluded_providers: tuple[str, ...] = (),
                 catalog_loader: Callable[[], tuple[tuple[str, str, str], ...]] = fetch_krx_stock_catalog,
                 catalog_retry_seconds: int = 3600) -> None:
        self._client, self._store = client, store
        self._queries = _queries(queries)
        self._enabled = bool(enabled)
        self._poll_seconds = max(60, int(poll_seconds))
        self._hard_limit = max(1, min(24_000, int(hard_limit)))
        self._query_limit = max(0, min(self._hard_limit, int(query_limit)))
        self._processing_excluded_providers = _provider_values(processing_excluded_providers)
        self._catalog_loader = catalog_loader
        self._catalog_retry_seconds = max(60, int(catalog_retry_seconds))
        self._catalog_retry_after = 0.0
        self._closing = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._credential_paused = False
        self._collecting: set[asyncio.Task] = set()

    async def pause_credentials(self):
        self._credential_paused = True
        if self._collecting:
            await asyncio.gather(*(asyncio.shield(t) for t in tuple(self._collecting)), return_exceptions=True)

    def replace_client(self, client):
        if not self._credential_paused:
            raise RuntimeError("NEWS_CREDENTIAL_DRAIN_REQUIRED")
        self._client = client

    def resume_credentials(self):
        self._credential_paused = False

    def update(self, *, enabled: bool, queries: tuple[str, ...], poll_seconds: int,
               processing_excluded_providers: tuple[str, ...] | None = None) -> None:
        self._enabled, self._queries = bool(enabled), _queries(queries)
        self._poll_seconds = max(60, int(poll_seconds))
        if processing_excluded_providers is not None:
            self._processing_excluded_providers = _provider_values(processing_excluded_providers)

    async def start(self) -> None:
        if self._task is None:
            self._closing.clear()
            self._task = asyncio.create_task(self._loop(), name="central-news-query-set")

    async def close(self) -> None:
        self._closing.set()
        self._credential_paused = True
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if self._collecting:
            await asyncio.gather(*(asyncio.shield(t) for t in tuple(self._collecting)), return_exceptions=True)

    async def run_once(self) -> int:
        if self._credential_paused or self._closing.is_set() or self._client is None or not self._enabled or not self._queries or self._query_limit <= 0:
            return 0
        completed = 0
        policy = (self._enabled, self._queries, self._poll_seconds)
        for query in policy[1]:
            if policy != (self._enabled, self._queries, self._poll_seconds):
                break
            source_id = _source_id(query)
            cursor = await asyncio.to_thread(self._store.load_news_source_cursor, source_id)
            scheduled = float((cursor or {}).get("next_schedule_at") or 0)
            if (cursor and scheduled > 0 and cursor.get("last_success") is not None
                    and int(cursor.get("next_start") or 1) == 1 and not cursor.get("error")):
                scheduled = float(cursor["last_success"]) + policy[2]
            if scheduled > time():
                continue
            if (self._credential_paused or self._closing.is_set() or self._client is None
                    or policy != (self._enabled, self._queries, self._poll_seconds)):
                break
            task = asyncio.create_task(self._run_query(query, source_id, cursor or {}, self._client))
            self._collecting.add(task)
            task.add_done_callback(self._collecting.discard)
            completed += int(await asyncio.shield(task))
        return completed

    async def _run_query(self, query, source_id, cursor, client):
        try:
            await self._collect_query(query, source_id, cursor, client)
            return True
        except Exception as error:
            await self._record_error(query, source_id, cursor, error)
            return False

    async def _collect_query(self, query: str, source_id: str, cursor: dict[str, Any], client) -> None:
        catalog = await asyncio.to_thread(self._load_catalog)
        start = max(1, min(1000, int(cursor.get("next_start") or 1)))
        old_marker = (str(cursor.get("cursor_published_at") or ""), str(cursor.get("cursor_identity") or ""))
        pending = (str(cursor.get("pending_published_at") or ""), str(cursor.get("pending_identity") or ""))
        run_id, checked_at = uuid.uuid4().hex, time()
        while start <= 1000:
            requests = 0

            def claim() -> bool:
                nonlocal requests
                accepted = self._store.claim_news_request(
                    "query_set", scope_limit=self._query_limit, hard_limit=self._hard_limit,
                    budget_date=datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat(),
                )
                requests += int(accepted)
                return accepted

            try:
                page = await asyncio.to_thread(
                    client.search_page, query, start=start, display=100, request_claim=claim,
                )
            except Exception as error:
                setattr(error, "news_request_count", requests)
                raise
            items = list(page.items)
            if not pending[1] and items:
                pending = _marker(items[0])
            reached = bool(old_marker[1]) and any(_marker(item) == old_marker for item in items)
            exhausted = page.display < 100 or start + page.display > page.total
            capped = start + max(page.display, 100) > 1000 and not reached and not exhausted
            completed = reached or exhausted or capped
            truncated = bool(capped)
            coverage = "query_set_truncated" if truncated else ("query_set_cursor_reached" if reached else "query_set")
            gap_seconds = _gap_seconds(items[-1] if items else None, old_marker) if truncated else 0.0
            next_start = 1 if completed else start + 100
            promoted = pending if completed else old_marker
            remaining = await asyncio.to_thread(self._budget_remaining)
            filter_stats: dict[str, int] = {}
            stored_items = await asyncio.to_thread(
                _page_items, items, catalog, self._processing_excluded_providers, filter_stats,
            )
            await asyncio.to_thread(self._store.save_news_source_page, {
                "source_id": source_id, "scope": "query_set", "query_text": query,
                "run_id": run_id, "page_start": start, "checked_at": checked_at,
                "completed_at": time(), "request_count": requests, "budget_remaining": remaining,
                "coverage": coverage, "truncated": truncated, "error": "",
                "cursor_published_at": promoted[0] or None, "cursor_identity": promoted[1],
                "pending_published_at": None if completed else (pending[0] or None),
                "pending_identity": "" if completed else pending[1], "next_start": next_start,
                "next_schedule_at": time() + self._poll_seconds if completed else time(),
                "last_success": time() if completed else cursor.get("last_success"),
                "document": {"total": page.total, "display": page.display, "gap_seconds": gap_seconds,
                             "missing_gap": truncated,
                             "raw_items": len(items), "stored_items": len(stored_items),
                             "skipped_title_only": filter_stats.get("title_only", 0),
                             "skipped_irrelevant": filter_stats.get("irrelevant", 0),
                             "scope_statement": "configured NAVER query set; not a complete market feed"},
                "items": stored_items,
            })
            if completed:
                return
            start = next_start

    async def _record_error(self, query: str, source_id: str, cursor: dict[str, Any], error: Exception) -> None:
        now = time()
        latest = await asyncio.to_thread(self._store.load_news_source_cursor, source_id)
        if latest:
            cursor = latest
        text = f"{type(error).__name__}: {error}"
        await asyncio.to_thread(self._store.save_news_source_page, {
            "source_id": source_id, "scope": "query_set", "query_text": query,
            "run_id": uuid.uuid4().hex, "page_start": int(cursor.get("next_start") or 1),
            "checked_at": now, "completed_at": time(),
            "request_count": int(getattr(error, "news_request_count", 0)),
            "budget_remaining": await asyncio.to_thread(self._budget_remaining),
            "coverage": "query_set_error", "truncated": False, "error": text,
            "cursor_published_at": cursor.get("cursor_published_at"),
            "cursor_identity": str(cursor.get("cursor_identity") or ""),
            "pending_published_at": cursor.get("pending_published_at"),
            "pending_identity": str(cursor.get("pending_identity") or ""),
            "next_start": int(cursor.get("next_start") or 1), "next_schedule_at": now + self._poll_seconds,
            "last_success": cursor.get("last_success"), "document": {"error_kind": _error_kind(error)}, "items": [],
        })

    def _load_catalog(self) -> tuple[tuple[str, str], ...]:
        rows = self._store.load_documents("stock_catalog", "krx", 10_000)
        result = []
        for row in rows:
            document = row.get("document")
            if isinstance(document, dict):
                code, name = str(document.get("code") or ""), str(document.get("name") or "").strip()
                if code and name:
                    result.append((code, name))
        if result:
            return tuple(result)
        now = time()
        if now < self._catalog_retry_after:
            return ()
        try:
            fetched = self._catalog_loader()
            if not fetched:
                raise ValueError("KRX 상장종목 목록이 비어 있습니다.")
            # 다운로드 중 TOP20 경로가 먼저 채웠다면 그 목록을 그대로 사용한다.
            refreshed = self._store.load_documents("stock_catalog", "krx", 10_000)
            refreshed_usable = []
            existing_by_code: dict[str, dict[str, Any]] = {}
            for row in refreshed:
                document = row.get("document")
                if not isinstance(document, dict):
                    continue
                code = str(document.get("code") or row.get("key") or "").strip()
                name = str(document.get("name") or "").strip()
                if code:
                    existing_by_code[code] = dict(document)
                if code and name:
                    refreshed_usable.append((code, name))
            if refreshed_usable:
                return tuple(refreshed_usable)
            as_of = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
            usable = [(code, name, market) for code, name, market in fetched if code and name]
            if not usable:
                raise ValueError("KRX 상장종목 목록에 유효한 종목이 없습니다.")
            documents = []
            for code, name, market in usable:
                document = existing_by_code.get(code, {})
                document.update({"code": code, "name": name, "as_of": as_of})
                if market or not document.get("market"):
                    document["market"] = market
                documents.append({"owner": "krx", "key": code, "document": document})
            documents.append({
                "owner": "krx", "key": "_meta",
                "document": {"as_of": as_of, "rows": len(usable)},
            })
            self._store.upsert_documents("stock_catalog", documents)
            self._catalog_retry_after = 0.0
            return tuple((code, name) for code, name, _market in usable)
        except Exception as error:
            self._catalog_retry_after = now + self._catalog_retry_seconds
            LOGGER.warning("뉴스 종목 연결용 KRX 카탈로그 초기화 실패(미확정 유지): %s", error)
            return ()

    def _budget_remaining(self) -> int:
        budget_date = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
        used = self._store.news_request_count(budget_date)
        return max(0, self._hard_limit - used)

    async def _loop(self) -> None:
        while not self._closing.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("query_set 뉴스 수집 주기에 실패했습니다.")
            try:
                await asyncio.wait_for(self._closing.wait(), timeout=min(60, self._poll_seconds))
            except TimeoutError:
                pass


def _queries(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))[:50]


def _provider_values(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))[:100]


def _source_id(query: str) -> str:
    return "naver-query:" + hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]


def _identity(item: StockNewsItem) -> str:
    return item.original_link or item.link or f"{item.published_at!s}|{item.title}"


def _marker(item: StockNewsItem) -> tuple[str, str]:
    return (item.published_at.astimezone(UTC).isoformat() if item.published_at else "", _identity(item))


def _article_document(item: StockNewsItem) -> dict[str, Any]:
    return {"title": item.title, "description": item.description, "link": item.link,
            "original_link": item.original_link,
            "publisher_domain": news_provider_domain(item),
            "publisher_name": news_provider(item),
            "published_at": item.published_at.astimezone(UTC).isoformat() if item.published_at else None}


def _targets(item: StockNewsItem, catalog: tuple[tuple[str, str], ...]) -> list[dict[str, Any]]:
    text = f"{item.title} {item.description}"
    by_name: dict[str, list[str]] = {}
    for code, name in catalog:
        if _exact_company_name(name, text):
            by_name.setdefault(name, []).append(code)
    if not by_name:
        return [{"stock_code": None, "stock_name": None, "relation_status": "unresolved",
                 "evidence_text": "", "rule_version": TARGET_RULE_VERSION,
                 "reason": "no exact KRX company-name match"}]
    result = []
    for name, codes in sorted(by_name.items()):
        unique_codes = sorted(set(codes))
        if len(unique_codes) == 1:
            result.append({"stock_code": unique_codes[0], "stock_name": name,
                           "relation_status": "confirmed", "evidence_text": name,
                           "rule_version": TARGET_RULE_VERSION, "match": "exact_company_name"})
        else:
            result.append({"stock_code": None, "stock_name": name, "relation_status": "ambiguous",
                           "evidence_text": name, "rule_version": TARGET_RULE_VERSION,
                           "candidate_codes": unique_codes, "reason": "same KRX company name has multiple codes"})
    return result


def _page_items(
    items: list[StockNewsItem], catalog: tuple[tuple[str, str], ...],
    processing_excluded_providers: tuple[str, ...] = (),
    filter_stats: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    result = []
    for item in items:
        document = _article_document(item)
        targets = _targets(item, catalog)
        skip_reason = _storage_skip_reason(item, targets)
        if skip_reason:
            if filter_stats is not None:
                filter_stats[skip_reason] = filter_stats.get(skip_reason, 0) + 1
            continue
        result.append({
            "identity": _identity(item), "document": document,
            "targets": targets,
            "processing_excluded": is_excluded_provider(
                str(document["publisher_domain"]), str(document["publisher_name"]),
                processing_excluded_providers,
            ),
        })
    return result


def _storage_skip_reason(item: StockNewsItem, targets: list[dict[str, Any]]) -> str:
    """본문 수집 전에 확실히 판별 가능한 저장 제외 사유만 반환한다."""
    title = " ".join(str(item.title or "").split())
    description = " ".join(str(item.description or "").split())
    if not title or not description:
        return "title_only"
    if _NON_INVESTMENT_NEWS.search(title):
        return "irrelevant"
    if any(str(target.get("relation_status") or "") in {"confirmed", "ambiguous"} for target in targets):
        return ""
    text = f"{title} {description}"
    if _MARKET_NEWS_SIGNAL.search(text) or _CORPORATE_EVENT_SIGNAL.search(text):
        return ""
    return "irrelevant"


def _exact_company_name(name: str, text: str) -> bool:
    # 한글의 부분 문자열(예: 상장사 "동일" vs. "동일한")을 회사명 일치로 확정하지 않는다.
    if name not in text:
        return False
    return _company_name_pattern(name).search(text) is not None


@lru_cache(maxsize=10_000)
def _company_name_pattern(name: str) -> re.Pattern[str]:
    before = r"(?<![0-9A-Za-z가-힣])"
    after = r"(?=$|[\s,.;:!?()\[\]{}'\"·/]|은|는|이|가|의|과|와|을|를|에|도|로)"
    return re.compile(before + re.escape(name) + after)


def _gap_seconds(oldest: StockNewsItem | None, old_marker: tuple[str, str]) -> float:
    if oldest is None or oldest.published_at is None or not old_marker[0]:
        return 0.0
    try:
        old = datetime.fromisoformat(old_marker[0]).astimezone(UTC)
    except ValueError:
        return 0.0
    return max(0.0, (oldest.published_at.astimezone(UTC) - old).total_seconds())


def _error_kind(error: Exception) -> str:
    code = getattr(error, "code", None)
    if code in {401, 403}:
        return "auth"
    if code == 429:
        return "rate_limit"
    if "예산" in str(error):
        return "budget"
    return "provider"
