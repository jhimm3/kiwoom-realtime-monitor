"""Qt 위젯과 무관한 매매 자동분석 표시 문구 조립."""

from __future__ import annotations

from datetime import datetime, time
from typing import Protocol

from kiwoom_monitor.application.trade_review_analysis import TradeReviewAnalysis


class EntrySnapshotDisplayLike(Protocol):
    executed_at: datetime
    rank: int | None
    trade_value_1m_eok: float | None
    trade_value_5m_eok: float | None
    themes: tuple[str, ...]
    theme_ranks: dict[str, int] | None
    high_distance_percent: float | None
    news: tuple[dict[str, object], ...]
    investor_flow: dict[str, object] | None
    orderbook: dict[str, object] | None
    market_state: dict[str, object] | None


class NewsAssessmentDisplayLike(Protocol):
    outlook: str


class LinkedNewsDisplayLike(Protocol):
    published_at: datetime | None
    assessment: NewsAssessmentDisplayLike
    title: str


def format_analysis_rate(value: float | None) -> str:
    return "계산 대기" if value is None else f"{value:+.2f}%"


def format_analysis_data_status(
    *,
    active_pack_names: tuple[str, ...],
    lesson_counts: dict[int, int],
    personal_rule_count: int,
    analyses: tuple[TradeReviewAnalysis, ...],
    snapshot_count: int,
) -> str:
    """자동분석에 실제 연결된 데이터와 원칙 범위를 한 줄로 표시한다."""
    data_state = "분봉 확인" if all(value.complete for value in analyses) else "분봉 범위 일부만 반영"
    rules_state = (
        "개인원칙 강의구조 연결 · "
        + " · ".join(f"{lesson}강 {count}문단" for lesson, count in sorted(lesson_counts.items()))
        if lesson_counts
        else f"개인원칙 문서 {personal_rule_count}문단 연결(강의구조 없음)"
        if personal_rule_count
        else "개인원칙 문서 미연결"
    )
    snapshot_state = (
        f"진입 스냅샷 {snapshot_count}건 연결"
        if snapshot_count else "당시 뉴스·테마·매수세·시장 수급 스냅샷 없음"
    )
    return f"활성 전략팩 {', '.join(active_pack_names)}  ·  {rules_state}  ·  {data_state}  ·  {snapshot_state}"


def format_daily_analysis_summary(
    analyses: tuple[TradeReviewAnalysis, ...],
    total_return_rate: float,
) -> str:
    average_score = round(sum(value.discipline_score for value in analyses) / len(analyses))
    evaluated_count = sum(value.evaluated_rule_count for value in analyses)
    overall_verdict = (
        "자동 확인 가능한 설정 기준 없음" if not evaluated_count
        else "자동 확인 가능한 설정 기준 충족" if average_score >= 85
        else "자동 확인 가능한 설정 기준 일부 주의" if average_score >= 60
        else "자동 확인 가능한 설정 기준 주의"
    )
    return (
        f"하루 종합 · {overall_verdict} · 자동 확인 기준 {evaluated_count}개 · 평균 {average_score}점 · "
        f"총 {len(analyses)}회차 · 전체 실현 {total_return_rate:+.2f}%"
    )


def format_linked_news_blocks(news: tuple[LinkedNewsDisplayLike, ...]) -> tuple[str, ...]:
    if not news:
        return ()
    return (
        "\n[매매일지 대표 뉴스]",
        *(
            f"{item.published_at.astimezone().strftime('%H:%M') if item.published_at else '--:--'} · "
            f"{item.assessment.outlook or '미분류'} · {item.title}"
            for item in news
        ),
    )


def format_cycle_analysis_block(
    *,
    cycle_number: int,
    setup_type: str,
    pack_name: str,
    source_text: str,
    relevant_rule_count: int,
    analysis: TradeReviewAnalysis,
) -> str:
    """회차별 자동분석 본문을 기존 표시 계약대로 만든다."""
    labels = " · ".join(analysis.labels)
    return (
        f"\n[{cycle_number}차 · {setup_type}] 전략팩: {pack_name}\n"
        f"적용 강의: {source_text}\n"
        f"구조화 강의항목 {relevant_rule_count}개 참고자료 연결 · {analysis.verdict} · 실제 자동 확인 {analysis.evaluated_rule_count}개 · "
        f"점수 {analysis.discipline_score}점 · "
        f"보유 {analysis.holding_text} · 실현 {analysis.realized_return_rate:+.2f}%\n"
        f"분석 신뢰도 {analysis.confidence}% · MFE(보유 중 최대 유리폭) {format_analysis_rate(analysis.max_favorable_rate)} · "
        f"MAE(보유 중 최대 불리폭) {format_analysis_rate(analysis.max_adverse_rate)} · {labels}\n"
        f"확인된 진입 근거: {' / '.join(analysis.setup_evidence) or '가격 데이터로 확인된 근거 없음'}\n"
        f"강의 기준 주의: {' / '.join(analysis.lesson_warnings) or '가격 데이터에서 확인된 주의 없음'}\n"
        f"현재 판정 불가: {' / '.join(analysis.unverifiable_items) or '추가 확인 항목 없음'}\n"
        f"개인 원칙 충족: {' / '.join(analysis.positive_points) or '확인된 충족 항목 없음'}\n"
        f"개인 원칙 주의: {' / '.join(analysis.negative_points) or '확인된 위반 없음'}\n"
        f"사용자 설정 기준 충족: {' / '.join(analysis.user_setting_matches) or '확인된 충족 항목 없음'}\n"
        f"사용자 설정 기준 주의: {' / '.join(analysis.user_setting_warnings) or '설정 기준 경고 없음'}\n"
        f"프로그램 보조정보: {' / '.join(analysis.program_notes) or '추가 참고사항 없음'}\n"
        f"프로그램 기본 경고: {' / '.join(analysis.program_warnings) or '기본 경고 없음'}\n"
        f"다음 행동: {' / '.join(analysis.next_actions[:5]) or '현재 기준을 유지하며 표본을 더 확인하세요.'}"
    )


def format_entry_snapshot_blocks(entry: EntrySnapshotDisplayLike) -> tuple[str, ...]:
    """진입 스냅샷과 시장 상태를 화면용 문단으로 만든다."""
    pressure = entry.orderbook or {}
    investor = entry.investor_flow or {}
    raw_program = investor.get("program_trade")
    program = raw_program if isinstance(raw_program, dict) else {}
    investor_pending = investor.get("status") == "pending_close" or (
        investor.get("foreign_net_buy_quantity") == 0
        and investor.get("institution_net_buy_quantity") == 0
        and str(investor.get("as_of_date", "")) == entry.executed_at.strftime("%Y%m%d")
        and entry.executed_at.time() < time(20, 5)
    )
    foreign_text = "집계 전" if investor_pending else investor.get("foreign_net_buy_quantity", "-")
    institution_text = "집계 전" if investor_pending else investor.get("institution_net_buy_quantity", "-")
    theme_text = ", ".join(
        f"{theme}({entry.theme_ranks.get(theme)}위)"
        if entry.theme_ranks and theme in entry.theme_ranks else theme
        for theme in entry.themes
    ) or "없음"
    if entry.high_distance_percent is not None:
        headline = (
            "진입 스냅샷: "
            f"실시간 {entry.rank if entry.rank is not None else '-'}위 · "
            f"1분 {entry.trade_value_1m_eok or 0:.2f}억 · 5분 {entry.trade_value_5m_eok or 0:.2f}억 · "
            f"신고가 거리 {entry.high_distance_percent:.2f}% · "
        )
    else:
        headline = "진입 스냅샷: 신고가 거리 자료 없음 · "

    execution_strength = pressure.get("execution_strength")
    buy_share = pressure.get("buy_share_percent")
    strength_text = f"{float(execution_strength):.2f}" if isinstance(execution_strength, (int, float)) else "-"
    buy_share_text = f"{float(buy_share):.2f}%" if isinstance(buy_share, (int, float)) else "-"
    details = (
        f"테마: {theme_text} · 체결강도 {strength_text} · "
        f"최근 60초 매수비중 {buy_share_text} · "
        f"외국인 순매수 {foreign_text} · "
        f"기관 순매수 {institution_text} · "
        f"수급 기준 {investor.get('market_basis', '-')} · "
        f"프로그램 순매수 {program.get('net_buy_amount_million_won', '-')}백만원/"
        f"{program.get('net_buy_quantity', '-')}주 · "
        f"직전 프로그램 갱신 변화 {program.get('net_buy_amount_change_million_won', '-')}백만원/"
        f"{program.get('net_buy_quantity_change', '-')}주 · 관련 뉴스 {len(entry.news)}건"
    )

    blocks = [headline, details]
    kospi = entry.market_state.get("kospi", {}) if entry.market_state else {}
    kosdaq = entry.market_state.get("kosdaq", {}) if entry.market_state else {}
    if kospi or kosdaq:
        blocks.append(
            f"시장: 코스피 {kospi.get('index', '-')} ({kospi.get('change_rate', '-')}%) · "
            f"거래대금 {kospi.get('trade_value_eok', '-')}억 / "
            f"코스닥 {kosdaq.get('index', '-')} ({kosdaq.get('change_rate', '-')}%) · "
            f"거래대금 {kosdaq.get('trade_value_eok', '-')}억 · "
            f"시장 조회 {entry.market_state.get('observed_at', '-')}"
        )
    return tuple(blocks)
