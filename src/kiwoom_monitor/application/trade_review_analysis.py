"""체결 묶음과 분봉을 이용한 매매 복기 지표 계산."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kiwoom_monitor.application.trade_journal_summary import TradeEpisode


@dataclass(frozen=True)
class TradeReviewAnalysis:
    holding_seconds: int
    average_buy: float
    average_sell: float
    realized_return_rate: float
    max_favorable_rate: float | None
    max_adverse_rate: float | None
    exit_efficiency: float | None
    missed_gain_rate: float | None
    labels: tuple[str, ...]
    complete: bool
    confidence: int
    positive_points: tuple[str, ...]
    negative_points: tuple[str, ...]
    next_actions: tuple[str, ...]
    discipline_score: int
    verdict: str
    evaluated_rule_count: int = 0
    program_notes: tuple[str, ...] = ()
    program_warnings: tuple[str, ...] = ()
    user_setting_matches: tuple[str, ...] = ()
    user_setting_warnings: tuple[str, ...] = ()
    setup_evidence: tuple[str, ...] = ()
    lesson_warnings: tuple[str, ...] = ()
    unverifiable_items: tuple[str, ...] = ()

    @property
    def holding_text(self) -> str:
        minutes = max(0, self.holding_seconds) // 60
        if minutes < 60:
            return f"{minutes}분"
        hours, remainder = divmod(minutes, 60)
        if hours < 24:
            return f"{hours}시간 {remainder}분"
        days, hours = divmod(hours, 24)
        return f"{days}일 {hours}시간"


def _position_cycle_counts(episode: TradeEpisode) -> tuple[tuple[int, int], ...]:
    """Return buy/sell fill counts for each flat-to-flat position cycle.

    A manually combined episode may contain buy-sell-buy-sell.  Those are two
    independent entries, not one split entry and one split exit.
    """
    cycles: list[tuple[int, int]] = []
    position = buy_count = sell_count = 0
    for fill in sorted(episode.fills, key=lambda value: value.filled_at):
        quantity = max(0, int(fill.quantity))
        if fill.side == "매수":
            if position == 0 and (buy_count or sell_count):
                cycles.append((buy_count, sell_count))
                buy_count = sell_count = 0
            position += quantity
            buy_count += 1
        elif fill.side == "매도":
            if position <= 0:
                continue
            position = max(0, position - quantity)
            sell_count += 1
            if position == 0:
                cycles.append((buy_count, sell_count))
                buy_count = sell_count = 0
    if buy_count or sell_count:
        cycles.append((buy_count, sell_count))
    return tuple(cycles)


def analyze_trade_episode(
    episode: TradeEpisode, rows: tuple[tuple[object, ...], ...], now: datetime | None = None,
    personal_rules: tuple[str, ...] = (), trade_value_threshold_eok: float = 0.0,
    setup_type: str = "", setup_confidence: int | None = None,
    setup_evidence: tuple[str, ...] = (), lesson_warnings: tuple[str, ...] = (),
    unverifiable_items: tuple[str, ...] = (),
) -> TradeReviewAnalysis:
    buys = tuple(sorted((fill for fill in episode.fills if fill.side == "매수"), key=lambda value: value.filled_at))
    sells = tuple(sorted((fill for fill in episode.fills if fill.side == "매도"), key=lambda value: value.filled_at))
    buy_quantity = sum(fill.quantity for fill in buys)
    sell_quantity = sum(fill.quantity for fill in sells)
    average_buy = sum(fill.quantity * fill.price for fill in buys) / buy_quantity if buy_quantity else 0.0
    average_sell = sum(fill.quantity * fill.price for fill in sells) / sell_quantity if sell_quantity else 0.0
    position_cycles = _position_cycle_counts(episode)
    split_buy_count = max((buy_count for buy_count, _ in position_cycles), default=0)
    split_sell_count = max((sell_count for _, sell_count in position_cycles), default=0)
    has_split_buys = split_buy_count > 1
    has_split_sells = split_sell_count > 1
    add_style = ""
    if has_split_buys and len(buys) > 1:
        later_quantity = sum(fill.quantity for fill in buys[1:])
        later_average = sum(fill.quantity * fill.price for fill in buys[1:]) / later_quantity if later_quantity else buys[0].price
        if later_average <= buys[0].price * 0.995:
            add_style = "평단 낮추기"
        elif later_average >= buys[0].price * 1.005:
            add_style = "상승 확인 후 추가"
        else:
            add_style = "동일 가격대 분할"
    end = sells[-1].filled_at if sells else ((now or datetime.now()) if episode.summary.open_quantity > 0 else episode.ended_at)
    start = buys[0].filled_at if buys else episode.started_at
    holding_seconds = max(0, int((end - start).total_seconds()))

    start_minute = start.replace(second=0, microsecond=0)
    end_minute = end.replace(second=0, microsecond=0)
    # 분봉의 시각은 해당 1분 구간의 시작이다. 초 단위 체결과 직접 비교하면
    # 매수한 분의 봉(예: 체결 10:23:30, 봉 10:23:00)이 항상 누락된다.
    row_values = tuple(
        row for row in rows
        if start_minute <= datetime.fromisoformat(str(row[0])) <= end_minute
    )
    complete = (
        bool(row_values)
        and datetime.fromisoformat(str(row_values[0][0])) <= start_minute
        and datetime.fromisoformat(str(row_values[-1][0])) >= end_minute
    )
    favorable = adverse = efficiency = missed = None
    if average_buy > 0 and row_values:
        # A minute candle contains prices from before an intra-minute entry and
        # after an intra-minute exit.  Treating its full high/low as the held
        # path can invent an MFE/MAE that happened while no position existed.
        # Use full OHLC only for minutes wholly inside the holding interval.
        # For boundary minutes, retain only prices known to occur while held.
        held_prices: list[float] = [average_buy]
        if average_sell > 0:
            held_prices.append(average_sell)
        for row in row_values:
            minute = datetime.fromisoformat(str(row[0]))
            if minute == start_minute and start == start_minute and start_minute < end_minute:
                held_prices.extend(float(row[index]) for index in (1, 2, 3, 4))
            elif start_minute < minute < end_minute:
                held_prices.extend(float(row[index]) for index in (1, 2, 3, 4))
            elif minute == start_minute and start_minute < end_minute:
                held_prices.append(float(row[4]))  # entry-minute close
            elif minute == end_minute and start_minute < end_minute:
                held_prices.append(float(row[1]))  # exit-minute open
        highest = max(held_prices)
        lowest = min(held_prices)
        favorable = (highest / average_buy - 1) * 100
        adverse = (lowest / average_buy - 1) * 100
        if average_sell > 0:
            efficiency = (average_sell - lowest) / (highest - lowest) * 100 if highest > lowest else 100.0
            missed = max(0.0, favorable - (average_sell / average_buy - 1) * 100)

    labels: list[str] = []
    if has_split_buys:
        labels.append(f"분할매수 {split_buy_count}회")
        labels.append(add_style)
    if has_split_sells:
        labels.append(f"분할매도 {split_sell_count}회")
    if len(position_cycles) > 1:
        labels.append(f"재진입 {len(position_cycles)}회")
    labels.append("당일매매" if episode.started_at.date() == episode.ended_at.date() else "다일매매")
    labels.append("완전청산" if episode.summary.open_quantity == 0 else episode.summary.state)
    positive: list[str] = []  # verified personal-rule matches only
    negative: list[str] = []  # verified personal-rule warnings only
    actions: list[str] = []
    program_notes: list[str] = []
    program_warnings: list[str] = []
    user_setting_matches: list[str] = []
    user_setting_warnings: list[str] = []
    trade_value_state = ""
    evaluated_rule_count = 0
    if adverse is not None and adverse <= -3.0:
        program_warnings.append(
            f"MAE가 {adverse:.2f}%입니다. -3%는 미모사 원칙이 아니라 프로그램의 기본 위험 알림 기준입니다."
        )
        actions.append("진입 전 손절 기준과 최대 허용 손실을 정해두세요.")
    if add_style == "평단 낮추기":
        program_notes.append("후속 매수의 평균가격이 첫 매수가보다 0.5% 이상 낮았습니다.")

    if setup_type in ("과대낙폭", "낙주"):
        actions.append("급락 원인과 저점 이탈 시 손절 기준을 함께 확인하세요.")
    elif setup_type == "기타":
        actions.append("자동 유형이 불명확하므로 직접 유형과 진입 근거를 지정하세요.")
    entry_context = tuple(
        row for row in rows
        if datetime.fromisoformat(str(row[0])).date() == start.date()
        and datetime.fromisoformat(str(row[0])) <= start_minute
    )[-5:]
    trade_values = tuple(float(row[6] or 0) for row in entry_context)
    if trade_value_threshold_eok > 0 and trade_values:
        evaluated_rule_count += 1
        hits = tuple(value for value in trade_values if value >= trade_value_threshold_eok)
        peak = max(trade_values)
        if hits:
            trade_value_state = "충족"
            user_setting_matches.append(
                f"매수 직전 5분 중 {len(hits)}개 분봉이 설정 거래대금 {trade_value_threshold_eok:g}억을 넘었습니다(최고 {peak:,.2f}억)."
            )
        else:
            trade_value_state = "미달"
            user_setting_warnings.append(
                f"매수 직전 5분 거래대금이 설정 기준 {trade_value_threshold_eok:g}억에 도달하지 못했습니다(최고 {peak:,.2f}억)."
            )
    score = (
        round(len(user_setting_matches) / evaluated_rule_count * 100)
        if evaluated_rule_count else 0
    )
    score = max(0, min(100, score))
    verdict = (
        "자동 확인 가능한 설정 기준 없음" if not evaluated_rule_count
        else "자동 확인 가능한 설정 기준 충족" if score >= 85
        else "자동 확인 가능한 설정 기준 일부 주의" if score >= 60
        else "자동 확인 가능한 설정 기준 주의"
    )
    confidence = 90 if complete else (55 if row_values else 20)
    if setup_confidence is not None:
        confidence = min(confidence, max(20, setup_confidence))
    if setup_type == "기타":
        confidence = min(confidence, 50)
    if average_buy <= 0 or not sells:
        confidence = min(confidence, 40)
    return TradeReviewAnalysis(
        holding_seconds, average_buy, average_sell, episode.summary.return_rate,
        favorable, adverse, efficiency, missed, tuple(labels), complete, confidence,
        tuple(dict.fromkeys(positive)), tuple(dict.fromkeys(negative)), tuple(dict.fromkeys(actions)), score, verdict,
        evaluated_rule_count, tuple(dict.fromkeys(program_notes)), tuple(dict.fromkeys(program_warnings)),
        tuple(dict.fromkeys(user_setting_matches)), tuple(dict.fromkeys(user_setting_warnings)),
        tuple(dict.fromkeys(setup_evidence)), tuple(dict.fromkeys(lesson_warnings)),
        tuple(dict.fromkeys(unverifiable_items)),
    )
