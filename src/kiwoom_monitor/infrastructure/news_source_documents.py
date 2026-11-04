"""Pure source article documents and exact-company target mapping.

Collectors own HTTP, budgets, cursors and task lifetime. This module only maps
explicit news and catalog inputs; the company-pattern cache is the existing LRU.
"""
from __future__ import annotations

from datetime import UTC
from functools import lru_cache
import re
from typing import Any

from kiwoom_monitor.infrastructure.naver_news import (
    StockNewsItem, is_excluded_provider, news_provider, news_provider_domain,
)

TARGET_RULE_VERSION = "exact-krx-company-name-v1"

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


def article_identity(item: StockNewsItem) -> str:
    return item.original_link or item.link or f"{item.published_at!s}|{item.title}"


def article_marker(item: StockNewsItem) -> tuple[str, str]:
    return (item.published_at.astimezone(UTC).isoformat() if item.published_at else "", article_identity(item))


def article_document(item: StockNewsItem) -> dict[str, Any]:
    return {"title": item.title, "description": item.description, "link": item.link,
            "original_link": item.original_link,
            "publisher_domain": news_provider_domain(item),
            "publisher_name": news_provider(item),
            "published_at": item.published_at.astimezone(UTC).isoformat() if item.published_at else None}


def article_targets(item: StockNewsItem, catalog: tuple[tuple[str, str], ...]) -> list[dict[str, Any]]:
    text = f"{item.title} {item.description}"
    by_name: dict[str, list[str]] = {}
    for code, name in catalog:
        if exact_company_name(name, text):
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


def page_items(
    items: list[StockNewsItem], catalog: tuple[tuple[str, str], ...],
    processing_excluded_providers: tuple[str, ...] = (),
    filter_stats: dict[str, int] | None = None,
    *, retain_market_articles: bool = False,
) -> list[dict[str, Any]]:
    result = []
    for item in items:
        if retain_market_articles and (not str(item.title or "").strip()
                                       or not str(item.link or "").strip()
                                       or item.published_at is None):
            if filter_stats is not None:
                filter_stats["invalid_market_article"] = filter_stats.get("invalid_market_article", 0) + 1
            continue
        document = article_document(item)
        targets = article_targets(item, catalog)
        skip_reason = "" if retain_market_articles else storage_skip_reason(item, targets)
        if skip_reason:
            if filter_stats is not None:
                filter_stats[skip_reason] = filter_stats.get(skip_reason, 0) + 1
            continue
        result.append({
            "identity": article_identity(item), "document": document,
            "targets": targets,
            "processing_excluded": is_excluded_provider(
                str(document["publisher_domain"]), str(document["publisher_name"]),
                processing_excluded_providers,
            ),
        })
    return result


def storage_skip_reason(item: StockNewsItem, targets: list[dict[str, Any]]) -> str:
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


def exact_company_name(name: str, text: str) -> bool:
    # 한글의 부분 문자열(예: 상장사 "동일" vs. "동일한")을 회사명 일치로 확정하지 않는다.
    if name not in text:
        return False
    return company_name_pattern(name).search(text) is not None


@lru_cache(maxsize=10_000)
def company_name_pattern(name: str) -> re.Pattern[str]:
    before = r"(?<![0-9A-Za-z가-힣])"
    after = r"(?=$|[\s,.;:!?()\[\]{}'\"·/]|은|는|이|가|의|과|와|을|를|에|도|로)"
    return re.compile(before + re.escape(name) + after)
