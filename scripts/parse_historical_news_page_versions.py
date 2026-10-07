"""Parse selected historical source-page versions on the PC, resumably.

Only fixed rowids from the input key manifest are read.  Raw page payloads stay
in the PC source database; the output records parsed versions and any errors.
"""

from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from kiwoom_monitor.infrastructure.historical_backfill import parse_naver_historical_search_page
from scripts.select_historical_news_page_keys import _request_fields


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _initialize(raw: sqlite3.Connection, key_path: Path, output: sqlite3.Connection,
                manifest: dict[str, Any], key_hash: str,
                prepared_hash: str) -> None:
    output.execute("CREATE TABLE page_versions ("
                   "source_rowid INTEGER PRIMARY KEY,request_key TEXT NOT NULL,"
                   "observed_at TEXT NOT NULL,response_sha256 TEXT NOT NULL,"
                   "declared_item_count INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'pending',"
                   "parsed_items INTEGER NOT NULL DEFAULT 0,prepared_matches INTEGER NOT NULL DEFAULT 0,"
                   "error TEXT NOT NULL DEFAULT '')")
    output.execute("CREATE TABLE page_article_observations ("
                   "source_rowid INTEGER NOT NULL,article_key TEXT NOT NULL,"
                   "stock_code TEXT NOT NULL,identity TEXT NOT NULL,"
                   "observed_at TEXT NOT NULL,position INTEGER NOT NULL,"
                   "item_hash TEXT NOT NULL,item_json TEXT NOT NULL,prepared_match INTEGER NOT NULL,"
                   "PRIMARY KEY(source_rowid,article_key))")
    output.execute("CREATE INDEX page_article_identity ON "
                   "page_article_observations(stock_code,identity,observed_at)")
    output.execute("CREATE TABLE page_parse_manifest (key TEXT PRIMARY KEY,value_json TEXT NOT NULL)")
    raw.execute("ATTACH DATABASE ? AS selected", (f"file:{key_path.as_posix()}?mode=ro",))
    count = 0
    reader = raw.execute(
        "SELECT p.rowid,p.request_key,p.observed_at,p.response_sha256,p.item_count "
        "FROM selected.selected_request_keys k JOIN source_pages p "
        "ON p.provider='naver_historical_search' AND p.request_key=k.request_key "
        "WHERE p.rowid<=?", (int(manifest["source_pages_max_rowid"]),),
    )
    while batch := reader.fetchmany(500):
        output.executemany(
            "INSERT INTO page_versions(source_rowid,request_key,observed_at,"
            "response_sha256,declared_item_count) VALUES(?,?,?,?,?)", batch)
        count += len(batch)
    if count != manifest["page_versions"]:
        raise ValueError("selected page count changed since the key manifest")
    output.execute("INSERT INTO page_parse_manifest VALUES('input',?)",
                   (json.dumps({"schema": "historical-news-page-parse/v1",
                                "page_key_sha256": key_hash,
                                "prepared_sha256": prepared_hash,
                                "raw_path": manifest["raw_path"],
                                "prepared_path": manifest["prepared_path"],
                                "source_pages_max_rowid": manifest["source_pages_max_rowid"],
                                "page_versions": count}, sort_keys=True),))


def parse_pages(raw_path: Path, prepared_path: Path, key_path: Path,
                output_path: Path, *, max_pages: int = 0) -> dict[str, Any]:
    if max_pages < 0:
        raise ValueError("max_pages must be non-negative")
    raw_path = raw_path.resolve(strict=True)
    prepared_path = prepared_path.resolve(strict=True)
    key_path = key_path.resolve(strict=True)
    output_path = output_path.resolve()
    if not output_path.parent.is_dir():
        raise FileNotFoundError("page-parse output parent does not exist")
    with closing(sqlite3.connect(f"file:{key_path.as_posix()}?mode=ro&immutable=1", uri=True)) as key_db:
        manifest_row = key_db.execute("SELECT value_json FROM input_manifest WHERE key='report'").fetchone()
        if manifest_row is None:
            raise ValueError("page-key manifest is missing")
        key_manifest = json.loads(manifest_row[0])
    if key_manifest.get("schema") != "historical-news-page-keys/v2":
        raise ValueError("unsupported page-key manifest")
    if str(raw_path) != key_manifest["raw_path"] or str(prepared_path) != key_manifest["prepared_path"]:
        raise ValueError("page-key inputs do not match")
    key_hash = _sha256(key_path)
    prepared_hash = _sha256(prepared_path)
    started = time.monotonic()
    with closing(sqlite3.connect(f"file:{prepared_path.as_posix()}?mode=ro&immutable=1", uri=True)) as prepared:
        wanted = {(str(code), str(identity)) for code, identity in prepared.execute(
            "SELECT stock_code,identity FROM prepared_news WHERE scope='historical_backfill'")}
    with closing(sqlite3.connect(f"file:{raw_path.as_posix()}?mode=ro", uri=True)) as raw:
        with closing(sqlite3.connect(output_path)) as output:
            exists = output.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                    "AND name='page_parse_manifest'").fetchone()
            if not exists:
                if output.execute("SELECT 1 FROM sqlite_master WHERE type='table'").fetchone():
                    raise FileExistsError("page-parse output exists with an unrelated schema")
                output.execute("BEGIN IMMEDIATE")
                try:
                    _initialize(raw, key_path, output, key_manifest, key_hash,
                                prepared_hash)
                except BaseException:
                    output.rollback()
                    raise
                output.commit()
            else:
                saved_row = output.execute(
                    "SELECT value_json FROM page_parse_manifest WHERE key='input'").fetchone()
                if saved_row is None:
                    raise ValueError("page-parse input manifest is missing")
                saved = json.loads(saved_row[0])
                if (saved.get("page_key_sha256") != key_hash or
                    saved.get("prepared_sha256") != prepared_hash or
                    saved.get("raw_path") != str(raw_path) or
                    saved.get("prepared_path") != str(prepared_path) or
                    saved.get("source_pages_max_rowid") !=
                        key_manifest["source_pages_max_rowid"] or
                    saved.get("page_versions") != key_manifest["page_versions"]):
                    raise ValueError("page-parse input changed since parsing began")
            processed_now = errors_now = 0
            while max_pages == 0 or processed_now < max_pages:
                take = min(25, max_pages - processed_now) if max_pages else 25
                pages = output.execute(
                    "SELECT source_rowid,request_key,observed_at,response_sha256,declared_item_count "
                    "FROM page_versions WHERE status='pending' ORDER BY source_rowid LIMIT ?",
                    (take,),
                ).fetchall()
                if not pages:
                    break
                output.execute("BEGIN IMMEDIATE")
                for rowid, request_key, observed_at, expected_hash, declared_count in pages:
                    try:
                        raw_row = raw.execute("SELECT payload_json FROM source_pages WHERE rowid=?",
                                              (rowid,)).fetchone()
                        if raw_row is None:
                            raise ValueError("source page row was removed")
                        payload_json = str(raw_row[0])
                        actual_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
                        if actual_hash != expected_hash:
                            raise ValueError("source page payload hash changed")
                        code, first, last, query, start = _request_fields(request_key)
                        page = parse_naver_historical_search_page(
                            code, query, first, start, json.loads(payload_json),
                            target_end_date=last, observed_at=datetime.fromisoformat(observed_at),
                        )
                        if len(page.items) != declared_count:
                            raise ValueError("parsed item count differs from source record")
                        matches = 0
                        for item in page.items:
                            identity = (item.original_url or item.portal_url or
                                        f"naver:{item.office_id}:{item.article_id}")
                            selected = (code, identity) in wanted
                            matches += int(selected)
                            item_json = json.dumps(asdict(item), ensure_ascii=False,
                                                   sort_keys=True, separators=(",", ":"))
                            output.execute(
                                "INSERT INTO page_article_observations VALUES(?,?,?,?,?,?,?,?,?)",
                                (rowid, item.article_key, code, identity, observed_at,
                                 item.position, hashlib.sha256(item_json.encode("utf-8")).hexdigest(),
                                 item_json, int(selected)),
                            )
                        output.execute("UPDATE page_versions SET status='parsed',"
                                       "parsed_items=?,prepared_matches=? WHERE source_rowid=?",
                                       (len(page.items), matches, rowid))
                    except Exception as error:
                        # A bad page must stay visible; no article rows from it
                        # may survive its failed transaction unit.
                        output.execute("DELETE FROM page_article_observations WHERE source_rowid=?", (rowid,))
                        output.execute("UPDATE page_versions SET status='error',error=? "
                                       "WHERE source_rowid=?",
                                       (f"{type(error).__name__}: {error}"[:1000], rowid))
                        errors_now += 1
                    processed_now += 1
                output.commit()
            states = dict(output.execute("SELECT status,COUNT(*) FROM page_versions GROUP BY status"))
            observations = output.execute("SELECT COUNT(*),COALESCE(SUM(prepared_match),0) "
                                          "FROM page_article_observations").fetchone()
            return {"processed_now": processed_now, "errors_now": errors_now,
                    "states": states, "article_observations": observations[0],
                    "prepared_matches": observations[1],
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                    "build_state": "processing" if states.get("pending", 0) else
                    ("incomplete_errors" if states.get("error", 0) else "parsed_unverified")}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", required=True, type=Path)
    parser.add_argument("--prepared", required=True, type=Path)
    parser.add_argument("--keys", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--max-pages", type=int, default=0)
    args = parser.parse_args()
    print(json.dumps(parse_pages(args.raw, args.prepared, args.keys,
                                 args.output, max_pages=args.max_pages),
                     ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
