from __future__ import annotations

from datetime import datetime
from html.parser import HTMLParser
from urllib.request import Request, urlopen

from PySide6.QtCore import QThread, Signal


class _KrxTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(); self.rows: list[list[str]] = []; self._row: list[str] | None = None; self._cell: list[str] | None = None
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr": self._row = []
        elif tag in {"td", "th"} and self._row is not None: self._cell = []
    def handle_data(self, data: str) -> None:
        if self._cell is not None: self._cell.append(data)
    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append("".join(self._cell).strip()); self._cell = None
        elif tag == "tr" and self._row:
            self.rows.append(self._row); self._row = None


class KrxStockCatalogWorker(QThread):
    completed = Signal(int, bool)
    failed = Signal(str)
    history_failed = Signal(str)
    _URL = "https://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13"

    def __init__(self, stocks: object, settings: object) -> None:
        super().__init__(); self._stocks = stocks; self._settings = settings

    def run(self) -> None:
        now = datetime.now()
        try:
            request = Request(self._URL, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(request, timeout=30) as response:
                document = response.read().decode("euc-kr", errors="replace")
            if self.isInterruptionRequested():
                return
            parser = _KrxTableParser(); parser.feed(document)
            header = next((row for row in parser.rows if "회사명" in row and "종목코드" in row), None)
            if header is None: raise ValueError("KRX 상장종목 목록 형식을 확인할 수 없습니다.")
            name_at, code_at = header.index("회사명"), header.index("종목코드")
            market_at = header.index("시장구분") if "시장구분" in header else None
            rows = []
            for row in parser.rows[parser.rows.index(header) + 1:]:
                if self.isInterruptionRequested():
                    return
                if len(row) <= max(name_at, code_at): continue
                code = row[code_at].strip().upper()
                if code.isdigit():
                    code = code.zfill(6)
                name = row[name_at].strip()
                if len(code) == 6 and code.isalnum() and name:
                    rows.append((code, name, row[market_at].strip() if market_at is not None and len(row) > market_at else ""))
            if not rows: raise ValueError("KRX 상장종목 목록에 종목이 없습니다.")
            if self.isInterruptionRequested():
                return
            self._stocks.upsert_many(tuple(rows)); self._settings.set("krx_stock_catalog_date", now.strftime("%Y-%m-%d %H:%M:%S")); self._settings.set("krx_stock_catalog_format_version", "2")
            sync_history = getattr(self._stocks, "sync_kind_name_history", None)
            if callable(sync_history) and not self.isInterruptionRequested():
                try:
                    initial = not bool(self._settings.get("kind_name_history_sync_date"))
                    sync_history(initial=initial)
                    self._settings.set("kind_name_history_sync_date", now.strftime("%Y-%m-%d %H:%M:%S"))
                except Exception as error:
                    self.history_failed.emit(str(error))
            if not self.isInterruptionRequested():
                self.completed.emit(len(rows), False)
        except Exception as error:
            self.failed.emit(str(error))
