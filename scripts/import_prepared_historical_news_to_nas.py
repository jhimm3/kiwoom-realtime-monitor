"""Commit PC-prepared historical BODY/RULE to the normal NAS news tables.

Run inside the server container against a copied, immutable snapshot of the
PC prepared_news.sqlite3. This does not claim work from the NAS queue or fetch
article pages. Existing canonical save/complete functions write the revisions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from kiwoom_monitor.central_server.database import PostgresQueryStore, _news_article_content_hash
from kiwoom_monitor.application.news_rules import SUPPLY_CONTRACT_RULE_VERSION
from kiwoom_monitor.domain.news_observation import (
    ARTICLE_BODY_EXTRACTOR_VERSION, stable_document_hash,
)


def _prepared_hash(article: dict, body: dict, rules: list[dict]) -> str:
    encoded = json.dumps([article, body, rules], ensure_ascii=False,
                         sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _open_ledger(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    ledger = sqlite3.connect(path, timeout=60)
    ledger.execute("CREATE TABLE IF NOT EXISTS imported_prepared_news ("
                   "scope TEXT NOT NULL,stock_code TEXT NOT NULL,identity TEXT NOT NULL,"
                   "prepared_hash TEXT NOT NULL,imported_at REAL NOT NULL,"
                   "PRIMARY KEY(scope,stock_code,identity))")
    ledger.commit()
    return ledger


def _article_revision(connection: psycopg.Connection, article: dict, scope: str) -> str:
    content_hash = (stable_document_hash(article["document"])
                    if scope == "historical_backfill" else
                    _news_article_content_hash({**article["document"],
                                                "stock_code": "GLOBAL",
                                                "identity": article["identity"]}))
    with connection.cursor() as cursor:
        cursor.execute("SELECT article_revision_id FROM central_news_article_revisions "
                       "WHERE stock_code=%s AND identity=%s AND content_hash=%s "
                       "ORDER BY accepted_sequence DESC LIMIT 1",
                       (article["stock_code"], article["identity"], content_hash))
        row = cursor.fetchone()
    if row is None:
        raise RuntimeError("정상 뉴스 적재 뒤 기사 리비전을 찾지 못했습니다.")
    return str(row[0])


def _job_for_completion(connection: psycopg.Connection, article_id: str,
                        stage: str, target_id: str) -> tuple[str, int, str]:
    version = ARTICLE_BODY_EXTRACTOR_VERSION if stage == "BODY" else SUPPLY_CONTRACT_RULE_VERSION
    with connection.cursor() as cursor:
        cursor.execute("SELECT job_key,attempts,state FROM central_news_jobs "
                       "WHERE article_revision_id=%s AND stage=%s AND target_id=%s "
                       "AND processing_version=%s ORDER BY updated_at DESC LIMIT 1 FOR UPDATE",
                       (article_id, stage, target_id, version))
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError(f"{stage} 저장 작업이 생성되지 않았습니다: {target_id}")
        key, attempts, state = str(row[0]), int(row[1]), str(row[2])
        if state != "COMPLETED":
            attempts += 1
            cursor.execute("UPDATE central_news_jobs SET state='RUNNING',attempts=%s,"
                           "updated_at=%s WHERE job_key=%s", (attempts, time.time(), key))
    connection.commit()
    return key, attempts, state


def _import_article(store: PostgresQueryStore, article: dict, scope: str) -> None:
    if scope == "historical_backfill":
        store.upsert_documents("news_article", [{
            "owner": article["stock_code"], "key": article["identity"],
            "document": article["document"], "collector_id": "naver_historical_web",
            "collection_scope": "historical_news_pc_backfill",
        }])
    elif scope == "historical_market_backfill":
        source = str(article["source"])
        batch_id = hashlib.sha256(("pc-prepared-v1\0" + source + "\0" +
                                   article["identity"]).encode("utf-8")).hexdigest()
        store.save_historical_market_news_batch(
            source, str(article["target_date"]), batch_id,
            [article["source_item"]], "pc",
        )
    else:
        raise ValueError(f"unsupported legacy scope: {scope}")


def _commit_prepared(store: PostgresQueryStore, connection: psycopg.Connection,
                     scope: str, article: dict, body: dict, rules: list[dict]) -> None:
    _import_article(store, article, scope)
    article_id = _article_revision(connection, article, scope)
    key, attempts, state = _job_for_completion(
        connection, article_id, "BODY", article["stock_code"])
    if state != "COMPLETED":
        store.complete_external_historical_news_job({
            "job_key": key, "attempts": attempts, "stage": "BODY",
            "body_text": body["body_text"], "body_status": body["body_status"],
            "fetched_at": body.get("fetched_at"),
            "original_published_at": body.get("original_published_at") or "",
            "source_url": body.get("source_url") or "",
        })
    for rule in rules:
        target = str(rule["target_id"])
        key, attempts, state = _job_for_completion(connection, article_id, "RULE", target)
        if state == "COMPLETED":
            continue
        store.complete_external_historical_news_job({
            "job_key": key, "attempts": attempts, "stage": "RULE",
            "assessment": rule["assessment"],
            "core_sentences": rule["core_sentences"],
            "rule_result": rule.get("rule_result"),
        })


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--ledger", type=Path,
                        default=Path("/app/data/maintenance/prepared-news-imports.sqlite3"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("limit must be non-negative")
    prepared = args.prepared.resolve(strict=True)
    if args.dry_run:
        with closing(sqlite3.connect(f"file:{prepared.as_posix()}?mode=ro", uri=True)) as source:
            rows = source.execute("SELECT state,COUNT(*) FROM prepared_news GROUP BY state").fetchall()
        print(json.dumps({"mode": "dry_run", "rows": dict(rows)}), flush=True)
        return 0
    database_url = os.environ.get("KIWOOM_SERVER_DATABASE_URL")
    if not database_url:
        raise SystemExit("KIWOOM_SERVER_DATABASE_URL is not configured")
    import psycopg

    store = PostgresQueryStore(database_url)
    imported = skipped = failed = 0
    with closing(sqlite3.connect(f"file:{prepared.as_posix()}?mode=ro",
                                 uri=True)) as source, closing(_open_ledger(args.ledger)) as ledger, \
         psycopg.connect(database_url) as connection:
        for scope, code, identity, article_raw, body_raw, rules_raw in source.execute(
            "SELECT scope,stock_code,identity,article_json,body_json,rules_json "
            "FROM prepared_news WHERE state='ready' ORDER BY rowid"):
            article, body, rules = json.loads(article_raw), json.loads(body_raw), json.loads(rules_raw)
            digest = _prepared_hash(article, body, rules)
            prior = ledger.execute("SELECT prepared_hash FROM imported_prepared_news "
                                   "WHERE scope=? AND stock_code=? AND identity=?",
                                   (scope, code, identity)).fetchone()
            if prior and prior[0] == digest:
                skipped += 1
                continue
            try:
                _commit_prepared(store, connection, scope, article, body, rules)
            except Exception as error:
                failed += 1
                print(json.dumps({"state": "failed", "scope": scope, "stock_code": code,
                                  "identity": identity, "error": f"{type(error).__name__}: {error}"[:500]},
                                 ensure_ascii=False), flush=True)
                connection.rollback()
                continue
            ledger.execute("INSERT INTO imported_prepared_news VALUES(?,?,?,?,?) "
                           "ON CONFLICT(scope,stock_code,identity) DO UPDATE SET "
                           "prepared_hash=excluded.prepared_hash,imported_at=excluded.imported_at",
                           (scope, code, identity, digest, time.time()))
            ledger.commit()
            imported += 1
            if imported % 100 == 0:
                print(json.dumps({"imported": imported, "skipped": skipped,
                                  "failed": failed}), flush=True)
            if args.limit and imported + failed >= args.limit:
                break
    print(json.dumps({"imported": imported, "skipped": skipped, "failed": failed}), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
