from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class NewsAssessment:
    relevant: bool
    category: str
    outlook: str
    reason: str
    relevance_score: int
    outlook_score: int


SECURITIES_TERMS = {
    "주가": 2, "증시": 2, "코스피": 2, "코스닥": 2, "상한가": 3,
    "하한가": 3, "급등": 2, "급락": 2, "거래량": 2, "공시": 3,
    "실적": 3, "매출": 2, "영업이익": 3, "순이익": 2, "적자": 2,
    "흑자": 2, "수주": 3, "공급계약": 3, "계약": 1, "투자": 2,
    "인수": 2, "합병": 2, "증자": 3, "자본감소": 3, "무상감자": 3, "유상감자": 3, "감자 결정": 3, "전환사채": 3,
    "자사주": 3, "배당": 3, "임상": 2, "허가": 2, "특허": 2,
    "최대주주": 3, "목표주가": 3, "투자의견": 3, "증권사": 2,
    "상장": 2, "거래정지": 3, "기업가치": 2, "시가총액": 2,
}

CATEGORY_TERMS = (
    ("실적·전망", ("실적", "매출", "영업이익", "순이익", "흑자", "적자", "전망")),
    ("수주·계약", ("수주", "공급계약", "납품", "계약 체결")),
    ("투자·인수합병", ("투자", "인수", "합병", "m&a", "지분 취득")),
    ("자본·주주환원", ("유상증자", "무상증자", "자본감소", "무상감자", "유상감자", "감자 결정", "전환사채", "자사주", "배당")),
    ("임상·허가", ("임상", "허가", "승인", "특허", "신약")),
    ("경영권·주주", ("최대주주", "경영권", "대표이사", "주주총회")),
    ("주가·수급", ("주가", "상한가", "하한가", "급등", "급락", "거래량", "수급")),
    ("공시·규제", ("공시", "거래정지", "불성실공시", "제재", "조사", "규제")),
)

POSITIVE_TERMS = {
    "수주": 3, "공급계약": 3, "계약 체결": 2, "흑자전환": 4, "흑자 전환": 4,
    "사상 최대": 3, "최대 실적": 3, "급증": 2, "증가": 1, "성장": 1,
    "상향": 2, "승인": 3, "허가": 2, "특허": 2, "임상 성공": 4,
    "자사주 취득": 3, "자사주 소각": 4, "배당 확대": 3, "신고가": 2,
    "상한가": 2, "턴어라운드": 3, "국책과제": 2, "반등 흐름": 3,
    "반등세": 3, "회복세": 3, "상승 전환": 3,
}

NEGATIVE_TERMS = {
    "적자전환": 4, "적자 전환": 4, "영업손실": 3, "순손실": 3,
    "급감": 2, "감소": 1, "하향": 2, "유상증자": 3, "자본감소": 3,
    "무상감자": 3, "유상감자": 3, "감자 결정": 3,
    "전환사채": 1, "거래정지": 4, "상장폐지": 5, "횡령": 5, "배임": 5,
    "압수수색": 4, "소송": 2, "제재": 3, "리콜": 3, "임상 실패": 5,
    "허가 취소": 4, "계약 해지": 4, "수주 취소": 4, "하한가": 3,
}


def _plain(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", value)).strip().casefold()


def _direction_terms(text: str) -> tuple[list[str], list[str]]:
    positive = [term for term in POSITIVE_TERMS if term in text]
    recovery_context = bool(re.search(
        r"(?:급감|급락|감소)(?:했던|했으나|한|세| 이후| 후)?[^.!?]{0,45}(?:반등|회복|상승 전환)", text
    ))
    negative = [
        term for term in NEGATIVE_TERMS
        if term in text and not (recovery_context and term in {"급감", "감소"})
    ]
    if recovery_context and not any(term in positive for term in ("반등 흐름", "반등세", "회복세", "상승 전환")):
        positive.append("과거 하락 뒤 반등")
    return positive, negative


def assess_stock_news(stock_name: str, title: str, description: str) -> NewsAssessment:
    """보수적인 규칙으로 증권 관련성과 호재·악재 *가능성*을 판정한다."""
    name = re.sub(r"\s+", "", stock_name).casefold()
    title_text = _plain(title)
    body_text = _plain(description)
    combined = f"{title_text} {body_text}"
    compact = re.sub(r"\s+", "", combined)
    direct = bool(name and name in compact)
    relevance_score = (4 if direct else 0) + sum(weight for term, weight in SECURITIES_TERMS.items() if term in combined)
    relevant = direct and relevance_score >= 5

    category = "기타 증권뉴스"
    # 제목의 핵심 사건을 요약문에 섞인 부가 실적 표현보다 우선한다.
    for source in (title_text, combined):
        matched = next((label for label, terms in CATEGORY_TERMS if any(term in source for term in terms)), "")
        if matched:
            category = matched
            break

    matched_positive, matched_negative = _direction_terms(combined)
    positive = sum(POSITIVE_TERMS.get(term, 3) for term in matched_positive)
    negative = sum(NEGATIVE_TERMS[term] for term in matched_negative)
    score = positive - negative
    if not relevant:
        return NewsAssessment(False, category, "관련성 낮음", "종목 직접 언급과 증권 관련 표현이 부족합니다.", relevance_score, score)
    if score >= 4:
        outlook = "호재 가능성 높음"
    elif score >= 2:
        outlook = "호재 가능성"
    elif score <= -4:
        outlook = "악재 가능성 높음"
    elif score <= -2:
        outlook = "악재 가능성"
    else:
        outlook = "판단 보류"
    evidence = matched_positive[:2] + matched_negative[:2]
    reason = f"기사에서 {', '.join(evidence)} 표현을 확인했습니다." if evidence else "방향을 단정할 핵심 표현이 부족합니다."
    return NewsAssessment(True, category, outlook, reason, relevance_score, score)
