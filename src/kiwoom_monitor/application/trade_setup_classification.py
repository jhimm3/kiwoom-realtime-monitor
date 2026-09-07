"""매수 직전 분봉으로 사용자가 시도한 매매 유형을 설명 가능한 규칙으로 추정한다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kiwoom_monitor.application.trade_journal_summary import TradeEpisode, split_trade_episode_cycles


TRADE_SETUP_TYPES = (
    "주도주 돌파", "테마주 돌파", "돌파", "눌림", "시가베팅", "상따",
    "신규주", "종가베팅", "과대낙폭", "낙주", "기타",
)


def normalize_trade_setup_type(value: str) -> str:
    """과거 버전의 진입 모양 이름을 강의 주제 기준 이름으로 옮긴다."""
    return {
        "추격매수": "주도주 돌파",
    }.get(value, value if value in TRADE_SETUP_TYPES else "기타")


@dataclass(frozen=True)
class TradeSetupClassification:
    setup_type: str
    confidence: int
    evidence: tuple[str, ...]
    subtype: str = ""
    unverifiable: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def lesson_unverifiable_for(setup_type: str) -> tuple[str, ...]:
    """Return structured-lecture conditions absent from stored chart/fill data."""
    value = {
        "주도주 돌파": (
            "1강 기준의 당시 시장 주도주 여부(시장 전체 순위·지속적인 관심)",
            "당시 뉴스·테마·호가·외국인·기관 수급",
        ),
        "테마주 돌파": (
            "3강 기준의 테마 전체 동반 상승과 테마 내 실질 주도주 순위",
            "당시 뉴스·호가·외국인·기관 수급",
        ),
        "신규주": (
            "4강 기준의 직전 신규주 투자심리와 당시 다른 주도주의 존재 여부",
            "당시 뉴스·호가·시장 수급",
        ),
        "종가베팅": (
            "5강 기준의 익일 신규 매수세를 만들 뉴스·일정·테마 기대감",
            "당시 외국인·기관 수급과 관련 해외 종목 흐름",
        ),
        "과대낙폭": (
            "6강 기준의 최근 시장 주도주 이력과 상승 논리 유지 여부",
            "급락 원인과 당시 뉴스·테마·시장 수급",
        ),
        "낙주": (
            "6강 기준의 당일 주도주·강한 테마주 여부",
            "급락 원인과 당시 뉴스·호가·시장 수급",
        ),
    }.get(setup_type)
    if value is not None:
        return value
    if setup_type in {"돌파", "눌림", "상따"}:
        return (
            "당시 시장 주도주·테마 여부와 재료의 지속성",
            "당시 뉴스·호가·외국인·기관 수급",
        )
    if setup_type == "시가베팅":
        return ("장 시작 전 재료와 갭 형성 원인", "당시 시장 수급과 호가")
    return ()


def _lesson_context(
    result: TradeSetupClassification, subtype: str = "",
) -> TradeSetupClassification:
    """Attach only the lecture conditions that the stored market data cannot prove."""
    return TradeSetupClassification(
        result.setup_type, result.confidence, result.evidence,
        subtype or result.subtype, lesson_unverifiable_for(result.setup_type), result.warnings,
    )


def classify_trade_setup(
    episode: TradeEpisode, rows: tuple[tuple[object, ...], ...],
    daily_rows: tuple[tuple[object, ...], ...] = (),
) -> TradeSetupClassification:
    results = classify_trade_setup_cycles(episode, rows, daily_rows)
    if len(results) <= 1:
        return results[0]
    return TradeSetupClassification(
        " · ".join(f"{index}차 {result.setup_type}" for index, result in enumerate(results, 1)),
        round(sum(result.confidence for result in results) / len(results)),
        tuple(
            f"{index}차: {evidence}"
            for index, result in enumerate(results, 1)
            for evidence in result.evidence
        ),
        " · ".join(f"{index}차 {result.subtype}" for index, result in enumerate(results, 1) if result.subtype),
        tuple(
            f"{index}차: {item}"
            for index, result in enumerate(results, 1)
            for item in result.unverifiable
        ),
        tuple(
            f"{index}차: {item}"
            for index, result in enumerate(results, 1)
            for item in result.warnings
        ),
    )


def classify_trade_setup_cycles(
    episode: TradeEpisode, rows: tuple[tuple[object, ...], ...],
    daily_rows: tuple[tuple[object, ...], ...] = (),
) -> tuple[TradeSetupClassification, ...]:
    """각 포지션 회차를 따로 판정해 UI에서 회차별 수정할 수 있게 한다."""
    cycles = split_trade_episode_cycles(episode)
    results: list[TradeSetupClassification] = []
    prior_breakout_context = False
    for cycle in cycles:
        result = _classify_single_cycle(cycle, rows, daily_rows)
        if (
            prior_breakout_context and result.setup_type == "기타"
            and result.subtype == "당일 고점 근접 진입"
        ):
            result = _lesson_context(TradeSetupClassification("주도주 돌파", 52, (
                *result.evidence,
                "같은 날 앞 회차에서 돌파 형태가 확인되어 이후 고점 부근 재진입으로 연결했습니다.",
            ), warnings=("최초 돌파가 아니라 돌파 뒤 재진입이므로 고점 추격 여부를 별도로 확인해야 합니다.",)), "돌파 후 고점 재진입 후보")
        results.append(result)
        prior_breakout_context = prior_breakout_context or (
            result.setup_type == "주도주 돌파" or "고점 돌파" in result.subtype
        )
    return tuple(results)


def _classify_single_cycle(
    episode: TradeEpisode, rows: tuple[tuple[object, ...], ...],
    daily_rows: tuple[tuple[object, ...], ...] = (),
) -> TradeSetupClassification:
    buys = tuple(sorted((fill for fill in episode.fills if fill.side == "매수"), key=lambda value: value.filled_at))
    if not buys:
        return TradeSetupClassification("기타", 20, ("매수 체결이 없어 진입 유형을 판정할 수 없습니다.",))
    entry = buys[0]
    entry_minute = entry.filled_at.replace(second=0, microsecond=0)
    available_daily_dates = tuple(dict.fromkeys(
        datetime.fromisoformat(str(row[0])).date()
        for row in daily_rows if datetime.fromisoformat(str(row[0])).date() <= entry_minute.date()
    ))
    is_new_listing = len(available_daily_dates) == 1 and available_daily_dates[0] == entry_minute.date()
    parsed = tuple((datetime.fromisoformat(str(row[0])), row) for row in rows)
    same_day = tuple((minute, row) for minute, row in parsed if minute.date() == entry_minute.date() and minute <= entry_minute)
    if not same_day:
        return TradeSetupClassification("기타", 20, ("매수 시점 이전 분봉이 없어 자동분류를 보류했습니다.",))

    minute_of_day = entry.filled_at.hour * 60 + entry.filled_at.minute
    market = str(entry.market or "").upper()
    eight_at = entry_minute.replace(hour=8, minute=0)
    nine_at = entry_minute.replace(hour=9, minute=0)
    integrated_rows = tuple((minute, row) for minute, row in same_day if minute >= eight_at) or same_day
    regular_rows = tuple((minute, row) for minute, row in same_day if minute >= nine_at)
    prior_day = tuple((minute, row) for minute, row in parsed if minute.date() < entry_minute.date())
    previous_close = float(prior_day[-1][1][4]) if prior_day else 0.0
    day_open = float(integrated_rows[0][1][1])
    regular_open = float(regular_rows[0][1][1]) if regular_rows else 0.0
    entry_price = float(entry.price)
    prior_pairs = tuple((minute, row) for minute, row in integrated_rows if minute < entry_minute)
    prior_rows = tuple(row for _, row in prior_pairs)
    prior_high = max((float(row[2]) for row in prior_rows[-30:]), default=0.0)
    session_high = max((entry_price, *(float(row[2]) for row in prior_rows)))
    session_low = min((entry_price, *(float(row[3]) for row in prior_rows)))
    peak_index = max(range(len(prior_rows)), key=lambda index: float(prior_rows[index][2])) if prior_rows else -1
    after_peak = prior_rows[peak_index + 1:] if peak_index >= 0 else ()
    post_peak_low = min((entry_price, *(float(row[3]) for row in after_peak)))
    change = entry_price / (previous_close or day_open) - 1 if (previous_close or day_open) else 0.0
    rise_before = prior_high / day_open - 1 if prior_high and day_open else 0.0
    drawdown = entry_price / prior_high - 1 if prior_high else 0.0
    rebound = entry_price / session_low - 1 if session_low else 0.0
    if minute_of_day < 9 * 60:
        opening_reference = day_open
        opening_reference_minute = 8 * 60
        opening_label = "NXT 장"
    else:
        opening_reference = regular_open or day_open
        opening_reference_minute = 9 * 60
        opening_label = "KRX 정규장"
    minutes_from_opening_reference = max(0, minute_of_day - opening_reference_minute)
    opening_reference_text = f"{opening_reference_minute // 60:02d}:{opening_reference_minute % 60:02d}"
    closing_entry = (
        (market == "KRX" and minute_of_day >= 15 * 60 + 10)
        or (market == "NXT" and minute_of_day >= 19 * 60 + 40)
        or (market not in {"KRX", "NXT"} and (
            15 * 60 + 10 <= minute_of_day <= 15 * 60 + 30
            or minute_of_day >= 19 * 60 + 40
        ))
    )
    prior_daily = tuple(
        row for row in daily_rows
        if datetime.fromisoformat(str(row[0])).date() < entry_minute.date()
    )
    recent_peak = max((float(row[2]) for row in prior_daily[-20:]), default=0.0)
    decline_from_recent_peak = entry_price / recent_peak - 1 if recent_peak else 0.0
    decline_from_session_high = entry_price / session_high - 1 if session_high else 0.0
    held_overnight = episode.ended_at.date() > entry.filled_at.date() or episode.summary.open_quantity > 0
    post_peak_rebound = entry_price / post_peak_low - 1 if post_peak_low else 0.0
    pullback_range = prior_high - post_peak_low if prior_high else 0.0
    recovery_ratio = (entry_price - post_peak_low) / pullback_range if pullback_range > 0 else 0.0
    breakout_candidate = len(prior_rows) >= 3 and prior_high and entry_price >= prior_high * 0.998
    recent_context = prior_rows[-5:]
    recent_trade_values = tuple(float(row[6] or 0.0) for row in recent_context)
    lecture_turnover_hits = sum(value >= 40.0 for value in recent_trade_values)
    turnover_context = (
        f"매수 직전 5분 중 {lecture_turnover_hits}개 분봉이 강의의 경험적 예시인 거래대금 40억 원을 넘었습니다."
        if recent_trade_values else ""
    )
    # 강의의 핵심은 숫자 하나가 아니라 좁은 가격대의 매물 소화 뒤 돌파다.
    # 최근 5개 완성봉의 전체 범위가 3% 이내인 경우만 박스권 후보로 표시한다.
    recent_high = max((float(row[2]) for row in recent_context), default=0.0)
    recent_low = min((float(row[3]) for row in recent_context), default=0.0)
    consolidation_range = recent_high / recent_low - 1 if recent_low else 0.0
    box_candidate = len(recent_context) >= 5 and consolidation_range <= 0.03
    convergence_context = prior_rows[-6:]
    triangle_candidate = False
    if len(convergence_context) == 6:
        early, late = convergence_context[:3], convergence_context[3:]
        early_high = max(float(row[2]) for row in early)
        early_low = min(float(row[3]) for row in early)
        late_high = max(float(row[2]) for row in late)
        late_low = min(float(row[3]) for row in late)
        early_width = early_high - early_low
        late_width = late_high - late_low
        triangle_candidate = bool(
            early_width > 0 and late_width <= early_width * 0.8
            and late_high <= early_high * 1.003 and late_low >= early_low * 0.997
        )
    w_candidate = False
    w_context = prior_rows[-10:]
    if len(w_context) >= 7:
        lows = tuple(float(row[3]) for row in w_context)
        local_lows = tuple(
            index for index in range(1, len(lows) - 1)
            if lows[index] <= lows[index - 1] and lows[index] <= lows[index + 1]
        )
        for first, second in zip(local_lows, local_lows[1:]):
            if second - first < 3 or not lows[first]:
                continue
            neckline = max(float(row[2]) for row in w_context[first:second + 1])
            second_low_ratio = lows[second] / lows[first]
            similar_or_rising_lows = 0.995 <= second_low_ratio <= 1.025
            rebound_height = neckline / max(lows[first], lows[second]) - 1 if neckline else 0.0
            if (
                similar_or_rising_lows and rebound_height >= 0.02
                and entry_price >= neckline * 0.998
            ):
                w_candidate = True
                break
    opening_gap = opening_reference / previous_close - 1 if previous_close and opening_reference else 0.0
    sharp_rise = False
    if len(prior_rows) >= 4:
        first_price = float(prior_rows[-4][1])
        sharp_rise = bool(first_price and prior_high / first_price - 1 >= 0.10)
    immediate_rebreak_after_drop = bool(
        breakout_candidate and prior_high and post_peak_low
        and post_peak_low / prior_high - 1 <= -0.045
    )
    pullback_candidate = rise_before >= 0.03 and -0.08 <= drawdown <= -0.01 and len(after_peak) >= 1
    pullback_recovered = (
        pullback_candidate and len(after_peak) >= 2
        and post_peak_rebound >= 0.005 and recovery_ratio >= 0.35
    )

    if previous_close and entry_price >= previous_close * 1.285:
        return TradeSetupClassification("기타", 45, (
            f"매수가가 전일 종가 대비 {change * 100:+.1f}%입니다.",
            "가격제한폭 상단 부근 진입은 확인했지만 강의 주제를 특정할 신규주·주도주 정보가 없습니다.",
        ))
    if is_new_listing:
        subtype = "IPO 신규주 후보"
        confidence = 88
        extra = ()
        lecture_warnings = []
        if lecture_turnover_hits < 2:
            lecture_warnings.append(
                "4강의 핵심인 지속적인 거래대금은 매수 직전 분봉에서 충분히 확인되지 않았습니다."
            )
        if sharp_rise:
            lecture_warnings.append(
                "상장 당일 짧은 시간의 수직 급등 구간이어서 4강의 추격 회피 기준을 확인해야 합니다."
            )
        if decline_from_session_high <= -0.08 and rebound >= 0.02:
            subtype = "IPO 신규주 · 분봉 급락 반등 후보"
            extra = (f"상장 당일 고점 대비 {decline_from_session_high * 100:.1f}% 급락 후 저가에서 {rebound * 100:.1f}% 반등한 진입입니다.",)
        elif breakout_candidate:
            subtype = "IPO 신규주 · 30분 고점 돌파 후보"
            extra = (f"상장 당일 직전 30분 고점 {prior_high:,.0f}원 돌파 형태로 진입했습니다.",)
        elif pullback_recovered:
            subtype = "IPO 신규주 · 눌림 후 재상승 후보"
            extra = ("상장 당일 상승 뒤 눌림과 재상승 형태가 함께 확인됩니다.",)
        elif pullback_candidate:
            subtype = "IPO 신규주 · 눌림 선진입 후보"
            confidence = 72
            extra = ("상장 당일 눌림 구간이지만 재상승 확인은 충분하지 않습니다.",)
        return _lesson_context(TradeSetupClassification("신규주", confidence, (
            f"매매일 {entry_minute:%Y-%m-%d}까지 확인된 일봉이 상장 당일 1개뿐입니다.",
            "4강 신규주 매매로 분류했습니다. 일봉 저장 누락 가능성이 있으면 상장일을 추가 확인하세요.",
            *extra,
            *((turnover_context,) if turnover_context else ()),
        ), warnings=tuple(lecture_warnings)), subtype)
    if closing_entry and held_overnight:
        subtype = "장 마감 진입"
        extra = ()
        lesson_warnings = []
        close_distance = entry_price / session_high - 1 if session_high else 0.0
        if close_distance <= -0.05:
            lesson_warnings.append(
                f"당일 고점보다 {abs(close_distance) * 100:.1f}% 낮아 5강의 종가까지 살아 있는 추세는 약하게 보입니다."
            )
        if lecture_turnover_hits < 2:
            lesson_warnings.append(
                "5강의 큰 거래대금·지속적인 관심 조건은 매수 직전 분봉에서 충분히 확인되지 않았습니다."
            )
        if recent_peak and decline_from_recent_peak <= -0.15:
            subtype = "과대낙폭 구간 · 장 마감 진입"
            extra = (f"최근 20거래일 고점보다 {abs(decline_from_recent_peak) * 100:.1f}% 낮은 과대낙폭 구간이기도 합니다.",)
        return _lesson_context(TradeSetupClassification("종가베팅", 86, (
            f"장 마감 구간인 {entry.filled_at:%H:%M}에 첫 매수했습니다.",
            f"당일 고가 대비 매수가 위치는 {(entry_price / session_high - 1) * 100:+.1f}%입니다.",
            "다음 거래일까지 보유한 체결 내역이 확인됩니다.",
            *extra,
            *((turnover_context,) if turnover_context else ()),
        ), warnings=tuple(lesson_warnings)), subtype)
    if decline_from_session_high <= -0.08 and rebound >= 0.02:
        subtype = "분봉 투매성 급락 후보"
        extra = ()
        if pullback_candidate:
            subtype = "분봉 투매성 급락 구간 · 눌림 반등 후보"
            extra = ("급락 이전 상승·조정 구조도 함께 나타나므로 낙주와 눌림 형태를 함께 표시합니다.",)
        return _lesson_context(TradeSetupClassification("낙주", 80, (
            f"당일 고점 대비 {decline_from_session_high * 100:.1f}% 급락한 구간에서 진입했습니다.",
            f"당일 저가에서 {rebound * 100:.1f}% 반등해 프로그램의 분봉 낙주 후보 조건에 해당합니다.",
            *extra,
        ), warnings=("고점 대비 8%·저가 대비 2%는 강의의 고정 공식이 아니라 프로그램의 탐색 기준입니다.",)), subtype)
    if decline_from_session_high <= -0.08:
        return _lesson_context(TradeSetupClassification("낙주", 55, (
            f"당일 고점 대비 {decline_from_session_high * 100:.1f}% 급락한 구간에서 진입했습니다.",
            f"당일 저가 대비 반등은 {rebound * 100:.1f}%로 아직 뚜렷하지 않습니다.",
        ), warnings=(
            "6강 낙주 반등을 시도한 진입일 수 있지만 투매가 끝나기 전에 선진입했을 가능성이 있습니다.",
            "고점 대비 8%는 강의의 고정 공식이 아니라 프로그램의 탐색 기준입니다.",
        )), "투매 진행 중 선진입 후보")
    if recent_peak and decline_from_recent_peak <= -0.15 and rebound >= 0.01:
        subtype = "일봉 과대낙폭 반등 후보"
        extra = ()
        confidence = 62
        if breakout_candidate:
            subtype = "일봉 과대낙폭 구간 · 30분 고점 돌파 후보"
            confidence = 68
            extra = (f"진입 순간에는 직전 30분 고점 {prior_high:,.0f}원 돌파 형태도 함께 확인됩니다.",)
        elif pullback_recovered:
            subtype = "일봉 과대낙폭 구간 · 눌림 후 재상승 후보"
            confidence = 66
            extra = ("진입 순간에는 눌림 뒤 재상승 형태도 함께 확인됩니다.",)
        elif pullback_candidate:
            subtype = "일봉 과대낙폭 구간 · 눌림 선진입 후보"
            extra = ("진입 순간에는 눌림 구간이지만 재상승 확인은 충분하지 않습니다.",)
        return _lesson_context(TradeSetupClassification("과대낙폭", confidence, (
            f"최근 20거래일 고점 {recent_peak:,.0f}원보다 {abs(decline_from_recent_peak) * 100:.1f}% 낮은 가격에서 진입했습니다.",
            f"당일 저가 대비 {rebound * 100:.1f}% 반등해 프로그램의 일봉 과대낙폭 후보 조건에 해당합니다.",
            *extra,
        ), warnings=(
            "최근 20거래일 고점 대비 15%는 강의의 고정 공식이 아니라 프로그램의 탐색 기준입니다.",
            "최근 시장 주도주였고 상승 논리가 유지되는지는 저장된 가격만으로 확인할 수 없습니다.",
        )), subtype)
    if breakout_candidate:
        excess = (entry_price / prior_high - 1) * 100
        lecture_warnings = []
        if not (box_candidate or triangle_candidate or w_candidate):
            lecture_warnings.append(
                "직전 고점 돌파는 확인했지만 2강의 박스권·삼각수렴·W형 매물 소화는 자동 확인되지 않았습니다."
            )
        if lecture_turnover_hits < 2:
            lecture_warnings.append(
                "매수 직전 거래대금의 지속성이 약합니다. 40억 원은 강의의 경험적 예시이며 절대 기준은 아닙니다."
            )
        if opening_gap >= 0.08:
            lecture_warnings.append(
                f"정규장 시가가 전일 종가보다 {opening_gap * 100:.1f}% 높아 2강의 높은 시초가 위험 구간입니다."
            )
        if sharp_rise:
            lecture_warnings.append(
                "직전 짧은 구간에 10% 이상 급등해 2강의 가파른 상승 뒤 재돌파 위험을 확인해야 합니다."
            )
        if immediate_rebreak_after_drop:
            lecture_warnings.append(
                "직전 고점에서 4.5% 이상 밀린 뒤 곧바로 재돌파한 형태여서 매물 소화 부족을 확인해야 합니다."
            )
        if w_candidate:
            breakout_subtype = "W형 회복 후 고점 돌파 후보"
        elif triangle_candidate:
            breakout_subtype = "삼각수렴 후 고점 돌파 후보"
        elif box_candidate:
            breakout_subtype = "박스권 고점 돌파 후보"
        else:
            breakout_subtype = "직전 고점 돌파 후보"
        if excess > 1.0:
            return _lesson_context(TradeSetupClassification("주도주 돌파", 60, (
                f"직전 30분 고점 {prior_high:,.0f}원을 넘긴 뒤 매수가 {entry_price:,.0f}원에 진입했습니다.",
                f"직전 고점보다 {excess:.2f}% 높은 위치여서 최초 돌파보다 돌파 후 확장 구간에 가깝습니다.",
                *((turnover_context,) if turnover_context else ()),
            ), warnings=(
                "2강의 정확한 돌파 지점을 지나 가격이 확장된 뒤 진입했을 가능성을 확인해야 합니다.",
                *lecture_warnings,
            )), "돌파 후 확장 진입 후보")
        structured_breakout = box_candidate or triangle_candidate or w_candidate
        confidence = 84 if structured_breakout and lecture_turnover_hits >= 2 else 72
        return _lesson_context(TradeSetupClassification("주도주 돌파", confidence, (
            f"직전 30분 고점 {prior_high:,.0f}원 부근을 매수가 {entry_price:,.0f}원으로 돌파했습니다.",
            f"직전 고점 대비 진입 위치는 {excess:+.2f}%입니다.",
            *((turnover_context,) if turnover_context else ()),
        ), warnings=tuple(lecture_warnings)), breakout_subtype)
    if pullback_recovered:
        return _lesson_context(TradeSetupClassification("주도주 돌파", 76, (
            f"시가 이후 고점까지 {rise_before * 100:.1f}% 상승한 뒤 진입했습니다.",
            f"2강의 눌림 후 재상승 후보입니다. 직전 고점 대비 {drawdown * 100:.1f}% 조정, 저가 대비 {post_peak_rebound * 100:.1f}% 반등, 조정폭의 {recovery_ratio * 100:.0f}%를 회복했습니다.",
        )), "눌림 후 재상승 후보")
    if rise_before >= 0.05 and -0.02 <= drawdown < -0.002:
        if box_candidate:
            return _lesson_context(TradeSetupClassification("주도주 돌파", 58, (
                f"시가 이후 {rise_before * 100:.1f}% 상승한 뒤 최근 5개 완성봉이 {consolidation_range * 100:.1f}% 범위에서 횡보했습니다.",
                f"직전 고점보다 {abs(drawdown) * 100:.1f}% 아래에서 매수해 돌파 확인 전 선진입으로 분류했습니다.",
                *((turnover_context,) if turnover_context else ()),
            ), warnings=("2강의 박스권 상단 돌파가 확인되기 전에 진입했으므로 이후 돌파 실패 여부를 확인해야 합니다.",)), "박스권 상단 선진입 후보")
        if sharp_rise or rise_before >= 0.10:
            return _lesson_context(TradeSetupClassification("주도주 돌파", 52, (
                f"시가 이후 이미 {rise_before * 100:.1f}% 상승한 종목을 고점에서 {abs(drawdown) * 100:.1f}% 이내에서 매수했습니다.",
                "박스권·수렴 뒤 돌파는 확인되지 않아 급등 후 고점 추격 후보로 분류했습니다.",
                *((turnover_context,) if turnover_context else ()),
            ), warnings=("2강의 급등 후 후기 발산 구간일 수 있어 추가 매수보다 매물 출회를 경계해야 합니다.",)), "급등 후 고점 추격 후보")
        return _lesson_context(TradeSetupClassification("주도주 돌파", 55, (
            f"시가 이후 이미 {rise_before * 100:.1f}% 상승한 종목을 고점에서 {abs(drawdown) * 100:.1f}% 이내에서 매수했습니다.",
            "2강 돌파매매 후보이지만 직전 고점 돌파나 충분한 눌림·반등은 확인되지 않아 신뢰도를 낮췄습니다.",
        ), warnings=("큰 급등 뒤 고점 진입일 수 있어 2강의 후기 발산·추격 구간 여부를 확인해야 합니다.",)), "고점 근접 진입")
    if pullback_candidate:
        return _lesson_context(TradeSetupClassification("주도주 돌파", 48, (
            f"시가 이후 고점까지 {rise_before * 100:.1f}% 상승한 뒤 고점 대비 {drawdown * 100:.1f}% 조정 구간에서 진입했습니다.",
            f"조정 저점에서 {post_peak_rebound * 100:.1f}% 반등했고 조정폭의 {max(0.0, recovery_ratio) * 100:.0f}%만 회복해 재상승 확인은 부족합니다.",
        ), warnings=("2강의 눌림 구간을 시도한 모양이지만 재상승 확인 전에 진입했을 가능성이 있습니다.",)), "눌림 구간 선진입 후보")
    if minutes_from_opening_reference > 10 and rise_before >= 0.03 and -0.08 <= drawdown <= -0.01:
        return _lesson_context(TradeSetupClassification("주도주 돌파", 40, (
            f"시가 이후 고점까지 {rise_before * 100:.1f}% 상승한 뒤 고점 대비 {drawdown * 100:.1f}% 조정된 시점에 진입했습니다.",
            "고점 이후 완성된 분봉이 부족해 눌림 지속·저점 형성·재상승 여부는 아직 확인되지 않습니다.",
        ), warnings=("2강 눌림매매를 시도했을 가능성은 있지만 조정 직후 진입이라 자동 확정할 수 없습니다.",)), "상승 후 조정 즉시 진입 후보")
    if minutes_from_opening_reference <= 10:
        opening_change = entry_price / opening_reference - 1 if opening_reference else 0.0
        if opening_change >= 0.05:
            return TradeSetupClassification("기타", 42, (
                f"{opening_label} 시작({opening_reference_text}) 후 {minutes_from_opening_reference}분에 첫 매수했습니다.",
                f"장 시작가 대비 {opening_change * 100:+.1f}% 급등한 위치입니다.",
                "선행 분봉이 부족해 주도주 돌파인지 신규주 급등인지 확정하지 않습니다.",
            ), "장 초반 급등 진입 후보", (), ("장 초반 급등만으로 강의 유형과 실제 돌파 기준 가격을 확정할 수 없습니다.",))
        return TradeSetupClassification("기타", 35, (
            f"{opening_label} 시작({opening_reference_text}) 후 {minutes_from_opening_reference}분에 첫 매수했습니다.",
            f"해당 장 시작가 대비 매수가 위치는 {(entry_price / opening_reference - 1) * 100:+.1f}%입니다." if opening_reference else "시가를 확인하지 못했습니다.",
            "장 초반이라는 사실만으로는 4강 신규주인지 1~3강 주도주 돌파인지 구분할 수 없습니다.",
        ), "장 초반 방향 탐색 후보", (), ("장 초반 가격만으로 매매 강의 유형을 확정할 수 없습니다.",))
    distance_from_session_high = entry_price / session_high - 1 if session_high else 0.0
    if session_high and distance_from_session_high >= -0.01:
        return TradeSetupClassification("기타", 45, (
            f"당일 고점 대비 {distance_from_session_high * 100:+.1f}% 위치에서 진입했습니다.",
            "고점 근접 사실은 확인되지만 상승폭과 돌파 구조가 부족해 주도주 돌파로 확정하지 않습니다.",
        ), "당일 고점 근접 진입", (), ("당시 시장 주도주·테마 여부와 실제 돌파 기준 가격은 확인할 수 없습니다.",))
    data_warning = ("과거 일봉이 없어 신규주·과대낙폭 여부는 판정에서 제외했습니다.",) if not prior_daily else ()
    return TradeSetupClassification("기타", 42, (
        "현재 규칙에서 1~6강의 주제 조건이 명확하지 않습니다.",
        f"기준가 대비 {change * 100:+.1f}%, 당일 고가 대비 {(entry_price / session_high - 1) * 100:+.1f}% 위치입니다.",
    ), warnings=data_warning)
