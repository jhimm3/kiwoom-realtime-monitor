"""Create a consistent read-only copy of the running PC news-preparation ledger."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path,
                        default=Path("data/historical_collection/prepared_news.sqlite3"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    output = args.output.resolve()
    if source == output:
        parser.error("output must differ from source")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".partial")
    if temporary.exists() or output.exists():
        parser.error("output or partial file already exists")
    with closing(sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)) as origin, \
         closing(sqlite3.connect(temporary)) as target:
        origin.backup(target, pages=1000)
        status = target.execute("PRAGMA integrity_check").fetchone()[0]
        counts = dict(target.execute("SELECT state,COUNT(*) FROM prepared_news GROUP BY state"))
    if status != "ok":
        raise RuntimeError(f"snapshot integrity check failed: {status}")
    temporary.replace(output)
    with output.open("rb") as snapshot:
        digest = hashlib.file_digest(snapshot, "sha256").hexdigest()
    print(json.dumps({"output": str(output), "sha256": digest, "rows": counts}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
