"""PySide에 의존하지 않는 KRX 상장종목 카탈로그 조회·파싱."""

from __future__ import annotations

from html.parser import HTMLParser
from urllib.request import Request, urlopen


KRX_STOCK_CATALOG_URL = (
    "https://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13"
)


class _KrxTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append("".join(self._cell).strip())
            self._cell = None
        elif tag == "tr" and self._row:
            self.rows.append(self._row)
            self._row = None


def normalize_krx_market(value: str) -> str:
    """KRX 표기 변형을 TOP20 지수가 사용하는 고정 시장명으로 바꾼다."""
    normalized = value.strip().upper()
    if "코스닥" in normalized or "KOSDAQ" in normalized or normalized == "KSQ":
        return "KOSDAQ"
    if normalized == "유가" or "유가증권" in normalized or "코스피" in normalized or "KOSPI" in normalized:
        return "KOSPI"
    if "코넥스" in normalized or "KONEX" in normalized:
        return "KONEX"
    return value.strip()


def parse_krx_stock_catalog(document: str) -> tuple[tuple[str, str, str], ...]:
    parser = _KrxTableParser()
    parser.feed(document)
    header = next(
        (row for row in parser.rows if "회사명" in row and "종목코드" in row), None,
    )
    if header is None:
        raise ValueError("KRX 상장종목 목록 형식을 확인할 수 없습니다.")
    name_at, code_at = header.index("회사명"), header.index("종목코드")
    market_at = header.index("시장구분") if "시장구분" in header else None
    rows: list[tuple[str, str, str]] = []
    for row in parser.rows[parser.rows.index(header) + 1 :]:
        if len(row) <= max(name_at, code_at):
            continue
        code = row[code_at].strip().upper()
        if code.isdigit():
            code = code.zfill(6)
        name = row[name_at].strip()
        market = (
            normalize_krx_market(row[market_at])
            if market_at is not None and len(row) > market_at else ""
        )
        if len(code) == 6 and code.isalnum() and name:
            rows.append((code, name, market))
    if not rows:
        raise ValueError("KRX 상장종목 목록에 종목이 없습니다.")
    return tuple(rows)


def fetch_krx_stock_catalog(*, timeout: float = 30.0) -> tuple[tuple[str, str, str], ...]:
    request = Request(KRX_STOCK_CATALOG_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=timeout) as response:
        document = response.read().decode("euc-kr", errors="replace")
    return parse_krx_stock_catalog(document)
