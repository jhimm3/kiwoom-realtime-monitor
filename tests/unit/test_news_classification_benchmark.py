from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import closing

from scripts.benchmark_news_classification import reference_label, build_and_run


class NewsClassificationBenchmarkTests(unittest.TestCase):
    def test_reference_keeps_company_fact_before_price_reaction(self) -> None:
        actual = reference_label(
            "테스트기업", "테스트기업 500억원 공급계약 체결에 급등", "장중 강세", "",
        )
        self.assertEqual(("KEEP", "수주·계약", "HIGH"), (actual.decision, actual.category, actual.confidence))

    def test_reference_drops_market_wrap(self) -> None:
        actual = reference_label(
            "SK하이닉스", "코스피 3% 급락해 6700선 붕괴", "SK하이닉스도 약세", "",
        )
        self.assertEqual(("DROP", "시세 반영·시장 요약"), (actual.decision, actual.category))

    def test_reference_keeps_earnings_estimate_revision(self) -> None:
        actual = reference_label(
            "SK하이닉스", "SK하이닉스 영업익 전망 14조원 하향", "컨센서스가 낮아졌다", "",
        )
        self.assertEqual(("KEEP", "실적·전망"), (actual.decision, actual.category))

    def test_reference_drops_global_article_without_investment_evidence_at_low_confidence(self) -> None:
        actual = reference_label("", "새로운 산업 변화", "시장 관심이 커지고 있다", "")
        self.assertEqual(("DROP", "LOW"), (actual.decision, actual.confidence))

    def test_reference_does_not_treat_generic_report_word_as_analyst_fact(self) -> None:
        actual = reference_label(
            "SK하이닉스", "SK하이닉스 ETF 주간 리포트", "이번 주 가격 흐름을 정리했다", "",
        )
        self.assertNotEqual("HIGH", actual.confidence)
        # The precise category/decision for generic reports is not a user-defined oracle.
        # Independently verify the benchmark stores the real input and its result.
        title = "SK하이닉스 ETF 주간 리포트"
        description = "이번 주 가격 흐름을 정리했다"
        body = "SK하이닉스 ETF 주간 가격 흐름을 정리했다."
        document = {"title": title, "description": description, "original_link": "https://publisher.test/report", "published_at": "2026-10-18T09:00:00+09:00"}
        records = [("history:article", {"stock_code": "000660", "identity": document["original_link"], "article_revision_id": "article-1", "document": document, "available_at": 1}), ("history:body", {"article_revision_id": "article-1", "body_revision_id": "body-1", "status": "fulltext", "body_text": body, "available_at": 2}), ("content:news_article", {"owner": "000660", "document": document}), ("content:news_watchlist", {"document": {"stock_code": "000660", "stock_name": "SK하이닉스"}})]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "benchmark.sqlite3"
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("CREATE TABLE records(kind TEXT,payload_json TEXT)")
                db.executemany("INSERT INTO records VALUES(?,?)", [(kind, json.dumps(value, ensure_ascii=False)) for kind, value in records])
                source_before = db.execute("SELECT * FROM records").fetchall()
            summary = build_and_run(path)
            self.assertEqual((1, 1), (summary["total_items"], summary["candidate_items"]))
            self.assertEqual(1, sum(summary["reference_counts"].values()))
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(source_before, db.execute("SELECT * FROM records").fetchall())
                row = db.execute("SELECT title,description,raw_body_length,raw_body_hash,payload_json FROM news_audit_items").fetchone()
                self.assertEqual((title, description, len(body), hashlib.sha256(body.encode()).hexdigest()), row[:4])
                self.assertEqual(document["original_link"], json.loads(row[4])["identity"])
                self.assertEqual(("body-1", "article-1", body), db.execute("SELECT body_revision_id,article_revision_id,clean_body_text FROM news_cleaned_bodies").fetchone())
                self.assertEqual(1, db.execute("SELECT COUNT(*) FROM news_reference_labels").fetchone()[0])
                self.assertEqual(1, db.execute("SELECT COUNT(*) FROM news_candidate_runs").fetchone()[0])


    def test_reference_drops_social_mou(self) -> None:
        actual = reference_label(
            "테스트기업", "테스트기업, 청소년 교육 업무협약", "사회공헌 지원사업을 진행한다", "",
        )
        self.assertEqual(("DROP", "비투자성 기업 소식"), (actual.decision, actual.category))

    def test_reference_drops_early_supplier_payment_as_low_value(self) -> None:
        actual = reference_label(
            "현대차", "현대차그룹, 추석 전 협력사 납품대금 조기 지급",
            "현대차와 계열사가 대금을 앞당겨 지급한다", "",
        )
        self.assertEqual(("DROP", "비투자성 기업 소식"), (actual.decision, actual.category))

    def test_reference_keeps_hard_event_in_full_company_title_despite_price_move(self) -> None:
        actual = reference_label(
            "삼성바이오로직스", "삼성바이오로직스 유증 공시에 7% 급락", "유상증자 소식",
            "삼성바이오로직스가 이날 3조원 유상증자를 결정했다고 공시했다.",
        )
        self.assertEqual(("KEEP", "자본·주주환원", "HIGH"), (
            actual.decision, actual.category, actual.confidence,
        ))

    def test_reference_drops_market_recap_before_old_event_words(self) -> None:
        actual = reference_label(
            "한전기술", "[베스트&워스트] 한전기술 41% 폭등…자사주 소각 훈풍",
            "한 주간 등락을 정리했다", "",
        )
        self.assertEqual(("DROP", "시세 반영·시장 요약", "HIGH"), (
            actual.decision, actual.category, actual.confidence,
        ))

    def test_reference_drops_broker_attribution_as_stock_identity(self) -> None:
        actual = reference_label(
            "현대차", "롯데칠성 목표가 하향-현대차", "현대차증권은 롯데칠성을 분석했다", "",
        )
        self.assertEqual(("DROP", "관련성 낮음", "HIGH"), (
            actual.decision, actual.category, actual.confidence,
        ))


if __name__ == "__main__":
    unittest.main()
