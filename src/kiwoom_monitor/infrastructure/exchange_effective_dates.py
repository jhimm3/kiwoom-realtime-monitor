"""Extract explicitly stated exchange trading dates from archived DART documents.

Receipt dates, proposed delistings and old periods in correction notices are never
promoted to effective trading dates.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from io import BytesIO

from kiwoom_monitor.domain.time_basis import KST, aware_iso_kst


_DATE = re.compile(r"(?<!\d)(?:(20\d{2})|(?:'?(\d{2})))\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})(?:일)?")
_TIME = re.compile(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?::([0-5]\d))?(?!\d)")
_DELIST = re.compile(r"상장폐지일\s*[:：]?\s*([^\s~]+)")


@dataclass(frozen=True)
class ExchangeEffectiveEvent:
    kind: str
    effective_date: str
    effective_time: str
    precision: str
    source_label: str


class _Rows(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def document_rows(document: str) -> list[list[str]]:
    parser = _Rows()
    parser.feed(document)
    return parser.rows


def archive_rows(archive_bytes: bytes) -> list[list[str]]:
    if len(archive_bytes) > 8_000_000 or not zipfile.is_zipfile(BytesIO(archive_bytes)):
        raise ValueError("DART original document is not a supported ZIP")
    result: list[list[str]] = []
    with zipfile.ZipFile(BytesIO(archive_bytes)) as archive:
        for member in archive.infolist():
            if not member.filename.lower().endswith((".xml", ".htm", ".html")):
                continue
            if member.file_size > 16_000_000:
                raise ValueError("DART original document is too large")
            raw = archive.read(member)
            try:
                decoded = raw.decode("utf-8")
            except UnicodeDecodeError:
                decoded = raw.decode("cp949")
            result.extend(document_rows(decoded))
    return result


def extract_effective_events(rows: list[list[str]], *, exchange_filing: bool) -> tuple[ExchangeEffectiveEvent, ...]:
    """Accept exact suspension/release fields; delisting only from exchange filings."""
    found: set[ExchangeEffectiveEvent] = set()
    for row in rows:
        for index, cell in enumerate(row):
            label = re.sub(r"\s+", "", cell)
            if "변경전" in label or "변경후" in label:
                continue
            kind = ""
            if "정지" in label and "일시" in label and "기간" not in label:
                kind = "resume" if "해제" in label else "halt"
            elif "해제일시" in label:
                kind = "resume"
            if kind:
                value = " ".join(row[index + 1:index + 3])
                parsed = _date_time(value)
                if parsed:
                    day, clock = parsed
                    found.add(ExchangeEffectiveEvent(kind, day, clock,
                                                     "second" if len(clock) == 8 else "minute" if clock else "date",
                                                     cell))
            if exchange_filing and "상장폐지일" in cell and not any(
                term in cell for term in ("예정", "신청", "결정")
            ):
                # The date must follow the explicit field label, not a nearby
                # liquidation period or a date in a historical explanation.
                match = _DELIST.search(cell)
                value = match.group(1) if match else " ".join(row[index + 1:index + 2])
                parsed = _date_time(value)
                if parsed:
                    found.add(ExchangeEffectiveEvent("delist", parsed[0], "", "date", cell[:100]))
    return tuple(sorted(found, key=lambda event: (event.kind, event.effective_date,
                                                   event.effective_time, event.source_label)))


def _date_time(value: str) -> tuple[str, str] | None:
    match = _DATE.search(value)
    if not match:
        return None
    year = int(match.group(1) or 2000 + int(match.group(2)))
    month, day = int(match.group(3)), int(match.group(4))
    try:
        normalized = datetime(year, month, day).date().isoformat()
    except ValueError:
        return None
    after = value[match.end():]
    clock_match = _TIME.search(after[:24])
    clock = (f"{int(clock_match.group(1)):02d}:{clock_match.group(2)}"
             + (f":{clock_match.group(3)}" if clock_match.group(3) else "")) if clock_match else ""
    return normalized, clock


def kind_trading_notice(rows: list[list[str]], title: str, stock_name: str) -> dict | None:
    """Keep timed exchange notices for the exact share class of the news target."""
    title = "".join(title.split())
    if "매매거래정지" not in title or "예고" in title or not stock_name:
        return None

    def issuer(name: str) -> str:
        name = re.sub(r"\s+|주식회사|\(주\)|㈜", "", name)
        return re.sub(r"(?:보통주권|보통주|주권)$", "", name)

    fields = [(re.sub(r"^\d+\.|\s+", "", row[0]), row[1:]) for row in rows if len(row) > 1]
    names = [name for label, cells in fields if label in {"종목명", "대상종목"} for name in cells]
    if not any(issuer(name) and issuer(name) == issuer(stock_name) for name in names):
        return None

    def exact_time(*labels: str) -> datetime | None:
        values = {_date_time(" ".join(cells[:2])) for label, cells in fields if label in labels}
        if len(values) != 1:
            return None
        value = values.pop()
        if value is None or not value[1]:
            return None
        return datetime.fromisoformat("T".join(value)).replace(tzinfo=KST)

    start = exact_time("매매거래정지일시", "매매거래정지일")
    end = exact_time("매매거래정지해제일시", "매매거래정지해제일", "매매거래재개일시")
    return {"starts_at": start.isoformat() if start else None, "ends_at": end.isoformat() if end else None,
            "verified_interval": "정정" not in title and bool(start and end and start < end)}


def kind_trading_state(notices: list[dict] | None, cut: datetime) -> dict:
    """Describe the committed interval independently of VI and order eligibility."""
    missing = {"state": "UNKNOWN", "reason": "no_visible_kind_trading_notice"}
    if notices is None:
        return {**missing, "reason": "kind_notice_window_incomplete"}
    visible = []
    for notice in notices:
        try:
            accepted = aware_iso_kst(notice["available_at"])
            start = aware_iso_kst(notice["starts_at"]) if notice.get("starts_at") else None
            end = aware_iso_kst(notice["ends_at"]) if notice.get("ends_at") else None
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
        if accepted <= cut:
            visible.append((notice, accepted, start, end))
    active = [
        (row, accepted, start, end) for row, accepted, start, end in visible
        if row.get("verified_interval") is True and start is not None and end is not None and start <= cut < end
    ]
    if len({(start, end) for _, _, start, end in active}) > 1:
        return {**missing, "reason": "conflicting_kind_intervals"}
    relevant = active or [
        (row, accepted, start, end) for row, accepted, start, end in visible
        if row.get("verified_interval") is True and start is not None and end is not None
        and cut.date() in {start.date(), end.date()}
    ]
    if not relevant:
        if any(not row.get("verified_interval") and accepted.date() == cut.date()
               for row, accepted, _, _ in visible):
            return {**missing, "reason": "unverified_kind_trading_interval"}
        return missing
    row, accepted, start, end = max(relevant, key=lambda item: item[1])
    if any(other is not row and other_accepted >= accepted and (
        other.get("original_filing_ref") == row["filing_ref"]
        or (not other.get("verified_interval") and other_accepted.date() == cut.date())
    ) for other, other_accepted, _, _ in visible):
        return {**missing, "reason": "kind_notice_changed_or_unresolved"}
    state = "HALTED" if active else "SCHEDULED" if cut < start else "RESUME_TIME_REACHED"
    return {"state": state, "source": "krx-kind", "starts_at": start.isoformat(),
            "ends_at": end.isoformat(), "available_at": accepted.isoformat(),
            "source_ref": row["source_ref"], "body_ref": row["body_ref"]}
