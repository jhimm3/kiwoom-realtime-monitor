"""Complete prepared search article/BODY rows in the PC archive, resumably.

Each ready article, BODY, provenance record, and progress update shares one
SQLite transaction.  This stage never enqueues NAS jobs and deliberately does
not mark RULE/events or the archive as complete.
"""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any
import uuid

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from scripts.stage_prepared_historical_news_archive import (
    MANIFEST_KEY, SCOPE, _input_hash, _sha256,
)
from kiwoom_monitor.central_server.database import stable_document_hash
from kiwoom_monitor.domain.news_observation import ARTICLE_BODY_EXTRACTOR_VERSION


FINALIZER_KEY = "prepared_search_article_body"
UNVERIFIED_VERSION = "prepared-body-version-unverified-v1"
SUMMARY_VERSION = "prepared-summary-v1"


def _raw_evidence(raw_path: Path, cutoff: int) -> dict[tuple[str, str, str], set[tuple[str, str, float | None]]]:
    evidence: dict[tuple[str, str, str], set[tuple[str, str, float | None]]] = {}
    with closing(sqlite3.connect(f"file:{raw_path.as_posix()}?mode=ro", uri=True)) as raw:
        maximum = raw.execute(
            "SELECT COALESCE(MAX(rowid),0) FROM news_article_body_snapshots"
        ).fetchone()[0]
        if cutoff < 0 or cutoff > maximum:
            raise ValueError("raw BODY rowid cutoff is outside the source table")
        with closing(raw.execute(
            "SELECT provider,office_id,article_id,extractor_version,body_sha256,fetched_at "
            "FROM news_article_body_snapshots WHERE rowid<=?", (cutoff,),
        )) as rows:
            for provider, office, article, version, digest, fetched_at in rows:
                key = (str(provider), str(office), str(article))
                try:
                    fetched_epoch = datetime.fromisoformat(str(fetched_at)).timestamp()
                except (TypeError, ValueError, OverflowError):
                    fetched_epoch = None
                evidence.setdefault(key, set()).add((str(version), str(digest), fetched_epoch))
    return evidence


def _body_provenance(article: dict[str, Any], body: dict[str, Any],
                     evidence: dict[tuple[str, str, str], set[tuple[str, str, float | None]]]
                     ) -> tuple[str, str, str, str, str, str]:
    status = str(body.get("body_status") or "")
    source = article.get("document", {}).get("historical_source")
    key = tuple(str(source.get(part) or "") for part in
                ("provider", "office_id", "article_id")) if isinstance(source, dict) else ("", "", "")
    if status == "summary_only":
        return ("summary_from_prepared", SUMMARY_VERSION, *key, "")
    if status != "fulltext":
        raise ValueError("prepared BODY has an unsupported status")
    digest = hashlib.sha256(body["body_text"].encode("utf-8")).hexdigest()
    snapshots = evidence.get(key)
    if not snapshots:
        return ("version_unverified", UNVERIFIED_VERSION, *key, digest)
    matches = {(version, fetched_at) for version, raw_digest, fetched_at in snapshots
               if raw_digest == digest}
    if not matches:
        raise ValueError("prepared fulltext differs from captured raw BODY snapshots")
    prepared_fetched_at = float(body.get("fetched_at"))
    versions = {version for version, fetched_at in matches
                if fetched_at is not None and abs(fetched_at - prepared_fetched_at) <= 0.001}
    if not versions:
        return ("raw_hash_only", UNVERIFIED_VERSION, *key, digest)
    selected = (ARTICLE_BODY_EXTRACTOR_VERSION if ARTICLE_BODY_EXTRACTOR_VERSION in versions
                else sorted(versions)[0])
    return ("raw_hash_verified", selected, *key, digest)


def _complete_one(archive: sqlite3.Connection, prepared: sqlite3.Connection,
                  scope: str, stock: str, identity: str, expected_hash: str,
                  evidence: dict[tuple[str, str, str], set[tuple[str, str, float | None]]]) -> str:
    row = prepared.execute(
        "SELECT stock_code,identity,state,article_json,body_json,rules_json,error,updated_at "
        "FROM prepared_news WHERE scope=? AND stock_code=? AND identity=?",
        (scope, stock, identity),
    ).fetchone()
    if row is None or str(row[2]) != "ready" or _input_hash(tuple(row)) != expected_hash:
        raise ValueError("prepared article changed or disappeared after staging")
    article = json.loads(row[3])
    body = json.loads(row[4])
    document = article.get("document")
    if (not isinstance(document, dict) or str(article.get("stock_code")) != stock or
        str(article.get("identity")) != identity or
        str(document.get("stock_code")) != stock or
        str(document.get("identity")) != identity):
        raise ValueError("prepared article identity does not match its source key")
    body_text = body.get("body_text")
    if not isinstance(body_text, str) or not body_text.strip() or len(body_text) > 2_000_000:
        raise ValueError("prepared BODY is empty or exceeds the accepted size")
    body_status = str(body.get("body_status") or "")
    provenance, source_version, provider, office, source_article, raw_hash = (
        _body_provenance(article, body, evidence)
    )
    fetched_at = float(body.get("fetched_at"))
    if not math.isfinite(fetched_at) or fetched_at <= 0:
        raise ValueError("prepared BODY fetch time is invalid")
    content_hash = stable_document_hash(document)
    body_hash = stable_document_hash({"body": body_text})
    now = time.time()
    archive.execute("BEGIN IMMEDIATE")
    try:
        progress = archive.execute(
            "SELECT progress_state,input_hash FROM archive_input_progress "
            "WHERE scope=? AND stock_code=? AND identity=?",
            (scope, stock, identity),
        ).fetchone()
        if progress != ("pending", expected_hash):
            raise ValueError("article progress changed before the transaction")
        previous = archive.execute(
            "SELECT article_revision_id FROM central_news_article_revisions "
            "WHERE stock_code=? AND identity=? ORDER BY accepted_sequence DESC LIMIT 1",
            (stock, identity),
        ).fetchone()
        existing = archive.execute(
            "SELECT article_revision_id FROM central_news_article_revisions "
            "WHERE stock_code=? AND identity=? AND content_hash=? "
            "ORDER BY accepted_sequence DESC LIMIT 1",
            (stock, identity, content_hash),
        ).fetchone()
        article_id = str(existing[0]) if existing else uuid.uuid4().hex
        if not existing:
            archive.execute(
                "INSERT INTO central_news_article_revisions("
                "article_revision_id,stock_code,identity,content_hash,collector_id,"
                "published_at,received_at,available_at,collection_scope,revision_of,document_json) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (article_id, stock, identity, content_hash, "naver_historical_web",
                 document.get("published_at"), now, now, "historical_news_pc_backfill",
                 str(previous[0]) if previous else None,
                 json.dumps(document, ensure_ascii=False, separators=(",", ":"))),
            )
        existing_body = archive.execute(
            "SELECT body_revision_id,extractor_version FROM central_news_body_revisions "
            "WHERE article_revision_id=? AND content_hash=? AND status=? "
            "ORDER BY accepted_sequence DESC LIMIT 1",
            (article_id, body_hash, body_status),
        ).fetchone()
        body_id = str(existing_body[0]) if existing_body else uuid.uuid4().hex
        if existing_body:
            source_version = str(existing_body[1])
            if provenance in ("version_unverified", "raw_hash_only"):
                provenance = "seed_exact_body"
        else:
            archive.execute(
                "INSERT INTO central_news_body_revisions("
                "body_revision_id,article_revision_id,content_hash,extractor_version,"
                "fetched_at,available_at,status,body_text,error) VALUES(?,?,?,?,?,?,?,?,?)",
                (body_id, article_id, body_hash, source_version, fetched_at,
                 now, body_status, body_text, ""),
            )
        archive.execute(
            "INSERT INTO archive_body_provenance("
            "scope,stock_code,identity,article_revision_id,body_revision_id,"
            "source_status,extractor_version,provider,office_id,source_article_id,"
            "raw_body_sha256,prepared_input_hash,fetched_at,source_url) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (scope, stock, identity, article_id, body_id, provenance,
             source_version, provider, office, source_article, raw_hash,
             expected_hash, fetched_at, str(body.get("source_url") or "")),
        )
        changed = archive.execute(
            "UPDATE archive_input_progress SET progress_state='article_body_done',"
            "article_revision_id=?,body_revision_id=? "
            "WHERE scope=? AND stock_code=? AND identity=? "
            "AND progress_state='pending' AND input_hash=?",
            (article_id, body_id, scope, stock, identity, expected_hash),
        ).rowcount
        if changed != 1:
            raise ValueError("article progress update lost its ownership")
        archive.commit()
    except BaseException:
        archive.rollback()
        raise
    return provenance


def finalize(prepared_path: Path, archive_path: Path, raw_path: Path,
             expected_sha256: str, raw_rowid_cutoff: int,
             *, max_rows: int = 0) -> dict[str, Any]:
    if max_rows < 0:
        raise ValueError("max_rows must be non-negative")
    prepared_path = prepared_path.resolve(strict=True)
    archive_path = archive_path.resolve(strict=True)
    raw_path = raw_path.resolve(strict=True)
    if _sha256(prepared_path) != expected_sha256:
        raise ValueError("prepared snapshot hash changed")
    wal_path = prepared_path.with_name(prepared_path.name + "-wal")
    if wal_path.exists() and wal_path.stat().st_size:
        raise ValueError("prepared input has uncheckpointed WAL content")
    evidence = _raw_evidence(raw_path, raw_rowid_cutoff)
    with closing(sqlite3.connect(
        f"file:{prepared_path.as_posix()}?mode=ro", uri=True
    )) as prepared, closing(sqlite3.connect(archive_path, timeout=30)) as archive:
        archive.execute("PRAGMA busy_timeout=30000")
        build = archive.execute(
            "SELECT value_json FROM archive_build_manifest WHERE key='report'"
        ).fetchone()
        staged = archive.execute(
            "SELECT value_json FROM archive_build_manifest WHERE key=?", (MANIFEST_KEY,)
        ).fetchone()
        if not build or json.loads(build[0]).get("build_state") != "building" or not staged:
            raise ValueError("archive has no unfinished, fully staged input")
        staged_report = json.loads(staged[0])
        if (staged_report.get("state") != "complete" or
            staged_report.get("source_sha256") != expected_sha256 or
            staged_report.get("source_path") != str(prepared_path)):
            raise ValueError("prepared input does not match completed staging")
        archive.execute("BEGIN IMMEDIATE")
        try:
            archive.execute(
                "CREATE TABLE IF NOT EXISTS archive_body_provenance ("
                "scope TEXT NOT NULL,stock_code TEXT NOT NULL,identity TEXT NOT NULL,"
                "article_revision_id TEXT NOT NULL,body_revision_id TEXT NOT NULL,"
                "source_status TEXT NOT NULL,extractor_version TEXT NOT NULL,"
                "provider TEXT NOT NULL,office_id TEXT NOT NULL,source_article_id TEXT NOT NULL,"
                "raw_body_sha256 TEXT NOT NULL,prepared_input_hash TEXT NOT NULL,"
                "fetched_at REAL NOT NULL,source_url TEXT NOT NULL,"
                "PRIMARY KEY(scope,stock_code,identity))"
            )
            saved = archive.execute(
                "SELECT value_json FROM archive_build_manifest WHERE key=?",
                (FINALIZER_KEY,),
            ).fetchone()
            config = {"schema": "prepared-search-article-body/v1",
                      "prepared_sha256": expected_sha256,
                      "raw_path": str(raw_path), "raw_rowid_cutoff": raw_rowid_cutoff,
                      "body_version": ARTICLE_BODY_EXTRACTOR_VERSION}
            if saved is None:
                if archive.execute("SELECT 1 FROM archive_body_provenance LIMIT 1").fetchone():
                    raise ValueError("BODY provenance exists without a finalizer manifest")
                archive.execute(
                    "INSERT INTO archive_build_manifest(key,value_json) VALUES(?,?)",
                    (FINALIZER_KEY, json.dumps(config, sort_keys=True)),
                )
            elif json.loads(saved[0]) != config:
                raise ValueError("article/BODY finalizer input changed")
            archive.commit()
        except BaseException:
            archive.rollback()
            raise
        counts: dict[str, int] = {}
        processed = 0
        while not max_rows or processed < max_rows:
            next_row = archive.execute(
                "SELECT scope,stock_code,identity,input_hash FROM archive_input_progress "
                "WHERE scope=? AND progress_state='pending' "
                "ORDER BY stock_code,identity LIMIT 1", (SCOPE,),
            ).fetchone()
            if next_row is None:
                break
            provenance = _complete_one(archive, prepared, *next_row, evidence)
            counts[provenance] = counts.get(provenance, 0) + 1
            processed += 1
        if _sha256(prepared_path) != expected_sha256:
            raise ValueError("prepared snapshot changed during finalization")
        remaining = archive.execute(
            "SELECT COUNT(*) FROM archive_input_progress WHERE scope=? AND progress_state='pending'",
            (SCOPE,),
        ).fetchone()[0]
        return {"processed_now": processed, "remaining_pending": remaining,
                "body_provenance_now": counts,
                "archive_build_state": "building"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", required=True, type=Path)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--raw", required=True, type=Path)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--raw-rowid-cutoff", required=True, type=int)
    parser.add_argument("--max-rows", type=int, default=0)
    args = parser.parse_args()
    print(json.dumps(finalize(args.prepared, args.archive, args.raw,
                              args.expected_sha256, args.raw_rowid_cutoff,
                              max_rows=args.max_rows), sort_keys=True))


if __name__ == "__main__":
    main()
