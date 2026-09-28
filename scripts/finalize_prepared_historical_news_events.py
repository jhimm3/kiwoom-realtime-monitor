"""Append verified PC archive events, preserving seed IDs and transaction history."""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import UTC, datetime
import hashlib
import heapq
import json
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any
import uuid

ROOT = Path(__file__).resolve().parents[1]
for source in (ROOT, ROOT / "src"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from kiwoom_monitor.application.news_rules import (
    SupplyContractRuleResult, grouped_candidate_identities, rule_input_hash,
)
from kiwoom_monitor.central_server.database import _save_sqlite_news_event
from scripts.stage_prepared_historical_news_archive import MANIFEST_KEY, SCOPE, _sha256
from scripts.verify_prepared_historical_news_rules import VERIFY_KEY, _json


EVENT_KEY = "prepared_search_events"
POLICY = "prepared-search-event-order/v1"
LEASE_SECONDS = 60.0
EVENT_CODE_FILES = (
    "scripts/finalize_prepared_historical_news_events.py",
    "src/kiwoom_monitor/central_server/database.py",
    "src/kiwoom_monitor/application/news_rules.py",
    "src/kiwoom_monitor/application/news_grouping.py",
    "src/kiwoom_monitor/infrastructure/naver_news.py",
)


def _event_code_digest() -> str:
    digest = hashlib.sha256()
    for name in EVENT_CODE_FILES:
        data = (ROOT / name).read_bytes()
        digest.update(name.encode("utf-8"))
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def _sort_time(value: str | None) -> tuple[int, float]:
    if not value:
        return (1, 0)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return (1, 0)
        return (0, parsed.astimezone(UTC).timestamp())
    except (ValueError, OverflowError):
        return (1, 0)


def _ordered_worklist(rows: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    by_article = {str(row[4]): row for row in rows}
    if len(by_article) != len(rows):
        raise ValueError("multiple prepared RULE rows resolve to one article revision")
    children: dict[str, list[str]] = {key: [] for key in by_article}
    degree = {key: 0 for key in by_article}
    for article_id, row in by_article.items():
        parent = str(row[7] or "")
        if parent in by_article:
            children[parent].append(article_id)
            degree[article_id] = 1
    queue: list[tuple[Any, ...]] = []
    for article_id, row in by_article.items():
        if degree[article_id] == 0:
            heapq.heappush(queue, (*_sort_time(row[6]), str(row[0]), str(row[1]),
                                   article_id, str(row[5]), str(row[2])))
    ordered: list[tuple[Any, ...]] = []
    while queue:
        *_, article_id, _, _ = heapq.heappop(queue)
        row = by_article[article_id]
        ordered.append(row)
        for child in children[article_id]:
            degree[child] -= 1
            if degree[child] == 0:
                next_row = by_article[child]
                heapq.heappush(queue, (*_sort_time(next_row[6]), str(next_row[0]),
                                       str(next_row[1]), child, str(next_row[5]),
                                       str(next_row[2])))
    if len(ordered) != len(rows):
        raise ValueError("article revision parent graph contains a cycle")
    return ordered


def _freeze(archive: sqlite3.Connection, expected_sha256: str) -> dict[str, Any]:
    build = archive.execute("SELECT value_json FROM archive_build_manifest WHERE key='report'").fetchone()
    staged = archive.execute("SELECT value_json FROM archive_build_manifest WHERE key=?",
                             (MANIFEST_KEY,)).fetchone()
    verified = archive.execute("SELECT value_json FROM archive_build_manifest WHERE key=?",
                               (VERIFY_KEY,)).fetchone()
    if (not build or json.loads(build[0]).get("build_state") != "building" or
        not staged or json.loads(staged[0]).get("state") != "complete" or
        json.loads(staged[0]).get("source_sha256") != expected_sha256 or
        not verified or json.loads(verified[0]).get("source_sha256") != expected_sha256):
        raise ValueError("event input, RULE verification, or building archive is missing")
    incomplete = archive.execute(
        "SELECT progress_state,COUNT(*) FROM archive_input_progress WHERE scope=? "
        "AND progress_state NOT IN ('assessment_done','source_failed','event_done') "
        "GROUP BY progress_state", (SCOPE,),
    ).fetchall()
    if incomplete:
        raise ValueError(f"article/BODY/assessment input is incomplete: {incomplete}")
    missing = archive.execute(
        "SELECT COUNT(*) FROM archive_input_progress p LEFT JOIN archive_verified_rule_inputs v "
        "ON v.scope=p.scope AND v.stock_code=p.stock_code AND v.identity=p.identity "
        "WHERE p.scope=? AND p.progress_state IN ('assessment_done','event_done') "
        "AND (v.scope IS NULL OR v.outcome<>'verified' OR v.input_hash<>p.input_hash "
        "OR v.article_revision_id<>p.article_revision_id "
        "OR v.body_revision_id<>p.body_revision_id)", (SCOPE,),
    ).fetchone()[0]
    if missing:
        raise ValueError(f"{missing} RULE results are unverified or mismatched")
    rows = archive.execute(
        "SELECT v.stock_code,v.identity,v.input_hash,v.verification_fingerprint,"
        "v.article_revision_id,v.body_revision_id,a.published_at,a.revision_of "
        "FROM archive_verified_rule_inputs v JOIN central_news_article_revisions a "
        "ON a.article_revision_id=v.article_revision_id WHERE v.scope=? "
        "AND v.outcome='verified'", (SCOPE,),
    ).fetchall()
    ordered = _ordered_worklist(rows)
    digest = hashlib.sha256()
    for row in ordered:
        digest.update(_json(row).encode("utf-8"))
        digest.update(b"\n")
    config = {"policy": POLICY, "source_sha256": expected_sha256,
              "event_code_digest": _event_code_digest(),
              "verification": json.loads(verified[0]),
              "worklist_hash": digest.hexdigest(), "worklist_count": len(ordered)}
    archive.execute("BEGIN IMMEDIATE")
    try:
        archive.execute(
            "CREATE TABLE IF NOT EXISTS archive_event_worklist ("
            "ordinal INTEGER NOT NULL UNIQUE,scope TEXT NOT NULL,stock_code TEXT NOT NULL,"
            "identity TEXT NOT NULL,input_hash TEXT NOT NULL,verification_fingerprint TEXT NOT NULL,"
            "article_revision_id TEXT NOT NULL,body_revision_id TEXT NOT NULL,"
            "PRIMARY KEY(scope,stock_code,identity))"
        )
        archive.execute(
            "CREATE TABLE IF NOT EXISTS archive_event_resolution ("
            "scope TEXT NOT NULL,stock_code TEXT NOT NULL,identity TEXT NOT NULL,"
            "ordinal INTEGER NOT NULL UNIQUE,input_hash TEXT NOT NULL,"
            "verification_fingerprint TEXT NOT NULL,policy TEXT NOT NULL,"
            "outcome TEXT NOT NULL,reason TEXT NOT NULL,event_id TEXT,"
            "event_revision_id TEXT,membership_revision_id TEXT,"
            "event_revision_of TEXT,membership_revision_of TEXT,"
            "candidate_event_ids_json TEXT NOT NULL,computed_at REAL NOT NULL,"
            "PRIMARY KEY(scope,stock_code,identity))"
        )
        old = archive.execute("SELECT value_json FROM archive_build_manifest WHERE key=?",
                              (EVENT_KEY,)).fetchone()
        if old is None:
            if archive.execute("SELECT 1 FROM archive_event_worklist LIMIT 1").fetchone() or \
               archive.execute("SELECT 1 FROM archive_event_resolution LIMIT 1").fetchone():
                raise ValueError("event worklist exists without frozen manifest")
            archive.executemany(
                "INSERT INTO archive_event_worklist VALUES(?,?,?,?,?,?,?,?)",
                ((ordinal, SCOPE, row[0], row[1], row[2], row[3], row[4], row[5])
                 for ordinal, row in enumerate(ordered, 1)),
            )
            archive.execute("INSERT INTO archive_build_manifest(key,value_json) VALUES(?,?)",
                            (EVENT_KEY, _json(config)))
        elif json.loads(old[0]) != config:
            raise ValueError("frozen event inputs changed after worklist creation")
        else:
            actual = archive.execute(
                "SELECT ordinal,scope,stock_code,identity,input_hash,"
                "verification_fingerprint,article_revision_id,body_revision_id "
                "FROM archive_event_worklist ORDER BY ordinal"
            )
            for ordinal, row in enumerate(ordered, 1):
                expected = (ordinal, SCOPE, row[0], row[1], row[2], row[3], row[4], row[5])
                if actual.fetchone() != expected:
                    raise ValueError("frozen event worklist changed")
            if actual.fetchone() is not None:
                raise ValueError("frozen event worklist has extra rows")
        archive.commit()
    except BaseException:
        archive.rollback()
        raise
    return config


def _group_candidates(archive: sqlite3.Connection, stock: str, identity: str,
                      event_key: str | None) -> tuple[str, tuple[str, ...]]:
    by_identity = tuple(str(row[0]) for row in archive.execute(
        "SELECT DISTINCT m.event_id FROM central_news_event_membership_revisions m "
        "JOIN central_news_article_revisions a ON a.article_revision_id=m.article_revision_id "
        "JOIN central_news_event_revisions e ON e.event_revision_id=m.event_revision_id "
        "WHERE e.stock_code=? AND a.identity=?", (stock, identity),
    ))
    by_key: tuple[str, ...] = ()
    if event_key:
        by_key = tuple(str(row[0]) for row in archive.execute(
            "SELECT DISTINCT event_id FROM central_news_event_revisions "
            "WHERE stock_code=? AND event_key=?", (stock, event_key),
        ))
    if by_identity and by_key and set(by_identity) != set(by_key):
        return "identity_event_key", tuple(sorted(set(by_identity) | set(by_key)))
    if by_identity:
        return "same_identity", by_identity
    if by_key:
        return "event_key", by_key
    return "new_group", ()


def _acquire_run_lease(archive: sqlite3.Connection) -> str:
    owner = uuid.uuid4().hex
    archive.execute("BEGIN IMMEDIATE")
    try:
        archive.execute(
            "CREATE TABLE IF NOT EXISTS archive_event_builder_lease ("
            "lease_id INTEGER PRIMARY KEY CHECK(lease_id=1),"
            "owner TEXT NOT NULL,expires_at REAL NOT NULL)"
        )
        current = archive.execute(
            "SELECT owner,expires_at FROM archive_event_builder_lease WHERE lease_id=1"
        ).fetchone()
        if current and current[1] > time.time():
            raise ValueError("another archive event builder is active")
        archive.execute(
            "INSERT INTO archive_event_builder_lease VALUES(1,?,?) "
            "ON CONFLICT(lease_id) DO UPDATE SET owner=excluded.owner,"
            "expires_at=excluded.expires_at",
            (owner, time.time() + LEASE_SECONDS),
        )
        archive.commit()
    except BaseException:
        archive.rollback()
        raise
    return owner


def _release_run_lease(archive: sqlite3.Connection, owner: str) -> None:
    archive.execute("BEGIN IMMEDIATE")
    try:
        archive.execute(
            "DELETE FROM archive_event_builder_lease WHERE lease_id=1 AND owner=?",
            (owner,),
        )
        archive.commit()
    except BaseException:
        archive.rollback()
        raise


def _finish_one(archive: sqlite3.Connection, row: tuple[Any, ...], owner: str) -> str:
    ordinal, stock, identity, input_hash, fingerprint, article_id, body_id = row
    archive.execute("BEGIN IMMEDIATE")
    try:
        lease = archive.execute(
            "SELECT owner FROM archive_event_builder_lease WHERE lease_id=1"
        ).fetchone()
        if lease != (owner,):
            raise ValueError("archive event builder lost its single-writer lease")
        progress = archive.execute(
            "SELECT progress_state,input_hash,article_revision_id,body_revision_id "
            "FROM archive_input_progress WHERE scope=? AND stock_code=? AND identity=?",
            (SCOPE, stock, identity),
        ).fetchone()
        verified = archive.execute(
            "SELECT outcome,verification_fingerprint,computed_json,rule_version "
            "FROM archive_verified_rule_inputs WHERE scope=? AND stock_code=? AND identity=?",
            (SCOPE, stock, identity),
        ).fetchone()
        if (progress != ("assessment_done", input_hash, article_id, body_id) or
            verified is None or verified[0] != "verified" or verified[1] != fingerprint):
            raise ValueError("event input lost its verified progress owner")
        computed = json.loads(verified[2])
        result = computed["rule_result"]
        outcome = reason = "no_event"
        event_id = event_revision_id = membership_id = event_parent = membership_parent = None
        candidate_event_ids: tuple[str, ...] = ()
        if result is not None:
            if not isinstance(result, dict) or result.get("rule_version") != verified[3]:
                raise ValueError("verified event result has an invalid rule version")
            typed = SupplyContractRuleResult(**result)
            if _json(typed.as_document()) != _json(result):
                raise ValueError("verified event result is not the rule document")
            rule_hash = rule_input_hash(article_id, body_id, typed)
            exact = archive.execute(
                "SELECT event_revision_id,event_id FROM central_news_event_revisions "
                "WHERE article_revision_id=? AND body_revision_id=? AND rule_version=? "
                "AND input_hash=? AND stock_code=?",
                (article_id, body_id, verified[3], rule_hash, stock),
            ).fetchone()
            if exact:
                outcome = reason = "exact_reuse"
                event_revision_id, event_id = str(exact[0]), str(exact[1])
            else:
                reason, groups = _group_candidates(archive, stock, identity,
                                                    result.get("event_key"))
                candidate_event_ids = tuple(sorted(groups))
                if len(groups) > 1:
                    outcome, reason = "conflict", f"ambiguous_{reason}"
                else:
                    article = archive.execute(
                        "SELECT document_json FROM central_news_article_revisions "
                        "WHERE article_revision_id=?", (article_id,),
                    ).fetchone()
                    if article is None:
                        raise ValueError("verified event article disappeared")
                    recent = [dict(json.loads(item[0]), identity=item[1]) for item in archive.execute(
                        "SELECT document_json,identity FROM central_news_article_revisions "
                        "WHERE stock_code=? AND article_revision_id<>? "
                        "ORDER BY accepted_sequence DESC LIMIT 100", (stock, article_id),
                    )]
                    hints = grouped_candidate_identities(
                        dict(json.loads(article[0]), identity=identity), recent,
                    )
                    event_revision_id = _save_sqlite_news_event(archive, {
                        "stock_code": stock, "article_revision_id": article_id,
                        "body_revision_id": body_id, "rule_version": verified[3],
                        "input_hash": rule_hash, "candidate_identities": hints,
                        "result": result,
                    })
                    outcome = "new_group" if not groups else "connected"
            if event_revision_id:
                event = archive.execute(
                    "SELECT event_id,revision_of,available_at FROM central_news_event_revisions "
                    "WHERE event_revision_id=? AND article_revision_id=? AND body_revision_id=?",
                    (event_revision_id, article_id, body_id),
                ).fetchone()
                members = archive.execute(
                    "SELECT membership_revision_id,revision_of FROM central_news_event_membership_revisions "
                    "WHERE event_revision_id=? AND article_revision_id=? AND body_revision_id=?",
                    (event_revision_id, article_id, body_id),
                ).fetchall()
                if event is None or len(members) != 1 or (event_id and event_id != event[0]):
                    raise ValueError("event revision has no unique matching membership")
                event_id, event_parent = event[0], event[1]
                membership_id, membership_parent = members[0]
        archive.execute(
            "INSERT INTO archive_event_resolution("
            "scope,stock_code,identity,ordinal,input_hash,verification_fingerprint,"
            "policy,outcome,reason,event_id,event_revision_id,membership_revision_id,"
            "event_revision_of,membership_revision_of,candidate_event_ids_json,computed_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (SCOPE, stock, identity, ordinal, input_hash, fingerprint, POLICY,
             outcome, reason, event_id, event_revision_id, membership_id,
             event_parent, membership_parent, _json(candidate_event_ids), time.time()),
        )
        if outcome != "conflict" and archive.execute(
            "UPDATE archive_input_progress SET progress_state='event_done' "
            "WHERE scope=? AND stock_code=? AND identity=? "
            "AND progress_state='assessment_done' AND input_hash=?",
            (SCOPE, stock, identity, input_hash),
        ).rowcount != 1:
            raise ValueError("event progress update lost ownership")
        if archive.execute(
            "UPDATE archive_event_builder_lease SET expires_at=? "
            "WHERE lease_id=1 AND owner=?",
            (time.time() + LEASE_SECONDS, owner),
        ).rowcount != 1:
            raise ValueError("archive event builder lease disappeared")
        archive.commit()
    except BaseException:
        archive.rollback()
        raise
    return outcome


def finalize(prepared_path: Path, archive_path: Path, expected_sha256: str,
             *, max_rows: int = 0) -> dict[str, Any]:
    if max_rows < 0:
        raise ValueError("max_rows must be non-negative")
    prepared_path = prepared_path.resolve(strict=True)
    archive_path = archive_path.resolve(strict=True)
    if prepared_path == archive_path or _sha256(prepared_path) != expected_sha256:
        raise ValueError("frozen event source changed")
    wal = prepared_path.with_name(prepared_path.name + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise ValueError("frozen event source has uncheckpointed WAL")
    with closing(sqlite3.connect(archive_path, timeout=30)) as archive:
        archive.execute("PRAGMA busy_timeout=30000")
        config = _freeze(archive, expected_sha256)
        owner = _acquire_run_lease(archive)
        try:
            counts = {"exact_reuse": 0, "new_group": 0, "connected": 0,
                      "no_event": 0, "conflict": 0}
            blocked = archive.execute(
                "SELECT ordinal,reason FROM archive_event_resolution "
                "WHERE outcome='conflict' ORDER BY ordinal LIMIT 1"
            ).fetchone()
            resolved_count, last_ordinal = archive.execute(
                "SELECT COUNT(*),COALESCE(MAX(ordinal),0) FROM archive_event_resolution"
            ).fetchone()
            if resolved_count != last_ordinal or last_ordinal > config["worklist_count"]:
                raise ValueError("event resolution ledger is not a contiguous worklist prefix")
            while blocked is None and (not max_rows or sum(counts.values()) < max_rows):
                row = archive.execute(
                    "SELECT ordinal,stock_code,identity,input_hash,verification_fingerprint,"
                    "article_revision_id,body_revision_id FROM archive_event_worklist "
                    "WHERE ordinal>? ORDER BY ordinal LIMIT 1", (last_ordinal,),
                ).fetchone()
                if row is None:
                    break
                outcome = _finish_one(archive, row, owner)
                counts[outcome] += 1
                last_ordinal = row[0]
                if outcome == "conflict":
                    blocked = (row[0], "ambiguous event group")
            if _sha256(prepared_path) != expected_sha256:
                raise ValueError("frozen event source changed during finalization")
            remaining = config["worklist_count"] - archive.execute(
                "SELECT COUNT(*) FROM archive_event_resolution"
            ).fetchone()[0]
            return {"processed_now": sum(counts.values()), "outcomes_now": counts,
                    "remaining": remaining, "blocked_at": blocked,
                    "worklist_hash": config["worklist_hash"],
                    "archive_build_state": "building"}
        finally:
            _release_run_lease(archive, owner)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--max-rows", type=int, default=0)
    args = parser.parse_args()
    print(_json(finalize(args.prepared, args.archive, args.expected_sha256,
                         max_rows=args.max_rows)))


if __name__ == "__main__":
    main()
