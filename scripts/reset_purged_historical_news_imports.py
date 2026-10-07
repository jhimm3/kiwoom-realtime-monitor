"""Requeue only PC archive rows named by the NAS legacy-news purge manifest."""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path


def load_manifest(connection: sqlite3.Connection, manifest: Path) -> dict[str, int]:
    connection.execute("CREATE TEMP TABLE purged_search(stock_code TEXT, identity TEXT, "
                       "PRIMARY KEY(stock_code,identity))")
    connection.execute("CREATE TEMP TABLE purged_market(identity TEXT PRIMARY KEY)")
    counts = {"search": 0, "market": 0}
    with manifest.open("r", encoding="utf-8") as source:
        for line in source:
            value = json.loads(line)
            scope = value["scope"]
            identity = value["identity"]
            if scope == "historical_backfill":
                connection.execute("INSERT OR IGNORE INTO purged_search VALUES(?,?)",
                                   (value["stock_code"], identity))
                counts["search"] += 1
            elif scope == "historical_market_backfill":
                connection.execute("INSERT OR IGNORE INTO purged_market VALUES(?)", (identity,))
                counts["market"] += 1
            else:
                raise ValueError(f"unexpected collection_scope: {scope}")
    return counts


def reset_search(database: Path, manifest: Path, execute: bool) -> dict[str, int]:
    with closing(sqlite3.connect(database, timeout=60)) as connection, connection:
        counts = load_manifest(connection, manifest)
        found = connection.execute(
            "SELECT COUNT(*) FROM nas_news_imports i JOIN purged_search p "
            "ON p.stock_code=i.code AND p.identity=i.identity WHERE i.state='imported'").fetchone()[0]
        if execute:
            connection.execute(
                "UPDATE nas_news_imports SET state='pending',imported_at='',last_error='' "
                "WHERE state='imported' AND EXISTS (SELECT 1 FROM purged_search p "
                "WHERE p.stock_code=nas_news_imports.code "
                "AND p.identity=nas_news_imports.identity)")
        else:
            connection.rollback()
    return {"search_manifest": counts["search"], "search_requeued": found,
            "market_manifest": counts["market"]}


def reset_market(database: Path, manifest: Path, execute: bool) -> int:
    with closing(sqlite3.connect(database, timeout=60)) as connection, connection:
        load_manifest(connection, manifest)
        predicate = (
            "EXISTS (SELECT 1 FROM market_news_articles a "
            "JOIN purged_market p ON p.identity=a.article_url "
            "WHERE a.source=nas_market_news_imports.source "
            "AND a.office_id=nas_market_news_imports.office_id "
            "AND a.article_id=nas_market_news_imports.article_id)"
        )
        found = connection.execute(
            "SELECT COUNT(*) FROM nas_market_news_imports WHERE state='imported' AND "
            + predicate).fetchone()[0]
        if execute:
            connection.execute(
                "UPDATE nas_market_news_imports SET state='pending',imported_at='',last_error='' "
                "WHERE state='imported' AND " + predicate)
        else:
            connection.rollback()
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--search-database", type=Path,
                        default=Path("data/historical_intelligence.sqlite3"))
    parser.add_argument("--market-database", type=Path,
                        default=Path("data/naver_stock_market_news.sqlite3"))
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    manifest = args.manifest.resolve(strict=True)
    search = reset_search(args.search_database.resolve(strict=True), manifest, args.execute)
    market = reset_market(args.market_database.resolve(strict=True), manifest, args.execute)
    print(json.dumps({"mode": "execute" if args.execute else "preview", **search,
                      "market_requeued": market}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
