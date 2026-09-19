"""불변 기사/본문 근거에서 공급계약 사건 후보를 만드는 기록 전용 규칙."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping

from kiwoom_monitor.application.news_analysis import NewsAssessment, is_price_reaction_news
from kiwoom_monitor.application.news_grouping import group_similar_news, is_market_reaction_article
from kiwoom_monitor.infrastructure.naver_news import StockNewsItem


SUPPLY_CONTRACT_RULE_VERSION = "supply-contract-rule-v2"
SUPPLY_CONTRACT_SCORE_VERSION = "supply-contract-score-v1"

_SUPPLY = re.compile(r"공급\s*(?:계약|계약서|합의|논의|협의|검토|예정)|수주(?!\s*잔고)|납품|MOU|업무협약", re.I)
_CONFIRMED = re.compile(r"(?:공급\s*)?계약\s*(?:체결|완료|확정|공시)|수주(?:했다|\s*공시)?|납품\s*계약", re.I)
_POTENTIAL = re.compile(r"MOU|업무협약|논의|협의|검토|추진|기대|가능성|예정|협상", re.I)
_TERMINATED = re.compile(r"해지|취소|철회|파기|중단", re.I)
_DENIED = re.compile(r"부인|사실무근|확정된\s*바\s*없|계약한\s*바\s*없|아니라고", re.I)
_REPUBLISHED = re.compile(r"재탕|재조명|지난해|작년|과거|당시|\d+\s*(?:년|개월)\s*전")
_CONDITIONAL = re.compile(r"최대|약\s*\d|조건부|예정|추정|한도|옵션")
_REACTION = re.compile(r"특징주|급등|급락|강세|약세|상한가|하한가|주가\s*(?:상승|하락)")
_MONEY = re.compile(r"(?P<number>\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<unit>조|억|만)?\s*(?P<currency>원|달러|USD)", re.I)
_COUNTERPARTY = re.compile(
    r"(?P<name>[A-Za-z가-힣][A-Za-z0-9가-힣&.\- ]{1,28}?)(?:와|과|에|向)\s*"
    r"(?:\d[\d,.]*(?:조|억|만)?\s*(?:원|달러|USD)\s*)?(?:규모\s*)?(?:공급|납품|수주|MOU|업무협약)",
    re.I,
)


@dataclass(frozen=True)
class EvidenceSpan:
    field: str
    start: int
    end: int
    text: str
    fact: str


@dataclass(frozen=True)
class SupplyContractRuleResult:
    role: str
    scope: str
    event_type: str
    certainty: str
    novelty: str
    evidence_spans: tuple[EvidenceSpan, ...]
    amount_won: int | None
    revenue_ratio: float | None
    amount_text: str | None
    currency: str | None
    conditional_amount: bool
    counterparty: str | None
    targets: tuple[dict[str, str], ...]
    importance_score: int
    confidence_score: int
    novelty_score: int
    score_components: dict[str, int]
    score_formula_version: str
    rule_version: str
    ai_required: bool
    ai_reason: tuple[str, ...]
    title_body_conflict: bool
    event_key: str | None

    def as_document(self) -> dict[str, Any]:
        return asdict(self)


def classify_supply_contract(
    article: Mapping[str, Any], body: str = "", *, prior_event: bool = False,
) -> SupplyContractRuleResult | None:
    """SUPPLY_CONTRACT 문맥만 판정한다. ``None``은 이 규칙의 대상이 아님을 뜻한다."""
    title = str(article.get("title") or "")
    description = str(article.get("description") or "")
    body = str(body or "")
    combined = ". ".join(part for part in (title, description, body) if part)
    if not _SUPPLY.search(combined):
        return None
    if (
        is_price_reaction_news(str(article.get("stock_name") or ""), title, description, body)
        and not _SUPPLY.search(f"{title}. {description}")
    ):
        # 가격 중계 기사 본문 뒤쪽의 과거 계약·수주잔고를 새 사건으로 만들지 않는다.
        return None

    evidence: list[EvidenceSpan] = []
    for field, text in (("title", title), ("description", description), ("body", body)):
        for fact, pattern in (
            ("supply_contract", _SUPPLY), ("confirmed", _CONFIRMED),
            ("potential", _POTENTIAL), ("terminated", _TERMINATED), ("denied", _DENIED),
            ("market_reaction", _REACTION),
        ):
            match = pattern.search(text)
            if match:
                evidence.append(EvidenceSpan(field, match.start(), match.end(), match.group(0), fact))

    title_certainty = _certainty(title)
    body_certainty = _certainty(" ".join((description, body)))
    conflict = (
        title_certainty not in {"UNKNOWN", body_certainty}
        and body_certainty != "UNKNOWN"
    )
    certainty = "UNKNOWN" if conflict else _certainty(combined)
    money = _contract_money(combined)
    amount_text = money.group(0) if money else None
    currency = money.group("currency").upper() if money else None
    conditional = bool(money and _CONDITIONAL.search(combined[max(0, money.start() - 12):money.end() + 12]))
    amount_won = _amount_won(money, conditional)
    if money:
        field, offset = _locate_match(title, description, body, money.group(0))
        evidence.append(EvidenceSpan(field, offset, offset + len(money.group(0)), money.group(0), "amount"))

    counterpart_match = _COUNTERPARTY.search(combined)
    counterparty = _clean_counterparty(counterpart_match.group("name")) if counterpart_match else None
    if counterpart_match and counterparty:
        field, offset = _locate_match(title, description, body, counterparty)
        evidence.append(EvidenceSpan(field, offset, offset + len(counterparty), counterparty, "counterparty"))

    reaction = bool(_REACTION.search(title))
    role = "REACTION" if reaction else ("FACT" if certainty != "UNKNOWN" else "UNKNOWN")
    stock_code = str(article.get("stock_code") or "")
    stock_name = str(article.get("stock_name") or "")
    direct = bool(stock_code and stock_name and stock_name.casefold() in combined.casefold())
    scope = "TARGET_COMPANY" if direct else "UNKNOWN"
    direction = "NEGATIVE" if certainty in {"TERMINATED", "DENIED"} else (
        "POSITIVE" if certainty == "CONFIRMED" else "UNKNOWN"
    )
    targets = ({"target_id": stock_code, "direction": direction,
                "directness": "DIRECT" if direct else "UNKNOWN"},) if stock_code else ()
    novelty = "REPUBLICATION" if _REPUBLISHED.search(combined) else ("UPDATE" if prior_event else "NEW")

    components = {
        "confirmed_fact": 35 if certainty == "CONFIRMED" else 0,
        "status_fact": 30 if certainty in {"TERMINATED", "DENIED"} else 0,
        "counterparty": 15 if counterparty else 0,
        "amount": 15 if amount_won is not None else 0,
        "direct_target": 15 if direct else 0,
        "conflict_penalty": -45 if conflict else 0,
        "uncertain_penalty": -20 if certainty in {"POTENTIAL", "UNKNOWN"} else 0,
    }
    confidence = max(0, min(100, 30 + sum(components.values())))
    importance = 20
    if certainty in {"CONFIRMED", "TERMINATED"}:
        importance += 35
    if amount_won is not None:
        importance += 25 if amount_won >= 100_000_000_000 else 15
    if direct:
        importance += 10
    novelty_score = {"NEW": 80, "UPDATE": 60, "REPUBLICATION": 10}.get(novelty, 30)
    reasons: list[str] = []
    if certainty in {"POTENTIAL", "UNKNOWN", "DENIED"}:
        reasons.append(f"certainty:{certainty.lower()}")
    if conflict:
        reasons.append("title_body_conflict")
    if conditional:
        reasons.append("conditional_amount")
    if money and amount_won is None:
        reasons.append("amount_not_safely_convertible")
    if not counterparty:
        reasons.append("counterparty_unknown")
    if not direct:
        reasons.append("target_scope_unknown")
    event_key = _event_key(stock_code, counterparty, amount_won, amount_text)
    return SupplyContractRuleResult(
        role=role, scope=scope, event_type="SUPPLY_CONTRACT", certainty=certainty,
        novelty=novelty, evidence_spans=tuple(evidence), amount_won=amount_won,
        revenue_ratio=None, amount_text=amount_text, currency=currency,
        conditional_amount=conditional, counterparty=counterparty, targets=targets,
        importance_score=min(100, importance), confidence_score=confidence,
        novelty_score=novelty_score, score_components=components,
        score_formula_version=SUPPLY_CONTRACT_SCORE_VERSION,
        rule_version=SUPPLY_CONTRACT_RULE_VERSION, ai_required=bool(reasons),
        ai_reason=tuple(reasons), title_body_conflict=conflict, event_key=event_key,
    )


def grouped_candidate_identities(
    current: Mapping[str, Any], recent: Iterable[Mapping[str, Any]],
) -> tuple[str, ...]:
    """기존 grouping을 보수적인 possible-related 후보 계산에만 재사용한다."""
    documents = [current, *recent]
    items = tuple(_as_item(document) for document in documents)
    if not items:
        return ()
    identities = {id(item): str(document.get("identity") or _item_identity(item))
                  for item, document in zip(items, documents, strict=True)}
    current_item = items[0]
    for group in group_similar_news(items):
        if any(item is current_item for item in group.items):
            return tuple(identities[id(item)] for item in group.items if item is not current_item)
    return ()


def rule_input_hash(article_revision_id: str, body_revision_id: str, result: SupplyContractRuleResult) -> str:
    encoded = json.dumps(
        result.as_document(), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    value = "\0".join((article_revision_id, body_revision_id, result.rule_version,
                        hashlib.sha256(encoded).hexdigest()))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _certainty(text: str) -> str:
    # 부인/해지 문구는 같은 문장에 체결이라는 과거 설명이 있어도 현재 상태를 우선한다.
    if _DENIED.search(text):
        return "DENIED"
    if _TERMINATED.search(text):
        return "TERMINATED"
    if _CONFIRMED.search(text):
        return "CONFIRMED"
    if _POTENTIAL.search(text):
        return "POTENTIAL"
    return "UNKNOWN"


def _amount_won(match: re.Match[str] | None, conditional: bool) -> int | None:
    if match is None or conditional or match.group("currency").casefold() not in {"원"}:
        return None
    number = float(match.group("number").replace(",", ""))
    multiplier = {"조": 1_000_000_000_000, "억": 100_000_000, "만": 10_000, None: 1}[match.group("unit")]
    return int(number * multiplier)


def _contract_money(text: str) -> re.Match[str] | None:
    """공급·수주 표현과 같은 문맥에 있는 금액만 계약금액 후보로 선택한다."""
    candidates: list[tuple[int, int, re.Match[str]]] = []
    for money in _MONEY.finditer(text):
        left = max(0, money.start() - 90)
        right = min(len(text), money.end() + 90)
        context = text[left:right]
        supply_matches = []
        for supply in _SUPPLY.finditer(context):
            supply_start, supply_end = left + supply.start(), left + supply.end()
            between = text[min(money.end(), supply_end):max(money.start(), supply_start)]
            if not re.search(r"[.!?…\n]", between):
                supply_matches.append(supply)
        if not supply_matches:
            continue
        absolute_money = money.start()
        distance = min(
            abs(absolute_money - (left + supply.start()))
            for supply in supply_matches
        )
        candidates.append((distance, money.start(), money))
    return min(candidates, key=lambda value: (value[0], value[1]))[2] if candidates else None


def _clean_counterparty(value: str) -> str | None:
    cleaned = re.sub(r"^(?:계약상대는|상대방은|고객사인)\s*", "", value).strip(" ,·")
    # 앞 문장 전체가 잡히는 것을 피하고 마지막 공백 토큰을 사용한다.
    if " " in cleaned:
        cleaned = cleaned.split()[-1]
    return cleaned or None


def _event_key(stock_code: str, counterparty: str | None, amount_won: int | None,
               amount_text: str | None) -> str | None:
    if not stock_code or not counterparty or not (amount_won is not None or amount_text):
        return None
    amount = str(amount_won) if amount_won is not None else re.sub(r"\s+", "", amount_text or "").casefold()
    raw = "\0".join((stock_code, counterparty.casefold(), amount))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _locate_match(title: str, description: str, body: str, value: str) -> tuple[str, int]:
    for field, text in (("title", title), ("description", description), ("body", body)):
        offset = text.find(value)
        if offset >= 0:
            return field, offset
    return "body", 0


def _as_item(document: Mapping[str, Any]) -> StockNewsItem:
    published = None
    try:
        published = datetime.fromisoformat(str(document.get("published_at") or ""))
    except ValueError:
        pass
    return StockNewsItem(
        str(document.get("title") or ""), str(document.get("description") or ""),
        str(document.get("link") or ""), str(document.get("original_link") or ""), published,
        NewsAssessment(
            bool(document.get("relevant", 0)), str(document.get("category") or ""),
            str(document.get("outlook") or ""), str(document.get("reason") or ""),
            int(document.get("relevance_score") or 0), int(document.get("outlook_score") or 0),
        ),
    )


def _item_identity(item: StockNewsItem) -> str:
    return str(item.original_link or item.link or f"{item.published_at!s}|{item.title}")
