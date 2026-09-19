"""로컬 뉴스 benchmark의 불일치 유형과 대표 제목을 Markdown으로 기록한다."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def build_report(database_path: Path, run_id: str = "", *, samples: int = 12) -> str:
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        if not run_id:
            row = connection.execute(
                "SELECT run_id FROM news_benchmark_runs ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if row is None:
                raise ValueError("benchmark 실행 기록이 없습니다.")
            run_id = str(row[0])
        run = connection.execute(
            "SELECT candidate_version,created_at,summary_json FROM news_benchmark_runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if run is None:
            raise ValueError(f"benchmark 실행을 찾지 못했습니다: {run_id}")
        summary = json.loads(str(run["summary_json"]))
        groups = connection.execute(
            "SELECT r.decision AS reference_decision,r.confidence AS reference_confidence,r.category AS reference_category,"
            "r.reason AS reference_reason,c.relevant AS candidate_relevant,c.category AS candidate_category,"
            "c.reason AS candidate_reason,COUNT(*) AS item_count "
            "FROM news_reference_labels r JOIN news_candidate_runs c ON c.item_id=r.item_id "
            "WHERE c.run_id=? AND r.decision!='REVIEW' "
            "AND c.relevant != CASE WHEN r.decision='KEEP' THEN 1 ELSE 0 END "
            "GROUP BY 1,2,3,4,5,6,7 ORDER BY item_count DESC",
            (run_id,),
        ).fetchall()
        lines = [
            "# 뉴스 분류 오프라인 차이 보고서", "",
            f"- 실행 ID: `{run_id}`",
            f"- 후보 버전: `{run['candidate_version']}`",
            f"- 전체 평가 항목: {summary['total_items']:,}",
            f"- 비교 가능 항목: {summary['comparable_items']:,}",
            f"- 판정 일치율: {summary['agreement_rate']:.2%}",
            f"- 필요한 뉴스 제외 후보: {summary['false_drop']:,}",
            f"- 불필요한 뉴스 보관 후보: {summary['false_keep']:,}",
            "",
            "자동 기준 라벨은 확정 정답이 아니다. 아래 차이를 묶어 확인하고 기준 또는 후보 규칙을 수정한다.",
            "",
            "모든 항목은 정제 본문을 사용해 규칙 판정을 마쳤다. 불일치 수는 정답 개수가 아니라 직접 검토할 후보 개수다.",
            "",
            "## 불일치 묶음", "",
            "|기준|신뢰도|기준 분류|후보 분류|건수|기준 이유|후보 이유|",
            "|---|---|---|---|---:|---|---|",
        ]
        for group in groups:
            candidate_decision = "보관" if group["candidate_relevant"] else "제외"
            lines.append(
                f"|{group['reference_decision']}|{group['reference_confidence']}|{_cell(group['reference_category'])}|"
                f"{candidate_decision} / {_cell(group['candidate_category'])}|{group['item_count']:,}|"
                f"{_cell(group['reference_reason'])}|{_cell(group['candidate_reason'])}|"
            )
        lines.extend(["", "## 대표 제목", ""])
        for expected, candidate, label in (("KEEP", 0, "필요한 뉴스 제외 후보"), ("DROP", 1, "불필요한 뉴스 보관 후보")):
            lines.extend([f"### {label}", ""])
            rows = connection.execute(
                "SELECT i.stock_code,i.stock_name,i.title,r.confidence AS reference_confidence,r.category AS reference_category,"
                "r.reason AS reference_reason,c.category AS candidate_category,c.reason AS candidate_reason "
                "FROM news_reference_labels r JOIN news_candidate_runs c ON c.item_id=r.item_id "
                "JOIN news_audit_items i ON i.item_id=r.item_id "
                "WHERE c.run_id=? AND r.decision=? AND c.relevant=? "
                "ORDER BY i.published_at DESC,i.item_id LIMIT ?",
                (run_id, expected, candidate, samples),
            ).fetchall()
            for row in rows:
                lines.append(
                    f"- **{row['stock_name'] or row['stock_code']}** — {_single_line(row['title'])}  "
                    f"기준: {row['reference_category']} [{row['reference_confidence']}] ({row['reference_reason']}) / "
                    f"후보: {row['candidate_category']} ({row['candidate_reason']})"
                )
            lines.append("")
    return "\n".join(lines)


def _cell(value: object) -> str:
    return _single_line(value).replace("|", "\\|")


def _single_line(value: object) -> str:
    return " ".join(str(value or "").split())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=12)
    arguments = parser.parse_args()
    report = build_report(arguments.database, arguments.run_id, samples=arguments.samples)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(report + "\n", encoding="utf-8")
    print(arguments.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
