from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from scripts.export_news_classification_corpus import NewsCorpusExporter
from kiwoom_monitor.central_server.database import SQLiteQueryStore


class _Exporter(NewsCorpusExporter):
    def __init__(self, pages):
        super().__init__("http://nas", "token", page_size=2)
        self.pages = list(pages)
        self.parameters = []

    def get_json(self, path, parameters=None):
        self.parameters.append((path, parameters))
        return self.pages.pop(0)


class NewsCorpusExporterTests(unittest.TestCase):
    def test_history_retains_more_than_one_page_at_the_same_available_time(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "central.sqlite3"
            store = SQLiteQueryStore(path)
            store.initialize()
            try:
                store.upsert_documents("news_article", [
                    {"owner": "005930", "key": f"article-{index}", "document": {
                        "identity": f"article-{index}", "title": f"title {index}",
                        "published_at": "2026-10-18T00:00:00+09:00",
                    }} for index in range(1005)
                ])
                with closing(sqlite3.connect(path)) as connection, connection:
                    connection.execute("UPDATE central_news_article_revisions SET available_at=10.0")

                class StoredExporter(NewsCorpusExporter):
                    def get_json(self, route, parameters=None):
                        kwargs = {"limit": parameters["limit"]}
                        if "as_of" in parameters:
                            kwargs["available_at"] = parameters["as_of"]
                        if "before_revision_id" in parameters:
                            kwargs["before_revision_id"] = parameters["before_revision_id"]
                        return {"revisions": store.load_news_history(route.rsplit("/", 1)[-1], **kwargs)}

                values = StoredExporter("http://unused", "fixture", page_size=1000).history("article")
                self.assertEqual(1005, len(values))
                self.assertEqual(1005, len({value["article_revision_id"] for value in values}))
                self.assertEqual({f"article-{index}" for index in range(1005)}, {value["identity"] for value in values})
                # Accepted order can differ from available_at order after an
                # old observation is imported. Paging must still retain all.
                with closing(sqlite3.connect(path)) as connection, connection:
                    connection.execute(
                        "UPDATE central_news_article_revisions SET available_at=1.0 "
                        "WHERE accepted_sequence=(SELECT MAX(accepted_sequence) FROM central_news_article_revisions)"
                    )
                reordered = StoredExporter("http://unused", "fixture", page_size=1000).history("article")
                self.assertEqual(
                    {value["article_revision_id"] for value in values},
                    {value["article_revision_id"] for value in reordered},
                )
            finally:
                store.close()

    def test_history_pages_backwards_without_duplicate_revision(self) -> None:
        exporter = _Exporter([
            {"revisions": [
                {"article_revision_id": "a3", "available_at": 3.0},
                {"article_revision_id": "a2", "available_at": 2.0},
            ]},
            {"revisions": [
                {"article_revision_id": "a1", "available_at": 1.0},
            ]},
        ])

        actual = exporter.history("article")

        self.assertEqual(["a3", "a2", "a1"], [row["article_revision_id"] for row in actual])
        self.assertEqual("a2", exporter.parameters[1][1]["before_revision_id"])

    def test_content_uses_offset_until_short_page(self) -> None:
        exporter = _Exporter([
            {"documents": [{"owner": "A", "key": str(index)} for index in range(10_000)]},
            {"documents": [{"owner": "A", "key": "last"}]},
        ])

        actual = exporter.content("news_article")

        self.assertEqual(10_001, len(actual))
        self.assertEqual(10_000, exporter.parameters[1][1]["offset"])


if __name__ == "__main__":
    unittest.main()
