"""Read-only status counts for the beginning of the audited preparation manifest."""

import argparse
import csv
import json
import sqlite3
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "data/historical_collection/audits/market-news-20260929"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=10000)
    args = parser.parse_args()
    counts: Counter[str] = Counter()
    with (AUDIT / "market-news-unprepared.csv").open(encoding="utf-8-sig", newline="") as handle, sqlite3.connect(
        f"file:{(ROOT / 'data/historical_collection/prepared-market-news.sqlite3').as_posix()}?mode=ro",
        uri=True, timeout=10,
    ) as database:
        for index, row in enumerate(csv.DictReader(handle)):
            if index >= args.limit:
                break
            found = database.execute(
                "SELECT state,body_json FROM prepared_news "
                "WHERE scope=? AND stock_code=? AND identity=?",
                ("historical_market", "GLOBAL", row["identity"]),
            ).fetchone()
            if found is None:
                counts["missing"] += 1
            elif found[0] != "ready":
                counts[f"state:{found[0]}"] += 1
            else:
                counts[f"body:{json.loads(found[1]).get('body_status', 'unknown')}"] += 1
    print(json.dumps({"first_manifest_rows": args.limit, "counts": counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
