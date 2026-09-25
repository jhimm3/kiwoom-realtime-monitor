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

from kiwoom_monitor.central_server.database import (
    PostgresQueryStore, _complete_external_news_job, _news_article_content_hash,
)
from kiwoom_monitor.application.news_rules import SUPPLY_CONTRACT_RULE_VERSION
from kiwoom_monitor.domain.news_observation import (
    ARTICLE_BODY_EXTRACTOR_VERSION, stable_document_hash,
)


class PreparedImportDeferred(RuntimeError):
    """The normal news worker currently owns one of this article's jobs."""


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


def _complete_prepared_job(connection: psycopg.Connection, article_id: str,
                           stage: str, target_id: str,
                           result: dict) -> str:
    version = ARTICLE_BODY_EXTRACTOR_VERSION if stage == "BODY" else SUPPLY_CONTRACT_RULE_VERSION
    now = time.time()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT job_key,state FROM central_news_jobs "
            "WHERE article_revision_id=%s AND stage=%s AND target_id=%s "
            "AND processing_version=%s ORDER BY updated_at DESC LIMIT 1",
            (article_id, stage, target_id, version),
        )
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError(f"{stage} 저장 작업이 생성되지 않았습니다: {target_id}")
        key, state = str(row[0]), str(row[1])
        if state != "COMPLETED":
            # Claim only a due PENDING row and complete it in the same transaction.
            # A worker that already owns RUNNING must never have its attempt overwritten.
            cursor.execute(
                "UPDATE central_news_jobs SET state='RUNNING',attempts=attempts+1,updated_at=%s "
                "WHERE job_key=%s AND state='PENDING' AND next_retry_at<=%s "
                "RETURNING attempts",
                (now, key, now),
            )
            claimed = cursor.fetchone()
            if claimed is None:
                return "deferred"
            payload = {**result, "job_key": key, "attempts": int(claimed[0]), "stage": stage}
            saved = _complete_external_news_job(cursor, payload, postgres=True)
            return saved["state"]
    return "already_completed"


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


def _prepare_article(store: PostgresQueryStore, connection: psycopg.Connection,
                     scope: str, article: dict) -> str:
    _import_article(store, article, scope)
    article_id = _article_revision(connection, article, scope)
    connection.commit()
    return article_id


def _complete_prepared(connection: psycopg.Connection, article_id: str,
                       article: dict, body: dict, rules: list[dict]) -> None:
    body_state = _complete_prepared_job(
        connection, article_id, "BODY", article["stock_code"], {
        "body_text": body["body_text"], "body_status": body["body_status"],
        "fetched_at": body.get("fetched_at"),
        "original_published_at": body.get("original_published_at") or "",
        "source_url": body.get("source_url") or "",
    })
    if body_state == "deferred":
        raise PreparedImportDeferred("BODY 작업이 다른 실행기에 선점되어 적재를 미뤘습니다.")
    for rule in rules:
        target = str(rule["target_id"])
        rule_state = _complete_prepared_job(
            connection, article_id, "RULE", target, {
                "assessment": rule["assessment"],
                "core_sentences": rule["core_sentences"],
                "rule_result": rule.get("rule_result"),
            },
        )
        if rule_state == "deferred":
            raise PreparedImportDeferred(
                f"RULE 작업이 다른 실행기에 선점되어 적재를 미뤘습니다: {target}"
            )


def _complete_batch(connection: psycopg.Connection, records: list[dict]
                    ) -> tuple[list[dict], list[tuple[dict, str]], list[tuple[dict, str]]]:
    completed: list[dict] = []
    deferred: list[tuple[dict, str]] = []
    failed: list[tuple[dict, str]] = []
    with connection.transaction():
        for record in records:
            try:
                # A savepoint keeps one unavailable job from rolling back peers.
                with connection.transaction():
                    _complete_prepared(connection, record["article_id"], record["article"],
                                       record["body"], record["rules"])
                completed.append(record)
            except PreparedImportDeferred as error:
                deferred.append((record, str(error)))
            except Exception as error:
                failed.append((record, f"{type(error).__name__}: {error}"[:500]))
    return completed, deferred, failed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--ledger", type=Path,
                        default=Path("/app/data/maintenance/prepared-news-imports.sqlite3"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=25,
                        help="BODY/RULE completions committed together (default: 25)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("limit must be non-negative")
    if not 1 <= args.batch_size <= 250:
        parser.error("batch-size must be between 1 and 250")
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
    imported = skipped = failed = deferred = batches_committed = 0
    with closing(sqlite3.connect(f"file:{prepared.as_posix()}?mode=ro",
                                 uri=True)) as source, closing(_open_ledger(args.ledger)) as ledger, \
         psycopg.connect(database_url) as connection:
        rows = source.execute(
            "SELECT scope,stock_code,identity,article_json,body_json,rules_json "
            "FROM prepared_news WHERE state='ready' ORDER BY rowid")
        stop = False
        while not stop:
            batch = rows.fetchmany(args.batch_size)
            if not batch:
                break
            ready: list[dict] = []
            for scope, code, identity, article_raw, body_raw, rules_raw in batch:
                article, body, rules = json.loads(article_raw), json.loads(body_raw), json.loads(rules_raw)
                digest = _prepared_hash(article, body, rules)
                prior = ledger.execute("SELECT prepared_hash FROM imported_prepared_news "
                                       "WHERE scope=? AND stock_code=? AND identity=?",
                                       (scope, code, identity)).fetchone()
                if prior and prior[0] == digest:
                    skipped += 1
                    continue
                if args.limit and imported + failed + deferred + len(ready) >= args.limit:
                    stop = True
                    break
                record = {"scope": scope, "code": code, "identity": identity,
                          "article": article, "body": body, "rules": rules, "digest": digest}
                try:
                    record["article_id"] = _prepare_article(store, connection, scope, article)
                except Exception as error:
                    failed += 1
                    print(json.dumps({"state": "failed", "scope": scope, "stock_code": code,
                                      "identity": identity,
                                      "error": f"{type(error).__name__}: {error}"[:500]},
                                     ensure_ascii=False), flush=True)
                    connection.rollback()
                    continue
                ready.append(record)

            if ready:
                # Per-article savepoints isolate worker ownership races and bad rows,
                # while one outer commit amortizes WAL flushes across the batch.
                completed, deferred_rows, failed_rows = _complete_batch(connection, ready)
                deferred += len(deferred_rows)
                failed += len(failed_rows)
                for record, reason in deferred_rows:
                    print(json.dumps({"state": "deferred", "scope": record["scope"],
                                      "stock_code": record["code"], "identity": record["identity"],
                                      "reason": reason[:300]}, ensure_ascii=False), flush=True)
                for record, error in failed_rows:
                    print(json.dumps({"state": "failed", "scope": record["scope"],
                                      "stock_code": record["code"], "identity": record["identity"],
                                      "error": error}, ensure_ascii=False), flush=True)
                ledger.executemany(
                    "INSERT INTO imported_prepared_news VALUES(?,?,?,?,?) "
                    "ON CONFLICT(scope,stock_code,identity) DO UPDATE SET "
                    "prepared_hash=excluded.prepared_hash,imported_at=excluded.imported_at",
                    [(record["scope"], record["code"], record["identity"],
                      record["digest"], time.time()) for record in completed],
                )
                ledger.commit()
                imported += len(completed)
                batches_committed += 1
                if imported and imported % 100 < args.batch_size:
                    print(json.dumps({"imported": imported, "skipped": skipped,
                                      "failed": failed, "deferred": deferred,
                                      "batches_committed": batches_committed,
                                      "batch_size": args.batch_size}), flush=True)
    print(json.dumps({"imported": imported, "skipped": skipped, "failed": failed,
                      "deferred": deferred, "batches_committed": batches_committed,
                      "batch_size": args.batch_size}), flush=True)
    return 1 if failed else 2 if deferred else 0


if __name__ == "__main__":
    raise SystemExit(main())
