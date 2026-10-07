"""Materialize frozen PC search assessments without running NAS news jobs.

This is an unfinished archive stage. Event grouping and archive sealing remain
separate because they depend on chronological revision and seed history.
"""

from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.stage_prepared_historical_news_archive import MANIFEST_KEY, SCOPE, _input_hash, _sha256


ASSESSMENT_KEY = "prepared_search_assessments"
RULE_VERSION = "supply-contract-rule-v2"


def _finish_one(archive: sqlite3.Connection, prepared: sqlite3.Connection,
                stock: str, identity: str, input_hash: str,
                article_id: str, body_id: str) -> str:
    row = prepared.execute(
        "SELECT stock_code,identity,state,article_json,body_json,rules_json,error,updated_at "
        "FROM prepared_news WHERE scope=? AND stock_code=? AND identity=?",
        (SCOPE, stock, identity),
    ).fetchone()
    if row is None or row[2] != "ready" or _input_hash(tuple(row)) != input_hash:
        raise ValueError("prepared article changed after staging")
    article, body, rules = (json.loads(row[index]) for index in (3, 4, 5))
    if not isinstance(rules, list) or len(rules) != 1 or not isinstance(rules[0], dict):
        raise ValueError("search article must have exactly one prepared target")
    rule = rules[0]
    targets = article.get("targets")
    if (not isinstance(targets, list) or len(targets) != 1 or
        str(targets[0][0]) != stock or str(rule.get("target_id")) != stock):
        raise ValueError("prepared RULE target differs from article target")
    assessment, sentences, result = (rule.get(key) for key in
                                     ("assessment", "core_sentences", "rule_result"))
    if not isinstance(assessment, dict) or not isinstance(sentences, list):
        raise ValueError("prepared assessment has invalid shape")
    if result is not None and (not isinstance(result, dict) or
                               result.get("rule_version") != RULE_VERSION):
        raise ValueError("prepared RULE result has unsupported version")
    original_at = str(body.get("original_published_at") or "")
    document = article.get("document")
    if not isinstance(document, dict):
        raise ValueError("prepared article has no document")
    listing_at = str(document.get("published_at") or "")
    assessment_doc: dict[str, Any] = {
        "article_revision_id": article_id,
        "body_revision_id": body_id,
        "rule_version": "stock-news-assessment-v1",
        "published_at": original_at or listing_at,
        "listing_published_at": listing_at,
        "original_published_at": original_at,
        "published_at_source": "article_html" if original_at else "listing",
        "assessment": assessment,
        "core_sentences": sentences,
    }
    archive.execute("BEGIN IMMEDIATE")
    try:
        current = archive.execute(
            "SELECT progress_state,input_hash,article_revision_id,body_revision_id "
            "FROM archive_input_progress WHERE scope=? AND stock_code=? AND identity=?",
            (SCOPE, stock, identity),
        ).fetchone()
        if current != ("article_body_done", input_hash, article_id, body_id):
            raise ValueError("article/BODY progress changed before assessment")
        existing = archive.execute(
            "SELECT document_json FROM central_documents WHERE collection='news_assessment' "
            "AND owner=? AND document_key=?", (stock, article_id),
        ).fetchone()
        if existing is None:
            archive.execute(
                "INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
                "VALUES(?,?,?,?,?)",
                ("news_assessment", stock, article_id, time.time(),
                 json.dumps(assessment_doc, ensure_ascii=False, separators=(",", ":"))),
            )
        elif json.loads(existing[0]) != assessment_doc:
            raise ValueError("seed assessment conflicts with frozen prepared assessment")
        archive.execute(
            "INSERT INTO archive_prepared_rule_inputs("
            "scope,stock_code,identity,input_hash,article_revision_id,body_revision_id,"
            "target_id,rule_version,rule_result_json) VALUES(?,?,?,?,?,?,?,?,?)",
            (SCOPE, stock, identity, input_hash, article_id, body_id, stock,
             RULE_VERSION, json.dumps(result, ensure_ascii=False,
                                      sort_keys=True, separators=(",", ":"))),
        )
        if archive.execute(
            "UPDATE archive_input_progress SET progress_state='assessment_done' "
            "WHERE scope=? AND stock_code=? AND identity=? "
            "AND input_hash=? AND progress_state='article_body_done'",
            (SCOPE, stock, identity, input_hash),
        ).rowcount != 1:
            raise ValueError("assessment progress update lost ownership")
        archive.commit()
    except BaseException:
        archive.rollback()
        raise
    return "has_event_candidate" if result is not None else "no_event_candidate"


def finalize(prepared_path: Path, archive_path: Path, expected_sha256: str,
             *, max_rows: int = 0) -> dict[str, Any]:
    if max_rows < 0:
        raise ValueError("max_rows must be non-negative")
    prepared_path = prepared_path.resolve(strict=True)
    archive_path = archive_path.resolve(strict=True)
    if prepared_path == archive_path or _sha256(prepared_path) != expected_sha256:
        raise ValueError("prepared snapshot is missing, changed, or is the archive")
    wal_path = prepared_path.with_name(prepared_path.name + "-wal")
    if wal_path.exists() and wal_path.stat().st_size:
        raise ValueError("prepared snapshot has uncheckpointed WAL")
    with closing(sqlite3.connect(
        f"file:{prepared_path.as_posix()}?mode=ro", uri=True,
    )) as prepared, closing(sqlite3.connect(archive_path, timeout=30)) as archive:
        build = archive.execute(
            "SELECT value_json FROM archive_build_manifest WHERE key='report'"
        ).fetchone()
        staged = archive.execute(
            "SELECT value_json FROM archive_build_manifest WHERE key=?", (MANIFEST_KEY,)
        ).fetchone()
        if (not build or json.loads(build[0]).get("build_state") != "building" or
            not staged or json.loads(staged[0]).get("state") != "complete" or
            json.loads(staged[0]).get("source_sha256") != expected_sha256):
            raise ValueError("archive has no unfinished, fully staged input")
        archive.execute("BEGIN IMMEDIATE")
        try:
            archive.execute(
                "CREATE TABLE IF NOT EXISTS archive_prepared_rule_inputs ("
                "scope TEXT NOT NULL,stock_code TEXT NOT NULL,identity TEXT NOT NULL,"
                "input_hash TEXT NOT NULL,article_revision_id TEXT NOT NULL,"
                "body_revision_id TEXT NOT NULL,target_id TEXT NOT NULL,"
                "rule_version TEXT NOT NULL,rule_result_json TEXT NOT NULL,"
                "PRIMARY KEY(scope,stock_code,identity))"
            )
            config = {"schema": "prepared-search-assessments/v1",
                      "prepared_sha256": expected_sha256,
                      "rule_version": RULE_VERSION}
            old = archive.execute(
                "SELECT value_json FROM archive_build_manifest WHERE key=?",
                (ASSESSMENT_KEY,),
            ).fetchone()
            if old is None:
                if archive.execute(
                    "SELECT 1 FROM archive_prepared_rule_inputs LIMIT 1"
                ).fetchone():
                    raise ValueError("RULE rows exist without manifest")
                archive.execute(
                    "INSERT INTO archive_build_manifest(key,value_json) VALUES(?,?)",
                    (ASSESSMENT_KEY, json.dumps(config, sort_keys=True)),
                )
            elif json.loads(old[0]) != config:
                raise ValueError("RULE finalizer configuration changed")
            archive.commit()
        except BaseException:
            archive.rollback()
            raise
        counts = {"has_event_candidate": 0, "no_event_candidate": 0}
        processed = 0
        while not max_rows or processed < max_rows:
            next_row = archive.execute(
                "SELECT stock_code,identity,input_hash,article_revision_id,body_revision_id "
                "FROM archive_input_progress WHERE scope=? AND progress_state='article_body_done' "
                "ORDER BY stock_code,identity LIMIT 1", (SCOPE,),
            ).fetchone()
            if next_row is None:
                break
            kind = _finish_one(archive, prepared, *next_row)
            counts[kind] += 1
            processed += 1
        if _sha256(prepared_path) != expected_sha256:
            raise ValueError("prepared snapshot changed during assessment finalization")
        remaining = archive.execute(
            "SELECT COUNT(*) FROM archive_input_progress WHERE scope=? "
            "AND progress_state='article_body_done'", (SCOPE,),
        ).fetchone()[0]
        return {"processed_now": processed, "remaining_article_body_done": remaining,
                "outcomes_now": counts, "archive_build_state": "building"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--max-rows", type=int, default=0)
    args = parser.parse_args()
    print(json.dumps(finalize(args.prepared, args.archive, args.expected_sha256,
                              max_rows=args.max_rows), sort_keys=True))


if __name__ == "__main__":
    main()
