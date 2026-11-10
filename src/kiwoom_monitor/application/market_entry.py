"""Point-in-time market context and explicitly supplied Paper entry comparisons."""
from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping
from datetime import datetime
import hashlib
import json

from kiwoom_monitor.application.market_session_schedule import krx_regular_session_hours
from kiwoom_monitor.domain.strategy_contract import LeaderPositionStep
from kiwoom_monitor.domain.time_basis import as_kst


AXES = ("turnover_million_won", "net_advancers", "theme_concentration_ppm", "sustained_rise_ppm")
DIRECTIONS = {"bullish", "bearish", "mixed"}
ENTRY_TRIAL = "market_entry_trial/v1"
REGIME_TRIAL = "market_entry_trial/v2"


def _time(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("market context requires an offset-aware time")
    return as_kst(parsed)


def market_entry_context(cut, comparison, turnover, shadow_market, *, max_age_seconds, market_breadth=None):
    """Collect the four axes without converting missing observations into zeroes."""
    cut = as_kst(cut)
    markets = ("kospi", "kosdaq")
    breadth = dict(comparison.get("observations", {}))
    for market, row in (market_breadth or {}).items():
        at = _time(row["observed_at"])
        up, down = row.get("advancing_count"), row.get("declining_count")
        if (at.date() == cut.date() and 0 <= (cut - at).total_seconds() <= max_age_seconds
                and type(up) is int and type(down) is int and min(up, down) >= 0 and up + down > 0):
            breadth[market] = {"up": up, "down": down, "flat": row.get("flat_count"), "observed_at": at.isoformat()}
    net = (sum(breadth[key]["up"] - breadth[key]["down"] for key in markets)
           if all(key in breadth for key in markets) else None)
    valid = {}
    for market, row in turnover.items():
        observed = _time(row["observed_at"])
        value = row.get("cumulative_trade_value_million_won")
        if observed.date() == cut.date() and 0 <= (cut - observed).total_seconds() <= max_age_seconds:
            if type(value) is int and value >= 0:
                valid[market] = row
    total = (sum(valid[key]["cumulative_trade_value_million_won"] for key in markets)
             if all(key in valid for key in markets) else None)
    facts, market_inferences = {}, {}
    if (shadow_market and _time(shadow_market["available_at"]) <= cut < _time(shadow_market["expires_at"])
            and _time(shadow_market["available_at"]).date() == cut.date()):
        facts = shadow_market.get("facts", {})
        market_inferences = shadow_market.get("inferences", {})
    premarket = comparison.get("premarket") if comparison.get("reason") != "premarket_brief_expired" else None
    return {
        "factor_id": "market_entry_context/v1", "as_of": cut.isoformat(),
        "premarket_direction": (premarket or {}).get("direction"), "premarket": premarket,
        "turnover_million_won": total, "net_advancers": net,
        "theme_concentration_ppm": facts.get("leading_theme_concentration_ppm"),
        "sustained_rise_ppm": facts.get("mature_breakout_success_ppm"),
        "mature_rise_sample_count": facts.get("mature_breakout_denominator"),
        "leading_theme": facts.get("leading_theme"), "theme_scope": facts.get("venue"),
        "observed_market_type": market_inferences.get("market_type", "UNKNOWN"),
        "market_turnover": valid, "market_breadth": breadth,
        "shadow_input_refs": shadow_market.get("evidence_refs", ()) if facts else (),
        "missing_axes": [key for key, value in zip(AXES, (total, net, facts.get("leading_theme_concentration_ppm"),
                                                           facts.get("mature_breakout_success_ppm"))) if value is None],
    }


class MarketEntryTrial:
    """Frozen Paper-only policies, with optional breakout management by entry regime."""

    def __init__(self, document):
        fields = {"version", "strategy_policies", "observations"}
        if isinstance(document, Mapping) and document.get("version") == REGIME_TRIAL and "profit_takes" in document:
            fields.add("profit_takes")
        if (not isinstance(document, Mapping) or set(document) != fields
                or document["version"] not in (ENTRY_TRIAL, REGIME_TRIAL) or not isinstance(document["strategy_policies"], Mapping)
                or not isinstance(document["observations"], list)):
            raise ValueError("invalid market entry comparison format")
        self.version = document["version"]
        self._profit_takes = self._profit_take_steps(document.get("profit_takes", []))
        if self.version == REGIME_TRIAL:
            self._policies = self._regime_policies(document["strategy_policies"])
            rows = self._regime_rows(document["observations"])
        else:
            self._policies = self._entry_policies(document["strategy_policies"])
            rows = self._entry_rows(document["observations"])
        rows.sort(key=lambda item: item[0])
        self._times = tuple(item[0] for item in rows)
        if not rows or len(set(self._times)) != len(rows) or not self._policies:
            raise ValueError("market trial requires unique observations and at least one strategy policy")
        self._rows = tuple(rows)
        self.sha256 = hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":"),
                                                ensure_ascii=False, allow_nan=False).encode()).hexdigest()

    @staticmethod
    def _entry_policies(policies):
        result = {}
        for family, policy in policies.items():
            if (not isinstance(policy, Mapping) or set(policy) != {"directions", "ranges", "match_bps", "miss_bps", "unknown_bps"}
                    or not isinstance(policy["directions"], list) or not isinstance(policy["ranges"], Mapping)
                    or any(not isinstance(value, str) for value in policy["directions"])):
                raise ValueError("market entry policy requires explicit thresholds and outcomes")
            if (not policy["directions"] or not set(policy["directions"]) <= DIRECTIONS
                    or not policy["ranges"] or not set(policy["ranges"]) <= set(AXES)
                    or any(type(policy[key]) is not int or not 0 <= policy[key] <= 10_000
                           for key in ("match_bps", "miss_bps", "unknown_bps"))):
                raise ValueError("invalid market entry direction, range or position multiplier")
            for bounds in policy["ranges"].values():
                if (not isinstance(bounds, list) or len(bounds) != 2 or bounds == [None, None]
                        or any(value is not None and type(value) is not int for value in bounds)
                        or all(value is not None for value in bounds) and bounds[0] > bounds[1]):
                    raise ValueError("each market threshold requires ordered integer bounds")
            result[family] = json.loads(json.dumps(policy))
        return result

    @staticmethod
    def _entry_rows(observations):
        rows = []
        refs = set()
        for row in observations:
            if not isinstance(row, Mapping) or set(row) != {"revision_id", "available_at", "expires_at", "premarket_direction",
                                                              "premarket_available_at", *AXES}:
                raise ValueError("invalid frozen market context observation")
            ready, expires = (_time(row[key]) for key in ("available_at", "expires_at"))
            premarket = _time(row["premarket_available_at"]) if row["premarket_available_at"] else None
            direction = row["premarket_direction"]
            if (not isinstance(row["revision_id"], str) or not row["revision_id"] or row["revision_id"] in refs
                    or ready >= expires or (direction is None) != (premarket is None)
                    or direction is not None and (not isinstance(direction, str) or direction not in DIRECTIONS | {"uncertain"}
                       or premarket.date() != ready.date()
                       or premarket > ready or premarket.time() >= krx_regular_session_hours(ready.date())[0])
                    or any(row[axis] is not None and type(row[axis]) is not int for axis in AXES)
                    or row[AXES[0]] is not None and row[AXES[0]] < 0
                    or any(row[axis] is not None and not 0 <= row[axis] <= 1_000_000 for axis in AXES[2:])):
                raise ValueError("market context has invalid availability, revision or units")
            refs.add(row["revision_id"])
            rows.append((ready, expires, dict(row)))
        return rows

    @staticmethod
    def _regime_policies(policies):
        from kiwoom_monitor.application.section12_minute_research import BREAKOUT_STUDY_FAMILIES

        result = {}
        for family, rules in policies.items():
            if family not in BREAKOUT_STUDY_FAMILIES or not isinstance(rules, Mapping) or "UNKNOWN" not in rules:
                raise ValueError("regime trial requires a breakout family and an explicit UNKNOWN rule")
            for regime, rule in rules.items():
                if (not isinstance(regime, str) or not regime.strip() or not isinstance(rule, Mapping)
                        or set(rule) != {"size_bps", "exit_on_regimes"}
                        or type(rule["size_bps"]) is not int or not 0 <= rule["size_bps"] <= 10_000
                        or not isinstance(rule["exit_on_regimes"], list)
                        or any(not isinstance(value, str) or not value.strip() or value in {"UNKNOWN", regime}
                               for value in rule["exit_on_regimes"])):
                    raise ValueError("regime rule requires an explicit size and confirmed exit regimes")
            result[family] = {regime: {**rule, "exit_on_regimes": tuple(rule["exit_on_regimes"])}
                              for regime, rule in rules.items()}
        return result

    @staticmethod
    def _regime_rows(observations):
        rows, refs = [], set()
        for row in observations:
            if (not isinstance(row, Mapping) or set(row) != {
                    "revision_id", "available_at", "expires_at", "regime", "evidence_refs"}
                    or any(not isinstance(row[key], str) or not row[key].strip()
                           for key in ("revision_id", "available_at", "expires_at", "regime"))
                    or not isinstance(row["evidence_refs"], list)
                    or any(not isinstance(ref, str) or not ref.strip() for ref in row["evidence_refs"])):
                raise ValueError("invalid frozen regime observation")
            ready, expires = (_time(row[key]) for key in ("available_at", "expires_at"))
            if ready >= expires or row["revision_id"] in refs:
                raise ValueError("regime observation requires ordered availability and a unique revision")
            refs.add(row["revision_id"])
            rows.append((ready, expires, {**row, "evidence_refs": tuple(row["evidence_refs"])}))
        return rows

    @staticmethod
    def _profit_take_steps(observations):
        if not isinstance(observations, list):
            raise ValueError("profit_takes must be a list of explicit price levels")
        steps = {}
        for row in observations:
            if (not isinstance(row, Mapping) or set(row) != {"symbol", "available_at", "revision_id",
                    "reference_price", "quantity", "reason_ref"} or not isinstance(row["available_at"], str)
                    or not isinstance(row["symbol"], str) or len(row["symbol"]) != 6 or not row["symbol"].isascii()
                    or not row["symbol"].isalnum()):
                raise ValueError("invalid breakout profit-taking evidence")
            ready = _time(row["available_at"])
            key = (row["symbol"], ready.date())
            if key in steps:
                raise ValueError("each symbol and session requires one explicit profit-taking level")
            steps[key] = ready, LeaderPositionStep(row["revision_id"], "REDUCE", "price_at_or_above",
                                                    row["reference_price"], row["quantity"], row["reason_ref"])
        return steps

    def profit_take_at(self, symbol, entry_cut):
        cut = as_kst(entry_cut)
        found = self._profit_takes.get((symbol, cut.date()))
        return found[1] if found is not None and found[0] <= cut else None

    def validate_family(self, family):
        if family not in self._policies:
            raise ValueError(f"market trial has no explicit policy for {family}")

    def _observation_at(self, cut):
        index = bisect_right(self._times, cut) - 1
        if index >= 0:
            ready, expires, found = self._rows[index]
            if ready.date() == cut.date() and cut < expires:
                return ready, found
        return None, None

    def assess(self, family, cut):
        cut = as_kst(cut)
        _, row = self._observation_at(cut)
        policy = self._policies[family]
        if self.version == REGIME_TRIAL:
            observed = row["regime"] if row is not None else "UNKNOWN"
            regime = observed if observed in policy else "UNKNOWN"
            return {"factor_id": self.version, "status": regime, "observed_regime": observed,
                    **policy[regime], "context": row, "as_of": cut.isoformat()}
        ranges = policy["ranges"]
        if row is None or row["premarket_direction"] in (None, "uncertain") or any(row[key] is None for key in ranges):
            status = "unknown"
        elif row["premarket_direction"] in policy["directions"] and all(
                (low is None or row[key] >= low) and (high is None or row[key] <= high)
                for key, (low, high) in ranges.items()):
            status = "match"
        else:
            status = "miss"
        return {"factor_id": self.version, "status": status, "size_bps": policy[f"{status}_bps"],
                "context": row, "as_of": cut.isoformat()}

    def assess_breakout_exit(self, entry, cut, opened_at):
        cut = as_kst(cut)
        ready, row = self._observation_at(cut)
        if (row is None or ready <= as_kst(opened_at) or row["regime"] == entry["observed_regime"]
                or row["regime"] not in entry["exit_on_regimes"]):
            return None
        return {"factor_id": self.version, "status": row["regime"], "as_of": cut.isoformat(),
                "context": row, "entry_context": entry}
