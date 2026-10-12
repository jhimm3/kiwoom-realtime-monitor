"""Add dated news searches from persisted NAS TOP20 membership, preserving the queue."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
import json
from pathlib import Path
import sqlite3
import sys
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kiwoom_monitor.domain.ranking import normalize_stock_code
from kiwoom_monitor.infrastructure.central_content_client import CentralContentClient
from kiwoom_monitor.infrastructure.central_server_config import DataSourceConfig
from kiwoom_monitor.infrastructure.historical_backfill import initialize_probe_database

KST = ZoneInfo("Asia/Seoul")


def ranking_jobs(day: str, snapshots: list[dict]) -> list[tuple[str, str, str]]:
    """Keep all observed names for each membership code, with original snapshot provenance."""
    if len(snapshots) >= 5000:
        raise ValueError(f"{day}: snapshot limit reached; do not seed an incomplete day")
    jobs: dict[tuple[str, str], str] = {}
    for snapshot in sorted(snapshots, key=lambda row: str(row.get("snapshot_key", ""))):
        key = str(snapshot.get("snapshot_key", ""))
        observed = datetime.fromisoformat(key)
        observed = observed.replace(tzinfo=KST) if observed.tzinfo is None else observed.astimezone(KST)
        if observed.date().isoformat() != day:
            raise ValueError(f"{day}: snapshot timestamp belongs to another date")
        payload = snapshot["payload"]
        codes = {normalize_stock_code(code) for code in payload["codes"]}
        named = set()
        for item in payload["items"]:
            code = normalize_stock_code(item.get("stk_cd", item.get("code", "")))
            name = str(item.get("stk_nm", item.get("name", ""))).strip()
            if code in codes and code and name:
                named.add(code)
                jobs.setdefault((code, name), key)
        if not codes or named != codes:
            raise ValueError(f"{day}: membership has missing stock names")
    return [(code, name, key) for (code, name), key in sorted(jobs.items())]


def seed_day(database: Path, day: str, jobs: list[tuple[str, str, str]]) -> int:
    date.fromisoformat(day)
    initialize_probe_database(database)
    now = datetime.now(UTC).isoformat()
    with closing(sqlite3.connect(database, timeout=60)) as connection, connection:
        # Count direct inserts, excluding the incremental monitor trigger's own writes.
        inserted = 0
        for code, name, key in jobs:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO news_backfill_jobs "
                "(code,target_date,query_text,name_source,name_source_ref,state,updated_at) "
                "VALUES(?,?,?,'nas.top20_membership',?,'pending',?)",
                (code, day, name, key, now),
            )
            inserted += max(0, cursor.rowcount)
        return inserted


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", type=date.fromisoformat, required=True)
    parser.add_argument("--end-date", type=date.fromisoformat,
                        default=datetime.now(KST).date() - timedelta(days=1))
    parser.add_argument("--config", type=Path, default=ROOT / "data/data_source.json")
    parser.add_argument("--output", type=Path, default=ROOT / "data/historical_intelligence.sqlite3")
    parser.add_argument("--apply", action="store_true", help="Insert only missing searches; default is preview")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.start_date > args.end_date or (args.end_date - args.start_date).days > 366:
        parser.error("date range must be ordered and at most 367 days")
    settings = DataSourceConfig(args.config).load()
    client = CentralContentClient(settings.server_url, settings.access_token, timeout_seconds=30)
    days = []
    current = args.start_date
    while current <= args.end_date:
        day = current.isoformat()
        query = urlencode({"subject": day, "limit": 5000})
        rows = client._request("GET", "/api/v1/market/snapshots/top20_membership?" + query)["snapshots"]
        jobs = ranking_jobs(day, rows)
        inserted = seed_day(args.output, day, jobs) if args.apply and jobs else 0
        result = {"day": day, "snapshots": len(rows), "unique_stocks": len({j[0] for j in jobs}),
                  "queries": len(jobs), "inserted": inserted,
                  "state": "seeded" if args.apply and jobs else "preview" if jobs else "no_saved_membership",
                  "provisional": current >= datetime.now(KST).date()}
        days.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            temporary = args.report.with_suffix(".tmp")
            temporary.write_text(json.dumps({"source": "nas.top20_membership", "days": days,
                                             "inserted": sum(d["inserted"] for d in days)},
                                            ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(args.report)
        current += timedelta(days=1)


if __name__ == "__main__":
    main()
