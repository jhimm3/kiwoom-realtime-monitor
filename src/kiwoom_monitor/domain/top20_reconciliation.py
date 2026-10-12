"""Pure TOP20 half-minute allocation and display quality policy."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from typing import Any

KST = timezone(timedelta(hours=9))
RECONCILED_KIND = "top20_index_reconciled"
SCHEMA = "top20_reconciliation/v1"
ALGORITHM = "minute_bar_half_cohort/v1"
BAR_FIELDS = ("trading_date", "minute", "code", "market", "open", "high", "low", "close",
              "volume", "trade_value_million_won")


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def bar_fingerprint(bars: dict[str, dict[str, Any]]) -> str:
    return fingerprint([{key: bars[minute].get(key) for key in BAR_FIELDS} for minute in sorted(bars)])


def metadata_fingerprint(metadata: dict[str, dict[str, Any]]) -> str:
    names = ("venue", "unit", "value_kind", "completeness", "origin", "source")
    return fingerprint({minute: {key: value.get(key) for key in names}
                        for minute, value in sorted(metadata.items())})


@dataclass(frozen=True)
class Top20ReconciliationInput:
    trading_date: str
    cohorts: dict[str, dict[str, Any]]
    pairs: dict[str, dict[str, Any]]
    eligibility: dict[str, bool | None]
    markets: dict[str, str]
    input_as_of: str
    sources: list[dict[str, Any]] = field(default_factory=list)


def minute_keys(day: str) -> list[str]:
    start = datetime.fromisoformat(day).replace(hour=8)
    return [(start + timedelta(minutes=index)).isoformat(timespec="minutes") for index in range(720)]


def cohort_weights(minute: str, payload: dict[str, Any]) -> dict[str, float]:
    """Last segment wins at a duplicated 00/30 boundary, as in the live collector."""
    segments = payload.get("cohort_segments")
    if not isinstance(segments, (list, tuple)) or not segments:
        raise ValueError("cohort_missing")
    boundaries: dict[int, tuple[str, ...]] = {}
    previous = -1
    for segment in segments:
        if not isinstance(segment, (list, tuple)) or len(segment) != 2:
            raise ValueError("cohort_invalid")
        try:
            at = datetime.fromisoformat(str(segment[0]))
        except ValueError as error:
            raise ValueError("cohort_invalid") from error
        if at.tzinfo is not None:
            at = at.astimezone(KST).replace(tzinfo=None)
        if at.isoformat(timespec="minutes") != minute or at.second not in (0, 30) or at.microsecond:
            raise ValueError("cohort_boundary_invalid")
        if at.second < previous or not isinstance(segment[1], (list, tuple)):
            raise ValueError("cohort_invalid")
        codes = tuple(dict.fromkeys(str(code).strip() for code in segment[1]))
        if not codes or len(codes) > 20 or any(not code for code in codes):
            raise ValueError("cohort_invalid")
        boundaries[at.second] = codes
        previous = at.second
    if 0 not in boundaries:
        raise ValueError("cohort_first_half_missing")
    first = boundaries[0]
    second = boundaries.get(30, first)
    return {code: (int(code in first) + int(code in second)) / 2 for code in sorted(set(first) | set(second))}


def _closed_coverage(value: dict[str, Any], day: str) -> bool:
    return (value.get("kind") == "minute" and value.get("scope") == "full_day"
            and value.get("as_of") == day and value.get("window_closed") is True
            and value.get("session_finalized") is True)


def _valid_bar(bar: dict[str, Any], metadata: dict[str, Any], day: str, code: str, venue: str) -> bool:
    return (bar.get("trading_date") == day and bar.get("code") == code and bar.get("market") == venue
            # Compound OHLCV metadata uses UNKNOWN; the named money column and
            # whitelisted query source define its existing million-won unit.
            and metadata.get("venue") == venue and metadata.get("unit") in {"million_won", "unknown"}
            and metadata.get("completeness") == "complete" and metadata.get("origin") in {"query", "backfilled"}
            and str(metadata.get("source", "")).startswith("kiwoom-ka10080")
            and metadata.get("value_kind") in {"estimated", "actual"})


def pair_evidence(pair: dict[str, Any], day: str, code: str, venue: str) -> tuple[dict[str, float], bool]:
    bars, metadata, coverage = (pair.get("bars", {}), pair.get("metadata", {}), pair.get("coverage", {}))
    valid: dict[str, float] = {}
    if not _closed_coverage(coverage, day):
        return valid, False
    for minute, bar in bars.items():
        if not isinstance(bar, dict) or not _valid_bar(bar, metadata.get(minute, {}), day, code, venue):
            continue
        try:
            value = float(bar["trade_value_million_won"])
        except (KeyError, ValueError, TypeError):
            continue
        if math.isfinite(value) and value >= 0:
            valid[minute] = value
    expected = coverage.get("expected_minutes")
    zero_proven = False
    if (coverage.get("schema_version") == "minute_backfill_coverage/v2"
            and isinstance(expected, list) and expected and len(set(expected)) == len(expected)):
        expected_bars = {key: bars[key] for key in expected if key in bars}
        expected_metadata = {key: metadata[key] for key in expected if key in metadata}
        zero_proven = (set(expected).issubset(valid)
                       and coverage.get("bars_fingerprint") == bar_fingerprint(expected_bars)
                       and coverage.get("metadata_fingerprint") == metadata_fingerprint(expected_metadata))
        if zero_proven:
            # A complete query receipt defines this source; ignore stale extra DB rows.
            valid = {key: valid[key] for key in expected}
    return valid, zero_proven


def _coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def section(values: list[dict[str, Any]]) -> dict[str, Any]:
        missing = [row["minute"] for row in values if row["status"] != "complete"]
        ranges: list[list[str]] = []
        for key in missing:
            if ranges and datetime.fromisoformat(key) - datetime.fromisoformat(ranges[-1][1]) == timedelta(minutes=1):
                ranges[-1][1] = key
            else:
                ranges.append([key, key])
        return {"expected": len(values), "complete": len(values) - len(missing), "missing": len(missing),
                "missing_ranges": ranges}
    return {"full_day": section(rows),
            "regular": section([row for row in rows if "09:00" <= row["minute"][11:16] <= "15:30"])}


def build_top20_reconciliation(inputs: Top20ReconciliationInput, *, available_at: str) -> dict[str, Any]:
    evidence: dict[str, tuple[dict[str, float], bool]] = {}
    sources: list[dict[str, Any]] = list(inputs.sources)
    for subject, pair in sorted(inputs.pairs.items()):
        code, venue = subject.split(":", 1)
        evidence[subject] = pair_evidence(pair, inputs.trading_date, code, venue)
        coverage = dict(pair.get("coverage", {}))
        coverage.pop("completed_at", None)
        sources.append({"subject": subject, "bars": bar_fingerprint(pair.get("bars", {})),
                        "metadata": metadata_fingerprint(pair.get("metadata", {})), "coverage": fingerprint(coverage)})
    sources.sort(key=lambda source: json.dumps(source, sort_keys=True))
    generation = fingerprint({"algorithm": ALGORITHM, "day": inputs.trading_date, "cohorts": inputs.cohorts,
                              "sources": sources, "eligibility": inputs.eligibility, "markets": inputs.markets})
    rows: list[dict[str, Any]] = []
    unknown_codes: set[str] = set()
    for minute in minute_keys(inputs.trading_date):
        row: dict[str, Any] = {"minute": minute, "status": "unconfirmed", "market_values": None, "total": None}
        try:
            weights = cohort_weights(minute, inputs.cohorts.get(minute, {}))
        except ValueError as error:
            row["reason"] = str(error)
            rows.append(row)
            continue
        values = [0.0, 0.0, 0.0]
        missing: list[str] = []
        for code, weight in weights.items():
            eligible = inputs.eligibility.get(code)
            nxt_values, _ = evidence.get(f"{code}:NXT", ({}, False))
            if eligible is None and any(amount > 0 for amount in nxt_values.values()):
                eligible = True
            if eligible is None:
                missing.append(f"{code}:nxt_eligibility")
                continue
            total = 0.0
            for venue in (("KRX", "NXT") if eligible else ("KRX",)):
                pair_values, zero_proven = evidence.get(f"{code}:{venue}", ({}, False))
                clock = minute[11:16]
                if clock not in pair_values and not zero_proven:
                    missing.append(f"{code}:{venue}:{clock}")
                else:
                    total += pair_values.get(clock, 0.0)
            market = inputs.markets.get(code, "").casefold()
            index = 0 if market == "kospi" else 1 if market == "kosdaq" else 2
            if index == 2:
                unknown_codes.add(code)
            values[index] += total * weight / 100
        if missing:
            row.update(reason="minute_evidence_missing", missing_inputs=missing)
        else:
            row.update(status="complete", market_values=values, total=sum(values))
        rows.append(row)
    coverage = _coverage(rows)
    coverage["unknown_market_codes"] = len(unknown_codes)
    return {"schema_version": SCHEMA, "trading_date": inputs.trading_date, "generation": generation,
            "algorithm": ALGORITHM, "basis": "after_close_estimate", "value_kind": "derived_from_estimate",
            "origin": "derived", "unit": "eok_won", "input_as_of": inputs.input_as_of,
            "available_at": available_at, "input_fingerprint": generation, "sources": sources,
            "rows": rows, "coverage": coverage,
            "status": "complete" if coverage["full_day"]["missing"] == 0 else "partial"}


def select_top20_series(day: str, raw: list[dict[str, Any]], reconciled: dict[str, Any] | None,
                        *, basis: str = "display") -> dict[str, Any]:
    if basis not in {"display", "realtime"}:
        raise ValueError("invalid TOP20 basis")
    if basis == "display" and reconciled is not None:
        if (reconciled.get("schema_version") == SCHEMA and reconciled.get("trading_date") == day
                and reconciled.get("basis") == "after_close_estimate"
                and _valid_projection(day, reconciled)):
            return reconciled
    by_minute = {str(value.get("minute", ""))[:16]: value for value in raw}
    rows = []
    for minute in minute_keys(day):
        value = by_minute.get(minute, {})
        amounts = value.get("market_values")
        valid = False
        if value.get("capture_state") == "realtime_complete" and isinstance(amounts, (list, tuple)) and len(amounts) == 3:
            try:
                amounts = [float(amount) for amount in amounts]
                valid = all(math.isfinite(amount) and amount >= 0 for amount in amounts)
            except (ValueError, TypeError):
                valid = False
        rows.append({"minute": minute, "status": "complete" if valid else "unconfirmed",
                     "market_values": amounts if valid else None, "total": sum(amounts) if valid else None,
                     "reason": "" if valid else "realtime_unconfirmed"})
    return {"schema_version": "top20_series/v1", "trading_date": day, "basis": "realtime",
            "generation": "", "available_at": None, "rows": rows, "coverage": _coverage(rows)}


def _valid_projection(day: str, value: dict[str, Any]) -> bool:
    rows = value.get("rows")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        return False
    if [row.get("minute") for row in rows] != minute_keys(day):
        return False
    for row in rows:
        if row.get("status") == "complete":
            amounts = row.get("market_values")
            if not isinstance(amounts, list) or len(amounts) != 3:
                return False
            if any(type(amount) not in (int, float) or not math.isfinite(amount) or amount < 0 for amount in amounts):
                return False
            if type(row.get("total")) not in (int, float) or not math.isclose(row["total"], sum(amounts)):
                return False
        elif row.get("status") != "unconfirmed" or row.get("total") is not None or row.get("market_values") is not None:
            return False
    expected = _coverage(rows)
    return all(value.get("coverage", {}).get(section) == expected[section] for section in expected)


def display_statistics(series: dict[str, Any], market: dict[str, Any]) -> dict[str, Any]:
    totals = [0.0, 0.0, 0.0]
    hourly: dict[str, dict[str, Any]] = {}
    for row in series["rows"]:
        if row["status"] != "complete":
            continue
        clock = row["minute"][11:16]
        hour = clock[:2] + ":00"
        bucket = hourly.setdefault(hour, {"hour": hour, "average_eok": 0.0, "sample_count": 0, "day_count": 1})
        bucket["average_eok"] += row["total"]
        bucket["sample_count"] += 1
        if "09:00" <= clock <= "15:30":
            totals = [a + b for a, b in zip(totals, row["market_values"])]
    for bucket in hourly.values():
        bucket["average_eok"] /= bucket["sample_count"]
    coverage = series["coverage"]["regular"]
    complete = coverage["missing"] == 0
    return {"hourly": list(hourly.values()), "comparisons": [{
        "trade_date": series["trading_date"], "basis": series["basis"], "generation": series.get("generation", ""),
        "coverage": coverage, "top20_eok": sum(totals) if complete else None,
        "top20_market_values": totals if complete else None, "known_top20_eok": sum(totals),
        "kospi_eok": market.get("kospi_eok", 0.0), "kosdaq_eok": market.get("kosdaq_eok", 0.0),
        "market_complete": market.get("market_complete", all(key in market for key in ("kospi_eok", "kosdaq_eok"))),
        "status": "complete" if complete else "partial"}]}
