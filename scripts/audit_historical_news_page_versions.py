"""Read-only coverage audit for a frozen PC historical page-parse input."""

from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def audit(parsed_path: Path, prepared_path: Path, key_path: Path,
          *, deep: bool = False) -> dict[str, object]:
    parsed_path = parsed_path.resolve(strict=True)
    prepared_path = prepared_path.resolve(strict=True)
    key_path = key_path.resolve(strict=True)
    with closing(sqlite3.connect(f"file:{parsed_path.as_posix()}?mode=ro", uri=True)) as db:
        if db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ValueError("parsed page database integrity check failed")
        row = db.execute("SELECT value_json FROM page_parse_manifest WHERE key='input'").fetchone()
        if row is None:
            raise ValueError("page-parse input manifest is missing")
        source = json.loads(row[0])
        if source.get("schema") != "historical-news-page-parse/v1":
            raise ValueError("unsupported page-parse schema")
        if (source.get("prepared_path") != str(prepared_path) or
            source.get("prepared_sha256") != _sha256(prepared_path) or
            source.get("page_key_sha256") != _sha256(key_path)):
            raise ValueError("page-parse inputs changed")
        states = dict(db.execute("SELECT status,COUNT(*) FROM page_versions GROUP BY status"))
        if sum(states.values()) != source["page_versions"]:
            raise ValueError("page-parse state count does not match manifest")
        db.execute("ATTACH DATABASE ? AS prepared", (f"file:{prepared_path.as_posix()}?mode=ro",))
        coverage = [
            {"state": str(state), "prepared_rows": int(count), "without_page_observation":
             int(db.execute(
                 "SELECT COUNT(*) FROM prepared.prepared_news p "
                 "WHERE p.scope='historical_backfill' AND p.state=? AND NOT EXISTS ("
                 "SELECT 1 FROM page_article_observations o "
                 "WHERE o.stock_code=p.stock_code AND o.identity=p.identity)",
                 (state,),
             ).fetchone()[0])}
            for state, count in db.execute(
                "SELECT state,COUNT(*) FROM prepared.prepared_news "
                "WHERE scope='historical_backfill' GROUP BY state")
        ]
        observed, prepared_matches = db.execute(
            "SELECT COUNT(*),COALESCE(SUM(prepared_match),0) "
            "FROM page_article_observations").fetchone()
        errors = db.execute(
            "SELECT source_rowid,error FROM page_versions WHERE status='error' "
            "ORDER BY source_rowid LIMIT 20").fetchall()
        report: dict[str, object] = {"page_versions": source["page_versions"], "states": states,
                "article_observations": int(observed),
                "prepared_match_observations": int(prepared_matches),
                "prepared_coverage": coverage,
                "page_errors_first_20": [{"source_rowid": int(rowid), "error": str(error)}
                                         for rowid, error in errors],
                "coverage_state": "incomplete" if states.get("pending") or states.get("error")
                else "parsed_unverified"}
        if deep:
            last_key: tuple[str, str] | None = None
            first_content = ""
            changed_identities = distinct_identities = 0
            already_changed = False
            for code, identity, content in db.execute(
                "SELECT stock_code,identity,json_remove(item_json,'$.position') "
                "FROM page_article_observations ORDER BY stock_code,identity,observed_at"):
                key = (str(code), str(identity))
                if key != last_key:
                    distinct_identities += 1
                    last_key = key
                    first_content = str(content)
                    already_changed = False
                elif not already_changed and str(content) != first_content:
                    changed_identities += 1
                    already_changed = True
            last_request = ""
            first_response = ""
            changed_requests = 0
            request_changed = False
            for request_key, response_hash in db.execute(
                "SELECT request_key,response_sha256 FROM page_versions "
                "ORDER BY request_key,observed_at"):
                if request_key != last_request:
                    last_request = str(request_key)
                    first_response = str(response_hash)
                    request_changed = False
                elif not request_changed and response_hash != first_response:
                    changed_requests += 1
                    request_changed = True
            report["variants"] = {
                "distinct_stock_article_identities": distinct_identities,
                "identities_with_changed_page_item_excluding_position": changed_identities,
                "request_keys_with_changed_response_hash": changed_requests,
                "note": "page response changes are not article revisions; item changes are candidates, not validated stored revisions",
            }
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parsed", required=True, type=Path)
    parser.add_argument("--prepared", required=True, type=Path)
    parser.add_argument("--keys", required=True, type=Path)
    parser.add_argument("--deep", action="store_true")
    args = parser.parse_args()
    print(json.dumps(audit(args.parsed, args.prepared, args.keys,
                           deep=args.deep),
                     ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
