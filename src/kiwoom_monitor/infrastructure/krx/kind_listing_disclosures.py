"""Read KRX listing dates and independently corroborate their listing type in KIND."""

from __future__ import annotations

import html
import re
from collections.abc import Callable, Collection, Iterator
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from kiwoom_monitor.domain.market_data_contract import DataCompleteness, ListingEvidence, ListingType
from kiwoom_monitor.domain.time_basis import KST
from kiwoom_monitor.infrastructure.exchange_effective_dates import document_rows

from .kind_name_history import decode_kind_response


_BASE = "https://kind.krx.co.kr"
_DISCLOSURES = "/disclosure/details.do"
_TYPES = "/listinvstg/listingcompany.do"
_RECENT_DAYS = 14
_SOURCE = "krx-kind-listing-disclosure"
_LISTED_TYPES = {
    "신규상장": ("01", ListingType.IPO),
    "이전상장": ("02", ListingType.TRANSFER),
    "재상장": ("03", ListingType.RELISTING),
    "재상장 폐지": ("04", ListingType.RELISTING),
    "재상장 합병": ("05", ListingType.RELISTING),
}
_NOTICE_TITLES = {"신규상장", "이전상장", "재상장"}
_MARKET_FILERS = {"유가증권시장본부", "코스닥시장본부", "코넥스시장본부"}
_KONEX_TRANSFER_NOTICE = "상장폐지(코스닥시장이전상장)"
_KONEX_FILERS = {"코넥스시장", "코넥스시장본부"}
_ROW = re.compile(r"<tr\b[^>]*>.*?</tr>", re.IGNORECASE | re.DOTALL)
_PAGE = re.compile(r"전체\s*<em>([\d,]+)</em>\s*건\s*:\s*<strong>(\d+)</strong>\s*/\s*(\d+)")
_RECEIPT = re.compile(r"openDisclsViewer\('(\d{14})'")
_COMPANY = re.compile(r"companysummary_open\('([A-Z0-9]{5,6})'\)")
_CATALOG_ROW = re.compile(r"fnDetailView\('([A-Z0-9]{5,6})','(\d{14}|)'\)")
_SELECTED_DOC = re.compile(r"<option\s+value=['\"](\d{14})\|[YN]['\"]\s*selected(?:\s*=|\s|>)", re.IGNORECASE)
_BODY_PATH = re.compile(r"parent\.setPath\('',\s*'https://kind\.krx\.co\.kr(/external/[\d/]+\.htm)'")
_PUBLISHED = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}(:\d{2})?")
_KOREAN_DATE = re.compile(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일")
_STOCK_CODE = re.compile(r"[A-Z0-9]{6}")
_DATED_TITLE = re.compile(r"(신규상장|이전상장|재상장)\(.+,\s*상장일\s*\d{4}\.\d{2}\.\d{2}\)")
_PRE = re.compile(r"<pre\b[^>]*>(.*?)</pre>", re.IGNORECASE | re.DOTALL)
_ISSUE_SECTION = re.compile(r"(?m)^[ \t]*①\s*상장종목명\b")
_ORDINARY_ISSUE = re.compile(r"(?m)^[ \t]*-\s*한글종목명\s*:[^\r\n]*보통주(?:권|식)?[ \t]*\r?$")
_ISSUE_CODE = re.compile(r"(?m)^[ \t]*④\s*표준코드\s*:\s*KR[A-Z0-9]{10}\s*\(단축코드\s*:\s*A([A-Z0-9]{6})\)")
_ISSUE_DAY = re.compile(r"(?m)^[ \t]*②\s*상장일\s*:[ \t]*([^\r\n]+)")


@dataclass(frozen=True)
class _Notice:
    receipt: str
    code: str
    title: str
    available_at: datetime | None
    filer: str

    @property
    def viewer(self) -> str:
        return f"/common/disclsviewer.do?method=search&acptno={self.receipt}&docno=&viewerhost=&viewerport="


class KindListingDisclosures:
    """Read-only source. The undated type catalog is eligible from its observation time."""

    def __init__(self, *, now_provider: Callable[[], datetime] | None = None) -> None:
        self._now = now_provider
        self._verified: dict[_Notice, ListingEvidence] = {}

    def listing_evidence(
        self, code: str, *, trading_date: date, as_of: datetime, from_date: date | None = None,
    ) -> tuple[ListingEvidence, ...]:
        return self.listing_evidence_for((code,), trading_date=trading_date, as_of=as_of, from_date=from_date)

    def listing_evidence_for(
        self, codes: Collection[str] | None = None, *, trading_date: date, as_of: datetime,
        from_date: date | None = None,
    ) -> tuple[ListingEvidence, ...]:
        cutoff = _kst_instant(as_of)
        end = cutoff.date()
        start = from_date or end - timedelta(days=_RECENT_DAYS - 1)
        if start > end or end > trading_date:
            raise ValueError("invalid KIND listing evidence dates")
        if codes is not None and not codes:
            return ()

        notices = tuple(self._notices(start, end))
        first_receipt_day = start.strftime("%Y%m%d")
        self._verified = {notice: row for notice, row in self._verified.items() if notice.receipt[:8] >= first_receipt_day}
        rows: list[ListingEvidence] = []
        latest: dict[str, list[ListingEvidence]] = {}
        uncertain: set[str] = set()
        for notice in notices:
            if codes is not None and notice.code not in codes:
                continue
            published = notice.available_at
            if published is not None and published > cutoff:
                continue
            row = self._verified_evidence(notice)
            rows.append(row)
            if published is None:
                uncertain.add(notice.code)
                continue
            same_time = latest.get(notice.code)
            if not same_time or published > same_time[0].available_at:
                latest[notice.code] = [row]
            elif published == same_time[0].available_at:
                same_time.append(row)

        by_receipt = {notice.receipt: notice for notice in notices}
        pending = [group[0] for code, group in latest.items() if code not in uncertain and len(group) == 1
                   and group[0].listing_date is not None
                   and group[0].listing_type in {ListingType.UNKNOWN, ListingType.TRANSFER}]
        if pending and self._now is not None:
            kosdaq_codes = {row.symbol for row in pending if by_receipt[row.revision].filer == "코스닥시장본부"}
            departures = self._konex_departures(start, cutoff, kosdaq_codes)
            generic = [row for row in pending if row.listing_type == ListingType.UNKNOWN]
            days = [row.listing_date for row in generic]
            try:
                catalog = self._types(min(days), max(days)) if days else {}
            except (OSError, ValueError):
                catalog = {}
            for row in pending:
                labels = catalog.get((row.symbol, row.listing_date), ())
                origin = departures.get(row.symbol, ())
                if origin:
                    receipts, origin_row = self._select_departure(origin)
                    observed = _kst_instant(self._now())
                    if observed > row.available_at:
                        verified = origin_row if all(label == "이전상장" for label, _ in labels) else None
                        rows.append(_transfer_evidence(row, receipts, verified, observed))
                    continue
                observed = _kst_instant(self._now())
                if observed <= row.available_at:
                    continue
                if len(labels) != 1:
                    continue
                label, process = next(iter(labels))
                category, listing_type = _LISTED_TYPES[label]
                rows.append(replace(
                    row, available_at=observed,
                    listing_type=listing_type,
                    completeness=DataCompleteness.COMPLETE,
                    source=f"{_SOURCE}+listing-type",
                    revision=f"{row.revision}/kind-type:{category}:{process}",
                    evidence_id=f"{row.evidence_id}#kind-listing-type-{category}-{process}",
                ))
        return tuple(rows)

    def _konex_departures(self, start: date, cutoff: datetime, codes: set[str]) -> dict[str, list[_Notice]]:
        if not codes:
            return {}
        departures: dict[str, list[_Notice]] = {}
        try:
            for notice in self._notices(start, cutoff.date(), konex_departures=True):
                if notice.code in codes and (notice.available_at is None or notice.available_at <= cutoff):
                    departures.setdefault(notice.code, []).append(notice)
        except (OSError, ValueError):
            return {}
        return departures

    def _select_departure(self, origins: list[_Notice]) -> tuple[str, ListingEvidence | None]:
        latest = [notice for notice in origins if notice.available_at is None]
        if not latest:
            newest = max(notice.available_at for notice in origins)
            latest = [notice for notice in origins if notice.available_at == newest]
        origin = self._verified_evidence(latest[0]) if len(latest) == 1 and latest[0].available_at else None
        receipts = "+".join(sorted(notice.receipt for notice in latest))
        return receipts, origin

    def _notices(self, start: date, end: date, *, konex_departures: bool = False) -> Iterator[_Notice]:
        fields = {
            "method": "searchDetailsSub", "forward": "details_sub", "orderMode": "1", "orderStat": "D",
            "lastReport": "", "disclosureType02": "0321", "reportNm": "", "reportNmTemp": "",
            "marketType": "", "securities": "1", "bfrDsclsType": "on", "searchCorpName": "",
            "searchCodeType": "", "repIsuSrtCd": "", "allRepIsuSrtCd": "", "business": "",
            "settlementMonth": "", "submitOblgNm": "", "enterprise": "",
        }
        if konex_departures:
            fields.update(disclosureType02="", reportNm="상장폐지", reportNmTemp="상장폐지")
        seen: set[str] = set()
        for raw in self._paged_rows(_DISCLOSURES, fields, start, end, "openDisclsViewer("):
            receipt = _RECEIPT.search(raw)
            if receipt is None or receipt[1] in seen:
                raise ValueError("KIND disclosure receipt could not be verified")
            seen.add(receipt[1])
            cells, = document_rows(raw)
            if len(cells) < 5:
                raise ValueError("KIND disclosure list row is incomplete")
            filer = cells[4]
            title = re.sub(r"^\[정정\]\s*", "", cells[3]).strip()
            if konex_departures:
                title = re.sub(r"\s+", "", title)
                if filer not in _KONEX_FILERS or title != _KONEX_TRANSFER_NOTICE:
                    continue
            else:
                dated_title = _DATED_TITLE.fullmatch(title)
                if dated_title is not None:
                    title = dated_title[1]
                if filer not in _MARKET_FILERS or title not in _NOTICE_TITLES:
                    continue
            company = _COMPANY.search(raw)
            if company is None:
                raise ValueError("KIND listing disclosure symbol could not be verified")
            filed = datetime.strptime(receipt[1][:8], "%Y%m%d").date()
            if not start <= filed <= end:
                raise ValueError("KIND listing disclosure falls outside the query dates")
            stamp = _published_at(cells[1], receipt[1])
            yield _Notice(receipt[1], company[1].ljust(6, "0"), title, stamp, filer)

    def _verified_evidence(self, notice: _Notice) -> ListingEvidence:
        row = self._verified.get(notice)
        if row is None:
            row = self._evidence(notice)
            if row.listing_date is not None:
                self._verified[notice] = row
        return row

    def _evidence(self, notice: _Notice) -> ListingEvidence:
        url = _BASE + notice.viewer
        day = None
        listing_type = ListingType.UNKNOWN
        unresolved = []
        if notice.available_at is None:
            unresolved.append("publication")
        try:
            viewer = self._request(notice.viewer)
            doc = _SELECTED_DOC.search(viewer)
            if doc is None:
                raise ValueError("KIND selected disclosure document is missing")
            contents = self._request(f"/common/disclsviewer.do?method=searchContents&docNo={doc[1]}")
            path = _BODY_PATH.search(contents)
            receipt = notice.receipt
            expected = f"/external/{receipt[:4]}/{receipt[4:6]}/{receipt[6:8]}/{receipt[8:]}/{doc[1]}/"
            if path is None or not path[1].startswith(expected):
                raise ValueError("KIND original document did not match its disclosure receipt")
            url = _BASE + path[1]
            day, listing_type = _listing_fields(self._request(path[1]), notice)
        except (OSError, ValueError):
            # A correction with no usable body still supersedes older evidence at its release.
            unresolved.append("body")
        if unresolved:
            url += "#unverified-" + "-".join(unresolved)
        return ListingEvidence(
            notice.code, day, listing_type, notice.available_at, _SOURCE, notice.receipt, url,
            DataCompleteness.COMPLETE if listing_type != ListingType.UNKNOWN and notice.available_at is not None
            else DataCompleteness.PARTIAL,
        )

    def _types(self, start: date, end: date) -> dict[tuple[str, date], set[tuple[str, str]]]:
        fields = {
            "method": "searchListingTypeSub", "forward": "listingtype_sub", "orderMode": "1", "orderStat": "D",
            "listTypeArrStr": "01|02|03|04|05|", "secuGrpArrStr": "ST|FS|", "choicTypeArrStr": "",
            "marketType": "", "country": "", "industry": "", "repMajAgntDesignAdvserComp": "",
            "searchCorpName": "", "searchCodeType": "", "repIsuSrtCd": "", "isurCd": "",
        }
        catalog: dict[tuple[str, date], set[tuple[str, str]]] = {}
        for raw in self._paged_rows(_TYPES, fields, start, end, "fnDetailView("):
            code = _CATALOG_ROW.search(raw)
            cells, = document_rows(raw)
            if code is None or len(cells) < 4 or cells[2] not in _LISTED_TYPES or cells[3] != "주권":
                continue
            day = date.fromisoformat(cells[1])
            if start <= day <= end:
                catalog.setdefault((code[1].ljust(6, "0"), day), set()).add((cells[2], code[2]))
        return catalog

    def _paged_rows(
        self, path: str, fields: dict[str, str], start: date, end: date, row_marker: str,
    ) -> Iterator[str]:
        page, count = 1, 0
        expected = None
        while True:
            document = self._request(path, dict(fields, fromDate=start.isoformat(), toDate=end.isoformat(),
                                                pageIndex=str(page), currentPageSize="100"))
            pagination = _PAGE.search(document)
            if pagination is None or int(pagination[2]) != page:
                raise ValueError("KIND listing pagination could not be verified")
            totals = (int(pagination[1].replace(",", "")), int(pagination[3]))
            if expected is not None and totals != expected:
                raise ValueError("KIND listing count changed while paging")
            expected = totals
            for row in _ROW.findall(document):
                if row_marker in row:
                    count += 1
                    yield row
            if page == totals[1]:
                if count != totals[0]:
                    raise ValueError("KIND listing was incomplete")
                return
            if not page < totals[1] <= max(totals[0], 1):
                raise ValueError("KIND listing page count is invalid")
            page += 1

    def _request(self, path: str, fields: dict[str, str] | None = None) -> str:
        referer = _TYPES + "?method=searchListingTypeMain" if path == _TYPES else _DISCLOSURES + "?method=searchDetailsMain"
        request = Request(_BASE + path, data=urlencode(fields).encode() if fields else None, headers={
            "User-Agent": "Mozilla/5.0 KiwoomRealtimeMonitor/1.1",
            "Referer": _BASE + referer,
            "X-Requested-With": "XMLHttpRequest",
        })
        with urlopen(request, timeout=5) as response:
            return decode_kind_response(response)


def _kst_instant(value: datetime) -> datetime:
    if value.utcoffset() is None:
        raise ValueError("KIND listing evidence requires a timezone-aware clock")
    return value.astimezone(KST)


def _published_at(value: str, receipt: str) -> datetime | None:
    match = _PUBLISHED.fullmatch(value)
    if match is None or value[:10].replace("-", "") != receipt[:8]:
        return None
    try:
        stamp = datetime.fromisoformat(value).replace(tzinfo=KST)
    except ValueError:
        return None
    # A minute-precision publication is only certain once that minute ends.
    return stamp if match[1] else stamp + timedelta(minutes=1)


def _transfer_evidence(
    listing: ListingEvidence, receipts: str, origin: ListingEvidence | None, observed: datetime,
) -> ListingEvidence:
    verified = origin is not None and origin.listing_date == listing.listing_date
    if verified:
        evidence_id = f"{origin.evidence_id}#kosdaq-listing-{listing.revision}"
    else:
        evidence_id = f"{listing.evidence_id}#kind-konex-unverified-{receipts}"
    return replace(
        listing, available_at=observed, source=f"{_SOURCE}+konex-transfer",
        listing_type=ListingType.KONEX_TO_KOSDAQ if verified else ListingType.UNKNOWN,
        completeness=DataCompleteness.COMPLETE if verified else DataCompleteness.PARTIAL,
        revision=f"{listing.revision}/kind-type:02:konex:{receipts}", evidence_id=evidence_id,
    )


def _listing_fields(document: str, notice: _Notice) -> tuple[date, ListingType]:
    days: set[date] = set()
    codes: set[str] = set()
    spac = False
    departing = notice.title == _KONEX_TRANSFER_NOTICE
    date_labels = {"상장폐지일"} if departing else {"상장일(매매개시일)", "상장일", "매매개시일"}
    reasons: set[str] = set()
    for row in document_rows(document):
        label = re.sub(r"^\d+\.\s*", "", row[0]).replace(" ", "")
        if label in {"보통주", "보통주식"}:
            codes.update(value[1:] for value in row[1:] if value.startswith("A") and _STOCK_CODE.fullmatch(value[1:]))
        if label in date_labels and len(row) == 2:
            days.add(_listing_date(row[1]))
        if departing and label == "상장폐지사유" and len(row) == 2:
            reasons.add(re.sub(r"\s+", "", row[1]))
        if label == "소속부" and row[1:] == ["SPAC(소속부없음)"]:
            spac = True
    if not departing and not codes and not days:
        # KOSPI uses a preformatted issue list. The ordinary and preferred
        # sections can report different codes, so keep date and code paired.
        for body in _PRE.findall(document):
            for issue in _ISSUE_SECTION.split(html.unescape(body))[1:]:
                code = _ISSUE_CODE.search(issue)
                day = _ISSUE_DAY.search(issue)
                if code and day and _ORDINARY_ISSUE.search(issue):
                    codes.add(code[1])
                    days.add(_listing_date(day[1].strip()))
    if codes != {notice.code} or len(days) != 1:
        raise ValueError("KIND disclosure did not establish an exact security and listing date")
    if departing:
        if reasons != {"코스닥시장이전상장"}:
            raise ValueError("KIND departure did not establish transfer to KOSDAQ")
        listing_type = ListingType.TRANSFER
    elif notice.title == "신규상장":
        # KIND uses the same headline for transfers. The form identifies SPAC
        # explicitly, while ordinary shares need the separate type catalog.
        listing_type = ListingType.SPAC if spac else ListingType.UNKNOWN
    else:
        _, listing_type = _LISTED_TYPES[notice.title]
    return next(iter(days)), listing_type


def _listing_date(value: str) -> date:
    match = _KOREAN_DATE.fullmatch(value)
    return date(*(int(item) for item in match.groups())) if match else date.fromisoformat(value)
