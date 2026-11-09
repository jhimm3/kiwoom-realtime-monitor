"""Merge PC-observed KIND evidence into a new frozen strategy reference file."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from kiwoom_monitor.application.market_session_schedule import decode_strategy_references
from kiwoom_monitor.infrastructure.persistence.backup_file import write_immutable_bytes


def prepare_kind_listing_references(database: Path, references: Path, output: Path, cutoff: datetime) -> str:
    if cutoff.utcoffset() is None:
        raise ValueError("listing reference cutoff must include a timezone")
    document = json.loads(references.read_bytes())
    decode_strategy_references(document)
    listings = {row["evidence_id"]: row for row in document["listings"]}
    official = []
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        stored = connection.execute(
            "SELECT document_json FROM central_documents WHERE collection=? AND owner=? ORDER BY document_key",
            ("listing_evidence", "kind"),
        )
        for (raw,) in stored:
            item = json.loads(raw)
            row = item["evidence"]
            available = row["available_at"]
            if datetime.fromisoformat(item["observed_at"]) > cutoff or (
                    available is not None and datetime.fromisoformat(available) > cutoff):
                continue
            official.append(row)
    publications: dict[str, dict[str, datetime]] = {}
    recovered = set()
    for row in official:
        if row["available_at"] is not None and "/kind-type:" not in row["revision"]:
            publications.setdefault(row["symbol"], {})[row["revision"]] = datetime.fromisoformat(row["available_at"])
            if row["listing_date"] is not None:
                recovered.add((row["symbol"], row["revision"]))
    for row in official:
        if ((row["symbol"], row["revision"]) in recovered
                and (row["available_at"] is None or row["listing_date"] is None)):
            continue
        receipt, marker, _ = row["revision"].partition("/kind-type:")
        if marker:
            sequence = publications.get(row["symbol"], {})
            published = sequence.get(receipt)
            classified_at = datetime.fromisoformat(row["available_at"])
            if published is None or any(published < moment <= classified_at for moment in sequence.values()):
                continue
        previous = listings.setdefault(row["evidence_id"], row)
        if previous != row:
            raise ValueError("conflicting listing evidence for the same ID")
    document["listings"] = [listings[key] for key in sorted(listings)]
    decode_strategy_references(document)
    raw = (json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    write_immutable_bytes(output, raw)
    return hashlib.sha256(raw).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--references", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--as-of", type=datetime.fromisoformat, required=True)
    args = parser.parse_args()
    print(prepare_kind_listing_references(args.database, args.references, args.output, args.as_of))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
