from __future__ import annotations
import re, zipfile
from pathlib import Path
from xml.etree import ElementTree

class ThemeRepository:
    def __init__(self, path: Path) -> None: self._path = path
    def load(self) -> dict[str, str]:
        return {self._key(name): themes.strip() for name, themes in self.load_rows() if self._key(name) and themes.strip()}

    def load_rows(self) -> tuple[tuple[str, str], ...]:
        return self.load_header_and_rows()[1]

    def load_header_and_rows(self) -> tuple[tuple[str, str], tuple[tuple[str, str], ...]]:
        if not self._path.exists(): return (("", ""), ())
        with zipfile.ZipFile(self._path) as book:
            shared = self._shared_strings(book)
            root = ElementTree.fromstring(book.read("xl/worksheets/sheet1.xml"))
        rows = {}
        for cell in root.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c"):
            ref = cell.attrib.get("r", ""); value = cell.findtext("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v", "")
            if cell.attrib.get("t") == "s" and value: value = shared[int(value)]
            elif cell.attrib.get("t") == "inlineStr": value = "".join(cell.itertext())
            row = re.sub(r"\D", "", ref); col = re.sub(r"\d", "", ref)
            rows.setdefault(row, {})[col] = value
        values = tuple(
            (row.get("A", ""), row.get("B", ""))
            for number, row in sorted(rows.items(), key=lambda item: int(item[0] or 0))
        )
        return (values[0] if values else ("", ""), values[1:])
    @staticmethod
    def _key(name: str) -> str: return re.sub(r"\s+", "", name).strip()
    @staticmethod
    def _shared_strings(book: zipfile.ZipFile) -> list[str]:
        try: root = ElementTree.fromstring(book.read("xl/sharedStrings.xml"))
        except KeyError: return []
        ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        return ["".join(item.itertext()) for item in root.iter(f"{ns}si")]
