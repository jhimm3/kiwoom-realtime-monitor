from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace

from kiwoom_monitor.application.trade_review_analysis import TradeReviewAnalysis
from kiwoom_monitor.presentation.trade_review_formatting import (
    format_analysis_data_status,
    format_analysis_rate,
    format_cycle_analysis_block,
    format_daily_analysis_summary,
    format_entry_snapshot_blocks,
    format_linked_news_blocks,
)


class TradeReviewFormattingTests(unittest.TestCase):
    @staticmethod
    def analysis(*, score: int = 100, count: int = 2, complete: bool = True) -> TradeReviewAnalysis:
        return TradeReviewAnalysis(
            60, 1000, 1010, 1.0, 2.0, -1.0, None, None, ("당일매매",),
            complete, 90, (), (), (), score, "기준 충족", count,
        )

    def test_rate_preserves_waiting_and_signed_percent_format(self) -> None:
        self.assertEqual("계산 대기", format_analysis_rate(None))
        self.assertEqual("+1.23%", format_analysis_rate(1.234))

    def test_cycle_block_preserves_existing_labels_and_fallbacks(self) -> None:
        analysis = TradeReviewAnalysis(
            holding_seconds=3660, average_buy=1000, average_sell=1010,
            realized_return_rate=1.0, max_favorable_rate=2.5, max_adverse_rate=-0.75,
            exit_efficiency=None, missed_gain_rate=None, labels=("당일매매", "완전청산"),
            complete=True, confidence=88, positive_points=(), negative_points=(),
            next_actions=(), discipline_score=100, verdict="기준 충족", evaluated_rule_count=2,
            setup_evidence=("고점 돌파",), unverifiable_items=("지속 관심",),
        )

        block = format_cycle_analysis_block(
            cycle_number=2, setup_type="돌파", pack_name="기본", source_text="1강",
            relevant_rule_count=7, analysis=analysis,
        )

        self.assertIn("\n[2차 · 돌파] 전략팩: 기본\n적용 강의: 1강", block)
        self.assertIn("보유 1시간 1분 · 실현 +1.00%", block)
        self.assertIn("MFE(보유 중 최대 유리폭) +2.50%", block)
        self.assertIn("현재 판정 불가: 지속 관심", block)
        self.assertIn("개인 원칙 충족: 확인된 충족 항목 없음", block)
        self.assertTrue(block.endswith("다음 행동: 현재 기준을 유지하며 표본을 더 확인하세요."))

    def test_snapshot_blocks_include_theme_flow_program_and_market(self) -> None:
        entry = SimpleNamespace(
            executed_at=datetime(2026, 9, 9, 9, 5), rank=3,
            trade_value_1m_eok=41.25, trade_value_5m_eok=120.5,
            themes=("반도체",), theme_ranks={"반도체": 2}, high_distance_percent=-0.3,
            news=({"title": "기사"},),
            investor_flow={
                "available": True, "foreign_net_buy_quantity": 0,
                "institution_net_buy_quantity": 0, "as_of_date": "20260909",
                "market_basis": "KRX", "program_trade": {
                    "net_buy_amount_million_won": 12, "net_buy_quantity": 300,
                    "net_buy_amount_change_million_won": 2, "net_buy_quantity_change": 50,
                },
            },
            orderbook={"execution_strength": 96.42, "buy_share_percent": 74.7582},
            market_state={
                "kospi": {"index": 3300, "change_rate": 0.5, "trade_value_eok": 120000},
                "kosdaq": {"index": 900, "change_rate": -0.2, "trade_value_eok": 80000},
                "observed_at": "09:05:00",
            },
        )

        blocks = format_entry_snapshot_blocks(entry)

        self.assertEqual(3, len(blocks))
        self.assertEqual(
            "진입 스냅샷: 실시간 3위 · 1분 41.25억 · 5분 120.50억 · 신고가 거리 -0.30% · ",
            blocks[0],
        )
        self.assertIn("테마: 반도체(2위) · 체결강도 96.42 · 최근 60초 매수비중 74.76%", blocks[1])
        self.assertIn("외국인 순매수 집계 전 · 기관 순매수 집계 전", blocks[1])
        self.assertIn("프로그램 순매수 12백만원/300주", blocks[1])
        self.assertEqual(
            "시장: 코스피 3300 (0.5%) · 거래대금 120000억 / 코스닥 900 (-0.2%) · "
            "거래대금 80000억 · 시장 조회 09:05:00",
            blocks[2],
        )

    def test_missing_high_distance_preserves_legacy_headline(self) -> None:
        entry = SimpleNamespace(
            executed_at=datetime(2026, 9, 9, 21, 0), rank=1,
            trade_value_1m_eok=10.0, trade_value_5m_eok=20.0,
            themes=(), theme_ranks=None, high_distance_percent=None, news=(),
            investor_flow={}, orderbook={}, market_state={},
        )

        blocks = format_entry_snapshot_blocks(entry)

        self.assertEqual("진입 스냅샷: 신고가 거리 자료 없음 · ", blocks[0])
        self.assertEqual(2, len(blocks))

    def test_status_and_daily_summary_preserve_aggregate_contract(self) -> None:
        analyses = (self.analysis(score=100, count=2), self.analysis(score=70, count=1, complete=False))

        status = format_analysis_data_status(
            active_pack_names=("기본", "눌림"), lesson_counts={2: 3, 1: 2},
            personal_rule_count=5, analyses=analyses, snapshot_count=4,
        )
        summary = format_daily_analysis_summary(analyses, 1.234)

        self.assertEqual(
            "활성 전략팩 기본, 눌림  ·  개인원칙 강의구조 연결 · 1강 2문단 · 2강 3문단  ·  "
            "분봉 범위 일부만 반영  ·  진입 스냅샷 4건 연결",
            status,
        )
        self.assertEqual(
            "하루 종합 · 자동 확인 가능한 설정 기준 충족 · 자동 확인 기준 3개 · 평균 85점 · "
            "총 2회차 · 전체 실현 +1.23%",
            summary,
        )

    def test_linked_news_blocks_include_heading_and_fallbacks(self) -> None:
        news = (
            SimpleNamespace(
                published_at=None, assessment=SimpleNamespace(outlook=""), title="첫 기사",
            ),
        )

        self.assertEqual(
            ("\n[매매일지 대표 뉴스]", "--:-- · 미분류 · 첫 기사"),
            format_linked_news_blocks(news),
        )
        self.assertEqual((), format_linked_news_blocks(()))


if __name__ == "__main__":
    unittest.main()
