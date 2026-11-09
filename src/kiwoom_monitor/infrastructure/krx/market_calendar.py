"""Read KRX's public holiday table and equity session notices."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta
from urllib.parse import urlencode
from urllib.request import Request, urlopen


_BASE = "https://open.krx.co.kr"
_API = _BASE + "/contents/OPN/99/OPN99000001.jspx"
_HOLIDAYS = "/contents/MKD/01/0110/01100305/MKD01100305.jsp"
_PRESS = "/contents/OPN/05/05000000/OPN05000000T1.jsp"
_PRESS_BLD = "OPN/05/05000000/opn05000000t1_"
HOLIDAY_SOURCE = _BASE + _HOLIDAYS
_CLOCK_RANGE = r"(\d{1,2}:\d{2})\s*[~～∼]\s*(\d{1,2}:\d{2})"
_EQUITIES = re.compile(r"1\)\s*유가증권시장\s*,\s*코스닥시장.*?(?=\n\s*2\)\s*파생상품시장)", re.S)
_REGULAR_ROW = re.compile(r"(?:^|\n)\s*정규시장\*?\s*(?:\([^)]*\)\s*)?" + _CLOCK_RANGE + r"\s+" + _CLOCK_RANGE)
_DATED_NOTICE = re.compile(r"\(\s*(\d{1,2})(?:\s*\.\s*|\s*월\s*)(\d{1,2})(?:일)?\s*[),]")


class KrxMarketCalendar:
    def __init__(self, *, request=None) -> None:
        self._request = request or _request

    def load(self, start: date, end: date, *, as_of: date | None = None) -> dict[date, tuple[tuple[time, time] | None, str]]:
        """Return evidenced equity days only. A missing date stays unresolved."""
        days = {}
        for year in range(start.year, end.year + 1):
            holidays = self._holidays(year)
            if not holidays:
                continue  # KRX may not yet have published the next year's table.
            day = max(start, date(year, 1, 1))
            while day <= min(end, date(year, 12, 31)):
                closed = day.weekday() >= 5 or day in holidays
                days[day] = (None if closed else (time(9), time(15, 30)), HOLIDAY_SOURCE)
                day += timedelta(days=1)

        revisions = {}
        for posted, seq, title in self._notices(as_of or start):
            dates = _notice_dates(title, posted)
            if dates and all(day < start or day > end for day in dates) and "연초" not in title:
                continue
            text, source = self._notice_text(seq)
            for day, hours in _notice_sessions(title, posted, text).items():
                if not start <= day <= end:
                    continue
                previous = revisions.get(day)
                if previous and previous[0] == posted and previous[1] != hours:
                    raise ValueError("conflicting KRX equity notices for one date")
                if previous is None or posted >= previous[0]:
                    revisions[day] = (posted, hours)
                    if hours is None or days.get(day) != (None, HOLIDAY_SOURCE):
                        days[day] = (hours, source)
        return days

    def _post(self, page: str, bld: str, **fields):
        return json.loads(self._request(_API, _BASE + page, {"pagePath": page, "bld": bld, **fields}))

    def _holidays(self, year: int) -> set[date]:
        response = self._post(_HOLIDAYS, "MKD/01/0110/01100305/mkd01100305_01", search_bas_yy=year)
        rows = response["block1"]
        days = {date.fromisoformat(row["calnd_dd"]) for row in rows if row["holdy_nm"].strip()}
        if len(days) != len(rows) or any(day.year != year for day in days):
            raise ValueError("invalid KRX holiday table")
        return days

    def _notices(self, as_of: date):
        notices = {}
        for keyword in ("거래시간", "운영시간", "개장일", "휴장"):
            rows = self._post(_PRESS, _PRESS_BLD + "01", sch_tp="title", sch_word=keyword,
                              fromdate=(as_of - timedelta(days=90)).strftime("%Y%m%d"),
                              todate=as_of.strftime("%Y%m%d"), curPage=1, pageSize=100)["output"]
            if rows and any(int(row["totCnt"]) != len(rows) for row in rows):
                raise ValueError("incomplete KRX market notices")
            for row in rows:
                title = row["title"]
                if _equity_notice(title):
                    posted = date.fromisoformat(row["noti_dd"].replace("/", "-"))
                    if posted <= as_of:
                        notices[row["noti_no"]] = (posted, row["noti_no"], title)
        return sorted(notices.values())

    def _notice_text(self, seq: str) -> tuple[str, str]:
        if not re.fullmatch(r"\d+", seq):
            raise ValueError("invalid KRX notice number")
        files = self._post(_PRESS, _PRESS_BLD + "03", seq=seq)["block1"]
        pdfs = [item for item in files if item["file_nm"].endswith(".pdf")]
        if len(pdfs) != 1:
            raise ValueError("KRX equity notice PDF unavailable")
        filename = pdfs[0]["save_file_nm"]
        if pdfs[0]["file_path"] != "/obk/dyn/noti/" or not re.fullmatch(r"[\w-]+\.pdf", filename):
            raise ValueError("invalid KRX notice attachment path")
        url = "https://inc.krx.co.kr/attach/obk/dyn/noti/" + filename
        return _pdf_text(self._request(url, _BASE + _PRESS, None)), url


def _equity_notice(title: str) -> bool:
    text = "".join(title.split())
    if text.startswith("(보도참고자료)"):
        return False
    if "연초개장일" in text or "대학수학능력시험일" in text:
        return True
    equity = re.search(r"증권시장|증권[·∙‧ㆍ]파생|주식시장|유가증권시장|코스닥시장", text)
    return bool(equity and ("휴장" in text or re.search(r"(?:거래|운영)시간.*(?:변경|조정|순연|연기)", text)))


def _notice_dates(title: str, posted: date) -> tuple[date, ...]:
    days = []
    for month, day in _DATED_NOTICE.findall(title):
        value = date(posted.year, int(month), int(day))
        if value.month < posted.month and posted.month == 12 and value.month == 1:
            value = value.replace(year=posted.year + 1)
        days.append(value)
    return tuple(days)


def _notice_sessions(title: str, posted: date, text: str) -> dict[date, tuple[time, time] | None]:
    text = text.replace("\r", "")
    days = _notice_dates(title, posted)
    results = {}
    year_end = re.search(r"연말 휴장일[^:\n]*:\s*(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})", text)
    if year_end:
        results[date(*map(int, year_end.groups()))] = None
    if "휴장" in title and "연초" not in title:
        if not days or not re.search(r"휴장일\s*:", text) or not re.search(r"증권시장\s*\[\s*주식시장", text):
            raise ValueError("KRX stock market closure could not be confirmed")
        return dict.fromkeys(days)
    equity = _EQUITIES.search(text)
    row = _REGULAR_ROW.search(equity[0]) if equity else None
    if len(days) != 1 or row is None:
        raise ValueError("KRX stock market regular session could not be confirmed")
    day = days[0]
    opened, closed = (time.fromisoformat(value.zfill(5)) for value in row.groups()[2:])
    if datetime.combine(day, closed) - datetime.combine(day, opened) <= timedelta(minutes=10):
        raise ValueError("invalid KRX stock market regular session")
    results[day] = (opened, closed)
    return results


def _pdf_text(raw: bytes) -> str:
    import pypdfium2 as pdfium

    with pdfium.PdfDocument(raw) as document:
        pages = []
        for page in document:
            text = page.get_textpage()
            try:
                pages.append(text.get_text_range())
            finally:
                text.close()
                page.close()
        return "\n".join(pages)


def _request(url: str, referer: str, fields: dict | None) -> bytes:
    request = Request(url, data=urlencode(fields).encode() if fields is not None else None, headers={
        "User-Agent": "Mozilla/5.0 KiwoomRealtimeMonitor/1.1",
        "Referer": referer,
        "X-Requested-With": "XMLHttpRequest",
    })
    with urlopen(request, timeout=5) as response:
        raw = response.read(5_000_001)
    if len(raw) > 5_000_000:
        raise ValueError("KRX calendar response is too large")
    return raw
