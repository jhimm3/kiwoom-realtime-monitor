"""The readonly SQLite snapshot boundary shared by one-off preservation tools."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from time import perf_counter


class SQLiteSnapshotContractError(ValueError):
    """The caller's declared live/frozen source cannot be read safely."""


@contextmanager
def readonly_sqlite_snapshot(path: Path, *, frozen: bool = False, metrics: dict | None = None):
    """Honor live WAL, or explicitly frozen no-sidecar state; never initialize."""
    metrics = metrics if metrics is not None else {}
    started = perf_counter()
    source = path.resolve(strict=True)
    sidecars = [Path(str(source) + suffix) for suffix in ("-wal", "-shm", "-journal")]
    before = source.stat()
    with source.open("rb") as stream:
        header = stream.read(100)
    if frozen:
        if any(item.exists() for item in sidecars):
            raise SQLiteSnapshotContractError("frozen source must have no WAL/SHM/journal; never ignore live WAL")
        uri = source.as_uri() + "?mode=ro&immutable=1"
    else:
        if header[18:20] == b"\x02\x02" and not all(item.is_file() for item in sidecars[:2]):
            raise SQLiteSnapshotContractError("live WAL source requires its existing WAL and SHM")
        uri = source.as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    metrics["connections"] = 1
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        metrics["begin"] = 1
        metrics["connection_ms"] = round((perf_counter() - started) * 1000, 3)
        yield connection
    finally:
        try:
            connection.rollback()
            metrics["rollback"] = 1
        finally:
            connection.close()
        if frozen:
            after = source.stat()
            if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) !=
                    (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                    or any(item.exists() for item in sidecars)):
                raise SQLiteSnapshotContractError("declared frozen source changed during its read")
