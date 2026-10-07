"""Retry one missing historical 5-minute interval without resetting its job ledger."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import uuid
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from kiwoom_monitor.infrastructure.historical_backfill import import_daishin_backfill_ndjson
from scripts.run_daishin_candidate_collection import _five_minute_to_date, _ranges, _run_backfill


def retry(code: str, database: Path, raw_root: Path) -> dict[str, object]:
    code = code.strip().upper()
    if not re.fullmatch(r"[0-9A-Z]{6}", code):
        raise ValueError("code must be a six-character stock code")
    with closing(sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True)) as connection:
        row = connection.execute(
            "SELECT state,one_minute_bars,one_minute_oldest,five_minute_bars,last_error "
            "FROM market_backfill_jobs WHERE code=?", (code,),
        ).fetchone()
    if row is None:
        raise ValueError(f"code is absent from the collection ledger: {code}")
    state, one_count, one_oldest, five_count, last_error = row
    if int(five_count) != 0:
        raise ValueError(f"five-minute bars already exist for {code}")
    if int(one_count) == 0 and not (
        state == "failed" and "one-minute response contained no bars" in str(last_error)
    ):
        raise ValueError(f"code is not an approved missing-five-minute retry: {code}")
    if int(one_count) > 0 and not one_oldest:
        raise ValueError(f"one-minute oldest date is missing for {code}")

    to_date = (
        _five_minute_to_date(one_oldest) if int(one_count) > 0
        else (date.today() - timedelta(days=1)).strftime("%Y%m%d")
    )
    run_dir = raw_root / code / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
    run_dir.mkdir(parents=True, exist_ok=False)
    artifact = run_dir / "5m.ndjson"
    result: dict[str, object] = {
        "code": code, "state_before": state, "one_minute_bars": int(one_count),
        "to_date": to_date, "artifact": str(artifact), "status": "requesting",
    }
    try:
        _run_backfill(code, 5, artifact, to_date=to_date)
        imported = import_daishin_backfill_ndjson(
            artifact, database, before_date=str(one_oldest)[:10] if int(one_count) > 0 else "",
        )
        result.update(imported)
        if int(imported["selected_bars"]) > 0:
            counts = _ranges(database, code)
            with closing(sqlite3.connect(database, timeout=60)) as connection, connection:
                connection.execute(
                    "UPDATE market_backfill_jobs SET five_minute_bars=?,five_minute_oldest=?,"
                    "five_minute_newest=?,updated_at=? WHERE code=? AND five_minute_bars=0",
                    (int(counts[3]), str(counts[4] or ""), str(counts[5] or ""),
                     datetime.now(UTC).isoformat(), code),
                )
            result["status"] = "imported"
        else:
            result["status"] = "provider_returned_no_older_bars"
    except Exception as error:
        result["status"] = "failed"
        result["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        (run_dir / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code", required=True)
    parser.add_argument("--database", type=Path, default=Path("data/historical_intelligence.sqlite3"))
    parser.add_argument("--raw-root", type=Path, default=Path("data/historical_collection/daishin-retry"))
    args = parser.parse_args()
    result = retry(args.code, args.database.resolve(strict=True), args.raw_root.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
