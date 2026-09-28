"""Build the PC-owned search list projection after all frozen events are resolved.

The projection is an unfinished archive component.  This command does not seal,
publish, or copy the SQLite file, and never calls a NAS writer or news analyzer.
"""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import UTC, datetime
import hashlib
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

from kiwoom_monitor.infrastructure.article_text import clean_article_text

from scripts.finalize_prepared_historical_news_events import EVENT_KEY
from scripts.stage_prepared_historical_news_archive import SCOPE
from scripts.verify_prepared_historical_news_rules import VERIFY_KEY


PROJECTION_KEY = "prepared_search_projection"
PROJECTION_SCHEMA = "historical-search-projection/v1"
LEASE_SECONDS = 60.0


def _code_digest() -> str:
    digest = hashlib.sha256()
    for name in ("scripts/build_prepared_historical_search_projection.py",
                 "src/kiwoom_monitor/infrastructure/article_text.py"):
        data = (ROOT / name).read_bytes()
        digest.update(name.encode("utf-8"))
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _publication(value: str | None) -> tuple[int, int]:
    if not value:
        return 1, 0
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return 0, round(parsed.astimezone(UTC).timestamp() * 1_000_000)
    except (ValueError, OverflowError):
        return 1, 0


def _preflight(db: sqlite3.Connection) -> dict[str, Any]:
    report = db.execute(
        "SELECT value_json FROM archive_build_manifest WHERE key='report'"
    ).fetchone()
    event = db.execute(
        "SELECT value_json FROM archive_build_manifest WHERE key=?", (EVENT_KEY,)
    ).fetchone()
    verified = db.execute(
        "SELECT value_json FROM archive_build_manifest WHERE key=?", (VERIFY_KEY,)
    ).fetchone()
    if not report or json.loads(report[0]).get("build_state") != "building" or not event or not verified:
        raise ValueError("building archive and frozen event/RULE manifests are required")
    event_config = json.loads(event[0])
    if event_config.get("verification") != json.loads(verified[0]):
        raise ValueError("event and RULE verification generations differ")
    progress = dict(db.execute(
        "SELECT progress_state,COUNT(*) FROM archive_input_progress WHERE scope=? "
        "GROUP BY progress_state", (SCOPE,),
    ).fetchall())
    if not progress or set(progress) - {"event_done", "source_failed"}:
        raise ValueError(f"search input is not fully resolved: {progress}")
    ready = progress.get("event_done", 0)
    worklist = db.execute("SELECT COUNT(*) FROM archive_event_worklist").fetchone()[0]
    resolutions = db.execute(
        "SELECT COUNT(*) FROM archive_event_resolution WHERE outcome<>'conflict'"
    ).fetchone()[0]
    if ready != worklist or ready != resolutions or ready != event_config.get("worklist_count"):
        raise ValueError("search event worklist, resolution, and progress counts differ")
    if db.execute("SELECT 1 FROM archive_event_resolution WHERE outcome='conflict' LIMIT 1").fetchone():
        raise ValueError("search event group conflict remains")
    return {"schema": PROJECTION_SCHEMA, "projection_code_digest": _code_digest(),
            "event_worklist_hash": event_config["worklist_hash"],
            "event_code_digest": event_config["event_code_digest"],
            "source_sha256": event_config["source_sha256"],
            "ready_rows": ready, "failed_rows": progress.get("source_failed", 0)}


def _freeze_sources(db: sqlite3.Connection, config: dict[str, Any]) -> dict[str, Any]:
    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute(
            "CREATE TABLE IF NOT EXISTS archive_search_projection_sources ("
            "stock_code TEXT NOT NULL,identity TEXT NOT NULL,article_revision_id TEXT NOT NULL,"
            "body_revision_id TEXT,source_kind TEXT NOT NULL,accepted_sequence INTEGER NOT NULL,"
            "PRIMARY KEY(stock_code,identity))"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS archive_search_projection ("
            "stock_code TEXT NOT NULL,identity TEXT NOT NULL,article_revision_id TEXT NOT NULL,"
            "body_revision_id TEXT,event_id TEXT,event_revision_id TEXT,"
            "membership_revision_id TEXT,source_kind TEXT NOT NULL,body_status TEXT NOT NULL,"
            "assessment_status TEXT NOT NULL,sort_missing INTEGER NOT NULL,sort_us INTEGER NOT NULL,"
            "display_json TEXT NOT NULL,PRIMARY KEY(stock_code,identity))"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS archive_search_projection_page ON "
            "archive_search_projection(stock_code,sort_missing,sort_us DESC,identity)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS archive_membership_article_body ON "
            "central_news_event_membership_revisions(article_revision_id,body_revision_id)"
        )
        old = db.execute(
            "SELECT value_json FROM archive_build_manifest WHERE key=?", (PROJECTION_KEY,)
        ).fetchone()
        if old:
            manifest = json.loads(old[0])
            if {key: manifest.get(key) for key in config} != config:
                raise ValueError("frozen search projection inputs changed")
            if db.execute("SELECT COUNT(*) FROM archive_search_projection_sources").fetchone()[0] != manifest["sources"]:
                raise ValueError("search projection source count changed")
        else:
            if db.execute("SELECT 1 FROM archive_search_projection_sources LIMIT 1").fetchone() or \
               db.execute("SELECT 1 FROM archive_search_projection LIMIT 1").fetchone():
                raise ValueError("search projection rows exist without manifest")
            # The seed role is explicit: support_only articles must not become list owners.
            db.execute(
                "INSERT INTO archive_search_projection_sources "
                "SELECT a.stock_code,a.identity,a.article_revision_id,"
                "(SELECT b.body_revision_id FROM central_news_body_revisions b "
                "WHERE b.article_revision_id=a.article_revision_id "
                "ORDER BY b.accepted_sequence DESC LIMIT 1),'seed',a.accepted_sequence "
                "FROM central_news_article_revisions a JOIN archive_seed_roles r "
                "ON r.table_name='central_news_article_revisions' "
                "AND r.row_key=json_array(a.article_revision_id) AND r.role='historical' "
                "WHERE a.stock_code<>'GLOBAL' "
                "ORDER BY a.accepted_sequence "
                "ON CONFLICT(stock_code,identity) DO UPDATE SET "
                "article_revision_id=excluded.article_revision_id,"
                "body_revision_id=excluded.body_revision_id,source_kind=excluded.source_kind,"
                "accepted_sequence=excluded.accepted_sequence "
                "WHERE excluded.accepted_sequence>archive_search_projection_sources.accepted_sequence"
            )
            db.execute(
                "INSERT INTO archive_search_projection_sources "
                "SELECT p.stock_code,p.identity,p.article_revision_id,p.body_revision_id,"
                "'prepared',a.accepted_sequence "
                "FROM archive_input_progress p JOIN central_news_article_revisions a "
                "ON a.article_revision_id=p.article_revision_id "
                "WHERE p.scope=? AND p.progress_state='event_done' "
                "ORDER BY p.stock_code,p.identity "
                "ON CONFLICT(stock_code,identity) DO UPDATE SET "
                "article_revision_id=excluded.article_revision_id,"
                "body_revision_id=excluded.body_revision_id,source_kind=excluded.source_kind,"
                "accepted_sequence=excluded.accepted_sequence "
                "WHERE excluded.accepted_sequence>=archive_search_projection_sources.accepted_sequence",
                (SCOPE,),
            )
            sources = db.execute(
                "SELECT COUNT(*) FROM archive_search_projection_sources"
            ).fetchone()[0]
            manifest = {**config, "sources": sources, "last_key": None,
                        "projected": 0, "state": "building"}
            db.execute("INSERT INTO archive_build_manifest(key,value_json) VALUES(?,?)",
                       (PROJECTION_KEY, _json(manifest)))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return manifest


def _row(db: sqlite3.Connection, source: tuple[str, str, str, str | None, str]) -> tuple[Any, ...]:
    stock, identity, article_id, body_id, source_kind = source
    article = db.execute(
        "SELECT document_json,published_at FROM central_news_article_revisions "
        "WHERE article_revision_id=? AND stock_code=? AND identity=?",
        (article_id, stock, identity),
    ).fetchone()
    if not article:
        raise ValueError("search projection article relation is missing")
    document = json.loads(article[0])
    if not isinstance(document, dict):
        raise ValueError("search projection article document is invalid")
    body = db.execute(
        "SELECT status,body_text FROM central_news_body_revisions "
        "WHERE body_revision_id=? AND article_revision_id=?", (body_id, article_id),
    ).fetchone() if body_id else None
    if body_id and not body:
        raise ValueError("search projection BODY relation is missing")
    body_status = str(body[0]) if body else "missing"
    clean = clean_article_text(str(body[1])) if body and body_status == "fulltext" else ""
    if len(clean) < 40:
        clean = ""
    resolution = db.execute(
        "SELECT event_id,event_revision_id,membership_revision_id,outcome "
        "FROM archive_event_resolution WHERE scope=? AND stock_code=? AND identity=? "
        "AND input_hash=(SELECT input_hash FROM archive_input_progress "
        "WHERE scope=? AND stock_code=? AND identity=?)",
        (SCOPE, stock, identity, SCOPE, stock, identity),
    ).fetchone() if source_kind == "prepared" else None
    if source_kind == "prepared" and not resolution:
        raise ValueError("prepared search projection lost event resolution")
    event_id = event_revision_id = membership_id = None
    if resolution:
        if resolution[3] == "conflict":
            raise ValueError("conflicted search event cannot be projected")
        event_id, event_revision_id, membership_id = resolution[:3]
    elif body_id:
        membership = db.execute(
            "SELECT event_id,event_revision_id,membership_revision_id "
            "FROM central_news_event_membership_revisions "
            "WHERE article_revision_id=? AND body_revision_id=? "
            "ORDER BY accepted_sequence DESC LIMIT 1", (article_id, body_id),
        ).fetchone()
        if membership:
            event_id, event_revision_id, membership_id = membership
    if source_kind == "prepared":
        verified = db.execute(
            "SELECT computed_json,outcome FROM archive_verified_rule_inputs "
            "WHERE scope=? AND stock_code=? AND identity=?",
            (SCOPE, stock, identity),
        ).fetchone()
        if not verified or verified[1] != "verified":
            raise ValueError("prepared search projection has no verified assessment")
        assessment = json.loads(verified[0])
        assessment_status = "verified"
    else:
        stored = db.execute(
            "SELECT document_json FROM central_documents WHERE collection='news_assessment' "
            "AND owner=? AND document_key=?", (stock, article_id),
        ).fetchone()
        assessment = json.loads(stored[0]) if stored else None
        assessment_status = "seed_stored" if stored else "unassessed"
    missing, sort_us = _publication(article[1])
    display = {"title": str(document.get("title") or ""),
               "description": str(document.get("description") or ""),
               "link": str(document.get("link") or ""),
               "original_link": str(document.get("original_link") or ""),
               "published_at": article[1], "body_text": clean,
               "body_status": body_status, "assessment": assessment,
               "assessment_status": assessment_status}
    return (stock, identity, article_id, body_id, event_id, event_revision_id,
            membership_id, source_kind, body_status, assessment_status,
            missing, sort_us, _json(display))


def _lease(db: sqlite3.Connection, owner: str | None) -> str | None:
    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute(
            "CREATE TABLE IF NOT EXISTS archive_search_projection_lease ("
            "lease_id INTEGER PRIMARY KEY CHECK(lease_id=1),owner TEXT NOT NULL,"
            "expires_at REAL NOT NULL)"
        )
        if owner is None:
            token = uuid.uuid4().hex
            row = db.execute(
                "SELECT expires_at FROM archive_search_projection_lease WHERE lease_id=1"
            ).fetchone()
            if row and row[0] > time.time():
                raise ValueError("another search projection builder is active")
            db.execute(
                "INSERT INTO archive_search_projection_lease VALUES(1,?,?) "
                "ON CONFLICT(lease_id) DO UPDATE SET owner=excluded.owner,"
                "expires_at=excluded.expires_at",
                (token, time.time() + LEASE_SECONDS),
            )
        else:
            token = None
            db.execute("DELETE FROM archive_search_projection_lease "
                       "WHERE lease_id=1 AND owner=?", (owner,))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return token


def build(archive_path: Path, *, max_rows: int = 0) -> dict[str, Any]:
    if max_rows < 0:
        raise ValueError("max_rows must be non-negative")
    archive_path = archive_path.resolve(strict=True)
    with closing(sqlite3.connect(archive_path, timeout=30)) as db:
        db.execute("PRAGMA busy_timeout=30000")
        config = _preflight(db)
        manifest = _freeze_sources(db, config)
        if db.execute("SELECT COUNT(*) FROM archive_search_projection").fetchone()[0] != manifest["projected"]:
            raise ValueError("search projection row count differs from manifest")
        owner = _lease(db, None)
        try:
            processed = 0
            while manifest["state"] != "complete" and (not max_rows or processed < max_rows):
                take = min(500, max_rows - processed) if max_rows else 500
                last = manifest["last_key"]
                rows = db.execute(
                    "SELECT stock_code,identity,article_revision_id,body_revision_id,source_kind "
                    "FROM archive_search_projection_sources WHERE (stock_code,identity)>(?,?) "
                    "ORDER BY stock_code,identity LIMIT ?",
                    (last[0], last[1], take) if last else ("", "", take),
                ).fetchall()
                if not rows and manifest["projected"] != manifest["sources"]:
                    raise ValueError("search projection ended before all sources were processed")
                updated = ({**manifest, "state": "complete"} if not rows else
                           {**manifest, "last_key": list(rows[-1][:2]),
                            "projected": manifest["projected"] + len(rows)})
                if updated["projected"] == manifest["sources"]:
                    updated["state"] = "complete"
                db.execute("BEGIN IMMEDIATE")
                try:
                    if db.execute("SELECT owner FROM archive_search_projection_lease "
                                  "WHERE lease_id=1").fetchone() != (owner,):
                        raise ValueError("search projection builder lost its lease")
                    if rows:
                        values = [_row(db, row) for row in rows]
                        db.executemany(
                            "INSERT INTO archive_search_projection VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", values,
                        )
                    db.execute("UPDATE archive_build_manifest SET value_json=? WHERE key=?",
                               (_json(updated), PROJECTION_KEY))
                    db.execute("UPDATE archive_search_projection_lease SET expires_at=? "
                               "WHERE lease_id=1 AND owner=?",
                               (time.time() + LEASE_SECONDS, owner))
                    db.commit()
                except BaseException:
                    db.rollback()
                    raise
                manifest = updated
                processed += len(rows)
            return {"state": manifest["state"], "sources": manifest["sources"],
                    "projected": manifest["projected"], "processed_now": processed,
                    "archive_build_state": "building"}
        finally:
            _lease(db, owner)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--max-rows", type=int, default=0)
    args = parser.parse_args()
    print(_json(build(args.archive, max_rows=args.max_rows)))


if __name__ == "__main__":
    main()
