from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from kiwoom_monitor.infrastructure.central_content_client import CentralContentClient


DEFAULT_DATABASE = Path("data/historical_intelligence.sqlite3")
DEFAULT_DATA_SOURCE = Path("data/data_source.json")


def _initialize_ledger(database: Path) -> None:
    with closing(sqlite3.connect(database)) as connection:
        with connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS nas_news_imports (
                    provider TEXT NOT NULL,
                    office_id TEXT NOT NULL,
                    article_id TEXT NOT NULL,
                    code TEXT NOT NULL,
                    identity TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    state TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    imported_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (provider, office_id, article_id, code)
                )
                """
            )


def _pending_documents(database: Path, limit: int) -> list[tuple[dict[str, object], tuple[str, ...]]]:
    with closing(sqlite3.connect(database)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT a.provider,a.office_id,a.article_id,a.title,a.summary,a.published_at,
                   a.published_precision,a.office_name,a.article_url,a.original_url,a.portal_url,
                   a.published_at_source,a.publication_source_url,o.code,MIN(o.query_text) AS stock_name
            FROM news_articles AS a
            JOIN news_search_observations AS o
              ON o.provider=a.provider AND o.office_id=a.office_id AND o.article_id=a.article_id
            LEFT JOIN nas_news_imports AS i
              ON i.provider=a.provider AND i.office_id=a.office_id
             AND i.article_id=a.article_id AND i.code=o.code AND i.state='imported'
            WHERE a.training_eligible=1 AND a.article_fetch_status='published_at_found'
              AND i.article_id IS NULL
            GROUP BY a.provider,a.office_id,a.article_id,o.code
            ORDER BY a.published_at,a.office_id,a.article_id,o.code
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    result: list[tuple[dict[str, object], tuple[str, ...]]] = []
    for row in rows:
        original = str(row["original_url"] or "")
        portal = str(row["portal_url"] or row["article_url"] or "")
        identity = original or portal or f"naver:{row['office_id']}:{row['article_id']}"
        document = {
            "stock_code": str(row["code"]),
            "stock_name": str(row["stock_name"] or ""),
            "identity": identity,
            "title": str(row["title"] or ""),
            "description": str(row["summary"] or ""),
            "link": portal,
            "original_link": original,
            "published_at": str(row["published_at"]),
            "publisher_name": str(row["office_name"] or ""),
            "historical_source": {
                "provider": str(row["provider"]),
                "office_id": str(row["office_id"]),
                "article_id": str(row["article_id"]),
                "published_precision": str(row["published_precision"]),
                "published_at_source": str(row["published_at_source"]),
                "publication_source_url": str(row["publication_source_url"]),
            },
        }
        content_hash = hashlib.sha256(
            json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        result.append(({
            "owner": str(row["code"]),
            "key": identity,
            "document": document,
            "collector_id": "naver_historical_web",
            "collection_scope": "historical_backfill",
        }, (
            str(row["provider"]), str(row["office_id"]), str(row["article_id"]),
            str(row["code"]), identity, content_hash,
        )))
    return result


def _mark(database: Path, identities: list[tuple[str, ...]], state: str, error: str = "") -> None:
    now = datetime.now(UTC).isoformat()
    with closing(sqlite3.connect(database)) as connection:
        with connection:
            connection.executemany(
                """
                INSERT INTO nas_news_imports
                (provider,office_id,article_id,code,identity,content_hash,state,attempts,
                 last_error,imported_at,updated_at)
                VALUES(?,?,?,?,?,?,?,1,?,?,?)
                ON CONFLICT(provider,office_id,article_id,code) DO UPDATE SET
                    identity=excluded.identity,content_hash=excluded.content_hash,state=excluded.state,
                    attempts=nas_news_imports.attempts+1,last_error=excluded.last_error,
                    imported_at=excluded.imported_at,updated_at=excluded.updated_at
                """,
                [
                    (*identity, state, error[:2000], now if state == "imported" else "", now)
                    for identity in identities
                ],
            )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="발행시각이 검증된 과거 기사를 기존 NAS 뉴스 revision 경로로 넣습니다."
    )
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--data-source", type=Path, default=DEFAULT_DATA_SOURCE)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--max-batches", type=int, default=0)
    args = parser.parse_args()
    if args.batch_size < 1 or args.batch_size > 1000:
        parser.error("--batch-size must be between 1 and 1000")
    if args.max_batches < 0:
        parser.error("--max-batches must be non-negative")

    database = args.database.resolve(strict=True)
    config = json.loads(args.data_source.resolve(strict=True).read_text(encoding="utf-8-sig"))
    server_url = str(config.get("server_url") or "").strip()
    access_token = str(config.get("access_token") or "").strip()
    if not server_url or not access_token:
        raise SystemExit("data source server_url/access_token is not configured")
    _initialize_ledger(database)
    client = CentralContentClient(server_url, access_token, timeout_seconds=60)
    imported = failed = batches = 0
    while args.max_batches == 0 or batches < args.max_batches:
        pending = _pending_documents(database, args.batch_size)
        if not pending:
            break
        documents = [item[0] for item in pending]
        identities = [item[1] for item in pending]
        try:
            client.upsert("news_article", documents)
        except Exception as error:
            _mark(database, identities, "failed", f"{type(error).__name__}: {error}")
            failed += len(identities)
            raise
        _mark(database, identities, "imported")
        imported += len(identities)
        batches += 1
        print(json.dumps({
            "event": "historical_news_import_batch",
            "batch": batches,
            "imported": len(identities),
            "total_imported_this_run": imported,
        }, ensure_ascii=False), flush=True)
    print(json.dumps({
        "imported": imported, "failed": failed, "batches": batches,
        "database": str(database),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
