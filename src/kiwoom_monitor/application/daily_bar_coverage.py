"""Bounded source-window evidence for adjusted daily bars, independent of storage."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

COLLECTION = "daily_bar_history_coverage"
PERIODS = (5, 20, 250)
VALUE_FIELDS = ("trading_date", "open", "high", "low", "close", "volume", "trade_value_million_won")


def daily_query_date() -> date:
    return datetime.now(timezone(timedelta(hours=9))).date()


def source_integer(value: object) -> int | None:
    try:
        return abs(int(str(value).strip().replace(",", "")))
    except (TypeError, ValueError):
        return None


def normalize_source_daily_bar(record: dict[str, Any]) -> dict[str, Any] | None:
    """Use the ingestor's integer/sign/unit rules; timestamps are not price evidence."""
    raw = str(record.get("date", record.get("dt", ""))).strip()
    if len(raw) != 8 or not raw.isdigit():
        return None
    try:
        day = date.fromisoformat(f"{raw[:4]}-{raw[4:6]}-{raw[6:]}").isoformat()
    except ValueError:
        return None
    prices = [source_integer(record.get(key)) for key in ("open_pric", "high_pric", "low_pric", "cur_prc")]
    if any(value is None for value in prices):
        return None
    return dict(zip(VALUE_FIELDS, (day, *prices, source_integer(record.get("trde_qty")) or 0,
                                  source_integer(record.get("trde_prica")))))


def fingerprint(rows: Iterable[dict[str, Any]]) -> str:
    values = [[row.get(key) for key in VALUE_FIELDS] for row in rows]
    return hashlib.sha256(json.dumps(values, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class DailySourceWindow:
    """Validate continuation and overlapping pages without inventing missing trading days."""

    def __init__(self, query_basis_date: str, window_end: str) -> None:
        self.basis = date.fromisoformat(query_basis_date).isoformat()
        self.end = date.fromisoformat(window_end).isoformat()
        if self.end > self.basis:
            raise ValueError("일봉 대상일은 조회 기준일 이후일 수 없습니다.")
        self._rows: dict[str, dict[str, Any]] = {}
        self._keys: set[str] = set()
        self.pages = 0
        self.source_exhausted = False

    @property
    def rows(self) -> list[dict[str, Any]]:
        return [self._rows[day] for day in sorted(self._rows, reverse=True) if day <= self.end][:250]

    @property
    def complete(self) -> bool:
        return bool(self.rows) and (len(self.rows) == 250 or self.source_exhausted)

    def add_page(self, payload: dict[str, Any], has_next: bool, next_key: str) -> None:
        records = payload.get("stk_dt_pole_chart_qry", payload.get("stk_ddwkmm", []))
        if not isinstance(records, list) or (not records and not self._rows):
            raise ValueError("일봉 첫 응답이 비었거나 목록 형식이 잘못되었습니다.")
        previous_oldest = min(self._rows, default=None)
        last_day: str | None = None
        added = 0
        for record in records:
            row = normalize_source_daily_bar(record) if isinstance(record, dict) else None
            if row is None or any((row[field] or 0) <= 0 for field in ("open", "high", "low", "close")):
                raise ValueError("일봉 날짜 또는 가격 형식이 잘못되었습니다.")
            day = row["trading_date"]
            if day > self.basis or (last_day is not None and day > last_day):
                raise ValueError("일봉 날짜 범위 또는 내림차순이 잘못되었습니다.")
            last_day = day
            existing = self._rows.get(day)
            if existing is not None and existing != row:
                raise ValueError("일봉 중복 날짜의 값이 다릅니다.")
            if existing is None:
                if previous_oldest is not None and day >= previous_oldest:
                    raise ValueError("일봉 연속조회가 이전 구간보다 진행하지 않습니다.")
                self._rows[day] = row
                added += 1
        if self.pages and records and not added:
            raise ValueError("일봉 연속조회 페이지가 진행하지 않습니다.")
        self.pages += 1
        if has_next:
            if not next_key or next_key in self._keys or not added:
                raise ValueError("일봉 연속조회 키 또는 진행 상태가 잘못되었습니다.")
            self._keys.add(next_key)
        self.source_exhausted = not has_next
        if self.source_exhausted and not self.rows:
            raise ValueError("일봉 응답에 대상 구간의 봉이 없습니다.")
        if self.pages >= 8 and not self.complete:
            raise ValueError("일봉 연속조회 상한에 도달했지만 확보되지 않았습니다.")

    def evidence(self, *, code: str, market: str, scope: str, checked_at: str) -> dict[str, Any]:
        if not self.complete:
            raise ValueError("미완료 일봉 구간은 완료 근거로 기록할 수 없습니다.")
        rows = self.rows
        return {"schema_version": 1, "code": code, "market": market, "scope": scope,
                "source": "kiwoom-ka10081", "adjustment_mode": "1", "query_basis_date": self.basis,
                "window_end": self.end, "checked_at": checked_at, "source_exhausted": self.source_exhausted,
                "latest_bar_date": rows[0]["trading_date"], "oldest_bar_date": rows[-1]["trading_date"],
                "expected_dates": [row["trading_date"] for row in rows],
                "fingerprints": {str(n): fingerprint(rows[:n]) for n in PERIODS}}


def assess_daily_coverage(
    rows: Iterable[dict[str, Any]], evidence: dict[str, Any] | None, *,
    code: str, market: str, query_basis_date: str,
) -> dict[str, Any]:
    """Revalidate each prefix against returned rows; a short response proves no unseen period."""
    result: dict[str, Any] = {"collection_verified": False, "scope": "unverified", "periods": {
        str(n): {"status": "unverified", "reason": "missing_or_stale_evidence"} for n in PERIODS}}
    if not isinstance(evidence, dict):
        return result
    dates = evidence.get("expected_dates")
    hashes = evidence.get("fingerprints")
    if (evidence.get("schema_version") != 1 or evidence.get("code") != code
            or evidence.get("market") != market or evidence.get("adjustment_mode") != "1"
            or evidence.get("source") != "kiwoom-ka10081" or evidence.get("scope") not in {"initial", "final"}
            or evidence.get("query_basis_date") != query_basis_date
            or not isinstance(dates, list) or not 1 <= len(dates) <= 250
            or any(not isinstance(day, str) for day in dates)
            or dates != sorted(set(dates), reverse=True) or not isinstance(hashes, dict)
            or (len(dates) < 250 and evidence.get("source_exhausted") is not True)):
        return result
    try:
        end = date.fromisoformat(evidence["window_end"]).isoformat()
        if end > query_basis_date or any(date.fromisoformat(day).isoformat() != day or day > end for day in dates):
            return result
        ordered = sorted((row for row in rows if str(row.get("trading_date", "")) <= end),
                         key=lambda row: row["trading_date"], reverse=True)
        if len({row["trading_date"] for row in ordered}) != len(ordered):
            return result
        matched = {}
        for n in PERIODS:
            count = min(n, len(dates))
            prefix = ordered[:count]
            valid = ([row["trading_date"] for row in prefix] == dates[:count]
                     and fingerprint(prefix) == hashes.get(str(n)))
            matched[n] = valid
            status = "provisional" if dates[0] == query_basis_date and evidence["scope"] != "final" else "ready"
            result["periods"][str(n)] = {"status": status if valid else "unverified",
                "reason": ("source_exhausted_short_history" if len(dates) < n else "source_window_verified")
                          if valid else "stored_window_mismatch",
                "requested_count": n, "available_count": count}
        result.update({key: evidence.get(key) for key in (
            "scope", "query_basis_date", "window_end", "checked_at", "source_exhausted", "source",
            "latest_bar_date", "oldest_bar_date")})
        result["expected_count"] = len(dates)
        result["collection_verified"] = matched[250]
    except (KeyError, TypeError, ValueError):
        return {"collection_verified": False, "scope": "unverified", "periods": {
            str(n): {"status": "unverified", "reason": "invalid_evidence_or_rows"} for n in PERIODS}}
    return result


def choose_daily_coverage(rows: list[dict[str, Any]], documents: list[dict[str, Any]], *,
                          code: str, market: str, query_basis_date: str) -> dict[str, Any]:
    candidates = [assess_daily_coverage(rows, row.get("document"), code=code, market=market,
                                      query_basis_date=query_basis_date) for row in documents]
    # Prefer the newest target window, then an intact final proof and verified prefixes.
    return max(candidates, key=lambda item: (item.get("window_end", ""), item["collection_verified"], item.get("scope") == "final",
               sum(part["status"] != "unverified" for part in item["periods"].values())),
               default=assess_daily_coverage(rows, None, code=code, market=market, query_basis_date=query_basis_date))
