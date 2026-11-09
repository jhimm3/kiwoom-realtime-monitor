import unittest
from datetime import date, datetime
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from kiwoom_monitor.domain.market_data_contract import (
    DataCompleteness, ListingEvidenceStatus, ListingPhase, ListingType, select_listing_evidence,
)
from kiwoom_monitor.domain.time_basis import KST
from kiwoom_monitor.infrastructure.krx.kind_listing_disclosures import KindListingDisclosures


ORIGINAL = "20250818000421"
CORRECTION = "20250819000463"
ORIGINAL_DOC = "20250818001151"
CORRECTION_DOC = "20250819001295"
LISTING_DAY = date(2025, 8, 20)
TRANSFER = "20261013000597"
TRANSFER_DOC = "20261013001434"
KONEX_DEPARTURE = "20261013000661"
KONEX_DEPARTURE_DOC = "20261013001505"


def _time(value):
    return datetime.fromisoformat(value).replace(tzinfo=KST)


def _body_path(receipt, doc):
    return f"/external/{receipt[:4]}/{receipt[4:6]}/{receipt[6:8]}/{receipt[8:]}/{doc}/70760.htm"


def _list_row(receipt, stamp, code, title="신규상장", filer="코스닥시장본부"):
    return f"""<tr><td>1</td><td>{stamp}</td>
    <td><a onclick="companysummary_open('{code}'); return false;">회사</a></td>
    <td><a onclick="openDisclsViewer('{receipt}','')" title='신규상장'>{title}</a></td>
    <td>{filer}</td><td></td></tr>"""


def _type_row(code, process, day, listing_type):
    return f"""<tr onclick="fnDetailView('{code}','{process}')"><td>회사</td>
    <td>{day}</td><td>{listing_type}</td><td>주권</td></tr>"""


def _page(*rows, page=1, pages=1, total=None):
    return f"전체 <em>{len(rows) if total is None else total}</em>건 : <strong>{page}</strong>/{pages}" + "".join(rows)


def _body(code, day, *, spac=False, prefix=""):
    return f"""<table>{prefix}
    <tr><td>보통주</td><td>KR7{code}003</td><td>A{code}</td><td>36,502,352</td></tr>
    <tr><td>7.소속부</td><td>{'SPAC(소속부없음)' if spac else '벤처기업부'}</td></tr>
    <tr><td>9.승인일</td><td>2025년 08월 18일</td></tr>
    <tr><td>10.상장일(매매개시일)</td><td>{day.year}년 {day.month:02d}월 {day.day:02d}일</td></tr>
    <tr><td>13.비고</td><td>투자설명서는 KIND IPO 현황의 공모기업란을 참고.</td></tr></table>"""


class _KindFixture:
    def __init__(self, *, listings, filings, catalog="", departures=""):
        self.listings = listings
        self.filings = filings
        self.catalog = catalog or _page()
        self.departures = departures or _page()
        self.calls = []

    def request(self, path, fields=None):
        self.calls.append((path, fields))
        url = urlsplit(path)
        query = parse_qs(url.query)
        if path == "/disclosure/details.do":
            if fields["reportNm"] == "상장폐지":
                return self.departures
            return self.listings[int(fields["pageIndex"]) - 1]
        if path == "/listinvstg/listingcompany.do":
            return self.catalog
        if query.get("method") == ["search"]:
            receipt, = query["acptno"]
            doc, _ = self.filings[receipt]
            previous = f"<option value='{ORIGINAL_DOC}|N'>신규상장</option>" if receipt == CORRECTION else ""
            return previous + f"<option value='{doc}|Y'selected=\"selected\">신규상장</option>"
        for receipt, (doc, body) in self.filings.items():
            original = _body_path(receipt, doc)
            if query.get("docNo") == [doc]:
                return f"parent.setPath('','https://kind.krx.co.kr{original}','', '03','12');"
            if path == original:
                return body
        raise AssertionError(path)


class TestKindListingDisclosures(unittest.TestCase):
    def test_original_and_selected_correction_keep_receipts_and_distinct_clocks(self):
        before_correction = _time("2025-08-19 18:04:10")
        observed = _time("2025-08-20 08:50:31")
        fixture = _KindFixture(
            listings=[
                _page(_list_row(CORRECTION, "2025-08-19 18:04", "12549", "<font>[정정]</font>신규상장"),
                      page=1, pages=2, total=2),
                _page(_list_row(ORIGINAL, "2025-08-18 16:42", "12549"), page=2, pages=2, total=2),
            ],
            filings={
                ORIGINAL: (ORIGINAL_DOC, _body("125490", LISTING_DAY)),
                CORRECTION: (CORRECTION_DOC, _body(
                    "125490", LISTING_DAY,
                    prefix="<tr><td>10.상장일(매매개시일)</td><td>2025-08-21</td><td>2025-08-20</td></tr>",
                )),
            },
            catalog=_page(_type_row("12549", "20240530000160", LISTING_DAY, "신규상장")),
        )
        source = KindListingDisclosures(now_provider=lambda: observed)
        with patch.object(source, "_request", side_effect=fixture.request):
            before = source.listing_evidence("125490", trading_date=LISTING_DAY, as_of=before_correction)
            rows = source.listing_evidence_for(trading_date=LISTING_DAY, as_of=observed)

        self.assertEqual(before[0].revision, ORIGINAL)
        self.assertNotIn(CORRECTION, {row.revision for row in before})
        self.assertEqual(before[0].available_at, _time("2025-08-18 16:43"))
        correction = next(row for row in rows if row.revision == CORRECTION)
        self.assertEqual(correction.available_at, _time("2025-08-19 18:05"))
        self.assertEqual(correction.evidence_id, "https://kind.krx.co.kr" + _body_path(CORRECTION, CORRECTION_DOC))
        self.assertEqual(correction.listing_date, LISTING_DAY)
        self.assertEqual(correction.listing_type, ListingType.UNKNOWN)
        before_type = select_listing_evidence(rows, symbol="125490", trading_date=LISTING_DAY,
                                              as_of=_time("2025-08-20 08:50:30"))
        self.assertEqual(before_type.status, ListingEvidenceStatus.INCOMPLETE)
        at_type = select_listing_evidence(rows, symbol="125490", trading_date=LISTING_DAY, as_of=observed)
        self.assertEqual((at_type.status, at_type.phase), (ListingEvidenceStatus.AVAILABLE, ListingPhase.LISTING_DAY))
        self.assertEqual(at_type.evidence[0].listing_type, ListingType.IPO)
        self.assertIn(f"{CORRECTION}/kind-type:01:20240530000160", at_type.evidence[0].revision)
        self.assertEqual(sum(path == _body_path(ORIGINAL, ORIGINAL_DOC) for path, _ in fixture.calls), 1)
        self.assertEqual(fixture.calls[0][1]["lastReport"], "")

    def test_same_title_and_prospectus_note_do_not_hide_transfer_or_relisting(self):
        trading_day = date(2026, 10, 15)
        observed = _time("2026-10-15 08:50")
        for label, category, expected in (("이전상장", "02", ListingType.TRANSFER),
                                          ("재상장", "03", ListingType.RELISTING)):
            with self.subTest(label=label):
                fixture = _KindFixture(
                    listings=[_page(_list_row(TRANSFER, "2026-10-13 17:03", "25003"))],
                    filings={TRANSFER: (TRANSFER_DOC, _body("250030", trading_day))},
                    catalog=_page(_type_row("25003", "20260126000320", trading_day, label)),
                )
                source = KindListingDisclosures(now_provider=lambda: observed)
                with patch.object(source, "_request", side_effect=fixture.request):
                    filed, classified = source.listing_evidence_for(trading_date=trading_day, as_of=observed)
                self.assertEqual((filed.listing_type, filed.completeness), (ListingType.UNKNOWN, DataCompleteness.PARTIAL))
                self.assertEqual((classified.listing_type, classified.completeness), (expected, DataCompleteness.COMPLETE))
                self.assertEqual(classified.available_at, observed)
                self.assertIn(f"kind-type:{category}:20260126000320", classified.revision)

    def test_konex_departure_proves_route_without_an_offering_or_catalog_row(self):
        day = date(2026, 10, 15)
        departure_body = """<table>
        <tr><td>보통주</td><td>A250030</td><td>2,891,933</td><td>2019-11-29</td></tr>
        <tr><td>5.상장폐지사유</td><td>코스닥시장 이전상장</td></tr>
        <tr><td>7.상장폐지일</td><td>2026-10-15</td></tr></table>"""
        correction = "20261014000055"
        correction_doc = "20261014000123"
        departure_row = _list_row(KONEX_DEPARTURE, "2026-10-13 17:25", "25003",
                                  "상장폐지(코스닥시장 이전상장)", "코넥스시장")
        corrected_row = _list_row(correction, "2026-10-14 09:11", "25003",
                                  "[정정]상장폐지(코스닥시장 이전상장)", "코넥스시장")
        fixture = _KindFixture(
            listings=[_page(_list_row(TRANSFER, "2026-10-13 17:03", "25003"))],
            departures=_page(departure_row),
            filings={TRANSFER: (TRANSFER_DOC, _body("250030", day).replace(
                         "투자설명서는 KIND IPO 현황의 공모기업란을 참고.", "-")),
                     KONEX_DEPARTURE: (KONEX_DEPARTURE_DOC, departure_body),
                     correction: (correction_doc, departure_body.replace("2026-10-15", "2026-10-16"))},
        )
        clock = [_time("2026-10-13 17:25:30")]
        source = KindListingDisclosures(now_provider=lambda: clock[0])

        def selected():
            rows = source.listing_evidence("250030", trading_date=day, as_of=clock[0])
            return select_listing_evidence(rows, symbol="250030", trading_date=day, as_of=clock[0])

        with patch.object(source, "_request", side_effect=fixture.request):
            self.assertEqual(ListingEvidenceStatus.INCOMPLETE, selected().status)
            clock[0] = _time("2026-10-13 17:26")
            accepted = selected()
            self.assertEqual(ListingType.KONEX_TO_KOSDAQ, accepted.evidence[0].listing_type)
            self.assertTrue(accepted.supports_ipo_strategy)
            self.assertIn(KONEX_DEPARTURE, accepted.evidence[0].revision)
            self.assertIn(_body_path(KONEX_DEPARTURE, KONEX_DEPARTURE_DOC), accepted.evidence[0].evidence_id)
            clock[0] = _time("2026-10-14 09:12")
            fixture.departures = _page(corrected_row, departure_row)
            corrected = selected()
            self.assertEqual(ListingEvidenceStatus.INCOMPLETE, corrected.status)
            self.assertIn(correction, corrected.evidence[0].revision)
            self.assertFalse(corrected.supports_ipo_strategy)

    def test_absent_or_wrong_date_classification_and_unverified_time_remain_unknown(self):
        cutoff = _time("2025-08-20 08:50")
        for stamp in ("2025-08-18 16:42", "2025-08-18", "2025-08-19 16:42"):
            with self.subTest(stamp=stamp):
                fixture = _KindFixture(
                    listings=[_page(_list_row(ORIGINAL, stamp, "12549"))],
                    filings={ORIGINAL: (ORIGINAL_DOC, _body("125490", LISTING_DAY))},
                    catalog=_page(_type_row("12549", "20240530000160", date(2025, 8, 21), "신규상장")),
                )
                source = KindListingDisclosures(now_provider=lambda: cutoff)
                with patch.object(source, "_request", side_effect=fixture.request):
                    row, = source.listing_evidence_for(trading_date=LISTING_DAY, as_of=cutoff)
                    fixture.listings = [_page(_list_row(ORIGINAL, "2025-08-18 16:42", "12549"))]
                    verified, = source.listing_evidence_for(trading_date=LISTING_DAY, as_of=cutoff)
                self.assertEqual(row.listing_type, ListingType.UNKNOWN)
                if stamp != "2025-08-18 16:42":
                    self.assertIsNone(row.available_at)
                    self.assertTrue(row.evidence_id.endswith("#unverified-publication"))
                    self.assertEqual(row.revision, verified.revision)
                    self.assertNotEqual(row.evidence_id, verified.evidence_id)

    def test_failed_correction_body_blocks_original_and_retries_without_duplicating_original_read(self):
        cutoff = _time("2025-08-20 08:50")
        fixture = _KindFixture(
            listings=[_page(_list_row(ORIGINAL, "2025-08-18 16:42", "0205H"),
                            _list_row(CORRECTION, "2025-08-19 18:04", "0205H", "[정정]신규상장"))],
            filings={
                ORIGINAL: (ORIGINAL_DOC, _body("0205H0", LISTING_DAY, spac=True)),
                CORRECTION: (CORRECTION_DOC, _body("125490", LISTING_DAY, spac=True)),
            },
        )
        source = KindListingDisclosures()
        with patch.object(source, "_request", side_effect=fixture.request):
            rows = source.listing_evidence_for(trading_date=LISTING_DAY, as_of=cutoff)
            fixture.filings[CORRECTION] = CORRECTION_DOC, _body("0205H0", LISTING_DAY, spac=True)
            retry = source.listing_evidence_for(trading_date=LISTING_DAY, as_of=cutoff)
        self.assertEqual(rows[0].listing_type, ListingType.SPAC)
        blocked = select_listing_evidence(rows, symbol="0205H0", trading_date=LISTING_DAY, as_of=cutoff)
        self.assertEqual(blocked.status, ListingEvidenceStatus.INCOMPLETE)
        self.assertEqual(blocked.evidence[0].revision, CORRECTION)
        self.assertIsNone(blocked.evidence[0].listing_date)
        recovered = select_listing_evidence(retry, symbol="0205H0", trading_date=LISTING_DAY, as_of=cutoff)
        self.assertEqual(recovered.status, ListingEvidenceStatus.AVAILABLE)
        self.assertEqual(recovered.evidence[0].listing_type, ListingType.SPAC)
        self.assertEqual(sum(path == _body_path(ORIGINAL, ORIGINAL_DOC) for path, _ in fixture.calls), 1)

    def test_incomplete_list_never_returns_evidence(self):
        fixture = _KindFixture(listings=[_page(_list_row(ORIGINAL, "2025-08-18 16:42", "12549"), total=2)],
                               filings={})
        source = KindListingDisclosures()
        with patch.object(source, "_request", side_effect=fixture.request), self.assertRaisesRegex(ValueError, "incomplete"):
            source.listing_evidence_for(trading_date=LISTING_DAY, as_of=_time("2025-08-20 08:50"))
        self.assertEqual(len(fixture.calls), 1)

    def test_kospi_preformatted_notice_identifies_ordinary_share_and_effective_date(self):
        receipt, doc = "20260821000488", "20260821001579"
        trading_day = date(2026, 8, 25)
        cutoff = _time("2026-08-25 08:50")
        body = """<PRE CLASS="fontA">
        ◎ 한화머시너리앤서비스홀딩스 주권 재상장 내역(재상장-인적분할)
        (1) 보통주권
        ① 상장종목명
        - 한글종목명 : 한화머시너리앤서비스홀딩스보통주
        ② 상장일 : 2026년 08월 25일
        ④ 표준코드 : KR70220W0000(단축코드 : A0220W0)
        (2) 종류주권(3우선주)
        ① 상장종목명
        - 한글종목명 : 한화머시너리앤서비스홀딩스3우선주
        ② 상장일 : 2026년 08월 25일
        ④ 표준코드 : KR70220WK021(단축코드 : A0220WL)
        </PRE>"""
        fixture = _KindFixture(
            listings=[_page(_list_row(receipt, "2026-08-21 16:03", "0220W",
                                      "재상장(한화머시너리앤서비스홀딩스(주), 상장일 2026.08.25)", "유가증권시장본부"))],
            filings={receipt: (doc, body)},
        )
        source = KindListingDisclosures()
        with patch.object(source, "_request", side_effect=fixture.request):
            row, = source.listing_evidence_for(trading_date=trading_day, as_of=cutoff)
        self.assertEqual((row.symbol, row.listing_date, row.listing_type), ("0220W0", trading_day, ListingType.RELISTING))
        self.assertEqual(row.available_at, _time("2026-08-21 16:04"))
        self.assertEqual(row.completeness, DataCompleteness.COMPLETE)
