"""Read dated KRX share suspensions from the official KIND market notices."""

from __future__ import annotations

import html
import re
from datetime import date, time, timedelta
from time import monotonic
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from kiwoom_monitor.infrastructure.exchange_effective_dates import document_rows, extract_effective_events

from .kind_name_history import decode_kind_response


_BASE = "https://kind.krx.co.kr"
_LIST = "/disclosure/details.do"
_HALT_TYPES = "0311,0357,0345"
_PAGE = re.compile(r"전체\s*<em>([\d,]+)</em>\s*건\s*:\s*<strong>(\d+)</strong>\s*/\s*(\d+)")
_NOTICE = re.compile(r"openDisclsViewer\('(\d{14})'[^)]*\)[^>]+title=['\"]([^>]+?)['\"]\s*>")
_SYMBOL = re.compile(r"companysummary_open\('([A-Z0-9]{5,6})'\)")
_CONDITIONAL_EXPIRY = (
    "변경상장일전일까지", "우회상장여부통지일까지", "대상여부에관한결정일까지", "실질심사결과통지일까지",
)


class KindTradingHalts:
    def __init__(self) -> None:
        self._listing: tuple[date, float, dict[str, list[tuple[str, str]]]] | None = None

    def full_day_halt(self, code: str, day: date) -> dict[str, str] | None:
        """Return an explicit pre-session halt, with no same-day release in the complete listing."""
        resume_url = ""
        for receipt, title in self._notices(day).get(code, ()):
            normalized = "".join(title.split())
            if "매매거래정지" not in normalized or "예고" in normalized:
                continue
            if normalized.startswith("["):
                # The simple notice body does not resolve old versus corrected terms.
                return None
            url, rows = self._notice(receipt)
            events = [event for event in extract_effective_events(rows, exchange_filing=True)
                      if event.kind in {"halt", "resume"}]
            if not events or not any("보통주" in row for row in rows):
                return None
            halt = [event for event in events if event.kind == "halt"]
            resumes = [event for event in events if event.kind == "resume"]
            if any(event.effective_date <= day.isoformat() for event in resumes):
                return None
            if resumes:
                resume_url = url
            if not halt:
                continue
            if len(halt) != 1:
                return None
            start = halt[0]
            if start.effective_date > day.isoformat():
                continue
            if start.effective_date == day.isoformat() and start.effective_time and time.fromisoformat(start.effective_time) > time(8):
                return None
            expiry = next((row[1] for row in rows if len(row) >= 2 and "만료일시" in row[0]), "")
            if not "".join(expiry.split()).endswith(_CONDITIONAL_EXPIRY):
                # Combined/intraday notices may expire without another disclosure.
                return None
            return {"source": "krx-kind", "halt_notice_url": url, "starts_on": start.effective_date,
                    "starts_at": start.effective_time, "stated_expiry": expiry, "resume_notice_url": resume_url}
        return None

    def _notices(self, day: date) -> dict[str, list[tuple[str, str]]]:
        cached = self._listing
        if cached is not None and cached[0] == day and monotonic() - cached[1] < 300:
            return cached[2]
        symbols: dict[str, list[tuple[str, str]]] = {}
        seen: set[str] = set()
        listing_size = None
        for page in range(1, 11):
            document = self._request(_LIST, {
                "method": "searchDetailsSub", "forward": "details_sub", "currentPageSize": "100",
                "pageIndex": str(page), "orderMode": "0", "orderStat": "D", "lastReport": "",
                "fromDate": (day - timedelta(days=90)).isoformat(), "toDate": day.isoformat(),
                "disclosureType02": _HALT_TYPES, "reportNm": "", "reportNmTemp": "", "marketType": "",
                "securities": "1", "bfrDsclsType": "on", "searchCorpName": "", "searchCodeType": "",
                "repIsuSrtCd": "", "allRepIsuSrtCd": "", "business": "", "settlementMonth": "",
                "submitOblgNm": "", "enterprise": "",
            })
            result = _PAGE.search(document)
            if result is None or int(result[2]) != page:
                raise ValueError("KIND market notice pagination could not be verified")
            total = int(result[1].replace(",", ""))
            if listing_size is not None and listing_size != total:
                raise ValueError("KIND market notice count changed while paging")
            listing_size = total
            for row in re.findall(r"<tr\b[^>]*>.*?</tr>", document, re.DOTALL):
                if "openDisclsViewer(" not in row:
                    continue
                notice, symbol = _NOTICE.search(row), _SYMBOL.search(row)
                if notice is None or symbol is None or notice[1] in seen:
                    raise ValueError("KIND market notice row could not be identified")
                seen.add(notice[1])
                short_code = symbol[1].ljust(6, "0")
                symbols.setdefault(short_code, []).append((notice[1], html.unescape(notice[2])))
            if page == int(result[3]):
                if len(seen) != total:
                    raise ValueError("KIND market notice listing was incomplete")
                for notices in symbols.values():
                    notices.sort(reverse=True)
                self._listing = (day, monotonic(), symbols)
                return symbols
        raise ValueError("KIND market notice listing exceeded the lookup limit")

    def _notice(self, receipt: str) -> tuple[str, list[list[str]]]:
        viewer = self._request(f"/common/disclsviewer.do?method=search&acptno={receipt}&docno=&viewerhost=&viewerport=")
        document_number = re.search(r"<option value=['\"](\d+)\|", viewer)
        if document_number is None:
            raise ValueError("KIND original notice number is missing")
        contents = self._request(f"/common/disclsviewer.do?method=searchContents&docNo={document_number[1]}")
        path = re.search(r"parent\.setPath\('',\s*'https://kind\.krx\.co\.kr(/external/[\w/.\-]+\.htm)'", contents)
        if path is None:
            raise ValueError("KIND original notice URL is missing")
        return _BASE + path[1], document_rows(self._request(path[1]))

    def _request(self, path: str, fields: dict[str, str] | None = None) -> str:
        request = Request(_BASE + path, data=urlencode(fields).encode() if fields else None, headers={
            "User-Agent": "Mozilla/5.0 KiwoomRealtimeMonitor/1.1",
            "Referer": _BASE + _LIST + "?method=searchDetailsMain",
            "X-Requested-With": "XMLHttpRequest",
        })
        with urlopen(request, timeout=5) as response:
            return decode_kind_response(response)
