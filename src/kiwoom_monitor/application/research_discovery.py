"""Mine observed minute-bar combinations and evaluate them as research-only rules."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta
from functools import cached_property
import hashlib
import json
from itertools import combinations

from kiwoom_monitor.application.breakout_strategy import evaluate_factor_entry_bar
from kiwoom_monitor.application.research_execution import estimate_buy_cash_won, _slipped_price
from kiwoom_monitor.application.research_factors import FactorValue
from kiwoom_monitor.application.research_replay import ResearchReplayCursor
from kiwoom_monitor.domain.research_contract import DISCOVERY_FAMILY
from kiwoom_monitor.domain.strategy_contract import REGULAR_SESSION_EXIT_POLICY, validate_session_exit_policy
from kiwoom_monitor.domain.time_basis import as_kst


FEATURE_BINS = {
    "return_bps": (-50, -10, 10, 50),
    "volume_ratio_pct": (75, 125, 200),
    "turnover_ratio_pct": (75, 125, 200),
    "range_bps": (30, 80, 160),
    "time_kst": (600, 690, 810, 900),  # Minutes after midnight.
}


@dataclass(frozen=True)
class DiscoveryRisk:
    quantity: int
    capital_won: int
    stop_loss_bps: int
    target_bps: int
    max_hold_minutes: int
    signal_valid_seconds: int
    cooldown_seconds: int
    session_exit_buffer_seconds: int

    def __post_init__(self):
        values = tuple(getattr(self, field.name) for field in fields(DiscoveryRisk))
        if any(type(value) is not int or value < 0 for value in values):
            raise ValueError("discovery risk fields must be nonnegative integers")
        if (min(self.quantity, self.capital_won, self.max_hold_minutes, self.signal_valid_seconds) <= 0
                or not 0 < self.stop_loss_bps < 10_000 or not 0 < self.target_bps < 10_000):
            raise ValueError("discovery requires explicit positive research capital, quantity, hold and price limits")
        validate_session_exit_policy(REGULAR_SESSION_EXIT_POLICY, self.session_exit_buffer_seconds)


@dataclass(frozen=True)
class DiscoveryStrategy(DiscoveryRisk):
    conditions: tuple[tuple[str, int], ...]

    family = DISCOVERY_FAMILY
    strategy_version = "v1"
    rank_persistence_enabled = False
    session_exit_policy = REGULAR_SESSION_EXIT_POLICY

    def __post_init__(self):
        super().__post_init__()
        if (len(self.conditions) != 2 or any(len(row) != 2 for row in self.conditions)
                or any(not isinstance(name, str) or name not in FEATURE_BINS or type(bucket) is not int
                       or not 0 <= bucket <= len(FEATURE_BINS[name]) for name, bucket in self.conditions)
                or self.conditions[0][0] >= self.conditions[1][0]):
            raise ValueError("discovery requires two distinct versioned feature buckets")

    @cached_property
    def conditions_hash(self):
        return hashlib.sha256(json.dumps(self.conditions, separators=(",", ":")).encode()).hexdigest()

    def to_dict(self):
        return {**asdict(self), "conditions": [list(row) for row in self.conditions]}

    @classmethod
    def from_dict(cls, document):
        return cls(**{**document, "conditions": tuple(tuple(row) for row in document["conditions"])})


def _features(bar, history):
    if bar.market_phase != "CONTINUOUS" or min(bar.close, bar.volume) <= 0:
        return {}, ()
    start = datetime.fromisoformat(bar.bar_start)
    if datetime.fromisoformat(bar.bar_end) - start != timedelta(minutes=1):
        return {}, ()
    previous = next((row for row in history if row.market_phase == "CONTINUOUS"
                     and row.research_session == bar.research_session
                     and datetime.fromisoformat(row.bar_end) == start
                     and datetime.fromisoformat(row.bar_start) == start - timedelta(minutes=1)), None)
    if previous is None or previous.close <= 0:
        return {}, ()
    kst = as_kst(start)
    values = {
        "return_bps": (bar.close - previous.close) * 10_000 // previous.close,
        "range_bps": (bar.high - bar.low) * 10_000 // previous.close,
        "time_kst": kst.hour * 60 + kst.minute,
    }
    if previous.volume > 0:
        values["volume_ratio_pct"] = bar.volume * 100 // previous.volume
    if previous.trade_value_million_won > 0:
        values["turnover_ratio_pct"] = bar.trade_value_million_won * 100 // previous.trade_value_million_won
    return {name: sum(value >= edge for edge in FEATURE_BINS[name]) for name, value in values.items()}, (
        previous.revision_id, bar.revision_id)


def evaluate_discovered_pattern(*, run_id, bar, history, config, state):
    values, refs = _features(bar, history)
    factor = FactorValue("discovered_minute_pattern", "v1", config.conditions_hash, bar.code, bar.available_at, bar.available_at,
        {"buckets": values, "matched": all(values.get(name) == bucket for name, bucket in config.conditions),
         "reference_revision_id": bar.revision_id} if refs else None,
        "valid" if refs else "missing", "" if refs else "contiguous_complete_minute_inputs_missing", refs)
    return evaluate_factor_entry_bar(run_id=run_id, evaluation_bar=bar, universe_frames=(), config=config,
        state=state, factors=(factor,), strategy_id="discovered_minute_pattern", setup=DISCOVERY_FAMILY,
        signal_key="matched", negative_reason="discovery_pattern_not_matched", positive_reason="discovery_pattern_matched",
        require_universe=False)


@dataclass
class _PatternCount:
    occurrences: int = 0
    labeled: int = 0
    net_bps: int = 0
    wins: int = 0

    def to_dict(self):
        return {**asdict(self), "mean_net_bps": self.net_bps / self.labeled if self.labeled else None}


def scan_patterns(observations, *, fold, purge_seconds, risk, cost, minimum_samples, max_candidates, checkpoint,
                  relative_shortlist=False):
    """One chronological scan. Use later TRAIN bars only as a rough one-minute outcome label."""
    start = datetime.fromisoformat(fold.start)
    end = datetime.fromisoformat(fold.end) - timedelta(seconds=purge_seconds)
    cursor = ResearchReplayCursor(observations, session_profile="krx-regular/v1", checkpoint=checkpoint,
                                  universe_kinds=())
    counts = defaultdict(_PatternCount)
    last_start = {}
    pending = defaultdict(list)
    total = _PatternCount()
    for observation in observations:
        checkpoint()
        if observation.get("kind") != "minute_bar" or observation.get("venue") != "KRX":
            continue
        bar, history, _ = cursor.advance_for_observation(observation, recent_first=True)
        if bar is None:
            continue
        bar_start, cut = datetime.fromisoformat(bar.bar_start), datetime.fromisoformat(bar.available_at)
        if bar.code in last_start and bar_start <= last_start[bar.code]:
            continue
        last_start[bar.code] = bar_start
        if cut >= end:
            break
        keep = []
        for target, session, keys in pending[bar.code]:
            if session != bar.research_session or bar_start > target:
                continue
            if bar_start < target:
                keep.append((target, session, keys))
                continue
            if (bar.market_phase != "CONTINUOUS" or bar.volume < risk.quantity or min(bar.open, bar.close) <= 0
                    or datetime.fromisoformat(bar.bar_end) - bar_start != timedelta(minutes=1)):
                continue
            entry = estimate_buy_cash_won(bar.open, risk.quantity, cost)
            if entry > risk.capital_won:
                continue
            gross = _slipped_price(bar.close, "SELL", cost.slippage_bps) * risk.quantity
            net = gross - sum(cost.fees_won(gross, sell=True)) - entry
            for row in (total, *(counts[key] for key in keys)):
                row.labeled += 1
                row.net_bps += net * 10_000 // entry
                row.wins += net > 0
        pending[bar.code] = keep
        if bar_start < start:
            continue
        values, _ = _features(bar, history)
        if not values:
            continue
        keys = tuple(combinations(sorted(values.items()), 2))
        total.occurrences += 1
        for key in keys:
            counts[key].occurrences += 1
        target = cut.replace(second=0, microsecond=0)
        if target < cut:
            target += timedelta(minutes=1)
        if target <= cut + timedelta(seconds=risk.signal_valid_seconds) and target + timedelta(minutes=1) < end:
            pending[bar.code].append((target, bar.research_session, keys))

    baseline = total.to_dict()["mean_net_bps"]
    above_baseline = [(key, stats) for key, stats in counts.items() if stats.labeled >= minimum_samples
                      and stats.net_bps * total.labeled > total.net_bps * stats.labeled]
    positive = [(key, stats) for key, stats in above_baseline if stats.net_bps > 0]
    qualified = above_baseline if relative_shortlist else positive
    qualified.sort(key=lambda item: (-item[1].net_bps / item[1].labeled, -item[1].labeled, item[0]))
    return {"opportunities": total.to_dict(), "observed_patterns": len(counts), "qualified_patterns": len(qualified),
            "positive_qualified_patterns": len(positive),
            "candidates": [{"strategy": DiscoveryStrategy(**asdict(risk), conditions=key).to_dict(),
                            "screen": {**stats.to_dict(), "baseline_mean_net_bps": baseline}}
                           for key, stats in qualified[:max_candidates]]}
