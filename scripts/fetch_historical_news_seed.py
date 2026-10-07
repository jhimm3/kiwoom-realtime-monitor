"""Copy the one-off NAS news seed to this PC and verify its SHA-256."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess


REMOTE = (
    "/volume1/docker/kiwoom-monitor/deploy/synology/server-data/maintenance/"
    "historical-news-seed-20260927.sqlite3"
)
EXPECTED_SHA256 = "55f7ffc2c9533ae3abb3be2d19cba7245e2731a2958e58a15f32c64cde3981ba"
DESTINATION = Path(__file__).resolve().parents[1] / "data/historical_collection/nas-news-seed-20260927.sqlite3"


def main() -> None:
    partial = DESTINATION.with_name(DESTINATION.name + ".partial")
    if DESTINATION.exists() or partial.exists():
        raise FileExistsError("destination or partial transfer already exists")
    digest = hashlib.sha256()
    copied = 0
    process = subprocess.Popen(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
         "k379@192.168.0.5", f"cat {REMOTE}"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert process.stdout is not None
    assert process.stderr is not None
    try:
        with partial.open("xb") as output:
            while block := process.stdout.read(1024 * 1024):
                output.write(block)
                digest.update(block)
                copied += len(block)
                if copied // (100 * 1024 * 1024) != (copied - len(block)) // (100 * 1024 * 1024):
                    print(f"copied_mib={copied // (1024 * 1024)}", flush=True)
            output.flush()
            os.fsync(output.fileno())
        error = process.stderr.read().decode("utf-8", errors="replace")
        code = process.wait()
        if code != 0:
            raise RuntimeError(f"SSH copy exited {code}: {error[:500]}")
        actual = digest.hexdigest()
        if actual != EXPECTED_SHA256:
            raise ValueError(f"seed transfer SHA-256 mismatch: {actual}")
        partial.replace(DESTINATION)
        print(f"verified_bytes={copied} sha256={actual} path={DESTINATION}")
    except BaseException:
        process.kill()
        process.wait()
        raise


if __name__ == "__main__":
    main()
