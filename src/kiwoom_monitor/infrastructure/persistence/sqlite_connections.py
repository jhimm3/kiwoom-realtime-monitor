"""SQLite 연결 종료와 쓰기 트랜잭션을 일관되게 보장한다."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def sqlite_read_connection(
    path: Path | str, *, timeout: float = 5.0, uri: bool = False
) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(path, timeout=timeout, uri=uri)
    try:
        yield connection
    finally:
        connection.close()


@contextmanager
def sqlite_transaction(
    path: Path | str, *, timeout: float = 5.0, uri: bool = False
) -> Iterator[sqlite3.Connection]:
    with sqlite_read_connection(path, timeout=timeout, uri=uri) as connection:
        with connection:
            yield connection
