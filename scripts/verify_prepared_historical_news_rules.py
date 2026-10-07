"""Verify frozen prepared RULE results on the PC before event finalization.

This stage only reads the prepared snapshot and existing archive evidence. It
records both successful matches and mismatches; neither result seals the file.
"""

from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for source in (ROOT, ROOT / "src"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from kiwoom_monitor.central_server.database import stable_document_hash
from kiwoom_monitor.application.news_rules import SUPPLY_CONTRACT_RULE_VERSION
from scripts.preprocess_historical_news_to_nas import prepare_job
from scripts.stage_prepared_historical_news_archive import MANIFEST_KEY, SCOPE, _input_hash, _sha256


VERIFY_KEY = "prepared_search_rule_verification"
POLICY = "prepared-search-rule-verification/v1"
CODE_FILES = (
    "scripts/verify_prepared_historical_news_rules.py",
    "scripts/preprocess_historical_news_to_nas.py",
    "src/kiwoom_monitor/application/news_analysis.py",
    "src/kiwoom_monitor/application/news_rules.py",
    "src/kiwoom_monitor/application/news_grouping.py",
    "src/kiwoom_monitor/infrastructure/article_text.py",
    "src/kiwoom_monitor/infrastructure/naver_news.py",
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _code_digest() -> str:
    digest = hashlib.sha256()
    for name in CODE_FILES:
        data = (ROOT / name).read_bytes()
        digest.update(name.encode("utf-8"))
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def _fingerprint(input_hash: str, article_hash: str, body_hash: str,
                 body_status: str, provenance: tuple[str, str, str],
                 target: str, name: str, code_digest: str) -> str:
    return hashlib.sha256(_json((POLICY, input_hash, article_hash, body_hash,
                                 body_status, provenance, target, name,
                                 code_digest)).encode("utf-8")).hexdigest()


def _verify_one(archive: sqlite3.Connection, prepared: sqlite3.Connection,
                stock: str, identity: str, input_hash: str, article_id: str,
                body_id: str, code_digest: str) -> str:
    source = prepared.execute(
        "SELECT stock_code,identity,state,article_json,body_json,rules_json,error,updated_at "
        "FROM prepared_news WHERE scope=? AND stock_code=? AND identity=?",
        (SCOPE, stock, identity),
    ).fetchone()
    if source is None or source[2] != "ready" or _input_hash(tuple(source)) != input_hash:
        raise ValueError("prepared RULE input changed after staging")
    article, body, prepared_rules = (json.loads(source[i]) for i in (3, 4, 5))
    if not isinstance(prepared_rules, list) or len(prepared_rules) != 1:
        raise ValueError("search RULE input does not have exactly one target")
    prepared_rule = prepared_rules[0]
    targets = article.get("targets")
    if (not isinstance(prepared_rule, dict) or not isinstance(targets, list) or
        len(targets) != 1 or str(targets[0][0]) != stock or
        str(prepared_rule.get("target_id")) != stock or
        str(prepared_rule.get("stock_name")) != str(targets[0][1])):
        raise ValueError("prepared RULE target differs from frozen article")
    article_row = archive.execute(
        "SELECT content_hash,document_json FROM central_news_article_revisions "
        "WHERE article_revision_id=? AND stock_code=? AND identity=?",
        (article_id, stock, identity),
    ).fetchone()
    body_row = archive.execute(
        "SELECT article_revision_id,content_hash,status,body_text "
        "FROM central_news_body_revisions WHERE body_revision_id=?", (body_id,),
    ).fetchone()
    if (article_row is None or body_row is None or body_row[0] != article_id or
        article_row[0] != stable_document_hash(article["document"]) or
        json.loads(article_row[1]) != article["document"] or
        body_row[1] != stable_document_hash({"body": body["body_text"]}) or
        body_row[2] != body["body_status"] or body_row[3] != body["body_text"]):
        raise ValueError("archived article/BODY differs from frozen RULE input")
    provenance_row = archive.execute(
        "SELECT source_status,extractor_version,raw_body_sha256,prepared_input_hash "
        "FROM archive_body_provenance WHERE scope=? AND stock_code=? AND identity=? "
        "AND article_revision_id=? AND body_revision_id=?",
        (SCOPE, stock, identity, article_id, body_id),
    ).fetchone()
    if provenance_row is None or provenance_row[3] != input_hash:
        raise ValueError("archived BODY provenance differs from frozen RULE input")
    provenance = tuple(str(value) for value in provenance_row[:3])
    name = str(prepared_rule["stock_name"])
    computed = prepare_job(
        {"job_key": "pc-archive-verification", "attempts": 1, "stage": "RULE",
         "target_id": stock, "payload": {"stock_code": stock, "stock_name": name}},
        article, {"body_text": body["body_text"], "status": body["body_status"]},
        allow_network=False,
    )
    result = computed["rule_result"]
    if result is not None and result.get("rule_version") != SUPPLY_CONTRACT_RULE_VERSION:
        raise ValueError("computed RULE version changed")
    prepared_value = {key: prepared_rule.get(key) for key in
                      ("assessment", "core_sentences", "rule_result")}
    computed_value = {key: computed[key] for key in prepared_value}
    outcome = "verified" if _json(prepared_value) == _json(computed_value) else "mismatch"
    fingerprint = _fingerprint(input_hash, article_row[0], body_row[1],
                               body_row[2], provenance, stock, name, code_digest)
    archive.execute("BEGIN IMMEDIATE")
    try:
        progress = archive.execute(
            "SELECT progress_state,input_hash,article_revision_id,body_revision_id "
            "FROM archive_input_progress WHERE scope=? AND stock_code=? AND identity=?",
            (SCOPE, stock, identity),
        ).fetchone()
        if progress != ("assessment_done", input_hash, article_id, body_id):
            raise ValueError("RULE verification lost its progress owner")
        provisional = archive.execute(
            "SELECT input_hash,article_revision_id,body_revision_id,target_id,rule_result_json "
            "FROM archive_prepared_rule_inputs WHERE scope=? AND stock_code=? AND identity=?",
            (SCOPE, stock, identity),
        ).fetchone()
        if provisional != (input_hash, article_id, body_id, stock,
                           _json(prepared_rule.get("rule_result"))):
            raise ValueError("staged RULE result differs from frozen prepared input")
        if archive.execute(
            "SELECT 1 FROM archive_verified_rule_inputs WHERE scope=? AND stock_code=? AND identity=?",
            (SCOPE, stock, identity),
        ).fetchone():
            raise ValueError("RULE verification already recorded")
        archive.execute(
            "INSERT INTO archive_verified_rule_inputs("
            "scope,stock_code,identity,input_hash,article_revision_id,body_revision_id,"
            "verification_fingerprint,code_digest,rule_version,body_provenance_status,"
            "prepared_json,computed_json,outcome,verified_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (SCOPE, stock, identity, input_hash, article_id, body_id, fingerprint,
             code_digest, SUPPLY_CONTRACT_RULE_VERSION, provenance[0], _json(prepared_value),
             _json(computed_value), outcome, time.time()),
        )
        archive.commit()
    except BaseException:
        archive.rollback()
        raise
    return outcome


def verify(prepared_path: Path, archive_path: Path, expected_sha256: str,
           *, max_rows: int = 0) -> dict[str, Any]:
    if max_rows < 0:
        raise ValueError("max_rows must be non-negative")
    prepared_path = prepared_path.resolve(strict=True)
    archive_path = archive_path.resolve(strict=True)
    if prepared_path == archive_path or _sha256(prepared_path) != expected_sha256:
        raise ValueError("frozen prepared RULE snapshot changed")
    wal = prepared_path.with_name(prepared_path.name + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise ValueError("frozen prepared RULE snapshot has uncheckpointed WAL")
    digest = _code_digest()
    with closing(sqlite3.connect(f"file:{prepared_path.as_posix()}?mode=ro", uri=True)) as prepared, \
         closing(sqlite3.connect(archive_path, timeout=30)) as archive:
        archive.execute("PRAGMA busy_timeout=30000")
        build = archive.execute(
            "SELECT value_json FROM archive_build_manifest WHERE key='report'"
        ).fetchone()
        staged = archive.execute(
            "SELECT value_json FROM archive_build_manifest WHERE key=?", (MANIFEST_KEY,)
        ).fetchone()
        if (not build or json.loads(build[0]).get("build_state") != "building" or
            not staged or json.loads(staged[0]).get("state") != "complete" or
            json.loads(staged[0]).get("source_sha256") != expected_sha256):
            raise ValueError("archive has no complete frozen staged RULE input")
        config = {"policy": POLICY, "source_sha256": expected_sha256,
                  "code_digest": digest, "rule_version": SUPPLY_CONTRACT_RULE_VERSION}
        archive.execute("BEGIN IMMEDIATE")
        try:
            archive.execute(
                "CREATE TABLE IF NOT EXISTS archive_verified_rule_inputs ("
                "scope TEXT NOT NULL,stock_code TEXT NOT NULL,identity TEXT NOT NULL,"
                "input_hash TEXT NOT NULL,article_revision_id TEXT NOT NULL,"
                "body_revision_id TEXT NOT NULL,verification_fingerprint TEXT NOT NULL,"
                "code_digest TEXT NOT NULL,rule_version TEXT NOT NULL,"
                "body_provenance_status TEXT NOT NULL,"
                "prepared_json TEXT NOT NULL,computed_json TEXT NOT NULL,"
                "outcome TEXT NOT NULL,verified_at REAL NOT NULL,"
                "PRIMARY KEY(scope,stock_code,identity))"
            )
            old = archive.execute(
                "SELECT value_json FROM archive_build_manifest WHERE key=?", (VERIFY_KEY,)
            ).fetchone()
            if old is None:
                if archive.execute("SELECT 1 FROM archive_verified_rule_inputs LIMIT 1").fetchone():
                    raise ValueError("verified RULE rows exist without manifest")
                archive.execute("INSERT INTO archive_build_manifest(key,value_json) VALUES(?,?)",
                                (VERIFY_KEY, _json(config)))
            elif json.loads(old[0]) != config:
                raise ValueError("RULE verification code or input changed within this generation")
            archive.commit()
        except BaseException:
            archive.rollback()
            raise
        counts = {"verified": 0, "mismatch": 0}
        last = ("", "")
        while not max_rows or sum(counts.values()) < max_rows:
            row = archive.execute(
                "SELECT p.stock_code,p.identity,p.input_hash,p.article_revision_id,p.body_revision_id "
                "FROM archive_input_progress p LEFT JOIN archive_verified_rule_inputs v "
                "ON v.scope=p.scope AND v.stock_code=p.stock_code AND v.identity=p.identity "
                "WHERE p.scope=? AND p.progress_state='assessment_done' "
                "AND (p.stock_code,p.identity)>(?,?) AND v.scope IS NULL "
                "ORDER BY p.stock_code,p.identity LIMIT 1",
                (SCOPE, *last),
            ).fetchone()
            if row is None:
                break
            counts[_verify_one(archive, prepared, *row, digest)] += 1
            last = (row[0], row[1])
        if _sha256(prepared_path) != expected_sha256:
            raise ValueError("prepared RULE snapshot changed during verification")
        remaining = archive.execute(
            "SELECT COUNT(*) FROM archive_input_progress p LEFT JOIN archive_verified_rule_inputs v "
            "ON v.scope=p.scope AND v.stock_code=p.stock_code AND v.identity=p.identity "
            "WHERE p.scope=? AND p.progress_state='assessment_done' AND v.scope IS NULL",
            (SCOPE,),
        ).fetchone()[0]
        return {"processed_now": sum(counts.values()), "outcomes_now": counts,
                "remaining_assessment_done": remaining, "code_digest": digest,
                "archive_build_state": "building"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--max-rows", type=int, default=0)
    args = parser.parse_args()
    print(_json(verify(args.prepared, args.archive, args.expected_sha256,
                       max_rows=args.max_rows)))


if __name__ == "__main__":
    main()
