"""Create a deterministic corpus sample for human review of extractive summaries."""

from __future__ import annotations

import argparse
import sqlite3
from collections import Counter
from pathlib import Path

from kiwoom_monitor.application.news_analysis import assess_stock_news, extractive_news_summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--per-category", type=int, default=2)
    args = parser.parse_args()

    with sqlite3.connect(args.database) as connection:
        rows = connection.execute(
            """
            SELECT a.identity, a.stock_code, a.stock_name, a.title, a.description,
                   b.clean_body_text, a.published_at
            FROM news_audit_items AS a
            JOIN news_cleaned_bodies AS b ON b.body_revision_id=a.body_revision_id
            WHERE b.effective_status='fulltext' AND length(b.clean_body_text) BETWEEN 400 AND 8000
            ORDER BY a.published_at DESC, a.identity
            """,
        ).fetchall()

    counts: Counter[str] = Counter()
    seen: set[str] = set()
    selected = []
    for identity, stock_code, stock_name, title, description, body, published_at in rows:
        if identity in seen:
            continue
        assessment = assess_stock_news(
            stock_name or "", title or "", description or "", article_body=body or "",
        )
        category = assessment.category
        if counts[category] >= max(1, args.per_category):
            continue
        seen.add(identity)
        counts[category] += 1
        selected.append((identity, stock_code, stock_name, title, category, body, published_at))

    lines = ["# AI 없는 추출 요약 수동 비교 표본", ""]
    for index, (_identity, stock_code, stock_name, title, category, body, published_at) in enumerate(selected, 1):
        summary = extractive_news_summary(stock_name or "", title or "", body or "")
        lines.extend([
            f"## {index}. {title}", "",
            f"- 종목: {stock_name}({stock_code})",
            f"- 규칙 분류: {category}",
            f"- 발행: {published_at}",
            "- 추출 요약:",
            *[f"  - {sentence}" for sentence in summary],
            "- 비교용 정제 본문 앞부분:",
            f"  > {(body or '')[:1800]}",
            "",
        ])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {len(selected)} samples to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
