"""최근 주식 뉴스를 동일 사건 단위로 묶고 대표 기사를 고른다."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from kiwoom_monitor.infrastructure.naver_news import StockNewsItem, news_provider


@dataclass(frozen=True)
class NewsEventGroup:
    representative: StockNewsItem
    items: tuple[StockNewsItem, ...]
    stage: str = ""
    past_event_republication: bool = False


_REACTION_PATTERN = re.compile(
    r"(?:\[(?:특징주|종목현미경|주식 초고수는 지금)[^]]*\]|"
    r"\b(?:특징주|급등|급락|강세|약세|상한가|하한가|상승세|하락세)\b)",
    re.IGNORECASE,
)
_TRACKING_QUERY_KEYS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid"}
_MIN_TIMESTAMP = float("-inf")


def group_similar_news(
    items: tuple[StockNewsItem, ...], *, window: timedelta = timedelta(hours=48),
) -> tuple[NewsEventGroup, ...]:
    """완전 중복을 제거하고 최근 48시간의 유사 기사를 사건별로 묶는다."""
    unique = _deduplicate(items)
    ordered = sorted(unique, key=_published_timestamp, reverse=True)
    # 같은 기사에 대한 정규화·숫자 추출이 군집 비교 안쪽에서 수천 번
    # 반복되면 200건 기준 수 초가 걸린다. 기사별 특징은 처음 한 번만 만든다.
    features = {id(item): _event_features(item) for item in ordered}
    clusters: list[list[StockNewsItem]] = []
    for item in ordered:
        item_features = features[id(item)]
        target = next(
            (
                cluster for cluster in clusters
                if _within_window_features(item_features, cluster, features, window)
                and all(
                    not _anchors_conflict(item_features.anchors, features[id(existing)].anchors)
                    for existing in cluster
                )
                and any(_same_event_features(item_features, features[id(existing)]) for existing in cluster)
            ),
            None,
        )
        if target is None:
            clusters.append([item])
        else:
            target.append(item)

    groups = []
    for cluster in clusters:
        representative = max(cluster, key=_representative_score)
        related = sorted(
            (item for item in cluster if item is not representative),
            key=_published_timestamp,
            reverse=True,
        )
        group_items = (representative, *related)
        groups.append(NewsEventGroup(
            representative, group_items, _event_stage(representative),
            any(_is_past_event_republication(item) for item in group_items),
        ))
    return tuple(sorted(groups, key=lambda group: max(map(_published_timestamp, group.items)), reverse=True))


@dataclass(frozen=True)
class _EventFeatures:
    title: str
    combined: str
    title_ngrams: frozenset[str]
    anchors: dict[str, frozenset[str]]
    category: str
    timestamp: float


def _event_features(item: StockNewsItem) -> _EventFeatures:
    title = _normalize(item.title)
    return _EventFeatures(
        title,
        _normalize(f"{item.title} {item.description}"),
        frozenset(_ngrams(title)),
        _event_anchors(item),
        item.assessment.category,
        item.published_at.astimezone(UTC).timestamp() if item.published_at else _MIN_TIMESTAMP,
    )


def _same_event_features(left: _EventFeatures, right: _EventFeatures) -> bool:
    if _anchors_conflict(left.anchors, right.anchors):
        return False
    categories_compatible = left.category == right.category or "주가·수급" in {left.category, right.category}
    if not categories_compatible:
        return False
    title_ngrams = _set_similarity(left.title_ngrams, right.title_ngrams)
    if title_ngrams >= 0.48:
        return True
    # 공통 3글자조차 없으면 긴 한국어 뉴스 제목이 동일 사건일 가능성이
    # 없으므로 비용이 큰 SequenceMatcher 두 번을 실행하지 않는다.
    if not (left.title_ngrams & right.title_ngrams):
        return False
    title_ratio = SequenceMatcher(None, left.title, right.title).ratio()
    if title_ratio >= 0.60:
        return True
    return title_ratio >= 0.48 and SequenceMatcher(None, left.combined, right.combined).ratio() >= 0.52


def _within_window_features(
    item: _EventFeatures, cluster: list[StockNewsItem],
    features: dict[int, _EventFeatures], window: timedelta,
) -> bool:
    if item.timestamp == _MIN_TIMESTAMP:
        return False
    seconds = window.total_seconds()
    return any(
        features[id(existing)].timestamp != _MIN_TIMESTAMP
        and abs(item.timestamp - features[id(existing)].timestamp) <= seconds
        for existing in cluster
    )


def is_market_reaction_article(item: StockNewsItem) -> bool:
    """새 사실보다 주가 반응을 중심으로 다시 쓴 제목인지 판별한다."""
    title = item.title.casefold()
    return bool(_REACTION_PATTERN.search(title)) or (
        item.assessment.category == "주가·수급"
        and any(term in title for term in ("주가", "급등", "급락", "강세", "약세", "상한가", "하한가"))
    )


def _deduplicate(items: tuple[StockNewsItem, ...]) -> tuple[StockNewsItem, ...]:
    result: list[StockNewsItem] = []
    seen: set[str] = set()
    for item in items:
        url = _canonical_url(item.original_link or item.link)
        identity = url or f"{_normalize(item.title)}|{item.published_at!s}"
        if identity in seen:
            continue
        seen.add(identity)
        result.append(item)
    return tuple(result)


def _canonical_url(url: str) -> str:
    if not url.strip():
        return ""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    query = urlencode([
        (key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.casefold() not in _TRACKING_QUERY_KEYS
    ])
    return urlunsplit((parts.scheme.casefold(), parts.netloc.casefold().removeprefix("www."), parts.path.rstrip("/"), query, ""))


def _same_event(left: StockNewsItem, right: StockNewsItem) -> bool:
    if _anchors_conflict(_event_anchors(left), _event_anchors(right)):
        return False
    left_title = _normalize(left.title)
    right_title = _normalize(right.title)
    title_ratio = SequenceMatcher(None, left_title, right_title).ratio()
    title_ngrams = _ngram_similarity(left_title, right_title)
    combined_ratio = SequenceMatcher(
        None,
        _normalize(f"{left.title} {left.description}"),
        _normalize(f"{right.title} {right.description}"),
    ).ratio()
    categories_compatible = (
        left.assessment.category == right.assessment.category
        or "주가·수급" in {left.assessment.category, right.assessment.category}
    )
    return categories_compatible and (
        title_ratio >= 0.60
        or title_ngrams >= 0.48
        or (title_ratio >= 0.48 and combined_ratio >= 0.52)
    )


def _normalize(value: str) -> str:
    value = _REACTION_PATTERN.sub(" ", value.casefold())
    value = re.sub(r"(?:기대감에|소식에|영향으로|관련주)[…\.·\s]*(?:상승|하락)?", " ", value)
    return re.sub(r"[^0-9a-z가-힣]+", "", value)


def _ngrams(value: str, size: int = 3) -> set[str]:
    return {value[index:index + size] for index in range(max(0, len(value) - size + 1))}


def _ngram_similarity(left: str, right: str) -> float:
    left_values, right_values = _ngrams(left), _ngrams(right)
    if not left_values or not right_values:
        return 0.0
    return len(left_values & right_values) / len(left_values | right_values)


def _set_similarity(left: frozenset[str], right: frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _event_anchors(item: StockNewsItem) -> dict[str, frozenset[str]]:
    text = f"{item.title} {item.description}".replace(",", "")
    patterns = {
        "money": r"\d+(?:\.\d+)?\s*(?:조|억|만)?\s*원",
        "percent": r"\d+(?:\.\d+)?\s*%",
        "shares": r"\d+(?:\.\d+)?\s*(?:만|억)?\s*주",
        "clinical": r"(?:임상\s*)?[123]\s*상(?:a|b)?",
    }
    anchors = {
        kind: frozenset(re.sub(r"\s+", "", value.casefold()) for value in re.findall(pattern, text, re.IGNORECASE))
        for kind, pattern in patterns.items()
    }
    # `삼성전자와`, `LG에너지솔루션과`처럼 계약 상대가 명시되면 서로
    # 다른 후속 공시를 같은 사건으로 합치지 않는다.
    anchors["counterparty"] = frozenset(
        value.casefold()
        for value in re.findall(r"([A-Za-z가-힣][A-Za-z0-9가-힣&.\-]{1,24})(?:와|과)(?=\s|,|·)", text)
    )
    stage = _event_stage(item)
    anchors["stage"] = frozenset((stage,)) if stage else frozenset()
    if is_market_reaction_article(item):
        # 주가 반응률은 사건의 핵심 수치가 아니며 시점마다 달라진다.
        anchors["percent"] = frozenset()
    return anchors


def _anchors_conflict(left: dict[str, frozenset[str]], right: dict[str, frozenset[str]]) -> bool:
    return any(left[kind] and right[kind] and left[kind].isdisjoint(right[kind]) for kind in left)


def _within_window(item: StockNewsItem, cluster: list[StockNewsItem], window: timedelta) -> bool:
    if item.published_at is None:
        return False
    current = item.published_at.astimezone(UTC)
    return any(
        existing.published_at is not None
        and abs(current - existing.published_at.astimezone(UTC)) <= window
        for existing in cluster
    )


def _published_timestamp(item: StockNewsItem) -> float:
    value = item.published_at or datetime.min.replace(tzinfo=UTC)
    return value.astimezone(UTC).timestamp()


def _representative_score(item: StockNewsItem) -> tuple[int, int, int, int, int, float]:
    anchors = sum(len(values) for values in _event_anchors(item).values())
    return (
        0 if is_market_reaction_article(item) else 1,
        _original_source_score(item),
        anchors,
        1 if item.original_link else 0,
        min(500, len(item.description)),
        _published_timestamp(item),
    )


def _original_source_score(item: StockNewsItem) -> int:
    provider = news_provider(item)
    text = f"{item.title} {item.description}"
    if provider == "DART 공시":
        return 3
    if re.search(r"공식\s*(?:발표|공시)|회사\s*(?:발표|측)|보도자료", text):
        return 2
    return 1


def _event_stage(item: StockNewsItem) -> str:
    text = f"{item.title} {item.description}"
    stages = (
        ("변경·취소", r"변경|정정|해지|취소|철회"),
        ("체결", r"체결|계약\s*완료"),
        ("결정", r"결정|승인|확정"),
        ("검토", r"검토|추진|협의|가능성"),
    )
    return next((label for label, pattern in stages if re.search(pattern, text)), "")


def _is_past_event_republication(item: StockNewsItem) -> bool:
    text = f"{item.title} {item.description}"
    return bool(re.search(r"지난해|작년|과거|당시|재조명|재탕|\d+\s*(?:년|개월)\s*전", text))
