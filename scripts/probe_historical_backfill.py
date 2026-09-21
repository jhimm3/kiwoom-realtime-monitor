from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from kiwoom_monitor.infrastructure.historical_backfill import (
    fetch_article_publication,
    fetch_naver_historical_search_page,
    fetch_naver_stock_news_page,
    inspect_candidate_database,
    inspect_daishin_environment,
    import_daishin_backfill_ndjson,
    latest_candidates,
    store_daishin_probe_payload,
    store_article_publication_result,
    store_naver_historical_search_page,
    store_naver_stock_news_page,
)


DEFAULT_CANDIDATE_DB = Path(
    r"C:\Users\pc-1\Desktop\kiwoom_history_backfill\data\kiwoom_history.sqlite3"
)
DEFAULT_OUTPUT_DB = Path("data/historical_backfill_probe.sqlite3")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="기존 후보 DB와 대신·네이버 증권 과거자료 수집 경계를 표본 검증합니다."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    candidates = subparsers.add_parser("candidates", help="후보 DB를 읽기 전용으로 점검합니다.")
    candidates.add_argument("--database", type=Path, default=DEFAULT_CANDIDATE_DB)
    candidates.add_argument("--limit", type=int, default=5)

    naver = subparsers.add_parser("naver-news", help="네이버 증권 종목 뉴스 목록 표본을 저장합니다.")
    naver.add_argument("code", help="6자리 종목코드")
    naver.add_argument("--pages", type=int, default=1)
    naver.add_argument("--page-size", type=int, default=20)
    naver.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DB)

    history = subparsers.add_parser(
        "naver-history", help="날짜 지정 네이버 뉴스 검색 표본을 저장합니다."
    )
    history.add_argument("code", help="6자리 종목코드")
    history.add_argument("query", help="해당 날짜에 유효한 종목명 또는 별칭")
    history.add_argument("target_date", help="YYYY-MM-DD")
    history.add_argument("--pages", type=int, default=1)
    history.add_argument(
        "--skip-original-time", action="store_true",
        help="원문 발행시각 확인을 생략하고 검색 날짜만 저장합니다.",
    )
    history.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DB)

    daishin = subparsers.add_parser("daishin-preflight", help="CREON Plus 조회 환경만 점검합니다.")
    daishin.add_argument("--no-connect", action="store_true")

    sample = subparsers.add_parser("daishin-sample", help="CREON StockChart 분봉 한 묶음을 저장합니다.")
    sample.add_argument("code", help="6자리 종목코드")
    sample.add_argument("--interval", type=int, choices=(1, 5), default=1)
    sample.add_argument("--count", type=int, default=200)
    sample.add_argument("--venue", choices=("A", "K", "N"), default="K")
    sample.add_argument("--session", choices=("regular", "regular_and_after"), default="regular")
    sample.add_argument("--adjustment", choices=("raw", "adjusted"), default="raw")
    sample.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DB)

    imported = subparsers.add_parser(
        "daishin-import", help="연속조회 NDJSON을 별도 표본 DB에 반영합니다."
    )
    imported.add_argument("artifact", type=Path)
    imported.add_argument("--before-date", default="", help="이 날짜보다 오래된 봉만 반영")
    imported.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DB)

    args = parser.parse_args()
    if args.command == "candidates":
        result = {
            "overview": asdict(inspect_candidate_database(args.database)),
            "latest_candidates": [asdict(item) for item in latest_candidates(args.database, args.limit)],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "daishin-preflight":
        result = inspect_daishin_environment(try_connection=False)
        output: dict[str, object] = {"python_environment": asdict(result)}
        if args.no_connect:
            print(json.dumps(output, ensure_ascii=False, indent=2))
            return 0 if not result.missing_progids else 2
        powershell32 = Path(
            r"C:\Windows\SysWOW64\WindowsPowerShell\v1.0\powershell.exe"
        )
        bridge = Path(__file__).with_name("daishin_stockchart_probe.ps1")
        completed = subprocess.run(
            [
                str(powershell32), "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(bridge), "-Code", "005930", "-PreflightOnly",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        raw = completed.stdout.strip().splitlines()
        output["bridge"] = (
            json.loads(raw[-1].lstrip("\ufeff")) if raw else {
                "error": completed.stderr.strip() or "Daishin bridge returned no output"
            }
        )
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0 if completed.returncode == 0 else 2
    if args.command == "daishin-sample":
        powershell32 = Path(
            r"C:\Windows\SysWOW64\WindowsPowerShell\v1.0\powershell.exe"
        )
        bridge = Path(__file__).with_name("daishin_stockchart_probe.ps1")
        completed = subprocess.run(
            [
                str(powershell32), "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(bridge), "-Code", args.code, "-Interval", str(args.interval),
                "-Count", str(args.count), "-Venue", args.venue, "-Session", args.session,
                "-Adjustment", args.adjustment,
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        raw = completed.stdout.strip().splitlines()
        if not raw:
            raise SystemExit(completed.stderr.strip() or "Daishin bridge returned no output")
        payload = json.loads(raw[-1].lstrip("\ufeff"))
        if completed.returncode or payload.get("error"):
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 2
        saved = store_daishin_probe_payload(args.output, payload)
        print(json.dumps({
            "provider": "daishin_creon",
            "code": args.code,
            "interval": args.interval,
            "received": payload.get("received_count"),
            "saved": saved,
            "continue": payload.get("continue"),
            "oldest": payload["bars"][-1]["bar_time"] if payload.get("bars") else "",
            "newest": payload["bars"][0]["bar_time"] if payload.get("bars") else "",
            "output": str(args.output.resolve()),
        }, ensure_ascii=False, indent=2))
        return 0
    if args.command == "daishin-import":
        result = import_daishin_backfill_ndjson(
            args.artifact, args.output, before_date=args.before_date,
        )
        result["output"] = str(args.output.resolve())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "naver-history":
        if args.pages < 1 or args.pages > 200:
            parser.error("--pages must be between 1 and 200")
        saved_items = 0
        pages_observed = 0
        publication_statuses: dict[str, int] = {}
        for page_index in range(args.pages):
            page = fetch_naver_historical_search_page(
                args.code, args.query, args.target_date, 1 + page_index * 10
            )
            store_naver_historical_search_page(args.output, page)
            pages_observed += 1
            saved_items += len(page.items)
            if not args.skip_original_time:
                for item in page.items:
                    result = fetch_article_publication(item)
                    store_article_publication_result(args.output, result)
                    publication_statuses[result.status] = publication_statuses.get(result.status, 0) + 1
            if not page.has_structured_news:
                break
        print(json.dumps({
            "provider": "naver_historical_search",
            "code": args.code,
            "query": args.query,
            "target_date": args.target_date,
            "pages_observed": pages_observed,
            "items_observed": saved_items,
            "publication_statuses": publication_statuses,
            "output": str(args.output.resolve()),
        }, ensure_ascii=False, indent=2))
        return 0
    if args.pages < 1 or args.pages > 100:
        parser.error("--pages must be between 1 and 100")
    saved_items = 0
    last_published_at = ""
    for page_number in range(1, args.pages + 1):
        page = fetch_naver_stock_news_page(args.code, page_number, args.page_size)
        store_naver_stock_news_page(args.output, page)
        saved_items += len(page.items)
        if page.items:
            last_published_at = page.items[-1].published_at
        if page.cluster_count < args.page_size:
            break
    print(json.dumps({
        "provider": "naver_stock",
        "code": args.code,
        "pages_requested": args.pages,
        "items_observed": saved_items,
        "oldest_item_on_last_page": last_published_at,
        "output": str(args.output.resolve()),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
