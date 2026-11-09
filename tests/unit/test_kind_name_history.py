import unittest
from datetime import date
from unittest.mock import patch

from kiwoom_monitor.infrastructure.krx.kind_name_history import (
    parse_kind_disclosure_list,
    parse_kind_former_names,
)
from kiwoom_monitor.infrastructure.krx.kind_trading_halts import KindTradingHalts


class TestKindNameHistory(unittest.TestCase):
    def test_parse_kind_disclosure_list(self) -> None:
        document = """
        <tr><td>2026-08-03 17:10</td>
        <td><a onclick="companysummary_open('12301')" title='MSDI'>MSDI</a></td>
        <td><a onclick="openDisclsViewer('20260803000607','')">상호변경안내</a></td></tr>
        """
        assert parse_kind_disclosure_list(document) == (
            ("20260803000607", "123010", "MSDI", "2026-08-03"),
        )


    def test_parse_kind_former_names_includes_direct_and_older_history(self) -> None:
        document = """
        <span>가. 변경전</span><span>국문</span>
        <span class="xforms_input">주식회사 알엔티엑스</span>
        <div>변경전 : 주식회사 아이윈플러스 → 변경후 : 주식회사 알엔티엑스</div>
        """
        assert parse_kind_former_names(document) == ("알엔티엑스", "아이윈플러스")


class TestKindTradingHalts(unittest.TestCase):
    def test_krx_halt_spans_a_whole_day_only_before_effective_resumption(self) -> None:
        halt = ("20261105000005", "주권매매거래정지")
        resume = ("20261105000974", "주권매매거래정지해제")
        cases = (
            ("07:52:00", "2026-11-06", "우회상장 여부 통지일까지", True),
            ("10:00:00", "2026-11-06", "우회상장 여부 통지일까지", False),
            ("07:52:00", "2026-11-05", "우회상장 여부 통지일까지", False),
            ("07:52:00", "2026-11-06", "당일 09:30까지", False),
        )
        for started, released, expiry, completed in cases:
            with self.subTest(started=started, released=released, expiry=expiry):
                source = KindTradingHalts()
                rows = {
                    halt[0]: ("halt-url", [["대상종목", "피노", "보통주"],
                                           ["정지일시", "2026-11-05", started], ["만료일시", expiry]]),
                    resume[0]: ("resume-url", [["대상종목", "피노", "보통주"], ["해제일시", released, "-"]]),
                }
                with patch.object(source, "_notices", return_value={"033790": [resume, halt]}), \
                        patch.object(source, "_notice", side_effect=rows.__getitem__):
                    proof = source.full_day_halt("033790", date(2026, 11, 5))
                self.assertEqual(completed, proof is not None)
                if completed:
                    self.assertEqual(("halt-url", "resume-url"), (proof["halt_notice_url"], proof["resume_notice_url"]))

    def test_incomplete_krx_notice_list_cannot_certify_a_halt(self) -> None:
        source = KindTradingHalts()
        first_page = """
        <tr><td><a onclick="companysummary_open('03379')">피노</a></td>
        <td><a onclick="openDisclsViewer('20261105000005','')" title='주권매매거래정지'>공시</a></td></tr>
        전체 <em>2</em>건 : <strong>1</strong>/2
        """
        empty_second_page = "전체 <em>2</em>건 : <strong>2</strong>/2"
        with patch.object(source, "_request", side_effect=[first_page, empty_second_page]), \
                patch.object(source, "_notice") as notice:
            with self.assertRaisesRegex(ValueError, "listing was incomplete"):
                source.full_day_halt("033790", date(2026, 11, 5))
            notice.assert_not_called()
