from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any, Mapping

from kiwoom_monitor.application.news_analysis import extractive_news_summary
from kiwoom_monitor.application.news_grouping import NewsEventGroup
from kiwoom_monitor.infrastructure.naver_news import StockNewsItem, news_provider
from kiwoom_monitor.infrastructure.persistence.news_ai_repository import StoredAINewsAnalysis


@dataclass(frozen=True)
class NewsDisplayRow:
    published: str
    provider: str
    category: str
    outlook: str
    title: str


@dataclass(frozen=True)
class StoredNewsEvidence:
    """선택한 목록 판본과 정확히 연결된 NAS 본문·규칙 근거."""

    identity: str
    title: str = ""
    article_revision_id: str = ""
    body_revision_id: str = ""
    body_status: str = "missing"
    body_text: str = ""
    body_error: str = ""
    event: Mapping[str, Any] | None = None
    historical_revision: bool = False
    notice: str = ""


def escape_html(value: object) -> str:
    return html.escape(str(value or "")).replace("\n", "<br>")


def effective_judgment(
    item: StockNewsItem,
    stored: StoredAINewsAnalysis | None,
) -> tuple[str, str, str, str]:
    if stored is None:
        return (
            item.assessment.category,
            item.assessment.outlook,
            item.assessment.reason,
            "제목·검색 요약 규칙",
        )
    result = stored.analysis
    if result.outlook == "긍정":
        outlook = "호재 가능성 높음" if result.confidence >= 70 else "호재 가능성"
    elif result.outlook == "부정":
        outlook = "악재 가능성 높음" if result.confidence >= 70 else "악재 가능성"
    elif result.outlook == "혼재":
        outlook = "호재·악재 혼재"
    else:
        outlook = "판단 보류"
    reason = result.reason or "AI 원문 분석에서 구체적인 판단 이유를 제공하지 않았습니다."
    category = result.category or item.assessment.category
    return category, outlook, reason, f"AI 원문 분석 ({stored.provider} · 신뢰도 {result.confidence}%)"


def build_display_row(
    item: StockNewsItem,
    group: NewsEventGroup,
    stored: StoredAINewsAnalysis | None,
) -> NewsDisplayRow:
    published = item.published_at.astimezone().strftime("%m-%d %H:%M") if item.published_at else "-"
    category, outlook, _reason, source = effective_judgment(item, stored)
    displayed_outlook = f"{outlook}  ᴬᴵ" if source.startswith("AI 원문 분석") else outlook
    suffixes: list[str] = []
    if len(group.items) > 1:
        suffixes.append(f"관련 기사 {len(group.items)}건")
    if group.stage:
        suffixes.append(f"단계: {group.stage}")
    if group.past_event_republication:
        suffixes.append("과거 사건 재언급 가능")
    title = item.title + (f"  · {' · '.join(suffixes)}" if suffixes else "")
    return NewsDisplayRow(published, news_provider(item), category, displayed_outlook, title)


def related_articles_html(group: NewsEventGroup) -> str:
    if len(group.items) <= 1:
        return ""
    rows: list[str] = []
    for item in group.items[1:]:
        published = item.published_at.astimezone().strftime("%m-%d %H:%M") if item.published_at else "-"
        url = item.original_link or item.link
        title = escape_html(item.title)
        title_html = f"<a href='{escape_html(url)}'>{title}</a>" if url else title
        rows.append(
            f"<li>{escape_html(published)} · {escape_html(news_provider(item))} · {title_html}</li>"
        )
    return f"<hr><p><b>관련 기사 {len(group.items)}건</b></p><ul>{''.join(rows)}</ul>"


def ai_detail_html(item: StockNewsItem, stored: StoredAINewsAnalysis | None) -> str:
    if stored is None:
        return ""
    result = stored.analysis
    positive = " / ".join(result.positive_evidence) or "-"
    negative = " / ".join(result.negative_evidence) or "-"
    return (
        "<div style='background:#EEF6FF; border:1px solid #9CC7F2; padding:10px;'>"
        f"<h3 style='margin-top:0'>AI 원문 분석 · 관련성 {item.assessment.relevance_score}점"
        f" · 신뢰도 {result.confidence}%</h3>"
        f"<p><b>판단:</b> {escape_html(result.outlook)}</p>"
        f"<p><b>원문 기준 분류:</b> {escape_html(result.category or item.assessment.category)}</p>"
        f"<p><b>이유:</b> {escape_html(result.reason)}</p>"
        f"<p><b>긍정 근거:</b> {escape_html(positive)}</p>"
        f"<p><b>부정 근거:</b> {escape_html(negative)}</p>"
        f"<p><b>원문 요약:</b> {escape_html(result.summary)}</p>"
        f"<p style='color:#667085'>{escape_html(stored.provider)} · "
        f"{escape_html(stored.model)} · DB 저장됨</p></div><hr>"
    )


def stored_news_evidence_html(evidence: StoredNewsEvidence | None, *, loading: bool = False) -> str:
    """NAS 원문과 기록 전용 규칙을 AI 판단과 분리해 표시한다."""
    if loading:
        return _evidence_box("기사 원문", "저장된 본문을 불러오는 중…")
    if evidence is None:
        return ""
    if not evidence.article_revision_id:
        return _evidence_box("기사 원문", evidence.notice) if evidence.notice else ""

    status_labels = {
        "fulltext": "원문 수집 완료",
        "summary_only": "검색 요약만 저장",
        "failed": "본문 수집 실패",
        "missing": "본문 아직 처리 중",
    }
    status = status_labels.get(evidence.body_status, "본문 상태 미확인")
    notice = ""
    if evidence.historical_revision:
        notice = (
            "<p style='color:#9A6700'><b>판본 안내:</b> NAS 최신 판본과 현재 목록의 제목 또는 "
            "요약이 달라, 현재 목록과 정확히 일치하는 저장 당시 판본의 근거입니다.</p>"
        )
    if evidence.notice:
        notice += f"<p style='color:#667085'>{escape_html(evidence.notice)}</p>"

    body = ""
    if evidence.body_text:
        body_text = evidence.body_text[:20_000]
        truncated = "<p style='color:#667085'>화면에는 본문 앞부분 20,000자만 표시합니다.</p>" \
            if len(evidence.body_text) > len(body_text) else ""
        body = f"{_article_body_html(body_text)}{truncated}"
    elif evidence.body_error:
        body = f"<p style='color:#B42318'>{escape_html(evidence.body_error)}</p>"
    else:
        body = "<p style='color:#667085'>저장된 본문이 없습니다.</p>"

    event_html = _contract_event_html(evidence.event)
    return (
        "<div style='background:#F8FAFC; border:1px solid #CBD5E1; padding:10px;'>"
        f"<h3 style='margin-top:0'>기사 원문 · {escape_html(status)}</h3>"
        f"{notice}{body}{event_html}</div><hr>"
    )


def stored_news_core_sentences_html(evidence: StoredNewsEvidence | None) -> str:
    """최종 판단 바로 뒤에 둘 비AI 원문 핵심 문장만 렌더링한다."""
    if evidence is None or evidence.body_status != "fulltext" or not evidence.body_text:
        return ""
    summary_sentences = extractive_news_summary("", evidence.title, evidence.body_text[:20_000])
    if not summary_sentences:
        return ""
    summary = "".join(
        "<li style='margin:0 0 8px 0; line-height:1.65'>" + escape_html(sentence) + "</li>"
        for sentence in summary_sentences
    )
    return (
        "<div style='background:#FFF; border-left:4px solid #2563EB; padding:8px 12px; margin-bottom:16px;'>"
        "<h3 style='margin:0 0 8px 0'>AI 없이 뽑은 핵심 문장</h3>"
        "<p style='color:#667085; margin:0 0 8px 0'>원문의 문장을 바꾸지 않고 골랐습니다.</p>"
        f"<ul style='margin:0; padding-left:20px'>{summary}</ul></div>"
    )


def _article_body_html(text: str) -> str:
    """저장 본문을 바꾸지 않고 문장 경계만 묶어 읽기 좋은 문단 HTML로 만든다."""
    normalized = " ".join(str(text or "").split())
    if not normalized:
        return ""
    sections = re.split(r"(?=(?:\[관련 기사\s+\d+/\d+\s*:[^]]*\]))", normalized)
    rendered: list[str] = []
    for section in sections:
        section = section.strip()
        if not section:
            continue
        header = re.match(r"(\[관련 기사\s+\d+/\d+\s*:[^]]*\])\s*", section)
        if header:
            rendered.append(
                "<h4 style='margin:16px 0 8px 0'>" + escape_html(header.group(1)) + "</h4>"
            )
            section = section[header.end():].strip()
        sentences = [value.strip() for value in re.findall(r".+?(?:[.!?](?=\s|$)|$)", section) if value.strip()]
        paragraphs: list[str] = []
        current: list[str] = []
        for sentence in sentences:
            if current and (len(current) >= 3 or len(" ".join(current + [sentence])) > 420):
                paragraphs.append(" ".join(current))
                current = []
            current.append(sentence)
        if current:
            paragraphs.append(" ".join(current))
        for paragraph in paragraphs or [section]:
            rendered.append(
                "<p style='line-height:1.75; margin:0 0 14px 0; word-break:keep-all;'>"
                + escape_html(paragraph) + "</p>"
            )
    return "".join(rendered)


def _contract_event_html(event: Mapping[str, Any] | None) -> str:
    if event is None:
        return ""
    result = event.get("result") if isinstance(event.get("result"), Mapping) else {}
    certainty = {
        "CONFIRMED": "계약 확정",
        "POTENTIAL": "논의·예정 단계",
        "TERMINATED": "해지·취소",
        "DENIED": "계약 부인",
        "UNKNOWN": "확인 필요",
    }.get(str(event.get("certainty") or ""), "미확인")
    novelty = {
        "NEW": "최초 관측",
        "UPDATE": "후속 변경",
        "REPUBLICATION": "과거 사실 재언급",
    }.get(str(event.get("novelty") or ""), "미확인")
    amount_won = event.get("amount_won")
    amount_text = result.get("amount_text") if isinstance(result, Mapping) else ""
    if isinstance(amount_won, (int, float)):
        amount = f"{int(amount_won):,}원"
    else:
        amount = str(amount_text or "안전하게 환산할 수 없음")
    spans = result.get("evidence_spans", ()) if isinstance(result, Mapping) else ()
    evidence_rows = []
    if isinstance(spans, (list, tuple)):
        for span in spans[:12]:
            if not isinstance(span, Mapping):
                continue
            evidence_rows.append(
                f"<li>{escape_html(_evidence_span_label(span.get('field'), span.get('fact')))}: "
                f"{escape_html(span.get('text'))}</li>"
            )
    evidence_html = f"<ul>{''.join(evidence_rows)}</ul>" if evidence_rows else "<p>근거 문구 없음</p>"
    ai_required = bool(event.get("ai_required"))
    ai_reasons = result.get("ai_reason", ()) if isinstance(result, Mapping) else ()
    if not isinstance(ai_reasons, (list, tuple)):
        ai_reasons = ()
    additional_check = "필요" if ai_required else "필수 아님"
    reasons = ", ".join(_ai_reason_label(str(value)) for value in ai_reasons) or "-"
    scope = {
        "TARGET_COMPANY": "선택 종목 직접 관련",
        "UNKNOWN": "대상 종목 직접성 미확인",
    }.get(str(event.get("scope") or ""), "미확인")
    role = {
        "FACT": "계약 사실 기사",
        "REACTION": "가격 반응 기사",
        "UNKNOWN": "기사 역할 미확인",
    }.get(str(event.get("role") or ""), "미확인")
    return (
        "<hr><h3>공급계약 규칙 근거</h3>"
        f"<p><b>상태:</b> {escape_html(certainty)} · <b>새 정보 여부:</b> {escape_html(novelty)}"
        f" · <b>금액:</b> {escape_html(amount)}</p>"
        f"<p><b>상대방:</b> {escape_html(event.get('counterparty') or '미확인')}"
        f" · <b>대상 범위:</b> {escape_html(scope)}"
        f" · <b>역할:</b> {escape_html(role)}</p>"
        f"<p><b>규칙 점수:</b> 중요도 {_score_text(event.get('importance_score'))}"
        f" · 확신도 {_score_text(event.get('confidence_score'))}"
        f" · 새 정보 {_score_text(event.get('novelty_score'))}"
        " <span style='color:#667085'>(AI 분석 점수나 AI 신뢰도가 아님)</span></p>"
        f"<p><b>추가 확인:</b> {additional_check} · {escape_html(reasons)}</p>"
        f"<p><b>근거 문구:</b></p>{evidence_html}"
    )


def _ai_reason_label(value: str) -> str:
    labels = {
        "certainty:potential": "논의·예정 단계",
        "certainty:unknown": "계약 상태 불명확",
        "certainty:denied": "계약 부인",
        "title_body_conflict": "제목과 본문 충돌",
        "conditional_amount": "조건부 금액",
        "amount_not_safely_convertible": "금액 환산 불가",
        "counterparty_unknown": "상대방 미확인",
        "target_scope_unknown": "대상 종목 직접성 미확인",
    }
    return labels.get(value, value)


def _score_text(value: object) -> str:
    return escape_html("-" if value is None else str(value))


def _evidence_span_label(field: object, fact: object) -> str:
    fields = {"title": "제목", "description": "검색 요약", "body": "본문"}
    facts = {
        "supply_contract": "공급계약 문맥", "confirmed": "확정 문구",
        "potential": "논의·예정 문구", "terminated": "해지·취소 문구",
        "denied": "부인 문구", "market_reaction": "가격 반응 문구",
        "amount": "금액", "counterparty": "계약 상대방",
    }
    return f"{fields.get(str(field), '근거')} · {facts.get(str(fact), '판정 근거')}"


def _evidence_box(title: str, message: str) -> str:
    return (
        "<div style='background:#F8FAFC; border:1px solid #CBD5E1; padding:10px;'>"
        f"<h3 style='margin-top:0'>{escape_html(title)}</h3>"
        f"<p style='color:#667085'>{escape_html(message)}</p></div><hr>"
    )
