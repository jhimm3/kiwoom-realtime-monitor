"""One-off, resumable NAS import of verified PC-prepared news snapshots.

Run detached inside the server container. The underlying importer has an
identity/content ledger, so retrying after a container restart skips rows
already committed. This script never fetches article pages.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path("/app/data/maintenance")
IMPORTER = ROOT / "import_prepared_historical_news_to_nas.py"
STATUS = ROOT / "prepared-news-import-status.json"
LOG = ROOT / "prepared-news-import-20260925.log"
SNAPSHOTS = (
    ("search", ROOT / "prepared-news-batch-20260924-1427.sqlite3",
     "0cd2ac49ff1f176dad2ba1dcfb72d4b759c0d450bf3e71372f9be44d425f1141"),
    ("market", ROOT / "prepared-market-news-snapshot-20260924.sqlite3",
     "904fd54ce98384bb7f3b9f560354deb276f5a9d0a3a6b08b45643c776c267e2c"),
    ("search-current", ROOT / "prepared-news-20260925T2215.sqlite3",
     "b01f8680fce196a856095ae573970eb51b3e1190f010e7d664c06b08b078e765"),
)


def write_status(**fields: object) -> None:
    payload = {"schema": "prepared-news-import/v1",
               "updated_at": datetime.now(UTC).isoformat(), **fields}
    temporary = STATUS.with_name(STATUS.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(STATUS)


def main() -> int:
    if not IMPORTER.is_file():
        raise SystemExit("prepared-news importer is missing")
    with (ROOT / "prepared-news-import.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("prepared-news import is already running") from None
        started = time.time()
        with LOG.open("a", encoding="utf-8", buffering=1) as log:
            for name, snapshot, expected in SNAPSHOTS:
                if not snapshot.is_file():
                    write_status(state="failed", stage=name, error="snapshot missing")
                    return 1
                with snapshot.open("rb") as source:
                    actual = hashlib.file_digest(source, "sha256").hexdigest()
                if actual != expected:
                    write_status(state="failed", stage=name, error="SHA-256 mismatch")
                    return 1
                write_status(state="running", stage=name, snapshot=snapshot.name,
                             started_at=started, log=str(LOG))
                print(f"{datetime.now(UTC).isoformat()} stage={name} begin", file=log)
                command = [sys.executable, "-u", str(IMPORTER),
                           "--prepared", str(snapshot)]
                with subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT, text=True,
                                      encoding="utf-8", errors="replace", bufsize=1) as process:
                    assert process.stdout is not None
                    for line in process.stdout:
                        log.write(line)
                    result = process.wait()
                print(f"{datetime.now(UTC).isoformat()} stage={name} exit={result}", file=log)
                if result:
                    write_status(state="failed", stage=name, snapshot=snapshot.name,
                                 exit_code=result, log=str(LOG))
                    return result
        write_status(state="complete", stages=[name for name, _, _ in SNAPSHOTS],
                     started_at=started, elapsed_seconds=round(time.time() - started, 1),
                     log=str(LOG))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
