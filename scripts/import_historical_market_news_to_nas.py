"""Import archived Naver Stock FLASH/WORLD articles into the normal NAS news feed.

The local SQLite remains the collection archive and a resumable upload ledger.
Only the authenticated NAS API writes operational news, BODY, and RULE records.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from collections import defaultdict
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from kiwoom_monitor.central_server.news_sources import _page_items
from kiwoom_monitor.infrastructure.central_content_client import CentralContentClient
from kiwoom_monitor.infrastructure.historical_backfill import individual_candidate_catalog


DEFAULT_DATABASE = Path("data/naver_stock_market_news.sqlite3")
DEFAULT_CANDIDATES = Path(r"C:\Users\pc-1\Desktop\kiwoom_history_backfill\data\kiwoom_history.sqlite3")


def _connection(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database, timeout=60)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=60000")
    return connection


def _initialize_ledger(database: Path) -> None:
    with closing(_connection(database)) as connection, connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS nas_market_news_imports ("
            "source TEXT NOT NULL,office_id TEXT NOT NULL,article_id TEXT NOT NULL,"
            "state TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,"
            "last_error TEXT NOT NULL DEFAULT '',imported_at TEXT NOT NULL DEFAULT '',"
            "updated_at TEXT NOT NULL,PRIMARY KEY(source,office_id,article_id))"
        )


def _pending(database: Path, limit: int,
             after: tuple[str, str, str, str] | None = None) -> list[sqlite3.Row]:
    cursor_sql = ("AND (a.source,a.published_at,a.office_id,a.article_id) > (?,?,?,?) "
                  if after is not None else "")
    with closing(_connection(database)) as connection:
        return connection.execute(
            "SELECT a.source,a.office_id,a.article_id,a.published_at,a.title,a.summary,"
            "a.publisher,a.article_url FROM market_news_articles a "
            "LEFT JOIN nas_market_news_imports i ON i.source=a.source "
            "AND i.office_id=a.office_id AND i.article_id=a.article_id "
            "WHERE (i.state IS NULL OR i.state NOT IN ('imported','filtered')) " + cursor_sql +
            "ORDER BY a.source,a.published_at,a.office_id,a.article_id LIMIT ?",
            (*after, limit) if after is not None else (limit,),
        ).fetchall()


def _catalog_matcher(candidates: Path) -> tuple[dict[str, tuple[tuple[str, str], ...]], re.Pattern[str]]:
    by_name: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for code, name in individual_candidate_catalog(candidates).items():
        if name:
            by_name[name].append((code, name))
    pattern = re.compile("|".join(re.escape(name) for name in sorted(by_name, key=len, reverse=True)) or r"(?!)")
    return {name: tuple(values) for name, values in by_name.items()}, pattern


def _item(row: sqlite3.Row, by_name: dict[str, tuple[tuple[str, str], ...]],
          matcher: re.Pattern[str]) -> dict[str, object] | None:
    title, summary = str(row["title"] or ""), str(row["summary"] or "")
    text = f"{title} {summary}"
    matched = set(matcher.findall(text))
    catalog = tuple(pair for name in matched for pair in by_name[name])
    url = str(row["article_url"] or "")
    news = SimpleNamespace(title=title, description=summary, link=url,
                           original_link="", published_at=datetime.fromisoformat(str(row["published_at"])))
    accepted = _page_items([news], catalog, retain_market_articles=True)
    if not accepted:
        return None
    accepted[0]["document"]["publisher_name"] = str(row["publisher"] or "")
    return accepted[0]


def _batch_id(source: str, target_date: str, items: list[dict[str, object]]) -> str:
    value = [source, target_date, items]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _mark(database: Path, rows: list[sqlite3.Row], state: str, error: str = "") -> None:
    now = datetime.now(UTC).isoformat()
    with closing(_connection(database)) as connection, connection:
        connection.executemany(
            "INSERT INTO nas_market_news_imports(source,office_id,article_id,state,attempts,"
            "last_error,imported_at,updated_at) VALUES(?,?,?,?,1,?,?,?) "
            "ON CONFLICT(source,office_id,article_id) DO UPDATE SET "
            "state=excluded.state,attempts=nas_market_news_imports.attempts+1,"
            "last_error=excluded.last_error,imported_at=excluded.imported_at,"
            "updated_at=excluded.updated_at",
            [(row["source"], row["office_id"], row["article_id"], state,
              error[:1000], now if state == "imported" else "", now) for row in rows],
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="과거 속보·해외뉴스를 NAS 일반 뉴스 피드로 업로드합니다.")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--data-source", type=Path, default=Path("data/data_source.json"))
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--max-batches", type=int, default=0)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 100 or args.max_batches < 0:
        parser.error("batch-size는 1~100, max-batches는 0 이상이어야 합니다.")
    database = args.database.resolve(strict=True)
    config = json.loads(args.data_source.resolve(strict=True).read_text(encoding="utf-8-sig"))
    url, token = str(config.get("server_url") or "").strip(), str(config.get("access_token") or "").strip()
    if not url or not token:
        raise SystemExit("NAS server_url/access_token 설정이 필요합니다.")
    by_name, matcher = _catalog_matcher(args.candidates.resolve(strict=True))
    _initialize_ledger(database)
    client = CentralContentClient(url, token, timeout_seconds=60)
    batches = imported = 0
    after: tuple[str, str, str, str] | None = None
    while args.max_batches == 0 or batches < args.max_batches:
        rows = _pending(database, args.batch_size, after)
        if not rows:
            break
        groups: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
        for row in rows:
            groups[(str(row["source"]), str(row["published_at"])[:10])].append(row)
        for (source, target_date), group in groups.items():
            if args.max_batches and batches >= args.max_batches:
                break
            prepared = [(row, _item(row, by_name, matcher)) for row in group]
            accepted_rows = [row for row, item in prepared if item is not None]
            filtered_rows = [row for row, item in prepared if item is None]
            items = [item for _, item in prepared if item is not None]
            if filtered_rows:
                _mark(database, filtered_rows, "filtered")
            if not items:
                last = group[-1]
                after = (str(last["source"]), str(last["published_at"]),
                         str(last["office_id"]), str(last["article_id"]))
                batches += 1
                continue
            batch = {"source": source, "target_date": target_date,
                     "processing_owner": "pc",
                     "batch_id": _batch_id(source, target_date, items), "items": items}
            try:
                response = client.import_historical_market_articles(batch)
                if response.get("state") not in {"imported", "already_imported"}:
                    raise RuntimeError(f"unexpected NAS import state: {response.get('state')}")
            except Exception as error:
                _mark(database, accepted_rows, "failed", f"{type(error).__name__}: {error}")
                raise
            _mark(database, accepted_rows, "imported")
            last = group[-1]
            after = (str(last["source"]), str(last["published_at"]),
                     str(last["office_id"]), str(last["article_id"]))
            batches += 1
            imported += len(accepted_rows)
            print(json.dumps({"source": source, "date": target_date, "batch": batches,
                              "imported": len(accepted_rows), "filtered": len(filtered_rows),
                              "total": imported}, ensure_ascii=False), flush=True)
    print(json.dumps({"batches": batches, "imported": imported}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
