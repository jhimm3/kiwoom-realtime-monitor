"""Audit a frozen prepared search snapshot against PC raw BODY evidence.

This reads both SQLite files without changing them.  Raw BODY snapshots are
bounded by their rowid at the start of the audit, so later collector inserts
cannot silently enter the reported population.
"""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime
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


def audit(prepared_path: Path, raw_path: Path, *, limit: int = 0) -> dict[str, object]:
    if limit < 0:
        raise ValueError("limit must be non-negative")
    prepared_path = prepared_path.resolve(strict=True)
    raw_path = raw_path.resolve(strict=True)
    if prepared_path == raw_path:
        raise ValueError("prepared and raw databases must differ")
    prepared_hash = _sha256(prepared_path)
    with closing(sqlite3.connect(f"file:{raw_path.as_posix()}?mode=ro", uri=True)) as raw:
        cutoff = int(raw.execute(
            "SELECT COALESCE(MAX(rowid),0) FROM news_article_body_snapshots"
        ).fetchone()[0])
        evidence: dict[tuple[str, str, str], list[tuple[str, str, float | None]]] = {}
        for provider, office_id, article_id, version, body_hash, fetched_at in raw.execute(
            "SELECT provider,office_id,article_id,extractor_version,body_sha256,fetched_at "
            "FROM news_article_body_snapshots WHERE rowid<=?", (cutoff,)
        ):
            key = (str(provider), str(office_id), str(article_id))
            try:
                raw_fetched_at = datetime.fromisoformat(str(fetched_at)).timestamp()
            except (TypeError, ValueError, OverflowError):
                raw_fetched_at = None
            evidence.setdefault(key, []).append((str(version), str(body_hash),
                                                  raw_fetched_at))

    counts: Counter[str] = Counter()
    versions: Counter[str] = Counter()
    with closing(sqlite3.connect(
        f"file:{prepared_path.as_posix()}?mode=ro", uri=True
    )) as prepared:
        total = int(prepared.execute(
            "SELECT COUNT(*) FROM prepared_news WHERE scope='historical_backfill' "
            "AND state='ready'"
        ).fetchone()[0])
        query = ("SELECT article_json,body_json FROM prepared_news "
                 "WHERE scope='historical_backfill' AND state='ready' "
                 "ORDER BY stock_code,identity")
        if limit:
            query += " LIMIT ?"
        with closing(prepared.execute(query, (limit,) if limit else ())) as rows:
            for article_json, body_json in rows:
                article = json.loads(article_json)
                body = json.loads(body_json)
                status = str(body.get("body_status") or "missing_status")
                counts[f"body_status:{status}"] += 1
                if status != "fulltext":
                    continue
                source = article.get("document", {}).get("historical_source")
                if not isinstance(source, dict):
                    counts["fulltext:no_source_key"] += 1
                    continue
                key = tuple(str(source.get(field) or "") for field in
                            ("provider", "office_id", "article_id"))
                if not all(key):
                    counts["fulltext:no_source_key"] += 1
                    continue
                body_text = body.get("body_text")
                if not isinstance(body_text, str) or not body_text:
                    counts["fulltext:empty_body"] += 1
                    continue
                body_hash = hashlib.sha256(body_text.encode("utf-8")).hexdigest()
                snapshots = evidence.get(key)
                if not snapshots:
                    counts["fulltext:no_raw_snapshot"] += 1
                    continue
                matching = {(version, fetched) for version, digest, fetched in snapshots
                            if digest == body_hash}
                if matching:
                    counts["fulltext:raw_hash_match"] += 1
                    prepared_fetched_at = float(body.get("fetched_at") or 0)
                    exact = {version for version, fetched in matching
                             if fetched is not None and
                             abs(fetched - prepared_fetched_at) <= 0.001}
                    counts["fulltext:raw_hash_and_fetch_time_match" if exact else
                           "fulltext:raw_hash_only"] += 1
                    for version in exact:
                        versions[version] += 1
                else:
                    counts["fulltext:raw_hash_mismatch"] += 1

    inspected = sum(value for key, value in counts.items() if key.startswith("body_status:"))
    return {
        "schema": "prepared-historical-body-provenance-audit/v1",
        "prepared_path": str(prepared_path),
        "prepared_sha256": prepared_hash,
        "raw_path": str(raw_path),
        "raw_snapshot_rowid_cutoff": cutoff,
        "ready_rows_total": total,
        "ready_rows_inspected": inspected,
        "limited": bool(limit),
        "counts": dict(sorted(counts.items())),
        "matched_extractor_versions": dict(sorted(versions.items())),
        "scope_note": "Matching hash and fetch time support prepared-body extractor provenance; a hash-only match proves byte identity but not how the prepared body was obtained. An absent raw snapshot does not prove the prepared body is wrong.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", required=True, type=Path)
    parser.add_argument("--raw", required=True, type=Path)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    print(json.dumps(audit(args.prepared, args.raw, limit=args.limit),
                     ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
