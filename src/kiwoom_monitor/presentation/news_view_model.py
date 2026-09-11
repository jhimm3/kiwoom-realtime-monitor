from __future__ import annotations

import html
from dataclasses import dataclass

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
        return (
            "<p style='color:#667085'><b>AI 원문 분석</b>"
            f" · 관련성 {item.assessment.relevance_score}점 · 신뢰도 - · 아직 분석하지 않음</p><hr>"
        )
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
