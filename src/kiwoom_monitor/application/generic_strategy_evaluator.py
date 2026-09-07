"""검토·승인된 사용자 전략팩을 안전한 측정값 비교식으로 평가한다."""

from __future__ import annotations

from datetime import datetime
from operator import eq, ge, gt, le, lt

from .strategy_pack import StrategyPackManifest
from .strategy_pack_extraction import ExtractedStrategyDraft, StrategyRuleDraft
from .trade_journal_summary import TradeEpisode
from .trade_setup_classification import TradeSetupClassification


def evaluate_strategy_pack(
    manifest: StrategyPackManifest, draft: ExtractedStrategyDraft, episode: TradeEpisode,
    minute_rows: tuple[tuple[object, ...], ...], daily_rows: tuple[tuple[object, ...], ...] = (),
) -> TradeSetupClassification | None:
    metrics = strategy_metrics(episode, minute_rows, daily_rows)
    entry_rules = tuple(rule for rule in draft.rules if rule.category == "진입 조건" and _mapped(rule))
    if not entry_rules:
        return None
    matched_entry = tuple(rule for rule in entry_rules if _matches(rule, metrics))
    risk_rules = tuple(rule for rule in draft.rules if rule.category in {"제외 조건", "위험관리"} and _mapped(rule))
    matched_risk = tuple(rule for rule in risk_rules if _matches(rule, metrics))
    ratio = len(matched_entry) / len(entry_rules)
    # 우연히 한 조건만 맞은 전략팩이 기존 전문 판정을 덮지 않도록 최소 2개 또는 전체 일치를 요구한다.
    enough_evidence = len(matched_entry) >= 2 or (len(entry_rules) == 1 and ratio == 1.0)
    if not enough_evidence or ratio < 0.6:
        return None
    confidence = max(35, min(92, round(45 + ratio * 45 - len(matched_risk) * 8)))
    setup_type = next((value for value in manifest.setup_types if value and value != "기타"), manifest.setup_types[0] if manifest.setup_types else "기타")
    evidence = tuple(_evidence(rule, metrics) for rule in matched_entry)
    warnings = tuple(f"전략팩 주의조건 일치: {rule.text}" for rule in matched_risk)
    unavailable = tuple(
        f"측정값 미확보: {rule.text}" for rule in (*entry_rules, *risk_rules)
        if metrics.get(rule.metric_key) is None
    )
    return TradeSetupClassification(
        setup_type, confidence, evidence,
        f"{manifest.name} · {len(matched_entry)}/{len(entry_rules)}개 진입조건 일치",
        warnings, unavailable,
    )


def strategy_metrics(
    episode: TradeEpisode, minute_rows: tuple[tuple[object, ...], ...],
    daily_rows: tuple[tuple[object, ...], ...] = (),
) -> dict[str, float | None]:
    entry = next((fill for fill in episode.fills if fill.side == "매수"), episode.fills[0])
    parsed = [(datetime.fromisoformat(str(row[0])), row) for row in minute_rows]
    same_day = [(moment, row) for moment, row in parsed if moment.date() == entry.filled_at.date() and moment <= entry.filled_at]
    if not same_day:
        return {key: None for key in (
            "entry_from_open_pct", "breakout_over_30m_high_pct", "pullback_from_session_high_pct",
            "rebound_from_pullback_low_pct", "entry_trade_value_eok", "trade_value_vs_prior_ratio",
            "recent_high_distance_pct", "entry_time_hhmm",
        )}
    opening = float(same_day[0][1][1]); entry_price = float(entry.price)
    prior_30 = same_day[-31:-1] if len(same_day) > 1 else ()
    prior_high = max((float(row[2]) for _, row in prior_30), default=None)
    session_high = max(float(row[2]) for _, row in same_day)
    peak_index = max(range(len(same_day)), key=lambda index: float(same_day[index][1][2]))
    post_peak_low = min(float(row[3]) for _, row in same_day[peak_index:])
    entry_row = same_day[-1][1]
    prior_values = [float(row[6]) for _, row in same_day[-6:-1] if float(row[6]) >= 0]
    recent_high = max((float(row[2]) for row in daily_rows[-20:]), default=None)
    return {
        "entry_from_open_pct": (entry_price / opening - 1) * 100 if opening else None,
        "breakout_over_30m_high_pct": (entry_price / prior_high - 1) * 100 if prior_high else None,
        "pullback_from_session_high_pct": max(0.0, (1 - entry_price / session_high) * 100) if session_high else None,
        "rebound_from_pullback_low_pct": (entry_price / post_peak_low - 1) * 100 if post_peak_low else None,
        "entry_trade_value_eok": float(entry_row[6]),
        "trade_value_vs_prior_ratio": float(entry_row[6]) / (sum(prior_values) / len(prior_values)) if prior_values and sum(prior_values) else None,
        "recent_high_distance_pct": max(0.0, (1 - entry_price / recent_high) * 100) if recent_high else None,
        "entry_time_hhmm": float(entry.filled_at.hour * 100 + entry.filled_at.minute),
    }


def _mapped(rule: StrategyRuleDraft) -> bool:
    return bool(rule.automatable and rule.metric_key and rule.threshold is not None)


def _matches(rule: StrategyRuleDraft, metrics: dict[str, float | None]) -> bool:
    actual = metrics.get(rule.metric_key)
    if actual is None or rule.threshold is None:
        return False
    operation = {">=": ge, "<=": le, ">": gt, "<": lt, "==": eq}.get(rule.comparison)
    return bool(operation and operation(actual, rule.threshold))


def _evidence(rule: StrategyRuleDraft, metrics: dict[str, float | None]) -> str:
    actual = metrics.get(rule.metric_key)
    return f"{rule.text} (측정 {actual:.2f} {rule.comparison} 기준 {rule.threshold:g})" if actual is not None and rule.threshold is not None else rule.text
