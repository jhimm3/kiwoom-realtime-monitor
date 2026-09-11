from __future__ import annotations

import sqlite3
import unittest

from kiwoom_monitor.infrastructure.central_sync_utils import (
    central_document,
    normalize_batch_size,
    table_names,
    upload_documents,
)


class _Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[dict[str, object]]]] = []

    def upsert(self, collection: str, values: list[dict[str, object]]) -> int:
        self.calls.append((collection, values))
        return len(values)


class CentralSyncUtilsTests(unittest.TestCase):
    def test_upload_splits_documents_without_changing_order(self) -> None:
        client = _Client()
        values = [central_document("owner", index, {"value": index}) for index in range(5)]

        saved = upload_documents(client, "sample", values, 2)  # type: ignore[arg-type]

        self.assertEqual(5, saved)
        self.assertEqual([2, 2, 1], [len(batch) for _name, batch in client.calls])
        self.assertEqual(["0", "1", "2", "3", "4"], [
            value["key"] for _name, batch in client.calls for value in batch
        ])

    def test_batch_size_and_table_inventory_are_bounded(self) -> None:
        self.assertEqual(1, normalize_batch_size(0))
        self.assertEqual(1000, normalize_batch_size(5000))
        connection = sqlite3.connect(":memory:")
        try:
            connection.execute("CREATE TABLE sample(id INTEGER)")
            self.assertIn("sample", table_names(connection))
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
