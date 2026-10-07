from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from kiwoom_monitor.infrastructure.central_content_client import CentralContentClient


def normalize_batch_size(value: int) -> int:
    return max(1, min(int(value), 1000))


def upload_documents(
    client: CentralContentClient,
    collection: str,
    documents: Sequence[dict[str, Any]],
    batch_size: int,
) -> int:
    saved = 0
    size = normalize_batch_size(batch_size)
    for offset in range(0, len(documents), size):
        saved += client.upsert(collection, list(documents[offset:offset + size]))
    return saved


def central_document(owner: object, key: object, document: Mapping[str, Any]) -> dict[str, Any]:
    return {"owner": str(owner), "key": str(key), "document": dict(document)}


def table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0]) for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
