from __future__ import annotations

import unittest

from kiwoom_monitor.infrastructure.krx.stock_catalog import (
    normalize_krx_market,
    parse_krx_stock_catalog,
)


class KrxStockCatalogTests(unittest.TestCase):
    def test_parser_normalizes_market_names(self) -> None:
        document = """
        <table><tr><th>회사명</th><th>종목코드</th><th>시장구분</th></tr>
        <tr><td>삼성전자</td><td>5930</td><td>유가증권시장</td></tr>
        <tr><td>테스트</td><td>123456</td><td>코스닥시장</td></tr></table>
        """
        self.assertEqual(
            (("005930", "삼성전자", "KOSPI"), ("123456", "테스트", "KOSDAQ")),
            parse_krx_stock_catalog(document),
        )

    def test_market_normalizer_keeps_unknown_market_explicit(self) -> None:
        self.assertEqual("KOSPI", normalize_krx_market("유가"))
        self.assertEqual("KONEX", normalize_krx_market("코넥스시장"))
        self.assertEqual("기타", normalize_krx_market("기타"))
