"""KIND 상호변경안내를 작은 증분 작업으로 보충한다."""

from __future__ import annotations

import html
import re
import sqlite3
from datetime import date, timedelta
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener


_BASE = "https://kind.krx.co.kr"
_USER_AGENT = "Mozilla/5.0 KiwoomRealtimeMonitor/1.1"


def _decode_response(response: object) -> str:
    payload = response.read()
    charset = response.headers.get_content_charset()
    head = payload[:2048].decode("ascii", errors="ignore")
    meta = re.search(r"charset\s*=\s*[\"']?([\w-]+)", head, re.IGNORECASE)
    encodings = [value for value in (charset, meta.group(1) if meta else None, "utf-8", "euc-kr") if value]
    candidates = [payload.decode(encoding, errors="replace") for encoding in dict.fromkeys(encodings)]
    return min(candidates, key=lambda value: value.count("�"))


def _clean_company_name(value: str) -> str:
    value = html.unescape(re.sub(r"<[^>]+>", " ", value))
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"^(?:주식회사|㈜|\(주\))\s*", "", value)
    value = re.sub(r"\s*(?:주식회사|㈜|\(주\))$", "", value)
    return value.strip()


def parse_kind_disclosure_list(document: str) -> tuple[tuple[str, str, str, str], ...]:
    rows = re.findall(
        r"<tr[^>]*>.*?(\d{4}-\d{2}-\d{2})\s+\d{2}:\d{2}.*?"
        r"companysummary_open\('(\d+)'\).*?title='([^']+)'.*?"
        r"openDisclsViewer\('(\d+)'",
        document,
        re.DOTALL,
    )
    # KIND의 companysummary_open 값은 보통 종목코드 마지막 0을 생략한 5자리다.
    return tuple((acpt_no, code + "0" if len(code) == 5 else code.zfill(6), html.unescape(name).strip(), day) for day, code, name, acpt_no in rows)


def parse_kind_former_names(document: str) -> tuple[str, ...]:
    names: list[str] = []
    direct = re.search(
        r"가\.\s*변경전</span>.*?국문</span>.*?class=[\"']xforms_input[\"'][^>]*>(.*?)</span>",
        document,
        re.DOTALL,
    )
    if direct:
        names.append(_clean_company_name(direct.group(1)))
    text = _clean_company_name(document)
    for old_name in re.findall(r"변경전\s*:\s*(.*?)\s*(?:→|-&gt;|->)\s*변경후", text):
        names.append(_clean_company_name(old_name))
    return tuple(dict.fromkeys(name for name in names if name))


class KindNameHistorySync:
    """목록은 연 단위로 받고 상세 본문은 실행당 일부만 처리한다."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path
        self._opener = build_opener(HTTPCookieProcessor(CookieJar()))

    def sync(self, *, initial: bool, detail_limit: int = 12) -> int:
        self._get("/disclosure/details.do?method=searchDetailsMain")
        today = date.today()
        ranges: list[tuple[date, date]] = []
        end = today
        years = 3 if initial else 1
        for _ in range(years):
            start = max(date(1996, 1, 1), end - timedelta(days=364))
            ranges.append((start, end))
            end = start - timedelta(days=1)
        disclosures: dict[str, tuple[str, str, str, str]] = {}
        for start, end in ranges:
            for row in parse_kind_disclosure_list(self._search(start, end)):
                disclosures[row[0]] = row
        connection = sqlite3.connect(self._database_path)
        try:
            with connection:
                connection.executemany(
                    "INSERT OR IGNORE INTO kind_name_disclosures(acpt_no,stock_code,current_name,disclosed_on,status) "
                    "VALUES(?,?,?,?,'pending')",
                    tuple(disclosures.values()),
                )
            pending = connection.execute(
                "SELECT acpt_no,stock_code,current_name FROM kind_name_disclosures d "
                "WHERE status='pending' AND EXISTS(SELECT 1 FROM stocks s WHERE s.code=d.stock_code) "
                "ORDER BY CASE WHEN EXISTS(SELECT 1 FROM stock_name_history h "
                "WHERE h.stock_code=d.stock_code AND h.source='기본 과거명 자료' AND h.decision='pending') "
                "THEN 0 ELSE 1 END, disclosed_on DESC LIMIT ?",
                (detail_limit,),
            ).fetchall()
            saved = 0
            for acpt_no, stock_code, current_name in pending:
                try:
                    former_names = self._load_former_names(str(acpt_no))
                except Exception:
                    continue
                with connection:
                    for old_name in former_names:
                        if old_name == current_name:
                            continue
                        connection.execute(
                            "INSERT INTO stock_name_history(stock_code,old_name,new_name,source,decision) "
                            "VALUES(?,?,?,'KIND 상호변경안내','pending') "
                            "ON CONFLICT(stock_code,old_name) DO UPDATE SET new_name=excluded.new_name,source=excluded.source",
                            (stock_code, old_name, current_name),
                        )
                        saved += 1
                    connection.execute("UPDATE kind_name_disclosures SET status='completed' WHERE acpt_no=?", (acpt_no,))
            return saved
        finally:
            connection.close()

    def _search(self, start: date, end: date) -> str:
        fields = {
            "method": "searchDetailsSub", "currentPageSize": "100", "pageIndex": "1",
            "orderMode": "1", "orderStat": "D", "forward": "details_sub",
            "fromDate": start.isoformat(), "toDate": end.isoformat(),
            "reportNm": "상호변경안내", "reportNmTemp": "상호변경안내",
            "marketType": "", "securities": "1", "bfrDsclsType": "on",
            "searchCorpName": "", "searchCodeType": "", "repIsuSrtCd": "",
            "allRepIsuSrtCd": "", "business": "", "settlementMonth": "",
            "submitOblgNm": "", "enterprise": "", "lastReport": "T",
        }
        return self._post("/disclosure/details.do", fields)

    def _load_former_names(self, acpt_no: str) -> tuple[str, ...]:
        viewer = self._get(f"/common/disclsviewer.do?method=search&acptno={acpt_no}&docno=&viewerhost=&viewerport=")
        match = re.search(r"<option value=['\"](\d+)\|", viewer)
        if match is None:
            return ()
        path_document = self._get(f"/common/disclsviewer.do?method=searchContents&docNo={match.group(1)}")
        path_match = re.search(r"parent\.setPath\('',\s*'([^']+)'", path_document)
        if path_match is None:
            return ()
        return parse_kind_former_names(self._get(path_match.group(1)))

    def _get(self, path: str) -> str:
        url = path if path.startswith("http") else _BASE + path
        request = Request(url, headers={"User-Agent": _USER_AGENT, "Referer": _BASE + "/"})
        with self._opener.open(request, timeout=20) as response:
            return _decode_response(response)

    def _post(self, path: str, fields: dict[str, str]) -> str:
        request = Request(
            _BASE + path,
            data=urlencode(fields).encode(),
            headers={"User-Agent": _USER_AGENT, "Referer": _BASE + "/disclosure/details.do?method=searchDetailsMain", "X-Requested-With": "XMLHttpRequest"},
        )
        with self._opener.open(request, timeout=30) as response:
            return _decode_response(response)
