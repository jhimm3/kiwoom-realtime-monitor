from kiwoom_monitor.infrastructure.krx.kind_name_history import (
    parse_kind_disclosure_list,
    parse_kind_former_names,
)


def test_parse_kind_disclosure_list() -> None:
    document = """
    <tr><td>2026-08-03 17:10</td>
    <td><a onclick="companysummary_open('12301')" title='MSDI'>MSDI</a></td>
    <td><a onclick="openDisclsViewer('20260803000607','')">상호변경안내</a></td></tr>
    """
    assert parse_kind_disclosure_list(document) == (
        ("20260803000607", "123010", "MSDI", "2026-08-03"),
    )


def test_parse_kind_former_names_includes_direct_and_older_history() -> None:
    document = """
    <span>가. 변경전</span><span>국문</span>
    <span class="xforms_input">주식회사 알엔티엑스</span>
    <div>변경전 : 주식회사 아이윈플러스 → 변경후 : 주식회사 알엔티엑스</div>
    """
    assert parse_kind_former_names(document) == ("알엔티엑스", "아이윈플러스")
