from __future__ import annotations

import unittest

from scripts.export_news_classification_corpus import NewsCorpusExporter


class _Exporter(NewsCorpusExporter):
    def __init__(self, pages):
        super().__init__("http://nas", "token", page_size=2)
        self.pages = list(pages)
        self.parameters = []

    def get_json(self, path, parameters=None):
        self.parameters.append((path, parameters))
        return self.pages.pop(0)


class NewsCorpusExporterTests(unittest.TestCase):
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
        self.assertLess(exporter.parameters[1][1]["as_of"], 2.0)

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
