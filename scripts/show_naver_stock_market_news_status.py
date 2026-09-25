"""Show current FLASH/WORLD backfill progress from its state and page ledger."""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = PROJECT_ROOT / "data" / "naver_stock_market_news.sqlite3"
DEFAULT_STATE_FILE = PROJECT_ROOT / "data" / "historical_collection" / "market-news-state.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE_FILE)
    args = parser.parse_args()
    state = json.loads(args.state_file.read_text(encoding="utf-8")) if args.state_file.exists() else {}
    summary: dict[str, object] = {"collector": state}
    if args.database.exists():
        with closing(sqlite3.connect(f"file:{args.database.resolve().as_posix()}?mode=ro", uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            summary["coverage"] = [dict(row) for row in connection.execute(
                "SELECT source,state,COUNT(*) AS days,SUM(articles) AS articles "
                "FROM market_news_days GROUP BY source,state ORDER BY source,state"
            )]
            summary["latest_days"] = [dict(row) for row in connection.execute(
                "SELECT source,target_date,state,pages,articles,last_error,updated_at "
                "FROM market_news_days ORDER BY updated_at DESC LIMIT 4"
            )]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
