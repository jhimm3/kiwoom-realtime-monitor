"""Read-only PC audit of prepared historical news against source-page coverage."""

from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time


def audit(raw_path: Path, prepared_path: Path) -> dict[str, object]:
    raw_path = raw_path.resolve(strict=True)
    prepared_path = prepared_path.resolve(strict=True)
    with closing(sqlite3.connect(f"file:{raw_path.as_posix()}?mode=ro", uri=True)) as db:
        db.execute("ATTACH DATABASE ? AS prepared",
                   (f"file:{prepared_path.as_posix()}?mode=ro",))
        maximum = db.execute("SELECT MAX(rowid) FROM source_pages").fetchone()[0]
        started = time.monotonic()
        matches, keys = db.execute(
            "SELECT COUNT(*),COUNT(DISTINCT "
            "printf('code=%s&date=%s&query=%s&start=%d',"
            "o.code,o.source_date,o.query_text,o.start)) "
            "FROM news_search_observations o "
            "JOIN news_articles a ON a.provider=o.provider AND a.office_id=o.office_id "
            "AND a.article_id=o.article_id "
            "JOIN prepared.prepared_news p ON p.scope='historical_backfill' "
            "AND p.stock_code=o.code AND p.identity=COALESCE(NULLIF(a.original_url,''),"
            "NULLIF(a.portal_url,''),'naver:'||a.office_id||':'||a.article_id)"
        ).fetchone()
        return {"source_pages_max_rowid": int(maximum or 0),
                "matched_observations": int(matches),
                "distinct_request_keys": int(keys),
                "join_seconds": round(time.monotonic() - started, 3),
                "raw_path": str(raw_path), "prepared_path": str(prepared_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", required=True, type=Path)
    parser.add_argument("--prepared", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.raw, args.prepared), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
