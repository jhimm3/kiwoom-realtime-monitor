from __future__ import annotations

import argparse
import json
import os
import sqlite3
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path


DEFAULT_DATABASE = Path("data/historical_intelligence.sqlite3")
DEFAULT_NAS_PROJECT = Path(r"X:\kiwoom-monitor")


def _rows(connection: sqlite3.Connection, sql: str) -> list[list[object]]:
    return [list(row) for row in connection.execute(sql).fetchall()]


def _status(database: Path, *, fast: bool = False) -> dict[str, object]:
    with closing(sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True)) as connection:
        job_rows = _rows(
            connection,
            "SELECT state,COUNT(*),COALESCE(SUM(pages_observed),0),"
            "COALESCE(SUM(items_observed),0),COALESCE(SUM(usable_articles),0),"
            "COALESCE(SUM(unreadable_articles),0),COALESCE(SUM(missing_time_articles),0) "
            "FROM news_backfill_jobs GROUP BY state ORDER BY state",
        )
        total_jobs = sum(int(row[1]) for row in job_rows)
        finished_jobs = sum(
            int(row[1]) for row in job_rows if str(row[0]) in {"complete", "truncated"}
        )
        range_job_rows = _rows(
            connection,
            "SELECT state,COUNT(*),COALESCE(SUM(member_count),0),"
            "COALESCE(SUM(pages_observed),0),COALESCE(SUM(items_observed),0) "
            "FROM news_range_jobs GROUP BY state ORDER BY state",
        ) if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='news_range_jobs'"
        ).fetchone() else []
        market_jobs = _rows(
            connection,
            "SELECT state,COUNT(*) FROM market_backfill_jobs GROUP BY state ORDER BY state",
        ) if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_backfill_jobs'"
        ).fetchone() else []
        market_total = sum(int(row[1]) for row in market_jobs)
        market_finished = sum(
            int(row[1]) for row in market_jobs if str(row[0]) == "complete"
        )
        result: dict[str, object] = {
            "schema": "historical-collection-status/v1",
            "generated_at": datetime.now(UTC).isoformat(),
            "database": str(database.resolve()),
            "database_bytes": database.stat().st_size,
            "news": {
                "total_jobs": total_jobs,
                "finished_jobs": finished_jobs,
                "progress_percent": round(100 * finished_jobs / total_jobs, 4) if total_jobs else 0,
                "states": job_rows,
                "range_states": range_job_rows,
                "articles": [] if fast else _rows(
                    connection,
                    "SELECT article_fetch_status,training_eligible,COUNT(*) FROM news_articles "
                    "GROUP BY article_fetch_status,training_eligible ORDER BY 1,2",
                ),
                "fetch_attempts": None if fast else int(connection.execute(
                    "SELECT COUNT(*) FROM news_article_fetch_attempts"
                ).fetchone()[0]),
                "article_counts_deferred": fast,
                "last_job_update": connection.execute(
                    "SELECT MAX(updated_at) FROM news_backfill_jobs"
                ).fetchone()[0],
                "nas_imports": _rows(
                    connection,
                    "SELECT state,COUNT(*) FROM nas_news_imports GROUP BY state ORDER BY state",
                ) if connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='nas_news_imports'"
                ).fetchone() else [],
            },
            "market": {
                "total_jobs": market_total,
                "finished_jobs": market_finished,
                "progress_percent": round(100 * market_finished / market_total, 4) if market_total else 0,
                "codes": None if fast else int(connection.execute(
                    "SELECT COUNT(DISTINCT code) FROM market_bars"
                ).fetchone()[0]),
                "bars": [] if fast else _rows(
                    connection,
                    "SELECT interval_seconds,COUNT(*),MIN(bar_time),MAX(bar_time) "
                    "FROM market_bars GROUP BY interval_seconds ORDER BY interval_seconds",
                ),
                "bar_counts_deferred": fast,
                "jobs": market_jobs,
            },
        }
    return result


def _markdown(status: dict[str, object], published_run: str) -> str:
    news = status["news"]
    market = status["market"]
    assert isinstance(news, dict) and isinstance(market, dict)
    lines = [
        "# 과거자료 수집 현황",
        "",
        f"갱신 시각(UTC): `{status['generated_at']}`  ",
        f"마지막 NAS DB 스냅샷: `{published_run or '없음'}`",
        "",
        "## 뉴스",
        "",
        f"- 완료/절단: **{news['finished_jobs']:,} / {news['total_jobs']:,}** "
        f"(**{news['progress_percent']}%**)",
        f"- 마지막 작업 갱신: `{news['last_job_update'] or '없음'}`",
        (
            "- 원문 URL 확인 시도: 운영 중 전체 집계 생략"
            if news["fetch_attempts"] is None
            else f"- 원문 URL 확인 시도: **{news['fetch_attempts']:,}회**"
        ),
        "",
        "| 상태 | 작업 | 페이지 | 목록 기사 | 시각 확보 | 원문 불가 | 시각 없음 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for state, jobs, pages, items, usable, unreadable, missing in news["states"]:
        lines.append(
            f"| {state} | {int(jobs):,} | {int(pages):,} | {int(items):,} | "
            f"{int(usable):,} | {int(unreadable):,} | {int(missing):,} |"
        )
    if news["range_states"]:
        lines.extend([
            "",
            "### 묶음 검색 작업",
            "",
            "| 상태 | 묶음 | 포함 일별 작업 | 페이지 | 목록 기사 |",
            "|---|---:|---:|---:|---:|",
        ])
        for state, ranges, members, pages, items in news["range_states"]:
            lines.append(
                f"| {state} | {int(ranges):,} | {int(members):,} | "
                f"{int(pages):,} | {int(items):,} |"
            )
    lines.extend([
        "",
        "### 기사 원문시각 상태",
        "",
        "| 상태 | 학습 적격 | 기사 |",
        "|---|---:|---:|",
    ])
    if news["article_counts_deferred"]:
        lines.append("| 운영 중 전체 집계 생략 | - | - |")
    else:
        for state, eligible, count in news["articles"]:
            lines.append(f"| {state} | {int(eligible)} | {int(count):,} |")
    lines.extend(["", "### NAS 운영 뉴스 DB 반영", ""])
    if news["nas_imports"]:
        for state, count in news["nas_imports"]:
            lines.append(f"- {state}: **{int(count):,}건**")
    else:
        lines.append("- 아직 반영 기록 없음")
    lines.extend([
        "",
        "## 대신증권 분봉",
        "",
        f"- 완료: **{market['finished_jobs']:,} / {market['total_jobs']:,}** "
        f"(**{market['progress_percent']}%**)",
        (
            "- 수집 종목·봉: 운영 중 전체 집계 생략"
            if market["codes"] is None
            else f"- 수집 종목: **{market['codes']:,}개**"
        ),
        "",
        "| 주기 | 봉 수 | 최초 | 최종 |",
        "|---|---:|---|---|",
    ])
    if market["bar_counts_deferred"]:
        lines.append("| 운영 중 전체 집계 생략 | - | - | - |")
    else:
        for interval, count, oldest, newest in market["bars"]:
            lines.append(f"| {int(interval) // 60}분 | {int(count):,} | {oldest} | {newest} |")
    lines.extend(["", "### 대신 수집 작업", ""])
    if market["jobs"]:
        for state, count in market["jobs"]:
            lines.append(f"- {state}: **{int(count):,}종목**")
    else:
        lines.append("- 아직 작업 원장 없음")
    lines.extend([
        "",
        "> 이 문서는 수집 작업 원장의 현재 상태입니다. NAS 운영 뉴스 DB 반영 건수와는 별도입니다.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="과거자료 수집 진행률을 NAS에서 볼 수 있게 게시합니다.")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--nas-project", type=Path, default=DEFAULT_NAS_PROJECT)
    parser.add_argument("--local-only", action="store_true")
    parser.add_argument(
        "--fast", action="store_true",
        help="운영 중 17GB 기사·시도 전체 집계를 생략합니다.",
    )
    args = parser.parse_args()

    database = args.database.resolve(strict=True)
    status = _status(database, fast=args.fast)
    if args.local_only:
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return 0

    nas_project = args.nas_project.resolve(strict=True)
    expected = DEFAULT_NAS_PROJECT.resolve(strict=True)
    if os.path.normcase(str(nas_project)) != os.path.normcase(str(expected)):
        raise SystemExit(f"Refusing unexpected NAS project root: {nas_project}")
    if not (nas_project / "AGENTS.md").is_file():
        raise SystemExit(f"NAS project sentinel is missing: {nas_project}")
    destination = (
        nas_project / "deploy" / "synology" / "server-data"
        / "historical-intelligence" / "v1"
    )
    destination.mkdir(parents=True, exist_ok=True)
    latest_path = destination / "latest.json"
    published_run = ""
    if latest_path.is_file():
        published_run = str(json.loads(latest_path.read_text(encoding="utf-8")).get("run") or "")
    status["latest_published_run"] = published_run
    documents = {
        "status.json": json.dumps(status, ensure_ascii=False, indent=2) + "\n",
        "STATUS.md": _markdown(status, published_run),
    }
    for name, content in documents.items():
        temporary = destination / f".{name}.{uuid.uuid4().hex}.tmp"
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, destination / name)
    print(json.dumps({
        "status": str(destination / "STATUS.md"),
        "json": str(destination / "status.json"),
        "news_progress_percent": status["news"]["progress_percent"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
