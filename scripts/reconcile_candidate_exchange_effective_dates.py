"""Compare explicit exchange effective dates with saved Kiwoom daily trade rows.

Daily volume is a cross-check, not a replacement for the exchange's event time:
an intraday halt may coexist with positive volume on the same date.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import UTC, date, datetime
from pathlib import Path


DEFAULT_CANDIDATES = Path(r"C:\Users\pc-1\Desktop\kiwoom_history_backfill\data\kiwoom_history.sqlite3")
DEFAULT_CONTEXT = Path("data/historical_market_context.sqlite3")


def reconcile(context: Path, candidates: Path) -> dict[str, object]:
    source = candidates.resolve(strict=True)
    with closing(sqlite3.connect(context, timeout=60)) as destination, closing(
        sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
    ) as daily:
        destination.execute("PRAGMA busy_timeout=60000")
        daily.execute("PRAGMA query_only=ON")
        destination.executescript("""
            CREATE TABLE IF NOT EXISTS historical_exchange_effective_daily_checks (
                code TEXT NOT NULL, receipt_no TEXT NOT NULL, kind TEXT NOT NULL,
                effective_date TEXT NOT NULL, effective_time TEXT NOT NULL,
                receipt_date TEXT NOT NULL, receipt_to_effective_days INTEGER NOT NULL,
                daily_row_state TEXT NOT NULL, daily_volume INTEGER,
                last_positive_before TEXT NOT NULL DEFAULT '',
                first_positive_on_or_after TEXT NOT NULL DEFAULT '',
                review_status TEXT NOT NULL DEFAULT 'no_detected_contradiction',
                candidate_database_size INTEGER NOT NULL,
                candidate_database_mtime_ns INTEGER NOT NULL,
                checked_at TEXT NOT NULL,
                PRIMARY KEY(code,receipt_no,kind,effective_date,effective_time)
            );
        """)
        if "review_status" not in {row[1] for row in destination.execute(
            "PRAGMA table_info(historical_exchange_effective_daily_checks)"
        )}:
            destination.execute(
                "ALTER TABLE historical_exchange_effective_daily_checks "
                "ADD COLUMN review_status TEXT NOT NULL DEFAULT 'no_detected_contradiction'"
            )
        source_stat = source.stat()
        rows = destination.execute(
            "SELECT code,receipt_no,receipt_date,kind,effective_date,effective_time "
            "FROM historical_exchange_effective_events "
            "ORDER BY code,effective_date,receipt_no,kind"
        ).fetchall()
        now = datetime.now(UTC).isoformat()
        statuses: Counter[str] = Counter()
        review_counts: Counter[str] = Counter()
        delist_positive_after: list[dict[str, str]] = []
        negative_lag: list[dict[str, str]] = []
        inputs = []
        for code, receipt, receipt_date, kind, effective_date, effective_time in rows:
            volume_row = daily.execute(
                "SELECT volume FROM daily_bars WHERE code=? AND dt=?",
                (code, effective_date),
            ).fetchone()
            volume = int(volume_row[0] or 0) if volume_row else None
            state = "absent" if volume is None else "positive" if volume > 0 else "zero"
            statuses[f"{kind}:{state}"] += 1
            prior = daily.execute(
                "SELECT MAX(dt) FROM daily_bars WHERE code=? AND dt<? AND volume>0",
                (code, effective_date),
            ).fetchone()[0] or ""
            next_positive = daily.execute(
                "SELECT MIN(dt) FROM daily_bars WHERE code=? AND dt>=? AND volume>0",
                (code, effective_date),
            ).fetchone()[0] or ""
            if kind == "delist" and next_positive and next_positive >= effective_date:
                delist_positive_after.append({
                    "code": code, "receipt_no": receipt,
                    "effective_date": effective_date,
                    "first_positive_on_or_after": next_positive,
                })
            lag = (date.fromisoformat(effective_date) - date.fromisoformat(receipt_date)).days
            review_status = (
                "delist_with_later_trade_requires_review"
                if kind == "delist" and next_positive and next_positive >= effective_date
                else "receipt_after_effective_date_requires_review" if lag < 0
                else "no_detected_contradiction"
            )
            review_counts[review_status] += 1
            if lag < 0:
                negative_lag.append({
                    "code": code, "receipt_no": receipt,
                    "receipt_date": receipt_date, "effective_date": effective_date,
                    "kind": kind,
                })
            inputs.append((code, receipt, kind, effective_date, effective_time,
                           receipt_date, lag, state, volume, prior, next_positive,
                           review_status,
                           source_stat.st_size, source_stat.st_mtime_ns, now))
        with destination:
            destination.executemany(
                "INSERT INTO historical_exchange_effective_daily_checks "
                "(code,receipt_no,kind,effective_date,effective_time,receipt_date,"
                "receipt_to_effective_days,daily_row_state,daily_volume,"
                "last_positive_before,first_positive_on_or_after,review_status,"
                "candidate_database_size,candidate_database_mtime_ns,checked_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(code,receipt_no,kind,effective_date,effective_time) "
                "DO UPDATE SET receipt_to_effective_days=excluded.receipt_to_effective_days,"
                "daily_row_state=excluded.daily_row_state,daily_volume=excluded.daily_volume,"
                "last_positive_before=excluded.last_positive_before,"
                "first_positive_on_or_after=excluded.first_positive_on_or_after,"
                "review_status=excluded.review_status,"
                "candidate_database_size=excluded.candidate_database_size,"
                "candidate_database_mtime_ns=excluded.candidate_database_mtime_ns,"
                "checked_at=excluded.checked_at",
                inputs,
            )
    return {
        "version": "historical_exchange_effective_daily_check/v1",
        "event_count": len(rows), "daily_states": dict(sorted(statuses.items())),
        "review_status_counts": dict(sorted(review_counts.items())),
        "receipt_date_differs_from_effective_date": sum(row[6] != 0 for row in inputs),
        "receipt_after_effective_date_count": len(negative_lag),
        "receipt_after_effective_date_examples": negative_lag[:50],
        "intraday_precision_count": sum(bool(row[4]) for row in inputs),
        "delisting_with_later_positive_trade": delist_positive_after,
        "candidate_database": {"path": str(source), "size_bytes": source_stat.st_size,
                               "mtime_ns": source_stat.st_mtime_ns},
        "limitations": [
            "A daily zero-volume row does not prove an official full-day suspension.",
            "Positive volume on a halt date can precede an intraday halt.",
            "The candidate daily database may end before a later effective date.",
            "Conflicting or corrected official filings require separate revision review.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", type=Path, default=DEFAULT_CONTEXT)
    parser.add_argument("--candidate-database", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("effective-date reconciliation report is immutable")
    result = reconcile(args.context, args.candidate_database)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)
                           + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in (
        "event_count", "daily_states", "receipt_date_differs_from_effective_date",
        "intraday_precision_count",
    )}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
